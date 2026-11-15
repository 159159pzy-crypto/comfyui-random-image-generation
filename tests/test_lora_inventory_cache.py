"""A page of previews must not normalize the whole model library per image."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anima_webui.comfy import ComfyError
from anima_webui.lora_library import LoraLibraryService, LoraLibraryStore
from anima_webui.lora_triggers import LoraTriggerOverrideStore
from anima_webui.resource_classification import classify_base_model
from test_lora_library import Characters, LibraryClient


class CachedLibraryClient(LibraryClient):
    def __init__(self, count=3):
        super().__init__(count)
        self.cached = None
        self.generation = 0
        self.offline = False
        self.expired = False

    def classification_generation(self, kind):
        return self.generation

    async def resource_classifications(self, kind, refresh=False):
        if self.offline:
            raise ComfyError("fixture offline")
        if self.cached is None or refresh or self.expired:
            self.cached = await super().resource_classifications(kind, refresh)
            self.generation += 1
            self.expired = False
        return self.cached


class InventoryCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.store = LoraLibraryStore(root / "library.json")
        self.triggers = LoraTriggerOverrideStore(root / "triggers.json")
        self.client = CachedLibraryClient(1000)
        self.service = LoraLibraryService(self.client, self.store, self.triggers, Characters())

    async def asyncTearDown(self):
        await self.service.close()
        self.directory.cleanup()

    async def test_sixty_previews_share_one_normalized_thousand_item_inventory(self):
        with patch("anima_webui.lora_library.classify_base_model", wraps=classify_base_model) as classify:
            items = await self.service.inventory()
            result = await asyncio.gather(*(self.service.item(item["id"]) for item in items[:60]))
            self.assertEqual(classify.call_count, 1000)
            self.assertEqual([item["id"] for item in result], [item["id"] for item in items[:60]])
            result[0]["tags"].append("caller mutation")
            self.assertNotIn("caller mutation", (await self.service.item(result[0]["id"]))["tags"])

    async def test_preview_snapshot_reuses_known_paths_even_after_source_expiry(self):
        items = await self.service.inventory()
        reads = self.client.reads
        self.client.expired = True
        with patch.object(self.client, "resource_classifications", side_effect=AssertionError("preview must not rescan")):
            result = await asyncio.gather(*(self.service.item(item["id"], use_snapshot=True) for item in items[:60]))
        self.assertEqual(len(result), 60)
        self.assertEqual(self.client.reads, reads)
        await self.service.query({})
        self.assertEqual(self.client.reads, reads + 1)

    async def test_cache_hits_do_not_change_last_source_sync_time(self):
        first = (await self.service.inventory())[0]
        timestamp = self.service._last_synced_at
        await self.service.item(first["id"])
        await self.service.item(first["id"], use_snapshot=True)
        self.assertEqual(self.service._last_synced_at, timestamp)


    async def test_local_edit_reuses_inventory_and_never_imports_shared_personal_fields(self):
        first = (await self.service.inventory())[0]
        self.client.raw[0].update(favorite=True, notes="external notes", tags=["shared"])
        reads = self.client.reads
        with patch.object(self.client, "resource_classifications", side_effect=AssertionError("local edit must not rescan")):
            result = await self.service.update_item(first["id"], {"favorite": True, "default_strength": 0.4})
        self.assertEqual((result["favorite"], result["notes"], result["tags"], result["default_strength"]),
                         (True, "", [], 0.4))
        self.assertEqual(self.client.reads, reads)
        self.assertEqual((await self.service.item(first["id"], use_snapshot=True))["notes"], "")

    async def test_cached_inventory_observes_local_revisions_and_technical_refreshes(self):
        first = (await self.service.inventory())[0]
        identity = first["id"]
        await self.service.update_item(identity, {"tags": ["Local"], "notes": "kept here"})
        self.assertEqual((await self.service.item(identity))["tags"], ["local"])
        await self.triggers.set(first["filename"], ["local trigger"])
        self.assertEqual((await self.service.item(identity))["trigger_words"], ["local trigger"])
        await self.triggers.delete(first["filename"])
        self.assertEqual((await self.service.item(identity))["trigger_words"], first["trigger_words"])
        self.client.raw[0].update(model_name="New technical name", notes="shared private notes")
        self.client.expired = True
        refreshed = await self.service.item(identity)
        self.assertEqual(refreshed["display_name"], "New technical name")
        self.assertEqual(refreshed["notes"], "kept here")
        self.client.offline = True
        cached = await self.service.item(identity, allow_cached=True)
        self.assertTrue(cached["offline"])
        self.assertFalse(cached["writable"])
        self.client.offline = False
        self.assertFalse((await self.service.item(identity))["offline"])




    async def test_failed_trigger_save_does_not_publish_or_cache_uncommitted_words(self):
        first = (await self.service.inventory())[0]
        entered, finish = asyncio.Event(), asyncio.Event()

        async def fail_save(*args):
            entered.set()
            await finish.wait()
            raise OSError("fixture disk full")

        with patch.object(self.triggers, "_save", side_effect=fail_save):
            task = asyncio.create_task(self.triggers.set(first["filename"], ["not committed"]))
            await entered.wait()
            try:
                self.assertEqual((await self.service.item(first["id"]))["trigger_words"], first["trigger_words"])
            finally:
                finish.set()
                with self.assertRaises(OSError):
                    await task
        self.assertEqual((await self.service.item(first["id"]))["trigger_words"], first["trigger_words"])
