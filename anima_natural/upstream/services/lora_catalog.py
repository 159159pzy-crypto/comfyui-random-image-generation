"""
AstrBot Comfy Anima 插件 v1.2.0

功能描述：
- 安全读取局域网 HTTP LoRA 清单或 ComfyUI object_info
- 为 LLM 工具提供可搜索、可缓存的 LoRA 元数据

作者: Yen
版本: 1.2.0
日期: 2026-07-19
"""

import asyncio
import ipaddress
import json
import re
import time
import unicodedata
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import Any, Callable, Mapping, Optional
from urllib.parse import parse_qs, unquote, urlparse, urlunparse

import aiohttp

from ..core.lora import LORA_EXTENSIONS, canonical_lora_name
from ..models import LoraSelection, PluginSettings


MAX_CATALOG_BYTES = 10 * 1024 * 1024
MAX_MANAGER_PREVIEW_BYTES = 4 * 1024 * 1024
MAX_CHARACTER_IDENTITIES_PER_LORA = 24
MAX_WORK_IDENTITIES_PER_LORA = 24
GENERIC_IDENTITY_TERMS = {
    "anima",
    "lora",
    "character",
    "characters",
    "artist",
    "artists",
    "artist style",
    "art style",
    "style",
    "styles",
    "anime",
    "model",
    "preview",
    "base",
    "new",
    "新增",
    "新增anima",
    "角色",
    "画师",
    "风格",
}
WORK_ALIAS_GROUPS = (
    ("鸣潮 / Wuthering Waves", ("鸣潮", "wuthering waves", "wuwa")),
    ("绝区零 / Zenless Zone Zero", ("绝区零", "zenless zone zero", "zzz")),
    ("原神 / Genshin Impact", ("原神", "genshin impact", "genshin")),
    (
        "崩坏：星穹铁道 / Honkai: Star Rail",
        (
            "崩坏：星穹铁道",
            "崩坏星穹铁道",
            "星穹铁道",
            "honkai star rail",
            "star rail",
            "hsr",
        ),
    ),
    ("崩坏3 / Honkai Impact 3rd", ("崩坏3", "崩坏三", "honkai impact 3rd", "hi3")),
    ("碧蓝档案 / Blue Archive", ("碧蓝档案", "blue archive", "ba")),
    ("明日方舟 / Arknights", ("明日方舟", "arknights")),
    ("胜利女神：妮姬 / NIKKE", ("胜利女神妮姬", "胜利女神", "nikke")),
    ("碧蓝航线 / Azur Lane", ("碧蓝航线", "azur lane")),
    (
        "少女前线 / Girls' Frontline",
        ("少女前线", "girls frontline", "girls' frontline", "gfl"),
    ),
    (
        "少女前线2：追放 / Girls' Frontline 2",
        ("少女前线2", "少女前线2追放", "girls frontline 2", "gfl2"),
    ),
    ("光之美少女 / Pretty Cure", ("光之美少女", "pretty cure", "precure")),
)

LORA_CATEGORIES = (
    "character",
    "artist_style",
    "speed_sampling",
    "quality_enhancement",
    "detail_restoration",
    "composition_pose",
    "lighting_color",
    "background_environment",
    "clothing_concept",
    "mixed",
    "unclassified",
    "unknown",
)
FUNCTIONAL_LORA_CATEGORIES = (
    "speed_sampling",
    "quality_enhancement",
    "detail_restoration",
    "composition_pose",
    "lighting_color",
    "background_environment",
    "clothing_concept",
)

_LORA_ASSET_TYPES = frozenset(
    {
        "lora",
        "locon",
        "loha",
        "lokr",
        "lycoris",
        "dora",
        "glora",
        "oft",
    }
)
_NON_LORA_ASSET_TYPES = frozenset(
    {
        "checkpoint",
        "checkpoints",
        "ckpt",
        "unet",
        "diffusionmodel",
        "diffusionmodels",
        "diffusioncheckpoint",
        "embedding",
        "embeddings",
        "hypernetwork",
        "vae",
        "clip",
        "controlnet",
    }
)
_NON_LORA_DIRECTORY_NAMES = frozenset(
    {
        "checkpoints",
        "checkpoint",
        "unet",
        "diffusion_models",
        "diffusion-models",
        "diffusionmodels",
        "embeddings",
        "embedding",
        "vae",
        "controlnet",
    }
)


class LoraCatalogError(RuntimeError):
    """局域网 LoRA 清单不可用或格式无效。"""

    def __init__(self, user_message: str, detail: str = ""):
        self.user_message = user_message
        self.detail = detail
        super().__init__(detail or user_message)


@dataclass(frozen=True)
class LoraRecord:
    """一项可供 LLM 选择的 LoRA 元数据。"""

    name: str
    trigger_words: tuple[str, ...] = ()
    description: str = ""
    model_name: str = ""
    base_model: str = ""
    folder: str = ""
    file_path: str = ""
    preview_url: str = ""
    tags: tuple[str, ...] = ()
    favorite: bool = False
    sha256: str = ""
    source: str = "catalog"
    category: str = "unknown"
    aliases: tuple[str, ...] = ()
    character_name: str = ""
    source_work: str = ""
    from_civitai: bool = False
    source_fingerprint: str = ""


class _DirectoryLinkParser(HTMLParser):
    """从公开 HTTP 目录页提取 LoRA 文件链接。"""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag.casefold() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(unquote(href))


class _PlainTextParser(HTMLParser):
    """把 LoRA Manager 的 HTML 说明转换为适合 LLM 的纯文本。"""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


