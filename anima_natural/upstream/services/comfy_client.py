"""
AstrBot Comfy Anima 插件 v1.2.0

功能描述：
- 调用 ComfyUI HTTP API
- 轮询任务、提取并下载生成图片

作者: Yen
版本: 1.2.0
日期: 2026-07-14
"""

import asyncio
import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlparse

import aiohttp

from ..models import ImageReference, PluginSettings, UploadedImageReference


class ComfyClientError(RuntimeError):
    """ComfyUI 请求或生成失败，并区分用户提示与日志详情。"""

    def __init__(self, user_message: str, detail: str = ""):
        self.user_message = user_message
        self.detail = detail
        super().__init__(detail or user_message)


class ComfyClient:
    """可复用连接的异步 ComfyUI 客户端。"""

    def __init__(self, settings: PluginSettings):
        self._settings = settings
        self._base_url = settings.comfyui_url.rstrip("/")
        parsed = urlparse(self._base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("comfyui_url 必须是有效的 http/https 地址")
        self._session: Optional[aiohttp.ClientSession] = None
        self._client_id = str(uuid.uuid4())

    async def _get_session(self) -> aiohttp.ClientSession:
        """延迟创建并复用 HTTP 会话。"""
        if self._session is None or self._session.closed:
            headers = {}
            if self._settings.api_token:
                headers["Authorization"] = f"Bearer {self._settings.api_token}"
            timeout = aiohttp.ClientTimeout(total=self._settings.request_timeout)
            self._session = aiohttp.ClientSession(timeout=timeout, headers=headers)
        return self._session

    async def _request_json(
        self, method: str, path: str, json_body: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """发送请求并读取 JSON，隐藏服务端冗长错误正文。"""
        session = await self._get_session()
        url = f"{self._base_url}{path}"
        try:
            async with session.request(method, url, json=json_body) as response:
                response_text = await response.text()
                if response.status >= 400:
                    detail = response_text[:1000]
                    raise ComfyClientError(
                        f"ComfyUI 拒绝了请求（HTTP {response.status}）",
                        f"ComfyUI HTTP {response.status}: {detail}",
                    )
                if not response_text.strip():
                    return {}
                try:
                    payload = json.loads(response_text)
                except json.JSONDecodeError as exc:
                    raise ComfyClientError(
                        "ComfyUI 返回了无法解析的数据",
                        f"ComfyUI JSON 解析失败: {exc}; body={response_text[:500]}",
                    ) from exc
        except asyncio.TimeoutError as exc:
            raise ComfyClientError("连接 ComfyUI 超时") from exc
        except aiohttp.ClientError as exc:
            raise ComfyClientError(f"无法连接 ComfyUI: {exc}") from exc
        if not isinstance(payload, dict):
            raise ComfyClientError("ComfyUI 返回了非对象 JSON")
        return payload

    async def health(self) -> dict[str, Any]:
        """获取 ComfyUI 系统状态。"""
        return await self._request_json("GET", "/system_stats")

    async def object_info(self, node_type: str = "") -> dict[str, Any]:
        """Return registered ComfyUI node definitions for dependency checks."""

        value = str(node_type or "").strip()
        path = f"/object_info/{quote(value, safe='')}" if value else "/object_info"
        return await self._request_json("GET", path)

    @staticmethod
    async def _read_bounded_content(
        response: aiohttp.ClientResponse,
        maximum_bytes: int,
    ) -> bytes:
        """Read a complete streamed body while enforcing a hard size limit.

        ``StreamReader.read(n)`` may return fewer than ``n`` bytes before EOF.
        Calling it once can therefore truncate chunked JSON responses from
        extensions such as Danbooru Gallery.
        """

        content_length = response.content_length
        if content_length is not None and content_length > maximum_bytes:
            raise ComfyClientError("Danbooru 角色外观证据响应过大")
        content = bytearray()
        async for chunk in response.content.iter_chunked(64 * 1024):
            content.extend(chunk)
            if len(content) > maximum_bytes:
                raise ComfyClientError("Danbooru 角色外观证据响应过大")
        return bytes(content)

    async def danbooru_character_posts(
        self,
        canonical_tag: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return bounded safe-rated post metadata through Danbooru Gallery.

        This endpoint is optional.  It is used only to derive stable appearance
        evidence for an already exact-verified character; images and raw posts
        are never persisted by the AstrBot plugin.
        """

        canonical = str(canonical_tag or "").strip().casefold()
        if not re.fullmatch(r"[a-z0-9_().!'&+:/\-]{1,160}", canonical):
            raise ComfyClientError("角色 canonical Tag 无法用于外观证据查询")
        bounded_limit = min(100, max(12, int(limit)))
        session = await self._get_session()
        params = {
            "search[tags]": f"{canonical} solo",
            "search[rating]": "g",
            "limit": str(bounded_limit),
            "page": "1",
            "source": "danbooru",
        }
        try:
            async with session.get(
                f"{self._base_url}/danbooru_gallery/posts",
                params=params,
            ) as response:
                if response.status >= 400:
                    raise ComfyClientError(
                        "Danbooru 角色外观证据接口不可用",
                        f"Danbooru Gallery HTTP {response.status}",
                    )
                maximum_bytes = 12 * 1024 * 1024
                content = await self._read_bounded_content(response, maximum_bytes)
        except asyncio.TimeoutError as exc:
            raise ComfyClientError("读取 Danbooru 角色外观证据超时") from exc
        except aiohttp.ClientError as exc:
            raise ComfyClientError("无法读取 Danbooru 角色外观证据", str(exc)) from exc
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ComfyClientError("Danbooru 角色外观证据格式无效") from exc
        if not isinstance(payload, list):
            raise ComfyClientError("Danbooru 角色外观证据不是列表")
        return [item for item in payload[:bounded_limit] if isinstance(item, dict)]

    async def danbooru_character_autocomplete(
        self,
        query: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return bounded Gallery autocomplete rows for exact verification."""

        normalized = str(query or "").strip().casefold()
        if not re.fullmatch(r"[a-z0-9_().!'&+:/\-]{1,160}", normalized):
            raise ComfyClientError("角色候选 Tag 无法用于画廊精确查询")
        session = await self._get_session()
        try:
            async with session.get(
                f"{self._base_url}/danbooru_gallery/autocomplete",
                params={"query": normalized, "limit": str(min(50, max(1, limit)))},
            ) as response:
                if response.status >= 400:
                    raise ComfyClientError(
                        "Danbooru 角色候选接口不可用",
                        f"Danbooru Gallery HTTP {response.status}",
                    )
                content = await self._read_bounded_content(response, 2 * 1024 * 1024)
        except asyncio.TimeoutError as exc:
            raise ComfyClientError("读取 Danbooru 角色候选超时") from exc
        except aiohttp.ClientError as exc:
            raise ComfyClientError("无法读取 Danbooru 角色候选", str(exc)) from exc
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ComfyClientError("Danbooru 角色候选格式无效") from exc
        if not isinstance(payload, list):
            raise ComfyClientError("Danbooru 角色候选不是列表")
        return [item for item in payload[:limit] if isinstance(item, dict)]

    async def danbooru_gallery_health(self) -> dict[str, Any]:
        """Return the optional Gallery network state without exposing secrets."""

        payload = await self._request_json("GET", "/danbooru_gallery/check_network")
        if not isinstance(payload, dict):
            raise ComfyClientError("Danbooru Gallery 状态格式无效")
        return payload

    async def gpu_name(self) -> str:
        """Return the first ComfyUI GPU model without allocator decorations."""
        payload = await self.health()
        devices = payload.get("devices")
        if not isinstance(devices, list):
            return "未知 GPU"
        for device in devices:
            if not isinstance(device, dict):
                continue
            name = str(device.get("name") or "").strip()
            if not name:
                continue
            name = re.sub(r"^cuda:\d+\s+", "", name, flags=re.IGNORECASE)
            name = re.sub(r"\s*:\s*cudaMallocAsync\s*$", "", name)
            return name.strip() or "未知 GPU"
        return "未知 GPU"

    async def upload_image(
        self,
        image_path: Path,
        *,
        subfolder: str = "astrbot_comfy_anima",
    ) -> UploadedImageReference:
        """Upload a validated local image to ComfyUI's input directory."""
        source = image_path.resolve(strict=True)
        suffix = source.suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ComfyClientError("只支持 PNG、JPEG 和 WebP 图片")
        content = await asyncio.to_thread(source.read_bytes)
        digest = hashlib.sha256(content).hexdigest()[:24]
        filename = f"{digest}{suffix}"
        form = aiohttp.FormData()
        form.add_field(
            "image",
            content,
            filename=filename,
            content_type={
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
            }[suffix],
        )
        form.add_field("type", "input")
        form.add_field("subfolder", subfolder)
        form.add_field("overwrite", "true")
        session = await self._get_session()
        try:
            async with session.post(f"{self._base_url}/upload/image", data=form) as response:
                body = await response.text()
                if response.status >= 400:
                    raise ComfyClientError(
                        f"ComfyUI 图片上传失败（HTTP {response.status}）",
                        body[:1000],
                    )
        except asyncio.TimeoutError as exc:
            raise ComfyClientError("上传图片到 ComfyUI 超时") from exc
        except aiohttp.ClientError as exc:
            raise ComfyClientError("无法上传图片到 ComfyUI", str(exc)) from exc
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ComfyClientError("ComfyUI 返回了无效的上传结果") from exc
        name = str(payload.get("name") or "").strip()
        returned_subfolder = str(payload.get("subfolder") or "").strip().strip("/")
        image_type = str(payload.get("type") or "input").strip()
        for value in (name, returned_subfolder):
            if ".." in value or "\\" in value or value.startswith(("/", "~")):
                raise ComfyClientError("ComfyUI 返回了不安全的图片路径")
        if not name or Path(name).name != name or image_type != "input":
            raise ComfyClientError("ComfyUI 返回了无效的图片引用")
        return UploadedImageReference(name, returned_subfolder, image_type)

    async def queue(self) -> dict[str, Any]:
        """获取 ComfyUI 队列状态。"""
        return await self._request_json("GET", "/queue")

    async def submit(self, workflow: dict[str, Any]) -> str:
        """提交工作流并返回 prompt_id。"""
        payload = await self._request_json(
            "POST", "/prompt", {"prompt": workflow, "client_id": self._client_id}
        )
        prompt_id = payload.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id:
            node_errors = payload.get("node_errors")
            raise ComfyClientError(
                "工作流校验失败，请检查 ComfyUI 的模型和自定义节点",
                f"ComfyUI 未返回 prompt_id，node_errors={node_errors}",
            )
        return prompt_id

    async def wait_for_images(
        self, prompt_id: str, preferred_node_ids: list[str]
    ) -> list[ImageReference]:
        """轮询历史记录直到生成完成并返回图片引用。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._settings.generation_timeout
        while loop.time() < deadline:
            payload = await self._request_json("GET", f"/history/{prompt_id}")
            history = payload.get(prompt_id)
            if isinstance(history, dict):
                status = history.get("status", {})
                status_text = str(status.get("status_str", "")).lower()
                if status_text in {"error", "failed"}:
                    messages = status.get("messages", [])
                    raise ComfyClientError(
                        "ComfyUI 执行工作流失败，请联系管理员查看日志",
                        f"ComfyUI 任务失败: {str(messages)[:1000]}",
                    )
                outputs = history.get("outputs", {})
                if isinstance(outputs, dict):
                    images = self.extract_images(outputs, preferred_node_ids)
                    if images:
                        return images
                raise ComfyClientError("任务已结束，但历史记录中没有图片输出")
            await asyncio.sleep(self._settings.poll_interval)
        raise ComfyClientError(
            f"生成超过 {self._settings.generation_timeout} 秒，已停止等待"
        )

    @staticmethod
    def extract_images(
        outputs: dict[str, Any], preferred_node_ids: list[str]
    ) -> list[ImageReference]:
        """按优先节点从 ComfyUI outputs 中提取图片。"""
        ordered_ids = list(dict.fromkeys(preferred_node_ids))
        ordered_ids.extend(node_id for node_id in outputs if node_id not in ordered_ids)
        for node_id in ordered_ids:
            node_output = outputs.get(node_id)
            if not isinstance(node_output, dict):
                continue
            raw_images = node_output.get("images")
            if not isinstance(raw_images, list) or not raw_images:
                continue
            result = []
            for raw in raw_images:
                if not isinstance(raw, dict) or not raw.get("filename"):
                    continue
                result.append(
                    ImageReference(
                        filename=str(raw["filename"]),
                        subfolder=str(raw.get("subfolder", "")),
                        image_type=str(raw.get("type", "output")),
                        node_id=str(node_id),
                    )
                )
            if result:
                return result
        return []

    async def download_image(self, image: ImageReference, target_dir: Path) -> Path:
        """下载单张图片到安全的临时目录。"""
        session = await self._get_session()
        target_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(image.filename).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            suffix = ".png"
        target = target_dir / f"{uuid.uuid4().hex}{suffix}"
        params = {
            "filename": image.filename,
            "subfolder": image.subfolder,
            "type": image.image_type,
        }
        limit = self._settings.max_image_size_mb * 1024 * 1024
        downloaded = 0
        try:
            async with session.get(f"{self._base_url}/view", params=params) as response:
                if response.status >= 400:
                    raise ComfyClientError(f"图片下载失败，HTTP {response.status}")
                content_length = response.content_length
                if content_length is not None and content_length > limit:
                    raise ComfyClientError("生成图片超过插件允许的大小")
                with target.open("wb") as file:
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        downloaded += len(chunk)
                        if downloaded > limit:
                            raise ComfyClientError("生成图片超过插件允许的大小")
                        file.write(chunk)
        except (aiohttp.ClientError, OSError) as exc:
            target.unlink(missing_ok=True)
            raise ComfyClientError(f"图片保存失败: {exc}") from exc
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return target

    async def cancel(self, prompt_id: str) -> None:
        """从队列移除任务；按配置决定是否中断当前全局任务。"""
        try:
            await self._request_json("POST", "/queue", {"delete": [prompt_id]})
            if self._settings.allow_global_interrupt:
                await self._request_json("POST", "/interrupt", {})
        except ComfyClientError:
            return

    async def close(self) -> None:
        """关闭 HTTP 会话。"""
        if self._session is not None and not self._session.closed:
            await self._session.close()
