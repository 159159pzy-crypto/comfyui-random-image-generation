"""Local, lossless classification of custom character prompt tokens.

Only comparison keys are normalized. Unknown words and ambiguous dictionary
matches remain reviewable; neither token position nor a name-like shape is
evidence of identity. This module never loads model weights or uses a network.
"""
from __future__ import annotations

import copy
import csv
import logging
import re
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

from .character_clothing_tags import is_character_clothing_tag


logger = logging.getLogger(__name__)
ROLES = frozenset({"trigger", "feature", "unknown"})
SOURCES = frozenset({"dictionary", "metadata", "candidate", "unknown", "manual", "explicit", "conflict"})
MANUAL_SOURCES = frozenset({"manual", "explicit"})
CHARACTER_FIELDS = (
    "prompt", "character_parts", "character_no_trigger", "character_status", "trigger", "tags",
)


class CharacterClassificationError(ValueError):
    pass


def comparison_key(value: str) -> str:
    value = re.sub(r"\\+([()])", r"\1", value)
    return " ".join(value.casefold().replace("_", " ").split())


def split_character_prompt(value: str) -> list[str]:
    """Split separators while leaving escaped/compound prompt expressions opaque."""
    parts: list[str] = []
    current: list[str] = []
    stack: list[str] = []
    escaped = False
    quote = ""
    pairs = {"(": ")", "[": "]", "{": "}"}
    for index, char in enumerate(value):
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
        previous = value[index - 1] if index else ""
        if char in {'"', "'"} and not (char == "'" and (previous.isalnum() or previous == "_")):
            closing = index + 1
            while closing < len(value):
                if value[closing] == "\\":
                    closing += 2
                    continue
                if value[closing] == char:
                    break
                closing += 1
            if closing < len(value):
                quote = char
        if char in pairs:
            stack.append(pairs[char])
        elif stack and char == stack[-1]:
            stack.pop()
        if not stack and not quote and char in {",", "\r", "\n"}:
            text = "".join(current).strip()
            if text:
                parts.append(text)
            current = []
        else:
            current.append(char)
    text = "".join(current).strip()
    if text:
        parts.append(text)
    return parts


def _text_list(value: Any, field: str) -> list[str]:
    if isinstance(value, str):
        return split_character_prompt(value)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise CharacterClassificationError(f"角色 {field} 必须是文本或文本数组")
    result: list[str] = []
    for item in value:
        result.extend(split_character_prompt(item))
    return result


def validate_parts(value: Any, tokens: list[str]) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) != len(tokens):
        raise CharacterClassificationError("角色分类必须逐项对应完整提示词，不可遗漏或添加标签")
    result = []
    for index, (raw, text) in enumerate(zip(value, tokens)):
        if not isinstance(raw, dict) or raw.get("text") != text:
            raise CharacterClassificationError(f"角色分类第 {index + 1} 项与原提示词不一致，请重新识别")
        role, source = raw.get("role"), raw.get("source")
        if not isinstance(role, str) or not isinstance(source, str) or role not in ROLES or source not in SOURCES:
            raise CharacterClassificationError(f"角色分类第 {index + 1} 项的分类或依据无效")
        part = {"text": text, "role": role, "source": source}
        if "suggestion" in raw:
            if not isinstance(raw["suggestion"], str) or raw["suggestion"] not in {"trigger", "feature"} or role != "unknown":
                raise CharacterClassificationError("角色分类候选建议无效")
            part["suggestion"] = raw["suggestion"]
        result.append(part)
    return result


def character_previews(parts: list[dict[str, str]], ready: bool) -> dict[str, str | None]:
    return {
        "trigger_tags": ", ".join(part["text"] for part in parts),
        "trigger": ", ".join(part["text"] for part in parts if part["role"] == "trigger") if ready else None,
        "trigger_tags_no_clothing": ", ".join(
            part["text"] for part in parts
            if part["role"] != "feature" or not is_character_clothing_tag(part["text"])
        ) if ready else None,
    }


