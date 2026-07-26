from __future__ import annotations

import asyncio
import copy
import functools
import uuid
from typing import Any

from .catalog import SECTIONS, CatalogError, PromptCatalog, normalize_text


MAX_GROUPS = 128
MAX_ITEMS = 50000
FAVORITE_SECTIONS = (*SECTIONS, "artist")


def _section(value: str) -> str:
    if value not in FAVORITE_SECTIONS:
        raise CatalogError(f"不支持的收藏分类: {value}")
    return value


def _repair_group_hierarchy(groups: list[dict[str, Any]]) -> None:
    by_id = {group["id"]: group for group in groups}
    for group in groups:
        parent_id = group.get("parentId")
        if group["id"] == "default" or parent_id not in by_id or parent_id == group["id"]:
            group["parentId"] = None

    for group in groups:
        seen: set[str] = set()
        current = group
        while current.get("parentId"):
            if current["id"] in seen:
                group["parentId"] = None
                break
            seen.add(current["id"])
            parent = by_id.get(current["parentId"])
            if parent is None:
                group["parentId"] = None
                break
            current = parent


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
        parent_id = str(raw.get("parentId") or "").strip() or None
        source_id = str(raw.get("sourceCustomGroupId") or "").strip() or None
        normalized_groups.append(
            {
                "id": group_id,
                "name": name[:100],
                "isSystem": group_id == "default",
                "parentId": parent_id,
                "sourceCustomGroupId": source_id,
            }
        )
    if "default" not in seen_groups:
        normalized_groups.insert(
            0,
            {
                "id": "default",
                "name": "Default Favorites",
                "isSystem": True,
                "parentId": None,
                "sourceCustomGroupId": None,
            },
        )
    _repair_group_hierarchy(normalized_groups)
    valid_group_ids = {group["id"] for group in normalized_groups}
    normalized_items: list[dict[str, Any]] = []
    for raw in items[:MAX_ITEMS]:
        if not isinstance(raw, dict):
            continue
        item = copy.deepcopy(raw)
        group_ids = item.get("groupIds") if isinstance(item.get("groupIds"), list) else []
        item["groupIds"] = list(
            dict.fromkeys(str(group_id) for group_id in group_ids if str(group_id) in valid_group_ids)
        )
        normalized_items.append(item)
    return {"groups": normalized_groups, "items": normalized_items}


def favorite_key(section: str, item: dict[str, Any]) -> str:
    if section in {"character", "artist"}:
        return str(item.get("name") or item.get("id") or "")
    return str(item.get("id") or item.get("name") or "")


def _children_by_parent(groups: list[dict[str, Any]]) -> dict[str | None, list[dict[str, Any]]]:
    result: dict[str | None, list[dict[str, Any]]] = {}
    for group in groups:
        result.setdefault(group.get("parentId"), []).append(group)
    return result


def _descendant_ids(groups: list[dict[str, Any]], group_id: str) -> set[str]:
    children = _children_by_parent(groups)
    result: set[str] = set()
    pending = [group_id]
    while pending:
        current = pending.pop()
        if current in result:
            continue
        result.add(current)
        pending.extend(child["id"] for child in children.get(current, []))
    return result


