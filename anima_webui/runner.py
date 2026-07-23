from __future__ import annotations

import asyncio
import secrets
import uuid
from datetime import datetime
from typing import Any

from .comfy import ComfyError, extract_images, extract_positive_prompt
from .catalog import PromptCatalog
from .history import HistoryStore
from .workflow import MAX_SAMPLE_SEED, WorkflowError, WorkflowTemplates, validate_loras, validate_settings


class BatchConflict(RuntimeError):
    pass


class BatchManager:
    def __init__(
        self,
        templates: WorkflowTemplates,
        history: HistoryStore,
        comfy: Any,
        catalog: PromptCatalog | None = None,
    ):
        self.templates = templates
        self.history = history
        self.comfy = comfy
        self.task: asyncio.Task[None] | None = None
        self.state: dict[str, Any] | None = None
        self.stop_requested = False
        self.client_id = str(uuid.uuid4())
        self.catalog = catalog

    def active(self) -> bool:
        return bool(self.task and not self.task.done())

    async def start(self, overrides: dict[str, Any]) -> dict[str, Any]:
        if self.active():
            raise BatchConflict("已有批次正在运行")
        settings = validate_settings(overrides)
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
        batch_id = uuid.uuid4().hex[:12]
        self.stop_requested = False
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
        self.history.create_batch(batch_id, settings["count"], settings)
        self.task = asyncio.create_task(self._run(batch_id, settings))
        return self.snapshot()

    def snapshot(self) -> dict[str, Any] | None:
        return dict(self.state) if self.state else None

    async def request_stop(self, batch_id: str) -> dict[str, Any]:
        if not self.state or self.state["id"] != batch_id:
            raise KeyError(batch_id)
        if self.active():
            self.stop_requested = True
            self.state["status"] = "stopping"
            self.history.update_batch(batch_id, status="stopping")
        return self.snapshot()

    async def wait(self) -> dict[str, Any] | None:
        if self.task:
            await self.task
        return self.snapshot()

    async def _run(self, batch_id: str, settings: dict[str, Any]) -> None:
        try:
            for sequence in range(1, settings["count"] + 1):
                if self.stop_requested:
                    break
                sample_seed = secrets.randbelow(MAX_SAMPLE_SEED + 1)
                prompt_seed = secrets.randbelow(2**31)
                date_folder = datetime.now().strftime("%Y-%m-%d")
                prefix = f"AnimaRandom/{date_folder}/{batch_id}/image"
                resolved = self.catalog.resolve_prompt(settings, prompt_seed) if self.catalog else {
                    "composer_prompt": "",
                    "full_prompt": "",
                    "selected": {},
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
                entry = await self.comfy.wait_for_history(prompt_id)
                images = extract_images(entry)
                if not images:
                    raise ComfyError("任务完成但未返回保存图片")
                positive = extract_positive_prompt(entry)
                for image in images:
                    self.history.add_image(
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
                self.history.update_batch(batch_id, completed=sequence)
                if self.stop_requested:
                    break

            status = "stopped" if self.stop_requested else "completed"
            self.state["status"] = status
            self.history.update_batch(batch_id, completed=self.state["completed"], status=status)
        except Exception as error:
            self.state["status"] = "error"
            self.state["error"] = str(error)
            self.history.update_batch(
                batch_id,
                completed=self.state["completed"],
                status="error",
                error=str(error),
            )
