from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from anima_studio.task_store import TaskStore


class _RuntimeLease:
    """Cross-process guard for the task database recovery owner."""

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(f"{Path(database_path)}.runtime.lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+b")
        self._locked = False
        try:
            self._handle.seek(0, os.SEEK_END)
            if self._handle.tell() == 0:
                self._handle.write(b"\0")
                self._handle.flush()
            self._handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._locked = True
        except (OSError, PermissionError) as exc:
            self._handle.close()
            raise RuntimeError(
                f"Studio task runtime is already active for {Path(database_path)}"
            ) from exc

    def close(self) -> None:
        if not self._locked:
            return
        try:
            self._handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._locked = False
            self._handle.close()


class StudioTaskRuntime:
    """Async boundary around the shared, thread-safe operational task store."""

    def __init__(self, database_path: str | Path) -> None:
        self._lease = _RuntimeLease(database_path)
        try:
            self.store = TaskStore(
                database_path,
                retention_days=30,
                max_tasks=2_000,
                max_events=50_000,
                max_runtime_logs=20_000,
            )
        except Exception:
            self._lease.close()
            raise
        # Persisted work is never replayed implicitly. A running record means
        # the previous process disappeared before it could write a terminal state.
        recovery = (
            (
                "running",
                "restart_while_running",
                "Studio restarted while the task was running; retry it explicitly.",
            ),
            (
                "queued",
                "restart_before_start",
                "Studio restarted before the queued task began; retry it explicitly.",
            ),
        )
        self.recovered_tasks: list[dict[str, Any]] = []
        for status, error_code, error_summary in recovery:
            for task in self.store.recent_tasks(limit=500, statuses=[status]):
                self.recovered_tasks.append(
                    self.store.finish_task(
                        task["run_id"],
                        "interrupted",
                        error_code=error_code,
                        error_summary=error_summary,
                    )
                )

    async def create(
        self,
        task_type: str,
        *,
        run_id: str,
        mode: str = "",
        total_items: int = 0,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        await asyncio.to_thread(
            self.store.create_task,
            task_type,
            run_id=run_id,
            mode=mode,
            total_items=total_items,
            metadata=metadata,
        )
        return await self.get(run_id)

    async def start(self, run_id: str, *, total_items: int | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.store.start_task,
            run_id,
            total_items=total_items,
        )

    async def heartbeat(
        self,
        run_id: str,
        *,
        completed_items: int | None = None,
        failed_items: int | None = None,
        total_items: int | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.store.heartbeat,
            run_id,
            completed_items=completed_items,
            failed_items=failed_items,
            total_items=total_items,
        )

    async def event(
        self,
        run_id: str,
        phase: str,
        message: str,
        *,
        level: str = "INFO",
        event_code: str = "",
        details: Mapping[str, Any] | None = None,
        batch_index: int | None = None,
        batch_total: int | None = None,
    ) -> int:
        return await asyncio.to_thread(
            self.store.append_event,
            run_id,
            phase,
            message,
            level=level,
            event_code=event_code,
            details=details,
            batch_index=batch_index,
            batch_total=batch_total,
        )

    async def finish(
        self,
        run_id: str,
        status: str,
        *,
        completed_items: int = 0,
        failed_items: int = 0,
        error_code: str = "",
        error_summary: str = "",
        result: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.store.finish_task,
            run_id,
            status,
            completed_items=completed_items,
            failed_items=failed_items,
            error_code=error_code,
            error_summary=error_summary,
            result=result,
        )

    async def get(self, run_id: str) -> dict[str, Any]:
        task = await asyncio.to_thread(self.store.get_task, run_id)
        if task is None:
            raise KeyError(run_id)
        return task

    async def list(
        self,
        *,
        limit: int = 50,
        statuses: Sequence[str] | None = None,
        task_type: str = "",
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self.store.recent_tasks,
            limit=limit,
            statuses=statuses,
            task_type=task_type,
        )

    async def events(
        self,
        *,
        run_id: str = "",
        after_seq: int = 0,
        limit: int = 500,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.store.read_events,
            run_id=run_id,
            after_seq=after_seq,
            limit=limit,
        )

    async def logs(self, *, limit: int = 200) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.store.recent_runtime_logs, limit=limit)

    async def read_logs(
        self,
        *,
        after_seq: int = 0,
        limit: int = 200,
        levels: Sequence[str] | None = None,
        category: str = "",
        run_id: str = "",
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.store.read_runtime_logs,
            after_seq=after_seq,
            limit=limit,
            levels=levels,
            category=category,
            run_id=run_id,
        )

    async def clear_logs(self) -> int:
        return await asyncio.to_thread(self.store.clear_runtime_logs)

    async def close(self) -> None:
        try:
            await asyncio.to_thread(self.store.close)
        finally:
            self._lease.close()


async def publish_recovered_task_events(runtime: StudioTaskRuntime, events: Any) -> int:
    """Publish one resumable SSE event for each task interrupted during startup."""
    recovered = list(runtime.recovered_tasks)
    for task in recovered:
        metadata = task.get("metadata") if isinstance(task.get("metadata"), Mapping) else {}
        task_type = str(task.get("task_type") or "")
        workspace = str(metadata.get("workspace") or "").casefold()
        if workspace not in {"random", "natural", "studio"}:
            workspace = "random" if task_type == "random_batch" else (
                "natural" if task_type == "natural_generation" else "studio"
            )
        run_id = str(task.get("run_id") or "")
        await events.publish(
            "job.interrupted",
            {
                "id": run_id,
                "run_id": run_id,
                "status": "interrupted",
                "source_workspace": workspace,
                "type": "generation" if workspace in {"random", "natural"} else "studio_operation",
                "error_code": str(task.get("error_code") or ""),
                "error_summary": str(task.get("error_summary") or ""),
            },
            workspace=workspace,
            entity_id=run_id,
        )
    runtime.recovered_tasks.clear()
    return len(recovered)
