from __future__ import annotations

import asyncio
import io
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from anima_natural.assets import AssetError, AssetStore
from anima_natural.engine import NaturalEngine, NaturalEngineError
from anima_natural.jobs import NaturalJobManager
from anima_natural.providers import (
    MemorySecretStore,
    NativeNaturalPlanner,
    NativePlanningToolRegistry,
)


APP_DIR = Path(__file__).resolve().parents[1]


def image_bytes(color: str = "red") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(output, format="PNG")
    return output.getvalue()


class CountingComfy:
    def __init__(self) -> None:
        self.interrupts = 0

    async def interrupt(self) -> None:
        self.interrupts += 1


class NaturalCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        engine = SimpleNamespace(root=root, data_dir=root)
        self.comfy = CountingComfy()
        self.manager = NaturalJobManager(engine, self.comfy, None, asyncio.Lock())

    async def asyncTearDown(self) -> None:
        await self.manager.close()
        self.temp.cleanup()

    def _add_job(self, job_id: str, state: str) -> None:
        now = time.time()
        job = {
            "id": job_id,
            "state": state,
            "stage": state,
            "message": state,
            "created_at": now,
            "updated_at": now,
            "job_type": "text_to_image",
            "pipeline": "base",
            "plan": {},
            "progress": {"completed": 0, "total": 1},
            "images": [],
            "error": "",
            "payload": {},
            "stop_requested": False,
            "events": [],
        }
        self.manager.jobs[job_id] = job
        self.manager.task_store.create_task(
            "natural_generation", run_id=job_id, metadata={"job": job}
        )

    async def test_queued_cancellation_does_not_interrupt_comfy(self) -> None:
        self._add_job("job_queued", "queued")

        cancelled = await self.manager.cancel("job_queued")

        self.assertEqual(cancelled["state"], "cancelling")
        self.assertEqual(self.comfy.interrupts, 0)

    async def test_only_prompt_owner_can_interrupt_comfy(self) -> None:
        self._add_job("job_running", "running")
        self.manager._execution_owner = "job_running"
        self.manager._prompt_owner = ("job_running", "prompt-1")

        await self.manager.cancel("job_running")

        self.assertEqual(self.comfy.interrupts, 1)


class NaturalPlanRevisionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp.name) / "natural"
        self.engine = NaturalEngine(
            APP_DIR,
            self.data_dir,
            secret_store=MemorySecretStore(),
        )

    async def asyncTearDown(self) -> None:
        await self.engine.close()
        self.temp.cleanup()

    async def test_override_creates_validated_revision_and_rejects_stale_reference(self) -> None:
        original = await self.engine.plan(
            {
                "job_type": "text_to_image",
                "text": "1girl, portrait",
                "use_llm": False,
                "pipeline": "base",
            }
        )
        reference = {
            "id": original["id"],
            "revision": original["revision"],
            "digest": original["digest"],
        }

        revised = await self.engine.plan(
            {
                "plan_revision": reference,
                "overrides": {
                    "positive_prompt": "1girl, blue hair, portrait",
                    "negative_prompt": "low quality",
                },
            }
        )

        self.assertEqual(revised["id"], original["id"])
        self.assertEqual(revised["revision"], 2)
        self.assertNotEqual(revised["digest"], original["digest"])
        self.assertEqual(revised["positive_prompt"], "1girl, blue hair, portrait")
        with self.assertRaises(NaturalEngineError) as raised:
            await self.engine.plan(
                {"plan_revision": reference, "overrides": {"negative_prompt": "blur"}}
            )
        self.assertEqual(raised.exception.code, "plan_revision_conflict")

    async def test_v7_canonical_positive_prompt_is_a_planning_input(self) -> None:
        plan = await self.engine.plan(
            {
                "job_type": "text_to_image",
                "positive_prompt": "1girl, seaside portrait",
                "use_llm": False,
                "pipeline": "base",
            }
        )

        self.assertEqual(plan["source_text"], "1girl, seaside portrait")
        self.assertIn("seaside portrait", plan["positive_prompt"])

    async def test_frozen_v7_plan_skips_provider_planning_during_execution(self) -> None:
        manager = NaturalJobManager(
            self.engine,
            SimpleNamespace(),
            None,
            asyncio.Lock(),
        )

        async def unexpected_plan(_payload):
            raise AssertionError("the frozen V7 intent must not be planned again")

        self.engine.plan = unexpected_plan
        try:
            job = await manager.create(
                {
                    "job_type": "text_to_image",
                    "positive_prompt": "1girl, seaside portrait",
                    "preview_only": True,
                },
                frozen_plan={
                    "job_type": "text_to_image",
                    "pipeline": "base",
                    "positive_prompt": "1girl, seaside portrait",
                    "negative_prompt": "low quality",
                    "requires_confirmation": [],
                },
            )
        finally:
            await manager.close()

        self.assertEqual(job["state"], "completed")
        self.assertEqual(job["plan"]["positive_prompt"], "1girl, seaside portrait")

    async def test_plan_snapshot_survives_engine_restart(self) -> None:
        original = await self.engine.plan(
            {
                "job_type": "text_to_image",
                "text": "1girl, portrait",
                "use_llm": False,
            }
        )
        await self.engine.close()
        self.engine = NaturalEngine(
            APP_DIR,
            self.data_dir,
            secret_store=MemorySecretStore(),
        )

        restored = await self.engine.plan(
            {
                "plan_revision": {
                    "id": original["id"],
                    "revision": original["revision"],
                    "digest": original["digest"],
                }
            }
        )

        self.assertEqual(restored, original)


class NativePlannerToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_planner_uses_exact_aliases_and_surfaces_fuzzy_confirmation(self) -> None:
        class Gateway:
            async def complete(self, *_args, **_kwargs):
                raise AssertionError("configured planning assets must use the bounded tool loop")

            async def tool_loop(self, *, tools, **_kwargs):
                self.lora = tools["resolve_lora"][1]({"query": "水墨"})
                self.artist = tools["resolve_artist"][1]({"query": "安米"})
                self.preset = tools["resolve_preset"][1]({"query": "Soft"})
                self.asset = tools["resolve_prompt_asset"][1]({"query": "电影光"})
                self.plan = tools["resolve_prompt_plan"][1]({"query": "plan_saved"})
                self.character = tools["resolve_character_alias"][1]({"query": "初音"})
                return (
                    '{"positive_prompt":"1girl, ink","character_queries":[]}',
                    "director_one",
                )

        gateway = Gateway()
        registry = NativePlanningToolRegistry(
            artists=[{"id": "artist_anmi", "name": "anmi", "aliases": ["安米"]}],
            loras=[{"filename": "styles/ink.safetensors", "aliases": ["水墨"]}],
            presets=[{"id": "soft", "name": "Soft Light"}],
            prompt_assets=[{"id": "asset_light", "name": "cinematic light", "aliases": ["电影光"]}],
            prompt_plans=[{"id": "plan_saved", "name": "Saved portrait"}],
            character_aliases=[
                {
                    "id": "miku",
                    "name": "Hatsune Miku",
                    "canonical_tag": "hatsune miku",
                    "aliases": ["初音"],
                }
            ],
        )
        planner = NativeNaturalPlanner(gateway, "missing-reference.md", tools=registry)

        instruction, provider_id = await planner.generate_instruction("画一张安米水墨风初音")

        self.assertEqual(provider_id, "director_one")
        self.assertEqual(instruction.artist_tags, ("@anmi",))
        self.assertEqual(instruction.loras[0].filename, "styles/ink.safetensors")
        self.assertEqual(instruction.prompt_asset_ids, ("asset_light",))
        self.assertEqual(instruction.prompt_plan_id, "plan_saved")
        self.assertIn("hatsune miku", instruction.character_queries)
        self.assertEqual(instruction.style_preset_id, "")
        self.assertEqual(len(instruction.requires_confirmation), 1)
        self.assertEqual(instruction.requires_confirmation[0]["kind"], "preset")
        self.assertEqual(gateway.lora["status"], "matched")
        self.assertEqual(gateway.artist["status"], "matched")
        self.assertEqual(gateway.preset["status"], "needs_confirmation")
        self.assertIn("loras", instruction.sources)
        self.assertIn("artist_tags", instruction.sources)


class NaturalAssetRecoveryTests(unittest.TestCase):
    def test_restart_recovers_valid_upload_and_prunes_orphans(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = AssetStore(root).add(image_bytes())
            (root / ".upload-abandoned.tmp").write_bytes(b"partial")
            (root / "asset_0000000000000000.png").write_bytes(b"not an image")

            reopened = AssetStore(root)

            self.assertEqual(reopened.get(original.id).sha256, original.sha256)
            self.assertFalse((root / ".upload-abandoned.tmp").exists())
            self.assertFalse((root / "asset_0000000000000000.png").exists())

    def test_restart_prunes_expired_upload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = AssetStore(root, ttl_seconds=3600).add(image_bytes())
            old = time.time() - 10
            os.utime(original.path, (old, old))

            reopened = AssetStore(root, ttl_seconds=1)

            with self.assertRaises(AssetError):
                reopened.get(original.id)
            self.assertFalse(original.path.exists())


if __name__ == "__main__":
    unittest.main()
