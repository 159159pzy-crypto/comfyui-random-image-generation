from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.check_v7_native import violations


class V7NativeGateTests(unittest.TestCase):
    def test_clean_native_runtime_and_frontend_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "anima_studio").mkdir()
            (root / "anima_studio" / "domain.py").write_text(
                "class GenerationIntent: pass\n", encoding="utf-8"
            )
            (root / "static" / "js").mkdir(parents=True)
            (root / "static" / "js" / "api.js").write_text(
                'fetch("/api/v7/jobs")\n', encoding="utf-8"
            )
            self.assertEqual(violations(root), [])

    def test_runtime_and_all_legacy_frontend_routes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "anima_webui").mkdir()
            (root / "anima_webui" / "bad.py").write_text(
                "from anima_natural.upstream.services import x\n"
                "event = BrowserEvent()\n"
                "name = 'astrbot-output'\n",
                encoding="utf-8",
            )
            (root / "static").mkdir()
            (root / "static" / "bad.js").write_text(
                'fetch("/api/natural/jobs");\n'
                'fetch("/api/batches");\n'
                'fetch("/api/v6/studio");\n',
                encoding="utf-8",
            )
            failures = violations(root)
            self.assertTrue(any("upstream_import" in item for item in failures))
            self.assertTrue(any("transport_compatibility" in item for item in failures))
            self.assertTrue(any("astrbot_runtime" in item for item in failures))
            self.assertTrue(any("legacy_natural_api" in item for item in failures))
            self.assertTrue(any("legacy_batches_api" in item for item in failures))
            self.assertTrue(any("legacy_v6_api" in item for item in failures))

    def test_authorization_snapshot_and_migration_fixture_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "anima_natural" / "upstream").mkdir(parents=True)
            (root / "anima_natural" / "upstream" / "plugin.py").write_text(
                "import astrbot\n", encoding="utf-8"
            )
            (root / "anima_webui").mkdir()
            (root / "anima_webui" / "migrations.py").write_text(
                'SOURCE = "astrbot historical fixture"\n', encoding="utf-8"
            )
            self.assertEqual(violations(root), [])


if __name__ == "__main__":
    unittest.main()
