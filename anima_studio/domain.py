from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from typing import Any, ClassVar


class DomainValidationError(ValueError):
    """Raised when untrusted V7 domain input cannot be normalized safely."""


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DomainValidationError(f"{name} must be an object")
    return value


def _text(value: Any, name: str, *, maximum: int = 20_000) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise DomainValidationError(f"{name} must be a string")
    value = value.strip()
    if len(value) > maximum:
        raise DomainValidationError(f"{name} exceeds {maximum} characters")
    return value


def _boolean(value: Any, name: str, *, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise DomainValidationError(f"{name} must be a boolean")
    return value


def _integer(value: Any, name: str, minimum: int, maximum: int, default: int) -> int:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise DomainValidationError(f"{name} must be an integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise DomainValidationError(f"{name} must be an integer") from exc
    if isinstance(value, float) and not value.is_integer():
        raise DomainValidationError(f"{name} must be an integer")
    if not minimum <= result <= maximum:
        raise DomainValidationError(f"{name} must be between {minimum} and {maximum}")
    return result


def _number(value: Any, name: str, minimum: float, maximum: float, default: float) -> float:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise DomainValidationError(f"{name} must be a number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise DomainValidationError(f"{name} must be a number") from exc
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise DomainValidationError(f"{name} must be between {minimum:g} and {maximum:g}")
    return result


def _string_tuple(value: Any, name: str, *, maximum: int = 256) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        values = re.split(r"[\r\n,]+", value)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = value
    else:
        raise DomainValidationError(f"{name} must be a string or array")
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        item = _text(raw, name, maximum=500)
        key = item.casefold()
        if item and key not in seen:
            result.append(item)
            seen.add(key)
    if len(result) > maximum:
        raise DomainValidationError(f"{name} cannot contain more than {maximum} values")
    return tuple(result)


def _safe_relative_path(value: Any, name: str) -> str:
    raw = _text(value, name, maximum=1000).replace("\\", "/")
    path = PurePosixPath(raw)
    if (
        not raw
        or raw.startswith("/")
        or re.match(r"^[A-Za-z]:", raw)
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise DomainValidationError(f"{name} must be a safe relative path")
    return str(path)


def _canonical_digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_state(value: Any, *, name: str = "workspace_state") -> dict[str, Any]:
    if value in (None, ""):
        return {}
    source = _mapping(value, name)

    def validate(item: Any, depth: int) -> Any:
        if depth > 8:
            raise DomainValidationError(f"{name} exceeds the maximum nesting depth")
        if item is None or isinstance(item, (str, bool, int)):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise DomainValidationError(f"{name} contains a non-finite number")
            return item
        if isinstance(item, Mapping):
            result: dict[str, Any] = {}
            for raw_key, raw_value in item.items():
                if not isinstance(raw_key, str) or not raw_key or len(raw_key) > 200:
                    raise DomainValidationError(f"{name} keys must be non-empty strings")
                result[raw_key] = validate(raw_value, depth + 1)
            return result
        if isinstance(item, (list, tuple)):
            return [validate(child, depth + 1) for child in item]
        raise DomainValidationError(f"{name} contains a non-JSON value")

    result = validate(source, 0)
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 256_000:
        raise DomainValidationError(f"{name} exceeds 256000 bytes")
    return result


@dataclass(frozen=True, slots=True)
class LoraSelection:
    filename: str
    enabled: bool = True
    strength: float = 1.0
    role: str = "style"
    order: int = 0

    ROLES: ClassVar[frozenset[str]] = frozenset(
        {"style", "character", "detail", "concept", "utility", "other"}
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "filename", _safe_relative_path(self.filename, "filename"))
        object.__setattr__(self, "enabled", _boolean(self.enabled, "enabled", default=True))
        object.__setattr__(self, "strength", _number(self.strength, "strength", -100, 100, 1))
        role = _text(self.role or "style", "role", maximum=30).casefold()
        if role not in self.ROLES:
            raise DomainValidationError(f"unsupported LoRA role: {role}")
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "order", _integer(self.order, "order", 0, 63, 0))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, order: int = 0) -> LoraSelection:
        item = _mapping(value, "LoRA")
        filename = item.get("filename", item.get("name"))
        strength = item.get("strength")
        if strength in (None, ""):
            model_strength = item.get("strength_model")
            clip_strength = item.get("strength_clip")
            strength = model_strength if model_strength not in (None, "") else clip_strength
        return cls(
            filename=str(filename or ""),
            enabled=item.get("enabled", True),
            strength=1.0 if strength in (None, "") else strength,
            role=str(item.get("role") or "style"),
            order=item.get("order", order),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "enabled": self.enabled,
            "strength": self.strength,
            "role": self.role,
            "order": self.order,
        }

    @property
    def name(self) -> str:
        """Transitional workflow-builder spelling; V7 persistence uses filename."""
        return self.filename


@dataclass(frozen=True, slots=True)
class SamplingSettings:
    width: int = 832
    height: int = 1216
    steps: int = 30
    cfg: float = 4.0
    seed: int = -1
    prompt_seed: int = -1
    count: int = 1
    sampler: str = ""
    scheduler: str = ""
    denoise: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "width", _integer(self.width, "width", 64, 8192, 832))
        object.__setattr__(self, "height", _integer(self.height, "height", 64, 8192, 1216))
        object.__setattr__(self, "steps", _integer(self.steps, "steps", 1, 200, 30))
        object.__setattr__(self, "cfg", _number(self.cfg, "cfg", 0, 100, 4))
        object.__setattr__(self, "seed", _integer(self.seed, "seed", -1, 2**63 - 1, -1))
        object.__setattr__(
            self,
            "prompt_seed",
            _integer(self.prompt_seed, "prompt_seed", -1, 2**63 - 1, -1),
        )
        object.__setattr__(self, "count", _integer(self.count, "count", 1, 1000, 1))
        object.__setattr__(self, "sampler", _text(self.sampler, "sampler", maximum=100))
        object.__setattr__(self, "scheduler", _text(self.scheduler, "scheduler", maximum=100))
        object.__setattr__(self, "denoise", _number(self.denoise, "denoise", 0, 1, 1))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> SamplingSettings:
        item = dict(value or {})
        return cls(
            width=item.get("width", 832),
            height=item.get("height", 1216),
            steps=item.get("steps", 30),
            cfg=item.get("cfg", 4.0),
            seed=item.get("seed", item.get("sample_seed", -1)),
            prompt_seed=item.get("prompt_seed", -1),
            count=item.get("count", 1),
            sampler=item.get("sampler", item.get("sampler_name", "")),
            scheduler=item.get("scheduler", ""),
            denoise=item.get("denoise", 1.0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "steps": self.steps,
            "cfg": self.cfg,
            "seed": self.seed,
            "prompt_seed": self.prompt_seed,
            "count": self.count,
            "sampler": self.sampler,
            "scheduler": self.scheduler,
            "denoise": self.denoise,
        }


@dataclass(frozen=True, slots=True)
class RepairSettings:
    hires_enabled: bool = False
    upscale_model: str = ""
    upscale_percent: int = 0
    detailers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "hires_enabled", _boolean(self.hires_enabled, "hires_enabled"))
        object.__setattr__(self, "upscale_model", _text(self.upscale_model, "upscale_model", maximum=500))
        object.__setattr__(
            self,
            "upscale_percent",
            _integer(self.upscale_percent, "upscale_percent", 0, 100, 0),
        )
        detailers = _string_tuple(self.detailers, "detailers", maximum=32)
        object.__setattr__(self, "detailers", detailers)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> RepairSettings:
        item = dict(value or {})
        hires = item.get("hires") if isinstance(item.get("hires"), Mapping) else item
        raw_detailers = item.get("detailers", ())
        if isinstance(raw_detailers, Mapping):
            raw_detailers = [name for name, enabled in raw_detailers.items() if enabled is True]
        return cls(
            hires_enabled=hires.get("enabled", item.get("hires_enabled", False)),
            upscale_model=hires.get("model_name", item.get("upscale_model", "")),
            upscale_percent=hires.get("percent", item.get("upscale_percent", 0)),
            detailers=raw_detailers,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "hires_enabled": self.hires_enabled,
            "upscale_model": self.upscale_model,
            "upscale_percent": self.upscale_percent,
            "detailers": list(self.detailers),
        }


@dataclass(frozen=True, slots=True)
class ImageInput:
    asset_id: str = ""
    path: str = ""
    sha256: str = ""
    mime_type: str = ""
    width: int = 0
    height: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_id", _text(self.asset_id, "asset_id", maximum=200))
        object.__setattr__(self, "path", _text(self.path, "path", maximum=2000))
        digest = _text(self.sha256, "sha256", maximum=64).casefold()
        if digest and not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise DomainValidationError("sha256 must be 64 hexadecimal characters")
        object.__setattr__(self, "sha256", digest)
        object.__setattr__(self, "mime_type", _text(self.mime_type, "mime_type", maximum=100))
        object.__setattr__(self, "width", _integer(self.width, "width", 0, 100_000, 0))
        object.__setattr__(self, "height", _integer(self.height, "height", 0, 100_000, 0))
        if not self.asset_id and not self.path:
            raise DomainValidationError("image input requires asset_id or path")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ImageInput:
        item = _mapping(value, "image input")
        return cls(
            asset_id=item.get("asset_id", item.get("id", "")),
            path=item.get("path", item.get("image_path", "")),
            sha256=item.get("sha256", ""),
            mime_type=item.get("mime_type", ""),
            width=item.get("width", 0),
            height=item.get("height", 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "path": self.path,
            "sha256": self.sha256,
            "mime_type": self.mime_type,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True, slots=True)
class ControlInput:
    kind: str
    image: ImageInput
    strength: float = 1.0
    start: float = 0.0
    end: float = 1.0
    preprocessor: str = ""
    model: str = ""

    def __post_init__(self) -> None:
        kind = _text(self.kind, "control kind", maximum=60).casefold()
        if not kind:
            raise DomainValidationError("control kind is required")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "strength", _number(self.strength, "control strength", 0, 2, 1))
        object.__setattr__(self, "start", _number(self.start, "control start", 0, 1, 0))
        object.__setattr__(self, "end", _number(self.end, "control end", 0, 1, 1))
        if self.start > self.end:
            raise DomainValidationError("control start cannot exceed end")
        object.__setattr__(self, "preprocessor", _text(self.preprocessor, "preprocessor", maximum=200))
        object.__setattr__(self, "model", _text(self.model, "control model", maximum=500))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ControlInput:
        item = _mapping(value, "control input")
        image = item.get("image")
        if not isinstance(image, Mapping):
            image = {"asset_id": item.get("asset_id", ""), "path": item.get("image_path", "")}
        return cls(
            kind=item.get("kind", item.get("type", "")),
            image=ImageInput.from_mapping(image),
            strength=item.get("strength", 1.0),
            start=item.get("start", 0.0),
            end=item.get("end", 1.0),
            preprocessor=item.get("preprocessor", ""),
            model=item.get("model", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "image": self.image.to_dict(),
            "strength": self.strength,
            "start": self.start,
            "end": self.end,
            "preprocessor": self.preprocessor,
            "model": self.model,
        }


@dataclass(frozen=True, slots=True)
class PoolSelection:
    mode: str = "include"
    ids: tuple[str, ...] = ()
    excluded_ids: tuple[str, ...] = ()
    count: int = 1
    fixed_tags: str = ""

    def __post_init__(self) -> None:
        mode = _text(self.mode or "include", "pool mode", maximum=20).casefold()
        if mode not in {"include", "exclude", "all", "off"}:
            raise DomainValidationError(f"unsupported pool mode: {mode}")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "ids", _string_tuple(self.ids, "pool ids"))
        object.__setattr__(self, "excluded_ids", _string_tuple(self.excluded_ids, "excluded pool ids"))
        object.__setattr__(self, "count", _integer(self.count, "pool count", 0, 100, 1))
        object.__setattr__(self, "fixed_tags", _text(self.fixed_tags, "fixed_tags"))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None, *, fixed_tags: str = "") -> PoolSelection:
        item = dict(value or {})
        return cls(
            mode=item.get("mode", "include"),
            ids=item.get("ids", ()),
            excluded_ids=item.get("excluded_ids", ()),
            count=item.get("count", 1),
            fixed_tags=item.get("fixed_tags", fixed_tags),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "ids": list(self.ids),
            "excluded_ids": list(self.excluded_ids),
            "count": self.count,
            "fixed_tags": self.fixed_tags,
        }


