"""Safe local LoRA visual manifest and thumbnail cache.

The service deliberately has no network client and no delete-by-path API.  It
only reads model files and exact companion images located below explicitly
allowed LoRA roots, then writes metadata-free WebP previews below its own cache
directory.  Remote images can only enter as bounded bytes for an opaque key
already authorized by the current manifest; this module never accepts or
fetches their URL.  These boundaries keep the future WebUI integration from
becoming an arbitrary URL proxy or arbitrary filesystem browser.
"""

from __future__ import annotations

from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from typing import Any, Iterable, Mapping, Sequence

from .lora_catalog import LoraRecord

try:  # Pillow is optional at runtime; safe original-image caching is the fallback.
    from PIL import Image, ImageOps, UnidentifiedImageError

    _PIL_AVAILABLE = True
    _PIL_DECOMPRESSION_ERROR = Image.DecompressionBombError
except ImportError:  # pragma: no cover - exercised by monkeypatch in tests.
    Image = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]
    UnidentifiedImageError = OSError  # type: ignore[assignment,misc]
    _PIL_AVAILABLE = False
    _PIL_DECOMPRESSION_ERROR = OSError


SUPPORTED_PREVIEW_EXTENSIONS = (".webp", ".png", ".jpg", ".jpeg")
SUPPORTED_CACHE_EXTENSIONS = (".webp",)
DEFAULT_MAX_CACHE_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_PREVIEW_BYTES = 4 * 1024 * 1024
DEFAULT_MAX_PIXELS = 16_000_000
MAX_OUTPUT_BYTES = 1 * 1024 * 1024
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
MAX_WARMUP_ITEMS = 200
MAX_WARMUP_WORKERS = 4

_CACHE_NAME_RE = re.compile(r"^(?P<digest>[0-9a-f]{64})(?P<ext>\.webp|\.png|\.jpg|\.jpeg)$")
_WEBP_CACHE_NAME_RE = re.compile(r"^(?P<digest>[0-9a-f]{64})\.webp$")
_SAFE_KEY_RE = re.compile(r"^[0-9a-f]{64}$")


class LoraVisualError(RuntimeError):
    """Base error for local LoRA visual operations."""


class LoraVisualSecurityError(LoraVisualError):
    """A requested path or cache key crossed the service security boundary."""


class LoraPreviewError(LoraVisualError):
    """A local companion image is missing, invalid, or too large."""


@dataclass(frozen=True)
class PreviewAsset:
    """Path-free metadata for one verified, re-encoded cache asset."""

    key: str
    media_type: str
    size: int
    cached: bool = True
    width: int | None = None
    height: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "media_type": self.media_type,
            "size": self.size,
            "cached": self.cached,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class LoraVisualItem:
    """One deterministic, JSON-ready LoRA visual catalog row."""

    asset_id: str
    name: str
    display_name: str
    category: str
    size: int | None
    mtime: float | None
    mtime_iso: str
    fingerprint: str
    metadata_status: str
    metadata_source: str
    preview_status: str
    preview_key: str
    preview_media_type: str
    preview_width: int | None
    preview_height: int | None
    path_status: str
    favorite: bool = False
    _search_text: str = field(default="", repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "name": self.name,
            "display_name": self.display_name,
            "category": self.category,
            "size": self.size,
            "mtime": self.mtime,
            "mtime_iso": self.mtime_iso,
            "fingerprint": self.fingerprint,
            "metadata_status": self.metadata_status,
            "metadata_source": self.metadata_source,
            "preview_status": self.preview_status,
            "preview_key": self.preview_key,
            "preview_media_type": self.preview_media_type,
            "preview_width": self.preview_width,
            "preview_height": self.preview_height,
            "path_status": self.path_status,
            "favorite": self.favorite,
        }


@dataclass(frozen=True)
class LoraVisualManifest:
    """Stable manifest plus summary counts for one LoRA catalog snapshot."""

    items: tuple[LoraVisualItem, ...]
    fingerprint: str
    generated_at: float
    category_counts: Mapping[str, int]
    metadata_counts: Mapping[str, int]
    preview_counts: Mapping[str, int]

    @property
    def total(self) -> int:
        return len(self.items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "generated_at": self.generated_at,
            "total": self.total,
            "category_counts": dict(self.category_counts),
            "metadata_counts": dict(self.metadata_counts),
            "preview_counts": dict(self.preview_counts),
            "items": [item.to_dict() for item in self.items],
        }


@dataclass(frozen=True)
class LoraVisualPage:
    """Filtered and paginated view without losing the source manifest identity."""

    items: tuple[LoraVisualItem, ...]
    manifest_fingerprint: str
    total: int
    page: int
    page_size: int
    pages: int
    category_counts: Mapping[str, int]
    metadata_counts: Mapping[str, int]
    preview_counts: Mapping[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_fingerprint": self.manifest_fingerprint,
            "total": self.total,
            "page": self.page,
            "page_size": self.page_size,
            "pages": self.pages,
            "category_counts": dict(self.category_counts),
            "metadata_counts": dict(self.metadata_counts),
            "preview_counts": dict(self.preview_counts),
            "items": [item.to_dict() for item in self.items],
        }


@dataclass(frozen=True)
class WarmupSchedule:
    accepted: int
    deduplicated: int
    already_cached: int
    unavailable: int
    truncated: int
    keys: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "deduplicated": self.deduplicated,
            "already_cached": self.already_cached,
            "unavailable": self.unavailable,
            "truncated": self.truncated,
            "keys": list(self.keys),
        }


