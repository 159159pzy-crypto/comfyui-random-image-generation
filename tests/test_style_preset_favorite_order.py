import asyncio
import copy
import json
from unittest.mock import patch

import pytest

from anima_webui.style_presets import StylePresetStore


def at(store, day):
    timestamp = f"2026-11-{day:02d}T00:00:00+00:00"
    store._now = lambda: timestamp
    return timestamp


def names(store, profile="anima"):
    return [item["name"] for item in store.list(profile)["items"]]


@pytest.mark.parametrize("profile", ["anima"])
def test_new_favorites_lead_and_unfavorite_restores_content_order_after_reload(tmp_path, profile):
    async def run():
        store = StylePresetStore(tmp_path / "presets.json")
        items = []
        for day, name in enumerate(("Oldest", "Middle", "Newest"), 1):
            at(store, day)
            items.append(await store.create({"name": name, "model_profile": profile, "settings": {}}))
        original_order = names(store, profile)
        for item, day in zip(items[:2], (4, 5)):
            favorite_time = at(store, day)
            saved = await store.update(item["id"], {"model_profile": profile, "favorite": True})
            assert saved["favorite_at"] == favorite_time
            assert saved["updated_at"] == item["updated_at"]
        assert names(store, profile) == ["Middle", "Oldest", "Newest"]
        assert names(StylePresetStore(store.path), profile) == names(store, profile)
        for item, day in zip(items[:2], (6, 7)):
            at(store, day)
            saved = await store.update(item["id"], {"favorite": False})
            assert saved["favorite_at"] is None
            assert saved["updated_at"] == item["updated_at"]
        assert names(store, profile) == original_order
        assert names(StylePresetStore(store.path), profile) == original_order
        assert [item["id"] for item in store.items] == [item["id"] for item in items]
    asyncio.run(run())


def test_editor_favorite_only_save_and_repeated_favorite_preserve_content_time(tmp_path):
    async def run():
        store = StylePresetStore(tmp_path / "presets.json")
        created = at(store, 1)
        item = await store.create({"name": "Ink", "tags": ["Warm"], "settings": {},
                                   "created_at": "forged", "updated_at": "forged", "favorite_at": "forged"})
        assert item["created_at"] == item["updated_at"] == created
        assert item["favorite_at"] is None
        favorite_time = at(store, 2)
        editor_payload = {key: copy.deepcopy(item[key]) for key in
                          ("model_profile", "name", "favorite", "groups", "tags", "cover_id")}
        editor_payload.update(name=" Ink ", tags=["Warm", "warm"], favorite=True,
                              favorite_at="forged", updated_at="forged", created_at="forged")
        saved = await store.update(item["id"], editor_payload)
        assert saved["favorite_at"] == favorite_time
        assert saved["updated_at"] == saved["created_at"] == created
        at(store, 3)
        again = await store.update(item["id"], {"favorite": True, "favorite_at": "forged"})
        assert again == saved
        at(store, 4)
        canceled = await store.update(item["id"], {**editor_payload, "favorite": False})
        assert canceled["favorite_at"] is None and canceled["updated_at"] == created
        refavorite_time = at(store, 5)
        refavorited = await store.update(item["id"], {"favorite": True})
        assert refavorited["favorite_at"] == refavorite_time
        assert refavorited["updated_at"] == created
    asyncio.run(run())


@pytest.mark.parametrize("change", [
    {"name": "Renamed"}, {"tags": ["Warm"]}, {"cover_id": "a" * 64}, {"settings": {"steps": 17}},
])
def test_content_edits_update_content_time_without_reordering_favorites(tmp_path, change):
    async def run():
        store = StylePresetStore(tmp_path / "presets.json")
        at(store, 1)
        first = await store.create({"name": "First", "favorite": True, "settings": {}})
        at(store, 2)
        second = await store.create({"name": "Second", "favorite": True, "settings": {}})
        edited_time = at(store, 3)
        edited = await store.update(first["id"], {**change, "favorite": True})
        assert edited["updated_at"] == edited_time
        assert edited["favorite_at"] == first["favorite_at"]
        assert [item["id"] for item in store.list()["items"]] == [second["id"], first["id"]]
    asyncio.run(run())


