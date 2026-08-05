from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from aiohttp import web

from anima_studio.studio_services import PromptPlanConflictError

LlmCallback = Callable[[str, str], Awaitable[Any]]
StudioOperation = Callable[
    [str, threading.Event], Awaitable[Mapping[str, Any]] | Mapping[str, Any]
]


V7_STUDIO_CONTRACTS: tuple[dict[str, Any], ...] = (
    {"method": "GET", "path": "/api/v7/studio/contracts", "domain": "diagnostics"},
    {"method": "GET", "path": "/api/v7/studio/diagnostics", "domain": "diagnostics"},
    {"method": "PUT", "path": "/api/v7/studio/logs/level", "domain": "logs"},
    {"method": "GET", "path": "/api/v7/studio/providers", "domain": "providers"},
    {"method": "POST", "path": "/api/v7/studio/providers", "domain": "providers"},
    {
        "method": "PUT",
        "path": "/api/v7/studio/providers/bindings",
        "domain": "providers",
    },
    {
        "method": "PUT",
        "path": "/api/v7/studio/providers/{provider_id}",
        "domain": "providers",
    },
    {
        "method": "DELETE",
        "path": "/api/v7/studio/providers/{provider_id}",
        "domain": "providers",
    },
    {
        "method": "POST",
        "path": "/api/v7/studio/providers/{provider_id}/test",
        "domain": "providers",
        "manual": True,
    },
    {
        "method": "POST",
        "path": "/api/v7/studio/providers/{provider_id}/models",
        "domain": "providers",
        "manual": True,
    },
    {"method": "GET", "path": "/api/v7/studio/settings", "domain": "settings"},
    {"method": "PUT", "path": "/api/v7/studio/settings", "domain": "settings"},
    {
        "method": "GET",
        "path": "/api/v7/studio/lora-profiles",
        "domain": "lora_profiles",
    },
    {
        "method": "POST",
        "path": "/api/v7/studio/lora-profiles",
        "domain": "lora_profiles",
    },
    {
        "method": "PUT",
        "path": "/api/v7/studio/lora-profiles/{item_id}",
        "domain": "lora_profiles",
    },
    {
        "method": "DELETE",
        "path": "/api/v7/studio/lora-profiles/{item_id}",
        "domain": "lora_profiles",
    },
    {"method": "GET", "path": "/api/v7/studio/identities", "domain": "identities"},
    {"method": "POST", "path": "/api/v7/studio/identities", "domain": "identities"},
    {
        "method": "PUT",
        "path": "/api/v7/studio/identities/{item_id}",
        "domain": "identities",
    },
    {
        "method": "DELETE",
        "path": "/api/v7/studio/identities/{item_id}",
        "domain": "identities",
    },
    {"method": "GET", "path": "/api/v7/studio/prompt-lab", "domain": "prompt_lab"},
    {"method": "POST", "path": "/api/v7/studio/prompt-lab", "domain": "prompt_lab"},
    {
        "method": "PUT",
        "path": "/api/v7/studio/prompt-lab/{item_id}",
        "domain": "prompt_lab",
    },
    {
        "method": "DELETE",
        "path": "/api/v7/studio/prompt-lab/{item_id}",
        "domain": "prompt_lab",
    },
    {
        "method": "POST",
        "path": "/api/v7/studio/prompt-lab/{item_id}/confirm",
        "domain": "prompt_lab",
    },
    {
        "method": "POST",
        "path": "/api/v7/studio/prompt-lab/candidates",
        "domain": "prompt_lab",
    },
    {
        "method": "POST",
        "path": "/api/v7/studio/prompt-lab/batches/{batch_id}/confirm",
        "domain": "prompt_lab",
    },
    {
        "method": "GET",
        "path": "/api/v7/studio/prompt-plans",
        "domain": "prompt_plans",
    },
    {
        "method": "POST",
        "path": "/api/v7/studio/prompt-plans",
        "domain": "prompt_plans",
    },
    {
        "method": "GET",
        "path": "/api/v7/studio/prompt-plans/{item_id}",
        "domain": "prompt_plans",
    },
    {
        "method": "PUT",
        "path": "/api/v7/studio/prompt-plans/{item_id}",
        "domain": "prompt_plans",
    },
    {
        "method": "DELETE",
        "path": "/api/v7/studio/prompt-plans/{item_id}",
        "domain": "prompt_plans",
    },
    {
        "method": "GET",
        "path": "/api/v7/studio/prompt-assets/facets",
        "domain": "prompt_assets",
    },
    {
        "method": "POST",
        "path": "/api/v7/studio/prompt-assets/import",
        "domain": "prompt_assets",
    },
    {
        "method": "POST",
        "path": "/api/v7/studio/prompt-assets/update",
        "domain": "prompt_assets",
        "manual": True,
    },
    {"method": "GET", "path": "/api/v7/studio/loras", "domain": "loras"},
    {
        "method": "POST",
        "path": "/api/v7/studio/loras/refresh",
        "domain": "loras",
        "manual": True,
    },
    {
        "method": "POST",
        "path": "/api/v7/studio/loras/detail",
        "domain": "loras",
        "manual": True,
    },
    {"method": "GET", "path": "/api/v7/studio/loras/visuals", "domain": "loras"},
    {
        "method": "POST",
        "path": "/api/v7/studio/loras/analyze",
        "domain": "loras",
        "manual": True,
    },
    {
        "method": "POST",
        "path": "/api/v7/studio/loras/archive",
        "domain": "loras",
        "manual": True,
    },
    {
        "method": "POST",
        "path": "/api/v7/studio/loras/download",
        "domain": "loras",
        "manual": True,
    },
    {"method": "GET", "path": "/api/v7/studio/danbooru", "domain": "danbooru"},
    {"method": "GET", "path": "/api/v7/studio/danbooru/search", "domain": "danbooru"},
    {
        "method": "POST",
        "path": "/api/v7/studio/danbooru/build",
        "domain": "danbooru",
        "manual": True,
    },
    {
        "method": "PUT",
        "path": "/api/v7/studio/danbooru/schedule",
        "domain": "danbooru",
        "manual": True,
    },
    {
        "method": "POST",
        "path": "/api/v7/studio/danbooru/schedule/run",
        "domain": "danbooru",
        "manual": True,
    },
    {"method": "GET", "path": "/api/v7/studio/workflows", "domain": "workflows"},
    {
        "method": "GET",
        "path": "/api/v7/studio/config-profiles",
        "domain": "config_profiles",
    },
    {
        "method": "POST",
        "path": "/api/v7/studio/config-profiles",
        "domain": "config_profiles",
    },
    {
        "method": "GET",
        "path": "/api/v7/studio/config-profiles/{name}/export",
        "domain": "config_profiles",
    },
    {
        "method": "POST",
        "path": "/api/v7/studio/config-profiles/import",
        "domain": "config_profiles",
    },
    {
        "method": "POST",
        "path": "/api/v7/studio/config-profiles/{name}/activate",
        "domain": "config_profiles",
    },
    {
        "method": "DELETE",
        "path": "/api/v7/studio/config-profiles/{name}",
        "domain": "config_profiles",
    },
    {"method": "GET", "path": "/api/v7/studio/models/quarantine", "domain": "models"},
    {
        "method": "POST",
        "path": "/api/v7/studio/models/quarantine",
        "domain": "models",
        "manual": True,
    },
    {
        "method": "POST",
        "path": "/api/v7/studio/models/quarantine/{entry_id}/restore",
        "domain": "models",
        "manual": True,
    },
    {
        "method": "POST",
        "path": "/api/v7/studio/models/refresh",
        "domain": "models",
        "manual": True,
    },
    {"method": "GET", "path": "/api/v7/studio/logs", "domain": "logs"},
    {
        "method": "DELETE",
        "path": "/api/v7/studio/logs",
        "domain": "logs",
        "manual": True,
    },
    {
        "method": "POST",
        "path": "/api/v7/studio/operations/{run_id}/cancel",
        "domain": "operations",
    },
)


