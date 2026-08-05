"""Deterministic runtime LoRA merging and trigger-word planning."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Mapping

from ..core.lora import LoraWorkflowError, canonical_lora_name
from ..models import LoraSelection
from .danbooru_index import escape_prompt_tag
from .lora_catalog import FUNCTIONAL_LORA_CATEGORIES, LoraRecord
from .lora_presets import (
    LoraPreset,
    PRESET_CATEGORY_ARTIST_STYLE,
    PRESET_CATEGORY_CHARACTER,
    PRESET_CATEGORY_MIXED,
    deduplicate_selections,
)
from .prompt_composer import (
    insert_tags_before_scene_sentence,
    split_hybrid_prompt,
)


_TRIGGER_SPLIT_RE = re.compile(r"\s*(?:,,|[,，;；\n\r]+)\s*")
_PROMPT_TERM_SPLIT_RE = re.compile(r"\s*(?:,,|[,，;；\n\r]+)\s*")
_WEIGHT_SUFFIX_RE = re.compile(r":\s*[+-]?(?:\d+(?:\.\d+)?|\.\d+)\s*$")
_METADATA_ESCAPE_RE = re.compile(r"\\([()\[\]{},])")
_GENERIC_CHARACTER_TRIGGERS = frozenset(
    {
        "1girl",
        "1boy",
        "solo",
        "character",
        "anime character",
        "masterpiece",
        "best quality",
        "high quality",
        "very aesthetic",
    }
)
_APPEARANCE_OR_OUTFIT_MARKERS = (
    "hair",
    "eyes",
    "eye",
    "dress",
    "uniform",
    "outfit",
    "clothes",
    "clothing",
    "shirt",
    "skirt",
    "coat",
    "jacket",
    "pants",
    "boots",
    "shoes",
    "gloves",
    "hat",
    "armor",
    "weapon",
    "swimsuit",
    "bikini",
    "lingerie",
    "bodysuit",
    "leotard",
    "underwear",
    "panties",
    "bra",
    "stockings",
    "thighhighs",
    "发色",
    "头发",
    "眼睛",
    "服装",
    "衣服",
    "制服",
    "裙",
    "外套",
    "鞋",
    "武器",
)


@dataclass(frozen=True)
class LoraMergePlan:
    """The authoritative runtime stack plus ignored locked overrides."""

    selections: tuple[LoraSelection, ...]
    ignored_locked_overrides: tuple[str, ...] = ()


@dataclass(frozen=True)
class LoraTriggerPlan:
    """A prompt with evidence-backed trigger words and an audit trail."""

    prompt: str
    added: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()


def _canonical_key(value: str) -> str:
    return canonical_lora_name(value).casefold()


def _term_key(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    text = text.strip("()[]{}<>‘’“”\"' ")
    text = _WEIGHT_SUFFIX_RE.sub("", text)
    return re.sub(r"[^0-9a-z@_\u3400-\u9fff]+", "", text)


def _split_trigger_text(value: str) -> tuple[str, ...]:
    return tuple(
        token.strip()
        for token in _TRIGGER_SPLIT_RE.split(str(value or ""))
        if token.strip()
    )


def _bound_activation_terms(values: Iterable[str]) -> tuple[str, ...]:
    """Normalize file-bound activation terms without treating them as identity."""

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        for token in _split_trigger_text(str(raw or "")):
            value = token.strip(" ,")
            if not value:
                continue
            if "<lora:" in value.casefold() or "\x00" in value:
                raise LoraWorkflowError(
                    "bound character activation term contains forbidden syntax"
                )
            # Activation terms are literal prompt tags, not Comfy weighting
            # expressions. Keep their spelling while emitting exactly one
            # escape layer around parentheses.
            value = re.sub(r"\\*([()])", r"\\\1", value)
            key = _term_key(value)
            if not key or key in seen:
                continue
            seen.add(key)
            normalized.append(value)
    return tuple(normalized)


def _clean_metadata_trigger(value: str) -> str:
    """Restore punctuation escaped by Civitai/Markdown serialization."""

    return _METADATA_ESCAPE_RE.sub(r"\1", str(value or "").strip())


def _prompt_term_keys(value: str) -> set[str]:
    return {
        key
        for token in _PROMPT_TERM_SPLIT_RE.split(str(value or ""))
        if (key := _term_key(token))
    }


def _validate_groups(groups: Iterable[Iterable[LoraSelection]]) -> None:
    for group in groups:
        deduplicate_selections(tuple(group))


def merge_runtime_lora_selections(
    presets: tuple[LoraPreset, ...],
    *requested_groups: Iterable[LoraSelection],
) -> LoraMergePlan:
    """Merge LoRAs while keeping saved style/mixed preset weights immutable.

    Character presets remain adjustable by an explicit command or LLM choice.
    Later explicit groups win for every non-locked LoRA.
    """

    _validate_groups(
        (
            *(preset.selections for preset in presets),
            *requested_groups,
        )
    )
    ordered: list[str] = []
    values: dict[str, LoraSelection] = {}
    locked: set[str] = set()
    ignored: list[str] = []

    for preset in presets:
        preset_locks_weights = preset.category in {
            PRESET_CATEGORY_ARTIST_STYLE,
            PRESET_CATEGORY_MIXED,
        }
        for selection in preset.selections:
            normalized = deduplicate_selections((selection,))[0]
            key = _canonical_key(normalized.name)
            if key not in values:
                ordered.append(key)
                values[key] = normalized
            elif key not in locked:
                values[key] = normalized
            if preset_locks_weights:
                locked.add(key)

    for group in requested_groups:
        for selection in deduplicate_selections(tuple(group)):
            key = _canonical_key(selection.name)
            if key in locked:
                if values[key].strength != selection.strength:
                    ignored.append(selection.name)
                continue
            if key not in values:
                ordered.append(key)
            values[key] = selection

    return LoraMergePlan(
        selections=tuple(values[key] for key in ordered),
        ignored_locked_overrides=tuple(dict.fromkeys(ignored)),
    )


def _identity_terms(record: LoraRecord) -> tuple[str, ...]:
    values: list[str] = []
    # Do not use aliases here.  Catalog aliases may themselves contain every
    # trained word (including clothes), which would circularly "prove" an
    # outfit tag to be a character identity trigger.
    for raw in (record.character_name,):
        for part in re.split(r"\s*(?:/|\||；|;|,|，)\s*", str(raw or "")):
            key = _term_key(part)
            if len(key) >= 3 and key not in values:
                values.append(key)
    return tuple(values)


def is_character_identity_trigger_candidate(trigger: str) -> bool:
    folded = unicodedata.normalize("NFKC", trigger).casefold()
    key = _term_key(trigger)
    if not key or folded.strip() in _GENERIC_CHARACTER_TRIGGERS:
        return False
    if any(marker in folded for marker in _APPEARANCE_OR_OUTFIT_MARKERS):
        return False
    return True


def _metadata_trigger_terms(record: LoraRecord) -> tuple[str, ...]:
    """Return atomic prompt terms from Manager/Civitai trained-word entries.

    Civitai commonly stores one trained-word item as ``identity, outfit, detail``.
    Treating that whole string as one identity breaks prompt validation and lets a
    multi-character LoRA silently select the first character.  Split every entry
    before identity classification while preserving first-seen order.
    """

    terms: list[str] = []
    seen: set[str] = set()
    for raw in record.trigger_words:
        cleaned = _clean_metadata_trigger(raw)
        for term in _split_trigger_text(cleaned):
            key = _term_key(term)
            if not key or key in seen:
                continue
            seen.add(key)
            terms.append(term)
    return tuple(terms)


def atomic_lora_trigger_terms(record: LoraRecord) -> tuple[str, ...]:
    """Expose bounded atomic Manager/Civitai trigger terms for identity validation.

    Metadata category labels are advisory.  Callers that enforce character
    identity must inspect these terms even when a record is mislabeled as a
    style LoRA or has no ``character_name`` field.
    """

    return _metadata_trigger_terms(record)


def character_identity_trigger_candidates(record: LoraRecord) -> tuple[str, ...]:
    """Return every metadata term that is proven to name this record's character."""

    candidates = _metadata_trigger_terms(record)
    identities = _identity_terms(record)
    result: list[str] = []
    for trigger in candidates:
        trigger_key = _term_key(trigger)
        comparable_trigger = trigger_key.replace("_", "")
        if is_character_identity_trigger_candidate(trigger) and any(
            (comparable_identity := identity.replace("_", ""))
            and (
                comparable_identity in comparable_trigger
                or comparable_trigger in comparable_identity
            )
            for identity in identities
        ):
            result.append(trigger)
    if not result:
        fallback = tuple(
            trigger
            for trigger in candidates
            if is_character_identity_trigger_candidate(trigger)
        )
        if len(fallback) == 1:
            return fallback
    return tuple(result)


