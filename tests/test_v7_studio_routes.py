from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from anima_studio import PromptPlanStore
from anima_webui.v7_studio_routes import (
    V7_STUDIO_CONTRACTS,
    setup_v7_studio_routes,
)


class FakeRuntime:
    def __init__(self) -> None:
        self.items: dict[str, dict] = {}
        self.log_items = [
            {
                "seq": 1,
                "level": "INFO",
                "category": "studio",
                "run_id": "run-info",
                "message": "ok",
                "api_key": "never-return-this",
            },
            {
                "seq": 2,
                "level": "ERROR",
                "category": "generation",
                "run_id": "run-error",
                "message": "render failed",
            },
        ]

    async def create(self, task_type: str, *, run_id: str, **options: object) -> dict:
        self.items[run_id] = {
            "run_id": run_id,
            "task_type": task_type,
            "status": "queued",
            "metadata": dict(options.get("metadata") or {}),
        }
        return dict(self.items[run_id])

    async def start(self, run_id: str, **_: object) -> dict:
        self.items[run_id]["status"] = "running"
        return dict(self.items[run_id])

    async def event(self, *_: object, **__: object) -> int:
        return 1

    async def finish(self, run_id: str, status: str, **options: object) -> dict:
        self.items[run_id].update({"status": status, **options})
        return dict(self.items[run_id])

    async def get(self, run_id: str) -> dict:
        if run_id not in self.items:
            raise KeyError(run_id)
        return dict(self.items[run_id])

    async def list(self, **_: object) -> list[dict]:
        return [dict(item) for item in self.items.values()]

    async def logs(self, **_: object) -> list[dict]:
        return list(self.log_items)

    async def read_logs(self, **options: object) -> dict:
        after = int(options.get("after_seq") or 0)
        levels = {str(item).upper() for item in options.get("levels") or []}
        category = str(options.get("category") or "")
        run_id = str(options.get("run_id") or "")
        items = [
            dict(item)
            for item in self.log_items
            if int(item["seq"]) > after
            and (not levels or item["level"] in levels)
            and (not category or item["category"] == category)
            and (not run_id or item["run_id"] == run_id)
        ]
        limit = int(options.get("limit") or 200)
        items = items[:limit]
        return {"entries": items, "cursor": items[-1]["seq"] if items else after}

    async def clear_logs(self) -> int:
        count = len(self.log_items)
        self.log_items.clear()
        return count


class FakeEvents:
    def __init__(self) -> None:
        self.items: list[dict] = []

    async def publish(self, event: str, payload: object, **context: object) -> dict:
        item = {"event": event, "payload": payload, **context}
        self.items.append(item)
        return item


class FakeProviderRegistry:
    def __init__(self) -> None:
        self.items: dict[str, dict] = {}
        self.bindings: dict[str, str] = {}

    def snapshot(self) -> dict:
        return {"items": list(self.items.values()), "bindings": dict(self.bindings)}

    def upsert(self, payload: dict, provider_id: str = "") -> dict:
        provider_id = provider_id or str(payload.get("id") or "provider-1")
        item = {
            "id": provider_id,
            "name": str(payload.get("name") or "Provider"),
            "has_key": bool(payload.get("api_key")),
        }
        self.items[provider_id] = item
        return item

    def delete(self, provider_id: str) -> None:
        if provider_id not in self.items:
            raise KeyError(provider_id)
        del self.items[provider_id]

    def set_bindings(self, payload: dict) -> dict[str, str]:
        self.bindings = {str(key): str(value) for key, value in payload.items()}
        return dict(self.bindings)


class FakeProviderClient:
    async def test(self, provider_id: str) -> dict:
        return {"id": provider_id, "ok": True}

    async def list_models(self, _: str) -> list[str]:
        return ["text-model", "vision-model"]


