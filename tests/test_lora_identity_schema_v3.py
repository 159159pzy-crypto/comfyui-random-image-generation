from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from anima_natural.engine import NaturalEngine
from anima_natural.workspace_data import NaturalDataError, NaturalWorkspaceData

SHA_A = "a" * 64
SHA_B = "b" * 64
SOURCE_A = "c" * 64
SOURCE_B = "d" * 64


def lookup(tag: str, category: str) -> dict[str, object]:
    return {
        "canonical_tag": tag,
        "category": category,
        "verified": True,
        "matched_by": "canonical_exact",
    }


class LoraIdentitySchemaV3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = NaturalWorkspaceData(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def profile(self, *, activation_terms: list[str] | None = None) -> dict:
        return self.data.upsert(
            "lora_profiles",
            {
                "id": "profile-a",
                "filename": "characters/team.safetensors",
                "display_name": "Team",
                "activation_terms": activation_terms or [],
                "sha256": SHA_A,
                "source_fingerprint": SOURCE_A,
                "file_status": "current",
            },
        )

    def bind(
        self,
        *,
        binding_id: str,
        character: str,
        terms: list[str] | None = None,
        copyright: str = "",
    ) -> dict:
        return self.data.upsert_verified_identity(
            {
                "id": binding_id,
                "name": character,
                "lora_profile_id": "profile-a",
                "character_canonical": character,
                "copyright_canonical": copyright,
                "activation_terms": terms or [],
            },
            character_lookup=lookup(character, "character"),
            copyright_lookup=(lookup(copyright, "copyright") if copyright else None),
            lora_detail={
                "filename": "characters/team.safetensors",
                "sha256": SHA_A,
                "source_fingerprint": SOURCE_A,
            },
        )

    def test_simplified_files_migrate_once_without_authorizing_identity(self) -> None:
        (self.root / "lora_profiles_v3.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "items": [
                        {
                            "id": "legacy-profile",
                            "filename": "legacy.safetensors",
                            "identity_tags": ["legacy_character"],
                            "updated_at": 123.0,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (self.root / "identity_bindings_v3.json").write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "id": "legacy-binding",
                            "name": "Legacy",
                            "canonical_tag": "legacy_character",
                            "lora_profile_ids": ["legacy-profile"],
                            "updated_at": 124.0,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        profile = self.data.list("lora_profiles")[0]
        binding = self.data.list("identities")[0]
        first_profile_bytes = (self.root / "lora_profiles_v3.json").read_bytes()
        first_binding_bytes = (self.root / "identity_bindings_v3.json").read_bytes()

        self.assertEqual(profile["activation_terms"], [])
        self.assertEqual(len(profile["semantic_fingerprint"]), 64)
        self.assertEqual(profile["file_status"], "unverified")
        self.assertEqual(binding["character_canonical"], "legacy_character")
        self.assertEqual(binding["verification_status"], "review_needed")
        self.assertIn("exact_verification_required", binding["invalid_reasons"])
        self.assertEqual(self.data.active_identity_bindings("legacy-profile"), [])

        self.data.list("lora_profiles")
        self.data.list("identities")
        self.assertEqual(
            (self.root / "lora_profiles_v3.json").read_bytes(),
            first_profile_bytes,
        )
        self.assertEqual(
            (self.root / "identity_bindings_v3.json").read_bytes(),
            first_binding_bytes,
        )

    def test_exact_verified_binding_is_bound_to_file_and_semantics(self) -> None:
        profile = self.profile(activation_terms=["team style"])
        binding = self.bind(
            binding_id="alice",
            character="alice_(wonderland)",
            copyright="wonderland",
            terms=["alice trigger"],
        )

        self.assertEqual(binding["verification_status"], "verified")
        self.assertEqual(binding["verified_sha256"], SHA_A)
        self.assertEqual(
            binding["verified_semantic_fingerprint"],
            profile["semantic_fingerprint"],
        )
        self.assertEqual(
            [item["id"] for item in self.data.active_identity_bindings("profile-a")],
            ["alice"],
        )

    def test_generic_exact_marker_cannot_authorize_canonical_binding(self) -> None:
        profile = self.profile()
        with self.assertRaisesRegex(NaturalDataError, "canonical exact match"):
            self.data.upsert_verified_identity(
                {
                    "id": "alice",
                    "name": "Alice",
                    "lora_profile_id": profile["id"],
                    "character_canonical": "alice_(wonderland)",
                },
                character_lookup={
                    **lookup("alice_(wonderland)", "character"),
                    "matched_by": "exact",
                },
                copyright_lookup=None,
                lora_detail={
                    "filename": profile["filename"],
                    "sha256": SHA_A,
                    "source_fingerprint": SOURCE_A,
                },
            )

    def test_alias_or_cross_work_binding_is_rejected(self) -> None:
        self.profile()
        with self.assertRaisesRegex(NaturalDataError, "canonical exact"):
            self.data.upsert_verified_identity(
                {
                    "name": "Alias",
                    "lora_profile_id": "profile-a",
                    "character_canonical": "alice",
                },
                character_lookup={
                    **lookup("alice_(wonderland)", "character"),
                    "matched_by": "alias_exact",
                },
                copyright_lookup=None,
                lora_detail={
                    "filename": "characters/team.safetensors",
                    "sha256": SHA_A,
                    "source_fingerprint": SOURCE_A,
                },
            )
        with self.assertRaisesRegex(NaturalDataError, "same Danbooru identity"):
            self.bind(
                binding_id="wrong-work",
                character="alice_(wonderland)",
                copyright="other_work",
                terms=["alice trigger"],
            )

    def test_semantic_or_file_change_invalidates_binding(self) -> None:
        profile = self.profile()
        self.bind(binding_id="alice", character="alice", terms=["alice trigger"])

        changed = self.data.upsert(
            "lora_profiles",
            {**profile, "activation_terms": ["new shared trigger"]},
            "profile-a",
        )
        self.assertNotEqual(
            changed["semantic_fingerprint"], profile["semantic_fingerprint"]
        )
        stale = self.data.get("identities", "alice")
        self.assertEqual(stale["verification_status"], "stale")
        self.assertIn("semantic_fingerprint_changed", stale["invalid_reasons"])
        self.assertEqual(self.data.active_identity_bindings("profile-a"), [])

        self.bind(binding_id="alice", character="alice", terms=["alice trigger"])
        self.data.reconcile_lora_profile(
            "profile-a",
            sha256=SHA_B,
            source_fingerprint=SOURCE_B,
            present=True,
        )
        stale = self.data.get("identities", "alice")
        self.assertIn("lora_sha256_changed", stale["invalid_reasons"])
        self.assertIn("source_fingerprint_changed", stale["invalid_reasons"])
        self.assertEqual(self.data.active_identity_bindings("profile-a"), [])

    def test_multi_character_lora_requires_exclusive_activation_terms(self) -> None:
        self.profile()
        self.bind(binding_id="alice", character="alice")
        with self.assertRaisesRegex(NaturalDataError, "dedicated activation_terms"):
            self.bind(binding_id="bob", character="bob", terms=["bob trigger"])

        self.bind(
            binding_id="alice",
            character="alice",
            terms=["alice trigger"],
        )
        with self.assertRaisesRegex(NaturalDataError, "exclusive"):
            self.bind(
                binding_id="bob",
                character="bob",
                terms=["alice trigger"],
            )
        self.bind(binding_id="bob", character="bob", terms=["bob trigger"])
        self.assertEqual(
            {item["id"] for item in self.data.active_identity_bindings("profile-a")},
            {"alice", "bob"},
        )

    def test_plain_upsert_cannot_spoof_verified_status(self) -> None:
        self.profile()
        item = self.data.upsert(
            "identities",
            {
                "name": "Spoofed",
                "character_canonical": "spoofed",
                "lora_profile_id": "profile-a",
                "verification_status": "verified",
                "verified_sha256": SHA_A,
                "verified_source_fingerprint": SOURCE_A,
                "verified_semantic_fingerprint": SHA_B,
            },
        )
        self.assertEqual(item["verification_status"], "review_needed")
        self.assertEqual(self.data.active_identity_bindings("profile-a"), [])

    def test_activation_terms_reject_nested_lora_syntax(self) -> None:
        with self.assertRaisesRegex(NaturalDataError, "LoRA syntax"):
            self.profile(activation_terms=["<lora:other:1>"])


class LoraIdentityRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_uses_only_current_file_bound_activation_terms(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data = NaturalWorkspaceData(directory)
            profile = data.upsert(
                "lora_profiles",
                {
                    "id": "profile-a",
                    "filename": "characters/alice.safetensors",
                    "activation_terms": ["shared trigger"],
                    "identity_tags": ["search only"],
                    "sha256": SHA_A,
                    "source_fingerprint": SOURCE_A,
                    "file_status": "current",
                },
            )
            data.upsert_verified_identity(
                {
                    "id": "alice",
                    "name": "Alice",
                    "lora_profile_id": profile["id"],
                    "character_canonical": "alice_(wonderland)",
                    "copyright_canonical": "wonderland",
                    "activation_terms": ["alice trigger"],
                },
                character_lookup=lookup("alice_(wonderland)", "character"),
                copyright_lookup=lookup("wonderland", "copyright"),
                lora_detail={
                    "filename": profile["filename"],
                    "sha256": SHA_A,
                    "source_fingerprint": SOURCE_A,
                },
            )
            data.upsert(
                "identities",
                {
                    "id": "spoofed",
                    "name": "Spoofed",
                    "lora_profile_id": profile["id"],
                    "character_canonical": "spoofed_character",
                    "activation_terms": ["spoofed trigger"],
                },
            )

            class Comfy:
                async def lora_inventory(self) -> dict:
                    return {
                        "items": [
                            {
                                "filename": profile["filename"],
                                "folder": "characters",
                            }
                        ]
                    }

            engine = NaturalEngine.__new__(NaturalEngine)
            engine.comfy = Comfy()
            engine.workspace_data = data
            engine.semantic_index = SimpleNamespace(sync_presence=lambda _: None)

            records = await engine._lora_records()

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].category, "character")
            self.assertEqual(records[0].sha256, SHA_A)
            self.assertEqual(records[0].source_fingerprint, SOURCE_A)
            self.assertEqual(records[0].source_work, "wonderland")
            self.assertIn("alice_(wonderland)", records[0].trigger_words)
            self.assertIn("shared trigger", records[0].trigger_words)
            self.assertIn("alice trigger", records[0].trigger_words)
            self.assertNotIn("spoofed_character", records[0].trigger_words)
            self.assertNotIn("spoofed trigger", records[0].trigger_words)


if __name__ == "__main__":
    unittest.main()