@dataclass(frozen=True)
class WarmupStatus:
    queued: int
    completed: int
    failed: int
    last_errors: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "queued": self.queued,
            "completed": self.completed,
            "failed": self.failed,
            "last_errors": dict(self.last_errors),
        }


@dataclass(frozen=True)
class _PreviewSource:
    key: str
    path: Path
    media_type: str
    size: int
    mtime_ns: int
    width: int | None
    height: int | None
    extension: str


@dataclass(frozen=True)
class _CachedPreview:
    key: str
    path: Path
    media_type: str
    size: int
    width: int | None
    height: int | None


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _iso_mtime(value: float | None) -> str:
    if value is None:
        return ""
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _media_type(extension: str) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }[extension.casefold()]


def _as_filter(values: str | Iterable[str] | None) -> frozenset[str]:
    if values is None:
        return frozenset()
    if isinstance(values, str):
        values = (values,)
    return frozenset(str(value).strip().casefold() for value in values if str(value).strip())


def _metadata_state(record: LoraRecord) -> tuple[str, str]:
    trigger_words = tuple(value for value in record.trigger_words if str(value).strip())
    tags = tuple(value for value in record.tags if str(value).strip())
    aliases = tuple(value for value in record.aliases if str(value).strip())
    description = str(record.description or "").strip()
    identity = str(record.model_name or record.character_name or "").strip()
    supporting = bool(
        description
        or identity
        or trigger_words
        or tags
        or aliases
        or str(record.base_model or "").strip()
        or str(record.source_work or "").strip()
        or str(record.source_fingerprint or "").strip()
    )
    source = "civitai" if record.from_civitai else ("local" if supporting else "none")
    complete = bool(record.from_civitai and description and identity and (trigger_words or tags))
    if complete:
        return "complete", source
    if supporting:
        return "partial", source
    return "missing", source


