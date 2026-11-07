"""Anima sampling, resize and live node parameter validation."""

from __future__ import annotations

import copy
import math
from typing import Any


LATENT_METHODS = {"sesqui", "nearest-exact", "bilinear", "area", "bicubic", "bislerp"}
LATENT_FORMATS = {"SDXL", "Flux", "Flux2", "Ideogram 4", "Wan 2.1"}
LATENT_OPTIONS = {
    "upscale_method": "sesqui",
    "model_format": "Wan 2.1",
    "half_precision": True,
}
DUAL_DEFAULTS = {
    "switch_step": 15,
    "scale": 1.0,
    "second_cfg": 8.0,
    "second_sampler_name": "euler",
    "second_scheduler": "simple",
    "upscale_method": "sesqui",
    "model_format": "Wan 2.1",
    "half_precision": True,
}


DLSS_DEFAULTS = {
    "style": "电影",
    "intensity": 1.0,
    "local_tone": 1.0,
    "local_struct": 1.0,
    "output_view": "处理",
    "output_mix": 1.0,
    "gpu_gen": "Auto",
}


def validate_dlss(value: Any, path: str = "upscalers.nvidia.dlss") -> dict[str, Any]:
    from .workflow import WorkflowError, _boolean

    if not isinstance(value, dict) or set(value) - (set(DLSS_DEFAULTS) | {"enabled"}):
        raise WorkflowError(f"{path} 包含未知参数或不是对象")
    result = {**DLSS_DEFAULTS, **copy.deepcopy(value)}
    # Old standalone toggles are accepted at import only. N-card owns execution.
    if "enabled" in result:
        _boolean(f"{path}.enabled", result.pop("enabled"))
    for key, allowed in (
        ("style", {"默认", "自然", "电影", "风格3"}),
        ("output_view", {"处理", "差异×10", "左右对比"}),
        ("gpu_gen", {"Auto", "RTX 30", "RTX 40", "RTX 50"}),
    ):
        if not isinstance(result[key], str) or result[key] not in allowed:
            raise WorkflowError(f"{path}.{key} 无效")
    for key in ("intensity", "local_tone", "local_struct", "output_mix"):
        value = result[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 <= value <= 1
        ):
            raise WorkflowError(f"{path}.{key} 必须在 0-1 之间")
        result[key] = float(value)
    return result


def validate_resize_options(value: dict[str, Any], path: str) -> dict[str, Any]:
    from .workflow import WorkflowError, _boolean

    options = {key: value.get(key, default) for key, default in LATENT_OPTIONS.items()}
    for key, choices in (
        ("upscale_method", LATENT_METHODS),
        ("model_format", LATENT_FORMATS),
    ):
        if not isinstance(options[key], str) or options[key] not in choices:
            raise WorkflowError(f"{path}.{key} 无效")
    options["half_precision"] = _boolean(
        f"{path}.half_precision", options["half_precision"]
    )
    return options


def validate_resize_scale(
    value: Any,
    method: str,
    path: str,
    *,
    minimum: float = 0.01,
    maximum: float = 64,
    active: bool = True,
) -> float:
    from .workflow import WorkflowError

    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise WorkflowError(f"{path} 必须是有限数字")
    if not minimum <= value <= maximum:
        raise WorkflowError(f"{path} 必须在 {minimum:g}-{maximum:g} 之间")
    if active and method == "sesqui" and not 1 <= value <= 2:
        raise WorkflowError(f"{path} 使用 SesquiLSR 时必须在 1-2 之间")
    return float(value)


