"""The single supported Anima profile and resource availability reports."""

import pytest
from anima_webui.model_profiles import (
    MODEL_PROFILE_IDS,
    ModelProfileError,
    get_model_profile,
    normalize_model_profile,
    profile_public,
)


def test_only_anima_is_supported():
    assert MODEL_PROFILE_IDS == ("anima",)
    for value in ("Anima", "anima1", "anima_v1"):
        assert normalize_model_profile(value) == "anima"
    for value in ("krea", "krea2", "Krea 2", "unknown", None):
        with pytest.raises(ModelProfileError):
            normalize_model_profile(value)


def test_profile_defaults_and_copies():
    profile = get_model_profile()
    assert profile["model_name"] == "anima-base-v1.0.safetensors"
    assert profile["clip_name"] == "qwen_3_06b_base.safetensors"
    profile["defaults"]["steps"] = 1
    assert get_model_profile()["defaults"]["steps"] == 30
    assert profile_public()["latent_sampler_defaults"] == {
        "steps": 16,
        "cfg": 4,
        "denoise": 0.2,
        "sampler_name": "er_sde",
        "scheduler": "simple",
    }


def test_availability_does_not_fabricate_installed_resources():
    assert profile_public()["available"]["model"] is None
    summary = profile_public(
        available={
            "models": ["Anima/anima-base-v1.0.safetensors"],
            "clips": [],
            "vaes": [],
        }
    )
    assert (
        summary["available"]["model"] is True and summary["available"]["clip"] is False
    )
    assert "qwen_3_06b_base.safetensors" in summary["missing"]
