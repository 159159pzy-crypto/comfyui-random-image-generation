import asyncio
import io
import sys
import unittest
from collections import Counter
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestServer
from PIL import Image


APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

from anima_webui.comfy import ComfyAborted, ComfyClient, ComfyError, extract_images, extract_positive_prompt, validate_comfy_url  # noqa: E402
from anima_webui.workflow import WorkflowError  # noqa: E402


class ValidateComfyUrlTests(unittest.TestCase):
    def test_accepts_local_http_addresses(self):
        # README 宣称的安全边界:--comfy-url 只接受本机 HTTP 地址。
        self.assertEqual(validate_comfy_url("http://127.0.0.1:8188/"), "http://127.0.0.1:8188")
        self.assertEqual(validate_comfy_url("http://localhost:8188"), "http://localhost:8188")

    def test_rejects_remote_and_unsafe_addresses(self):
        for value in (
            "http://192.168.1.5:8188",
            "http://evil.example:8188",
            "https://127.0.0.1:8188",
            "http://user:pass@127.0.0.1:8188",
            "http://127.0.0.1:8188/?x=1",
            "http://127.0.0.1:8188/#frag",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ComfyError):
                    validate_comfy_url(value)


class NetworkErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_connection_failure_maps_to_comfy_error(self):
        # 真实网络路径:连接被拒绝时应归一化为 ComfyError,而不是裸 aiohttp 异常。
        client = ComfyClient("http://127.0.0.1:9")  # discard 端口,连接必被拒绝
        try:
            with self.assertRaisesRegex(ComfyError, "无法连接"):
                await client.status()
        finally:
            await client.close()


class PlainTextResponseTests(unittest.IsolatedAsyncioTestCase):
    """针对原版 ComfyUI(未装 Anima-Tools 扩展)的真实响应形态。"""

    async def asyncSetUp(self):
        app = web.Application()

        async def object_info(request):
            return web.json_response(
                {"LoraLoader": {"input": {"required": {"lora_name": [["one.safetensors"]]}}}}
            )

        app.router.add_get("/object_info", object_info)
        # 故意不注册 /anima-tools/*:aiohttp 对未知路由返回纯文本 404,与原版 ComfyUI 一致
        self.server = TestServer(app)
        await self.server.start_server()
        self.client = ComfyClient(f"http://127.0.0.1:{self.server.port}")

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.close()

    async def test_non_json_error_body_maps_to_comfy_error(self):
        # 回归:纯文本 404 曾以 JSONDecodeError 泄漏出 _json,绕过所有
        # except ComfyError 的降级逻辑,还被错误中间件误判成客户端请求 JSON 无效。
        with self.assertRaisesRegex(ComfyError, "404"):
            await self.client._json("GET", "/anima-tools/lora/manifest")

    async def test_lora_inventory_requires_lora_manager(self):
        with self.assertRaisesRegex(ComfyError, "LoRA Manager.*404"):
            await self.client.lora_inventory()


class LoraPreviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app = web.Application()
        self.preview_body = b"webp"
        self.preview_type = "image/webp"
        self.preview_length = None

        async def object_info(request):
            return web.json_response(
                {"LoraLoader": {"input": {"required": {"lora_name": [["风格\\one.safetensors"]]}}}}
            )

        async def manager_list(request):
            return web.json_response(
                {
                    "items": [
                        {
                            "file_path": "F:/comfyui/models/loras/风格/one.safetensors",
                            "folder": "风格",
                            "base_model": "Anima",
                            "preview_url": "/api/lm/previews?path=one",
                            "civitai": {"trainedWords": ["@one"]},
                        }
                    ],
                    "total_pages": 1,
                    "page": 1,
                    "page_size": 100,
                    "total": 1,
                }
            )

        async def preview(request):
            headers = {"Cache-Control": "private, max-age=90"}
            if self.preview_length is not None:
                headers["Content-Length"] = str(self.preview_length)
            return web.Response(
                body=self.preview_body,
                content_type=self.preview_type,
                headers=headers,
            )

        async def roots(request):
            return web.json_response({"success": True, "roots": ["F:/comfyui/models/loras"]})

        app.router.add_get("/object_info", object_info)
        app.router.add_get("/api/lm/loras/list", manager_list)
        app.router.add_get("/api/lm/loras/roots", roots)
        app.router.add_get("/api/lm/previews", preview)
        self.server = TestServer(app)
        await self.server.start_server()
        self.client = ComfyClient(f"http://127.0.0.1:{self.server.port}")

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.close()

    async def test_preview_is_loaded_only_for_inventory_filename(self):
        body, content_type, cache_control = await self.client.lora_preview(
            "风格/one.safetensors"
        )
        self.assertEqual(body, b"webp")
        self.assertEqual(content_type, "image/webp")
        self.assertEqual(cache_control, "private, max-age=90")

        with self.assertRaises(WorkflowError):
            await self.client.lora_preview("../one.safetensors")
        with self.assertRaises(KeyError):
            await self.client.lora_preview("missing.safetensors")

    async def test_generic_binary_raster_previews_get_their_real_content_type(self):
        self.preview_type = "application/octet-stream"
        for format_name, mime in (("PNG", "image/png"), ("JPEG", "image/jpeg"), ("GIF", "image/gif"), ("WEBP", "image/webp")):
            with self.subTest(format=format_name):
                buffer = io.BytesIO()
                Image.new("RGB", (2, 2), "white").save(buffer, format=format_name)
                self.preview_body = buffer.getvalue()
                body, content_type, cache_control = await self.client.lora_preview("风格/one.safetensors")
                self.assertEqual(body, self.preview_body)
                self.assertEqual(content_type, mime)
                self.assertEqual(cache_control, "private, max-age=90")

    async def test_generic_binary_preview_rejects_non_raster_signatures(self):
        self.preview_type = "application/octet-stream"
        for body in (b"", b"RIFF", b"<html><script>bad</script>", b'<svg xmlns="http://www.w3.org/2000/svg"/>', b"RIFF1234WAVEfmt ", b"RIFF1234WEBPjunk"):
            with self.subTest(body=body):
                self.preview_body = body
                with self.assertRaisesRegex(ComfyError, "格式不支持"):
                    await self.client.lora_preview_asset("/api/lm/previews?path=one.webp")

    async def test_preview_sniffing_keeps_size_and_declared_type_checks(self):
        self.preview_type = "application/octet-stream"
        self.preview_body = b"RIFF1234WEBPVP8 "
        self.preview_length = 33 * 1024 * 1024
        with self.assertRaisesRegex(ComfyError, "32 MiB"):
            await self.client.lora_preview_asset("/api/lm/previews?path=one")
        self.preview_length = None
        self.preview_type = "text/html"
        with self.assertRaisesRegex(ComfyError, "格式不支持"):
            await self.client.lora_preview_asset("/api/lm/previews?path=one")


class FakeInventoryClient(ComfyClient):
    def __init__(self):
        super().__init__()

    async def object_info(self):
        return {
            "LoraLoader": {"input": {"required": {"lora_name": [["风格\\one.safetensors"]]}}},
            "UNETLoader": {"input": {"required": {"unet_name": [["model.safetensors"]]}}},
            "UpscaleModelLoader": {
                "input": {"required": {"model_name": ["COMBO", {"options": ["upscale.pth"]}]}}
            },
            "easy hiresFix": {"input": {"required": {"model_name": [["upscale.pth"]]}}},
            "KSampler": {
                "input": {
                    "required": {
                        "sampler_name": [["er_sde", "euler"]],
                        "scheduler": [["simple", "karras"]],
                    }
                }
            },
        }

    async def _json(self, method, path, **kwargs):
        if path == "/api/lm/loras/roots":
            return {"success": True, "roots": ["F:/comfyui/models/loras"]}
        if path.startswith("/api/lm/loras/list?"):
            return {"items": [{"file_path": "F:/comfyui/models/loras/风格/one.safetensors", "base_model": "Anima"}], "total_pages": 1}
        raise ComfyError("unexpected")


class InventoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_basic_resources_work_without_optional_upscaler(self):
        class NoUpscaler(FakeInventoryClient):
            async def object_info(self):
                result = await super().object_info()
                result["UpscaleModelLoader"] = {"input": {"required": {"model_name": [[]]}}}
                result["easy hiresFix"] = {"input": {"required": {"model_name": [[]]}}}
                return result

        resources = await NoUpscaler().resource_inventory()
        self.assertEqual(resources["upscale_models"], [])
        self.assertEqual(resources["models"], ["model.safetensors"])

    async def test_lora_inventory_deduplicates_path_separators(self):
        client = FakeInventoryClient()
        inventory = await client.lora_inventory()
        self.assertEqual(inventory["count"], 1)
        self.assertEqual(inventory["items"][0]["filename"], "风格\\one.safetensors")
        self.assertEqual(inventory["items"][0]["normalized_path"], "风格/one.safetensors")
        self.assertEqual(inventory["items"][0]["folder"], "风格")
        self.assertEqual(inventory["items"][0]["metadata_source"], "lora-manager")
        self.assertTrue(inventory["items"][0]["trigger_metadata_available"])
        self.assertEqual(inventory["items"][0]["model_profile"], "anima")

    async def test_lora_manager_metadata_is_paginated_matched_and_normalized(self):
        class ManagerClient(FakeInventoryClient):
            async def _json(self, method, path, **kwargs):
                if path.startswith("/api/lm/loras/list"):
                    page = 2 if "page=2" in path else 1
                    return {
                        "items": [{"file_path": "F:/comfyui/models/loras/stale.safetensors", "base_model": "Anima"}]
                        if page == 2
                        else [
                            {
                                "model_name": "One Model",
                                "base_model": "Anima",
                                "file_path": "F:/comfyui/models/loras/风格/one.safetensors",
                                "folder": "风格",
                                "file_size": 123,
                                "preview_url": "/api/lm/previews?path=one",
                                "preview_nsfw_level": 2,
                                "civitai": {
                                    "trainedWords": [" @One ", "@one", "", None, "second"]
                                },
                            }
                        ],
                        "total_pages": 2,
                    }
                return await super()._json(method, path, **kwargs)

        inventory = await ManagerClient().lora_inventory()
        self.assertEqual(inventory["count"], 1)
        item = inventory["items"][0]
        self.assertEqual(item["display_name"], "One Model")
        self.assertEqual(item["preview"], "/api/lm/previews?path=one")
        self.assertEqual(item["trigger_words"], ["@One", "second"])
        self.assertEqual(item["metadata_source"], "lora-manager")
        self.assertTrue(item["trigger_metadata_available"])
        self.assertEqual(item["size"], 123)

    async def test_resource_inventory_reads_live_choices(self):
        resources = await FakeInventoryClient().resource_inventory()
        self.assertEqual(
            resources,
            {
                "models": ["model.safetensors"],
                "upscale_models": ["upscale.pth"],
                "samplers": ["er_sde", "euler"],
                "schedulers": ["simple", "karras"],
            },
        )


class ManagerClassificationApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = Counter()
        self.info = {
            "LoraLoader": {"input": {"required": {"lora_name": [["style\\one.safetensors"]]}}},
            "UNETLoader": {"input": {"required": {"unet_name": [["model.safetensors"]]}}},
        }
        self.items = {
            "loras": [{"file_path": "F:/models/loras/style/one.safetensors", "base_model": "Anima"}],
            "checkpoints": [
                {"file_path": "F:/models/checkpoints/model.safetensors", "base_model": "Anima"},
                {"file_path": "F:/models/diffusion_models/model.safetensors", "base_model": "Krea 2 Turbo"},
            ],
        }
        self.overrides = {}

        async def handler(request):
            self.calls[request.path] += 1
            key = request.path + (f"?page={request.query['page']}" if "page" in request.query else "")
            if key in self.overrides:
                value = self.overrides[key]
                if isinstance(value, web.Response):
                    return value
                return web.json_response(value)
            if request.path == "/object_info":
                return web.json_response(self.info)
            if request.path == "/api/lm/loras/roots":
                return web.json_response({"success": True, "roots": ["F:/models/loras"]})
            if request.path == "/api/lm/checkpoints/unet_roots":
                return web.json_response({"success": True, "roots": ["F:/models/diffusion_models", "F:/models/unet"]})
            if request.path in {"/api/lm/loras/list", "/api/lm/checkpoints/list"}:
                values = self.items[request.path.split("/")[-2]]
                return web.json_response({"items": values, "total": len(values), "page": 1, "page_size": 100, "total_pages": 1 if values else 0})
            return web.Response(status=404, text="Not Found")

        app = web.Application()
        app.router.add_get("/{path:.*}", handler)
        self.server = TestServer(app)
        await self.server.start_server()
        self.client = ComfyClient(f"http://127.0.0.1:{self.server.port}")

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.close()

    async def test_both_inventories_use_plugin_and_real_loader_names(self):
        models = await self.client.resource_classifications("models")
        loras = await self.client.lora_inventory()
        self.assertEqual(models["source"], "lora-manager")
        self.assertEqual(models["manager_url"], self.client.base_url + "/checkpoints")
        self.assertEqual(loras["manager_url"], self.client.base_url + "/loras")
        self.assertIsNone(models["items"][0]["model_profile"])
        self.assertEqual(models["items"][0]["classification_status"], "unsupported")
        self.assertEqual(loras["items"][0]["name"], "style/one.safetensors")
        self.assertEqual(await self.client.lora_filenames(), ["style\\one.safetensors"])
        self.assertEqual(self.calls["/object_info"], 1)
        self.assertFalse(any("anima-tools" in path or "scan" in path or "health-check" in path for path in self.calls))

    async def test_all_pages_are_read_before_matching(self):
        values = [{"file_path": f"F:/models/loras/unused/{i}.safetensors", "base_model": "Pony"} for i in range(100)]
        self.overrides["/api/lm/loras/list?page=1"] = {"items": values, "total": 101, "page": 1, "page_size": 100, "total_pages": 2}
        self.overrides["/api/lm/loras/list?page=2"] = {"items": self.items["loras"], "total": 101, "page": 2, "page_size": 100, "total_pages": 2}
        result = await self.client.lora_inventory()
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["model_profile"], "anima")
        self.assertEqual(self.calls["/api/lm/loras/list"], 2)

    async def test_missing_plugin_routes_are_explicit_errors_without_fallback(self):
        for path in ("/api/lm/loras/roots", "/api/lm/loras/list?page=1", "/api/lm/checkpoints/unet_roots"):
            with self.subTest(path=path):
                self.overrides = {path: web.Response(status=404, text="Not Found")}
                with self.assertRaisesRegex(ComfyError, "LoRA Manager.*404"):
                    await self.client.resource_classifications("models" if "checkpoints" in path else "loras", refresh=True)

    async def test_page_failure_rejects_entire_inventory(self):
        self.overrides["/api/lm/loras/list?page=1"] = {"items": self.items["loras"], "total_pages": 2}
        self.overrides["/api/lm/loras/list?page=2"] = web.Response(status=503, text="not ready")
        with self.assertRaisesRegex(ComfyError, "LoRA Manager.*503"):
            await self.client.lora_inventory()
        self.assertNotIn("loras", self.client._classification_cache)

    async def test_malformed_pagination_is_rejected(self):
        good = {"items": self.items["loras"], "total_pages": 1}
        for payload in (
            {**good, "items": {}}, {**good, "items": ["invalid"]},
            {**good, "items": [{"file_path": "relative.safetensors"}]},
            {**good, "total_pages": "1"}, {**good, "total_pages": True},
            {**good, "total_pages": -1}, {**good, "total_pages": 0},
            {**good, "page": 2}, {**good, "page_size": 1},
            {**good, "total": 2}, {**good, "total": True},
            {"items": [], "total_pages": 2},
        ):
            with self.subTest(payload=payload):
                self.overrides["/api/lm/loras/list?page=1"] = payload
                with self.assertRaisesRegex(ComfyError, "LoRA Manager"):
                    await self.client.lora_inventory(refresh=True)

    async def test_repeated_or_changed_pages_are_rejected(self):
        self.overrides["/api/lm/loras/list?page=1"] = {"items": self.items["loras"], "total_pages": 2}
        for page_two in (
            {"items": self.items["loras"], "total_pages": 2},
            {"items": self.items["loras"], "total_pages": 3},
            {"items": [], "total_pages": 2},
        ):
            with self.subTest(page_two=page_two):
                self.overrides["/api/lm/loras/list?page=2"] = page_two
                with self.assertRaisesRegex(ComfyError, "LoRA Manager"):
                    await self.client.lora_inventory(refresh=True)

    async def test_invalid_roots_and_loader_info_are_rejected(self):
        for payload in ({"roots": []}, {"success": False, "roots": []}, {"success": True, "roots": "F:/loras"}, {"success": True, "roots": [None]}):
            with self.subTest(payload=payload):
                self.overrides["/api/lm/loras/roots"] = payload
                with self.assertRaisesRegex(ComfyError, "LoRA Manager"):
                    await self.client.lora_inventory(refresh=True)
        self.overrides.clear()
        for node in ({}, {"input": []}, {"input": {"required": {"lora_name": [[123]]}}}):
            with self.subTest(node=node):
                self.info["LoraLoader"] = node
                with self.assertRaisesRegex(ComfyError, "LoraLoader"):
                    await self.client.lora_inventory(refresh=True)

    async def test_empty_plugin_cache_does_not_assign_unknown_loader_choices(self):
        self.items["loras"] = []
        result = await self.client.lora_inventory()
        self.assertEqual(result["items"][0]["classification_status"], "unindexed")
        self.assertIsNone(result["items"][0]["model_profile"])

    async def test_empty_loader_inventory_is_valid_and_not_filled_by_plugin(self):
        self.info["LoraLoader"]["input"]["required"]["lora_name"] = [[]]
        self.assertEqual((await self.client.lora_inventory())["items"], [])

    async def test_combo_options_are_supported(self):
        self.info["UNETLoader"]["input"]["required"]["unet_name"] = ["COMBO", {"options": ["model.safetensors"]}]
        result = await self.client.resource_classifications("models")
        self.assertIsNone(result["items"][0]["model_profile"])
        self.assertEqual(result["items"][0]["classification_status"], "unsupported")

    async def test_classification_cache_is_shared_with_lora_inventory_and_refreshes_both_sources(self):
        first = await self.client.lora_inventory()
        self.assertIs(await self.client.resource_classifications("loras"), first)
        self.items["loras"][0]["base_model"] = "Krea 2"
        self.assertEqual((await self.client.lora_inventory())["items"][0]["model_profile"], "anima")
        self.info["LoraLoader"]["input"]["required"]["lora_name"][0].append("new.safetensors")
        refreshed = await self.client.resource_classifications("loras", refresh=True)
        self.assertIsNone(refreshed["items"][0]["model_profile"])
        self.assertEqual(refreshed["items"][0]["classification_status"], "unsupported")
        self.assertEqual(refreshed["items"][1]["classification_status"], "unindexed")
        self.assertIs(await self.client.lora_inventory(), refreshed)
        self.assertEqual(self.calls["/object_info"], 2)
        self.assertEqual(self.calls["/api/lm/loras/list"], 2)

    async def test_expired_cache_reloads_and_failed_refresh_does_not_return_stale_classification(self):
        await self.client.lora_inventory()
        _, result = self.client._classification_cache["loras"]
        self.client._classification_cache["loras"] = (-10.0, result)
        self.items["loras"][0]["base_model"] = "Krea 2"
        self.assertIsNone((await self.client.lora_inventory())["items"][0]["model_profile"])
        self.overrides["/api/lm/loras/roots"] = web.Response(status=500, text="broken")
        with self.assertRaisesRegex(ComfyError, "LoRA Manager"):
            await self.client.lora_inventory(refresh=True)
        with self.assertRaisesRegex(ComfyError, "LoRA Manager"):
            await self.client.lora_inventory()

    async def test_refresh_waits_for_older_fetch_and_remains_the_cached_result(self):
        started, release = asyncio.Event(), asyncio.Event()
        original = self.client._json

        async def delayed(method, path, **kwargs):
            payload = await original(method, path, **kwargs)
            if path.startswith("/api/lm/loras/list?") and not started.is_set():
                started.set()
                await release.wait()
            return payload

        self.client._json = delayed
        old_request = asyncio.create_task(self.client.lora_inventory())
        await asyncio.wait_for(started.wait(), 2)
        self.items["loras"][0]["base_model"] = "Krea 2"
        refresh_request = asyncio.create_task(self.client.lora_inventory(refresh=True))
        await asyncio.sleep(0)
        self.assertFalse(refresh_request.done())
        release.set()
        old, fresh = await asyncio.gather(old_request, refresh_request)
        self.assertEqual(old["items"][0]["model_profile"], "anima")
        self.assertIsNone(fresh["items"][0]["model_profile"])
        self.assertEqual(fresh["items"][0]["classification_status"], "unsupported")
        self.assertIs(await self.client.lora_inventory(), fresh)
        self.assertEqual(self.calls["/api/lm/loras/list"], 2)

    async def test_old_object_info_cannot_overwrite_refreshed_object_cache(self):
        started, release = asyncio.Event(), asyncio.Event()
        original = self.client._json

        async def delayed(method, path, **kwargs):
            payload = await original(method, path, **kwargs)
            if path == "/object_info" and not started.is_set():
                started.set()
                await release.wait()
            return payload

        self.client._json = delayed
        old_request = asyncio.create_task(self.client.object_info())
        await asyncio.wait_for(started.wait(), 2)
        self.info["LoraLoader"]["input"]["required"]["lora_name"][0].append("new.safetensors")
        fresh = await self.client.lora_inventory(refresh=True)
        self.assertEqual(fresh["count"], 2)
        release.set()
        await old_request
        info = await self.client.object_info()
        self.assertEqual(info["LoraLoader"]["input"]["required"]["lora_name"][0], ["style\\one.safetensors", "new.safetensors"])


