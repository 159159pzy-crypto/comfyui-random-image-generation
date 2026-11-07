import copy
import json

import pytest

from anima_webui.catalog import PromptCatalog, resolve_history_selection
from anima_webui.runner import BatchManager
from anima_webui.workflow import validate_settings
from test_server import _write_fake_tools


MODE = "trigger_tags_no_clothing"


@pytest.fixture
def catalog(tmp_path):
    tools = _write_fake_tools(tmp_path)
    js = tools / "js"
    (js / "character_data.js").write_text('const data = ' + json.dumps([
        {"name": "alpha", "copyright": "series", "gender": "1girl"},
        {"name": "beta", "copyright": "series", "gender": "1girl"},
    ]) + ';', encoding="utf-8")
    (js / "character_official_data.json").write_text(json.dumps({
        f"{name}||series": {
            # A clothing-like trigger must never be filtered.
            "trigger": f"{name}, white shirt",
            "tags": ["1girl", "Blue Eyes", "white shirt", "detached sleeves", "barefoot",
                     "hair bow", "bare shoulders", "navel", "cleavage", "unknown garment"],
        } for name in ("alpha", "beta")
    }), encoding="utf-8")
    return PromptCatalog(tmp_path, tools)


def settings(**overrides):
    return validate_settings({
        "character_detail": MODE, "quality_prompt": "", "manual_artist": "",
        "random_character": True, "female_count": 1, "male_count": 0,
        "pools": {"character": {"mode": "all", "ids": [], "excluded_ids": []}},
        **overrides,
    })


def test_modes_preserve_identity_order_and_random_choices(catalog):
    assert validate_settings({})["character_detail"] == "trigger_tags"
    original = catalog.all_items("character")
    for seed in (0, 7, 25):
        results = {mode: catalog.resolve_prompt(settings(character_detail=mode), seed)
                   for mode in ("trigger", "trigger_tags", MODE)}
        draws = [result["selected"]["character"][0] for result in results.values()]
        assert len({entry["id"] for entry in draws}) == 1
        trigger, full, filtered = draws
        assert trigger["prompt"] == ", ".join(trigger["trigger"])
        assert "detached sleeves" in full["prompt"]
        assert "barefoot" in full["prompt"]
        expected = ", ".join(filtered["trigger"] + [
            "Blue Eyes", "hair bow", "bare shoulders", "navel", "cleavage", "unknown garment",
        ])
        assert filtered["prompt"] == expected
        assert results[MODE]["full_prompt"] == f"1girl, {expected}, "
        assert filtered["snapshot_version"] == 2
        assert filtered["tags"] == full["tags"]
    assert catalog.all_items("character") == original


def test_custom_fixed_and_other_prompt_sources_are_preserved(catalog):
    catalog.set_custom_items([{"id": "custom:clothed", "section": "character", "title": "My character",
                               "prompt": "custom hero, white shirt, barefoot, hair bow"}])
    custom = settings(pools={"character": {"mode": "include", "ids": ["custom:clothed"]}})
    assert catalog.resolve_prompt(custom, 1)["selected"]["character"][0]["prompt"] == (
        "custom hero, white shirt, barefoot, hair bow"
    )
    fixed = settings(random_character=False, fixed_character="fixed hero, white shirt, barefoot",
                     fixed_clothing="black gloves", extra_prompt="detached sleeves")
    assert catalog.resolve_prompt(fixed, 1)["full_prompt"] == (
        "fixed hero, white shirt, barefoot, black gloves, 1girl, detached sleeves, "
    )
    mixed = settings(random_clothing=True, extra_prompt="detached sleeves",
                     pools={"character": {"mode": "all"},
                            "clothing": {"mode": "include", "ids": ["clothing:coat"]}})
    result = catalog.resolve_prompt(mixed, 1)
    assert result["selected"]["clothing"][0]["prompt"] == "red coat"
    assert "red coat" in result["full_prompt"]
    assert "detached sleeves" in result["full_prompt"]
    assert "detached sleeves" not in result["selected"]["character"][0]["prompt"]


@pytest.mark.parametrize("snapshot_version", [None, 2])
def test_historical_prompt_is_not_refiltered_with_current_dictionary(catalog, snapshot_version):
    entry = {"id": "character:alpha||series", "builtin": True, "trigger": ["alpha"],
             "tags": ["blue eyes", "white shirt"], "prompt": "alpha, blue eyes, white shirt"}
    if snapshot_version is not None:
        entry["snapshot_version"] = snapshot_version
    original = copy.deepcopy(entry)
    restored, issues = resolve_history_selection({"character": [entry]}, settings(), catalog)
    assert issues == {}
    assert restored["character"][0]["prompt"] == entry["prompt"]
    assert entry == original


def test_redraw_preserves_old_role_but_new_role_uses_selected_mode(catalog):
    manager = BatchManager.__new__(BatchManager)
    manager.catalog = catalog
    current = settings(random_pose=True, pools={"character": {"mode": "all"},
                                               "pose": {"mode": "all"}})
    original = catalog.resolve_prompt({**current, "character_detail": "trigger_tags"}, 3)
    old_role = original["selected"]["character"]
    preserved = manager._resolve_regeneration(current, 9, {
        "mode": "content_redraw", "fixed_selection": {"character": old_role},
        "original_selection": original["selected"], "redraw_sections": ["pose"],
    })
    assert preserved["selected"]["character"] == old_role
    assert "detached sleeves" in preserved["full_prompt"]
    fresh = manager._resolve_regeneration(current, 9, {
        "mode": "content_redraw", "fixed_selection": {"pose": original["selected"]["pose"]},
        "original_selection": original["selected"], "redraw_sections": ["character"],
    })
    assert "detached sleeves" not in fresh["full_prompt"]
    assert "hair bow" in fresh["full_prompt"]
    assert fresh["selected"]["pose"] == original["selected"]["pose"]
