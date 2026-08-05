from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any, Generic, Iterable, Mapping, TypeVar

from .domain import DomainValidationError, LoraSelection, StylePreset


T = TypeVar("T")


def _normal(value: str) -> str:
    value = value.strip().casefold().replace("\\", "/")
    value = re.sub(r"\s+", " ", value)
    return value


@dataclass(frozen=True, slots=True)
class AssetCandidate(Generic[T]):
    id: str
    name: str
    aliases: tuple[str, ...] = ()
    value: T | None = None
    score: float = 1.0
    matched_by: str = "exact"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "aliases": list(self.aliases),
            "score": self.score,
            "matched_by": self.matched_by,
        }


@dataclass(frozen=True, slots=True)
class AssetMatch(Generic[T]):
    query: str
    kind: str
    status: str
    candidates: tuple[AssetCandidate[T], ...] = ()
    reason: str = ""

    STATUSES = frozenset({"matched", "needs_confirmation", "not_found"})

    def __post_init__(self) -> None:
        if self.status not in self.STATUSES:
            raise DomainValidationError(f"unsupported match status: {self.status}")
        if self.status == "matched" and len(self.candidates) != 1:
            raise DomainValidationError("matched result must contain exactly one candidate")
        if self.status == "needs_confirmation" and not self.candidates:
            raise DomainValidationError("confirmation result requires candidates")

    @property
    def matched(self) -> bool:
        return self.status == "matched"

    @property
    def needs_confirmation(self) -> bool:
        return self.status == "needs_confirmation"

    @property
    def selected(self) -> AssetCandidate[T] | None:
        return self.candidates[0] if self.matched else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "kind": self.kind,
            "status": self.status,
            "needs_confirmation": self.needs_confirmation,
            "candidates": [item.to_dict() for item in self.candidates],
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class AssetRecord(Generic[T]):
    id: str
    name: str
    aliases: tuple[str, ...] = ()
    value: T | None = None


class ExactAliasResolver(Generic[T]):
    """Resolve exact identities automatically and surface fuzzy candidates only."""

    def __init__(self, kind: str, records: Iterable[AssetRecord[T]]) -> None:
        self.kind = kind
        self.records = tuple(records)

    def normalize_query(self, query: str) -> str:
        return _normal(query)

    def resolve(self, query: str, *, fuzzy_limit: int = 5) -> AssetMatch[T]:
        raw_query = str(query or "").strip()
        normalized = self.normalize_query(raw_query)
        if not normalized:
            return AssetMatch(raw_query, self.kind, "not_found", reason="empty_query")
        exact: list[AssetCandidate[T]] = []
        for record in self.records:
            canonical = self.normalize_query(record.name)
            aliases = tuple(self.normalize_query(alias) for alias in record.aliases)
            if normalized == canonical:
                exact.append(self._candidate(record, 1.0, "exact"))
            elif normalized in aliases:
                exact.append(self._candidate(record, 1.0, "alias"))
        if len(exact) == 1:
            return AssetMatch(raw_query, self.kind, "matched", tuple(exact))
        if len(exact) > 1:
            return AssetMatch(
                raw_query,
                self.kind,
                "needs_confirmation",
                tuple(exact[:fuzzy_limit]),
                "ambiguous_exact_or_alias",
            )
        fuzzy: list[AssetCandidate[T]] = []
        for record in self.records:
            terms = (record.name, *record.aliases)
            score = max(
                difflib.SequenceMatcher(None, normalized, self.normalize_query(term)).ratio()
                for term in terms
                if self.normalize_query(term)
            )
            substring = any(
                normalized in self.normalize_query(term) or self.normalize_query(term) in normalized
                for term in terms
                if self.normalize_query(term)
            )
            if score >= 0.62 or substring:
                fuzzy.append(self._candidate(record, score, "fuzzy"))
        fuzzy.sort(key=lambda item: (-item.score, item.name.casefold(), item.id))
        if fuzzy:
            return AssetMatch(
                raw_query,
                self.kind,
                "needs_confirmation",
                tuple(fuzzy[: max(1, fuzzy_limit)]),
                "fuzzy_matches_are_never_selected_automatically",
            )
        return AssetMatch(raw_query, self.kind, "not_found", reason="no_candidate")

    @staticmethod
    def _candidate(record: AssetRecord[T], score: float, matched_by: str) -> AssetCandidate[T]:
        return AssetCandidate(
            id=record.id,
            name=record.name,
            aliases=record.aliases,
            value=record.value,
            score=round(score, 6),
            matched_by=matched_by,
        )


def _record(value: AssetRecord[T] | Mapping[str, Any], *, value_key: str = "") -> AssetRecord[T]:
    if isinstance(value, AssetRecord):
        return value
    item = dict(value)
    name = str(item.get("name") or item.get("filename") or "").strip()
    identifier = str(item.get("id") or item.get("filename") or name).strip()
    aliases = item.get("aliases") or ()
    if isinstance(aliases, str):
        aliases = (aliases,)
    payload = item.get(value_key) if value_key else value
    return AssetRecord(identifier, name, tuple(str(alias) for alias in aliases), payload)


class ArtistResolver(ExactAliasResolver[str]):
    def __init__(self, records: Iterable[AssetRecord[str] | Mapping[str, Any] | str]) -> None:
        normalized: list[AssetRecord[str]] = []
        for value in records:
            if isinstance(value, str):
                name = value.removeprefix("@").strip()
                normalized.append(AssetRecord(name, name, (), f"@{name}"))
            else:
                record = _record(value)
                name = record.name.removeprefix("@").strip()
                aliases = tuple(alias.removeprefix("@").strip() for alias in record.aliases)
                normalized.append(AssetRecord(record.id, name, aliases, f"@{name}"))
        super().__init__("artist", normalized)

    def normalize_query(self, query: str) -> str:
        value = _normal(query)
        if value.startswith("@"):
            value = value[1:].strip()
        if value.startswith("by "):
            value = value[3:].strip()
        return value


class LoraResolver(ExactAliasResolver[LoraSelection]):
    def __init__(self, records: Iterable[AssetRecord[LoraSelection] | Mapping[str, Any] | str]) -> None:
        normalized: list[AssetRecord[LoraSelection]] = []
        for index, value in enumerate(records):
            if isinstance(value, str):
                selection = LoraSelection(value, order=index)
                normalized.append(AssetRecord(value, value, (), selection))
            elif isinstance(value, AssetRecord):
                normalized.append(value)
            else:
                item = dict(value)
                selection = LoraSelection.from_mapping(item, order=index)
                aliases = item.get("aliases") or item.get("style_tags") or ()
                if isinstance(aliases, str):
                    aliases = (aliases,)
                normalized.append(
                    AssetRecord(
                        str(item.get("id") or selection.filename),
                        selection.filename,
                        tuple(str(alias) for alias in aliases),
                        selection,
                    )
                )
        super().__init__("lora", normalized)

    def normalize_query(self, query: str) -> str:
        value = _normal(query)
        value = re.sub(r"\.(safetensors|ckpt|pt)$", "", value)
        return value


class PresetResolver(ExactAliasResolver[StylePreset]):
    def __init__(self, presets: Iterable[StylePreset | Mapping[str, Any]]) -> None:
        records: list[AssetRecord[StylePreset]] = []
        for value in presets:
            preset = value if isinstance(value, StylePreset) else StylePreset.from_mapping(value)
            records.append(AssetRecord(preset.id, preset.name, preset.aliases, preset))
        super().__init__("preset", records)
