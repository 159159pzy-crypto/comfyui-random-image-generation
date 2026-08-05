from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import Mapping
from typing import Any

from anima_webui.comfy import ComfyAborted, ComfyError, extract_images
from anima_webui.task_runtime import StudioTaskRuntime

from .engine import NaturalEngine, NaturalEngineError


class NaturalJobManager:
    MAX_JOBS = 200
    MAX_EVENTS_PER_JOB = 200
    TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "interrupted"})

    def __init__(
        self,
        engine: NaturalEngine,
        comfy: Any,
        history: Any,
        execution_lock: asyncio.Lock,
        task_runtime: StudioTaskRuntime | None = None,
    ) -> None:
        self.engine = engine
        self.comfy = comfy
        self.history = history
        self.execution_lock = execution_lock
        self.client_id = f"natural-{uuid.uuid4()}"
        self.jobs: dict[str, dict[str, Any]] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.listeners: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._execution_owner: str | None = None
        self._prompt_owner: tuple[str, str] | None = None
        self.task_runtime = task_runtime or StudioTaskRuntime(
            self.engine.data_dir / "studio_tasks.sqlite3"
        )
        self._owns_task_runtime = task_runtime is None
        self.task_store = self.task_runtime.store
        self._restore_jobs()

    def _restore_jobs(self) -> None:
        status_states = {
            "queued": "interrupted",
            "running": "interrupted",
            "succeeded": "completed",
            "partial": "failed",
            "failed": "failed",
            "cancelled": "cancelled",
            "timed_out": "failed",
            "interrupted": "interrupted",
        }
        records = self.task_store.recent_tasks(
            limit=self.MAX_JOBS, task_type="natural_generation"
        )
        for record in reversed(records):
            snapshot = (record.get("result") or {}).get("job")
            if not isinstance(snapshot, Mapping):
                snapshot = (record.get("metadata") or {}).get("job")
            if not isinstance(snapshot, Mapping):
                continue
            job = dict(snapshot)
            job_id = str(record.get("run_id") or job.get("id") or "")
            if not job_id:
                continue
            task_status = str(record.get("status") or "interrupted")
            state = status_states.get(task_status, "interrupted")
            if task_status == "queued":
                try:
                    self.task_store.finish_task(
                        job_id,
                        "interrupted",
                        error_code="studio_restarted",
                        error_summary="Studio restarted before the queued task began",
                    )
                except RuntimeError:
                    pass
            job.update(
                {
                    "id": job_id,
                    "state": state,
                    "stop_requested": state in self.TERMINAL_STATES,
                    "events": [],
                }
            )
            entries = self.task_store.read_events(run_id=job_id, limit=self.MAX_EVENTS_PER_JOB)[
                "entries"
            ]
            for event in entries:
                code = str(event.get("event_code") or "")
                if not code.startswith("natural_"):
                    continue
                job["events"].append(
                    {
                        "job_id": job_id,
                        "stage": code.removeprefix("natural_"),
                        "message": str(event.get("message") or ""),
                        "timestamp": float(event.get("timestamp") or 0),
                        "details": dict(event.get("details") or {}),
                    }
                )
            self.jobs[job_id] = job

    @staticmethod
    def _stored_job(job: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in job.items()
            if key not in {"payload", "asset_paths", "stop_requested", "events"}
        }

    def _make_room(self) -> None:
        if len(self.jobs) < self.MAX_JOBS:
            return
        terminal = sorted(
            (
                item
                for item in self.jobs.values()
                if str(item.get("state")) in self.TERMINAL_STATES
            ),
            key=lambda item: float(item.get("updated_at") or item.get("created_at") or 0),
        )
        while len(self.jobs) >= self.MAX_JOBS and terminal:
            removed = terminal.pop(0)
            self.jobs.pop(str(removed["id"]), None)
            self.listeners.pop(str(removed["id"]), None)
        if len(self.jobs) >= self.MAX_JOBS:
            raise NaturalEngineError(
                "自然语言任务队列已满，请等待现有任务结束",
                code="natural_queue_full",
                status=429,
            )

    def _public(self, job: Mapping[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in job.items()
            if key not in {"payload", "asset_paths", "stop_requested"}
        }

    def timeline(self, job_id: str) -> list[dict[str, Any]]:
        if job_id not in self.jobs:
            raise KeyError(job_id)
        return list(self.jobs[job_id].get("events") or ())

    def _safe_text(self, value: Any, limit: int = 500) -> str:
        text = str(value or "")
        for path in (self.engine.root, self.engine.data_dir):
            text = text.replace(str(path), "[local-path]")
            text = text.replace(str(path).replace("\\", "/"), "[local-path]")
        text = re.sub(r"(?i)bearer\s+[a-z0-9._~-]+", "Bearer [redacted]", text)
        text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[redacted]", text)
        return text[:limit]

    def get(self, job_id: str) -> dict[str, Any]:
        try:
            return self._public(self.jobs[job_id])
        except KeyError as exc:
            raise KeyError(job_id) from exc

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        return [
            self._public(item)
            for item in sorted(self.jobs.values(), key=lambda value: value["created_at"], reverse=True)[:limit]
        ]

    async def _emit(self, job: dict[str, Any], stage: str, message: str, **details: Any) -> None:
        safe_details = {
            str(key): self._safe_text(value) if key in {"error", "message", "detail"} else value
            for key, value in details.items()
        }
        event = {
            "job_id": job["id"],
            "stage": stage,
            "message": self._safe_text(message),
            "timestamp": time.time(),
            "details": safe_details,
        }
        job["stage"] = stage
        job["message"] = message
        job["updated_at"] = event["timestamp"]
        job.setdefault("events", []).append(event)
        if len(job["events"]) > self.MAX_EVENTS_PER_JOB:
            del job["events"][: -self.MAX_EVENTS_PER_JOB]
        try:
            await self.task_runtime.event(
                str(job["id"]),
                stage,
                event["message"],
                event_code=f"natural_{stage}",
                details=safe_details,
            )
        except (RuntimeError, ValueError):
            pass
        for queue in tuple(self.listeners.get(job["id"], set())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def create(
        self,
        payload: Mapping[str, Any],
        *,
        job_id: str = "",
        task_exists: bool = False,
        frozen_plan: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._make_room()
        job_type = str(payload.get("job_type") or "text_to_image").strip().casefold()
        if job_type in {"reverse", "control", "img2img", "inpaint", "upscale", "character_swap"}:
            self.engine.assets.get(str(payload.get("asset_id") or ""))
        if job_type == "inpaint":
            self.engine.assets.get(str(payload.get("mask_asset_id") or ""))
        if frozen_plan is None:
            plan = await self.engine.plan(payload)
        else:
            plan = dict(frozen_plan)
            expected_job_type = "img2img" if job_type == "image_to_image" else job_type
            if str(plan.get("job_type") or "") != expected_job_type:
                raise NaturalEngineError(
                    "frozen plan mode does not match the generation intent",
                    code="frozen_plan_invalid",
                    status=409,
                )
            if plan.get("requires_confirmation"):
                raise NaturalEngineError(
                    "frozen plan still requires confirmation",
                    code="asset_confirmation_required",
                    status=409,
                )
            if str(plan.get("pipeline") or "") not in {"base", "rtx", "iterative"}:
                raise NaturalEngineError(
                    "frozen plan pipeline is invalid",
                    code="frozen_plan_invalid",
                    status=409,
                )
            if expected_job_type not in {"reverse", "upscale"} and not str(
                plan.get("positive_prompt") or ""
            ).strip():
                raise NaturalEngineError(
                    "frozen plan positive prompt is empty",
                    code="frozen_plan_invalid",
                    status=409,
                )
        if not bool(payload.get("preview_only")) and plan["job_type"] != "reverse":
            await self.engine.validate_workflow_dependencies(payload, plan, self.comfy)
        job_id = str(job_id or f"job_{uuid.uuid4().hex[:12]}").strip()
        if not job_id or job_id in self.jobs:
            raise NaturalEngineError("job id is empty or already active", code="job_id_conflict")
        now = time.time()
        job = {
            "id": job_id,
            "state": "planning",
            "stage": "planned",
            "message": "计划已生成",
            "created_at": now,
            "updated_at": now,
            "job_type": plan["job_type"],
            "pipeline": plan["pipeline"],
            "plan": plan,
            "progress": {"completed": 0, "total": int(payload.get("count") or 1)},
            "images": [],
            "error": "",
            "payload": dict(payload),
            "stop_requested": False,
            "events": [],
        }
        self.jobs[job_id] = job
        if not task_exists:
            try:
                await self.task_runtime.create(
                    "natural_generation",
                    run_id=job_id,
                    mode=str(job["job_type"]),
                    total_items=int(job["progress"]["total"]),
                    metadata={"job": self._stored_job(job)},
                )
            except Exception:
                self.jobs.pop(job_id, None)
                raise
        await self._emit(job, "planned", "自然语言计划已通过本地校验")
        if bool(payload.get("preview_only")) or plan["job_type"] == "reverse":
            job["state"] = "completed"
            await self._emit(job, "completed", "计划预览已完成")
            await self.task_runtime.finish(
                job_id,
                "succeeded",
                completed_items=0,
                result={"job": self._stored_job(job)},
            )
            return self._public(job)
        task = asyncio.create_task(self._run(job), name=f"anima-natural:{job_id}")
        self.tasks[job_id] = task
        return self._public(job)

    async def run_coordinated(
        self,
        job_id: str,
        payload: Mapping[str, Any],
        *,
        frozen_plan: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        await self.create(
            payload,
            job_id=job_id,
            task_exists=True,
            frozen_plan=frozen_plan,
        )
        task = self.tasks.get(job_id)
        if task is not None:
            await task
        return self.get(job_id)

    async def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job["state"] in self.TERMINAL_STATES:
            return self._public(job)
        owner = self._prompt_owner
        execution_owner = self._execution_owner
        prompt_id = str(job.get("prompt_id") or (owner[1] if owner else "") or "")
        job["stop_requested"] = True
        job["state"] = "cancelling"
        await self._emit(job, "cancelling", "正在停止自然语言任务")
        if owner is not None and owner == (str(job_id), prompt_id) and execution_owner == job_id:
            try:
                if prompt_id:
                    job["prompt_id"] = prompt_id
                    try:
                        await self.comfy.interrupt(prompt_id)
                    except TypeError:
                        # Transitional support for pre-V7 Comfy test doubles.
                        await self.comfy.interrupt()
            except Exception:
                pass
        return self._public(job)

    async def events(self, job_id: str):
        if job_id not in self.jobs:
            raise KeyError(job_id)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        self.listeners.setdefault(job_id, set()).add(queue)
        try:
            yield {
                "job_id": job_id,
                "stage": self.jobs[job_id]["stage"],
                "message": self.jobs[job_id]["message"],
                "timestamp": time.time(),
                "details": {},
            }
            while True:
                job = self.jobs[job_id]
                if job["state"] in self.TERMINAL_STATES and queue.empty():
                    break
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield {"job_id": job_id, "stage": "heartbeat", "message": "", "timestamp": time.time(), "details": {}}
        finally:
            listeners = self.listeners.get(job_id)
            if listeners is not None:
                listeners.discard(queue)
                if not listeners:
                    self.listeners.pop(job_id, None)

    async def _upload_assets(self, payload: Mapping[str, Any], job: dict[str, Any]) -> dict[str, str]:
        uploaded: dict[str, str] = {}
        for role, key in (("source", "asset_id"), ("mask", "mask_asset_id")):
            asset_id = str(payload.get(key) or "")
            if not asset_id:
                continue
            asset = self.engine.assets.get(asset_id)
            reference = await self.comfy.upload_image(
                asset.path,
                filename=f"{job['id']}-{role}{asset.path.suffix}",
                subfolder="anima_natural",
            )
            name = str(reference.get("name") or reference.get("filename") or "")
            subfolder = str(reference.get("subfolder") or "").strip("/\\")
            uploaded[role] = f"{subfolder}/{name}" if subfolder else name
        return uploaded

    @staticmethod
    def _preferred_images(entry: Mapping[str, Any], preferred_nodes: list[str]) -> list[dict[str, Any]]:
        outputs = entry.get("outputs") or {}
        selected: list[dict[str, Any]] = []
        for node_id in preferred_nodes:
            output = outputs.get(str(node_id)) or {}
            for image in output.get("images") or []:
                if isinstance(image, dict) and image.get("filename"):
                    selected.append(image)
        return selected or extract_images(dict(entry))

    async def _run(self, job: dict[str, Any]) -> None:
        payload = job["payload"]
        count = min(20, max(1, int(payload.get("count") or 1)))
        batch_id = job["id"]
        try:
            if job["stop_requested"]:
                raise ComfyAborted("任务已取消")
            job["state"] = "queued"
            await self._emit(job, "queued", "任务已进入共享 ComfyUI 执行队列")
            async with self.execution_lock:
                if job["stop_requested"]:
                    raise ComfyAborted("任务已取消")
                self._execution_owner = str(job["id"])
                job["state"] = "running"
                try:
                    await self.task_runtime.start(job["id"], total_items=count)
                except RuntimeError:
                    pass
                await self.history.create_batch(
                    batch_id,
                    count,
                    dict(payload),
                    source_workspace="natural",
                    job_type=job["job_type"],
                    request=dict(payload),
                    plan=job["plan"],
                )
                await self._emit(job, "uploading", "正在准备输入图片")
                uploaded = await self._upload_assets(payload, job)
                for index in range(1, count + 1):
                    if job["stop_requested"]:
                        raise ComfyAborted("任务已取消")
                    await self._emit(job, "building", "正在构建受约束的 ComfyUI 工作流", sequence=index)
                    workflow_payload = dict(payload)
                    if count > 1 and payload.get("seed") not in (None, "", -1, "-1"):
                        workflow_payload["seed"] = int(payload["seed"]) + index - 1
                    workflow, seed, preferred_nodes = self.engine.build_workflow(
                        workflow_payload, job["plan"], uploaded
                    )
                    submission = {
                        "prompt": workflow,
                        "client_id": self.client_id,
                        "extra_data": {
                            "extra_pnginfo": {
                                "workflow": workflow,
                                "anima_natural": {
                                    "job_id": job["id"],
                                    "job_type": job["job_type"],
                                    "plan": job["plan"],
                                }
                            }
                        },
                    }
                    await self._emit(job, "submitting", "正在提交 ComfyUI", sequence=index)
                    prompt_id = await self.comfy.submit(submission)
                    job["prompt_id"] = prompt_id
                    self._prompt_owner = (str(job["id"]), str(prompt_id))
                    if job["stop_requested"]:
                        try:
                            await self.comfy.interrupt(prompt_id)
                        except TypeError:
                            # Transitional support for pre-V7 Comfy test doubles.
                            await self.comfy.interrupt()
                        except Exception:
                            pass
                        raise ComfyAborted("任务已取消")
                    await self._emit(job, "sampling", "ComfyUI 正在生成", sequence=index, prompt_id=prompt_id)
                    try:
                        entry = await self.comfy.wait_for_history(
                            prompt_id, should_abort=lambda: bool(job["stop_requested"])
                        )
                    finally:
                        if self._prompt_owner == (str(job["id"]), str(prompt_id)):
                            self._prompt_owner = None
                    images = self._preferred_images(entry, preferred_nodes)
                    if not images:
                        raise ComfyError("任务完成但没有图片输出")
                    for image in images:
                        record = await self.history.add_image(
                            batch_id=batch_id,
                            sequence=index,
                            prompt_id=prompt_id,
                            image=image,
                            positive_prompt=str(job["plan"]["positive_prompt"]),
                            negative_prompt=str(job["plan"].get("negative_prompt") or ""),
                            sample_seed=seed,
                            prompt_seed=0,
                            settings=dict(payload),
                            resolved_selection={"natural_plan": job["plan"]},
                            resolved_prompt=str(job["plan"]["positive_prompt"]),
                        )
                        job["images"].append(record)
                    job["progress"] = {"completed": index, "total": count}
                    try:
                        await self.task_runtime.heartbeat(
                            job["id"], completed_items=index, total_items=count
                        )
                    except RuntimeError:
                        pass
                    await self.history.update_batch(batch_id, completed=index)
                    await self._emit(job, "image_completed", "图片已写入共享作品库", sequence=index)
                job["state"] = "completed"
                await self.history.update_batch(batch_id, status="completed", error="")
                await self._emit(job, "completed", "自然语言任务已完成")
                try:
                    await self.task_runtime.finish(
                        job["id"],
                        "succeeded",
                        completed_items=count,
                        result={"job": self._stored_job(job)},
                    )
                except RuntimeError:
                    pass
        except ComfyAborted:
            job["state"] = "cancelled"
            try:
                await self.history.update_batch(batch_id, status="stopped", error="用户停止任务")
            except Exception:
                pass
            await self._emit(job, "cancelled", "自然语言任务已停止")
            try:
                await self.task_runtime.finish(
                    job["id"],
                    "cancelled",
                    completed_items=int((job.get("progress") or {}).get("completed") or 0),
                    result={"job": self._stored_job(job)},
                )
            except RuntimeError:
                pass
        except Exception as exc:
            job["state"] = "failed"
            job["error"] = self._safe_text(exc)
            try:
                await self.history.update_batch(batch_id, status="error", error=self._safe_text(exc))
            except Exception:
                pass
            await self._emit(job, "failed", "自然语言任务失败", error=self._safe_text(exc))
            try:
                await self.task_runtime.finish(
                    job["id"],
                    "failed",
                    completed_items=int((job.get("progress") or {}).get("completed") or 0),
                    failed_items=1,
                    error_code=type(exc).__name__,
                    error_summary=self._safe_text(exc),
                    result={"job": self._stored_job(job)},
                )
            except RuntimeError:
                pass
        finally:
            if self._execution_owner == job["id"]:
                self._execution_owner = None
            if self._prompt_owner is not None and self._prompt_owner[0] == job["id"]:
                self._prompt_owner = None
            self.tasks.pop(job["id"], None)

    async def close(self) -> None:
        for job in self.jobs.values():
            if job["state"] not in {"completed", "failed", "cancelled"}:
                job["stop_requested"] = True
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks.values()), return_exceptions=True)
        if self._owns_task_runtime:
            await self.task_runtime.close()
