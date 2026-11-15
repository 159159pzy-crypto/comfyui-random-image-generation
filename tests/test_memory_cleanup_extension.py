import asyncio
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
import pytest

from anima_webui.comfy_extension import atelier_resources
from anima_webui.comfy_extension.atelier_resources import memory_cleanup


def prompt_queue():
    return SimpleNamespace(mutex=threading.RLock(), queue=[], currently_running={})


def cleanup_routes(monkeypatch, queue, empty_cache):
    routes = web.RouteTableDef()
    server = SimpleNamespace(instance=SimpleNamespace(routes=routes, prompt_queue=queue))
    monkeypatch.setitem(sys.modules, "folder_paths", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "nodes", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "server", SimpleNamespace(PromptServer=server))
    monkeypatch.setitem(sys.modules, "comfy", SimpleNamespace(
        model_management=SimpleNamespace(soft_empty_cache=empty_cache)))
    atelier_resources.register_routes()
    return routes


async def post_cleanup(routes):
    app = web.Application()
    app.add_routes(routes)
    async with TestClient(TestServer(app)) as client:
        async with client.post("/anima/memory/cleanup") as response:
            return response.status, await response.json()


def test_cleanup_completes_once_in_normal_stage_order(monkeypatch):
    queue = prompt_queue()
    calls = []
    cache = object()
    queue.execution_cache = cache
    queue.unload_all_models = Mock(side_effect=AssertionError("must retain models"))
    queue.reset = Mock(side_effect=AssertionError("must retain execution cache"))
    monkeypatch.setattr(memory_cleanup.gc, "collect", lambda: calls.append("gc"))
    routes = cleanup_routes(monkeypatch, queue, lambda: calls.append("empty_cache"))

    assert asyncio.run(post_cleanup(routes)) == (200, {"status": "completed"})
    assert calls == ["empty_cache", "gc"]
    assert queue.execution_cache is cache
    queue.unload_all_models.assert_not_called()
    queue.reset.assert_not_called()


@pytest.mark.parametrize("busy_queue", ["queue", "currently_running"])
def test_busy_running_or_pending_queue_never_cleans(monkeypatch, busy_queue):
    queue = prompt_queue()
    if busy_queue == "queue":
        queue.queue.append("other-prompt")
    else:
        queue.currently_running[0] = "other-prompt"
    empty_cache = Mock()
    collect = Mock()
    monkeypatch.setattr(memory_cleanup.gc, "collect", collect)
    routes = cleanup_routes(monkeypatch, queue, empty_cache)

    status, _ = asyncio.run(post_cleanup(routes))
    assert status == 409
    empty_cache.assert_not_called()
    collect.assert_not_called()
    assert queue.mutex.acquire(blocking=False)
    queue.mutex.release()


def test_busy_mutex_never_waits_or_cleans(monkeypatch):
    queue = prompt_queue()
    empty_cache = Mock()
    collect = Mock()
    monkeypatch.setattr(memory_cleanup.gc, "collect", collect)
    routes = cleanup_routes(monkeypatch, queue, empty_cache)

    with queue.mutex:
        assert asyncio.run(post_cleanup(routes))[0] == 409
    empty_cache.assert_not_called()
    collect.assert_not_called()


@pytest.mark.parametrize("queue", [
    SimpleNamespace(),
    SimpleNamespace(mutex=object()),
    SimpleNamespace(mutex=SimpleNamespace(acquire=lambda: True, release=lambda: None)),
    SimpleNamespace(mutex=threading.RLock(), queue=None, currently_running={}),
    SimpleNamespace(mutex=threading.RLock(), queue=[], currently_running=None),
])
def test_unknown_queue_interface_is_unsupported(monkeypatch, queue):
    empty_cache = Mock()
    collect = Mock()
    monkeypatch.setattr(memory_cleanup.gc, "collect", collect)
    routes = cleanup_routes(monkeypatch, queue, empty_cache)

    assert asyncio.run(post_cleanup(routes))[0] == 501
    empty_cache.assert_not_called()
    collect.assert_not_called()
    if isinstance(getattr(queue, "mutex", None), type(threading.RLock())):
        assert queue.mutex.acquire(blocking=False)
        queue.mutex.release()


@pytest.mark.parametrize("failing_action", ["empty_cache", "gc"])
def test_cleanup_error_releases_lock_and_sanitizes_response(monkeypatch, caplog, failing_action):
    queue = prompt_queue()
    empty_cache = Mock()
    collect = Mock()
    action = empty_cache if failing_action == "empty_cache" else collect
    action.side_effect = RuntimeError("sensitive internal detail")
    monkeypatch.setattr(memory_cleanup.gc, "collect", collect)
    routes = cleanup_routes(monkeypatch, queue, empty_cache)

    status, data = asyncio.run(post_cleanup(routes))
    assert status == 500
    assert "sensitive" not in json.dumps(data)
    assert "sensitive internal detail" in caplog.text
    assert queue.mutex.acquire(blocking=False)
    queue.mutex.release()
    if failing_action == "empty_cache":
        collect.assert_not_called()


def test_external_enqueue_cannot_pass_cleanup(monkeypatch):
    queue = prompt_queue()
    cleaning = threading.Event()
    allow_finish = threading.Event()
    enqueue_attempted = threading.Event()
    enqueued = threading.Event()
    calls = []

    def empty_cache():
        calls.append("empty_cache")
        cleaning.set()
        assert allow_finish.wait(3)

    def enqueue():
        enqueue_attempted.set()
        with queue.mutex:
            queue.queue.append("next-prompt")
            calls.append("enqueue")
            enqueued.set()

    monkeypatch.setattr(memory_cleanup.gc, "collect", lambda: calls.append("gc"))
    with ThreadPoolExecutor(max_workers=2) as executor:
        cleanup = executor.submit(memory_cleanup.cleanup_if_idle, queue, empty_cache)
        try:
            assert cleaning.wait(3)
            assert not queue.mutex.acquire(blocking=False)
            enqueue_task = executor.submit(enqueue)
            assert enqueue_attempted.wait(3)
            assert not enqueued.is_set()
        finally:
            allow_finish.set()
        assert cleanup.result(timeout=3) is True
        enqueue_task.result(timeout=3)
    assert calls == ["empty_cache", "gc", "enqueue"]


def test_cancelled_request_keeps_thread_lock_until_cleanup_finishes(monkeypatch):
    queue = prompt_queue()
    cleaning = threading.Event()
    allow_finish = threading.Event()
    collected = threading.Event()

    def empty_cache():
        cleaning.set()
        assert allow_finish.wait(3)

    monkeypatch.setattr(memory_cleanup.gc, "collect", collected.set)
    routes = cleanup_routes(monkeypatch, queue, empty_cache)
    handler = next(route.handler for route in routes if route.path == "/anima/memory/cleanup")

    async def check():
        request_task = asyncio.create_task(handler(None))
        try:
            assert await asyncio.to_thread(cleaning.wait, 3)
            request_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request_task
            assert not queue.mutex.acquire(blocking=False)
            assert not collected.is_set()
        finally:
            allow_finish.set()

    asyncio.run(check())
    assert collected.is_set()
    assert queue.mutex.acquire(blocking=False)
    queue.mutex.release()
