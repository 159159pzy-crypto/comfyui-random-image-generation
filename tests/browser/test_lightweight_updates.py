"""Opt-in real Chromium acceptance for the random-only upstream update.

Every server uses temporary data, synthetic catalogues and FakeComfy. Browser
requests outside the temporary server are blocked; these checks never use a GPU.
"""
from __future__ import annotations

import copy
import importlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest

from aiohttp.test_utils import TestServer
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_server import APP_DIR, FakeComfy, _write_fake_tools  # noqa: E402
from anima_webui.server import HISTORY_KEY, create_app  # noqa: E402
from anima_webui.workflow import DEFAULT_SETTINGS  # noqa: E402


class BrowserComfy(FakeComfy):
    async def object_info(self):
        return {}


@unittest.skipUnless(os.environ.get("ANIMA_BROWSER_TESTS") == "1", "opt in to Chromium acceptance")
class LightweightUpdateBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="anima-random-browser-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.comfy = BrowserComfy()
        self.app = create_app(app_dir=APP_DIR, comfy=self.comfy, data_dir=self.root / "data",
                              anima_tools_dir=_write_fake_tools(self.root))
        self.server = TestServer(self.app)
        await self.server.start_server()
        self.addAsyncCleanup(self.server.close)
        self.origin = str(self.server.make_url(""))
        api = importlib.import_module("playwright.async_api")
        self.expect = api.expect
        self.playwright = await api.async_playwright().start()
        self.addAsyncCleanup(self.playwright.stop)
        self.browser = await self.playwright.chromium.launch(channel=os.environ.get("ANIMA_BROWSER_CHANNEL", "chrome"))
        self.addAsyncCleanup(self.browser.close)
        self.context = await self.browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")
        self.addAsyncCleanup(self.context.close)
        self.requests = []
        self.errors = []

        async def local_only(route):
            self.requests.append((route.request.method, route.request.url))
            if route.request.url.startswith(self.origin + "/"):
                await route.continue_()
            else:
                await route.abort()

        await self.context.route("**/*", local_only)
        self.page = await self.context.new_page()
        self.page.set_default_timeout(10000)
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        await self.page.goto(self.origin)
        await self.page.wait_for_function("typeof initialized !== 'undefined' && initialized", timeout=10000)
        await self.expect(self.page.locator("#appError")).to_be_hidden()

    async def asyncTearDown(self):
        artifacts = APP_DIR / "output" / "playwright" / "lightweight-updates"
        artifacts.mkdir(parents=True, exist_ok=True)
        if hasattr(self, "page") and not self.page.is_closed():
            await self.page.screenshot(path=str(artifacts / f"{self._testMethodName}.png"))
        self.assertEqual(self.comfy.submissions, [])
        self.assertFalse(any(method == "POST" and url.endswith("/api/batches") for method, url in self.requests))
        self.assertEqual(self.errors, [])

    async def open_presets(self):
        if await self.page.locator(".preset-section").get_attribute("open") is None:
            await self.page.locator(".preset-section > summary").click()

    async def test_random_shell_new_defaults_and_mobile_filter_focus(self):
        self.assertEqual(await self.page.title(), "Anima Random WebUI")
        self.assertEqual(await self.page.evaluate("readSettings().pipeline_version"), 5)
        self.assertEqual(await self.page.locator("#workbenchSwitch, #singleWorkbenchControls, #kreaPoolDrawer").count(), 0)
        await self.page.set_viewport_size({"width": 390, "height": 844})
        await self.page.locator('[data-mobile-view="gallery"]').click()
        await self.page.locator("#openHistoryFilters").click()
        await self.expect(self.page.locator("#historyFilterDrawer")).to_be_visible()
        await self.page.locator("#historyFilterSearch").fill("中文筛选")
        await self.page.keyboard.press("Escape")
        await self.expect(self.page.locator("#historyFilterDrawer")).to_be_hidden()
        await self.expect(self.page.locator("#openHistoryFilters")).to_be_focused()
        self.assertLessEqual(await self.page.evaluate("document.documentElement.scrollWidth"), 390)

    async def test_old_v4_draft_keeps_version_dimensions_seed_and_inactive_repair(self):
        settings = copy.deepcopy(DEFAULT_SETTINGS)
        settings.update(pipeline_version=4, width=768, height=1280, steps=17, cfg=6)
        settings["upscalers"]["pixel_hires"].update(enabled=False, percent=37)
        settings["upscalers"]["post_sampler"].update(enabled=False, steps=27)
        payload = {"version": 1, "settings": settings, "profileDrafts": {}, "seedMode": "fixed", "seed": "765", "samplingModes": {}}
        await self.page.evaluate("value => { localStorage.setItem('anima-random-webui:draft:v1', JSON.stringify(value)); initialized = false; }", payload)
        await self.page.reload()
        await self.page.wait_for_function("initialized")
        saved = await self.page.evaluate("readSettings()")
        self.assertEqual((saved["pipeline_version"], saved["width"], saved["height"], saved["steps"], saved["cfg"]), (4, 768, 1280, 17, 6))
        self.assertEqual(saved["upscalers"]["pixel_hires"]["percent"], 37)
        self.assertEqual(saved["upscalers"]["post_sampler"]["steps"], 27)
        await self.expect(self.page.locator("#randomSampleSeed")).to_have_value("765")
        await self.page.evaluate("persistNow()")
        self.assertEqual(await self.page.evaluate("JSON.parse(localStorage.getItem('anima-random-webui:draft:v1')).settings.pipeline_version"), 4)

    async def test_preset_library_captures_only_supported_optional_groups(self):
        await self.open_presets()
        await self.page.locator("#savePreset").click()
        await self.expect(self.page.locator("#stylePresetEditor")).to_be_visible()
        await self.page.locator("#stylePresetName").fill("浏览器验收风格")
        await self.page.locator("#stylePresetSave").click()
        await self.expect(self.page.locator("#stylePresetEditor")).to_be_hidden()
        await self.expect(self.page.locator("#presetList")).to_contain_text("浏览器验收风格")
        response = await self.context.request.get(self.origin + "/api/style-presets?model_profile=anima")
        item = (await response.json())["items"][0]
        self.assertEqual(item["include"], {"size": False, "repair": False})
        await self.page.locator("#manageStylePresets").click()
        await self.expect(self.page.locator("#stylePresetManager")).to_be_visible()
        await self.expect(self.page.locator("#stylePresetManager")).to_contain_text("浏览器验收风格")
        self.assertNotIn("单图", await self.page.locator("#stylePresetManager").inner_text())

    async def test_character_classification_preserves_manual_roles_and_raw_text(self):
        await self.page.locator('[data-manage="character"]').click()
        await self.page.locator("#addCustom").click()
        await self.page.locator("#customTitle").fill("验收角色")
        raw = "alpha, blue hair, wearing unknown coat"
        await self.page.locator("#customPrompt").fill(raw)
        await self.page.locator("#customTitle").click()
        parts = self.page.locator("#customDialog [data-character-part]")
        await self.expect(parts).to_have_count(3)
        for index, role in enumerate(["trigger", "feature", "feature"]):
            await self.expect(parts.nth(index)).to_be_enabled()
            await parts.nth(index).select_option(role)
        await self.page.locator('#customForm button[type="submit"]').click()
        await self.expect(self.page.locator("#customDialog")).to_be_hidden()
        stored = json.loads((self.root / "data" / "custom_prompts.json").read_text("utf-8"))
        item = next(item for item in stored["items"] if item["title"] == "验收角色")
        self.assertEqual(item["prompt"], raw)
        self.assertEqual([part["role"] for part in item["character_parts"]], ["trigger", "feature", "feature"])

    async def test_history_search_and_cross_page_record_deletion(self):
        history = self.app[HISTORY_KEY]
        await history.create_batch("browser", 26, {"model_profile": "anima"})
        buffer = io.BytesIO()
        Image.new("RGB", (96, 128), "#91a5b8").save(buffer, format="PNG")
        await self.page.route(re.compile(r"/api/images/\d+(?:/thumbnail)?(?:\?|$)"), lambda route: route.fulfill(body=buffer.getvalue(), content_type="image/png"))
        for index in range(1, 27):
            await history.add_image(batch_id="browser", sequence=index, prompt_id=str(index), image={"filename": f"art-{index}.png"},
                                    positive_prompt="unique sunset" if index == 26 else "blue sky", negative_prompt="",
                                    sample_seed=index, prompt_seed=index, settings={"workbench": "random", "model_profile": "anima"})
        await self.page.evaluate("loadHistory(1)")
        await self.expect(self.page.locator("#gallery .image-card")).to_have_count(24)
        await self.page.locator("#openHistoryFilters").click()
        await self.page.locator("#historyFilterSearch").fill("unique sunset")
        await self.expect(self.page.locator("#gallery .image-card")).to_have_count(1)
        await self.page.locator("#historyFilterClear").click()
        await self.expect(self.page.locator("#gallery .image-card")).to_have_count(24)
        await self.page.locator("#closeHistoryFilters").click()
        await self.page.locator("#artworkSelect").click()
        await self.page.locator('#gallery [data-record-id="26"] .image-card-open').click()
        await self.page.locator("#nextPage").click()
        await self.page.locator('#gallery [data-record-id="1"] .image-card-open').click()
        await self.page.locator("#artworkBatchDelete").click()
        await self.expect(self.page.locator("#historyDeleteSummary")).to_contain_text("2")
        await self.page.locator("#historyDeleteRecord").click()
        await self.expect(self.page.locator("#historyDeleteResults")).to_contain_text("2")
        response = await self.context.request.get(self.origin + "/api/history")
        self.assertEqual((await response.json())["total"], 24)