def validate_random_dual(value: Any, settings: dict[str, Any]) -> dict[str, Any]:
    """Validate the independent controls of the two base sampling stages."""
    from .workflow import WorkflowError, _integer, _text

    if not isinstance(value, dict):
        raise WorkflowError("dual 必须是对象")
    if set(value) - set(DUAL_DEFAULTS):
        raise WorkflowError("未知 dual 参数")
    result = {**DUAL_DEFAULTS, **copy.deepcopy(value)}
    active = settings.get("base_mode") == "dual_sampler"
    result["switch_step"] = _integer("dual.switch_step", result["switch_step"], 0, 149)
    if active and result["switch_step"] >= settings["steps"]:
        raise WorkflowError("第二采样起始步必须小于总步数")
    result.update(validate_resize_options(result, "dual"))
    result["scale"] = validate_resize_scale(
        result["scale"],
        result["upscale_method"],
        "dual.scale",
        minimum=1,
        maximum=8,
        active=active,
    )
    cfg = result["second_cfg"]
    if (
        isinstance(cfg, bool)
        or not isinstance(cfg, (int, float))
        or not math.isfinite(cfg)
        or not 0 <= cfg <= 30
    ):
        raise WorkflowError("dual.second_cfg 必须在 0-30 之间")
    result["second_cfg"] = float(cfg)
    for key in ("second_sampler_name", "second_scheduler"):
        result[key] = _text(f"dual.{key}", result[key])
        if not result[key]:
            raise WorkflowError(f"dual.{key} 不能为空")
    return result


def latent_upscale_node(
    source: list[Any], config: dict[str, Any], scale: float
) -> dict[str, Any]:
    if config["upscale_method"] == "sesqui":
        return {
            "class_type": "SesquiLatentUpscale",
            "inputs": {
                "latent": list(source),
                "scale": scale,
                "model_format": config.get(
                    "model_format", LATENT_OPTIONS["model_format"]
                ),
                "half_precision": config.get(
                    "half_precision", LATENT_OPTIONS["half_precision"]
                ),
            },
        }
    return {
        "class_type": "LatentUpscaleBy",
        "inputs": {
            "samples": list(source),
            "scale_by": scale,
            "upscale_method": config["upscale_method"],
        },
    }


def node_input_issues(
    resources: dict[str, Any],
    node_type: str,
    values: dict[str, Any],
    label: str,
    *,
    issue_type: str = "sampling_parameter",
) -> list[dict[str, str]]:
    """Validate actual node inputs, including Comfy dynamic combo branches."""
    schema = (resources.get("node_inputs") or {}).get(node_type)
    if not isinstance(schema, dict):
        return []
    issues: list[dict[str, str]] = []

    def check_value(key: str, spec: Any, prefix: str = "") -> None:
        field = prefix + key
        if field not in values or not isinstance(spec, (list, tuple)) or not spec:
            return
        value, kind = values[field], spec[0]
        options = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
        choices = kind if isinstance(kind, list) else options.get("options")
        reason = ""
        if isinstance(choices, list):
            if choices and all(
                isinstance(item, dict) and "key" in item for item in choices
            ):
                branch = next((item for item in choices if item["key"] == value), None)
                if branch is None:
                    reason = "选项已不可用"
                else:
                    for child, child_spec in (
                        branch.get("inputs", {}).get("required", {}).items()
                    ):
                        child_field = field + "." + child
                        if child_field not in values:
                            issues.append(
                                {
                                    "type": issue_type,
                                    "name": child_field,
                                    "label": f"{label} 缺少当前模式必需参数",
                                }
                            )
                        else:
                            check_value(child, child_spec, field + ".")
            elif value not in choices:
                reason = "选项已不可用"
        elif (
            kind in ("INT", "FLOAT")
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        ):
            minimum, maximum = options.get("min"), options.get("max")
            if (
                minimum is not None
                and value < minimum
                or maximum is not None
                and value > maximum
            ):
                reason = f"超出当前节点范围 {minimum if minimum is not None else '-∞'}–{maximum if maximum is not None else '∞'}"
        if reason:
            issues.append(
                {
                    "type": issue_type,
                    "name": f"{field}={value}",
                    "label": f"{label} {reason}",
                }
            )

    for key, spec in schema.items():
        check_value(key, spec)
    return issues


