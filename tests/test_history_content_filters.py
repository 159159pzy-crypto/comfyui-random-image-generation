from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from anima_webui import history_search
from anima_webui.artwork_deletion import ArtworkDeletionService
from anima_webui.history import HistoryStore


def character(term_id: str, *, title: str = "Alice", hair: str = "black hair",
              eye: str = "blue eyes", series: str = "Series A", gender: str = "1girl"):
    return {"id": term_id, "title": title, "hair": hair, "eye": eye, "copyright": series, "gender": gender}


def ids(result):
    return [item["id"] for item in result["items"]]


class HistoryContentFilterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "history.sqlite3"
        self.store = HistoryStore(self.path)
        await self.store.create_batch("content", 100, {"model_profile": "anima"})

    async def asyncTearDown(self):
        await self.store.close()
        self.temp.cleanup()

    async def add(self, selected=None, *, positive="saved prompt", negative="secret negative",
                  resolved="", workbench="random"):
        return await self.store.add_image(
            batch_id="content", sequence=1, prompt_id="saved", image={"filename": "saved.png"},
            positive_prompt=positive, negative_prompt=negative, sample_seed=1, prompt_seed=2,
            settings={"model_profile": "anima", "workbench": workbench},
            resolved_selection=selected, resolved_prompt=resolved,
        )

    async def test_includes_union_within_pool_intersection_across_pools(self):
        first = await self.add({"character": [character("a")], "pose": [{"id": "sitting", "title": "Sitting"}]})
        second = await self.add({"character": [character("b")], "pose": [{"id": "standing", "title": "Standing"}]})
        await self.add({"character": [character("c")], "pose": [{"id": "sitting", "title": "Sitting"}]})
        selected = {"include": {"character": ["a", "b"], "pose": ["sitting"]}}
        self.assertEqual(ids(await self.store.list_images(content_filter=selected)), [first["id"]])
        selected["include"]["pose"].append("standing")
        self.assertEqual(ids(await self.store.list_images(content_filter=selected)), [second["id"], first["id"]])

    async def test_all_positive_character_properties_belong_to_same_character(self):
        await self.add({"character": [character("a"), character("b", hair="blonde hair", eye="green eyes")]})
        correct = await self.add({"character": [character("a", hair="blonde hair", eye="green eyes")]})
        await self.add({"character": [character("a", hair="blonde hair"), character("b", eye="green eyes")]})
        filters = {"include": {"character": ["a"]}, "attributes": {
            "hair": {"include": ["Blonde Hair"]}, "eye": {"include": ["green eyes"]},
        }}
        self.assertEqual(ids(await self.store.list_images(content_filter=filters)), [correct["id"]])
        del filters["include"]
        self.assertEqual((await self.store.list_images(content_filter=filters))["total"], 2)

    async def test_exclusion_anywhere_wins_and_missing_records_survive(self):
        await self.add({"character": [character("a"), character("b", hair="blonde hair")]})
        keep = await self.add({"character": [character("a")]})
        unknown = await self.add({}, workbench="random")
        filters = {"exclude": {"character": ["b"]}}
        self.assertEqual(ids(await self.store.list_images(content_filter=filters)), [unknown["id"], keep["id"]])
        filters = {"include": {"character": ["a"]}, "attributes": {"hair": {"exclude": ["blonde hair"]}}}
        self.assertEqual(ids(await self.store.list_images(content_filter=filters)), [keep["id"]])

    async def test_deleted_or_empty_included_group_never_turns_into_unrestricted(self):
        await self.add({"character": [character("a")]})
        await self.add({})
        self.assertEqual((await self.store.list_images(content_filter={"include": {"character": []}}))["total"], 0)
        self.assertEqual((await self.store.list_images(content_filter={"exclude": {"character": []}}))["total"], 2)

    async def test_large_groups_do_not_exceed_sql_parameter_limits(self):
        included = await self.add({"pose": [{"id": "p:4999"}]})
        await self.add({"pose": [{"id": "outside"}]})
        group = [f"p:{index}" for index in range(5000)]
        self.assertEqual(ids(await self.store.list_images(content_filter={"include": {"pose": group}})), [included["id"]])
        self.assertEqual((await self.store.content_filter_options("pose", term_ids=group))["total"], 1)

    async def test_keyword_is_literal_unicode_and_does_not_search_negative(self):
        first = await self.add({"character": [character("a", title="白发人物")]}, positive="Straße ＡＢＣ 100%_海边")
        other = await self.add({}, positive="100xx海边", resolved="winter mountains")
        for query in ("STRASSE", "abc", "%_", "白发", "海边"):
            with self.subTest(query=query):
                self.assertIn(first["id"], ids(await self.store.list_images(q=query)))
        self.assertEqual(ids(await self.store.list_images(q="%_")), [first["id"]])
        self.assertEqual(ids(await self.store.list_images(q="WINTER")), [other["id"]])
        self.assertEqual((await self.store.list_images(q="secret negative"))["total"], 0)

    async def test_unknown_structures_are_not_inferred_as_applied_terms(self):
        record = await self.add({"natural_plan": {"locked_pool_selection": {"character": [character("a")]}}}, positive="Alice")
        self.assertEqual(ids(await self.store.list_images(q="alice")), [record["id"]])
        self.assertEqual((await self.store.list_images(content_filter={"include": {"character": ["a"]}}))["total"], 0)
        self.assertEqual((await self.store.content_filter_options("character"))["total"], 0)

    async def test_cursor_total_and_workbench_use_same_filter(self):
        for i in range(6):
            await self.add({"pose": [{"id": "sitting" if i % 2 == 0 else "standing"}]}, workbench="random")
        filters = {"include": {"pose": ["sitting"]}}
        result = await self.store.list_images(limit=1, content_filter=filters)
        self.assertEqual((ids(result), result["total"], result["pages"], result["has_more"]), ([5], 3, 3, True))
        result = await self.store.list_images(limit=1, before_id=5, content_filter=filters)
        self.assertEqual((ids(result), result["total"], result["has_more"]), ([3], 3, True))
        result = await self.store.list_images(limit=1, before_id=3, content_filter=filters)
        self.assertEqual((ids(result), result["total"], result["has_more"]), ([1], 3, False))
        self.assertEqual(ids(await self.store.list_images(workbench="random", content_filter=filters)), [5, 3, 1])

    async def test_artwork_folder_intersects_filter_without_duplicates(self):
        first = await self.add({"pose": [{"id": "sitting"}]})
        second = await self.add({"pose": [{"id": "standing"}]})
        await self.add({"pose": [{"id": "sitting"}]})
        folder = (await self.store.create_artwork_folder("Favorites"))["folder"]["id"]
        await self.store.update_artwork_memberships([first["id"], second["id"]], [folder], "add")
        result = await self.store.list_images(folder_id=folder, content_filter={"include": {"pose": ["sitting"]}})
        self.assertEqual((ids(result), result["total"]), ([first["id"]], 1))

    async def test_candidates_keep_identity_latest_name_and_search_old_alias(self):
        await self.add({"character": [character("custom:a", title="Old Alice"), character("custom:a", title="Old Alice")]})
        await self.add({"character": [character("custom:a", title="New Alice")]})
        await self.add({"character": [character("b", title="New Alice")]})
        result = await self.store.content_filter_options("character", q="Old Alice")
        self.assertEqual(result["items"], [{"id": "custom:a", "title": "New Alice", "subtitle": "", "count": 2}])
        result = await self.store.content_filter_options("character", limit=1)
        self.assertEqual((result["items"][0]["id"], result["total"], result["has_more"]), ("custom:a", 2, True))
        tail = await self.store.content_filter_options("character", limit=1, page=2)
        self.assertEqual((tail["items"][0]["id"], tail["has_more"]), ("b", False))
        empty = await self.store.content_filter_options("character", term_ids=[])
        self.assertEqual(empty["total"], 0)

    async def test_attribute_candidates_aggregate_images_and_preserve_display(self):
        await self.add({"character": [character("a", hair="Blonde Hair"), character("b", hair="blonde hair")]})
        await self.add({"character": [character("a", hair="Blonde Hair")]})
        result = await self.store.content_filter_options("character", attribute="hair", q="BLONDE")
        self.assertEqual(result["items"], [{"id": "blonde hair", "title": "Blonde Hair", "subtitle": "", "count": 2}])
        gender = await self.store.content_filter_options("character", attribute="gender", q="女性")
        self.assertEqual(gender["items"], [{"id": "1girl", "title": "女性", "subtitle": "", "count": 2}])

    async def test_insert_failure_rolls_back_image_and_index(self):
        with mock.patch("anima_webui.history_search.write_document", side_effect=sqlite3.OperationalError("index failed")):
            with self.assertRaises(sqlite3.OperationalError):
                await self.add({"character": [character("a")]})
        self.assertEqual((await self.store.list_images())["total"], 0)
        self.assertEqual((await self.store.content_filter_options("character"))["total"], 0)
        record = await self.add({"character": [character("a")]})
        self.assertEqual(ids(await self.store.list_images(q="alice")), [record["id"]])

    async def test_both_deletion_paths_cascade_search_rows(self):
        first = await self.add({"character": [character("a")]})
        second = await self.add({"character": [character("b")]})
        await self.store.delete_image(first["id"])
        service = ArtworkDeletionService(self.store, None)
        result = await self.store._call(service._delete, second["id"], "record", {})
        self.assertTrue(result["record_deleted"])
        def counts():
            return tuple(self.store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                         for table in ("history_search_documents", "history_content_terms"))
        self.assertEqual(await self.store._call(counts), (0, 0))


class HistoryContentMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def legacy_history(self, path):
        store = HistoryStore(path)
        await store.create_batch("legacy", 1, {"model_profile": "anima"})
        await store.add_image(batch_id="legacy", sequence=1, prompt_id="old", image={"filename": "old.png"},
                              positive_prompt="old prompt", negative_prompt="", sample_seed=1, prompt_seed=2,
                              settings={"model_profile": "anima"}, resolved_selection={"character": [character("a")]})
        await store.update_batch("legacy", status="completed")
        await store.close()
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("DROP TABLE history_content_terms")
            connection.execute("DROP TABLE history_search_documents")
            return connection.execute("SELECT * FROM images").fetchall()

    async def test_backup_backfill_and_restart_are_idempotent_without_rewriting_images(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            original = await self.legacy_history(path)
            for _ in range(2):
                store = HistoryStore(path)
                try:
                    self.assertTrue(store.available)
                    self.assertEqual((await store.list_images(q="alice"))["total"], 1)
                    rows = await store._call(lambda: [tuple(row) for row in store.connection.execute("SELECT * FROM images")])
                    self.assertEqual(rows, original)
                finally:
                    await store.close()
            backups = list(Path(directory).glob("*.pre-history-content-v1*.bak"))
            self.assertEqual(len(backups), 1)
            with closing(sqlite3.connect(backups[0])) as backup:
                self.assertEqual(backup.execute("SELECT * FROM images").fetchall(), original)
                self.assertFalse(backup.execute("SELECT 1 FROM sqlite_master WHERE name='history_search_documents'").fetchone())

    async def test_failed_index_creation_rolls_back_all_new_tables_and_keeps_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            original = await self.legacy_history(path)
            original_bytes = path.read_bytes()
            connect = sqlite3.connect
            def deny(destination, *args, **kwargs):
                connection = connect(destination, *args, **kwargs)
                if str(destination) == str(path):
                    connection.set_authorizer(lambda action, name, *_: sqlite3.SQLITE_DENY
                                             if action == sqlite3.SQLITE_CREATE_INDEX and name == "idx_history_content_term"
                                             else sqlite3.SQLITE_OK)
                return connection
            with mock.patch("anima_webui.history.sqlite3.connect", side_effect=deny):
                store = HistoryStore(path)
            try:
                self.assertFalse(store.available)
                self.assertEqual(path.read_bytes(), original_bytes)
                with closing(connect(path)) as connection:
                    self.assertEqual(connection.execute("SELECT * FROM images").fetchall(), original)
                    self.assertFalse(connection.execute("SELECT 1 FROM sqlite_master WHERE name='history_search_documents'").fetchone())
            finally:
                await store.close()

    async def test_missing_documents_and_new_index_version_are_rebuilt(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            await self.legacy_history(path)
            store = HistoryStore(path)
            await store.close()
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("UPDATE history_search_documents SET index_version=0,search_text='wrong'")
            store = HistoryStore(path)
            try:
                self.assertEqual((await store.list_images(q="alice"))["total"], 1)
                self.assertEqual((await store.list_images(q="wrong"))["total"], 0)
            finally:
                await store.close()
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("DELETE FROM history_search_documents")
            store = HistoryStore(path)
            try:
                self.assertEqual((await store.list_images(q="alice"))["total"], 1)
            finally:
                await store.close()

    async def test_backfill_failure_rolls_back_partially_written_document(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            original = await self.legacy_history(path)
            original_bytes = path.read_bytes()
            write_document = history_search.write_document
            def fail_after_write(*args, **kwargs):
                write_document(*args, **kwargs)
                raise sqlite3.OperationalError("backfill failed")
            with mock.patch("anima_webui.history_search.write_document", side_effect=fail_after_write):
                store = HistoryStore(path)
            try:
                self.assertFalse(store.available)
                self.assertEqual(path.read_bytes(), original_bytes)
                with closing(sqlite3.connect(path)) as connection:
                    self.assertEqual(connection.execute("SELECT * FROM images").fetchall(), original)
                    self.assertFalse(connection.execute("SELECT 1 FROM sqlite_master WHERE name='history_search_documents'").fetchone())
            finally:
                await store.close()

    async def test_missing_term_table_rebuilds_even_when_documents_are_current(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            original = await self.legacy_history(path)
            store = HistoryStore(path)
            await store.close()
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("DROP TABLE history_content_terms")
                self.assertEqual(connection.execute("SELECT index_version FROM history_search_documents").fetchone()[0], history_search.INDEX_VERSION)
            store = HistoryStore(path)
            try:
                self.assertTrue(store.available)
                self.assertEqual((await store.list_images(content_filter={"include": {"character": ["a"]}}))["total"], 1)
                self.assertEqual((await store.content_filter_options("character"))["items"][0]["id"], "a")
                self.assertEqual(await store._call(lambda: [tuple(row) for row in store.connection.execute("SELECT * FROM images")]), original)
            finally:
                await store.close()
