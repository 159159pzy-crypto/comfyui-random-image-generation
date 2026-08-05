"""Native Studio management services shared by both V7 workspaces."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import os
import random
import re
import sqlite3
import threading
import time
import unicodedata
import uuid
from collections.abc import Awaitable, Callable, Mapping, MutableMapping, Sequence
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar
from urllib.parse import urlparse

import aiohttp

from .natural_runtime import NativeNaturalSettings


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _clean(value: Any, *, limit: int = 512) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    if len(text) > limit:
        raise ValueError(f"text exceeds {limit} characters")
    return text


def _strings(value: Any, *, limit: int = 64) -> tuple[str, ...]:
    if value is None:
        return ()
    raw = (value,) if isinstance(value, str) else value
    if isinstance(raw, Mapping):
        raise TypeError("expected text or a text sequence")
    result: list[str] = []
    for item in raw:
        text = _clean(item)
        if text and text.casefold() not in {entry.casefold() for entry in result}:
            result.append(text)
        if len(result) > limit:
            raise ValueError(f"text sequence exceeds {limit} entries")
    return tuple(result)


class PromptAssetError(ValueError):
    pass


class PromptPlanConflictError(RuntimeError):
    def __init__(self, current: Mapping[str, Any]) -> None:
        super().__init__("prompt plan revision or digest is stale")
        self.current = dict(current)


class PromptPlanStore:
    """Native prompt-plan repository with optimistic concurrency control."""

    _SCHEMA = 1
    _ID_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
    _METADATA_KEYS = frozenset(
        {"id", "name", "description", "plan", "revision", "digest", "created_at", "updated_at"}
    )

    def __init__(self, database_path: str | os.PathLike[str]) -> None:
        self.database_path = Path(database_path).expanduser().resolve(strict=False)
        self._lock = threading.RLock()
        self._ensure()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _ensure(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS prompt_plans (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    description TEXT NOT NULL DEFAULT '',
                    plan_json TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_prompt_plans_updated
                    ON prompt_plans(updated_at DESC, id);
                """
            )

    @classmethod
    def _normalized(
        cls, payload: Mapping[str, Any], plan_id: str = ""
    ) -> dict[str, Any]:
        identifier = _clean(
            plan_id or payload.get("id") or f"prompt_plan_{uuid.uuid4().hex[:16]}",
            limit=128,
        )
        if not cls._ID_PATTERN.fullmatch(identifier):
            raise ValueError("prompt plan id contains unsupported characters")
        name = _clean(payload.get("name") or payload.get("title"), limit=120)
        if not name:
            raise ValueError("prompt plan name is required")
        description = _clean(payload.get("description"), limit=2000)
        raw_plan = payload.get("plan")
        if raw_plan is None:
            raw_plan = {
                str(key): value
                for key, value in payload.items()
                if str(key) not in cls._METADATA_KEYS and str(key) != "title"
            }
        if not isinstance(raw_plan, Mapping):
            raise TypeError("prompt plan payload must be an object")
        plan = dict(raw_plan)
        try:
            encoded = _stable_json(plan)
        except (TypeError, ValueError) as error:
            raise ValueError("prompt plan payload must be JSON serializable") from error
        if len(encoded.encode("utf-8")) > 256 * 1024:
            raise ValueError("prompt plan payload exceeds 256KB")
        return {
            "id": identifier,
            "name": name,
            "description": description,
            "plan": plan,
        }

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        plan = json.loads(row["plan_json"])
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "plan": plan if isinstance(plan, dict) else {},
            "revision": int(row["revision"]),
            "digest": row["digest"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list(self, *, query: str = "") -> dict[str, Any]:
        statement = "SELECT * FROM prompt_plans"
        values: list[Any] = []
        if query.strip():
            statement += " WHERE name LIKE ? OR description LIKE ?"
            needle = f"%{query.strip()}%"
            values.extend((needle, needle))
        statement += " ORDER BY updated_at DESC, name COLLATE NOCASE, id"
        with closing(self._connect()) as connection:
            items = [self._public(row) for row in connection.execute(statement, values)]
        return {"items": items, "count": len(items), "schema_version": self._SCHEMA}

    def get(self, plan_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM prompt_plans WHERE id=?", (str(plan_id),)
            ).fetchone()
        if row is None:
            raise KeyError(plan_id)
        return self._public(row)

    @staticmethod
    def _digest(record: Mapping[str, Any]) -> str:
        return hashlib.sha256(_stable_json(record).encode("utf-8")).hexdigest()

    def create(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        record = self._normalized(payload)
        digest = self._digest(record)
        now = _utc_now()
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM prompt_plans WHERE id=? OR name=? COLLATE NOCASE",
                (record["id"], record["name"]),
            ).fetchone()
            if current is not None:
                connection.rollback()
                raise PromptPlanConflictError(self._public(current))
            connection.execute(
                """
                INSERT INTO prompt_plans(
                    id, name, description, plan_json, revision, digest,
                    created_at, updated_at
                ) VALUES(?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    record["id"],
                    record["name"],
                    record["description"],
                    _stable_json(record["plan"]),
                    digest,
                    now,
                    now,
                ),
            )
            connection.commit()
        return self.get(record["id"])

    @staticmethod
    def _check_expected(
        current: Mapping[str, Any], *, expected_revision: int, expected_digest: str
    ) -> None:
        if (
            int(expected_revision) != int(current["revision"])
            or str(expected_digest) != str(current["digest"])
        ):
            raise PromptPlanConflictError(current)

    def update(
        self,
        plan_id: str,
        payload: Mapping[str, Any],
        *,
        expected_revision: int,
        expected_digest: str,
    ) -> dict[str, Any]:
        record = self._normalized(payload, plan_id)
        digest = self._digest(record)
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM prompt_plans WHERE id=?", (record["id"],)
            ).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError(plan_id)
            current = self._public(row)
            try:
                self._check_expected(
                    current,
                    expected_revision=expected_revision,
                    expected_digest=expected_digest,
                )
            except PromptPlanConflictError:
                connection.rollback()
                raise
            if digest == current["digest"]:
                connection.rollback()
                return current
            duplicate = connection.execute(
                "SELECT * FROM prompt_plans WHERE name=? COLLATE NOCASE AND id<>?",
                (record["name"], record["id"]),
            ).fetchone()
            if duplicate is not None:
                connection.rollback()
                raise PromptPlanConflictError(self._public(duplicate))
            connection.execute(
                """
                UPDATE prompt_plans
                SET name=?, description=?, plan_json=?, revision=?, digest=?, updated_at=?
                WHERE id=?
                """,
                (
                    record["name"],
                    record["description"],
                    _stable_json(record["plan"]),
                    current["revision"] + 1,
                    digest,
                    _utc_now(),
                    record["id"],
                ),
            )
            connection.commit()
        return self.get(record["id"])

    def delete(
        self,
        plan_id: str,
        *,
        expected_revision: int,
        expected_digest: str,
    ) -> dict[str, Any]:
        with self._lock, closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM prompt_plans WHERE id=?", (str(plan_id),)
            ).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError(plan_id)
            current = self._public(row)
            try:
                self._check_expected(
                    current,
                    expected_revision=expected_revision,
                    expected_digest=expected_digest,
                )
            except PromptPlanConflictError:
                connection.rollback()
                raise
            connection.execute("DELETE FROM prompt_plans WHERE id=?", (str(plan_id),))
            connection.commit()
        return current


class PromptAssetLibrary:
    """SQLite-backed native prompt asset repository."""

    _SCHEMA = 1

    def __init__(self, database_path: str | os.PathLike[str]) -> None:
        self.database_path = Path(database_path).expanduser().resolve(strict=False)
        self._lock = threading.RLock()
        self._ensure()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS prompt_assets (
                    asset_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    search_text TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    custom INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_prompt_assets_source
                    ON prompt_assets(source);
                CREATE INDEX IF NOT EXISTS idx_prompt_assets_type
                    ON prompt_assets(asset_type);
                CREATE TABLE IF NOT EXISTS prompt_asset_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO prompt_asset_meta(key, value) VALUES('revision', '0')"
            )

    @staticmethod
    def _record(raw: Mapping[str, Any], source: str) -> dict[str, Any]:
        asset_type = _clean(raw.get("asset_type") or raw.get("type"), limit=64)
        if not asset_type:
            raise PromptAssetError("prompt asset requires asset_type")
        name = _clean(
            raw.get("name") or raw.get("name_en") or raw.get("name_zh") or raw.get("label"),
            limit=256,
        )
        tags = _strings(raw.get("tags"))
        categories = _strings(raw.get("categories"))
        traits = _strings(raw.get("traits"))
        aliases = _strings(raw.get("aliases"))
        asset_id = _clean(raw.get("asset_id") or raw.get("id"), limit=128)
        if not asset_id:
            digest = hashlib.sha256(
                _stable_json([source, asset_type, name, tags]).encode("utf-8")
            ).hexdigest()[:32]
            asset_id = f"pa_{digest}"
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", asset_id):
            raise PromptAssetError("prompt asset id contains unsupported characters")
        result = dict(raw)
        result.update(
            {
                "asset_id": asset_id,
                "asset_type": asset_type,
                "name": name,
                "tags": list(tags),
                "categories": list(categories),
                "traits": list(traits),
                "aliases": list(aliases),
                "source": source,
            }
        )
        return result

    def import_bytes(
        self,
        data: bytes,
        *,
        source: str,
        content_type: str = "application/json",
        provenance: Mapping[str, Any] | None = None,
        mode: str = "replace_source",
    ) -> dict[str, Any]:
        del provenance
        normalized_source = _clean(source, limit=128)
        if not normalized_source:
            raise PromptAssetError("prompt asset source is required")
        if len(data) > 16 * 1024 * 1024:
            raise PromptAssetError("prompt asset import exceeds 16MB")
        try:
            if "csv" in content_type.casefold():
                records = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
            else:
                payload = json.loads(data.decode("utf-8-sig"))
                records = payload.get("assets", payload) if isinstance(payload, Mapping) else payload
        except (UnicodeError, csv.Error, json.JSONDecodeError) as exc:
            raise PromptAssetError("prompt asset import is invalid") from exc
        if not isinstance(records, list):
            raise PromptAssetError("prompt asset import must contain an assets array")
        prepared = [self._record(item, normalized_source) for item in records if isinstance(item, Mapping)]
        if len(prepared) != len(records):
            raise PromptAssetError("every prompt asset must be an object")
        if len(prepared) > 100_000:
            raise PromptAssetError("prompt asset import exceeds 100000 records")
        if mode not in {"replace_source", "merge"}:
            raise PromptAssetError("prompt asset import mode must be replace_source or merge")
        with self._lock, closing(self._connect()) as connection, connection:
            if mode == "replace_source":
                connection.execute("DELETE FROM prompt_assets WHERE source = ?", (normalized_source,))
            now = _utc_now()
            for item in prepared:
                search_text = " ".join(
                    str(value)
                    for value in (
                        item["name"],
                        item.get("name_en", ""),
                        item.get("name_zh", ""),
                        *item["tags"],
                        *item["categories"],
                        *item["traits"],
                        *item["aliases"],
                    )
                ).casefold()
                connection.execute(
                    """
                    INSERT INTO prompt_assets(
                        asset_id, source, asset_type, name, search_text, payload_json,
                        favorite, custom, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, 0, 0, ?)
                    ON CONFLICT(asset_id) DO UPDATE SET
                        source=excluded.source, asset_type=excluded.asset_type,
                        name=excluded.name, search_text=excluded.search_text,
                        payload_json=excluded.payload_json, updated_at=excluded.updated_at
                    """,
                    (
                        item["asset_id"], normalized_source, item["asset_type"],
                        item["name"], search_text, _stable_json(item), now,
                    ),
                )
            connection.execute(
                "UPDATE prompt_asset_meta SET value = CAST(value AS INTEGER) + 1 WHERE key='revision'"
            )
        return {**self.status(), "last_import_count": len(prepared), "source": normalized_source}

    def import_file(self, path: str | os.PathLike[str], **options: Any) -> dict[str, Any]:
        source_path = Path(path)
        media = "text/csv" if source_path.suffix.casefold() == ".csv" else "application/json"
        return self.import_bytes(source_path.read_bytes(), content_type=media, **options)

    async def update_from_url(self, url: str, **options: Any) -> dict[str, Any]:
        parsed = urlparse(str(url or ""))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            raise PromptAssetError("prompt asset URL must be an uncredentialed HTTP(S) URL")
        timeout = float(options.pop("timeout", 30))
        source = str(options.pop("source", url))
        max_bytes = min(int(options.pop("max_bytes", 16 * 1024 * 1024)), 16 * 1024 * 1024)
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session,
            session.get(
                url,
                allow_redirects=False,
                headers={"User-Agent": "Anima-Studio/7"},
            ) as response,
        ):
            if response.status >= 300:
                raise PromptAssetError(f"prompt asset server returned HTTP {response.status}")
            data = await response.content.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise PromptAssetError("prompt asset response exceeds configured limit")
            return self.import_bytes(
                data,
                source=source,
                content_type=response.headers.get("Content-Type", "application/json"),
                **options,
            )

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        item = json.loads(row["payload_json"])
        item["favorite"] = bool(row["favorite"])
        item["custom"] = bool(row["custom"])
        return item

    def status(self) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            count = int(connection.execute("SELECT COUNT(*) FROM prompt_assets").fetchone()[0])
            custom = int(connection.execute("SELECT COUNT(*) FROM prompt_assets WHERE custom=1").fetchone()[0])
            favorite = int(connection.execute("SELECT COUNT(*) FROM prompt_assets WHERE favorite=1").fetchone()[0])
            revision = connection.execute("SELECT value FROM prompt_asset_meta WHERE key='revision'").fetchone()[0]
        return {
            "schema_version": self._SCHEMA,
            "asset_count": count,
            "custom_count": custom,
            "favorite_count": favorite,
            "revision": str(revision),
            "database_path": str(self.database_path),
        }

    def search(
        self,
        query: str = "",
        *,
        asset_type: str = "",
        categories: Sequence[str] = (),
        traits: Sequence[str] = (),
        tags: Sequence[str] = (),
        favorite_only: bool = False,
        custom_only: bool | None = None,
        page: int = 1,
        page_size: int = 50,
        **_: Any,
    ) -> dict[str, Any]:
        where: list[str] = []
        params: list[Any] = []
        if query.strip():
            where.append("search_text LIKE ?")
            params.append(f"%{query.strip().casefold()}%")
        if asset_type.strip():
            where.append("asset_type = ? COLLATE NOCASE")
            params.append(asset_type.strip())
        if favorite_only:
            where.append("favorite = 1")
        if custom_only is not None:
            where.append("custom = ?")
            params.append(int(bool(custom_only)))
        statement = "SELECT * FROM prompt_assets"
        if where:
            statement += " WHERE " + " AND ".join(where)
        statement += " ORDER BY name COLLATE NOCASE, asset_id"
        with closing(self._connect()) as connection:
            items = [self._public(row) for row in connection.execute(statement, params)]
        required = [*categories, *traits, *tags]
        if required:
            needles = {str(item).casefold() for item in required}
            items = [
                item for item in items
                if needles.issubset(
                    {str(value).casefold() for key in ("categories", "traits", "tags") for value in item.get(key, [])}
                )
            ]
        size = max(1, min(int(page_size), 200))
        current = max(1, int(page))
        start = (current - 1) * size
        return {"items": items[start : start + size], "total": len(items), "page": current, "page_size": size}

    def facets(self, *, limit: int = 200, **filters: Any) -> dict[str, Any]:
        filters.pop("page", None)
        filters.pop("page_size", None)
        items = self.search(page=1, page_size=200, **filters)["items"]
        result: dict[str, list[dict[str, Any]]] = {}
        for field in ("asset_type", "categories", "traits", "tags"):
            counts: dict[str, int] = {}
            for item in items:
                values = [item.get(field)] if field == "asset_type" else item.get(field, [])
                for value in values:
                    if value:
                        counts[str(value)] = counts.get(str(value), 0) + 1
            result[field if field != "asset_type" else "asset_types"] = [
                {"value": value, "count": count}
                for value, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0].casefold()))[: max(1, min(int(limit), 200))]
            ]
        return result

    def get(self, asset_id: str) -> dict[str, Any]:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT * FROM prompt_assets WHERE asset_id=?", (asset_id,)).fetchone()
        if row is None:
            raise KeyError(asset_id)
        return self._public(row)

    def create_custom(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        record = self._record(payload, "custom")
        self.import_bytes(_stable_json({"assets": [record]}).encode(), source="custom", mode="merge")
        with closing(self._connect()) as connection, connection:
            connection.execute("UPDATE prompt_assets SET custom=1 WHERE asset_id=?", (record["asset_id"],))
        return self.get(record["asset_id"])

    def update_custom(self, asset_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        current = self.get(asset_id)
        if not current.get("custom"):
            raise PromptAssetError("only custom prompt assets can be changed")
        return self.create_custom({**current, **dict(payload), "asset_id": asset_id})

    def delete_custom(self, asset_id: str) -> dict[str, Any]:
        current = self.get(asset_id)
        if not current.get("custom"):
            raise PromptAssetError("only custom prompt assets can be deleted")
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM prompt_assets WHERE asset_id=?", (asset_id,))
        return current

    def set_favorite(self, asset_id: str, favorite: bool = True) -> dict[str, Any]:
        with closing(self._connect()) as connection, connection:
            changed = connection.execute("UPDATE prompt_assets SET favorite=? WHERE asset_id=?", (int(favorite), asset_id)).rowcount
        if not changed:
            raise KeyError(asset_id)
        return self.get(asset_id)


PROMPT_LAB_LAYERS = ("identity", "clothing", "pose", "camera", "background", "style", "relation", "lora")
_LAYER_ALIASES = {"character": "identity", "outfit": "clothing", "artist": "style"}


@dataclass(frozen=True)
class PromptLabCandidate:
    candidate_id: str
    ordinal: int
    layers: dict[str, Any]
    selected_assets: tuple[tuple[str, str], ...]
    locked_layers: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class PromptLabBatch:
    batch_id: str
    seed: str
    seed_value: int
    requested_count: int
    candidates: tuple[PromptLabCandidate, ...]
    locked_layers: tuple[str, ...]
    enabled_asset_types: tuple[str, ...]
    negative_prompt: str = ""

    def find_candidate(self, selection: int | str) -> PromptLabCandidate:
        if isinstance(selection, int) and not isinstance(selection, bool):
            if 1 <= selection <= len(self.candidates):
                return self.candidates[selection - 1]
            raise ValueError("candidate ordinal is out of range")
        text = str(selection).strip()
        if text.isdecimal():
            return self.find_candidate(int(text))
        for candidate in self.candidates:
            if candidate.candidate_id == text:
                return candidate
        raise ValueError("candidate does not exist")


class PromptLab:
    """Bounded deterministic prompt candidate planner."""

    @staticmethod
    def _asset(layer: str, raw: Any, index: int) -> tuple[str, tuple[str, ...], str]:
        if isinstance(raw, Mapping):
            asset_id = _clean(raw.get("asset_id") or raw.get("id") or f"{layer}-{index}", limit=128)
            values = raw.get("tags", raw.get("prompt", raw.get("value", ())))
            relation = _clean(raw.get("relation") or raw.get("scene_sentence"), limit=2048)
        else:
            asset_id = f"{layer}-{index}"
            values = raw
            relation = _clean(raw, limit=2048) if layer == "relation" else ""
        terms = () if layer == "relation" else _strings(values)
        return asset_id, terms, relation

    def generate_candidates(
        self,
        *,
        seed: int | str = 0,
        count: int = 1,
        base_layers: Mapping[str, Any] | None = None,
        asset_pools: Mapping[str, Sequence[Any]] | None = None,
        locked_layers: Sequence[str] = (),
        negative_prompt: str = "",
        **_: Any,
    ) -> PromptLabBatch:
        requested = max(1, min(int(count), 6))
        seed_text = str(seed)
        seed_value = int.from_bytes(hashlib.sha256(seed_text.encode()).digest()[:8], "big")
        bases: dict[str, Any] = {layer: () for layer in PROMPT_LAB_LAYERS}
        for raw_layer, values in dict(base_layers or {}).items():
            layer = _LAYER_ALIASES.get(str(raw_layer), str(raw_layer))
            if layer not in bases:
                raise ValueError(f"unknown prompt layer: {raw_layer}")
            bases[layer] = _clean(values, limit=2048) if layer == "relation" and isinstance(values, str) else _strings(values)
        pools: dict[str, tuple[tuple[str, tuple[str, ...], str], ...]] = {}
        for raw_layer, values in dict(asset_pools or {}).items():
            layer = _LAYER_ALIASES.get(str(raw_layer), str(raw_layer))
            if layer not in bases:
                raise ValueError(f"unknown prompt layer: {raw_layer}")
            pools[layer] = tuple(self._asset(layer, item, index) for index, item in enumerate(values))
        locked = tuple(dict.fromkeys(_LAYER_ALIASES.get(str(item), str(item)) for item in locked_layers))
        if any(item not in bases for item in locked):
            raise ValueError("locked_layers contains an unknown layer")
        batch_material = _stable_json([seed_text, requested, bases, pools, locked])
        batch_id = "plb_" + hashlib.sha256(batch_material.encode()).hexdigest()[:24]
        candidates: list[PromptLabCandidate] = []
        for ordinal in range(1, requested + 1):
            rng = random.Random(seed_value + ordinal)
            layers = dict(bases)
            selected: list[tuple[str, str]] = []
            for layer, assets in pools.items():
                if layer in locked or not assets:
                    continue
                asset_id, terms, relation = assets[rng.randrange(len(assets))]
                layers[layer] = relation if layer == "relation" else terms
                selected.append((layer, asset_id))
            candidate_id = "plc_" + hashlib.sha256(f"{batch_id}:{ordinal}:{selected}".encode()).hexdigest()[:24]
            candidates.append(PromptLabCandidate(candidate_id, ordinal, layers, tuple(selected), locked))
        return PromptLabBatch(
            batch_id, seed_text, seed_value, requested, tuple(candidates), locked,
            tuple(pools), _clean(negative_prompt, limit=8192),
        )

    def confirm_candidate(self, batch: PromptLabBatch, selection: int | str) -> dict[str, Any]:
        candidate = batch.find_candidate(selection)
        layers = candidate.layers
        ordered = [str(term) for layer in PROMPT_LAB_LAYERS if layer != "relation" for term in layers.get(layer, ())]
        hard_tags = tuple(dict.fromkeys(term for term in ordered if term))
        anchors: list[tuple[str, str]] = []
        categories = {"identity": "character", "clothing": "clothing", "pose": "pose", "camera": "camera", "background": "environment", "style": "artist", "lora": "lora"}
        selected_layers = {layer for layer, _ in candidate.selected_assets}
        for layer, category in categories.items():
            if layer in selected_layers:
                continue
            anchors.extend((term, category) for term in layers.get(layer, ()))
        relation = str(layers.get("relation", "") or "")
        prompt = ", ".join((*hard_tags, relation) if relation else hard_tags)
        return {
            "batch_id": batch.batch_id,
            "candidate_id": candidate.candidate_id,
            "positive_prompt": prompt,
            "negative_prompt": batch.negative_prompt,
            "hard_tags": hard_tags,
            "visual_phrases": (),
            "scene_sentence": relation,
            "anchors": tuple(anchors),
            "source": "prompt_lab",
        }


@dataclass(frozen=True)
class LoraRecord:
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


class LoraCatalogService:
    """Native ComfyUI object-info and filesystem LoRA catalog."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self._cache: tuple[LoraRecord, ...] = ()
        self._session: aiohttp.ClientSession | None = None

    def _roots(self) -> tuple[Path, ...]:
        values = getattr(self.settings, "lora_visual_roots", ()) or ()
        return tuple(Path(item).expanduser().resolve(strict=False) for item in values)

    @staticmethod
    def _digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _local_records(self) -> list[LoraRecord]:
        result: list[LoraRecord] = []
        for root in self._roots():
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if not path.is_file() or path.suffix.casefold() not in {".safetensors", ".ckpt", ".pt"}:
                    continue
                relative = path.relative_to(root).as_posix()
                result.append(LoraRecord(name=relative, folder=str(PurePosixPath(relative).parent), file_path=str(path), source="filesystem"))
        return result

    async def _remote_records(self) -> list[LoraRecord]:
        url = str(getattr(self.settings, "lora_catalog_url", "") or "").strip()
        if not url:
            base = str(getattr(self.settings, "comfyui_url", "") or "").rstrip("/")
            url = f"{base}/object_info/LoraLoader" if base else ""
        if not url:
            return []
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            raise ValueError("LoRA catalog URL is invalid")
        timeout = float(getattr(self.settings, "lora_catalog_timeout", 30) or 30)
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout))
        headers = {"User-Agent": "Anima-Studio/7"}
        token = str(getattr(self.settings, "api_token", "") or "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with self._session.get(url, headers=headers, allow_redirects=False) as response:
            if response.status >= 300:
                raise RuntimeError(f"ComfyUI LoRA catalog returned HTTP {response.status}")
            payload = await response.json(content_type=None)
        names: list[str] = []
        if isinstance(payload, list):
            names = [str(item) for item in payload]
        elif isinstance(payload, Mapping):
            node = payload.get("LoraLoader", payload)
            if isinstance(node, Mapping):
                required = node.get("input", {}).get("required", {}) if isinstance(node.get("input"), Mapping) else {}
                value = required.get("lora_name", ()) if isinstance(required, Mapping) else ()
                if isinstance(value, Sequence) and value and isinstance(value[0], Sequence):
                    names = [str(item) for item in value[0]]
                elif isinstance(payload.get("loras"), list):
                    names = [str(item) for item in payload["loras"]]
        return [LoraRecord(name=name.replace("\\", "/"), source="comfyui") for name in names if name]

    async def list_loras(
        self,
        query: str = "",
        limit: int | None = None,
        force: bool = False,
        force_refresh: bool = False,
    ) -> tuple[LoraRecord, ...]:
        if not self._cache or force or force_refresh:
            records = self._local_records()
            try:
                records.extend(await self._remote_records())
            except (TimeoutError, aiohttp.ClientError):
                if not records:
                    raise
            by_name: dict[str, LoraRecord] = {}
            for record in records:
                by_name.setdefault(record.name.casefold(), record)
            self._cache = tuple(sorted(by_name.values(), key=lambda item: item.name.casefold()))
        result = self._cache
        if query.strip():
            needle = query.strip().casefold()
            result = tuple(item for item in result if needle in " ".join((item.name, *item.aliases, *item.tags)).casefold())
        effective = max(1, int(limit or getattr(self.settings, "lora_max_results", 1000) or 1000))
        return result[:effective]

    async def get_detail_v2(self, record: LoraRecord) -> dict[str, Any]:
        path = Path(record.file_path) if record.file_path else None
        digest = record.sha256
        if path and path.is_file() and not digest:
            digest = await asyncio.to_thread(self._digest, path)
        fingerprint = hashlib.sha256(_stable_json([record.name, digest, record.trigger_words]).encode()).hexdigest()
        return {**asdict(record), "filename": record.name, "sha256": digest, "source_fingerprint": record.source_fingerprint or fingerprint, "schema_version": 3}

    async def refresh_summary(self) -> str:
        records = await self.list_loras(force=True)
        return f"{len(records)} LoRA files"

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()


class LoraVisualService:
    def __init__(
        self,
        roots: Sequence[str | os.PathLike[str]],
        cache_dir: str | os.PathLike[str],
        **options: Any,
    ) -> None:
        del options
        self.roots = tuple(Path(item).resolve(strict=False) for item in roots)
        self.cache_dir = Path(cache_dir).resolve(strict=False)
        self._closed = False

    def _preview(self, record: LoraRecord) -> str:
        if record.preview_url:
            return record.preview_url
        if record.file_path:
            base = Path(record.file_path)
            for suffix in (".preview.png", ".png", ".jpg", ".jpeg", ".webp"):
                candidate = base.with_suffix(suffix)
                if candidate.is_file():
                    return str(candidate)
        return ""

    def build_manifest(self, records: Sequence[LoraRecord]) -> dict[str, Any]:
        items = [{"filename": item.name, "preview": self._preview(item), "category": item.category} for item in records]
        return {"count": len(items), "items": items, "generated_at": _utc_now()}

    def list_page(
        self,
        records: Sequence[LoraRecord],
        *,
        query: str = "",
        page: int = 1,
        page_size: int = 50,
        category: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        items = list(records)
        if query.strip():
            needle = query.strip().casefold()
            items = [item for item in items if needle in item.name.casefold()]
        if category.strip():
            items = [item for item in items if item.category.casefold() == category.strip().casefold()]
        size = max(1, min(int(page_size), 200))
        current = max(1, int(page))
        start = (current - 1) * size
        return {"total": len(items), "page": current, "page_size": size, "items": self.build_manifest(items[start : start + size])["items"]}

    def warmup_status(self) -> dict[str, Any]:
        return {"running": False, "closed": self._closed, "cache_dir": str(self.cache_dir)}

    def close(self, *, wait: bool = True) -> None:
        del wait
        self._closed = True


class LoraAnalysisPipeline:
    def __init__(self, semantic_index: Any, semantic_path: str | os.PathLike[str], task_store: Any) -> None:
        self.semantic_index = semantic_index
        self.semantic_path = Path(semantic_path)
        self.task_store = task_store

    async def run(
        self,
        details: Sequence[Any],
        llm_callback: Callable[[str, str], Awaitable[Any]],
        **options: Any,
    ) -> dict[str, Any]:
        analyzed: list[dict[str, Any]] = []
        for item in details[: max(1, min(int(options.get("limit", len(details) or 1)), 1000))]:
            payload = asdict(item) if hasattr(item, "__dataclass_fields__") else dict(item) if isinstance(item, Mapping) else {"value": str(item)}
            response = await llm_callback(
                "Classify this LoRA metadata. Return a JSON object with category, aliases and trigger_words.",
                _stable_json(payload),
            )
            try:
                parsed = json.loads(response) if isinstance(response, str) else dict(response)
            except (TypeError, ValueError):
                parsed = {"raw": str(response)[:2000]}
            analyzed.append({"source": payload, "analysis": parsed})
        self.semantic_path.parent.mkdir(parents=True, exist_ok=True)
        self.semantic_path.write_text(_stable_json({"schema_version": 3, "items": analyzed}) + "\n", encoding="utf-8")
        return {"run_id": str(options.get("run_id") or uuid.uuid4().hex), "selected_count": len(details), "analyzed_count": len(analyzed), "items": analyzed}


class LoraArchiveService:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path).resolve(strict=False)

    def _read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"schema_version": 1, "items": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def catalog_status(self, records: Sequence[LoraRecord]) -> dict[str, Any]:
        archived = self._read().get("items", [])
        current = {item.name.casefold() for item in records}
        return {"current_count": len(current), "archived_count": len(archived), "missing_count": sum(str(item.get("name", "")).casefold() not in current for item in archived if isinstance(item, Mapping))}

    async def archive_with_llm(
        self,
        records: Sequence[LoraRecord],
        llm_callback: Callable[[str, str], Awaitable[Any]],
        **options: Any,
    ) -> dict[str, Any]:
        summary = await llm_callback("Summarize this native LoRA catalog as JSON.", _stable_json([asdict(item) for item in records]))
        payload = {"schema_version": 1, "updated_at": _utc_now(), "items": [asdict(item) for item in records], "summary": summary}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(_stable_json(payload) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)
        return {"archived": len(records), "path": str(self.path), "provider": options.get("provider", "")}


class LoraDownloadService:
    """Explicit native downloader using configured local roots as destination."""

    def __init__(self, settings: Any, catalog: LoraCatalogService) -> None:
        self.settings = settings
        self.catalog = catalog
        self._session: aiohttp.ClientSession | None = None

    async def download_from_url(self, url: str) -> dict[str, Any]:
        parsed = urlparse(str(url or ""))
        allowed = tuple(str(item).casefold() for item in getattr(self.settings, "lora_download_allowed_hosts", ("civitai.com", "www.civitai.com")))
        if parsed.scheme != "https" or not parsed.hostname or parsed.hostname.casefold() not in allowed or parsed.username:
            raise ValueError("LoRA download URL host is not allowed")
        roots = self.catalog._roots()
        if not roots:
            raise RuntimeError("no local LoRA destination is configured")
        filename = Path(parsed.path).name
        if Path(filename).suffix.casefold() not in {".safetensors", ".ckpt", ".pt"}:
            raise ValueError("LoRA download URL must name a supported model file")
        target = roots[0] / filename
        if target.exists():
            raise FileExistsError(filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        timeout = float(getattr(self.settings, "lora_download_timeout", 3600) or 3600)
        self._session = self._session or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout))
        temporary = target.with_suffix(target.suffix + ".part")
        digest = hashlib.sha256()
        size = 0
        try:
            async with self._session.get(url, allow_redirects=False, headers={"User-Agent": "Anima-Studio/7"}) as response:
                if response.status >= 300:
                    raise RuntimeError(f"LoRA download returned HTTP {response.status}")
                with temporary.open("xb") as handle:
                    async for block in response.content.iter_chunked(1024 * 1024):
                        size += len(block)
                        if size > 8 * 1024 * 1024 * 1024:
                            raise RuntimeError("LoRA download exceeds 8GB")
                        digest.update(block)
                        handle.write(block)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        await self.catalog.list_loras(force=True)
        return {"url": url, "downloaded": True, "filename": filename, "size": size, "sha256": digest.hexdigest()}

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()


