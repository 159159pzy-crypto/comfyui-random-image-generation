"""Durable preset covers, serialized with their references and garbage collection."""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import math
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from .async_utils import serialized_mutation
from .workflow import WorkflowError

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000


logger = logging.getLogger(__name__)
COVER_ID = re.compile(r"^[a-f0-9]{64}$")
COVER_SIZE = (768, 576)
RETENTION_SECONDS = 24 * 60 * 60
MAX_COVER_BYTES = 4 * 1024 * 1024


class StylePresetCoverStore:
    """One lock covers uploads, reference commits, reads, and orphan collection.

    The store's cancellation boundary waits for an in-flight worker/JSON commit,
    so collection cannot delete a cover between validation and saving its ID.
    """

    def __init__(self, directory: str | Path, presets: Any):
        self.path = Path(directory)
        self.path.mkdir(parents=True, exist_ok=True)
        self.presets = presets
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task | None = None
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise WorkflowError("封面服务正在关闭")

    @staticmethod
    def url(asset_id: str | None) -> str | None:
        return f"/api/style-preset-covers/{asset_id}" if asset_id else None

    def _path(self, asset_id: str) -> Path:
        if not isinstance(asset_id, str) or not COVER_ID.fullmatch(asset_id):
            raise WorkflowError("风格预设封面 ID 无效")
        target = self.path / f"{asset_id}.webp"
        if target.is_symlink() or not target.resolve().is_relative_to(self.path.resolve()):
            raise WorkflowError("风格预设封面路径无效")
        return target

    def _read(self, asset_id: str) -> bytes:
        target = self._path(asset_id)
        try:
            with target.open("rb") as stream:
                body = stream.read(MAX_COVER_BYTES + 1)
        except FileNotFoundError as error:
            raise WorkflowError("封面已过期或不存在，请重新导入") from error
        if len(body) > MAX_COVER_BYTES or hashlib.sha256(body).hexdigest() != asset_id:
            raise WorkflowError("封面文件已损坏，请重新导入")
        return body

    @staticmethod
    def _crop_box(crop: Any, width: int, height: int) -> tuple[float, float, float, float]:
        if crop is None:
            crop_width = min(width, height * 4 / 3)
            crop_height = crop_width * 3 / 4
            left, top = (width - crop_width) / 2, (height - crop_height) / 2
            return left, top, left + crop_width, top + crop_height
        if not isinstance(crop, dict) or set(crop) != {"x", "y", "width", "height"}:
            raise WorkflowError("封面裁切需要 x、y、width、height")
        for value in crop.values():
            if type(value) not in (int, float) or not math.isfinite(value):
                raise WorkflowError("封面裁切坐标必须是有限数字")
        x, y, w, h = (crop[key] for key in ("x", "y", "width", "height"))
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1.000001 or y + h > 1.000001:
            raise WorkflowError("封面裁切范围超出图片")
        if abs((w * width) / (h * height) - 4 / 3) > .015:
            raise WorkflowError("封面裁切比例必须为 4:3")
        return x * width, y * height, min(width, (x + w) * width), min(height, (y + h) * height)

    def _add(self, body: bytes, crop: Any) -> dict[str, Any]:
        if not body or len(body) > MAX_IMAGE_BYTES:
            raise WorkflowError("封面图片必须非空且不超过 20 MiB")
        try:
            with Image.open(io.BytesIO(body)) as original:
                if original.format not in {"PNG", "JPEG", "WEBP"}:
                    raise WorkflowError("封面仅支持 PNG、JPEG、WebP")
                if original.width * original.height > MAX_IMAGE_PIXELS:
                    raise WorkflowError("封面图片不能超过 4000 万像素")
                if getattr(original, "n_frames", 1) != 1:
                    raise WorkflowError("封面仅支持单帧图片")
                original.load()
                with ImageOps.exif_transpose(original) as oriented:
                    box = self._crop_box(crop, oriented.width, oriented.height)
                    mode = "RGBA" if "A" in oriented.getbands() or "transparency" in oriented.info else "RGB"
                    with oriented.convert(mode) as converted:
                        with converted.resize(COVER_SIZE, Image.Resampling.LANCZOS, box=box) as cover:
                            stream = io.BytesIO()
                            cover.save(stream, format="WEBP", quality=90, method=4)
                            encoded = stream.getvalue()
        except WorkflowError:
            raise
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
            raise WorkflowError("无法读取封面图片，图片可能已损坏") from error
        asset_id = hashlib.sha256(encoded).hexdigest()
        target = self._path(asset_id)
        fd, temporary = tempfile.mkstemp(prefix=".upload-", dir=self.path)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {"id": asset_id, "url": self.url(asset_id), "width": COVER_SIZE[0], "height": COVER_SIZE[1]}

    @serialized_mutation
    async def add(self, body: bytes, crop: Any = None) -> dict[str, Any]:
        self._ensure_open()
        self.presets.ensure_writable()
        return await asyncio.to_thread(self._add, body, crop)

    @serialized_mutation
    async def read(self, asset_id: str) -> bytes:
        self._ensure_open()
        return await asyncio.to_thread(self._read, asset_id)

    def _references(self) -> set[str]:
        return {item["cover_id"] for item in self.presets.items if item.get("cover_id")}

    @serialized_mutation
    async def mutate(self, method: Any, *args: Any, payload: dict[str, Any] | None = None) -> Any:
        self._ensure_open()
        self.presets.ensure_writable()
        if payload is not None and payload.get("cover_id") is not None:
            await asyncio.to_thread(self._read, payload["cover_id"])
        previous = self._references()
        result = await method(*args)
        # Removing a long-lived reference starts a fresh grace period. A failed
        # mutation never changes references or removes the previous cover.
        for asset_id in previous - self._references():
            try:
                await asyncio.to_thread(os.utime, self._path(asset_id), None)
            except OSError:
                logger.warning("无法更新已移除封面的清理时间: %s", asset_id)
        return result

    def _collect(self, referenced: set[str], now: float) -> int:
        removed = 0
        for target in self.path.iterdir():
            is_cover = target.suffix == ".webp" and COVER_ID.fullmatch(target.stem)
            is_temporary = target.name.startswith(".upload-")
            if not (is_cover or is_temporary) or target.is_symlink() or not target.is_file():
                continue
            if is_cover and target.stem in referenced:
                continue
            if now - target.stat().st_mtime > RETENTION_SECONDS:
                target.unlink()
                removed += 1
        return removed

    @serialized_mutation
    async def collect(self, *, now: float | None = None) -> int:
        # A successful migration can have informational warnings and still own
        # every reference. Only incomplete recovery or write protection suspends
        # collection; the completeness marker survives a recovery restart.
        if self._closed or self.presets.read_only_reason or not self.presets.cover_references_complete:
            return 0
        return await asyncio.to_thread(self._collect, self._references(), time.time() if now is None else now)

    def start(self) -> None:
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _cleanup_loop(self) -> None:
        while True:
            try:
                await self.collect()
            except Exception:
                logger.exception("风格预设临时封面清理失败")
            await asyncio.sleep(60 * 60)

    async def close(self) -> None:
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
        # Wait for any cancelled request's still-running worker/commit.
        async with self._lock:
            self._closed = True
