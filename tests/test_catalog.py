import json
import random
import sys
import tempfile
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

from anima_webui.catalog import CatalogError, PromptCatalog, compose_people_tags  # noqa: E402
from anima_webui.custom_prompts import CustomPromptStore  # noqa: E402
from anima_webui.workflow import DEFAULT_SETTINGS, validate_settings  # noqa: E402


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        tools = root / "tools"
        js = tools / "js"
        js.mkdir(parents=True)
        datasets = {
            "character_data.js": [
                {"name": "alpha", "copyright": "series", "gender": "1girl", "hair": "blue", "eye": "red", "post_count": 20},
                {"name": "beta", "copyright": "series", "gender": "1boy", "hair": "black", "eye": "blue", "post_count": 10},
                {"name": "mystery", "copyright": "other", "post_count": 1},
            ],
            "clothing_data.js": [
                {"id": "coat", "name": "Coat", "name_zh": "外套", "tags": "red coat, long sleeves", "categories": ["日常/休闲 (Casual & Daily)"], "traits": ["red", "long sleeves"]},
                {"id": "dress", "name": "Dress", "name_zh": "礼服", "tags": "blue dress", "categories": ["礼服/裙装 (Dress & Gown)"], "traits": ["blue"]},
            ],
            "pose_data.js": [
                {"id": "stand", "name": "Standing", "name_zh": "站立", "tags": "standing", "categories": ["站立与动态 (Standing & Dynamic)"], "traits": ["standing"]},
                {"id": "sit", "name": "Sitting", "name_zh": "坐姿", "tags": "sitting", "categories": ["坐姿 (Sitting Poses)"], "traits": ["sitting"]},
                {"id": "point", "name": "Pointing", "name_zh": "指向", "tags": "pointing", "categories": ["手势与手臂 (Gestures & Arms)"], "traits": ["hand", "pointing"]},
                {"id": "fist", "name": "Fist", "name_zh": "握拳", "tags": "fist", "categories": ["手势与手臂 (Gestures & Arms)"], "traits": ["hand", "fist"]},
            ],
            "background_data.js": [
                {"id": "room", "name": "Room", "name_zh": "房间", "tags": "indoors", "categories": ["都市与日常 (Urban & Daily)"], "traits": ["indoor", "home"]},
                {"id": "forest", "name": "Forest", "name_zh": "森林", "tags": "forest", "categories": ["Nature & Outdoors"], "traits": ["outdoor", "greenery"]},
            ],
        }
        for filename, values in datasets.items():
            (js / filename).write_text(f"const data = {json.dumps(values)};\n", encoding="utf-8")
        official = {
            "alpha||series": {"trigger": "alpha, series", "tags": ["1girl", "blue eyes"]},
            "beta||series": {"trigger": "beta, series", "tags": ["1boy", "black hair"]},
        }
        (js / "character_official_data.json").write_text(json.dumps(official), encoding="utf-8")
        self.root = root
        self.catalog = PromptCatalog(root, tools)

    def tearDown(self):
        self.temp.cleanup()

    def test_search_and_stable_ids(self):
        result = self.catalog.search("clothing", "外套")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["id"], "clothing:coat")
        self.assertEqual(self.catalog.count("character"), 3)

    def test_anima_character_facets_and_combined_filters(self):
        result = self.catalog.search("character", gender="1girl", hair="blue", eye="red", series="series")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["favorite_key"], "alpha")
        self.assertEqual(result["items"][0]["post_count"], 20)
        self.assertEqual(self.catalog.search("character", gender="1girl")["total"], 1)
        genders = {item["value"]: item["count"] for item in result["facets"]["gender"]}
        self.assertEqual(genders, {"1boy": 1, "1girl": 1})

    def test_anima_pose_facets_traits_and_conflicts(self):
        filtered = self.catalog.search("pose", categories=["手势与手臂 (Gestures & Arms)"], traits=["hand", "pointing"])
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["items"][0]["conflict_slots"], ["hand_action"])
        same_slot = {"mode": "include", "ids": ["pose:point", "pose:fist"], "excluded_ids": []}
        with self.assertRaisesRegex(CatalogError, "手部动作"):
            self.catalog.resolve_selection("pose", same_slot, 2, random.Random(4))
        compatible = {"mode": "include", "ids": ["pose:point", "pose:stand"], "excluded_ids": []}
        first = self.catalog.resolve_selection("pose", compatible, 2, random.Random(9))
        second = self.catalog.resolve_selection("pose", compatible, 2, random.Random(9))
        self.assertEqual([item["id"] for item in first], [item["id"] for item in second])

    def test_anima_clothing_and_background_native_categories(self):
        clothing = self.catalog.search("clothing", categories=["Casual & Daily"], traits=["red", "long sleeves"])
        self.assertEqual([item["id"] for item in clothing["items"]], ["clothing:coat"])
        self.assertEqual(
            [item["value"] for item in clothing["facets"]["categories"]],
            ["Dress & Gown", "Casual & Daily"],
        )
        background = self.catalog.search("background", categories=["都市与日常 (Urban & Daily)"], traits=["indoor", "home"])
        self.assertEqual([item["id"] for item in background["items"]], ["background:room"])
        self.assertEqual(
            [item["value"] for item in background["facets"]["categories"]],
            ["Nature & Outdoors", "Urban & Daily"],
        )

    def test_custom_prompt_crud_updates_catalog(self):
        store = CustomPromptStore(self.root / "custom.json", self.catalog)
        item = store.create({"section": "pose", "title": "挥手", "prompt": "waving hand", "categories": ["Gestures & Arms"], "traits": ["hand"]})
        self.assertTrue(item["id"].startswith("custom:"))
        self.assertEqual(self.catalog.search("pose", "挥手")["total"], 1)
        updated = store.update(item["id"], {"title": "招手"})
        self.assertEqual(updated["prompt"], "waving hand")
        self.assertTrue(store.delete(item["id"]))
        self.assertEqual(self.catalog.search("pose", "招手")["total"], 0)

    def test_resolution_is_deterministic_and_strips_person_count_tags(self):
        settings = validate_settings({**DEFAULT_SETTINGS, "random_character": True, "random_character_count": 1, "pools": {**DEFAULT_SETTINGS["pools"], "character": {"mode": "all", "ids": [], "excluded_ids": []}}})
        self.catalog.validate_settings(settings)
        first = self.catalog.resolve_prompt(settings, 22)
        second = self.catalog.resolve_prompt(settings, 22)
        self.assertEqual(first, second)
        self.assertIn("1girl", first["composer_prompt"])
        self.assertNotIn("1boy", first["composer_prompt"])
        self.assertEqual(len(first["selected"]["character"]), 1)

    def test_expression_pool_is_builtin_and_adds_one_expression(self):
        self.assertGreater(self.catalog.count("expression"), 10)
        self.assertGreater(self.catalog.search("expression", categories=["愉悦"])["total"], 0)
        settings = validate_settings({
            **DEFAULT_SETTINGS,
            "random_expression": True,
            "random_expression_count": 1,
            "pools": {
                **DEFAULT_SETTINGS["pools"],
                "expression": {"mode": "all", "ids": [], "excluded_ids": []},
            },
        })
        resolved = self.catalog.resolve_prompt(settings, 44)
        self.assertEqual(len(resolved["selected"]["expression"]), 1)
        self.assertIn(resolved["selected"]["expression"][0]["tags"][0], resolved["full_prompt"])

        fixed = self.catalog.resolve_prompt(validate_settings({
            **DEFAULT_SETTINGS,
            "fixed_expression": "gentle smile, relaxed expression",
        }), 45)
        self.assertIn("gentle smile, relaxed expression", fixed["full_prompt"])
        self.assertEqual(fixed["selected"]["expression"], [])

    def test_custom_groups_and_import_preserve_overwritten_id(self):
        path = self.root / "custom.json"
        path.write_text(json.dumps({
            "version": 2,
            "items": [{"id": "custom:legacy", "section": "pose", "title": "Wave", "prompt": "waving"}],
        }), encoding="utf-8")
        store = CustomPromptStore(path, self.catalog)
        legacy_group = store.create_group("pose", {"name": "Legacy"})["group"]
        target_group = store.create_group("pose", {"name": "Batch"})["group"]
        store.update("custom:legacy", {"groupIds": [legacy_group["id"]]})
        self.assertEqual(
            {value["name"]: value["count"] for value in store.list_groups("pose")["groups"]},
            {"Legacy": 1, "Batch": 0},
        )
        preview = store.preview_import("json", json.dumps({"items": [
            {"section": "pose", "title": "Wave", "prompt": "waving hand", "groups": ["Hands", "Daily"]},
        ]}), "pose")
        self.assertEqual(preview["summary"], {"new": 0, "conflict": 1, "error": 0})
        preview["rows"][0]["action"] = "overwrite"
        result = store.commit_import(preview["rows"], "pose", [target_group["id"]])
        self.assertEqual(result["updated"], 1)
        self.assertEqual(store.list("pose")[0]["id"], "custom:legacy")
        self.assertEqual(store.list("pose")[0]["prompt"], "waving hand")
        groups = store.list_groups("pose")["groups"]
        self.assertEqual({value["name"] for value in groups}, {"Legacy", "Hands", "Batch", "Daily"})
        group_names = {value["id"]: value["name"] for value in groups}
        self.assertEqual({group_names[value] for value in store.list("pose")[0]["groupIds"]}, {"Hands", "Batch", "Daily"})
        self.assertEqual(next(value for value in groups if value["name"] == "Legacy")["count"], 0)

    def test_templates_are_specific_to_each_section_and_format(self):
        store = CustomPromptStore(self.root / "custom.json", self.catalog)
        expected_titles = {
            "character": "示例角色",
            "clothing": "休闲连帽衫",
            "pose": "站立挥手",
            "background": "樱花街道",
            "expression": "期待",
        }
        for section, title in expected_titles.items():
            with self.subTest(section=section, format="json"):
                filename, content_type, body = store.template(section, "json")
                item = json.loads(body)["items"][0]
                self.assertEqual(filename, f"custom-prompts-{section}-template.json")
                self.assertEqual(content_type, "application/json")
                self.assertEqual(item["section"], section)
                self.assertEqual(item["title"], title)
            with self.subTest(section=section, format="csv"):
                filename, content_type, body = store.template(section, "csv")
                text = body.decode("utf-8-sig")
                self.assertEqual(filename, f"custom-prompts-{section}-template.csv")
                self.assertEqual(content_type, "text/csv")
                self.assertIn(f"{section},{title},", text)

    def test_import_rejects_duplicate_rows_and_tampered_builtin_overwrite(self):
        store = CustomPromptStore(self.root / "custom.json", self.catalog)
        preview = store.preview_import("json", json.dumps({"items": [
            {"section": "expression", "title": "My Mood", "prompt": "soft smile"},
            {"section": "expression", "title": "My Mood", "prompt": "angry face"},
        ]}), "expression")
        self.assertEqual(preview["summary"], {"new": 1, "conflict": 0, "error": 1})
        self.assertIn("重名", preview["rows"][1]["error"])

        cross_section = store.preview_import("json", json.dumps({"items": [
            {"section": "background", "title": "Wrong Pool", "prompt": "street"},
        ]}), "expression")
        self.assertEqual(cross_section["summary"], {"new": 0, "conflict": 0, "error": 1})
        self.assertIn("当前池", cross_section["rows"][0]["error"])

        with self.assertRaisesRegex(CatalogError, "不存在的分组"):
            store.commit_import([{
                "action": "create",
                "item": {"section": "expression", "title": "New Mood", "prompt": "soft face"},
            }], "expression", ["custom_group_missing"])
        self.assertEqual(store.list("expression"), [])

        pose_group = store.create_group("pose", {"name": "Pose Only"})["group"]
        with self.assertRaisesRegex(CatalogError, "不存在的分组"):
            store.commit_import([{
                "action": "create",
                "item": {"section": "expression", "title": "Cross Group", "prompt": "soft face"},
            }], "expression", [pose_group["id"]])
        with self.assertRaisesRegex(CatalogError, "当前池"):
            store.commit_import([{
                "action": "create",
                "item": {"section": "pose", "title": "Tampered", "prompt": "waving"},
            }], "expression")
        self.assertEqual(store.list("expression"), [])

        with self.assertRaisesRegex(CatalogError, "内置条目不可覆盖"):
            store.commit_import([{
                "action": "create",
                "item": {"section": "expression", "title": "温柔微笑", "prompt": "tampered"},
            }], "expression")
        with self.assertRaisesRegex(CatalogError, "没有可覆盖"):
            store.commit_import([{
                "action": "overwrite",
                "item": {"section": "expression", "title": "Missing", "prompt": "missing"},
            }], "expression")

    def test_empty_pool_and_excess_count_are_rejected(self):
        settings = validate_settings({**DEFAULT_SETTINGS, "random_pose": True})
        with self.assertRaises(CatalogError):
            self.catalog.validate_settings(settings)
        settings["pools"]["pose"] = {"mode": "all", "ids": [], "excluded_ids": []}
        settings["random_pose_count"] = 5
        with self.assertRaises(CatalogError):
            self.catalog.validate_settings(settings)

    def test_people_tags_and_people_validation(self):
        self.assertEqual(compose_people_tags(2, 1), ["2girls", "1boy"])
        with self.assertRaisesRegex(ValueError, "总人数"):
            validate_settings({**DEFAULT_SETTINGS, "female_count": 4, "male_count": 2})


if __name__ == "__main__":
    unittest.main()