_WORKSPACES = {"random", "natural", "studio"}
_MODES = {
    "text_to_image",
    "reverse",
    "control",
    "image_to_image",
    "inpaint",
    "character_swap",
    "upscale",
}


@dataclass(frozen=True, slots=True)
class GenerationIntent:
    workspace: str
    mode: str = "text_to_image"
    positive_prompt: str = ""
    negative_prompt: str = ""
    locked_tags: tuple[str, ...] = ()
    artist_tags: tuple[str, ...] = ()
    style_preset_id: str = ""
    prompt_asset_ids: tuple[str, ...] = ()
    prompt_plan_id: str = ""
    model: str = ""
    pipeline: str = ""
    loras: tuple[LoraSelection, ...] = ()
    sampling: SamplingSettings = field(default_factory=SamplingSettings)
    random_pools: tuple[tuple[str, PoolSelection], ...] = ()
    random_options: Mapping[str, Any] = field(default_factory=dict)
    input_image: ImageInput | None = None
    mask_image: ImageInput | None = None
    inpaint_mode: str = "quick"
    controls: tuple[ControlInput, ...] = ()
    repair: RepairSettings = field(default_factory=RepairSettings)
    intent_id: str = ""
    revision: int = 1

    def __post_init__(self) -> None:
        workspace = _text(self.workspace, "workspace", maximum=20).casefold()
        if workspace not in _WORKSPACES:
            raise DomainValidationError(f"unsupported workspace: {workspace}")
        object.__setattr__(self, "workspace", workspace)
        mode = _text(self.mode or "text_to_image", "mode", maximum=40).casefold()
        if mode not in _MODES:
            raise DomainValidationError(f"unsupported generation mode: {mode}")
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "positive_prompt", _text(self.positive_prompt, "positive_prompt"))
        object.__setattr__(self, "negative_prompt", _text(self.negative_prompt, "negative_prompt"))
        object.__setattr__(self, "locked_tags", _string_tuple(self.locked_tags, "locked_tags"))
        artists: list[str] = []
        for item in _string_tuple(self.artist_tags, "artist_tags", maximum=64):
            normalized = item[1:].strip() if item.startswith("@") else item
            if normalized.casefold().startswith("by "):
                normalized = normalized[3:].strip()
            if normalized:
                artists.append(f"@{normalized}")
        object.__setattr__(self, "artist_tags", tuple(artists))
        object.__setattr__(self, "style_preset_id", _text(self.style_preset_id, "style_preset_id", maximum=200))
        object.__setattr__(
            self,
            "prompt_asset_ids",
            _string_tuple(self.prompt_asset_ids, "prompt_asset_ids", maximum=200),
        )
        object.__setattr__(self, "prompt_plan_id", _text(self.prompt_plan_id, "prompt_plan_id", maximum=200))
        object.__setattr__(self, "model", _text(self.model, "model", maximum=1000))
        object.__setattr__(self, "pipeline", _text(self.pipeline, "pipeline", maximum=200))
        if len(self.loras) > 64:
            raise DomainValidationError("an intent cannot contain more than 64 LoRAs")
        identities: set[str] = set()
        normalized_loras: list[LoraSelection] = []
        for index, item in enumerate(self.loras):
            lora = item if isinstance(item, LoraSelection) else LoraSelection.from_mapping(item, order=index)
            identity = lora.filename.casefold()
            if identity in identities:
                raise DomainValidationError(f"duplicate LoRA: {lora.filename}")
            identities.add(identity)
            normalized_loras.append(lora)
        normalized_loras.sort(key=lambda item: item.order)
        object.__setattr__(self, "loras", tuple(normalized_loras))
        pools: list[tuple[str, PoolSelection]] = []
        seen_pools: set[str] = set()
        for raw_name, raw_selection in self.random_pools:
            name = _text(raw_name, "pool name", maximum=100).casefold()
            if not name or name in seen_pools:
                raise DomainValidationError(f"invalid or duplicate pool: {name}")
            seen_pools.add(name)
            selection = (
                raw_selection
                if isinstance(raw_selection, PoolSelection)
                else PoolSelection.from_mapping(raw_selection)
            )
            pools.append((name, selection))
        object.__setattr__(self, "random_pools", tuple(pools))
        object.__setattr__(self, "random_options", _json_state(self.random_options))
        if self.input_image is not None and not isinstance(self.input_image, ImageInput):
            object.__setattr__(self, "input_image", ImageInput.from_mapping(self.input_image))
        if self.mask_image is not None and not isinstance(self.mask_image, ImageInput):
            object.__setattr__(self, "mask_image", ImageInput.from_mapping(self.mask_image))
        inpaint_mode = _text(self.inpaint_mode or "quick", "inpaint_mode", maximum=20).casefold()
        if inpaint_mode not in {"quick", "lanpaint"}:
            raise DomainValidationError(f"unsupported inpaint mode: {inpaint_mode}")
        object.__setattr__(self, "inpaint_mode", inpaint_mode)
        object.__setattr__(self, "intent_id", _text(self.intent_id, "intent_id", maximum=200))
        object.__setattr__(self, "revision", _integer(self.revision, "revision", 1, 2**31 - 1, 1))

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        workspace: str | None = None,
    ) -> GenerationIntent:
        item = dict(_mapping(value, "generation intent"))
        selected_workspace = str(workspace or item.get("workspace") or _detect_workspace(item))
        nested_sampling = item.get("sampling") if isinstance(item.get("sampling"), Mapping) else {}
        sampling_values = {
            **dict(nested_sampling),
            **{
                key: item[key]
                for key in (
                    "width",
                    "height",
                    "steps",
                    "cfg",
                    "seed",
                    "sample_seed",
                    "prompt_seed",
                    "count",
                    "sampler",
                    "scheduler",
                    "denoise",
                )
                if key in item
            },
        }
        raw_loras = item.get("loras") or item.get("dynamic_loras") or []
        if not isinstance(raw_loras, Sequence) or isinstance(raw_loras, (str, bytes, bytearray)):
            raise DomainValidationError("loras must be an array")
        loras = tuple(LoraSelection.from_mapping(raw, order=index) for index, raw in enumerate(raw_loras))
        positive = item.get("positive_prompt")
        if positive is None:
            positive = item.get("full_prompt", item.get("composer_prompt", item.get("prompt", "")))
        locked = item.get("locked_tags", item.get("extra_prompt", ()))
        artists = item.get("artist_tags", item.get("manual_artist", ()))
        raw_pools = item.get("random_pools", item.get("pools", {}))
        pools: list[tuple[str, PoolSelection]] = []
        if isinstance(raw_pools, Mapping):
            for name, selection in raw_pools.items():
                fixed = item.get(f"fixed_{name}", "")
                count = item.get(f"random_{name}_count")
                pool_value = dict(selection) if isinstance(selection, Mapping) else {}
                if count is not None:
                    pool_value["count"] = count
                random_key = f"random_{name}"
                if random_key in item:
                    if bool(item.get(random_key)):
                        if not pool_value.get("ids") and not pool_value.get("excluded_ids"):
                            pool_value["mode"] = "all"
                    else:
                        pool_value["mode"] = "off"
                pools.append((str(name), PoolSelection.from_mapping(pool_value, fixed_tags=str(fixed))))
        random_options = item.get("random_options")
        if not isinstance(random_options, Mapping):
            random_options = {
                key: item[key]
                for key in ("female_count", "male_count", "character_detail", "quality_prompt")
                if key in item
            }
        input_value = item.get("input_image")
        if not isinstance(input_value, Mapping) and item.get("input_image_path"):
            input_value = {"path": item["input_image_path"]}
        mask_value = item.get("mask_image")
        if not isinstance(mask_value, Mapping):
            if item.get("mask_asset_id"):
                mask_value = {"asset_id": item["mask_asset_id"]}
            elif item.get("mask_image_path") or item.get("mask_path"):
                mask_value = {"path": item.get("mask_image_path") or item.get("mask_path")}
        controls_value = item.get("controls") or []
        if not isinstance(controls_value, Sequence) or isinstance(controls_value, (str, bytes, bytearray)):
            raise DomainValidationError("controls must be an array")
        repair_value = item.get("repair") if isinstance(item.get("repair"), Mapping) else item
        return cls(
            workspace=selected_workspace,
            mode=_legacy_mode(item),
            positive_prompt=positive or "",
            negative_prompt=item.get("negative_prompt", ""),
            locked_tags=locked,
            artist_tags=artists,
            style_preset_id=item.get("style_preset_id", item.get("style_preset", "")),
            prompt_asset_ids=item.get("prompt_asset_ids", ()),
            prompt_plan_id=item.get("prompt_plan_id", ""),
            model=item.get("model", item.get("model_name", "")),
            pipeline=item.get("pipeline", item.get("workflow", "")),
            loras=loras,
            sampling=SamplingSettings.from_mapping(sampling_values),
            random_pools=tuple(pools),
            random_options=random_options,
            input_image=ImageInput.from_mapping(input_value) if isinstance(input_value, Mapping) else None,
            mask_image=ImageInput.from_mapping(mask_value) if isinstance(mask_value, Mapping) else None,
            inpaint_mode=item.get("inpaint_mode", "quick"),
            controls=tuple(ControlInput.from_mapping(control) for control in controls_value),
            repair=RepairSettings.from_mapping(repair_value),
            intent_id=item.get("intent_id", item.get("id", "")),
            revision=item.get("revision", 1),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], workspace: str | None = None) -> GenerationIntent:
        return cls.from_mapping(value, workspace)

    @classmethod
    def from_legacy_random(cls, value: Mapping[str, Any]) -> GenerationIntent:
        return cls.from_mapping(value, "random")

    @classmethod
    def from_legacy_natural(cls, value: Mapping[str, Any]) -> GenerationIntent:
        return cls.from_mapping(value, "natural")

    @property
    def digest(self) -> str:
        return _canonical_digest(self.to_dict(include_digest=False))

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "intent_id": self.intent_id,
            "workspace": self.workspace,
            "mode": self.mode,
            "positive_prompt": self.positive_prompt,
            "negative_prompt": self.negative_prompt,
            "locked_tags": list(self.locked_tags),
            "artist_tags": list(self.artist_tags),
            "style_preset_id": self.style_preset_id,
            "prompt_asset_ids": list(self.prompt_asset_ids),
            "prompt_plan_id": self.prompt_plan_id,
            "model": self.model,
            "pipeline": self.pipeline,
            "loras": [item.to_dict() for item in self.loras],
            "sampling": self.sampling.to_dict(),
            "random_pools": {name: selection.to_dict() for name, selection in self.random_pools},
            "random_options": dict(self.random_options),
            "input_image": self.input_image.to_dict() if self.input_image else None,
            "mask_image": self.mask_image.to_dict() if self.mask_image else None,
            "inpaint_mode": self.inpaint_mode,
            "controls": [control.to_dict() for control in self.controls],
            "repair": self.repair.to_dict(),
            "revision": self.revision,
        }
        if include_digest:
            result["digest"] = _canonical_digest(result)
        return result

    def validate(self) -> GenerationIntent:
        return self

    def revised(self, **changes: Any) -> GenerationIntent:
        return replace(self, revision=self.revision + 1, **changes)


