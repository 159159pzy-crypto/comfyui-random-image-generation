from __future__ import annotations

import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from anima_webui.comfy import ComfyError
from anima_webui.history_filters import HistoryFilterError, HistoryFilterService, parse_content_filters
from anima_webui.server import CATALOG_KEY, HISTORY_FILTERS_KEY, HISTORY_KEY, create_app
from anima_webui.workflow import WorkflowError
from test_server import APP_DIR, FakeComfy, _write_fake_tools


def token(pool, kind, value):
    return {"pool": pool, "kind": kind, "value": value}


class HistoryFilterApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.comfy = FakeComfy()
        self.app = create_app(app_dir=APP_DIR, data_dir=self.root / "data", comfy=self.comfy,
                              anima_tools_dir=_write_fake_tools(self.root))
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.history = self.app[HISTORY_KEY]
        self.catalog = self.app[CATALOG_KEY]
        self.filters = self.app[HISTORY_FILTERS_KEY]
        self.favorite_data = self.filters.lifecycle.extension.profiles.setdefault("anima", {})
        await self.history.create_batch("filter", 4, {"model_profile": "anima"})
        self.character = self.catalog.all_items("character")[0]
        self.favorite_data["pose"] = {
            "groups": [{"id": "parent", "name": "All poses"},
                       {"id": "child", "name": "Seated", "parentId": "parent"}],
            "items": [{"id": "stand", "groupIds": ["parent"]}, {"id": "sit", "groupIds": ["child"]}],
        }
        self.ids = []
        for index, selection in enumerate([
            {"character": [self.character], "pose": [self.catalog.get("pose:stand")]},
            {"character": [self.character], "pose": [self.catalog.get("pose:sit")]},
            {},
            {"character": [{**self.character, "id": "gone:historical", "title": "Archived Person", "hair": "gold"}]},
        ]):
            record = await self.history.add_image(
                batch_id="filter", sequence=index, prompt_id=str(index), image={"filename": f"{index}.png"},
                positive_prompt="白发 sunlight" if index == 2 else "scene", negative_prompt="negative-only",
                sample_seed=1, prompt_seed=2, settings={"model_profile": "anima", "workbench": "random"},
                resolved_selection=selection,
            )
            self.ids.append(record["id"])

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def read(self, path="/api/history", *, filters=None, status=200, **params):
        if filters is not None:
            params["content_filters"] = json.dumps(filters, ensure_ascii=False)
        response = await self.client.get(path, params=params)
        payload = await response.json()
        self.assertEqual(response.status, status, payload)
        return payload

    async def test_parent_group_exclusion_and_legacy_pages(self):
        filters = {"include": [token("pose", "favorite_group", "parent")],
                   "exclude": [token("pose", "item", "pose:sit")]}
        result = await self.read(filters=filters)
        self.assertEqual([item["id"] for item in result["items"]], self.ids[:1])
        self.assertEqual(result["total"], 1)
        self.assertTrue(result["group_revision"])
        filters["exclude"] = [token("pose", "favorite_group", "child")]
        self.assertEqual((await self.read(filters=filters))["total"], 1)
        self.assertEqual((await self.read(limit=1))["total"], 4)
        await self.read(workbench="single", status=400)
        missing = await self.read(filters={"include": [token("pose", "favorite_group", "deleted")]})
        self.assertEqual(missing["total"], 0)

    async def test_same_pool_union_across_pool_intersection_and_literal_search(self):
        filters = {"include": [token("pose", "favorite_group", "child"), token("pose", "item", "pose:stand"),
                                token("character", "item", self.character["id"])]}
        self.assertEqual((await self.read(filters=filters))["total"], 2)
        self.assertEqual((await self.read(q="SUNLIGHT"))["items"][0]["id"], self.ids[2])
        self.assertEqual((await self.read(q="negative-only"))["total"], 0)
        self.assertEqual((await self.read(q="白发", filters=filters))["total"], 0)

    async def test_catalog_has_complete_tree_and_group_candidate_search(self):
        result = await self.read("/api/history/filter-catalog")
        self.assertEqual([item["id"] for item in result["sections"]], ["character", "clothing", "pose", "background", "expression"])
        pose = next(item for item in result["sections"] if item["id"] == "pose")
        favorite = next(item for item in pose["groups"] if item["value"] == "favorites")
        parent = next(item for item in favorite["children"] if item["value"] == "parent")
        self.assertEqual(parent["children"][0]["value"], "child")
        candidates = await self.read("/api/history/filter-options", section="pose", group_kind="favorite_group",
                                     group_id="parent", group_revision=result["group_revision"], limit=1)
        self.assertEqual(candidates["total"], 2)
        self.assertTrue(candidates["has_more"])
        tail = await self.read("/api/history/filter-options", section="pose", group_kind="favorite_group",
                              group_id="parent", group_revision=result["group_revision"], limit=1, page=2)
        self.assertNotEqual(candidates["items"][0]["id"], tail["items"][0]["id"])
        historical = await self.read("/api/history/filter-options", section="character", q="archived")
        self.assertEqual(historical["items"][0]["id"], "gone:historical")
        hair = await self.read("/api/history/filter-options", section="character", group_kind="hair")
        self.assertEqual({item["id"] for item in hair["items"]}, {"blue", "gold"})

    async def test_revision_detects_member_rename_and_deletion_without_cached_stale_results(self):
        original = await self.read("/api/history/filter-catalog")
        query = {"include": [token("pose", "favorite_group", "parent")]}
        self.favorite_data["pose"]["items"] = [{"id": "sit", "groupIds": ["child"]}]
        stale = await self.read(filters=query, group_revision=original["group_revision"], status=409)
        self.assertEqual(stale["code"], "history_filter_catalog_changed")
        current = await self.read(filters=query, group_revision=stale["group_revision"])
        self.assertEqual([item["id"] for item in current["items"]], self.ids[1:2])
        self.favorite_data["pose"]["groups"][0]["name"] = "Renamed"
        renamed = await self.read("/api/history/filter-catalog")
        self.assertNotEqual(renamed["group_revision"], stale["group_revision"])
        self.favorite_data["pose"] = {"groups": [], "items": []}
        self.assertEqual((await self.read(filters=query))["total"], 0)

    async def test_all_filter_reads_remain_local_offline(self):
        with patch.object(self.comfy, "favorites", AsyncMock(side_effect=ComfyError("offline")), create=True):
            self.assertEqual((await self.read(q="sunlight"))["total"], 1)
            self.assertEqual((await self.read(filters={"include": [token("character", "hair", "gold")]}))["total"], 1)
            await self.read("/api/history/filter-options", section="character", group_kind="hair")
            await self.read("/api/history/filter-catalog")
            self.assertEqual((await self.read(filters={"include": [token("pose", "favorite_group", "parent")]}))["total"], 2)

    async def test_character_attributes_bind_to_one_saved_person_and_exclusions_apply_to_whole_work(self):
        record = await self.history.add_image(
            batch_id="filter", sequence=5, prompt_id="multi", image={"filename": "multi.png"},
            positive_prompt="two people", negative_prompt="", sample_seed=1, prompt_seed=2,
            settings={"model_profile": "anima"}, resolved_selection={"character": [
                self.character, {**self.character, "id": "character:beta||series", "title": "Beta", "hair": "gold"},
            ]},
        )
        wrong_person = {"include": [token("character", "item", self.character["id"]), token("character", "hair", "gold")]}
        self.assertEqual((await self.read(filters=wrong_person))["total"], 0)
        matching = {"include": [token("character", "item", self.character["id"]), token("character", "hair", "blue")]}
        self.assertIn(record["id"], [item["id"] for item in (await self.read(filters=matching))["items"]])
        matching["exclude"] = [token("character", "hair", "gold")]
        self.assertNotIn(record["id"], [item["id"] for item in (await self.read(filters=matching))["items"]])

    async def test_valid_read_only_source_remains_browsable(self):
        self.filters.lifecycle.custom._protect_storage("read-only test")
        self.filters.lifecycle.extension._protect_storage("read-only test")
        await self.read("/api/history/filter-catalog")

    async def test_compiled_snapshot_cache_checks_every_read_and_invalidates_without_a_ttl(self):
        service = self.filters
        with patch.object(service, "_build_snapshot", wraps=service._build_snapshot) as build, \
             patch.object(self.comfy, "favorites", AsyncMock(side_effect=AssertionError("remote read")), create=True) as fetch:
            initial = await service.snapshot()
            self.assertIs(await service.snapshot(), initial)
            self.assertEqual(build.call_count, 1)
            self.assertEqual(fetch.call_count, 0)
            # Parent names are mutable in place, so list identities alone are
            # insufficient for the custom/favorite portion of the snapshot.
            service.lifecycle.custom.groups["pose"].append({"id": "p", "name": "Custom group", "kind": "group"})
            grouped = await service.snapshot()
            self.assertNotEqual(grouped.revision, initial.revision)
            service.lifecycle.custom.groups["pose"][0]["name"] = "Renamed"
            renamed = await service.snapshot()
            self.assertNotEqual(renamed.revision, grouped.revision)
            self.catalog.set_custom_items([{"id": "custom:new", "section": "pose", "title": "New pose", "prompt": "test",
                                           "groupIds": ["p"], "categories": [], "traits": []}])
            changed = await service.snapshot()
            self.assertEqual(changed.ids("pose", "custom_group", "p"), {"custom:new"})
            self.assertEqual(build.call_count, 4)
            self.assertIs(await service.snapshot(), changed)
            self.assertEqual(build.call_count, 4)
        with patch.object(self.comfy, "favorites", AsyncMock(side_effect=ComfyError("offline")), create=True):
            await self.read("/api/history/filter-catalog")

    async def test_concurrent_reads_share_work_and_one_cancelled_waiter_does_not_cancel_others(self):
        started, release = asyncio.Event(), asyncio.Event()
        original = self.filters._read_snapshot
        async def slow_read():
            started.set()
            await release.wait()
            return await original()
        with patch.object(self.filters, "_read_snapshot", AsyncMock(side_effect=slow_read)) as fetch:
            waiters = [asyncio.create_task(self.filters.snapshot()) for _ in range(6)]
            await started.wait()
            waiters[0].cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiters[0]
            release.set()
            snapshots = await asyncio.gather(*waiters[1:])
            self.assertEqual(fetch.call_count, 1)
            self.assertTrue(all(value is snapshots[0] for value in snapshots))
            await self.filters.snapshot()
            self.assertEqual(fetch.call_count, 2)

    async def test_failed_local_snapshot_is_shared_and_next_request_retries(self):
        async def unavailable():
            await asyncio.sleep(.01)
            raise HistoryFilterError("unavailable local catalog")
        with patch.object(self.filters, "_read_snapshot", AsyncMock(side_effect=unavailable)) as fetch:
            results = await asyncio.gather(*(self.filters.snapshot() for _ in range(5)), return_exceptions=True)
            self.assertEqual(fetch.call_count, 1)
            self.assertTrue(all(isinstance(value, HistoryFilterError) and value.status == 503 for value in results))
        await self.read("/api/history/filter-catalog")

    async def test_filter_reads_never_reconcile_or_change_generation_sources(self):
        lifecycle = self.filters.lifecycle
        before = (copy.deepcopy(lifecycle.custom.items), copy.deepcopy(lifecycle.custom.groups),
                  copy.deepcopy(lifecycle.extension.profiles), copy.deepcopy(self.favorite_data))
        with patch.object(lifecycle, "ensure_ready", AsyncMock(side_effect=AssertionError("unexpected recovery"))), \
             patch.object(lifecycle, "recover_pending", AsyncMock(side_effect=AssertionError("unexpected recovery"))), \
             patch.object(self.comfy, "save_favorites", AsyncMock(side_effect=AssertionError("unexpected write")), create=True):
            await self.read("/api/history/filter-catalog")
            await self.read(filters={"include": [token("pose", "favorite_group", "parent")]})
        self.assertEqual(before, (lifecycle.custom.items, lifecycle.custom.groups, lifecycle.extension.profiles, self.favorite_data))
        lifecycle._journal("anima").write_text("{}", encoding="utf-8")
        await self.read("/api/history/filter-catalog", status=503)
        self.assertEqual(lifecycle._journal("anima").read_text(encoding="utf-8"), "{}")

    async def test_validation_is_consistent_for_history_and_candidates(self):
        for filters in ([], {"unknown": []}, {"include": "x"},
                        {"include": [token("invalid", "item", "a")]},
                        {"include": [token("pose", "hair", "blue")]},
                        {"include": [token("pose", "item", "a")], "exclude": [token("pose", "item", "a")]},
                        {"include": [{**token("pose", "item", "a"), "extra": 1}]}):
            await self.read(filters=filters, status=400)
        await self.read(content_filters="{", status=400)
        await self.read(q="x" * 1001, status=400)
        for params in ({"section": "none"}, {"section": "pose", "group_kind": "hair"},
                       {"section": "pose", "group_kind": "favorite_group"},
                       {"section": "pose", "page": "no"}, {"section": "pose", "limit": 101}):
            await self.read("/api/history/filter-options", status=400, **params)


