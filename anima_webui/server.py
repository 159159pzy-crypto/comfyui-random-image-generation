from __future__ import annotations

import argparse
import asyncio
import copy
import ipaddress
import json
import logging
import random
import threading
import webbrowser
from email.utils import formatdate
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from aiohttp import web
from aiohttp.web_protocol import RequestPayloadError

from .comfy import ComfyClient, ComfyError
from .artwork_deletion import ArtworkDeletionService
from .catalog import CatalogError, PromptCatalog, SECTIONS, resolve_history_selection
from .persistence import StorageReadOnlyError
from .custom_prompts import CustomPromptStore
from .favorites import FavoriteSavePendingError, FavoritesService, FavoritesStore, favorite_key
from .pool_lifecycle import PoolLifecycleError, PoolLifecycleService
from .image_location import ImageLocationError, reveal_image
from .graph_utils import active_lora_pipelines
from .history import ArtworkCollectionConflict, HistoryStore
from .http_security import (
    SQLITE_MAX_INTEGER,
    parse_authority,
    parse_origin,
    strict_json_loads,
)
from .lora_triggers import LoraTriggerOverrideStore
from .lora_library import LoraLibraryService, LoraLibraryStore, register_lora_routes
from .lora_binding import validate_frozen_resources
from .prompt_rules import PromptRuleStore
from .runner import BatchConflict, BatchManager, validate_seeds
from .style_presets import StylePresetStore
from .thumbnails import ThumbnailStore
from .workflow import WorkflowError, WorkflowTemplates, normalize_lora_path, upgrade_pipeline_settings, validate_loras, validate_settings
from .model_profiles import (
    MODEL_PROFILE_IDS,
    model_profile_defaults,
    normalize_model_profile,
    profile_public,
)
from .resource_config import configure_resources
from .resource_classification import technical_resource_metadata


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
LORA_TRIGGER_OVERRIDES_KEY: web.AppKey[Any] = web.AppKey("lora_trigger_overrides", object)
LORA_LIBRARY_KEY: web.AppKey[Any] = web.AppKey("lora_library", object)
PROMPT_RULES_KEY: web.AppKey[Any] = web.AppKey("prompt_rules", object)
THUMBNAILS_KEY: web.AppKey[Any] = web.AppKey("thumbnails", object)
ARTWORK_DELETION_KEY: web.AppKey[Any] = web.AppKey("artwork_deletion", object)
ALLOWED_HOSTNAMES_KEY: web.AppKey[frozenset[str]] = web.AppKey(
    "allowed_hostnames", frozenset
)

LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}
RESOURCE_KINDS = ("models", "loras")


def _hostname(value: str) -> str:
    """从 Host 头(host[:port])或 Origin(scheme://host[:port])提取主机名。"""
    try:
        target = value if "://" in value else f"//{value}"
        return (urlsplit(target).hostname or "").lower()
    except ValueError:
        return ""


def _find_inventory_model_match(requested: Any, candidates: list[str]) -> str | None:
    """Resolve an exact model path or an unambiguous basename in an inventory."""
    normalized = normalize_lora_path(requested)
    identity = normalized.casefold()
    exact = [
        candidate
        for candidate in candidates
        if normalize_lora_path(candidate).casefold() == identity
    ]
    if exact:
        return exact[0] if len(exact) == 1 else None
    # A path-shaped request is an explicit path selection.  Do not silently
    # redirect it to another directory; only legacy basename settings use the
    # basename fallback.
    if "/" in normalized:
        return None
    matches = [
        candidate
        for candidate in candidates
        if normalize_lora_path(candidate).rsplit("/", 1)[-1].casefold()
        == identity
    ]
    return matches[0] if len(matches) == 1 else None


def _resolve_owned_model(
    requested: Any,
    model_names: list[str],
    owners: dict[str, str | None],
    profile: str,
    classifications: dict[str, dict[str, Any]] | None = None,
    *,
    allow_basename: bool = True,
) -> str:
    """Resolve and enforce the owner of the model selected by a request."""
    normalized = normalize_lora_path(requested)
    identity = normalized.casefold()
    exact = [
        candidate
        for candidate in model_names
        if normalize_lora_path(candidate).casefold() == identity
    ]
    if len(exact) > 1:
        raise WorkflowError(f"主模型路径匹配不唯一: {normalized}")
    if exact:
        actual = exact[0]
    elif not allow_basename or "/" in normalized:
        raise WorkflowError(f"主模型不存在于当前 ComfyUI 清单: {normalized}")
    else:
        basename_matches = [
            candidate
            for candidate in model_names
            if normalize_lora_path(candidate).rsplit("/", 1)[-1].casefold()
            == identity
        ]
        if not basename_matches:
            raise WorkflowError(f"主模型不存在于当前 ComfyUI 清单: {normalized}")
        if len(basename_matches) != 1:
            raise WorkflowError(f"主模型 basename 匹配不唯一: {normalized}")
        actual = basename_matches[0]

    assigned = owners.get(actual)
    if assigned != profile:
        item = (classifications or {}).get(actual, {})
        reason = item.get("classification_reason") or f"插件分类为 {item.get('base_model') or assigned or 'Unknown'}"
        raise WorkflowError(
            f"主模型不适用于 {profile} 档案: {actual}；{reason}，请在 LoRA Manager 中检查分类"
        )
    return actual


def _redraw_availability(
    catalog: PromptCatalog,
    settings: dict[str, Any],
    section: str,
    original: list[dict[str, Any]],
) -> tuple[bool, str, bool]:
    """Check real pool rules without guessing across a finite set of seeds."""
    selection = copy.deepcopy(settings["pools"][section])
    count = settings[f"random_{section}_count"]
    try:
        sample = catalog.resolve_selection(section, selection, count, random.Random(0))
    except CatalogError as error:
        return False, str(error), False
    old_ids = [item.get("id") for item in original]
    if [item.get("id") for item in sample] != old_ids or count > 1:
        # Execution compares ordered IDs. Reordering a valid multi-item draw
        # remains an alternative, including a unique unordered combination.
        return True, "", True
    if selection["mode"] == "all":
        selection["excluded_ids"] = [*selection["excluded_ids"], *old_ids]
    else:
        selection["ids"] = [item_id for item_id in selection["ids"] if item_id not in old_ids]
    try:
        catalog.resolve_selection(section, selection, count, random.Random(0))
    except CatalogError:
        return False, f"{section} 随机池没有区别于历史结果的可用替代组合，请先增加候选项", True
    return True, "", True


@web.middleware
async def local_only_middleware(request: web.Request, handler: Any) -> web.StreamResponse:
    # 只信任本机来源:防御恶意网页发起的 CSRF 与 DNS 重绑定。
    allowed_hostnames = request.app[ALLOWED_HOSTNAMES_KEY]
    hosts = request.headers.getall("Host", [])
    authority = parse_authority(hosts[0]) if len(hosts) == 1 else None
    if authority is None or authority[0] not in allowed_hostnames:
        return web.json_response({"error": "仅允许本机访问"}, status=403)
    hostname, host_port = authority
    origins = request.headers.getall("Origin", [])
    if origins:
        origin = parse_origin(origins[0]) if len(origins) == 1 else None
        if origin is None or origin[1] != hostname:
            return web.json_response({"error": "仅允许同源访问"}, status=403)
        scheme, _, origin_port = origin
        # Compare the actual local origin, including its port. Never trust
        # client-supplied X-Forwarded-* headers.
        if hostname in LOCAL_HOSTNAMES and scheme != request.scheme:
            return web.json_response({"error": "仅允许同源访问"}, status=403)
        if origin_port != (host_port or (443 if scheme == "https" else 80)):
            return web.json_response({"error": "仅允许同源访问"}, status=403)
    if request.path.startswith("/api/") and request.headers.get("Sec-Fetch-Site") in {"cross-site", "same-site"}:
        return web.json_response({"error": "仅允许同源访问"}, status=403)
    return await handler(request)