def _detect_workspace(item: Mapping[str, Any]) -> str:
    random_markers = {"pools", "random_character", "fixed_character", "quality_prompt", "manual_artist"}
    return "random" if random_markers.intersection(item) else "natural"


def _legacy_mode(item: Mapping[str, Any]) -> str:
    mode = str(
        item.get("mode")
        or item.get("job_type")
        or item.get("generation_mode")
        or item.get("task")
        or ""
    ).casefold()
    aliases = {
        "txt2img": "text_to_image",
        "img2img": "image_to_image",
        "control_generation": "control",
        "character-swap": "character_swap",
        "swap": "character_swap",
    }
    if mode in aliases:
        return aliases[mode]
    if mode in _MODES:
        return mode
    if item.get("mask_image") or item.get("mask_asset_id") or item.get("mask_image_path") or item.get("mask_path"):
        return "inpaint"
    if item.get("input_image") or item.get("input_image_path"):
        return "image_to_image"
    return "text_to_image"


@dataclass(frozen=True, slots=True)
class WorkspaceDraft:
    workspace: str
    intent: GenerationIntent
    workspace_state: Mapping[str, Any] = field(default_factory=dict)
    revision: int = 1
    updated_at: str = ""

    def __post_init__(self) -> None:
        workspace = _text(self.workspace, "workspace", maximum=20).casefold()
        if workspace not in {"random", "natural"}:
            raise DomainValidationError("a workspace draft must be random or natural")
        if self.intent.workspace != workspace:
            raise DomainValidationError("draft workspace and intent workspace differ")
        object.__setattr__(self, "workspace", workspace)
        object.__setattr__(self, "workspace_state", _json_state(self.workspace_state))
        object.__setattr__(self, "revision", _integer(self.revision, "revision", 1, 2**31 - 1, 1))
        object.__setattr__(self, "updated_at", _text(self.updated_at, "updated_at", maximum=100))

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        workspace: str | None = None,
    ) -> WorkspaceDraft:
        item = _mapping(value, "workspace draft")
        selected = str(workspace or item.get("workspace") or _detect_workspace(item))
        raw_intent = item.get("intent") if isinstance(item.get("intent"), Mapping) else item
        return cls(
            workspace=selected,
            intent=GenerationIntent.from_mapping(raw_intent, selected),
            workspace_state=item.get("workspace_state", item.get("ui_state", {})),
            revision=item.get("revision", 1),
            updated_at=item.get("updated_at", ""),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any], workspace: str | None = None) -> WorkspaceDraft:
        return cls.from_mapping(value, workspace)

    @property
    def digest(self) -> str:
        return _canonical_digest(self.to_dict(include_digest=False))

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        result = {
            "workspace": self.workspace,
            "intent": self.intent.to_dict(),
            "workspace_state": _json_state(self.workspace_state),
            "revision": self.revision,
            "updated_at": self.updated_at,
        }
        if include_digest:
            result["digest"] = _canonical_digest(result)
        return result

    def validate(self) -> WorkspaceDraft:
        return self


