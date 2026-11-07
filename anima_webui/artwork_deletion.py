"""Delete one history record and its explicitly requested local original."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sqlite3
import stat
from typing import Any

from .history import HistoryStore
from . import image_location
from .image_location import ImageLocationError
from .persistence import StorageReadOnlyError
from .workflow import WorkflowError


def deletion_image_path(directory: Path, record: dict[str, Any]) -> Path:
    """Permit a missing leaf, but never interpret an unavailable root as absence."""
    parts = image_location._record_parts(record)
    try:
        root = directory.resolve(strict=True)
        if not root.is_dir():
            raise OSError("not a directory")
    except (OSError, RuntimeError, ValueError):
        raise ImageLocationError("无法访问 ComfyUI 图片目录，未删除记录", code="image_directory_unavailable") from None
    try:
        target = root.joinpath(*parts).resolve(strict=False)
        if not target.is_relative_to(root) or target == root:
            raise ImageLocationError("图片路径超出 ComfyUI 文件目录", code="invalid_image_location", status=400)
        try:
            info = target.stat()
        except FileNotFoundError:
            return target
        if not stat.S_ISREG(info.st_mode):
            raise ImageLocationError("原图路径不是普通文件", code="invalid_image_location", status=400)
        return target
    except ImageLocationError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise ImageLocationError("无法访问原图文件，请检查目录与文件权限", code="image_file_unavailable") from None


def _result(image_id: int, **values: Any) -> dict[str, Any]:
    return {"id": image_id, "record_deleted": False, "file_deleted": False,
            "file_missing": False, "code": "", "error": "", **values}


class ArtworkDeletionService:
    def __init__(self, history: HistoryStore, thumbnails: Any | None = None):
        self.history = history
        self.thumbnails = thumbnails
        self._lock = asyncio.Lock()

    async def delete(self, image_id: int, mode: str = "record", *,
                     stats: dict[str, Any] | None = None, comfy_url: str = "") -> dict[str, Any]:
        if type(image_id) is not int or not 1 <= image_id <= 2**63 - 1:
            raise WorkflowError("作品 ID 必须为正整数")
        if not isinstance(mode, str) or mode not in {"record", "file"}:
            raise WorkflowError("删除模式必须为 record 或 file")
        self.history.ensure_writable()
        task = asyncio.create_task(self._run(image_id, mode, stats or {}, comfy_url))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # A worker may have unlinked or committed already. Finish derived
            # cleanup even if the HTTP caller is cancelled more than once.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not task.cancelled():
                task.exception()
            raise

    async def _run(self, image_id: int, mode: str, stats: dict[str, Any], comfy_url: str) -> dict[str, Any]:
        async with self._lock:
            if mode == "file":
                try:
                    if not image_location.IS_WINDOWS:
                        raise ImageLocationError("删除原文件仅支持本机 Windows ComfyUI", code="image_delete_unsupported")
                    await asyncio.to_thread(image_location.verify_local_comfy, comfy_url, stats)
                except ImageLocationError as error:
                    return self._payload(mode, _result(image_id, code=error.code, error=str(error)))
            result = await self.history._call(self._delete, image_id, mode, stats)
            if self.thumbnails is not None and (result["record_deleted"] or result["code"] == "already_missing"):
                try:
                    await self.thumbnails.discard(image_id)
                except Exception:
                    result["warning"] = "记录已删除，缩略图缓存清理失败，将在后续维护时重试"
            return self._payload(mode, result)

    @staticmethod
    def _payload(mode: str, result: dict[str, Any]) -> dict[str, Any]:
        # Keep the single-delete HTTP response compatible with existing clients.
        success = int(result["record_deleted"] or result["code"] == "already_missing")
        return {"mode": mode, "results": [result], "deleted_count": int(result["record_deleted"]),
                "success_count": success, "failed_count": 1 - success,
                "file_deleted_count": int(result["file_deleted"])}

    def _rollback(self) -> None:
        try:
            if self.history.connection.in_transaction:
                self.history.connection.rollback()
        except sqlite3.Error as error:
            self.history._protect_storage(f"历史数据库事务回滚失败({error})")

    @staticmethod
    def _path(record: dict[str, Any], stats: dict[str, Any]) -> Path:
        directory = image_location.comfy_image_directory(stats, record.get("file_type") or "output")
        return deletion_image_path(Path(directory), record)

    def _delete(self, image_id: int, mode: str, stats: dict[str, Any]) -> dict[str, Any]:
        self.history.ensure_writable()
        connection = self.history.connection
        if mode == "record":
            try:
                connection.execute("BEGIN IMMEDIATE")
                deleted = connection.execute("DELETE FROM images WHERE id = ?", (image_id,)).rowcount > 0
                connection.commit()
                return _result(image_id, record_deleted=deleted, code="deleted" if deleted else "already_missing")
            except sqlite3.Error:
                self._rollback()
                return _result(image_id, code="database_error", error="历史记录写入失败，记录未删除")

        row = connection.execute("SELECT id, filename, subfolder, file_type FROM images WHERE id = ?", (image_id,)).fetchone()
        if row is None:
            return _result(image_id, code="already_missing")
        try:
            path = self._path(dict(row), stats)
        except ImageLocationError as error:
            return _result(image_id, code=error.code, error=str(error))
        key = os.path.normcase(str(path))
        deleted_file = missing_file = False
        try:
            self.history.ensure_writable()
            connection.execute("BEGIN IMMEDIATE")
            # Re-read the record under the write lock before using its file.
            row = connection.execute("SELECT id, filename, subfolder, file_type FROM images WHERE id = ?", (image_id,)).fetchone()
            if row is None:
                self._rollback()
                return _result(image_id, code="already_missing")
            record = dict(row)
            if os.path.normcase(str(self._path(record, stats))) != key:
                self._rollback()
                return _result(image_id, code="record_changed", error="作品记录已变化，请刷新后重试")
            # A different record may reference the same original through a
            # filename alias or directory junction. Resolve every reference.
            shared = []
            for row in connection.execute("SELECT id, filename, subfolder, file_type FROM images WHERE id != ?", (image_id,)):
                try:
                    candidate = self._path(dict(row), stats)
                except ImageLocationError:
                    continue
                if os.path.normcase(str(candidate)) == key:
                    shared.append(row["id"])
            if shared:
                self._rollback()
                return _result(image_id, code="file_shared", shared_ids=sorted(shared),
                               error="原文件仍被其他历史记录引用，未删除原文件或记录")
            connection.execute("DELETE FROM images WHERE id = ?", (image_id,))
            # Reject a junction changed after either earlier resolution.
            confirmed = self._path(record, stats)
            if os.path.normcase(str(confirmed)) != key:
                raise ImageLocationError("原图路径已变化，请刷新后重试", code="image_location_changed")
            try:
                confirmed.unlink()
                deleted_file = True
            except FileNotFoundError:
                missing_file = True
            connection.commit()
            return _result(image_id, record_deleted=True, file_deleted=deleted_file,
                           file_missing=missing_file, code="deleted")
        except ImageLocationError as error:
            self._rollback()
            return _result(image_id, code=error.code, error=str(error))
        except (sqlite3.Error, StorageReadOnlyError):
            self._rollback()
            message = "原文件已删除，但记录写入失败；记录仍保留，请重试删除记录" if deleted_file else "历史记录写入失败，记录未删除"
            return _result(image_id, file_deleted=deleted_file, file_missing=missing_file,
                           code="database_error", error=message)
        except OSError:
            self._rollback()
            return _result(image_id, code="image_delete_failed", error="无法删除原文件，请检查文件占用或目录权限")
