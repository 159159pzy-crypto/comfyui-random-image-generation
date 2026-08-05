from __future__ import annotations

import hashlib
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
import re

from PIL import Image, UnidentifiedImageError


class AssetError(ValueError):
    pass


@dataclass(frozen=True)
class NaturalAsset:
    id: str
    path: Path
    media_type: str
    width: int
    height: int
    size: int
    sha256: str
    created_at: float

    def public(self) -> dict[str, object]:
        return {
            "id": self.id,
            "media_type": self.media_type,
            "width": self.width,
            "height": self.height,
            "size": self.size,
            "created_at": self.created_at,
        }


class AssetStore:
    EXTENSIONS = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}
    MEDIA_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
    ASSET_NAME = re.compile(r"^(asset_[0-9a-f]{16})(\.png|\.jpg|\.webp)$", re.IGNORECASE)

    def __init__(
        self,
        root: str | Path,
        *,
        max_bytes: int = 20 * 1024 * 1024,
        max_pixels: int = 40_000_000,
        ttl_seconds: int = 24 * 3600,
        max_items: int = 200,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.max_pixels = max_pixels
        self.ttl_seconds = ttl_seconds
        self.max_items = max(1, int(max_items))
        self._items: dict[str, NaturalAsset] = {}
        self._rebuild()
        self.prune()

    def _inspect(self, path: Path, asset_id: str) -> NaturalAsset:
        stat = path.stat()
        if stat.st_size <= 0 or stat.st_size > self.max_bytes:
            raise AssetError("图片文件大小无效")
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                image_format = str(image.format or "").upper()
                width, height = image.size
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise AssetError("文件不是有效图片") from exc
        if image_format not in self.EXTENSIONS:
            raise AssetError("不支持的图片格式")
        if path.suffix.casefold() != self.EXTENSIONS[image_format]:
            raise AssetError("图片扩展名与内容不一致")
        if width <= 0 or height <= 0 or width * height > self.max_pixels:
            raise AssetError("图片像素总量超过安全上限")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return NaturalAsset(
            id=asset_id,
            path=path,
            media_type=self.MEDIA_TYPES[image_format],
            width=width,
            height=height,
            size=stat.st_size,
            sha256=digest.hexdigest(),
            created_at=stat.st_mtime,
        )

    @staticmethod
    def _remove(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _rebuild(self) -> None:
        for path in self.root.iterdir():
            if not path.is_file() or path.is_symlink():
                if path.is_symlink():
                    self._remove(path)
                continue
            match = self.ASSET_NAME.fullmatch(path.name)
            if match is None:
                self._remove(path)
                continue
            asset_id = match.group(1).casefold()
            try:
                asset = self._inspect(path, asset_id)
            except (AssetError, OSError):
                self._remove(path)
                continue
            self._items[asset_id] = asset

    def add(self, data: bytes) -> NaturalAsset:
        if not data:
            raise AssetError("图片内容为空")
        if len(data) > self.max_bytes:
            raise AssetError(f"图片超过 {self.max_bytes // (1024 * 1024)}MB 上限")
        fd, temporary = tempfile.mkstemp(prefix=".upload-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                with Image.open(temporary) as image:
                    image.verify()
                with Image.open(temporary) as image:
                    image_format = str(image.format or "").upper()
                    width, height = image.size
            except (UnidentifiedImageError, OSError, ValueError) as exc:
                raise AssetError("文件不是有效的 PNG、JPEG 或 WebP 图片") from exc
            if image_format not in self.EXTENSIONS:
                raise AssetError("只支持 PNG、JPEG 和 WebP 图片")
            if width <= 0 or height <= 0 or width * height > self.max_pixels:
                raise AssetError("图片像素总量超过安全上限")
            asset_id = f"asset_{uuid.uuid4().hex[:16]}"
            destination = self.root / f"{asset_id}{self.EXTENSIONS[image_format]}"
            os.replace(temporary, destination)
            asset = NaturalAsset(
                id=asset_id,
                path=destination,
                media_type=self.MEDIA_TYPES[image_format],
                width=width,
                height=height,
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                created_at=time.time(),
            )
            self._items[asset_id] = asset
            self.prune()
            return asset
        finally:
            if os.path.exists(temporary):
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def get(self, asset_id: str) -> NaturalAsset:
        self.prune()
        asset = self._items.get(str(asset_id))
        if asset is None or not asset.path.is_file():
            raise AssetError("图片资产不存在或已过期")
        return asset

    def prune(self) -> None:
        cutoff = time.time() - self.ttl_seconds
        for asset_id, asset in tuple(self._items.items()):
            if asset.created_at >= cutoff:
                continue
            self._remove(asset.path)
            self._items.pop(asset_id, None)
        overflow = len(self._items) - self.max_items
        if overflow > 0:
            oldest = sorted(self._items.values(), key=lambda asset: asset.created_at)[:overflow]
            for asset in oldest:
                self._remove(asset.path)
                self._items.pop(asset.id, None)
        # Clean abandoned temp files and any file that has no reconstructed record.
        known = {asset.path.name for asset in self._items.values()}
        for path in self.root.iterdir():
            if path.is_file() and path.name not in known:
                self._remove(path)
