"""Durable artwork collections, batch atomicity, and legacy upgrades."""
from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from anima_webui.history import ArtworkCollectionConflict, HistoryStore
from anima_webui.persistence import StorageReadOnlyError
from anima_webui.workflow import WorkflowError
from test_storage_transactions import FailingConnection


async def add_image(store, filename="image.png", workbench="random", model="anima"):
    return await store.add_image(batch_id="test", sequence=1, prompt_id="prompt", image={"filename": filename},
                                 positive_prompt="hello", negative_prompt="", sample_seed=1, prompt_seed=2,
                                 settings={"workbench": workbench, "model_profile": model})


def legacy_management_tables(connection):
    connection.executescript("""
        DROP TABLE IF EXISTS artwork_memberships;
        DROP TABLE IF EXISTS artwork_folders;
        CREATE TABLE artwork_folders(id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE artwork_memberships(
            folder_id TEXT NOT NULL REFERENCES artwork_folders(id) ON DELETE CASCADE,
            image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
            PRIMARY KEY(folder_id,image_id));
        INSERT INTO artwork_folders VALUES('default','Saved favorites','before upgrade');
        CREATE TABLE managed_image_files (
            kind TEXT NOT NULL, asset_id TEXT NOT NULL, root TEXT NOT NULL,
            relative_path TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL,
            created_at REAL NOT NULL, last_used_at REAL NOT NULL, origin TEXT NOT NULL,
            PRIMARY KEY(kind,asset_id,root));
        INSERT INTO managed_image_files VALUES('input','old-asset','old-root','old.png','digest',10,1,1,'upload');
        CREATE TABLE image_draft_leases (
            session_id TEXT NOT NULL,kind TEXT NOT NULL,asset_id TEXT NOT NULL,expires_at REAL NOT NULL,
            PRIMARY KEY(session_id,kind,asset_id));
        INSERT INTO image_draft_leases VALUES('old-session','input','old-asset',1);
        CREATE TABLE image_cleanup_settings (
            id INTEGER PRIMARY KEY CHECK(id=1),enabled INTEGER NOT NULL,
            retention_days INTEGER NOT NULL,last_run TEXT);
        INSERT INTO image_cleanup_settings VALUES(1,1,7,'{"deleted_files":3}');
    """)


