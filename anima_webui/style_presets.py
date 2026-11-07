from __future__ import annotations

import asyncio
import copy
import functools
import json
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .async_utils import serialized_mutation
from .persistence import StorageProtection, backup_for_migration
from .prompt_rules import PromptRuleStore
from .model_profiles import (
    DEFAULT_MODEL_PROFILE,
    ModelProfileError,
    get_model_profile,
    normalize_model_profile,
)
from .workflow import DEFAULT_SETTINGS, WorkflowError, upgrade_pipeline_settings, validate_settings


logger = logging.getLogger(__name__)


def _locked(method: Any) -> Any:
    """变更方法整体串行化:内存修改在事件循环线程完成,写盘经 to_thread 让出循环。"""

    @functools.wraps(method)
    @serialized_mutation
    async def wrapper(self: "StylePresetStore", *args: Any, **kwargs: Any) -> Any:
        self.ensure_writable()
        previous_items = copy.deepcopy(self.items)
        try:
            return await method(self, *args, **kwargs)
        except Exception:
            self.items = previous_items
            raise

    return wrapper


PRESET_SETTING_KEYS = (
    "pipeline_version",
    "workbench",
    "generation_mode",
    "model_profile",
    "model_name",
    "clip_name",
    "vae_name",
    "clip_type",
    "loras",
    "lora_managed_triggers",
    "manual_artist",
    "quality_prompt",
    "extra_prompt",
    "negative_prompt",
    "width",
    "height",
    "steps",
    "cfg",
    "sampler_name",
    "scheduler",
    "base_mode",
    "base_denoise",
    "dual",
    "upscalers",
)
# v2 snapshots are intentionally free of controls that no longer exist in
# the UI.  ``PRESET_SETTING_KEYS`` still accepts those names at the import
# boundary so a v1 JSON file can be migrated without data loss before its
# obsolete controls are dropped.
V2_PRESET_SETTING_KEYS = tuple(
    key
    for key in PRESET_SETTING_KEYS
    if key not in {"hires", "detailers", "cfg_zero_star", "second_sampler"}
)
MAX_PRESETS = 256