@dataclass(frozen=True)
class DanbooruBuildOptions:
    base_url: str = "https://danbooru.donmai.us"
    proxy_url: str = ""
    mode: str = "identity"
    general_min_posts: int = 10
    meta_min_posts: int = 10
    page_size: int = 1000
    request_interval_ms: int = 750
    timeout_seconds: int = 60
    max_records: int = 2_000_000
    include_aliases: bool = True
    max_retries: int = 5

    def normalized(self) -> DanbooruBuildOptions:
        mode = str(self.mode).strip().casefold()
        if mode not in {"identity", "full"}:
            raise ValueError("Danbooru mode must be identity or full")
        base_url = str(self.base_url or "").strip().rstrip("/")
        if not base_url:
            raise ValueError("Danbooru base URL is required")
        return replace(
            self,
            base_url=base_url,
            proxy_url=str(self.proxy_url or "").strip(),
            mode=mode,
            general_min_posts=max(0, min(int(self.general_min_posts), 1_000_000)),
            meta_min_posts=max(0, min(int(self.meta_min_posts), 1_000_000)),
            page_size=max(1, min(int(self.page_size), 1000)),
            max_records=max(1, min(int(self.max_records), 3_000_000)),
            request_interval_ms=max(250, min(int(self.request_interval_ms), 10_000)),
            timeout_seconds=max(10, min(int(self.timeout_seconds), 300)),
            max_retries=max(1, min(int(self.max_retries), 8)),
        )

    def signature_payload(self) -> dict[str, Any]:
        payload = asdict(self.normalized())
        for key in ("proxy_url", "timeout_seconds", "request_interval_ms", "max_retries"):
            payload.pop(key, None)
        return payload


