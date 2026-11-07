import asyncio
import copy
import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from anima_webui.catalog import CatalogError
from anima_webui.favorites import FavoritesService, FavoritesStore, _group_stats, normalize_section, sync_source_fields


class FakeComfy(FavoritesStore):
    """Temporary local store; payload stays convenient for grouping fixtures."""
    def __init__(self, payload=None):
        self._temporary = tempfile.TemporaryDirectory()
        super().__init__(Path(self._temporary.name) / "favorites.json")
        self.profiles = {"anima": copy.deepcopy(payload or {})}
        if payload:
            self._write_text(self._serialize(self.profiles))
            self._source_text = self.path.read_text(encoding="utf-8")

    @property
    def payload(self):
        return self.profiles["anima"]

    async def favorites(self):
        raise AssertionError("External favorites must not be read")

    async def save_favorites(self, payload):
        raise AssertionError("External favorites must not be written")


class FakeCatalog:
    def __init__(self, items=None):
        self.items = {item["id"]: copy.deepcopy(item) for item in items or []}

    def get(self, item_id):
        item = self.items.get(item_id)
        return copy.deepcopy(item) if item else None

    def all_items(self, section):
        return [copy.deepcopy(item) for item in self.items.values() if item.get("section") == section]


def custom_item(item_id, title):
    return {
        "id": item_id,
        "favorite_key": item_id,
        "section": "pose",
        "title": title,
        "prompt": title.lower(),
        "builtin": False,
    }


class FavoriteNormalizationTests(unittest.TestCase):
    def test_source_sync_updates_default_names_and_preserves_aliases(self):
        old = {"id": "custom:one", "title": "Old", "prompt": "old text"}
        new = {"id": "custom:one", "title": "New", "prompt": "new text", "subtitle": "New subtitle"}
        for nickname, expected in (("", "New"), ("Old", "New"), ("My alias", "My alias")):
            with self.subTest(nickname=nickname):
                saved = {"id": "custom:one", "nickname": nickname, "groupIds": ["private"]}
                sync_source_fields(saved, new, old)
                self.assertEqual(saved["nickname"], expected)
                self.assertEqual(saved["customContent"], "new text")
                self.assertEqual(saved["subtitle"], "New subtitle")
                self.assertEqual(saved["groupIds"], ["private"])

    def test_legacy_source_sync_preserves_unknown_alias_and_favorite_identity(self):
        saved = {"id": "favorite_kept", "name": "favorite_kept", "title": "Legacy title",
                 "nickname": "Legacy alias", "groupIds": ["private"], "text": "old"}
        sync_source_fields(saved, {"id": "prompt_source", "title": "Current", "text": "new"}, prompt=True)
        self.assertEqual(saved["id"], "favorite_kept")
        self.assertEqual(saved["name"], "favorite_kept")
        self.assertEqual(saved["title"], "Legacy title")
        self.assertEqual(saved["nickname"], "Legacy alias")
        self.assertEqual(saved["source_item_id"], "prompt_source")
        self.assertEqual(saved["text"], saved["customContent"])
        self.assertEqual(saved["text"], "new")
        self.assertEqual(saved["groupIds"], ["private"])

    def test_legacy_groups_and_corrupt_hierarchy_are_repaired_without_data_loss(self):
        current = normalize_section(
            {
                "groups": [
                    {"id": "default", "name": "Default", "parentId": "legacy"},
                    {"id": "legacy", "name": "Legacy"},
                    {"id": "missing", "name": "Missing parent", "parentId": "gone"},
                    {"id": "cycle_a", "name": "Cycle A", "parentId": "cycle_b"},
                    {"id": "cycle_b", "name": "Cycle B", "parentId": "cycle_a"},
                ],
                "items": [{"id": "one", "groupIds": ["legacy", "gone"]}],
            }
        )
        groups = {group["id"]: group for group in current["groups"]}
        self.assertIsNone(groups["default"]["parentId"])
        self.assertIsNone(groups["legacy"]["parentId"])
        self.assertIsNone(groups["missing"]["parentId"])
        self.assertTrue(
            groups["cycle_a"]["parentId"] is None
            or groups["cycle_b"]["parentId"] is None
        )
        self.assertEqual(current["items"][0]["groupIds"], ["legacy"])

    def test_stats_distinguish_direct_total_child_and_exclusive_counts(self):
        current = normalize_section(
            {
                "groups": [
                    {"id": "default", "name": "Default"},
                    {"id": "parent", "name": "Parent"},
                    {"id": "leaf", "name": "Leaf", "parentId": "parent"},
                ],
                "items": [
                    {"id": "direct", "groupIds": ["parent"]},
                    {"id": "leaf-only", "groupIds": ["leaf"]},
                    {"id": "shared", "groupIds": ["leaf", "default"]},
                ],
            }
        )
        stats = {group["id"]: group for group in _group_stats(current)}
        self.assertEqual(
            {
                key: stats["parent"][key]
                for key in ("directCount", "totalCount", "childCount", "exclusiveCount")
            },
            {"directCount": 1, "totalCount": 3, "childCount": 1, "exclusiveCount": 1},
        )
        self.assertEqual(stats["leaf"]["directCount"], 2)
        self.assertEqual(stats["leaf"]["totalCount"], 2)
        self.assertEqual(stats["leaf"]["exclusiveCount"], 1)


class FavoriteServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_custom_preserves_alias_and_all_group_memberships(self):
        item = custom_item("custom:one", "Old")
        comfy = FakeComfy({"pose": {"groups": [{"id": "default", "name": "Default"}], "items": [
            {"id": item["id"], "isCustom": True, "nickname": "My alias", "groupIds": ["default"]},
        ]}})
        service = FavoritesService(comfy, FakeCatalog([item]))
        await service.sync_custom("pose", {**item, "title": "New", "prompt": "new prompt"}, item)
        saved = (await service.get("pose"))["items"][0]
        self.assertEqual(saved["nickname"], "My alias")
        self.assertEqual(saved["customContent"], "new prompt")
        self.assertEqual(saved["groupIds"], ["default"])

    async def test_batch_favorite_selection_creates_and_appends_groups(self):
        first = custom_item("custom:first", "First")
        second = custom_item("custom:second", "Second")
        comfy = FakeComfy(
            {
                "pose": {
                    "groups": [
                        {"id": "default", "name": "Default"},
                        {"id": "g1", "name": "G1"},
                        {"id": "g2", "name": "G2"},
                    ],
                    "items": [
                        {
                            "id": "custom:first",
                            "name": "custom:first",
                            "nickname": "keep me",
                            "customContent": "first",
                            "groupIds": ["g1"],
                            "isCustom": True,
                        }
                    ],
                }
            }
        )
        service = FavoritesService(comfy, FakeCatalog([first, second]))

        payload = await service.update_selection(
            "pose",
            {"mode": "include", "ids": ["custom:first", "custom:second", "custom:first"]},
            ["g2", "default", "g2"],
        )

        self.assertEqual(payload["selectedCount"], 2)
        self.assertEqual(payload["createdCount"], 1)
        self.assertEqual(payload["updatedCount"], 1)
        self.assertEqual(payload["groupIds"], ["g2", "default"])
        items = {item["id"]: item for item in payload["items"]}
        self.assertEqual(items["custom:first"]["groupIds"], ["g1", "g2", "default"])
        self.assertEqual(items["custom:first"]["nickname"], "keep me")
        self.assertEqual(items["custom:second"]["groupIds"], ["g2", "default"])

    async def test_batch_favorite_all_excludes_items_and_skips_unchanged(self):
        items = [custom_item(f"custom:{name}", name.title()) for name in ("one", "two", "three")]
        comfy = FakeComfy(
            {
                "pose": {
                    "groups": [{"id": "default", "name": "Default"}, {"id": "g1", "name": "G1"}],
                    "items": [
                        {
                            "id": "custom:one",
                            "name": "custom:one",
                            "nickname": "One",
                            "customContent": "one",
                            "groupIds": ["g1"],
                            "isCustom": True,
                        }
                    ],
                }
            }
        )
        payload = await FavoritesService(comfy, FakeCatalog(items)).update_selection(
            "pose", {"mode": "all", "excluded_ids": ["custom:three"]}, ["g1"]
        )
        self.assertEqual(payload["selectedCount"], 2)
        self.assertEqual(payload["createdCount"], 1)
        self.assertEqual(payload["updatedCount"], 0)
        self.assertEqual({item["id"] for item in payload["items"]}, {"custom:one", "custom:two"})

    async def test_batch_favorite_rejects_invalid_payload_without_saving(self):
        item = custom_item("custom:first", "First")
        original = {
            "pose": {
                "groups": [{"id": "default", "name": "Default"}],
                "items": [],
            }
        }
        invalid_cases = [
            (None, ["default"]),
            ({"mode": "invalid", "ids": [item["id"]]}, ["default"]),
            ({"mode": "include", "ids": []}, ["default"]),
            ({"mode": "include", "ids": ["missing"]}, ["default"]),
            ({"mode": "include", "ids": [item["id"]]}, []),
            ({"mode": "include", "ids": [item["id"]]}, ["missing"]),
        ]
        for selection, group_ids in invalid_cases:
            with self.subTest(selection=selection, group_ids=group_ids):
                comfy = FakeComfy(original)
                with self.assertRaises(CatalogError):
                    await FavoritesService(comfy, FakeCatalog([item])).update_selection(
                        "pose", selection, group_ids
                    )
                self.assertEqual(comfy.payload, original)
    async def test_snapshot_import_supports_depth_and_rejects_duplicate_siblings(self):
        first = custom_item("custom:first", "First")
        second = custom_item("custom:second", "Second")
        comfy = FakeComfy(
            {
                "pose": {
                    "groups": [
                        {"id": "default", "name": "Default"},
                        {"id": "parent_a", "name": "Parent A"},
                        {"id": "parent_b", "name": "Parent B"},
                    ],
                    "items": [],
                }
            }
        )
        service = FavoritesService(comfy, FakeCatalog([first, second]))
        imported = await service.import_custom_group(
            "pose", "parent_a", {"id": "source", "name": "Snapshot"}, [first, second]
        )
        child = imported["group"]
        self.assertEqual(child["parentId"], "parent_a")
        self.assertEqual(child["sourceCustomGroupId"], "source")
        self.assertEqual({item["id"] for item in imported["items"]}, {"custom:first", "custom:second"})
        with self.assertRaises(CatalogError):
            await service.import_custom_group(
                "pose", "parent_a", {"id": "source", "name": "Snapshot"}, [first]
            )

        nested = await service.import_custom_group(
            "pose", child["id"], {"id": "nested-source", "name": "Nested"}, [first]
        )
        self.assertEqual(nested["group"]["parentId"], child["id"])
        elsewhere = await service.import_custom_group(
            "pose", "parent_b", {"id": "source", "name": "Snapshot"}, [first]
        )
        self.assertEqual(elsewhere["group"]["parentId"], "parent_b")
        with self.assertRaises(CatalogError):
            await service.import_custom_group(
                "pose", "parent_b", {"id": "empty", "name": "Empty"}, []
            )

    async def test_refavorite_without_group_ids_preserves_memberships(self):
        # 回归:payload 不带 groupIds 的重复收藏(前端 toggleFavorite 就是这么发的)
        # 曾把分组重置为 ["default"],悄悄摧毁用户的分组整理。
        item = custom_item("custom:first", "First")
        comfy = FakeComfy(
            {
                "pose": {
                    "groups": [
                        {"id": "default", "name": "Default"},
                        {"id": "g1", "name": "G1"},
                        {"id": "g2", "name": "G2"},
                    ],
                    "items": [],
                }
            }
        )
        service = FavoritesService(comfy, FakeCatalog([item]))
        await service.update_item("pose", {"id": "custom:first", "favorite": True, "groupIds": ["g1", "g2"]})
        result = await service.update_item("pose", {"id": "custom:first", "favorite": True})
        self.assertEqual(result["items"][0]["groupIds"], ["g1", "g2"])
        # 显式携带 groupIds 时仍然生效
        result = await service.update_item("pose", {"id": "custom:first", "favorite": True, "groupIds": ["g2"]})
        self.assertEqual(result["items"][0]["groupIds"], ["g2"])

    async def test_artist_refavorite_without_group_ids_preserves_memberships(self):
        # 回归:同一缺陷的画师路径——前端"保存当前画师"对每个画师无条件发
        # favorite:true,不带 groupIds,曾把已分组的画师全部打回默认分组。
        comfy = FakeComfy(
            {
                "artist": {
                    "groups": [{"id": "default", "name": "Default"}, {"id": "g1", "name": "G1"}],
                    "items": [],
                }
            }
        )
        service = FavoritesService(comfy, FakeCatalog())
        await service.update_item("artist", {"name": "@anmi", "favorite": True, "groupIds": ["g1"]})
        result = await service.update_item("artist", {"name": "@anmi", "favorite": True})
        self.assertEqual(result["items"][0]["groupIds"], ["g1"])

    async def test_parent_delete_removes_subtree_and_moves_orphans_to_default(self):
        comfy = FakeComfy(
            {
                "pose": {
                    "groups": [
                        {"id": "default", "name": "Default"},
                        {"id": "parent", "name": "Parent"},
                        {"id": "child", "name": "Child", "parentId": "parent"},
                        {"id": "other", "name": "Other"},
                    ],
                    "items": [
                        {"id": "orphan", "groupIds": ["child"]},
                        {"id": "shared", "groupIds": ["child", "other"]},
                    ],
                }
            }
        )
        payload = await FavoritesService(comfy, FakeCatalog()).delete_group("pose", "parent")
        self.assertEqual(set(payload["deletedGroupIds"]), {"parent", "child"})
        self.assertEqual(payload["movedToDefaultCount"], 1)
        items = {item["id"]: item for item in payload["items"]}
        self.assertEqual(items["orphan"]["groupIds"], ["default"])
        self.assertEqual(items["shared"]["groupIds"], ["other"])

    async def test_delete_items_is_limited_to_nonempty_child_leaves(self):
        comfy = FakeComfy(
            {
                "pose": {
                    "groups": [
                        {"id": "default", "name": "Default"},
                        {"id": "parent", "name": "Parent"},
                        {"id": "leaf", "name": "Leaf", "parentId": "parent"},
                        {"id": "other", "name": "Other"},
                    ],
                    "items": [
                        {"id": "exclusive", "groupIds": ["leaf"]},
                        {"id": "shared", "groupIds": ["leaf", "other"]},
                    ],
                }
            }
        )
        service = FavoritesService(comfy, FakeCatalog())
        with self.assertRaises(CatalogError):
            await service.delete_group("pose", "parent", delete_items=True)
        payload = await service.delete_group("pose", "leaf", delete_items=True)
        self.assertEqual(payload["deletedFavoriteCount"], 1)
        self.assertEqual(payload["detachedItemCount"], 1)
        self.assertEqual([item["id"] for item in payload["items"]], ["shared"])
        self.assertEqual(payload["items"][0]["groupIds"], ["other"])

    async def test_removing_last_item_preserves_all_groups(self):
        item = custom_item("custom:one", "One")
        comfy = FakeComfy(
            {
                "pose": {
                    "groups": [
                        {"id": "default", "name": "Default"},
                        {"id": "parent", "name": "Parent"},
                        {"id": "leaf", "name": "Leaf", "parentId": "parent"},
                        {"id": "unrelated", "name": "Unrelated", "parentId": "parent"},
                    ],
                    "items": [{"id": "custom:one", "groupIds": ["leaf"], "isCustom": True}],
                }
            }
        )
        payload = await FavoritesService(comfy, FakeCatalog([item])).update_item(
            "pose", {"id": "custom:one", "favorite": False}
        )
        group_ids = {group["id"] for group in payload["groups"]}
        self.assertEqual(group_ids, {"default", "parent", "leaf", "unrelated"})
        self.assertEqual(payload["items"], [])

    async def test_source_cancellation_maps_builtin_key_and_removes_all_references(self):
        source = {"id": "character:one", "favorite_key": "one||series", "section": "character"}
        comfy = FakeComfy({"character": {
            "groups": [{"id": "default", "name": "Default"}, {"id": "parent", "name": "Parent"},
                       {"id": "leaf", "name": "Leaf", "parentId": "parent"}],
            "items": [{"id": "alias1", "name": "one||series", "groupIds": ["leaf"]},
                      {"id": "alias2", "name": "one||series", "groupIds": ["default"]},
                      {"id": "other", "name": "other||series", "groupIds": ["leaf"]}],
        }})
        service = FavoritesService(comfy, FakeCatalog([source]))
        result = await service.update_item("character", {"source_item_id": source["id"], "favorite": False})
        self.assertEqual(result["removedCount"], 2)
        self.assertEqual(result["source_item_id"], source["id"])
        self.assertEqual([item["id"] for item in result["items"]], ["other"])
        self.assertEqual({group["id"] for group in result["groups"]}, {"default", "parent", "leaf"})
        again = await service.update_item("character", {"source_item_id": source["id"], "favorite": False})
        self.assertEqual(again["removedCount"], 0)
        self.assertEqual(again["items"], result["items"])

    async def test_source_cancellation_can_remove_missing_custom_source(self):
        comfy = FakeComfy({"pose": {"groups": [{"id": "default", "name": "Default"}],
                                   "items": [{"id": "custom:gone", "groupIds": ["default"], "isCustom": True}]}})
        service = FavoritesService(comfy, FakeCatalog())
        result = await service.update_item("pose", {"source_item_id": "custom:gone", "favorite": False})
        self.assertEqual(result["removedCount"], 1)
        self.assertEqual(result["items"], [])


class FavoriteTargetApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from anima_webui.server import create_app
        from test_server import APP_DIR, FakeComfy as ServerComfy, _write_fake_tools

        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.comfy = ServerComfy()
        self.app = create_app(app_dir=APP_DIR, data_dir=root / "data", comfy=self.comfy,
                              anima_tools_dir=_write_fake_tools(root))
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def test_source_target_is_exclusive_idempotent_and_keeps_empty_groups(self):
        from anima_webui.server import CUSTOM_PROMPTS_KEY

        parent = await (await self.client.post("/api/favorites/pose/groups", json={"name": "Parent"})).json()
        leaf = await (await self.client.post("/api/favorites/pose/groups",
                                           json={"name": "Leaf", "parentId": parent["group"]["id"]})).json()
        saved = await (await self.client.post("/api/favorites/pose/text",
                                            json={"title": "One", "text": "standing", "groupIds": [leaf["group"]["id"]]})).json()
        source_id = saved["item_id"]
        original = copy.deepcopy(self.comfy.favorites_data)
        invalid = [
            {"source_item_id": source_id, "id": source_id, "favorite": False},
            {"source_item_id": source_id, "favorite": True},
            {"source_item_id": source_id},
            {"source_item_id": source_id, "favorite": False, "groupIds": []},
            {"source_item_id": None, "favorite": False},
            {"source_item_id": " ", "favorite": False},
            {"source_item_id": source_id, "favorite": 0},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                response = await self.client.put("/api/favorites/pose/item", json=payload)
                self.assertEqual(response.status, 400, await response.json())
                self.assertEqual(self.comfy.favorites_data, original)
        for expected in (1, 0):
            response = await self.client.put("/api/favorites/pose/item",
                                             json={"source_item_id": source_id, "favorite": False})
            result = await response.json()
            self.assertEqual(response.status, 200, result)
            self.assertEqual(result["source_item_id"], source_id)
            self.assertEqual(result["removedCount"], expected)
            self.assertEqual(result["items"], [])
            self.assertEqual({group["id"] for group in result["groups"]},
                             {group["id"] for group in saved["groups"]})
            self.assertEqual(self.app[CUSTOM_PROMPTS_KEY].list("pose")[0]["id"], source_id)



class SlowComfy(FakeComfy):
    """在读/写两侧都插入让出点,制造真实的并发交错窗口。"""

    async def favorites(self):
        await asyncio.sleep(0)
        return await super().favorites()

    async def save_favorites(self, payload):
        await asyncio.sleep(0)
        return await super().save_favorites(payload)


class FavoriteConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_updates_do_not_lose_writes(self):
        # 回归 #P1-2:收藏变更是「读全量 → 内存改 → 整体写回」,
        # 无锁时两个并发操作会后写覆盖前写,静默丢失一个收藏。
        comfy = SlowComfy()
        service = FavoritesService(comfy, FakeCatalog())
        await asyncio.gather(
            service.update_item("artist", {"name": "alpha", "favorite": True}),
            service.update_item("artist", {"name": "beta", "favorite": True}),
        )
        names = {item["name"] for item in comfy.payload["artist"]["items"]}
        self.assertEqual(names, {"alpha", "beta"})


if __name__ == "__main__":
    unittest.main()