class CharacterClassifier:
    def __init__(self, characters: Iterable[dict[str, Any]], tagger_paths: Iterable[Path] = ()):
        self.identities: set[str] = set()
        self.character_names: set[str] = set()
        self.features: set[str] = set()
        self.warnings: list[str] = []
        for item in characters:
            raw_name = str(item.get("raw_id") or item.get("title") or "")
            self.character_names.add(comparison_key(raw_name))
            self.identities.add(comparison_key(raw_name))
            self.identities.add(comparison_key(str(item.get("copyright") or "")))
            for field, target in (("trigger", self.identities), ("tags", self.features)):
                for tag in _text_list(item.get(field) or [], field):
                    target.add(comparison_key(tag))
        for values in (self.identities, self.character_names, self.features):
            values.discard("")
        for path in dict.fromkeys(Path(path) for path in tagger_paths):
            self._load_tagger(path)

    def _load_tagger(self, path: Path) -> None:
        if not path.is_file():
            return
        try:
            if not 0 < path.stat().st_size <= 32 * 1024 * 1024:
                raise ValueError("标签表大小无效")
            identities, features = set(), set()
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                if not {"name", "category"}.issubset(reader.fieldnames or []):
                    raise ValueError("标签表缺少 name/category")
                for index, row in enumerate(reader):
                    if index >= 200000 or row.get("name") is None or len(row["name"]) > 10000:
                        raise ValueError("标签表条目无效")
                    category = int(row["category"])
                    key = comparison_key(row["name"])
                    # Some published PixAI tables contain an unused empty class.
                    if not key:
                        continue
                    if category == 4:
                        identities.add(key)
                    elif category == 0:
                        features.add(key)
            self.identities.update(identities)
            self.character_names.update(identities)
            self.features.update(features)
        except (OSError, UnicodeError, csv.Error, ValueError, TypeError) as error:
            logger.warning("自定义角色分类忽略不可读本地标签表 %s: %s", path, error)
            self.warnings.append(f"本地标签表 {path.name} 无法读取，已使用其余本地词库")

    @staticmethod
    def _metadata(payload: dict[str, Any], fields: tuple[str, ...]) -> set[str]:
        aliases = set()
        for field in fields:
            value = payload.get(field, "")
            if not isinstance(value, str):
                raise CharacterClassificationError(f"角色 {field} 必须是文本")
            aliases.update(comparison_key(alias) for alias in re.split(r"\s*[·/|]\s*", value) if alias.strip())
        return aliases

    def _classify(self, text: str, metadata: set[str], names: set[str]) -> dict[str, str]:
        key = comparison_key(text)
        identity = key in self.identities
        feature = key in self.features or is_character_clothing_tag(text)
        if identity and feature:
            return {"text": text, "role": "unknown", "source": "conflict"}
        if feature:
            return {"text": text, "role": "feature", "source": "dictionary"}
        if identity:
            return {"text": text, "role": "trigger", "source": "dictionary"}
        if key in metadata:
            return {"text": text, "role": "trigger", "source": "metadata"}
        bare = re.sub(r"(?:\s*\([^()]*\))+$", "", key).strip()
        if bare != key and bare in self.character_names | names:
            return {"text": text, "role": "unknown", "source": "candidate", "suggestion": "trigger"}
        return {"text": text, "role": "unknown", "source": "unknown"}

    def analyze(self, payload: dict[str, Any], *, previous: dict[str, Any] | None = None,
                reanalyze: bool = False) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise CharacterClassificationError("角色分析内容必须是对象")
        prompt = payload.get("prompt")
        has_parts = "character_parts" in payload
        explicit = not has_parts and ("trigger" in payload or "tags" in payload)
        trigger = _text_list(payload.get("trigger", []), "trigger") if explicit else []
        tags = _text_list(payload.get("tags", []), "tags") if explicit else []
        if prompt is None and explicit:
            prompt = ", ".join([*trigger, *tags])
        if not isinstance(prompt, str) or not prompt.strip():
            raise CharacterClassificationError("角色提示词必须是非空文本")
        if len(prompt) > 20000:
            raise CharacterClassificationError("角色提示词不能超过 20000 个字符")
        tokens = split_character_prompt(prompt)
        if not tokens:
            raise CharacterClassificationError("角色提示词必须包含有效标签")
        no_trigger = payload.get("character_no_trigger", False)
        if type(no_trigger) is not bool:
            raise CharacterClassificationError("角色无触发词确认必须是布尔值")
        metadata = self._metadata(payload, ("title", "subtitle", "copyright"))
        names = self._metadata(payload, ("title", "subtitle"))
        supplied = validate_parts(payload["character_parts"], tokens) if has_parts else None
        if supplied is not None and not reanalyze:
            parts = supplied
        elif explicit:
            if Counter(tokens) != Counter([*trigger, *tags]):
                raise CharacterClassificationError("角色 trigger/tags 与完整提示词不一致，不可遗漏或添加标签")
            counts = Counter(trigger)
            parts = []
            for text in tokens:
                role = "trigger" if counts[text] else "feature"
                counts[text] -= role == "trigger"
                parts.append({"text": text, "role": role, "source": "explicit"})
            if not trigger:
                no_trigger = True
        else:
            retained: dict[str, deque[dict[str, str]]] = defaultdict(deque)
            old = supplied
            if old is None and previous is not None:
                if not isinstance(previous, dict):
                    raise CharacterClassificationError("之前的角色分类必须是对象")
                old_prompt = previous.get("prompt")
                if "character_parts" in previous:
                    if not isinstance(old_prompt, str):
                        raise CharacterClassificationError("之前的角色提示词无效")
                    old = validate_parts(previous["character_parts"], split_character_prompt(old_prompt))
            for part in old or []:
                if part["source"] in MANUAL_SOURCES:
                    retained[part["text"]].append(part)
            parts = [copy.deepcopy(retained[text].popleft()) if retained[text]
                     else self._classify(text, metadata, names) for text in tokens]
        trigger = [part["text"] for part in parts if part["role"] == "trigger"]
        tags = [part["text"] for part in parts if part["role"] == "feature"]
        if no_trigger and trigger:
            raise CharacterClassificationError("角色仍含触发词，不能同时确认无触发词")
        ready = not any(part["role"] == "unknown" for part in parts) and (bool(trigger) or no_trigger)
        return {
            "prompt": prompt, "character_parts": parts,
            "character_no_trigger": no_trigger, "character_status": "ready" if ready else "needs_review",
            "trigger": trigger, "tags": tags,
            "previews": character_previews(parts, ready), "warnings": list(self.warnings),
        }
