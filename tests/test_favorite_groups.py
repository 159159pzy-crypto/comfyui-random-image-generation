import asyncio
import copy
import unittest

from anima_webui.catalog import CatalogError
from anima_webui.favorites import FavoritesService, _group_stats, normalize_section


class FakeComfy:
    def __init__(self, payload=None):
        self.payload = copy.deepcopy(payload or {})

    async def favorites(self):
        return copy.deepcopy(self.payload)

    async def save_favorites(self, payload):
        for section, value in payload.items():
            self.payload[section] = copy.deepcopy(value)


class FakeCatalog:
    def __init__(self, items=None):
        self.items = {item["id"]: copy.deepcopy(item) for item in items or []}

    def get(self, item_id):
        item = self.items.get(item_id)
        return copy.deepcopy(item) if item else None


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

    async def test_removing_last_item_prunes_only_the_affected_empty_child_leaf(self):
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
        self.assertNotIn("leaf", group_ids)
        self.assertIn("unrelated", group_ids)
        self.assertIn("parent", group_ids)


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