@web.middleware
async def error_middleware(request: web.Request, handler: Any) -> web.StreamResponse:
    try:
        response = await handler(request)
    except PoolLifecycleError as error:
        response = web.json_response(error.payload, status=503)
    except FavoriteSavePendingError as error:
        response = web.json_response(
            {"error": str(error), "code": "favorite_save_pending", "item_id": error.item_id,
             "source_saved": True, "retryable": True},
            status=502,
        )
    except StorageReadOnlyError as error:
        response = web.json_response({"error": str(error), "code": error.code, "store": error.store,
                                      "path": str(error.path), "source_committed": False,
                                      "recovery_required": False}, status=503)
    except ArtworkCollectionConflict as error:
        response = web.json_response(error.payload, status=409)
    except WorkflowError as error:
        response = web.json_response({"error": str(error)}, status=400)
    except CatalogError as error:
        response = web.json_response({"error": str(error)}, status=400)
    except BatchConflict as error:
        response = web.json_response({"error": str(error)}, status=409)
    except KeyError:
        response = web.json_response({"error": "记录不存在"}, status=404)
    except ComfyError as error:
        response = web.json_response({"error": str(error)}, status=503)
    except json.JSONDecodeError:
        response = web.json_response({"error": "请求 JSON 无效"}, status=400)
    except RequestPayloadError:
        response = web.json_response({"error": "请求体传输或压缩编码无效"}, status=400)
        # The payload parser cannot recover this HTTP connection reliably.
        response.force_close()
    except web.HTTPException as error:
        if error.status < 400 or not request.path.startswith("/api/"):
            raise
        response = web.json_response(
            {"error": error.reason},
            status=error.status,
            headers={
                name: value for name, value in error.headers.items()
                if name.lower() not in {"content-type", "content-length"}
            },
        )
    except Exception:
        # Preserve cancellation (a BaseException) and never return internal
        # paths, provider responses or tracebacks to API consumers.
        logger.exception("Unhandled request failure: %s %s", request.method, request.path)
        response = web.json_response({"error": "服务内部错误，请检查服务日志", "code": "internal_error"}, status=500)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    if request.path == "/" or request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


async def _json_body(request: web.Request) -> dict[str, Any]:
    if request.content_type != "application/json" and not (
        request.content_type.startswith("application/") and request.content_type.endswith("+json")
    ):
        raise web.HTTPUnsupportedMediaType(reason="请求 Content-Type 必须是 application/json")
    try:
        value = await request.json(loads=strict_json_loads)
    except (ValueError, UnicodeError, LookupError, RecursionError) as error:
        raise WorkflowError("请求 JSON 无效") from error
    if not isinstance(value, dict):
        raise WorkflowError("请求内容必须是对象")
    return value


def _image_id(raw: Any) -> int:
    if isinstance(raw, str) and raw.isascii() and raw.isdecimal() and len(raw) <= 19:
        raw = int(raw)
    if type(raw) is not int or not 1 <= raw <= SQLITE_MAX_INTEGER:
        raise WorkflowError("image_id 必须为有效作品 ID")
    return raw


def _query_bool(request: web.Request, name: str, default: bool = False) -> bool:
    raw = request.query.get(name)
    if raw is None:
        return default
    if raw.lower() in {"1", "true", "yes"}:
        return True
    if raw.lower() in {"0", "false", "no"}:
        return False
    raise WorkflowError(f"{name} 必须是布尔值")


def _require_local_file_request(request: web.Request) -> None:
    """Filesystem actions require a direct, same-origin local browser request."""
    authority = parse_authority(request.headers.get("Host", ""))
    origin = parse_origin(request.headers.get("Origin", ""))
    peer = request.transport.get_extra_info("peername") if request.transport else None
    try:
        local_peer = bool(peer) and ipaddress.ip_address(peer[0]).is_loopback
    except ValueError:
        local_peer = False
    if (not local_peer or not authority or authority[0] not in LOCAL_HOSTNAMES
            or origin != (request.scheme, authority[0], authority[1] or (443 if request.scheme == "https" else 80))
            or any(name.lower() == "forwarded" or name.lower().startswith("x-forwarded-") for name in request.headers)):
        raise web.HTTPForbidden(reason="原图文件操作仅允许在本机工作台中进行")


