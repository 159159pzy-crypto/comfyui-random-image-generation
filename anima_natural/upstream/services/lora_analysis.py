"""Per-item, auditable LLM analysis for the LoRA semantic v2 index.

The pipeline intentionally analyzes one LoRA per provider request.  A malformed
or unavailable item therefore cannot roll back classifications that have already
been validated and atomically saved.  Operational events contain counters,
stages and validation codes only; neither prompts nor complete provider replies
are sent to :class:`TaskStore`.
"""

from __future__ import annotations

import ast
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import inspect
import json
from pathlib import Path
import re
from typing import Any, Awaitable, Callable, Iterable, Mapping, Optional, Sequence

from .lora_detail import LoraDetailV2
from .lora_semantic import (
    SEMANTIC_CATEGORIES,
    LoraSemanticError,
    LoraSemanticIndex,
    SemanticEntry,
    SemanticFact,
    semantic_identity_key,
)
from .task_store import TaskStore


LlmAnalysisCallback = Callable[[str, str], Awaitable[Any] | Any]

DEFAULT_ANALYSIS_SYSTEM_PROMPT = """You are a LoRA metadata archivist.
Analyze exactly one LoRA dossier and return one JSON object only. Do not output
chain-of-thought. You may translate character/work/style names and infer common
abbreviations when useful for Chinese natural-language search, but do not invent
an unrelated identity. The server marks every newly proposed semantic value as
llm_inferred; cite the source field that supports each inference.

Allowed category values: character, artist_style, speed_sampling,
quality_enhancement, detail_restoration, composition_pose, lighting_color,
background_environment, clothing_concept, mixed, unclassified.
- character requires at least one character_names value.
- artist_style requires at least one artist_style_names value.
- mixed requires both character_names and artist_style_names.
- functional categories describe acceleration/sampling help, quality enhancement,
  detail repair, composition/pose, lighting/color, background/environment, or
  clothing/concepts.
- use unclassified when the dossier cannot support a reliable identity.

Return this shape:
{
  "asset_id": "copy exactly from input",
  "lora_name": "copy exactly from input identity.lora_name",
  "category": "character|artist_style|speed_sampling|quality_enhancement|detail_restoration|composition_pose|lighting_color|background_environment|clothing_concept|mixed|unclassified",
  "character_names": ["..."],
  "source_works": ["..."],
  "artist_style_names": ["..."],
  "aliases": ["translations, romanizations, common short names"],
  "summary": "short factual archive summary, not hidden reasoning",
  "confidence": 0.0,
  "evidence": [
    {"source": "descriptions.model", "quote": "short exact quote"}
  ]
}

Evidence source may point to identity, existing_semantics, descriptions,
trigger_words, tags, creator, usage_tips or example_images. A source pointer is
required for every non-unclassified result. Return JSON only.
"""

_CATEGORY_ALIASES = {
    "character": "character",
    "character_lora": "character",
    "role": "character",
    "角色": "character",
    "人物": "character",
    "artist_style": "artist_style",
    "artist": "artist_style",
    "style": "artist_style",
    "artist/style": "artist_style",
    "artist style": "artist_style",
    "画师": "artist_style",
    "风格": "artist_style",
    "画师/风格": "artist_style",
    "speed_sampling": "speed_sampling",
    "speed": "speed_sampling",
    "acceleration": "speed_sampling",
    "sampling_helper": "speed_sampling",
    "采样加速": "speed_sampling",
    "加速": "speed_sampling",
    "quality_enhancement": "quality_enhancement",
    "quality": "quality_enhancement",
    "quality_boost": "quality_enhancement",
    "画质增强": "quality_enhancement",
    "detail_restoration": "detail_restoration",
    "detail": "detail_restoration",
    "detail_repair": "detail_restoration",
    "细节修复": "detail_restoration",
    "composition_pose": "composition_pose",
    "composition": "composition_pose",
    "pose": "composition_pose",
    "构图姿势": "composition_pose",
    "lighting_color": "lighting_color",
    "lighting": "lighting_color",
    "color_grading": "lighting_color",
    "光影色彩": "lighting_color",
    "background_environment": "background_environment",
    "background": "background_environment",
    "environment": "background_environment",
    "背景环境": "background_environment",
    "clothing_concept": "clothing_concept",
    "clothing": "clothing_concept",
    "outfit": "clothing_concept",
    "服装概念": "clothing_concept",
    "mixed": "mixed",
    "hybrid": "mixed",
    "混合": "mixed",
    "unclassified": "unclassified",
    "unknown": "unclassified",
    "uncertain": "unclassified",
    "未分类": "unclassified",
}
_CONFIDENCE_ALIASES = {
    "high": 0.9,
    "medium": 0.7,
    "low": 0.35,
}
_FIELD_ALIASES = {
    "model_description": "descriptions.model",
    "version_description": "descriptions.version",
    "notes": "descriptions.local_notes",
    "model_name": "identity.model_name",
    "version_name": "identity.version_name",
    "base_model": "identity.base_model",
    "work": "existing_semantics.source_work",
}
_EVIDENCE_ROOTS = frozenset(
    {
        "identity",
        "existing_semantics",
        "descriptions",
        "trigger_words",
        "tags",
        "creator",
        "license",
        "usage_tips",
        "example_images",
        "version_status",
        "file_status",
        "metadata_health",
    }
)
_LIST_FIELD_ALIASES = {
    "character_names": ("character_names", "characters", "role_names", "roles"),
    "source_works": ("source_works", "works", "work_names", "sources"),
    "artist_style_names": (
        "artist_style_names",
        "artist_names",
        "style_names",
        "styles",
    ),
    "aliases": ("aliases", "alias", "common_names", "short_names"),
}


