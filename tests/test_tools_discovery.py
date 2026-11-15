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

    def valid_at(self, path):
        (path / "js").mkdir(parents=True)
        for file in DATA_FILES.values():
            (path / "js" / file).write_text("const data = [];", encoding="utf-8")
        return path

    def valid(self, name):
        return self.valid_at(self.nodes / name)

    def directory_alias(self, alias, target):
        alias.parent.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            import subprocess
            subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(target)], check=True, capture_output=True)
            self.addCleanup(lambda: alias.rmdir() if alias.exists() else None)
        else:
            alias.symlink_to(target, target_is_directory=True)
            self.addCleanup(lambda: alias.unlink(missing_ok=True))

    @staticmethod
    def unique_paths(paths):
        result, seen = [], set()
        for path in paths:
            physical = path.resolve()
            key = os.path.normcase(str(physical))
            if key not in seen:
                result.append(str(physical))
                seen.add(key)
        return result

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
        self.directory_alias(alias, one)
        self.assertEqual(discover_tools(self.app)["candidates"], [str(one.resolve())])

    def test_renamed_partial_install_is_not_treated_as_absent(self):
        partial = self.nodes / "改名 不完整" / "js"
        partial.mkdir(parents=True)
        (partial / "character_data.js").write_text("[]", encoding="utf-8")
        self.assertEqual(discover_tools(self.app)["status"], "incomplete")

    def test_standard_layouts_inside_and_beside_project(self):
        case = 0
        for anchor in (self.app, self.root):
            for relative in (Path("custom_nodes"), Path("ComfyUI/custom_nodes"), Path("comfyui/custom_nodes")):
                with self.subTest(anchor=anchor.name, relative=str(relative)):
                    case += 1
                    plugin = self.valid_at(anchor / relative / f"Comfyui-Anima-Tools-{case}")
                    result = discover_tools(self.app)
                    self.assertEqual(result["status"], "available", result)
                    self.assertEqual(result["path"], str(plugin.resolve()))
                    plugin.rename(plugin.with_name(plugin.name + ".disabled"))

    def test_portable_layouts_inside_and_beside_project(self):
        names = ("ComfyUI_windows_portable", "comfyui_WINDOWS_PORTABLE_nvidia",
                 "ComfyUI_windows_portable_amd", "ComfyUI_windows_portable_intel")
        case = 0
        for anchor in (self.app, self.root):
            for wrapper in names:
                for inner in ("ComfyUI", "comfyui"):
                    with self.subTest(anchor=anchor.name, wrapper=wrapper, inner=inner):
                        case += 1
                        nodes = anchor / wrapper / inner / "custom_nodes"
                        plugin = self.valid_at(nodes / f"Comfyui-Anima-Tools-{case}")
                        result = discover_tools(self.app)
                        self.assertEqual(result["status"], "available", result)
                        self.assertEqual(result["path"], str(plugin.resolve()))
                        plugin.rename(plugin.with_name(plugin.name + ".disabled"))

    def test_hidden_and_disabled_portable_wrappers_are_not_scanned(self):
        names = (".ComfyUI_windows_portable", "ComfyUI_windows_portable.disabled",
                 "ComfyUI_windows_portable.anima-installing", "ComfyUI_windows_portable.extracting")
        for anchor in (self.app, self.root):
            for wrapper in names:
                self.valid_at(anchor / wrapper / "ComfyUI" / "custom_nodes" / "Comfyui-Anima-Tools")
        result = discover_tools(self.app)
        self.assertEqual(result["status"], "missing", result)
        self.assertFalse(any("portable" in path.casefold() for path in result["searched_paths"]))

    def test_hidden_and_disabled_plugins_are_not_candidates(self):
        nodes = self.app / "ComfyUI_windows_portable" / "ComfyUI" / "custom_nodes"
        for name in (".Comfyui-Anima-Tools", "Comfyui-Anima-Tools.DISABLED",
                     "Comfyui-Anima-Tools.anima-installing", "Comfyui-Anima-Tools.extracting"):
            self.valid_at(nodes / name)
        result = discover_tools(self.app)
        self.assertEqual(result["status"], "missing", result)
        self.assertEqual(result["candidates"], [])

    def test_portable_search_does_not_recurse_or_scan_unrelated_wrappers(self):
        paths = (
            self.app / "nested" / "ComfyUI_windows_portable" / "ComfyUI" / "custom_nodes",
            self.root / "other_app" / "ComfyUI_windows_portable" / "ComfyUI" / "custom_nodes",
            self.app / "ComfyUI_windows_portable" / "nested" / "ComfyUI" / "custom_nodes",
            self.app / "unrelated_wrapper" / "ComfyUI" / "custom_nodes",
            self.app / "ComfyUI_windows_portable" / "ComfyUI" / "custom_nodes" / "nested",
        )
        for nodes in paths:
            self.valid_at(nodes / "Comfyui-Anima-Tools")
        result = discover_tools(self.app)
        self.assertEqual(result["status"], "missing", result)
        self.assertEqual(result["candidates"], [])

    def test_distinct_standard_and_portable_installations_are_ambiguous(self):
        one = self.valid("standard")
        two = self.valid_at(self.app / "ComfyUI_windows_portable_amd" / "ComfyUI" / "custom_nodes" / "portable")
        result = discover_tools(self.app)
        self.assertEqual(result["status"], "ambiguous", result)
        self.assertEqual(result["candidates"], sorted((str(one.resolve()), str(two.resolve()))))

    def test_incomplete_portable_installation_is_reported(self):
        plugin = self.app / "ComfyUI_windows_portable" / "ComfyUI" / "custom_nodes" / "renamed-plugin"
        (plugin / "js").mkdir(parents=True)
        (plugin / "js" / "character_data.js").write_text("const data = [];", encoding="utf-8")
        result = discover_tools(self.app)
        self.assertEqual(result["status"], "incomplete", result)
        self.assertEqual(result["candidates"], [str(plugin.resolve())])
        self.assertIn("clothing_data.js", result["message"])

    def test_search_roots_are_physically_deduplicated_before_scanning(self):
        plugin = self.valid_at(self.app / "custom_nodes" / "real-plugin")
        self.directory_alias(self.app / "custom_nodes" / "plugin-alias", plugin)
        self.directory_alias(self.app / "ComfyUI" / "custom_nodes", self.app / "custom_nodes")
        self.directory_alias(self.root / "ComfyUI_windows_portable" / "ComfyUI" / "custom_nodes", self.app / "custom_nodes")
        original = Path.iterdir
        reads = []

        def tracked(path):
            reads.append(os.path.normcase(str(path.resolve())))
            return original(path)

        with patch.object(Path, "iterdir", tracked):
            result = discover_tools(self.app)
        self.assertEqual(result["status"], "available", result)
        self.assertEqual(result["candidates"], [str(plugin.resolve())])
        key = os.path.normcase(str((self.app / "custom_nodes").resolve()))
        self.assertEqual(reads.count(key), 1)
        self.assertEqual(sum(os.path.normcase(path) == key for path in result["searched_paths"]), 1)

    def test_searched_paths_are_ordered_unique_and_include_missing_fixed_roots(self):
        for anchor in (self.app, self.root):
            # Create in reverse order to ensure directory enumeration order
            # cannot determine the diagnostic or candidate search order.
            for wrapper in ("ComfyUI_windows_portable_nvidia", "ComfyUI_windows_portable_amd"):
                (anchor / wrapper).mkdir()
        expected = []
        for anchor in (self.app, self.root):
            expected.extend((anchor / "custom_nodes", anchor / "ComfyUI" / "custom_nodes",
                             anchor / "comfyui" / "custom_nodes"))
            for wrapper in ("ComfyUI_windows_portable_amd", "ComfyUI_windows_portable_nvidia"):
                expected.extend((anchor / wrapper / "ComfyUI" / "custom_nodes",
                                 anchor / wrapper / "comfyui" / "custom_nodes"))
        result = discover_tools(self.app)
        self.assertEqual(result["status"], "missing", result)
        self.assertEqual(result["searched_paths"], self.unique_paths(expected))
        self.assertIn(str((self.app / "custom_nodes").resolve()), result["searched_paths"])
        self.assertFalse((self.app / "custom_nodes").exists())
        self.assertNotIn("未安装", result["message"])

    def test_explicit_sources_do_not_report_automatic_searches(self):
        plugin = self.valid("valid")
        result = discover_tools(self.app, plugin)
        self.assertEqual((result["source"], result["status"], result["searched_paths"]), ("argument", "available", []))
        for key in ("ANIMA_TOOLS_DIR", "COMFYUI_ANIMA_TOOLS"):
            with self.subTest(key=key), patch.dict(os.environ, {key: str(plugin)}):
                result = discover_tools(self.app)
                self.assertEqual((result["source"], result["status"], result["searched_paths"]), (key, "available", []))

    def test_stale_environment_does_not_fall_back_to_other_env_or_auto(self):
        plugin = self.valid("valid")
        with patch.dict(os.environ, {"ANIMA_TOOLS_DIR": str(self.root / "missing"), "COMFYUI_ANIMA_TOOLS": str(plugin)}):
            result = discover_tools(self.app)
        self.assertEqual((result["source"], result["status"], result["searched_paths"]), ("ANIMA_TOOLS_DIR", "invalid", []))
        self.assertNotEqual(result["path"], str(plugin.resolve()))

    def test_explicit_js_directory_still_requires_plugin_root(self):
        plugin = self.valid("valid")
        result = discover_tools(self.app, plugin / "js")
        self.assertEqual((result["status"], result["searched_paths"]), ("invalid", []))
        self.assertIn("js/character_data.js", result["missing_files"])

    @unittest.skipIf(os.name == "nt", "Requires a case-sensitive filesystem")
    def test_distinct_uppercase_and_lowercase_comfyui_roots_are_ambiguous(self):
        upper = self.valid_at(self.root / "ComfyUI" / "custom_nodes" / "upper-plugin")
        if (self.root / "comfyui" / "custom_nodes" / "upper-plugin").exists():
            self.skipTest("The temporary filesystem is case-insensitive")
        lower = self.valid("lower-plugin")
        result = discover_tools(self.app)
        self.assertEqual(result["status"], "ambiguous", result)
        self.assertEqual(result["candidates"], sorted((str(upper.resolve()), str(lower.resolve()))))
