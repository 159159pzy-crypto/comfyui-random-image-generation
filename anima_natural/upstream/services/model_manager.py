"""Safe LoRA and UNET deletion through ComfyUI-Lora-Manager.

The browser-facing layer must never submit a filesystem path.  This service
accepts only an exact display name plus a repeated confirmation name, refreshes
the authoritative remote catalogs, resolves the Manager-owned path internally,
and returns a path-free result.
"""

from __future__ import annotations

import asyncio
import hmac
import inspect
import ipaddress
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional, TypeAlias
from urllib.parse import urlparse

import aiohttp

from ..core.lora import LORA_EXTENSIONS, canonical_lora_name
from ..models import PluginSettings
from .lora_catalog import LoraCatalogError, LoraCatalogService, LoraRecord
from .unet_catalog import UnetCatalogError, UnetCatalogService, UnetModelEntry


MAX_MANAGER_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_MANAGER_PAGES = 100
MAX_EXACT_NAME_LENGTH = 1024
UNET_EXTENSIONS = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf")

PresetReferenceResolver: TypeAlias = Callable[
    [str], Sequence[str] | Awaitable[Sequence[str]]
]
PresetRemovalCallback: TypeAlias = Callable[
    [str], int | Sequence[str] | None | Awaitable[int | Sequence[str] | None]
]
CurrentUnetResolver: TypeAlias = Callable[[], str]


class ModelManagerError(RuntimeError):
    """A model deletion request failed without exposing a remote path."""

    def __init__(self, user_message: str, detail: str = ""):
        self.user_message = user_message
        self.detail = detail
        super().__init__(detail or user_message)


@dataclass(frozen=True)
class ModelDeleteResult:
    """Path-free result of one completed model deletion."""

    model_type: str
    exact_name: str
    deleted: bool
    refreshed_count: int
    removed_from_presets: bool = False
    preset_cleanup_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        """Return a WebUI-safe representation with no filesystem path."""
        return {
            "model_type": self.model_type,
            "exact_name": self.exact_name,
            "deleted": self.deleted,
            "refreshed_count": self.refreshed_count,
            "removed_from_presets": self.removed_from_presets,
            "preset_cleanup_count": self.preset_cleanup_count,
        }


