from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


class V7FrontendContractTests(unittest.TestCase):
    def runtime_sources(self) -> list[Path]:
        return [STATIC / "app.js", STATIC / "natural.js", *sorted((STATIC / "js").glob("*.js"))]

    def test_v7_pages_do_not_call_unversioned_or_legacy_apis(self) -> None:
        forbidden = ("/api/natural", "/api/batches", "/api/v6")
        violations = []
        for path in self.runtime_sources():
            source = path.read_text(encoding="utf-8")
            for endpoint in forbidden:
                if endpoint in source:
                    violations.append(f"{path.relative_to(ROOT)}: {endpoint}")
            for marker in ('"/api/', "'/api/", "`/api/"):
                start = 0
                while (index := source.find(marker, start)) >= 0:
                    if not source.startswith("v7", index + len(marker)):
                        violations.append(f"{path.relative_to(ROOT)}: unversioned API at {index}")
                    start = index + len(marker)
        self.assertEqual(violations, [])
        state_source = (STATIC / "js" / "v7-state.js").read_text(encoding="utf-8")
        self.assertIn("bootstrap?.events?.cursor", state_source)
        self.assertIn("?after=${encodeURIComponent(cursor)}", state_source)

    def test_natural_submit_uses_frozen_intent_and_confirmation_receipts(self) -> None:
        source = (STATIC / "js" / "natural-plan.js").read_text(encoding="utf-8")
        for contract in (
            "normalizePlanResolution",
            "hasUnresolvedConfirmations",
            "applyConfirmedResolution",
            "state.planIntent",
            "resolution_confirmations",
            'api("/api/v7/jobs"',
            "resolution.matches",
            "resolution.sources",
            "resolution.requiresConfirmation",
        ):
            self.assertIn(contract, source)
        self.assertNotIn('postV7("/jobs"', source)
        self.assertIn("请先确认所有歧义素材", source)

    def test_sse_event_families_refresh_both_workspaces(self) -> None:
        state_source = (STATIC / "js" / "v7-state.js").read_text(encoding="utf-8")
        for event_type in (
            "job.created", "job.queued", "job.dispatching", "job.started",
            "job.succeeded", "job.partial", "job.failed", "job.timed_out",
            "job.cancelled", "job.interrupted", "job.retried",
            "preset.created", "preset.updated", "preset.deleted",
            "history.created", "history.deleted", "draft.updated", "asset.changed",
        ):
            self.assertIn(f'"{event_type}"', state_source)
        self.assertIn("await this.loadAssets({ refresh: true })", state_source)
        self.assertLess(
            state_source.index("await this.loadAssets({ refresh: true })"),
            state_source.index('new CustomEvent("studio:assets-changed"'),
        )

        random_source = (STATIC / "app.js").read_text(encoding="utf-8")
        for event_name, callback in (
            ("studio:history-changed", "loadHistory"),
            ("studio:assets-changed", "loadLoraInventory"),
            ("studio:presets-changed", "loadStylePresets"),
        ):
            self.assertIn(f'window.addEventListener("{event_name}"', random_source)
            self.assertIn(callback, random_source)

    def test_server_drafts_restore_assets_and_clear_random_authoritatively(self) -> None:
        random_source = (STATIC / "app.js").read_text(encoding="utf-8")
        natural_source = (STATIC / "natural.js").read_text(encoding="utf-8")
        self.assertIn("await window.clearRandomServerDraft", random_source)
        self.assertIn('v7State.saveDraft("random"', natural_source)
        state_source = (STATIC / "js" / "v7-state.js").read_text(encoding="utf-8")
        self.assertIn("window.clearTimeout(this.saveTimers.get(workspace))", state_source)
        self.assertIn("this.saveTimers.delete(workspace)", state_source)
        self.assertIn("payload.source_asset || payload.input_image", natural_source)
        self.assertIn("payload.mask_asset || payload.mask_image", natural_source)
        self.assertIn("source_asset: state.sourceAsset", natural_source)
        self.assertIn("mask_asset: state.maskAsset", natural_source)

    def test_natural_diagnostics_reads_the_v7_runtime_contract(self) -> None:
        natural_source = (STATIC / "natural.js").read_text(encoding="utf-8")
        self.assertIn("const runtime = diagnostics.runtime || {};", natural_source)
        self.assertIn("runtime.comfy_online", natural_source)
        self.assertIn("runtime.workflows || []", natural_source)

    def test_url_workspace_is_applied_before_async_bootstrap(self) -> None:
        natural_source = (STATIC / "natural.js").read_text(encoding="utf-8")
        immediate = 'setWorkspace(initialWorkspace, { updateUrl: false, load: false });'
        self.assertIn(immediate, natural_source)
        initialize = natural_source.index("async function initializeV7()")
        self.assertLess(
            natural_source.index(immediate, initialize),
            natural_source.index("await v7State.init();", initialize),
        )

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for the JS contract test")
    def test_artwork_seed_reads_canonical_intent_sampling(self) -> None:
        script = r"""
import assert from "node:assert/strict";
import { recordPromptSeed, recordSeed } from "./static/js/artwork-viewer.js";

assert.equal(recordSeed({ sample_seed_text: "9007199254740993", sample_seed: 9007199254740992 }), "9007199254740993");
assert.equal(recordSeed({ sample_seed: 7, intent_json: { sampling: { seed: 8 } } }), 7);
assert.equal(recordSeed({ intent_json: { sampling: { seed: 8 } } }), 8);
assert.equal(recordSeed({ intent: { sampling: { seed: 9 } } }), 9);
assert.equal(recordSeed({ intent_json: { seed: 10 } }), 10);
assert.equal(recordPromptSeed({ prompt_seed_text: "9007199254740995", prompt_seed: 1 }), "9007199254740995");
"""
        completed = subprocess.run(
            [shutil.which("node") or "node", "--input-type=module", "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        viewer_source = (STATIC / "js" / "artwork-viewer.js").read_text(encoding="utf-8")
        self.assertIn("touchStart = null;", viewer_source)
        self.assertIn("if (!start || this.scale > 1", viewer_source)
        self.assertIn('classList.toggle("zoomed", this.scale > 1)', viewer_source)
        styles = (STATIC / "styles.css").read_text(encoding="utf-8")
        self.assertIn("touch-action: pan-y", styles)
        self.assertIn("figure.zoomed { touch-action: none; }", styles)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for the JS contract test")
    def test_task_center_terminal_message_follows_terminal_state(self) -> None:
        script = r"""
import assert from "node:assert/strict";
import { normalizeJob } from "./static/js/task-center.js";

const completed = normalizeJob({ status: "succeeded", message: "等待执行", source_workspace: "random" });
assert.equal(completed.state, "completed");
assert.equal(completed.message, "任务已完成");
const failed = normalizeJob({ status: "failed", message: "old", error_summary: "broken" });
assert.equal(failed.message, "broken");
"""
        completed = subprocess.run(
            [shutil.which("node") or "node", "--input-type=module", "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for the JS contract test")
    def test_lora_paths_use_one_v7_schema_across_inventory_and_drafts(self) -> None:
        script = r"""
import assert from "node:assert/strict";
import {
  canonicalLoraFilename,
  normalizeLoraInventory,
  normalizeLoras,
} from "./static/js/shared-controls.js";

assert.equal(canonicalLoraFilename("./人物\\hero.safetensors"), "人物/hero.safetensors");
const inventory = normalizeLoraInventory([
  { filename: "人物\\hero.safetensors", display_name: "Hero" },
  { name: "styles\\ink.safetensors" },
  { path: "details\\line.safetensors" },
  { filename: "人物/hero.safetensors", display_name: "duplicate" },
]);
assert.deepEqual(inventory.map((item) => item.filename), [
  "人物/hero.safetensors",
  "styles/ink.safetensors",
  "details/line.safetensors",
]);
assert.equal(inventory[0].display_name, "Hero");

const draft = normalizeLoras([
  { filename: "人物\\hero.safetensors", enabled: false, strength: 0.65, role: "character", order: 2 },
  { path: "styles\\ink.safetensors", enabled: true, strength: 0.9, role: "style", order: 0 },
  { filename: "STYLES/INK.safetensors", enabled: false, strength: 1.2, role: "detail", order: 3 },
]);
assert.deepEqual(draft, [
  { filename: "styles/ink.safetensors", enabled: true, strength: 0.9, role: "style", order: 0 },
  { filename: "人物/hero.safetensors", enabled: false, strength: 0.65, role: "character", order: 1 },
]);
"""
        completed = subprocess.run(
            [shutil.which("node") or "node", "--input-type=module", "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for the JS contract test")
    def test_random_lora_editor_uses_v7_role_and_order_schema(self) -> None:
        source = (STATIC / "app.js").read_text(encoding="utf-8")
        helper_source = source[
            source.index("function normalizeV7Loras") : source.index("function normalizeSettings")
        ]
        script = helper_source + r"""
import assert from "node:assert/strict";

const result = normalizeV7Loras([
  { name: "Style\\Ink.safetensors", strength: 0.7, role: "detail", order: 4 },
  { path: "characters\\hero.safetensors", enabled: false, strength: 0.8, role: "character", order: 1 },
  { filename: "style/ink.safetensors", role: "style", order: 5 },
]);
assert.deepEqual(result, [
  { filename: "characters/hero.safetensors", enabled: false, strength: 0.8, role: "character", order: 0 },
  { filename: "Style/Ink.safetensors", enabled: true, strength: 0.7, role: "detail", order: 1 },
]);
"""
        completed = subprocess.run(
            [shutil.which("node") or "node", "--input-type=module", "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        for contract in (
            'role.className = "lora-role"',
            '["style", "风格"]',
            '["character", "角色"]',
            'role.addEventListener("change"',
            "item.role = role.value",
        ):
            self.assertIn(contract, source)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required for the JS contract test")
    def test_ambiguous_lora_must_be_confirmed_before_it_is_applied(self) -> None:
        source = (STATIC / "js" / "natural-plan.js").read_text(encoding="utf-8")
        helper_source = source[
            source.index("const RESOLUTION_FIELDS") : source.index(
                "export function createNaturalPlanController"
            )
        ].replace("export function", "function")
        script = helper_source + r"""
import assert from "node:assert/strict";

const item = {
  kind: "lora",
  query: "ink",
  candidates: [{ id: "ink-one", name: "styles/ink.safetensors" }],
};
const resolution = normalizePlanResolution({
  requires_confirmation: [item],
  resolution: { matches: [item], sources: { loras: [{ id: "ink-one" }] } },
});
assert.equal(hasUnresolvedConfirmations(resolution.requiresConfirmation, {}), true);
const choices = {
  "lora:ink:0": { action: "select_candidate", candidate: item.candidates[0] },
};
assert.equal(hasUnresolvedConfirmations(resolution.requiresConfirmation, choices), false);
const result = applyConfirmedResolution(
  { workspace: "natural", loras: [] },
  resolution.requiresConfirmation,
  choices,
);
assert.equal(result.intent.loras[0].filename, "styles/ink.safetensors");
assert.equal(result.receipts[0].candidate_id, "ink-one");
"""
        completed = subprocess.run(
            [
                shutil.which("node") or "node",
                "--input-type=module",
                "-e",
                script,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)


if __name__ == "__main__":
    unittest.main()
