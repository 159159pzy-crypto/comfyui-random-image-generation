from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import threading
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aiohttp import web

from anima_natural.assets import AssetError
from anima_natural.engine import NaturalEngine, NaturalEngineError
from anima_natural.jobs import NaturalJobManager
from anima_natural.providers import ProviderRegistryError, SecretStore
from anima_natural.workspace_data import NaturalDataError
from anima_studio import GenerationIntent, PromptPlanStore

from .catalog import SECTIONS, CatalogError, PromptCatalog
from .comfy import ComfyClient, ComfyError
from .custom_prompts import CustomPromptStore
from .favorites import FavoritesService, favorite_key
from .history import HistoryStore
from .migrations import (
    import_legacy_task_events,
    prepare_v6_backup,
    prepare_v7_backup,
    prepare_v7_migration,
)
from .runner import BatchConflict, BatchManager, validate_seeds
from .style_presets import StylePresetStore
from .task_runtime import StudioTaskRuntime, publish_recovered_task_events
from .v6_routes import (
    STUDIO_OPERATIONS_KEY,
    close_studio_services,
    create_studio_services,
    setup_v6_routes,
)
from .v7_events import StudioEventBus
from .v7_queue import V7GenerationQueue, V7StudioQueueAdapter
from .v7_routes import setup_v7_routes, v7_deprecation_middleware
from .v7_store import V7Store
from .v7_studio_routes import setup_v7_studio_routes
from .workflow import (
    DEFAULT_SETTINGS,
    WorkflowError,
    WorkflowTemplates,
    validate_settings,
)

APP_DIR = Path(__file__).resolve().parents[1]

logger = logging.getLogger(__name__)

# aiohttp 4 将移除字符串 app key;统一使用 AppKey(消除弃用告警)。
COMFY_KEY: web.AppKey[Any] = web.AppKey("comfy", object)
HISTORY_KEY: web.AppKey[Any] = web.AppKey("history", object)
MANAGER_KEY: web.AppKey[Any] = web.AppKey("manager", object)
CATALOG_KEY: web.AppKey[Any] = web.AppKey("catalog", object)
CUSTOM_PROMPTS_KEY: web.AppKey[Any] = web.AppKey("custom_prompts", object)
FAVORITES_KEY: web.AppKey[Any] = web.AppKey("favorites", object)
STYLE_PRESETS_KEY: web.AppKey[Any] = web.AppKey("style_presets", object)
NATURAL_ENGINE_KEY: web.AppKey[Any] = web.AppKey("natural_engine", object)
NATURAL_MANAGER_KEY: web.AppKey[Any] = web.AppKey("natural_manager", object)
EXECUTION_LOCK_KEY: web.AppKey[Any] = web.AppKey("execution_lock", object)
TASK_RUNTIME_KEY: web.AppKey[Any] = web.AppKey("task_runtime", object)

LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}


def _v7_random_hires_settings(
    repair: dict[str, Any], defaults: dict[str, Any]
) -> dict[str, Any]:
    configured_percent = int(repair.get("upscale_percent") or 0)
    return {
        "enabled": bool(repair.get("hires_enabled", False)),
        "model_name": str(repair.get("upscale_model") or defaults["model_name"]),
        "percent": configured_percent or int(defaults["percent"]),
    }


def _hostname(value: str) -> str:
    """从 Host 头(host[:port])或 Origin(scheme://host[:port])提取主机名。"""
    try:
        target = value if "://" in value else f"//{value}"
        return (urlsplit(target).hostname or "").lower()
    except ValueError:
        return ""


@web.middleware
async def local_only_middleware(request: web.Request, handler: Any) -> web.StreamResponse:
    # 只信任本机来源:防御恶意网页发起的 CSRF 与 DNS 重绑定。
    if _hostname(request.host or "") not in LOCAL_HOSTNAMES:
        return web.json_response({"error": "仅允许本机访问"}, status=403)
    origin = request.headers.get("Origin")
    if origin is not None and (origin == "null" or _hostname(origin) not in LOCAL_HOSTNAMES):
        return web.json_response({"error": "仅允许本机来源"}, status=403)
    return await handler(request)


@web.middleware
async def error_middleware(request: web.Request, handler: Any) -> web.StreamResponse:
    try:
        response = await handler(request)
    except WorkflowError as error:
        return web.json_response({"error": str(error)}, status=400)
    except CatalogError as error:
        return web.json_response({"error": str(error)}, status=400)
    except BatchConflict as error:
        return web.json_response({"error": str(error)}, status=409)
    except NaturalEngineError as error:
        return web.json_response(
            {"error": str(error), "code": error.code, "details": error.details},
            status=error.status,
        )
    except (ProviderRegistryError, AssetError, NaturalDataError) as error:
        return web.json_response({"error": str(error)}, status=400)
    except KeyError:
        return web.json_response({"error": "记录不存在"}, status=404)
    except ComfyError as error:
        return web.json_response({"error": str(error)}, status=503)
    except json.JSONDecodeError:
        return web.json_response({"error": "请求 JSON 无效"}, status=400)
    if request.path == "/" or request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


async def _json_body(request: web.Request) -> dict[str, Any]:
    try:
        value = await request.json()
    except Exception as error:
        raise WorkflowError("请求 JSON 无效") from error
    if not isinstance(value, dict):
        raise WorkflowError("请求内容必须是对象")
    return value


def _query_bool(request: web.Request, name: str, default: bool = False) -> bool:
    raw = request.query.get(name)
    if raw is None:
        return default
    if raw.lower() in {"1", "true", "yes"}:
        return True
    if raw.lower() in {"0", "false", "no"}:
        return False
    raise WorkflowError(f"{name} 必须是布尔值")