class FakeWorkspaceData:
    def __init__(self) -> None:
        self.groups = {"lora_profiles": [], "identities": [], "prompt_lab": []}

    def list(self, kind: str) -> list[dict]:
        return [dict(item) for item in self.groups[kind]]

    def get(self, kind: str, item_id: str) -> dict:
        for item in self.groups[kind]:
            if item["id"] == item_id:
                return dict(item)
        raise KeyError(item_id)

    def upsert(self, kind: str, payload: dict, item_id: str = "") -> dict:
        item_id = item_id or str(payload.get("id") or f"{kind}-1")
        item = {**payload, "id": item_id}
        values = self.groups[kind]
        values[:] = [current for current in values if current["id"] != item_id]
        values.append(item)
        return dict(item)

    def delete(self, kind: str, item_id: str) -> None:
        values = self.groups[kind]
        remaining = [item for item in values if item["id"] != item_id]
        if len(remaining) == len(values):
            raise KeyError(item_id)
        values[:] = remaining

    def upsert_verified_identity(
        self,
        payload: dict,
        item_id: str = "",
        **context: object,
    ) -> dict:
        self.last_identity_context = context
        return self.upsert(
            "identities",
            {
                **payload,
                "verification_status": "verified",
                "character_canonical": payload.get("character_canonical"),
            },
            item_id,
        )

    def confirm_prompt_lab(self, item_id: str) -> dict:
        for item in self.groups["prompt_lab"]:
            if item["id"] == item_id:
                item["status"] = "confirmed"
                return dict(item)
        raise KeyError(item_id)

    def redacted_logs(self, _: int) -> list[dict]:
        return [{"message": "natural", "token": "also-secret"}]


@dataclass(frozen=True)
class FakeBatch:
    batch_id: str
    candidates: tuple[str, ...]


class FakePrompts:
    def generate_batch(self, **_: object) -> FakeBatch:
        return FakeBatch("batch-1", ("first", "second"))

    def confirm_candidate(self, _: FakeBatch, selection: int) -> dict:
        return {"selection": selection, "prompt": "second"}

    def facets(self, **_: object) -> dict:
        return {"asset_types": ["clothing"]}

    def import_native_assets(self, assets: list, **_: object) -> dict:
        return {"imported": len(assets)}

    async def update_from_url(self, url: str, **_: object) -> dict:
        return {"updated": True, "url": url}


@dataclass(frozen=True)
class FakeLoraRecord:
    name: str


class FakeCatalog:
    async def get_detail_v2(self, item: FakeLoraRecord) -> dict:
        return {
            "filename": item.name,
            "sha256": "a" * 64,
            "source_fingerprint": "b" * 64,
        }


class FakeLoras:
    def __init__(self) -> None:
        self.catalog = FakeCatalog()
        self._records = (FakeLoraRecord("styles/ink.safetensors"),)
        self.refresh_count = 0
        self.refresh_gate: asyncio.Event | None = None

    def snapshot(self) -> dict:
        return {"records": [{"name": item.name} for item in self._records]}

    async def refresh_catalog(self, **_: object) -> dict:
        self.refresh_count += 1
        if self.refresh_gate is not None:
            await self.refresh_gate.wait()
        else:
            await asyncio.sleep(0)
        return self.snapshot()

    def visual_manifest(self) -> dict:
        return {"count": len(self._records)}

    def visual_page(self, **options: object) -> dict:
        return {"items": [], "page": options["page"]}

    async def analyze(self, details: list, *_: object, **__: object) -> dict:
        return {"analyzed": len(details)}

    async def archive(self, *_: object, **__: object) -> dict:
        return {"archived": len(self._records)}

    async def download(self, url: str, **_: object) -> dict:
        return {"downloaded": True, "url": url}


class FakeDanbooru:
    def __init__(self) -> None:
        self.schedule = {
            "enabled": False,
            "interval_hours": 168,
            "requires_confirmation": True,
        }

    def snapshot(self) -> dict:
        return {
            "checkpoint": {
                "available": True,
                "generator": "astrbot_plugin_comfy_anima",
            },
            "schedule": dict(self.schedule),
        }

    async def build(self, options: dict, **context: object) -> dict:
        progress = context.get("progress")
        if callable(progress):
            await progress({"event": "page", "message": "one page"})
        return {"tag_count": 5, "mode": options.get("mode", "identity")}

    def configure_schedule(self, *, enabled: bool, interval_hours: int, **_: object) -> dict:
        self.schedule.update({"enabled": enabled, "interval_hours": interval_hours})
        return dict(self.schedule)

    async def run_scheduled(self, **context: object) -> dict:
        progress = context.get("progress")
        if callable(progress):
            await progress({"event": "page", "message": "scheduled page"})
        return {"started": True, "result": {"tag_count": 6}, "schedule": dict(self.schedule)}


