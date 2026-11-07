"""Concurrent resource reads share one fetch without weakening forced refreshes."""
import asyncio
import unittest

from anima_webui.comfy import ComfyClient, ComfyError


class ObjectInfoCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = ComfyClient()
        self.calls = []

        async def fetch(method, path, **kwargs):
            future = asyncio.get_running_loop().create_future()
            self.calls.append(future)
            return await future

        self.client._json = fetch

    async def asyncTearDown(self):
        await self.client.close()

    async def started(self, count=1):
        for _ in range(20):
            if len(self.calls) == count:
                return
            await asyncio.sleep(0)
        self.fail(f"Expected {count} requests, got {len(self.calls)}")

    async def test_concurrent_cache_misses_share_one_request(self):
        requests = [asyncio.create_task(self.client.object_info()) for _ in range(6)]
        await self.started()
        self.calls[0].set_result({"version": "first"})
        values = await asyncio.gather(*requests)
        self.assertTrue(all(value is values[0] for value in values))
        self.assertIs(await self.client.object_info(), values[0])
        self.assertEqual(len(self.calls), 1)

    async def test_failed_fetch_reaches_all_waiters_and_can_retry(self):
        requests = [asyncio.create_task(self.client.object_info()) for _ in range(2)]
        await self.started()
        self.calls[0].set_exception(ComfyError("offline"))
        errors = await asyncio.gather(*requests, return_exceptions=True)
        self.assertTrue(all(isinstance(error, ComfyError) for error in errors))
        retry = asyncio.create_task(self.client.object_info())
        await self.started(2)
        self.calls[1].set_result({"version": "recovered"})
        self.assertEqual((await retry)["version"], "recovered")

    async def test_cancelled_waiter_does_not_cancel_other_waiters(self):
        first = asyncio.create_task(self.client.object_info())
        second = asyncio.create_task(self.client.object_info())
        await self.started()
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertFalse(self.calls[0].cancelled())
        self.calls[0].set_result({"version": "shared"})
        self.assertEqual((await second)["version"], "shared")

    async def test_new_generation_never_joins_or_caches_older_fetch(self):
        old = asyncio.create_task(self.client.object_info())
        await self.started()
        # resource_classifications(refresh=True) establishes this same barrier.
        self.client._object_info_generation += 1
        self.client._object_info_cache = (0.0, None)
        fresh = asyncio.create_task(self.client.object_info())
        await self.started(2)
        self.calls[1].set_result({"version": "fresh"})
        await fresh
        self.calls[0].set_result({"version": "old"})
        await old
        self.assertEqual((await self.client.object_info())["version"], "fresh")

    async def test_close_cancels_shared_requests(self):
        pending = asyncio.create_task(self.client.object_info())
        await self.started()
        await self.client.close()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertEqual(self.client._object_info_pending, {})
