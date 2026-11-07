"""Content redraw checks real candidate rules before queueing any work."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from aiohttp.test_utils import TestClient, TestServer

from anima_webui.server import CATALOG_KEY, HISTORY_KEY, MANAGER_KEY, create_app
from anima_webui.workflow import validate_settings
from test_server import FakeComfy, _write_fake_tools


@asynccontextmanager
async def studio(tmp_path):
    comfy = FakeComfy()
    app = create_app(
        data_dir=tmp_path / "data",
        comfy=comfy,
        anima_tools_dir=_write_fake_tools(tmp_path),
    )
    async with TestClient(TestServer(app)) as client:
        yield app, client, comfy


def pose_settings(selection, count=1):
    return validate_settings(
        {
            "count": 1,
            "random_pose": True,
            "random_pose_count": count,
            "quality_prompt": "",
            "female_count": 0,
            "pools": {"pose": selection},
        }
    )


async def record(app, settings, selected):
    catalog = app[CATALOG_KEY]
    snapshots = {}
    for section, item_ids in selected.items():
        snapshots[section] = []
        for item_id in item_ids:
            item = catalog.get(item_id)
            snapshot = catalog.public_item(item)
            snapshot.update(
                snapshot_version=2,
                prompt=", ".join(catalog.prompt_parts(item, section)),
            )
            snapshots[section].append(snapshot)
    positive = ", ".join(
        item["prompt"] for items in snapshots.values() for item in items
    )
    history = app[HISTORY_KEY]
    await history.create_batch("original", 1, settings)
    return await history.add_image(
        batch_id="original",
        sequence=1,
        prompt_id="original",
        image={"filename": "original.png"},
        settings=settings,
        positive_prompt=positive,
        negative_prompt="",
        sample_seed=123,
        prompt_seed=456,
        resolved_prompt=positive,
        resolved_selection=snapshots,
    )


async def assert_rejected_without_queue(app, client, comfy, image, sections, expected):
    manager = app[MANAGER_KEY]
    before = manager.snapshot()
    response = await client.post(
        f"/api/history/{image['id']}/regenerate",
        json={
            "mode": "content_redraw",
            "sections": sections,
            "count": 1,
        },
    )
    assert response.status == 400, await response.text()
    assert expected in (await response.json())["error"]
    assert manager.snapshot() == before
    assert manager.queue == []
    assert comfy.submissions == []
    assert (
        app[HISTORY_KEY]
        .connection.execute("SELECT COUNT(*) FROM batches")
        .fetchone()[0]
        == 1
    )


@pytest.mark.parametrize(
    "selection,count,error",
    [
        ({"mode": "include", "ids": ["pose:stand"]}, 1, "替代组合"),
        (
            {"mode": "include", "ids": ["pose:stand", "pose:stand", "pose:deleted"]},
            1,
            "替代组合",
        ),
        ({"mode": "all", "excluded_ids": ["pose:sit"]}, 1, "替代组合"),
        ({"mode": "all", "excluded_ids": ["pose:stand", "pose:sit"]}, 1, "随机池为空"),
        ({"mode": "include", "ids": ["pose:stand"]}, 2, "无法抽取"),
        ({"mode": "include", "ids": ["pose:stand", "pose:sit"]}, 2, "互不冲突"),
    ],
)
def test_unavailable_redraw_is_disabled_and_rejected_before_batch_creation(
    tmp_path, selection, count, error
):
    async def run():
        async with studio(tmp_path) as (app, client, comfy):
            image = await record(
                app, pose_settings(selection, count), {"pose": ["pose:stand"]}
            )
            response = await client.get(
                f"/api/history/{image['id']}/regeneration-options"
            )
            assert response.status == 200, await response.text()
            options = await response.json()
            assert not options["sections"]["pose"]["canRedraw"]
            assert error in options["sections"]["pose"]["redrawReason"]
            assert not options["modes"]["content_redraw"]["available"]
            assert error in str(options["modes"]["content_redraw"]["reasons"])
            assert options["modes"]["replay"]["available"]
            await assert_rejected_without_queue(
                app, client, comfy, image, ["pose"], error
            )

    asyncio.run(run())


@pytest.mark.parametrize(
    "selection",
    [
        {"mode": "include", "ids": ["pose:stand", "pose:sit"]},
        {"mode": "all", "excluded_ids": []},
        {
            "mode": "include",
            "ids": ["pose:stand", "pose:sit"],
            "excluded_ids": ["pose:sit"],
        },
    ],
)
def test_real_alternative_completes_with_different_selected_ids(tmp_path, selection):
    async def run():
        async with studio(tmp_path) as (app, client, comfy):
            image = await record(
                app, pose_settings(selection), {"pose": ["pose:stand"]}
            )
            options = await (
                await client.get(f"/api/history/{image['id']}/regeneration-options")
            ).json()
            assert options["sections"]["pose"]["canRedraw"]
            assert options["modes"]["content_redraw"]["available"]
            response = await client.post(
                f"/api/history/{image['id']}/regenerate",
                json={
                    "mode": "content_redraw",
                    "sections": ["pose"],
                    "count": 1,
                },
            )
            assert response.status == 201, await response.text()
            assert (await app[MANAGER_KEY].wait())["status"] == "completed"
            saved = (await app[HISTORY_KEY].list_images())["items"][0]
            assert [item["id"] for item in saved["resolved_selection"]["pose"]] == [
                "pose:sit"
            ]
            assert len(comfy.submissions) == 1

    asyncio.run(run())


def test_group_selection_is_rechecked_after_alternative_is_deleted(tmp_path):
    async def run():
        async with studio(tmp_path) as (app, client, comfy):
            group = await (
                await client.post(
                    "/api/custom-groups/expression", json={"name": "Only this group"}
                )
            ).json()
            group_id = group["group"]["id"]
            items = []
            for title in ("Calm", "Happy"):
                response = await client.post(
                    "/api/custom-prompts",
                    json={
                        "section": "expression",
                        "title": title,
                        "prompt": title.lower(),
                        "groupIds": [group_id],
                    },
                )
                assert response.status == 201, await response.text()
                items.append(await response.json())
            group_pool = await (
                await client.post(
                    "/api/pools/expression/ids", json={"custom_group": group_id}
                )
            ).json()
            assert set(group_pool["ids"]) == {item["id"] for item in items}
            settings = validate_settings(
                {
                    "count": 1,
                    "random_expression": True,
                    "quality_prompt": "",
                    "female_count": 0,
                    "pools": {
                        "expression": {"mode": "include", "ids": group_pool["ids"]}
                    },
                }
            )
            image = await record(app, settings, {"expression": [items[0]["id"]]})
            endpoint = f"/api/history/{image['id']}/regeneration-options"
            assert (await (await client.get(endpoint)).json())["sections"][
                "expression"
            ]["canRedraw"]
            removed = await client.delete(f"/api/custom-prompts/{items[1]['id']}")
            assert removed.status == 200, await removed.text()
            # Builtin expressions outside the saved group selection cannot be
            # substituted when its only remaining selected item is unchanged.
            await assert_rejected_without_queue(
                app, client, comfy, image, ["expression"], "替代组合"
            )
            assert not (await (await client.get(endpoint)).json())["sections"][
                "expression"
            ]["canRedraw"]

    asyncio.run(run())


def test_multi_section_redraw_keeps_existing_any_changed_rule(tmp_path):
    async def run():
        async with studio(tmp_path) as (app, client, _comfy):
            settings = pose_settings({"mode": "include", "ids": ["pose:stand"]})
            settings["random_expression"] = True
            ids = ["expression:gentle-smile", "expression:bright-smile"]
            settings["pools"]["expression"] = {
                "mode": "include",
                "ids": ids,
                "excluded_ids": [],
            }
            image = await record(
                app, settings, {"pose": ["pose:stand"], "expression": [ids[0]]}
            )
            options = await (
                await client.get(f"/api/history/{image['id']}/regeneration-options")
            ).json()
            assert not options["sections"]["pose"]["canRedraw"]
            assert options["sections"]["expression"]["canRedraw"]
            assert options["modes"]["content_redraw"]["available"]
            response = await client.post(
                f"/api/history/{image['id']}/regenerate",
                json={
                    "mode": "content_redraw",
                    "sections": ["pose", "expression"],
                    "count": 1,
                },
            )
            assert response.status == 201, await response.text()
            assert (await app[MANAGER_KEY].wait())["status"] == "completed"
            saved = (await app[HISTORY_KEY].list_images())["items"][0][
                "resolved_selection"
            ]
            assert [item["id"] for item in saved["pose"]] == ["pose:stand"]
            assert [item["id"] for item in saved["expression"]] == [ids[1]]

    asyncio.run(run())


def test_multi_item_draw_preserves_order_sensitive_redraw_semantics(tmp_path):
    async def run():
        async with studio(tmp_path) as (app, client, _comfy):
            created = await client.post(
                "/api/custom-prompts",
                json={"section": "clothing", "title": "Hat", "prompt": "blue hat"},
            )
            assert created.status == 201, await created.text()
            ids = ["clothing:coat", (await created.json())["id"]]
            settings = validate_settings(
                {
                    "count": 1,
                    "random_clothing": True,
                    "random_clothing_count": 2,
                    "quality_prompt": "",
                    "female_count": 0,
                    "pools": {"clothing": {"mode": "include", "ids": ids}},
                }
            )
            image = await record(app, settings, {"clothing": ids})
            options = await (
                await client.get(f"/api/history/{image['id']}/regeneration-options")
            ).json()
            assert options["sections"]["clothing"]["canRedraw"]
            response = await client.post(
                f"/api/history/{image['id']}/regenerate",
                json={
                    "mode": "content_redraw",
                    "sections": ["clothing"],
                    "count": 1,
                },
            )
            assert response.status == 201, await response.text()
            assert (await app[MANAGER_KEY].wait())["status"] == "completed"
            saved = (await app[HISTORY_KEY].list_images())["items"][0][
                "resolved_selection"
            ]
            assert [item["id"] for item in saved["clothing"]] == list(reversed(ids))

    asyncio.run(run())
