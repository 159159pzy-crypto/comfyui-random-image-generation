from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from aiohttp import web

from .v7_events import StudioEventBus
from .v7_store import DraftConflictError, V7Store

V7_STORE_KEY: web.AppKey[V7Store] = web.AppKey("v7_store", V7Store)
V7_EVENTS_KEY: web.AppKey[StudioEventBus] = web.AppKey("v7_events", StudioEventBus)

JobCallback = Callable[[str, Mapping[str, Any]], Awaitable[Mapping[str, Any] | None]]
JobSubmitCallback = Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]]
IntentPreviewCallback = Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]]
UploadCallback = Callable[[bytes], Mapping[str, Any] | Awaitable[Mapping[str, Any]]]
JobOverlay = Callable[[Mapping[str, Any]], Mapping[str, Any]]


class IntentRevisionConflictError(RuntimeError):
    def __init__(self, current: Mapping[str, Any]):
        super().__init__("intent revision conflict")
        self.current = dict(current)


@web.middleware
async def v7_deprecation_middleware(
    request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
) -> web.StreamResponse:
    response = await handler(request)
    if request.path.startswith(("/api/natural/", "/api/batches", "/api/style-presets")):
        store = request.app.get(V7_STORE_KEY)
        if store is not None:
            workspace = "natural" if request.path.startswith("/api/natural/") else "random"
            await asyncio.to_thread(
                store.record_deprecation, request.method, request.path, workspace
            )
        response.headers["Deprecation"] = "true"
        response.headers["Sunset"] = "Wed, 05 Aug 2027 00:00:00 GMT"
        response.headers["Link"] = '</api/v7/bootstrap>; rel="successor-version"'
    return response


def _public(value: Any) -> Any:
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return to_dict(include_digest=True)
        except TypeError:
            return to_dict()
    if isinstance(value, Mapping):
        return {str(key): _public(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_public(item) for item in value]
    return value


def _without_private(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _without_private(item)
            for key, item in value.items()
            if not str(key).startswith("_")
        }
    if isinstance(value, (list, tuple)):
        return [_without_private(item) for item in value]
    return value


def _public_intent(value: Mapping[str, Any]) -> dict[str, Any]:
    return _without_private(_public(value))


def _domain_classes() -> tuple[Any, Any, Any, type[Exception]]:
    from anima_studio.domain import (  # imported lazily during the V7 transition
        DomainValidationError,
        GenerationIntent,
        StylePreset,
        WorkspaceDraft,
    )

    return GenerationIntent, WorkspaceDraft, StylePreset, DomainValidationError


async def _json_body(request: web.Request) -> dict[str, Any]:
    try:
        value = await request.json()
    except Exception as error:
        raise ValueError("request body must be valid JSON") from error
    if not isinstance(value, dict):
        raise TypeError("request body must be an object")
    return value


def _integer(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    if value in (None, ""):
        return default
    result = int(value)
    if not minimum <= result <= maximum:
        raise ValueError(f"integer value must be between {minimum} and {maximum}")
    return result


def _normalize_intent(body: Mapping[str, Any], workspace: str | None = None) -> dict[str, Any]:
    GenerationIntent, _, _, _ = _domain_classes()
    candidate = body.get("intent") if isinstance(body.get("intent"), Mapping) else body
    workspace = str(workspace or candidate.get("workspace") or "natural")
    return _public(GenerationIntent.from_mapping(candidate, workspace=workspace))


def _normalize_draft(body: Mapping[str, Any], workspace: str) -> dict[str, Any]:
    _, WorkspaceDraft, _, _ = _domain_classes()
    candidate = dict(body)
    candidate.pop("revision", None)
    candidate.pop("digest", None)
    candidate.pop("updated_at", None)
    return _public(WorkspaceDraft.from_mapping(candidate, workspace=workspace))


def _normalize_preset(body: Mapping[str, Any], preset_id: str = "") -> dict[str, Any]:
    _, _, StylePreset, _ = _domain_classes()
    candidate = dict(body)
    candidate["id"] = preset_id or str(candidate.get("id") or f"preset_{uuid.uuid4().hex[:16]}")
    return _public(StylePreset.from_mapping(candidate))


def _source(task: Mapping[str, Any]) -> str:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), Mapping) else {}
    workspace = str(metadata.get("workspace") or "").casefold()
    if workspace in V7Store.WORKSPACES:
        return workspace
    task_type = str(task.get("task_type") or "").casefold()
    mode = str(task.get("mode") or "").casefold()
    if task_type.startswith("random") or mode == "random":
        return "random"
    if task_type.startswith("natural") or mode in {
        "natural",
        "text_to_image",
        "image_to_image",
        "character_swap",
        "reverse",
    }:
        return "natural"
    return "studio"


