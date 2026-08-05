"""Deterministic, side-effect-free prompt candidate planning.

``PromptLab`` is deliberately smaller than the drawing director.  It does not
call an LLM, write state, inspect ComfyUI or submit a workflow.  It only turns
an explicit set of base layers and caller-provided asset pools into bounded,
reproducible candidate drafts.  A selected draft can then be converted to the
strict keyword arguments accepted by :class:`services.prompt_composer.PromptComposer`.

The five first-class visual asset kinds are character, outfit, pose,
background and artist.  Camera, relation and LoRA pools are also accepted as
advanced layer pools so callers do not have to invent a second randomizer.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence

from .prompt_composer import (
    _clean_text,
    _conflict_identity,
    _dedupe_key,
    _extract_loras,
    _split_top_level_commas,
)


PROMPT_LAB_LAYERS = (
    "identity",
    "clothing",
    "pose",
    "camera",
    "background",
    "style",
    "relation",
    "lora",
)
VISUAL_ASSET_TYPES = ("character", "outfit", "pose", "background", "artist")

MIN_CANDIDATES = 1
MAX_CANDIDATES = 6
MAX_POOLS = len(PROMPT_LAB_LAYERS)
MAX_ASSETS_PER_POOL = 256
MAX_TOTAL_ASSETS = 1024
MAX_TERMS_PER_ASSET = 64
MAX_TERMS_PER_LAYER = 64
MAX_TOTAL_TERMS = 256
MAX_TERM_LENGTH = 512
MAX_RELATION_LENGTH = 2048
MAX_NEGATIVE_PROMPT_LENGTH = 8192
MAX_FINAL_PROMPT_LENGTH = 16384
MAX_SEED_TEXT_LENGTH = 256

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LORA_RE = re.compile(r"^<\s*lora\s*:[^<>\r\n]+>$", re.IGNORECASE)

_LAYER_ALIASES = {
    "identity": "identity",
    "character": "identity",
    "characters": "identity",
    "role": "identity",
    "clothing": "clothing",
    "clothes": "clothing",
    "outfit": "clothing",
    "outfits": "clothing",
    "costume": "clothing",
    "costumes": "clothing",
    "pose": "pose",
    "poses": "pose",
    "camera": "camera",
    "cameras": "camera",
    "composition": "camera",
    "view": "camera",
    "background": "background",
    "backgrounds": "background",
    "environment": "background",
    "location": "background",
    "style": "style",
    "styles": "style",
    "artist": "style",
    "artists": "style",
    "relation": "relation",
    "relations": "relation",
    "scene": "relation",
    "scene_sentence": "relation",
    "lora": "lora",
    "loras": "lora",
    "lora_tags": "lora",
}

_CANONICAL_ASSET_TYPE = {
    "identity": "character",
    "clothing": "outfit",
    "pose": "pose",
    "background": "background",
    "style": "artist",
    "camera": "camera",
    "relation": "relation",
    "lora": "lora",
}

_ANCHOR_CATEGORIES = {
    "identity": "character",
    "clothing": "clothing",
    "pose": "pose",
    "camera": "camera",
    "background": "environment",
    "style": "artist",
    "lora": "lora",
}


class PromptLabError(ValueError):
    """Raised when candidate inputs cannot produce a bounded strict draft."""


def _canonical_layer(value: Any) -> str:
    key = unicodedata.normalize("NFKC", _clean_text(value)).casefold()
    key = key.replace("-", "_").replace(" ", "_")
    layer = _LAYER_ALIASES.get(key)
    if layer is None:
        raise PromptLabError(f"unknown prompt lab layer: {value}")
    return layer


def _bounded_text(value: Any, *, limit: int, label: str) -> str:
    raw = str(value or "")
    if _CONTROL_RE.search(raw):
        raise PromptLabError(f"{label} contains control characters")
    text = _clean_text(raw)
    if len(text) > limit:
        raise PromptLabError(f"{label} exceeds {limit} characters")
    return text


def _coerce_terms(value: Any, *, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw_items: Iterable[Any] = (value,)
    elif isinstance(value, Mapping):
        raise PromptLabError(f"{label} must be text or a sequence of text")
    else:
        try:
            raw_items = tuple(value)
        except TypeError as exc:
            raise PromptLabError(
                f"{label} must be text or a sequence of text"
            ) from exc

    result: list[str] = []
    for raw in raw_items:
        text = _bounded_text(raw, limit=MAX_TERM_LENGTH * 4, label=label)
        if not text:
            continue
        for term in _split_top_level_commas(text):
            clean = _bounded_text(term, limit=MAX_TERM_LENGTH, label=label)
            if clean:
                result.append(clean)
            if len(result) > MAX_TERMS_PER_ASSET:
                raise PromptLabError(
                    f"{label} exceeds {MAX_TERMS_PER_ASSET} terms"
                )
    return tuple(result)


def _exact_dedupe(values: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    accepted: list[str] = []
    duplicates: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = _dedupe_key(value)
        if not key:
            continue
        if key in seen:
            duplicates.append(value)
            continue
        seen.add(key)
        accepted.append(value)
    return tuple(accepted), tuple(duplicates)


def _coerce_relation(value: Any, *, label: str) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        items: Sequence[Any] = (value,)
    elif isinstance(value, Mapping):
        raise PromptLabError(f"{label} must be text or a sequence of text")
    else:
        try:
            items = tuple(value)
        except TypeError as exc:
            raise PromptLabError(
                f"{label} must be text or a sequence of text"
            ) from exc
    parts = [
        _bounded_text(item, limit=MAX_RELATION_LENGTH, label=label)
        for item in items
    ]
    text = " ".join(part for part in parts if part)
    if len(text) > MAX_RELATION_LENGTH:
        raise PromptLabError(f"{label} exceeds {MAX_RELATION_LENGTH} characters")
    return text


def _stable_digest(payload: Any) -> str:
    material = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _normalize_seed(seed: int | str) -> tuple[str, int]:
    if isinstance(seed, bool):
        raise PromptLabError("seed must be an integer or non-empty text")
    if isinstance(seed, int):
        canonical = f"int:{seed}"
    elif isinstance(seed, str):
        text = _bounded_text(seed, limit=MAX_SEED_TEXT_LENGTH, label="seed")
        if not text:
            raise PromptLabError("seed must not be empty")
        canonical = f"str:{text}"
    else:
        raise PromptLabError("seed must be an integer or non-empty text")
    value = int.from_bytes(
        hashlib.sha256(canonical.encode("utf-8")).digest()[:8],
        "big",
    )
    return canonical, value


@dataclass(frozen=True)
class PromptLabAsset:
    """One caller-provided asset; no remote or bundled catalog is consulted."""

    asset_id: str
    label: str
    tags: tuple[str, ...] = ()
    visual_phrases: tuple[str, ...] = ()
    relation: str = ""
    lora_tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PromptLabLayers:
    """The eight explicit candidate layers exposed to the UI and caller."""

    identity: tuple[str, ...] = ()
    clothing: tuple[str, ...] = ()
    pose: tuple[str, ...] = ()
    camera: tuple[str, ...] = ()
    background: tuple[str, ...] = ()
    style: tuple[str, ...] = ()
    relation: str = ""
    lora: tuple[str, ...] = ()

    def get(self, layer: str) -> tuple[str, ...] | str:
        return getattr(self, _canonical_layer(layer))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PromptComposerDraft:
    """Strict, inert arguments for a future ``PromptComposer.compose`` call."""

    batch_id: str
    candidate_id: str
    positive_prompt: str
    negative_prompt: str
    hard_tags: tuple[str, ...]
    visual_phrases: tuple[str, ...]
    scene_sentence: str
    anchors: tuple[tuple[str, str], ...]
    source: str = "prompt_lab"

    def to_composer_kwargs(self) -> dict[str, Any]:
        """Return only public keyword arguments accepted by PromptComposer."""

        return {
            "positive_prompt": self.positive_prompt,
            "negative_prompt": self.negative_prompt,
            "hard_tags": self.hard_tags,
            "visual_phrases": self.visual_phrases,
            "scene_sentence": self.scene_sentence,
            "anchors": self.anchors,
            "source": self.source,
        }

    as_composer_kwargs = to_composer_kwargs

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PromptLabCandidate:
    """A preview-only candidate.  It contains no execution capability."""

    candidate_id: str
    ordinal: int
    layers: PromptLabLayers
    visual_phrases: tuple[str, ...]
    selected_assets: tuple[tuple[str, str], ...]
    locked_layers: tuple[str, ...]
    duplicates_removed: tuple[str, ...] = ()
    conflicts_resolved: tuple[str, ...] = ()
    discarded_terms: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PromptLabBatch:
    """One reproducible candidate batch, suitable for short-lived UI state."""

    batch_id: str
    seed: str
    seed_value: int
    requested_count: int
    candidates: tuple[PromptLabCandidate, ...]
    locked_layers: tuple[str, ...]
    enabled_asset_types: tuple[str, ...]
    negative_prompt: str = ""
    warnings: tuple[str, ...] = ()

    def find_candidate(self, selection: int | str) -> PromptLabCandidate:
        if isinstance(selection, bool):
            raise PromptLabError("candidate selection must be an ordinal or id")
        if isinstance(selection, int):
            if 1 <= selection <= len(self.candidates):
                return self.candidates[selection - 1]
            raise PromptLabError(f"candidate ordinal out of range: {selection}")
        text = _clean_text(selection)
        if text.isdecimal():
            return self.find_candidate(int(text))
        for candidate in self.candidates:
            if candidate.candidate_id == text:
                return candidate
        raise PromptLabError(f"candidate not found: {selection}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _asset_from_value(layer: str, raw: Any, index: int) -> PromptLabAsset:
    label_prefix = f"asset_pools[{layer}][{index}]"
    if isinstance(raw, PromptLabAsset):
        asset = raw
    elif isinstance(raw, str):
        if layer == "relation":
            relation = _coerce_relation(raw, label=label_prefix)
            tags: tuple[str, ...] = ()
        else:
            relation = ""
            tags = _coerce_terms(raw, label=label_prefix)
        digest = _stable_digest((layer, tags, relation))[:16]
        asset = PromptLabAsset(
            asset_id=f"{layer}-{digest}",
            label=_bounded_text(raw, limit=MAX_TERM_LENGTH, label=label_prefix),
            tags=tags,
            relation=relation,
        )
    elif isinstance(raw, Mapping):
        tag_value = raw.get(
            "tags",
            raw.get(
                "prompt",
                raw.get("value", raw.get("trigger_words", ())),
            ),
        )
        relation_value = raw.get(
            "relation",
            raw.get("scene_sentence", ""),
        )
        if layer == "relation" and not relation_value:
            relation_value = tag_value
            tag_value = ()
        tags = _coerce_terms(tag_value, label=f"{label_prefix}.tags")
        phrases = _coerce_terms(
            raw.get("visual_phrases", raw.get("phrases", ())),
            label=f"{label_prefix}.visual_phrases",
        )
        relation = _coerce_relation(
            relation_value,
            label=f"{label_prefix}.relation",
        )
        loras = _coerce_terms(
            raw.get("lora_tags", raw.get("loras", ())),
            label=f"{label_prefix}.lora_tags",
        )
        explicit_id = _bounded_text(
            raw.get("asset_id", raw.get("id", raw.get("key", ""))),
            limit=128,
            label=f"{label_prefix}.asset_id",
        )
        label = _bounded_text(
            raw.get("label", raw.get("name", raw.get("title", explicit_id))),
            limit=256,
            label=f"{label_prefix}.label",
        )
        digest = _stable_digest((layer, tags, phrases, relation, loras))[:16]
        asset = PromptLabAsset(
            asset_id=explicit_id or f"{layer}-{digest}",
            label=label or explicit_id or f"{layer} {index + 1}",
            tags=tags,
            visual_phrases=phrases,
            relation=relation,
            lora_tags=loras,
        )
    else:
        raise PromptLabError(f"{label_prefix} must be text, mapping or PromptLabAsset")

    asset_id = _bounded_text(
        asset.asset_id,
        limit=128,
        label=f"{label_prefix}.asset_id",
    )
    if not asset_id:
        raise PromptLabError(f"{label_prefix}.asset_id must not be empty")
    label = _bounded_text(
        asset.label,
        limit=256,
        label=f"{label_prefix}.label",
    )
    tags = _coerce_terms(asset.tags, label=f"{label_prefix}.tags")
    phrases = _coerce_terms(
        asset.visual_phrases,
        label=f"{label_prefix}.visual_phrases",
    )
    relation = _coerce_relation(
        asset.relation,
        label=f"{label_prefix}.relation",
    )
    explicit_loras = _coerce_terms(
        asset.lora_tags,
        label=f"{label_prefix}.lora_tags",
    )
    embedded_loras, ordinary = _extract_loras(tags)
    loras, _ = _exact_dedupe((*explicit_loras, *embedded_loras))
    if layer == "lora":
        loras, _ = _exact_dedupe((*loras, *ordinary))
        ordinary = ()
    invalid_loras = tuple(item for item in loras if not _LORA_RE.fullmatch(item))
    if invalid_loras:
        raise PromptLabError(
            f"{label_prefix} contains invalid LoRA controls: "
            + ", ".join(invalid_loras)
        )
    return PromptLabAsset(
        asset_id=asset_id,
        label=label,
        tags=ordinary,
        visual_phrases=phrases,
        relation=relation,
        lora_tags=loras,
    )


def _normalize_base_layers(
    raw_layers: Mapping[str, Any] | None,
) -> PromptLabLayers:
    if raw_layers is None:
        raw_layers = {}
    if not isinstance(raw_layers, Mapping):
        raise PromptLabError("base_layers must be a mapping")
    collected: dict[str, list[str]] = {
        layer: [] for layer in PROMPT_LAB_LAYERS if layer != "relation"
    }
    relations: list[str] = []
    for raw_layer, raw_value in raw_layers.items():
        layer = _canonical_layer(raw_layer)
        if layer == "relation":
            relation = _coerce_relation(raw_value, label="base_layers.relation")
            if relation:
                relations.append(relation)
            continue
        terms = _coerce_terms(raw_value, label=f"base_layers.{layer}")
        embedded_loras, ordinary = _extract_loras(terms)
        collected["lora"].extend(embedded_loras)
        collected[layer].extend(ordinary)

    normalized: dict[str, tuple[str, ...]] = {}
    for layer, terms in collected.items():
        values, _ = _exact_dedupe(terms)
        if len(values) > MAX_TERMS_PER_LAYER:
            raise PromptLabError(
                f"base_layers.{layer} exceeds {MAX_TERMS_PER_LAYER} terms"
            )
        normalized[layer] = values
    invalid_loras = tuple(
        item for item in normalized["lora"] if not _LORA_RE.fullmatch(item)
    )
    if invalid_loras:
        raise PromptLabError(
            "base_layers.lora contains invalid LoRA controls: "
            + ", ".join(invalid_loras)
        )
    relation = _coerce_relation(relations, label="base_layers.relation")
    layers = PromptLabLayers(relation=relation, **normalized)
    _validate_base_conflicts(layers)
    return layers


def _validate_base_conflicts(layers: PromptLabLayers) -> None:
    established: dict[str, tuple[str, str]] = {}
    conflicts: list[str] = []
    for layer in PROMPT_LAB_LAYERS:
        if layer in {"relation", "lora"}:
            continue
        for term in getattr(layers, layer):
            identity = _conflict_identity(term)
            if identity is None:
                continue
            group, variant = identity
            prior = established.get(group)
            if prior and prior[0] != variant:
                conflicts.append(f"{group}: {prior[1]} conflicts with {term}")
            else:
                established[group] = (variant, term)
    if conflicts:
        raise PromptLabError("base layers contain conflicts: " + "; ".join(conflicts))


def _normalize_locked_layers(values: Iterable[Any] | None) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        raw_values: Iterable[Any] = _split_top_level_commas(values)
    else:
        raw_values = values
    locked = {_canonical_layer(value) for value in raw_values}
    return tuple(layer for layer in PROMPT_LAB_LAYERS if layer in locked)


def _normalize_enabled_types(
    values: Iterable[Any] | None,
    available_layers: Iterable[str],
) -> tuple[str, ...]:
    available = set(available_layers)
    if values is None:
        enabled_layers = available
    else:
        if isinstance(values, str):
            raw_values: Iterable[Any] = _split_top_level_commas(values)
        else:
            raw_values = values
        enabled_layers = {_canonical_layer(value) for value in raw_values}
        unknown = enabled_layers - available
        if unknown:
            raise PromptLabError(
                "enabled asset types have no provided pool: "
                + ", ".join(sorted(unknown))
            )
    return tuple(
        _CANONICAL_ASSET_TYPE[layer]
        for layer in PROMPT_LAB_LAYERS
        if layer in enabled_layers
    )


def _normalize_asset_pools(
    raw_pools: Mapping[str, Iterable[Any]] | None,
) -> dict[str, tuple[PromptLabAsset, ...]]:
    if raw_pools is None:
        return {}
    if not isinstance(raw_pools, Mapping):
        raise PromptLabError("asset_pools must be a mapping")
    if len(raw_pools) > MAX_POOLS * 4:
        raise PromptLabError("asset_pools contains too many named pools")
    merged: dict[str, list[Any]] = {}
    for raw_layer, raw_values in raw_pools.items():
        layer = _canonical_layer(raw_layer)
        if isinstance(raw_values, str) or isinstance(raw_values, Mapping):
            values = (raw_values,)
        else:
            try:
                values = tuple(raw_values)
            except TypeError as exc:
                raise PromptLabError(
                    f"asset_pools.{layer} must be a sequence"
                ) from exc
        merged.setdefault(layer, []).extend(values)

    result: dict[str, tuple[PromptLabAsset, ...]] = {}
    total = 0
    for layer in PROMPT_LAB_LAYERS:
        raw_values = merged.get(layer, [])
        if not raw_values:
            continue
        if len(raw_values) > MAX_ASSETS_PER_POOL:
            raise PromptLabError(
                f"asset_pools.{layer} exceeds {MAX_ASSETS_PER_POOL} assets"
            )
        assets: list[PromptLabAsset] = []
        signatures: set[str] = set()
        ids: dict[str, str] = {}
        for index, raw in enumerate(raw_values):
            asset = _asset_from_value(layer, raw, index)
            signature = _stable_digest(asset.to_dict())
            prior = ids.get(asset.asset_id)
            if prior is not None and prior != signature:
                raise PromptLabError(
                    f"asset_pools.{layer} reuses id {asset.asset_id} "
                    "for different content"
                )
            ids[asset.asset_id] = signature
            if signature in signatures:
                continue
            signatures.add(signature)
            assets.append(asset)
        total += len(assets)
        if total > MAX_TOTAL_ASSETS:
            raise PromptLabError(
                f"asset pools exceed {MAX_TOTAL_ASSETS} total assets"
            )
        result[layer] = tuple(assets)
    return result


def _coprime_step(seed_value: int, total: int) -> int:
    if total <= 1:
        return 1
    candidate = (seed_value % (total - 1)) + 1
    while math.gcd(candidate, total) != 1:
        candidate += 1
        if candidate >= total:
            candidate = 1
    return candidate


def _choice_indices(
    seed_value: int,
    sizes: tuple[int, ...],
    ordinal: int,
) -> tuple[int, ...]:
    total = math.prod(sizes) if sizes else 1
    offset_seed = int.from_bytes(
        hashlib.sha256(f"{seed_value}:offset".encode("ascii")).digest()[:8],
        "big",
    )
    step_seed = int.from_bytes(
        hashlib.sha256(f"{seed_value}:step".encode("ascii")).digest()[:8],
        "big",
    )
    encoded = (offset_seed % total + ordinal * _coprime_step(step_seed, total)) % total
    result: list[int] = []
    for size in sizes:
        result.append(encoded % size)
        encoded //= size
    return tuple(result)


def _candidate_layers(
    base: PromptLabLayers,
    pools: Mapping[str, tuple[PromptLabAsset, ...]],
    active_layers: tuple[str, ...],
    locked_layers: tuple[str, ...],
    choices: tuple[int, ...],
    base_visual_phrases: tuple[str, ...],
) -> tuple[
    PromptLabLayers,
    tuple[str, ...],
    tuple[tuple[str, str], ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    locked = set(locked_layers)
    selected = {
        layer: pools[layer][choice]
        for layer, choice in zip(active_layers, choices)
    }
    raw: dict[str, list[str]] = {
        layer: [] for layer in PROMPT_LAB_LAYERS if layer != "relation"
    }
    relation = base.relation
    phrases = list(base_visual_phrases)
    selected_assets: list[tuple[str, str]] = []
    warnings: list[str] = []

    for layer in PROMPT_LAB_LAYERS:
        asset = selected.get(layer)
        if layer == "relation":
            if asset is not None:
                selected_assets.append((layer, asset.asset_id))
                phrases.extend(asset.visual_phrases)
                if layer not in locked:
                    relation = asset.relation
                if asset.lora_tags:
                    if "lora" in locked:
                        warnings.append(
                            f"{layer}:{asset.asset_id}: LoRA controls ignored by lock"
                        )
                    else:
                        raw["lora"].extend(asset.lora_tags)
            continue
        base_values = list(getattr(base, layer))
        if layer in locked or asset is None:
            raw[layer].extend(base_values)
        elif layer == "lora":
            raw[layer].extend(asset.lora_tags)
        else:
            raw[layer].extend(asset.tags)
        if asset is None:
            continue
        selected_assets.append((layer, asset.asset_id))
        phrases.extend(asset.visual_phrases)
        if asset.relation:
            if "relation" in locked:
                warnings.append(f"{layer}:{asset.asset_id}: relation ignored by lock")
            elif layer != "relation":
                relation = asset.relation
        if asset.lora_tags:
            if "lora" in locked:
                warnings.append(f"{layer}:{asset.asset_id}: LoRA controls ignored by lock")
            elif layer != "lora":
                raw["lora"].extend(asset.lora_tags)

    accepted: dict[str, list[str]] = {
        layer: [] for layer in PROMPT_LAB_LAYERS if layer != "relation"
    }
    duplicates: list[str] = []
    conflicts: list[str] = []
    discarded: list[str] = []
    seen: set[str] = set()
    established: dict[str, tuple[str, str, str]] = {}
    processing_order = tuple(locked_layers) + tuple(
        layer for layer in PROMPT_LAB_LAYERS if layer not in locked
    )
    for layer in processing_order:
        if layer == "relation":
            continue
        for term in raw[layer]:
            key = _dedupe_key(term)
            if not key:
                continue
            if key in seen:
                duplicates.append(term)
                continue
            identity = (
                None
                if layer == "lora"
                else _conflict_identity(term)
            )
            if identity is not None:
                group, variant = identity
                prior = established.get(group)
                if prior and prior[0] != variant:
                    conflicts.append(
                        f"{group}: {prior[1]} ({prior[2]}) conflicts with "
                        f"{term} ({layer})"
                    )
                    discarded.append(term)
                    continue
                established[group] = (variant, term, layer)
            seen.add(key)
            accepted[layer].append(term)

    clean_phrases: list[str] = []
    for phrase in phrases:
        key = _dedupe_key(phrase)
        if not key:
            continue
        if key in seen:
            duplicates.append(phrase)
            continue
        seen.add(key)
        clean_phrases.append(phrase)

    total_terms = sum(len(values) for values in accepted.values()) + len(
        clean_phrases
    )
    if total_terms > MAX_TOTAL_TERMS:
        raise PromptLabError(
            f"candidate exceeds {MAX_TOTAL_TERMS} total prompt terms"
        )
    for layer, values in accepted.items():
        if len(values) > MAX_TERMS_PER_LAYER:
            raise PromptLabError(
                f"candidate layer {layer} exceeds {MAX_TERMS_PER_LAYER} terms"
            )

    layers = PromptLabLayers(
        identity=tuple(accepted["identity"]),
        clothing=tuple(accepted["clothing"]),
        pose=tuple(accepted["pose"]),
        camera=tuple(accepted["camera"]),
        background=tuple(accepted["background"]),
        style=tuple(accepted["style"]),
        relation=relation,
        lora=tuple(accepted["lora"]),
    )
    return (
        layers,
        tuple(clean_phrases),
        tuple(selected_assets),
        tuple(duplicates),
        tuple(conflicts),
        tuple(discarded),
        tuple(warnings),
    )


def _candidate_prompt_length(
    layers: PromptLabLayers,
    visual_phrases: tuple[str, ...],
) -> int:
    tags: list[str] = list(layers.lora)
    for layer in PROMPT_LAB_LAYERS:
        if layer in {"lora", "relation"}:
            continue
        tags.extend(getattr(layers, layer))
    tags.extend(visual_phrases)
    prefix = ", ".join(tags)
    final = (
        f"{prefix}. {layers.relation}"
        if prefix and layers.relation
        else prefix or layers.relation
    )
    return len(final)


class PromptLab:
    """Create and confirm deterministic prompt candidates without side effects."""

    def generate_candidates(
        self,
        *,
        seed: int | str,
        count: int = 4,
        base_layers: Mapping[str, Any] | None = None,
        asset_pools: Mapping[str, Iterable[Any]] | None = None,
        locked_layers: Iterable[str] | None = None,
        enabled_asset_types: Iterable[str] | None = None,
        negative_prompt: str = "",
        visual_phrases: str | Iterable[Any] | None = None,
    ) -> PromptLabBatch:
        if isinstance(count, bool) or not isinstance(count, int):
            raise PromptLabError("candidate count must be an integer from 1 to 6")
        if not MIN_CANDIDATES <= count <= MAX_CANDIDATES:
            raise PromptLabError("candidate count must be from 1 to 6")
        seed_text, seed_value = _normalize_seed(seed)
        base = _normalize_base_layers(base_layers)
        pools = _normalize_asset_pools(asset_pools)
        locks = _normalize_locked_layers(locked_layers)
        enabled_types = _normalize_enabled_types(enabled_asset_types, pools)
        enabled_layers = {
            layer
            for layer, asset_type in _CANONICAL_ASSET_TYPE.items()
            if asset_type in enabled_types
        }
        active_layers = tuple(
            layer
            for layer in PROMPT_LAB_LAYERS
            if layer in pools and layer in enabled_layers and layer not in locks
        )
        base_phrases = _coerce_terms(
            visual_phrases,
            label="visual_phrases",
        )
        negative = _bounded_text(
            negative_prompt,
            limit=MAX_NEGATIVE_PROMPT_LENGTH,
            label="negative_prompt",
        )

        warnings: list[str] = []
        for layer in locks:
            if layer in pools and layer in enabled_layers:
                warnings.append(f"asset pool ignored for locked layer: {layer}")
        sizes = tuple(len(pools[layer]) for layer in active_layers)
        combination_count = math.prod(sizes) if sizes else 1
        if combination_count < count:
            warnings.append(
                f"variation space has {combination_count} unique combination(s); "
                f"{count} candidates requested"
            )

        candidates: list[PromptLabCandidate] = []
        for ordinal in range(count):
            choices = _choice_indices(seed_value, sizes, ordinal)
            (
                layers,
                phrases,
                selected_assets,
                duplicates,
                conflicts,
                discarded,
                candidate_warnings,
            ) = _candidate_layers(
                base,
                pools,
                active_layers,
                locks,
                choices,
                base_phrases,
            )
            prompt_length = _candidate_prompt_length(layers, phrases)
            if prompt_length > MAX_FINAL_PROMPT_LENGTH:
                raise PromptLabError(
                    f"candidate exceeds {MAX_FINAL_PROMPT_LENGTH} prompt characters"
                )
            signature = {
                "seed": seed_text,
                "ordinal": ordinal + 1,
                "layers": layers.to_dict(),
                "visual_phrases": phrases,
                "selected_assets": selected_assets,
                "locks": locks,
            }
            candidate_id = f"plc-{_stable_digest(signature)[:20]}"
            candidates.append(
                PromptLabCandidate(
                    candidate_id=candidate_id,
                    ordinal=ordinal + 1,
                    layers=layers,
                    visual_phrases=phrases,
                    selected_assets=selected_assets,
                    locked_layers=locks,
                    duplicates_removed=duplicates,
                    conflicts_resolved=conflicts,
                    discarded_terms=discarded,
                    warnings=candidate_warnings,
                )
            )
        batch_signature = {
            "seed": seed_text,
            "requested_count": count,
            "candidate_ids": [item.candidate_id for item in candidates],
            "locks": locks,
            "enabled_asset_types": enabled_types,
        }
        return PromptLabBatch(
            batch_id=f"plb-{_stable_digest(batch_signature)[:20]}",
            seed=seed_text,
            seed_value=seed_value,
            requested_count=count,
            candidates=tuple(candidates),
            locked_layers=locks,
            enabled_asset_types=enabled_types,
            negative_prompt=negative,
            warnings=tuple(warnings),
        )

    generate = generate_candidates

    def confirm_candidate(
        self,
        batch: PromptLabBatch,
        selection: int | str,
    ) -> PromptComposerDraft:
        if not isinstance(batch, PromptLabBatch):
            raise PromptLabError("batch must be a PromptLabBatch")
        candidate = batch.find_candidate(selection)
        locked = set(candidate.locked_layers)
        anchors: list[tuple[str, str]] = []
        generated: list[str] = []
        for layer in PROMPT_LAB_LAYERS:
            if layer == "relation":
                continue
            values = candidate.layers.get(layer)
            assert isinstance(values, tuple)
            if layer in locked:
                category = _ANCHOR_CATEGORIES.get(layer, layer)
                anchors.extend((value, category) for value in values)
            else:
                generated.extend(values)
        return PromptComposerDraft(
            batch_id=batch.batch_id,
            candidate_id=candidate.candidate_id,
            positive_prompt="",
            negative_prompt=batch.negative_prompt,
            hard_tags=tuple(generated),
            visual_phrases=candidate.visual_phrases,
            scene_sentence=candidate.layers.relation,
            anchors=tuple(anchors),
        )

    confirm = confirm_candidate


def generate_prompt_candidates(**kwargs: Any) -> PromptLabBatch:
    """Convenience wrapper for one deterministic candidate batch."""

    return PromptLab().generate_candidates(**kwargs)


def confirm_prompt_candidate(
    batch: PromptLabBatch,
    selection: int | str,
) -> PromptComposerDraft:
    """Convenience wrapper returning inert PromptComposer keyword arguments."""

    return PromptLab().confirm_candidate(batch, selection)


__all__ = [
    "MAX_ASSETS_PER_POOL",
    "MAX_CANDIDATES",
    "MAX_TOTAL_ASSETS",
    "PROMPT_LAB_LAYERS",
    "VISUAL_ASSET_TYPES",
    "PromptComposerDraft",
    "PromptLab",
    "PromptLabAsset",
    "PromptLabBatch",
    "PromptLabCandidate",
    "PromptLabError",
    "PromptLabLayers",
    "confirm_prompt_candidate",
    "generate_prompt_candidates",
]
