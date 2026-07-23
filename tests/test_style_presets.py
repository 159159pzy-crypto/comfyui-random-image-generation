import json
import sys
import tempfile
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

from anima_webui.style_presets import PRESET_SETTING_KEYS, StylePresetStore, preset_settings  # noqa: E402
from anima_webui.workflow import DEFAULT_SETTINGS, WorkflowError  # noqa: E402


def snapshot(**overrides):
    values = {key: DEFAULT_SETTINGS[key] for key in PRESET_SETTING_KEYS}
    values.update(overrides)
    return values


class StylePresetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "style_presets.json"
        self.store = StylePresetStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_favorites_sort_first_and_updates_replace_the_snapshot(self):
        favorite = self.store.create(
            {"name": "Favorite", "favorite": True, "settings": snapshot(width=768)}
        )
        regular = self.store.create(
            {"name": "Regular", "favorite": False, "settings": snapshot(width=832)}
        )
        self.assertEqual([item["id"] for item in self.store.list()["items"]], [favorite["id"], regular["id"]])

        updated = self.store.update(
            regular["id"],
            {"name": "Renamed", "favorite": True, "settings": snapshot(width=1024)},
        )
        self.assertEqual(updated["name"], "Renamed")
        self.assertEqual(updated["settings"]["width"], 1024)
        self.assertTrue(self.store.delete(favorite["id"]))
        self.assertFalse(self.store.delete("missing"))

    def test_atomic_save_leaves_valid_json_without_temp_files(self):
        self.store.create({"name": "Atomic", "settings": snapshot(), "favorite": False})
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["items"][0]["name"], "Atomic")
        self.assertEqual(list(self.path.parent.glob("style-presets-*.json")), [])

    def test_partial_legacy_snapshot_receives_current_defaults(self):
        settings = preset_settings({"width": 1024, "manual_artist": "anmi"})
        self.assertEqual(settings["model_name"], DEFAULT_SETTINGS["model_name"])
        self.assertEqual(settings["hires"], DEFAULT_SETTINGS["hires"])
        self.assertEqual(settings["detailers"], DEFAULT_SETTINGS["detailers"])
        self.assertEqual(settings["manual_artist"], "@anmi")

    def test_duplicate_names_are_rejected_case_insensitively(self):
        first = self.store.create({"name": "Soft Light", "settings": snapshot(), "favorite": False})
        with self.assertRaisesRegex(WorkflowError, "同名"):
            self.store.create({"name": "soft light", "settings": snapshot(), "favorite": False})

        second = self.store.create({"name": "Ink", "settings": snapshot(), "favorite": False})
        with self.assertRaisesRegex(WorkflowError, "同名"):
            self.store.update(second["id"], {"name": "SOFT LIGHT"})
        self.assertEqual(self.store.update(first["id"], {"name": "Soft Light"})["name"], "Soft Light")


if __name__ == "__main__":
    unittest.main()
