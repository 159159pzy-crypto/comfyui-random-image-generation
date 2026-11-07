from __future__ import annotations

import asyncio
import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .async_utils import serialized_mutation
from .persistence import StorageProtection, backup_for_migration
from .model_profiles import (
    DEFAULT_MODEL_PROFILE,
    MODEL_PROFILE_IDS,
    ModelProfileError,
    normalize_model_profile,
)
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


class LoraTriggerOverrideStore(StorageProtection):
    store_name = "lora_triggers"

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # ``profile_overrides`` is the v2 store.  ``overrides`` remains an
        # Anima alias for callers and v1 tests which used the old flat map.
        self.profile_overrides: dict[str, dict[str, list[str]]] = {}
        self.overrides: dict[str, list[str]] = {}
        self.load_warnings: list[str] = []
        self._lock = asyncio.Lock()
        self.reload()

    def reload(self) -> None:
        self.load_warnings = []
        self.read_only_reason = None
        if not self.path.is_file():
            self.profile_overrides = {}
            self.overrides = {}
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") not in {1, 2}:
                raise WorkflowError("LoRA 触发词覆盖文件版本无效")
            raw_overrides = payload.get("overrides", {})
            if not isinstance(raw_overrides, dict):
                raise WorkflowError("LoRA 触发词覆盖内容无效")
            profile_overrides: dict[str, dict[str, list[str]]] = {}
            migrated_legacy = payload.get("version") == 1
            if migrated_legacy:
                profile_overrides[DEFAULT_MODEL_PROFILE] = self._normalize_map(raw_overrides)
            else:
                if len(raw_overrides) > len(MODEL_PROFILE_IDS) + 16:
                    raise WorkflowError("LoRA 触发词覆盖配置过多")
                for raw_profile, values in raw_overrides.items():
                    profile = self._profile(raw_profile)
                    if not isinstance(values, dict):
                        raise WorkflowError("LoRA 触发词覆盖内容无效")
                    profile_overrides[profile] = self._normalize_map(values)
            if sum(len(values) for values in profile_overrides.values()) > MAX_OVERRIDES:
                raise WorkflowError(f"LoRA 触发词覆盖不能超过 {MAX_OVERRIDES} 项")
        except (OSError, UnicodeError, json.JSONDecodeError, WorkflowError) as error:
            self._recover_load_failure(error)
            self.profile_overrides = {}
            self.overrides = {}
            return
        self.profile_overrides = profile_overrides
        self.overrides = self.profile_overrides.setdefault(DEFAULT_MODEL_PROFILE, {})
        if migrated_legacy:
            try:
                backup = backup_for_migration(self.path, 1)
                if backup is not None:
                    self.load_warnings.append(f"LoRA 触发词配置已迁移,原文件备份为 {backup.name}")
                self._write_text(
                    json.dumps(
                        {"version": 2, "overrides": self.profile_overrides},
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n"
                )
            except OSError as error:
                self._protect_storage(f"LoRA 触发词已在内存迁移，但迁移备份或写回失败({error})")

    @staticmethod
    def _profile(value: Any) -> str:
        try:
            return normalize_model_profile(value or DEFAULT_MODEL_PROFILE)
        except ModelProfileError as error:
            raise WorkflowError(str(error)) from error

    @staticmethod
    def _normalize_map(raw: dict[str, Any]) -> dict[str, list[str]]:
        if len(raw) > MAX_OVERRIDES:
            raise WorkflowError(f"LoRA 触发词覆盖不能超过 {MAX_OVERRIDES} 项")
        result: dict[str, list[str]] = {}
        for raw_filename, words in raw.items():
            filename = normalize_lora_path(raw_filename)
            result[filename] = normalize_trigger_words(words)
        return result

    def _map(self, model_profile: Any = DEFAULT_MODEL_PROFILE) -> dict[str, list[str]]:
        profile = self._profile(model_profile)
        return self.profile_overrides.setdefault(profile, {})

    def has(self, filename: str, model_profile: Any = DEFAULT_MODEL_PROFILE) -> bool:
        identity = normalize_lora_path(filename).casefold()
        return any(key.casefold() == identity for key in self.profile_overrides.get(self._profile(model_profile), {}))

    def get(self, filename: str, model_profile: Any = DEFAULT_MODEL_PROFILE) -> list[str] | None:
        identity = normalize_lora_path(filename).casefold()
        for key, words in self.profile_overrides.get(self._profile(model_profile), {}).items():
            if key.casefold() == identity:
                return copy.deepcopy(words)
        return None

    def effective(
        self,
        filename: str,
        source_words: Any,
        model_profile: Any = DEFAULT_MODEL_PROFILE,
    ) -> tuple[list[str], bool]:
        override = self.get(filename, model_profile)
        if override is not None:
            return override, True
        try:
            return normalize_trigger_words(source_words), False
        except WorkflowError:
            return [], False

    @serialized_mutation
    async def set(
        self,
        filename: str,
        words: Any,
        model_profile: Any = DEFAULT_MODEL_PROFILE,
    ) -> list[str]:
        normalized_filename = normalize_lora_path(filename)
        normalized_words = normalize_trigger_words(words)
        profile = self._profile(model_profile)
        self.ensure_writable()
        previous = copy.deepcopy((self.profile_overrides, self.overrides))
        overrides = self.profile_overrides.get(profile, {})
        existing_key = next(
            (key for key in overrides if key.casefold() == normalized_filename.casefold()),
            None,
        )
        if existing_key is None and sum(len(value) for value in self.profile_overrides.values()) >= MAX_OVERRIDES:
            raise WorkflowError(f"LoRA 触发词覆盖不能超过 {MAX_OVERRIDES} 项")
        self.profile_overrides[profile] = overrides
        if existing_key is not None and existing_key != normalized_filename:
            del overrides[existing_key]
        overrides[normalized_filename] = normalized_words
        self.overrides = self.profile_overrides.setdefault(DEFAULT_MODEL_PROFILE, {})
        try:
            await self._save()
        except Exception:
            self.profile_overrides, self.overrides = previous
            raise
        return copy.deepcopy(normalized_words)

    @serialized_mutation
    async def delete(self, filename: str, model_profile: Any = DEFAULT_MODEL_PROFILE) -> bool:
        identity = normalize_lora_path(filename).casefold()
        profile = self._profile(model_profile)
        self.ensure_writable()
        previous = copy.deepcopy((self.profile_overrides, self.overrides))
        overrides = self.profile_overrides.get(profile, {})
        key = next((item for item in overrides if item.casefold() == identity), None)
        if key is None:
            return False
        del overrides[key]
        self.overrides = self.profile_overrides.setdefault(DEFAULT_MODEL_PROFILE, {})
        try:
            await self._save()
        except Exception:
            self.profile_overrides, self.overrides = previous
            raise
        return True

    async def _save(self) -> None:
        payload = json.dumps(
            {"version": 2, "overrides": self.profile_overrides},
            ensure_ascii=False,
            indent=2,
        ) + "\n"
        await asyncio.to_thread(self._write_text, payload)

    def _write_text(self, text: str) -> None:
        self.ensure_writable()
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
