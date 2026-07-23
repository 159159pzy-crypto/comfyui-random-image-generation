import asyncio
import tempfile
import unittest
from pathlib import Path

import sys

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

from anima_webui.history import HistoryStore  # noqa: E402
from anima_webui.runner import BatchConflict, BatchManager  # noqa: E402
from anima_webui.workflow import DEFAULT_SETTINGS  # noqa: E402


class FakeTemplates:
    def submission(self, settings, sample_seed, prompt_seed, filename_prefix, client_id, sequence, **resolved):
        return {
            "settings": settings,
            "sample_seed": sample_seed,
            "prompt_seed": prompt_seed,
            "filename_prefix": filename_prefix,
            "sequence": sequence,
            **resolved,
        }


class FakeCatalog:
    def validate_settings(self, settings):
        return None

    def resolve_prompt(self, settings, prompt_seed):
        return {
            "composer_prompt": "requested character, ",
            "full_prompt": "quality, requested character, ",
            "selected": {"character": [{"title": "requested character"}]},
        }


class FakeComfy:
    def __init__(self):
        self.submissions = []
        self.block = None
        self.error_at = None
        self.available_loras = [item["filename"] for item in DEFAULT_SETTINGS["loras"]]
        self.available_models = [DEFAULT_SETTINGS["model_name"]]
        self.available_upscalers = [DEFAULT_SETTINGS["hires"]["model_name"]]

    async def lora_filenames(self):
        return list(self.available_loras)

    async def resource_inventory(self):
        return {
            "models": list(self.available_models),
            "upscale_models": list(self.available_upscalers),
        }

    async def submit(self, payload):
        self.submissions.append(payload)
        return f"prompt-{len(self.submissions)}"

    async def wait_for_history(self, prompt_id):
        sequence = len(self.submissions)
        if self.block is not None:
            await self.block.wait()
        if self.error_at == sequence:
            raise RuntimeError("render failed")
        payload = self.submissions[sequence - 1]
        return {
            "outputs": {
                "12": {
                    "images": [
                        {
                            "filename": f"image_{sequence}.png",
                            "subfolder": "AnimaRandom",
                            "type": "output",
                        }
                    ]
                }
            },
            "prompt": [0, prompt_id, {}, {"extra_pnginfo": {"anima_prompt": {"42": {"positive": f"prompt {sequence}"}}}}],
        }


class HistoryTests(unittest.TestCase):
    def test_history_persists_and_delete_keeps_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            store = HistoryStore(path)
            store.create_batch("batch", 1, DEFAULT_SETTINGS)
            image = store.add_image(
                batch_id="batch",
                sequence=1,
                prompt_id="prompt",
                image={"filename": "one.png", "subfolder": "folder", "type": "output"},
                positive_prompt="positive",
                negative_prompt="negative",
                sample_seed=1,
                prompt_seed=2,
                settings=DEFAULT_SETTINGS,
            )
            store.close()
            reopened = HistoryStore(path)
            self.assertEqual(reopened.list_images()["total"], 1)
            self.assertEqual(reopened.get_image(image["id"])["positive_prompt"], "positive")
            self.assertTrue(reopened.delete_image(image["id"]))
            self.assertEqual(reopened.list_images()["total"], 0)
            self.assertEqual(reopened.get_batch("batch")["id"], "batch")
            reopened.close()

    def test_running_batch_is_marked_interrupted_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            store = HistoryStore(path)
            store.create_batch("batch", 2, DEFAULT_SETTINGS)
            store.close()
            reopened = HistoryStore(path)
            self.assertEqual(reopened.get_batch("batch")["status"], "interrupted")
            reopened.close()


class BatchManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.history = HistoryStore(Path(self.temp.name) / "history.sqlite3")
        self.comfy = FakeComfy()
        self.manager = BatchManager(FakeTemplates(), self.history, self.comfy)

    async def asyncTearDown(self):
        if self.manager.task and not self.manager.task.done():
            self.manager.task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await self.manager.task
        self.history.close()
        self.temp.cleanup()

    async def test_generates_exact_count_sequentially(self):
        await self.manager.start({**DEFAULT_SETTINGS, "count": 3})
        result = await self.manager.wait()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["completed"], 3)
        self.assertEqual([item["sequence"] for item in self.comfy.submissions], [1, 2, 3])
        self.assertTrue(all(item["filename_prefix"].endswith("/image") for item in self.comfy.submissions))
        self.assertEqual(self.history.list_images()["total"], 3)

    async def test_only_one_batch_can_run(self):
        self.comfy.block = asyncio.Event()
        await self.manager.start({**DEFAULT_SETTINGS, "count": 2})
        await asyncio.sleep(0)
        with self.assertRaises(BatchConflict):
            await self.manager.start(DEFAULT_SETTINGS)
        self.comfy.block.set()
        await self.manager.wait()

    async def test_stop_finishes_current_image_then_stops(self):
        self.comfy.block = asyncio.Event()
        state = await self.manager.start({**DEFAULT_SETTINGS, "count": 3})
        while not self.comfy.submissions:
            await asyncio.sleep(0)
        await self.manager.request_stop(state["id"])
        self.comfy.block.set()
        result = await self.manager.wait()
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["completed"], 1)
        self.assertEqual(len(self.comfy.submissions), 1)

    async def test_error_stops_without_retry(self):
        self.comfy.error_at = 2
        await self.manager.start({**DEFAULT_SETTINGS, "count": 4})
        result = await self.manager.wait()
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["completed"], 1)
        self.assertEqual(len(self.comfy.submissions), 2)
        self.assertIn("render failed", result["error"])

    async def test_history_uses_actual_comfy_prompt_when_catalog_is_present(self):
        manager = BatchManager(FakeTemplates(), self.history, self.comfy, FakeCatalog())

        await manager.start({**DEFAULT_SETTINGS, "count": 1})
        await manager.wait()

        image = self.history.list_images()["items"][0]
        self.assertEqual(image["positive_prompt"], "prompt 1")
        self.assertEqual(image["resolved_prompt"], "prompt 1")

    async def test_missing_lora_is_rejected_before_batch_creation(self):
        settings = {
            **DEFAULT_SETTINGS,
            "loras": [{"filename": "deleted.safetensors", "enabled": True, "strength": 1}],
        }

        with self.assertRaisesRegex(ValueError, "deleted.safetensors"):
            await self.manager.start(settings)

        self.assertIsNone(self.manager.state)
        self.assertEqual(self.history.list_images()["total"], 0)

    async def test_missing_models_are_rejected_before_batch_creation(self):
        self.comfy.available_models = []
        with self.assertRaisesRegex(ValueError, "主模型不存在"):
            await self.manager.start(DEFAULT_SETTINGS)
        self.assertIsNone(self.manager.state)

        self.comfy.available_models = [DEFAULT_SETTINGS["model_name"]]
        self.comfy.available_upscalers = []
        with self.assertRaisesRegex(ValueError, "高清修复模型不存在"):
            await self.manager.start(DEFAULT_SETTINGS)
        self.assertIsNone(self.manager.state)


if __name__ == "__main__":
    unittest.main()
