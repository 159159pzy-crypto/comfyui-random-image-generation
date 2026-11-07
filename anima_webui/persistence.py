"""本地持久化文件的通用工具。"""

from __future__ import annotations

import os
import logging
import shutil
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


class StorageReadOnlyError(RuntimeError):
    """A store cannot be changed until its original data is safely recovered."""

    code = "storage_read_only"

    def __init__(self, path: Path, store: str, reason: str):
        self.path = Path(path)
        self.store = store
        self.read_only_reason = reason
        super().__init__(reason)


class StorageProtection:
    """Shared protection for readable fallback views whose source is unsafe to write."""

    store_name = "storage"
    path: Path
    load_warnings: list[str]
    read_only_reason: str | None = None

    def ensure_writable(self) -> None:
        if self.read_only_reason:
            raise StorageReadOnlyError(self.path, self.store_name, self.read_only_reason)

    def storage_status(self) -> dict[str, Any]:
        return {
            "store": self.store_name,
            "read_only": self.read_only_reason is not None,
            "reason": self.read_only_reason,
        }

    def _protect_storage(self, reason: str) -> None:
        self.read_only_reason = (
            f"{self.path.name}: {reason}；已停止此存储的写入，"
            "请检查原文件和目录权限，修复后重新启动工作台"
        )
        self.load_warnings.append(self.read_only_reason)
        logger.warning("%s", self.read_only_reason)

    def _recover_load_failure(self, error: Exception, *, fallback: str = "空数据") -> None:
        # An unreadable file is not necessarily corrupt. Never move a valid
        # file merely because Windows denied access or a disk read failed.
        if isinstance(error, OSError):
            self._protect_storage(f"文件读取失败({error})，原文件未改动")
            return
        try:
            backup = backup_corrupt_file(self.path)
        except OSError as backup_error:
            self._protect_storage(
                f"文件内容无法读取({error})，备份失败({backup_error})，原文件未改动"
            )
            return
        message = f"{self.path.name} 无法读取({error}),已备份为 {backup.name} 并以{fallback}启动"
        self.load_warnings.append(message)
        logger.warning("%s", message)


def backup_corrupt_file(path: Path) -> Path:
    """把无法读取的数据文件改名备份,让应用可以用空数据继续启动。

    原文件内容不会丢失:用户可以手工检查/修复备份文件后再改回原名。
    """
    backup = _reserve_backup(path, "corrupt")
    try:
        os.replace(path, backup)
    except OSError:
        _discard_incomplete_backup(backup)
        raise
    return backup


def _reserve_backup(path: Path, label: str) -> Path:
    """Claim a backup name atomically; concurrent startup must not overwrite it."""
    counter = 0
    while True:
        suffix = f"-{counter}" if counter else ""
        backup = path.with_name(f"{path.name}.{label}{suffix}.bak")
        try:
            with backup.open("xb"):
                pass
        except FileExistsError:
            counter += 1
        else:
            return backup


def _discard_incomplete_backup(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # Keep the original I/O failure as the actionable error. An incomplete
        # reserved file can be inspected manually; the source is untouched.
        logger.warning("无法删除未完成的备份 %s", path, exc_info=True)


def backup_for_migration(path: Path, version: int) -> Path | None:
    """Keep the original persisted file before an in-place schema upgrade."""
    if not path.is_file():
        return None
    backup = _reserve_backup(path, f"v{version}")
    try:
        shutil.copy2(path, backup)
        # Windows' _commit requires a writable file descriptor.
        with backup.open("r+b") as stream:
            os.fsync(stream.fileno())
    except OSError:
        _discard_incomplete_backup(backup)
        raise
    return backup
