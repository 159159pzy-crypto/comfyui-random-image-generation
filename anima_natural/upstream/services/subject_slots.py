"""Deterministic source-subject selection for bounded multi-person swaps.

The source selector is natural language.  This module never authorizes a target
identity and never invents visual facts.  It only binds selector phrases to
observable prompt terms or structured reverse observations.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Sequence
import unicodedata


_PAREN_ESCAPE_RE = re.compile(r"\\+([()])")
_CARDINALITY_RE = re.compile(r"^(\d+)(girls?|boys?|people|persons?)$")
_SELECTOR_NOISE_RE = re.compile(
    r"(?:原来|原本|原|当前|这个|这个画面中|图中|画面中|图片中|该|目标)?"
    r"(?:的)?(?:角色|人物)(?:之一)?",
    re.IGNORECASE,
)


class SubjectSelectionError(ValueError):
    """A source subject could not be selected uniquely."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.details = dict(details or {})
        super().__init__(message)


@dataclass(frozen=True)
class SubjectSelection:
    subject_count: int
    multi_subject: bool
    selector_text: str
    selector_atoms: tuple[str, ...] = ()
    matched_term_ids: tuple[int, ...] = ()
    matched_terms: tuple[str, ...] = ()
    protected_term_ids: tuple[int, ...] = ()
    protected_terms: tuple[str, ...] = ()
    basis: str = "single_subject"
    direction_used: bool = False


@dataclass(frozen=True)
class ObservedSubject:
    name: str = ""
    source_work: str = ""
    gender: str = ""
    appearance_tags: tuple[str, ...] = ()
    outfit_tags: tuple[str, ...] = ()
    action_tags: tuple[str, ...] = ()
    position: str = ""
    confidence: float = 0.0

    @property
    def observable_terms(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                term
                for term in (
                    *self.appearance_tags,
                    *self.outfit_tags,
                    *self.action_tags,
                    self.position,
                )
                if str(term or "").strip()
            )
        )


_FEATURE_GROUPS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "yellow_hair",
        ("黄色头发", "黄色长发", "黄发", "金发", "yellow hair", "blonde hair"),
        ("yellow hair", "blonde hair"),
    ),
    ("red_hair", ("红色头发", "红发", "red hair"), ("red hair",)),
    ("black_hair", ("黑色头发", "黑发", "black hair"), ("black hair",)),
    ("white_hair", ("白色头发", "白发", "white hair"), ("white hair",)),
    ("silver_hair", ("银色头发", "银发", "silver hair"), ("silver hair",)),
    ("blue_hair", ("蓝色头发", "蓝发", "blue hair"), ("blue hair",)),
    ("green_hair", ("绿色头发", "绿发", "green hair"), ("green hair",)),
    ("purple_hair", ("紫色头发", "紫发", "purple hair"), ("purple hair",)),
    ("pink_hair", ("粉色头发", "粉发", "pink hair"), ("pink hair",)),
    ("brown_hair", ("棕色头发", "棕发", "brown hair"), ("brown hair",)),
    (
        "grey_hair",
        ("灰色头发", "灰发", "gray hair", "grey hair"),
        ("gray hair", "grey hair"),
    ),
    ("orange_hair", ("橙色头发", "橙发", "orange hair"), ("orange hair",)),
    ("long_hair", ("长头发", "长发", "long hair"), ("long hair",)),
    ("short_hair", ("短头发", "短发", "short hair"), ("short hair",)),
    ("twintails", ("双马尾", "双尾", "twintails", "twin tails"), ("twintails", "twin tails")),
    ("ponytail", ("马尾", "ponytail"), ("ponytail",)),
    ("braid", ("辫子", "编发", "braid", "braided hair"), ("braid", "braided hair")),
    ("school_uniform", ("校服", "school uniform"), ("school uniform",)),
    ("maid", ("女仆装", "女仆", "maid outfit", "maid"), ("maid", "maid outfit")),
    ("white_dress", ("白色连衣裙", "白裙", "white dress"), ("white dress",)),
    ("black_dress", ("黑色连衣裙", "黑裙", "black dress"), ("black dress",)),
    ("sitting", ("坐着", "坐下", "sitting"), ("sitting",)),
    ("standing", ("站着", "站立", "standing"), ("standing",)),
    ("kneeling", ("跪着", "跪姿", "kneeling"), ("kneeling",)),
)

_HAIR_COLOR_LABELS = frozenset(
    label for label, _phrases, _tags in _FEATURE_GROUPS if label.endswith("_hair")
    and label not in {"long_hair", "short_hair"}
)

_DIRECTION_GROUPS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("left", ("左边", "左侧", "左面的", "left side", "on the left"), ("left", "left side", "on the left")),
    ("right", ("右边", "右侧", "右面的", "right side", "on the right"), ("right", "right side", "on the right")),
    ("front", ("前面", "前排", "foreground"), ("foreground", "front")),
    ("back", ("后面", "后排", "background person"), ("background person", "back")),
)


