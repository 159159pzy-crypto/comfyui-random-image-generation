from __future__ import annotations

import asyncio
import functools
import json
import logging
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from .persistence import backup_corrupt_file


logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class HistoryStore:
    """sqlite 持久化。

    所有数据库操作都在一个专用单线程 executor 上执行:
    - 单线程天然串行化,连接不会被并发使用;
    - commit/fsync 的耗时不再冻结事件循环(批次中每张图都要落库)。
    构造函数在调用方线程完成建表迁移,此后连接只被 executor 线程使用,
    因此 check_same_thread=False 是安全的。
    """

    def __init__(self, path: str | Path):
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.load_warnings: list[str] = []
        try:
            self.connection = self._open(destination)
        except sqlite3.DatabaseError as error:
            # 坏数据库不再阻断启动:改名备份后重建,并向界面报告警告。
            backup = backup_corrupt_file(destination)
            logger.warning("历史数据库无法读取(%s),已备份为 %s 并重建", error, backup.name)
            self.load_warnings.append(f"历史数据库无法读取,已备份为 {backup.name} 并重建")
            self.connection = self._open(destination)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="anima-history")

    @staticmethod
    def _open(destination: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(destination, check_same_thread=False)
        try:
            HistoryStore._initialize(connection)
        except sqlite3.DatabaseError:
            connection.close()
            raise
        return connection

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS batches (
                id TEXT PRIMARY KEY,
                total INTEGER NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '',
                settings_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL,
                prompt_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                subfolder TEXT NOT NULL DEFAULT '',
                file_type TEXT NOT NULL DEFAULT 'output',
                positive_prompt TEXT NOT NULL DEFAULT '',
                negative_prompt TEXT NOT NULL DEFAULT '',
                sample_seed INTEGER NOT NULL,
                prompt_seed INTEGER NOT NULL,
                settings_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_images_created ON images(id DESC);
            CREATE INDEX IF NOT EXISTS idx_images_batch ON images(batch_id, sequence);
            """
        )
        image_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(images)").fetchall()
        }
        if "resolved_selection_json" not in image_columns:
            connection.execute(
                "ALTER TABLE images ADD COLUMN resolved_selection_json TEXT NOT NULL DEFAULT '{}'"
            )
        if "resolved_prompt" not in image_columns:
            connection.execute(
                "ALTER TABLE images ADD COLUMN resolved_prompt TEXT NOT NULL DEFAULT ''"
            )
        connection.execute(
            "UPDATE batches SET status = 'interrupted', error = 'WebUI 重启，未完成批次未自动恢复', updated_at = ? WHERE status IN ('running', 'stopping')",
            (_now(),),
        )
        connection.commit()

    async def _call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, functools.partial(func, *args, **kwargs))

    async def create_batch(self, batch_id: str, total: int, settings: dict[str, Any]) -> dict[str, Any]:
        return await self._call(self._create_batch, batch_id, total, settings)

    def _create_batch(self, batch_id: str, total: int, settings: dict[str, Any]) -> dict[str, Any]:
        timestamp = _now()
        self.connection.execute(
            "INSERT INTO batches (id, total, completed, status, error, settings_json, created_at, updated_at) VALUES (?, ?, 0, 'running', '', ?, ?, ?)",
            (batch_id, total, json.dumps(settings, ensure_ascii=False), timestamp, timestamp),
        )
        self.connection.commit()
        return self._get_batch(batch_id)

    async def update_batch(
        self,
        batch_id: str,
        *,
        completed: int | None = None,
        status: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        return await self._call(
            self._update_batch, batch_id, completed=completed, status=status, error=error
        )

    def _update_batch(
        self,
        batch_id: str,
        *,
        completed: int | None = None,
        status: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        fields = ["updated_at = ?"]
        values: list[Any] = [_now()]
        if completed is not None:
            fields.append("completed = ?")
            values.append(completed)
        if status is not None:
            fields.append("status = ?")
            values.append(status)
        if error is not None:
            fields.append("error = ?")
            values.append(error)
        values.append(batch_id)
        self.connection.execute(f"UPDATE batches SET {', '.join(fields)} WHERE id = ?", values)
        self.connection.commit()
        return self._get_batch(batch_id)

    async def add_image(self, **kwargs: Any) -> dict[str, Any]:
        return await self._call(self._add_image, **kwargs)

    def _add_image(
        self,
        *,
        batch_id: str,
        sequence: int,
        prompt_id: str,
        image: dict[str, Any],
        positive_prompt: str,
        negative_prompt: str,
        sample_seed: int,
        prompt_seed: int,
        settings: dict[str, Any],
        resolved_selection: dict[str, Any] | None = None,
        resolved_prompt: str = "",
    ) -> dict[str, Any]:
        cursor = self.connection.execute(
            """
            INSERT INTO images (
                batch_id, sequence, prompt_id, filename, subfolder, file_type,
                positive_prompt, negative_prompt, sample_seed, prompt_seed,
                settings_json, created_at, resolved_selection_json, resolved_prompt
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                sequence,
                prompt_id,
                str(image["filename"]),
                str(image.get("subfolder") or ""),
                str(image.get("type") or "output"),
                positive_prompt,
                negative_prompt,
                sample_seed,
                prompt_seed,
                json.dumps(settings, ensure_ascii=False),
                _now(),
                json.dumps(resolved_selection or {}, ensure_ascii=False),
                str(resolved_prompt or ""),
            ),
        )
        self.connection.commit()
        return self._get_image(cursor.lastrowid)

    async def get_batch(self, batch_id: str) -> dict[str, Any]:
        return await self._call(self._get_batch, batch_id)

    def _get_batch(self, batch_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
        if row is None:
            raise KeyError(batch_id)
        result = dict(row)
        result["settings"] = json.loads(result.pop("settings_json"))
        return result

    async def get_image(self, image_id: int) -> dict[str, Any]:
        return await self._call(self._get_image, image_id)

    def _get_image(self, image_id: int) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT images.*, batches.total AS batch_total FROM images JOIN batches ON batches.id = images.batch_id WHERE images.id = ?",
            (image_id,),
        ).fetchone()
        if row is None:
            raise KeyError(image_id)
        return self._image_row(row)

    def _image_row(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["settings"] = json.loads(result.pop("settings_json"))
        result["resolved_selection"] = json.loads(result.pop("resolved_selection_json", "{}") or "{}")
        return result

    async def list_images(self, page: int = 1, limit: int = 24) -> dict[str, Any]:
        return await self._call(self._list_images, page, limit)

    def _list_images(self, page: int = 1, limit: int = 24) -> dict[str, Any]:
        page = max(1, page)
        limit = min(60, max(1, limit))
        total = int(self.connection.execute("SELECT COUNT(*) FROM images").fetchone()[0])
        rows = self.connection.execute(
            """
            SELECT images.*, batches.total AS batch_total
            FROM images JOIN batches ON batches.id = images.batch_id
            ORDER BY images.id DESC LIMIT ? OFFSET ?
            """,
            (limit, (page - 1) * limit),
        ).fetchall()
        return {
            "items": [self._image_row(row) for row in rows],
            "page": page,
            "limit": limit,
            "total": total,
            "pages": max(1, (total + limit - 1) // limit),
        }

    async def delete_image(self, image_id: int) -> bool:
        return await self._call(self._delete_image, image_id)

    def _delete_image(self, image_id: int) -> bool:
        cursor = self.connection.execute("DELETE FROM images WHERE id = ?", (image_id,))
        self.connection.commit()
        return cursor.rowcount > 0

    async def close(self) -> None:
        await self._call(self.connection.close)
        self._executor.shutdown(wait=True)
