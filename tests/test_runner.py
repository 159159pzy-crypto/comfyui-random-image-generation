import asyncio
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import call, patch

import sys

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

from anima_webui.comfy import ComfyAborted  # noqa: E402
from anima_webui.history import HistoryStore  # noqa: E402
from anima_webui.runner import BatchConflict, BatchManager, MAX_EXECUTION_NONCE, new_execution_context  # noqa: E402
from anima_webui.workflow import DEFAULT_SETTINGS, MAX_SAMPLE_SEED  # noqa: E402


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
        self.honor_abort = False
        self.interrupted = False
        self.available_loras = [item["filename"] for item in DEFAULT_SETTINGS["loras"]]
        self.available_models = [DEFAULT_SETTINGS["model_name"]]
        self.available_upscalers = [DEFAULT_SETTINGS["upscalers"]["pixel_hires"]["model_name"]]
        self.available_samplers = [DEFAULT_SETTINGS["sampler_name"]]
        self.available_schedulers = [DEFAULT_SETTINGS["scheduler"]]

    async def lora_filenames(self):
        return list(self.available_loras)

    async def resource_inventory(self):
        return {
            "models": list(self.available_models),
            "upscale_models": list(self.available_upscalers),
            "samplers": list(self.available_samplers),
            "schedulers": list(self.available_schedulers),
        }

    async def submit(self, payload):
        self.submissions.append(payload)
        return f"prompt-{len(self.submissions)}"

    async def interrupt(self):
        self.interrupted = True

    async def wait_for_history(self, prompt_id, should_abort=None, missing_timeout=30.0):
        sequence = len(self.submissions)
        if self.block is not None:
            if self.honor_abort and should_abort is not None:
                while not self.block.is_set():
                    if should_abort():
                        raise ComfyAborted("stop")
                    await asyncio.sleep(0)
            else:
                await self.block.wait()
        if self.error_at == sequence:
            raise RuntimeError("render failed")
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


class RedrawCatalog(FakeCatalog):
    def prompt_parts(self, item, section, character_detail="trigger_tags", strip_person_tags=False):
        return [item.get("prompt") or item.get("title") or ""]

    def resolve_prompt(self, settings, prompt_seed):
        pose_id = "pose:new" if prompt_seed % 2 else "pose:old"
        pose = {"id": pose_id, "title": pose_id, "prompt": pose_id}
        character = {"id": "character:new", "title": "character:new", "prompt": "character:new"}
        fixed_character = settings.get("fixed_character") or ""
        return {
            "composer_prompt": f"{fixed_character}, {pose_id}, ",
            "full_prompt": f"quality, {fixed_character}, {pose_id}, ",
            "selected": {"character": [character], "pose": [pose]},
        }

