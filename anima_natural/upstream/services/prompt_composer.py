"""Deterministic composition for hybrid Anima prompts.

The drawing director is deliberately allowed to be creative.  The last step
before a prompt reaches ComfyUI should be much less creative: preserve LoRA
control tags verbatim, keep explicit facts, remove only exact duplicates and
record every conservative correction.  This module provides that last step.

No diagnostic is written to disk.  :class:`PromptDiagnosticsStore` is a small,
bounded, thread-safe in-memory buffer intended for the plugin control page.
"""

from __future__ import annotations

import re
import threading
import time
import unicodedata
import uuid
from collections import OrderedDict
from dataclasses import asdict, dataclass, replace
from typing import Any, Callable, Iterable, Mapping, Sequence


_LORA_TAG_RE = re.compile(r"<\s*lora\s*:[^<>\r\n]+>", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")
_WORD_RE = re.compile(r"[A-Za-z]+(?:[-'][A-Za-z]+)?")
_WEIGHTED_TAG_RE = re.compile(r"^\(+(?P<tag>.+?):\s*[+-]?(?:\d+(?:\.\d+)?|\.\d+)\)+$")
_PLAIN_WEIGHT_RE = re.compile(r"^(?P<tag>.+?):\s*[+-]?(?:\d+(?:\.\d+)?|\.\d+)$")

_COMMON_ABBREVIATIONS = frozenset(
    {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "vs", "etc", "ver"}
)
_SUBJECT_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "she",
        "he",
        "they",
        "it",
        "her",
        "his",
        "their",
        "girl",
        "boy",
        "woman",
        "man",
        "character",
        "couple",
        "group",
    }
)
_SCENE_VERBS = frozenset(
    {
        "is",
        "are",
        "stands",
        "stand",
        "sits",
        "sit",
        "lies",
        "lie",
        "kneels",
        "kneel",
        "squats",
        "squat",
        "walks",
        "walk",
        "runs",
        "run",
        "holds",
        "hold",
        "wears",
        "wear",
        "looks",
        "look",
        "leans",
        "lean",
        "reaches",
        "reach",
        "rests",
        "rest",
        "floats",
        "float",
        "faces",
        "face",
        "watches",
        "watch",
        "turns",
        "turn",
        "raises",
        "raise",
        "lowers",
        "lower",
        "crouches",
        "crouch",
        "smiles",
        "smile",
    }
)
_SCENE_THIRD_PERSON_NOUNS = frozenset(
    {
        "arms",
        "bangs",
        "boots",
        "breasts",
        "buildings",
        "clothes",
        "clouds",
        "eyes",
        "fingers",
        "glasses",
        "gloves",
        "hands",
        "legs",
        "shoes",
        "sparks",
        "stairs",
        "stockings",
        "thighs",
        "toes",
        "trees",
        "waves",
    }
)
_DANBOORU_COUNT_TOKEN_RE = re.compile(
    r"^\d+(?:girls?|boys?|people|persons?)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PromptLayers:
    """The four physical parts of a three-layer hybrid prompt.

    LoRA controls are exposed separately because they must be placed before all
    ordinary hard tags.  Semantically they still belong to the hard layer.
    """

    lora_tags: tuple[str, ...] = ()
    hard_tags: tuple[str, ...] = ()
    visual_phrases: tuple[str, ...] = ()
    scene_sentence: str = ""

    @property
    def hard_layer(self) -> tuple[str, ...]:
        return self.lora_tags + self.hard_tags

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PromptDiagnostics:
    """One immutable, display-safe composition audit record."""

    diagnostic_id: str
    created_at: float
    source: str = ""
    provider_id: str = ""
    pipeline: str = ""
    adaptive_negative_mode: str = "conservative"
    anchors: tuple[tuple[str, str], ...] = ()
    duplicates_removed: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    discarded_tags: tuple[str, ...] = ()
    adaptive_negative_added: tuple[str, ...] = ()
    validation_warnings: tuple[str, ...] = ()
    unknown_tags: tuple[str, ...] = ()
    anchor_count: int = 0
    duplicates_removed_count: int = 0
    conflict_count: int = 0
    discarded_tag_count: int = 0
    adaptive_negative_count: int = 0
    validation_warning_count: int = 0
    unknown_tag_count: int = 0
    positive_prompt: str = ""
    negative_prompt: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def redacted(self) -> PromptDiagnostics:
        """Return a history-safe record containing counts but no prompt terms."""

        return replace(
            self,
            anchors=(),
            duplicates_removed=(),
            conflicts=(),
            discarded_tags=(),
            adaptive_negative_added=(),
            validation_warnings=(),
            unknown_tags=(),
            positive_prompt="",
            negative_prompt="",
        )


@dataclass(frozen=True)
class ComposedPrompt:
    """The final prompts and their in-memory diagnostic reference."""

    positive_prompt: str
    negative_prompt: str
    layers: PromptLayers
    diagnostic_id: str
    diagnostics: PromptDiagnostics

    @property
    def positive(self) -> str:
        return self.positive_prompt

    @property
    def negative(self) -> str:
        return self.negative_prompt


class PromptDiagnosticsStore:
    """A bounded and thread-safe in-memory diagnostics buffer."""

    def __init__(self, max_items: int = 200, *, capacity: int | None = None):
        size = capacity if capacity is not None else max_items
        if int(size) < 1:
            raise ValueError("diagnostics capacity must be at least 1")
        self._max_items = int(size)
        self._items: OrderedDict[str, PromptDiagnostics] = OrderedDict()
        self._lock = threading.RLock()

    @property
    def max_items(self) -> int:
        return self._max_items

    def put(self, diagnostics: PromptDiagnostics) -> str:
        if not isinstance(diagnostics, PromptDiagnostics):
            raise TypeError("diagnostics must be PromptDiagnostics")
        with self._lock:
            self._items.pop(diagnostics.diagnostic_id, None)
            self._items[diagnostics.diagnostic_id] = diagnostics
            while len(self._items) > self._max_items:
                self._items.popitem(last=False)
        return diagnostics.diagnostic_id

    add = put
    record = put

    def get(self, diagnostic_id: str) -> PromptDiagnostics | None:
        with self._lock:
            return self._items.get(str(diagnostic_id))

    def list(
        self,
        limit: int | None = None,
        *,
        newest_first: bool = True,
    ) -> tuple[PromptDiagnostics, ...]:
        with self._lock:
            values = tuple(self._items.values())
        if newest_first:
            values = tuple(reversed(values))
        if limit is not None:
            values = values[: max(0, int(limit))]
        return values

    snapshot = list

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


def _clean_text(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").replace("\x00", " ")).strip()


def _dedupe_key(value: str) -> str:
    """Return an exact-tag key without performing semantic normalization."""

    return unicodedata.normalize("NFKC", _clean_text(value)).casefold()


def _semantic_tag(value: str) -> str:
    text = _clean_text(value).replace(r"\(", "(").replace(r"\)", ")")
    match = _WEIGHTED_TAG_RE.match(text) or _PLAIN_WEIGHT_RE.match(text)
    if match:
        text = match.group("tag")
    text = text.strip(" ()[]{}")
    text = text.replace("_", " ").replace("-", " ").casefold()
    return _SPACE_RE.sub(" ", text).strip()


def _scan_delimiters(text: str, delimiters: frozenset[str]) -> list[int]:
    positions: list[int] = []
    stack: list[str] = []
    closing = {"(": ")", "[": "]", "{": "}", "<": ">"}
    quote = ""
    escaped = False
    for index, char in enumerate(text):
        word_apostrophe = (
            char == "'"
            and index > 0
            and index + 1 < len(text)
            and text[index - 1].isalnum()
            and text[index + 1].isalnum()
        )
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote and not word_apostrophe:
                quote = ""
            continue
        if char in {'"', "'"} and not word_apostrophe:
            quote = char
            continue
        if char in closing:
            stack.append(closing[char])
            continue
        if stack and char == stack[-1]:
            stack.pop()
            continue
        if not stack and char in delimiters:
            positions.append(index)
    return positions


def _split_top_level_commas(value: str) -> tuple[str, ...]:
    text = str(value or "")
    positions = _scan_delimiters(text, frozenset({",", "，", "\n", "\r"}))
    if not positions:
        cleaned = _clean_text(text.strip(" ,，\r\n"))
        return (cleaned,) if cleaned else ()
    terms: list[str] = []
    start = 0
    for position in positions:
        item = _clean_text(text[start:position].strip(" ,，\r\n"))
        if item:
            terms.append(item)
        start = position + 1
    item = _clean_text(text[start:].strip(" ,，\r\n"))
    if item:
        terms.append(item)
    return tuple(terms)


def _scene_likeness(value: str) -> bool:
    text = _clean_text(value)
    leading_match = re.match(r"^\s*([^\s,]+)", text)
    if leading_match and _DANBOORU_COUNT_TOKEN_RE.fullmatch(leading_match.group(1)):
        return False
    words = [word.casefold() for word in _WORD_RE.findall(text)]
    if len(words) < 5:
        return False
    # In a comma-only hybrid prompt the candidate suffix can still contain a
    # few preceding tags (``beach, night, she ...``).  Requiring the subject at
    # the actual beginning prevents us from swallowing those tags into prose.
    has_subject = words[0] in _SUBJECT_WORDS
    if not has_subject and text[:1].isupper() and len(words) >= 7:
        has_subject = True
    has_verb = bool(set(words) & _SCENE_VERBS) or any(
        word.endswith("ing") for word in words[1:]
    )
    if has_subject and not has_verb:
        has_verb = any(
            len(word) >= 4
            and word.endswith(("s", "es"))
            and word not in _SCENE_THIRD_PERSON_NOUNS
            for word in words[1:10]
        )
    return has_subject and has_verb


def _is_sentence_period(text: str, index: int) -> bool:
    if index <= 0 or index + 1 >= len(text):
        return False
    before = text[index - 1]
    after = text[index + 1]
    if before.isdigit() and after.isdigit():
        return False
    prefix_words = _WORD_RE.findall(text[:index])
    if prefix_words and prefix_words[-1].casefold() in _COMMON_ABBREVIATIONS:
        return False
    return after.isspace()


def split_hybrid_prompt(prompt: str) -> tuple[str, str]:
    """Split ``tag block. scene sentence`` without breaking model versions.

    A legacy comma-only hybrid prompt is also supported.  Decimal weights,
    version strings such as ``v1.2`` and periods inside ``<lora:...>`` are never
    treated as sentence boundaries.
    """

    text = _clean_text(prompt)
    if not text:
        return "", ""

    period_positions = _scan_delimiters(text, frozenset({"."}))
    for position in period_positions:
        if not _is_sentence_period(text, position):
            continue
        prefix = text[:position].strip(" ,.")
        suffix = text[position + 1 :].strip()
        if prefix and suffix and _scene_likeness(suffix):
            # A pure prose paragraph is not a tag block merely because it has a
            # period.  Hybrid prefixes normally contain a comma or a LoRA tag.
            if "," in prefix or "，" in prefix or _LORA_TAG_RE.search(prefix):
                return prefix, suffix

    # Older prompts often omitted the period before their final prose clause.
    comma_positions = _scan_delimiters(text, frozenset({",", "，"}))
    for position in reversed(comma_positions):
        prefix = text[:position].strip(" ,，.")
        suffix = text[position + 1 :].strip()
        if prefix and _scene_likeness(suffix):
            return prefix, suffix

    if _scene_likeness(text) and not comma_positions:
        return "", text
    return text.strip(" ,，"), ""


def _coerce_terms(value: str | Iterable[Any] | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return _split_top_level_commas(value)
    terms: list[str] = []
    for item in value:
        if item is None:
            continue
        if isinstance(item, str):
            terms.extend(_split_top_level_commas(item))
        else:
            cleaned = _clean_text(item)
            if cleaned:
                terms.append(cleaned)
    return tuple(terms)


def insert_tags_before_scene_sentence(
    prompt: str,
    tags: str | Iterable[str],
) -> str:
    """Insert tags into the tag block, never after scene prose.

    LoRA controls are recognized in either argument and kept ahead of ordinary
    tags, which makes the helper safe for both trigger words and control tags.
    """

    tag_block, scene = split_hybrid_prompt(prompt)
    additions = _coerce_terms(tags)
    existing_loras, existing_ordinary = _extract_loras(
        _split_top_level_commas(tag_block)
    )
    added_loras, added_ordinary = _extract_loras(additions)
    combined: list[str] = []
    seen: set[str] = set()
    for item in (*existing_loras, *added_loras, *existing_ordinary, *added_ordinary):
        key = _dedupe_key(item)
        if key and key not in seen:
            combined.append(item)
            seen.add(key)
    prefix = ", ".join(combined)
    if prefix and scene:
        return f"{prefix}. {scene}"
    return prefix or scene


def remove_prompt_terms(prompt: str, terms: Iterable[str]) -> str:
    """Remove exact top-level tags while preserving any trailing scene prose."""

    removal_keys = {_dedupe_key(term) for term in terms if _dedupe_key(term)}
    if not removal_keys:
        return _clean_text(prompt)
    tag_block, scene = split_hybrid_prompt(prompt)
    kept = tuple(
        term
        for term in _split_top_level_commas(tag_block)
        if _dedupe_key(term) not in removal_keys
    )
    prefix = ", ".join(kept)
    if prefix and scene:
        return f"{prefix}. {scene}"
    return prefix or scene


def _looks_like_visual_phrase(value: str) -> bool:
    text = _clean_text(value)
    if not text or _LORA_TAG_RE.fullmatch(text):
        return False
    words = _WORD_RE.findall(text)
    if len(words) >= 6:
        return True
    relation_words = {
        "across",
        "around",
        "beneath",
        "between",
        "through",
        "toward",
        "while",
    }
    return len(words) >= 4 and bool(
        {word.casefold() for word in words} & relation_words
    )


def _extract_loras(terms: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    loras: list[str] = []
    ordinary: list[str] = []
    for raw in terms:
        value = _clean_text(raw)
        if not value:
            continue
        matches = tuple(_LORA_TAG_RE.finditer(value))
        if not matches:
            ordinary.append(value)
            continue
        for match in matches:
            loras.append(_clean_text(match.group(0)))
        residue = _LORA_TAG_RE.sub(" ", value).strip(" ,，")
        if residue:
            ordinary.extend(_split_top_level_commas(residue))
    return tuple(loras), tuple(ordinary)


def _normalize_anchor_category(value: str) -> str:
    category = _semantic_tag(value).replace(" ", "_")
    aliases = {
        "time_of_day": "time",
        "lighting_time": "time",
        "environment": "space",
        "location_type": "space",
        "eye_state": "eyes",
        "view_direction": "gaze",
        "color_mode": "colour",
        "subject_count": "count",
        "person_count": "count",
    }
    return aliases.get(category, category)


def _coerce_anchors(value: Any) -> tuple[tuple[str, str], ...]:
    if not value:
        return ()
    if isinstance(value, Mapping):
        if any(key in value for key in ("value", "tag", "name")):
            items: Sequence[Any] = (value,)
        else:
            expanded: list[Any] = []
            for category, values in value.items():
                if isinstance(values, str) or not isinstance(values, Iterable):
                    values = (values,)
                expanded.extend((item, category) for item in values)
            items = expanded
    elif isinstance(value, str):
        items = (value,)
    else:
        items = tuple(value)

    anchors: list[tuple[str, str]] = []
    for item in items:
        category = ""
        raw_value: Any = item
        if isinstance(item, Mapping):
            raw_value = item.get("value", item.get("tag", item.get("name", "")))
            category = str(item.get("category", item.get("kind", "")) or "")
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            raw_value, category = item[0], str(item[1] or "")
        for term in _coerce_terms(str(raw_value or "")):
            anchors.append((term, _normalize_anchor_category(category)))
    return tuple(anchors)


_CONFLICT_VALUES: dict[str, dict[str, str]] = {
    "time": {
        "day": "day",
        "daytime": "day",
        "night": "night",
        "nighttime": "night",
    },
    "space": {
        "indoors": "indoors",
        "indoor": "indoors",
        "outdoors": "outdoors",
        "outdoor": "outdoors",
    },
    "eyes": {
        "closed eyes": "closed",
        "eyes closed": "closed",
        "open eyes": "open",
        "eyes open": "open",
    },
    "gaze": {"looking at viewer": "viewer", "looking away": "away"},
    "colour": {
        "monochrome": "mono",
        "grayscale": "mono",
        "greyscale": "mono",
        "full color": "colour",
        "colorful": "colour",
    },
    "count": {
        "solo": "solo",
        "multiple girls": "multiple",
        "multiple boys": "multiple",
        "group": "multiple",
        "crowd": "multiple",
    },
}


def _conflict_identity(tag: str, category: str = "") -> tuple[str, str] | None:
    semantic = _semantic_tag(tag)
    normalized_category = _normalize_anchor_category(category)
    if normalized_category in _CONFLICT_VALUES:
        value = _CONFLICT_VALUES[normalized_category].get(semantic, semantic)
        return normalized_category, value
    for group, values in _CONFLICT_VALUES.items():
        if semantic in values:
            return group, values[semantic]
    if re.fullmatch(
        r"[2-9]\d*(?:girls?|boys?|people|persons?)", semantic.replace(" ", "")
    ):
        return "count", "multiple"
    return None


def _tag_index_known(
    tag_index: Any,
    value: str,
    category: str = "",
) -> bool | None:
    if tag_index is None:
        return None
    semantic = _semantic_tag(value)
    candidates = (semantic, semantic.replace(" ", "_"), semantic.replace("_", " "))
    if not semantic:
        return None
    try:
        status = getattr(tag_index, "status", None)
        if callable(status):
            readiness = status()
            if isinstance(readiness, Mapping) and not bool(readiness.get("ready")):
                return None
        if isinstance(tag_index, Mapping):
            normalized_keys = {_semantic_tag(str(key)): key for key in tag_index.keys()}
            return any(
                _semantic_tag(candidate) in normalized_keys for candidate in candidates
            )
        if callable(tag_index):
            result = tag_index(value)
            return bool(result)
        for method_name in ("contains", "is_known", "validate", "lookup", "resolve"):
            method = getattr(tag_index, method_name, None)
            if callable(method):
                try:
                    result = method(value, category) if category else method(value)
                except TypeError:
                    result = method(value)
                if result is None:
                    return None
                if hasattr(result, "verified"):
                    return bool(getattr(result, "verified"))
                if isinstance(result, Mapping):
                    if "verified" in result:
                        return bool(result["verified"])
                    if "found" in result:
                        return bool(result["found"])
                if hasattr(result, "found"):
                    return bool(getattr(result, "found"))
                return bool(result)
        return any(candidate in tag_index for candidate in candidates)
    except (KeyError, TypeError, ValueError):
        return False
    except Exception:
        # A local optional index must never make the drawing path unavailable.
        return None


def _should_validate(value: str) -> bool:
    if _LORA_TAG_RE.fullmatch(_clean_text(value)):
        return False
    semantic = _semantic_tag(value)
    return bool(semantic) and len(_WORD_RE.findall(semantic)) <= 6


def _adaptive_negative_tags(
    mode: str, positive_terms: Sequence[str]
) -> tuple[str, ...]:
    if mode == "off":
        return ()
    semantic = {_semantic_tag(term) for term in positive_terms}
    joined = " | ".join(sorted(semantic))
    result: list[str] = []
    if mode == "standard":
        result.extend(("lowres", "worst quality", "low quality", "bad anatomy"))

    def add_if(markers: Iterable[str], tags: Iterable[str]) -> None:
        if any(marker in joined for marker in markers):
            result.extend(tags)

    add_if(
        ("hand", "finger", "holding", "grabbing", "peace sign"),
        ("bad hands", "malformed hands", "extra fingers", "missing fingers"),
    )
    add_if(
        ("feet", "foot", "toes", "barefoot", "full body"),
        ("bad feet", "malformed feet", "extra toes"),
    )
    add_if(
        ("2girls", "2boys", "multiple", "group", "crowd", "couple"),
        ("fused bodies", "duplicated person", "extra limbs"),
    )
    add_if(
        (
            "from below",
            "from above",
            "foreshortening",
            "fisheye",
            "wide angle",
            "worm's eye",
            "bird's eye",
        ),
        ("bad perspective", "distorted perspective"),
    )
    add_if(
        ("text", "lettering", "signboard", "caption"),
        ("garbled text", "misspelled text"),
    )
    return tuple(dict.fromkeys(result))


class PromptComposer:
    """Compose a stable three-layer prompt and retain a bounded audit."""

    _MODES = frozenset({"off", "conservative", "standard"})
    _VALIDATION_MODES = frozenset({"off", "report", "guarded", "strict"})

    def __init__(
        self,
        adaptive_negative_mode: str = "conservative",
        diagnostics_store: PromptDiagnosticsStore | None = None,
        tag_index: Any = None,
        validation_mode: str = "report",
        include_content: bool = False,
    ):
        mode = str(adaptive_negative_mode or "conservative").strip().casefold()
        validation = str(validation_mode or "report").strip().casefold()
        if mode not in self._MODES:
            raise ValueError(
                "adaptive_negative_mode must be off, conservative or standard"
            )
        if validation not in self._VALIDATION_MODES:
            raise ValueError("validation_mode must be off, report, guarded or strict")
        self.adaptive_negative_mode = mode
        self.diagnostics_store = (
            diagnostics_store
            if diagnostics_store is not None
            else PromptDiagnosticsStore()
        )
        self.tag_index = tag_index
        self.validation_mode = validation
        self.include_content = bool(include_content)

    def compose(
        self,
        positive_prompt: str,
        negative_prompt: str = "",
        hard_tags: str | Iterable[Any] | None = None,
        visual_phrases: str | Iterable[Any] | None = None,
        scene_sentence: str = "",
        anchors: Any = (),
        source: str = "",
        provider_id: str = "",
        pipeline: str = "",
    ) -> ComposedPrompt:
        raw_tag_block, raw_scene = split_hybrid_prompt(positive_prompt)
        raw_terms = _split_top_level_commas(raw_tag_block)
        raw_loras, raw_ordinary = _extract_loras(raw_terms)

        anchor_pairs = _coerce_anchors(anchors)
        anchor_loras, anchor_ordinary = _extract_loras(item[0] for item in anchor_pairs)
        anchor_category_by_key = {
            _dedupe_key(value): category for value, category in anchor_pairs
        }

        generated_hard = _coerce_terms(hard_tags)
        generated_loras, generated_ordinary = _extract_loras(generated_hard)
        explicit_visual = _coerce_terms(visual_phrases)
        visual_loras, explicit_visual = _extract_loras(explicit_visual)

        duplicates: list[str] = []

        def exact_dedupe(values: Iterable[str]) -> tuple[str, ...]:
            result: list[str] = []
            seen: set[str] = set()
            for item in values:
                key = _dedupe_key(item)
                if not key:
                    continue
                if key in seen:
                    duplicates.append(item)
                    continue
                seen.add(key)
                result.append(item)
            return tuple(result)

        lora_tags = exact_dedupe(
            (*raw_loras, *anchor_loras, *generated_loras, *visual_loras)
        )

        raw_hard: list[str] = []
        raw_visual: list[str] = []
        for item in raw_ordinary:
            (raw_visual if _looks_like_visual_phrase(item) else raw_hard).append(item)

        anchor_hard = tuple(anchor_ordinary)
        conflicts: list[str] = []
        discarded: list[str] = []
        established_conflicts: dict[str, tuple[str, str, str]] = {}

        def accept_tag(value: str, origin: str, category: str = "") -> bool:
            identity = _conflict_identity(value, category)
            if identity is None:
                return True
            group, variant = identity
            prior = established_conflicts.get(group)
            if prior and prior[0] != variant:
                message = (
                    f"{group}: {prior[1]} ({prior[2]}) conflicts with "
                    f"{value} ({origin})"
                )
                conflicts.append(message)
                # Only later generated tags may be dropped.  Input and anchor
                # facts are retained and merely reported for human inspection.
                if origin == "generated":
                    discarded.append(value)
                    return False
            else:
                established_conflicts[group] = (variant, value, origin)
            return True

        accepted_anchor = tuple(
            item
            for item in anchor_hard
            if accept_tag(
                item, "anchor", anchor_category_by_key.get(_dedupe_key(item), "")
            )
        )
        accepted_raw = tuple(item for item in raw_hard if accept_tag(item, "input"))
        accepted_generated = tuple(
            item for item in generated_ordinary if accept_tag(item, "generated")
        )
        final_hard = exact_dedupe(
            (*accepted_anchor, *accepted_raw, *accepted_generated)
        )
        provisional_visual = exact_dedupe((*raw_visual, *explicit_visual))
        hard_keys = {_dedupe_key(item) for item in final_hard}
        final_visual_list: list[str] = []
        for item in provisional_visual:
            if _dedupe_key(item) in hard_keys:
                duplicates.append(item)
                continue
            final_visual_list.append(item)
        final_visual = tuple(final_visual_list)

        explicit_scene = _clean_text(scene_sentence)
        chosen_scene = raw_scene or explicit_scene
        if (
            raw_scene
            and explicit_scene
            and _dedupe_key(raw_scene) != _dedupe_key(explicit_scene)
        ):
            conflicts.append("scene: input scene conflicts with later generated scene")
            discarded.append(explicit_scene)

        layers = PromptLayers(
            lora_tags=lora_tags,
            hard_tags=final_hard,
            visual_phrases=final_visual,
            scene_sentence=chosen_scene,
        )
        prefix = ", ".join((*lora_tags, *final_hard, *final_visual))
        final_positive = (
            f"{prefix}. {chosen_scene}"
            if prefix and chosen_scene
            else prefix or chosen_scene
        )

        negative_terms = list(_split_top_level_commas(negative_prompt))
        positive_keys = {
            _dedupe_key(item) for item in (*lora_tags, *final_hard, *final_visual)
        }
        negative_seen: set[str] = set()
        final_negative: list[str] = []
        for item in negative_terms:
            key = _dedupe_key(item)
            if not key or key in negative_seen:
                if key:
                    duplicates.append(item)
                continue
            negative_seen.add(key)
            final_negative.append(item)

        adaptive_added: list[str] = []
        suggestions = _adaptive_negative_tags(
            self.adaptive_negative_mode,
            (*final_hard, *final_visual, chosen_scene),
        )
        for item in suggestions:
            key = _dedupe_key(item)
            if key in positive_keys or key in negative_seen:
                continue
            final_negative.append(item)
            negative_seen.add(key)
            adaptive_added.append(item)
        final_negative_text = ", ".join(final_negative)

        unknown: list[str] = []
        guarded_unknown: list[str] = []
        warnings: list[str] = []
        if self.validation_mode != "off" and self.tag_index is not None:
            for item in final_hard:
                if not _should_validate(item):
                    continue
                category = anchor_category_by_key.get(_dedupe_key(item), "")
                known = _tag_index_known(self.tag_index, item, category)
                if known is False:
                    unknown.append(item)
                    warnings.append(f"unknown tag: {item}")
                    if category in {"character", "copyright", "artist"}:
                        guarded_unknown.append(item)
        if self.validation_mode == "guarded" and guarded_unknown:
            raise ValueError(
                "unknown guarded identity tags: " + ", ".join(guarded_unknown)
            )
        if self.validation_mode == "strict" and unknown:
            raise ValueError("unknown prompt tags: " + ", ".join(unknown))

        diagnostic_id = uuid.uuid4().hex
        diagnostics = PromptDiagnostics(
            diagnostic_id=diagnostic_id,
            created_at=time.time(),
            source=_clean_text(source),
            provider_id=_clean_text(provider_id),
            pipeline=_clean_text(pipeline),
            adaptive_negative_mode=self.adaptive_negative_mode,
            anchors=anchor_pairs,
            duplicates_removed=tuple(duplicates),
            conflicts=tuple(conflicts),
            discarded_tags=tuple(discarded),
            adaptive_negative_added=tuple(adaptive_added),
            validation_warnings=tuple(warnings),
            unknown_tags=tuple(unknown),
            anchor_count=len(anchor_pairs),
            duplicates_removed_count=len(duplicates),
            conflict_count=len(conflicts),
            discarded_tag_count=len(discarded),
            adaptive_negative_count=len(adaptive_added),
            validation_warning_count=len(warnings),
            unknown_tag_count=len(unknown),
            positive_prompt=final_positive if self.include_content else "",
            negative_prompt=final_negative_text if self.include_content else "",
        )
        store = self.diagnostics_store
        stored_diagnostics = (
            diagnostics if self.include_content else diagnostics.redacted()
        )
        if hasattr(store, "put"):
            store.put(stored_diagnostics)
        elif hasattr(store, "add"):
            store.add(stored_diagnostics)
        else:
            raise TypeError("diagnostics_store must provide put() or add()")
        return ComposedPrompt(
            positive_prompt=final_positive,
            negative_prompt=final_negative_text,
            layers=layers,
            diagnostic_id=diagnostic_id,
            diagnostics=diagnostics,
        )

    def get_diagnostics(self, diagnostic_id: str) -> PromptDiagnostics | None:
        getter: Callable[[str], PromptDiagnostics | None] | None = getattr(
            self.diagnostics_store, "get", None
        )
        return getter(diagnostic_id) if getter else None


def compose_prompt(
    positive_prompt: str,
    negative_prompt: str = "",
    **kwargs: Any,
) -> ComposedPrompt:
    """Convenience wrapper for one-off deterministic composition."""

    composer_keys = {
        "adaptive_negative_mode",
        "diagnostics_store",
        "tag_index",
        "validation_mode",
        "include_content",
    }
    composer_kwargs = {
        key: kwargs.pop(key) for key in tuple(kwargs) if key in composer_keys
    }
    return PromptComposer(**composer_kwargs).compose(
        positive_prompt,
        negative_prompt,
        **kwargs,
    )


__all__ = [
    "ComposedPrompt",
    "PromptComposer",
    "PromptDiagnostics",
    "PromptDiagnosticsStore",
    "PromptLayers",
    "compose_prompt",
    "insert_tags_before_scene_sentence",
    "remove_prompt_terms",
    "split_hybrid_prompt",
]
