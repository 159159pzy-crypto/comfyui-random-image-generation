import tempfile
import unittest
from pathlib import Path

from anima_webui.catalog import CatalogError
from anima_webui.custom_prompts import CustomPromptStore


class FakeCatalog:
    def __init__(self):
        self.items = []

    def set_custom_items(self, items):
        self.items = list(items)


class CustomGroupDeleteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.catalog = FakeCatalog()
        self.store = CustomPromptStore(Path(self.temp.name) / "custom.json", self.catalog)

    def tearDown(self):
        self.temp.cleanup()

    async def test_delete_group_can_remove_exclusive_items_and_detach_shared_items(self):
        first = (await self.store.create_group("pose", {"name": "First"}))["group"]
        second = (await self.store.create_group("pose", {"name": "Second"}))["group"]
        exclusive = await self.store.create(
            {"section": "pose", "title": "Exclusive", "prompt": "exclusive", "groupIds": [first["id"]]}
        )
        shared = await self.store.create(
            {
                "section": "pose",
                "title": "Shared",
                "prompt": "shared",
                "groupIds": [first["id"], second["id"]],
            }
        )
        counts = {group["id"]: group for group in self.store.list_groups("pose")["groups"]}
        self.assertEqual(counts[first["id"]]["exclusiveCount"], 1)
        self.assertEqual(counts[second["id"]]["exclusiveCount"], 0)

        payload = await self.store.delete_group("pose", first["id"], delete_items=True)
        self.assertEqual(payload["deletedItemIds"], [exclusive["id"]])
        self.assertEqual(payload["deletedItemCount"], 1)
        self.assertEqual(payload["detachedItemCount"], 1)
        remaining = {item["id"]: item for item in self.store.list("pose")}
        self.assertNotIn(exclusive["id"], remaining)
        self.assertEqual(remaining[shared["id"]]["groupIds"], [second["id"]])
        self.assertEqual({item["id"] for item in self.catalog.items}, {shared["id"]})

    def test_corrupt_file_is_backed_up_and_store_starts_empty(self):
        # 回归 #P2-4:坏文件不再阻断启动,改名备份后以空数据继续并给出警告。
        path = Path(self.temp.name) / "custom.json"
        path.write_text('{"items": [{"section": "pose"}]}', encoding="utf-8")  # 缺 title/prompt → 校验失败
        store = CustomPromptStore(path, FakeCatalog())
        self.assertEqual(store.items, [])
        self.assertEqual(len(store.load_warnings), 1)
        self.assertIn("custom.json.corrupt.bak", store.load_warnings[0])
        self.assertTrue(path.with_name("custom.json.corrupt.bak").is_file())

    async def test_default_delete_keeps_items_and_only_detaches_membership(self):
        group = (await self.store.create_group("expression", {"name": "Mood"}))["group"]
        item = await self.store.create(
            {"section": "expression", "title": "Calm", "prompt": "calm", "groupIds": [group["id"]]}
        )
        payload = await self.store.delete_group("expression", group["id"])
        self.assertEqual(payload["deletedItemIds"], [])
        self.assertEqual(payload["detachedItemCount"], 1)
        self.assertEqual(self.store.list("expression")[0]["id"], item["id"])
        self.assertEqual(self.store.list("expression")[0]["groupIds"], [])

    async def test_delete_all_removes_exclusive_and_shared_items(self):
        first = (await self.store.create_group("pose", {"name": "First"}))["group"]
        second = (await self.store.create_group("pose", {"name": "Second"}))["group"]
        exclusive = await self.store.create(
            {"section": "pose", "title": "Exclusive", "prompt": "exclusive", "groupIds": [first["id"]]}
        )
        shared = await self.store.create(
            {
                "section": "pose",
                "title": "Shared",
                "prompt": "shared",
                "groupIds": [first["id"], second["id"]],
            }
        )

        payload = await self.store.delete_group("pose", first["id"], delete_mode="all")

        self.assertEqual(payload["deleteMode"], "all")
        self.assertEqual(set(payload["deletedItemIds"]), {exclusive["id"], shared["id"]})
        self.assertEqual(payload["deletedItemCount"], 2)
        self.assertEqual(payload["detachedItemCount"], 0)
        self.assertEqual(self.store.list("pose"), [])
        self.assertEqual(self.catalog.items, [])

    async def test_delete_all_on_empty_group_and_invalid_mode(self):
        empty = (await self.store.create_group("background", {"name": "Empty"}))["group"]
        payload = await self.store.delete_group("background", empty["id"], delete_mode="all")
        self.assertEqual(payload["deletedGroupCount"], 1)
        self.assertEqual(payload["deletedItemCount"], 0)
        self.assertEqual(payload["deleteMode"], "all")

        other = (await self.store.create_group("background", {"name": "Other"}))["group"]
        with self.assertRaisesRegex(CatalogError, "deleteMode"):
            await self.store.delete_group("background", other["id"], delete_mode="invalid")


if __name__ == "__main__":
    unittest.main()