def _public_job(task: Mapping[str, Any], *, source_workspace: str = "") -> dict[str, Any]:
    result = dict(_without_private(_public(task)))
    source = source_workspace or _source(result)
    result.pop("task_type", None)
    result["type"] = "studio_operation" if source == "studio" else "generation"
    result["source_workspace"] = source
    return result


def _identity(value: Any) -> str:
    return str(value or "").strip().casefold().replace("\\", "/").removeprefix("@")


def _resolution_values(intent: Mapping[str, Any], kind: str) -> set[str]:
    if kind == "artist":
        return {_identity(item) for item in intent.get("artist_tags") or []}
    if kind == "lora":
        return {
            _identity(item.get("filename"))
            for item in intent.get("loras") or []
            if isinstance(item, Mapping)
            and bool(item.get("enabled", True))
        }
    if kind in {"preset", "style_preset"}:
        return {_identity(intent.get("style_preset_id"))}
    if kind == "prompt_asset":
        return {_identity(item) for item in intent.get("prompt_asset_ids") or []}
    if kind == "prompt_plan":
        return {_identity(intent.get("prompt_plan_id"))}
    if kind == "character_alias":
        return {_identity(item) for item in intent.get("locked_tags") or []}
    return set()


_RESOLUTION_SOURCE_FIELDS = {
    "artist": "artist_tags",
    "lora": "loras",
    "preset": "style_preset_id",
    "style_preset": "style_preset_id",
    "prompt_asset": "prompt_asset_ids",
    "prompt_plan": "prompt_plan_id",
    "character_alias": "locked_tags",
}


def _apply_explicit_resolutions(
    plan: Mapping[str, Any], explicit_intent: Mapping[str, Any]
) -> dict[str, Any]:
    result = dict(plan)
    required = list(result.get("requires_confirmation") or [])
    if not required:
        return result

    unresolved: list[Any] = []
    resolved: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in required:
        if not isinstance(raw, Mapping):
            unresolved.append(raw)
            continue
        item = dict(raw)
        kind = str(item.get("kind") or "").strip()
        query = str(item.get("query") or "").strip()
        explicit_values = _resolution_values(explicit_intent, kind) - {""}
        selected = next(
            (
                dict(candidate)
                for candidate in item.get("candidates") or []
                if isinstance(candidate, Mapping)
                and explicit_values
                & {
                    _identity(candidate.get("id")),
                    _identity(candidate.get("name")),
                }
            ),
            None,
        )
        if selected is None:
            unresolved.append(item)
            continue
        resolved[(kind, query)] = selected

    result["requires_confirmation"] = unresolved
    matches = result.get("matches")
    if isinstance(matches, list):
        normalized_matches: list[Any] = []
        for raw in matches:
            if not isinstance(raw, Mapping):
                normalized_matches.append(raw)
                continue
            match = dict(raw)
            key = (
                str(match.get("kind") or "").strip(),
                str(match.get("query") or "").strip(),
            )
            selected = resolved.get(key)
            if selected is not None:
                match.update(
                    {
                        "status": "matched",
                        "needs_confirmation": False,
                        "reason": "",
                        "selected": selected,
                    }
                )
            normalized_matches.append(match)
        result["matches"] = normalized_matches

    sources = dict(result.get("sources") or {})
    for (kind, _query), selected in resolved.items():
        field = _RESOLUTION_SOURCE_FIELDS.get(kind)
        if not field:
            continue
        current = sources.get(field)
        entries = list(current) if isinstance(current, list) else ([] if current is None else [current])
        source = {
            "kind": kind,
            "id": str(selected.get("id") or selected.get("name") or ""),
            "matched_by": "explicit",
        }
        if source not in entries:
            entries.append(source)
        sources[field] = entries
    result["sources"] = sources
    return result


