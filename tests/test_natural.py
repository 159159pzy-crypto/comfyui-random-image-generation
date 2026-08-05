import asyncio
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

from anima_natural.assets import AssetError, AssetStore
from anima_natural.engine import NaturalEngine, NaturalEngineError
from anima_natural.jobs import NaturalJobManager
from anima_natural.providers import (
    MemorySecretStore,
    OpenAIProviderClient,
    ProviderProfile,
    ProviderRegistry,
    ProviderRegistryError,
)
from anima_natural.upstream.services.character_identity import (
    resolve_character_identity,
)
from anima_natural.upstream.services.danbooru_index import TagLookup
from anima_natural.upstream.services.prompt_director import (
    EditInstruction,
    PictureInstruction,
)
from anima_natural.upstream.services.reverse_prompt import (
    ReverseCharacter,
    ReversePromptResult,
    parse_reverse_prompt,
)
from anima_webui.comfy import ComfyAborted
from anima_webui.history import HistoryStore
from anima_webui.server import create_app
from anima_webui.workflow import DEFAULT_SETTINGS

APP_DIR = Path(__file__).resolve().parents[1]


def image_bytes(color: str = "red") -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(output, format="PNG")
    return output.getvalue()


class NaturalProviderTests(unittest.TestCase):
    def test_provider_keys_stay_out_of_plain_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            secrets = MemorySecretStore()
            registry = ProviderRegistry(directory, secret_store=secrets)
            profile = registry.upsert(
                {
                    "name": "Local OpenAI",
                    "base_url": "http://127.0.0.1:9000/v1",
                    "director_model": "director",
                    "vision_model": "vision",
                    "api_key": "top-secret",
                }
            )
            self.assertTrue(profile["has_api_key"])
            self.assertNotIn("api_key", profile)
            persisted = (Path(directory) / "providers.json").read_text(encoding="utf-8")
            self.assertNotIn("top-secret", persisted)
            self.assertNotIn("has_api_key", persisted)
            reloaded = ProviderRegistry(directory, secret_store=secrets)
            self.assertEqual(reloaded.api_key(profile["id"]), "top-secret")

    def test_character_identity_ignores_zero_count_exact_conflict(self):
        class Index:
            @staticmethod
            def lookup_many(values, _category="character"):
                records = {
                    "hatsune_miku": (142905, "hatsune_miku"),
                    "miku_hatsune": (0, "miku_hatsune"),
                }
                return tuple(
                    TagLookup(
                        query=value,
                        normalized_query=value,
                        tag=records[value][1],
                        canonical_tag=records[value][1],
                        category="character",
                        count=records[value][0],
                        match_type="canonical",
                        matched_value=value,
                        verified=True,
                    )
                    for value in values
                )

        result = resolve_character_identity(
            Index(),
            target_query="hatsune_miku",
            canonical_tag="hatsune_miku",
            identity_candidates=("miku_hatsune",),
        )
        self.assertTrue(result.verified)
        self.assertFalse(result.ambiguous)
        self.assertEqual(result.canonical_tag, "hatsune_miku")
        self.assertEqual(result.match_variant, "active_exact_over_zero_count_conflict")


class NaturalProviderProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.registry = ProviderRegistry(self.temp.name, secret_store=MemorySecretStore())
        self.profile_data = self.registry.upsert(
            {
                "name": "Fake",
                "base_url": "http://127.0.0.1",
                "director_model": "director",
                "vision_model": "vision",
                "embedding_model": "embedding",
                "rerank_model": "rerank",
            }
        )
        self.client = OpenAIProviderClient(self.registry)

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def _serve(self, handler):
        app = web.Application()
        app.router.add_route("*", "/v1/{tail:.*}", handler)
        server = TestServer(app)
        await server.start_server()
        profile = self.registry.get(self.profile_data["id"])
        profile = ProviderProfile(**{**profile.__dict__, "base_url": str(server.make_url("/v1")).rstrip("/")})
        self.registry._profiles[profile.id] = profile
        return server, profile

    async def test_streaming_content_and_tool_calls_are_assembled(self):
        async def handler(request):
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            chunks = [
                {"choices": [{"delta": {"content": "hel"}}]},
                {"choices": [{"delta": {"content": "lo", "tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "lookup", "arguments": '{"q":'}}]}}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"x"}'}}]}, "finish_reason": "tool_calls"}]},
            ]
            for chunk in chunks:
                await response.write(f"data: {json.dumps(chunk)}\n\n".encode())
            await response.write(b"data: [DONE]\n\n")
            return response

        server, profile = await self._serve(handler)
        try:
            data = await self.client.chat(profile, model="director", prompt="x", stream=True)
            message = data["choices"][0]["message"]
            self.assertEqual(message["content"], "hello")
            self.assertEqual(message["tool_calls"][0]["function"]["name"], "lookup")
            self.assertEqual(json.loads(message["tool_calls"][0]["function"]["arguments"]), {"q": "x"})
        finally:
            await server.close()

    def test_swap_reverse_normalizes_center_position_to_unspecified(self):
        result = parse_reverse_prompt(
            json.dumps(
                {
                    "positive_tags": "1girl, solo, white hair, blue eyes",
                    "negative_tags": "",
                    "characters": [
                        {
                            "name": "",
                            "source_work": "",
                            "gender": "girl",
                            "appearance_tags": ["white hair", "blue eyes"],
                            "outfit_tags": [],
                            "action_tags": [],
                            "position": "center",
                            "confidence": 0.9,
                        }
                    ],
                    "confidence": 0.9,
                }
            ),
            profile="swap",
        )
        self.assertEqual(result.characters[0].position, "")

    async def test_system_prompt_does_not_drop_image_user_message(self):
        captured = {}

        async def handler(request):
            captured.update(await request.json())
            return web.json_response({"choices": [{"message": {"content": "ok"}}]})

        image_path = Path(self.temp.name) / "source.png"
        image_path.write_bytes(image_bytes())
        server, profile = await self._serve(handler)
        try:
            result = await self.client.complete(
                profile,
                model="vision",
                prompt="inspect image",
                system_prompt="return structured facts",
                image_paths=[str(image_path)],
            )
            self.assertEqual(result, "ok")
            self.assertEqual([item["role"] for item in captured["messages"]], ["system", "user"])
            content = captured["messages"][1]["content"]
            self.assertEqual(content[0], {"type": "text", "text": "inspect image"})
            self.assertEqual(content[1]["type"], "image_url")
            self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))
        finally:
            await server.close()

    async def test_transient_invalid_argument_retries_without_response_format(self):
        requests = []

        async def handler(request):
            requests.append(await request.json())
            if len(requests) < 3:
                return web.json_response(
                    {"error": {"message": "Request contains an invalid argument."}},
                    status=400,
                )
            return web.json_response({"choices": [{"message": {"content": "ok"}}]})

        image_path = Path(self.temp.name) / "source.png"
        image_path.write_bytes(image_bytes())
        server, profile = await self._serve(handler)
        try:
            result = await self.client.complete(
                profile,
                model="vision",
                prompt="inspect image",
                system_prompt="return JSON facts",
                image_paths=[str(image_path)],
            )
            self.assertEqual(result, "ok")
            self.assertEqual(len(requests), 3)
            self.assertEqual(requests[0]["response_format"], {"type": "json_object"})
            self.assertNotIn("response_format", requests[1])
            self.assertNotIn("response_format", requests[2])
        finally:
            await server.close()

    async def test_empty_vision_content_retries_with_larger_output_budget(self):
        requests = []

        async def handler(request):
            requests.append(await request.json())
            if len(requests) == 1:
                return web.json_response(
                    {
                        "choices": [
                            {
                                "finish_reason": "length",
                                "message": {
                                    "content": "",
                                    "reasoning_content": "hidden",
                                },
                            }
                        ]
                    }
                )
            return web.json_response({"choices": [{"message": {"content": "ok"}}]})

        image_path = Path(self.temp.name) / "source.png"
        image_path.write_bytes(image_bytes())
        server, profile = await self._serve(handler)
        try:
            result = await self.client.complete(
                profile,
                model="vision",
                prompt="inspect image",
                system_prompt="return JSON facts",
                image_paths=[str(image_path)],
                max_tokens=1600,
            )
            self.assertEqual(result, "ok")
            self.assertEqual(len(requests), 2)
            self.assertEqual(requests[0]["max_tokens"], 1600)
            self.assertEqual(requests[1]["max_tokens"], 4096)
            self.assertNotIn("response_format", requests[1])
        finally:
            await server.close()

    async def test_complete_accepts_structured_visible_content(self):
        async def handler(_request):
            return web.json_response(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": {
                                    "type": "output_text",
                                    "text": "structured answer",
                                }
                            },
                        }
                    ]
                }
            )

        server, profile = await self._serve(handler)
        try:
            result = await self.client.complete(
                profile,
                model="director",
                prompt="x",
            )
            self.assertEqual(result, "structured answer")
        finally:
            await server.close()

    async def test_embeddings_rerank_and_bounded_tool_loop(self):
        async def handler(request):
            body = await request.json()
            if request.path.endswith("/embeddings"):
                return web.json_response({"data": [{"index": index, "embedding": [float(index + 1), 0.5]} for index, _ in enumerate(body["input"])]})
            if request.path.endswith("/rerank"):
                return web.json_response({"results": [{"index": 1, "relevance_score": 0.9}]})
            if any(item.get("role") == "tool" for item in body["messages"]):
                return web.json_response({"choices": [{"message": {"content": "done"}}]})
            return web.json_response({"choices": [{"message": {"content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "lookup", "arguments": json.dumps({"q": "tag"})}}]}}]})

        server, profile = await self._serve(handler)
        try:
            self.assertEqual(len(await self.client.embeddings(profile, ["a", "b"])), 2)
            ranked = await self.client.rerank(profile, "q", ["a", "b"])
            self.assertEqual(ranked[0]["index"], 1)
            result = await self.client.bounded_tool_loop(
                profile,
                model="director",
                prompt="x",
                system_prompt="",
                tools={"lookup": ({"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}, lambda args: {"hit": args["q"]})},
            )
            self.assertEqual(result, "done")
        finally:
            await server.close()

    async def test_tool_loop_accepts_structured_final_content_and_standard_tool_message(self):
        requests = []

        async def handler(request):
            body = await request.json()
            requests.append(body)
            if len(requests) == 1:
                return web.json_response(
                    {
                        "choices": [
                            {
                                "finish_reason": "tool_calls",
                                "message": {
                                    "role": "assistant",
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": "call_structured",
                                            "type": "function",
                                            "function": {
                                                "name": "lookup",
                                                "arguments": {"q": "tag"},
                                            },
                                        }
                                    ],
                                },
                            }
                        ]
                    }
                )
            self.assertEqual(requests[1]["messages"][-2]["content"], None)
            self.assertEqual(
                requests[1]["messages"][-2]["tool_calls"][0]["function"]["arguments"],
                '{"q":"tag"}',
            )
            self.assertEqual(
                requests[1]["messages"][-1],
                {
                    "role": "tool",
                    "tool_call_id": "call_structured",
                    "content": '{"hit":"tag"}',
                },
            )
            return web.json_response(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": [
                                    {"type": "output_text", "text": "final "},
                                    {"type": "text", "text": "answer"},
                                ],
                            },
                        }
                    ]
                }
            )

        server, profile = await self._serve(handler)
        try:
            result = await self.client.bounded_tool_loop(
                profile,
                model="director",
                prompt="x",
                system_prompt="",
                tools={
                    "lookup": (
                        {
                            "type": "function",
                            "function": {"name": "lookup", "parameters": {"type": "object"}},
                        },
                        lambda args: {"hit": args["q"]},
                    )
                },
            )
            self.assertEqual(result, "final answer")
            self.assertEqual(len(requests), 2)
            self.assertEqual(
                [request["max_tokens"] for request in requests],
                [2000, 4096],
            )
        finally:
            await server.close()

    async def test_tool_loop_retries_empty_output_with_larger_budget(self):
        requests = []

        async def handler(request):
            body = await request.json()
            requests.append(body)
            if len(requests) < 3:
                return web.json_response(
                    {
                        "choices": [
                            {
                                "finish_reason": "length",
                                "message": {
                                    "content": "",
                                    "reasoning_content": "hidden",
                                },
                            }
                        ]
                    }
                )
            return web.json_response(
                {"choices": [{"message": {"content": "final answer"}}]}
            )

        server, profile = await self._serve(handler)
        try:
            result = await self.client.bounded_tool_loop(
                profile,
                model="director",
                prompt="x",
                system_prompt="",
                tools={
                    "lookup": (
                        {
                            "type": "function",
                            "function": {
                                "name": "lookup",
                                "parameters": {"type": "object"},
                            },
                        },
                        lambda _args: {},
                    )
                },
            )
            self.assertEqual(result, "final answer")
            self.assertEqual(
                [request["max_tokens"] for request in requests],
                [2000, 4096, 8192],
            )
            self.assertTrue(
                all(
                    "reasoning_content" not in json.dumps(request)
                    for request in requests[1:]
                )
            )
        finally:
            await server.close()

    async def test_invalid_json_rate_limit_and_timeout_are_stable(self):
        async def invalid(_request):
            return web.Response(text="not-json")
        server, profile = await self._serve(invalid)
        try:
            with self.assertRaisesRegex(ProviderRegistryError, "无效 JSON"):
                await self.client.list_models(profile.id)
        finally:
            await server.close()

        async def limited(_request):
            return web.json_response({"error": {"message": "limited"}}, status=429)
        server, profile = await self._serve(limited)
        try:
            with self.assertRaisesRegex(ProviderRegistryError, "HTTP 429"):
                await self.client.chat(profile, model="director", prompt="x")
        finally:
            await server.close()

        async def slow(_request):
            await asyncio.sleep(0.2)
            return web.json_response({"choices": [{"message": {"content": "late"}}]})
        server, profile = await self._serve(slow)
        profile = ProviderProfile(**{**profile.__dict__, "timeout": 0.05})
        try:
            with self.assertRaisesRegex(ProviderRegistryError, "TimeoutError"):
                await self.client.chat(profile, model="director", prompt="x")
        finally:
            await server.close()


class NaturalAssetTests(unittest.TestCase):
    def test_asset_validation_and_public_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AssetStore(directory)
            asset = store.add(image_bytes())
            self.assertEqual(asset.media_type, "image/png")
            self.assertEqual((asset.width, asset.height), (32, 24))
            self.assertNotIn("path", asset.public())
            with self.assertRaises(AssetError):
                store.add(b"not-an-image")


class NaturalUiContractTests(unittest.TestCase):
    def test_all_natural_ui_references_are_bound_to_existing_elements(self):
        script = (APP_DIR / "static" / "natural.js").read_text(encoding="utf-8")
        html = (APP_DIR / "static" / "index.html").read_text(encoding="utf-8")
        binding_block = script.split("].map((id) => [id, byId(id)])", 1)[0]
        referenced = set(re.findall(r"naturalUi\.([A-Za-z][A-Za-z0-9_]*)", script))
        bound = set(re.findall(r'"([A-Za-z][A-Za-z0-9_]*)"', binding_block))
        html_ids = set(re.findall(r'id="([A-Za-z][A-Za-z0-9_]*)"', html))
        self.assertEqual(referenced - bound, set())
        self.assertEqual(referenced - html_ids, set())

    def test_natural_tab_controls_expose_and_update_selected_state(self):
        script = (APP_DIR / "static" / "natural.js").read_text(encoding="utf-8")
        html = (APP_DIR / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="naturalMobileTabs" class="natural-mobile-tabs glass" data-natural-ui role="tablist"', html)
        self.assertIn('role="tab" aria-selected="true" data-natural-view="compose"', html)
        self.assertIn('role="tab" aria-selected="true" data-job-type="text_to_image"', html)
        self.assertGreaterEqual(
            script.count('button.setAttribute("aria-selected", String(active))'),
            2,
        )

    def test_source_upload_defaults_image_edit_dimensions_to_source_size(self):
        script = (APP_DIR / "static" / "natural.js").read_text(encoding="utf-8")
        self.assertIn(
            "naturalUi.naturalWidth.value = String(fitDimension(image.naturalWidth))",
            script,
        )
        self.assertIn(
            "naturalUi.naturalHeight.value = String(fitDimension(image.naturalHeight))",
            script,
        )


class FakeNaturalComfy:
    base_url = "http://127.0.0.1:8188"

    def __init__(self):
        self.submissions = []
        self.uploads = []
        self.interrupted = False
        self.interrupted_prompt_id = None
        self.block = None
        self.submit_gate = None
        self.submit_started = asyncio.Event()
        classes = set()
        for path in (APP_DIR / "anima_natural" / "upstream" / "workflow").glob("*.json"):
            try:
                workflow = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            classes.update(
                str(node["class_type"])
                for node in workflow.values()
                if isinstance(node, dict) and node.get("class_type")
            )
        self.object_info_payload = {name: {} for name in classes}

    async def object_info(self):
        return dict(self.object_info_payload)

    async def lora_inventory(self):
        return {"items": [{"filename": "known.safetensors"}]}

    async def upload_image(self, path, *, filename, subfolder="", overwrite=True):
        self.uploads.append((str(path), filename, subfolder))
        return {"name": filename, "subfolder": subfolder, "type": "input"}

    async def submit(self, payload):
        self.submissions.append(payload)
        self.submit_started.set()
        if self.submit_gate is not None:
            await self.submit_gate.wait()
        return f"prompt-{len(self.submissions)}"

    async def wait_for_history(self, prompt_id, should_abort=None, missing_timeout=30.0):
        if self.block is not None:
            while not self.block.is_set():
                if should_abort and should_abort():
                    raise ComfyAborted("cancelled")
                await asyncio.sleep(0)
        if should_abort and should_abort():
            raise ComfyAborted("cancelled")
        return {
            "outputs": {
                node: {
                    "images": [
                        {
                            "filename": f"{prompt_id}.png",
                            "subfolder": "natural",
                            "type": "output",
                        }
                    ]
                }
                for node in ("26", "88", "458")
            }
        }

    async def interrupt(self, prompt_id=None):
        self.interrupted = True
        self.interrupted_prompt_id = prompt_id

    async def image_bytes(self, image):
        return image_bytes(), "image/png"

    async def close(self):
        return None


class NaturalEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = NaturalEngine(
            APP_DIR,
            Path(self.temp.name) / "natural",
            secret_store=MemorySecretStore(),
        )

    async def asyncTearDown(self):
        await self.engine.close()
        self.temp.cleanup()

    async def test_deterministic_plan_preserves_locked_native_pool_tags(self):
        plan = await self.engine.plan(
            {
                "job_type": "text_to_image",
                "text": "1girl, cinematic city at night",
                "locked_tags": ["standing", "red dress"],
                "use_llm": False,
                "pipeline": "base",
            }
        )
        self.assertEqual(plan["locked_tags"], ["standing", "red dress"])
        self.assertIn("standing", plan["positive_prompt"])
        self.assertIn("red dress", plan["positive_prompt"])

    async def test_all_generation_job_types_build_manifest_workflows(self):
        for job_type in (
            "text_to_image",
            "control",
            "img2img",
            "inpaint",
            "upscale",
            "character_swap",
        ):
            with self.subTest(job_type=job_type):
                payload = {
                    "job_type": job_type,
                    "text": "1girl, red dress, standing in a city",
                    "use_llm": False,
                    "pipeline": "base",
                    "control_modes": ["pose"],
                }
                if job_type == "upscale":
                    payload["text"] = ""
                plan = await self.engine.plan(payload)
                workflow, seed, outputs = self.engine.build_workflow(
                    payload,
                    plan,
                    {"source": "source.png", "mask": "mask.png"},
                )
                self.assertTrue(workflow)
                self.assertIsInstance(seed, int)
                self.assertTrue(outputs)
                self.assertNotIn(
                    "Lora Loader (LoraManager)",
                    {node.get("class_type") for node in workflow.values()},
                )

    def test_local_vision_adapter_uses_reasoning_safe_output_budget(self):
        self.assertEqual(
            self.engine.reverse_service._settings.reverse_prompt_max_tokens,
            4096,
        )

    async def test_image_edit_reverses_before_directing(self):
        asset = self.engine.assets.add(image_bytes())
        order = []

        class Reverse:
            async def reverse(_self, _context, _event, _path, _supplement="", **_kwargs):
                order.append("reverse")
                return ReversePromptResult(positive_tags="1girl, blue hair"), "vision"

        class Director:
            async def generate_instruction(_self, *_args, **_kwargs):
                order.append("director")
                return PictureInstruction(prompt="1girl, blue hair, red dress"), "director"

        self.engine.reverse_service = Reverse()
        self.engine.director = Director()
        plan = await self.engine.plan(
            {
                "job_type": "img2img",
                "asset_id": asset.id,
                "text": "change the dress to red",
                "use_llm": True,
                "pipeline": "base",
            }
        )
        self.assertEqual(order, ["reverse", "director"])
        self.assertEqual(plan["reverse"]["positive_tags"], "1girl, blue hair")

    async def test_native_planner_resolution_is_exposed_and_blocks_ambiguous_execution(self):
        confirmation = {
            "query": "soft",
            "kind": "preset",
            "status": "needs_confirmation",
            "needs_confirmation": True,
            "candidates": [{"id": "soft_a"}, {"id": "soft_b"}],
        }

        class Director:
            async def generate_instruction(_self, *_args, **_kwargs):
                return SimpleNamespace(
                    prompt="1girl, portrait",
                    negative_prompt="",
                    pipeline="",
                    character_queries=(),
                    artist_tags=("@anmi",),
                    loras=(),
                    style_preset_id="",
                    prompt_asset_ids=(),
                    prompt_plan_id="",
                    selected_preset={},
                    selected_prompt_assets=(),
                    selected_prompt_plan={},
                    matches=(confirmation,),
                    requires_confirmation=(confirmation,),
                    sources={"artist_tags": [{"kind": "artist", "id": "anmi"}]},
                ), "director"

        self.engine.director = Director()
        plan = await self.engine.plan(
            {
                "job_type": "text_to_image",
                "text": "soft portrait by anmi",
                "use_llm": True,
                "pipeline": "base",
            }
        )

        self.assertEqual(plan["requires_confirmation"], [confirmation])
        self.assertEqual(plan["sources"]["artist_tags"][0]["id"], "anmi")
        self.assertIn("@anmi", plan["positive_prompt"])
        with self.assertRaises(NaturalEngineError) as raised:
            self.engine.build_workflow({}, plan, {})
        self.assertEqual(raised.exception.code, "asset_confirmation_required")

    async def test_inpaint_uses_edit_protocol_and_preserves_directed_mode(self):
        asset = self.engine.assets.add(image_bytes())
        order = []

        class Reverse:
            async def reverse(_self, _context, _event, _path, _supplement="", **_kwargs):
                order.append("reverse")
                return ReversePromptResult(positive_tags="1girl, black coat"), "vision"

        class Director:
            async def generate_edit_instruction(_self, *_args, **_kwargs):
                order.append("edit")
                return EditInstruction(
                    prompt="1girl, red coat",
                    negative_prompt="black coat",
                    mode="lanpaint",
                ), "director"

            async def generate_instruction(_self, *_args, **_kwargs):
                self.fail("inpaint must not use the picture transport")

        self.engine.reverse_service = Reverse()
        self.engine.director = Director()
        plan = await self.engine.plan(
            {
                "job_type": "inpaint",
                "asset_id": asset.id,
                "text": "change the coat to red",
                "use_llm": True,
                "pipeline": "base",
            }
        )
        self.assertEqual(order, ["reverse", "edit"])
        self.assertEqual(plan["inpaint_mode"], "lanpaint")
        self.assertIn("red coat", plan["positive_prompt"])
        workflow, _seed, outputs = self.engine.build_workflow(
            {
                "job_type": "inpaint",
                "pipeline": "base",
                "steps": 1,
            },
            plan,
            {"source": "source.png", "mask": "mask.png"},
        )
        self.assertEqual(outputs, ["25"])
        self.assertEqual(
            workflow["25"]["inputs"]["filename_prefix"], "anima_studio/anima_lanpaint"
        )

    async def test_character_swap_requires_unique_observed_subject(self):
        asset = self.engine.assets.add(image_bytes())

        class Reverse:
            async def reverse(_self, _context, _event, _path, _supplement="", **_kwargs):
                return ReversePromptResult(
                    positive_tags="2girls, blonde hair, black hair",
                    characters=(
                        ReverseCharacter(name="", gender="girl", appearance_tags=("blonde hair",), position="left"),
                        ReverseCharacter(name="", gender="girl", appearance_tags=("black hair",), position="right"),
                    ),
                ), "vision"

        self.engine.reverse_service = Reverse()
        with self.assertRaises(NaturalEngineError) as captured:
            await self.engine.plan(
                {
                    "job_type": "character_swap",
                    "asset_id": asset.id,
                    "text": "replace with Hatsune Miku",
                    "use_llm": True,
                    "pipeline": "base",
                }
            )
        self.assertEqual(captured.exception.code, "source_selector_required")
        self.assertEqual(len(captured.exception.details["subjects"]), 2)

    async def test_character_swap_uses_strict_planner_and_removes_source_identity(self):
        asset = self.engine.assets.add(image_bytes())

        class Reverse:
            async def reverse(_self, _context, _event, _path, _supplement="", **_kwargs):
                return ReversePromptResult(
                    positive_tags=(
                        "1girl, solo, short hair, silver hair, blue eyes, black jacket, "
                        "white shirt, black tights, standing, full body, looking at viewer, "
                        "night, foggy, rim lighting"
                    ),
                    characters=(
                        ReverseCharacter(
                            name="",
                            gender="girl",
                            appearance_tags=("short hair", "silver hair", "blue eyes"),
                            outfit_tags=("black jacket", "white shirt", "black tights"),
                            action_tags=("standing", "looking at viewer"),
                            position="foreground",
                            confidence=0.9,
                        ),
                    ),
                ), "vision"

        async def semantic_target(_request):
            return (
                ("hatsune_miku",),
                {
                    "confidence": 1.0,
                    "index_verified": True,
                    "canonical_tag": "hatsune_miku",
                    "anchor_source": "danbooru_exact",
                    "match_variant": "canonical_exact",
                    "match_type": "canonical",
                    "candidate_count": 1,
                    "query_count": 1,
                },
                "director",
            )

        async def no_loras():
            return ()

        self.engine.reverse_service = Reverse()
        self.engine._semantic_target_tags = semantic_target
        self.engine._lora_records = no_loras
        self.engine.danbooru.lookup_many = lambda tags: tuple(
            SimpleNamespace(verified=True, category="general", canonical_tag=tag)
            for tag in tags
        )
        plan = await self.engine.plan(
            {
                "job_type": "character_swap",
                "asset_id": asset.id,
                "text": "把图中角色替换成初音未来，保留服装、姿势、构图、场景和光照",
                "use_llm": True,
                "pipeline": "base",
                "preview_only": True,
            }
        )
        self.assertIn("hatsune_miku", plan["positive_prompt"])
        self.assertNotIn("silver hair", plan["positive_prompt"])
        self.assertEqual(plan["character_swap"]["classifier"], "deterministic")
        self.assertTrue(plan["character_swap"]["forbid_character_loras"])

    async def test_manifest_contract_rejects_missing_binding_and_output(self):
        manifest = {
            "bindings": {"positive_prompt": {"node_id": "1", "input": "missing"}},
            "output_variants": {"base": {"preferred_node_ids": ["2"]}},
        }
        workflow = {"1": {"class_type": "Text", "inputs": {"text": "x"}}}
        result = self.engine._workflow_contract(manifest, workflow, {"Text": {}})
        self.assertTrue(any("缺少输入" in item for item in result["errors"]))
        self.assertTrue(any("输出变体" in item for item in result["errors"]))


class NaturalJobTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.engine = NaturalEngine(
            APP_DIR,
            root / "natural",
            secret_store=MemorySecretStore(),
        )
        self.history = HistoryStore(root / "history.sqlite3")
        self.comfy = FakeNaturalComfy()
        self.manager = NaturalJobManager(
            self.engine, self.comfy, self.history, asyncio.Lock()
        )

    async def asyncTearDown(self):
        await self.manager.close()
        await self.engine.close()
        await self.history.close()
        self.temp.cleanup()

    async def test_job_runs_through_shared_history_and_records_workspace(self):
        job = await self.manager.create(
            {
                "job_type": "text_to_image",
                "text": "1girl, portrait",
                "use_llm": False,
                "pipeline": "base",
                "count": 1,
            }
        )
        await self.manager.tasks[job["id"]]
        finished = self.manager.get(job["id"])
        self.assertEqual(finished["state"], "completed")
        history = await self.history.list_images()
        self.assertEqual(history["items"][0]["source_workspace"], "natural")
        self.assertEqual(history["items"][0]["job_type"], "text_to_image")

    async def test_cancel_interrupts_only_the_owned_prompt_id(self):
        self.comfy.block = asyncio.Event()
        job = await self.manager.create(
            {
                "job_type": "text_to_image",
                "text": "1girl, portrait",
                "use_llm": False,
                "pipeline": "base",
                "count": 1,
            }
        )
        for _ in range(100):
            if self.manager.get(job["id"]).get("prompt_id"):
                break
            await asyncio.sleep(0)

        cancelled = await self.manager.cancel(job["id"])
        await self.manager.tasks[job["id"]]

        self.assertIn(cancelled["state"], {"cancelling", "cancelled"})
        self.assertTrue(self.comfy.interrupted)
        self.assertEqual(self.comfy.interrupted_prompt_id, "prompt-1")
        self.assertEqual(self.manager.get(job["id"])["state"], "cancelled")

    async def test_cancel_during_submit_interrupts_the_returned_prompt_id(self):
        self.comfy.submit_gate = asyncio.Event()
        job = await self.manager.create(
            {
                "job_type": "text_to_image",
                "text": "1girl, portrait",
                "use_llm": False,
                "pipeline": "base",
                "count": 1,
            }
        )
        await asyncio.wait_for(self.comfy.submit_started.wait(), timeout=1)

        await self.manager.cancel(job["id"])
        self.assertFalse(self.comfy.interrupted)
        self.comfy.submit_gate.set()
        await self.manager.tasks[job["id"]]

        self.assertTrue(self.comfy.interrupted)
        self.assertEqual(self.comfy.interrupted_prompt_id, "prompt-1")
        self.assertEqual(self.manager.get(job["id"])["state"], "cancelled")

    async def test_job_rejects_missing_comfy_node_before_queue(self):
        self.comfy.object_info_payload.pop("UNETLoader", None)
        with self.assertRaisesRegex(NaturalEngineError, "UNETLoader"):
            await self.manager.create(
                {
                    "job_type": "text_to_image",
                    "text": "1girl, portrait",
                    "use_llm": False,
                    "pipeline": "base",
                    "count": 1,
                }
            )
        self.assertEqual(self.comfy.submissions, [])

    async def test_job_rejects_lora_missing_from_live_inventory(self):
        for lora in (
            {"name": "missing.safetensors", "strength": 0.8},
            {
                "filename": "styles/missing.safetensors",
                "enabled": True,
                "strength": 0.8,
                "role": "style",
                "order": 0,
            },
        ):
            with self.subTest(lora=lora):
                with self.assertRaisesRegex(NaturalEngineError, "LoRA"):
                    await self.manager.create(
                        {
                            "job_type": "text_to_image",
                            "text": "1girl, portrait",
                            "use_llm": False,
                            "pipeline": "base",
                            "loras": [lora],
                        }
                    )


class NaturalApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        app = create_app(
            app_dir=APP_DIR,
            comfy=FakeNaturalComfy(),
            history_path=root / "history.sqlite3",
            studio_path=root / "studio.sqlite3",
            custom_prompts_path=root / "custom_prompts.json",
            style_presets_path=root / "style_presets.json",
            anima_tools_dir=root / "missing-tools",
            natural_secret_store=MemorySecretStore(),
            natural_data_dir=root / "natural",
        )
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def test_provider_upload_plan_and_preview_job_endpoints(self):
        response = await self.client.post(
            "/api/natural/providers",
            json={
                "name": "Test",
                "base_url": "http://127.0.0.1:9000",
                "director_model": "director",
                "api_key": "secret",
            },
        )
        self.assertEqual(response.status, 201)
        provider = await response.json()
        self.assertNotIn("api_key", provider)

        form = __import__("aiohttp").FormData()
        form.add_field("file", image_bytes(), filename="source.png", content_type="image/png")
        upload = await self.client.post("/api/natural/uploads", data=form)
        self.assertEqual(upload.status, 201)
        asset = await upload.json()
        self.assertTrue(asset["id"].startswith("asset_"))

        payload = {
            "job_type": "text_to_image",
            "text": "1girl, city street",
            "use_llm": False,
            "pipeline": "base",
            "preview_only": True,
            "pool_settings": DEFAULT_SETTINGS,
        }
        plan_response = await self.client.post("/api/natural/plans", json=payload)
        self.assertEqual(plan_response.status, 201)
        plan = await plan_response.json()
        self.assertIn("positive_prompt", plan)

        job_response = await self.client.post("/api/natural/jobs", json=payload)
        self.assertEqual(job_response.status, 201)
        job = await job_response.json()
        self.assertEqual(job["state"], "completed")

        capabilities = await self.client.get("/api/natural/capabilities")
        self.assertEqual(capabilities.status, 200)
        self.assertIn("workflows", await capabilities.json())

        lora_profile = await self.client.post(
            "/api/natural/data/lora_profiles",
            json={"filename": "known.safetensors", "identity_tags": ["test_character"]},
        )
        self.assertEqual(lora_profile.status, 201)
        identity = await self.client.post(
            "/api/natural/data/identities",
            json={"name": "Test Character", "canonical_tag": "test_character"},
        )
        self.assertEqual(identity.status, 201)
        candidate = await self.client.post(
            "/api/natural/data/prompt_lab",
            json={"prompt": "1girl, portrait", "source_plan_id": plan["id"]},
        )
        self.assertEqual(candidate.status, 201)
        candidate_body = await candidate.json()
        confirmed = await self.client.post(
            f"/api/natural/prompt-lab/{candidate_body['id']}/confirm", json={}
        )
        self.assertEqual((await confirmed.json())["status"], "confirmed")

        logs = await self.client.get("/api/natural/logs")
        self.assertEqual(logs.status, 200)
        self.assertNotIn("secret", json.dumps(await logs.json()))

        timeline = await self.client.get(f"/api/natural/jobs/{job['id']}/timeline")
        self.assertEqual(timeline.status, 200)
        self.assertTrue((await timeline.json())["items"])
