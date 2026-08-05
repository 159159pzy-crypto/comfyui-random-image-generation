from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _decode(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _now() -> float:
    return time.time()


class DraftConflictError(RuntimeError):
    def __init__(self, current: Mapping[str, Any]) -> None:
        super().__init__("workspace draft revision is stale")
        self.current = dict(current)


class V7Store:
    """Native persistent state used by both V7 workspaces.

    The store owns one SQLite connection guarded by an RLock. Public methods are
    deliberately synchronous; aiohttp handlers move calls to a worker thread.
    """

    SCHEMA_VERSION = 7
    WORKSPACES = frozenset({"random", "natural"})

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        with self._lock:
            self._initialize()

    def _initialize(self) -> None:
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS v7_schema (
                version INTEGER PRIMARY KEY,
                applied_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS drafts (
                workspace TEXT PRIMARY KEY CHECK(workspace IN ('random', 'natural')),
                revision INTEGER NOT NULL DEFAULT 0,
                digest TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS presets (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                favorite INTEGER NOT NULL DEFAULT 0,
                revision INTEGER NOT NULL DEFAULT 1,
                digest TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS intents (
                id TEXT PRIMARY KEY,
                workspace TEXT NOT NULL CHECK(workspace IN ('random', 'natural')),
                digest TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_v7_intents_workspace
                ON intents(workspace, created_at DESC);
            CREATE TABLE IF NOT EXISTS studio_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                source_workspace TEXT NOT NULL DEFAULT 'studio',
                entity_id TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_v7_events_created
                ON studio_events(created_at DESC);
            CREATE TABLE IF NOT EXISTS deprecation_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                method TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                source_workspace TEXT NOT NULL DEFAULT '',
                called_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_v7_deprecation_endpoint
                ON deprecation_calls(endpoint, called_at DESC);
            """
        )
        self.connection.execute(
            "INSERT OR IGNORE INTO v7_schema(version, applied_at) VALUES (?, ?)",
            (self.SCHEMA_VERSION, _now()),
        )
        for workspace in sorted(self.WORKSPACES):
            payload = {"workspace": workspace, "intent": {"workspace": workspace}}
            self.connection.execute(
                """
                INSERT OR IGNORE INTO drafts
                    (workspace, revision, digest, payload_json, updated_at)
                VALUES (?, 0, ?, ?, ?)
                """,
                (workspace, _digest(payload), _canonical_json(payload), _now()),
            )
        self.connection.commit()

    @classmethod
    def _workspace(cls, value: Any, *, allow_studio: bool = False) -> str:
        workspace = str(value or "").strip().casefold()
        allowed = cls.WORKSPACES | ({"studio"} if allow_studio else set())
        if workspace not in allowed:
            raise ValueError("workspace must be random or natural")
        return workspace

    def get_draft(self, workspace: str) -> dict[str, Any]:
        workspace = self._workspace(workspace)
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM drafts WHERE workspace = ?", (workspace,)
            ).fetchone()
        if row is None:
            raise KeyError(workspace)
        return self._draft_row(row)

    @staticmethod
    def _draft_row(row: sqlite3.Row) -> dict[str, Any]:
        payload = _decode(row["payload_json"], {})
        return {
            **(payload if isinstance(payload, dict) else {}),
            "workspace": row["workspace"],
            "revision": int(row["revision"]),
            "digest": row["digest"],
            "updated_at": float(row["updated_at"]),
        }

    def save_draft(
        self,
        workspace: str,
        payload: Mapping[str, Any],
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace)
        clean = dict(payload)
        clean.pop("revision", None)
        clean.pop("digest", None)
        clean.pop("updated_at", None)
        clean["workspace"] = workspace
        digest = _digest(clean)
        with self._lock:
            current_row = self.connection.execute(
                "SELECT * FROM drafts WHERE workspace = ?", (workspace,)
            ).fetchone()
            if current_row is None:
                raise KeyError(workspace)
            current = self._draft_row(current_row)
            if int(expected_revision) != current["revision"]:
                raise DraftConflictError(current)
            if digest == current["digest"]:
                return current
            revision = current["revision"] + 1
            timestamp = _now()
            cursor = self.connection.execute(
                """
                UPDATE drafts
                SET revision = ?, digest = ?, payload_json = ?, updated_at = ?
                WHERE workspace = ? AND revision = ?
                """,
                (
                    revision,
                    digest,
                    _canonical_json(clean),
                    timestamp,
                    workspace,
                    current["revision"],
                ),
            )
            if cursor.rowcount != 1:
                latest = self.connection.execute(
                    "SELECT * FROM drafts WHERE workspace = ?", (workspace,)
                ).fetchone()
                raise DraftConflictError(self._draft_row(latest))
            self.connection.commit()
        return self.get_draft(workspace)

    def list_presets(self) -> dict[str, Any]:
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT * FROM presets
                ORDER BY favorite DESC, updated_at DESC, name COLLATE NOCASE
                """
            ).fetchall()
        items = [self._preset_row(row) for row in rows]
        return {"items": items, "count": len(items)}

    @staticmethod
    def _preset_row(row: sqlite3.Row) -> dict[str, Any]:
        payload = _decode(row["payload_json"], {})
        return {
            **(payload if isinstance(payload, dict) else {}),
            "id": row["id"],
            "name": row["name"],
            "favorite": bool(row["favorite"]),
            "revision": int(row["revision"]),
            "digest": row["digest"],
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
        }

    def get_preset(self, preset_id: str) -> dict[str, Any]:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM presets WHERE id = ?", (preset_id,)
            ).fetchone()
        if row is None:
            raise KeyError(preset_id)
        return self._preset_row(row)

    def save_preset(
        self,
        payload: Mapping[str, Any],
        *,
        preset_id: str = "",
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        clean = dict(payload)
        clean.pop("revision", None)
        clean.pop("digest", None)
        clean.pop("created_at", None)
        clean.pop("updated_at", None)
        identifier = str(preset_id or clean.get("id") or f"preset_{uuid.uuid4().hex[:16]}").strip()
        name = str(clean.get("name") or "").strip()
        if not name or len(name) > 100:
            raise ValueError("preset name must contain 1-100 characters")
        favorite = bool(clean.get("favorite", False))
        clean.update({"id": identifier, "name": name, "favorite": favorite})
        digest = _digest(clean)
        timestamp = _now()
        with self._lock:
            current = self.connection.execute(
                "SELECT * FROM presets WHERE id = ?", (identifier,)
            ).fetchone()
            if current is None:
                revision = 1
                self.connection.execute(
                    """
                    INSERT INTO presets
                        (id, name, favorite, revision, digest, payload_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identifier,
                        name,
                        int(favorite),
                        revision,
                        digest,
                        _canonical_json(clean),
                        timestamp,
                        timestamp,
                    ),
                )
            else:
                existing = self._preset_row(current)
                if expected_revision is not None and int(expected_revision) != existing["revision"]:
                    raise DraftConflictError(existing)
                if digest == existing["digest"]:
                    return existing
                revision = existing["revision"] + 1
                self.connection.execute(
                    """
                    UPDATE presets
                    SET name = ?, favorite = ?, revision = ?, digest = ?, payload_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        name,
                        int(favorite),
                        revision,
                        digest,
                        _canonical_json(clean),
                        timestamp,
                        identifier,
                    ),
                )
            self.connection.commit()
        return self.get_preset(identifier)

    def delete_preset(self, preset_id: str) -> bool:
        with self._lock:
            cursor = self.connection.execute(
                "DELETE FROM presets WHERE id = ?", (preset_id,)
            )
            self.connection.commit()
            return cursor.rowcount > 0

    def import_presets(self, items: list[Mapping[str, Any]]) -> int:
        imported = 0
        for item in items:
            identifier = str(item.get("id") or "").strip()
            if not identifier:
                continue
            try:
                self.get_preset(identifier)
            except KeyError:
                self.save_preset(item, preset_id=identifier)
                imported += 1
        return imported

    def create_intent(self, payload: Mapping[str, Any], *, workspace: str) -> dict[str, Any]:
        workspace = self._workspace(workspace)
        clean = dict(payload)
        clean["workspace"] = workspace
        identifier = str(
            clean.get("id") or clean.get("intent_id") or f"intent_{uuid.uuid4().hex}"
        ).strip()
        clean["id"] = identifier
        clean["intent_id"] = identifier
        digest = _digest(clean)
        timestamp = _now()
        with self._lock:
            self.connection.execute(
                """
                INSERT INTO intents
                    (id, workspace, digest, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (identifier, workspace, digest, _canonical_json(clean), timestamp, timestamp),
            )
            self.connection.commit()
        return self.get_intent(identifier)

    def get_intent(self, intent_id: str) -> dict[str, Any]:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM intents WHERE id = ?", (intent_id,)
            ).fetchone()
        if row is None:
            raise KeyError(intent_id)
        payload = _decode(row["payload_json"], {})
        return {
            **(payload if isinstance(payload, dict) else {}),
            "id": row["id"],
            "workspace": row["workspace"],
            "digest": row["digest"],
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
        }

    def list_intents(self, *, workspace: str = "", limit: int = 50) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if workspace:
            where = "WHERE workspace = ?"
            params.append(self._workspace(workspace))
        params.append(min(200, max(1, int(limit))))
        with self._lock:
            rows = self.connection.execute(
                f"SELECT id FROM intents {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self.get_intent(str(row["id"])) for row in rows]

    def append_event(
        self,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        source_workspace: str = "studio",
        entity_id: str = "",
    ) -> dict[str, Any]:
        source_workspace = self._workspace(source_workspace, allow_studio=True)
        event_type = str(event_type or "").strip()
        if not event_type:
            raise ValueError("event_type is required")
        timestamp = _now()
        with self._lock:
            cursor = self.connection.execute(
                """
                INSERT INTO studio_events
                    (event_type, source_workspace, entity_id, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event_type[:120],
                    source_workspace,
                    str(entity_id or "")[:200],
                    _canonical_json(dict(payload or {})),
                    timestamp,
                ),
            )
            self.connection.commit()
            seq = int(cursor.lastrowid)
        return {
            "id": seq,
            "event": event_type[:120],
            "workspace": source_workspace,
            "entity_id": str(entity_id or "")[:200],
            "data": dict(payload or {}),
            "created_at": timestamp,
        }

    def read_events(self, *, after_id: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT * FROM studio_events WHERE seq > ? ORDER BY seq ASC LIMIT ?
                """,
                (max(0, int(after_id)), min(2000, max(1, int(limit)))),
            ).fetchall()
        return [
            {
                "id": int(row["seq"]),
                "event": row["event_type"],
                "workspace": row["source_workspace"],
                "entity_id": row["entity_id"],
                "data": _decode(row["payload_json"], {}),
                "created_at": float(row["created_at"]),
            }
            for row in rows
        ]

    def latest_event_id(self) -> int:
        with self._lock:
            row = self.connection.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM studio_events"
            ).fetchone()
        return int(row[0] if row is not None else 0)

    def record_deprecation(self, method: str, endpoint: str, source_workspace: str = "") -> None:
        workspace = str(source_workspace or "").strip().casefold()
        if workspace and workspace not in self.WORKSPACES:
            workspace = ""
        with self._lock:
            self.connection.execute(
                """
                INSERT INTO deprecation_calls(method, endpoint, source_workspace, called_at)
                VALUES (?, ?, ?, ?)
                """,
                (str(method).upper()[:16], str(endpoint)[:300], workspace, _now()),
            )
            self.connection.commit()

    def deprecation_summary(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.connection.execute(
                """
                SELECT method, endpoint, source_workspace, COUNT(*) AS calls,
                       MAX(called_at) AS last_called_at
                FROM deprecation_calls
                GROUP BY method, endpoint, source_workspace
                ORDER BY last_called_at DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self.connection.close()
