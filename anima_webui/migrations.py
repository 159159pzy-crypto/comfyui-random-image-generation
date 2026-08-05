from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Protocol

from .v7_store import V7Store


class TaskStoreLike(Protocol):
    def get_task(self, run_id: str) -> Mapping[str, Any] | None: ...

    def create_task(self, task_type: str, **kwargs: Any) -> Any: ...

    def append_event(self, run_id: str, phase: str, message: str, **kwargs: Any) -> Any: ...

    def finish_task(self, run_id: str, status: str, **kwargs: Any) -> Any: ...


V5_DATA_FILES = (
    "history.sqlite3",
    "custom_prompts.json",
    "style_presets.json",
    "natural/settings.json",
    "natural/providers.json",
    "natural/provider_secrets.json",
    "natural/lora_profiles_v3.json",
    "natural/identity_bindings_v3.json",
    "natural/prompt_lab.json",
    "natural/lora_semantic_v3.json",
    "natural/task_events.jsonl",
)

V6_DATA_FILES = (
    "history.sqlite3",
    "studio.sqlite3",
    "custom_prompts.json",
    "style_presets.json",
    "natural/settings.json",
    "natural/providers.json",
    "natural/provider_secrets.json",
    "natural/lora_profiles_v3.json",
    "natural/identity_bindings_v3.json",
    "natural/prompt_lab.json",
    "natural/lora_semantic_v3.json",
)
V7_MIGRATION_REVISION = 2


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _sqlite_snapshot(source: Path, destination: Path) -> None:
    """Create a transactionally consistent SQLite backup, including live WAL pages."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(fd)
    source_connection: sqlite3.Connection | None = None
    destination_connection: sqlite3.Connection | None = None
    try:
        source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        destination_connection = sqlite3.connect(temporary)
        source_connection.backup(destination_connection)
        result = destination_connection.execute("PRAGMA quick_check").fetchone()
        if result is None or result[0] != "ok":
            raise RuntimeError(f"V7 SQLite backup verification failed: {source.name}")
        destination_connection.close()
        destination_connection = None
        source_connection.close()
        source_connection = None
        os.replace(temporary, destination)
    finally:
        if destination_connection is not None:
            destination_connection.close()
        if source_connection is not None:
            source_connection.close()
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare_v6_backup(root: str | Path) -> dict[str, Any]:
    """Create one verified, non-destructive backup before V6 writes new state."""

    root = Path(root)
    data_dir = root / "data"
    marker = data_dir / "migrations" / "v6.json"
    if marker.is_file():
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
            if isinstance(value, dict):
                return value
        except (OSError, ValueError):
            pass

    backup_root = data_dir / "backups" / "v5-pre-v6"
    files: list[dict[str, Any]] = []
    for relative in V5_DATA_FILES:
        source = data_dir / relative
        if not source.is_file():
            continue
        destination = backup_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        source_hash = _sha256(source)
        if destination.exists():
            if not destination.is_file() or _sha256(destination) != source_hash:
                raise RuntimeError(f"V6 备份目标与源文件不一致: {relative}")
        else:
            shutil.copy2(source, destination)
            if _sha256(destination) != source_hash:
                raise RuntimeError(f"V6 备份校验失败: {relative}")
        files.append(
            {
                "path": relative.replace("\\", "/"),
                "sha256": source_hash,
                "size": source.stat().st_size,
            }
        )

    report = {
        "schema_version": 6,
        "created_at": time.time(),
        "backup": str(backup_root.relative_to(root)).replace("\\", "/"),
        "files": files,
        "legacy_events_imported": 0,
    }
    _atomic_json(marker, report)
    return report


def import_legacy_task_events(root: str | Path, store: TaskStoreLike) -> int:
    """Import V5 JSONL timelines once, retaining only redacted operational data."""

    root = Path(root)
    marker = root / "data" / "migrations" / "v6.json"
    report = prepare_v6_backup(root)
    if report.get("legacy_events_imported"):
        return int(report["legacy_events_imported"])
    path = root / "data" / "natural" / "task_events.jsonl"
    if not path.is_file():
        return 0

    grouped: dict[str, list[dict[str, Any]]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not isinstance(item, Mapping):
            continue
        job_id = str(item.get("job_id") or "").strip()
        if job_id:
            grouped.setdefault(job_id, []).append(dict(item))

    imported = 0
    terminal = {"completed": "succeeded", "failed": "failed", "cancelled": "cancelled"}
    for job_id, events in grouped.items():
        if store.get_task(job_id) is not None:
            continue
        store.create_task(
            "natural_generation",
            run_id=job_id,
            mode="natural",
            metadata={"workspace": "natural", "legacy": True},
        )
        for event in events[-200:]:
            stage = str(event.get("stage") or "legacy")[:100]
            store.append_event(
                job_id,
                stage,
                str(event.get("message") or stage)[:4000],
                event_code=f"legacy_{stage}"[:100],
                details=event.get("details") if isinstance(event.get("details"), Mapping) else {},
                timestamp=float(event.get("timestamp") or time.time()),
            )
        final_stage = str(events[-1].get("stage") or "")
        store.finish_task(
            job_id,
            terminal.get(final_stage, "interrupted"),
            error_code="legacy_import" if final_stage not in terminal else "",
            error_summary="从 V5 任务事件迁移" if final_stage not in terminal else "",
        )
        imported += 1

    report = {**report, "legacy_events_imported": imported, "legacy_events_imported_at": time.time()}
    _atomic_json(marker, report)
    return imported


def prepare_v7_backup(root: str | Path) -> dict[str, Any]:
    """Copy the V6 data set before any V7 connection mutates its schema."""
    root = Path(root)
    data_dir = root / "data"
    marker = data_dir / "migrations" / "v7.json"
    if marker.is_file():
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
            if isinstance(value, dict) and int(value.get("schema_version") or 0) == 7:
                return value
        except (OSError, ValueError, TypeError):
            pass

    backup_root = data_dir / "backups" / "v6-pre-v7"
    files: list[dict[str, Any]] = []
    for relative in V6_DATA_FILES:
        source = data_dir / relative
        if not source.is_file():
            continue
        destination = backup_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        source_hash = _sha256(source)
        if source.suffix.casefold() in {".sqlite", ".sqlite3", ".db"}:
            if destination.exists():
                if not destination.is_file():
                    raise RuntimeError(f"V7 backup is not a file: {relative}")
                connection = sqlite3.connect(destination)
                try:
                    result = connection.execute("PRAGMA quick_check").fetchone()
                    if result is None or result[0] != "ok":
                        raise RuntimeError(f"V7 SQLite backup is invalid: {relative}")
                finally:
                    connection.close()
            else:
                _sqlite_snapshot(source, destination)
            backup_hash = _sha256(destination)
            backup_kind = "sqlite_backup"
        elif destination.exists():
            if not destination.is_file() or _sha256(destination) != source_hash:
                raise RuntimeError(f"V7 backup differs from source: {relative}")
            backup_hash = source_hash
            backup_kind = "file_copy"
        else:
            shutil.copy2(source, destination)
            if _sha256(destination) != source_hash:
                raise RuntimeError(f"V7 backup verification failed: {relative}")
            backup_hash = source_hash
            backup_kind = "file_copy"
        files.append(
            {
                "path": relative.replace("\\", "/"),
                "sha256": source_hash,
                "backup_sha256": backup_hash,
                "backup_kind": backup_kind,
                "size": source.stat().st_size,
            }
        )

    return {
        "schema_version": 7,
        "created_at": time.time(),
        "backup": str(backup_root.relative_to(root)).replace("\\", "/"),
        "files": files,
    }


def prepare_v7_migration(
    root: str | Path,
    store: V7Store,
    *,
    backup_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Import JSON presets exactly once after a verified pre-V7 backup."""

    root = Path(root)
    data_dir = root / "data"
    marker = data_dir / "migrations" / "v7.json"
    if marker.is_file():
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
            if (
                isinstance(value, dict)
                and int(value.get("schema_version") or 0) == 7
                and int(value.get("migration_revision") or 0) >= V7_MIGRATION_REVISION
            ):
                return value
        except (OSError, ValueError, TypeError):
            pass
    report = dict(backup_report or prepare_v7_backup(root))
    preset_source = data_dir / "style_presets.json"
    preset_items: list[Mapping[str, Any]] = []
    if preset_source.is_file():
        try:
            payload = json.loads(preset_source.read_text(encoding="utf-8"))
            values = payload.get("items") if isinstance(payload, Mapping) else []
            if isinstance(values, list):
                preset_items = [item for item in values if isinstance(item, Mapping)]
        except (OSError, ValueError):
            preset_items = []
    normalized_presets: list[Mapping[str, Any]] = []
    for item in preset_items:
        try:
            from anima_studio.domain import StylePreset

            normalized_presets.append(StylePreset.from_mapping(item).to_dict())
        except (ImportError, TypeError, ValueError):
            # Keep startup non-destructive when an old preset cannot be mapped;
            # the source JSON remains in the verified backup for manual repair.
            continue
    imported = store.import_presets(normalized_presets)
    report = {
        **report,
        "migration_revision": V7_MIGRATION_REVISION,
        "style_presets_seen": len(preset_items),
        "style_presets_valid": len(normalized_presets),
        "style_presets_imported": imported,
    }
    _atomic_json(marker, report)
    return report
