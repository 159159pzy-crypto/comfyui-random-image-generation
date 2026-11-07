"""LoRA Manager metadata and workbench-owned collections and character bindings."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import os
import tempfile
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from aiohttp import web

from .async_utils import serialized_mutation
from .comfy import ComfyError
from .http_security import strict_json_loads
from .lora_triggers import normalize_trigger_words
from .persistence import StorageProtection
from .resource_classification import _absolute_path, _path_key, classify_base_model
from .workflow import MAX_LORA_STRENGTH, MIN_LORA_STRENGTH, WorkflowError, normalize_lora_path


def _strings(value: Any, label: str, *, limit: int = 4096) -> list[str]:
    if not isinstance(value, list) or len(value) > limit or any(not isinstance(x, str) or not x.strip() for x in value):
        raise WorkflowError(f"{label} 必须是非空字符串数组，最多 {limit} 项")
    return list(dict.fromkeys(x.strip() for x in value))


def _strength(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not MIN_LORA_STRENGTH <= value <= MAX_LORA_STRENGTH:
        raise WorkflowError(f"LoRA 权重必须是 {MIN_LORA_STRENGTH:g} 至 {MAX_LORA_STRENGTH:g} 之间的有限数值")
    return float(value)


def _name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 100:
        raise WorkflowError("收藏夹名称需为 1 至 100 个字符")
    return value.strip()


def _descendants(groups: list[dict[str, Any]], group_id: str) -> set[str]:
    result = {group_id}
    while True:
        more = {g["id"] for g in groups if g.get("parentId") in result} - result
        if not more:
            return result
        result.update(more)




class LoraLibraryStore(StorageProtection):
    store_name = "lora_library"

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = asyncio.Lock()
        self.load_warnings: list[str] = []
        self.read_only_reason = None
        self._source_text: str | None = None
        self._retryable_write_failure = False
        self.data: dict[str, Any] = {"version": 1, "revision": 0, "groups": [], "resources": {}, "bindings": {}}
        self.reload()

    def reload(self) -> None:
        self.load_warnings = []
        self.read_only_reason = None
        self._retryable_write_failure = False
        if not self.path.exists():
            return
        try:
            source = self.path.read_text(encoding="utf-8")
            data = strict_json_loads(source)
            self._validate(data)
            self.data, self._source_text = data, source
        except (OSError, ValueError, WorkflowError, TypeError, KeyError) as error:
            self._protect_storage(f"LoRA 管理数据无法读取({error})，原文件已保留")

    @staticmethod
    def _validate(data: Any) -> None:
        if not isinstance(data, dict) or type(data.get("version")) is not int or data.get("version") != 1 or type(data.get("revision")) is not int or data["revision"] < 0:
            raise WorkflowError("LoRA 管理数据版本无效")
        groups, resources, bindings = data.get("groups"), data.get("resources"), data.get("bindings")
        if not isinstance(groups, list) or len(groups) > 128 or not isinstance(resources, dict) or not isinstance(bindings, dict):
            raise WorkflowError("LoRA 管理数据结构无效")
        group_ids = [g.get("id") for g in groups if isinstance(g, dict)]
        if len(group_ids) != len(groups) or any(not isinstance(g, str) or not g for g in group_ids) or len(set(group_ids)) != len(groups):
            raise WorkflowError("收藏夹 ID 无效")
        siblings = set()
        by_id = {g["id"]: g for g in groups}
        for group in groups:
            name = _name(group.get("name"))
            parent = group.get("parentId") or ""
            if not isinstance(parent, str):
                raise WorkflowError("父收藏夹 ID 无效")
            if parent and parent not in by_id:
                raise WorkflowError("父收藏夹不存在")
            identity = (parent, name.casefold())
            if identity in siblings:
                raise WorkflowError("同级收藏夹名称不能重复")
            siblings.add(identity)
            seen = {group["id"]}
            while parent:
                if parent in seen:
                    raise WorkflowError("收藏夹不能移动到自身或下级")
                seen.add(parent)
                parent = by_id[parent].get("parentId") or ""
        for resource_id, resource in resources.items():
            if not isinstance(resource_id, str) or not isinstance(resource, dict):
                raise WorkflowError("LoRA 资源记录无效")
            normalize_lora_path(resource["filename"])
            _absolute_path(resource["file_path"])
            if not isinstance(resource.get("sha256", ""), str):
                raise WorkflowError("LoRA 文件摘要无效")
            _strength(resource.get("default_strength", 1))
            if type(resource.get("favorite", False)) is not bool:
                raise WorkflowError("favorite 必须是布尔值")
            tags = _strings(resource.get("tags", []), "tags", limit=256)
            if any(len(tag) > 200 for tag in tags):
                raise WorkflowError("标签不能超过 200 个字符")
            if not isinstance(resource.get("notes", ""), str) or len(resource.get("notes", "")) > 20000:
                raise WorkflowError("备注必须是不超过 20000 个字符的文本")
            if not set(_strings(resource.get("groupIds", []), "groupIds", limit=128)).issubset(by_id):
                raise WorkflowError("收藏夹不存在")
        for character_id, binding in bindings.items():
            if not isinstance(character_id, str) or not character_id or len(character_id) > 1024 or not isinstance(binding, dict) or binding.get("character_id") != character_id or not isinstance(binding.get("loras"), list) or len(binding["loras"]) > 128:
                raise WorkflowError("人物绑定数据无效")
            if not isinstance(binding.get("name"), str) or not binding["name"]:
                raise WorkflowError("人物绑定名称无效")
            bound_ids = set()
            for item in binding["loras"]:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item.get("id") not in resources or item["id"] in bound_ids:
                    raise WorkflowError("人物绑定的 LoRA 记录不存在")
                bound_ids.add(item["id"])
                normalize_lora_path(item.get("filename"))
                if not isinstance(item.get("sha256"), str):
                    raise WorkflowError("人物绑定的文件摘要无效")
                normalize_trigger_words(item.get("trigger_words"))
                _strength(item.get("strength", 1))
                if type(item.get("enabled", True)) is not bool:
                    raise WorkflowError("绑定启用状态必须是布尔值")

    @serialized_mutation
    async def change(self, edit: Callable[[dict[str, Any]], Any], *, retry_write: bool = False, skip_unchanged: bool = False) -> Any:
        if retry_write and self._retryable_write_failure:
            current = self.path.read_text(encoding="utf-8") if self.path.exists() else None
            if current == self._source_text:
                previous_reason = self.read_only_reason
                self.read_only_reason = None
                self.load_warnings = [warning for warning in self.load_warnings if warning != previous_reason]
            else:
                self._retryable_write_failure = False
                self._protect_storage("数据文件已被外部修改，未解除写保护")
        self.ensure_writable()
        current = self.path.read_text(encoding="utf-8") if self.path.exists() else None
        if current != self._source_text:
            self._retryable_write_failure = False
            self._protect_storage("数据文件已被外部修改，请重新启动后重试")
            self.ensure_writable()
        updated = copy.deepcopy(self.data)
        result = edit(updated)
        if skip_unchanged and updated == self.data:
            return copy.deepcopy(result)
        updated["revision"] += 1
        self._validate(updated)
        source = json.dumps(updated, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        try:
            await asyncio.to_thread(self._write, source)
        except OSError as error:
            self._retryable_write_failure = True
            self._protect_storage(f"写入失败({error})，已保留原内容")
            self.ensure_writable()
        self.data, self._source_text = updated, source
        self._retryable_write_failure = False
        return copy.deepcopy(result)

    def _write(self, source: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="lora-library-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(source)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class LoraLibraryService:
    def __init__(self, client: Any, store: LoraLibraryStore, trigger_store: Any, catalog: Any, *,
                 local_usage: Callable[[], Awaitable[dict[str, int]]] | None = None):
        self.client, self.store, self.trigger_store, self.catalog = client, store, trigger_store, catalog
        self.local_usage = local_usage
        self.jobs: dict[str, dict[str, Any]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._item_locks: dict[str, asyncio.Lock] = {}
        self._binding_lock = asyncio.Lock()
        self._closing = False
        self._inventory_snapshot: dict[str, Any] | None = None
        self._last_synced_at: str | None = None
        self._offline_error: str | None = None

    def _ensure_open(self) -> None:
        if self._closing:
            raise ComfyError("LoRA 管理正在关闭，请重新连接后重试")
        if self._offline_error:
            raise ComfyError("LoRA Manager 暂不可用，缓存仅供查看；请恢复连接并刷新后再修改")

    def _connection_state(self) -> dict[str, Any]:
        offline = self._offline_error is not None
        return {"offline": offline, "cached": offline and self._inventory_snapshot is not None,
                "writable": not offline and not self._closing and self.store.read_only_reason is None,
                "last_synced_at": self._last_synced_at, "error": self._offline_error,
                "message": "LoRA Manager 暂不可用，正在显示上次成功加载的资料。恢复连接后可继续修改。" if offline else ""}


    def default_strength(self, filename: str) -> float:
        identity = normalize_lora_path(filename).casefold()
        matches = [item for item in self.store.data["resources"].values()
                   if normalize_lora_path(item["filename"]).casefold() == identity]
        return float(matches[0].get("default_strength", 1)) if len(matches) == 1 else 1.0

    async def available_bindings(self) -> dict[str, Any]:
        return await self.client.resource_classifications("loras", refresh=True)

    async def inventory(self, *, refresh: bool = False, model_profile: str = "all", allow_cached: bool = False) -> list[dict[str, Any]]:
        try:
            inventory = await self.client.resource_classifications("loras", refresh=refresh)
        except ComfyError as error:
            self._offline_error = str(error)
            if not allow_cached or self._inventory_snapshot is None:
                raise
            inventory = copy.deepcopy(self._inventory_snapshot)
        else:
            self._offline_error = None
            self._inventory_snapshot = copy.deepcopy(inventory)
            self._last_synced_at = datetime.now(timezone.utc).isoformat()
        connection = self._connection_state()
        usage_counts = await self.local_usage() if self.local_usage is not None else {}
        classifications = inventory.get("items", [])
        raw_items = inventory.get("manager_items")
        roots = inventory.get("roots", [])
        if raw_items is None:
            raw_items = [{**item, "model_name": item.get("display_name"), "file_size": item.get("size"),
                          "preview_url": item.get("preview"), "civitai": {"trainedWords": item.get("trigger_words", [])}}
                         for item in classifications if item.get("file_path")]
        unique_raw = {_path_key(_absolute_path(item["file_path"])): item for item in raw_items}
        saved = self.store.data["resources"]
        classified_by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
        ambiguous_by_relative: dict[str, dict[str, Any]] = {}
        saved_by_path: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
        bindings_by_resource: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for classified in classifications:
            if classified.get("file_path"):
                classified_by_path[_path_key(_absolute_path(classified["file_path"]))].append(classified)
            if classified.get("classification_status") == "ambiguous":
                ambiguous_by_relative[normalize_lora_path(classified["filename"]).casefold()] = classified
        for resource_id, record in saved.items():
            saved_by_path[_path_key(_absolute_path(record["file_path"]))].append((resource_id, record))
        for character_id, binding in self.store.data["bindings"].items():
            for lora in binding["loras"]:
                bindings_by_resource[lora["id"]].append({"character_id": character_id, "name": binding["name"]})
        result = []
        for path_key, raw in unique_raw.items():
            path = _absolute_path(raw["file_path"])
            containing = [root for root in roots if path_key.startswith(_path_key(_absolute_path(root)).rstrip("/") + "/")]
            root = max(containing, key=len) if containing else ""
            relative = path[len(_absolute_path(root).rstrip("/")) + 1:] if root else str(raw.get("filename") or raw.get("folder", "") + "/" + path.rsplit("/", 1)[-1]).lstrip("/")
            candidates = classified_by_path.get(path_key, [])
            exact = next((item for item in candidates if normalize_lora_path(item["filename"]).casefold() == relative.casefold()), None)
            match = exact or (candidates[0] if len(candidates) == 1 else None)
            collision = ambiguous_by_relative.get(relative.casefold())
            profile, status, reason = classify_base_model(raw.get("base_model"))
            if match:
                profile, status, reason = match.get("model_profile"), match.get("classification_status"), match.get("classification_reason", "")
            elif collision:
                profile, status, reason = None, "ambiguous", collision.get("classification_reason", "加载路径不唯一")
            else:
                status, reason = "unindexed", "ComfyUI 加载器尚未收录此文件"
            filename = str(match["filename"]) if match else relative
            sha = str(raw.get("sha256") or "").casefold()
            saved_matches = saved_by_path.get(path_key, [])
            resource_id = saved_matches[0][0] if len(saved_matches) == 1 else "lora_" + hashlib.sha256(path_key.encode()).hexdigest()[:24]
            local = saved.get(resource_id, {})
            words = (raw.get("civitai") or {}).get("trainedWords", [])
            words, overridden = self.trigger_store.effective(filename, words, "anima")
            preview = str(raw.get("preview_url") or "")
            result.append({"id": resource_id, "name": normalize_lora_path(filename), "filename": filename,
                           "normalized_path": normalize_lora_path(filename), "file_path": path, "root": root,
                           "folder": relative.rpartition("/")[0], "basename": relative.rsplit("/", 1)[-1],
                           "display_name": str(raw.get("model_name") or relative.rsplit("/", 1)[-1]),
                           "base_model": str(raw.get("base_model") or "Unknown"), "model_profile": profile,
                           "classification_status": status, "classification_reason": reason,
                           "available": not connection["offline"] and status == "recognized" and (model_profile == "all" or profile == model_profile),
                           "sha256": sha, "favorite": local.get("favorite", False),
                           "tags": list(local.get("tags", [])), "notes": local.get("notes", ""),
                           "size": raw.get("file_size") or 0, "modified": raw.get("modified") or 0,
                           "usage_count": usage_counts.get(normalize_lora_path(filename).casefold(), 0), "has_preview": bool(preview),
                           "preview": f"/api/lora-library/items/{resource_id}/preview" if preview else "", "_preview": preview,
                           "preview_nsfw_level": raw.get("preview_nsfw_level", 0),
                           "trigger_words": words, "source_trigger_words": (raw.get("civitai") or {}).get("trainedWords", []),
                           "trigger_override": overridden, "trigger_metadata_available": True,
                           "default_strength": local.get("default_strength", 1.0), "groupIds": list(local.get("groupIds", [])),
                           "configured": resource_id in saved,
                           "offline": connection["offline"], "cached": connection["cached"], "writable": connection["writable"],
                           "bindings": copy.deepcopy(bindings_by_resource.get(resource_id, []))})
        return result

    @staticmethod
    def public(item: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in item.items() if not key.startswith("_")}

    async def item(self, resource_id: str, *, refresh: bool = False, model_profile: str = "all", allow_cached: bool = False) -> dict[str, Any]:
        result = next((item for item in await self.inventory(refresh=refresh, model_profile=model_profile, allow_cached=allow_cached) if item["id"] == resource_id), None)
        if result is None:
            raise KeyError(resource_id)
        return result

    @staticmethod
    def _remember(data: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
        record = data["resources"].setdefault(item["id"], {"groupIds": [], "default_strength": 1.0, "favorite": False, "tags": [], "notes": ""})
        record.update({key: item[key] for key in ("filename", "file_path", "sha256")})
        return record

    async def query(self, params: Any, *, ids_only: bool = False) -> dict[str, Any]:
        def many(key: str) -> list[str]:
            value = params.getall(key, []) if hasattr(params, "getall") else params.get(key, [])
            return [str(x) for x in (value if isinstance(value, list) else [value]) if x]
        def boolean(key: str) -> bool | None:
            value = params.get(key)
            if value is None or value == "":
                return None
            if value in (True, "true", "1"):
                return True
            if value in (False, "false", "0"):
                return False
            raise WorkflowError(f"{key} 必须是布尔值")
        profile = params.get("model_profile", "all")
        if not isinstance(profile, str) or profile not in {"all", "anima"}:
            raise WorkflowError("模型档案无效")
        items = await self.inventory(refresh=boolean("refresh") is True, model_profile=profile, allow_cached=True)
        connection = self._connection_state()
        groups = copy.deepcopy(self.store.data["groups"])
        all_items = items
        if profile != "all":
            items = [item for item in items if item["model_profile"] == profile]
        tags = Counter(tag for item in items for tag in item["tags"])
        folder_paths = sorted({(item["root"], item["folder"]) for item in all_items})
        folders = Counter((item["root"], item["folder"]) for item in items)
        facets = {"tags": [{"name": key, "count": value} for key, value in sorted(tags.items())],
                  "base_models": [{"name": key, "count": value} for key, value in sorted(Counter(x["base_model"] for x in all_items).items())],
                  "folders": [{"root": root, "path": folder, "count": folders[root, folder]} for root, folder in folder_paths],
                  "roots": [{"id": root, "path": root, "name": root} for root in sorted({x["root"] for x in all_items})]}
        query = str(params.get("q", "")).strip().casefold()
        if query:
            terms = query.split()
            items = [item for item in items if all(term in " ".join([item["display_name"], item["filename"], item["notes"], *item["tags"], *item["trigger_words"]]).casefold() for term in terms)]
        root = params.get("root")
        if root:
            items = [item for item in items if item["root"] == root]
        if "folder" in params:
            folder = str(params["folder"]).replace("\\", "/").strip("/")
            recursive = boolean("recursive") is not False
            items = [item for item in items if item["folder"] == folder or recursive and (not folder or item["folder"].startswith(folder + "/"))]
        collection = params.get("collection")
        if collection:
            if collection not in {"__all__", "__ungrouped__"} and collection not in {g["id"] for g in groups}:
                raise WorkflowError("收藏夹不存在")
            descendants = _descendants(groups, collection)
            items = [item for item in items if item["favorite"] and (collection == "__all__" or collection == "__ungrouped__" and not item["groupIds"] or bool(descendants.intersection(item["groupIds"])))]
        for key, field in (("favorite", "favorite"), ("has_preview", "has_preview"), ("bound", "bindings")):
            desired = boolean(key)
            if desired is not None:
                items = [item for item in items if bool(item[field]) == desired]
        in_configured = boolean("in_configured")
        if in_configured is not None:
            configured_filenames = {normalize_lora_path(filename).casefold() for filename in many("configured_filenames")}
            items = [item for item in items if (normalize_lora_path(item["filename"]).casefold() in configured_filenames) == in_configured]
        selected_tags = set(many("tag"))
        if selected_tags:
            items = [item for item in items if (bool(selected_tags.intersection(item["tags"])) if params.get("tag_mode") == "any" else selected_tags.issubset(item["tags"]))]
        excluded_tags = set(many("tag_exclude"))
        if excluded_tags:
            items = [item for item in items if not excluded_tags.intersection(item["tags"])]
        base_models = many("base_model")
        if base_models:
            items = [item for item in items if item["base_model"] in base_models]
        sort = params.get("sort", "name")
        sort_keys = {"name": lambda x: x["display_name"].casefold(), "modified": lambda x: x["modified"],
                     "size": lambda x: x["size"], "usage": lambda x: x["usage_count"], "favorite": lambda x: not x["favorite"]}
        if sort not in sort_keys or params.get("order", "asc") not in {"asc", "desc"}:
            raise WorkflowError("排序方式无效")
        items.sort(key=lambda x: (sort_keys[sort](x), x["filename"].casefold(), x["id"]), reverse=params.get("order") == "desc")
        total = len(items)
        if ids_only:
            return {"ids": [item["id"] for item in items],
                    "resources": [{"id": item["id"], "filename": item["filename"]} for item in items],
                    "total": total, "revision": self.store.data["revision"], **connection}
        try:
            page, limit = max(1, int(params.get("page", 1))), max(1, min(100, int(params.get("limit", 60))))
        except (TypeError, ValueError) as error:
            raise WorkflowError("分页参数无效") from error
        return {"items": [self.public(item) for item in items[(page - 1) * limit:page * limit]], "total": total,
                "page": page, "limit": limit, "total_pages": math.ceil(total / limit), "facets": facets,
                "groups": groups, "revision": self.store.data["revision"], **connection}

    async def update_item(self, resource_id: str, values: dict[str, Any], *, merge_mode: str = "replace") -> dict[str, Any]:
        self._ensure_open()
        if not self.store._retryable_write_failure:
            self.store.ensure_writable()
        if not values or set(values) - {"favorite", "tags", "notes", "default_strength", "groupIds"}:
            raise WorkflowError("不支持此 LoRA 修改")
        if merge_mode not in {"replace", "add", "remove"}:
            raise WorkflowError("批量修改模式无效")
        updates = copy.deepcopy(values)
        if "favorite" in updates and type(updates["favorite"]) is not bool:
            raise WorkflowError("favorite 必须是布尔值")
        if "tags" in updates:
            updates["tags"] = list(dict.fromkeys(tag.lower() for tag in _strings(updates["tags"], "tags", limit=256)))
            if any(len(tag) > 200 for tag in updates["tags"]):
                raise WorkflowError("标签不能超过 200 个字符")
        if "notes" in updates and (not isinstance(updates["notes"], str) or len(updates["notes"]) > 20000):
            raise WorkflowError("备注必须是不超过 20000 个字符的文本")
        if "default_strength" in updates:
            updates["default_strength"] = _strength(updates["default_strength"])
        if "groupIds" in updates:
            updates["groupIds"] = _strings(updates["groupIds"], "groupIds", limit=128)
        async with self._item_locks.setdefault(resource_id, asyncio.Lock()):
            item = await self.item(resource_id, refresh=True)
            def edit(data: dict[str, Any]) -> dict[str, Any]:
                record = self._remember(data, item)
                merged = copy.deepcopy(updates)
                for key in ("tags", "groupIds"):
                    if key in merged and merge_mode != "replace":
                        old, incoming = record.get(key, []), merged[key]
                        merged[key] = list(dict.fromkeys(old + incoming)) if merge_mode == "add" else [value for value in old if value not in incoming]
                if "groupIds" in merged and set(merged["groupIds"]) - set(record.get("groupIds", [])):
                    merged["favorite"] = True
                record.update(merged)
                return record
            saved = await self.store.change(edit, retry_write=True)
            item.update({key: copy.deepcopy(saved.get(key, default)) for key, default in
                         (("favorite", False), ("tags", []), ("notes", ""), ("groupIds", []), ("default_strength", 1.0))})
            item["configured"] = True
            return self.public(item)



    async def group(self, method: str, group_id: str = "", values: dict[str, Any] | None = None) -> dict[str, Any]:
        self._ensure_open()
        values = values or {}
        if set(values) - {"name", "parentId"}:
            raise WorkflowError("收藏夹参数无效")
        if values.get("parentId") is not None and not isinstance(values.get("parentId"), str):
            raise WorkflowError("父收藏夹 ID 无效")
        def edit(data: dict[str, Any]) -> None:
            groups = data["groups"]
            if method == "POST":
                groups.append({"id": "collection_" + uuid.uuid4().hex, "name": _name(values.get("name")), "parentId": values.get("parentId") or ""})
            else:
                found = next((g for g in groups if g["id"] == group_id), None)
                if found is None:
                    raise KeyError(group_id)
                if method == "DELETE":
                    removed = _descendants(groups, group_id)
                    data["groups"] = [g for g in groups if g["id"] not in removed]
                    for record in data["resources"].values():
                        record["groupIds"] = [key for key in record.get("groupIds", []) if key not in removed]
                else:
                    if "name" in values:
                        found["name"] = _name(values["name"])
                    if "parentId" in values:
                        found["parentId"] = values["parentId"] or ""
        await self.store.change(edit)
        return {"groups": copy.deepcopy(self.store.data["groups"]), "revision": self.store.data["revision"]}

    def character(self, character_id: str) -> dict[str, Any]:
        character = self.catalog.get(character_id)
        if character is None or character.get("section") != "character":
            raise WorkflowError("人物池条目不存在，请刷新人物池")
        return character

    async def binding_snapshot(self, *, allow_cached: bool = False) -> dict[str, Any]:
        data = copy.deepcopy(self.store.data)
        if not data["bindings"]:
            return {"revision": data["revision"], "bindings": {}}
        inventory = {item["id"]: item for item in await self.inventory(refresh=True, allow_cached=allow_cached)}
        bindings = data["bindings"]
        for binding in bindings.values():
            binding["character_missing"] = self.catalog.get(binding["character_id"]) is None
            for lora in binding["loras"]:
                item = inventory.get(lora["id"])
                if item:
                    changed = bool(lora.get("sha256") and lora["sha256"] != item["sha256"])
                    if not changed:
                        lora["trigger_words"] = item["trigger_words"]
                    lora["status"] = "changed" if changed else item["classification_status"]
                    lora["model_profile"] = item["model_profile"]
                else:
                    lora["status"] = "missing"
        return {"revision": data["revision"], "bindings": bindings, **(self._connection_state() if allow_cached else {})}

    async def set_binding(self, character_id: str, values: Any, *, append: bool = False) -> dict[str, Any]:
        if append:
            return (await self.append_binding(character_id, values))["binding"]
        self._ensure_open()
        self.store.ensure_writable()
        previous_binding = self.store.data["bindings"].get(character_id)
        character = self.catalog.get(character_id)
        if (character is None or character.get("section") != "character") and previous_binding is None:
            raise WorkflowError("人物池条目不存在，请刷新人物池")
        if not isinstance(values, list) or len(values) > 128:
            raise WorkflowError("人物绑定 LoRA 必须为数组，最多 128 项")
        async with self._binding_lock:
            inventory = {item["id"]: item for item in await self.inventory(refresh=True)} if values else {}
            previous = {item["id"]: item for item in self.store.data["bindings"].get(character_id, {}).get("loras", [])}
            loras, remembered, seen = [], [], set()
            for value in values:
                if not isinstance(value, dict) or not isinstance(value.get("id"), str) or value.get("id") not in inventory and value.get("id") not in previous:
                    raise WorkflowError("绑定 LoRA 不存在")
                if set(value) - {"id", "strength", "enabled", "rebind"}:
                    raise WorkflowError("人物绑定参数包含未知字段")
                rebind = value.get("rebind", False)
                if type(rebind) is not bool:
                    raise WorkflowError("重新绑定标记必须是布尔值")
                resource_id = value["id"]
                item, saved_lora = inventory.get(resource_id), previous.get(resource_id)
                if resource_id in seen:
                    raise WorkflowError("人物绑定不能包含重复 LoRA")
                seen.add(resource_id)
                valid = item is not None and item["classification_status"] == "recognized" and item["model_profile"] == "anima"
                changed = bool(saved_lora and item and saved_lora.get("sha256") and saved_lora["sha256"] != item["sha256"])
                if rebind and not valid:
                    raise WorkflowError("重新绑定需要当前有效且已明确分类的 Anima LoRA")
                if (not valid or changed) and saved_lora is None:
                    raise WorkflowError("人物绑定仅支持已明确分类的 Anima LoRA")
                enabled = value.get("enabled", saved_lora.get("enabled", True) if saved_lora else True)
                if type(enabled) is not bool:
                    raise WorkflowError("绑定启用状态必须是布尔值")
                if saved_lora and (not valid or changed) and not rebind:
                    if enabled and not saved_lora.get("enabled", True):
                        raise WorkflowError("失效绑定需要重新选择有效 LoRA 后才能启用")
                    loras.append({**copy.deepcopy(saved_lora), "enabled": enabled,
                                  "strength": _strength(value.get("strength", saved_lora["strength"]))})
                    continue
                loras.append({key: item[key] for key in ("id", "filename", "sha256", "trigger_words")}
                             | {"strength": _strength(value.get("strength", item["default_strength"])), "enabled": enabled})
                remembered.append(item)
            name = (character.get("title_zh") or character.get("title") or character_id) if character else previous_binding["name"]
            def edit(data: dict[str, Any]) -> dict[str, Any]:
                for item in remembered:
                    self._remember(data, item)
                binding = {"character_id": character_id, "name": name, "loras": loras}
                data["bindings"][character_id] = binding
                return binding
            return await self.store.change(edit)

    async def append_binding(self, character_id: str, values: Any) -> dict[str, Any]:
        """Add missing relationships atomically, without editing any existing row."""
        self._ensure_open()
        self.store.ensure_writable()
        if not isinstance(character_id, str) or not character_id:
            raise WorkflowError("人物 ID 必须是非空字符串")
        self.character(character_id)
        if not isinstance(values, list) or not values or len(values) > 128:
            raise WorkflowError("追加绑定 LoRA 必须为非空数组，最多 128 项")
        incoming = []
        seen = set()
        for value in values:
            if not isinstance(value, dict) or set(value) - {"id", "strength", "enabled"}:
                raise WorkflowError("追加绑定参数无效")
            resource_id = value.get("id")
            if not isinstance(resource_id, str) or not resource_id or resource_id in seen:
                raise WorkflowError("追加绑定必须包含有效且不重复的 LoRA ID")
            if "strength" in value:
                _strength(value["strength"])
            if type(value.get("enabled", True)) is not bool:
                raise WorkflowError("绑定启用状态必须是布尔值")
            seen.add(resource_id)
            incoming.append(copy.deepcopy(value))
        async with self._binding_lock:
            inventory = {item["id"]: item for item in await self.inventory(refresh=True)}

            def edit(data: dict[str, Any]) -> dict[str, Any]:
                character = self.character(character_id)
                previous = data["bindings"].get(character_id)
                existing = {row["id"] for row in previous["loras"]} if previous else set()
                additions = []
                for value in incoming:
                    if value["id"] in existing:
                        continue
                    item = inventory.get(value["id"])
                    if item is None or item["classification_status"] != "recognized" or item["model_profile"] != "anima":
                        raise WorkflowError("人物绑定仅支持当前有效且已明确分类的 Anima LoRA")
                    additions.append({key: copy.deepcopy(item[key]) for key in ("id", "filename", "sha256", "trigger_words")}
                                     | {"strength": _strength(value.get("strength", item["default_strength"])),
                                        "enabled": value.get("enabled", True)})
                if len(existing) + len(additions) > 128:
                    raise WorkflowError("每个人物最多绑定 128 个 LoRA")
                if additions:
                    binding = previous or {"character_id": character_id,
                                           "name": character.get("title_zh") or character.get("title") or character_id,
                                           "loras": []}
                    binding["loras"].extend(additions)
                    data["bindings"][character_id] = binding
                    for row in additions:
                        self._remember(data, inventory[row["id"]])
                return {"binding": data["bindings"][character_id], "added_count": len(additions),
                        "skipped_count": len(incoming) - len(additions)}

            return await self.store.change(edit, skip_unchanged=True)

    async def delete_binding(self, character_id: str) -> dict[str, Any]:
        self._ensure_open()
        await self.store.change(lambda data: data["bindings"].pop(character_id, None))
        return {"deleted": True, "revision": self.store.data["revision"]}

    async def create_job(self, payload: dict[str, Any], *, binding_strengths: dict[str, float] | None = None) -> dict[str, Any]:
        self._ensure_open()
        self.store.ensure_writable()
        ids = _strings(payload.get("ids"), "ids")
        action, values = payload.get("action"), payload.get("values", {})
        if not ids or not isinstance(action, str) or action not in {"favorite", "groups", "tags", "bind", "bind_characters", "strength"} or not isinstance(values, dict):
            raise WorkflowError("批量操作参数无效")
        if sum(job["status"] in {"queued", "running"} for job in self.jobs.values()) >= 4:
            raise WorkflowError("正在执行的批量操作过多，请稍后重试")
        values = copy.deepcopy(values)
        if action == "bind_characters":
            if set(values) - {"resource_id", "strength"} or not isinstance(values.get("resource_id"), str) or not values["resource_id"]:
                raise WorkflowError("批量人物绑定需要有效的 resource_id 和可选权重")
            resource = await self.item(values["resource_id"], refresh=True)
            if resource["classification_status"] != "recognized" or resource["model_profile"] != "anima":
                raise WorkflowError("人物绑定仅支持已明确分类的 Anima LoRA")
            values["strength"] = _strength(values.get("strength", resource["default_strength"]))
        elif action == "bind":
            if not isinstance(values.get("character_id"), str) or not values["character_id"]:
                raise WorkflowError("批量绑定需要有效的人物 ID")
            if binding_strengths is None:
                if "strength" in values:
                    binding_strengths = dict.fromkeys(ids, _strength(values["strength"]))
                else:
                    resources = {item["id"]: item for item in await self.inventory(refresh=True)}
                    binding_strengths = {key: _strength(resources.get(key, {}).get("default_strength", 1)) for key in ids}
        # Source validation can await the plugin while other requests enqueue work.
        self._ensure_open()
        if sum(job["status"] in {"queued", "running"} for job in self.jobs.values()) >= 4:
            raise WorkflowError("正在执行的批量操作过多，请稍后重试")
        # Snapshot IDs and values once; later filtering must not change targets.
        job_id = "job_" + uuid.uuid4().hex
        job = {"id": job_id, "status": "queued", "total": len(ids), "completed": 0, "succeeded": 0,
               "failed": 0, "results": [], "cancel_requested": False, "_ids": ids,
                "target_type": "character" if action == "bind_characters" else "resource",
                "_action": action, "_values": values,
                "_binding_strengths": copy.deepcopy(binding_strengths or {})}
        self.jobs[job_id] = job
        self._tasks[job_id] = asyncio.create_task(self._run_job(job))
        if len(self.jobs) > 100:
            for key in list(self.jobs):
                if len(self.jobs) <= 100:
                    break
                if self.jobs[key]["status"] in {"completed", "cancelled"}:
                    self.jobs.pop(key)
                    self._tasks.pop(key, None)
        return self.public(job)

    async def _run_job(self, job: dict[str, Any]) -> None:
        job["status"] = "running"
        targets = iter(job["_ids"])
        async def worker() -> None:
            while not job["cancel_requested"]:
                resource_id = next(targets, None)
                if resource_id is None:
                    return
                try:
                    action, values = job["_action"], job["_values"]
                    counts = {}
                    if action in {"bind", "bind_characters"}:
                        character_id = resource_id if action == "bind_characters" else values["character_id"]
                        lora_id = values["resource_id"] if action == "bind_characters" else resource_id
                        strength = values["strength"] if action == "bind_characters" else job["_binding_strengths"][resource_id]
                        result = await self.append_binding(character_id, [{"id": lora_id, "strength": strength}])
                        counts = {key: result[key] for key in ("added_count", "skipped_count")}
                    elif action in {"favorite", "strength"}:
                        key = "favorite" if action == "favorite" else "default_strength"
                        await self.update_item(resource_id, {key: values.get(key)})
                    else:
                        key = "groupIds" if action == "groups" else "tags"
                        incoming = _strings(values.get(key), key)
                        mode = values.get("mode", "add")
                        if mode not in {"add", "remove", "replace"}:
                            raise WorkflowError("批量修改模式无效")
                        await self.update_item(resource_id, {key: incoming}, merge_mode=mode)
                    job["succeeded"] += 1
                    job["results"].append({"id": resource_id, "target_type": job["target_type"], "status": "succeeded", **counts})
                except Exception as error:
                    job["failed"] += 1
                    job["results"].append({"id": resource_id, "target_type": job["target_type"], "status": "failed", "error": str(error)})
                finally:
                    job["completed"] += 1
        await asyncio.gather(worker(), worker())
        job["status"] = "cancelled" if job["cancel_requested"] and job["completed"] < job["total"] else "completed"

    async def retry_job(self, job_id: str) -> dict[str, Any]:
        job = self.jobs[job_id]
        if job["status"] in {"queued", "running"}:
            raise WorkflowError("操作仍在进行，请等待完成")
        failed = [item["id"] for item in job["results"] if item["status"] == "failed"]
        return await self.create_job({"ids": failed, "action": job["_action"], "values": job["_values"]},
                                     binding_strengths=job["_binding_strengths"])

    async def close(self) -> None:
        self._closing = True
        for job in self.jobs.values():
            job["cancel_requested"] = True
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)


def register_lora_routes(app: web.Application, service: LoraLibraryService) -> None:
    async def body(request: web.Request) -> dict[str, Any]:
        if request.content_type != "application/json" and not (request.content_type.startswith("application/") and request.content_type.endswith("+json")):
            raise web.HTTPUnsupportedMediaType(reason="请求必须使用 application/json")
        try:
            value = await request.json(loads=strict_json_loads)
        except (ValueError, UnicodeError, LookupError, RecursionError) as error:
            raise WorkflowError("请求 JSON 无效") from error
        if not isinstance(value, dict):
            raise WorkflowError("请求 JSON 必须是对象")
        return value

    async def listing(request: web.Request) -> web.Response:
        return web.json_response(await service.query(request.query))

    async def selection(request: web.Request) -> web.Response:
        query = (await body(request)).get("query", {})
        if not isinstance(query, dict):
            raise WorkflowError("query 必须是对象")
        return web.json_response(await service.query(query, ids_only=True))

    async def item(request: web.Request) -> web.Response:
        if request.method == "PATCH":
            value = await service.update_item(request.match_info["id"], await body(request))
        else:
            value = service.public(await service.item(request.match_info["id"], model_profile=request.query.get("model_profile", "all"), allow_cached=True))
        return web.json_response(value)

    async def preview(request: web.Request) -> web.Response:
        value = await service.item(request.match_info["id"], allow_cached=True)
        if not value.get("_preview"):
            raise KeyError(value["id"])
        content, content_type, cache_control = await service.client.lora_preview_asset(value["_preview"])
        return web.Response(body=content, headers={"Content-Type": content_type, "Cache-Control": cache_control})

    async def groups(request: web.Request) -> web.Response:
        if request.method == "GET":
            result = {"groups": service.store.data["groups"], "revision": service.store.data["revision"]}
        else:
            result = await service.group(request.method, request.match_info.get("id", ""), await body(request) if request.method != "DELETE" else {})
        return web.json_response(result)

    async def bindings(request: web.Request) -> web.Response:
        character_id = request.match_info.get("character_id", "")
        if request.method == "PUT":
            result = await service.set_binding(character_id, (await body(request)).get("loras"))
        elif request.method == "DELETE":
            result = await service.delete_binding(character_id)
        else:
            snapshot = await service.binding_snapshot(allow_cached=True)
            if character_id:
                result = snapshot["bindings"].get(character_id)
                if result is None:
                    character = service.character(character_id)
                    result = {"character_id": character_id, "name": character.get("title_zh") or character.get("title") or character_id, "loras": []}
            else:
                result = {**snapshot, "bindings": list(snapshot["bindings"].values())}
        return web.json_response(result)

    async def characters(request: web.Request) -> web.Response:
        try:
            page, limit = int(request.query.get("page", 1)), int(request.query.get("limit", 30))
        except ValueError as error:
            raise WorkflowError("分页参数无效") from error
        return web.json_response(service.catalog.search("character", query=request.query.get("q", ""), page=page, limit=limit))

    async def jobs(request: web.Request) -> web.Response:
        job_id = request.match_info.get("id")
        operation = request.match_info.get("operation")
        if not job_id:
            result = await service.create_job(await body(request))
        elif operation == "retry":
            result = await service.retry_job(job_id)
        else:
            job = service.jobs[job_id]
            if operation == "cancel":
                job["cancel_requested"] = True
            result = service.public(job)
        return web.json_response(result)

    prefix = "/api/lora-library"
    app.router.add_get(prefix, listing)
    app.router.add_post(prefix + "/selection", selection)
    app.router.add_get(prefix + "/items/{id}", item)
    app.router.add_patch(prefix + "/items/{id}", item)
    app.router.add_get(prefix + "/items/{id}/preview", preview)
    app.router.add_get(prefix + "/characters", characters)
    for path in (prefix + "/groups", "/api/lora-collections"):
        app.router.add_get(path, groups)
        app.router.add_post(path, groups)
        app.router.add_patch(path + "/{id}", groups)
        app.router.add_delete(path + "/{id}", groups)
    for path in (prefix + "/bindings", "/api/character-lora-bindings"):
        app.router.add_get(path, bindings)
        app.router.add_get(path + "/{character_id}", bindings)
        app.router.add_put(path + "/{character_id}", bindings)
        app.router.add_delete(path + "/{character_id}", bindings)
    app.router.add_post(prefix + "/jobs", jobs)
    app.router.add_get(prefix + "/jobs/{id}", jobs)
    app.router.add_post(prefix + "/jobs/{id}/{operation:cancel|retry}", jobs)
    async def cleanup(_: web.Application) -> None:
        await service.close()
    app.on_cleanup.append(cleanup)
