"""Failure boundaries for local source/favorites transactions."""
import asyncio
import copy
import json
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from anima_webui.catalog import PromptCatalog
from anima_webui.custom_prompts import CustomPromptStore
from anima_webui.favorites import FavoritesStore, FavoritesService
from anima_webui.persistence import StorageReadOnlyError
from anima_webui.pool_lifecycle import PoolLifecycleError, PoolLifecycleService
from test_server import APP_DIR, _write_fake_tools


def make_world(root, tools=None, comfy=None):
    tools = tools or _write_fake_tools(root)
    catalog = PromptCatalog(APP_DIR, tools)
    custom = CustomPromptStore(root / "custom.json", catalog)
    extension = FavoritesStore(root / "favorites.json")
    favorites = {"anima": FavoritesService(extension, catalog)}
    lifecycle = PoolLifecycleService(root, custom, favorites)
    return SimpleNamespace(root=root, tools=tools, comfy=None, catalog=catalog, custom=custom,
                           extension=extension, favorites=favorites, lifecycle=lifecycle)


@pytest.fixture
def world(tmp_path):
    return make_world(tmp_path)


async def save(world, profile="anima", section="pose", title="Old", text="old text"):
    return await world.lifecycle.save_text(profile, section, {"title": title, "text": text})


def test_anima_edit_and_delete_preserve_alias_and_favorite_groups(world):
    async def run():
        saved = await save(world)
        item_id = saved["item_id"]
        favorite = world.favorites["anima"]
        group = (await favorite.create_group("pose", {"name": "Keep this group"}))["group"]
        await favorite.update_item("pose", {"id": item_id, "nickname": "My alias", "groupIds": [group["id"]]})
        edited = await world.lifecycle.source("anima", "pose", "update", item_id,
                                               {"title": "New", "prompt": "new text", "subtitle": "New detail"})
        current = (await favorite.get("pose"))["items"][0]
        assert current["nickname"] == "My alias"
        assert current["customContent"] == "new text" and current["subtitle"] == "New detail"
        assert edited["favoritesSync"]["state"] == "complete"
        deleted = await world.lifecycle.source("anima", "pose", "delete", item_id)
        assert deleted["deletedItemIds"] == [item_id]
        after = await favorite.get("pose")
        assert after["items"] == []
        assert group["id"] in {group["id"] for group in after["groups"]}
        assert world.catalog.get(item_id) is None
    asyncio.run(run())


def test_anima_import_overwrite_uses_same_lifecycle(world):
    async def run():
        saved = await save(world)
        row = {"action": "overwrite", "existingId": saved["item_id"],
               "item": {"section": "pose", "title": "Old", "prompt": "imported text"}}
        result = await world.lifecycle.source("anima", "pose", "commit_import", [row], "pose", [], "Updated pack")
        assert result["updated"] == 1
        assert (await world.favorites["anima"].get("pose"))["items"][0]["customContent"] == "imported text"
        assert world.catalog.get(saved["item_id"])["prompt"] == "imported text"
    asyncio.run(run())


@pytest.mark.parametrize("mode,removed", [("keep", 0), ("exclusive", 1), ("all", 2)])
def test_anima_group_delete_only_removes_affected_sources(world, mode, removed):
    async def run():
        first = await save(world, title="First", text="first")
        second = await save(world, title="Second", text="second")
        group = (await world.custom.create_group("pose", {"name": "Remove"}))["group"]
        other = (await world.custom.create_group("pose", {"name": "Keep"}))["group"]
        await world.custom.update(first["item_id"], {"groupIds": [group["id"]]})
        await world.custom.update(second["item_id"], {"groupIds": [group["id"], other["id"]]})
        result = await world.lifecycle.source("anima", "pose", "delete_group", "pose", group["id"], delete_mode=mode)
        assert len(result["deletedItemIds"]) == removed
        assert len((await world.favorites["anima"].get("pose"))["items"]) == 2 - removed
        assert len(world.custom.list("pose")) == 2 - removed
    asyncio.run(run())