class ModelManagerService:
    """Resolve and delete LoRA/UNET files from mandatory fresh catalogs."""

    def __init__(
        self,
        settings: PluginSettings,
        lora_catalog: LoraCatalogService,
        unet_catalog: UnetCatalogService,
        *,
        preset_reference_resolver: Optional[PresetReferenceResolver] = None,
        preset_removal_callback: Optional[PresetRemovalCallback] = None,
        current_unet_resolver: Optional[CurrentUnetResolver] = None,
    ) -> None:
        self._settings = settings
        self._lora_catalog = lora_catalog
        self._unet_catalog = unet_catalog
        self._preset_reference_resolver = preset_reference_resolver
        self._preset_removal_callback = preset_removal_callback
        self._current_unet_resolver = current_unet_resolver
        self._origin = self._resolve_manager_origin(settings)
        self._session: Optional[aiohttp.ClientSession] = None
        self._operation_lock = asyncio.Lock()
        self._closed = False

    @staticmethod
    def _resolve_manager_origin(settings: PluginSettings) -> str:
        """Return the validated Manager origin without retaining URL paths."""
        raw = settings.lora_manager_url.strip() or settings.comfyui_url.strip()
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ModelManagerError("LoRA Manager 地址不是有效的 HTTP/HTTPS URL")
        if parsed.username or parsed.password:
            raise ModelManagerError("LoRA Manager URL 不允许包含用户名或密码")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ModelManagerError("LoRA Manager URL 端口无效") from exc

        hostname = parsed.hostname
        if settings.lora_lan_only:
            try:
                address = ipaddress.ip_address(hostname)
            except ValueError as exc:
                raise ModelManagerError(
                    "启用局域网限制时，LoRA Manager 必须使用 IP 地址"
                ) from exc
            if not (address.is_private or address.is_loopback or address.is_link_local):
                raise ModelManagerError("LoRA Manager 不是私有局域网地址")

        url_host = f"[{hostname}]" if ":" in hostname else hostname
        if port is not None:
            url_host = f"{url_host}:{port}"
        return f"{parsed.scheme}://{url_host}"

    async def _get_session(self) -> aiohttp.ClientSession:
        """Create the bounded Manager HTTP session lazily."""
        if self._closed:
            raise ModelManagerError("模型管理服务已关闭，请重载插件后重试")
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=None)
            )
        return self._session

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        payload: Optional[dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> dict[str, Any]:
        """Call a fixed Manager endpoint and never expose its raw error body."""
        session = await self._get_session()
        headers: dict[str, str] = {}
        if self._settings.api_token:
            headers["Authorization"] = f"Bearer {self._settings.api_token}"
        try:
            async with session.request(
                method,
                f"{self._origin}{path}",
                params=params,
                json=payload,
                headers=headers,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(
                    total=timeout or self._settings.lora_catalog_timeout
                ),
            ) as response:
                if 300 <= response.status < 400:
                    raise ModelManagerError("LoRA Manager 不允许 HTTP 重定向")
                chunks: list[bytes] = []
                response_size = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    response_size += len(chunk)
                    if response_size > MAX_MANAGER_RESPONSE_BYTES:
                        raise ModelManagerError("LoRA Manager 响应超过 5MB")
                    chunks.append(chunk)
                if response.status >= 400:
                    raise ModelManagerError(
                        f"LoRA Manager 操作失败（HTTP {response.status}）"
                    )
        except asyncio.TimeoutError as exc:
            raise ModelManagerError("LoRA Manager 操作超时") from exc
        except aiohttp.ClientError as exc:
            raise ModelManagerError("无法连接 LoRA Manager") from exc

        try:
            data = json.loads(b"".join(chunks).decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise ModelManagerError("LoRA Manager 返回了无效 JSON") from exc
        if not isinstance(data, dict):
            raise ModelManagerError("LoRA Manager 返回格式无效")
        return data

    @staticmethod
    def _validated_exact_name(value: str, label: str) -> str:
        """Validate a relative display name supplied by the browser."""
        name = str(value or "").strip().replace("\\", "/")
        if not name:
            raise ModelManagerError(f"{label}不能为空")
        if len(name) > MAX_EXACT_NAME_LENGTH:
            raise ModelManagerError(f"{label}过长")
        if any(ord(character) < 32 for character in name) or "\x00" in name:
            raise ModelManagerError(f"{label}包含非法控制字符")
        if "://" in name or name.startswith("/") or re.match(r"^[A-Za-z]:/", name):
            raise ModelManagerError(f"{label}必须是清单中的相对名称")
        segments = [segment for segment in name.split("/") if segment]
        if not segments or any(segment in {".", ".."} for segment in segments):
            raise ModelManagerError(f"{label}包含不安全路径片段")
        return "/".join(segments)

    @staticmethod
    def _validated_manager_path(
        value: Any,
        *,
        expected_name: str,
        extensions: tuple[str, ...],
        canonicalize: bool,
    ) -> str:
        """Validate an absolute Manager-owned path without returning it publicly."""
        raw_path = str(value or "").strip().replace("\\", "/")
        if not raw_path or len(raw_path) > 4096:
            raise ModelManagerError("Manager 未返回可安全删除的模型路径")
        if any(ord(character) < 32 for character in raw_path) or "\x00" in raw_path:
            raise ModelManagerError("Manager 返回的模型路径不安全")
        if "://" in raw_path:
            raise ModelManagerError("Manager 返回的模型路径不安全")
        if not (raw_path.startswith("/") or re.match(r"^[A-Za-z]:/", raw_path)):
            raise ModelManagerError("Manager 返回的模型路径不是绝对路径")
        segments = [segment for segment in raw_path.split("/") if segment]
        if not segments or any(segment in {".", ".."} for segment in segments):
            raise ModelManagerError("Manager 返回的模型路径不安全")
        basename = segments[-1]
        if not basename.casefold().endswith(
            tuple(ext.casefold() for ext in extensions)
        ):
            raise ModelManagerError("Manager 返回的模型文件类型不受支持")

        expected_basename = expected_name.replace("\\", "/").rsplit("/", 1)[-1]
        if canonicalize:
            actual_key = canonical_lora_name(basename).casefold()
            expected_key = canonical_lora_name(expected_basename).casefold()
        else:
            actual_key = basename.casefold()
            expected_key = expected_basename.casefold()
        if not expected_key or actual_key != expected_key:
            raise ModelManagerError("Manager 返回路径与所选模型名称不一致")
        return raw_path

    @staticmethod
    def _confirmed(exact_name: str, confirm_name: str) -> None:
        """Require the repeated confirmation value to match byte-for-byte."""
        if not hmac.compare_digest(exact_name, str(confirm_name or "").strip()):
            raise ModelManagerError("确认名称与所选模型名称不一致")

    async def _preset_references(self, exact_name: str) -> tuple[str, ...]:
        """Resolve current preset references without exposing their internals."""
        if self._preset_reference_resolver is None:
            return ()
        result = self._preset_reference_resolver(exact_name)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, str):
            values: Sequence[str] = (result,)
        else:
            values = result
        return tuple(str(value).strip() for value in values if str(value).strip())

    async def delete_lora(
        self,
        exact_name: str,
        confirm_name: str,
        *,
        remove_from_presets: bool = False,
    ) -> ModelDeleteResult:
        """Delete one exact LoRA after a mandatory Manager and ComfyUI refresh."""
        requested_name = self._validated_exact_name(exact_name, "LoRA 名称")
        async with self._operation_lock:
            try:
                records = await self._lora_catalog.refresh_for_operation()
            except LoraCatalogError as exc:
                raise ModelManagerError(exc.user_message) from exc

            self._confirmed(requested_name, confirm_name)
            requested_key = canonical_lora_name(requested_name).casefold()
            matches = [
                record
                for record in records
                if canonical_lora_name(record.name).casefold() == requested_key
            ]
            if len(matches) != 1:
                raise ModelManagerError("最新 LoRA 清单中找不到该精确名称")
            record: LoraRecord = matches[0]
            file_path = self._validated_manager_path(
                record.file_path,
                expected_name=record.name,
                extensions=LORA_EXTENSIONS,
                canonicalize=True,
            )

            references = await self._preset_references(record.name)
            preset_cleanup_count = 0
            if references:
                if not remove_from_presets:
                    raise ModelManagerError(
                        f"该 LoRA 仍被 {len(references)} 个预设引用，已阻止删除"
                    )
                if self._preset_removal_callback is None:
                    raise ModelManagerError("未配置 LoRA 预设清理回调，已阻止删除")
                removal_result = self._preset_removal_callback(record.name)
                if inspect.isawaitable(removal_result):
                    removal_result = await removal_result
                if isinstance(removal_result, int) and not isinstance(
                    removal_result, bool
                ):
                    preset_cleanup_count = max(0, removal_result)
                elif isinstance(removal_result, str):
                    preset_cleanup_count = 1 if removal_result.strip() else 0
                elif removal_result is not None:
                    preset_cleanup_count = len(
                        [value for value in removal_result if str(value).strip()]
                    )
                else:
                    preset_cleanup_count = len(references)
                if await self._preset_references(record.name):
                    raise ModelManagerError("LoRA 预设引用清理不完整，已阻止删除")

            data = await self._request_json(
                "POST",
                "/api/lm/loras/delete",
                payload={"file_path": file_path},
                timeout=self._settings.lora_manager_scan_timeout,
            )
            if data.get("success") is not True:
                raise ModelManagerError("LoRA Manager 未确认删除成功")
            return ModelDeleteResult(
                model_type="lora",
                exact_name=record.name,
                deleted=True,
                refreshed_count=len(records),
                removed_from_presets=bool(references),
                preset_cleanup_count=preset_cleanup_count,
            )

    async def _fresh_unet_sources(
        self,
    ) -> tuple[list[dict[str, Any]], tuple[UnetModelEntry, ...]]:
        """Force the Manager scan and ComfyUI UNETLoader list before mutation."""
        scan = await self._request_json(
            "GET",
            "/api/lm/checkpoints/scan",
            timeout=self._settings.lora_manager_scan_timeout,
        )
        if scan.get("success") is False:
            raise ModelManagerError("LoRA Manager 的模型扫描失败")

        page = 1
        total_pages = 1
        items: list[dict[str, Any]] = []
        while page <= total_pages:
            if page > MAX_MANAGER_PAGES:
                raise ModelManagerError("LoRA Manager 模型分页数量异常")
            payload = await self._request_json(
                "GET",
                "/api/lm/checkpoints/list",
                params={"page": page, "page_size": 100, "sort_by": "name"},
            )
            raw_items = payload.get("items")
            if not isinstance(raw_items, list):
                raise ModelManagerError("LoRA Manager 模型清单格式无效")
            items.extend(
                dict(item)
                for item in raw_items
                if isinstance(item, Mapping)
                and str(item.get("sub_type") or "").strip().casefold()
                == "diffusion_model"
            )
            try:
                total_pages = max(1, int(payload.get("total_pages", 1)))
            except (TypeError, ValueError):
                total_pages = 1
            page += 1

        try:
            entries = await self._unet_catalog.list_models()
        except UnetCatalogError as exc:
            raise ModelManagerError(exc.user_message) from exc
        return items, entries

    @staticmethod
    def _unet_item_matches(item: Mapping[str, Any], exact_name: str) -> bool:
        """Match only exact relative names or an exact Manager path suffix."""
        key = exact_name.replace("\\", "/").strip("/").casefold()
        file_name = str(item.get("file_name") or "").strip().replace("\\", "/")
        folder = str(item.get("folder") or "").strip().strip("/\\")
        relative_path = str(item.get("relative_path") or "").strip().replace("\\", "/")
        candidates = {file_name.strip("/").casefold()}
        if folder and file_name:
            candidates.add(f"{folder}/{file_name}".strip("/").casefold())
        if relative_path:
            candidates.add(relative_path.strip("/").casefold())
        if key in candidates:
            return True
        file_path = str(item.get("file_path") or "").strip().replace("\\", "/")
        return bool(file_path) and file_path.casefold().endswith(f"/{key}")

    async def delete_unet(
        self,
        exact_name: str,
        confirm_name: str,
    ) -> ModelDeleteResult:
        """Delete one exact diffusion-model UNET after two-source refresh."""
        requested_name = self._validated_exact_name(exact_name, "UNET 名称")
        async with self._operation_lock:
            manager_items, entries = await self._fresh_unet_sources()
            self._confirmed(requested_name, confirm_name)
            requested_key = requested_name.casefold()
            comfy_matches = [
                entry
                for entry in entries
                if entry.name.replace("\\", "/").casefold() == requested_key
            ]
            if len(comfy_matches) != 1:
                raise ModelManagerError("最新 ComfyUI UNET 清单中找不到该精确名称")
            entry = comfy_matches[0]
            manager_matches = [
                item
                for item in manager_items
                if self._unet_item_matches(item, entry.name)
            ]
            if len(manager_matches) != 1:
                if manager_matches:
                    raise ModelManagerError("LoRA Manager 中存在重名 UNET，已阻止删除")
                raise ModelManagerError("最新 LoRA Manager 清单中找不到该 UNET")

            current_name = (
                self._current_unet_resolver()
                if self._current_unet_resolver is not None
                else self._settings.unet_model_name
            )
            current_key = str(current_name or "").strip().replace("\\", "/").casefold()
            target_key = entry.name.replace("\\", "/").casefold()
            if current_key and (
                current_key == target_key
                or current_key.rsplit("/", 1)[-1] == target_key.rsplit("/", 1)[-1]
            ):
                raise ModelManagerError("当前正在使用该 UNET，请先切换模型再删除")

            item = manager_matches[0]
            file_path = self._validated_manager_path(
                item.get("file_path"),
                expected_name=entry.name,
                extensions=UNET_EXTENSIONS,
                canonicalize=False,
            )
            data = await self._request_json(
                "POST",
                "/api/lm/checkpoints/delete",
                payload={"file_path": file_path},
                timeout=self._settings.lora_manager_scan_timeout,
            )
            if data.get("success") is not True:
                raise ModelManagerError("LoRA Manager 未确认 UNET 删除成功")
            return ModelDeleteResult(
                model_type="unet",
                exact_name=entry.name,
                deleted=True,
                refreshed_count=len(entries),
            )

    async def close(self) -> None:
        """Close the private HTTP session."""
        self._closed = True
        if self._session is not None and not self._session.closed:
            await self._session.close()


__all__ = [
    "ModelDeleteResult",
    "ModelManagerError",
    "ModelManagerService",
]
