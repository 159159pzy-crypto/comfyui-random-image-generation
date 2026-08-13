from __future__ import annotations

import asyncio
import logging
import secrets
import uuid
from datetime import datetime
from typing import Any

from .comfy import ComfyAborted, ComfyError, extract_images, extract_positive_prompt
from .catalog import PromptCatalog
from .history import HistoryStore
from .prompt_rules import PromptRuleStore
from .workflow import MAX_SAMPLE_SEED, WorkflowError, WorkflowTemplates, validate_loras, validate_settings


logger = logging.getLogger(__name__)


class BatchConflict(RuntimeError):
    pass


MAX_QUEUE = 20


def validate_seeds(value: Any) -> dict[str, int] | None:
    """校验复现用的固定种子。None 表示每张图随机(默认行为)。"""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise WorkflowError("seeds 必须是对象")
    unknown = set(value) - {"sample_seed", "prompt_seed"}
    if unknown:
        raise WorkflowError(f"seeds 包含未知参数: {', '.join(sorted(unknown))}")
    result: dict[str, int] = {}
    for name, maximum in (("sample_seed", MAX_SAMPLE_SEED), ("prompt_seed", 2**31 - 1)):
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
        # 排队中的批次(仅内存;WebUI 重启后队列清空,历史记录只在真正开跑时创建)。
        self.queue: list[dict[str, Any]] = []
        self.shutting_down = False
        # 实时进度:由 _monitor_progress 通过 ComfyUI websocket 更新。
        self.preview: tuple[int, str, bytes] | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._preview_seq = 0
        # start 里的资源校验要 await 多次 ComfyUI 请求,期间若不上锁,
        # 第二个并发 start 也能通过 active() 检查并启动重复批次。
        self._start_lock = asyncio.Lock()

    def active(self) -> bool:
        return bool(self.task and not self.task.done())

    async def start(self, overrides: dict[str, Any], seeds: dict[str, int] | None = None) -> dict[str, Any]:
        async with self._start_lock:
            normalized_overrides = overrides
            if self.prompt_rules is not None:
                normalized_overrides, _ = self.prompt_rules.normalize_settings(overrides)
            settings = validate_settings(normalized_overrides)
            seeds = validate_seeds(seeds)
            if self.catalog:
                self.catalog.validate_settings(settings)
            lora_filenames = getattr(self.comfy, "lora_filenames", None)
            if not lora_filenames:
                raise ComfyError("ComfyUI 客户端无法读取 LoRA 列表")
            settings["loras"] = validate_loras(settings, await lora_filenames())
            resource_inventory = getattr(self.comfy, "resource_inventory", None)
            if resource_inventory:
                resources = await resource_inventory()
                if settings["model_name"] not in resources.get("models", []):
                    raise WorkflowError(f"主模型不存在: {settings['model_name']}")
                if settings["hires"]["enabled"] and settings["hires"]["model_name"] not in resources.get("upscale_models", []):
                    raise WorkflowError(f"高清修复模型不存在: {settings['hires']['model_name']}")
                if settings["sampler_name"] not in resources.get("samplers", []):
                    raise WorkflowError(f"Sampler 不可用: {settings['sampler_name']}")
                if settings["scheduler"] not in resources.get("schedulers", []):
                    raise WorkflowError(f"Scheduler 不可用: {settings['scheduler']}")
            if self.active():
                # 有批次在跑:进入队列,当前批次结束后自动接续。
                if len(self.queue) >= MAX_QUEUE:
                    raise BatchConflict(f"队列已满(最多 {MAX_QUEUE} 个)")
                entry = {
                    "queue_id": f"queue_{uuid.uuid4().hex[:8]}",
                    "settings": settings,
                    "seeds": seeds,
                }
                self.queue.append(entry)
                return {
                    "status": "queued",
                    "queue_id": entry["queue_id"],
                    "position": len(self.queue),
                    "count": settings["count"],
                }
            await self._begin_batch(settings, seeds)
            return self.snapshot()

    async def _begin_batch(self, settings: dict[str, Any], seeds: dict[str, int] | None) -> None:
        batch_id = uuid.uuid4().hex[:12]
        self.stop_requested = False
        self.preview = None
        self.state = {
            "id": batch_id,
            "status": "running",
            "total": settings["count"],
            "completed": 0,
            "current": 0,
            "prompt_id": "",
            "error": "",
            "settings": settings,
        }
        await self.history.create_batch(batch_id, settings["count"], settings)
        self.task = asyncio.create_task(self._run(batch_id, settings, seeds))
        self._ensure_monitor()

    def queue_snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "queue_id": entry["queue_id"],
                "position": index + 1,
                "count": entry["settings"]["count"],
                "model_name": entry["settings"]["model_name"],
            }
            for index, entry in enumerate(self.queue)
        ]

    def remove_queued(self, queue_id: str) -> bool:
        previous = len(self.queue)
        self.queue = [entry for entry in self.queue if entry["queue_id"] != queue_id]
        return len(self.queue) < previous

    def snapshot(self) -> dict[str, Any] | None:
        return dict(self.state) if self.state else None

    async def request_stop(self, batch_id: str, clear_queue: bool = True) -> dict[str, Any]:
        if not self.state or self.state["id"] != batch_id:
            raise KeyError(batch_id)
        if clear_queue:
            self.queue.clear()
        if self.active():
            self.stop_requested = True
            self.state["status"] = "stopping"
            await self.history.update_batch(batch_id, status="stopping")
        return self.snapshot()

    async def wait(self) -> dict[str, Any] | None:
        # 队列自动接续会替换 self.task,循环等待直到全部批次收尾。
        while self.task and not self.task.done():
            await self.task
            await asyncio.sleep(0)
        return self.snapshot()

    async def _run(self, batch_id: str, settings: dict[str, Any], seeds: dict[str, int] | None = None) -> None:
        try:
            for sequence in range(1, settings["count"] + 1):
                if self.stop_requested:
                    break
                # 固定种子用于复现历史图片;未指定时每张随机。
                sample_seed = seeds["sample_seed"] if seeds else secrets.randbelow(MAX_SAMPLE_SEED + 1)
                prompt_seed = seeds["prompt_seed"] if seeds else secrets.randbelow(2**31)
                date_folder = datetime.now().strftime("%Y-%m-%d")
                prefix = f"AnimaRandom/{date_folder}/{batch_id}/image"
                resolved = self.catalog.resolve_prompt(settings, prompt_seed) if self.catalog else {
                    "composer_prompt": "",
                    "full_prompt": "",
                    "selected": {},
                }
                if self.prompt_rules is not None:
                    protected = settings.get("lora_managed_triggers", [])
                    composer_prompt, _ = self.prompt_rules.normalize_text(
                        resolved["composer_prompt"],
                        "positive",
                        protected_lora_words=protected,
                    )
                    full_prompt, _ = self.prompt_rules.normalize_text(
                        resolved["full_prompt"],
                        "positive",
                        protected_lora_words=protected,
                    )
                    resolved = {
                        **resolved,
                        "composer_prompt": composer_prompt,
                        "full_prompt": full_prompt,
                    }
                submission_args = (
                    settings, sample_seed, prompt_seed, prefix, self.client_id, sequence
                )
                if self.catalog:
                    payload = self.templates.submission(
                        *submission_args,
                        resolved_prompt=resolved["composer_prompt"],
                        resolved_selection=resolved["selected"],
                        resolved_prompt_full=resolved["full_prompt"],
                    )
                else:
                    payload = self.templates.submission(*submission_args)
                self.state["current"] = sequence
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
                positive = extract_positive_prompt(entry)
                for image in images:
                    await self.history.add_image(
                        batch_id=batch_id,
                        sequence=sequence,
                        prompt_id=prompt_id,
                        image=image,
                        positive_prompt=positive,
                        negative_prompt=settings["negative_prompt"],
                        sample_seed=sample_seed,
                        prompt_seed=prompt_seed,
                        settings=settings,
                        resolved_selection=resolved["selected"],
                        resolved_prompt=positive or resolved["full_prompt"],
                    )
                self.state["completed"] = sequence
                await self.history.update_batch(batch_id, completed=sequence)
                if self.stop_requested:
                    break

            status = "stopped" if self.stop_requested else "completed"
            self.state["status"] = status
            await self.history.update_batch(batch_id, completed=self.state["completed"], status=status)
        except Exception as error:
            logger.warning("批次 %s 出错: %s", batch_id, error)
            self.state["status"] = "error"
            self.state["error"] = str(error)
            await self.history.update_batch(
                batch_id,
                completed=self.state["completed"],
                status="error",
                error=str(error),
            )
        finally:
            await self._advance_queue()

    async def _advance_queue(self) -> None:
        """当前批次收尾后自动接续队列中的下一个;关闭时不再接续。

        stop_requested 只作用于当前批次:clearQueue=true 的停止已经清空了队列,
        clearQueue=false(只停当前)则应继续接续,_begin_batch 会复位该标志。
        """
        if self.shutting_down or not self.queue:
            self._stop_monitor()
            return
        entry = self.queue.pop(0)
        try:
            await self._begin_batch(entry["settings"], entry["seeds"])
        except Exception as error:  # 接续失败不应吞掉:记录并继续尝试下一个
            logger.warning("队列批次启动失败: %s", error)
            await self._advance_queue()

    def _ensure_monitor(self) -> None:
        if self._monitor_task and not self._monitor_task.done():
            return
        stream = getattr(self.comfy, "progress_stream", None)
        if stream is None:
            return
        self._monitor_task = asyncio.create_task(self._monitor_progress())

    def _stop_monitor(self) -> None:
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
        self._monitor_task = None

    async def _monitor_progress(self) -> None:
        """通过 ComfyUI websocket 更新采样进度与实时预览。

        尽力而为:连接失败或断开只影响进度展示,不影响批次本身
        (批次完成仍由 wait_for_history 轮询判定)。
        """
        while self.active():
            try:
                async for item in self.comfy.progress_stream(self.client_id):
                    state = self.state
                    if state is None or state.get("status") not in ("running", "stopping"):
                        continue
                    if item.get("kind") == "event":
                        payload = item.get("payload") or {}
                        data = payload.get("data") or {}
                        if payload.get("type") == "progress":
                            state["progress"] = {
                                "value": int(data.get("value") or 0),
                                "max": int(data.get("max") or 0),
                            }
                        elif payload.get("type") == "executing":
                            state["progress_node"] = str(data.get("node") or "")
                            if data.get("node") is None:
                                state.pop("progress", None)
                    elif item.get("kind") == "preview":
                        self._preview_seq += 1
                        self.preview = (
                            self._preview_seq,
                            f"image/{item.get('format') or 'jpeg'}",
                            item.get("bytes") or b"",
                        )
                        state["preview_id"] = self._preview_seq
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.debug("进度 websocket 中断,稍后重连: %s", error)
            if not self.active():
                break
            await asyncio.sleep(2.0)

    async def _interrupt_current(self) -> None:
        """停止时尽力中断 ComfyUI 正在渲染的任务;失败不影响批次收尾。"""
        interrupt = getattr(self.comfy, "interrupt", None)
        if interrupt is None:
            return
        try:
            await interrupt()
        except ComfyError:
            pass
