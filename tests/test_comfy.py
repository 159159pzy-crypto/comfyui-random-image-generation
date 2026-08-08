import sys
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestServer


APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

from anima_webui.comfy import ComfyAborted, ComfyClient, ComfyError, extract_positive_prompt, validate_comfy_url  # noqa: E402
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

    async def test_lora_inventory_degrades_without_manifest_endpoint(self):
        # 回归:清单端点不存在时应回退到 LoraLoader 名称,而不是让 /api/loras
        # 与批次启动整体失败。
        inventory = await self.client.lora_inventory()
        self.assertEqual([item["filename"] for item in inventory["items"]], ["one.safetensors"])


class LoraPreviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app = web.Application()

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
                            "preview_url": "/api/lm/previews?path=one",
                            "civitai": {"trainedWords": ["@one"]},
                        }
                    ],
                    "total_pages": 1,
                }
            )

        async def preview(request):
            return web.Response(
                body=b"webp",
                content_type="image/webp",
                headers={"Cache-Control": "private, max-age=90"},
            )

        app.router.add_get("/object_info", object_info)
        app.router.add_get("/api/lm/loras/list", manager_list)
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
        }

    async def _json(self, method, path, **kwargs):
        if path == "/anima-tools/lora/manifest":
            return {"items": [{"filename": "风格/one.safetensors", "display_name": "One"}]}
        raise ComfyError("unexpected")


class InventoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_lora_inventory_deduplicates_path_separators(self):
        client = FakeInventoryClient()
        inventory = await client.lora_inventory()
        self.assertEqual(inventory["count"], 1)
        self.assertEqual(inventory["items"][0]["filename"], "风格\\one.safetensors")
        self.assertEqual(inventory["items"][0]["normalized_path"], "风格/one.safetensors")
        self.assertEqual(inventory["items"][0]["folder"], "风格")
        self.assertEqual(inventory["items"][0]["metadata_source"], "anima-tools")
        self.assertFalse(inventory["items"][0]["trigger_metadata_available"])

    async def test_lora_manager_metadata_is_paginated_matched_and_normalized(self):
        class ManagerClient(FakeInventoryClient):
            async def _json(self, method, path, **kwargs):
                if path.startswith("/api/lm/loras/list"):
                    page = 2 if "page=2" in path else 1
                    return {
                        "items": []
                        if page == 2
                        else [
                            {
                                "model_name": "One Model",
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
                if path == "/anima-tools/lora/manifest":
                    return {
                        "items": [
                            {
                                "filename": "风格/one.safetensors",
                                "display_name": "Manifest One",
                                "thumb_url": "/anima-tools/preview",
                            },
                            {"filename": "stale.safetensors"},
                        ]
                    }
                raise ComfyError("unexpected")

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
        self.assertEqual(resources, {"models": ["model.safetensors"], "upscale_models": ["upscale.pth"]})


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


if __name__ == "__main__":
    unittest.main()
