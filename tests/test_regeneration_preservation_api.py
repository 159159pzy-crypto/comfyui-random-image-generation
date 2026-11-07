import copy
import json
import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from test_server import APP_DIR, FakeComfy, _write_fake_tools
from anima_webui.server import HISTORY_KEY, MANAGER_KEY, PROMPT_RULES_KEY, create_app
from anima_webui.workflow import DEFAULT_SETTINGS


class RegenerationPreservationApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.comfy = FakeComfy()
        app = create_app(
            app_dir=APP_DIR,
            data_dir=root / "data",
            comfy=self.comfy,
            anima_tools_dir=_write_fake_tools(root),
        )
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.history = app[HISTORY_KEY]
        self.manager = app[MANAGER_KEY]
        self.rules = app[PROMPT_RULES_KEY]

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def create_history_image(
        self, *, positive="1girl, alpha, series, standing", character=None
    ):
        settings = copy.deepcopy(DEFAULT_SETTINGS)
        settings.update(
            {
                "count": 1,
                "random_character": True,
                "random_pose": True,
                "character_detail": "trigger",
                "quality_prompt": "",
                "manual_artist": "",
            }
        )
        settings["pools"]["character"] = {
            "mode": "include",
            "ids": ["character:alpha||series"],
            "excluded_ids": [],
        }
        settings["pools"]["pose"] = {
            "mode": "include",
            "ids": ["pose:stand", "pose:sit"],
            "excluded_ids": [],
        }
        selected = {
            "character": [
                character
                or {
                    "id": "character:alpha||series",
                    "title": "alpha",
                    "builtin": True,
                    "prompt": "alpha, series, 1girl, blue eyes",
                    "tags": ["1girl", "blue eyes"],
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
        await self.history.create_batch("legacy", 1, settings)
        return await self.history.add_image(
            batch_id="legacy",
            sequence=1,
            prompt_id="legacy-prompt",
            image={"filename": "legacy.png", "type": "output"},
            positive_prompt=positive,
            negative_prompt="historical negative",
            sample_seed=123456,
            prompt_seed=98765,
            settings=settings,
            resolved_selection=selected,
            resolved_prompt=positive,
        )

    async def options(self, image):
        response = await self.client.get(
            f"/api/history/{image['id']}/regeneration-options"
        )
        self.assertEqual(response.status, 200)
        return await response.json()

    async def test_saved_snapshot_is_recovered_without_rewriting_history(self):
        image = await self.create_history_image()
        options = await self.options(image)
        self.assertTrue(options["sections"]["character"]["hasSnapshot"])
        self.assertTrue(options["sections"]["character"]["canPreserve"])
        self.assertEqual(options["sections"]["character"]["preserveReason"], "")
        response = await self.client.post(
            f"/api/history/{image['id']}/regenerate",
            json={"mode": "content_redraw", "count": 1, "sections": ["pose"]},
        )
        self.assertEqual(response.status, 201, await response.text())
        await self.manager.wait()
        self.assertEqual(self.manager.snapshot()["status"], "completed")
        submitted = self.comfy.submissions[-1]
        text = submitted["prompt"]["1010:995"]["inputs"]["text"]
        self.assertIn("alpha, series", text)
        self.assertNotIn("blue eyes", text)
        saved = await self.history.get_image(image["id"])
        self.assertEqual(saved["resolved_selection"], image["resolved_selection"])
        self.assertEqual(saved["settings"]["pipeline_version"], 4)
        self.assertEqual(
            submitted["extra_data"]["extra_pnginfo"]["anima_random_webui"]["settings"][
                "pipeline_version"
            ],
            4,
        )

    async def test_clothing_filter_reaches_workflow_history_and_frozen_regeneration(
        self,
    ):
        catalog = self.manager.catalog
        (catalog.tools_dir / "js" / "character_official_data.json").write_text(
            json.dumps(
                {
                    "alpha||series": {
                        "trigger": "alpha, series",
                        "tags": [
                            "1girl",
                            "blue eyes",
                            "white shirt",
                            "detached sleeves",
                            "barefoot",
                            "hair bow",
                            "bare shoulders",
                            "navel",
                            "cleavage",
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )
        catalog.reload()

        async def captured_history(prompt_id, should_abort=None, missing_timeout=30.0):
            payload = self.comfy.submissions[-1]
            return {
                "outputs": {
                    "12": {"images": [{"filename": "filtered.png", "type": "output"}]}
                },
                "prompt": [0, prompt_id, payload["prompt"], payload["extra_data"]],
            }

        self.comfy.wait_for_history = captured_history
        settings = copy.deepcopy(DEFAULT_SETTINGS)
        settings.update(
            {
                "count": 1,
                "random_character": True,
                "character_detail": "trigger_tags_no_clothing",
                "quality_prompt": "",
                "manual_artist": "",
            }
        )
        settings["pools"]["character"] = {
            "mode": "include",
            "ids": ["character:alpha||series"],
        }
        response = await self.client.post("/api/batches", json=settings)
        self.assertEqual(response.status, 201, await response.text())
        await self.manager.wait()
        self.assertEqual(self.manager.snapshot()["status"], "completed")
        expected = (
            "1girl, alpha, series, blue eyes, hair bow, bare shoulders, navel, cleavage"
        )
        submitted = self.comfy.submissions[-1]
        self.assertEqual(submitted["prompt"]["1010:995"]["inputs"]["text"], expected)
        image = (await self.history.list_images())["items"][0]
        self.assertEqual(image["positive_prompt"], expected)
        self.assertEqual(image["settings"]["pipeline_version"], 4)
        self.assertEqual(
            image["settings"]["character_detail"], "trigger_tags_no_clothing"
        )
        role = image["resolved_selection"]["character"][0]
        self.assertEqual(role["prompt"], expected.removeprefix("1girl, "))
        self.assertIn("white shirt", role["tags"])

        # Frozen operations must work even when the original source disappears.
        catalog._items["character"] = []
        catalog._by_id.pop("character:alpha||series")
        for mode in ("replay", "prompt_variant"):
            with self.subTest(mode=mode):
                body = {
                    "mode": mode,
                    **({"count": 1} if mode == "prompt_variant" else {}),
                }
                response = await self.client.post(
                    f"/api/history/{image['id']}/regenerate", json=body
                )
                self.assertEqual(response.status, 201, await response.text())
                await self.manager.wait()
                self.assertEqual(self.manager.snapshot()["status"], "completed")
                submitted = self.comfy.submissions[-1]
                self.assertEqual(
                    submitted["prompt"]["1010:995"]["inputs"]["text"], expected
                )
                metadata = submitted["extra_data"]["extra_pnginfo"][
                    "anima_random_webui"
                ]
                self.assertEqual(
                    metadata["resolved_selection"], image["resolved_selection"]
                )

    async def test_mismatched_role_requires_including_character_in_redraw(self):
        image = await self.create_history_image(
            positive="1girl, mystery, other, standing"
        )
        options = await self.options(image)
        character = options["sections"]["character"]
        self.assertTrue(character["hasSnapshot"])
        self.assertFalse(character["canPreserve"])
        self.assertIn("不一致", character["preserveReason"])
        self.assertTrue(options["sections"]["pose"]["canPreserve"])
        endpoint = f"/api/history/{image['id']}/regenerate"
        rejected = await self.client.post(
            endpoint,
            json={
                "mode": "content_redraw",
                "count": 1,
                "sections": ["pose"],
            },
        )
        self.assertEqual(rejected.status, 400)
        error = (await rejected.json())["error"]
        self.assertIn("同时重抽", error)
        self.assertIn("character", error)
        self.assertEqual(self.comfy.submissions, [])
        accepted = await self.client.post(
            endpoint,
            json={
                "mode": "content_redraw",
                "count": 1,
                "sections": ["character", "pose"],
            },
        )
        self.assertEqual(accepted.status, 201, await accepted.text())
        await self.manager.wait()
        self.assertEqual(self.manager.snapshot()["status"], "completed")
        self.assertEqual(len(self.comfy.submissions), 1)

    async def test_preservation_issue_does_not_block_frozen_replay(self):
        positive = "1girl, mystery, other, standing, "
        image = await self.create_history_image(positive=positive)
        options = await self.options(image)
        self.assertFalse(options["sections"]["character"]["canPreserve"])
        self.assertTrue(options["modes"]["replay"]["available"])
        response = await self.client.post(
            f"/api/history/{image['id']}/regenerate",
            json={"mode": "replay"},
        )
        self.assertEqual(response.status, 201, await response.text())
        await self.manager.wait()
        self.assertEqual(self.manager.snapshot()["status"], "completed")
        payload = self.comfy.submissions[-1]
        graph = payload["prompt"]
        self.assertEqual(graph["1010:995"]["inputs"]["text"], positive)
        self.assertEqual(graph["1010:1004"]["inputs"]["text"], "historical negative")
        metadata = payload["extra_data"]["extra_pnginfo"]["anima_random_webui"]
        self.assertEqual(metadata["sample_seed"], image["sample_seed"])
        self.assertEqual(metadata["prompt_seed"], image["prompt_seed"])
        self.assertEqual(metadata["resolved_selection"], image["resolved_selection"])

    async def test_effective_snapshot_conflict_also_rejects_preservation(self):
        image = await self.create_history_image(
            positive="1girl, beta, series, standing",
            character={
                "id": "character:alpha||series",
                "builtin": True,
                "snapshot_version": 2,
                "prompt": "alpha, series",
                "trigger": ["alpha", "series"],
                "tags": ["1girl", "blue eyes"],
            },
        )
        options = await self.options(image)
        self.assertFalse(options["sections"]["character"]["canPreserve"])
        self.assertIn("不一致", options["sections"]["character"]["preserveReason"])
        self.assertTrue(options["modes"]["replay"]["available"])
        endpoint = f"/api/history/{image['id']}/regenerate"
        response = await self.client.post(
            endpoint,
            json={
                "mode": "content_redraw",
                "count": 1,
                "sections": ["pose"],
            },
        )
        self.assertEqual(response.status, 400)
        self.assertIn("同时重抽", (await response.json())["error"])
        response = await self.client.post(
            endpoint,
            json={
                "mode": "content_redraw",
                "count": 1,
                "sections": ["character", "pose"],
            },
        )
        self.assertEqual(response.status, 201, await response.text())
        await self.manager.wait()
        self.assertEqual(self.manager.snapshot()["status"], "completed")

    async def test_unrecoverable_character_exposes_actionable_reason(self):
        image = await self.create_history_image(
            character={
                "id": "character:deleted||unknown",
                "builtin": True,
                "prompt": "blue eyes",
                "tags": ["blue eyes"],
            }
        )
        options = await self.options(image)
        self.assertFalse(options["sections"]["character"]["canPreserve"])
        reason = options["sections"]["character"]["preserveReason"]
        self.assertIn("同时重抽角色", reason)
        self.assertIn("同提示词变体", reason)

    async def test_changed_rule_requires_redrawing_affected_dimension(self):
        image = await self.create_history_image()
        await self.rules.create({"from": "alpha", "to": "beta", "scopes": ["positive"]})
        options = await self.options(image)
        self.assertFalse(options["sections"]["character"]["canPreserve"])
        self.assertIn(
            "当前提示词规则", options["sections"]["character"]["preserveReason"]
        )
        response = await self.client.post(
            f"/api/history/{image['id']}/regenerate",
            json={
                "mode": "content_redraw",
                "count": 1,
                "sections": ["pose"],
            },
        )
        self.assertEqual(response.status, 400)
        self.assertEqual(self.comfy.submissions, [])
        self.assertTrue(options["modes"]["replay"]["available"])

    async def test_new_snapshots_record_effective_prompt_rules_and_survive_rule_removal(
        self,
    ):
        async def captured_history(prompt_id, should_abort=None, missing_timeout=30.0):
            payload = self.comfy.submissions[-1]
            return {
                "outputs": {
                    "12": {"images": [{"filename": "test.png", "type": "output"}]}
                },
                "prompt": [0, prompt_id, payload["prompt"], payload["extra_data"]],
            }

        self.comfy.wait_for_history = captured_history
        rule = await self.rules.create(
            {"from": "alpha", "to": "beta", "scopes": ["positive"]}
        )
        settings = copy.deepcopy(DEFAULT_SETTINGS)
        settings.update(
            {
                "count": 1,
                "random_character": True,
                "random_pose": True,
                "character_detail": "trigger",
                "quality_prompt": "",
                "manual_artist": "",
            }
        )
        settings["pools"]["character"] = {
            "mode": "include",
            "ids": ["character:alpha||series"],
            "excluded_ids": [],
        }
        settings["pools"]["pose"] = {
            "mode": "include",
            "ids": ["pose:stand", "pose:sit"],
            "excluded_ids": [],
        }
        response = await self.client.post("/api/batches", json=settings)
        self.assertEqual(response.status, 201, await response.text())
        await self.manager.wait()
        self.assertEqual(self.manager.snapshot()["status"], "completed")
        image = (await self.history.list_images())["items"][0]
        character = image["resolved_selection"]["character"][0]
        self.assertEqual(character["prompt"], "beta")
        self.assertEqual(character["trigger"], ["beta"])
        self.assertIn("beta", image["positive_prompt"])
        self.assertNotIn("alpha", image["positive_prompt"])
        await self.rules.delete(rule["id"])
        self.assertTrue(
            (await self.options(image))["sections"]["character"]["canPreserve"]
        )
        response = await self.client.post(
            f"/api/history/{image['id']}/regenerate",
            json={
                "mode": "content_redraw",
                "count": 1,
                "sections": ["pose"],
            },
        )
        self.assertEqual(response.status, 201, await response.text())
        await self.manager.wait()
        self.assertEqual(self.manager.snapshot()["status"], "completed")
        metadata = self.comfy.submissions[-1]["extra_data"]["extra_pnginfo"][
            "anima_random_webui"
        ]
        self.assertEqual(
            metadata["resolved_selection"]["character"][0]["prompt"], "beta"
        )
        self.assertIn("beta", metadata["resolved_prompt_full"])
        self.assertNotIn("alpha", metadata["resolved_prompt_full"])


if __name__ == "__main__":
    unittest.main()
