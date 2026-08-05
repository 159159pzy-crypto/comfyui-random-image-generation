"""Bounded, redacted in-memory log console for this plugin only."""

from __future__ import annotations

import logging
import os
import re
import threading
import traceback
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

DEFAULT_LOG_CAPACITY = 1000
MAX_LOG_MESSAGE_CHARS = 12000

_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?ix)"
    r"(?P<prefix>(?:[\"']?)(?:"
    r"api[_-]?(?:key|token)|civitai[_-]?token|access[_-]?token|"
    r"refresh[_-]?token|csrf[_-]?token|authorization|password|passwd|"
    r"secret|cookie|session[_-]?(?:id|token)|token|key"
    r")(?:[\"']?)\s*[:=]\s*)"
    r"(?:"
    r"(?P<quote>[\"'])(?P<quoted>.*?)(?P=quote)"
    r"|(?P<bare>[^\s,;\"'}\]]+)"
    r")"
)
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:api[_-]?key|key|token|secret|password)=)([^&#\s]+)"
)
_BEARER_RE = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_URL_CREDENTIAL_RE = re.compile(
    r"(?i)\b(https?://)([^\s/@:]+):([^\s/@]+)@"
)


def redact_log_text(value: str) -> str:
    """Remove common credentials before a log line enters the Web UI buffer."""
    text = str(value or "")
    text = _BEARER_RE.sub(lambda match: f"{match.group(1)} ***", text)
    text = _SECRET_ASSIGNMENT_RE.sub(
        lambda match: (
            f"{match.group('prefix')}"
            + (
                f"{match.group('quote')}***{match.group('quote')}"
                if match.group("quote")
                else "***"
            )
        ),
        text,
    )
    text = _QUERY_SECRET_RE.sub(r"\1***", text)
    text = _OPENAI_KEY_RE.sub("sk-***", text)
    text = _URL_CREDENTIAL_RE.sub(r"\1***:***@", text)
    return text


class PluginLogConsole(logging.Handler):
    """Capture only this plugin's records in a thread-safe bounded buffer."""

    _handler_marker = "_comfy_anima_log_console_handler"

    def __init__(
        self,
        plugin_dir: Path,
        *,
        capacity: int = DEFAULT_LOG_CAPACITY,
        persistent_store: Any = None,
    ) -> None:
        super().__init__(level=logging.DEBUG)
        self._plugin_dir = os.path.normcase(
            os.path.abspath(str(Path(plugin_dir).resolve()))
        )
        self._capacity = max(100, min(5000, int(capacity)))
        self._entries: deque[dict[str, Any]] = deque(maxlen=self._capacity)
        self._persistent_store = persistent_store
        self._buffer_lock = threading.RLock()
        self._next_id = 1
        self._evicted = 0
        self._cleared_entries = 0
        self._clear_generation = 0
        self._attached_logger: Any = None
        self._stream_id = uuid.uuid4().hex
        self._persistent_cursor = 0
        self._started_at = datetime.now().astimezone().isoformat(
            timespec="milliseconds"
        )
        self._restore_persisted_entries()
        setattr(self, self._handler_marker, True)

    def _restore_persisted_entries(self) -> None:
        reader = getattr(self._persistent_store, "recent_runtime_logs", None)
        if not callable(reader):
            return
        try:
            rows = reader(limit=self._capacity)
        except Exception:
            return
        for row in rows if isinstance(rows, list) else ():
            if not isinstance(row, dict):
                continue
            try:
                entry_id = max(1, int(row.get("seq") or row.get("id") or 0))
                timestamp = float(row.get("timestamp") or 0.0)
            except (TypeError, ValueError):
                continue
            self._entries.append(self._entry_from_persisted_row(row, entry_id, timestamp))
            self._persistent_cursor = max(self._persistent_cursor, entry_id)
            self._next_id = max(self._next_id, entry_id + 1)

    def _sync_persisted_entries(self) -> None:
        """Pull task-event mirrors written directly through ``TaskStore``."""
        reader = getattr(self._persistent_store, "read_runtime_logs", None)
        if not callable(reader):
            return
        try:
            result = reader(after_seq=self._persistent_cursor, limit=self._capacity)
        except Exception:
            return
        rows = result.get("entries", []) if isinstance(result, dict) else []
        with self._buffer_lock:
            known = {int(entry["id"]) for entry in self._entries}
            for row in rows if isinstance(rows, list) else ():
                if not isinstance(row, dict):
                    continue
                try:
                    entry_id = max(1, int(row.get("seq") or row.get("id") or 0))
                    timestamp = float(row.get("timestamp") or 0.0)
                except (TypeError, ValueError):
                    continue
                self._persistent_cursor = max(self._persistent_cursor, entry_id)
                self._next_id = max(self._next_id, entry_id + 1)
                if entry_id in known:
                    continue
                if len(self._entries) >= self._capacity:
                    self._evicted += 1
                self._entries.append(
                    self._entry_from_persisted_row(row, entry_id, timestamp)
                )
                known.add(entry_id)

    def _entry_from_persisted_row(
        self,
        row: dict[str, Any],
        entry_id: int,
        timestamp: float,
    ) -> dict[str, Any]:
        message = str(row.get("message") or "")
        return {
            "id": entry_id,
            "timestamp": timestamp,
            "time": datetime.fromtimestamp(timestamp)
            .astimezone()
            .isoformat(timespec="milliseconds"),
            "level": self._normalize_level(str(row.get("level") or "INFO")),
            "category": str(row.get("category") or "plugin"),
            "source": str(row.get("source") or "plugin"),
            "line": max(0, int(row.get("line") or 0)),
            "message": message,
            "truncated": len(message) > MAX_LOG_MESSAGE_CHARS,
        }

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def attached(self) -> bool:
        return self._attached_logger is not None

    def attach(self, log: Any) -> bool:
        """Attach to AstrBot's standard logger when that API is available."""
        add_handler = getattr(log, "addHandler", None)
        remove_handler = getattr(log, "removeHandler", None)
        if not callable(add_handler):
            return False

        handlers: Iterable[Any] = tuple(getattr(log, "handlers", ()) or ())
        for existing in handlers:
            if existing is self or not getattr(
                existing,
                self._handler_marker,
                False,
            ):
                continue
            if callable(remove_handler):
                remove_handler(existing)
            try:
                existing.close()
            except Exception:
                pass

        add_handler(self)
        self._attached_logger = log
        return True

    def detach(self) -> None:
        log = self._attached_logger
        self._attached_logger = None
        remove_handler = getattr(log, "removeHandler", None)
        if callable(remove_handler):
            try:
                remove_handler(self)
            except Exception:
                pass

    def close(self) -> None:
        self.detach()
        super().close()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if not self._belongs_to_plugin(record):
                return
            message = record.getMessage()
            if record.exc_info:
                message = "\n".join(
                    (
                        message,
                        "".join(traceback.format_exception(*record.exc_info)).rstrip(),
                    )
                )
            message = redact_log_text(message)
            truncated = len(message) > MAX_LOG_MESSAGE_CHARS
            if truncated:
                message = (
                    message[:MAX_LOG_MESSAGE_CHARS]
                    + "\n… [日志内容过长，已在 WebUI 中截断]"
                )
            entry = {
                "id": 0,
                "timestamp": float(record.created),
                "time": datetime.fromtimestamp(record.created)
                .astimezone()
                .isoformat(timespec="milliseconds"),
                "level": self._normalize_level(record.levelname),
                "category": self._category(record, message),
                "source": self._source(record),
                "line": max(0, int(record.lineno or 0)),
                "message": message,
                "truncated": truncated,
            }
            persistent_id = 0
            writer = getattr(self._persistent_store, "append_runtime_log", None)
            if callable(writer):
                try:
                    persistent_id = int(
                        writer(
                            entry["level"],
                            entry["category"],
                            entry["source"],
                            entry["line"],
                            entry["message"],
                            timestamp=entry["timestamp"],
                        )
                    )
                except Exception:
                    persistent_id = 0
            with self._buffer_lock:
                if persistent_id > 0:
                    entry["id"] = persistent_id
                    self._persistent_cursor = max(
                        self._persistent_cursor,
                        persistent_id,
                    )
                    self._next_id = max(self._next_id, persistent_id + 1)
                else:
                    entry["id"] = self._next_id
                    self._next_id += 1
                if len(self._entries) >= self._capacity:
                    self._evicted += 1
                self._entries.append(entry)
        except Exception:
            # A diagnostics feature must never break the caller's logging path.
            return

    def snapshot(self, *, after_id: int = 0, limit: int = 500) -> dict[str, Any]:
        """Return new records and aggregate buffer status without exposing secrets."""
        self._sync_persisted_entries()
        safe_after = max(0, int(after_id))
        safe_limit = max(1, min(self._capacity, int(limit)))
        with self._buffer_lock:
            entries = list(self._entries)
            newest_id = self._next_id - 1
            oldest_id = entries[0]["id"] if entries else newest_id
            stream_reset = safe_after > newest_id and newest_id >= 0
            effective_after = 0 if stream_reset else safe_after
            pending = [entry.copy() for entry in entries if entry["id"] > effective_after]
            limit_truncated = len(pending) > safe_limit
            if limit_truncated:
                pending = pending[-safe_limit:]
            gap = bool(entries and effective_after > 0 and effective_after < oldest_id - 1)
            missed = max(0, oldest_id - effective_after - 1) if gap else 0
            counts = {
                "DEBUG": 0,
                "INFO": 0,
                "WARNING": 0,
                "ERROR": 0,
                "CRITICAL": 0,
            }
            categories: dict[str, int] = {}
            for entry in entries:
                level = str(entry["level"])
                counts[level] = counts.get(level, 0) + 1
                category = str(entry["category"])
                categories[category] = categories.get(category, 0) + 1
            return {
                "entries": pending,
                "cursor": newest_id,
                "oldest_id": oldest_id,
                "buffer_size": len(entries),
                "capacity": self._capacity,
                "evicted": self._evicted,
                "cleared": self._clear_generation,
                "clear_generation": self._clear_generation,
                "cleared_entries": self._cleared_entries,
                "truncated": limit_truncated or gap,
                "gap": gap,
                "missed": missed,
                "stream_reset": stream_reset,
                "stream_id": self._stream_id,
                "attached": self.attached,
                "started_at": self._started_at,
                "counts": counts,
                "categories": categories,
            }

    def clear_buffer(self) -> dict[str, Any]:
        """Clear this plugin-owned persisted view; AstrBot logs remain untouched."""
        with self._buffer_lock:
            removed = len(self._entries)
            self._entries.clear()
            self._cleared_entries += removed
            self._clear_generation += 1
            persistent_removed = 0
            clearer = getattr(self._persistent_store, "clear_runtime_logs", None)
            if callable(clearer):
                try:
                    persistent_removed = int(clearer())
                except Exception:
                    persistent_removed = 0
            return {
                "removed": max(removed, persistent_removed),
                "cursor": self._next_id - 1,
                "buffer_size": 0,
                "capacity": self._capacity,
                "clear_generation": self._clear_generation,
                "cleared_entries": self._cleared_entries,
                "stream_id": self._stream_id,
                "message": "插件持久控制台视图已清空；AstrBot 文件日志未被删除",
            }

    def _belongs_to_plugin(self, record: logging.LogRecord) -> bool:
        pathname = str(getattr(record, "pathname", "") or "")
        if pathname:
            normalized = os.path.normcase(os.path.abspath(pathname))
            try:
                if os.path.commonpath((self._plugin_dir, normalized)) == self._plugin_dir:
                    return True
            except ValueError:
                pass
        return False

    def _source(self, record: logging.LogRecord) -> str:
        pathname = str(getattr(record, "pathname", "") or "")
        if pathname:
            normalized = os.path.normcase(os.path.abspath(pathname))
            try:
                if os.path.commonpath((self._plugin_dir, normalized)) == self._plugin_dir:
                    return os.path.relpath(normalized, self._plugin_dir).replace("\\", "/")
            except ValueError:
                pass
        return str(getattr(record, "name", "plugin") or "plugin")

    @staticmethod
    def _normalize_level(value: str) -> str:
        level = str(value or "INFO").upper()
        if level == "WARN":
            return "WARNING"
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            return "INFO"
        return level

    @staticmethod
    def _category(record: logging.LogRecord, message: str) -> str:
        haystack = " ".join(
            (
                str(getattr(record, "pathname", "") or ""),
                str(getattr(record, "funcName", "") or ""),
                message,
            )
        ).casefold()
        if "web_ui" in haystack or "web ui" in haystack or "webui" in haystack:
            return "web"
        if "lora" in haystack:
            return "lora"
        if any(token in haystack for token in ("director", "provider", "llm", "分镜")):
            return "llm"
        if any(
            token in haystack
            for token in (
                "comfy",
                "workflow",
                "generation",
                "generate",
                "绘图",
                "出图",
                "生成",
            )
        ):
            return "generation"
        return "plugin"


__all__ = [
    "DEFAULT_LOG_CAPACITY",
    "PluginLogConsole",
    "redact_log_text",
]
