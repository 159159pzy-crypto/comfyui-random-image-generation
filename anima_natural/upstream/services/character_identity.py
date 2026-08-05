"""Deterministic local Danbooru character identity resolution.

Provider output is discovery data, never identity authority.  This module expands
only bounded prompt-safe variants, accepts only verified character exact/unique
alias lookups, and checks the work qualifier before returning a canonical tag.
Prefix/keyword results are used only to collapse variants of one proven identity;
they are exact-confirmed again before authorization.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Sequence
import unicodedata

from .danbooru_index import DanbooruTagIndex, TagCandidate, TagLookup, normalize_tag


_QUALIFIER_RE = re.compile(r"_\(([^()]*)\)")
_TRAILING_QUALIFIER_RE = re.compile(r"_\(([^()]*)\)$")
_ASCII_LOOKUP_RE = re.compile(r"[A-Za-z][A-Za-z0-9_ .()!'&+:/-]{0,79}")
_SAFE_LOOKUP_RE = re.compile(r"[a-z0-9_().!'&+:/-]{1,80}")


@dataclass(frozen=True)
class CharacterIdentityResolution:
    canonical_tag: str = ""
    verified: bool = False
    ambiguous: bool = False
    match_variant: str = "none"
    match_type: str = "none"
    query_count: int = 0
    candidate_count: int = 0
    candidates: tuple[str, ...] = ()
    conflicting_works: tuple[str, ...] = ()
    confirmed_work: str = ""


@dataclass(frozen=True)
class _LookupSpec:
    value: str
    variant: str
    expected_work: str = ""
    allow_qualifierless_canonical: bool = False


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_tag(value)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return tuple(result)


def _qualifiers(value: str) -> tuple[str, ...]:
    return tuple(
        item
        for raw in _QUALIFIER_RE.findall(normalize_tag(value))
        if (item := normalize_tag(raw))
    )


def _work_qualifier(value: str) -> str:
    qualifiers = _qualifiers(value)
    return qualifiers[-1] if qualifiers else ""


def _strip_last_qualifier(value: str) -> tuple[str, str]:
    normalized = normalize_tag(value)
    match = _TRAILING_QUALIFIER_RE.search(normalized)
    if match is None:
        return normalized, ""
    stripped = normalized[: match.start()].rstrip("_")
    return stripped, normalize_tag(match.group(1))


def _replace_last_qualifier(value: str, work: str) -> str:
    normalized = normalize_tag(value)
    safe_work = _safe_lookup_value(work)
    match = _TRAILING_QUALIFIER_RE.search(normalized)
    if match is None or not safe_work:
        return normalized
    return f"{normalized[: match.start()]}_({safe_work})"


def _identity_root(value: str) -> str:
    """Collapse costume variants while retaining the final work qualifier."""

    normalized = normalize_tag(value)
    qualifiers = _qualifiers(normalized)
    if not qualifiers:
        return normalized
    base = _QUALIFIER_RE.sub("", normalized).rstrip("_")
    return f"{base}_({qualifiers[-1]})"


def _safe_lookup_value(value: Any) -> str:
    normalized = normalize_tag(str(value or ""))
    if not normalized or not _SAFE_LOOKUP_RE.fullmatch(normalized):
        return ""
    return normalized


def _ascii_query_segments(value: str) -> tuple[str, ...]:
    segments: list[str] = []
    for raw in re.split(r"[/|、；;\n]+", unicodedata.normalize("NFKC", value)):
        # Parentheses may be the meaningful Danbooru work qualifier in an
        # explicit value such as ``viola (bang_dream!)``.  Strip only wrapper
        # punctuation here; ``normalize_tag`` will canonicalize the qualifier.
        stripped = raw.strip(" ，,:：[]{}<>\"'")
        if not stripped or not stripped.isascii() or not _ASCII_LOOKUP_RE.fullmatch(stripped):
            continue
        safe = _safe_lookup_value(stripped)
        if safe:
            segments.append(safe)
    return _dedupe(segments)


def character_identity_lookup_candidates(
    *,
    target_query: str,
    canonical_tag: str = "",
    identity_candidates: Sequence[str] = (),
    work_hints: Sequence[str] = (),
) -> tuple[str, ...]:
    """Expose the bounded exact candidates for optional Gallery verification.

    The Gallery remains a discovery/evidence source.  Callers must require an
    exact Character-category response for these already-safe candidates and
    must still reject cross-identity ambiguity.
    """

    return tuple(
        dict.fromkeys(
            spec.value
            for spec in _lookup_specs(
                target_query,
                canonical_tag,
                identity_candidates,
                work_hints,
            )
        )
    )


def _lookup_specs(
    target_query: str,
    canonical_tag: str,
    identity_candidates: Sequence[str],
    work_hints: Sequence[str],
    confirmed_works: Sequence[str] = (),
) -> tuple[_LookupSpec, ...]:
    specs: list[_LookupSpec] = []
    global_works = _dedupe((*work_hints, *confirmed_works))
    safe_global_works = tuple(
        work for raw in global_works if (work := _safe_lookup_value(raw))
    )[:4]

    confirmed_work_set = frozenset(_dedupe(confirmed_works))
    explicit_qualified_identity = bool(
        _work_qualifier(canonical_tag)
        or any(_work_qualifier(value) for value in identity_candidates[:8])
    )

    def add(
        value: str,
        variant: str,
        expected_work: str = "",
        *,
        allow_qualifierless_canonical: bool = False,
    ) -> None:
        safe = _safe_lookup_value(value)
        work = _safe_lookup_value(expected_work)
        if not safe:
            return
        specs.append(
            _LookupSpec(
                safe,
                variant,
                work,
                bool(
                    allow_qualifierless_canonical
                    and work
                    and work in confirmed_work_set
                ),
            )
        )

    canonical = _safe_lookup_value(canonical_tag)
    if canonical:
        canonical_work = _work_qualifier(canonical)
        expected_work = canonical_work or (
            safe_global_works[0] if len(safe_global_works) == 1 else ""
        )
        add(
            canonical,
            "canonical_exact",
            expected_work,
            allow_qualifierless_canonical=(
                not canonical_work and not explicit_qualified_identity
            ),
        )
        stripped, work = _strip_last_qualifier(canonical)
        if stripped and work:
            add(stripped, "alias_without_work", work)

    for candidate in identity_candidates[:8]:
        safe = _safe_lookup_value(candidate)
        if not safe:
            continue
        expected_work = _work_qualifier(safe) or (
            safe_global_works[0] if len(safe_global_works) == 1 else ""
        )
        add(
            safe,
            "provider_candidate_exact",
            expected_work,
            allow_qualifierless_canonical=(
                "_(" not in safe and not explicit_qualified_identity
            ),
        )
        stripped, stripped_work = _strip_last_qualifier(safe)
        if stripped and stripped_work:
            add(stripped, "provider_candidate_alias_without_work", stripped_work)
        elif "_(" not in safe:
            # A short alias such as ``rio`` is not exact when costume variants
            # also exist. Copyright evidence may construct bounded Character
            # candidates, but every result is still exact-checked below.
            for work in safe_global_works:
                add(
                    f"{safe}_({work})",
                    "provider_candidate_work_qualified",
                    work,
                )

    for segment in _ascii_query_segments(target_query):
        add(
            segment,
            "user_ascii_exact",
            safe_global_works[0] if len(safe_global_works) == 1 else "",
            allow_qualifierless_canonical=(
                "_(" not in segment and not explicit_qualified_identity
            ),
        )
        if "_(" not in segment:
            for work in safe_global_works:
                add(
                    f"{segment}_({work})",
                    "user_ascii_work_qualified",
                    work,
                )

    deduped: list[_LookupSpec] = []
    seen: set[tuple[str, str]] = set()
    for spec in specs:
        key = (normalize_tag(spec.value), normalize_tag(spec.expected_work))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(spec)
    return tuple(deduped[:24])


def _work_matches(canonical_tag: str, expected_work: str) -> bool:
    expected = normalize_tag(expected_work)
    if not expected:
        return True
    return _work_qualifier(canonical_tag) == expected


def _verified_hit(
    lookup: TagLookup,
    expected_work: str,
    *,
    query: str = "",
    allow_qualifierless_canonical: bool = False,
) -> bool:
    if not (
        lookup.found
        and lookup.verified
        and str(lookup.category or "").casefold() == "character"
    ):
        return False
    canonical = normalize_tag(lookup.canonical_tag or lookup.tag)
    if _work_matches(canonical, expected_work):
        return True
    if not (
        expected_work
        and allow_qualifierless_canonical
        and not _work_qualifier(canonical)
    ):
        return False
    # A Copyright-exact work hint may accompany a qualifierless Character tag,
    # but it must never turn a short alias into identity authority.  The
    # Character side therefore still has to be the exact canonical itself.
    return bool(
        normalize_tag(query) == canonical
        and str(getattr(lookup, "match_type", "") or "").casefold()
        in {"canonical", "exact"}
    )


def _lookup_many(
    index: DanbooruTagIndex,
    values: Sequence[str],
    category: str = "character",
) -> tuple[TagLookup, ...]:
    batch = getattr(index, "lookup_many", None)
    if callable(batch):
        return tuple(batch(values, category))
    lookup = getattr(index, "lookup")
    return tuple(lookup(value, category) for value in values)


def _verified_copyright_hit(lookup: TagLookup) -> bool:
    return bool(
        lookup.found
        and lookup.verified
        and str(lookup.category or "").casefold() == "copyright"
    )


def _copyright_punctuation_key(value: str) -> str:
    """Compare ASCII Copyright spellings while ignoring punctuation only."""

    return "".join(re.findall(r"[a-z0-9]+", normalize_tag(value)))


def _discover_unique_copyright(
    index: DanbooruTagIndex,
    value: str,
    initial_lookup: TagLookup | None = None,
) -> str:
    """Discover one punctuation-only Copyright variant, then exact-confirm it."""

    initial = (
        (initial_lookup,)
        if initial_lookup is not None
        else _lookup_many(index, (value,), "copyright")
    )
    if initial and str(getattr(initial[0], "match_type", "") or "") in {
        "alias_ambiguous",
        "canonical_category_mismatch",
    }:
        return ""
    target_key = _copyright_punctuation_key(value)
    tokens = re.findall(r"[a-z0-9]+", normalize_tag(value))
    search = getattr(index, "search", None)
    if not target_key or not tokens or len(tokens[0]) < 3 or not callable(search):
        return ""

    discovered: dict[str, TagCandidate] = {}
    for mode in ("prefix", "keyword"):
        for candidate in search(
            tokens[0],
            mode=mode,
            category="copyright",
            limit=50,
        ):
            canonical = _candidate_canonical(candidate)
            if (
                canonical
                and str(candidate.category or "").casefold() == "copyright"
                and _copyright_punctuation_key(canonical) == target_key
            ):
                discovered.setdefault(canonical, candidate)
        if discovered:
            break
    if len(discovered) != 1:
        return ""

    canonical = next(iter(discovered))
    exact = _lookup_many(index, (canonical,), "copyright")
    if len(exact) != 1 or not _verified_copyright_hit(exact[0]):
        return ""
    return _safe_lookup_value(exact[0].canonical_tag or exact[0].tag)


def _confirmed_copyright_work_hints(
    index: DanbooruTagIndex,
    work_hints: Sequence[str],
) -> tuple[str, ...]:
    """Return only locally exact-confirmed Copyright identities."""

    safe_hints = tuple(
        work
        for raw in _dedupe(work_hints[:4])
        if (work := _safe_lookup_value(raw))
    )
    if not safe_hints:
        return ()
    lookups = _lookup_many(index, safe_hints, "copyright")
    confirmed: list[str] = []
    for raw, lookup in zip(safe_hints, lookups):
        canonical = ""
        if _verified_copyright_hit(lookup):
            canonical = _safe_lookup_value(lookup.canonical_tag or lookup.tag)
        if not canonical:
            canonical = _discover_unique_copyright(index, raw, lookup)
        if canonical and canonical not in confirmed:
            confirmed.append(canonical)
    return tuple(confirmed)


def _normalize_provider_work_evidence(
    index: DanbooruTagIndex,
    *,
    canonical_tag: str,
    identity_candidates: Sequence[str],
    work_hints: Sequence[str],
) -> tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Normalize only Copyright exact/unique-alias evidence.

    Provider punctuation is advisory.  A work spelling is rewritten only after
    the local Copyright category exact-confirms it, and multiple different
    confirmed works are returned as a conflict instead of being guessed away.
    """

    bounded_candidates = tuple(identity_candidates[:8])
    bounded_hints = tuple(work_hints[:4])
    raw_works = _dedupe(
        (
            _work_qualifier(canonical_tag),
            *(_work_qualifier(value) for value in bounded_candidates),
            *bounded_hints,
        )
    )
    safe_works = tuple(
        work for raw in raw_works if (work := _safe_lookup_value(raw))
    )[:13]
    if not safe_works:
        return canonical_tag, bounded_candidates, bounded_hints, ()

    lookups = _lookup_many(index, safe_works, "copyright")
    resolved: dict[str, str] = {}
    for raw, lookup in zip(safe_works, lookups):
        if not _verified_copyright_hit(lookup):
            continue
        canonical = _safe_lookup_value(lookup.canonical_tag or lookup.tag)
        if canonical:
            resolved[raw] = canonical
    for raw in safe_works:
        if raw in resolved:
            continue
        matching_lookup = next(
            (
                lookup
                for candidate, lookup in zip(safe_works, lookups)
                if candidate == raw
            ),
            None,
        )
        canonical = _discover_unique_copyright(index, raw, matching_lookup)
        if canonical:
            resolved[raw] = canonical

    confirmed_works = tuple(dict.fromkeys(resolved.values()))
    def normalize_qualified(value: str) -> str:
        safe = _safe_lookup_value(value)
        if not safe:
            return str(value or "")
        raw_work = _work_qualifier(safe)
        canonical_work = resolved.get(raw_work, "")
        return _replace_last_qualifier(safe, canonical_work) if canonical_work else safe

    normalized_hints = tuple(
        dict.fromkeys(resolved.get(_safe_lookup_value(value), _safe_lookup_value(value))
                      for value in bounded_hints
                      if _safe_lookup_value(value))
    )
    return (
        normalize_qualified(canonical_tag),
        tuple(normalize_qualified(value) for value in bounded_candidates),
        normalized_hints,
        confirmed_works,
    )


