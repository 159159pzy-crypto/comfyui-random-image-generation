"""Coordinate pool sources and their favorites without publishing half a change.

Existing store methods run on isolated in-memory planning copies. Only this
coordinator publishes their results, favorites first, with a durable undo record.
The journal is recovery evidence, never a queue of user actions to replay.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import tempfile
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

from .async_utils import serialized_mutation
from .catalog import CatalogError, SECTIONS
from .favorites import favorite_key, sync_source_fields
from .model_profiles import ModelProfileError, normalize_model_profile


class PoolLifecycleError(RuntimeError):
    def __init__(self, message: str, *, code: str = "favorites_unavailable",
                 source_committed: bool = False, recovery_required: bool = False,
                 operation_id: str = ""):
        super().__init__(message)
        self.payload = {"error": message, "code": code, "source_committed": source_committed,
                        "recovery_required": recovery_required, "operation_id": operation_id}


def _digest(body: bytes | None) -> str | None:
    return hashlib.sha256(body).hexdigest() if body is not None else None


def _read(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def _atomic(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix="favorite-transaction-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _copy_store(store: Any) -> Any:
    planned = copy.copy(store)
    planned._lock = asyncio.Lock()
    for name in ("items", "groups", "profiles", "load_warnings"):
        if hasattr(store, name):
            setattr(planned, name, copy.deepcopy(getattr(store, name)))
    planned._write_text = lambda text: None
    if hasattr(store, "catalog"):
        planned.catalog = copy.copy(store.catalog)
        planned.catalog._items = {key: list(items) for key, items in store.catalog._items.items()}
        planned.catalog._by_id = dict(store.catalog._by_id)
    return planned


class PoolLifecycleService:
    def __init__(self, directory: Path, custom: Any, favorites: dict[str, Any]):
        self.directory = Path(directory)
        self.custom, self.favorites = custom, favorites
        self.extension = favorites["anima"].store
        self._lock = asyncio.Lock()

    def _source(self, profile: str) -> Any:
        normalize_model_profile(profile)
        return self.custom

    def _journal(self, profile: str) -> Path:
        return self.directory / f"favorites-lifecycle-{profile}.json"

    @asynccontextmanager
    async def _participants(self):
        async with AsyncExitStack() as stack:
            for lock in (self.favorites["anima"]._lock, self.custom._lock, self.extension._lock):
                await stack.enter_async_context(lock)
            yield

    @staticmethod
    def _trusted(store: Any) -> None:
        store.ensure_writable()
        state = getattr(store, "load_state", "loaded")
        trusted = getattr(store, "load_trusted", getattr(store, "source_trusted", True))
        if not trusted and state != "missing":
            raise PoolLifecycleError("源存储未能完整加载，已暂停词池与收藏修改", code="favorite_source_unavailable")
        if state == "loaded" and not store.path.exists():
            raise PoolLifecycleError("源存储文件已丢失，已暂停词池与收藏修改", code="favorite_source_unavailable")

    @staticmethod
    def _check_missing_source(source: Any, saved: dict[str, Any]) -> None:
        referenced = any(
            bool(item.get("isCustom") and favorite_key(section, item).startswith("custom:"))
            for section, value in saved.items() for item in value["items"]
        )
        if not source.path.exists() and referenced:
            raise PoolLifecycleError("源词库文件缺失，已暂停收藏同步", code="favorite_source_unavailable")

    @staticmethod
    def _validate(profile: str, section: str) -> str:
        try:
            profile = normalize_model_profile(profile)
        except ModelProfileError as error:
            raise CatalogError(str(error)) from error
        if section not in SECTIONS:
            raise CatalogError("收藏分类无效")
        return profile

    def _source_text(self, store: Any, profile: str) -> str:
        return store._serialize(store.groups, store.items, store.source_completeness)

    async def _check_completeness(self, profile: str, section: str, source: Any, planned: Any) -> None:
        if profile != "anima" or source.source_completeness == "complete":
            return
        # A newly missing file could be a fresh installation OR a lost five-pool
        # source. Verify every saved reference before declaring it complete.
        sections = list(SECTIONS)
        saved = await self._read_favorites(profile, sections)
        sources = self._source_items(source, profile)
        for category, value in saved.items():
            for item in value["items"]:
                key = favorite_key(category, item)
                if item.get("isCustom") and key.startswith("custom:"):
                    if key not in sources or sources[key]["section"] != category:
                        raise PoolLifecycleError("源词库完整性尚未确认，请先恢复缺失源文件；原收藏已保留", code="favorite_source_unavailable")
        planned.source_completeness = "complete"

    def _source_items(self, store: Any, profile: str) -> dict[str, dict[str, Any]]:
        return {item["id"]: item for item in store.list()}

    async def _read_favorites(self, profile: str, sections: list[str]) -> dict[str, Any]:
        self._trusted(self.extension)
        try:
            await asyncio.to_thread(self.extension.read_durable)
            return {section: self.extension.get(profile, section) for section in sections}
        except (OSError, TimeoutError, ValueError, TypeError) as error:
            raise PoolLifecycleError(f"本地收藏不可用，词池未修改：{error}") from error

    async def _write_favorites(self, profile: str, section: str, value: dict[str, Any]) -> None:
        profiles = copy.deepcopy(self.extension.profiles)
        profiles.setdefault(profile, {})[section] = copy.deepcopy(value)
        text = self.extension._serialize(profiles)
        await asyncio.to_thread(self.extension._write_text, text)
        self.extension.profiles = profiles
        self.extension._source_text = text
        self.extension.source_trusted = True
        self.extension.load_state = "loaded"

    @staticmethod
    def _patches(section: str, before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
        old = {favorite_key(section, item): item for item in before["items"]}
        new = {favorite_key(section, item): item for item in after["items"]}
        return [{"key": key, "before": old.get(key), "after": new.get(key)}
                for key in dict.fromkeys([*old, *new]) if old.get(key) != new.get(key)]

    async def _save_journal(self, profile: str, journal: dict[str, Any]) -> None:
        await asyncio.to_thread(_atomic, self._journal(profile),
                                json.dumps(journal, ensure_ascii=False).encode("utf-8"))

    async def _recover(self, profile: str) -> None:
        path = self._journal(profile)
        if not path.exists():
            return
        source = self._source(profile)
        journal = {}
        committed = False
        try:
            journal = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(journal, dict) or journal.get("version") != 1 or journal.get("profile") != profile:
                raise ValueError("恢复记录格式无效")
            current_hash = _digest(_read(source.path))
            if current_hash == journal["before_hash"]:
                committed = current_hash == journal["after_hash"] and journal.get("commit_decided", False)
            elif current_hash == journal["after_hash"]:
                committed = True
            else:
                raise ValueError("源文件存在外部变更")
            for section, patches in journal["favorites"].items():
                current = (await self._read_favorites(profile, [section]))[section]
                by_key = {favorite_key(section, item): item for item in current["items"]}
                for patch in patches:
                    actual = by_key.get(patch["key"])
                    target, other = patch["after" if committed else "before"], patch["before" if committed else "after"]
                    if actual == target:
                        continue
                    if actual != other:
                        raise ValueError(f"收藏 {patch['key']} 已被其他操作修改")
                    if target is None:
                        by_key.pop(patch["key"], None)
                    else:
                        if not set(target.get("groupIds", [])).issubset({g["id"] for g in current["groups"]}):
                            raise ValueError("收藏分组在恢复期间发生变化")
                        by_key[patch["key"]] = copy.deepcopy(target)
                desired = {**current, "items": list(by_key.values())}
                if desired != current:
                    await self._write_favorites(profile, section, desired)
                    checked = (await self._read_favorites(profile, [section]))[section]
                    if checked != desired:
                        raise ValueError("收藏恢复写入未确认")
            # A previous process may have committed the source before losing its
            # response. Reload only when disk and this process disagree.
            if committed and _digest(self._source_text(source, profile).encode()) != journal["after_hash"]:
                source.reload()
                self._trusted(source)
            await asyncio.to_thread(path.unlink)
        except Exception as error:
            raise PoolLifecycleError(f"收藏一致性恢复尚未完成，请重试：{error}",
                                     code="favorite_sync_recovery_required", source_committed=committed,
                                     recovery_required=True, operation_id=journal.get("id", "") if isinstance(journal, dict) else "") from error

    async def _commit(self, profile: str, planned: Any, before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        source = self._source(profile)
        old_items, new_items = self._source_items(source, profile), self._source_items(planned, profile)
        deleted = [key for key in old_items if key not in new_items]
        updated = [key for key in new_items if old_items.get(key) != new_items[key]]
        text = self._source_text(planned, profile)
        changed_source = self._source_text(source, profile) != text
        changes = {section: self._patches(section, before[section], value) for section, value in after.items()}
        changes = {section: patches for section, patches in changes.items() if patches}
        stats = {"state": "complete", "updatedCount": sum(p["after"] is not None for ps in changes.values() for p in ps),
                 "deletedCount": sum(p["after"] is None for ps in changes.values() for p in ps)}
        if not changed_source and not changes:
            return {"updatedItemIds": [], "deletedItemIds": [], "favoritesSync": stats}
        source.ensure_writable()
        try:
            disk_before = _read(source.path)
            if _digest(disk_before) != getattr(source, "disk_digest", _digest(disk_before)):
                raise PoolLifecycleError("源词库已被外部修改，请重新加载后重试", code="favorite_source_changed")
            journal = {"version": 1, "id": uuid.uuid4().hex, "profile": profile,
                       "before_hash": _digest(disk_before),
                       "after_hash": _digest(text.encode("utf-8")) if changed_source else _digest(disk_before),
                       "favorites": changes, "commit_decided": False}
            await self._save_journal(profile, journal)
        except (OSError, TimeoutError) as error:
            # Even a journal write can finish before its caller reports failure.
            # Resolve that evidence now; never leave a rejected action queued.
            if self._journal(profile).exists():
                await self._recover(profile)
            raise PoolLifecycleError(f"操作准备失败，词池与收藏未修改：{error}",
                                     code="favorite_sync_prepare_failed") from error
        try:
            for section in changes:
                if (await self._read_favorites(profile, [section]))[section] != before[section]:
                    raise ValueError("收藏在准备期间发生外部变更")
                try:
                    await self._write_favorites(profile, section, after[section])
                except (OSError, TimeoutError):
                    # A lost response is not proof the write failed.
                    if (await self._read_favorites(profile, [section]))[section] != after[section]:
                        raise
                if (await self._read_favorites(profile, [section]))[section] != after[section]:
                    raise ValueError("收藏写入未确认")
            if _read(source.path) != disk_before:
                raise ValueError("源文件在提交期间发生外部变更")
            journal["commit_decided"] = True
            await self._save_journal(profile, journal)
            if changed_source:
                await asyncio.to_thread(source._write_text, text)
                source.items, source.groups = copy.deepcopy(planned.items), copy.deepcopy(planned.groups)
                if profile == "anima":
                    source.source_completeness = planned.source_completeness
                    source.catalog.set_custom_items(source.items)
                source.load_trusted = True
                source.load_state = "loaded"
            await asyncio.to_thread(self._journal(profile).unlink)
        except Exception as error:
            await self._recover(profile)
            if _digest(_read(source.path)) == journal["after_hash"] and (changed_source or journal["commit_decided"]):
                return {"updatedItemIds": updated, "deletedItemIds": deleted, "favoritesSync": stats}
            raise PoolLifecycleError(f"操作未提交，词池与收藏已恢复：{error}", code="favorite_sync_failed") from error
        return {"updatedItemIds": updated, "deletedItemIds": deleted, "favoritesSync": stats}



    def _sync(self, profile: str, source: Any, planned: Any, saved: dict[str, Any], *, reconcile: bool = False) -> None:
        old, new = self._source_items(source, profile), self._source_items(planned, profile)
        for section, value in saved.items():
            retained = []
            for entry in value["items"]:
                key = favorite_key(section, entry)
                owned = entry.get("isCustom") and str(key).startswith("custom:")
                if not owned:
                    retained.append(entry)
                    continue
                item = new.get(key)
                if item is None or (profile == "anima" and item["section"] != section):
                    continue
                sync_source_fields(entry, item, None if reconcile else old.get(key), prompt=False)
                retained.append(entry)
            value["items"] = retained

    async def _plan_favorite(self, profile: str, planned: Any, saved: dict[str, Any], section: str, payload: dict[str, Any]) -> dict[str, Any]:
        service = copy.copy(self.favorites[profile])
        service._lock = asyncio.Lock()
        service.catalog = planned.catalog
        service.expression_store = _copy_store(self.extension)

        async def read(_section):
            return copy.deepcopy(saved)

        async def write(_section, value):
            saved[_section] = copy.deepcopy(value)

        service._read_payload, service._save_section = read, write
        return await service.save_text(section, payload, planned)

    @serialized_mutation
    async def source(self, profile: str, section: str, method: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        profile = self._validate(profile, section)
        async with self._participants():
            await self._recover(profile)
            source = self._source(profile)
            self._trusted(source)
            planned = _copy_store(source)
            await self._check_completeness(profile, section, source, planned)
            before: dict[str, Any] = {}
            after: dict[str, Any] = {}
            result = await getattr(planned, method)(*args, **kwargs)
            if result is False:
                raise KeyError(args[0] if args else "")
            if not isinstance(result, dict):
                result = {"deleted": bool(result)}
            if profile == "anima":
                old, new = self._source_items(source, profile), self._source_items(planned, profile)
                sections = {item["section"] for key, item in old.items() if new.get(key) != item}
                sections.update(new[key]["section"] for key in old if key in new and new[key] != old[key])
                sections.add(section)
                before = await self._read_favorites(profile, sorted(sections))
                self._check_missing_source(source, before)
                after = copy.deepcopy(before)
            self._sync(profile, source, planned, after)
            meta = await self._commit(profile, planned, before, after)
            return {**result, "model_profile": profile, "section": section, **meta}

    @serialized_mutation
    async def save_text(self, profile: str, section: str, payload: dict[str, Any]) -> dict[str, Any]:
        profile = self._validate(profile, section)
        async with self._participants():
            await self._recover(profile)
            source = self._source(profile)
            self._trusted(source)
            planned = _copy_store(source)
            await self._check_completeness(profile, section, source, planned)
            before = await self._read_favorites(profile, [section])
            self._check_missing_source(source, before)
            after = copy.deepcopy(before)
            result = await self._plan_favorite(profile, planned, after, section, payload)
            meta = await self._commit(profile, planned, before, after)
            return {**result, "section": section, **meta}

    async def _reconcile_locked(self, profile: str, section: str) -> dict[str, Any]:
        await self._recover(profile)
        source = self._source(profile)
        self._trusted(source)
        before = await self._read_favorites(profile, [section])
        # Missing source files are not evidence of deletion. An entirely new
        # installation has no favorites and is safe to initialize.
        self._check_missing_source(source, before)
        planned, after = _copy_store(source), copy.deepcopy(before)
        await self._check_completeness(profile, section, source, planned)
        self._sync(profile, source, planned, after, reconcile=True)
        meta = await self._commit(profile, planned, before, after)
        return {"model_profile": profile, "section": section, **meta}

    @serialized_mutation
    async def reconcile(self, profile: str, section: str) -> dict[str, Any]:
        profile = self._validate(profile, section)
        async with self._participants():
            return await self._reconcile_locked(profile, section)

    @serialized_mutation
    async def favorite(self, profile: str, section: str, method: str, *args: Any, **kwargs: Any) -> Any:
        profile = normalize_model_profile(profile)
        service = self.favorites[profile]
        service._section(section)
        async with self._participants():
            await self._recover(profile)
            await self._read_favorites(profile, [section])
        # The coordinator still excludes source mutations; let the existing
        # service acquire its own locks for ordinary favorite-only writes.
        return await getattr(service, method)(section, *args, **kwargs)

    @serialized_mutation
    async def recover(self, profile: str) -> None:
        profile = normalize_model_profile(profile)
        async with self._participants():
            await self._recover(profile)

    @serialized_mutation
    async def recover_pending(self) -> dict[str, Any]:
        failures = {}
        async with self._participants():
            for profile in self.favorites:
                if not self._journal(profile).exists():
                    continue
                try:
                    await self._recover(profile)
                except PoolLifecycleError as error:
                    failures[profile] = error.payload
        return failures

    async def ensure_ready(self, profile: str) -> None:
        if self._journal(profile).exists():
            await self.recover(profile)
