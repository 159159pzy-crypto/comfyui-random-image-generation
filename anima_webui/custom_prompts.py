from __future__ import annotations

import copy
import csv
import io
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .catalog import SECTIONS, CatalogError, PromptCatalog, normalize_text


MAX_GROUPS_PER_SECTION = 128
MAX_IMPORT_ROWS = 2000
IMPORT_FIELDS = (
    "section", "title", "prompt", "subtitle", "gender", "hair", "eye",
    "copyright", "groups", "categories", "traits",
)
TEMPLATE_EXAMPLES = {
    "character": {
        "title": "示例角色", "prompt": "1girl, silver hair, blue eyes, looking at viewer",
        "subtitle": "角色模板示例", "gender": "female", "hair": "silver",
        "eye": "blue", "copyright": "原创角色", "groups": ["常用角色"],
        "categories": ["女性角色"], "traits": ["looking at viewer"],
    },
    "clothing": {
        "title": "休闲连帽衫", "prompt": "oversized hoodie, pleated skirt, casual outfit",
        "subtitle": "服装模板示例", "gender": "unknown", "hair": "", "eye": "",
        "copyright": "", "groups": ["日常服装"], "categories": ["casual"],
        "traits": ["hoodie", "pleated skirt"],
    },
    "pose": {
        "title": "站立挥手", "prompt": "standing, waving, one hand raised",
        "subtitle": "姿势模板示例", "gender": "unknown", "hair": "", "eye": "",
        "copyright": "", "groups": ["常用姿势"], "categories": ["Standing & Dynamic"],
        "traits": ["waving", "hand raised"],
    },
    "background": {
        "title": "樱花街道", "prompt": "cherry blossom street, spring daylight, soft bokeh",
        "subtitle": "背景模板示例", "gender": "unknown", "hair": "", "eye": "",
        "copyright": "", "groups": ["户外背景"], "categories": ["outdoor"],
        "traits": ["cherry blossoms", "street"],
    },
    "expression": {
        "title": "期待", "prompt": "expectant, sparkling eyes",
        "subtitle": "表情模板示例", "gender": "unknown", "hair": "", "eye": "",
        "copyright": "", "groups": ["常用表情"], "categories": ["愉悦"],
        "traits": ["sparkling eyes"],
    },
}


