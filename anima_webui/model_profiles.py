"""Anima resource defaults and public model contract."""

from __future__ import annotations
import copy
from typing import Any, Mapping


class ModelProfileError(ValueError):
    """Raised when a model profile identifier is not known."""


MODEL_PROFILE_VERSION = 1
DEFAULT_MODEL_PROFILE = "anima"
MODEL_PROFILE_IDS = ("anima",)
MODEL_PROFILES: dict[str, dict[str, Any]] = {
    "anima": {
        "id": "anima",
        "label": "Anima",
        "model_name": "anima-base-v1.0.safetensors",
        "clip_name": "qwen_3_06b_base.safetensors",
        "vae_name": "qwen_image_vae.safetensors",
        "clip_type": "stable_diffusion",
        "prompt_mode": "tag_or_natural",
        "defaults": {
            "steps": 30,
            "cfg": 8.0,
            "sampler_name": "euler",
            "scheduler": "simple",
            "base_mode": "dual_sampler",
        },
        "seedvr2_model": "seedvr2_3b_int8_convrot.safetensors",
        "seedvr2_vae": "ema_vae_fp16.safetensors",
        "pixel_hires_model": "4x_foolhardy_Remacri.pth",
        "post_sampler_defaults": {
            "enabled": False,
            "steps": 16,
            "cfg": 4.0,
            "denoise": 0.2,
            "sampler_name": "er_sde",
            "scheduler": "simple",
        },
        "latent_sampler_defaults": {
            "steps": 16,
            "cfg": 4.0,
            "denoise": 0.2,
            "sampler_name": "er_sde",
            "scheduler": "simple",
        },
        "lora_namespace": "anima",
        "pool_namespace": "anima",
        "style_namespace": "anima",
        "legacy_model_names": (
            "miaomiaoHarem_anima14.safetensors",
            "silvermoonmixAnima_v2329BTurbo.safetensors",
            "silvermoonmixAnima_v2329B.safetensors",
        ),
    }
}
_ALIASES = {
    "anima": "anima",
    "anima1": "anima",
    "anima_v1": "anima",
    "anima-base": "anima",
}


def normalize_model_profile(value: Any) -> str:
    """Return a canonical profile id or raise :class:`ModelProfileError`."""
    if not isinstance(value, str):
        raise ModelProfileError("model_profile 必须是字符串")
    key = value.strip().casefold()
    profile_id = _ALIASES.get(key)
    if profile_id is None:
        raise ModelProfileError(
            f"model_profile 无效，可选值: {', '.join(MODEL_PROFILE_IDS)}"
        )
    return profile_id


def get_model_profile(value: Any = DEFAULT_MODEL_PROFILE) -> dict[str, Any]:
    """Return a defensive copy of a profile definition."""
    profile_id = normalize_model_profile(value)
    return copy.deepcopy(MODEL_PROFILES[profile_id])


def model_profile_defaults(value: Any = DEFAULT_MODEL_PROFILE) -> dict[str, Any]:
    """Return settings controlled by a profile.

    ``model_profile`` and the bound resource names are included so callers can
    merge this result into user settings without accidentally retaining the
    previous model's CLIP or VAE.
    """
    profile = get_model_profile(value)
    defaults = copy.deepcopy(profile["defaults"])
    defaults.update(
        {
            "model_profile": profile["id"],
            "model_name": profile["model_name"],
            "clip_name": profile["clip_name"],
            "vae_name": profile["vae_name"],
            "clip_type": profile["clip_type"],
        }
    )
    return defaults


def profile_resource_names(value: Any = DEFAULT_MODEL_PROFILE) -> dict[str, str]:
    profile = get_model_profile(value)
    return {
        "model_name": profile["model_name"],
        "clip_name": profile["clip_name"],
        "vae_name": profile["vae_name"],
        "seedvr2_model": profile["seedvr2_model"],
        "seedvr2_vae": profile["seedvr2_vae"],
    }


