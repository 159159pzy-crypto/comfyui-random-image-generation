from __future__ import annotations

import asyncio
import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .persistence import backup_corrupt_file
from .workflow import WorkflowError, normalize_lora_path


MAX_OVERRIDES = 4096
MAX_TRIGGER_WORDS = 256
MAX_TRIGGER_LENGTH = 500


def normalize_trigger_words(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise WorkflowError("triggerWords 必须是数组")
    if len(value) > MAX_TRIGGER_WORDS:
        raise WorkflowError(f"LoRA 触发词不能超过 {MAX_TRIGGER_WORDS} 个")
    result: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        if not isinstance(raw, str):
            raise WorkflowError(f"triggerWords[{index}] 必须是字符串")
        word = raw.strip()
        if len(word) > MAX_TRIGGER_LENGTH:
            raise WorkflowError(f"triggerWords[{index}] 不能超过 {MAX_TRIGGER_LENGTH} 个字符")
        identity = word.casefold()
        if word and identity not in seen:
            result.append(word)
            seen.add(identity)
    return result


class LoraTriggerOverrideStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.overrides: dict[str, list[str]] = {}
        self.load_warnings: list[str] = []
        self._lock = asyncio.Lock()
        self.reload()

    def reload(self) -> None:
        self.load_warnings = []
        if not self.path.is_file():
            self.overrides = {}
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != 1:
                raise WorkflowError("LoRA 触发词覆盖文件版本无效")
            raw_overrides = payload.get("overrides", {})
            if not isinstance(raw_overrides, dict) or len(raw_overrides) > MAX_OVERRIDES:
                raise WorkflowError("LoRA 触发词覆盖内容无效")
            overrides: dict[str, list[str]] = {}
            for raw_filename, words in raw_overrides.items():
                filename = normalize_lora_path(raw_filename)
                overrides[filename] = normalize_trigger_words(words)
        except (OSError, json.JSONDecodeError, WorkflowError) as error:
            backup = backup_corrupt_file(self.path)
            self.load_warnings.append(
                f"LoRA 触发词覆盖文件无法读取({error}),已备份为 {backup.name} 并以空数据启动"
            )
            self.overrides = {}
            return
        self.overrides = overrides

    def has(self, filename: str) -> bool:
        identity = normalize_lora_path(filename).casefold()
        return any(key.casefold() == identity for key in self.overrides)

    def get(self, filename: str) -> list[str] | None:
        identity = normalize_lora_path(filename).casefold()
        for key, words in self.overrides.items():
            if key.casefold() == identity:
                return copy.deepcopy(words)
        return None

    def effective(self, filename: str, source_words: Any) -> tuple[list[str], bool]:
        override = self.get(filename)
        if override is not None:
            return override, True
        try:
            return normalize_trigger_words(source_words), False
        except WorkflowError:
            return [], False

    async def set(self, filename: str, words: Any) -> list[str]:
        normalized_filename = normalize_lora_path(filename)
        normalized_words = normalize_trigger_words(words)
        async with self._lock:
            existing_key = next(
                (key for key in self.overrides if key.casefold() == normalized_filename.casefold()),
                None,
            )
            if existing_key is None and len(self.overrides) >= MAX_OVERRIDES:
                raise WorkflowError(f"LoRA 触发词覆盖不能超过 {MAX_OVERRIDES} 项")
            if existing_key is not None and existing_key != normalized_filename:
                del self.overrides[existing_key]
            self.overrides[normalized_filename] = normalized_words
            await self._save()
        return copy.deepcopy(normalized_words)

    async def delete(self, filename: str) -> bool:
        identity = normalize_lora_path(filename).casefold()
        async with self._lock:
            key = next((item for item in self.overrides if item.casefold() == identity), None)
            if key is None:
                return False
            del self.overrides[key]
            await self._save()
        return True

    async def _save(self) -> None:
        payload = json.dumps(
            {"version": 1, "overrides": self.overrides},
            ensure_ascii=False,
            indent=2,
        ) + "\n"
        await asyncio.to_thread(self._write_text, payload)

    def _write_text(self, text: str) -> None:
        fd, temp_name = tempfile.mkstemp(
            prefix="lora-trigger-overrides-", suffix=".json", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
