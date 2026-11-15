"""Character classification survives import, edits, migrations and restarts."""
import asyncio
import copy
import csv
import io
import json

import pytest

from anima_webui.catalog import CatalogError, PromptCatalog
from anima_webui.custom_prompts import CUSTOM_DATA_VERSION, CustomPromptStore
from anima_webui.persistence import StorageReadOnlyError


@pytest.fixture
def catalog(tmp_path):
    tools = tmp_path / "tools"
    js = tools / "js"
    js.mkdir(parents=True)
    for name, entries in {
        "character_data.js": [{"name": "alpha", "copyright": "series", "gender": "1girl"}],
        "clothing_data.js": [], "pose_data.js": [], "background_data.js": [],
    }.items():
        (js / name).write_text("const data = " + json.dumps(entries) + ";", encoding="utf-8")
    (js / "character_official_data.json").write_text(json.dumps({
        "alpha||series": {"trigger": "alpha, series", "tags": ["blue eyes", "hair bow", "1girl"]},
    }), encoding="utf-8")
    return PromptCatalog(tmp_path, tools)


@pytest.fixture
def store(tmp_path, catalog):
    return CustomPromptStore(tmp_path / "custom.json", catalog)


def row(title="Custom Alpha", prompt="blue eyes, alpha, white shirt, series, hair bow", **fields):
    return {"section": "character", "title": title, "prompt": prompt, **fields}


def preview(store, items):
    return store.preview_import("json", json.dumps({"items": items}), "character", "Characters")


def commit(store, rows):
    return asyncio.run(store.commit_import(rows, "character", bundle_name="Characters"))


@pytest.mark.parametrize("file_format", ["json", "csv"])
def test_templates_roundtrip_explicit_roles(store, file_format):
    _, _, body = store.template("character", file_format)
    result = store.preview_import(file_format, body.decode("utf-8-sig"), "character", "Characters")
    imported = result["rows"][0]
    assert imported["status"] == "new" and imported["action"] == "create"
    assert imported["item"]["character_status"] == "ready"
    assert imported["item"]["trigger"] == ["example hero"]
    assert "white shirt" in imported["item"]["tags"]
    assert commit(store, result["rows"])["imported"] == 1
    loaded = CustomPromptStore(store.path, store.catalog)
    assert loaded.load_trusted and loaded.items == store.items


def test_legacy_import_classifies_all_positions_and_requires_unknown_confirmation(store):
    result = preview(store, [row(), row("Unknown role", "unlisted token, blue eyes")])
    known, unknown = result["rows"]
    assert known["item"]["trigger"] == ["alpha", "series"]
    assert [part["text"] for part in known["item"]["character_parts"]] == row()["prompt"].split(", ")
    assert known["action"] == "create"
    assert unknown["item"]["character_status"] == "needs_review"
    assert unknown["action"] == "skip"
    assert commit(store, result["rows"]) == {
        "imported": 1, "updated": 0, "skipped": 1, "total": 1,
        "bundleId": store.groups["character"][0]["id"], "bundleName": "Characters",
    }


def test_pending_commit_is_atomic_and_cannot_be_bypassed_by_status(store):
    result = preview(store, [row(), row("Unknown role", "unlisted token, blue eyes")])
    unknown = result["rows"][1]
    unknown["action"] = "create"
    unknown["item"]["character_status"] = "ready"
    with pytest.raises(CatalogError, match="尚未确认"):
        commit(store, result["rows"])
    assert store.items == [] and store.groups["character"] == []
    assert not store.path.exists()


def test_manual_confirmation_and_overwrite_preserve_id(store):
    result = preview(store, [row("Unknown role", "unlisted token, blue eyes")])
    imported = result["rows"][0]
    imported["item"]["character_parts"][0].update(role="trigger", source="manual")
    imported["action"] = "create"
    assert commit(store, result["rows"])["imported"] == 1
    original_id = store.items[0]["id"]
    replacement = preview(store, [row("Unknown role", "white shirt, alternate token",
                                    trigger=["alternate token"], tags=["white shirt"])])
    assert replacement["rows"][0]["status"] == "conflict"
    replacement["rows"][0]["action"] = "overwrite"
    assert commit(store, replacement["rows"])["updated"] == 1
    assert store.items[0]["id"] == original_id
    assert store.items[0]["prompt"] == "white shirt, alternate token"
    assert store.items[0]["trigger"] == ["alternate token"]


def test_csv_optional_empty_columns_remain_legacy_input(store):
    content = io.StringIO()
    writer = csv.DictWriter(content, fieldnames=["section", "title", "prompt", "trigger", "tags"])
    writer.writeheader()
    writer.writerow(row())
    result = store.preview_import("csv", content.getvalue(), "character", "Characters")
    assert result["rows"][0]["item"]["trigger"] == ["alpha", "series"]
    assert result["rows"][0]["action"] == "create"


def test_no_trigger_confirmation_is_explicit_and_survives_reload(store):
    item = asyncio.run(store.create(row("Original", "blue eyes, hair bow")))
    assert item["character_status"] == "needs_review"
    updated = asyncio.run(store.update(item["id"], {"character_no_trigger": True}))
    assert updated["character_status"] == "ready" and updated["trigger"] == []
    loaded = CustomPromptStore(store.path, store.catalog)
    assert loaded.items[0]["character_no_trigger"] is True
    assert loaded.items[0]["character_status"] == "ready"


