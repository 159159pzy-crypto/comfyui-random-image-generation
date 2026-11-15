"""User-confirmed resource roles, bound to a current file identity."""
from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .async_utils import serialized_mutation
from .persistence import StorageProtection
from .workflow import WorkflowError, normalize_lora_path


RESOURCE_ROLES = {
    "models": {"anima.model", "seedvr2.model"},
    "clips": {"anima.clip"},
    "vaes": {"anima.vae", "seedvr2.vae"},
    "upscale_models": {"upscale"},
}


class ResourceUsageStore(StorageProtection):
    store_name = "resource_usage"

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self.load_warnings: list[str] = []
        self.entries: dict[str, dict[str, Any]] = {}
        self.reload()

    @staticmethod
    def _entry(kind: Any, name: Any, roles: Any, fingerprint: Any) -> dict[str, Any]:
        if not isinstance(kind, str) or kind not in RESOURCE_ROLES:
            raise WorkflowError("资源类型无效")
        name = normalize_lora_path(name)
        if not isinstance(roles, list) or not roles or not all(isinstance(role, str) for role in roles):
            raise WorkflowError("用途 roles 必须是非空字符串数组")
        if set(roles) - RESOURCE_ROLES[kind]:
            raise WorkflowError("用途与资源加载类型不兼容")
        # The shared generation VAE can serve both supported profiles; other
        # model families are intentionally exclusive.
        if len(set(roles)) > 1:
            raise WorkflowError("不能把同一模型指定为互不兼容的用途")
        if not isinstance(fingerprint, str) or not fingerprint.strip() or len(fingerprint) > 512:
            raise WorkflowError("当前文件身份不可用，请刷新资源后重试")
        return {"kind": kind, "name": name, "roles": sorted(set(roles)), "fingerprint": fingerprint}

    @staticmethod
    def _key(kind: str, name: str) -> str:
        return kind + ":" + normalize_lora_path(name).casefold()

    def reload(self) -> None:
        self.entries = {}
        self.load_warnings = []
        self.read_only_reason = None
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("items"), list):
                raise WorkflowError("模型用途文件版本或格式无效")
            entries = {}
            for item in data["items"]:
                if not isinstance(item, dict):
                    raise WorkflowError("模型用途条目无效")
                entry = self._entry(item.get("kind"), item.get("name"), item.get("roles"), item.get("fingerprint"))
                entries[self._key(entry["kind"], entry["name"])] = entry
            self.entries = entries
        except (OSError, ValueError, TypeError) as error:
            self._recover_load_failure(error)

    def public(self) -> dict[str, Any]:
        return {"version": 1, "items": copy.deepcopy(list(self.entries.values())),
                "storage": self.storage_status()}

    def lookup(self, kind: str, name: str, fingerprint: str) -> dict[str, Any] | None:
        entry = self.entries.get(self._key(kind, name))
        return copy.deepcopy(entry) if fingerprint and entry and entry["fingerprint"] == fingerprint else None

    @serialized_mutation
    async def set_usage(self, kind: str, name: str, roles: list[str], fingerprint: str,
                        *, source_status: str = "unknown") -> dict[str, Any]:
        self.ensure_writable()
        if source_status not in {"unknown", "manual"}:
            raise WorkflowError("已识别的架构或无效文件不能覆盖用途")
        entry = self._entry(kind, name, roles, fingerprint)
        changed = {**self.entries, self._key(kind, name): entry}
        await asyncio.to_thread(self._write, changed)
        self.entries = changed
        return copy.deepcopy(entry)

    @serialized_mutation
    async def remove_usage(self, kind: str, name: str) -> dict[str, Any]:
        self.ensure_writable()
        if not isinstance(kind, str) or kind not in RESOURCE_ROLES:
            raise WorkflowError("资源类型无效")
        changed = dict(self.entries)
        changed.pop(self._key(kind, name), None)
        await asyncio.to_thread(self._write, changed)
        self.entries = changed
        return self.public()

    def _write(self, entries: dict[str, Any]) -> None:
        self.ensure_writable()
        fd, name = tempfile.mkstemp(prefix="resource-usage-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump({"version": 1, "items": list(entries.values())}, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
