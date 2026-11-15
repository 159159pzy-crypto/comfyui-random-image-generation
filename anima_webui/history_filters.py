"""Read-only current pool membership for filtering saved generation records.

The pool editor's recovery/reconciliation entrypoints are deliberately not used:
opening a history filter must never change a generation draft or a source store.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import unicodedata
from dataclasses import dataclass
from typing import Any

from .catalog import ANIMA_CATEGORY_ORDER, SECTIONS, anima_category_label_zh, anima_category_value, normalize_text
from .comfy import ComfyError
from .favorites import _extension_section, favorite_key
from .http_security import strict_json_loads
from .history_search import normalize as normalize_history_value
from .workflow import WorkflowError


POOL_LABELS = dict(zip(SECTIONS, ("人物", "服装", "姿势", "背景", "表情")))
ATTRIBUTES = {"gender": "性别", "hair": "发色", "eye": "瞳色", "series": "作品／系列"}
GROUP_KINDS = {"favorite_group", "custom_group", "category", "trait"}
FILTER_KINDS = GROUP_KINDS | {"item", *ATTRIBUTES}
MAX_CONDITIONS = 128


class HistoryFilterError(RuntimeError):
    def __init__(self, message: str, *, code: str = "history_filter_catalog_unavailable", status: int = 503,
                 group_revision: str | None = None):
        super().__init__(message)
        self.status = status
        self.payload: dict[str, Any] = {"error": message, "code": code}
        if group_revision is not None:
            self.payload["group_revision"] = group_revision


def parse_content_filters(raw: str | None) -> dict[str, list[dict[str, str]]]:
    if raw is None:
        return {"include": [], "exclude": []}
    if len(raw) > 60000:
        raise WorkflowError("筛选条件过多，请减少条件")
    try:
        value = strict_json_loads(raw)
    except (ValueError, TypeError, RecursionError) as error:
        raise WorkflowError("content_filters 必须是有效的 JSON 对象") from error
    if not isinstance(value, dict) or set(value) - {"include", "exclude"}:
        raise WorkflowError("content_filters 只接受 include 和 exclude")
    result: dict[str, list[dict[str, str]]] = {"include": [], "exclude": []}
    identities: dict[str, set[tuple[str, str, str]]] = {"include": set(), "exclude": set()}
    for mode in result:
        tokens = value.get(mode, [])
        if not isinstance(tokens, list) or len(tokens) > MAX_CONDITIONS:
            raise WorkflowError("筛选条件必须是数组，且每侧最多 128 项")
        for token in tokens:
            if not isinstance(token, dict) or set(token) != {"pool", "kind", "value"}:
                raise WorkflowError("筛选项必须包含 pool、kind、value")
            pool, kind, key = token["pool"], token["kind"], token["value"]
            if not isinstance(pool, str) or pool not in SECTIONS or not isinstance(kind, str) or kind not in FILTER_KINDS:
                raise WorkflowError("筛选分类无效")
            if kind in ATTRIBUTES and pool != "character":
                raise WorkflowError("人物属性只适用于人物池")
            if (not isinstance(key, str) or not key.strip() or len(key) > 1000
                    or any(unicodedata.category(char) in {"Cc", "Cs"} for char in key)):
                raise WorkflowError("筛选值必须是有效的非空文本")
            if kind in ATTRIBUTES:
                key = normalize_history_value(key)
            elif kind == "category":
                key = normalize_text(anima_category_value(key))
            elif kind == "trait":
                key = normalize_text(key)
            if not key:
                raise WorkflowError("筛选值必须是有效的非空文本")
            identity = (pool, kind, key)
            if identity not in identities[mode]:
                identities[mode].add(identity)
                result[mode].append({"pool": pool, "kind": kind, "value": key})
    if identities["include"] & identities["exclude"]:
        raise WorkflowError("同一个条件不能同时包含和排除")
    return result


def validate_query(value: str) -> str:
    if len(value) > 1000 or any(unicodedata.category(char) == "Cs" for char in value):
        raise WorkflowError("搜索内容过长或包含无效字符")
    return value.strip()


def _tree(groups: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    nodes = {str(group["id"]): {"kind": kind, "value": str(group["id"]),
             "label": "我的收藏" if kind == "favorite_group" and group["id"] == "default" else str(group["name"]),
             "children": []} for group in groups}
    roots = []
    for group in groups:
        node = nodes[str(group["id"])]
        parent = nodes.get(str(group.get("parentId") or ""))
        (parent["children"] if parent is not None else roots).append(node)
    return roots


def _members_with_descendants(groups: list[dict[str, Any]], direct: dict[str, set[str]]) -> dict[str, set[str]]:
    children: dict[str, list[str]] = {}
    valid = {str(group["id"]) for group in groups}
    for group in groups:
        children.setdefault(str(group.get("parentId") or ""), []).append(str(group["id"]))
    result = {}
    for key in valid:
        pending, seen, members = [key], set(), set()
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            members.update(direct.get(current, set()))
            pending.extend(children.get(current, []))
        result[key] = members
    return result


@dataclass
class FilterSnapshot:
    sections: list[dict[str, Any]]
    members: dict[tuple[str, str, str], set[str]]
    revision: str

    def ids(self, pool: str, kind: str, value: str) -> set[str]:
        if kind == "category":
            value = normalize_text(anima_category_value(value))
        elif kind == "trait":
            value = normalize_text(value)
        return self.members.get((pool, kind, value), set())


class HistoryFilterService:
    def __init__(self, history: Any, catalog: Any, lifecycle: Any):
        self.history, self.catalog, self.lifecycle = history, catalog, lifecycle
        self._snapshot_lock = asyncio.Lock()
        self._snapshot_pending: asyncio.Task[FilterSnapshot] | None = None
        self._cached_snapshot: FilterSnapshot | None = None
        self._catalog_lists: tuple[Any, ...] = ()
        self._cached_items: dict[str, Any] = {}
        self._cached_groups: dict[str, Any] = {}
        self._cached_favorites: dict[str, Any] = {}

    def _check_sources(self) -> None:
        custom, extension = self.lifecycle.custom, self.lifecycle.extension
        if (not getattr(custom, "load_trusted", getattr(custom, "source_trusted", True))
                and getattr(custom, "load_state", "loaded") != "missing"):
            raise HistoryFilterError("自定义词池未完整加载，暂时无法读取分组")
        if not getattr(extension, "source_trusted", True) and getattr(extension, "load_state", "loaded") != "missing":
            raise HistoryFilterError("词条收藏未完整加载，暂时无法读取分组")

    @staticmethod
    def _copy_catalog(lists: tuple[Any, ...]) -> dict[str, Any]:
        # Prompt bodies/preview URLs are irrelevant to membership. Avoid copying
        # the entire (often very large) generation catalog on each keystroke.
        fields = ("id", "favorite_key", "title", "title_zh", "gender", "hair", "eye", "copyright",
                  "group_ids", "categories", "traits")
        return {section: [{field: copy.deepcopy(item.get(field)) for field in fields} for item in values]
                for section, values in zip(SECTIONS, lists)}

    async def snapshot(self) -> FilterSnapshot:
        task = self._snapshot_pending
        if task is None or task.done():
            task = asyncio.create_task(self._read_snapshot())
            self._snapshot_pending = task
            task.add_done_callback(self._snapshot_finished)
        # Rapid changes share the same current read, including its timeout. One
        # cancelled HTTP request must not cancel work used by another request.
        return await asyncio.shield(task)

    def _snapshot_finished(self, task: asyncio.Task[FilterSnapshot]) -> None:
        if self._snapshot_pending is task:
            self._snapshot_pending = None
        if not task.cancelled():
            task.exception()  # Retrieve failures even if every waiter cancelled.

    async def _read_snapshot(self) -> FilterSnapshot:
        # Identical lock order to the publisher, but no ensure_ready/recovery or
        # mutable store read helpers. No half-published favorite/source pair.
        try:
            async with self._snapshot_lock:
                async with self.lifecycle._lock:
                    async with self.lifecycle._participants():
                        if self.lifecycle._journal("anima").exists():
                            raise HistoryFilterError("词池有尚未完成的同步，请先在词池管理中处理后重试")
                        self._check_sources()
                        # reload/set_custom_items replace every affected list;
                        # keep references rather than ids to prevent id reuse.
                        lists = tuple(self.catalog._items[section] for section in SECTIONS)
                        same_catalog = bool(self._catalog_lists) and all(a is b for a, b in zip(lists, self._catalog_lists))
                        raw_favorites = {section: self.lifecycle.extension.get("anima", section) for section in SECTIONS}
                        groups = self.lifecycle.custom.groups
                        if (self._cached_snapshot is not None and same_catalog and groups == self._cached_groups
                                and raw_favorites == self._cached_favorites):
                            return self._cached_snapshot
                        items = self._cached_items if same_catalog else await asyncio.to_thread(self._copy_catalog, lists)
                        groups = copy.deepcopy(groups)
                        raw_favorites = copy.deepcopy(raw_favorites)
                        favorites = {section: _extension_section(raw_favorites[section], section) for section in SECTIONS}
                        # Keep the publishing lock through compilation: once an
                        # own mutation completes, the next read cannot coalesce
                        # onto an older snapshot still being built.
                        snapshot = await asyncio.to_thread(self._build_snapshot, items, groups, favorites)
                        self._cached_snapshot, self._catalog_lists, self._cached_items = snapshot, lists, items
                        self._cached_groups, self._cached_favorites = groups, raw_favorites
                        return snapshot
        except HistoryFilterError:
            raise
        except (ComfyError, TimeoutError, OSError, ValueError, TypeError) as error:
            raise HistoryFilterError("暂时无法读取词池分组，请重试；原筛选条件已保留") from error

    @staticmethod
    def _build_snapshot(items: dict[str, Any], custom: dict[str, Any], favorites: dict[str, Any]) -> FilterSnapshot:
        sections, members, revision_items = [], {}, []
        for section in SECTIONS:
            entries = items[section]
            custom_groups = custom.get(section, [])
            saved = favorites[section]
            favorite_ids: dict[str, set[str]] = {}
            direct_custom: dict[str, set[str]] = {}
            categories: dict[str, str] = {}
            traits: dict[str, str] = {}
            for item in entries:
                item_id = str(item["id"])
                favorite_ids.setdefault(str(item.get("favorite_key") or ""), set()).add(item_id)
                for group in item.get("group_ids") or []:
                    direct_custom.setdefault(str(group), set()).add(item_id)
                for value in item.get("categories") or []:
                    key = normalize_text(anima_category_value(value))
                    if key:
                        categories.setdefault(key, anima_category_label_zh(value))
                        members.setdefault((section, "category", key), set()).add(item_id)
                for value in item.get("traits") or []:
                    key = normalize_text(value)
                    if key:
                        traits.setdefault(key, str(value))
                        members.setdefault((section, "trait", key), set()).add(item_id)
                # Include names and current property values in the revision so
                # browsing a refreshed catalog cannot retain an obsolete label.
                revision_items.append([section, item_id, item.get("title"), item.get("title_zh"),
                                       *[item.get(field) for field in ("gender", "hair", "eye", "copyright")]])
            direct_favorites: dict[str, set[str]] = {}
            for item in saved["items"]:
                ids = favorite_ids.get(favorite_key(section, item), set())
                for group in item.get("groupIds") or []:
                    direct_favorites.setdefault(str(group), set()).update(ids)
            for kind, values, direct in (("favorite_group", saved["groups"], direct_favorites),
                                         ("custom_group", custom_groups, direct_custom)):
                for key, ids in _members_with_descendants(values, direct).items():
                    members[(section, kind, key)] = ids
            branches = [
                {"kind": "heading", "value": "favorites", "label": "收藏分组", "children": _tree(saved["groups"], "favorite_group")},
                {"kind": "heading", "value": "custom", "label": "自定义分组", "children": _tree(custom_groups, "custom_group")},
            ]
            if section != "character":
                order = {normalize_text(value): index for index, value in enumerate(ANIMA_CATEGORY_ORDER.get(section, []))}
                category_keys = sorted(categories, key=lambda key: (order.get(key, len(order)), key))
                branches.extend([
                    {"kind": "heading", "value": "categories", "label": "场景分类" if section == "background" else POOL_LABELS[section] + "分类",
                     "children": [{"kind": "category", "value": key, "label": categories[key], "children": []} for key in category_keys]},
                    {"kind": "heading", "value": "traits", "label": "场景特征" if section == "background" else POOL_LABELS[section] + "特征",
                     "children": [{"kind": "trait", "value": key, "label": traits[key], "children": []} for key in sorted(traits)]},
                ])
            sections.append({"id": section, "label": POOL_LABELS[section], "groups": branches,
                             "attributes": [{"kind": kind, "label": label} for kind, label in ATTRIBUTES.items()] if section == "character" else []})
        digest = {"sections": sections, "members": [[*key, sorted(ids)] for key, ids in sorted(members.items())],
                  "items": revision_items}
        revision = hashlib.sha256(json.dumps(digest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return FilterSnapshot(sections, members, revision)

    @staticmethod
    def check_revision(snapshot: FilterSnapshot, requested: str | None) -> None:
        if requested and requested != snapshot.revision:
            raise HistoryFilterError("词池分组已更新，正在重新加载筛选结果", code="history_filter_catalog_changed",
                                     status=409, group_revision=snapshot.revision)

    async def resolve(self, filters: dict[str, Any], revision: str | None = None) -> tuple[dict[str, Any], str | None]:
        dynamic = any(token["kind"] in GROUP_KINDS for mode in filters.values() for token in mode)
        snapshot = await self.snapshot() if dynamic else None
        if snapshot:
            self.check_revision(snapshot, revision)
        resolved: dict[str, Any] = {"include": {}, "exclude": {}, "attributes": {}}
        for mode in ("include", "exclude"):
            for token in filters[mode]:
                pool, kind, value = token["pool"], token["kind"], token["value"]
                if kind in ATTRIBUTES:
                    resolved["attributes"].setdefault(kind, {"include": [], "exclude": []})[mode].append(value)
                else:
                    ids = {value} if kind == "item" else snapshot.ids(pool, kind, value)
                    resolved[mode].setdefault(pool, set()).update(ids)
        for mode in ("include", "exclude"):
            resolved[mode] = {pool: sorted(ids) for pool, ids in resolved[mode].items()}
        return resolved, snapshot.revision if snapshot else None

    async def options(self, *, section: str, kind: str = "", value: str = "", query: str = "",
                      page: int = 1, limit: int = 40, revision: str | None = None) -> dict[str, Any]:
        if section not in SECTIONS or kind not in {"", "all", *GROUP_KINDS, *ATTRIBUTES}:
            raise WorkflowError("候选分类无效")
        if kind in ATTRIBUTES and section != "character":
            raise WorkflowError("人物属性只适用于人物池")
        if kind in GROUP_KINDS and not value:
            raise WorkflowError("分组候选需要 group_id")
        if page < 1 or not 1 <= limit <= 100 or page > 1000000:
            raise WorkflowError("候选分页参数无效")
        # Local item/property search remains usable if ComfyUI is offline. A
        # dynamic-group read always obtains and verifies one complete snapshot.
        snapshot = await self.snapshot() if kind in GROUP_KINDS else None
        if snapshot:
            self.check_revision(snapshot, revision)
        result = await self.history.content_filter_options(
            section, q=validate_query(query), page=page, limit=limit,
            term_ids=sorted(snapshot.ids(section, kind, value)) if snapshot else None,
            attribute=kind if kind in ATTRIBUTES else None,
        )
        return {**result, "group_revision": snapshot.revision if snapshot else revision}
