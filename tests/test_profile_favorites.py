"""Text favorites preserve source identity without sharing another project's data."""
import asyncio
import copy
from unittest.mock import patch

import pytest

from anima_webui.catalog import CatalogError
from anima_webui.favorites import FavoritesStore
from anima_webui.pool_lifecycle import PoolLifecycleError
from test_pool_lifecycle import make_world, save


def test_text_reuse_preserves_source_and_merges_local_groups(tmp_path):
    async def run():
        world = make_world(tmp_path)
        first = await save(world)
        favorite = world.favorites["anima"]
        group = (await favorite.create_group("pose", {"name": "More"}))["group"]["id"]
        second = await world.lifecycle.save_text("anima", "pose", {"title": "Other title", "text": "old text", "groupIds": [group]})
        assert first["item_id"] == second["item_id"]
        assert len(world.custom.list("pose")) == 1
        assert world.custom.list("pose")[0]["title"] == "Old"
        assert set(second["items"][0]["groupIds"]) == {"default", group}
        reopened = FavoritesStore(world.extension.path)
        assert reopened.get("anima", "pose")["items"] == second["items"]
    asyncio.run(run())


def test_invalid_group_never_creates_source_or_favorite(tmp_path):
    async def run():
        world = make_world(tmp_path)
        with pytest.raises(CatalogError):
            await world.lifecycle.save_text("anima", "pose", {"title": "No", "text": "no", "groupIds": ["missing"]})
        assert not world.custom.path.exists() and not world.extension.path.exists()
    asyncio.run(run())


@pytest.mark.parametrize("section", ["character", "clothing", "pose", "background", "expression"])
def test_all_random_pool_text_is_local_and_source_linked(tmp_path, section):
    async def run():
        world = make_world(tmp_path)
        saved = await save(world, section=section)
        assert world.catalog.get(saved["item_id"])["section"] == section
        await world.lifecycle.source("anima", section, "update", saved["item_id"], {"prompt": "new"})
        assert (await world.favorites["anima"].get(section))["items"][0]["customContent"] == "new"
        await world.lifecycle.source("anima", section, "delete", saved["item_id"])
        assert (await world.favorites["anima"].get(section))["items"] == []
    asyncio.run(run())


def test_favorites_replace_then_error_is_confirmed_before_source_commit(tmp_path):
    async def run():
        world = make_world(tmp_path)
        saved = await save(world)
        write = world.extension._write_text
        first = True
        def write_then_error(text):
            nonlocal first
            write(text)
            if first:
                first = False
                raise OSError("reply after replace lost")
        with patch.object(world.extension, "_write_text", side_effect=write_then_error):
            result = await world.lifecycle.source("anima", "pose", "update", saved["item_id"], {"prompt": "confirmed"})
        assert result["favoritesSync"]["state"] == "complete"
        assert world.custom.list("pose")[0]["prompt"] == "confirmed"
        assert FavoritesStore(world.extension.path).get("anima", "pose")["items"][0]["customContent"] == "confirmed"
    asyncio.run(run())


def test_favorite_write_failure_does_not_publish_source(tmp_path):
    async def run():
        world = make_world(tmp_path)
        saved = await save(world)
        before, favorites = world.custom.path.read_bytes(), copy.deepcopy(world.extension.profiles)
        with patch.object(world.extension, "_write_text", side_effect=OSError("disk full")):
            with pytest.raises(PoolLifecycleError):
                await world.lifecycle.source("anima", "pose", "update", saved["item_id"], {"prompt": "rejected"})
        assert world.custom.path.read_bytes() == before
        assert world.extension.profiles == favorites
    asyncio.run(run())
