import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from anima_webui.catalog import DATA_FILES, CatalogError, PromptCatalog, discover_tools, discover_tools_dir


class ToolsDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="目录 空格 ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = self.root / "studio"
        self.app.mkdir()
        self.nodes = self.root / "comfyui" / "custom_nodes"
        self.nodes.mkdir(parents=True)
        env = patch.dict(os.environ, {"ANIMA_TOOLS_DIR": "", "COMFYUI_ANIMA_TOOLS": ""})
        env.start()
        self.addCleanup(env.stop)

    def valid(self, name):
        path = self.nodes / name
        (path / "js").mkdir(parents=True)
        for file in DATA_FILES.values():
            (path / "js" / file).write_text("const data = [];", encoding="utf-8")
        return path

    def test_aliases_and_renamed_directory(self):
        for name in ("Comfyui-Anima-Tools", "Comfyui-Anima-Tools-main", "Comfyui-Anima-Tools-master", "我的 词库"):
            with self.subTest(name=name):
                path = self.valid(name)
                self.assertEqual(discover_tools_dir(self.app), path.resolve())
                path.rename(path.with_name(name + ".disabled"))

    def test_invalid_explicit_does_not_fall_back(self):
        self.valid("valid")
        with self.assertRaisesRegex(CatalogError, "character_data.js"):
            discover_tools_dir(self.app, self.nodes / "missing")
        catalog = PromptCatalog(self.app, self.nodes / "missing")
        self.assertFalse(catalog.available)
        self.assertEqual(catalog.discovery["status"], "invalid")
        self.assertTrue(catalog.load_warnings)

    def test_precedence_and_ambiguity(self):
        one, two = self.valid("one"), self.valid("two")
        self.assertEqual(discover_tools(self.app)["status"], "ambiguous")
        with patch.dict(os.environ, {"ANIMA_TOOLS_DIR": str(one), "COMFYUI_ANIMA_TOOLS": str(two)}):
            self.assertEqual(discover_tools_dir(self.app), one.resolve())
            self.assertEqual(discover_tools_dir(self.app, two), two.resolve())
        with patch.dict(os.environ, {"ANIMA_TOOLS_DIR": str(self.nodes / "missing")}):
            self.assertEqual(discover_tools(self.app)["status"], "invalid")

    def test_incomplete_is_not_missing_and_scan_is_shallow(self):
        self.valid("nested/valid")
        self.assertIsNone(discover_tools_dir(self.app))
        (self.nodes / "Comfyui-Anima-Tools-main").mkdir()
        self.assertEqual(discover_tools(self.app)["status"], "incomplete")

    def test_same_physical_directory_is_deduplicated(self):
        one = self.valid("one")
        alias = self.nodes / "alias"
        if os.name == "nt":
            import subprocess
            subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(one)], check=True, capture_output=True)
            self.addCleanup(lambda: alias.rmdir() if alias.exists() else None)
        else:
            alias.symlink_to(one, target_is_directory=True)
        self.assertEqual(discover_tools(self.app)["candidates"], [str(one.resolve())])

    def test_renamed_partial_install_is_not_treated_as_absent(self):
        partial = self.nodes / "改名 不完整" / "js"
        partial.mkdir(parents=True)
        (partial / "character_data.js").write_text("[]", encoding="utf-8")
        self.assertEqual(discover_tools(self.app)["status"], "incomplete")