class FakeWorkflows:
    def __init__(self) -> None:
        self.profile = {"name": "Local", "settings": {"url": "local"}}

    def list_workflows(self) -> list[dict]:
        return [{"id": "text-to-image"}]

    def list_profiles(self) -> list[dict]:
        return [dict(self.profile)]

    def save_profile(self, name: str, config: dict, **_: object) -> dict:
        self.profile = {"name": name, "settings": dict(config)}
        return dict(self.profile)

    def export_profile(self, _: str) -> dict:
        return dict(self.profile)

    def import_profile(self, profile: dict, **_: object) -> dict:
        self.profile = dict(profile)
        return dict(self.profile)

    def activate_profile(self, name: str, config: dict, **_: object) -> dict:
        return {"name": name, "active": True, "settings": config}

    def delete_profile(self, name: str) -> dict:
        return {"deleted": True, "name": name}


class FakeModels:
    def __init__(self) -> None:
        self.entry: dict | None = None

    def snapshot(self) -> dict:
        return {"entries": [self.entry] if self.entry else []}

    def quarantine(
        self, kind: str, exact_name: str, *, confirm_name: str, **_: object
    ) -> dict:
        if exact_name != confirm_name:
            raise ValueError("exact confirmation does not match")
        self.entry = {"id": "quarantine-1", "kind": kind, "name": exact_name}
        return dict(self.entry)

    def restore(self, entry_id: str, *, confirm_name: str) -> dict:
        if not self.entry or self.entry["id"] != entry_id:
            raise KeyError(entry_id)
        if self.entry["name"] != confirm_name:
            raise ValueError("exact confirmation does not match")
        item = {**self.entry, "restored": True}
        self.entry = None
        return item


class FakeServices:
    def __init__(self) -> None:
        self.prompts = FakePrompts()
        self.loras = FakeLoras()
        self.danbooru = FakeDanbooru()
        self.workflows = FakeWorkflows()
        self.models = FakeModels()

    def capabilities(self) -> dict:
        return {"prompt_lab": {"ready": True}, "lora_download": {"manual_only": True}}


class FakeEngine:
    def __init__(self) -> None:
        self.workspace_data = FakeWorkspaceData()
        self.registry = FakeProviderRegistry()
        self.provider_client = FakeProviderClient()
        self.config = {"model": "default"}
        self.danbooru = SimpleNamespace(
            status=lambda: {"ready": True},
            lookup=lambda tag: SimpleNamespace(
                canonical_tag=tag,
                category="character",
                verified=True,
                matched_by="canonical_exact",
            ),
        )
        self.refreshes = 0

    def settings_snapshot(self) -> dict:
        return dict(self.config)

    def update_settings(self, payload: dict) -> dict:
        self.config.update(payload)
        return dict(self.config)

    def _refresh_services(self) -> None:
        self.refreshes += 1

    def danbooru_search(self, query: str, category: str) -> dict:
        return {"items": [{"tag": query, "category": category}]}

    async def capabilities(self, comfy: object) -> dict:
        assert comfy is not None
        return {
            "comfy_online": True,
            "comfy_error": "",
            "workflows": [{"id": "text", "ready": True}],
        }


class FakeResources:
    async def resource_inventory(self) -> dict:
        return {"models": ["model.safetensors"], "unets": ["anima.safetensors"]}