def test_edits_preserve_manual_roles_and_reanalyze_only_changed_text(store):
    item = asyncio.run(store.create(row("Custom", "novel token, blue eyes",
                                       trigger=["novel token"], tags=["blue eyes"])))
    renamed = asyncio.run(store.update(item["id"], {"title": "Renamed", "copyright": "Other"}))
    assert renamed["character_parts"] == item["character_parts"]
    changed = asyncio.run(store.update(item["id"], {"prompt": "white shirt, novel token, new mystery"}))
    assert changed["trigger"] == ["novel token"]
    assert changed["character_parts"][1]["source"] == "explicit"
    assert changed["character_parts"][2]["role"] == "unknown"
    assert changed["character_status"] == "needs_review"
    assert changed["prompt"] == "white shirt, novel token, new mystery"


def test_edit_explicit_fields_does_not_conflict_with_inherited_prompt(store):
    item = asyncio.run(store.create(row(trigger=["alpha", "series"], tags=["blue eyes", "white shirt", "hair bow"])))
    changed = asyncio.run(store.update(item["id"], {"trigger": ["other token"], "tags": ["blue eyes"]}))
    assert changed["prompt"] == "other token, blue eyes"
    assert changed["trigger"] == ["other token"]
    assert changed["character_status"] == "ready"


def legacy_document():
    return {
        "version": 4, "source_completeness": "unverified",
        "groups": {"character": [
            {"id": "folder", "name": "Pack", "kind": "import", "parentId": None},
            {"id": "leaf", "name": "Characters", "kind": "group", "parentId": "folder"},
        ]},
        "items": [{"id": "custom:original", **row(prompt="  blue eyes, alpha, white shirt, series  "),
                   "groupIds": ["leaf"]}],
    }


def test_v4_migration_preserves_original_prompt_ids_and_tree(tmp_path, catalog):
    path = tmp_path / "custom.json"
    legacy = legacy_document()
    original = json.dumps(legacy).encode()
    path.write_bytes(original)
    store = CustomPromptStore(path, catalog)
    assert store.load_trusted and not store.read_only_reason
    assert store.items[0]["id"] == "custom:original"
    assert store.items[0]["prompt"] == legacy["items"][0]["prompt"]
    assert store.items[0]["groupIds"] == ["leaf"]
    assert store.groups["character"] == legacy["groups"]["character"]
    assert store.items[0]["trigger"] == ["alpha", "series"]
    assert json.loads(path.read_text())["version"] == CUSTOM_DATA_VERSION
    assert json.loads(path.read_text())["source_completeness"] == "unverified"
    assert path.with_name("custom.json.pre-v5.bak").read_bytes() == original
    assert not path.with_name("custom.json.pre-v4.bak").exists()
    persisted = path.read_bytes()
    # Changing the local dictionary must not silently change saved decisions.
    catalog._official.clear()
    reloaded = CustomPromptStore(path, catalog)
    assert reloaded.items == store.items
    assert reloaded.load_trusted and path.read_bytes() == persisted


@pytest.mark.parametrize("failure", ["backup", "write"])
def test_migration_failure_preserves_source_and_becomes_read_only(tmp_path, catalog, monkeypatch, failure):
    path = tmp_path / "custom.json"
    original = json.dumps(legacy_document()).encode()
    path.write_bytes(original)
    def fail(*args):
        raise OSError("test disk failure")
    monkeypatch.setattr(CustomPromptStore, "_backup_before_v5_migration" if failure == "backup" else "_write_text", fail)
    store = CustomPromptStore(path, catalog)
    assert store.load_trusted and store.items[0]["id"] == "custom:original"
    assert path.read_bytes() == original
    with pytest.raises(StorageReadOnlyError):
        store.ensure_writable()


@pytest.mark.parametrize("broken", ["missing_parts", "missing_status", "changed_text", "wrong_status", "wrong_trigger", "wrong_source"])
def test_invalid_v5_classification_is_not_silently_rebuilt(store, broken):
    asyncio.run(store.create(row()))
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    item = payload["items"][0]
    if broken == "missing_parts":
        item.pop("character_parts")
    elif broken == "missing_status":
        item.pop("character_status")
    elif broken == "changed_text":
        item["character_parts"][0]["text"] = "silently changed"
    elif broken == "wrong_status":
        item["character_status"] = "needs_review"
    elif broken == "wrong_trigger":
        item["trigger"] = ["other token"]
    else:
        item["character_parts"][0]["source"] = "invented source"
    original = json.dumps(payload).encode()
    store.path.write_bytes(original)
    reloaded = CustomPromptStore(store.path, store.catalog)
    assert not reloaded.load_trusted and reloaded.load_state == "invalid"
    assert reloaded.items == [] and reloaded.read_only_reason
    assert store.path.read_bytes() == original


def test_explicit_import_rejects_dropped_tokens(store):
    result = preview(store, [row(trigger=["alpha"], tags=["blue eyes"])])
    assert result["rows"][0]["status"] == "error"
    assert result["rows"][0]["action"] == "skip"
    assert not store.path.exists()


def test_unknown_saved_favorite_source_can_still_be_created_and_reused(store):
    payload = row("Favorite", "unlisted custom scene, new description")
    first, created = asyncio.run(store.create_or_reuse(payload))
    assert created and first["character_status"] == "needs_review"
    again, created = asyncio.run(store.create_or_reuse(copy.deepcopy(payload)))
    assert not created and again["id"] == first["id"]