class LegacyArtworkDataTests(unittest.IsolatedAsyncioTestCase):
    async def test_reopen_restores_legacy_favorites_without_changing_cleanup_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            store = HistoryStore(path)
            await store.create_batch("test", 1, {"model_profile": "anima"})
            image = await add_image(store)
            await store._call(legacy_management_tables, store.connection)

            def seed():
                with store.connection:
                    store.connection.execute("INSERT INTO artwork_memberships VALUES('default',?)", (image["id"],))

            await store._call(seed)
            names = ("artwork_folders", "artwork_memberships", "managed_image_files",
                     "image_draft_leases", "image_cleanup_settings")
            before = await store._call(lambda: {name: [tuple(row) for row in store.connection.execute(f"SELECT * FROM {name}")]
                                                for name in names})
            await store.close()
            store = HistoryStore(path)
            try:
                records = await store.list_images()
                self.assertEqual(records["total"], 1)
                self.assertEqual(records["items"][0]["folder_ids"], ["default"])
                after = await store._call(lambda: {name: [tuple(row) for row in store.connection.execute(f"SELECT * FROM {name}")]
                                                   for name in names})
                for name in names[2:]:
                    self.assertEqual(after[name], before[name])
                self.assertEqual(after["artwork_folders"], [(*before["artwork_folders"][0], 0)])
                self.assertEqual(after["artwork_memberships"], [(*before["artwork_memberships"][0], 0)])
                backup = Path(f"{path}.pre-artwork-favorites-v2.bak")
                with closing(sqlite3.connect(backup)) as snapshot:
                    self.assertEqual(snapshot.execute("SELECT * FROM artwork_memberships").fetchall(), before["artwork_memberships"])
                self.assertEqual((await store.list_artwork_folders())["folders"][0]["is_system"], False)
                await store.rename_artwork_folder("default", "Renamed old favorites")
                await store.delete_artwork_folder("default")
            finally:
                await store.close()
            store = HistoryStore(path)
            try:
                self.assertEqual((await store.list_artwork_folders())["folders"], [])
                self.assertEqual((await store.list_images())["total"], 1)
                self.assertEqual(len(list(Path(directory).glob("*.pre-artwork-favorites-v2*.bak"))), 1)
            finally:
                await store.close()

    async def test_fresh_history_creates_empty_collections_without_cleanup_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            store = HistoryStore(Path(directory) / "history.sqlite3")
            try:
                names = await store._call(lambda: {row[0] for row in store.connection.execute("SELECT name FROM sqlite_master")})
                self.assertTrue({"artwork_folders", "artwork_memberships"}.issubset(names))
                self.assertTrue(names.isdisjoint({"managed_image_files", "image_draft_leases", "image_cleanup_settings"}))
                self.assertEqual(await store.list_artwork_folders(), {"folders": [], "total": 0})
            finally:
                await store.close()

    async def test_upgrade_backup_failure_preserves_original_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            store = HistoryStore(path)
            await store._call(legacy_management_tables, store.connection)
            await store.close()
            original = path.read_bytes()
            with mock.patch.object(Path, "open", side_effect=PermissionError("blocked")):
                store = HistoryStore(path)
            try:
                self.assertFalse(store.available)
                self.assertEqual(path.read_bytes(), original)
                with self.assertRaises(StorageReadOnlyError):
                    await store.create_artwork_folder("blocked")
            finally:
                await store.close()

    async def test_legacy_twenty_memberships_keep_order_and_new_collection_is_first(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            store = HistoryStore(path)
            await store.create_batch("test", 21, {"model_profile": "anima"})
            ids = [(await add_image(store, str(index)))["id"] for index in range(21)]
            await store._call(legacy_management_tables, store.connection)
            def seed():
                with store.connection:
                    store.connection.executemany("INSERT INTO artwork_memberships VALUES('default',?)", [(value,) for value in ids[1:]])
                    store.connection.execute("INSERT INTO artwork_folders VALUES('other','Other','later')")
            await store._call(seed)
            await store.close()
            store = HistoryStore(path)
            try:
                result = await store.list_images(folder_id="default")
                self.assertEqual([item["id"] for item in result["items"]], list(reversed(ids[1:])))
                self.assertEqual([item["id"] for item in (await store.list_artwork_folders())["folders"]], ["default", "other"])
                await store.update_artwork_memberships([ids[0]], ["default"])
                self.assertEqual((await store.list_images(folder_id="default"))["items"][0]["id"], ids[0])
            finally:
                await store.close()

    async def test_failed_collection_migration_rolls_back_schema_and_preserves_old_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            store = HistoryStore(path)
            await store.create_batch("test", 1, {"model_profile": "anima"})
            image_id = (await add_image(store))["id"]
            await store._call(legacy_management_tables, store.connection)
            def seed():
                with store.connection:
                    store.connection.execute("INSERT INTO artwork_memberships VALUES('default',?)", (image_id,))
            await store._call(seed)
            await store.close()
            backup = Path(f"{path}.pre-artwork-favorites-v2.bak")
            backup.write_bytes(b"previous snapshot")
            original = path.read_bytes()
            connect = sqlite3.connect
            def deny_index(destination, *args, **kwargs):
                connection = connect(destination, *args, **kwargs)
                if str(destination) == str(path):
                    connection.set_authorizer(lambda action, first, *_: sqlite3.SQLITE_DENY
                        if action == sqlite3.SQLITE_CREATE_INDEX and first == "idx_artwork_memberships_recent" else sqlite3.SQLITE_OK)
                return connection
            with mock.patch("anima_webui.history.sqlite3.connect", side_effect=deny_index):
                store = HistoryStore(path)
            try:
                self.assertFalse(store.available)
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(backup.read_bytes(), b"previous snapshot")
                with closing(connect(Path(f"{path}.pre-artwork-favorites-v2-2.bak"))) as snapshot:
                    self.assertEqual(snapshot.execute("SELECT * FROM artwork_memberships").fetchall(), [("default", image_id)])
            finally:
                await store.close()


class ArtworkCollectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "history.sqlite3"
        connect = sqlite3.connect
        with mock.patch("anima_webui.history.sqlite3.connect", side_effect=lambda *args, **kwargs: connect(*args, factory=FailingConnection, **kwargs)):
            self.store = HistoryStore(self.path)
        await self.store.create_batch("test", 600, {"model_profile": "anima"})

    async def asyncTearDown(self):
        await self.store.close()
        self.temp.cleanup()

    async def folder(self, name="Favorites"):
        return (await self.store.create_artwork_folder(name))["folder"]["id"]

    async def test_recent_membership_order_source_filter_and_page_contract(self):
        folder = await self.folder()
        ids = [(await add_image(self.store, str(index), "single" if index % 2 else "random"))["id"] for index in range(6)]
        await self.store.update_artwork_memberships(ids[2:], [folder])
        await self.store.update_artwork_memberships(ids[:2], [folder])
        first = await self.store.list_images(folder_id=folder, limit=3)
        self.assertEqual([item["id"] for item in first["items"]], [ids[1], ids[0], ids[5]])
        self.assertIsNone(first["next_before_id"])
        self.assertTrue(first["has_more"])
        second = await self.store.list_images(folder_id=folder, page=2, limit=3)
        self.assertEqual([item["id"] for item in second["items"]], [ids[4], ids[3], ids[2]])
        self.assertFalse(second["has_more"])
        single = await self.store.list_images(folder_id=folder, workbench="single")
        self.assertEqual([item["id"] for item in single["items"]], [ids[1], ids[5], ids[3]])
        self.assertEqual(single["total"], 3)
        with self.assertRaises(WorkflowError):
            await self.store.list_images(folder_id=folder, before_id=3)
        self.assertEqual((await self.store.list_images(limit=2))["next_before_id"], ids[-2])

    async def test_duplicate_add_preserves_time_and_remove_readd_moves_to_front(self):
        folder = await self.folder()
        one, two = [(await add_image(self.store, str(index)))["id"] for index in range(2)]
        await self.store.update_artwork_memberships([one], [folder])
        await self.store.update_artwork_memberships([two], [folder])
        old_time = await self.store._call(lambda: self.store.connection.execute("SELECT added_at FROM artwork_memberships WHERE image_id=?", (one,)).fetchone()[0])
        duplicate = await self.store.update_artwork_memberships([one, one], [folder, folder])
        self.assertEqual((duplicate["changed_count"], duplicate["changed_image_ids"]), (0, []))
        self.assertEqual(await self.store._call(lambda: self.store.connection.execute("SELECT added_at FROM artwork_memberships WHERE image_id=?", (one,)).fetchone()[0]), old_time)
        self.assertEqual((await self.store.list_images(folder_id=folder))["items"][0]["id"], two)
        await self.store.update_artwork_memberships([one], [folder], "remove")
        with mock.patch("anima_webui.history.time.time_ns", return_value=1):
            await self.store.update_artwork_memberships([one], [folder])
        self.assertEqual((await self.store.list_images(folder_id=folder))["items"][0]["id"], one)

    async def test_multi_folder_counts_delete_and_restart_preserve_other_memberships(self):
        first, second = await self.folder("First"), await self.folder("Second")
        one, two = [(await add_image(self.store, str(index)))["id"] for index in range(2)]
        added = await self.store.update_artwork_memberships([one, two], [first, second])
        self.assertEqual(added["changed_count"], 4)
        self.assertEqual(added["updated_count"], 2)
        self.assertEqual(added["total"], 2)
        self.assertEqual([item["count"] for item in added["folders"]], [2, 2])
        await self.store.update_artwork_memberships([one], [second], "remove")
        deleted = await self.store.delete_artwork_folder(first)
        self.assertEqual(deleted["affected_image_ids"], [one, two])
        self.assertEqual(deleted["items"], [{"id": one, "folder_ids": []}, {"id": two, "folder_ids": [second]}])
        self.assertEqual((await self.store.list_images())["total"], 2)
        await self.store.close()
        self.store = HistoryStore(self.path)
        self.assertEqual((await self.store.get_image(two))["folder_ids"], [second])
        await self.store.delete_image(two)
        self.assertEqual((await self.store.list_artwork_folders())["total"], 0)

    async def test_folder_names_reordering_and_empty_reorder(self):
        self.assertEqual(await self.store.reorder_artwork_folders([]), {"folders": [], "total": 0})
        first, second = await self.folder(" First "), await self.folder("Second")
        self.assertEqual((await self.store.list_artwork_folders())["folders"][0]["name"], "First")
        for name in ("first", "", "  ", "x" * 81, "line\nbreak", "control\x85", "surrogate\ud800", 2, None):
            with self.subTest(name=name), self.assertRaises(WorkflowError):
                await self.store.create_artwork_folder(name)
        await self.store.rename_artwork_folder(first, "Renamed")
        result = await self.store.reorder_artwork_folders([second, first])
        self.assertEqual([item["id"] for item in result["folders"]], [second, first])
        with self.assertRaises(WorkflowError):
            await self.store.reorder_artwork_folders([first, first])
        with self.assertRaises(ArtworkCollectionConflict) as failure:
            await self.store.reorder_artwork_folders([first, "missing"])
        self.assertEqual(failure.exception.payload["missing_folder_ids"], ["missing"])
        self.assertEqual([item["id"] for item in (await self.store.list_artwork_folders())["folders"]], [second, first])
        third = await self.folder("Third")
        self.assertEqual([item["id"] for item in (await self.store.list_artwork_folders())["folders"]], [second, first, third])

    async def test_batch_crosses_sqlite_chunk_boundary_and_rejects_missing_atomically(self):
        folder = await self.folder()
        ids = [(await add_image(self.store, str(index)))["id"] for index in range(501)]
        await self.store._call(self.store.connection.setlimit, sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 500)
        with self.assertRaises(ArtworkCollectionConflict) as failure:
            await self.store.update_artwork_memberships([*ids, 9999], [folder, "gone"])
        self.assertEqual(failure.exception.payload["missing_image_ids"], [9999])
        self.assertEqual(failure.exception.payload["missing_folder_ids"], ["gone"])
        self.assertEqual((await self.store.list_artwork_folders())["total"], 0)
        result = await self.store.update_artwork_memberships(ids, [folder])
        self.assertEqual(result["changed_count"], 501)
        self.assertEqual(len(result["items"]), 501)
        queried = await self.store.read_artwork_memberships([*ids, 9999])
        self.assertEqual(queried["missing_image_ids"], [9999])
        self.assertEqual(queried["items"], result["items"])
        self.assertEqual((await self.store.update_artwork_memberships(ids, [folder], "remove"))["changed_count"], 501)

    async def test_invalid_ids_actions_and_commit_failure_do_not_leak_changes(self):
        folder = await self.folder()
        image_id = (await add_image(self.store))["id"]
        for ids in ([], [True], [0], [-1], [2**63], [1.0], "1"):
            with self.subTest(ids=ids), self.assertRaises(WorkflowError):
                await self.store.update_artwork_memberships(ids, [folder])
        for action in ("clear", [], {}, True):
            with self.subTest(action=action), self.assertRaises(WorkflowError):
                await self.store.update_artwork_memberships([image_id], [folder], action)
        await self.store._call(setattr, self.store.connection, "fail_commit", True)
        with self.assertRaises(sqlite3.Error):
            await self.store.update_artwork_memberships([image_id], [folder])
        self.assertEqual((await self.store.get_image(image_id))["folder_ids"], [])
        await self.store.create_artwork_folder("After failure")
        self.assertEqual((await self.store.list_artwork_folders())["total"], 0)
        await self.store._call(setattr, self.store.connection, "fail_commit", True)
        await self.store._call(setattr, self.store.connection, "fail_rollback", True)
        with self.assertRaises(sqlite3.Error):
            await self.store.update_artwork_memberships([image_id], [folder])
        with self.assertRaises(StorageReadOnlyError):
            await self.store.update_artwork_memberships([image_id], [folder])

    async def test_remove_delete_reorder_failures_preserve_committed_state(self):
        first, second = await self.folder("First"), await self.folder("Second")
        image_id = (await add_image(self.store))["id"]
        await self.store.update_artwork_memberships([image_id], [first, second])
        before = await self.store.list_artwork_folders()
        for operation in (
            lambda: self.store.update_artwork_memberships([image_id], [first], "remove"),
            lambda: self.store.delete_artwork_folder(first),
            lambda: self.store.reorder_artwork_folders([second, first]),
            lambda: self.store.rename_artwork_folder(first, "Changed"),
        ):
            await self.store._call(setattr, self.store.connection, "fail_commit", True)
            with self.assertRaises(sqlite3.Error):
                await operation()
            self.assertEqual(await self.store.list_artwork_folders(), before)
            self.assertEqual((await self.store.get_image(image_id))["folder_ids"], sorted([first, second]))

    async def test_collection_page_reads_memberships_once_and_uses_recent_index(self):
        folder = await self.folder()
        ids = [(await add_image(self.store, str(index)))["id"] for index in range(8)]
        await self.store.update_artwork_memberships(ids, [folder])
        statements = []
        await self.store._call(self.store.connection.set_trace_callback, statements.append)
        try:
            await self.store.list_images(folder_id=folder)
        finally:
            await self.store._call(self.store.connection.set_trace_callback, None)
        reads = [sql for sql in statements if sql.startswith("SELECT image_id,folder_id FROM artwork_memberships")]
        self.assertEqual(len(reads), 1)
        query = next(sql for sql in statements if "batches.total AS batch_total" in sql)
        plan = await self.store._call(lambda: " ".join(row[3] for row in self.store.connection.execute("EXPLAIN QUERY PLAN " + query)))
        self.assertIn("idx_artwork_memberships_recent", plan)
        self.assertNotIn("TEMP B-TREE", plan)

    async def test_cancelled_http_style_wait_does_not_split_or_duplicate_transaction(self):
        folder = await self.folder()
        image_id = (await add_image(self.store))["id"]
        entered, release = threading.Event(), threading.Event()
        original_commit = FailingConnection.commit
        def blocking_commit(connection):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test did not release commit")
            return original_commit(connection)
        with mock.patch.object(FailingConnection, "commit", blocking_commit):
            operation = asyncio.create_task(self.store.update_artwork_memberships([image_id], [folder]))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                operation.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await operation
            finally:
                release.set()
            duplicate = await self.store.update_artwork_memberships([image_id], [folder])
        self.assertEqual(duplicate["changed_count"], 0)
        self.assertEqual(duplicate["items"], [{"id": image_id, "folder_ids": [folder]}])
