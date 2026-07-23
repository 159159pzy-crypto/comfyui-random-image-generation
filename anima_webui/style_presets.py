from __future__ import annotations

import copy
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .workflow import DEFAULT_SETTINGS, WorkflowError, validate_settings


PRESET_SETTING_KEYS = (
    "model_name",
    "loras",
    "hires",
    "detailers",
    "manual_artist",
    "quality_prompt",
    "extra_prompt",
    "negative_prompt",
    "width",
    "height",
    "steps",
    "cfg",
)
MAX_PRESETS = 256


def preset_settings(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowError("风格预设 settings 必须是对象")
    unknown = set(value) - set(PRESET_SETTING_KEYS)
    if unknown:
        raise WorkflowError(f"风格预设包含未知参数: {', '.join(sorted(unknown))}")
    candidate = copy.deepcopy(DEFAULT_SETTINGS)
    candidate.update(value)
    normalized = validate_settings(candidate)
    return {key: copy.deepcopy(normalized[key]) for key in PRESET_SETTING_KEYS}


class StylePresetStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.items: list[dict[str, Any]] = []
        self.reload()

    def reload(self) -> None:
        if not self.path.is_file():
            self.items = []
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise WorkflowError(f"风格预设文件无法读取: {self.path.name}") from error
        values = payload.get("items", []) if isinstance(payload, dict) else []
        self.items = [self._normalize(item, existing=True) for item in values if isinstance(item, dict)]

    def list(self) -> dict[str, Any]:
        favorites = sorted(
            (item for item in self.items if item["favorite"]),
            key=lambda item: item["updated_at"],
            reverse=True,
        )
        regular = sorted(
            (item for item in self.items if not item["favorite"]),
            key=lambda item: item["updated_at"],
            reverse=True,
        )
        return {"items": copy.deepcopy(favorites + regular), "count": len(self.items)}

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        if len(self.items) >= MAX_PRESETS:
            raise WorkflowError(f"风格预设不能超过 {MAX_PRESETS} 个")
        self._ensure_unique_name(payload.get("name"))
        now = self._now()
        item = self._normalize(
            {
                **payload,
                "id": f"preset_{uuid.uuid4().hex[:16]}",
                "created_at": now,
                "updated_at": now,
            }
        )
        self.items.append(item)
        self._save()
        return copy.deepcopy(item)

    def update(self, preset_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        index = next((index for index, item in enumerate(self.items) if item["id"] == preset_id), None)
        if index is None:
            raise KeyError(preset_id)
        if "name" in payload:
            self._ensure_unique_name(payload.get("name"), excluding_id=preset_id)
        current = self.items[index]
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
        self._save()
        return copy.deepcopy(item)

    def delete(self, preset_id: str) -> bool:
        previous = len(self.items)
        self.items = [item for item in self.items if item["id"] != preset_id]
        if len(self.items) == previous:
            return False
        self._save()
        return True

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _ensure_unique_name(self, value: Any, excluding_id: str | None = None) -> None:
        name = str(value or "").strip().casefold()
        if name and any(
            item["id"] != excluding_id and item["name"].strip().casefold() == name
            for item in self.items
        ):
            raise WorkflowError("已有同名风格预设")

    @staticmethod
    def _normalize(payload: dict[str, Any], existing: bool = False) -> dict[str, Any]:
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
        return {
            "id": preset_id,
            "name": name,
            "favorite": favorite,
            "settings": preset_settings(payload.get("settings")),
            "created_at": created_at,
            "updated_at": updated_at,
        }

    def _save(self) -> None:
        payload = {"version": 1, "items": self.items}
        fd, temp_name = tempfile.mkstemp(prefix="style-presets-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
