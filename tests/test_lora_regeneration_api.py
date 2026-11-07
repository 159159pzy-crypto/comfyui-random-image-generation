import copy
import json
import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from anima_webui.server import HISTORY_KEY, MANAGER_KEY, create_app
from anima_webui.workflow import DEFAULT_SETTINGS
from test_server import APP_DIR, FakeComfy, _write_fake_tools


class LoraRegenerationApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.comfy = FakeComfy()
        tools = _write_fake_tools(root)
        (tools / "js" / "character_data.js").write_text(
            "const data = "
            + json.dumps(
                [
                    {"name": "alpha", "copyright": "series", "gender": "1girl"},
                    {"name": "beta", "copyright": "series", "gender": "1girl"},
                ]
            )
            + ";",
            encoding="utf-8",
        )
        app = create_app(
            app_dir=APP_DIR,
            data_dir=root / "data",
            comfy=self.comfy,
            anima_tools_dir=tools,
        )
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.history, self.manager = app[HISTORY_KEY], app[MANAGER_KEY]

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def record(self, *, runtime=True, stored_hash=""):
        settings = copy.deepcopy(DEFAULT_SETTINGS)
        settings.update(
            count=1,
            random_character=True,
            random_pose=True,
            character_detail="trigger",
            quality_prompt="",
            manual_artist="",
            loras=[
                {
                    "filename": "missing/old-character.safetensors",
                    "enabled": True,
                    "strength": 0.7,
                }
            ],
        )
        settings["pools"]["character"] = {
            "mode": "include",
            "ids": ["character:alpha||series", "character:beta||series"],
            "excluded_ids": [],
        }
        settings["pools"]["pose"] = {
            "mode": "include",
            "ids": ["pose:stand", "pose:sit"],
            "excluded_ids": [],
        }
        selected = {
            "character": [
                {
                    "id": "character:alpha||series",
                    "title": "alpha",
                    "builtin": True,
                    "prompt": "alpha, series",
                    "tags": ["1girl"],
                }
            ],
            "pose": [
                {
                    "id": "pose:stand",
                    "title": "Standing",
                    "prompt": "standing",
                    "tags": ["standing"],
                }
            ],
        }
        await self.history.create_batch("lora-history", 1, settings)
        kwargs = (
            {
                "lora_runtime": {
                    "version": 1,
                    "binding_revision": 1,
                    "manual_loras": [],
                    "manual_managed_triggers": [],
                    "effective_loras": copy.deepcopy(settings["loras"]),
                    "applied": [
                        {
                            "character_id": "character:alpha||series",
                            "character_name": "alpha",
                            **settings["loras"][0],
                            "sha256": stored_hash,
                            "trigger_words": ["old trigger"],
                        }
                    ],
                    "skipped": [],
                    "conflicts": [],
                    "automatic_trigger_words": ["old trigger"],
                }
            }
            if runtime
            else {}
        )
        return await self.history.add_image(
            batch_id="lora-history",
            sequence=1,
            prompt_id="historic",
            image={"filename": "historic.png", "type": "output"},
            positive_prompt="1girl, alpha, series, standing, old trigger",
            negative_prompt="",
            sample_seed=123,
            prompt_seed=456,
            settings=settings,
            resolved_selection=selected,
            resolved_prompt="1girl, alpha, series, standing, old trigger",
            **kwargs,
        )

    async def test_missing_automatic_resource_blocks_frozen_modes_but_allows_new_character(
        self,
    ):
        image = await self.record()
        response = await self.client.get(
            f"/api/history/{image['id']}/regeneration-options"
        )
        self.assertEqual(response.status, 200, await response.text())
        value = await response.json()
        self.assertEqual(value["resourceIssues"], [])
        self.assertFalse(value["modes"]["replay"]["available"])
        self.assertTrue(value["modes"]["replay"]["resourceIssues"])
        self.assertFalse(value["modes"]["prompt_variant"]["available"])
        self.assertTrue(value["modes"]["settings_reroll"]["available"])
        self.assertFalse(value["sections"]["character"]["canPreserve"])
        for body in (
            {"mode": "replay"},
            {"mode": "prompt_variant", "count": 1},
            {"mode": "content_redraw", "count": 1, "sections": ["pose"]},
        ):
            result = await self.client.post(
                f"/api/history/{image['id']}/regenerate", json=body
            )
            self.assertEqual(result.status, 400, await result.text())
        result = await self.client.post(
            f"/api/history/{image['id']}/regenerate",
            json={"mode": "content_redraw", "count": 1, "sections": ["character"]},
        )
        self.assertEqual(result.status, 201, await result.text())
        await self.manager.wait()
        self.assertEqual(
            self.manager.snapshot()["status"], "completed", self.manager.snapshot()
        )
        submitted = self.comfy.submissions[-1]["extra_data"]["extra_pnginfo"][
            "anima_random_webui"
        ]
        self.assertEqual(submitted["settings"]["loras"], [])
        self.assertNotIn("old trigger", submitted["resolved_prompt_full"])

    async def test_history_without_automatic_bindings_keeps_all_saved_loras_mandatory(
        self,
    ):
        image = await self.record(runtime=False)
        response = await self.client.get(
            f"/api/history/{image['id']}/regeneration-options"
        )
        value = await response.json()
        self.assertTrue(value["resourceIssues"])
        self.assertFalse(value["modes"]["settings_reroll"]["available"])
        result = await self.client.post(
            f"/api/history/{image['id']}/regenerate",
            json={"mode": "settings_reroll", "count": 1},
        )
        self.assertEqual(result.status, 400)

    async def test_replaced_historical_character_file_is_detected_before_replay(self):
        image = await self.record(stored_hash="original-hash")
        self.comfy.preview_items = [
            {
                "filename": "missing/old-character.safetensors",
                "sha256": "replacement-hash",
            }
        ]

        async def filenames():
            return ["missing/old-character.safetensors"]

        self.comfy.lora_filenames = filenames
        response = await self.client.get(
            f"/api/history/{image['id']}/regeneration-options"
        )
        value = await response.json()
        self.assertEqual(value["resourceIssues"], [])
        self.assertFalse(value["modes"]["replay"]["available"])
        self.assertTrue(value["modes"]["settings_reroll"]["available"])
        self.assertFalse(value["sections"]["character"]["canPreserve"])
