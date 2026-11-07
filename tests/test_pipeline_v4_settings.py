"""Exact v4 parameter boundaries and independent repair controls."""

import pytest
from anima_webui.workflow import (
    WorkflowError,
    upgrade_pipeline_settings,
    validate_settings,
)
from anima_webui.sampling import enabled_postprocess_parameter_issues


def test_v4_roundtrip_and_removed_version_rejection():
    settings = validate_settings(
        {
            "upscalers": {
                "latent": {"sampler": {"steps": 9, "cfg": 3}},
                "post_sampler": {"steps": 6},
            }
        }
    )
    assert settings["upscalers"]["latent"]["sampler"]["steps"] == 9
    assert settings["upscalers"]["post_sampler"]["steps"] == 6
    assert upgrade_pipeline_settings(settings) == settings
    for version in (1, 2, 3, 5):
        with pytest.raises(WorkflowError):
            upgrade_pipeline_settings({"pipeline_version": version})


@pytest.mark.parametrize(
    "sampler",
    [
        None,
        False,
        {"enabled": False},
        {"seed": 1},
        {"cfg": float("nan")},
        {"steps": True},
        {"sampler_name": ""},
    ],
)
def test_latent_sampler_has_five_parameters_and_no_toggle(sampler):
    with pytest.raises(WorkflowError, match="latent.sampler"):
        validate_settings({"upscalers": {"latent": {"sampler": sampler}}})


@pytest.mark.parametrize(
    "method,accepted,rejected", [("bicubic", 8, 8.01), ("sesqui", 2, 2.01)]
)
def test_latent_native_range(method, accepted, rejected):
    def settings(scale):
        return {
            "base_mode": "normal",
            "upscalers": {
                "latent": {"enabled": True, "upscale_method": method, "scale_by": scale}
            },
        }

    assert (
        validate_settings(settings(accepted))["upscalers"]["latent"]["scale_by"]
        == accepted
    )
    with pytest.raises(WorkflowError, match="scale_by"):
        validate_settings(settings(rejected))


def test_disabled_post_sampler_does_not_require_hidden_options():
    settings = validate_settings({"upscalers": {"post_sampler": {"denoise": 0.8}}})
    schema = {"node_inputs": {"KSampler": {"denoise": ["FLOAT", {"max": 0.5}]}}}
    assert enabled_postprocess_parameter_issues(settings, schema) == []
    settings["upscalers"]["post_sampler"]["enabled"] = True
    assert len(enabled_postprocess_parameter_issues(settings, schema)) == 1
