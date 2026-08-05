from __future__ import annotations

import unittest

from anima_studio import (
    ArtistResolver,
    CompletionRequest,
    DomainValidationError,
    GenerationIntent,
    LoraResolver,
    LoraSelection,
    OpenAIProviderFacade,
    PresetResolver,
    ProviderMessage,
    StylePreset,
    ToolDefinition,
    WorkspaceDraft,
    merge_intent_layers,
    run_tool_loop,
)


class GenerationIntentTests(unittest.TestCase):
    def test_random_legacy_payload_is_normalized_to_one_schema(self) -> None:
        intent = GenerationIntent.from_mapping(
            {
                "count": 3,
                "model_name": "models/anima.safetensors",
                "full_prompt": "masterpiece, 1girl",
                "negative_prompt": "low quality",
                "manual_artist": "@anmi, by ask",
                "extra_prompt": "blue sky, smile",
                "loras": [
                    {
                        "filename": "styles/ink.safetensors",
                        "enabled": True,
                        "strength_model": 0.75,
                    }
                ],
                "width": 1024,
                "height": 1024,
                "steps": 20,
                "cfg": 5,
                "pools": {
                    "character": {"mode": "include", "ids": ["anime"]},
                },
                "fixed_character": "hatsune miku",
                "random_character_count": 2,
                "hires": {"enabled": True, "model_name": "4x.pth", "percent": 45},
                "detailers": {"face": True, "hand": False},
            }
        )

        self.assertEqual(intent.workspace, "random")
        self.assertEqual(intent.model, "models/anima.safetensors")
        self.assertEqual(intent.artist_tags, ("@anmi", "@ask"))
        self.assertEqual(intent.locked_tags, ("blue sky", "smile"))
        self.assertEqual(intent.loras[0].to_dict(), {
            "filename": "styles/ink.safetensors",
            "enabled": True,
            "strength": 0.75,
            "role": "style",
            "order": 0,
        })
        self.assertEqual(intent.sampling.count, 3)
        self.assertEqual(dict(intent.random_pools)["character"].fixed_tags, "hatsune miku")
        self.assertEqual(dict(intent.random_pools)["character"].count, 2)
        self.assertTrue(intent.repair.hires_enabled)
        self.assertEqual(intent.repair.detailers, ("face",))

    def test_legacy_random_pool_and_options_round_trip_without_loss(self) -> None:
        intent = GenerationIntent.from_legacy_random(
            {
                "pools": {"character": {"mode": "include", "ids": []}},
                "random_character": True,
                "random_character_count": 2,
                "female_count": 1,
                "quality_prompt": "masterpiece",
            }
        )

        character = dict(intent.random_pools)["character"]
        self.assertEqual(character.mode, "all")
        self.assertEqual(character.count, 2)
        self.assertEqual(intent.random_options["female_count"], 1)
        self.assertEqual(intent.random_options["quality_prompt"], "masterpiece")
        self.assertEqual(GenerationIntent.from_mapping(intent.to_dict()), intent)

    def test_natural_legacy_payload_accepts_name_and_strength(self) -> None:
        intent = GenerationIntent.from_legacy_natural(
            {
                "text": "draw a watercolor portrait",
                "positive_prompt": "1girl, watercolor",
                "loras": [{"name": "watercolor.safetensors", "strength": 0.6}],
                "pipeline": "anima",
                "width": 768,
            }
        )
        self.assertEqual(intent.workspace, "natural")
        self.assertEqual(intent.loras[0].filename, "watercolor.safetensors")
        self.assertEqual(intent.loras[0].strength, 0.6)
        self.assertEqual(intent.sampling.width, 768)

    def test_all_natural_job_types_have_stable_canonical_modes(self) -> None:
        expected = {
            "text_to_image": "text_to_image",
            "reverse": "reverse",
            "control": "control",
            "img2img": "image_to_image",
            "inpaint": "inpaint",
            "upscale": "upscale",
            "character_swap": "character_swap",
        }
        for job_type, canonical in expected.items():
            with self.subTest(job_type=job_type):
                intent = GenerationIntent.from_legacy_natural({"job_type": job_type})
                self.assertEqual(intent.mode, canonical)

    def test_sample_and_prompt_seeds_round_trip_independently(self) -> None:
        intent = GenerationIntent.from_mapping(
            {
                "workspace": "random",
                "sample_seed": 101,
                "prompt_seed": 202,
            }
        )
        self.assertEqual(intent.sampling.seed, 101)
        self.assertEqual(intent.sampling.prompt_seed, 202)
        restored = GenerationIntent.from_mapping(intent.to_dict())
        self.assertEqual(restored.sampling, intent.sampling)

    def test_round_trip_and_digest_are_stable(self) -> None:
        original = GenerationIntent.from_mapping(
            {
                "workspace": "natural",
                "positive_prompt": "portrait",
                "prompt_asset_ids": ["asset_portrait"],
                "prompt_plan_id": "plan_portrait",
                "loras": [
                    {
                        "filename": "a.safetensors",
                        "enabled": True,
                        "strength": 0.8,
                        "role": "character",
                        "order": 0,
                    }
                ],
            }
        )
        restored = GenerationIntent.from_mapping(original.to_dict())
        self.assertEqual(restored, original)
        self.assertEqual(restored.prompt_asset_ids, ("asset_portrait",))
        self.assertEqual(restored.prompt_plan_id, "plan_portrait")
        self.assertEqual(restored.digest, original.digest)
        self.assertEqual(original.revised(positive_prompt="new").revision, 2)

    def test_unsafe_and_duplicate_loras_are_rejected(self) -> None:
        with self.assertRaises(DomainValidationError):
            LoraSelection("../outside.safetensors")
        with self.assertRaisesRegex(DomainValidationError, "duplicate LoRA"):
            GenerationIntent.from_mapping(
                {
                    "workspace": "natural",
                    "loras": [
                        {"filename": "same.safetensors"},
                        {"filename": "SAME.safetensors"},
                    ],
                }
            )

    def test_input_control_and_repair_are_explicit_structures(self) -> None:
        intent = GenerationIntent.from_mapping(
            {
                "workspace": "natural",
                "mode": "image_to_image",
                "input_image": {"asset_id": "upload_one", "width": 512, "height": 512},
                "controls": [
                    {
                        "kind": "pose",
                        "asset_id": "control_one",
                        "strength": 0.8,
                        "start": 0.1,
                        "end": 0.9,
                    }
                ],
                "repair": {"hires_enabled": True, "upscale_percent": 40},
            }
        )
        self.assertEqual(intent.input_image.asset_id, "upload_one")
        self.assertEqual(intent.controls[0].kind, "pose")
        self.assertTrue(intent.repair.hires_enabled)

    def test_inpaint_mask_and_mode_round_trip(self) -> None:
        intent = GenerationIntent.from_mapping(
            {
                "workspace": "natural",
                "mode": "inpaint",
                "input_image": {"asset_id": "source_one"},
                "mask_image": {"asset_id": "mask_one"},
                "inpaint_mode": "lanpaint",
            }
        )

        self.assertEqual(intent.mask_image.asset_id, "mask_one")
        self.assertEqual(intent.inpaint_mode, "lanpaint")
        self.assertEqual(GenerationIntent.from_mapping(intent.to_dict()), intent)

    def test_legacy_mask_asset_id_is_preserved_and_implies_inpaint(self) -> None:
        intent = GenerationIntent.from_legacy_natural(
            {
                "input_image_path": "uploads/source.png",
                "mask_asset_id": "mask_legacy",
            }
        )

        self.assertEqual(intent.mode, "inpaint")
        self.assertEqual(intent.mask_image.asset_id, "mask_legacy")
        self.assertEqual(intent.inpaint_mode, "quick")

    def test_invalid_inpaint_mode_is_rejected(self) -> None:
        with self.assertRaisesRegex(DomainValidationError, "unsupported inpaint mode"):
            GenerationIntent.from_mapping(
                {"workspace": "natural", "mode": "inpaint", "inpaint_mode": "guess"}
            )


