from __future__ import annotations

import asyncio
import copy
import logging
import secrets
import uuid
import time
from datetime import datetime
from typing import Any

from .comfy import ComfyAborted, ComfyError, extract_images, extract_positive_prompt
from .catalog import PromptCatalog, resolve_history_selection, split_prompt
from .history import HistoryStore
from .prompt_rules import PromptRuleStore
from .model_profiles import get_model_profile
from .lora_binding import (append_trigger_words, binding_policy, manual_settings,
                           resolve_character_loras, runtime_snapshot, validate_frozen_resources)
from .graph_utils import active_lora_pipelines
from .pipeline_v3 import STAGE_LABELS
from .workflow import MAX_SAMPLE_SEED, WorkflowError, WorkflowTemplates, migrate_settings, validate_loras, validate_settings


logger = logging.getLogger(__name__)


class BatchConflict(RuntimeError):
    pass


MAX_QUEUE = 20
SHUTDOWN_GRACE = 2.0
CANCEL_PROMPT_TIMEOUT = 5.0
REGENERATION_MODES = {"replay", "prompt_variant", "content_redraw", "settings_reroll"}
PROMPT_SECTIONS = ("character", "clothing", "pose", "background", "expression")
MAX_EXECUTION_NONCE = 2**53 - 1
_last_execution_nonce = 0


