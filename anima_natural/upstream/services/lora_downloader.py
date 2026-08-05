"""
AstrBot Comfy Anima 插件 v1.2.0

通过 ComfyUI-Lora-Manager 从 Civitai 下载 LoRA，并在下载完成后
再次获取该文件的 Civitai 元数据。
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePath
from typing import Any, Iterable, Optional, Protocol
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import aiohttp

from ..models import PluginSettings


MAX_RESPONSE_BYTES = 5 * 1024 * 1024
_CIVITAI_MODEL_PATH_RE = re.compile(
    r"^/models/(?P<model_id>[1-9]\d*)(?:/[^/]+)?/?$",
    flags=re.IGNORECASE,
)


class LoraDownloadError(Exception):
    """LoRA 下载、文件定位或 Manager 通信失败。"""

    def __init__(self, user_message: str, detail: str = ""):
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail


class CivitaiUrlError(LoraDownloadError):
    """用户提供的 Civitai 模型页 URL 不符合安全规则。"""


@dataclass(frozen=True)
class CivitaiModelReference:
    """从 Civitai 模型页 URL 中提取的稳定标识。"""

    model_id: int
    version_id: Optional[int]
    source_url: str


@dataclass(frozen=True)
class LoraDownloadResult:
    """一次下载与后处理操作的最终结果。"""

    model_id: int
    version_id: int
    version_name: str
    file_name: str
    file_path: str
    downloaded: bool
    auto_selected_version: bool
    metadata_success: bool
    metadata_message: str
    catalog_success: bool
    catalog_message: str


class _CatalogRefresher(Protocol):
    async def refresh_summary(self) -> str:
        """强制重新读取 LoRA Manager 与 ComfyUI 清单。"""


def _normalize_allowed_host(value: str) -> str:
    """把配置中的主机名或 URL 规范成可精确匹配的 hostname。"""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        parsed = urlparse(raw)
        hostname = parsed.hostname or ""
    else:
        parsed = urlparse(f"//{raw}")
        hostname = parsed.hostname or ""
    return hostname.rstrip(".").casefold()


def parse_civitai_model_url(
    url: str,
    allowed_hosts: Iterable[str],
) -> CivitaiModelReference:
    """安全解析 Civitai 模型页；不会访问用户给出的 URL。"""
    raw = str(url or "").strip()
    if raw.startswith("<") and raw.endswith(">"):
        raw = raw[1:-1].strip()
    if not raw or any(character.isspace() for character in raw):
        raise CivitaiUrlError("请提供一个完整且不含空格的 Civitai 模型页 URL")

    parsed = urlparse(raw)
    if parsed.scheme.casefold() != "https" or not parsed.hostname:
        raise CivitaiUrlError("仅支持 https:// 开头的 Civitai 模型页 URL")
    if parsed.username or parsed.password:
        raise CivitaiUrlError("Civitai URL 不允许包含用户名或密码")
    try:
        if parsed.port not in {None, 443}:
            raise CivitaiUrlError("Civitai URL 不允许使用非标准端口")
    except ValueError as exc:
        raise CivitaiUrlError("Civitai URL 端口无效") from exc

    hostname = parsed.hostname.rstrip(".").casefold()
    allowed = {
        normalized
        for item in allowed_hosts
        if (normalized := _normalize_allowed_host(str(item)))
    }
    if hostname not in allowed:
        raise CivitaiUrlError("只允许配置白名单中的 Civitai 域名，仿冒域名不会被接受")

    match = _CIVITAI_MODEL_PATH_RE.fullmatch(parsed.path)
    if not match:
        raise CivitaiUrlError(
            "URL 必须是 https://civitai.com/models/<模型ID> 形式的模型页"
        )
    model_id = int(match.group("model_id"))

    query = parse_qs(parsed.query, keep_blank_values=True)
    raw_version_ids = query.get("modelVersionId", [])
    if len(raw_version_ids) > 1:
        raise CivitaiUrlError("URL 中只能包含一个 modelVersionId")
    version_id: Optional[int] = None
    if raw_version_ids:
        raw_version_id = raw_version_ids[0].strip()
        if not re.fullmatch(r"[1-9]\d*", raw_version_id):
            raise CivitaiUrlError("modelVersionId 必须是正整数")
        version_id = int(raw_version_id)

    safe_url = f"https://{hostname}/models/{model_id}"
    if version_id is not None:
        safe_url += f"?modelVersionId={version_id}"
    return CivitaiModelReference(
        model_id=model_id,
        version_id=version_id,
        source_url=safe_url,
    )


class LoraDownloadService:
    """调用 LoRA Manager 完成下载、元数据更新与 AstrBot 清单刷新。"""

    def __init__(
        self,
        settings: PluginSettings,
        catalog: Optional[_CatalogRefresher] = None,
    ):
        self._settings = settings
        self._catalog = catalog
        self._origin = self._resolve_manager_origin(settings)
        self._lora_api = f"{self._origin}/api/lm/loras"
        self._session: Optional[aiohttp.ClientSession] = None
        self._semaphore = asyncio.Semaphore(
            max(1, int(settings.lora_download_max_concurrent))
        )
        self._active_lock = asyncio.Lock()
        self._active_references: set[str] = set()
        self._closed = False

    @staticmethod
    def _resolve_manager_origin(settings: PluginSettings) -> str:
        """从 Manager 页面、API 地址或 ComfyUI 地址提取可信 origin。"""
        raw = settings.lora_manager_url.strip() or settings.comfyui_url.strip()
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise LoraDownloadError("LoRA Manager 地址不是有效的 HTTP/HTTPS URL")
        if parsed.username or parsed.password:
            raise LoraDownloadError("LoRA Manager URL 不允许包含用户名或密码")
        try:
            port = parsed.port
        except ValueError as exc:
            raise LoraDownloadError("LoRA Manager URL 端口无效") from exc

        hostname = parsed.hostname
        if settings.lora_lan_only:
            try:
                address = ipaddress.ip_address(hostname)
            except ValueError as exc:
                raise LoraDownloadError(
                    "启用局域网限制时，LoRA Manager 必须使用 IP 地址"
                ) from exc
            if not (address.is_private or address.is_loopback or address.is_link_local):
                raise LoraDownloadError("LoRA Manager 不是私有局域网地址")

        url_host = f"[{hostname}]" if ":" in hostname else hostname
        if port is not None:
            url_host = f"{url_host}:{port}"
        return f"{parsed.scheme}://{url_host}"

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._closed:
            raise LoraDownloadError("LoRA 下载服务已关闭，请重载插件后重试")
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=None)
            )
        return self._session

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        label: str,
        params: Optional[dict[str, Any]] = None,
        payload: Optional[dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> Any:
        """向固定的局域网 Manager 发请求，并限制响应大小及重定向。"""
        session = await self._get_session()
        headers: dict[str, str] = {}
        if self._settings.api_token:
            headers["Authorization"] = f"Bearer {self._settings.api_token}"
        try:
            async with session.request(
                method,
                url,
                params=params,
                json=payload,
                headers=headers,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(
                    total=timeout or self._settings.lora_catalog_timeout
                ),
            ) as response:
                if 300 <= response.status < 400:
                    raise LoraDownloadError(f"{label}不允许 HTTP 重定向")
                chunks: list[bytes] = []
                response_size = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    response_size += len(chunk)
                    if response_size > MAX_RESPONSE_BYTES:
                        raise LoraDownloadError(f"{label}响应超过 5MB")
                    chunks.append(chunk)
                body = b"".join(chunks)
                decoded = body.decode("utf-8", errors="replace")
                try:
                    data = json.loads(decoded) if decoded else {}
                except json.JSONDecodeError as exc:
                    raise LoraDownloadError(f"{label}返回了无效 JSON") from exc
                if response.status >= 400:
                    detail = ""
                    if isinstance(data, dict):
                        detail = str(
                            data.get("error")
                            or data.get("message")
                            or data.get("detail")
                            or ""
                        ).strip()
                    raise LoraDownloadError(
                        f"{label}失败（HTTP {response.status}）",
                        detail,
                    )
                return data
        except asyncio.TimeoutError as exc:
            raise LoraDownloadError(f"{label}超时") from exc
        except aiohttp.ClientError as exc:
            raise LoraDownloadError(
                f"无法连接 LoRA Manager：{label}", str(exc)
            ) from exc

    async def _fetch_versions(self, model_id: int) -> list[dict[str, Any]]:
        data = await self._request_json(
            "GET",
            f"{self._lora_api}/civitai/versions/{model_id}",
            label="读取 Civitai 版本列表",
            timeout=max(30, self._settings.lora_catalog_timeout),
        )
        if not isinstance(data, list):
            raise LoraDownloadError("LoRA Manager 返回的 Civitai 版本列表格式无效")
        versions = [item for item in data if isinstance(item, dict)]
        if not versions:
            raise LoraDownloadError(f"Civitai 模型 {model_id} 没有可下载版本")
        return versions

    @staticmethod
    def _version_sort_key(version: dict[str, Any]) -> tuple[float, int]:
        raw_created_at = str(version.get("createdAt") or "").strip()
        timestamp = 0.0
        if raw_created_at:
            try:
                timestamp = datetime.fromisoformat(
                    raw_created_at.replace("Z", "+00:00")
                ).timestamp()
            except ValueError:
                timestamp = 0.0
        try:
            version_id = int(version.get("id") or 0)
        except (TypeError, ValueError):
            version_id = 0
        return timestamp, version_id

    @classmethod
    def _select_version(
        cls,
        versions: list[dict[str, Any]],
        requested_version_id: Optional[int],
    ) -> dict[str, Any]:
        if requested_version_id is not None:
            for version in versions:
                try:
                    version_id = int(version.get("id"))
                except (TypeError, ValueError):
                    continue
                if version_id == requested_version_id:
                    return version
            raise LoraDownloadError(
                f"模型中找不到版本 modelVersionId={requested_version_id}"
            )
        return max(versions, key=cls._version_sort_key)

    async def _fetch_manager_items(self) -> list[dict[str, Any]]:
        page = 1
        total_pages = 1
        items: list[dict[str, Any]] = []
        while page <= total_pages:
            if page > 100:
                raise LoraDownloadError("LoRA Manager 分页数量异常")
            data = await self._request_json(
                "GET",
                f"{self._lora_api}/list",
                label="读取 LoRA Manager 清单",
                params={
                    "page": page,
                    "page_size": self._settings.lora_manager_page_size,
                    "sort_by": "name",
                },
            )
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise LoraDownloadError("LoRA Manager 清单格式无效")
            items.extend(item for item in data["items"] if isinstance(item, dict))
            try:
                total_pages = max(1, int(data.get("total_pages", 1)))
            except (TypeError, ValueError):
                total_pages = 1
            page += 1
        return items

    @staticmethod
    def _as_positive_int(value: Any) -> Optional[int]:
        try:
            converted = int(value)
        except (TypeError, ValueError):
            return None
        return converted if converted > 0 else None

    @classmethod
    def _version_hashes(cls, version: dict[str, Any]) -> set[str]:
        hashes: set[str] = set()
        files = version.get("files")
        if not isinstance(files, list):
            return hashes
        for file_info in files:
            if not isinstance(file_info, dict):
                continue
            raw_hashes = file_info.get("hashes")
            if not isinstance(raw_hashes, dict):
                continue
            sha256 = str(raw_hashes.get("SHA256") or "").strip().casefold()
            if sha256:
                hashes.add(sha256)
        return hashes

    @classmethod
    def _version_file_names(cls, version: dict[str, Any]) -> set[str]:
        names: set[str] = set()
        files = version.get("files")
        if not isinstance(files, list):
            return names
        for file_info in files:
            if not isinstance(file_info, dict):
                continue
            name = str(
                file_info.get("overrideName") or file_info.get("name") or ""
            ).strip()
            if name:
                names.add(name.casefold())
                names.add(PurePath(name).stem.casefold())
        return names

    @classmethod
    def _find_downloaded_item(
        cls,
        items: list[dict[str, Any]],
        *,
        model_id: int,
        version_id: int,
        version: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Return one uniquely and exactly identified Manager item.

        A model id alone is not enough to prove that a specific Civitai version
        is available. Ambiguous ids, hashes, or file names fail closed so
        metadata is never written to an arbitrary LoRA file.
        """

        version_hashes = cls._version_hashes(version)
        version_names = cls._version_file_names(version)
        exact_matches: list[dict[str, Any]] = []
        for item in items:
            civitai = item.get("civitai")
            if not isinstance(civitai, dict):
                civitai = {}
            item_version_id = cls._as_positive_int(civitai.get("id"))
            item_model_id = cls._as_positive_int(civitai.get("modelId"))
            sha256 = str(item.get("sha256") or "").strip().casefold()
            file_name = str(item.get("file_name") or "").strip().casefold()
            id_match = item_version_id == version_id and item_model_id in {
                None,
                model_id,
            }
            hash_match = bool(sha256 and sha256 in version_hashes)
            name_match = bool(
                item_model_id == model_id
                and item_version_id in {None, version_id}
                and (
                    file_name in version_names
                    or PurePath(file_name).stem in version_names
                )
            )
            if id_match or hash_match or name_match:
                exact_matches.append(item)

        if len(exact_matches) > 1:
            raise LoraDownloadError(
                "LoRA Manager 最新清单中存在多个精确匹配文件，已停止",
                f"model={model_id}, version={version_id}, matches={len(exact_matches)}",
            )
        return exact_matches[0] if exact_matches else None

    async def _post_download(
        self,
        model_id: int,
        version_id: int,
        download_id: str,
    ) -> dict[str, Any]:
        data = await self._request_json(
            "POST",
            f"{self._origin}/api/lm/download-model",
            label="LoRA 下载",
            payload={
                "model_id": model_id,
                "model_version_id": version_id,
                "model_root": "",
                "relative_path": "",
                "use_default_paths": True,
                "download_id": download_id,
            },
            timeout=self._settings.lora_download_timeout,
        )
        if not isinstance(data, dict):
            raise LoraDownloadError("LoRA Manager 下载响应格式无效")
        if data.get("success") is False:
            raise LoraDownloadError(
                "LoRA Manager 下载失败",
                str(data.get("error") or data.get("message") or "").strip(),
            )
        return data

    async def _scan_manager(self) -> None:
        data = await self._request_json(
            "GET",
            f"{self._lora_api}/scan",
            label="LoRA Manager 扫描",
            timeout=self._settings.lora_manager_scan_timeout,
        )
        if isinstance(data, dict) and data.get("success") is False:
            raise LoraDownloadError(
                "LoRA Manager 扫描失败",
                str(data.get("error") or data.get("message") or "").strip(),
            )

    async def _fetch_civitai_metadata(self, file_path: str) -> tuple[bool, str]:
        try:
            data = await self._request_json(
                "POST",
                f"{self._lora_api}/fetch-civitai",
                label="从 Civitai 获取元数据",
                payload={"file_path": file_path},
                timeout=self._settings.lora_metadata_timeout,
            )
        except LoraDownloadError as exc:
            detail = f"：{exc.detail}" if exc.detail else ""
            return False, f"{exc.user_message}{detail}"
        if not isinstance(data, dict):
            return False, "LoRA Manager 元数据响应格式无效"
        if data.get("success") is not True:
            message = str(data.get("error") or data.get("message") or "未知错误")
            return False, message.strip()
        return True, "已再次从 Civitai 获取并写入元数据"

    async def fetch_civitai_metadata(self, file_path: str) -> tuple[bool, str]:
        """Fetch and persist Civitai metadata for one Manager file path."""
        normalized = str(file_path or "").strip()
        if not normalized:
            raise LoraDownloadError("LoRA 文件路径为空，无法获取 Civitai 元数据")
        return await self._fetch_civitai_metadata(normalized)

    async def fetch_all_civitai_metadata(self) -> dict[str, Any]:
        """Run LoRA Manager's native bulk Civitai metadata operation."""
        data = await self._request_json(
            "POST",
            f"{self._lora_api}/fetch-all-civitai",
            label="批量从 Civitai 获取 LoRA 元数据",
            payload={},
            timeout=max(
                self._settings.lora_metadata_timeout,
                self._settings.lora_manager_scan_timeout,
                self._settings.lora_download_timeout,
            ),
        )
        if not isinstance(data, dict):
            raise LoraDownloadError("LoRA Manager 批量元数据响应格式无效")
        if data.get("success") is False and data.get("status") not in {
            "completed",
            "cancelled",
        }:
            raise LoraDownloadError(
                "LoRA Manager 批量元数据获取失败",
                str(data.get("error") or data.get("message") or "").strip(),
            )
        return data

    @staticmethod
    def _item_file_path(item: Optional[dict[str, Any]]) -> str:
        if not item:
            return ""
        return str(item.get("file_path") or "").strip()

    @staticmethod
    def _item_file_name(item: Optional[dict[str, Any]], file_path: str) -> str:
        if file_path:
            return file_path.replace("\\", "/").rsplit("/", 1)[-1]
        if item:
            return str(item.get("file_name") or "").strip()
        return ""

    async def _refresh_catalog(self) -> tuple[bool, str]:
        if self._catalog is None:
            return False, "AstrBot LoRA 双源清单服务未启用"
        try:
            return True, await self._catalog.refresh_summary()
        except Exception as exc:
            return False, str(getattr(exc, "user_message", exc)).strip()

    async def _require_catalog_refresh(self, stage: str) -> str:
        """Require a fresh Manager + ComfyUI loadable catalog before continuing."""
        success, message = await self._refresh_catalog()
        if not success:
            raise LoraDownloadError(
                f"{stage}刷新 LoRA Manager 与 ComfyUI 可加载清单失败，已停止",
                message or "未知错误",
            )
        return message

    async def download_from_url(self, url: str) -> LoraDownloadResult:
        """下载 URL 指向的版本，随后获取元数据并刷新 AstrBot 清单。"""
        reference = parse_civitai_model_url(
            url,
            self._settings.lora_download_allowed_hosts,
        )
        active_key = f"{reference.model_id}:{reference.version_id or 'latest'}"
        async with self._active_lock:
            if active_key in self._active_references:
                raise LoraDownloadError("这个 Civitai LoRA 正在下载，请勿重复提交")
            self._active_references.add(active_key)

        try:
            async with self._semaphore:
                # Keep direct service calls fail-closed even if the command/UI
                # caller already performed its own freshness check.
                await self._require_catalog_refresh("下载前")
                # 下载/判重前也必须先扫描，不能依据 Manager 的旧缓存。
                await self._scan_manager()
                versions = await self._fetch_versions(reference.model_id)
                version = self._select_version(versions, reference.version_id)
                version_id = self._as_positive_int(version.get("id"))
                if version_id is None:
                    raise LoraDownloadError("LoRA Manager 返回了无效的版本 ID")
                version_name = str(version.get("name") or version_id).strip()

                items = await self._fetch_manager_items()
                matched_item = self._find_downloaded_item(
                    items,
                    model_id=reference.model_id,
                    version_id=version_id,
                    version=version,
                )
                downloaded = matched_item is None
                if downloaded:
                    await self._post_download(
                        reference.model_id,
                        version_id,
                        uuid4().hex,
                    )
                    try:
                        # 文件完成后再次扫描，确认真实路径后才获取元数据。
                        await self._scan_manager()
                        items = await self._fetch_manager_items()
                    except LoraDownloadError as exc:
                        detail = exc.user_message
                        if exc.detail:
                            detail = f"{detail}: {exc.detail}"
                        raise LoraDownloadError(
                            "LoRA 下载已提交，但 Manager 刷新失败，文件当前未就绪",
                            detail,
                        ) from exc
                    matched_item = self._find_downloaded_item(
                        items,
                        model_id=reference.model_id,
                        version_id=version_id,
                        version=version,
                    )
                    if matched_item is None:
                        raise LoraDownloadError(
                            "LoRA 下载已提交，但刷新后的 Manager 清单未找到唯一精确文件，"
                            "当前未就绪",
                            f"model={reference.model_id}, version={version_id}",
                        )

                file_path = self._item_file_path(matched_item)
                if not file_path:
                    raise LoraDownloadError(
                        "LoRA Manager 最新清单中的精确匹配文件缺少路径，已停止",
                        f"model={reference.model_id}, version={version_id}",
                    )
                file_name = self._item_file_name(matched_item, file_path)

                (
                    metadata_success,
                    metadata_message,
                ) = await self._fetch_civitai_metadata(file_path)

                catalog_success, catalog_message = await self._refresh_catalog()
                return LoraDownloadResult(
                    model_id=reference.model_id,
                    version_id=version_id,
                    version_name=version_name,
                    file_name=file_name,
                    file_path=file_path,
                    downloaded=downloaded,
                    auto_selected_version=reference.version_id is None,
                    metadata_success=metadata_success,
                    metadata_message=metadata_message,
                    catalog_success=catalog_success,
                    catalog_message=catalog_message,
                )
        finally:
            async with self._active_lock:
                self._active_references.discard(active_key)

    async def close(self) -> None:
        """关闭独立长超时 HTTP 会话。"""
        self._closed = True
        if self._session is not None and not self._session.closed:
            await self._session.close()
