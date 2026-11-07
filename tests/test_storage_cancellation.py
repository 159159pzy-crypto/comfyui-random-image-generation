from __future__ import annotations

import asyncio
import copy
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from anima_webui.custom_prompts import CustomPromptStore
from anima_webui.favorites import FavoritesExtensionStore
from anima_webui.lora_triggers import LoraTriggerOverrideStore
from anima_webui.prompt_rules import PromptRuleStore
from anima_webui.style_presets import StylePresetStore


class MemoryCatalog:
    def __init__(self):
        self.items = []

    def set_custom_items(self, items):
        self.items = copy.deepcopy(items)


FACTORIES = {
    "custom": lambda path: CustomPromptStore(path, MemoryCatalog()),
    "styles": StylePresetStore,
    "triggers": LoraTriggerOverrideStore,
    "rules": PromptRuleStore,
    "favorites": FavoritesExtensionStore,
}


def state(store):
    return copy.deepcopy({
        name: getattr(store, name) for name in
        ("items", "groups", "profiles", "profile_overrides", "overrides", "assignments", "initialized")
        if hasattr(store, name)
    })


def mutate(name, store, value):
    if name == "custom":
        return store.create({"section": "pose", "title": value, "prompt": value})
    if name == "styles":
        return store.create({"name": value, "settings": {}})
    if name == "triggers":
        return store.set(f"{value}.safetensors", [value])
    if name == "rules":
        return store.create({"from": value, "to": f"new-{value}"})
    return store.save({"items": [{"id": value, "name": value}], "groups": []})


class StorageCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_write_finishes_memory_and_disk_before_unlocking(self):
        for name, factory in FACTORIES.items():
            with self.subTest(store=name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "store.json"
                store = factory(path)
                real_write = store._write_text
                entered = asyncio.Event()
                second_write = threading.Event()
                release = threading.Event()
                finished = threading.Event()
                loop = asyncio.get_running_loop()
                calls = 0

                def blocked_write(text):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        loop.call_soon_threadsafe(entered.set)
                        if not release.wait(5):
                            raise TimeoutError("test writer was not released")
                    else:
                        second_write.set()
                    try:
                        real_write(text)
                    finally:
                        finished.set()

                with patch.object(store, "_write_text", side_effect=blocked_write):
                    first = asyncio.create_task(mutate(name, store, "first"))
                    second = None
                    try:
                        await asyncio.wait_for(entered.wait(), 2)
                        first.cancel()
                        await asyncio.sleep(0)
                        # A repeated cancellation must not detach a still
                        # running write or release its mutation lock either.
                        first.cancel()
                        second = asyncio.create_task(mutate(name, store, "second"))
                        await asyncio.sleep(0.03)
                        self.assertFalse(first.done())
                        self.assertFalse(second_write.is_set())
                    finally:
                        release.set()
                        await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
                        self.assertTrue(await asyncio.to_thread(finished.wait, 2))
                    self.assertTrue(first.cancelled())
                    self.assertIsNotNone(second)
                    self.assertIsNone(second.exception())
                self.assertEqual(state(store), state(factory(path)))
                if name == "custom":
                    self.assertEqual(store.catalog.items, store.items)

    async def test_cancelled_lock_waiter_does_not_modify_store(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LoraTriggerOverrideStore(Path(directory) / "store.json")
            async with store._lock:
                waiting = asyncio.create_task(store.set("cancelled.safetensors", ["cancelled"]))
                await asyncio.sleep(0)
                waiting.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await waiting
            await store.set("kept.safetensors", ["kept"])
            self.assertEqual(store.overrides, {"kept.safetensors": ["kept"]})

    async def test_write_failures_restore_memory_and_do_not_leak_into_later_saves(self):
        for name, factory in FACTORIES.items():
            if name == "favorites":
                continue  # Favorites already protect failed writes as read-only.
            with self.subTest(store=name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "store.json"
                store = factory(path)
                await mutate(name, store, "kept")
                before, original = state(store), path.read_bytes()
                with patch.object(store, "_write_text", side_effect=OSError("disk full")):
                    with self.assertRaises(OSError):
                        await mutate(name, store, "failed")
                self.assertEqual(state(store), before)
                self.assertEqual(path.read_bytes(), original)
                await mutate(name, store, "later")
                self.assertNotIn("failed", path.read_text(encoding="utf-8"))
                self.assertEqual(state(store), state(factory(path)))


    async def test_failed_updates_deletes_and_group_edits_restore_original_state(self):
        for name in ("custom", "styles", "triggers", "rules"):
            with self.subTest(store=name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "store.json"
                store = FACTORIES[name](path)
                item = await mutate(name, store, "kept")
                if name == "custom":
                    group = (await store.create_group("pose", {"name": "kept group"}))["group"]
                    item = await store.update(item["id"], {"groupIds": [group["id"]]})
                    operations = [
                        lambda: store.update(item["id"], {"title": "failed"}),
                        lambda: store.delete(item["id"]),
                        lambda: store.create_group("pose", {"name": "failed"}),
                        lambda: store.update_group("pose", group["id"], {"name": "failed"}),
                        lambda: store.delete_group("pose", group["id"]),
                    ]
                elif name == "styles":
                    operations = [
                        lambda: store.update(item["id"], {"name": "failed"}),
                        lambda: store.delete(item["id"]),
                    ]
                elif name == "triggers":
                    operations = [
                        lambda: store.set("KEPT.safetensors", ["failed"]),
                        lambda: store.delete("kept.safetensors"),
                    ]
                else:
                    operations = [
                        lambda: store.update(item["id"], {"to": "failed"}),
                        lambda: store.delete(item["id"]),
                    ]
                before, original = state(store), path.read_bytes()
                for operation in operations:
                    with patch.object(store, "_write_text", side_effect=OSError("disk full")):
                        with self.assertRaises(OSError):
                            await operation()
                    self.assertEqual(state(store), before)
                    self.assertEqual(path.read_bytes(), original)
                    if name == "custom":
                        self.assertEqual(store.catalog.items, store.items)

    async def test_write_failure_after_cancellation_rolls_back_before_next_write(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StylePresetStore(Path(directory) / "styles.json")
            entered, release = asyncio.Event(), threading.Event()
            real_write, loop = store._write_text, asyncio.get_running_loop()
            calls = 0

            def fail_first_write(text):
                nonlocal calls
                calls += 1
                if calls == 1:
                    loop.call_soon_threadsafe(entered.set)
                    if not release.wait(5):
                        raise TimeoutError("test writer was not released")
                    raise OSError("disk full")
                real_write(text)

            with patch.object(store, "_write_text", side_effect=fail_first_write):
                first = asyncio.create_task(store.create({"name": "failed", "settings": {}}))
                second = None
                try:
                    await asyncio.wait_for(entered.wait(), 2)
                    first.cancel()
                    second = asyncio.create_task(store.create({"name": "kept", "settings": {}}))
                    await asyncio.sleep(0)
                finally:
                    release.set()
                    results = await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
                self.assertIsInstance(results[0], asyncio.CancelledError)
                self.assertEqual(results[1]["name"], "kept")
                self.assertEqual([item["name"] for item in store.items], ["kept"])
                self.assertEqual(state(store), state(StylePresetStore(store.path)))