class DraftPresetAndPrecedenceTests(unittest.TestCase):
    def test_draft_revision_digest_and_workspace_integrity(self) -> None:
        draft = WorkspaceDraft.from_mapping(
            {
                "workspace": "natural",
                "revision": 4,
                "intent": {"positive_prompt": "saved prompt"},
                "workspace_state": {
                    "use_llm": False,
                    "source_selector": "left subject",
                    "panels": ["compose", "assets"],
                },
            }
        )
        restored = WorkspaceDraft.from_mapping(draft.to_dict())
        self.assertEqual(restored.revision, 4)
        self.assertEqual(restored.digest, draft.digest)
        self.assertEqual(restored.workspace_state["source_selector"], "left subject")
        with self.assertRaises(DomainValidationError):
            WorkspaceDraft("random", draft.intent)

    def test_workspace_state_rejects_non_json_and_excessive_depth(self) -> None:
        intent = GenerationIntent.from_mapping({"workspace": "natural"})
        with self.assertRaisesRegex(DomainValidationError, "non-JSON"):
            WorkspaceDraft("natural", intent, {"bad": object()})
        nested = value = {}
        for _ in range(10):
            value["next"] = {}
            value = value["next"]
        with self.assertRaisesRegex(DomainValidationError, "nesting depth"):
            WorkspaceDraft("natural", intent, nested)

    def test_legacy_style_preset_becomes_a_v7_intent_snapshot(self) -> None:
        preset = StylePreset.from_mapping(
            {
                "id": "preset_ink",
                "name": "Ink",
                "aliases": ["line art"],
                "settings": {
                    "model_name": "ink-model.safetensors",
                    "manual_artist": "@ask",
                    "loras": [{"filename": "ink.safetensors", "enabled": True, "strength": 0.7}],
                },
            }
        )
        self.assertEqual(preset.intent.model, "ink-model.safetensors")
        self.assertEqual(preset.intent.artist_tags, ("@ask",))
        self.assertEqual(StylePreset.from_mapping(preset.to_dict()), preset)

    def test_precedence_is_explicit_then_natural_then_preset_then_draft(self) -> None:
        merged = merge_intent_layers(
            {"workspace": "natural", "model": "default", "sampling": {"cfg": 3, "steps": 10}},
            {"model": "draft", "sampling": {"cfg": 4}},
            {"model": "preset", "sampling": {"cfg": 5}},
            {"model": "natural", "sampling": {"cfg": 6}},
            {"model": "explicit", "sampling": {"steps": 22}},
        )
        self.assertEqual(merged.workspace, "natural")
        self.assertEqual(merged.model, "explicit")
        self.assertEqual(merged.sampling.cfg, 6)
        self.assertEqual(merged.sampling.steps, 22)

    def test_preset_source_workspace_does_not_change_target_workspace(self) -> None:
        merged = merge_intent_layers(
            {"workspace": "natural", "model": "default"},
            preset={"workspace": "random", "model": "preset"},
        )
        self.assertEqual(merged.workspace, "natural")


