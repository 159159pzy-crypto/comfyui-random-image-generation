import json
import random
import sys
import tempfile
import unittest
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

from anima_webui.catalog import CatalogError, PromptCatalog, compose_people_tags  # noqa: E402
from anima_webui.custom_prompts import MAX_GROUPS_PER_SECTION, CustomPromptStore  # noqa: E402
from anima_webui.workflow import DEFAULT_SETTINGS, validate_settings  # noqa: E402


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        tools = root / "tools"
        js = tools / "js"
        js.mkdir(parents=True)
        datasets = {
            "character_data.js": [
                {"name": "hatsune miku", "copyright": "vocaloid", "gender": "1girl", "hair": "blue", "eye": "blue", "post_count": 99},
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
        self.assertEqual(self.catalog.count("character"), 4)

    def test_anima_character_facets_and_combined_filters(self):
        result = self.catalog.search("character", gender="1girl", hair="blue", eye="red", series="series")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["favorite_key"], "alpha")
        self.assertEqual(result["items"][0]["post_count"], 20)
        self.assertEqual(self.catalog.search("character", gender="1girl")["total"], 2)
        genders = {item["value"]: item["count"] for item in result["facets"]["gender"]}
        self.assertEqual(genders, {"1boy": 1, "1girl": 2})

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

    def test_builtin_zh_names_apply_to_characters(self):
        # 热门角色/系列的内置译名:标题、系列、facet 标签与中文搜索。
        result = self.catalog.search("character", "初音")
        self.assertEqual(result["total"], 1)
        entry = result["items"][0]
        self.assertEqual(entry["title"], "hatsune miku")
        self.assertEqual(entry["title_zh"], "初音未来")
        self.assertEqual(entry["subtitle_zh"], "VOCALOID")
        unmapped = self.catalog.search("character", "alpha")["items"][0]
        self.assertEqual(unmapped["title_zh"], "")
        series = {item["value"]: item["label_zh"] for item in self.catalog.facets("character")["series"]}
        self.assertEqual(series["vocaloid"], "VOCALOID")

    def test_facets_carry_chinese_labels(self):
        # 双语分类「中文 (English)」的中文半边随 facet 返回;性别值给出中文标签。
        clothing = self.catalog.facets("clothing")["categories"]
        labels = {entry["value"]: entry["label_zh"] for entry in clothing}
        self.assertEqual(labels["Casual & Daily"], "日常/休闲")
        self.assertEqual(labels["Dress & Gown"], "礼服/裙装")
        expression = self.catalog.facets("expression")["categories"]
        self.assertTrue(all(entry["label_zh"] == entry["value"] for entry in expression))
        gender = {entry["value"]: entry["label_zh"] for entry in self.catalog.facets("character")["gender"]}
        self.assertEqual(gender["1girl"], "女性")
        self.assertEqual(gender["1boy"], "男性")

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

    async def test_custom_prompt_crud_updates_catalog(self):
        store = CustomPromptStore(self.root / "custom.json", self.catalog)
        item = await store.create({"section": "pose", "title": "挥手", "prompt": "waving hand", "categories": ["Gestures & Arms"], "traits": ["hand"]})
        self.assertTrue(item["id"].startswith("custom:"))
        self.assertEqual(self.catalog.search("pose", "挥手")["total"], 1)
        updated = await store.update(item["id"], {"title": "招手"})
        self.assertEqual(updated["prompt"], "waving hand")
        self.assertTrue(await store.delete(item["id"]))
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

    def test_include_selection_keeps_selection_order(self):
        # 回归:include 候选曾经过 set 去重,迭代顺序随进程哈希种子漂移,
        # 固定 prompt_seed 的"复现"在 WebUI 重启后会抽出不同条目。
        customs = [
            {"id": f"custom:c{i:02d}", "section": "clothing", "title": f"C{i}", "prompt": f"c{i}"}
            for i in range(16)
        ]
        self.catalog.set_custom_items(customs)
        ids = [f"custom:c{i:02d}" for i in (9, 2, 14, 0, 7, 11, 4, 15, 1, 8)] + ["clothing:dress", "clothing:coat"]
        selection = {"mode": "include", "ids": ids, "excluded_ids": []}
        candidates = self.catalog._selection_candidates("clothing", selection)
        self.assertEqual([item["id"] for item in candidates], ids)
        # 重复 id 去重后保持首次出现的顺序
        dup = {"mode": "include", "ids": ["clothing:dress", "custom:c03", "clothing:dress"], "excluded_ids": []}
        self.assertEqual(
            [item["id"] for item in self.catalog._selection_candidates("clothing", dup)],
            ["clothing:dress", "custom:c03"],
        )
        first = self.catalog.resolve_selection("clothing", selection, 3, random.Random(42))
        second = self.catalog.resolve_selection("clothing", selection, 3, random.Random(42))
        self.assertEqual([item["id"] for item in first], [item["id"] for item in second])

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

    def test_reload_with_legacy_item_id_backs_up_and_starts_empty(self):
        # 回归:不带 custom: 前缀的 id 能通过 _normalize,却在 try 块之外的
        # set_custom_items 处抛 CatalogError——服务器无法启动且不产生备份,
        # 目录还被留在"内置项已剥离、_by_id 过期"的半修改状态。
        path = self.root / "custom.json"
        path.write_text(json.dumps({
            "items": [{"id": "legacy-001", "section": "pose", "title": "旧条目", "prompt": "standing"}],
        }), encoding="utf-8")
        store = CustomPromptStore(path, self.catalog)
        self.assertEqual(store.items, [])
        self.assertEqual(len(store.load_warnings), 1)
        self.assertIn("custom.json.corrupt.bak", store.load_warnings[0])
        self.assertTrue(path.with_name("custom.json.corrupt.bak").is_file())
        # 目录回到一致状态:自定义项清空,内置项完好可检索
        self.assertEqual(self.catalog.count("pose"), 4)
        self.assertTrue(all(item["builtin"] for item in self.catalog.all_items("pose")))
        self.assertEqual(self.catalog.search("pose", "站立")["total"], 1)

    def test_reload_with_malformed_shapes_backs_up_or_degrades(self):
        # 回归:groups/items 为 null/错误类型的手改文件曾以 AttributeError/TypeError
        # 炸掉启动(不在保护的异常列表里,也不产生备份)。
        # null 视为空数据继续;错误类型视为坏文件,备份后以空数据启动。
        path = self.root / "custom.json"
        path.write_text(json.dumps({"groups": None, "items": None}), encoding="utf-8")
        store = CustomPromptStore(path, self.catalog)
        self.assertEqual(store.items, [])
        self.assertEqual(store.load_warnings, [])
        for payload in ('{"groups": [], "items": 123}', '{"groups": "x", "items": []}', '[1, 2, 3]'):
            with self.subTest(payload=payload):
                path.write_text(payload, encoding="utf-8")
                store = CustomPromptStore(path, self.catalog)
                self.assertEqual(store.items, [])
                self.assertEqual(len(store.load_warnings), 1)
                self.assertEqual(self.catalog.count("pose"), 4)

    async def test_custom_groups_and_import_preserve_overwritten_id(self):
        path = self.root / "custom.json"
        path.write_text(json.dumps({
            "version": 2,
            "items": [{"id": "custom:legacy", "section": "pose", "title": "Wave", "prompt": "waving"}],
        }), encoding="utf-8")
        store = CustomPromptStore(path, self.catalog)
        migrated = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(migrated["version"], 4)
        self.assertTrue(path.with_name("custom.json.pre-v4.bak").is_file())
        history = next(group for group in store.list_groups("pose")["groups"] if group["kind"] == "legacy")
        history_children = [
            group for group in store.list_groups("pose")["groups"] if group.get("parentId") == history["id"]
        ]
        self.assertEqual([group["name"] for group in history_children], ["未分组"])
        self.assertEqual(history["count"], 1)
        legacy_group = (await store.create_group("pose", {"name": "Legacy"}))["group"]
        target_group = (await store.create_group("pose", {"name": "Batch"}))["group"]
        await store.update("custom:legacy", {"groupIds": [legacy_group["id"]]})
        counts = {value["id"]: value["count"] for value in store.list_groups("pose")["groups"]}
        self.assertEqual(counts[legacy_group["id"]], 1)
        self.assertEqual(counts[target_group["id"]], 0)
        preview = store.preview_import("json", json.dumps({"items": [
            {"section": "pose", "title": "Wave", "prompt": "waving hand", "groups": ["Hands", "Daily"]},
        ]}), "pose", "Pose Pack")
        self.assertEqual(preview["summary"], {"new": 0, "conflict": 1, "error": 0})
        preview["rows"][0]["action"] = "overwrite"
        result = await store.commit_import(preview["rows"], "pose", [target_group["id"]], "Pose Pack")
        self.assertEqual(result["updated"], 1)
        self.assertEqual(store.list("pose")[0]["id"], "custom:legacy")
        self.assertEqual(store.list("pose")[0]["prompt"], "waving hand")
        groups = store.list_groups("pose")["groups"]
        bundle = next(value for value in groups if value["kind"] == "import" and value["name"] == "Pose Pack")
        children = [value for value in groups if value.get("parentId") == bundle["id"]]
        self.assertEqual({value["name"] for value in children}, {"Hands", "Daily"})
        self.assertEqual(bundle["count"], 1)
        group_names = {value["id"]: value["name"] for value in groups}
        self.assertEqual({group_names[value] for value in store.list("pose")[0]["groupIds"]}, {"Hands", "Batch", "Daily"})
        self.assertEqual(next(value for value in groups if value["name"] == "Legacy")["count"], 0)

        second = store.preview_import("json", json.dumps({"items": [
            {"section": "pose", "title": "Step", "prompt": "one step", "groups": ["Hands"]},
            {"section": "pose", "title": "Plain", "prompt": "plain pose"},
        ]}), "pose", " pose pack ")
        committed = await store.commit_import(second["rows"], "pose", [], " pose pack ")
        self.assertEqual(committed["bundleId"], bundle["id"])
        groups = store.list_groups("pose")["groups"]
        children = [value for value in groups if value.get("parentId") == bundle["id"]]
        self.assertEqual({value["name"] for value in children}, {"Hands", "Daily", "未分组"})
        self.assertEqual(next(value for value in groups if value["id"] == bundle["id"])["count"], 3)
        self.assertEqual(len(store.list("pose", bundle["id"])), 3)

    async def test_import_bundle_subtree_delete_preserves_shared_items(self):
        store = CustomPromptStore(self.root / "custom.json", self.catalog)
        manual = (await store.create_group("expression", {"name": "Keep"}))["group"]
        preview = store.preview_import("json", json.dumps({"items": [
            {"section": "expression", "title": "Exclusive", "prompt": "exclusive face", "groups": ["Mood"]},
            {"section": "expression", "title": "Shared", "prompt": "shared face"},
        ]}), "expression", "Faces")
        committed = await store.commit_import(preview["rows"], "expression", [manual["id"]], "Faces")
        bundle_id = committed["bundleId"]
        bundle = next(group for group in store.list_groups("expression")["groups"] if group["id"] == bundle_id)
        self.assertEqual(bundle["count"], 2)
        self.assertEqual(bundle["exclusiveCount"], 0)

        # 让 Exclusive 只属于导入子树,Shared 同时属于手动分组。
        values = {item["title"]: item for item in store.list("expression")}
        await store.update(
            values["Exclusive"]["id"],
            {"groupIds": [value for value in values["Exclusive"]["groupIds"] if value != manual["id"]]},
        )
        deleted = await store.delete_group("expression", bundle_id, delete_items=True)
        self.assertEqual(deleted["deletedGroupCount"], 3)
        self.assertEqual(deleted["deletedItemCount"], 1)
        self.assertEqual([item["title"] for item in store.list("expression")], ["Shared"])
        self.assertEqual(store.list("expression")[0]["groupIds"], [manual["id"]])

    async def test_import_bundle_subtree_delete_all_removes_shared_items(self):
        store = CustomPromptStore(self.root / "custom-all.json", self.catalog)
        manual = (await store.create_group("expression", {"name": "Keep"}))["group"]
        preview = store.preview_import("json", json.dumps({"items": [
            {"section": "expression", "title": "Exclusive", "prompt": "exclusive face", "groups": ["Mood"]},
            {"section": "expression", "title": "Shared", "prompt": "shared face"},
        ]}), "expression", "Faces")
        committed = await store.commit_import(preview["rows"], "expression", [manual["id"]], "Faces")
        bundle_id = committed["bundleId"]

        deleted = await store.delete_group("expression", bundle_id, delete_mode="all")

        self.assertEqual(deleted["deleteMode"], "all")
        self.assertEqual(deleted["deletedGroupCount"], 3)
        self.assertEqual(deleted["deletedItemCount"], 2)
        self.assertEqual(store.list("expression"), [])
        self.assertEqual(
            [group["name"] for group in store.list_groups("expression")["groups"]],
            ["Keep"],
        )

    def test_import_preview_rejects_group_tree_overflow(self):
        store = CustomPromptStore(self.root / "custom.json", self.catalog)
        store.groups["expression"] = [
            {"id": f"custom_group_{index}", "name": f"Group {index}", "parentId": None, "kind": "group"}
            for index in range(MAX_GROUPS_PER_SECTION)
        ]
        with self.assertRaisesRegex(CatalogError, "数量已达到上限"):
            store.preview_import("json", json.dumps({"items": [
                {"section": "expression", "title": "Overflow", "prompt": "overflow"},
            ]}), "expression", "Overflow Pack")
        self.assertFalse((self.root / "custom.json").exists())

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

    async def test_import_rejects_duplicate_rows_and_tampered_builtin_overwrite(self):
        store = CustomPromptStore(self.root / "custom.json", self.catalog)
        preview = store.preview_import("json", json.dumps({"items": [
            {"section": "expression", "title": "My Mood", "prompt": "soft smile"},
            {"section": "expression", "title": "My Mood", "prompt": "angry face"},
        ]}), "expression", "Mood Pack")
        self.assertEqual(preview["summary"], {"new": 1, "conflict": 0, "error": 1})
        self.assertIn("重名", preview["rows"][1]["error"])

        cross_section = store.preview_import("json", json.dumps({"items": [
            {"section": "background", "title": "Wrong Pool", "prompt": "street"},
        ]}), "expression", "Mood Pack")
        self.assertEqual(cross_section["summary"], {"new": 0, "conflict": 0, "error": 1})
        self.assertIn("当前池", cross_section["rows"][0]["error"])

        with self.assertRaisesRegex(CatalogError, "不存在的分组"):
            await store.commit_import([{
                "action": "create",
                "item": {"section": "expression", "title": "New Mood", "prompt": "soft face"},
            }], "expression", ["custom_group_missing"], "Mood Pack")
        self.assertEqual(store.list("expression"), [])

        pose_group = (await store.create_group("pose", {"name": "Pose Only"}))["group"]
        with self.assertRaisesRegex(CatalogError, "不存在的分组"):
            await store.commit_import([{
                "action": "create",
                "item": {"section": "expression", "title": "Cross Group", "prompt": "soft face"},
            }], "expression", [pose_group["id"]], "Mood Pack")
        with self.assertRaisesRegex(CatalogError, "当前池"):
            await store.commit_import([{
                "action": "create",
                "item": {"section": "pose", "title": "Tampered", "prompt": "waving"},
            }], "expression", [], "Mood Pack")
        self.assertEqual(store.list("expression"), [])

        with self.assertRaisesRegex(CatalogError, "内置条目不可覆盖"):
            await store.commit_import([{
                "action": "create",
                "item": {"section": "expression", "title": "温柔微笑", "prompt": "tampered"},
            }], "expression", [], "Mood Pack")
        with self.assertRaisesRegex(CatalogError, "没有可覆盖"):
            await store.commit_import([{
                "action": "overwrite",
                "item": {"section": "expression", "title": "Missing", "prompt": "missing"},
            }], "expression", [], "Mood Pack")

        valid = store.preview_import("json", json.dumps({"items": [
            {"section": "expression", "title": "Valid", "prompt": "valid face"},
        ]}), "expression", "Valid Pack")
        created = await store.commit_import(valid["rows"], "expression", [], "Valid Pack")
        with self.assertRaisesRegex(CatalogError, "叶子分组"):
            await store.commit_import([{
                "action": "create",
                "item": {"section": "expression", "title": "Folder Target", "prompt": "invalid target"},
            }], "expression", [created["bundleId"]], "Other Pack")
        self.assertEqual(self.catalog.search("expression", "Folder Target")["total"], 0)

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
