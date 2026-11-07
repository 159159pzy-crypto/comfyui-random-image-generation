from __future__ import annotations

import asyncio
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock

from anima_webui.artwork_deletion import ArtworkDeletionService, deletion_image_path
from anima_webui.history import HistoryStore
from anima_webui.image_location import ImageLocationError
from anima_webui.workflow import WorkflowError
from anima_webui.persistence import StorageReadOnlyError
from test_artwork_collections import add_image, legacy_management_tables
from test_storage_transactions import FailingConnection


class ArtworkDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        connect = sqlite3.connect
        with mock.patch("anima_webui.history.sqlite3.connect", side_effect=lambda *a, **kw: connect(*a, factory=FailingConnection, **kw)):
            self.store = HistoryStore(self.directory / "history.sqlite3")
        await self.store.create_batch("test", 20, {"model_profile": "anima"})
        await self.store._call(legacy_management_tables, self.store.connection)
        self.thumbnails = mock.Mock(discard=mock.AsyncMock())
        self.service = ArtworkDeletionService(self.store, self.thumbnails)
        self.patches = [mock.patch("anima_webui.artwork_deletion.image_location.IS_WINDOWS", True),
                        mock.patch("anima_webui.artwork_deletion.image_location.verify_local_comfy"),
                        mock.patch("anima_webui.artwork_deletion.image_location.comfy_image_directory", return_value=self.directory)]
        for patch in self.patches:
            patch.start()

    async def asyncTearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        await self.store.close()
        self.temp.cleanup()

    async def memberships(self, image_id):
        return await self.store._call(lambda: [row[0] for row in self.store.connection.execute(
            "SELECT folder_id FROM artwork_memberships WHERE image_id=?", (image_id,))])

    async def membership_count(self):
        return await self.store._call(lambda: self.store.connection.execute(
            "SELECT COUNT(*) FROM artwork_memberships").fetchone()[0])

    async def image(self, filename="image.png", create=True):
        if create:
            (self.directory / filename).write_bytes(b"original")
        image_id = (await add_image(self.store, filename))["id"]
        def favorite():
            with self.store.connection:
                self.store.connection.execute("INSERT INTO artwork_memberships VALUES('default',?)", (image_id,))
        await self.store._call(favorite)
        return image_id

    async def test_record_only_preserves_original_and_cascades_legacy_membership(self):
        image_id = await self.image()
        result = await self.service.delete(image_id)
        self.assertEqual(result["deleted_count"], 1)
        self.assertEqual(result["success_count"], 1)
        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(result["file_deleted_count"], 0)
        self.assertEqual(len(result["results"]), 1)
        self.assertTrue((self.directory / "image.png").is_file())
        self.assertEqual(await self.membership_count(), 0)
        repeated = await self.service.delete(image_id)
        self.assertEqual(repeated["results"][0]["code"], "already_missing")
        self.assertEqual((repeated["deleted_count"], repeated["success_count"]), (0, 1))

    async def test_invalid_id_or_mode_has_no_effect(self):
        image_id = await self.image()
        for invalid in ([image_id], [image_id, 999], [], {}, True, False, None, "1", 1.0, 0, -1, 2**63):
            with self.subTest(image_id=invalid), self.assertRaises(WorkflowError):
                await self.service.delete(invalid)
        for mode in ([], {}, True, "unknown"):
            with self.assertRaises(WorkflowError):
                await self.service.delete(image_id, mode)
        self.assertEqual(await self.memberships(image_id), ["default"])
        self.assertTrue((self.directory / "image.png").exists())
        self.thumbnails.discard.assert_not_awaited()

    async def test_shared_file_is_kept_until_other_record_is_removed(self):
        one, two = await self.image(), await self.image()
        rejected = await self.service.delete(one, "file")
        self.assertEqual(rejected["results"][0]["code"], "file_shared")
        self.assertEqual(rejected["results"][0]["shared_ids"], [two])
        self.assertEqual(rejected["failed_count"], 1)
        self.assertTrue((self.directory / "image.png").exists())
        await self.service.delete(two, "record")
        deleted = await self.service.delete(one, "file")
        self.assertEqual(deleted["deleted_count"], 1)
        self.assertEqual(deleted["file_deleted_count"], 1)
        self.assertFalse((self.directory / "image.png").exists())
        self.assertEqual(await self.membership_count(), 0)
        self.assertNotIn("file_key", deleted["results"][0])

    async def test_alias_of_shared_original_is_detected(self):
        one, two = await self.image("one.png"), await self.image("alias.png")
        with mock.patch.object(self.service, "_path", return_value=self.directory / "one.png"):
            result = await self.service.delete(one, "file")
        self.assertEqual(result["results"][0]["code"], "file_shared")
        self.assertEqual(result["results"][0]["shared_ids"], [two])
        self.assertTrue((self.directory / "one.png").exists())
        self.assertTrue((self.directory / "alias.png").exists())
        self.assertEqual((await self.store.list_images())["total"], 2)

    async def test_missing_file_completes_but_unavailable_root_preserves_record(self):
        image_id = await self.image(create=False)
        result = await self.service.delete(image_id, "file")
        self.assertTrue(result["results"][0]["file_missing"])
        self.assertTrue(result["results"][0]["record_deleted"])
        self.assertEqual(result["file_deleted_count"], 0)
        other = await self.image()
        with mock.patch("anima_webui.artwork_deletion.image_location.comfy_image_directory", return_value=self.directory / "unavailable"):
            result = await self.service.delete(other, "file")
        self.assertEqual(result["results"][0]["code"], "image_directory_unavailable")
        self.assertEqual(await self.memberships(other), ["default"])

    async def test_filesystem_failure_keeps_record_for_retry(self):
        one, two = await self.image("denied.png"), await self.image("untouched.png")
        with mock.patch.object(Path, "unlink", side_effect=PermissionError("locked")):
            result = await self.service.delete(one, "file")
        self.assertEqual((result["deleted_count"], result["failed_count"]), (0, 1))
        self.assertEqual(result["results"][0]["code"], "image_delete_failed")
        self.assertEqual(await self.memberships(one), ["default"])
        self.assertTrue((self.directory / "denied.png").exists())
        self.assertTrue((self.directory / "untouched.png").exists())
        retry = await self.service.delete(one, "file")
        self.assertTrue(retry["results"][0]["record_deleted"])
        self.assertEqual((await self.store.get_image(two))["id"], two)

    async def test_commit_failure_reports_unlinked_file_and_record_retry_succeeds(self):
        one, two = await self.image("first.png"), await self.image("untouched.png")
        await self.store._call(setattr, self.store.connection, "fail_commit", True)
        result = await self.service.delete(one, "file")
        failed = result["results"][0]
        self.assertEqual(failed["code"], "database_error")
        self.assertTrue(failed["file_deleted"])
        self.assertFalse(failed["record_deleted"])
        self.assertEqual(result["file_deleted_count"], 1)
        self.assertEqual(await self.memberships(one), ["default"])
        self.assertEqual((await self.store.get_image(two))["id"], two)
        self.assertTrue((self.directory / "untouched.png").exists())
        retry = await self.service.delete(one, "record")
        self.assertTrue(retry["results"][0]["record_deleted"])
        self.assertFalse(retry["results"][0]["file_deleted"])

    async def test_file_retry_after_failed_commit_accepts_missing_original(self):
        image_id = await self.image()
        await self.store._call(setattr, self.store.connection, "fail_commit", True)
        await self.service.delete(image_id, "file")
        retry = await self.service.delete(image_id, "file")
        self.assertTrue(retry["results"][0]["record_deleted"])
        self.assertTrue(retry["results"][0]["file_missing"])

    async def test_record_commit_failure_rolls_back_only_requested_record(self):
        one, two = await self.image("one.png"), await self.image("two.png")
        await self.store._call(setattr, self.store.connection, "fail_commit", True)
        result = await self.service.delete(one)
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual((await self.store.list_images())["total"], 2)
        self.assertEqual(await self.membership_count(), 2)
        self.assertTrue((self.directory / "one.png").exists())
        self.assertEqual((await self.store.get_image(two))["id"], two)

    async def test_record_changed_before_transaction_keeps_both_files(self):
        image_id = await self.image("before.png")
        (self.directory / "after.png").write_bytes(b"other original")
        original_path = self.service._path
        calls = 0
        def change_record(record, stats):
            nonlocal calls
            calls += 1
            path = original_path(record, stats)
            if calls == 1:
                with self.store.connection:
                    self.store.connection.execute("UPDATE images SET filename='after.png' WHERE id=?", (image_id,))
            return path
        with mock.patch.object(self.service, "_path", side_effect=change_record):
            result = await self.service.delete(image_id, "file")
        self.assertEqual(result["results"][0]["code"], "record_changed")
        self.assertEqual((await self.store.get_image(image_id))["filename"], "after.png")
        self.assertTrue((self.directory / "before.png").exists())
        self.assertTrue((self.directory / "after.png").exists())

    async def test_final_path_change_rolls_back_record_without_unlinking(self):
        image_id = await self.image("before.png")
        (self.directory / "after.png").write_bytes(b"other original")
        before, after = self.directory / "before.png", self.directory / "after.png"
        with mock.patch.object(self.service, "_path", side_effect=[before, before, after]):
            result = await self.service.delete(image_id, "file")
        self.assertEqual(result["results"][0]["code"], "image_location_changed")
        self.assertEqual(await self.memberships(image_id), ["default"])
        self.assertTrue(before.exists())
        self.assertTrue(after.exists())

    async def test_read_only_rechecked_after_waiting_for_delete_lock(self):
        image_id = await self.image()
        async with self.service._lock:
            operation = asyncio.create_task(self.service.delete(image_id))
            await asyncio.sleep(0)
            self.store._protect_storage("test protection")
        with self.assertRaises(StorageReadOnlyError):
            await operation
        self.assertTrue((self.directory / "image.png").exists())
        self.assertEqual(await self.memberships(image_id), ["default"])
        self.thumbnails.discard.assert_not_awaited()

    async def test_remote_verification_failure_and_unsafe_record_never_delete(self):
        image_id = await self.image()
        with mock.patch("anima_webui.artwork_deletion.image_location.verify_local_comfy", side_effect=ImageLocationError("remote")):
            result = await self.service.delete(image_id, "file")
        self.assertEqual(result["failed_count"], 1)
        unsafe = (await add_image(self.store, "../outside.png"))["id"]
        result = await self.service.delete(unsafe, "file")
        self.assertEqual(result["results"][0]["code"], "invalid_image_location")
        self.assertTrue((self.directory / "image.png").is_file())

    async def test_repeated_cancellation_finishes_database_and_thumbnail_cleanup(self):
        image_id = await self.image()
        entered, release = threading.Event(), threading.Event()
        original = self.service._delete
        def paused(*args):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("release")
            return original(*args)
        with mock.patch.object(self.service, "_delete", side_effect=paused):
            task = asyncio.create_task(self.service.delete(image_id))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual((await self.store.list_images())["total"], 0)
        self.thumbnails.discard.assert_awaited_once_with(image_id)


class DeletionPathTests(unittest.TestCase):
    def test_path_escape_missing_root_and_non_file_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "directory.png").mkdir()
            for directory, record in ((root, {"filename": "../outside.png"}), (root / "missing", {"filename": "one.png"}), (root, {"filename": "directory.png"})):
                with self.assertRaises(ImageLocationError):
                    deletion_image_path(directory, record)
            outside = root.parent / "outside.png"
            original = Path.resolve
            with mock.patch.object(Path, "resolve", autospec=True, side_effect=lambda path, **kwargs: outside if path.name == "escaped.png" else original(path, **kwargs)):
                with self.assertRaises(ImageLocationError) as caught:
                    deletion_image_path(root, {"filename": "escaped.png"})
            self.assertEqual(caught.exception.code, "invalid_image_location")
