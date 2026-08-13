import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
import sys

from aiohttp.test_utils import TestClient, TestServer


APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

from anima_webui.server import COMFY_KEY, MANAGER_KEY, create_app  # noqa: E402
from anima_webui.workflow import DEFAULT_SETTINGS  # noqa: E402


# 测试必须密闭:池数据来自 tempdir 里的假 Anima Tools 目录,
# 不依赖本机 ComfyUI 安装,也不吸入真实词库(回归 #P1-5)。
FAKE_TOOLS_DATASETS = {
    "character_data.js": [
        {"name": "alpha", "copyright": "series", "gender": "1girl", "hair": "blue", "eye": "red", "post_count": 5},
    ],
    "clothing_data.js": [
        {"id": "coat", "name": "Coat", "name_zh": "外套", "tags": "red coat", "categories": ["日常/休闲 (Casual & Daily)"], "traits": ["red"]},
    ],
    "pose_data.js": [
        {"id": "stand", "name": "Standing", "name_zh": "站立", "tags": "standing", "categories": ["站立与动态 (Standing & Dynamic)"], "traits": ["standing"]},
        {"id": "sit", "name": "Sitting", "name_zh": "坐姿", "tags": "sitting", "categories": ["坐姿 (Sitting Poses)"], "traits": ["sitting"]},
    ],
    "background_data.js": [
        {"id": "room", "name": "Room", "name_zh": "房间", "tags": "indoors", "categories": ["都市与日常 (Urban & Daily)"], "traits": ["indoor"]},
    ],
}


def _write_fake_tools(root: Path) -> Path:
    js = root / "tools" / "js"
    js.mkdir(parents=True)
    for filename, values in FAKE_TOOLS_DATASETS.items():
        (js / filename).write_text(
            f"const data = {json.dumps(values, ensure_ascii=False)};\n", encoding="utf-8"
        )
    return root / "tools"


