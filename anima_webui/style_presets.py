from __future__ import annotations

import asyncio
import copy
import functools
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .async_utils import serialized_mutation
from .model_profiles import DEFAULT_MODEL_PROFILE, ModelProfileError, normalize_model_profile
from .persistence import StorageProtection, backup_for_migration
from .preset_contract import normalize_include, normalize_source, preset_settings, resolve_settings
from .prompt_rules import PromptRuleStore
from .workflow import DEFAULT_SETTINGS, WorkflowError


# Capture accepts full settings; preset_contract emits only allowed fields.
PRESET_SETTING_KEYS = tuple(DEFAULT_SETTINGS)
MAX_PRESETS = 256
MAX_GROUPS = 128
MAX_TAGS = 64
STORE_VERSION = 4
_COVER_ID = re.compile(r"^[a-f0-9]{64}$")
_ITEM_KEYS = {"id", "name", "favorite", "model_profile", "settings", "include", "source",
              "groups", "tags", "cover_id", "created_at", "updated_at", "favorite_at"}


def _profile(value: Any) -> str:
    try:
        return normalize_model_profile(value)
    except ModelProfileError as error:
        raise WorkflowError(str(error)) from error


def _locked(method: Any) -> Any:
    @functools.wraps(method)
    @serialized_mutation
    async def wrapper(self: "StylePresetStore", *args: Any, **kwargs: Any) -> Any:
        self.ensure_writable()
        previous = copy.deepcopy((self.items, self.groups))
        try:
            return await method(self, *args, **kwargs)
        except Exception:
            self.items, self.groups = previous
            raise
    return wrapper


def _name(value: Any, label: str = "风格预设") -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 100:
        raise WorkflowError(f"{label}名称需要 1-100 个字符")
    return value.strip()


def _strings(value: Any, label: str, maximum: int, *, fold: bool = False) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        raise WorkflowError(f"{label}必须是最多 {maximum} 项的数组")
    result, seen = [], set()
    for entry in value:
        if not isinstance(entry, str) or not entry.strip() or len(entry.strip()) > 100:
            raise WorkflowError(f"{label}每项需要 1-100 个字符")
        entry = entry.strip()
        identity = entry.casefold() if fold else entry
        if identity not in seen:
            seen.add(identity)
            result.append(entry)
    return result


