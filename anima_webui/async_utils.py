"""Cancellation boundaries for durable, serialized mutations."""
from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any


logger = logging.getLogger(__name__)


def serialized_mutation(method: Any) -> Any:
    """Hold the store lock until a started mutation has completely finished.

    Waiting for the lock is cancellable. Once a mutation starts, cancelling its
    caller waits for its disk write and memory update (or rollback) to finish
    before propagating cancellation. Shielding only to_thread is insufficient:
    it would release the lock and skip the following memory update.
    """
    @functools.wraps(method)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        async with self._lock:
            task = asyncio.create_task(method(self, *args, **kwargs))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                # A caller may be cancelled repeatedly during shutdown.
                # Keep the lock until the owned task really finishes.
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not task.cancelled():
                    try:
                        task.result()
                    except Exception:
                        logger.exception("存储变更在请求取消后失败: %s", method.__qualname__)
                raise

    return wrapper
