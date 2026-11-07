"""Shared, typed image/latent repair chain and faithful editable UI export.

The Anima base renderer prepares the two sampling modes. This
module starts at their final LATENT and only crosses a VAE boundary when the
next enabled operation actually requires the other representation.
"""

from __future__ import annotations

import copy
from typing import Any

from .sampling import latent_upscale_node


STAGE_LABELS = {
    "base": "底图",
    "pixel_hires": "像素高清",
    "nvidia": "N 卡 VSR",
    "seedvr2": "SeedVR2",
    "latent": "潜空间放大",
    "post_sampler": "整图后采样",
    "face_refine": "脸部细化",
    "dlss": "DLSS",
    "export": "导出保存",
}
STAGE_ORDER_V4 = (
    "base",
    "pixel_hires",
    "latent",
    "post_sampler",
    "face_refine",
    "seedvr2",
    "nvidia",
    "dlss",
    "export",
)
MAX_EXECUTION_NONCE = 2**53 - 1
_TILING = {
    "tiled": True,
    "tile_size": 512,
    "overlap": 64,
    "temporal_size": 64,
    "temporal_overlap": 8,
}


def _link(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], str)
        and isinstance(value[1], int)
    )


def stage_order(version: int) -> tuple[str, ...]:
    return STAGE_ORDER_V4


def needs_main_encode(settings: dict[str, Any]) -> bool:
    stages = settings["upscalers"]
    prior_image_stages = ("pixel_hires",)
    return any(stages[key]["enabled"] for key in prior_image_stages) and (
        stages["latent"]["enabled"] or stages["post_sampler"]["enabled"]
    )


def required_node_classes(settings: dict[str, Any]) -> set[str]:
    """Return repair-stage requirements, in addition to existing base checks."""
    stages = settings["upscalers"]
    required: set[str] = {"PrimitiveInt", "ComfyMathExpression"}
    if stages["pixel_hires"]["enabled"]:
        required.update({"UpscaleModelLoader", "ImageUpscaleWithModel"})
        if not stages["post_sampler"]["enabled"]:
            required.add("ImageScaleBy")
    if stages["nvidia"]["enabled"]:
        required.add("RTXVideoSuperResolution")
    if stages["seedvr2"]["enabled"]:
        required.update(
            {
                "ImageScaleBy",
                "UNETLoader",
                "VAELoader",
                "SeedVR2Preprocess",
                "VAEEncodeTiled",
                "SeedVR2Conditioning",
                "KSampler",
                "VAEDecodeTiled",
                "SeedVR2PostProcessing",
            }
        )
    if stages["latent"]["enabled"]:
        latent = stages["latent"]
        required.add(
            "SesquiLatentUpscale"
            if latent["upscale_method"] == "sesqui"
            else "LatentUpscaleBy"
        )
        required.add("KSampler")
    if stages["post_sampler"]["enabled"]:
        required.add("KSampler")
    if needs_main_encode(settings):
        required.add("VAEEncodeTiled")
    if stages["face_refine"]["enabled"]:
        required.add("ForbiddenVisionFaceProcessorIntegrated")
    if stages["dlss"]["enabled"]:
        required.add("DLSS5NeuralRender")
    return required


