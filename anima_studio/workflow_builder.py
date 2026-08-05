from __future__ import annotations

import copy
import json
import secrets
from pathlib import Path
from typing import Any, Mapping, Sequence

from .natural_runtime import NativeNaturalSettings, WorkflowGenerationOptions


class WorkflowError(ValueError):
    pass


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WorkflowError(f"cannot read workflow asset {path.name}: {exc}") from exc
    if not isinstance(value, dict) or not value:
        raise WorkflowError(f"workflow asset is empty: {path.name}")
    return value


class _ManifestBuilder:
    def __init__(self, path: str | Path, settings: NativeNaturalSettings) -> None:
        self.path = Path(path)
        self.settings = settings
        self.template = _read_object(self.path)
        self.manifest = _read_object(self.path.parent / "manifests" / self.path.name)
        self.bindings = self.manifest.get("bindings")
        if not isinstance(self.bindings, Mapping):
            raise WorkflowError("workflow manifest contains no bindings")

    @staticmethod
    def _inputs(workflow: Mapping[str, Any], node_id: Any) -> dict[str, Any]:
        node = workflow.get(str(node_id))
        if not isinstance(node, Mapping) or not isinstance(node.get("inputs"), dict):
            raise WorkflowError(f"workflow node is missing: {node_id}")
        return node["inputs"]

    def _set(self, workflow: Mapping[str, Any], binding: Any, value: Any, default_input: str = "") -> None:
        if not isinstance(binding, Mapping):
            return
        name = str(binding.get("input") or default_input)
        if not name:
            raise WorkflowError("manifest binding has no input name")
        self._inputs(workflow, binding.get("node_id"))[name] = value

    def _variant(self, requested: str = "") -> tuple[str, list[str]]:
        variants = self.manifest.get("output_variants")
        if not isinstance(variants, Mapping) or not variants:
            raise WorkflowError("workflow manifest contains no output variants")
        name = requested if requested in variants else str(self.manifest.get("default_output_variant") or "")
        if name not in variants:
            name = next(iter(variants))
        value = variants[name]
        outputs = value.get("preferred_node_ids") if isinstance(value, Mapping) else ()
        result = [str(item) for item in outputs or ()]
        if not result:
            raise WorkflowError("workflow output variant is empty")
        return name, result

    @staticmethod
    def _ancestors(workflow: Mapping[str, Any], outputs: Sequence[str]) -> set[str]:
        keep: set[str] = set()
        pending = list(outputs)
        while pending:
            node_id = str(pending.pop())
            if node_id in keep:
                continue
            node = workflow.get(node_id)
            if not isinstance(node, Mapping):
                continue
            keep.add(node_id)
            inputs = node.get("inputs")
            if not isinstance(inputs, Mapping):
                continue
            for value in inputs.values():
                if isinstance(value, list) and len(value) >= 2 and str(value[0]) in workflow:
                    pending.append(str(value[0]))
        return keep

    def _common(
        self,
        options: WorkflowGenerationOptions,
        *,
        output_variant: str = "",
        prune: bool = True,
    ) -> tuple[dict[str, Any], int, list[str]]:
        workflow = copy.deepcopy(self.template)
        self._set(workflow, self.bindings.get("positive_prompt"), options.prompt)
        self._set(workflow, self.bindings.get("negative_prompt"), options.negative_prompt)
        seed = options.seed if options.seed is not None else secrets.randbelow(2**63)
        for binding in self.bindings.get("seed") or ():
            self._set(workflow, binding, seed, "seed")
        resolution = self.bindings.get("resolution")
        if isinstance(resolution, Mapping):
            inputs = self._inputs(workflow, resolution.get("node_id"))
            inputs[str(resolution.get("width_input") or "width")] = options.width or self.settings.default_width
            inputs[str(resolution.get("height_input") or "height")] = options.height or self.settings.default_height
        for sampler in self.bindings.get("samplers") or ():
            if not isinstance(sampler, Mapping):
                continue
            inputs = self._inputs(workflow, sampler.get("node_id"))
            for field, value in (("steps", options.steps), ("cfg", options.cfg), ("denoise", options.denoise)):
                key = sampler.get(f"{field}_input")
                if key and value is not None:
                    inputs[str(key)] = value
        unet = self.bindings.get("unet")
        if self.settings.unet_model_name and isinstance(unet, Mapping):
            self._set(workflow, unet, self.settings.unet_model_name, self.settings.unet_model_input_name)
        lora = self.bindings.get("lora")
        if options.dynamic_loras and isinstance(lora, Mapping):
            values = [
                {
                    "name": item.filename,
                    "strength": item.strength,
                    "active": item.enabled,
                    "clipStrength": item.strength,
                    "expanded": False,
                }
                for item in options.dynamic_loras
                if item.enabled
            ]
            self._inputs(workflow, lora.get("node_id"))["loras"] = {"__value__": values}
        variant, outputs = self._variant(output_variant)
        del variant
        if prune:
            keep = self._ancestors(workflow, outputs)
            workflow = {key: value for key, value in workflow.items() if key in keep}
        return workflow, seed, outputs


