"""Preserve historical file identities across admission and queued execution."""
import asyncio
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from anima_webui.history import HistoryStore
from anima_webui.runner import BatchManager
from anima_webui.workflow import DEFAULT_SETTINGS, WorkflowError
from test_runner import FakeCatalog, FakeComfy, FakeTemplates


class RunnerResourceFreezeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.history = HistoryStore(Path(self.temp.name) / "history.sqlite3")
        self.comfy = FakeComfy()
        self.manager = BatchManager(FakeTemplates(), self.history, self.comfy)
        self.identities = [{"field": "model_name", "name": DEFAULT_SETTINGS["model_name"],
                            "kind": "models", "fingerprint": "original-file"}]
        self.saved_resolution = {"resources": copy.deepcopy(self.identities)}
        self.settings = {**DEFAULT_SETTINGS, "count": 1}
        self.regeneration = {"mode": "replay", "frozen_positive_prompt": "frozen prompt",
                             "frozen_negative_prompt": "", "fixed_selection": {}}

        async def resolve(settings, **kwargs):
            return {"version": 1, "effective_settings": copy.deepcopy(settings),
                    "issues": [], "replacements": [], "resources": copy.deepcopy(self.identities)}

        self.manager.resource_resolver = resolve

    async def asyncTearDown(self):
        if self.comfy.block:
            self.comfy.block.set()
        await self.manager.shutdown()
        await self.history.close()
        self.temp.cleanup()

    async def test_replay_and_variant_recheck_the_original_identity_before_admission(self):
        for mode in ("replay", "prompt_variant"):
            for changed in ([], [{**self.identities[0], "fingerprint": "replacement-file"}],
                            [{**self.identities[0], "fingerprint": ""}]):
                with self.subTest(mode=mode, resources=changed):
                    self.identities = copy.deepcopy(changed)
                    with self.assertRaisesRegex(WorkflowError, "历史资源文件身份已变化"):
                        await self.manager.start(self.settings, regeneration={**self.regeneration, "mode": mode,
                                                                              **({"prompt_seed": 123} if mode == "prompt_variant" else {})},
                                                 resource_resolution=self.saved_resolution)
                    self.assertFalse(self.manager.active())
                    self.assertEqual(self.manager.queue, [])
                    self.assertEqual(self.comfy.submissions, [])
            self.identities = copy.deepcopy(self.saved_resolution["resources"])

    async def test_unchanged_frozen_identity_is_saved_in_the_new_history(self):
        await self.manager.start(self.settings, regeneration=self.regeneration,
                                 resource_resolution=self.saved_resolution)
        result = await self.manager.wait()
        self.assertEqual(result["status"], "completed", result)
        saved = (await self.history.list_images())["items"][0]
        self.assertEqual(saved["bound_resources"]["resource_resolution"]["resources"],
                         self.saved_resolution["resources"])

    async def test_preserved_character_checks_old_lora_identity_while_allowing_model_replacement(self):
        filename = "hero.safetensors"
        self.comfy.available_loras.append(filename)
        self.settings["loras"] = [{"filename": filename, "enabled": True, "strength": 1}]
        saved = {"resources": [*copy.deepcopy(self.identities), {
            "field": "loras.0.filename", "kind": "loras", "name": filename, "fingerprint": "original-lora",
        }]}
        self.identities = copy.deepcopy(saved["resources"])
        self.identities[0]["fingerprint"] = "replacement-model"
        regeneration = {"mode": "content_redraw", "redraw_sections": ["pose"],
                        "fixed_selection": {}, "original_selection": {}}
        self.manager._begin_batch = AsyncMock()
        await self.manager.start(self.settings, regeneration=regeneration, resource_resolution=saved)
        self.manager._begin_batch.assert_awaited_once()
        self.manager._begin_batch.reset_mock()
        self.identities[1]["fingerprint"] = "replacement-lora"
        with self.assertRaisesRegex(WorkflowError, "历史资源文件身份已变化"):
            await self.manager.start(self.settings, regeneration=regeneration, resource_resolution=saved)
        self.manager._begin_batch.assert_not_awaited()

    async def test_queued_job_rechecks_identity_before_any_comfy_submission(self):
        self.comfy.block = asyncio.Event()
        await self.manager.start(self.settings)
        async with asyncio.timeout(2):
            while not self.comfy.submissions:
                await asyncio.sleep(0)
        queued = await self.manager.start(self.settings, regeneration=self.regeneration,
                                          resource_resolution=self.saved_resolution)
        self.assertEqual(queued["status"], "queued")
        self.identities[0]["fingerprint"] = "replacement-file"
        self.comfy.block.set()
        async with asyncio.timeout(2):
            while self.manager.queue or self.manager.active():
                await asyncio.sleep(0)
        self.assertEqual(self.manager.snapshot()["status"], "error")
        self.assertIn("入队后资源已变化", self.manager.snapshot()["error"])
        self.assertEqual(len(self.comfy.submissions), 1)

    def install_character_bindings(self):
        self.fingerprints = {"hero-a.safetensors": "file-a", "hero-b.safetensors": "file-b"}
        bindings = {}
        for index, filename in enumerate(self.fingerprints):
            bindings[f"character:{index}"] = {"loras": [{"filename": filename, "enabled": True,
                                                         "strength": 0.8, "sha256": ""}]}
        self.manager.lora_library = SimpleNamespace(binding_snapshot=AsyncMock(return_value={
            "revision": 1, "bindings": bindings,
        }))
        self.manager._binding_inventory = AsyncMock(return_value={"items": [
            {"filename": filename, "model_profile": "anima", "classification_status": "recognized", "sha256": ""}
            for filename in self.fingerprints
        ]})
        self.comfy.available_loras.extend(self.fingerprints)

        async def inspect(**kwargs):
            return {"supported": True, "lora_files": [{"name": name, "fingerprint": value}
                                                       for name, value in self.fingerprints.items()]}

        async def resolve(settings, **kwargs):
            resources = copy.deepcopy(self.identities)
            resources.extend({"field": f"loras.{index}.filename", "name": item["filename"],
                              "kind": "loras", "fingerprint": self.fingerprints[item["filename"]]}
                             for index, item in enumerate(settings["loras"]) if item["enabled"])
            return {"version": 1, "effective_settings": copy.deepcopy(settings),
                    "issues": [], "replacements": [], "resources": resources}

        class CharacterCatalog(FakeCatalog):
            draws = 0

            def resolve_prompt(self, settings, prompt_seed):
                result = super().resolve_prompt(settings, prompt_seed)
                result["selected"]["character"][0]["id"] = f"character:{self.draws % 2}"
                self.draws += 1
                return result

        self.comfy.resource_inspection = inspect
        self.manager.resource_resolver = resolve
        self.manager.catalog = CharacterCatalog()
        self.settings = {**self.settings, "loras": [], "random_character": True}

    async def test_each_image_records_its_automatic_lora_file_identity(self):
        self.install_character_bindings()
        await self.manager.start({**self.settings, "count": 2})
        result = await self.manager.wait()
        self.assertEqual(result["status"], "completed", result)
        records = (await self.history.list_images())["items"]
        seen = set()
        for record in records:
            resources = record["bound_resources"]["resource_resolution"]["resources"]
            filename = record["settings"]["loras"][0]["filename"]
            loras = [item for item in resources if item["kind"] == "loras"]
            self.assertEqual(loras, [{"field": "loras.0.filename", "name": filename,
                                      "kind": "loras", "fingerprint": self.fingerprints[filename]}])
            payload = self.comfy.submissions[record["sequence"] - 1]
            png = payload["extra_data"]["extra_pnginfo"]["anima_random_webui"]
            self.assertEqual(png["resource_resolution"]["resources"], resources)
            seen.add(filename)
        self.assertEqual(seen, set(self.fingerprints))
        self.assertFalse(any(item["kind"] == "loras" for item in result["resource_resolution"]["resources"]))

    async def test_queued_automatic_binding_cannot_adopt_replaced_file(self):
        self.comfy.block = asyncio.Event()
        await self.manager.start(self.settings)
        async with asyncio.timeout(2):
            while not self.comfy.submissions:
                await asyncio.sleep(0)
        self.install_character_bindings()
        queued = await self.manager.start(self.settings)
        self.assertEqual(queued["status"], "queued")
        self.fingerprints["hero-a.safetensors"] = "replacement-a"
        self.comfy.block.set()
        async with asyncio.timeout(2):
            while self.manager.queue or self.manager.active():
                await asyncio.sleep(0)
        self.assertEqual(self.manager.snapshot()["status"], "error")
        self.assertIn("入队后资源已变化", self.manager.snapshot()["error"])
        self.assertEqual(len(self.comfy.submissions), 1)

    async def test_settings_reroll_freezes_current_bindings_instead_of_old_file_identities(self):
        self.install_character_bindings()
        self.fingerprints["hero-a.safetensors"] = "newly-confirmed-binding"
        old = {"resources": [*self.identities, {"field": "loras.0.filename", "name": "hero-a.safetensors",
                                               "kind": "loras", "fingerprint": "previous-binding"}]}
        await self.manager.start(self.settings, regeneration={"mode": "settings_reroll"}, resource_resolution=old)
        result = await self.manager.wait()
        self.assertEqual(result["status"], "completed", result)
        saved = (await self.history.list_images())["items"][0]
        self.assertEqual(saved["bound_resources"]["resource_resolution"]["resources"][-1]["fingerprint"],
                         "newly-confirmed-binding")
