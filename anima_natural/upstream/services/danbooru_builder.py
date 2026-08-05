"""Resumable official Danbooru API index generation.

The generated dataset is operator-owned runtime data.  It is built below
``plugin_data`` and is never bundled with the plugin or a release archive.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import re
import sqlite3
import threading
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import aiohttp

from .danbooru_index import (
    DanbooruIndexError,
    DanbooruTagIndex,
    _PinnedResolver,
    normalize_tag,
)


DEFAULT_API_BASE_URL = "https://danbooru.donmai.us"
DEFAULT_USER_AGENT = (
    "AstrBot-Comfy-Anima-Indexer/2.0.0 "
    "(https://github.com/yenn001/astrbot_plugin_comfy_anima)"
)
MAX_PAGE_BYTES = 8 * 1024 * 1024
_CHECKPOINT_SCHEMA = "1"
_CATEGORY_NAMES = {
    0: "general",
    1: "artist",
    3: "copyright",
    4: "character",
    5: "meta",
}
_SAFE_NAME_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,256}$")


class DanbooruBuildError(RuntimeError):
    """A safe, user-facing official API build failure."""

    def __init__(self, message: str, code: str = "danbooru_build_failed"):
        super().__init__(message)
        self.user_message = message
        self.code = code


@dataclass(frozen=True)
class DanbooruBuildOptions:
    """Bounded operator settings for one official API generation."""

    base_url: str = DEFAULT_API_BASE_URL
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

    def normalized(self) -> "DanbooruBuildOptions":
        mode = str(self.mode or "identity").strip().casefold()
        if mode not in {"identity", "full"}:
            raise DanbooruBuildError(
                "Danbooru 生成模式必须是 identity 或 full",
                "invalid_build_mode",
            )
        return DanbooruBuildOptions(
            base_url=str(self.base_url or DEFAULT_API_BASE_URL).strip().rstrip("/"),
            proxy_url=str(self.proxy_url or "").strip(),
            mode=mode,
            general_min_posts=max(0, min(int(self.general_min_posts), 1_000_000)),
            meta_min_posts=max(0, min(int(self.meta_min_posts), 1_000_000)),
            page_size=max(1, min(int(self.page_size), 1000)),
            request_interval_ms=max(
                250,
                min(int(self.request_interval_ms), 10_000),
            ),
            timeout_seconds=max(10, min(int(self.timeout_seconds), 300)),
            max_records=max(1_000, min(int(self.max_records), 3_000_000)),
            include_aliases=bool(self.include_aliases),
            max_retries=max(1, min(int(self.max_retries), 8)),
        )

    def signature_payload(self) -> dict[str, Any]:
        payload = asdict(self.normalized())
        payload.pop("proxy_url", None)
        payload.pop("timeout_seconds", None)
        payload.pop("request_interval_ms", None)
        payload.pop("max_retries", None)
        return payload


ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


class DanbooruApiBuilder:
    """Build one immutable local index through a resumable staging database."""

    def __init__(
        self,
        index: DanbooruTagIndex,
        checkpoint_path: Path,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
    ):
        self.index = index
        self.checkpoint_path = Path(checkpoint_path)
        self.user_agent = str(user_agent or DEFAULT_USER_AGENT).strip()

    async def build(
        self,
        options: DanbooruBuildOptions,
        *,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        effective = options.normalized()
        cancellation = cancel_event or threading.Event()
        base_url, base_addresses = await self._validated_endpoint(
            effective.base_url,
            label="Danbooru API",
        )
        proxy_url = ""
        if effective.proxy_url:
            proxy_url, _ = await self._validated_endpoint(
                effective.proxy_url,
                label="Danbooru API 代理",
                proxy=True,
            )

        signature = hashlib.sha256(
            json.dumps(
                effective.signature_payload(),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        resumed = self._prepare_checkpoint(signature, effective)
        await self._emit(
            progress,
            phase="preflight",
            event="build_resumed" if resumed else "build_started",
            message=(
                "检测到相同配置的未完成快照，正在从最近游标继续。"
                if resumed
                else "已创建新的 Danbooru API 生成检查点。"
            ),
            completed=0,
            total=7,
            resumed=resumed,
        )

        timeout = aiohttp.ClientTimeout(total=float(effective.timeout_seconds))
        connector = None
        if not proxy_url:
            connector = aiohttp.TCPConnector(
                resolver=_PinnedResolver(urlparse(base_url).hostname, base_addresses),
                use_dns_cache=True,
            )
        try:
            async with aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers={"User-Agent": self.user_agent, "Accept": "application/json"},
            ) as session:
                await self._fetch_tags(
                    session,
                    effective,
                    base_url,
                    proxy_url,
                    cancellation,
                    progress,
                )
                if effective.include_aliases:
                    await self._fetch_aliases(
                        session,
                        effective,
                        base_url,
                        proxy_url,
                        cancellation,
                        progress,
                    )
                else:
                    self._set_metadata({"aliases_done": "1", "build_phase": "build"})

            self._raise_if_cancelled(cancellation)
            await self._emit(
                progress,
                phase="stream_build",
                event="stream_build_started",
                message="官方记录抓取完成，正在流式构建新的 SQLite 快照。",
                completed=5,
                total=7,
                **self._checkpoint_counts(),
            )
            summary = self._checkpoint_summary(effective, base_url)
            build_task = asyncio.create_task(
                asyncio.to_thread(
                    self.index.build_from_records,
                    self._iter_checkpoint_records(),
                    source=base_url,
                    provenance={
                        "source": base_url,
                        "transport": urlparse(base_url).scheme,
                        "dataset": "danbooru_public_api",
                    },
                    metadata={
                        "source": base_url,
                        "transport": urlparse(base_url).scheme,
                        "dataset": "danbooru_public_api",
                        "source_updated_at": summary["source_updated_at"],
                        "source_max_tag_id": summary["source_max_tag_id"],
                        "source_max_alias_id": summary["source_max_alias_id"],
                        "build_mode": effective.mode,
                        "general_min_posts": effective.general_min_posts,
                        "meta_min_posts": effective.meta_min_posts,
                        "identity_complete": summary["identity_complete"],
                        "source_cutoff_at": summary["source_cutoff_at"],
                        "generator": "astrbot_plugin_comfy_anima",
                    },
                    revision=summary["revision"],
                    sha256=summary["logical_sha256"],
                    _cancel_event=cancellation,
                ),
                name="danbooru-index-stream-build",
            )
            try:
                result = await asyncio.shield(build_task)
            except asyncio.CancelledError as cancel_exc:
                cancellation.set()
                try:
                    result = await asyncio.wait_for(
                        asyncio.shield(build_task), timeout=30.0
                    )
                except (asyncio.TimeoutError, DanbooruIndexError):
                    raise cancel_exc
                else:
                    # Cancellation arrived after the atomic replace had already
                    # committed.  Report the real successful state rather than
                    # claiming the old snapshot was retained.
                    cancellation.clear()
            self._raise_if_cancelled(cancellation)
            await self._emit(
                progress,
                phase="validate_snapshot",
                event="snapshot_validated",
                message="新快照已通过结构、计数与 SQLite 完整性校验。",
                completed=6,
                total=7,
                tag_count=int(result.get("tag_count") or 0),
                alias_count=int(result.get("alias_count") or 0),
            )
            await self._emit(
                progress,
                phase="activate_snapshot",
                event="snapshot_activated",
                message="新的 Danbooru 本地索引已原子激活。",
                completed=7,
                total=7,
                revision=str(result.get("revision") or ""),
            )
            self._remove_checkpoint_files()
            return {
                **result,
                "mode": effective.mode,
                "resumed": resumed,
                "source_updated_at": summary["source_updated_at"],
                "source_cutoff_at": summary["source_cutoff_at"],
                "source_max_tag_id": summary["source_max_tag_id"],
                "source_max_alias_id": summary["source_max_alias_id"],
                "identity_complete": summary["identity_complete"],
                "source_category_counts": summary["category_counts"],
                "category_counts": dict(
                    result.get("category_counts")
                    or summary["category_counts"]
                ),
            }
        except asyncio.CancelledError:
            cancellation.set()
            raise
        except DanbooruBuildError:
            raise
        except DanbooruIndexError as exc:
            raise DanbooruBuildError(str(exc), "snapshot_build_failed") from exc
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise DanbooruBuildError(
                f"Danbooru API 请求失败：{type(exc).__name__}",
                "api_request_failed",
            ) from exc

    async def _fetch_tags(
        self,
        session: aiohttp.ClientSession,
        options: DanbooruBuildOptions,
        base_url: str,
        proxy_url: str,
        cancel_event: threading.Event,
        progress: ProgressCallback | None,
    ) -> None:
        plan = self._category_plan(options)
        for category_index, (category, minimum_posts) in enumerate(plan, start=1):
            done_key = f"tags_done_{category}"
            if self._metadata_value(done_key) == "1":
                continue
            high_water_key = f"tags_high_water_{category}"
            high_water = int(self._metadata_value(high_water_key) or 0)
            if high_water <= 0:
                high_water = await self._fetch_high_water(
                    session,
                    urljoin(base_url + "/", "tags.json"),
                    params={
                        "search[category]": category,
                        "search[is_deprecated]": "false",
                        **(
                            {"search[post_count_gteq]": minimum_posts}
                            if minimum_posts > 0
                            else {}
                        ),
                    },
                    proxy_url=proxy_url,
                    options=options,
                    cancel_event=cancel_event,
                )
                self._set_metadata(
                    {
                        high_water_key: str(high_water),
                        "source_cutoff_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            cursor = int(self._metadata_value(f"tags_cursor_{category}") or 0)
            pages = int(self._metadata_value(f"tags_pages_{category}") or 0)
            while True:
                self._raise_if_cancelled(cancel_event)
                params: dict[str, str | int] = {
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
                    urljoin(base_url + "/", "tags.json"),
                    params=params,
                    proxy_url=proxy_url,
                    options=options,
                    cancel_event=cancel_event,
                )
                if not rows:
                    self._set_metadata(
                        {
                            done_key: "1",
                            "build_phase": f"tags:{category}:done",
                        }
                    )
                    break
                accepted, next_cursor, latest_updated = self._store_tag_page(
                    rows,
                    category=category,
                    minimum_posts=minimum_posts,
                    cursor=cursor,
                )
                if next_cursor <= cursor:
                    raise DanbooruBuildError(
                        "Danbooru Tag API 游标没有向前推进",
                        "tag_cursor_stalled",
                    )
                cursor = next_cursor
                pages += 1
                counts = self._checkpoint_counts()
                if counts["tag_count"] > options.max_records:
                    raise DanbooruBuildError(
                        "Danbooru 生成记录超过配置上限；请提高上限或提高 General/Meta 帖子阈值",
                        "record_limit_exceeded",
                    )
                updates = {
                    f"tags_cursor_{category}": str(cursor),
                    f"tags_pages_{category}": str(pages),
                    "build_phase": f"tags:{category}",
                }
                if latest_updated:
                    updates["source_updated_at"] = max(
                        latest_updated,
                        self._metadata_value("source_updated_at"),
                    )
                self._set_metadata(updates)
                await self._emit(
                    progress,
                    phase="download_tags",
                    event="tag_page_committed",
                    message=(
                        f"已写入 {_CATEGORY_NAMES[category]} 第 {pages} 页；"
                        f"本页接受 {accepted} 条。"
                    ),
                    completed=1 + min(category_index, 3),
                    total=7,
                    category=_CATEGORY_NAMES[category],
                    pages=pages,
                    cursor=cursor,
                    **counts,
                )
                await self._paced_sleep(options, cancel_event)

    async def _fetch_aliases(
        self,
        session: aiohttp.ClientSession,
        options: DanbooruBuildOptions,
        base_url: str,
        proxy_url: str,
        cancel_event: threading.Event,
        progress: ProgressCallback | None,
    ) -> None:
        if self._metadata_value("aliases_done") == "1":
            return
        high_water = int(self._metadata_value("aliases_high_water") or 0)
        if high_water <= 0:
            high_water = await self._fetch_high_water(
                session,
                urljoin(base_url + "/", "tag_aliases.json"),
                params={"search[status]": "active"},
                proxy_url=proxy_url,
                options=options,
                cancel_event=cancel_event,
            )
            self._set_metadata(
                {
                    "aliases_high_water": str(high_water),
                    "source_cutoff_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        cursor = int(self._metadata_value("aliases_cursor") or 0)
        pages = int(self._metadata_value("aliases_pages") or 0)
        while True:
            self._raise_if_cancelled(cancel_event)
            rows = await self._request_json(
                session,
                urljoin(base_url + "/", "tag_aliases.json"),
                params={
                    "limit": options.page_size,
                    "page": f"a{cursor}",
                    "search[status]": "active",
                    "search[id_lteq]": high_water,
                },
                proxy_url=proxy_url,
                options=options,
                cancel_event=cancel_event,
            )
            if not rows:
                self._set_metadata(
                    {"aliases_done": "1", "build_phase": "aliases:done"}
                )
                break
            accepted, next_cursor, latest_updated = self._store_alias_page(
                rows,
                cursor=cursor,
            )
            if next_cursor <= cursor:
                raise DanbooruBuildError(
                    "Danbooru Alias API 游标没有向前推进",
                    "alias_cursor_stalled",
                )
            cursor = next_cursor
            pages += 1
            updates = {
                "aliases_cursor": str(cursor),
                "aliases_pages": str(pages),
                "build_phase": "aliases",
            }
            if latest_updated:
                updates["source_updated_at"] = max(
                    latest_updated,
                    self._metadata_value("source_updated_at"),
                )
            self._set_metadata(updates)
            await self._emit(
                progress,
                phase="download_aliases",
                event="alias_page_committed",
                message=f"已写入 Alias 第 {pages} 页；本页接受 {accepted} 条。",
                completed=4,
                total=7,
                pages=pages,
                cursor=cursor,
                **self._checkpoint_counts(),
            )
            await self._paced_sleep(options, cancel_event)

    async def _request_json(
        self,
        session: aiohttp.ClientSession,
        url: str,
        *,
        params: Mapping[str, str | int],
        proxy_url: str,
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
                    proxy=proxy_url or None,
                    allow_redirects=False,
                ) as response:
                    if 300 <= response.status < 400:
                        raise DanbooruBuildError(
                            "Danbooru API 重定向已被拒绝",
                            "api_redirect_rejected",
                        )
                    if response.status in {400, 401, 403, 404, 422}:
                        raise DanbooruBuildError(
                            f"Danbooru API 返回 HTTP {response.status}",
                            "api_request_rejected",
                        )
                    if response.status == 429 or response.status >= 500:
                        retry_after = self._retry_after(response.headers)
                        last_error = f"HTTP {response.status}"
                        if attempt >= options.max_retries:
                            break
                        await self._cancelable_sleep(
                            retry_after
                            if retry_after is not None
                            else min(60.0, (2 ** (attempt - 1)) + random.random()),
                            cancel_event,
                        )
                        continue
                    if response.status >= 400:
                        raise DanbooruBuildError(
                            f"Danbooru API 返回 HTTP {response.status}",
                            "api_request_failed",
                        )
                    payload = await self._bounded_response_bytes(response)
                decoded = json.loads(payload.decode("utf-8"))
                if not isinstance(decoded, list) or any(
                    not isinstance(item, dict) for item in decoded
                ):
                    raise DanbooruBuildError(
                        "Danbooru API 返回了无效 JSON 结构",
                        "invalid_api_payload",
                    )
                return [dict(item) for item in decoded]
            except DanbooruBuildError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError, json.JSONDecodeError) as exc:
                last_error = type(exc).__name__
                if attempt >= options.max_retries:
                    break
                await self._cancelable_sleep(
                    min(60.0, (2 ** (attempt - 1)) + random.random()),
                    cancel_event,
                )
        raise DanbooruBuildError(
            f"Danbooru API 连续重试失败：{last_error or 'unknown'}",
            "api_retry_exhausted",
        )

    async def _fetch_high_water(
        self,
        session: aiohttp.ClientSession,
        url: str,
        *,
        params: Mapping[str, str | int],
        proxy_url: str,
        options: DanbooruBuildOptions,
        cancel_event: threading.Event,
    ) -> int:
        rows = await self._request_json(
            session,
            url,
            params={
                **dict(params),
                "limit": 1,
                "page": "b2147483647",
            },
            proxy_url=proxy_url,
            options=options,
            cancel_event=cancel_event,
        )
        if not rows:
            return 0
        high_water = self._positive_int(rows[0].get("id"))
        if high_water <= 0:
            raise DanbooruBuildError(
                "Danbooru API 没有返回合法的高水位 ID",
                "invalid_high_water",
            )
        return high_water

    @staticmethod
    async def _bounded_response_bytes(response: aiohttp.ClientResponse) -> bytes:
        declared = response.headers.get("Content-Length", "")
        if declared:
            try:
                if int(declared) > MAX_PAGE_BYTES:
                    raise DanbooruBuildError(
                        "Danbooru API 单页响应超过安全上限",
                        "api_page_too_large",
                    )
            except ValueError:
                pass
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.content.iter_chunked(64 * 1024):
            total += len(chunk)
            if total > MAX_PAGE_BYTES:
                raise DanbooruBuildError(
                    "Danbooru API 单页响应超过安全上限",
                    "api_page_too_large",
                )
            chunks.append(chunk)
        return b"".join(chunks)

    def _prepare_checkpoint(
        self,
        signature: str,
        options: DanbooruBuildOptions,
    ) -> bool:
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        resumed = False
        if self.checkpoint_path.is_file():
            try:
                existing = self._read_metadata()
            except (OSError, sqlite3.Error):
                existing = {}
            resumed = bool(
                existing.get("schema_version") == _CHECKPOINT_SCHEMA
                and existing.get("signature") == signature
                and existing.get("completed") != "1"
            )
            if not resumed:
                self.checkpoint_path.unlink(missing_ok=True)
        if not self.checkpoint_path.is_file():
            connection = sqlite3.connect(self.checkpoint_path, timeout=30)
            try:
                connection.executescript(
                    """
                    PRAGMA journal_mode=WAL;
                    PRAGMA synchronous=FULL;
                    CREATE TABLE metadata (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );
                    CREATE TABLE tags (
                        source_id INTEGER PRIMARY KEY,
                        name TEXT NOT NULL UNIQUE,
                        category INTEGER NOT NULL,
                        post_count INTEGER NOT NULL CHECK (post_count >= 0),
                        updated_at TEXT NOT NULL DEFAULT ''
                    );
                    CREATE TABLE aliases (
                        source_id INTEGER PRIMARY KEY,
                        antecedent_name TEXT NOT NULL,
                        consequent_name TEXT NOT NULL,
                        updated_at TEXT NOT NULL DEFAULT '',
                        UNIQUE(antecedent_name, consequent_name)
                    );
                    CREATE INDEX idx_checkpoint_alias_consequent
                        ON aliases(consequent_name);
                    """
                )
                now = datetime.now(timezone.utc).isoformat()
                metadata = {
                    "schema_version": _CHECKPOINT_SCHEMA,
                    "signature": signature,
                    "started_at": now,
                    "build_phase": "preflight",
                    "completed": "0",
                    "options": json.dumps(
                        options.signature_payload(),
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                }
                connection.executemany(
                    "INSERT INTO metadata(key, value) VALUES (?, ?)",
                    metadata.items(),
                )
                connection.commit()
            finally:
                connection.close()
        return resumed

    def _store_tag_page(
        self,
        rows: list[dict[str, Any]],
        *,
        category: int,
        minimum_posts: int,
        cursor: int,
    ) -> tuple[int, int, str]:
        accepted = 0
        next_cursor = cursor
        latest_updated = ""
        connection = sqlite3.connect(self.checkpoint_path, timeout=30)
        try:
            connection.execute("BEGIN IMMEDIATE")
            for row in rows:
                source_id = self._positive_int(row.get("id"))
                next_cursor = max(next_cursor, source_id)
                name = str(row.get("name") or "").strip()
                row_category = self._nonnegative_int(row.get("category"))
                post_count = self._nonnegative_int(row.get("post_count"))
                deprecated = row.get("is_deprecated") is True
                updated_at = self._safe_timestamp(row.get("updated_at"))
                latest_updated = max(latest_updated, updated_at)
                if (
                    not source_id
                    or row_category != category
                    or deprecated
                    or post_count < minimum_posts
                    or not _SAFE_NAME_RE.fullmatch(name)
                ):
                    continue
                connection.execute(
                    """INSERT OR REPLACE INTO tags
                       (source_id, name, category, post_count, updated_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (source_id, name, category, post_count, updated_at),
                )
                accepted += 1
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return accepted, next_cursor, latest_updated

    def _store_alias_page(
        self,
        rows: list[dict[str, Any]],
        *,
        cursor: int,
    ) -> tuple[int, int, str]:
        accepted = 0
        next_cursor = cursor
        latest_updated = ""
        connection = sqlite3.connect(self.checkpoint_path, timeout=30)
        try:
            connection.execute("BEGIN IMMEDIATE")
            for row in rows:
                source_id = self._positive_int(row.get("id"))
                next_cursor = max(next_cursor, source_id)
                antecedent = str(row.get("antecedent_name") or "").strip()
                consequent = str(row.get("consequent_name") or "").strip()
                status = str(row.get("status") or "").strip().casefold()
                updated_at = self._safe_timestamp(row.get("updated_at"))
                latest_updated = max(latest_updated, updated_at)
                if (
                    not source_id
                    or status != "active"
                    or not _SAFE_NAME_RE.fullmatch(antecedent)
                    or not _SAFE_NAME_RE.fullmatch(consequent)
                    or antecedent == consequent
                ):
                    continue
                connection.execute(
                    """INSERT OR REPLACE INTO aliases
                       (source_id, antecedent_name, consequent_name, updated_at)
                       VALUES (?, ?, ?, ?)""",
                    (source_id, antecedent, consequent, updated_at),
                )
                accepted += 1
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return accepted, next_cursor, latest_updated

    def _iter_checkpoint_records(self) -> Iterator[dict[str, Any]]:
        connection = sqlite3.connect(
            self.checkpoint_path.resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=30,
            check_same_thread=False,
        )
        try:
            connection.create_function(
                "normalize_tag",
                1,
                normalize_tag,
                deterministic=True,
            )
            cursor = connection.execute(
                """WITH RECURSIVE alias_paths(origin, current, path, depth) AS (
                       SELECT antecedent_name, consequent_name,
                              '|' || antecedent_name || '|' || consequent_name || '|',
                              1
                       FROM aliases
                       UNION ALL
                       SELECT p.origin, a.consequent_name,
                              p.path || a.consequent_name || '|', p.depth + 1
                       FROM alias_paths p
                       JOIN aliases a ON a.antecedent_name = p.current
                       WHERE p.depth < 32
                         AND instr(
                               p.path,
                               '|' || a.consequent_name || '|'
                             ) = 0
                   ), resolved_aliases AS (
                       SELECT DISTINCT origin AS antecedent_name,
                                       normalize_tag(current) AS normalized_consequent
                       FROM alias_paths
                       WHERE normalize_tag(origin) <> normalize_tag(current)
                   ), ranked_tags AS (
                       SELECT source_id, name, category, post_count,
                              normalize_tag(name) AS normalized_name,
                              ROW_NUMBER() OVER (
                                  PARTITION BY normalize_tag(name)
                                  ORDER BY
                                      CASE
                                          WHEN name = normalize_tag(name) THEN 0
                                          ELSE 1
                                      END,
                                      post_count DESC,
                                      source_id DESC,
                                      name
                              ) AS normalized_rank
                       FROM tags
                   ), normalized_aliases AS (
                       SELECT antecedent_name, normalized_consequent
                       FROM resolved_aliases
                       WHERE normalized_consequent <> ''
                       UNION
                       SELECT name AS antecedent_name,
                              normalized_name AS normalized_consequent
                       FROM ranked_tags
                       WHERE normalized_rank > 1
                         AND normalized_name <> ''
                         AND normalize_tag(name) <> ''
                   )
                   SELECT t.source_id, t.name, t.category, t.post_count,
                          a.antecedent_name
                   FROM ranked_tags t
                   LEFT JOIN normalized_aliases a
                     ON a.normalized_consequent = t.normalized_name
                   WHERE t.normalized_rank = 1
                     AND t.normalized_name <> ''
                   ORDER BY t.source_id, a.antecedent_name"""
            )
            current_id = None
            record: dict[str, Any] | None = None
            aliases: list[str] = []
            for source_id, name, category, post_count, alias in cursor:
                if current_id is not None and int(source_id) != current_id:
                    assert record is not None
                    record["aliases"] = tuple(aliases)
                    yield record
                    aliases = []
                if current_id is None or int(source_id) != current_id:
                    current_id = int(source_id)
                    record = {
                        "tag": str(name),
                        "category": int(category),
                        "count": int(post_count),
                        "provenance": {"source_id": current_id},
                    }
                if alias:
                    aliases.append(str(alias))
            if record is not None:
                record["aliases"] = tuple(aliases)
                yield record
        finally:
            connection.close()

    def _checkpoint_summary(
        self,
        options: DanbooruBuildOptions,
        base_url: str,
    ) -> dict[str, Any]:
        connection = sqlite3.connect(self.checkpoint_path, timeout=30)
        try:
            tag_count, max_tag_id = connection.execute(
                "SELECT COUNT(*), COALESCE(MAX(source_id), 0) FROM tags"
            ).fetchone()
            alias_count, max_alias_id = connection.execute(
                "SELECT COUNT(*), COALESCE(MAX(source_id), 0) FROM aliases"
            ).fetchone()
            source_updated_at = self._metadata_value(
                "source_updated_at",
                connection=connection,
            )
            source_cutoff_at = self._metadata_value(
                "source_cutoff_at",
                connection=connection,
            )
            tag_high_waters = [
                int(value)
                for _key, value in connection.execute(
                    "SELECT key, value FROM metadata WHERE key LIKE 'tags_high_water_%'"
                ).fetchall()
                if str(value).isdigit()
            ]
            alias_high_water = self._positive_int(
                self._metadata_value("aliases_high_water", connection=connection)
            )
            category_counts = {
                _CATEGORY_NAMES[int(category)]: int(amount)
                for category, amount in connection.execute(
                    "SELECT category, COUNT(*) FROM tags GROUP BY category"
                ).fetchall()
                if int(category) in _CATEGORY_NAMES
            }
            active_categories = {
                str(key): int(value)
                for key, value in dict(
                    self.index.status().get("category_counts") or {}
                ).items()
            }
            incomplete_identity = [
                name
                for name in ("artist", "copyright", "character")
                if category_counts.get(name, 0) <= 0
                or category_counts.get(name, 0) < active_categories.get(name, 0)
            ]
            if incomplete_identity:
                raise DanbooruBuildError(
                    "官方 API 生成的身份分类数量低于当前快照，已拒绝激活："
                    + ", ".join(incomplete_identity),
                    "identity_completeness_failed",
                )
            logical = {
                "source": base_url,
                "mode": options.mode,
                "tag_count": int(tag_count),
                "alias_count": int(alias_count),
                "max_tag_id": max(tag_high_waters, default=int(max_tag_id)),
                "max_alias_id": alias_high_water or int(max_alias_id),
                "source_updated_at": source_updated_at,
                "source_cutoff_at": source_cutoff_at,
                "general_min_posts": options.general_min_posts,
                "meta_min_posts": options.meta_min_posts,
                "category_counts": category_counts,
                "identity_complete": True,
            }
            hasher = hashlib.sha256()
            hasher.update(
                json.dumps(
                    logical,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            hasher.update(b"\n--tags--\n")
            for row in connection.execute(
                """SELECT source_id, name, category, post_count, updated_at
                   FROM tags ORDER BY source_id, name"""
            ):
                hasher.update(
                    json.dumps(
                        list(row),
                        ensure_ascii=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
                hasher.update(b"\n")
            hasher.update(b"--aliases--\n")
            for row in connection.execute(
                """SELECT source_id, antecedent_name, consequent_name, updated_at
                   FROM aliases ORDER BY source_id, antecedent_name, consequent_name"""
            ):
                hasher.update(
                    json.dumps(
                        list(row),
                        ensure_ascii=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
                hasher.update(b"\n")
            digest = hasher.hexdigest()
            return {
                "logical_sha256": digest,
                "revision": digest[:12],
                "source_updated_at": source_updated_at,
                "source_cutoff_at": source_cutoff_at,
                "source_max_tag_id": logical["max_tag_id"],
                "source_max_alias_id": logical["max_alias_id"],
                "category_counts": category_counts,
                "identity_complete": True,
            }
        finally:
            connection.close()

    def _checkpoint_counts(self) -> dict[str, int]:
        if not self.checkpoint_path.is_file():
            return {"tag_count": 0, "alias_count": 0}
        connection = sqlite3.connect(self.checkpoint_path, timeout=30)
        try:
            return {
                "tag_count": int(
                    connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
                ),
                "alias_count": int(
                    connection.execute("SELECT COUNT(*) FROM aliases").fetchone()[0]
                ),
            }
        finally:
            connection.close()

    def checkpoint_status(self) -> dict[str, Any]:
        if not self.checkpoint_path.is_file():
            return {"available": False}
        try:
            metadata = self._read_metadata()
            counts = self._checkpoint_counts()
        except (OSError, sqlite3.Error):
            return {"available": False, "error": "checkpoint_unreadable"}
        return {
            "available": True,
            "phase": metadata.get("build_phase", ""),
            "started_at": metadata.get("started_at", ""),
            "source_updated_at": metadata.get("source_updated_at", ""),
            "source_cutoff_at": metadata.get("source_cutoff_at", ""),
            **counts,
        }

    def _remove_checkpoint_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(str(self.checkpoint_path) + suffix).unlink(missing_ok=True)
            except OSError:
                continue

    def _read_metadata(self) -> dict[str, str]:
        connection = sqlite3.connect(self.checkpoint_path, timeout=30)
        try:
            return {
                str(key): str(value)
                for key, value in connection.execute(
                    "SELECT key, value FROM metadata"
                ).fetchall()
            }
        finally:
            connection.close()

    def _metadata_value(
        self,
        key: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> str:
        owns_connection = connection is None
        db = connection or sqlite3.connect(self.checkpoint_path, timeout=30)
        try:
            row = db.execute(
                "SELECT value FROM metadata WHERE key = ?",
                (str(key),),
            ).fetchone()
            return str(row[0]) if row else ""
        finally:
            if owns_connection:
                db.close()

    def _set_metadata(self, values: Mapping[str, Any]) -> None:
        connection = sqlite3.connect(self.checkpoint_path, timeout=30)
        try:
            connection.executemany(
                """INSERT INTO metadata(key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                [(str(key), str(value)) for key, value in values.items()],
            )
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def _category_plan(options: DanbooruBuildOptions) -> tuple[tuple[int, int], ...]:
        if options.mode == "full":
            return ((4, 0), (3, 0), (1, 0), (0, 0), (5, 0))
        return (
            (4, 0),
            (3, 0),
            (1, 0),
            (0, options.general_min_posts),
            (5, options.meta_min_posts),
        )

    @staticmethod
    def _positive_int(value: Any) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 0
        return parsed if parsed > 0 else 0

    @staticmethod
    def _nonnegative_int(value: Any) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return 0
        return max(0, parsed)

    @staticmethod
    def _safe_timestamp(value: Any) -> str:
        text = str(value or "").strip()
        return text[:64] if len(text) <= 64 else ""

    @staticmethod
    def _retry_after(headers: Mapping[str, str]) -> float | None:
        raw = str(headers.get("Retry-After") or "").strip()
        if not raw:
            return None
        try:
            return max(0.25, min(float(raw), 60.0))
        except ValueError:
            return None

    async def _validated_endpoint(
        self,
        value: str,
        *,
        label: str,
        proxy: bool = False,
    ) -> tuple[str, tuple[Any, ...]]:
        parsed = urlparse(str(value or "").strip())
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise DanbooruBuildError(
                f"{label} 地址不能包含凭据、查询参数或片段",
                "invalid_endpoint",
            )
        if proxy and parsed.path not in {"", "/"}:
            raise DanbooruBuildError(
                f"{label} 地址不能包含路径",
                "invalid_proxy_url",
            )
        try:
            addresses = await DanbooruTagIndex._validate_update_url(parsed)
        except DanbooruIndexError as exc:
            raise DanbooruBuildError(str(exc), "invalid_endpoint") from exc
        sanitized = urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", "")
        )
        return sanitized, addresses

    @staticmethod
    async def _emit(
        callback: ProgressCallback | None,
        **payload: Any,
    ) -> None:
        if callback is None:
            return
        result = callback(dict(payload))
        if isinstance(result, Awaitable):
            await result

    @staticmethod
    def _raise_if_cancelled(cancel_event: threading.Event) -> None:
        if cancel_event.is_set():
            raise asyncio.CancelledError

    async def _paced_sleep(
        self,
        options: DanbooruBuildOptions,
        cancel_event: threading.Event,
    ) -> None:
        await self._cancelable_sleep(
            options.request_interval_ms / 1000.0,
            cancel_event,
        )

    @staticmethod
    async def _cancelable_sleep(
        seconds: float,
        cancel_event: threading.Event,
    ) -> None:
        remaining = max(0.0, float(seconds))
        while remaining > 0:
            if cancel_event.is_set():
                raise asyncio.CancelledError
            step = min(0.25, remaining)
            await asyncio.sleep(step)
            remaining -= step


__all__ = [
    "DEFAULT_API_BASE_URL",
    "DanbooruApiBuilder",
    "DanbooruBuildError",
    "DanbooruBuildOptions",
]
