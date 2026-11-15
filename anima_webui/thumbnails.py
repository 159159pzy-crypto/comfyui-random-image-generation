"""Disposable, bounded thumbnails for history cards; originals stay in ComfyUI."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import stat
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

from .comfy import ComfyError


THUMBNAIL_EDGE = 512
THUMBNAIL_QUALITY = 82
CACHE_MAX_BYTES = 1024 * 1024 * 1024
MAINTENANCE_INTERVAL_SECONDS = 10 * 60
TEMP_TTL_SECONDS = 60 * 60
MAX_SOURCE_PIXELS = 40_000_000
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_THUMBNAIL_BYTES = 4 * 1024 * 1024
_RECIPE = "webp-512-q82-exif-icc-v1"
_CACHE_NAME = re.compile(r"^[0-9]+-[a-f0-9]{64}\.webp$")


@dataclass(frozen=True, slots=True)
class Thumbnail:
    body: bytes
    modified: float
    etag: str

    @classmethod
    def from_bytes(cls, body: bytes, modified: float) -> Thumbnail:
        return cls(body, modified, hashlib.sha256(body).hexdigest())


@dataclass(slots=True)
class _Pending:
    image_id: int
    invalidated: threading.Event = field(default_factory=threading.Event)
    task: asyncio.Task[Thumbnail] | None = None


@dataclass(slots=True)
class _Entry:
    signature: tuple[int, int, int, int]
    accessed: int
    etag: str | None = None

    @property
    def size(self) -> int:
        return self.signature[2]


def _signature(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _is_temporary(name: str) -> bool:
    return name.startswith(".thumbnail-") and name.endswith(".tmp")


def _validate_thumbnail(body: bytes) -> str:
    with Image.open(io.BytesIO(body)) as image:
        if image.format != "WEBP" or max(image.size) > THUMBNAIL_EDGE or getattr(image, "n_frames", 1) != 1:
            raise ValueError("Invalid thumbnail")
        image.load()
    return hashlib.sha256(body).hexdigest()


def make_thumbnail(body: bytes) -> bytes:
    """Decode in a worker, retaining color and alpha but no source metadata."""
    if not body or len(body) > MAX_SOURCE_BYTES:
        raise ComfyError("历史图片为空或超过缩略图处理大小限制")
    try:
        with Image.open(io.BytesIO(body)) as original:
            if original.format not in {"PNG", "JPEG", "WEBP"}:
                raise ComfyError("历史缩略图仅支持 PNG、JPEG、WebP 图片")
            if original.width * original.height > MAX_SOURCE_PIXELS:
                raise ComfyError("历史图片超过缩略图处理像素限制")
            if getattr(original, "n_frames", 1) != 1:
                raise ComfyError("历史缩略图仅支持单帧图片")
            ImageOps.exif_transpose(original, in_place=True)
            icc = original.info.get("icc_profile") or b""
            alpha = original.mode in {"RGBA", "LA", "PA"} or "transparency" in original.info
            image = original
            # A CMYK/gray/Lab profile cannot be attached unchanged to RGB pixels.
            if icc and original.mode in {"CMYK", "L", "LAB"}:
                try:
                    target = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
                    image = ImageCms.profileToProfile(
                        original, ImageCms.ImageCmsProfile(io.BytesIO(icc)), target, outputMode="RGB"
                    )
                    icc = target.tobytes()
                except (ImageCms.PyCMSError, OSError, ValueError):
                    icc = b""
            if image is original and original.mode != ("RGBA" if alpha else "RGB"):
                image = original.convert("RGBA" if alpha else "RGB")
            try:
                image.thumbnail((THUMBNAIL_EDGE, THUMBNAIL_EDGE), Image.Resampling.LANCZOS, reducing_gap=3.0)
                encoded = io.BytesIO()
                image.save(encoded, format="WEBP", quality=THUMBNAIL_QUALITY, method=4, icc_profile=icc)
                return encoded.getvalue()
            finally:
                if image is not original:
                    image.close()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
        raise ComfyError("无法读取历史图片或图片数据已损坏") from error


class ThumbnailStore:
    """Capacity LRU with eight cached reads and four cold generation jobs.

    File access times persist LRU order without a second on-disk database.
    Caller cancellation never cancels shared work; close drains all workers.
    """

    def __init__(
        self, directory: str | Path, client: Any, *,
        max_bytes: int = CACHE_MAX_BYTES, history: Any | None = None,
    ):
        self.path = Path(directory)
        self.client = client
        self.history = history
        self.max_bytes = max_bytes
        self._slots = asyncio.Semaphore(4)
        self._read_slots = asyncio.Semaphore(8)
        self._pending: dict[str, _Pending] = {}
        self._disk_lock = threading.RLock()
        self._scan_lock = threading.Lock()
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._temporary_sizes: dict[str, int] = {}
        self._active_temporaries: set[str] = set()
        self._failed_discards: set[str] = set()
        self._scan_dirty: set[str] | None = None
        self._total_bytes = 0
        self._reserved_bytes = 0
        self._closed = False
        self._stop = asyncio.Event()
        self._start_task: asyncio.Task[None] | None = None
        self._maintenance_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._closed:
            raise ComfyError("缩略图服务正在关闭")
        if self._start_task is None:
            self._start_task = asyncio.create_task(self._initialize())
        await asyncio.shield(self._start_task)

    async def _initialize(self) -> None:
        await asyncio.to_thread(self._maintain)
        if not self._closed:
            self._maintenance_task = asyncio.create_task(self._maintenance_loop())

    async def _maintenance_loop(self) -> None:
        while not self._closed:
            try:
                await asyncio.wait_for(self._stop.wait(), MAINTENANCE_INTERVAL_SECONDS)
                return
            except TimeoutError:
                await asyncio.to_thread(self._maintain)

    def _key(self, record: dict[str, Any]) -> str:
        identity = {
            "recipe": _RECIPE,
            "backend": str(getattr(self.client, "base_url", "local")).rstrip("/"),
            **{name: record.get(name) for name in (
                "id", "batch_id", "prompt_id", "created_at", "filename", "subfolder", "file_type"
            )},
        }
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
        return f"{int(record['id'])}-{digest}.webp"

    async def get(self, record: dict[str, Any]) -> Thumbnail:
        await self.start()
        if self._closed:
            raise ComfyError("缩略图服务正在关闭")
        key = self._key(record)
        pending = self._pending.get(key)
        if pending is None:
            pending = _Pending(int(record["id"]))
            self._pending[key] = pending
            pending.task = asyncio.create_task(self._load(key, record, pending))
            pending.task.add_done_callback(lambda task: self._completed(key, pending, task))
        return await asyncio.shield(pending.task)

    def _completed(self, key: str, pending: _Pending, task: asyncio.Task[Thumbnail]) -> None:
        if self._pending.get(key) is pending:
            del self._pending[key]
        # A disconnected caller may leave nobody to retrieve a failed result.
        if not task.cancelled():
            task.exception()

    async def _load(self, key: str, record: dict[str, Any], pending: _Pending) -> Thumbnail:
        async with self._read_slots:
            if pending.invalidated.is_set():
                raise KeyError(pending.image_id)
            cached = await asyncio.to_thread(self._read, key)
        if cached is None:
            async with self._slots:
                if pending.invalidated.is_set():
                    raise KeyError(pending.image_id)
                body, _content_type = await self.client.image_bytes(record, max_bytes=MAX_SOURCE_BYTES)
                # Keep the slot until conversion AND disk publication finish.
                cached = await asyncio.to_thread(self._convert_and_store, key, body, pending)
        if pending.invalidated.is_set():
            raise KeyError(pending.image_id)
        if self.history is not None:
            try:
                current = await self.history.get_image(pending.image_id)
                if self._key(current) != key:
                    raise KeyError(pending.image_id)
            except KeyError:
                # Also runs when all HTTP callers have disconnected.
                pending.invalidated.set()
                await asyncio.to_thread(self._discard_files, pending.image_id)
                raise
        return cached

    def _dirty(self, key: str) -> None:
        if self._scan_dirty is not None:
            self._scan_dirty.add(key)

    def _put_entry(self, key: str, entry: _Entry) -> None:
        previous = self._entries.pop(key, None)
        self._total_bytes += entry.size - (previous.size if previous else 0)
        self._entries[key] = entry
        self._dirty(key)

    def _forget(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        self._total_bytes -= entry.size if entry else self._temporary_sizes.pop(key, 0)
        self._dirty(key)

    def _read(self, key: str) -> Thumbnail | None:
        path = self.path / key
        signature = None
        try:
            # Keep only small file operations in this lock, so deleting a file
            # never races an open Windows file handle. Decode/hash happen below.
            with self._disk_lock:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_THUMBNAIL_BYTES:
                    self._remove(path)
                    return None
                signature = _signature(info)
                previous = self._entries.get(key)
                etag = previous.etag if previous and previous.signature == signature else None
                body = path.read_bytes()
                if len(body) != info.st_size or _signature(path.lstat()) != signature:
                    raise ValueError("Thumbnail changed while being read")
            if etag is None:
                etag = _validate_thumbnail(body)
            with self._disk_lock:
                # A concurrent eviction/deletion must not be undone by a late
                # validation result. The already-read bytes remain usable.
                try:
                    current = path.lstat()
                except FileNotFoundError:
                    current = None
                    self._forget(key)
                if current is not None and _signature(current) == signature:
                    accessed = time.time_ns()
                    self._put_entry(key, _Entry(signature, accessed, etag))
                    try:
                        os.utime(path, ns=(accessed, info.st_mtime_ns))
                    except OSError:
                        pass
            return Thumbnail(body, info.st_mtime, etag)
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
            with self._disk_lock:
                try:
                    if signature is None or _signature(path.lstat()) == signature:
                        self._remove(path)
                except FileNotFoundError:
                    self._forget(key)
                except OSError:
                    pass
            return None

    def _convert_and_store(self, key: str, body: bytes, pending: _Pending) -> Thumbnail:
        encoded = make_thumbnail(body)
        modified = time.time()
        etag = hashlib.sha256(encoded).hexdigest()
        temporary: str | None = None
        reserved = False
        try:
            with self._disk_lock:
                if pending.invalidated.is_set():
                    raise KeyError(pending.image_id)
                self._evict()
                if len(encoded) > min(MAX_THUMBNAIL_BYTES, self.max_bytes):
                    return Thumbnail(encoded, modified, etag)
                self._evict(len(encoded))
                if self._total_bytes + self._reserved_bytes + len(encoded) > self.max_bytes:
                    return Thumbnail(encoded, modified, etag)
                self._reserved_bytes += len(encoded)
                reserved = True
                self.path.mkdir(parents=True, exist_ok=True)
                descriptor, temporary = tempfile.mkstemp(prefix=".thumbnail-", suffix=".tmp", dir=self.path)
                self._active_temporaries.add(Path(temporary).name)
                self._dirty(Path(temporary).name)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
                info = os.fstat(stream.fileno())
            with self._disk_lock:
                if pending.invalidated.is_set():
                    raise KeyError(pending.image_id)
                os.replace(temporary, self.path / key)
                modified = info.st_mtime
                self._put_entry(key, _Entry(_signature(info), time.time_ns(), etag))
                self._failed_discards.discard(key)
                self._reserved_bytes -= len(encoded)
                reserved = False
        except OSError:
            # Cache failure must not prevent viewing the generated thumbnail.
            pass
        finally:
            with self._disk_lock:
                if temporary:
                    name = Path(temporary).name
                    self._active_temporaries.discard(name)
                    self._dirty(name)
                    try:
                        Path(temporary).unlink(missing_ok=True)
                    except OSError:
                        # Count an undeletable partial file after releasing its
                        # full-size reservation; maintenance will retry cleanup.
                        try:
                            size = Path(temporary).lstat().st_size
                            self._total_bytes += size - self._temporary_sizes.get(name, 0)
                            self._temporary_sizes[name] = size
                        except OSError:
                            # Conservatively retain the reservation size.
                            self._total_bytes += len(encoded) - self._temporary_sizes.get(name, 0)
                            self._temporary_sizes[name] = len(encoded)
                if reserved:
                    self._reserved_bytes -= len(encoded)
        return Thumbnail(encoded, modified, etag)

    def _remove(self, path: Path) -> bool:
        try:
            path.unlink(missing_ok=True)
            self._forget(path.name)
            return True
        except OSError:
            return False

    def _evict(self, incoming: int = 0) -> None:
        # No directory scan or sort: the in-memory order is updated on access.
        failed: list[tuple[str, _Entry]] = []
        while self._entries and self._total_bytes + self._reserved_bytes + incoming > self.max_bytes:
            key, entry = self._entries.popitem(last=False)
            try:
                (self.path / key).unlink(missing_ok=True)
                self._total_bytes -= entry.size
                self._dirty(key)
            except OSError:
                failed.append((key, entry))
        for key, entry in reversed(failed):
            self._entries[key] = entry
            self._entries.move_to_end(key, last=False)

    def _scan_directory(self) -> list[tuple[str, os.stat_result]] | None:
        entries: list[tuple[str, os.stat_result]] = []
        try:
            with os.scandir(self.path) as paths:
                for path in paths:
                    if _CACHE_NAME.fullmatch(path.name) or _is_temporary(path.name):
                        try:
                            entries.append((path.name, path.stat(follow_symlinks=False)))
                        except OSError:
                            pass
        except FileNotFoundError:
            return []
        except OSError:
            return None
        return sorted(entries, key=lambda item: (item[1].st_atime_ns, item[0]))

    def _maintain(self) -> None:
        """Reconcile outside request paths; do not overwrite concurrent changes."""
        with self._scan_lock:
            with self._disk_lock:
                self._scan_dirty = set()
            scanned = self._scan_directory()
            with self._disk_lock:
                dirty, self._scan_dirty = self._scan_dirty, None
                if scanned is None:
                    return
                entries: OrderedDict[str, _Entry] = OrderedDict()
                temporary_sizes: dict[str, int] = {}
                now = time.time()
                for key, info in scanned:
                    if key in dirty or key in self._active_temporaries:
                        continue
                    temporary = _is_temporary(key)
                    invalid = not stat.S_ISREG(info.st_mode) or (
                        now - info.st_mtime >= TEMP_TTL_SECONDS if temporary else info.st_size > MAX_THUMBNAIL_BYTES
                    )
                    if invalid:
                        try:
                            (self.path / key).unlink(missing_ok=True)
                            continue
                        except OSError:
                            pass
                    if temporary:
                        temporary_sizes[key] = info.st_size
                    else:
                        signature = _signature(info)
                        previous = self._entries.get(key)
                        etag = previous.etag if previous and previous.signature == signature else None
                        entries[key] = _Entry(signature, info.st_atime_ns, etag)
                # Dirty entries retain their current order, behind untouched
                # files: writes/reads during the scan are the newest accesses.
                for key, entry in self._entries.items():
                    if key in dirty:
                        entries[key] = entry
                for key, size in self._temporary_sizes.items():
                    if key in dirty:
                        temporary_sizes[key] = size
                self._entries = entries
                self._temporary_sizes = temporary_sizes
                self._total_bytes = sum(entry.size for entry in entries.values()) + sum(temporary_sizes.values())
                for key in tuple(self._failed_discards):
                    if self._remove(self.path / key):
                        self._failed_discards.discard(key)
                self._evict()

    async def discard(self, image_id: int) -> None:
        # Mark before yielding; a worker holding the disk lock may finish its
        # publication, then _discard_files removes it under that same lock.
        for pending in self._pending.values():
            if pending.image_id == image_id:
                pending.invalidated.set()
        await asyncio.to_thread(self._discard_files, image_id)

    def _discard_files(self, image_id: int) -> None:
        prefix = f"{int(image_id)}-"
        # Indexed keys cover normal operation; also find files created outside
        # this process, without making every cache read scan the directory.
        try:
            keys = {path.name for path in self.path.glob(f"{prefix}*.webp") if _CACHE_NAME.fullmatch(path.name)}
        except OSError:
            keys = set()
        with self._disk_lock:
            keys.update(key for key in self._entries if key.startswith(prefix))
            for key in keys:
                if self._remove(self.path / key):
                    self._failed_discards.discard(key)
                else:
                    self._failed_discards.add(key)
                    self._dirty(key)

    async def _drain(self) -> None:
        self._stop.set()
        if self._start_task is not None:
            await self._start_task
        if self._maintenance_task is not None:
            await self._maintenance_task
        tasks = [pending.task for pending in self._pending.values() if pending.task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self) -> None:
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._drain())
        await asyncio.shield(self._close_task)