class LoraAnalysisError(RuntimeError):
    """The requested analysis run cannot be started safely."""


class LoraAnalysisValidationError(ValueError):
    """One provider response could not be normalized into a safe proposal."""

    def __init__(self, code: str, message: str):
        self.code = str(code)
        super().__init__(message)


@dataclass(frozen=True)
class AnalysisEvidence:
    source: str
    quote: str = ""

    def audit_text(self) -> str:
        if not self.quote:
            return self.source
        return f'{self.source}: "{self.quote}"'


@dataclass(frozen=True)
class LoraAnalysisProposal:
    asset_id: str
    lora_name: str
    category: str
    character_names: tuple[str, ...]
    source_works: tuple[str, ...]
    artist_style_names: tuple[str, ...]
    aliases: tuple[str, ...]
    summary: str
    confidence: float
    evidence: tuple[AnalysisEvidence, ...]


@dataclass(frozen=True)
class LoraAnalysisItemResult:
    name: str
    asset_id: str
    success: bool
    attempts: int
    analysis_status: str = ""
    category: str = ""
    character_names: tuple[str, ...] = ()
    source_works: tuple[str, ...] = ()
    artist_style_names: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    summary: str = ""
    confidence: float = 0.0
    evidence: tuple[str, ...] = ()
    evidence_count: int = 0
    error_code: str = ""
    error_message: str = ""


@dataclass(frozen=True)
class LoraAnalysisRunResult:
    run_id: str
    status: str
    selected_count: int
    succeeded_count: int
    failed_count: int
    items: tuple[LoraAnalysisItemResult, ...]

    @property
    def updated_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.items if item.success)

    @property
    def failed_names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.items if not item.success)


