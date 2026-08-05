from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

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


class TaskStoreError(RuntimeError):
    pass


class TaskTransitionError(TaskStoreError):
    pass


_SECRET = re.compile(r"(?i)(bearer\s+)[a-z0-9._~-]+|\bsk-[a-z0-9_-]{8,}\b")
_SECRET_KEYS = {"api_key", "api_token", "token", "password", "secret", "authorization", "cookie"}
_CONTENT_KEYS = {"system_prompt", "user_prompt", "raw_prompt", "response", "raw_response", "completion"}


def _redact_text(value: Any, limit: int = 12_000) -> str:
    text = _SECRET.sub(lambda match: (match.group(1) or "") + "[redacted]", str(value or ""))
    return text if len(text) <= limit else text[:limit] + "\n[content truncated]"


def _sanitize(value: Any, key: str = "", depth: int = 0) -> Any:
    normalized = key.strip().casefold().replace("-", "_")
    if normalized in _SECRET_KEYS or any(token in normalized for token in ("password", "secret", "cookie")):
        return "***"
    if normalized in _CONTENT_KEYS:
        return {"omitted": True, "chars": len(str(value or ""))}
    if depth >= 8:
        return "[maximum nesting depth reached]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, (bytes, bytearray)):
        return {"omitted": True, "bytes": len(value)}
    if isinstance(value, Mapping):
        return {str(item_key): _sanitize(item, str(item_key), depth + 1) for item_key, item in value.items()}
    if isinstance(value, Sequence):
        return [_sanitize(item, depth=depth + 1) for item in list(value)[:500]]
    return _redact_text(value, 1000)


