"""实时读取 ComfyUI UNETLoader 模型清单。"""

from __future__ import annotations

import asyncio
import ipaddress
import json
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

import aiohttp

from ..models import PluginSettings


MAX_UNET_CATALOG_BYTES = 5 * 1024 * 1024


class UnetCatalogError(Exception):
    """UNET 模型清单读取或选择失败。"""

    def __init__(self, user_message: str, detail: str = ""):
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail


@dataclass(frozen=True)
class UnetModelEntry:
    """ComfyUI 当前可加载的单个 UNET 文件。"""

    index: int
    name: str


class UnetCatalogService:
    """不使用缓存，每次操作都读取 ComfyUI 最新 UNET 文件清单。"""

    def __init__(self, settings: PluginSettings):
        self._settings = settings
        self._url = self._resolve_url(settings)
        self._validate_url(self._url)
        self._session: Optional[aiohttp.ClientSession] = None

    @staticmethod
    def _resolve_url(settings: PluginSettings) -> str:
        raw = settings.unet_catalog_url.strip()
        if not raw:
            return f"{settings.comfyui_url.rstrip('/')}/object_info/UNETLoader"
        parsed = urlparse(raw)
        path = parsed.path.rstrip("/")
        if path in {"", "/"}:
            return f"{raw.rstrip('/')}/object_info/UNETLoader"
        if path.endswith("/object_info"):
            return f"{raw.rstrip('/')}/UNETLoader"
        return raw.rstrip("/")

    def _validate_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise UnetCatalogError("UNET 模型清单必须是有效的 HTTP/HTTPS 地址")
        if parsed.username or parsed.password:
            raise UnetCatalogError("UNET 模型清单 URL 不允许包含用户名或密码")
        if not self._settings.unet_lan_only:
            return
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError as exc:
            raise UnetCatalogError(
                "启用局域网限制时，UNET 模型清单必须使用 IP 地址"
            ) from exc
        if not (address.is_private or address.is_loopback or address.is_link_local):
            raise UnetCatalogError("UNET 模型清单不是私有局域网地址")

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._settings.unet_catalog_timeout)
            )
        return self._session

    async def list_models(self) -> tuple[UnetModelEntry, ...]:
        """实时请求 object_info；故意不缓存，保证切换前读取最新目录。"""
        session = await self._get_session()
        headers: dict[str, str] = {}
        if self._settings.api_token:
            headers["Authorization"] = f"Bearer {self._settings.api_token}"
        try:
            async with session.get(
                self._url,
                headers=headers,
                allow_redirects=False,
            ) as response:
                if 300 <= response.status < 400:
                    raise UnetCatalogError("UNET 模型清单不允许 HTTP 重定向")
                if response.status >= 400:
                    raise UnetCatalogError(f"UNET 模型清单返回 HTTP {response.status}")
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    size += len(chunk)
                    if size > MAX_UNET_CATALOG_BYTES:
                        raise UnetCatalogError("UNET 模型清单响应超过 5MB")
                    chunks.append(chunk)
        except asyncio.TimeoutError as exc:
            raise UnetCatalogError("读取 UNET 模型清单超时") from exc
        except aiohttp.ClientError as exc:
            raise UnetCatalogError("无法连接 ComfyUI UNET 模型清单", str(exc)) from exc

        try:
            payload = json.loads(b"".join(chunks).decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise UnetCatalogError("ComfyUI 返回了无效的 UNET 清单 JSON") from exc
        names = self.parse_payload(payload)
        if not names:
            raise UnetCatalogError("ComfyUI 的 UNET 模型目录中没有可用文件")
        return tuple(
            UnetModelEntry(index=index, name=name)
            for index, name in enumerate(names, start=1)
        )

    @staticmethod
    def parse_payload(payload: Any) -> tuple[str, ...]:
        """解析 `/object_info/UNETLoader` 的 required.unet_name 列表。"""
        if not isinstance(payload, dict):
            return ()
        node = payload.get("UNETLoader")
        if not isinstance(node, dict):
            return ()
        inputs = node.get("input")
        required = inputs.get("required") if isinstance(inputs, dict) else None
        raw_field = required.get("unet_name") if isinstance(required, dict) else None
        raw_names = raw_field[0] if isinstance(raw_field, list) and raw_field else None
        if not isinstance(raw_names, list):
            return ()
        names: list[str] = []
        seen: set[str] = set()
        for value in raw_names:
            name = str(value).strip()
            key = name.casefold()
            if name and key not in seen:
                seen.add(key)
                names.append(name)
        return tuple(names)

    @staticmethod
    def resolve(
        identifier: str,
        entries: tuple[UnetModelEntry, ...],
    ) -> UnetModelEntry:
        """按 1-based 序号或完整名称选择刚刚刷新得到的模型。"""
        value = str(identifier or "").strip()
        if not value:
            raise UnetCatalogError("请提供 UNET 模型序号或完整名称")
        if value.isdecimal():
            index = int(value)
            if index < 1 or index > len(entries):
                raise UnetCatalogError(f"UNET 模型序号必须在 1 到 {len(entries)} 之间")
            return entries[index - 1]
        matches = [
            entry for entry in entries if entry.name.casefold() == value.casefold()
        ]
        if len(matches) == 1:
            return matches[0]
        raise UnetCatalogError("找不到该 UNET 模型，请先使用 /模型列表 查看完整名称")

    @staticmethod
    def format_listing(
        entries: tuple[UnetModelEntry, ...],
        current_model: str = "",
    ) -> str:
        current_key = current_model.strip().casefold()
        lines = [f"最新 UNET 模型清单：共 {len(entries)} 个"]
        for entry in entries:
            marker = " ✅ 当前" if entry.name.casefold() == current_key else ""
            lines.append(f"{entry.index}. {entry.name}{marker}")
        if current_key and not any(
            entry.name.casefold() == current_key for entry in entries
        ):
            lines.append(f"⚠️ 当前配置 {current_model} 已不在最新目录清单中")
        return "\n".join(lines)

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()


__all__ = [
    "UnetCatalogError",
    "UnetCatalogService",
    "UnetModelEntry",
]
