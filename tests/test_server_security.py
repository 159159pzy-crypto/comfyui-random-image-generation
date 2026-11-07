"""Public HTTP boundaries must fail safely before reaching mutable services."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aiohttp.test_utils import TestClient, TestServer

from anima_webui.http_security import MAX_JSON_DEPTH, strict_json_loads
from anima_webui.server import COMFY_KEY, HISTORY_KEY, MANAGER_KEY, create_app
from test_server import APP_DIR, FakeComfy, _write_fake_tools


class StrictJsonDepthTests(unittest.TestCase):
    def test_container_depth_boundary(self):
        for kind in ("array", "object", "mixed"):
            for depth in (MAX_JSON_DEPTH - 1, MAX_JSON_DEPTH, MAX_JSON_DEPTH + 1):
                payload = "0"
                for level in range(depth):
                    if kind == "array" or (kind == "mixed" and level % 2 == 0):
                        payload = "[" + payload + "]"
                    else:
                        payload = '{"value":' + payload + "}"
                with self.subTest(kind=kind, depth=depth):
                    if depth <= MAX_JSON_DEPTH:
                        self.assertEqual(
                            strict_json_loads(payload), json.loads(payload)
                        )
                    else:
                        with self.assertRaisesRegex(ValueError, "nesting"):
                            strict_json_loads(payload)

    def test_strings_and_escaped_quotes_do_not_count_as_containers(self):
        texts = (
            "[" * 1000 + "}" * 1000,
            'quoted " [{ brackets }] and backslash \\',
            '\\\\"[{' * 100,
            '\\"}]"' * 100,
        )
        for text in texts:
            payload = {text: text, "sibling": [{"text": text}]}
            with self.subTest(text=text[:30]):
                self.assertEqual(strict_json_loads(json.dumps(payload)), payload)
        self.assertEqual(
            strict_json_loads(r'{"text": "\u005b\u007b\u005d\u007d"}'), {"text": "[{]}"}
        )

    def test_escaped_string_cannot_hide_following_nested_containers(self):
        for text in ('\\"[{', "\\", '\\\\"}]'):
            payload = (
                '{"text":'
                + json.dumps(text)
                + ',"selection":'
                + "[" * MAX_JSON_DEPTH
                + "0"
                + "]" * MAX_JSON_DEPTH
                + "}"
            )
            with self.subTest(text=text):
                with self.assertRaisesRegex(ValueError, "nesting"):
                    strict_json_loads(payload)

    def test_many_sibling_containers_are_not_a_depth_overflow(self):
        payload = {"items": [{} for _ in range(MAX_JSON_DEPTH * 4)]}
        self.assertEqual(strict_json_loads(json.dumps(payload)), payload)


class ServerSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        directory = Path(self.temp.name)
        self.app = create_app(
            app_dir=APP_DIR,
            data_dir=directory / "data",
            comfy=FakeComfy(),
            anima_tools_dir=_write_fake_tools(directory),
        )
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def test_other_local_port_cannot_modify_data(self):
        response = await self.client.post(
            "/api/custom-prompts",
            data=json.dumps(
                {"section": "pose", "title": "Cross origin", "prompt": "standing"}
            ),
            headers={
                "Origin": f"http://127.0.0.1:{self.client.port + 1}",
                "Content-Type": "text/plain",
            },
        )
        self.assertEqual(response.status, 403)
        pool = await (await self.client.get("/api/custom-prompts?section=pose")).json()
        self.assertEqual(pool["items"], [])

    async def test_host_and_origin_must_be_strict_authorities(self):
        invalid_headers = [
            {"Host": f"user@127.0.0.1:{self.client.port}"},
            {"Host": f"127.0.0.1:{self.client.port}/ignored"},
            {"Host": "127.0.0.1:invalid"},
            {"Origin": f"file://127.0.0.1:{self.client.port}"},
            {"Origin": f"http://user@127.0.0.1:{self.client.port}"},
            {"Origin": f"http://127.0.0.1:{self.client.port}/ignored"},
            {"Origin": f"https://127.0.0.1:{self.client.port}"},
            {"Origin": f"http://localhost:{self.client.port}"},
        ]
        for headers in invalid_headers:
            with self.subTest(headers=headers):
                response = await self.client.get("/api/config", headers=headers)
                self.assertEqual(response.status, 403)

    async def test_same_origin_loopback_works_and_remote_proxy_is_rejected(self):
        authorities = [
            (f"127.0.0.1:{self.client.port}", "http"),
            (f"localhost:{self.client.port}", "http"),
            (f"[::1]:{self.client.port}", "http"),
        ]
        for authority, scheme in authorities:
            with self.subTest(authority=authority):
                response = await self.client.get(
                    "/api/config",
                    headers={
                        "Host": authority,
                        "Origin": f"{scheme}://{authority}",
                        "Sec-Fetch-Site": "same-origin",
                    },
                )
                self.assertEqual(response.status, 200)
        response = await self.client.get(
            "/api/config",
            headers={
                "Host": "studio.example",
                "Origin": "https://studio.example:8443",
                "X-Forwarded-Host": "studio.example:8443",
            },
        )
        self.assertEqual(response.status, 403)

    async def test_cross_site_fetch_without_origin_is_rejected(self):
        response = await self.client.get(
            "/api/config", headers={"Sec-Fetch-Site": "cross-site"}
        )
        self.assertEqual(response.status, 403)
        # A user following the README's link can still open the actual UI.
        response = await self.client.get("/", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(response.status, 200)

    async def test_non_json_content_type_is_rejected(self):
        response = await self.client.post(
            "/api/custom-prompts",
            data=json.dumps(
                {"section": "pose", "title": "Plain form", "prompt": "standing"}
            ),
            headers={"Content-Type": "text/plain"},
        )
        self.assertEqual(response.status, 415)
        self.assertIn("error", await response.json())

    async def test_non_finite_and_deep_json_are_invalid_requests(self):
        payloads = (
            '{"page": Infinity}',
            '{"page": NaN}',
            '{"page": 1e9999}',
            '{"page": ' + "9" * 5000 + "}",
            '{"selection": ' + "[" * 1100 + "0" + "]" * 1100 + "}",
        )
        for payload in payloads:
            with self.subTest(payload=payload[:50]):
                response = await self.client.post(
                    "/api/pools/pose/query",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(response.status, 400)
                self.assertIn("error", await response.json())

    async def test_json_depth_boundary_returns_consistent_http_status(self):
        for depth, expected_status in (
            (MAX_JSON_DEPTH - 1, 200),
            (MAX_JSON_DEPTH, 200),
            (MAX_JSON_DEPTH + 1, 400),
        ):
            # The outer request object also consumes one container level.
            payload = (
                '{"selection":' + "[" * (depth - 1) + "0" + "]" * (depth - 1) + "}"
            )
            with self.subTest(depth=depth):
                response = await self.client.post(
                    "/api/pools/pose/query",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(response.status, expected_status)
                if expected_status == 400:
                    self.assertIn("error", await response.json())

    async def test_body_limit_preserves_413_json_contract(self):
        # Exercise aiohttp's real payload limit without allocating 21 MiB.
        self.app._client_max_size = 128
        response = await self.client.post(
            "/api/pools/pose/query", json={"q": "x" * 256}
        )
        self.assertEqual(response.status, 413)
        self.assertIn("error", await response.json())

    async def test_invalid_gzip_body_returns_400_and_closes_broken_connection(self):
        for endpoint, content_type in [
            ("/api/pools/pose/query", "application/json"),
        ]:
            with self.subTest(endpoint=endpoint):
                response = await self.client.post(
                    endpoint,
                    data=b"not gzip",
                    headers={"Content-Type": content_type, "Content-Encoding": "gzip"},
                )
                self.assertEqual(response.status, 400)
                self.assertEqual(response.headers["Connection"], "close")
                self.assertIn("error", await response.json())
                self.assertEqual((await self.client.get("/api/config")).status, 200)

    async def test_sqlite_ids_and_offsets_are_bounded(self):
        huge_id = str(2**63)
        endpoints = [
            ("GET", f"/api/images/{huge_id}", None),
            ("GET", f"/api/history/{huge_id}/regeneration-options", None),
            ("DELETE", f"/api/history/{huge_id}", None),
            ("POST", f"/api/history/{huge_id}/regenerate", {"mode": "replay"}),
            ("GET", f"/api/history?page={huge_id}", None),
        ]
        for method, endpoint, body in endpoints:
            with self.subTest(endpoint=endpoint):
                response = await self.client.request(method, endpoint, json=body)
                self.assertEqual(response.status, 400)
                self.assertIn("error", await response.json())

    async def test_api_http_errors_are_json_and_page_cannot_be_framed(self):
        missing = await self.client.get("/api/no-such-endpoint")
        self.assertEqual(missing.status, 404)
        self.assertIn("error", await missing.json())
        wrong_method = await self.client.post("/api/config", json={})
        self.assertEqual(wrong_method.status, 405)
        self.assertIn("GET", wrong_method.headers["Allow"])
        self.assertIn("error", await wrong_method.json())
        page = await self.client.get("/")
        self.assertEqual(page.headers["X-Frame-Options"], "DENY")
        self.assertEqual(page.headers["X-Content-Type-Options"], "nosniff")

    async def test_unexpected_error_is_logged_without_exposing_exception(self):
        with mock.patch(
            "anima_webui.server.CustomPromptStore.list",
            side_effect=RuntimeError("sensitive private storage path"),
        ):
            with self.assertLogs("anima_webui.server", level="ERROR"):
                response = await self.client.get("/api/custom-prompts")
        self.assertEqual(response.status, 500)
        self.assertIn("error", await response.json())
        self.assertNotIn("sensitive", await response.text())
        self.assertEqual((await self.client.get("/api/config")).status, 200)

    async def test_request_cancellation_is_not_converted_to_server_error(self):
        from anima_webui.server import error_middleware

        async def cancelled(_):
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await error_middleware(mock.Mock(path="/api/config"), cancelled)

    async def test_cleanup_closes_history_even_when_worker_and_client_fail(self):
        app = create_app(
            app_dir=APP_DIR,
            data_dir=Path(self.temp.name) / "cleanup-failure",
            comfy=FakeComfy(),
            anima_tools_dir=Path(self.temp.name) / "tools",
        )
        manager, comfy, history = (app[MANAGER_KEY], app[COMFY_KEY], app[HISTORY_KEY])
        with (
            mock.patch.object(
                manager, "shutdown", side_effect=RuntimeError("worker cleanup")
            ) as shutdown,
            mock.patch.object(
                comfy, "close", side_effect=RuntimeError("client cleanup")
            ) as close_client,
            mock.patch.object(history, "close", wraps=history.close) as close_history,
        ):
            with self.assertRaisesRegex(RuntimeError, "client cleanup"):
                await app.on_cleanup[-1](app)
        shutdown.assert_awaited_once()
        close_client.assert_awaited_once()
        close_history.assert_awaited_once()


class RemovedSurfaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_local_host_configuration_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "本机"):
                create_app(
                    app_dir=APP_DIR,
                    data_dir=Path(directory) / "data",
                    comfy=FakeComfy(),
                    trusted_hostnames={"studio.example"},
                )

    async def test_removed_upload_and_launcher_endpoints_return_json_404(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(
                app_dir=APP_DIR, data_dir=Path(directory) / "data", comfy=FakeComfy()
            )
            async with TestClient(TestServer(app)) as client:
                for endpoint in (
                    "/api/input-images",
                    "/api/prompt-images",
                    "/api/prompt-jobs",
                    "/api/launcher-shutdown",
                    "/api/prompt-pools/items",
                ):
                    response = await client.post(endpoint, json={})
                    self.assertEqual(response.status, 404, endpoint)
                    self.assertIn("error", await response.json())