@dataclass(frozen=True, slots=True)
class StylePreset:
    id: str
    name: str
    intent: GenerationIntent
    aliases: tuple[str, ...] = ()
    favorite: bool = False
    revision: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "preset id", maximum=200))
        object.__setattr__(self, "name", _text(self.name, "preset name", maximum=100))
        if not self.id or not self.name:
            raise DomainValidationError("preset id and name are required")
        object.__setattr__(self, "aliases", _string_tuple(self.aliases, "preset aliases", maximum=64))
        object.__setattr__(self, "favorite", _boolean(self.favorite, "favorite"))
        object.__setattr__(self, "revision", _integer(self.revision, "revision", 1, 2**31 - 1, 1))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> StylePreset:
        item = _mapping(value, "style preset")
        raw_intent = item.get("intent")
        if not isinstance(raw_intent, Mapping):
            raw_intent = item.get("settings")
        if not isinstance(raw_intent, Mapping):
            raise DomainValidationError("style preset requires intent or settings")
        workspace = str(raw_intent.get("workspace") or item.get("workspace") or "random")
        return cls(
            id=item.get("id", item.get("preset_id", "")),
            name=item.get("name", ""),
            intent=GenerationIntent.from_mapping(raw_intent, workspace),
            aliases=item.get("aliases", ()),
            favorite=item.get("favorite", False),
            revision=item.get("revision", 1),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StylePreset:
        return cls.from_mapping(value)

    @property
    def digest(self) -> str:
        return _canonical_digest(self.to_dict(include_digest=False))

    @property
    def settings(self) -> dict[str, Any]:
        return self.intent.to_dict(include_digest=False)

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        result = {
            "id": self.id,
            "name": self.name,
            "aliases": list(self.aliases),
            "favorite": self.favorite,
            "intent": self.intent.to_dict(),
            "revision": self.revision,
        }
        if include_digest:
            result["digest"] = _canonical_digest(result)
        return result

    def validate(self) -> StylePreset:
        return self


def _layer_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, WorkspaceDraft):
        return value.intent.to_dict(include_digest=False)
    if isinstance(value, StylePreset):
        return value.intent.to_dict(include_digest=False)
    if isinstance(value, GenerationIntent):
        return value.to_dict(include_digest=False)
    return dict(_mapping(value, "intent layer"))