def choose_character_identity_trigger(
    record: LoraRecord,
    identity_hints: Iterable[str] = (),
) -> str:
    """Choose one identity term, never the first character by accident.

    ``identity_hints`` are query-derived names, not authority.  They only select
    among identity terms already proven by the current record metadata.  Without
    hints, a multi-character LoRA is intentionally ambiguous.
    """

    candidates = character_identity_trigger_candidates(record)
    if not candidates:
        return ""
    if len(candidates) == 1:
        return candidates[0]
    hint_keys = tuple(
        dict.fromkeys(
            key for value in identity_hints if (key := _term_key(str(value or "")))
        )
    )
    if hint_keys:
        matched = tuple(
            trigger
            for trigger in candidates
            if any(
                hint == (trigger_key := _term_key(trigger))
                or hint in trigger_key
                or trigger_key in hint
                for hint in hint_keys
                if len(hint) >= 3
            )
        )
        unique = tuple(dict.fromkeys(_term_key(item) for item in matched))
        if len(unique) == 1:
            return matched[0]
        return ""
    return ""


def build_lora_trigger_plan(
    *,
    prompt: str,
    negative_prompt: str,
    selections: tuple[LoraSelection, ...],
    records_by_name: Mapping[str, LoraRecord],
    presets: tuple[LoraPreset, ...] = (),
    suppressed_terms: Iterable[str] = (),
    verified_character_triggers: Mapping[str, str] | None = None,
    bound_character_activation_terms: Mapping[str, Iterable[str]] | None = None,
) -> LoraTriggerPlan:
    """Append only explicit, role-appropriate trigger words.

    Style and functional LoRAs receive every metadata trigger.  Character
    LoRAs receive one reliable identity trigger only, so default clothes and
    appearance tags cannot defeat an explicit outfit change.  A preset's
    manually saved trigger string supplements the latest role-appropriate
    Manager metadata for its members. ``verified_character_triggers`` may
    select one exact metadata identity from a legacy multi-character LoRA and
    must still match current metadata. ``bound_character_activation_terms``
    are already authorized by a file-bound Danbooru identity binding; they are
    injected as activation only and never become character identity evidence.
    """

    prompt_text = str(prompt or "").strip(" ,")
    existing = _prompt_term_keys(prompt_text)
    negative = _prompt_term_keys(negative_prompt)
    suppressed = {
        key for value in suppressed_terms if (key := _term_key(str(value or "")))
    }
    added: list[str] = []
    skipped: list[str] = []
    preset_roles: dict[str, str] = {}
    manual_triggers: list[tuple[str, str]] = []
    verified_overrides = {
        _canonical_key(name): str(trigger or "").strip()
        for name, trigger in (verified_character_triggers or {}).items()
        if _canonical_key(name)
    }
    bound_activation_overrides = {
        _canonical_key(name): _bound_activation_terms(terms)
        for name, terms in (bound_character_activation_terms or {}).items()
        if _canonical_key(name)
    }

    def append_trigger(trigger: str, source: str) -> None:
        value = trigger.strip(" ,")
        key = _term_key(value)
        if not value or not key:
            return
        if key in suppressed:
            skipped.append(f"{source}: trigger suppressed by semantic rewrite: {value}")
            return
        if key in negative:
            skipped.append(f"{source}: trigger conflicts with negative prompt: {value}")
            return
        if key in existing:
            return
        existing.add(key)
        added.append(value)

    for preset in presets:
        for selection in preset.selections:
            key = _canonical_key(selection.name)
            resolved_record = records_by_name.get(key)
            resolved_key = (
                _canonical_key(resolved_record.name) if resolved_record is not None else key
            )
            current = preset_roles.get(key)
            if current != PRESET_CATEGORY_ARTIST_STYLE:
                preset_roles[key] = preset.category
            current = preset_roles.get(resolved_key)
            if current != PRESET_CATEGORY_ARTIST_STYLE:
                preset_roles[resolved_key] = preset.category
        manual = _split_trigger_text(preset.trigger_words)
        if not manual:
            continue
        for trigger in manual:
            manual_triggers.append((trigger, f"preset {preset.name}"))

    effective_roles: dict[str, str] = {}
    resolved_character_triggers: dict[str, tuple[str, ...]] = {}
    for selection in selections:
        key = _canonical_key(selection.name)
        record = records_by_name.get(key)
        if record is None:
            if key in verified_overrides or key in bound_activation_overrides:
                raise LoraWorkflowError(
                    f"verified character trigger has no fresh LoRA metadata: {selection.name}"
                )
            continue
        record_category = str(record.category or "").strip().casefold()
        # Catalog character evidence is authoritative.  A saved style preset
        # must never turn a character LoRA into a style LoRA and thereby inject
        # every trained word, including default clothes and appearance.
        if (
            record_category == "character"
            or str(record.character_name or "").strip()
            or key in verified_overrides
            or key in bound_activation_overrides
        ):
            role = PRESET_CATEGORY_CHARACTER
        else:
            role = preset_roles.get(key) or record_category
        effective_roles[key] = role

        has_override = key in verified_overrides
        has_bound_activation = key in bound_activation_overrides
        if role != PRESET_CATEGORY_CHARACTER:
            if has_override or has_bound_activation:
                raise LoraWorkflowError(
                    f"verified character trigger targets non-character LoRA: {selection.name}"
                )
            continue

        if has_bound_activation:
            terms = bound_activation_overrides[key]
            if not terms:
                raise LoraWorkflowError(
                    f"bound character LoRA has no activation terms: {selection.name}"
                )
            resolved_character_triggers[key] = terms
            continue

        if not has_override:
            trigger = choose_character_identity_trigger(record)
            resolved_character_triggers[key] = (trigger,) if trigger else ()
            continue

        override_key = _term_key(verified_overrides[key])
        override_candidates = tuple(
            trigger
            for trigger in atomic_lora_trigger_terms(record)
            if is_character_identity_trigger_candidate(trigger)
        )
        matches = tuple(
            candidate
            for candidate in override_candidates
            if override_key and _term_key(candidate) == override_key
        )
        if not matches and (
            record_category == "character" or str(record.character_name or "").strip()
        ):
            identity_anchor_matches = tuple(
                part.strip()
                for part in re.split(
                    r"\s*(?:/|\||；|;|,|，)\s*",
                    str(record.character_name or ""),
                )
                if part.strip()
                and part.strip().isascii()
                and re.fullmatch(r"[A-Za-z0-9_ .@'!&+:/\-]{2,80}", part.strip())
                and _term_key(part) == override_key
                and is_character_identity_trigger_candidate(part)
            )
            if len(identity_anchor_matches) == 1:
                matches = identity_anchor_matches
        if len(matches) != 1:
            raise LoraWorkflowError(
                f"verified character trigger does not match LoRA metadata: {selection.name}"
            )
        # This path is backed by an exact Danbooru character binding. Emit its
        # prompt form with one (and only one) parenthesis escape layer. Other
        # Manager, preset and user-authored terms remain byte-for-byte free-form.
        resolved_character_triggers[key] = (escape_prompt_tag(matches[0]),)

    conflicting_character_terms: set[str] = set()
    for selection in selections:
        key = _canonical_key(selection.name)
        record = records_by_name.get(key)
        if record is None:
            continue
        role = effective_roles.get(key) or str(record.category or "").casefold()
        if role != PRESET_CATEGORY_CHARACTER:
            continue
        identity_trigger_keys = {
            _term_key(value)
            for value in resolved_character_triggers.get(key, ())
            if _term_key(value)
        }
        for trigger in _metadata_trigger_terms(record):
            trigger_key = _term_key(trigger)
            if (
                trigger_key
                and trigger_key not in identity_trigger_keys
                and trigger_key in negative
            ):
                conflicting_character_terms.add(trigger_key)

    if conflicting_character_terms:
        tag_block, scene_sentence = split_hybrid_prompt(prompt_text)
        kept_terms: list[str] = []
        for term in _PROMPT_TERM_SPLIT_RE.split(tag_block):
            value = term.strip()
            if not value:
                continue
            if _term_key(value) in conflicting_character_terms:
                skipped.append(
                    f"removed positive character metadata term conflicting with negative: {value}"
                )
                continue
            kept_terms.append(value)
        prompt_text = insert_tags_before_scene_sentence(scene_sentence, kept_terms)
        existing = _prompt_term_keys(prompt_text)

    for trigger, source in manual_triggers:
        append_trigger(trigger, source)

    for selection in selections:
        key = _canonical_key(selection.name)
        record = records_by_name.get(key)
        if record is None:
            skipped.append(f"{selection.name}: no fresh metadata record")
            continue
        triggers = _metadata_trigger_terms(record)
        if not triggers:
            skipped.append(f"{selection.name}: no metadata trigger words")
            continue

        role = effective_roles.get(key) or str(record.category or "").casefold()
        if role == PRESET_CATEGORY_ARTIST_STYLE or role in FUNCTIONAL_LORA_CATEGORIES:
            for trigger in triggers:
                append_trigger(trigger, selection.name)
            continue
        if role == PRESET_CATEGORY_CHARACTER or role == "character":
            triggers = resolved_character_triggers.get(key, ())
            if triggers:
                for trigger in triggers:
                    append_trigger(trigger, selection.name)
            else:
                skipped.append(
                    f"{selection.name}: no reliable character identity trigger"
                )
            continue
        if role == PRESET_CATEGORY_MIXED or role == "mixed":
            if record.character_name:
                character_triggers = resolved_character_triggers.get(key, ())
                if character_triggers:
                    for trigger in character_triggers:
                        append_trigger(trigger, selection.name)
                else:
                    skipped.append(
                        f"{selection.name}: mixed LoRA has no reliable identity trigger"
                    )
            else:
                for trigger in triggers:
                    append_trigger(trigger, selection.name)
            continue
        skipped.append(f"{selection.name}: unclassified trigger words not auto-applied")

    combined = insert_tags_before_scene_sentence(prompt_text, added)
    return LoraTriggerPlan(
        prompt=combined,
        added=tuple(added),
        skipped=tuple(skipped),
    )


__all__ = [
    "LoraMergePlan",
    "LoraTriggerPlan",
    "build_lora_trigger_plan",
    "character_identity_trigger_candidates",
    "choose_character_identity_trigger",
    "is_character_identity_trigger_candidate",
    "merge_runtime_lora_selections",
]
