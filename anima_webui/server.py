from __future__ import annotations

import argparse
import asyncio
import json
import logging
import threading
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from aiohttp import web

from .comfy import ComfyClient, ComfyError
from .catalog import CatalogError, PromptCatalog, SECTIONS
from .custom_prompts import CustomPromptStore
from .favorites import FavoritesService, favorite_key
from .history import HistoryStore
from .lora_triggers import LoraTriggerOverrideStore
from .prompt_rules import PromptRuleStore
from .runner import BatchConflict, BatchManager
from .style_presets import StylePresetStore
from .workflow import DEFAULT_SETTINGS, WorkflowError, WorkflowTemplates


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
PROMPT_RULES_KEY: web.AppKey[Any] = web.AppKey("prompt_rules", object)
ALLOWED_HOSTNAMES_KEY: web.AppKey[frozenset[str]] = web.AppKey(
    "allowed_hostnames", frozenset
)

LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}


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
    allowed_hostnames = request.app[ALLOWED_HOSTNAMES_KEY]
    if _hostname(request.host or "") not in allowed_hostnames:
        return web.json_response({"error": "仅允许本机访问"}, status=403)
    origin = request.headers.get("Origin")
    if origin is not None and (origin == "null" or _hostname(origin) not in allowed_hostnames):
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
    lora_trigger_overrides_path: str | Path | None = None,
    prompt_replacements_path: str | Path | None = None,
    anima_tools_dir: str | Path | None = None,
    trusted_hostnames: set[str] | frozenset[str] | None = None,
) -> web.Application:
    root = Path(app_dir)
    client = comfy or ComfyClient()
    history = HistoryStore(history_path or root / "data" / "history.sqlite3")
    catalog = PromptCatalog(root, anima_tools_dir)
    custom_prompts = CustomPromptStore(
        custom_prompts_path or root / "data" / "custom_prompts.json", catalog
    )
    favorites = FavoritesService(client, catalog)
    prompt_rules = PromptRuleStore(
        prompt_replacements_path or root / "data" / "prompt_replacements.json"
    )
    lora_trigger_overrides = LoraTriggerOverrideStore(
        lora_trigger_overrides_path or root / "data" / "lora_trigger_overrides.json"
    )
    style_presets = StylePresetStore(
        style_presets_path or root / "data" / "style_presets.json", prompt_rules
    )
    templates = WorkflowTemplates.load(root / "templates")
    manager = BatchManager(templates, history, client, catalog, prompt_rules)

    startup_warnings = [
        *history.load_warnings,
        *custom_prompts.load_warnings,
        *style_presets.load_warnings,
        *lora_trigger_overrides.load_warnings,
        *prompt_rules.load_warnings,
    ]

    app = web.Application(middlewares=[error_middleware, local_only_middleware])
    normalized_trusted_hostnames = {
        hostname
        for value in trusted_hostnames or set()
        if (hostname := _hostname(value))
    }
    app[ALLOWED_HOSTNAMES_KEY] = frozenset(LOCAL_HOSTNAMES | normalized_trusted_hostnames)
    app[COMFY_KEY] = client
    app[HISTORY_KEY] = history
    app[MANAGER_KEY] = manager
    app[CATALOG_KEY] = catalog
    app[CUSTOM_PROMPTS_KEY] = custom_prompts
    app[FAVORITES_KEY] = favorites
    app[STYLE_PRESETS_KEY] = style_presets
    app[LORA_TRIGGER_OVERRIDES_KEY] = lora_trigger_overrides
    app[PROMPT_RULES_KEY] = prompt_rules

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

    async def update_favorite_selection(request: web.Request) -> web.Response:
        body = await _json_body(request)
        return web.json_response(
            await favorites.update_selection(
                request.match_info["section"], body.get("selection"), body.get("groupIds")
            )
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
        delete_mode = request.query.get("deleteMode")
        delete_items = False if delete_mode is not None else _query_bool(request, "deleteItems")
        return web.json_response(
            await custom_prompts.delete_group(
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
            await custom_prompts.commit_import(
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

    def _public_lora_item(item: dict[str, Any]) -> dict[str, Any]:
        value = dict(item)
        source_words = value.get("trigger_words") or []
        effective_words, overridden = lora_trigger_overrides.effective(
            value["filename"], source_words
        )
        value["source_trigger_words"] = source_words
        value["trigger_words"] = effective_words
        value["trigger_override"] = overridden
        if overridden:
            value["trigger_metadata_available"] = True
        preview = str(value.get("preview") or "")
        value["preview"] = (
            f"/api/loras/preview?{urlencode({'filename': value['filename']})}"
            if preview
            else ""
        )
        return value

    async def loras(_: web.Request) -> web.Response:
        inventory = await client.lora_inventory()
        public = {
            "items": [],
            "count": inventory.get("count", 0),
        }
        for item in inventory.get("items") or []:
            public["items"].append(_public_lora_item(item))
        return web.json_response(public)

    async def update_lora_triggers(request: web.Request) -> web.Response:
        body = await _json_body(request)
        item, filename = await _lora_inventory_item(body.get("filename"))
        words = await lora_trigger_overrides.set(filename, body.get("triggerWords"))
        value = _public_lora_item({**item, "filename": filename})
        value["trigger_words"] = words
        value["trigger_override"] = True
        return web.json_response(value)

    async def reset_lora_triggers(request: web.Request) -> web.Response:
        item, filename = await _lora_inventory_item(request.query.get("filename"))
        await lora_trigger_overrides.delete(filename)
        return web.json_response(_public_lora_item({**item, "filename": filename}))

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

    async def list_prompt_rules(_: web.Request) -> web.Response:
        return web.json_response(prompt_rules.list())

    async def create_prompt_rule(request: web.Request) -> web.Response:
        return web.json_response(
            await prompt_rules.create(await _json_body(request)), status=201
        )

    async def update_prompt_rule(request: web.Request) -> web.Response:
        return web.json_response(
            await prompt_rules.update(
                request.match_info["rule_id"], await _json_body(request)
            )
        )

    async def delete_prompt_rule(request: web.Request) -> web.Response:
        rule_id = request.match_info["rule_id"]
        if not await prompt_rules.delete(rule_id):
            raise KeyError(rule_id)
        return web.json_response({"deleted": True})

    async def normalize_prompts(request: web.Request) -> web.Response:
        body = await _json_body(request)
        unknown = set(body) - {"fields", "managedTriggers"}
        if unknown:
            raise WorkflowError(f"提示词规范请求包含未知参数: {', '.join(sorted(unknown))}")
        return web.json_response(
            prompt_rules.normalize_fields(
                body.get("fields"), body.get("managedTriggers", [])
            )
        )

    async def start_batch(request: web.Request) -> web.Response:
        await client.status()
        body = await _json_body(request)
        seeds = body.pop("seeds", None)  # 复现历史图片时携带固定种子,不属于 settings
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

    async def batch_preview(_: web.Request) -> web.Response:
        preview = manager.preview
        if preview is None:
            return web.Response(status=204)
        _seq, content_type, body = preview
        return web.Response(body=body, content_type=content_type, headers={"Cache-Control": "no-store"})

    async def list_history(request: web.Request) -> web.Response:
        try:
            page = int(request.query.get("page", "1"))
            limit = int(request.query.get("limit", "24"))
        except ValueError as error:
            raise WorkflowError("分页参数无效") from error
        return web.json_response(await history.list_images(page, limit))

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
        resource_issues = await manager.resource_issues(settings)
        sections: dict[str, Any] = {}
        for section in SECTIONS:
            random_enabled = bool(settings.get(f"random_{section}"))
            actual = selected.get(section) if isinstance(selected.get(section), list) else []
            sections[section] = {
                "random": random_enabled,
                "hasSnapshot": bool(actual),
                "canRedraw": random_enabled,
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
            "resourceIssues": resource_issues,
            "sections": sections,
            "warnings": warnings,
        }
        return record, context

    async def regeneration_options(request: web.Request) -> web.Response:
        _record, context = await _regeneration_context(
            int(request.match_info["image_id"])
        )
        resources_ok = not context["resourceIssues"]
        positive_ok = bool(context["positive"])
        random_sections = [
            section for section, value in context["sections"].items() if value["random"]
        ]
        return web.json_response(
            {
                "modes": {
                    "replay": {
                        "available": resources_ok and positive_ok,
                        "reasons": _regeneration_reasons(context, require_prompt=True),
                    },
                    "prompt_variant": {
                        "available": resources_ok and positive_ok,
                        "reasons": _regeneration_reasons(context, require_prompt=True),
                    },
                    "content_redraw": {
                        "available": resources_ok and bool(random_sections),
                        "reasons": _regeneration_reasons(
                            context, require_random=bool(random_sections)
                        ),
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
        require_random: bool = True,
    ) -> list[str]:
        reasons = [
            f"{item['label']} 不可用: {item['name']}"
            for item in context["resourceIssues"]
        ]
        if require_prompt and not context["positive"]:
            reasons.append("历史记录缺少最终正向提示词")
        if require_random is False:
            reasons.append("历史设置没有可重新抽取的随机维度")
        return reasons

    async def regenerate_history(request: web.Request) -> web.Response:
        await client.status()
        record, context = await _regeneration_context(
            int(request.match_info["image_id"])
        )
        body = await _json_body(request)
        unknown = set(body) - {"mode", "count", "sections"}
        if unknown:
            raise WorkflowError(
                f"再生成请求包含未知参数: {', '.join(sorted(unknown))}"
            )
        mode = body.get("mode")
        if mode not in {"replay", "prompt_variant", "content_redraw", "settings_reroll"}:
            raise WorkflowError("mode 无效")
        if context["resourceIssues"]:
            missing = "；".join(
                f"{item['label']}: {item['name']}" for item in context["resourceIssues"]
            )
            raise WorkflowError(f"历史资源不可用: {missing}")
        settings = dict(record.get("settings") or {})
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
                    if value["random"] and section not in sections and not value["hasSnapshot"]
                ]
                if missing_fixed:
                    raise WorkflowError(
                        "缺少历史实际抽取结果，请同时重抽: " + ", ".join(missing_fixed)
                    )
                fixed_selection = {
                    section: context["selected"].get(section, [])
                    for section, value in context["sections"].items()
                    if value["random"] and section not in sections
                }
                regeneration = {
                    "mode": mode,
                    "redraw_sections": sections,
                    "fixed_selection": fixed_selection,
                    "original_selection": context["selected"],
                }
        state = await manager.start(settings, seeds=seeds, regeneration=regeneration)
        return web.json_response(state, status=201)

    async def delete_history(request: web.Request) -> web.Response:
        image_id = int(request.match_info["image_id"])
        if not await history.delete_image(image_id):
            raise KeyError(image_id)
        return web.json_response({"deleted": True})

    async def image(request: web.Request) -> web.Response:
        record = await history.get_image(int(request.match_info["image_id"]))
        body, content_type = await client.image_bytes(record)
        return web.Response(body=body, content_type=content_type.split(";", 1)[0])

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
    app.router.add_get("/api/resources", resources)
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
    app.router.add_put("/api/favorites/{section}/item", update_favorite)
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
    app.router.add_get(
        r"/api/history/{image_id:\d+}/regeneration-options",
        regeneration_options,
    )
    app.router.add_post(
        r"/api/history/{image_id:\d+}/regenerate", regenerate_history
    )
    app.router.add_delete(r"/api/history/{image_id:\d+}", delete_history)
    app.router.add_get(r"/api/images/{image_id:\d+}", image)
    app.router.add_get("/", index)
    app.router.add_get("/favicon.ico", favicon)
    app.router.add_static("/static/", root / "static", show_index=False)

    async def cleanup(_: web.Application) -> None:
        manager.shutting_down = True
        manager.queue.clear()
        manager._stop_monitor()
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
        await history.close()

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
    parser.add_argument(
        "--trusted-host",
        action="append",
        default=[],
        help="额外允许的反向代理主机名；可重复指定",
    )
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("WebUI 只允许监听本机地址")
    app = create_app(
        comfy=ComfyClient(args.comfy_url),
        anima_tools_dir=args.anima_tools_dir,
        trusted_hostnames=set(args.trusted_host),
    )
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()
    web.run_app(app, host=args.host, port=args.port, print=lambda message: print(message, flush=True))
