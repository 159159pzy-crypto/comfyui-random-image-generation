import copy
import json

import pytest

from anima_webui.catalog import CatalogError, CharacterReviewRequired, PromptCatalog
from anima_webui.character_classification import CharacterClassifier, split_character_prompt
from test_server import _write_fake_tools


@pytest.fixture
def catalog(tmp_path):
    tools = _write_fake_tools(tmp_path)
    js = tools / "js"
    (js / "character_data.js").write_text("const data = " + json.dumps([
        {"name": "hero", "copyright": "series", "gender": "1girl"},
        {"name": "other", "copyright": "other series", "gender": "1boy"},
    ]) + ";", encoding="utf-8")
    (js / "character_official_data.json").write_text(json.dumps({
        "hero||series": {"trigger": "hero, series", "tags": ["1girl", "blue eyes", "hair bow", "white shirt"]},
        "other||other series": {"trigger": "other, other series", "tags": ["1boy", "green eyes"]},
    }), encoding="utf-8")
    return PromptCatalog(tmp_path, tools)


def roles(result):
    return {part["text"]: part["role"] for part in result["character_parts"]}


def test_identity_is_found_at_any_position_and_order_is_preserved(catalog):
    original = "  hair bow, series, white shirt, hero, Blue_Eyes  "
    first = catalog.analyze_character({"prompt": original, "title": "自定义人物"})
    second = catalog.analyze_character({"prompt": "hero, white shirt, Blue_Eyes, hair bow, series"})
    assert first["prompt"] == original
    assert roles(first) == roles(second)
    assert first["trigger"] == ["series", "hero"]
    assert first["character_status"] == "ready"
    assert first["previews"] == {
        "trigger_tags": "hair bow, series, white shirt, hero, Blue_Eyes",
        "trigger": "series, hero",
        "trigger_tags_no_clothing": "hair bow, series, hero, Blue_Eyes",
    }


def test_metadata_aliases_are_complete_matches_without_adding_tokens(catalog):
    result = catalog.analyze_character({
        "prompt": "new character, blue eyes, arknights: endfield",
        "title": "新角色", "subtitle": "new character · Operators Collection v4",
        "copyright": "明日方舟：终末地 / Arknights: Endfield|unused series",
    })
    assert result["trigger"] == ["new character", "arknights: endfield"]
    assert result["tags"] == ["blue eyes"]
    assert all("unused" not in part["text"] for part in result["character_parts"])
    assert result["character_parts"][0]["source"] == "metadata"
    feature_title = catalog.analyze_character({"title": "blue eyes", "prompt": "blue eyes"})
    assert feature_title["trigger"] == []
    assert feature_title["tags"] == ["blue eyes"]
    assert feature_title["character_status"] == "needs_review"


def test_escaped_parentheses_compare_without_rewriting(catalog):
    prompt = r"new_character \(new_series\), blue eyes"
    result = catalog.analyze_character({"title": "new character (new series)", "prompt": prompt})
    assert result["trigger"] == [r"new_character \(new_series\)"]
    assert result["prompt"] == prompt


def test_variants_and_unknown_words_do_not_become_identity_by_position(catalog):
    result = catalog.analyze_character({"prompt": "made_up_token, hero (winter), series (winter), whale girl, blue eyes"})
    assert result["trigger"] == []
    assert result["character_parts"][0]["source"] == "unknown"
    assert result["character_parts"][1] == {
        "text": "hero (winter)", "role": "unknown", "source": "candidate", "suggestion": "trigger",
    }
    assert result["character_parts"][2]["source"] == "unknown"
    assert result["character_status"] == "needs_review"
    assert result["previews"]["trigger"] is None


