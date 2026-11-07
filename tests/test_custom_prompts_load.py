"""Persisted custom pools must not silently lose records before reconciliation."""
import copy
import json

import pytest

from anima_webui.custom_prompts import MAX_GROUPS_PER_SECTION, CustomPromptStore
from anima_webui.persistence import StorageReadOnlyError


class MemoryCatalog:
    def set_custom_items(self, items):
        self.items = copy.deepcopy(items)


def source_document():
    return {
        "version": 4,
        "groups": {"pose": [{"id": "leaf", "name": "Leaf"}]},
        "items": [{"id": "custom:kept", "section": "pose", "title": "Kept", "prompt": "standing",
                   "groupIds": ["leaf"]}],
    }


def invalid_document(case):
    value = source_document()
    item = value["items"][0]
    group = value["groups"]["pose"][0]
    if case == "missing_items":
        value.pop("items")
    elif case == "null_items":
        value["items"] = None
    elif case == "wrong_items":
        value["items"] = {}
    elif case == "null_groups":
        value["groups"] = None
    elif case == "wrong_groups":
        value["groups"] = []
    elif case == "null_section_groups":
        value["groups"]["pose"] = None
    elif case == "unknown_group_section":
        value["groups"]["unknown"] = []
    elif case == "nonobject_item":
        value["items"].append(None)
    elif case == "duplicate_item":
        value["items"].append({**item, "section": "background", "groupIds": []})
    elif case.startswith("item_id_"):
        item["id"] = {"missing": None, "number": 123, "prefix": "source:x", "empty": "custom:",
                      "space": "custom: x"}[case.removeprefix("item_id_")]
    elif case == "numeric_prompt":
        item["prompt"] = 123
    elif case == "numeric_title":
        item["title"] = 123
    elif case == "object_subtitle":
        item["subtitle"] = {}
    elif case == "unknown_item_section":
        item["section"] = "unknown"
    elif case == "nonobject_group":
        value["groups"]["pose"].append(None)
    elif case == "duplicate_group":
        value["groups"]["pose"].append({**group, "name": "Other"})
    elif case == "long_group_name":
        group["name"] = "X" * 101
    elif case == "numeric_group_name":
        group["name"] = 123
    elif case == "empty_group_id":
        group["id"] = ""
    elif case == "invalid_group_kind":
        group["kind"] = "unknown"
    elif case == "unknown_parent":
        group["parentId"] = "missing"
    elif case == "nonfolder_parent":
        group["parentId"] = "leaf"
    elif case == "folder_with_parent":
        group.update(kind="import", parentId="leaf")
    elif case == "too_many_groups":
        value["groups"]["pose"] = [{"id": str(i), "name": str(i)} for i in range(MAX_GROUPS_PER_SECTION + 1)]
    elif case == "unknown_membership":
        item["groupIds"].append("missing")
    elif case == "folder_membership":
        group["kind"] = "import"
    elif case == "null_memberships":
        item["groupIds"] = None
    elif case == "text_memberships":
        item["groupIds"] = "leaf"
    elif case == "duplicate_memberships":
        item["groupIds"].append("leaf")
    elif case == "numeric_trait":
        item["traits"] = [123]
    elif case == "null_version":
        value["version"] = None
    elif case == "future_version":
        value["version"] = 5
    elif case == "migration_id_collision":
        value["version"] = 3
        group["id"] = "custom_folder_legacy_pose"
        item["groupIds"] = [group["id"]]
    else:
        raise AssertionError(case)
    return value


INVALID_CASES = [
    "missing_items", "null_items", "wrong_items", "null_groups", "wrong_groups", "null_section_groups",
    "unknown_group_section", "nonobject_item", "duplicate_item", "item_id_missing", "item_id_number",
    "item_id_prefix", "item_id_empty", "item_id_space", "numeric_prompt", "numeric_title", "object_subtitle",
    "unknown_item_section", "nonobject_group", "duplicate_group", "long_group_name", "numeric_group_name",
    "empty_group_id", "invalid_group_kind", "unknown_parent", "nonfolder_parent", "folder_with_parent",
    "too_many_groups", "unknown_membership", "folder_membership", "null_memberships", "text_memberships",
    "duplicate_memberships", "numeric_trait", "null_version", "future_version", "migration_id_collision",
]


@pytest.mark.parametrize("case", INVALID_CASES)
def test_lossy_source_is_preserved_and_read_only_across_restarts(tmp_path, case):
    path = tmp_path / "custom.json"
    original = json.dumps(invalid_document(case)).encode()
    path.write_bytes(original)
    for _ in range(2):
        catalog = MemoryCatalog()
        store = CustomPromptStore(path, catalog)
        assert not store.load_trusted
        assert store.load_state == "invalid"
        assert store.items == catalog.items == []
        assert path.read_bytes() == original
        with pytest.raises(StorageReadOnlyError):
            store.ensure_writable()
    backups = list(tmp_path.glob("custom.json.v4*.bak"))
    assert len(backups) == 2
    assert all(backup.read_bytes() == original for backup in backups)
    assert not (tmp_path / "custom.json.pre-v4.bak").exists()


@pytest.mark.parametrize("version", [None, 1, 2, 3])
def test_legal_legacy_source_migrates_without_losing_items_or_memberships(tmp_path, version):
    document = source_document()
    if version is None:
        document.pop("version")
    else:
        document["version"] = version
    document["items"].append({"id": "custom:ungrouped", "section": "pose", "title": "Other", "prompt": "sitting"})
    path = tmp_path / "custom.json"
    original = json.dumps(document).encode()
    path.write_bytes(original)
    store = CustomPromptStore(path, MemoryCatalog())
    assert store.load_trusted and not store.read_only_reason
    assert store.items[0]["groupIds"] == ["leaf"]
    assert store.items[1]["groupIds"] == ["custom_group_legacy_ungrouped_pose"]
    assert {item["id"] for item in store.items} == {"custom:kept", "custom:ungrouped"}
    assert (tmp_path / "custom.json.pre-v4.bak").read_bytes() == original
    reloaded = CustomPromptStore(path, MemoryCatalog())
    assert reloaded.load_trusted and reloaded.items == store.items and reloaded.groups == store.groups


def test_current_source_at_capacity_preserves_tree_and_all_records(tmp_path):
    document = source_document()
    document["groups"]["pose"] = [
        {"id": "folder", "name": "Folder", "kind": "import"},
        {"id": "leaf", "name": "Leaf", "parentId": "folder"},
        *[{"id": str(i), "name": str(i)} for i in range(MAX_GROUPS_PER_SECTION - 2)],
    ]
    path = tmp_path / "custom.json"
    original = json.dumps(document).encode()
    path.write_bytes(original)
    store = CustomPromptStore(path, MemoryCatalog())
    assert store.load_trusted and not store.read_only_reason
    assert len(store.groups["pose"]) == MAX_GROUPS_PER_SECTION
    assert store.groups["pose"][1]["parentId"] == "folder"
    assert store.items[0]["groupIds"] == ["leaf"]
    assert path.read_bytes() == original
    assert not list(tmp_path.glob("*.bak"))
