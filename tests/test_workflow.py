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
    WorkflowTemplates,
    build_submission,
    normalize_artist_tags,
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

    def test_source_workflows_have_no_default_lora_slots(self):
        self.assertFalse(
            any(key.startswith("lora_") for key in self.api_source["2"]["inputs"])
        )
        visual_lora = next(node for node in self.ui_source["nodes"] if node["id"] == 2)
        self.assertEqual(
            visual_lora["widgets_values"],
            [{}, {"type": "PowerLoraLoaderHeaderWidget"}, {}, ""],
        )

    def test_prompt_nodes_are_replaced_without_touching_core_nodes(self):
        self.assertNotIn("3", self.api)
        self.assertNotIn("4", self.api)
        self.assertEqual(self.api["60"]["class_type"], "AnimaPromptComposer")
        self.assertEqual(self.api["42"]["class_type"], "AnimaPromptPlusClipEncode")
        self.assertEqual(self.api["42"]["inputs"]["extra_prompt"], ["60", 0])
        self.assertIsInstance(self.api["45"]["inputs"]["text"], str)
        for node_id in ("1", "2", "5", "12", "17", "18", "22", "26", "48"):
            self.assertEqual(self.api[node_id]["class_type"], self.api_source[node_id]["class_type"])
        self.assertEqual(self.api["61"]["class_type"], "UpscaleModelLoader")
        self.assertEqual(self.api["62"]["class_type"], "ImageUpscaleWithModel")
        self.assertEqual(self.api["51"]["class_type"], "ImageScaleBy")

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
            "sampler_name": "euler",
            "scheduler": "karras",
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
        self.assertEqual(api["42"]["inputs"]["artist_tags"], "@anmi")
        self.assertEqual(api["5"]["inputs"]["sampler_name"], "euler")
        self.assertEqual(api["5"]["inputs"]["scheduler"], "karras")
        sampler_ui = next(node for node in ui["nodes"] if node["id"] == 5)
        self.assertEqual(sampler_ui["widgets_values"][4:6], ["euler", "karras"])
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
        # 回归:标量控件节点的 widgets_values 必须保持列表结构,
        # 否则嵌入 PNG 的 UI 工作流拖回 ComfyUI 时无法还原(#P0-1)。
        for node_id, expected in ((23, 1024), (31, 768), (35, 1), (39, 22), (41, 3.5), (12, "AnimaRandom/test")):
            with self.subTest(node=node_id):
                self.assertIsInstance(ui_nodes[node_id]["widgets_values"], list)
                self.assertEqual(ui_nodes[node_id]["widgets_values"][0], expected)

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
        self.assertEqual(DEFAULT_LORAS, [])
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
        managed = validate_settings(
            {**DEFAULT_SETTINGS, "lora_managed_triggers": [" @one ", "@ONE", "second"]}
        )
        self.assertEqual(managed["lora_managed_triggers"], ["@one", "second"])

        invalid_loras = (
            [{"filename": "one.safetensors", "enabled": True, "strength": 1}] * 2,
            [{"filename": "../one.safetensors", "enabled": True, "strength": 1}],
            [{"filename": "C:\\models\\one.safetensors", "enabled": True, "strength": 1}],
            [{"filename": "one.safetensors", "enabled": "yes", "strength": 1}],
            [{"filename": "one.safetensors", "enabled": True, "strength": float("inf")}],
            [{"filename": "one.safetensors", "enabled": True, "strength": 100.01}],
        )
        for loras in invalid_loras:
            with self.subTest(loras=loras), self.assertRaises(WorkflowError):
                validate_settings({**DEFAULT_SETTINGS, "loras": loras})

        with self.assertRaisesRegex(WorkflowError, "lora_managed_triggers"):
            validate_settings({**DEFAULT_SETTINGS, "lora_managed_triggers": "@one"})

        nested = validate_loras(
            {"loras": [{"filename": "风格/one.safetensors", "enabled": True, "strength": 1}]},
            ["风格\\one.safetensors"],
        )
        self.assertEqual(nested[0]["filename"], "风格\\one.safetensors")
        legacy = validate_loras(
            {"loras": [{"filename": "one.safetensors", "enabled": True, "strength": 1}]},
            ["风格\\one.safetensors"],
        )
        self.assertEqual(legacy[0]["filename"], "风格\\one.safetensors")
        with self.assertRaisesRegex(WorkflowError, "多个子目录"):
            validate_loras(
                {"loras": [{"filename": "one.safetensors", "enabled": True, "strength": 1}]},
                ["风格\\one.safetensors", "人物\\one.safetensors"],
            )

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

    def test_model_hires_and_detailer_chain_are_rendered(self):
        settings = {
            **DEFAULT_SETTINGS,
            "model_name": "alternate.safetensors",
            "hires": {"enabled": False, "model_name": "upscale.pth", "percent": 60},
            "detailers": {"hand": True, "nsfw": False, "face": True, "eyes": False},
        }
        api, ui = render_workflows(self.api, self.ui, settings, 11, 12, "test")
        self.assertEqual(api["1"]["inputs"]["unet_name"], "alternate.safetensors")
        self.assertNotIn("51", api)
        self.assertNotIn("61", api)
        self.assertNotIn("62", api)
        self.assertEqual(api["27"]["inputs"]["image"], ["48", 0])
        self.assertEqual(api["29"]["inputs"]["image"], ["27", 0])
        self.assertEqual(api["12"]["inputs"]["images"], ["29", 0])
        self.assertEqual(api["13"]["inputs"]["Select to add Wildcard"], "Select the Wildcard to add to the text")
        self.assertEqual(api["15"]["inputs"]["Select to add LoRA"], "Select the LoRA to add to the text")
        self.assertNotIn("28", api)
        self.assertNotIn("30", api)
        modes = {node["id"]: node["mode"] for node in ui["nodes"]}
        self.assertEqual(modes[51], 4)
        self.assertEqual(modes[27], 0)
        self.assertEqual(modes[29], 0)
        self.assertEqual(modes[28], 4)

    def test_hires_settings_keep_save_connected(self):
        settings = {
            **DEFAULT_SETTINGS,
            "hires": {"enabled": True, "model_name": "upscale.pth", "percent": 60},
        }
        api, ui = render_workflows(self.api, self.ui, settings, 11, 12, "test")
        self.assertEqual(api["61"]["inputs"]["model_name"], "upscale.pth")
        self.assertEqual(api["62"]["inputs"]["image"], ["48", 0])
        self.assertEqual(api["51"]["inputs"]["scale_by"], 0.6)
        self.assertEqual(api["12"]["inputs"]["images"], ["51", 0])
        nodes = {node["id"]: node for node in ui["nodes"]}
        self.assertEqual(nodes[61]["widgets_values"][0], "upscale.pth")
        self.assertEqual(nodes[51]["widgets_values"][1], 0.6)

    def test_hires_percent_accepts_declared_bounds(self):
        for percent in (1, 1000):
            with self.subTest(percent=percent):
                settings = validate_settings(
                    {
                        **DEFAULT_SETTINGS,
                        "hires": {**DEFAULT_SETTINGS["hires"], "percent": percent},
                    }
                )
                self.assertEqual(settings["hires"]["percent"], percent)

                api, _ = render_workflows(self.api, self.ui, settings, 11, 12, "test")
                self.assertEqual(api["51"]["inputs"]["scale_by"], percent / 100)

    def test_each_detailer_and_full_chain_keep_declared_order(self):
        node_for = {"hand": "27", "nsfw": "28", "face": "29", "eyes": "30"}
        for enabled_names in (("hand",), ("nsfw",), ("face",), ("eyes",), tuple(node_for)):
            detailers = {name: name in enabled_names for name in node_for}
            with self.subTest(enabled=enabled_names):
                api, _ = render_workflows(
                    self.api,
                    self.ui,
                    {**DEFAULT_SETTINGS, "hires": {**DEFAULT_SETTINGS["hires"], "enabled": False}, "detailers": detailers},
                    11,
                    12,
                    "test",
                )
                current = ["48", 0]
                for name, node_id in node_for.items():
                    if detailers[name]:
                        self.assertEqual(api[node_id]["inputs"]["image"], current)
                        current = [node_id, 0]
                    else:
                        self.assertNotIn(node_id, api)
                self.assertEqual(api["12"]["inputs"]["images"], current)

    def test_artist_tags_are_canonical_and_independent(self):
        self.assertEqual(normalize_artist_tags("@anmi @rella, by Foo, anmi"), "@anmi, @rella, @Foo")

    def test_frozen_prompt_is_injected_once_and_preserves_generation_settings(self):
        api, ui = render_workflows(
            self.api,
            self.ui,
            DEFAULT_SETTINGS,
            123,
            456,
            "replay",
            frozen_positive_prompt="frozen hero, frozen pose, ",
            frozen_negative_prompt="frozen negative ",
        )
        self.assertEqual(api["60"]["inputs"]["resolved_prompt"], "frozen hero, frozen pose, ")
        self.assertEqual(api["42"]["inputs"]["quality_prompt"], "")
        self.assertEqual(api["42"]["inputs"]["artist_tags"], "")
        self.assertEqual(api["45"]["inputs"]["text"], "frozen negative ")
        self.assertEqual(api["37"]["inputs"]["seed"], 123)
        self.assertEqual(api["5"]["inputs"]["sampler_name"], DEFAULT_SETTINGS["sampler_name"])
        self.assertEqual(api["1"]["inputs"]["unet_name"], DEFAULT_SETTINGS["model_name"])
        self.assertEqual(ui["nodes"][0]["type"], self.ui["nodes"][0]["type"])
        settings = validate_settings({**DEFAULT_SETTINGS, "manual_artist": "anmi, @rella"})
        self.assertEqual(settings["manual_artist"], "@anmi, @rella")

    def test_submission_embeds_recoverable_workflow(self):
        payload = build_submission(self.api, self.ui, DEFAULT_SETTINGS, 11, 12, "prefix", "client", 1)
        self.assertIn("prompt", payload)
        extra = payload["extra_data"]["extra_pnginfo"]
        self.assertEqual(extra["anima_random_webui"]["sample_seed"], 11)
        self.assertEqual(extra["workflow"]["last_node_id"], 62)

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
            {"sampler_name": ""},
            {"sampler_name": 1},
            {"scheduler": ""},
            {"scheduler": False},
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


class TemplateValidationTests(unittest.TestCase):
    def test_templates_load_rejects_renumbered_exports(self):
        # 回归 #P2-5:重新导出(节点重编号)的模板应在加载时给出明确错误,
        # 而不是运行期的 KeyError/IndexError。
        template_dir = APP_DIR / "templates"
        api = read_json(template_dir / "workflow_api.json")
        ui = read_json(template_dir / "workflow_ui.json")
        WorkflowTemplates(api, ui)  # 完整模板通过校验
        broken_api = {key: value for key, value in api.items() if key != "23"}
        with self.assertRaisesRegex(WorkflowError, "23"):
            WorkflowTemplates(broken_api, ui)
        broken_ui = {**ui, "nodes": [node for node in ui["nodes"] if node["id"] != 37]}
        with self.assertRaisesRegex(WorkflowError, "37"):
            WorkflowTemplates(api, broken_ui)


if __name__ == "__main__":
    unittest.main()
