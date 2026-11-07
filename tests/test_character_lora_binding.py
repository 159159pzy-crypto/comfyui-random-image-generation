import asyncio
import copy
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from anima_webui.comfy import ComfyError
from anima_webui.history import HistoryStore
from anima_webui.lora_binding import (
    append_trigger_words, binding_policy, manual_settings, resolve_character_loras,
    validate_frozen_resources,
)
from anima_webui.runner import BatchManager
from anima_webui.workflow import DEFAULT_SETTINGS, WorkflowError, WorkflowTemplates
from test_runner import FakeComfy, FakeTemplates


def lora(name, strength=0.7, enabled=True, words=None, **extra):
    return {"filename": name, "strength": strength, "enabled": enabled,
            "trigger_words": words or [], **extra}


def snapshot(*characters, revision=1):
    return {"revision": revision, "bindings": {
        name: {"character_id": name, "name": name, "loras": items}
        for name, items in characters
    }}


def inventory(*names):
    return {"items": [{"filename": name, "name": name, "model_profile": "anima",
                       "classification_status": "recognized"} for name in names]}


def selected(*names):
    return {"character": [{"id": name, "title": name} for name in names]}


def settings(**extra):
    return {**copy.deepcopy(DEFAULT_SETTINGS), "count": 1, "loras": [],
            "random_character": True,
            "lora_managed_triggers": [], "extra_prompt": "", "quality_prompt": "",
            "manual_artist": "", **extra}


class BindingResolutionTests(unittest.TestCase):
    def test_manual_disabled_zero_and_first_character_win(self):
        manual = [{"filename": "OFF.safetensors", "enabled": False, "strength": 0.4},
                  {"filename": "zero.safetensors", "enabled": True, "strength": 0}]
        source = settings(loras=manual)
        before = copy.deepcopy(source)
        bindings = snapshot(("A", [lora("off.safetensors", words=["off word"]),
                                    lora("zero.safetensors", words=["zero word"]),
                                    lora("shared.safetensors", 0.25, words=["A_B", "same"])]),
                            ("B", [lora("shared.safetensors", 0.9, words=["wrong word"]),
                                    lora("other.safetensors", words=["SAME", "other word"])]))
        effective, runtime = resolve_character_loras(source, selected("A", "B"), bindings,
                                                      inventory("off.safetensors", "zero.safetensors", "shared.safetensors", "other.safetensors"))
        self.assertEqual(source, before)
        self.assertEqual(effective["loras"][:2], manual)
        self.assertEqual(effective["loras"][2]["strength"], 0.25)
        self.assertEqual(runtime["automatic_trigger_words"], ["A_B", "same", "other word"])
        self.assertEqual([item["reason"] for item in runtime["conflicts"]], ["manual_override", "manual_override", "duplicate"])
        self.assertEqual(runtime["applied"][0]["character_id"], "A")

    def test_missing_incompatible_changed_and_capacity_are_explicit(self):
        bindings = snapshot(("A", [lora("missing"), lora("unsupported"), lora("changed", sha256="old"), lora("valid")]))
        resources = inventory("unsupported", "changed", "valid")
        resources["items"][0]["model_profile"] = "unsupported"
        resources["items"][1]["sha256"] = "new"
        source = settings(loras=[{"filename": f"manual-{i}", "enabled": False, "strength": 1} for i in range(64)])
        effective, runtime = resolve_character_loras(source, selected("A"), bindings, resources)
        self.assertEqual(effective["loras"], source["loras"])
        self.assertEqual([item["reason"] for item in runtime["skipped"]],
                         ["missing", "incompatible", "changed", "capacity_exceeded"])
        self.assertEqual(runtime["automatic_trigger_words"], [])

    def test_trigger_words_reach_real_encode_and_png_without_duplicates(self):
        source = settings()
        effective, runtime = resolve_character_loras(source, selected("A"),
            snapshot(("A", [lora("a.safetensors", words=["Exact_Case", "portrait"])])), inventory("a.safetensors"))
        resolved = append_trigger_words({"composer_prompt": "portrait, ", "full_prompt": "quality, portrait, "}, runtime["automatic_trigger_words"])
        templates = WorkflowTemplates.load(Path(__file__).resolve().parents[1] / "templates")
        for mode in ("normal", "dual_sampler"):
            payload = templates.submission({**effective, "base_mode": mode}, 1, 2, "test", "test", 1,
                resolved_prompt=resolved["composer_prompt"], resolved_prompt_full=resolved["full_prompt"])
            self.assertEqual(payload["prompt"]["1010:995"]["inputs"]["text"], "quality, portrait, Exact_Case")
            self.assertEqual(payload["extra_data"]["extra_pnginfo"]["anima_random_webui"]["resolved_prompt_full"], "quality, portrait, Exact_Case")
            self.assertEqual(payload["prompt"]["72"]["inputs"]["loras"]["__value__"][0]["name"], "a.safetensors")

    def test_automatic_trigger_capacity_does_not_break_the_manual_stack(self):
        source = settings(lora_managed_triggers=[f"manual-{i}" for i in range(256)])
        effective, runtime = resolve_character_loras(source, selected("A"),
            snapshot(("A", [lora("new", words=["one new trigger"])])), inventory("new"))
        self.assertEqual(effective["loras"], [])
        self.assertEqual(effective["lora_managed_triggers"], source["lora_managed_triggers"])
        self.assertEqual(runtime["skipped"][0]["reason"], "trigger_capacity_exceeded")

    def test_frozen_never_skips_missing_and_old_history_stays_manual(self):
        source = settings(loras=[{"filename": "old", "enabled": True, "strength": 1}])
        self.assertEqual(manual_settings(source, {}), source)
        with self.assertRaisesRegex(WorkflowError, "历史 LoRA 不可用"):
            validate_frozen_resources(source, {}, inventory())
        self.assertEqual(binding_policy({"mode": "content_redraw", "redraw_sections": ["pose"]}), "preserve_character")
        self.assertEqual(binding_policy({"mode": "content_redraw", "redraw_sections": ["character"]}), "current")


