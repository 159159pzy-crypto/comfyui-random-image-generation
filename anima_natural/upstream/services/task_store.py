"""Persistent task/event and runtime-log storage for the plugin WebUI.

The store intentionally persists operational metadata only.  Prompt bodies,
complete LLM responses, credentials, cookies, and authorization headers are
removed before JSON is written.  It is safe to share one ``TaskStore`` instance
between AstrBot's event-loop thread and worker threads.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from .log_console import redact_log_text


TASK_STATUSES = (
    "queued",
    "running",
    "succeeded",
    "partial",
    "failed",
    "cancelled",
    "timed_out",
    "interrupted",
)
TERMINAL_TASK_STATUSES = frozenset(TASK_STATUSES) - {"queued", "running"}

DEFAULT_RETENTION_DAYS = 30
DEFAULT_MAX_TASKS = 2000
DEFAULT_MAX_EVENTS = 50000
DEFAULT_MAX_RUNTIME_LOGS = 20000
DEFAULT_CLEANUP_INTERVAL = 100

MAX_EVENT_MESSAGE_CHARS = 4000
MAX_LOG_MESSAGE_CHARS = 12000
MAX_SHORT_TEXT_CHARS = 1000

_SECRET_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "api_token",
        "access_token",
        "refresh_token",
        "token",
        "password",
        "passwd",
        "secret",
        "cookie",
        "cookies",
        "authorization",
    }
)
_RAW_CONTENT_KEYS = frozenset(
    {
        "prompt",
        "system_prompt",
        "user_prompt",
        "raw_prompt",
        "prompt_text",
        "response",
        "raw_response",
        "llm_response",
        "response_text",
        "completion",
        "completion_text",
    }
)


class TaskStoreError(RuntimeError):
    """The persistent task store could not complete an operation."""


def _clean_key(value: Any) -> str:
    return str(value or "").strip().casefold().replace("-", "_")


def _bounded_text(value: Any, limit: int) -> str:
    text = redact_log_text(str(value or ""))
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[content truncated]"


def _omitted_content(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        size = len(value)
    elif isinstance(value, (bytes, bytearray)):
        size = len(value)
    else:
        try:
            size = len(json.dumps(value, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            size = 0
    return {"omitted": True, "chars": size}


def _sanitize_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    """Return a JSON-safe, redacted value with raw LLM content removed."""
    normalized_key = _clean_key(key)
    if normalized_key in _SECRET_KEYS or any(
        token in normalized_key
        for token in ("password", "secret", "authorization", "cookie")
    ):
        return "***"
    if normalized_key in _RAW_CONTENT_KEYS:
        return _omitted_content(value)
    if depth >= 8:
        return "[maximum nesting depth reached]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _bounded_text(value, MAX_LOG_MESSAGE_CHARS)
    if isinstance(value, (bytes, bytearray)):
        return {"omitted": True, "bytes": len(value)}
    if isinstance(value, Mapping):
        return {
            str(item_key): _sanitize_value(
                item_value,
                key=str(item_key),
                depth=depth + 1,
            )
            for item_key, item_value in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            _sanitize_value(item, depth=depth + 1)
            for item in list(value)[:500]
        ]
    return _bounded_text(value, MAX_SHORT_TEXT_CHARS)


def _json_dump(value: Optional[Mapping[str, Any]]) -> str:
    cleaned = _sanitize_value(dict(value or {}))
    return json.dumps(cleaned, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_load(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        payload = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


class TaskStore:
    """Thread-safe SQLite store for resumable task timelines and plugin logs."""

    def __init__(
        self,
        database_path: Path | str,
        *,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        max_tasks: int = DEFAULT_MAX_TASKS,
        max_events: int = DEFAULT_MAX_EVENTS,
        max_runtime_logs: int = DEFAULT_MAX_RUNTIME_LOGS,
        cleanup_interval: int = DEFAULT_CLEANUP_INTERVAL,
    ) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.retention_days = max(0, int(retention_days))
        self.max_tasks = max(1, int(max_tasks))
        self.max_events = max(1, int(max_events))
        self.max_runtime_logs = max(1, int(max_runtime_logs))
        self.cleanup_interval = max(1, int(cleanup_interval))
        self._lock = threading.RLock()
        self._writes_since_cleanup = 0
        try:
            self._connection = sqlite3.connect(
                str(self.database_path),
                timeout=10.0,
                isolation_level=None,
                check_same_thread=False,
            )
            self._connection.row_factory = sqlite3.Row
            with self._lock:
                self._connection.execute("PRAGMA busy_timeout = 10000")
                self._connection.execute("PRAGMA foreign_keys = ON")
                self._connection.execute("PRAGMA journal_mode = WAL")
                self._connection.execute("PRAGMA synchronous = NORMAL")
                self._create_schema_locked()
                self.interrupt_running_tasks()
                self.cleanup()
        except sqlite3.Error as exc:
            raise TaskStoreError(f"Unable to initialize task store: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
                self._connection = None

    def __enter__(self) -> "TaskStore":
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.close()

    @property
    def journal_mode(self) -> str:
        with self._lock:
            row = self._execute_locked("PRAGMA journal_mode").fetchone()
            return str(row[0] if row else "").casefold()

    def create_task(
        self,
        task_type: str,
        *,
        mode: str = "",
        status: str = "queued",
        requested_by: str = "",
        total_items: int = 0,
        metadata: Optional[Mapping[str, Any]] = None,
        run_id: str = "",
        timestamp: Optional[float] = None,
    ) -> str:
        status = self._validate_status(status)
        task_type = _bounded_text(task_type, 100).strip()
        if not task_type:
            raise ValueError("task_type must not be empty")
        identifier = str(run_id or uuid.uuid4().hex).strip()
        if not identifier:
            raise ValueError("run_id must not be empty")
        now = self._timestamp(timestamp)
        started_at = now if status == "running" else None
        heartbeat_at = now if status == "running" else None
        with self._lock:
            try:
                self._connection.execute(
                    """
                    INSERT INTO task_runs (
                        run_id, task_type, mode, status, requested_by,
                        total_items, completed_items, failed_items,
                        created_at, started_at, heartbeat_at, ended_at,
                        error_code, error_summary, metadata_json, result_json
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, NULL, '', '', ?, '{}')
                    """,
                    (
                        identifier,
                        task_type,
                        _bounded_text(mode, 100),
                        status,
                        _bounded_text(requested_by, 200),
                        max(0, int(total_items)),
                        now,
                        started_at,
                        heartbeat_at,
                        _json_dump(metadata),
                    ),
                )
                self._append_event_locked(
                    identifier,
                    "lifecycle",
                    "任务记录已创建，等待进入执行阶段。",
                    event_code="task_created",
                    details={"status": status, "total_items": max(0, int(total_items))},
                    timestamp=now,
                )
                if status == "running":
                    self._append_event_locked(
                        identifier,
                        "lifecycle",
                        "任务已在创建时直接进入运行状态。",
                        event_code="task_started",
                        details={"total_items": max(0, int(total_items))},
                        timestamp=now,
                    )
            except sqlite3.IntegrityError as exc:
                raise TaskStoreError(f"Task already exists: {identifier}") from exc
            except sqlite3.Error as exc:
                raise TaskStoreError(f"Unable to create task: {exc}") from exc
            self._after_write_locked()
        return identifier

    def get_task(self, run_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._execute_locked(
                "SELECT * FROM task_runs WHERE run_id = ?",
                (str(run_id),),
            ).fetchone()
            return self._task_row(row) if row else None

    def start_task(
        self,
        run_id: str,
        *,
        total_items: Optional[int] = None,
        timestamp: Optional[float] = None,
    ) -> dict[str, Any]:
        now = self._timestamp(timestamp)
        updates = ["status = 'running'", "started_at = COALESCE(started_at, ?)", "heartbeat_at = ?", "ended_at = NULL"]
        values: list[Any] = [now, now]
        if total_items is not None:
            updates.append("total_items = ?")
            values.append(max(0, int(total_items)))
        values.append(str(run_id))
        with self._lock:
            previous = self._execute_locked(
                "SELECT status FROM task_runs WHERE run_id = ?", (str(run_id),)
            ).fetchone()
            if previous is None:
                raise TaskStoreError(f"Unknown task: {run_id}")
            transitioning = str(previous["status"]) != "running"
            if transitioning:
                self._append_event_locked(
                    run_id,
                    "lifecycle",
                    "任务即将离开队列并开始执行。",
                    event_code="task_starting",
                    details={"total_items": total_items},
                    timestamp=now,
                )
            cursor = self._execute_locked(
                f"UPDATE task_runs SET {', '.join(updates)} WHERE run_id = ?",
                values,
            )
            self._require_task(cursor, run_id)
            if transitioning:
                self._append_event_locked(
                    run_id,
                    "lifecycle",
                    "任务已进入运行状态。",
                    event_code="task_started",
                    details={"total_items": total_items},
                    timestamp=now,
                )
            self._after_write_locked()
            return self._task_row(
                self._execute_locked(
                    "SELECT * FROM task_runs WHERE run_id = ?", (str(run_id),)
                ).fetchone()
            )

    def heartbeat(
        self,
        run_id: str,
        *,
        completed_items: Optional[int] = None,
        failed_items: Optional[int] = None,
        total_items: Optional[int] = None,
        timestamp: Optional[float] = None,
    ) -> dict[str, Any]:
        updates = ["heartbeat_at = ?"]
        values: list[Any] = [self._timestamp(timestamp)]
        for column, value in (
            ("completed_items", completed_items),
            ("failed_items", failed_items),
            ("total_items", total_items),
        ):
            if value is not None:
                updates.append(f"{column} = ?")
                values.append(max(0, int(value)))
        values.append(str(run_id))
        with self._lock:
            cursor = self._execute_locked(
                f"UPDATE task_runs SET {', '.join(updates)} WHERE run_id = ?",
                values,
            )
            self._require_task(cursor, run_id)
            self._after_write_locked()
            return self._task_row(
                self._execute_locked(
                    "SELECT * FROM task_runs WHERE run_id = ?", (str(run_id),)
                ).fetchone()
            )

    def finish_task(
        self,
        run_id: str,
        status: str,
        *,
        completed_items: Optional[int] = None,
        failed_items: Optional[int] = None,
        error_code: str = "",
        error_summary: str = "",
        result: Optional[Mapping[str, Any]] = None,
        timestamp: Optional[float] = None,
    ) -> dict[str, Any]:
        status = self._validate_status(status)
        if status not in TERMINAL_TASK_STATUSES:
            raise ValueError("finish_task requires a terminal status")
        now = self._timestamp(timestamp)
        updates = [
            "status = ?",
            "heartbeat_at = ?",
            "ended_at = ?",
            "error_code = ?",
            "error_summary = ?",
            "result_json = ?",
        ]
        values: list[Any] = [
            status,
            now,
            now,
            _bounded_text(error_code, 100),
            _bounded_text(error_summary, MAX_EVENT_MESSAGE_CHARS),
            _json_dump(result),
        ]
        if completed_items is not None:
            updates.append("completed_items = ?")
            values.append(max(0, int(completed_items)))
        if failed_items is not None:
            updates.append("failed_items = ?")
            values.append(max(0, int(failed_items)))
        values.append(str(run_id))
        with self._lock:
            previous = self._execute_locked(
                "SELECT status FROM task_runs WHERE run_id = ?",
                (str(run_id),),
            ).fetchone()
            if previous is None:
                raise TaskStoreError(f"Unknown task: {run_id}")
            final_level = (
                "ERROR"
                if status in {"failed", "timed_out"}
                else "WARNING"
                if status in {"partial", "cancelled", "interrupted"}
                else "INFO"
            )
            self._append_event_locked(
                run_id,
                "lifecycle",
                f"任务正在收尾，准备写入终态 {status}。",
                level=final_level,
                event_code="task_finishing",
                details={
                    "previous_status": str(previous["status"]),
                    "target_status": status,
                    "completed_items": completed_items,
                    "failed_items": failed_items,
                    "error_code": error_code,
                },
                timestamp=now,
            )
            cursor = self._execute_locked(
                f"UPDATE task_runs SET {', '.join(updates)} WHERE run_id = ?",
                values,
            )
            self._require_task(cursor, run_id)
            final_message = (
                f"任务已结束：状态={status}，成功={max(0, int(completed_items or 0))}，"
                f"失败={max(0, int(failed_items or 0))}。"
            )
            if error_summary:
                final_message += f" 原因：{_bounded_text(error_summary, 500)}"
            self._append_event_locked(
                run_id,
                "lifecycle",
                final_message,
                level=final_level,
                event_code="task_finished",
                details={
                    "status": status,
                    "completed_items": completed_items,
                    "failed_items": failed_items,
                    "error_code": error_code,
                },
                timestamp=now,
            )
            self._after_write_locked()
            return self._task_row(
                self._execute_locked(
                    "SELECT * FROM task_runs WHERE run_id = ?", (str(run_id),)
                ).fetchone()
            )

    def append_event(
        self,
        run_id: str,
        phase: str,
        message: str,
        *,
        level: str = "INFO",
        item_name: str = "",
        batch_index: Optional[int] = None,
        batch_total: Optional[int] = None,
        event_code: str = "",
        duration_ms: Optional[int] = None,
        attempt: int = 1,
        details: Optional[Mapping[str, Any]] = None,
        timestamp: Optional[float] = None,
    ) -> int:
        with self._lock:
            try:
                sequence = self._append_event_locked(
                    run_id,
                    phase,
                    message,
                    level=level,
                    item_name=item_name,
                    batch_index=batch_index,
                    batch_total=batch_total,
                    event_code=event_code,
                    duration_ms=duration_ms,
                    attempt=attempt,
                    details=details,
                    timestamp=timestamp,
                )
            except sqlite3.IntegrityError as exc:
                raise TaskStoreError(f"Unknown task: {run_id}") from exc
            except sqlite3.Error as exc:
                raise TaskStoreError(f"Unable to append task event: {exc}") from exc
            self._after_write_locked()
            return sequence

    def _append_event_locked(
        self,
        run_id: str,
        phase: str,
        message: str,
        *,
        level: str = "INFO",
        item_name: str = "",
        batch_index: Optional[int] = None,
        batch_total: Optional[int] = None,
        event_code: str = "",
        duration_ms: Optional[int] = None,
        attempt: int = 1,
        details: Optional[Mapping[str, Any]] = None,
        timestamp: Optional[float] = None,
    ) -> int:
        """Atomically append a task event and its persistent-console mirror."""
        connection = getattr(self, "_connection", None)
        if connection is None:
            raise TaskStoreError("Task store is closed")
        event_time = self._timestamp(timestamp)
        normalized_level = self._normalize_level(level)
        clean_phase = _bounded_text(phase, 100)
        clean_item = _bounded_text(item_name, 500)
        clean_code = _bounded_text(event_code, 100)
        clean_message = _bounded_text(message, MAX_EVENT_MESSAGE_CHARS)
        clean_batch_index = self._optional_nonnegative_int(batch_index)
        clean_batch_total = self._optional_nonnegative_int(batch_total)
        clean_duration = self._optional_nonnegative_int(duration_ms)
        clean_attempt = max(1, int(attempt))
        task_row = connection.execute(
            "SELECT task_type FROM task_runs WHERE run_id = ?",
            (str(run_id),),
        ).fetchone()
        if task_row is None:
            raise sqlite3.IntegrityError(f"Unknown task: {run_id}")
        task_type = str(task_row["task_type"] or "task")
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT INTO task_events (
                    run_id, timestamp, level, phase, item_name,
                    batch_index, batch_total, event_code, message,
                    duration_ms, attempt, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(run_id),
                    event_time,
                    normalized_level,
                    clean_phase,
                    clean_item,
                    clean_batch_index,
                    clean_batch_total,
                    clean_code,
                    clean_message,
                    clean_duration,
                    clean_attempt,
                    _json_dump(details),
                ),
            )
            runtime_message = self._task_event_runtime_message(
                run_id=str(run_id),
                task_type=task_type,
                phase=clean_phase,
                message=clean_message,
                item_name=clean_item,
                batch_index=clean_batch_index,
                batch_total=clean_batch_total,
                event_code=clean_code,
                duration_ms=clean_duration,
                attempt=clean_attempt,
            )
            should_mirror = True
            if clean_code == "provider_heartbeat":
                previous = connection.execute(
                    """
                    SELECT message FROM runtime_logs
                    WHERE run_id = ? AND source = ?
                    ORDER BY seq DESC LIMIT 1
                    """,
                    (str(run_id), f"task/{task_type}"),
                ).fetchone()
                should_mirror = previous is None or str(previous["message"]) != runtime_message
            if should_mirror:
                connection.execute(
                    """
                    INSERT INTO runtime_logs (
                        timestamp, level, category, source, line, message, run_id
                    ) VALUES (?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        event_time,
                        normalized_level,
                        self._task_log_category(task_type),
                        f"task/{task_type}",
                        runtime_message,
                        str(run_id),
                    ),
                )
            connection.execute("COMMIT")
            return int(cursor.lastrowid)
        except Exception:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise

    @staticmethod
    def _task_log_category(task_type: str) -> str:
        value = str(task_type or "").casefold()
        if "lora" in value:
            return "lora"
        if "generation" in value or "image" in value or "comfy" in value:
            return "generation"
        if "llm" in value or "provider" in value:
            return "llm"
        return "plugin"

    @staticmethod
    def _task_event_runtime_message(
        *,
        run_id: str,
        task_type: str,
        phase: str,
        message: str,
        item_name: str,
        batch_index: Optional[int],
        batch_total: Optional[int],
        event_code: str,
        duration_ms: Optional[int],
        attempt: int,
    ) -> str:
        context = [
            f"任务={run_id}",
            f"类型={task_type}",
            f"阶段={phase or 'event'}",
        ]
        if item_name:
            context.append(f"LoRA={item_name}")
        if batch_index is not None:
            context.append(f"批次={batch_index}/{batch_total or '?'}")
        if attempt > 1:
            context.append(f"尝试={attempt}")
        if duration_ms is not None:
            context.append(f"耗时={duration_ms}ms")
        if event_code:
            context.append(f"事件={event_code}")
        return _bounded_text(" | ".join(context) + f" | {message}", MAX_LOG_MESSAGE_CHARS)

    def read_events(
        self,
        *,
        run_id: str = "",
        after_seq: int = 0,
        limit: int = 500,
    ) -> dict[str, Any]:
        safe_after = max(0, int(after_seq))
        safe_limit = max(1, min(2000, int(limit)))
        conditions = ["seq > ?"]
        values: list[Any] = [safe_after]
        if run_id:
            conditions.append("run_id = ?")
            values.append(str(run_id))
        values.append(safe_limit)
        with self._lock:
            rows = self._execute_locked(
                f"SELECT * FROM task_events WHERE {' AND '.join(conditions)} ORDER BY seq ASC LIMIT ?",
                values,
            ).fetchall()
            entries = [self._event_row(row) for row in rows]
            cursor = entries[-1]["seq"] if entries else safe_after
            return {"entries": entries, "cursor": cursor}

    def recent_tasks(
        self,
        *,
        limit: int = 50,
        statuses: Optional[Sequence[str]] = None,
        task_type: str = "",
    ) -> list[dict[str, Any]]:
        safe_limit = max(1, min(500, int(limit)))
        conditions: list[str] = []
        values: list[Any] = []
        if statuses:
            normalized = [self._validate_status(status) for status in statuses]
            placeholders = ",".join("?" for _ in normalized)
            conditions.append(f"status IN ({placeholders})")
            values.extend(normalized)
        if task_type:
            conditions.append("task_type = ?")
            values.append(str(task_type))
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        values.append(safe_limit)
        with self._lock:
            rows = self._execute_locked(
                f"SELECT * FROM task_runs {where} ORDER BY created_at DESC, run_id DESC LIMIT ?",
                values,
            ).fetchall()
            return [self._task_row(row) for row in rows]

    def interrupt_running_tasks(self, *, timestamp: Optional[float] = None) -> int:
        now = self._timestamp(timestamp)
        with self._lock:
            running = self._execute_locked(
                "SELECT run_id FROM task_runs WHERE status = 'running'"
            ).fetchall()
            cursor = self._execute_locked(
                """
                UPDATE task_runs
                SET status = 'interrupted', heartbeat_at = ?, ended_at = ?,
                    error_code = CASE WHEN error_code = '' THEN 'plugin_restarted' ELSE error_code END,
                    error_summary = CASE
                        WHEN error_summary = '' THEN 'Plugin restarted while task was running'
                        ELSE error_summary
                    END
                WHERE status = 'running'
                """,
                (now, now),
            )
            for row in running:
                self._append_event_locked(
                    str(row["run_id"]),
                    "lifecycle",
                    "插件重新启动，之前仍在运行的任务已标记为中断。",
                    level="WARNING",
                    event_code="task_interrupted_on_startup",
                    details={"status": "interrupted", "error_code": "plugin_restarted"},
                    timestamp=now,
                )
            if running:
                self._after_write_locked()
            return max(0, int(cursor.rowcount))

    def append_runtime_log(
        self,
        level: str,
        category: str,
        source: str,
        line: int,
        message: str,
        *,
        run_id: str = "",
        timestamp: Optional[float] = None,
    ) -> int:
        with self._lock:
            try:
                cursor = self._connection.execute(
                    """
                    INSERT INTO runtime_logs (
                        timestamp, level, category, source, line, message, run_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self._timestamp(timestamp),
                        self._normalize_level(level),
                        _bounded_text(category, 100),
                        _bounded_text(source, 500),
                        max(0, int(line)),
                        _bounded_text(message, MAX_LOG_MESSAGE_CHARS),
                        _bounded_text(run_id, 100),
                    ),
                )
            except sqlite3.Error as exc:
                raise TaskStoreError(f"Unable to append runtime log: {exc}") from exc
            sequence = int(cursor.lastrowid)
            self._after_write_locked()
            return sequence

    def read_runtime_logs(
        self,
        *,
        after_seq: int = 0,
        limit: int = 500,
        levels: Optional[Sequence[str]] = None,
        category: str = "",
        run_id: str = "",
    ) -> dict[str, Any]:
        safe_after = max(0, int(after_seq))
        safe_limit = max(1, min(2000, int(limit)))
        conditions = ["seq > ?"]
        values: list[Any] = [safe_after]
        if levels:
            normalized = [self._normalize_level(level) for level in levels]
            placeholders = ",".join("?" for _ in normalized)
            conditions.append(f"level IN ({placeholders})")
            values.extend(normalized)
        if category:
            conditions.append("category = ?")
            values.append(str(category))
        if run_id:
            conditions.append("run_id = ?")
            values.append(str(run_id))
        values.append(safe_limit)
        with self._lock:
            rows = self._execute_locked(
                f"SELECT * FROM runtime_logs WHERE {' AND '.join(conditions)} ORDER BY seq ASC LIMIT ?",
                values,
            ).fetchall()
            entries = [dict(row) for row in rows]
            cursor = entries[-1]["seq"] if entries else safe_after
            return {"entries": entries, "cursor": cursor}

    def recent_runtime_logs(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        """Return the newest persisted runtime logs in chronological order."""
        safe_limit = max(1, min(5000, int(limit)))
        with self._lock:
            rows = self._execute_locked(
                "SELECT * FROM runtime_logs ORDER BY seq DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
            return [dict(row) for row in reversed(rows)]

    def clear_runtime_logs(self) -> int:
        """Delete the persisted runtime-log view without touching task events."""
        with self._lock:
            try:
                cursor = self._connection.execute("DELETE FROM runtime_logs")
            except sqlite3.Error as exc:
                raise TaskStoreError(
                    f"Unable to clear persistent runtime logs: {exc}"
                ) from exc
            return max(0, int(cursor.rowcount))

    def cleanup(self, *, now: Optional[float] = None) -> dict[str, int]:
        timestamp = self._timestamp(now)
        removed = {"tasks": 0, "events": 0, "runtime_logs": 0}
        with self._lock:
            if self.retention_days > 0:
                cutoff = timestamp - self.retention_days * 86400
                cursor = self._execute_locked(
                    """
                    DELETE FROM task_runs
                    WHERE status NOT IN ('queued', 'running')
                      AND COALESCE(ended_at, created_at) < ?
                    """,
                    (cutoff,),
                )
                removed["tasks"] += max(0, int(cursor.rowcount))
                cursor = self._execute_locked(
                    "DELETE FROM runtime_logs WHERE timestamp < ?", (cutoff,)
                )
                removed["runtime_logs"] += max(0, int(cursor.rowcount))

            removed["tasks"] += self._trim_tasks_locked(self.max_tasks)
            removed["events"] += self._trim_table_locked(
                "task_events", "seq DESC", self.max_events
            )
            removed["runtime_logs"] += self._trim_table_locked(
                "runtime_logs", "seq DESC", self.max_runtime_logs
            )
            self._writes_since_cleanup = 0
        return removed

    def _create_schema_locked(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS task_runs (
                run_id TEXT PRIMARY KEY,
                task_type TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                requested_by TEXT NOT NULL DEFAULT '',
                total_items INTEGER NOT NULL DEFAULT 0,
                completed_items INTEGER NOT NULL DEFAULT 0,
                failed_items INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                started_at REAL,
                heartbeat_at REAL,
                ended_at REAL,
                error_code TEXT NOT NULL DEFAULT '',
                error_summary TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                result_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_task_runs_recent
                ON task_runs(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_task_runs_status
                ON task_runs(status, created_at DESC);

            CREATE TABLE IF NOT EXISTS task_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                timestamp REAL NOT NULL,
                level TEXT NOT NULL,
                phase TEXT NOT NULL DEFAULT '',
                item_name TEXT NOT NULL DEFAULT '',
                batch_index INTEGER,
                batch_total INTEGER,
                event_code TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL,
                duration_ms INTEGER,
                attempt INTEGER NOT NULL DEFAULT 1,
                details_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(run_id) REFERENCES task_runs(run_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_task_events_run_seq
                ON task_events(run_id, seq);

            CREATE TABLE IF NOT EXISTS runtime_logs (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                level TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                line INTEGER NOT NULL DEFAULT 0,
                message TEXT NOT NULL,
                run_id TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_runtime_logs_time
                ON runtime_logs(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_runtime_logs_run_seq
                ON runtime_logs(run_id, seq);
            """
        )

    def _execute_locked(
        self,
        sql: str,
        parameters: Sequence[Any] = (),
    ) -> sqlite3.Cursor:
        connection = getattr(self, "_connection", None)
        if connection is None:
            raise TaskStoreError("Task store is closed")
        try:
            return connection.execute(sql, tuple(parameters))
        except sqlite3.Error as exc:
            raise TaskStoreError(f"Task store query failed: {exc}") from exc

    def _after_write_locked(self) -> None:
        self._writes_since_cleanup += 1
        if self._writes_since_cleanup >= self.cleanup_interval:
            self.cleanup()

    def _trim_table_locked(self, table: str, order_by: str, capacity: int) -> int:
        row = self._execute_locked(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
        count = int(row["count"] if row else 0)
        excess = max(0, count - capacity)
        if not excess:
            return 0
        key = "run_id" if table == "task_runs" else "seq"
        cursor = self._execute_locked(
            f"""
            DELETE FROM {table}
            WHERE {key} IN (
                SELECT {key} FROM {table}
                ORDER BY {order_by}
                LIMIT ? OFFSET ?
            )
            """,
            (excess, capacity),
        )
        return max(0, int(cursor.rowcount))

    def _trim_tasks_locked(self, capacity: int) -> int:
        """Trim oldest terminal tasks while never deleting active work."""
        row = self._execute_locked("SELECT COUNT(*) AS count FROM task_runs").fetchone()
        count = int(row["count"] if row else 0)
        excess = max(0, count - capacity)
        if not excess:
            return 0
        cursor = self._execute_locked(
            """
            DELETE FROM task_runs
            WHERE run_id IN (
                SELECT run_id FROM task_runs
                WHERE status NOT IN ('queued', 'running')
                ORDER BY created_at ASC, run_id ASC
                LIMIT ?
            )
            """,
            (excess,),
        )
        return max(0, int(cursor.rowcount))

    @staticmethod
    def _timestamp(value: Optional[float]) -> float:
        return float(time.time() if value is None else value)

    @staticmethod
    def _optional_nonnegative_int(value: Optional[int]) -> Optional[int]:
        return None if value is None else max(0, int(value))

    @staticmethod
    def _normalize_level(value: str) -> str:
        level = str(value or "INFO").strip().upper()
        if level == "WARN":
            return "WARNING"
        return level if level in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"} else "INFO"

    @staticmethod
    def _validate_status(status: str) -> str:
        normalized = str(status or "").strip().casefold()
        if normalized not in TASK_STATUSES:
            raise ValueError(f"Unsupported task status: {status}")
        return normalized

    @staticmethod
    def _require_task(cursor: sqlite3.Cursor, run_id: str) -> None:
        if cursor.rowcount != 1:
            raise TaskStoreError(f"Unknown task: {run_id}")

    @staticmethod
    def _task_row(row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["metadata"] = _json_load(payload.pop("metadata_json", "{}"))
        payload["result"] = _json_load(payload.pop("result_json", "{}"))
        return payload

    @staticmethod
    def _event_row(row: sqlite3.Row) -> dict[str, Any]:
        payload = dict(row)
        payload["details"] = _json_load(payload.pop("details_json", "{}"))
        return payload


__all__ = [
    "DEFAULT_MAX_EVENTS",
    "DEFAULT_MAX_RUNTIME_LOGS",
    "DEFAULT_MAX_TASKS",
    "DEFAULT_RETENTION_DAYS",
    "TASK_STATUSES",
    "TERMINAL_TASK_STATUSES",
    "TaskStore",
    "TaskStoreError",
]
