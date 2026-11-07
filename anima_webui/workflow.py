from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path
from typing import Any

from .sampling import (
    latent_upscale_node,
    validate_random_dual,
    validate_resize_options,
    validate_resize_scale,
    DUAL_DEFAULTS,
    DLSS_DEFAULTS,
    validate_dlss,
)
from .model_profiles import (
    DEFAULT_MODEL_PROFILE,
    ModelProfileError,
    MODEL_PROFILES,
    get_model_profile,
    normalize_model_profile,
)


DEFAULT_QUALITY = (
    "masterpiece, best quality, score_9, score_8, highres, year 2025, newest, safe"
)


DEFAULT_NEGATIVE = (
    "worst quality, low quality, lowres, score_1, score_2, score_3, blurry, "
    "jpeg artifacts, bad anatomy, watermark, artist name,"
)


DEFAULT_LORAS: list[dict[str, Any]] = []


DEFAULT_MODEL = MODEL_PROFILES[DEFAULT_MODEL_PROFILE]["model_name"]


DEFAULT_SAMPLER = "euler"


DEFAULT_SCHEDULER = "simple"


MAX_LORAS = 64


MAX_LORA_MANAGED_TRIGGERS = 256


MIN_LORA_STRENGTH = -100.0


MAX_LORA_STRENGTH = 100.0


PROMPT_SECTIONS = ("character", "clothing", "pose", "background", "expression")


MAX_SAMPLE_SEED = 1125899906842624


DEFAULT_SETTINGS = {
    "pipeline_version": 4,
    "workbench": "random",
    "generation_mode": "txt2img",
    "dual": copy.deepcopy(DUAL_DEFAULTS),
    "base_denoise": 1.0,
    "count": 10,
    "random_character": False,
    "random_clothing": False,
    "random_pose": False,
    "random_background": False,
    "random_expression": False,
    "random_character_count": 1,
    "random_clothing_count": 1,
    "random_pose_count": 1,
    "random_background_count": 1,
    "random_expression_count": 1,
    "fixed_character": "",
    "fixed_clothing": "",
    "fixed_pose": "",
    "fixed_background": "",
    "fixed_expression": "",
    "female_count": 1,
    "male_count": 0,
    "model_name": DEFAULT_MODEL,
    "model_profile": DEFAULT_MODEL_PROFILE,
    "clip_name": MODEL_PROFILES[DEFAULT_MODEL_PROFILE]["clip_name"],
    "vae_name": MODEL_PROFILES[DEFAULT_MODEL_PROFILE]["vae_name"],
    "clip_type": MODEL_PROFILES[DEFAULT_MODEL_PROFILE]["clip_type"],
    "loras": DEFAULT_LORAS,
    "lora_managed_triggers": [],
    "pools": {
        "character": {"mode": "include", "ids": [], "excluded_ids": []},
        "clothing": {"mode": "include", "ids": [], "excluded_ids": []},
        "pose": {"mode": "include", "ids": [], "excluded_ids": []},
        "background": {"mode": "include", "ids": [], "excluded_ids": []},
        "expression": {"mode": "include", "ids": [], "excluded_ids": []},
    },
    "character_detail": "trigger_tags",
    "manual_artist": "",
    "quality_prompt": DEFAULT_QUALITY,
    "extra_prompt": "",
    "negative_prompt": DEFAULT_NEGATIVE,
    "width": 832,
    "height": 1216,
    "steps": MODEL_PROFILES[DEFAULT_MODEL_PROFILE]["defaults"]["steps"],
    "cfg": MODEL_PROFILES[DEFAULT_MODEL_PROFILE]["defaults"]["cfg"],
    "sampler_name": MODEL_PROFILES[DEFAULT_MODEL_PROFILE]["defaults"]["sampler_name"],
    "scheduler": MODEL_PROFILES[DEFAULT_MODEL_PROFILE]["defaults"]["scheduler"],
    "base_mode": MODEL_PROFILES[DEFAULT_MODEL_PROFILE]["defaults"]["base_mode"],
    "upscalers": {
        "pixel_hires": {
            "enabled": False,
            "model_name": MODEL_PROFILES[DEFAULT_MODEL_PROFILE]["pixel_hires_model"],
            "percent": 50,
            "upscale_method": "lanczos",
        },
        "post_sampler": copy.deepcopy(
            MODEL_PROFILES[DEFAULT_MODEL_PROFILE]["post_sampler_defaults"]
        ),
        "dlss": {"enabled": False, **copy.deepcopy(DLSS_DEFAULTS)},
        "latent": {
            "enabled": False,
            "scale_by": 1.5,
            "upscale_method": "sesqui",
            "model_format": "Wan 2.1",
            "half_precision": True,
            "sampler": copy.deepcopy(
                MODEL_PROFILES[DEFAULT_MODEL_PROFILE]["latent_sampler_defaults"]
            ),
        },
        "seedvr2": {
            "enabled": False,
            "model_name": MODEL_PROFILES[DEFAULT_MODEL_PROFILE]["seedvr2_model"],
            "vae_name": MODEL_PROFILES[DEFAULT_MODEL_PROFILE]["seedvr2_vae"],
            "scale_by": 2.0,
            "weight_dtype": "default",
            "color_correction_method": "lab",
            "sampler": {
                "steps": 1,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
            },
            "encode": {
                "tile_size": 512,
                "overlap": 64,
                "temporal_size": 64,
                "temporal_overlap": 8,
            },
            "decode": {
                "tile_size": 512,
                "overlap": 128,
                "temporal_size": 4096,
                "temporal_overlap": 8,
            },
        },
        "nvidia": {
            "enabled": False,
            "resize_type": "scale by multiplier",
            "scale": 2.0,
            "quality": "ULTRA",
        },
        "face_refine": {
            "enabled": False,
            "steps": 8,
            "cfg_scale": 1.0,
            "sampler": "res_2m_ode",
            "scheduler": "beta57",
            "denoise_strength": 0.3,
            "face_selection": 0,
            "detection_confidence": 0.75,
            "manual_rotation": "None",
            "processing_resolution": 1024,
            "enable_pre_upscale": True,
            "upscaler_model": "Fast 4x (Lanczos)",
            "crop_padding": 1.6,
            "face_positive_prompt": "",
            "replace_positive_prompt": False,
            "face_negative_prompt": "",
            "replace_negative_prompt": False,
            "exclusions": "",
            "blend_softness": 8,
            "mask_expansion": 2,
            "sampling_mask_blur_size": 21,
            "sampling_mask_blur_strength": 1,
            "enable_color_correction": True,
            "enable_segmentation": True,
            "enable_differential_diffusion": True,
            "enable_lightness_rescue": True,
            "enable_final_refinement": True,
            "offload_models_to_cpu": True,
        },
    },
}