def _normalize_danbooru_tag(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return re.sub(r"[\s-]+", "_", text)


class DanbooruTagIndex:
    """Small V2-compatible Danbooru index used by isolated Studio instances."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path).resolve(strict=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def replace(self, tags: Sequence[Mapping[str, Any]], aliases: Sequence[Mapping[str, Any]] = ()) -> None:
        staging = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.staging.sqlite3")
        try:
            _write_danbooru_snapshot(
                staging,
                tags,
                aliases,
                metadata={"generator": "anima_studio", "imported_at": _utc_now()},
            )
            with staging.open("r+b") as handle:
                os.fsync(handle.fileno())
            os.replace(staging, self.path)
        finally:
            staging.unlink(missing_ok=True)

    def status(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {
                "available": False,
                "ready": False,
                "tag_count": 0,
                "alias_count": 0,
                "path": str(self.path),
            }
        try:
            with closing(sqlite3.connect(self.path)) as connection:
                tags = int(connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0])
                aliases = int(connection.execute("SELECT COUNT(*) FROM aliases").fetchone()[0])
                has_metadata = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='metadata'"
                ).fetchone()
                metadata = (
                    {str(key): str(value) for key, value in connection.execute("SELECT key, value FROM metadata")}
                    if has_metadata
                    else {}
                )
        except (OSError, sqlite3.Error) as error:
            return {
                "available": False,
                "ready": False,
                "tag_count": 0,
                "alias_count": 0,
                "path": str(self.path),
                "error": str(error),
            }
        return {
            "available": tags > 0,
            "ready": tags > 0,
            "tag_count": tags,
            "alias_count": aliases,
            "path": str(self.path),
            **metadata,
        }


def _write_danbooru_snapshot(
    path: Path,
    tags: Sequence[Mapping[str, Any]],
    aliases: Sequence[Mapping[str, Any]],
    *,
    metadata: Mapping[str, Any],
) -> dict[str, int]:
    """Create and validate one immutable V2 snapshot at ``path``."""

    path.unlink(missing_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode=OFF;
            PRAGMA synchronous=OFF;
            PRAGMA foreign_keys=ON;
            CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE tags(
                id INTEGER PRIMARY KEY,
                tag TEXT NOT NULL,
                normalized_tag TEXT NOT NULL UNIQUE,
                tag_length INTEGER NOT NULL,
                category TEXT NOT NULL DEFAULT '',
                count INTEGER NOT NULL DEFAULT 0 CHECK(count >= 0),
                provenance TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE aliases(
                tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                alias TEXT NOT NULL,
                normalized_alias TEXT NOT NULL,
                alias_length INTEGER NOT NULL,
                PRIMARY KEY(normalized_alias, tag_id)
            ) WITHOUT ROWID;
            CREATE TABLE alias_keys(
                normalized_alias TEXT PRIMARY KEY,
                alias_length INTEGER NOT NULL,
                owner_count INTEGER NOT NULL CHECK(owner_count > 0),
                canonical_conflict INTEGER NOT NULL CHECK(canonical_conflict IN (0, 1))
            ) WITHOUT ROWID;
            """
        )
        names: dict[str, int] = {}
        provenance = _stable_json(
            {"source": str(metadata.get("source") or ""), "owner": "anima_studio"}
        )
        for index, item in enumerate(tags, start=1):
            name = _normalize_danbooru_tag(item.get("name", item.get("tag", "")))
            if not name or len(name) > 256:
                raise ValueError("Danbooru tag name is invalid")
            category = str(item.get("category") or "general").strip().casefold()
            count = max(0, int(item.get("post_count", item.get("count", 0)) or 0))
            source_id = int(item.get("source_id", item.get("id", index)) or index)
            connection.execute(
                "INSERT INTO tags(id, tag, normalized_tag, tag_length, category, count, provenance) VALUES(?, ?, ?, ?, ?, ?, ?)",
                (source_id, name, name, len(name), category, count, provenance),
            )
            names[name] = source_id
        for item in aliases:
            alias = _normalize_danbooru_tag(item.get("alias", item.get("antecedent_name", "")))
            target = _normalize_danbooru_tag(item.get("target", item.get("consequent_name", "")))
            tag_id = names.get(target)
            if not alias or not tag_id or alias == target or len(alias) > 256:
                continue
            connection.execute(
                "INSERT OR IGNORE INTO aliases(tag_id, alias, normalized_alias, alias_length) VALUES(?, ?, ?, ?)",
                (tag_id, alias, alias, len(alias)),
            )
        connection.execute(
            """
            INSERT INTO alias_keys(normalized_alias, alias_length, owner_count, canonical_conflict)
            SELECT a.normalized_alias, MIN(a.alias_length), COUNT(*),
                   CASE WHEN MAX(t.id IS NOT NULL) THEN 1 ELSE 0 END
            FROM aliases a
            LEFT JOIN tags t ON t.normalized_tag = a.normalized_alias
            GROUP BY a.normalized_alias
            """
        )
        connection.executescript(
            """
            CREATE INDEX idx_alias_length ON aliases(alias_length);
            CREATE INDEX idx_alias_tag_id ON aliases(tag_id);
            CREATE INDEX idx_tags_category ON tags(category);
            CREATE INDEX idx_tags_length_count ON tags(tag_length, count DESC);
            """
        )
        tag_count = int(connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0])
        alias_count = int(connection.execute("SELECT COUNT(*) FROM aliases").fetchone()[0])
        if tag_count == 0:
            raise ValueError("Danbooru snapshot contains no tags")
        snapshot_metadata = {
            "schema_version": "2",
            "tag_count": str(tag_count),
            "alias_count": str(alias_count),
            **{str(key): str(value) for key, value in metadata.items()},
        }
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES(?, ?)",
            sorted(snapshot_metadata.items()),
        )
        connection.commit()
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if integrity.casefold() != "ok" or foreign_keys:
            raise sqlite3.DatabaseError("Danbooru snapshot integrity validation failed")
        return {"tag_count": tag_count, "alias_count": alias_count}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


