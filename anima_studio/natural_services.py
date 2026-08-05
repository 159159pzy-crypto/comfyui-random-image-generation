from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
import uuid
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .domain import LoraSelection
from .natural_runtime import LoraIdentityExpectation


class DanbooruIndexError(RuntimeError):
    pass


class ReversePromptError(RuntimeError):
    def __init__(self, message: str, *, code: str = "reverse_prompt_error") -> None:
        self.code = code
        super().__init__(message)


def _normal(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return re.sub(r"[\s-]+", "_", text)


@dataclass(frozen=True, slots=True)
class DanbooruLookup:
    query: str
    canonical_tag: str = ""
    category: str = ""
    count: int = 0
    verified: bool = False
    matched_by: str = ""


@dataclass(frozen=True, slots=True)
class DanbooruSearchResult:
    tag: str
    category: str
    count: int
    matched_by: str = "prefix"


class DanbooruTagIndex:
    """Read-only exact/unique-alias access to the local Danbooru SQLite index."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        if not self.path.is_file():
            raise DanbooruIndexError("Danbooru index is not installed")
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def status(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"ready": False, "path": str(self.path), "tag_count": 0}
        try:
            with closing(self._connect()) as connection:
                count = int(connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0])
                metadata = {
                    str(row[0]): str(row[1])
                    for row in connection.execute("SELECT key, value FROM metadata")
                }
        except (sqlite3.Error, OSError) as exc:
            return {"ready": False, "path": str(self.path), "tag_count": 0, "error": str(exc)}
        return {"ready": count > 0, "path": str(self.path), "tag_count": count, **metadata}

    def lookup(self, query: str) -> DanbooruLookup:
        normalized = _normal(query)
        if not normalized:
            return DanbooruLookup(str(query or ""))
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT tag, category, count FROM tags WHERE normalized_tag = ?",
                    (normalized,),
                ).fetchone()
                if row is not None:
                    return DanbooruLookup(
                        str(query), str(row[0]), str(row[1]), int(row[2]), True, "canonical_exact"
                    )
                rows = connection.execute(
                    """
                    SELECT t.tag, t.category, t.count
                    FROM aliases a JOIN tags t ON t.id = a.tag_id
                    WHERE a.normalized_alias = ?
                    ORDER BY t.count DESC, t.tag
                    LIMIT 2
                    """,
                    (normalized,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise DanbooruIndexError(str(exc)) from exc
        if len(rows) == 1:
            row = rows[0]
            return DanbooruLookup(str(query), str(row[0]), str(row[1]), int(row[2]), True, "alias_exact")
        return DanbooruLookup(str(query))

    def lookup_many(self, queries: Sequence[str]) -> tuple[DanbooruLookup, ...]:
        return tuple(self.lookup(query) for query in queries)

    def search(self, query: str, *, category: str = "", limit: int = 30) -> tuple[DanbooruSearchResult, ...]:
        normalized = _normal(query)
        if not normalized:
            return ()
        clauses = ["normalized_tag LIKE ?"]
        values: list[Any] = [f"{normalized}%"]
        if category:
            clauses.append("category = ?")
            values.append(str(category).casefold())
        values.append(max(1, min(100, int(limit))))
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    f"SELECT tag, category, count FROM tags WHERE {' AND '.join(clauses)} "
                    "ORDER BY count DESC, tag LIMIT ?",
                    values,
                ).fetchall()
        except sqlite3.Error as exc:
            raise DanbooruIndexError(str(exc)) from exc
        return tuple(DanbooruSearchResult(str(row[0]), str(row[1]), int(row[2])) for row in rows)


@dataclass(frozen=True, slots=True)
class CharacterIdentityResolution:
    canonical_tag: str = ""
    verified: bool = False
    ambiguous: bool = False
    candidates: tuple[str, ...] = ()
    match_variant: str = ""
    match_type: str = ""
    candidate_count: int = 0
    query_count: int = 0


def resolve_character_identity(
    index: DanbooruTagIndex,
    *,
    target_query: str,
    canonical_tag: str,
    identity_candidates: Sequence[str] = (),
    work_hints: Sequence[str] = (),
) -> CharacterIdentityResolution:
    del target_query
    queries = tuple(dict.fromkeys((canonical_tag, *identity_candidates)))
    verified: dict[str, DanbooruLookup] = {}
    for query in queries:
        lookup = index.lookup(query)
        if lookup.verified and lookup.category == "character":
            verified[_normal(lookup.canonical_tag)] = lookup
    if work_hints and len(verified) > 1:
        work_keys = {_normal(item) for item in work_hints}
        narrowed = {
            key: value
            for key, value in verified.items()
            if any(work in _normal(value.canonical_tag) for work in work_keys)
        }
        if narrowed:
            verified = narrowed
    values = tuple(verified.values())
    if len(values) != 1:
        return CharacterIdentityResolution(
            ambiguous=len(values) > 1,
            candidates=tuple(item.canonical_tag for item in values),
            candidate_count=len(values),
            query_count=len(queries),
        )
    match = values[0]
    return CharacterIdentityResolution(
        canonical_tag=match.canonical_tag,
        verified=True,
        candidates=(match.canonical_tag,),
        match_variant=match.matched_by,
        match_type="canonical" if match.matched_by == "canonical_exact" else "alias",
        candidate_count=1,
        query_count=len(queries),
    )


@dataclass(frozen=True, slots=True)
class LoraRecord:
    name: str
    trigger_words: tuple[str, ...] = ()
    description: str = ""
    folder: str = ""
    preview_url: str = ""
    category: str = "unknown"
    aliases: tuple[str, ...] = ()
    character_name: str = ""
    source: str = ""
    sha256: str = ""
    source_fingerprint: str = ""
    source_work: str = ""


class LoraSemanticIndex:
    def __init__(self, path: str | Path, document: Mapping[str, Any] | None = None) -> None:
        self.path = Path(path)
        self.document = dict(document or {})
        self.present: tuple[str, ...] = ()

    @classmethod
    def load(cls, path: str | Path) -> LoraSemanticIndex:
        source = Path(path)
        try:
            value = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            value = {}
        return cls(source, value if isinstance(value, Mapping) else {})

    def sync_presence(self, records: Sequence[LoraRecord]) -> None:
        self.present = tuple(record.name for record in records)


@dataclass(frozen=True, slots=True)
class PromptLayers:
    source: str
    base_prompt: str
    hard_tags: tuple[str, ...] = ()
    anchors: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class PromptDiagnostics:
    diagnostic_id: str
    source: str
    provider_id: str
    pipeline: str
    duplicate_count: int = 0
    invalid_count: int = 0


@dataclass(frozen=True, slots=True)
class ComposedPrompt:
    positive_prompt: str
    negative_prompt: str
    layers: PromptLayers
    diagnostics: PromptDiagnostics


class PromptDiagnosticsStore:
    def __init__(self, capacity: int = 100) -> None:
        self.capacity = max(1, int(capacity))
        self._items: OrderedDict[str, PromptDiagnostics] = OrderedDict()

    def add(self, diagnostics: PromptDiagnostics) -> str:
        self._items[diagnostics.diagnostic_id] = diagnostics
        while len(self._items) > self.capacity:
            self._items.popitem(last=False)
        return diagnostics.diagnostic_id

    def list(self) -> tuple[PromptDiagnostics, ...]:
        return tuple(reversed(self._items.values()))


def _prompt_terms(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,\r\n]+", str(value or "")) if item.strip()]


class PromptComposer:
    def __init__(
        self,
        *,
        adaptive_negative_mode: str = "conservative",
        diagnostics_store: PromptDiagnosticsStore | None = None,
        tag_index: DanbooruTagIndex | None = None,
        validation_mode: str = "report",
        include_content: bool = True,
    ) -> None:
        self.adaptive_negative_mode = adaptive_negative_mode
        self.diagnostics_store = diagnostics_store
        self.tag_index = tag_index
        self.validation_mode = validation_mode
        self.include_content = include_content

    def compose(
        self,
        positive_prompt: str,
        negative_prompt: str = "",
        *,
        hard_tags: Sequence[str] = (),
        anchors: Sequence[tuple[str, str]] = (),
        source: str = "",
        provider_id: str = "",
        pipeline: str = "",
    ) -> ComposedPrompt:
        terms = [*map(str, hard_tags), *_prompt_terms(positive_prompt)]
        result: list[str] = []
        seen: set[str] = set()
        duplicate_count = 0
        for term in terms:
            key = _normal(term)
            if not key:
                continue
            if key in seen:
                duplicate_count += 1
                continue
            seen.add(key)
            result.append(term.strip())
        diagnostics = PromptDiagnostics(
            diagnostic_id=f"diag_{uuid.uuid4().hex[:12]}",
            source=source,
            provider_id=provider_id,
            pipeline=pipeline,
            duplicate_count=duplicate_count,
        )
        if self.diagnostics_store is not None:
            self.diagnostics_store.add(diagnostics)
        return ComposedPrompt(
            positive_prompt=", ".join(result),
            negative_prompt=", ".join(_prompt_terms(negative_prompt)),
            layers=PromptLayers(source, positive_prompt, tuple(map(str, hard_tags)), tuple(anchors)),
            diagnostics=diagnostics,
        )


class SubjectSelectionError(ValueError):
    def __init__(self, message: str, *, code: str, details: Mapping[str, Any] | None = None) -> None:
        self.code = code
        self.details = dict(details or {})
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class SubjectSelection:
    subject_count: int
    multi_subject: bool
    selector_text: str
    matched_terms: tuple[str, ...] = ()
    protected_terms: tuple[str, ...] = ()
    basis: str = "single_subject"
    direction_used: bool = False


@dataclass(frozen=True, slots=True)
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
                value
                for value in (
                    *self.appearance_tags,
                    *self.outfit_tags,
                    *self.action_tags,
                    self.position,
                )
                if str(value).strip()
            )
        )


def _selector_key(value: str) -> str:
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", unicodedata.normalize("NFKC", value).casefold())


def select_observed_subject(
    subjects: Sequence[ObservedSubject], selector_text: str
) -> tuple[int, SubjectSelection]:
    selector = str(selector_text or "").strip()
    if not subjects:
        raise SubjectSelectionError("reverse result contains no subject", code="source_subject_missing")
    if len(subjects) == 1:
        return 0, SubjectSelection(1, False, selector, subjects[0].observable_terms)
    if not selector:
        raise SubjectSelectionError(
            "multiple subjects require an explicit source selector",
            code="source_selector_required",
            details={"subject_count": len(subjects)},
        )
    selector_key = _selector_key(selector)
    scored: list[tuple[int, int, tuple[str, ...], bool]] = []
    for index, subject in enumerate(subjects):
        identity = tuple(
            value
            for value in (subject.name, subject.source_work)
            if _selector_key(value) and _selector_key(value) in selector_key
        )
        visual = tuple(
            value
            for value in subject.observable_terms
            if _selector_key(value) and _selector_key(value) in selector_key
        )
        direction = bool(subject.position and _selector_key(subject.position) in selector_key)
        score = len(identity) * 100 + len(visual) * 20 + (5 if direction else 0)
        scored.append((score, index, tuple(dict.fromkeys((*identity, *visual))), direction))
    best = max(score for score, _index, _matched, _direction in scored)
    winners = [item for item in scored if item[0] == best and best > 0]
    if len(winners) != 1:
        raise SubjectSelectionError(
            "source selector does not identify exactly one subject",
            code="source_selector_ambiguous",
            details={"subject_count": len(subjects), "candidate_count": len(winners)},
        )
    _score, selected, matched, direction = winners[0]
    protected = tuple(
        term for index, subject in enumerate(subjects) if index != selected for term in subject.observable_terms
    )
    return selected, SubjectSelection(
        len(subjects),
        True,
        selector,
        matched,
        tuple(dict.fromkeys(protected)),
        "natural_direction_fallback" if direction else "observed_features",
        direction,
    )


class CharacterSwapError(RuntimeError):
    def __init__(
        self, message: str, *, code: str = "character_swap_error", details: Mapping[str, Any] | None = None
    ) -> None:
        self.code = code
        self.details = dict(details or {})
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class CharacterSwapRequest:
    source_query: str
    target_query: str
    tags: str = ""
    mode: str = "keep-outfit"
    target_lora_strength: float = 0.65
    preview: bool = False
    use_target_lora: bool = True
    require_target_lora: bool = False
    pipeline: str = ""
    seed: int | None = None
    steps: int | None = None
    cfg: float | None = None
    denoise: float | None = None
    semantic_identity_confidence: float = 0.0
    semantic_identity_index_verified: bool = False
    semantic_identity_anchor_source: str = ""
    semantic_identity_match_variant: str = ""
    semantic_identity_match_type: str = ""
    semantic_identity_candidate_count: int = 0
    semantic_identity_query_count: int = 0
    semantic_identity_canonical_tag: str = ""
    require_target_appearance_slots: bool = False
    source_subject_count: int = 1
    source_selector_terms: tuple[str, ...] = ()
    protected_subject_terms: tuple[str, ...] = ()
    source_selector_basis: str = ""
    source_selector_direction_used: bool = False


@dataclass(frozen=True, slots=True)
class CharacterSwapClassification:
    source_identity_ids: tuple[int, ...]
    outfit_ids: tuple[int, ...]
    pose_action_ids: tuple[int, ...]
    composition_ids: tuple[int, ...]
    scene_lighting_ids: tuple[int, ...]
    style_quality_ids: tuple[int, ...]
    uncertain_ids: tuple[int, ...]
    target_identity_trigger_id: int | None
    target_appearance_trigger_ids: tuple[int, ...]
    target_default_outfit_trigger_ids: tuple[int, ...]
    subject_count: int
    confidence: float


@dataclass(frozen=True, slots=True)
class CharacterSwapPreparation:
    request: CharacterSwapRequest
    tags: tuple[str, ...]
    negative_prompt: str
    target_record: LoraRecord | None
    target_metadata_record: LoraRecord | None
    source_record: LoraRecord | None
    preserved_loras: tuple[LoraSelection, ...]
    target_trigger_words: tuple[str, ...]
    deterministic_target_trigger: str
    source_tag_categories: tuple[str, ...] = ()
    source_tag_verified: tuple[bool, ...] = ()
    source_tag_canonicals: tuple[str, ...] = ()
    source_subject_count: int = 1
    multi_subject: bool = False
    source_selector_term_ids: tuple[int, ...] = ()
    source_selector_terms: tuple[str, ...] = ()
    protected_subject_terms: tuple[str, ...] = ()
    source_selector_basis: str = "single_subject"
    source_selector_direction_used: bool = False


@dataclass(frozen=True, slots=True)
class CharacterSwapPlan:
    prompt: str
    negative_prompt: str
    loras: tuple[LoraSelection, ...]
    expectations: tuple[LoraIdentityExpectation, ...]
    target_record: LoraRecord | None
    source_record: LoraRecord | None
    target_identity_trigger: str
    removed_terms: tuple[str, ...]
    kept_terms: tuple[str, ...]
    added_terms: tuple[str, ...]
    suppressed_terms: tuple[str, ...]
    suppress_default_style: bool
    target_activation_terms: tuple[str, ...] = ()
    preserved_character_lora_names: tuple[str, ...] = ()
    classification_confidence: float = 0.0
    feature_swap_categories: tuple[str, ...] = ()
    target_appearance_terms: tuple[str, ...] = ()
    target_appearance_source: str = ""
    target_feature_categories: tuple[str, ...] = ()
    missing_target_feature_categories: tuple[str, ...] = ()
    source_subject_count: int = 1
    multi_subject: bool = False
    source_selector_terms: tuple[str, ...] = ()
    protected_subject_terms: tuple[str, ...] = ()
    source_selector_basis: str = "single_subject"
    source_selector_direction_used: bool = False

    def preview_text(self) -> str:
        return f"replace with {self.target_identity_trigger}; removed: {', '.join(self.removed_terms)}"


_APPEARANCE_MARKERS = (
    "hair",
    "eyes",
    "eye",
    "horn",
    "tail",
    "wing",
    "halo",
    "ears",
    "skin",
    "freckles",
    "scar",
    "tattoo",
)
_OUTFIT_MARKERS = (
    "shirt",
    "jacket",
    "dress",
    "skirt",
    "coat",
    "pants",
    "tights",
    "uniform",
    "shoe",
    "boot",
    "glove",
    "hat",
)
_POSE_MARKERS = ("standing", "sitting", "kneeling", "looking", "walking", "running")


def _record_matches(record: LoraRecord, query: str) -> bool:
    key = _normal(query).removesuffix(".safetensors")
    terms = (record.name, record.character_name, *record.aliases, *record.trigger_words)
    return any(_normal(term).removesuffix(".safetensors") == key for term in terms if term)


class CharacterSwapPlanner:
    def __init__(self, semantic_index: LoraSemanticIndex) -> None:
        self.semantic_index = semantic_index

    @staticmethod
    def attach_source_tag_evidence(
        preparation: CharacterSwapPreparation, lookups: Sequence[Any]
    ) -> CharacterSwapPreparation:
        if len(lookups) != len(preparation.tags):
            raise CharacterSwapError("source tag evidence length mismatch", code="source_tag_evidence_mismatch")
        return replace(
            preparation,
            source_tag_categories=tuple(
                str(getattr(item, "category", "") or "").casefold()
                if getattr(item, "verified", False)
                else ""
                for item in lookups
            ),
            source_tag_verified=tuple(bool(getattr(item, "verified", False)) for item in lookups),
            source_tag_canonicals=tuple(
                str(getattr(item, "canonical_tag", "") or "")
                if getattr(item, "verified", False)
                else ""
                for item in lookups
            ),
        )

    def prepare(
        self,
        request: CharacterSwapRequest,
        *,
        positive_prompt: str,
        negative_prompt: str,
        records: Sequence[LoraRecord],
        fallback_target_tags: Sequence[str] = (),
        replace_source_style: bool = False,
    ) -> CharacterSwapPreparation:
        del replace_source_style
        if request.mode not in {"keep-outfit", "target-outfit"}:
            raise CharacterSwapError("unsupported swap mode", code="unsupported_swap_mode")
        candidates = tuple(record for record in records if _record_matches(record, request.target_query))
        if len(candidates) > 1:
            raise CharacterSwapError("target LoRA is ambiguous", code="ambiguous_character")
        if request.require_target_lora and not candidates:
            raise CharacterSwapError("required target LoRA is missing", code="required_target_lora_missing")
        target_metadata = candidates[0] if candidates else None
        target = target_metadata if request.use_target_lora else None
        trigger = (
            (target_metadata.trigger_words[0] if target_metadata and target_metadata.trigger_words else "")
            or (str(fallback_target_tags[0]) if fallback_target_tags else "")
        )
        if not trigger:
            raise CharacterSwapError("target identity is not verified", code="target_identity_unverified")
        tags = tuple(_prompt_terms(re.sub(r"<lora:[^>]+>", "", positive_prompt)))
        selected_ids = tuple(
            index
            for index, tag in enumerate(tags)
            if any(_normal(term) == _normal(tag) for term in request.source_selector_terms)
        )
        return CharacterSwapPreparation(
            request=request,
            tags=tags,
            negative_prompt=negative_prompt,
            target_record=target,
            target_metadata_record=target_metadata,
            source_record=None,
            preserved_loras=(),
            target_trigger_words=(trigger,),
            deterministic_target_trigger=trigger,
            source_subject_count=request.source_subject_count,
            multi_subject=request.source_subject_count > 1,
            source_selector_term_ids=selected_ids,
            source_selector_terms=request.source_selector_terms,
            protected_subject_terms=request.protected_subject_terms,
            source_selector_basis=request.source_selector_basis or "single_subject",
            source_selector_direction_used=request.source_selector_direction_used,
        )

    @staticmethod
    def deterministic_classification(
        preparation: CharacterSwapPreparation,
    ) -> CharacterSwapClassification | None:
        source_ids: list[int] = []
        outfit: list[int] = []
        pose: list[int] = []
        other: list[int] = []
        for index, tag in enumerate(preparation.tags):
            value = tag.casefold()
            if preparation.multi_subject:
                if index in preparation.source_selector_term_ids:
                    source_ids.append(index)
                else:
                    other.append(index)
            elif any(marker in value for marker in _APPEARANCE_MARKERS):
                source_ids.append(index)
            elif any(marker in value for marker in _OUTFIT_MARKERS):
                outfit.append(index)
            elif any(marker in value for marker in _POSE_MARKERS):
                pose.append(index)
            else:
                other.append(index)
        return CharacterSwapClassification(
            tuple(source_ids),
            tuple(outfit),
            tuple(pose),
            (),
            (),
            tuple(other),
            (),
            0,
            (),
            (),
            preparation.source_subject_count,
            1.0,
        )

    @staticmethod
    def classification_prompts(preparation: CharacterSwapPreparation) -> tuple[str, str]:
        return (
            "Classify indexed prompt tags into source identity, outfit, pose, composition, scene, and style. JSON only.",
            json.dumps({"tags": list(enumerate(preparation.tags))}, ensure_ascii=False),
        )

    @staticmethod
    def parse_classification(
        text: str,
        *,
        tag_count: int,
        target_trigger_count: int,
        deterministic_target_identity_id: int | None = None,
    ) -> CharacterSwapClassification:
        del target_trigger_count
        try:
            value = json.loads(text)
        except ValueError as exc:
            raise CharacterSwapError("invalid classification JSON", code="classification_invalid") from exc
        if not isinstance(value, Mapping):
            raise CharacterSwapError("invalid classification object", code="classification_invalid")

        def ids(name: str) -> tuple[int, ...]:
            result = tuple(int(item) for item in value.get(name) or ())
            if any(item < 0 or item >= tag_count for item in result):
                raise CharacterSwapError("classification index out of range", code="classification_invalid")
            return result

        return CharacterSwapClassification(
            ids("source_identity_ids"),
            ids("outfit_ids"),
            ids("pose_action_ids"),
            ids("composition_ids"),
            ids("scene_lighting_ids"),
            ids("style_quality_ids"),
            ids("uncertain_ids"),
            deterministic_target_identity_id,
            (),
            (),
            int(value.get("subject_count") or 1),
            float(value.get("confidence") or 0),
        )

    @staticmethod
    def finalize(
        preparation: CharacterSwapPreparation, classification: CharacterSwapClassification
    ) -> CharacterSwapPlan:
        if classification.subject_count != preparation.source_subject_count:
            raise CharacterSwapError("subject count mismatch", code="subject_count_mismatch")
        removed_ids = set(classification.source_identity_ids)
        if preparation.multi_subject and not removed_ids.issubset(preparation.source_selector_term_ids):
            raise CharacterSwapError("source selector scope violation", code="source_selector_scope_violation")
        removed = tuple(tag for index, tag in enumerate(preparation.tags) if index in removed_ids)
        kept = tuple(tag for index, tag in enumerate(preparation.tags) if index not in removed_ids)
        target = preparation.deterministic_target_trigger
        loras = (
            (LoraSelection(preparation.target_record.name, strength=preparation.request.target_lora_strength, role="character"),)
            if preparation.target_record is not None
            else ()
        )
        expectations = (
            (LoraIdentityExpectation(preparation.target_record.name, preparation.target_record.sha256),)
            if preparation.target_record is not None
            else ()
        )
        return CharacterSwapPlan(
            prompt=", ".join((*kept, target)),
            negative_prompt=preparation.negative_prompt,
            loras=loras,
            expectations=expectations,
            target_record=preparation.target_record,
            source_record=preparation.source_record,
            target_identity_trigger=target,
            removed_terms=removed,
            kept_terms=kept,
            added_terms=(target,),
            suppressed_terms=removed,
            suppress_default_style=True,
            classification_confidence=classification.confidence,
            source_subject_count=preparation.source_subject_count,
            multi_subject=preparation.multi_subject,
            source_selector_terms=preparation.source_selector_terms,
            protected_subject_terms=preparation.protected_subject_terms,
            source_selector_basis=preparation.source_selector_basis,
            source_selector_direction_used=preparation.source_selector_direction_used,
        )


def parse_natural_character_swap(text: str) -> CharacterSwapRequest | None:
    source = str(text or "").strip()
    patterns = (
        r"(?:replace|swap|change)\s+(?:the\s+)?(?:character|person|subject)?\s*(?:with|to|into)\s+(.+?)(?:[,.;]|$)",
        r"(?:\u66ff\u6362\u6210|\u6362\u6210|\u6539\u6210)([^\uff0c\u3002,.;]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, source, flags=re.IGNORECASE)
        if match and match.group(1).strip():
            return CharacterSwapRequest("", match.group(1).strip())
    return None


def normalize_semantic_identity_payload(
    value: Mapping[str, Any],
) -> tuple[tuple[str, ...], float, tuple[str, ...]]:
    canonical = str(value.get("canonical_identity_tag") or "").strip()
    if not canonical or not re.fullmatch(r"[0-9A-Za-z_()' .-]{2,200}", canonical):
        raise CharacterSwapError("invalid canonical identity", code="semantic_identity_invalid")
    confidence = float(value.get("confidence") or 0)
    if not 0 <= confidence <= 1:
        raise CharacterSwapError("invalid semantic confidence", code="semantic_identity_invalid")
    appearance = tuple(str(item).strip() for item in value.get("appearance_tags") or () if str(item).strip())
    return (canonical,), confidence, appearance[:6]


def semantic_identity_lookup_hints(value: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    identities = tuple(
        str(item).strip() for item in value.get("identity_candidates") or () if str(item).strip()
    )[:8]
    works = tuple(str(item).strip() for item in value.get("work_hints") or () if str(item).strip())[:4]
    return identities, works


__all__ = [
    "CharacterSwapError",
    "CharacterSwapPlan",
    "CharacterSwapPlanner",
    "CharacterSwapRequest",
    "DanbooruIndexError",
    "DanbooruTagIndex",
    "LoraRecord",
    "LoraSemanticIndex",
    "ObservedSubject",
    "PromptComposer",
    "PromptDiagnosticsStore",
    "ReversePromptError",
    "SubjectSelectionError",
    "normalize_semantic_identity_payload",
    "parse_natural_character_swap",
    "resolve_character_identity",
    "select_observed_subject",
    "semantic_identity_lookup_hints",
]