def create_app(
    *,
    app_dir: str | Path = APP_DIR,
    comfy: Any | None = None,
    history_path: str | Path | None = None,
    custom_prompts_path: str | Path | None = None,
    style_presets_path: str | Path | None = None,
    lora_trigger_overrides_path: str | Path | None = None,
    prompt_replacements_path: str | Path | None = None,
    anima_tools_dir: str | Path | None = None,
    trusted_hostnames: set[str] | frozenset[str] | None = None,
    data_dir: str | Path | None = None,
    resource_paths: dict[str, str] | None = None,
) -> web.Application:
    normalized_trusted_hostnames = {
        hostname
        for value in trusted_hostnames or set()
        if (hostname := _hostname(value))
    }
    if normalized_trusted_hostnames - LOCAL_HOSTNAMES:
        raise ValueError("WebUI 只允许本机主机名")
    root = Path(app_dir)
    storage = Path(data_dir) if data_dir is not None else root / "data"
    client = comfy or ComfyClient()
    history = HistoryStore(history_path or storage / "history.sqlite3")
    thumbnails = ThumbnailStore(storage / "history-thumbnails", client, history=history)
    catalog = PromptCatalog(root, anima_tools_dir)
    custom_prompts = CustomPromptStore(
        custom_prompts_path or storage / "custom_prompts.json", catalog
    )
    favorite_store = FavoritesStore(storage / "favorites.json")
    favorites = FavoritesService(favorite_store, catalog)
    profile_favorites = {"anima": favorites}
    prompt_rules = PromptRuleStore(
        prompt_replacements_path or storage / "prompt_replacements.json"
    )
    lora_trigger_overrides = LoraTriggerOverrideStore(
        lora_trigger_overrides_path or storage / "lora_trigger_overrides.json"
    )
    lora_library_store = LoraLibraryStore(storage / "lora_library.json")
    lora_library = LoraLibraryService(client, lora_library_store, lora_trigger_overrides, catalog,
                                      local_usage=history.lora_usage_counts)
    style_presets = StylePresetStore(
        style_presets_path or storage / "style_presets.json", prompt_rules
    )
    pool_lifecycle = PoolLifecycleService(storage, custom_prompts, profile_favorites)
    templates = WorkflowTemplates.load(root / "templates")
    startup_defaults = configure_resources(templates, resource_paths)
    manager = BatchManager(templates, history, client, catalog, prompt_rules)
    manager.lora_library = lora_library
    artwork_deletion = ArtworkDeletionService(history, thumbnails)

    startup_warnings = [
        *catalog.load_warnings,
        *history.load_warnings,
        *custom_prompts.load_warnings,
        *style_presets.load_warnings,
        *lora_trigger_overrides.load_warnings,
        *lora_library_store.load_warnings,
        *prompt_rules.load_warnings,
        *favorite_store.load_warnings,
    ]

    app = web.Application(middlewares=[error_middleware, local_only_middleware], client_max_size=16 * 1024 * 1024)
    app[ALLOWED_HOSTNAMES_KEY] = frozenset(LOCAL_HOSTNAMES)
    app[COMFY_KEY] = client
    app[HISTORY_KEY] = history
    app[MANAGER_KEY] = manager
    app[CATALOG_KEY] = catalog
    app[CUSTOM_PROMPTS_KEY] = custom_prompts
    app[FAVORITES_KEY] = favorites
    app[STYLE_PRESETS_KEY] = style_presets
    app[LORA_TRIGGER_OVERRIDES_KEY] = lora_trigger_overrides
    app[LORA_LIBRARY_KEY] = lora_library
    app[PROMPT_RULES_KEY] = prompt_rules
    app[THUMBNAILS_KEY] = thumbnails
    app[ARTWORK_DELETION_KEY] = artwork_deletion
    cleanup_task: asyncio.Task[None] | None = None

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
                "defaults": startup_defaults,
                "model_profiles": {profile_id: profile_public(profile_id) for profile_id in MODEL_PROFILE_IDS},
                "comfy_url": getattr(client, "base_url", "local"),
                "catalog": {
                    "available": catalog.available,
                    "path": str(catalog.tools_dir or ""),
                    "counts": {section: catalog.count(section) for section in SECTIONS},
                    "discovery": catalog.discovery,
                },
                "storage_status": {
                    store.store_name: {**store.storage_status(), "path": str(store.path)}
                    for store in (history, custom_prompts, style_presets, lora_trigger_overrides, lora_library_store, prompt_rules, favorite_store)
                },
                "warnings": startup_warnings,
            }
        )

    async def _pool_response(
        section: str, params: dict[str, Any], selection: dict[str, Any] | None,
        *, ids_only: bool = False,
    ) -> web.Response:
        """列表和全部匹配 ID 共用参数归一化、筛选与排序。"""
        try:
            model_profile = normalize_model_profile(params.get("model_profile", "anima"))
        except ValueError as error:
            raise WorkflowError(str(error)) from error
        await pool_lifecycle.ensure_ready(model_profile)
        try:
            page = 1 if ids_only else int(params.get("page") or 1)
            limit = 48 if ids_only else int(params.get("limit") or 48)
        except (TypeError, ValueError) as error:
            raise WorkflowError("分页参数无效") from error
        selection_state = params.get("selection_state")
        if selection_state is not None and not isinstance(selection_state, dict):
            raise WorkflowError("selection_state 必须是对象")
        if selection_state is not None:
            if selection_state.get("mode", "include") not in ("include", "all"):
                raise WorkflowError("selection_state.mode 必须是 include 或 all")
            for key in ("ids", "excluded_ids"):
                if key in selection_state and not isinstance(selection_state[key], list):
                    raise WorkflowError(f"selection_state.{key} 必须是数组")
        collection = str(params.get("collection") or "")
        sort = str(params.get("sort") or "")
        custom_group = str(params.get("custom_group") or "")
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
            custom_group=custom_prompts.group_filter_ids(section, custom_group)
            if custom_group
            else "",
            favorite_keys=keys,
            favorites_only=bool(collection),
            sort=sort,
            page=page,
            limit=limit,
            selection=selection,
            selection_state=selection_state,
            ids_only=ids_only,
        )
        return web.json_response(result)

    async def pool(request: web.Request) -> web.Response:
        params: dict[str, Any] = {
            key: request.query.get(key, "")
            for key in ("q", "gender", "hair", "eye", "series", "custom_group", "collection", "sort")
        }
        params["page"] = request.query.get("page", "1")
        params["limit"] = request.query.get("limit", "48")
        params["model_profile"] = request.query.get("model_profile", "anima")
        params["categories"] = request.query.getall("category", [])
        params["traits"] = request.query.getall("trait", [])
        return await _pool_response(request.match_info["section"], params, None)

    async def _pool_query_response(request: web.Request, *, ids_only: bool = False) -> web.Response:
        body = await _json_body(request)
        params = dict(body)
        params.setdefault("model_profile", "anima")
        params["categories"] = body.get("categories") if isinstance(body.get("categories"), list) else []
        params["traits"] = body.get("traits") if isinstance(body.get("traits"), list) else []
        selection = body.get("selection") if isinstance(body.get("selection"), dict) else None
        return await _pool_response(request.match_info["section"], params, selection, ids_only=ids_only)

    async def pool_query(request: web.Request) -> web.Response:
        return await _pool_query_response(request)

    async def pool_ids(request: web.Request) -> web.Response:
        return await _pool_query_response(request, ids_only=True)

    def request_favorites(request: web.Request) -> FavoritesService:
        try:
            profile = normalize_model_profile(request.query.get("model_profile", "anima"))
        except ValueError as error:
            raise WorkflowError(str(error)) from error
        service = profile_favorites[profile]
        service._section(request.match_info["section"])
        return service

    async def get_favorites(request: web.Request) -> web.Response:
        service = request_favorites(request)
        await pool_lifecycle.ensure_ready(service.model_profile)
        return web.json_response(await service.get(request.match_info["section"]))

    async def reconcile_favorites(request: web.Request) -> web.Response:
        service = request_favorites(request)
        return web.json_response(await pool_lifecycle.reconcile(service.model_profile, request.match_info["section"]))

    async def update_favorite(request: web.Request) -> web.Response:
        return web.json_response(
            await pool_lifecycle.favorite(request_favorites(request).model_profile,
                                          request.match_info["section"], "update_item", await _json_body(request))
        )

    async def save_favorite_text(request: web.Request) -> web.Response:
        service = request_favorites(request)
        return web.json_response(
            await pool_lifecycle.save_text(service.model_profile, request.match_info["section"], await _json_body(request))
        )

    async def update_favorite_selection(request: web.Request) -> web.Response:
        service = request_favorites(request)
        body = await _json_body(request)
        return web.json_response(
            await pool_lifecycle.favorite(
                service.model_profile, request.match_info["section"], "update_selection",
                body.get("selection"), body.get("groupIds")
            )
        )

    async def create_favorite_group(request: web.Request) -> web.Response:
        return web.json_response(
            await pool_lifecycle.favorite(request_favorites(request).model_profile,
                                          request.match_info["section"], "create_group", await _json_body(request)),
            status=201,
        )

    async def update_favorite_group(request: web.Request) -> web.Response:
        return web.json_response(
            await pool_lifecycle.favorite(
                request_favorites(request).model_profile, request.match_info["section"], "update_group",
                request.match_info["group_id"], await _json_body(request)
            )
        )

    async def import_favorite_child_group(request: web.Request) -> web.Response:
        service = request_favorites(request)
        section = request.match_info["section"]
        body = await _json_body(request)
        custom_group_id = str(body.get("customGroupId") or "").strip()
        if not custom_group_id:
            raise WorkflowError("customGroupId 不能为空")
        source_groups = custom_prompts.list_groups(section)["groups"]
        source_group = next(
            (
                group
                for group in source_groups
                if group["id"] == custom_group_id
            ),
            None,
        )
        if source_group is None:
            raise KeyError(custom_group_id)
        return web.json_response(
            await pool_lifecycle.favorite(
                service.model_profile, section, "import_custom_group",
                request.match_info["parent_id"],
                source_group,
                custom_prompts.list(section, custom_group_id),
            ),
            status=201,
        )

    async def delete_favorite_group(request: web.Request) -> web.Response:
        return web.json_response(
            await pool_lifecycle.favorite(
                request_favorites(request).model_profile, request.match_info["section"], "delete_group",
                request.match_info["group_id"],
                _query_bool(request, "deleteItems"),
            )
        )

    async def list_custom_prompts(request: web.Request) -> web.Response:
        await pool_lifecycle.ensure_ready("anima")
        section = request.query.get("section") or None
        return web.json_response({"items": custom_prompts.list(section)})

    async def create_custom_prompt(request: web.Request) -> web.Response:
        body = await _json_body(request)
        return web.json_response(await pool_lifecycle.source("anima", body.get("section", ""), "create", body), status=201)

    async def update_custom_prompt(request: web.Request) -> web.Response:
        custom_prompts.ensure_writable()
        item = catalog.get(request.match_info["item_id"])
        if item is None:
            raise KeyError(request.match_info["item_id"])
        return web.json_response(await pool_lifecycle.source("anima", item["section"], "update",
                                                            request.match_info["item_id"], await _json_body(request)))

    async def delete_custom_prompt(request: web.Request) -> web.Response:
        custom_prompts.ensure_writable()
        item = catalog.get(request.match_info["item_id"])
        if item is None:
            raise KeyError(request.match_info["item_id"])
        return web.json_response(await pool_lifecycle.source("anima", item["section"], "delete", request.match_info["item_id"]))

    async def list_custom_groups(request: web.Request) -> web.Response:
        await pool_lifecycle.ensure_ready("anima")
        return web.json_response(custom_prompts.list_groups(request.match_info["section"]))

    async def create_custom_group(request: web.Request) -> web.Response:
        return web.json_response(
            await pool_lifecycle.source("anima", request.match_info["section"], "create_group",
                                        request.match_info["section"], await _json_body(request)),
            status=201,
        )

    async def update_custom_group(request: web.Request) -> web.Response:
        return web.json_response(
            await pool_lifecycle.source("anima", request.match_info["section"], "update_group",
                request.match_info["section"], request.match_info["group_id"], await _json_body(request)
            )
        )

    async def delete_custom_group(request: web.Request) -> web.Response:
        delete_mode = request.query.get("deleteMode")
        delete_items = False if delete_mode is not None else _query_bool(request, "deleteItems")
        return web.json_response(
            await pool_lifecycle.source("anima", request.match_info["section"], "delete_group",
                request.match_info["section"],
                request.match_info["group_id"],
                delete_items,
                delete_mode,
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
                str(body.get("bundleName") or ""),
            )
        )

    async def commit_custom_import(request: web.Request) -> web.Response:
        body = await _json_body(request)
        return web.json_response(
            await pool_lifecycle.source("anima", str(body.get("section") or ""), "commit_import",
                body.get("rows"),
                str(body.get("section") or ""),
                body.get("targetGroupIds"),
                str(body.get("bundleName") or ""),
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

    async def _lora_inventory_item(filename: Any) -> tuple[dict[str, Any], str]:
        target = str(filename or "")
        if not target:
            raise WorkflowError("filename 不能为空")
        from .workflow import normalize_lora_path

        identity = normalize_lora_path(target).casefold()
        inventory = await client.lora_inventory()
        matches = [
            item
            for item in inventory.get("items") or []
            if normalize_lora_path(item.get("filename")).casefold() == identity
        ]
        if len(matches) != 1:
            raise WorkflowError(f"LoRA 文件不存在: {target}")
        return matches[0], normalize_lora_path(matches[0]["filename"])

    def _public_lora_item(
        item: dict[str, Any], model_profile: str = "anima"
    ) -> dict[str, Any]:
        value = technical_resource_metadata(item)
        source_words = value.get("trigger_words") or []
        effective_words, overridden = lora_trigger_overrides.effective(
            value["filename"], source_words, model_profile
        )
        # Trigger overrides remain profile-scoped; they must not rewrite the
        # authoritative classification when editing an unavailable resource.
        value.setdefault("model_profile", model_profile)
        value["source_trigger_words"] = source_words
        value["trigger_words"] = effective_words
        value["trigger_override"] = overridden
        value["default_strength"] = lora_library.default_strength(value["filename"])
        if overridden:
            value["trigger_metadata_available"] = True
        preview = str(value.get("preview") or "")
        value["preview"] = (
            f"/api/loras/preview?{urlencode({'filename': value['filename']})}"
            if preview
            else ""
        )
        return value

    async def loras(request: web.Request) -> web.Response:
        model_profile = request.query.get("model_profile", "anima")
        # Validation happens even when inventory is empty.
        from .model_profiles import normalize_model_profile
        try:
            model_profile = normalize_model_profile(model_profile)
        except ValueError as error:
            raise WorkflowError(str(error)) from error
        inventory = await client.resource_classifications(
            "loras", refresh=_query_bool(request, "refresh")
        )
        inventory_items = [technical_resource_metadata(item) for item in inventory.get("items") or []]
        public = {
            "items": [],
            "count": 0,
            "source": inventory["source"],
            "manager_url": inventory["manager_url"],
            "classification_items": inventory_items,
        }
        for item in inventory_items:
            if item.get("model_profile") != model_profile:
                continue
            public["items"].append(_public_lora_item(item, model_profile))
        public["count"] = len(public["items"])
        public["model_profile"] = model_profile
        return web.json_response(public)

    async def update_lora_triggers(request: web.Request) -> web.Response:
        body = await _json_body(request)
        from .model_profiles import normalize_model_profile
        try:
            model_profile = normalize_model_profile(body.get("model_profile", "anima"))
        except ValueError as error:
            raise WorkflowError(str(error)) from error
        item, filename = await _lora_inventory_item(body.get("filename"))
        words = await lora_trigger_overrides.set(
            filename, body.get("triggerWords"), model_profile
        )
        value = _public_lora_item({**item, "filename": filename}, model_profile)
        value["trigger_words"] = words
        value["trigger_override"] = True
        return web.json_response(value)

    async def reset_lora_triggers(request: web.Request) -> web.Response:
        from .model_profiles import normalize_model_profile
        try:
            model_profile = normalize_model_profile(
                request.query.get("model_profile", "anima")
            )
        except ValueError as error:
            raise WorkflowError(str(error)) from error
        item, filename = await _lora_inventory_item(request.query.get("filename"))
        await lora_trigger_overrides.delete(filename, model_profile)
        return web.json_response(
            _public_lora_item({**item, "filename": filename}, model_profile)
        )

    async def lora_preview(request: web.Request) -> web.Response:
        filename = request.query.get("filename")
        if not filename:
            raise WorkflowError("filename 不能为空")
        body, content_type, cache_control = await client.lora_preview(filename)
        return web.Response(
            body=body,
            content_type=content_type.split(";", 1)[0],
            headers={"Cache-Control": cache_control},
        )

    async def resources(request: web.Request) -> web.Response:
        try:
            selected_profile = normalize_model_profile(
                request.query.get("model_profile", "anima")
            )
        except ValueError as error:
            raise WorkflowError(str(error)) from error
        classified = await client.resource_classifications(
            "models", refresh=_query_bool(request, "refresh")
        )
        inventory = dict(await client.resource_inventory())
        inventory.setdefault("clips", [])
        inventory.setdefault("vaes", [])
        inventory.setdefault("seedvr2_models", [])
        inventory.setdefault("seedvr2_vaes", [])
        inventory.setdefault("nodes", {})
        inventory.setdefault("plugins", {})
        model_items = [technical_resource_metadata(item) for item in classified["items"]]
        model_names = [item["name"] for item in model_items]
        full_inventory = dict(inventory)
        full_inventory["models"] = model_names
        model_ownership = {item["name"]: item["model_profile"] for item in model_items}
        inventory["model_items"] = model_items
        inventory["source"] = classified["source"]
        inventory["manager_url"] = classified["manager_url"]
        inventory["model_assignments"] = {
            name: model_ownership.get(name) for name in model_names
        }
        inventory["profile_models"] = {
            profile_id: [
                name for name in model_names if model_ownership.get(name) == profile_id
            ]
            for profile_id in MODEL_PROFILE_IDS
        }
        inventory["unassigned_models"] = [
            name for name in model_names if model_ownership.get(name) is None
        ]
        # Preserve the existing compact inventory keys while making the
        # profile-specific model dropdown safe by construction.
        inventory["models"] = list(inventory["profile_models"].get(selected_profile, []))
        inventory["model_profile"] = selected_profile
        profile_details: dict[str, dict[str, Any]] = {}
        for profile_id in MODEL_PROFILE_IDS:
            profile_models = inventory["profile_models"][profile_id]
            profile_inventory = {**full_inventory, "models": profile_models}
            details = profile_public(profile_id, available=profile_inventory)
            profile_model_name = details["model_name"]
            resolved_model_name = _find_inventory_model_match(
                profile_model_name, model_names
            )
            if resolved_model_name not in profile_models:
                resolved_model_name = None
            if resolved_model_name is not None:
                details["model_name"] = resolved_model_name
            # profile_public's basename-aware resource check intentionally
            # works for generic callers. Here ownership and ambiguity must be
            # reflected too: only an exact or unique match in this profile's
            # full inventory makes the default model available. A known but
            # incompatible exact path must never redirect to a subfolder.
            details["available"]["model"] = resolved_model_name is not None
            if not details["available"]["model"] and profile_model_name not in details["missing"]:
                details["missing"].append(profile_model_name)
            profile_details[profile_id] = details
        inventory["model_profiles"] = profile_details
        return web.json_response(inventory)

    async def resource_ownership(request: web.Request) -> web.Response:
        kind = request.query.get("kind", "models")
        if kind not in RESOURCE_KINDS:
            raise WorkflowError("kind 必须是 models 或 loras")
        classified = await client.resource_classifications(
            kind, refresh=_query_bool(request, "refresh")
        )
        items = [technical_resource_metadata(item) for item in classified["items"]]
        return web.json_response(
            {
                "kind": kind,
                "source": classified["source"],
                "manager_url": classified["manager_url"],
                "items": items,
                "unassigned": [item for item in items if item["model_profile"] is None],
                "assignments": {
                    item["name"]: item["model_profile"] for item in items
                },
            }
        )

    async def update_resource_ownership(request: web.Request) -> web.Response:
        return web.json_response(
            {"error": "本地归属编辑已停用，请在 ComfyUI-Lora-Manager 中修改基础模型分类，然后刷新分类。",
             "code": "classification_managed_by_lora_manager"},
            status=410,
        )


















    async def list_style_presets(request: web.Request) -> web.Response:
        return web.json_response(style_presets.list(request.query.get("model_profile")))

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

    async def list_prompt_rules(request: web.Request) -> web.Response:
        return web.json_response(prompt_rules.list(request.query.get("model_profile", "anima")))

    async def create_prompt_rule(request: web.Request) -> web.Response:
        body = await _json_body(request)
        profile = body.pop("model_profile", "anima")
        return web.json_response(
            await prompt_rules.create(body, model_profile=profile), status=201
        )

    async def update_prompt_rule(request: web.Request) -> web.Response:
        body = await _json_body(request)
        profile = body.pop("model_profile", "anima")
        return web.json_response(
            await prompt_rules.update(
                request.match_info["rule_id"], body, model_profile=profile
            )
        )

    async def delete_prompt_rule(request: web.Request) -> web.Response:
        rule_id = request.match_info["rule_id"]
        if not await prompt_rules.delete(rule_id, model_profile=request.query.get("model_profile", "anima")):
            raise KeyError(rule_id)
        return web.json_response({"deleted": True})

    async def normalize_prompts(request: web.Request) -> web.Response:
        body = await _json_body(request)
        unknown = set(body) - {"fields", "managedTriggers", "model_profile"}
        if unknown:
            raise WorkflowError(f"提示词规范请求包含未知参数: {', '.join(sorted(unknown))}")
        try:
            profile = normalize_model_profile(body.get("model_profile", "anima"))
        except ValueError as error:
            raise WorkflowError(str(error)) from error
        result = prompt_rules.normalize_fields(
            body.get("fields"), body.get("managedTriggers", []), model_profile=profile
        )
        result["model_profile"] = profile
        return web.json_response(result)

    async def validate_resource_ownership(
        settings: dict[str, Any], *, resources_resolved: bool = False,
    ) -> None:
        """Reject stale or cross-profile model/LoRA selections at the API edge."""
        try:
            profile = normalize_model_profile(settings.get("model_profile", "anima"))
        except ValueError as error:
            raise WorkflowError(str(error)) from error
        # Submission must observe plugin edits/disconnection even when a list
        # request populated the short-lived display cache moments earlier.
        inventory = await client.resource_classifications("models", refresh=True)
        classifications = {item["name"]: item for item in inventory["items"]}
        model_names = list(classifications)
        model_owners = {name: item["model_profile"] for name, item in classifications.items()}
        model_name = settings.get("model_name") or model_profile_defaults(profile)["model_name"]
        settings["model_name"] = _resolve_owned_model(
            model_name, model_names, model_owners, profile, classifications,
            allow_basename=not resources_resolved,
        )

        active = active_lora_pipelines(validate_settings(settings))
        stacks = []
        if "primary" in active:
            stacks.extend(settings.get("loras") or [])
        lora_items = [item for item in stacks if isinstance(item, dict) and item.get("enabled")]
        if not lora_items:
            return
        lora_inventory = await client.resource_classifications("loras", refresh=True)
        available = {normalize_lora_path(item["name"]).casefold(): item for item in lora_inventory["items"]}
        settings["loras"] = validate_loras(
            settings, [item["name"] for item in lora_inventory["items"]],
            allow_basename=not resources_resolved,
        )
        for item in settings["loras"]:
            if not item["enabled"]:
                continue
            name = normalize_lora_path(item.get("filename"))
            classified = available.get(name.casefold())
            if classified is None:
                raise WorkflowError(f"LoRA 不存在于当前 ComfyUI 清单: {name}")
            if classified.get("model_profile") != profile:
                reason = classified.get("classification_reason") or f"插件分类为 {classified.get('base_model') or 'Unknown'}"
                raise WorkflowError(f"LoRA 不适用于 {profile} 档案: {name}；{reason}，请在 LoRA Manager 中检查分类")


    async def start_batch(request: web.Request) -> web.Response:
        history.ensure_writable()
        body = await _json_body(request)
        seeds = validate_seeds(body.pop("seeds", None))
        if resource_paths is not None:
            def merge(base: dict[str, Any], submitted: dict[str, Any]) -> dict[str, Any]:
                result = copy.deepcopy(base)
                for key, value in submitted.items():
                    result[key] = merge(result[key], value) if isinstance(result.get(key), dict) and isinstance(value, dict) else copy.deepcopy(value)
                return result
            # Only bind startup resource paths. Mode-dependent sampler defaults
            # must still be selected from the submitted base_mode below.
            resource_defaults = {
                key: startup_defaults[key]
                for key in ("model_name", "clip_name", "vae_name")
            }
            resource_defaults["upscalers"] = {
                stage: {key: startup_defaults["upscalers"][stage][key] for key in fields}
                for stage, fields in (
                    ("pixel_hires", ("model_name",)),
                    ("seedvr2", ("model_name", "vae_name")),
                )
            }
            body = merge(resource_defaults, body)
        body = upgrade_pipeline_settings(body)
        validate_settings(body)
        await client.status()
        await validate_resource_ownership(body)
        state = await manager.start(body, seeds=seeds)
        return web.json_response(state, status=201)














    async def current_batch(_: web.Request) -> web.Response:
        return web.json_response({"batch": manager.snapshot(), "queue": manager.queue_snapshot()})

    async def stop_batch(request: web.Request) -> web.Response:
        state = await manager.request_stop(
            request.match_info["batch_id"],
            clear_queue=_query_bool(request, "clearQueue", True),
        )
        return web.json_response({"batch": state, "queue": manager.queue_snapshot()})

    async def remove_queued_batch(request: web.Request) -> web.Response:
        if not manager.remove_queued(request.match_info["queue_id"]):
            raise KeyError(request.match_info["queue_id"])
        return web.json_response({"queue": manager.queue_snapshot()})

    async def batch_preview(request: web.Request) -> web.Response:
        preview = manager.preview
        owner = manager.preview_metadata
        headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        if preview is None:
            return web.Response(status=204, headers=headers)
        for key in ("batch_id", "prompt_id", "node_id"):
            if key in request.query and (owner is None or request.query[key] != owner[key]):
                return web.Response(status=204, headers=headers)
        if "epoch" in request.query and (owner is None or request.query["epoch"] != str(owner.get("epoch"))):
            return web.Response(status=204, headers=headers)
        if owner is not None:
            state = manager.state or {}
            if (owner["batch_id"] != state.get("id") or owner["prompt_id"] != state.get("prompt_id")
                    or owner["epoch"] != state.get("preview_epoch")
                    or state.get("stage") != "generating" or state.get("status") not in ("running", "stopping")):
                return web.Response(status=204, headers=headers)
            headers.update({"X-Preview-Batch-Id": owner["batch_id"], "X-Preview-Prompt-Id": owner["prompt_id"],
                            "X-Preview-Node-Id": owner["node_id"], "X-Preview-Epoch": str(owner["epoch"])})
        seq, content_type, body = preview
        headers["X-Preview-Id"] = str(seq)
        return web.Response(body=body, content_type=content_type, headers=headers)

    async def list_artwork_folders(_: web.Request) -> web.Response:
        return web.json_response(await history.list_artwork_folders())

    async def create_artwork_folder(request: web.Request) -> web.Response:
        body = await _json_body(request)
        if set(body) != {"name"}:
            raise WorkflowError("收藏夹只接受 name")
        return web.json_response(await history.create_artwork_folder(body["name"]), status=201)

    async def rename_artwork_folder(request: web.Request) -> web.Response:
        body = await _json_body(request)
        if set(body) != {"name"}:
            raise WorkflowError("收藏夹只接受 name")
        return web.json_response(await history.rename_artwork_folder(request.match_info["folder_id"], body["name"]))

    async def delete_artwork_folder(request: web.Request) -> web.Response:
        return web.json_response(await history.delete_artwork_folder(request.match_info["folder_id"]))

    async def reorder_artwork_folders(request: web.Request) -> web.Response:
        body = await _json_body(request)
        if set(body) != {"folder_ids"}:
            raise WorkflowError("收藏夹排序只接受 folder_ids")
        return web.json_response(await history.reorder_artwork_folders(body["folder_ids"]))

    async def update_artwork_members(request: web.Request) -> web.Response:
        body = await _json_body(request)
        if set(body) != {"image_ids", "folder_ids", "action"}:
            raise WorkflowError("收藏请求需要 image_ids、folder_ids 和 action")
        return web.json_response(await history.update_artwork_memberships(body["image_ids"], body["folder_ids"], body["action"]))

    async def read_artwork_members(request: web.Request) -> web.Response:
        body = await _json_body(request)
        if set(body) != {"image_ids"}:
            raise WorkflowError("收藏归属查询只接受 image_ids")
        return web.json_response(await history.read_artwork_memberships(body["image_ids"]))

    async def list_history(request: web.Request) -> web.Response:
        try:
            page = int(request.query.get("page", "1"))
            limit = int(request.query.get("limit", "24"))
        except ValueError as error:
            raise WorkflowError("分页参数无效") from error
        if max(0, page - 1) * min(60, max(1, limit)) > SQLITE_MAX_INTEGER:
            raise WorkflowError("分页参数超出有效范围")
        before_id = None
        if "before_id" in request.query:
            raw_before_id = request.query["before_id"]
            if not raw_before_id.isascii() or not raw_before_id.isdecimal():
                raise WorkflowError("before_id 必须为正整数图片 ID")
            try:
                before_id = int(raw_before_id)
            except ValueError as error:
                raise WorkflowError("before_id 必须为正整数图片 ID") from error
            if not 1 <= before_id <= 2**63 - 1:
                raise WorkflowError("before_id 必须为正整数图片 ID")
        workbench = request.query.get("workbench") or None
        if workbench not in {None, "random"}:
            raise WorkflowError("workbench 仅支持 random")
        folder_id = request.query.get("folder_id")
        if folder_id is not None and before_id is not None:
            raise WorkflowError("收藏夹按收藏时间分页，不能与 before_id 同时使用")
        return web.json_response({
            **await history.list_images(page, limit, workbench, before_id=before_id, folder_id=folder_id),
            "available": history.available,
        })

    async def _regeneration_context(image_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
        record = await history.get_image(image_id)
        settings = dict(record.get("settings") or {})
        positive = ""
        for candidate in (record.get("positive_prompt"), record.get("resolved_prompt")):
            text = str(candidate or "")
            if text.strip():
                positive = text
                break
        negative = ""
        for candidate in (record.get("negative_prompt"), settings.get("negative_prompt")):
            text = str(candidate or "")
            if text.strip():
                negative = text
                break
        selected = record.get("resolved_selection") or {}
        if not isinstance(selected, dict):
            selected = {}
        restored, preserve_issues = resolve_history_selection(selected, settings, catalog, positive)
        for section, items in restored.items():
            for item in items:
                saved_prompt = str(item.get("prompt") or "")
                normalized, _ = prompt_rules.normalize_text(
                    saved_prompt, "positive", protected_lora_words=settings.get("lora_managed_triggers", []),
                    model_profile=settings.get("model_profile", "anima"),
                )
                if normalized != saved_prompt:
                    preserve_issues[section] = "当前提示词规则会改变历史快照，请同时重抽此维度，或使用同提示词变体"
                    break
        async def inspect_resources(values: dict[str, Any]) -> list[dict[str, Any]]:
            issues = []
            try:
                await validate_resource_ownership(values, resources_resolved=True)
            except (WorkflowError, ComfyError) as error:
                issues.append({"type": "classification", "name": str(error), "label": "模型分类"})
            try:
                issues.extend(await manager.resource_issues(values, resources_resolved=True))
            except ComfyError as error:
                if not issues:
                    issues.append({"type": "classification", "name": str(error), "label": "模型分类"})
            return issues

        frozen_resource_issues = await inspect_resources(settings)
        runtime = record.get("lora_runtime") or {}
        has_lora_runtime = isinstance(runtime, dict) and runtime.get("version") == 1 and isinstance(runtime.get("manual_loras"), list)
        resource_issues = frozen_resource_issues
        if has_lora_runtime:
            if not frozen_resource_issues:
                try:
                    validate_frozen_resources(settings, runtime, await client.resource_classifications("loras", refresh=True))
                except (WorkflowError, ComfyError) as error:
                    frozen_resource_issues = [{"type": "lora", "name": str(error), "label": "历史 LoRA"}]
            manual_settings = copy.deepcopy(settings)
            manual_settings["loras"] = copy.deepcopy(runtime["manual_loras"])
            resource_issues = await inspect_resources(manual_settings)
            if frozen_resource_issues and not resource_issues:
                preserve_issues["character"] = "历史人物 LoRA 已失效，请同时重抽人物以使用当前绑定"
        sections: dict[str, Any] = {}
        redraw_issues: dict[str, str] = {}
        try:
            redraw_settings = validate_settings(settings)
            settings_error = ""
        except WorkflowError as error:
            redraw_settings = {}
            settings_error = str(error)
        for section in SECTIONS:
            random_enabled = bool(settings.get(f"random_{section}"))
            actual = selected.get(section) if isinstance(selected.get(section), list) else []
            can_redraw, redraw_reason, valid_draw = False, "历史设置未启用此随机维度", True
            if random_enabled:
                if settings_error:
                    can_redraw, redraw_reason, valid_draw = False, settings_error, False
                else:
                    can_redraw, redraw_reason, valid_draw = _redraw_availability(
                        catalog, redraw_settings, section, actual,
                    )
                if not valid_draw:
                    redraw_issues[section] = redraw_reason
            sections[section] = {
                "random": random_enabled,
                "hasSnapshot": bool(actual),
                "canRedraw": can_redraw,
                "redrawReason": redraw_reason,
                "canPreserve": section not in preserve_issues,
                "preserveReason": preserve_issues.get(section, ""),
            }
        warnings = [
            "资源文件内容已被覆盖时无法识别，原种子重跑不承诺像素级一致。"
        ]
        if not positive:
            warnings.append("历史记录缺少最终正向提示词，无法可靠回放。")
        context = {
            "positive": positive,
            "negative": negative,
            "selected": selected,
            "restored": restored,
            "resourceIssues": resource_issues,
            "frozenResourceIssues": frozen_resource_issues,
            "sections": sections,
            "redrawIssues": redraw_issues,
            "warnings": warnings,
        }
        return record, context

    async def regeneration_options(request: web.Request) -> web.Response:
        _record, context = await _regeneration_context(
            _image_id(request.match_info["image_id"])
        )
        resources_ok = not context["resourceIssues"] and not history.read_only_reason
        frozen_context = {**context, "resourceIssues": context["frozenResourceIssues"]}
        frozen_resources_ok = not context["frozenResourceIssues"] and not history.read_only_reason
        positive_ok = bool(context["positive"])
        random_sections = [
            section for section, value in context["sections"].items() if value["canRedraw"]
        ]
        redraw_reasons = _regeneration_reasons(context)
        if not random_sections:
            redraw_reasons.extend(
                value["redrawReason"] for value in context["sections"].values()
                if value["random"] and value["redrawReason"]
            )
            if not any(value["random"] for value in context["sections"].values()):
                redraw_reasons.append("历史设置没有可重新抽取的随机维度")
        redraw_reasons.extend(
            reason for section, reason in context["redrawIssues"].items()
            if not context["sections"][section]["canPreserve"] and reason not in redraw_reasons
        )
        return web.json_response(
            {
                "modes": {
                    "replay": {
                        "available": frozen_resources_ok and positive_ok,
                        "reasons": _regeneration_reasons(frozen_context, require_prompt=True),
                        "resourceIssues": context["frozenResourceIssues"],
                    },
                    "prompt_variant": {
                        "available": frozen_resources_ok and positive_ok,
                        "reasons": _regeneration_reasons(frozen_context, require_prompt=True),
                        "resourceIssues": context["frozenResourceIssues"],
                    },
                    "content_redraw": {
                        "available": resources_ok and bool(random_sections) and not redraw_reasons,
                        "reasons": redraw_reasons,
                    },
                    "settings_reroll": {
                        "available": resources_ok,
                        "reasons": _regeneration_reasons(context),
                    },
                },
                "resourceIssues": context["resourceIssues"],
                "warnings": context["warnings"],
                "sections": context["sections"],
            }
        )

    def _regeneration_reasons(
        context: dict[str, Any],
        *,
        require_prompt: bool = False,
    ) -> list[str]:
        reasons = [
            f"{item['label']} 不可用: {item['name']}"
            for item in context["resourceIssues"]
        ]
        if history.read_only_reason:
            reasons.append(history.read_only_reason)
        if require_prompt and not context["positive"]:
            reasons.append("历史记录缺少最终正向提示词")
        return reasons

    async def regenerate_history(request: web.Request) -> web.Response:
        history.ensure_writable()
        image_id = _image_id(request.match_info["image_id"])
        body = await _json_body(request)
        unknown = set(body) - {"mode", "count", "sections"}
        if unknown:
            raise WorkflowError(
                f"再生成请求包含未知参数: {', '.join(sorted(unknown))}"
            )
        mode = body.get("mode")
        if not isinstance(mode, str) or mode not in {"replay", "prompt_variant", "content_redraw", "settings_reroll"}:
            raise WorkflowError("mode 无效")
        if mode == "replay" and set(body) - {"mode"}:
            raise WorkflowError("replay 不接受 count 或 sections")
        if mode != "replay":
            count = body.get("count", 4)
            if type(count) is not int or not 1 <= count <= 100:
                raise WorkflowError("count 必须是 1-100 之间的整数")
        if mode in {"prompt_variant", "settings_reroll"} and "sections" in body:
            raise WorkflowError(f"{mode} 不接受 sections")
        if mode == "content_redraw":
            sections = body.get("sections")
            if (not isinstance(sections, list) or not sections
                    or any(not isinstance(section, str) or section not in SECTIONS for section in sections)
                    or len(sections) != len(set(sections))):
                raise WorkflowError("sections 必须为不重复的有效重抽维度数组")
        await client.status()
        record, context = await _regeneration_context(image_id)
        relevant_issues = context["frozenResourceIssues"] if mode in {"replay", "prompt_variant"} else context["resourceIssues"]
        if relevant_issues:
            missing = "；".join(
                f"{item['label']}: {item['name']}" for item in relevant_issues
            )
            raise WorkflowError(f"历史资源不可用: {missing}")
        settings = copy.deepcopy(record.get("settings") or {})
        if mode == "replay":
            if set(body) - {"mode"}:
                raise WorkflowError("replay 不接受 count 或 sections")
            if not context["positive"]:
                raise WorkflowError("历史记录缺少可回放的最终正向提示词")
            settings["count"] = 1
            regeneration = {
                "mode": mode,
                "frozen_positive_prompt": context["positive"],
                "frozen_negative_prompt": context["negative"],
                "fixed_selection": context["selected"],
            }
            seeds = {
                "sample_seed": record["sample_seed"],
                "prompt_seed": record["prompt_seed"],
            }
        else:
            count = body.get("count", 4)
            if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 100:
                raise WorkflowError("count 必须是 1-100 之间的整数")
            settings["count"] = count
            seeds = None
            if mode == "prompt_variant":
                if "sections" in body:
                    raise WorkflowError("prompt_variant 不接受 sections")
                if not context["positive"]:
                    raise WorkflowError("历史记录缺少可回放的最终正向提示词")
                regeneration = {
                    "mode": mode,
                    "frozen_positive_prompt": context["positive"],
                    "frozen_negative_prompt": context["negative"],
                    "fixed_selection": context["selected"],
                    "prompt_seed": record["prompt_seed"],
                }
            elif mode == "settings_reroll":
                if "sections" in body:
                    raise WorkflowError("settings_reroll 不接受 sections")
                regeneration = {"mode": mode}
            else:
                sections = body.get("sections")
                if not isinstance(sections, list) or not sections:
                    raise WorkflowError("content_redraw 至少选择一个重抽维度")
                if any(not isinstance(section, str) for section in sections):
                    raise WorkflowError("sections 必须是字符串数组")
                if len(sections) != len(set(sections)):
                    raise WorkflowError("重抽维度不能重复")
                invalid = set(sections) - set(SECTIONS)
                if invalid:
                    raise WorkflowError(f"不支持的重抽维度: {', '.join(sorted(invalid))}")
                not_random = [
                    section for section in sections if not context["sections"][section]["random"]
                ]
                if not_random:
                    raise WorkflowError(
                        f"这些维度在历史设置中不是随机池: {', '.join(not_random)}"
                    )
                missing_fixed = [
                    section
                    for section, value in context["sections"].items()
                    if value["random"] and section not in sections and not value["canPreserve"]
                ]
                if missing_fixed:
                    raise WorkflowError(
                        "无法可靠保留历史内容，请同时重抽: " + "; ".join(
                            f"{section}: {context['sections'][section]['preserveReason']}" for section in missing_fixed
                        )
                    )
                invalid_draws = [context["redrawIssues"][section] for section in sections
                                 if section in context["redrawIssues"]]
                if invalid_draws:
                    raise WorkflowError("所选维度无法重抽: " + "；".join(invalid_draws))
                if not any(context["sections"][section]["canRedraw"] for section in sections):
                    raise WorkflowError("所选维度无可用替代组合: " + "；".join(
                        context["sections"][section]["redrawReason"] for section in sections
                    ))
                fixed_selection = {
                    section: context["restored"].get(section, [])
                    for section, value in context["sections"].items()
                    if value["random"] and section not in sections
                }
                regeneration = {
                    "mode": mode,
                    "redraw_sections": sections,
                    "fixed_selection": fixed_selection,
                    "original_selection": context["selected"],
                }
        lora_runtime = record.get("lora_runtime") or {}
        if isinstance(lora_runtime, dict) and lora_runtime.get("version") == 1 and isinstance(lora_runtime.get("manual_loras"), list):
            regeneration["lora_runtime"] = copy.deepcopy(lora_runtime)
            uses_current_bindings = mode == "settings_reroll" or (
                mode == "content_redraw" and "character" in regeneration.get("redraw_sections", [])
            )
            if uses_current_bindings:
                settings["loras"] = copy.deepcopy(lora_runtime["manual_loras"])
                settings["lora_managed_triggers"] = copy.deepcopy(lora_runtime.get("manual_managed_triggers", []))
        await validate_resource_ownership(settings, resources_resolved=True)
        state = await manager.start(settings, seeds=seeds, regeneration=regeneration)
        return web.json_response(state, status=201)

    async def delete_artwork(request: web.Request, image_id: int, mode: Any) -> dict[str, Any]:
        if not isinstance(mode, str) or mode not in {"record", "file"}:
            raise WorkflowError("删除方式必须为 record 或 file")
        stats = None
        if mode == "file":
            _require_local_file_request(request)
            stats = await client.status()
        deletion = asyncio.create_task(artwork_deletion.delete(
            image_id, mode, stats=stats, comfy_url=getattr(client, "base_url", ""),
        ))
        try:
            return await asyncio.shield(deletion)
        except asyncio.CancelledError:
            # Complete a started deletion and its thumbnail invalidation even
            # if the browser closes or repeatedly cancels its request.
            while not deletion.done():
                try:
                    await asyncio.shield(deletion)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not deletion.cancelled():
                deletion.exception()
            raise

    async def delete_history(request: web.Request) -> web.Response:
        image_id = _image_id(request.match_info["image_id"])
        if set(request.query) - {"mode"}:
            raise WorkflowError("删除操作只接受 mode，不接受文件路径")
        payload = await delete_artwork(request, image_id, request.query.get("mode", "record"))
        result = payload["results"][0]
        if result.get("code") == "already_missing":
            raise KeyError(image_id)
        return web.json_response({"deleted": bool(result.get("record_deleted")), **payload},
                                 status=200 if result.get("record_deleted") else 409)

    async def image(request: web.Request) -> web.Response:
        record = await history.get_image(_image_id(request.match_info["image_id"]))
        body, content_type = await client.image_bytes(record)
        return web.Response(body=body, content_type=content_type.split(";", 1)[0])

    async def thumbnail(request: web.Request) -> web.Response:
        image_id = _image_id(request.match_info["image_id"])
        record = await history.get_image(image_id)
        result = await thumbnails.get(record)
        headers = {
            "Cache-Control": "private, max-age=3600",
            "ETag": f'"{result.etag}"',
            "Last-Modified": formatdate(result.modified, usegmt=True),
        }
        if "If-None-Match" in request.headers:
            unchanged = any(tag.value in {"*", result.etag} for tag in request.if_none_match or ())
        else:
            since = request.if_modified_since
            unchanged = since is not None and int(result.modified) <= int(since.timestamp())
        if unchanged:
            return web.Response(status=304, headers=headers)
        return web.Response(body=result.body, content_type="image/webp", headers=headers)

    async def reveal_history_image(request: web.Request) -> web.Response:
        _require_local_file_request(request)
        body = await _json_body(request)
        if body:
            raise WorkflowError("打开所在文件夹仅接受作品 ID，不接受文件路径")
        record = await history.get_image(_image_id(request.match_info["image_id"]))
        stats = await client.status()
        try:
            await asyncio.to_thread(reveal_image, record, stats, getattr(client, "base_url", ""))
        except ImageLocationError as error:
            return web.json_response({"error": str(error), "code": error.code}, status=error.status)
        return web.json_response({"opened": True})

    async def index(_: web.Request) -> web.FileResponse:
        return web.FileResponse(root / "static" / "index.html")

    async def favicon(_: web.Request) -> web.Response:
        return web.Response(status=204)



    app.router.add_get("/api/config", config)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/loras", loras)
    app.router.add_get("/api/loras/preview", lora_preview)
    app.router.add_put("/api/loras/triggers", update_lora_triggers)
    app.router.add_delete("/api/loras/triggers", reset_lora_triggers)
    register_lora_routes(app, lora_library)
    app.router.add_get("/api/resources", resources)
    app.router.add_get("/api/resource-ownership", resource_ownership)
    app.router.add_put("/api/resource-ownership", update_resource_ownership)
    app.router.add_get("/api/style-presets", list_style_presets)
    app.router.add_post("/api/style-presets", create_style_preset)
    app.router.add_put("/api/style-presets/{preset_id}", update_style_preset)
    app.router.add_delete("/api/style-presets/{preset_id}", delete_style_preset)
    app.router.add_get("/api/prompt-rules", list_prompt_rules)
    app.router.add_post("/api/prompt-rules", create_prompt_rule)
    app.router.add_put("/api/prompt-rules/{rule_id}", update_prompt_rule)
    app.router.add_delete("/api/prompt-rules/{rule_id}", delete_prompt_rule)
    app.router.add_post("/api/prompts/normalize", normalize_prompts)
    app.router.add_get("/api/favorites/{section}", get_favorites)
    app.router.add_post("/api/favorites/{section}/reconcile", reconcile_favorites)
    app.router.add_put("/api/favorites/{section}/item", update_favorite)
    app.router.add_post("/api/favorites/{section}/text", save_favorite_text)
    app.router.add_post("/api/favorites/{section}/selection", update_favorite_selection)
    app.router.add_post("/api/favorites/{section}/groups", create_favorite_group)
    app.router.add_put("/api/favorites/{section}/groups/{group_id}", update_favorite_group)
    app.router.add_post(
        "/api/favorites/{section}/groups/{parent_id}/children/import",
        import_favorite_child_group,
    )
    app.router.add_delete("/api/favorites/{section}/groups/{group_id}", delete_favorite_group)
    app.router.add_get("/api/pools/{section}", pool)
    app.router.add_post("/api/pools/{section}/query", pool_query)
    app.router.add_post("/api/pools/{section}/ids", pool_ids)
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
    app.router.add_get("/api/artwork-folders", list_artwork_folders)
    app.router.add_post("/api/artwork-folders", create_artwork_folder)
    app.router.add_post("/api/artwork-folders/reorder", reorder_artwork_folders)
    app.router.add_post("/api/artwork-folders/members/query", read_artwork_members)
    app.router.add_post("/api/artwork-folders/members", update_artwork_members)
    app.router.add_patch("/api/artwork-folders/{folder_id}", rename_artwork_folder)
    app.router.add_delete("/api/artwork-folders/{folder_id}", delete_artwork_folder)
    app.router.add_get("/api/batches/current", current_batch)
    app.router.add_get("/api/batches/current/preview", batch_preview)
    app.router.add_post("/api/batches/{batch_id}/stop", stop_batch)
    app.router.add_delete("/api/batches/queue/{queue_id}", remove_queued_batch)
    app.router.add_get("/api/history", list_history)
    app.router.add_get(
        r"/api/history/{image_id:\d+}/regeneration-options",
        regeneration_options,
    )
    app.router.add_post(
        r"/api/history/{image_id:\d+}/regenerate", regenerate_history
    )
    app.router.add_delete(r"/api/history/{image_id:\d+}", delete_history)
    app.router.add_post(r"/api/history/{image_id:\d+}/reveal", reveal_history_image)
    app.router.add_get(r"/api/images/{image_id:\d+}", image)
    app.router.add_get(r"/api/images/{image_id:\d+}/thumbnail", thumbnail)
    app.router.add_get("/", index)
    app.router.add_get("/favicon.ico", favicon)
    app.router.add_static("/static/", root / "static", show_index=False)

    async def start_resources(_: web.Application) -> None:
        recovery_failures = await pool_lifecycle.recover_pending()
        for profile, failure in recovery_failures.items():
            logger.warning("%s 收藏一致性恢复尚未完成: %s", profile, failure["error"])

    async def cleanup_resources() -> None:
        try:
            try:
                await lora_library.close()
            finally:
                await manager.shutdown()
        finally:
            try:
                await thumbnails.close()
            finally:
                try:
                    close = getattr(client, "close", None)
                    if close:
                        result = close()
                        if asyncio.iscoroutine(result):
                            await result
                finally:
                    await history.close()

    async def cleanup(_: web.Application) -> None:
        nonlocal cleanup_task
        if cleanup_task is None:
            cleanup_task = asyncio.create_task(cleanup_resources())
        # A disconnected shutdown caller must not cancel resource cleanup.
        await asyncio.shield(cleanup_task)

    app.on_startup.append(start_resources)
    app.on_cleanup.append(cleanup)
    return app


def _setup_logging(root: Path, data_dir: Path | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        directory = data_dir if data_dir is not None else root / "data"
        directory.mkdir(parents=True, exist_ok=True)
        from logging.handlers import RotatingFileHandler
        handlers.append(RotatingFileHandler(directory / "webui.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Anima Random WebUI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8191)
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    parser.add_argument("--anima-tools-dir", default=None)
    parser.add_argument("--data-dir", default=None, help="用户数据目录；默认仍为项目 data")
    parser.add_argument("--resource-paths", default=None, help="可选的模型相对路径映射 JSON")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("WebUI 只允许监听本机地址")
    _setup_logging(APP_DIR, Path(args.data_dir) if args.data_dir else None)
    app = create_app(
        comfy=ComfyClient(args.comfy_url),
        anima_tools_dir=args.anima_tools_dir,
        data_dir=args.data_dir,
        resource_paths=json.loads(Path(args.resource_paths).read_text(encoding="utf-8")) if args.resource_paths else None,
    )
    if not args.no_browser:
        browser_host = f"[{args.host}]" if ":" in args.host else args.host
        threading.Timer(0.8, lambda: webbrowser.open(f"http://{browser_host}:{args.port}")).start()
    web.run_app(app, host=args.host, port=args.port, print=lambda message: print(message, flush=True))
