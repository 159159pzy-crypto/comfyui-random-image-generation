from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "static" / "index.html"
STYLES = ROOT / "static" / "styles.css"
SCRIPT = ROOT / "static" / "js" / "studio-admin.js"


class V7StudioFrontendContractTests(unittest.TestCase):
    def test_admin_module_is_loaded_and_all_controls_exist(self) -> None:
        html = HTML.read_text(encoding="utf-8")
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('type="module" src="/static/js/studio-admin.js', html)
        referenced = set(re.findall(r'"(studio[A-Z][A-Za-z0-9]+)"', script))
        html_ids = set(re.findall(r'id="([A-Za-z][A-Za-z0-9]+)"', html))
        self.assertEqual(referenced - html_ids, set())

    def test_every_required_studio_domain_has_an_api_entry(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        endpoints = (
            "/prompt-assets/import",
            "/prompt-assets/update",
            "/prompt-lab/candidates",
            "/prompt-plans",
            "/loras/refresh",
            "/loras/detail",
            "/loras/visuals",
            "/loras/analyze",
            "/loras/archive",
            "/loras/download",
            "/danbooru/search",
            "/danbooru/build",
            "/danbooru/schedule",
            "/danbooru/schedule/run",
            "/models/refresh",
            "/models/quarantine",
            "/workflows",
            "/config-profiles",
            "/logs?limit=500",
            "/logs/level",
            "/diagnostics",
            "/operations/",
        )
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                self.assertIn(endpoint, source)

    def test_dangerous_operations_require_manual_confirmation(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("window.confirm(message)", source)
        self.assertIn("confirm_manual: true", source)
        for endpoint in (
            "/prompt-assets/update",
            "/loras/refresh",
            "/loras/detail",
            "/loras/analyze",
            "/loras/archive",
            "/loras/download",
            "/danbooru/build",
            "/danbooru/schedule",
            "/danbooru/schedule/run",
            "/models/refresh",
            "/models/quarantine",
        ):
            with self.subTest(endpoint=endpoint):
                pattern = rf"(?:manual|loraOperation)\([^\n]+{re.escape(endpoint)}"
                self.assertRegex(source, pattern)

    def test_unavailable_capabilities_are_not_reported_as_success(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("[404, 501, 503]", source)
        self.assertIn("当前服务不可用", source)
        self.assertIn("Prompt Plan 管理不可用", source)
        self.assertIn('item.status === "rejected"', source)

    def test_admin_layout_has_mobile_overflow_guards(self) -> None:
        styles = STYLES.read_text(encoding="utf-8")
        for contract in (
            ".studio-admin { grid-column: 1 / -1; min-width: 0;",
            ".studio-toolbar { min-width: 0; display: flex; flex-wrap: wrap;",
            ".studio-row { min-width: 0;",
            ".studio-select-list { max-height: 280px; overflow-y: auto;",
            ".studio-toolbar > input, .studio-toolbar > select, .studio-toolbar > button { flex: 1 1 100%; width: 100%; }",
        ):
            self.assertIn(contract, styles)

    def test_schema_v3_identity_editor_exposes_exact_binding_fields(self) -> None:
        html = HTML.read_text(encoding="utf-8")
        natural = (ROOT / "static" / "natural.js").read_text(encoding="utf-8")
        studio = SCRIPT.read_text(encoding="utf-8")
        for control_id in (
            "naturalLoraActivationTerms",
            "naturalIdentityProfile",
            "naturalIdentityCanonical",
            "naturalIdentityCopyright",
            "naturalIdentityActivationTerms",
        ):
            self.assertIn(f'id="{control_id}"', html)
            self.assertIn(f'"{control_id}"', natural)
        for field in (
            "character_canonical",
            "copyright_canonical",
            "activation_terms",
            "lora_profile_id",
            "verification_status",
        ):
            self.assertIn(field, natural)
        self.assertIn("/lora-profiles", studio)
        self.assertIn("/identities", studio)
        self.assertIn('binding.verification_status === "verified"', studio)
        self.assertIn("editingLoraProfileId", natural)
        self.assertIn("editingLoraFilename", natural)
        self.assertIn("confirm_manual: true", natural)


if __name__ == "__main__":
    unittest.main()
