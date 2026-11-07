import copy
import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path
import sys

from aiohttp.test_utils import TestClient, TestServer


APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

from anima_webui.server import (  # noqa: E402
    CATALOG_KEY,
    COMFY_KEY,
    HISTORY_KEY,
    MANAGER_KEY,
    create_app,
)
from anima_webui.comfy import ComfyError  # noqa: E402
from anima_webui.image_location import ImageLocationError  # noqa: E402
from anima_webui.workflow import DEFAULT_SETTINGS  # noqa: E402


# 测试必须密闭:池数据来自 tempdir 里的假 Anima Tools 目录,
# 不依赖本机 ComfyUI 安装,也不吸入真实词库(回归 #P1-5)。
FAKE_TOOLS_DATASETS = {
    "character_data.js": [
        {
            "name": "alpha",
            "copyright": "series",
            "gender": "1girl",
            "hair": "blue",
            "eye": "red",
            "post_count": 5,
        },
    ],
    "clothing_data.js": [
        {
            "id": "coat",
            "name": "Coat",
            "name_zh": "外套",
            "tags": "red coat",
            "categories": ["日常/休闲 (Casual & Daily)"],
            "traits": ["red"],
        },
    ],
    "pose_data.js": [
        {
            "id": "stand",
            "name": "Standing",
            "name_zh": "站立",
            "tags": "standing",
            "categories": ["站立与动态 (Standing & Dynamic)"],
            "traits": ["standing"],
        },
        {
            "id": "sit",
            "name": "Sitting",
            "name_zh": "坐姿",
            "tags": "sitting",
            "categories": ["坐姿 (Sitting Poses)"],
            "traits": ["sitting"],
        },
    ],
    "background_data.js": [
        {
            "id": "room",
            "name": "Room",
            "name_zh": "房间",
            "tags": "indoors",
            "categories": ["都市与日常 (Urban & Daily)"],
            "traits": ["indoor"],
        },
    ],
}


def _write_fake_tools(root: Path) -> Path:
    js = root / "tools" / "js"
    js.mkdir(parents=True)
    for filename, values in FAKE_TOOLS_DATASETS.items():
        (js / filename).write_text(
            f"const data = {json.dumps(values, ensure_ascii=False)};\n",
            encoding="utf-8",
        )
    return root / "tools"


def effective_base_seed(graph):
    """Read the sampling seed through the execution-only cache gate."""
    sampler = (
        graph["701:1245"]["inputs"]
        if "701:1245" in graph
        else graph["691:1240"]["inputs"]
    )
    seed = sampler.get("noise_seed", sampler.get("seed"))
    if isinstance(seed, list):
        gate = graph[seed[0]]["inputs"]
        assert gate["expression"] == "a"
        seed = graph[gate["values.a"][0]]["inputs"]["value"]
    return seed