def _group_stats(current: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    groups = current["groups"]
    items = current["items"]
    children = _children_by_parent(groups)
    valid_ids = {group["id"] for group in groups}
    result: list[dict[str, Any]] = []
    for group in groups:
        group_id = group["id"]
        descendants = _descendant_ids(groups, group_id)
        direct_items = [item for item in items if group_id in (item.get("groupIds") or [])]
        result.append(
            {
                **group,
                "directCount": len(direct_items),
                "totalCount": sum(
                    bool(descendants.intersection(item.get("groupIds") or [])) for item in items
                ),
                "childCount": len(children.get(group_id, [])),
                "exclusiveCount": sum(
                    not ({str(value) for value in item.get("groupIds") or []} & valid_ids - {group_id})
                    for item in direct_items
                ),
            }
        )
    return result


def _locked(method: Any) -> Any:
    """把整个变更方法包进 self._lock,保证读-改-写不被并发交错。"""

    @functools.wraps(method)
    async def wrapper(self: "FavoritesService", *args: Any, **kwargs: Any) -> Any:
        async with self._lock:
            return await method(self, *args, **kwargs)

    return wrapper


class FavoritesService:
    def __init__(self, comfy: Any, catalog: PromptCatalog):
        self.comfy = comfy
        self.catalog = catalog
        # 所有变更都是「读 ComfyUI 全量 → 内存改 → 整体写回」,
        # 两次操作并发时后写者会覆盖前写者;用锁把读-改-写串行化。
        self._lock = asyncio.Lock()

    async def get(self, section: str) -> dict[str, Any]:
        section = _section(section)
        payload = await self.comfy.favorites()
        result = normalize_section(payload.get(section))
        return {
            **result,
            "groups": _group_stats(result),
            "favorite_keys": sorted(
                favorite_key(section, item) for item in result["items"] if item.get("groupIds")
            ),
        }

    async def collection_group_ids(self, section: str, group_id: str) -> set[str]:
        section = _section(section)
        payload = await self.comfy.favorites()
        current = normalize_section(payload.get(section))
        if not any(group["id"] == group_id for group in current["groups"]):
            return set()
        return _descendant_ids(current["groups"], group_id)

    @_locked
    async def update_item(self, section: str, payload: dict[str, Any]) -> dict[str, Any]:
        section = _section(section)
        if section == "artist":
            return await self._update_artist(payload)
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
        existing = current["items"][position] if position is not None else None
        old_group_ids = list(existing.get("groupIds") or []) if existing else []
        enabled = payload.get("favorite", True)
        if not isinstance(enabled, bool):
            raise CatalogError("favorite 必须是布尔值")
        groups = self._validated_group_ids(current, payload.get("groupIds"), enabled)
        nickname = str(payload.get("nickname") or "").strip()[:300]
        if not enabled:
            if position is not None:
                current["items"].pop(position)
        else:
            saved = self._favorite_record(section, catalog_item, groups, nickname)
            if position is None:
                current["items"].append(saved)
            else:
                if "nickname" not in payload:
                    saved["nickname"] = str(existing.get("nickname") or "")
                current["items"][position] = saved
        self._prune_empty_affected_children(current, old_group_ids)
        await self.comfy.save_favorites({section: current})
        return await self.get(section)

    @_locked
    async def create_group(self, section: str, payload: dict[str, Any]) -> dict[str, Any]:
        section = _section(section)
        name = self._group_name(payload)
        data = await self.comfy.favorites()
        current = normalize_section(data.get(section))
        if len(current["groups"]) >= MAX_GROUPS:
            raise CatalogError("收藏分组数量已达到上限")
        self._ensure_unique_sibling(current, None, name)
        group = {
            "id": f"group_{uuid.uuid4().hex[:12]}",
            "name": name,
            "isSystem": False,
            "parentId": None,
            "sourceCustomGroupId": None,
        }
        current["groups"].append(group)
        await self.comfy.save_favorites({section: current})
        return {"group": group, **(await self.get(section))}

    @_locked
    async def import_custom_group(
        self,
        section: str,
        parent_id: str,
        source_group: dict[str, Any],
        source_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        section = _section(section)
        data = await self.comfy.favorites()
        current = normalize_section(data.get(section))
        if not any(group["id"] == parent_id for group in current["groups"]):
            raise KeyError(parent_id)
        if not source_items:
            raise CatalogError("空自定义分组不能导入收藏子分组")
        if len(current["groups"]) >= MAX_GROUPS:
            raise CatalogError("收藏分组数量已达到上限")
        source_id = str(source_group.get("id") or "").strip()
        name = self._group_name(source_group)
        self._ensure_unique_sibling(current, parent_id, name, source_id)
        group = {
            "id": f"group_{uuid.uuid4().hex[:12]}",
            "name": name,
            "isSystem": False,
            "parentId": parent_id,
            "sourceCustomGroupId": source_id,
        }
        current["groups"].append(group)
        for source_item in source_items:
            catalog_item = self.catalog.get(str(source_item.get("id") or ""))
            if catalog_item is None or catalog_item.get("section") != section:
                continue
            key = favorite_key(section, catalog_item)
            existing = next(
                (item for item in current["items"] if favorite_key(section, item) == key), None
            )
            if existing is not None:
                existing["groupIds"] = list(
                    dict.fromkeys([*(existing.get("groupIds") or []), group["id"]])
                )
            else:
                current["items"].append(
                    self._favorite_record(section, catalog_item, [group["id"]], "")
                )
        await self.comfy.save_favorites({section: current})
        return {"group": group, **(await self.get(section))}

    @_locked
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
        self._ensure_unique_sibling(current, group.get("parentId"), name, except_id=group_id)
        group["name"] = name
        await self.comfy.save_favorites({section: current})
        return await self.get(section)

    @_locked
    async def delete_group(
        self, section: str, group_id: str, delete_items: bool = False
    ) -> dict[str, Any]:
        section = _section(section)
        if group_id == "default":
            raise CatalogError("默认收藏分组不能删除")
        data = await self.comfy.favorites()
        current = normalize_section(data.get(section))
        group = next((item for item in current["groups"] if item["id"] == group_id), None)
        if group is None:
            raise KeyError(group_id)
        subtree_ids = _descendant_ids(current["groups"], group_id)
        direct_items = [item for item in current["items"] if group_id in (item.get("groupIds") or [])]
        child_count = sum(item.get("parentId") == group_id for item in current["groups"])
        if delete_items and (group.get("parentId") is None or child_count or not direct_items):
            raise CatalogError("只有含有条目且没有子分组的收藏叶子子组可以同时删除条目")

        deleted_favorite_count = 0
        detached_item_count = 0
        moved_to_default_count = 0
        next_items: list[dict[str, Any]] = []
        for item in current["items"]:
            group_ids = list(item.get("groupIds") or [])
            affected = bool(subtree_ids.intersection(group_ids))
            if not affected:
                next_items.append(item)
                continue
            remaining = [value for value in group_ids if value not in subtree_ids]
            if delete_items and group_id in group_ids and not remaining:
                deleted_favorite_count += 1
                continue
            if delete_items:
                detached_item_count += 1
            if not remaining:
                remaining = ["default"]
                moved_to_default_count += 1
            item["groupIds"] = remaining
            next_items.append(item)
        current["items"] = next_items
        current["groups"] = [
            item for item in current["groups"] if item["id"] not in subtree_ids
        ]
        await self.comfy.save_favorites({section: current})
        return {
            **(await self.get(section)),
            "deletedGroupIds": sorted(subtree_ids),
            "deletedGroupCount": len(subtree_ids),
            "deletedFavoriteCount": deleted_favorite_count,
            "detachedItemCount": detached_item_count,
            "movedToDefaultCount": moved_to_default_count,
        }

    @_locked
    async def sync_custom(self, section: str, item: dict[str, Any]) -> None:
        section = _section(section)
        data = await self.comfy.favorites()
        current = normalize_section(data.get(section))
        key = str(item.get("id") or "")
        saved = next(
            (
                entry
                for entry in current["items"]
                if entry.get("isCustom") and favorite_key(section, entry) == key
            ),
            None,
        )
        if saved is None:
            return
        saved["nickname"] = str(item.get("title") or "")
        saved["customContent"] = str(item.get("prompt") or "")
        await self.comfy.save_favorites({section: current})

    async def _update_artist(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_name = str(payload.get("name") or "").strip()
        name = self._artist_name(raw_name)
        data = await self.comfy.favorites()
        current = normalize_section(data.get("artist"))
        position = next(
            (
                index
                for index, item in enumerate(current["items"])
                if normalize_text(self._artist_name(item.get("name"))) == normalize_text(name)
            ),
            None,
        )
        existing = current["items"][position] if position is not None else None
        old_group_ids = list(existing.get("groupIds") or []) if existing else []
        enabled = payload.get("favorite", True)
        if not isinstance(enabled, bool):
            raise CatalogError("favorite 必须是布尔值")
        groups = self._validated_group_ids(current, payload.get("groupIds"), enabled)
        if not enabled:
            if position is not None:
                current["items"].pop(position)
        else:
            nickname = str(payload.get("nickname") or "").strip()[:300]
            saved = {"name": name, "nickname": nickname, "groupIds": groups, "isCustom": False}
            if position is None:
                current["items"].append(saved)
            else:
                if "nickname" not in payload:
                    saved["nickname"] = str(existing.get("nickname") or "")
                current["items"][position] = saved
        self._prune_empty_affected_children(current, old_group_ids)
        await self.comfy.save_favorites({"artist": current})
        return await self.get("artist")

    @staticmethod
    def _validated_group_ids(
        current: dict[str, list[dict[str, Any]]], groups: Any, enabled: bool
    ) -> list[str]:
        valid_group_ids = {group["id"] for group in current["groups"]}
        if groups is None:
            return ["default"] if enabled else []
        if not isinstance(groups, list):
            raise CatalogError("groupIds 必须是数组")
        result = list(
            dict.fromkeys(str(value) for value in groups if str(value) in valid_group_ids)
        )
        return result or (["default"] if enabled else [])

    @staticmethod
    def _prune_empty_affected_children(
        current: dict[str, list[dict[str, Any]]], affected_group_ids: list[str]
    ) -> None:
        affected = set(affected_group_ids)
        if not affected:
            return
        child_parent_ids = {group.get("parentId") for group in current["groups"]}
        non_empty = {
            group_id for item in current["items"] for group_id in item.get("groupIds") or []
        }
        removed = {
            group["id"]
            for group in current["groups"]
            if group["id"] in affected
            and not group.get("isSystem")
            and group.get("parentId") is not None
            and group["id"] not in child_parent_ids
            and group["id"] not in non_empty
        }
        if removed:
            current["groups"] = [group for group in current["groups"] if group["id"] not in removed]

    @staticmethod
    def _ensure_unique_sibling(
        current: dict[str, list[dict[str, Any]]],
        parent_id: str | None,
        name: str,
        source_id: str | None = None,
        except_id: str = "",
    ) -> None:
        name_key = normalize_text(name)
        for group in current["groups"]:
            if group["id"] == except_id or group.get("parentId") != parent_id:
                continue
            if normalize_text(group["name"]) == name_key:
                raise CatalogError("同一收藏父分组下不能有重名分组")
            if source_id and group.get("sourceCustomGroupId") == source_id:
                raise CatalogError("这个自定义分组已经导入到当前收藏父分组")

    @staticmethod
    def _group_name(payload: dict[str, Any]) -> str:
        name = str(payload.get("name") or "").strip()
        if not name or len(name) > 100:
            raise CatalogError("收藏分组名称需要 1-100 个字符")
        return name

    @staticmethod
    def _artist_name(value: Any) -> str:
        name = str(value or "").strip()
        if name.startswith("@"):
            name = name[1:].strip()
        elif name.lower().startswith("by "):
            name = name[3:].strip()
        if not name or len(name) > 200 or "," in name:
            raise CatalogError("画师名称需要 1-200 个字符且不能包含逗号")
        return name

    @staticmethod
    def _favorite_record(
        section: str, item: dict[str, Any], groups: list[str], nickname: str
    ) -> dict[str, Any]:
        if item.get("builtin"):
            if section == "character":
                return {
                    "name": item["favorite_key"],
                    "nickname": nickname,
                    "groupIds": groups,
                    "isCustom": False,
                }
            return {
                "id": item["favorite_key"],
                "name": item.get("subtitle") or item.get("title"),
                "nickname": nickname,
                "groupIds": groups,
                "isCustom": False,
            }
        custom_name = str(item.get("id") or "custom")
        return {
            "id": custom_name,
            "name": custom_name,
            "nickname": item.get("title") or nickname,
            "customContent": item.get("prompt") or "",
            "groupIds": groups,
            "isCustom": True,
        }
