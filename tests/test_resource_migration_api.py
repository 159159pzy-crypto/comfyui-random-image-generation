"""Compatible new tasks must never weaken the frozen-history resource boundary."""
import copy
import unittest

from anima_webui.server import HISTORY_KEY, MANAGER_KEY
from anima_webui.workflow import DEFAULT_SETTINGS
import test_server as server_fixture


class ResourceMigrationApiTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = server_fixture.ServerTests.asyncSetUp
    asyncTearDown = server_fixture.ServerTests.asyncTearDown

    async def record(self, settings, bound_resources=None):
        history = self.client.server.app[HISTORY_KEY]
        await history.create_batch("saved", 1, settings)
        return await history.add_image(
            batch_id="saved", sequence=1, prompt_id="saved-prompt",
            image={"filename": "saved.png", "type": "output"},
            positive_prompt="blue sky", negative_prompt="bad quality",
            sample_seed=123, prompt_seed=456, settings=settings,
            bound_resources=bound_resources,
        )

    async def test_new_tasks_and_rerolls_replace_but_replay_and_variant_refuse(self):
        settings = copy.deepcopy(DEFAULT_SETTINGS)
        settings.update(model_name="missing-anima.safetensors", count=1, base_mode="normal")
        resolved = await (await self.client.post("/api/resources/resolve", json={"settings": settings})).json()
        self.assertTrue(resolved["ready"], resolved)
        self.assertEqual(resolved["effective_settings"]["model_name"], DEFAULT_SETTINGS["model_name"])
        image = await self.record(settings)
        options = await (await self.client.get(f"/api/history/{image['id']}/regeneration-options")).json()
        for mode in ("replay", "prompt_variant"):
            self.assertFalse(options["modes"][mode]["available"])
            response = await self.client.post(f"/api/history/{image['id']}/regenerate", json={"mode": mode})
            self.assertEqual(response.status, 400)
        self.assertTrue(options["modes"]["settings_reroll"]["available"])
        self.assertTrue(options["modes"]["settings_reroll"]["resourceChanged"])
        response = await self.client.post(f"/api/history/{image['id']}/regenerate", json={"mode": "settings_reroll", "count": 1})
        self.assertEqual(response.status, 201, await response.text())
        final = await self.client.server.app[MANAGER_KEY].wait()
        self.assertEqual(final["status"], "completed", final)
        self.assertEqual((await self.client.server.app[HISTORY_KEY].get_image(image["id"]))["settings"], settings)

    async def test_replay_refuses_replaced_file_identity_at_the_same_path(self):
        settings = copy.deepcopy(DEFAULT_SETTINGS)
        settings.update(count=1, base_mode="normal")

        async def inspect(**kwargs):
            return {"supported": True, "version": 1, "items": [{
                "kind": "models", "name": settings["model_name"], "roles": ["anima.model"],
                "status": "recognized", "fingerprint": "new-file", "source": "header",
            }]}

        self.comfy.resource_inspection = inspect
        image = await self.record(settings, {"resource_resolution": {"resources": [{
            "field": "model_name", "name": settings["model_name"], "fingerprint": "original-file",
        }]}})
        options = await (await self.client.get(f"/api/history/{image['id']}/regeneration-options")).json()
        self.assertFalse(options["modes"]["replay"]["available"])
        self.assertTrue(any("文件身份" in reason for reason in options["modes"]["replay"]["reasons"]))
        response = await self.client.post(f"/api/history/{image['id']}/regenerate", json={"mode": "replay"})
        self.assertEqual(response.status, 400)
        self.assertEqual(self.comfy.submissions, [])

    async def test_resolve_rejects_removed_modes_and_settings_normalize_stays_offline(self):
        for values in ({"model_profile": "krea2"}, {"workbench": "single"}, {"generation_mode": "img2img"}):
            response = await self.client.post("/api/resources/resolve", json={"settings": values})
            self.assertEqual(response.status, 400)

        async def unavailable(*args, **kwargs):
            raise AssertionError("normalize must not contact ComfyUI")

        self.comfy.resource_inventory = unavailable
        response = await self.client.post("/api/settings/normalize", json={"settings": {"pipeline_version": 4, "cfg": 7}})
        self.assertEqual(response.status, 200)
        settings = (await response.json())["settings"]
        self.assertEqual((settings["pipeline_version"], settings["cfg"]), (4, 7))

    async def test_unknown_lora_is_never_replaced(self):
        settings = {**copy.deepcopy(DEFAULT_SETTINGS), "loras": [{"filename": "missing.safetensors", "enabled": True, "strength": 1}]}
        response = await self.client.post("/api/resources/resolve", json={"settings": settings})
        result = await response.json()
        self.assertFalse(result["ready"])
        self.assertTrue(any(issue["module"] == "loras" for issue in result["issues"]))
        self.assertEqual(result["effective_settings"]["loras"], settings["loras"])
        self.assertEqual(self.comfy.submissions, [])

    async def test_missing_already_mapped_target_is_not_mapped_a_second_time(self):
        manager = self.client.server.app[MANAGER_KEY]
        manager.templates.resource_paths = {"original.safetensors": "mapped.safetensors", "mapped.safetensors": "replacement.safetensors"}
        original_inventory = self.comfy.resource_inventory

        async def inventory():
            result = await original_inventory()
            result["models"] = ["replacement.safetensors"]
            return result

        self.comfy.resource_inventory = inventory
        settings = {**copy.deepcopy(DEFAULT_SETTINGS), "model_name": "mapped.safetensors"}
        result = await (await self.client.post("/api/resources/resolve", json={"settings": settings})).json()
        self.assertTrue(result["ready"], result)
        self.assertEqual(result["effective_settings"]["model_name"], "replacement.safetensors")
        change = next(item for item in result["replacements"] if item["field"] == "model_name")
        self.assertEqual(change["from"], "mapped.safetensors")
        self.assertEqual(change["reason"], "missing_model")
