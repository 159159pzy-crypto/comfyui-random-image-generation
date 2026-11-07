"""Every v4 repair combination, editable export, and replay execution identity."""

import itertools
from pathlib import Path
import pytest
from anima_webui.pipeline_v3 import (
    STAGE_ORDER_V4,
    needs_main_encode,
    required_node_classes,
)
from anima_webui.workflow import WorkflowTemplates, validate_settings

ROOT = Path(__file__).resolve().parents[1]
STAGES = (
    "pixel_hires",
    "latent",
    "post_sampler",
    "face_refine",
    "seedvr2",
    "nvidia",
    "dlss",
)


@pytest.fixture(scope="module")
def templates():
    return WorkflowTemplates.load(ROOT / "templates")


def submit(templates, enabled=(), base="normal", **overrides):
    settings = {
        "base_mode": base,
        "upscalers": {stage: {"enabled": stage in enabled} for stage in STAGES},
        **overrides,
    }
    return templates.submission(
        settings,
        12345,
        67890,
        "AnimaRandomWebUI/test",
        "client",
        1,
        execution_nonce=654321,
    )


def refs(node):
    return [
        (name, value)
        for name, value in node["inputs"].items()
        if isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], str)
        and type(value[1]) is int
    ]


@pytest.mark.parametrize("base", ["normal", "dual_sampler"])
@pytest.mark.parametrize(
    "enabled",
    [
        tuple(stage for stage, flag in zip(STAGES, flags) if flag)
        for flags in itertools.product((False, True), repeat=len(STAGES))
    ],
)
def test_every_repair_combination_has_one_connected_sink_and_ordered_stages(
    templates, base, enabled
):
    result = submit(templates, enabled, base)
    graph = result["prompt"]
    visited = set()
    active = set()

    def visit(key):
        assert key in graph and key not in active
        if key in visited:
            return
        active.add(key)
        for _, (source, _) in refs(graph[key]):
            visit(source)
            assert STAGE_ORDER_V4.index(
                graph[source]["_meta"]["pipeline_stage"]
            ) <= STAGE_ORDER_V4.index(graph[key]["_meta"]["pipeline_stage"])
        active.remove(key)
        visited.add(key)

    visit("v2:output")
    assert visited == graph.keys()
    assert sum(node["class_type"] == "SaveImage" for node in graph.values()) == 1
    assert all(
        node["class_type"]
        not in {"KreaSeedVarianceEnhancer", "LoadImage", "AaaliceImagePicker"}
        for node in graph.values()
    )
    assert ("v4:latent:sample" in graph) == ("latent" in enabled)
    if "latent" in enabled:
        assert graph["v4:latent:sample"]["inputs"]["latent_image"] == ["v3:latent", 0]
        if "post_sampler" in enabled:
            assert graph["v3:post"]["inputs"]["latent_image"] == ["v4:latent:sample", 0]
    meta = result["extra_data"]["extra_pnginfo"]["workflow"]["extra"]["pipeline"]
    assert meta["version"] == 4 and meta["stages"] == ["base", *enabled, "export"]
    assert meta["execution_nonce"] == 654321 and meta["api_prompt"] == graph
    assert graph["v2:output"]["inputs"]["filename_prefix"] == "AnimaRandomWebUI/test"


@pytest.mark.parametrize("base", ["normal", "dual_sampler"])
def test_latent_and_post_sampling_inherit_final_base_resources(templates, base):
    result = submit(
        templates,
        STAGES,
        base,
        loras=[{"filename": "style.safetensors", "strength": 0.5, "enabled": True}],
    )
    graph = result["prompt"]
    final = graph["701:1246" if base == "dual_sampler" else "691:1240"]["inputs"]
    for key in ("v4:latent:sample", "v3:post", "v3:face"):
        assert graph[key]["inputs"]["seed"] == 12345
        for field in ("model", "positive", "negative"):
            assert graph[key]["inputs"][field] == final[field]
    assert graph["72"]["inputs"]["loras"]["__value__"] == [
        {
            "active": True,
            "name": "style.safetensors",
            "strength": 0.5,
            "clipStrength": 0.5,
        }
    ]
    assert graph["v3:seed:resize"]["inputs"]["image"] == ["v3:face", 0]
    assert graph["v3:vsr"]["inputs"]["images"] == ["v3:seed:finish", 0]
    assert graph["v3:dlss"]["inputs"]["images"] == ["v3:vsr", 0]
    ui = result["extra_data"]["extra_pnginfo"]["workflow"]
    nodes = {node["properties"]["pipeline_api_id"]: node for node in ui["nodes"]}
    assert nodes["v4:latent:sample"]["widgets_values"] == [
        12345,
        "fixed",
        16,
        4,
        "er_sde",
        "simple",
        0.2,
    ]
    assert (
        nodes["v3:face"]["pos"][0]
        < nodes["v3:seed:resize"]["pos"][0]
        < nodes["v3:vsr"]["pos"][0]
    )
    assert {link[1] for link in ui["links"]}.issubset(
        {node["id"] for node in ui["nodes"]}
    )


