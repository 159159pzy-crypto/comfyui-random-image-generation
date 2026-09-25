import copy
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from aiohttp.test_utils import TestClient, TestServer

from anima_webui.launcher import configure_resources
from anima_webui.server import create_app
from anima_webui.workflow import DEFAULT_SETTINGS, WorkflowError, WorkflowTemplates

ROOT = Path(__file__).resolve().parents[1]


class ResourceMappingTests(unittest.TestCase):
    def test_subfolders_remap_templates_and_defaults_without_global_mutation(self):
        templates = WorkflowTemplates.load(ROOT / "templates")
        original = copy.deepcopy(DEFAULT_SETTINGS)
        defaults = configure_resources(templates, {
            "miaomiaoHarem_anima14.safetensors": "Anima/miaomiaoHarem_anima14.safetensors",
            "qwen_3_06b_base.safetensors": "Anima/qwen_3_06b_base.safetensors",
        })
        self.assertEqual(defaults["model_name"], "Anima/miaomiaoHarem_anima14.safetensors")
        self.assertEqual(templates.api["17"]["inputs"]["clip_name"], "Anima/qwen_3_06b_base.safetensors")
        self.assertFalse(defaults["hires"]["enabled"])
        self.assertEqual(DEFAULT_SETTINGS, original)

    def test_legacy_launch_preserves_defaults(self):
        templates = WorkflowTemplates.load(ROOT / "templates")
        self.assertEqual(configure_resources(templates, None), DEFAULT_SETTINGS)

    def test_mapping_rejects_unsafe_paths(self):
        templates = WorkflowTemplates.load(ROOT / "templates")
        with self.assertRaises(WorkflowError):
            configure_resources(templates, {"model.safetensors": "../outside.safetensors"})


class LauncherStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_identifies_application_and_separate_data_directory(self):
        with TemporaryDirectory() as temp:
            app = create_app(data_dir=temp)
            async with TestClient(TestServer(app)) as client:
                response = await client.get("/api/launcher-health")
                health = await response.json()
                self.assertEqual(health["app"], "anima-random-studio")
                self.assertEqual(Path(health["data_dir"]), Path(temp).resolve())
                self.assertTrue((Path(temp) / "history.sqlite3").is_file())
                self.assertEqual(Path(health["app_dir"]), ROOT)
