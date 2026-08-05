"""
AstrBot Comfy Anima 插件 v1.9.21

功能描述：
- 加载和修改 ComfyUI API 工作流
- 解析绘图指令中的可选参数

作者: Yen
版本: 1.9.21
日期: 2026-08-03
"""

import copy
import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..constants import MAX_CFG, MAX_IMAGE_SIDE, MAX_SEED, MAX_STEPS, MIN_IMAGE_SIDE
from ..models import GenerationOptions, PluginSettings
from .command_aliases import (
    CONTEXT_GENERATION,
    CONTEXT_INPAINT,
    CONTEXT_SEMANTIC_REDRAW,
    normalize_command_aliases,
)
from .lora import inject_loras
from .workflow_profiles import (
    InputBinding,
    WorkflowProfile,
    WorkflowProfileError,
    load_workflow_profile,
)


class WorkflowError(ValueError):
    """工作流格式或节点映射无效。"""


@dataclass(frozen=True, slots=True)
class _GenerationCommandToken:
    """One parsed command token and its exact location in the source text."""

    value: str
    start: int
    end: int


def _tokenize_generation_command(command_text: str) -> list[_GenerationCommandToken]:
    """Split command options without treating prompt apostrophes as shell quotes.

    Danbooru/Anima tags commonly contain English possessives such as
    ``worm's eye view``. A quote starts a grouped token only when it is the
    token's first character, so apostrophes and Danbooru backslashes remain
    literal. Source spans are retained because option removal must not flatten
    a hybrid prompt's original Tag and visual-sentence layers.
    """

    tokens: list[_GenerationCommandToken] = []
    length = len(command_text)
    index = 0
    while index < length:
        while index < length and command_text[index].isspace():
            index += 1
        if index >= length:
            break

        start = index
        quote = command_text[index] if command_text[index] in {'"', "'"} else ""
        if quote:
            index += 1
            value_start = index
            while index < length and command_text[index] != quote:
                index += 1
            if index >= length:
                raise ValueError("参数引号不完整: No closing quotation")
            value = command_text[value_start:index]
            index += 1
            end = index
        else:
            while index < length and not command_text[index].isspace():
                index += 1
            end = index
            value = command_text[start:end]
        tokens.append(_GenerationCommandToken(value=value, start=start, end=end))
    return tokens


def _split_generation_command_tokens(command_text: str) -> list[str]:
    """Return command token values while preserving legacy parser semantics."""

    return [token.value for token in _tokenize_generation_command(command_text)]


_SOURCE_VALUE_OPTIONS = frozenset(
    {
        "--negative",
        "--n",
        "--seed",
        "--sd",
        "--size",
        "--sz",
        "--steps",
        "--st",
        "--cfg",
        "--c",
        "--pipeline",
        "--p",
        "--denoise",
        "--d",
        "--preset",
        "--lora-preset",
        "--pr",
    }
)
_SOURCE_FLAG_OPTIONS = frozenset(
    {
        "--upscale",
        "--u",
        "--no-upscale",
        "--nu",
        "--raw",
        "--no-llm",
        "--r",
    }
)
_SOURCE_LLM_OPTIONS = frozenset(
    {"--llm", "--l", "--llmcc", "--lcc", "--llm-character-change"}
)
_SOURCE_LLM_EXPANSION_VALUES = frozenset(
    {
        "s",
        "standard",
        "normal",
        "普通",
        "简洁",
        "u",
        "ultra",
        "complex",
        "ornate",
        "复杂",
        "华丽",
        "高质量",
    }
)
_SOURCE_CHARACTER_CHANGE_VALUES = frozenset(
    {
        "c",
        "cc",
        "char-change",
        "char_change",
        "character-change",
        "character_change",
    }
)
_SOURCE_CONTROL_OPTIONS = frozenset({"--m", "--control", "--control-mode"})
_SOURCE_CHARACTER_SWAP_MODE_VALUES = frozenset(
    {"k", "keep-outfit", "t", "target-outfit"}
)


def _is_generation_control_value(value: str) -> bool:
    """Ask the canonical alias normalizer whether one value is a control mode."""

    try:
        normalized = normalize_command_aliases(
            ("--control-mode", value),
            context=CONTEXT_GENERATION,
        )
    except ValueError:
        return False
    return bool(normalized and normalized[0] == "--control-mode")


def _source_prompt_token_indexes(
    tokens: list[_GenerationCommandToken],
    *,
    mode_context: str,
    character_change_requested: bool,
) -> list[int]:
    """Return source-token indexes that belong to the prompt, not options."""

    prompt_indexes: list[int] = []
    index = 0
    while index < len(tokens):
        token = tokens[index].value.casefold()
        if not token.startswith("--"):
            prompt_indexes.append(index)
            index += 1
            continue

        if token in _SOURCE_VALUE_OPTIONS:
            index += 2
            continue
        if token in _SOURCE_FLAG_OPTIONS:
            index += 1
            continue
        if token in _SOURCE_LLM_OPTIONS:
            index += 1
            while index < len(tokens) and not tokens[index].value.startswith("--"):
                value = tokens[index].value.strip().casefold()
                if value in _SOURCE_LLM_EXPANSION_VALUES:
                    index += 1
                    continue
                if mode_context == "generation" and value in _SOURCE_CHARACTER_CHANGE_VALUES:
                    index += 1
                    continue
                if (
                    mode_context == "generation"
                    and value == "char"
                    and index + 1 < len(tokens)
                    and tokens[index + 1].value.strip().casefold() == "change"
                ):
                    index += 2
                    continue
                break
            continue

        if mode_context == "generation" and token in _SOURCE_CONTROL_OPTIONS:
            if (
                token == "--m"
                and character_change_requested
                and index + 1 < len(tokens)
                and tokens[index + 1].value.strip().casefold()
                in _SOURCE_CHARACTER_SWAP_MODE_VALUES
            ):
                index += 2
                continue
            index += 1
            while index < len(tokens) and _is_generation_control_value(
                tokens[index].value
            ):
                index += 1
            continue

        if token in {"--mode", "--m"} and mode_context != "generation":
            index += 2
            continue
        if character_change_requested and token in {
            "--mode",
            "--swap-mode",
            "--weight",
            "--swap-weight",
            "--w",
        }:
            index += 2
            continue
        if character_change_requested and token in {
            "--preview",
            "--swap-preview",
            "--v",
            "--no-character-lora",
            "--no-lora",
            "--nl",
            "--swap-no-character-lora",
        }:
            index += 1
            continue

        # The owning parser reports unknown options before reconstruction.
        index += 1
    return prompt_indexes


