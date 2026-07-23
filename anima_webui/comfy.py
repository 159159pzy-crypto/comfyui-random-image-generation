from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import urlencode, urlparse

import aiohttp

from .workflow import normalize_lora_path


class ComfyError(RuntimeError):
    pass


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
                data = await response.json(content_type=None)
                if response.status >= 400:
                    message = data.get("error") if isinstance(data, dict) else data
                    raise ComfyError(f"ComfyUI 返回 {response.status}: {message}")
                if not isinstance(data, dict):
                    raise ComfyError("ComfyUI 返回了无效数据")
                return data
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            raise ComfyError(f"无法连接 ComfyUI: {error}") from error

    async def status(self) -> dict[str, Any]:
        return await self._json("GET", "/system_stats")

    async def object_info(self) -> dict[str, Any]:
        return await self._json("GET", "/object_info")

    async def lora_inventory(self) -> dict[str, Any]:
        """Return the local LoRA names exposed by ComfyUI plus optional manifest metadata."""
        names: list[str] = []
        source_available = False
        try:
            object_info = await self.object_info()
            node = object_info.get("LoraLoader") or {}
            required = ((node.get("input") or {}).get("required") or {})
            choices = required.get("lora_name")
            if isinstance(choices, list) and choices and isinstance(choices[0], list):
                names = [str(value) for value in choices[0]]
                source_available = True
        except ComfyError:
            names = []

        manifest_items: dict[str, dict[str, Any]] = {}
        try:
            manifest = await self._json("GET", "/anima-tools/lora/manifest")
            source_available = True
            for item in manifest.get("items") or []:
                if isinstance(item, dict) and item.get("filename"):
                    filename = str(item["filename"])
                    identity = normalize_lora_path(filename).casefold()
                    manifest_items[identity] = item
                    if not any(normalize_lora_path(name).casefold() == identity for name in names):
                        names.append(filename)
        except ComfyError:
            pass

        if not source_available:
            raise ComfyError("无法读取 ComfyUI 本地 LoRA 列表")

        items = []
        seen: set[str] = set()
        for filename in names:
            normalized = normalize_lora_path(filename)
            identity = normalized.casefold()
            if identity in seen:
                continue
            seen.add(identity)
            metadata = manifest_items.get(identity) or {}
            items.append(
                {
                    "filename": filename,
                    "normalized_path": normalized,
                    "folder": normalized.rsplit("/", 1)[0] if "/" in normalized else "",
                    "basename": normalized.rsplit("/", 1)[-1],
                    "display_name": str(
                        metadata.get("display_name")
                        or normalized.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                    ),
                    "preview": str(metadata.get("thumb_url") or ""),
                    "has_preview": bool(metadata.get("has_preview")),
                    "size": metadata.get("size"),
                }
            )
        return {"items": items, "count": len(items)}

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

    async def wait_for_history(self, prompt_id: str) -> dict[str, Any]:
        while True:
            data = await self._json("GET", f"/history/{prompt_id}")
            entry = data.get(prompt_id)
            if entry:
                status = entry.get("status") or {}
                if status.get("status_str") == "error" or status.get("completed") is False:
                    messages = status.get("messages") or []
                    raise ComfyError(f"ComfyUI 执行失败: {messages[-1] if messages else '未知错误'}")
                return entry
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
