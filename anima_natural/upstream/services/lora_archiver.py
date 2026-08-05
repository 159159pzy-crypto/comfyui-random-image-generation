"""LLM-assisted logical archive for the current LoRA catalog.

The service deliberately keeps three concerns separate:

* the fresh, loadable catalog is supplied by :mod:`lora_catalog`;
* the LLM produces a conservative semantic classification;
* human overrides are stored independently and always win.

No LoRA file is renamed, moved or deleted.  The persisted JSON document is only
an index that WebUI and prompt tools may consume.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping, Optional, Sequence

from ..core.lora import canonical_lora_name
from .lora_catalog import LoraRecord


ARCHIVE_SCHEMA_VERSION = 1
ARCHIVE_CATEGORIES = (
    "character",
    "artist_style",
    "mixed",
    "unclassified",
)
CONFIDENCE_LEVELS = ("high", "medium", "low")

_THINK_BLOCK_RE = re.compile(
    r"<think\b[^>]*>.*?</think\s*>", flags=re.IGNORECASE | re.DOTALL
)
_EXPECTED_ITEM_KEYS = {
    "name",
    "category",
    "display_name",
    "character_names",
    "source_works",
    "artist_style_names",
    "aliases",
    "summary",
    "evidence",
    "confidence",
    "uncertainty",
}
_OVERRIDABLE_KEYS = _EXPECTED_ITEM_KEYS - {"name"}


class LoraArchiveError(RuntimeError):
    """The logical archive could not be built or safely persisted."""

    def __init__(self, user_message: str, detail: str = ""):
        self.user_message = user_message
        self.detail = detail
        super().__init__(detail or user_message)


@dataclass(frozen=True)
class CatalogArchiveStatus:
    """Difference between the fresh catalog and the last complete archive."""

    changed: bool
    fingerprint: str
    archived_fingerprint: str
    added: tuple[str, ...]
    modified: tuple[str, ...]
    removed: tuple[str, ...]
    pending: tuple[str, ...]
    current_count: int
    archived_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ArchivePrompt:
    """One bounded request for the configured drawing-director provider."""

    system_prompt: str
    user_prompt: str
    record_names: tuple[str, ...]
    catalog_fingerprint: str


@dataclass(frozen=True)
class ArchiveRunResult:
    """Summary returned after an LLM archive run."""

    skipped: bool
    selected_count: int
    batch_count: int
    updated_names: tuple[str, ...]
    status: CatalogArchiveStatus

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.to_dict()
        return payload


LlmArchiveCallback = Callable[[str, str], Awaitable[Any]]


ARCHIVE_SYSTEM_PROMPT = """
你是 LoRA 元数据归档员。你的任务是依据输入中的原始资料，为每个 LoRA 建立保守、可审计的逻辑归档。

事实边界：
1. 只能使用输入 JSON 中出现的文件名、Civitai 模型名、模型/版本描述、全部触发词、标签、已有别名、角色名和作品线索。
2. 不得调用外部常识补全角色、作品、画师或翻译；不得因为名字看起来熟悉就猜测。
3. 模糊、冲突或证据不足时必须使用 unclassified，并在 uncertainty 说明缺少什么；不得编造。
4. character 仅用于明确的角色/人物 LoRA；artist_style 仅用于明确的画师、画风、画质、美感、材质或摄影风格 LoRA；mixed 仅用于同一 LoRA 有明确证据同时覆盖角色与画师/风格；其余为 unclassified。
5. 原始 trigger words 不需要在输出中改写或删减，系统会原样保存。aliases、名称和作品字段只能整理输入中已有的写法。
6. evidence 的每一项都必须是输入中原样出现的、可直接检索到的短文本；非 unclassified 项至少提供一条证据。
7. confidence=low 时 category 必须为 unclassified。

