import sys
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

from anima_webui.comfy import extract_positive_prompt  # noqa: E402


class PositivePromptTests(unittest.TestCase):
    def test_rebuilds_prompt_when_history_contains_connection_placeholder(self):
        entry = {
            "prompt": [0, 0, {}, {"extra_pnginfo": {
                "anima_prompt": {"42": {"positive": "quality, ['60', 0], "}},
                "anima_prompt_composer": {"60": {"resolved_prompt": "character, pose, "}},
                "anima_random_webui": {"settings": {
                    "quality_prompt": "quality",
                    "manual_artist": "artist, @second",
                    "extra_prompt": "lighting",
                }},
            }}],
        }
        self.assertEqual(extract_positive_prompt(entry), "quality, @artist, @second, character, pose, lighting, ")

    def test_rebuild_includes_fixed_categories_without_repeating_extra_prompt(self):
        entry = {
            "prompt": [0, 0, {}, {"extra_pnginfo": {
                "anima_prompt": {"42": {"positive": "quality, ['60', 0], "}},
                "anima_prompt_composer": {"60": {"resolved_prompt": "lighting, "}},
                "anima_random_webui": {"settings": {
                    "quality_prompt": "quality",
                    "manual_artist": "",
                    "random_character": False,
                    "random_clothing": False,
                    "random_pose": False,
                    "random_background": False,
                    "fixed_character": "hero",
                    "fixed_clothing": "red coat",
                    "fixed_pose": "standing",
                    "fixed_background": "city",
                    "extra_prompt": "lighting",
                }},
            }}],
        }

        self.assertEqual(
            extract_positive_prompt(entry),
            "quality, hero, red coat, standing, city, lighting, ",
        )


if __name__ == "__main__":
    unittest.main()
