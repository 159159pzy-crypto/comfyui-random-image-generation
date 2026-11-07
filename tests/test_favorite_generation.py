"""Saved text must reach the matching random-workbench prompt resolver."""
import tempfile
import unittest
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer

from anima_webui.server import MANAGER_KEY, create_app
from anima_webui.workflow import validate_settings
from test_server import APP_DIR, FakeComfy, _write_fake_tools


class FavoriteGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.app = create_app(
            app_dir=APP_DIR,
            data_dir=root / "data",
            anima_tools_dir=_write_fake_tools(root),
            comfy=FakeComfy(),
        )
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def post(self, path, payload):
        response = await self.client.post(path, json=payload)
        result = await response.json()
        self.assertLess(response.status, 300, result)
        return result

    async def test_anima_saved_text_is_a_selectable_prompt_in_every_category(self):
        for section in ("character", "clothing", "pose", "background", "expression"):
            with self.subTest(section=section):
                text = f"unique {section} favorite"
                saved = await self.post(
                    f"/api/favorites/{section}/text?model_profile=anima",
                    {"title": f"Favorite {section}", "text": text, "groupIds": ["default"]},
                )
                response = await self.client.get(
                    f"/api/pools/{section}?model_profile=anima&collection=__all__"
                )
                listed = await response.json()
                self.assertEqual(response.status, 200, listed)
                self.assertIn(saved["item_id"], [item["id"] for item in listed["items"]])
                settings = validate_settings({
                    "model_profile": "anima", "workbench": "random",
                    **{f"random_{name}": False for name in ("character", "clothing", "pose", "background", "expression")},
                    f"random_{section}": True, f"random_{section}_count": 1,
                    "pools": {section: {"mode": "include", "ids": [saved["item_id"]]}},
                    "female_count": 0, "male_count": 0, "manual_artist": "",
                    "quality_prompt": "", "extra_prompt": "",
                })
                resolved = self.app[MANAGER_KEY]._resolve_prompt(settings, 123)
                self.assertIn(text, resolved["full_prompt"])
                self.assertEqual(resolved["selected"][section][0]["id"], saved["item_id"])
