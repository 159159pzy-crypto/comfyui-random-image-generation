"""Discovery diagnostics are usable through the real local HTTP API."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

from anima_webui.catalog import DATA_FILES, EXPRESSION_DATA
from anima_webui.server import create_app
from test_server import APP_DIR, FAKE_TOOLS_DATASETS, FakeComfy


class ToolsDiscoveryApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="anima-tools-api-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app_dir = self.root / "工作目录" / "Anima Random"
        self.app_dir.mkdir(parents=True)
        for directory in ("static", "templates"):
            shutil.copytree(APP_DIR / directory, self.app_dir / directory)
        # No developer environment variable may pull in a real local catalogue.
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.comfy = FakeComfy()
        self.run_number = 0

    def write_tools(self, path):
        (path / "js").mkdir(parents=True)
        for filename, items in FAKE_TOOLS_DATASETS.items():
            (path / "js" / filename).write_text(
                f"const data = {json.dumps(items, ensure_ascii=False)};\n",
                encoding="utf-8",
            )
        return path.resolve()

    async def read_config(self, **kwargs):
        self.run_number += 1
        app = create_app(
            app_dir=self.app_dir,
            data_dir=self.root / f"data-{self.run_number}",
            comfy=self.comfy,
            **kwargs,
        )
        async with TestClient(TestServer(app)) as client:
            response = await client.get("/api/config")
            self.assertEqual(response.status, 200)
            payload = await response.json()
        self.assertEqual(self.comfy.submissions, [])
        return payload

    def assert_fixture_counts(self, catalog):
        expected = {
            section: len(FAKE_TOOLS_DATASETS[filename])
            for section, filename in DATA_FILES.items()
        }
        expected["expression"] = len(EXPRESSION_DATA)
        self.assertEqual(catalog["counts"], expected)

    async def test_portable_layout_is_discovered_and_exposed_with_real_counts(self):
        nodes = self.app_dir.parent / "ComfyUI_windows_portable" / "ComfyUI" / "custom_nodes"
        tools = self.write_tools(nodes / "Comfyui-Anima-Tools-main")

        payload = await self.read_config()
        catalog = payload["catalog"]
        discovery = catalog["discovery"]
        self.assertTrue(catalog["available"])
        self.assertEqual(catalog["path"], str(tools))
        self.assertEqual(discovery["status"], "available")
        self.assertEqual(discovery["source"], "auto")
        self.assertEqual(discovery["candidates"], [str(tools)])
        self.assertIn(str(nodes.resolve()), discovery["searched_paths"])
        self.assertEqual(discovery["message"], "")
        self.assert_fixture_counts(catalog)

    async def test_missing_catalog_reports_searched_paths_and_recovery_in_config(self):
        nodes = self.app_dir.parent / "ComfyUI_windows_portable" / "ComfyUI" / "custom_nodes"
        nodes.mkdir(parents=True)

        payload = await self.read_config()
        catalog = payload["catalog"]
        discovery = catalog["discovery"]
        self.assertFalse(catalog["available"])
        self.assertEqual(catalog["path"], "")
        self.assertEqual(discovery["status"], "missing")
        searched = discovery["searched_paths"]
        self.assertIn(str((self.app_dir / "custom_nodes").resolve()), searched)
        self.assertIn(str(nodes.resolve()), searched)
        self.assertTrue(all(Path(path).is_absolute() for path in searched))
        self.assertEqual(len(searched), len({os.path.normcase(path) for path in searched}))
        self.assertIn("--anima-tools-dir", discovery["message"])
        for path in searched:
            self.assertIn(path, discovery["message"])
        self.assertIn(discovery["message"], payload["warnings"])
        self.assertEqual({section: catalog["counts"][section] for section in DATA_FILES},
                         dict.fromkeys(DATA_FILES, 0))
        self.assertEqual(catalog["counts"]["expression"], len(EXPRESSION_DATA))

    async def test_explicit_path_reports_no_automatic_search_and_does_not_fall_back(self):
        self.write_tools(self.app_dir.parent / "ComfyUI" / "custom_nodes" / "Comfyui-Anima-Tools")
        explicit = self.root / "显式 词库"

        invalid = await self.read_config(anima_tools_dir=explicit)
        discovery = invalid["catalog"]["discovery"]
        self.assertFalse(invalid["catalog"]["available"])
        self.assertEqual(discovery["status"], "invalid")
        self.assertEqual(discovery["source"], "argument")
        self.assertEqual(discovery["searched_paths"], [])
        self.assertEqual(discovery["path"], str(explicit.resolve()))
        self.assertIn(str(explicit.resolve()), discovery["message"])
        self.assertIn("js/character_data.js", discovery["missing_files"])
        self.assertIn(discovery["message"], invalid["warnings"])

        self.write_tools(explicit)
        valid = await self.read_config(anima_tools_dir=explicit)
        discovery = valid["catalog"]["discovery"]
        self.assertTrue(valid["catalog"]["available"])
        self.assertEqual(discovery["status"], "available")
        self.assertEqual(discovery["searched_paths"], [])
        self.assertEqual(discovery["path"], str(explicit.resolve()))
        self.assert_fixture_counts(valid["catalog"])

    async def test_invalid_environment_path_remains_explicit_in_http_diagnostic(self):
        self.write_tools(self.app_dir.parent / "ComfyUI" / "custom_nodes" / "Comfyui-Anima-Tools")
        explicit = self.root / "不存在的环境词库"
        with patch.dict(os.environ, {"ANIMA_TOOLS_DIR": str(explicit)}):
            payload = await self.read_config()
        discovery = payload["catalog"]["discovery"]
        self.assertFalse(payload["catalog"]["available"])
        self.assertEqual(discovery["source"], "ANIMA_TOOLS_DIR")
        self.assertEqual(discovery["status"], "invalid")
        self.assertEqual(discovery["searched_paths"], [])
        self.assertIn(str(explicit.resolve()), discovery["message"])
