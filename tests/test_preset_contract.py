"""The lightweight preset library preserves random drafts and v4 provenance."""
import asyncio
import copy
import json
from unittest.mock import patch

import pytest

from anima_webui.preset_contract import INCLUDE_KEYS, normalize_source, preset_settings
from anima_webui.style_presets import StylePresetStore
from anima_webui.workflow import WorkflowError, validate_settings


def settings(**values):
    return validate_settings({"pipeline_version": 5, "base_mode": "normal", **values})


@pytest.mark.parametrize("flags", [{}, {"size": True}, {"repair": True}, {"size": True, "repair": True}])
def test_capture_only_carries_selected_optional_groups(flags):
    value = settings(width=768, height=1024, dual={"scale": 1.5, "half_precision": False},
                     upscalers={"pixel_hires": {"enabled": True}, "latent": {"half_precision": False}})
    saved = preset_settings(value, include=flags)
    assert ("width" in saved) == flags.get("size", False)
    assert ("upscalers" in saved) == flags.get("repair", False)
    assert saved["dual"]["scale"] == 1.5
    assert "half_precision" not in saved["dual"]
    if flags.get("repair"):
        assert "half_precision" not in saved["upscalers"]["latent"]
    assert not {"count", "pools", "pipeline_version", "workbench", "generation_mode"} & saved.keys()
    assert INCLUDE_KEYS == ("size", "repair")


@pytest.mark.parametrize("value", [{"img2img": True}, {"affixes": False}, {"size": 1}, [], False])
def test_excluded_or_malformed_flags_are_rejected(value):
    with pytest.raises(WorkflowError):
        preset_settings({}, include=value)


@pytest.mark.parametrize("source", [{"workbench": "single"}, {"pipeline_version": 2}, {"pipeline_version": 3}, {"pipeline_version": True}])
def test_excluded_preset_sources_are_rejected(source):
    with pytest.raises(WorkflowError):
        normalize_source(source)


@pytest.mark.parametrize("repair", [False, True])
def test_application_preserves_random_draft_devices_and_source_pipeline(tmp_path, repair):
    async def run():
        store = StylePresetStore(tmp_path / "presets.json")
        saved = await store.create({"name": "Old style", "settings": settings(
            pipeline_version=4, width=768, cfg=2, upscalers={"pixel_hires": {"enabled": True}}),
            "include": {"repair": repair}})
        current = settings(width=1152, count=7, dual={"half_precision": False},
                           fixed_pose="keep pose", random_pose=False,
                           upscalers={"latent": {"half_precision": False}})
        before = copy.deepcopy(current)
        disk = store.path.read_bytes()
        result = store.resolve(saved["id"], current)
        target = result["settings"]
        assert target["cfg"] == 2 and target["width"] == 1152 and target["count"] == 7
        assert target["pools"] == current["pools"] and target["fixed_pose"] == "keep pose"
        assert target["dual"]["half_precision"] is False
        assert target["upscalers"]["latent"]["half_precision"] is False
        assert target["pipeline_version"] == (4 if repair else 5)
        assert result["version_change"] == ({"from": 5, "to": 4} if repair else None)
        assert current == before and store.path.read_bytes() == disk
        if not repair:
            assert target["upscalers"] == current["upscalers"]
    asyncio.run(run())


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_presets_back_up_and_default_to_no_optional_groups(tmp_path, version):
    path = tmp_path / "presets.json"
    original = {"version": version, "items": [{"id": "old", "name": "Legacy", "favorite": True,
                 "settings": {"model_profile": "anima", "width": 768, "height": 1024, "base_mode": "normal",
                              "upscalers": {"pixel_hires": {"enabled": True}}}}]}
    path.write_text(json.dumps(original), encoding="utf-8")
    before = path.read_bytes()
    store = StylePresetStore(path)
    item = store.get("old")
    assert item["include"] == {"size": False, "repair": False}
    assert item["source"] == {"workbench": "random", "pipeline_version": 4}
    assert not {"width", "height", "upscalers"} & item["settings"].keys()
    assert path.with_name("presets.json.v4.bak").read_bytes() == before
    assert StylePresetStore(path).items == store.items


def test_metadata_updates_do_not_capture_new_defaults_and_failed_write_rolls_back(tmp_path):
    async def run():
        store = StylePresetStore(tmp_path / "presets.json")
        item = await store.create({"name": "Style", "settings": settings(pipeline_version=4)})
        updated = await store.update(item["id"], {"tags": ["Warm", "warm"], "favorite": True})
        assert updated["settings"] == item["settings"] and updated["source"] == item["source"]
        assert updated["tags"] == ["Warm"]
        with pytest.raises(WorkflowError):
            await store.update(item["id"], {"include": {"size": True}})
        before, disk = copy.deepcopy(store.items), store.path.read_bytes()
        with patch.object(store, "_write_text", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                await store.update(item["id"], {"name": "Changed"})
        assert store.items == before and store.path.read_bytes() == disk
    asyncio.run(run())
