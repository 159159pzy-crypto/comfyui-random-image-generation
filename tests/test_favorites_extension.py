"""All favorite categories use one isolated, atomic local store."""
import asyncio
import json
from unittest.mock import patch

import pytest

from anima_webui.favorites import FAVORITE_SECTIONS, FavoritesStore, FavoritesService
from anima_webui.persistence import StorageReadOnlyError


class Catalog:
    def get(self, item_id):
        section = item_id.split(":", 1)[0]
        return {"id": item_id, "favorite_key": item_id, "section": section,
                "title": item_id, "prompt": "smile", "builtin": False}


def test_six_categories_are_local_and_survive_restart(tmp_path):
    async def run():
        store = FavoritesStore(tmp_path / "favorites.json")
        service = FavoritesService(store, Catalog())
        for section in FAVORITE_SECTIONS:
            assert (await service.get(section))["items"] == []
            value = {"name": "anmi"} if section == "artist" else {"id": section + ":one"}
            await service.update_item(section, value)
        restored = FavoritesService(FavoritesStore(store.path), Catalog())
        for section in FAVORITE_SECTIONS:
            assert len((await restored.get(section))["items"]) == 1
        payload = json.loads(store.path.read_text(encoding="utf-8"))
        assert set(payload["sections"]) == set(FAVORITE_SECTIONS)
        other = FavoritesService(FavoritesStore(tmp_path / "other" / "favorites.json"), Catalog())
        for section in FAVORITE_SECTIONS:
            assert (await other.get(section))["items"] == []
    asyncio.run(run())


def test_simultaneous_updates_preserve_every_category(tmp_path):
    async def run():
        service = FavoritesService(FavoritesStore(tmp_path / "favorites.json"), Catalog())
        await asyncio.gather(*(service.update_item(section, {"id": f"{section}:{index}"})
                               for section in FAVORITE_SECTIONS if section != "artist" for index in range(8)))
        restored = FavoritesStore(service.store.path)
        for section in FAVORITE_SECTIONS[:-1]:
            assert len(restored.get("anima", section)["items"]) == 8
    asyncio.run(run())


def test_failed_write_preserves_memory_and_durable_content(tmp_path):
    async def run():
        store = FavoritesStore(tmp_path / "favorites.json")
        service = FavoritesService(store, Catalog())
        await service.update_item("expression", {"id": "expression:original"})
        before = store.path.read_bytes()
        with patch.object(store, "_write_text", side_effect=OSError("disk unavailable")):
            with pytest.raises(StorageReadOnlyError):
                await service.update_item("expression", {"id": "expression:lost"})
        assert store.path.read_bytes() == before
        assert (await service.get("expression"))["favorite_keys"] == ["expression:original"]
        assert store.storage_status()["read_only"]
    asyncio.run(run())


@pytest.mark.parametrize("value", ["{broken", '{"version":99,"sections":{}}',
    json.dumps({"version": 1, "sections": {"pose": {"groups": [], "items": [None]}}}),
    json.dumps({"version": 1, "sections": {"pose": {"groups": [], "items": [
        {"id": "one"}, {"id": "one"}]}}}),
    json.dumps({"version": 1, "sections": {"pose": {"groups": [], "items": [
        {"id": "one", "groupIds": ["missing"]}]}}}),
])
def test_invalid_store_never_drops_data_or_overwrites_original(tmp_path, value):
    path = tmp_path / "favorites.json"
    path.write_text(value, encoding="utf-8")
    for _ in range(2):
        store = FavoritesStore(path)
        assert not store.source_trusted and store.storage_status()["read_only"]
        assert path.read_text(encoding="utf-8") == value
        with pytest.raises(StorageReadOnlyError):
            asyncio.run(store.save({"groups": [], "items": []}, "anima", "pose"))


def test_external_change_blocks_stale_write(tmp_path):
    async def run():
        store = FavoritesStore(tmp_path / "favorites.json")
        service = FavoritesService(store, Catalog())
        await service.update_item("pose", {"id": "pose:one"})
        external = FavoritesService(FavoritesStore(store.path), Catalog())
        await external.update_item("pose", {"id": "pose:two"})
        before = store.path.read_bytes()
        with pytest.raises(StorageReadOnlyError):
            await service.update_item("pose", {"id": "pose:three"})
        assert store.path.read_bytes() == before
    asyncio.run(run())
