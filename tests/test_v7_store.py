from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from anima_webui.history import HistoryStore
from anima_webui.migrations import prepare_v7_migration
from anima_webui.v7_store import DraftConflictError, V7Store


class V7StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = V7Store(self.root / "data" / "studio.sqlite3")

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_schema_is_idempotent_and_drafts_use_optimistic_revision(self) -> None:
        first = self.store.get_draft("natural")
        self.assertEqual(first["revision"], 0)
        saved = self.store.save_draft(
            "natural",
            {"workspace": "natural", "intent": {"workspace": "natural", "positive_prompt": "cat"}},
            expected_revision=0,
        )
        self.assertEqual(saved["revision"], 1)
        self.assertEqual(len(saved["digest"]), 64)
        unchanged = self.store.save_draft(
            "natural",
            {"workspace": "natural", "intent": {"workspace": "natural", "positive_prompt": "cat"}},
            expected_revision=1,
        )
        self.assertEqual(unchanged["revision"], 1)
        with self.assertRaises(DraftConflictError) as conflict:
            self.store.save_draft("natural", {"workspace": "natural"}, expected_revision=0)
        self.assertEqual(conflict.exception.current["revision"], 1)

        second = V7Store(self.store.path)
        try:
            self.assertEqual(second.get_draft("natural")["revision"], 1)
            tables = {
                row[0]
                for row in second.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertTrue(
                {
                    "drafts",
                    "presets",
                    "intents",
                    "studio_events",
                    "deprecation_calls",
                }.issubset(tables)
            )
        finally:
            second.close()

    def test_events_and_intents_are_cursor_addressable(self) -> None:
        intent = self.store.create_intent(
            {"workspace": "random", "positive_prompt": "sunset"}, workspace="random"
        )
        self.assertEqual(self.store.get_intent(intent["id"])["positive_prompt"], "sunset")
        first = self.store.append_event("intent.created", intent, source_workspace="random")
        second = self.store.append_event("history.created", {"id": 9}, source_workspace="random")
        self.assertEqual(self.store.latest_event_id(), second["id"])
        self.assertEqual(
            [item["id"] for item in self.store.read_events(after_id=first["id"])],
            [second["id"]],
        )

    def test_verified_migration_imports_legacy_presets_once(self) -> None:
        preset_path = self.root / "data" / "style_presets.json"
        preset_path.parent.mkdir(parents=True, exist_ok=True)
        preset_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "items": [
                        {
                            "id": "preset_old",
                            "name": "Old Style",
                            "favorite": True,
                            "settings": {"model_name": "anima.safetensors", "loras": []},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        first = prepare_v7_migration(self.root, self.store)
        second = prepare_v7_migration(self.root, self.store)
        self.assertEqual(first, second)
        self.assertEqual(first["style_presets_imported"], 1)
        self.assertEqual(first["migration_revision"], 2)
        self.assertEqual(self.store.list_presets()["items"][0]["id"], "preset_old")
        backup = self.root / first["backup"] / "style_presets.json"
        self.assertEqual(backup.read_bytes(), preset_path.read_bytes())
        studio_entry = next(item for item in first["files"] if item["path"] == "studio.sqlite3")
        self.assertEqual(studio_entry["backup_kind"], "sqlite_backup")
        connection = sqlite3.connect(self.store.path)
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM presets").fetchone()[0],
                1,
            )
        finally:
            connection.close()

    def test_old_v7_marker_repairs_presets_into_canonical_table(self) -> None:
        preset_path = self.root / "data" / "style_presets.json"
        preset_path.parent.mkdir(parents=True, exist_ok=True)
        preset_path.write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "id": "preset_repair",
                            "name": "Repair",
                            "settings": {"model_name": "model.safetensors", "loras": []},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        marker = self.root / "data" / "migrations" / "v7.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps(
                {
                    "schema_version": 7,
                    "backup": "data/backups/v6-pre-v7",
                    "files": [],
                    "style_presets_imported": 1,
                }
            ),
            encoding="utf-8",
        )
        repaired = prepare_v7_migration(self.root, self.store)
        self.assertEqual(repaired["migration_revision"], 2)
        self.assertEqual(repaired["style_presets_imported"], 1)
        self.assertEqual(self.store.list_presets()["count"], 1)


class V7HistoryIntentTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_intent_link_is_written_to_images(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            history = HistoryStore(Path(temporary) / "history.sqlite3")
            try:
                intent = {"workspace": "natural", "positive_prompt": "portrait"}
                await history.link_intent("job-v7", "intent-v7", intent, "natural")
                await history.create_batch(
                    "job-v7", 1, {}, source_workspace="natural", job_type="text_to_image"
                )
                image = await history.add_image(
                    batch_id="job-v7",
                    sequence=1,
                    prompt_id="prompt-v7",
                    image={"filename": "one.png"},
                    positive_prompt="portrait",
                    negative_prompt="",
                    sample_seed=225152443312944224,
                    prompt_seed=225152443312944225,
                    settings={},
                )
                self.assertEqual(image["intent_id"], "intent-v7")
                self.assertEqual(image["intent"], intent)
                self.assertEqual(image["source_workspace"], "natural")
                self.assertEqual(image["sample_seed_text"], "225152443312944224")
                self.assertEqual(image["prompt_seed_text"], "225152443312944225")
            finally:
                await history.close()


if __name__ == "__main__":
    unittest.main()
