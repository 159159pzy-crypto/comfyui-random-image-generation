"""Standalone resource paths stay consistent from preflight through replay."""
import asyncio
import copy
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from anima_webui.artwork_deletion import ArtworkDeletionService
from anima_webui.history import HistoryStore
from anima_webui.resource_config import configure_resources
from anima_webui.runner import BatchManager
from anima_webui.server import HISTORY_KEY, MANAGER_KEY, create_app
from anima_webui.workflow import DEFAULT_SETTINGS, WorkflowError, WorkflowTemplates, validate_settings
from test_server import FakeComfy, _write_fake_tools


def settings(**overrides):
    return validate_settings({
        "base_mode": "normal", "count": 1, "loras": [],
        **{f"random_{section}": False for section in ("character", "clothing", "pose", "background", "expression")},
        **overrides,
    })


def templates():
    return WorkflowTemplates.load(Path(__file__).resolve().parents[1] / "templates")


class PathComfy(FakeComfy):
    def __init__(self):
        super().__init__()
        self.inventory = {
            "models": ["Anima/" + DEFAULT_SETTINGS["model_name"]],
            "clips": ["Text Encoders/" + DEFAULT_SETTINGS["clip_name"]],
            "vaes": ["VAE/" + DEFAULT_SETTINGS["vae_name"]],
            "upscale_models": ["Upscale/" + DEFAULT_SETTINGS["upscalers"]["pixel_hires"]["model_name"]],
            "seedvr2_models": ["SeedVR2/" + DEFAULT_SETTINGS["upscalers"]["seedvr2"]["model_name"]],
            "seedvr2_vaes": ["SeedVR2/" + DEFAULT_SETTINGS["upscalers"]["seedvr2"]["vae_name"]],
            "samplers": ["euler"], "schedulers": ["simple"],
        }

    async def resource_inventory(self):
        return copy.deepcopy(self.inventory)


def assert_bound_paths(payload, record, expected):
    graph = payload["prompt"]
    metadata = payload["extra_data"]["extra_pnginfo"]["anima_random_webui"]
    saved = record["settings"]
    assert metadata["settings"] == saved
    fields = (("unet", "models", "model_name", "167:56", "unet_name"),
              ("clip", "clips", "clip_name", "167:57", "clip_name"),
              ("vae", "vaes", "vae_name", "167:58", "vae_name"))
    for bound, category, key, node, input_name in fields:
        actual = expected[category][0].replace("\\", "/")
        assert saved[key] == actual
        assert record["bound_resources"][bound] == actual
        assert graph[node]["inputs"][input_name] == actual
    for stage, key, category, bound, node, input_name in (
        ("pixel_hires", "model_name", "upscale_models", "pixel_hires_model", "v3:pixel:model", "model_name"),
        ("seedvr2", "model_name", "seedvr2_models", "seedvr2_model", "v3:seed:model", "unet_name"),
        ("seedvr2", "vae_name", "seedvr2_vaes", "seedvr2_vae", "v3:seed:vae", "vae_name"),
    ):
        if not saved["upscalers"][stage]["enabled"]:
            assert bound not in record["bound_resources"]
            continue
        actual = expected[category][0].replace("\\", "/")
        assert saved["upscalers"][stage][key] == actual
        assert record["bound_resources"][bound] == actual
        assert graph[node]["inputs"][input_name] == actual


def test_subdirectory_paths_match_loader_png_metadata_history_and_replay(tmp_path):
    async def run():
        history = HistoryStore(tmp_path / "history.sqlite3")
        comfy = PathComfy()
        comfy.inventory["clips"][0] = comfy.inventory["clips"][0].replace("/", "\\")
        manager = BatchManager(templates(), history, comfy)
        try:
            source = settings(upscalers={"pixel_hires": {"enabled": True}, "seedvr2": {"enabled": True}})
            original = copy.deepcopy(source)
            await manager.start(source, seeds={"sample_seed": 123, "prompt_seed": 456})
            assert (await manager.wait())["status"] == "completed"
            record = (await history.list_images())["items"][0]
            assert_bound_paths(comfy.submissions[0], record, comfy.inventory)
            assert source == original
            await manager.start(record["settings"], seeds={"sample_seed": 123, "prompt_seed": 456}, regeneration={
                "mode": "replay", "frozen_positive_prompt": record["positive_prompt"],
                "frozen_negative_prompt": record["negative_prompt"], "fixed_selection": {},
            })
            assert (await manager.wait())["status"] == "completed"
            repeated = (await history.list_images())["items"][0]
            assert_bound_paths(comfy.submissions[1], repeated, comfy.inventory)
            assert repeated["bound_resources"] == record["bound_resources"]
            assert repeated["execution_context"] != record["execution_context"]
        finally:
            await manager.shutdown()
            await history.close()

    asyncio.run(run())


def test_mapped_paths_are_not_remapped_and_missing_frozen_resource_is_rejected(tmp_path):
    async def run():
        history = HistoryStore(tmp_path / "history.sqlite3")
        comfy = PathComfy()
        target = comfy.inventory["models"][0]
        other = "Other/alternate.safetensors"
        comfy.inventory["models"].append(other)
        workflow = templates()
        configure_resources(workflow, {DEFAULT_SETTINGS["model_name"]: target, target: other})
        manager = BatchManager(workflow, history, comfy)
        try:
            await manager.start(settings())
            assert (await manager.wait())["status"] == "completed"
            record = (await history.list_images())["items"][0]
            assert_bound_paths(comfy.submissions[0], record, comfy.inventory)
            # A new config may redirect an alias, but an already saved loader
            # path cannot silently become another model during replay.
            comfy.inventory["models"] = [other]
            issues = await manager.resource_issues(record["settings"], resources_resolved=True)
            assert any(target in item["name"] for item in issues)
            with pytest.raises(WorkflowError, match="主模型不存在"):
                await manager.start(record["settings"], regeneration={
                    "mode": "replay", "frozen_positive_prompt": "saved prompt",
                })
            assert len(comfy.submissions) == 1
        finally:
            await manager.shutdown()
            await history.close()

    asyncio.run(run())