def _candidate_canonical(candidate: TagCandidate) -> str:
    return normalize_tag(candidate.canonical_tag or candidate.tag)


def resolve_character_identity(
    index: DanbooruTagIndex,
    *,
    target_query: str,
    canonical_tag: str = "",
    identity_candidates: Sequence[str] = (),
    work_hints: Sequence[str] = (),
    allow_discovery: bool = True,
) -> CharacterIdentityResolution:
    """Resolve one character using local verified evidence only."""

    (
        canonical_tag,
        identity_candidates,
        work_hints,
        confirmed_works,
    ) = _normalize_provider_work_evidence(
        index,
        canonical_tag=canonical_tag,
        identity_candidates=identity_candidates,
        work_hints=work_hints,
    )
    if len(confirmed_works) > 1:
        return CharacterIdentityResolution(
            ambiguous=True,
            match_variant="copyright_exact_conflict",
            candidate_count=len(confirmed_works),
            conflicting_works=confirmed_works[:8],
        )
    confirmed_work = confirmed_works[0] if len(confirmed_works) == 1 else ""

    specs = _lookup_specs(
        target_query,
        canonical_tag,
        identity_candidates,
        work_hints,
        confirmed_works,
    )
    if not specs:
        return CharacterIdentityResolution()

    lookups = _lookup_many(index, [spec.value for spec in specs])
    exact_hits: list[tuple[_LookupSpec, TagLookup, str]] = []
    for spec, lookup in zip(specs, lookups):
        if not _verified_hit(
            lookup,
            spec.expected_work,
            query=spec.value,
            allow_qualifierless_canonical=spec.allow_qualifierless_canonical,
        ):
            continue
        canonical = normalize_tag(lookup.canonical_tag or lookup.tag)
        exact_hits.append((spec, lookup, canonical))

    unique_exact = tuple(dict.fromkeys(hit[2] for hit in exact_hits))
    if len(unique_exact) == 1:
        selected = next(hit for hit in exact_hits if hit[2] == unique_exact[0])
        return CharacterIdentityResolution(
            canonical_tag=unique_exact[0],
            verified=True,
            match_variant=selected[0].variant,
            match_type=str(getattr(selected[1], "match_type", "") or "exact"),
            query_count=len(specs),
            candidate_count=1,
            candidates=unique_exact,
            confirmed_work=confirmed_work,
        )
    if len(unique_exact) > 1:
        active_exact = tuple(
            hit for hit in exact_hits if int(getattr(hit[1], "count", 0) or 0) > 0
        )
        unique_active = tuple(dict.fromkeys(hit[2] for hit in active_exact))
        if len(unique_active) == 1:
            selected = next(hit for hit in active_exact if hit[2] == unique_active[0])
            return CharacterIdentityResolution(
                canonical_tag=unique_active[0],
                verified=True,
                match_variant="active_exact_over_zero_count_conflict",
                match_type=str(getattr(selected[1], "match_type", "") or "exact"),
                query_count=len(specs),
                candidate_count=1,
                candidates=unique_active,
                confirmed_work=confirmed_work,
            )
        return CharacterIdentityResolution(
            ambiguous=True,
            match_variant="exact_conflict",
            query_count=len(specs),
            candidate_count=len(unique_exact),
            candidates=unique_exact[:8],
        )
    blocked_exact = tuple(
        lookup
        for lookup in lookups
        if str(getattr(lookup, "match_type", "") or "")
        in {"alias_ambiguous", "canonical_category_mismatch"}
    )
    if blocked_exact:
        blocked_candidates = tuple(
            dict.fromkeys(
                canonical
                for lookup in blocked_exact
                for candidate in getattr(lookup, "candidates", ())
                if (canonical := _candidate_canonical(candidate))
            )
        )[:8]
        variants = {
            str(getattr(lookup, "match_type", "") or "")
            for lookup in blocked_exact
        }
        return CharacterIdentityResolution(
            ambiguous=True,
            match_variant=(
                "global_alias_ambiguous"
                if "alias_ambiguous" in variants
                else "canonical_category_mismatch"
            ),
            query_count=len(specs),
            candidate_count=len(blocked_candidates),
            candidates=blocked_candidates,
        )
    if not allow_discovery:
        return CharacterIdentityResolution(query_count=len(specs))

    discovered: dict[str, TagCandidate] = {}
    for spec in specs[:12]:
        # Work-qualified full tags are poor discovery seeds; their safe stripped
        # alias is already present as a separate spec.
        if "_(" in spec.value:
            continue
        for mode in ("prefix", "keyword"):
            for candidate in index.search(
                spec.value,
                mode=mode,
                category="character",
                limit=12,
            ):
                canonical = _candidate_canonical(candidate)
                if not canonical or not _work_matches(canonical, spec.expected_work):
                    continue
                discovered.setdefault(canonical, candidate)

    if not discovered:
        return CharacterIdentityResolution(query_count=len(specs))

    # Discovery results are hints.  Re-run exact on every canonical before they
    # participate in deterministic grouping.
    verified = _lookup_many(index, tuple(discovered))
    verified_tags = tuple(
        normalize_tag(item.canonical_tag or item.tag)
        for item in verified
        if _verified_hit(item, "")
    )
    roots: dict[str, list[str]] = {}
    for tag in dict.fromkeys(verified_tags):
        roots.setdefault(_identity_root(tag), []).append(tag)
    if len(roots) != 1:
        candidates = tuple(dict.fromkeys(verified_tags))[:8]
        return CharacterIdentityResolution(
            ambiguous=len(roots) > 1,
            match_variant="discovery_ambiguous" if len(roots) > 1 else "none",
            query_count=len(specs),
            candidate_count=len(candidates),
            candidates=candidates,
        )

    root, variants = next(iter(roots.items()))
    canonical = root if root in variants else min(
        variants,
        key=lambda item: (len(_qualifiers(item)), -int(discovered[item].count), item),
    )
    final_lookup = index.lookup(canonical, "character")
    if not _verified_hit(final_lookup, _work_qualifier(canonical)):
        return CharacterIdentityResolution(
            query_count=len(specs),
            candidate_count=len(verified_tags),
        )
    return CharacterIdentityResolution(
        canonical_tag=normalize_tag(final_lookup.canonical_tag or final_lookup.tag),
        verified=True,
        match_variant="local_discovery_unique_identity",
        match_type=str(getattr(final_lookup, "match_type", "") or "canonical"),
        query_count=len(specs),
        candidate_count=len(verified_tags),
        candidates=tuple(dict.fromkeys(verified_tags))[:8],
        confirmed_work=confirmed_work,
    )