class V7StudioRoutesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.prompt_plans = PromptPlanStore(
            Path(self.temp_directory.name) / "studio.sqlite3"
        )
        self.runtime = FakeRuntime()
        self.events = FakeEvents()
        self.services = FakeServices()
        self.engine = FakeEngine()
        app = web.Application()

        async def llm_callback(_: str, __: str) -> str:
            return "{}"

        self.operations = setup_v7_studio_routes(
            app,
            services=self.services,
            engine=self.engine,
            runtime=self.runtime,
            llm_callback=llm_callback,
            events=self.events,
            resource_runtime=FakeResources(),
            prompt_plans=self.prompt_plans,
        )
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.operations.close()
        await self.client.close()
        self.temp_directory.cleanup()

    async def wait_terminal(self, run_id: str) -> dict:
        for _ in range(100):
            task = await self.runtime.get(run_id)
            if task["status"] in self.operations.TERMINAL:
                return task
            await asyncio.sleep(0.005)
        self.fail("Studio operation did not finish")

    async def test_contracts_provider_crud_and_manual_network_confirmation(
        self,
    ) -> None:
        contracts = await (await self.client.get("/api/v7/studio/contracts")).json()
        self.assertEqual(len(contracts["items"]), len(V7_STUDIO_CONTRACTS))
        self.assertTrue(
            all(item["path"].startswith("/api/v7/") for item in contracts["items"])
        )

        created = await self.client.post(
            "/api/v7/studio/providers",
            json={"name": "Local", "api_key": "secret"},
        )
        self.assertEqual(created.status, 201)
        self.assertNotIn("secret", await created.text())
        rejected = await self.client.post(
            "/api/v7/studio/providers/provider-1/test", json={}
        )
        self.assertEqual(rejected.status, 409)
        self.assertEqual(
            (await rejected.json())["code"], "manual_confirmation_required"
        )
        accepted = await self.client.post(
            "/api/v7/studio/providers/provider-1/test",
            json={"confirm_manual": True},
        )
        self.assertTrue((await accepted.json())["ok"])
        models = await self.client.post(
            "/api/v7/studio/providers/provider-1/models",
            json={"confirm_manual": True},
        )
        self.assertEqual((await models.json())["count"], 2)

        bindings = await self.client.put(
            "/api/v7/studio/providers/bindings", json={"text": "provider-1"}
        )
        self.assertEqual((await bindings.json())["bindings"]["text"], "provider-1")
        self.assertEqual(self.engine.refreshes, 1)

    async def test_native_metadata_prompt_lab_and_diagnostics(self) -> None:
        profile = await self.client.post(
            "/api/v7/studio/lora-profiles",
            json={"filename": "styles/ink.safetensors"},
        )
        self.assertEqual(profile.status, 201)
        listing = await (await self.client.get("/api/v7/studio/lora-profiles")).json()
        self.assertEqual(listing["count"], 1)
        profile_id = listing["items"][0]["id"]
        repeated_profile = await self.client.post(
            "/api/v7/studio/lora-profiles",
            json={
                "filename": "styles/ink.safetensors",
                "activation_terms": ["ink trigger"],
            },
        )
        self.assertEqual(repeated_profile.status, 200)
        repeated_listing = await (
            await self.client.get("/api/v7/studio/lora-profiles")
        ).json()
        self.assertEqual(repeated_listing["count"], 1)
        identity = await self.client.post(
            "/api/v7/studio/identities",
            json={
                "name": "Ink Character",
                "character_canonical": "ink_character",
                "lora_profile_id": profile_id,
                "activation_terms": ["ink char"],
            },
        )
        self.assertEqual(identity.status, 201)
        identity_body = await identity.json()
        self.assertEqual(identity_body["verification_status"], "verified")
        self.assertEqual(
            self.engine.workspace_data.last_identity_context[
                "character_lookup"
            ].matched_by,
            "canonical_exact",
        )

        candidate = await self.client.post(
            "/api/v7/studio/prompt-lab", json={"prompt": "portrait"}
        )
        candidate_id = (await candidate.json())["id"]
        confirmed = await self.client.post(
            f"/api/v7/studio/prompt-lab/{candidate_id}/confirm", json={}
        )
        self.assertEqual((await confirmed.json())["status"], "confirmed")

        generated = await self.client.post(
            "/api/v7/studio/prompt-lab/candidates", json={"count": 2}
        )
        self.assertEqual(generated.status, 201)
        draft = await self.client.post(
            "/api/v7/studio/prompt-lab/batches/batch-1/confirm",
            json={"selection": 2},
        )
        self.assertEqual((await draft.json())["selection"], 2)

        await self.runtime.create(
            "random_batch",
            run_id="legacy-random",
            metadata={"workspace": "random"},
        )
        diagnostics = await (await self.client.get("/api/v7/studio/diagnostics")).json()
        self.assertTrue(diagnostics["native"])
        self.assertTrue(diagnostics["capabilities"]["prompt_lab"]["ready"])
        self.assertTrue(diagnostics["runtime"]["comfy_online"])
        self.assertTrue(diagnostics["runtime"]["workflows"][0]["ready"])
        self.assertEqual(diagnostics["danbooru"]["generator"], "anima_studio")
        operation = diagnostics["operations"]["items"][0]
        self.assertEqual(operation["type"], "generation")
        self.assertEqual(operation["source_workspace"], "random")
        self.assertNotIn("task_type", operation)
        danbooru = await (await self.client.get("/api/v7/studio/danbooru")).json()
        self.assertEqual(danbooru["generator"], "anima_studio")
        self.assertEqual(danbooru["checkpoint"]["generator"], "anima_studio")
        self.assertNotIn("astrbot", json.dumps(danbooru).casefold())

    async def test_manual_long_operations_are_persisted_and_publish_changes(
        self,
    ) -> None:
        rejected = await self.client.post(
            "/api/v7/studio/loras/refresh", json={"confirm_manual": False}
        )
        self.assertEqual(rejected.status, 409)
        self.assertEqual(self.runtime.items, {})

        accepted = await self.client.post(
            "/api/v7/studio/loras/refresh", json={"confirm_manual": True}
        )
        self.assertEqual(accepted.status, 202)
        queued = await accepted.json()
        finished = await self.wait_terminal(queued["run_id"])
        self.assertEqual(finished["status"], "succeeded")
        self.assertEqual(self.services.loras.refresh_count, 1)
        self.assertTrue(
            any(item["event"] == "job.succeeded" for item in self.events.items)
        )
        self.assertTrue(
            any(item["event"] == "asset.changed" for item in self.events.items)
        )

        built = await self.client.post(
            "/api/v7/studio/danbooru/build",
            json={"confirm_manual": True, "mode": "identity"},
        )
        danbooru = await self.wait_terminal((await built.json())["run_id"])
        self.assertEqual(danbooru["result"]["operation"]["tag_count"], 5)

        schedule_rejected = await self.client.put(
            "/api/v7/studio/danbooru/schedule",
            json={"enabled": True, "interval_hours": 24},
        )
        self.assertEqual(schedule_rejected.status, 409)
        schedule = await self.client.put(
            "/api/v7/studio/danbooru/schedule",
            json={"confirm_manual": True, "enabled": True, "interval_hours": 24},
        )
        self.assertTrue((await schedule.json())["enabled"])
        scheduled = await self.client.post(
            "/api/v7/studio/danbooru/schedule/run",
            json={"confirm_manual": True, "force": True},
        )
        scheduled_task = await self.wait_terminal((await scheduled.json())["run_id"])
        self.assertEqual(
            scheduled_task["result"]["operation"]["result"]["tag_count"],
            6,
        )

    async def test_lora_workflows_models_and_logs(self) -> None:
        blocked_detail = await self.client.post(
            "/api/v7/studio/loras/detail",
            json={"filename": "styles/ink.safetensors"},
        )
        self.assertEqual(blocked_detail.status, 409)
        detail = await (
            await self.client.post(
                "/api/v7/studio/loras/detail",
                json={
                    "confirm_manual": True,
                    "filename": "styles/ink.safetensors",
                },
            )
        ).json()
        self.assertEqual(detail["filename"], "styles/ink.safetensors")
        workflows = await (await self.client.get("/api/v7/studio/workflows")).json()
        self.assertEqual(workflows["count"], 1)

        blocked = await self.client.post(
            "/api/v7/studio/models/quarantine",
            json={
                "kind": "lora",
                "exact_name": "styles/ink.safetensors",
                "confirm_name": "styles/ink.safetensors",
            },
        )
        self.assertEqual(blocked.status, 409)
        quarantined = await self.client.post(
            "/api/v7/studio/models/quarantine",
            json={
                "confirm_manual": True,
                "kind": "lora",
                "exact_name": "styles/ink.safetensors",
                "confirm_name": "styles/ink.safetensors",
            },
        )
        self.assertEqual(quarantined.status, 201)
        entry = await quarantined.json()
        restored = await self.client.post(
            f"/api/v7/studio/models/quarantine/{entry['id']}/restore",
            json={"confirm_manual": True, "confirm_name": "styles/ink.safetensors"},
        )
        self.assertTrue((await restored.json())["restored"])

        refreshed = await self.client.post(
            "/api/v7/studio/models/refresh", json={"confirm_manual": True}
        )
        self.assertEqual((await refreshed.json())["unets"], ["anima.safetensors"])
        logs = await (await self.client.get("/api/v7/studio/logs")).text()
        self.assertNotIn("never-return-this", logs)
        self.assertNotIn("also-secret", logs)
        self.assertIn("[REDACTED]", logs)

    async def test_prompt_plan_crud_conflicts_and_filtered_log_clear(self) -> None:
        created_response = await self.client.post(
            "/api/v7/studio/prompt-plans",
            json={
                "id": "portrait",
                "name": "Portrait",
                "description": "Saved portrait plan",
                "plan": {"positive_prompt": "portrait lighting"},
            },
        )
        self.assertEqual(created_response.status, 201)
        created = await created_response.json()
        self.assertEqual(created["revision"], 1)
        self.assertEqual(len(created["digest"]), 64)
        fetched = await (
            await self.client.get("/api/v7/studio/prompt-plans/portrait")
        ).json()
        self.assertEqual(fetched["plan"]["positive_prompt"], "portrait lighting")

        stale = await self.client.put(
            "/api/v7/studio/prompt-plans/portrait",
            json={
                **created,
                "revision": 0,
                "plan": {"positive_prompt": "stale"},
            },
        )
        self.assertEqual(stale.status, 409)
        self.assertEqual((await stale.json())["code"], "prompt_plan_conflict")
        updated_response = await self.client.put(
            "/api/v7/studio/prompt-plans/portrait",
            json={
                **created,
                "plan": {"positive_prompt": "updated portrait"},
            },
        )
        self.assertEqual(updated_response.status, 200)
        updated = await updated_response.json()
        self.assertEqual(updated["revision"], 2)
        self.assertNotEqual(updated["digest"], created["digest"])

        stale_delete = await self.client.delete(
            "/api/v7/studio/prompt-plans/portrait",
            json={"revision": 1, "digest": created["digest"]},
        )
        self.assertEqual(stale_delete.status, 409)
        deleted = await self.client.delete(
            "/api/v7/studio/prompt-plans/portrait",
            json={"revision": updated["revision"], "digest": updated["digest"]},
        )
        self.assertEqual(deleted.status, 200)
        self.assertTrue((await deleted.json())["deleted"])

        logs_response = await self.client.get(
            "/api/v7/studio/logs?level=ERROR&category=generation&filter=failed"
        )
        logs = await logs_response.json()
        self.assertEqual([item["seq"] for item in logs["items"]], [2])
        level_response = await self.client.put(
            "/api/v7/studio/logs/level", json={"level": "WARNING"}
        )
        self.assertEqual(level_response.status, 200)
        self.assertEqual((await level_response.json())["level"], "WARNING")
        invalid_level = await self.client.put(
            "/api/v7/studio/logs/level", json={"level": "TRACE"}
        )
        self.assertEqual(invalid_level.status, 400)
        rejected_clear = await self.client.delete("/api/v7/studio/logs", json={})
        self.assertEqual(rejected_clear.status, 409)
        cleared = await self.client.delete(
            "/api/v7/studio/logs", json={"confirm_manual": True}
        )
        self.assertEqual((await cleared.json())["cleared"], 2)

    async def test_running_studio_operation_can_be_cancelled_by_its_owner(self) -> None:
        self.services.loras.refresh_gate = asyncio.Event()
        accepted = await self.client.post(
            "/api/v7/studio/loras/refresh", json={"confirm_manual": True}
        )
        run_id = (await accepted.json())["run_id"]
        for _ in range(100):
            if (await self.runtime.get(run_id))["status"] == "running":
                break
            await asyncio.sleep(0)
        cancelled = await self.client.post(
            f"/api/v7/studio/operations/{run_id}/cancel", json={}
        )
        self.assertEqual(cancelled.status, 200)
        self.assertEqual((await cancelled.json())["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