class WaitForHistoryTests(unittest.IsolatedAsyncioTestCase):
    def make_client(self, history_responses, queued=False):
        client = ComfyClient(poll_interval=0.001)
        calls = {"history": 0}

        async def fake_json(method, path, **kwargs):
            if path.startswith("/history/"):
                calls["history"] += 1
                index = min(calls["history"] - 1, len(history_responses) - 1)
                return history_responses[index]
            if path == "/queue":
                return {
                    "queue_running": [[0, "prompt-1"]] if queued else [],
                    "queue_pending": [],
                }
            raise AssertionError(f"unexpected request: {path}")

        client._json = fake_json
        return client

    async def test_abort_raises_before_polling(self):
        # 回归 #P1-3:停止请求应在轮询循环内立即生效。
        client = ComfyClient(poll_interval=0.001)

        async def unexpected(method, path, **kwargs):
            raise AssertionError("收到停止请求后不应继续发起请求")

        client._json = unexpected
        with self.assertRaises(ComfyAborted):
            await client.wait_for_history("prompt-1", should_abort=lambda: True)

    async def test_vanished_prompt_times_out_instead_of_hanging(self):
        # 回归 #P1-3:任务既不在 history 也不在队列(历史被清空/ComfyUI 重启)时,
        # 必须在 missing_timeout 内报错,而不是永久卡死批次。
        client = self.make_client([{}], queued=False)
        with self.assertRaisesRegex(ComfyError, "找不到该任务"):
            await client.wait_for_history("prompt-1", missing_timeout=0.05)

    async def test_queued_prompt_keeps_waiting_until_history_appears(self):
        entry = {"outputs": {}, "status": {"status_str": "success", "completed": True}}
        client = self.make_client([{}, {}, {}, {"prompt-1": entry}], queued=True)
        result = await client.wait_for_history("prompt-1", missing_timeout=0.01)
        self.assertEqual(result, entry)

    async def test_error_status_raises_comfy_error(self):
        entry = {"status": {"status_str": "error", "messages": ["boom"]}}
        client = self.make_client([{"prompt-1": entry}])
        with self.assertRaisesRegex(ComfyError, "执行失败"):
            await client.wait_for_history("prompt-1")


