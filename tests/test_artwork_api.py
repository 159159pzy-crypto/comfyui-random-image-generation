from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from anima_webui.server import ARTWORK_DELETION_KEY, HISTORY_KEY, create_app
from test_server import APP_DIR, FakeComfy, _write_fake_tools


class ArtworkApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.app = create_app(app_dir=APP_DIR, data_dir=root / "data", comfy=FakeComfy(),
                              anima_tools_dir=_write_fake_tools(root))
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.history = self.app[HISTORY_KEY]
        await self.history.create_batch("artwork", 3, {"model_profile": "anima"})
        self.ids = []
        for i, (workbench, profile) in enumerate((("random", "anima"),) * 3):
            item = await self.history.add_image(
                batch_id="artwork", sequence=i + 1, prompt_id=f"p-{i}",
                image={"filename": f"output-{i}.png"}, positive_prompt="test", negative_prompt="",
                sample_seed=1, prompt_seed=2, settings={"workbench": workbench, "model_profile": profile},
            )
            self.ids.append(item["id"])

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def json(self, method, path, body=None, status=200, **kwargs):
        if body is not None:
            kwargs["json"] = body
        response = await self.client.request(method, path, **kwargs)
        payload = await response.json()
        self.assertEqual(response.status, status, payload)
        return payload

    async def test_removed_cleanup_and_bulk_delete_routes_remain_unavailable(self):
        routes = (("POST", "/api/history/delete"),
                  ("GET", "/api/image-cleanup"), ("PUT", "/api/image-cleanup"),
                  ("POST", "/api/image-cleanup/run"), ("POST", "/api/image-cleanup/leases"))
        for method, path in routes:
            with self.subTest(method=method, path=path):
                response = await self.client.request(method, path)
                self.assertEqual(response.status, 404)
        self.assertEqual((await self.history.list_images())["total"], 3)

    async def test_history_retains_source_and_cursor_pagination_with_memberships(self):
        first = await self.json("GET", "/api/history?workbench=random&limit=1")
        self.assertEqual(first["total"], 3)
        self.assertEqual(first["items"][0]["id"], self.ids[2])
        self.assertEqual(first["items"][0]["folder_ids"], [])
        second = await self.json("GET", f"/api/history?workbench=random&limit=1&before_id={first['next_before_id']}")
        self.assertEqual(second["items"][0]["id"], self.ids[1])
        self.assertTrue(second["has_more"])
        await self.json("GET", "/api/history?folder_id=missing", status=404)

    async def test_collection_crud_order_memberships_and_recent_history_contract(self):
        self.assertEqual(await self.json("GET", "/api/artwork-folders"), {"folders": [], "total": 0})
        first = (await self.json("POST", "/api/artwork-folders", {"name": "First"}, status=201))["folder"]
        second = (await self.json("POST", "/api/artwork-folders", {"name": "Second"}, status=201))["folder"]
        self.assertFalse(first["is_system"])
        renamed = await self.json("PATCH", f"/api/artwork-folders/{first['id']}", {"name": "Renamed"})
        self.assertEqual(renamed["folder"]["name"], "Renamed")
        ordered = await self.json("POST", "/api/artwork-folders/reorder", {"folder_ids": [second["id"], first["id"]]})
        self.assertEqual([item["id"] for item in ordered["folders"]], [second["id"], first["id"]])
        folders = [first["id"], second["id"]]
        await self.json("POST", "/api/artwork-folders/members", {"image_ids": self.ids[1:], "folder_ids": folders, "action": "add"})
        result = await self.json("POST", "/api/artwork-folders/members", {"image_ids": self.ids[:1], "folder_ids": folders, "action": "add"})
        self.assertEqual(result["changed_count"], 2)
        self.assertEqual(result["items"], [{"id": self.ids[0], "folder_ids": sorted(folders)}])
        listing = await self.json("GET", f"/api/history?folder_id={first['id']}&limit=2")
        self.assertEqual([item["id"] for item in listing["items"]], [self.ids[0], self.ids[2]])
        self.assertIsNone(listing["next_before_id"])
        self.assertTrue(listing["has_more"])
        last = await self.json("GET", f"/api/history?folder_id={first['id']}&limit=2&page=2")
        self.assertEqual([item["id"] for item in last["items"]], [self.ids[1]])
        filtered = await self.json("GET", f"/api/history?folder_id={first['id']}&workbench=random")
        self.assertEqual([item["id"] for item in filtered["items"]], [self.ids[0], self.ids[2], self.ids[1]])
        await self.json("GET", f"/api/history?folder_id={first['id']}&before_id=3", status=400)
        deleted = await self.json("DELETE", f"/api/artwork-folders/{first['id']}")
        self.assertEqual(deleted["affected_image_ids"], self.ids)
        self.assertTrue(all(item["folder_ids"] == [second["id"]] for item in deleted["items"]))
        self.assertEqual((await self.json("GET", "/api/history"))["total"], 3)

    async def test_collection_input_validation_and_atomic_conflicts(self):
        folder = (await self.json("POST", "/api/artwork-folders", {"name": "Folder"}, status=201))["folder"]["id"]
        for name in ("folder", "", "x" * 81, "bad\nname", None):
            await self.json("POST", "/api/artwork-folders", {"name": name}, status=400)
        await self.json("POST", "/api/artwork-folders", {"name": "Extra", "path": "x"}, status=400)
        for body in ({"image_ids": self.ids, "folder_ids": [folder]},
                     {"image_ids": [True], "folder_ids": [folder], "action": "add"},
                     {"image_ids": self.ids, "folder_ids": [], "action": "add"},
                     {"image_ids": self.ids, "folder_ids": [folder], "action": "clear"}):
            await self.json("POST", "/api/artwork-folders/members", body, status=400)
        failure = await self.json("POST", "/api/artwork-folders/members", {
            "image_ids": [*self.ids, 9999], "folder_ids": [folder, "gone"], "action": "add"}, status=409)
        self.assertEqual(failure["code"], "artwork_collection_changed")
        self.assertEqual(failure["missing_image_ids"], [9999])
        self.assertEqual(failure["missing_folder_ids"], ["gone"])
        self.assertEqual((await self.json("GET", "/api/artwork-folders"))["total"], 0)
        await self.json("POST", "/api/artwork-folders/reorder", {"folder_ids": [folder, folder]}, status=400)
        mismatch = await self.json("POST", "/api/artwork-folders/reorder", {"folder_ids": []}, status=409)
        self.assertEqual(mismatch["current_folder_ids"], [folder])
        await self.json("DELETE", "/api/artwork-folders/missing", status=404)

    async def test_read_only_collections_keep_reads_and_reject_all_mutations(self):
        folder = (await self.json("POST", "/api/artwork-folders", {"name": "Folder"}, status=201))["folder"]["id"]
        self.history._protect_storage("test protection")
        await self.json("GET", "/api/artwork-folders")
        await self.json("GET", f"/api/history?folder_id={folder}")
        await self.json("POST", "/api/artwork-folders/members/query", {"image_ids": self.ids})
        for method, path, body in (
            ("POST", "/api/artwork-folders", {"name": "New"}),
            ("PATCH", f"/api/artwork-folders/{folder}", {"name": "Rename"}),
            ("DELETE", f"/api/artwork-folders/{folder}", None),
            ("POST", "/api/artwork-folders/reorder", {"folder_ids": [folder]}),
            ("POST", "/api/artwork-folders/members", {"image_ids": self.ids, "folder_ids": [folder], "action": "add"}),
        ):
            await self.json(method, path, body, status=503)

    async def test_membership_query_is_authoritative_read_only_and_reports_missing_images(self):
        folder = (await self.json("POST", "/api/artwork-folders", {"name": "Folder"}, status=201))["folder"]["id"]
        await self.json("POST", "/api/artwork-folders/members", {"image_ids": self.ids[:1], "folder_ids": [folder], "action": "add"})
        before = (await self.history._call(lambda: self.history.connection.total_changes))
        result = await self.json("POST", "/api/artwork-folders/members/query", {"image_ids": [self.ids[1], 9999, self.ids[0], self.ids[0]]})
        self.assertEqual(result["items"], [{"id": self.ids[1], "folder_ids": []}, {"id": self.ids[0], "folder_ids": [folder]}])
        self.assertEqual(result["missing_image_ids"], [9999])
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["folders"][0]["id"], folder)
        empty = await self.json("POST", "/api/artwork-folders/members/query", {"image_ids": []})
        self.assertEqual(empty["items"], [])
        self.assertEqual(empty["missing_image_ids"], [])
        self.assertEqual(await self.history._call(lambda: self.history.connection.total_changes), before)
        for body in ({}, {"image_ids": None}, {"image_ids": [True]}, {"image_ids": [0]},
                     {"image_ids": [2**63]}, {"image_ids": [1.0]}, {"image_ids": ["1"]},
                     {"image_ids": self.ids, "action": "remove"}):
            await self.json("POST", "/api/artwork-folders/members/query", body, status=400)

    async def test_single_record_delete_and_missing_record(self):
        payload = await self.json("DELETE", f"/api/history/{self.ids[0]}?mode=record")
        self.assertTrue(payload["deleted"])
        self.assertEqual(payload["deleted_count"], 1)
        self.assertEqual(payload["file_deleted_count"], 0)
        self.assertEqual((await self.history.list_images())["total"], 2)
        await self.json("DELETE", f"/api/history/{self.ids[0]}", status=404)

    async def test_single_delete_rejects_paths_and_invalid_modes(self):
        for query in ("mode=wipe", "path=C:/file.png", "mode=file&path=x"):
            await self.json("DELETE", f"/api/history/{self.ids[0]}?{query}", status=400)
        self.assertEqual((await self.history.list_images())["total"], 3)

    async def test_file_mode_requires_same_origin_direct_local_request(self):
        service = self.app[ARTWORK_DELETION_KEY]
        result = {"results": [{"id": self.ids[0], "record_deleted": False,
                               "file_deleted": False, "file_missing": False, "code": "file_shared",
                               "error": "其他作品仍引用原图"}], "deleted_count": 0, "failed_count": 1}
        path = f"/api/history/{self.ids[0]}?mode=file"
        with patch.object(service, "delete", new=AsyncMock(return_value=result)) as deletion:
            await self.json("DELETE", path, status=403)
            deletion.assert_not_awaited()
            origin = str(self.client.make_url("/")).rstrip("/")
            await self.json("DELETE", path,
                            headers={"Origin": origin, "X-Forwarded-For": "127.0.0.1"}, status=403)
            deletion.assert_not_awaited()
            returned = await self.json("DELETE", path, headers={"Origin": origin}, status=409)
            self.assertEqual(returned["results"][0]["code"], "file_shared")
            self.assertFalse(returned["deleted"])
            deletion.assert_awaited_once()
            self.assertEqual(deletion.await_args.args[:2], (self.ids[0], "file"))





if __name__ == "__main__":
    unittest.main()