class FakeComfy:
    base_url = "http://127.0.0.1:8188"

    def __init__(self):
        self.preview_items = []
        self.submissions = []
        self.favorites_data = {
            section: {"groups": [{"id": "default", "name": "Default Favorites", "isSystem": True}], "items": []}
            for section in ("artist", "character", "lora", "clothing", "background", "pose", "expression")
        }

    async def status(self):
        return {"system": {"comfyui_version": "test"}, "devices": [{"name": "GPU"}]}

    async def lora_inventory(self):
        if self.preview_items:
            return {"items": copy.deepcopy(self.preview_items), "count": len(self.preview_items)}
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

    async def lora_preview(self, filename):
        item = next((value for value in self.preview_items if value["filename"] == filename), None)
        if not item:
            raise KeyError(filename)
        return b"preview", "image/webp", "private, max-age=60"

    async def lora_filenames(self):
        return [item["filename"] for item in DEFAULT_SETTINGS["loras"]]

    async def resource_inventory(self):
        return {
            "models": [DEFAULT_SETTINGS["model_name"]],
            "upscale_models": [DEFAULT_SETTINGS["hires"]["model_name"]],
            "samplers": [DEFAULT_SETTINGS["sampler_name"], "euler"],
            "schedulers": [DEFAULT_SETTINGS["scheduler"], "karras"],
        }

    async def favorites(self):
        return copy.deepcopy(self.favorites_data)

    async def save_favorites(self, payload):
        for section, value in payload.items():
            self.favorites_data[section] = copy.deepcopy(value)
        return {"success": True}

    async def submit(self, payload):
        self.submissions.append(copy.deepcopy(payload))
        return "prompt-id"

    async def wait_for_history(self, prompt_id, should_abort=None, missing_timeout=30.0):
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
        self._saved_env = {
            name: os.environ.pop(name, None)
            for name in ("ANIMA_TOOLS_DIR", "COMFYUI_ANIMA_TOOLS")
        }
        self.comfy = FakeComfy()
        app = create_app(
            app_dir=APP_DIR,
            comfy=self.comfy,
            history_path=Path(self.temp.name) / "history.sqlite3",
            custom_prompts_path=Path(self.temp.name) / "custom_prompts.json",
            style_presets_path=Path(self.temp.name) / "style_presets.json",
            lora_trigger_overrides_path=Path(self.temp.name) / "lora_trigger_overrides.json",
            prompt_replacements_path=Path(self.temp.name) / "prompt_replacements.json",
            anima_tools_dir=_write_fake_tools(Path(self.temp.name)),
        )
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()
        for name, value in self._saved_env.items():
            if value is not None:
                os.environ[name] = value

    async def test_index_and_status(self):
        response = await self.client.get("/")
        self.assertEqual(response.status, 200)
        index_html = await response.text()
        self.assertIn("Anima", index_html)
        self.assertIn(
            'id="hires_percent" type="number" min="1" max="1000" step="1" value="45" required',
            index_html,
        )
        self.assertIn("控制放大模型输出的最终缩放比例", index_html)
        self.assertEqual(
            response.headers["Cache-Control"], "no-cache, max-age=0, must-revalidate"
        )
        static_response = await self.client.get("/static/app.js")
        app_js = await static_response.text()
        self.assertIn('"hires_percent"', app_js)
        self.assertIn("percent: Number(ui.hires_percent.value)", app_js)
        self.assertIn("function reconcileLoraTriggers", app_js)
        self.assertIn("lora_managed_triggers", app_js)
        self.assertIn("lora-catalog-preview", index_html + app_js)
        self.assertIn('id="loraTriggerDialog"', index_html)
        self.assertIn('id="promptRuleDialog"', index_html)
        self.assertIn('id="sampler_name"', index_html)
        self.assertIn('id="scheduler"', index_html)
        self.assertIn('sampler_name: ui.sampler_name.value', app_js)
        self.assertIn('scheduler: ui.scheduler.value', app_js)
        self.assertIn("normalizePromptFields", app_js)
        self.assertIn('id="deleteGroupAll"', index_html)
        self.assertIn('value="exclusive"', index_html)
        self.assertIn("deleteMode=${deleteMode}", app_js)
        self.assertIn('id="favoriteSelection"', index_html)
        self.assertIn('id="favoriteSelectionDialog"', index_html)
        self.assertIn("/api/favorites/${activeSection}/selection", app_js)
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

    async def test_lora_inventory_uses_same_origin_preview_proxy(self):
        self.comfy.preview_items = [
            {
                "filename": "风格\\one.safetensors",
                "normalized_path": "风格/one.safetensors",
                "folder": "风格",
                "basename": "one.safetensors",
                "display_name": "One",
                "preview": "/api/lm/previews?path=secret",
                "has_preview": True,
                "trigger_words": ["@one"],
                "trigger_metadata_available": True,
                "metadata_source": "lora-manager",
                "size": 123,
            }
        ]
        payload = await (await self.client.get("/api/loras")).json()
        item = payload["items"][0]
        self.assertEqual(item["trigger_words"], ["@one"])
        self.assertEqual(item["source_trigger_words"], ["@one"])
        self.assertFalse(item["trigger_override"])
        self.assertNotIn("secret", item["preview"])
        self.assertTrue(item["preview"].startswith("/api/loras/preview?filename="))

        preview = await self.client.get(item["preview"])
        self.assertEqual(preview.status, 200)
        self.assertEqual(await preview.read(), b"preview")
        self.assertEqual(preview.headers["Content-Type"], "image/webp")
        self.assertEqual(preview.headers["Cache-Control"], "private, max-age=60")

        invalid = await self.client.get("/api/loras/preview?filename=../one.safetensors")
        self.assertEqual(invalid.status, 404)

    async def test_lora_trigger_override_can_be_edited_cleared_and_reset(self):
        self.comfy.preview_items = [
            {
                "filename": "风格/one.safetensors",
                "display_name": "One",
                "preview": "",
                "trigger_words": ["@One", "Second"],
                "trigger_metadata_available": True,
            }
        ]
        updated = await self.client.put(
            "/api/loras/triggers",
            json={"filename": "风格/one.safetensors", "triggerWords": [" @Exact_One ", "@exact_one"]},
        )
        self.assertEqual(updated.status, 200)
        payload = await updated.json()
        self.assertEqual(payload["source_trigger_words"], ["@One", "Second"])
        self.assertEqual(payload["trigger_words"], ["@Exact_One"])
        self.assertTrue(payload["trigger_override"])

        cleared = await self.client.put(
            "/api/loras/triggers",
            json={"filename": "风格/one.safetensors", "triggerWords": []},
        )
        self.assertEqual((await cleared.json())["trigger_words"], [])
        listing = await (await self.client.get("/api/loras")).json()
        self.assertEqual(listing["items"][0]["trigger_words"], [])
        self.assertTrue(listing["items"][0]["trigger_override"])
        self.assertTrue(Path(self.temp.name, "lora_trigger_overrides.json").is_file())

        reset = await self.client.delete(
            "/api/loras/triggers?filename=%E9%A3%8E%E6%A0%BC%2Fone.safetensors"
        )
        self.assertEqual(reset.status, 200)
        reset_payload = await reset.json()
        self.assertEqual(reset_payload["trigger_words"], ["@One", "Second"])
        self.assertFalse(reset_payload["trigger_override"])

    async def test_prompt_normalization_and_rule_crud(self):
        normalized = await self.client.post(
            "/api/prompts/normalize",
            json={
                "fields": {
                    "quality_prompt": "2025，SCORE 9, Blue_Hair",
                    "extra_prompt": "@Niji9il, Blue_Hair",
                },
                "managedTriggers": ["@Niji9il"],
            },
        )
        self.assertEqual(normalized.status, 200)
        payload = await normalized.json()
        self.assertEqual(payload["fields"]["quality_prompt"], "year 2025, score_9, blue hair")
        self.assertEqual(payload["fields"]["extra_prompt"], "@Niji9il, blue hair")

        created = await self.client.post(
            "/api/prompt-rules",
            json={"from": "wrong_tag", "to": "Right_Tag", "scopes": ["positive"], "enabled": True},
        )
        self.assertEqual(created.status, 201)
        item = await created.json()
        custom = await self.client.post(
            "/api/prompts/normalize",
            json={"fields": {"extra_prompt": "WRONG_TAG"}},
        )
        self.assertEqual((await custom.json())["fields"]["extra_prompt"], "right tag")

        disabled = await self.client.put(
            "/api/prompt-rules/lowercase-tags", json={"enabled": False}
        )
        self.assertEqual(disabled.status, 200)
        listing = await (await self.client.get("/api/prompt-rules")).json()
        self.assertFalse(next(rule for rule in listing["items"] if rule["id"] == "lowercase-tags")["enabled"])
        deleted = await self.client.delete(f"/api/prompt-rules/{item['id']}")
        self.assertEqual(deleted.status, 200)
        self.assertTrue(Path(self.temp.name, "prompt_replacements.json").is_file())

    async def test_config_has_empty_default_loras(self):
        payload = await (await self.client.get("/api/config")).json()
        self.assertEqual(payload["defaults"]["loras"], [])

    async def test_resources_and_style_preset_crud(self):
        resources = await (await self.client.get("/api/resources")).json()
        self.assertEqual(resources["models"], [DEFAULT_SETTINGS["model_name"]])
        self.assertEqual(resources["samplers"], [DEFAULT_SETTINGS["sampler_name"], "euler"])
        self.assertEqual(resources["schedulers"], [DEFAULT_SETTINGS["scheduler"], "karras"])
        snapshot = {
            key: copy.deepcopy(DEFAULT_SETTINGS[key])
            for key in ("model_name", "loras", "lora_managed_triggers", "hires", "detailers", "manual_artist", "quality_prompt", "extra_prompt", "negative_prompt", "width", "height", "steps", "cfg", "sampler_name", "scheduler")
        }
        snapshot["quality_prompt"] = "2025, Blue_Hair"
        created = await self.client.post(
            "/api/style-presets",
            json={"name": "Soft", "favorite": False, "settings": snapshot},
        )
        self.assertEqual(created.status, 201)
        item = await created.json()
        self.assertEqual(item["settings"]["quality_prompt"], "year 2025, blue hair")
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
        self.assertEqual(self.client.server.app[COMFY_KEY].favorites_data["character"]["items"], [])

        deleted = await self.client.delete(f"/api/favorites/pose/groups/{group_id}")
        self.assertEqual(deleted.status, 200)
        payload = await deleted.json()
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["groupIds"], ["default"])

    async def test_batch_favorite_selection_api_appends_groups_and_preserves_nickname(self):
        pose = await (await self.client.get("/api/pools/pose?page=1&limit=2")).json()
        first_id, second_id = [item["id"] for item in pose["items"]]
        first_group = await self.client.post("/api/favorites/pose/groups", json={"name": "First"})
        first_group_id = (await first_group.json())["group"]["id"]
        second_group = await self.client.post("/api/favorites/pose/groups", json={"name": "Second"})
        second_group_id = (await second_group.json())["group"]["id"]
        saved = await self.client.put(
            "/api/favorites/pose/item",
            json={"id": first_id, "favorite": True, "groupIds": [first_group_id], "nickname": "memo"},
        )
        self.assertEqual(saved.status, 200)

        response = await self.client.post(
            "/api/favorites/pose/selection",
            json={
                "selection": {"mode": "include", "ids": [first_id, second_id, first_id], "excluded_ids": []},
                "groupIds": [second_group_id],
            },
        )
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["selectedCount"], 2)
        self.assertEqual(payload["createdCount"], 1)
        self.assertEqual(payload["updatedCount"], 1)
        by_id = {item["id"]: item for item in payload["items"]}
        first_key = first_id.split(":", 1)[-1]
        second_key = second_id.split(":", 1)[-1]
        self.assertEqual(by_id[first_key]["groupIds"], [first_group_id, second_group_id])
        self.assertEqual(by_id[first_key]["nickname"], "memo")
        self.assertEqual(by_id[second_key]["groupIds"], [second_group_id])

        all_response = await self.client.post(
            "/api/favorites/pose/selection",
            json={
                "selection": {"mode": "all", "ids": [], "excluded_ids": [second_id]},
                "groupIds": ["default"],
            },
        )
        self.assertEqual(all_response.status, 200)
        all_payload = await all_response.json()
        self.assertEqual(all_payload["selectedCount"], 1)
        self.assertEqual(all_payload["updatedCount"], 1)
        self.assertIn("default", next(item for item in all_payload["items"] if item["id"] == first_key)["groupIds"])

        invalid = await self.client.post(
            "/api/favorites/pose/selection",
            json={"selection": {"mode": "include", "ids": ["missing"]}, "groupIds": ["default"]},
        )
        self.assertEqual(invalid.status, 400)

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

    async def test_custom_group_delete_modes_and_legacy_query_compatibility(self):
        async def create_group(name):
            response = await self.client.post("/api/custom-groups/pose", json={"name": name})
            return (await response.json())["group"]

        first = await create_group("First")
        second = await create_group("Second")
        exclusive = await (
            await self.client.post(
                "/api/custom-prompts",
                json={"section": "pose", "title": "Exclusive", "prompt": "exclusive", "groupIds": [first["id"]]},
            )
        ).json()
        shared = await (
            await self.client.post(
                "/api/custom-prompts",
                json={"section": "pose", "title": "Shared", "prompt": "shared", "groupIds": [first["id"], second["id"]]},
            )
        ).json()

        deleted = await self.client.delete(
            f"/api/custom-groups/pose/{first['id']}?deleteMode=all&deleteItems=maybe"
        )
        self.assertEqual(deleted.status, 200)
        payload = await deleted.json()
        self.assertEqual(payload["deleteMode"], "all")
        self.assertEqual(set(payload["deletedItemIds"]), {exclusive["id"], shared["id"]})
        self.assertEqual(
            (await (await self.client.get("/api/custom-prompts?section=pose")).json())["items"],
            [],
        )

        legacy = await create_group("Legacy")
        legacy_item = await (
            await self.client.post(
                "/api/custom-prompts",
                json={"section": "pose", "title": "Legacy", "prompt": "legacy", "groupIds": [legacy["id"]]},
            )
        ).json()
        legacy_deleted = await self.client.delete(
            f"/api/custom-groups/pose/{legacy['id']}?deleteItems=true"
        )
        self.assertEqual(legacy_deleted.status, 200)
        legacy_payload = await legacy_deleted.json()
        self.assertEqual(legacy_payload["deleteMode"], "exclusive")
        self.assertEqual(legacy_payload["deletedItemIds"], [legacy_item["id"]])

        invalid = await create_group("Invalid")
        invalid_response = await self.client.delete(
            f"/api/custom-groups/pose/{invalid['id']}?deleteMode=invalid"
        )
        self.assertEqual(invalid_response.status, 400)
        self.assertIn("deleteMode", (await invalid_response.json())["error"])

    async def test_artist_favorites_normalize_prefix_and_preserve_other_sections(self):
        response = await self.client.put(
            "/api/favorites/artist/item",
            json={"name": "@rella", "favorite": True},
        )
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["items"][0]["name"], "rella")
        self.assertEqual(payload["items"][0]["groupIds"], ["default"])
        self.assertEqual(self.client.server.app[COMFY_KEY].favorites_data["pose"]["items"], [])

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
            json={"format": "json", "section": "expression", "bundleName": "Expression Pack", "content": '{"items":[{"section":"expression","title":"Calm","prompt":"calm face","groups":["Mood"]}]}'},
        )
        self.assertEqual(preview_response.status, 200)
        preview = await preview_response.json()
        self.assertEqual(preview["summary"]["new"], 1)
        committed = await self.client.post("/api/custom-prompts/import", json={
            "rows": preview["rows"], "section": "expression", "targetGroupIds": [target_group_id],
            "bundleName": "Expression Pack",
        })
        self.assertEqual(committed.status, 200)
        committed_payload = await committed.json()
        self.assertEqual(committed_payload["imported"], 1)
        pool = await (await self.client.get("/api/pools/expression?q=Calm")).json()
        self.assertEqual(pool["items"][0]["title"], "Calm")
        groups = (await (await self.client.get("/api/custom-groups/expression")).json())["groups"]
        bundle = next(item for item in groups if item["id"] == committed_payload["bundleId"])
        children = [item for item in groups if item.get("parentId") == bundle["id"]]
        self.assertEqual(bundle["count"], 1)
        self.assertEqual([item["name"] for item in children], ["Mood"])
        self.assertEqual(next(item for item in groups if item["id"] == target_group_id)["count"], 1)
        parent_pool = await (
            await self.client.get(f"/api/pools/expression?custom_group={bundle['id']}")
        ).json()
        self.assertEqual([item["title"] for item in parent_pool["items"]], ["Calm"])

        mixed_preview = await self.client.post(
            "/api/custom-prompts/import/preview",
            json={"format": "json", "section": "expression", "bundleName": "Expression Pack", "content": '{"items":[{"section":"pose","title":"Wave","prompt":"waving"}]}'},
        )
        self.assertEqual(mixed_preview.status, 200)
        self.assertEqual((await mixed_preview.json())["summary"]["error"], 1)

        invalid_target = await self.client.post("/api/custom-prompts/import", json={
            "rows": [{"action": "create", "item": {"section": "expression", "title": "Unsafe", "prompt": "unsafe"}}],
            "section": "expression", "targetGroupIds": ["custom_group_missing"], "bundleName": "Unsafe Pack",
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

        for percent in (0, 1001, "", 60.5):
            with self.subTest(percent=percent):
                invalid_hires = {**DEFAULT_SETTINGS["hires"], "percent": percent}
                response = await self.client.post(
                    "/api/batches", json={**DEFAULT_SETTINGS, "hires": invalid_hires}
                )
                self.assertEqual(response.status, 400)
                self.assertIn("hires.percent", (await response.json())["error"])

        for field, value in (("sampler_name", ""), ("sampler_name", 1), ("scheduler", ""), ("scheduler", False)):
            with self.subTest(field=field, value=value):
                response = await self.client.post(
                    "/api/batches", json={**DEFAULT_SETTINGS, field: value}
                )
                self.assertEqual(response.status, 400)
                self.assertIn(field, (await response.json())["error"])

        for field, value in (("sampler_name", "missing"), ("scheduler", "missing")):
            with self.subTest(field=field):
                response = await self.client.post(
                    "/api/batches", json={**DEFAULT_SETTINGS, field: value}
                )
                self.assertEqual(response.status, 400)
                self.assertIn("不可用", (await response.json())["error"])

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
        manager = self.client.server.app[MANAGER_KEY]
        await manager.wait()
        history = await (await self.client.get("/api/history")).json()
        saved = history["items"][0]["settings"]
        self.assertEqual(saved["random_character_count"], 2)
        self.assertEqual(saved["fixed_character"], "hero")
        self.assertEqual(saved["random_clothing_count"], 5)

    async def test_batch_submission_normalizes_settings_and_resolved_prompt(self):
        settings = {
            **DEFAULT_SETTINGS,
            "count": 1,
            "quality_prompt": "2025，SCORE 9, Blue_Hair",
            "extra_prompt": "@Niji9il, Blue_Hair",
            "lora_managed_triggers": ["@Niji9il"],
        }
        response = await self.client.post("/api/batches", json=settings)
        self.assertEqual(response.status, 201)
        state = await response.json()
        self.assertEqual(
            state["settings"]["quality_prompt"], "year 2025, score_9, blue hair"
        )
        self.assertEqual(state["settings"]["extra_prompt"], "@Niji9il, blue hair")
        await self.client.server.app[MANAGER_KEY].wait()
        submitted = self.comfy.submissions[-1]["prompt"]
        self.assertEqual(
            submitted["42"]["inputs"]["quality_prompt"],
            "year 2025, score_9, blue hair",
        )
        self.assertEqual(
            submitted["60"]["inputs"]["resolved_prompt"],
            "1girl, @Niji9il, blue hair",
        )

    async def test_batch_completes_and_history_is_paged(self):
        response = await self.client.post("/api/batches", json={**DEFAULT_SETTINGS, "count": 1})
        self.assertEqual(response.status, 201)
        manager = self.client.server.app[MANAGER_KEY]
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

    async def test_non_numeric_image_id_returns_404_not_500(self):
        # 回归 #P2-1:此前 int() 裸抛 ValueError 变成 500。
        self.assertEqual((await self.client.get("/api/images/abc")).status, 404)
        self.assertEqual((await self.client.delete("/api/history/abc")).status, 404)

    async def test_pool_query_endpoint_filters_and_selection(self):
        # 此前 POST /api/pools/{section}/query 零测试覆盖(#P2-13)。
        payload = await (
            await self.client.post(
                "/api/pools/pose/query",
                json={"page": 1, "limit": 48, "q": "Standing"},
            )
        ).json()
        self.assertEqual([item["id"] for item in payload["items"]], ["pose:stand"])
        excluded = await (
            await self.client.post(
                "/api/pools/pose/query",
                json={
                    "page": 1,
                    "limit": 48,
                    "selection": {"mode": "include", "ids": ["pose:sit"], "excluded_ids": []},
                },
            )
        ).json()
        self.assertEqual([item["id"] for item in excluded["items"]], ["pose:sit"])
        bad = await self.client.post("/api/pools/pose/query", json={"page": "abc"})
        self.assertEqual(bad.status, 400)

    async def test_non_local_host_or_origin_is_rejected(self):
        # 回归 #P2-2:恶意网页的 CSRF/DNS 重绑定应被本机来源校验拦截。
        spoofed_host = await self.client.get("/api/config", headers={"Host": "evil.example:8190"})
        self.assertEqual(spoofed_host.status, 403)
        evil_origin = await self.client.post(
            "/api/style-presets", json={}, headers={"Origin": "http://evil.example"}
        )
        self.assertEqual(evil_origin.status, 403)
        null_origin = await self.client.post("/api/style-presets", json={}, headers={"Origin": "null"})
        self.assertEqual(null_origin.status, 403)
        local_origin = await self.client.get(
            "/api/config", headers={"Origin": f"http://127.0.0.1:{self.client.port}"}
        )
        self.assertEqual(local_origin.status, 200)

    async def test_explicit_trusted_proxy_host_and_origin_are_allowed(self):
        trusted_host = "anima.165-99-43-225.sslip.io"
        trusted_app = create_app(
            app_dir=APP_DIR,
            comfy=FakeComfy(),
            history_path=Path(self.temp.name) / "trusted-history.sqlite3",
            custom_prompts_path=Path(self.temp.name) / "trusted-custom-prompts.json",
            style_presets_path=Path(self.temp.name) / "trusted-style-presets.json",
            anima_tools_dir=Path(self.temp.name) / "tools",
            trusted_hostnames={trusted_host},
        )
        trusted_client = TestClient(TestServer(trusted_app))
        await trusted_client.start_server()
        try:
            trusted = await trusted_client.get(
                "/api/config",
                headers={
                    "Host": trusted_host,
                    "Origin": f"https://{trusted_host}",
                },
            )
            self.assertEqual(trusted.status, 200)
            evil = await trusted_client.get(
                "/api/config",
                headers={"Host": "evil.example", "Origin": "https://evil.example"},
            )
            self.assertEqual(evil.status, 403)
        finally:
            await trusted_client.close()

    async def test_config_exposes_startup_warnings(self):
        payload = await (await self.client.get("/api/config")).json()
        self.assertEqual(payload["warnings"], [])

    async def test_current_batch_includes_queue_and_preview_endpoint(self):
        # 0.2.0:current 返回队列;预览端点无帧时 204,有帧时回图。
        current = await (await self.client.get("/api/batches/current")).json()
        self.assertEqual(current["queue"], [])
        self.assertEqual((await self.client.get("/api/batches/current/preview")).status, 204)
        manager = self.client.server.app[MANAGER_KEY]
        manager.preview = (1, "image/jpeg", b"frame")
        preview = await self.client.get("/api/batches/current/preview")
        self.assertEqual(preview.status, 200)
        self.assertEqual(await preview.read(), b"frame")
        self.assertEqual(preview.headers["Content-Type"], "image/jpeg")
        missing = await self.client.delete("/api/batches/queue/queue_missing")
        self.assertEqual(missing.status, 404)

    async def test_batch_with_seeds_reproduces_exact_image(self):
        # 0.2.0:复现请求携带 seeds,历史记录里的种子必须一字不差。
        response = await self.client.post(
            "/api/batches",
            json={**DEFAULT_SETTINGS, "count": 1, "seeds": {"sample_seed": 987654321, "prompt_seed": 24680}},
        )
        self.assertEqual(response.status, 201)
        await self.client.server.app[MANAGER_KEY].wait()
        history = await (await self.client.get("/api/history")).json()
        self.assertEqual(history["items"][0]["sample_seed"], 987654321)
        self.assertEqual(history["items"][0]["prompt_seed"], 24680)
        bad = await self.client.post(
            "/api/batches",
            json={**DEFAULT_SETTINGS, "count": 1, "seeds": {"sample_seed": -5, "prompt_seed": 1}},
        )
        self.assertEqual(bad.status, 400)


if __name__ == "__main__":
    unittest.main()