class WorkflowError(ValueError):
    pass


def read_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str | Path, value: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _integer(name: str, value: Any, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorkflowError(f"{name} 必须是整数")
    if not minimum <= value <= maximum:
        raise WorkflowError(f"{name} 必须在 {minimum}-{maximum} 之间")
    return value


def _boolean(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise WorkflowError(f"{name} 必须是布尔值")
    return value


def _text(name: str, value: Any) -> str:
    if not isinstance(value, str):
        raise WorkflowError(f"{name} 必须是字符串")
    if len(value) > 20000:
        raise WorkflowError(f"{name} 不能超过 20000 个字符")
    return value.strip()


def normalize_lora_path(value: Any) -> str:
    filename = _text("LoRA 文件名", value).replace("\\", "/")
    parts = filename.split("/")
    if (
        not filename
        or filename.startswith("/")
        or len(filename) >= 2
        and filename[1] == ":"
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise WorkflowError("LoRA 文件名必须是安全的相对路径")
    return "/".join(parts)


def normalize_artist_tags(value: Any) -> str:
    raw = _text("manual_artist", value).replace("\r", ",").replace("\n", ",")
    names: list[str] = []
    seen: set[str] = set()
    for part in re.split(r",|(?=@)", raw):
        name = part.strip()
        if name.startswith("@"):
            name = name[1:].strip()
        elif name.lower().startswith("by "):
            name = name[3:].strip()
        if not name:
            continue
        identity = name.casefold()
        if identity not in seen:
            names.append(name)
            seen.add(identity)
    return ", ".join(f"@{name}" for name in names)


def _normalize_loras(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise WorkflowError("loras 必须是数组")
    if len(value) > MAX_LORAS:
        raise WorkflowError(f"LoRA 配置不能超过 {MAX_LORAS} 项")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(value, 1):
        if not isinstance(item, dict):
            raise WorkflowError(f"loras[{index}] 必须是对象")
        filename = normalize_lora_path(item.get("filename"))
        identity = filename.casefold()
        if identity in seen:
            raise WorkflowError(f"LoRA 不能重复配置: {filename}")
        seen.add(identity)
        enabled = _boolean(f"loras[{index}].enabled", item.get("enabled"))
        strength = item.get("strength")
        if isinstance(strength, bool) or not isinstance(strength, (int, float)):
            raise WorkflowError(f"loras[{index}].strength 必须是数字")
        if not math.isfinite(float(strength)):
            raise WorkflowError(f"loras[{index}].strength 必须是有限数字")
        strength = float(strength)
        if not MIN_LORA_STRENGTH <= strength <= MAX_LORA_STRENGTH:
            raise WorkflowError(
                f"loras[{index}].strength 必须在 {MIN_LORA_STRENGTH:g}-{MAX_LORA_STRENGTH:g} 之间"
            )
        result.append({"filename": filename, "enabled": enabled, "strength": strength})
    return result


def validate_loras(
    settings: dict[str, Any],
    available_filenames: Any | None = None,
    *,
    allow_basename: bool = True,
) -> list[dict[str, Any]]:
    """Validate the persisted LoRA shape and, when available, the ComfyUI inventory."""
    loras = _normalize_loras(settings.get("loras", DEFAULT_LORAS))
    if available_filenames is not None:
        available: dict[str, str] = {}
        by_basename: dict[str, list[str]] = {}
        for raw in available_filenames:
            exact = str(raw)
            normalized = normalize_lora_path(exact)
            available.setdefault(normalized.casefold(), exact)
            by_basename.setdefault(normalized.rsplit("/", 1)[-1].casefold(), []).append(
                exact
            )
        for item in loras:
            if not item["enabled"]:
                continue
            normalized = normalize_lora_path(item["filename"])
            exact = available.get(normalized.casefold())
            if exact is None and allow_basename and "/" not in normalized:
                matches = list(
                    dict.fromkeys(by_basename.get(normalized.casefold(), []))
                )
                if len(matches) == 1:
                    exact = matches[0]
                elif len(matches) > 1:
                    raise WorkflowError(
                        f"LoRA 文件名存在多个子目录匹配，请重新选择: {item['filename']}"
                    )
            if exact is None:
                raise WorkflowError(f"LoRA 文件不存在: {item['filename']}")
            item["filename"] = exact
    return loras


def _normalize_managed_triggers(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise WorkflowError("lora_managed_triggers 必须是数组")
    if len(value) > MAX_LORA_MANAGED_TRIGGERS:
        raise WorkflowError(
            f"lora_managed_triggers 不能超过 {MAX_LORA_MANAGED_TRIGGERS} 项"
        )
    result: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        word = _text(f"lora_managed_triggers[{index}]", raw)
        if len(word) > 500:
            raise WorkflowError(f"lora_managed_triggers[{index}] 过长")
        identity = word.casefold()
        if word and identity not in seen:
            result.append(word)
            seen.add(identity)
    return result


def _validate_post_sampler(value: Any, field_prefix: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowError(f"{field_prefix} 必须是对象")
    allowed = {"enabled", "steps", "cfg", "sampler_name", "scheduler", "denoise"}
    unknown = set(value) - allowed
    if unknown:
        raise WorkflowError(f"未知 {field_prefix} 参数: {', '.join(sorted(unknown))}")
    defaults = {
        "enabled": True,
        "steps": 20,
        "cfg": 8.0,
        "sampler_name": DEFAULT_SAMPLER,
        "scheduler": DEFAULT_SCHEDULER,
        "denoise": 1.0,
    }
    merged = {**defaults, **value}
    enabled = _boolean(f"{field_prefix}.enabled", merged["enabled"])
    steps = _integer(f"{field_prefix}.steps", merged["steps"], 1, 150)
    cfg = merged["cfg"]
    if isinstance(cfg, bool) or not isinstance(cfg, (int, float)):
        raise WorkflowError(f"{field_prefix}.cfg 必须是数字")
    cfg = float(cfg)
    if not math.isfinite(cfg) or not 0.1 <= cfg <= 30:
        raise WorkflowError(f"{field_prefix}.cfg 必须在 0.1-30 之间")
    sampler_name = _text(f"{field_prefix}.sampler_name", merged["sampler_name"])
    scheduler = _text(f"{field_prefix}.scheduler", merged["scheduler"])
    if not sampler_name or not scheduler:
        raise WorkflowError(f"{field_prefix} 的 sampler_name 和 scheduler 不能为空")
    denoise = merged["denoise"]
    if isinstance(denoise, bool) or not isinstance(denoise, (int, float)):
        raise WorkflowError(f"{field_prefix}.denoise 必须是数字")
    denoise = float(denoise)
    if not math.isfinite(denoise) or not 0 <= denoise <= 1:
        raise WorkflowError(f"{field_prefix}.denoise 必须在 0-1 之间")
    return {
        "enabled": enabled,
        "steps": steps,
        "cfg": cfg,
        "sampler_name": sampler_name,
        "scheduler": scheduler,
        "denoise": denoise,
    }


def _validate_upscalers(
    value: Any, profile_id: str = DEFAULT_MODEL_PROFILE, pipeline_version: int = 4
) -> dict[str, Any]:
    """Validate repair settings using the recorded pipeline contract.

    Each stage is independent; the renderer decides whether an installed
    optional node can be connected.  Validation deliberately only checks the
    persisted shape and numeric bounds so a missing optional ComfyUI plugin is
    reported by the resource preflight instead of corrupting settings.
    """

    if not isinstance(value, dict):
        raise WorkflowError("upscalers 必须是对象")
    allowed = {
        "latent",
        "seedvr2",
        "nvidia",
        "face_refine",
        "pixel_hires",
        "post_sampler",
        "dlss",
    }
    unknown = set(value) - allowed
    if unknown:
        raise WorkflowError(f"未知放大修复模块: {', '.join(sorted(unknown))}")
    merged = copy.deepcopy(DEFAULT_SETTINGS["upscalers"])
    for key, item in value.items():
        if not isinstance(item, dict):
            raise WorkflowError(f"upscalers.{key} 必须是对象")
        merged[key].update(copy.deepcopy(item))
    profile = get_model_profile(profile_id)
    pixel = merged["pixel_hires"]
    if set(pixel) - {"enabled", "model_name", "percent", "upscale_method"}:
        raise WorkflowError("未知像素高清参数")
    pixel_method = _text(
        "upscalers.pixel_hires.upscale_method", pixel["upscale_method"]
    )
    if pixel_method not in {"nearest-exact", "bilinear", "area", "bicubic", "lanczos"}:
        raise WorkflowError("upscalers.pixel_hires.upscale_method 无效")
    pixel_name = _text("upscalers.pixel_hires.model_name", pixel["model_name"])
    if not pixel_name:
        raise WorkflowError("upscalers.pixel_hires.model_name 不能为空")
    pixel = {
        "enabled": _boolean("upscalers.pixel_hires.enabled", pixel["enabled"]),
        "model_name": pixel_name,
        "percent": _integer("upscalers.pixel_hires.percent", pixel["percent"], 1, 100),
        "upscale_method": pixel_method,
    }
    post = _validate_post_sampler(
        {**profile["post_sampler_defaults"], **value.get("post_sampler", {})},
        "upscalers.post_sampler",
    )
    dlss = {
        "enabled": _boolean("upscalers.dlss.enabled", merged["dlss"]["enabled"]),
        **validate_dlss(merged["dlss"], "upscalers.dlss"),
    }
    latent = merged["latent"]
    if set(latent) - {
        "enabled",
        "scale_by",
        "upscale_method",
        "model_format",
        "half_precision",
        "sampler",
    }:
        raise WorkflowError("未知潜空间放大参数")
    enabled = _boolean("upscalers.latent.enabled", latent["enabled"])
    scale_by = latent["scale_by"]
    if isinstance(scale_by, bool) or not isinstance(scale_by, (int, float)):
        raise WorkflowError("upscalers.latent.scale_by 必须是数字")
    maximum = 8
    if not math.isfinite(float(scale_by)) or not 0.01 <= float(scale_by) <= maximum:
        raise WorkflowError(f"upscalers.latent.scale_by 必须在 0.01-{maximum} 之间")
    resize_options = validate_resize_options(latent, "upscalers.latent")
    result: dict[str, Any] = {
        "pixel_hires": pixel,
        "post_sampler": post,
        "dlss": dlss,
        "latent": {
            "enabled": enabled,
            "scale_by": float(scale_by),
            **resize_options,
        },
    }
    sampler = value.get("latent", {}).get("sampler", {})
    if not isinstance(sampler, dict) or set(sampler) - set(
        profile["latent_sampler_defaults"]
    ):
        raise WorkflowError("upscalers.latent.sampler 必须是仅含采样参数的对象")
    sampler = _validate_post_sampler(
        {**profile["latent_sampler_defaults"], **sampler}, "upscalers.latent.sampler"
    )
    sampler.pop("enabled")
    result["latent"]["sampler"] = sampler

    seedvr2 = merged["seedvr2"]
    seedvr2_allowed = {
        "enabled",
        "model_name",
        "vae_name",
        "scale_by",
        "weight_dtype",
        "color_correction_method",
        "sampler",
        "encode",
        "decode",
    }
    if set(seedvr2) - seedvr2_allowed:
        raise WorkflowError("未知 upscalers.seedvr2 参数")
    profile = get_model_profile(profile_id)
    seedvr2_model = _text(
        "upscalers.seedvr2.model_name",
        seedvr2.get("model_name") or profile["seedvr2_model"],
    )
    seedvr2_vae = _text(
        "upscalers.seedvr2.vae_name", seedvr2.get("vae_name") or profile["seedvr2_vae"]
    )
    seedvr2_scale = seedvr2.get("scale_by")
    if isinstance(seedvr2_scale, bool) or not isinstance(seedvr2_scale, (int, float)):
        raise WorkflowError("upscalers.seedvr2.scale_by 必须是数字")
    seedvr2_scale = float(seedvr2_scale)
    if not math.isfinite(seedvr2_scale) or not 0.01 <= seedvr2_scale <= 64:
        raise WorkflowError("upscalers.seedvr2.scale_by 必须在 0.01-64 之间")
    weight_dtype = _text("upscalers.seedvr2.weight_dtype", seedvr2["weight_dtype"])
    color_correction = _text(
        "upscalers.seedvr2.color_correction_method", seedvr2["color_correction_method"]
    )
    seed_sampler = seedvr2["sampler"]
    if not isinstance(seed_sampler, dict):
        raise WorkflowError("upscalers.seedvr2.sampler 必须是对象")
    seed_sampler = {
        **DEFAULT_SETTINGS["upscalers"]["seedvr2"]["sampler"],
        **seed_sampler,
    }
    seed_sampler = _validate_post_sampler(
        {"enabled": True, **seed_sampler}, "upscalers.seedvr2.sampler"
    )
    seed_sampler.pop("enabled", None)

    def validate_tiles(name: str, item: Any) -> dict[str, int]:
        defaults = DEFAULT_SETTINGS["upscalers"]["seedvr2"][name]
        if not isinstance(item, dict):
            raise WorkflowError(f"upscalers.seedvr2.{name} 必须是对象")
        unknown = set(item) - set(defaults)
        if unknown:
            raise WorkflowError(
                f"未知 upscalers.seedvr2.{name} 参数: {', '.join(sorted(unknown))}"
            )
        values = {**defaults, **item}
        validated = {
            "tile_size": _integer(
                f"upscalers.seedvr2.{name}.tile_size", values["tile_size"], 64, 16384
            ),
            "overlap": _integer(
                f"upscalers.seedvr2.{name}.overlap", values["overlap"], 0, 4096
            ),
            "temporal_size": _integer(
                f"upscalers.seedvr2.{name}.temporal_size",
                values["temporal_size"],
                1,
                16384,
            ),
            "temporal_overlap": _integer(
                f"upscalers.seedvr2.{name}.temporal_overlap",
                values["temporal_overlap"],
                0,
                4096,
            ),
        }
        # Encoding may use half-width/half-height tiles for a 2-D VAE. A
        # non-positive stride otherwise reaches range(..., step=0) or division
        # by zero inside ComfyUI before any useful image can be produced.
        spatial_limit = (
            validated["tile_size"] // 2 if name == "encode" else validated["tile_size"]
        )
        if validated["overlap"] >= spatial_limit:
            raise WorkflowError(
                f"upscalers.seedvr2.{name}.overlap 必须小于{'分块大小的一半' if name == 'encode' else '分块大小'}"
            )
        if validated["temporal_overlap"] >= validated["temporal_size"]:
            raise WorkflowError(
                f"upscalers.seedvr2.{name}.temporal_overlap 必须小于时间分块大小"
            )
        return validated

    result["seedvr2"] = {
        "enabled": _boolean("upscalers.seedvr2.enabled", seedvr2["enabled"]),
        "model_name": seedvr2_model,
        "vae_name": seedvr2_vae,
        "scale_by": seedvr2_scale,
        "weight_dtype": weight_dtype,
        "color_correction_method": color_correction,
        "sampler": seed_sampler,
        "encode": validate_tiles("encode", seedvr2["encode"]),
        "decode": validate_tiles("decode", seedvr2["decode"]),
    }

    nvidia = merged["nvidia"]
    if set(nvidia) - {"enabled", "resize_type", "scale", "quality"}:
        raise WorkflowError("未知 upscalers.nvidia 参数")
    scale = nvidia["scale"]
    if isinstance(scale, bool) or not isinstance(scale, (int, float)):
        raise WorkflowError("upscalers.nvidia.scale 必须是数字")
    scale = float(scale)
    if not math.isfinite(scale) or not 0.01 <= scale <= 64:
        raise WorkflowError("upscalers.nvidia.scale 必须在 0.01-64 之间")
    resize_type = _text("upscalers.nvidia.resize_type", nvidia["resize_type"])
    quality = _text("upscalers.nvidia.quality", nvidia["quality"])
    if not resize_type or not quality:
        raise WorkflowError("upscalers.nvidia.resize_type 和 quality 不能为空")
    result["nvidia"] = {
        "enabled": _boolean("upscalers.nvidia.enabled", nvidia["enabled"]),
        "resize_type": resize_type,
        "scale": scale,
        "quality": quality,
    }

    face = merged["face_refine"]
    face_defaults = DEFAULT_SETTINGS["upscalers"]["face_refine"]
    if set(face) - set(face_defaults):
        raise WorkflowError("未知 upscalers.face_refine 参数")
    face_values = {**face_defaults, **face}
    face_float_ranges = {
        "cfg_scale": (0.1, 30.0),
        "denoise_strength": (0.0, 1.0),
        "detection_confidence": (0.0, 1.0),
        "crop_padding": (0.0, 16.0),
        "sampling_mask_blur_strength": (0.0, 6.0),
    }
    face_int_ranges = {
        "steps": (1, 150),
        "face_selection": (0, 1000000),
        "processing_resolution": (64, 8192),
        "blend_softness": (0, 4096),
        "mask_expansion": (0, 4096),
        "sampling_mask_blur_size": (0, 4096),
    }
    validated_face: dict[str, Any] = {
        "enabled": _boolean("upscalers.face_refine.enabled", face_values["enabled"])
    }
    for name, bounds in face_float_ranges.items():
        number = face_values[name]
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise WorkflowError(f"upscalers.face_refine.{name} 必须是数字")
        number = float(number)
        if not math.isfinite(number) or not bounds[0] <= number <= bounds[1]:
            raise WorkflowError(f"upscalers.face_refine.{name} 超出允许范围")
        validated_face[name] = number
    for name, bounds in face_int_ranges.items():
        validated_face[name] = _integer(
            f"upscalers.face_refine.{name}", face_values[name], bounds[0], bounds[1]
        )
    for name in (
        "sampler",
        "scheduler",
        "manual_rotation",
        "upscaler_model",
        "face_positive_prompt",
        "face_negative_prompt",
        "exclusions",
    ):
        validated_face[name] = _text(f"upscalers.face_refine.{name}", face_values[name])
    if not validated_face["sampler"] or not validated_face["scheduler"]:
        raise WorkflowError("脸部细化 sampler 和 scheduler 不能为空")
    for name in (
        "enable_pre_upscale",
        "replace_positive_prompt",
        "replace_negative_prompt",
        "enable_color_correction",
        "enable_segmentation",
        "enable_differential_diffusion",
        "enable_lightness_rescue",
        "enable_final_refinement",
        "offload_models_to_cpu",
    ):
        validated_face[name] = _boolean(
            f"upscalers.face_refine.{name}", face_values[name]
        )
    result["face_refine"] = validated_face
    return result


def migrate_settings(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Normalize this standalone application's settings without importing legacy modes."""
    if overrides is None:
        source = {}
    elif isinstance(overrides, dict):
        source = copy.deepcopy(overrides)
    else:
        raise WorkflowError("设置必须是对象")
    unknown = set(source) - set(DEFAULT_SETTINGS)
    if unknown:
        raise WorkflowError(f"未知参数: {', '.join(sorted(unknown))}")
    try:
        profile = normalize_model_profile(source.get("model_profile", "anima"))
    except ModelProfileError as error:
        raise WorkflowError(str(error)) from error
    if source.get("workbench", "random") != "random":
        raise WorkflowError("仅支持 Anima 随机工作台 workbench=random")
    if source.get("generation_mode", "txt2img") != "txt2img":
        raise WorkflowError("随机工作台仅支持文生图 generation_mode=txt2img")
    source["pipeline_version"] = _integer(
        "pipeline_version", source.get("pipeline_version", 4), 4, 4
    )
    source.update(model_profile=profile, workbench="random", generation_mode="txt2img")
    for key in ("clip_name", "vae_name", "clip_type"):
        source.setdefault(key, MODEL_PROFILES[profile][key])
    base = source.get("base_mode", DEFAULT_SETTINGS["base_mode"])
    if base not in ("normal", "dual_sampler"):
        raise WorkflowError("base_mode 必须是 normal 或 dual_sampler")
    source["base_mode"] = base
    source.setdefault("steps", 30 if base == "dual_sampler" else 20)
    return source


def upgrade_pipeline_settings(
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Keep fresh requests and saved settings on the supported v4 contract."""
    return validate_settings(overrides)


def validate_settings(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    source = migrate_settings(overrides)
    profile_id = source["model_profile"]
    legacy_pools = "pools" not in source and any(
        source.get(f"random_{s}") for s in PROMPT_SECTIONS
    )
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    settings.update(source)
    settings["count"] = _integer("生成数量", settings["count"], 1, 1000)
    for name in tuple(f"random_{section}" for section in PROMPT_SECTIONS):
        settings[name] = _boolean(name, settings[name])
    for name in tuple(f"random_{section}_count" for section in PROMPT_SECTIONS):
        settings[name] = _integer(name, settings[name], 1, 5)
    if settings["random_expression_count"] != 1:
        raise WorkflowError("表情随机池每张只能抽取 1 项")
    settings["female_count"] = _integer("女性人数", settings["female_count"], 0, 5)
    settings["male_count"] = _integer("男性人数", settings["male_count"], 0, 5)
    total_people = settings["female_count"] + settings["male_count"]
    if total_people > 5:
        raise WorkflowError("画面总人数不能超过 5")
    if (
        settings["random_character"]
        and total_people
        and settings["random_character_count"] > total_people
    ):
        raise WorkflowError("指定角色数不能超过画面总人数")

    pools = settings.get("pools")
    if legacy_pools:
        pools = {
            section: {"mode": "all", "ids": [], "excluded_ids": []}
            for section in PROMPT_SECTIONS
        }
    if not isinstance(pools, dict):
        raise WorkflowError("pools 必须是对象")
    normalized_pools = {}
    for section in PROMPT_SECTIONS:
        selection = pools.get(section, {})
        if not isinstance(selection, dict):
            raise WorkflowError(f"{section} 随机池设置无效")
        mode = selection.get("mode", "include")
        if not isinstance(mode, str) or mode not in {"include", "all"}:
            raise WorkflowError(f"{section} 随机池模式无效")
        ids = selection.get("ids", [])
        excluded_ids = selection.get("excluded_ids", [])
        if not isinstance(ids, list) or not isinstance(excluded_ids, list):
            raise WorkflowError(f"{section} 随机池条目必须是数组")
        if len(ids) > 50000 or len(excluded_ids) > 50000:
            raise WorkflowError(f"{section} 随机池条目过多")
        normalized_pools[section] = {
            "mode": mode,
            "ids": list(
                dict.fromkeys(_text(f"{section} 条目", value) for value in ids)
            ),
            "excluded_ids": list(
                dict.fromkeys(
                    _text(f"{section} 排除条目", value) for value in excluded_ids
                )
            ),
        }
    settings["pools"] = normalized_pools
    settings["model_name"] = _text("model_name", settings["model_name"])
    if not settings["model_name"]:
        raise WorkflowError("model_name 不能为空")
    for name in ("clip_name", "vae_name", "clip_type"):
        settings[name] = _text(name, settings[name])
        if not settings[name]:
            raise WorkflowError(f"{name} 不能为空")
    base_mode = settings.get("base_mode")
    if not isinstance(base_mode, str) or base_mode not in {"normal", "dual_sampler"}:
        raise WorkflowError("base_mode 必须是 normal 或 dual_sampler")
    settings["upscalers"] = _validate_upscalers(
        settings.get("upscalers"), profile_id, settings["pipeline_version"]
    )
    settings["loras"] = validate_loras(settings)
    settings["lora_managed_triggers"] = _normalize_managed_triggers(
        settings["lora_managed_triggers"]
    )

    if not isinstance(settings["character_detail"], str) or settings[
        "character_detail"
    ] not in {"trigger", "trigger_tags", "trigger_tags_no_clothing"}:
        raise WorkflowError(
            "character_detail 必须是 trigger、trigger_tags 或 trigger_tags_no_clothing"
        )

    settings["manual_artist"] = normalize_artist_tags(settings["manual_artist"])
    for name in (
        "fixed_character",
        "fixed_clothing",
        "fixed_pose",
        "fixed_background",
        "fixed_expression",
        "quality_prompt",
        "extra_prompt",
        "negative_prompt",
    ):
        settings[name] = _text(name, settings[name])

    for name in ("width", "height"):
        settings[name] = _integer(name, settings[name], 64, 4096)
        if settings[name] % 8:
            raise WorkflowError(f"{name} 必须能被 8 整除")

    settings["steps"] = _integer("steps", settings["steps"], 1, 150)
    if isinstance(settings["cfg"], bool) or not isinstance(
        settings["cfg"], (int, float)
    ):
        raise WorkflowError("cfg 必须是数字")
    settings["cfg"] = float(settings["cfg"])
    if not 0.1 <= settings["cfg"] <= 30:
        raise WorkflowError("cfg 必须在 0.1-30 之间")
    settings["sampler_name"] = _text("sampler_name", settings["sampler_name"])
    settings["scheduler"] = _text("scheduler", settings["scheduler"])
    if not settings["sampler_name"]:
        raise WorkflowError("sampler_name 不能为空")
    if not settings["scheduler"]:
        raise WorkflowError("scheduler 不能为空")
    denoise = settings["base_denoise"]
    if (
        isinstance(denoise, bool)
        or not isinstance(denoise, (int, float))
        or not math.isfinite(denoise)
        or not 0 <= denoise <= 1
    ):
        raise WorkflowError("base_denoise 必须在 0-1 之间")
    settings["base_denoise"] = float(denoise)
    settings["dual"] = validate_random_dual(settings["dual"], settings)
    latent = settings["upscalers"]["latent"]
    validate_resize_scale(
        latent["scale_by"],
        latent["upscale_method"],
        "upscalers.latent.scale_by",
        maximum=8,
        active=latent["enabled"],
    )
    return settings


def _mapped_resource_name(
    value: str, resource_paths: dict[str, str] | None = None
) -> str:
    """Apply configured ComfyUI-relative paths to a profile resource name."""

    if not resource_paths:
        return value
    identity = str(value).replace("\\", "/").casefold()
    for source, target in resource_paths.items():
        if str(source).replace("\\", "/").casefold() == identity:
            return str(target).replace("\\", "/")
    return value


def _lora_manager_values(loras: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Lora Manager consumes active/name/clipStrength, unlike rgthree's
    # on/lora/strengthTwo widgets used by the legacy workflow.
    return [
        {
            "active": bool(item["enabled"]),
            "name": item["filename"],
            "strength": item["strength"],
            "clipStrength": item["strength"],
        }
        for item in loras
    ]


def _v2_prompt(
    settings: dict[str, Any], resolved_prompt: str, resolved_prompt_full: str
) -> str:
    """Build a deterministic prompt for the graph's plain CLIPTextEncode."""

    if resolved_prompt_full.strip():
        return resolved_prompt_full
    if resolved_prompt.strip():
        return resolved_prompt
    if any(settings.get(f"random_{section}") for section in PROMPT_SECTIONS):
        raise WorkflowError("随机提示词尚未解析，无法提交生成")
    parts: list[str] = []
    for key in ("quality_prompt", "manual_artist"):
        value = str(settings.get(key) or "").strip()
        if value:
            parts.append(value)
    for section in PROMPT_SECTIONS:
        if not settings.get(f"random_{section}"):
            value = str(settings.get(f"fixed_{section}") or "").strip()
            if value:
                parts.append(value)
    extra = str(settings.get("extra_prompt") or "").strip()
    if extra:
        parts.append(extra)
    return ", ".join(parts)


V2_UNET_ID = "167:56"
V2_CLIP_ID = "167:57"
V2_VAE_ID = "167:58"
V2_OUTPUT_ID = "v2:output"


def validate_template_nodes(api: dict[str, Any], ui: dict[str, Any]) -> None:
    required = {
        V2_UNET_ID: "UNETLoader",
        V2_CLIP_ID: "CLIPLoader",
        V2_VAE_ID: "VAELoader",
        "72": "Lora Loader (LoraManager)",
        "1010:995": "CLIPTextEncode",
        "1010:1004": "CLIPTextEncode",
        "691:1240": "KSampler",
        "701:1245": "KSamplerAdvanced",
        "701:1246": "KSamplerAdvanced",
    }
    missing = [
        key
        for key, kind in required.items()
        if api.get(key, {}).get("class_type") != kind
    ]
    if missing or not isinstance(ui.get("nodes"), list):
        raise WorkflowError("Anima 工作流模板不完整: " + ", ".join(missing))


def _render_base(
    api_template: dict[str, Any],
    settings: dict[str, Any],
    sample_seed: int,
    positive: str,
    negative: str,
    resource_paths: dict[str, str] | None,
) -> dict[str, Any]:
    """Bind the retained Anima base template; repair stages are built independently."""
    ids = (V2_UNET_ID, V2_CLIP_ID, V2_VAE_ID, "72", "1010:995", "1010:1004")
    api = {key: copy.deepcopy(api_template[key]) for key in ids}
    api[V2_UNET_ID]["inputs"]["unet_name"] = _mapped_resource_name(
        settings["model_name"], resource_paths
    )
    api[V2_CLIP_ID]["inputs"].update(
        clip_name=_mapped_resource_name(settings["clip_name"], resource_paths),
        type=settings["clip_type"],
    )
    api[V2_VAE_ID]["inputs"]["vae_name"] = _mapped_resource_name(
        settings["vae_name"], resource_paths
    )
    api["72"]["inputs"].update(
        loras={"__value__": _lora_manager_values(settings["loras"])},
        text=", ".join(settings["lora_managed_triggers"]),
    )
    for key, text in (("1010:995", positive), ("1010:1004", negative)):
        api[key]["inputs"] = {"clip": ["72", 1], "text": text}
    inherited = {
        "model": ["72", 0],
        "positive": ["1010:995", 0],
        "negative": ["1010:1004", 0],
    }
    empty = "701:496" if settings["base_mode"] == "dual_sampler" else "691:53"
    api[empty] = {
        "class_type": "EmptyLatentImage",
        "inputs": {
            "width": settings["width"],
            "height": settings["height"],
            "batch_size": 1,
        },
    }
    sampler = {
        key: settings[key] for key in ("steps", "cfg", "sampler_name", "scheduler")
    }
    if settings["base_mode"] == "normal":
        api["691:1240"] = copy.deepcopy(api_template["691:1240"])
        api["691:1240"]["inputs"] = {
            **inherited,
            **sampler,
            "seed": sample_seed,
            "latent_image": [empty, 0],
            "denoise": settings["base_denoise"],
        }
    else:
        dual = settings["dual"]
        api["701:1450"] = latent_upscale_node(["701:1245", 0], dual, dual["scale"])
        for key, start, latent, leftover in (
            ("701:1245", 0, [empty, 0], "enable"),
            ("701:1246", dual["switch_step"], ["701:1450", 0], "disable"),
        ):
            api[key] = copy.deepcopy(api_template[key])
            api[key]["inputs"] = {
                **inherited,
                **sampler,
                "noise_seed": sample_seed,
                "latent_image": latent,
                "start_at_step": start,
                "end_at_step": settings["steps"],
                "add_noise": "enable",
                "return_with_leftover_noise": leftover,
            }
        api["701:1246"]["inputs"].update(
            cfg=dual["second_cfg"],
            sampler_name=dual["second_sampler_name"],
            scheduler=dual["second_scheduler"],
        )
    if settings["upscalers"]["face_refine"]["enabled"]:
        api["1253:1215"] = {
            "class_type": "ForbiddenVisionFaceProcessorIntegrated",
            "inputs": {
                **{
                    key: value
                    for key, value in settings["upscalers"]["face_refine"].items()
                    if key != "enabled"
                },
                "seed": sample_seed,
                **inherited,
                "vae": [V2_VAE_ID, 0],
                "clip": ["72", 1],
            },
        }
    return api


def render_workflows(
    api_template: dict[str, Any],
    ui_template: dict[str, Any],
    settings: dict[str, Any],
    sample_seed: int,
    prompt_seed: int,
    filename_prefix: str,
    resolved_prompt: str = "",
    resolved_selection: dict[str, Any] | None = None,
    resolved_prompt_full: str = "",
    frozen_positive_prompt: str = "",
    frozen_negative_prompt: str = "",
    resource_paths: dict[str, str] | None = None,
    *,
    execution_nonce: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from .pipeline_v3 import build_pipeline

    settings = validate_settings(settings)
    _integer("sample_seed", sample_seed, 0, MAX_SAMPLE_SEED)
    _integer("prompt_seed", prompt_seed, 0, 2**31 - 1)
    positive = frozen_positive_prompt or _v2_prompt(
        settings, resolved_prompt, resolved_prompt_full
    )
    negative = frozen_negative_prompt or settings["negative_prompt"]
    api = _render_base(
        api_template, settings, sample_seed, positive, negative, resource_paths
    )
    return build_pipeline(
        api,
        ui_template,
        settings,
        sample_seed,
        prompt_seed,
        filename_prefix,
        resource_paths=resource_paths,
        execution_nonce=execution_nonce,
    )


def build_submission(
    api_template: dict[str, Any],
    ui_template: dict[str, Any],
    settings: dict[str, Any],
    sample_seed: int,
    prompt_seed: int,
    filename_prefix: str,
    client_id: str,
    sequence: int,
    resolved_prompt: str = "",
    resolved_selection: dict[str, Any] | None = None,
    resolved_prompt_full: str = "",
    frozen_positive_prompt: str = "",
    frozen_negative_prompt: str = "",
    resource_paths: dict[str, str] | None = None,
    *,
    execution_nonce: int | None = None,
) -> dict[str, Any]:
    api, ui = render_workflows(
        api_template,
        ui_template,
        settings,
        sample_seed,
        prompt_seed,
        filename_prefix,
        resolved_prompt,
        resolved_selection,
        resolved_prompt_full,
        frozen_positive_prompt,
        frozen_negative_prompt,
        resource_paths,
        execution_nonce=execution_nonce,
    )
    metadata_settings = validate_settings(settings)
    final_positive = frozen_positive_prompt or _v2_prompt(
        metadata_settings, resolved_prompt, resolved_prompt_full
    )
    metadata = {
        "settings": metadata_settings,
        "sample_seed": sample_seed,
        "prompt_seed": prompt_seed,
        "sequence": sequence,
        "resolved_selection": resolved_selection or {},
        "resolved_prompt": resolved_prompt,
        "resolved_prompt_full": final_positive,
        "frozen_positive_prompt": frozen_positive_prompt,
    }
    pipeline = (ui.get("extra") or {}).get("pipeline") or {}
    if pipeline.get("version", 2) >= 3:
        metadata["execution_context"] = {"execution_nonce": pipeline["execution_nonce"]}
    return {
        "prompt": api,
        "client_id": client_id,
        "extra_data": {
            "extra_pnginfo": {"workflow": ui, "anima_random_webui": metadata},
        },
    }


class WorkflowTemplates:
    def __init__(self, api_template: dict[str, Any], ui_template: dict[str, Any]):
        validate_template_nodes(api_template, ui_template)
        self.api = api_template
        self.ui = ui_template
        self.resource_paths: dict[str, str] = {}
        self.is_v2 = True  # Namespaced base template used by the current v4 renderer.

    @classmethod
    def load(cls, template_dir: str | Path) -> "WorkflowTemplates":
        directory = Path(template_dir)
        v2_api = directory / "workflow_api_v2.json"
        v2_ui = directory / "workflow_ui_v2.json"
        return cls(read_json(v2_api), read_json(v2_ui))

    def submission(
        self,
        settings: dict[str, Any],
        sample_seed: int,
        prompt_seed: int,
        filename_prefix: str,
        client_id: str,
        sequence: int,
        resolved_prompt: str = "",
        resolved_selection: dict[str, Any] | None = None,
        resolved_prompt_full: str = "",
        frozen_positive_prompt: str = "",
        frozen_negative_prompt: str = "",
        *,
        execution_nonce: int | None = None,
        resources_resolved: bool = False,
    ) -> dict[str, Any]:
        return build_submission(
            self.api,
            self.ui,
            settings,
            sample_seed,
            prompt_seed,
            filename_prefix,
            client_id,
            sequence,
            resolved_prompt,
            resolved_selection,
            resolved_prompt_full,
            frozen_positive_prompt,
            frozen_negative_prompt,
            None if resources_resolved else self.resource_paths,
            execution_nonce=execution_nonce,
        )
