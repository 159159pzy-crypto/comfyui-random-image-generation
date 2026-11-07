"""New LoRA choices may resolve a unique basename; history paths are frozen."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from aiohttp.test_utils import TestClient, TestServer

from anima_webui.server import HISTORY_KEY, MANAGER_KEY, create_app
from anima_webui.workflow import WorkflowError
from test_server import FakeComfy, _write_fake_tools


class LoraPathComfy(FakeComfy):
    def __init__(self, filenames):
        super().__init__()
        self.filenames = filenames

    async def lora_inventory(self):
        return {
            "items": [
                {"filename": filename, "trigger_words": []}
                for filename in self.filenames
            ],
            "count": len(self.filenames),
        }

    async def lora_filenames(self):
        return list(self.filenames)


@asynccontextmanager
async def studio(tmp_path, filenames):
    comfy = LoraPathComfy(filenames)
    app = create_app(
        data_dir=tmp_path / "data",
        comfy=comfy,
        anima_tools_dir=_write_fake_tools(tmp_path),
    )
    async with TestClient(TestServer(app)) as client:
        yield app, client, comfy


def settings(filename):
    return {
        "count": 1,
        "loras": [{"filename": filename, "enabled": True, "strength": 0.7}],
    }


@pytest.mark.parametrize(
    "names,requested,resolved",
    [
        (["folder/style.safetensors"], "style.safetensors", "folder/style.safetensors"),
        (
            ["style.safetensors", "folder/style.safetensors"],
            "style.safetensors",
            "style.safetensors",
        ),
        (
            ["one/style.safetensors", "two/style.safetensors"],
            "two/style.safetensors",
            "two/style.safetensors",
        ),
    ],
)
def test_new_submission_resolves_unique_basename_and_freezes_real_path(
    tmp_path, names, requested, resolved
):
    async def run():
        async with studio(tmp_path, names) as (app, client, comfy):
            response = await client.post("/api/batches", json=settings(requested))
            assert response.status == 201, await response.text()
            assert (await response.json())["settings"]["loras"][0][
                "filename"
            ] == resolved
            assert (await app[MANAGER_KEY].wait())["status"] == "completed"
            saved = (await app[HISTORY_KEY].list_images())["items"][0]
            assert saved["settings"]["loras"][0]["filename"] == resolved
            assert saved["bound_resources"]["loras"] == [resolved]
            metadata = comfy.submissions[0]["extra_data"]["extra_pnginfo"][
                "anima_random_webui"
            ]
            assert metadata["settings"]["loras"][0]["filename"] == resolved

    asyncio.run(run())


@pytest.mark.parametrize(
    "names,requested,error",
    [
        (
            ["one/style.safetensors", "two/style.safetensors"],
            "style.safetensors",
            "多个子目录",
        ),
        (["new/style.safetensors"], "old/style.safetensors", "不存在"),
    ],
)
def test_new_submission_rejects_ambiguity_and_missing_explicit_paths(
    tmp_path, names, requested, error
):
    async def run():
        async with studio(tmp_path, names) as (app, client, comfy):
            response = await client.post("/api/batches", json=settings(requested))
            assert response.status == 400, await response.text()
            assert error in (await response.json())["error"]
            assert app[MANAGER_KEY].snapshot() is None and comfy.submissions == []
            assert (
                app[HISTORY_KEY]
                .connection.execute("SELECT COUNT(*) FROM batches")
                .fetchone()[0]
                == 0
            )

    asyncio.run(run())


def test_basename_resolution_does_not_bypass_model_classification(tmp_path):
    async def run():
        async with studio(tmp_path, ["folder/style.safetensors"]) as (
            app,
            client,
            comfy,
        ):
            comfy.base_models["folder/style.safetensors"] = "Qwen"
            response = await client.post(
                "/api/batches", json=settings("style.safetensors")
            )
            assert response.status == 400, await response.text()
            assert "Qwen" in (await response.json())["error"]
            assert app[MANAGER_KEY].snapshot() is None and comfy.submissions == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "original", ["style.safetensors", "original/style.safetensors"]
)
def test_missing_frozen_lora_cannot_retarget_in_http_or_direct_manager(
    tmp_path, original
):
    async def run():
        async with studio(tmp_path, [original]) as (app, client, comfy):
            response = await client.post("/api/batches", json=settings(original))
            assert response.status == 201, await response.text()
            manager = app[MANAGER_KEY]
            assert (await manager.wait())["status"] == "completed"
            saved = (await app[HISTORY_KEY].list_images())["items"][0]
            comfy.filenames = ["replacement/style.safetensors"]
            endpoint = f"/api/history/{saved['id']}"
            options = await (
                await client.get(endpoint + "/regeneration-options")
            ).json()
            assert not options["modes"]["replay"]["available"]
            assert not options["modes"]["prompt_variant"]["available"]
            response = await client.post(
                endpoint + "/regenerate", json={"mode": "replay"}
            )
            assert response.status == 400, await response.text()
            assert original in (await response.json())["error"]
            with pytest.raises(WorkflowError, match="LoRA 文件不存在"):
                await manager.start(
                    saved["settings"],
                    regeneration={"mode": "replay", "frozen_positive_prompt": "saved"},
                )
            assert manager.queue == [] and len(comfy.submissions) == 1
            assert (
                app[HISTORY_KEY]
                .connection.execute("SELECT COUNT(*) FROM batches")
                .fetchone()[0]
                == 1
            )

    asyncio.run(run())


def test_frozen_exact_lora_still_replays_with_another_same_basename_present(tmp_path):
    async def run():
        async with studio(tmp_path, ["style.safetensors"]) as (app, client, comfy):
            response = await client.post(
                "/api/batches", json=settings("style.safetensors")
            )
            assert response.status == 201, await response.text()
            assert (await app[MANAGER_KEY].wait())["status"] == "completed"
            saved = (await app[HISTORY_KEY].list_images())["items"][0]
            comfy.filenames.append("replacement/style.safetensors")
            response = await client.post(
                f"/api/history/{saved['id']}/regenerate", json={"mode": "replay"}
            )
            assert response.status == 201, await response.text()
            assert (await app[MANAGER_KEY].wait())["status"] == "completed"
            latest = (await app[HISTORY_KEY].list_images())["items"][0]
            assert latest["settings"]["loras"][0]["filename"] == "style.safetensors"

    asyncio.run(run())
