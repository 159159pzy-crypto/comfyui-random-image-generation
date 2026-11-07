"""Standalone Anima settings and deterministic submission contract."""

import copy
from pathlib import Path
import pytest
from anima_webui.resource_config import configure_resources
from anima_webui.workflow import (
    DEFAULT_SETTINGS,
    MAX_SAMPLE_SEED,
    WorkflowError,
    WorkflowTemplates,
    normalize_artist_tags,
    validate_loras,
    validate_settings,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def templates():
    return WorkflowTemplates.load(ROOT / "templates")


@pytest.mark.parametrize(
    "values",
    [
        {"model_profile": "krea2"},
        {"model_profile": "krea"},
        {"workbench": "single"},
        {"generation_mode": "img2img"},
        {"single": {}},
        {"krea_variance": {}},
        {"krea_prompt_pool": {}},
        {"pipeline_version": 2},
        {"pipeline_version": 3},
        {"pipeline_version": 5},
        {"hires": {}},
        {"detailers": {}},
        {"upscalers": {"latent": {"second_sampler": {}}}},
        {"upscalers": {"nvidia": {"dlss": {}}}},
    ],
)
def test_removed_modes_and_namespaces_are_rejected(values):
    with pytest.raises(WorkflowError):
        validate_settings(values)


def test_defaults_and_validation_are_independent_copies():
    first = validate_settings()
    assert first["pipeline_version"] == 4
    assert (first["model_profile"], first["workbench"], first["generation_mode"]) == (
        "anima",
        "random",
        "txt2img",
    )
    assert first["base_mode"] == "dual_sampler" and first["steps"] == 30
    assert validate_settings({"base_mode": "normal"})["steps"] == 20
    first["dual"]["scale"] = 1.9
    first["pools"]["character"]["ids"].append("a")
    assert DEFAULT_SETTINGS["dual"]["scale"] == 1
    assert validate_settings()["pools"]["character"]["ids"] == []
    assert validate_settings(first) == first


@pytest.mark.parametrize(
    "values",
    [
        {"width": 65},
        {"height": 0},
        {"count": 1001},
        {"count": True},
        {"cfg": float("nan")},
        {"base_denoise": float("inf")},
        {"female_count": 5, "male_count": 1},
        {"random_expression_count": 2},
        {"sampler_name": ""},
        {"steps": 0},
        {"random_pose": 1},
        {"dual": None},
        {"upscalers": {"seedvr2": {"encode": {"overlap": 512}}}},
    ],
)
def test_invalid_generation_parameters(values):
    with pytest.raises(WorkflowError):
        validate_settings(values)


def test_lora_normalization_and_duplicate_rejection():
    value = [
        {"filename": "folder\\style.safetensors", "strength": 0.75, "enabled": True}
    ]
    assert validate_loras({"loras": value})[0]["filename"] == "folder/style.safetensors"
    assert value[0]["filename"] == "folder\\style.safetensors"
    with pytest.raises(WorkflowError):
        validate_loras({"loras": [*value, *value]})
    assert normalize_artist_tags("@one, @one, two") == "@one, @two"


def test_submission_freezes_seeds_prompts_resources(templates):
    settings = {
        "base_mode": "normal",
        "clip_name": "encoders/qwen_3_06b_base.safetensors",
        "vae_name": "vae/qwen_image_vae.safetensors",
        "negative_prompt": "current negative",
    }
    source = copy.deepcopy(settings)
    result = templates.submission(
        settings,
        MAX_SAMPLE_SEED,
        2147483647,
        "AnimaRandomWebUI/test",
        "client",
        2,
        resolved_prompt="current positive",
        frozen_positive_prompt="old positive",
        frozen_negative_prompt="old negative",
        execution_nonce=123,
    )
    graph = result["prompt"]
    assert graph["1010:995"]["inputs"]["text"] == "old positive"
    assert graph["1010:1004"]["inputs"]["text"] == "old negative"
    assert graph["167:57"]["inputs"]["clip_name"] == settings["clip_name"]
    assert graph["167:58"]["inputs"]["vae_name"] == settings["vae_name"]
    metadata = result["extra_data"]["extra_pnginfo"]["anima_random_webui"]
    assert (
        metadata["sample_seed"] == MAX_SAMPLE_SEED
        and metadata["prompt_seed"] == 2147483647
    )
    assert metadata["sequence"] == 2 and metadata["settings"]["pipeline_version"] == 4
    assert settings == source and "single_runtime" not in metadata


def test_generic_resource_mapping(templates):
    mapping = {
        "anima-base-v1.0.safetensors": "anima/main.safetensors",
        "seedvr2_3b_int8_convrot.safetensors": "repair/seed.safetensors",
    }
    defaults = configure_resources(templates, mapping)
    assert defaults["model_name"] == "anima/main.safetensors"
    graph = templates.submission(
        {"upscalers": {"seedvr2": {"enabled": True}}}, 1, 2, "test", "client", 1
    )["prompt"]
    assert (
        graph["167:56"]["inputs"]["unet_name"] == mapping["anima-base-v1.0.safetensors"]
    )
    assert (
        graph["v3:seed:model"]["inputs"]["unet_name"]
        == mapping["seedvr2_3b_int8_convrot.safetensors"]
    )


def test_random_prompt_resolution_and_template_immutability(templates):
    original = copy.deepcopy(templates.api)
    with pytest.raises(WorkflowError, match="尚未解析"):
        templates.submission({"random_pose": True}, 0, 0, "test", "client", 1)
    templates.submission(
        {"random_pose": True}, 0, 0, "test", "client", 1, resolved_prompt="standing"
    )
    assert templates.api == original


@pytest.mark.parametrize(
    "seed,prompt_seed",
    [(-1, 0), (MAX_SAMPLE_SEED + 1, 0), (True, 0), (0, -1), (0, 2**31)],
)
def test_seed_boundaries(templates, seed, prompt_seed):
    with pytest.raises(WorkflowError):
        templates.submission({}, seed, prompt_seed, "test", "client", 1)


@pytest.mark.parametrize(
    "resources_resolved, expected", [(False, "third/"), (True, "resolved/")]
)
def test_resource_mapping_is_not_reapplied_to_resolved_settings(
    templates, resources_resolved, expected
):
    resources = {
        "model_name": "main.safetensors",
        "clip_name": "clip.safetensors",
        "vae_name": "vae.safetensors",
    }
    mapping = {"resolved/" + value: "third/" + value for value in resources.values()}
    mapping.update(
        {
            "resolved/pixel.pth": "third/pixel.pth",
            "resolved/seed.safetensors": "third/seed.safetensors",
            "resolved/seed_vae.safetensors": "third/seed_vae.safetensors",
        }
    )
    configure_resources(templates, mapping)
    settings = validate_settings(
        {
            **{key: "resolved/" + value for key, value in resources.items()},
            "upscalers": {
                "pixel_hires": {"enabled": True, "model_name": "resolved/pixel.pth"},
                "seedvr2": {
                    "enabled": True,
                    "model_name": "resolved/seed.safetensors",
                    "vae_name": "resolved/seed_vae.safetensors",
                },
            },
        }
    )
    original = copy.deepcopy(settings)
    result = templates.submission(
        settings, 1, 2, "test", "client", 1, resources_resolved=resources_resolved
    )
    graph = result["prompt"]
    for node_id, key, filename in (
        ("167:56", "unet_name", "main.safetensors"),
        ("167:57", "clip_name", "clip.safetensors"),
        ("167:58", "vae_name", "vae.safetensors"),
        ("v3:seed:model", "unet_name", "seed.safetensors"),
        ("v3:seed:vae", "vae_name", "seed_vae.safetensors"),
    ):
        assert graph[node_id]["inputs"][key] == expected + filename
    pixel = next(
        node for node in graph.values() if node["class_type"] == "UpscaleModelLoader"
    )
    assert pixel["inputs"]["model_name"] == expected + "pixel.pth"
    assert (
        result["extra_data"]["extra_pnginfo"]["anima_random_webui"]["settings"]
        == original
    )
    assert settings == original
