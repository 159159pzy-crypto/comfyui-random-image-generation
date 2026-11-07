"""Independent Anima base sampling and enabled-only live resource checks."""

from pathlib import Path
import pytest
from anima_webui.sampling import random_sampling_resource_issues
from anima_webui.workflow import WorkflowError, WorkflowTemplates, validate_settings

ROOT = Path(__file__).resolve().parents[1]


def test_dual_controls_reach_sampler_and_resize_nodes():
    settings = {
        "steps": 24,
        "cfg": 3,
        "dual": {
            "switch_step": 9,
            "scale": 1.7,
            "second_cfg": 5,
            "second_sampler_name": "heun",
            "second_scheduler": "beta",
        },
    }
    graph = WorkflowTemplates.load(ROOT / "templates").submission(
        settings, 123, 456, "test", "client", 1
    )["prompt"]
    first, second = (graph[key]["inputs"] for key in ("701:1245", "701:1246"))
    assert (
        first["end_at_step"]
        == second["end_at_step"]
        == first["steps"]
        == second["steps"]
        == 24
    )
    assert first["start_at_step"] == 0 and second["start_at_step"] == 9
    assert (
        first["cfg"],
        second["cfg"],
        second["sampler_name"],
        second["scheduler"],
    ) == (3, 5, "heun", "beta")
    assert graph["701:1450"]["inputs"]["scale"] == 1.7
    assert (
        first["return_with_leftover_noise"] == "enable"
        and second["return_with_leftover_noise"] == "disable"
    )


def test_normal_mode_keeps_hidden_dual_controls_without_resource_checks():
    settings = validate_settings(
        {
            "base_mode": "normal",
            "steps": 8,
            "dual": {"switch_step": 20, "scale": 7, "second_sampler_name": "missing"},
        }
    )
    assert not random_sampling_resource_issues(settings, {"node_types": []})
    with pytest.raises(WorkflowError, match="起始步"):
        validate_settings({**settings, "base_mode": "dual_sampler"})


@pytest.mark.parametrize(
    "dual",
    [
        False,
        [],
        {"unknown": 1},
        {"switch_step": True},
        {"scale": float("nan")},
        {"scale": 2.1},
        {"second_cfg": 31},
        {"upscale_method": "bad"},
        {"second_sampler_name": ""},
    ],
)
def test_invalid_dual_parameters(dual):
    with pytest.raises(WorkflowError):
        validate_settings({"dual": dual})


def test_dual_and_optional_latent_resize_are_independent():
    settings = {
        "dual": {"upscale_method": "bicubic", "scale": 2.5},
        "upscalers": {
            "latent": {
                "enabled": True,
                "scale_by": 1.6,
                "model_format": "Flux",
                "half_precision": False,
            }
        },
    }
    graph = WorkflowTemplates.load(ROOT / "templates").submission(
        settings, 1, 2, "test", "c", 1
    )["prompt"]
    assert graph["701:1450"]["inputs"]["scale_by"] == 2.5
    assert graph["v3:latent"]["inputs"]["scale"] == 1.6
    assert graph["v3:latent"]["inputs"]["model_format"] == "Flux"


def test_live_node_ranges_and_options():
    settings = validate_settings()
    resources = {
        "node_types": ["KSamplerAdvanced", "SesquiLatentUpscale"],
        "samplers": ["euler"],
        "schedulers": ["simple"],
        "node_inputs": {
            "SesquiLatentUpscale": {
                "scale": ["FLOAT", {"min": 1.5, "max": 2}],
                "model_format": [["Flux"]],
            }
        },
    }
    assert {
        issue["name"] for issue in random_sampling_resource_issues(settings, resources)
    } == {"scale=1.0", "model_format=Wan 2.1"}
