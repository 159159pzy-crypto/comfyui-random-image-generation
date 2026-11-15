"""Run the normal lightweight cleanup while preventing new prompt dispatch."""
from __future__ import annotations

from collections.abc import Callable
import gc
from typing import Any


class CleanupUnavailable(Exception):
    """The queue cannot safely exclude generation while memory is cleaned."""


def cleanup_if_idle(queue: Any, empty_cache: Callable[[], None]) -> bool:
    mutex = getattr(queue, "mutex", None)
    acquire = getattr(mutex, "acquire", None)
    release = getattr(mutex, "release", None)
    if not callable(acquire) or not callable(release):
        raise CleanupUnavailable
    try:
        acquired = acquire(blocking=False)
    except TypeError as error:
        raise CleanupUnavailable from error
    if not acquired:
        return False
    try:
        pending = getattr(queue, "queue", None)
        running = getattr(queue, "currently_running", None)
        if not isinstance(pending, list) or not isinstance(running, dict):
            raise CleanupUnavailable
        if pending or running:
            return False
        # Keep ownership in this thread even if the HTTP request is cancelled.
        # Both enqueue and dispatch use this mutex; no prompt/cache is created.
        empty_cache()
        gc.collect()
        return True
    finally:
        release()
