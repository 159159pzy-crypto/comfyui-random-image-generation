from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anima_webui.custom_prompts import CustomPromptStore
from anima_webui.history import HistoryMigrationError, HistoryStore
from anima_webui.lora_triggers import LoraTriggerOverrideStore
from anima_webui.persistence import StorageReadOnlyError
from anima_webui.prompt_rules import PromptRuleStore
from anima_webui.style_presets import StylePresetStore


class MemoryCatalog:
    def __init__(self):
        self.items = []

    def set_custom_items(self, items):
        self.items = copy.deepcopy(items)


FACTORIES = {
    "custom_prompts": lambda path: CustomPromptStore(path, MemoryCatalog()),
    "style_presets": StylePresetStore,
    "lora_triggers": LoraTriggerOverrideStore,
    "prompt_rules": PromptRuleStore,
}
VALID_EMPTY = {
    "custom_prompts": {"version": 4, "groups": {}, "items": []},
    "style_presets": {"version": 2, "items": []},
    "lora_triggers": {"version": 2, "overrides": {}},
    "prompt_rules": {"version": 1, "disabledBuiltins": [], "rules": []},
}
REFERENCED_SOURCE_STORES = {"custom_prompts"}


def memory_state(store):
    return copy.deepcopy({
        key: getattr(store, key)
        for key in (
            "items", "groups", "profile_overrides", "overrides", "custom_rules",
            "disabled_builtins", "assignments", "initialized",
        )
        if hasattr(store, key)
    })


def mutations(store):
    name = store.store_name
    if name == "custom_prompts":
        return [
            lambda: store.create({"section": "pose", "title": "Test", "prompt": "standing"}),
            lambda: store.update("custom:missing", {}),
            lambda: store.delete("custom:missing"),
            lambda: store.create_group("pose", {"name": "Test"}),
            lambda: store.update_group("pose", "missing", {"name": "Test"}),
            lambda: store.delete_group("pose", "missing"),
            lambda: store.commit_import([], "pose", bundle_name="Test"),
        ]
    if name == "style_presets":
        return [
            lambda: store.create({"name": "Test", "settings": {}}),
            lambda: store.update("missing", {"name": "Test"}),
            lambda: store.delete("missing"),
        ]
    if name == "lora_triggers":
        return [lambda: store.set("test.safetensors", ["word"]), lambda: store.delete("test.safetensors")]
    if name == "prompt_rules":
        return [
            lambda: store.create({"from": "old", "to": "new"}),
            lambda: store.update("prompt_rule_missing", {"from": "old", "to": "new"}),
            lambda: store.delete("prompt_rule_missing"),
        ]
    raise AssertionError(name)


class StorageRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    async def test_failed_json_backup_keeps_bytes_and_blocks_all_mutations_before_memory_changes(self):
        for name, factory in FACTORIES.items():
            with self.subTest(store=name):
                path = self.root / f"{name}.json"
                original = b'{ damaged json with irreplaceable user data'
                path.write_bytes(original)
                operation = "shutil.copy2" if name in REFERENCED_SOURCE_STORES else "os.replace"
                with patch(f"anima_webui.persistence.{operation}", side_effect=PermissionError("locked source")) as backup:
                    store = factory(path)
                backup.assert_called()
                self.assertTrue(store.storage_status()["read_only"])
                self.assertIn("读取失败" if name == "custom_prompts" else "备份失败", store.read_only_reason)
                self.assertEqual(path.read_bytes(), original)
                self.assertFalse(list(self.root.glob(f"{path.name}.corrupt*")))
                before = memory_state(store)
                for mutate in mutations(store):
                    with self.assertRaises(StorageReadOnlyError) as caught:
                        await mutate()
                    self.assertEqual(caught.exception.code, "storage_read_only")
                    self.assertEqual(caught.exception.store, name)
                    self.assertEqual(caught.exception.path, path)
                    self.assertEqual(memory_state(store), before)
                    self.assertEqual(path.read_bytes(), original)
                with self.assertRaises(StorageReadOnlyError):
                    store._write_text("{}")

                if name == "lora_triggers":
                    self.assertEqual(store.effective("test.safetensors", ["source"], "anima"), (["source"], False))
                    self.assertEqual(memory_state(store), before)

                # Repair is explicit; a successful reload clears the lock.
                path.write_text(json.dumps(VALID_EMPTY[name]), encoding="utf-8")
                store.reload()
                self.assertIsNone(store.read_only_reason)
                store.ensure_writable()

    async def test_read_permission_error_does_not_quarantine_valid_json(self):
        for name, factory in FACTORIES.items():
            with self.subTest(store=name):
                path = self.root / f"{name}.json"
                original = json.dumps(VALID_EMPTY[name]).encode()
                path.write_bytes(original)
                read_method = "read_bytes" if name in REFERENCED_SOURCE_STORES else "read_text"
                with patch.object(Path, read_method, side_effect=PermissionError("read denied")), patch(
                    "anima_webui.persistence.backup_corrupt_file"
                ) as backup:
                    store = factory(path)
                backup.assert_not_called()
                self.assertIn("读取失败", store.read_only_reason)
                self.assertEqual(path.read_bytes(), original)
                if name in REFERENCED_SOURCE_STORES:
                    self.assertFalse(store.load_trusted)
                    self.assertEqual(store.load_state, "unreadable")

    async def test_referenced_sources_remain_read_only_while_other_stores_can_recover_empty(self):
        for name, factory in FACTORIES.items():
            with self.subTest(store=name):
                path = self.root / f"{name}.json"
                path.write_bytes(b"{broken")
                store = factory(path)
                suffix = ".v4.bak" if name == "custom_prompts" else ".corrupt.bak"
                self.assertEqual(path.with_name(path.name + suffix).read_bytes(), b"{broken")
                if name in REFERENCED_SOURCE_STORES:
                    for current in (store, factory(path)):
                        self.assertFalse(current.load_trusted)
                        self.assertEqual(current.load_state, "invalid")
                        with self.assertRaises(StorageReadOnlyError):
                            await mutations(current)[0]()
                        self.assertEqual(path.read_bytes(), b"{broken")
                else:
                    self.assertIsNone(store.read_only_reason)
                    await mutations(store)[0]()
                    self.assertIsInstance(json.loads(path.read_text(encoding="utf-8")), dict)

    async def test_migration_backup_or_write_failure_preserves_valid_source_and_read_view(self):
        legacy = {
            "custom_prompts": {"version": 3, "items": [{"id": "custom:kept", "section": "pose", "title": "Kept", "prompt": "standing"}], "groups": {}},
            "style_presets": {"version": 1, "items": [{"id": "kept", "name": "Kept", "settings": {"width": 768}}]},
            "lora_triggers": {"version": 1, "overrides": {"kept.safetensors": ["kept"]}},
        }
        classes = {"custom_prompts": CustomPromptStore, "style_presets": StylePresetStore,
                   "lora_triggers": LoraTriggerOverrideStore}
        for name, payload in legacy.items():
            for failure in ("backup", "write"):
                with self.subTest(store=name, failure=failure):
                    path = self.root / f"{name}-{failure}.json"
                    original = json.dumps(payload).encode()
                    path.write_bytes(original)
                    target = (
                        patch("anima_webui.persistence.shutil.copy2", side_effect=PermissionError("backup denied"))
                        if failure == "backup" else
                        patch.object(classes[name], "_write_text", side_effect=PermissionError("write denied"))
                    )
                    with target:
                        store = FACTORIES[name](path)
                    self.assertTrue(store.read_only_reason)
                    self.assertEqual(path.read_bytes(), original)
                    self.assertFalse(list(self.root.glob(f"{path.name}.corrupt*")))
                    if name == "lora_triggers":
                        self.assertEqual(store.get("kept.safetensors"), ["kept"])
                    else:
                        self.assertEqual(len(store.items), 1)
                    before = memory_state(store)
                    with self.assertRaises(StorageReadOnlyError):
                        await mutations(store)[0]()
                    self.assertEqual(memory_state(store), before)

    async def test_history_failed_quarantine_has_empty_view_and_rejects_all_writes(self):
        path = self.root / "history.sqlite3"
        original = b"not a sqlite database; original data must remain"
        path.write_bytes(original)
        with patch("anima_webui.history.backup_corrupt_file", side_effect=PermissionError("locked source")):
            store = HistoryStore(path)
        try:
            self.assertFalse(store.available)
            self.assertFalse(store.storage_status()["available"])
            self.assertEqual((await store.list_images())["total"], 0)
            with self.assertRaises(KeyError):
                await store.get_image(1)
            for mutate in (
                lambda: store.create_batch("test", 1, {}),
                lambda: store.update_batch("test", completed=1),
                lambda: store.add_image(),
                lambda: store.delete_image(1),
            ):
                with self.assertRaises(StorageReadOnlyError):
                    await mutate()
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(list(self.root.glob("*.corrupt*")))
        finally:
            await store.close()

    async def test_history_busy_error_is_not_quarantined(self):
        path = self.root / "history.sqlite3"
        store = HistoryStore(path)
        await store.close()
        original = path.read_bytes()
        with patch.object(HistoryStore, "_open", side_effect=sqlite3.OperationalError("database is locked")), patch(
            "anima_webui.history.backup_corrupt_file"
        ) as backup:
            store = HistoryStore(path)
        try:
            backup.assert_not_called()
            self.assertFalse(store.available)
            self.assertEqual((await store.list_images())["items"], [])
            self.assertEqual(path.read_bytes(), original)
        finally:
            await store.close()

    async def test_history_migration_backup_failure_does_not_replace_source(self):
        path = self.root / "history.sqlite3"
        store = HistoryStore(path)
        await store.create_batch("legacy", 1, {"count": 1})
        await store.close()
        original = path.read_bytes()
        with patch.object(HistoryStore, "_backup_before_migration", side_effect=HistoryMigrationError("backup denied")), patch(
            "anima_webui.history.backup_corrupt_file"
        ) as quarantine:
            store = HistoryStore(path)
        try:
            quarantine.assert_not_called()
            self.assertFalse(store.available)
            self.assertEqual(path.read_bytes(), original)
            with self.assertRaises(StorageReadOnlyError):
                store.ensure_writable()
        finally:
            await store.close()

    async def test_history_schema_backup_failure_does_not_alter_legacy_file(self):
        path = self.root / "legacy.sqlite3"
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE batches (id TEXT, settings_json TEXT)")
        connection.execute("INSERT INTO batches VALUES ('legacy', '{}')")
        connection.commit()
        connection.close()
        original = path.read_bytes()
        with patch.object(HistoryStore, "_backup_before_migration", side_effect=HistoryMigrationError("backup denied")):
            store = HistoryStore(path)
        try:
            self.assertFalse(store.available)
            self.assertEqual(path.read_bytes(), original)
        finally:
            await store.close()