def _removed_option_separator(source: str) -> str:
    """Collapse only whitespace surrounding removed options, preserving layers."""

    if "\n" in source or "\r" in source:
        return "\n"
    return " " if any(character.isspace() for character in source) else ""


def _rebuild_generation_prompt(
    command_text: str,
    tokens: list[_GenerationCommandToken],
    prompt_indexes: list[int],
) -> str:
    """Rebuild prompt tokens with their original inter-token layout intact."""

    if not prompt_indexes:
        return ""
    parts = [tokens[prompt_indexes[0]].value]
    previous_index = prompt_indexes[0]
    for token_index in prompt_indexes[1:]:
        previous = tokens[previous_index]
        current = tokens[token_index]
        separator = command_text[previous.end : current.start]
        if token_index != previous_index + 1:
            separator = _removed_option_separator(separator)
        parts.extend((separator, current.value))
        previous_index = token_index
    return "".join(parts).strip()


class WorkflowBuilder:
    """基于模板构造单次 ComfyUI 工作流。"""

    def __init__(self, workflow_path: Path, settings: PluginSettings):
        self._workflow_path = workflow_path
        self._settings = settings
        self._template = self._load_workflow(workflow_path)
        try:
            self._profile = load_workflow_profile(workflow_path, settings)
        except WorkflowProfileError as exc:
            raise WorkflowError(f"工作流档案无效: {exc}") from exc
        self._validate_required_nodes()

    @property
    def profile(self) -> WorkflowProfile:
        return self._profile

    @staticmethod
    def _load_workflow(path: Path) -> dict[str, Any]:
        """加载 ComfyUI API 格式工作流。"""
        if not path.is_file():
            raise WorkflowError(f"工作流文件不存在: {path}")
        if path.stat().st_size > 10 * 1024 * 1024:
            raise WorkflowError("工作流文件超过 10MB，已拒绝加载")
        try:
            with path.open("r", encoding="utf-8") as file:
                workflow = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkflowError(f"工作流读取失败: {exc}") from exc
        if not isinstance(workflow, dict) or not workflow:
            raise WorkflowError("工作流根节点必须是非空对象")
        if "nodes" in workflow:
            raise WorkflowError("检测到 UI 工作流，请先导出为 API Format JSON")
        return workflow

    def _validate_required_nodes(self) -> None:
        """验证生成必需的节点及输入字段。"""
        if self._profile.task_type not in {
            "text_to_image",
            "img2img",
            "control_generation",
        }:
            raise WorkflowError("当前工作流不是生图工作流")
        binding = self._profile.prompt
        if binding is None:
            raise WorkflowError("工作流档案缺少正面提示词映射")
        node = self._template.get(binding.node_id)
        inputs = node.get("inputs") if isinstance(node, dict) else None
        if not isinstance(inputs, dict):
            raise WorkflowError(f"工作流缺少节点 {binding.node_id}")
        if binding.input_name:
            if binding.input_name not in inputs:
                raise WorkflowError(
                    f"节点 {binding.node_id} 缺少输入 {binding.input_name}"
                )
        elif not any(name in inputs for name in ("positive", "text", "prompt")):
            raise WorkflowError(
                f"节点 {binding.node_id} 缺少 positive、text 或 prompt 文本输入"
            )

        for variant in self._profile.output_variants.values():
            for node_id in variant.preferred_node_ids:
                if node_id not in self._template:
                    raise WorkflowError(f"工作流缺少输出节点 {node_id}")

    def get_template_input(self, node_id: str, input_name: str) -> Any:
        """读取模板节点输入，用于管理命令显示当前模型。"""
        node = self._template.get(node_id)
        inputs = node.get("inputs") if isinstance(node, dict) else None
        if not isinstance(inputs, dict):
            return None
        return copy.deepcopy(inputs.get(input_name))

    def template_sampler_settings(self) -> list[dict[str, Any]]:
        """Return safe sampler defaults for WebUI inspection."""
        result: list[dict[str, Any]] = []
        for binding in self._profile.samplers:
            node = self._template.get(binding.node_id)
            inputs = node.get("inputs") if isinstance(node, dict) else None
            if not isinstance(inputs, dict):
                continue
            result.append(
                {
                    "node_id": binding.node_id,
                    "title": str((node.get("_meta") or {}).get("title") or "Sampler"),
                    "class_type": str(node.get("class_type") or ""),
                    "steps": inputs.get(binding.steps_input),
                    "cfg": inputs.get(binding.cfg_input),
                    "denoise": inputs.get(binding.denoise_input),
                }
            )
        return result

    @staticmethod
    def _resolve_input_name(inputs: dict[str, Any], binding: InputBinding) -> str:
        if binding.input_name:
            return binding.input_name
        for name in ("noise_seed", "seed", "positive", "text", "prompt"):
            if name in inputs:
                return name
        raise WorkflowError(f"节点 {binding.node_id} 没有可写入的兼容输入")

    @staticmethod
    def _set_input(
        workflow: dict[str, Any], node_id: str, input_name: str, value: Any
    ) -> None:
        """设置指定节点输入，节点不存在时抛出明确错误。"""
        node = workflow.get(node_id)
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            raise WorkflowError(f"无法设置节点 {node_id}，节点不存在或格式错误")
        node["inputs"][input_name] = value

    def build(
        self, options: GenerationOptions
    ) -> tuple[dict[str, Any], int, list[str]]:
        """生成一次可提交的工作流副本。

        Args:
            options: 用户本次生成参数。

        Returns:
            工作流、实际随机种和优先输出节点列表。
        """
        workflow = copy.deepcopy(self._template)
        seed = options.seed if options.seed is not None else secrets.randbelow(MAX_SEED)

        unet_binding = self._profile.unet
        if self._settings.unet_model_name and unet_binding is not None:
            unet_node = workflow.get(unet_binding.node_id)
            unet_inputs = (
                unet_node.get("inputs") if isinstance(unet_node, dict) else None
            )
            input_name = unet_binding.input_name
            if not isinstance(unet_inputs, dict) or input_name not in unet_inputs:
                raise WorkflowError(
                    f"工作流缺少 UNET 节点 {unet_binding.node_id} "
                    f"或输入 {input_name}"
                )
            unet_inputs[input_name] = self._settings.unet_model_name

        prompt_binding = self._profile.prompt
        assert prompt_binding is not None
        prompt_inputs = workflow[prompt_binding.node_id]["inputs"]
        prompt_input_name = self._resolve_input_name(prompt_inputs, prompt_binding)
        prompt_inputs[prompt_input_name] = options.prompt.strip()
        if options.dynamic_loras or options.lora_injection_mode == "replace":
            if not self._profile.lora_node_id:
                raise WorkflowError("当前工作流档案没有动态 LoRA 节点")
            inject_loras(
                workflow,
                self._profile.lora_node_id,
                options.dynamic_loras,
                mode=(options.lora_injection_mode or self._settings.dynamic_lora_mode),
            )
        if options.negative_prompt:
            negative_binding = self._profile.negative
            if negative_binding is None:
                raise WorkflowError("当前工作流档案没有负面提示词节点")
            negative_node = workflow.get(negative_binding.node_id)
            if not isinstance(negative_node, dict) or not isinstance(
                negative_node.get("inputs"), dict
            ):
                raise WorkflowError(
                    f"工作流缺少负面提示词节点 {negative_binding.node_id}"
                )
            negative_inputs = negative_node["inputs"]
            input_name = self._resolve_input_name(negative_inputs, negative_binding)
            original = str(negative_inputs.get(input_name, "")).strip()
            combined = ", ".join(
                part for part in (original, options.negative_prompt.strip()) if part
            )
            negative_inputs[input_name] = combined

        for binding in self._profile.seed_bindings:
            seed_node = workflow.get(binding.node_id)
            seed_inputs = (
                seed_node.get("inputs") if isinstance(seed_node, dict) else None
            )
            if not isinstance(seed_inputs, dict):
                continue
            input_name = self._resolve_input_name(seed_inputs, binding)
            seed_inputs[input_name] = seed

        resolution_binding = self._profile.resolution
        resolution_node = (
            workflow.get(resolution_binding.node_id)
            if resolution_binding is not None
            else None
        )
        if isinstance(resolution_node, dict) and isinstance(
            resolution_node.get("inputs"), dict
        ):
            width = (
                options.width
                if options.width is not None
                else self._settings.default_width
            )
            height = (
                options.height
                if options.height is not None
                else self._settings.default_height
            )
            assert resolution_binding is not None
            self._set_input(
                workflow,
                resolution_binding.node_id,
                resolution_binding.width_input,
                width,
            )
            self._set_input(
                workflow,
                resolution_binding.node_id,
                resolution_binding.height_input,
                height,
            )
        elif options.width is not None or options.height is not None:
            raise WorkflowError(
                "当前工作流档案没有可写入的分辨率节点"
            )

        configured_steps = int(getattr(self._settings, "sampler_steps_override", 0) or 0)
        effective_steps = options.steps
        if effective_steps is None and configured_steps > 0:
            effective_steps = configured_steps
        for binding in self._profile.samplers:
            node = workflow.get(binding.node_id)
            if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
                continue
            if effective_steps is not None:
                node["inputs"][binding.steps_input] = effective_steps
            if options.cfg is not None:
                node["inputs"][binding.cfg_input] = options.cfg
            if (
                options.denoise is not None
                and self._profile.profile_id != "anima_iterative"
            ):
                node["inputs"][binding.denoise_input] = options.denoise

        if self._profile.upscale is not None:
            binding = self._profile.upscale
            upscale_node = workflow.get(binding.node_id)
            inputs = (
                upscale_node.get("inputs")
                if isinstance(upscale_node, dict)
                else None
            )
            if isinstance(inputs, dict):
                inputs[binding.scale_input] = self._settings.rtx_scale
                inputs[binding.quality_input] = self._settings.rtx_quality

        if self._profile.profile_id == "anima_iterative":
            iterative_node = workflow.get("101")
            iterative_inputs = (
                iterative_node.get("inputs")
                if isinstance(iterative_node, dict)
                else None
            )
            if isinstance(iterative_inputs, dict):
                iterative_inputs["upscale_factor"] = self._settings.iterative_scale
                iterative_inputs["steps"] = self._settings.iterative_steps
            iterative_sampler = workflow.get("100")
            iterative_sampler_inputs = (
                iterative_sampler.get("inputs")
                if isinstance(iterative_sampler, dict)
                else None
            )
            if isinstance(iterative_sampler_inputs, dict):
                iterative_sampler_inputs["denoise"] = (
                    options.denoise
                    if options.denoise is not None
                    else self._settings.iterative_denoise
                )

        upscale_enabled = (
            self._settings.enable_upscale
            if options.enable_upscale is None
            else options.enable_upscale
        )
        variant_name = "rtx" if upscale_enabled else "base"
        variant = self._profile.output_variants.get(variant_name)
        if variant is None:
            variant = self._profile.active_output
        for node_id in variant.prune_node_ids:
            workflow.pop(node_id, None)
        preferred_nodes = list(variant.preferred_node_ids)

        return workflow, seed, preferred_nodes


