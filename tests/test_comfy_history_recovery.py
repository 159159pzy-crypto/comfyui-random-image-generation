"""Recover accepted jobs through read-only polling without resubmission."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, call, patch

from aiohttp import web
from aiohttp.test_utils import TestServer

from anima_webui.comfy import ComfyAborted, ComfyClient, ComfyError, ComfyTransportError
from anima_webui.history import HistoryStore
from anima_webui.runner import BatchManager
from anima_webui.workflow import DEFAULT_SETTINGS
import test_runner as runner_fixtures


class HistoryRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.replies = []
        self.entry = {'status': {'status_str': 'success', 'completed': True}, 'outputs': {}}
        app = web.Application()

        async def endpoint(request):
            self.calls.append((request.method, request.path))
            item = self.replies.pop(0)
            if isinstance(item, int):
                return web.Response(status=item, text='temporary failure')
            return web.json_response(item)

        app.router.add_route('*', '/{path:.*}', endpoint)
        self.server = TestServer(app)
        await self.server.start_server()
        self.client = ComfyClient(str(self.server.make_url('/')), poll_interval=.001)

    async def asyncTearDown(self):
        await self.client.close()
        await self.server.close()

    async def test_history_and_queue_transient_errors_recover_same_prompt(self):
        self.replies = [503, {}, 502, {'owned': self.entry}]
        with patch('anima_webui.comfy.HISTORY_RETRY_DELAYS', (0, 0, 0, 0, 0), create=True):
            result = await self.client.wait_for_history('owned')
        self.assertEqual(result, self.entry)
        self.assertTrue(all(method == 'GET' for method, _ in self.calls))
        self.assertEqual(self.calls[-1], ('GET', '/history/owned'))

    async def test_retry_exhaustion_is_bounded_and_keeps_prompt_identity(self):
        self.replies = [503] * 6
        with patch.object(self.client, '_history_delay', new_callable=AsyncMock) as delay:
            with self.assertRaisesRegex(ComfyError, 'owned'):
                await self.client.wait_for_history('owned')
        self.assertEqual(delay.await_args_list, [call(seconds, None) for seconds in (1, 2, 4, 8, 8)])
        self.assertEqual(len(self.calls), 6)

    async def test_retry_budget_resets_only_after_a_complete_healthy_poll(self):
        # Five failures, then a healthy empty-history + running-queue cycle,
        # followed by another outage. Each outage gets its own five retries.
        self.replies = ([503] * 5 + [{}, {'queue_running': [[0, 'owned']], 'queue_pending': []}]
                        + [503] * 5 + [{'owned': self.entry}])
        with patch.object(self.client, '_history_delay', new_callable=AsyncMock) as delay:
            result = await self.client.wait_for_history('owned')
        self.assertEqual(result, self.entry)
        self.assertEqual(delay.await_args_list, [call(seconds, None) for seconds in (
            1, 2, 4, 8, 8, self.client.poll_interval, 1, 2, 4, 8, 8)])

        # A successful history GET alone must not keep resetting the budget
        # while every required queue GET is still failing.
        self.calls.clear()
        self.replies = [{}, 503] * 6
        with patch.object(self.client, '_history_delay', new_callable=AsyncMock) as delay:
            with self.assertRaisesRegex(ComfyError, '结果未确认'):
                await self.client.wait_for_history('owned')
        self.assertEqual(len(self.calls), 12)
        self.assertEqual(delay.await_count, 5)

    async def test_connection_outage_does_not_consume_missing_prompt_timeout(self):
        empty_queue = {'queue_running': [], 'queue_pending': []}
        self.replies = [{}, empty_queue, 503, {}, empty_queue, {'owned': self.entry}]
        started = asyncio.get_running_loop().time()
        with patch('anima_webui.comfy.HISTORY_RETRY_DELAYS', (.06,) * 5):
            result = await self.client.wait_for_history('owned', missing_timeout=.02)
        self.assertEqual(result, self.entry)
        self.assertGreaterEqual(asyncio.get_running_loop().time() - started, .06)

    async def test_permanent_http_failure_and_execution_error_are_not_retried(self):
        for reply in (400, {'owned': {'status': {'status_str': 'error', 'messages': ['boom']}}}):
            self.calls.clear()
            self.replies = [reply]
            with self.assertRaises(ComfyError):
                await self.client.wait_for_history('owned')
            self.assertEqual(len(self.calls), 1)

    async def test_malformed_queue_and_history_shapes_fail_without_retry(self):
        cases = [
            [{}, {}],
            [{}, {'queue_running': []}],
            [{}, {'queue_pending': []}],
            [{}, {'queue_running': {}, 'queue_pending': []}],
            [{}, {'queue_running': None, 'queue_pending': []}],
            [{}, {'queue_running': [], 'queue_pending': 'owned'}],
            [{}, {'queue_running': [], 'queue_pending': [['incomplete']]}],
            [{}, {'queue_running': [[0, {'id': 'owned'}]], 'queue_pending': []}],
            [{}, {'queue_running': [[0, 'owned']], 'queue_pending': None}],
            [{'owned': []}],
            [{'owned': None}],
            [{'owned': {'status': []}}],
            [{'owned': {'status': {'completed': 'false'}}}],
            [{'owned': {'status': {'messages': {}}}}],
            [{'owned': {'outputs': []}}],
            [{'owned': {'outputs': {'output': []}}}],
        ]
        for replies in cases:
            with self.subTest(replies=replies):
                self.calls.clear()
                self.replies = list(replies)
                with patch.object(self.client, '_history_delay', new_callable=AsyncMock) as delay:
                    with self.assertRaisesRegex(ComfyError, '无效'):
                        await self.client.wait_for_history('owned')
                delay.assert_not_awaited()
                self.assertEqual(len(self.calls), len(replies))

    async def test_legacy_missing_history_status_remains_valid(self):
        self.replies = [{}, {'queue_running': [], 'queue_pending': []}, {'owned': {'outputs': {}}}]
        result = await self.client.wait_for_history('owned')
        self.assertEqual(result, {'outputs': {}})
        self.assertEqual(len(self.calls), 3)

    async def test_cancel_acknowledgement_requires_complete_valid_queue_response(self):
        for queue in ({}, {'queue_running': []}, {'queue_pending': []},
                      {'queue_running': [], 'queue_pending': None},
                      {'queue_running': [], 'queue_pending': []}):
            with self.subTest(queue=queue), tempfile.TemporaryDirectory() as directory:
                history = HistoryStore(Path(directory) / 'history.sqlite3')
                manager = BatchManager(runner_fixtures.FakeTemplates(), history, self.client)
                manager.state = {'id': 'batch', 'kind': 'generation', 'prompt_id': 'owned'}
                self.calls.clear()
                self.replies = [queue]
                try:
                    with patch.object(self.client, 'cancel_prompt', new_callable=AsyncMock) as cancel:
                        await manager._interrupt_current()
                        cancel.assert_awaited_once_with('owned')
                    confirmed = queue == {'queue_running': [], 'queue_pending': []}
                    self.assertEqual(manager.state['cancel_state'], 'confirmed' if confirmed else 'unconfirmed')
                    self.assertEqual('cancel_warning' in manager.state, not confirmed)
                    self.assertEqual(self.calls, [('GET', '/queue')])
                finally:
                    await manager.shutdown()
                    await history.close()

    async def test_stop_interrupts_pending_read(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()
        stop = False

        async def blocked(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        self.client._json = blocked
        task = asyncio.create_task(self.client.wait_for_history('owned', should_abort=lambda: stop))
        await started.wait()
        stop = True
        with self.assertRaises(ComfyAborted):
            await asyncio.wait_for(task, .5)
        self.assertTrue(cancelled.is_set())

    async def test_submit_failure_is_never_retried(self):
        self.replies = [503]
        with self.assertRaises(ComfyError):
            await self.client.submit({'prompt': {}})
        self.assertEqual(self.calls, [('POST', '/prompt')])

    async def test_transport_read_failure_retries_and_format_failure_does_not(self):
        calls = 0

        async def read(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ComfyTransportError('connection reset')
            return {'owned': self.entry}

        self.client._json = read
        with patch('anima_webui.comfy.HISTORY_RETRY_DELAYS', (0,) * 5):
            self.assertEqual(await self.client.wait_for_history('owned'), self.entry)
        self.assertEqual(calls, 2)

        async def malformed(*args, **kwargs):
            nonlocal calls
            calls += 1
            raise ComfyError('invalid JSON')

        self.client._json = malformed
        with self.assertRaisesRegex(ComfyError, 'invalid JSON'):
            await self.client.wait_for_history('owned')
        self.assertEqual(calls, 3)

    async def test_recovery_deadline_bounds_a_stalled_retry(self):
        calls = 0
        cancelled = asyncio.Event()

        async def read(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ComfyTransportError('connection reset')
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        self.client._json = read
        with patch('anima_webui.comfy.HISTORY_RETRY_DELAYS', (0,) * 5), \
                patch('anima_webui.comfy.HISTORY_RECOVERY_TIMEOUT', .03):
            with self.assertRaisesRegex(ComfyError, 'owned'):
                await asyncio.wait_for(self.client.wait_for_history('owned'), .5)
        self.assertEqual(calls, 2)
        self.assertTrue(cancelled.is_set())

    async def test_stop_interrupts_retry_backoff(self):
        started = asyncio.Event()
        stop = False

        async def read(*args, **kwargs):
            started.set()
            raise ComfyTransportError('offline')

        self.client._json = read
        task = asyncio.create_task(self.client.wait_for_history('owned', should_abort=lambda: stop))
        await started.wait()
        await asyncio.sleep(0)
        stop = True
        with self.assertRaises(ComfyAborted):
            await asyncio.wait_for(task, .5)

    async def test_cancelling_owner_closes_pending_read(self):
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def read(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        self.client._json = read
        task = asyncio.create_task(self.client.wait_for_history('owned'))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cancelled.is_set())

    async def test_recovery_saves_once_and_exhaustion_keeps_queue_failure_semantics(self):
        for exhaust in (False, True):
            with self.subTest(exhaust=exhaust), tempfile.TemporaryDirectory() as directory:
                fake = runner_fixtures.FakeComfy()
                self.client.lora_filenames = fake.lora_filenames
                self.client.resource_inventory = fake.resource_inventory
                self.client.submit = fake.submit
                image_entry = {**self.entry, 'outputs': {'output': {'images': [
                    {'filename': 'result.png', 'type': 'output'}]}}}
                self.replies = ([503] * 6 if exhaust else [503, {'prompt-1': image_entry}]) + [
                    {'prompt-2': image_entry}]
                self.calls.clear()
                history = HistoryStore(Path(directory) / 'history.sqlite3')
                manager = BatchManager(runner_fixtures.FakeTemplates(), history, self.client)
                # This test exercises the result connection, not websocket previews.
                manager._ensure_monitor = lambda: None
                try:
                    with patch('anima_webui.comfy.HISTORY_RETRY_DELAYS', (0,) * 5):
                        first = await manager.start({**DEFAULT_SETTINGS, 'count': 1})
                        queued = await manager.start({**DEFAULT_SETTINGS, 'count': 1})
                        self.assertEqual(queued['status'], 'queued')
                        await manager.wait()
                    first_batch = await history.get_batch(first['id'])
                    self.assertEqual(first_batch['status'], 'error' if exhaust else 'completed')
                    if exhaust:
                        self.assertIn('prompt-1', first_batch['error'])
                        self.assertIn('结果未确认', first_batch['error'])
                    images = (await history.list_images())['items']
                    self.assertEqual(len(images), 1 if exhaust else 2)
                    self.assertEqual(len(fake.submissions), 2)
                    self.assertTrue(all(method == 'GET' for method, _ in self.calls))
                    self.assertEqual(manager.snapshot()['status'], 'completed')
                finally:
                    await manager.shutdown()
                    await history.close()


    async def test_later_image_outage_preserves_completed_artwork_and_advances_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = runner_fixtures.FakeComfy()
            self.client.lora_filenames = fake.lora_filenames
            self.client.resource_inventory = fake.resource_inventory
            self.client.submit = fake.submit
            image_entry = {**self.entry, 'outputs': {'output': {'images': [
                {'filename': 'result.png', 'type': 'output'}]}}}
            self.replies = [{'prompt-1': image_entry}] + [503] * 6 + [{'prompt-3': image_entry}]
            history = HistoryStore(Path(directory) / 'history.sqlite3')
            manager = BatchManager(runner_fixtures.FakeTemplates(), history, self.client)
            manager._ensure_monitor = lambda: None
            try:
                with patch('anima_webui.comfy.HISTORY_RETRY_DELAYS', (0,) * 5):
                    first = await manager.start({**DEFAULT_SETTINGS, 'count': 2})
                    queued = await manager.start({**DEFAULT_SETTINGS, 'count': 1})
                    self.assertEqual(queued['status'], 'queued')
                    await manager.wait()
                failed = await history.get_batch(first['id'])
                self.assertEqual((failed['total'], failed['completed'], failed['status']), (2, 1, 'error'))
                self.assertIn('prompt-2', failed['error'])
                images = (await history.list_images())['items']
                self.assertEqual({item['prompt_id'] for item in images}, {'prompt-1', 'prompt-3'})
                self.assertEqual(sum(item['batch_id'] == first['id'] for item in images), 1)
                self.assertEqual(len(fake.submissions), 3)
                self.assertEqual(manager.snapshot()['status'], 'completed')
                self.assertEqual(manager.snapshot()['completed'], 1)
                self.assertTrue(all(method == 'GET' for method, _ in self.calls))
            finally:
                await manager.shutdown()
                await history.close()