class StylePresetStore(StorageProtection):
    store_name = "style_presets"

    def __init__(self, path: str | Path, prompt_rules: PromptRuleStore | None = None):
        self.path = Path(path)
        self.prompt_rules = prompt_rules
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.items: list[dict[str, Any]] = []
        self.groups: list[dict[str, Any]] = []
        self.load_warnings: list[str] = []
        self._lock = asyncio.Lock()
        self.reload()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _group_ids(self, values: Any, profile: str) -> list[str]:
        ids = _strings(values, "预设分组", MAX_GROUPS)
        available = {group["id"] for group in self.groups if group["model_profile"] == profile}
        if set(ids) - available:
            raise WorkflowError("预设分组不存在或属于其他模型档案")
        return ids

    def _normalize_group(self, payload: Any) -> dict[str, str]:
        if not isinstance(payload, dict) or set(payload) - {"id", "name", "model_profile"}:
            raise WorkflowError("预设分组格式无效")
        group_id = payload.get("id")
        if not isinstance(group_id, str) or not group_id or len(group_id) > 100:
            raise WorkflowError("预设分组 ID 无效")
        return {"id": group_id, "name": _name(payload.get("name"), "分组"),
                "model_profile": _profile(payload.get("model_profile", DEFAULT_MODEL_PROFILE))}

    def _normalize_item(self, payload: Any, *, legacy: bool = False,
                        force_model_profile: str | None = None) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) - _ITEM_KEYS:
            raise WorkflowError("风格预设包含未知字段或不是对象")
        preset_id = payload.get("id")
        if not isinstance(preset_id, str) or not preset_id or len(preset_id) > 100:
            raise WorkflowError("风格预设 ID 无效")
        favorite = payload.get("favorite", False)
        if not isinstance(favorite, bool):
            raise WorkflowError("风格预设 favorite 必须是布尔值")
        raw = payload.get("settings", {})
        if not isinstance(raw, dict):
            raise WorkflowError("风格预设 settings 必须是对象")
        profile = _profile(force_model_profile or payload.get("model_profile")
                           or raw.get("model_profile", DEFAULT_MODEL_PROFILE))
        if force_model_profile is None and "model_profile" in raw and _profile(raw["model_profile"]) != profile:
            raise WorkflowError("风格预设模型档案不一致")
        flags = normalize_include(None if legacy else payload.get("include"))
        origin = normalize_source(None if legacy else payload.get("source"), raw)
        settings = preset_settings({**raw, "model_profile": profile}, include=flags, source=origin,
                                   force_model_profile=force_model_profile)
        groups = self._group_ids(payload.get("groups", []), profile)
        cover = payload.get("cover_id")
        if cover is not None and (not isinstance(cover, str) or not _COVER_ID.fullmatch(cover)):
            raise WorkflowError("风格预设 cover_id 必须是有效图片 ID 或 null")
        created = payload.get("created_at") or self._now()
        updated = payload.get("updated_at") or created
        if not isinstance(created, str) or not isinstance(updated, str):
            raise WorkflowError("风格预设时间格式无效")
        favorite_at = payload.get("favorite_at")
        if favorite_at is not None and not isinstance(favorite_at, str):
            raise WorkflowError("风格预设收藏时间格式无效")
        # Older files used the last update for favorite ordering. Preserve that
        # order when introducing a separate, server-owned favorite timestamp.
        favorite_at = (favorite_at or updated) if favorite else None
        return {
            "id": preset_id, "name": _name(payload.get("name")), "favorite": favorite,
            "model_profile": profile, "settings": settings, "include": flags, "source": origin,
            "groups": groups, "tags": _strings(payload.get("tags", []), "预设标签", MAX_TAGS, fold=True),
            "cover_id": cover, "created_at": created, "updated_at": updated, "favorite_at": favorite_at,
        }

    @staticmethod
    def _set_mutation_times(item: dict[str, Any], previous: dict[str, Any] | None, now: str) -> None:
        ordering_fields = {"favorite", "favorite_at", "updated_at"}
        content_changed = previous is None or any(
            value != previous.get(key) for key, value in item.items() if key not in ordering_fields
        )
        item["updated_at"] = now if content_changed else previous["updated_at"]
        item["favorite_at"] = (
            previous["favorite_at"] if previous and previous["favorite"] else now
        ) if item["favorite"] else None

    def reload(self) -> None:
        self.load_warnings = []
        self.read_only_reason = None
        # Cover collection needs an authoritative reference set. Informational
        # migration warnings do not make that set incomplete; dropped presets do.
        self.cover_references_complete = True
        self.items, self.groups = [], []
        if not self.path.is_file():
            # A previous corrupt-file recovery may have quarantined the only
            # source before any new write could persist the incomplete marker.
            self.cover_references_complete = not any(
                self.path.parent.glob(f"{self.path.name}.corrupt*.bak")
            )
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or type(payload.get("version", 1)) is not int:
                raise WorkflowError("风格预设文件版本无效")
            version = payload.get("version", 1)
            if version not in {1, 2, 3, 4}:
                raise WorkflowError("风格预设文件版本无效")
            if version >= 3:
                complete = payload.get("cover_references_complete", True)
                if not isinstance(complete, bool):
                    raise WorkflowError("风格预设封面引用完整性标记无效")
                self.cover_references_complete = complete
            values = payload.get("items", [])
            raw_groups = payload.get("groups", []) if version >= 3 else []
            if not isinstance(values, list) or not isinstance(raw_groups, list):
                raise WorkflowError("风格预设 items/groups 必须是数组")
            cleaned = False
            for group in raw_groups:
                try:
                    normalized = self._normalize_group(group)
                    if any(entry["id"] == normalized["id"] or
                           entry["model_profile"] == normalized["model_profile"] and
                           entry["name"].casefold() == normalized["name"].casefold() for entry in self.groups):
                        raise WorkflowError("分组 ID 或名称重复")
                    self.groups.append(normalized)
                except (WorkflowError, TypeError, ValueError) as error:
                    cleaned = True
                    self.load_warnings.append(f"预设分组无法读取({error}),已跳过")
            for index, raw in enumerate(values):
                try:
                    candidate = copy.deepcopy(raw)
                    if version < 3 and isinstance(candidate, dict):
                        candidate = {key: value for key, value in candidate.items() if key in _ITEM_KEYS}
                        if isinstance(candidate.get("settings"), dict):
                            candidate["settings"] = {key: value for key, value in candidate["settings"].items()
                                                     if key in PRESET_SETTING_KEYS}
                    if isinstance(candidate, dict) and isinstance(candidate.get("groups"), list):
                        settings = candidate.get("settings")
                        profile = candidate.get("model_profile", settings.get("model_profile", "anima") if isinstance(settings, dict) else "anima")
                        known = {group["id"] for group in self.groups if group["model_profile"] == profile}
                        kept = [group for group in candidate["groups"] if isinstance(group, str) and group in known]
                        if kept != candidate["groups"]:
                            cleaned = True
                            candidate["groups"] = kept
                    item = self._normalize_item(candidate, legacy=version < 3,
                                                force_model_profile=DEFAULT_MODEL_PROFILE if version == 1 else None)
                    if any(entry["id"] == item["id"] for entry in self.items):
                        raise WorkflowError("预设 ID 重复")
                    self._ensure_unique_name(item["name"], model_profile=item["model_profile"])
                    self.items.append(item)
                except (WorkflowError, TypeError, ValueError) as error:
                    cleaned = True
                    self.cover_references_complete = False
                    name = raw.get("name") if isinstance(raw, dict) else index + 1
                    self.load_warnings.append(f"风格预设 {name or index + 1} 无法迁移({error}),已跳过")
        except (OSError, UnicodeError, json.JSONDecodeError, WorkflowError) as error:
            self.cover_references_complete = False
            self._recover_load_failure(error)
            self.items, self.groups = [], []
            return
        if version < STORE_VERSION or cleaned:
            try:
                backup = backup_for_migration(self.path, STORE_VERSION)
                if backup is not None:
                    self.load_warnings.append(f"风格预设数据格式已迁移升级，原文件备份为 {backup.name}")
                self._write_text(self._serialized())
            except OSError as error:
                self._protect_storage(f"风格预设已在内存迁移，但迁移备份或写回失败({error})")

    def list(self, model_profile: str | None = None) -> dict[str, Any]:
        profile = _profile(model_profile) if model_profile is not None and str(model_profile).strip() else None
        items = [item for item in self.items if profile is None or item["model_profile"] == profile]
        items = sorted(items, key=lambda item: (
            item["favorite"], item["favorite_at"] if item["favorite"] else item["updated_at"], item["updated_at"],
        ), reverse=True)
        return {"items": copy.deepcopy(items), "count": len(items), "model_profile": profile,
                "groups": copy.deepcopy([group for group in self.groups if profile is None or group["model_profile"] == profile])}

    def get(self, preset_id: str, model_profile: str | None = None) -> dict[str, Any]:
        item = next((item for item in self.items if item["id"] == preset_id), None)
        if item is None:
            raise KeyError(preset_id)
        if model_profile is not None and _profile(model_profile) != item["model_profile"]:
            raise WorkflowError("不能跨模型档案操作风格预设")
        return copy.deepcopy(item)

    def resolve(self, preset_id: str, current_settings: Any, model_profile: str | None = None) -> dict[str, Any]:
        return resolve_settings(self.get(preset_id, model_profile), current_settings)

    def _ensure_unique_name(self, value: Any, excluding_id: str | None = None,
                            model_profile: Any = DEFAULT_MODEL_PROFILE) -> None:
        name = _name(value).casefold()
        profile = _profile(model_profile)
        if any(item["id"] != excluding_id and item["name"].casefold() == name
               and item["model_profile"] == profile for item in self.items):
            raise WorkflowError("已有同名风格预设")

    def _capture(self, payload: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
        raw = payload.get("settings", {})
        if not isinstance(raw, dict):
            raise WorkflowError("风格预设 settings 必须是对象")
        profile = _profile(payload.get("model_profile") or raw.get("model_profile")
                           or (previous or {}).get("model_profile", DEFAULT_MODEL_PROFILE))
        if "model_profile" in raw and _profile(raw["model_profile"]) != profile:
            raise WorkflowError("风格预设模型档案不一致")
        flags = normalize_include(payload.get("include", (previous or {}).get("include")))
        origin = normalize_source(payload.get("source"), raw)
        captured = preset_settings({**raw, "model_profile": profile}, self.prompt_rules, include=flags, source=origin)
        return {**payload, "model_profile": profile, "settings": captured, "include": flags, "source": origin}

    @_locked
    async def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) - _ITEM_KEYS:
            raise WorkflowError("风格预设包含未知字段或不是对象")
        if len(self.items) >= MAX_PRESETS:
            raise WorkflowError(f"风格预设不能超过 {MAX_PRESETS} 个")
        captured = self._capture(payload)
        self._ensure_unique_name(payload.get("name"), model_profile=captured["model_profile"])
        now = self._now()
        item = self._normalize_item({**captured, "id": f"preset_{uuid.uuid4().hex[:16]}",
                                     "created_at": now, "updated_at": now, "favorite_at": None})
        if item["groups"]:
            item["favorite"] = True
        self._set_mutation_times(item, None, now)
        self.items.append(item)
        await self._save()
        return copy.deepcopy(item)

    @_locked
    async def update(self, preset_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) - _ITEM_KEYS:
            raise WorkflowError("风格预设包含未知字段或不是对象")
        current = self.get(preset_id)
        for envelope in (payload, payload.get("settings")):
            if isinstance(envelope, dict) and "model_profile" in envelope and _profile(envelope["model_profile"]) != current["model_profile"]:
                raise WorkflowError("不能更改已有风格预设的模型档案，请在目标档案另存新预设")
        changes = {**payload, "model_profile": current["model_profile"]}
        if "settings" in payload:
            changes = self._capture(changes, current)
        else:
            if "source" in payload and payload["source"] != current["source"]:
                raise WorkflowError("修改预设来源需要提供当前 settings")
            flags = normalize_include(payload.get("include", current["include"]))
            if any(flags[key] and not current["include"][key] for key in flags):
                raise WorkflowError("新增携带项需要提供当前 settings")
        merged = {**current, **changes, "id": preset_id, "created_at": current["created_at"],
                  "updated_at": current["updated_at"], "favorite_at": current["favorite_at"]}
        self._ensure_unique_name(merged["name"], excluding_id=preset_id, model_profile=current["model_profile"])
        item = self._normalize_item(merged)
        if "groups" in payload and set(item["groups"]) - set(current["groups"]):
            item["favorite"] = True
        self._set_mutation_times(item, current, self._now())
        self.items[next(index for index, entry in enumerate(self.items) if entry["id"] == preset_id)] = item
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

    def _unique_group(self, group: dict[str, str], excluding_id: str | None = None) -> None:
        if any(entry["id"] != excluding_id and entry["model_profile"] == group["model_profile"]
               and entry["name"].casefold() == group["name"].casefold() for entry in self.groups):
            raise WorkflowError("已有同名预设分组")

    @_locked
    async def create_group(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise WorkflowError("预设分组必须是对象")
        if len(self.groups) >= MAX_GROUPS:
            raise WorkflowError(f"预设分组不能超过 {MAX_GROUPS} 个")
        group = self._normalize_group({**payload, "id": f"preset_group_{uuid.uuid4().hex[:16]}"})
        self._unique_group(group)
        self.groups.append(group)
        await self._save()
        return copy.deepcopy(group)

    @_locked
    async def update_group(self, group_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise WorkflowError("预设分组必须是对象")
        index = next((index for index, group in enumerate(self.groups) if group["id"] == group_id), None)
        if index is None:
            raise KeyError(group_id)
        old = self.groups[index]
        if "model_profile" in payload and _profile(payload["model_profile"]) != old["model_profile"]:
            raise WorkflowError("不能跨模型档案修改预设分组")
        group = self._normalize_group({**old, **payload, "id": group_id})
        self._unique_group(group, group_id)
        self.groups[index] = group
        await self._save()
        return copy.deepcopy(group)

    @_locked
    async def delete_group(self, group_id: str, model_profile: str) -> bool:
        profile = _profile(model_profile)
        group = next((group for group in self.groups if group["id"] == group_id), None)
        if group is None:
            return False
        if group["model_profile"] != profile:
            raise WorkflowError("不能跨模型档案删除预设分组")
        self.groups = [group for group in self.groups if group["id"] != group_id]
        for item in self.items:
            if group_id in item["groups"]:
                item["groups"].remove(group_id)
                item["updated_at"] = self._now()
        await self._save()
        return True

    @_locked
    async def batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != {"model_profile", "ids", "action", "value"}:
            raise WorkflowError("预设批量整理需要 model_profile、ids、action、value")
        profile = _profile(payload["model_profile"])
        ids = _strings(payload["ids"], "预设 ID", MAX_PRESETS)
        if not ids:
            raise WorkflowError("请先选择预设")
        selected = [self.get(preset_id, profile) for preset_id in ids]
        action, value = payload["action"], payload["value"]
        if not isinstance(action, str):
            raise WorkflowError("不支持的预设批量操作")
        if action == "favorite":
            if not isinstance(value, bool):
                raise WorkflowError("收藏状态必须是布尔值")
        elif action in {"groups", "tags"}:
            if (not isinstance(value, dict) or set(value) != {"mode", "values"}
                    or not isinstance(value["mode"], str) or value["mode"] not in {"add", "remove", "replace"}):
                raise WorkflowError("批量分组/标签需要 mode 和 values")
            values = (self._group_ids(value["values"], profile) if action == "groups"
                      else _strings(value["values"], "预设标签", MAX_TAGS, fold=True))
        else:
            raise WorkflowError("不支持的预设批量操作")
        now = self._now()
        for item in selected:
            previous = copy.deepcopy(item)
            if action == "favorite":
                item["favorite"] = value
            else:
                old = item[action]
                identity = (lambda entry: entry.casefold()) if action == "tags" else (lambda entry: entry)
                if value["mode"] == "replace":
                    combined = list(values)
                elif value["mode"] == "remove":
                    removed = {identity(entry) for entry in values}
                    combined = [entry for entry in old if identity(entry) not in removed]
                else:
                    combined = list(old)
                    existing = {identity(entry) for entry in old}
                    combined.extend(entry for entry in values if identity(entry) not in existing)
                item[action] = (_strings(combined, "预设标签", MAX_TAGS, fold=True) if action == "tags"
                                else self._group_ids(combined, profile))
                if action == "groups" and set(item[action]) - set(old):
                    item["favorite"] = True
            self._set_mutation_times(item, previous, now)
        changes = {item["id"]: item for item in selected}
        self.items = [changes.get(item["id"], item) for item in self.items]
        await self._save()
        return self.list(profile)

    def _serialized(self) -> str:
        return json.dumps({"version": STORE_VERSION, "items": self.items, "groups": self.groups,
                           "cover_references_complete": self.cover_references_complete},
                          ensure_ascii=False, indent=2) + "\n"

    async def _save(self) -> None:
        await asyncio.to_thread(self._write_text, self._serialized())

    def _write_text(self, text: str) -> None:
        self.ensure_writable()
        descriptor, temporary = tempfile.mkstemp(prefix="style-presets-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