class LoraVisualService:
    """Build local visual manifests and maintain a bounded thumbnail cache."""

    def __init__(
        self,
        allowed_roots: Sequence[str | os.PathLike[str]],
        cache_dir: str | os.PathLike[str],
        *,
        max_cache_bytes: int = DEFAULT_MAX_CACHE_BYTES,
        max_workers: int = 2,
        max_preview_bytes: int = DEFAULT_MAX_PREVIEW_BYTES,
        max_pixels: int = DEFAULT_MAX_PIXELS,
        thumbnail_size: tuple[int, int] = (512, 512),
        webp_quality: int = 88,
    ) -> None:
        roots: list[Path] = []
        for raw_root in allowed_roots:
            root = Path(raw_root).expanduser().resolve(strict=False)
            if root not in roots:
                roots.append(root)
        width, height = (int(thumbnail_size[0]), int(thumbnail_size[1]))
        if width < 1 or height < 1 or width > 4096 or height > 4096:
            raise ValueError("thumbnail_size must be between 1 and 4096 pixels")

        self._allowed_roots = tuple(roots)
        self._cache_dir = Path(cache_dir).expanduser().resolve(strict=False)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        if not self._cache_dir.is_dir():
            raise ValueError("cache_dir must be a directory")

        self._max_cache_bytes = max(0, int(max_cache_bytes))
        self._max_preview_bytes = max(1, int(max_preview_bytes))
        self._max_pixels = max(1, int(max_pixels))
        self._thumbnail_size = (width, height)
        self._webp_quality = min(100, max(1, int(webp_quality)))
        self._max_workers = min(MAX_WARMUP_WORKERS, max(1, int(max_workers)))

        self._state_lock = threading.RLock()
        self._cache_lock = threading.RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="lora-visual-warmup",
        )
        self._closed = False
        self._sources: dict[str, _PreviewSource] = {}
        self._current_preview_keys: frozenset[str] = frozenset()
        self._current_remote_keys: frozenset[str] = frozenset()
        self._probe_cache: dict[tuple[str, int, int], _PreviewSource] = {}
        self._inflight: dict[str, Future[_CachedPreview | None]] = {}
        self._completed = 0
        self._failed = 0
        self._last_errors: dict[str, str] = {}

    @property
    def max_workers(self) -> int:
        return self._max_workers

    def __enter__(self) -> "LoraVisualService":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close(wait=True)

    def close(self, *, wait: bool = True) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._sources = {}
            self._current_preview_keys = frozenset()
            self._current_remote_keys = frozenset()
        self._executor.shutdown(wait=wait, cancel_futures=not wait)

    def build_manifest(self, records: Sequence[LoraRecord]) -> LoraVisualManifest:
        """Build a deterministic manifest without following remote preview URLs."""

        try:
            built = [self._build_item(record) for record in records]
        except Exception:
            with self._state_lock:
                self._sources = {}
                self._current_preview_keys = frozenset()
                self._current_remote_keys = frozenset()
            raise
        built.sort(key=lambda pair: (pair[0].name.casefold(), pair[0].asset_id))
        items = [pair[0] for pair in built]
        next_sources: dict[str, _PreviewSource] = {}
        next_remote_keys: set[str] = set()
        for item, source, is_remote in built:
            if source is not None:
                next_sources.setdefault(source.key, source)
            if is_remote and item.preview_key:
                next_remote_keys.add(item.preview_key)

        # Catalog services normally deduplicate already.  Keep the visual layer
        # defensive so duplicated manager rows do not produce duplicated cards.
        unique: list[LoraVisualItem] = []
        seen: set[tuple[str, str]] = set()
        for item in items:
            identity = (item.name.casefold(), item.asset_id)
            if identity in seen:
                continue
            seen.add(identity)
            unique.append(item)
        frozen = tuple(unique)

        # Cache residency is intentionally excluded: preheating a thumbnail must
        # not make the underlying LoRA catalog look like a different catalog.
        material = [
            {
                "name": item.name,
                "asset_id": item.asset_id,
                "fingerprint": item.fingerprint,
                "preview_key": item.preview_key,
                "preview_available": bool(item.preview_key),
                "preview_remote": item.preview_key in next_remote_keys,
            }
            for item in frozen
        ]
        # Publish the new authorization set only after the complete manifest was
        # built.  A removed LoRA or switched profile immediately invalidates its
        # old preview key even when a content-addressed cache file still exists.
        with self._state_lock:
            self._sources = next_sources
            self._current_remote_keys = frozenset(next_remote_keys)
            self._current_preview_keys = frozenset(next_sources) | frozenset(
                next_remote_keys
            )
        return LoraVisualManifest(
            items=frozen,
            fingerprint=_stable_hash(material),
            generated_at=time.time(),
            category_counts=dict(Counter(item.category for item in frozen)),
            metadata_counts=dict(Counter(item.metadata_status for item in frozen)),
            preview_counts=dict(Counter(item.preview_status for item in frozen)),
        )

    def list_page(
        self,
        records: Sequence[LoraRecord],
        *,
        query: str = "",
        categories: str | Iterable[str] | None = None,
        metadata_statuses: str | Iterable[str] | None = None,
        preview_statuses: str | Iterable[str] | None = None,
        favorite_only: bool = False,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> LoraVisualPage:
        """Filter by text/category/status and return at most 200 rows."""

        if not isinstance(favorite_only, bool):
            raise TypeError("favorite_only must be a bool")
        manifest = self.build_manifest(records)
        category_filter = _as_filter(categories)
        metadata_filter = _as_filter(metadata_statuses)
        preview_filter = _as_filter(preview_statuses)
        query_key = str(query or "").strip().casefold()

        filtered = tuple(
            item
            for item in manifest.items
            if (not category_filter or item.category.casefold() in category_filter)
            and (
                not metadata_filter
                or item.metadata_status.casefold() in metadata_filter
            )
            and (not preview_filter or item.preview_status.casefold() in preview_filter)
            and (not favorite_only or item.favorite)
            and (not query_key or query_key in item._search_text)
        )
        safe_page_size = min(MAX_PAGE_SIZE, max(1, int(page_size)))
        safe_page = max(1, int(page))
        total = len(filtered)
        pages = math.ceil(total / safe_page_size) if total else 0
        start = (safe_page - 1) * safe_page_size
        page_items = filtered[start : start + safe_page_size]
        return LoraVisualPage(
            items=page_items,
            manifest_fingerprint=manifest.fingerprint,
            total=total,
            page=safe_page,
            page_size=safe_page_size,
            pages=pages,
            category_counts=dict(Counter(item.category for item in filtered)),
            metadata_counts=dict(Counter(item.metadata_status for item in filtered)),
            preview_counts=dict(Counter(item.preview_status for item in filtered)),
        )

    def record_for_preview(
        self,
        records: Sequence[LoraRecord],
        preview_key: str,
    ) -> LoraRecord:
        """Resolve a current remote key to exactly one fresh catalog record.

        This is an internal controller hand-off, not response data.  It accepts
        neither a path nor a URL and performs no basename/fuzzy matching.  The
        caller can pass the returned fresh record to its existing trusted LoRA
        Manager client without exposing ``file_path`` or ``preview_url`` in the
        visual manifest.
        """

        key = self._validate_key(preview_key)
        with self._state_lock:
            if key not in self._current_remote_keys:
                raise LoraPreviewError(
                    "preview key is not an authorized remote asset in the current manifest"
                )
        matches: list[LoraRecord] = []
        for record in records:
            item, _, is_remote = self._build_item(record)
            if is_remote and item.preview_key == key:
                matches.append(record)
        if len(matches) != 1:
            raise LoraPreviewError(
                "remote preview key did not resolve to exactly one fresh LoRA record"
            )
        return matches[0]

    def resolve_preview(
        self,
        preview_key: str,
        *,
        create_cache: bool = False,
        touch: bool = True,
    ) -> PreviewAsset:
        """Resolve only a current-manifest, safely re-encoded WebP asset."""

        key = self._validate_key(preview_key)
        with self._state_lock:
            if key not in self._current_preview_keys:
                raise LoraPreviewError(
                    "preview key is not authorized by the current manifest"
                )
        cached = self._cached_asset(key)
        if cached is not None:
            if touch:
                try:
                    os.utime(cached.path, None)
                except OSError:
                    pass
            return self._public_asset(cached)

        if not create_cache:
            raise LoraPreviewError(
                "preview is not safely cached; create the re-encoded cache first"
            )
        cached = self._cache_preview_by_key(key)
        if cached is None:
            raise LoraPreviewError("preview could not be safely re-encoded")
        return self._public_asset(cached)

    def read_preview(
        self,
        preview_key: str,
        *,
        create_cache: bool = False,
    ) -> tuple[bytes, str]:
        """Read a verified local preview with a hard response-size bound."""

        asset = self.resolve_preview(preview_key, create_cache=create_cache)
        cached = self._cached_asset(asset.key)
        if cached is None:
            raise LoraPreviewError("re-encoded preview cache disappeared")
        path = cached.path.resolve(strict=True)
        if path.parent != self._cache_dir:
            raise LoraVisualSecurityError("cached preview escaped cache directory")
        stat = path.stat()
        if stat.st_size < 1 or stat.st_size > MAX_OUTPUT_BYTES:
            raise LoraPreviewError("preview exceeds the configured response limit")
        return path.read_bytes(), asset.media_type

    def ingest_preview_bytes(
        self,
        preview_key: str,
        data: bytes | bytearray | memoryview,
        content_type: str,
    ) -> PreviewAsset:
        """Safely ingest bytes fetched elsewhere for a current remote preview.

        The service never receives a URL and never performs a network request.
        Only a key emitted for a ``remote_only`` row in the current manifest is
        accepted.  The input is decoded as one bounded still image, stripped of
        metadata and atomically re-encoded to WebP before it can be read back.
        """

        key = self._validate_key(preview_key)
        with self._state_lock:
            if key not in self._current_preview_keys or key not in self._current_remote_keys:
                raise LoraPreviewError(
                    "preview key is not an authorized remote asset in the current manifest"
                )
        if not _PIL_AVAILABLE:
            raise LoraPreviewError(
                "Pillow is unavailable; remote preview bytes cannot be ingested"
            )
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("preview data must be bytes-like")
        payload = bytes(data)
        if not payload or len(payload) > self._max_preview_bytes:
            raise LoraPreviewError("remote preview exceeds the configured size limit")
        normalized_type = str(content_type or "").split(";", 1)[0].strip().casefold()
        if normalized_type not in {
            "image/png",
            "image/jpeg",
            "image/jpg",
            "image/webp",
            "application/octet-stream",
        }:
            raise LoraPreviewError("unsupported remote preview content type")

        target = self._cache_dir / f"{key}.webp"
        with self._cache_lock:
            temp_path: Path | None = None
            try:
                descriptor, temp_name = tempfile.mkstemp(
                    prefix=".lora-visual-remote-",
                    suffix=".webp",
                    dir=self._cache_dir,
                )
                os.close(descriptor)
                temp_path = Path(temp_name)
                assert Image is not None
                try:
                    with Image.open(BytesIO(payload)) as opened:
                        if str(opened.format or "").upper() not in {
                            "PNG",
                            "JPEG",
                            "WEBP",
                        }:
                            raise LoraPreviewError(
                                "remote preview decoded to an unsupported image format"
                            )
                        self._write_open_image(opened, temp_path)
                except LoraPreviewError:
                    raise
                except (
                    OSError,
                    ValueError,
                    UnidentifiedImageError,
                    _PIL_DECOMPRESSION_ERROR,
                ) as exc:
                    raise LoraPreviewError("remote preview image is invalid") from exc
                stat = temp_path.stat()
                if stat.st_size < 1 or stat.st_size > MAX_OUTPUT_BYTES:
                    raise LoraPreviewError("generated preview exceeds the cache limit")
                with temp_path.open("rb") as cache_file:
                    self._validate_magic(".webp", cache_file.read(16))
                os.replace(temp_path, target)
                temp_path = None
            finally:
                if temp_path is not None:
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass
        self.prune_cache()
        cached = self._cached_asset(key)
        if cached is None:
            raise LoraPreviewError("remote preview cache was rejected by the quota")
        return self._public_asset(cached)

    def schedule_warmup(
        self,
        records: Sequence[LoraRecord],
        *,
        keys: Iterable[str] | None = None,
        limit: int | None = None,
    ) -> WarmupSchedule:
        """Queue bounded background thumbnail work with content-key deduplication."""

        with self._state_lock:
            if self._closed:
                raise LoraVisualError("visual service is closed")
        manifest = self.build_manifest(records)
        manifest_keys = tuple(
            dict.fromkeys(item.preview_key for item in manifest.items if item.preview_key)
        )
        with self._state_lock:
            current_keys = self._current_preview_keys
            local_keys = frozenset(self._sources)
        if keys is None:
            all_keys = manifest_keys
        else:
            raw_keys: Iterable[str] = (keys,) if isinstance(keys, str) else keys
            requested_keys = tuple(
                dict.fromkeys(self._validate_key(value) for value in raw_keys)
            )
            if len(requested_keys) > MAX_WARMUP_ITEMS:
                raise LoraPreviewError("at most 200 warmup keys are allowed")
            unknown = tuple(key for key in requested_keys if key not in current_keys)
            if unknown:
                raise LoraPreviewError(
                    "warmup keys must belong to the current fresh manifest"
                )
            all_keys = requested_keys
        already_cached_keys = tuple(
            key for key in all_keys if self._cached_asset(key) is not None
        )
        candidate_keys = tuple(
            key
            for key in all_keys
            if key in local_keys and key not in already_cached_keys
        )
        requested_limit = MAX_WARMUP_ITEMS if limit is None else max(0, int(limit))
        hard_limit = min(MAX_WARMUP_ITEMS, requested_limit)
        selected_keys = candidate_keys[:hard_limit]

        accepted = 0
        deduplicated = 0
        already_cached = len(already_cached_keys)
        unavailable = sum(1 for item in manifest.items if not item.preview_key) + sum(
            1
            for key in all_keys
            if key not in local_keys and key not in already_cached_keys
        )
        truncated = max(0, len(candidate_keys) - len(selected_keys))
        keys: list[str] = []
        for key in selected_keys:
            with self._state_lock:
                if key in self._inflight:
                    deduplicated += 1
                    continue
                future = self._executor.submit(self._cache_preview_by_key, key)
                self._inflight[key] = future
                future.add_done_callback(
                    lambda completed, content_key=key: self._warmup_done(
                        content_key, completed
                    )
                )
            accepted += 1
            keys.append(key)
        return WarmupSchedule(
            accepted=accepted,
            deduplicated=deduplicated,
            already_cached=already_cached,
            unavailable=unavailable,
            truncated=truncated,
            keys=tuple(keys),
        )

    def wait_for_idle(self, timeout: float | None = None) -> WarmupStatus:
        """Wait for currently queued jobs; primarily useful for shutdown/tests."""

        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        while True:
            with self._state_lock:
                futures = tuple(self._inflight.values())
            if not futures:
                return self.warmup_status()
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            if remaining == 0.0:
                return self.warmup_status()
            _, pending = wait(futures, timeout=remaining)
            if pending and deadline is not None and time.monotonic() >= deadline:
                return self.warmup_status()

    def warmup_status(self) -> WarmupStatus:
        with self._state_lock:
            return WarmupStatus(
                queued=len(self._inflight),
                completed=self._completed,
                failed=self._failed,
                last_errors=dict(self._last_errors),
            )

    def prune_cache(self) -> dict[str, int]:
        """Apply the byte quota to recognized content-addressed cache files only."""

        with self._cache_lock:
            entries: list[tuple[float, Path, int, str]] = []
            total = 0
            for child in self._cache_dir.iterdir():
                match = _CACHE_NAME_RE.fullmatch(child.name)
                if not match:
                    continue
                try:
                    resolved = child.resolve(strict=True)
                    if resolved.parent != self._cache_dir or not resolved.is_file():
                        continue
                    stat = resolved.stat()
                except OSError:
                    continue
                total += stat.st_size
                entries.append((stat.st_mtime, resolved, stat.st_size, match.group("digest")))

            removed = 0
            removed_bytes = 0
            with self._state_lock:
                protected = frozenset(self._inflight)
            for _, path, size, digest in sorted(entries, key=lambda entry: entry[0]):
                if total <= self._max_cache_bytes:
                    break
                if digest in protected:
                    continue
                try:
                    path.unlink()
                except OSError:
                    continue
                total -= size
                removed += 1
                removed_bytes += size
            return {
                "removed": removed,
                "removed_bytes": removed_bytes,
                "remaining_bytes": total,
            }

    def clear_cache(self) -> dict[str, int]:
        """Delete only idle, strictly named WebP cache assets.

        There is intentionally no path argument.  Unknown files, legacy image
        extensions, directories, escaping symlinks and currently in-flight keys
        are preserved.
        """

        with self._cache_lock:
            with self._state_lock:
                protected = frozenset(self._inflight)
            removed = 0
            removed_bytes = 0
            remaining_bytes = 0
            for child in self._cache_dir.iterdir():
                match = _WEBP_CACHE_NAME_RE.fullmatch(child.name)
                if not match:
                    continue
                try:
                    resolved = child.resolve(strict=True)
                    if resolved.parent != self._cache_dir or not resolved.is_file():
                        continue
                    size = resolved.stat().st_size
                except OSError:
                    continue
                if match.group("digest") in protected:
                    remaining_bytes += size
                    continue
                try:
                    resolved.unlink()
                except OSError:
                    remaining_bytes += size
                    continue
                removed += 1
                removed_bytes += size
            return {
                "removed": removed,
                "removed_bytes": removed_bytes,
                "remaining_bytes": remaining_bytes,
            }

    def _build_item(
        self, record: LoraRecord
    ) -> tuple[LoraVisualItem, _PreviewSource | None, bool]:
        model_path, path_status = self._resolve_model_path(record)
        size: int | None = None
        mtime: float | None = None
        mtime_ns: int | None = None
        if model_path is not None:
            stat = model_path.stat()
            size = stat.st_size
            mtime = stat.st_mtime
            mtime_ns = stat.st_mtime_ns

        source: _PreviewSource | None = None
        preview_status = "missing"
        if model_path is not None:
            source, preview_status = self._find_companion(model_path)
        has_remote_preview = bool(str(record.preview_url or "").strip())

        preview_key = ""
        preview_media_type = ""
        preview_width: int | None = None
        preview_height: int | None = None
        if source is not None:
            preview_key = source.key
            preview_media_type = "image/webp"
            preview_width = source.width
            preview_height = source.height
            preview_status = "cached" if self._cached_asset(source.key) else "local"

        metadata_status, metadata_source = _metadata_state(record)
        name = self._loadable_name(record, model_path)
        display_name = str(record.model_name or record.character_name or "").strip()
        normalized_display = display_name.replace("\\", "/")
        if normalized_display.startswith("/") or re.match(
            r"^[A-Za-z]:/", normalized_display
        ):
            display_name = Path(normalized_display).name
        if not display_name:
            display_name = (
                Path(name).stem
                or (model_path.stem if model_path is not None else "")
                or "Unnamed LoRA"
            )
        category = str(record.category or "unknown").strip().casefold() or "unknown"
        normalized_name = name.replace("\\", "/")
        asset_material: dict[str, Any] = {
            "scope": "lora-visual-asset-v1",
            "name": normalized_name.casefold(),
            "sha256": str(record.sha256 or "").strip().casefold(),
        }
        if not asset_material["sha256"]:
            asset_material.update({"size": size, "mtime_ns": mtime_ns})
        asset_id = _stable_hash(asset_material)
        is_remote = False
        if source is None and has_remote_preview:
            locator_digest = hashlib.sha256(
                str(record.preview_url or "").strip().encode("utf-8")
            ).hexdigest()
            preview_key = _stable_hash(
                {
                    "scope": "lora-visual-remote-preview-v1",
                    "asset_id": asset_id,
                    "source_fingerprint": str(
                        record.source_fingerprint or ""
                    ).strip(),
                    "locator_digest": locator_digest,
                }
            )
            preview_media_type = "image/webp"
            preview_status = "cached" if self._cached_asset(preview_key) else "remote_only"
            is_remote = True

        metadata_identity = {
            "status": metadata_status,
            "source": metadata_source,
            "source_fingerprint": str(record.source_fingerprint or "").strip(),
            "sha256": str(record.sha256 or "").strip().casefold(),
            "trigger_words": sorted(str(value).strip() for value in record.trigger_words),
            "tags": sorted(str(value).strip() for value in record.tags),
            "aliases": sorted(str(value).strip() for value in record.aliases),
            "description": str(record.description or "").strip(),
            "model_name": str(record.model_name or "").strip(),
            "base_model": str(record.base_model or "").strip(),
            "character_name": str(record.character_name or "").strip(),
            "source_work": str(record.source_work or "").strip(),
        }
        item_fingerprint = _stable_hash(
            {
                "name": name.replace("\\", "/"),
                "asset_id": asset_id,
                "path_status": path_status,
                "size": size,
                "mtime_ns": mtime_ns,
                "category": category,
                "metadata": metadata_identity,
                "preview_key": preview_key,
                "preview_state": (
                    "available" if preview_key else preview_status
                ),
            }
        )
        search_values = (
            name,
            display_name,
            category,
            str(record.character_name or ""),
            str(record.source_work or ""),
            *record.aliases,
            *record.tags,
            *record.trigger_words,
        )
        return LoraVisualItem(
            asset_id=asset_id,
            name=name,
            display_name=display_name,
            category=category,
            size=size,
            mtime=mtime,
            mtime_iso=_iso_mtime(mtime),
            fingerprint=item_fingerprint,
            metadata_status=metadata_status,
            metadata_source=metadata_source,
            preview_status=preview_status,
            preview_key=preview_key,
            preview_media_type=preview_media_type,
            preview_width=preview_width,
            preview_height=preview_height,
            path_status=path_status,
            favorite=bool(record.favorite),
            _search_text="\n".join(str(value or "").casefold() for value in search_values),
        ), source, is_remote

    def _resolve_model_path(self, record: LoraRecord) -> tuple[Path | None, str]:
        if not self._allowed_roots:
            return None, "missing"
        blocked = False
        raw_path = str(record.file_path or "").strip()
        if raw_path:
            direct = Path(raw_path).expanduser()
            if direct.is_absolute() and direct.exists():
                try:
                    resolved = direct.resolve(strict=True)
                except OSError:
                    resolved = None
                if resolved is not None and resolved.is_file():
                    if self._is_allowed(resolved):
                        return resolved, "available"
                    blocked = True
            elif not direct.is_absolute():
                relative = self._safe_relative_path(raw_path)
                if relative is None:
                    blocked = True
                else:
                    found = self._find_under_roots(relative)
                    if found is not None:
                        return found, "available"

        relative_name = self._safe_relative_path(str(record.name or ""))
        if relative_name is None:
            blocked = True
        else:
            found = self._find_under_roots(relative_name)
            if found is not None:
                return found, "available"
        return None, "blocked" if blocked else "missing"

    def _loadable_name(self, record: LoraRecord, model_path: Path | None) -> str:
        relative = self._safe_relative_path(str(record.name or ""))
        if relative is not None:
            return relative.as_posix()
        if model_path is not None:
            for root in self._allowed_roots:
                try:
                    return model_path.relative_to(root).as_posix()
                except ValueError:
                    continue
        return ""

    @staticmethod
    def _safe_relative_path(value: str) -> Path | None:
        normalized = str(value or "").strip().replace("\\", "/")
        if not normalized or normalized.startswith("/") or "\x00" in normalized:
            return None
        # A drive prefix is absolute on Windows but not on POSIX; reject it on both.
        if re.match(r"^[A-Za-z]:", normalized):
            return None
        parts = tuple(part for part in normalized.split("/") if part not in ("", "."))
        if not parts or any(part == ".." for part in parts):
            return None
        return Path(*parts)

    def _find_under_roots(self, relative: Path) -> Path | None:
        for root in self._allowed_roots:
            candidate = root / relative
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                continue
            if resolved.is_file() and self._is_allowed(resolved):
                return resolved
        return None

    def _is_allowed(self, path: Path) -> bool:
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            return False
        for root in self._allowed_roots:
            try:
                resolved.relative_to(root)
            except ValueError:
                continue
            return True
        return False

    def _find_companion(self, model_path: Path) -> tuple[_PreviewSource | None, str]:
        base = model_path.stem
        full = model_path.name
        desired: list[str] = []
        for extension in SUPPORTED_PREVIEW_EXTENSIONS:
            desired.extend(
                (
                    f"{base}.preview{extension}",
                    f"{base}{extension}",
                    f"{full}.preview{extension}",
                    f"{full}{extension}",
                )
            )
        try:
            files = {
                entry.name.casefold(): entry
                for entry in sorted(model_path.parent.iterdir(), key=lambda path: path.name)
                if entry.is_file()
            }
        except OSError:
            return None, "missing"

        saw_invalid = False
        for candidate_name in desired:
            candidate = files.get(candidate_name.casefold())
            if candidate is None:
                continue
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                saw_invalid = True
                continue
            if not self._is_allowed(resolved):
                saw_invalid = True
                continue
            if not _PIL_AVAILABLE:
                return None, "decoder_unavailable"
            try:
                return self._probe_image(resolved), "local"
            except LoraPreviewError:
                saw_invalid = True
        return None, "invalid" if saw_invalid else "missing"

    def _probe_image(self, path: Path) -> _PreviewSource:
        if not _PIL_AVAILABLE:
            raise LoraPreviewError(
                "Pillow is unavailable; original previews are never served"
            )
        try:
            resolved = path.resolve(strict=True)
            stat = resolved.stat()
        except OSError as exc:
            raise LoraPreviewError("preview file is not readable") from exc
        if not self._is_allowed(resolved):
            raise LoraVisualSecurityError("preview escaped allowed LoRA roots")
        extension = resolved.suffix.casefold()
        if extension not in SUPPORTED_PREVIEW_EXTENSIONS:
            raise LoraPreviewError("unsupported preview extension")
        if stat.st_size < 1 or stat.st_size > self._max_preview_bytes:
            raise LoraPreviewError("preview exceeds the configured size limit")

        probe_key = (str(resolved), stat.st_size, stat.st_mtime_ns)
        with self._state_lock:
            cached_probe = self._probe_cache.get(probe_key)
        if cached_probe is not None:
            return cached_probe

        with resolved.open("rb") as source_file:
            header = source_file.read(16)
            source_file.seek(0)
            digest = hashlib.sha256()
            for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
                digest.update(chunk)
        self._validate_magic(extension, header)

        width: int | None = None
        height: int | None = None
        assert Image is not None
        try:
            with Image.open(resolved) as image:
                if bool(getattr(image, "is_animated", False)) or int(
                    getattr(image, "n_frames", 1)
                ) != 1:
                    raise LoraPreviewError("animated previews are not supported")
                width, height = (int(image.width), int(image.height))
                if width < 1 or height < 1 or width * height > self._max_pixels:
                    raise LoraPreviewError("preview dimensions exceed the pixel limit")
                image.verify()
        except LoraPreviewError:
            raise
        except (
            OSError,
            ValueError,
            UnidentifiedImageError,
            _PIL_DECOMPRESSION_ERROR,
        ) as exc:
            raise LoraPreviewError("preview image is invalid") from exc

        source = _PreviewSource(
            key=digest.hexdigest(),
            path=resolved,
            media_type=_media_type(extension),
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            width=width,
            height=height,
            extension=extension,
        )
        with self._state_lock:
            self._probe_cache[probe_key] = source
            # Bound memory even if a very large library changes files repeatedly.
            if len(self._probe_cache) > 4096:
                for stale_key in tuple(self._probe_cache)[:1024]:
                    self._probe_cache.pop(stale_key, None)
        return source

    @staticmethod
    def _validate_magic(extension: str, header: bytes) -> None:
        valid = False
        if extension == ".png":
            valid = header.startswith(b"\x89PNG\r\n\x1a\n")
        elif extension in {".jpg", ".jpeg"}:
            valid = header.startswith(b"\xff\xd8\xff")
        elif extension == ".webp":
            valid = len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP"
        if not valid:
            raise LoraPreviewError("preview content does not match its extension")

    @staticmethod
    def _validate_key(value: str) -> str:
        key = str(value or "").strip().casefold()
        if not _SAFE_KEY_RE.fullmatch(key):
            raise LoraVisualSecurityError("invalid preview key")
        return key

    def _cached_asset(self, key: str) -> _CachedPreview | None:
        safe_key = self._validate_key(key)
        for extension in SUPPORTED_CACHE_EXTENSIONS:
            candidate = self._cache_dir / f"{safe_key}{extension}"
            try:
                resolved = candidate.resolve(strict=True)
                if resolved.parent != self._cache_dir or not resolved.is_file():
                    continue
                stat = resolved.stat()
            except OSError:
                continue
            if stat.st_size < 1 or stat.st_size > MAX_OUTPUT_BYTES:
                continue
            try:
                with resolved.open("rb") as cache_file:
                    self._validate_magic(extension, cache_file.read(16))
            except (OSError, LoraPreviewError):
                continue
            with self._state_lock:
                source = self._sources.get(safe_key)
            return _CachedPreview(
                key=safe_key,
                path=resolved,
                media_type=_media_type(extension),
                size=stat.st_size,
                width=source.width if source else None,
                height=source.height if source else None,
            )
        return None

    @staticmethod
    def _public_asset(cached: _CachedPreview) -> PreviewAsset:
        return PreviewAsset(
            key=cached.key,
            media_type=cached.media_type,
            size=cached.size,
            cached=True,
            width=cached.width,
            height=cached.height,
        )

    def _cache_preview_by_key(self, key: str) -> _CachedPreview | None:
        safe_key = self._validate_key(key)
        with self._state_lock:
            if safe_key not in self._current_preview_keys:
                raise LoraPreviewError(
                    "preview key is not authorized by the current manifest"
                )
        if not _PIL_AVAILABLE:
            raise LoraPreviewError(
                "Pillow is unavailable; original previews are never cached"
            )
        if self._max_cache_bytes <= 0:
            return None
        cached = self._cached_asset(safe_key)
        if cached is not None:
            return cached
        with self._state_lock:
            source = self._sources.get(safe_key)
        if source is None:
            raise LoraPreviewError("preview key is not present in the current manifest")
        current = self._probe_image(source.path)
        if current.key != safe_key:
            raise LoraPreviewError("preview source changed during cache warmup")

        target_extension = ".webp"
        target = self._cache_dir / f"{safe_key}{target_extension}"
        with self._cache_lock:
            cached = self._cached_asset(safe_key)
            if cached is not None:
                return cached
            temp_path: Path | None = None
            try:
                descriptor, temp_name = tempfile.mkstemp(
                    prefix=".lora-visual-",
                    suffix=target_extension,
                    dir=self._cache_dir,
                )
                os.close(descriptor)
                temp_path = Path(temp_name)
                self._write_webp(source.path, temp_path)
                stat = temp_path.stat()
                if stat.st_size < 1 or stat.st_size > MAX_OUTPUT_BYTES:
                    raise LoraPreviewError("generated preview exceeds the cache limit")
                with temp_path.open("rb") as cache_file:
                    self._validate_magic(target_extension, cache_file.read(16))
                os.replace(temp_path, target)
                temp_path = None
            finally:
                if temp_path is not None:
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass
        self.prune_cache()
        return self._cached_asset(safe_key)

    def _write_webp(self, source_path: Path, target_path: Path) -> None:
        assert Image is not None and ImageOps is not None
        try:
            with Image.open(source_path) as opened:
                self._write_open_image(opened, target_path)
        except LoraPreviewError:
            raise
        except (
            OSError,
            ValueError,
            UnidentifiedImageError,
            _PIL_DECOMPRESSION_ERROR,
        ) as exc:
            raise LoraPreviewError("failed to create cached preview") from exc

    def _write_open_image(self, opened: Any, target_path: Path) -> None:
        assert Image is not None and ImageOps is not None
        if bool(getattr(opened, "is_animated", False)) or int(
            getattr(opened, "n_frames", 1)
        ) != 1:
            raise LoraPreviewError("animated previews are not supported")
        if int(opened.width) < 1 or int(opened.height) < 1:
            raise LoraPreviewError("preview dimensions are invalid")
        if int(opened.width) * int(opened.height) > self._max_pixels:
            raise LoraPreviewError("preview dimensions exceed the pixel limit")
        image = ImageOps.exif_transpose(opened)
        image.load()
        has_alpha = "A" in image.getbands() or "transparency" in image.info
        converted = image.convert("RGBA" if has_alpha else "RGB")
        size_candidates: list[tuple[int, int]] = [self._thumbnail_size]
        for cap in (384, 320):
            candidate = (
                min(self._thumbnail_size[0], cap),
                min(self._thumbnail_size[1], cap),
            )
            if candidate not in size_candidates:
                size_candidates.append(candidate)
        quality_candidates = tuple(
            dict.fromkeys(
                (self._webp_quality,)
                + tuple(
                    quality
                    for quality in (80, 72, 64, 56, 48)
                    if quality < self._webp_quality
                )
            )
        )
        for size in size_candidates:
            resized = converted.copy()
            resized.thumbnail(size)
            # Construct from raw pixels after orientation/resizing so EXIF, PNG
            # text chunks, prompts and every other source metadata field are
            # absent from the only file that can be served by the WebUI.
            clean = Image.frombytes(
                resized.mode,
                resized.size,
                resized.tobytes(),
            )
            for quality in quality_candidates:
                clean.save(
                    target_path,
                    format="WEBP",
                    quality=quality,
                    method=4,
                )
                if target_path.stat().st_size <= MAX_OUTPUT_BYTES:
                    return
        raise LoraPreviewError("re-encoded preview exceeds the 1 MiB output limit")

    def _warmup_done(
        self,
        key: str,
        future: Future[_CachedPreview | None],
    ) -> None:
        error = ""
        try:
            result = future.result()
            if result is None:
                error = "cache disabled or preview exceeded cache quota"
        except Exception as exc:  # callback must never escape into executor internals.
            error = f"{type(exc).__name__}: {exc}"
        with self._state_lock:
            self._inflight.pop(key, None)
            if error:
                self._failed += 1
                self._last_errors[key] = error[:240]
                if len(self._last_errors) > 20:
                    oldest = next(iter(self._last_errors))
                    self._last_errors.pop(oldest, None)
            else:
                self._completed += 1
                self._last_errors.pop(key, None)


__all__ = [
    "DEFAULT_MAX_CACHE_BYTES",
    "DEFAULT_MAX_PIXELS",
    "DEFAULT_MAX_PREVIEW_BYTES",
    "DEFAULT_PAGE_SIZE",
    "LoraPreviewError",
    "LoraVisualError",
    "LoraVisualItem",
    "LoraVisualManifest",
    "LoraVisualPage",
    "LoraVisualSecurityError",
    "LoraVisualService",
    "MAX_OUTPUT_BYTES",
    "MAX_PAGE_SIZE",
    "MAX_WARMUP_ITEMS",
    "MAX_WARMUP_WORKERS",
    "PreviewAsset",
    "SUPPORTED_PREVIEW_EXTENSIONS",
    "WarmupSchedule",
    "WarmupStatus",
]