class LoraAnalysisPipeline:
    """Analyze LoRA details one at a time and immediately persist successes."""

    def __init__(
        self,
        semantic_index: LoraSemanticIndex,
        semantic_index_path: Path | str,
        task_store: TaskStore,
        *,
        system_prompt: str = DEFAULT_ANALYSIS_SYSTEM_PROMPT,
        auto_searchable_confidence: float = 0.7,
        heartbeat_interval: float = 15.0,
    ) -> None:
        self.semantic_index = semantic_index
        self.semantic_index_path = Path(semantic_index_path)
        self.task_store = task_store
        self.system_prompt = str(system_prompt or DEFAULT_ANALYSIS_SYSTEM_PROMPT)
        self.auto_searchable_confidence = max(
            0.0, min(1.0, float(auto_searchable_confidence))
        )
        self.heartbeat_interval = max(0.05, float(heartbeat_interval))
        self._run_lock = asyncio.Lock()

    async def run(
        self,
        details: Sequence[LoraDetailV2],
        llm_callback: LlmAnalysisCallback,
        *,
        selected_names: Optional[Sequence[str]] = None,
        run_id: str = "",
        requested_by: str = "",
        max_repair_retries: int = 2,
    ) -> LoraAnalysisRunResult:
        """Run selected or all items, committing each valid result immediately.

        ``max_repair_retries`` is capped at two and counts retries after the
        initial provider call.  Therefore one item has at most three attempts.
        Exact selected names are required so duplicate basenames cannot target
        the wrong LoRA.
        """

        if not callable(llm_callback):
            raise TypeError("llm_callback must be callable")
        retries = max(0, min(2, int(max_repair_retries)))
        selected = self._select_details(tuple(details), selected_names)

        async with self._run_lock:
            existing = self.task_store.get_task(run_id) if run_id else None
            if existing is not None:
                identifier = run_id
            else:
                identifier = self.task_store.create_task(
                    "lora_semantic_analysis",
                    mode="selected" if selected_names is not None else "all",
                    requested_by=requested_by,
                    total_items=len(selected),
                    metadata={
                        "selected_names": [detail.name for detail in selected],
                        "per_item_requests": True,
                        "max_repair_retries": retries,
                    },
                    run_id=run_id,
                )
            self.task_store.start_task(identifier, total_items=len(selected))
            self.task_store.append_event(
                identifier,
                "run",
                f"LoRA semantic analysis started for {len(selected)} item(s).",
                event_code="analysis_started",
                batch_total=len(selected),
                details={"selected_count": len(selected)},
            )

            item_results: list[LoraAnalysisItemResult] = []
            succeeded = 0
            failed = 0
            try:
                for item_index, detail in enumerate(selected, start=1):
                    result = await self._analyze_item(
                        identifier,
                        detail,
                        llm_callback,
                        item_index=item_index,
                        item_total=len(selected),
                        max_attempts=1 + retries,
                        completed_items=succeeded,
                        failed_items=failed,
                    )
                    item_results.append(result)
                    if result.success:
                        succeeded += 1
                    else:
                        failed += 1
                    self.task_store.heartbeat(
                        identifier,
                        completed_items=succeeded,
                        failed_items=failed,
                        total_items=len(selected),
                    )

                status = self._terminal_status(len(selected), succeeded, failed)
                self.task_store.finish_task(
                    identifier,
                    status,
                    completed_items=succeeded,
                    failed_items=failed,
                    error_code="item_failures" if failed else "",
                    error_summary=(
                        f"{failed} item(s) failed semantic analysis." if failed else ""
                    ),
                    result={
                        "updated_names": [
                            item.name for item in item_results if item.success
                        ],
                        "failed_names": [
                            item.name for item in item_results if not item.success
                        ],
                        "succeeded_count": succeeded,
                        "failed_count": failed,
                    },
                )
                self.task_store.append_event(
                    identifier,
                    "run",
                    (
                        f"LoRA semantic analysis finished: "
                        f"{succeeded} succeeded, {failed} failed."
                    ),
                    level="ERROR" if status == "failed" else "INFO",
                    event_code="analysis_finished",
                    batch_total=len(selected),
                    details={
                        "status": status,
                        "succeeded_count": succeeded,
                        "failed_count": failed,
                    },
                )
                return LoraAnalysisRunResult(
                    run_id=identifier,
                    status=status,
                    selected_count=len(selected),
                    succeeded_count=succeeded,
                    failed_count=failed,
                    items=tuple(item_results),
                )
            except asyncio.CancelledError:
                self.task_store.append_event(
                    identifier,
                    "run",
                    "LoRA semantic analysis was cancelled.",
                    level="WARNING",
                    event_code="analysis_cancelled",
                    details={
                        "processed_items": succeeded + failed,
                        "succeeded_count": succeeded,
                        "failed_count": failed,
                    },
                )
                self.task_store.finish_task(
                    identifier,
                    "cancelled",
                    completed_items=succeeded,
                    failed_items=failed,
                    error_code="cancelled",
                    error_summary="Analysis was cancelled.",
                )
                raise
            except Exception as exc:
                self.task_store.append_event(
                    identifier,
                    "run",
                    "LoRA semantic analysis stopped because of an internal error.",
                    level="ERROR",
                    event_code="analysis_internal_error",
                    details={"exception_type": type(exc).__name__},
                )
                self.task_store.finish_task(
                    identifier,
                    "failed",
                    completed_items=succeeded,
                    failed_items=failed,
                    error_code="internal_error",
                    error_summary=f"Internal analysis error ({type(exc).__name__}).",
                )
                raise

    async def _analyze_item(
        self,
        run_id: str,
        detail: LoraDetailV2,
        llm_callback: LlmAnalysisCallback,
        *,
        item_index: int,
        item_total: int,
        max_attempts: int,
        completed_items: int,
        failed_items: int,
    ) -> LoraAnalysisItemResult:
        payload = detail.to_llm_payload()
        fingerprint = _payload_fingerprint(payload)
        source_fingerprint = detail.source_fingerprint or fingerprint
        try:
            self._set_analysis_status(
                detail,
                "analyzing",
                source_fingerprint=source_fingerprint,
            )
        except (LoraSemanticError, OSError) as exc:
            message = f"Semantic index save failed ({type(exc).__name__})."
            self.task_store.append_event(
                run_id,
                "persist",
                message,
                level="ERROR",
                item_name=detail.name,
                batch_index=item_index,
                batch_total=item_total,
                event_code="semantic_save_failed",
            )
            return LoraAnalysisItemResult(
                name=detail.name,
                asset_id=detail.asset_id,
                success=False,
                attempts=0,
                error_code="semantic_save_failed",
                error_message=message,
            )
        self.task_store.append_event(
            run_id,
            "prepare",
            "Prepared a bounded LoRA metadata dossier.",
            item_name=detail.name,
            batch_index=item_index,
            batch_total=item_total,
            event_code="item_prepared",
            details={
                "asset_id": detail.asset_id,
                "metadata_health": detail.metadata_health.status,
                "payload_chars": len(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                ),
                "source_fingerprint": fingerprint,
            },
        )

        base_prompt = self._build_user_prompt(payload)
        previous_output = ""
        validation_error: Optional[LoraAnalysisValidationError] = None
        last_error_code = "analysis_failed"
        last_error_message = "No valid semantic proposal was returned."

        for attempt in range(1, max_attempts + 1):
            is_repair = attempt > 1
            phase = "repair" if is_repair else "llm"
            prompt = (
                self._build_repair_prompt(
                    payload,
                    previous_output,
                    validation_error,
                )
                if is_repair
                else base_prompt
            )
            self.task_store.heartbeat(
                run_id,
                completed_items=completed_items,
                failed_items=failed_items,
                total_items=item_total,
            )
            self.task_store.append_event(
                run_id,
                phase,
                (
                    "Requesting a corrected structured response."
                    if is_repair
                    else "Requesting structured semantic analysis."
                ),
                item_name=detail.name,
                batch_index=item_index,
                batch_total=item_total,
                event_code="repair_requested" if is_repair else "provider_requested",
                attempt=attempt,
                details={
                    "payload_fingerprint": fingerprint,
                    "request_chars": len(prompt),
                    "previous_error_code": (
                        validation_error.code if validation_error else ""
                    ),
                },
            )

            try:
                response = await self._call_provider_with_heartbeat(
                    run_id,
                    detail.name,
                    item_index,
                    item_total,
                    attempt,
                    llm_callback,
                    prompt,
                    completed_items,
                    failed_items,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error_code = "provider_error"
                last_error_message = f"Provider call failed ({type(exc).__name__})."
                validation_error = LoraAnalysisValidationError(
                    last_error_code, last_error_message
                )
                previous_output = ""
                self.task_store.append_event(
                    run_id,
                    "llm",
                    last_error_message,
                    level="WARNING" if attempt < max_attempts else "ERROR",
                    item_name=detail.name,
                    batch_index=item_index,
                    batch_total=item_total,
                    event_code=last_error_code,
                    attempt=attempt,
                    details={"exception_type": type(exc).__name__},
                )
                continue

            response_text = _response_text(response)
            previous_output = response_text[:12000]
            self.task_store.append_event(
                run_id,
                "parse",
                "Provider response received; validating structured fields.",
                item_name=detail.name,
                batch_index=item_index,
                batch_total=item_total,
                event_code="response_received",
                attempt=attempt,
                details={"response_chars": len(response_text)},
            )
            try:
                proposal = self.parse_response(response, detail, payload=payload)
            except LoraAnalysisValidationError as exc:
                validation_error = exc
                last_error_code = exc.code
                last_error_message = str(exc)
                self.task_store.append_event(
                    run_id,
                    "validate",
                    str(exc),
                    level="WARNING" if attempt < max_attempts else "ERROR",
                    item_name=detail.name,
                    batch_index=item_index,
                    batch_total=item_total,
                    event_code=exc.code,
                    attempt=attempt,
                    details={"will_retry": attempt < max_attempts},
                )
                continue

            try:
                entry = self._build_semantic_entry(
                    detail,
                    proposal,
                    source_fingerprint,
                )
                self._commit_entry(entry)
            except (LoraSemanticError, OSError) as exc:
                last_error_code = "semantic_save_failed"
                last_error_message = (
                    f"Semantic index save failed ({type(exc).__name__})."
                )
                self.task_store.append_event(
                    run_id,
                    "persist",
                    last_error_message,
                    level="ERROR",
                    item_name=detail.name,
                    batch_index=item_index,
                    batch_total=item_total,
                    event_code=last_error_code,
                    attempt=attempt,
                )
                return LoraAnalysisItemResult(
                    name=detail.name,
                    asset_id=detail.asset_id,
                    success=False,
                    attempts=attempt,
                    error_code=last_error_code,
                    error_message=last_error_message,
                )

            self.task_store.append_event(
                run_id,
                "persist",
                "Validated semantic entry was atomically saved.",
                item_name=detail.name,
                batch_index=item_index,
                batch_total=item_total,
                event_code="item_saved",
                attempt=attempt,
                details={
                    "analysis_status": entry.analysis_status,
                    "category": proposal.category,
                    "confidence": proposal.confidence,
                    "evidence_count": len(proposal.evidence),
                    "summary_chars": len(proposal.summary),
                },
            )
            return LoraAnalysisItemResult(
                name=detail.name,
                asset_id=detail.asset_id,
                success=True,
                attempts=attempt,
                analysis_status=entry.analysis_status,
                category=proposal.category,
                character_names=proposal.character_names,
                source_works=proposal.source_works,
                artist_style_names=proposal.artist_style_names,
                aliases=proposal.aliases,
                summary=proposal.summary,
                confidence=proposal.confidence,
                evidence=tuple(item.audit_text() for item in proposal.evidence),
                evidence_count=len(proposal.evidence),
            )

        try:
            self._set_analysis_status(
                detail,
                "failed",
                source_fingerprint=source_fingerprint,
                error=last_error_code,
            )
        except (LoraSemanticError, OSError):
            last_error_code = "semantic_save_failed"
            last_error_message = "Unable to persist the failed analysis state."
        self.task_store.append_event(
            run_id,
            "item",
            "LoRA semantic analysis exhausted its repair attempts.",
            level="ERROR",
            item_name=detail.name,
            batch_index=item_index,
            batch_total=item_total,
            event_code="item_failed",
            attempt=max_attempts,
            details={"error_code": last_error_code},
        )
        return LoraAnalysisItemResult(
            name=detail.name,
            asset_id=detail.asset_id,
            success=False,
            attempts=max_attempts,
            error_code=last_error_code,
            error_message=last_error_message,
        )

    async def _call_provider_with_heartbeat(
        self,
        run_id: str,
        item_name: str,
        item_index: int,
        item_total: int,
        attempt: int,
        llm_callback: LlmAnalysisCallback,
        user_prompt: str,
        completed_items: int,
        failed_items: int,
    ) -> Any:
        value = llm_callback(self.system_prompt, user_prompt)
        if not inspect.isawaitable(value):
            return value
        task = asyncio.ensure_future(value)
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=self.heartbeat_interval)
                if done:
                    return await task
                self.task_store.heartbeat(
                    run_id,
                    completed_items=completed_items,
                    failed_items=failed_items,
                    total_items=item_total,
                )
                self.task_store.append_event(
                    run_id,
                    "heartbeat",
                    "Waiting for the semantic-analysis provider.",
                    item_name=item_name,
                    batch_index=item_index,
                    batch_total=item_total,
                    event_code="provider_heartbeat",
                    attempt=attempt,
                )
        except asyncio.CancelledError:
            task.cancel()
            raise

    def _commit_entry(self, entry: SemanticEntry) -> None:
        previous = self.semantic_index.entries.get(entry.identity_key)
        self.semantic_index.upsert(entry)
        try:
            self.semantic_index.save(self.semantic_index_path)
        except Exception:
            if previous is None:
                self.semantic_index.entries.pop(entry.identity_key, None)
            else:
                self.semantic_index.entries[entry.identity_key] = previous
            raise

    def _set_analysis_status(
        self,
        detail: LoraDetailV2,
        status: str,
        *,
        source_fingerprint: str,
        error: str = "",
    ) -> None:
        identity_key = semantic_identity_key(detail.name, detail.file_status.sha256)
        previous = self.semantic_index.entries.get(identity_key)
        fields = {
            field_name: previous.facts(field_name) if previous is not None else ()
            for field_name in (
                "category",
                "character_names",
                "source_works",
                "artist_style_names",
                "aliases",
            )
        }
        self._commit_entry(
            SemanticEntry(
                identity_key=identity_key,
                canonical_name=detail.name,
                sha256=detail.file_status.sha256,
                analysis_status=status,
                analysis_summary=(
                    previous.analysis_summary if previous is not None else ""
                ),
                analysis_confidence=(
                    previous.analysis_confidence if previous is not None else 0.0
                ),
                source_fingerprint=source_fingerprint,
                updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                error=error,
                present=True,
                **fields,
            )
        )

    def _build_semantic_entry(
        self,
        detail: LoraDetailV2,
        proposal: LoraAnalysisProposal,
        source_fingerprint: str,
    ) -> SemanticEntry:
        identity_key = semantic_identity_key(detail.name, detail.file_status.sha256)
        previous = self.semantic_index.entries.get(identity_key)
        evidence = tuple(item.audit_text() for item in proposal.evidence)

        previous_fields: dict[str, tuple[SemanticFact, ...]] = {}
        for field_name in (
            "category",
            "character_names",
            "source_works",
            "activation_terms",
            "artist_style_names",
            "aliases",
        ):
            facts = previous.facts(field_name) if previous is not None else ()
            previous_fields[field_name] = tuple(
                fact for fact in facts if fact.source != "llm_inferred"
            )

        derived: dict[str, tuple[SemanticFact, ...]] = {
            "category": (),
            "character_names": (),
            "source_works": (),
            "activation_terms": (),
            "artist_style_names": (),
            "aliases": (),
        }
        if detail.category in SEMANTIC_CATEGORIES:
            derived["category"] = (
                SemanticFact(
                    detail.category,
                    "derived",
                    ("fresh_record.category",),
                    0.75,
                ),
            )
        if detail.character_name:
            derived["character_names"] = (
                SemanticFact(
                    detail.character_name,
                    "derived",
                    ("fresh_record.character_name",),
                    0.75,
                ),
            )
        if detail.source_work:
            derived["source_works"] = (
                SemanticFact(
                    detail.source_work,
                    "derived",
                    ("fresh_record.source_work",),
                    0.75,
                ),
            )
        derived["activation_terms"] = tuple(
            SemanticFact(value, "observed", ("fresh_record.trigger_words",), 1.0)
            for value in detail.trigger_words
            if _clean_text(value)
        )
        derived["aliases"] = tuple(
            SemanticFact(value, "derived", ("fresh_record.aliases",), 0.7)
            for value in detail.aliases
            if _clean_text(value)
        )

        llm_values = {
            "category": (proposal.category,),
            "character_names": proposal.character_names,
            "source_works": proposal.source_works,
            "activation_terms": (),
            "artist_style_names": proposal.artist_style_names,
            "aliases": proposal.aliases,
        }
        merged_fields: dict[str, tuple[SemanticFact, ...]] = {}
        for field_name, values in llm_values.items():
            llm_facts = tuple(
                SemanticFact(
                    value,
                    "llm_inferred",
                    evidence,
                    proposal.confidence,
                )
                for value in values
            )
            merged_fields[field_name] = _dedupe_facts(
                (
                    *previous_fields[field_name],
                    *derived[field_name],
                    *llm_facts,
                )
            )

        status = (
            "searchable"
            if proposal.category != "unclassified"
            and proposal.confidence >= self.auto_searchable_confidence
            else "review_needed"
        )
        return SemanticEntry(
            identity_key=identity_key,
            canonical_name=detail.name,
            sha256=detail.file_status.sha256,
            analysis_status=status,
            analysis_summary=proposal.summary,
            analysis_confidence=proposal.confidence,
            source_fingerprint=source_fingerprint,
            updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            error="",
            present=True,
            identity_bindings=(
                previous.identity_bindings if previous is not None else ()
            ),
            **merged_fields,
        )

    @staticmethod
    def parse_response(
        response: Any,
        detail: LoraDetailV2,
        *,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> LoraAnalysisProposal:
        source_payload = dict(payload or detail.to_llm_payload())
        parsed = _response_payload(response)
        if isinstance(parsed.get("classification"), Mapping):
            parsed = {**parsed, **dict(parsed["classification"])}
        if isinstance(parsed.get("item"), Mapping):
            parsed = dict(parsed["item"])
        elif isinstance(parsed.get("result"), Mapping):
            parsed = dict(parsed["result"])
        elif isinstance(parsed.get("items"), list):
            items = [item for item in parsed["items"] if isinstance(item, Mapping)]
            if len(items) != 1:
                raise LoraAnalysisValidationError(
                    "invalid_item_count", "Response must contain exactly one item."
                )
            parsed = dict(items[0])

        returned_asset = _clean_text(parsed.get("asset_id"))
        if returned_asset and returned_asset != detail.asset_id:
            raise LoraAnalysisValidationError(
                "identity_mismatch", "Response changed the immutable LoRA asset ID."
            )
        returned_name = _clean_text(parsed.get("lora_name") or parsed.get("name"))
        if returned_name and returned_name != detail.name:
            raise LoraAnalysisValidationError(
                "identity_mismatch", "Response changed the exact LoRA name."
            )

        raw_category = _clean_text(parsed.get("category")).casefold()
        category = _CATEGORY_ALIASES.get(raw_category, raw_category)
        if category not in SEMANTIC_CATEGORIES:
            raise LoraAnalysisValidationError(
                "invalid_category", "Response category is missing or unsupported."
            )

        fields = {
            field_name: _extract_text_list(parsed, aliases)
            for field_name, aliases in _LIST_FIELD_ALIASES.items()
        }
        if category == "character" and not fields["character_names"]:
            raise LoraAnalysisValidationError(
                "missing_character",
                "Character classification requires a character name.",
            )
        if category == "artist_style" and not fields["artist_style_names"]:
            raise LoraAnalysisValidationError(
                "missing_artist_style",
                "Artist/style classification requires an artist or style name.",
            )
        if category == "mixed" and not (
            fields["character_names"] and fields["artist_style_names"]
        ):
            raise LoraAnalysisValidationError(
                "incomplete_mixed",
                "Mixed classification requires character and artist/style names.",
            )

        summary = _clean_text(
            parsed.get("summary")
            or parsed.get("archive_summary")
            or parsed.get("description"),
            limit=3000,
        )
        if not summary:
            raise LoraAnalysisValidationError(
                "missing_summary", "Response must include a short factual summary."
            )
        confidence = _parse_confidence(parsed.get("confidence"))
        evidence = _parse_evidence(parsed.get("evidence"), source_payload)
        if category != "unclassified" and not evidence:
            raise LoraAnalysisValidationError(
                "missing_evidence",
                "A classified LoRA requires at least one valid source pointer.",
            )

        return LoraAnalysisProposal(
            asset_id=detail.asset_id,
            lora_name=detail.name,
            category=category,
            character_names=fields["character_names"],
            source_works=fields["source_works"],
            artist_style_names=fields["artist_style_names"],
            aliases=fields["aliases"],
            summary=summary,
            confidence=confidence,
            evidence=evidence,
        )

    @staticmethod
    def _build_user_prompt(payload: Mapping[str, Any]) -> str:
        return (
            "Analyze this single LoRA dossier. Copy asset_id and lora_name exactly. "
            "Return JSON only.\n"
            + json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
        )

    @staticmethod
    def _build_repair_prompt(
        payload: Mapping[str, Any],
        previous_output: str,
        error: Optional[LoraAnalysisValidationError],
    ) -> str:
        error_code = error.code if error else "provider_error"
        error_message = str(error) if error else "No valid response was received."
        previous = _clean_response_text(previous_output)[:8000]
        return (
            "Repair the previous structured response. Do not explain the error and "
            "do not output markdown. Return one corrected JSON object only.\n"
            f"Validation error [{error_code}]: {error_message}\n"
            "Authoritative single-LoRA dossier:\n"
            + json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
            + "\nPrevious output (untrusted, may be empty):\n"
            + previous
        )

    @staticmethod
    def _select_details(
        details: tuple[LoraDetailV2, ...],
        selected_names: Optional[Sequence[str]],
    ) -> tuple[LoraDetailV2, ...]:
        by_name: dict[str, LoraDetailV2] = {}
        duplicates: set[str] = set()
        for detail in details:
            if detail.name in by_name:
                duplicates.add(detail.name)
            by_name[detail.name] = detail
        if duplicates:
            raise LoraAnalysisError(
                "Duplicate exact LoRA names in detail input: "
                + ", ".join(sorted(duplicates))
            )
        if selected_names is None:
            return details
        requested = tuple(str(name) for name in selected_names)
        if len(set(requested)) != len(requested):
            raise LoraAnalysisError("Selected LoRA names must not contain duplicates.")
        missing = tuple(name for name in requested if name not in by_name)
        if missing:
            raise LoraAnalysisError(
                "Selected exact LoRA name was not found: " + ", ".join(missing)
            )
        return tuple(by_name[name] for name in requested)

    @staticmethod
    def _terminal_status(total: int, succeeded: int, failed: int) -> str:
        if failed and succeeded:
            return "partial"
        if failed:
            return "failed"
        return "succeeded"


def _response_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    if isinstance(response, Mapping):
        for key in ("completion_text", "text", "content"):
            value = response.get(key)
            if isinstance(value, str):
                return value
        try:
            return json.dumps(response, ensure_ascii=False)
        except (TypeError, ValueError):
            return ""
    for attribute in ("completion_text", "text", "content"):
        value = getattr(response, attribute, None)
        if isinstance(value, str):
            return value
    return str(response or "")


def _response_payload(response: Any) -> dict[str, Any]:
    if isinstance(response, Mapping) and not any(
        isinstance(response.get(key), str)
        for key in ("completion_text", "text", "content")
    ):
        return dict(response)
    text = _clean_response_text(_response_text(response))
    if not text:
        raise LoraAnalysisValidationError(
            "empty_response", "Provider returned an empty response."
        )

    candidates = [text, *_balanced_json_objects(text)]
    seen: set[str] = set()
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        for repaired in (
            candidate,
            re.sub(r",\s*([}\]])", r"\1", candidate),
        ):
            try:
                parsed = json.loads(repaired)
            except (json.JSONDecodeError, TypeError):
                try:
                    parsed = ast.literal_eval(repaired)
                except (ValueError, SyntaxError):
                    continue
            if isinstance(parsed, Mapping):
                return dict(parsed)
    raise LoraAnalysisValidationError(
        "invalid_json", "Provider response does not contain a valid JSON object."
    )


def _clean_response_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"<think\b[^>]*>.*?</think\s*>", "", text, flags=re.I | re.S)
    text = re.sub(r"```(?:json|javascript|js|python)?\s*", "", text, flags=re.I)
    text = text.replace("```", "")
    return text.strip()