def _dump(value: Mapping[str, Any] | None) -> str:
    return json.dumps(_sanitize(dict(value or {})), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load(value: Any) -> dict[str, Any]:
    try:
        result = json.loads(str(value or "{}"))
    except ValueError:
        return {}
    return result if isinstance(result, dict) else {}


class TaskStore:
    """Thread-safe native operational store compatible with existing V6 tables."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        retention_days: int = 30,
        max_tasks: int = 2_000,
        max_events: int = 50_000,
        max_runtime_logs: int = 20_000,
        cleanup_interval: int = 100,
    ) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.retention_days = max(0, int(retention_days))
        self.max_tasks = max(1, int(max_tasks))
        self.max_events = max(1, int(max_events))
        self.max_runtime_logs = max(1, int(max_runtime_logs))
        self.cleanup_interval = max(1, int(cleanup_interval))
        self._writes = 0
        self._lock = threading.RLock()
        try:
            self._connection = sqlite3.connect(
                self.database_path,
                timeout=10,
                isolation_level=None,
                check_same_thread=False,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA busy_timeout=10000")
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._create_schema()
        except sqlite3.Error as exc:
            raise TaskStoreError(f"unable to open task store: {exc}") from exc

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS task_runs (
                run_id TEXT PRIMARY KEY, task_type TEXT NOT NULL, mode TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL, requested_by TEXT NOT NULL DEFAULT '',
                total_items INTEGER NOT NULL DEFAULT 0, completed_items INTEGER NOT NULL DEFAULT 0,
                failed_items INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
                started_at REAL, heartbeat_at REAL, ended_at REAL,
                error_code TEXT NOT NULL DEFAULT '', error_summary TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}', result_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_task_runs_recent ON task_runs(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_task_runs_status ON task_runs(status, created_at DESC);
            CREATE TABLE IF NOT EXISTS task_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                timestamp REAL NOT NULL, level TEXT NOT NULL, phase TEXT NOT NULL DEFAULT '',
                item_name TEXT NOT NULL DEFAULT '', batch_index INTEGER, batch_total INTEGER,
                event_code TEXT NOT NULL DEFAULT '', message TEXT NOT NULL, duration_ms INTEGER,
                attempt INTEGER NOT NULL DEFAULT 1, details_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(run_id) REFERENCES task_runs(run_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_task_events_run_seq ON task_events(run_id, seq);
            CREATE TABLE IF NOT EXISTS runtime_logs (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL,
                level TEXT NOT NULL, category TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
                line INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL, run_id TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_runtime_logs_time ON runtime_logs(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_runtime_logs_run_seq ON runtime_logs(run_id, seq);
            """
        )

    @property
    def journal_mode(self) -> str:
        with self._lock:
            row = self._execute("PRAGMA journal_mode").fetchone()
            return str(row[0] if row else "").casefold()

    def close(self) -> None:
        with self._lock:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
                self._connection = None

    def __enter__(self) -> TaskStore:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def create_task(
        self,
        task_type: str,
        *,
        mode: str = "",
        status: str = "queued",
        requested_by: str = "",
        total_items: int = 0,
        metadata: Mapping[str, Any] | None = None,
        run_id: str = "",
        timestamp: float | None = None,
    ) -> str:
        status = self._status(status)
        task_type = _redact_text(task_type, 100).strip()
        if not task_type:
            raise ValueError("task_type must not be empty")
        identifier = str(run_id or uuid.uuid4().hex).strip()
        now = self._time(timestamp)
        started = now if status == "running" else None
        with self._lock:
            try:
                self._execute(
                    """INSERT INTO task_runs
                    (run_id,task_type,mode,status,requested_by,total_items,created_at,started_at,heartbeat_at,metadata_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        identifier,
                        task_type,
                        _redact_text(mode, 100),
                        status,
                        _redact_text(requested_by, 200),
                        max(0, int(total_items)),
                        now,
                        started,
                        started,
                        _dump(metadata),
                    ),
                )
            except TaskStoreError as exc:
                if "UNIQUE" in str(exc):
                    raise TaskStoreError(f"Task already exists: {identifier}") from exc
                raise
            self._append_event(identifier, "lifecycle", "Task created", event_code="task_created", timestamp=now)
            self._after_write()
        return identifier

    def get_task(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._execute("SELECT * FROM task_runs WHERE run_id=?", (str(run_id),)).fetchone()
            return self._task(row) if row else None

    def start_task(self, run_id: str, *, total_items: int | None = None, timestamp: float | None = None) -> dict[str, Any]:
        now = self._time(timestamp)
        values: list[Any] = [now, now]
        total_sql = ""
        if total_items is not None:
            total_sql = ", total_items=?"
            values.append(max(0, int(total_items)))
        values.append(str(run_id))
        with self._lock:
            cursor = self._execute(
                f"UPDATE task_runs SET status='running',started_at=COALESCE(started_at,?),heartbeat_at=?,ended_at=NULL{total_sql} WHERE run_id=? AND status='queued'",
                values,
            )
            if cursor.rowcount != 1:
                self._raise_transition(run_id, "running", expected="queued")
            self._append_event(
                run_id,
                "lifecycle",
                "Task is leaving the queue",
                event_code="task_starting",
                timestamp=now,
            )
            self._append_event(run_id, "lifecycle", "Task started", event_code="task_started", timestamp=now)
            self._after_write()
            return self._required_task(run_id)

    def heartbeat(
        self,
        run_id: str,
        *,
        completed_items: int | None = None,
        failed_items: int | None = None,
        total_items: int | None = None,
        timestamp: float | None = None,
    ) -> dict[str, Any]:
        updates = ["heartbeat_at=?"]
        values: list[Any] = [self._time(timestamp)]
        for column, value in (("completed_items", completed_items), ("failed_items", failed_items), ("total_items", total_items)):
            if value is not None:
                updates.append(f"{column}=?")
                values.append(max(0, int(value)))
        values.append(str(run_id))
        with self._lock:
            cursor = self._execute(
                f"UPDATE task_runs SET {','.join(updates)} WHERE run_id=? AND status='running'",
                values,
            )
            if cursor.rowcount != 1:
                self._raise_transition(run_id, "running heartbeat", expected="running")
            self._after_write()
            return self._required_task(run_id)

    def finish_task(
        self,
        run_id: str,
        status: str,
        *,
        completed_items: int | None = None,
        failed_items: int | None = None,
        error_code: str = "",
        error_summary: str = "",
        result: Mapping[str, Any] | None = None,
        timestamp: float | None = None,
    ) -> dict[str, Any]:
        status = self._status(status)
        if status not in TERMINAL_TASK_STATUSES:
            raise ValueError("finish_task requires a terminal status")
        now = self._time(timestamp)
        updates = ["status=?", "heartbeat_at=?", "ended_at=?", "error_code=?", "error_summary=?", "result_json=?"]
        values: list[Any] = [status, now, now, _redact_text(error_code, 100), _redact_text(error_summary, 4000), _dump(result)]
        for column, value in (("completed_items", completed_items), ("failed_items", failed_items)):
            if value is not None:
                updates.append(f"{column}=?")
                values.append(max(0, int(value)))
        values.append(str(run_id))
        with self._lock:
            cursor = self._execute(
                f"UPDATE task_runs SET {','.join(updates)} WHERE run_id=? AND status IN ('queued','running')",
                values,
            )
            if cursor.rowcount != 1:
                current = self._required_task(run_id)
                if str(current.get("status") or "") in TERMINAL_TASK_STATUSES:
                    return current
                self._raise_transition(run_id, status, expected="queued or running")
            self._append_event(run_id, "lifecycle", f"Task finished: {status}", event_code="task_finished", timestamp=now)
            self._after_write()
            return self._required_task(run_id)

    def append_event(
        self,
        run_id: str,
        phase: str,
        message: str,
        *,
        level: str = "INFO",
        item_name: str = "",
        batch_index: int | None = None,
        batch_total: int | None = None,
        event_code: str = "",
        duration_ms: int | None = None,
        attempt: int = 1,
        details: Mapping[str, Any] | None = None,
        timestamp: float | None = None,
    ) -> int:
        with self._lock:
            sequence = self._append_event(
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
            self._after_write()
            return sequence

    def _append_event(self, run_id: str, phase: str, message: str, **values: Any) -> int:
        if self.get_task(run_id) is None:
            raise TaskStoreError(f"Unknown task: {run_id}")
        timestamp = self._time(values.get("timestamp"))
        level = self._level(values.get("level", "INFO"))
        cursor = self._execute(
            """INSERT INTO task_events
            (run_id,timestamp,level,phase,item_name,batch_index,batch_total,event_code,message,duration_ms,attempt,details_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(run_id), timestamp, level, _redact_text(phase, 100),
                _redact_text(values.get("item_name", ""), 500), values.get("batch_index"),
                values.get("batch_total"), _redact_text(values.get("event_code", ""), 100),
                _redact_text(message, 4000), values.get("duration_ms"), max(1, int(values.get("attempt", 1))),
                _dump(values.get("details")),
            ),
        )
        self._execute(
            "INSERT INTO runtime_logs(timestamp,level,category,source,line,message,run_id) VALUES(?,?,?,?,0,?,?)",
            (timestamp, level, self._category(self._required_task(run_id)["task_type"]), f"task/{self._required_task(run_id)['task_type']}", _redact_text(message), str(run_id)),
        )
        return int(cursor.lastrowid)

    def read_events(self, *, run_id: str = "", after_seq: int = 0, limit: int = 500) -> dict[str, Any]:
        conditions = ["seq>?"]
        values: list[Any] = [max(0, int(after_seq))]
        if run_id:
            conditions.append("run_id=?")
            values.append(str(run_id))
        values.append(min(2000, max(1, int(limit))))
        with self._lock:
            rows = self._execute(
                f"SELECT * FROM task_events WHERE {' AND '.join(conditions)} ORDER BY seq ASC LIMIT ?",
                values,
            ).fetchall()
        entries = [self._event(row) for row in rows]
        return {"entries": entries, "cursor": entries[-1]["seq"] if entries else max(0, int(after_seq))}

    def recent_tasks(self, *, limit: int = 50, statuses: Sequence[str] | None = None, task_type: str = "") -> list[dict[str, Any]]:
        conditions: list[str] = []
        values: list[Any] = []
        if statuses:
            normalized = [self._status(status) for status in statuses]
            conditions.append("status IN (" + ",".join("?" for _ in normalized) + ")")
            values.extend(normalized)
        if task_type:
            conditions.append("task_type=?")
            values.append(str(task_type))
        values.append(min(500, max(1, int(limit))))
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self._lock:
            rows = self._execute(f"SELECT * FROM task_runs{where} ORDER BY created_at DESC,run_id DESC LIMIT ?", values).fetchall()
            return [self._task(row) for row in rows]

    def interrupt_running_tasks(self, *, timestamp: float | None = None) -> int:
        now = self._time(timestamp)
        with self._lock:
            cursor = self._execute(
                "UPDATE task_runs SET status='interrupted',heartbeat_at=?,ended_at=?,error_code='studio_restarted' WHERE status='running'",
                (now, now),
            )
            return max(0, int(cursor.rowcount))

    def append_runtime_log(self, level: str, category: str, source: str, line: int, message: str, *, run_id: str = "", timestamp: float | None = None) -> int:
        with self._lock:
            cursor = self._execute(
                "INSERT INTO runtime_logs(timestamp,level,category,source,line,message,run_id) VALUES(?,?,?,?,?,?,?)",
                (self._time(timestamp), self._level(level), _redact_text(category, 100), _redact_text(source, 500), max(0, int(line)), _redact_text(message), _redact_text(run_id, 100)),
            )
            self._after_write()
            return int(cursor.lastrowid)

    def read_runtime_logs(self, *, after_seq: int = 0, limit: int = 500, levels: Sequence[str] | None = None, category: str = "", run_id: str = "") -> dict[str, Any]:
        conditions = ["seq>?"]
        values: list[Any] = [max(0, int(after_seq))]
        if levels:
            normalized = [self._level(level) for level in levels]
            conditions.append("level IN (" + ",".join("?" for _ in normalized) + ")")
            values.extend(normalized)
        for column, value in (("category", category), ("run_id", run_id)):
            if value:
                conditions.append(f"{column}=?")
                values.append(value)
        values.append(min(2000, max(1, int(limit))))
        with self._lock:
            rows = self._execute(f"SELECT * FROM runtime_logs WHERE {' AND '.join(conditions)} ORDER BY seq ASC LIMIT ?", values).fetchall()
        entries = [dict(row) for row in rows]
        return {"entries": entries, "cursor": entries[-1]["seq"] if entries else max(0, int(after_seq))}

    def recent_runtime_logs(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._execute("SELECT * FROM runtime_logs ORDER BY seq DESC LIMIT ?", (min(5000, max(1, int(limit))),)).fetchall()
            return [dict(row) for row in reversed(rows)]

    def clear_runtime_logs(self) -> int:
        with self._lock:
            return max(0, int(self._execute("DELETE FROM runtime_logs").rowcount))

    def cleanup(self, *, now: float | None = None) -> dict[str, int]:
        removed = {"tasks": 0, "events": 0, "runtime_logs": 0}
        with self._lock:
            if self.retention_days:
                cutoff = self._time(now) - self.retention_days * 86_400
                removed["tasks"] += max(0, int(self._execute("DELETE FROM task_runs WHERE status NOT IN ('queued','running') AND COALESCE(ended_at,created_at)<?", (cutoff,)).rowcount))
                removed["runtime_logs"] += max(0, int(self._execute("DELETE FROM runtime_logs WHERE timestamp<?", (cutoff,)).rowcount))
            removed["tasks"] += self._trim("task_runs", "run_id", self.max_tasks, "created_at DESC")
            removed["events"] += self._trim("task_events", "seq", self.max_events, "seq DESC")
            removed["runtime_logs"] += self._trim("runtime_logs", "seq", self.max_runtime_logs, "seq DESC")
            self._writes = 0
        return removed

    def _trim(self, table: str, key: str, maximum: int, order: str) -> int:
        count = int(self._execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        excess = max(0, count - maximum)
        if not excess:
            return 0
        cursor = self._execute(f"DELETE FROM {table} WHERE {key} IN (SELECT {key} FROM {table} ORDER BY {order} LIMIT ?)", (excess,))
        return max(0, int(cursor.rowcount))

    def _execute(self, sql: str, values: Sequence[Any] = ()) -> sqlite3.Cursor:
        connection = getattr(self, "_connection", None)
        if connection is None:
            raise TaskStoreError("Task store is closed")
        try:
            return connection.execute(sql, tuple(values))
        except sqlite3.Error as exc:
            raise TaskStoreError(f"Task store query failed: {exc}") from exc

    def _required_task(self, run_id: str) -> dict[str, Any]:
        task = self.get_task(run_id)
        if task is None:
            raise TaskStoreError(f"Unknown task: {run_id}")
        return task

    @staticmethod
    def _require(cursor: sqlite3.Cursor, run_id: str) -> None:
        if cursor.rowcount != 1:
            raise TaskStoreError(f"Unknown task: {run_id}")

    def _raise_transition(self, run_id: str, target: str, *, expected: str) -> None:
        current = self._required_task(run_id)
        raise TaskTransitionError(
            f"Task {run_id} cannot transition from {current['status']} to {target}; "
            f"expected {expected}"
        )

    def _after_write(self) -> None:
        self._writes += 1
        if self._writes >= self.cleanup_interval:
            self.cleanup()

    @staticmethod
    def _task(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["metadata"] = _load(result.pop("metadata_json", "{}"))
        result["result"] = _load(result.pop("result_json", "{}"))
        return result

    @staticmethod
    def _event(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["details"] = _load(result.pop("details_json", "{}"))
        return result

    @staticmethod
    def _status(value: str) -> str:
        status = str(value or "").strip().casefold()
        if status not in TASK_STATUSES:
            raise ValueError(f"Unsupported task status: {value}")
        return status

    @staticmethod
    def _level(value: Any) -> str:
        level = str(value or "INFO").strip().upper()
        if level == "WARN":
            level = "WARNING"
        return level if level in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"} else "INFO"

    @staticmethod
    def _time(value: float | None) -> float:
        return time.time() if value is None else float(value)

    @staticmethod
    def _category(task_type: str) -> str:
        value = task_type.casefold()
        if "lora" in value:
            return "lora"
        if any(token in value for token in ("generation", "image", "comfy")):
            return "generation"
        if any(token in value for token in ("llm", "provider")):
            return "llm"
        return "studio"