class FakeComfy:
    base_url = "http://127.0.0.1:8188"

    def __init__(self):
        self.preview_items = []
        self.submissions = []
        self.base_models = {
            "krea2_turbo_int8_convrot.safetensors": "Krea 2 Turbo",
            "Krea/krea2_turbo_int8_convrot.safetensors": "Krea 2 Turbo",
        }
        self.classification_calls = []
        self.favorites_data = {
            section: {
                "groups": [
                    {"id": "default", "name": "Default Favorites", "isSystem": True}
                ],
                "items": [],
            }
            for section in (
                "artist",
                "character",
                "lora",
                "clothing",
                "background",
                "pose",
                "expression",
            )
        }

    async def status(self):
        return {"system": {"comfyui_version": "test"}, "devices": [{"name": "GPU"}]}

    async def lora_inventory(self):
        if self.preview_items:
            return {
                "items": copy.deepcopy(self.preview_items),
                "count": len(self.preview_items),
            }
        items = [
            {
                "filename": item["filename"],
                "display_name": item["filename"].removesuffix(".safetensors"),
                "preview": "",
                "has_preview": False,
                "size": None,
            }
            for item in DEFAULT_SETTINGS["loras"]
        ]
        return {"items": items, "count": len(items)}

    async def lora_preview(self, filename):
        item = next(
            (value for value in self.preview_items if value["filename"] == filename),
            None,
        )
        if not item:
            raise KeyError(filename)
        return b"preview", "image/webp", "private, max-age=60"

    async def lora_filenames(self):
        return [item["filename"] for item in DEFAULT_SETTINGS["loras"]]

    async def resource_inventory(self):
        return {
            "models": [DEFAULT_SETTINGS["model_name"]],
            "upscale_models": [
                DEFAULT_SETTINGS["upscalers"]["pixel_hires"]["model_name"]
            ],
            "samplers": [
                DEFAULT_SETTINGS["sampler_name"],
                "euler",
                DEFAULT_SETTINGS["upscalers"]["face_refine"]["sampler"],
                DEFAULT_SETTINGS["upscalers"]["latent"]["sampler"]["sampler_name"],
            ],
            "schedulers": [
                DEFAULT_SETTINGS["scheduler"],
                "karras",
                DEFAULT_SETTINGS["upscalers"]["face_refine"]["scheduler"],
            ],
        }

    async def resource_classifications(self, kind, *, refresh=False):
        self.classification_calls.append((kind, refresh))
        if kind == "loras":
            items = copy.deepcopy((await self.lora_inventory())["items"])
        else:
            items = [
                {"filename": name}
                for name in (await self.resource_inventory())["models"]
            ]
        for item in items:
            name = item["filename"].replace("\\", "/")
            # Synthetic fixtures have explicit Anima metadata by default;
            # tests override metadata independently of the resource filename.
            base = self.base_models.get(name, item.get("base_model", "Anima"))
            profile = {"Anima": "anima"}.get(base)
            status = (
                "recognized"
                if profile
                else "unknown"
                if base == "Unknown"
                else "unsupported"
            )
            item.update(
                name=name,
                normalized_path=name,
                basename=name.rsplit("/", 1)[-1],
                folder=name.rsplit("/", 1)[0] if "/" in name else "",
                base_model=base,
                model_profile=profile,
                classification_source="lora-manager",
                classification_status=status,
                classification_reason="" if profile else f"插件分类为 {base}",
            )
        return {
            "items": items,
            "source": "lora-manager",
            "manager_url": self.base_url
            + ("/loras" if kind == "loras" else "/checkpoints"),
        }

    async def favorites(self):
        raise AssertionError("Plugin favorites must never be read")

    async def save_favorites(self, payload):
        raise AssertionError("Plugin favorites must never be written")

    async def submit(self, payload):
        self.submissions.append(copy.deepcopy(payload))
        return "prompt-id"

    async def wait_for_history(
        self, prompt_id, should_abort=None, missing_timeout=30.0
    ):
        return {
            "outputs": {
                "12": {
                    "images": [
                        {"filename": "test.png", "subfolder": "", "type": "output"}
                    ]
                }
            },
            "prompt": [
                0,
                prompt_id,
                {},
                {"extra_pnginfo": {"anima_prompt": {"42": {"positive": "generated"}}}},
            ],
        }

    async def image_bytes(self, image):
        return b"png", "image/png"

    async def close(self):
        return None


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self._saved_env = {
            name: os.environ.pop(name, None)
            for name in ("ANIMA_TOOLS_DIR", "COMFYUI_ANIMA_TOOLS")
        }
        self.comfy = FakeComfy()
        app = create_app(
            app_dir=APP_DIR,
            data_dir=Path(self.temp.name) / "data",
            comfy=self.comfy,
            history_path=Path(self.temp.name) / "history.sqlite3",
            custom_prompts_path=Path(self.temp.name) / "custom_prompts.json",
            style_presets_path=Path(self.temp.name) / "style_presets.json",
            lora_trigger_overrides_path=Path(self.temp.name)
            / "lora_trigger_overrides.json",
            prompt_replacements_path=Path(self.temp.name) / "prompt_replacements.json",
            anima_tools_dir=_write_fake_tools(Path(self.temp.name)),
        )
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()
        for name, value in self._saved_env.items():
            if value is not None:
                os.environ[name] = value

    async def test_index_and_status(self):
        response = await self.client.get("/")
        self.assertEqual(response.status, 200)
        index_html = await response.text()
        self.assertIn("Anima", index_html)
        self.assertNotIn('value="krea2"', index_html)
        self.assertIn('id="base_mode"', index_html)
        self.assertIn('id="latent_upscale_enabled"', index_html)
        self.assertIn('id="seedvr2_enabled"', index_html)
        self.assertIn('id="nvidia_upscale_enabled"', index_html)
        self.assertIn('id="face_refine_enabled"', index_html)
        for removed_id in (
            "hires_enabled",
            "hires_percent",
            "hires_model_name",
            "detailer_hand",
            "detailer_nsfw",
            "detailer_face",
            "detailer_eyes",
            "cfg_zero_star",
            "second_sampler_enabled",
            "secondSamplerDrawer",
        ):
            self.assertNotIn(f'id="{removed_id}"', index_html)
        self.assertEqual(
            response.headers["Cache-Control"], "no-cache, max-age=0, must-revalidate"
        )
        static_response = await self.client.get("/static/app.js")
        app_js = await static_response.text()
        self.assertNotIn('"hires_percent"', app_js)
        self.assertNotIn("percent: Number(ui.hires_percent.value)", app_js)
        self.assertIn("function reconcileLoraTriggers", app_js)
        self.assertIn("lora_managed_triggers", app_js)
        self.assertIn("lora-catalog-preview", index_html + app_js)
        self.assertIn('id="loraTriggerDialog"', index_html)
        self.assertIn('id="promptRuleDialog"', index_html)
        self.assertIn('id="sampler_name"', index_html)
        self.assertIn('id="scheduler"', index_html)
        self.assertIn("sampler_name: ui.sampler_name.value", app_js)
        self.assertIn("scheduler: ui.scheduler.value", app_js)
        self.assertIn("normalizePromptFields", app_js)
        self.assertIn('id="deleteGroupAll"', index_html)
        self.assertIn('value="exclusive"', index_html)
        self.assertIn("deleteMode=${deleteMode}", app_js)
        self.assertIn('id="favoriteSelection"', index_html)
        self.assertIn('id="favoriteSelectionDialog"', index_html)
        self.assertIn(
            "/api/favorites/${section}/selection?model_profile=${profile}", app_js
        )
        self.assertEqual(
            static_response.headers["Cache-Control"],
            "no-cache, max-age=0, must-revalidate",
        )
        status = await (await self.client.get("/api/status")).json()
        self.assertTrue(status["online"])
        self.assertEqual(status["device"], "GPU")

    async def test_lora_inventory(self):
        response = await self.client.get("/api/loras")
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["count"], 0)
        self.assertEqual(
            [item["filename"] for item in payload["items"]],
            [item["filename"] for item in DEFAULT_SETTINGS["loras"]],
        )

    async def test_lora_inventory_follows_plugin_classification_and_refresh(self):
        self.comfy.preview_items = [
            {"filename": "style/one.safetensors", "trigger_words": ["one"]},
            {"filename": "style/two.safetensors", "trigger_words": ["two"]},
        ]
        anima = await (await self.client.get("/api/loras?model_profile=anima")).json()
        self.assertEqual(anima["count"], 2)
        rejected = await self.client.get("/api/loras?model_profile=krea2")
        self.assertEqual(rejected.status, 400)
        self.comfy.base_models["style/one.safetensors"] = "Krea 2 Turbo"
        anima = await (
            await self.client.get("/api/loras?model_profile=anima&refresh=1")
        ).json()
        self.assertIn(("loras", True), self.comfy.classification_calls)
        self.assertEqual(
            [item["filename"] for item in anima["items"]], ["style/two.safetensors"]
        )
        ownership = await (
            await self.client.get("/api/resource-ownership?kind=loras")
        ).json()
        self.assertIsNone(ownership["assignments"]["style/one.safetensors"])
        self.assertEqual(ownership["assignments"]["style/two.safetensors"], "anima")
        self.assertEqual(ownership["source"], "lora-manager")
        self.assertEqual(ownership["manager_url"], "http://127.0.0.1:8188/loras")

    async def test_resource_models_filter_and_profile_availability_use_full_inventory(
        self,
    ):
        async def inventory():
            return {
                "models": [
                    "anima-base-v1.0.safetensors",
                    "krea2_turbo_int8_convrot.safetensors",
                    "custom/older.safetensors",
                ],
                "samplers": ["euler"],
                "schedulers": ["normal"],
            }

        self.comfy.resource_inventory = inventory
        anima = await (
            await self.client.get("/api/resources?model_profile=anima")
        ).json()
        self.assertEqual(
            anima["models"], ["anima-base-v1.0.safetensors", "custom/older.safetensors"]
        )
        self.assertEqual(set(anima["model_profiles"]), {"anima"})
        self.assertTrue(anima["model_profiles"]["anima"]["available"]["model"])
        rejected = await self.client.get("/api/resources?model_profile=krea2")
        self.assertEqual(rejected.status, 400)
        self.assertIn("model_profile", (await rejected.json())["error"])

    async def test_resource_profile_models_resolve_owned_subfolders_and_filter_availability(
        self,
    ):
        anima_path = "Anima/anima-base-v1.0.safetensors"
        krea_path = "Krea/krea2_turbo_int8_convrot.safetensors"

        async def inventory():
            return {
                "models": [anima_path, krea_path],
                "samplers": ["euler"],
                "schedulers": ["normal"],
            }

        self.comfy.resource_inventory = inventory
        resources = await (
            await self.client.get("/api/resources?model_profile=anima")
        ).json()
        self.assertEqual(resources["model_profiles"]["anima"]["model_name"], anima_path)
        self.assertTrue(resources["model_profiles"]["anima"]["available"]["model"])
        self.assertEqual(set(resources["model_profiles"]), {"anima"})

        self.comfy.base_models[anima_path] = "Krea 2 Turbo"
        resources = await (
            await self.client.get("/api/resources?model_profile=anima&refresh=1")
        ).json()
        self.assertIn(("models", True), self.comfy.classification_calls)
        self.assertFalse(resources["model_profiles"]["anima"]["available"]["model"])
        self.assertEqual(set(resources["model_profiles"]), {"anima"})

    async def test_batch_model_basename_resolves_to_unique_owned_subfolder_path(self):
        actual_path = "Anima/anima-base-v1.0.safetensors"

        async def inventory():
            return {
                "models": [actual_path],
                "upscale_models": [
                    DEFAULT_SETTINGS["upscalers"]["pixel_hires"]["model_name"]
                ],
                "samplers": [DEFAULT_SETTINGS["sampler_name"], "euler"],
                "schedulers": [DEFAULT_SETTINGS["scheduler"], "karras"],
            }

        self.comfy.resource_inventory = inventory
        response = await self.client.post("/api/batches", json=DEFAULT_SETTINGS)
        self.assertEqual(response.status, 201, await response.text())
        state = await response.json()
        self.assertEqual(state["settings"]["model_name"], actual_path)
        await self.client.server.app[MANAGER_KEY].wait()

    async def test_exact_unknown_default_model_cannot_redirect_to_recognized_subfolder(
        self,
    ):
        exact = "anima-base-v1.0.safetensors"
        nested = "Anima/anima-base-v1.0.safetensors"

        async def inventory():
            return {
                "models": [exact, nested],
                "samplers": ["euler"],
                "schedulers": ["normal"],
            }

        self.comfy.resource_inventory = inventory
        self.comfy.base_models[exact] = "Unknown"
        payload = await (await self.client.get("/api/resources")).json()
        self.assertEqual(payload["models"], [nested])
        self.assertFalse(payload["model_profiles"]["anima"]["available"]["model"])
        self.assertEqual(payload["model_profiles"]["anima"]["model_name"], exact)
        response = await self.client.post(
            "/api/batches", json={**DEFAULT_SETTINGS, "model_name": exact}
        )
        self.assertEqual(response.status, 400)
        self.assertIn("Unknown", (await response.json())["error"])
        self.assertEqual(self.comfy.submissions, [])

    async def test_batch_rejects_ambiguous_model_basename_but_accepts_exact_path(self):
        first_path = "folder-a/anima-base-v1.0.safetensors"
        second_path = "folder-b/anima-base-v1.0.safetensors"

        async def inventory():
            return {
                "models": [first_path, second_path],
                "upscale_models": [
                    DEFAULT_SETTINGS["upscalers"]["pixel_hires"]["model_name"]
                ],
                "samplers": [DEFAULT_SETTINGS["sampler_name"], "euler"],
                "schedulers": [DEFAULT_SETTINGS["scheduler"], "karras"],
            }

        self.comfy.resource_inventory = inventory
        ambiguous = await self.client.post("/api/batches", json=DEFAULT_SETTINGS)
        self.assertEqual(ambiguous.status, 400)
        self.assertIn("basename 匹配不唯一", (await ambiguous.json())["error"])

        exact = await self.client.post(
            "/api/batches", json={**DEFAULT_SETTINGS, "model_name": second_path}
        )
        self.assertEqual(exact.status, 201, await exact.text())
        state = await exact.json()
        self.assertEqual(state["settings"]["model_name"], second_path)
        await self.client.server.app[MANAGER_KEY].wait()

    async def test_batch_rejects_model_owned_by_other_profile_even_when_basename_is_unique(
        self,
    ):
        krea_path = "Krea/krea2_turbo_int8_convrot.safetensors"

        async def inventory():
            return {
                "models": [krea_path],
                "upscale_models": [
                    DEFAULT_SETTINGS["upscalers"]["pixel_hires"]["model_name"]
                ],
                "samplers": [DEFAULT_SETTINGS["sampler_name"], "euler"],
                "schedulers": [DEFAULT_SETTINGS["scheduler"], "karras"],
            }

        self.comfy.resource_inventory = inventory
        response = await self.client.post(
            "/api/batches",
            json={
                **DEFAULT_SETTINGS,
                "model_profile": "anima",
                "model_name": "krea2_turbo_int8_convrot.safetensors",
            },
        )
        self.assertEqual(response.status, 400)
        error = (await response.json())["error"]
        self.assertIn("主模型不适用于 anima 档案", error)
        self.assertIn("Krea 2 Turbo", error)

        stale_path = await self.client.post(
            "/api/batches",
            json={
                **DEFAULT_SETTINGS,
                "model_name": "old-folder/krea2_turbo_int8_convrot.safetensors",
            },
        )
        self.assertEqual(stale_path.status, 400)
        self.assertIn("不存在于当前 ComfyUI 清单", (await stale_path.json())["error"])

    async def test_lora_inventory_uses_same_origin_preview_proxy(self):
        self.comfy.preview_items = [
            {
                "filename": "风格\\one.safetensors",
                "normalized_path": "风格/one.safetensors",
                "folder": "风格",
                "basename": "one.safetensors",
                "display_name": "One",
                "preview": "/api/lm/previews?path=secret",
                "has_preview": True,
                "trigger_words": ["@one"],
                "trigger_metadata_available": True,
                "metadata_source": "lora-manager",
                "size": 123,
            }
        ]
        payload = await (await self.client.get("/api/loras")).json()
        item = payload["items"][0]
        self.assertEqual(item["trigger_words"], ["@one"])
        self.assertEqual(item["source_trigger_words"], ["@one"])
        self.assertFalse(item["trigger_override"])
        self.assertNotIn("secret", item["preview"])
        self.assertTrue(item["preview"].startswith("/api/loras/preview?filename="))

        preview = await self.client.get(item["preview"])
        self.assertEqual(preview.status, 200)
        self.assertEqual(await preview.read(), b"preview")
        self.assertEqual(preview.headers["Content-Type"], "image/webp")
        self.assertEqual(preview.headers["Cache-Control"], "private, max-age=60")

        invalid = await self.client.get(
            "/api/loras/preview?filename=../one.safetensors"
        )
        self.assertEqual(invalid.status, 404)

    async def test_plugin_metadata_controls_selection_without_filename_guessing(self):
        self.comfy.preview_items = [
            {"filename": "anima-style.safetensors", "base_model": "Unknown"},
            {"filename": "krea2-style.safetensors", "base_model": "Anima"},
            {"filename": "other.safetensors", "base_model": "Qwen"},
        ]
        payload = await (await self.client.get("/api/loras")).json()
        self.assertEqual(
            [item["filename"] for item in payload["items"]], ["krea2-style.safetensors"]
        )
        self.assertEqual(len(payload["classification_items"]), 3)
        for filename, base in (
            ("anima-style.safetensors", "Unknown"),
            ("other.safetensors", "Qwen"),
        ):
            response = await self.client.post(
                "/api/batches",
                json={
                    **DEFAULT_SETTINGS,
                    "loras": [{"filename": filename, "strength": 1, "enabled": True}],
                },
            )
            self.assertEqual(response.status, 400)
            self.assertIn(base, (await response.json())["error"])
        self.assertEqual(self.comfy.submissions, [])

    async def test_unknown_model_metadata_blocks_batch_and_retires_manual_assignment(
        self,
    ):
        self.comfy.base_models[DEFAULT_SETTINGS["model_name"]] = "Unknown"
        payload = await (await self.client.get("/api/resources?refresh=1")).json()
        self.assertEqual(payload["models"], [])
        self.assertEqual(payload["model_items"][0]["base_model"], "Unknown")
        response = await self.client.post("/api/batches", json=DEFAULT_SETTINGS)
        self.assertEqual(response.status, 400)
        self.assertIn("Unknown", (await response.json())["error"])
        response = await self.client.put(
            "/api/resource-ownership",
            json={
                "kind": "models",
                "name": DEFAULT_SETTINGS["model_name"],
                "model_profile": "anima",
            },
        )
        self.assertEqual(response.status, 410)
        self.assertIn("Lora-Manager", (await response.json())["error"])
        self.assertFalse(Path(self.temp.name, "resource_profiles.json").exists())
        self.assertEqual(self.comfy.submissions, [])

    async def test_plugin_failure_blocks_lists_and_submission_without_stale_fallback(
        self,
    ):
        self.assertEqual((await self.client.get("/api/resources")).status, 200)

        async def offline(kind, *, refresh=False):
            raise ComfyError("LoRA Manager 清单读取失败")

        self.comfy.resource_classifications = offline
        for endpoint in (
            "/api/resources",
            "/api/loras",
            "/api/resource-ownership?kind=models",
        ):
            response = await self.client.get(endpoint)
            self.assertEqual(response.status, 503)
            self.assertIn("LoRA Manager", (await response.json())["error"])
        response = await self.client.post("/api/batches", json=DEFAULT_SETTINGS)
        self.assertEqual(response.status, 503)
        self.assertEqual(self.comfy.submissions, [])

    async def test_changed_plugin_classification_blocks_all_history_modes(self):
        history = self.client.server.app[MANAGER_KEY].history
        await history.create_batch("classified-history", 1, DEFAULT_SETTINGS)
        image = await history.add_image(
            batch_id="classified-history",
            sequence=1,
            prompt_id="old-prompt",
            image={"filename": "old.png", "type": "output"},
            positive_prompt="saved prompt",
            negative_prompt="saved negative",
            sample_seed=123,
            prompt_seed=456,
            settings=DEFAULT_SETTINGS,
            resolved_selection={},
            resolved_prompt="saved prompt",
        )
        self.comfy.base_models[DEFAULT_SETTINGS["model_name"]] = "Qwen"
        endpoint = f"/api/history/{image['id']}"
        response = await self.client.get(endpoint + "/regeneration-options")
        self.assertEqual(response.status, 200)
        options = await response.json()
        self.assertTrue(
            all(not value["available"] for value in options["modes"].values())
        )
        self.assertIn("Qwen", str(options["resourceIssues"]))
        for mode in ("replay", "prompt_variant", "content_redraw", "settings_reroll"):
            response = await self.client.post(
                endpoint + "/regenerate",
                json={
                    "mode": mode,
                    **({"sections": ["pose"]} if mode == "content_redraw" else {}),
                },
            )
            self.assertEqual(response.status, 400)
            self.assertIn("Qwen", (await response.json())["error"])
        stored = await history.get_image(image["id"])
        self.assertEqual(stored["sample_seed"], 123)
        self.assertEqual(stored["prompt_seed"], 456)
        self.assertEqual(self.comfy.submissions, [])

    async def test_lora_trigger_override_can_be_edited_cleared_and_reset(self):
        self.comfy.preview_items = [
            {
                "filename": "风格/one.safetensors",
                "display_name": "One",
                "preview": "",
                "trigger_words": ["@One", "Second"],
                "trigger_metadata_available": True,
            }
        ]
        updated = await self.client.put(
            "/api/loras/triggers",
            json={
                "filename": "风格/one.safetensors",
                "triggerWords": [" @Exact_One ", "@exact_one"],
            },
        )
        self.assertEqual(updated.status, 200)
        payload = await updated.json()
        self.assertEqual(payload["source_trigger_words"], ["@One", "Second"])
        self.assertEqual(payload["trigger_words"], ["@Exact_One"])
        self.assertTrue(payload["trigger_override"])

        cleared = await self.client.put(
            "/api/loras/triggers",
            json={"filename": "风格/one.safetensors", "triggerWords": []},
        )
        self.assertEqual((await cleared.json())["trigger_words"], [])
        listing = await (await self.client.get("/api/loras")).json()
        self.assertEqual(listing["items"][0]["trigger_words"], [])
        self.assertTrue(listing["items"][0]["trigger_override"])
        self.assertTrue(Path(self.temp.name, "lora_trigger_overrides.json").is_file())

        reset = await self.client.delete(
            "/api/loras/triggers?filename=%E9%A3%8E%E6%A0%BC%2Fone.safetensors"
        )
        self.assertEqual(reset.status, 200)
        reset_payload = await reset.json()
        self.assertEqual(reset_payload["trigger_words"], ["@One", "Second"])
        self.assertFalse(reset_payload["trigger_override"])

    async def test_prompt_normalization_and_rule_crud(self):
        normalized = await self.client.post(
            "/api/prompts/normalize",
            json={
                "fields": {
                    "quality_prompt": "2025，SCORE 9, Blue_Hair",
                    "extra_prompt": "@Niji9il, Blue_Hair",
                },
                "managedTriggers": ["@Niji9il"],
                "model_profile": "anima",
            },
        )
        self.assertEqual(normalized.status, 200)
        payload = await normalized.json()
        self.assertEqual(
            payload["fields"]["quality_prompt"], "year 2025, score_9, blue hair"
        )
        self.assertEqual(payload["fields"]["extra_prompt"], "@Niji9il, blue hair")
        self.assertEqual(payload["model_profile"], "anima")

        created = await self.client.post(
            "/api/prompt-rules",
            json={
                "from": "wrong_tag",
                "to": "Right_Tag",
                "scopes": ["positive"],
                "enabled": True,
            },
        )
        self.assertEqual(created.status, 201)
        item = await created.json()
        custom = await self.client.post(
            "/api/prompts/normalize",
            json={"fields": {"extra_prompt": "WRONG_TAG"}},
        )
        self.assertEqual((await custom.json())["fields"]["extra_prompt"], "right tag")

        disabled = await self.client.put(
            "/api/prompt-rules/lowercase-tags", json={"enabled": False}
        )
        self.assertEqual(disabled.status, 200)
        listing = await (await self.client.get("/api/prompt-rules")).json()
        self.assertFalse(
            next(rule for rule in listing["items"] if rule["id"] == "lowercase-tags")[
                "enabled"
            ]
        )
        deleted = await self.client.delete(f"/api/prompt-rules/{item['id']}")
        self.assertEqual(deleted.status, 200)
        self.assertTrue(Path(self.temp.name, "prompt_replacements.json").is_file())

    async def test_config_has_empty_default_loras(self):
        payload = await (await self.client.get("/api/config")).json()
        defaults = payload["defaults"]
        self.assertEqual(defaults["loras"], [])
        self.assertEqual(defaults["model_profile"], "anima")
        self.assertIn(defaults["base_mode"], {"normal", "dual_sampler"})
        self.assertIn("upscalers", defaults)
        for removed in ("hires", "detailers", "cfg_zero_star", "second_sampler"):
            self.assertNotIn(removed, defaults)

    async def test_resources_and_style_preset_crud(self):
        resources = await (await self.client.get("/api/resources")).json()
        self.assertEqual(resources["models"], [DEFAULT_SETTINGS["model_name"]])
        self.assertEqual(
            resources["samplers"],
            [
                DEFAULT_SETTINGS["sampler_name"],
                "euler",
                DEFAULT_SETTINGS["upscalers"]["face_refine"]["sampler"],
                DEFAULT_SETTINGS["upscalers"]["latent"]["sampler"]["sampler_name"],
            ],
        )
        self.assertEqual(
            resources["schedulers"],
            [
                DEFAULT_SETTINGS["scheduler"],
                "karras",
                DEFAULT_SETTINGS["upscalers"]["face_refine"]["scheduler"],
            ],
        )
        snapshot = {
            key: copy.deepcopy(DEFAULT_SETTINGS[key])
            for key in (
                "model_name",
                "loras",
                "lora_managed_triggers",
                "upscalers",
                "manual_artist",
                "quality_prompt",
                "extra_prompt",
                "negative_prompt",
                "width",
                "height",
                "steps",
                "cfg",
                "sampler_name",
                "scheduler",
            )
        }
        snapshot["quality_prompt"] = "2025, Blue_Hair"
        created = await self.client.post(
            "/api/style-presets",
            json={"name": "Soft", "favorite": False, "settings": snapshot},
        )
        self.assertEqual(created.status, 201)
        item = await created.json()
        self.assertEqual(item["settings"]["quality_prompt"], "year 2025, blue hair")
        updated = await self.client.put(
            f"/api/style-presets/{item['id']}",
            json={"favorite": True, "name": "Soft Light"},
        )
        self.assertEqual(updated.status, 200)
        self.assertTrue((await updated.json())["favorite"])
        listing = await (await self.client.get("/api/style-presets")).json()
        self.assertEqual(listing["items"][0]["name"], "Soft Light")
        self.assertTrue(Path(self.temp.name, "style_presets.json").is_file())
        deleted = await self.client.delete(f"/api/style-presets/{item['id']}")
        self.assertEqual(deleted.status, 200)
        self.assertEqual(
            (await (await self.client.get("/api/style-presets")).json())["count"], 0
        )

    async def test_favorite_crud_preserves_other_sections(self):
        pool = await (await self.client.get("/api/pools/pose?page=1&limit=1")).json()
        item_id = pool["items"][0]["id"]
        created = await self.client.post(
            "/api/favorites/pose/groups", json={"name": "Test Group"}
        )
        self.assertEqual(created.status, 201)
        group_id = (await created.json())["group"]["id"]
        saved = await self.client.put(
            "/api/favorites/pose/item",
            json={
                "id": item_id,
                "favorite": True,
                "groupIds": [group_id],
                "nickname": "memo",
            },
        )
        self.assertEqual(saved.status, 200)
        payload = await saved.json()
        self.assertEqual(payload["items"][0]["nickname"], "memo")
        self.assertIn(group_id, payload["items"][0]["groupIds"])
        self.assertEqual(
            self.client.server.app[COMFY_KEY].favorites_data["character"]["items"], []
        )

        deleted = await self.client.delete(f"/api/favorites/pose/groups/{group_id}")
        self.assertEqual(deleted.status, 200)
        payload = await deleted.json()
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["groupIds"], ["default"])

    async def test_batch_favorite_selection_api_appends_groups_and_preserves_nickname(
        self,
    ):
        pose = await (await self.client.get("/api/pools/pose?page=1&limit=2")).json()
        first_id, second_id = [item["id"] for item in pose["items"]]
        first_group = await self.client.post(
            "/api/favorites/pose/groups", json={"name": "First"}
        )
        first_group_id = (await first_group.json())["group"]["id"]
        second_group = await self.client.post(
            "/api/favorites/pose/groups", json={"name": "Second"}
        )
        second_group_id = (await second_group.json())["group"]["id"]
        saved = await self.client.put(
            "/api/favorites/pose/item",
            json={
                "id": first_id,
                "favorite": True,
                "groupIds": [first_group_id],
                "nickname": "memo",
            },
        )
        self.assertEqual(saved.status, 200)

        response = await self.client.post(
            "/api/favorites/pose/selection",
            json={
                "selection": {
                    "mode": "include",
                    "ids": [first_id, second_id, first_id],
                    "excluded_ids": [],
                },
                "groupIds": [second_group_id],
            },
        )
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["selectedCount"], 2)
        self.assertEqual(payload["createdCount"], 1)
        self.assertEqual(payload["updatedCount"], 1)
        by_id = {item["id"]: item for item in payload["items"]}
        first_key = first_id.split(":", 1)[-1]
        second_key = second_id.split(":", 1)[-1]
        self.assertEqual(
            by_id[first_key]["groupIds"], [first_group_id, second_group_id]
        )
        self.assertEqual(by_id[first_key]["nickname"], "memo")
        self.assertEqual(by_id[second_key]["groupIds"], [second_group_id])

        all_response = await self.client.post(
            "/api/favorites/pose/selection",
            json={
                "selection": {"mode": "all", "ids": [], "excluded_ids": [second_id]},
                "groupIds": ["default"],
            },
        )
        self.assertEqual(all_response.status, 200)
        all_payload = await all_response.json()
        self.assertEqual(all_payload["selectedCount"], 1)
        self.assertEqual(all_payload["updatedCount"], 1)
        self.assertIn(
            "default",
            next(item for item in all_payload["items"] if item["id"] == first_key)[
                "groupIds"
            ],
        )

        invalid = await self.client.post(
            "/api/favorites/pose/selection",
            json={
                "selection": {"mode": "include", "ids": ["missing"]},
                "groupIds": ["default"],
            },
        )
        self.assertEqual(invalid.status, 400)

    async def test_favorite_child_import_parent_filter_and_safe_delete_api(self):
        source = await self.client.post(
            "/api/custom-groups/pose", json={"name": "Snapshot"}
        )
        source_id = (await source.json())["group"]["id"]
        custom = await self.client.post(
            "/api/custom-prompts",
            json={
                "section": "pose",
                "title": "Snapshot Pose",
                "prompt": "snapshot pose",
                "groupIds": [source_id],
            },
        )
        custom_id = (await custom.json())["id"]
        parent = await self.client.post(
            "/api/favorites/pose/groups", json={"name": "Parent"}
        )
        parent_id = (await parent.json())["group"]["id"]

        imported = await self.client.post(
            f"/api/favorites/pose/groups/{parent_id}/children/import",
            json={"customGroupId": source_id},
        )
        self.assertEqual(imported.status, 201)
        imported_payload = await imported.json()
        child = imported_payload["group"]
        self.assertEqual(child["parentId"], parent_id)
        self.assertEqual(child["sourceCustomGroupId"], source_id)
        child_with_stats = next(
            group for group in imported_payload["groups"] if group["id"] == child["id"]
        )
        self.assertEqual(child_with_stats["directCount"], 1)

        duplicate = await self.client.post(
            f"/api/favorites/pose/groups/{parent_id}/children/import",
            json={"customGroupId": source_id},
        )
        self.assertEqual(duplicate.status, 400)
        filtered = await (
            await self.client.get(
                f"/api/pools/pose?collection={parent_id}&q=Snapshot%20Pose"
            )
        ).json()
        self.assertEqual([item["id"] for item in filtered["items"]], [custom_id])

        invalid_bool = await self.client.delete(
            f"/api/favorites/pose/groups/{child['id']}?deleteItems=maybe"
        )
        self.assertEqual(invalid_bool.status, 400)
        deleted = await self.client.delete(f"/api/favorites/pose/groups/{parent_id}")
        self.assertEqual(deleted.status, 200)
        payload = await deleted.json()
        self.assertEqual(payload["deletedGroupCount"], 2)
        favorite = next(item for item in payload["items"] if item["id"] == custom_id)
        self.assertEqual(favorite["groupIds"], ["default"])

    async def test_custom_group_delete_modes_and_legacy_query_compatibility(self):
        async def create_group(name):
            response = await self.client.post(
                "/api/custom-groups/pose", json={"name": name}
            )
            return (await response.json())["group"]

        first = await create_group("First")
        second = await create_group("Second")
        exclusive = await (
            await self.client.post(
                "/api/custom-prompts",
                json={
                    "section": "pose",
                    "title": "Exclusive",
                    "prompt": "exclusive",
                    "groupIds": [first["id"]],
                },
            )
        ).json()
        shared = await (
            await self.client.post(
                "/api/custom-prompts",
                json={
                    "section": "pose",
                    "title": "Shared",
                    "prompt": "shared",
                    "groupIds": [first["id"], second["id"]],
                },
            )
        ).json()

        deleted = await self.client.delete(
            f"/api/custom-groups/pose/{first['id']}?deleteMode=all&deleteItems=maybe"
        )
        self.assertEqual(deleted.status, 200)
        payload = await deleted.json()
        self.assertEqual(payload["deleteMode"], "all")
        self.assertEqual(
            set(payload["deletedItemIds"]), {exclusive["id"], shared["id"]}
        )
        self.assertEqual(
            (await (await self.client.get("/api/custom-prompts?section=pose")).json())[
                "items"
            ],
            [],
        )

        legacy = await create_group("Legacy")
        legacy_item = await (
            await self.client.post(
                "/api/custom-prompts",
                json={
                    "section": "pose",
                    "title": "Legacy",
                    "prompt": "legacy",
                    "groupIds": [legacy["id"]],
                },
            )
        ).json()
        legacy_deleted = await self.client.delete(
            f"/api/custom-groups/pose/{legacy['id']}?deleteItems=true"
        )
        self.assertEqual(legacy_deleted.status, 200)
        legacy_payload = await legacy_deleted.json()
        self.assertEqual(legacy_payload["deleteMode"], "exclusive")
        self.assertEqual(legacy_payload["deletedItemIds"], [legacy_item["id"]])

        invalid = await create_group("Invalid")
        invalid_response = await self.client.delete(
            f"/api/custom-groups/pose/{invalid['id']}?deleteMode=invalid"
        )
        self.assertEqual(invalid_response.status, 400)
        self.assertIn("deleteMode", (await invalid_response.json())["error"])

    async def test_artist_favorites_normalize_prefix_and_preserve_other_sections(self):
        response = await self.client.put(
            "/api/favorites/artist/item",
            json={"name": "@rella", "favorite": True},
        )
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertEqual(payload["items"][0]["name"], "rella")
        self.assertEqual(payload["items"][0]["groupIds"], ["default"])
        self.assertEqual(
            self.client.server.app[COMFY_KEY].favorites_data["pose"]["items"], []
        )

    async def test_custom_group_templates_and_import_flow(self):
        expected_titles = {
            "character": "示例角色",
            "clothing": "休闲连帽衫",
            "pose": "站立挥手",
            "background": "樱花街道",
            "expression": "期待",
        }
        for section, title in expected_titles.items():
            template = await self.client.get(
                f"/api/custom-prompts/templates/{section}/json"
            )
            self.assertEqual(template.status, 200)
            self.assertIn(
                f"custom-prompts-{section}-template.json",
                template.headers["Content-Disposition"],
            )
            item = json.loads(await template.text())["items"][0]
            self.assertEqual(item["section"], section)
            self.assertEqual(item["title"], title)
        csv_template = await self.client.get("/api/custom-prompts/templates/pose/csv")
        self.assertEqual(csv_template.status, 200)
        self.assertIn(
            "section,title,prompt", (await csv_template.text()).lstrip("\ufeff")
        )

        group_response = await self.client.post(
            "/api/custom-groups/expression", json={"name": "Mood"}
        )
        self.assertEqual(group_response.status, 201)
        target_group_response = await self.client.post(
            "/api/custom-groups/expression", json={"name": "Imported"}
        )
        self.assertEqual(target_group_response.status, 201)
        target_group_id = (await target_group_response.json())["group"]["id"]
        preview_response = await self.client.post(
            "/api/custom-prompts/import/preview",
            json={
                "format": "json",
                "section": "expression",
                "bundleName": "Expression Pack",
                "content": '{"items":[{"section":"expression","title":"Calm","prompt":"calm face","groups":["Mood"]}]}',
            },
        )
        self.assertEqual(preview_response.status, 200)
        preview = await preview_response.json()
        self.assertEqual(preview["summary"]["new"], 1)
        committed = await self.client.post(
            "/api/custom-prompts/import",
            json={
                "rows": preview["rows"],
                "section": "expression",
                "targetGroupIds": [target_group_id],
                "bundleName": "Expression Pack",
            },
        )
        self.assertEqual(committed.status, 200)
        committed_payload = await committed.json()
        self.assertEqual(committed_payload["imported"], 1)
        pool = await (await self.client.get("/api/pools/expression?q=Calm")).json()
        self.assertEqual(pool["items"][0]["title"], "Calm")
        groups = (
            await (await self.client.get("/api/custom-groups/expression")).json()
        )["groups"]
        bundle = next(
            item for item in groups if item["id"] == committed_payload["bundleId"]
        )
        children = [item for item in groups if item.get("parentId") == bundle["id"]]
        self.assertEqual(bundle["count"], 1)
        self.assertEqual([item["name"] for item in children], ["Mood"])
        self.assertEqual(
            next(item for item in groups if item["id"] == target_group_id)["count"], 1
        )
        parent_pool = await (
            await self.client.get(f"/api/pools/expression?custom_group={bundle['id']}")
        ).json()
        self.assertEqual([item["title"] for item in parent_pool["items"]], ["Calm"])

        mixed_preview = await self.client.post(
            "/api/custom-prompts/import/preview",
            json={
                "format": "json",
                "section": "expression",
                "bundleName": "Expression Pack",
                "content": '{"items":[{"section":"pose","title":"Wave","prompt":"waving"}]}',
            },
        )
        self.assertEqual(mixed_preview.status, 200)
        self.assertEqual((await mixed_preview.json())["summary"]["error"], 1)

        invalid_target = await self.client.post(
            "/api/custom-prompts/import",
            json={
                "rows": [
                    {
                        "action": "create",
                        "item": {
                            "section": "expression",
                            "title": "Unsafe",
                            "prompt": "unsafe",
                        },
                    }
                ],
                "section": "expression",
                "targetGroupIds": ["custom_group_missing"],
                "bundleName": "Unsafe Pack",
            },
        )
        self.assertEqual(invalid_target.status, 400)
        unsafe_pool = await (
            await self.client.get("/api/pools/expression?q=Unsafe")
        ).json()
        self.assertEqual(unsafe_pool["total"], 0)

    async def test_missing_lora_returns_clear_error(self):
        settings = {
            **DEFAULT_SETTINGS,
            "loras": [
                {"filename": "missing.safetensors", "enabled": True, "strength": 1}
            ],
        }
        response = await self.client.post("/api/batches", json=settings)
        self.assertEqual(response.status, 400)
        self.assertIn("missing.safetensors", (await response.json())["error"])

    async def test_invalid_settings_return_400(self):
        response = await self.client.post(
            "/api/batches", json={**DEFAULT_SETTINGS, "count": 0}
        )
        self.assertEqual(response.status, 400)
        self.assertIn("生成数量", (await response.json())["error"])

        for percent in (0, 101, "", 60.5):
            with self.subTest(percent=percent):
                invalid_upscalers = copy.deepcopy(DEFAULT_SETTINGS["upscalers"])
                invalid_upscalers["pixel_hires"].update(enabled=True, percent=percent)
                response = await self.client.post(
                    "/api/batches",
                    json={**DEFAULT_SETTINGS, "upscalers": invalid_upscalers},
                )
                self.assertEqual(response.status, 400)
                self.assertIn(
                    "upscalers.pixel_hires.percent", (await response.json())["error"]
                )

        for field, value in (
            ("sampler_name", ""),
            ("sampler_name", 1),
            ("scheduler", ""),
            ("scheduler", False),
        ):
            with self.subTest(field=field, value=value):
                response = await self.client.post(
                    "/api/batches", json={**DEFAULT_SETTINGS, field: value}
                )
                self.assertEqual(response.status, 400)
                self.assertIn(field, (await response.json())["error"])

        for field, value in (("sampler_name", "missing"), ("scheduler", "missing")):
            with self.subTest(field=field):
                response = await self.client.post(
                    "/api/batches", json={**DEFAULT_SETTINGS, field: value}
                )
                self.assertEqual(response.status, 400)
                self.assertIn("不可用", (await response.json())["error"])

    async def test_new_dimension_settings_are_returned_and_persisted(self):
        settings = {
            **DEFAULT_SETTINGS,
            "random_character": False,
            "random_character_count": 2,
            "fixed_character": "hero",
            "random_clothing_count": 5,
        }
        response = await self.client.post("/api/batches", json=settings)
        self.assertEqual(response.status, 201)
        manager = self.client.server.app[MANAGER_KEY]
        await manager.wait()
        history = await (await self.client.get("/api/history")).json()
        saved = history["items"][0]["settings"]
        self.assertEqual(saved["random_character_count"], 2)
        self.assertEqual(saved["fixed_character"], "hero")
        self.assertEqual(saved["random_clothing_count"], 5)

    async def test_optional_sampling_settings_are_submitted_and_persisted(self):
        from anima_webui.workflow import validate_settings

        settings = validate_settings(
            {
                "count": 1,
                "base_mode": "dual_sampler",
                "upscalers": {
                    "latent": {
                        "enabled": True,
                        "sampler": {"steps": 7, "cfg": 4, "denoise": 0.25},
                    },
                    "post_sampler": {
                        "enabled": True,
                        "steps": 9,
                        "cfg": 5,
                        "denoise": 0.45,
                    },
                },
            }
        )
        response = await self.client.post("/api/batches", json=settings)
        self.assertEqual(response.status, 201, await response.text())
        state = await response.json()
        self.assertEqual(state["settings"]["upscalers"], settings["upscalers"])
        self.assertEqual(
            (await self.client.server.app[MANAGER_KEY].wait())["status"], "completed"
        )
        prompt = self.comfy.submissions[-1]["prompt"]
        self.assertIn("701:1245", prompt)
        self.assertIn("701:1246", prompt)
        latent = prompt["v4:latent:sample"]["inputs"]
        post = next(
            node["inputs"]
            for node in prompt.values()
            if node["class_type"] == "KSampler"
            and node.get("_meta", {}).get("pipeline_stage") == "post_sampler"
        )
        self.assertEqual(
            (latent["steps"], latent["cfg"], latent["denoise"]), (7, 4, 0.25)
        )
        self.assertEqual((post["steps"], post["cfg"], post["denoise"]), (9, 5, 0.45))
        history = await (await self.client.get("/api/history")).json()
        self.assertEqual(
            history["items"][0]["settings"]["upscalers"], settings["upscalers"]
        )

    async def test_batch_submission_normalizes_settings_and_resolved_prompt(self):
        settings = {
            **DEFAULT_SETTINGS,
            "count": 1,
            "quality_prompt": "2025，SCORE 9, Blue_Hair",
            "extra_prompt": "@Niji9il, Blue_Hair",
            "lora_managed_triggers": ["@Niji9il"],
        }
        response = await self.client.post("/api/batches", json=settings)
        self.assertEqual(response.status, 201)
        state = await response.json()
        self.assertEqual(
            state["settings"]["quality_prompt"], "year 2025, score_9, blue hair"
        )
        self.assertEqual(state["settings"]["extra_prompt"], "@Niji9il, blue hair")
        await self.client.server.app[MANAGER_KEY].wait()
        submitted = self.comfy.submissions[-1]["prompt"]
        final_prompt = "year 2025, score_9, blue hair, 1girl, @Niji9il"
        self.assertEqual(submitted["1010:995"]["inputs"]["text"], final_prompt)
        self.assertNotIn("42", submitted)
        self.assertNotIn("60", submitted)

    async def test_batch_completes_and_history_is_paged(self):
        response = await self.client.post(
            "/api/batches", json={**DEFAULT_SETTINGS, "count": 1}
        )
        self.assertEqual(response.status, 201)
        manager = self.client.server.app[MANAGER_KEY]
        await manager.wait()
        current = await (await self.client.get("/api/batches/current")).json()
        self.assertEqual(current["batch"]["status"], "completed")
        history = await (await self.client.get("/api/history?page=1&limit=10")).json()
        self.assertEqual(history["total"], 1)
        image_id = history["items"][0]["id"]
        image = await self.client.get(f"/api/images/{image_id}")
        self.assertEqual(await image.read(), b"png")
        deleted = await self.client.delete(f"/api/history/{image_id}")
        self.assertEqual(deleted.status, 200)
        self.assertEqual(
            (await (await self.client.get("/api/history")).json())["total"], 0
        )

    async def test_missing_history_record_returns_404(self):
        self.assertEqual((await self.client.get("/api/images/999")).status, 404)

    async def test_reveal_image_uses_saved_anima_record(self):
        headers = {"Origin": str(self.client.make_url("/")).rstrip("/")}
        history = self.client.server.app[HISTORY_KEY]
        record = {
            "id": 51,
            "filename": "saved.png",
            "subfolder": "saved/folder",
            "file_type": "output",
            "settings": {"workbench": "random"},
        }
        with (
            mock.patch.object(
                history, "get_image", new=mock.AsyncMock(return_value=record)
            ) as get_record,
            mock.patch("anima_webui.server.reveal_image") as reveal,
        ):
            response = await self.client.post(
                "/api/history/51/reveal", json={}, headers=headers
            )
            self.assertEqual(response.status, 200)
            self.assertEqual(await response.json(), {"opened": True})
            get_record.assert_awaited_once_with(51)
            reveal.assert_called_once_with(
                record, await self.comfy.status(), self.comfy.base_url
            )

    async def test_reveal_requires_direct_local_same_origin_json_request(self):
        origin = str(self.client.make_url("/")).rstrip("/")
        with mock.patch("anima_webui.server.reveal_image") as reveal:
            for headers in (
                {},
                {"Origin": "http://localhost:1234"},
                {"Origin": origin, "Sec-Fetch-Site": "cross-site"},
                {"Origin": origin, "Forwarded": "for=192.0.2.1"},
                {"Origin": origin, "X-Forwarded-For": "127.0.0.1"},
            ):
                with self.subTest(headers=headers):
                    response = await self.client.post(
                        "/api/history/51/reveal", json={}, headers=headers
                    )
                    self.assertEqual(response.status, 403)
            with mock.patch(
                "anima_webui.server.ipaddress.ip_address",
                return_value=mock.Mock(is_loopback=False),
            ):
                response = await self.client.post(
                    "/api/history/51/reveal", json={}, headers={"Origin": origin}
                )
                self.assertEqual(response.status, 403)
            response = await self.client.post(
                "/api/history/51/reveal",
                data="{}",
                headers={"Origin": origin, "Content-Type": "text/plain"},
            )
            self.assertEqual(response.status, 415)
            response = await self.client.post(
                "/api/history/51/reveal",
                json={"path": "C:/arbitrary.png"},
                headers={"Origin": origin},
            )
            self.assertEqual(response.status, 400)
            reveal.assert_not_called()

    async def test_reveal_missing_record_and_errors_do_not_report_success(self):
        headers = {"Origin": str(self.client.make_url("/")).rstrip("/")}
        with mock.patch("anima_webui.server.reveal_image") as reveal:
            response = await self.client.post(
                "/api/history/999/reveal", json={}, headers=headers
            )
            self.assertEqual(response.status, 404)
            response = await self.client.post(
                "/api/history/9223372036854775808/reveal", json={}, headers=headers
            )
            self.assertEqual(response.status, 400)
            reveal.assert_not_called()
        with (
            mock.patch.object(
                self.client.server.app[HISTORY_KEY],
                "get_image",
                new=mock.AsyncMock(return_value={"id": 51}),
            ),
            mock.patch(
                "anima_webui.server.reveal_image",
                side_effect=ImageLocationError(
                    "原图文件不存在，可能已被移动或删除",
                    code="image_file_missing",
                    status=404,
                ),
            ),
        ):
            response = await self.client.post(
                "/api/history/51/reveal", json={}, headers=headers
            )
            self.assertEqual(response.status, 404)
            self.assertEqual((await response.json())["code"], "image_file_missing")
        response = await self.client.get("/api/history/51/reveal")
        self.assertEqual(response.status, 405)

    async def test_non_numeric_image_id_returns_404_not_500(self):
        # 回归 #P2-1:此前 int() 裸抛 ValueError 变成 500。
        self.assertEqual((await self.client.get("/api/images/abc")).status, 404)
        self.assertEqual((await self.client.delete("/api/history/abc")).status, 404)

    async def test_pool_query_endpoint_filters_and_selection(self):
        # 此前 POST /api/pools/{section}/query 零测试覆盖(#P2-13)。
        payload = await (
            await self.client.post(
                "/api/pools/pose/query",
                json={"page": 1, "limit": 48, "q": "Standing"},
            )
        ).json()
        self.assertEqual([item["id"] for item in payload["items"]], ["pose:stand"])
        excluded = await (
            await self.client.post(
                "/api/pools/pose/query",
                json={
                    "page": 1,
                    "limit": 48,
                    "selection": {
                        "mode": "include",
                        "ids": ["pose:sit"],
                        "excluded_ids": [],
                    },
                },
            )
        ).json()
        self.assertEqual([item["id"] for item in excluded["items"]], ["pose:sit"])
        bad = await self.client.post("/api/pools/pose/query", json={"page": "abc"})
        self.assertEqual(bad.status, 400)

    async def test_pool_ids_cover_large_results_without_pagination_or_full_items(self):
        catalog = self.client.server.app[CATALOG_KEY]
        entries = [
            catalog._to_entry(
                "character",
                {
                    "name": f"character {index:04}",
                    "copyright": "large",
                    "post_count": index,
                },
            )
            for index in range(8000)
        ]
        catalog._items["character"] = entries
        catalog._by_id.update({item["id"]: item for item in entries})
        expected = [item["id"] for item in reversed(entries)]
        for limit in (1, 48, 100, 999999):
            with self.subTest(limit=limit):
                response = await self.client.post(
                    "/api/pools/character/ids", json={"page": 2, "limit": limit}
                )
                self.assertEqual(response.status, 200)
                self.assertEqual(
                    await response.json(),
                    {"ids": expected, "total": 8000, "pool_total": 8000},
                )
        page = await (await self.client.get("/api/pools/character?limit=999999")).json()
        self.assertEqual(
            (len(page["items"]), page["pages"], page["pool_total"]), (100, 80, 8000)
        )

    async def test_pool_ids_use_combined_collection_group_and_content_filters(self):
        group = await (
            await self.client.post("/api/custom-groups/pose", json={"name": "Match"})
        ).json()
        item = await (
            await self.client.post(
                "/api/custom-prompts",
                json={
                    "section": "pose",
                    "title": "Matching pose",
                    "prompt": "matching pose",
                    "categories": ["Gestures & Arms"],
                    "traits": ["hand", "pointing"],
                    "groupIds": [group["group"]["id"]],
                },
            )
        ).json()
        await self.client.put(
            "/api/favorites/pose/item", json={"id": item["id"], "favorite": True}
        )
        query = {
            "q": "matching",
            "custom_group": group["group"]["id"],
            "collection": "default",
            "categories": ["Gestures & Arms"],
            "traits": ["hand", "pointing"],
            "sort": "favorite-first",
            "selection_state": {
                "mode": "include",
                "ids": [item["id"], "pose:stand", "missing"],
            },
        }
        page = await (
            await self.client.post("/api/pools/pose/query", json=query)
        ).json()
        result = await (
            await self.client.post("/api/pools/pose/ids", json=query)
        ).json()
        self.assertEqual([value["id"] for value in page["items"]], [item["id"]])
        self.assertEqual(
            result,
            {"ids": [item["id"]], "total": 1, "pool_total": 3, "selected_total": 2},
        )
        query["selection"] = {"mode": "include", "ids": ["pose:stand"]}
        result = await (
            await self.client.post("/api/pools/pose/ids", json=query)
        ).json()
        self.assertEqual((result["ids"], result["selected_total"]), ([], 2))

    async def test_pool_selection_totals_do_not_filter_legacy_list_results(self):
        legacy = await (await self.client.get("/api/pools/pose?q=Standing")).json()
        self.assertEqual((legacy["total"], legacy["pool_total"]), (1, 2))
        self.assertNotIn("selected_total", legacy)
        states = [
            (
                {
                    "mode": "include",
                    "ids": [
                        "pose:stand",
                        "pose:sit",
                        "pose:sit",
                        "missing",
                        "clothing:coat",
                    ],
                },
                2,
            ),
            (
                {
                    "mode": "all",
                    "excluded_ids": [
                        "pose:stand",
                        "pose:stand",
                        "missing",
                        "clothing:coat",
                    ],
                },
                1,
            ),
        ]
        for state, expected in states:
            for endpoint in ("query", "ids"):
                with self.subTest(endpoint=endpoint, state=state):
                    result = await (
                        await self.client.post(
                            f"/api/pools/pose/{endpoint}",
                            json={
                                "q": "Standing",
                                "selection_state": state,
                            },
                        )
                    ).json()
                    self.assertEqual(
                        (
                            result["total"],
                            result["pool_total"],
                            result["selected_total"],
                        ),
                        (1, 2, expected),
                    )
        for body in (
            {"model_profile": "krea2"},
            {"selection_state": []},
            {"selection_state": {"ids": "bad"}},
        ):
            for endpoint in ("query", "ids"):
                with self.subTest(body=body, endpoint=endpoint):
                    response = await self.client.post(
                        f"/api/pools/pose/{endpoint}", json=body
                    )
                    self.assertEqual(response.status, 400)

    async def test_non_local_host_or_origin_is_rejected(self):
        # 回归 #P2-2:恶意网页的 CSRF/DNS 重绑定应被本机来源校验拦截。
        spoofed_host = await self.client.get(
            "/api/config", headers={"Host": "evil.example:8190"}
        )
        self.assertEqual(spoofed_host.status, 403)
        evil_origin = await self.client.post(
            "/api/style-presets", json={}, headers={"Origin": "http://evil.example"}
        )
        self.assertEqual(evil_origin.status, 403)
        null_origin = await self.client.post(
            "/api/style-presets", json={}, headers={"Origin": "null"}
        )
        self.assertEqual(null_origin.status, 403)
        local_origin = await self.client.get(
            "/api/config", headers={"Origin": f"http://127.0.0.1:{self.client.port}"}
        )
        self.assertEqual(local_origin.status, 200)

    async def test_remote_trusted_host_is_rejected_before_storage_creation(self):
        destination = Path(self.temp.name) / "remote-data"
        with self.assertRaisesRegex(ValueError, "本机"):
            create_app(
                app_dir=APP_DIR,
                data_dir=destination,
                comfy=FakeComfy(),
                anima_tools_dir=Path(self.temp.name) / "tools",
                trusted_hostnames={"anima.example.com"},
            )
        self.assertFalse(destination.exists())

    async def test_config_exposes_startup_warnings(self):
        payload = await (await self.client.get("/api/config")).json()
        self.assertEqual(payload["warnings"], [])

    async def test_current_batch_includes_queue_and_preview_endpoint(self):
        # 0.2.0:current 返回队列;预览端点无帧时 204,有帧时回图。
        current = await (await self.client.get("/api/batches/current")).json()
        self.assertEqual(current["queue"], [])
        self.assertEqual(
            (await self.client.get("/api/batches/current/preview")).status, 204
        )
        manager = self.client.server.app[MANAGER_KEY]
        manager.preview = (1, "image/jpeg", b"frame")
        preview = await self.client.get("/api/batches/current/preview")
        self.assertEqual(preview.status, 200)
        self.assertEqual(await preview.read(), b"frame")
        self.assertEqual(preview.headers["Content-Type"], "image/jpeg")
        missing = await self.client.delete("/api/batches/queue/queue_missing")
        self.assertEqual(missing.status, 404)

    async def test_batch_with_seeds_reproduces_exact_image(self):
        # 0.2.0:复现请求携带 seeds,历史记录里的种子必须一字不差。
        response = await self.client.post(
            "/api/batches",
            json={
                **DEFAULT_SETTINGS,
                "count": 1,
                "seeds": {"sample_seed": 987654321, "prompt_seed": 24680},
            },
        )
        self.assertEqual(response.status, 201)
        await self.client.server.app[MANAGER_KEY].wait()
        history = await (await self.client.get("/api/history")).json()
        self.assertEqual(history["items"][0]["sample_seed"], 987654321)
        self.assertEqual(history["items"][0]["prompt_seed"], 24680)
        bad = await self.client.post(
            "/api/batches",
            json={
                **DEFAULT_SETTINGS,
                "count": 1,
                "seeds": {"sample_seed": -5, "prompt_seed": 1},
            },
        )
        self.assertEqual(bad.status, 400)

    async def test_batch_with_sample_only_seed_randomizes_prompts_and_can_replay(self):
        with mock.patch("anima_webui.runner.secrets.randbelow", side_effect=[123, 456]):
            response = await self.client.post(
                "/api/batches",
                json={**DEFAULT_SETTINGS, "count": 2, "seeds": {"sample_seed": 0}},
            )
            self.assertEqual(response.status, 201)
            result = await self.client.server.app[MANAGER_KEY].wait()
        self.assertEqual(result["status"], "completed")
        history = await (await self.client.get("/api/history")).json()
        images = sorted(history["items"], key=lambda image: image["sequence"])
        self.assertEqual([image["sample_seed"] for image in images], [0, 0])
        self.assertEqual([image["prompt_seed"] for image in images], [123, 456])

        with mock.patch("anima_webui.runner.secrets.randbelow") as random_seed:
            replay = await self.client.post(
                f"/api/history/{images[0]['id']}/regenerate",
                json={"mode": "replay"},
            )
            self.assertEqual(replay.status, 201)
            result = await self.client.server.app[MANAGER_KEY].wait()
        self.assertEqual(result["status"], "completed")
        random_seed.assert_not_called()
        replayed = (await (await self.client.get("/api/history")).json())["items"][0]
        self.assertEqual((replayed["sample_seed"], replayed["prompt_seed"]), (0, 123))

    async def _create_history_image(self):
        response = await self.client.post(
            "/api/batches", json={**DEFAULT_SETTINGS, "count": 1}
        )
        self.assertEqual(response.status, 201)
        await self.client.server.app[MANAGER_KEY].wait()
        history = await (await self.client.get("/api/history")).json()
        return history["items"][0]

    async def test_minimal_random_submission_defaults_to_v4(self):
        response = await self.client.post(
            "/api/batches", json={"count": 1, "base_mode": "normal"}
        )
        self.assertEqual(response.status, 201)
        result = await self.client.server.app[MANAGER_KEY].wait()
        self.assertEqual(result["status"], "completed")
        image = (await (await self.client.get("/api/history")).json())["items"][0]
        self.assertEqual(image["settings"]["pipeline_version"], 4)
        self.assertFalse(image["settings"]["upscalers"]["post_sampler"]["enabled"])

    async def test_replay_refreshes_execution_identity_without_changing_seeds_or_settings(
        self,
    ):
        image = await self._create_history_image()
        original_context = image["execution_context"]
        self.assertTrue(original_context["execution_id"])
        response = await self.client.post(
            f"/api/history/{image['id']}/regenerate", json={"mode": "replay"}
        )
        self.assertEqual(response.status, 201, await response.text())
        await self.client.server.app[MANAGER_KEY].wait()
        replayed = (await self.client.server.app[HISTORY_KEY].list_images())["items"][0]
        context = replayed["execution_context"]
        self.assertNotEqual(context["execution_id"], original_context["execution_id"])
        self.assertNotEqual(
            context["execution_nonce"], original_context["execution_nonce"]
        )
        self.assertEqual(replayed["settings"], image["settings"])
        self.assertEqual(
            (replayed["sample_seed"], replayed["prompt_seed"]),
            (image["sample_seed"], image["prompt_seed"]),
        )
        payload = self.comfy.submissions[-1]
        self.assertEqual(effective_base_seed(payload["prompt"]), image["sample_seed"])
        self.assertEqual(
            payload["prompt"]["v3:run:nonce"]["inputs"]["value"],
            context["execution_nonce"],
        )
        metadata = payload["extra_data"]["extra_pnginfo"]["anima_random_webui"]
        self.assertEqual(metadata["execution_context"], context)
        self.assertNotIn("execution_context", metadata["settings"])

    async def test_config_and_minimal_submission_keep_anima_default_stage_settings(
        self,
    ):
        config = await (await self.client.get("/api/config")).json()
        response = await self.client.post("/api/batches", json={"count": 1})
        self.assertEqual(response.status, 201, await response.text())
        state = await response.json()
        self.assertEqual(
            state["settings"]["upscalers"], config["defaults"]["upscalers"]
        )
        self.assertEqual(state["settings"]["upscalers"], DEFAULT_SETTINGS["upscalers"])
        self.assertEqual(
            (await self.client.server.app[MANAGER_KEY].wait())["status"], "completed"
        )

    async def test_history_regeneration_keeps_v4_pipeline_in_every_mode(self):
        settings = {
            **copy.deepcopy(DEFAULT_SETTINGS),
            "count": 1,
            "base_mode": "normal",
            "random_pose": True,
        }
        settings["pools"]["pose"] = {
            "mode": "include",
            "ids": ["pose:stand", "pose:sit"],
            "excluded_ids": [],
        }
        store = self.client.server.app[HISTORY_KEY]
        await store.create_batch("legacy-pipeline", 1, settings)
        image = await store.add_image(
            batch_id="legacy-pipeline",
            sequence=1,
            prompt_id="legacy",
            image={"filename": "legacy.png", "type": "output", "subfolder": ""},
            positive_prompt="standing",
            negative_prompt="",
            sample_seed=123,
            prompt_seed=456,
            settings=settings,
        )
        for mode in ("replay", "prompt_variant", "settings_reroll", "content_redraw"):
            with self.subTest(mode=mode):
                payload = {"mode": mode}
                if mode != "replay":
                    payload["count"] = 1
                if mode == "content_redraw":
                    payload["sections"] = ["pose"]
                response = await self.client.post(
                    f"/api/history/{image['id']}/regenerate", json=payload
                )
                self.assertEqual(response.status, 201, await response.text())
                result = await self.client.server.app[MANAGER_KEY].wait()
                self.assertEqual(result["status"], "completed", result)
                saved = self.comfy.submissions[-1]["extra_data"]["extra_pnginfo"][
                    "anima_random_webui"
                ]["settings"]
                self.assertEqual(saved["pipeline_version"], 4)
        self.assertEqual((await store.get_image(image["id"]))["settings"], settings)

    async def test_v4_latent_history_replay_preserves_saved_sampler_settings(self):
        from anima_webui.workflow import validate_settings

        settings = validate_settings(
            {
                "base_mode": "normal",
                "count": 1,
                "upscalers": {
                    "latent": {
                        "enabled": True,
                        "sampler": {"steps": 11, "cfg": 3, "denoise": 0.21},
                    }
                },
            }
        )
        store = self.client.server.app[HISTORY_KEY]
        await store.create_batch("frozen-v4", 1, settings)
        image = await store.add_image(
            batch_id="frozen-v4",
            sequence=1,
            prompt_id="saved",
            image={"filename": "v4.png"},
            positive_prompt="cat",
            negative_prompt="",
            sample_seed=12,
            prompt_seed=34,
            settings=settings,
        )
        response = await self.client.post(
            f"/api/history/{image['id']}/regenerate", json={"mode": "replay"}
        )
        self.assertEqual(response.status, 201, await response.text())
        self.assertEqual(
            (await self.client.server.app[MANAGER_KEY].wait())["status"], "completed"
        )
        replay = self.comfy.submissions[-1]
        saved = replay["extra_data"]["extra_pnginfo"]["anima_random_webui"]["settings"]
        self.assertEqual(saved["pipeline_version"], 4)
        latent = replay["prompt"]["v4:latent:sample"]["inputs"]
        self.assertEqual(
            (latent["steps"], latent["cfg"], latent["denoise"]), (11, 3, 0.21)
        )
        self.assertEqual(effective_base_seed(replay["prompt"]), 12)
        self.assertEqual(replay["prompt"]["1010:995"]["inputs"]["text"], "cat")
        self.assertEqual((await store.get_image(image["id"]))["settings"], settings)

    async def test_regeneration_options_and_frozen_modes(self):
        image = await self._create_history_image()
        options = await (
            await self.client.get(f"/api/history/{image['id']}/regeneration-options")
        ).json()
        self.assertTrue(options["modes"]["replay"]["available"])
        self.assertTrue(options["modes"]["prompt_variant"]["available"])
        original_submissions = len(self.comfy.submissions)
        replay = await self.client.post(
            f"/api/history/{image['id']}/regenerate", json={"mode": "replay"}
        )
        self.assertEqual(replay.status, 201)
        await self.client.server.app[MANAGER_KEY].wait()
        replay_prompt = self.comfy.submissions[original_submissions]["prompt"]
        self.assertEqual(effective_base_seed(replay_prompt), image["sample_seed"])
        self.assertEqual(
            self.comfy.submissions[original_submissions]["extra_data"]["extra_pnginfo"][
                "anima_random_webui"
            ]["prompt_seed"],
            image["prompt_seed"],
        )
        self.assertEqual(replay_prompt["1010:995"]["inputs"]["text"], "generated")
        for removed_id in ("37", "42", "49", "60"):
            self.assertNotIn(removed_id, replay_prompt)

        variant = await self.client.post(
            f"/api/history/{image['id']}/regenerate",
            json={"mode": "prompt_variant", "count": 2},
        )
        self.assertEqual(variant.status, 201)
        await self.client.server.app[MANAGER_KEY].wait()
        variant_prompts = self.comfy.submissions[-2:]
        self.assertTrue(
            all(
                item["extra_data"]["extra_pnginfo"]["anima_random_webui"]["prompt_seed"]
                == image["prompt_seed"]
                for item in variant_prompts
            )
        )
        self.assertTrue(
            all(
                effective_base_seed(item["prompt"]) != image["sample_seed"]
                for item in variant_prompts
            )
        )

    async def test_regeneration_validation_and_missing_resources(self):
        image = await self._create_history_image()
        endpoint = f"/api/history/{image['id']}/regenerate"
        cases = [
            ({"mode": "replay", "count": 2}, "不接受"),
            ({"mode": "prompt_variant", "count": 0}, "count"),
            ({"mode": "content_redraw", "count": 2, "sections": []}, "sections"),
            (
                {"mode": "content_redraw", "count": 2, "sections": ["pose", "pose"]},
                "重复",
            ),
            (
                {"mode": "content_redraw", "count": 2, "sections": ["unknown"]},
                "sections",
            ),
        ]
        for payload, text in cases:
            with self.subTest(payload=payload):
                response = await self.client.post(endpoint, json=payload)
                self.assertEqual(response.status, 400)
                self.assertIn(text, (await response.json())["error"])

        async def missing_inventory():
            return {
                "models": [],
                "upscale_models": [],
                "samplers": [],
                "schedulers": [],
            }

        self.comfy.resource_inventory = missing_inventory
        options = await (
            await self.client.get(f"/api/history/{image['id']}/regeneration-options")
        ).json()
        self.assertFalse(options["modes"]["replay"]["available"])
        self.assertTrue(options["resourceIssues"])

    async def test_launcher_and_removed_workbench_routes_are_absent(self):
        for path in (
            "/api/launcher-shutdown",
            "/api/prompt-jobs",
            "/api/reverse-prompt",
            "/api/single-presets",
        ):
            with self.subTest(path=path):
                response = await self.client.post(path, json={"token": "secret-token"})
                self.assertEqual(response.status, 404)
                self.assertIn("error", await response.json())
        self.assertEqual(self.comfy.submissions, [])


if __name__ == "__main__":
    unittest.main()
