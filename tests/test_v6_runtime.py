from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from anima_studio.task_store import TaskStore, TaskTransitionError
from anima_webui.migrations import import_legacy_task_events, prepare_v6_backup
from anima_webui.task_runtime import StudioTaskRuntime, publish_recovered_task_events


class V6MigrationTests(unittest.TestCase):
    def test_backup_is_verified_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "data" / "natural" / "providers.json"
            source.parent.mkdir(parents=True)
            source.write_text('{"profiles": []}', encoding="utf-8")

            first = prepare_v6_backup(root)
            second = prepare_v6_backup(root)

            self.assertEqual(first, second)
            backup = root / first["backup"] / "natural" / "providers.json"
            self.assertEqual(backup.read_bytes(), source.read_bytes())
            self.assertEqual(
                first["files"][0]["sha256"],
                hashlib.sha256(source.read_bytes()).hexdigest(),
            )

    def test_legacy_events_import_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            event_path = root / "data" / "natural" / "task_events.jsonl"
            event_path.parent.mkdir(parents=True)
            event_path.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {"job_id": "job_old", "stage": "queued", "message": "排队", "timestamp": 1},
                        {"job_id": "job_old", "stage": "completed", "message": "完成", "timestamp": 2},
                    )
                ),
                encoding="utf-8",
            )
            store = TaskStore(root / "data" / "studio.sqlite3")
            try:
                self.assertEqual(import_legacy_task_events(root, store), 1)
                self.assertEqual(import_legacy_task_events(root, store), 1)
                task = store.get_task("job_old")
                self.assertIsNotNone(task)
                self.assertEqual(task["status"], "succeeded")
            finally:
                store.close()


class StudioTaskRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_runtime_lease_blocks_false_restart_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "studio.sqlite3"
            first = StudioTaskRuntime(database)
            try:
                await first.create("natural_generation", run_id="live")
                await first.start("live")

                with self.assertRaisesRegex(RuntimeError, "already active"):
                    StudioTaskRuntime(database)

                self.assertEqual((await first.get("live"))["status"], "running")
            finally:
                await first.close()

            recovered = StudioTaskRuntime(database)
            try:
                self.assertEqual((await recovered.get("live"))["status"], "interrupted")
            finally:
                await recovered.close()

    async def test_terminal_state_is_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = StudioTaskRuntime(Path(temporary) / "studio.sqlite3")
            try:
                await runtime.create("natural_generation", run_id="immutable")
                await runtime.start("immutable")
                interrupted = await runtime.finish(
                    "immutable",
                    "interrupted",
                    error_code="studio_restarted",
                )

                completed = await runtime.finish(
                    "immutable",
                    "succeeded",
                    completed_items=1,
                )

                self.assertEqual(completed, interrupted)
                with self.assertRaises(TaskTransitionError):
                    await runtime.start("immutable")
                with self.assertRaises(TaskTransitionError):
                    await runtime.heartbeat("immutable", completed_items=1)
                events = await runtime.events(run_id="immutable")
                self.assertEqual(
                    [
                        item["message"]
                        for item in events["entries"]
                        if item["event_code"] == "task_finished"
                    ],
                    ["Task finished: interrupted"],
                )
            finally:
                await runtime.close()

    async def test_concurrent_terminal_writes_choose_one_terminal_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "studio.sqlite3"
            first = TaskStore(database)
            second = TaskStore(database)
            first.create_task("natural_generation", run_id="race")
            first.start_task("race")
            barrier = threading.Barrier(2)

            def finish(store: TaskStore, status: str) -> dict:
                barrier.wait(timeout=2)
                return store.finish_task("race", status)

            try:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = (
                        executor.submit(finish, first, "interrupted"),
                        executor.submit(finish, second, "succeeded"),
                    )
                    results = [future.result(timeout=5) for future in futures]

                self.assertEqual(results[0]["status"], results[1]["status"])
                self.assertIn(results[0]["status"], {"interrupted", "succeeded"})
                events = first.read_events(run_id="race")["entries"]
                self.assertEqual(
                    len([item for item in events if item["event_code"] == "task_finished"]),
                    1,
                )
            finally:
                first.close()
                second.close()

    async def test_restart_marks_unstarted_queue_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "studio.sqlite3"
            first = StudioTaskRuntime(database)
            await first.create("random_batch", run_id="queue_one", mode="random")
            await first.create("natural_generation", run_id="running_one", mode="natural")
            await first.start("running_one")
            await first.close()

            second = StudioTaskRuntime(database)
            try:
                task = await second.get("queue_one")
                self.assertEqual(task["status"], "interrupted")
                self.assertEqual(task["error_code"], "restart_before_start")
                running = await second.get("running_one")
                self.assertEqual(running["status"], "interrupted")
                self.assertEqual(running["error_code"], "restart_while_running")
                self.assertEqual(
                    {item["run_id"] for item in second.recovered_tasks},
                    {"queue_one", "running_one"},
                )
                published: list[tuple[str, dict, dict]] = []

                class Events:
                    async def publish(self, event: str, data: dict, **metadata: object) -> None:
                        published.append((event, data, metadata))

                self.assertEqual(await publish_recovered_task_events(second, Events()), 2)
                self.assertEqual(second.recovered_tasks, [])
                self.assertEqual({item[0] for item in published}, {"job.interrupted"})
                self.assertEqual(
                    {item[1]["source_workspace"] for item in published},
                    {"random", "natural"},
                )
            finally:
                await second.close()

    async def test_events_are_cursor_addressable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = StudioTaskRuntime(Path(temporary) / "studio.sqlite3")
            try:
                await runtime.create("natural_generation", run_id="job_one")
                await runtime.start("job_one", total_items=1)
                await runtime.event("job_one", "sampling", "正在生成")
                payload = await runtime.events(run_id="job_one", after_seq=0)
                self.assertGreaterEqual(len(payload["entries"]), 4)
                self.assertGreater(payload["cursor"], 0)
            finally:
                await runtime.close()