def create_app(
    *,
    app_dir: str | Path = APP_DIR,
    comfy: Any | None = None,
    history_path: str | Path | None = None,
    custom_prompts_path: str | Path | None = None,
    style_presets_path: str | Path | None = None,
    anima_tools_dir: str | Path | None = None,
    natural_secret_store: SecretStore | None = None,
    natural_data_dir: str | Path | None = None,
    studio_path: str | Path | None = None,
) -> web.Application:
    root = Path(app_dir)
    prepare_v6_backup(root)
    v7_backup = prepare_v7_backup(root)
    client = comfy or ComfyClient()
    history = HistoryStore(history_path or root / "data" / "history.sqlite3")
    catalog = PromptCatalog(root, anima_tools_dir)
    custom_prompts = CustomPromptStore(
        custom_prompts_path or root / "data" / "custom_prompts.json", catalog
    )
    favorites = FavoritesService(client, catalog)
    style_presets = StylePresetStore(style_presets_path or root / "data" / "style_presets.json")
    templates = WorkflowTemplates.load(root / "templates")
    execution_lock = asyncio.Lock()
    studio_database = Path(studio_path or root / "data" / "studio.sqlite3")
    task_runtime = StudioTaskRuntime(studio_database)
    prompt_plans = PromptPlanStore(studio_database)
    import_legacy_task_events(root, task_runtime.store)
    v7_store = V7Store(studio_database)
    prepare_v7_migration(root, v7_store, backup_report=v7_backup)
    v7_events = StudioEventBus(v7_store)
    manager = BatchManager(
        templates,
        history,
        client,
        catalog,
        execution_lock=execution_lock,
        task_runtime=task_runtime,
    )
    natural_engine = NaturalEngine(
        root,
        natural_data_dir or root / "data" / "natural",
        secret_store=natural_secret_store,
        comfy=client,
    )
    natural_manager = NaturalJobManager(
        natural_engine,
        client,
        history,
        execution_lock,
        task_runtime=task_runtime,
    )
    studio_services = create_studio_services(
        root,
        natural_engine,
        task_runtime,
        comfy_url=str(getattr(client, "base_url", "http://127.0.0.1:8188")),
    )

    startup_warnings = [
        *history.load_warnings,
        *custom_prompts.load_warnings,
        *style_presets.load_warnings,
    ]

    app = web.Application(
        middlewares=[error_middleware, local_only_middleware, v7_deprecation_middleware]
    )
    app[COMFY_KEY] = client
    app[HISTORY_KEY] = history
    app[MANAGER_KEY] = manager
    app[CATALOG_KEY] = catalog
    app[CUSTOM_PROMPTS_KEY] = custom_prompts
    app[FAVORITES_KEY] = favorites
    app[STYLE_PRESETS_KEY] = style_presets
    app[NATURAL_ENGINE_KEY] = natural_engine
    app[NATURAL_MANAGER_KEY] = natural_manager
    app[EXECUTION_LOCK_KEY] = execution_lock
    app[TASK_RUNTIME_KEY] = task_runtime

    async def publish_history_created(record: dict[str, Any]) -> None:
        workspace = str(record.get("source_workspace") or "random")
        if workspace not in {"random", "natural"}:
            workspace = "random"
        await v7_events.publish(
            "history.created",
            record,
            workspace=workspace,
            entity_id=str(record.get("id") or ""),
        )

    history.on_image_added = publish_history_created

    async def favorite_keys(section: str, collection: str = "") -> set[str] | None:
        if not collection:
            return None
        payload = await favorites.get(section)
        collection_ids = {collection}
        if collection != "__all__":
            pending = [collection]
            while pending:
                parent_id = pending.pop()
                children = [
                    group["id"]
                    for group in payload["groups"]
                    if group.get("parentId") == parent_id and group["id"] not in collection_ids
                ]
                collection_ids.update(children)
                pending.extend(children)
        return {
            favorite_key(section, item)
            for item in payload["items"]
            if (collection == "__all__" and item.get("groupIds"))
            or bool(collection_ids.intersection(item.get("groupIds") or []))
        }

    async def config(_: web.Request) -> web.Response:
        return web.json_response(
            {
                "defaults": DEFAULT_SETTINGS,
                "comfy_url": getattr(client, "base_url", "local"),
                "catalog": {
                    "available": catalog.available,
                    "path": str(catalog.tools_dir or ""),
                    "counts": {section: catalog.count(section) for section in SECTIONS},
                },
                "warnings": startup_warnings,
            }
        )

    async def _pool_response(
        section: str, params: dict[str, Any], selection: dict[str, Any] | None
    ) -> web.Response:
        """GET /api/pools 与 POST /api/pools/query 共用的参数归一化与搜索。"""
        try:
            page = int(params.get("page") or 1)
            limit = int(params.get("limit") or 48)
        except (TypeError, ValueError) as error:
            raise WorkflowError("分页参数无效") from error
        collection = str(params.get("collection") or "")
        sort = str(params.get("sort") or "")
        keys = await favorite_keys(section, collection or ("__all__" if sort == "favorite-first" else ""))
        result = catalog.search(
            section,
            query=str(params.get("q") or ""),
            categories=[str(value) for value in params.get("categories") or []],
            traits=[str(value) for value in params.get("traits") or []],
            gender=str(params.get("gender") or ""),
            hair=str(params.get("hair") or ""),
            eye=str(params.get("eye") or ""),
            series=str(params.get("series") or ""),
            custom_group=str(params.get("custom_group") or ""),
            favorite_keys=keys,
            favorites_only=bool(collection),
            sort=sort,
            page=page,
            limit=limit,
            selection=selection,
        )
        return web.json_response(result)

    async def pool(request: web.Request) -> web.Response:
        params: dict[str, Any] = {
            key: request.query.get(key, "")
            for key in ("q", "gender", "hair", "eye", "series", "custom_group", "collection", "sort")
        }
        params["page"] = request.query.get("page", "1")
        params["limit"] = request.query.get("limit", "48")
        params["categories"] = request.query.getall("category", [])
        params["traits"] = request.query.getall("trait", [])
        return await _pool_response(request.match_info["section"], params, None)

    async def pool_query(request: web.Request) -> web.Response:
        body = await _json_body(request)
        params = dict(body)
        params["categories"] = body.get("categories") if isinstance(body.get("categories"), list) else []
        params["traits"] = body.get("traits") if isinstance(body.get("traits"), list) else []
        selection = body.get("selection") if isinstance(body.get("selection"), dict) else None
        return await _pool_response(request.match_info["section"], params, selection)

    async def get_favorites(request: web.Request) -> web.Response:
        return web.json_response(await favorites.get(request.match_info["section"]))

    async def update_favorite(request: web.Request) -> web.Response:
        return web.json_response(
            await favorites.update_item(request.match_info["section"], await _json_body(request))
        )

    async def create_favorite_group(request: web.Request) -> web.Response:
        return web.json_response(
            await favorites.create_group(request.match_info["section"], await _json_body(request)),
            status=201,
        )

    async def update_favorite_group(request: web.Request) -> web.Response:
        return web.json_response(
            await favorites.update_group(
                request.match_info["section"], request.match_info["group_id"], await _json_body(request)
            )
        )

    async def import_favorite_child_group(request: web.Request) -> web.Response:
        section = request.match_info["section"]
        body = await _json_body(request)
        custom_group_id = str(body.get("customGroupId") or "").strip()
        if not custom_group_id:
            raise WorkflowError("customGroupId 不能为空")
        source_group = next(
            (
                group
                for group in custom_prompts.list_groups(section)["groups"]
                if group["id"] == custom_group_id
            ),
            None,
        )
        if source_group is None:
            raise KeyError(custom_group_id)
        return web.json_response(
            await favorites.import_custom_group(
                section,
                request.match_info["parent_id"],
                source_group,
                custom_prompts.list(section, custom_group_id),
            ),
            status=201,
        )

    async def delete_favorite_group(request: web.Request) -> web.Response:
        return web.json_response(
            await favorites.delete_group(
                request.match_info["section"],
                request.match_info["group_id"],
                _query_bool(request, "deleteItems"),
            )
        )

    async def list_custom_prompts(request: web.Request) -> web.Response:
        section = request.query.get("section") or None
        return web.json_response({"items": custom_prompts.list(section)})

    async def create_custom_prompt(request: web.Request) -> web.Response:
        return web.json_response(await custom_prompts.create(await _json_body(request)), status=201)

    async def update_custom_prompt(request: web.Request) -> web.Response:
        item = await custom_prompts.update(request.match_info["item_id"], await _json_body(request))
        try:
            await favorites.sync_custom(item["section"], item)
        except ComfyError as error:
            logger.warning("同步收藏昵称失败(条目 %s): %s", item.get("id"), error)
        return web.json_response(item)

    async def delete_custom_prompt(request: web.Request) -> web.Response:
        if not await custom_prompts.delete(request.match_info["item_id"]):
            raise KeyError(request.match_info["item_id"])
        return web.json_response({"deleted": True})

    async def list_custom_groups(request: web.Request) -> web.Response:
        return web.json_response(custom_prompts.list_groups(request.match_info["section"]))

    async def create_custom_group(request: web.Request) -> web.Response:
        return web.json_response(
            await custom_prompts.create_group(request.match_info["section"], await _json_body(request)),
            status=201,
        )

    async def update_custom_group(request: web.Request) -> web.Response:
        return web.json_response(
            await custom_prompts.update_group(
                request.match_info["section"], request.match_info["group_id"], await _json_body(request)
            )
        )

    async def delete_custom_group(request: web.Request) -> web.Response:
        return web.json_response(
            await custom_prompts.delete_group(
                request.match_info["section"],
                request.match_info["group_id"],
                _query_bool(request, "deleteItems"),
            )
        )

    async def custom_template(request: web.Request) -> web.Response:
        filename, content_type, body = custom_prompts.template(
            request.match_info["section"], request.match_info["format"]
        )
        return web.Response(
            body=body,
            content_type=content_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    async def preview_custom_import(request: web.Request) -> web.Response:
        body = await _json_body(request)
        return web.json_response(
            custom_prompts.preview_import(
                str(body.get("format") or ""),
                str(body.get("content") or ""),
                str(body.get("section") or ""),
            )
        )

    async def commit_custom_import(request: web.Request) -> web.Response:
        body = await _json_body(request)
        return web.json_response(
            await custom_prompts.commit_import(
                body.get("rows"),
                str(body.get("section") or ""),
                body.get("targetGroupIds"),
            )
        )

    async def status(_: web.Request) -> web.Response:
        try:
            stats = await client.status()
            system = stats.get("system") or {}
            devices = stats.get("devices") or []
            return web.json_response(
                {
                    "online": True,
                    "version": system.get("comfyui_version", ""),
                    "device": devices[0].get("name", "") if devices else "",
                }
            )
        except ComfyError as error:
            return web.json_response({"online": False, "error": str(error)})

    async def loras(_: web.Request) -> web.Response:
        inventory = await client.lora_inventory()
        return web.json_response(inventory)

    async def resources(_: web.Request) -> web.Response:
        return web.json_response(await client.resource_inventory())

    async def list_style_presets(_: web.Request) -> web.Response:
        return web.json_response(style_presets.list())

    async def create_style_preset(request: web.Request) -> web.Response:
        return web.json_response(await style_presets.create(await _json_body(request)), status=201)

    async def update_style_preset(request: web.Request) -> web.Response:
        return web.json_response(
            await style_presets.update(request.match_info["preset_id"], await _json_body(request))
        )

    async def delete_style_preset(request: web.Request) -> web.Response:
        if not await style_presets.delete(request.match_info["preset_id"]):
            raise KeyError(request.match_info["preset_id"])
        return web.json_response({"deleted": True})

    async def start_batch(request: web.Request) -> web.Response:
        await client.status()
        body = await _json_body(request)
        seeds = body.pop("seeds", None)  # 复现历史图片时携带固定种子,不属于 settings
        fixed_seeds = validate_seeds(seeds)
        normalized, fixed_seeds = await manager._validate_request(body, fixed_seeds)
        intent = GenerationIntent.from_legacy_random(
            {
                **normalized,
                **(
                    {
                        "sample_seed": fixed_seeds["sample_seed"],
                        "prompt_seed": fixed_seeds["prompt_seed"],
                    }
                    if fixed_seeds
                    else {}
                ),
            }
        ).to_dict()
        task = await generation_queue.submit(intent)
        attempts = 500 if int(task.get("position") or 0) == 1 else 1
        for _ in range(attempts):
            if manager.state and manager.state.get("id") == task["id"]:
                return web.json_response(manager.snapshot(), status=201)
            current = await task_runtime.get(task["id"])
            if current["status"] not in {"queued", "running"}:
                break
            await asyncio.sleep(0.01)
        return web.json_response(task, status=201)

    async def current_batch(_: web.Request) -> web.Response:
        return web.json_response({"batch": manager.snapshot(), "queue": manager.queue_snapshot()})

    async def stop_batch(request: web.Request) -> web.Response:
        job_id = request.match_info["batch_id"]
        task = await task_runtime.get(job_id)
        state = await v7_cancel_job(job_id, task)
        if state is None:
            raise KeyError(job_id)
        return web.json_response({"batch": state, "queue": manager.queue_snapshot()})

    async def remove_queued_batch(request: web.Request) -> web.Response:
        queue_id = request.match_info["queue_id"]
        pending = await generation_queue.cancel_pending(queue_id)
        if pending is None and not await manager.cancel_queued(queue_id):
            raise KeyError(queue_id)
        return web.json_response({"queue": manager.queue_snapshot()})

    async def batch_preview(_: web.Request) -> web.Response:
        preview = manager.preview
        if preview is None:
            return web.Response(status=204)
        _seq, content_type, body = preview
        return web.Response(body=body, content_type=content_type, headers={"Cache-Control": "no-store"})

    async def v7_job_preview(request: web.Request) -> web.Response:
        job_id = request.match_info["job_id"]
        if not manager.state or manager.state.get("id") != job_id:
            raise KeyError(job_id)
        preview = manager.preview
        if preview is None:
            return web.Response(status=204)
        _seq, content_type, body = preview
        return web.Response(
            body=body,
            content_type=content_type,
            headers={"Cache-Control": "no-store"},
        )

    async def list_history(request: web.Request) -> web.Response:
        try:
            page = int(request.query.get("page", "1"))
            limit = int(request.query.get("limit", "24"))
        except ValueError as error:
            raise WorkflowError("分页参数无效") from error
        return web.json_response(await history.list_images(page, limit))

    async def delete_history(request: web.Request) -> web.Response:
        image_id = int(request.match_info["image_id"])
        if not await history.delete_image(image_id):
            raise KeyError(image_id)
        return web.json_response({"deleted": True})

    async def image(request: web.Request) -> web.Response:
        record = await history.get_image(int(request.match_info["image_id"]))
        body, content_type = await client.image_bytes(record)
        return web.Response(body=body, content_type=content_type.split(";", 1)[0])

    async def natural_providers(_: web.Request) -> web.Response:
        return web.json_response(natural_engine.registry.snapshot())

    async def create_natural_provider(request: web.Request) -> web.Response:
        return web.json_response(
            natural_engine.registry.upsert(await _json_body(request)),
            status=201,
        )

    async def update_natural_provider(request: web.Request) -> web.Response:
        return web.json_response(
            natural_engine.registry.upsert(
                await _json_body(request), request.match_info["provider_id"]
            )
        )

    async def delete_natural_provider(request: web.Request) -> web.Response:
        natural_engine.registry.delete(request.match_info["provider_id"])
        return web.json_response({"deleted": True})

    async def update_natural_bindings(request: web.Request) -> web.Response:
        bindings = natural_engine.registry.set_bindings(await _json_body(request))
        natural_engine._refresh_services()
        return web.json_response({"bindings": bindings})

    async def natural_provider_test(request: web.Request) -> web.Response:
        return web.json_response(
            await natural_engine.provider_client.test(request.match_info["provider_id"])
        )

    async def natural_provider_models(request: web.Request) -> web.Response:
        models = await natural_engine.provider_client.list_models(request.match_info["provider_id"])
        return web.json_response({"models": models})

    async def natural_settings(_: web.Request) -> web.Response:
        return web.json_response(natural_engine.settings_snapshot())

    async def update_natural_settings(request: web.Request) -> web.Response:
        return web.json_response(natural_engine.update_settings(await _json_body(request)))

    async def natural_capabilities(_: web.Request) -> web.Response:
        return web.json_response(await natural_engine.capabilities(client))

    def prepare_natural_payload(body: dict[str, Any]) -> dict[str, Any]:
        pool_settings = body.pop("pool_settings", None)
        if not isinstance(pool_settings, dict):
            return body
        normalized = validate_settings(pool_settings)
        catalog.validate_settings(normalized)
        resolved = catalog.resolve_prompt(normalized, int(body.get("pool_seed") or 0))
        locked = body.get("locked_tags") or []
        if isinstance(locked, str):
            locked = [locked]
        if not isinstance(locked, list):
            raise WorkflowError("locked_tags 必须是字符串数组")
        if resolved["composer_prompt"]:
            locked.append(resolved["composer_prompt"])
        body["locked_tags"] = locked
        body["locked_pool_selection"] = resolved["selected"]
        return body

    async def natural_plan(request: web.Request) -> web.Response:
        return web.json_response(
            await natural_engine.plan(prepare_natural_payload(await _json_body(request))),
            status=201,
        )

    async def natural_upload(request: web.Request) -> web.Response:
        maximum = natural_engine.assets.max_bytes
        if request.content_type.startswith("multipart/"):
            reader = await request.multipart()
            data = bytearray()
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
                    if len(data) > maximum:
                        raise AssetError(f"图片超过 {maximum // (1024 * 1024)}MB 上限")
                break
            raw = bytes(data)
        else:
            raw = await request.content.readexactly(request.content_length) if request.content_length else await request.read()
        return web.json_response(natural_engine.assets.add(raw).public(), status=201)

    async def natural_jobs(request: web.Request) -> web.Response:
        try:
            limit = int(request.query.get("limit", "50"))
        except ValueError as error:
            raise WorkflowError("limit 参数无效") from error
        return web.json_response({"items": natural_manager.list(min(100, max(1, limit)))})

    async def create_natural_job(request: web.Request) -> web.Response:
        body = prepare_natural_payload(await _json_body(request))
        intent = GenerationIntent.from_legacy_natural(
            {
                **body,
                "positive_prompt": str(
                    body.get("positive_prompt")
                    or body.get("text")
                    or body.get("description")
                    or ""
                ),
            }
        ).to_dict()
        intent.update(
            {
                "use_llm": bool(body.get("use_llm", True)),
                "preview_only": bool(body.get("preview_only", False)),
            }
        )
        task = await generation_queue.submit(intent)
        attempts = 500 if int(task.get("position") or 0) == 1 else 1
        for _ in range(attempts):
            current = await task_runtime.get(task["id"])
            if task["id"] in natural_manager.jobs:
                snapshot = natural_manager.get(task["id"])
                if task["id"] in natural_manager.tasks:
                    return web.json_response(snapshot, status=201)
                if (
                    snapshot["state"] in natural_manager.TERMINAL_STATES
                    and current["status"] in V7GenerationQueue.TERMINAL
                ):
                    return web.json_response(snapshot, status=201)
            elif current["status"] not in {"queued", "running"}:
                break
            await asyncio.sleep(0.01)
        if task["id"] in natural_manager.jobs:
            return web.json_response(natural_manager.get(task["id"]), status=201)
        return web.json_response(task, status=201)

    async def natural_job(request: web.Request) -> web.Response:
        return web.json_response(natural_manager.get(request.match_info["job_id"]))

    async def natural_job_timeline(request: web.Request) -> web.Response:
        return web.json_response(
            {"items": natural_manager.timeline(request.match_info["job_id"])}
        )

    async def cancel_natural_job(request: web.Request) -> web.Response:
        if request.can_read_body:
            body = await _json_body(request)
            action = str(body.get("action") or "cancel")
            if action != "cancel":
                raise WorkflowError("自然语言任务只支持 cancel 操作")
        job_id = request.match_info["job_id"]
        pending = await generation_queue.cancel_pending(job_id)
        if pending is not None:
            return web.json_response(pending)
        return web.json_response(await natural_manager.cancel(job_id))

    async def natural_job_events(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream; charset=utf-8",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
        await response.prepare(request)
        try:
            async for event in natural_manager.events(request.match_info["job_id"]):
                await response.write(
                    f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode()
                )
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        return response

    async def natural_danbooru(request: web.Request) -> web.Response:
        return web.json_response(
            natural_engine.danbooru_search(
                request.query.get("q", ""), request.query.get("category", "")
            )
        )

    async def natural_danbooru_status(_: web.Request) -> web.Response:
        return web.json_response(natural_engine.danbooru.status())

    async def natural_data_list(request: web.Request) -> web.Response:
        return web.json_response(
            {"items": natural_engine.workspace_data.list(request.match_info["kind"])}
        )

    async def natural_data_create(request: web.Request) -> web.Response:
        return web.json_response(
            natural_engine.workspace_data.upsert(
                request.match_info["kind"], await _json_body(request)
            ),
            status=201,
        )

    async def natural_data_update(request: web.Request) -> web.Response:
        return web.json_response(
            natural_engine.workspace_data.upsert(
                request.match_info["kind"],
                await _json_body(request),
                request.match_info["item_id"],
            )
        )

    async def natural_data_delete(request: web.Request) -> web.Response:
        natural_engine.workspace_data.delete(
            request.match_info["kind"], request.match_info["item_id"]
        )
        return web.json_response({"deleted": True})

    async def natural_prompt_lab_confirm(request: web.Request) -> web.Response:
        return web.json_response(
            natural_engine.workspace_data.confirm_prompt_lab(request.match_info["item_id"])
        )

    async def natural_logs(request: web.Request) -> web.Response:
        try:
            limit = int(request.query.get("limit", "100"))
        except ValueError as exc:
            raise WorkflowError("limit 参数无效") from exc
        return web.json_response(
            {"items": natural_engine.workspace_data.redacted_logs(limit)}
        )

    async def v6_jobs(request: web.Request) -> web.Response:
        try:
            limit = min(200, max(1, int(request.query.get("limit", "50"))))
        except ValueError as exc:
            raise WorkflowError("limit 参数无效") from exc
        statuses = [
            value.strip()
            for value in request.query.get("status", "").split(",")
            if value.strip()
        ]
        return web.json_response(
            {
                "items": await task_runtime.list(
                    limit=limit,
                    statuses=statuses or None,
                    task_type=request.query.get("type", "").strip(),
                )
            }
        )

    async def v6_job(request: web.Request) -> web.Response:
        return web.json_response(await task_runtime.get(request.match_info["job_id"]))

    async def v6_job_events(request: web.Request) -> web.Response:
        try:
            after = max(0, int(request.query.get("after", "0")))
            limit = min(2000, max(1, int(request.query.get("limit", "500"))))
        except ValueError as exc:
            raise WorkflowError("事件游标参数无效") from exc
        return web.json_response(
            await task_runtime.events(
                run_id=request.match_info["job_id"],
                after_seq=after,
                limit=limit,
            )
        )

    async def v6_cancel_job(request: web.Request) -> web.Response:
        job_id = request.match_info["job_id"]
        pending = await generation_queue.cancel_pending(job_id)
        if pending is not None:
            return web.json_response(pending)
        if job_id in natural_manager.jobs:
            return web.json_response(await natural_manager.cancel(job_id))
        if manager.state and manager.state.get("id") == job_id:
            return web.json_response(
                await manager.request_stop(job_id, clear_queue=False)
            )
        if await manager.cancel_queued(job_id):
            return web.json_response(await task_runtime.get(job_id))
        operation = await request.app[STUDIO_OPERATIONS_KEY].cancel(job_id)
        if operation is not None:
            return web.json_response(operation)
        task = await task_runtime.get(job_id)
        if task["status"] in {
            "succeeded",
            "partial",
            "failed",
            "cancelled",
            "timed_out",
            "interrupted",
        }:
            return web.json_response(task)
        raise WorkflowError("该任务类型暂不支持运行中取消")

    async def v6_logs(request: web.Request) -> web.Response:
        try:
            limit = min(1000, max(1, int(request.query.get("limit", "200"))))
        except ValueError as exc:
            raise WorkflowError("limit 参数无效") from exc
        return web.json_response({"items": await task_runtime.logs(limit=limit)})

    async def index(_: web.Request) -> web.FileResponse:
        return web.FileResponse(root / "static" / "index.html")

    async def favicon(_: web.Request) -> web.Response:
        return web.Response(status=204)

    app.router.add_get("/api/config", config)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/loras", loras)
    app.router.add_get("/api/resources", resources)
    app.router.add_get("/api/style-presets", list_style_presets)
    app.router.add_post("/api/style-presets", create_style_preset)
    app.router.add_put("/api/style-presets/{preset_id}", update_style_preset)
    app.router.add_delete("/api/style-presets/{preset_id}", delete_style_preset)
    app.router.add_get("/api/favorites/{section}", get_favorites)
    app.router.add_put("/api/favorites/{section}/item", update_favorite)
    app.router.add_post("/api/favorites/{section}/groups", create_favorite_group)
    app.router.add_put("/api/favorites/{section}/groups/{group_id}", update_favorite_group)
    app.router.add_post(
        "/api/favorites/{section}/groups/{parent_id}/children/import",
        import_favorite_child_group,
    )
    app.router.add_delete("/api/favorites/{section}/groups/{group_id}", delete_favorite_group)
    app.router.add_get("/api/pools/{section}", pool)
    app.router.add_post("/api/pools/{section}/query", pool_query)
    app.router.add_get("/api/custom-prompts", list_custom_prompts)
    app.router.add_post("/api/custom-prompts", create_custom_prompt)
    app.router.add_put("/api/custom-prompts/{item_id}", update_custom_prompt)
    app.router.add_delete("/api/custom-prompts/{item_id}", delete_custom_prompt)
    app.router.add_get("/api/custom-prompts/templates/{section}/{format}", custom_template)
    app.router.add_post("/api/custom-prompts/import/preview", preview_custom_import)
    app.router.add_post("/api/custom-prompts/import", commit_custom_import)
    app.router.add_get("/api/custom-groups/{section}", list_custom_groups)
    app.router.add_post("/api/custom-groups/{section}", create_custom_group)
    app.router.add_put("/api/custom-groups/{section}/{group_id}", update_custom_group)
    app.router.add_delete("/api/custom-groups/{section}/{group_id}", delete_custom_group)
    app.router.add_post("/api/batches", start_batch)
    app.router.add_get("/api/batches/current", current_batch)
    app.router.add_get("/api/batches/current/preview", batch_preview)
    app.router.add_post("/api/batches/{batch_id}/stop", stop_batch)
    app.router.add_delete("/api/batches/queue/{queue_id}", remove_queued_batch)
    app.router.add_get("/api/history", list_history)
    app.router.add_delete(r"/api/history/{image_id:\d+}", delete_history)
    app.router.add_get(r"/api/images/{image_id:\d+}", image)
    app.router.add_get("/api/v7/config", config)
    app.router.add_get("/api/v7/status", status)
    app.router.add_get("/api/v7/favorites/{section}", get_favorites)
    app.router.add_put("/api/v7/favorites/{section}/item", update_favorite)
    app.router.add_post("/api/v7/favorites/{section}/groups", create_favorite_group)
    app.router.add_put(
        "/api/v7/favorites/{section}/groups/{group_id}", update_favorite_group
    )
    app.router.add_post(
        "/api/v7/favorites/{section}/groups/{group_id}/children/import",
        import_favorite_child_group,
    )
    app.router.add_delete(
        "/api/v7/favorites/{section}/groups/{group_id}", delete_favorite_group
    )
    app.router.add_get("/api/v7/pools/{section}", pool)
    app.router.add_post("/api/v7/pools/{section}/query", pool_query)
    app.router.add_get("/api/v7/custom-prompts", list_custom_prompts)
    app.router.add_post("/api/v7/custom-prompts", create_custom_prompt)
    app.router.add_put("/api/v7/custom-prompts/{item_id}", update_custom_prompt)
    app.router.add_delete("/api/v7/custom-prompts/{item_id}", delete_custom_prompt)
    app.router.add_get(
        "/api/v7/custom-prompts/templates/{section}/{format}", custom_template
    )
    app.router.add_post(
        "/api/v7/custom-prompts/import/preview", preview_custom_import
    )
    app.router.add_post("/api/v7/custom-prompts/import", commit_custom_import)
    app.router.add_get("/api/v7/custom-groups/{section}", list_custom_groups)
    app.router.add_post("/api/v7/custom-groups/{section}", create_custom_group)
    app.router.add_put(
        "/api/v7/custom-groups/{section}/{group_id}", update_custom_group
    )
    app.router.add_delete(
        "/api/v7/custom-groups/{section}/{group_id}", delete_custom_group
    )
    app.router.add_get(r"/api/v7/images/{image_id:\d+}", image)
    app.router.add_get("/api/natural/providers", natural_providers)
    app.router.add_post("/api/natural/providers", create_natural_provider)
    app.router.add_put("/api/natural/providers/bindings", update_natural_bindings)
    app.router.add_get("/api/natural/providers/{provider_id}/models", natural_provider_models)
    app.router.add_post("/api/natural/providers/{provider_id}/test", natural_provider_test)
    app.router.add_put("/api/natural/providers/{provider_id}", update_natural_provider)
    app.router.add_delete("/api/natural/providers/{provider_id}", delete_natural_provider)
    app.router.add_get("/api/natural/settings", natural_settings)
    app.router.add_put("/api/natural/settings", update_natural_settings)
    app.router.add_get("/api/natural/capabilities", natural_capabilities)
    app.router.add_post("/api/natural/plans", natural_plan)
    app.router.add_post("/api/natural/uploads", natural_upload)
    app.router.add_get("/api/natural/jobs", natural_jobs)
    app.router.add_post("/api/natural/jobs", create_natural_job)
    app.router.add_get("/api/natural/jobs/{job_id}", natural_job)
    app.router.add_get("/api/natural/jobs/{job_id}/timeline", natural_job_timeline)
    app.router.add_post("/api/natural/jobs/{job_id}", cancel_natural_job)
    app.router.add_delete("/api/natural/jobs/{job_id}", cancel_natural_job)
    app.router.add_get("/api/natural/jobs/{job_id}/events", natural_job_events)
    app.router.add_get("/api/natural/danbooru", natural_danbooru)
    app.router.add_get("/api/natural/danbooru/status", natural_danbooru_status)
    app.router.add_get(
        r"/api/natural/data/{kind:lora_profiles|identities|prompt_lab}",
        natural_data_list,
    )
    app.router.add_post(
        r"/api/natural/data/{kind:lora_profiles|identities|prompt_lab}",
        natural_data_create,
    )
    app.router.add_put(
        r"/api/natural/data/{kind:lora_profiles|identities|prompt_lab}/{item_id}",
        natural_data_update,
    )
    app.router.add_delete(
        r"/api/natural/data/{kind:lora_profiles|identities|prompt_lab}/{item_id}",
        natural_data_delete,
    )
    app.router.add_post(
        "/api/natural/prompt-lab/{item_id}/confirm", natural_prompt_lab_confirm
    )
    app.router.add_get("/api/natural/logs", natural_logs)
    app.router.add_post("/api/v6/plans", natural_plan)
    app.router.add_get("/api/v6/jobs", v6_jobs)
    app.router.add_post("/api/v6/jobs", create_natural_job)
    app.router.add_get("/api/v6/jobs/{job_id}", v6_job)
    app.router.add_get("/api/v6/jobs/{job_id}/events", v6_job_events)
    app.router.add_post("/api/v6/jobs/{job_id}/cancel", v6_cancel_job)
    app.router.add_get("/api/v6/logs", v6_logs)
    async def studio_llm(system_prompt: str, user_prompt: str) -> str:
        text, _ = await natural_engine.provider_gateway.complete(
            "director",
            prompt=user_prompt,
            system_prompt=system_prompt,
        )
        return text

    studio_operations = setup_v6_routes(
        app,
        services=studio_services,
        runtime=task_runtime,
        llm_callback=studio_llm,
    )

    async def v7_cancel_job(
        job_id: str, task: dict[str, Any]
    ) -> dict[str, Any] | None:
        pending = await generation_queue.cancel_pending(job_id)
        if pending is not None:
            return pending
        studio = await generation_queue.cancel_studio(job_id)
        if studio is not None:
            return studio
        if job_id in natural_manager.jobs:
            return await natural_manager.cancel(job_id)
        if manager.state and manager.state.get("id") == job_id:
            return await manager.request_stop(job_id, clear_queue=False)
        if await manager.cancel_queued(job_id):
            return await task_runtime.get(job_id)
        return await studio_operations.cancel(job_id)

    async def v7_execute_job(
        job_id: str,
        intent: dict[str, Any],
    ) -> dict[str, Any]:
        workspace = str(intent.get("workspace") or "natural")
        await history.link_intent(
            job_id,
            str(intent.get("id") or ""),
            intent,
            workspace,
        )
        sampling = intent.get("sampling") if isinstance(intent.get("sampling"), dict) else {}
        repair = intent.get("repair") if isinstance(intent.get("repair"), dict) else {}
        loras = [
            {
                "filename": str(item.get("filename") or ""),
                "enabled": bool(item.get("enabled", True)),
                "strength": float(item.get("strength", 1.0)),
                "role": str(item.get("role") or "style"),
                "order": int(item.get("order", index)),
            }
            for index, item in enumerate(intent.get("loras") or [])
            if isinstance(item, dict)
        ]
        if workspace == "random":
            settings = copy.deepcopy(DEFAULT_SETTINGS)
            random_options = (
                intent.get("random_options")
                if isinstance(intent.get("random_options"), dict)
                else {}
            )
            settings.update(
                {
                    key: random_options[key]
                    for key in ("female_count", "male_count", "character_detail", "quality_prompt")
                    if key in random_options
                }
            )
            settings.update(
                {
                    "count": int(sampling.get("count") or 1),
                    "model_name": str(intent.get("model") or settings["model_name"]),
                    "loras": loras,
                    "manual_artist": ", ".join(intent.get("artist_tags") or []),
                    "quality_prompt": str(random_options.get("quality_prompt") or ""),
                    "extra_prompt": ", ".join(
                        [
                            str(intent.get("positive_prompt") or ""),
                            *(str(item) for item in intent.get("locked_tags") or []),
                        ]
                    ).strip(", "),
                    "negative_prompt": str(intent.get("negative_prompt") or ""),
                    "width": int(sampling.get("width") or settings["width"]),
                    "height": int(sampling.get("height") or settings["height"]),
                    "steps": int(sampling.get("steps") or settings["steps"]),
                    "cfg": float(sampling.get("cfg") or settings["cfg"]),
                    "hires": _v7_random_hires_settings(repair, settings["hires"]),
                    "detailers": {
                        name: name in set(repair.get("detailers") or [])
                        for name in settings["detailers"]
                    },
                }
            )
            pools = intent.get("random_pools")
            if isinstance(pools, dict):
                for name, selection in pools.items():
                    if name not in settings["pools"] or not isinstance(selection, dict):
                        continue
                    settings["pools"][name] = {
                        **{
                            key: selection[key]
                            for key in ("ids", "excluded_ids")
                            if key in selection
                        },
                        "mode": (
                            "include"
                            if selection.get("mode") == "off"
                            else selection.get("mode", "include")
                        ),
                    }
                    settings[f"random_{name}"] = selection.get("mode") != "off"
                    settings[f"random_{name}_count"] = int(selection.get("count") or 1)
                    settings[f"fixed_{name}"] = str(selection.get("fixed_tags") or "")
            seed = sampling.get("seed")
            prompt_seed = sampling.get("prompt_seed")
            seeds = None
            if seed not in (None, -1, "-1") or prompt_seed not in (None, -1, "-1"):
                if seed in (None, -1, "-1") or prompt_seed in (None, -1, "-1"):
                    raise WorkflowError("固定复现必须同时提供 sample seed 和 prompt seed")
                seeds = {"sample_seed": int(seed), "prompt_seed": int(prompt_seed)}
            result = await manager.run_coordinated(job_id, settings, seeds=seeds)
        else:
            mode = str(intent.get("mode") or "text_to_image")
            mode = "img2img" if mode == "image_to_image" else mode
            controls = [item for item in intent.get("controls") or [] if isinstance(item, dict)]
            payload = {
                "description": str(intent.get("positive_prompt") or ""),
                "text": str(intent.get("positive_prompt") or ""),
                "job_type": mode,
                "locked_tags": [
                    *(str(item) for item in intent.get("artist_tags") or []),
                    *(str(item) for item in intent.get("locked_tags") or []),
                ],
                "negative_prompt": str(intent.get("negative_prompt") or ""),
                "model_name": str(intent.get("model") or ""),
                "pipeline": str(intent.get("pipeline") or ""),
                "loras": loras,
                "count": int(sampling.get("count") or 1),
                "width": int(sampling.get("width") or 832),
                "height": int(sampling.get("height") or 1216),
                "steps": int(sampling.get("steps") or 30),
                "cfg": float(sampling.get("cfg") or 4),
                "seed": int(sampling.get("seed") if sampling.get("seed") is not None else -1),
                "intent_id": str(intent.get("id") or ""),
                "use_llm": bool(intent.get("use_llm", True)),
                "preview_only": bool(intent.get("preview_only", False)),
                "control_modes": [
                    str(item.get("kind") or item.get("type") or "")
                    for item in controls
                    if str(item.get("kind") or item.get("type") or "")
                ],
                "inpaint_mode": str(intent.get("inpaint_mode") or "quick"),
            }
            input_image = intent.get("input_image")
            if isinstance(input_image, dict) and input_image.get("asset_id"):
                payload["asset_id"] = str(input_image["asset_id"])
            elif controls:
                control_image = controls[0].get("image")
                if isinstance(control_image, dict) and control_image.get("asset_id"):
                    payload["asset_id"] = str(control_image["asset_id"])
            mask_image = intent.get("mask_image")
            if isinstance(mask_image, dict) and mask_image.get("asset_id"):
                payload["mask_asset_id"] = str(mask_image["asset_id"])
            frozen_plan = intent.get("_execution_plan")
            result = await natural_manager.run_coordinated(
                job_id,
                payload,
                frozen_plan=frozen_plan if isinstance(frozen_plan, dict) else None,
            )
        return result

    async def v7_execute_random(
        job_id: str, intent: dict[str, Any]
    ) -> dict[str, Any]:
        return await v7_execute_job(job_id, {**intent, "workspace": "random"})

    async def v7_execute_natural(
        job_id: str, intent: dict[str, Any]
    ) -> dict[str, Any]:
        return await v7_execute_job(job_id, {**intent, "workspace": "natural"})

    generation_queue = V7GenerationQueue(
        task_runtime,
        {"random": v7_execute_random, "natural": v7_execute_natural},
        publish=v7_events.publish,
    )

    async def v7_preview_intent(intent: dict[str, Any]) -> dict[str, Any]:
        try:
            artist_snapshot = await favorites.get("artist")
            artists = list(artist_snapshot.get("items") or [])
        except (ComfyError, ValueError, TypeError):
            artists = []
        try:
            lora_snapshot = await client.lora_inventory()
            lora_items = list(lora_snapshot.get("items") or [])
        except (ComfyError, ValueError, TypeError):
            lora_items = []
        preset_snapshot = await asyncio.to_thread(v7_store.list_presets)
        prompt_snapshot = studio_services.prompts.search(page=1, page_size=200)
        prompt_plan_snapshot = await asyncio.to_thread(prompt_plans.list)
        planning_prompt_plans = [
            {
                **dict(item.get("plan") or {}),
                "id": item["id"],
                "name": item["name"],
                "description": item["description"],
            }
            for item in prompt_plan_snapshot.get("items") or []
        ]
        natural_engine.configure_planning_tools(
            artists=artists,
            loras=lora_items,
            presets=list(preset_snapshot.get("items") or []),
            prompt_assets=list(prompt_snapshot.get("items") or []),
            prompt_plans=planning_prompt_plans,
        )
        payload = dict(intent)
        mode = str(payload.get("job_type") or payload.get("mode") or "text_to_image")
        payload["job_type"] = "img2img" if mode == "image_to_image" else mode
        return await natural_engine.plan(prepare_natural_payload(payload))

    v7_studio_operations = setup_v7_studio_routes(
        app,
        services=studio_services,
        engine=natural_engine,
        runtime=task_runtime,
        llm_callback=studio_llm,
        events=v7_events,
        resource_runtime=client,
        operation_manager=V7StudioQueueAdapter(generation_queue),
        prompt_plans=prompt_plans,
    )

    async def v7_submit_job(intent: dict[str, Any]) -> dict[str, Any]:
        return await generation_queue.submit(intent)

    def v7_job_overlay(task: dict[str, Any]) -> dict[str, Any]:
        job_id = str(task.get("run_id") or task.get("id") or "")
        if manager.state and manager.state.get("id") == job_id:
            state = manager.snapshot() or {}
            return {
                "preview_id": state.get("preview_id"),
                "progress": state.get("progress"),
                "prompt_id": state.get("prompt_id"),
                "message": state.get("error") or "",
            }
        if job_id in natural_manager.jobs:
            natural = natural_manager.get(job_id)
            return {
                "state": natural.get("state"),
                "stage": natural.get("stage"),
                "message": natural.get("message"),
                "progress": natural.get("progress"),
                "prompt_id": natural.get("prompt_id"),
            }
        return {}

    def v7_upload_asset(data: bytes) -> dict[str, Any]:
        return natural_engine.assets.add(data).public()

    async def v7_retry_job(
        _: str, task: dict[str, Any]
    ) -> dict[str, Any] | None:
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        intent = metadata.get("intent")
        if isinstance(intent, dict):
            return await generation_queue.submit(intent)
        if str(task.get("task_type") or "") == "random_batch":
            settings = metadata.get("settings")
            if isinstance(settings, dict):
                return await generation_queue.submit(
                    GenerationIntent.from_legacy_random(settings).to_dict()
                )
        if str(task.get("task_type") or "") == "natural_generation":
            job = metadata.get("job")
            payload = job.get("payload") if isinstance(job, dict) else None
            if isinstance(payload, dict):
                return await generation_queue.submit(
                    GenerationIntent.from_legacy_natural(payload).to_dict()
                )
        return None

    setup_v7_routes(
        app,
        store=v7_store,
        events=v7_events,
        history=history,
        comfy=client,
        runtime=task_runtime,
        studio_services=studio_services,
        preview_job=v7_preview_intent,
        submit_job=v7_submit_job,
        cancel_job=v7_cancel_job,
        retry_job=v7_retry_job,
        job_overlay=v7_job_overlay,
        upload_asset=v7_upload_asset,
        max_upload_bytes=natural_engine.assets.max_bytes,
    )
    app.router.add_get("/api/v7/jobs/{job_id}/preview", v7_job_preview)
    app.router.add_get("/", index)
    app.router.add_get("/favicon.ico", favicon)
    app.router.add_static("/static/", root / "static", show_index=False)

    async def publish_recovered(_: web.Application) -> None:
        await publish_recovered_task_events(task_runtime, v7_events)

    async def cleanup(_: web.Application) -> None:
        await generation_queue.close()
        manager.shutting_down = True
        manager.queue.clear()
        manager._stop_monitor()
        if manager.task and not manager.task.done():
            manager.stop_requested = True
            try:
                await asyncio.wait_for(manager.task, timeout=2)
            except (TimeoutError, asyncio.CancelledError):
                manager.task.cancel()
        await natural_manager.close()
        history.on_image_added = None
        await v7_events.close()
        await v7_studio_operations.close()
        await studio_operations.close()
        await close_studio_services(studio_services)
        await natural_engine.close()
        close = getattr(client, "close", None)
        if close:
            result = close()
            if asyncio.iscoroutine(result):
                await result
        await task_runtime.close()
        await asyncio.to_thread(v7_store.close)
        await history.close()

    app.on_startup.append(publish_recovered)
    app.on_cleanup.append(cleanup)
    return app


def _setup_logging(root: Path) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        (root / "data").mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(root / "data" / "webui.log", encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
    )


def main() -> None:
    _setup_logging(APP_DIR)
    parser = argparse.ArgumentParser(description="Anima Random WebUI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8190)
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    parser.add_argument("--anima-tools-dir", default=None)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("WebUI 只允许监听本机地址")
    app = create_app(comfy=ComfyClient(args.comfy_url), anima_tools_dir=args.anima_tools_dir)
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()
    web.run_app(app, host=args.host, port=args.port, print=lambda message: print(message, flush=True))