class BindingCatalog:
    def validate_settings(self, settings):
        pass

    def resolve_prompt(self, settings, prompt_seed):
        name = ["A", "B", "C"][prompt_seed % 3]
        return {"composer_prompt": name, "full_prompt": name, "selected": selected(name)}


class BindingLibrary:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    async def binding_snapshot(self):
        self.calls += 1
        return self.value


class BindingComfy(FakeComfy):
    def __init__(self):
        super().__init__()
        self.available_loras = ["a.safetensors", "b.safetensors", "manual.safetensors"]
        self.classification_error = None

    async def resource_classifications(self, kind, refresh=False):
        if self.classification_error:
            raise self.classification_error
        return inventory(*self.available_loras)


class BindingRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "history.sqlite3"
        self.history = HistoryStore(self.path)
        self.comfy = BindingComfy()
        self.manager = BatchManager(FakeTemplates(), self.history, self.comfy, BindingCatalog())
        self.library = BindingLibrary(snapshot(("A", [lora("a.safetensors", words=["trigger A"])]),
                                              ("B", [lora("b.safetensors", words=["trigger B"])])))
        self.manager.lora_library = self.library

    async def asyncTearDown(self):
        if self.manager.task and not self.manager.task.done():
            self.manager.task.cancel()
            await asyncio.gather(self.manager.task, return_exceptions=True)
        await self.history.close()
        self.temp.cleanup()

    async def wait_for_submission(self, count):
        for _ in range(1000):
            if len(self.comfy.submissions) >= count:
                return
            await asyncio.sleep(0.001)
        self.fail("fake submission did not arrive")

    async def test_each_image_uses_its_own_stack_prompt_history_and_metadata(self):
        source = settings(count=3)
        with patch("anima_webui.runner.secrets.randbelow", side_effect=[0, 1, 2]):
            await self.manager.start(source, seeds={"sample_seed": 5})
            await self.manager.wait()
        self.assertEqual(self.manager.state["status"], "completed")
        self.assertEqual([payload["settings"]["loras"] for payload in self.comfy.submissions], [
            [{"filename": "a.safetensors", "enabled": True, "strength": 0.7}],
            [{"filename": "b.safetensors", "enabled": True, "strength": 0.7}], []])
        self.assertEqual([payload["resolved_prompt_full"] for payload in self.comfy.submissions], ["A, trigger A", "B, trigger B", "C"])
        self.assertEqual(source["loras"], [])
        images = list(reversed((await self.history.list_images())["items"]))
        for image, payload in zip(images, self.comfy.submissions):
            runtime = payload["extra_data"]["extra_pnginfo"]["anima_random_webui"]["lora_runtime"]
            self.assertEqual(image["lora_runtime"], runtime)
            self.assertEqual(image["settings"]["loras"], payload["settings"]["loras"])
            self.assertEqual(image["positive_prompt"], payload["resolved_prompt_full"])
            self.assertEqual(image["bound_resources"]["loras"], [item["filename"] for item in runtime["effective_loras"]])
        reopened = HistoryStore(self.path)
        try:
            self.assertEqual((await reopened.get_image(images[0]["id"]))["lora_runtime"], images[0]["lora_runtime"])
        finally:
            await reopened.close()

    async def test_queue_freezes_bindings_and_triggers_at_enqueue(self):
        self.comfy.block = asyncio.Event()
        await self.manager.start(settings(), seeds={"sample_seed": 1, "prompt_seed": 0})
        await self.wait_for_submission(1)
        queued = await self.manager.start(settings(), seeds={"sample_seed": 2, "prompt_seed": 0})
        self.assertEqual(queued["status"], "queued")
        self.library.value["bindings"]["A"]["loras"][0].update(strength=0.1, trigger_words=["changed trigger"])
        self.library.value["revision"] = 9
        self.comfy.block.set()
        await self.manager.wait()
        second = self.comfy.submissions[1]
        self.assertEqual(second["settings"]["loras"][0]["strength"], 0.7)
        self.assertEqual(second["resolved_prompt_full"], "A, trigger A")
        self.assertEqual(second["extra_data"]["extra_pnginfo"]["anima_random_webui"]["lora_runtime"]["binding_revision"], 1)

    async def test_frozen_replay_ignores_new_bindings_and_missing_resource_blocks(self):
        await self.manager.start(settings(), seeds={"sample_seed": 1, "prompt_seed": 0})
        await self.manager.wait()
        original = (await self.history.list_images())["items"][0]
        self.library.value = snapshot(("A", [lora("b.safetensors", words=["new trigger"])]), revision=8)
        regen = {"mode": "replay", "fixed_selection": original["resolved_selection"],
                 "frozen_positive_prompt": original["positive_prompt"], "lora_runtime": original["lora_runtime"]}
        await self.manager.start(original["settings"], seeds={"sample_seed": 1, "prompt_seed": 0}, regeneration=regen)
        await self.manager.wait()
        replay = self.comfy.submissions[-1]
        self.assertEqual(replay["settings"]["loras"], original["settings"]["loras"])
        self.assertEqual(replay["resolved_prompt_full"], original["positive_prompt"])
        self.assertEqual(self.library.calls, 1)
        self.comfy.available_loras.remove("a.safetensors")
        with self.assertRaisesRegex(WorkflowError, "LoRA 文件不存在"):
            await self.manager.start(original["settings"], regeneration=regen)

    async def test_reroll_replaces_only_old_automatic_stack_and_words(self):
        manual = [{"filename": "manual.safetensors", "enabled": True, "strength": 0.4}]
        await self.manager.start(settings(loras=manual, lora_managed_triggers=["manual word"]),
                                 seeds={"sample_seed": 1, "prompt_seed": 0})
        await self.manager.wait()
        original = (await self.history.list_images())["items"][0]
        await self.manager.start(original["settings"], seeds={"sample_seed": 1, "prompt_seed": 1},
                                 regeneration={"mode": "settings_reroll", "lora_runtime": original["lora_runtime"]})
        await self.manager.wait()
        payload = self.comfy.submissions[-1]
        self.assertEqual([item["filename"] for item in payload["settings"]["loras"]], ["manual.safetensors", "b.safetensors"])
        self.assertEqual(payload["settings"]["lora_managed_triggers"], ["manual word", "trigger B"])
        self.assertNotIn("trigger A", payload["resolved_prompt_full"])

    async def test_windows_replay_runtime_matches_native_paths_without_editing_original(self):
        self.comfy.available_loras = ["人物\\a.safetensors"]
        self.library.value = snapshot(("A", [lora("人物/a.safetensors", words=["trigger A"])]))
        await self.manager.start(settings(), seeds={"sample_seed": 1, "prompt_seed": 0})
        await self.manager.wait()
        original = (await self.history.list_images())["items"][0]
        self.assertEqual(original["lora_runtime"]["effective_loras"][0]["filename"], "人物/a.safetensors")
        await self.manager.start(original["settings"], seeds={"sample_seed": 1, "prompt_seed": 0}, regeneration={
            "mode": "replay", "fixed_selection": original["resolved_selection"],
            "frozen_positive_prompt": original["positive_prompt"], "lora_runtime": original["lora_runtime"]})
        await self.manager.wait()
        replay = (await self.history.list_images())["items"][0]
        self.assertEqual(replay["settings"]["loras"][0]["filename"], "人物\\a.safetensors")
        self.assertEqual(replay["lora_runtime"]["effective_loras"], replay["settings"]["loras"])
        self.assertEqual((await self.history.get_image(original["id"]))["lora_runtime"], original["lora_runtime"])

    async def test_preserved_character_uses_frozen_words_not_current_library(self):
        effective, runtime = resolve_character_loras(settings(), selected("A"), self.library.value, inventory("a.safetensors"))
        self.library.value["bindings"]["A"]["loras"][0]["trigger_words"] = ["new word"]
        regen = {"mode": "content_redraw", "redraw_sections": ["pose"], "lora_runtime": runtime}
        actual, resolved, saved = await self.manager._prepare_character_loras(effective,
            {"composer_prompt": "A, new pose", "full_prompt": "A, new pose", "selected": selected("A")}, None, regen)
        self.assertEqual(actual["loras"], effective["loras"])
        self.assertEqual(resolved["full_prompt"], "A, new pose, trigger A")
        self.assertEqual(saved, runtime)
        self.comfy.available_loras.remove("a.safetensors")
        with self.assertRaisesRegex(WorkflowError, "历史 LoRA 不可用"):
            await self.manager._prepare_character_loras(effective, resolved, None, regen)

    async def test_skip_warning_is_persisted_but_network_failure_is_not_a_skip(self):
        self.comfy.available_loras.remove("a.safetensors")
        await self.manager.start(settings(), seeds={"sample_seed": 1, "prompt_seed": 0})
        await self.manager.wait()
        record = (await self.history.list_images())["items"][0]
        self.assertEqual(record["settings"]["loras"], [])
        self.assertEqual(record["lora_runtime"]["skipped"][0]["reason"], "missing")
        self.assertEqual(self.manager.state["lora_warning_count"], 1)
        self.comfy.classification_error = ComfyError("plugin offline")
        await self.manager.start(settings(), seeds={"sample_seed": 1, "prompt_seed": 0})
        await self.manager.wait()
        self.assertEqual(self.manager.state["status"], "error")
        self.assertIn("plugin offline", self.manager.state["error"])
        self.assertEqual(len(self.comfy.submissions), 1)

    async def test_old_frozen_history_never_enables_current_binding(self):
        await self.manager.start(settings(), seeds={"sample_seed": 1, "prompt_seed": 0}, regeneration={
            "mode": "replay", "fixed_selection": selected("A"), "frozen_positive_prompt": "original A"})
        await self.manager.wait()
        self.assertEqual(self.library.calls, 0)
        self.assertEqual(self.comfy.submissions[0]["settings"]["loras"], [])
        self.assertEqual((await self.history.list_images())["items"][0]["lora_runtime"], {})


class BindingHistoryMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_runtime_column_is_backed_up_and_defaults_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.sqlite3"
            store = HistoryStore(path)
            await store.create_batch("legacy", 1, settings())
            image = await store.add_image(batch_id="legacy", sequence=1, prompt_id="p", image={"filename": "old.png"},
                positive_prompt="old", negative_prompt="", sample_seed=1, prompt_seed=2, settings=settings())
            await store.close()
            with closing(sqlite3.connect(path)) as db:
                db.execute("ALTER TABLE images DROP COLUMN lora_runtime_json")
                db.commit()
            reopened = HistoryStore(path)
            try:
                self.assertEqual((await reopened.get_image(image["id"]))["lora_runtime"], {})
                backup = Path(f"{path}.pre-lora-bindings.bak")
                self.assertTrue(backup.exists())
                with closing(sqlite3.connect(backup)) as db:
                    self.assertNotIn("lora_runtime_json", {row[1] for row in db.execute("PRAGMA table_info(images)")})
            finally:
                await reopened.close()
