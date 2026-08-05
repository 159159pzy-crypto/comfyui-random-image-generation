import asyncio
import tempfile
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from anima_natural.studio import StudioServices
from anima_studio.studio_services import LoraRecord
from anima_webui.task_runtime import StudioTaskRuntime
from anima_webui.v6_routes import setup_v6_routes

APP_DIR = Path(__file__).resolve().parents[1]


class FakeCatalog:
    def __init__(self) -> None:
        self.calls = 0

    async def list_loras(self, *, force: bool = False):
        self.calls += 1
        return (LoraRecord(name="styles/example.safetensors"),)


class FakeVisuals:
    def build_manifest(self, records):
        return {"count": len(records), "items": []}

    def list_page(self, records, **filters):
        return {
            "total": len(records),
            "page": filters.get("page", 1),
            "items": [],
        }


class FakeDownloader:
    async def download_from_url(self, url: str):
        return {"url": url, "downloaded": True}


class FakeDanbooruBuilder:
    def checkpoint_status(self):
        return {"available": True, "tag_count": 0}

    async def build(self, options, *, progress=None, cancel_event=None):
        if progress is not None:
            result = progress({"event": "page", "message": "one page"})
            if asyncio.iscoroutine(result):
                await result
        return {"mode": options.mode, "tag_count": 1}


class V6RoutesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.models = self.root / "models" / "loras"
        self.models.mkdir(parents=True)
        self.catalog = FakeCatalog()
        self.services = StudioServices.create_local(
            self.root / "studio",
            workflow_dir=APP_DIR / "anima_natural" / "upstream" / "workflow",
            model_roots={"lora": [self.models]},
            lora_catalog=self.catalog,
            lora_visuals=FakeVisuals(),
            lora_downloader=FakeDownloader(),
            danbooru_builder=FakeDanbooruBuilder(),
        )
        self.runtime = StudioTaskRuntime(self.root / "tasks.sqlite3")
        app = web.Application()

        async def llm_callback(system_prompt: str, user_prompt: str) -> str:
            return "{}"

        self.operations = setup_v6_routes(
            app,
            services=self.services,
            runtime=self.runtime,
            llm_callback=llm_callback,
        )
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.operations.close()
        await self.client.close()
        await self.runtime.close()
        self.temp.cleanup()

    async def _wait_terminal(self, run_id: str) -> dict:
        for _ in range(100):
            task = await self.runtime.get(run_id)
            if task["status"] in {
                "succeeded",
                "partial",
                "failed",
                "cancelled",
                "timed_out",
                "interrupted",
            }:
                return task
            await asyncio.sleep(0.01)
        self.fail("studio operation did not reach a terminal state")

    async def test_capabilities_prompt_assets_and_prompt_lab(self):
        capabilities = await (await self.client.get("/api/v6/capabilities")).json()
        self.assertEqual(capabilities["version"], 6)
        self.assertTrue(capabilities["capabilities"]["prompt_assets"]["ready"])
        self.assertTrue(capabilities["capabilities"]["lora_catalog"]["manual_only"])

        imported = await self.client.post(
            "/api/v6/prompt-assets/import",
            json={
                "assets": [
                    {
                        "asset_id": "pa_11111111111111111111111111111111",
                        "asset_type": "clothing",
                        "name_en": "Native coat",
                        "tags": ["coat"],
                        "categories": ["Casual & Daily", "Dress & Gown"],
                        "traits": ["layered"],
                    }
                ]
            },
        )
        self.assertEqual(imported.status, 201)
        listing = await (
            await self.client.get("/api/v6/prompt-assets?q=coat&asset_type=clothing")
        ).json()
        self.assertEqual(
            listing["items"][0]["categories"],
            ["Casual & Daily", "Dress & Gown"],
        )

        generated = await self.client.post(
            "/api/v6/prompt-lab/candidates",
            json={
                "seed": 7,
                "count": 2,
                "base_layers": {"identity": ["1girl"]},
                "asset_pools": {"clothing": ["coat", "dress"]},
                "locked_layers": ["identity"],
            },
        )
        self.assertEqual(generated.status, 201)
        batch = await generated.json()
        confirmed = await self.client.post(
            f"/api/v6/prompt-lab/{batch['batch_id']}/confirm",
            json={"selection": 1},
        )
        self.assertEqual(confirmed.status, 200)
        draft = await confirmed.json()
        self.assertEqual(draft["anchors"], [["1girl", "character"]])

    async def test_profiles_are_secret_free_and_quarantine_is_reversible(self):
        created = await self.client.post(
            "/api/v6/config-profiles",
            json={
                "name": "Local",
                "config": {
                    "comfyui_url": "http://127.0.0.1:8188",
                    "api_token": "must-not-leak",
                },
            },
        )
        self.assertEqual(created.status, 201)
        self.assertNotIn("must-not-leak", await created.text())
        profiles = await (await self.client.get("/api/v6/config-profiles")).json()
        self.assertEqual(profiles["items"][0]["name"], "Local")

        model = self.models / "characters" / "alice.safetensors"
        model.parent.mkdir()
        model.write_bytes(b"model")
        blocked = await self.client.post(
            "/api/v6/quarantine",
            json={
                "kind": "lora",
                "exact_name": "characters/alice.safetensors",
                "confirm_name": "characters/alice.safetensors",
                "references": ["characters/alice.safetensors"],
            },
        )
        self.assertEqual(blocked.status, 409)
        quarantined = await self.client.post(
            "/api/v6/quarantine",
            json={
                "kind": "lora",
                "exact_name": "characters/alice.safetensors",
                "confirm_name": "characters/alice.safetensors",
            },
        )
        self.assertEqual(quarantined.status, 201)
        entry = await quarantined.json()
        self.assertFalse(model.exists())
        restored = await self.client.post(
            f"/api/v6/quarantine/{entry['id']}/restore",
            json={"confirm_name": "characters/alice.safetensors"},
        )
        self.assertEqual(restored.status, 200)
        self.assertEqual(model.read_bytes(), b"model")

    async def test_external_actions_require_confirmation_and_use_task_runtime(self):
        rejected = await self.client.post(
            "/api/v6/loras/refresh", json={"confirm_manual": False}
        )
        self.assertEqual(rejected.status, 409)
        self.assertEqual((await rejected.json())["code"], "manual_confirmation_required")
        self.assertEqual(await self.runtime.list(), [])

        accepted = await self.client.post(
            "/api/v6/loras/refresh", json={"confirm_manual": True}
        )
        self.assertEqual(accepted.status, 202)
        queued = await accepted.json()
        finished = await self._wait_terminal(queued["run_id"])
        self.assertEqual(finished["status"], "succeeded")
        self.assertEqual(finished["result"]["operation"]["record_count"], 1)
        self.assertEqual(self.catalog.calls, 1)

        built = await self.client.post(
            "/api/v6/danbooru/build",
            json={"confirm_manual": True, "mode": "identity", "max_records": 1},
        )
        self.assertEqual(built.status, 202)
        danbooru_task = await self._wait_terminal((await built.json())["run_id"])
        self.assertEqual(danbooru_task["status"], "succeeded")
        events = await self.runtime.events(run_id=danbooru_task["run_id"])
        self.assertTrue(any(item["event_code"] == "page" for item in events["entries"]))


if __name__ == "__main__":
    unittest.main()