def _balanced_json_objects(text: str) -> tuple[str, ...]:
    results: list[str] = []
    start: Optional[int] = None
    depth = 0
    in_string = False
    escaped = False
    for index, character in enumerate(text):
        if start is None:
            if character == "{":
                start = index
                depth = 1
                in_string = False
                escaped = False
            continue
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                results.append(text[start : index + 1])
                start = None
    return tuple(results)


def _extract_text_list(
    payload: Mapping[str, Any], aliases: Sequence[str]
) -> tuple[str, ...]:
    raw: Any = None
    for key in aliases:
        if key in payload:
            raw = payload.get(key)
            break
    if raw is None:
        return ()
    if isinstance(raw, str):
        values: Iterable[Any] = re.split(r"[,;；\n]+", raw)
    elif isinstance(raw, (list, tuple, set)):
        values = raw
    else:
        raise LoraAnalysisValidationError(
            "invalid_list", f"Field {aliases[0]} must be a string or list."
        )
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, Mapping):
            value = value.get("value") or value.get("name") or ""
        text = _clean_text(value, limit=240)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
            if len(result) >= 60:
                break
    return tuple(result)


def _parse_confidence(value: Any) -> float:
    if isinstance(value, str):
        text = value.strip().casefold()
        if text in _CONFIDENCE_ALIASES:
            return _CONFIDENCE_ALIASES[text]
        if text.endswith("%"):
            try:
                value = float(text[:-1]) / 100.0
            except ValueError as exc:
                raise LoraAnalysisValidationError(
                    "invalid_confidence", "Confidence must be between 0 and 1."
                ) from exc
    try:
        confidence = float(value)
    except (TypeError, ValueError) as exc:
        raise LoraAnalysisValidationError(
            "invalid_confidence", "Confidence must be numeric or high/medium/low."
        ) from exc
    if not 0.0 <= confidence <= 1.0:
        raise LoraAnalysisValidationError(
            "invalid_confidence", "Confidence must be between 0 and 1."
        )
    return confidence