def _public(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _public(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _public(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_public(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _public_danbooru(value: Any) -> dict[str, Any]:
    def native_metadata(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {
                str(key): (
                    "anima_studio"
                    if str(key).casefold() == "generator"
                    else native_metadata(nested)
                )
                for key, nested in item.items()
            }
        if isinstance(item, list):
            return [native_metadata(nested) for nested in item]
        return item

    result = dict(native_metadata(_public(value)) or {})
    result["generator"] = "anima_studio"
    return result


def _public_operation(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(_public(value))
    metadata = result.get("metadata") if isinstance(result.get("metadata"), Mapping) else {}
    source = str(metadata.get("workspace") or "").casefold()
    task_type = str(result.pop("task_type", "") or "").casefold()
    if source not in {"random", "natural", "studio"}:
        if task_type.startswith("random"):
            source = "random"
        elif task_type.startswith("natural"):
            source = "natural"
        else:
            source = "studio"
    result["source_workspace"] = source
    result["type"] = "generation" if source in {"random", "natural"} else "studio_operation"
    return result


_SECRET_KEYS = frozenset(
    {"api_key", "apikey", "api_token", "authorization", "password", "secret", "token"}
)


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]"
            if str(key).casefold() in _SECRET_KEYS
            else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


def _bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ValueError("boolean value is invalid")


def _integer(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    if value in (None, ""):
        return default
    result = int(value)
    if not minimum <= result <= maximum:
        raise ValueError(f"integer value must be between {minimum} and {maximum}")
    return result


async def _json_body(request: web.Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception as error:
        raise ValueError("request body must be valid JSON") from error
    if not isinstance(body, dict):
        raise TypeError("request body must be an object")
    return body


class ManualConfirmationRequired(ValueError):
    pass


class StudioCapabilityUnavailable(RuntimeError):
    pass


def _confirmed(body: Mapping[str, Any]) -> bool:
    if _bool(body.get("confirm_manual")) is not True:
        raise ManualConfirmationRequired(
            "destructive, external, or filesystem operation requires confirm_manual=true"
        )
    return True


class _PromptBatches:
    def __init__(self, capacity: int = 50) -> None:
        self.capacity = max(1, int(capacity))
        self._items: OrderedDict[str, Any] = OrderedDict()

    def add(self, batch: Any) -> str:
        batch_id = str(
            getattr(batch, "batch_id", "") or _public(batch).get("batch_id") or ""
        )
        if not batch_id:
            raise ValueError("Prompt Lab did not return a batch identifier")
        self._items[batch_id] = batch
        self._items.move_to_end(batch_id)
        while len(self._items) > self.capacity:
            self._items.popitem(last=False)
        return batch_id

    def get(self, batch_id: str) -> Any:
        try:
            batch = self._items[batch_id]
        except KeyError as error:
            raise KeyError(batch_id) from error
        self._items.move_to_end(batch_id)
        return batch


class V7StudioOperationManager:
    """Persisted, serial FIFO for manual Studio operations."""

    TERMINAL = frozenset(
        {"succeeded", "partial", "failed", "cancelled", "timed_out", "interrupted"}
    )

    def __init__(self, runtime: Any, events: Any | None = None) -> None:
        self.runtime = runtime
        self.events = events
        self._queue: asyncio.Queue[tuple[str, StudioOperation, threading.Event]] = (
            asyncio.Queue()
        )
        self._worker: asyncio.Task[None] | None = None
        self._current_id = ""
        self._current_task: asyncio.Task[Any] | None = None
        self._cancellations: dict[str, threading.Event] = {}
        self._cancelled_pending: set[str] = set()
        self._closed = False

    async def _publish(
        self, event: str, payload: Mapping[str, Any], run_id: str
    ) -> None:
        if self.events is None:
            return
        await self.events.publish(
            event,
            _public(payload),
            workspace="studio",
            entity_id=run_id,
        )

    async def submit(
        self,
        task_type: str,
        operation: StudioOperation,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("Studio operation manager is closed")
        run_id = f"studio_{uuid.uuid4().hex}"
        await self.runtime.create(
            task_type,
            run_id=run_id,
            mode="manual",
            total_items=1,
            metadata={"workspace": "studio", "manual": True, **dict(metadata or {})},
        )
        cancellation = threading.Event()
        self._cancellations[run_id] = cancellation
        await self._queue.put((run_id, operation, cancellation))
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="anima-v7-studio-fifo")
        task = await self.runtime.get(run_id)
        await self._publish("job.queued", task, run_id)
        return {**task, "id": run_id, "source_workspace": "studio"}

    async def _run(self) -> None:
        while not self._closed:
            try:
                run_id, operation, cancellation = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            if run_id in self._cancelled_pending:
                self._cancelled_pending.discard(run_id)
                self._cancellations.pop(run_id, None)
                self._queue.task_done()
                continue
            self._current_id = run_id
            try:
                await self.runtime.start(run_id, total_items=1)
                await self.runtime.event(
                    run_id,
                    "run",
                    "Manual Studio operation started",
                    event_code="started",
                )
                await self._publish(
                    "job.started", await self.runtime.get(run_id), run_id
                )
                result = operation(run_id, cancellation)
                if inspect.isawaitable(result):
                    self._current_task = asyncio.create_task(result)
                    result = await self._current_task
                finished = await self.runtime.finish(
                    run_id,
                    "succeeded",
                    completed_items=1,
                    result={"operation": _public(result)},
                )
                await self._publish("job.succeeded", finished, run_id)
            except asyncio.CancelledError:
                await self._finish_cancelled(run_id)
            except Exception as error:  # noqa: BLE001 - persisted task boundary
                current = await self.runtime.get(run_id)
                if str(current.get("status") or "") not in self.TERMINAL:
                    current = await self.runtime.finish(
                        run_id,
                        "failed",
                        failed_items=1,
                        error_code=type(error).__name__,
                        error_summary=str(error)[:1000],
                    )
                await self._publish("job.failed", current, run_id)
            finally:
                self._current_id = ""
                self._current_task = None
                self._cancellations.pop(run_id, None)
                self._queue.task_done()

    async def _finish_cancelled(self, run_id: str) -> dict[str, Any]:
        current = await self.runtime.get(run_id)
        if str(current.get("status") or "") not in self.TERMINAL:
            current = await self.runtime.finish(
                run_id,
                "cancelled",
                error_code="cancelled",
                error_summary="Studio operation cancelled by the operator",
            )
        await self._publish("job.cancelled", current, run_id)
        return current

    async def cancel(self, run_id: str) -> dict[str, Any] | None:
        cancellation = self._cancellations.get(run_id)
        if cancellation is None:
            return None
        cancellation.set()
        if run_id == self._current_id:
            if self._current_task is not None:
                self._current_task.cancel()
                await asyncio.gather(self._current_task, return_exceptions=True)
            for _ in range(100):
                current = await self.runtime.get(run_id)
                if str(current.get("status") or "") in self.TERMINAL:
                    return current
                await asyncio.sleep(0)
            return await self.runtime.get(run_id)
        self._cancelled_pending.add(run_id)
        result = await self._finish_cancelled(run_id)
        return result

    async def close(self) -> None:
        self._closed = True
        for cancellation in self._cancellations.values():
            cancellation.set()
        if self._current_task is not None:
            self._current_task.cancel()
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
        while True:
            try:
                run_id, _, _ = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                current = await self.runtime.get(run_id)
                if str(current.get("status") or "") not in self.TERMINAL:
                    await self.runtime.finish(
                        run_id,
                        "interrupted",
                        error_code="studio_shutdown",
                        error_summary="Studio stopped before the queued operation began",
                    )
            finally:
                self._queue.task_done()


def setup_v7_studio_routes(
    app: web.Application,
    *,
    services: Any,
    engine: Any,
    runtime: Any,
    llm_callback: LlmCallback,
    events: Any | None = None,
    resource_runtime: Any | None = None,
    operation_manager: Any | None = None,
    prompt_plans: Any | None = None,
) -> Any:
    """Register native V7 Studio APIs without calling legacy HTTP routes."""

    operations = operation_manager or V7StudioOperationManager(runtime, events)
    prompt_batches = _PromptBatches()
    workspace_data = engine.workspace_data
    provider_registry = engine.registry
    provider_client = engine.provider_client

    def handler(
        function: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> Callable[..., Any]:
        async def wrapped(request: web.Request) -> web.StreamResponse:
            try:
                return await function(request)
            except ManualConfirmationRequired as error:
                return web.json_response(
                    {"error": str(error), "code": "manual_confirmation_required"},
                    status=409,
                )
            except PromptPlanConflictError as error:
                return web.json_response(
                    {
                        "error": "prompt_plan_conflict",
                        "code": "prompt_plan_conflict",
                        "current": _public(error.current),
                    },
                    status=409,
                )
            except KeyError as error:
                return web.json_response(
                    {
                        "error": "not_found",
                        "code": "not_found",
                        "id": str(error.args[0]),
                    },
                    status=404,
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - HTTP domain boundary
                name = type(error).__name__
                if name == "ManualActionRequiredError":
                    status, code = 409, "manual_confirmation_required"
                elif name == "CapabilityDisabledError":
                    status, code = 503, "capability_unavailable"
                elif name == "ModelQuarantineError":
                    status, code = 409, "model_operation_conflict"
                elif isinstance(error, (ValueError, TypeError)):
                    status, code = 400, "invalid_request"
                else:
                    status, code = 500, "internal_error"
                return web.json_response(
                    {"error": str(error)[:1000], "code": code},
                    status=status,
                )

        return wrapped

    async def publish_asset_changed(domain: str, payload: Mapping[str, Any]) -> None:
        if events is not None:
            await events.publish(
                "asset.changed", _public(payload), workspace="studio", entity_id=domain
            )

    @handler
    async def contracts(_: web.Request) -> web.Response:
        return web.json_response({"version": 7, "items": list(V7_STUDIO_CONTRACTS)})

    @handler
    async def diagnostics(_: web.Request) -> web.Response:
        tasks = [_public_operation(task) for task in await runtime.list(limit=10)]
        runtime_diagnostics: dict[str, Any] = {
            "comfy_online": False,
            "comfy_error": "ComfyUI runtime is not configured",
            "workflows": [],
        }
        capability_probe = getattr(engine, "capabilities", None)
        if resource_runtime is not None and callable(capability_probe):
            probed = capability_probe(resource_runtime)
            if inspect.isawaitable(probed):
                probed = await probed
            if isinstance(probed, Mapping):
                runtime_diagnostics = dict(probed)
        return web.json_response(
            {
                "version": 7,
                "native": True,
                "capabilities": _public(services.capabilities()),
                "runtime": _public(runtime_diagnostics),
                "providers": _public(provider_registry.snapshot()),
                "settings": _public(engine.settings_snapshot()),
                "danbooru": _public_danbooru(engine.danbooru.status()),
                "operations": {"items": tasks},
            }
        )

    @handler
    async def providers(_: web.Request) -> web.Response:
        return web.json_response(_public(provider_registry.snapshot()))

    @handler
    async def create_provider(request: web.Request) -> web.Response:
        item = provider_registry.upsert(await _json_body(request))
        await publish_asset_changed("providers", item)
        return web.json_response(_public(item), status=201)

    @handler
    async def update_provider(request: web.Request) -> web.Response:
        item = provider_registry.upsert(
            await _json_body(request), request.match_info["provider_id"]
        )
        await publish_asset_changed("providers", item)
        return web.json_response(_public(item))

    @handler
    async def delete_provider(request: web.Request) -> web.Response:
        provider_id = request.match_info["provider_id"]
        provider_registry.delete(provider_id)
        await publish_asset_changed("providers", {"deleted": True, "id": provider_id})
        return web.json_response({"deleted": True, "id": provider_id})

    @handler
    async def update_bindings(request: web.Request) -> web.Response:
        bindings = provider_registry.set_bindings(await _json_body(request))
        refresh = getattr(engine, "_refresh_services", None)
        if callable(refresh):
            refresh()
        await publish_asset_changed("provider_bindings", {"bindings": bindings})
        return web.json_response({"bindings": bindings})

    @handler
    async def test_provider(request: web.Request) -> web.Response:
        _confirmed(await _json_body(request))
        return web.json_response(
            _public(await provider_client.test(request.match_info["provider_id"]))
        )

    @handler
    async def provider_models(request: web.Request) -> web.Response:
        _confirmed(await _json_body(request))
        models = await provider_client.list_models(request.match_info["provider_id"])
        return web.json_response({"items": list(models), "count": len(models)})

    @handler
    async def settings(_: web.Request) -> web.Response:
        return web.json_response(_public(engine.settings_snapshot()))

    @handler
    async def update_settings(request: web.Request) -> web.Response:
        result = engine.update_settings(await _json_body(request))
        await publish_asset_changed("settings", result)
        return web.json_response(_public(result))

    def data_handlers(kind: str) -> tuple[Callable[..., Any], ...]:
        @handler
        async def listing(_: web.Request) -> web.Response:
            items = workspace_data.list(kind)
            return web.json_response({"items": _public(items), "count": len(items)})

        @handler
        async def create(request: web.Request) -> web.Response:
            item = workspace_data.upsert(kind, await _json_body(request))
            await publish_asset_changed(kind, item)
            return web.json_response(_public(item), status=201)

        @handler
        async def update(request: web.Request) -> web.Response:
            item = workspace_data.upsert(
                kind, await _json_body(request), request.match_info["item_id"]
            )
            await publish_asset_changed(kind, item)
            return web.json_response(_public(item))

        @handler
        async def delete(request: web.Request) -> web.Response:
            item_id = request.match_info["item_id"]
            workspace_data.delete(kind, item_id)
            payload = {"deleted": True, "id": item_id}
            await publish_asset_changed(kind, payload)
            return web.json_response(payload)

        return listing, create, update, delete

    @handler
    async def list_lora_profiles(_: web.Request) -> web.Response:
        items = workspace_data.list("lora_profiles")
        return web.json_response({"items": _public(items), "count": len(items)})

    async def lora_detail_for_filename(filename: str) -> Mapping[str, Any]:
        records = tuple(getattr(services.loras, "_records", ()))
        if not any(str(getattr(item, "name", "")) == filename for item in records):
            catalog = services.loras.catalog
            method = getattr(catalog, "list_loras", None)
            if catalog is None or not callable(method):
                raise RuntimeError("LoRA catalog backend is not configured")
            records = tuple(await method(force=False))
            services.loras.set_records(records)
        matches = tuple(
            item for item in records if str(getattr(item, "name", "")) == filename
        )
        if len(matches) != 1:
            raise KeyError("exact LoRA filename does not exist or is ambiguous")
        catalog = services.loras.catalog
        detail_method = getattr(catalog, "get_detail_v2", None)
        if not callable(detail_method):
            raise StudioCapabilityUnavailable(
                "LoRA detail backend is not configured"
            )
        detail = await detail_method(matches[0])
        if not isinstance(detail, Mapping):
            raise TypeError("LoRA detail backend returned an invalid record")
        return detail

    async def save_lora_profile(
        request: web.Request, item_id: str = ""
    ) -> web.Response:
        body = await _json_body(request)
        filename = str(body.get("filename") or "").strip().replace("\\", "/")
        if not filename:
            raise ValueError("filename is required")
        if not item_id:
            existing = next(
                (
                    item
                    for item in workspace_data.list("lora_profiles")
                    if str(item.get("filename") or "").casefold()
                    == filename.casefold()
                ),
                None,
            )
            if existing is not None:
                item_id = str(existing["id"])
        detail = await lora_detail_for_filename(filename)
        item = workspace_data.upsert(
            "lora_profiles",
            {
                **body,
                "filename": filename,
                "sha256": detail.get("sha256"),
                "source_fingerprint": detail.get("source_fingerprint"),
                "file_status": (
                    "current"
                    if detail.get("sha256") and detail.get("source_fingerprint")
                    else "unverified"
                ),
            },
            item_id,
        )
        await publish_asset_changed("lora_profiles", item)
        return web.json_response(_public(item), status=201 if not item_id else 200)

    @handler
    async def create_lora_profile(request: web.Request) -> web.Response:
        return await save_lora_profile(request)

    @handler
    async def update_lora_profile(request: web.Request) -> web.Response:
        return await save_lora_profile(request, request.match_info["item_id"])

    @handler
    async def delete_lora_profile(request: web.Request) -> web.Response:
        item_id = request.match_info["item_id"]
        workspace_data.delete("lora_profiles", item_id)
        payload = {"deleted": True, "id": item_id}
        await publish_asset_changed("lora_profiles", payload)
        return web.json_response(payload)

    @handler
    async def list_identities(_: web.Request) -> web.Response:
        items = workspace_data.list("identities")
        return web.json_response({"items": _public(items), "count": len(items)})

    async def save_identity(
        request: web.Request, item_id: str = ""
    ) -> web.Response:
        body = await _json_body(request)
        profile_id = str(
            body.get("lora_profile_id")
            or next(iter(body.get("lora_profile_ids") or ()), "")
        )
        profile = workspace_data.get("lora_profiles", profile_id)
        detail = await lora_detail_for_filename(str(profile["filename"]))
        character = str(
            body.get("character_canonical") or body.get("canonical_tag") or ""
        ).strip()
        if not character:
            raise ValueError("character_canonical is required")
        character_lookup = engine.danbooru.lookup(character)
        copyright = str(body.get("copyright_canonical") or "").strip()
        copyright_lookup = engine.danbooru.lookup(copyright) if copyright else None
        item = workspace_data.upsert_verified_identity(
            body,
            item_id,
            character_lookup=character_lookup,
            copyright_lookup=copyright_lookup,
            lora_detail=detail,
        )
        await publish_asset_changed("identities", item)
        return web.json_response(_public(item), status=201 if not item_id else 200)

    @handler
    async def create_identity(request: web.Request) -> web.Response:
        return await save_identity(request)

    @handler
    async def update_identity(request: web.Request) -> web.Response:
        return await save_identity(request, request.match_info["item_id"])

    @handler
    async def delete_identity(request: web.Request) -> web.Response:
        item_id = request.match_info["item_id"]
        workspace_data.delete("identities", item_id)
        payload = {"deleted": True, "id": item_id}
        await publish_asset_changed("identities", payload)
        return web.json_response(payload)

    lora_profiles = (
        list_lora_profiles,
        create_lora_profile,
        update_lora_profile,
        delete_lora_profile,
    )
    identities = (
        list_identities,
        create_identity,
        update_identity,
        delete_identity,
    )
    prompt_lab_items = data_handlers("prompt_lab")

    @handler
    async def confirm_prompt_lab_item(request: web.Request) -> web.Response:
        item = workspace_data.confirm_prompt_lab(request.match_info["item_id"])
        await publish_asset_changed("prompt_lab", item)
        return web.json_response(_public(item))

    @handler
    async def prompt_candidates(request: web.Request) -> web.Response:
        batch = services.prompts.generate_batch(**await _json_body(request))
        prompt_batches.add(batch)
        return web.json_response(_public(batch), status=201)

    @handler
    async def confirm_prompt_candidate(request: web.Request) -> web.Response:
        batch = prompt_batches.get(request.match_info["batch_id"])
        body = await _json_body(request)
        return web.json_response(
            _public(services.prompts.confirm_candidate(batch, body.get("selection", 1)))
        )

    def require_prompt_plans() -> Any:
        if prompt_plans is None:
            raise StudioCapabilityUnavailable("prompt plan store is not configured")
        return prompt_plans

    @handler
    async def list_prompt_plans(request: web.Request) -> web.Response:
        return web.json_response(
            _public(
                await asyncio.to_thread(
                    require_prompt_plans().list,
                    query=str(request.query.get("query") or ""),
                )
            )
        )

    @handler
    async def create_prompt_plan(request: web.Request) -> web.Response:
        item = await asyncio.to_thread(
            require_prompt_plans().create, await _json_body(request)
        )
        await publish_asset_changed("prompt_plans", item)
        return web.json_response(_public(item), status=201)

    @handler
    async def get_prompt_plan(request: web.Request) -> web.Response:
        item = await asyncio.to_thread(
            require_prompt_plans().get, request.match_info["item_id"]
        )
        return web.json_response(_public(item))

    @handler
    async def update_prompt_plan(request: web.Request) -> web.Response:
        body = await _json_body(request)
        if "revision" not in body or "digest" not in body:
            raise ValueError("revision and digest are required")
        item = await asyncio.to_thread(
            require_prompt_plans().update,
            request.match_info["item_id"],
            body,
            expected_revision=int(body["revision"]),
            expected_digest=str(body["digest"]),
        )
        await publish_asset_changed("prompt_plans", item)
        return web.json_response(_public(item))

    @handler
    async def delete_prompt_plan(request: web.Request) -> web.Response:
        body = await _json_body(request)
        if "revision" not in body or "digest" not in body:
            raise ValueError("revision and digest are required")
        item_id = request.match_info["item_id"]
        await asyncio.to_thread(
            require_prompt_plans().delete,
            item_id,
            expected_revision=int(body["revision"]),
            expected_digest=str(body["digest"]),
        )
        payload = {"deleted": True, "id": item_id}
        await publish_asset_changed("prompt_plans", payload)
        return web.json_response(payload)

    @handler
    async def prompt_facets(request: web.Request) -> web.Response:
        options: dict[str, Any] = dict(request.query)
        options["limit"] = _integer(
            options.get("limit"), default=200, minimum=1, maximum=200
        )
        return web.json_response(_public(services.prompts.facets(**options)))

    @handler
    async def import_prompt_assets(request: web.Request) -> web.Response:
        body = await _json_body(request)
        result = services.prompts.import_native_assets(
            body.get("assets") or [],
            source=str(body.get("source") or "anima-native"),
            mode=str(body.get("mode") or "replace_source"),
        )
        await publish_asset_changed("prompt_assets", result)
        return web.json_response(_public(result), status=201)

    @handler
    async def update_prompt_assets(request: web.Request) -> web.Response:
        body = await _json_body(request)
        confirmed = _confirmed(body)
        url = str(body.pop("url", "") or "").strip()
        body.pop("confirm_manual", None)
        if not url:
            raise ValueError("url is required")

        async def operation(_: str, __: threading.Event) -> Mapping[str, Any]:
            result = await services.prompts.update_from_url(
                url, confirm_manual=confirmed, **body
            )
            await publish_asset_changed("prompt_assets", result)
            return result

        task = await operations.submit(
            "prompt_asset_update", operation, metadata={"url": url}
        )
        return web.json_response(task, status=202)

    @handler
    async def loras(_: web.Request) -> web.Response:
        return web.json_response(_public(services.loras.snapshot()))

    @handler
    async def refresh_loras(request: web.Request) -> web.Response:
        body = await _json_body(request)
        confirmed = _confirmed(body)

        async def operation(_: str, __: threading.Event) -> Mapping[str, Any]:
            result = await services.loras.refresh_catalog(
                confirm_manual=confirmed,
                force=_bool(body.get("force"), default=True),
            )
            result = dict(result)
            result["identity_reconciliation"] = await reconcile_lora_profiles()
            await publish_asset_changed("loras", result)
            return result

        task = await operations.submit("lora_catalog_refresh", operation)
        return web.json_response(task, status=202)

    def matching_lora_records(names: Sequence[str]) -> tuple[Any, ...]:
        records = tuple(getattr(services.loras, "_records", ()))
        if not names:
            return records
        requested = {str(name) for name in names}
        matched = tuple(
            item for item in records if str(getattr(item, "name", "")) in requested
        )
        if {str(getattr(item, "name", "")) for item in matched} != requested:
            raise KeyError("one or more exact LoRA filenames do not exist")
        return matched

    async def reconcile_lora_profiles() -> dict[str, int]:
        records = tuple(getattr(services.loras, "_records", ()))
        by_name = {
            str(getattr(item, "name", "")).replace("\\", "/").casefold(): item
            for item in records
            if str(getattr(item, "name", ""))
        }
        current = stale = missing = 0
        catalog = services.loras.catalog
        detail_method = getattr(catalog, "get_detail_v2", None)
        for profile in workspace_data.list("lora_profiles"):
            profile_id = str(profile.get("id") or "")
            record = by_name.get(
                str(profile.get("filename") or "").replace("\\", "/").casefold()
            )
            if record is None:
                workspace_data.reconcile_lora_profile(
                    profile_id,
                    sha256="",
                    source_fingerprint="",
                    present=False,
                )
                missing += 1
                continue
            if not callable(detail_method):
                continue
            detail = await detail_method(record)
            before = workspace_data.get("lora_profiles", profile_id)
            updated = workspace_data.reconcile_lora_profile(
                profile_id,
                sha256=str(detail.get("sha256") or ""),
                source_fingerprint=str(detail.get("source_fingerprint") or ""),
                present=True,
            )
            changed = (
                before.get("sha256")
                and before.get("sha256") != updated.get("sha256")
            ) or (
                before.get("source_fingerprint")
                and before.get("source_fingerprint")
                != updated.get("source_fingerprint")
            )
            if changed:
                stale += 1
            else:
                current += 1
        return {"current": current, "stale": stale, "missing": missing}

    @handler
    async def lora_detail(request: web.Request) -> web.Response:
        body = await _json_body(request)
        _confirmed(body)
        filename = str(body.get("filename") or "").strip().replace("\\", "/")
        if not filename:
            raise ValueError("filename is required")
        records = matching_lora_records((filename,))
        catalog = services.loras.catalog
        if catalog is None or not callable(getattr(catalog, "get_detail_v2", None)):
            raise RuntimeError("LoRA detail backend is not configured")
        detail = await catalog.get_detail_v2(records[0])
        matching_profiles = [
            item
            for item in workspace_data.list("lora_profiles")
            if str(item.get("filename") or "").casefold() == filename.casefold()
        ]
        binding_status: list[dict[str, Any]] = []
        for profile in matching_profiles:
            updated = workspace_data.reconcile_lora_profile(
                str(profile["id"]),
                sha256=str(detail.get("sha256") or ""),
                source_fingerprint=str(detail.get("source_fingerprint") or ""),
                present=True,
            )
            binding_status.append(
                {
                    "profile_id": updated["id"],
                    "file_status": updated["file_status"],
                    "active_bindings": len(
                        workspace_data.active_identity_bindings(updated["id"])
                    ),
                }
            )
        return web.json_response(
            _public({**dict(detail), "identity_binding_status": binding_status})
        )

    @handler
    async def lora_visuals(request: web.Request) -> web.Response:
        if _bool(request.query.get("manifest"), default=False):
            return web.json_response(_public(services.loras.visual_manifest()))
        options: dict[str, Any] = dict(request.query)
        options.pop("manifest", None)
        options["page"] = _integer(
            options.get("page"), default=1, minimum=1, maximum=100000
        )
        options["page_size"] = _integer(
            options.get("page_size"), default=48, minimum=1, maximum=200
        )
        if "favorite_only" in options:
            options["favorite_only"] = _bool(options["favorite_only"])
        return web.json_response(_public(services.loras.visual_page(**options)))

    @handler
    async def analyze_loras(request: web.Request) -> web.Response:
        body = await _json_body(request)
        confirmed = _confirmed(body)
        names = tuple(str(item) for item in body.get("selected_names") or ())

        async def operation(run_id: str, _: threading.Event) -> Mapping[str, Any]:
            await services.loras.refresh_catalog(confirm_manual=True, force=True)
            records = matching_lora_records(names)
            catalog = services.loras.catalog
            details = [await catalog.get_detail_v2(item) for item in records]
            result = await services.loras.analyze(
                details,
                llm_callback,
                confirm_manual=confirmed,
                selected_names=names or None,
                run_id=run_id,
                requested_by="v7-web",
            )
            await publish_asset_changed("lora_analysis", result)
            return result

        task = await operations.submit(
            "lora_semantic_analysis",
            operation,
            metadata={"selected_names": list(names)},
        )
        return web.json_response(task, status=202)

    @handler
    async def archive_loras(request: web.Request) -> web.Response:
        body = await _json_body(request)
        confirmed = _confirmed(body)
        names = tuple(str(item) for item in body.get("selected_names") or ())

        async def operation(_: str, __: threading.Event) -> Mapping[str, Any]:
            await services.loras.refresh_catalog(confirm_manual=True, force=True)
            result = await services.loras.archive(
                llm_callback,
                confirm_manual=confirmed,
                selected_names=names or None,
                skip_when_unchanged=_bool(
                    body.get("skip_when_unchanged"), default=True
                ),
            )
            await publish_asset_changed("lora_archive", result)
            return result

        task = await operations.submit(
            "lora_archive", operation, metadata={"selected_names": list(names)}
        )
        return web.json_response(task, status=202)

    @handler
    async def download_lora(request: web.Request) -> web.Response:
        body = await _json_body(request)
        confirmed = _confirmed(body)
        url = str(body.get("url") or "").strip()
        if not url:
            raise ValueError("url is required")

        async def operation(_: str, __: threading.Event) -> Mapping[str, Any]:
            result = await services.loras.download(url, confirm_manual=confirmed)
            await publish_asset_changed("loras", result)
            return result

        task = await operations.submit(
            "lora_download", operation, metadata={"url": url}
        )
        return web.json_response(task, status=202)

    @handler
    async def danbooru_status(_: web.Request) -> web.Response:
        return web.json_response(_public_danbooru(services.danbooru.snapshot()))

    @handler
    async def danbooru_search(request: web.Request) -> web.Response:
        return web.json_response(
            _public(
                engine.danbooru_search(
                    str(request.query.get("q") or ""),
                    str(request.query.get("category") or ""),
                )
            )
        )

    @handler
    async def build_danbooru(request: web.Request) -> web.Response:
        body = await _json_body(request)
        confirmed = _confirmed(body)
        options = dict(body)
        options.pop("confirm_manual", None)

        async def operation(
            run_id: str, cancellation: threading.Event
        ) -> Mapping[str, Any]:
            async def progress(event: dict[str, Any]) -> None:
                await runtime.event(
                    run_id,
                    "progress",
                    str(event.get("message") or "Danbooru index progress"),
                    event_code=str(event.get("event") or "progress"),
                    details=_redact(event),
                )

            result = await services.danbooru.build(
                options,
                confirm_manual=confirmed,
                progress=progress,
                cancel_event=cancellation,
            )
            await publish_asset_changed("danbooru", result)
            return result

        task = await operations.submit("danbooru_index_build", operation)
        return web.json_response(task, status=202)

    @handler
    async def configure_danbooru_schedule(request: web.Request) -> web.Response:
        body = await _json_body(request)
        confirmed = _confirmed(body)
        options = body.get("options")
        result = services.danbooru.configure_schedule(
            enabled=bool(body.get("enabled")),
            interval_hours=int(body.get("interval_hours") or 168),
            options=dict(options) if isinstance(options, Mapping) else None,
            confirm_manual=confirmed,
        )
        await publish_asset_changed("danbooru_schedule", result)
        return web.json_response(_public(result))

    @handler
    async def run_danbooru_schedule(request: web.Request) -> web.Response:
        body = await _json_body(request)
        confirmed = _confirmed(body)

        async def operation(
            run_id: str, cancellation: threading.Event
        ) -> Mapping[str, Any]:
            async def progress(event: dict[str, Any]) -> None:
                await runtime.event(
                    run_id,
                    "progress",
                    str(event.get("message") or "Danbooru scheduled update progress"),
                    event_code=str(event.get("event") or "progress"),
                    details=_redact(event),
                )

            result = await services.danbooru.run_scheduled(
                confirm_manual=confirmed,
                force=bool(body.get("force")),
                progress=progress,
                cancel_event=cancellation,
            )
            await publish_asset_changed("danbooru_schedule", result)
            return result

        task = await operations.submit("danbooru_scheduled_update", operation)
        return web.json_response(task, status=202)

    @handler
    async def workflows(_: web.Request) -> web.Response:
        items = services.workflows.list_workflows()
        return web.json_response({"items": _public(items), "count": len(items)})

    @handler
    async def profiles(_: web.Request) -> web.Response:
        items = services.workflows.list_profiles()
        return web.json_response({"items": _public(items), "count": len(items)})

    @handler
    async def save_profile(request: web.Request) -> web.Response:
        body = await _json_body(request)
        item = services.workflows.save_profile(
            str(body.get("name") or ""),
            body.get("config") or {},
            overwrite=_bool(body.get("overwrite")),
            activate=_bool(body.get("activate")),
        )
        await publish_asset_changed("config_profiles", item)
        return web.json_response(_redact(_public(item)), status=201)

    @handler
    async def export_profile(request: web.Request) -> web.Response:
        return web.json_response(
            _redact(
                _public(services.workflows.export_profile(request.match_info["name"]))
            )
        )

    @handler
    async def import_profile(request: web.Request) -> web.Response:
        body = await _json_body(request)
        item = services.workflows.import_profile(
            body.get("profile") or body,
            overwrite=_bool(body.get("overwrite")),
        )
        await publish_asset_changed("config_profiles", item)
        return web.json_response(_redact(_public(item)), status=201)

    @handler
    async def activate_profile(request: web.Request) -> web.Response:
        await _json_body(request)
        config = dict(getattr(engine, "config", {}) or {})
        item = services.workflows.activate_profile(
            request.match_info["name"],
            config,
            persist_updates=lambda updates: bool(engine.update_settings(updates)),
        )
        await publish_asset_changed("config_profiles", item)
        return web.json_response(_redact(_public(item)))

    @handler
    async def delete_profile(request: web.Request) -> web.Response:
        item = services.workflows.delete_profile(request.match_info["name"])
        await publish_asset_changed("config_profiles", item)
        return web.json_response(_redact(_public(item)))

    @handler
    async def quarantine_snapshot(_: web.Request) -> web.Response:
        return web.json_response(_public(services.models.snapshot()))

    @handler
    async def quarantine_model(request: web.Request) -> web.Response:
        body = await _json_body(request)
        _confirmed(body)
        item = services.models.quarantine(
            str(body.get("kind") or ""),
            str(body.get("exact_name") or ""),
            confirm_name=str(body.get("confirm_name") or ""),
            references=body.get("references") or (),
        )
        await publish_asset_changed("models", item)
        return web.json_response(_public(item), status=201)

    @handler
    async def restore_model(request: web.Request) -> web.Response:
        body = await _json_body(request)
        _confirmed(body)
        item = services.models.restore(
            request.match_info["entry_id"],
            confirm_name=str(body.get("confirm_name") or ""),
        )
        await publish_asset_changed("models", item)
        return web.json_response(_public(item))

    @handler
    async def refresh_models(request: web.Request) -> web.Response:
        _confirmed(await _json_body(request))
        target = resource_runtime or getattr(engine, "comfy", None)
        method = getattr(target, "resource_inventory", None)
        if not callable(method):
            raise StudioCapabilityUnavailable(
                "model inventory runtime is not configured"
            )
        result = method()
        if inspect.isawaitable(result):
            result = await result
        await publish_asset_changed("models", _public(result))
        return web.json_response(_public(result))

    @handler
    async def logs(request: web.Request) -> web.Response:
        limit = _integer(
            request.query.get("limit"), default=200, minimum=1, maximum=1000
        )
        after_seq = _integer(
            request.query.get("after"), default=0, minimum=0, maximum=2_147_483_647
        )
        levels = tuple(
            level.strip()
            for value in request.query.getall("level", [])
            for level in value.split(",")
            if level.strip()
        )
        result = await runtime.read_logs(
            after_seq=after_seq,
            limit=limit,
            levels=levels or None,
            category=str(request.query.get("category") or ""),
            run_id=str(request.query.get("run_id") or ""),
        )
        task_logs = _redact(_public(result.get("entries") or []))
        natural_logs = _redact(_public(workspace_data.redacted_logs(limit)))
        text_filter = str(request.query.get("filter") or "").strip().casefold()
        if text_filter:
            task_logs = [
                item
                for item in task_logs
                if text_filter in " ".join(str(value) for value in item.values()).casefold()
            ]
            natural_logs = [
                item
                for item in natural_logs
                if text_filter in " ".join(str(value) for value in item.values()).casefold()
            ]
        return web.json_response(
            {
                "items": task_logs,
                "natural_items": natural_logs,
                "count": len(task_logs),
                "cursor": int(result.get("cursor") or after_seq),
            }
        )

    @handler
    async def clear_logs(request: web.Request) -> web.Response:
        _confirmed(await _json_body(request))
        cleared = await runtime.clear_logs()
        return web.json_response({"cleared": int(cleared)})

    @handler
    async def update_log_level(request: web.Request) -> web.Response:
        body = await _json_body(request)
        level_name = str(body.get("level") or "").strip().upper()
        if level_name not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("unsupported log level")
        level = int(getattr(logging, level_name))
        for logger_name in ("anima_studio", "anima_natural", "anima_webui"):
            logging.getLogger(logger_name).setLevel(level)
        return web.json_response({"level": level_name})

    @handler
    async def cancel_operation(request: web.Request) -> web.Response:
        run_id = request.match_info["run_id"]
        result = await operations.cancel(run_id)
        if result is None:
            current = await runtime.get(run_id)
            if str(current.get("status") or "") in operations.TERMINAL:
                return web.json_response(_public(current))
            return web.json_response(
                {
                    "error": "operation is not owned by Studio",
                    "code": "cancel_not_supported",
                },
                status=409,
            )
        return web.json_response(_public(result))

    app.router.add_get("/api/v7/studio/contracts", contracts)
    app.router.add_get("/api/v7/studio/diagnostics", diagnostics)
    app.router.add_get("/api/v7/studio/providers", providers)
    app.router.add_post("/api/v7/studio/providers", create_provider)
    app.router.add_put("/api/v7/studio/providers/bindings", update_bindings)
    app.router.add_post("/api/v7/studio/providers/{provider_id}/test", test_provider)
    app.router.add_post(
        "/api/v7/studio/providers/{provider_id}/models", provider_models
    )
    app.router.add_put("/api/v7/studio/providers/{provider_id}", update_provider)
    app.router.add_delete("/api/v7/studio/providers/{provider_id}", delete_provider)
    app.router.add_get("/api/v7/studio/settings", settings)
    app.router.add_put("/api/v7/studio/settings", update_settings)
    for path, group in (
        ("lora-profiles", lora_profiles),
        ("identities", identities),
        ("prompt-lab", prompt_lab_items),
    ):
        app.router.add_get(f"/api/v7/studio/{path}", group[0])
        app.router.add_post(f"/api/v7/studio/{path}", group[1])
        app.router.add_put(f"/api/v7/studio/{path}/{{item_id}}", group[2])
        app.router.add_delete(f"/api/v7/studio/{path}/{{item_id}}", group[3])
    app.router.add_post(
        "/api/v7/studio/prompt-lab/{item_id}/confirm", confirm_prompt_lab_item
    )
    app.router.add_post("/api/v7/studio/prompt-lab/candidates", prompt_candidates)
    app.router.add_post(
        "/api/v7/studio/prompt-lab/batches/{batch_id}/confirm",
        confirm_prompt_candidate,
    )
    app.router.add_get("/api/v7/studio/prompt-plans", list_prompt_plans)
    app.router.add_post("/api/v7/studio/prompt-plans", create_prompt_plan)
    app.router.add_get("/api/v7/studio/prompt-plans/{item_id}", get_prompt_plan)
    app.router.add_put("/api/v7/studio/prompt-plans/{item_id}", update_prompt_plan)
    app.router.add_delete("/api/v7/studio/prompt-plans/{item_id}", delete_prompt_plan)
    app.router.add_get("/api/v7/studio/prompt-assets/facets", prompt_facets)
    app.router.add_post("/api/v7/studio/prompt-assets/import", import_prompt_assets)
    app.router.add_post("/api/v7/studio/prompt-assets/update", update_prompt_assets)
    app.router.add_get("/api/v7/studio/loras", loras)
    app.router.add_post("/api/v7/studio/loras/refresh", refresh_loras)
    app.router.add_post("/api/v7/studio/loras/detail", lora_detail)
    app.router.add_get("/api/v7/studio/loras/visuals", lora_visuals)
    app.router.add_post("/api/v7/studio/loras/analyze", analyze_loras)
    app.router.add_post("/api/v7/studio/loras/archive", archive_loras)
    app.router.add_post("/api/v7/studio/loras/download", download_lora)
    app.router.add_get("/api/v7/studio/danbooru", danbooru_status)
    app.router.add_get("/api/v7/studio/danbooru/search", danbooru_search)
    app.router.add_post("/api/v7/studio/danbooru/build", build_danbooru)
    app.router.add_put(
        "/api/v7/studio/danbooru/schedule", configure_danbooru_schedule
    )
    app.router.add_post(
        "/api/v7/studio/danbooru/schedule/run", run_danbooru_schedule
    )
    app.router.add_get("/api/v7/studio/workflows", workflows)
    app.router.add_get("/api/v7/studio/config-profiles", profiles)
    app.router.add_post("/api/v7/studio/config-profiles", save_profile)
    app.router.add_get("/api/v7/studio/config-profiles/{name}/export", export_profile)
    app.router.add_post("/api/v7/studio/config-profiles/import", import_profile)
    app.router.add_post(
        "/api/v7/studio/config-profiles/{name}/activate", activate_profile
    )
    app.router.add_delete("/api/v7/studio/config-profiles/{name}", delete_profile)
    app.router.add_get("/api/v7/studio/models/quarantine", quarantine_snapshot)
    app.router.add_post("/api/v7/studio/models/quarantine", quarantine_model)
    app.router.add_post(
        "/api/v7/studio/models/quarantine/{entry_id}/restore", restore_model
    )
    app.router.add_post("/api/v7/studio/models/refresh", refresh_models)
    app.router.add_get("/api/v7/studio/logs", logs)
    app.router.add_put("/api/v7/studio/logs/level", update_log_level)
    app.router.add_delete("/api/v7/studio/logs", clear_logs)
    app.router.add_post("/api/v7/studio/operations/{run_id}/cancel", cancel_operation)
    return operations


__all__ = [
    "V7_STUDIO_CONTRACTS",
    "V7StudioOperationManager",
    "setup_v7_studio_routes",
]
