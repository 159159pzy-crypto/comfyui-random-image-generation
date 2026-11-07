from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any
from urllib.parse import urlencode, urlparse

import aiohttp

from .resource_classification import classify_resources
from .workflow import WorkflowError, normalize_lora_path


class ComfyError(RuntimeError):
    def __init__(self, message: str, *, node_id: str = "", node_type: str = ""):
        super().__init__(message)
        self.node_id = node_id
        self.node_type = node_type


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
        self._object_info_generation = 0
        self._object_info_pending: dict[int, asyncio.Task[dict[str, Any]]] = {}
        self._classification_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._classification_locks = {kind: asyncio.Lock() for kind in ("models", "loras")}

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=20, sock_read=20)
            self.session = aiohttp.ClientSession(timeout=timeout)
        return self.session

    async def close(self) -> None:
        pending = list(self._object_info_pending.values())
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
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
        generation = self._object_info_generation
        task = self._object_info_pending.get(generation)
        if task is None:
            async def fetch() -> dict[str, Any]:
                try:
                    data = await self._json("GET", "/object_info")
                    if generation == self._object_info_generation:
                        self._object_info_cache = (loop.time(), data)
                    return data
                finally:
                    self._object_info_pending.pop(generation, None)

            task = asyncio.create_task(fetch())
            self._object_info_pending[generation] = task
            # A cancelled HTTP waiter must neither cancel the shared request
            # nor leave an unobserved exception if every waiter disconnects.
            task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return await asyncio.shield(task)

    async def _manager_inventory(self, prefix: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        expected_pages: int | None = None
        expected_total: int | None = None
        seen_pages: set[str] = set()
        page = 1
        while True:
            payload = await self._json(
                "GET", f"/api/lm/{prefix}/list?{urlencode({'page': page, 'page_size': 100})}"
            )
            values = payload.get("items")
            total_pages = payload.get("total_pages")
            if (
                not isinstance(values, list)
                or not all(isinstance(value, dict) for value in values)
                or isinstance(total_pages, bool)
                or not isinstance(total_pages, int)
                or total_pages < 0
                or total_pages == 0 and values
                or total_pages > 1 and not values
                or len(values) > 100
            ):
                raise ComfyError("LoRA Manager 返回了无效的分页清单")
            if "page" in payload and (type(payload["page"]) is not int or payload["page"] != page):
                raise ComfyError("LoRA Manager 返回的页码不一致")
            if "page_size" in payload and (type(payload["page_size"]) is not int or payload["page_size"] != 100):
                raise ComfyError("LoRA Manager 返回的分页大小不一致")
            if expected_pages is not None and total_pages != expected_pages:
                raise ComfyError("LoRA Manager 清单在分页期间发生变化，请刷新重试")
            expected_pages = total_pages
            page_identity = json.dumps(values, sort_keys=True)
            if values and page_identity in seen_pages:
                raise ComfyError("LoRA Manager 返回了重复分页，无法确认完整清单")
            seen_pages.add(page_identity)
            if "total" in payload:
                total = payload["total"]
                if type(total) is not int or total < 0:
                    raise ComfyError("LoRA Manager 返回了无效的模型总数")
                if expected_total is not None and total != expected_total:
                    raise ComfyError("LoRA Manager 清单在分页期间发生变化，请刷新重试")
                expected_total = total
            items.extend(values)
            if page >= max(1, total_pages):
                if expected_total is not None and len(items) != expected_total:
                    raise ComfyError("LoRA Manager 返回的清单不完整，请在插件中检查并刷新")
                return items
            page += 1

    async def resource_classifications(self, kind: str, *, refresh: bool = False) -> dict[str, Any]:
        if kind not in {"models", "loras"}:
            raise ValueError("资源类型必须是 models 或 loras")
        async with self._classification_locks[kind]:
            loop = asyncio.get_running_loop()
            if refresh:
                self._object_info_generation += 1
                self._object_info_cache = (0.0, None)
                self._classification_cache.pop(kind, None)
            cached = self._classification_cache.get(kind)
            if cached is not None and loop.time() - cached[0] < 5.0:
                return cached[1]

            object_info = await self.object_info()
            node_name, input_name = ("UNETLoader", "unet_name") if kind == "models" else ("LoraLoader", "lora_name")
            node = object_info.get(node_name)
            inputs = node.get("input") if isinstance(node, dict) else None
            required = inputs.get("required") if isinstance(inputs, dict) else None
            choices = required.get(input_name) if isinstance(required, dict) else None
            names = None
            if isinstance(choices, list) and choices and isinstance(choices[0], list):
                names = choices[0]
            elif isinstance(choices, list) and len(choices) > 1 and isinstance(choices[1], dict):
                names = choices[1].get("options")
            if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
                raise ComfyError(f"ComfyUI 未返回有效的 {node_name} 资源列表")

            prefix = "checkpoints" if kind == "models" else "loras"
            roots_path = "/api/lm/checkpoints/unet_roots" if kind == "models" else "/api/lm/loras/roots"
            try:
                roots_data = await self._json("GET", roots_path)
                roots = roots_data.get("roots")
                if roots_data.get("success") is not True or not isinstance(roots, list):
                    raise ComfyError("LoRA Manager 返回了无效的模型根目录")
                manager_items = await self._manager_inventory(prefix)
                items = classify_resources(names, manager_items, roots)
            except (ComfyError, ValueError, WorkflowError) as error:
                raise ComfyError(f"LoRA Manager 分类读取失败: {error}；请确认插件已启用并完成扫描") from error
            result = {
                "items": items,
                "count": len(items),
                "source": "lora-manager",
                "manager_url": f"{self.base_url}/{prefix}",
                "roots": roots,
                "manager_items": manager_items,
            }
            self._classification_cache[kind] = (loop.time(), result)
            return result

    async def lora_inventory(self, *, refresh: bool = False) -> dict[str, Any]:
        return await self.resource_classifications("loras", refresh=refresh)


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
        return await self.lora_preview_asset(str(item["preview"]))

    async def lora_preview_asset(self, preview: str) -> tuple[bytes, str, str]:
        """Proxy only bounded raster/video assets from the configured local plugin."""
        parsed = urlparse(preview)
        if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
            raise ComfyError("LoRA 预览地址无效")
        session = await self._get_session()
        try:
            async with session.get(f"{self.base_url}{preview}", allow_redirects=False) as response:
                if response.status == 404:
                    raise KeyError(preview)
                if response.status >= 300:
                    raise ComfyError(f"ComfyUI 返回 {response.status}: 无法读取 LoRA 预览")
                content_type = response.headers.get("Content-Type", "application/octet-stream")
                media_type = content_type.split(";", 1)[0].strip().lower()
                if media_type not in {"image/png", "image/jpeg", "image/webp", "image/gif", "image/avif", "video/mp4", "video/webm", "application/octet-stream"}:
                    raise ComfyError("LoRA 预览格式不支持")
                limit = 32 * 1024 * 1024
                if response.content_length is not None and response.content_length > limit:
                    raise ComfyError("LoRA 预览超过 32 MiB 限制")
                chunks, size = [], 0
                if media_type == "application/octet-stream":
                    # Some Windows plugin installations serve WebP as generic binary.
                    # Inspect only a bounded prefix; never infer an active type from a filename.
                    try:
                        prefix = await response.content.readexactly(16)
                    except asyncio.IncompleteReadError as error:
                        prefix = error.partial
                    if len(prefix) < 16:
                        raise ComfyError("LoRA 预览格式不支持")
                    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
                        content_type = "image/png"
                    elif prefix.startswith(b"\xff\xd8\xff"):
                        content_type = "image/jpeg"
                    elif prefix.startswith((b"GIF87a", b"GIF89a")):
                        content_type = "image/gif"
                    elif len(prefix) == 16 and prefix[:4] == b"RIFF" and prefix[8:12] == b"WEBP" and prefix[12:16] in {b"VP8 ", b"VP8L", b"VP8X"}:
                        content_type = "image/webp"
                    else:
                        raise ComfyError("LoRA 预览格式不支持")
                    chunks.append(prefix)
                    size = len(prefix)
                async for chunk in response.content.iter_chunked(64 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise ComfyError("LoRA 预览超过 32 MiB 限制")
                    chunks.append(chunk)
                cache_control = response.headers.get("Cache-Control", "private, max-age=300")
                return b"".join(chunks), content_type, cache_control
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
        clips = choices("CLIPLoader", "clip_name")
        clip_types = choices("CLIPLoader", "type")
        vaes = choices("VAELoader", "vae_name")
        upscale_models = choices("UpscaleModelLoader", "model_name") or choices("easy hiresFix", "model_name")
        samplers = choices("KSampler", "sampler_name")
        schedulers = choices("KSampler", "scheduler")
        if not models or not samplers or not schedulers:
            raise ComfyError("ComfyUI 未返回模型或采样器列表")
        node_names = set(object_info)
        seedvr_nodes = sorted(
            name for name in node_names if "seedvr" in name.casefold()
        )
        nvidia_nodes = sorted(
            name
            for name in node_names
            if name == "RTXVideoSuperResolution"
            or any(token in name.casefold() for token in ("dlss", "vsr", "nvidia"))
        )
        face_node = "ForbiddenVisionFaceProcessorIntegrated"
        seedvr2_models = [
            name
            for name in models
            if "seedvr2" in name.casefold() or "seedvr" in name.casefold()
        ]
        seedvr2_vaes = [
            name
            for name in vaes
            if "seedvr" in name.casefold() or "ema_vae" in name.casefold()
        ]
        result = {
            "models": models,
            "clips": clips,
            "clip_types": clip_types,
            "vaes": vaes,
            "seedvr2_models": seedvr2_models,
            "seedvr2_vaes": seedvr2_vaes,
            "upscale_models": upscale_models,
            "samplers": samplers,
            "schedulers": schedulers,
            "nodes": {
                "UNETLoader": "UNETLoader" in node_names,
                "CLIPLoader": "CLIPLoader" in node_names,
                "VAELoader": "VAELoader" in node_names,
                "SeedVR2": bool(seedvr_nodes),
                "NvidiaUpscale": "RTXVideoSuperResolution" in node_names,
                "RTXVideoSuperResolution": "RTXVideoSuperResolution" in node_names,
                "DLSS5NeuralRender": "DLSS5NeuralRender" in node_names,
                "UpscaleModelLoader": "UpscaleModelLoader" in node_names,
                "ImageUpscaleWithModel": "ImageUpscaleWithModel" in node_names,
                "ForbiddenVisionFaceProcessorIntegrated": face_node in node_names,
            },
            "plugins": {
                "seedvr2": bool(seedvr_nodes),
                "nvidia_upscale": "RTXVideoSuperResolution" in node_names,
                "dlss": "DLSS5NeuralRender" in node_names,
                "pixel_hires": {"UpscaleModelLoader", "ImageUpscaleWithModel"}.issubset(node_names),
                "forbidden_vision_face": face_node in node_names,
            },
            "node_inputs": {
                name: (object_info[name].get("input") or {}).get("required", {})
                for name in ("UNETLoader", "CLIPLoader", "VAELoader", "KSampler", "KSamplerAdvanced",
                             "VAEDecodeTiled", "VAEEncodeTiled", "VAEDecode", "VAEEncode", "LoadImage",
                             "SesquiLatentUpscale", "LatentUpscaleBy", "DLSS5NeuralRender",
                             "ForbiddenVisionFaceProcessorIntegrated", "RTXVideoSuperResolution", "SeedVR2PostProcessing",
                             "UpscaleModelLoader", "ImageUpscaleWithModel", "ImageScale", "ImageScaleBy",
                             "PrimitiveInt", "ComfyMathExpression")
                if name in object_info
            },
            "node_types": sorted(node_names),
        }
        # Preserve the compact inventory shape used by stock/legacy test
        # clients which do not expose CLIP/VAE or optional plugin nodes.  A
        # modern ComfyUI advertises those nodes, so the extended v2 contract
        # is returned automatically there.
        if "CLIPLoader" not in node_names and "VAELoader" not in node_names and not seedvr_nodes and not nvidia_nodes and face_node not in node_names:
            return {
                "models": models,
                "upscale_models": upscale_models,
                "samplers": samplers,
                "schedulers": schedulers,
            }
        return result



    async def submit(self, payload: dict[str, Any]) -> str:
        data = await self._json("POST", "/prompt", json=payload)
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise ComfyError("ComfyUI 未返回 prompt_id")
        return str(prompt_id)


    async def cancel_prompt(self, prompt_id: str) -> None:
        """Cancel only our own Comfy job, including jobs waiting behind others."""
        try:
            await self._cancel_prompt(prompt_id)
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise ComfyError(f"取消任务失败: {error}") from error

    async def _cancel_prompt(self, prompt_id: str) -> None:
        session = await self._get_session()
        async with session.post(f"{self.base_url}/api/jobs/{prompt_id}/cancel") as response:
            if response.status not in {404, 405}:
                if response.status >= 400:
                    raise ComfyError(f"取消任务失败: HTTP {response.status}")
                await response.read()
                return
        # Legacy /interrupt implementations ignore the request body and stop
        # whichever job is running, even when a prompt_id is supplied. Only
        # deleting this id from the pending queue is safe without the job API.
        async with session.post(
            f"{self.base_url}/queue", json={"delete": [prompt_id]}
        ) as response:
            await response.read()
            if response.status >= 400:
                raise ComfyError(f"取消任务失败: HTTP {response.status}")
        if await self.prompt_queued(prompt_id):
            raise ComfyError(
                "当前 ComfyUI 不支持安全的按任务取消；已移除等待项，"
                "远端正在执行的任务可能继续，请在 ComfyUI 中手动停止"
            )

    async def progress_stream(self, client_id: str) -> AsyncIterator[dict[str, Any]]:
        """连接 ComfyUI 的 websocket,产出进度事件与预览帧。

        文本消息 → {"kind": "event", "payload": {...}}(progress/executing 等);
        二进制类型 4 携带 prompt/node 元数据;旧服务的类型 1 仍兼容。
        帧使用同一个 preview 结构,新协议额外提供 prompt_id/node_id。
        连接失败或断开由调用方处理重连;本方法只负责单次连接的读取。
        """
        session = await self._get_session()
        async with session.ws_connect(
            f"{self.base_url}/ws?clientId={client_id}", heartbeat=30, max_msg_size=32 * 1024 * 1024
        ) as ws:
            await ws.send_json({"type": "feature_flags", "data": {"supports_preview_metadata": True}})
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
                    if len(raw) <= 8:
                        continue
                    event_type = int.from_bytes(raw[:4], "big")
                    if event_type == 1:
                        image_format = int.from_bytes(raw[4:8], "big")
                        if image_format not in (1, 2):
                            continue
                        yield {
                            "kind": "preview",
                            "format": "png" if image_format == 2 else "jpeg",
                            "bytes": raw[8:],
                        }
                    elif event_type == 4:
                        metadata_size = int.from_bytes(raw[4:8], "big")
                        if not 0 < metadata_size <= 65536 or 8 + metadata_size >= len(raw):
                            continue
                        try:
                            metadata = json.loads(raw[8:8 + metadata_size])
                        except (ValueError, UnicodeDecodeError):
                            continue
                        if not isinstance(metadata, dict):
                            continue
                        prompt_id, node_id = metadata.get("prompt_id"), metadata.get("node_id")
                        image_type = metadata.get("image_type")
                        if not isinstance(prompt_id, str) or not prompt_id or not isinstance(node_id, str) or not node_id:
                            continue
                        if image_type not in ("image/jpeg", "image/png"):
                            continue
                        yield {
                            "kind": "preview", "format": image_type.removeprefix("image/"),
                            "bytes": raw[8 + metadata_size:], "prompt_id": prompt_id,
                            "node_id": node_id,
                            "display_node_id": metadata.get("display_node_id"),
                            "parent_node_id": metadata.get("parent_node_id"),
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
                    detail = "未知错误"
                    failed_node_id = failed_node_type = ""
                    for message in reversed(messages):
                        if isinstance(message, (list, tuple)) and len(message) > 1 and isinstance(message[1], dict):
                            data = message[1]
                            if message[0] == "execution_interrupted":
                                detail = "任务已中断"
                                break
                            if data.get("exception_message"):
                                failed_node_id = str(data.get("node_id") or "")
                                failed_node_type = str(data.get("node_type") or "")
                                node = str(data.get("node_type") or data.get("node_id") or "节点")
                                summary = str(data["exception_message"]).strip().splitlines()[0][:600]
                                detail = f"{node}: {summary}"
                                break
                        elif isinstance(message, str):
                            detail = message[:600]
                    raise ComfyError(f"ComfyUI 执行失败: {detail}", node_id=failed_node_id, node_type=failed_node_type)
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
            # PreviewImage and similar nodes are marked ``temp`` in ComfyUI
            # history. They are not generated gallery assets and must not be
            # persisted as additional results for one batch item.
            if (
                isinstance(image, dict)
                and image.get("filename")
                and image.get("type", "output") == "output"
            ):
                images.append(image)
    return images


def extract_positive_prompt(history_entry: dict[str, Any]) -> str:
    prompt_data = history_entry.get("prompt") or []
    if len(prompt_data) > 2 and isinstance(prompt_data[2], dict):
        positive = prompt_data[2].get("42") or {}
        if positive.get("class_type") == "CLIPTextEncode":
            text = (positive.get("inputs") or {}).get("text")
            if isinstance(text, str):
                return text
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