def _parse_evidence(
    value: Any, source_payload: Mapping[str, Any]
) -> tuple[AnalysisEvidence, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, Mapping)):
        values: Iterable[Any] = (value,)
    elif isinstance(value, (list, tuple)):
        values = value
    else:
        raise LoraAnalysisValidationError(
            "invalid_evidence", "Evidence must be a string, object or list."
        )
    payload_text = json.dumps(source_payload, ensure_ascii=False).casefold()
    result: list[AnalysisEvidence] = []
    seen: set[tuple[str, str]] = set()
    for raw in values:
        source = ""
        quote = ""
        if isinstance(raw, Mapping):
            source = _clean_text(
                raw.get("source") or raw.get("field") or raw.get("path"),
                limit=200,
            )
            quote = _clean_text(
                raw.get("quote") or raw.get("text") or raw.get("value"),
                limit=500,
            )
        else:
            text = _clean_text(raw, limit=500)
            if _payload_path_exists(source_payload, text):
                source = text
            elif text and text.casefold() in payload_text:
                source = "source_payload"
                quote = text
        source = _FIELD_ALIASES.get(source, source)
        if source == "source_payload":
            valid_source = True
        else:
            root = re.split(r"[.\[]", source, maxsplit=1)[0]
            valid_source = root in _EVIDENCE_ROOTS and _payload_path_exists(
                source_payload, source
            )
        if not valid_source:
            continue
        source_value = _payload_path_value(source_payload, source)
        if quote and source != "source_payload":
            source_text = json.dumps(source_value, ensure_ascii=False).casefold()
            if quote.casefold() not in source_text:
                # The pointer is still auditable; discard an inexact paraphrase.
                quote = ""
        key = (source.casefold(), quote.casefold())
        if key not in seen:
            seen.add(key)
            result.append(AnalysisEvidence(source=source, quote=quote))
            if len(result) >= 30:
                break
    return tuple(result)


