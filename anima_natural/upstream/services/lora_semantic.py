"""Versioned semantic LoRA index with explicit provenance.

The semantic index is deliberately an overlay.  Fresh ``LoraRecord`` values
remain the source of truth for files that ComfyUI can currently load, while
this module stores searchable identity information gathered from metadata,
deterministic derivation, an LLM, or a human editor.
"""

from __future__ import annotations

import json
from hashlib import sha256
import re
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from ..core.lora import canonical_lora_name
from .lora_catalog import LoraRecord


SEMANTIC_SCHEMA_VERSION = 3

PROVENANCE_SOURCES = (
    "observed",
    "derived",
    "llm_inferred",
    "manual",
)
PROVENANCE_PRIORITY = {
    "observed": 10,
    "derived": 20,
    "llm_inferred": 30,
    "manual": 40,
}

ANALYSIS_STATUSES = (
    "metadata_ready",
    "analyzing",
    "searchable",
    "review_needed",
    "failed",
    "stale",
)
OVERLAY_STATUSES = {"searchable", "review_needed"}

SEMANTIC_CATEGORIES = (
    "character",
    "artist_style",
    "speed_sampling",
    "quality_enhancement",
    "detail_restoration",
    "composition_pose",
    "lighting_color",
    "background_environment",
    "clothing_concept",
    "mixed",
    "unclassified",
)

SEMANTIC_CATEGORY_GROUPS = {
    "identity": ("character",),
    "style": ("artist_style",),
    "functional": (
        "speed_sampling",
        "quality_enhancement",
        "detail_restoration",
        "composition_pose",
        "lighting_color",
        "background_environment",
        "clothing_concept",
    ),
    "mixed": ("mixed",),
    "review": ("unclassified",),
}

SEMANTIC_FIELDS = (
    "category",
    "character_names",
    "source_works",
    "activation_terms",
    "artist_style_names",
    "aliases",
)


class LoraSemanticError(RuntimeError):
    """The semantic index could not be parsed or safely applied."""


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _canonical_key(value: Any) -> str:
    return canonical_lora_name(_clean_text(value)).casefold()


def _clean_sha256(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or "")).casefold()
    return text if re.fullmatch(r"[0-9a-f]{8,128}", text) else ""


def semantic_identity_key(name: str, sha256: str = "") -> str:
    """Use a content hash when available and a canonical name otherwise."""
    digest = _clean_sha256(sha256)
    if digest:
        return f"sha256:{digest}"
    canonical = _canonical_key(name)
    if not canonical:
        raise LoraSemanticError("LoRA semantic identity requires a name or SHA-256")
    return f"name:{canonical}"


