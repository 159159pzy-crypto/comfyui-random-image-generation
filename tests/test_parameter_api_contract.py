"""HTTP settings preserve explicit parameters and select mode defaults."""

import asyncio
from unittest.mock import AsyncMock
from aiohttp.test_utils import TestClient, TestServer
import pytest
from anima_webui.server import MANAGER_KEY, create_app
from anima_webui.workflow import validate_settings
from test_server import FakeComfy, _write_fake_tools


@pytest.mark.parametrize("mapping", [None, {}])
def test_submission_keeps_explicit_parameters_and_mode_defaults(tmp_path, mapping):
    async def run():
        app = create_app(
            data_dir=tmp_path / "data",
            comfy=FakeComfy(),
            anima_tools_dir=_write_fake_tools(tmp_path),
            resource_paths=mapping,
        )
        async with TestClient(TestServer(app)) as client:
            manager = app[MANAGER_KEY]
            manager.start = AsyncMock(
                return_value={"id": "captured", "status": "queued"}
            )
            cases = [
                {"base_mode": "normal"},
                {"base_mode": "dual_sampler"},
                {
                    "base_mode": "dual_sampler",
                    "steps": 24,
                    "cfg": 2.5,
                    "sampler_name": "heun",
                    "dual": {"switch_step": 9, "second_cfg": 5},
                    "upscalers": {
                        "latent": {"scale_by": 1.7, "upscale_method": "bicubic"}
                    },
                },
                {
                    "base_mode": "normal",
                    "upscalers": {
                        "dlss": {"enabled": True, "style": "自然", "local_tone": 0.4}
                    },
                },
            ]
            for raw in cases:
                response = await client.post(
                    "/api/batches", json={**raw, "loras": [], "count": 1}
                )
                assert response.status == 201, await response.text()
                submitted = validate_settings(manager.start.call_args.args[0])
                assert submitted["pipeline_version"] == 4
                assert submitted["steps"] == raw.get(
                    "steps", 30 if raw["base_mode"] == "dual_sampler" else 20
                )
                assert submitted["cfg"] == raw.get("cfg", 8)
                assert submitted["sampler_name"] == raw.get("sampler_name", "euler")
                if "dual" in raw:
                    assert submitted["dual"]["switch_step"] == 9
                    assert submitted["dual"]["second_cfg"] == 5
                if raw.get("upscalers", {}).get("dlss"):
                    assert submitted["upscalers"]["dlss"]["style"] == "自然"
                    assert submitted["upscalers"]["dlss"]["local_tone"] == 0.4
                    assert not submitted["upscalers"]["nvidia"]["enabled"]
            manager.start.reset_mock()
            for removed in (
                {"workbench": "single"},
                {"model_profile": "krea2"},
                {"generation_mode": "img2img"},
                {"pipeline_version": 3},
            ):
                response = await client.post("/api/batches", json=removed)
                assert response.status == 400, await response.text()
            manager.start.assert_not_awaited()

    asyncio.run(run())
