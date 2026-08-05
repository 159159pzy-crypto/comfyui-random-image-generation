from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from anima_natural.engine import NaturalEngineError
from anima_webui.v7_events import StudioEventBus
from anima_webui.v7_routes import setup_v7_routes
from anima_webui.v7_store import V7Store


class FakeRuntime:
    def __init__(self) -> None:
        self.items: dict[str, dict] = {
            "failed-one": {
                "run_id": "failed-one",
                "task_type": "random_batch",
                "mode": "random",
                "status": "failed",
                "metadata": {"workspace": "random", "settings": {"count": 1}},
            },
            "queued-one": {
                "run_id": "queued-one",
                "task_type": "natural_generation",
                "mode": "natural",
                "status": "queued",
                "metadata": {"workspace": "natural"},
            },
        }

    async def list(self, **_: object) -> list[dict]:
        return list(self.items.values())

    async def get(self, run_id: str) -> dict:
        if run_id not in self.items:
            raise KeyError(run_id)
        return self.items[run_id]

    async def events(self, **_: object) -> dict:
        return {"entries": [], "cursor": 0}


class FakeHistory:
    def __init__(self) -> None:
        self.deleted = False

    async def list_images(self, page: int, limit: int) -> dict:
        return {
            "items": [{"id": 1, "source_workspace": "natural"}],
            "page": page,
            "limit": limit,
            "total": 1,
            "pages": 1,
        }

    async def get_image(self, image_id: int) -> dict:
        if image_id != 1 or self.deleted:
            raise KeyError(image_id)
        return {"id": 1, "source_workspace": "natural"}

    async def delete_image(self, image_id: int) -> bool:
        if image_id != 1 or self.deleted:
            return False
        self.deleted = True
        return True


class FakeComfy:
    async def resource_inventory(self) -> dict:
        return {"models": ["model.safetensors"], "upscale_models": ["upscale.pth"]}

    async def lora_inventory(self) -> dict:
        return {"items": [{"filename": "style.safetensors"}], "count": 1}


