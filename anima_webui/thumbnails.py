"""Disposable, bounded thumbnails for history cards; originals stay in ComfyUI."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image, ImageCms, ImageOps, UnidentifiedImageError

from .comfy import ComfyError


THUMBNAIL_EDGE = 512
THUMBNAIL_QUALITY = 82
CACHE_MAX_BYTES = 256 * 1024 * 1024
CACHE_TTL_SECONDS = 24 * 60 * 60
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
    """Share requests and bound the whole download/decode pipeline to two jobs.

    Caller cancellation never cancels a shared job, including its worker thread.
    close() drains those jobs before the owning ComfyClient is closed.
    """

    def __init__(
        self, directory: str | Path, client: Any, *,
        max_bytes: int = CACHE_MAX_BYTES, ttl: float = CACHE_TTL_SECONDS,
        history: Any | None = None,
    ):
        self.path = Path(directory)
        self.client = client
        self.history = history
        self.max_bytes = max_bytes
        self.ttl = ttl
        self._slots = asyncio.Semaphore(2)
        self._pending: dict[str, _Pending] = {}
        self._disk_lock = threading.RLock()
        self._total_bytes = 0
        self._last_maintenance = 0.0
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

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
        async with self._slots:
            if pending.invalidated.is_set():
                raise KeyError(pending.image_id)
            cached = await asyncio.to_thread(self._read, key)
            if cached is None:
                body, _content_type = await self.client.image_bytes(record)
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
                    # This shared job also owns the final existence check: a
                    # disconnected HTTP caller must not leave a late orphan.
                    pending.invalidated.set()
                    await asyncio.to_thread(self._discard_files, pending.image_id)
                    raise
            return cached

    def _read(self, key: str) -> Thumbnail | None:
        with self._disk_lock:
            self._maintain()
            path = self.path / key
            try:
                stat = path.stat()
                if path.is_symlink() or time.time() - stat.st_mtime >= self.ttl or stat.st_size > MAX_THUMBNAIL_BYTES:
                    self._remove(path)
                    return None
                body = path.read_bytes()
                with Image.open(io.BytesIO(body)) as image:
                    if image.format != "WEBP" or max(image.size) > THUMBNAIL_EDGE or getattr(image, "n_frames", 1) != 1:
                        raise ValueError("Invalid thumbnail")
                    image.load()
                # LRU access time is separate from the generation/expiry time.
                try:
                    os.utime(path, ns=(time.time_ns(), stat.st_mtime_ns))
                except OSError:
                    pass
                return Thumbnail.from_bytes(body, stat.st_mtime)
            except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
                self._remove(path)
                return None

    def _convert_and_store(self, key: str, body: bytes, pending: _Pending) -> Thumbnail:
        encoded = make_thumbnail(body)
        modified = time.time()
        with self._disk_lock:
            if pending.invalidated.is_set():
                raise KeyError(pending.image_id)
            self._maintain()
            temporary = None
            try:
                self.path.mkdir(parents=True, exist_ok=True)
                path = self.path / key
                previous_size = path.stat().st_size if path.exists() else 0
                descriptor, temporary = tempfile.mkstemp(prefix=".thumbnail-", suffix=".tmp", dir=self.path)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                modified = path.stat().st_mtime
                self._total_bytes += len(encoded) - previous_size
                if self._total_bytes > self.max_bytes:
                    self._maintain(force=True)
            except OSError:
                # A full/read-only cache must not prevent viewing this thumbnail.
                pass
            finally:
                if temporary:
                    try:
                        Path(temporary).unlink(missing_ok=True)
                    except OSError:
                        pass
        return Thumbnail.from_bytes(encoded, modified)

    def _remove(self, path: Path) -> None:
        try:
            size = path.stat().st_size
            path.unlink(missing_ok=True)
            self._total_bytes = max(0, self._total_bytes - size)
        except OSError:
            pass

    def _maintain(self, *, force: bool = False) -> None:
        """Called under _disk_lock; scan at most once a minute unless over budget."""
        now = time.time()
        if not force and now - self._last_maintenance < 60:
            return
        self._last_maintenance = now
        entries = []
        try:
            for path in self.path.iterdir():
                if not _CACHE_NAME.fullmatch(path.name):
                    if path.name.startswith(".thumbnail-") and path.suffix == ".tmp":
                        try:
                            if now - path.stat().st_mtime >= self.ttl:
                                path.unlink(missing_ok=True)
                        except OSError:
                            pass
                    continue
                try:
                    stat = path.stat()
                    if path.is_symlink() or now - stat.st_mtime >= self.ttl:
                        path.unlink(missing_ok=True)
                    else:
                        entries.append((stat.st_atime, path, stat.st_size))
                except OSError:
                    pass
        except OSError:
            return
        self._total_bytes = sum(item[2] for item in entries)
        for _accessed, path, _size in sorted(entries):
            if self._total_bytes <= self.max_bytes:
                break
            self._remove(path)

    async def discard(self, image_id: int) -> None:
        # Mark before yielding; a worker holding the disk lock may finish its
        # publication, then _discard_files removes it under that same lock.
        for pending in self._pending.values():
            if pending.image_id == image_id:
                pending.invalidated.set()
        await asyncio.to_thread(self._discard_files, image_id)

    def _discard_files(self, image_id: int) -> None:
        with self._disk_lock:
            try:
                for path in self.path.glob(f"{int(image_id)}-*.webp"):
                    if _CACHE_NAME.fullmatch(path.name):
                        self._remove(path)
            except OSError:
                pass

    async def _drain(self) -> None:
        tasks = [pending.task for pending in self._pending.values() if pending.task is not None]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self) -> None:
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._drain())
        await asyncio.shield(self._close_task)
