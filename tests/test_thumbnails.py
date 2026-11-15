"""History thumbnail quality, bounded work, cache lifecycle and HTTP contracts."""
from __future__ import annotations

import asyncio
import io
import os
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from aiohttp.test_utils import TestClient, TestServer, make_mocked_request
from PIL import Image, ImageCms

from anima_webui import thumbnails as thumbnail_module
from anima_webui.comfy import ComfyError
from anima_webui.server import ARTWORK_DELETION_KEY, HISTORY_KEY, THUMBNAILS_KEY, create_app
from anima_webui.thumbnails import CACHE_MAX_BYTES, TEMP_TTL_SECONDS, ThumbnailStore, make_thumbnail
from anima_webui.workflow import DEFAULT_SETTINGS
from test_server import APP_DIR, FakeComfy, _write_fake_tools


def source_image(size=(1200, 800), *, noise=False, mode="RGB", color="tomato", **save_options):
    image = Image.effect_noise(size, 80).convert(mode) if noise else Image.new(mode, size, color)
    with image:
        stream = io.BytesIO()
        image.save(stream, format="PNG", **save_options)
        return stream.getvalue()


def record(image_id=1):
    return {"id": image_id, "filename": f"test-{image_id}.png", "subfolder": "", "file_type": "output",
            "batch_id": "batch-1", "prompt_id": f"prompt-{image_id}", "created_at": "2026-10-22T12:00:00+08:00"}


class ImageClient:
    base_url = "http://127.0.0.1:8188"

    def __init__(self, body=None):
        self.body = body or source_image()
        self.calls = 0

    async def image_bytes(self, _record, *, max_bytes=None):
        self.calls += 1
        return self.body, "image/png"