def test_batch_favorites_keep_each_content_time_and_ignore_selection_order(tmp_path):
    async def run():
        store = StylePresetStore(tmp_path / "presets.json")
        items = []
        for day in range(1, 4):
            at(store, day)
            items.append(await store.create({"name": str(day), "settings": {}}))
        initial = names(store)
        at(store, 4)
        await store.update(items[2]["id"], {"favorite": True})
        payload = {"model_profile": "anima", "ids": [items[0]["id"], items[1]["id"]],
                   "action": "favorite", "value": True}
        favorite_time = at(store, 5)
        await store.batch(payload)
        assert names(store) == ["2", "1", "3"]
        for item in items[:2]:
            saved = store.get(item["id"])
            assert saved["updated_at"] == item["updated_at"]
            assert saved["favorite_at"] == favorite_time
        at(store, 6)
        await store.batch({**payload, "ids": list(reversed(payload["ids"]))})
        assert names(store) == ["2", "1", "3"]
        assert all(store.get(item["id"])["favorite_at"] == favorite_time for item in items[:2])
        at(store, 7)
        await store.batch({**payload, "ids": [item["id"] for item in items], "value": False})
        assert names(store) == initial
        assert names(StylePresetStore(store.path)) == initial
        assert all(store.get(item["id"])["favorite_at"] is None for item in items)
        before, disk = copy.deepcopy(store.items), store.path.read_bytes()
        with patch.object(store, "_write_text", side_effect=OSError("disk full")):
            with pytest.raises(OSError):
                await store.batch(payload)
            with pytest.raises(OSError):
                await store.update(items[0]["id"], {"favorite": True})
        assert store.items == before and store.path.read_bytes() == disk
    asyncio.run(run())


def test_group_auto_favorites_receive_times_only_on_new_favorite_transitions(tmp_path):
    async def run():
        store = StylePresetStore(tmp_path / "presets.json")
        groups = [await store.create_group({"name": name}) for name in ("One", "Two", "Three")]
        created_time = at(store, 1)
        grouped = await store.create({"name": "Grouped", "settings": {}, "favorite": False,
                                      "groups": [groups[0]["id"]], "favorite_at": "forged"})
        assert grouped["favorite"] and grouped["favorite_at"] == created_time
        plain = await store.create({"name": "Plain", "settings": {}})
        favorite_time = at(store, 2)
        updated = await store.update(plain["id"], {"groups": [groups[0]["id"]]})
        assert updated["favorite"] and updated["favorite_at"] == favorite_time
        assert updated["updated_at"] == favorite_time
        at(store, 3)
        batch = {"model_profile": "anima", "ids": [plain["id"]], "action": "groups",
                 "value": {"mode": "add", "values": [groups[1]["id"]]}}
        await store.batch(batch)
        assert store.get(plain["id"])["favorite_at"] == favorite_time
        at(store, 4)
        await store.update(plain["id"], {"favorite": False})
        new_favorite_time = at(store, 5)
        await store.batch({**batch, "value": {"mode": "add", "values": [groups[2]["id"]]}})
        assert store.get(plain["id"])["favorite_at"] == new_favorite_time
        at(store, 6)
        await store.delete_group(groups[2]["id"], "anima")
        assert store.get(plain["id"])["favorite_at"] == new_favorite_time
        assert StylePresetStore(store.path).items == store.items
    asyncio.run(run())


def legacy_v3_file(tmp_path):
    async def create():
        store = StylePresetStore(tmp_path / "presets.json")
        group = await store.create_group({"name": "Portrait"})
        at(store, 1)
        await store.create({"name": "Random", "settings": {"workbench": "random", "width": 960, "height": 640},
                            "include": {"size": True, "repair": True},
                            "groups": [group["id"]], "tags": ["Warm"], "cover_id": "a" * 64})
        at(store, 2)
        await store.create({"name": "Other", "model_profile": "anima", "settings": {}, "favorite": True})
        at(store, 3)
        await store.create({"name": "Plain", "settings": {}})
        return store
    store = asyncio.run(create())
    payload = json.loads(store.path.read_text("utf-8"))
    payload.update(version=3, cover_references_complete=False)
    for item in payload["items"]:
        item.pop("favorite_at")
    store.path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return store.path, payload


def test_v3_migration_backs_up_and_preserves_all_recipe_metadata(tmp_path):
    path, payload = legacy_v3_file(tmp_path)
    original = path.read_bytes()
    store = StylePresetStore(path)
    assert path.with_name(path.name + ".v4.bak").read_bytes() == original
    assert not store.cover_references_complete
    assert store.groups == payload["groups"]
    for item, previous in zip(store.items, payload["items"]):
        assert {key: value for key, value in item.items() if key != "favorite_at"} == previous
        assert item["favorite_at"] == (previous["updated_at"] if previous["favorite"] else None)
    assert names(store) == ["Other", "Random", "Plain"]
    assert json.loads(path.read_text("utf-8"))["version"] == 4
    assert StylePresetStore(path).items == store.items
    assert len(list(path.parent.glob("presets.json.v4*.bak"))) == 1


@pytest.mark.parametrize("failure", ["backup", "write"])
def test_v3_migration_failure_keeps_original_and_protects_store(tmp_path, failure):
    path, payload = legacy_v3_file(tmp_path)
    original = path.read_bytes()
    target = "anima_webui.style_presets.backup_for_migration" if failure == "backup" else "anima_webui.style_presets.StylePresetStore._write_text"
    with patch(target, side_effect=OSError("disk full")):
        store = StylePresetStore(path)
    assert path.read_bytes() == original
    assert store.read_only_reason
    assert len(store.items) == len(payload["items"])
    assert store.groups == payload["groups"]
    assert not store.cover_references_complete
