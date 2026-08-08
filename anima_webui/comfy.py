from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any
from urllib.parse import urlencode, urlparse

import aiohttp

from .workflow import normalize_lora_path


class ComfyError(RuntimeError):
    pass


class ComfyAborted(RuntimeError):
    """等待 ComfyUI 结果时收到停止请求。不是错误,由调用方决定如何收尾。"""


def validate_comfy_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ComfyError("ComfyUI 地址必须是本机 HTTP 地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ComfyError("ComfyUI 地址格式无效")
    return value.rstrip("/")


class ComfyClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8188", poll_interval: float = 0.75):
        self.base_url = validate_comfy_url(base_url)
        self.poll_interval = poll_interval
        self.session: aiohttp.ClientSession | None = None
        self._object_info_cache: tuple[float, dict[str, Any] | None] = (0.0, None)
        self._lora_inventory_cache: tuple[float, dict[str, Any] | None] = (0.0, None)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=20, sock_read=20)
            self.session = aiohttp.ClientSession(timeout=timeout)
        return self.session

    async def close(self) -> None:
        if self.session is not None and not self.session.closed:
            await self.session.close()

    async def _json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        session = await self._get_session()
        try:
            async with session.request(method, f"{self.base_url}{path}", **kwargs) as response:
                raw = await response.text()
                try:
                    data = json.loads(raw)
                except ValueError:
                    # 非 JSON 响应(如原版 ComfyUI 对未知路由返回纯文本 404)必须归一化为
                    # ComfyError,否则 JSONDecodeError 会绕过所有 except ComfyError 的降级逻辑。
                    data = None
                if response.status >= 400:
                    message = data.get("error") if isinstance(data, dict) else raw[:200]
                    raise ComfyError(f"ComfyUI 返回 {response.status}: {message}")
                if not isinstance(data, dict):
                    raise ComfyError("ComfyUI 返回了无效数据")
                return data
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise ComfyError(f"无法连接 ComfyUI: {error}") from error

    async def status(self) -> dict[str, Any]:
        return await self._json("GET", "/system_stats")

    async def object_info(self) -> dict[str, Any]:
        # /object_info 返回可达数 MB;批次启动会连续用到两次(LoRA 清单 + 模型校验),
        # 5 秒内复用同一份结果,避免重复拉取放大启动延迟。
        loop = asyncio.get_running_loop()
        timestamp, cached = self._object_info_cache
        if cached is not None and loop.time() - timestamp < 5.0:
            return cached
        data = await self._json("GET", "/object_info")
        self._object_info_cache = (loop.time(), data)
        return data

    async def lora_inventory(self) -> dict[str, Any]:
        """Return ComfyUI LoRAs enriched by LoRA Manager and Anima Tools metadata."""
        loop = asyncio.get_running_loop()
        timestamp, cached = self._lora_inventory_cache
        if cached is not None and loop.time() - timestamp < 5.0:
            return cached

        names: list[str] = []
        source_available = False
        object_info_available = False
        try:
            object_info = await self.object_info()
            node = object_info.get("LoraLoader") or {}
            required = ((node.get("input") or {}).get("required") or {})
            choices = required.get("lora_name")
            if isinstance(choices, list) and choices and isinstance(choices[0], list):
                names = [str(value) for value in choices[0]]
                source_available = True
                object_info_available = True
        except ComfyError:
            names = []

        manager_items: list[dict[str, Any]] = []
        manager_available = False
        try:
            page = 1
            while True:
                payload = await self._json(
                    "GET",
                    f"/api/lm/loras/list?{urlencode({'page': page, 'page_size': 100})}",
                )
                values = payload.get("items") or []
                if not isinstance(values, list):
                    raise ComfyError("LoRA Manager 返回了无效清单")
                manager_items.extend(item for item in values if isinstance(item, dict))
                manager_available = True
                total_pages = payload.get("total_pages", 1)
                if isinstance(total_pages, bool) or not isinstance(total_pages, int):
                    total_pages = 1
                if page >= max(1, total_pages):
                    break
                page += 1
        except ComfyError:
            manager_items = []
            manager_available = False

        manifest_items: dict[str, dict[str, Any]] = {}
        try:
            manifest = await self._json("GET", "/anima-tools/lora/manifest")
            source_available = True
            for item in manifest.get("items") or []:
                if isinstance(item, dict) and item.get("filename"):
                    filename = str(item["filename"])
                    identity = normalize_lora_path(filename).casefold()
                    manifest_items[identity] = item
                    if not object_info_available and not any(
                        normalize_lora_path(name).casefold() == identity for name in names
                    ):
                        names.append(filename)
        except ComfyError:
            pass

        if not source_available and manager_available:
            for item in manager_items:
                file_path = str(item.get("file_path") or "").replace("\\", "/")
                basename = file_path.rsplit("/", 1)[-1]
                folder = str(item.get("folder") or "").strip("/\\")
                if basename:
                    names.append(f"{folder}/{basename}" if folder else basename)
            source_available = True

        if not source_available:
            raise ComfyError("无法读取 ComfyUI 本地 LoRA 列表")

        def manager_item_for(normalized: str) -> dict[str, Any]:
            identity = normalized.casefold()
            matches = []
            for item in manager_items:
                file_path = str(item.get("file_path") or "").replace("\\", "/")
                path_identity = file_path.casefold()
                if path_identity == identity or path_identity.endswith(f"/{identity}"):
                    matches.append(item)
                    continue
                basename = file_path.rsplit("/", 1)[-1]
                folder = str(item.get("folder") or "").strip("/\\")
                relative = f"{folder}/{basename}" if folder else basename
                if relative.casefold() == identity:
                    matches.append(item)
            return matches[0] if len(matches) == 1 else {}

        def trigger_words(metadata: dict[str, Any]) -> list[str]:
            civitai = metadata.get("civitai") or {}
            values = civitai.get("trainedWords") if isinstance(civitai, dict) else []
            if not isinstance(values, list):
                return []
            result: list[str] = []
            seen_words: set[str] = set()
            for value in values:
                if not isinstance(value, str):
                    continue
                word = value.strip()
                identity = word.casefold()
                if word and identity not in seen_words:
                    result.append(word)
                    seen_words.add(identity)
            return result

        items = []
        seen: set[str] = set()
        for filename in names:
            normalized = normalize_lora_path(filename)
            identity = normalized.casefold()
            if identity in seen:
                continue
            seen.add(identity)
            manager_metadata = manager_item_for(normalized)
            manifest_metadata = manifest_items.get(identity) or {}
            preview = str(
                manager_metadata.get("preview_url")
                or manifest_metadata.get("thumb_url")
                or ""
            )
            metadata_source = (
                "lora-manager"
                if manager_metadata
                else "anima-tools"
                if manifest_metadata
                else "comfyui"
            )
            items.append(
                {
                    "filename": filename,
                    "normalized_path": normalized,
                    "folder": normalized.rsplit("/", 1)[0] if "/" in normalized else "",
                    "basename": normalized.rsplit("/", 1)[-1],
                    "display_name": str(
                        manager_metadata.get("model_name")
                        or manifest_metadata.get("display_name")
                        or normalized.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                    ),
                    "preview": preview,
                    "has_preview": bool(preview),
                    "preview_nsfw_level": manager_metadata.get("preview_nsfw_level", 0),
                    "trigger_words": trigger_words(manager_metadata),
                    "trigger_metadata_available": bool(manager_metadata),
                    "metadata_source": metadata_source,
                    "size": manager_metadata.get("file_size") or manifest_metadata.get("size"),
                }
            )
        result = {"items": items, "count": len(items)}
        self._lora_inventory_cache = (loop.time(), result)
        return result

    async def lora_preview(self, filename: str) -> tuple[bytes, str, str]:
        normalized = normalize_lora_path(filename)
        inventory = await self.lora_inventory()
        item = next(
            (
                value
                for value in inventory.get("items") or []
                if normalize_lora_path(value.get("filename")).casefold() == normalized.casefold()
            ),
            None,
        )
        if not item or not item.get("preview"):
            raise KeyError(filename)
        preview = str(item["preview"])
        parsed = urlparse(preview)
        if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
            raise ComfyError("LoRA 预览地址无效")
        session = await self._get_session()
        try:
            async with session.get(f"{self.base_url}{preview}") as response:
                if response.status == 404:
                    raise KeyError(filename)
                if response.status >= 400:
                    raise ComfyError(f"ComfyUI 返回 {response.status}: 无法读取 LoRA 预览")
                content_type = response.headers.get("Content-Type", "application/octet-stream")
                cache_control = response.headers.get("Cache-Control", "private, max-age=300")
                return await response.read(), content_type, cache_control
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise ComfyError(f"无法连接 ComfyUI: {error}") from error

    async def lora_filenames(self) -> list[str]:
        inventory = await self.lora_inventory()
        return [str(item["filename"]) for item in inventory.get("items") or []]

    async def resource_inventory(self) -> dict[str, Any]:
        object_info = await self.object_info()

        def choices(node_name: str, input_name: str) -> list[str]:
            node = object_info.get(node_name) or {}
            required = ((node.get("input") or {}).get("required") or {})
            value = required.get(input_name)
            if isinstance(value, list) and value and isinstance(value[0], list):
                return [str(item) for item in value[0]]
            if (
                isinstance(value, list)
                and len(value) > 1
                and isinstance(value[1], dict)
                and isinstance(value[1].get("options"), list)
            ):
                return [str(item) for item in value[1]["options"]]
            return []

        models = choices("UNETLoader", "unet_name")
        upscale_models = choices("UpscaleModelLoader", "model_name") or choices("easy hiresFix", "model_name")
        if not models or not upscale_models:
            raise ComfyError("ComfyUI 未返回模型或高清修复模型列表")
        return {"models": models, "upscale_models": upscale_models}

    async def favorites(self) -> dict[str, Any]:
        return await self._json("GET", "/anima-tools/favorites")

    async def save_favorites(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._json("POST", "/anima-tools/favorites", json=payload)

    async def submit(self, payload: dict[str, Any]) -> str:
        data = await self._json("POST", "/prompt", json=payload)
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise ComfyError("ComfyUI 未返回 prompt_id")
        return str(prompt_id)

    async def progress_stream(self, client_id: str) -> AsyncIterator[dict[str, Any]]:
        """连接 ComfyUI 的 websocket,产出进度事件与预览帧。

        文本消息 → {"kind": "event", "payload": {...}}(progress/executing 等);
        二进制消息(类型 1 = 预览图,前 8 字节为类型+格式头)→
        {"kind": "preview", "format": "jpeg"|"png", "bytes": ...}。
        连接失败或断开由调用方处理重连;本方法只负责单次连接的读取。
        """
        session = await self._get_session()
        async with session.ws_connect(
            f"{self.base_url}/ws?clientId={client_id}", heartbeat=30, max_msg_size=32 * 1024 * 1024
        ) as ws:
            async for message in ws:
                if message.type == aiohttp.WSMsgType.TEXT:
                    try:
                        payload = json.loads(message.data)
                    except ValueError:
                        continue
                    if isinstance(payload, dict):
                        yield {"kind": "event", "payload": payload}
                elif message.type == aiohttp.WSMsgType.BINARY:
                    raw = message.data
                    if len(raw) >= 8 and int.from_bytes(raw[:4], "big") == 1:
                        image_format = int.from_bytes(raw[4:8], "big")
                        yield {
                            "kind": "preview",
                            "format": "png" if image_format == 2 else "jpeg",
                            "bytes": raw[8:],
                        }
                elif message.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                    break

    async def interrupt(self) -> None:
        """尽力中断 ComfyUI 当前正在执行的任务。"""
        session = await self._get_session()
        try:
            async with session.post(f"{self.base_url}/interrupt") as response:
                await response.read()
                if response.status >= 400:
                    raise ComfyError(f"ComfyUI 返回 {response.status}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise ComfyError(f"无法连接 ComfyUI: {error}") from error

    async def prompt_queued(self, prompt_id: str) -> bool:
        """检查任务是否仍在 ComfyUI 的运行/等待队列中。"""
        data = await self._json("GET", "/queue")
        for key in ("queue_running", "queue_pending"):
            for entry in data.get(key) or []:
                if isinstance(entry, (list, tuple)) and len(entry) > 1 and str(entry[1]) == prompt_id:
                    return True
        return False

    async def wait_for_history(
        self,
        prompt_id: str,
        should_abort: Callable[[], bool] | None = None,
        missing_timeout: float = 30.0,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        missing_since: float | None = None
        next_queue_check = 0.0
        queued = True
        while True:
            if should_abort is not None and should_abort():
                raise ComfyAborted("收到停止请求")
            data = await self._json("GET", f"/history/{prompt_id}")
            entry = data.get(prompt_id)
            if entry:
                status = entry.get("status") or {}
                if status.get("status_str") == "error" or status.get("completed") is False:
                    messages = status.get("messages") or []
                    raise ComfyError(f"ComfyUI 执行失败: {messages[-1] if messages else '未知错误'}")
                return entry
            # 任务既不在 history 也不在队列时(历史被清空、ComfyUI 重启丢任务),
            # 持续 missing_timeout 秒即放弃,避免批次永久卡死。
            now = loop.time()
            if now >= next_queue_check:
                queued = await self.prompt_queued(prompt_id)
                next_queue_check = now + max(self.poll_interval, 5.0)
            if queued:
                missing_since = None
            elif missing_since is None:
                missing_since = now
            elif now - missing_since >= missing_timeout:
                raise ComfyError("ComfyUI 的队列与历史中都找不到该任务,已放弃等待(历史可能被清空或 ComfyUI 已重启)")
            await asyncio.sleep(self.poll_interval)

    async def image_bytes(self, image: dict[str, Any]) -> tuple[bytes, str]:
        query = urlencode(
            {
                "filename": image["filename"],
                "subfolder": image.get("subfolder") or "",
                "type": image.get("file_type") or image.get("type") or "output",
            }
        )
        session = await self._get_session()
        try:
            async with session.get(f"{self.base_url}/view?{query}") as response:
                if response.status >= 400:
                    raise ComfyError(f"读取图片失败: HTTP {response.status}")
                return await response.read(), response.headers.get("Content-Type", "image/png")
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise ComfyError(f"读取图片失败: {error}") from error


def extract_images(history_entry: dict[str, Any]) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    for output in (history_entry.get("outputs") or {}).values():
        for image in output.get("images") or []:
            if isinstance(image, dict) and image.get("filename"):
                images.append(image)
    return images


def extract_positive_prompt(history_entry: dict[str, Any]) -> str:
    prompt_data = history_entry.get("prompt") or []
    if len(prompt_data) > 3 and isinstance(prompt_data[3], dict):
        extra = prompt_data[3].get("extra_pnginfo") or {}
        records = extra.get("anima_prompt") or {}
        if isinstance(records, dict):
            for value in records.values():
                if isinstance(value, dict) and value.get("positive"):
                    positive = str(value["positive"])
                    if "['60', 0]" not in positive and '["60", 0]' not in positive:
                        return positive

        composers = extra.get("anima_prompt_composer") or {}
        composer = composers.get("60") if isinstance(composers, dict) else None
        resolved = composer.get("resolved_prompt", "") if isinstance(composer, dict) else ""
        metadata = extra.get("anima_random_webui") or {}
        resolved_full = metadata.get("resolved_prompt_full") if isinstance(metadata, dict) else ""
        if resolved_full:
            return str(resolved_full)
        settings = metadata.get("settings") or {}
        if resolved and isinstance(settings, dict):
            parts = []
            for value in (settings.get("quality_prompt", ""),):
                parts.extend(_prompt_tokens(value))
            for artist in _prompt_tokens(settings.get("manual_artist", "")):
                clean = artist[1:].strip() if artist.startswith("@") else artist[3:].strip() if artist.lower().startswith("by ") else artist
                if clean:
                    parts.append(f"@{clean}")
            for enabled, name in (
                ("random_character", "fixed_character"),
                ("random_clothing", "fixed_clothing"),
                ("random_pose", "fixed_pose"),
                ("random_background", "fixed_background"),
            ):
                if not settings.get(enabled, True):
                    parts.extend(_prompt_tokens(settings.get(name, "")))
            parts.extend(_prompt_tokens(resolved))
            extra_tokens = _prompt_tokens(settings.get("extra_prompt", ""))
            resolved_tokens = _prompt_tokens(resolved)
            if extra_tokens and resolved_tokens[-len(extra_tokens):] != extra_tokens:
                parts.extend(extra_tokens)
            return f"{', '.join(parts)}, " if parts else ""
    if len(prompt_data) > 2 and isinstance(prompt_data[2], dict):
        composer = prompt_data[2].get("60") or {}
        resolved = (composer.get("inputs") or {}).get("resolved_prompt")
        if resolved:
            return str(resolved)
    return ""


def _prompt_tokens(value: Any) -> list[str]:
    normalized = str(value or "").replace("\r", ",").replace("\n", ",")
    return [part.replace("_raw_:", "", 1).strip() for part in normalized.split(",") if part.replace("_raw_:", "", 1).strip()]