class ThumbnailEncodingTests(unittest.TestCase):
    def test_large_source_is_smaller_and_preserves_aspect_ratio(self):
        original = source_image((1200, 800), noise=True)
        output = make_thumbnail(original)
        with Image.open(io.BytesIO(output)) as image:
            self.assertEqual(image.format, "WEBP")
            self.assertEqual(image.size, (512, 341))
        self.assertLess(len(output), len(original) // 5)

    def test_small_transparent_image_is_not_upscaled_and_keeps_alpha_and_icc(self):
        icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        original = source_image((24, 12), mode="RGBA", color=(120, 30, 80, 90), icc_profile=icc)
        with Image.open(io.BytesIO(make_thumbnail(original))) as image:
            self.assertEqual(image.size, (24, 12))
            self.assertEqual(image.mode, "RGBA")
            self.assertEqual(image.getchannel("A").getextrema(), (90, 90))
            self.assertEqual(image.info["icc_profile"], icc)

    def test_exif_orientation_is_applied_and_not_reapplied(self):
        exif = Image.Exif()
        exif[274] = 6
        original = source_image((24, 12), exif=exif)
        with Image.open(io.BytesIO(make_thumbnail(original))) as image:
            self.assertEqual(image.size, (12, 24))
            self.assertNotIn(274, image.getexif())

    def test_invalid_sources_are_rejected_without_falling_back_to_original(self):
        for body in (b"", b"broken PNG"):
            with self.subTest(body=body), self.assertRaises(ComfyError):
                make_thumbnail(body)
        with mock.patch("anima_webui.thumbnails.MAX_SOURCE_PIXELS", 100):
            with self.assertRaisesRegex(ComfyError, "像素"):
                make_thumbnail(source_image((11, 11)))
        with mock.patch("anima_webui.thumbnails.MAX_SOURCE_BYTES", 8):
            with self.assertRaisesRegex(ComfyError, "大小"):
                make_thumbnail(b"123456789")


class ThumbnailStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / "thumbnails"
        self.client = ImageClient()
        self.store = ThumbnailStore(self.directory, self.client)
        self.extra_stores = []

    async def asyncTearDown(self):
        await self.store.close()
        for store in self.extra_stores:
            await store.close()
        self.temp.cleanup()

    async def test_simultaneous_requests_share_one_fetch_and_cache_survives_restart(self):
        self.assertEqual(CACHE_MAX_BYTES, 1024 ** 3)
        results = await asyncio.gather(*(self.store.get(record()) for _ in range(8)))
        self.assertEqual(self.client.calls, 1)
        self.assertTrue(all(result.body == results[0].body for result in results))
        await self.store.close()
        restarted_client = ImageClient()
        restarted = ThumbnailStore(self.directory, restarted_client)
        self.extra_stores.append(restarted)
        result = await restarted.get(record())
        self.assertEqual(result.etag, results[0].etag)
        self.assertEqual(result.modified, results[0].modified)
        self.assertEqual(restarted_client.calls, 0)

    async def test_backend_and_record_identity_partition_cache(self):
        await self.store.get(record())
        other_client = ImageClient()
        other_client.base_url = "http://127.0.0.1:8288"
        other = ThumbnailStore(self.directory, other_client)
        self.extra_stores.append(other)
        await other.get(record())
        self.assertEqual(other_client.calls, 1)
        await self.store.get({**record(), "prompt_id": "restored-record"})
        self.assertEqual(self.client.calls, 2)
        self.assertEqual(len(list(self.directory.glob("*.webp"))), 3)

    async def test_corruption_rebuilds_but_old_valid_cache_does_not_expire(self):
        initial = await self.store.get(record())
        path = self.directory / self.store._key(record())
        path.write_bytes(b"broken cached file")
        rebuilt = await self.store.get(record())
        self.assertEqual(rebuilt.body, initial.body)
        self.assertEqual(self.client.calls, 2)
        stale = time.time() - 90 * 24 * 60 * 60
        os.utime(path, (stale, stale))
        await self.store.get(record())
        self.assertEqual(self.client.calls, 2)

    async def test_read_only_cache_returns_this_thumbnail_without_leftover_temporary_file(self):
        with mock.patch("anima_webui.thumbnails.os.replace", side_effect=PermissionError("read-only")):
            result = await self.store.get(record())
        with Image.open(io.BytesIO(result.body)) as image:
            self.assertEqual(image.size, (512, 341))
        self.assertEqual(list(self.directory.iterdir()), [])
        await self.store.get(record())
        self.assertEqual(self.client.calls, 2)

    async def test_disk_budget_evicts_oldest_but_keeps_generated_result(self):
        self.client.body = source_image((300, 200), noise=True)
        result = await self.store.get(record(1))
        self.store.max_bytes = len(result.body) * 2 + 1
        await self.store.get(record(2))
        await self.store.get(record(3))
        files = list(self.directory.glob("*.webp"))
        self.assertLessEqual(sum(path.stat().st_size for path in files), self.store.max_bytes)
        self.assertFalse((self.directory / self.store._key(record(1))).exists())
        self.assertTrue((self.directory / self.store._key(record(3))).exists())
        self.store.max_bytes = 1
        result = await self.store.get(record(4))
        self.assertTrue(result.body)
        self.assertEqual(list(self.directory.glob("*.webp")), [])

    async def test_recently_read_entry_survives_capacity_eviction_after_restart(self):
        first = await self.store.get(record(1))
        await self.store.get(record(2))
        first_path = self.directory / self.store._key(record(1))
        modified = first_path.stat().st_mtime_ns
        await self.store.get(record(1))
        self.assertEqual(first_path.stat().st_mtime_ns, modified)
        await self.store.close()
        restarted = ThumbnailStore(self.directory, self.client, max_bytes=len(first.body) * 2 + 1)
        self.extra_stores.append(restarted)
        await restarted.start()
        await restarted.get(record(3))
        self.assertTrue(first_path.exists())
        self.assertFalse((self.directory / restarted._key(record(2))).exists())
        self.assertTrue((self.directory / restarted._key(record(3))).exists())

    async def test_cached_request_bypasses_four_blocked_cold_generations(self):
        warm = await self.store.get(record(99))
        release, all_started = threading.Event(), threading.Event()
        lock = threading.Lock()
        started = 0

        def blocked_convert(body):
            nonlocal started
            with lock:
                started += 1
                if started == 4:
                    all_started.set()
            if not release.wait(5):
                raise TimeoutError("Test conversion was not released")
            return make_thumbnail(body)

        with mock.patch("anima_webui.thumbnails.make_thumbnail", side_effect=blocked_convert):
            cold = [asyncio.create_task(self.store.get(record(index))) for index in range(1, 5)]
            try:
                self.assertTrue(await asyncio.to_thread(all_started.wait, 3))
                result = await asyncio.wait_for(self.store.get(record(99)), 1)
                self.assertEqual(result.body, warm.body)
                self.assertFalse(any(task.done() for task in cold))
            finally:
                release.set()
                await asyncio.gather(*cold, return_exceptions=True)

    async def test_cached_reads_remain_bounded_to_eight(self):
        # Keep the test independent of the machine's default executor size.
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=12))
        for index in range(1, 11):
            await self.store.get(record(index))
        release, eight_started = threading.Event(), threading.Event()
        counter_lock = threading.Lock()
        original_read = self.store._read
        active = peak = started = 0

        def blocked_read(key):
            nonlocal active, peak, started
            with counter_lock:
                active += 1
                started += 1
                peak = max(peak, active)
                if started == 8:
                    eight_started.set()
            try:
                if not release.wait(5):
                    raise TimeoutError("Test cache read was not released")
                return original_read(key)
            finally:
                with counter_lock:
                    active -= 1

        with mock.patch.object(self.store, "_read", side_effect=blocked_read):
            requests = [asyncio.create_task(self.store.get(record(index))) for index in range(1, 11)]
            try:
                self.assertTrue(await asyncio.to_thread(eight_started.wait, 3))
                self.assertEqual(started, 8)
                self.assertFalse(any(task.done() for task in requests))
                release.set()
                results = await asyncio.gather(*requests)
                self.assertTrue(all(result.body for result in results))
                self.assertEqual(started, 10)
                self.assertEqual(peak, 8)
                self.assertEqual(self.client.calls, 10, "warm reads must not download originals again")
            finally:
                release.set()
                await asyncio.gather(*requests, return_exceptions=True)

    async def test_slow_first_validation_does_not_hold_global_disk_lock(self):
        await self.store.get(record(1))
        warm = await self.store.get(record(2))
        await self.store.close()
        restarted = ThumbnailStore(self.directory, self.client)
        self.extra_stores.append(restarted)
        await restarted.get(record(2))
        release, started = threading.Event(), threading.Event()
        validate = thumbnail_module._validate_thumbnail

        def blocked_validate(body):
            started.set()
            if not release.wait(5):
                raise TimeoutError("Test validation was not released")
            return validate(body)

        with mock.patch("anima_webui.thumbnails._validate_thumbnail", side_effect=blocked_validate):
            validating = asyncio.create_task(restarted.get(record(1)))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 3))
                result = await asyncio.wait_for(restarted.get(record(2)), 1)
                self.assertEqual(result.body, warm.body)
                self.assertFalse(validating.done())
            finally:
                release.set()
                await asyncio.gather(validating, return_exceptions=True)

    async def test_repeat_hits_and_writes_do_not_rescan_or_redecode_unchanged_files(self):
        await self.store.get(record(1))
        await self.store.close()
        restarted = ThumbnailStore(self.directory, self.client)
        self.extra_stores.append(restarted)
        await restarted.start()
        with mock.patch.object(restarted, "_scan_directory", side_effect=AssertionError("Unexpected rescan")), \
                mock.patch("anima_webui.thumbnails._validate_thumbnail", wraps=thumbnail_module._validate_thumbnail) as validate:
            first = await restarted.get(record(1))
            await restarted.get(record(1))
            await restarted.get(record(2))
            await restarted.get(record(1))
            self.assertEqual(validate.call_count, 1)
            path = self.directory / restarted._key(record(1))
            info = path.stat()
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))
            changed = await restarted.get(record(1))
            self.assertEqual(changed.etag, first.etag)
            self.assertEqual(validate.call_count, 2)

    async def test_startup_counts_recent_temporary_files_and_removes_only_stale_owned_files(self):
        self.directory.mkdir()
        encoded = make_thumbnail(self.client.body)
        old = self.directory / self.store._key(record(1))
        old.write_bytes(encoded)
        recent = self.directory / ".thumbnail-recent.tmp"
        recent.write_bytes(encoded)
        stale = self.directory / ".thumbnail-stale.tmp"
        stale.write_bytes(b"unfinished")
        expired = time.time() - TEMP_TTL_SECONDS - 1
        os.utime(stale, (expired, expired))
        unrelated = self.directory / "keep.txt"
        unrelated.write_bytes(b"not a thumbnail" * 1000)
        self.store.max_bytes = len(encoded) * 2
        await self.store.start()
        self.assertFalse(stale.exists())
        self.assertTrue(recent.exists())
        self.assertEqual(self.store._total_bytes, len(encoded) * 2)
        await self.store.get(record(2))
        self.assertFalse(old.exists())
        self.assertTrue(unrelated.exists())
        self.assertLessEqual(self.store._total_bytes, self.store.max_bytes)

    async def test_concurrent_temporary_reservations_keep_cache_under_budget(self):
        encoded = make_thumbnail(self.client.body)
        self.store.max_bytes = len(encoded)
        release, writing = threading.Event(), threading.Event()
        fsync = os.fsync

        def blocked_fsync(descriptor):
            writing.set()
            if not release.wait(5):
                raise TimeoutError("Test write was not released")
            return fsync(descriptor)

        with mock.patch("anima_webui.thumbnails.os.fsync", side_effect=blocked_fsync):
            first = asyncio.create_task(self.store.get(record(1)))
            try:
                self.assertTrue(await asyncio.to_thread(writing.wait, 3))
                second = await asyncio.wait_for(self.store.get(record(2)), 1)
                self.assertTrue(second.body)
                self.assertFalse(first.done())
                self.assertLessEqual(sum(path.stat().st_size for path in self.directory.iterdir()), self.store.max_bytes)
                self.assertEqual(self.store._reserved_bytes, len(encoded))
            finally:
                release.set()
                await asyncio.gather(first, return_exceptions=True)
        self.assertEqual(self.store._reserved_bytes, 0)
        self.assertEqual(self.store._total_bytes, len(encoded))
        self.assertEqual(len(list(self.directory.glob("*.webp"))), 1)

    async def test_failed_write_releases_reservation_and_retry_can_persist(self):
        with mock.patch("anima_webui.thumbnails.os.fsync", side_effect=OSError("disk full")):
            result = await self.store.get(record())
        self.assertTrue(result.body)
        self.assertEqual(self.store._reserved_bytes, 0)
        self.assertEqual(self.store._total_bytes, 0)
        self.assertEqual(list(self.directory.iterdir()), [])
        await self.store.get(record())
        self.assertTrue((self.directory / self.store._key(record())).exists())

    async def test_maintenance_does_not_restore_deleted_entries_or_lose_new_writes(self):
        first = await self.store.get(record(1))
        release, scanned = threading.Event(), threading.Event()
        scan = self.store._scan_directory

        def delayed_scan():
            snapshot = scan()
            scanned.set()
            if not release.wait(5):
                raise TimeoutError("Test scan was not released")
            return snapshot

        with mock.patch.object(self.store, "_scan_directory", side_effect=delayed_scan):
            maintenance = asyncio.create_task(asyncio.to_thread(self.store._maintain))
            try:
                self.assertTrue(await asyncio.to_thread(scanned.wait, 3))
                await self.store.discard(1)
                await asyncio.wait_for(self.store.get(record(2)), 1)
            finally:
                release.set()
                await maintenance
        self.assertNotIn(self.store._key(record(1)), self.store._entries)
        self.assertIn(self.store._key(record(2)), self.store._entries)
        self.assertEqual(self.store._total_bytes, len(first.body))

    async def test_maintenance_retries_a_temporarily_locked_deleted_thumbnail(self):
        await self.store.get(record(1))
        await self.store.get(record(2))
        key = self.store._key(record(1))
        path = self.directory / key
        unlink = Path.unlink

        def locked_unlink(target, *args, **kwargs):
            if target == path:
                raise PermissionError("temporary antivirus lock")
            return unlink(target, *args, **kwargs)

        with mock.patch.object(Path, "unlink", locked_unlink):
            await self.store.discard(1)
            await asyncio.to_thread(self.store._maintain)
            self.assertTrue(path.exists())
            self.assertIn(key, self.store._failed_discards)
        await asyncio.to_thread(self.store._maintain)
        self.assertFalse(path.exists())
        self.assertNotIn(key, self.store._entries)
        self.assertFalse(self.store._failed_discards)
        self.assertTrue((self.directory / self.store._key(record(2))).exists())

    async def test_fetch_decode_limit_remains_four_when_a_caller_disconnects(self):
        release = threading.Event()
        four_started = threading.Event()
        counter_lock = threading.Lock()
        started = 0

        def blocked_convert(body):
            nonlocal started
            with counter_lock:
                started += 1
                if started == 4:
                    four_started.set()
            if not release.wait(5):
                raise TimeoutError("Test conversion was not released")
            return make_thumbnail(body)

        with mock.patch("anima_webui.thumbnails.make_thumbnail", side_effect=blocked_convert):
            requests = [asyncio.create_task(self.store.get(record(index))) for index in range(1, 6)]
            try:
                self.assertTrue(await asyncio.to_thread(four_started.wait, 3))
                self.assertEqual(self.client.calls, 4, "downloads must wait for a decoding slot too")
                requests[0].cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await requests[0]
                await asyncio.sleep(0)
                self.assertEqual(self.client.calls, 4, "a disconnected caller must not free a running slot")
                release.set()
                await asyncio.gather(*requests[1:])
                self.assertEqual(self.client.calls, 5)
            finally:
                release.set()
                await asyncio.gather(*requests, return_exceptions=True)

    async def test_close_drains_workers_even_if_first_close_caller_is_cancelled(self):
        release = threading.Event()
        started = threading.Event()

        def blocked_convert(body):
            started.set()
            if not release.wait(5):
                raise TimeoutError("Test conversion was not released")
            return make_thumbnail(body)

        with mock.patch("anima_webui.thumbnails.make_thumbnail", side_effect=blocked_convert):
            request = asyncio.create_task(self.store.get(record()))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 3))
                request.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await request
                first_close = asyncio.create_task(self.store.close())
                await asyncio.sleep(0)
                first_close.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await first_close
                second_close = asyncio.create_task(self.store.close())
                await asyncio.sleep(0)
                self.assertFalse(second_close.done())
                release.set()
                await second_close
                self.assertFalse(self.store._pending)
                with self.assertRaises(ComfyError):
                    await self.store.get(record())
            finally:
                release.set()

    async def test_delete_during_conversion_cannot_publish_an_orphan(self):
        release = threading.Event()
        started = threading.Event()

        def blocked_convert(body):
            started.set()
            if not release.wait(5):
                raise TimeoutError("Test conversion was not released")
            return make_thumbnail(body)

        with mock.patch("anima_webui.thumbnails.make_thumbnail", side_effect=blocked_convert):
            request = asyncio.create_task(self.store.get(record()))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 3))
                await self.store.discard(1)
                release.set()
                with self.assertRaises(KeyError):
                    await request
                self.assertEqual(list(self.directory.glob("*.webp")), [])
            finally:
                release.set()
                await asyncio.gather(request, return_exceptions=True)

    async def test_cancelled_late_job_checks_deleted_record_without_http_caller(self):
        release = threading.Event()
        started = threading.Event()
        self.store.history = mock.Mock(get_image=mock.AsyncMock(side_effect=KeyError(1)))

        def blocked_convert(body):
            started.set()
            if not release.wait(5):
                raise TimeoutError("Test conversion was not released")
            return make_thumbnail(body)

        with mock.patch("anima_webui.thumbnails.make_thumbnail", side_effect=blocked_convert):
            request = asyncio.create_task(self.store.get(record()))
            try:
                self.assertTrue(await asyncio.to_thread(started.wait, 3))
                request.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await request
                release.set()
                await self.store.close()
                self.store.history.get_image.assert_awaited_once_with(1)
                self.assertEqual(list(self.directory.glob("*.webp")), [])
            finally:
                release.set()

    async def test_failed_fetch_does_not_poison_shared_job_or_block_retry(self):
        with mock.patch.object(self.client, "image_bytes", side_effect=ComfyError("offline")):
            failures = await asyncio.gather(*(self.store.get(record()) for _ in range(3)), return_exceptions=True)
        self.assertTrue(all(isinstance(error, ComfyError) for error in failures))
        self.assertFalse(self.store._pending)
        result = await self.store.get(record())
        self.assertTrue(result.body)
        self.assertEqual(self.client.calls, 1)


class ThumbnailApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.source = source_image((1200, 800), noise=True)
        self.comfy = FakeComfy()
        self.comfy.image_bytes = mock.AsyncMock(return_value=(self.source, "image/png"))
        app = create_app(app_dir=APP_DIR, data_dir=Path(self.temp.name) / "data", comfy=self.comfy,
                         anima_tools_dir=_write_fake_tools(Path(self.temp.name)))
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        history = app[HISTORY_KEY]
        await history.create_batch("batch-1", 1, DEFAULT_SETTINGS)
        self.record = await history.add_image(
            batch_id="batch-1", sequence=1, prompt_id="prompt-1", image={"filename": "test.png"},
            positive_prompt="test", negative_prompt="", sample_seed=1, prompt_seed=2, settings=DEFAULT_SETTINGS,
        )
        self.endpoint = f"/api/images/{self.record['id']}/thumbnail"
        self.store = app[THUMBNAILS_KEY]

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def test_original_bytes_are_unchanged_and_thumbnail_is_cacheable_webp(self):
        original = await self.client.get(f"/api/images/{self.record['id']}")
        self.assertEqual(await original.read(), self.source)
        self.assertEqual(original.headers["Cache-Control"], "no-store")
        thumbnail = await self.client.get(self.endpoint)
        self.assertEqual(thumbnail.status, 200)
        self.assertEqual(thumbnail.content_type, "image/webp")
        self.assertEqual(thumbnail.headers["Cache-Control"], "private, max-age=3600")
        self.assertEqual(thumbnail.headers["X-Content-Type-Options"], "nosniff")
        self.assertLess(len(await thumbnail.read()), len(self.source) // 5)
        self.assertTrue(thumbnail.headers["ETag"])
        self.assertTrue(thumbnail.headers["Last-Modified"])

    async def test_conditional_requests_and_etag_precedence(self):
        first = await self.client.get(self.endpoint)
        etag, modified = first.headers["ETag"], first.headers["Last-Modified"]
        for headers in ({"If-None-Match": etag}, {"If-None-Match": "W/" + etag},
                        {"If-None-Match": "*"}, {"If-Modified-Since": modified}):
            with self.subTest(headers=headers):
                response = await self.client.get(self.endpoint, headers=headers)
                self.assertEqual(response.status, 304)
                self.assertEqual(await response.read(), b"")
                self.assertEqual(response.headers["ETag"], etag)
        changed = await self.client.get(self.endpoint, headers={"If-None-Match": '"different"', "If-Modified-Since": modified})
        self.assertEqual(changed.status, 200)
        self.assertEqual(self.comfy.image_bytes.await_count, 1)

    async def test_invalid_ids_deleted_records_and_corrupt_source(self):
        for path, expected in (("/api/images/999/thumbnail", 404), ("/api/images/no/thumbnail", 404),
                               ("/api/images/9223372036854775808/thumbnail", 400)):
            with self.subTest(path=path):
                self.assertEqual((await self.client.get(path)).status, expected)
        self.comfy.image_bytes.return_value = (b"bad source", "image/png")
        bad = await self.client.get(self.endpoint)
        self.assertEqual(bad.status, 503)
        self.assertEqual(bad.content_type, "application/json")
        self.comfy.image_bytes.return_value = (self.source, "image/png")
        self.assertEqual((await self.client.get(self.endpoint)).status, 200)
        self.assertTrue(list(self.store.path.glob("*.webp")))
        deleted = await self.client.delete(f"/api/history/{self.record['id']}")
        self.assertEqual(deleted.status, 200)
        self.assertEqual(list(self.store.path.glob("*.webp")), [])
        self.assertEqual((await self.client.get(self.endpoint)).status, 404)
        self.assertEqual((await self.client.get(f"/api/images/{self.record['id']}")).status, 404)

    async def test_delete_before_late_thumbnail_job_starts_cleans_its_cache(self):
        started = asyncio.Event()
        release = asyncio.Event()
        get = self.store.get

        async def delayed_get(saved_record):
            started.set()
            await release.wait()
            return await get(saved_record)

        with mock.patch.object(self.store, "get", side_effect=delayed_get):
            request = asyncio.create_task(self.client.get(self.endpoint))
            try:
                await asyncio.wait_for(started.wait(), 3)
                deleted = await self.client.delete(f"/api/history/{self.record['id']}")
                self.assertEqual(deleted.status, 200)
                release.set()
                self.assertEqual((await request).status, 404)
                self.assertEqual(list(self.store.path.glob("*.webp")), [])
            finally:
                release.set()
                await asyncio.gather(request, return_exceptions=True)

    async def test_cancelled_delete_finishes_database_and_thumbnail_removal(self):
        await self.client.get(self.endpoint)
        app = self.client.server.app
        history = app[HISTORY_KEY]
        deletion_service = app[ARTWORK_DELETION_KEY]
        original_delete = deletion_service.delete
        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed_delete(*args, **kwargs):
            started.set()
            await release.wait()
            return await original_delete(*args, **kwargs)

        route = next(route for route in app.router.routes()
                     if route.method == "DELETE" and route.resource.canonical == "/api/history/{image_id}")
        request = make_mocked_request("DELETE", f"/api/history/{self.record['id']}", app=app,
                                     match_info={"image_id": str(self.record["id"])})
        with mock.patch.object(deletion_service, "delete", side_effect=delayed_delete):
            deleting = asyncio.create_task(route.handler(request))
            try:
                await asyncio.wait_for(started.wait(), 3)
                deleting.cancel()
                await asyncio.sleep(0)
                deleting.cancel()
                await asyncio.sleep(0)
                self.assertFalse(deleting.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await deleting
                with self.assertRaises(KeyError):
                    await history.get_image(self.record["id"])
                self.assertEqual(list(self.store.path.glob("*.webp")), [])
            finally:
                release.set()
                await asyncio.gather(deleting, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
