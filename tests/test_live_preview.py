"""Real websocket framing and preview ownership, without GPU generation."""
from __future__ import annotations

import asyncio
import json
import struct
import tempfile
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from anima_webui.comfy import ComfyClient
from anima_webui.history import HistoryStore
from anima_webui.runner import BatchManager
from anima_webui.server import MANAGER_KEY, create_app
from anima_webui.workflow import DEFAULT_SETTINGS
from test_runner import FakeComfy, FakeTemplates
from test_server import APP_DIR, FakeComfy as ServerComfy, _write_fake_tools


def metadata_frame(**overrides):
    metadata = {"prompt_id": "prompt-1", "node_id": "sampler", "image_type": "image/jpeg", **overrides}
    encoded = json.dumps(metadata).encode()
    return struct.pack(">II", 4, len(encoded)) + encoded + b"image-data"


def event(kind="progress", prompt="prompt-1", node="sampler", **values):
    return {"kind": "event", "payload": {"type": kind, "data": {"prompt_id": prompt, "node": node, **values}}}


def frame(prompt="prompt-1", node="sampler", **values):
    return {"kind": "preview", "format": "jpeg", "bytes": b"frame", "prompt_id": prompt, "node_id": node, **values}


class PreviewProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.frames = []
        self.flags = None

        async def socket(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            self.flags = await ws.receive_json()
            for packet in self.frames:
                if isinstance(packet, str):
                    await ws.send_str(packet)
                else:
                    await ws.send_bytes(packet)
            await ws.close()
            return ws

        app = web.Application()
        app.router.add_get("/ws", socket)
        self.server = TestServer(app)
        await self.server.start_server()
        self.client = ComfyClient(f"http://127.0.0.1:{self.server.port}")

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.close()

    async def test_negotiates_metadata_and_reads_both_binary_protocols(self):
        self.frames = [struct.pack(">II", 1, 1) + b"old-jpeg", struct.pack(">II", 1, 2) + b"old-png",
                       metadata_frame(), metadata_frame(image_type="image/png", node_id="second", display_node_id="parent")]
        received = [item async for item in self.client.progress_stream("test-client")]
        self.assertEqual(self.flags, {"type": "feature_flags", "data": {"supports_preview_metadata": True}})
        self.assertEqual([item["format"] for item in received], ["jpeg", "png", "jpeg", "png"])
        self.assertNotIn("prompt_id", received[0])
        self.assertEqual(received[2]["prompt_id"], "prompt-1")
        self.assertEqual(received[3]["display_node_id"], "parent")
        self.assertEqual(received[3]["bytes"], b"image-data")

    async def test_malformed_packets_do_not_break_subsequent_previews(self):
        self.frames = [b"tiny", struct.pack(">II", 1, 8) + b"bad-format", struct.pack(">II", 4, 99999) + b"short",
                       struct.pack(">II", 4, 1) + b"{image", struct.pack(">II", 4, 1) + b"\xffimage",
                       struct.pack(">II", 4, 2) + b"[]image", metadata_frame(prompt_id=None), metadata_frame(node_id=""),
                       metadata_frame(image_type="text/html"), "not-json", "[]", metadata_frame()]
        received = [item async for item in self.client.progress_stream("test-client")]
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["bytes"], b"image-data")

    async def test_metadata_before_progress_matches_comfyui_emission_order(self):
        manager = BatchManager(FakeTemplates(), None, FakeComfy())
        manager.state = {"id": "batch-1", "prompt_id": "prompt-1", "kind": "generation", "stage": "generating", "status": "running"}
        self.frames = [json.dumps(event("executing")["payload"]), metadata_frame(),
                       json.dumps(event(value=1, max=30)["payload"])]
        async for item in self.client.progress_stream("test-client"):
            manager._consume_progress(item)
            if item["kind"] == "preview":
                self.assertIsNotNone(manager.preview)
                self.assertNotIn("progress", manager.state)
        self.assertEqual(manager.preview[2], b"image-data")
        self.assertEqual(manager.state["progress"], {"value": 1, "max": 30})


class PreviewOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.history = HistoryStore(Path(self.temp.name) / "history.sqlite3")
        self.manager = BatchManager(FakeTemplates(), self.history, FakeComfy())
        self.manager.state = {"id": "batch-1", "prompt_id": "prompt-1", "kind": "generation", "stage": "generating", "status": "running"}

    async def asyncTearDown(self):
        self.manager._stop_monitor()
        if self.manager.task and not self.manager.task.done():
            self.manager.task.cancel()
            await asyncio.gather(self.manager.task, return_exceptions=True)
        await self.history.close()
        self.temp.cleanup()

    async def test_metadata_rejects_other_prompt_and_late_previous_node(self):
        manager = self.manager
        manager._consume_progress(event("executing"))
        manager._consume_progress(frame(prompt="foreign"))
        self.assertIsNone(manager.preview)
        manager._consume_progress(frame())
        self.assertEqual(manager.preview_metadata, {"batch_id": "batch-1", "prompt_id": "prompt-1", "node_id": "sampler", "epoch": 1})
        manager._consume_progress(event("progress", value=12, max=30))
        manager._consume_progress(event("executing", node="second"))
        self.assertNotIn("progress", manager.state)
        manager._consume_progress(frame(bytes=b"late-first-node"))
        self.assertIsNone(manager.preview)
        manager._consume_progress(frame(node="second", bytes=b"second-frame"))
        self.assertEqual(manager.state["preview_node"], "second")
        self.assertEqual(manager.preview[2], b"second-frame")
        manager._consume_progress(event("executing", node=None))
        manager._consume_progress(frame(node="second", bytes=b"after-finish"))
        self.assertIsNone(manager.preview)

    async def test_late_progress_cannot_move_back_after_a_stage_transition(self):
        manager = self.manager
        for node in ("701:1245", "701:1246", "704:1248", "1253:191"):
            previous = manager.state.get("progress_node")
            manager._consume_progress(event("executing", node=node))
            epoch = manager.state["preview_epoch"]
            manager._consume_progress(frame(node=node, bytes=node.encode()))
            if previous:
                manager._consume_progress(event(node=previous, value=30, max=30))
                manager._consume_progress(frame(node=previous, bytes=b"late-old-stage"))
            self.assertEqual(manager.state["preview_epoch"], epoch)
            self.assertEqual(manager.state["progress_node"], node)
            self.assertEqual(manager.preview[2], node.encode())
        manager._consume_progress(event("executing", node=None))
        manager._consume_progress(event(node=node, value=30, max=30))
        manager._consume_progress(frame(node=node))
        self.assertIsNone(manager.preview)
        self.assertNotIn("progress", manager.state)

    async def test_same_node_reentry_advances_epoch_and_preserves_metadata_aliases(self):
        manager = self.manager
        manager._consume_progress(event("executing", node="parent"))
        manager._consume_progress(frame(node="child", parent_node_id="parent"))
        first_epoch = manager.preview_metadata["epoch"]
        manager._consume_progress(event("executing", node="parent"))
        self.assertIsNone(manager.preview)
        self.assertGreater(manager.state["preview_epoch"], first_epoch)
        manager._consume_progress(frame(node="child", display_node_id="parent", bytes=b"reentry"))
        self.assertEqual(manager.preview[2], b"reentry")
        self.assertEqual(manager.preview_metadata["epoch"], manager.state["preview_epoch"])

    async def test_legacy_frames_need_identified_current_execution(self):
        manager = self.manager
        legacy = {"kind": "preview", "format": "png", "bytes": b"legacy"}
        manager._consume_progress(legacy)
        self.assertIsNone(manager.preview)
        manager._consume_progress(event(prompt=None, value=1, max=30))
        manager._consume_progress(legacy)
        self.assertIsNone(manager.preview)
        manager._consume_progress(event(value=2, max=30))
        manager._consume_progress(legacy)
        self.assertEqual(manager.preview[1:], ("image/png", b"legacy"))
        manager._consume_progress(event("executing", prompt="other"))
        manager._consume_progress({**legacy, "bytes": b"foreign"})
        self.assertEqual(manager.preview[2], b"legacy")
        manager._clear_preview()
        manager.state.update(id="batch-2", prompt_id="prompt-2")
        manager._consume_progress(legacy)
        manager._consume_progress(frame())
        self.assertIsNone(manager.preview)

    async def test_prompt_processing_terminal_and_invalid_events_never_create_frames(self):
        manager = self.manager
        for changes in ({"stage": "prompt_processing"}, {"stage": "generating", "status": "completed"},
                        {"status": "running", "kind": "prompt_preview"}, {"kind": "generation", "prompt_id": ""}):
            manager.state.update(changes)
            manager._consume_progress(frame())
            self.assertIsNone(manager.preview)
        manager.state["prompt_id"] = "prompt-1"
        for invalid in (event(value="bad", max=30), event(value=1, max=0), event(value=-1, max=30),
                        {"kind": "event", "payload": []}, {"kind": "event", "payload": {"type": "progress", "data": []}}):
            manager._consume_progress(invalid)
        self.assertNotIn("progress", manager.state)
        manager._consume_progress(frame(bytes=b""))
        self.assertIsNone(manager.preview)

    async def test_metadata_works_after_reconnect_before_next_text_progress(self):
        self.manager._consume_progress(frame())
        self.assertEqual(self.manager.preview[2], b"frame")
        epoch = self.manager.state["preview_epoch"]
        self.manager._reset_preview_connection()
        self.assertGreater(self.manager.state["preview_epoch"], epoch)
        self.manager._consume_progress(event("executing", prompt=None, node="second"))
        self.manager._consume_progress({"kind": "preview", "bytes": b"unowned", "format": "jpeg"})
        self.assertIsNone(self.manager.preview)
        self.manager._consume_progress(frame(node="second", bytes=b"reconnected"))
        self.assertEqual(self.manager.preview[2], b"reconnected")
        self.manager._consume_progress(event(node="sampler", value=30, max=30))
        self.assertNotIn("progress", self.manager.state)
        self.manager._consume_progress(event(node="second", value=1, max=30))
        self.assertEqual(self.manager.state["progress_node"], "second")
        self.manager._consume_progress(event("executing", node=None))
        self.manager._reset_preview_connection()
        self.manager._consume_progress(frame(node="second", bytes=b"after-finish"))
        self.assertIsNone(self.manager.preview)

    async def test_websocket_failure_does_not_stop_history_completion(self):
        class DisconnectedComfy(FakeComfy):
            async def progress_stream(inner, client_id):
                raise ConnectionError("preview unavailable")
                yield  # pragma: no cover - async generator protocol

        comfy = DisconnectedComfy()
        comfy.block = asyncio.Event()
        self.manager.comfy = comfy
        await self.manager.start({**DEFAULT_SETTINGS, "count": 1})
        for _ in range(100):
            if self.manager.state.get("preview_status") == "disconnected":
                break
            await asyncio.sleep(.005)
        self.assertEqual(self.manager.state["preview_status"], "disconnected")
        comfy.block.set()
        result = await self.manager.wait()
        self.assertEqual((result["status"], result["completed"]), ("completed", 1))
        self.assertIsNone(self.manager.preview)
        self.assertNotIn("preview_id", result)

    async def test_next_image_and_failed_batch_clear_prior_frame_ownership(self):
        gates = [asyncio.Event(), asyncio.Event()]

        class GatedComfy(FakeComfy):
            async def wait_for_history(inner, prompt_id, **kwargs):
                await gates[len(inner.submissions) - 1].wait()
                return await super().wait_for_history(prompt_id, **kwargs)

        comfy = GatedComfy()
        comfy.error_at = 2
        self.manager.comfy = comfy
        await self.manager.start({**DEFAULT_SETTINGS, "count": 2})
        for _ in range(100):
            if self.manager.state.get("prompt_id") == "prompt-1":
                break
            await asyncio.sleep(.005)
        self.manager._consume_progress(frame())
        self.assertIsNotNone(self.manager.preview)
        gates[0].set()
        for _ in range(100):
            if self.manager.state.get("prompt_id") == "prompt-2":
                break
            await asyncio.sleep(.005)
        self.assertEqual(self.manager.state["prompt_id"], "prompt-2")
        self.assertIsNone(self.manager.preview)
        self.assertNotIn("preview_id", self.manager.state)
        self.manager._consume_progress(frame())
        self.assertIsNone(self.manager.preview)
        self.manager._consume_progress(frame(prompt="prompt-2"))
        self.assertIsNotNone(self.manager.preview)
        gates[1].set()
        result = await self.manager.wait()
        self.assertEqual((result["status"], result["completed"]), ("error", 1))
        self.assertIsNone(self.manager.preview)
        self.assertIsNone(self.manager.preview_metadata)

    async def test_stop_clears_preview_and_sampling_progress(self):
        comfy = self.manager.comfy
        comfy.block = asyncio.Event()
        comfy.honor_abort = True
        await self.manager.start({**DEFAULT_SETTINGS, "count": 1})
        for _ in range(100):
            if self.manager.state.get("prompt_id"):
                break
            await asyncio.sleep(.005)
        self.manager._consume_progress(event(value=1, max=30))
        self.manager._consume_progress(frame())
        self.assertIsNotNone(self.manager.preview)
        await self.manager.request_stop(self.manager.state["id"])
        result = await self.manager.wait()
        self.assertEqual(result["status"], "stopped")
        self.assertIsNone(self.manager.preview)
        self.assertNotIn("progress", result)


class PreviewEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        directory = Path(self.temp.name)
        app = create_app(app_dir=APP_DIR, data_dir=directory / "data", comfy=ServerComfy(),
                         history_path=directory / "history.sqlite3", anima_tools_dir=_write_fake_tools(directory))
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.manager = self.client.server.app[MANAGER_KEY]
        self.manager.state = {"id": "batch-1", "prompt_id": "prompt-1", "kind": "generation", "stage": "generating", "status": "running"}

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def test_missing_and_mismatched_ownership_return_uncached_204(self):
        response = await self.client.get("/api/batches/current/preview?batch_id=batch-1")
        self.assertEqual(response.status, 204)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.manager._consume_progress(frame())
        for identity in ("batch_id=batch-old", "prompt_id=prompt-old", "node_id=other", "batch_id=", "epoch=99", "epoch=invalid"):
            response = await self.client.get(f"/api/batches/current/preview?{identity}")
            self.assertEqual(response.status, 204)
        response = await self.client.get("/api/batches/current/preview?batch_id=batch-1&prompt_id=prompt-1&node_id=sampler&v=0")
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.read(), b"frame")
        self.assertEqual(response.headers["Content-Type"], "image/jpeg")
        self.assertEqual(response.headers["X-Preview-Prompt-Id"], "prompt-1")
        self.assertEqual(response.headers["X-Preview-Id"], "1")
        self.assertEqual(response.headers["X-Preview-Epoch"], "0")
        self.manager.state["prompt_id"] = "prompt-2"
        self.assertEqual((await self.client.get("/api/batches/current/preview")).status, 204)

    async def test_completed_batch_cannot_serve_a_stale_frame(self):
        self.manager._consume_progress(frame())
        self.manager.state["status"] = "completed"
        self.assertEqual((await self.client.get("/api/batches/current/preview")).status, 204)

    async def test_epoch_rejects_old_reads_when_the_same_node_executes_again(self):
        manager = self.manager
        manager._consume_progress(event("executing"))
        manager._consume_progress(frame(bytes=b"first-execution"))
        old_epoch = manager.state["preview_epoch"]
        query = "/api/batches/current/preview?batch_id=batch-1&prompt_id=prompt-1&node_id=sampler"
        self.assertEqual((await self.client.get(f"{query}&epoch={old_epoch}")).status, 200)
        manager._consume_progress(event("executing"))
        self.assertEqual((await self.client.get(query)).status, 204)
        manager._consume_progress(frame(bytes=b"second-execution"))
        stale = await self.client.get(f"{query}&epoch={old_epoch}")
        self.assertEqual(stale.status, 204)
        self.assertEqual(stale.headers["Cache-Control"], "no-store")
        current = await self.client.get(f"{query}&epoch={manager.state['preview_epoch']}")
        self.assertEqual(await current.read(), b"second-execution")
        self.assertEqual(current.headers["X-Preview-Epoch"], str(manager.state["preview_epoch"]))
