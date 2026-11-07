import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from anima_webui.history import HistoryStore


class HistoryWorkbenchIndexTests(unittest.IsolatedAsyncioTestCase):
    async def make_history(self, path):
        store = HistoryStore(path)
        await store.create_batch("index-test", 3, {"model_profile": "anima"})
        for workbench in (None, "single", "random"):
            settings = {"model_profile": "anima"}
            if workbench:
                settings["workbench"] = workbench
            await store.add_image(
                batch_id="index-test", sequence=1, prompt_id="saved",
                image={"filename": "saved.png"}, positive_prompt="text",
                negative_prompt="", sample_seed=1, prompt_seed=1, settings=settings,
            )
        await store.update_batch("index-test", status="completed")
        await store.close()

    async def test_upgrade_indexes_filter_and_cursor_without_changing_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            await self.make_history(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("DROP INDEX IF EXISTS idx_images_workbench_id")
                original = connection.execute("SELECT * FROM images ORDER BY id").fetchall()
            for _ in range(2):
                store = HistoryStore(path)
                try:
                    self.assertTrue(store.available)
                    result = await store.list_images(workbench="random", before_id=3)
                    self.assertEqual([item["id"] for item in result["items"]], [1])
                    self.assertEqual(result["total"], 2)
                    self.assertFalse(result["has_more"])
                    def inspect():
                        query = "SELECT id FROM images WHERE COALESCE(json_extract(settings_json, '$.workbench'), 'random') = ? AND id < ? ORDER BY id DESC LIMIT ?"
                        plan = store.connection.execute("EXPLAIN QUERY PLAN " + query, ("random", 3, 25)).fetchall()
                        return [tuple(row) for row in store.connection.execute("SELECT * FROM images ORDER BY id")], " ".join(row[3] for row in plan)
                    rows, plan = await store._call(inspect)
                    self.assertEqual(rows, original)
                    # Newer SQLite recognizes that this projection is covered
                    # by the same index; both plans must still perform a search.
                    self.assertRegex(plan, r"\bSEARCH images USING (?:COVERING )?INDEX idx_images_workbench_id\b")
                    self.assertNotIn("TEMP B-TREE", plan)
                finally:
                    await store.close()

    async def test_index_creation_failure_preserves_database_and_blocks_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            await self.make_history(path)
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("DROP INDEX IF EXISTS idx_images_workbench_id")
            original = path.read_bytes()
            connect = sqlite3.connect
            def deny_index(destination, *args, **kwargs):
                connection = connect(destination, *args, **kwargs)
                if str(destination) != ":memory:":
                    connection.set_authorizer(lambda action, first, *_: sqlite3.SQLITE_DENY
                        if action == sqlite3.SQLITE_CREATE_INDEX and first == "idx_images_workbench_id"
                        else sqlite3.SQLITE_OK)
                return connection
            with mock.patch("anima_webui.history.sqlite3.connect", side_effect=deny_index):
                store = HistoryStore(path)
            try:
                self.assertFalse(store.available)
                self.assertTrue(store.storage_status()["read_only"])
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(list(Path(directory).glob("*.corrupt*")), [])
            finally:
                await store.close()
