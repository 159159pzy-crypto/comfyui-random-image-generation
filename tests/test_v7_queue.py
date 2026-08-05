from __future__ import annotations

import asyncio
import unittest

from anima_webui.v7_queue import V7GenerationQueue


class FakeRuntime:
    TERMINAL = {"succeeded", "partial", "failed", "cancelled", "timed_out", "interrupted"}

    def __init__(self) -> None:
        self.tasks = {}

    async def create(self, task_type, *, run_id, mode, total_items, metadata):
        self.tasks[run_id] = {
            "run_id": run_id,
            "task_type": task_type,
            "mode": mode,
            "status": "queued",
            "metadata": metadata,
        }

    async def get(self, run_id):
        return dict(self.tasks[run_id])

    async def start(self, run_id, **fields):
        self.tasks[run_id].update({"status": "running", **fields})
        return dict(self.tasks[run_id])

    async def finish(self, run_id, status, **fields):
        if self.tasks[run_id].get("status") in self.TERMINAL:
            return dict(self.tasks[run_id])
        self.tasks[run_id].update({"status": status, **fields})
        return dict(self.tasks[run_id])


class V7GenerationQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_generation_terminal_state_is_published(self):
        runtime = FakeRuntime()
        events = []

        async def execute(job_id, _intent):
            await runtime.start(job_id)
            await runtime.finish(job_id, "succeeded")

        async def publish(event_type, payload, **metadata):
            events.append((event_type, payload, metadata))

        queue = V7GenerationQueue(
            runtime,
            {"random": execute, "natural": execute},
            publish=publish,
        )
        try:
            submitted = await queue.submit({"workspace": "natural"})
            for _ in range(100):
                if any(event[0] == "job.succeeded" for event in events):
                    break
                await asyncio.sleep(0)
        finally:
            await queue.close()

        terminal = next(event for event in events if event[0] == "job.succeeded")
        self.assertEqual(terminal[1]["run_id"], submitted["id"])
        self.assertEqual(terminal[2]["workspace"], "natural")

    async def test_random_and_natural_share_submission_order(self):
        runtime = FakeRuntime()
        order = []
        first_gate = asyncio.Event()

        async def execute(job_id, intent):
            order.append((job_id, intent["workspace"]))
            if len(order) == 1:
                await first_gate.wait()
            await runtime.finish(job_id, "succeeded")

        queue = V7GenerationQueue(
            runtime,
            {"random": execute, "natural": execute},
        )
        try:
            first = await queue.submit({"workspace": "random", "sampling": {"count": 1}})
            second = await queue.submit({"workspace": "natural", "sampling": {"count": 1}})
            third = await queue.submit({"workspace": "random", "sampling": {"count": 1}})
            await asyncio.sleep(0)
            self.assertEqual([item[0] for item in order], [first["id"]])
            first_gate.set()
            for _ in range(50):
                if len(order) == 3:
                    break
                await asyncio.sleep(0)
            self.assertEqual(
                order,
                [
                    (first["id"], "random"),
                    (second["id"], "natural"),
                    (third["id"], "random"),
                ],
            )
        finally:
            await queue.close()

    async def test_pending_item_can_be_removed_without_execution(self):
        runtime = FakeRuntime()
        gate = asyncio.Event()
        executed = []
        events = []

        async def execute(job_id, intent):
            executed.append(job_id)
            await gate.wait()
            await runtime.finish(job_id, "succeeded")

        async def publish(event_type, payload, **metadata):
            events.append((event_type, payload, metadata))

        queue = V7GenerationQueue(
            runtime,
            {"random": execute, "natural": execute},
            publish=publish,
        )
        try:
            first = await queue.submit({"workspace": "random"})
            second = await queue.submit({"workspace": "natural"})
            await asyncio.sleep(0)
            cancelled = await queue.cancel_pending(second["id"])
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertEqual(cancelled["error_code"], "removed_from_queue")
            terminal = next(
                event
                for event in events
                if event[0] == "job.cancelled" and event[1]["id"] == second["id"]
            )
            self.assertTrue(terminal[1]["pending"])
            self.assertEqual(terminal[2]["workspace"], "natural")
            gate.set()
            await asyncio.sleep(0)
            self.assertEqual(executed, [first["id"]])
        finally:
            await queue.close()

    async def test_running_studio_operation_can_only_cancel_itself(self):
        runtime = FakeRuntime()
        started = asyncio.Event()
        events = []

        async def generate(job_id, _intent):
            await runtime.finish(job_id, "succeeded")

        async def studio(_run_id, cancel_event):
            started.set()
            while not cancel_event.is_set():
                await asyncio.sleep(0)

        async def publish(event_type, payload, **metadata):
            events.append((event_type, payload, metadata))

        queue = V7GenerationQueue(
            runtime,
            {"random": generate, "natural": generate},
            publish=publish,
        )
        try:
            operation = await queue.submit_studio("model_refresh", studio)
            await asyncio.wait_for(started.wait(), timeout=1)
            self.assertIsNone(await queue.cancel_studio("studio_not_the_owner"))
            self.assertEqual((await runtime.get(operation["id"]))["status"], "running")

            cancelled = await queue.cancel_studio(operation["id"])
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertTrue(
                any(
                    event[0] == "job.cancelled"
                    and event[2]["entity_id"] == operation["id"]
                    for event in events
                )
            )
        finally:
            await queue.close()

    async def test_close_interrupts_active_and_pending_jobs(self):
        runtime = FakeRuntime()
        started = asyncio.Event()
        release = asyncio.Event()
        events = []

        async def execute(job_id, _intent):
            await runtime.start(job_id)
            started.set()
            await release.wait()

        async def publish(event_type, payload, **metadata):
            events.append((event_type, payload, metadata))

        queue = V7GenerationQueue(
            runtime,
            {"random": execute, "natural": execute},
            publish=publish,
        )
        active = await queue.submit({"workspace": "random"})
        pending = await queue.submit({"workspace": "natural"})
        await asyncio.wait_for(started.wait(), timeout=1)

        await queue.close()

        self.assertEqual((await runtime.get(active["id"]))["status"], "interrupted")
        self.assertEqual((await runtime.get(pending["id"]))["status"], "interrupted")
        interrupted_ids = {
            event[2]["entity_id"] for event in events if event[0] == "job.interrupted"
        }
        self.assertEqual(interrupted_ids, {active["id"], pending["id"]})

    async def test_studio_operation_uses_the_same_fifo_as_generation(self):
        runtime = FakeRuntime()
        order = []
        gate = asyncio.Event()

        async def generate(job_id, intent):
            order.append((job_id, intent["workspace"]))
            await gate.wait()
            await runtime.finish(job_id, "succeeded")

        async def studio(run_id, cancel_event):
            self.assertFalse(cancel_event.is_set())
            order.append((run_id, "studio"))
            return {"ok": True}

        queue = V7GenerationQueue(runtime, {"random": generate, "natural": generate})
        try:
            first = await queue.submit({"workspace": "natural"})
            operation = await queue.submit_studio("model_refresh", studio)
            third = await queue.submit({"workspace": "random"})
            await asyncio.sleep(0)
            gate.set()
            for _ in range(100):
                if len(order) == 3:
                    break
                await asyncio.sleep(0)
            self.assertEqual(
                order,
                [
                    (first["id"], "natural"),
                    (operation["id"], "studio"),
                    (third["id"], "random"),
                ],
            )
        finally:
            await queue.close()

    async def test_studio_operation_cannot_overwrite_external_terminal_state(self):
        runtime = FakeRuntime()
        events = []

        async def generate(job_id, _intent):
            await runtime.finish(job_id, "succeeded")

        async def studio(run_id, _cancel_event):
            await runtime.finish(
                run_id,
                "interrupted",
                error_code="studio_restarted",
            )
            return {"ok": True}

        async def publish(event_type, payload, **metadata):
            events.append((event_type, payload, metadata))

        queue = V7GenerationQueue(
            runtime,
            {"random": generate, "natural": generate},
            publish=publish,
        )
        try:
            operation = await queue.submit_studio("model_refresh", studio)
            for _ in range(100):
                if any(
                    event[0] == "job.interrupted"
                    and event[2]["entity_id"] == operation["id"]
                    for event in events
                ):
                    break
                await asyncio.sleep(0)

            self.assertEqual(
                (await runtime.get(operation["id"]))["status"],
                "interrupted",
            )
            self.assertFalse(
                any(
                    event[0] == "job.succeeded"
                    and event[2]["entity_id"] == operation["id"]
                    for event in events
                )
            )
        finally:
            await queue.close()


if __name__ == "__main__":
    unittest.main()