class CustomPromptStore:
    def __init__(self, path: str | Path, catalog: PromptCatalog):
        self.path = Path(path)
        self.catalog = catalog
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.items: list[dict[str, Any]] = []
        self.groups: dict[str, list[dict[str, Any]]] = {section: [] for section in SECTIONS}
        self.reload()

    def reload(self) -> None:
        if not self.path.is_file():
            self.items = []
            self.groups = {section: [] for section in SECTIONS}
            self.catalog.set_custom_items([])
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CatalogError(f"自定义提示词文件无法读取: {self.path.name}") from error
        raw_groups = payload.get("groups", {}) if isinstance(payload, dict) else {}
        self.groups = {
            section: self._normalize_groups(raw_groups.get(section, []))
            for section in SECTIONS
        }
        raw_items = payload.get("items", []) if isinstance(payload, dict) else []
        self.items = [self._normalize(item) for item in raw_items if isinstance(item, dict)]
        self._remove_unknown_group_ids()
        self.catalog.set_custom_items(self.items)

    def list(self, section: str | None = None, group_id: str | None = None) -> list[dict[str, Any]]:
        if section and section not in SECTIONS:
            raise CatalogError(f"不支持的随机池: {section}")
        values = self.items if not section else [item for item in self.items if item["section"] == section]
        if group_id:
            values = [item for item in values if group_id in item.get("groupIds", [])]
        return [copy.deepcopy(item) for item in values]

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        item = self._normalize({**payload, "id": f"custom:{uuid.uuid4().hex}"})
        self._validate_group_ids(item["section"], item["groupIds"])
        self.items.append(item)
        self._save()
        self.catalog.set_custom_items(self.items)
        return copy.deepcopy(item)

    def update(self, item_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        index = next((index for index, item in enumerate(self.items) if item["id"] == item_id), None)
        if index is None:
            raise KeyError(item_id)
        item = self._normalize({**self.items[index], **payload, "id": item_id})
        self._validate_group_ids(item["section"], item["groupIds"])
        self.items[index] = item
        self._save()
        self.catalog.set_custom_items(self.items)
        return copy.deepcopy(item)

    def delete(self, item_id: str) -> bool:
        old_length = len(self.items)
        self.items = [item for item in self.items if item["id"] != item_id]
        if len(self.items) == old_length:
            return False
        self._save()
        self.catalog.set_custom_items(self.items)
        return True

    def list_groups(self, section: str) -> dict[str, Any]:
        self._check_section(section)
        groups = copy.deepcopy(self.groups[section])
        counts = {
            group["id"]: sum(group["id"] in item.get("groupIds", []) for item in self.items if item["section"] == section)
            for group in groups
        }
        exclusive_counts = {
            group["id"]: sum(
                item["section"] == section
                and group["id"] in item.get("groupIds", [])
                and len(item.get("groupIds", [])) == 1
                for item in self.items
            )
            for group in groups
        }
        return {
            "groups": [
                {
                    **group,
                    "count": counts[group["id"]],
                    "exclusiveCount": exclusive_counts[group["id"]],
                }
                for group in groups
            ]
        }

    def create_group(self, section: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._check_section(section)
        name = self._group_name(payload.get("name"))
        if len(self.groups[section]) >= MAX_GROUPS_PER_SECTION:
            raise CatalogError("自定义分组数量已达到上限")
        self._ensure_unique_group_name(section, name)
        group = {"id": f"custom_group_{uuid.uuid4().hex[:12]}", "name": name}
        self.groups[section].append(group)
        self._save()
        return {"group": copy.deepcopy(group), **self.list_groups(section)}

    def update_group(self, section: str, group_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._check_section(section)
        group = next((item for item in self.groups[section] if item["id"] == group_id), None)
        if group is None:
            raise KeyError(group_id)
        name = self._group_name(payload.get("name"))
        self._ensure_unique_group_name(section, name, except_id=group_id)
        group["name"] = name
        self._save()
        return self.list_groups(section)

    def delete_group(
        self, section: str, group_id: str, delete_items: bool = False
    ) -> dict[str, Any]:
        self._check_section(section)
        if not any(item["id"] == group_id for item in self.groups[section]):
            raise KeyError(group_id)
        self.groups[section] = [item for item in self.groups[section] if item["id"] != group_id]
        deleted_item_ids: list[str] = []
        detached_item_count = 0
        next_items: list[dict[str, Any]] = []
        for item in self.items:
            group_ids = list(item.get("groupIds", []))
            if item["section"] != section or group_id not in group_ids:
                next_items.append(item)
                continue
            remaining = [value for value in group_ids if value != group_id]
            if delete_items and not remaining:
                deleted_item_ids.append(item["id"])
                continue
            item["groupIds"] = remaining
            detached_item_count += 1
            next_items.append(item)
        self.items = next_items
        self._save()
        self.catalog.set_custom_items(self.items)
        return {
            **self.list_groups(section),
            "deletedItemIds": deleted_item_ids,
            "deletedItemCount": len(deleted_item_ids),
            "detachedItemCount": detached_item_count,
        }

    def template(self, section: str, file_format: str) -> tuple[str, str, bytes]:
        self._check_section(section)
        example = {"section": section, **copy.deepcopy(TEMPLATE_EXAMPLES[section])}
        filename = f"custom-prompts-{section}-template"
        if file_format == "json":
            body = json.dumps({"version": 1, "items": [example]}, ensure_ascii=False, indent=2) + "\n"
            return f"{filename}.json", "application/json", body.encode("utf-8")
        if file_format == "csv":
            buffer = io.StringIO(newline="")
            writer = csv.DictWriter(buffer, fieldnames=IMPORT_FIELDS)
            writer.writeheader()
            writer.writerow({
                **example,
                "groups": "|".join(example["groups"]),
                "categories": "|".join(example["categories"]),
                "traits": "|".join(example["traits"]),
            })
            return f"{filename}.csv", "text/csv", ("\ufeff" + buffer.getvalue()).encode("utf-8")
        raise CatalogError("导入模板只支持 json 或 csv")

    def preview_import(self, file_format: str, content: str, section: str) -> dict[str, Any]:
        self._check_section(section)
        raw_items = self._parse_import(file_format, content)
        if len(raw_items) > MAX_IMPORT_ROWS:
            raise CatalogError(f"单次最多导入 {MAX_IMPORT_ROWS} 项")
        rows: list[dict[str, Any]] = []
        pending_names: dict[str, set[str]] = {section: set() for section in SECTIONS}
        import_keys: set[tuple[str, str]] = set()
        existing_custom = {(item["section"], normalize_text(item["title"])): item for item in self.items}
        builtin_names = {
            (section, normalize_text(item["title"]))
            for section in SECTIONS for item in self.catalog.all_items(section) if item.get("builtin")
        }
        for row_number, raw in enumerate(raw_items, 2 if file_format == "csv" else 1):
            try:
                prepared, group_names = self._prepare_import_item(raw)
                if prepared["section"] != section:
                    raise CatalogError("导入文件包含其他随机池条目，只能导入当前池")
                key = (prepared["section"], normalize_text(prepared["title"]))
                if key in builtin_names:
                    raise CatalogError("名称与内置条目冲突，内置条目不可覆盖")
                if key in import_keys:
                    raise CatalogError("同一导入文件中存在重名项")
                import_keys.add(key)
                conflict = existing_custom.get(key)
                status = "conflict" if conflict else "new"
                for name in group_names:
                    if not self._group_by_name(prepared["section"], name):
                        pending_names[prepared["section"]].add(name)
                rows.append({
                    "row": row_number, "status": status, "action": "skip" if conflict else "create",
                    "existingId": conflict["id"] if conflict else "", "item": prepared, "groups": group_names, "error": "",
                })
            except (CatalogError, ValueError) as error:
                rows.append({"row": row_number, "status": "error", "action": "skip", "item": raw, "groups": [], "error": str(error)})
        return {
            "rows": rows,
            "summary": {name: sum(row["status"] == name for row in rows) for name in ("new", "conflict", "error")},
            "newGroups": {section: sorted(values) for section, values in pending_names.items() if values},
        }

    def commit_import(self, rows: Any, section: str, target_group_ids: Any = None) -> dict[str, Any]:
        self._check_section(section)
        if not isinstance(rows, list) or len(rows) > MAX_IMPORT_ROWS:
            raise CatalogError("导入确认数据无效")
        if target_group_ids is None:
            target_group_ids = []
        if not isinstance(target_group_ids, list) or any(not isinstance(value, str) for value in target_group_ids):
            raise CatalogError("目标分组必须是数组")
        target_group_ids = list(dict.fromkeys(value.strip() for value in target_group_ids if value.strip()))
        self._validate_group_ids(section, target_group_ids)
        next_items = copy.deepcopy(self.items)
        next_groups = copy.deepcopy(self.groups)
        imported = updated = skipped = 0
        builtin_names = {
            (section, normalize_text(item["title"]))
            for section in SECTIONS for item in self.catalog.all_items(section) if item.get("builtin")
        }
        for raw_row in rows:
            if not isinstance(raw_row, dict):
                raise CatalogError("导入行无效")
            action = str(raw_row.get("action") or "skip")
            if action == "skip":
                skipped += 1
                continue
            if action not in {"create", "overwrite"}:
                raise CatalogError("导入操作无效")
            prepared, embedded_group_names = self._prepare_import_item(raw_row.get("item") or {})
            if prepared["section"] != section:
                raise CatalogError("导入文件包含其他随机池条目，只能导入当前池")
            group_names = self._list_value(raw_row.get("groups")) or embedded_group_names
            group_names = [self._group_name(value) for value in group_names]
            group_ids: list[str] = list(target_group_ids)
            for name in group_names:
                group = next((item for item in next_groups[section] if normalize_text(item["name"]) == normalize_text(name)), None)
                if group is None:
                    if len(next_groups[section]) >= MAX_GROUPS_PER_SECTION:
                        raise CatalogError(f"{section} 自定义分组数量已达到上限")
                    group = {"id": f"custom_group_{uuid.uuid4().hex[:12]}", "name": name}
                    next_groups[section].append(group)
                if group["id"] not in group_ids:
                    group_ids.append(group["id"])
            key = (section, normalize_text(prepared["title"]))
            if key in builtin_names:
                raise CatalogError("名称与内置条目冲突，内置条目不可覆盖")
            position = next((index for index, item in enumerate(next_items) if (item["section"], normalize_text(item["title"])) == key), None)
            if position is not None and action != "overwrite":
                raise CatalogError(f"导入项已存在: {prepared['title']}")
            if position is None and action == "overwrite":
                raise CatalogError(f"没有可覆盖的自定义项: {prepared['title']}")
            item_id = next_items[position]["id"] if position is not None else f"custom:{uuid.uuid4().hex}"
            item = self._normalize({**prepared, "id": item_id, "groupIds": group_ids})
            if position is None:
                next_items.append(item)
                imported += 1
            else:
                next_items[position] = item
                updated += 1
        self._write(next_groups, next_items)
        self.groups = next_groups
        self.items = next_items
        self.catalog.set_custom_items(self.items)
        return {"imported": imported, "updated": updated, "skipped": skipped, "total": len(self.items)}

    def _normalize(self, payload: dict[str, Any]) -> dict[str, Any]:
        section = str(payload.get("section") or "")
        self._check_section(section)
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
        categories = self._list_value(payload.get("categories"))
        traits = self._list_value(payload.get("traits"))
        group_ids = self._list_value(payload.get("groupIds"))
        if len(categories) > 32 or len(traits) > 128:
            raise CatalogError("自定义项分类或特征过多")
        return {
            "id": str(payload.get("id") or ""), "section": section, "title": title,
            "subtitle": subtitle, "prompt": prompt, "gender": gender,
            "hair": str(payload.get("hair") or "").strip().lower() if section == "character" else "",
            "eye": str(payload.get("eye") or "").strip().lower() if section == "character" else "",
            "copyright": str(payload.get("copyright") or "").strip() if section == "character" else "",
            "categories": categories, "traits": traits, "groupIds": group_ids,
        }

    def _prepare_import_item(self, raw: Any) -> tuple[dict[str, Any], list[str]]:
        if not isinstance(raw, dict):
            raise CatalogError("导入行必须是对象")
        groups = self._list_value(raw.get("groups"))
        item = self._normalize({**raw, "id": "custom:preview", "groupIds": []})
        return {key: value for key, value in item.items() if key not in {"id", "groupIds"}}, [self._group_name(value) for value in groups]

    def _parse_import(self, file_format: str, content: str) -> list[dict[str, Any]]:
        if not isinstance(content, str) or len(content.encode("utf-8")) > 5 * 1024 * 1024:
            raise CatalogError("导入文件无效或超过 5 MB")
        if file_format == "json":
            try:
                payload = json.loads(content.lstrip("\ufeff"))
            except json.JSONDecodeError as error:
                raise CatalogError(f"JSON 无法解析: 第 {error.lineno} 行") from error
            values = payload.get("items") if isinstance(payload, dict) else payload
            if not isinstance(values, list):
                raise CatalogError("JSON 模板必须包含 items 数组")
            return [item for item in values]
        if file_format == "csv":
            try:
                return [dict(row) for row in csv.DictReader(io.StringIO(content.lstrip("\ufeff")))]
            except csv.Error as error:
                raise CatalogError(f"CSV 无法解析: {error}") from error
        raise CatalogError("导入文件只支持 json 或 csv")

    def _normalize_groups(self, values: Any) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in values if isinstance(values, list) else []:
            if not isinstance(raw, dict):
                continue
            group_id = str(raw.get("id") or "").strip()
            name = str(raw.get("name") or "").strip()
            if group_id and name and group_id not in seen:
                result.append({"id": group_id, "name": name[:100]})
                seen.add(group_id)
        return result[:MAX_GROUPS_PER_SECTION]

    def _remove_unknown_group_ids(self) -> None:
        valid = {section: {group["id"] for group in groups} for section, groups in self.groups.items()}
        for item in self.items:
            item["groupIds"] = [value for value in item.get("groupIds", []) if value in valid[item["section"]]]

    @staticmethod
    def _list_value(value: Any) -> list[str]:
        if isinstance(value, list):
            values = value
        elif isinstance(value, str):
            values = value.split("|") if "|" in value else ([] if not value.strip() else [value])
        elif value in (None, ""):
            values = []
        else:
            raise CatalogError("多值字段必须是数组或使用 | 分隔的文本")
        return list(dict.fromkeys(str(item).strip() for item in values if str(item).strip()))

    def _validate_group_ids(self, section: str, group_ids: list[str]) -> None:
        valid = {group["id"] for group in self.groups[section]}
        if any(group_id not in valid for group_id in group_ids):
            raise CatalogError("自定义项包含不存在的分组")

    def _group_by_name(self, section: str, name: str) -> dict[str, Any] | None:
        key = normalize_text(name)
        return next((group for group in self.groups[section] if normalize_text(group["name"]) == key), None)

    def _ensure_unique_group_name(self, section: str, name: str, except_id: str = "") -> None:
        if any(group["id"] != except_id and normalize_text(group["name"]) == normalize_text(name) for group in self.groups[section]):
            raise CatalogError("同一随机池内不能有重名的自定义分组")

    @staticmethod
    def _group_name(value: Any) -> str:
        name = str(value or "").strip()
        if not name or len(name) > 100:
            raise CatalogError("自定义分组名称需要 1-100 个字符")
        return name

    @staticmethod
    def _check_section(section: str) -> None:
        if section not in SECTIONS:
            raise CatalogError(f"不支持的随机池: {section}")

    def _save(self) -> None:
        self._write(self.groups, self.items)

    def _write(self, groups: dict[str, list[dict[str, Any]]], items: list[dict[str, Any]]) -> None:
        payload = {"version": 3, "groups": groups, "items": items}
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
