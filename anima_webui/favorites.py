from __future__ import annotations

import copy
import uuid
from typing import Any

from .catalog import SECTIONS, CatalogError, PromptCatalog


MAX_GROUPS = 128
MAX_ITEMS = 50000


def _section(value: str) -> str:
    if value not in SECTIONS:
        raise CatalogError(f"不支持的收藏分类: {value}")
    return value


def normalize_section(value: Any) -> dict[str, list[dict[str, Any]]]:
    value = value if isinstance(value, dict) else {}
    groups = value.get("groups") if isinstance(value.get("groups"), list) else []
    items = value.get("items") if isinstance(value.get("items"), list) else []
    normalized_groups: list[dict[str, Any]] = []
    seen_groups: set[str] = set()
    for raw in groups[:MAX_GROUPS]:
        if not isinstance(raw, dict):
            continue
        group_id = str(raw.get("id") or "").strip()
        name = str(raw.get("name") or "").strip()
        if not group_id or not name or group_id in seen_groups:
            continue
        seen_groups.add(group_id)
        normalized_groups.append({"id": group_id, "name": name[:100], "isSystem": group_id == "default"})
    if "default" not in seen_groups:
        normalized_groups.insert(0, {"id": "default", "name": "Default Favorites", "isSystem": True})
    normalized_items = [copy.deepcopy(item) for item in items[:MAX_ITEMS] if isinstance(item, dict)]
    return {"groups": normalized_groups, "items": normalized_items}


def favorite_key(section: str, item: dict[str, Any]) -> str:
    if section == "character":
        return str(item.get("name") or item.get("id") or "")
    return str(item.get("id") or item.get("name") or "")


class FavoritesService:
    def __init__(self, comfy: Any, catalog: PromptCatalog):
        self.comfy = comfy
        self.catalog = catalog

    async def get(self, section: str) -> dict[str, Any]:
        section = _section(section)
        payload = await self.comfy.favorites()
        result = normalize_section(payload.get(section))
        return {
            **result,
            "favorite_keys": sorted(
                favorite_key(section, item) for item in result["items"] if item.get("groupIds")
            ),
        }

    async def update_item(self, section: str, payload: dict[str, Any]) -> dict[str, Any]:
        section = _section(section)
        item_id = str(payload.get("id") or "").strip()
        catalog_item = self.catalog.get(item_id)
        if catalog_item is None or catalog_item.get("section") != section:
            raise CatalogError("收藏条目不存在")
        data = await self.comfy.favorites()
        current = normalize_section(data.get(section))
        key = str(catalog_item.get("favorite_key") or "")
        position = next(
            (index for index, item in enumerate(current["items"]) if favorite_key(section, item) == key),
            None,
        )
        enabled = payload.get("favorite", True)
        if not isinstance(enabled, bool):
            raise CatalogError("favorite 必须是布尔值")
        groups = payload.get("groupIds")
        valid_group_ids = {group["id"] for group in current["groups"]}
        if groups is None:
            groups = ["default"] if enabled else []
        if not isinstance(groups, list):
            raise CatalogError("groupIds 必须是数组")
        groups = list(dict.fromkeys(str(value) for value in groups if str(value) in valid_group_ids))
        if enabled and not groups:
            groups = ["default"]
        nickname = str(payload.get("nickname") or "").strip()[:300]
        if not enabled:
            if position is not None:
                current["items"].pop(position)
        else:
            saved = self._favorite_record(section, catalog_item, groups, nickname)
            if position is None:
                current["items"].append(saved)
            else:
                existing = current["items"][position]
                if "nickname" not in payload:
                    saved["nickname"] = str(existing.get("nickname") or "")
                current["items"][position] = saved
        await self.comfy.save_favorites({section: current})
        return await self.get(section)

    async def create_group(self, section: str, payload: dict[str, Any]) -> dict[str, Any]:
        section = _section(section)
        name = self._group_name(payload)
        data = await self.comfy.favorites()
        current = normalize_section(data.get(section))
        if len(current["groups"]) >= MAX_GROUPS:
            raise CatalogError("收藏分组数量已达到上限")
        group = {"id": f"group_{uuid.uuid4().hex[:12]}", "name": name, "isSystem": False}
        current["groups"].append(group)
        await self.comfy.save_favorites({section: current})
        return {"group": group, **(await self.get(section))}

    async def update_group(self, section: str, group_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        section = _section(section)
        if group_id == "default":
            raise CatalogError("默认收藏分组不能重命名")
        name = self._group_name(payload)
        data = await self.comfy.favorites()
        current = normalize_section(data.get(section))
        group = next((item for item in current["groups"] if item["id"] == group_id), None)
        if group is None:
            raise KeyError(group_id)
        group["name"] = name
        await self.comfy.save_favorites({section: current})
        return await self.get(section)

    async def delete_group(self, section: str, group_id: str) -> dict[str, Any]:
        section = _section(section)
        if group_id == "default":
            raise CatalogError("默认收藏分组不能删除")
        data = await self.comfy.favorites()
        current = normalize_section(data.get(section))
        if not any(item["id"] == group_id for item in current["groups"]):
            raise KeyError(group_id)
        current["groups"] = [item for item in current["groups"] if item["id"] != group_id]
        for item in current["items"]:
            item["groupIds"] = [value for value in item.get("groupIds") or [] if value != group_id]
        await self.comfy.save_favorites({section: current})
        return await self.get(section)

    async def sync_custom(self, section: str, item: dict[str, Any]) -> None:
        section = _section(section)
        data = await self.comfy.favorites()
        current = normalize_section(data.get(section))
        key = str(item.get("id") or "")
        saved = next(
            (entry for entry in current["items"] if entry.get("isCustom") and favorite_key(section, entry) == key),
            None,
        )
        if saved is None:
            return
        saved["nickname"] = str(item.get("title") or "")
        saved["customContent"] = str(item.get("prompt") or "")
        await self.comfy.save_favorites({section: current})

    @staticmethod
    def _group_name(payload: dict[str, Any]) -> str:
        name = str(payload.get("name") or "").strip()
        if not name or len(name) > 100:
            raise CatalogError("收藏分组名称需要 1-100 个字符")
        return name

    @staticmethod
    def _favorite_record(section: str, item: dict[str, Any], groups: list[str], nickname: str) -> dict[str, Any]:
        if item.get("builtin"):
            if section == "character":
                return {"name": item["favorite_key"], "nickname": nickname, "groupIds": groups, "isCustom": False}
            return {"id": item["favorite_key"], "name": item.get("subtitle") or item.get("title"), "nickname": nickname, "groupIds": groups, "isCustom": False}
        custom_name = str(item.get("id") or "custom")
        return {
            "id": custom_name,
            "name": custom_name,
            "nickname": item.get("title") or nickname,
            "customContent": item.get("prompt") or "",
            "groupIds": groups,
            "isCustom": True,
        }
