from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from typing import Any

from .v7_store import V7Store


class StudioEventBus:
    """Persistent fan-out bus with resumable cursors for browser SSE clients."""

    def __init__(self, store: V7Store, *, queue_size: int = 256) -> None:
        self.store = store
        self.queue_size = max(8, queue_size)
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()
        self._closed = False

    async def publish(
        self,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        workspace: str = "studio",
        entity_id: str = "",
    ) -> dict[str, Any]:
        event = await asyncio.to_thread(
            self.store.append_event,
            event_type,
            payload,
            source_workspace=workspace,
            entity_id=entity_id,
        )
        async with self._lock:
            if self._closed:
                return event
            for queue in tuple(self._subscribers):
                if queue.full():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                queue.put_nowait(event)
        return event

    async def read(self, *, after_id: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self.store.read_events,
            after_id=after_id,
            limit=limit,
        )

    async def stream(
        self,
        *,
        after_id: int = 0,
        heartbeat_seconds: float = 15.0,
    ) -> AsyncIterator[dict[str, Any] | None]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self.queue_size)
        async with self._lock:
            if self._closed:
                return
            self._subscribers.add(queue)
            backlog = await self.read(after_id=after_id, limit=2000)
        cursor = after_id
        try:
            for event in backlog:
                cursor = max(cursor, int(event["id"]))
                yield event
            while not self._closed:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=max(1.0, heartbeat_seconds)
                    )
                except asyncio.TimeoutError:
                    yield None
                    continue
                event_id = int(event["id"])
                if event_id <= cursor:
                    continue
                cursor = event_id
                yield event
        finally:
            async with self._lock:
                self._subscribers.discard(queue)

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            self._subscribers.clear()