class Img2ImgWorkflowBuilder(WorkflowBuilder):
    """Build a true Anima img2img workflow from one uploaded source image.

    Unlike reverse-prompt redraw, the source pixels are always resized, VAE
    encoded, and connected directly to the primary sampler latent input.
    """

    _PIPELINE_LAYOUT = {
        "base": {
            "preferred": ("88",),
            "prune": ("100", "101", "102", "103", "458", "552"),
        },
        "rtx": {
            "preferred": ("458",),
            "prune": ("88", "100", "101", "102", "103"),
        },
        "iterative": {
            "preferred": ("103",),
            "prune": ("88", "458", "552"),
        },
    }

    def __init__(self, workflow_path: Path, settings: PluginSettings):
        super().__init__(workflow_path, settings)
        profile = self.profile
        if profile.profile_id != "anima_img2img" or profile.input_image is None:
            raise WorkflowError("current workflow is not the bundled Anima img2img workflow")
        if profile.task_type != "img2img":
            raise WorkflowError("Anima img2img manifest must declare task_type img2img")

        required_nodes = {
            "8",
            "11",
            "12",
            "15",
            "19",
            "44",
            "45",
            "88",
            "100",
            "101",
            "102",
            "103",
            "458",
            "462",
            "500",
            "501",
            "502",
            "552",
        }
        missing = sorted(required_nodes - set(self._template))
        if missing:
            raise WorkflowError(
                "Anima img2img workflow is missing nodes: " + ", ".join(missing)
            )
        expected_links = {
            ("501", "image"): ["500", 0],
            ("502", "pixels"): ["501", 0],
            ("502", "vae"): ["15", 0],
            ("19", "latent_image"): ["502", 0],
            ("19", "positive"): ["11", 0],
            ("19", "negative"): ["12", 0],
        }
        for (node_id, input_name), expected in expected_links.items():
            node = self._template.get(node_id)
            inputs = node.get("inputs") if isinstance(node, dict) else None
            actual = inputs.get(input_name) if isinstance(inputs, dict) else None
            if actual != expected:
                raise WorkflowError(
                    f"Anima img2img node {node_id}.{input_name} must be {expected}"
                )
        if any(
            node.get("class_type") == "EmptyLatentImage"
            for node in self._template.values()
            if isinstance(node, dict)
        ):
            raise WorkflowError("Anima img2img workflow must not use EmptyLatentImage")

    def build(
        self,
        image_name: str,
        options: GenerationOptions,
    ) -> tuple[dict[str, Any], int, list[str]]:
        """Build an img2img request and select base, RTX, or iterative output."""

        normalized_image_name = str(image_name or "").strip()
        if not normalized_image_name:
            raise WorkflowError("img2img source image is required")

        workflow, seed, _ = super().build(options)
        input_binding = self.profile.input_image
        assert input_binding is not None
        self._set_input(
            workflow,
            input_binding.node_id,
            input_binding.input_name,
            normalized_image_name,
        )

        # Reassert the pixel-to-latent chain on every build. This prevents a
        # custom manifest or stale template from silently falling back to an
        # empty text-to-image latent.
        self._set_input(workflow, "501", "image", ["500", 0])
        self._set_input(workflow, "502", "pixels", ["501", 0])
        self._set_input(workflow, "502", "vae", ["15", 0])
        self._set_input(workflow, "19", "latent_image", ["502", 0])

        pipeline = str(
            options.pipeline
            or getattr(self._settings, "default_generation_pipeline", "base")
            or "base"
        ).strip().casefold()
        layout = self._PIPELINE_LAYOUT.get(pipeline)
        if layout is None:
            raise WorkflowError(f"Anima img2img does not support pipeline: {pipeline}")

        if pipeline == "iterative":
            iterative_node = workflow.get("101")
            iterative_inputs = (
                iterative_node.get("inputs")
                if isinstance(iterative_node, dict)
                else None
            )
            if isinstance(iterative_inputs, dict):
                iterative_inputs["upscale_factor"] = self._settings.iterative_scale
                iterative_inputs["steps"] = self._settings.iterative_steps
            iterative_sampler = workflow.get("100")
            iterative_sampler_inputs = (
                iterative_sampler.get("inputs")
                if isinstance(iterative_sampler, dict)
                else None
            )
            if isinstance(iterative_sampler_inputs, dict):
                # The second-stage upscale sampler is intentionally isolated
                # from the primary img2img redraw strength. A high/free redraw
                # denoise belongs only to sampler 19 and must not destabilize
                # the iterative refinement pass.
                iterative_sampler_inputs["denoise"] = self._settings.iterative_denoise

        for node_id in layout["prune"]:
            workflow.pop(node_id, None)
        return workflow, seed, list(layout["preferred"])

    def build_img2img(
        self,
        image_name: str,
        options: GenerationOptions,
    ) -> tuple[dict[str, Any], int, list[str]]:
        """Compatibility alias with the control/inpaint builder naming style."""

        return self.build(image_name, options)


