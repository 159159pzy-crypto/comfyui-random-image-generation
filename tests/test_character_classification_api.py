"""HTTP and workflow acceptance for local custom-character classification."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from anima_webui.server import CUSTOM_PROMPTS_KEY, HISTORY_KEY, MANAGER_KEY, create_app
from anima_webui.workflow import DEFAULT_SETTINGS
from test_server import APP_DIR, FakeComfy, _write_fake_tools


class CharacterClassificationApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        tools = _write_fake_tools(self.root)
        (tools / "js" / "character_official_data.json").write_text(json.dumps({
            "alpha||series": {"trigger": "alpha, series", "tags": [
                "1girl", "blue eyes", "white shirt", "hair bow",
            ]},
        }), encoding="utf-8")
        self.comfy = FakeComfy()
        self.app = create_app(app_dir=APP_DIR, data_dir=self.root / "data",
                              comfy=self.comfy, anima_tools_dir=tools)
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.manager = self.app[MANAGER_KEY]
        self.store = self.app[CUSTOM_PROMPTS_KEY]
        self.history = self.app[HISTORY_KEY]

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def analyze(self, **overrides):
        response = await self.client.post("/api/custom-prompts/character-analysis", json={
            "title": "Imported hero", "prompt": "white shirt, series, Blue Eyes, alpha, hair bow",
            **overrides,
        })
        self.assertEqual(response.status, 200, await response.text())
        return await response.json()

    async def create(self, **overrides):
        response = await self.client.post("/api/custom-prompts", json={
            "section": "character", "title": "Imported hero",
            "prompt": "white shirt, series, Blue Eyes, alpha, hair bow", **overrides,
        })
        self.assertEqual(response.status, 201, await response.text())
        return await response.json()

    def settings(self, item, mode):
        settings = copy.deepcopy(DEFAULT_SETTINGS)
        settings.update(count=1, random_character=True, character_detail=mode,
                        random_pose=False, quality_prompt="", manual_artist="",
                        female_count=0, male_count=0)
        settings["pools"]["character"] = {"mode": "include", "ids": [item["id"]]}
        return settings

    async def test_analysis_is_read_only_and_preserves_position(self):
        before = self.store.path.read_bytes() if self.store.path.exists() else None
        result = await self.analyze()
        self.assertEqual(result["character_status"], "ready")
        self.assertEqual(result["trigger"], ["series", "alpha"])
        self.assertEqual(result["previews"]["trigger_tags"], "white shirt, series, Blue Eyes, alpha, hair bow")
        self.assertEqual(result["previews"]["trigger"], "series, alpha")
        self.assertEqual(result["previews"]["trigger_tags_no_clothing"], "series, Blue Eyes, alpha, hair bow")
        self.assertEqual(before, self.store.path.read_bytes() if self.store.path.exists() else None)
        self.assertEqual(self.store.items, [])
        self.assertEqual(self.comfy.submissions, [])
        self.store.read_only_reason = "test protection"
        self.assertEqual((await self.analyze())["character_status"], "ready")

    async def test_analysis_request_validation_and_manual_empty_trigger(self):
        for body in ({"prompt": "alpha", "reanalyze": "true"},
                     {"prompt": "alpha", "previous": []}, {"prompt": ["alpha"]}):
            response = await self.client.post("/api/custom-prompts/character-analysis", json=body)
            self.assertEqual(response.status, 400, await response.text())
        result = await self.analyze(prompt="whale girl, white shirt")
        self.assertEqual(result["character_status"], "needs_review")
        for part in result["character_parts"]:
            part.update(role="feature", source="manual")
        result = await self.analyze(prompt=result["prompt"], character_parts=result["character_parts"],
                                    character_no_trigger=True)
        self.assertEqual(result["character_status"], "ready")
        self.assertEqual(result["previews"]["trigger"], "")
        self.assertEqual(result["previews"]["trigger_tags_no_clothing"], "whale girl")

    async def test_pending_candidates_are_rejected_without_enqueuing(self):
        item = await self.create(prompt="unidentified token, white shirt")
        self.assertEqual(item["character_status"], "needs_review")
        for mode in ("trigger", "trigger_tags_no_clothing"):
            response = await self.client.post("/api/batches", json=self.settings(item, mode))
            self.assertEqual(response.status, 400, await response.text())
            body = await response.json()
            self.assertEqual(body["code"], "character_review_required")
            self.assertEqual(body["characters"][0]["id"], item["id"])
        self.assertEqual(self.comfy.submissions, [])

    async def test_modes_reach_workflow_and_history_and_preserved_role_ignores_current_source(self):
        item = await self.create()

        async def captured_history(prompt_id, should_abort=None, missing_timeout=30.0):
            payload = self.comfy.submissions[-1]
            return {"outputs": {"12": {"images": [{"filename": "classified.png", "type": "output"}]}},
                    "prompt": [0, prompt_id, payload["prompt"], payload["extra_data"]]}

        self.comfy.wait_for_history = captured_history
        for mode, expected in (
            ("trigger_tags", "white shirt, series, blue eyes, alpha, hair bow"),
            ("trigger", "series, alpha"),
            ("trigger_tags_no_clothing", "series, blue eyes, alpha, hair bow"),
        ):
            settings = self.settings(item, mode)
            settings["random_pose"] = True
            settings["pools"]["pose"] = {"mode": "include", "ids": ["pose:stand", "pose:sit"]}
            response = await self.client.post("/api/batches", json=settings)
            self.assertEqual(response.status, 201, await response.text())
            await self.manager.wait()
            self.assertEqual(self.manager.snapshot()["status"], "completed")
            submitted = self.comfy.submissions[-1]
            text = submitted["prompt"]["1010:995"]["inputs"]["text"]
            self.assertTrue(text.startswith(expected), text)
            self.assertNotIn("1026:1011", submitted["prompt"])
            image = (await self.history.list_images())["items"][0]
            self.assertEqual(image["positive_prompt"], text)
            self.assertEqual(image["resolved_selection"]["character"][0]["prompt"].lower(), expected)

        response = await self.client.put(f"/api/custom-prompts/{item['id']}", json={
            "prompt": "new unknown identity, white shirt",
        })
        self.assertEqual(response.status, 200, await response.text())
        self.assertEqual((await response.json())["character_status"], "needs_review")
        for operation in ({"mode": "replay"},
                          {"mode": "content_redraw", "count": 1, "sections": ["pose"]}):
            response = await self.client.post(f"/api/history/{image['id']}/regenerate", json=operation)
            self.assertEqual(response.status, 201, await response.text())
            await self.manager.wait()
            self.assertEqual(self.manager.snapshot()["status"], "completed")
            latest = (await self.history.list_images())["items"][0]
            self.assertEqual(latest["resolved_selection"]["character"], image["resolved_selection"]["character"])