def _validate_resolution_confirmations(
    required: list[Any],
    receipts: list[Any],
    submitted: Mapping[str, Any],
    saved: Mapping[str, Any],
) -> list[dict[str, Any]]:
    unresolved: list[dict[str, Any]] = []
    used: set[int] = set()
    for raw_required in required:
        if not isinstance(raw_required, Mapping):
            raise TypeError("saved confirmation requirement is invalid")
        item = dict(raw_required)
        kind = str(item.get("kind") or "").strip()
        query = str(item.get("query") or "").strip()
        matches = [
            (index, receipt)
            for index, receipt in enumerate(receipts)
            if isinstance(receipt, Mapping)
            and str(receipt.get("kind") or "").strip() == kind
            and str(receipt.get("query") or "").strip() == query
        ]
        if not matches:
            unresolved.append(item)
            continue
        if len(matches) != 1:
            raise ValueError(f"duplicate confirmation receipt for {kind}: {query}")
        receipt_index, receipt = matches[0]
        used.add(receipt_index)
        action = str(receipt.get("action") or "")
        if action == "keep_explicit":
            if not any(_resolution_values(saved, kind)):
                raise ValueError(f"confirmation has no explicit {kind} override")
            continue
        if action != "select_candidate":
            raise ValueError(f"unsupported confirmation action: {action}")
        receipt_id = _identity(receipt.get("candidate_id"))
        receipt_name = _identity(receipt.get("candidate_name"))
        if not receipt_id and not receipt_name:
            raise ValueError("confirmation candidate identity is required")
        candidates = [value for value in item.get("candidates") or [] if isinstance(value, Mapping)]
        selected = next(
            (
                value
                for value in candidates
                if (receipt_id and receipt_id == _identity(value.get("id")))
                or (receipt_name and receipt_name == _identity(value.get("name")))
            ),
            None,
        )
        if selected is None:
            raise ValueError(f"confirmation candidate is not allowed for {kind}: {query}")
        allowed = {_identity(selected.get("id")), _identity(selected.get("name"))} - {""}
        if not (_resolution_values(submitted, kind) & allowed):
            raise ValueError(f"confirmed {kind} candidate is missing from the submitted intent")
    if any(index not in used for index in range(len(receipts))):
        raise ValueError("confirmation receipt does not match the preview")
    return unresolved


