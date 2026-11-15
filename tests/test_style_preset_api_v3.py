import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

from anima_webui.server import STYLE_PRESETS_KEY, STYLE_PRESET_COVERS_KEY, create_app
from anima_webui.workflow import DEFAULT_SETTINGS
from test_server import APP_DIR, FakeComfy, _write_fake_tools
from test_style_preset_covers import picture


class StylePresetApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.comfy = FakeComfy()
        self.app = create_app(app_dir=APP_DIR, data_dir=self.root / "data", comfy=self.comfy,
                              anima_tools_dir=_write_fake_tools(self.root))
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def upload(self, *, crop=None, body=None):
        form = FormData()
        form.add_field("image", body or picture(), filename="cover.png", content_type="image/png")
        if crop is not None:
            form.add_field("crop", json.dumps(crop), content_type="application/json")
        return await self.client.post("/api/style-preset-covers", data=form)

    async def create_preset(self, **updates):
        payload = {"name": "Soft light", "model_profile": "anima", "settings": copy.deepcopy(DEFAULT_SETTINGS), **updates}
        response = await self.client.post("/api/style-presets", json=payload)
        self.assertEqual(response.status, 201, await response.text())
        return await response.json()

    async def test_cover_upload_attach_replace_and_restart_read(self):
        response = await self.upload(crop={"x": 0, "y": 0, "width": 1, "height": 1})
        self.assertEqual(response.status, 201, await response.text())
        cover = await response.json()
        item = await self.create_preset(cover_id=cover["id"])
        self.assertEqual(item["cover_url"], cover["url"])
        response = await self.client.get(cover["url"])
        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "image/webp")
        self.assertEqual(await response.read(), await self.app[STYLE_PRESET_COVERS_KEY].read(cover["id"]))
        listing = await (await self.client.get("/api/style-presets?model_profile=anima")).json()
        self.assertEqual(listing["items"][0]["cover_url"], cover["url"])
        stored = json.loads((self.root / "data" / "style_presets.json").read_text("utf-8"))
        self.assertEqual(stored["items"][0]["cover_id"], cover["id"])
        self.assertNotIn("cover_url", stored["items"][0])

        response = await self.client.put(f"/api/style-presets/{item['id']}", json={"cover_id": "0" * 64})
        self.assertEqual(response.status, 400)
        self.assertEqual(self.app[STYLE_PRESETS_KEY].get(item["id"])["cover_id"], cover["id"])
        response = await self.client.put(f"/api/style-presets/{item['id']}", json={"cover_id": None})
        self.assertEqual(response.status, 200)
        self.assertIsNone((await response.json())["cover_url"])
        self.assertEqual((await self.client.get(cover["url"])).status, 200)

    async def test_resolve_preserves_current_and_never_starts_generation(self):
        settings = copy.deepcopy(DEFAULT_SETTINGS)
        settings.update(workbench="random", generation_mode="txt2img", width=768, height=768, steps=27)
        settings["fixed_pose"] = "must not be saved"
        item = await self.create_preset(settings=settings)
        current = copy.deepcopy(DEFAULT_SETTINGS)
        current.update(workbench="random", generation_mode="txt2img", width=1024, height=1024)
        current["fixed_pose"] = "my current prompt"
        current["dual"]["half_precision"] = False
        response = await self.client.post(f"/api/style-presets/{item['id']}/resolve",
                                          json={"settings": current, "model_profile": "anima"})
        self.assertEqual(response.status, 200, await response.text())
        resolved = await response.json()
        self.assertEqual(resolved["settings"]["fixed_pose"], "my current prompt")
        self.assertFalse(resolved["settings"]["dual"]["half_precision"])
        self.assertEqual(resolved["settings"]["width"], 1024)
        self.assertEqual(resolved["settings"]["steps"], 27)
        self.assertEqual(self.comfy.submissions, [])
        self.assertEqual(self.app[STYLE_PRESETS_KEY].get(item["id"]), {k: v for k, v in item.items() if k != "cover_url"})

    async def test_groups_batch_tags_and_profile_isolation(self):
        first = await self.create_preset()
        second = await self.create_preset(name="Line art")
        response = await self.client.post("/api/style-preset-groups", json={"name": "Portrait", "model_profile": "anima"})
        self.assertEqual(response.status, 201, await response.text())
        group = await response.json()
        response = await self.client.put(f"/api/style-preset-groups/{group['id']}",
                                        json={"name": "People", "model_profile": "anima"})
        self.assertEqual(response.status, 200, await response.text())
        ids = [first["id"], second["id"]]
        for action, value in (("groups", {"mode": "add", "values": [group["id"]]}),
                              ("tags", {"mode": "add", "values": ["Portrait", "Ink"]})):
            response = await self.client.post("/api/style-presets/batch",
                                              json={"model_profile": "anima", "ids": ids, "action": action, "value": value})
            self.assertEqual(response.status, 200, await response.text())
        listing = await (await self.client.get("/api/style-presets?model_profile=anima")).json()
        self.assertEqual(listing["groups"][0]["name"], "People")
        self.assertTrue(all(item["favorite"] and item["groups"] == [group["id"]] for item in listing["items"]))
        self.assertTrue(all(len(item["tags"]) == 2 for item in listing["items"]))
        response = await self.client.delete(f"/api/style-presets/{first['id']}?model_profile=krea2")
        self.assertEqual(response.status, 400)
        self.assertEqual(self.app[STYLE_PRESETS_KEY].list()["count"], 2)
        response = await self.client.delete(f"/api/style-preset-groups/{group['id']}?model_profile=anima")
        self.assertEqual(response.status, 200, await response.text())
        self.assertTrue(all(not item["groups"] and item["favorite"] for item in self.app[STYLE_PRESETS_KEY].items))

    async def test_favorite_times_and_order_match_put_batch_get_and_reload(self):
        store = self.app[STYLE_PRESETS_KEY]
        timestamps = [f"2026-11-{day:02d}T00:00:00+00:00" for day in range(1, 8)]
        items = []
        for index, name in enumerate(("Oldest", "Middle", "Newest")):
            with patch.object(store, "_now", return_value=timestamps[index]):
                items.append(await self.create_preset(name=name))
        first, second, third = items
        with patch.object(store, "_now", return_value=timestamps[3]):
            response = await self.client.put(f"/api/style-presets/{first['id']}", json={
                "model_profile": "anima", "favorite": True, "favorite_at": "forged", "updated_at": "forged",
                "name": first["name"], "groups": first["groups"], "tags": first["tags"], "cover_id": first["cover_id"],
            })
        self.assertEqual(response.status, 200, await response.text())
        saved = await response.json()
        self.assertEqual(saved["favorite_at"], timestamps[3])
        self.assertEqual(saved["updated_at"], first["updated_at"])
        batch = {"model_profile": "anima", "ids": [second["id"]], "action": "favorite", "value": True}
        with patch.object(store, "_now", return_value=timestamps[4]):
            response = await self.client.post("/api/style-presets/batch", json=batch)
        self.assertEqual(response.status, 200, await response.text())
        listing = await response.json()
        self.assertEqual([item["id"] for item in listing["items"]], [second["id"], first["id"], third["id"]])
        self.assertEqual(listing["items"][0]["favorite_at"], timestamps[4])
        self.assertEqual(listing["items"][0]["updated_at"], second["updated_at"])
        self.assertEqual(await (await self.client.get("/api/style-presets?model_profile=anima")).json(), listing)
        with patch.object(store, "_now", return_value=timestamps[5]):
            response = await self.client.put(f"/api/style-presets/{first['id']}", json={"favorite": False})
        self.assertEqual(response.status, 200)
        self.assertIsNone((await response.json())["favorite_at"])
        with patch.object(store, "_now", return_value=timestamps[6]):
            response = await self.client.post("/api/style-presets/batch", json={**batch, "value": False})
        self.assertEqual(response.status, 200)
        listing = await response.json()
        self.assertEqual([item["id"] for item in listing["items"]], [third["id"], second["id"], first["id"]])
        self.assertTrue(all(item["favorite_at"] is None for item in listing["items"]))
        for original in items:
            saved = next(item for item in listing["items"] if item["id"] == original["id"])
            self.assertEqual(saved["updated_at"], original["updated_at"])
        store.reload()
        self.assertEqual(await (await self.client.get("/api/style-presets?model_profile=anima")).json(), listing)

    async def test_failed_cover_reference_write_keeps_original(self):
        cover = await (await self.upload()).json()
        item = await self.create_preset(cover_id=cover["id"])
        replacement = await (await self.upload(body=picture(color="blue"))).json()
        with patch.object(self.app[STYLE_PRESETS_KEY], "_write_text", side_effect=OSError("disk full")):
            response = await self.client.put(f"/api/style-presets/{item['id']}", json={"cover_id": replacement["id"]})
        self.assertEqual(response.status, 500)
        self.assertEqual(self.app[STYLE_PRESETS_KEY].get(item["id"])["cover_id"], cover["id"])
        self.assertEqual((await self.client.get(cover["url"])).status, 200)

    async def test_upload_fields_limits_and_same_origin_protection(self):
        invalid = await self.upload(crop={"x": 2, "y": 0, "width": 1, "height": 1})
        self.assertEqual(invalid.status, 400)
        form = FormData()
        for _ in range(2):
            form.add_field("image", picture(), filename="cover.png")
        self.assertEqual((await self.client.post("/api/style-preset-covers", data=form)).status, 400)
        form = FormData()
        form.add_field("image", picture(), filename="cover.png")
        form.add_field("crop", "x" * 4097)
        self.assertEqual((await self.client.post("/api/style-preset-covers", data=form)).status, 413)
        form = FormData()
        form.add_field("image", picture(), filename="cover.png")
        response = await self.client.post("/api/style-preset-covers", data=form, headers={"Origin": "http://evil.example"})
        self.assertEqual(response.status, 403)
        self.assertEqual(list((self.root / "data" / "style-preset-covers").iterdir()), [])