class V7RoutesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = V7Store(Path(self.temporary.name) / "studio.sqlite3")
        self.events = StudioEventBus(self.store)
        self.runtime = FakeRuntime()
        self.history = FakeHistory()
        self.submissions: list[dict] = []
        self.previews: list[dict] = []
        app = web.Application()

        async def retry_job(_: str, __: dict) -> dict:
            return {"id": "replacement", "status": "queued"}

        async def cancel_job(job_id: str, current: dict) -> dict:
            cancelled = {**current, "status": "cancelled"}
            self.runtime.items[job_id] = cancelled
            return cancelled

        async def submit_job(intent: dict) -> dict:
            self.submissions.append(intent)
            return {"id": "submitted", "status": "queued", "echo": intent["workspace"]}

        async def preview_job(intent: dict) -> dict:
            self.previews.append(intent)
            prompt = str(intent.get("positive_prompt") or "")
            if prompt == "engine-error":
                raise NaturalEngineError(
                    "provider failed",
                    code="director_failed",
                    status=422,
                    details={"stage": "tool_loop"},
                )
            ambiguous = prompt == "ambiguous"
            return {
                "job_type": "text_to_image",
                "pipeline": "base",
                "positive_prompt": prompt,
                "negative_prompt": str(intent.get("negative_prompt") or ""),
                "requires_confirmation": (
                    [
                        {
                            "kind": "lora",
                            "query": "ambiguous",
                            "candidates": [
                                {"id": "a", "name": "a"},
                                {"id": "b", "name": "b"},
                            ],
                        }
                    ]
                    if ambiguous
                    else []
                ),
                "matches": {"loras": []},
                "sources": {"positive_prompt": "natural_language"},
            }

        def upload_asset(data: bytes) -> dict:
            return {"asset_id": "asset-v7", "size": len(data)}

        setup_v7_routes(
            app,
            store=self.store,
            events=self.events,
            history=self.history,
            comfy=FakeComfy(),
            runtime=self.runtime,
            preview_job=preview_job,
            submit_job=submit_job,
            cancel_job=cancel_job,
            retry_job=retry_job,
            upload_asset=upload_asset,
        )
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()
        await self.events.close()
        self.store.close()
        self.temporary.cleanup()

    async def test_bootstrap_draft_conflict_and_assets(self) -> None:
        bootstrap = await (await self.client.get("/api/v7/bootstrap?workspace=natural")).json()
        self.assertEqual(bootstrap["version"], 7)
        self.assertEqual(bootstrap["draft"]["revision"], 0)
        self.assertEqual(bootstrap["events"]["cursor"], 0)

        saved_response = await self.client.put(
            "/api/v7/drafts/natural",
            json={
                "revision": 0,
                "intent": {"workspace": "natural", "positive_prompt": "a red dress"},
            },
        )
        self.assertEqual(saved_response.status, 200)
        saved = await saved_response.json()
        self.assertEqual(saved["revision"], 1)
        conflict = await self.client.put(
            "/api/v7/drafts/natural",
            json={"revision": 0, "intent": {"workspace": "natural"}},
        )
        self.assertEqual(conflict.status, 409)
        self.assertEqual((await conflict.json())["current"]["revision"], 1)

        models = await (await self.client.get("/api/v7/assets/models")).json()
        loras = await (await self.client.get("/api/v7/assets/loras")).json()
        self.assertEqual(models["items"][0]["filename"], "model.safetensors")
        self.assertEqual(models["upscale_models"], ["upscale.pth"])
        self.assertEqual(loras["items"][0]["filename"], "style.safetensors")

    async def test_intent_accepts_direct_and_wrapped_bodies(self) -> None:
        direct = await self.client.post(
            "/api/v7/intents/preview",
            json={
                "workspace": "natural",
                "positive_prompt": "portrait",
                "loras": [{"filename": "style.safetensors", "strength": 0.8}],
            },
        )
        self.assertEqual(direct.status, 201)
        self.assertEqual((await direct.json())["intent"]["loras"][0]["role"], "style")
        wrapped = await self.client.post(
            "/api/v7/intents/preview",
            json={"intent": {"workspace": "random", "positive_prompt": "landscape"}},
        )
        self.assertEqual(wrapped.status, 201)
        self.assertEqual((await wrapped.json())["intent"]["workspace"], "random")

        submitted = await self.client.post(
            "/api/v7/jobs",
            json={"intent": {"workspace": "natural", "positive_prompt": "ready"}},
        )
        self.assertEqual(submitted.status, 201)
        submission = await submitted.json()
        self.assertEqual(submission["id"], "submitted")
        self.assertTrue(submission["intent_id"].startswith("intent_"))

        ambiguous = await self.client.post(
            "/api/v7/jobs",
            json={"workspace": "natural", "positive_prompt": "ambiguous"},
        )
        self.assertEqual(ambiguous.status, 409)
        ambiguity = await ambiguous.json()
        self.assertEqual(ambiguity["code"], "asset_confirmation_required")
        self.assertEqual(len(self.submissions), 1)

        uploaded = await self.client.post(
            "/api/v7/studio/uploads",
            data=b"image-bytes",
            headers={"Content-Type": "image/png"},
        )
        self.assertEqual(uploaded.status, 201)
        self.assertEqual((await uploaded.json())["asset_id"], "asset-v7")

    async def test_native_engine_error_status_and_code_are_preserved(self) -> None:
        response = await self.client.post(
            "/api/v7/intents/preview",
            json={"workspace": "natural", "positive_prompt": "engine-error"},
        )

        self.assertEqual(response.status, 422)
        payload = await response.json()
        self.assertEqual(payload["code"], "director_failed")
        self.assertEqual(payload["details"], {"stage": "tool_loop"})

    async def test_explicit_lora_selection_resolves_matching_planner_ambiguity(self) -> None:
        response = await self.client.post(
            "/api/v7/intents/preview",
            json={
                "workspace": "natural",
                "positive_prompt": "ambiguous",
                "loras": [
                    {
                        "filename": "a",
                        "enabled": True,
                        "strength": 0.8,
                        "role": "style",
                        "order": 0,
                    }
                ],
            },
        )

        self.assertEqual(response.status, 201)
        payload = await response.json()
        self.assertEqual(payload["requires_confirmation"], [])
        self.assertEqual(payload["plan"]["requires_confirmation"], [])
        self.assertEqual(payload["resolution"]["status"], "resolved")
        self.assertEqual(payload["plan"]["sources"]["loras"][0]["matched_by"], "explicit")

    async def test_frozen_intent_confirmation_is_verified_without_replanning(self) -> None:
        preview_response = await self.client.post(
            "/api/v7/intents/preview",
            json={"workspace": "natural", "positive_prompt": "ambiguous"},
        )
        self.assertEqual(preview_response.status, 201)
        preview = await preview_response.json()
        frozen = preview["intent"]
        self.assertNotIn("_plan", frozen)
        self.assertNotIn("_resolution", frozen)
        self.assertEqual(len(self.previews), 1)

        frozen["loras"] = [
            {"filename": "a", "enabled": True, "strength": 0.8, "role": "style", "order": 0}
        ]
        receipt = {
            "kind": "lora",
            "query": "ambiguous",
            "action": "select_candidate",
            "candidate_id": "a",
            "candidate_name": "a",
        }
        submitted = await self.client.post(
            "/api/v7/jobs",
            json={"intent": frozen, "resolution_confirmations": [receipt]},
        )
        self.assertEqual(submitted.status, 201)
        result = await submitted.json()
        self.assertEqual(len(self.previews), 1)
        self.assertNotEqual(result["intent_id"], frozen["id"])
        self.assertEqual(result["intent"]["preview_intent_id"], frozen["id"])
        self.assertEqual(result["intent"]["resolution_confirmations"], [receipt])
        self.assertNotIn("_execution_plan", result["intent"])
        self.assertEqual(self.submissions[-1]["_execution_plan"]["loras"][0]["filename"], "a")

        forged = dict(frozen)
        forged["loras"] = [
            {"filename": "forged", "enabled": True, "strength": 1, "role": "style", "order": 0}
        ]
        forged_receipt = {**receipt, "candidate_id": "forged", "candidate_name": "forged"}
        rejected = await self.client.post(
            "/api/v7/jobs",
            json={"intent": forged, "resolution_confirmations": [forged_receipt]},
        )
        self.assertEqual(rejected.status, 400)
        self.assertEqual((await rejected.json())["code"], "invalid_request")

        stale = {**frozen, "digest": "stale"}
        conflict = await self.client.post(
            "/api/v7/jobs",
            json={"intent": stale, "resolution_confirmations": [receipt]},
        )
        self.assertEqual(conflict.status, 409)
        self.assertEqual((await conflict.json())["code"], "intent_revision_conflict")

    async def test_presets_jobs_history_and_sse_resume(self) -> None:
        created = await self.client.post(
            "/api/v7/presets",
            json={
                "name": "Ink",
                "aliases": ["ink style"],
                "intent": {"workspace": "random", "positive_prompt": "ink drawing"},
            },
        )
        self.assertEqual(created.status, 201)
        preset = await created.json()
        self.assertTrue(preset["id"].startswith("preset_"))

        retried = await self.client.post("/api/v7/jobs/failed-one/retry", json={})
        self.assertEqual(retried.status, 201)
        self.assertEqual((await retried.json())["id"], "replacement")
        history = await (await self.client.get("/api/v7/history/1")).json()
        self.assertEqual(history["source_workspace"], "natural")
        deleted = await self.client.delete("/api/v7/history/1")
        self.assertEqual(deleted.status, 200)
        self.assertTrue((await deleted.json())["deleted"])

        jobs = await (await self.client.get("/api/v7/jobs")).json()
        self.assertEqual(jobs["items"][0]["type"], "generation")
        self.assertEqual(jobs["items"][0]["source_workspace"], "random")
        self.assertNotIn("task_type", jobs["items"][0])
        job = await (await self.client.get("/api/v7/jobs/failed-one")).json()
        self.assertEqual(job["type"], "generation")
        self.assertNotIn("task_type", job)

        first = await self.events.publish("test.first", {"n": 1})
        second = await self.events.publish("test.second", {"n": 2})
        response = await self.client.get(
            "/api/v7/events", headers={"Last-Event-ID": str(first["id"])}
        )
        try:
            frame = b""
            for _ in range(4):
                frame += await asyncio.wait_for(response.content.readline(), timeout=1)
            text = frame.decode("utf-8")
            self.assertIn(f"id: {second['id']}", text)
            self.assertIn("event: test.second", text)
            self.assertNotIn("event: test.first", text)
        finally:
            response.close()

    async def test_cancel_and_retry_publish_terminal_events(self) -> None:
        cancelled_response = await self.client.post(
            "/api/v7/jobs/queued-one/cancel", json={}
        )
        self.assertEqual(cancelled_response.status, 200)
        cancelled = await cancelled_response.json()
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["source_workspace"], "natural")

        retried_response = await self.client.post(
            "/api/v7/jobs/failed-one/retry", json={}
        )
        self.assertEqual(retried_response.status, 201)
        self.assertEqual((await retried_response.json())["id"], "replacement")

        event_items = await self.events.read(after_id=0)
        cancelled_event = next(
            item for item in event_items if item["event"] == "job.cancelled"
        )
        retried_event = next(
            item for item in event_items if item["event"] == "job.retried"
        )
        self.assertEqual(cancelled_event["entity_id"], "queued-one")
        self.assertEqual(cancelled_event["workspace"], "natural")
        self.assertEqual(retried_event["data"]["original_job_id"], "failed-one")
        self.assertEqual(retried_event["data"]["job"]["id"], "replacement")


if __name__ == "__main__":
    unittest.main()