严格输出协议：
- 只输出一个 JSON 对象，不要 Markdown、代码围栏、think 标签或解释。
- 顶层只能包含 items。
- items 必须与输入记录一一对应，不得漏项、增项、重复或修改 name。
- 每项必须且只能包含以下字段：
  name, category, display_name, character_names, source_works,
  artist_style_names, aliases, summary, evidence, confidence, uncertainty。
- category 只能是 character、artist_style、mixed、unclassified。
- character_names、source_works、artist_style_names、aliases、evidence 必须是字符串数组。
- display_name、summary、uncertainty 必须是字符串；confidence 只能是 high、medium、low。

输出形状：
{"items":[{"name":"输入中的精确名称","category":"unclassified","display_name":"","character_names":[],"source_works":[],"artist_style_names":[],"aliases":[],"summary":"","evidence":[],"confidence":"low","uncertainty":"证据不足"}]}
""".strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize_space(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _name_key(value: Any) -> str:
    return canonical_lora_name(str(value or "")).casefold()


def _stable_strings(values: Iterable[Any]) -> list[str]:
    unique: dict[str, str] = {}
    for value in values:
        text = _normalize_space(value)
        if text:
            unique.setdefault(text.casefold(), text)
    return sorted(unique.values(), key=str.casefold)


def _source_snapshot(record: LoraRecord) -> dict[str, Any]:
    """Preserve every useful catalog field without truncation."""
    return {
        "name": record.name,
        "canonical_name": canonical_lora_name(record.name),
        "civitai_model_name": record.model_name,
        "civitai_model_or_version_description": record.description,
        "civitai_metadata_present": bool(record.from_civitai),
        "base_model": record.base_model,
        "trigger_words": list(record.trigger_words),
        "tags": list(record.tags),
        "existing_category": record.category,
        "existing_aliases": list(record.aliases),
        "existing_character_name": record.character_name,
        "existing_source_work": record.source_work,
        "folder": record.folder,
        "file_path": record.file_path,
        "preview_url": record.preview_url,
        "sha256": record.sha256,
        "favorite": bool(record.favorite),
        "catalog_source": record.source,
    }


def _fingerprint_source(record: LoraRecord) -> dict[str, Any]:
    """Return semantic fields whose changes should invalidate an archive.

    Volatile presentation fields (preview URL and absolute file path) are not
    included, so switching a saved LAN profile does not make an unchanged
    library look different.  Set-like metadata is sorted to make the digest
    independent from API ordering.
    """
    return {
        "name": canonical_lora_name(record.name),
        "model_name": _normalize_space(record.model_name),
        "description": _normalize_space(record.description),
        "base_model": _normalize_space(record.base_model),
        "trigger_words": _stable_strings(record.trigger_words),
        "tags": _stable_strings(record.tags),
        "category": _normalize_space(record.category),
        "aliases": _stable_strings(record.aliases),
        "character_name": _normalize_space(record.character_name),
        "source_work": _normalize_space(record.source_work),
        "sha256": _normalize_space(record.sha256).casefold(),
        "from_civitai": bool(record.from_civitai),
    }


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class LoraArchiveService:
    """Build and persist a conservative LLM-backed logical LoRA archive."""

    def __init__(
        self,
        index_path: Path | str,
        system_prompt_path: Path | str | None = None,
    ):
        self._index_path = Path(index_path)
        self._lock = asyncio.Lock()
        self._system_prompt = ARCHIVE_SYSTEM_PROMPT
        if system_prompt_path is not None:
            prompt_path = Path(system_prompt_path)
            try:
                prompt_text = prompt_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise LoraArchiveError(
                    "无法读取 LoRA 归档提示词",
                    f"{prompt_path}: {exc}",
                ) from exc
            if not prompt_text:
                raise LoraArchiveError("LoRA 归档提示词不能为空")
            if len(prompt_text) > 1024 * 1024:
                raise LoraArchiveError("LoRA 归档提示词超过 1MB")
            self._system_prompt = prompt_text

    @staticmethod
    def record_fingerprint(record: LoraRecord) -> str:
        return _sha256_json(_fingerprint_source(record))

    @classmethod
    def catalog_fingerprint(cls, records: Sequence[LoraRecord]) -> str:
        """Return a stable fingerprint independent from catalog ordering."""
        payload = sorted(
            (
                _name_key(record.name),
                cls.record_fingerprint(record),
            )
            for record in records
        )
        return _sha256_json(payload)

    @staticmethod
    def _empty_index() -> dict[str, Any]:
        return {
            "schema_version": ARCHIVE_SCHEMA_VERSION,
            "catalog_fingerprint": "",
            "last_seen_fingerprint": "",
            "updated_at": "",
            "entries": {},
        }

    def read_index(self) -> dict[str, Any]:
        """Read the logical archive; malformed files fail closed."""
        if not self._index_path.is_file():
            return self._empty_index()
        try:
            payload = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LoraArchiveError("LoRA 逻辑归档索引无法读取", str(exc)) from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("entries"), dict):
            raise LoraArchiveError("LoRA 逻辑归档索引格式无效")
        if payload.get("schema_version") != ARCHIVE_SCHEMA_VERSION:
            raise LoraArchiveError("LoRA 逻辑归档索引版本不受支持")
        # Return a detached value so callers cannot mutate in-memory state.
        return json.loads(json.dumps(payload, ensure_ascii=False))

    def _write_index(self, payload: Mapping[str, Any]) -> None:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._index_path.with_name(f".{self._index_path.name}.tmp")
        try:
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            temp_path.replace(self._index_path)
        except OSError as exc:
            raise LoraArchiveError("无法保存 LoRA 逻辑归档索引", str(exc)) from exc
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass

    def catalog_status(self, records: Sequence[LoraRecord]) -> CatalogArchiveStatus:
        """Compare a fresh catalog with the persisted per-record source digests."""
        index = self.read_index()
        entries = index.get("entries", {})
        current = {_name_key(record.name): record for record in records}
        added: list[str] = []
        modified: list[str] = []
        for key, record in current.items():
            entry = entries.get(key)
            if not isinstance(entry, dict):
                added.append(record.name)
            elif entry.get("catalog_source_fingerprint") != self.record_fingerprint(
                record
            ):
                modified.append(record.name)
        removed = [
            str(entry.get("name") or key)
            for key, entry in entries.items()
            if isinstance(entry, dict)
            and entry.get("present", True)
            and key not in current
        ]
        added.sort(key=str.casefold)
        modified.sort(key=str.casefold)
        removed.sort(key=str.casefold)
        fingerprint = self.catalog_fingerprint(records)
        archived_fingerprint = str(index.get("catalog_fingerprint") or "")
        pending = tuple((*added, *modified))
        changed = bool(
            pending
            or removed
            or fingerprint != archived_fingerprint
        )
        return CatalogArchiveStatus(
            changed=changed,
            fingerprint=fingerprint,
            archived_fingerprint=archived_fingerprint,
            added=tuple(added),
            modified=tuple(modified),
            removed=tuple(removed),
            pending=pending,
            current_count=len(current),
            archived_count=sum(
                1
                for entry in entries.values()
                if isinstance(entry, dict) and entry.get("present", True)
            ),
        )

    def sync_catalog_presence(
        self,
        records: Sequence[LoraRecord],
    ) -> CatalogArchiveStatus:
        """Acknowledge deletions without invoking the LLM or archiving new data."""
        catalog_records = tuple(records)
        index = self.read_index()
        current_keys = {_name_key(record.name) for record in catalog_records}
        entries = index.get("entries", {})
        if not isinstance(entries, dict):
            raise LoraArchiveError("LoRA 归档索引 entries 字段无效")
        for key, entry in entries.items():
            if isinstance(entry, dict):
                entry["present"] = key in current_keys
        now = _utc_now()
        current_fingerprint = self.catalog_fingerprint(catalog_records)
        index["last_seen_fingerprint"] = current_fingerprint
        index["updated_at"] = now

        pending = []
        for record in catalog_records:
            entry = entries.get(_name_key(record.name))
            if not isinstance(entry, dict) or entry.get(
                "catalog_source_fingerprint"
            ) != self.record_fingerprint(record):
                pending.append(record.name)
        if not pending:
            index["catalog_fingerprint"] = current_fingerprint
        self._write_index(index)
        return self.catalog_status(catalog_records)

    @staticmethod
    def select_records(
        records: Sequence[LoraRecord],
        selected_names: Optional[Sequence[str]] = None,
    ) -> tuple[LoraRecord, ...]:
        """Select all, one or many records without unsafe fuzzy guessing.

        ``None`` means all.  Explicit selections accept an exact canonical name
        or a basename only when that basename is unique in the current catalog.
        """
        ordered = tuple(records)
        if selected_names is None:
            return ordered
        if not selected_names:
            raise LoraArchiveError("请至少选择一个需要归档的 LoRA")

        exact: dict[str, LoraRecord] = {}
        basenames: dict[str, list[LoraRecord]] = {}
        for record in ordered:
            key = _name_key(record.name)
            exact[key] = record
            basenames.setdefault(key.rsplit("/", 1)[-1], []).append(record)

        selected: list[LoraRecord] = []
        seen: set[str] = set()
        missing: list[str] = []
        ambiguous: dict[str, list[str]] = {}
        for raw_name in selected_names:
            requested = canonical_lora_name(str(raw_name))
            key = requested.casefold()
            record = exact.get(key)
            if record is None:
                candidates = basenames.get(key.rsplit("/", 1)[-1], [])
                if len(candidates) == 1:
                    record = candidates[0]
                elif len(candidates) > 1:
                    ambiguous[str(raw_name)] = [item.name for item in candidates]
            if record is None:
                missing.append(str(raw_name))
                continue
            record_key = _name_key(record.name)
            if record_key not in seen:
                seen.add(record_key)
                selected.append(record)

        if ambiguous:
            raise LoraArchiveError(
                "LoRA 简称对应多个文件，请使用完整路径名称",
                json.dumps(ambiguous, ensure_ascii=False),
            )
        if missing:
            raise LoraArchiveError(
                "所选 LoRA 不在最新可加载清单中",
                json.dumps(missing, ensure_ascii=False),
            )
        return tuple(selected)

    @classmethod
    def build_prompt(
        cls,
        records: Sequence[LoraRecord],
        *,
        catalog_fingerprint: str = "",
        system_prompt: str = ARCHIVE_SYSTEM_PROMPT,
    ) -> ArchivePrompt:
        """Build a no-truncation prompt containing all available metadata."""
        selected = tuple(records)
        if not selected:
            raise LoraArchiveError("没有可供 LLM 归档的 LoRA")
        fingerprint = catalog_fingerprint or cls.catalog_fingerprint(selected)
        payload = {
            "task": "classify_and_logically_archive_loras",
            "catalog_fingerprint": fingerprint,
            "record_count": len(selected),
            "allowed_categories": list(ARCHIVE_CATEGORIES),
            "records": [_source_snapshot(record) for record in selected],
        }
        user_prompt = (
            "请完整消化以下 LoRA 原始资料并按系统协议输出严格 JSON。"
            "所有触发词和描述均不得截断；证据不足时保留原始资料并归入 unclassified。\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )
        return ArchivePrompt(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            record_names=tuple(record.name for record in selected),
            catalog_fingerprint=fingerprint,
        )

    @staticmethod
    def _response_text(response: Any) -> str:
        if isinstance(response, str):
            text = response
        elif isinstance(response, Mapping):
            text = next(
                (
                    value
                    for key in ("completion_text", "text", "content")
                    if isinstance((value := response.get(key)), str)
                ),
                "",
            )
        else:
            text = getattr(response, "completion_text", "")
        if not isinstance(text, str) or not text.strip():
            raise LoraArchiveError("LLM 没有返回 LoRA 归档结果")
        return text.strip()

    @staticmethod
    def _clean_json_response(text: str) -> str:
        cleaned = _THINK_BLOCK_RE.sub("", text).strip()
        if cleaned.startswith("```") and cleaned.endswith("```"):
            lines = cleaned.splitlines()
            if len(lines) >= 3 and lines[0].strip().casefold() in {
                "```",
                "```json",
            } and lines[-1].strip() == "```":
                cleaned = "\n".join(lines[1:-1]).strip()
        return cleaned

    @staticmethod
    def _string(value: Any, field: str, *, maximum: int = 4000) -> str:
        if not isinstance(value, str):
            raise LoraArchiveError(f"LLM 归档字段 {field} 必须是字符串")
        if len(value) > maximum:
            raise LoraArchiveError(f"LLM 归档字段 {field} 过长")
        return value.strip()

    @classmethod
    def _string_list(
        cls, value: Any, field: str, *, maximum_items: int = 100
    ) -> list[str]:
        if not isinstance(value, list):
            raise LoraArchiveError(f"LLM 归档字段 {field} 必须是字符串数组")
        if len(value) > maximum_items:
            raise LoraArchiveError(f"LLM 归档字段 {field} 项目过多")
        result: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = cls._string(item, field, maximum=1000)
            if text and text.casefold() not in seen:
                seen.add(text.casefold())
                result.append(text)
        return result

    @staticmethod
    def _evidence_is_grounded(evidence: str, record: LoraRecord) -> bool:
        if not evidence:
            return False
        source_text = json.dumps(
            _source_snapshot(record), ensure_ascii=False, sort_keys=True
        ).casefold()
        return evidence.casefold() in source_text

    @classmethod
    def parse_response(
        cls,
        response: Any,
        expected_records: Sequence[LoraRecord],
    ) -> tuple[dict[str, Any], ...]:
        """Strictly validate JSON output and bind it to exact source records."""
        text = cls._clean_json_response(cls._response_text(response))
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LoraArchiveError("LLM 返回的 LoRA 归档不是有效 JSON", str(exc)) from exc
        if not isinstance(payload, dict) or set(payload) != {"items"}:
            raise LoraArchiveError("LLM 归档 JSON 顶层只能包含 items")
        items = payload.get("items")
        if not isinstance(items, list):
            raise LoraArchiveError("LLM 归档 items 必须是数组")

        expected = {record.name: record for record in expected_records}
        if len(items) != len(expected):
            raise LoraArchiveError("LLM 归档结果与所选 LoRA 数量不一致")
        parsed: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_item in items:
            if not isinstance(raw_item, dict) or set(raw_item) != _EXPECTED_ITEM_KEYS:
                raise LoraArchiveError("LLM 归档条目字段不完整或包含未知字段")
            name = cls._string(raw_item["name"], "name", maximum=1000)
            if name not in expected:
                raise LoraArchiveError("LLM 修改或编造了 LoRA 文件名", name)
            if name in seen:
                raise LoraArchiveError("LLM 归档结果包含重复 LoRA", name)
            seen.add(name)
            category = cls._string(raw_item["category"], "category", maximum=32)
            if category not in ARCHIVE_CATEGORIES:
                raise LoraArchiveError("LLM 返回了不受支持的 LoRA 分类", category)
            confidence = cls._string(
                raw_item["confidence"], "confidence", maximum=16
            )
            if confidence not in CONFIDENCE_LEVELS:
                raise LoraArchiveError("LLM 返回了无效置信度", confidence)
            if confidence == "low" and category != "unclassified":
                raise LoraArchiveError("低置信度 LoRA 必须归入 unclassified")

            normalized = {
                "name": name,
                "category": category,
                "display_name": cls._string(
                    raw_item["display_name"], "display_name", maximum=1000
                ),
                "character_names": cls._string_list(
                    raw_item["character_names"], "character_names"
                ),
                "source_works": cls._string_list(
                    raw_item["source_works"], "source_works"
                ),
                "artist_style_names": cls._string_list(
                    raw_item["artist_style_names"], "artist_style_names"
                ),
                "aliases": cls._string_list(raw_item["aliases"], "aliases"),
                "summary": cls._string(
                    raw_item["summary"], "summary", maximum=4000
                ),
                "evidence": cls._string_list(raw_item["evidence"], "evidence"),
                "confidence": confidence,
                "uncertainty": cls._string(
                    raw_item["uncertainty"], "uncertainty", maximum=2000
                ),
            }
            if category == "character" and not normalized["character_names"]:
                raise LoraArchiveError("角色 LoRA 缺少 character_names", name)
            if category == "artist_style" and not normalized["artist_style_names"]:
                raise LoraArchiveError("画师/风格 LoRA 缺少 artist_style_names", name)
            if category == "mixed" and not (
                normalized["character_names"] and normalized["artist_style_names"]
            ):
                raise LoraArchiveError(
                    "mixed LoRA 必须同时有角色与画师/风格证据", name
                )
            if category != "unclassified" and not normalized["evidence"]:
                raise LoraArchiveError("已分类 LoRA 缺少可审计证据", name)
            for evidence in normalized["evidence"]:
                if not cls._evidence_is_grounded(evidence, expected[name]):
                    raise LoraArchiveError(
                        "LLM 归档证据未出现在原始元数据中",
                        f"{name}: {evidence}",
                    )
            parsed.append(normalized)

        missing = set(expected) - seen
        if missing:
            raise LoraArchiveError(
                "LLM 漏掉了部分 LoRA",
                json.dumps(sorted(missing), ensure_ascii=False),
            )
        # Persist in the input order even if the provider reordered items.
        by_name = {item["name"]: item for item in parsed}
        return tuple(by_name[record.name] for record in expected_records)

    @classmethod
    def _record_batches(
        cls,
        records: Sequence[LoraRecord],
        *,
        batch_size: int,
        max_batch_chars: int,
        catalog_fingerprint: str,
    ) -> tuple[tuple[LoraRecord, ...], ...]:
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        if max_batch_chars < 1000:
            raise ValueError("max_batch_chars must be at least 1000")
        batches: list[tuple[LoraRecord, ...]] = []
        current: list[LoraRecord] = []
        for record in records:
            candidate = [*current, record]
            candidate_chars = len(
                cls.build_prompt(
                    candidate, catalog_fingerprint=catalog_fingerprint
                ).user_prompt
            )
            if current and (
                len(candidate) > batch_size or candidate_chars > max_batch_chars
            ):
                batches.append(tuple(current))
                current = [record]
            else:
                current = candidate
        if current:
            batches.append(tuple(current))
        return tuple(batches)

    @staticmethod
    def _effective(classification: Mapping[str, Any], override: Any) -> dict[str, Any]:
        effective = dict(classification)
        if isinstance(override, dict):
            effective.update(
                {key: value for key, value in override.items() if key in _OVERRIDABLE_KEYS}
            )
        return effective

    def _persist_classifications(
        self,
        catalog_records: Sequence[LoraRecord],
        archived_records: Sequence[LoraRecord],
        classifications: Sequence[Mapping[str, Any]],
    ) -> CatalogArchiveStatus:
        index = self.read_index()
        entries = index["entries"]
        catalog_by_key = {_name_key(record.name): record for record in catalog_records}
        archived_by_name = {record.name: record for record in archived_records}
        now = _utc_now()
        current_keys = set(catalog_by_key)

        for key, entry in entries.items():
            if isinstance(entry, dict):
                entry["present"] = key in current_keys
        for classification in classifications:
            name = str(classification["name"])
            key = _name_key(name)
            base_record = catalog_by_key.get(key)
            archived_record = archived_by_name.get(name)
            if base_record is None or archived_record is None:
                raise LoraArchiveError("归档结果与最新目录不一致", name)
            previous = entries.get(key) if isinstance(entries.get(key), dict) else {}
            manual_override = previous.get("manual_override", {})
            if not isinstance(manual_override, dict):
                manual_override = {}
            clean_classification = dict(classification)
            entries[key] = {
                "name": base_record.name,
                "present": True,
                "catalog_source_fingerprint": self.record_fingerprint(base_record),
                "archive_source_fingerprint": self.record_fingerprint(archived_record),
                "source": _source_snapshot(archived_record),
                "classification": clean_classification,
                "manual_override": manual_override,
                "effective": self._effective(clean_classification, manual_override),
                "classified_at": now,
            }

        current_fingerprint = self.catalog_fingerprint(catalog_records)
        index["last_seen_fingerprint"] = current_fingerprint
        index["updated_at"] = now

        pending = []
        for key, record in catalog_by_key.items():
            entry = entries.get(key)
            if not isinstance(entry, dict) or entry.get(
                "catalog_source_fingerprint"
            ) != self.record_fingerprint(record):
                pending.append(record.name)
        if not pending:
            index["catalog_fingerprint"] = current_fingerprint
        self._write_index(index)
        return self.catalog_status(catalog_records)

    async def archive_with_llm(
        self,
        catalog_records: Sequence[LoraRecord],
        llm_callback: LlmArchiveCallback,
        *,
        selected_names: Optional[Sequence[str]] = None,
        archived_records: Optional[Sequence[LoraRecord]] = None,
        batch_size: int = 8,
        max_batch_chars: int = 60000,
        skip_when_unchanged: bool = False,
    ) -> ArchiveRunResult:
        """Classify selected/all records through an injected async callback.

        The callback receives ``(system_prompt, user_prompt)`` and may return a
        string, a mapping containing ``completion_text``/``text``/``content``,
        or an AstrBot response object exposing ``completion_text``.

        ``catalog_records`` must be the latest loadable catalog.  Callers may
        supply detail-enriched ``archived_records`` for prompting while source
        change detection continues to use the fresh base records.
        """
        async with self._lock:
            catalog_records = tuple(catalog_records)
            before = self.catalog_status(catalog_records)
            if skip_when_unchanged and not before.changed and selected_names is None:
                return ArchiveRunResult(
                    skipped=True,
                    selected_count=0,
                    batch_count=0,
                    updated_names=(),
                    status=before,
                )

            selected_base = self.select_records(catalog_records, selected_names)
            if archived_records is None:
                selected_for_prompt = selected_base
            else:
                selected_for_prompt = self.select_records(
                    tuple(archived_records),
                    tuple(record.name for record in selected_base),
                )
            fingerprint = self.catalog_fingerprint(catalog_records)
            batches = self._record_batches(
                selected_for_prompt,
                batch_size=batch_size,
                max_batch_chars=max_batch_chars,
                catalog_fingerprint=fingerprint,
            )
            parsed_items: list[dict[str, Any]] = []
            for batch in batches:
                prompt = self.build_prompt(
                    batch,
                    catalog_fingerprint=fingerprint,
                    system_prompt=self._system_prompt,
                )
                try:
                    response = await llm_callback(
                        prompt.system_prompt,
                        prompt.user_prompt,
                    )
                except LoraArchiveError:
                    raise
                except Exception as exc:
                    raise LoraArchiveError(
                        "LLM LoRA 归档调用失败", str(exc)
                    ) from exc
                parsed_items.extend(self.parse_response(response, batch))

            status = self._persist_classifications(
                catalog_records,
                selected_for_prompt,
                parsed_items,
            )
            return ArchiveRunResult(
                skipped=False,
                selected_count=len(selected_for_prompt),
                batch_count=len(batches),
                updated_names=tuple(record.name for record in selected_for_prompt),
                status=status,
            )

    async def archive_from_catalog(
        self,
        catalog_service: Any,
        llm_callback: LlmArchiveCallback,
        *,
        selected_names: Optional[Sequence[str]] = None,
        enrich_details: bool = True,
        batch_size: int = 8,
        max_batch_chars: int = 60000,
        skip_when_unchanged: bool = False,
    ) -> ArchiveRunResult:
        """Refresh Manager/object_info first, optionally enrich, then archive.

        This adapter intentionally requires ``refresh_for_operation`` so every
        archive run follows the plugin's strict "refresh before LoRA use" rule.
        Detail enrichment uses the catalog service's existing metadata method
        when available; failures conservatively retain the original record.
        """
        if not hasattr(catalog_service, "refresh_for_operation"):
            raise LoraArchiveError("LoRA 清单服务不支持强制刷新")
        catalog_records = tuple(await catalog_service.refresh_for_operation())
        if skip_when_unchanged and selected_names is None:
            status = self.catalog_status(catalog_records)
            if not status.changed:
                return ArchiveRunResult(
                    skipped=True,
                    selected_count=0,
                    batch_count=0,
                    updated_names=(),
                    status=status,
                )
        selected = self.select_records(catalog_records, selected_names)
        archived_records = selected
        enricher = getattr(catalog_service, "_enrich_manager_detail", None)
        if enrich_details and callable(enricher):
            archived_records = tuple([await enricher(record) for record in selected])
        return await self.archive_with_llm(
            catalog_records,
            llm_callback,
            selected_names=tuple(record.name for record in selected),
            archived_records=archived_records,
            batch_size=batch_size,
            max_batch_chars=max_batch_chars,
            skip_when_unchanged=False,
        )

    @classmethod
    def _validate_override(cls, override: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(override) - _OVERRIDABLE_KEYS
        if unknown:
            raise LoraArchiveError(
                "人工覆盖包含不受支持的字段",
                json.dumps(sorted(unknown), ensure_ascii=False),
            )
        clean: dict[str, Any] = {}
        for key, value in override.items():
            if key in {
                "character_names",
                "source_works",
                "artist_style_names",
                "aliases",
                "evidence",
            }:
                clean[key] = cls._string_list(value, key)
            else:
                clean[key] = cls._string(value, key)
        if "category" in clean and clean["category"] not in ARCHIVE_CATEGORIES:
            raise LoraArchiveError("人工覆盖分类无效", clean["category"])
        if "confidence" in clean and clean["confidence"] not in CONFIDENCE_LEVELS:
            raise LoraArchiveError("人工覆盖置信度无效", clean["confidence"])
        return clean

    def set_manual_override(
        self, name: str, override: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Set a partial human override without changing the LLM result."""
        index = self.read_index()
        key = _name_key(name)
        entry = index["entries"].get(key)
        if not isinstance(entry, dict):
            raise LoraArchiveError("该 LoRA 尚未建立逻辑归档", name)
        clean = self._validate_override(override)
        entry["manual_override"] = clean
        entry["effective"] = self._effective(entry.get("classification", {}), clean)
        entry["manual_updated_at"] = _utc_now()
        index["updated_at"] = _utc_now()
        self._write_index(index)
        return json.loads(json.dumps(entry["effective"], ensure_ascii=False))

    def clear_manual_override(self, name: str) -> dict[str, Any]:
        return self.set_manual_override(name, {})

    def list_entries(
        self,
        *,
        category: str = "",
        present_only: bool = True,
    ) -> tuple[dict[str, Any], ...]:
        """Return effective entries for WebUI category filtering."""
        if category and category not in ARCHIVE_CATEGORIES:
            raise LoraArchiveError("LoRA 归档筛选分类无效", category)
        entries: list[dict[str, Any]] = []
        for entry in self.read_index()["entries"].values():
            if not isinstance(entry, dict):
                continue
            if present_only and not entry.get("present", True):
                continue
            effective = entry.get("effective")
            if not isinstance(effective, dict):
                continue
            if category and effective.get("category") != category:
                continue
            entries.append(json.loads(json.dumps(entry, ensure_ascii=False)))
        return tuple(
            sorted(entries, key=lambda item: str(item.get("name", "")).casefold())
        )
