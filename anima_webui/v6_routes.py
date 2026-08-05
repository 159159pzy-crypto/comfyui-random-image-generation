from __future__ import annotations

import asyncio
import inspect
import json
import threading
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from aiohttp import web

from anima_natural.engine import NaturalEngine
from anima_natural.studio import (
    CapabilityDisabledError,
    DanbooruApiBuilder,
    LoraAnalysisPipeline,
    LoraArchiveService,
    LoraCatalogService,
    LoraDownloadService,
    LoraVisualService,
    ManualActionRequiredError,
    ModelQuarantineError,
    StudioServiceError,
    StudioServices,
)

from .task_runtime import StudioTaskRuntime

STUDIO_SERVICES_KEY: web.AppKey[StudioServices] = web.AppKey(
    "studio_services", StudioServices
)
STUDIO_OPERATIONS_KEY: web.AppKey[StudioOperationManager] = web.AppKey(
    "studio_operations", object
)

Operation = Callable[[str, threading.Event], Awaitable[Mapping[str, Any] | Any]]
LlmCallback = Callable[[str, str], Awaitable[Any]]


def _public(value: Any) -> Any:
    if is_dataclass(value):
        return _public(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _public(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_public(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _json_error(error: Exception) -> web.Response:
    if isinstance(error, CapabilityDisabledError):
        status = 501
        code = "capability_disabled"
    elif isinstance(error, ManualActionRequiredError):
        status = 409
        code = "manual_confirmation_required"
    elif isinstance(error, ModelQuarantineError):
        status = 409
        code = "quarantine_conflict"
    elif isinstance(error, (StudioServiceError, ValueError, TypeError, KeyError)):
        status = 400
        code = "invalid_request"
    else:
        status = 502
        code = type(error).__name__
    return web.json_response(
        {"error": str(error)[:1000] or type(error).__name__, "code": code},
        status=status,
    )


def _handler(function: Callable[..., Awaitable[web.StreamResponse]]) -> Callable[..., Any]:
    async def wrapped(request: web.Request) -> web.StreamResponse:
        try:
            return await function(request)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return _json_error(error)

    return wrapped


async def _json_body(request: web.Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as error:
        raise ValueError("request body must be valid JSON") from error
    if not isinstance(payload, dict):
        raise ValueError("request body must be an object")
    return payload


def _bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if str(value).strip().casefold() in {"1", "true", "yes"}:
        return True
    if str(value).strip().casefold() in {"0", "false", "no"}:
        return False
    raise ValueError("boolean value is invalid")


def _integer(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    if value in (None, ""):
        return default
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("integer value is invalid") from error
    if not minimum <= result <= maximum:
        raise ValueError(f"integer value must be between {minimum} and {maximum}")
    return result


class StudioOperationManager:
    """Run explicit long operations through the shared persistent task ledger."""

    def __init__(self, runtime: StudioTaskRuntime) -> None:
        self.runtime = runtime
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancellations: dict[str, threading.Event] = {}

    async def submit(
        self,
        task_type: str,
        operation: Operation,
        *,
        mode: str = "manual",
        metadata: Mapping[str, Any] | None = None,
        lifecycle_managed_by_operation: bool = False,
    ) -> dict[str, Any]:
        run_id = f"studio-{uuid.uuid4().hex}"
        await self.runtime.create(
            task_type,
            run_id=run_id,
            mode=mode,
            metadata={"manual": True, **dict(metadata or {})},
        )
        cancel_event = threading.Event()
        task = asyncio.create_task(
            self._run(
                run_id,
                operation,
                cancel_event,
                lifecycle_managed_by_operation=lifecycle_managed_by_operation,
            ),
            name=f"{task_type}:{run_id}",
        )
        self._tasks[run_id] = task
        self._cancellations[run_id] = cancel_event
        return await self.runtime.get(run_id)

    async def _run(
        self,
        run_id: str,
        operation: Operation,
        cancel_event: threading.Event,
        *,
        lifecycle_managed_by_operation: bool,
    ) -> None:
        try:
            if not lifecycle_managed_by_operation:
                await self.runtime.start(run_id)
                await self.runtime.event(
                    run_id, "run", "Manual studio operation started", event_code="started"
                )
            result = operation(run_id, cancel_event)
            if inspect.isawaitable(result):
                result = await result
            if not lifecycle_managed_by_operation:
                await self.runtime.finish(
                    run_id,
                    "succeeded",
                    completed_items=1,
                    result={"operation": _public(result)},
                )
        except asyncio.CancelledError:
            try:
                current = await self.runtime.get(run_id)
                if current["status"] not in {
                    "succeeded", "partial", "failed", "cancelled", "timed_out", "interrupted"
                }:
                    await self.runtime.finish(
                        run_id,
                        "cancelled",
                        error_code="cancelled",
                        error_summary="Operation cancelled by the operator",
                    )
            except (KeyError, RuntimeError):
                pass
        except Exception as error:
            try:
                current = await self.runtime.get(run_id)
                if current["status"] not in {
                    "succeeded", "partial", "failed", "cancelled", "timed_out", "interrupted"
                }:
                    await self.runtime.finish(
                        run_id,
                        "failed",
                        failed_items=1,
                        error_code=type(error).__name__,
                        error_summary=str(error)[:1000],
                    )
            except (KeyError, RuntimeError):
                pass
        finally:
            self._tasks.pop(run_id, None)
            self._cancellations.pop(run_id, None)

    async def cancel(self, run_id: str) -> dict[str, Any] | None:
        task = self._tasks.get(run_id)
        if task is None:
            return None
        self._cancellations[run_id].set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return await self.runtime.get(run_id)

    async def close(self) -> None:
        for event in self._cancellations.values():
            event.set()
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks.values()), return_exceptions=True)


class PromptBatchRegistry:
    def __init__(self, capacity: int = 50) -> None:
        self.capacity = max(1, capacity)
        self._batches: OrderedDict[str, Any] = OrderedDict()

    def add(self, batch: Any) -> None:
        identifier = str(batch.batch_id)
        self._batches[identifier] = batch
        self._batches.move_to_end(identifier)
        while len(self._batches) > self.capacity:
            self._batches.popitem(last=False)

    def get(self, identifier: str) -> Any:
        try:
            batch = self._batches[identifier]
        except KeyError as error:
            raise KeyError("prompt lab batch does not exist or has expired") from error
        self._batches.move_to_end(identifier)
        return batch


def _model_roots(root: Path) -> dict[str, list[Path]]:
    comfy_root = root.parent / "comfyui"
    models = comfy_root / "models"
    candidates = {
        "checkpoint": [models / "checkpoints"],
        "unet": [models / "diffusion_models", models / "unet"],
        "vae": [models / "vae"],
        "lora": [models / "loras"],
    }
    return {
        kind: [path for path in paths if path.is_dir()]
        for kind, paths in candidates.items()
    }


def _reference_checker(root: Path) -> Callable[[str, str], list[str]]:
    allowed = [root / "templates", root / "data", root]

    def walk(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, Mapping):
            result: list[str] = []
            for item in value.values():
                result.extend(walk(item))
            return result
        if isinstance(value, list):
            result = []
            for item in value:
                result.extend(walk(item))
            return result
        return []

    def check(_: str, exact_name: str) -> list[str]:
        expected = {exact_name.casefold(), Path(exact_name).name.casefold()}
        matches: list[str] = []
        seen_paths: set[Path] = set()
        for base in allowed:
            if not base.exists():
                continue
            paths = base.glob("*.json") if base == root else base.rglob("*.json")
            for path in paths:
                resolved = path.resolve(strict=False)
                if resolved in seen_paths or "quarantine" in resolved.parts or "backups" in resolved.parts:
                    continue
                seen_paths.add(resolved)
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                for value in walk(payload):
                    normalized = value.strip().replace("\\", "/")
                    if normalized.casefold() in expected:
                        matches.append(normalized)
        return list(dict.fromkeys(matches))

    return check


def create_studio_services(
    root: Path,
    natural_engine: NaturalEngine,
    task_runtime: StudioTaskRuntime,
    *,
    comfy_url: str,
) -> StudioServices:
    roots = _model_roots(root)
    runtime_values = asdict(natural_engine._runtime_settings())
    runtime_values.update(natural_engine.config)
    runtime_values.update(
        {
            "comfyui_url": comfy_url,
            "api_token": "",
            "lora_catalog_url": "",
            "lora_visual_roots": [str(item) for item in roots.get("lora", ())],
            "lora_visual_cache_mb": 512,
            "lora_visual_warmup_workers": 2,
            "lora_visual_preview_max_mb": 16,
            "lora_visual_thumbnail_size": 320,
            "lora_catalog_timeout": 30,
            "lora_max_results": 1000,
            "lora_download_timeout": 3600,
            "lora_download_allowed_hosts": [
                "civitai.com",
                "www.civitai.com",
            ],
        }
    )
    settings = SimpleNamespace(**runtime_values)
    catalog = LoraCatalogService(settings)
    lora_roots = [Path(item) for item in settings.lora_visual_roots]
    visuals = LoraVisualService(
        lora_roots,
        root / "data" / "studio" / "lora_visual_cache",
        max_cache_bytes=int(settings.lora_visual_cache_mb) * 1024 * 1024,
        max_workers=settings.lora_visual_warmup_workers,
        max_preview_bytes=int(settings.lora_visual_preview_max_mb) * 1024 * 1024,
        thumbnail_size=(settings.lora_visual_thumbnail_size,) * 2,
    )
    analyzer = LoraAnalysisPipeline(
        natural_engine.semantic_index,
        natural_engine.semantic_path,
        task_runtime.store,
    )
    archiver = LoraArchiveService(root / "data" / "studio" / "lora_archive.json")
    downloader = LoraDownloadService(settings, catalog)
    danbooru_builder = DanbooruApiBuilder(
        natural_engine.danbooru,
        root / "data" / "studio" / "danbooru.checkpoint.sqlite3",
    )
    return StudioServices.create_local(
        root / "data" / "studio",
        settings=settings,
        workflow_dir=root / "anima_natural" / "upstream" / "workflow",
        model_roots=roots,
        lora_catalog=catalog,
        lora_visuals=visuals,
        lora_analyzer=analyzer,
        lora_archiver=archiver,
        lora_downloader=downloader,
        danbooru_builder=danbooru_builder,
        reference_checker=_reference_checker(root),
    )


async def close_studio_services(services: StudioServices) -> None:
    for backend in (
        services.loras.downloader,
        services.loras.catalog,
    ):
        close = getattr(backend, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result
    close_visuals = getattr(services.loras.visuals, "close", None)
    if callable(close_visuals):
        close_visuals(wait=False)


def setup_v6_routes(
    app: web.Application,
    *,
    services: StudioServices,
    runtime: StudioTaskRuntime,
    llm_callback: LlmCallback,
) -> StudioOperationManager:
    operations = StudioOperationManager(runtime)
    prompt_batches = PromptBatchRegistry()
    app[STUDIO_SERVICES_KEY] = services
    app[STUDIO_OPERATIONS_KEY] = operations

    @_handler
    async def capabilities(_: web.Request) -> web.Response:
        return web.json_response({"version": 6, "capabilities": services.capabilities()})

    @_handler
    async def studio_snapshot(_: web.Request) -> web.Response:
        return web.json_response(services.snapshot())

    @_handler
    async def prompt_assets(request: web.Request) -> web.Response:
        query = dict(request.query)
        if "q" in query and "query" not in query:
            query["query"] = query.pop("q")
        for key in ("categories", "traits", "tags"):
            if key in query:
                query[key] = request.query.getall(key)
        query["page"] = _integer(query.get("page"), default=1, minimum=1, maximum=100000)
        query["page_size"] = _integer(
            query.get("page_size"), default=50, minimum=1, maximum=200
        )
        query["favorite_only"] = _bool(query.get("favorite_only"), default=False)
        if "custom_only" in query:
            query["custom_only"] = _bool(query["custom_only"])
        return web.json_response(services.prompts.search(**query))

    @_handler
    async def prompt_facets(request: web.Request) -> web.Response:
        options: dict[str, Any] = dict(request.query)
        options["limit"] = _integer(
            options.get("limit"), default=200, minimum=1, maximum=200
        )
        options["favorite_only"] = _bool(options.get("favorite_only"), default=False)
        if "custom_only" in options:
            options["custom_only"] = _bool(options["custom_only"])
        return web.json_response(services.prompts.facets(**options))

    @_handler
    async def import_prompt_assets(request: web.Request) -> web.Response:
        body = await _json_body(request)
        result = services.prompts.import_native_assets(
            body.get("assets") or [],
            source=str(body.get("source") or "anima-native"),
            mode=str(body.get("mode") or "replace_source"),
        )
        return web.json_response(result, status=201)

    @_handler
    async def prompt_candidates(request: web.Request) -> web.Response:
        body = await _json_body(request)
        batch = services.prompts.generate_batch(**body)
        prompt_batches.add(batch)
        return web.json_response(_public(batch), status=201)

    @_handler
    async def confirm_prompt_candidate(request: web.Request) -> web.Response:
        body = await _json_body(request)
        batch = prompt_batches.get(request.match_info["batch_id"])
        return web.json_response(
            services.prompts.confirm_candidate(batch, body.get("selection", 1))
        )

    @_handler
    async def lora_snapshot(_: web.Request) -> web.Response:
        return web.json_response(services.loras.snapshot())

    @_handler
    async def refresh_loras(request: web.Request) -> web.Response:
        body = await _json_body(request)
        confirmed = _bool(body.get("confirm_manual"))
        if not confirmed:
            raise ManualActionRequiredError(
                "external synchronization requires an explicit manual confirmation"
            )

        async def operation(_: str, __: threading.Event) -> Mapping[str, Any]:
            return await services.loras.refresh_catalog(
                confirm_manual=confirmed,
                force=_bool(body.get("force"), default=True),
            )

        task = await operations.submit("lora_catalog_refresh", operation)
        return web.json_response(task, status=202)

    @_handler
    async def lora_visuals(request: web.Request) -> web.Response:
        if _bool(request.query.get("manifest"), default=False):
            return web.json_response(services.loras.visual_manifest())
        options: dict[str, Any] = dict(request.query)
        options.pop("manifest", None)
        for name, default in (("page", 1), ("page_size", 48)):
            if name in options:
                options[name] = _integer(
                    options[name], default=default, minimum=1, maximum=200
                )
        if "favorite_only" in options:
            options["favorite_only"] = _bool(options["favorite_only"])
        return web.json_response(services.loras.visual_page(**options))

    @_handler
    async def analyze_loras(request: web.Request) -> web.Response:
        body = await _json_body(request)
        confirmed = _bool(body.get("confirm_manual"))
        if not confirmed:
            raise ManualActionRequiredError(
                "external synchronization requires an explicit manual confirmation"
            )
        names = tuple(str(item) for item in body.get("selected_names") or ())

        async def operation(run_id: str, _: threading.Event) -> Mapping[str, Any]:
            await services.loras.refresh_catalog(confirm_manual=True, force=True)
            records = tuple(services.loras._records)
            selected = records if not names else tuple(item for item in records if item.name in names)
            if names and {item.name for item in selected} != set(names):
                raise ValueError("one or more exact LoRA names do not exist")
            details = [await services.loras.catalog.get_detail_v2(item) for item in selected]
            return await services.loras.analyze(
                details,
                llm_callback,
                confirm_manual=True,
                selected_names=names or None,
                run_id=run_id,
                requested_by="web",
            )

        task = await operations.submit(
            "lora_semantic_analysis",
            operation,
            metadata={"selected_names": list(names)},
            lifecycle_managed_by_operation=True,
        )
        return web.json_response(task, status=202)

    @_handler
    async def archive_loras(request: web.Request) -> web.Response:
        body = await _json_body(request)
        confirmed = _bool(body.get("confirm_manual"))
        if not confirmed:
            raise ManualActionRequiredError(
                "external synchronization requires an explicit manual confirmation"
            )
        names = tuple(str(item) for item in body.get("selected_names") or ())

        async def operation(_: str, __: threading.Event) -> Mapping[str, Any]:
            await services.loras.refresh_catalog(confirm_manual=confirmed, force=True)
            return await services.loras.archive(
                llm_callback,
                confirm_manual=confirmed,
                selected_names=names or None,
                skip_when_unchanged=_bool(body.get("skip_when_unchanged"), default=True),
            )

        task = await operations.submit(
            "lora_archive", operation, metadata={"selected_names": list(names)}
        )
        return web.json_response(task, status=202)

    @_handler
    async def download_lora(request: web.Request) -> web.Response:
        body = await _json_body(request)
        confirmed = _bool(body.get("confirm_manual"))
        if not confirmed:
            raise ManualActionRequiredError(
                "external synchronization requires an explicit manual confirmation"
            )
        url = str(body.get("url") or "").strip()
        if not url:
            raise ValueError("url is required")

        async def operation(_: str, __: threading.Event) -> Mapping[str, Any]:
            return await services.loras.download(url, confirm_manual=confirmed)

        task = await operations.submit("lora_download", operation, metadata={"url": url})
        return web.json_response(task, status=202)

    @_handler
    async def danbooru_status(_: web.Request) -> web.Response:
        return web.json_response(services.danbooru.snapshot())

    @_handler
    async def build_danbooru(request: web.Request) -> web.Response:
        body = await _json_body(request)
        confirmed = _bool(body.pop("confirm_manual", False))
        if not confirmed:
            raise ManualActionRequiredError(
                "external synchronization requires an explicit manual confirmation"
            )

        async def operation(
            run_id: str, cancel_event: threading.Event
        ) -> Mapping[str, Any]:
            async def progress(event: dict[str, Any]) -> None:
                await runtime.event(
                    run_id,
                    "progress",
                    str(event.get("message") or "Danbooru index progress"),
                    event_code=str(event.get("event") or "progress"),
                    details=event,
                )

            return await services.danbooru.build(
                body,
                confirm_manual=confirmed,
                progress=progress,
                cancel_event=cancel_event,
            )

        task = await operations.submit("danbooru_index_build", operation)
        return web.json_response(task, status=202)

    @_handler
    async def workflows(_: web.Request) -> web.Response:
        return web.json_response({"items": services.workflows.list_workflows()})

    @_handler
    async def profiles(_: web.Request) -> web.Response:
        return web.json_response({"items": services.workflows.list_profiles()})

    @_handler
    async def save_profile(request: web.Request) -> web.Response:
        body = await _json_body(request)
        item = services.workflows.save_profile(
            str(body.get("name") or ""),
            body.get("config") or {},
            overwrite=_bool(body.get("overwrite")),
            activate=_bool(body.get("activate")),
        )
        return web.json_response(item, status=201)

    @_handler
    async def export_profile(request: web.Request) -> web.Response:
        return web.json_response(
            services.workflows.export_profile(request.match_info["name"])
        )

    @_handler
    async def import_profile(request: web.Request) -> web.Response:
        body = await _json_body(request)
        return web.json_response(
            services.workflows.import_profile(
                body.get("profile") or body,
                overwrite=_bool(body.get("overwrite")),
            ),
            status=201,
        )

    @_handler
    async def delete_profile(request: web.Request) -> web.Response:
        return web.json_response(
            services.workflows.delete_profile(request.match_info["name"])
        )

    @_handler
    async def quarantine_snapshot(_: web.Request) -> web.Response:
        return web.json_response(services.models.snapshot())

    @_handler
    async def quarantine_model(request: web.Request) -> web.Response:
        body = await _json_body(request)
        return web.json_response(
            services.models.quarantine(
                str(body.get("kind") or ""),
                str(body.get("exact_name") or ""),
                confirm_name=str(body.get("confirm_name") or ""),
                references=body.get("references") or (),
            ),
            status=201,
        )

    @_handler
    async def restore_model(request: web.Request) -> web.Response:
        body = await _json_body(request)
        return web.json_response(
            services.models.restore(
                request.match_info["entry_id"],
                confirm_name=str(body.get("confirm_name") or ""),
            )
        )

    app.router.add_get("/api/v6/capabilities", capabilities)
    app.router.add_get("/api/v6/studio", studio_snapshot)
    app.router.add_get("/api/v6/prompt-assets", prompt_assets)
    app.router.add_get("/api/v6/prompt-assets/facets", prompt_facets)
    app.router.add_post("/api/v6/prompt-assets/import", import_prompt_assets)
    app.router.add_post("/api/v6/prompt-lab/candidates", prompt_candidates)
    app.router.add_post(
        "/api/v6/prompt-lab/{batch_id}/confirm", confirm_prompt_candidate
    )
    app.router.add_get("/api/v6/loras", lora_snapshot)
    app.router.add_post("/api/v6/loras/refresh", refresh_loras)
    app.router.add_get("/api/v6/loras/visuals", lora_visuals)
    app.router.add_post("/api/v6/loras/analyze", analyze_loras)
    app.router.add_post("/api/v6/loras/archive", archive_loras)
    app.router.add_post("/api/v6/loras/download", download_lora)
    app.router.add_get("/api/v6/danbooru", danbooru_status)
    app.router.add_post("/api/v6/danbooru/build", build_danbooru)
    app.router.add_get("/api/v6/workflows", workflows)
    app.router.add_get("/api/v6/config-profiles", profiles)
    app.router.add_post("/api/v6/config-profiles", save_profile)
    app.router.add_get("/api/v6/config-profiles/{name}/export", export_profile)
    app.router.add_post("/api/v6/config-profiles/import", import_profile)
    app.router.add_delete("/api/v6/config-profiles/{name}", delete_profile)
    app.router.add_get("/api/v6/quarantine", quarantine_snapshot)
    app.router.add_post("/api/v6/quarantine", quarantine_model)
    app.router.add_post("/api/v6/quarantine/{entry_id}/restore", restore_model)
    return operations