def _payload_path_exists(payload: Mapping[str, Any], path: str) -> bool:
    return _payload_path_value(payload, path, missing_marker=_MISSING) is not _MISSING


_MISSING = object()


def _payload_path_value(
    payload: Mapping[str, Any], path: str, *, missing_marker: Any = None
) -> Any:
    if not path:
        return missing_marker
    current: Any = payload
    normalized_tokens: list[Any] = []
    for whole, index in re.findall(r"([^.\[\]]+)|\[(\d+)\]", path):
        normalized_tokens.append(int(index) if index else whole)
    for token in normalized_tokens:
        if isinstance(token, int):
            if not isinstance(current, list) or not 0 <= token < len(current):
                return missing_marker
            current = current[token]
        else:
            if not isinstance(current, Mapping) or token not in current:
                return missing_marker
            current = current[token]
    return current


def _clean_text(value: Any, *, limit: int = 3000) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text


def _payload_fingerprint(payload: Mapping[str, Any]) -> str:
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256(serialized.encode("utf-8")).hexdigest()


def _dedupe_facts(values: Iterable[SemanticFact]) -> tuple[SemanticFact, ...]:
    result: list[SemanticFact] = []
    seen: set[tuple[str, str]] = set()
    for fact in values:
        key = (fact.source, fact.value.casefold())
        if key not in seen:
            seen.add(key)
            result.append(fact)
    return tuple(result)


__all__ = [
    "AnalysisEvidence",
    "DEFAULT_ANALYSIS_SYSTEM_PROMPT",
    "LlmAnalysisCallback",
    "LoraAnalysisError",
    "LoraAnalysisItemResult",
    "LoraAnalysisPipeline",
    "LoraAnalysisProposal",
    "LoraAnalysisRunResult",
    "LoraAnalysisValidationError",
]
