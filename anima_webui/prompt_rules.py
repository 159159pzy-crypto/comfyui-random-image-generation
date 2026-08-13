from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

from .persistence import backup_corrupt_file
from .workflow import WorkflowError


SCOPES = ("positive", "negative", "lora")
MAX_CUSTOM_RULES = 512
MAX_RULE_TEXT = 500

FIELD_SCOPES = {
    "quality_prompt": "positive",
    "manual_artist": "positive",
    "fixed_character": "positive",
    "fixed_clothing": "positive",
    "fixed_pose": "positive",
    "fixed_background": "positive",
    "fixed_expression": "positive",
    "extra_prompt": "positive",
    "negative_prompt": "negative",
    "custom_prompt": "positive",
    "resolved_prompt": "positive",
    "lora_trigger": "lora",
}

BUILTIN_RULES = (
    ("format-separators", "规范分隔符", "统一换行、全角逗号、空项与逗号空格"),
    ("lowercase-tags", "Tag 转小写", "仅转换可安全识别的 tag，不改自然语言句子"),
    ("underscores-to-spaces", "下划线转空格", "普通 tag 使用空格，score_* 保留下划线"),
    (
        "score-format",
        "规范分数 Tag",
        "把 score 9、score 8 up 等形式转换为 score_9、score_8_up",
    ),
    ("year-format", "规范年份 Tag", "把独立年份转换为 year YYYY"),
    ("artist-prefix", "规范画师 Tag", "画师字段统一为 @artist"),
    ("deduplicate", "移除重复 Tag", "大小写不敏感并保留首次出现位置"),
)
BUILTIN_IDS = {item[0] for item in BUILTIN_RULES}

_SCORE_RE = re.compile(r"^score[ _-]?([1-9])(?:[ _-]+(up))?$", re.IGNORECASE)
_NORMALIZED_SCORE_RE = re.compile(r"^score_[1-9](?:_up)?$", re.IGNORECASE)
_YEAR_RE = re.compile(r"^(?:year\s+)?((?:19|20)\d{2})$", re.IGNORECASE)
_WEIGHT_RE = re.compile(r"^\((.*):(-?\d+(?:\.\d+)?)\)$", re.DOTALL)
_TAG_SHAPE_RE = re.compile(r"^[\w@+\\/\-;' ]+$", re.UNICODE)
_SENTENCE_PUNCTUATION_RE = re.compile(r"[.!?。！？]")


def _split_top_level(value: str) -> list[str]:
    text = value.replace("，", ",").replace("､", ",").replace("\r", "\n")
    result: list[str] = []
    current: list[str] = []
    stack: list[str] = []
    quote = ""
    escaped = False
    pairs = {"(": ")", "[": "]", "{": "}"}
    for char in text:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            current.append(char)
            escaped = True
            continue
        if quote:
            current.append(char)
            if char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
            current.append(char)
            continue
        if char in pairs:
            stack.append(pairs[char])
            current.append(char)
            continue
        if stack and char == stack[-1]:
            stack.pop()
            current.append(char)
            continue
        if not stack and char in {",", "\n"}:
            part = "".join(current).strip()
            if part:
                result.append(part)
            current = []
            continue
        current.append(char)
    part = "".join(current).strip()
    if part:
        result.append(part)
    return result


def _looks_like_tag(value: str) -> bool:
    if not value or _SENTENCE_PUNCTUATION_RE.search(value):
        return False
    if len(value.split()) > 12:
        return False
    return bool(_TAG_SHAPE_RE.fullmatch(value))


class PromptRuleStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.disabled_builtins: set[str] = set()
        self.custom_rules: list[dict[str, Any]] = []
        self.load_warnings: list[str] = []
        self._lock = asyncio.Lock()
        self.reload()

    def reload(self) -> None:
        self.load_warnings = []
        if not self.path.is_file():
            self.disabled_builtins = set()
            self.custom_rules = []
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != 1:
                raise WorkflowError("提示词规则文件版本无效")
            disabled = payload.get("disabledBuiltins", [])
            rules = payload.get("rules", [])
            if not isinstance(disabled, list) or not isinstance(rules, list):
                raise WorkflowError("提示词规则文件内容无效")
            self.disabled_builtins = {
                str(value) for value in disabled if str(value) in BUILTIN_IDS
            }
            if len(rules) > MAX_CUSTOM_RULES:
                raise WorkflowError(f"自定义提示词规则不能超过 {MAX_CUSTOM_RULES} 条")
            self.custom_rules = [self._normalize_custom(item, existing=True) for item in rules]
        except (OSError, json.JSONDecodeError, WorkflowError) as error:
            backup = backup_corrupt_file(self.path)
            self.load_warnings.append(
                f"提示词规则文件无法读取({error}),已备份为 {backup.name} 并以默认规则启动"
            )
            self.disabled_builtins = set()
            self.custom_rules = []

    def list(self) -> dict[str, Any]:
        builtins = [
            {
                "id": rule_id,
                "kind": "builtin",
                "name": name,
                "description": description,
                "enabled": rule_id not in self.disabled_builtins,
            }
            for rule_id, name, description in BUILTIN_RULES
        ]
        return {
            "items": [*builtins, *copy.deepcopy(self.custom_rules)],
            "count": len(builtins) + len(self.custom_rules),
        }

    async def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            if len(self.custom_rules) >= MAX_CUSTOM_RULES:
                raise WorkflowError(f"自定义提示词规则不能超过 {MAX_CUSTOM_RULES} 条")
            item = self._normalize_custom(
                {**payload, "id": f"prompt_rule_{uuid.uuid4().hex[:16]}"}
            )
            self._ensure_unique(item)
            self.custom_rules.append(item)
            await self._save()
        return copy.deepcopy(item)

    async def update(self, rule_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            if rule_id in BUILTIN_IDS:
                unknown = set(payload) - {"enabled"}
                if unknown or not isinstance(payload.get("enabled"), bool):
                    raise WorkflowError("内置规则只能修改 enabled")
                if payload["enabled"]:
                    self.disabled_builtins.discard(rule_id)
                else:
                    self.disabled_builtins.add(rule_id)
                await self._save()
                return next(item for item in self.list()["items"] if item["id"] == rule_id)
            index = next(
                (index for index, item in enumerate(self.custom_rules) if item["id"] == rule_id),
                None,
            )
            if index is None:
                raise KeyError(rule_id)
            item = self._normalize_custom(
                {**self.custom_rules[index], **payload, "id": rule_id}, existing=True
            )
            self._ensure_unique(item, excluding_id=rule_id)
            self.custom_rules[index] = item
            await self._save()
        return copy.deepcopy(item)

    async def delete(self, rule_id: str) -> bool:
        if rule_id in BUILTIN_IDS:
            raise WorkflowError("内置规则不能删除,可以停用")
        async with self._lock:
            previous = len(self.custom_rules)
            self.custom_rules = [item for item in self.custom_rules if item["id"] != rule_id]
            if len(self.custom_rules) == previous:
                return False
            await self._save()
        return True

    def normalize_fields(
        self,
        fields: Any,
        managed_triggers: Any | None = None,
    ) -> dict[str, Any]:
        if not isinstance(fields, dict):
            raise WorkflowError("fields 必须是对象")
        if len(fields) > len(FIELD_SCOPES):
            raise WorkflowError("提示词字段过多")
        raw_managed = managed_triggers or []
        normalized_managed = self._normalize_lora_list(raw_managed)
        protected = list(
            dict.fromkeys(
                [
                    *(item.strip() for item in raw_managed if isinstance(item, str) and item.strip()),
                    *normalized_managed,
                ]
            )
        )
        result: dict[str, str] = {}
        changes: list[dict[str, Any]] = []
        for field, raw in fields.items():
            if field not in FIELD_SCOPES:
                raise WorkflowError(f"不支持规范字段: {field}")
            if not isinstance(raw, str):
                raise WorkflowError(f"fields.{field} 必须是字符串")
            if len(raw) > 20000:
                raise WorkflowError(f"fields.{field} 不能超过 20000 个字符")
            normalized, rules = self.normalize_text(
                raw,
                FIELD_SCOPES[field],
                artist_field=field == "manual_artist",
                protected_lora_words=protected if field == "extra_prompt" else [],
            )
            result[field] = normalized
            if normalized != raw:
                changes.append(
                    {
                        "field": field,
                        "before": raw,
                        "after": normalized,
                        "rules": sorted(rules),
                    }
                )
        return {"fields": result, "changes": changes, "changed": bool(changes)}

    def normalize_settings(self, settings: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        normalized = copy.deepcopy(settings)
        original_managed = normalized.get("lora_managed_triggers", [])
        managed = self._normalize_lora_list(original_managed)
        normalized["lora_managed_triggers"] = managed
        fields = {
            field: normalized.get(field, "")
            for field in FIELD_SCOPES
            if field in normalized and field not in {"custom_prompt", "resolved_prompt", "lora_trigger"}
        }
        result = self.normalize_fields(fields, original_managed)
        normalized.update(result["fields"])
        if managed != original_managed:
            result["changes"].append(
                {
                    "field": "lora_managed_triggers",
                    "before": original_managed,
                    "after": managed,
                    "rules": ["lora-scope"],
                }
            )
        return normalized, result["changes"]

    def normalize_text(
        self,
        value: str,
        scope: str,
        *,
        artist_field: bool = False,
        protected_lora_words: list[str] | None = None,
    ) -> tuple[str, set[str]]:
        if scope not in SCOPES:
            raise WorkflowError(f"无效提示词作用域: {scope}")
        protected = {item.casefold() for item in protected_lora_words or []}
        parts = _split_top_level(value)
        rules_used: set[str] = set()
        result: list[str] = []
        seen: set[str] = set()
        for raw_part in parts:
            part_scope = "lora" if raw_part.casefold() in protected else scope
            part, used = self._normalize_part(raw_part, part_scope, artist_field=artist_field)
            rules_used.update(used)
            if not part:
                continue
            identity = part.casefold()
            if "deduplicate" not in self.disabled_builtins:
                if identity in seen:
                    rules_used.add("deduplicate")
                    continue
                seen.add(identity)
            result.append(part)
        separator = ", " if "format-separators" not in self.disabled_builtins else ","
        normalized = separator.join(result)
        if normalized != value and "format-separators" not in self.disabled_builtins:
            rules_used.add("format-separators")
        return normalized, rules_used

    def _normalize_part(
        self, raw_part: str, scope: str, *, artist_field: bool = False
    ) -> tuple[str, set[str]]:
        part = raw_part.strip()
        used: set[str] = set()
        custom = next(
            (
                item
                for item in reversed(self.custom_rules)
                if item["enabled"]
                and scope in item["scopes"]
                and item["from"].casefold() == part.casefold()
            ),
            None,
        )
        if custom is not None:
            part = custom["to"]
            used.add(custom["id"])

        weight = _WEIGHT_RE.fullmatch(part)
        if weight:
            inner, inner_used = self._normalize_part(
                weight.group(1), scope, artist_field=artist_field
            )
            used.update(inner_used)
            return f"({inner}:{weight.group(2)})", used

        if scope == "lora":
            return part, used

        if artist_field and "artist-prefix" not in self.disabled_builtins:
            clean = part
            if clean.startswith("@"):
                clean = clean[1:].strip()
            elif clean.lower().startswith("by "):
                clean = clean[3:].strip()
            if clean:
                next_part = f"@{clean}"
                if next_part != part:
                    used.add("artist-prefix")
                part = next_part

        score = _SCORE_RE.fullmatch(part)
        if score and "score-format" not in self.disabled_builtins:
            suffix = "_up" if score.group(2) else ""
            next_part = f"score_{score.group(1)}{suffix}"
            if next_part != part:
                used.add("score-format")
            part = next_part
        else:
            year = _YEAR_RE.fullmatch(part)
            if year and "year-format" not in self.disabled_builtins:
                next_part = f"year {year.group(1)}"
                if next_part != part:
                    used.add("year-format")
                part = next_part

        if _looks_like_tag(part):
            if (
                "underscores-to-spaces" not in self.disabled_builtins
                and not _NORMALIZED_SCORE_RE.fullmatch(part)
            ):
                next_part = re.sub(r"_+", " ", part)
                if next_part != part:
                    used.add("underscores-to-spaces")
                part = next_part
            if "lowercase-tags" not in self.disabled_builtins:
                next_part = part.lower()
                if next_part != part:
                    used.add("lowercase-tags")
                part = next_part
            part = re.sub(r"\s+", " ", part).strip()
        return part, used

    def _normalize_lora_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            raise WorkflowError("managedTriggers 必须是数组")
        if len(value) > 256:
            raise WorkflowError("managedTriggers 不能超过 256 个")
        result: list[str] = []
        seen: set[str] = set()
        for index, raw in enumerate(value):
            if not isinstance(raw, str):
                raise WorkflowError(f"managedTriggers[{index}] 必须是字符串")
            part, _ = self._normalize_part(raw.strip(), "lora")
            if len(part) > MAX_RULE_TEXT:
                raise WorkflowError(f"managedTriggers[{index}] 过长")
            identity = part.casefold()
            if part and identity not in seen:
                result.append(part)
                seen.add(identity)
        return result

    def _normalize_custom(self, payload: Any, existing: bool = False) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise WorkflowError("自定义提示词规则必须是对象")
        rule_id = str(payload.get("id") or "").strip()
        source = str(payload.get("from") or "").strip()
        target = str(payload.get("to") or "").strip()
        scopes = payload.get("scopes", ["positive", "negative"])
        enabled = payload.get("enabled", True)
        if not rule_id or not rule_id.startswith("prompt_rule_"):
            raise WorkflowError("自定义提示词规则 ID 无效")
        if not source or not target:
            raise WorkflowError("替换原词和规范词不能为空")
        if len(source) > MAX_RULE_TEXT or len(target) > MAX_RULE_TEXT:
            raise WorkflowError(f"替换词不能超过 {MAX_RULE_TEXT} 个字符")
        if len(_split_top_level(source)) != 1 or len(_split_top_level(target)) != 1:
            raise WorkflowError("自定义规则只能精确替换单个 Tag")
        if not isinstance(scopes, list) or not scopes:
            raise WorkflowError("自定义规则至少需要一个作用域")
        normalized_scopes = list(dict.fromkeys(str(item) for item in scopes))
        if any(item not in SCOPES for item in normalized_scopes):
            raise WorkflowError("自定义规则作用域无效")
        if not isinstance(enabled, bool):
            raise WorkflowError("自定义规则 enabled 必须是布尔值")
        return {
            "id": rule_id,
            "kind": "custom",
            "from": source,
            "to": target,
            "scopes": normalized_scopes,
            "enabled": enabled,
        }

    def _ensure_unique(self, item: dict[str, Any], excluding_id: str | None = None) -> None:
        source = item["from"].casefold()
        scopes = set(item["scopes"])
        if any(
            existing["id"] != excluding_id
            and existing["from"].casefold() == source
            and scopes.intersection(existing["scopes"])
            for existing in self.custom_rules
        ):
            raise WorkflowError("相同作用域已经存在该原词的自定义规则")

    async def _save(self) -> None:
        payload = json.dumps(
            {
                "version": 1,
                "disabledBuiltins": sorted(self.disabled_builtins),
                "rules": self.custom_rules,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n"
        await asyncio.to_thread(self._write_text, payload)

    def _write_text(self, text: str) -> None:
        fd, temp_name = tempfile.mkstemp(
            prefix="prompt-rules-", suffix=".json", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