def enabled_postprocess_parameter_issues(
    settings: dict[str, Any], resources: dict[str, Any]
) -> list[dict[str, str]]:
    """Check only enabled repair stages against the actual ComfyUI schemas."""
    # Older clients advertise only node/plugin availability and may pass
    # partial stage dictionaries; without schemas there is nothing to check.
    stages = settings["upscalers"]
    issues: list[dict[str, str]] = []
    issue_type = "sampling_parameter"
    # Older callers expose plugin availability and partial settings only.
    if (
        not isinstance(resources.get("node_inputs"), dict)
        or not resources["node_inputs"]
    ):
        return issues

    def check(node_type: str, values: dict[str, Any], label: str) -> None:
        issues.extend(
            node_input_issues(
                resources, node_type, values, label, issue_type=issue_type
            )
        )

    if stages.get("pixel_hires", {}).get("enabled"):
        pixel = stages["pixel_hires"]
        check("UpscaleModelLoader", {"model_name": pixel["model_name"]}, "像素高清模型")
        if not stages.get("post_sampler", {}).get("enabled"):
            check(
                "ImageScaleBy",
                {
                    "upscale_method": pixel["upscale_method"],
                    "scale_by": pixel["percent"] / 100,
                },
                "像素高清缩图",
            )
    if stages.get("post_sampler", {}).get("enabled"):
        check("KSampler", stages["post_sampler"], "整图后采样")
    if stages["latent"]["enabled"]:
        check("KSampler", stages["latent"]["sampler"], "潜空间重采样")
    if stages["seedvr2"]["enabled"]:
        seed = stages["seedvr2"]
        check("UNETLoader", {"weight_dtype": seed["weight_dtype"]}, "SeedVR2 模型精度")
        check("KSampler", seed["sampler"], "SeedVR2 采样")
        check("VAEEncodeTiled", seed["encode"], "SeedVR2 VAE 编码")
        check("VAEDecodeTiled", seed["decode"], "SeedVR2 VAE 解码")
        check(
            "SeedVR2PostProcessing",
            {"color_correction_method": seed["color_correction_method"]},
            "SeedVR2 色彩校正",
        )
    if stages["nvidia"]["enabled"]:
        nvidia = stages["nvidia"]
        check(
            "RTXVideoSuperResolution",
            {
                "resize_type": nvidia["resize_type"],
                "resize_type.scale": nvidia["scale"],
                "quality": nvidia["quality"],
            },
            "NVIDIA 放大",
        )
    if stages.get("dlss", {}).get("enabled"):
        check("DLSS5NeuralRender", stages["dlss"], "DLSS")
    if stages["face_refine"]["enabled"]:
        check(
            "ForbiddenVisionFaceProcessorIntegrated", stages["face_refine"], "脸部细化"
        )
    return issues


def random_sampling_resource_issues(
    settings: dict[str, Any], resources: dict[str, Any]
) -> list[dict[str, str]]:
    if settings.get("workbench") != "random":
        return []
    issues: list[dict[str, str]] = []
    advertised = resources.get("node_types")
    node_types = set(advertised) if isinstance(advertised, list) else None

    def node_inputs(node_type: str, values: dict[str, Any], label: str) -> None:
        if node_types is not None and node_type not in node_types:
            issues.append(
                {"type": "sampling_node", "name": node_type, "label": f"{label} 节点"}
            )
        issues.extend(node_input_issues(resources, node_type, values, label))

    if settings["base_mode"] == "normal":
        issues.extend(
            node_input_issues(
                resources,
                "KSampler",
                {"denoise": settings.get("base_denoise", 1.0)},
                "底图采样",
            )
        )
    latent = settings["upscalers"]["latent"]
    dual = settings.get("dual")
    if settings["base_mode"] == "dual_sampler":
        for key, inventory, label in (
            ("second_sampler_name", "samplers", "第二阶段采样器"),
            ("second_scheduler", "schedulers", "第二阶段调度器"),
        ):
            if (
                resources.get(inventory) is not None
                and dual[key] not in resources[inventory]
            ):
                issues.append(
                    {
                        "type": "dual_sampler"
                        if inventory == "samplers"
                        else "dual_scheduler",
                        "name": dual[key],
                        "label": label,
                    }
                )
        node_inputs(
            "KSamplerAdvanced",
            {
                "steps": settings["steps"],
                "cfg": dual["second_cfg"],
                "sampler_name": dual["second_sampler_name"],
                "scheduler": dual["second_scheduler"],
            },
            "第二阶段采样",
        )
    resizes = []
    if settings["base_mode"] == "dual_sampler":
        resizes.append((dual, dual["scale"], "双采样中间放大"))
    if latent["enabled"]:
        resizes.append((latent, latent["scale_by"], "潜空间放大"))
    for config, scale, label in resizes:
        resize = latent_upscale_node([], config, scale)
        node_inputs(resize["class_type"], resize["inputs"], label)
    issues.extend(enabled_postprocess_parameter_issues(settings, resources))
    return issues
