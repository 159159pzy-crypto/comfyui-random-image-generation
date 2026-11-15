import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from anima_webui.history import HistoryStore
from anima_webui.comfy import ComfyCleanupSkipped, ComfyError
from anima_webui.runner import BatchConflict, BatchManager
from anima_webui.workflow import DEFAULT_SETTINGS
from test_runner import FakeComfy, FakeTemplates


class RunnerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.history = HistoryStore(Path(self.temp.name) / "history.sqlite3")
        self.comfy = FakeComfy()
        self.manager = BatchManager(FakeTemplates(), self.history, self.comfy)

    async def asyncTearDown(self):
        if self.comfy.block:
            self.comfy.block.set()
        await self.manager.shutdown()
        await self.history.close()
        self.temp.cleanup()

    async def submitted(self):
        async with asyncio.timeout(2):
            while not self.comfy.submissions:
                await asyncio.sleep(0)

    async def test_generation_rejects_shutdown_without_side_effects(self):
        await self.manager.shutdown()
        with self.assertRaises(BatchConflict):
            await self.manager.start(DEFAULT_SETTINGS)
        self.assertEqual(self.comfy.submissions, [])

    async def test_shutdown_during_preflight_rejects_unsubmitted_batch(self):
        preflight = asyncio.Event()
        release = asyncio.Event()
        original = self.comfy.lora_filenames

        async def blocked_inventory():
            preflight.set()
            await release.wait()
            return await original()

        with patch.object(self.comfy, "lora_filenames", blocked_inventory):
            starting = asyncio.create_task(self.manager.start(DEFAULT_SETTINGS))
            await preflight.wait()
            closing = asyncio.create_task(self.manager.shutdown())
            await asyncio.sleep(0)
            release.set()
            with self.assertRaises(BatchConflict):
                await starting
            await closing
        self.assertEqual(self.comfy.submissions, [])
        self.assertIsNone(self.manager.state)




    async def test_shutdown_stops_current_and_clears_waiting_generations(self):
        self.comfy.block = asyncio.Event()
        self.comfy.honor_abort = True
        state = await self.manager.start({**DEFAULT_SETTINGS, "count": 2})
        await self.submitted()
        await self.manager.start({**DEFAULT_SETTINGS, "count": 1})
        await self.manager.shutdown()

        self.assertTrue(self.manager.task.done())
        self.assertEqual(self.manager.queue, [])
        self.assertEqual(len(self.comfy.submissions), 1)
        self.assertEqual((await self.history.get_batch(state["id"]))["status"], "stopped")

    async def test_shutdown_waits_for_cancelled_worker_database_finalization(self):
        self.comfy.block = asyncio.Event()  # This fake ignores should_abort.
        state = await self.manager.start({**DEFAULT_SETTINGS, "count": 1})
        await self.submitted()
        finalizing = asyncio.Event()
        release = asyncio.Event()
        original = self.history.update_batch

        async def update(batch_id, **kwargs):
            if kwargs.get("status") == "stopped":
                finalizing.set()
                await release.wait()
            return await original(batch_id, **kwargs)

        with patch.object(self.history, "update_batch", update), \
                patch("anima_webui.runner.SHUTDOWN_GRACE", 0.01):
            closing = asyncio.create_task(self.manager.shutdown())
            await asyncio.wait_for(finalizing.wait(), 2)
            self.assertFalse(closing.done())
            release.set()
            await closing
        self.assertTrue(self.manager.task.done())
        self.assertEqual((await self.history.get_batch(state["id"]))["status"], "stopped")

    async def test_shutdown_waits_for_websocket_cleanup(self):
        connected = asyncio.Event()
        disconnecting = asyncio.Event()
        release = asyncio.Event()

        async def stream(client_id):
            connected.set()
            try:
                await asyncio.Event().wait()
                yield {}
            finally:
                disconnecting.set()
                await release.wait()

        self.comfy.block = asyncio.Event()
        self.comfy.honor_abort = True
        with patch.object(self.comfy, "progress_stream", stream, create=True):
            await self.manager.start({**DEFAULT_SETTINGS, "count": 1})
            await connected.wait()
            closing = asyncio.create_task(self.manager.shutdown())
            await asyncio.wait_for(disconnecting.wait(), 2)
            self.assertFalse(closing.done())
            release.set()
            await closing
        self.assertEqual(self.manager._monitor_tasks, set())


    async def test_slow_remote_cancel_has_one_total_time_budget(self):
        cancel_started = asyncio.Event()

        async def cancel(prompt_id):
            cancel_started.set()
            await asyncio.Event().wait()

        self.manager.state = {"kind": "generation", "prompt_id": "owned"}
        with patch.object(self.comfy, "cancel_prompt", cancel, create=True), \
                patch("anima_webui.runner.CANCEL_PROMPT_TIMEOUT", 0.01):
            await asyncio.wait_for(self.manager._interrupt_current(), 1)
        self.assertTrue(cancel_started.is_set())
        self.assertIn("远端任务可能继续", self.manager.state["cancel_warning"])


    def memory_client(self):
        self.comfy.cancel_prompt = AsyncMock()
        self.comfy.prompt_queued = AsyncMock(return_value=False)
        self.comfy.queue_idle = AsyncMock(return_value=True)
        self.comfy.cleanup_memory = AsyncMock()
        return self.comfy


    async def stoppable_batch(self):
        self.comfy.block = asyncio.Event()
        self.comfy.honor_abort = True
        state = await self.manager.start({**DEFAULT_SETTINGS, "count": 2})
        await self.submitted()
        return state


    async def test_manual_stop_cleans_once_after_confirmed_cancel_and_keeps_history_clean(self):
        client = self.memory_client()
        order = []
        client.cancel_prompt.side_effect = lambda _: order.append("cancel")
        client.prompt_queued.side_effect = lambda _: order.append("confirm") or False
        client.queue_idle.side_effect = lambda: order.append("idle") or True
        client.cleanup_memory.side_effect = lambda: order.append("cleanup")
        state = await self.stoppable_batch()
        await asyncio.gather(self.manager.request_stop(state["id"]),
                             self.manager.request_stop(state["id"]))
        result = await self.manager.wait()
        await self.manager.request_stop(state["id"])
        self.assertEqual(order, ["cancel", "confirm", "idle", "cleanup"])
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["memory_release"], "completed")
        self.assertEqual(result["memory_release_message"], "清缓存与垃圾回收已完成")
        record = await self.history.get_batch(state["id"])
        self.assertEqual(record["status"], "stopped")
        self.assertNotIn("memory_release", record["settings"])


    async def test_stop_before_submission_can_clean_without_unloading_models(self):
        client = self.memory_client()
        state = await self.manager.start({**DEFAULT_SETTINGS, "count": 2})
        await self.manager.request_stop(state["id"])
        result = await self.manager.wait()
        self.assertEqual(client.submissions, [])
        client.cancel_prompt.assert_not_awaited()
        client.queue_idle.assert_awaited_once()
        client.cleanup_memory.assert_awaited_once()
        self.assertEqual(result["cancel_state"], "not_requested")
        self.assertEqual(result["memory_release"], "completed")


    async def test_remote_busy_unconfirmed_or_invalid_queue_never_releases(self):
        for queue_result, cancel_error in ((False, None), (None, None),
                                          (ComfyError("queue unavailable"), None),
                                          (True, ComfyError("cancel unavailable"))):
            with self.subTest(queue=queue_result, cancel=cancel_error):
                client = self.memory_client()
                client.submissions.clear()
                client.cancel_prompt.side_effect = cancel_error
                if isinstance(queue_result, Exception):
                    client.queue_idle.side_effect = queue_result
                else:
                    client.queue_idle.return_value = queue_result
                state = await self.stoppable_batch()
                await self.manager.request_stop(state["id"])
                result = await self.manager.wait()
                self.assertEqual(result["status"], "stopped")
                self.assertEqual(result["memory_release"], "skipped")
                client.cleanup_memory.assert_not_awaited()
                if cancel_error:
                    client.queue_idle.assert_not_awaited()
                    self.assertEqual(result["cancel_state"], "unconfirmed")


    async def test_release_errors_and_timeout_do_not_change_stopped_or_retry(self):
        for timeout in (False, True):
            with self.subTest(timeout=timeout):
                client = self.memory_client()
                client.submissions.clear()

                async def cleanup():
                    if timeout:
                        await asyncio.Event().wait()
                    raise ComfyError("sensitive transport detail")

                client.cleanup_memory.side_effect = cleanup
                state = await self.stoppable_batch()
                with patch("anima_webui.runner.MEMORY_RELEASE_TIMEOUT", .02):
                    await self.manager.request_stop(state["id"])
                    result = await asyncio.wait_for(self.manager.wait(), 1)
                self.assertEqual(result["status"], "stopped")
                self.assertEqual(result["error"], "")
                self.assertEqual(result["memory_release"], "failed")
                self.assertIn("完成情况未确认", result["memory_release_message"])
                self.assertNotIn("sensitive", result["memory_release_message"])
                await self.manager.request_stop(state["id"])
                client.cleanup_memory.assert_awaited_once()


    async def test_extension_busy_or_unavailable_is_skipped_without_changing_stop(self):
        for message in ("ComfyUI 队列忙碌，已跳过清缓存与垃圾回收",
                        "清理扩展尚未就绪，请通过启动器重启 ComfyUI"):
            with self.subTest(message=message):
                client = self.memory_client()
                client.submissions.clear()
                client.cleanup_memory.side_effect = ComfyCleanupSkipped(message)
                state = await self.stoppable_batch()
                await self.manager.request_stop(state["id"])
                result = await self.manager.wait()
                self.assertEqual(result["status"], "stopped")
                self.assertEqual(result["error"], "")
                self.assertEqual(result["memory_release"], "skipped")
                self.assertEqual(result["memory_release_message"], message)
                client.cleanup_memory.assert_awaited_once()


    async def test_queue_check_and_release_share_one_total_timeout(self):
        client = self.memory_client()

        async def busy_check():
            await asyncio.Event().wait()

        client.queue_idle.side_effect = busy_check
        state = await self.stoppable_batch()
        with patch("anima_webui.runner.MEMORY_RELEASE_TIMEOUT", .02):
            await self.manager.request_stop(state["id"])
            result = await asyncio.wait_for(self.manager.wait(), 1)
        self.assertEqual(result["memory_release"], "skipped")
        client.cleanup_memory.assert_not_awaited()


    async def test_keep_queue_skips_release_and_next_batch_resets_runtime_fields(self):
        client = self.memory_client()
        state = await self.stoppable_batch()
        await self.manager.start({**DEFAULT_SETTINGS, "count": 1})
        stopped_state = self.manager.state
        await self.manager.request_stop(state["id"], clear_queue=False)
        client.block.set()
        result = await self.manager.wait()
        self.assertEqual(stopped_state["memory_release"], "skipped")
        self.assertIn("本地任务", stopped_state["memory_release_message"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["memory_release"], "not_requested")
        client.queue_idle.assert_not_awaited()
        client.cleanup_memory.assert_not_awaited()


    async def test_completion_pending_removal_and_shutdown_never_release(self):
        client = self.memory_client()
        state = await self.manager.start({**DEFAULT_SETTINGS, "count": 1})
        await self.manager.wait()
        await self.manager.request_stop(state["id"])
        self.assertEqual(self.manager.state["memory_release"], "not_requested")
        client.submissions.clear()
        await self.stoppable_batch()
        queued = await self.manager.start({**DEFAULT_SETTINGS, "count": 1})
        self.assertTrue(self.manager.remove_queued(queued["queue_id"]))
        self.assertEqual(self.manager.state["memory_release"], "not_requested")
        await self.manager.shutdown()
        self.assertEqual(self.manager.state["status"], "stopped")
        self.assertEqual(self.manager.state["memory_release"], "not_requested")
        client.cleanup_memory.assert_not_awaited()


    async def test_internal_prompt_cancellation_does_not_request_memory_release(self):
        client = self.memory_client()
        state = await self.stoppable_batch()
        # Prompt-processing cancellation calls the private stop path, even
        # when its owning batch is an image generation.
        async with self.manager._start_lock:
            await self.manager._request_stop(state["id"], clear_queue=False)
        result = await self.manager.wait()
        self.assertEqual(result["memory_release"], "not_requested")
        client.cleanup_memory.assert_not_awaited()


    async def test_concurrent_and_repeated_cancel_share_one_confirmed_operation(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def cancel(prompt_id):
            self.assertEqual(prompt_id, "owned")
            entered.set()
            await release.wait()

        self.manager.state = {"id": "batch", "kind": "generation", "prompt_id": "owned",
                              "cancel_warning": "previous unconfirmed warning"}
        with patch.object(self.comfy, "cancel_prompt", AsyncMock(side_effect=cancel), create=True) as cancel_mock, \
                patch.object(self.comfy, "prompt_queued", AsyncMock(return_value=False), create=True) as queued:
            first = asyncio.create_task(self.manager._interrupt_current())
            await entered.wait()
            second = asyncio.create_task(self.manager._interrupt_current())
            await asyncio.sleep(0)
            self.assertEqual(self.manager.state["cancel_state"], "pending")
            release.set()
            await asyncio.gather(first, second)
            await self.manager._interrupt_current()
            cancel_mock.assert_awaited_once_with("owned")
            queued.assert_awaited_once_with("owned")
        self.assertEqual(self.manager.state["cancel_state"], "confirmed")
        self.assertNotIn("cancel_warning", self.manager.state)


    async def test_cancel_acknowledgement_without_queue_check_is_unconfirmed(self):
        self.manager.state = {"id": "batch", "kind": "generation", "prompt_id": "owned"}
        with patch.object(self.comfy, "cancel_prompt", AsyncMock(), create=True):
            await self.manager._interrupt_current()
        self.assertEqual(self.manager.state["cancel_state"], "unconfirmed")
        self.assertIn("远端任务可能继续", self.manager.state["cancel_warning"])
        self.assertFalse(self.comfy.interrupted)


    async def test_failed_queue_read_does_not_confirm_remote_stop(self):
        for result in (ComfyError("queue unavailable"), None):
            self.manager.state = {"id": str(result), "kind": "generation", "prompt_id": "owned"}
            query = AsyncMock(side_effect=result) if isinstance(result, Exception) else AsyncMock(return_value=result)
            with patch.object(self.comfy, "cancel_prompt", AsyncMock(), create=True), \
                    patch.object(self.comfy, "prompt_queued", query, create=True):
                await self.manager._interrupt_current()
            self.assertEqual(self.manager.state["cancel_state"], "unconfirmed")


    async def test_cancelled_job_remaining_in_remote_queue_is_unconfirmed(self):
        self.manager.state = {"id": "batch", "kind": "generation", "prompt_id": "owned"}
        with patch.object(self.comfy, "cancel_prompt", AsyncMock(), create=True), \
                patch.object(self.comfy, "prompt_queued", AsyncMock(return_value=True), create=True), \
                patch("anima_webui.runner.CANCEL_PROMPT_TIMEOUT", 0.01):
            await self.manager._interrupt_current()
        self.assertEqual(self.manager.state["cancel_state"], "unconfirmed")


    async def test_late_cancel_result_does_not_modify_another_batch_or_prompt(self):
        for replace_state in (False, True):
            entered, release = asyncio.Event(), asyncio.Event()

            async def cancel(_):
                entered.set()
                await release.wait()

            self.manager.state = {"id": str(replace_state), "kind": "generation", "prompt_id": "old"}
            with patch.object(self.comfy, "cancel_prompt", cancel, create=True), \
                    patch.object(self.comfy, "prompt_queued", AsyncMock(return_value=False), create=True):
                stopping = asyncio.create_task(self.manager._interrupt_current())
                await entered.wait()
                if replace_state:
                    self.manager.state = {"id": "new", "prompt_id": "new", "cancel_state": "not_requested"}
                else:
                    self.manager.state.update(prompt_id="new", cancel_state="not_requested")
                release.set()
                await stopping
            self.assertEqual(self.manager.state["cancel_state"], "not_requested")
            self.assertNotIn("cancel_warning", self.manager.state)


    async def test_stop_request_and_aborted_wait_cancel_owned_prompt_only_once(self):
        self.comfy.block = asyncio.Event()
        self.comfy.honor_abort = True
        entered, release = asyncio.Event(), asyncio.Event()

        async def cancel(_):
            entered.set()
            await release.wait()

        with patch.object(self.comfy, "cancel_prompt", AsyncMock(side_effect=cancel), create=True) as cancel_mock, \
                patch.object(self.comfy, "prompt_queued", AsyncMock(return_value=False), create=True):
            state = await self.manager.start({**DEFAULT_SETTINGS, "count": 2})
            await self.submitted()
            await self.manager.start({**DEFAULT_SETTINGS, "count": 1})
            stopping = asyncio.create_task(self.manager.request_stop(state["id"]))
            await entered.wait()
            await asyncio.sleep(0)
            self.assertEqual(self.manager.queue, [])
            release.set()
            await stopping
            result = await self.manager.wait()
            cancel_mock.assert_awaited_once_with("prompt-1")
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["cancel_state"], "confirmed")
        self.assertEqual(len(self.comfy.submissions), 1)


    async def test_no_accepted_prompt_needs_no_remote_cancellation(self):
        self.manager.state = {"id": "batch", "kind": "generation", "prompt_id": ""}
        with patch.object(self.comfy, "cancel_prompt", AsyncMock(), create=True) as cancel:
            await self.manager._interrupt_current()
            cancel.assert_not_awaited()
        self.assertEqual(self.manager.state["cancel_state"], "not_requested")
        self.assertFalse(self.comfy.interrupted)
