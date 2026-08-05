import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestServer

from anima_studio.natural_services import DanbooruTagIndex as RuntimeDanbooruTagIndex
from anima_studio.studio_services import (
    DanbooruApiBuilder,
    DanbooruBuildOptions,
    DanbooruUpdateScheduler,
)


class _ControlledDanbooruApi:
    def __init__(self) -> None:
        self.tags = [
            {"id": 1, "name": "artist_one", "category": 1, "post_count": 12},
            {"id": 2, "name": "series_one", "category": 3, "post_count": 8},
            {"id": 3, "name": "character_one", "category": 4, "post_count": 20},
        ]
        self.aliases = [
            {
                "id": 10,
                "antecedent_name": "char_one",
                "consequent_name": "character_one",
                "status": "active",
            }
        ]
        self.requests: list[tuple[str, str, str]] = []
        self.fail_tag_cursor: int | None = None
        self.fail_tag_category = ""
        self.app = web.Application()
        self.app.router.add_get("/tags.json", self.tags_handler)
        self.app.router.add_get("/tag_aliases.json", self.aliases_handler)

    @staticmethod
    def _page(rows: list[dict], request: web.Request) -> list[dict]:
        page = str(request.query.get("page") or "")
        high_water = int(request.query.get("search[id_lteq]") or 2_147_483_647)
        eligible = [item for item in rows if int(item["id"]) <= high_water]
        if page.startswith("b"):
            return sorted(eligible, key=lambda item: int(item["id"]), reverse=True)[:1]
        cursor = int(page.removeprefix("a") or 0)
        limit = int(request.query.get("limit") or 1000)
        return [
            item
            for item in sorted(eligible, key=lambda candidate: int(candidate["id"]))
            if int(item["id"]) > cursor
        ][:limit]

    async def tags_handler(self, request: web.Request) -> web.Response:
        page = str(request.query.get("page") or "")
        category = str(request.query.get("search[category]") or "")
        self.requests.append(("tags", category, page))
        if (
            self.fail_tag_cursor is not None
            and category == self.fail_tag_category
            and page.startswith("a")
            and int(page[1:] or 0) >= self.fail_tag_cursor
        ):
            return web.json_response({"error": "controlled failure"}, status=503)
        rows = [
            item
            for item in self.tags
            if int(item["category"]) == int(category)
            and int(item["post_count"])
            >= int(request.query.get("search[post_count_gteq]") or 0)
        ]
        return web.json_response(self._page(rows, request))

    async def aliases_handler(self, request: web.Request) -> web.Response:
        page = str(request.query.get("page") or "")
        self.requests.append(("aliases", "", page))
        return web.json_response(self._page(self.aliases, request))


class DanbooruBuilderIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.api = _ControlledDanbooruApi()
        self.server = TestServer(self.api.app)
        await self.server.start_server()
        self.index = RuntimeDanbooruTagIndex(self.root / "danbooru.sqlite3")
        self.builder = DanbooruApiBuilder(
            self.index,
            self.root / "checkpoint.sqlite3",
            allow_insecure_localhost=True,
            pace_requests=False,
        )

    async def asyncTearDown(self) -> None:
        await self.server.close()
        self.directory.cleanup()

    def options(self, **overrides) -> DanbooruBuildOptions:
        values = {
            "base_url": str(self.server.make_url("/")).rstrip("/"),
            "mode": "identity",
            "page_size": 2,
            "max_records": 100,
            "max_retries": 1,
        }
        values.update(overrides)
        return DanbooruBuildOptions(**values)

    async def test_builds_v2_snapshot_with_hash_and_high_water_increment(self) -> None:
        first = await self.builder.build(self.options())

        self.assertTrue(first["activated"])
        self.assertFalse(first["incremental"])
        self.assertEqual(first["tag_count"], 3)
        self.assertEqual(len(first["content_sha256"]), 64)
        self.assertEqual(self.index.lookup("char one").canonical_tag, "character_one")
        status = self.builder.checkpoint_status()
        self.assertTrue(status["checkpoint_completed"])
        self.assertEqual(status["source_max_tag_id"], 3)
        original_digest = first["content_sha256"]

        self.api.requests.clear()
        self.api.tags.append(
            {"id": 4, "name": "artist_two", "category": 1, "post_count": 5}
        )
        self.api.aliases.append(
            {
                "id": 11,
                "antecedent_name": "artist_ii",
                "consequent_name": "artist_two",
                "status": "active",
            }
        )
        second = await self.builder.build(self.options())

        self.assertTrue(second["incremental"])
        self.assertFalse(second["resumed"])
        self.assertEqual(second["tag_count"], 4)
        self.assertNotEqual(second["content_sha256"], original_digest)
        self.assertIn(("tags", "1", "a1"), self.api.requests)
        self.assertIn(("aliases", "", "a10"), self.api.requests)
        self.assertTrue(self.index.lookup("artist ii").verified)
        with closing(sqlite3.connect(self.index.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        self.assertEqual(metadata["sha256"], second["content_sha256"])
        self.assertEqual(metadata["generator"], "anima_studio")

    async def test_failed_update_keeps_old_index_and_resume_uses_checkpoint(self) -> None:
        baseline = await self.builder.build(self.options())
        baseline_bytes = self.index.path.read_bytes()

        checkpoint = self.root / "fresh-checkpoint.sqlite3"
        builder = DanbooruApiBuilder(
            self.index,
            checkpoint,
            allow_insecure_localhost=True,
            pace_requests=False,
        )
        self.api.fail_tag_category = "3"
        self.api.fail_tag_cursor = 0
        with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
            await builder.build(self.options(page_size=1))

        self.assertEqual(self.index.path.read_bytes(), baseline_bytes)
        failed_status = builder.checkpoint_status()
        self.assertTrue(failed_status["resumable"])
        self.assertEqual(failed_status["tag_count"], 1)
        self.assertIn("HTTP 503", failed_status["last_error"])

        self.api.fail_tag_cursor = None
        restarted_builder = DanbooruApiBuilder(
            self.index,
            checkpoint,
            allow_insecure_localhost=True,
            pace_requests=False,
        )
        resumed = await restarted_builder.build(self.options(page_size=1))
        self.assertTrue(resumed["resumed"])
        self.assertEqual(resumed["content_sha256"], baseline["content_sha256"])
        self.assertTrue(resumed["unchanged"])


class _ScheduledBuilder:
    def __init__(self) -> None:
        self.calls = 0

    async def build(self, options, *, progress=None, cancel_event=None):
        self.calls += 1
        return {"completed": True, "mode": options.mode, "tag_count": 1}


class DanbooruUpdateSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_defaults_offline_and_requires_two_explicit_confirmations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = _ScheduledBuilder()
            scheduler = DanbooruUpdateScheduler(
                backend, Path(directory) / "danbooru.schedule.json"
            )
            start = datetime(2026, 8, 5, tzinfo=UTC)

            self.assertFalse(scheduler.snapshot()["enabled"])
            self.assertEqual(scheduler.snapshot()["network_default"], "offline")
            with self.assertRaises(PermissionError):
                scheduler.configure(enabled=True, interval_hours=24, now=start)
            configured = scheduler.configure(
                enabled=True,
                interval_hours=24,
                confirm_manual=True,
                now=start,
            )
            self.assertFalse(configured["due"])
            restored = DanbooruUpdateScheduler(backend, scheduler.path)
            self.assertTrue(restored.snapshot()["enabled"])

            due = start + timedelta(hours=25)
            with self.assertRaises(PermissionError):
                await restored.run_due(now=due)
            result = await restored.run_due(confirm_scheduled=True, now=due)

            self.assertTrue(result["started"])
            self.assertEqual(backend.calls, 1)
            self.assertFalse(result["schedule"]["running"])
            self.assertFalse(result["schedule"]["due"])


if __name__ == "__main__":
    unittest.main()
