from __future__ import annotations

import argparse
import asyncio
import json
import threading
import webbrowser
from pathlib import Path
from typing import Any

from aiohttp import web

from .comfy import ComfyClient, ComfyError
from .catalog import CatalogError, PromptCatalog, SECTIONS
from .custom_prompts import CustomPromptStore
from .favorites import FavoritesService, favorite_key
from .history import HistoryStore
from .runner import BatchConflict, BatchManager
from .workflow import DEFAULT_SETTINGS, WorkflowError, WorkflowTemplates


APP_DIR = Path(__file__).resolve().parents[1]


@web.middleware
async def error_middleware(request: web.Request, handler: Any) -> web.StreamResponse:
    try:
        return await handler(request)
    except WorkflowError as error:
        return web.json_response({"error": str(error)}, status=400)
    except CatalogError as error:
        return web.json_response({"error": str(error)}, status=400)
    except BatchConflict as error:
        return web.json_response({"error": str(error)}, status=409)
    except KeyError:
        return web.json_response({"error": "记录不存在"}, status=404)
    except ComfyError as error:
        return web.json_response({"error": str(error)}, status=503)
    except json.JSONDecodeError:
        return web.json_response({"error": "请求 JSON 无效"}, status=400)


async def _json_body(request: web.Request) -> dict[str, Any]:
    try:
        value = await request.json()
    except Exception as error:
        raise WorkflowError("请求 JSON 无效") from error
    if not isinstance(value, dict):
        raise WorkflowError("请求内容必须是对象")
    return value