@pytest.mark.parametrize(
    "enabled,encodes,decodes",
    [
        ((), 0, 1),
        (("latent",), 0, 1),
        (("latent", "post_sampler"), 0, 1),
        (("post_sampler", "seedvr2"), 0, 1),
        (("pixel_hires", "latent"), 1, 2),
        (STAGES, 1, 2),
    ],
)
def test_main_vae_crossings_are_minimal(templates, enabled, encodes, decodes):
    graph = submit(templates, enabled)["prompt"]
    main = [node for key, node in graph.items() if not key.startswith("v3:seed:")]
    assert sum(node["class_type"].startswith("VAEEncode") for node in main) == encodes
    assert sum(node["class_type"].startswith("VAEDecode") for node in main) == decodes
    settings = validate_settings(
        {"upscalers": {stage: {"enabled": stage in enabled} for stage in STAGES}}
    )
    assert needs_main_encode(settings) == bool(encodes)


def test_pixel_resize_is_bypassed_only_when_post_sampling_enabled(templates):
    first = submit(templates, ("pixel_hires", "latent"))["prompt"]
    second = submit(templates, ("pixel_hires", "latent", "post_sampler"))["prompt"]
    assert "v3:pixel:resize" in first and "v3:pixel:resize" not in second
    assert first["v3:pixel:resize"]["inputs"]["scale_by"] == 0.5


@pytest.mark.parametrize("base", ["normal", "dual_sampler"])
def test_execution_nonce_invalidates_generation_without_changing_seed_resources(
    templates, base
):
    settings = {
        "base_mode": base,
        "upscalers": {stage: {"enabled": True} for stage in STAGES},
    }
    a = templates.submission(settings, 999, 321, "test", "c", 1, execution_nonce=17)[
        "prompt"
    ]
    b = templates.submission(settings, 999, 321, "test", "c", 1, execution_nonce=18)[
        "prompt"
    ]
    assert [key for key in a if a[key] != b[key]] == ["v3:run:nonce"]

    def ancestors(key):
        return {key}.union(*(ancestors(source) for _, (source, _) in refs(a[key])))

    assert "v3:run:nonce" in ancestors("v2:output")
    for key, node in a.items():
        if node["class_type"] in {
            "UNETLoader",
            "CLIPLoader",
            "VAELoader",
            "CLIPTextEncode",
            "Lora Loader (LoraManager)",
        }:
            assert "v3:run:nonce" not in ancestors(key)
    assert a["v3:run:seed"]["inputs"]["value"] == 999
    for key in ("v4:latent:sample", "v3:post", "v3:seed:sample", "v3:face"):
        assert a[key]["inputs"]["seed"] == 999


def test_disabled_optional_stages_have_no_requirements():
    settings = validate_settings({"base_mode": "normal"})
    assert required_node_classes(settings) == {"PrimitiveInt", "ComfyMathExpression"}
    settings["upscalers"]["dlss"]["enabled"] = True
    assert "DLSS5NeuralRender" in required_node_classes(settings)
    assert "RTXVideoSuperResolution" not in required_node_classes(settings)


def test_latent_and_post_sampler_parameters_stay_independent(templates):
    graph = submit(
        templates,
        upscalers={
            "latent": {
                "enabled": True,
                "sampler": {"steps": 13, "cfg": 2.5, "denoise": 0.4},
            },
            "post_sampler": {"enabled": True, "steps": 7, "cfg": 3, "denoise": 0.15},
        },
    )["prompt"]
    assert (
        graph["v4:latent:sample"]["inputs"]["steps"],
        graph["v4:latent:sample"]["inputs"]["cfg"],
    ) == (13, 2.5)
    assert (graph["v3:post"]["inputs"]["steps"], graph["v3:post"]["inputs"]["cfg"]) == (
        7,
        3,
    )