def resolve_user_adjacent_character_alias(
    index: DanbooruTagIndex,
    *,
    alias: str,
    canonical_candidates: Sequence[str] = (),
    work_hints: Sequence[str] = (),
) -> CharacterIdentityResolution:
    """Resolve one user-authored adjacent ASCII alias without LoRA authority.

    The alias is trusted only as a lookup seed.  Prefix/keyword discovery may
    locate candidates, but the selected Character canonical and its base
    identity root must both exact-confirm.  This deliberately prefers the base
    identity over costume variants such as ``toki_(bunny)_(blue_archive)``.
    """

    safe_alias = _safe_lookup_value(alias)
    if not safe_alias:
        return CharacterIdentityResolution()

    confirmed_works = _confirmed_copyright_work_hints(index, work_hints)
    if len(confirmed_works) > 1:
        return CharacterIdentityResolution(
            ambiguous=True,
            match_variant="user_adjacent_alias_work_conflict",
            candidate_count=len(confirmed_works),
            conflicting_works=confirmed_works[:8],
        )

    direct_lookup = index.lookup(safe_alias, "character")
    direct_root = (
        _identity_root(direct_lookup.canonical_tag or direct_lookup.tag)
        if _verified_hit(direct_lookup, "")
        else ""
    )

    def alias_matches_root(root: str) -> bool:
        base, _work = _strip_last_qualifier(root)
        return bool(
            base == safe_alias
            or (direct_root and normalize_tag(direct_root) == normalize_tag(root))
        )

    exact_candidates = _lookup_many(
        index,
        tuple(
            dict.fromkeys(
                safe
                for value in canonical_candidates[:12]
                if (safe := _safe_lookup_value(value))
            )
        ),
        "character",
    )
    prompt_roots = tuple(
        dict.fromkeys(
            root
            for lookup in exact_candidates
            if _verified_hit(lookup, "")
            and (
                root := _identity_root(lookup.canonical_tag or lookup.tag)
            )
        )
    )
    eligible_prompt_roots = (
        tuple(
            root
            for root in prompt_roots
            if (
                not _work_qualifier(root)
                or _work_qualifier(root) in confirmed_works
            )
        )
        if confirmed_works
        else prompt_roots
    )
    exact_roots = tuple(
        root for root in eligible_prompt_roots if alias_matches_root(root)
    )

    def prompt_conflict(resolved_root: str) -> CharacterIdentityResolution | None:
        mismatched = tuple(
            root
            for root in eligible_prompt_roots
            if normalize_tag(root) != normalize_tag(resolved_root)
        )
        if not mismatched:
            return None
        candidates = tuple(
            dict.fromkeys((normalize_tag(resolved_root), *mismatched))
        )
        return CharacterIdentityResolution(
            ambiguous=True,
            match_variant="user_adjacent_alias_prompt_conflict",
            candidate_count=len(candidates),
            candidates=candidates[:8],
        )

    if len(exact_roots) > 1:
        return CharacterIdentityResolution(
            ambiguous=True,
            match_variant="user_adjacent_alias_prompt_conflict",
            candidate_count=len(exact_roots),
            candidates=exact_roots[:8],
        )
    if len(exact_roots) == 1:
        resolved = resolve_character_identity(
            index,
            target_query=safe_alias,
            canonical_tag=exact_roots[0],
            work_hints=work_hints,
            allow_discovery=False,
        )
        if resolved.verified:
            if conflict := prompt_conflict(_identity_root(resolved.canonical_tag)):
                return conflict
            return CharacterIdentityResolution(
                canonical_tag=resolved.canonical_tag,
                verified=True,
                match_variant="user_adjacent_alias_prompt_exact",
                match_type=resolved.match_type,
                query_count=resolved.query_count,
                candidate_count=max(1, len(exact_candidates)),
                candidates=exact_roots,
                confirmed_work=resolved.confirmed_work,
            )
        if resolved.ambiguous:
            return resolved

    discovered = resolve_character_identity(
        index,
        target_query=safe_alias,
        identity_candidates=(safe_alias,),
        work_hints=work_hints,
        allow_discovery=True,
    )
    if not discovered.verified or discovered.ambiguous:
        return discovered
    root = _identity_root(discovered.canonical_tag)
    if not alias_matches_root(root):
        return CharacterIdentityResolution(
            query_count=discovered.query_count,
            candidate_count=discovered.candidate_count,
            candidates=discovered.candidates,
        )
    if conflict := prompt_conflict(root):
        return conflict
    root_lookup = index.lookup(root, "character")
    if not _verified_hit(root_lookup, _work_qualifier(root)):
        return CharacterIdentityResolution(
            query_count=discovered.query_count,
            candidate_count=discovered.candidate_count,
            candidates=discovered.candidates,
        )
    return CharacterIdentityResolution(
        canonical_tag=normalize_tag(root_lookup.canonical_tag or root_lookup.tag),
        verified=True,
        match_variant="user_adjacent_alias_unique_base",
        match_type=str(getattr(root_lookup, "match_type", "") or "canonical"),
        query_count=discovered.query_count,
        candidate_count=discovered.candidate_count,
        candidates=discovered.candidates,
        confirmed_work=discovered.confirmed_work,
    )


__all__ = [
    "CharacterIdentityResolution",
    "character_identity_lookup_candidates",
    "resolve_character_identity",
    "resolve_user_adjacent_character_alias",
]