def new_execution_context() -> dict[str, Any]:
    """Allocate an execution identity without consuming either sampling seed.

    A process-wide monotonic microsecond counter also separates images created
    in the same clock tick or after a clock adjustment. The UUID identifies
    the execution independently across server processes and restarts.
    """
    global _last_execution_nonce
    clock_nonce = max(0, time.time_ns() // 1_000) % (MAX_EXECUTION_NONCE + 1)
    _last_execution_nonce = max(clock_nonce, _last_execution_nonce + 1) % (MAX_EXECUTION_NONCE + 1)
    return {"execution_id": uuid.uuid4().hex, "execution_nonce": _last_execution_nonce}


def validate_seeds(value: Any) -> dict[str, int] | None:
    """固定图像种子；提示词种子可省略以保持每张随机，双种子用于复现。"""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise WorkflowError("seeds 必须是对象")
    unknown = set(value) - {"sample_seed", "prompt_seed"}
    if unknown:
        raise WorkflowError(f"seeds 包含未知参数: {', '.join(sorted(unknown))}")
    result: dict[str, int] = {}
    for name, maximum in (("sample_seed", MAX_SAMPLE_SEED), ("prompt_seed", 2**31 - 1)):
        if name == "prompt_seed" and name not in value:
            continue
        raw = value.get(name)
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise WorkflowError(f"seeds.{name} 必须是整数")
        if not 0 <= raw <= maximum:
            raise WorkflowError(f"seeds.{name} 必须在 0-{maximum} 之间")
        result[name] = raw
    return result


class BatchManager:
    def __init__(
        self,
        templates: WorkflowTemplates,
        history: HistoryStore,
        comfy: Any,
        catalog: PromptCatalog | None = None,
        prompt_rules: PromptRuleStore | None = None,
    ):
        self.templates = templates
        self.history = history
        self.comfy = comfy
        self.task: asyncio.Task[None] | None = None
        self.state: dict[str, Any] | None = None
        self.stop_requested = False
        self.client_id = str(uuid.uuid4())
        self.catalog = catalog
        self.prompt_rules = prompt_rules
        self.lora_library = None
        # 排队中的批次(仅内存;WebUI 重启后队列清空,历史记录只在真正开跑时创建)。
        self.queue: list[dict[str, Any]] = []
        self.shutting_down = False
        # 实时进度:由 _monitor_progress 通过 ComfyUI websocket 更新。
        self.preview: tuple[int, str, bytes] | None = None
        self.preview_metadata: dict[str, Any] | None = None
        self._preview_context: tuple[str, str, str] | None = None
        self._preview_execution: tuple[str, str, str] | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._monitor_tasks: set[asyncio.Task[None]] = set()
        self._preview_seq = 0
        self._preview_epoch = 0
        self._submitted_node_stages: dict[str, tuple[str, str]] = {}
        # start 里的资源校验要 await 多次 ComfyUI 请求,期间若不上锁,
        # 第二个并发 start 也能通过 active() 检查并启动重复批次。
        self._start_lock = asyncio.Lock()
        self._shutdown_lock = asyncio.Lock()

    def active(self) -> bool:
        return bool(self.task and not self.task.done())

    def _ensure_accepting(self) -> None:
        if self.shutting_down:
            raise BatchConflict("服务正在关闭，无法接受新任务")


    async def shutdown(self) -> None:
        """Drain owned work before the HTTP client and database are closed."""
        # Set this before waiting for preflight/dispatch, so a runner finishing
        # concurrently cannot advance to another queued batch.
        self.shutting_down = True
        self.stop_requested = True
        async with self._shutdown_lock:
            async with self._start_lock:
                self.stop_requested = True
                self.queue.clear()
            try:
                task = self.task
                if task is not None:
                    if not task.done():
                        # Give an in-flight submit a chance to return its id;
                        # cancellation can then target the accepted Comfy job.
                        _, pending = await asyncio.wait({task}, timeout=SHUTDOWN_GRACE)
                        if pending:
                            task.cancel()
                    results = await asyncio.gather(task, return_exceptions=True)
                    for result in results:
                        if isinstance(result, Exception):
                            logger.warning("关闭时任务收尾失败: %s", result)
            finally:
                self._stop_monitor()
                if self._monitor_tasks:
                    await asyncio.gather(*self._monitor_tasks, return_exceptions=True)
    def _uses_v2_workflow(self) -> bool:
        """Return whether submissions use the namespaced v2 API template.

        Fake/template adapters used by older integrations do not expose an
        ``api`` mapping, so they retain the v1 compatibility settings.
        """
        api = getattr(self.templates, "api", None)
        return isinstance(api, dict) and any(":" in str(node_id) for node_id in api)

    def _bound_resources(self, settings: dict[str, Any]) -> dict[str, Any]:
        resources = {"model_profile": "anima", "unet": settings["model_name"],
                     "clip": settings["clip_name"], "vae": settings["vae_name"]}
        resources["loras"] = [item["filename"] for item in settings["loras"] if item["enabled"]]
        for stage, keys in (("pixel_hires", ("model_name",)), ("seedvr2", ("model_name", "vae_name"))):
            value = settings["upscalers"][stage]
            if value["enabled"]:
                resources.update({stage + ("_vae" if key == "vae_name" else "_model"): value[key] for key in keys})
        return resources

    @staticmethod
    def _validate_regeneration(value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise WorkflowError("regeneration 必须是对象")
        unknown = set(value) - {
            "mode",
            "frozen_positive_prompt",
            "frozen_negative_prompt",
            "fixed_selection",
            "redraw_sections",
            "original_selection",
            "prompt_seed",
            "lora_runtime",
        }
        if unknown:
            raise WorkflowError(
                f"regeneration 包含未知参数: {', '.join(sorted(unknown))}"
            )
        mode = value.get("mode")
        if not isinstance(mode, str) or mode not in REGENERATION_MODES:
            raise WorkflowError("regeneration.mode 无效")
        result = copy.deepcopy(value)
        result["mode"] = mode
        if "lora_runtime" in result and not isinstance(result["lora_runtime"], dict):
            raise WorkflowError("历史 LoRA 运行快照无效")
        if mode in {"replay", "prompt_variant"}:
            positive = str(value.get("frozen_positive_prompt") or "")
            if not positive.strip():
                raise WorkflowError("历史记录缺少可回放的最终正向提示词")
            result["frozen_positive_prompt"] = positive
            result["frozen_negative_prompt"] = str(
                value.get("frozen_negative_prompt") or ""
            )
            fixed_selection = value.get("fixed_selection") or {}
            if not isinstance(fixed_selection, dict):
                raise WorkflowError("regeneration.fixed_selection 必须是对象")
            result["fixed_selection"] = copy.deepcopy(fixed_selection)
            if mode == "prompt_variant":
                prompt_seed = value.get("prompt_seed")
                if isinstance(prompt_seed, bool) or not isinstance(prompt_seed, int):
                    raise WorkflowError("regeneration.prompt_seed 必须是整数")
                if not 0 <= prompt_seed <= 2**31 - 1:
                    raise WorkflowError("regeneration.prompt_seed 超出范围")
                result["prompt_seed"] = prompt_seed
        elif mode == "content_redraw":
            sections = value.get("redraw_sections")
            if not isinstance(sections, list) or not sections:
                raise WorkflowError("content_redraw 至少选择一个重抽维度")
            if any(not isinstance(section, str) for section in sections):
                raise WorkflowError("regeneration.redraw_sections 必须是字符串数组")
            if len(sections) != len(set(sections)):
                raise WorkflowError("重抽维度不能重复")
            invalid = set(sections) - set(PROMPT_SECTIONS)
            if invalid:
                raise WorkflowError(f"不支持的重抽维度: {', '.join(sorted(invalid))}")
            fixed_selection = value.get("fixed_selection") or {}
            original_selection = value.get("original_selection") or {}
            if not isinstance(fixed_selection, dict) or not isinstance(
                original_selection, dict
            ):
                raise WorkflowError("历史实际抽取结果无效")
            result["redraw_sections"] = list(sections)
            result["fixed_selection"] = copy.deepcopy(fixed_selection)
            result["original_selection"] = copy.deepcopy(original_selection)
        return result

    def _resolve_resource_paths(
        self, settings: dict[str, Any], resources: dict[str, Any], *, resources_resolved: bool = False,
    ) -> dict[str, Any]:
        result = copy.deepcopy(settings)
        mapping = {
            str(key).replace("\\", "/").casefold(): str(value).replace("\\", "/")
            for key, value in (getattr(self.templates, "resource_paths", {}) or {}).items()
        }

        def resolve(value: str, category: str) -> str:
            requested = str(value).replace("\\", "/")
            values = resources.get(category)
            if values is None:
                return requested if resources_resolved else mapping.get(requested.casefold(), requested)
            candidates = [str(item).replace("\\", "/") for item in values]
            exact = [item for item in candidates if item.casefold() == requested.casefold()]
            if len(exact) == 1:
                return exact[0]
            # Defaults may already contain a mapped path, and saved history
            # always does. Never reinterpret an existing explicit resource as
            # an alias, or let a replay silently switch to a moved namesake.
            if not resources_resolved:
                requested = mapping.get(requested.casefold(), requested)
                exact = [item for item in candidates if item.casefold() == requested.casefold()]
            matches = exact or (
                [item for item in candidates if item.rsplit("/", 1)[-1].casefold() == requested.casefold()]
                if not resources_resolved and "/" not in requested else []
            )
            if len(matches) == 1:
                return matches[0]
            reason = "匹配不唯一" if len(matches) > 1 else "不存在"
            label = {"models": "主模型", "clips": "CLIP", "vaes": "VAE", "upscale_models": "高清修复模型"}.get(category, category)
            raise WorkflowError(f"{label}{reason}: {requested}")

        for key, category in (("model_name", "models"), ("clip_name", "clips"), ("vae_name", "vaes")):
            result[key] = resolve(result[key], category)
        stages = result["upscalers"]
        if stages["pixel_hires"]["enabled"]:
            stages["pixel_hires"]["model_name"] = resolve(stages["pixel_hires"]["model_name"], "upscale_models")
        if stages["seedvr2"]["enabled"]:
            for key, category in (("model_name", "seedvr2_models"), ("vae_name", "seedvr2_vaes")):
                fallback = "models" if key == "model_name" else "vaes"
                stages["seedvr2"][key] = resolve(stages["seedvr2"][key], category if category in resources else fallback)
        return result

    async def resource_issues(
        self, overrides: dict[str, Any], *, resources_resolved: bool = False,
    ) -> list[dict[str, str]]:
        settings = validate_settings(overrides)
        issues: list[dict[str, str]] = []
        lora_filenames = getattr(self.comfy, "lora_filenames", None)
        available = {str(value).replace("\\", "/").casefold() for value in await lora_filenames()} if lora_filenames else set()
        for item in settings["loras"]:
            if item["enabled"] and item["filename"].replace("\\", "/").casefold() not in available:
                issues.append({"type": "lora", "name": item["filename"], "label": "LoRA"})
        inventory = getattr(self.comfy, "resource_inventory", None)
        if inventory:
            resources = await inventory()
            try:
                settings = self._resolve_resource_paths(settings, resources, resources_resolved=resources_resolved)
            except WorkflowError as error:
                issues.append({"type": "resource", "name": str(error), "label": "模型路径"})
            issues.extend(self._resource_inventory_issues(settings, resources))
        return issues

    @staticmethod
    def _resource_inventory_issues(
        settings: dict[str, Any],
        resources: dict[str, Any],
        *,
        legacy_requested: bool = False,
    ) -> list[dict[str, str]]:
        """Validate only resources used by the selected pipeline graph.

        Older ComfyUI clients return only UNet/sampler inventories.  Empty
        optional lists therefore mean "not advertised" and are tolerated for
        disabled stages, while an explicitly enabled plugin stage requires
        its matching node and any stage-specific model resources when the
        server reports those inventories.
        """

        def norm(value: Any) -> str:
            return str(value).replace("\\", "/").casefold()

        def contains(values: Any, value: Any) -> bool:
            if values is None:
                return False
            if isinstance(values, dict):
                values = values.keys()
            if isinstance(values, (str, bytes)):
                values = [values]
            try:
                wanted = norm(value)
                wanted_base = wanted.rsplit("/", 1)[-1]
                available = {norm(item) for item in values}
                # ComfyUI's object_info returns paths relative to the model
                # root (for example ``Anima/anima-base-v1.0.safetensors``),
                # Requests may still use an unambiguous bare filename;
                # submission resolves and freezes its relative path.
                return wanted in available or (
                    "/" not in wanted and sum(item.rsplit("/", 1)[-1] == wanted_base for item in available) == 1
                )
            except TypeError:
                return False

        def check(kind: str, label: str, name: Any, values: Any, *, required: bool = True) -> None:
            # ``None`` means an old client did not advertise this category;
            # an explicit empty list means it advertised the category but the
            # requested resource is absent.
            if required and values is not None and not contains(values, name):
                issues.append({"type": kind, "name": str(name), "label": label})

        issues: list[dict[str, str]] = []
        profile = get_model_profile(settings.get("model_profile", "anima"))
        check("model", "主模型", settings["model_name"], resources.get("models"))
        check("clip", "CLIP", settings["clip_name"], resources.get("clips"))
        check("vae", "VAE", settings["vae_name"], resources.get("vaes"))
        check("sampler", "Sampler", settings["sampler_name"], resources.get("samplers"))
        check("scheduler", "Scheduler", settings["scheduler"], resources.get("schedulers"))

        # These four controls are the only post-processing stages exposed by
        # v2.  Their node flags come from /object_info and are checked even
        # when the optional model inventory is not exposed by ComfyUI.
        upscalers = settings.get("upscalers") or {}
        v3 = settings.get("pipeline_version", 2) >= 3
        nodes = resources.get("nodes")
        plugins = resources.get("plugins")

        def advertised_node(container: Any, *names: str) -> bool | None:
            """Preserve absent-vs-explicit-missing node inventory semantics."""
            if container is None:
                return None
            if isinstance(container, dict):
                return any(container.get(name) is True for name in names)
            try:
                available = {str(item).casefold() for item in container}
            except TypeError:
                return False
            return any(name.casefold() in available for name in names)

        def stage_node(node_name: str, kind: str, label: str, plugin: str) -> None:
            # Exact node inventories are authoritative. A broadly named plugin
            # (in particular DLSS) cannot stand in for a different VSR node.
            advertised = resources.get("node_types")
            if isinstance(advertised, list):
                available = node_name in advertised
            elif isinstance(nodes, dict) and node_name in nodes:
                available = nodes[node_name] is True
            else:
                available = advertised_node(nodes, node_name)
                plugin_available = advertised_node(plugins, plugin)
                if available is None and plugin_available is None:
                    return
                available = available is True or plugin_available is True
            if not available:
                issues.append({"type": kind, "name": node_name, "label": label})

        pixel = upscalers.get("pixel_hires") or {}
        if v3 and pixel.get("enabled"):
            check("upscale_model", "像素高清模型", pixel.get("model_name"), resources.get("upscale_models"))
            stage_node("UpscaleModelLoader", "pixel_hires_node", "像素高清模型加载节点", "pixel_hires")
            stage_node("ImageUpscaleWithModel", "pixel_hires_node", "像素高清放大节点", "pixel_hires")
        post_sampler = upscalers.get("post_sampler") or {}
        if v3 and post_sampler.get("enabled"):
            check("post_sampler", "整图后采样 Sampler", post_sampler.get("sampler_name"), resources.get("samplers"))
            check("post_scheduler", "整图后采样 Scheduler", post_sampler.get("scheduler"), resources.get("schedulers"))

        latent = upscalers.get("latent") or {}
        if settings.get("pipeline_version", 2) >= 4 and latent.get("enabled"):
            latent_sampler = latent.get("sampler") or {}
            check("latent_sampler", "潜空间重采样 Sampler", latent_sampler.get("sampler_name"), resources.get("samplers"))
            check("latent_scheduler", "潜空间重采样 Scheduler", latent_sampler.get("scheduler"), resources.get("schedulers"))
        second_sampler = latent.get("second_sampler") or {}
        if not v3 and latent.get("enabled") and second_sampler.get("enabled"):
            check(
                "second_sampler",
                "潜空间后二次采样 Sampler",
                second_sampler.get("sampler_name"),
                resources.get("samplers"),
            )
            check(
                "second_scheduler",
                "潜空间后二次采样 Scheduler",
                second_sampler.get("scheduler"),
                resources.get("schedulers"),
            )

        seedvr2 = upscalers.get("seedvr2") or {}
        if seedvr2.get("enabled"):
            node_available = advertised_node(
                nodes,
                "SeedVR2",
                "SeedVR2Conditioning",
                "SeedVR2Preprocess",
                "SeedVR2PostProcessing",
            )
            plugin_available = advertised_node(plugins, "seedvr2")
            if (node_available is not None or plugin_available is not None) and not (
                node_available is True or plugin_available is True
            ):
                issues.append({"type": "seedvr2_node", "name": "SeedVR2", "label": "SeedVR2 节点"})
            seed_name = seedvr2.get("model_name") or profile["seedvr2_model"]
            available = resources.get("seedvr2_models")
            if available is None:
                available = resources.get("models")
            if available is not None and not contains(available, seed_name):
                issues.append({"type": "seedvr2", "name": str(seed_name), "label": "SeedVR2 模型"})
            seed_vae = seedvr2.get("vae_name") or profile["seedvr2_vae"]
            available_vae = resources.get("seedvr2_vaes")
            if available_vae is None:
                available_vae = resources.get("vaes")
            if available_vae is not None and not contains(available_vae, seed_vae):
                issues.append({"type": "seedvr2_vae", "name": str(seed_vae), "label": "SeedVR2 VAE"})
            sampler = seedvr2.get("sampler") or {}
            check("seedvr2_sampler", "SeedVR2 Sampler", sampler.get("sampler_name"), resources.get("samplers"))
            check("seedvr2_scheduler", "SeedVR2 Scheduler", sampler.get("scheduler"), resources.get("schedulers"))
        nvidia = upscalers.get("nvidia") or {}
        if v3 and nvidia.get("enabled"):
            stage_node("RTXVideoSuperResolution", "nvidia_node", "N 卡 VSR 节点", "nvidia_upscale")
        elif nvidia.get("enabled"):
            node_available = advertised_node(
                nodes, "NvidiaUpscale", "RTXVideoSuperResolution"
            )
            plugin_available = advertised_node(plugins, "nvidia_upscale")
            if (node_available is not None or plugin_available is not None) and not (
                node_available is True or plugin_available is True
            ):
                issues.append({"type": "nvidia_node", "name": "RTXVideoSuperResolution", "label": "N 卡放大节点"})
        if v3 and (upscalers.get("dlss") or {}).get("enabled"):
            stage_node("DLSS5NeuralRender", "dlss_node", "DLSS 节点", "dlss")
        face = upscalers.get("face_refine") or {}
        if face.get("enabled"):
            node_available = advertised_node(nodes, "ForbiddenVisionFaceProcessorIntegrated")
            plugin_available = advertised_node(plugins, "forbidden_vision_face")
            if (node_available is not None or plugin_available is not None) and not (
                node_available is True or plugin_available is True
            ):
                issues.append(
                    {
                        "type": "face_refine_node",
                        "name": "ForbiddenVisionFaceProcessorIntegrated",
                        "label": "脸部细化节点",
                    }
                )
            check("face_refine_sampler", "脸部细化 Sampler", face.get("sampler"), resources.get("samplers"))
            check("face_refine_scheduler", "脸部细化 Scheduler", face.get("scheduler"), resources.get("schedulers"))

        # Keep v1 resource diagnostics meaningful when a caller explicitly
        # submits a legacy settings object during migration tests.
        if legacy_requested and "second_sampler" in settings and (settings.get("second_sampler") or {}).get("enabled"):
            second = settings["second_sampler"]
            check("second_sampler", "2ndSampler Sampler", second["sampler_name"], resources.get("samplers"))
            check("second_scheduler", "2ndSampler Scheduler", second["scheduler"], resources.get("schedulers"))
        if legacy_requested and "hires" in settings and (settings.get("hires") or {}).get("enabled"):
            check("upscale_model", "高清模型", settings["hires"]["model_name"], resources.get("upscale_models"))
        if v3 and isinstance(resources.get("node_types"), list):
            from .pipeline_v3 import required_node_classes
            advertised = set(resources["node_types"])
            missing = {item["name"] for item in issues if item["type"].endswith("_node")}
            for node_name in sorted(required_node_classes(settings) - advertised - missing):
                issues.append({"type": "pipeline_node", "name": node_name, "label": "当前处理链节点"})
        from .sampling import random_sampling_resource_issues
        issues.extend(random_sampling_resource_issues(settings, resources))
        return issues











    async def start(
        self,
        overrides: dict[str, Any],
        seeds: dict[str, int] | None = None,
        regeneration: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with self._start_lock:
            self._ensure_accepting()
            ensure_writable = getattr(self.history, "ensure_writable", None)
            if ensure_writable is not None:
                ensure_writable()
            regeneration = self._validate_regeneration(regeneration)
            overrides = copy.deepcopy(overrides)
            if binding_policy(regeneration) == "current":
                overrides = manual_settings(overrides, runtime_snapshot(regeneration))
            normalized_overrides = overrides
            if self.prompt_rules is not None and not (
                regeneration and regeneration.get("frozen_positive_prompt")
            ):
                try:
                    profile = get_model_profile(overrides.get("model_profile", "anima"))["id"]
                except ValueError as error:
                    raise WorkflowError(str(error)) from error
                normalized_overrides, _ = self.prompt_rules.normalize_settings(
                    overrides, model_profile=profile, apply_custom=False,
                )
                if profile == "anima":
                    if "negative_prompt" in overrides:
                        negative = self.prompt_rules.normalize_fields(
                            {"negative_prompt": overrides["negative_prompt"]}, model_profile=profile
                        )
                        normalized_overrides.update(negative["fields"])
                    managed, _ = self.prompt_rules.normalize_settings(
                        {"lora_managed_triggers": overrides.get("lora_managed_triggers", [])},
                        model_profile=profile,
                    )
                    normalized_overrides["lora_managed_triggers"] = managed["lora_managed_triggers"]
            settings = validate_settings(normalized_overrides)
            v2_workflow = self._uses_v2_workflow()
            if v2_workflow:
                # Keep queue entries, batch snapshots and history on the v2
                # schema even when an embedded caller submits DEFAULT_SETTINGS
                # with the four deprecated aliases attached.
                settings = migrate_settings(settings)
            seeds = validate_seeds(seeds)
            if self.catalog and not (
                regeneration and regeneration.get("frozen_positive_prompt")
            ):
                self.catalog.validate_settings(settings)
            lora_filenames = getattr(self.comfy, "lora_filenames", None)
            if not lora_filenames:
                raise ComfyError("ComfyUI 客户端无法读取 LoRA 列表")
            available_loras = await lora_filenames()
            active = active_lora_pipelines(settings)
            if "primary" in active:
                settings["loras"] = validate_loras(
                    settings, available_loras, allow_basename=not bool(regeneration),
                )
            resource_inventory = getattr(self.comfy, "resource_inventory", None)
            if resource_inventory:
                resources = await resource_inventory()
                settings = self._resolve_resource_paths(settings, resources, resources_resolved=bool(regeneration))
                issues = self._resource_inventory_issues(
                    settings,
                    resources,
                    legacy_requested=(
                        not v2_workflow
                        and settings.get("pipeline_version", 2) < 3
                        and any(
                            key in (overrides or {})
                            for key in ("hires", "detailers", "cfg_zero_star", "second_sampler")
                        )
                    ),
                )
                if issues:
                    first = issues[0]
                    legacy_messages = {
                        "model": "主模型不存在",
                        "sampler": "Sampler 不可用",
                        "scheduler": "Scheduler 不可用",
                        "second_sampler": "2ndSampler Sampler 不可用",
                        "second_scheduler": "2ndSampler Scheduler 不可用",
                        "upscale_model": "高清修复模型不存在",
                    }
                    prefix = legacy_messages.get(first["type"], f"{first['label']}不可用")
                    raise WorkflowError(f"{prefix}: {first['name']}")
            binding_snapshot = None
            if (self.lora_library is not None and settings.get("workbench") == "random"
                    and settings["model_profile"] == "anima" and settings.get("random_character")
                    and binding_policy(regeneration) == "current"):
                binding_snapshot = copy.deepcopy(await self.lora_library.binding_snapshot())
            self._ensure_accepting()
            if self.active():
                # 有批次在跑:进入队列,当前批次结束后自动接续。
                if len(self.queue) >= MAX_QUEUE:
                    raise BatchConflict(f"队列已满(最多 {MAX_QUEUE} 个)")
                entry = {
                    "queue_id": f"queue_{uuid.uuid4().hex[:8]}",
                    "settings": settings,
                    "seeds": seeds,
                    "regeneration": regeneration,
                    "binding_snapshot": binding_snapshot,
                    "kind": "generation",
                }
                self.queue.append(entry)
                return {
                    "status": "queued",
                    "queue_id": entry["queue_id"],
                    "position": len(self.queue),
                    "count": settings["count"],
                }
            await self._begin_batch(settings, seeds, regeneration, binding_snapshot)
            return self.snapshot()

    async def _begin_batch(
        self,
        settings: dict[str, Any],
        seeds: dict[str, int] | None,
        regeneration: dict[str, Any] | None = None,
        binding_snapshot: dict[str, Any] | None = None,
    ) -> None:
        batch_id = uuid.uuid4().hex[:12]
        self.stop_requested = False
        self._clear_preview()
        self._submitted_node_stages = {}
        self.state = {
            "id": batch_id,
            "kind": "generation",
            "stage": "generating",
            "status": "running",
            "total": settings["count"],
            "completed": 0,
            "current": 0,
            "prompt_id": "",
            "error": "",
            "settings": settings,
        }
        await self.history.create_batch(batch_id, settings["count"], settings)
        self.task = asyncio.create_task(self._run(batch_id, settings, seeds, regeneration, binding_snapshot))
        self._ensure_monitor()

    def queue_snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "queue_id": entry["queue_id"],
                "position": index + 1,
                "count": entry["settings"].get("count", 1),
                "model_name": entry["settings"].get("model_name", ""),
                "kind": entry.get("kind", "generation"),
                "workbench": entry["settings"].get("workbench", "random"),
            }
            for index, entry in enumerate(self.queue)
        ]

    def _resolve_prompt(self, settings: dict[str, Any], prompt_seed: int) -> dict[str, Any]:
        """Resolve the five Anima pools with the submitted prompt seed."""
        if self.catalog:
            return self.catalog.resolve_prompt(settings, prompt_seed)
        return {"composer_prompt": "", "full_prompt": "", "selected": {}}

    def remove_queued(self, queue_id: str) -> bool:
        previous = len(self.queue)
        self.queue = [entry for entry in self.queue if entry["queue_id"] != queue_id]
        return len(self.queue) < previous

    def snapshot(self) -> dict[str, Any] | None:
        return dict(self.state) if self.state else None

    async def request_stop(self, batch_id: str, clear_queue: bool = True) -> dict[str, Any]:
        async with self._start_lock:
            return await self._request_stop(batch_id, clear_queue)

    async def _request_stop(self, batch_id: str, clear_queue: bool = True) -> dict[str, Any]:
        if not self.state or self.state["id"] != batch_id:
            raise KeyError(batch_id)
        if clear_queue:
            self.queue.clear()
        if self.active() and self.state.get("status") in {"running", "stopping"}:
            self.stop_requested = True
            self.state["status"] = "stopping"
            await self.history.update_batch(batch_id, status="stopping")
            if self.state.get("prompt_id"):
                await self._interrupt_current()
        return self.snapshot()

    async def wait(self) -> dict[str, Any] | None:
        # 队列自动接续会替换 self.task,循环等待直到全部批次收尾。
        while self.task and not self.task.done():
            await self.task
            await asyncio.sleep(0)
        return self.snapshot()

    async def _binding_inventory(self) -> dict[str, Any]:
        classify = getattr(self.comfy, "resource_classifications", None)
        if classify is None:
            raise ComfyError("无法核验人物 LoRA 的模型归属")
        inventory = await classify("loras", refresh=True)
        if not isinstance(inventory, dict) or not isinstance(inventory.get("items"), list):
            raise ComfyError("LoRA Manager 返回了无效分类清单")
        return inventory

    async def _prepare_character_loras(self, settings: dict[str, Any], resolved: dict[str, Any],
                                      snapshot: dict[str, Any] | None,
                                      regeneration: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        historical = runtime_snapshot(regeneration)
        policy = binding_policy(regeneration)
        if settings.get("workbench") != "random" or settings.get("model_profile") != "anima":
            return settings, resolved, {}
        if historical and policy != "current":
            # Unlike a new binding, every enabled historical resource is
            # required. Recheck at execution, since this task may have queued.
            validate_frozen_resources(settings, historical, await self._binding_inventory())
            historical["effective_loras"] = copy.deepcopy(settings["loras"])
            if policy == "preserve_character":
                resolved = append_trigger_words(resolved, historical.get("automatic_trigger_words", []))
            return copy.deepcopy(settings), resolved, historical
        if snapshot is None:
            return settings, resolved, {}
        characters = resolved.get("selected", {}).get("character", [])
        has_bindings = any((snapshot.get("bindings") or {}).get(item.get("id"), {}).get("loras") for item in characters)
        inventory = await self._binding_inventory() if has_bindings else {"items": []}
        effective, runtime = resolve_character_loras(settings, resolved.get("selected", {}), snapshot, inventory)
        return effective, append_trigger_words(resolved, runtime["automatic_trigger_words"]), runtime

    async def _run(
        self,
        batch_id: str,
        settings: dict[str, Any],
        seeds: dict[str, int] | None = None,
        regeneration: dict[str, Any] | None = None,
        binding_snapshot: dict[str, Any] | None = None,
    ) -> None:
        try:
            for sequence in range(1, settings["count"] + 1):
                if self.stop_requested:
                    break
                # 图像种子可单独固定；仅显式的提示词种子才冻结每张的抽取。
                sample_seed = seeds["sample_seed"] if seeds else secrets.randbelow(MAX_SAMPLE_SEED + 1)
                prompt_seed = (
                    seeds["prompt_seed"]
                    if seeds and "prompt_seed" in seeds
                    else regeneration.get("prompt_seed")
                    if regeneration and regeneration.get("mode") == "prompt_variant"
                    else secrets.randbelow(2**31)
                )
                date_folder = datetime.now().strftime("%Y-%m-%d")
                prefix = f"AnimaRandomWebUI/{date_folder}/{batch_id}/image"
                resolved = self._resolve_regeneration(settings, prompt_seed, regeneration) if regeneration else self._resolve_prompt(settings, prompt_seed)
                if regeneration and regeneration.get("mode") == "content_redraw":
                    for attempt in range(512):
                        candidate_seed = (prompt_seed + attempt) % (2**31)
                        resolved = self._resolve_regeneration(
                            settings, candidate_seed, regeneration
                        )
                        if resolved.pop("_changed", False):
                            prompt_seed = candidate_seed
                            break
                    else:
                        raise WorkflowError("所选维度无可用替代组合")
                effective_settings, resolved, lora_runtime = await self._prepare_character_loras(
                    settings, resolved, binding_snapshot, regeneration,
                )
                if lora_runtime:
                    self.state["lora_runtime"] = copy.deepcopy(lora_runtime)
                    self.state["lora_warning_count"] = self.state.get("lora_warning_count", 0) + len(lora_runtime.get("skipped", []))
                if self.prompt_rules is not None and not (
                    regeneration and regeneration.get("frozen_positive_prompt")
                ):
                    protected = effective_settings.get("lora_managed_triggers", [])
                    composer_prompt, _ = self.prompt_rules.normalize_text(
                        resolved["composer_prompt"],
                        "positive",
                        protected_lora_words=protected,
                        model_profile=settings["model_profile"], apply_custom=True,
                    )
                    full_prompt, _ = self.prompt_rules.normalize_text(
                        resolved["full_prompt"],
                        "positive",
                        protected_lora_words=protected,
                        model_profile=settings["model_profile"], apply_custom=True,
                    )
                    selected = copy.deepcopy(resolved["selected"])
                    for entries in selected.values():
                        for snapshot in entries:
                            if snapshot.get("snapshot_version") != 2:
                                continue
                            snapshot["prompt"], _ = self.prompt_rules.normalize_text(
                                snapshot["prompt"], "positive", protected_lora_words=protected,
                                model_profile=settings["model_profile"],
                            )
                            if snapshot.get("trigger"):
                                trigger, _ = self.prompt_rules.normalize_text(
                                    ", ".join(split_prompt(snapshot["trigger"])),
                                    "positive", protected_lora_words=protected,
                                    model_profile=settings["model_profile"],
                                )
                                snapshot["trigger"] = split_prompt(trigger)
                    resolved = {
                        **resolved,
                        "composer_prompt": composer_prompt,
                        "full_prompt": full_prompt,
                        "selected": selected,
                    }
                submission_args = (
                    effective_settings, sample_seed, prompt_seed, prefix, self.client_id, sequence
                )
                execution_context = new_execution_context() if settings.get("pipeline_version", 2) >= 3 else None
                payload = self.templates.submission(
                    *submission_args,
                    resolved_prompt=resolved["composer_prompt"],
                    resolved_selection=resolved["selected"],
                    resolved_prompt_full=resolved["full_prompt"],
                    frozen_positive_prompt=(regeneration or {}).get(
                        "frozen_positive_prompt", ""
                    ),
                    frozen_negative_prompt=(regeneration or {}).get(
                        "frozen_negative_prompt", ""
                    ),
                    **({"execution_nonce": execution_context["execution_nonce"]} if execution_context is not None else {}),
                    **({"resources_resolved": True} if isinstance(self.templates, WorkflowTemplates) else {}),
                )
                if execution_context is not None:
                    metadata = payload.setdefault("extra_data", {}).setdefault("extra_pnginfo", {}).setdefault("anima_random_webui", {})
                    metadata["execution_context"] = copy.deepcopy(execution_context)
                    self.state["execution_context"] = copy.deepcopy(execution_context)
                if lora_runtime:
                    metadata = payload.setdefault("extra_data", {}).setdefault("extra_pnginfo", {}).setdefault("anima_random_webui", {})
                    metadata["lora_runtime"] = copy.deepcopy(lora_runtime)
                self.state["current"] = sequence
                self.state["prompt_id"] = ""
                self._clear_preview()
                self._submitted_node_stages = {
                    str(node_id): (str(meta["pipeline_stage"]), STAGE_LABELS.get(meta["pipeline_stage"], str(meta.get("title") or meta["pipeline_stage"])))
                    for node_id, node in (payload.get("prompt") or {}).items()
                    if isinstance(node, dict) and isinstance(meta := node.get("_meta"), dict)
                    and meta.get("pipeline_stage")
                }
                for key in ("progress_stage", "progress_stage_label", "failed_stage", "failed_stage_label"):
                    self.state.pop(key, None)
                self.state["preview_status"] = "waiting"
                prompt_id = await self.comfy.submit(payload)
                self.state["prompt_id"] = prompt_id
                try:
                    entry = await self.comfy.wait_for_history(
                        prompt_id, should_abort=lambda: self.stop_requested
                    )
                except ComfyAborted:
                    await self._interrupt_current()
                    break
                images = extract_images(entry)
                if not images:
                    raise ComfyError("任务完成但未返回保存图片")
                frozen_positive = (regeneration or {}).get("frozen_positive_prompt", "")
                frozen_negative = (regeneration or {}).get("frozen_negative_prompt", "")
                positive = frozen_positive or extract_positive_prompt(entry)
                if lora_runtime:
                    positive = frozen_positive or resolved["full_prompt"]
                bound_resources = self._bound_resources(effective_settings)
                for image in images:
                    await self.history.add_image(
                        batch_id=batch_id,
                        sequence=sequence,
                        prompt_id=prompt_id,
                        image=image,
                        positive_prompt=positive,
                        negative_prompt=frozen_negative or settings["negative_prompt"],
                        sample_seed=sample_seed,
                        prompt_seed=prompt_seed,
                        settings=effective_settings,
                        resolved_selection=resolved["selected"],
                        resolved_prompt=positive or resolved["full_prompt"],
                        bound_resources=bound_resources,
                        **({"execution_context": execution_context} if execution_context is not None else {}),
                        **({"lora_runtime": lora_runtime} if lora_runtime else {}),
                    )
                self.state["completed"] = sequence
                await self.history.update_batch(batch_id, completed=sequence)
                if self.stop_requested:
                    break

            status = "stopped" if self.stop_requested else "completed"
            self.state["status"] = status
            await self.history.update_batch(batch_id, completed=self.state["completed"], status=status)
        except (ComfyAborted, asyncio.CancelledError):
            await self._interrupt_current()
            self.state["status"] = "stopped"
            await self.history.update_batch(batch_id, completed=self.state["completed"], status="stopped")
        except Exception as error:
            logger.warning("批次 %s 出错: %s", batch_id, error)
            self.state["status"] = "error"
            error_text = str(error)
            failed_stage = self._submitted_node_stages.get(str(getattr(error, "node_id", "")))
            if failed_stage:
                self.state["failed_stage"], self.state["failed_stage_label"] = failed_stage
                error_text = f"{failed_stage[1]}失败：{error_text}"
            self.state["error"] = error_text
            await self.history.update_batch(
                batch_id,
                completed=self.state["completed"],
                status="error",
                error=error_text,
            )
        finally:
            await self._advance_queue()

    async def _advance_queue(self) -> None:
        """Finish this batch, then dispatch the next owned generation task."""
        self._clear_preview()
        async with self._start_lock:
            while not self.shutting_down and self.queue:
                entry = self.queue.pop(0)
                try:
                    await self._begin_batch(entry["settings"], entry["seeds"], entry.get("regeneration"), entry.get("binding_snapshot"))
                    return
                except Exception as error:
                    logger.warning("队列任务启动失败: %s", error)
            self._stop_monitor()

    def _ensure_monitor(self) -> None:
        if self._monitor_task and not self._monitor_task.done():
            return
        stream = getattr(self.comfy, "progress_stream", None)
        if stream is None:
            return
        self._monitor_task = asyncio.create_task(self._monitor_progress())
        self._monitor_tasks.add(self._monitor_task)
        self._monitor_task.add_done_callback(self._monitor_finished)

    def _monitor_finished(self, task: asyncio.Task[None]) -> None:
        self._monitor_tasks.discard(task)
        if not task.cancelled():
            task.exception()

    def _stop_monitor(self) -> None:
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
        self._monitor_task = None

    def _clear_preview(self) -> None:
        """Drop all ownership when a prompt, batch or processing stage changes."""
        self._advance_preview_epoch()
        self._preview_context = None
        self._preview_execution = None

    def _advance_preview_epoch(self) -> None:
        """Invalidate in-flight image reads, even when the next node has the same id."""
        self._preview_epoch += 1
        self.preview = None
        self.preview_metadata = None
        if self.state is not None:
            self.state["preview_epoch"] = self._preview_epoch
            for key in ("preview_id", "preview_prompt_id", "preview_node", "preview_status", "progress", "progress_node"):
                self.state.pop(key, None)

    def _reset_preview_connection(self) -> None:
        execution = self._preview_execution
        self._clear_preview()
        if execution and not execution[2]:
            self._preview_execution = execution

    def _consume_progress(self, item: dict[str, Any]) -> None:
        state = self.state
        if not state or state.get("status") not in ("running", "stopping"):
            return
        # LLM prompt processing can have its own progress stream; it must never
        # be mistaken for a frame from the subsequent image-generation prompt.
        if state.get("kind") != "generation" or state.get("stage") != "generating":
            return
        batch_id, prompt_id = state["id"], state.get("prompt_id")
        if not prompt_id:
            return
        context = self._preview_context
        context_matches = bool(context and context[:2] == (batch_id, prompt_id))
        execution = self._preview_execution
        execution_matches = bool(execution and execution[:2] == (batch_id, prompt_id))
        if item.get("kind") == "event":
            payload = item.get("payload")
            if not isinstance(payload, dict) or payload.get("type") not in ("progress", "executing"):
                return
            data = payload.get("data")
            if not isinstance(data, dict):
                return
            event_prompt = data.get("prompt_id")
            if event_prompt and event_prompt != prompt_id:
                # Old binary packets carry no identity. A foreign execution
                # event invalidates their inferred owner until ours is seen.
                self._preview_context = None
                return
            if not event_prompt and not context_matches:
                return
            node = str(data.get("node") or "")
            if payload["type"] == "executing":
                self._advance_preview_epoch()
                state["progress_node"] = node
                state["preview_status"] = "waiting"
                self._preview_execution = (batch_id, prompt_id, node)
                self._preview_context = (batch_id, prompt_id, node)
            else:
                try:
                    value, maximum = int(data.get("value") or 0), int(data.get("max") or 0)
                except (ValueError, TypeError, OverflowError):
                    return
                if value < 0 or maximum <= 0:
                    return
                node = node or (context[2] if context_matches else "")
                if not node:
                    return
                # Progress cannot reopen a completed execution or move back to
                # an earlier node after an executing event has advanced it.
                if execution_matches and node != execution[2]:
                    return
                if not execution_matches:
                    self._preview_execution = (batch_id, prompt_id, node)
                self._preview_context = (batch_id, prompt_id, node)
                state.setdefault("preview_epoch", self._preview_epoch)
                state["progress_node"] = node
                state["progress"] = {"value": min(value, maximum), "max": maximum}
            progress_stage = self._submitted_node_stages.get(node)
            if progress_stage:
                state["progress_stage"], state["progress_stage_label"] = progress_stage
            else:
                state.pop("progress_stage", None)
                state.pop("progress_stage_label", None)
            if state.get("preview_status") == "disconnected":
                state["preview_status"] = "live" if self.preview else "waiting"
            return
        if item.get("kind") != "preview":
            return
        frame_prompt, node = item.get("prompt_id"), item.get("node_id")
        if frame_prompt:
            if frame_prompt != prompt_id or not isinstance(node, str) or not node:
                return
            if execution_matches:
                node_ids = (node, item.get("display_node_id"), item.get("parent_node_id"))
                if not execution[2] or execution[2] not in node_ids:
                    return
        else:
            # Legacy frames are accepted only after an identified execution or
            # progress event on this connection and for this exact prompt.
            if not context_matches or not context[2]:
                return
            node = context[2]
        body, image_format = item.get("bytes"), item.get("format")
        if not isinstance(body, bytes) or not body or image_format not in ("jpeg", "png"):
            return
        # ComfyUI's metadata preview arrives before its progress event. It can
        # establish initial ownership after reconnect without accepting an
        # unidentified legacy frame from the previous connection.
        if not execution_matches:
            self._preview_execution = (batch_id, prompt_id, node)
        self._preview_seq += 1
        self.preview = (self._preview_seq, f"image/{image_format}", body)
        self.preview_metadata = {"batch_id": batch_id, "prompt_id": prompt_id, "node_id": node, "epoch": self._preview_epoch}
        state.update(preview_id=self._preview_seq, preview_prompt_id=prompt_id,
                     preview_node=node, preview_epoch=self._preview_epoch, preview_status="live")

    async def _monitor_progress(self) -> None:
        """通过 ComfyUI websocket 更新采样进度与实时预览。

        尽力而为:连接失败或断开只影响进度展示,不影响批次本身
        (批次完成仍由 wait_for_history 轮询判定)。
        """
        while self.active():
            # Never infer legacy-frame ownership from events before reconnect.
            self._reset_preview_connection()
            try:
                async for item in self.comfy.progress_stream(self.client_id):
                    self._consume_progress(item)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.debug("进度 websocket 中断,稍后重连: %s", error)
            self._preview_context = None
            self._advance_preview_epoch()
            if self.state and self.state.get("status") in ("running", "stopping") and self.state.get("stage") == "generating":
                self.state["preview_status"] = "disconnected"
            if not self.active():
                break
            await asyncio.sleep(2.0)

    async def _interrupt_current(self) -> None:
        """停止时尽力中断 ComfyUI 正在渲染的任务;失败不影响批次收尾。"""
        try:
            async with asyncio.timeout(CANCEL_PROMPT_TIMEOUT):
                await self._cancel_current_prompt()
        except (ComfyError, asyncio.TimeoutError) as error:
            warning = str(error) or "取消请求超时，远端任务可能继续执行"
            if self.state is not None:
                self.state["cancel_warning"] = warning
            logger.warning("ComfyUI 任务取消未确认；本地任务将继续收尾: %s", warning)

    async def _cancel_current_prompt(self) -> None:
        cancel = getattr(self.comfy, "cancel_prompt", None)
        prompt_id = (self.state or {}).get("prompt_id")
        if cancel is not None:
            if prompt_id:
                await cancel(prompt_id)
                queued = getattr(self.comfy, "prompt_queued", None)
                if queued:
                    for _ in range(40):
                        if not await queued(prompt_id):
                            break
                        await asyncio.sleep(0.1)
                    else:
                        raise ComfyError("取消请求后任务仍在 ComfyUI 队列中，远端任务可能继续执行")
            return
        interrupt = getattr(self.comfy, "interrupt", None)
        if interrupt is None:
            return
        await interrupt()

    def _resolve_regeneration(
        self,
        settings: dict[str, Any],
        prompt_seed: int,
        regeneration: dict[str, Any],
    ) -> dict[str, Any]:
        mode = regeneration["mode"]
        fixed_selection = copy.deepcopy(regeneration.get("fixed_selection") or {})
        if mode in {"replay", "prompt_variant"}:
            positive = regeneration["frozen_positive_prompt"]
            return {
                "composer_prompt": positive,
                "full_prompt": positive,
                "selected": fixed_selection,
            }
        if mode == "settings_reroll":
            return self._resolve_prompt(settings, prompt_seed)
        if not self.catalog:
            raise WorkflowError("当前提示词目录不可用，无法重新抽取内容")
        restored, issues = resolve_history_selection(fixed_selection, settings, self.catalog)
        for section in fixed_selection:
            if section in issues:
                raise WorkflowError(issues[section])
        fixed_selection = restored
        redraw_settings = copy.deepcopy(settings)
        for section, entries in fixed_selection.items():
            redraw_settings[f"random_{section}"] = False
            redraw_settings[f"fixed_{section}"] = ", ".join(item["prompt"] for item in entries)
        resolved = self.catalog.resolve_prompt(redraw_settings, prompt_seed)
        merged = copy.deepcopy(resolved["selected"])
        for section, entries in fixed_selection.items():
            merged[section] = copy.deepcopy(entries)
        redraw_sections = regeneration["redraw_sections"]
        original = regeneration.get("original_selection") or {}
        changed = any(
            [item.get("id") for item in merged.get(section, [])]
            != [item.get("id") for item in original.get(section, [])]
            for section in redraw_sections
        )
        return {**resolved, "selected": merged, "_changed": changed}