@pytest.mark.parametrize("category", ["models", "clips", "vaes", "upscale_models", "seedvr2_models", "seedvr2_vaes"])
def test_enabled_missing_resources_reject_before_history_or_submission(tmp_path, category):
    async def run():
        history = HistoryStore(tmp_path / "history.sqlite3")
        comfy = PathComfy()
        comfy.inventory[category] = []
        manager = BatchManager(templates(), history, comfy)
        try:
            with pytest.raises(WorkflowError, match="不存在"):
                await manager.start(settings(upscalers={"pixel_hires": {"enabled": True}, "seedvr2": {"enabled": True}}))
            assert manager.state is None
            assert not comfy.submissions
            assert (await history.list_images())["total"] == 0
            assert await history._call(lambda: history.connection.execute("SELECT COUNT(*) FROM batches").fetchone()[0]) == 0
        finally:
            await manager.shutdown()
            await history.close()

    asyncio.run(run())


def test_resolution_rejects_ambiguous_or_wrong_subfolder_and_ignores_disabled_optional_models():
    manager = BatchManager(templates(), None, None)
    source = settings()
    inventory = PathComfy().inventory
    inventory["models"] = ["First/" + source["model_name"], "Second/" + source["model_name"]]
    with pytest.raises(WorkflowError, match="匹配不唯一"):
        manager._resolve_resource_paths(source, inventory)
    source["model_name"] = "Missing/" + source["model_name"]
    with pytest.raises(WorkflowError, match="不存在"):
        manager._resolve_resource_paths(source, inventory)
    source["model_name"] = inventory["models"][1]
    for category in ("upscale_models", "seedvr2_models", "seedvr2_vaes"):
        inventory[category] = []
    resolved = manager._resolve_resource_paths(source, inventory)
    assert resolved["model_name"] == source["model_name"]
    assert not manager._resource_inventory_issues(resolved, inventory)
    assert set(manager._bound_resources(resolved)) == {"model_profile", "unet", "clip", "vae", "loras"}


def test_frozen_root_filename_is_not_replaced_by_same_basename_in_another_folder():
    manager = BatchManager(templates(), None, None)
    source = settings()
    inventory = PathComfy().inventory
    with pytest.raises(WorkflowError, match="主模型不存在"):
        manager._resolve_resource_paths(source, inventory, resources_resolved=True)


def test_history_http_cannot_retarget_deleted_root_model_to_same_basename(tmp_path):
    async def run():
        comfy = PathComfy()
        comfy.inventory["models"] = [DEFAULT_SETTINGS["model_name"]]
        app = create_app(data_dir=tmp_path / "data", comfy=comfy, anima_tools_dir=_write_fake_tools(tmp_path))
        async with TestClient(TestServer(app)) as client:
            response = await client.post("/api/batches", json=settings())
            assert response.status == 201, await response.text()
            assert (await app[MANAGER_KEY].wait())["status"] == "completed"
            record = (await app[HISTORY_KEY].list_images())["items"][0]
            comfy.inventory["models"] = ["Replacement/" + DEFAULT_SETTINGS["model_name"]]
            response = await client.get(f"/api/history/{record['id']}/regeneration-options")
            assert response.status == 200, await response.text()
            modes = (await response.json())["modes"]
            assert not modes["replay"]["available"]
            assert not modes["prompt_variant"]["available"]
            response = await client.post(f"/api/history/{record['id']}/regenerate", json={"mode": "replay"})
            assert response.status == 400, await response.text()
            assert len(comfy.submissions) == 1

    asyncio.run(run())


def test_new_history_and_original_deletion_stay_isolated_from_other_project(tmp_path):
    async def run():
        old = HistoryStore(tmp_path / "other-project" / "history.sqlite3")
        new = HistoryStore(tmp_path / "new-project" / "history.sqlite3")
        output = tmp_path / "shared-comfy-output"
        output.mkdir()
        try:
            records = []
            for store, name in ((old, "old.png"), (new, "new.png")):
                (output / name).write_bytes(name.encode())
                await store.create_batch("one", 1, settings())
                records.append(await store.add_image(
                    batch_id="one", sequence=1, prompt_id="one",
                    image={"filename": name, "type": "output"}, positive_prompt="saved",
                    negative_prompt="", sample_seed=1, prompt_seed=2, settings=settings(),
                ))
            assert records[0]["id"] == records[1]["id"]
            service = ArtworkDeletionService(new)
            with patch("anima_webui.artwork_deletion.image_location.IS_WINDOWS", True), \
                 patch("anima_webui.artwork_deletion.image_location.verify_local_comfy"), \
                 patch("anima_webui.artwork_deletion.image_location.comfy_image_directory", return_value=output):
                result = await service.delete(records[1]["id"], "file", comfy_url="http://127.0.0.1:8188")
            assert result["file_deleted_count"] == 1
            assert not (output / "new.png").exists()
            assert (output / "old.png").read_bytes() == b"old.png"
            assert (await old.list_images())["total"] == 1
            assert (await new.list_images())["total"] == 0
            tables = await new._call(lambda: {row[0] for row in new.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")})
            assert not {"source_images", "generation_jobs", "single_jobs"}.intersection(tables)
        finally:
            await old.close()
            await new.close()

    asyncio.run(run())
