"""Local recovery completes before the application serves favorite reads."""
import asyncio
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from aiohttp.test_utils import TestClient, TestServer

from anima_webui.catalog import PromptCatalog
from anima_webui.custom_prompts import CHARACTER_FIELDS, CUSTOM_DATA_VERSION, CustomPromptStore
from anima_webui.persistence import StorageReadOnlyError
from anima_webui.favorites import FavoritesStore, FavoritesService, favorite_key
from anima_webui.pool_lifecycle import PoolLifecycleService
from anima_webui.server import CUSTOM_PROMPTS_KEY, FAVORITES_KEY, create_app
from test_server import APP_DIR, FakeComfy, _write_fake_tools


def prepare(root):
    tools = root / "tools" if (root / "tools").is_dir() else _write_fake_tools(root)
    catalog = PromptCatalog(APP_DIR, tools)
    custom = CustomPromptStore(root / "custom_prompts.json", catalog)
    store = FavoritesStore(root / "favorites.json")
    service = FavoritesService(store, catalog)
    lifecycle = PoolLifecycleService(root, custom, {"anima": service})
    return SimpleNamespace(root=root, tools=tools, custom=custom, store=store, lifecycle=lifecycle)


async def leave_pending(world):
    await world.lifecycle.save_text("anima", "pose", {"title": "Old", "text": "old text"})
    before = world.store.get("anima", "pose")
    after = copy.deepcopy(before)
    after["items"][0]["customContent"] = "uncommitted text"
    await world.store.save(after, "anima", "pose")
    original = world.custom.path.read_bytes()
    journal = world.lifecycle._journal("anima")
    journal.write_text(json.dumps({"version": 1, "id": "pending", "profile": "anima",
        "before_hash": hashlib.sha256(original).hexdigest(),
        "after_hash": hashlib.sha256(b"source write never happened").hexdigest(), "commit_decided": False,
        "favorites": {"pose": [{"key": favorite_key("pose", before["items"][0]),
                                 "before": before["items"][0], "after": after["items"][0]}]}}), encoding="utf-8")
    return before, original, journal


def test_startup_recovers_local_journal_without_external_favorites(tmp_path):
    class ReadOnlyComfy(FakeComfy):
        async def favorites(self):
            raise AssertionError("must not read shared favorites")
        async def save_favorites(self, payload):
            raise AssertionError("must not write shared favorites")
    async def run():
        world = prepare(tmp_path)
        before, original, journal = await leave_pending(world)
        app = create_app(app_dir=APP_DIR, comfy=ReadOnlyComfy(), data_dir=tmp_path, anima_tools_dir=world.tools)
        async with TestClient(TestServer(app)):
            assert not journal.exists()
            assert world.custom.path.read_bytes() == original
            assert app[FAVORITES_KEY].store.get("anima", "pose") == before
    asyncio.run(run())


def test_restart_does_not_replay_rejected_source_edit(tmp_path):
    async def run():
        world = prepare(tmp_path)
        before, original, journal = await leave_pending(world)
        restarted = prepare(tmp_path)
        assert await restarted.lifecycle.recover_pending() == {}
        assert not journal.exists() and restarted.custom.path.read_bytes() == original
        assert restarted.store.get("anima", "pose") == before
    asyncio.run(run())


async def leave_v4_character_journal(world, committed):
    """Represent either durable side of an interrupted pre-upgrade transaction."""
    await world.lifecycle.save_text("anima", "character", {"title": "Custom Alpha", "text": "alpha, series"})
    before = (await world.lifecycle._read_favorites("anima", ["character"]))["character"]
    after = copy.deepcopy(before)
    after["items"][0]["customContent"] = "series, alpha, white shirt"
    original = json.loads(world.custom.path.read_text(encoding="utf-8"))
    original["version"] = 4
    for item in original["items"]:
        for key in CHARACTER_FIELDS:
            item.pop(key, None)
    changed = copy.deepcopy(original)
    changed["items"][0]["prompt"] = "series, alpha, white shirt"
    before_bytes = json.dumps(original).encode()
    after_bytes = json.dumps(changed).encode()
    source_bytes = after_bytes if committed else before_bytes
    world.custom.path.write_bytes(source_bytes)
    # Force recovery to actually apply the journal, rather than only remove it.
    await world.store.save(before if committed else after, "anima", "character")
    journal = world.lifecycle._journal("anima")
    journal.write_text(json.dumps({
        "version": 1, "id": "pending-v4", "profile": "anima",
        "before_hash": hashlib.sha256(before_bytes).hexdigest(),
        "after_hash": hashlib.sha256(after_bytes).hexdigest(), "commit_decided": committed,
        "favorites": {"character": [{"key": favorite_key("character", before["items"][0]),
                                       "before": before["items"][0], "after": after["items"][0]}]},
    }), encoding="utf-8")
    return SimpleNamespace(source_bytes=source_bytes, journal=journal, before=before, after=after)


@pytest.mark.parametrize("committed", [False, True])
def test_v4_source_migration_waits_for_both_journal_recovery_endpoints(tmp_path, committed):
    async def run():
        world = prepare(tmp_path)
        pending = await leave_v4_character_journal(world, committed)
        app = create_app(app_dir=APP_DIR, comfy=FakeComfy(), data_dir=tmp_path, anima_tools_dir=world.tools)
        store = app[CUSTOM_PROMPTS_KEY]
        assert store.migration_pending and store.storage_status()["read_only"]
        assert store.path.read_bytes() == pending.source_bytes
        with pytest.raises(StorageReadOnlyError):
            store.ensure_writable()
        async with TestClient(TestServer(app)):
            assert not pending.journal.exists()
            assert not store.migration_pending and not store.storage_status()["read_only"]
            assert app[FAVORITES_KEY].store.get("anima", "character") == (pending.after if committed else pending.before)
            migrated = json.loads(store.path.read_text(encoding="utf-8"))
            assert migrated["version"] == CUSTOM_DATA_VERSION
            assert migrated["items"][0]["character_status"] == "ready"
            assert migrated["items"][0]["prompt"] == ("series, alpha, white shirt" if committed else "alpha, series")
            assert store.path.with_name("custom_prompts.json.pre-v5.bak").read_bytes() == pending.source_bytes
            # A later explicit edit and reload must retain the post-recovery v5
            # classification instead of falling back to journal-era metadata.
            item = store.items[0]
            parts = copy.deepcopy(item["character_parts"])
            for part in parts:
                part["source"] = "manual"
            edited = await store.update(item["id"], {"title": "Reviewed", "character_parts": parts})
            store.reload()
            assert store.load_trusted and store.items[0] == edited
    asyncio.run(run())