def preset_settings(
    value: Any,
    prompt_rules: PromptRuleStore | None = None,
    *,
    force_model_profile: str | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowError("风格预设 settings 必须是对象")
    unknown = set(value) - set(PRESET_SETTING_KEYS)
    if unknown:
        raise WorkflowError(f"风格预设包含未知参数: {', '.join(sorted(unknown))}")
    # Start from the public defaults.  Copying DEFAULT_SETTINGS wholesale
    # would reintroduce v1 keys and cause validate_settings to serialize them
    # into every newly saved preset.
    candidate = {
        key: copy.deepcopy(DEFAULT_SETTINGS[key])
        for key in V2_PRESET_SETTING_KEYS
        if key in DEFAULT_SETTINGS
    }
    # Presets are snapshots.  Legacy snapshots are migrated before validation
    # so removed v1 controls cannot leak into a new profile's preset.
    migrated = upgrade_pipeline_settings(value)
    if force_model_profile is not None:
        forced_profile = normalize_model_profile(force_model_profile)
        profile = get_model_profile(forced_profile)
        migrated.update(
            {
                "model_profile": forced_profile,
                "clip_name": profile["clip_name"],
                "vae_name": profile["vae_name"],
                "clip_type": profile["clip_type"],
            }
        )
    candidate.update(migrated)
    if force_model_profile is None and "model_profile" in value:
        candidate["model_profile"] = value["model_profile"]
    if prompt_rules is not None:
        candidate, _ = prompt_rules.normalize_settings(candidate, model_profile=candidate.get("model_profile", "anima"))
    normalized = validate_settings(candidate)
    return {
        key: copy.deepcopy(normalized[key])
        for key in V2_PRESET_SETTING_KEYS
        if key in normalized
    }


class StylePresetStore(StorageProtection):
    store_name = "style_presets"

    def __init__(self, path: str | Path, prompt_rules: PromptRuleStore | None = None):
        self.path = Path(path)
        self.prompt_rules = prompt_rules
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.items: list[dict[str, Any]] = []
        self.load_warnings: list[str] = []
        self._lock = asyncio.Lock()
        self.reload()

    def reload(self) -> None:
        self.load_warnings = []
        self.read_only_reason = None
        if not self.path.is_file():
            self.items = []
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            values = payload.get("items", []) if isinstance(payload, dict) else []
            version = payload.get("version", 1) if isinstance(payload, dict) else 1
            if not isinstance(payload, dict) or version not in {1, 2}:
                raise WorkflowError("风格预设文件版本无效")
            if not isinstance(values, list):
                raise WorkflowError("风格预设 items 必须是数组")
            # Version 1 snapshots predate profile namespaces and the v2 repair
            # schema. Always preserve a backup before persisting normalization, even
            # when a hand-edited v1 item already happens to contain a profile.
            migrated_legacy = version == 1
            # Migrate each snapshot independently.  A single hand-edited or
            # obsolete entry must not make every previously saved style
            # disappear.  Keep a warning with the item name so the user can
            # repair it manually; valid entries remain available immediately.
            items = []
            cleaned_legacy = False
            for index, raw_item in enumerate(values):
                if not isinstance(raw_item, dict):
                    self.load_warnings.append(f"风格预设第 {index + 1} 项不是对象,已跳过")
                    cleaned_legacy = True
                    continue
                try:
                    items.append(
                        self._normalize(
                            raw_item,
                            existing=True,
                            force_model_profile=DEFAULT_MODEL_PROFILE if migrated_legacy else None,
                        )
                    )
                except (WorkflowError, TypeError, ValueError) as error:
                    # v1 exports occasionally contain fields added by a newer
                    # UI.  Retry with the documented snapshot keys removed
                    # from the outer envelope; this preserves old settings
                    # while keeping the strict API validation for new writes.
                    fallback = dict(raw_item)
                    raw_settings = fallback.get("settings")
                    if isinstance(raw_settings, dict):
                        fallback["settings"] = {
                            key: value
                            for key, value in raw_settings.items()
                            if key in PRESET_SETTING_KEYS
                        }
                    try:
                        items.append(
                            self._normalize(
                                fallback,
                                existing=True,
                                force_model_profile=DEFAULT_MODEL_PROFILE if migrated_legacy else None,
                            )
                        )
                        self.load_warnings.append(
                            f"风格预设 {raw_item.get('name') or index + 1} 含旧字段,已自动迁移"
                        )
                        cleaned_legacy = True
                    except (WorkflowError, TypeError, ValueError):
                        self.load_warnings.append(
                            f"风格预设 {raw_item.get('name') or index + 1} 无法迁移({error}),已跳过"
                        )
                        cleaned_legacy = True
        except (OSError, UnicodeError, json.JSONDecodeError, WorkflowError) as error:
            self._recover_load_failure(error)
            self.items = []
            return
        self.items = items
        if migrated_legacy or cleaned_legacy:
            try:
                backup = backup_for_migration(self.path, 2)
                if backup is not None:
                    self.load_warnings.append(
                        f"风格预设已迁移,原文件备份为 {backup.name}"
                    )
                self._write_text(
                    json.dumps({"version": 2, "items": self.items}, ensure_ascii=False, indent=2)
                    + "\n"
                )
            except OSError as error:
                self._protect_storage(f"风格预设已在内存迁移，但迁移备份或写回失败({error})")

    def list(self, model_profile: str | None = None) -> dict[str, Any]:
        profile_id: str | None = None
        if model_profile is not None and str(model_profile).strip():
            try:
                profile_id = normalize_model_profile(model_profile)
            except ModelProfileError as error:
                raise WorkflowError(str(error)) from error
        visible = self.items
        if profile_id:
            visible = [
                item
                for item in visible
                if item["settings"].get("model_profile", DEFAULT_MODEL_PROFILE) == profile_id
            ]
        favorites = sorted(
            (item for item in visible if item["favorite"]),
            key=lambda item: item["updated_at"],
            reverse=True,
        )
        regular = sorted(
            (item for item in visible if not item["favorite"]),
            key=lambda item: item["updated_at"],
            reverse=True,
        )
        return {
            "items": copy.deepcopy(favorites + regular),
            "count": len(visible),
            "model_profile": profile_id,
        }

    @_locked
    async def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        if len(self.items) >= MAX_PRESETS:
            raise WorkflowError(f"风格预设不能超过 {MAX_PRESETS} 个")
        profile_id = payload.get("model_profile") or (payload.get("settings") or {}).get(
            "model_profile", DEFAULT_MODEL_PROFILE
        )
        self._ensure_unique_name(payload.get("name"), model_profile=profile_id)
        now = self._now()
        item = self._normalize(
            {
                **payload,
                "model_profile": profile_id,
                "id": f"preset_{uuid.uuid4().hex[:16]}",
                "created_at": now,
                "updated_at": now,
            }
        )
        self.items.append(item)
        await self._save()
        return copy.deepcopy(item)

    @_locked
    async def update(self, preset_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        index = next((index for index, item in enumerate(self.items) if item["id"] == preset_id), None)
        if index is None:
            raise KeyError(preset_id)
        current = self.items[index]
        if "name" in payload:
            profile_id = payload.get("model_profile") or (
                payload.get("settings") or current.get("settings") or {}
            ).get("model_profile", DEFAULT_MODEL_PROFILE)
            self._ensure_unique_name(
                payload.get("name"), excluding_id=preset_id, model_profile=profile_id
            )
        item = self._normalize(
            {
                **current,
                **payload,
                "id": preset_id,
                "created_at": current["created_at"],
                "updated_at": self._now(),
            }
        )
        self.items[index] = item
        await self._save()
        return copy.deepcopy(item)

    @_locked
    async def delete(self, preset_id: str) -> bool:
        previous = len(self.items)
        self.items = [item for item in self.items if item["id"] != preset_id]
        if len(self.items) == previous:
            return False
        await self._save()
        return True

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _ensure_unique_name(
        self,
        value: Any,
        excluding_id: str | None = None,
        model_profile: Any = DEFAULT_MODEL_PROFILE,
    ) -> None:
        name = str(value or "").strip().casefold()
        try:
            profile_id = normalize_model_profile(model_profile)
        except ModelProfileError as error:
            raise WorkflowError(str(error)) from error
        if name and any(
            item["id"] != excluding_id and item["name"].strip().casefold() == name
            and item["settings"].get("model_profile", DEFAULT_MODEL_PROFILE) == profile_id
            for item in self.items
        ):
            raise WorkflowError("已有同名风格预设")

    def _normalize(
        self,
        payload: dict[str, Any],
        existing: bool = False,
        force_model_profile: str | None = None,
    ) -> dict[str, Any]:
        preset_id = str(payload.get("id") or "").strip()
        name = str(payload.get("name") or "").strip()
        favorite = payload.get("favorite", False)
        if not preset_id or not name or len(name) > 100:
            raise WorkflowError("风格预设名称需要 1-100 个字符")
        if not isinstance(favorite, bool):
            raise WorkflowError("风格预设 favorite 必须是布尔值")
        created_at = str(payload.get("created_at") or "")
        updated_at = str(payload.get("updated_at") or created_at)
        if existing and not created_at:
            created_at = updated_at = StylePresetStore._now()
        raw_settings = payload.get("settings")
        if raw_settings is None:
            raw_settings = {
                "model_profile": payload.get("model_profile", DEFAULT_MODEL_PROFILE)
            }
        if not isinstance(raw_settings, dict):
            raise WorkflowError("风格预设 settings 必须是对象")
        if force_model_profile is None and payload.get("model_profile") is not None:
            raw_settings = {**raw_settings, "model_profile": payload["model_profile"]}
        normalized_settings = preset_settings(
            raw_settings,
            self.prompt_rules,
            force_model_profile=force_model_profile,
        )
        # Top-level profile is a convenience for API filtering; the settings
        # snapshot remains authoritative when loading a preset.
        profile_id = normalized_settings.get("model_profile", DEFAULT_MODEL_PROFILE)
        return {
            "id": preset_id,
            "name": name,
            "favorite": favorite,
            "model_profile": profile_id,
            "settings": normalized_settings,
            "created_at": created_at,
            "updated_at": updated_at,
        }

    async def _save(self) -> None:
        # 快照序列化在事件循环线程完成(状态一致),fsync 等慢速 I/O 移入工作线程。
        payload = json.dumps({"version": 2, "items": self.items}, ensure_ascii=False, indent=2) + "\n"
        await asyncio.to_thread(self._write_text, payload)

    def _write_text(self, text: str) -> None:
        self.ensure_writable()
        fd, temp_name = tempfile.mkstemp(prefix="style-presets-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
