"""Local recovery completes before the application serves favorite reads."""
import asyncio
import copy
import hashlib
import json
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer

from anima_webui.catalog import PromptCatalog
from anima_webui.custom_prompts import CustomPromptStore
from anima_webui.favorites import FavoritesStore, FavoritesService, favorite_key
from anima_webui.pool_lifecycle import PoolLifecycleService
from anima_webui.server import FAVORITES_KEY, create_app
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
