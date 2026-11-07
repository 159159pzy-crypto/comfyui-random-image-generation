from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from anima_webui.lora_triggers import LoraTriggerOverrideStore, normalize_trigger_words
from anima_webui.workflow import WorkflowError


class LoraTriggerOverrideStoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "lora_trigger_overrides.json"
        self.store = LoraTriggerOverrideStore(self.path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    async def test_override_empty_reset_and_case_insensitive_lookup(self):
        words = await self.store.set(
            "Style/Test.safetensors", [" @Exact_One ", "@exact_one", "Second"]
        )
        self.assertEqual(words, ["@Exact_One", "Second"])
        self.assertEqual(
            self.store.effective("style/test.safetensors", ["source"]),
            (["@Exact_One", "Second"], True),
        )

        await self.store.set("Style/Test.safetensors", [])
        reopened = LoraTriggerOverrideStore(self.path)
        self.assertEqual(reopened.effective("STYLE/TEST.safetensors", ["source"]), ([], True))
        self.assertTrue(await reopened.delete("style/test.safetensors"))
        self.assertEqual(reopened.effective("Style/Test.safetensors", ["source"]), (["source"], False))

    def test_validation_preserves_exact_spelling(self):
        self.assertEqual(normalize_trigger_words(["@Niji9il", "A_B"]), ["@Niji9il", "A_B"])
        with self.assertRaisesRegex(WorkflowError, "必须是数组"):
            normalize_trigger_words("@bad")
        with self.assertRaisesRegex(WorkflowError, "必须是字符串"):
            normalize_trigger_words([1])

    def test_corrupt_file_is_backed_up(self):
        self.path.write_text("[]", encoding="utf-8")
        reopened = LoraTriggerOverrideStore(self.path)
        self.assertEqual(reopened.overrides, {})
        self.assertTrue(reopened.load_warnings)
        self.assertTrue((self.path.with_name(self.path.name + ".corrupt.bak")).is_file())


if __name__ == "__main__":
    unittest.main()
