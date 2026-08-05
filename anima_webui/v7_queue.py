from __future__ import annotations

import asyncio
import inspect
import threading
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

JobExecutor = Callable[[str, dict[str, Any]], Awaitable[Mapping[str, Any] | None]]
EventPublisher = Callable[..., Awaitable[Mapping[str, Any]]]
StudioOperation = Callable[
    [str, threading.Event], Awaitable[Mapping[str, Any]] | Mapping[str, Any]
]


class V7GenerationQueue:
    """One persisted FIFO for Random and Natural generation submissions."""

    TERMINAL = frozenset(
        {"succeeded", "partial", "failed", "cancelled", "timed_out", "interrupted"}
    )

    def __init__(
        self,
        runtime: Any,
        executors: Mapping[str, JobExecutor],
        *,
        publish: EventPublisher | None = None,
        capacity: int = 100,
    ) -> None:
        self.runtime = runtime
        self.executors = dict(executors)
        self.publish = publish
        self.capacity = max(1, int(capacity))
        self._pending: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._active_id = ""
        self._active_item: dict[str, Any] | None = None
        self._active_operation: asyncio.Task[Any] | None = None
        self._worker: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def active_id(self) -> str:
        return self._active_id

    async def submit(self, intent: Mapping[str, Any]) -> dict[str, Any]:
        clean = dict(intent)
        workspace = str(clean.get("workspace") or "natural").casefold()
        if workspace not in self.executors:
            raise ValueError("workspace has no generation executor")
        async with self._lock:
            if self._closed:
                raise RuntimeError("generation queue is closed")
            if len(self._pending) >= self.capacity:
                raise RuntimeError("global generation queue is full")
            job_id = f"job_{uuid.uuid4().hex[:16]}"
            task_type = "random_batch" if workspace == "random" else "natural_generation"
            await self.runtime.create(
                task_type,
                run_id=job_id,
                mode=str(clean.get("mode") or workspace),
                total_items=int((clean.get("sampling") or {}).get("count") or 1),
                metadata={
                    "workspace": workspace,
                    "intent": clean,
                    "coordinator": "v7-global-fifo",
                },
            )
            self._pending[job_id] = {"id": job_id, "workspace": workspace, "intent": clean}
            position = len(self._pending) + (1 if self._active_id else 0)
            self._ensure_worker()
        await self._event(
            "job.queued",
            {"id": job_id, "position": position, "source_workspace": workspace},
            workspace,
            job_id,
        )
        task = await self.runtime.get(job_id)
        return {**task, "id": job_id, "position": position, "source_workspace": workspace}

    async def submit_studio(
        self,
        task_type: str,
        operation: StudioOperation,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not callable(operation):
            raise TypeError("studio operation must be callable")
        async with self._lock:
            if self._closed:
                raise RuntimeError("generation queue is closed")
            if len(self._pending) >= self.capacity:
                raise RuntimeError("global Studio queue is full")
            job_id = f"studio_{uuid.uuid4().hex}"
            await self.runtime.create(
                str(task_type),
                run_id=job_id,
                mode="manual",
                total_items=1,
                metadata={
                    "workspace": "studio",
                    "manual": True,
                    "coordinator": "v7-global-fifo",
                    **dict(metadata or {}),
                },
            )
            self._pending[job_id] = {
                "id": job_id,
                "workspace": "studio",
                "operation": operation,
                "cancel_event": threading.Event(),
            }
            position = len(self._pending) + (1 if self._active_id else 0)
            self._ensure_worker()
        await self._event(
            "job.queued",
            {"id": job_id, "position": position, "source_workspace": "studio"},
            "studio",
            job_id,
        )
        task = await self.runtime.get(job_id)
        return {**task, "id": job_id, "position": position, "source_workspace": "studio"}

    async def cancel_pending(self, job_id: str) -> dict[str, Any] | None:
        async with self._lock:
            item = self._pending.pop(str(job_id), None)
        if item is None:
            return None
        await self.runtime.finish(
            str(job_id),
            "cancelled",
            error_code="removed_from_queue",
            error_summary="Removed from the global generation queue before execution",
        )
        task = await self.runtime.get(str(job_id))
        await self._event(
            "job.cancelled",
            {**task, "id": str(job_id), "pending": True},
            str(item["workspace"]),
            str(job_id),
        )
        return task

    async def cancel_studio(self, job_id: str) -> dict[str, Any] | None:
        pending = await self.cancel_pending(job_id)
        if pending is not None:
            return pending
        async with self._lock:
            item = self._active_item if self._active_id == str(job_id) else None
            operation = self._active_operation
        if not item or item.get("workspace") != "studio":
            return None
        cancel_event = item.get("cancel_event")
        if isinstance(cancel_event, threading.Event):
            cancel_event.set()
        if operation is not None and not operation.done():
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
        for _ in range(100):
            task = await self.runtime.get(str(job_id))
            if str(task.get("status") or "") in self.TERMINAL:
                return task
            await asyncio.sleep(0)
        return await self.runtime.get(str(job_id))

    def snapshot(self) -> list[dict[str, Any]]:
        offset = 1 if self._active_id else 0
        return [
            {
                "id": job_id,
                "source_workspace": str(item["workspace"]),
                "position": index + offset,
            }
            for index, (job_id, item) in enumerate(self._pending.items(), 1)
        ]

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="anima-v7-global-fifo")

    async def _run(self) -> None:
        while True:
            async with self._lock:
                if self._closed or not self._pending:
                    self._worker = None
                    return
                job_id, item = self._pending.popitem(last=False)
                self._active_id = job_id
                self._active_item = item
            workspace = str(item["workspace"])
            try:
                await self._event(
                    "job.dispatching",
                    {"id": job_id, "source_workspace": workspace},
                    workspace,
                    job_id,
                )
                if workspace == "studio":
                    await self.runtime.start(job_id, total_items=1)
                    result = item["operation"](job_id, item["cancel_event"])
                    if inspect.isawaitable(result):
                        self._active_operation = asyncio.create_task(result)
                        result = await self._active_operation
                    finished = await self.runtime.finish(
                        job_id,
                        "succeeded",
                        completed_items=1,
                        result={"operation": dict(result or {})},
                    )
                    status = str(finished.get("status") or "succeeded")
                    await self._event(
                        f"job.{status}" if status in self.TERMINAL else "job.updated",
                        finished,
                        workspace,
                        job_id,
                    )
                else:
                    await self.executors[workspace](job_id, dict(item["intent"]))
                    task = await self.runtime.get(job_id)
                    status = str(task.get("status") or "")
                    event_type = (
                        f"job.{status}"
                        if status in self.TERMINAL
                        else "job.updated"
                    )
                    await self._event(event_type, task, workspace, job_id)
            except asyncio.CancelledError:
                if self._closed:
                    raise
                await self.runtime.finish(
                    job_id,
                    "cancelled",
                    error_code="cancelled",
                    error_summary="Studio operation cancelled by the operator",
                )
                await self._event(
                    "job.cancelled",
                    await self.runtime.get(job_id),
                    workspace,
                    job_id,
                )
            except Exception as error:
                try:
                    task = await self.runtime.get(job_id)
                    if str(task.get("status") or "") not in self.TERMINAL:
                        task = await self.runtime.finish(
                            job_id,
                            "failed",
                            error_code=type(error).__name__,
                            error_summary=str(error)[:1000],
                        )
                finally:
                    status = str(task.get("status") or "failed")
                    await self._event(
                        f"job.{status}" if status in self.TERMINAL else "job.failed",
                        {**task, "id": job_id, "error": str(error)[:1000]},
                        workspace,
                        job_id,
                    )
            finally:
                async with self._lock:
                    if self._active_id == job_id:
                        self._active_id = ""
                        self._active_item = None
                        self._active_operation = None

    async def _event(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        workspace: str,
        entity_id: str,
    ) -> None:
        if self.publish is not None:
            await self.publish(
                event_type,
                payload,
                workspace=workspace,
                entity_id=entity_id,
            )

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            pending = list(self._pending.items())
            self._pending.clear()
            worker = self._worker
            active_id = self._active_id
            active_workspace = str((self._active_item or {}).get("workspace") or "studio")
        for job_id, item in pending:
            try:
                task = await self.runtime.finish(
                    job_id,
                    "interrupted",
                    error_code="studio_shutdown",
                    error_summary="Studio stopped before the queued task began",
                )
                await self._event(
                    "job.interrupted",
                    task,
                    str(item.get("workspace") or "studio"),
                    job_id,
                )
            except Exception:
                pass
        if worker is not None and not worker.done():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        if active_id:
            try:
                task = await self.runtime.get(active_id)
                if str(task.get("status") or "") not in self.TERMINAL:
                    task = await self.runtime.finish(
                        active_id,
                        "interrupted",
                        error_code="studio_shutdown",
                        error_summary="Studio stopped while the task was running",
                    )
                    await self._event(
                        "job.interrupted",
                        task,
                        active_workspace,
                        active_id,
                    )
            except Exception:
                pass


class V7StudioQueueAdapter:
    """Studio route operation-manager surface backed by the global FIFO."""

    def __init__(self, queue: V7GenerationQueue) -> None:
        self.queue = queue

    async def submit(
        self,
        task_type: str,
        operation: StudioOperation,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self.queue.submit_studio(
            task_type,
            operation,
            metadata=metadata,
        )

    async def cancel(self, run_id: str) -> dict[str, Any] | None:
        return await self.queue.cancel_studio(run_id)

    async def close(self) -> None:
        return None