def create_app(
    *,
    app_dir: str | Path = APP_DIR,
    comfy: Any | None = None,
    history_path: str | Path | None = None,
    custom_prompts_path: str | Path | None = None,
    anima_tools_dir: str | Path | None = None,
) -> web.Application:
    root = Path(app_dir)
    client = comfy or ComfyClient()
    history = HistoryStore(history_path or root / "data" / "history.sqlite3")
    catalog = PromptCatalog(root, anima_tools_dir)
    custom_prompts = CustomPromptStore(
        custom_prompts_path or root / "data" / "custom_prompts.json", catalog
    )
    favorites = FavoritesService(client, catalog)
    templates = WorkflowTemplates.load(root / "templates")
    manager = BatchManager(templates, history, client, catalog)

    app = web.Application(middlewares=[error_middleware])
    app["comfy"] = client
    app["history"] = history
    app["manager"] = manager
    app["catalog"] = catalog
    app["custom_prompts"] = custom_prompts
    app["favorites"] = favorites

    async def favorite_keys(section: str, collection: str = "") -> set[str] | None:
        if not collection:
            return None
        payload = await favorites.get(section)
        return {
            favorite_key(section, item)
            for item in payload["items"]
            if (collection == "__all__" and item.get("groupIds"))
            or collection in (item.get("groupIds") or [])
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
            }
        )

    async def pool(request: web.Request) -> web.Response:
        section = request.match_info["section"]
        try:
            page = int(request.query.get("page", "1"))
            limit = int(request.query.get("limit", "48"))
        except ValueError as error:
            raise WorkflowError("分页参数无效") from error
        section = request.match_info["section"]
        collection = request.query.get("collection", "")
        sort = request.query.get("sort", "")
        keys = await favorite_keys(section, collection or ("__all__" if sort == "favorite-first" else ""))
        result = catalog.search(
            section,
            query=request.query.get("q", ""),
            categories=request.query.getall("category", []),
            traits=request.query.getall("trait", []),
            gender=request.query.get("gender", ""),
            hair=request.query.get("hair", ""),
            eye=request.query.get("eye", ""),
            series=request.query.get("series", ""),
            custom_group=request.query.get("custom_group", ""),
            favorite_keys=keys,
            favorites_only=bool(collection),
            sort=sort,
            page=page,
            limit=limit,
        )
        return web.json_response(result)

    async def pool_query(request: web.Request) -> web.Response:
        section = request.match_info["section"]
        body = await _json_body(request)
        try:
            page = int(body.get("page", 1))
            limit = int(body.get("limit", 48))
        except (TypeError, ValueError) as error:
            raise WorkflowError("分页参数无效") from error
        collection = str(body.get("collection") or "")
        sort = str(body.get("sort") or "")
        keys = await favorite_keys(section, collection or ("__all__" if sort == "favorite-first" else ""))
        result = catalog.search(
            section,
            query=str(body.get("q", "")),
            categories=body.get("categories") if isinstance(body.get("categories"), list) else [],
            traits=body.get("traits") if isinstance(body.get("traits"), list) else [],
            gender=str(body.get("gender") or ""),
            hair=str(body.get("hair") or ""),
            eye=str(body.get("eye") or ""),
            series=str(body.get("series") or ""),
            custom_group=str(body.get("custom_group") or ""),
            favorite_keys=keys,
            favorites_only=bool(collection),
            sort=sort,
            page=page,
            limit=limit,
            selection=body.get("selection") if isinstance(body.get("selection"), dict) else None,
        )
        return web.json_response(result)

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

    async def delete_favorite_group(request: web.Request) -> web.Response:
        return web.json_response(
            await favorites.delete_group(request.match_info["section"], request.match_info["group_id"])
        )

    async def list_custom_prompts(request: web.Request) -> web.Response:
        section = request.query.get("section") or None
        return web.json_response({"items": custom_prompts.list(section)})

    async def create_custom_prompt(request: web.Request) -> web.Response:
        return web.json_response(custom_prompts.create(await _json_body(request)), status=201)

    async def update_custom_prompt(request: web.Request) -> web.Response:
        item = custom_prompts.update(request.match_info["item_id"], await _json_body(request))
        try:
            await favorites.sync_custom(item["section"], item)
        except ComfyError:
            pass
        return web.json_response(item)

    async def delete_custom_prompt(request: web.Request) -> web.Response:
        if not custom_prompts.delete(request.match_info["item_id"]):
            raise KeyError(request.match_info["item_id"])
        return web.json_response({"deleted": True})

    async def list_custom_groups(request: web.Request) -> web.Response:
        return web.json_response(custom_prompts.list_groups(request.match_info["section"]))

    async def create_custom_group(request: web.Request) -> web.Response:
        return web.json_response(
            custom_prompts.create_group(request.match_info["section"], await _json_body(request)),
            status=201,
        )

    async def update_custom_group(request: web.Request) -> web.Response:
        return web.json_response(
            custom_prompts.update_group(
                request.match_info["section"], request.match_info["group_id"], await _json_body(request)
            )
        )

    async def delete_custom_group(request: web.Request) -> web.Response:
        return web.json_response(
            custom_prompts.delete_group(request.match_info["section"], request.match_info["group_id"])
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
            custom_prompts.commit_import(
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

    async def start_batch(request: web.Request) -> web.Response:
        await client.status()
        state = await manager.start(await _json_body(request))
        return web.json_response(state, status=201)

    async def current_batch(_: web.Request) -> web.Response:
        return web.json_response({"batch": manager.snapshot()})

    async def stop_batch(request: web.Request) -> web.Response:
        state = await manager.request_stop(request.match_info["batch_id"])
        return web.json_response(state)

    async def list_history(request: web.Request) -> web.Response:
        try:
            page = int(request.query.get("page", "1"))
            limit = int(request.query.get("limit", "24"))
        except ValueError as error:
            raise WorkflowError("分页参数无效") from error
        return web.json_response(history.list_images(page, limit))

    async def delete_history(request: web.Request) -> web.Response:
        image_id = int(request.match_info["image_id"])
        if not history.delete_image(image_id):
            raise KeyError(image_id)
        return web.json_response({"deleted": True})

    async def image(request: web.Request) -> web.Response:
        record = history.get_image(int(request.match_info["image_id"]))
        body, content_type = await client.image_bytes(record)
        return web.Response(body=body, content_type=content_type.split(";", 1)[0])

    async def index(_: web.Request) -> web.FileResponse:
        return web.FileResponse(root / "static" / "index.html")

    async def favicon(_: web.Request) -> web.Response:
        return web.Response(status=204)

    app.router.add_get("/api/config", config)
    app.router.add_get("/api/status", status)
    app.router.add_get("/api/loras", loras)
    app.router.add_get("/api/favorites/{section}", get_favorites)
    app.router.add_put("/api/favorites/{section}/item", update_favorite)
    app.router.add_post("/api/favorites/{section}/groups", create_favorite_group)
    app.router.add_put("/api/favorites/{section}/groups/{group_id}", update_favorite_group)
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
    app.router.add_post("/api/batches/{batch_id}/stop", stop_batch)
    app.router.add_get("/api/history", list_history)
    app.router.add_delete("/api/history/{image_id}", delete_history)
    app.router.add_get("/api/images/{image_id}", image)
    app.router.add_get("/", index)
    app.router.add_get("/favicon.ico", favicon)
    app.router.add_static("/static/", root / "static", show_index=False)

    async def cleanup(_: web.Application) -> None:
        if manager.task and not manager.task.done():
            manager.stop_requested = True
            try:
                await asyncio.wait_for(manager.task, timeout=2)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                manager.task.cancel()
        close = getattr(client, "close", None)
        if close:
            result = close()
            if asyncio.iscoroutine(result):
                await result
        history.close()

    app.on_cleanup.append(cleanup)
    return app


def main() -> None:
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
