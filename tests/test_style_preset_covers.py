import asyncio
import io
import json
import os
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

from anima_webui.style_preset_covers import StylePresetCoverStore, RETENTION_SECONDS
from anima_webui.style_presets import StylePresetStore
from anima_webui.workflow import WorkflowError


def picture(size=(400, 300), mode="RGB", color="red", fmt="PNG", **options):
    with Image.new(mode, size, color) as image:
        buffer = io.BytesIO()
        image.save(buffer, format=fmt, **options)
        return buffer.getvalue()


class Presets:
    def __init__(self):
        self.items = []
        self.read_only_reason = None
        self.load_warnings = []
        self.cover_references_complete = True

    def ensure_writable(self):
        if self.read_only_reason:
            raise WorkflowError("read only")


class CoverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.presets = Presets()
        self.store = StylePresetCoverStore(Path(self.temp.name), self.presets)

    async def asyncTearDown(self):
        await self.store.close()
        self.temp.cleanup()

    async def test_crop_content_and_durable_read(self):
        with Image.new("RGB", (800, 300), "red") as source:
            source.paste("blue", (400, 0, 800, 300))
            data = io.BytesIO()
            source.save(data, format="PNG")
        cover = await self.store.add(data.getvalue(), {"x": .5, "y": 0, "width": .5, "height": 1})
        body = await self.store.read(cover["id"])
        with Image.open(io.BytesIO(body)) as image:
            self.assertEqual(image.size, (768, 576))
            self.assertGreater(image.getpixel((384, 288))[2], 240)
            self.assertFalse(image.getexif())
        reopened = StylePresetCoverStore(self.store.path, self.presets)
        self.assertEqual(await reopened.read(cover["id"]), body)
        self.assertEqual(len(list(self.store.path.iterdir())), 1)

    async def test_exif_orientation_before_crop(self):
        exif = Image.Exif()
        exif[274] = 6
        result = await self.store.add(picture((300, 400), fmt="JPEG", exif=exif),
                                      {"x": 0, "y": 0, "width": 1, "height": 1})
        with Image.open(io.BytesIO(await self.store.read(result["id"]))) as image:
            self.assertEqual(image.size, (768, 576))
            self.assertFalse(image.getexif())

    async def test_invalid_formats_crops_animation_and_limits(self):
        for crop in ({}, {"x": -1, "y": 0, "width": 1, "height": 1},
                     {"x": 0, "y": 0, "width": float("nan"), "height": 1},
                     {"x": 0, "y": 0, "width": .5, "height": 1},
                     {"x": False, "y": 0, "width": 1, "height": 1}):
            with self.subTest(crop=crop), self.assertRaises(WorkflowError):
                await self.store.add(picture(), crop)
        for body in (b"broken", picture(fmt="BMP")):
            with self.assertRaises(WorkflowError):
                await self.store.add(body)
        with patch("anima_webui.style_preset_covers.MAX_IMAGE_BYTES", 10):
            with self.assertRaises(WorkflowError):
                await self.store.add(picture())
        with patch("anima_webui.style_preset_covers.MAX_IMAGE_PIXELS", 10):
            with self.assertRaises(WorkflowError):
                await self.store.add(picture())
        with Image.new("RGB", (400, 300), "blue") as frame:
            animated = picture(save_all=True, append_images=[frame], duration=100, loop=0)
        with self.assertRaises(WorkflowError):
            await self.store.add(animated)
        self.assertEqual(list(self.store.path.iterdir()), [])

    async def test_references_survive_collection_and_failed_replace(self):
        old = await self.store.add(picture())
        new = await self.store.add(picture(color="blue"))
        self.presets.items = [{"id": "one", "cover_id": old["id"]}]
        old_time = time.time() - RETENTION_SECONDS - 10
        for item in (old, new):
            os.utime(self.store._path(item["id"]), (old_time, old_time))

        async def failure():
            raise OSError("write failed")

        with self.assertRaises(OSError):
            await self.store.mutate(failure, payload={"cover_id": new["id"]})
        self.assertEqual(self.presets.items[0]["cover_id"], old["id"])
        self.assertEqual(await self.store.collect(), 1)
        self.assertTrue(self.store._path(old["id"]).exists())

    async def test_reference_commit_blocks_collection_and_removal_gets_grace(self):
        cover = await self.store.add(picture())
        old_time = time.time() - RETENTION_SECONDS - 10
        os.utime(self.store._path(cover["id"]), (old_time, old_time))
        started, release = asyncio.Event(), asyncio.Event()

        async def commit():
            started.set()
            await release.wait()
            self.presets.items = [{"cover_id": cover["id"]}]

        mutation = asyncio.create_task(self.store.mutate(commit, payload={"cover_id": cover["id"]}))
        await started.wait()
        collect = asyncio.create_task(self.store.collect())
        await asyncio.sleep(0)
        self.assertFalse(collect.done())
        release.set()
        await mutation
        self.assertEqual(await collect, 0)

        async def remove():
            self.presets.items = []

        await self.store.mutate(remove)
        self.assertEqual(await self.store.collect(), 0)
        self.assertEqual(await self.store.collect(now=time.time() + RETENTION_SECONDS + 1), 1)

    async def test_cancelled_upload_holds_lock_until_worker_finishes(self):
        started, release = threading.Event(), threading.Event()
        original = self.store._add

        def blocked(body, crop):
            started.set()
            release.wait(5)
            return original(body, crop)

        with patch.object(self.store, "_add", blocked):
            uploading = asyncio.create_task(self.store.add(picture()))
            await asyncio.to_thread(started.wait, 5)
            uploading.cancel()
            collecting = asyncio.create_task(self.store.collect())
            await asyncio.sleep(0)
            self.assertFalse(collecting.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await uploading
            self.assertEqual(await collecting, 0)
        self.assertEqual(len(list(self.store.path.glob("*.webp"))), 1)

    async def test_protected_store_never_collects_and_rejects_uploads(self):
        cover = await self.store.add(picture())
        self.presets.read_only_reason = "corrupt file"
        self.assertEqual(await self.store.collect(now=time.time() + RETENTION_SECONDS + 1), 0)
        self.assertTrue(self.store._path(cover["id"]).exists())
        with self.assertRaises(WorkflowError):
            await self.store.add(picture())

    async def test_successful_legacy_migration_allows_orphan_collection_despite_notice(self):
        kept = await self.store.add(picture())
        orphan = await self.store.add(picture(color="blue"))
        path = self.store.path / "presets.json"
        path.write_text(json.dumps({"version": 2, "items": [
            {"id": "kept", "name": "Kept", "settings": {}, "cover_id": kept["id"]},
        ]}), encoding="utf-8")
        migrated = StylePresetStore(path)
        self.assertTrue(migrated.load_warnings)
        self.assertTrue(migrated.cover_references_complete)
        self.assertTrue(json.loads(path.read_text("utf-8"))["cover_references_complete"])
        self.store.presets = migrated
        self.assertEqual(await self.store.collect(now=time.time() + RETENTION_SECONDS + 1), 1)
        self.assertTrue(self.store._path(kept["id"]).exists())
        self.assertFalse(self.store._path(orphan["id"]).exists())

    async def test_discarded_preset_suspends_collection_across_restart_and_metadata_write(self):
        potential = await self.store.add(picture())
        path = self.store.path / "presets.json"
        path.write_text(json.dumps({"version": 3, "items": [
            {"id": "kept", "name": "Kept", "settings": {}},
            {"id": "broken", "name": "Broken", "settings": {"steps": "invalid"}, "cover_id": potential["id"]},
        ]}), encoding="utf-8")
        recovered = StylePresetStore(path)
        self.assertEqual(len(recovered.items), 1)
        self.assertFalse(recovered.cover_references_complete)
        await recovered.update("kept", {"name": "Renamed"})
        self.assertFalse(json.loads(path.read_text("utf-8"))["cover_references_complete"])
        reopened = StylePresetStore(path)
        self.assertFalse(reopened.cover_references_complete)
        self.assertEqual(reopened.load_warnings, [])
        self.store.presets = reopened
        self.assertEqual(await self.store.collect(now=time.time() + RETENTION_SECONDS + 1), 0)
        self.assertTrue(self.store._path(potential["id"]).exists())

    async def test_group_only_recovery_does_not_mark_cover_references_incomplete(self):
        orphan = await self.store.add(picture())
        path = self.store.path / "presets.json"
        path.write_text(json.dumps({"version": 3, "items": [
            {"id": "kept", "name": "Kept", "settings": {}, "groups": ["broken"]},
        ], "groups": [{"id": "broken", "name": ""}]}), encoding="utf-8")
        recovered = StylePresetStore(path)
        self.assertTrue(recovered.load_warnings)
        self.assertTrue(recovered.cover_references_complete)
        self.assertEqual(recovered.items[0]["groups"], [])
        self.store.presets = recovered
        self.assertEqual(await self.store.collect(now=time.time() + RETENTION_SECONDS + 1), 1)
        self.assertFalse(self.store._path(orphan["id"]).exists())

    async def test_corrupt_source_keeps_unknown_references_after_restart_before_new_write(self):
        potential = await self.store.add(picture())
        path = self.store.path / "presets.json"
        path.write_text("{broken", encoding="utf-8")
        recovered = StylePresetStore(path)
        self.assertFalse(recovered.cover_references_complete)
        self.assertFalse(path.exists())
        reopened = StylePresetStore(path)
        self.assertFalse(reopened.cover_references_complete)
        self.store.presets = reopened
        self.assertEqual(await self.store.collect(now=time.time() + RETENTION_SECONDS + 1), 0)
        self.assertTrue(self.store._path(potential["id"]).exists())

    async def test_traversal_and_corrupt_cover_rejected(self):
        for value in ("../outside", "x" * 64, None):
            with self.assertRaises(WorkflowError):
                await self.store.read(value)
        cover = await self.store.add(picture())
        self.store._path(cover["id"]).write_bytes(b"invalid")
        with self.assertRaisesRegex(WorkflowError, "损坏"):
            await self.store.read(cover["id"])