def test_dictionary_conflicts_and_optional_tagger_are_local(catalog, tmp_path):
    csv = tmp_path / "selected_tags.csv"
    csv.write_text("name,category\nhero,0\npixai_character,4\n,0\ngreen_hair,0\n", encoding="utf-8")
    tagged = PromptCatalog(tmp_path, catalog.tools_dir, character_tagger_paths=[csv])
    result = tagged.analyze_character({"prompt": "hero, pixai_character, green_hair"})
    assert result["character_parts"][0] == {"text": "hero", "role": "unknown", "source": "conflict"}
    assert roles(result)["pixai_character"] == "trigger"
    assert roles(result)["green_hair"] == "feature"
    # The optional table is a snapshot, not a generation-time dependency.
    csv.unlink()
    assert tagged.analyze_character({"prompt": "pixai_character"})["character_status"] == "ready"
    assert catalog.analyze_character({"prompt": "pixai_character"})["character_status"] == "needs_review"


def test_corrupt_tagger_is_ignored_atomically(catalog, tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("name,category\nnew_known,4\ninvalid,nope\n", encoding="utf-8")
    classifier = CharacterClassifier(catalog.all_items("character"), [path])
    result = classifier.analyze({"prompt": "new_known, hero"})
    assert roles(result)["new_known"] == "unknown"
    assert result["warnings"]


def test_explicit_no_trigger_is_valid_but_never_automatic(catalog):
    result = catalog.analyze_character({"prompt": "blue eyes, white shirt"})
    assert result["character_status"] == "needs_review"
    result["character_no_trigger"] = True
    accepted = catalog.analyze_character(result)
    assert accepted["character_status"] == "ready"
    assert accepted["previews"]["trigger"] == ""
    assert accepted["previews"]["trigger_tags_no_clothing"] == "blue eyes"
    explicit = catalog.analyze_character({"trigger": [], "tags": ["blue eyes"]})
    assert explicit["character_status"] == "ready"
    assert explicit["character_no_trigger"] is True
    with pytest.raises(CatalogError, match="仍含触发词"):
        catalog.analyze_character({"prompt": "hero", "character_no_trigger": True})


def test_explicit_multiple_triggers_preserve_original_order_and_duplicates(catalog):
    prompt = "white shirt, hero, blue eyes, white shirt"
    result = catalog.analyze_character({"prompt": prompt, "trigger": ["hero", "white shirt"],
                                        "tags": ["blue eyes", "white shirt"]})
    assert [part["text"] for part in result["character_parts"]] == split_character_prompt(prompt)
    assert result["previews"]["trigger_tags_no_clothing"] == "white shirt, hero, blue eyes"
    assert result["trigger"] == ["white shirt", "hero"]


def test_manual_decisions_survive_reanalysis_and_matching_prompt_edits(catalog):
    original = catalog.analyze_character({"prompt": "mystery, blue eyes, white shirt"})
    original["character_parts"][0].update(role="trigger", source="manual")
    saved = catalog.analyze_character(original)
    assert saved["character_status"] == "ready"
    # Store reload preserves all saved classifications even after dictionaries change.
    catalog._character_classifier.features.add("mystery")
    assert catalog.analyze_character(saved)["character_parts"] == saved["character_parts"]
    refreshed = catalog.analyze_character(saved, reanalyze=True)
    assert refreshed["character_parts"][0] == saved["character_parts"][0]
    edited = catalog.analyze_character({"prompt": "hair bow, mystery, blue eyes, new_unknown"}, previous=saved, reanalyze=True)
    assert edited["trigger"] == ["mystery"]
    assert edited["character_parts"][1]["source"] == "manual"
    assert edited["character_status"] == "needs_review"
    assert "white shirt" not in edited["tags"]


@pytest.mark.parametrize("mutation", [
    lambda value: value["character_parts"].pop(),
    lambda value: value["character_parts"].reverse(),
    lambda value: value["character_parts"][0].update(text="invented"),
    lambda value: value["character_parts"][0].update(role=[]),
    lambda value: value["character_parts"][0].update(source="ai"),
    lambda value: value["character_parts"][0].update(suggestion="feature"),
])
def test_submitted_parts_must_cover_exact_original_tokens(catalog, mutation):
    result = catalog.analyze_character({"prompt": "hero, blue eyes"})
    mutation(result)
    with pytest.raises(CatalogError):
        catalog.analyze_character(result)


@pytest.mark.parametrize("payload", [
    {"prompt": "hero", "trigger": ["hero"], "tags": ["blue eyes"]},
    {"prompt": "hero", "trigger": 1},
    {"prompt": "hero", "tags": [{}]},
    {"prompt": "hero", "character_no_trigger": "true"},
    {"prompt": "hero", "subtitle": []},
])
def test_invalid_explicit_input_is_rejected(catalog, payload):
    with pytest.raises(CatalogError):
        catalog.analyze_character(payload)


def test_compound_syntax_is_opaque_and_unweighted_clothing_only_is_removed(catalog):
    prompt = r"hero, (white shirt:1.2), (blue eyes, white shirt:1.1), escaped\,comma"
    result = catalog.analyze_character({"prompt": prompt})
    assert [part["text"] for part in result["character_parts"]] == [
        "hero", "(white shirt:1.2)", "(blue eyes, white shirt:1.1)", r"escaped\,comma",
    ]
    assert result["character_status"] == "needs_review"
    for part in result["character_parts"][1:]:
        part.update(role="feature", source="manual")
    accepted = catalog.analyze_character(result)
    assert accepted["previews"]["trigger_tags_no_clothing"] == prompt


def test_candidate_pool_preflight_blocks_without_changing_selection(catalog):
    catalog.set_custom_items([
        {"id": "custom:pending", "section": "character", "title": "待确认", "prompt": "unknown name, blue eyes"},
        {"id": "custom:ready", "section": "character", "title": "可用", "prompt": "hero, blue eyes"},
    ])
    settings = {"random_character": True, "random_character_count": 1, "character_detail": "trigger",
                "pools": {"character": {"mode": "all", "excluded_ids": []}}}
    before = copy.deepcopy(settings)
    with pytest.raises(CharacterReviewRequired) as caught:
        catalog.validate_settings(settings)
    assert caught.value.pending == [{"id": "custom:pending", "title": "待确认"}]
    assert settings == before
    catalog.validate_settings({**settings, "character_detail": "trigger_tags"})
    catalog.validate_settings({**settings, "pools": {"character": {"mode": "all", "excluded_ids": ["custom:pending"]}}})
    catalog.validate_settings({**settings, "random_character": False})


def test_generation_and_effective_snapshots_apply_custom_roles_in_place(catalog):
    from anima_webui.catalog import resolve_history_selection
    from anima_webui.workflow import validate_settings

    catalog.set_custom_items([
        {"id": "custom:ordered", "section": "character", "title": "顺序",
         "prompt": "hair bow, series, white shirt, hero, blue eyes"},
        {"id": "custom:features", "section": "character", "title": "无触发词",
         "trigger": [], "tags": ["blue eyes", "white shirt"]},
    ])
    settings = validate_settings({"quality_prompt": "", "manual_artist": "", "female_count": 0,
                                  "male_count": 0, "random_character": True,
                                  "pools": {"character": {"mode": "include", "ids": ["custom:ordered"]}}})
    expected = {"trigger_tags": "hair bow, series, white shirt, hero, blue eyes",
                "trigger": "series, hero", "trigger_tags_no_clothing": "hair bow, series, hero, blue eyes"}
    for mode, prompt in expected.items():
        current = {**settings, "character_detail": mode}
        result = catalog.resolve_prompt(current, 7)
        entry = result["selected"]["character"][0]
        assert entry["prompt"] == prompt
        assert entry["snapshot_version"] == 2
        restored, issues = resolve_history_selection(result["selected"], current, catalog)
        assert issues == {}
        assert restored["character"][0]["prompt"] == prompt
        assert result["full_prompt"] == prompt + ", "
    settings["pools"]["character"]["ids"] = ["custom:features"]
    settings["character_detail"] = "trigger"
    result = catalog.resolve_prompt(settings, 7)
    assert result["selected"]["character"][0]["prompt"] == ""
    restored, issues = resolve_history_selection(result["selected"], settings, catalog)
    assert issues == {}
    assert restored["character"][0]["prompt"] == ""