def build_pipeline(
    api: dict[str, Any],
    ui_template: dict[str, Any],
    settings: dict[str, Any],
    sample_seed: int,
    prompt_seed: int,
    filename_prefix: str,
    *,
    resource_paths: dict[str, str] | None = None,
    execution_nonce: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from .graph_utils import _prune_graph
    from .workflow import V2_OUTPUT_ID, WorkflowError, _mapped_resource_name

    execution_nonce = 0 if execution_nonce is None else execution_nonce
    if (
        type(execution_nonce) is not int
        or not 0 <= execution_nonce <= MAX_EXECUTION_NONCE
    ):
        raise WorkflowError("execution_nonce 必须是 JavaScript 安全范围内的非负整数")

    api = copy.deepcopy(api)
    version = settings["pipeline_version"]
    stages = settings["upscalers"]
    final_sampler_id = (
        "701:1246" if settings["base_mode"] == "dual_sampler" else "691:1240"
    )
    final_sampler = api[final_sampler_id]["inputs"]
    inherited = {
        key: copy.deepcopy(final_sampler[key])
        for key in ("model", "positive", "negative")
    }
    for node in api.values():
        node.setdefault("_meta", {}).update(pipeline_stage="base")

    def add(node_id: str, node_type: str, stage: str, **inputs: Any) -> list[Any]:
        api[node_id] = {
            "class_type": node_type,
            "inputs": inputs,
            "_meta": {
                "title": f"{STAGE_LABELS[stage]} · {node_type}",
                "pipeline_stage": stage,
            },
        }
        return [node_id, 0]

    image: list[Any] | None = None
    latent: list[Any] | None = [final_sampler_id, 0]
    latent_stage = "base"
    codec_index = 0
    encode_config = _TILING
    decode_config = _TILING

    def ensure_image() -> list[Any]:
        nonlocal image, codec_index
        if image is None:
            assert latent is not None
            codec_index += 1
            config = decode_config
            image = add(
                f"v3:decode:{codec_index}",
                "VAEDecodeTiled" if config["tiled"] else "VAEDecode",
                latent_stage,
                samples=list(latent),
                vae=["167:58", 0],
                **(
                    {k: v for k, v in config.items() if k != "tiled"}
                    if config["tiled"]
                    else {}
                ),
            )
        return list(image)

    def ensure_latent(stage: str) -> list[Any]:
        nonlocal latent, latent_stage, codec_index
        if latent is None:
            assert image is not None
            codec_index += 1
            config = encode_config
            latent = add(
                f"v3:encode:{codec_index}",
                "VAEEncodeTiled" if config["tiled"] else "VAEEncode",
                stage,
                pixels=list(image),
                vae=["167:58", 0],
                **(
                    {k: v for k, v in config.items() if k != "tiled"}
                    if config["tiled"]
                    else {}
                ),
            )
            latent_stage = stage
        return list(latent)

    if stages["pixel_hires"]["enabled"]:
        config = stages["pixel_hires"]
        model = add(
            "v3:pixel:model",
            "UpscaleModelLoader",
            "pixel_hires",
            model_name=_mapped_resource_name(config["model_name"], resource_paths),
        )
        image = add(
            "v3:pixel:upscale",
            "ImageUpscaleWithModel",
            "pixel_hires",
            upscale_model=model,
            image=ensure_image(),
        )
        latent = None
        if not stages["post_sampler"]["enabled"]:
            image = add(
                "v3:pixel:resize",
                "ImageScaleBy",
                "pixel_hires",
                image=image,
                upscale_method=config["upscale_method"],
                scale_by=config["percent"] / 100,
            )

    def nvidia_stage() -> None:
        nonlocal image, latent
        config = stages["nvidia"]
        image = add(
            "v3:vsr",
            "RTXVideoSuperResolution",
            "nvidia",
            images=ensure_image(),
            **{
                "resize_type": config["resize_type"],
                "resize_type.scale": config["scale"],
                "quality": config["quality"],
            },
        )
        latent = None

    def seedvr2_stage() -> None:
        nonlocal image, latent
        config = stages["seedvr2"]
        resized = add(
            "v3:seed:resize",
            "ImageScaleBy",
            "seedvr2",
            image=ensure_image(),
            upscale_method="lanczos",
            scale_by=config["scale_by"],
        )
        model = add(
            "v3:seed:model",
            "UNETLoader",
            "seedvr2",
            unet_name=_mapped_resource_name(config["model_name"], resource_paths),
            weight_dtype=config["weight_dtype"],
        )
        vae = add(
            "v3:seed:vae",
            "VAELoader",
            "seedvr2",
            vae_name=_mapped_resource_name(config["vae_name"], resource_paths),
        )
        prepared = add(
            "v3:seed:prepare", "SeedVR2Preprocess", "seedvr2", resized_images=resized
        )
        encoded = add(
            "v3:seed:encode",
            "VAEEncodeTiled",
            "seedvr2",
            pixels=prepared,
            vae=vae,
            **config["encode"],
        )
        conditioning = add(
            "v3:seed:conditioning",
            "SeedVR2Conditioning",
            "seedvr2",
            model=model,
            vae_conditioning=encoded,
        )
        sampled = add(
            "v3:seed:sample",
            "KSampler",
            "seedvr2",
            model=model,
            positive=conditioning,
            negative=[conditioning[0], 1],
            latent_image=encoded,
            seed=sample_seed,
            **config["sampler"],
        )
        decoded = add(
            "v3:seed:decode",
            "VAEDecodeTiled",
            "seedvr2",
            samples=sampled,
            vae=vae,
            **config["decode"],
        )
        # The reference must precede padding. Using the padded conditioning
        # image here would retain padding and alter downstream geometry.
        image = add(
            "v3:seed:finish",
            "SeedVR2PostProcessing",
            "seedvr2",
            images=decoded,
            original_resized_images=resized,
            color_correction_method=config["color_correction_method"],
        )
        latent = None

    def latent_stage_op() -> None:
        nonlocal latent, latent_stage, image
        config = stages["latent"]
        resize = latent_upscale_node(
            ensure_latent("latent"), config, stages["latent"]["scale_by"]
        )
        latent = add("v3:latent", resize["class_type"], "latent", **resize["inputs"])
        latent = add(
            "v4:latent:sample",
            "KSampler",
            "latent",
            latent_image=latent,
            seed=sample_seed,
            **inherited,
            **stages["latent"]["sampler"],
        )
        latent_stage, image = "latent", None

    def post_sampler_stage() -> None:
        nonlocal latent, latent_stage, image
        latent = add(
            "v3:post",
            "KSampler",
            "post_sampler",
            latent_image=ensure_latent("post_sampler"),
            seed=sample_seed,
            **inherited,
            **{k: v for k, v in stages["post_sampler"].items() if k != "enabled"},
        )
        latent_stage, image = "post_sampler", None

    def face_refine_stage() -> None:
        nonlocal image, latent
        # Face refinement shares the random workbench model and LoRA stack.
        face_inputs = copy.deepcopy(api["1253:1215"]["inputs"])
        face_inputs["image"] = ensure_image()
        image = add(
            "v3:face",
            "ForbiddenVisionFaceProcessorIntegrated",
            "face_refine",
            **face_inputs,
        )
        latent = None

    operations = {
        "nvidia": nvidia_stage,
        "seedvr2": seedvr2_stage,
        "latent": latent_stage_op,
        "post_sampler": post_sampler_stage,
        "face_refine": face_refine_stage,
    }
    for stage in stage_order(version):
        if stage in operations and stages[stage]["enabled"]:
            operations[stage]()
    if stages["dlss"]["enabled"]:
        image = add(
            "v3:dlss",
            "DLSS5NeuralRender",
            "dlss",
            images=ensure_image(),
            **{k: v for k, v in stages["dlss"].items() if k != "enabled"},
        )
        latent = None
    add(
        V2_OUTPUT_ID,
        "SaveImage",
        "export",
        images=ensure_image(),
        filename_prefix=filename_prefix,
    )
    # A declared input changes ComfyUI's ancestry cache key without changing
    # the actual sampling seed. Keep resource/conditioning branches cacheable.
    # Insert after constant folding so both dynamic inputs survive UI export.
    seed = add("v3:run:seed", "PrimitiveInt", "base", value=sample_seed)
    nonce = add("v3:run:nonce", "PrimitiveInt", "base", value=execution_nonce)
    add(
        "v3:run:gate",
        "ComfyMathExpression",
        "base",
        expression="a",
        **{"values.a": seed, "values.b": nonce},
    )
    first_sampler_id = (
        "701:1245" if settings["base_mode"] == "dual_sampler" else "691:1240"
    )
    seed_input = "noise_seed" if settings["base_mode"] == "dual_sampler" else "seed"
    api[first_sampler_id]["inputs"][seed_input] = ["v3:run:gate", 1]
    api = _prune_graph(api, V2_OUTPUT_ID)
    ui = export_ui_workflow(api, ui_template, pipeline_version=version)
    ui.setdefault("extra", {})["pipeline"] = {
        "version": version,
        "stages": [
            key
            for key in stage_order(version)
            if key in {"base", "export"} or stages[key]["enabled"]
        ],
        "execution_nonce": execution_nonce,
        "api_prompt": copy.deepcopy(api),
    }
    return api, ui


# Widget order is part of ComfyUI's editable-workflow format, unlike the API
# input dictionary. Explicit contracts avoid exporting stale template values.
_WIDGETS = {
    "PrimitiveInt": ["value", "@fixed"],
    "ComfyMathExpression": ["expression"],
    "UNETLoader": ["unet_name", "weight_dtype"],
    "CLIPLoader": ["clip_name", "type", "device"],
    "VAELoader": ["vae_name"],
    "CLIPTextEncode": ["text"],
    "LoadImage": ["image", "upload"],
    "Lora Loader (LoraManager)": ["__lm_autocomplete_meta_text", "text", "loras"],
    "KSampler": [
        "seed",
        "@fixed",
        "steps",
        "cfg",
        "sampler_name",
        "scheduler",
        "denoise",
    ],
    "KSamplerAdvanced": [
        "add_noise",
        "noise_seed",
        "@fixed",
        "steps",
        "cfg",
        "sampler_name",
        "scheduler",
        "start_at_step",
        "end_at_step",
        "return_with_leftover_noise",
    ],
    "EmptyLatentImage": ["width", "height", "batch_size"],
    "VAEDecodeTiled": ["tile_size", "overlap", "temporal_size", "temporal_overlap"],
    "VAEEncodeTiled": ["tile_size", "overlap", "temporal_size", "temporal_overlap"],
    "SesquiLatentUpscale": ["model_format", "scale", "half_precision"],
    "LatentUpscaleBy": ["upscale_method", "scale_by"],
    "UpscaleModelLoader": ["model_name"],
    "ImageScaleBy": ["upscale_method", "scale_by"],
    "SeedVR2PostProcessing": ["color_correction_method"],
    "RTXVideoSuperResolution": ["resize_type", "resize_type.scale", "quality"],
    "DLSS5NeuralRender": [
        "style",
        "intensity",
        "local_tone",
        "local_struct",
        "output_view",
        "output_mix",
        "gpu_gen",
    ],
    "SaveImage": ["filename_prefix"],
}
_OUTPUTS = {
    "PrimitiveInt": ["INT"],
    "ComfyMathExpression": ["FLOAT", "INT", "BOOLEAN"],
    "UNETLoader": ["MODEL"],
    "CLIPLoader": ["CLIP"],
    "VAELoader": ["VAE"],
    "Lora Loader (LoraManager)": ["MODEL", "CLIP", "STRING", "STRING"],
    "CLIPTextEncode": ["CONDITIONING"],
    "ConditioningZeroOut": ["CONDITIONING"],
    "SeedVR2Conditioning": ["CONDITIONING", "CONDITIONING"],
    "KSampler": ["LATENT"],
    "KSamplerAdvanced": ["LATENT"],
    "EmptyLatentImage": ["LATENT"],
    "SesquiLatentUpscale": ["LATENT"],
    "LatentUpscaleBy": ["LATENT"],
    "VAEEncode": ["LATENT"],
    "VAEEncodeTiled": ["LATENT"],
    "VAEDecode": ["IMAGE"],
    "VAEDecodeTiled": ["IMAGE"],
    "UpscaleModelLoader": ["UPSCALE_MODEL"],
    "ImageUpscaleWithModel": ["IMAGE"],
    "ImageScaleBy": ["IMAGE"],
    "SeedVR2Preprocess": ["IMAGE"],
    "SeedVR2PostProcessing": ["IMAGE"],
    "RTXVideoSuperResolution": ["IMAGE", "FLOAT", "INT", "INT"],
    "DLSS5NeuralRender": ["IMAGE"],
    "LoadImage": ["IMAGE", "MASK"],
    "SaveImage": [],
}


def export_ui_workflow(
    api: dict[str, Any], template: dict[str, Any], *, pipeline_version: int = 4
) -> dict[str, Any]:
    """Flatten the executable graph into an editable ComfyUI 0.4 workflow.

    Preserve custom node socket/widget metadata from the attached templates,
    while rebuilding every id/link and widget value from the submitted graph.
    """
    examples: dict[str, dict[str, Any]] = {}
    for graph in [template, *template.get("definitions", {}).get("subgraphs", [])]:
        for node in graph.get("nodes", []):
            examples.setdefault(node["type"], node)
    ids = {key: index + 1 for index, key in enumerate(api)}
    nodes: dict[str, dict[str, Any]] = {}
    for index, (key, source) in enumerate(api.items()):
        kind = source["class_type"]
        example = examples.get(kind, {})
        properties = copy.deepcopy(example.get("properties", {}))
        properties.pop("aaaliceControlHostId", None)
        properties.update(
            {
                "Node name for S&R": kind,
                "pipeline_api_id": key,
                "pipeline_stage": source.get("_meta", {}).get("pipeline_stage", "base"),
            }
        )
        inputs = source["inputs"]
        widget_names = _WIDGETS.get(kind)
        if widget_names is None:
            # Face processor's API inputs preserve its declared widget order.
            widget_names = [name for name, value in inputs.items() if not _link(value)]
            if kind == "ForbiddenVisionFaceProcessorIntegrated":
                widget_names.insert(widget_names.index("seed") + 1, "@fixed")
        values = []
        for name in widget_names:
            if name == "@fixed":
                values.append("fixed")
                continue
            value = inputs.get(
                name,
                "default"
                if name == "device"
                else "image"
                if name == "upload"
                else None,
            )
            if _link(value):
                value = 0
            if isinstance(value, dict) and "__value__" in value:
                value = value["__value__"]
            values.append(copy.deepcopy(value))
        output_types = _OUTPUTS.get(kind)
        if output_types is None:
            output_types = [out.get("type", "*") for out in example.get("outputs", [])]
        outputs = [
            {"name": output_type, "type": output_type, "links": []}
            for output_type in output_types
        ]
        stage_index = stage_order(pipeline_version).index(properties["pipeline_stage"])
        nodes[key] = {
            "id": ids[key],
            "type": kind,
            "title": source.get("_meta", {}).get("title", kind),
            "pos": [stage_index * 440 + (index % 2) * 40, (index // 2) * 240],
            "size": [360, max(100, 55 + 26 * len(values))],
            "flags": {},
            "order": index,
            "mode": 0,
            "inputs": [],
            "outputs": outputs,
            "properties": properties,
            "widgets_values": values,
        }
    links = []
    for key, source in api.items():
        kind = source["class_type"]
        example_inputs = {
            item["name"]: item for item in examples.get(kind, {}).get("inputs", [])
        }
        # Keep normal sockets even when optional/unconnected; ComfyUI matches
        # their positional slots when configuring the imported node.
        ordered_names = [
            name for name, spec in example_inputs.items() if "widget" not in spec
        ]
        ordered_names.extend(
            name
            for name, value in source["inputs"].items()
            if _link(value) and name not in ordered_names
        )
        for name in ordered_names:
            value = source["inputs"].get(name)
            spec = copy.deepcopy(example_inputs.get(name, {"name": name, "type": "*"}))
            spec["link"] = None
            if _link(value):
                origin, slot = value
                while len(nodes[origin]["outputs"]) <= slot:
                    nodes[origin]["outputs"].append(
                        {"name": "*", "type": "*", "links": []}
                    )
                output = nodes[origin]["outputs"][slot]
                spec["type"] = output["type"]
                link_id = len(links) + 1
                spec["link"] = link_id
                links.append(
                    [
                        link_id,
                        ids[origin],
                        slot,
                        ids[key],
                        len(nodes[key]["inputs"]),
                        output["type"],
                    ]
                )
                output["links"].append(link_id)
            nodes[key]["inputs"].append(spec)
    return {
        "version": 0.4,
        "last_node_id": len(nodes),
        "last_link_id": len(links),
        "nodes": list(nodes.values()),
        "links": links,
        "groups": [],
        "config": {},
        "extra": {"pipeline_api_ids": {str(value): key for key, value in ids.items()}},
    }
