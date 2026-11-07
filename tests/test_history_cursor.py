from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from anima_webui.history import HistoryStore
from anima_webui.server import HISTORY_KEY, create_app
from test_server import APP_DIR, FakeComfy, _write_fake_tools


async def add_records(store: HistoryStore, settings: list[dict]) -> list[int]:
    """Add history directly, without submitting any generation jobs."""
    ids = []
    for item in settings:
        record = await store.add_image(
            batch_id="cursor-test", sequence=1, prompt_id="saved-prompt",
            image={"filename": "saved.png"}, positive_prompt="saved text",
            negative_prompt="", sample_seed=1, prompt_seed=1, settings=item,
        )
        ids.append(record["id"])
    return ids


def item_ids(payload: dict) -> list[int]:
    return [item["id"] for item in payload["items"]]


class HistoryCursorSqlTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "history.sqlite3"
        self.store = HistoryStore(self.path)
        await self.store.create_batch("cursor-test", 100, {"model_profile": "anima"})

    async def asyncTearDown(self):
        await self.store.close()
        self.temp.cleanup()

    async def test_cursor_stays_stable_when_new_rows_arrive_and_old_rows_are_deleted(self):
        await add_records(self.store, [{"model_profile": "anima"}] * 9)
        first = await self.store.list_images(limit=3)
        self.assertEqual(item_ids(first), [9, 8, 7])
        self.assertEqual(first["next_before_id"], 7)
        self.assertTrue(first["has_more"])

        await add_records(self.store, [{"model_profile": "anima"}])
        for image_id in (8, 7, 5):
            await self.store.delete_image(image_id)
        statements = []
        await self.store._call(self.store.connection.set_trace_callback, statements.append)
        try:
            second = await self.store.list_images(page=99, limit=3, before_id=first["next_before_id"])
        finally:
            await self.store._call(self.store.connection.set_trace_callback, None)
        self.assertEqual(item_ids(second), [6, 4, 3])
        self.assertEqual(second["total"], 7)
        self.assertTrue(second["has_more"])
        row_query = next(sql for sql in statements if "batches.total AS batch_total" in sql)
        self.assertIn("images.id < 7", row_query)
        self.assertNotIn("OFFSET", row_query)

        last = await self.store.list_images(limit=3, before_id=second["next_before_id"])
        self.assertEqual(item_ids(last), [2, 1])
        self.assertFalse(last["has_more"])
        self.assertEqual(last["next_before_id"], 1)

    async def test_random_filter_keeps_records_without_explicit_workbench(self):
        settings = [
            {"model_profile": "anima"},
            {"workbench": "random", "model_profile": "anima"},
            {"workbench": "random", "model_profile": "anima"},
        ]
        ids = await add_records(self.store, settings)
        original_bytes = self.path.read_bytes()
        random = await self.store.list_images(limit=2, workbench="random")
        self.assertEqual(item_ids(random), [3, 2])
        self.assertEqual(random["total"], 3)
        self.assertTrue(random["has_more"])
        tail = await self.store.list_images(limit=2, workbench="random", before_id=2)
        self.assertEqual(item_ids(tail), [1])
        self.assertEqual(tail["items"][0]["workbench"], "random")
        self.assertFalse(tail["has_more"])

        for image_id, original in zip(ids, settings):
            self.assertEqual((await self.store.get_image(image_id))["settings"], original)
        self.assertEqual(self.path.read_bytes(), original_bytes)

    async def test_numbered_pages_remain_compatible_and_return_cursors(self):
        await add_records(self.store, [{"model_profile": "anima"}] * 30)
        page = await self.store.list_images(page=2, limit=10)
        self.assertEqual(item_ids(page), list(range(20, 10, -1)))
        self.assertEqual((page["page"], page["limit"], page["total"], page["pages"]), (2, 10, 30, 3))
        self.assertEqual(page["next_before_id"], 11)
        self.assertTrue(page["has_more"])
        last = await self.store.list_images(page=3, limit=10)
        self.assertEqual(item_ids(last), list(range(10, 0, -1)))
        self.assertFalse(last["has_more"])
        self.assertEqual(last["next_before_id"], 1)
        out_of_range = await self.store.list_images(page=99, limit=10)
        self.assertEqual(out_of_range["items"], [])
        self.assertIsNone(out_of_range["next_before_id"])
        self.assertFalse(out_of_range["has_more"])

    async def test_exact_size_final_page_and_deleted_tail_terminate(self):
        empty = await self.store.list_images(before_id=1)
        self.assertFalse(empty["has_more"])
        self.assertIsNone(empty["next_before_id"])
        await add_records(self.store, [{"model_profile": "anima"}] * 4)
        last = await self.store.list_images(limit=2, before_id=3)
        self.assertEqual(item_ids(last), [2, 1])
        self.assertFalse(last["has_more"])
        for image_id in (2, 1):
            await self.store.delete_image(image_id)
        emptied = await self.store.list_images(limit=2, before_id=3)
        self.assertEqual(emptied["items"], [])
        self.assertEqual(emptied["total"], 2)
        self.assertFalse(emptied["has_more"])
        self.assertIsNone(emptied["next_before_id"])

    async def test_store_rejects_invalid_cursor_values(self):
        for value in (0, -1, True, 1.5, "2", 2**63):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    await self.store.list_images(before_id=value)


class HistoryCursorApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.comfy = FakeComfy()
        self.app = create_app(
            app_dir=APP_DIR, data_dir=root / "data", comfy=self.comfy,
            anima_tools_dir=_write_fake_tools(root),
        )
        self.store = self.app[HISTORY_KEY]
        await self.store.create_batch("cursor-test", 100, {"model_profile": "anima"})
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def test_default_first_page_and_cursor_response_contract(self):
        await add_records(self.store, [{"model_profile": "anima"}] * 30)
        response = await self.client.get("/api/history?page=1&limit=24")
        self.assertEqual(response.status, 200)
        first = await response.json()
        self.assertTrue({"items", "page", "limit", "total", "pages", "available", "next_before_id", "has_more"}.issubset(first))
        self.assertTrue(first["available"])
        self.assertEqual(item_ids(first), list(range(30, 6, -1)))
        self.assertEqual(first["next_before_id"], 7)
        self.assertTrue(first["has_more"])
        response = await self.client.get("/api/history?page=99&limit=24&before_id=7")
        self.assertEqual(response.status, 200)
        last = await response.json()
        self.assertEqual(item_ids(last), list(range(6, 0, -1)))
        self.assertEqual((last["total"], last["pages"]), (30, 2))
        self.assertFalse(last["has_more"])
        self.assertEqual(last["next_before_id"], 1)
        self.assertEqual(self.comfy.submissions, [])

    async def test_filtered_cursor_and_empty_tail(self):
        await add_records(self.store, [
            {"workbench": "random", "model_profile": "anima"},
            {"model_profile": "anima"},
            {"workbench": "random", "model_profile": "anima"},
        ])
        first = await (await self.client.get("/api/history?workbench=random&limit=1")).json()
        self.assertEqual(item_ids(first), [3])
        self.assertEqual(first["total"], 3)
        last = await (await self.client.get("/api/history?workbench=random&limit=2&before_id=3")).json()
        self.assertEqual(item_ids(last), [2, 1])
        self.assertEqual(last["items"][0]["settings"]["model_profile"], "anima")
        self.assertFalse(last["has_more"])
        empty = await (await self.client.get("/api/history?workbench=random&before_id=1")).json()
        self.assertEqual(empty["items"], [])
        self.assertIsNone(empty["next_before_id"])
        self.assertFalse(empty["has_more"])
        random = await (await self.client.get("/api/history?workbench=random&before_id=3")).json()
        self.assertEqual(item_ids(random), [2, 1])

    async def test_invalid_query_parameters_return_400(self):
        for value in ("", "0", "-1", "1.5", "abc", "1e3", "+1", " 1", "１", str(2**63)):
            with self.subTest(before_id=value):
                response = await self.client.get("/api/history", params={"before_id": value})
                self.assertEqual(response.status, 400)
                self.assertIn("before_id", (await response.json())["error"])
        for params in ({"page": "bad"}, {"limit": "bad"}, {"workbench": "bad"}, {"workbench": "single"}):
            with self.subTest(params=params):
                response = await self.client.get("/api/history", params=params)
                self.assertEqual(response.status, 400)

    async def test_maximum_cursor_and_legacy_limit_clamping(self):
        await add_records(self.store, [{"model_profile": "anima"}] * 3)
        response = await self.client.get("/api/history", params={
            "before_id": str(2**63 - 1), "page": "0", "limit": "99",
        })
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(item_ids(payload), [3, 2, 1])
        self.assertEqual((payload["page"], payload["limit"]), (1, 60))
        self.assertFalse(payload["has_more"])
