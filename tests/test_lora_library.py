import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from anima_webui.comfy import ComfyClient, ComfyError
from anima_webui.lora_library import LoraLibraryService, LoraLibraryStore, register_lora_routes
from anima_webui.lora_triggers import LoraTriggerOverrideStore
from anima_webui.persistence import StorageReadOnlyError
from anima_webui.resource_classification import classify_resources
from anima_webui.workflow import WorkflowError


class LibraryClient:
    def __init__(self, count=3):
        self.roots = ["F:/models/loras"]
        self.raw = [{"file_path": f"F:/models/loras/people/hero-{index}.safetensors", "model_name": f"Hero {index:03}",
                     "base_model": "Anima", "sha256": str(index).zfill(64), "favorite": False,
                     "tags": ["portrait", "even" if index % 2 == 0 else "odd"], "notes": "",
                     "file_size": index + 1, "modified": index, "usage_count": index,
                     "preview_url": "/preview" if index % 2 == 0 else "",
                     "civitai": {"trainedWords": [f"@hero_{index}"]}} for index in range(count)]
        self.fail_paths = set()
        self.writes = []
        self.reads = 0
        self.gate = None
        self.active = 0
        self.peak = 0

    async def resource_classifications(self, kind, refresh=False):
        self.reads += 1
        names = [item["file_path"].split("/loras/", 1)[1] for item in self.raw]
        return {"items": classify_resources(names, self.raw, self.roots), "manager_items": copy.deepcopy(self.raw),
                "roots": self.roots, "source": "lora-manager"}

    async def update_lora_metadata(self, path, updates):
        raise AssertionError("External LoRA metadata must remain read-only")


class Characters:
    def __init__(self):
        self.items = {"character:hero": {"id": "character:hero", "section": "character", "title": "Hero", "title_zh": "人物"}}

    def get(self, key):
        return copy.deepcopy(self.items.get(key))

    def search(self, section, **kwargs):
        return {"items": list(self.items.values()), "total": len(self.items)}


class MetadataProxyTests(unittest.IsolatedAsyncioTestCase):

    async def test_preview_proxy_rejects_redirects_active_content_and_oversized_assets(self):
        app = web.Application()
        async def redirect(_):
            return web.Response(status=302, headers={"Location": "https://example.invalid/preview"})
        async def html(_):
            return web.Response(text="<script>bad</script>", content_type="text/html")
        async def large(_):
            return web.Response(body=b"x", content_type="image/png", headers={"Content-Length": str(33 * 1024 * 1024)})
        app.router.add_get("/redirect", redirect)
        app.router.add_get("/html", html)
        app.router.add_get("/large", large)
        server = TestServer(app)
        await server.start_server()
        client = ComfyClient(str(server.make_url("")))
        try:
            for path, message in (("/redirect", "302"), ("/html", "格式"), ("/large", "32 MiB")):
                with self.subTest(path=path), self.assertRaisesRegex(ComfyError, message):
                    await client.lora_preview_asset(path)
        finally:
            await client.close()
            await server.close()


class LoraLibraryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "library.json"
        self.store = LoraLibraryStore(self.path)
        self.client = LibraryClient()
        self.triggers = LoraTriggerOverrideStore(Path(self.directory.name) / "triggers.json")
        self.catalog = Characters()
        self.service = LoraLibraryService(self.client, self.store, self.triggers, self.catalog)

    async def asyncTearDown(self):
        await self.service.close()
        self.directory.cleanup()

    async def first(self):
        return (await self.service.inventory())[0]

    async def collection(self, name="People", parent=""):
        result = await self.service.group("POST", values={"name": name, "parentId": parent})
        return result["groups"][-1]["id"]

    async def test_inventory_and_queries_use_real_loader_classification(self):
        for item in await self.service.inventory():
            index = int(item["display_name"].split()[-1])
            await self.service.update_item(item["id"], {"tags": ["portrait", "even" if index % 2 == 0 else "odd"]})
        result = await self.service.query({"q": "@hero_0", "tag": ["even"], "has_preview": "true"})
        self.assertEqual(result["total"], 1)
        self.assertTrue(result["items"][0]["available"])
        self.assertNotIn("_preview", result["items"][0])
        self.assertEqual(result["facets"]["roots"][0]["path"], self.client.roots[0])
        result = await self.service.query({"tag_exclude": ["even"]})
        self.assertEqual(result["total"], 1)
        with self.assertRaises(WorkflowError):
            await self.service.query({"model_profile": "unsupported"})

    async def test_selection_covers_all_pages_and_is_snapshot(self):
        self.client.raw = LibraryClient(75).raw
        page = await self.service.query({"limit": 60})
        selected = await self.service.query({}, ids_only=True)
        self.assertEqual(len(page["items"]), 60)
        self.assertEqual(len(selected["ids"]), 75)
        self.assertEqual([item["id"] for item in selected["resources"]], selected["ids"])
        self.assertEqual(len({item["filename"] for item in selected["resources"]}), 75)
        frozen = copy.deepcopy(selected["resources"])
        self.client.raw.pop()
        self.assertEqual(len(selected["ids"]), 75)
        self.assertEqual(selected["resources"], frozen)
        self.assertEqual((await self.service.query({}))["total"], 74)

    async def test_selection_resources_come_from_same_filtered_inventory_and_keep_full_paths(self):
        self.client.raw[0]["file_path"] = "F:/models/loras/people/shared.safetensors"
        self.client.raw[1]["file_path"] = "F:/models/loras/styles/shared.safetensors"
        before = self.client.reads
        selected = await self.service.query({"q": "shared", "limit": 1}, ids_only=True)
        self.assertEqual(self.client.reads, before + 1)
        self.assertEqual(selected["total"], 2)
        self.assertEqual([row["id"] for row in selected["resources"]], selected["ids"])
        self.assertEqual({row["filename"] for row in selected["resources"]},
                         {"people/shared.safetensors", "styles/shared.safetensors"})
        self.assertTrue(all(set(row) == {"id", "filename"} for row in selected["resources"]))

    async def test_offline_refresh_preserves_cached_inventory_filters_and_read_only_state(self):
        initial = await self.service.query({"model_profile": "anima"})
        first = initial["items"][0]
        await self.service.update_item(first["id"], {"favorite": True})
        with patch.object(self.client, "resource_classifications", side_effect=ComfyError("plugin connection refused")):
            offline = await self.service.query({"model_profile": "anima", "refresh": "1"})
            self.assertEqual(offline["total"], initial["total"])
            self.assertEqual(offline["facets"], initial["facets"])
            self.assertTrue(offline["offline"])
            self.assertTrue(offline["cached"])
            self.assertFalse(offline["writable"])
            self.assertTrue(offline["last_synced_at"])
            self.assertIn("connection refused", offline["error"])
            self.assertTrue(offline["items"][0]["favorite"])
            self.assertTrue(all(not item["available"] for item in offline["items"]))
            filtered = await self.service.query({"q": "@hero_1", "limit": 1})
            self.assertEqual(filtered["total"], 1)
            selection = await self.service.query({"has_preview": "true"}, ids_only=True)
            self.assertEqual(selection["total"], 2)
            self.assertTrue(selection["cached"])
            detail = await self.service.item(first["id"], allow_cached=True)
            self.assertTrue(detail["offline"])
            with self.assertRaises(ComfyError):
                await self.service.item(first["id"], refresh=True)
            with self.assertRaises(ComfyError):
                await self.service.update_item(first["id"], {"favorite": False})
            with self.assertRaises(ComfyError):
                await self.service.group("POST", values={"name": "Blocked offline"})
            with self.assertRaises(ComfyError):
                await self.service.create_job({"ids": [first["id"]], "action": "favorite", "values": {"favorite": False}})
        self.assertEqual(len(self.client.writes), 0)
        reconnected = await self.service.query({"refresh": "true"})
        self.assertFalse(reconnected["offline"])
        self.assertFalse(reconnected["cached"])
        self.assertTrue(reconnected["writable"])
        await self.service.update_item(first["id"], {"favorite": False})
        self.assertEqual(len(self.client.writes), 0)

    async def test_offline_without_a_successful_inventory_never_fabricates_empty_success(self):
        with patch.object(self.client, "resource_classifications", side_effect=ComfyError("not connected")):
            with self.assertRaises(ComfyError):
                await self.service.query({"refresh": "1"})

    async def test_offline_cache_keeps_classification_and_accepts_successful_empty_library(self):
        self.client.raw[1]["base_model"] = "SDXL"
        initial = await self.service.query({"model_profile": "anima"})
        self.assertEqual(initial["total"], 2)
        with patch.object(self.client, "resource_classifications", side_effect=ComfyError("offline")):
            cached = await self.service.query({"model_profile": "anima", "refresh": "1"})
            self.assertEqual(cached["total"], 2)
            self.assertTrue(cached["offline"])
            self.assertTrue(all(row["model_profile"] == "anima" for row in cached["items"]))
        self.client.raw.clear()
        empty = await self.service.query({"refresh": "1"})
        self.assertEqual(empty["total"], 0)
        self.assertFalse(empty["offline"])

    async def test_binding_execution_snapshot_never_uses_offline_cache(self):
        first = await self.first()
        await self.service.set_binding("character:hero", [{"id": first["id"]}])
        with patch.object(self.client, "resource_classifications", side_effect=ComfyError("offline")):
            displayed = await self.service.binding_snapshot(allow_cached=True)
            self.assertTrue(displayed["cached"])
            with self.assertRaises(ComfyError):
                await self.service.binding_snapshot()

    async def test_thousand_resource_inventory_pages_and_complete_selection(self):
        self.client.raw = LibraryClient(1000).raw
        first = await self.first()
        await self.service.update_item(first["id"], {"default_strength": 0.75})
        await self.service.set_binding("character:hero", [{"id": first["id"]}])
        selected = await self.service.query({}, ids_only=True)
        self.assertEqual(selected["total"], 1000)
        self.assertEqual(len(set(selected["ids"])), 1000)
        page = await self.service.query({"page": 17, "limit": 60})
        self.assertEqual(page["total_pages"], 17)
        self.assertEqual(len(page["items"]), 40)
        configured = await self.service.query({"in_configured": "true", "configured_filenames": [first["filename"]]})
        self.assertEqual(configured["total"], 1)
        self.assertEqual(configured["items"][0]["default_strength"], 0.75)

    async def test_multiroot_collision_visible_but_cannot_bind(self):
        second = copy.deepcopy(self.client.raw[0])
        second["file_path"] = second["file_path"].replace("F:", "G:")
        self.client.roots.append("G:/models/loras")
        self.client.raw.append(second)
        items = await self.service.inventory()
        colliding = [item for item in items if item["filename"] == "people/hero-0.safetensors"]
        self.assertEqual(len(colliding), 2)
        self.assertEqual(len({item["id"] for item in colliding}), 2)
        self.assertTrue(all(item["classification_status"] == "ambiguous" for item in colliding))
        await self.service.update_item(colliding[0]["id"], {"favorite": True})
        self.assertTrue((await self.service.item(colliding[0]["id"]))["favorite"])
        self.assertFalse(self.client.raw[0]["favorite"])
        self.assertFalse(self.client.raw[-1]["favorite"])
        with self.assertRaises(WorkflowError):
            await self.service.set_binding("character:hero", [{"id": colliding[0]["id"]}])

    async def test_directory_snapshot_keeps_all_profiles_and_distinct_roots(self):
        second = copy.deepcopy(self.client.raw[0])
        second.update(file_path="G:/models/loras/people/other.safetensors", base_model="SDXL")
        self.client.roots.extend(["G:/models/loras", "H:/empty/loras"])
        self.client.raw.append(second)
        expected_paths = [("F:/models/loras", "people"), ("G:/models/loras", "people")]
        for profile, total, counts in (("all", 4, [3, 1]), ("anima", 3, [3, 0])):
            with self.subTest(profile=profile):
                result = await self.service.query({"model_profile": profile})
                self.assertEqual(result["total"], total)
                self.assertEqual([(row["root"], row["path"]) for row in result["facets"]["folders"]], expected_paths)
                self.assertEqual([row["count"] for row in result["facets"]["folders"]], counts)
        result = await self.service.query({"model_profile": "anima", "q": "not-found", "root": "G:/models/loras", "tag": ["missing"]})
        self.assertEqual(result["total"], 0)
        self.assertEqual([(row["root"], row["path"]) for row in result["facets"]["folders"]], expected_paths)
        self.assertEqual([row["count"] for row in result["facets"]["folders"]], [3, 0])
        self.assertNotIn("H:/empty/loras", {folder["root"] for folder in result["facets"]["folders"]})


    async def test_removing_membership_does_not_restar_other_memberships(self):
        first = await self.first()
        a, b = await self.collection("A"), await self.collection("B")
        await self.service.update_item(first["id"], {"groupIds": [a, b]})
        await self.service.update_item(first["id"], {"favorite": False})
        await self.service.update_item(first["id"], {"groupIds": [a]}, merge_mode="remove")
        self.assertFalse(self.client.raw[0]["favorite"])
        self.assertEqual((await self.service.item(first["id"]))["groupIds"], [b])

    async def test_nested_groups_validate_and_delete_without_affecting_binding_or_star(self):
        parent = await self.collection("Parent")
        child = await self.collection("Child", parent)
        first = await self.first()
        await self.service.update_item(first["id"], {"groupIds": [child]})
        await self.service.set_binding("character:hero", [{"id": first["id"]}])
        self.assertEqual((await self.service.query({"collection": parent}))["total"], 1)
        revision = self.store.data["revision"]
        with self.assertRaises(WorkflowError):
            await self.service.group("PATCH", parent, {"parentId": child})
        self.assertEqual(self.store.data["revision"], revision)
        with self.assertRaises(WorkflowError):
            await self.collection("Child", parent)
        await self.service.group("DELETE", parent)
        self.assertFalse(self.store.data["groups"])
        self.assertTrue((await self.service.item(first["id"]))["favorite"])
        self.assertTrue(self.store.data["bindings"]["character:hero"]["loras"])

    async def test_default_strength_and_notes_tags_persist_to_right_owner(self):
        first = await self.first()
        await self.service.update_item(first["id"], {"default_strength": -50, "notes": "use softly", "tags": [" A ", "a", "B"]})
        self.assertEqual((await self.service.item(first["id"]))["tags"], ["a", "b"])
        self.assertEqual(self.client.raw[0]["tags"], ["portrait", "even"])
        self.assertEqual((await self.service.item(first["id"]))["notes"], "use softly")
        self.assertEqual(self.client.raw[0]["notes"], "")
        self.assertNotIn("default_strength", self.client.raw[0])
        reopened = LoraLibraryStore(self.path)
        self.assertEqual(reopened.data["resources"][first["id"]]["default_strength"], -50)
        self.assertEqual(self.service.default_strength(first["filename"]), -50)
        for bad in (True, float("nan"), 101):
            with self.assertRaises(WorkflowError):
                await self.service.update_item(first["id"], {"default_strength": bad})


    async def test_bindings_freeze_effective_triggers_and_preserve_snapshot(self):
        first = await self.first()
        await self.triggers.set(first["filename"], ["exact_override"])
        await self.service.set_binding("character:hero", [{"id": first["id"], "strength": 0.65}])
        snapshot = await self.service.binding_snapshot()
        frozen = snapshot["bindings"]["character:hero"]["loras"][0]
        self.assertEqual(frozen["trigger_words"], ["exact_override"])
        await self.triggers.set(first["filename"], ["later"])
        self.assertEqual(frozen["trigger_words"], ["exact_override"])
        self.assertEqual((await self.service.binding_snapshot())["bindings"]["character:hero"]["loras"][0]["trigger_words"], ["later"])

    async def test_empty_binding_snapshot_does_not_contact_plugin(self):
        self.assertEqual(await self.service.binding_snapshot(), {"revision": 0, "bindings": {}})
        self.assertEqual(self.client.reads, 0)

    async def test_replaced_model_preserves_saved_hash_and_marks_changed(self):
        first = await self.first()
        await self.service.set_binding("character:hero", [{"id": first["id"]}])
        self.client.raw[0]["sha256"] = "f" * 64
        self.client.raw[0]["civitai"]["trainedWords"] = ["wrong"]
        snapshot = await self.service.binding_snapshot()
        binding = snapshot["bindings"]["character:hero"]["loras"][0]
        self.assertEqual(binding["sha256"], first["sha256"])
        self.assertEqual(binding["status"], "changed")
        self.assertNotEqual(binding["trigger_words"], ["wrong"])

    async def test_changed_model_can_only_adopt_current_hash_after_explicit_rebind(self):
        first = await self.first()
        await self.service.set_binding("character:hero", [{"id": first["id"]}])
        self.client.raw[0]["sha256"] = "f" * 64
        self.client.raw[0]["civitai"]["trainedWords"] = ["replacement_trigger"]
        await self.service.set_binding("character:hero", [{"id": first["id"], "strength": 0.8}])
        previous = self.store.data["bindings"]["character:hero"]["loras"][0]
        self.assertEqual(previous["sha256"], first["sha256"])
        await self.service.set_binding("character:hero", [{"id": first["id"], "strength": 0.8, "rebind": True}])
        rebound = (await self.service.binding_snapshot())["bindings"]["character:hero"]["loras"][0]
        self.assertEqual(rebound["sha256"], "f" * 64)
        self.assertEqual(rebound["trigger_words"], ["replacement_trigger"])
        self.assertEqual(rebound["status"], "recognized")
        self.assertNotIn("rebind", self.store.data["bindings"]["character:hero"]["loras"][0])
        for invalid in ("true", 1, None):
            with self.assertRaises(WorkflowError):
                await self.service.set_binding("character:hero", [{"id": first["id"], "rebind": invalid}])
        self.client.raw[0]["base_model"] = "Krea 2"
        with self.assertRaisesRegex(WorkflowError, "重新绑定需要"):
            await self.service.set_binding("character:hero", [{"id": first["id"], "rebind": True}])
        self.client.raw.pop(0)
        with self.assertRaisesRegex(WorkflowError, "重新绑定需要"):
            await self.service.set_binding("character:hero", [{"id": first["id"], "rebind": True}])

    async def test_missing_binding_can_be_retained_disabled_and_deleted(self):
        first = await self.first()
        await self.service.set_binding("character:hero", [{"id": first["id"]}])
        self.client.raw.pop(0)
        await self.service.set_binding("character:hero", [{"id": first["id"], "enabled": False}])
        item = (await self.service.binding_snapshot())["bindings"]["character:hero"]["loras"][0]
        self.assertEqual(item["status"], "missing")
        self.assertFalse(item["enabled"])
        self.catalog.items.clear()
        snapshot = await self.service.binding_snapshot()
        self.assertTrue(snapshot["bindings"]["character:hero"]["character_missing"])
        await self.service.set_binding("character:hero", [])
        await self.service.delete_binding("character:hero")
        self.assertFalse(self.store.data["bindings"])

    async def test_external_move_is_not_silently_relinked_by_hash(self):
        first = await self.first()
        await self.service.set_binding("character:hero", [{"id": first["id"]}])
        self.client.raw[0]["file_path"] = "F:/models/loras/moved.safetensors"
        snapshot = await self.service.binding_snapshot()
        self.assertEqual(snapshot["bindings"]["character:hero"]["loras"][0]["status"], "missing")
        self.assertNotEqual((await self.first())["id"], first["id"])



    async def test_batch_bind_appends_all_selected_loras(self):
        items = await self.service.inventory()
        job = await self.service.create_job({"ids": [item["id"] for item in items], "action": "bind", "values": {"character_id": "character:hero"}})
        await self.service._tasks[job["id"]]
        self.assertEqual(len(self.store.data["bindings"]["character:hero"]["loras"]), 3)

    async def test_append_preserves_existing_missing_changed_and_disabled_rows(self):
        first, second, third = await self.service.inventory()
        await self.service.set_binding("character:hero", [
            {"id": second["id"], "strength": -0.35, "enabled": False},
            {"id": first["id"], "strength": 0.65}])
        original = copy.deepcopy(self.store.data["bindings"]["character:hero"]["loras"])
        self.client.raw[1]["sha256"] = "replacement"
        self.client.raw.pop(0)
        values = [{"id": first["id"], "strength": 9}, {"id": second["id"], "strength": 8, "enabled": True},
                  {"id": third["id"], "strength": 0.7}]
        result = await self.service.append_binding("character:hero", values)
        self.assertEqual((result["added_count"], result["skipped_count"]), (1, 2))
        self.assertEqual(result["binding"]["loras"][:2], original)
        self.assertEqual(result["binding"]["loras"][2]["id"], third["id"])
        revision, source = self.store.data["revision"], self.path.read_bytes()
        with patch.object(self.store, "_write", wraps=self.store._write) as write:
            repeated = await self.service.append_binding("character:hero", values)
            write.assert_not_called()
        self.assertEqual((repeated["added_count"], repeated["skipped_count"]), (0, 3))
        self.assertEqual(self.store.data["revision"], revision)
        self.assertEqual(self.path.read_bytes(), source)

    async def test_append_checks_membership_inside_the_atomic_store_mutation(self):
        first = await self.first()
        other = LoraLibraryService(self.client, self.store, self.triggers, self.catalog)
        try:
            results = await asyncio.gather(
                self.service.append_binding("character:hero", [{"id": first["id"], "strength": 0.5}]),
                other.append_binding("character:hero", [{"id": first["id"], "strength": 0.9}]))
            self.assertEqual(sum(result["added_count"] for result in results), 1)
            self.assertEqual(sum(result["skipped_count"] for result in results), 1)
            self.assertEqual(len(self.store.data["bindings"]["character:hero"]["loras"]), 1)
            self.assertEqual(self.store.data["revision"], 1)
        finally:
            await other.close()

    async def test_append_capacity_failure_keeps_the_whole_original_binding(self):
        self.client.raw = LibraryClient(129).raw
        items = await self.service.inventory()
        await self.service.set_binding("character:hero", [{"id": item["id"]} for item in items[:128]])
        original, source = copy.deepcopy(self.store.data), self.path.read_bytes()
        with self.assertRaisesRegex(WorkflowError, "128"):
            await self.service.append_binding("character:hero", [{"id": items[0]["id"], "strength": 9}, {"id": items[128]["id"]}])
        self.assertEqual(self.store.data, original)
        self.assertEqual(self.path.read_bytes(), source)

    async def test_old_batch_bind_keeps_rows_in_place_and_freezes_new_default_weights(self):
        first, second, third = await self.service.inventory()
        await self.service.set_binding("character:hero", [
            {"id": second["id"], "strength": 0.4, "enabled": False}, {"id": first["id"], "strength": 0.8}])
        original = copy.deepcopy(self.store.data["bindings"]["character:hero"]["loras"])
        await self.service.update_item(third["id"], {"default_strength": 0.6})
        gate, append = asyncio.Event(), self.service.append_binding
        async def delayed(*args, **kwargs):
            await gate.wait()
            return await append(*args, **kwargs)
        with patch.object(self.service, "append_binding", side_effect=delayed):
            job = await self.service.create_job({"ids": [first["id"], third["id"], second["id"]], "action": "bind",
                                                 "values": {"character_id": "character:hero"}})
            await self.service.update_item(third["id"], {"default_strength": 2})
            gate.set()
            await self.service._tasks[job["id"]]
        rows = self.store.data["bindings"]["character:hero"]["loras"]
        self.assertEqual(rows[:2], original)
        self.assertEqual((rows[2]["id"], rows[2]["strength"]), (third["id"], 0.6))
        self.assertEqual(sum(item["skipped_count"] for item in self.service.jobs[job["id"]]["results"]), 2)

    async def test_batch_character_binding_freezes_weight_retries_only_failed_and_is_idempotent(self):
        first = await self.first()
        self.catalog.items["character:other"] = {"id": "character:other", "section": "character", "title": "Other"}
        await self.service.update_item(first["id"], {"default_strength": 0.65})
        await self.service.set_binding("character:hero", [{"id": first["id"], "strength": -0.25, "enabled": False}])
        original = copy.deepcopy(self.store.data["bindings"]["character:hero"])
        payload = {"ids": ["character:hero", "character:other", "character:missing", "character:hero"],
                   "action": "bind_characters", "values": {"resource_id": first["id"]}}
        gate, append = asyncio.Event(), self.service.append_binding
        async def delayed(*args, **kwargs):
            await gate.wait()
            return await append(*args, **kwargs)
        with patch.object(self.service, "append_binding", side_effect=delayed):
            job = await self.service.create_job(payload)
            self.assertEqual((job["target_type"], job["total"]), ("character", 3))
            payload["values"]["strength"] = 99
            await self.service.update_item(first["id"], {"default_strength": 2})
            gate.set()
            await self.service._tasks[job["id"]]
        result = self.service.jobs[job["id"]]
        self.assertEqual((result["succeeded"], result["failed"]), (2, 1))
        self.assertTrue(all(row["target_type"] == "character" for row in result["results"]))
        self.assertEqual(self.store.data["bindings"]["character:hero"], original)
        self.assertEqual(self.store.data["bindings"]["character:other"]["loras"][0]["strength"], 0.65)
        self.catalog.items["character:missing"] = {"id": "character:missing", "section": "character", "title": "Recovered"}
        retry = await self.service.retry_job(job["id"])
        await self.service._tasks[retry["id"]]
        self.assertEqual(retry["total"], 1)
        self.assertEqual(self.service.jobs[retry["id"]]["results"][0]["id"], "character:missing")
        self.assertEqual(self.store.data["bindings"]["character:missing"]["loras"][0]["strength"], 0.65)
        revision = self.store.data["revision"]
        repeated = await self.service.create_job(payload)
        await self.service._tasks[repeated["id"]]
        self.assertEqual(self.service.jobs[repeated["id"]]["succeeded"], 3)
        self.assertEqual(sum(row["skipped_count"] for row in self.service.jobs[repeated["id"]]["results"]), 3)
        self.assertEqual(self.store.data["revision"], revision)

    async def test_batch_character_cancel_completes_only_started_targets(self):
        first = await self.first()
        ids = [f"character:test-{index}" for index in range(8)]
        for character_id in ids:
            self.catalog.items[character_id] = {"id": character_id, "section": "character", "title": character_id}
        gate, append, started = asyncio.Event(), self.service.append_binding, []
        async def delayed(character_id, values):
            started.append(character_id)
            await gate.wait()
            return await append(character_id, values)
        with patch.object(self.service, "append_binding", side_effect=delayed):
            job = await self.service.create_job({"ids": ids, "action": "bind_characters",
                                                 "values": {"resource_id": first["id"], "strength": 0.75}})
            for _ in range(20):
                if len(started) == 2:
                    break
                await asyncio.sleep(0)
            self.assertEqual(len(started), 2)
            self.service.jobs[job["id"]]["cancel_requested"] = True
            gate.set()
            await self.service._tasks[job["id"]]
        result = self.service.jobs[job["id"]]
        self.assertEqual((result["status"], result["completed"], result["total"]), ("cancelled", 2, 8))
        self.assertEqual(set(self.store.data["bindings"]), set(started))

    async def test_batch_character_binding_validates_source_and_weight_before_enqueue(self):
        first = await self.first()
        for values in ({"resource_id": []}, {"resource_id": first["id"], "strength": True},
                       {"resource_id": first["id"], "strength": 101}, {"resource_id": first["id"], "rebind": True}):
            with self.subTest(values=values), self.assertRaises(WorkflowError):
                await self.service.create_job({"ids": ["character:hero"], "action": "bind_characters", "values": values})
        self.client.raw[0]["base_model"] = "Krea 2"
        with self.assertRaises(WorkflowError):
            await self.service.create_job({"ids": ["character:hero"], "action": "bind_characters", "values": {"resource_id": first["id"]}})
        self.assertFalse(self.service.jobs)
        self.assertFalse(self.store.data["bindings"])





    async def test_malformed_binding_store_is_protected_and_api_fields_validated(self):
        first = await self.first()
        await self.service.set_binding("character:hero", [{"id": first["id"]}])
        valid = copy.deepcopy(self.store.data)
        for field in ("filename", "sha256", "trigger_words"):
            corrupted = copy.deepcopy(valid)
            del corrupted["bindings"]["character:hero"]["loras"][0][field]
            self.path.write_text(json.dumps(corrupted), encoding="utf-8")
            self.assertIsNotNone(LoraLibraryStore(self.path).read_only_reason)
        for parent in ([], {}):
            with self.assertRaises(WorkflowError):
                await self.service.group("POST", values={"name": "Invalid", "parentId": parent})
        with self.assertRaises(WorkflowError):
            await self.service.set_binding("character:hero", [{"id": first["id"], "filename": "spoof"}])

    async def test_close_rejects_new_jobs_and_mutations(self):
        first = await self.first()
        await self.service.close()
        with self.assertRaises(ComfyError):
            await self.service.update_item(first["id"], {"favorite": True})
        with self.assertRaises(ComfyError):
            await self.service.create_job({"ids": [first["id"]], "action": "favorite", "values": {"favorite": True}})

    async def test_corrupt_or_externally_changed_store_is_protected(self):
        original = '{"version":900}'
        self.path.write_text(original, encoding="utf-8")
        broken = LoraLibraryStore(self.path)
        self.assertTrue(broken.storage_status()["read_only"])
        with self.assertRaises(StorageReadOnlyError):
            await broken.change(lambda data: None)
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)
        with self.assertRaises(StorageReadOnlyError):
            await self.store.change(lambda data: None)

    async def test_disk_failure_rolls_back_memory(self):
        before = copy.deepcopy(self.store.data)
        with patch.object(self.store, "_write", side_effect=OSError("full")):
            with self.assertRaises(StorageReadOnlyError):
                await self.collection()
        self.assertEqual(self.store.data, before)

    async def test_routes_aliases_and_deleted_character_binding(self):
        app = web.Application()
        register_lora_routes(app, self.service)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            response = await client.get("/api/lora-library?limit=1")
            self.assertEqual(response.status, 200)
            first = (await response.json())["items"][0]
            response = await client.post("/api/lora-collections", json={"name": "Test"})
            self.assertEqual(response.status, 200)
            response = await client.put("/api/character-lora-bindings/character:hero", json={"loras": [{"id": first["id"]}]})
            self.assertEqual(response.status, 200)
            self.catalog.items.clear()
            response = await client.get("/api/character-lora-bindings/character:hero")
            self.assertTrue((await response.json())["character_missing"])
        finally:
            await client.close()

    async def test_character_job_http_contract_and_retry_results(self):
        app = web.Application()
        register_lora_routes(app, self.service)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            first = await self.first()
            response = await client.post("/api/lora-library/jobs", json={
                "action": "bind_characters", "ids": ["character:hero", "character:later"],
                "values": {"resource_id": first["id"], "strength": 0.6}})
            self.assertEqual(response.status, 200)
            job = await response.json()
            self.assertEqual(job["target_type"], "character")
            self.assertFalse(any(key.startswith("_") for key in job))
            await self.service._tasks[job["id"]]
            response = await client.get(f"/api/lora-library/jobs/{job['id']}")
            result = await response.json()
            self.assertEqual((result["succeeded"], result["failed"]), (1, 1))
            self.assertEqual(result["results"][0]["target_type"], "character")
            self.catalog.items["character:later"] = {"id": "character:later", "section": "character", "title": "Later"}
            response = await client.post(f"/api/lora-library/jobs/{job['id']}/retry", json={})
            retry = await response.json()
            self.assertEqual(retry["total"], 1)
            await self.service._tasks[retry["id"]]
            response = await client.get(f"/api/lora-library/jobs/{retry['id']}")
            retried = await response.json()
            self.assertEqual(retried["results"], [{"id": "character:later", "target_type": "character",
                                                  "status": "succeeded", "added_count": 1, "skipped_count": 0}])
        finally:
            await client.close()


if __name__ == "__main__":
    unittest.main()
