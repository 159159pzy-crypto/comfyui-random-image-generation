import unittest

from anima_webui.resource_classification import classify_base_model, classify_resources


class BaseModelClassificationTests(unittest.TestCase):
    def test_only_explicit_supported_architectures_are_recognized(self):
        for value, expected in (
            (" Anima ", "anima"), ("ANIMA", "anima"),
        ):
            with self.subTest(value=value):
                self.assertEqual(classify_base_model(value), (expected, "recognized", ""))
        for value in ("", None, " Unknown "):
            with self.subTest(value=value):
                profile, status, reason = classify_base_model(value)
                self.assertIsNone(profile)
                self.assertEqual(status, "unknown")
                self.assertTrue(reason)
        for value in ("Krea 2", "krea-2", "KREA_2_TURBO", "Krea2Turbo", "Pony", "Flux.1 Krea", "Krea", "AnimaMix", "Anima v1", "krea2-base", "Other"):
            with self.subTest(value=value):
                self.assertEqual(classify_base_model(value)[:2], (None, "unsupported"))


class ResourcePathClassificationTests(unittest.TestCase):
    def test_exact_root_relative_match_preserves_loader_filename(self):
        items = classify_resources(
            ["人物\\one.safetensors", "人物/ONE.safetensors"],
            [{"file_path": "f:\\MODELS\\LoRAs\\人物\\one.safetensors", "base_model": "Krea 2",
              "model_name": "One", "file_size": 123, "preview_url": "/preview/one",
              "civitai": {"trainedWords": [" @One ", "@one", None, "", "second"]}}],
            ["F:/models/loras", "f:\\models\\loras\\"],
        )
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["name"], "人物/one.safetensors")
        self.assertEqual(item["filename"], "人物\\one.safetensors")
        self.assertEqual(item["folder"], "人物")
        self.assertIsNone(item["model_profile"])
        self.assertEqual(item["classification_status"], "unsupported")
        self.assertEqual(item["trigger_words"], ["@One", "second"])
        self.assertEqual(item["metadata_source"], "lora-manager")
        self.assertEqual(item["size"], 123)

    def test_names_and_folders_cannot_override_plugin_classification(self):
        items = classify_resources(
            ["Anima/anima.safetensors", "krea2.safetensors", "missing_anima.safetensors"],
            [{"file_path": "F:/loras/Anima/anima.safetensors", "base_model": "Pony"},
             {"file_path": "F:/loras/krea2.safetensors", "base_model": "Unknown"}],
            ["F:/loras"],
        )
        self.assertEqual([item["classification_status"] for item in items], ["unsupported", "unknown", "unindexed"])
        self.assertTrue(all(item["model_profile"] is None for item in items))

    def test_same_basename_does_not_match_different_relative_paths(self):
        items = classify_resources(
            ["one.safetensors", "B/one.safetensors"],
            [{"file_path": "F:/loras/A/one.safetensors", "folder": "B", "base_model": "Anima"},
             {"file_path": "F:/loras-other/B/one.safetensors", "base_model": "Anima"}],
            ["F:/loras"],
        )
        self.assertEqual([item["classification_status"] for item in items], ["unindexed", "unindexed"])

    def test_overlapping_roots_preserve_all_loader_aliases_and_deduplicate_files(self):
        items = classify_resources(
            ["one.safetensors", "nested/one.safetensors"],
            [{"file_path": "F:/loras/nested/one.safetensors", "base_model": "Anima"},
             {"file_path": "f:\\loras\\nested\\ONE.safetensors", "base_model": "Anima"}],
            ["F:/loras", "F:/loras/nested"],
        )
        self.assertEqual([item["classification_status"] for item in items], ["recognized", "recognized"])

    def test_distinct_files_at_same_relative_path_are_ambiguous_even_same_family(self):
        item = classify_resources(
            ["one.safetensors"],
            [{"file_path": "F:/loras/one.safetensors", "base_model": "Anima", "civitai": {"trainedWords": ["wrong"]}},
             {"file_path": "G:/loras/one.safetensors", "base_model": "Anima"}],
            ["F:/loras", "G:/loras"],
        )[0]
        self.assertEqual(item["classification_status"], "ambiguous")
        self.assertIsNone(item["model_profile"])
        self.assertFalse(item["trigger_metadata_available"])
        self.assertEqual(item["trigger_words"], [])

    def test_checkpoint_row_cannot_match_unet_inventory(self):
        item = classify_resources(
            ["one.safetensors"],
            [{"file_path": "F:/models/checkpoints/one.safetensors", "base_model": "Anima"},
             {"file_path": "F:/models/diffusion_models/one.safetensors", "base_model": "Krea 2"}],
            ["F:/models/diffusion_models", "F:/models/unet"],
        )[0]
        self.assertIsNone(item["model_profile"])
        self.assertEqual(item["classification_status"], "unsupported")

    def test_unc_and_posix_roots(self):
        for root, path in (("\\\\server\\share\\loras", "//SERVER/share/loras/one.safetensors"),
                           ("/models/loras", "/models/loras/one.safetensors")):
            with self.subTest(root=root):
                item = classify_resources(["one.safetensors"], [{"file_path": path, "base_model": "Anima"}], [root])[0]
                self.assertEqual(item["model_profile"], "anima")

    def test_posix_case_differences_are_not_collapsed(self):
        items = classify_resources(
            ["One.safetensors", "one.safetensors"],
            [{"file_path": "/models/One.safetensors", "base_model": "Anima"},
             {"file_path": "/models/one.safetensors", "base_model": "Krea 2"}], ["/models"],
        )
        self.assertEqual([item["model_profile"] for item in items], ["anima", None])

    def test_malformed_metadata_paths_and_roots_are_rejected(self):
        for path in ("relative/one.safetensors", "F:one.safetensors", "F:/loras/../one.safetensors", None, []):
            with self.subTest(path=path), self.assertRaises(ValueError):
                classify_resources(["one.safetensors"], [{"file_path": path}], ["F:/loras"])
        for root in ("relative", "F:", None):
            with self.subTest(root=root), self.assertRaises(ValueError):
                classify_resources([], [], [root])
        with self.assertRaises(ValueError):
            classify_resources([], [{"file_path": "F:/loras/one.safetensors", "base_model": ["Anima"]}], ["F:/loras"])

    def test_empty_index_leaves_loader_choices_unindexed(self):
        item = classify_resources(["one.safetensors"], [], ["F:/loras"])[0]
        self.assertEqual(item["classification_status"], "unindexed")
        self.assertIsNone(item["model_profile"])


if __name__ == "__main__":
    unittest.main()