def _text(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).casefold().strip()


def _key(value: Any) -> str:
    text = _PAREN_ESCAPE_RE.sub(r"\1", _text(value))
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", text)


def _clean_selector(value: Any) -> str:
    text = _text(value)
    text = _SELECTOR_NOISE_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip(" ，,。；;:\"'")


def _subject_counts(tags: Sequence[str]) -> tuple[int, dict[str, int], bool]:
    counts = {"girl": 0, "boy": 0, "person": 0}
    explicit_multi = False
    for tag in tags:
        key = _key(tag)
        match = _CARDINALITY_RE.fullmatch(key)
        if match:
            amount = int(match.group(1))
            kind = match.group(2)
            if kind.startswith("girl"):
                counts["girl"] += amount
            elif kind.startswith("boy"):
                counts["boy"] += amount
            else:
                counts["person"] += amount
            explicit_multi = explicit_multi or amount > 1
            continue
        if key == "1girl":
            counts["girl"] += 1
        elif key == "1boy":
            counts["boy"] += 1
        elif key in {"group", "crowd", "multiplepeople", "multiplepersons"}:
            explicit_multi = True
    subject_count = counts["girl"] + counts["boy"] + counts["person"]
    if subject_count == 0:
        subject_count = 2 if explicit_multi else 1
    return subject_count, counts, explicit_multi


def _selector_gender(selector: str) -> str:
    if re.search(r"(?:女孩|女生|少女|女性|女角色|\bgirl\b|\bwoman\b)", selector):
        return "girl"
    if re.search(r"(?:男孩|男生|少年|男性|男角色|\bboy\b|\bman\b)", selector):
        return "boy"
    return ""


def _group_matches(
    selector: str,
    tags: Sequence[str],
) -> tuple[list[tuple[str, tuple[int, ...]]], bool]:
    compact = _key(selector)
    tag_keys = tuple(_key(tag) for tag in tags)
    groups: list[tuple[str, tuple[int, ...]]] = []
    direction_used = False
    for label, phrases, alternatives in (*_FEATURE_GROUPS, *_DIRECTION_GROUPS):
        if not any(_key(phrase) in compact for phrase in phrases):
            continue
        alternative_keys = {_key(item) for item in alternatives}
        ids = tuple(
            index for index, tag_key in enumerate(tag_keys) if tag_key in alternative_keys
        )
        if ids:
            groups.append((label, ids))
            direction_used = direction_used or any(
                label == direction_label
                for direction_label, _phrases, _alternatives in _DIRECTION_GROUPS
            )

    known_ids = {index for _label, ids in groups for index in ids}
    for index, tag in enumerate(tags):
        if index in known_ids:
            continue
        tag_key = tag_keys[index]
        if not tag_key or tag_key in {
            "1girl",
            "1boy",
            "2girls",
            "2boys",
            "solo",
            "multiplepeople",
        }:
            continue
        if len(tag_key) >= 4 and tag_key in compact:
            groups.append((f"exact:{tag_key}", (index,)))
    return groups, direction_used


def analyze_prompt_subject_selection(
    tags: Sequence[str],
    selector_text: str,
) -> SubjectSelection:
    """Bind a natural-language selector to one source subject conservatively."""

    subject_count, counts, explicit_multi = _subject_counts(tags)
    multi_subject = bool(explicit_multi or subject_count > 1)
    selector = _clean_selector(selector_text)
    if not multi_subject:
        return SubjectSelection(
            subject_count=1,
            multi_subject=False,
            selector_text=selector,
        )
    if not selector:
        raise SubjectSelectionError(
            "多人换角必须用自然语言说明要替换的角色特征",
            code="source_selector_required",
            details={"subject_count": subject_count},
        )

    groups, direction_used = _group_matches(selector, tags)
    gender = _selector_gender(selector)
    gender_unique = bool(gender and counts.get(gender, 0) == 1)
    matched_ids = tuple(dict.fromkeys(index for _label, ids in groups for index in ids))
    matched_terms = tuple(tags[index] for index in matched_ids)
    labels = tuple(dict.fromkeys(label for label, _ids in groups))

    hair_color_labels = {label for label in labels if label in _HAIR_COLOR_LABELS}
    prompt_hair_colors = {
        label
        for label, _phrases, alternatives in _FEATURE_GROUPS
        if label in _HAIR_COLOR_LABELS
        and any(_key(tag) in {_key(item) for item in alternatives} for tag in tags)
    }
    color_partition_unique = bool(
        hair_color_labels
        and len(prompt_hair_colors) >= subject_count
        and len(hair_color_labels) == 1
    )
    exact_identity = any(label.startswith("exact:") for label in labels)
    direction_labels = {item[0] for item in _DIRECTION_GROUPS}
    combined_features = len(
        tuple(label for label in labels if label not in direction_labels)
    ) >= 2

    if gender_unique:
        basis = "unique_gender"
    elif exact_identity:
        basis = "exact_prompt_identity"
    elif color_partition_unique:
        basis = "unique_hair_color"
    elif combined_features:
        basis = "combined_visual_features"
    elif direction_used and matched_ids:
        basis = "natural_direction_fallback"
    else:
        raise SubjectSelectionError(
            "来源角色描述无法在多人画面中唯一确认；请补充服装、动作或最后使用自然语言方向",
            code="source_selector_ambiguous",
            details={
                "subject_count": subject_count,
                "matched_terms": list(matched_terms[:12]),
                "selector_atoms": list(labels[:12]),
            },
        )

    selected_hair_labels = hair_color_labels
    protected_ids: list[int] = []
    for label, _phrases, alternatives in _FEATURE_GROUPS:
        if label not in _HAIR_COLOR_LABELS or label in selected_hair_labels:
            continue
        alternative_keys = {_key(item) for item in alternatives}
        protected_ids.extend(
            index for index, tag in enumerate(tags) if _key(tag) in alternative_keys
        )
    protected_ids = list(dict.fromkeys(protected_ids))
    return SubjectSelection(
        subject_count=subject_count,
        multi_subject=True,
        selector_text=selector,
        selector_atoms=labels,
        matched_term_ids=matched_ids,
        matched_terms=matched_terms,
        protected_term_ids=tuple(protected_ids),
        protected_terms=tuple(tags[index] for index in protected_ids),
        basis=basis,
        direction_used=direction_used,
    )


