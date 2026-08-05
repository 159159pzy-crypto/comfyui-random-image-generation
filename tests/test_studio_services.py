import tempfile
import threading
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from anima_natural.studio import (
    DanbooruStudioService,
    LoraStudioService,
    ManualActionRequiredError,
    ModelQuarantineError,
    ModelQuarantineService,
    PromptStudioService,
    StudioServices,
)
from anima_studio.studio_services import (
    LoraCatalogService,
    LoraRecord,
    LoraVisualService,
    PromptAssetLibrary,
    PromptPlanConflictError,
    PromptPlanStore,
)


class PromptPlanStoreTests(unittest.TestCase):
    def test_persists_revision_digest_and_rejects_stale_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "studio.sqlite3"
            store = PromptPlanStore(path)
            created = store.create(
                {
                    "id": "portrait",
                    "name": "Portrait",
                    "description": "A reusable portrait plan",
                    "plan": {
                        "positive_prompt": "soft portrait light",
                        "negative_prompt": "blur",
                    },
                }
            )
            self.assertEqual(created["revision"], 1)
            self.assertEqual(len(created["digest"]), 64)
            self.assertEqual(
                PromptPlanStore(path).get("portrait")["plan"]["negative_prompt"],
                "blur",
            )

            with self.assertRaises(PromptPlanConflictError):
                store.update(
                    "portrait",
                    {**created, "plan": {"positive_prompt": "stale"}},
                    expected_revision=0,
                    expected_digest=created["digest"],
                )
            updated = store.update(
                "portrait",
                {**created, "plan": {"positive_prompt": "rim light"}},
                expected_revision=created["revision"],
                expected_digest=created["digest"],
            )
            self.assertEqual(updated["revision"], 2)
            self.assertNotEqual(updated["digest"], created["digest"])
            with self.assertRaises(PromptPlanConflictError):
                store.delete(
                    "portrait",
                    expected_revision=created["revision"],
                    expected_digest=created["digest"],
                )
            removed = store.delete(
                "portrait",
                expected_revision=updated["revision"],
                expected_digest=updated["digest"],
            )
            self.assertEqual(removed["id"], "portrait")
            with self.assertRaises(KeyError):
                store.get("portrait")


class PromptStudioServiceTests(unittest.TestCase):
    def test_native_taxonomy_round_trips_and_prompt_lab_confirms(self):
        with tempfile.TemporaryDirectory() as directory:
            service = PromptStudioService(
                PromptAssetLibrary(Path(directory) / "assets.sqlite3")
            )
            imported = service.import_native_assets(
                [
                    {
                        "asset_id": "pa_11111111111111111111111111111111",
                        "asset_type": "clothing",
                        "name_en": "Native coat",
                        "tags": ["coat", "red coat"],
                        "categories": ["Casual & Daily", "Dress & Gown"],
                        "traits": ["layered"],
                    }
                ]
            )
            self.assertEqual(imported["last_import_count"], 1)
            result = service.search(asset_type="clothing")
            self.assertEqual(result["items"][0]["categories"], [
                "Casual & Daily",
                "Dress & Gown",
            ])

            batch = service.generate_batch(
                seed=17,
                count=2,
                base_layers={"identity": ["1girl"]},
                asset_pools={
                    "clothing": [
                        {
                            "asset_id": "native-clothing-1",
                            "label": "Native coat",
                            "tags": ["coat"],
                        },
                        {
                            "asset_id": "native-clothing-2",
                            "label": "Native dress",
                            "tags": ["dress"],
                        },
                    ]
                },
                locked_layers=["identity"],
            )
            draft = service.confirm_candidate(batch, 1)
            self.assertEqual(draft["anchors"], [["1girl", "character"]])
            self.assertTrue(set(draft["hard_tags"]) & {"coat", "dress"})
            self.assertEqual(service.snapshot()["library"]["asset_count"], 1)


@dataclass(frozen=True)
class _VisualResult:
    count: int


class _FakeCatalog:
    def __init__(self):
        self.forces = []

    async def list_loras(self, *, force=False):
        self.forces.append(force)
        return (LoraRecord(name="people/example.safetensors"),)


class _FakeVisuals:
    def build_manifest(self, records):
        return _VisualResult(len(records))

    def list_page(self, records, **filters):
        return {"total": len(records), "query": filters.get("query", "")}


class _FakeAnalyzer:
    async def run(self, details, callback, **options):
        return {"selected_count": len(details), "run_id": options.get("run_id", "")}


class _FakeArchiver:
    def catalog_status(self, records):
        return {"current_count": len(records)}

    async def archive_with_llm(self, records, callback, **options):
        return {"archived": len(records), "provider": options["provider"]}


class _FakeDownloader:
    async def download_from_url(self, url):
        return {"url": url, "downloaded": True}


class LoraStudioServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_external_lora_operations_are_manual_and_injectable(self):
        catalog = _FakeCatalog()
        service = LoraStudioService(
            catalog=catalog,
            visuals=_FakeVisuals(),
            analyzer=_FakeAnalyzer(),
            archiver=_FakeArchiver(),
            downloader=_FakeDownloader(),
        )
        with self.assertRaises(ManualActionRequiredError):
            await service.refresh_catalog()

        snapshot = await service.refresh_catalog(confirm_manual=True)
        self.assertEqual(snapshot["record_count"], 1)
        self.assertEqual(catalog.forces, [True])
        self.assertEqual(service.visual_manifest(), {"count": 1})
        self.assertEqual(service.visual_page(query="example")["total"], 1)
        analyzed = await service.analyze(
            [object()], lambda *_: None, confirm_manual=True, run_id="run-1"
        )
        self.assertEqual(analyzed["run_id"], "run-1")
        archived = await service.archive(
            lambda *_: None,
            confirm_manual=True, provider="local-provider"
        )
        self.assertEqual(archived["archived"], 1)
        downloaded = await service.download(
            "https://civitai.com/models/1", confirm_manual=True
        )
        self.assertTrue(downloaded["downloaded"])

    async def test_native_catalog_scans_local_files_and_builds_v3_detail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "styles" / "ink.safetensors"
            model.parent.mkdir()
            model.write_bytes(b"native lora")
            settings = SimpleNamespace(
                lora_visual_roots=[str(root)],
                lora_catalog_url="",
                comfyui_url="",
                lora_max_results=50,
            )
            catalog = LoraCatalogService(settings)
            records = await catalog.list_loras(force=True)
            self.assertEqual([item.name for item in records], ["styles/ink.safetensors"])
            detail = await catalog.get_detail_v2(records[0])
            self.assertEqual(detail["schema_version"], 3)
            self.assertEqual(len(detail["sha256"]), 64)

            visuals = LoraVisualService([root], root / "cache")
            manifest = visuals.build_manifest(records)
            self.assertEqual(manifest["items"][0]["filename"], records[0].name)
            await catalog.close()


class _FakeDanbooruBuilder:
    def __init__(self):
        self.calls = 0

    def checkpoint_status(self):
        return {"available": True, "tag_count": 3}

    async def build(self, options, *, progress=None, cancel_event=None):
        self.calls += 1
        self.cancel_event = cancel_event
        return {"mode": options.mode, "tag_count": 4}


class DanbooruStudioServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_builder_never_runs_without_manual_confirmation(self):
        backend = _FakeDanbooruBuilder()
        service = DanbooruStudioService(backend)
        with self.assertRaises(ManualActionRequiredError):
            await service.build({"mode": "identity"})
        cancellation = threading.Event()
        result = await service.build(
            {"mode": "identity"},
            confirm_manual=True,
            cancel_event=cancellation,
        )
        self.assertEqual(result["tag_count"], 4)
        self.assertIs(backend.cancel_event, cancellation)
        self.assertEqual(service.snapshot()["checkpoint"]["tag_count"], 3)


class ModelQuarantineServiceTests(unittest.TestCase):
    def test_quarantine_blocks_references_and_restores_verified_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            loras = root / "loras"
            model = loras / "characters" / "alice.safetensors"
            model.parent.mkdir(parents=True)
            model.write_bytes(b"model bytes")
            service = ModelQuarantineService(
                {"lora": [loras]}, root / "project-data" / "quarantine"
            )

            with self.assertRaisesRegex(ModelQuarantineError, "still referenced"):
                service.quarantine(
                    "lora",
                    "characters/alice.safetensors",
                    confirm_name="characters/alice.safetensors",
                    references=["characters/alice.safetensors"],
                )
            with self.assertRaisesRegex(ModelQuarantineError, "exactly match"):
                service.quarantine(
                    "lora",
                    "characters/alice.safetensors",
                    confirm_name="alice.safetensors",
                )

            entry = service.quarantine(
                "lora",
                "characters/alice.safetensors",
                confirm_name="characters/alice.safetensors",
            )
            self.assertFalse(model.exists())
            self.assertEqual(service.snapshot()["entry_count"], 1)
            restored = service.restore(
                entry["id"], confirm_name="characters/alice.safetensors"
            )
            self.assertEqual(restored["sha256"], entry["sha256"])
            self.assertEqual(model.read_bytes(), b"model bytes")
            self.assertEqual(service.snapshot()["entry_count"], 0)
            audit_lines = service.audit_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(audit_lines), 2)

    def test_exact_name_cannot_escape_or_select_ambiguous_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first"
            second = root / "second"
            for model_root in (first, second):
                model_root.mkdir()
                (model_root / "same.safetensors").write_bytes(b"same")
            service = ModelQuarantineService(
                {"checkpoint": [first, second]}, root / "quarantine"
            )
            with self.assertRaises(ModelQuarantineError):
                service.quarantine(
                    "checkpoint", "../same.safetensors", confirm_name="../same.safetensors"
                )
            with self.assertRaisesRegex(ModelQuarantineError, "ambiguous"):
                service.quarantine(
                    "checkpoint", "same.safetensors", confirm_name="same.safetensors"
                )


class AggregateStudioServicesTests(unittest.TestCase):
    def test_local_factory_reports_disabled_live_integrations_and_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            services = StudioServices.create_local(directory)
            capabilities = services.capabilities()
            self.assertTrue(capabilities["prompt_lab"]["ready"])
            self.assertFalse(capabilities["lora_catalog"]["ready"])
            self.assertTrue(capabilities["danbooru_builder"]["manual_only"])

            profile = services.workflows.save_profile(
                "Local",
                {
                    "comfyui_url": "http://127.0.0.1:8188",
                    "api_token": "must-not-leak",
                },
            )
            self.assertNotIn("api_token", profile["settings"])
            exported = services.workflows.export_profile("Local")
            self.assertNotIn("must-not-leak", str(exported))
            snapshot = services.snapshot()
            self.assertEqual(snapshot["version"], 6)
            self.assertGreater(len(snapshot["workflows"]["workflows"]), 0)


if __name__ == "__main__":
    unittest.main()