def profile_public(
    value: Any = DEFAULT_MODEL_PROFILE, *, available: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Build the stable JSON shape returned by ``GET /api/resources``.

    ``available`` may contain inventories from ComfyUI (``models``, ``clips``,
    ``vaes``, ``upscale_models`` and ``nodes``).  Unknown/missing inventories
    are reported as ``None`` rather than pretending a resource is installed.
    """
    profile = get_model_profile(value)
    inventory = dict(available or {})

    def _contains(kind: str, filename: str) -> bool | None:
        values = inventory.get(kind)
        if values is None:
            return None
        try:
            wanted = str(filename).replace("\\", "/").casefold()
            wanted_base = wanted.rsplit("/", 1)[-1]
            available = {str(item).replace("\\", "/").casefold() for item in values}
            return wanted in available or wanted_base in {
                item.rsplit("/", 1)[-1] for item in available
            }
        except TypeError:
            return None

    resource_map = {
        "model": ("model_name", profile["model_name"]),
        "clip": ("clip_name", profile["clip_name"]),
        "vae": ("vae_name", profile["vae_name"]),
        "seedvr2": ("seedvr2_model", profile["seedvr2_model"]),
    }
    if "seedvr2_vaes" in inventory:
        resource_map["seedvr2_vae"] = ("seedvr2_vae", profile["seedvr2_vae"])
    aliases = {
        "clip": ("clips", "clip_models"),
        "vae": ("vaes", "vae_models"),
        "seedvr2_vae": ("seedvr2_vaes", "vaes", "vae_models"),
    }
    availability: dict[str, bool | None] = {}
    missing: list[str] = []
    for key, (field, filename) in resource_map.items():
        kinds = aliases.get(
            key, ("seedvr2_models", "models") if key == "seedvr2" else ("models",)
        )
        value_available: bool | None = None
        for kind in kinds:
            value_available = _contains(kind, filename)
            if value_available is not None:
                break
        availability[key] = value_available
        if value_available is False:
            missing.append(filename)

    def _component_status(category: str, names: tuple[str, ...]) -> bool | None:
        values = inventory.get(category)
        if values is None:
            return None
        expected = {name.casefold() for name in names}
        if isinstance(values, Mapping):
            normalized = {
                str(key).casefold(): bool(value) for key, value in values.items()
            }
            matches = [normalized[name] for name in expected if name in normalized]
            return any(matches) if matches else False
        if isinstance(values, (str, bytes)):
            values = [values]
        try:
            installed = {str(value).casefold() for value in values}
        except TypeError:
            return None
        return bool(expected & installed)

    node_status = {
        "unet_loader": _component_status("nodes", ("UNETLoader",)),
        "clip_loader": _component_status("nodes", ("CLIPLoader",)),
        "vae_loader": _component_status("nodes", ("VAELoader",)),
        "seedvr2": _component_status(
            "nodes",
            (
                "SeedVR2",
                "SeedVR2Conditioning",
                "SeedVR2Preprocess",
                "SeedVR2PostProcessing",
            ),
        ),
        "nvidia_upscale": _component_status(
            "nodes", ("NvidiaUpscale", "RTXVideoSuperResolution")
        ),
        "face_refine": _component_status(
            "nodes", ("ForbiddenVisionFaceProcessorIntegrated",)
        ),
    }
    plugin_status = {
        name: _component_status("plugins", (name,))
        for name in ("seedvr2", "nvidia_upscale", "forbidden_vision_face")
    }
    result = {
        "id": profile["id"],
        "label": profile["label"],
        "model_name": profile["model_name"],
        "clip_name": profile["clip_name"],
        "vae_name": profile["vae_name"],
        "clip_type": profile["clip_type"],
        "prompt_mode": profile["prompt_mode"],
        "defaults": copy.deepcopy(profile["defaults"]),
        "latent_sampler_defaults": copy.deepcopy(profile["latent_sampler_defaults"]),
        "seedvr2_model": profile["seedvr2_model"],
        "seedvr2_vae": profile["seedvr2_vae"],
        "namespaces": {
            "lora": profile["lora_namespace"],
            "pool": profile["pool_namespace"],
            "style": profile["style_namespace"],
        },
        "available": availability,
        "missing": missing,
        "nodes": node_status,
        "plugins": plugin_status,
    }
    return result


def profile_ids() -> tuple[str, ...]:
    return MODEL_PROFILE_IDS
