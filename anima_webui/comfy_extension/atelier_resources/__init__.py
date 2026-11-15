"""ComfyUI extension for Atelier resource discovery and idle memory cleanup."""
from __future__ import annotations

import asyncio
import logging

from .inspector import inspect_inventory, inspect_lora_files
from .memory_cleanup import CleanupUnavailable, cleanup_if_idle

logger = logging.getLogger(__name__)

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}


def register_routes() -> None:
    import folder_paths
    import nodes
    from aiohttp import web
    from comfy import model_management
    from server import PromptServer
    from .capabilities import inspect_capabilities, probe_native

    @PromptServer.instance.routes.post("/anima/memory/cleanup")
    async def cleanup_memory(request):
        try:
            completed = await asyncio.to_thread(
                cleanup_if_idle, PromptServer.instance.prompt_queue, model_management.soft_empty_cache,
            )
        except CleanupUnavailable:
            return web.json_response({"error": "当前 ComfyUI 不支持安全清理，请更新并重启 ComfyUI"}, status=501)
        except Exception:
            logger.exception("ComfyUI 清缓存和垃圾回收失败")
            return web.json_response({"error": "清缓存和垃圾回收失败"}, status=500)
        if not completed:
            return web.json_response({"error": "ComfyUI 正忙，已跳过清缓存和垃圾回收"}, status=409)
        return web.json_response({"status": "completed"})

    @PromptServer.instance.routes.get("/anima/resources/inspect")
    async def inspect(request):
        requested = request.query.get("probe", "")
        modules = list(dict.fromkeys(value for value in requested.split(",") if value))
        if set(modules) - {"nvidia", "dlss"}:
            return web.json_response({"error": "未知能力检查"}, status=400)
        items = await asyncio.to_thread(inspect_inventory, folder_paths)
        lora_files = await asyncio.to_thread(inspect_lora_files, folder_paths)
        capabilities, context = await asyncio.to_thread(inspect_capabilities, folder_paths, nodes)
        if modules:
            running, _ = PromptServer.instance.prompt_queue.get_current_queue()
            if not running:
                try:
                    await asyncio.wait_for(probe_native(modules, capabilities, context), 18)
                except asyncio.TimeoutError:
                    for module in modules:
                        key = "nvidia.vsr" if module == "nvidia" else "nvidia.dlss"
                        if capabilities.get(key, {}).get("status") == "unknown":
                            capabilities[key]["message"] = "原生运行库检查超时，请稍后重试"
            else:
                for module in modules:
                    key = "nvidia.vsr" if module == "nvidia" else "nvidia.dlss"
                    if capabilities.get(key, {}).get("status") == "unknown":
                        capabilities[key]["message"] = "正在生成，原生能力验证将在空闲时进行"
        return web.json_response({"version": 1, "supported": True, "items": items,
                                  "lora_files": lora_files, "capabilities": capabilities})


# Importing the pure inspector in tests does not require a running ComfyUI.
try:
    import folder_paths as _folder_paths  # noqa: F401
except ImportError:
    pass
else:
    register_routes()
