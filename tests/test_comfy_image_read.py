"""Thumbnail source limits stop downloads without changing original-image reads."""
from __future__ import annotations

import asyncio
import unittest

from aiohttp import web
from aiohttp.test_utils import TestServer

from anima_webui.comfy import ComfyClient, ComfyError


class BoundedImageReadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mode = "normal"
        self.body = b"test image bytes" * 16
        self.release = asyncio.Event()
        self.queries = []

        async def view(request):
            self.queries.append(dict(request.query))
            if self.mode == "normal":
                return web.Response(body=self.body, content_type="image/webp")
            if self.mode == "error":
                return web.Response(status=404)
            headers = {"Content-Type": "image/png"}
            if self.mode == "declared":
                headers["Content-Length"] = "1000000"
            response = web.StreamResponse(headers=headers)
            await response.prepare(request)
            if self.mode == "chunked":
                await response.write(b"x" * 17)
                await response.write(b"y" * 17)
            # The client must fail before the server finishes sending the body.
            await self.release.wait()
            return response

        app = web.Application()
        app.router.add_get("/view", view)
        self.server = TestServer(app)
        await self.server.start_server()
        self.client = ComfyClient(str(self.server.make_url("")))
        self.record = {"filename": "作品 #1.png", "subfolder": "output folder", "file_type": "output"}

    async def asyncTearDown(self):
        self.release.set()
        await self.client.close()
        await self.server.close()

    async def test_declared_oversize_stops_before_waiting_for_body(self):
        self.mode = "declared"
        with self.assertRaisesRegex(ComfyError, "大小限制"):
            await asyncio.wait_for(self.client.image_bytes(self.record, max_bytes=32), 2)

    async def test_chunked_oversize_stops_before_stream_finishes(self):
        self.mode = "chunked"
        with self.assertRaisesRegex(ComfyError, "大小限制"):
            await asyncio.wait_for(self.client.image_bytes(self.record, max_bytes=32), 2)

    async def test_exact_boundary_and_original_read_keep_bytes_type_and_identity(self):
        limited = await self.client.image_bytes(self.record, max_bytes=len(self.body))
        original = await self.client.image_bytes(self.record)
        self.assertEqual(limited, (self.body, "image/webp"))
        self.assertEqual(original, limited)
        self.assertEqual(self.queries, [
            {"filename": "作品 #1.png", "subfolder": "output folder", "type": "output"},
        ] * 2)

    async def test_original_read_has_no_thumbnail_size_limit(self):
        with self.assertRaisesRegex(ComfyError, "大小限制"):
            await self.client.image_bytes(self.record, max_bytes=32)
        self.assertEqual((await self.client.image_bytes(self.record))[0], self.body)

    async def test_http_errors_remain_comfy_errors(self):
        self.mode = "error"
        with self.assertRaisesRegex(ComfyError, "HTTP 404"):
            await self.client.image_bytes(self.record, max_bytes=32)
