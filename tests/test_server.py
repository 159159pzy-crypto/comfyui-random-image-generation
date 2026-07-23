import copy
import json
import tempfile
import unittest
from pathlib import Path
import sys

from aiohttp.test_utils import TestClient, TestServer


APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

from anima_webui.server import create_app  # noqa: E402
from anima_webui.workflow import DEFAULT_SETTINGS  # noqa: E402


class FakeComfy:
    base_url = "http://127.0.0.1:8188"

    def __init__(self):
        self.favorites_data = {
            section: {"groups": [{"id": "default", "name": "Default Favorites", "isSystem": True}], "items": []}
            for section in ("artist", "character", "lora", "clothing", "background", "pose", "expression")
        }

    async def status(self):
        return {"system": {"comfyui_version": "test"}, "devices": [{"name": "GPU"}]}

    async def lora_inventory(self):
        items = [
            {
                "filename": item["filename"],
                "display_name": item["filename"].removesuffix(".safetensors"),
                "preview": "",
                "has_preview": False,
                "size": None,
            }
            for item in DEFAULT_SETTINGS["loras"]
        ]
        return {"items": items, "count": len(items)}

    async def lora_filenames(self):
        return [item["filename"] for item in DEFAULT_SETTINGS["loras"]]

    async def resource_inventory(self):
        return {
            "models": [DEFAULT_SETTINGS["model_name"]],
            "upscale_models": [DEFAULT_SETTINGS["hires"]["model_name"]],
        }

    async def favorites(self):
        return copy.deepcopy(self.favorites_data)

    async def save_favorites(self, payload):
        for section, value in payload.items():
            self.favorites_data[section] = copy.deepcopy(value)
        return {"success": True}

    async def submit(self, payload):
        return "prompt-id"

    async def wait_for_history(self, prompt_id):
        return {
            "outputs": {"12": {"images": [{"filename": "test.png", "subfolder": "", "type": "output"}]}},
            "prompt": [0, prompt_id, {}, {"extra_pnginfo": {"anima_prompt": {"42": {"positive": "generated"}}}}],
        }

    async def image_bytes(self, image):
        return b"png", "image/png"

    async def close(self):
        return None


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        app = create_app(
            app_dir=APP_DIR,
            comfy=FakeComfy(),
            history_path=Path(self.temp.name) / "history.sqlite3",
            custom_prompts_path=Path(self.temp.name) / "custom_prompts.json",
            style_presets_path=Path(self.temp.name) / "style_presets.json",
        )
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def test_index_and_status(self):
        response = await self.client.get("/")
        self.assertEqual(response.status, 200)
        self.assertIn("Anima", await response.text())
        self.assertEqual(
            response.headers["Cache-Control"], "no-cache, max-age=0, must-revalidate"
        )
        static_response = await self.client.get("/static/app.js")
        self.assertEqual(
            static_response.headers["Cache-Control"],
            "no-cache, max-age=0, must-revalidate",
        )
        status = await (await self.client.get("/api/status")).json()
        self.assertTrue(status["online"])
        self.assertEqual(status["device"], "GPU")

    async def test_lora_inventory(self):
        response = await self.client.get("/api/loras")
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["count"], 0)
        self.assertEqual(
            [item["filename"] for item in payload["items"]],
            [item["filename"] for item in DEFAULT_SETTINGS["loras"]],
        )

    async def test_config_has_empty_default_loras(self):
        payload = await (await self.client.get("/api/config")).json()
        self.assertEqual(payload["defaults"]["loras"], [])

    async def test_resources_and_style_preset_crud(self):
        resources = await (await self.client.get("/api/resources")).json()
        self.assertEqual(resources["models"], [DEFAULT_SETTINGS["model_name"]])
        snapshot = {
            key: copy.deepcopy(DEFAULT_SETTINGS[key])
            for key in ("model_name", "loras", "hires", "detailers", "manual_artist", "quality_prompt", "extra_prompt", "negative_prompt", "width", "height", "steps", "cfg")
        }
        created = await self.client.post(
            "/api/style-presets",
            json={"name": "Soft", "favorite": False, "settings": snapshot},
        )
        self.assertEqual(created.status, 201)
        item = await created.json()
        updated = await self.client.put(
            f"/api/style-presets/{item['id']}", json={"favorite": True, "name": "Soft Light"}
        )
        self.assertEqual(updated.status, 200)
        self.assertTrue((await updated.json())["favorite"])
        listing = await (await self.client.get("/api/style-presets")).json()
        self.assertEqual(listing["items"][0]["name"], "Soft Light")
        self.assertTrue(Path(self.temp.name, "style_presets.json").is_file())
        deleted = await self.client.delete(f"/api/style-presets/{item['id']}")
        self.assertEqual(deleted.status, 200)
        self.assertEqual((await (await self.client.get("/api/style-presets")).json())["count"], 0)

    async def test_favorite_crud_preserves_other_sections(self):
        pool = await (await self.client.get("/api/pools/pose?page=1&limit=1")).json()
        item_id = pool["items"][0]["id"]
        created = await self.client.post("/api/favorites/pose/groups", json={"name": "Test Group"})
        self.assertEqual(created.status, 201)
        group_id = (await created.json())["group"]["id"]
        saved = await self.client.put(
            "/api/favorites/pose/item",
            json={"id": item_id, "favorite": True, "groupIds": [group_id], "nickname": "memo"},
        )
        self.assertEqual(saved.status, 200)
        payload = await saved.json()
        self.assertEqual(payload["items"][0]["nickname"], "memo")
        self.assertIn(group_id, payload["items"][0]["groupIds"])
        self.assertEqual(self.client.server.app["comfy"].favorites_data["character"]["items"], [])

        deleted = await self.client.delete(f"/api/favorites/pose/groups/{group_id}")
        self.assertEqual(deleted.status, 200)
        payload = await deleted.json()
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["groupIds"], ["default"])

    async def test_favorite_child_import_parent_filter_and_safe_delete_api(self):
        source = await self.client.post("/api/custom-groups/pose", json={"name": "Snapshot"})
        source_id = (await source.json())["group"]["id"]
        custom = await self.client.post(
            "/api/custom-prompts",
            json={
                "section": "pose",
                "title": "Snapshot Pose",
                "prompt": "snapshot pose",
                "groupIds": [source_id],
            },
        )
        custom_id = (await custom.json())["id"]
        parent = await self.client.post("/api/favorites/pose/groups", json={"name": "Parent"})
        parent_id = (await parent.json())["group"]["id"]

        imported = await self.client.post(
            f"/api/favorites/pose/groups/{parent_id}/children/import",
            json={"customGroupId": source_id},
        )
        self.assertEqual(imported.status, 201)
        imported_payload = await imported.json()
        child = imported_payload["group"]
        self.assertEqual(child["parentId"], parent_id)
        self.assertEqual(child["sourceCustomGroupId"], source_id)
        child_with_stats = next(
            group for group in imported_payload["groups"] if group["id"] == child["id"]
        )
        self.assertEqual(child_with_stats["directCount"], 1)

        duplicate = await self.client.post(
            f"/api/favorites/pose/groups/{parent_id}/children/import",
            json={"customGroupId": source_id},
        )
        self.assertEqual(duplicate.status, 400)
        filtered = await (
            await self.client.get(
                f"/api/pools/pose?collection={parent_id}&q=Snapshot%20Pose"
            )
        ).json()
        self.assertEqual([item["id"] for item in filtered["items"]], [custom_id])

        invalid_bool = await self.client.delete(
            f"/api/favorites/pose/groups/{child['id']}?deleteItems=maybe"
        )
        self.assertEqual(invalid_bool.status, 400)
        deleted = await self.client.delete(f"/api/favorites/pose/groups/{parent_id}")
        self.assertEqual(deleted.status, 200)
        payload = await deleted.json()
        self.assertEqual(payload["deletedGroupCount"], 2)
        favorite = next(item for item in payload["items"] if item["id"] == custom_id)
        self.assertEqual(favorite["groupIds"], ["default"])

    async def test_artist_favorites_normalize_prefix_and_preserve_other_sections(self):
        response = await self.client.put(
            "/api/favorites/artist/item",
            json={"name": "@rella", "favorite": True},
        )
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["items"][0]["name"], "rella")
        self.assertEqual(payload["items"][0]["groupIds"], ["default"])
        self.assertEqual(self.client.server.app["comfy"].favorites_data["pose"]["items"], [])

    async def test_custom_group_templates_and_import_flow(self):
        expected_titles = {
            "character": "示例角色",
            "clothing": "休闲连帽衫",
            "pose": "站立挥手",
            "background": "樱花街道",
            "expression": "期待",
        }
        for section, title in expected_titles.items():
            template = await self.client.get(f"/api/custom-prompts/templates/{section}/json")
            self.assertEqual(template.status, 200)
            self.assertIn(f"custom-prompts-{section}-template.json", template.headers["Content-Disposition"])
            item = json.loads(await template.text())["items"][0]
            self.assertEqual(item["section"], section)
            self.assertEqual(item["title"], title)
        csv_template = await self.client.get("/api/custom-prompts/templates/pose/csv")
        self.assertEqual(csv_template.status, 200)
        self.assertIn("section,title,prompt", (await csv_template.text()).lstrip("\ufeff"))

        group_response = await self.client.post("/api/custom-groups/expression", json={"name": "Mood"})
        self.assertEqual(group_response.status, 201)
        target_group_response = await self.client.post("/api/custom-groups/expression", json={"name": "Imported"})
        self.assertEqual(target_group_response.status, 201)
        target_group_id = (await target_group_response.json())["group"]["id"]
        preview_response = await self.client.post(
            "/api/custom-prompts/import/preview",
            json={"format": "json", "section": "expression", "content": '{"items":[{"section":"expression","title":"Calm","prompt":"calm face","groups":["Mood"]}]}'},
        )
        self.assertEqual(preview_response.status, 200)
        preview = await preview_response.json()
        self.assertEqual(preview["summary"]["new"], 1)
        committed = await self.client.post("/api/custom-prompts/import", json={
            "rows": preview["rows"], "section": "expression", "targetGroupIds": [target_group_id],
        })
        self.assertEqual(committed.status, 200)
        self.assertEqual((await committed.json())["imported"], 1)
        pool = await (await self.client.get("/api/pools/expression?q=Calm")).json()
        self.assertEqual(pool["items"][0]["title"], "Calm")
        group_counts = {item["name"]: item["count"] for item in (await (await self.client.get("/api/custom-groups/expression")).json())["groups"]}
        self.assertEqual(group_counts, {"Mood": 1, "Imported": 1})

        mixed_preview = await self.client.post(
            "/api/custom-prompts/import/preview",
            json={"format": "json", "section": "expression", "content": '{"items":[{"section":"pose","title":"Wave","prompt":"waving"}]}'},
        )
        self.assertEqual(mixed_preview.status, 200)
        self.assertEqual((await mixed_preview.json())["summary"]["error"], 1)

        invalid_target = await self.client.post("/api/custom-prompts/import", json={
            "rows": [{"action": "create", "item": {"section": "expression", "title": "Unsafe", "prompt": "unsafe"}}],
            "section": "expression", "targetGroupIds": ["custom_group_missing"],
        })
        self.assertEqual(invalid_target.status, 400)
        unsafe_pool = await (await self.client.get("/api/pools/expression?q=Unsafe")).json()
        self.assertEqual(unsafe_pool["total"], 0)

    async def test_missing_lora_returns_clear_error(self):
        settings = {
            **DEFAULT_SETTINGS,
            "loras": [{"filename": "missing.safetensors", "enabled": True, "strength": 1}],
        }
        response = await self.client.post("/api/batches", json=settings)
        self.assertEqual(response.status, 400)
        self.assertIn("missing.safetensors", (await response.json())["error"])

    async def test_invalid_settings_return_400(self):
        response = await self.client.post("/api/batches", json={**DEFAULT_SETTINGS, "count": 0})
        self.assertEqual(response.status, 400)
        self.assertIn("生成数量", (await response.json())["error"])

    async def test_new_dimension_settings_are_returned_and_persisted(self):
        settings = {
            **DEFAULT_SETTINGS,
            "random_character": False,
            "random_character_count": 2,
            "fixed_character": "hero",
            "random_clothing_count": 5,
        }
        response = await self.client.post("/api/batches", json=settings)
        self.assertEqual(response.status, 201)
        manager = self.client.server.app["manager"]
        await manager.wait()
        history = await (await self.client.get("/api/history")).json()
        saved = history["items"][0]["settings"]
        self.assertEqual(saved["random_character_count"], 2)
        self.assertEqual(saved["fixed_character"], "hero")
        self.assertEqual(saved["random_clothing_count"], 5)

    async def test_batch_completes_and_history_is_paged(self):
        response = await self.client.post("/api/batches", json={**DEFAULT_SETTINGS, "count": 1})
        self.assertEqual(response.status, 201)
        manager = self.client.server.app["manager"]
        await manager.wait()
        current = await (await self.client.get("/api/batches/current")).json()
        self.assertEqual(current["batch"]["status"], "completed")
        history = await (await self.client.get("/api/history?page=1&limit=10")).json()
        self.assertEqual(history["total"], 1)
        image_id = history["items"][0]["id"]
        image = await self.client.get(f"/api/images/{image_id}")
        self.assertEqual(await image.read(), b"png")
        deleted = await self.client.delete(f"/api/history/{image_id}")
        self.assertEqual(deleted.status, 200)
        self.assertEqual((await (await self.client.get("/api/history")).json())["total"], 0)

    async def test_missing_history_record_returns_404(self):
        self.assertEqual((await self.client.get("/api/images/999")).status, 404)


if __name__ == "__main__":
    unittest.main()
