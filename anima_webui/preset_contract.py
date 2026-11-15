"""Portable Anima random-workbench style patches."""
from __future__ import annotations

import copy
from typing import Any

from .model_profiles import get_model_profile, normalize_model_profile
from .prompt_rules import PromptRuleStore
from .sampling import DUAL_DEFAULTS
from .workflow import CURRENT_PIPELINE_VERSION, DEFAULT_SETTINGS, WorkflowError, validate_settings


INCLUDE_KEYS = ("size", "repair")
CORE_KEYS = (
    "model_profile", "model_name", "clip_name", "vae_name", "clip_type", "loras",
    "lora_managed_triggers", "manual_artist", "quality_prompt", "extra_prompt",
    "negative_prompt", "zero_negative", "steps", "cfg", "sampler_name", "scheduler",
    "base_mode", "base_denoise",
)
DUAL_KEYS = tuple(key for key in DUAL_DEFAULTS if key != "half_precision")
REPAIR_DEVICE_KEYS = {
    "latent": {"half_precision"}, "seedvr2": {"weight_dtype", "encode", "decode"},
    "face_refine": {"offload_models_to_cpu"}, "dlss": {"gpu_gen"},
}


def normalize_include(value: Any = None) -> dict[str, bool]:
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - set(INCLUDE_KEYS):
        raise WorkflowError("风格预设 include 必须只包含 size、repair")
    if any(not isinstance(flag, bool) for flag in value.values()):
        raise WorkflowError("风格预设携带项必须是布尔值")
    return {key: value.get(key, False) for key in INCLUDE_KEYS}


def normalize_source(value: Any, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or {}
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - {"workbench", "pipeline_version"}:
        raise WorkflowError("风格预设 source 无效")
    workbench = value.get("workbench", settings.get("workbench", "random"))
    version = value.get("pipeline_version", settings.get("pipeline_version", 4))
    if workbench != "random":
        raise WorkflowError("风格预设来源工作台必须为 random")
    if type(version) is not int or not 4 <= version <= CURRENT_PIPELINE_VERSION:
        raise WorkflowError("风格预设来源 pipeline_version 无效")
    return {"workbench": workbench, "pipeline_version": version}


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in patch.items():
        result[key] = (deep_merge(result.get(key, {}) if isinstance(result.get(key), dict) else {}, value)
                       if isinstance(value, dict) else copy.deepcopy(value))
    return result


def _pick(value: dict[str, Any], keys: Any) -> dict[str, Any]:
    return {key: copy.deepcopy(value[key]) for key in keys if key in value}


def _repair(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowError("风格预设 upscalers 必须是对象")
    result = copy.deepcopy(value)
    for stage, keys in REPAIR_DEVICE_KEYS.items():
        if isinstance(result.get(stage), dict):
            for key in keys:
                result[stage].pop(key, None)
    if isinstance(result.get("nvidia"), dict) and isinstance(result["nvidia"].get("dlss"), dict):
        result["nvidia"]["dlss"].pop("gpu_gen", None)
    return result


def effective_dual(settings: dict[str, Any]) -> dict[str, Any]:
    if isinstance(settings.get("dual"), dict):
        return _pick(settings["dual"], DUAL_KEYS)
    latent = settings["upscalers"]["latent"]
    return {
        "switch_step": 15, "scale": latent["scale_by"], "upscale_method": latent["upscale_method"],
        "model_format": latent["model_format"], "second_cfg": settings["cfg"],
        "second_sampler_name": settings["sampler_name"], "second_scheduler": settings["scheduler"],
    }


def preset_settings(value: Any, prompt_rules: PromptRuleStore | None = None, *,
                    force_model_profile: str | None = None, include: Any = None,
                    source: Any = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowError("风格预设 settings 必须是对象")
    unknown = set(value) - set(DEFAULT_SETTINGS)
    if unknown:
        raise WorkflowError(f"风格预设包含未知参数: {', '.join(sorted(unknown))}")
    flags = normalize_include(include)
    origin = normalize_source(source, value)
    candidate = {**_pick(value, CORE_KEYS), **origin, "generation_mode": "txt2img"}
    if force_model_profile is not None:
        profile_id = normalize_model_profile(force_model_profile)
        candidate.update(model_profile=profile_id,
                         **_pick(get_model_profile(profile_id), ("clip_name", "vae_name", "clip_type")))
    if "dual" in value:
        raw_dual = value["dual"]
        if raw_dual is not None and not isinstance(raw_dual, dict):
            raise WorkflowError("风格预设 dual 必须是对象或 null")
        candidate["dual"] = _pick(raw_dual, DUAL_KEYS) if raw_dual is not None else None
    if flags["size"]:
        candidate.update(_pick(value, ("width", "height")))
    if flags["repair"] and "upscalers" in value:
        candidate["upscalers"] = _repair(value["upscalers"])
    elif value.get("dual") is None:
        # Legacy dual:null borrows its middle resize from the repair block.
        latent = value.get("upscalers", {}).get("latent", {}) if isinstance(value.get("upscalers"), dict) else {}
        if not isinstance(latent, dict):
            raise WorkflowError("风格预设旧双采样放大参数无效")
        candidate["upscalers"] = {"latent": _pick(latent, ("scale_by", "upscale_method", "model_format"))}
    normalized = validate_settings(candidate)
    if prompt_rules is not None:
        normalized, _ = prompt_rules.normalize_settings(normalized, model_profile="anima")
        normalized = validate_settings(normalized)
    result = _pick(normalized, CORE_KEYS)
    result["dual"] = effective_dual(normalized)
    if normalized["base_mode"] == "dual_sampler" and result["dual"]["switch_step"] >= normalized["steps"]:
        raise WorkflowError("旧双采样第二轮起始步不小于总步数，请调整采样参数后保存")
    if flags["size"]:
        result.update(_pick(normalized, ("width", "height")))
    if flags["repair"]:
        result["upscalers"] = _repair(normalized["upscalers"])
    return result


def resolve_settings(item: dict[str, Any], current_settings: Any) -> dict[str, Any]:
    if not isinstance(current_settings, dict):
        raise WorkflowError("当前 settings 必须是对象")
    current = validate_settings(current_settings)
    if current["model_profile"] != item["model_profile"]:
        raise WorkflowError("不能跨模型档案应用风格预设")
    flags = normalize_include(item["include"])
    origin = normalize_source(item["source"])
    patch = preset_settings(item["settings"], include=flags, source=origin)
    if current["dual"] is None:
        current["dual"] = {**effective_dual(current), "half_precision": current["upscalers"]["latent"]["half_precision"]}
    target = deep_merge(current, patch)
    if flags["repair"]:
        target["pipeline_version"] = origin["pipeline_version"]
        target["upscalers"] = copy.deepcopy(patch["upscalers"])
        for stage, keys in REPAIR_DEVICE_KEYS.items():
            for key in keys:
                if key in current["upscalers"].get(stage, {}):
                    target["upscalers"].setdefault(stage, {})[key] = copy.deepcopy(current["upscalers"][stage][key])
        if "dlss" in current["upscalers"].get("nvidia", {}):
            target["upscalers"].setdefault("nvidia", {}).setdefault("dlss", {})["gpu_gen"] = current["upscalers"]["nvidia"]["dlss"]["gpu_gen"]
    target = validate_settings(target)
    change = ({"from": current["pipeline_version"], "to": target["pipeline_version"]}
              if target["pipeline_version"] != current["pipeline_version"] else None)
    return {"settings": target, "version_change": change}
