"""Named, secret-free environment profiles for the Comfy Anima plugin.

The AstrBot plugin configuration mixes three different concerns:

* connection and workflow details that change with a ComfyUI installation;
* global behaviour such as permissions and prompt-director settings;
* credentials such as the ComfyUI bearer token and Web UI password.

Only the first group belongs in an ordinary environment profile.  This module
therefore uses an explicit field allow-list instead of trying to remove known
secret names.  New plugin settings remain excluded until deliberately added
to :data:`ENVIRONMENT_FIELD_DEFAULTS`.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import unicodedata
from collections.abc import Callable, Mapping, MutableMapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..constants import (
    DEFAULT_NEGATIVE_NODE_ID,
    DEFAULT_PREVIEW_OUTPUT_NODE_ID,
    DEFAULT_PRIMARY_SAMPLER_NODE_ID,
    DEFAULT_PRIMARY_SEED_NODE_ID,
    DEFAULT_PROMPT_NODE_ID,
    DEFAULT_RESOLUTION_NODE_ID,
    DEFAULT_SECONDARY_SEED_NODE_ID,
    DEFAULT_UPSCALE_OUTPUT_NODE_ID,
    DEFAULT_WORKFLOW_FILE,
    MAX_IMAGE_SIDE,
    MIN_IMAGE_SIDE,
)


PROFILE_SCHEMA = "astrbot-comfy-anima-environment-profile"
PROFILE_SCHEMA_VERSION = 2
LEGACY_PROFILE_SCHEMA_VERSION = 1
MAX_PROFILE_NAME_LENGTH = 64
MAX_TEXT_FIELD_LENGTH = 2048
MAX_NODE_LIST_ITEMS = 32

# Deliberately narrow.  In particular, api_token, web_ui_*, prompt_llm_*,
# provider IDs, prompts, permissions and moderation settings are absent.
ENVIRONMENT_FIELD_DEFAULTS: dict[str, Any] = {
    "comfyui_url": "http://127.0.0.1:8188",
    "workflow_file": DEFAULT_WORKFLOW_FILE,
    "upscale_workflow_file": "workflow/rtx_upscale_api.json",
    "base_workflow_file": "workflow/anima_base_api.json",
    "rtx_generation_workflow_file": "workflow/anima_rtx_api.json",
    "iterative_workflow_file": "workflow/anima_iterative_api.json",
    "inpaint_crop_workflow_file": "workflow/anima_inpaint_crop_api.json",
    "lanpaint_workflow_file": "workflow/anima_lanpaint_api.json",
    "workflow_dir": "workflow",
    "unet_catalog_url": "",
    "unet_loader_node_id": "429",
    "unet_model_input_name": "unet_name",
    "unet_model_name": "",
    "lora_catalog_url": "",
    "lora_manager_url": "",
    "lora_loader_node_id": "462",
    "prompt_node_id": DEFAULT_PROMPT_NODE_ID,
    "negative_node_id": DEFAULT_NEGATIVE_NODE_ID,
    "primary_seed_node_id": DEFAULT_PRIMARY_SEED_NODE_ID,
    "secondary_seed_node_id": DEFAULT_SECONDARY_SEED_NODE_ID,
    "resolution_node_id": DEFAULT_RESOLUTION_NODE_ID,
    "sampler_node_ids": [DEFAULT_PRIMARY_SAMPLER_NODE_ID],
    "output_node_ids": [
        DEFAULT_UPSCALE_OUTPUT_NODE_ID,
        DEFAULT_PREVIEW_OUTPUT_NODE_ID,
    ],
    "upscale_output_node_id": DEFAULT_UPSCALE_OUTPUT_NODE_ID,
    "default_width": 832,
    "default_height": 1216,
}
ENVIRONMENT_FIELDS = frozenset(ENVIRONMENT_FIELD_DEFAULTS)
_V1_ADDED_WORKFLOW_FIELDS = frozenset(
    {
        "upscale_workflow_file",
        "base_workflow_file",
        "rtx_generation_workflow_file",
        "iterative_workflow_file",
        "inpaint_crop_workflow_file",
        "lanpaint_workflow_file",
    }
)

_URL_FIELDS = frozenset(
    {
        "comfyui_url",
        "unet_catalog_url",
        "lora_catalog_url",
        "lora_manager_url",
    }
)
_OPTIONAL_URL_FIELDS = _URL_FIELDS - {"comfyui_url"}
_PATH_FIELDS = frozenset(
    {
        "workflow_file",
        "upscale_workflow_file",
        "base_workflow_file",
        "rtx_generation_workflow_file",
        "iterative_workflow_file",
        "inpaint_crop_workflow_file",
        "lanpaint_workflow_file",
        "workflow_dir",
    }
)
_NODE_FIELDS = frozenset(
    {
        "unet_loader_node_id",
        "lora_loader_node_id",
        "prompt_node_id",
        "negative_node_id",
        "primary_seed_node_id",
        "secondary_seed_node_id",
        "resolution_node_id",
        "upscale_output_node_id",
    }
)
_FREE_TEXT_FIELDS = frozenset({"unet_model_input_name", "unet_model_name"})
_NODE_LIST_FIELDS = frozenset({"sampler_node_ids", "output_node_ids"})
_IMAGE_SIDE_FIELDS = frozenset({"default_width", "default_height"})
_UNSAFE_PROFILE_NAME_CHARS = frozenset('<>:"/\\|?*')


class ConfigProfileError(Exception):
    """Base error for profile operations."""


class ConfigProfileValidationError(ConfigProfileError):
    """A name, field or profile payload is invalid."""


class ConfigProfileNotFoundError(ConfigProfileError):
    """The requested profile does not exist."""


class ConfigProfileConflictError(ConfigProfileError):
    """A profile already exists and overwrite was not requested."""


class ConfigProfileStorageError(ConfigProfileError):
    """The profile store could not be read or atomically updated."""


class ConfigProfileApplyError(ConfigProfileError):
    """A validated profile could not be atomically applied."""


PersistUpdates = Callable[[dict[str, Any]], bool | None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _copy(value: Any) -> Any:
    return copy.deepcopy(value)


def _contains_control_characters(value: str) -> bool:
    return any(unicodedata.category(character).startswith("C") for character in value)


def normalize_profile_name(name: Any) -> str:
    """Return a safe display name suitable for a JSON profile key."""

    normalized = unicodedata.normalize("NFC", str(name or "")).strip()
    if not normalized:
        raise ConfigProfileValidationError("配置档案名称不能为空")
    if len(normalized) > MAX_PROFILE_NAME_LENGTH:
        raise ConfigProfileValidationError(
            f"配置档案名称不能超过 {MAX_PROFILE_NAME_LENGTH} 个字符"
        )
    if normalized in {".", ".."}:
        raise ConfigProfileValidationError("配置档案名称无效")
    if _contains_control_characters(normalized):
        raise ConfigProfileValidationError("配置档案名称不能包含控制字符")
    if any(character in _UNSAFE_PROFILE_NAME_CHARS for character in normalized):
        raise ConfigProfileValidationError(
            "配置档案名称不能包含 < > : \" / \\ | ? *"
        )
    return normalized


def _profile_id(name: str) -> str:
    # NFKC prevents visually equivalent full-width names from producing two
    # entries, while the NFC display name is still preserved in the record.
    return unicodedata.normalize("NFKC", name).casefold()


def _validate_text(value: Any, field: str, *, allow_empty: bool) -> str:
    if not isinstance(value, str):
        raise ConfigProfileValidationError(f"{field} 必须是字符串")
    normalized = value.strip()
    if not normalized and not allow_empty:
        raise ConfigProfileValidationError(f"{field} 不能为空")
    if len(normalized) > MAX_TEXT_FIELD_LENGTH:
        raise ConfigProfileValidationError(f"{field} 内容过长")
    if "\x00" in normalized or _contains_control_characters(normalized):
        raise ConfigProfileValidationError(f"{field} 不能包含控制字符")
    return normalized


def _validate_url(value: Any, field: str) -> str:
    normalized = _validate_text(
        value,
        field,
        allow_empty=field in _OPTIONAL_URL_FIELDS,
    )
    if not normalized:
        return ""
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigProfileValidationError(f"{field} 必须是有效的 HTTP(S) 地址")
    if parsed.username is not None or parsed.password is not None:
        raise ConfigProfileValidationError(f"{field} 不能包含账号或密码")
    if parsed.query or parsed.fragment:
        raise ConfigProfileValidationError(
            f"{field} 不能包含查询参数或片段，以免把凭据写入普通档案"
        )
    try:
        port = parsed.port
    except ValueError as exc:
        raise ConfigProfileValidationError(f"{field} 端口无效") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ConfigProfileValidationError(f"{field} 端口无效")
    return normalized.rstrip("/") if parsed.path in {"", "/"} else normalized


def _validate_node_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise ConfigProfileValidationError(f"{field} 必须是节点 ID 列表")
    if not value:
        raise ConfigProfileValidationError(f"{field} 不能为空")
    if len(value) > MAX_NODE_LIST_ITEMS:
        raise ConfigProfileValidationError(
            f"{field} 最多允许 {MAX_NODE_LIST_ITEMS} 个节点"
        )
    result: list[str] = []
    seen: set[str] = set()
    for raw_item in value:
        item = _validate_text(raw_item, field, allow_empty=False)
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def validate_environment_settings(
    settings: Mapping[str, Any],
    *,
    require_all: bool = True,
) -> dict[str, Any]:
    """Validate and normalize allow-listed environment settings.

    Unknown fields are rejected rather than ignored.  This is important for
    imported profile data: a future or malicious payload cannot smuggle a
    credential into the profile store under an arbitrary key.
    """

    if not isinstance(settings, Mapping):
        raise ConfigProfileValidationError("配置档案 settings 必须是对象")
    unknown = set(settings) - ENVIRONMENT_FIELDS
    if unknown:
        names = ", ".join(sorted(str(key) for key in unknown))
        raise ConfigProfileValidationError(f"配置档案包含不允许的字段：{names}")
    if require_all:
        missing = ENVIRONMENT_FIELDS - set(settings)
        if missing:
            names = ", ".join(sorted(missing))
            raise ConfigProfileValidationError(f"配置档案缺少字段：{names}")

    normalized: dict[str, Any] = {}
    for field, value in settings.items():
        if field in _URL_FIELDS:
            normalized[field] = _validate_url(value, field)
        elif field in _PATH_FIELDS:
            normalized[field] = _validate_text(value, field, allow_empty=False)
        elif field in _NODE_FIELDS:
            normalized[field] = _validate_text(value, field, allow_empty=False)
        elif field in _FREE_TEXT_FIELDS:
            normalized[field] = _validate_text(
                value,
                field,
                allow_empty=field == "unet_model_name",
            )
        elif field in _NODE_LIST_FIELDS:
            normalized[field] = _validate_node_list(value, field)
        elif field in _IMAGE_SIDE_FIELDS:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigProfileValidationError(f"{field} 必须是整数")
            if not MIN_IMAGE_SIDE <= value <= MAX_IMAGE_SIDE:
                raise ConfigProfileValidationError(
                    f"{field} 必须在 {MIN_IMAGE_SIDE} 到 {MAX_IMAGE_SIDE} 之间"
                )
            normalized[field] = value
        else:  # pragma: no cover - guarded by the field allow-list above.
            raise ConfigProfileValidationError(f"不支持的环境字段：{field}")
    return normalized


def extract_environment_settings(config: Mapping[str, Any]) -> dict[str, Any]:
    """Extract a complete, secret-free environment snapshot from config."""

    if not isinstance(config, Mapping):
        raise ConfigProfileValidationError("AstrBot 插件配置必须是字典兼容对象")
    raw = {
        field: _copy(config.get(field, default))
        for field, default in ENVIRONMENT_FIELD_DEFAULTS.items()
    }
    return validate_environment_settings(raw)


def _validate_stored_environment_settings(
    settings: Mapping[str, Any],
    *,
    schema_version: int,
) -> dict[str, Any]:
    """Validate a stored snapshot and migrate the known v1 workflow gap."""

    if schema_version == PROFILE_SCHEMA_VERSION:
        return validate_environment_settings(settings)
    if schema_version != LEGACY_PROFILE_SCHEMA_VERSION:
        raise ConfigProfileValidationError("version 不受支持")
    partial = validate_environment_settings(settings, require_all=False)
    missing = ENVIRONMENT_FIELDS - set(partial)
    unsupported_missing = missing - _V1_ADDED_WORKFLOW_FIELDS
    if unsupported_missing:
        names = ", ".join(sorted(unsupported_missing))
        raise ConfigProfileValidationError(f"配置档案缺少字段：{names}")
    completed = {
        field: _copy(partial.get(field, default))
        for field, default in ENVIRONMENT_FIELD_DEFAULTS.items()
    }
    return validate_environment_settings(completed)


class ConfigProfileService:
    """Persist and atomically activate named environment profiles."""

    def __init__(self, storage_path: str | Path):
        self.storage_path = Path(storage_path)
        self._lock = threading.RLock()

    def list_profiles(self) -> list[dict[str, Any]]:
        with self._lock:
            state = self._read_state()
            active_id = state["active_profile"]
            profiles = [
                self._public_profile(record, profile_id == active_id)
                for profile_id, record in state["profiles"].items()
            ]
        profiles.sort(key=lambda item: (item["name"].casefold(), item["name"]))
        return profiles

    def get_profile(self, name: Any) -> dict[str, Any]:
        normalized_name = normalize_profile_name(name)
        with self._lock:
            state = self._read_state()
            profile_id, record = self._find_profile(state, normalized_name)
            return self._public_profile(
                record,
                profile_id == state["active_profile"],
            )

    def save_profile(
        self,
        name: Any,
        config: Mapping[str, Any],
        *,
        overwrite: bool = False,
        activate: bool = False,
    ) -> dict[str, Any]:
        normalized_name = normalize_profile_name(name)
        profile_id = _profile_id(normalized_name)
        settings = extract_environment_settings(config)
        now = _utc_now()
        with self._lock:
            state = self._read_state()
            previous = state["profiles"].get(profile_id)
            if previous is not None and not overwrite:
                raise ConfigProfileConflictError(
                    f"配置档案“{previous['name']}”已存在；覆盖时请显式启用 overwrite"
                )
            record = {
                "name": normalized_name,
                "created_at": previous["created_at"] if previous else now,
                "updated_at": now,
                "settings": settings,
            }
            state["profiles"][profile_id] = record
            if activate:
                state["active_profile"] = profile_id
            self._write_state(state)
            return self._public_profile(
                record,
                state["active_profile"] == profile_id,
            )

    def delete_profile(self, name: Any) -> dict[str, Any]:
        normalized_name = normalize_profile_name(name)
        with self._lock:
            state = self._read_state()
            profile_id, record = self._find_profile(state, normalized_name)
            was_active = state["active_profile"] == profile_id
            del state["profiles"][profile_id]
            if was_active:
                state["active_profile"] = ""
            self._write_state(state)
            return self._public_profile(record, was_active)

    def activate_profile(
        self,
        name: Any,
        config: MutableMapping[str, Any],
        *,
        persist_updates: PersistUpdates | None = None,
    ) -> dict[str, Any]:
        """Validate, persist and activate a profile as one logical change.

        ``persist_updates`` is intended for ``plugin._persist_config_updates``.
        It receives the complete allow-listed update and must return ``False``
        on failure.  Without a callback the service updates ``config`` itself
        and calls ``config.save_config()`` when that method exists.

        If updating the active-profile marker fails, the previous environment
        is written back before an error is raised.  Sensitive and non-
        environment keys are never touched.
        """

        if not isinstance(config, MutableMapping):
            raise ConfigProfileValidationError(
                "切换配置档案需要可修改的 AstrBot 插件配置"
            )
        normalized_name = normalize_profile_name(name)
        with self._lock:
            state = self._read_state()
            profile_id, record = self._find_profile(state, normalized_name)
            updates = validate_environment_settings(record["settings"])
            previous_settings = extract_environment_settings(config)
            previous_raw = {
                key: (key in config, _copy(config.get(key)))
                for key in ENVIRONMENT_FIELDS
            }
            previous_active = state["active_profile"]

            self._apply_updates(config, updates, persist_updates)
            state["active_profile"] = profile_id
            try:
                self._write_state(state)
            except Exception as store_exc:
                try:
                    if persist_updates is None:
                        for key, (existed, value) in previous_raw.items():
                            if existed:
                                config[key] = value
                            else:
                                config.pop(key, None)
                        save_config = getattr(config, "save_config", None)
                        if callable(save_config):
                            save_config()
                    else:
                        self._apply_updates(config, previous_settings, persist_updates)
                except Exception as rollback_exc:
                    raise ConfigProfileApplyError(
                        "配置已应用，但活动档案状态保存失败，且配置回滚失败："
                        f"{rollback_exc}"
                    ) from store_exc
                state["active_profile"] = previous_active
                raise ConfigProfileApplyError(
                    "活动档案状态保存失败，配置修改已回滚"
                ) from store_exc
            return self._public_profile(record, True)

    def switch_profile(
        self,
        name: Any,
        config: MutableMapping[str, Any],
        *,
        persist_updates: PersistUpdates | None = None,
    ) -> dict[str, Any]:
        """Alias with user-facing terminology for :meth:`activate_profile`."""

        return self.activate_profile(
            name,
            config,
            persist_updates=persist_updates,
        )

    def export_profile(self, name: Any) -> dict[str, Any]:
        """Return a portable profile envelope containing no plugin secrets."""

        profile = self.get_profile(name)
        profile.pop("active", None)
        return {
            "schema": PROFILE_SCHEMA,
            "version": PROFILE_SCHEMA_VERSION,
            "profile": profile,
        }

    def import_profile(
        self,
        payload: Mapping[str, Any],
        *,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Validate and save a portable profile envelope."""

        if not isinstance(payload, Mapping):
            raise ConfigProfileValidationError("导入内容必须是对象")
        if payload.get("schema") != PROFILE_SCHEMA:
            raise ConfigProfileValidationError("不是 Comfy Anima 环境配置档案")
        raw_version = payload.get("version")
        if raw_version not in {
            LEGACY_PROFILE_SCHEMA_VERSION,
            PROFILE_SCHEMA_VERSION,
        }:
            raise ConfigProfileValidationError("不支持的配置档案版本")
        raw_profile = payload.get("profile")
        if not isinstance(raw_profile, Mapping):
            raise ConfigProfileValidationError("导入内容缺少 profile 对象")
        allowed_profile_fields = {"name", "created_at", "updated_at", "settings"}
        unknown = set(raw_profile) - allowed_profile_fields
        if unknown:
            names = ", ".join(sorted(str(key) for key in unknown))
            raise ConfigProfileValidationError(f"profile 包含不允许的字段：{names}")
        name = normalize_profile_name(raw_profile.get("name"))
        settings = _validate_stored_environment_settings(
            raw_profile.get("settings", {}),
            schema_version=int(raw_version),
        )
        profile_id = _profile_id(name)
        now = _utc_now()
        with self._lock:
            state = self._read_state()
            previous = state["profiles"].get(profile_id)
            if previous is not None and not overwrite:
                raise ConfigProfileConflictError(
                    f"配置档案“{previous['name']}”已存在；覆盖时请显式启用 overwrite"
                )
            record = {
                "name": name,
                "created_at": previous["created_at"] if previous else now,
                "updated_at": now,
                "settings": settings,
            }
            state["profiles"][profile_id] = record
            self._write_state(state)
            return self._public_profile(record, state["active_profile"] == profile_id)

    def _apply_updates(
        self,
        config: MutableMapping[str, Any],
        updates: Mapping[str, Any],
        persist_updates: PersistUpdates | None,
    ) -> None:
        payload = _copy(dict(updates))
        if persist_updates is not None:
            try:
                result = persist_updates(payload)
            except Exception as exc:
                raise ConfigProfileApplyError(f"配置档案保存失败：{exc}") from exc
            if result is False:
                raise ConfigProfileApplyError("配置档案保存失败，修改未生效")
            return

        previous = {
            key: (key in config, _copy(config.get(key)))
            for key in payload
        }
        try:
            config.update(payload)
            save_config = getattr(config, "save_config", None)
            if callable(save_config):
                save_config()
        except Exception as exc:
            for key, (existed, value) in previous.items():
                try:
                    if existed:
                        config[key] = value
                    else:
                        config.pop(key, None)
                except Exception:
                    pass
            raise ConfigProfileApplyError(
                f"配置档案保存失败，修改已回滚：{exc}"
            ) from exc

    @staticmethod
    def _public_profile(record: Mapping[str, Any], active: bool) -> dict[str, Any]:
        return {
            "name": str(record["name"]),
            "active": bool(active),
            "created_at": str(record["created_at"]),
            "updated_at": str(record["updated_at"]),
            "settings": _copy(record["settings"]),
        }

    @staticmethod
    def _find_profile(
        state: Mapping[str, Any], name: str
    ) -> tuple[str, dict[str, Any]]:
        profile_id = _profile_id(name)
        record = state["profiles"].get(profile_id)
        if record is None:
            raise ConfigProfileNotFoundError(f"配置档案“{name}”不存在")
        return profile_id, record

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "schema": PROFILE_SCHEMA,
            "version": PROFILE_SCHEMA_VERSION,
            "active_profile": "",
            "profiles": {},
        }

    def _read_state(self) -> dict[str, Any]:
        if not self.storage_path.exists():
            return self._empty_state()
        try:
            with self.storage_path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ConfigProfileStorageError(
                f"无法读取配置档案文件 {self.storage_path}: {exc}"
            ) from exc
        try:
            validated = self._validate_state(raw)
        except ConfigProfileValidationError as exc:
            raise ConfigProfileStorageError(f"配置档案文件损坏：{exc}") from exc
        if validated != raw:
            try:
                self._write_state(validated)
            except ConfigProfileStorageError:
                # Migration must not make existing profiles disappear merely
                # because a read-only filesystem prevents the optional rewrite.
                pass
        return validated

    def _validate_state(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise ConfigProfileValidationError("根节点必须是对象")
        if raw.get("schema") != PROFILE_SCHEMA:
            raise ConfigProfileValidationError("schema 不匹配")
        raw_version = raw.get("version")
        if raw_version not in {
            LEGACY_PROFILE_SCHEMA_VERSION,
            PROFILE_SCHEMA_VERSION,
        }:
            raise ConfigProfileValidationError("version 不受支持")
        raw_profiles = raw.get("profiles")
        if not isinstance(raw_profiles, Mapping):
            raise ConfigProfileValidationError("profiles 必须是对象")
        profiles: dict[str, dict[str, Any]] = {}
        for stored_id, raw_record in raw_profiles.items():
            if not isinstance(stored_id, str) or not isinstance(raw_record, Mapping):
                raise ConfigProfileValidationError("profile 记录格式无效")
            name = normalize_profile_name(raw_record.get("name"))
            canonical_id = _profile_id(name)
            if stored_id != canonical_id:
                raise ConfigProfileValidationError(f"档案“{name}”的索引无效")
            if canonical_id in profiles:
                raise ConfigProfileValidationError(f"档案“{name}”重复")
            created_at = raw_record.get("created_at")
            updated_at = raw_record.get("updated_at")
            if not isinstance(created_at, str) or not isinstance(updated_at, str):
                raise ConfigProfileValidationError(f"档案“{name}”时间字段无效")
            settings = _validate_stored_environment_settings(
                raw_record.get("settings", {}),
                schema_version=int(raw_version),
            )
            profiles[canonical_id] = {
                "name": name,
                "created_at": created_at,
                "updated_at": updated_at,
                "settings": settings,
            }
        active_profile = raw.get("active_profile", "")
        if not isinstance(active_profile, str):
            raise ConfigProfileValidationError("active_profile 必须是字符串")
        if active_profile and active_profile not in profiles:
            raise ConfigProfileValidationError("active_profile 指向不存在的档案")
        return {
            "schema": PROFILE_SCHEMA,
            "version": PROFILE_SCHEMA_VERSION,
            "active_profile": active_profile,
            "profiles": profiles,
        }

    def _write_state(self, state: Mapping[str, Any]) -> None:
        validated = self._validate_state(state)
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigProfileStorageError(
                f"无法创建配置档案目录 {self.storage_path.parent}: {exc}"
            ) from exc

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                prefix=f".{self.storage_path.name}.",
                suffix=".tmp",
                dir=self.storage_path.parent,
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                json.dump(
                    validated,
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.storage_path)
            temporary_path = None
            try:
                os.chmod(self.storage_path, 0o600)
            except OSError:
                # Windows ACLs and some mounted filesystems do not implement
                # POSIX modes.  The profile is secret-free, so this is best-effort.
                pass
        except (OSError, TypeError, ValueError) as exc:
            raise ConfigProfileStorageError(
                f"无法原子保存配置档案文件 {self.storage_path}: {exc}"
            ) from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass


__all__ = [
    "ENVIRONMENT_FIELDS",
    "ENVIRONMENT_FIELD_DEFAULTS",
    "PROFILE_SCHEMA",
    "PROFILE_SCHEMA_VERSION",
    "ConfigProfileApplyError",
    "ConfigProfileConflictError",
    "ConfigProfileError",
    "ConfigProfileNotFoundError",
    "ConfigProfileService",
    "ConfigProfileStorageError",
    "ConfigProfileValidationError",
    "extract_environment_settings",
    "normalize_profile_name",
    "validate_environment_settings",
]
