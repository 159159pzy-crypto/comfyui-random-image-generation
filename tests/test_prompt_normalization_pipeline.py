import tempfile
import unittest
from pathlib import Path

from anima_webui.history import HistoryStore
from anima_webui.model_profiles import model_profile_defaults
from anima_webui.prompt_rules import PromptRuleStore
from anima_webui.runner import BatchManager
from test_runner import FakeCatalog, FakeComfy, FakeTemplates


class SettingsCatalog(FakeCatalog):
    def resolve_prompt(self, settings, prompt_seed):
        text = settings["extra_prompt"]
        return {
            "composer_prompt": text,
            "full_prompt": text,
            "selected": {"character": [{"snapshot_version": 2, "prompt": text}]},
        }


class PromptNormalizationPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.history = HistoryStore(root / "history.sqlite3")
        self.rules = PromptRuleStore(root / "rules.json")
        self.comfy = FakeComfy()
        self.manager = BatchManager(
            FakeTemplates(), self.history, self.comfy, catalog=SettingsCatalog(),
            prompt_rules=self.rules,
        )
        for profile in ("anima",):
            await self.rules.create({"from": "original", "to": "middle",
                                     "scopes": ["positive", "negative"], "model_profile": profile})
            await self.rules.create({"from": "middle", "to": "final",
                                     "scopes": ["positive", "negative"], "model_profile": profile})

    async def asyncTearDown(self):
        await self.manager.wait()
        await self.history.close()
        self.temp.cleanup()

    async def test_random_settings_final_and_selected_apply_custom_once(self):
        for profile in ("anima",):
            with self.subTest(profile=profile):
                await self.manager.start({
                    **model_profile_defaults(profile), "model_profile": profile, "count": 1,
                    "quality_prompt": "", "extra_prompt": "original",
                    "negative_prompt": "original", "loras": [],
                })
                result = await self.manager.wait()
                self.assertEqual(result["status"], "completed", result)
                payload = self.comfy.submissions[-1]
                self.assertEqual(payload["resolved_prompt"], "middle")
                self.assertEqual(payload["resolved_prompt_full"], "middle")
                self.assertEqual(payload["settings"]["negative_prompt"], "middle")
                if profile == "anima":
                    self.assertEqual(payload["resolved_selection"]["character"][0]["prompt"], "middle")