class WorkflowBuilder(_ManifestBuilder):
    def build(self, options: WorkflowGenerationOptions) -> tuple[dict[str, Any], int, list[str]]:
        return self._common(options, output_variant=options.pipeline)


class Img2ImgWorkflowBuilder(_ManifestBuilder):
    def build_img2img(
        self,
        image: str,
        options: WorkflowGenerationOptions,
    ) -> tuple[dict[str, Any], int, list[str]]:
        workflow, seed, outputs = self._common(options, output_variant=options.pipeline, prune=False)
        self._set(workflow, self.bindings.get("input_image"), image, "image")
        keep = self._ancestors(workflow, outputs)
        return {key: value for key, value in workflow.items() if key in keep}, seed, outputs


class InpaintWorkflowBuilder(_ManifestBuilder):
    def build(
        self,
        image: str,
        mask: str,
        options: WorkflowGenerationOptions,
    ) -> tuple[dict[str, Any], int, list[str]]:
        workflow, seed, outputs = self._common(options, output_variant=options.inpaint_mode, prune=False)
        self._set(workflow, self.bindings.get("input_image"), image, "image")
        self._set(workflow, self.bindings.get("mask_image"), mask, "image")
        keep = self._ancestors(workflow, outputs)
        return {key: value for key, value in workflow.items() if key in keep}, seed, outputs


class ControlWorkflowBuilder(_ManifestBuilder):
    def build_control(
        self,
        image: str,
        options: WorkflowGenerationOptions,
    ) -> tuple[dict[str, Any], int, list[str]]:
        if not options.control_modes:
            raise WorkflowError("control generation requires at least one control mode")
        workflow, seed, outputs = self._common(options, output_variant="all", prune=False)
        self._set(workflow, self.bindings.get("input_image"), image, "image")
        controls = self.bindings.get("controls")
        if not isinstance(controls, Mapping):
            raise WorkflowError("control manifest contains no controls")
        unknown = set(options.control_modes) - set(controls)
        if unknown:
            raise WorkflowError("unknown control modes: " + ", ".join(sorted(unknown)))
        target = self.bindings.get("control_model_target")
        if not isinstance(target, Mapping):
            raise WorkflowError("control manifest contains no model target")
        model_value = self._inputs(workflow, target.get("node_id")).get(str(target.get("input") or "model"))
        if not isinstance(model_value, list):
            model_value = ["462", 0]
        current = model_value
        selected = set(options.control_modes)
        for name, binding in controls.items():
            if not isinstance(binding, Mapping):
                continue
            apply_id = str(binding.get("apply_node_id") or "")
            preprocess_id = str(binding.get("preprocessor_node_id") or "")
            if name not in selected:
                workflow.pop(apply_id, None)
                if preprocess_id:
                    workflow.pop(preprocess_id, None)
                continue
            inputs = self._inputs(workflow, apply_id)
            inputs["model"] = list(current)
            inputs["strength"] = float(binding.get("default_strength") or 1.0)
            if binding.get("end_percent") is not None:
                inputs["end_percent"] = float(binding["end_percent"])
            current = [apply_id, 0]
        self._inputs(workflow, target.get("node_id"))[str(target.get("input") or "model")] = current
        keep = self._ancestors(workflow, outputs)
        return {key: value for key, value in workflow.items() if key in keep}, seed, outputs


class ImageWorkflowBuilder(_ManifestBuilder):
    def build(
        self,
        image: str,
        *,
        scale: float | None = None,
        quality: str | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        workflow = copy.deepcopy(self.template)
        self._set(workflow, self.bindings.get("input_image"), image, "image")
        upscale = self.bindings.get("upscale")
        if not isinstance(upscale, Mapping):
            raise WorkflowError("upscale manifest contains no upscale binding")
        inputs = self._inputs(workflow, upscale.get("node_id"))
        inputs[str(upscale.get("scale_input") or "resize_type.scale")] = (
            self.settings.rtx_scale if scale is None else float(scale)
        )
        inputs[str(upscale.get("quality_input") or "quality")] = (
            self.settings.rtx_quality if quality is None else str(quality)
        )
        _, outputs = self._variant("rtx")
        keep = self._ancestors(workflow, outputs)
        return {key: value for key, value in workflow.items() if key in keep}, outputs