def select_observed_subject(
    subjects: Sequence[ObservedSubject],
    selector_text: str,
) -> tuple[int, SubjectSelection]:
    """Select one structured reverse subject using only observable fields."""

    selector = _clean_selector(selector_text)
    if not subjects:
        raise SubjectSelectionError(
            "反推结果没有可用于换角的角色槽位",
            code="source_subject_missing",
        )
    if len(subjects) == 1:
        subject = subjects[0]
        return 0, SubjectSelection(
            subject_count=1,
            multi_subject=False,
            selector_text=selector,
            matched_terms=subject.observable_terms,
        )
    if not selector:
        raise SubjectSelectionError(
            "多人图片换角必须描述要替换的角色",
            code="source_selector_required",
            details={"subject_count": len(subjects)},
        )

    selector_key = _key(selector)
    selector_gender = _selector_gender(selector)
    gender_matches = tuple(
        index
        for index, subject in enumerate(subjects)
        if selector_gender and _key(subject.gender) in {_key(selector_gender), f"1{_key(selector_gender)}"}
    )
    scored: list[tuple[int, int, tuple[str, ...], bool]] = []
    for index, subject in enumerate(subjects):
        identity_terms = (
            subject.name,
            subject.source_work,
        )
        identity_matches = tuple(
            term for term in identity_terms if _key(term) and _key(term) in selector_key
        )
        visual_groups, direction_used = _group_matches(
            selector,
            subject.observable_terms,
        )
        visual_ids = tuple(
            dict.fromkeys(term_id for _label, ids in visual_groups for term_id in ids)
        )
        visual_matches = tuple(subject.observable_terms[term_id] for term_id in visual_ids)
        non_direction_matches = tuple(
            subject.observable_terms[term_id]
            for label, ids in visual_groups
            if label not in {item[0] for item in _DIRECTION_GROUPS}
            for term_id in ids
        )
        position_match = bool(direction_used)
        matched = tuple(dict.fromkeys((*identity_matches, *visual_matches)))
        score = len(identity_matches) * 100 + len(non_direction_matches) * 20
        if len(gender_matches) == 1 and gender_matches[0] == index:
            score += 80
            matched = tuple(dict.fromkeys((*matched, subject.gender)))
        if position_match:
            score += 5
        scored.append((score, index, matched, position_match))
    scored.sort(reverse=True)
    best_score = scored[0][0]
    winners = [item for item in scored if item[0] == best_score and best_score > 0]
    if len(winners) != 1:
        raise SubjectSelectionError(
            "来源角色描述命中了多个图片角色槽位；请补充服装、动作或最后使用自然语言方向",
            code="source_selector_ambiguous",
            details={"subject_count": len(subjects), "candidate_count": len(winners)},
        )
    _score, selected_index, matched, position_match = winners[0]
    protected = tuple(
        term
        for index, subject in enumerate(subjects)
        if index != selected_index
        for term in subject.observable_terms
    )
    basis = "natural_direction_fallback" if position_match else "observed_features"
    return selected_index, SubjectSelection(
        subject_count=len(subjects),
        multi_subject=True,
        selector_text=selector,
        selector_atoms=tuple(_key(term) for term in matched if _key(term)),
        matched_terms=tuple(matched),
        protected_terms=tuple(dict.fromkeys(protected)),
        basis=basis,
        direction_used=position_match,
    )


__all__ = [
    "ObservedSubject",
    "SubjectSelection",
    "SubjectSelectionError",
    "analyze_prompt_subject_selection",
    "select_observed_subject",
]