def setup_v7_routes(
    app: web.Application,
    *,
    store: V7Store,
    events: StudioEventBus,
    history: Any,
    comfy: Any,
    runtime: Any,
    studio_services: Any | None = None,
    preview_job: IntentPreviewCallback | None = None,
    submit_job: JobSubmitCallback | None = None,
    cancel_job: JobCallback | None = None,
    retry_job: JobCallback | None = None,
    job_overlay: JobOverlay | None = None,
    upload_asset: UploadCallback | None = None,
    max_upload_bytes: int = 25 * 1024 * 1024,
) -> None:
    """Register the native V7 contract without removing legacy routes."""

    app[V7_STORE_KEY] = store
    app[V7_EVENTS_KEY] = events

    def handler(function: Callable[..., Awaitable[web.StreamResponse]]) -> Callable[..., Any]:
        async def wrapped(request: web.Request) -> web.StreamResponse:
            try:
                return await function(request)
            except DraftConflictError as error:
                return web.json_response(
                    {
                        "error": "revision_conflict",
                        "code": "revision_conflict",
                        "current": error.current,
                    },
                    status=409,
                )
            except IntentRevisionConflictError as error:
                return web.json_response(
                    {
                        "error": "intent_revision_conflict",
                        "code": "intent_revision_conflict",
                        "current": _public(error.current),
                    },
                    status=409,
                )
            except KeyError as error:
                return web.json_response(
                    {"error": "not_found", "code": "not_found", "id": str(error.args[0])},
                    status=404,
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - API boundary normalizes downstream failures.
                _, _, _, domain_error = _domain_classes()
                client_error = isinstance(error, (ValueError, TypeError, domain_error))
                status = int(getattr(error, "status", 400 if client_error else 500))
                if not 400 <= status <= 599:
                    status = 500
                code = str(
                    getattr(
                        error,
                        "code",
                        "invalid_request" if status < 500 else "internal_error",
                    )
                )
                payload = {"error": str(error)[:1000], "code": code}
                details = getattr(error, "details", None)
                if isinstance(details, Mapping):
                    payload["details"] = _without_private(_public(details))
                return web.json_response(payload, status=status)

        return wrapped

    async def resolve_generation_intent(
        body: Mapping[str, Any], workspace: str, *, accept_frozen: bool = False
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        nested = body.get("intent") if isinstance(body.get("intent"), Mapping) else {}
        candidate = dict(nested or body)
        if accept_frozen and workspace == "natural" and nested:
            intent_id = str(candidate.get("intent_id") or candidate.get("id") or "").strip()
            digest = str(candidate.get("digest") or "").strip()
            if intent_id or digest:
                if not intent_id or not digest:
                    raise IntentRevisionConflictError({"id": intent_id, "digest": digest})
                saved = await asyncio.to_thread(store.get_intent, intent_id)
                if saved.get("workspace") != workspace or digest != str(saved.get("digest") or ""):
                    raise IntentRevisionConflictError(saved)
                resolution = saved.get("_resolution")
                if not isinstance(resolution, Mapping):
                    resolution = {}
                required = list(resolution.get("requires_confirmation") or [])
                receipts = body.get("resolution_confirmations")
                receipts = list(receipts) if isinstance(receipts, list) else []
                unresolved = _validate_resolution_confirmations(
                    required,
                    receipts,
                    candidate,
                    saved,
                )
                normalized = _normalize_intent(candidate, workspace)
                normalized["intent_id"] = ""
                normalized["preview_intent_id"] = intent_id
                normalized["preview_digest"] = digest
                normalized["resolution_confirmations"] = receipts
                frozen_plan = saved.get("_plan")
                if not isinstance(frozen_plan, Mapping):
                    raise IntentRevisionConflictError(saved)
                execution_plan = dict(frozen_plan)
                execution_plan.update(
                    {
                        "job_type": (
                            "img2img"
                            if normalized.get("mode") == "image_to_image"
                            else normalized.get("mode", "text_to_image")
                        ),
                        "pipeline": normalized.get("pipeline", ""),
                        "inpaint_mode": normalized.get("inpaint_mode", "quick"),
                        "positive_prompt": normalized.get("positive_prompt", ""),
                        "negative_prompt": normalized.get("negative_prompt", ""),
                        "model": normalized.get("model", ""),
                        "loras": list(normalized.get("loras") or []),
                        "artist_tags": list(normalized.get("artist_tags") or []),
                        "style_preset_id": normalized.get("style_preset_id", ""),
                        "prompt_asset_ids": list(
                            normalized.get("prompt_asset_ids") or []
                        ),
                        "prompt_plan_id": normalized.get("prompt_plan_id", ""),
                        "locked_tags": list(normalized.get("locked_tags") or []),
                        "requires_confirmation": [],
                    }
                )
                execution_plan.pop("digest", None)
                normalized["_execution_plan"] = execution_plan
                return normalized, {
                    "requires_confirmation": unresolved,
                    "matches": list(resolution.get("matches") or []),
                    "sources": dict(resolution.get("sources") or {}),
                    "resolution_confirmations": receipts,
                    "frozen": True,
                }
        plan: dict[str, Any] = {}
        if preview_job is not None and workspace == "natural":
            explicit_candidate = dict(candidate)
            plan = _apply_explicit_resolutions(
                _public(await preview_job(candidate)), explicit_candidate
            )
            planned_mode = plan.get("job_type", candidate.get("mode", "text_to_image"))
            if planned_mode == "img2img":
                planned_mode = "image_to_image"
            candidate.update(
                {
                    "positive_prompt": plan.get("positive_prompt", candidate.get("positive_prompt", "")),
                    "negative_prompt": plan.get("negative_prompt", candidate.get("negative_prompt", "")),
                    "pipeline": plan.get("pipeline", candidate.get("pipeline", "")),
                    "mode": planned_mode,
                    "loras": plan.get("loras", candidate.get("loras", [])),
                    "model": plan.get("model", plan.get("model_name", candidate.get("model", candidate.get("model_name", "")))),
                    "artist_tags": plan.get("artist_tags", candidate.get("artist_tags", [])),
                    "style_preset_id": plan.get("style_preset_id", candidate.get("style_preset_id", "")),
                    "prompt_asset_ids": plan.get("prompt_asset_ids", candidate.get("prompt_asset_ids", [])),
                    "prompt_plan_id": plan.get("prompt_plan_id", candidate.get("prompt_plan_id", "")),
                    "locked_tags": plan.get("locked_tags", candidate.get("locked_tags", [])),
                }
            )
        return _normalize_intent(candidate, workspace), plan

    @handler
    async def bootstrap(request: web.Request) -> web.Response:
        workspace = str(request.query.get("workspace") or "natural").casefold()
        if workspace not in V7Store.WORKSPACES:
            raise ValueError("workspace must be random or natural")
        random_draft, natural_draft, presets, jobs = await asyncio.gather(
            asyncio.to_thread(store.get_draft, "random"),
            asyncio.to_thread(store.get_draft, "natural"),
            asyncio.to_thread(store.list_presets),
            runtime.list(limit=50),
        )
        return web.json_response(
            {
                "version": 7,
                "workspace": workspace,
                "draft": random_draft if workspace == "random" else natural_draft,
                "drafts": {"random": random_draft, "natural": natural_draft},
                "presets": presets,
                "jobs": {"items": [_public_job(item) for item in jobs]},
                "events": {
                    "url": "/api/v7/events",
                    "cursor": await asyncio.to_thread(store.latest_event_id),
                },
            }
        )

    @handler
    async def get_draft(request: web.Request) -> web.Response:
        return web.json_response(
            await asyncio.to_thread(store.get_draft, request.match_info["workspace"])
        )

    @handler
    async def save_draft(request: web.Request) -> web.Response:
        body = await _json_body(request)
        workspace = request.match_info["workspace"]
        if "revision" not in body:
            raise ValueError("revision is required")
        revision = int(body["revision"])
        normalized = _normalize_draft(body, workspace)
        saved = await asyncio.to_thread(
            store.save_draft,
            workspace,
            normalized,
            expected_revision=revision,
        )
        await events.publish(
            "draft.updated", saved, workspace=workspace, entity_id=workspace
        )
        return web.json_response(saved)

    @handler
    async def list_presets(_: web.Request) -> web.Response:
        return web.json_response(await asyncio.to_thread(store.list_presets))

    @handler
    async def create_preset(request: web.Request) -> web.Response:
        normalized = _normalize_preset(await _json_body(request))
        saved = await asyncio.to_thread(store.save_preset, normalized)
        await events.publish("preset.created", saved, entity_id=saved["id"])
        return web.json_response(saved, status=201)

    @handler
    async def update_preset(request: web.Request) -> web.Response:
        body = await _json_body(request)
        normalized = _normalize_preset(body, request.match_info["preset_id"])
        revision = body.get("revision")
        saved = await asyncio.to_thread(
            store.save_preset,
            normalized,
            preset_id=request.match_info["preset_id"],
            expected_revision=int(revision) if revision is not None else None,
        )
        await events.publish("preset.updated", saved, entity_id=saved["id"])
        return web.json_response(saved)

    @handler
    async def delete_preset(request: web.Request) -> web.Response:
        preset_id = request.match_info["preset_id"]
        if not await asyncio.to_thread(store.delete_preset, preset_id):
            raise KeyError(preset_id)
        await events.publish("preset.deleted", {"id": preset_id}, entity_id=preset_id)
        return web.json_response({"deleted": True, "id": preset_id})

    @handler
    async def models(_: web.Request) -> web.Response:
        resources = await comfy.resource_inventory()
        values = list(resources.get("models") or [])
        return web.json_response(
            {
                "items": [{"filename": value} for value in values],
                "models": values,
                "upscale_models": list(resources.get("upscale_models") or []),
                "count": len(values),
            }
        )

    @handler
    async def loras(_: web.Request) -> web.Response:
        return web.json_response(await comfy.lora_inventory())

    @handler
    async def prompt_assets(request: web.Request) -> web.Response:
        if studio_services is None:
            return web.json_response({"items": [], "total": 0, "page": 1, "page_size": 50})
        query: dict[str, Any] = dict(request.query)
        if "q" in query and "query" not in query:
            query["query"] = query.pop("q")
        query["page"] = _integer(query.get("page"), default=1, minimum=1, maximum=100000)
        query["page_size"] = _integer(query.get("page_size"), default=50, minimum=1, maximum=200)
        for name in ("categories", "traits", "tags"):
            if name in query:
                query[name] = request.query.getall(name)
        return web.json_response(_public(studio_services.prompts.search(**query)))

    @handler
    async def upload(request: web.Request) -> web.Response:
        if upload_asset is None:
            return web.json_response(
                {"error": "asset storage is unavailable", "code": "asset_store_unavailable"},
                status=503,
            )
        data = bytearray()
        if request.content_type.startswith("multipart/"):
            reader = await request.multipart()
            while True:
                part = await reader.next()
                if part is None:
                    break
                if not getattr(part, "filename", None):
                    continue
                while True:
                    chunk = await part.read_chunk(size=64 * 1024)
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > max_upload_bytes:
                        raise ValueError(f"upload exceeds {max_upload_bytes} bytes")
                break
        else:
            if request.content_length is not None and request.content_length > max_upload_bytes:
                raise ValueError(f"upload exceeds {max_upload_bytes} bytes")
            while True:
                chunk = await request.content.read(64 * 1024)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > max_upload_bytes:
                    raise ValueError(f"upload exceeds {max_upload_bytes} bytes")
        if not data:
            raise ValueError("upload is empty")
        result = upload_asset(bytes(data))
        if inspect.isawaitable(result):
            result = await result
        public = _public(result)
        asset_id = str(public.get("id") or public.get("asset_id") or "")
        await events.publish("asset.created", public, workspace="natural", entity_id=asset_id)
        return web.json_response(public, status=201)

    @handler
    async def preview_intent(request: web.Request) -> web.Response:
        body = await _json_body(request)
        nested = body.get("intent") if isinstance(body.get("intent"), Mapping) else {}
        workspace = str(body.get("workspace") or nested.get("workspace") or "natural")
        normalized, plan = await resolve_generation_intent(body, workspace)
        resolution = {
            "requires_confirmation": list(plan.get("requires_confirmation") or []),
            "matches": list(plan.get("matches") or []),
            "sources": dict(plan.get("sources") or {}),
            "plan_id": str(plan.get("id") or ""),
            "plan_revision": int(plan.get("revision") or 0),
            "plan_digest": str(plan.get("digest") or ""),
        }
        saved = await asyncio.to_thread(
            store.create_intent,
            {**normalized, "_resolution": resolution, "_plan": plan},
            workspace=workspace,
        )
        await events.publish(
            "intent.created",
            _public_intent(saved),
            workspace=workspace,
            entity_id=saved["id"],
        )
        requires_confirmation = list(plan.get("requires_confirmation") or [])
        return web.json_response(
            {
                "intent": _public_intent(saved),
                "plan": plan or None,
                "requires_confirmation": requires_confirmation,
                "resolution": {
                    "status": "confirmation_required" if requires_confirmation else "resolved",
                    "sources": plan.get("sources", {}),
                    "matches": plan.get("matches", {}),
                },
            },
            status=201,
        )

    @handler
    async def list_jobs(request: web.Request) -> web.Response:
        limit = _integer(request.query.get("limit"), default=50, minimum=1, maximum=200)
        statuses = [value.strip() for value in request.query.get("status", "").split(",") if value.strip()]
        items = await runtime.list(
            limit=limit,
            statuses=statuses or None,
            task_type=str(request.query.get("type") or "").strip(),
        )
        source_filter = str(request.query.get("workspace") or "").casefold()
        normalized = [_public_job(item) for item in items]
        if job_overlay is not None:
            normalized = [{**item, **_public(job_overlay(item))} for item in normalized]
        if source_filter:
            if source_filter not in V7Store.WORKSPACES | {"studio"}:
                raise ValueError("workspace filter is invalid")
            normalized = [item for item in normalized if item["source_workspace"] == source_filter]
        return web.json_response({"items": normalized})

    @handler
    async def create_job(request: web.Request) -> web.Response:
        if submit_job is None:
            return web.json_response(
                {"error": "generation scheduler is unavailable", "code": "scheduler_unavailable"},
                status=503,
            )
        body = await _json_body(request)
        nested = body.get("intent") if isinstance(body.get("intent"), Mapping) else {}
        workspace = str(body.get("workspace") or nested.get("workspace") or "natural")
        normalized, plan = await resolve_generation_intent(body, workspace, accept_frozen=True)
        requires_confirmation = list(plan.get("requires_confirmation") or [])
        if requires_confirmation:
            return web.json_response(
                {
                    "error": "natural language selection requires confirmation",
                    "code": "asset_confirmation_required",
                    "requires_confirmation": requires_confirmation,
                    "matches": plan.get("matches", {}),
                },
                status=409,
            )
        if workspace == "natural" and "_execution_plan" not in normalized:
            normalized["_execution_plan"] = dict(plan)
        intent = await asyncio.to_thread(store.create_intent, normalized, workspace=workspace)
        job = _public_job(await submit_job(intent), source_workspace=workspace)
        job_id = str(job.get("id") or job.get("run_id") or job.get("queue_id") or "")
        if not job_id:
            raise ValueError("scheduler did not return a job identifier")
        payload = {
            **job,
            "intent_id": intent["id"],
            "intent": _public_intent(intent),
            "source_workspace": workspace,
        }
        await events.publish("job.created", payload, workspace=workspace, entity_id=job_id)
        return web.json_response(payload, status=201)

    @handler
    async def get_job(request: web.Request) -> web.Response:
        task = await runtime.get(request.match_info["job_id"])
        result = _public_job(task)
        if job_overlay is not None:
            result.update(_public(job_overlay(result)))
        return web.json_response(result)

    @handler
    async def job_events(request: web.Request) -> web.Response:
        after = _integer(request.query.get("after"), default=0, minimum=0, maximum=2**63 - 1)
        return web.json_response(
            await runtime.events(run_id=request.match_info["job_id"], after_seq=after)
        )

    @handler
    async def cancel(request: web.Request) -> web.Response:
        job_id = request.match_info["job_id"]
        current = await runtime.get(job_id)
        result = await cancel_job(job_id, current) if cancel_job else None
        if result is None:
            if current.get("status") in {"succeeded", "partial", "failed", "cancelled", "timed_out", "interrupted"}:
                result = current
            else:
                return web.json_response(
                    {"error": "job cannot be cancelled by its owner", "code": "cancel_not_supported"},
                    status=409,
                )
        source = _source(current)
        result = _public_job(result, source_workspace=source)
        await events.publish("job.cancelled", result, workspace=source, entity_id=job_id)
        return web.json_response(result)

    @handler
    async def retry(request: web.Request) -> web.Response:
        job_id = request.match_info["job_id"]
        current = await runtime.get(job_id)
        if current.get("status") not in {"failed", "cancelled", "timed_out", "interrupted", "partial"}:
            return web.json_response(
                {"error": "only terminal unsuccessful jobs can be retried", "code": "retry_not_allowed"},
                status=409,
            )
        result = await retry_job(job_id, current) if retry_job else None
        if result is None:
            return web.json_response(
                {"error": "job has no replayable request", "code": "retry_not_supported"},
                status=409,
            )
        source = _source(current)
        result = _public_job(result, source_workspace=source)
        await events.publish(
            "job.retried",
            {"original_job_id": job_id, "job": result},
            workspace=source,
            entity_id=str(result.get("id") or result.get("run_id") or ""),
        )
        return web.json_response(result, status=201)

    @handler
    async def list_history(request: web.Request) -> web.Response:
        page = _integer(request.query.get("page"), default=1, minimum=1, maximum=1_000_000)
        limit = _integer(request.query.get("limit"), default=24, minimum=1, maximum=60)
        result = await history.list_images(page, limit)
        workspace = str(request.query.get("workspace") or "").casefold()
        if workspace:
            if workspace not in V7Store.WORKSPACES:
                raise ValueError("workspace filter is invalid")
            result["items"] = [item for item in result["items"] if item.get("source_workspace") == workspace]
        return web.json_response(result)

    @handler
    async def get_history(request: web.Request) -> web.Response:
        return web.json_response(await history.get_image(int(request.match_info["image_id"])))

    @handler
    async def delete_history(request: web.Request) -> web.Response:
        image_id = int(request.match_info["image_id"])
        record = await history.get_image(image_id)
        if not await history.delete_image(image_id):
            raise KeyError(image_id)
        workspace = str(record.get("source_workspace") or "random")
        await events.publish(
            "history.deleted",
            {"id": image_id},
            workspace=workspace,
            entity_id=str(image_id),
        )
        return web.json_response({"deleted": True, "id": image_id})

    @handler
    async def studio_snapshot(_: web.Request) -> web.Response:
        snapshot = _public(studio_services.snapshot()) if studio_services is not None else {}
        return web.json_response(
            {
                "version": 7,
                "capabilities": _public(studio_services.capabilities()) if studio_services is not None else {},
                "snapshot": snapshot,
                "deprecation_calls": await asyncio.to_thread(store.deprecation_summary),
            }
        )

    async def event_stream(request: web.Request) -> web.StreamResponse:
        try:
            header = request.headers.get("Last-Event-ID", "")
            after = int(header or request.query.get("after") or 0)
            if after < 0:
                raise ValueError
        except ValueError:
            return web.json_response({"error": "invalid event cursor", "code": "invalid_request"}, status=400)
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream; charset=utf-8",
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
        await response.prepare(request)
        try:
            async for event in events.stream(after_id=after):
                if event is None:
                    await response.write(b": keepalive\n\n")
                    continue
                data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                frame = f"id: {event['id']}\nevent: {event['event']}\ndata: {data}\n\n"
                await response.write(frame.encode("utf-8"))
        except (ConnectionResetError, asyncio.CancelledError, RuntimeError):
            pass
        return response

    app.router.add_get("/api/v7/bootstrap", bootstrap)
    app.router.add_get(r"/api/v7/drafts/{workspace:random|natural}", get_draft)
    app.router.add_put(r"/api/v7/drafts/{workspace:random|natural}", save_draft)
    app.router.add_get("/api/v7/presets", list_presets)
    app.router.add_post("/api/v7/presets", create_preset)
    app.router.add_put("/api/v7/presets/{preset_id}", update_preset)
    app.router.add_delete("/api/v7/presets/{preset_id}", delete_preset)
    app.router.add_get("/api/v7/assets/models", models)
    app.router.add_get("/api/v7/assets/loras", loras)
    app.router.add_get("/api/v7/prompt-assets", prompt_assets)
    app.router.add_post("/api/v7/uploads", upload)
    app.router.add_post("/api/v7/studio/uploads", upload)
    app.router.add_post("/api/v7/intents/preview", preview_intent)
    app.router.add_get("/api/v7/jobs", list_jobs)
    app.router.add_post("/api/v7/jobs", create_job)
    app.router.add_get("/api/v7/jobs/{job_id}", get_job)
    app.router.add_get("/api/v7/jobs/{job_id}/events", job_events)
    app.router.add_post("/api/v7/jobs/{job_id}/cancel", cancel)
    app.router.add_post("/api/v7/jobs/{job_id}/retry", retry)
    app.router.add_get("/api/v7/history", list_history)
    app.router.add_get(r"/api/v7/history/{image_id:\d+}", get_history)
    app.router.add_delete(r"/api/v7/history/{image_id:\d+}", delete_history)
    app.router.add_get("/api/v7/studio", studio_snapshot)
    app.router.add_get("/api/v7/events", event_stream)
