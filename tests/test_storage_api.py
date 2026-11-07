from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

from anima_webui.persistence import StorageReadOnlyError
from anima_webui.server import (
    CUSTOM_PROMPTS_KEY, HISTORY_KEY, LORA_LIBRARY_KEY, LORA_TRIGGER_OVERRIDES_KEY, MANAGER_KEY,
    PROMPT_RULES_KEY, STYLE_PRESETS_KEY,
    create_app,
)
from anima_webui.workflow import DEFAULT_SETTINGS


class FakeComfy:
    base_url = "http://127.0.0.1:8188"

    def __init__(self):
        self.status_calls = 0
        self.submissions = []
        self.classification_calls = []

    async def status(self):
        self.status_calls += 1
        return {"system": {}, "devices": []}

    async def resource_inventory(self):
        return {"models": [DEFAULT_SETTINGS["model_name"]], "samplers": ["euler"], "schedulers": ["simple"]}

    async def lora_inventory(self):
        return {"items": [{"filename": "test.safetensors", "trigger_words": ["source"]}], "count": 1}

    async def resource_classifications(self, kind, *, refresh=False):
        self.classification_calls.append((kind, refresh))
        name = "test.safetensors" if kind == "loras" else DEFAULT_SETTINGS["model_name"]
        item = {
            "name": name, "filename": name, "normalized_path": name,
            "basename": name, "folder": "", "model_profile": "anima",
            "base_model": "Anima", "classification_source": "lora-manager",
            "classification_status": "recognized", "classification_reason": "",
            "trigger_words": ["source"] if kind == "loras" else [],
            "trigger_metadata_available": True,
        }
        return {
            "items": [item], "count": 1, "source": "lora-manager",
            "manager_url": f"{self.base_url}/{'loras' if kind == 'loras' else 'checkpoints'}",
        }

    async def submit(self, payload):
        self.submissions.append(payload)
        raise AssertionError("Protected storage must reject generation before submit")

    async def close(self):
        pass


STORE_FILES = {
    "history": "history.sqlite3",
    "custom_prompts": "custom_prompts.json",
    "style_presets": "style_presets.json",
    "lora_triggers": "lora_trigger_overrides.json",
    "prompt_rules": "prompt_replacements.json",
    "favorites": "favorites.json",
    "lora_library": "lora_library.json",
}
STORE_KEYS = {
    "history": HISTORY_KEY,
    "custom_prompts": CUSTOM_PROMPTS_KEY,
    "style_presets": STYLE_PRESETS_KEY,
    "lora_triggers": LORA_TRIGGER_OVERRIDES_KEY,
    "prompt_rules": PROMPT_RULES_KEY,
    "lora_library": LORA_LIBRARY_KEY,
}


def state(store):
    store = getattr(store, "store", store)
    return copy.deepcopy({
        key: getattr(store, key)
        for key in ("items", "groups", "profile_overrides", "overrides", "custom_rules",
                    "disabled_builtins", "assignments", "initialized", "data")
        if hasattr(store, key)
    })


class StorageApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.original = b"{corrupt but valuable original contents"
        for filename in STORE_FILES.values():
            (self.data / filename).write_bytes(self.original)
        self.legacy_path = self.data / "resource_profiles.json"
        self.legacy_path.write_bytes(self.original)
        tools = self.root / "tools"
        (tools / "js").mkdir(parents=True)
        for filename in ("character_data.js", "clothing_data.js", "pose_data.js", "background_data.js"):
            (tools / "js" / filename).write_text("const data = [];", encoding="utf-8")
        self.comfy = FakeComfy()
        self.tools = tools
        original_open = Path.open

        def protect_legacy(path, *args, **kwargs):
            if path == self.legacy_path:
                raise AssertionError("The retired ownership file must never be opened")
            return original_open(path, *args, **kwargs)

        with patch("anima_webui.persistence.os.replace", side_effect=PermissionError("backup denied")), patch("anima_webui.persistence.shutil.copy2", side_effect=PermissionError("backup denied")), patch.object(Path, "open", new=protect_legacy):
            self.app = create_app(
                app_dir=Path(__file__).resolve().parents[1], data_dir=self.data,
                comfy=self.comfy, anima_tools_dir=tools,
            )
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    def assert_sources_unchanged(self):
        for filename in STORE_FILES.values():
            self.assertEqual((self.data / filename).read_bytes(), self.original)
        self.assertEqual(self.legacy_path.read_bytes(), self.original)

    async def test_config_reports_protection_with_real_paths_and_read_apis_remain_available(self):
        response = await self.client.get("/api/config")
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(set(payload["storage_status"]), set(STORE_FILES))
        for name, filename in STORE_FILES.items():
            status = payload["storage_status"][name]
            self.assertTrue(status["read_only"])
            self.assertEqual(status["path"], str(self.data / filename))
            self.assertIn(filename, status["reason"])
        self.assertFalse(payload["storage_status"]["history"]["available"])
        self.assertFalse(any("resource_profiles" in warning for warning in payload["warnings"]))
        for endpoint in (
            "/api/history", "/api/custom-prompts", "/api/style-presets", "/api/prompt-rules",
            "/api/resource-ownership?kind=models",
            "/api/lora-library", "/api/lora-collections", "/api/character-lora-bindings",
        ):
            response = await self.client.get(endpoint)
            self.assertEqual(response.status, 200, (endpoint, await response.text()))
        self.assert_sources_unchanged()

    async def test_invalid_source_remains_read_only_after_application_restart(self):
        for name, endpoint, body in (
            ("custom_prompts", "/api/custom-prompts", {"section": "pose", "title": "New", "prompt": "standing"}),
        ):
            with self.subTest(store=name):
                storage = self.root / f"restart-{name}"
                storage.mkdir()
                source_path = storage / STORE_FILES[name]
                source_path.write_bytes(self.original)
                for restart in range(2):
                    with self.subTest(restart=restart):
                        app = create_app(app_dir=Path(__file__).resolve().parents[1], data_dir=storage,
                                         comfy=FakeComfy(), anima_tools_dir=self.tools)
                        async with TestClient(TestServer(app)) as client:
                            status = (await (await client.get("/api/config")).json())["storage_status"][name]
                            self.assertTrue(status["read_only"])
                            response = await client.post(endpoint, json=body)
                            payload = await response.json()
                            self.assertEqual(response.status, 503, payload)
                            self.assertEqual(payload["code"], "storage_read_only")
                            self.assertEqual(payload["store"], name)
                            self.assertFalse(app[STORE_KEYS[name]].load_trusted)
                            self.assertEqual(source_path.read_bytes(), self.original)

    async def test_json_mutation_routes_return_503_before_state_or_file_changes(self):
        requests = {
            "custom_prompts": [
                ("post", "/api/custom-prompts", {"section": "pose", "title": "Test", "prompt": "standing"}),
                ("put", "/api/custom-prompts/missing", {}),
                ("delete", "/api/custom-prompts/missing", None),
                ("post", "/api/custom-groups/pose", {"name": "Test"}),
                ("put", "/api/custom-groups/pose/missing", {"name": "Test"}),
                ("delete", "/api/custom-groups/pose/missing", None),
                ("post", "/api/custom-prompts/import", {"rows": [], "section": "pose", "bundleName": "Test"}),
            ],
            "style_presets": [
                ("post", "/api/style-presets", {"name": "Test", "settings": {}}),
                ("put", "/api/style-presets/missing", {"name": "Test"}),
                ("delete", "/api/style-presets/missing", None),
            ],
            "lora_triggers": [
                ("put", "/api/loras/triggers", {"filename": "test.safetensors", "triggerWords": ["test"]}),
                ("delete", "/api/loras/triggers?filename=test.safetensors", None),
            ],
            "lora_library": [
                ("post", "/api/lora-collections", {"name": "Test"}),
                ("patch", "/api/lora-collections/missing", {"name": "Test"}),
                ("delete", "/api/lora-collections/missing", None),
                ("patch", "/api/lora-library/items/missing", {"favorite": True}),
                ("put", "/api/character-lora-bindings/missing", {"loras": []}),
                ("delete", "/api/character-lora-bindings/missing", None),
                ("post", "/api/lora-library/jobs", {"ids": ["missing"], "action": "favorite", "values": {"favorite": True}}),
            ],
            "prompt_rules": [
                ("post", "/api/prompt-rules", {"from": "old", "to": "new"}),
                ("put", "/api/prompt-rules/prompt_rule_missing", {"from": "old", "to": "new"}),
                ("delete", "/api/prompt-rules/prompt_rule_missing", None),
            ],
        }
        for name, operations in requests.items():
            before = state(self.app[STORE_KEYS[name]])
            for method, endpoint, body in operations:
                with self.subTest(store=name, method=method, endpoint=endpoint):
                    response = await self.client.request(method, endpoint, **({"json": body} if body is not None else {}))
                    payload = await response.json()
                    self.assertEqual(response.status, 503, payload)
                    self.assertEqual(payload["code"], "storage_read_only")
                    self.assertEqual(payload["store"], name)
                    self.assertEqual(payload["path"], str(self.data / STORE_FILES[name]))
                    self.assertEqual(state(self.app[STORE_KEYS[name]]), before)
                    self.assert_sources_unchanged()

    async def test_retired_ownership_write_returns_410_without_inventory_or_storage_access(self):
        original_open = Path.open

        def protect_legacy(path, *args, **kwargs):
            if path == self.legacy_path:
                raise AssertionError("The retired ownership file must never be opened")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", new=protect_legacy), patch.object(self.comfy, "resource_inventory", side_effect=AssertionError("Unexpected inventory read")), patch.object(self.comfy, "resource_classifications", side_effect=AssertionError("Unexpected plugin read")), patch.object(self.comfy, "lora_inventory", side_effect=AssertionError("Unexpected LoRA read")):
            for body in (
                {"json": {"kind": "models", "name": DEFAULT_SETTINGS["model_name"], "model_profile": "anima"}},
                {"data": "not valid JSON", "headers": {"Content-Type": "application/json"}},
            ):
                response = await self.client.put("/api/resource-ownership", **body)
                payload = await response.json()
                self.assertEqual(response.status, 410, payload)
                self.assertEqual(payload["code"], "classification_managed_by_lora_manager")
                self.assertIn("ComfyUI-Lora-Manager", payload["error"])
        self.assertEqual(self.comfy.status_calls, 0)
        self.assert_sources_unchanged()


    async def test_unavailable_history_blocks_new_generation_and_all_regeneration_modes_before_comfy(self):
        response = await self.client.get("/api/history")
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["items"], [])
        operations = [("post", "/api/batches", {"count": 1}), ("delete", "/api/history/1", None)]
        operations.extend(
            ("post", "/api/history/1/regenerate", {"mode": mode, "count": 1})
            for mode in ("replay", "prompt_variant", "content_redraw", "settings_reroll")
        )
        for method, endpoint, body in operations:
            response = await self.client.request(method, endpoint, **({"json": body} if body is not None else {}))
            payload = await response.json()
            self.assertEqual(response.status, 503, (endpoint, payload))
            self.assertEqual(payload["code"], "storage_read_only")
            self.assertEqual(payload["store"], "history")
        with self.assertRaises(StorageReadOnlyError):
            await self.app[MANAGER_KEY].start({"count": 1})
        self.assertEqual(self.comfy.status_calls, 0)
        self.assertEqual(self.comfy.submissions, [])
        self.assert_sources_unchanged()
