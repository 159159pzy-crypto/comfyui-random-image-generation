from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from anima_webui.prompt_rules import PromptRuleStore
from anima_webui.workflow import WorkflowError


class PromptRuleStoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "prompt_replacements.json"
        self.store = PromptRuleStore(self.path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_anima_tag_rules_are_safe_and_keep_order(self):
        result = self.store.normalize_fields(
            {
                "quality_prompt": "2025，SCORE 9, Blue_Hair, blue hair",
                "extra_prompt": "@Artist, (Chibi:2), ;d, A girl is smiling. She has Blue_Hair.",
            }
        )
        self.assertEqual(
            result["fields"]["quality_prompt"],
            "year 2025, score_9, blue hair",
        )
        self.assertEqual(
            result["fields"]["extra_prompt"],
            "@artist, (chibi:2), ;d, A girl is smiling. She has Blue_Hair.",
        )
        self.assertTrue(result["changed"])

    def test_artist_and_managed_lora_words_use_field_specific_rules(self):
        result = self.store.normalize_fields(
            {
                "manual_artist": "by Foo, @BAR",
                "extra_prompt": "@Niji9il, Blue_Hair",
            },
            ["@Niji9il"],
        )
        self.assertEqual(result["fields"]["manual_artist"], "@foo, @bar")
        self.assertEqual(result["fields"]["extra_prompt"], "@Niji9il, blue hair")

    def test_score_up_tags_are_normalized_and_preserved(self):
        result = self.store.normalize_fields(
            {
                "quality_prompt": (
                    "score 8 up, SCORE_7_UP, score-6-up, score_5_up, "
                    "(score 4 up:1.1), score 9"
                )
            }
        )
        self.assertEqual(
            result["fields"]["quality_prompt"],
            "score_8_up, score_7_up, score_6_up, score_5_up, "
            "(score_4_up:1.1), score_9",
        )
        self.assertIn("score-format", result["changes"][0]["rules"])
        self.assertNotIn("underscores-to-spaces", result["changes"][0]["rules"])

    async def test_custom_rules_are_scoped_exact_and_non_cascading(self):
        first = await self.store.create(
            {
                "from": "old_tag",
                "to": "Middle_Tag",
                "scopes": ["positive"],
                "enabled": True,
            }
        )
        await self.store.create(
            {
                "from": "Middle_Tag",
                "to": "final tag",
                "scopes": ["positive"],
                "enabled": True,
            }
        )
        positive = self.store.normalize_fields({"extra_prompt": "OLD_TAG"})
        negative = self.store.normalize_fields({"negative_prompt": "OLD_TAG"})
        self.assertEqual(positive["fields"]["extra_prompt"], "middle tag")
        self.assertEqual(negative["fields"]["negative_prompt"], "old tag")
        self.assertIn(first["id"], positive["changes"][0]["rules"])

    async def test_builtin_toggle_and_custom_crud_persist(self):
        await self.store.update("lowercase-tags", {"enabled": False})
        created = await self.store.create(
            {
                "from": "Wrong",
                "to": "Right",
                "scopes": ["positive", "lora"],
                "enabled": True,
            }
        )
        updated = await self.store.update(created["id"], {"to": "Correct"})
        self.assertEqual(updated["to"], "Correct")

        reopened = PromptRuleStore(self.path)
        self.assertIn("lowercase-tags", reopened.disabled_builtins)
        self.assertEqual(reopened.custom_rules[0]["to"], "Correct")
        self.assertTrue(await reopened.delete(created["id"]))
        self.assertFalse(await reopened.delete(created["id"]))

    async def test_duplicate_scope_and_invalid_empty_replacement_are_rejected(self):
        await self.store.create(
            {"from": "same", "to": "one", "scopes": ["positive"], "enabled": True}
        )
        with self.assertRaisesRegex(WorkflowError, "已经存在"):
            await self.store.create(
                {"from": "SAME", "to": "two", "scopes": ["positive"], "enabled": True}
            )
        with self.assertRaisesRegex(WorkflowError, "不能为空"):
            await self.store.create(
                {"from": "remove", "to": "", "scopes": ["positive"], "enabled": True}
            )

    def test_corrupt_file_is_backed_up(self):
        self.path.write_text("{bad", encoding="utf-8")
        reopened = PromptRuleStore(self.path)
        self.assertEqual(reopened.custom_rules, [])
        self.assertTrue(reopened.load_warnings)
        self.assertTrue((self.path.with_name(self.path.name + ".corrupt.bak")).is_file())


if __name__ == "__main__":
    unittest.main()