class ImageWorkflowBuilder:
    """Build a standalone image-processing workflow such as RTX upscale."""

    def __init__(self, workflow_path: Path, settings: PluginSettings):
        self._workflow_path = workflow_path
        self._settings = settings
        self._template = WorkflowBuilder._load_workflow(workflow_path)
        try:
            self._profile = load_workflow_profile(workflow_path, settings)
        except WorkflowProfileError as exc:
            raise WorkflowError(f"工作流档案无效: {exc}") from exc
        if self._profile.task_type != "upscale" or self._profile.input_image is None:
            raise WorkflowError("当前工作流不是独立图片放大工作流")

    @property
    def profile(self) -> WorkflowProfile:
        return self._profile

    def build(
        self,
        image_name: str,
        *,
        scale: float | None = None,
        quality: str | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        workflow = copy.deepcopy(self._template)
        input_binding = self._profile.input_image
        WorkflowBuilder._set_input(
            workflow,
            input_binding.node_id,
            input_binding.input_name,
            image_name,
        )
        if self._profile.upscale is not None:
            binding = self._profile.upscale
            WorkflowBuilder._set_input(
                workflow,
                binding.node_id,
                binding.scale_input,
                self._settings.rtx_scale if scale is None else scale,
            )
            WorkflowBuilder._set_input(
                workflow,
                binding.node_id,
                binding.quality_input,
                self._settings.rtx_quality if quality is None else quality,
            )
        variant = self._profile.active_output
        for node_id in variant.prune_node_ids:
            workflow.pop(node_id, None)
        return workflow, list(variant.preferred_node_ids)


class ControlWorkflowBuilder(WorkflowBuilder):
    """Build one Anima LLLite image-controlled generation workflow."""

    _CONTROL_ORDER = ("pose", "depth", "lineart", "reference")

    def __init__(self, workflow_path: Path, settings: PluginSettings):
        super().__init__(workflow_path, settings)
        profile = self.profile
        if (
            profile.task_type != "control_generation"
            or profile.input_image is None
            or profile.control_model_target is None
            or set(profile.controls) != set(self._CONTROL_ORDER)
        ):
            raise WorkflowError("当前工作流不是完整的 Anima 底图控制工作流")
        required_nodes = {
            profile.input_image.node_id,
            profile.control_model_target.node_id,
        }
        control_image_node_id = str(
            profile.defaults.get("control_image_node_id")
            or profile.input_image.node_id
        ).strip()
        if not control_image_node_id:
            raise WorkflowError("底图控制工作流缺少控制图输出节点")
        required_nodes.add(control_image_node_id)
        for binding in profile.controls.values():
            required_nodes.add(binding.apply_node_id)
            if binding.preprocessor_node_id:
                required_nodes.add(binding.preprocessor_node_id)
        missing = sorted(node_id for node_id in required_nodes if node_id not in self._template)
        if missing:
            raise WorkflowError("底图控制工作流缺少节点: " + ", ".join(missing))

    @staticmethod
    def _mode_strength(default_strength: float, mode_count: int) -> float:
        """Reduce competing controls conservatively while preserving single-mode fidelity."""

        factor = 1.0 if mode_count <= 2 else (0.85 if mode_count == 3 else 0.75)
        return round(max(0.0, min(10.0, default_strength * factor)), 4)

    def build_control(
        self,
        image_name: str,
        options: GenerationOptions,
    ) -> tuple[dict[str, Any], int, list[str]]:
        modes = tuple(
            mode
            for mode in self._CONTROL_ORDER
            if mode in set(options.control_modes)
        )
        if not modes:
            raise WorkflowError("底图控制至少需要 pose、depth、lineart 或 reference 之一")
        unknown = sorted(set(options.control_modes) - set(self._CONTROL_ORDER))
        if unknown:
            raise WorkflowError("未知底图控制模式: " + ", ".join(unknown))

        workflow, seed, preferred_nodes = super().build(options)
        profile = self.profile
        source = profile.input_image
        target = profile.control_model_target
        assert source is not None and target is not None
        self._set_input(workflow, source.node_id, source.input_name, image_name)
        control_image_node_id = str(
            profile.defaults.get("control_image_node_id") or source.node_id
        ).strip()
        control_image_node = workflow.get(control_image_node_id)
        control_image_inputs = (
            control_image_node.get("inputs")
            if isinstance(control_image_node, dict)
            else None
        )
        width = options.width or self._settings.default_width
        height = options.height or self._settings.default_height
        if isinstance(control_image_inputs, dict):
            if "width" in control_image_inputs:
                control_image_inputs["width"] = width
            if "height" in control_image_inputs:
                control_image_inputs["height"] = height
        control_image_link: list[Any] = [control_image_node_id, 0]

        base_model: list[Any]
        if not profile.lora_node_id:
            raise WorkflowError("底图控制工作流缺少动态 LoRA 节点")
        base_model = [profile.lora_node_id, 0]
        previous_model = base_model
        selected = set(modes)
        for mode in self._CONTROL_ORDER:
            binding = profile.controls[mode]
            if mode not in selected:
                workflow.pop(binding.apply_node_id, None)
                if binding.preprocessor_node_id:
                    workflow.pop(binding.preprocessor_node_id, None)
                continue
            image_link: list[Any] = control_image_link
            if binding.preprocessor_node_id:
                self._set_input(
                    workflow,
                    binding.preprocessor_node_id,
                    binding.preprocessor_image_input,
                    control_image_link,
                )
                preprocessor = workflow.get(binding.preprocessor_node_id)
                preprocessor_inputs = (
                    preprocessor.get("inputs")
                    if isinstance(preprocessor, dict)
                    else None
                )
                if isinstance(preprocessor_inputs, dict) and "resolution" in preprocessor_inputs:
                    requested_resolution = max(width, height)
                    preprocessor_inputs["resolution"] = min(
                        2048,
                        max(512, int(round(requested_resolution / 64) * 64)),
                    )
                image_link = [binding.preprocessor_node_id, 0]
            self._set_input(
                workflow,
                binding.apply_node_id,
                binding.model_input,
                previous_model,
            )
            self._set_input(
                workflow,
                binding.apply_node_id,
                binding.image_input,
                image_link,
            )
            self._set_input(
                workflow,
                binding.apply_node_id,
                binding.strength_input,
                self._mode_strength(binding.default_strength, len(modes)),
            )
            apply_node = workflow.get(binding.apply_node_id)
            apply_inputs = (
                apply_node.get("inputs") if isinstance(apply_node, dict) else None
            )
            if isinstance(apply_inputs, dict):
                if (
                    binding.default_start_percent is not None
                    and binding.start_input in apply_inputs
                ):
                    apply_inputs[binding.start_input] = binding.default_start_percent
                if (
                    binding.default_end_percent is not None
                    and binding.end_input in apply_inputs
                ):
                    apply_inputs[binding.end_input] = binding.default_end_percent
            previous_model = [binding.apply_node_id, 0]

        self._set_input(workflow, target.node_id, target.input_name, previous_model)
        pipeline = str(
            options.pipeline
            or getattr(self._settings, "default_generation_pipeline", "rtx")
            or "rtx"
        ).strip().casefold()
        if pipeline == "base":
            for node_id in ("100", "101", "102", "103", "458", "552"):
                workflow.pop(node_id, None)
            preferred_nodes = ["88"]
        elif pipeline == "rtx":
            for node_id in ("88", "100", "101", "102", "103"):
                workflow.pop(node_id, None)
            preferred_nodes = ["458"]
        elif pipeline == "iterative":
            for node_id in ("88", "458", "552"):
                workflow.pop(node_id, None)
            self._set_input(workflow, "100", "model", previous_model)
            iterative_node = workflow.get("101")
            iterative_inputs = (
                iterative_node.get("inputs")
                if isinstance(iterative_node, dict)
                else None
            )
            if isinstance(iterative_inputs, dict):
                iterative_inputs["upscale_factor"] = self._settings.iterative_scale
                iterative_inputs["steps"] = self._settings.iterative_steps
            iterative_sampler = workflow.get("100")
            iterative_sampler_inputs = (
                iterative_sampler.get("inputs")
                if isinstance(iterative_sampler, dict)
                else None
            )
            if isinstance(iterative_sampler_inputs, dict):
                iterative_sampler_inputs["denoise"] = (
                    options.denoise
                    if options.denoise is not None
                    else self._settings.iterative_denoise
                )
            preferred_nodes = ["103"]
        else:
            raise WorkflowError(f"底图控制不支持生成管线: {pipeline}")
        return workflow, seed, preferred_nodes


class InpaintWorkflowBuilder:
    """Build an Anima image-plus-mask redraw workflow."""

    def __init__(self, workflow_path: Path, settings: PluginSettings):
        self._workflow_path = workflow_path
        self._settings = settings
        self._template = WorkflowBuilder._load_workflow(workflow_path)
        try:
            self._profile = load_workflow_profile(workflow_path, settings)
        except WorkflowProfileError as exc:
            raise WorkflowError(f"工作流档案无效: {exc}") from exc
        if (
            self._profile.task_type != "inpaint"
            or self._profile.input_image is None
            or self._profile.mask_image is None
            or self._profile.prompt is None
        ):
            raise WorkflowError("当前工作流不是完整的重绘工作流")
        for variant in self._profile.output_variants.values():
            for node_id in variant.preferred_node_ids:
                if node_id not in self._template:
                    raise WorkflowError(f"工作流缺少输出节点 {node_id}")

    @property
    def profile(self) -> WorkflowProfile:
        return self._profile

    def build(
        self,
        image_name: str,
        mask_name: str,
        options: GenerationOptions,
    ) -> tuple[dict[str, Any], int, list[str]]:
        workflow = copy.deepcopy(self._template)
        seed = options.seed if options.seed is not None else secrets.randbelow(MAX_SEED)

        input_binding = self._profile.input_image
        mask_binding = self._profile.mask_image
        assert input_binding is not None and mask_binding is not None
        WorkflowBuilder._set_input(
            workflow,
            input_binding.node_id,
            input_binding.input_name,
            image_name,
        )
        WorkflowBuilder._set_input(
            workflow,
            mask_binding.node_id,
            mask_binding.input_name,
            mask_name,
        )

        unet_binding = self._profile.unet
        if self._settings.unet_model_name and unet_binding is not None:
            WorkflowBuilder._set_input(
                workflow,
                unet_binding.node_id,
                unet_binding.input_name,
                self._settings.unet_model_name,
            )

        prompt_binding = self._profile.prompt
        assert prompt_binding is not None
        WorkflowBuilder._set_input(
            workflow,
            prompt_binding.node_id,
            prompt_binding.input_name,
            options.prompt.strip(),
        )
        if options.dynamic_loras:
            if not self._profile.lora_node_id:
                raise WorkflowError("当前重绘工作流没有动态 LoRA 节点")
            inject_loras(
                workflow,
                self._profile.lora_node_id,
                options.dynamic_loras,
                mode=(
                    options.lora_injection_mode
                    or self._settings.dynamic_lora_mode
                ),
            )

        if options.negative_prompt and self._profile.negative is not None:
            binding = self._profile.negative
            node = workflow.get(binding.node_id)
            inputs = node.get("inputs") if isinstance(node, dict) else None
            if not isinstance(inputs, dict):
                raise WorkflowError("重绘负面提示词节点无效")
            original = str(inputs.get(binding.input_name, "")).strip()
            inputs[binding.input_name] = ", ".join(
                part
                for part in (original, options.negative_prompt.strip())
                if part
            )

        for binding in self._profile.seed_bindings:
            WorkflowBuilder._set_input(
                workflow,
                binding.node_id,
                binding.input_name,
                seed,
            )
        for binding in self._profile.samplers:
            node = workflow.get(binding.node_id)
            inputs = node.get("inputs") if isinstance(node, dict) else None
            if not isinstance(inputs, dict):
                continue
            if options.steps is not None:
                inputs[binding.steps_input] = options.steps
            if options.cfg is not None:
                inputs[binding.cfg_input] = options.cfg
            if options.denoise is not None:
                inputs[binding.denoise_input] = options.denoise

        variant = self._profile.active_output
        for node_id in variant.prune_node_ids:
            workflow.pop(node_id, None)
        return workflow, seed, list(variant.preferred_node_ids)


def parse_generation_options(
    command_text: str,
    *,
    mode_context: str = "inpaint",
) -> GenerationOptions:
    """解析 `/anima draw` 后的提示词和选项。

    支持 `--negative`、`--seed`、`--size`、`--steps`、`--cfg`、
    `--pipeline`、`--denoise`、`--upscale`、`--no-upscale`、`--llm [u|ultra]`、
    `--raw`、`--preset` 与重绘使用的 `--mode`。`mode_context` 为
    ``semantic_redraw`` 时，`--mode` 改为解析 preserve/balanced/free。
    含空格的负面词需要使用引号。
    """
    source_tokens = _tokenize_generation_command(command_text)
    tokens = [token.value for token in source_tokens]
    alias_context = {
        "generation": CONTEXT_GENERATION,
        "inpaint": CONTEXT_INPAINT,
        "semantic_redraw": CONTEXT_SEMANTIC_REDRAW,
    }.get(mode_context)
    if alias_context is None:
        raise ValueError(f"未知参数解析上下文: {mode_context}")
    tokens = list(normalize_command_aliases(tokens, context=alias_context))

    prompt_parts: list[str] = []
    negative_prompt = ""
    seed = None
    width = None
    height = None
    steps = None
    cfg = None
    enable_upscale = None
    use_prompt_llm = None
    prompt_expansion_mode = "standard"
    prompt_edit_mode = ""
    character_change_requested = False
    raw_prompt_requested = False
    character_swap_preview = False
    character_swap_mode = ""
    character_swap_use_target_lora = None
    character_swap_target_lora_strength = None
    lora_preset = ""
    pipeline = ""
    inpaint_mode = ""
    semantic_redraw_mode = ""
    denoise = None
    control_modes: list[str] = []
    index = 0

    def require_value(option: str) -> str:
        nonlocal index
        if index + 1 >= len(tokens):
            raise ValueError(f"{option} 缺少参数")
        index += 1
        return tokens[index]

    while index < len(tokens):
        token = tokens[index]
        if token == "--negative":
            negative_prompt = require_value(token)
        elif token == "--seed":
            value = require_value(token)
            try:
                seed = int(value)
            except ValueError as exc:
                raise ValueError("--seed 必须是整数") from exc
            if seed < 0 or seed > MAX_SEED:
                raise ValueError(f"--seed 必须在 0 到 {MAX_SEED} 之间")
        elif token == "--size":
            value = require_value(token).lower().replace("×", "x")
            try:
                width_text, height_text = value.split("x", 1)
                width, height = int(width_text), int(height_text)
            except (ValueError, AttributeError) as exc:
                raise ValueError("--size 格式应为 宽x高，例如 832x1216") from exc
            if not (
                MIN_IMAGE_SIDE <= width <= MAX_IMAGE_SIDE
                and MIN_IMAGE_SIDE <= height <= MAX_IMAGE_SIDE
            ):
                raise ValueError(
                    f"宽高必须在 {MIN_IMAGE_SIDE} 到 {MAX_IMAGE_SIDE} 之间"
                )
        elif token == "--steps":
            try:
                steps = int(require_value(token))
            except ValueError as exc:
                raise ValueError("--steps 必须是整数") from exc
            if not 1 <= steps <= MAX_STEPS:
                raise ValueError(f"--steps 必须在 1 到 {MAX_STEPS} 之间")
        elif token == "--cfg":
            try:
                cfg = float(require_value(token))
            except ValueError as exc:
                raise ValueError("--cfg 必须是数字") from exc
            if not 0 <= cfg <= MAX_CFG:
                raise ValueError(f"--cfg 必须在 0 到 {MAX_CFG:g} 之间")
        elif token == "--pipeline":
            pipeline = require_value(token).strip().lower()
            aliases = {
                "原图": "base",
                "base": "base",
                "rtx": "rtx",
                "放大": "rtx",
                "iterative": "iterative",
                "迭代": "iterative",
                "迭代放大": "iterative",
            }
            pipeline = aliases.get(pipeline, "")
            if not pipeline:
                raise ValueError("--pipeline 仅支持 base、rtx 或 iterative")
        elif token == "--denoise":
            try:
                denoise = float(require_value(token))
            except ValueError as exc:
                raise ValueError("--denoise 必须是数字") from exc
            if not 0.0 <= denoise <= 1.0:
                raise ValueError("--denoise 必须在 0 到 1 之间")
        elif token == "--mode":
            if mode_context == "generation":
                raise ValueError(
                    "--mode 不用于普通生图；文本换角请使用 --m k/t，"
                    "底图控制请使用 --m p/d/l/r"
                )
            raw_mode = require_value(token).strip().casefold()
            if mode_context == "semantic_redraw":
                aliases = {
                    "preserve": "preserve",
                    "保守": "preserve",
                    "保持": "preserve",
                    "balanced": "balanced",
                    "balance": "balanced",
                    "平衡": "balanced",
                    "默认": "balanced",
                    "free": "free",
                    "自由": "free",
                    "重画": "free",
                }
                semantic_redraw_mode = aliases.get(raw_mode, "")
                if not semantic_redraw_mode:
                    raise ValueError(
                        "--mode 仅支持 preserve、balanced 或 free"
                    )
            else:
                aliases = {
                    "quick": "quick",
                    "快速": "quick",
                    "局部": "quick",
                    "lanpaint": "lanpaint",
                    "精细": "lanpaint",
                    "多轮": "lanpaint",
                }
                inpaint_mode = aliases.get(raw_mode, "")
                if not inpaint_mode:
                    raise ValueError("--mode 仅支持 quick 或 lanpaint")
        elif token == "--swap-mode":
            if mode_context != "generation":
                raise ValueError("换角模式选项只用于 /画图 与 /画图no")
            character_swap_mode = require_value(token).strip().casefold()
            if character_swap_mode not in {"keep-outfit", "target-outfit"}:
                raise ValueError("换角模式只支持 keep-outfit 或 target-outfit")
        elif token == "--swap-weight":
            if mode_context != "generation":
                raise ValueError("换角 LoRA 权重只用于 /画图 与 /画图no")
            try:
                character_swap_target_lora_strength = float(require_value(token))
            except ValueError as exc:
                raise ValueError("--weight 必须是数字") from exc
            if not 0.55 <= character_swap_target_lora_strength <= 0.75:
                raise ValueError("换角角色 LoRA 权重必须在 0.55 到 0.75 之间")
        elif token == "--swap-preview":
            if mode_context != "generation":
                raise ValueError("换角预览只用于 /画图 与 /画图no")
            character_swap_preview = True
        elif token == "--swap-no-character-lora":
            if mode_context != "generation":
                raise ValueError("禁用目标角色 LoRA 只用于 /画图 与 /画图no")
            character_swap_use_target_lora = False
        elif token == "--control-mode":
            if mode_context != "generation":
                raise ValueError("--control-mode 只用于底图控制生成")
            control_mode = require_value(token).strip().casefold()
            if control_mode not in {"pose", "depth", "lineart", "reference"}:
                raise ValueError(
                    "--control-mode 仅支持 pose、depth、lineart 或 reference"
                )
            if control_mode not in control_modes:
                control_modes.append(control_mode)
        elif token == "--upscale":
            enable_upscale = True
        elif token == "--no-upscale":
            enable_upscale = False
        elif token in {"--llm", "--llm-character-change"}:
            if token == "--llm-character-change" and mode_context != "generation":
                raise ValueError("--llmcc/--lcc 仅用于 /画图 与 /画图no")
            use_prompt_llm = True
            if token == "--llm-character-change":
                prompt_edit_mode = "character_change"
                character_change_requested = True
            expansion_aliases = {
                "s": "standard",
                "standard": "standard",
                "normal": "standard",
                "普通": "standard",
                "简洁": "standard",
                "u": "ultra",
                "ultra": "ultra",
                "complex": "ultra",
                "ornate": "ultra",
                "复杂": "ultra",
                "华丽": "ultra",
                "高质量": "ultra",
            }
            character_change_aliases = {
                "c",
                "cc",
                "char-change",
                "char_change",
                "character-change",
                "character_change",
            }
            while index + 1 < len(tokens) and not tokens[index + 1].startswith("--"):
                raw_mode = tokens[index + 1].strip().casefold()
                normalized_expansion_mode = expansion_aliases.get(raw_mode)
                if normalized_expansion_mode:
                    prompt_expansion_mode = normalized_expansion_mode
                    index += 1
                    continue
                if raw_mode in character_change_aliases and mode_context == "generation":
                    prompt_edit_mode = "character_change"
                    character_change_requested = True
                    index += 1
                    continue
                if (
                    mode_context == "generation"
                    and raw_mode == "char"
                    and index + 2 < len(tokens)
                    and tokens[index + 2].strip().casefold() == "change"
                ):
                    prompt_edit_mode = "character_change"
                    character_change_requested = True
                    index += 2
                    continue
                break
        elif token in {"--raw", "--no-llm"}:
            raw_prompt_requested = True
            use_prompt_llm = False
            prompt_expansion_mode = "standard"
            prompt_edit_mode = ""
        elif token in {"--preset", "--lora-preset"}:
            lora_preset = require_value(token).strip()
        elif token.startswith("--"):
            raise ValueError(f"未知选项: {token}")
        else:
            prompt_parts.append(token)
        index += 1

    prompt_indexes = _source_prompt_token_indexes(
        source_tokens,
        mode_context=mode_context,
        character_change_requested=character_change_requested,
    )
    source_prompt_parts = [source_tokens[index].value for index in prompt_indexes]
    if source_prompt_parts != prompt_parts:
        # This is an internal parser invariant: alias handling may rewrite
        # options and their values, but it must never rewrite prompt tokens.
        raise ValueError("绘图参数解析内部状态不一致")
    prompt = _rebuild_generation_prompt(command_text, source_tokens, prompt_indexes)
    if not prompt:
        raise ValueError("请输入绘图提示词")
    swap_specific_requested = bool(
        character_swap_preview
        or character_swap_mode
        or character_swap_use_target_lora is not None
        or character_swap_target_lora_strength is not None
    )
    if character_change_requested and raw_prompt_requested:
        raise ValueError("文本换角不能与 --raw/--no-llm 同时使用")
    if swap_specific_requested and prompt_edit_mode != "character_change":
        raise ValueError(
            "换角专用选项需要同时使用 --llm c、--llmcc 或 --lcc"
        )
    return GenerationOptions(
        prompt=prompt,
        negative_prompt=negative_prompt,
        seed=seed,
        width=width,
        height=height,
        steps=steps,
        cfg=cfg,
        enable_upscale=enable_upscale,
        use_prompt_llm=use_prompt_llm,
        prompt_expansion_mode=prompt_expansion_mode,
        prompt_edit_mode=prompt_edit_mode,
        character_swap_preview=character_swap_preview,
        character_swap_mode=character_swap_mode,
        character_swap_use_target_lora=character_swap_use_target_lora,
        character_swap_target_lora_strength=(
            character_swap_target_lora_strength
        ),
        lora_preset=lora_preset,
        pipeline=pipeline,
        inpaint_mode=inpaint_mode,
        semantic_redraw_mode=semantic_redraw_mode,
        denoise=denoise,
        control_modes=tuple(control_modes),
    )