class LoraCatalogService:
    """带局域网限制、缓存和格式兼容的 LoRA 清单客户端。"""

    def __init__(self, settings: PluginSettings):
        self._settings = settings
        self._url = self._resolve_catalog_url(settings)
        self._manager_url = self._resolve_manager_url(settings)
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache: tuple[LoraRecord, ...] = ()
        self._cache_expires_at = 0.0
        self._lock = asyncio.Lock()
        self._last_manager_scan_at = 0.0
        self._manager_record_count = 0
        self._manager_items_by_name: dict[str, dict[str, Any]] = {}
        self._manager_items_by_hash: dict[str, dict[str, Any]] = {}
        self._last_source = "catalog"
        self._last_warning = ""
        self._record_overlay: Optional[
            Callable[[tuple[LoraRecord, ...]], tuple[LoraRecord, ...]]
        ] = None

    def set_record_overlay(
        self,
        overlay: Optional[Callable[[tuple[LoraRecord, ...]], tuple[LoraRecord, ...]]],
    ) -> None:
        """Install a semantic overlay that may enrich, but never add, records.

        The mandatory fresh ComfyUI list remains authoritative.  An overlay may
        add aliases, classifications or display metadata only when it returns
        the exact same canonical file-name set as the fresh catalog.
        """
        self._record_overlay = overlay
        self._cache = ()
        self._cache_expires_at = 0.0

    @staticmethod
    def _resolve_catalog_url(settings: PluginSettings) -> str:
        """解析自定义清单地址；空值时使用 ComfyUI object_info。"""
        raw = settings.lora_catalog_url.strip()
        if not raw:
            return f"{settings.comfyui_url.rstrip('/')}/object_info"
        parsed = urlparse(raw)
        if parsed.path in {"", "/"}:
            return f"{raw.rstrip('/')}/object_info"
        return raw

    @staticmethod
    def _resolve_manager_url(settings: PluginSettings) -> str:
        """解析 LoRA Manager 页面地址或 API 根地址。"""
        if not settings.enable_lora_manager:
            return ""
        raw = settings.lora_manager_url.strip()
        if not raw:
            return f"{settings.comfyui_url.rstrip('/')}/api/lm/loras"
        parsed = urlparse(raw)
        path = parsed.path.rstrip("/")
        if path.endswith("/api/lm/loras"):
            return raw.rstrip("/")
        if path.endswith("/loras") or path in {"", "/"}:
            origin = f"{parsed.scheme}://{parsed.netloc}"
            return f"{origin}/api/lm/loras"
        return raw.rstrip("/")

    def _validate_url(self, url: str, label: str = "LoRA 清单") -> None:
        """拒绝凭据、非 HTTP 协议及非内网 IP。"""
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise LoraCatalogError(f"{label}必须是有效的 HTTP/HTTPS 地址")
        if parsed.username or parsed.password:
            raise LoraCatalogError(f"{label} URL 不允许包含用户名或密码")
        if not self._settings.lora_lan_only:
            return
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError as exc:
            raise LoraCatalogError(
                f"启用局域网限制时，{label}必须填写 IP 地址，不能使用域名"
            ) from exc
        if not (address.is_private or address.is_loopback or address.is_link_local):
            raise LoraCatalogError(f"{label}地址不是私有局域网 IP")

    async def _get_session(self) -> aiohttp.ClientSession:
        """延迟创建 HTTP 会话。"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self._settings.lora_catalog_timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def list_loras(
        self, query: str = "", limit: Optional[int] = None, force_refresh: bool = False
    ) -> tuple[LoraRecord, ...]:
        """读取、搜索并限制 LoRA 清单结果。"""
        records = await self._get_records(force_refresh=force_refresh)
        if query.strip():
            records = self.search_records(records, query)
        effective_limit = limit or self._settings.lora_max_results
        return records[: max(1, effective_limit)]

    async def _get_records(
        self,
        force_refresh: bool,
        *,
        force_manager_scan: bool = False,
        require_manager: bool = False,
    ) -> tuple[LoraRecord, ...]:
        """返回缓存或重新读取远端清单。"""
        now = time.monotonic()
        if not force_refresh and self._cache and now < self._cache_expires_at:
            return self._cache
        async with self._lock:
            now = time.monotonic()
            if not force_refresh and self._cache and now < self._cache_expires_at:
                return self._cache
            records: tuple[LoraRecord, ...] = ()
            manager_records: tuple[LoraRecord, ...] = ()
            warning = ""
            if require_manager and not self._manager_url:
                raise LoraCatalogError("LoRA Manager 未启用，无法取得强制最新清单")
            if self._manager_url:
                try:
                    self._validate_url(self._manager_url, "LoRA Manager")
                    if force_refresh and (
                        force_manager_scan
                        or self._settings.lora_manager_scan_on_refresh
                    ):
                        await self._scan_manager(force=force_manager_scan)
                    manager_records = await self._fetch_manager_records()
                except LoraCatalogError as exc:
                    if require_manager:
                        raise LoraCatalogError(
                            "LoRA Manager 强制刷新失败",
                            exc.user_message,
                        ) from exc
                    warning = exc.user_message
            if require_manager and not manager_records:
                raise LoraCatalogError(
                    "LoRA Manager 刷新完成，但最新索引中没有 LoRA 文件"
                )

            actual_error: Optional[LoraCatalogError] = None
            try:
                self._validate_url(self._url)
                body, content_type = await self._fetch(self._url)
                records = self.parse_catalog(body, content_type)
            except LoraCatalogError as exc:
                actual_error = exc
                if require_manager:
                    raise LoraCatalogError(
                        "ComfyUI 实际可加载 LoRA 清单刷新失败",
                        exc.user_message,
                    ) from exc
                warning = (
                    f"{warning}；{exc.user_message}" if warning else exc.user_message
                )

            if not records:
                detail = (
                    actual_error.user_message
                    if actual_error
                    else "object_info 未提供 LoRA 选择项"
                )
                raise LoraCatalogError(
                    "ComfyUI 实际可加载 LoRA 清单为空，拒绝使用 Manager 缓存代替",
                    detail,
                )

            if manager_records and records:
                records = self._merge_catalogs(records, manager_records)
                self._last_source = "LoRA Manager + ComfyUI object_info"
            else:
                self._last_source = "ComfyUI object_info / custom catalog"
            if not records:
                raise LoraCatalogError("局域网清单中没有找到 LoRA 文件")
            records = self._apply_alias_rules(records)
            # Freeze the authoritative source fingerprint before semantic
            # aliases/categories are overlaid for search and display.
            from .lora_semantic import semantic_source_fingerprint

            records = tuple(
                replace(
                    record,
                    source_fingerprint=semantic_source_fingerprint(record),
                )
                for record in records
            )
            if self._record_overlay is not None:
                base_names = {
                    canonical_lora_name(record.name).casefold() for record in records
                }
                try:
                    overlaid = tuple(self._record_overlay(records))
                    overlay_names = {
                        canonical_lora_name(record.name).casefold()
                        for record in overlaid
                    }
                    if len(overlaid) != len(records) or overlay_names != base_names:
                        raise ValueError(
                            "semantic overlay changed the authoritative file set"
                        )
                    records = overlaid
                except Exception as exc:
                    overlay_warning = f"LoRA 语义索引暂不可用：{exc}"
                    warning = (
                        f"{warning}；{overlay_warning}" if warning else overlay_warning
                    )
            self._manager_record_count = len(manager_records)
            self._last_warning = warning
            self._cache = records
            self._cache_expires_at = now + self._settings.lora_cache_ttl
            return records

    async def refresh_for_operation(self) -> tuple[LoraRecord, ...]:
        """强制扫描 Manager 并读取全量最新索引，失败时禁止后续 LoRA 操作。"""
        return await self._get_records(
            force_refresh=True,
            force_manager_scan=True,
            require_manager=True,
        )

    async def _fetch(
        self,
        url: str,
        *,
        params: Optional[dict[str, Any]] = None,
        timeout: Optional[int] = None,
        max_bytes: int = MAX_CATALOG_BYTES,
        resource_label: str = "LoRA 清单",
    ) -> tuple[bytes, str]:
        """限制大小且禁止重定向地读取清单。"""
        byte_limit = min(MAX_CATALOG_BYTES, max(1, int(max_bytes)))
        session = await self._get_session()
        try:
            headers = {}
            if self._settings.api_token:
                headers["Authorization"] = f"Bearer {self._settings.api_token}"
            request_options: dict[str, Any] = {
                "params": params,
                "headers": headers,
                "allow_redirects": False,
            }
            if timeout:
                request_options["timeout"] = aiohttp.ClientTimeout(total=timeout)
            async with session.get(url, **request_options) as response:
                if 300 <= response.status < 400:
                    raise LoraCatalogError("LoRA 清单地址不允许 HTTP 重定向")
                if response.status >= 400:
                    raise LoraCatalogError(f"LoRA 清单返回 HTTP {response.status}")
                if (
                    response.content_length
                    and response.content_length > byte_limit
                ):
                    raise LoraCatalogError(
                        f"{resource_label}超过 {byte_limit // (1024 * 1024)}MB"
                    )
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    size += len(chunk)
                    if size > byte_limit:
                        raise LoraCatalogError(
                            f"{resource_label}超过 {byte_limit // (1024 * 1024)}MB"
                        )
                    chunks.append(chunk)
                return b"".join(chunks), response.headers.get("Content-Type", "")
        except asyncio.TimeoutError as exc:
            raise LoraCatalogError("读取局域网 LoRA 清单超时") from exc
        except aiohttp.ClientError as exc:
            raise LoraCatalogError("无法连接局域网 LoRA 清单", str(exc)) from exc

    @staticmethod
    def _normalized_manager_file_path(value: Any) -> str:
        text = unicodedata.normalize("NFKC", str(value or "")).strip()
        return re.sub(r"/+", "/", text.replace("\\", "/"))

    @staticmethod
    def _manager_paths_equal(left: str, right: str) -> bool:
        if re.match(r"^[A-Za-z]:/", left) and re.match(r"^[A-Za-z]:/", right):
            return left.casefold() == right.casefold()
        return left == right

    def _manager_preview_target(self, record: LoraRecord) -> str:
        """Resolve one current Manager-owned preview without exposing its path.

        The caller supplies a fresh catalog record, never a URL or server file
        path. Only the exact Manager item retained by the latest successful
        Manager + ComfyUI refresh may authorize the same-origin preview route.
        """

        current = {
            canonical_lora_name(item.name).casefold(): item for item in self._cache
        }.get(canonical_lora_name(record.name).casefold())
        if current is None:
            raise LoraCatalogError("LoRA 缩略图条目已过期，请刷新图库")
        expected_hash = str(current.sha256 or "").strip().casefold()
        supplied_hash = str(record.sha256 or "").strip().casefold()
        if expected_hash and supplied_hash and expected_hash != supplied_hash:
            raise LoraCatalogError("LoRA 缩略图指纹已变化，请刷新图库")

        item = self._manager_item_for(current)
        raw_preview = str(item.get("preview_url") or "").strip()
        if not raw_preview:
            raise LoraCatalogError("LoRA Manager 未提供可验证的缩略图")

        manager = urlparse(self._manager_url)
        candidate = urlparse(raw_preview)
        if (
            candidate.fragment
            or candidate.username
            or candidate.password
            or raw_preview.startswith("//")
        ):
            raise LoraCatalogError("LoRA Manager 缩略图地址不安全")
        try:
            candidate_port = candidate.port
            manager_port = manager.port
        except ValueError as exc:
            raise LoraCatalogError("LoRA Manager 缩略图端口无效") from exc
        if candidate.scheme or candidate.netloc:
            if (
                candidate.scheme.casefold() != manager.scheme.casefold()
                or candidate.hostname != manager.hostname
                or candidate_port != manager_port
            ):
                raise LoraCatalogError("LoRA Manager 缩略图不是同源地址")
        if candidate.path != "/api/lm/previews":
            raise LoraCatalogError("LoRA Manager 缩略图接口不受支持")
        try:
            query = parse_qs(
                candidate.query,
                keep_blank_values=True,
                strict_parsing=True,
            )
        except ValueError as exc:
            raise LoraCatalogError("LoRA Manager 缩略图参数无效") from exc
        if set(query) != {"path"} or len(query["path"]) != 1:
            raise LoraCatalogError("LoRA Manager 缩略图参数不受支持")
        supplied_path = self._normalized_manager_file_path(query["path"][0])
        if not supplied_path:
            raise LoraCatalogError("LoRA Manager 缩略图路径为空")

        return urlunparse(
            (
                manager.scheme,
                manager.netloc,
                candidate.path,
                "",
                candidate.query,
                "",
            )
        )

    async def fetch_manager_preview(
        self,
        record: LoraRecord,
        *,
        max_bytes: int = MAX_MANAGER_PREVIEW_BYTES,
    ) -> tuple[bytes, str]:
        """Fetch one bounded same-origin preview from a fresh Manager record."""

        target = self._manager_preview_target(record)
        body, content_type = await self._fetch(
            target,
            timeout=self._settings.lora_catalog_timeout,
            max_bytes=min(MAX_MANAGER_PREVIEW_BYTES, max(1, int(max_bytes))),
            resource_label="LoRA 缩略图",
        )
        media_type = str(content_type or "").split(";", 1)[0].strip().casefold()
        if media_type not in {
            "image/jpeg",
            "image/png",
            "image/webp",
            "application/octet-stream",
        }:
            raise LoraCatalogError("LoRA Manager 缩略图响应类型不受支持")
        if not body:
            raise LoraCatalogError("LoRA Manager 缩略图为空")
        return body, media_type

    async def _scan_manager(self, *, force: bool = False) -> None:
        """按最短间隔触发 LoRA Manager 重新扫描磁盘索引。"""
        now = time.monotonic()
        interval = self._settings.lora_manager_scan_interval
        if not force and interval and now - self._last_manager_scan_at < interval:
            return
        assert self._manager_url
        body, _ = await self._fetch(
            f"{self._manager_url}/scan",
            timeout=self._settings.lora_manager_scan_timeout,
        )
        if body:
            try:
                payload = json.loads(body.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict) and payload.get("success") is False:
                raise LoraCatalogError(
                    "LoRA Manager 扫描失败",
                    str(payload.get("error") or payload),
                )
        self._last_manager_scan_at = now

    async def _fetch_manager_records(self) -> tuple[LoraRecord, ...]:
        """分页读取 LoRA Manager 的全部模型索引。"""
        assert self._manager_url
        page = 1
        total_pages = 1
        records: list[LoraRecord] = []
        raw_items: list[dict[str, Any]] = []
        while page <= total_pages:
            if page > 100:
                raise LoraCatalogError("LoRA Manager 分页数量异常")
            body, _ = await self._fetch(
                f"{self._manager_url}/list",
                params={
                    "page": page,
                    "page_size": self._settings.lora_manager_page_size,
                    "sort_by": "name",
                },
            )
            try:
                payload = json.loads(body.decode("utf-8", errors="replace"))
            except json.JSONDecodeError as exc:
                raise LoraCatalogError("LoRA Manager 返回了无效 JSON") from exc
            if not isinstance(payload, dict) or not isinstance(
                payload.get("items"), list
            ):
                raise LoraCatalogError("LoRA Manager 列表格式不受支持")
            raw_items.extend(
                dict(item) for item in payload["items"] if isinstance(item, dict)
            )
            records.extend(
                record
                for item in payload["items"]
                if (record := self._record_from_manager_item(item)) is not None
            )
            try:
                total_pages = max(1, int(payload.get("total_pages", 1)))
            except (TypeError, ValueError):
                total_pages = 1
            page += 1
        self._manager_items_by_name = {}
        self._manager_items_by_hash = {}
        for item in raw_items:
            parsed = self._record_from_manager_item(item)
            if parsed is not None:
                self._manager_items_by_name[
                    canonical_lora_name(parsed.name).casefold()
                ] = item
            digest = str(item.get("sha256") or "").strip().casefold()
            if digest:
                self._manager_items_by_hash[digest] = item
        return self._deduplicate(records)

    def _manager_item_for(self, record: LoraRecord) -> dict[str, Any]:
        digest = str(record.sha256 or "").strip().casefold()
        if digest and digest in self._manager_items_by_hash:
            return dict(self._manager_items_by_hash[digest])
        key = canonical_lora_name(record.name).casefold()
        direct = self._manager_items_by_name.get(key)
        if direct is not None:
            return dict(direct)
        basename = key.rsplit("/", 1)[-1]
        matches = [
            item
            for name, item in self._manager_items_by_name.items()
            if name.rsplit("/", 1)[-1] == basename
        ]
        return dict(matches[0]) if len(matches) == 1 else {}

    async def get_detail_v2(self, record: LoraRecord) -> Any:
        """Fetch allow-listed Manager detail and build a source-aware dossier.

        Callers must obtain ``record`` from the current fresh catalog first.
        Individual endpoint failures are represented in metadata health rather
        than silently pretending that enrichment succeeded.
        """
        from .lora_detail import LoraDetailAggregator

        manager_list = self._manager_item_for(record)
        manager_metadata: Any = None
        model_description: Any = None
        usage_tips: Any = None
        source_errors: dict[str, str] = {}
        if not self._manager_url or not record.file_path:
            source_errors["manager_metadata"] = "LoRA Manager detail unavailable"
            return LoraDetailAggregator.aggregate(
                record,
                manager_list=manager_list or None,
                source_errors=source_errors,
            )

        endpoints = (
            (
                "manager_metadata",
                f"{self._manager_url}/metadata",
                {"file_path": record.file_path},
            ),
            (
                "model_description",
                f"{self._manager_url}/model-description",
                {"file_path": record.file_path},
            ),
            (
                "usage_tips",
                f"{self._manager_url}/usage-tips-by-path",
                {"relative_path": record.name},
            ),
        )
        values: dict[str, Any] = {}

        async def fetch_detail(
            source: str,
            url: str,
            params: dict[str, Any],
        ) -> tuple[str, Any, str]:
            try:
                body, _ = await self._fetch(url, params=params)
                return (
                    source,
                    json.loads(body.decode("utf-8", errors="replace")),
                    "",
                )
            except (LoraCatalogError, json.JSONDecodeError) as exc:
                return source, None, getattr(exc, "user_message", str(exc))

        results = await asyncio.gather(
            *(fetch_detail(source, url, params) for source, url, params in endpoints)
        )
        for source, value, error in results:
            if error:
                source_errors[source] = error
            else:
                values[source] = value
        manager_metadata = values.get("manager_metadata")
        model_description = values.get("model_description")
        usage_tips = values.get("usage_tips")
        return LoraDetailAggregator.aggregate(
            record,
            manager_list=manager_list or None,
            manager_metadata=manager_metadata,
            model_description=model_description,
            usage_tips=usage_tips,
            source_errors=source_errors,
        )

    @staticmethod
    def _normalized_asset_type(value: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())

    @classmethod
    def _manager_item_is_lora(cls, item: Mapping[str, Any]) -> bool:
        """Require directory or typed evidence before trusting a Manager row.

        A Manager route name is not sufficient evidence: stale or incorrectly
        configured caches can contain checkpoints, UNets or diffusion models.
        Explicit non-LoRA type evidence always wins over a misleading path.
        """

        civitai = item.get("civitai") if isinstance(item.get("civitai"), dict) else {}
        model = civitai.get("model") if isinstance(civitai.get("model"), dict) else {}
        raw_types = (
            item.get("model_type"),
            item.get("type"),
            item.get("sub_type"),
            item.get("asset_type"),
            model.get("type"),
        )
        normalized_types = {
            normalized
            for raw_type in raw_types
            if (normalized := cls._normalized_asset_type(raw_type))
        }
        if normalized_types & _NON_LORA_ASSET_TYPES:
            return False
        if normalized_types & _LORA_ASSET_TYPES:
            return True

        raw_paths = (
            item.get("file_path"),
            item.get("path"),
            item.get("folder"),
            item.get("relative_path"),
        )
        saw_lora_directory = False
        for raw_path in raw_paths:
            segments = tuple(
                segment.casefold()
                for segment in re.split(r"[\\/]+", str(raw_path or ""))
                if segment
            )
            if any(segment in _NON_LORA_DIRECTORY_NAMES for segment in segments):
                return False
            if any(segment in {"lora", "loras"} for segment in segments):
                saw_lora_directory = True
        return saw_lora_directory

    @classmethod
    def _record_from_manager_item(cls, item: Any) -> Optional[LoraRecord]:
        """把 ComfyUI-Lora-Manager 列表项转换为统一记录。"""
        if not isinstance(item, dict):
            return None
        if not cls._manager_item_is_lora(item):
            return None
        file_name = str(item.get("file_name") or "").strip()
        file_path = str(item.get("file_path") or "").strip()
        if not file_name and file_path:
            file_name = file_path.replace("\\", "/").rsplit("/", 1)[-1]
        file_name = canonical_lora_name(file_name)
        if not file_name:
            return None
        folder = str(item.get("folder") or "").strip().strip("/\\")
        name = f"{folder}/{file_name}" if folder else file_name

        civitai = item.get("civitai") if isinstance(item.get("civitai"), dict) else {}
        raw_triggers = (
            item.get("trigger_words")
            or item.get("trained_words")
            or civitai.get("trainedWords")
            or []
        )
        triggers = cls._as_string_tuple(raw_triggers)
        tags = cls._as_string_tuple(item.get("tags"))
        auto_tags = cls._as_string_tuple(item.get("auto_tags"))
        tags = tuple(dict.fromkeys((*tags, *auto_tags)))
        usage_tips = str(item.get("usage_tips") or "").strip()
        if usage_tips in {"{}", "[]", "null"}:
            usage_tips = ""
        notes = str(item.get("notes") or "").strip()
        description = notes or usage_tips
        model_name = str(item.get("model_name") or civitai.get("name") or "").strip()
        category = cls._infer_category(
            file_name=file_name,
            model_name=model_name,
            tags=tags,
            trigger_words=triggers,
        )
        aliases, character_name, source_work = cls._extract_identity_metadata(
            file_name=file_name,
            model_name=model_name,
            tags=tags,
            trigger_words=triggers,
            category=category,
        )
        return LoraRecord(
            name=name,
            trigger_words=triggers,
            description=description,
            model_name=model_name,
            base_model=str(item.get("base_model") or "").strip(),
            folder=folder,
            file_path=file_path,
            preview_url=str(item.get("preview_url") or "").strip(),
            tags=tags,
            favorite=bool(item.get("favorite", False)),
            sha256=str(item.get("sha256") or "").strip(),
            source="lora-manager",
            category=category,
            aliases=aliases,
            character_name=character_name,
            source_work=source_work,
            from_civitai=bool(item.get("from_civitai") or civitai),
        )

    @staticmethod
    def _as_string_tuple(value: Any) -> tuple[str, ...]:
        """清洗字符串、逗号分隔文本或字符串数组。"""
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        if isinstance(value, (list, tuple, set)):
            result: list[str] = []
            for part in value:
                if isinstance(part, dict):
                    part = part.get("name") or part.get("tag") or ""
                text = str(part).strip()
                if text:
                    result.append(text)
            return tuple(result)
        return ()

    @staticmethod
    def _clean_identity_text(value: Any) -> str:
        text = unicodedata.normalize("NFKC", str(value or ""))
        text = text.replace(r"\(", "(").replace(r"\)", ")")
        text = text.replace("（", "(").replace("）", ")")
        return re.sub(r"\s+", " ", text).strip(" ,|>/;；")

    @classmethod
    def _normalize_search_text(cls, value: Any) -> str:
        text = cls._clean_identity_text(value).casefold()
        text = re.sub(r"[_/|>\\:;；,，·]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def _compact_search_text(cls, value: Any) -> str:
        return "".join(
            character
            for character in cls._normalize_search_text(value)
            if character.isalnum() or "\u3400" <= character <= "\u9fff"
        )

    @classmethod
    def _is_generic_identity(cls, value: str) -> bool:
        normalized = cls._normalize_search_text(value)
        if not normalized or normalized in GENERIC_IDENTITY_TERMS:
            return True
        if re.fullmatch(
            r"(?:v|ver|version|epoch|step)?[\d._-]+",
            normalized,
        ):
            return True
        tokens = set(normalized.split())
        return bool(tokens) and tokens <= GENERIC_IDENTITY_TERMS

    @classmethod
    def _dedupe_identity_terms(
        cls,
        values: Any,
        *,
        limit: int = 40,
    ) -> tuple[str, ...]:
        result: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = cls._clean_identity_text(raw)
            if not 2 <= len(value) <= 160 or cls._is_generic_identity(value):
                continue
            key = cls._compact_search_text(value)
            if len(key) < 2 or key in seen:
                continue
            seen.add(key)
            result.append(value)
            if len(result) >= limit:
                break
        return tuple(result)

    @classmethod
    def _detect_work_groups(cls, value: Any) -> tuple[str, ...]:
        normalized = cls._normalize_search_text(value)
        if not normalized:
            return ()
        matches: list[tuple[int, str]] = []
        for display_name, raw_aliases in WORK_ALIAS_GROUPS:
            for raw_alias in raw_aliases:
                alias = cls._normalize_search_text(raw_alias)
                if not alias:
                    continue
                if alias.isascii() and len(alias) <= 4:
                    pattern = rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])"
                    matched = re.search(pattern, normalized) is not None
                else:
                    matched = alias in normalized
                if matched:
                    matches.append((len(alias), display_name))
                    break
        if not matches:
            return ()
        return (max(matches, key=lambda item: item[0])[1],)

    @classmethod
    def _work_aliases(cls, work_names: tuple[str, ...]) -> tuple[str, ...]:
        selected = set(work_names)
        aliases: list[str] = []
        for display_name, raw_aliases in WORK_ALIAS_GROUPS:
            if display_name in selected:
                aliases.extend((display_name, *raw_aliases))
        return cls._dedupe_identity_terms(aliases)

    @classmethod
    def _strip_known_works(cls, value: Any) -> str:
        text = cls._clean_identity_text(value)
        raw_aliases = sorted(
            (alias for _, aliases in WORK_ALIAS_GROUPS for alias in aliases),
            key=len,
            reverse=True,
        )
        for alias in raw_aliases:
            if alias.isascii() and len(alias) <= 4:
                pattern = rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])"
            else:
                pattern = re.escape(alias)
            text = re.sub(pattern, " ", text, flags=re.IGNORECASE)
        text = re.sub(
            r"(?i)^\s*(?:character|characters|角色)\s*[:>\-/]*\s*",
            "",
            text,
        )
        text = re.sub(r"\(\s*\)", " ", text)
        return cls._clean_identity_text(text)

    @classmethod
    def _clean_character_candidate(cls, value: Any) -> str:
        text = cls._strip_known_works(value)
        text = re.sub(r"\([^()]*\)", " ", text)
        text = cls._clean_identity_text(text)
        if cls._is_generic_identity(text):
            return ""
        return text

    @classmethod
    def _extract_identity_metadata(
        cls,
        *,
        file_name: str,
        model_name: str,
        tags: tuple[str, ...],
        trigger_words: tuple[str, ...],
        category: str,
    ) -> tuple[tuple[str, ...], str, str]:
        """Build logical archive fields and aliases from Civitai metadata."""
        title = cls._clean_identity_text(model_name)
        file_text = cls._clean_identity_text(
            canonical_lora_name(file_name).rsplit("/", 1)[-1]
        )
        raw_file_words = re.sub(r"[_.-]+", " ", file_text)
        file_tokens: list[str] = []
        for raw_token in raw_file_words.split():
            token = re.sub(
                r"(?i)(?:v|ver|version)\d+(?:[._-]\d+)*$",
                "",
                raw_token,
            ).strip()
            if not token or re.fullmatch(r"\d+", token):
                continue
            if re.fullmatch(
                r"(?i)(?:anima|lora|model|preview|epoch\d*|step\d*|base\d*)",
                token,
            ):
                continue
            file_tokens.append(token)
        file_words = " ".join(file_tokens)

        aliases: list[str] = [file_text, file_words, title]
        character_candidates: list[str] = []
        work_candidates: list[str] = []
        identity_hint_blob = cls._compact_search_text(f"{title} {file_words}")

        identity_sources = (title, *trigger_words)
        for source_index, source in enumerate(identity_sources):
            source = cls._clean_identity_text(source)
            aliases.append(source)
            for match in re.finditer(
                r"([^()]*)\(([^()]{2,100})\)",
                source,
            ):
                raw_name = re.split(r"[|>/;；]", match.group(1))[-1]
                name = cls._clean_character_candidate(raw_name)
                parenthetical = cls._clean_identity_text(match.group(2))
                detected_works = cls._detect_work_groups(parenthetical)
                aliases.extend((name, parenthetical))
                name_key = cls._compact_search_text(name)
                if name and (
                    source_index == 0
                    or (name_key and name_key in identity_hint_blob)
                ):
                    character_candidates.append(name)
                work_candidates.extend(detected_works)

        for segment in re.split(r"[|>/;；]", title):
            aliases.append(segment)
            work_candidates.extend(cls._detect_work_groups(segment))
            if "(" not in segment:
                candidate = cls._clean_character_candidate(segment)
                if candidate:
                    character_candidates.append(candidate)
        for trigger in trigger_words:
            cleaned = cls._clean_identity_text(trigger).rstrip(",")
            subject = cls._clean_identity_text(cleaned.split(",", 1)[0])
            aliases.extend((cleaned, subject))
            detected_works = cls._detect_work_groups(cleaned)
            work_candidates.extend(detected_works)
            subject_without_work = cls._clean_character_candidate(subject)
            subject_key = cls._compact_search_text(subject_without_work)
            if subject_without_work and (
                subject_key and subject_key in identity_hint_blob
            ):
                character_candidates.append(subject_without_work)

        aliases.extend(tags)
        for tag in tags:
            work_candidates.extend(cls._detect_work_groups(tag))

        file_aliases = [token for token in file_tokens if len(token) >= 3]
        aliases.extend(file_aliases)

        if category not in {"character", "mixed"}:
            character_candidates = []
            work_candidates = []
        elif not character_candidates:
            fallback = cls._clean_character_candidate(file_words)
            if fallback:
                character_candidates.append(fallback)

        characters = cls._dedupe_identity_terms(
            character_candidates,
            limit=MAX_CHARACTER_IDENTITIES_PER_LORA,
        )
        works = cls._dedupe_identity_terms(
            work_candidates,
            limit=MAX_WORK_IDENTITIES_PER_LORA,
        )
        work_aliases = cls._work_aliases(works)
        aliases.extend((*characters, *works, *work_aliases))
        aliases.extend(
            f"{work_alias} {character}"
            for work_alias in work_aliases
            for character in characters
            if len(work_alias) + len(character) <= 80
        )
        return (
            cls._dedupe_identity_terms(aliases),
            " / ".join(characters),
            "；".join(works),
        )

    @classmethod
    def _search_score(cls, record: LoraRecord, query: str) -> int:
        query_normalized = cls._normalize_search_text(query)
        query_compact = cls._compact_search_text(query)
        if not query_compact:
            return 0
        weighted_fields = (
            (record.name, 100),
            (record.character_name, 98),
            *((alias, 95) for alias in record.aliases),
            (record.source_work, 92),
            (record.model_name, 88),
            *((trigger, 86) for trigger in record.trigger_words),
            *((tag, 72) for tag in record.tags),
            (record.description, 55),
        )
        best = 0
        for value, weight in weighted_fields:
            normalized = cls._normalize_search_text(value)
            compact = cls._compact_search_text(value)
            if not compact:
                continue
            if query_normalized == normalized:
                best = max(best, weight)
            elif query_compact == compact:
                best = max(best, weight - 2)
            elif normalized.startswith(query_normalized):
                best = max(best, weight - 8)
            elif len(query_compact) >= 2 and query_compact in compact:
                best = max(best, weight - 16)

        searchable_fields = (
            record.name,
            record.model_name,
            record.character_name,
            record.source_work,
            *record.aliases,
            *record.trigger_words,
            *record.tags,
            record.description,
        )
        searchable_compacts = tuple(
            compact
            for value in searchable_fields
            if (compact := cls._compact_search_text(value))
        )
        searchable_compact = "".join(searchable_compacts)
        query_tokens = tuple(
            token
            for token in re.split(r"\s+", query_normalized)
            if cls._compact_search_text(token)
        )
        if query_tokens and all(
            any(
                cls._compact_search_text(token) in field
                for field in searchable_compacts
            )
            for token in query_tokens
        ):
            best = max(best, 88 if len(query_tokens) > 1 else 70)
        if query_compact in searchable_compact:
            best = max(best, 58)
        if best and record.favorite:
            best += 2
        return best

    @classmethod
    def _strict_character_identity_matches(
        cls,
        record: LoraRecord,
        query: str,
    ) -> bool:
        """Require character evidence before fuzzy-selecting a character LoRA.

        Catalog aliases also contain works and descriptive tags for discovery.
        Those broad fields may rank a record, but they must not authorize a
        strict character selection on their own.
        """
        query_normalized = cls._normalize_search_text(query)
        query_compact = cls._compact_search_text(query)
        if not query_compact or cls._is_generic_identity(query):
            return False

        work_keys = {
            cls._compact_search_text(part)
            for part in re.split(r"[/|;；]+", record.source_work)
            if cls._compact_search_text(part)
        }
        if source_work_key := cls._compact_search_text(record.source_work):
            work_keys.add(source_work_key)
        for work in cls._detect_work_groups(record.source_work):
            work_keys.update(
                cls._compact_search_text(alias)
                for alias in cls._work_aliases((work,))
            )
        if query_compact in work_keys:
            return False

        def value_matches(value: Any, *, exact: bool = False) -> bool:
            normalized = cls._normalize_search_text(value)
            compact = cls._compact_search_text(value)
            if not compact:
                return False
            if query_normalized == normalized or query_compact == compact:
                return True
            if exact:
                return False
            if normalized.isascii():
                pattern = rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])"
                return re.search(pattern, query_normalized) is not None
            return normalized in query_normalized

        character_terms = cls._dedupe_identity_terms(
            cls._clean_character_candidate(part)
            for part in re.split(r"[/|;；]+", record.character_name)
        )
        character_keys = {
            cls._compact_search_text(term) for term in character_terms
        }
        file_identity_key = cls._compact_search_text(
            canonical_lora_name(record.name).rsplit("/", 1)[-1]
        )
        trigger_entries: list[tuple[str, str, str, str]] = []
        trigger_candidate_keys: set[str] = set()
        for trigger in record.trigger_words:
            cleaned = cls._clean_identity_text(trigger).rstrip(",")
            subject = cls._clean_identity_text(cleaned.split(",", 1)[0])
            candidate = cls._clean_character_candidate(subject)
            candidate_key = cls._compact_search_text(candidate)
            trigger_entries.append((cleaned, subject, candidate, candidate_key))
            if candidate_key:
                trigger_candidate_keys.add(candidate_key)

        trusted_character_keys = {
            key
            for key in character_keys
            if key
            and (
                key in file_identity_key
                or file_identity_key in key
                or key in trigger_candidate_keys
            )
        }
        untrusted_character_keys = character_keys - trusted_character_keys
        if any(
            cls._compact_search_text(term) in trusted_character_keys
            and value_matches(term)
            for term in character_terms
        ):
            return True

        verified_trigger_subjects: list[str] = []
        trigger_alias_keys: set[str] = set()
        for cleaned, subject, candidate, candidate_key in trigger_entries:
            trigger_alias_keys.update(
                key
                for key in (
                    cls._compact_search_text(cleaned),
                    cls._compact_search_text(subject),
                )
                if key
            )
            if not candidate_key:
                continue
            known_character = candidate_key in trusted_character_keys
            if known_character:
                verified_trigger_subjects.append(candidate)
                if value_matches(cleaned, exact=True) or value_matches(subject):
                    return True

        broad_tag_keys = {
            cls._compact_search_text(tag) for tag in record.tags if str(tag).strip()
        }
        verified_trigger_keys = {
            cls._compact_search_text(subject)
            for subject in verified_trigger_subjects
        }
        for alias in record.aliases:
            if not value_matches(alias, exact=True):
                continue
            alias_key = cls._compact_search_text(alias)
            if not alias_key or alias_key in work_keys:
                continue
            if alias_key in untrusted_character_keys:
                alias_text = cls._normalize_search_text(alias)
                if (
                    not trusted_character_keys
                    or alias_text.isascii()
                    or len(alias_text) > 32
                    or " " in alias_text
                ):
                    continue
            is_identity_related = any(
                key and (key in alias_key or alias_key in key)
                for key in (*trusted_character_keys, *verified_trigger_keys)
            )
            if alias_key in trigger_alias_keys and not is_identity_related:
                continue
            if alias_key in broad_tag_keys and not is_identity_related:
                continue
            return True
        return False

    @classmethod
    def _record_requires_strict_character_identity(cls, record: LoraRecord) -> bool:
        category = str(record.category or "").strip().casefold()
        return bool(
            category in {"character", "mixed"}
            or record.character_name.strip()
            or any(
                cls._normalize_search_text(tag) == "character"
                for tag in record.tags
            )
        )

    @classmethod
    def search_records(
        cls,
        records: tuple[LoraRecord, ...],
        query: str,
    ) -> tuple[LoraRecord, ...]:
        return tuple(record for _score, record in cls.rank_records(records, query))

    @classmethod
    def rank_records(
        cls,
        records: tuple[LoraRecord, ...],
        query: str,
    ) -> tuple[tuple[int, LoraRecord], ...]:
        """Return the deterministic lexical ranking together with its scores."""
        scored = [(cls._search_score(record, query), record) for record in records]
        return tuple(
            sorted(
                (item for item in scored if item[0] > 0),
                key=lambda item: (
                    -item[0],
                    item[1].category != "character",
                    item[1].name.casefold(),
                ),
            )
        )

    @staticmethod
    def archive_summary(records: tuple[LoraRecord, ...]) -> dict[str, Any]:
        categories = {category: 0 for category in LORA_CATEGORIES}
        works: dict[str, int] = {}
        civitai_enriched = 0
        identified_characters = 0
        for record in records:
            category = record.category
            categories[category if category in categories else "unknown"] += 1
            if record.from_civitai:
                civitai_enriched += 1
            if record.character_name:
                identified_characters += 1
            if record.source_work:
                for work in record.source_work.split("；"):
                    work = work.strip()
                    if work:
                        works[work] = works.get(work, 0) + 1
        return {
            "categories": categories,
            "civitai_enriched": civitai_enriched,
            "identified_characters": identified_characters,
            "works": [
                {"name": name, "count": count}
                for name, count in sorted(
                    works.items(),
                    key=lambda item: (-item[1], item[0].casefold()),
                )
            ],
        }

    def _apply_alias_rules(
        self,
        records: tuple[LoraRecord, ...],
    ) -> tuple[LoraRecord, ...]:
        rules = getattr(self._settings, "lora_alias_rules", ())
        if not rules:
            return records
        index = {
            canonical_lora_name(record.name).casefold(): position
            for position, record in enumerate(records)
        }
        basename_index: dict[str, list[int]] = {}
        for position, record in enumerate(records):
            basename = canonical_lora_name(record.name).casefold().rsplit("/", 1)[-1]
            basename_index.setdefault(basename, []).append(position)
        updated = list(records)
        for raw_rule in rules:
            text = str(raw_rule or "").strip()
            if "=" not in text:
                continue
            target, raw_aliases = text.split("=", 1)
            target_key = canonical_lora_name(target).casefold()
            position = index.get(target_key)
            if position is None:
                basename_matches = basename_index.get(
                    target_key.rsplit("/", 1)[-1],
                    [],
                )
                if len(basename_matches) == 1:
                    position = basename_matches[0]
            if position is None:
                continue
            aliases = re.split(r"[,，|;；]+", raw_aliases)
            record = updated[position]
            updated[position] = replace(
                record,
                aliases=self._dedupe_identity_terms((*record.aliases, *aliases)),
            )
        return tuple(updated)

    @staticmethod
    def _infer_category(
        *,
        file_name: str,
        model_name: str,
        tags: tuple[str, ...],
        trigger_words: tuple[str, ...],
    ) -> str:
        """Classify identity, style and functional LoRAs from metadata evidence."""
        tokens = {str(tag).strip().casefold() for tag in tags if str(tag).strip()}
        triggers = {
            str(trigger).strip().casefold()
            for trigger in trigger_words
            if str(trigger).strip()
        }
        raw_combined = re.sub(
            r"([a-z0-9])([A-Z])",
            r"\1 \2",
            " ".join((file_name, model_name, *tags, *trigger_words)),
        )
        combined = re.sub(
            r"[_\-./\\]+",
            " ",
            raw_combined.casefold(),
        )

        def has_signal(*signals: str) -> bool:
            return any(
                signal in tokens
                or signal in triggers
                or re.search(
                    rf"(?<![a-z0-9]){re.escape(signal)}(?![a-z0-9])",
                    combined,
                )
                is not None
                for signal in signals
            )

        is_character = bool(
            tokens
            & {
                "character",
                "characters",
                "anime character",
                "game character",
                "person",
            }
        ) or any(marker in combined for marker in ("character", "角色"))
        is_artist_style = bool(
            tokens
            & {
                "artist",
                "artists",
                "art style",
                "painting style",
            }
        ) or any(word.strip().startswith("@") for word in trigger_words)
        if any(marker in combined for marker in (" artist", "画风", "画师")):
            is_artist_style = True
        is_style = is_artist_style or bool(tokens & {"style", "styles"})
        if "style" in combined:
            is_style = True
        if is_character and is_artist_style:
            return "mixed"
        if is_character:
            return "character"

        functional_signals = (
            (
                "speed_sampling",
                (
                    "acceleration",
                    "accelerator",
                    "fast sampling",
                    "few step",
                    "low step",
                    "lightning",
                    "hyper",
                    "turbo",
                    "lcm",
                    "dmd2",
                    "pcm",
                    "distillation",
                    "sampling helper",
                    "采样加速",
                    "加速",
                ),
            ),
            (
                "quality_enhancement",
                (
                    "quality enhancement",
                    "quality boost",
                    "quality",
                    "highres",
                    "high resolution",
                    "aesthetic boost",
                    "aesthetic",
                    "masterpiece",
                    "masterpieces",
                    "image quality",
                    "画质",
                    "质量",
                    "美感",
                    "质量增强",
                    "美感增强",
                ),
            ),
            (
                "detail_restoration",
                (
                    "detail restoration",
                    "detail repair",
                    "detail enhancer",
                    "skin detail",
                    "real skin",
                    "skin texture",
                    "shiny skin",
                    "face detail",
                    "hand fix",
                    "eye detail",
                    "restoration",
                    "fixer",
                    "细节修复",
                    "细节增强",
                    "皮肤细节",
                    "手部修复",
                ),
            ),
            (
                "composition_pose",
                (
                    "composition",
                    "dynamic pose",
                    "pose helper",
                    "camera angle",
                    "perspective",
                    "framing",
                    "anatomy pose",
                    "构图",
                    "姿势",
                    "镜头角度",
                    "透视",
                ),
            ),
            (
                "lighting_color",
                (
                    "lighting",
                    "light concept",
                    "cinematic light",
                    "color grading",
                    "colour grading",
                    "color palette",
                    "tone mapping",
                    "illumination",
                    "光影",
                    "灯光",
                    "色彩",
                    "调色",
                ),
            ),
            (
                "background_environment",
                (
                    "photo background",
                    "background environment",
                    "background helper",
                    "environment background",
                    "scene background",
                    "background pack",
                    "写真背景",
                    "摄影背景",
                    "背景环境",
                    "场景背景",
                ),
            ),
            (
                "clothing_concept",
                (
                    "clothing",
                    "outfit",
                    "costume",
                    "uniform",
                    "armor",
                    "dress concept",
                    "fashion concept",
                    "concept pack",
                    "服装",
                    "换装",
                    "制服",
                    "概念设计",
                ),
            ),
        )
        for category, signals in functional_signals:
            if has_signal(*signals):
                return category
        if is_style:
            return "artist_style"
        return "unknown"

    @classmethod
    def _merge_catalogs(
        cls,
        actual_records: tuple[LoraRecord, ...],
        manager_records: tuple[LoraRecord, ...],
    ) -> tuple[LoraRecord, ...]:
        """以 object_info 的真实名称为准，合并 LoRA Manager 元数据。"""
        manager_by_name = {
            canonical_lora_name(record.name).casefold(): record
            for record in manager_records
        }
        basename_candidates: dict[str, list[LoraRecord]] = {}
        for record in manager_records:
            key = canonical_lora_name(record.name).casefold()
            basename_candidates.setdefault(key.rsplit("/", 1)[-1], []).append(record)

        merged: list[LoraRecord] = []
        for actual in actual_records:
            key = canonical_lora_name(actual.name).casefold()
            metadata = manager_by_name.get(key)
            if metadata is None:
                candidates = basename_candidates.get(key.rsplit("/", 1)[-1], [])
                if len(candidates) == 1:
                    metadata = candidates[0]
            if metadata is None:
                merged.append(actual)
                continue
            merged.append(cls._merge_record(actual, metadata))

        # Manager 元数据只能补充 ComfyUI 实际可加载清单，不能单独证明文件存在。
        # 删除文件后 Manager 缓存可能短暂残留；追加 manager-only 记录会让 LLM
        # 继续选择一个磁盘上已经不存在的 LoRA。
        return cls._deduplicate(merged)

    @classmethod
    def _merge_record(
        cls,
        primary: LoraRecord,
        metadata: LoraRecord,
    ) -> LoraRecord:
        """保留真实加载名并补齐管理器字段。"""
        return LoraRecord(
            name=primary.name,
            trigger_words=metadata.trigger_words or primary.trigger_words,
            description=metadata.description or primary.description,
            model_name=metadata.model_name or primary.model_name,
            base_model=metadata.base_model or primary.base_model,
            folder=metadata.folder or primary.folder,
            file_path=metadata.file_path or primary.file_path,
            preview_url=metadata.preview_url or primary.preview_url,
            tags=metadata.tags or primary.tags,
            favorite=metadata.favorite or primary.favorite,
            sha256=metadata.sha256 or primary.sha256,
            source=(
                "lora-manager+catalog"
                if metadata.source == "lora-manager"
                else primary.source
            ),
            category=(
                metadata.category
                if metadata.category != "unknown"
                else primary.category
            ),
            aliases=cls._dedupe_identity_terms((*primary.aliases, *metadata.aliases)),
            character_name=metadata.character_name or primary.character_name,
            source_work=metadata.source_work or primary.source_work,
            from_civitai=metadata.from_civitai or primary.from_civitai,
        )

    @classmethod
    def parse_catalog(
        cls, body: bytes, content_type: str = ""
    ) -> tuple[LoraRecord, ...]:
        """解析 ComfyUI JSON、通用 JSON、纯文本或 HTTP 目录页。"""
        text = body.decode("utf-8", errors="replace").strip()
        records: list[LoraRecord] = []
        parsed_json = False
        if "json" in content_type.casefold() or text.startswith(("{", "[")):
            try:
                payload = json.loads(text)
                parsed_json = True
                records.extend(cls._records_from_json(payload))
            except json.JSONDecodeError:
                pass
        if not records and "<a" in text.casefold():
            parser = _DirectoryLinkParser()
            parser.feed(text)
            records.extend(
                LoraRecord(name=link.split("?")[0].lstrip("/"))
                for link in parser.links
                if link.split("?")[0].casefold().endswith(LORA_EXTENSIONS)
            )
        if not records and not parsed_json:
            records.extend(cls._records_from_text(text))
        return cls._deduplicate(records)

    @classmethod
    def _records_from_json(cls, payload: Any) -> list[LoraRecord]:
        """从通用 JSON 或 ComfyUI object_info 提取 LoRA 条目。"""
        records: list[LoraRecord] = []
        if isinstance(payload, list):
            for item in payload:
                record = cls._record_from_item(item)
                if record:
                    records.append(record)
            return records
        if not isinstance(payload, dict):
            return records

        for key in ("loras", "files", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                records.extend(cls._records_from_json(value))

        for key, value in payload.items():
            if str(key).casefold().endswith(LORA_EXTENSIONS):
                metadata = value if isinstance(value, dict) else {}
                records.append(cls._record_from_mapping(str(key), metadata))

        for node_info in payload.values():
            records.extend(
                LoraRecord(name=value)
                for value in cls._find_object_info_lora_filenames(node_info)
            )
        return records

    @classmethod
    def _find_object_info_lora_filenames(cls, node_info: Any) -> list[str]:
        """Read only file choices attached to explicit LoRA input fields.

        Scanning an entire node whose display name merely contains ``lora`` can
        accidentally collect UNET/checkpoint choices from hybrid custom nodes.
        ComfyUI input field names are stronger type evidence than node labels.
        """

        if not isinstance(node_info, dict):
            return []
        input_spec = node_info.get("input")
        if not isinstance(input_spec, dict):
            return []
        found: list[str] = []
        for section_name in ("required", "optional"):
            section = input_spec.get(section_name)
            if not isinstance(section, dict):
                continue
            for field_name, field_spec in section.items():
                normalized = re.sub(r"[^a-z0-9]+", "", str(field_name or "").casefold())
                if not (
                    normalized in {"lora", "loras", "loraname", "lorapath"}
                    or normalized.startswith("loraname")
                    or re.fullmatch(r"lora\d+", normalized)
                ):
                    continue
                found.extend(cls._find_lora_filenames(field_spec))
        return found

    @classmethod
    def _find_lora_filenames(cls, value: Any) -> list[str]:
        """递归寻找 object_info 中的 LoRA 文件名。"""
        found: list[str] = []
        if isinstance(value, str) and value.casefold().endswith(LORA_EXTENSIONS):
            found.append(value)
        elif isinstance(value, list):
            for item in value:
                found.extend(cls._find_lora_filenames(item))
        elif isinstance(value, dict):
            for item in value.values():
                found.extend(cls._find_lora_filenames(item))
        return found

    @classmethod
    def _record_from_item(cls, item: Any) -> Optional[LoraRecord]:
        """解析字符串或对象形式的单条记录。"""
        if isinstance(item, str):
            return LoraRecord(name=item.strip()) if item.strip() else None
        if not isinstance(item, dict):
            return None
        if item.get("file_name") or item.get("model_name"):
            manager_record = cls._record_from_manager_item(item)
            if manager_record:
                return manager_record
        name = item.get("name") or item.get("filename") or item.get("path")
        if not name:
            return None
        return cls._record_from_mapping(str(name), item)

    @staticmethod
    def _record_from_mapping(name: str, item: dict[str, Any]) -> LoraRecord:
        """从元数据对象读取触发词和描述。"""
        raw_triggers = (
            item.get("trigger_words")
            or item.get("triggers")
            or item.get("trainedWords")
            or []
        )
        triggers = LoraCatalogService._as_string_tuple(raw_triggers)
        description = str(item.get("description") or item.get("desc") or "").strip()
        return LoraRecord(
            name=name.strip(), trigger_words=triggers, description=description
        )

    @staticmethod
    def _records_from_text(text: str) -> list[LoraRecord]:
        """解析每行一个文件，或 `名称|触发词|描述` 格式。"""
        records: list[LoraRecord] = []
        for raw_line in text.splitlines():
            line = raw_line.strip().lstrip("-* ")
            if not line or line.startswith("#"):
                continue
            parts = [part.strip() for part in line.split("|", 2)]
            name = parts[0]
            if not name:
                continue
            triggers = (
                tuple(part.strip() for part in parts[1].split(",") if part.strip())
                if len(parts) > 1
                else ()
            )
            description = parts[2] if len(parts) > 2 else ""
            records.append(
                LoraRecord(name=name, trigger_words=triggers, description=description)
            )
        return records

    @staticmethod
    def _deduplicate(records: list[LoraRecord]) -> tuple[LoraRecord, ...]:
        """按规范名称去重并稳定排序。"""
        unique: dict[str, LoraRecord] = {}
        for record in records:
            name = record.name.strip()
            canonical = canonical_lora_name(name)
            if not canonical:
                continue
            key = canonical.casefold()
            current = unique.get(key)
            if current is None:
                unique[key] = LoraRecord(
                    name=name,
                    trigger_words=record.trigger_words,
                    description=record.description,
                    model_name=record.model_name,
                    base_model=record.base_model,
                    folder=record.folder,
                    file_path=record.file_path,
                    preview_url=record.preview_url,
                    tags=record.tags,
                    favorite=record.favorite,
                    sha256=record.sha256,
                    source=record.source,
                    category=record.category,
                    aliases=record.aliases,
                    character_name=record.character_name,
                    source_work=record.source_work,
                    from_civitai=record.from_civitai,
                )
            else:
                unique[key] = LoraCatalogService._merge_record(current, record)
        return tuple(sorted(unique.values(), key=lambda item: item.name.casefold()))

    async def resolve_selections_with_records(
        self,
        selections: tuple[LoraSelection, ...],
        strict: bool = True,
        *,
        records: Optional[tuple[LoraRecord, ...]] = None,
    ) -> tuple[tuple[LoraSelection, ...], dict[str, LoraRecord]]:
        """Resolve loadable names and return their latest authoritative records.

        The record mapping is keyed by the canonical resolved name.  A bare
        basename is deliberately rejected when the fresh catalog contains more
        than one file with that basename.  This prevents a legacy LoRA Manager
        name from silently loading the wrong character variant.
        """
        if not selections:
            return (), {}
        records = (
            tuple(records)
            if records is not None
            else await self._get_records(force_refresh=False)
        )
        index = {
            canonical_lora_name(record.name).casefold(): record for record in records
        }
        basename_index: dict[str, list[LoraRecord]] = {}
        for candidate in records:
            canonical = canonical_lora_name(candidate.name).casefold()
            basename_index.setdefault(canonical.rsplit("/", 1)[-1], []).append(
                candidate
            )
        manager_basename_names: dict[str, list[str]] = {}
        for manager_name in self._manager_items_by_name:
            basename = manager_name.rsplit("/", 1)[-1]
            manager_basename_names.setdefault(basename, []).append(manager_name)
        resolved: list[LoraSelection] = []
        resolved_records: dict[str, LoraRecord] = {}
        missing: list[str] = []
        ambiguous: dict[str, list[str]] = {}
        for selection in selections:
            requested = canonical_lora_name(selection.name)
            requested_key = requested.casefold()
            basename_matches = basename_index.get(
                requested_key.rsplit("/", 1)[-1], []
            )
            basename = requested_key.rsplit("/", 1)[-1]
            collision_names = manager_basename_names.get(basename) or [
                canonical_lora_name(candidate.name) for candidate in basename_matches
            ]
            record: Optional[LoraRecord] = None
            if "/" not in requested_key and len(set(collision_names)) > 1:
                ambiguous[selection.name] = list(dict.fromkeys(collision_names))[:5]
            else:
                record = index.get(requested_key)
                if record is None and len(basename_matches) == 1:
                    record = basename_matches[0]
            if record is None:
                candidates = sorted(
                    (
                        (self._search_score(candidate, selection.name), candidate)
                        for candidate in records
                        if not (
                            strict
                            and self._record_requires_strict_character_identity(
                                candidate
                            )
                            and not self._strict_character_identity_matches(
                                candidate,
                                selection.name,
                            )
                        )
                    ),
                    key=lambda item: (-item[0], item[1].name.casefold()),
                )
                candidates = [item for item in candidates if item[0] >= 70]
                if candidates and selection.name not in ambiguous:
                    top_score = candidates[0][0]
                    top_matches = [
                        candidate
                        for score, candidate in candidates
                        if score >= top_score - 5
                    ]
                    if len(top_matches) == 1:
                        record = top_matches[0]
                    else:
                        ambiguous[selection.name] = [
                            candidate.name for candidate in top_matches[:5]
                        ]
            if record is None:
                missing.append(selection.name)
                if not strict:
                    resolved.append(selection)
                continue
            if (
                len(set(collision_names)) > 1
                and "/" not in canonical_lora_name(record.name)
            ):
                ambiguous[selection.name] = list(dict.fromkeys(collision_names))[:5]
                missing.append(selection.name)
                continue
            resolved.append(
                LoraSelection(
                    name=canonical_lora_name(record.name),
                    strength=selection.strength,
                )
            )
            resolved_records[requested_key] = record
            resolved_records[canonical_lora_name(record.name).casefold()] = record
        if ambiguous:
            raise LoraCatalogError(
                "LoRA 简称或同名项匹配到多个文件，当前运行时无法唯一寻址；"
                "请将 LoRA Manager 的 "
                "lora_syntax_format 设为 full，并使用包含文件夹的精确名称",
                f"ambiguous={ambiguous}",
            )
        if missing and strict:
            raise LoraCatalogError(
                "LLM 选择了清单中不存在的 LoRA",
                f"missing={missing}",
            )
        return tuple(resolved), resolved_records

    async def resolve_selections(
        self,
        selections: tuple[LoraSelection, ...],
        strict: bool = True,
        *,
        records: Optional[tuple[LoraRecord, ...]] = None,
    ) -> tuple[LoraSelection, ...]:
        """把 LLM 名称解析为清单中的真实名称。"""
        resolved, _ = await self.resolve_selections_with_records(
            selections,
            strict=strict,
            records=records,
        )
        return resolved

    @staticmethod
    def _strip_html(value: str) -> str:
        """把 CivitAI HTML 描述压缩成纯文本。"""
        if not value:
            return ""
        parser = _PlainTextParser()
        try:
            parser.feed(value)
            parser.close()
        except Exception:
            return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()
        return parser.text()

    async def _enrich_manager_detail(self, record: LoraRecord) -> LoraRecord:
        """按需读取单个 LoRA 的完整管理器元数据。"""
        try:
            detail = await self.get_detail_v2(record)
        except (LoraCatalogError, ValueError):
            return record
        descriptions = tuple(
            value
            for value in (
                detail.model_description,
                detail.version_description,
                detail.local_notes,
            )
            if value
        )
        enriched = LoraRecord(
            name=record.name,
            trigger_words=detail.trigger_words or record.trigger_words,
            description="\n\n".join(descriptions) or record.description,
            model_name=detail.model_name or record.model_name,
            base_model=detail.base_model or record.base_model,
            folder=record.folder,
            file_path=record.file_path,
            preview_url=detail.preview_url or record.preview_url,
            tags=detail.tags or record.tags,
            favorite=detail.file_status.favorite or record.favorite,
            sha256=record.sha256,
            source=record.source,
            category=record.category,
            aliases=record.aliases,
            character_name=record.character_name,
            source_work=record.source_work,
            from_civitai=detail.file_status.from_civitai or record.from_civitai,
        )
        aliases, character_name, source_work = self._extract_identity_metadata(
            file_name=record.name,
            model_name=enriched.model_name,
            tags=enriched.tags,
            trigger_words=enriched.trigger_words,
            category=enriched.category,
        )
        return replace(
            enriched,
            aliases=self._dedupe_identity_terms((*record.aliases, *aliases)),
            character_name=character_name or record.character_name,
            source_work=source_work or record.source_work,
            from_civitai=True,
        )

    async def format_for_llm(
        self,
        query: str = "",
        limit: Optional[int] = None,
        *,
        force_refresh: bool = False,
        detail: bool = False,
    ) -> str:
        """返回适合函数工具结果的精简 LoRA 清单。"""
        records = await self.list_loras(
            query=query,
            limit=limit,
            force_refresh=force_refresh,
        )
        return await self.format_records_for_llm(
            records,
            force_refresh=force_refresh,
            detail=detail,
        )

    async def format_records_for_llm(
        self,
        records: tuple[LoraRecord, ...],
        *,
        force_refresh: bool = False,
        detail: bool = False,
        retrieval_info: Optional[Mapping[str, Any]] = None,
    ) -> str:
        """Format records from one authoritative refresh snapshot for the LLM."""
        if not records:
            return "No matching LoRA files were found. Continue without LoRA."
        if detail:
            records = tuple(
                [await self._enrich_manager_detail(record) for record in records[:5]]
                + list(records[5:])
            )
        lines = [
            (
                "Available Anima LoRAs. Search accepts character aliases, "
                "work titles and trigger words; use the returned exact file "
                "name in syntax <lora:name:weight>."
            ),
            (
                f"Catalog source: {self._last_source}; total={len(self._cache)}; "
                f"manager_metadata={self._manager_record_count}."
            ),
        ]
        if force_refresh:
            lines.append("The catalog was refreshed before this result.")
        if retrieval_info:
            mode = str(retrieval_info.get("mode") or "lexical")
            embedding_used = bool(retrieval_info.get("embedding_used"))
            rerank_used = bool(retrieval_info.get("rerank_used"))
            fallback = str(retrieval_info.get("fallback_code") or "")
            line = (
                f"Search mode: {mode}; embedding={embedding_used}; "
                f"rerank={rerank_used}."
            )
            if fallback:
                line += f" Safe fallback: {fallback}."
            lines.append(line)
        if self._last_warning:
            lines.append(f"Catalog warning: {self._last_warning}")
        for record in records:
            name = canonical_lora_name(record.name)
            line = f"- {name}"
            if record.model_name and record.model_name != name:
                line += f" | title: {record.model_name}"
            if record.base_model:
                line += f" | base: {record.base_model}"
            if record.category != "unknown":
                line += f" | category: {record.category}"
            if record.character_name:
                line += f" | character: {record.character_name}"
            if record.source_work:
                line += f" | work: {record.source_work}"
            if record.aliases:
                line += f" | aliases: {', '.join(record.aliases[:8])}"
            if record.trigger_words:
                line += f" | triggers: {', '.join(record.trigger_words)}"
            if record.tags:
                line += f" | tags: {', '.join(record.tags[:8])}"
            if record.favorite:
                line += " | favorite"
            if record.description:
                line += f" | {self._strip_html(record.description)[:400]}"
            lines.append(line)
        return "\n".join(lines)

    async def infer_preset_category(self, selections: tuple[LoraSelection, ...]) -> str:
        """根据已索引 LoRA 的分类推断组合类别。"""
        if not selections:
            return "mixed"
        records = await self._get_records(force_refresh=False)
        index = {
            canonical_lora_name(record.name).casefold(): record for record in records
        }
        categories = {
            record.category
            for selection in selections
            if (record := index.get(canonical_lora_name(selection.name).casefold()))
            is not None
            and record.category != "unknown"
        }
        if categories == {"character"}:
            return "character"
        if categories and categories <= {"artist_style", *FUNCTIONAL_LORA_CATEGORIES}:
            return "artist_style"
        return "mixed"

    async def classify_selections(
        self, selections: tuple[LoraSelection, ...]
    ) -> dict[str, str]:
        """返回每个精确 LoRA 名称对应的自动分类。"""
        if not selections:
            return {}
        records = await self._get_records(force_refresh=False)
        index = {
            canonical_lora_name(record.name).casefold(): record for record in records
        }
        return {
            selection.name: (
                index.get(canonical_lora_name(selection.name).casefold()).category
                if index.get(canonical_lora_name(selection.name).casefold())
                else "unknown"
            )
            for selection in selections
        }

    async def refresh_summary(self) -> str:
        """刷新索引并返回适合管理员查看的摘要。"""
        records = await self.refresh_for_operation()
        text = (
            f"LoRA 清单已刷新：共 {len(records)} 个可加载文件，"
            f"其中 {self._manager_record_count} 个带 LoRA Manager 元数据。"
        )
        if self._last_warning:
            text += f" 管理器警告：{self._last_warning}"
        return text

    async def close(self) -> None:
        """关闭 HTTP 会话。"""
        if self._session is not None and not self._session.closed:
            await self._session.close()