class PositivePromptTests(unittest.TestCase):
    def test_rebuilds_prompt_when_history_contains_connection_placeholder(self):
        entry = {
            "prompt": [0, 0, {}, {"extra_pnginfo": {
                "anima_prompt": {"42": {"positive": "quality, ['60', 0], "}},
                "anima_prompt_composer": {"60": {"resolved_prompt": "character, pose, "}},
                "anima_random_webui": {"settings": {
                    "quality_prompt": "quality",
                    "manual_artist": "artist, @second",
                    "extra_prompt": "lighting",
                }},
            }}],
        }
        self.assertEqual(extract_positive_prompt(entry), "quality, @artist, @second, character, pose, lighting, ")

    def test_rebuild_includes_fixed_categories_without_repeating_extra_prompt(self):
        entry = {
            "prompt": [0, 0, {}, {"extra_pnginfo": {
                "anima_prompt": {"42": {"positive": "quality, ['60', 0], "}},
                "anima_prompt_composer": {"60": {"resolved_prompt": "lighting, "}},
                "anima_random_webui": {"settings": {
                    "quality_prompt": "quality",
                    "manual_artist": "",
                    "random_character": False,
                    "random_clothing": False,
                    "random_pose": False,
                    "random_background": False,
                    "fixed_character": "hero",
                    "fixed_clothing": "red coat",
                    "fixed_pose": "standing",
                    "fixed_background": "city",
                    "extra_prompt": "lighting",
                }},
            }}],
        }

        self.assertEqual(
            extract_positive_prompt(entry),
            "quality, hero, red coat, standing, city, lighting, ",
        )


class ImageExtractionTests(unittest.TestCase):
    def test_ignores_temporary_preview_images(self):
        entry = {
            "outputs": {
                "save": {"images": [{"filename": "final.png", "type": "output"}]},
                "preview": {"images": [{"filename": "preview.png", "type": "temp"}]},
                "legacy": {"images": [{"filename": "legacy.png"}]},
            }
        }
        self.assertEqual(
            [image["filename"] for image in extract_images(entry)],
            ["final.png", "legacy.png"],
        )


if __name__ == "__main__":
    unittest.main()
