"""Malformed client data must fail before scheduling any generation."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from aiohttp.test_utils import TestClient, TestServer

from anima_webui.server import HISTORY_KEY, MANAGER_KEY, create_app
from anima_webui.workflow import validate_settings
from test_server import APP_DIR, FakeComfy, _write_fake_tools


@asynccontextmanager
async def studio(tmp_path):
    comfy = FakeComfy()
    app = create_app(
        app_dir=APP_DIR,
        data_dir=tmp_path / "data",
        comfy=comfy,
        anima_tools_dir=_write_fake_tools(tmp_path),
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield app, client, comfy
    finally:
        await client.close()


def settings():
    return validate_settings({"count": 1})


@pytest.mark.parametrize(
    "path",
    [
        ("base_mode",),
        ("character_detail",),
        *[
            ("pools", section, "mode")
            for section in ("character", "clothing", "pose", "background", "expression")
        ],
    ],
)
@pytest.mark.parametrize("bad", [[], {}])
def test_malformed_enum_types_return_400_without_starting_work(tmp_path, path, bad):
    async def run():
        async with studio(tmp_path) as (app, client, comfy):
            payload = settings()
            target = payload
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = bad
            response = await client.post("/api/batches", json=payload)
            assert response.status == 400, await response.text()
            assert (await response.json())["error"]
            assert comfy.classification_calls == []
            assert comfy.submissions == []
            assert app[MANAGER_KEY].snapshot() is None
            assert (
                app[HISTORY_KEY]
                .connection.execute("SELECT COUNT(*) FROM batches")
                .fetchone()[0]
                == 0
            )

    asyncio.run(run())


@pytest.mark.parametrize("bad", [[], {}])
def test_malformed_regeneration_mode_does_not_create_a_new_batch(tmp_path, bad):
    async def run():
        async with studio(tmp_path) as (app, client, comfy):
            await app[MANAGER_KEY].start(settings())
            await app[MANAGER_KEY].wait()
            original = app[MANAGER_KEY].snapshot()
            saved = (await app[HISTORY_KEY].list_images())["items"][0]
            response = await client.post(
                f"/api/history/{saved['id']}/regenerate", json={"mode": bad}
            )
            assert response.status == 400, await response.text()
            assert "mode" in (await response.json())["error"]
            assert app[MANAGER_KEY].snapshot() == original
            assert app[MANAGER_KEY].queue == []
            assert len(comfy.submissions) == 1

    asyncio.run(run())
