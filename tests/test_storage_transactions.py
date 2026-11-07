from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from anima_webui.history import HistoryStore
from anima_webui.persistence import StorageReadOnlyError


class FailingConnection(sqlite3.Connection):
    fail_commit = False
    fail_rollback = False
    commit_entered = None
    release_commit = None

    def commit(self):
        if self.fail_commit:
            if self.commit_entered is not None:
                self.commit_entered.set()
                if not self.release_commit.wait(5):
                    raise TimeoutError("test did not release commit")
            self.fail_commit = False
            raise sqlite3.OperationalError("simulated commit I/O failure")
        return super().commit()

    def rollback(self):
        if self.fail_rollback:
            raise sqlite3.OperationalError("simulated rollback I/O failure")
        return super().rollback()


class HistoryTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "history.sqlite3"
        connect = sqlite3.connect
        with patch("anima_webui.history.sqlite3.connect", side_effect=lambda *args, **kwargs:
                   connect(*args, factory=FailingConnection, **kwargs)):
            self.store = HistoryStore(self.path)

    async def asyncTearDown(self):
        await self.store.close()
        self.temporary.cleanup()

    async def test_failed_batch_commit_is_not_visible_or_committed_by_next_request(self):
        await self.store._call(setattr, self.store.connection, "fail_commit", True)
        with self.assertRaisesRegex(sqlite3.OperationalError, "commit I/O"):
            await self.store.create_batch("failed", 1, {"model_profile": "anima"})
        with self.assertRaises(KeyError):
            await self.store.get_batch("failed")
        await self.store.create_batch("successful", 1, {"model_profile": "anima"})
        with closing(sqlite3.connect(self.path)) as connection:
            ids = [row[0] for row in connection.execute("SELECT id FROM batches")]
        self.assertEqual(ids, ["successful"])


    async def test_failed_rollback_blocks_later_writes(self):
        await self.store._call(setattr, self.store.connection, "fail_commit", True)
        await self.store._call(setattr, self.store.connection, "fail_rollback", True)
        with self.assertRaisesRegex(sqlite3.OperationalError, "commit I/O"):
            await self.store.create_batch("failed", 1, {})
        self.assertTrue(self.store.storage_status()["read_only"])
        with self.assertRaises(StorageReadOnlyError):
            await self.store.create_batch("later", 1, {})

    async def test_write_already_queued_before_rollback_failure_is_also_rejected(self):
        entered, release = threading.Event(), threading.Event()
        for name, value in (
            ("commit_entered", entered), ("release_commit", release),
            ("fail_commit", True), ("fail_rollback", True),
        ):
            await self.store._call(setattr, self.store.connection, name, value)
        first = asyncio.create_task(self.store.create_batch("failed", 1, {}))
        second = None
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            second = asyncio.create_task(self.store.create_batch("queued", 1, {}))
            await asyncio.sleep(0)
        finally:
            release.set()
            results = await asyncio.gather(first, *([second] if second else []), return_exceptions=True)
        self.assertIsInstance(results[0], sqlite3.OperationalError)
        self.assertIsInstance(results[1], StorageReadOnlyError)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM batches").fetchone()[0], 0)