def _deep_overlay(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if key in {"digest", "revision", "intent_id", "id"}:
            continue
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _deep_overlay(dict(result[key]), value)
        else:
            result[key] = value
    return result


def merge_intent_layers(
    defaults: GenerationIntent | Mapping[str, Any],
    draft: WorkspaceDraft | GenerationIntent | Mapping[str, Any] | None = None,
    preset: StylePreset | GenerationIntent | Mapping[str, Any] | None = None,
    natural_language: GenerationIntent | Mapping[str, Any] | None = None,
    explicit: GenerationIntent | Mapping[str, Any] | None = None,
    *,
    workspace: str | None = None,
) -> GenerationIntent:
    """Merge complete or partial layers using the V7 precedence contract.

    Later arguments win: explicit > natural language > preset > draft > defaults.
    Arrays, including LoRAs and controls, replace as a whole; nested objects merge.
    """

    values = _layer_mapping(defaults)
    selected_workspace = workspace or str(values.get("workspace") or "natural")
    for layer in (draft, preset, natural_language, explicit):
        values = _deep_overlay(values, _layer_mapping(layer))
    values["workspace"] = selected_workspace
    revision = max(
        [getattr(value, "revision", 0) for value in (defaults, draft, preset, natural_language, explicit) if value]
        or [0]
    )
    values["revision"] = max(1, revision + 1)
    return GenerationIntent.from_mapping(values, selected_workspace)


# Public compatibility names use domain language rather than transport-specific names.
InputImage = ImageInput
RandomPoolSelection = PoolSelection


def generation_intent_from_random_payload(value: Mapping[str, Any]) -> GenerationIntent:
    return GenerationIntent.from_legacy_random(value)


def generation_intent_from_natural_payload(value: Mapping[str, Any]) -> GenerationIntent:
    return GenerationIntent.from_legacy_natural(value)
