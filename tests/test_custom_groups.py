import tempfile
import unittest
from pathlib import Path

from anima_webui.custom_prompts import CustomPromptStore


class FakeCatalog:
    def __init__(self):
        self.items = []

    def set_custom_items(self, items):
        self.items = list(items)


class CustomGroupDeleteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.catalog = FakeCatalog()
        self.store = CustomPromptStore(Path(self.temp.name) / "custom.json", self.catalog)

    def tearDown(self):
        self.temp.cleanup()

    def test_delete_group_can_remove_exclusive_items_and_detach_shared_items(self):
        first = self.store.create_group("pose", {"name": "First"})["group"]
        second = self.store.create_group("pose", {"name": "Second"})["group"]
        exclusive = self.store.create(
            {"section": "pose", "title": "Exclusive", "prompt": "exclusive", "groupIds": [first["id"]]}
        )
        shared = self.store.create(
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

        payload = self.store.delete_group("pose", first["id"], delete_items=True)
        self.assertEqual(payload["deletedItemIds"], [exclusive["id"]])
        self.assertEqual(payload["deletedItemCount"], 1)
        self.assertEqual(payload["detachedItemCount"], 1)
        remaining = {item["id"]: item for item in self.store.list("pose")}
        self.assertNotIn(exclusive["id"], remaining)
        self.assertEqual(remaining[shared["id"]]["groupIds"], [second["id"]])
        self.assertEqual({item["id"] for item in self.catalog.items}, {shared["id"]})

    def test_default_delete_keeps_items_and_only_detaches_membership(self):
        group = self.store.create_group("expression", {"name": "Mood"})["group"]
        item = self.store.create(
            {"section": "expression", "title": "Calm", "prompt": "calm", "groupIds": [group["id"]]}
        )
        payload = self.store.delete_group("expression", group["id"])
        self.assertEqual(payload["deletedItemIds"], [])
        self.assertEqual(payload["detachedItemCount"], 1)
        self.assertEqual(self.store.list("expression")[0]["id"], item["id"])
        self.assertEqual(self.store.list("expression")[0]["groupIds"], [])


if __name__ == "__main__":
    unittest.main()
