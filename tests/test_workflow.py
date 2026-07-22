import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

from anima_webui.workflow import (  # noqa: E402
    DEFAULT_LORAS,
    DEFAULT_SETTINGS,
    MAX_SAMPLE_SEED,
    WorkflowError,
    build_submission,
    prepare_templates,
    read_json,
    render_workflows,
    validate_loras,
    validate_settings,
)


SOURCE_DIR = APP_DIR / "sources"


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api_path = SOURCE_DIR / "AnimaBasicV7 (1).json"
        cls.ui_path = SOURCE_DIR / "AnimaBasicV7 (3).json"
        cls.api_source = read_json(cls.api_path)
        cls.ui_source = read_json(cls.ui_path)

    def setUp(self):
        self.api, self.ui = prepare_templates(self.api_source, self.ui_source)

    def test_sources_are_not_modified(self):
        before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (self.api_path, self.ui_path)]
        prepare_templates(self.api_source, self.ui_source)
        after = [hashlib.sha256(path.read_bytes()).hexdigest() for path in (self.api_path, self.ui_path)]
        self.assertEqual(before, after)

    def test_prompt_nodes_are_replaced_without_touching_core_nodes(self):
        self.assertNotIn("3", self.api)
        self.assertNotIn("4", self.api)
        self.assertEqual(self.api["60"]["class_type"], "AnimaPromptComposer")
        self.assertEqual(self.api["42"]["class_type"], "AnimaPromptPlusClipEncode")
        self.assertEqual(self.api["42"]["inputs"]["extra_prompt"], ["60", 0])
        self.assertIsInstance(self.api["45"]["inputs"]["text"], str)
        for node_id in ("1", "2", "5", "12", "17", "18", "22", "26", "48", "51"):
            self.assertEqual(self.api[node_id]["class_type"], self.api_source[node_id]["class_type"])

    def test_render_injects_controls_and_seeds(self):
        settings = {
            **DEFAULT_SETTINGS,
            "count": 3,
            "random_character": False,
            "random_clothing": True,
            "random_pose": False,
            "random_background": True,
            "random_character_count": 2,
            "random_clothing_count": 3,
            "random_pose_count": 1,
            "random_background_count": 4,
            "fixed_character": "fixed hero",
            "fixed_clothing": "fixed coat",
            "fixed_pose": "fixed pose",
            "fixed_background": "fixed room",
            "manual_artist": "anmi",
            "quality_prompt": "quality",
            "extra_prompt": "rain",
            "negative_prompt": "bad",
            "width": 1024,
            "height": 768,
            "steps": 22,
            "cfg": 3.5,
        }
        api, ui = render_workflows(self.api, self.ui, settings, 123456, 987, "AnimaRandom/test")
        composer = api["60"]["inputs"]
        self.assertFalse(composer["enable_artist"])
        self.assertEqual(composer["artist_count"], 0)
        self.assertFalse(composer["enable_character"])
        self.assertEqual(composer["character_count"], 2)
        self.assertTrue(composer["enable_clothing"])
        self.assertEqual(composer["clothing_count"], 3)
        self.assertFalse(composer["enable_pose"])
        self.assertEqual(composer["pose_count"], 1)
        self.assertTrue(composer["enable_background"])
        self.assertEqual(composer["background_count"], 4)
        self.assertEqual(composer["extra_prompt"], "rain")
        self.assertEqual(composer["seed"], 987)
        self.assertEqual(api["42"]["inputs"]["artist_tags"], "anmi")
        self.assertEqual(api["42"]["inputs"]["character_tags"], "fixed hero")
        self.assertEqual(api["42"]["inputs"]["clothing_tags"], "")
        self.assertEqual(api["42"]["inputs"]["pose_tags"], "fixed pose")
        self.assertEqual(api["42"]["inputs"]["background_tags"], "")
        self.assertEqual(api["42"]["inputs"]["quality_prompt"], "quality")
        self.assertEqual(api["45"]["inputs"]["text"], "bad")
        self.assertEqual(api["37"]["inputs"]["seed"], 123456)
        self.assertEqual(api["35"]["inputs"]["value"], 1)
        self.assertEqual(api["23"]["inputs"]["value"], 1024)
        self.assertEqual(api["31"]["inputs"]["value"], 768)
        self.assertEqual(api["39"]["inputs"]["value"], 22)
        self.assertEqual(api["41"]["inputs"]["value"], 3.5)
        self.assertEqual(api["12"]["inputs"]["filename_prefix"], "AnimaRandom/test")
        ui_nodes = {node["id"]: node for node in ui["nodes"]}
        self.assertEqual(ui_nodes[60]["widgets_values"][6], 987)
        self.assertEqual(ui_nodes[42]["widgets_values"][7], "rain")
        self.assertEqual(ui_nodes[60]["widgets_values"][10:14], [2, 3, 1, 4])
        self.assertEqual(ui_nodes[42]["widgets_values"][2:6], ["fixed hero", "", "fixed pose", ""])

    def test_render_replaces_loras_in_api_and_visual_workflows(self):
        settings = {
            **DEFAULT_SETTINGS,
            "loras": [
                {"filename": "second.safetensors", "enabled": False, "strength": -0.5},
                {"filename": "first.safetensors", "enabled": True, "strength": 1.25},
            ],
        }
        api, ui = render_workflows(self.api, self.ui, settings, 11, 12, "test")

        lora_inputs = api["2"]["inputs"]
        self.assertEqual(
            [key for key in lora_inputs if key.startswith("lora_")],
            ["lora_1", "lora_2"],
        )
        self.assertEqual(
            lora_inputs["lora_1"],
            {"on": False, "lora": "second.safetensors", "strength": -0.5},
        )
        self.assertEqual(
            lora_inputs["lora_2"],
            {"on": True, "lora": "first.safetensors", "strength": 1.25},
        )
        ui_node = next(node for node in ui["nodes"] if node["id"] == 2)
        self.assertEqual(
            ui_node["widgets_values"][2:4],
            [
                {
                    "on": False,
                    "lora": "second.safetensors",
                    "strength": -0.5,
                    "strengthTwo": None,
                },
                {
                    "on": True,
                    "lora": "first.safetensors",
                    "strength": 1.25,
                    "strengthTwo": None,
                },
            ],
        )

    def test_empty_loras_remove_all_template_slots(self):
        api, ui = render_workflows(
            self.api, self.ui, {**DEFAULT_SETTINGS, "loras": []}, 11, 12, "test"
        )

        self.assertFalse(any(key.startswith("lora_") for key in api["2"]["inputs"]))
        ui_node = next(node for node in ui["nodes"] if node["id"] == 2)
        self.assertEqual(
            ui_node["widgets_values"],
            [{}, {"type": "PowerLoraLoaderHeaderWidget"}, {}, ""],
        )

    def test_lora_validation_and_legacy_defaults(self):
        legacy = dict(DEFAULT_SETTINGS)
        legacy.pop("loras")
        self.assertEqual(validate_settings(legacy)["loras"], DEFAULT_LORAS)
        self.assertEqual(validate_settings({**DEFAULT_SETTINGS, "loras": []})["loras"], [])
        normalized = validate_loras(
            {
                "loras": [
                    {"filename": "one.safetensors", "enabled": False, "strength": 2}
                ]
            },
            ["one.safetensors"],
        )
        self.assertEqual(normalized[0]["strength"], 2.0)

        invalid_loras = (
            [{"filename": "one.safetensors", "enabled": True, "strength": 1}] * 2,
            [{"filename": "folder/one.safetensors", "enabled": True, "strength": 1}],
            [{"filename": "one.safetensors", "enabled": "yes", "strength": 1}],
            [{"filename": "one.safetensors", "enabled": True, "strength": float("inf")}],
            [{"filename": "one.safetensors", "enabled": True, "strength": 100.01}],
        )
        for loras in invalid_loras:
            with self.subTest(loras=loras), self.assertRaises(WorkflowError):
                validate_settings({**DEFAULT_SETTINGS, "loras": loras})

        with self.assertRaisesRegex(WorkflowError, "missing.safetensors"):
            validate_loras(
                {
                    "loras": [
                        {"filename": "missing.safetensors", "enabled": True, "strength": 1}
                    ]
                },
                [],
            )

    def test_render_does_not_mutate_templates(self):
        api_before = copy.deepcopy(self.api)
        ui_before = copy.deepcopy(self.ui)
        render_workflows(self.api, self.ui, DEFAULT_SETTINGS, 1, 2, "one")
        render_workflows(self.api, self.ui, {**DEFAULT_SETTINGS, "width": 1024}, 3, 4, "two")
        self.assertEqual(self.api, api_before)
        self.assertEqual(self.ui, ui_before)

    def test_submission_embeds_recoverable_workflow(self):
        payload = build_submission(self.api, self.ui, DEFAULT_SETTINGS, 11, 12, "prefix", "client", 1)
        self.assertIn("prompt", payload)
        extra = payload["extra_data"]["extra_pnginfo"]
        self.assertEqual(extra["anima_random_webui"]["sample_seed"], 11)
        self.assertEqual(extra["workflow"]["last_node_id"], 60)

    def test_visual_links_are_unique_and_reference_existing_nodes(self):
        node_ids = {int(node["id"]) for node in self.ui["nodes"]}
        link_ids = [int(link[0]) for link in self.ui["links"]]
        self.assertEqual(len(link_ids), len(set(link_ids)))
        for link in self.ui["links"]:
            self.assertIn(int(link[1]), node_ids)
            self.assertIn(int(link[3]), node_ids)
        self.assertNotIn(3, node_ids)
        self.assertNotIn(4, node_ids)

    def test_validation_rejects_bad_values(self):
        for overrides in (
            {"count": 0},
            {"count": 1001},
            {"width": 833},
            {"height": 5000},
            {"steps": 0},
            {"cfg": 31},
            {"random_pose": "yes"},
            {"random_character_count": 0},
            {"random_clothing_count": 6},
            {"random_pose_count": True},
            {"random_expression_count": 2},
            {"character_detail": "full"},
            {"unknown": 1},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(WorkflowError):
                    validate_settings(overrides)

        with self.assertRaises(WorkflowError):
            render_workflows(self.api, self.ui, DEFAULT_SETTINGS, MAX_SAMPLE_SEED + 1, 1, "test")

    def test_generated_json_supports_unicode(self):
        text = json.dumps(self.ui, ensure_ascii=False)
        self.assertIn("随机角色", text)


if __name__ == "__main__":
    unittest.main()