class ResolverTests(unittest.TestCase):
    def test_artist_exact_and_alias_are_selected(self) -> None:
        resolver = ArtistResolver(
            [{"id": "artist_1", "name": "anmi", "aliases": ["安米"]}]
        )
        self.assertEqual(resolver.resolve("@anmi").selected.value, "@anmi")
        self.assertEqual(resolver.resolve("安米").selected.value, "@anmi")

    def test_fuzzy_artist_never_auto_selects(self) -> None:
        result = ArtistResolver(["anmi", "ask"]).resolve("anmi style")
        self.assertTrue(result.needs_confirmation)
        self.assertIsNone(result.selected)

    def test_ambiguous_alias_requires_confirmation(self) -> None:
        resolver = ArtistResolver(
            [
                {"id": "one", "name": "artist one", "aliases": ["shared"]},
                {"id": "two", "name": "artist two", "aliases": ["shared"]},
            ]
        )
        result = resolver.resolve("shared")
        self.assertTrue(result.needs_confirmation)
        self.assertEqual(len(result.candidates), 2)

    def test_lora_filename_without_extension_is_exact(self) -> None:
        result = LoraResolver(
            [{"filename": "styles/WaterColor.safetensors", "aliases": ["水彩"]}]
        ).resolve("styles/watercolor")
        self.assertTrue(result.matched)
        self.assertEqual(result.selected.value.filename, "styles/WaterColor.safetensors")

    def test_preset_resolver_uses_aliases(self) -> None:
        preset = StylePreset.from_mapping(
            {
                "id": "soft",
                "name": "Soft Light",
                "aliases": ["柔光"],
                "intent": {"workspace": "natural"},
            }
        )
        self.assertEqual(PresetResolver([preset]).resolve("柔光").selected.value, preset)


class ProviderAbstractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_openai_facade_builds_native_request(self) -> None:
        seen = []

        async def transport(endpoint, payload):
            seen.append((endpoint, payload))
            return {
                "choices": [
                    {"message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}
                ]
            }

        provider = OpenAIProviderFacade(transport)
        response = await provider.complete(
            CompletionRequest("director", (ProviderMessage("user", "draw"),))
        )
        self.assertEqual(response.message.content, "done")
        self.assertEqual(seen[0][0], "/chat/completions")
        self.assertEqual(seen[0][1]["messages"][0], {"role": "user", "content": "draw"})

    async def test_bounded_tool_loop_executes_only_registered_callback(self) -> None:
        calls = 0

        async def transport(_endpoint, payload):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {"name": "find_lora", "arguments": '{"query":"ink"}'},
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            self.assertEqual(payload["messages"][-1]["role"], "tool")
            return {
                "choices": [
                    {"message": {"role": "assistant", "content": "use ink"}, "finish_reason": "stop"}
                ]
            }

        provider = OpenAIProviderFacade(transport)
        request = CompletionRequest(
            "director",
            (ProviderMessage("user", "ink style"),),
            tools=(
                ToolDefinition(
                    "find_lora",
                    "Find exact local LoRAs",
                    {"type": "object", "properties": {"query": {"type": "string"}}},
                    lambda arguments: {"matches": [arguments["query"]]},
                ),
            ),
        )
        response = await run_tool_loop(provider, request)
        self.assertEqual(response.message.content, "use ink")
        self.assertEqual(calls, 2)

    async def test_tool_loop_normalizes_structured_content_and_second_round_messages(self) -> None:
        requests = []

        async def transport(_endpoint, payload):
            requests.append(payload)
            if len(requests) == 1:
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "find_lora",
                                            "arguments": {"query": "ink"},
                                        },
                                    }
                                ],
                            },
                            # Some compatible gateways emit stop despite tool_calls.
                            "finish_reason": "stop",
                        }
                    ]
                }
            self.assertEqual(requests[1]["messages"][-2]["content"], None)
            self.assertEqual(
                requests[1]["messages"][-1],
                {
                    "role": "tool",
                    "content": '{"matches":["ink"]}',
                    "tool_call_id": "call_1",
                },
            )
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": {"type": "output_text", "text": "use ink"},
                        },
                        "finish_reason": "stop",
                    }
                ]
            }

        request = CompletionRequest(
            "director",
            (ProviderMessage("user", "ink style"),),
            tools=(
                ToolDefinition(
                    "find_lora",
                    "Find exact local LoRAs",
                    {"type": "object", "properties": {"query": {"type": "string"}}},
                    lambda arguments: {"matches": [arguments["query"]]},
                ),
            ),
        )
        response = await run_tool_loop(OpenAIProviderFacade(transport), request)
        self.assertEqual(response.message.content, "use ink")
        self.assertEqual(len(requests), 2)

    async def test_unauthorized_tool_call_is_rejected(self) -> None:
        async def transport(_endpoint, _payload):
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "bad",
                                    "function": {"name": "delete_model", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            }

        request = CompletionRequest(
            "director",
            (ProviderMessage("user", "x"),),
            tools=(ToolDefinition("search", "search", {"type": "object"}, lambda _: {}),),
        )
        with self.assertRaisesRegex(RuntimeError, "unauthorized"):
            await run_tool_loop(OpenAIProviderFacade(transport), request)


if __name__ == "__main__":
    unittest.main()