class DanbooruApiBuilder:
    """Resumable, high-water official API builder for the native V2 index."""

    _CATEGORIES: ClassVar[dict[int, str]] = {
        0: "general",
        1: "artist",
        3: "copyright",
        4: "character",
        5: "meta",
    }

    _CHECKPOINT_SCHEMA: ClassVar[str] = "2"
    _MAX_PAGE_BYTES: ClassVar[int] = 8 * 1024 * 1024

    def __init__(
        self,
        index: DanbooruTagIndex,
        checkpoint_path: str | os.PathLike[str],
        *,
        user_agent: str = "Anima-Studio/7",
        allow_insecure_localhost: bool = False,
        pace_requests: bool = True,
    ) -> None:
        self.index = index
        self.checkpoint_path = Path(checkpoint_path).resolve(strict=False)
        self.user_agent = str(user_agent or "Anima-Studio/7").strip()
        self.allow_insecure_localhost = bool(allow_insecure_localhost)
        self.pace_requests = bool(pace_requests)

    def checkpoint_status(self) -> dict[str, Any]:
        status = self.index.status()
        if not self.checkpoint_path.is_file():
            return {**status, "checkpoint_available": False, "available": False}
        try:
            metadata = self._read_metadata()
            counts = self._checkpoint_counts()
        except (OSError, sqlite3.Error):
            return {
                **status,
                "checkpoint_available": False,
                "available": False,
                "checkpoint_error": "checkpoint_unreadable",
            }
        high_water = {
            key.removeprefix("tags_high_water_"): int(value or 0)
            for key, value in metadata.items()
            if key.startswith("tags_high_water_")
        }
        return {
            **status,
            "checkpoint_available": True,
            "available": True,
            "checkpoint_completed": metadata.get("completed") == "1",
            "resumable": metadata.get("completed") != "1",
            "incremental_ready": metadata.get("completed") == "1",
            "phase": metadata.get("build_phase", ""),
            "started_at": metadata.get("started_at", ""),
            "completed_at": metadata.get("completed_at", ""),
            "source_cutoff_at": metadata.get("source_cutoff_at", ""),
            "source_updated_at": metadata.get("source_updated_at", ""),
            "source_max_tag_id": max(high_water.values(), default=0),
            "source_max_alias_id": int(metadata.get("aliases_high_water") or 0),
            "content_sha256": metadata.get("content_sha256", status.get("sha256", "")),
            "last_error": metadata.get("last_error", ""),
            **counts,
        }

    async def build(
        self,
        options: DanbooruBuildOptions,
        *,
        progress: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        effective = options.normalized()
        self._validate_endpoint(effective.base_url, label="Danbooru API")
        if effective.proxy_url:
            self._validate_endpoint(effective.proxy_url, label="Danbooru proxy")
        cancellation = cancel_event or threading.Event()
        signature = hashlib.sha256(
            _stable_json(effective.signature_payload()).encode("utf-8")
        ).hexdigest()
        resumed, incremental = self._prepare_checkpoint(signature, effective)
        await self._emit(
            progress,
            {
                "event": "build_resumed" if resumed else "build_started",
                "phase": "preflight",
                "message": (
                    "Resuming the persisted Danbooru checkpoint"
                    if resumed
                    else "Starting an incremental Danbooru update"
                    if incremental
                    else "Starting a new Danbooru snapshot"
                ),
                "resumed": resumed,
                "incremental": incremental,
                **self._checkpoint_counts(),
            },
        )
        timeout = aiohttp.ClientTimeout(total=effective.timeout_seconds)
        try:
            async with aiohttp.ClientSession(
                timeout=timeout,
                headers={"User-Agent": self.user_agent, "Accept": "application/json"},
            ) as session:
                await self._fetch_tags(session, effective, cancellation, progress)
                if effective.include_aliases:
                    await self._fetch_aliases(session, effective, cancellation, progress)
                else:
                    self._set_metadata({"aliases_done": "1"})
            self._raise_if_cancelled(cancellation)
            summary = self._logical_summary(effective)
            current = self.index.status()
            unchanged = bool(
                summary["content_sha256"]
                and summary["content_sha256"]
                == str(current.get("sha256") or current.get("content_sha256") or "")
                and int(current.get("tag_count") or 0) == summary["tag_count"]
            )
            if not unchanged:
                await asyncio.to_thread(
                    self._activate_snapshot,
                    effective,
                    summary,
                    cancellation,
                )
            else:
                self._raise_if_cancelled(cancellation)
            completed_at = _utc_now()
            self._set_metadata(
                {
                    "completed": "1",
                    "completed_at": completed_at,
                    "build_phase": "complete",
                    "content_sha256": summary["content_sha256"],
                    "last_error": "",
                }
            )
            result = {
                "mode": effective.mode,
                "tag_count": summary["tag_count"],
                "alias_count": summary["alias_count"],
                "category_counts": summary["category_counts"],
                "content_sha256": summary["content_sha256"],
                "revision": summary["content_sha256"][:12],
                "source_max_tag_id": summary["source_max_tag_id"],
                "source_max_alias_id": summary["source_max_alias_id"],
                "source_updated_at": summary["source_updated_at"],
                "source_cutoff_at": summary["source_cutoff_at"],
                "resumed": resumed,
                "incremental": incremental,
                "activated": not unchanged,
                "unchanged": unchanged,
                "completed": True,
                "completed_at": completed_at,
            }
            await self._emit(
                progress,
                {
                    "event": "snapshot_activated" if not unchanged else "snapshot_unchanged",
                    "phase": "complete",
                    "message": (
                        "The new Danbooru snapshot was atomically activated"
                        if not unchanged
                        else "Danbooru content is unchanged"
                    ),
                    **result,
                },
            )
            return result
        except asyncio.CancelledError:
            cancellation.set()
            self._record_failure("cancelled")
            raise
        except Exception as error:
            self._record_failure(str(error))
            raise

    def _validate_endpoint(self, value: str, *, label: str) -> None:
        parsed = urlparse(value)
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValueError(f"{label} must be an uncredentialed URL")
        if parsed.scheme == "https":
            return
        local_hosts = {"localhost", "127.0.0.1", "::1"}
        if (
            parsed.scheme == "http"
            and self.allow_insecure_localhost
            and parsed.hostname.casefold() in local_hosts
        ):
            return
        raise ValueError(f"{label} must use HTTPS")

    @staticmethod
    def _category_plan(options: DanbooruBuildOptions) -> tuple[tuple[int, int], ...]:
        if options.mode == "identity":
            return ((1, 0), (3, 0), (4, 0))
        return (
            (0, options.general_min_posts),
            (1, 0),
            (3, 0),
            (4, 0),
            (5, options.meta_min_posts),
        )

    def _prepare_checkpoint(
        self, signature: str, options: DanbooruBuildOptions
    ) -> tuple[bool, bool]:
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        if self.checkpoint_path.is_file():
            try:
                metadata = self._read_metadata()
            except (OSError, sqlite3.Error):
                metadata = {}
            compatible = bool(
                metadata.get("schema_version") == self._CHECKPOINT_SCHEMA
                and metadata.get("signature") == signature
            )
            if compatible and metadata.get("completed") != "1":
                self._set_metadata({"last_error": "", "build_phase": "resume"})
                return True, False
            if compatible:
                updates: dict[str, Any] = {
                    "completed": "0",
                    "started_at": _utc_now(),
                    "build_phase": "incremental",
                    "last_error": "",
                }
                for category, _ in self._category_plan(options):
                    previous = int(metadata.get(f"tags_high_water_{category}") or 0)
                    updates.update(
                        {
                            f"tags_cursor_{category}": previous,
                            f"tags_high_water_{category}": "0",
                            f"tags_done_{category}": "0",
                            f"tags_pages_{category}": "0",
                        }
                    )
                previous_alias = int(metadata.get("aliases_high_water") or 0)
                updates.update(
                    {
                        "aliases_cursor": previous_alias,
                        "aliases_high_water": "0",
                        "aliases_done": "0" if options.include_aliases else "1",
                        "aliases_pages": "0",
                    }
                )
                self._set_metadata(updates)
                return False, True
            self._remove_checkpoint()
        connection = sqlite3.connect(self.checkpoint_path, timeout=30)
        try:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE tags(
                    source_id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    category INTEGER NOT NULL,
                    post_count INTEGER NOT NULL CHECK(post_count >= 0),
                    updated_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE aliases(
                    source_id INTEGER PRIMARY KEY,
                    alias TEXT NOT NULL,
                    target TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX idx_checkpoint_alias_target ON aliases(target);
                """
            )
            connection.executemany(
                "INSERT INTO metadata(key, value) VALUES(?, ?)",
                [
                    ("schema_version", self._CHECKPOINT_SCHEMA),
                    ("signature", signature),
                    ("options", _stable_json(options.signature_payload())),
                    ("completed", "0"),
                    ("started_at", _utc_now()),
                    ("build_phase", "preflight"),
                    ("last_error", ""),
                ],
            )
            connection.commit()
        finally:
            connection.close()
        return False, False

    async def _fetch_tags(
        self,
        session: aiohttp.ClientSession,
        options: DanbooruBuildOptions,
        cancel_event: threading.Event,
        progress: Callable[[dict[str, Any]], Awaitable[None] | None] | None,
    ) -> None:
        for category, minimum_posts in self._category_plan(options):
            if self._metadata_value(f"tags_done_{category}") == "1":
                continue
            high_water = int(self._metadata_value(f"tags_high_water_{category}") or 0)
            if high_water <= 0:
                high_water = await self._fetch_high_water(
                    session,
                    f"{options.base_url}/tags.json",
                    {
                        "search[category]": category,
                        "search[is_deprecated]": "false",
                        **(
                            {"search[post_count_gteq]": minimum_posts}
                            if minimum_posts > 0
                            else {}
                        ),
                    },
                    options,
                    cancel_event,
                )
                self._set_metadata(
                    {
                        f"tags_high_water_{category}": high_water,
                        "source_cutoff_at": _utc_now(),
                    }
                )
            cursor = int(self._metadata_value(f"tags_cursor_{category}") or 0)
            pages = int(self._metadata_value(f"tags_pages_{category}") or 0)
            while cursor < high_water:
                self._raise_if_cancelled(cancel_event)
                params: dict[str, Any] = {
                    "limit": options.page_size,
                    "page": f"a{cursor}",
                    "search[category]": category,
                    "search[is_deprecated]": "false",
                    "search[id_lteq]": high_water,
                }
                if minimum_posts > 0:
                    params["search[post_count_gteq]"] = minimum_posts
                rows = await self._request_json(
                    session,
                    f"{options.base_url}/tags.json",
                    params,
                    options,
                    cancel_event,
                )
                if not rows:
                    break
                cursor, accepted, updated_at = self._commit_tag_page(
                    rows, category, minimum_posts, cursor
                )
                pages += 1
                self._set_metadata(
                    {
                        f"tags_cursor_{category}": cursor,
                        f"tags_pages_{category}": pages,
                        "build_phase": f"tags:{category}",
                        "source_updated_at": max(
                            updated_at, self._metadata_value("source_updated_at")
                        ),
                    }
                )
                counts = self._checkpoint_counts()
                if counts["tag_count"] > options.max_records:
                    raise ValueError("Danbooru tag count exceeds max_records")
                await self._emit(
                    progress,
                    {
                        "event": "tag_page_committed",
                        "phase": "download_tags",
                        "message": f"Committed {self._CATEGORIES[category]} page {pages}",
                        "category": self._CATEGORIES[category],
                        "cursor": cursor,
                        "accepted": accepted,
                        **counts,
                    },
                )
                await self._paced_sleep(options, cancel_event)
            self._set_metadata(
                {f"tags_done_{category}": "1", "build_phase": f"tags:{category}:done"}
            )

    async def _fetch_aliases(
        self,
        session: aiohttp.ClientSession,
        options: DanbooruBuildOptions,
        cancel_event: threading.Event,
        progress: Callable[[dict[str, Any]], Awaitable[None] | None] | None,
    ) -> None:
        if self._metadata_value("aliases_done") == "1":
            return
        high_water = int(self._metadata_value("aliases_high_water") or 0)
        if high_water <= 0:
            high_water = await self._fetch_high_water(
                session,
                f"{options.base_url}/tag_aliases.json",
                {"search[status]": "active"},
                options,
                cancel_event,
            )
            self._set_metadata(
                {"aliases_high_water": high_water, "source_cutoff_at": _utc_now()}
            )
        cursor = int(self._metadata_value("aliases_cursor") or 0)
        pages = int(self._metadata_value("aliases_pages") or 0)
        while cursor < high_water:
            self._raise_if_cancelled(cancel_event)
            rows = await self._request_json(
                session,
                f"{options.base_url}/tag_aliases.json",
                {
                    "limit": options.page_size,
                    "page": f"a{cursor}",
                    "search[status]": "active",
                    "search[id_lteq]": high_water,
                },
                options,
                cancel_event,
            )
            if not rows:
                break
            cursor, accepted, updated_at = self._commit_alias_page(rows, cursor)
            pages += 1
            self._set_metadata(
                {
                    "aliases_cursor": cursor,
                    "aliases_pages": pages,
                    "build_phase": "aliases",
                    "source_updated_at": max(
                        updated_at, self._metadata_value("source_updated_at")
                    ),
                }
            )
            counts = self._checkpoint_counts()
            if counts["alias_count"] > options.max_records:
                raise ValueError("Danbooru alias count exceeds max_records")
            await self._emit(
                progress,
                {
                    "event": "alias_page_committed",
                    "phase": "download_aliases",
                    "message": f"Committed alias page {pages}",
                    "cursor": cursor,
                    "accepted": accepted,
                    **counts,
                },
            )
            await self._paced_sleep(options, cancel_event)
        self._set_metadata({"aliases_done": "1", "build_phase": "aliases:done"})

    async def _fetch_high_water(
        self,
        session: aiohttp.ClientSession,
        url: str,
        params: Mapping[str, Any],
        options: DanbooruBuildOptions,
        cancel_event: threading.Event,
    ) -> int:
        rows = await self._request_json(
            session,
            url,
            {**dict(params), "limit": 1, "page": "b2147483647"},
            options,
            cancel_event,
        )
        if not rows:
            return 0
        value = int(rows[0].get("id") or 0)
        if value <= 0:
            raise ValueError("Danbooru API returned an invalid high-water ID")
        return value

    async def _request_json(
        self,
        session: aiohttp.ClientSession,
        url: str,
        params: Mapping[str, Any],
        options: DanbooruBuildOptions,
        cancel_event: threading.Event,
    ) -> list[dict[str, Any]]:
        last_error = ""
        for attempt in range(1, options.max_retries + 1):
            self._raise_if_cancelled(cancel_event)
            try:
                async with session.get(
                    url,
                    params=params,
                    proxy=options.proxy_url or None,
                    allow_redirects=False,
                ) as response:
                    if 300 <= response.status < 400:
                        raise RuntimeError("Danbooru API redirects are rejected")
                    if response.status in {400, 401, 403, 404, 422}:
                        raise RuntimeError(f"Danbooru API rejected the request: HTTP {response.status}")
                    if response.status == 429 or response.status >= 500:
                        last_error = f"HTTP {response.status}"
                        if attempt < options.max_retries:
                            await self._retry_sleep(attempt, cancel_event)
                            continue
                    if response.status >= 300:
                        raise RuntimeError(f"Danbooru API returned HTTP {response.status}")
                    declared = response.headers.get("Content-Length", "")
                    if declared and int(declared) > self._MAX_PAGE_BYTES:
                        raise RuntimeError("Danbooru API page exceeds the 8MB limit")
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        total += len(chunk)
                        if total > self._MAX_PAGE_BYTES:
                            raise RuntimeError("Danbooru API page exceeds the 8MB limit")
                        chunks.append(chunk)
                decoded = json.loads(b"".join(chunks).decode("utf-8"))
                if not isinstance(decoded, list) or any(
                    not isinstance(item, Mapping) for item in decoded
                ):
                    raise RuntimeError("Danbooru API returned an invalid JSON list")
                return [dict(item) for item in decoded]
            except (aiohttp.ClientError, TimeoutError, UnicodeError, json.JSONDecodeError) as error:
                last_error = type(error).__name__
                if attempt < options.max_retries:
                    await self._retry_sleep(attempt, cancel_event)
                    continue
                break
        raise RuntimeError(f"Danbooru API retries exhausted: {last_error or 'unknown error'}")

    def _commit_tag_page(
        self,
        rows: Sequence[Mapping[str, Any]],
        category: int,
        minimum_posts: int,
        cursor: int,
    ) -> tuple[int, int, str]:
        maximum = cursor
        latest = ""
        records: list[tuple[int, str, int, int, str]] = []
        for item in rows:
            source_id = int(item.get("id") or 0)
            maximum = max(maximum, source_id)
            name = _normalize_danbooru_tag(item.get("name"))
            post_count = int(item.get("post_count") or 0)
            updated_at = str(item.get("updated_at") or "")
            latest = max(latest, updated_at)
            if (
                source_id <= 0
                or not name
                or len(name) > 256
                or post_count < minimum_posts
                or bool(item.get("is_deprecated"))
            ):
                continue
            records.append((source_id, name, category, max(0, post_count), updated_at))
        if maximum <= cursor:
            raise RuntimeError("Danbooru tag cursor did not advance")
        with closing(sqlite3.connect(self.checkpoint_path, timeout=30)) as connection, connection:
            connection.executemany(
                """
                INSERT INTO tags(source_id, name, category, post_count, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    name=excluded.name, category=excluded.category,
                    post_count=excluded.post_count, updated_at=excluded.updated_at
                """,
                records,
            )
        return maximum, len(records), latest

    def _commit_alias_page(
        self, rows: Sequence[Mapping[str, Any]], cursor: int
    ) -> tuple[int, int, str]:
        maximum = cursor
        latest = ""
        records: list[tuple[int, str, str, str]] = []
        for item in rows:
            source_id = int(item.get("id") or 0)
            maximum = max(maximum, source_id)
            alias = _normalize_danbooru_tag(item.get("antecedent_name"))
            target = _normalize_danbooru_tag(item.get("consequent_name"))
            updated_at = str(item.get("updated_at") or "")
            latest = max(latest, updated_at)
            if source_id > 0 and alias and target and alias != target:
                records.append((source_id, alias, target, updated_at))
        if maximum <= cursor:
            raise RuntimeError("Danbooru alias cursor did not advance")
        with closing(sqlite3.connect(self.checkpoint_path, timeout=30)) as connection, connection:
            connection.executemany(
                """
                INSERT INTO aliases(source_id, alias, target, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    alias=excluded.alias, target=excluded.target, updated_at=excluded.updated_at
                """,
                records,
            )
        return maximum, len(records), latest

    def _logical_summary(self, options: DanbooruBuildOptions) -> dict[str, Any]:
        digest = hashlib.sha256()
        category_counts: dict[str, int] = {}
        with closing(sqlite3.connect(self.checkpoint_path, timeout=30)) as connection:
            for source_id, name, category, post_count in connection.execute(
                "SELECT source_id, name, category, post_count FROM tags ORDER BY source_id"
            ):
                category_name = self._CATEGORIES[int(category)]
                category_counts[category_name] = category_counts.get(category_name, 0) + 1
                digest.update(
                    (_stable_json(["tag", int(source_id), str(name), category_name, int(post_count)]) + "\n").encode("utf-8")
                )
            alias_count = 0
            for source_id, alias, target in connection.execute(
                """
                SELECT a.source_id, a.alias, a.target
                FROM aliases a JOIN tags t ON t.name = a.target
                ORDER BY a.source_id
                """
            ):
                alias_count += 1
                digest.update(
                    (_stable_json(["alias", int(source_id), str(alias), str(target)]) + "\n").encode("utf-8")
                )
            tag_count = int(connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0])
            metadata = self._read_metadata(connection=connection)
        if tag_count == 0:
            raise RuntimeError("Danbooru checkpoint contains no tags")
        high_waters = [
            int(value or 0)
            for key, value in metadata.items()
            if key.startswith("tags_high_water_")
        ]
        return {
            "tag_count": tag_count,
            "alias_count": alias_count,
            "category_counts": category_counts,
            "content_sha256": digest.hexdigest(),
            "source_max_tag_id": max(high_waters, default=0),
            "source_max_alias_id": int(metadata.get("aliases_high_water") or 0),
            "source_updated_at": metadata.get("source_updated_at", ""),
            "source_cutoff_at": metadata.get("source_cutoff_at", ""),
            "build_mode": options.mode,
        }

    def _activate_snapshot(
        self,
        options: DanbooruBuildOptions,
        summary: Mapping[str, Any],
        cancel_event: threading.Event,
    ) -> None:
        self._raise_if_cancelled(cancel_event)
        raw_index_path = getattr(self.index, "path", None)
        if raw_index_path is None:
            raise TypeError("Danbooru index does not expose a local path")
        index_path = Path(raw_index_path).resolve(strict=False)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = index_path.with_name(f".{index_path.name}.{uuid.uuid4().hex}.tmp")
        connection = sqlite3.connect(temporary, timeout=30)
        try:
            connection.executescript(
                """
                PRAGMA journal_mode=OFF;
                PRAGMA synchronous=OFF;
                PRAGMA foreign_keys=ON;
                CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE tags(
                    id INTEGER PRIMARY KEY, tag TEXT NOT NULL,
                    normalized_tag TEXT NOT NULL UNIQUE, tag_length INTEGER NOT NULL,
                    category TEXT NOT NULL DEFAULT '', count INTEGER NOT NULL DEFAULT 0 CHECK(count >= 0),
                    provenance TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE aliases(
                    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
                    alias TEXT NOT NULL, normalized_alias TEXT NOT NULL,
                    alias_length INTEGER NOT NULL,
                    PRIMARY KEY(normalized_alias, tag_id)
                ) WITHOUT ROWID;
                CREATE TABLE alias_keys(
                    normalized_alias TEXT PRIMARY KEY, alias_length INTEGER NOT NULL,
                    owner_count INTEGER NOT NULL CHECK(owner_count > 0),
                    canonical_conflict INTEGER NOT NULL CHECK(canonical_conflict IN (0, 1))
                ) WITHOUT ROWID;
                """
            )
            connection.execute("ATTACH DATABASE ? AS stage", (str(self.checkpoint_path),))
            provenance = _stable_json(
                {"source": options.base_url, "transport": urlparse(options.base_url).scheme, "owner": "anima_studio"}
            )
            connection.execute(
                """
                INSERT INTO tags(id, tag, normalized_tag, tag_length, category, count, provenance)
                SELECT source_id, name, name, length(name),
                       CASE category WHEN 0 THEN 'general' WHEN 1 THEN 'artist'
                           WHEN 3 THEN 'copyright' WHEN 4 THEN 'character'
                           WHEN 5 THEN 'meta' ELSE 'unknown' END,
                       post_count, ?
                FROM stage.tags ORDER BY source_id
                """,
                (provenance,),
            )
            self._raise_if_cancelled(cancel_event)
            connection.execute(
                """
                INSERT OR IGNORE INTO aliases(tag_id, alias, normalized_alias, alias_length)
                SELECT t.id, a.alias, a.alias, length(a.alias)
                FROM stage.aliases a JOIN tags t ON t.normalized_tag = a.target
                WHERE a.alias <> a.target
                ORDER BY a.source_id
                """
            )
            connection.execute(
                """
                INSERT INTO alias_keys(normalized_alias, alias_length, owner_count, canonical_conflict)
                SELECT a.normalized_alias, MIN(a.alias_length), COUNT(*),
                       CASE WHEN MAX(t.id IS NOT NULL) THEN 1 ELSE 0 END
                FROM aliases a LEFT JOIN tags t ON t.normalized_tag = a.normalized_alias
                GROUP BY a.normalized_alias
                """
            )
            connection.executescript(
                """
                CREATE INDEX idx_alias_length ON aliases(alias_length);
                CREATE INDEX idx_alias_tag_id ON aliases(tag_id);
                CREATE INDEX idx_tags_category ON tags(category);
                CREATE INDEX idx_tags_length_count ON tags(tag_length, count DESC);
                """
            )
            completed_at = _utc_now()
            metadata = {
                "schema_version": "2",
                "source": options.base_url,
                "transport": urlparse(options.base_url).scheme,
                "dataset": "danbooru_public_api",
                "generator": "anima_studio",
                "imported_at": completed_at,
                "revision": str(summary["content_sha256"])[:12],
                "sha256": summary["content_sha256"],
                "tag_count": summary["tag_count"],
                "alias_count": summary["alias_count"],
                "category_counts": _stable_json(summary["category_counts"]),
                "build_mode": options.mode,
                "general_min_posts": options.general_min_posts,
                "meta_min_posts": options.meta_min_posts,
                "identity_complete": True,
                "source_max_tag_id": summary["source_max_tag_id"],
                "source_max_alias_id": summary["source_max_alias_id"],
                "source_updated_at": summary["source_updated_at"],
                "source_cutoff_at": summary["source_cutoff_at"],
            }
            connection.executemany(
                "INSERT INTO metadata(key, value) VALUES(?, ?)",
                sorted((str(key), str(value)) for key, value in metadata.items()),
            )
            connection.commit()
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            actual_tags = int(connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0])
            actual_aliases = int(connection.execute("SELECT COUNT(*) FROM aliases").fetchone()[0])
            if (
                integrity.casefold() != "ok"
                or foreign_keys
                or actual_tags != int(summary["tag_count"])
                or actual_aliases != int(summary["alias_count"])
            ):
                raise sqlite3.DatabaseError("Danbooru snapshot validation failed")
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        try:
            self._raise_if_cancelled(cancel_event)
            with temporary.open("r+b") as handle:
                os.fsync(handle.fileno())
            self._replace_snapshot(temporary, index_path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _replace_snapshot(source: Path, destination: Path) -> None:
        last_error: OSError | None = None
        for attempt in range(20):
            try:
                os.replace(source, destination)
                return
            except PermissionError as error:
                last_error = error
                if attempt >= 19:
                    break
                time.sleep(0.05)
        if last_error is not None:
            raise last_error

    @staticmethod
    async def _emit(
        progress: Callable[[dict[str, Any]], Awaitable[None] | None] | None,
        event: dict[str, Any],
    ) -> None:
        if progress is None:
            return
        result = progress(event)
        if asyncio.iscoroutine(result):
            await result

    async def _paced_sleep(
        self, options: DanbooruBuildOptions, cancel_event: threading.Event
    ) -> None:
        if not self.pace_requests:
            return
        await self._cancelable_sleep(options.request_interval_ms / 1000, cancel_event)

    async def _retry_sleep(self, attempt: int, cancel_event: threading.Event) -> None:
        await self._cancelable_sleep(min(60.0, (2 ** (attempt - 1)) + random.random()), cancel_event)

    @staticmethod
    async def _cancelable_sleep(seconds: float, cancel_event: threading.Event) -> None:
        deadline = asyncio.get_running_loop().time() + max(0.0, seconds)
        while asyncio.get_running_loop().time() < deadline:
            if cancel_event.is_set():
                raise asyncio.CancelledError
            await asyncio.sleep(min(0.1, deadline - asyncio.get_running_loop().time()))

    @staticmethod
    def _raise_if_cancelled(cancel_event: threading.Event) -> None:
        if cancel_event.is_set():
            raise asyncio.CancelledError

    def _checkpoint_counts(self) -> dict[str, int]:
        if not self.checkpoint_path.is_file():
            return {"tag_count": 0, "alias_count": 0}
        with closing(sqlite3.connect(self.checkpoint_path, timeout=30)) as connection:
            return {
                "tag_count": int(connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0]),
                "alias_count": int(connection.execute("SELECT COUNT(*) FROM aliases").fetchone()[0]),
            }

    def _read_metadata(
        self, *, connection: sqlite3.Connection | None = None
    ) -> dict[str, str]:
        owns_connection = connection is None
        database = connection or sqlite3.connect(self.checkpoint_path, timeout=30)
        try:
            return {
                str(key): str(value)
                for key, value in database.execute("SELECT key, value FROM metadata")
            }
        finally:
            if owns_connection:
                database.close()

    def _metadata_value(self, key: str) -> str:
        with closing(sqlite3.connect(self.checkpoint_path, timeout=30)) as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key=?", (str(key),)
            ).fetchone()
        return str(row[0]) if row else ""

    def _set_metadata(self, values: Mapping[str, Any]) -> None:
        with closing(sqlite3.connect(self.checkpoint_path, timeout=30)) as connection, connection:
            connection.executemany(
                """
                INSERT INTO metadata(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                [(str(key), str(value)) for key, value in values.items()],
            )

    def _record_failure(self, message: str) -> None:
        if not self.checkpoint_path.is_file():
            return
        try:
            self._set_metadata(
                {
                    "last_error": " ".join(str(message or "build failed").split())[:500],
                    "last_failed_at": _utc_now(),
                }
            )
        except sqlite3.Error:
            return

    def _remove_checkpoint(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            Path(str(self.checkpoint_path) + suffix).unlink(missing_ok=True)


class DanbooruUpdateScheduler:
    """Persisted schedule requiring an explicit safe trigger for every run."""

    _SCHEMA: ClassVar[int] = 1

    def __init__(
        self,
        builder: DanbooruApiBuilder,
        path: str | os.PathLike[str],
    ) -> None:
        self.builder = builder
        self.path = Path(path).expanduser().resolve(strict=False)
        self._lock = threading.RLock()
        self._running = False

    @staticmethod
    def _defaults() -> dict[str, Any]:
        return {
            "schema_version": DanbooruUpdateScheduler._SCHEMA,
            "enabled": False,
            "interval_hours": 168,
            "options": asdict(DanbooruBuildOptions().normalized()),
            "configured_at": "",
            "next_run_at": "",
            "last_started_at": "",
            "last_completed_at": "",
            "last_error": "",
        }

    def snapshot(self) -> dict[str, Any]:
        state = self._load()
        return {
            **state,
            "running": self._running,
            "due": self.is_due(state=state),
            "network_default": "offline",
            "requires_confirmation": True,
        }

    def configure(
        self,
        *,
        enabled: bool,
        interval_hours: int = 168,
        options: DanbooruBuildOptions | Mapping[str, Any] | None = None,
        confirm_manual: bool = False,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if enabled and not confirm_manual:
            raise PermissionError("enabling scheduled Danbooru updates requires confirmation")
        effective = (
            options
            if isinstance(options, DanbooruBuildOptions)
            else DanbooruBuildOptions(**dict(options or {}))
        ).normalized()
        current_time = (now or datetime.now(UTC)).astimezone(UTC)
        interval = max(1, min(int(interval_hours), 24 * 365))
        state = self._load()
        state.update(
            {
                "enabled": bool(enabled),
                "interval_hours": interval,
                "options": asdict(effective),
                "configured_at": self._format_time(current_time),
                "next_run_at": (
                    self._format_time(current_time + timedelta(hours=interval))
                    if enabled
                    else ""
                ),
                "last_error": "",
            }
        )
        self._save(state)
        return self.snapshot()

    def is_due(
        self,
        *,
        now: datetime | None = None,
        state: Mapping[str, Any] | None = None,
    ) -> bool:
        current = dict(state or self._load())
        if not bool(current.get("enabled")):
            return False
        due_at = self._parse_time(current.get("next_run_at"))
        return due_at is not None and (now or datetime.now(UTC)).astimezone(UTC) >= due_at

    async def run_due(
        self,
        *,
        confirm_scheduled: bool = False,
        force: bool = False,
        now: datetime | None = None,
        progress: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        state = self._load()
        if not bool(state.get("enabled")):
            return {"started": False, "reason": "disabled", "schedule": self.snapshot()}
        if not force and not self.is_due(now=now, state=state):
            return {"started": False, "reason": "not_due", "schedule": self.snapshot()}
        if not confirm_scheduled:
            raise PermissionError("scheduled Danbooru network access requires a safe trigger")
        with self._lock:
            if self._running:
                return {"started": False, "reason": "already_running", "schedule": self.snapshot()}
            self._running = True
        started = (now or datetime.now(UTC)).astimezone(UTC)
        state["last_started_at"] = self._format_time(started)
        state["last_error"] = ""
        self._save(state)
        try:
            result = await self.builder.build(
                DanbooruBuildOptions(**dict(state.get("options") or {})),
                progress=progress,
                cancel_event=cancel_event,
            )
        except BaseException as error:
            state = self._load()
            state["last_error"] = " ".join(
                (str(error) or type(error).__name__).split()
            )[:500]
            state["next_run_at"] = self._format_time(
                started + timedelta(hours=int(state.get("interval_hours") or 168))
            )
            self._save(state)
            raise
        else:
            state = self._load()
            state["last_completed_at"] = self._format_time(started)
            state["last_error"] = ""
            state["next_run_at"] = self._format_time(
                started + timedelta(hours=int(state.get("interval_hours") or 168))
            )
            self._save(state)
            with self._lock:
                self._running = False
            return {"started": True, "result": result, "schedule": self.snapshot()}
        finally:
            with self._lock:
                self._running = False

    def _load(self) -> dict[str, Any]:
        defaults = self._defaults()
        if not self.path.is_file():
            return defaults
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return defaults
        if not isinstance(payload, Mapping) or int(payload.get("schema_version") or 0) != self._SCHEMA:
            return defaults
        defaults.update(dict(payload))
        return defaults

    def _save(self, state: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(_stable_json(dict(state)) + "\n", encoding="utf-8")
            with temporary.open("r+b") as handle:
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _format_time(value: datetime) -> str:
        return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text).astimezone(UTC)
        except ValueError:
            return None


@dataclass(frozen=True)
class WorkflowEntry:
    index: int
    filename: str
    path: Path


@dataclass(frozen=True)
class WorkflowDescriptor:
    entry: WorkflowEntry
    task_type: str
    profile_id: str
    display_name: str
    selectable: bool
    error: str = ""


class WorkflowRegistry:
    """Discover and inspect trusted ComfyUI API JSON manifests."""

    def __init__(self, workflow_dir: str | os.PathLike[str], settings: NativeNaturalSettings) -> None:
        self.workflow_dir = Path(workflow_dir).expanduser().resolve(strict=False)
        self.settings = settings

    def discover(self) -> tuple[WorkflowEntry, ...]:
        if not self.workflow_dir.is_dir():
            raise ValueError(f"workflow directory does not exist: {self.workflow_dir}")
        paths: list[Path] = []
        for candidate in self.workflow_dir.iterdir():
            if candidate.suffix.casefold() != ".json":
                continue
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(self.workflow_dir)
            except (OSError, RuntimeError, ValueError):
                continue
            if resolved.is_file():
                paths.append(resolved)
        paths.sort(key=lambda item: (item.name.casefold(), item.name))
        return tuple(WorkflowEntry(index, item.name, item) for index, item in enumerate(paths, 1))

    @staticmethod
    def _inspect(entry: WorkflowEntry) -> WorkflowDescriptor:
        try:
            payload = json.loads(entry.path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping) or not payload:
                raise ValueError("workflow manifest must be a non-empty object")
            classes = {str(node.get("class_type", "")) for node in payload.values() if isinstance(node, Mapping)}
            filename = entry.filename.casefold()
            if "upscale" in filename or "ImageScaleBy" in classes:
                task_type = "upscale"
            elif "inpaint" in filename:
                task_type = "inpaint"
            elif "img2img" in filename:
                task_type = "image_to_image"
            elif "control" in filename:
                task_type = "control"
            else:
                task_type = "text_to_image"
            profile_id = entry.path.stem.removesuffix("_api")
            title = next((str(node.get("_meta", {}).get("title")) for node in payload.values() if isinstance(node, Mapping) and isinstance(node.get("_meta"), Mapping) and node["_meta"].get("title")), entry.filename)
            return WorkflowDescriptor(entry, task_type, profile_id, title, task_type == "text_to_image")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return WorkflowDescriptor(entry, "invalid", "", entry.filename, False, str(exc)[:300])

    def describe(self) -> tuple[WorkflowDescriptor, ...]:
        return tuple(self._inspect(entry) for entry in self.discover())

    list_workflows = discover


class ConfigProfileService:
    """Atomic, secret-free named runtime profiles."""

    _SECRET = re.compile(r"(?:api[_-]?key|api[_-]?token|secret|password|authorization|cookie)", re.IGNORECASE)

    def __init__(self, storage_path: str | os.PathLike[str]) -> None:
        self.storage_path = Path(storage_path).resolve(strict=False)
        self._lock = threading.RLock()

    def _read(self) -> dict[str, Any]:
        if not self.storage_path.is_file():
            return {"schema_version": 1, "active_profile": "", "profiles": {}}
        payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1 or not isinstance(payload.get("profiles"), dict):
            raise ValueError("config profile store has an invalid schema")
        return payload

    def _write(self, state: Mapping[str, Any]) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.storage_path.with_suffix(self.storage_path.suffix + ".tmp")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, self.storage_path)

    @classmethod
    def _settings(cls, config: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for raw_key, value in config.items():
            key = str(raw_key)
            if cls._SECRET.search(key):
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                result[key] = value
            elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                result[key] = [str(item) for item in value[:128]]
        return result

    @staticmethod
    def _name(value: Any) -> str:
        name = _clean(value, limit=80)
        if not name or any(character in name for character in "\\/:*?\"<>|"):
            raise ValueError("config profile name is invalid")
        return name

    @staticmethod
    def _id(name: str) -> str:
        return hashlib.sha256(name.casefold().encode()).hexdigest()[:24]

    @staticmethod
    def _public(record: Mapping[str, Any], active: bool) -> dict[str, Any]:
        return {**dict(record), "active": active}

    def list_profiles(self) -> list[dict[str, Any]]:
        with self._lock:
            state = self._read()
            result = [self._public(item, key == state["active_profile"]) for key, item in state["profiles"].items()]
        return sorted(result, key=lambda item: item["name"].casefold())

    def save_profile(self, name: Any, config: Mapping[str, Any], *, overwrite: bool = False, activate: bool = False) -> dict[str, Any]:
        normalized = self._name(name)
        identifier = self._id(normalized)
        now = _utc_now()
        with self._lock:
            state = self._read()
            previous = state["profiles"].get(identifier)
            if previous and not overwrite:
                raise ValueError("config profile already exists")
            record = {"id": identifier, "name": normalized, "created_at": previous.get("created_at", now) if previous else now, "updated_at": now, "settings": self._settings(config)}
            state["profiles"][identifier] = record
            if activate:
                state["active_profile"] = identifier
            self._write(state)
        return self._public(record, activate or identifier == state["active_profile"])

    def _find(self, state: Mapping[str, Any], name: Any) -> tuple[str, dict[str, Any]]:
        normalized = self._name(name).casefold()
        matches = [(key, item) for key, item in state["profiles"].items() if str(item.get("name", "")).casefold() == normalized]
        if len(matches) != 1:
            raise KeyError(name)
        return matches[0]

    def export_profile(self, name: Any) -> dict[str, Any]:
        with self._lock:
            state = self._read()
            identifier, item = self._find(state, name)
            return self._public(item, identifier == state["active_profile"])

    def delete_profile(self, name: Any) -> dict[str, Any]:
        with self._lock:
            state = self._read()
            identifier, item = self._find(state, name)
            active = identifier == state["active_profile"]
            del state["profiles"][identifier]
            if active:
                state["active_profile"] = ""
            self._write(state)
            return self._public(item, active)

    def import_profile(self, payload: Mapping[str, Any], *, overwrite: bool = False) -> dict[str, Any]:
        return self.save_profile(payload.get("name"), payload.get("settings", {}), overwrite=overwrite)

    def activate_profile(self, name: Any, config: MutableMapping[str, Any], *, persist_updates: Callable[[dict[str, Any]], bool | None] | None = None) -> dict[str, Any]:
        with self._lock:
            state = self._read()
            identifier, item = self._find(state, name)
            updates = dict(item["settings"])
            previous = {key: config.get(key) for key in updates}
            if persist_updates is not None:
                if persist_updates(updates) is False:
                    raise RuntimeError("runtime profile persistence failed")
            else:
                config.update(updates)
                save = getattr(config, "save_config", None)
                if callable(save):
                    save()
            try:
                state["active_profile"] = identifier
                self._write(state)
            except Exception:
                config.update(previous)
                raise
            return self._public(item, True)


__all__ = [
    "ConfigProfileService",
    "DanbooruApiBuilder",
    "DanbooruBuildOptions",
    "DanbooruTagIndex",
    "DanbooruUpdateScheduler",
    "LoraAnalysisPipeline",
    "LoraArchiveService",
    "LoraCatalogService",
    "LoraDownloadService",
    "LoraRecord",
    "LoraVisualService",
    "PromptAssetLibrary",
    "PromptLab",
    "PromptLabBatch",
    "PromptPlanConflictError",
    "PromptPlanStore",
    "WorkflowRegistry",
]