def test_source_disk_failure_rolls_back_only_affected_favorite(world):
    async def run():
        saved = await save(world)
        await save(world, title="Unrelated", text="other")
        before, favorites = world.custom.path.read_bytes(), copy.deepcopy(world.extension.profiles["anima"])
        with patch.object(world.custom, "_write_text", side_effect=OSError("disk full")):
            with pytest.raises(PoolLifecycleError) as failure:
                await world.lifecycle.source("anima", "pose", "update", saved["item_id"], {"prompt": "rejected"})
        assert failure.value.payload["code"] == "favorite_sync_failed"
        assert world.custom.path.read_bytes() == before
        assert world.extension.profiles["anima"] == favorites
        assert not world.lifecycle._journal("anima").exists()
    asyncio.run(run())


def test_started_transaction_finishes_despite_repeated_cancellation(world):
    async def run():
        saved = await save(world)
        write, loop = world.custom._write_text, asyncio.get_running_loop()
        entered, release = asyncio.Event(), threading.Event()
        def held_write(text):
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(5):
                raise TimeoutError("test writer timed out")
            write(text)
        with patch.object(world.custom, "_write_text", side_effect=held_write):
            task = asyncio.create_task(world.lifecycle.source("anima", "pose", "update", saved["item_id"], {"prompt": "finished"}))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
            finally:
                release.set()
                outcome = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(outcome[0], asyncio.CancelledError)
        assert world.catalog.get(saved["item_id"])["prompt"] == "finished"
        assert (await world.favorites["anima"].get("pose"))["items"][0]["customContent"] == "finished"
        assert not world.lifecycle._journal("anima").exists()
    asyncio.run(run())


def test_invalid_source_is_never_used_to_clean_favorites(world):
    async def run():
        await save(world)
        world.custom.path.write_text("{broken", encoding="utf-8")
        restored = make_world(world.root, world.tools, world.comfy)
        before = copy.deepcopy(world.extension.profiles["anima"])
        with pytest.raises((StorageReadOnlyError, PoolLifecycleError)):
            await restored.lifecycle.reconcile("anima", "pose")
        assert world.extension.profiles["anima"] == before
    asyncio.run(run())


@pytest.mark.parametrize("damage", ["null_items", "nonobject_item", "duplicate_id", "unknown_group", "unknown_section"])
def test_lossy_source_is_never_used_to_clean_favorites(world, damage):
    async def run():
        await save(world)
        payload = json.loads(world.custom.path.read_text(encoding="utf-8"))
        if damage == "null_items":
            payload["items"] = None
        elif damage == "nonobject_item":
            payload["items"] = [None]
        elif damage == "duplicate_id":
            payload["items"].append({**payload["items"][0], "prompt": "conflicting content"})
        elif damage == "unknown_group":
            payload["items"][0]["groupIds"] = ["missing"]
        else:
            payload["groups"]["unknown"] = []
        original = json.dumps(payload).encode()
        world.custom.path.write_bytes(original)
        before = copy.deepcopy(world.extension.profiles["anima"])
        for _ in range(2):
            restored = make_world(world.root, world.tools, world.comfy)
            assert not restored.custom.load_trusted
            with pytest.raises((StorageReadOnlyError, PoolLifecycleError)):
                await restored.lifecycle.reconcile("anima", "pose")
            assert world.extension.profiles["anima"] == before
            assert world.custom.path.read_bytes() == original
            assert not restored.lifecycle._journal("anima").exists()
    asyncio.run(run())