class HistoryFilterSnapshotTests(unittest.TestCase):
    def test_favorite_keys_map_to_every_full_id_and_native_facets_are_not_truncated(self):
        sections = ("character", "clothing", "pose", "background", "expression")
        items = {section: [] for section in sections}
        items["character"] = [{"id": f"character:a||series-{i}", "favorite_key": "a", "title": "A", "copyright": f"series-{i}"}
                              for i in range(125)]
        items["pose"] = [{"id": "pose:x", "title": "Pose", "group_ids": ["leaf"],
                          "categories": ["坐姿 (Sitting Poses)"], "traits": ["Standing"]}]
        custom = {section: [] for section in sections}
        custom["pose"] = [{"id": "parent", "name": "Parent"}, {"id": "leaf", "name": "Leaf", "parentId": "parent"}]
        favorites = {section: {"groups": [], "items": []} for section in sections}
        favorites["character"] = {"groups": [{"id": "g", "name": "A favorites"}], "items": [{"name": "a", "groupIds": ["g"]}]}
        snapshot = HistoryFilterService._build_snapshot(items, custom, favorites)
        self.assertEqual(len(snapshot.ids("character", "favorite_group", "g")), 125)
        self.assertEqual(snapshot.ids("pose", "custom_group", "parent"), {"pose:x"})
        self.assertEqual(snapshot.ids("pose", "category", "Sitting Poses"), {"pose:x"})
        self.assertEqual(snapshot.ids("pose", "trait", "standing"), {"pose:x"})
        self.assertEqual(snapshot.ids("pose", "custom_group", "missing"), set())

    def test_duplicate_filter_tokens_are_deduplicated(self):
        value = token("pose", "item", "pose:stand")
        parsed = parse_content_filters(json.dumps({"include": [value, value]}))
        self.assertEqual(parsed, {"include": [value], "exclude": []})
        with self.assertRaises(WorkflowError):
            parse_content_filters(json.dumps({"include": [token("pose", "item", "\n")]}))

    def test_semantic_aliases_are_canonicalized_before_deduplication_and_conflict_checks(self):
        for pool, kind, left, right in (("character", "hair", "Gold", "gold"),
                                        ("character", "series", "ＳＴＲＡＳＳＥ", "strasse"),
                                        ("pose", "category", "坐姿 (Sitting Poses)", "sitting_poses"),
                                        ("pose", "trait", "Hands_Up", "hands up")):
            with self.subTest(kind=kind):
                parsed = parse_content_filters(json.dumps({"include": [token(pool, kind, left), token(pool, kind, right)]}))
                self.assertEqual(len(parsed["include"]), 1)
                with self.assertRaises(WorkflowError):
                    parse_content_filters(json.dumps({"include": [token(pool, kind, left)], "exclude": [token(pool, kind, right)]}))
        # IDs remain opaque: case/underscores can distinguish real source IDs.
        parsed = parse_content_filters(json.dumps({"include": [token("pose", "item", "custom:A_B")],
                                                  "exclude": [token("pose", "item", "custom:a b")]}))
        self.assertEqual(parsed["include"][0]["value"], "custom:A_B")
