"""Localized Danbooru alias discovery with exact canonical re-validation.

This module intentionally keeps localized names separate from the main Danbooru
alias table.  A localized name may refer to characters from several works, while
the main index requires one alias to identify one canonical tag.  Localized data
therefore discovers candidates only; the selected Character and Copyright tags
must still exact-confirm in :class:`DanbooruTagIndex`.

The optional CSV reader accepts the public ``tag,category,count,alias`` format
used by ComfyUI-Autocomplete-Plus.  The plugin does not bundle or redistribute
that external dataset.  Administrators may place their own licensed snapshot at
``plugin_data/astrbot_plugin_comfy_anima/localized_danbooru_aliases.csv``.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata
from typing import Iterable, Sequence

from .danbooru_index import DanbooruTagIndex, normalize_tag


_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]")
_CATEGORY_BY_NUMBER = {"3": "copyright", "4": "character"}
_SUPPORTED_CATEGORIES = frozenset({"character", "copyright"})
_WORK_QUALIFIER_RE = re.compile(r"_\(([^()]*)\)$")


def normalize_localized_alias(value: str) -> str:
    """Normalize user-facing names without forcing them to ASCII."""

    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    text = text.replace(r"\(", "(").replace(r"\)", ")")
    text = text.strip(" \t\r\n,，;；:：'\"“”‘’《》〈〉【】[]()（）")
    text = re.sub(r"[\s_\-·・]+", "", text)
    return text


def contains_localized_text(value: str) -> bool:
    return bool(_CJK_RE.search(unicodedata.normalize("NFKC", str(value or ""))))


def canonical_work_for_character(canonical_tag: str) -> str:
    match = _WORK_QUALIFIER_RE.search(normalize_tag(canonical_tag))
    return normalize_tag(match.group(1)) if match else ""


def split_localized_character_query(value: str) -> tuple[str, str]:
    """Split common Chinese ``work + character`` forms conservatively."""

    source = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not source:
        return "", ""
    patterns = (
        re.compile(r"^《(?P<work>[^》]{1,80})》\s*(?:的|里(?:的)?|中(?:的)?)?\s*(?P<name>.+)$"),
        re.compile(r"^(?P<name>.+?)\s*[（(]\s*(?P<work>[^()（）]{1,80})\s*[）)]$"),
    )
    for pattern in patterns:
        match = pattern.fullmatch(source)
        if match is None:
            continue
        name = match.group("name").strip(" ,，;；")
        work = match.group("work").strip(" ,，;；")
        if name and work:
            return name, work
    return source.strip(" ,，;；"), ""


@dataclass(frozen=True)
class LocalizedAliasEntry:
    alias: str
    canonical_tag: str
    category: str
    work: str = ""
    requires_work: bool = False
    locale: str = "und"
    source: str = ""
    license: str = ""
    revision: str = ""


@dataclass(frozen=True)
class LocalizedAliasCandidate:
    canonical_tag: str
    category: str
    matched_alias: str
    work: str = ""
    verified: bool = False
    match_type: str = "localized_alias_candidate"
    requires_work: bool = False
    source: str = ""
    license: str = ""
    revision: str = ""


@dataclass(frozen=True)
class LocalizedCharacterResolution:
    canonical_tag: str = ""
    verified: bool = False
    ambiguous: bool = False
    work_required: bool = False
    match_type: str = "none"
    candidate_count: int = 0
    candidates: tuple[LocalizedAliasCandidate, ...] = ()
    confirmed_work: str = ""


# These are independently curated factual corrections, not copied database rows.
# ``菲比`` is deliberately work-required because the same localized name can map
# to several unrelated Phoebe characters.
_BUILTIN_ENTRIES = (
    *(
        LocalizedAliasEntry(
            alias=alias,
            canonical_tag="hatsune_miku",
            category="character",
            work="vocaloid",
            locale=locale,
            source="builtin-curated-fact",
            license="facts-only",
            revision="2026-08-04",
        )
        for alias, locale in (
            ("初音未来", "zh-CN"),
            ("初音未來", "zh-TW"),
        )
    ),
    LocalizedAliasEntry(
        alias="菲比",
        canonical_tag="phoebe_(wuthering_waves)",
        category="character",
        work="wuthering_waves",
        requires_work=True,
        locale="zh-CN",
        source="builtin-curated-fact",
        license="facts-only",
        revision="2026-08-03",
    ),
    LocalizedAliasEntry(
        alias="菲碧",
        canonical_tag="phoebe_(wuthering_waves)",
        category="character",
        work="wuthering_waves",
        requires_work=True,
        locale="zh-CN",
        source="builtin-curated-fact",
        license="facts-only",
        revision="2026-08-03",
    ),
    *(
        LocalizedAliasEntry(
            alias=alias,
            canonical_tag="wuthering_waves",
            category="copyright",
            locale=locale,
            source="builtin-curated-fact",
            license="facts-only",
            revision="2026-08-03",
        )
        for alias, locale in (
            ("鸣潮", "zh-CN"),
            ("鳴潮", "zh-TW"),
            ("Wuthering Waves", "en"),
            ("WuWa", "en"),
        )
    ),
)


def _split_alias_cell(value: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            item.strip()
            for item in re.split(r"\s*,\s*", str(value or ""))
            if item.strip() and contains_localized_text(item)
        )
    )[:64]


def parse_autocomplete_csv(
    text: str,
    *,
    source: str = "operator-import",
    license_name: str = "operator-supplied",
    revision: str = "",
) -> tuple[LocalizedAliasEntry, ...]:
    """Parse a bounded multilingual autocomplete CSV into discovery entries."""

    reader = csv.reader(str(text or "").splitlines())
    entries: list[LocalizedAliasEntry] = []
    for row_number, row in enumerate(reader, start=1):
        if not row or all(not str(item).strip() for item in row):
            continue
        if row_number == 1 and str(row[0]).strip().casefold() in {"tag", "name"}:
            continue
        if len(row) < 4:
            continue
        canonical = normalize_tag(row[0])
        raw_category = str(row[1]).strip().casefold()
        category = _CATEGORY_BY_NUMBER.get(raw_category, raw_category)
        if not canonical or category not in _SUPPORTED_CATEGORIES:
            continue
        aliases = _split_alias_cell(row[3])
        if not aliases:
            continue
        work = canonical_work_for_character(canonical) if category == "character" else ""
        for alias in aliases:
            entries.append(
                LocalizedAliasEntry(
                    alias=alias,
                    canonical_tag=canonical,
                    category=category,
                    work=work,
                    locale="und",
                    source=source,
                    license=license_name,
                    revision=revision,
                )
            )
        if len(entries) > 500_000:
            raise ValueError("localized alias CSV exceeds 500000 entries")
    return tuple(entries)


class LocalizedCharacterAliasIndex:
    """One-to-many localized alias overlay with fail-closed exact validation."""

    def __init__(
        self,
        entries: Iterable[LocalizedAliasEntry] = (),
        *,
        csv_path: Path | None = None,
    ) -> None:
        merged = [*_BUILTIN_ENTRIES, *tuple(entries)]
        self._csv_error = ""
        self._csv_path = Path(csv_path) if csv_path is not None else None
        if self._csv_path is not None and self._csv_path.is_file():
            try:
                merged.extend(
                    parse_autocomplete_csv(
                        self._csv_path.read_text(encoding="utf-8-sig"),
                        source="operator-local-csv",
                        license_name="operator-supplied",
                        revision=str(int(self._csv_path.stat().st_mtime)),
                    )
                )
            except (OSError, UnicodeError, ValueError, csv.Error) as exc:
                self._csv_error = type(exc).__name__
        self._entries = self._deduplicate(merged)
        self._aliases: dict[tuple[str, str], tuple[LocalizedAliasEntry, ...]] = {}
        grouped: dict[tuple[str, str], list[LocalizedAliasEntry]] = {}
        for entry in self._entries:
            key = (entry.category, normalize_localized_alias(entry.alias))
            if key[1]:
                grouped.setdefault(key, []).append(entry)
        self._aliases = {key: tuple(values) for key, values in grouped.items()}

    @staticmethod
    def _deduplicate(
        entries: Sequence[LocalizedAliasEntry],
    ) -> tuple[LocalizedAliasEntry, ...]:
        result: list[LocalizedAliasEntry] = []
        seen: set[tuple[str, str, str]] = set()
        for entry in entries:
            category = str(entry.category or "").strip().casefold()
            alias = normalize_localized_alias(entry.alias)
            canonical = normalize_tag(entry.canonical_tag)
            if category not in _SUPPORTED_CATEGORIES or not alias or not canonical:
                continue
            key = (category, alias, canonical)
            if key in seen:
                continue
            seen.add(key)
            result.append(
                LocalizedAliasEntry(
                    alias=str(entry.alias).strip(),
                    canonical_tag=canonical,
                    category=category,
                    work=(
                        normalize_tag(entry.work)
                        or (
                            canonical_work_for_character(canonical)
                            if category == "character"
                            else ""
                        )
                    ),
                    requires_work=bool(entry.requires_work),
                    locale=str(entry.locale or "und")[:16],
                    source=str(entry.source or "")[:128],
                    license=str(entry.license or "")[:128],
                    revision=str(entry.revision or "")[:64],
                )
            )
        return tuple(result)

    def status(self) -> dict[str, object]:
        return {
            "ready": bool(self._entries),
            "entry_count": len(self._entries),
            "alias_count": len(self._aliases),
            "csv_loaded": bool(self._csv_path and self._csv_path.is_file() and not self._csv_error),
            "csv_error": self._csv_error,
        }

    def _exact_entries(
        self, alias: str, category: str
    ) -> tuple[LocalizedAliasEntry, ...]:
        return self._aliases.get(
            (str(category or "").casefold(), normalize_localized_alias(alias)),
            (),
        )

    @staticmethod
    def _verified_copyright(
        index: DanbooruTagIndex,
        canonical: str,
    ) -> str:
        lookup = index.lookup(canonical, "copyright")
        if not bool(getattr(lookup, "verified", False)):
            return ""
        return normalize_tag(lookup.canonical_tag or lookup.tag)

    def resolve_work(
        self,
        work: str,
        index: DanbooruTagIndex,
    ) -> tuple[str, ...]:
        source = str(work or "").strip()
        if not source:
            return ()
        candidates = [
            entry.canonical_tag
            for entry in self._exact_entries(source, "copyright")
        ]
        normalized = normalize_tag(source)
        if normalized and not contains_localized_text(source):
            candidates.append(normalized)
            camel_spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", source)
            camel_normalized = normalize_tag(camel_spaced)
            if camel_normalized:
                candidates.append(camel_normalized)
        verified: list[str] = []
        for candidate in dict.fromkeys(candidates):
            canonical = self._verified_copyright(index, candidate)
            if canonical:
                verified.append(canonical)
        return tuple(dict.fromkeys(verified))

    def resolve_character(
        self,
        name: str,
        work: str,
        index: DanbooruTagIndex,
    ) -> LocalizedCharacterResolution:
        entries = self._exact_entries(name, "character")
        if not entries:
            confirmed_works = self.resolve_work(work, index) if work else ()
            normalized_name = normalize_tag(name)
            if (
                len(confirmed_works) == 1
                and normalized_name
                and normalized_name.isascii()
                and re.search(r"[a-z]", normalized_name)
            ):
                confirmed_work = confirmed_works[0]
                direct_lookup = index.lookup(normalized_name, "character")
                if bool(getattr(direct_lookup, "verified", False)):
                    canonical = normalize_tag(
                        direct_lookup.canonical_tag or direct_lookup.tag
                    )
                    canonical_work = canonical_work_for_character(canonical)
                    exact_qualifierless = bool(
                        not canonical_work
                        and canonical == normalized_name
                        and str(
                            getattr(direct_lookup, "match_type", "") or ""
                        ).casefold()
                        in {"canonical", "exact"}
                    )
                    if canonical_work == confirmed_work or exact_qualifierless:
                        candidate = LocalizedAliasCandidate(
                            canonical_tag=canonical,
                            category="character",
                            matched_alias=str(name).strip(),
                            work=confirmed_work,
                            verified=True,
                            match_type="localized_work_ascii_character_exact",
                            source="derived-localized-work",
                        )
                        return LocalizedCharacterResolution(
                            canonical_tag=canonical,
                            verified=True,
                            match_type=candidate.match_type,
                            candidate_count=1,
                            candidates=(candidate,),
                            confirmed_work=confirmed_work,
                        )

                candidate_tag = f"{normalized_name}_({confirmed_work})"
                lookup = index.lookup(candidate_tag, "character")
                if bool(getattr(lookup, "verified", False)):
                    canonical = normalize_tag(lookup.canonical_tag or lookup.tag)
                    if canonical_work_for_character(canonical) == confirmed_work:
                        candidate = LocalizedAliasCandidate(
                            canonical_tag=canonical,
                            category="character",
                            matched_alias=str(name).strip(),
                            work=confirmed_work,
                            verified=True,
                            match_type="localized_work_ascii_character_exact",
                            source="derived-localized-work",
                        )
                        return LocalizedCharacterResolution(
                            canonical_tag=canonical,
                            verified=True,
                            match_type=candidate.match_type,
                            candidate_count=1,
                            candidates=(candidate,),
                            confirmed_work=confirmed_work,
                        )
            return LocalizedCharacterResolution()
        confirmed_works = self.resolve_work(work, index) if work else ()
        if work and not confirmed_works:
            return LocalizedCharacterResolution(
                match_type="localized_work_unverified",
                candidate_count=len(entries),
            )
        filtered = tuple(
            entry
            for entry in entries
            if not confirmed_works or entry.work in confirmed_works
        )
        if not filtered:
            return LocalizedCharacterResolution(
                match_type="localized_work_conflict",
                candidate_count=len(entries),
                confirmed_work=confirmed_works[0] if len(confirmed_works) == 1 else "",
            )
        if not work and (len(filtered) > 1 or any(item.requires_work for item in filtered)):
            candidates = tuple(
                self._candidate(entry, verified=False) for entry in filtered[:12]
            )
            return LocalizedCharacterResolution(
                ambiguous=True,
                work_required=True,
                match_type="localized_work_required",
                candidate_count=len(filtered),
                candidates=candidates,
            )

        verified_entries: list[LocalizedAliasEntry] = []
        for entry in filtered:
            character_lookup = index.lookup(entry.canonical_tag, "character")
            if not bool(getattr(character_lookup, "verified", False)):
                continue
            canonical = normalize_tag(
                character_lookup.canonical_tag or character_lookup.tag
            )
            canonical_work = canonical_work_for_character(canonical)
            effective_work = canonical_work or entry.work
            if entry.work and canonical_work and canonical_work != entry.work:
                continue
            if confirmed_works and effective_work not in confirmed_works:
                continue
            if effective_work and not self._verified_copyright(index, effective_work):
                continue
            verified_entries.append(
                LocalizedAliasEntry(
                    **{
                        **entry.__dict__,
                        "canonical_tag": canonical,
                        "work": effective_work,
                    }
                )
            )
        unique = {
            entry.canonical_tag: entry for entry in verified_entries
        }
        candidates = tuple(
            self._candidate(entry, verified=True) for entry in unique.values()
        )
        if len(unique) == 1:
            selected = next(iter(unique.values()))
            return LocalizedCharacterResolution(
                canonical_tag=selected.canonical_tag,
                verified=True,
                match_type="localized_alias_exact",
                candidate_count=1,
                candidates=candidates,
                confirmed_work=selected.work,
            )
        return LocalizedCharacterResolution(
            ambiguous=len(unique) > 1,
            match_type=(
                "localized_alias_ambiguous" if len(unique) > 1 else "localized_canonical_unverified"
            ),
            candidate_count=len(unique),
            candidates=candidates,
            confirmed_work=confirmed_works[0] if len(confirmed_works) == 1 else "",
        )

    @staticmethod
    def _candidate(
        entry: LocalizedAliasEntry,
        *,
        verified: bool,
    ) -> LocalizedAliasCandidate:
        return LocalizedAliasCandidate(
            canonical_tag=entry.canonical_tag,
            category=entry.category,
            matched_alias=entry.alias,
            work=entry.work,
            verified=verified,
            match_type=("localized_alias_exact" if verified else "localized_alias_candidate"),
            requires_work=entry.requires_work,
            source=entry.source,
            license=entry.license,
            revision=entry.revision,
        )

    def search(
        self,
        query: str,
        *,
        index: DanbooruTagIndex,
        category: str = "",
        limit: int = 8,
    ) -> tuple[LocalizedAliasCandidate, ...]:
        requested_category = str(category or "").strip().casefold()
        effective_limit = max(1, min(int(limit), 12))
        name, work = split_localized_character_query(query)
        if requested_category in {"", "character"}:
            if (
                work
                and contains_localized_text(name)
                and not self.resolve_work(work, index)
                and work.isascii()
                and re.search(r"[A-Za-z]", work)
            ):
                adjacent = index.lookup(work, "character")
                if bool(getattr(adjacent, "verified", False)):
                    canonical = normalize_tag(adjacent.canonical_tag or adjacent.tag)
                    if canonical:
                        return (
                            LocalizedAliasCandidate(
                                canonical_tag=canonical,
                                category="character",
                                matched_alias=work,
                                work=canonical_work_for_character(canonical),
                                verified=True,
                                match_type="localized_adjacent_ascii_alias_exact",
                                source="user-adjacent-alias",
                            ),
                        )
            resolution = self.resolve_character(name, work, index)
            if resolution.candidates:
                return resolution.candidates[:effective_limit]
        if requested_category in {"", "copyright"}:
            results: list[LocalizedAliasCandidate] = []
            for entry in self._exact_entries(query, "copyright"):
                canonical = self._verified_copyright(index, entry.canonical_tag)
                if not canonical:
                    continue
                results.append(
                    LocalizedAliasCandidate(
                        canonical_tag=canonical,
                        category="copyright",
                        matched_alias=entry.alias,
                        verified=True,
                        match_type="localized_alias_exact",
                        source=entry.source,
                        license=entry.license,
                        revision=entry.revision,
                    )
                )
            if results:
                return tuple(results[:effective_limit])
        return ()


__all__ = [
    "LocalizedAliasCandidate",
    "LocalizedAliasEntry",
    "LocalizedCharacterAliasIndex",
    "LocalizedCharacterResolution",
    "canonical_work_for_character",
    "contains_localized_text",
    "normalize_localized_alias",
    "parse_autocomplete_csv",
    "split_localized_character_query",
]