def test_existing_builtin_favorite_allows_first_custom_source_save(world):
    async def run():
        builtin = world.catalog.all_items("pose")[0]
        assert builtin["builtin"]
        favorite = world.favorites["anima"]
        await favorite.update_item("pose", {"id": builtin["id"]})
        before = copy.deepcopy(world.extension.profiles["anima"]["pose"]["items"])
        assert not world.custom.path.exists()
        saved = await save(world)
        assert world.custom.path.exists()
        assert world.custom.source_completeness == "complete"
        assert json.loads(world.custom.path.read_text(encoding="utf-8")).get("source_completeness", "complete") == "complete"
        assert world.custom.list("pose")[0]["id"] == saved["item_id"]
        records = (await favorite.get("pose"))["items"]
        assert len(records) == 2
        assert records[0] == before[0]
        assert records[1]["id"] == saved["item_id"]
    asyncio.run(run())


def test_invalid_journal_top_level_returns_recovery_error_without_writes(world):
    async def run():
        await save(world)
        source_before, favorites_before = world.custom.path.read_bytes(), copy.deepcopy(world.extension.profiles["anima"])
        journal = world.lifecycle._journal("anima")
        journal.write_text("[]", encoding="utf-8")
        with pytest.raises(PoolLifecycleError) as failure:
            await world.lifecycle.reconcile("anima", "pose")
        assert failure.value.payload["code"] == "favorite_sync_recovery_required"
        assert failure.value.payload["recovery_required"] is True
        assert source_before == world.custom.path.read_bytes()
        assert favorites_before == world.extension.profiles["anima"]
        assert journal.read_text(encoding="utf-8") == "[]"
    asyncio.run(run())


@pytest.mark.parametrize("boundary", ["source_read", "journal_write"])
@pytest.mark.parametrize("error_type", [OSError, TimeoutError])
def test_prepare_io_failure_never_changes_business_data(world, boundary, error_type):
    async def run():
        saved = await save(world)
        source_before, favorites_before = world.custom.path.read_bytes(), copy.deepcopy(world.extension.profiles["anima"])
        target = (patch("anima_webui.pool_lifecycle._read", side_effect=error_type("read failed"))
                  if boundary == "source_read" else
                  patch.object(world.lifecycle, "_save_journal", side_effect=error_type("journal failed")))
        with target, patch.object(world.extension, "_write_text", wraps=world.extension._write_text) as writer:
            with pytest.raises(PoolLifecycleError) as failure:
                await world.lifecycle.source("anima", "pose", "update", saved["item_id"], {"prompt": "rejected"})
            writer.assert_not_called()
        assert failure.value.payload["code"] == "favorite_sync_prepare_failed"
        assert failure.value.payload["source_committed"] is False
        assert world.custom.path.read_bytes() == source_before
        assert world.extension.profiles["anima"] == favorites_before
        assert not world.lifecycle._journal("anima").exists()
    asyncio.run(run())


def test_initial_journal_write_then_raise_cleans_undo_record_without_replaying(world):
    async def run():
        saved = await save(world)
        source_before, favorites_before = world.custom.path.read_bytes(), copy.deepcopy(world.extension.profiles["anima"])
        write, calls = world.lifecycle._save_journal, 0
        async def write_then_raise(profile, journal):
            nonlocal calls
            calls += 1
            await write(profile, journal)
            if calls == 1:
                raise OSError("journal durable but caller failed")
        with patch.object(world.lifecycle, "_save_journal", side_effect=write_then_raise), patch.object(world.extension, "_write_text", wraps=world.extension._write_text) as writer:
            with pytest.raises(PoolLifecycleError) as failure:
                await world.lifecycle.source("anima", "pose", "update", saved["item_id"], {"prompt": "never replay"})
            writer.assert_not_called()
        assert failure.value.payload["code"] == "favorite_sync_prepare_failed"
        assert world.custom.path.read_bytes() == source_before
        assert world.extension.profiles["anima"] == favorites_before
        assert not world.lifecycle._journal("anima").exists()
        restarted = make_world(world.root, world.tools, world.comfy)
        await restarted.lifecycle.reconcile("anima", "pose")
        assert restarted.custom.list("pose")[0]["prompt"] == "old text"
    asyncio.run(run())