def semantic_source_fingerprint(record: LoraRecord) -> str:
    """Fingerprint only semantic catalog fields that invalidate an analysis."""

    frozen = str(getattr(record, "source_fingerprint", "") or "").strip().casefold()
    if re.fullmatch(r"[0-9a-f]{64}", frozen):
        return frozen

    def stable_strings(values: Iterable[Any]) -> list[str]:
        unique: dict[str, str] = {}
        for value in values:
            text = _clean_text(value)
            if text:
                unique.setdefault(text.casefold(), text)
        return sorted(unique.values(), key=str.casefold)

    payload = {
        "name": canonical_lora_name(getattr(record, "name", "")),
        "model_name": _clean_text(getattr(record, "model_name", "")),
        "description": _clean_text(getattr(record, "description", "")),
        "base_model": _clean_text(getattr(record, "base_model", "")),
        "trigger_words": stable_strings(getattr(record, "trigger_words", ())),
        "tags": stable_strings(getattr(record, "tags", ())),
        "category": _clean_text(getattr(record, "category", "")),
        "aliases": stable_strings(getattr(record, "aliases", ())),
        "character_name": _clean_text(getattr(record, "character_name", "")),
        "source_work": _clean_text(getattr(record, "source_work", "")),
        "sha256": _clean_text(getattr(record, "sha256", "")).casefold(),
        "from_civitai": bool(getattr(record, "from_civitai", False)),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def semantic_catalog_fingerprint(records: Sequence[LoraRecord]) -> str:
    payload = sorted(
        (
            _canonical_key(getattr(record, "name", "")),
            semantic_source_fingerprint(record),
        )
        for record in records
    )
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _split_legacy_text(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = re.split(r"\s*(?:/|；|;|\|)\s*", _clean_text(value))
    return _dedupe_text(values)


def _dedupe_text(values: Iterable[Any]) -> tuple[str, ...]:
    unique: dict[str, str] = {}
    for value in values:
        text = _clean_text(value)
        if text:
            unique.setdefault(text.casefold(), text)
    return tuple(unique.values())


@dataclass(frozen=True)
class SemanticFact:
    """One semantic value together with its origin and audit evidence."""

    value: str
    source: str
    evidence: tuple[str, ...] = ()
    confidence: float = 1.0

    def __post_init__(self) -> None:
        value = _clean_text(self.value)
        if not value:
            raise LoraSemanticError("Semantic facts cannot be empty")
        if self.source not in PROVENANCE_SOURCES:
            raise LoraSemanticError(f"Unsupported semantic source: {self.source}")
        try:
            confidence = float(self.confidence)
        except (TypeError, ValueError) as exc:
            raise LoraSemanticError("Semantic confidence must be numeric") from exc
        if not 0.0 <= confidence <= 1.0:
            raise LoraSemanticError("Semantic confidence must be between 0 and 1")
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "evidence", _dedupe_text(self.evidence))
        object.__setattr__(self, "confidence", confidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "source": self.source,
            "evidence": list(self.evidence),
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SemanticFact":
        evidence = payload.get("evidence", ())
        if isinstance(evidence, str):
            evidence = (evidence,)
        if not isinstance(evidence, (list, tuple, set)):
            evidence = ()
        return cls(
            value=str(payload.get("value") or ""),
            source=str(payload.get("source") or ""),
            evidence=tuple(str(item) for item in evidence),
            confidence=payload.get("confidence", 1.0),
        )


def _facts_from_values(
    values: Any,
    source: str,
    *,
    evidence: Iterable[Any] = (),
    confidence: float = 1.0,
) -> tuple[SemanticFact, ...]:
    if isinstance(values, str):
        raw_values: Iterable[Any] = (values,)
    elif isinstance(values, (list, tuple, set)):
        raw_values = values
    else:
        raw_values = ()
    return tuple(
        SemanticFact(value, source, tuple(str(item) for item in evidence), confidence)
        for value in _dedupe_text(raw_values)
    )


@dataclass(frozen=True)
class LoraIdentityBinding:
    """One reviewed Character/Copyright ownership pair for a LoRA file."""

    character_canonical: str
    copyright_canonical: str = ""
    activation_terms: tuple[str, ...] = ()
    source: str = "manual"
    verified_revision: str = ""
    verified_at: str = ""

    def __post_init__(self) -> None:
        character = _clean_text(self.character_canonical)
        work = _clean_text(self.copyright_canonical)
        if not character:
            raise LoraSemanticError("LoRA identity binding requires a Character canonical")
        if self.source not in {"manual", "observed"}:
            raise LoraSemanticError("LoRA identity binding source must be manual or observed")
        object.__setattr__(self, "character_canonical", character)
        object.__setattr__(self, "copyright_canonical", work)
        object.__setattr__(
            self,
            "activation_terms",
            tuple(dict.fromkeys(_clean_text(item) for item in self.activation_terms if _clean_text(item)))[:24],
        )
        object.__setattr__(self, "verified_revision", _clean_text(self.verified_revision))
        object.__setattr__(self, "verified_at", _clean_text(self.verified_at))

    def to_dict(self) -> dict[str, Any]:
        return {
            "character_canonical": self.character_canonical,
            "copyright_canonical": self.copyright_canonical,
            "activation_terms": list(self.activation_terms),
            "source": self.source,
            "verified_revision": self.verified_revision,
            "verified_at": self.verified_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LoraIdentityBinding":
        activation_terms = payload.get("activation_terms", ())
        if not isinstance(activation_terms, (list, tuple)):
            raise LoraSemanticError("LoRA identity activation_terms must be an array")
        return cls(
            character_canonical=str(payload.get("character_canonical") or ""),
            copyright_canonical=str(payload.get("copyright_canonical") or ""),
            activation_terms=tuple(str(item) for item in activation_terms),
            source=str(payload.get("source") or "manual"),
            verified_revision=str(payload.get("verified_revision") or ""),
            verified_at=str(payload.get("verified_at") or ""),
        )


@dataclass(frozen=True)
class SemanticEntry:
    """Semantic information for one loadable LoRA identity."""

    identity_key: str
    canonical_name: str
    sha256: str = ""
    analysis_status: str = "metadata_ready"
    category: tuple[SemanticFact, ...] = ()
    character_names: tuple[SemanticFact, ...] = ()
    source_works: tuple[SemanticFact, ...] = ()
    activation_terms: tuple[SemanticFact, ...] = ()
    artist_style_names: tuple[SemanticFact, ...] = ()
    aliases: tuple[SemanticFact, ...] = ()
    identity_bindings: tuple[LoraIdentityBinding, ...] = ()
    analysis_summary: str = ""
    analysis_confidence: float = 0.0
    source_fingerprint: str = ""
    updated_at: str = ""
    error: str = ""
    present: bool = True

    def __post_init__(self) -> None:
        canonical = canonical_lora_name(self.canonical_name)
        sha256 = _clean_sha256(self.sha256)
        expected_key = semantic_identity_key(canonical, sha256)
        if self.identity_key != expected_key:
            raise LoraSemanticError(
                f"Semantic identity mismatch: {self.identity_key} != {expected_key}"
            )
        if self.analysis_status not in ANALYSIS_STATUSES:
            raise LoraSemanticError(
                f"Unsupported semantic analysis status: {self.analysis_status}"
            )
        for fact in self.category:
            if fact.value not in SEMANTIC_CATEGORIES:
                raise LoraSemanticError(f"Unsupported LoRA category: {fact.value}")
        object.__setattr__(self, "canonical_name", canonical)
        object.__setattr__(self, "sha256", sha256)
        object.__setattr__(
            self, "source_fingerprint", _clean_text(self.source_fingerprint)
        )
        object.__setattr__(self, "updated_at", _clean_text(self.updated_at))
        object.__setattr__(self, "error", _clean_text(self.error))
        object.__setattr__(
            self,
            "analysis_summary",
            _clean_text(self.analysis_summary)[:2000],
        )
        try:
            confidence = float(self.analysis_confidence)
        except (TypeError, ValueError) as exc:
            raise LoraSemanticError("Analysis confidence must be numeric") from exc
        if not 0.0 <= confidence <= 1.0:
            raise LoraSemanticError("Analysis confidence must be between 0 and 1")
        object.__setattr__(self, "analysis_confidence", confidence)

    def facts(self, field_name: str) -> tuple[SemanticFact, ...]:
        if field_name not in SEMANTIC_FIELDS:
            raise LoraSemanticError(f"Unsupported semantic field: {field_name}")
        return tuple(getattr(self, field_name))

    def effective_facts(self, field_name: str) -> tuple[SemanticFact, ...]:
        """Return deduplicated facts with manual values acting as overrides."""
        facts = self.facts(field_name)
        manual = tuple(fact for fact in facts if fact.source == "manual")
        candidates = manual or facts
        ordered = sorted(
            candidates,
            key=lambda fact: (
                -PROVENANCE_PRIORITY[fact.source],
                -fact.confidence,
                fact.value.casefold(),
            ),
        )
        unique: dict[str, SemanticFact] = {}
        for fact in ordered:
            unique.setdefault(fact.value.casefold(), fact)
        return tuple(unique.values())

    def effective_values(self, field_name: str) -> tuple[str, ...]:
        return tuple(fact.value for fact in self.effective_facts(field_name))

    @property
    def effective_category(self) -> str:
        facts = self.effective_facts("category")
        return facts[0].value if facts else ""

    @property
    def has_manual_facts(self) -> bool:
        return any(
            fact.source == "manual"
            for field_name in SEMANTIC_FIELDS
            for fact in self.facts(field_name)
        ) or any(binding.source == "manual" for binding in self.identity_bindings)

    @property
    def overlay_valid(self) -> bool:
        if not self.present or self.analysis_status not in OVERLAY_STATUSES:
            return False
        if self.analysis_status == "review_needed" and not self.has_manual_facts:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity_key": self.identity_key,
            "canonical_name": self.canonical_name,
            "sha256": self.sha256,
            "analysis_status": self.analysis_status,
            "semantic": {
                field_name: [fact.to_dict() for fact in self.facts(field_name)]
                for field_name in SEMANTIC_FIELDS
            },
            "identity_bindings": [
                binding.to_dict() for binding in self.identity_bindings
            ],
            "analysis_summary": self.analysis_summary,
            "analysis_confidence": self.analysis_confidence,
            "source_fingerprint": self.source_fingerprint,
            "updated_at": self.updated_at,
            "error": self.error,
            "present": self.present,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SemanticEntry":
        semantic = payload.get("semantic", {})
        if not isinstance(semantic, Mapping):
            raise LoraSemanticError("Semantic entry semantic field must be an object")

        parsed_fields: dict[str, tuple[SemanticFact, ...]] = {}
        for field_name in SEMANTIC_FIELDS:
            raw_facts = semantic.get(field_name, [])
            if not isinstance(raw_facts, list):
                raise LoraSemanticError(f"Semantic field {field_name} must be a list")
            parsed_fields[field_name] = tuple(
                SemanticFact.from_dict(item)
                for item in raw_facts
                if isinstance(item, Mapping)
            )
        raw_bindings = payload.get("identity_bindings", [])
        if not isinstance(raw_bindings, list):
            raise LoraSemanticError("LoRA identity_bindings must be an array")
        return cls(
            identity_key=str(payload.get("identity_key") or ""),
            canonical_name=str(payload.get("canonical_name") or ""),
            sha256=str(payload.get("sha256") or ""),
            analysis_status=str(payload.get("analysis_status") or "metadata_ready"),
            analysis_summary=str(payload.get("analysis_summary") or ""),
            analysis_confidence=payload.get("analysis_confidence", 0.0),
            source_fingerprint=str(payload.get("source_fingerprint") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            error=str(payload.get("error") or ""),
            present=bool(payload.get("present", True)),
            identity_bindings=tuple(
                LoraIdentityBinding.from_dict(item)
                for item in raw_bindings
                if isinstance(item, Mapping)
            ),
            **parsed_fields,
        )


@dataclass(frozen=True)
class SemanticCandidate:
    """One scored candidate returned by a semantic search."""

    name: str
    score: int
    matched_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticSearchResult:
    """Ranked candidates that never hide an unsafe ambiguous match."""

    query: str
    candidates: tuple[SemanticCandidate, ...]
    selected_name: str = ""
    ambiguous: bool = False


@dataclass
class LoraSemanticIndex:
    """In-memory representation of the versioned LoRA semantic overlay."""

    entries: dict[str, SemanticEntry] = field(default_factory=dict)
    schema_version: int = SEMANTIC_SCHEMA_VERSION
    updated_at: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != SEMANTIC_SCHEMA_VERSION:
            raise LoraSemanticError(
                f"Unsupported semantic schema version: {self.schema_version}"
            )

    @classmethod
    def empty(cls) -> "LoraSemanticIndex":
        return cls()

    @classmethod
    def load(cls, path: Path | str) -> "LoraSemanticIndex":
        index_path = Path(path)
        if not index_path.is_file():
            return cls.empty()
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LoraSemanticError(
                f"Unable to read LoRA semantic index: {exc}"
            ) from exc
        return cls.from_payload(payload)

    @classmethod
    def from_payload(cls, payload: Any) -> "LoraSemanticIndex":
        if not isinstance(payload, Mapping):
            raise LoraSemanticError("LoRA semantic index must be a JSON object")
        version = payload.get("schema_version")
        if version in {2, SEMANTIC_SCHEMA_VERSION}:
            raw_entries = payload.get("entries", {})
            if not isinstance(raw_entries, Mapping):
                raise LoraSemanticError("LoRA semantic entries must be an object")
            entries: dict[str, SemanticEntry] = {}
            for raw_entry in raw_entries.values():
                if not isinstance(raw_entry, Mapping):
                    continue
                entry = SemanticEntry.from_dict(raw_entry)
                entries[entry.identity_key] = entry
            return cls(
                entries=entries,
                updated_at=str(payload.get("updated_at") or ""),
            )
        if version in {None, 1} and isinstance(payload.get("entries"), Mapping):
            return cls._from_legacy_archive(payload)
        raise LoraSemanticError(f"Unsupported LoRA archive schema version: {version}")

    @classmethod
    def _from_legacy_archive(cls, payload: Mapping[str, Any]) -> "LoraSemanticIndex":
        """Read the v1 ``lora_archive.json`` without mutating the old file."""
        entries: dict[str, SemanticEntry] = {}
        for legacy_key, raw_entry in payload.get("entries", {}).items():
            if not isinstance(raw_entry, Mapping):
                continue
            source = raw_entry.get("source", {})
            if not isinstance(source, Mapping):
                source = {}
            name = str(raw_entry.get("name") or source.get("name") or legacy_key)
            canonical = canonical_lora_name(name)
            if not canonical:
                continue
            sha256 = _clean_sha256(source.get("sha256"))
            identity_key = semantic_identity_key(canonical, sha256)

            facts: dict[str, list[SemanticFact]] = {
                field_name: [] for field_name in SEMANTIC_FIELDS
            }
            observed_category = _clean_text(source.get("existing_category"))
            if observed_category in SEMANTIC_CATEGORIES:
                facts["category"].extend(
                    _facts_from_values(observed_category, "observed")
                )
            facts["character_names"].extend(
                _facts_from_values(
                    _split_legacy_text(source.get("existing_character_name")),
                    "observed",
                )
            )
            facts["source_works"].extend(
                _facts_from_values(
                    _split_legacy_text(source.get("existing_source_work")),
                    "observed",
                )
            )
            facts["aliases"].extend(
                _facts_from_values(source.get("existing_aliases", ()), "observed")
            )

            classification = raw_entry.get("classification", {})
            if isinstance(classification, Mapping):
                evidence = classification.get("evidence", ())
                confidence_name = str(classification.get("confidence") or "medium")
                confidence = {"high": 0.95, "medium": 0.7, "low": 0.35}.get(
                    confidence_name,
                    0.7,
                )
                cls._append_legacy_semantics(
                    facts,
                    classification,
                    "llm_inferred",
                    evidence=evidence if isinstance(evidence, (list, tuple)) else (),
                    confidence=confidence,
                )
            manual = raw_entry.get("manual_override", {})
            if isinstance(manual, Mapping):
                cls._append_legacy_semantics(facts, manual, "manual")

            category_values = tuple(fact.value for fact in facts["category"])
            has_classification = isinstance(classification, Mapping) and bool(
                classification
            )
            if not raw_entry.get("present", True):
                status = "stale"
            elif has_classification and (
                "unclassified" in category_values or confidence_name == "low"
            ):
                status = "review_needed"
            elif has_classification:
                status = "searchable"
            else:
                status = "metadata_ready"
            entry = SemanticEntry(
                identity_key=identity_key,
                canonical_name=canonical,
                sha256=sha256,
                analysis_status=status,
                analysis_summary=(
                    _clean_text(classification.get("summary"))
                    if isinstance(classification, Mapping)
                    else ""
                ),
                analysis_confidence=(
                    confidence
                    if isinstance(classification, Mapping) and classification
                    else 0.0
                ),
                source_fingerprint=str(
                    raw_entry.get("catalog_source_fingerprint") or ""
                ),
                updated_at=str(
                    raw_entry.get("manual_updated_at")
                    or raw_entry.get("classified_at")
                    or payload.get("updated_at")
                    or ""
                ),
                present=bool(raw_entry.get("present", True)),
                **{key: tuple(value) for key, value in facts.items()},
            )
            entries[entry.identity_key] = entry
        return cls(entries=entries, updated_at=str(payload.get("updated_at") or ""))

    @staticmethod
    def _append_legacy_semantics(
        target: dict[str, list[SemanticFact]],
        source_payload: Mapping[str, Any],
        provenance: str,
        *,
        evidence: Iterable[Any] = (),
        confidence: float = 1.0,
    ) -> None:
        mapping = {
            "category": "category",
            "character_names": "character_names",
            "source_works": "source_works",
            "activation_terms": "activation_terms",
            "artist_style_names": "artist_style_names",
            "aliases": "aliases",
        }
        for old_name, field_name in mapping.items():
            value = source_payload.get(old_name)
            if old_name == "category" and value not in SEMANTIC_CATEGORIES:
                continue
            target[field_name].extend(
                _facts_from_values(
                    value,
                    provenance,
                    evidence=evidence,
                    confidence=confidence,
                )
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SEMANTIC_SCHEMA_VERSION,
            "updated_at": self.updated_at,
            "entries": {
                key: entry.to_dict() for key, entry in sorted(self.entries.items())
            },
        }

    def save(self, path: Path | str) -> None:
        """Atomically persist the versioned semantic index."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        temporary = target.with_name(f".{target.name}.tmp")
        try:
            temporary.write_text(
                json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            temporary.replace(target)
        except OSError as exc:
            raise LoraSemanticError(
                f"Unable to save LoRA semantic index: {exc}"
            ) from exc
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass

    def upsert(self, entry: SemanticEntry) -> None:
        self.entries[entry.identity_key] = entry

    def sync_presence(self, records: Sequence[LoraRecord]) -> None:
        """Mark semantic rows present only when matched by the fresh catalog."""
        present_keys = {
            entry.identity_key
            for record in records
            if (entry := self.entry_for(record)) is not None
        }
        self.entries = {
            key: replace(entry, present=key in present_keys)
            for key, entry in self.entries.items()
        }

    def entry_for(self, record: LoraRecord) -> Optional[SemanticEntry]:
        digest = _clean_sha256(getattr(record, "sha256", ""))
        if digest:
            hashed = self.entries.get(f"sha256:{digest}")
            if hashed is not None:
                return hashed
            # A fresh content hash must never fall back to a name-only archive.
            # The file at this path may have been replaced since it was indexed.
            return None
        canonical = _canonical_key(getattr(record, "name", ""))
        direct = self.entries.get(f"name:{canonical}")
        if direct is not None:
            return direct
        matches = [
            entry
            for entry in self.entries.values()
            if _canonical_key(entry.canonical_name) == canonical
        ]
        return matches[0] if len(matches) == 1 else None

    def apply_overlay(self, record: LoraRecord) -> LoraRecord:
        """Apply only a current, identity-matched semantic overlay."""
        entry = self.entry_for(record)
        if entry is None or not entry.overlay_valid:
            return record
        current_fingerprint = semantic_source_fingerprint(record)
        if (
            entry.source_fingerprint
            and entry.source_fingerprint.casefold() != current_fingerprint.casefold()
        ):
            return record
        category = entry.effective_category
        aliases = _dedupe_text(
            (
                *record.aliases,
                *entry.effective_values("aliases"),
                *entry.effective_values("character_names"),
                *entry.effective_values("source_works"),
                *entry.effective_values("artist_style_names"),
            )
        )
        character_names = entry.effective_values("character_names")
        works = entry.effective_values("source_works")
        return replace(
            record,
            category=(
                category
                if category in SEMANTIC_CATEGORIES and category != "unclassified"
                else record.category
            ),
            aliases=aliases,
            character_name=" / ".join(character_names) or record.character_name,
            source_work="；".join(works) or record.source_work,
        )

    def apply_overlays(
        self,
        records: Sequence[LoraRecord],
    ) -> tuple[LoraRecord, ...]:
        return tuple(self.apply_overlay(record) for record in records)

    def search(
        self,
        records: Sequence[LoraRecord],
        query: str,
        *,
        limit: int = 10,
    ) -> SemanticSearchResult:
        normalized_query = _normalize_search(query)
        compact_query = _compact_search(query)
        if not compact_query:
            return SemanticSearchResult(query=_clean_text(query), candidates=())

        candidates: list[SemanticCandidate] = []
        for base_record in records:
            record = self.apply_overlay(base_record)
            terms = self._search_terms(record)
            best = 0
            matched: list[str] = []
            for value, weight in terms:
                score = _term_score(value, normalized_query, compact_query, weight)
                if score > best:
                    best = score
                    matched = [value]
                elif score and score == best:
                    matched.append(value)
            if best:
                candidates.append(
                    SemanticCandidate(
                        name=record.name,
                        score=best,
                        matched_terms=_dedupe_text(matched),
                    )
                )
        candidates.sort(key=lambda item: (-item.score, item.name.casefold()))
        bounded = tuple(candidates[: max(1, min(100, int(limit)))])
        selected_name = ""
        ambiguous = False
        if bounded and bounded[0].score >= 85:
            if len(bounded) == 1 or bounded[0].score - bounded[1].score >= 8:
                selected_name = bounded[0].name
            else:
                ambiguous = True
        return SemanticSearchResult(
            query=_clean_text(query),
            candidates=bounded,
            selected_name=selected_name,
            ambiguous=ambiguous,
        )

    @staticmethod
    def _search_terms(record: LoraRecord) -> tuple[tuple[str, int], ...]:
        canonical = canonical_lora_name(record.name)
        basename = canonical.rsplit("/", 1)[-1]
        weighted = [
            (canonical, 120),
            (basename, 110),
            (record.character_name, 105),
            *((alias, 100) for alias in record.aliases),
            (record.source_work, 92),
            (record.model_name, 88),
            *((trigger, 86) for trigger in record.trigger_words),
            *((tag, 70) for tag in record.tags),
            (record.description, 50),
        ]
        return tuple(
            (value, weight) for value, weight in weighted if _clean_text(value)
        )


def _normalize_search(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _clean_text(value)).casefold()
    text = re.sub(r"[_\-./\\:：()（）\[\]【】]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _compact_search(value: Any) -> str:
    return "".join(
        character for character in _normalize_search(value) if character.isalnum()
    )


def _term_score(
    value: str,
    normalized_query: str,
    compact_query: str,
    weight: int,
) -> int:
    normalized = _normalize_search(value)
    compact = _compact_search(value)
    if not compact:
        return 0
    if normalized_query == normalized or compact_query == compact:
        return weight
    if len(compact_query) < 2:
        return 0
    if normalized.startswith(normalized_query):
        return max(1, weight - 10)
    if compact_query in compact:
        return max(1, weight - 18)
    query_tokens = tuple(token for token in normalized_query.split() if token)
    if query_tokens and all(
        _compact_search(token) in compact for token in query_tokens
    ):
        return max(1, weight - 22)
    return 0


__all__ = [
    "ANALYSIS_STATUSES",
    "LoraSemanticError",
    "LoraIdentityBinding",
    "LoraSemanticIndex",
    "PROVENANCE_SOURCES",
    "SEMANTIC_CATEGORIES",
    "SEMANTIC_CATEGORY_GROUPS",
    "SEMANTIC_SCHEMA_VERSION",
    "SemanticCandidate",
    "SemanticEntry",
    "SemanticFact",
    "SemanticSearchResult",
    "semantic_identity_key",
]
