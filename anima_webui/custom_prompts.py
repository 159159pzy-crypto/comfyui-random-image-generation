from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .catalog import SECTIONS, CatalogError, PromptCatalog


class CustomPromptStore:
    def __init__(self, path: str | Path, catalog: PromptCatalog):
        self.path = Path(path)
        self.catalog = catalog
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.items: list[dict[str, Any]] = []
        self.reload()

    def reload(self) -> None:
        if not self.path.is_file():
            self.items = []
            self.catalog.set_custom_items([])
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CatalogError(f"自定义提示词文件无法读取: {self.path.name}") from error
        raw_items = payload.get("items", []) if isinstance(payload, dict) else []
        self.items = [self._normalize(item) for item in raw_items if isinstance(item, dict)]
        self.catalog.set_custom_items(self.items)

    def list(self, section: str | None = None) -> list[dict[str, Any]]:
        if section and section not in SECTIONS:
            raise CatalogError(f"不支持的随机池: {section}")
        values = self.items if not section else [item for item in self.items if item["section"] == section]
        return [dict(item) for item in values]

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        item = self._normalize({**payload, "id": f"custom:{uuid.uuid4().hex}"})
        self.items.append(item)
        self._save()
        self.catalog.set_custom_items(self.items)
        return dict(item)

    def update(self, item_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        index = next((index for index, item in enumerate(self.items) if item["id"] == item_id), None)
        if index is None:
            raise KeyError(item_id)
        item = self._normalize({**self.items[index], **payload, "id": item_id})
        self.items[index] = item
        self._save()
        self.catalog.set_custom_items(self.items)
        return dict(item)

    def delete(self, item_id: str) -> bool:
        old_length = len(self.items)
        self.items = [item for item in self.items if item["id"] != item_id]
        if len(self.items) == old_length:
            return False
        self._save()
        self.catalog.set_custom_items(self.items)
        return True

    def _normalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        section = str(payload.get("section") or "")
        if section not in SECTIONS:
            raise CatalogError("自定义项分类无效")
        title = str(payload.get("title") or "").strip()
        prompt = str(payload.get("prompt") or "").strip()
        subtitle = str(payload.get("subtitle") or "").strip()
        if not title or not prompt:
            raise CatalogError("自定义项需要名称和提示词")
        if len(title) > 200 or len(prompt) > 20000 or len(subtitle) > 300:
            raise CatalogError("自定义项内容过长")
        gender = str(payload.get("gender") or "unknown").strip().lower()
        if section != "character":
            gender = "unknown"
        gender = {"female": "1girl", "male": "1boy"}.get(gender, gender)
        if gender not in {"1girl", "1boy", "unknown"}:
            raise CatalogError("角色性别只能是 1girl、1boy 或 unknown")
        categories = payload.get("categories") or []
        traits = payload.get("traits") or []
        if not isinstance(categories, list) or not isinstance(traits, list):
            raise CatalogError("自定义项 categories 和 traits 必须是数组")
        categories = list(dict.fromkeys(str(value).strip() for value in categories if str(value).strip()))
        traits = list(dict.fromkeys(str(value).strip() for value in traits if str(value).strip()))
        if len(categories) > 32 or len(traits) > 128:
            raise CatalogError("自定义项分类或特征过多")
        return {
            "id": str(payload.get("id") or ""),
            "section": section,
            "title": title,
            "subtitle": subtitle,
            "prompt": prompt,
            "gender": gender,
            "hair": str(payload.get("hair") or "").strip().lower() if section == "character" else "",
            "eye": str(payload.get("eye") or "").strip().lower() if section == "character" else "",
            "copyright": str(payload.get("copyright") or "").strip() if section == "character" else "",
            "categories": categories,
            "traits": traits,
        }

    def _save(self) -> None:
        payload = {"version": 2, "items": self.items}
        fd, temp_name = tempfile.mkstemp(prefix="custom-prompts-", suffix=".json", dir=self.path.parent)
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