class ProgressComfy(FakeComfy):
    """带 websocket 进度流的 fake:产出一条采样进度和一帧预览后挂起。"""

    def __init__(self):
        super().__init__()
        self.release_stream = asyncio.Event()

    async def progress_stream(self, client_id):
        yield {"kind": "event", "payload": {"type": "progress", "data": {"value": 12, "max": 30, "prompt_id": "prompt-1", "node": "sampler"}}}
        yield {"kind": "preview", "format": "jpeg", "bytes": b"jpeg-frame"}
        await self.release_stream.wait()


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_persists_and_delete_keeps_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            store = HistoryStore(path)
            await store.create_batch("batch", 1, DEFAULT_SETTINGS)
            image = await store.add_image(
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
            await store.close()
            reopened = HistoryStore(path)
            self.assertEqual((await reopened.list_images())["total"], 1)
            self.assertEqual((await reopened.get_image(image["id"]))["positive_prompt"], "positive")
            self.assertTrue(await reopened.delete_image(image["id"]))
            self.assertEqual((await reopened.list_images())["total"], 0)
            self.assertEqual((await reopened.get_batch("batch"))["id"], "batch")
            await reopened.close()

    async def test_corrupt_database_is_backed_up_and_rebuilt(self):
        # 回归 #P2-4:坏数据库不再阻断启动,改名备份后重建并给出警告。
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            path.write_bytes(b"this is not a sqlite database")
            store = HistoryStore(path)
            try:
                self.assertEqual(len(store.load_warnings), 1)
                self.assertIn("history.sqlite3.corrupt.bak", store.load_warnings[0])
                self.assertTrue(path.with_name("history.sqlite3.corrupt.bak").is_file())
                self.assertEqual((await store.list_images())["total"], 0)
            finally:
                await store.close()

    async def test_read_only_history_rejects_generation_before_submit(self):
        def blocked():
            raise RuntimeError("history is read-only")
        with tempfile.TemporaryDirectory() as directory:
            history = HistoryStore(Path(directory) / "history.sqlite3")
            comfy = FakeComfy()
            manager = BatchManager(FakeTemplates(), history, comfy)
            history.ensure_writable = blocked
            try:
                with self.assertRaisesRegex(RuntimeError, "read-only"):
                    await manager.start({**DEFAULT_SETTINGS, "count": 1})
                self.assertEqual(comfy.submissions, [])
                self.assertFalse(manager.active())
            finally:
                await history.close()

    async def test_running_batch_is_marked_interrupted_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            store = HistoryStore(path)
            await store.create_batch("batch", 2, DEFAULT_SETTINGS)
            await store.close()
            reopened = HistoryStore(path)
            self.assertEqual((await reopened.get_batch("batch"))["status"], "interrupted")
            await reopened.close()


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
        await self.history.close()
        self.temp.cleanup()

    async def test_generates_exact_count_sequentially(self):
        await self.manager.start({**DEFAULT_SETTINGS, "count": 3})
        result = await self.manager.wait()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["completed"], 3)
        self.assertEqual([item["sequence"] for item in self.comfy.submissions], [1, 2, 3])
        self.assertTrue(all(item["filename_prefix"].endswith("/image") for item in self.comfy.submissions))
        self.assertEqual((await self.history.list_images())["total"], 3)

    async def test_v4_each_image_and_replay_get_fresh_execution_identity_with_fixed_seeds(self):
        settings = {**DEFAULT_SETTINGS, "count": 2}
        seeds = {"sample_seed": 123456, "prompt_seed": 98765}
        with patch("anima_webui.runner.secrets.randbelow", side_effect=AssertionError("sampling seeds must stay fixed")):
            await self.manager.start(settings, seeds=seeds)
            await self.manager.wait()
            await self.manager.start({**settings, "count": 1}, seeds=seeds, regeneration={
                "mode": "replay", "frozen_positive_prompt": "same frozen prompt",
                "frozen_negative_prompt": "same frozen negative", "fixed_selection": {},
            })
            await self.manager.wait()
        submitted = self.comfy.submissions
        contexts = [item["extra_data"]["extra_pnginfo"]["anima_random_webui"]["execution_context"] for item in submitted]
        self.assertEqual(len({item["execution_id"] for item in contexts}), 3)
        self.assertEqual(len({item["execution_nonce"] for item in contexts}), 3)
        for payload, context in zip(submitted, contexts):
            self.assertEqual(uuid.UUID(context["execution_id"]).version, 4)
            self.assertTrue(0 <= context["execution_nonce"] <= MAX_EXECUTION_NONCE)
            self.assertEqual(payload["execution_nonce"], context["execution_nonce"])
            self.assertEqual((payload["sample_seed"], payload["prompt_seed"]), (123456, 98765))
            self.assertNotIn("execution_nonce", payload["settings"])
            self.assertNotIn("execution_context", payload["settings"])
        images = (await self.history.list_images())["items"]
        self.assertEqual({image["execution_context"]["execution_id"] for image in images},
                         {context["execution_id"] for context in contexts})
        self.assertEqual(self.manager.snapshot()["execution_context"], contexts[-1])
        self.assertNotIn("execution_context", settings)


    def test_execution_nonce_is_unique_with_frozen_and_reversed_clock(self):
        with patch("anima_webui.runner._last_execution_nonce", 100), patch(
            "anima_webui.runner.time.time_ns", side_effect=[100_000, 100_000, 99_000]
        ):
            contexts = [new_execution_context() for _ in range(3)]
        self.assertEqual([context["execution_nonce"] for context in contexts], [101, 102, 103])
        self.assertEqual(len({context["execution_id"] for context in contexts}), 3)

    async def test_second_start_is_queued_and_runs_after_first(self):
        # 0.2.0 起,运行中再开批次不再 409,而是进入队列自动接续。
        self.comfy.block = asyncio.Event()
        await self.manager.start({**DEFAULT_SETTINGS, "count": 2})
        await asyncio.sleep(0)
        queued_settings = {
            **DEFAULT_SETTINGS,
            "count": 1,
            "sampler_name": "euler",
            "scheduler": "karras",
        }
        self.comfy.available_samplers.append("euler")
        self.comfy.available_schedulers.append("karras")
        queued = await self.manager.start(queued_settings)
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(queued["position"], 1)
        self.assertEqual(len(self.manager.queue), 1)
        self.assertEqual(self.manager.queue[0]["settings"]["sampler_name"], "euler")
        self.assertEqual(self.manager.queue[0]["settings"]["scheduler"], "karras")
        self.comfy.block.set()
        result = await self.manager.wait()
        # 队列自动接续:两个批次共 3 张图,全部按序生成
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.comfy.submissions), 3)
        self.assertEqual(self.comfy.submissions[-1]["settings"]["sampler_name"], "euler")
        self.assertEqual(self.comfy.submissions[-1]["settings"]["scheduler"], "karras")
        self.assertEqual(len(self.manager.queue), 0)

    async def test_queue_is_full_at_limit(self):
        from anima_webui.runner import MAX_QUEUE

        self.comfy.block = asyncio.Event()
        await self.manager.start({**DEFAULT_SETTINGS, "count": 1})
        await asyncio.sleep(0)
        for _ in range(MAX_QUEUE):
            await self.manager.start({**DEFAULT_SETTINGS, "count": 1})
        with self.assertRaises(BatchConflict):
            await self.manager.start({**DEFAULT_SETTINGS, "count": 1})
        self.manager.queue.clear()
        self.comfy.block.set()
        await self.manager.wait()

    async def test_stop_clears_queue_by_default(self):
        self.comfy.block = asyncio.Event()
        state = await self.manager.start({**DEFAULT_SETTINGS, "count": 3})
        while not self.comfy.submissions:
            await asyncio.sleep(0)
        await self.manager.start({**DEFAULT_SETTINGS, "count": 5})
        self.assertEqual(len(self.manager.queue), 1)
        await self.manager.request_stop(state["id"])
        self.assertEqual(len(self.manager.queue), 0)
        self.comfy.block.set()
        result = await self.manager.wait()
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(len(self.comfy.submissions), 1)

    async def test_stop_with_keep_queue_continues_next_batch(self):
        # 回归:clearQueue=false(只停当前批次)后,stop_requested 曾让
        # _advance_queue 直接返回,队列中的批次被永久卡死。
        self.comfy.block = asyncio.Event()
        self.comfy.honor_abort = True
        seeds = {"sample_seed": 123, "prompt_seed": 456}
        state = await self.manager.start({**DEFAULT_SETTINGS, "count": 3}, seeds=seeds)
        while not self.comfy.submissions:
            await asyncio.sleep(0)
        await self.manager.start({**DEFAULT_SETTINGS, "count": 1}, seeds=seeds)
        self.assertEqual(len(self.manager.queue), 1)
        await self.manager.request_stop(state["id"], clear_queue=False)
        self.comfy.block.set()
        result = await self.manager.wait()
        # 队列中的批次必须自动接续并跑完:最终状态来自接续批次
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.manager.queue), 0)
        self.assertEqual(len(self.comfy.submissions), 2)
        self.assertEqual((await self.history.get_batch(state["id"]))["status"], "stopped")
        first, second = self.comfy.submissions
        self.assertNotEqual(first["execution_nonce"], second["execution_nonce"])
        self.assertEqual([(item["sample_seed"], item["prompt_seed"]) for item in (first, second)], [(123, 456), (123, 456)])

    async def test_remove_queued_entry(self):
        self.comfy.block = asyncio.Event()
        await self.manager.start({**DEFAULT_SETTINGS, "count": 1})
        await asyncio.sleep(0)
        queued = await self.manager.start({**DEFAULT_SETTINGS, "count": 1})
        self.assertTrue(self.manager.remove_queued(queued["queue_id"]))
        self.assertFalse(self.manager.remove_queued("queue_missing"))
        self.assertEqual(self.manager.queue_snapshot(), [])
        self.comfy.block.set()
        await self.manager.wait()
        self.assertEqual(len(self.comfy.submissions), 1)

    async def test_start_race_during_validation_yields_serial_batches(self):
        # 回归 #P1-1(队列版):并发 start 被锁串行化,绝不出现两个 _run 并发;
        # 第二个进入队列并在第一个结束后自动接续。
        gate = asyncio.Event()
        original_lora_filenames = self.comfy.lora_filenames

        async def slow_lora_filenames():
            await gate.wait()
            return await original_lora_filenames()

        self.comfy.lora_filenames = slow_lora_filenames
        self.comfy.block = asyncio.Event()  # 让第一个批次保持运行,确保第二个进入队列
        first = asyncio.create_task(self.manager.start({**DEFAULT_SETTINGS, "count": 1}))
        second = asyncio.create_task(self.manager.start({**DEFAULT_SETTINGS, "count": 1}))
        await asyncio.sleep(0)
        gate.set()
        first_state = await first
        second_state = await second
        self.assertEqual(first_state["status"], "running")
        self.assertEqual(second_state["status"], "queued")
        self.comfy.block.set()
        await self.manager.wait()
        self.assertEqual(len(self.comfy.submissions), 2)
        self.assertEqual([item["sequence"] for item in self.comfy.submissions], [1, 1])

    async def test_progress_monitor_updates_state_and_preview(self):
        # 0.2.0:websocket 进度流应更新 state 里的采样进度并缓存预览帧。
        comfy = ProgressComfy()
        comfy.block = asyncio.Event()
        manager = BatchManager(FakeTemplates(), self.history, comfy)
        await manager.start({**DEFAULT_SETTINGS, "count": 1})
        for _ in range(500):
            if manager.preview is not None and manager.state.get("progress"):
                break
            await asyncio.sleep(0.01)
        self.assertEqual(manager.state["progress"], {"value": 12, "max": 30})
        seq, content_type, body = manager.preview
        self.assertEqual((content_type, body), ("image/jpeg", b"jpeg-frame"))
        self.assertEqual(manager.state["preview_id"], seq)
        comfy.block.set()
        comfy.release_stream.set()
        result = await manager.wait()
        self.assertEqual(result["status"], "completed")

    async def test_fixed_seeds_reproduce_submission(self):
        # 0.2.0:携带固定种子复现历史图片,提交给 ComfyUI 的种子必须一字不差。
        seeds = {"sample_seed": 123456789, "prompt_seed": 42}
        with patch("anima_webui.runner.secrets.randbelow") as random_seed:
            await self.manager.start({**DEFAULT_SETTINGS, "count": 3}, seeds=seeds)
            result = await self.manager.wait()
        self.assertEqual(result["status"], "completed")
        self.assertEqual([item["sample_seed"] for item in self.comfy.submissions], [123456789] * 3)
        self.assertEqual([item["prompt_seed"] for item in self.comfy.submissions], [42] * 3)
        random_seed.assert_not_called()

    async def test_fixed_sample_seed_keeps_prompt_random_for_each_image(self):
        prompt_seeds = [0, 71, 2**31 - 1]
        with patch("anima_webui.runner.secrets.randbelow", side_effect=prompt_seeds) as random_seed:
            await self.manager.start({**DEFAULT_SETTINGS, "count": 3}, seeds={"sample_seed": 0})
            result = await self.manager.wait()
        self.assertEqual(result["status"], "completed")
        self.assertEqual([item["sample_seed"] for item in self.comfy.submissions], [0] * 3)
        self.assertEqual([item["prompt_seed"] for item in self.comfy.submissions], prompt_seeds)
        self.assertEqual(random_seed.call_args_list, [call(2**31)] * 3)
        history = sorted((await self.history.list_images())["items"], key=lambda item: item["sequence"])
        self.assertEqual([item["sample_seed"] for item in history], [0] * 3)
        self.assertEqual([item["prompt_seed"] for item in history], prompt_seeds)

    async def test_omitted_seeds_randomize_both_values_for_each_image(self):
        with patch("anima_webui.runner.secrets.randbelow", side_effect=[11, 12, 21, 22]) as random_seed:
            await self.manager.start({**DEFAULT_SETTINGS, "count": 2})
            result = await self.manager.wait()
        self.assertEqual(result["status"], "completed")
        self.assertEqual([(item["sample_seed"], item["prompt_seed"]) for item in self.comfy.submissions], [(11, 12), (21, 22)])
        self.assertEqual(random_seed.call_args_list, [call(MAX_SAMPLE_SEED + 1), call(2**31)] * 2)

    async def test_queued_sample_only_seeds_keep_each_batch_policy(self):
        self.comfy.block = asyncio.Event()
        await self.manager.start(
            {**DEFAULT_SETTINGS, "count": 1}, seeds={"sample_seed": 8, "prompt_seed": 9},
        )
        async with asyncio.timeout(2):
            while not self.comfy.submissions:
                await asyncio.sleep(0)
        with patch("anima_webui.runner.secrets.randbelow", side_effect=[12, 13]):
            queued = await self.manager.start(
                {**DEFAULT_SETTINGS, "count": 2}, seeds={"sample_seed": MAX_SAMPLE_SEED},
            )
            self.assertEqual(queued["status"], "queued")
            self.comfy.block.set()
            result = await self.manager.wait()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            [(item["sample_seed"], item["prompt_seed"]) for item in self.comfy.submissions],
            [(8, 9), (MAX_SAMPLE_SEED, 12), (MAX_SAMPLE_SEED, 13)],
        )

    async def test_frozen_replay_bypasses_catalog_and_prompt_rules(self):
        manager = BatchManager(FakeTemplates(), self.history, self.comfy, FakeCatalog())
        regeneration = {
            "mode": "replay",
            "frozen_positive_prompt": "historical final prompt ",
            "frozen_negative_prompt": "historical negative ",
            "fixed_selection": {"pose": [{"id": "deleted", "title": "deleted"}]},
        }
        await manager.start(
            {**DEFAULT_SETTINGS, "count": 1},
            seeds={"sample_seed": 11, "prompt_seed": 12},
            regeneration=regeneration,
        )
        await manager.wait()
        submission = self.comfy.submissions[-1]
        self.assertEqual(submission["resolved_prompt"], "historical final prompt ")
        self.assertEqual(submission["frozen_positive_prompt"], "historical final prompt ")
        self.assertEqual(submission["sample_seed"], 11)
        self.assertEqual(submission["prompt_seed"], 12)
        image = (await self.history.list_images())["items"][0]
        self.assertEqual(image["positive_prompt"], "historical final prompt ")
        self.assertEqual(image["negative_prompt"], "historical negative ")

    async def test_content_redraw_keeps_unselected_dimensions_and_changes_selected(self):
        manager = BatchManager(FakeTemplates(), self.history, self.comfy, RedrawCatalog())
        original = {
            "character": [{"id": "character:old", "title": "character:old", "prompt": "character:old"}],
            "pose": [{"id": "pose:old", "title": "pose:old", "prompt": "pose:old"}],
        }
        regeneration = {
            "mode": "content_redraw",
            "redraw_sections": ["pose"],
            "fixed_selection": {"character": original["character"]},
            "original_selection": original,
        }
        await manager.start({**DEFAULT_SETTINGS, "count": 1}, regeneration=regeneration)
        await manager.wait()
        submitted = self.comfy.submissions[-1]
        self.assertEqual(submitted["resolved_selection"]["character"][0]["prompt"], original["character"][0]["prompt"])
        self.assertEqual(submitted["resolved_selection"]["character"][0]["snapshot_version"], 2)
        self.assertEqual(submitted["resolved_selection"]["pose"][0]["id"], "pose:new")

    async def test_invalid_seeds_are_rejected(self):
        for bad in (
            {}, {"prompt_seed": 1}, {"sample_seed": -1}, {"sample_seed": MAX_SAMPLE_SEED + 1},
            {"sample_seed": True}, {"sample_seed": 1.0}, {"x": 1}, [1, 2],
            {"sample_seed": 1, "prompt_seed": None}, {"sample_seed": 1, "prompt_seed": True},
            {"sample_seed": 1, "prompt_seed": -1}, {"sample_seed": 1, "prompt_seed": 2**31},
        ):
            with self.subTest(seeds=bad):
                with self.assertRaises(ValueError):
                    await self.manager.start({**DEFAULT_SETTINGS, "count": 1}, seeds=bad)
        self.assertIsNone(self.manager.state)

    async def test_stop_during_wait_interrupts_comfy_and_stops(self):
        # 回归 #P1-3:等待渲染期间请求停止,应立即中断 ComfyUI 而不是等当前图完成。
        self.comfy.block = asyncio.Event()
        self.comfy.honor_abort = True
        state = await self.manager.start({**DEFAULT_SETTINGS, "count": 3})
        while not self.comfy.submissions:
            await asyncio.sleep(0)
        await self.manager.request_stop(state["id"])
        result = await self.manager.wait()
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["completed"], 0)
        self.assertTrue(self.comfy.interrupted)
        self.assertEqual((await self.history.get_batch(state["id"]))["status"], "stopped")

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
        seeds = {"sample_seed": 123, "prompt_seed": 456}
        await self.manager.start({**DEFAULT_SETTINGS, "count": 4}, seeds=seeds)
        result = await self.manager.wait()
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["completed"], 1)
        self.assertEqual(len(self.comfy.submissions), 2)
        self.assertIn("render failed", result["error"])
        failed = self.comfy.submissions[-1]
        await self.manager.start({**DEFAULT_SETTINGS, "count": 1}, seeds=seeds)
        self.assertEqual((await self.manager.wait())["status"], "completed")
        restarted = self.comfy.submissions[-1]
        self.assertEqual(len(self.comfy.submissions), 3)
        self.assertNotEqual(failed["execution_nonce"], restarted["execution_nonce"])
        self.assertEqual((restarted["sample_seed"], restarted["prompt_seed"]), (123, 456))

    async def test_history_uses_actual_comfy_prompt_when_catalog_is_present(self):
        templates = FakeTemplates()
        templates.resource_paths = {
            "anima-base-v1.0.safetensors": "Anima/anima-base-v1.0.safetensors",
            "qwen_3_06b_base.safetensors": "TextEncoders/qwen_3_06b_base.safetensors",
            "qwen_image_vae.safetensors": "VAE/qwen_image_vae.safetensors",
            "seedvr2_3b_int8_convrot.safetensors": "SeedVR2/seedvr2_3b_int8_convrot.safetensors",
            "ema_vae_fp16.safetensors": "SeedVR2/ema_vae_fp16.safetensors",
            "4x_foolhardy_Remacri.pth": "Upscale/4x_foolhardy_Remacri.pth",
        }
        self.comfy.available_models = ["Anima/anima-base-v1.0.safetensors"]
        manager = BatchManager(templates, self.history, self.comfy, FakeCatalog())

        await manager.start({**DEFAULT_SETTINGS, "count": 1})
        await manager.wait()

        image = (await self.history.list_images())["items"][0]
        self.assertEqual(image["positive_prompt"], "prompt 1")
        self.assertEqual(image["resolved_prompt"], "prompt 1")
        self.assertEqual(
            image["bound_resources"],
            {
                "model_profile": "anima",
                "unet": "Anima/anima-base-v1.0.safetensors",
                "clip": "TextEncoders/qwen_3_06b_base.safetensors",
                "vae": "VAE/qwen_image_vae.safetensors",
                "loras": [],
            },
        )

    async def test_missing_lora_is_rejected_before_batch_creation(self):
        settings = {
            **DEFAULT_SETTINGS,
            "loras": [{"filename": "deleted.safetensors", "enabled": True, "strength": 1}],
        }

        with self.assertRaisesRegex(ValueError, "deleted.safetensors"):
            await self.manager.start(settings)

        self.assertIsNone(self.manager.state)
        self.assertEqual((await self.history.list_images())["total"], 0)

    async def test_missing_models_are_rejected_before_batch_creation(self):
        self.comfy.available_models = []
        with self.assertRaisesRegex(ValueError, "主模型不存在"):
            await self.manager.start(DEFAULT_SETTINGS)
        self.assertIsNone(self.manager.state)

        self.comfy.available_models = [DEFAULT_SETTINGS["model_name"]]
        self.comfy.available_upscalers = []
        with self.assertRaisesRegex(ValueError, "高清修复模型不存在"):
            await self.manager.start({**DEFAULT_SETTINGS, "upscalers": {"pixel_hires": {"enabled": True}}})
        self.assertIsNone(self.manager.state)

    def test_nvidia_preflight_requires_node_or_plugin_but_no_model_file(self):
        settings = {
            **DEFAULT_SETTINGS,
            "pipeline_version": 4,
            "upscalers": {
                **DEFAULT_SETTINGS["upscalers"],
                "nvidia": {"enabled": True},
            },
        }
        resources = {
            "models": [DEFAULT_SETTINGS["model_name"]],
            "samplers": [DEFAULT_SETTINGS["sampler_name"]],
            "schedulers": [DEFAULT_SETTINGS["scheduler"]],
            "nodes": {"RTXVideoSuperResolution": True},
            "plugins": {},
            # An RTXVideoSuperResolution node has no upscale model input.
            "upscale_models": [],
        }
        self.assertEqual(BatchManager._resource_inventory_issues(settings, resources), [])

        resources["nodes"] = {"RTXVideoSuperResolution": False}
        resources["plugins"] = {"nvidia_upscale": True}
        self.assertTrue(BatchManager._resource_inventory_issues(settings, resources))

        resources["plugins"] = {"nvidia_upscale": False}
        issues = BatchManager._resource_inventory_issues(settings, resources)
        self.assertEqual(
            [(item["type"], item["name"]) for item in issues],
            [("nvidia_node", "RTXVideoSuperResolution")],
        )

    def test_v4_preflight_checks_selected_models_and_each_enabled_sampler(self):
        from anima_webui.workflow import validate_settings

        settings = validate_settings(
            {
                "model_profile": "anima",
                "model_name": "Anima/custom.safetensors",
                "upscalers": {
                    "latent": {
                        "enabled": True,
                        "sampler": {
                            "sampler_name": "dpmpp_2m",
                            "scheduler": "karras",
                        },
                    },
                    "seedvr2": {
                        "enabled": True,
                        "model_name": "SeedVR2/custom.safetensors",
                        "vae_name": "SeedVR2/custom-vae.safetensors",
                        "sampler": {"sampler_name": "heun", "scheduler": "normal"},
                    },
                    "face_refine": {
                        "enabled": True,
                        "sampler": "heun",
                        "scheduler": "normal",
                    },
                },
            }
        )
        resources = {
            "models": ["Anima/custom.safetensors"],
            "clips": None,
            "vaes": None,
            "seedvr2_models": ["SeedVR2/custom.safetensors"],
            "seedvr2_vaes": ["SeedVR2/custom-vae.safetensors"],
            "samplers": [settings["sampler_name"]],
            "schedulers": [settings["scheduler"]],
            "nodes": {
                "SeedVR2": True,
                "RTXVideoSuperResolution": True,
                "ForbiddenVisionFaceProcessorIntegrated": True,
            },
            "plugins": {},
        }
        issues = BatchManager._resource_inventory_issues(settings, resources)
        self.assertEqual(
            {(item["type"], item["name"]) for item in issues},
            {
                ("latent_sampler", "dpmpp_2m"),
                ("latent_scheduler", "karras"),
                ("seedvr2_sampler", "heun"),
                ("seedvr2_scheduler", "normal"),
                ("face_refine_sampler", "heun"),
                ("face_refine_scheduler", "normal"),
            },
        )

        resources["samplers"].extend(["dpmpp_2m", "heun"])
        resources["schedulers"].extend(["karras", "normal"])
        self.assertEqual(BatchManager._resource_inventory_issues(settings, resources), [])

        resources["seedvr2_models"] = []
        issues = BatchManager._resource_inventory_issues(settings, resources)
        self.assertIn(("seedvr2", "SeedVR2/custom.safetensors"), {
            (item["type"], item["name"]) for item in issues
        })




if __name__ == "__main__":
    unittest.main()
