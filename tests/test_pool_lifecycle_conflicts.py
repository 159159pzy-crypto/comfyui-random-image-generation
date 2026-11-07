"""External local-file edits never get overwritten by a prepared transaction."""
import asyncio
import json
from unittest.mock import patch

import pytest

from anima_webui.pool_lifecycle import PoolLifecycleError
from test_pool_lifecycle import make_world, save


@pytest.mark.parametrize("change_target", [False, True])
def test_prepared_transaction_preserves_external_favorite_edit(tmp_path, change_target):
    async def run():
        world = make_world(tmp_path)
        target = await save(world)
        unrelated = await save(world, title="Other", text="other text")
        changed_id = (target if change_target else unrelated)["item_id"]
        before = world.custom.path.read_bytes()
        save_journal = world.lifecycle._save_journal
        injected = False
        async def change_after_prepare(profile, journal):
            nonlocal injected
            await save_journal(profile, journal)
            if not injected:
                injected = True
                payload = json.loads(world.extension.path.read_text(encoding="utf-8"))
                records = payload["sections"]["pose"]["items"]
                next(item for item in records if item["id"] == changed_id)["nickname"] = "External alias"
                world.extension.path.write_text(json.dumps(payload), encoding="utf-8")
        with patch.object(world.lifecycle, "_save_journal", side_effect=change_after_prepare):
            with pytest.raises(PoolLifecycleError) as failure:
                await world.lifecycle.source("anima", "pose", "update", target["item_id"], {"prompt": "rejected"})
        assert world.custom.path.read_bytes() == before
        assert failure.value.payload["recovery_required"] is change_target
        records = json.loads(world.extension.path.read_text(encoding="utf-8"))["sections"]["pose"]["items"]
        assert next(item for item in records if item["id"] == changed_id)["nickname"] == "External alias"
        assert world.lifecycle._journal("anima").exists() is change_target
    asyncio.run(run())


@pytest.mark.parametrize("section", ["pose", "expression"])
def test_unreadable_local_favorites_block_source_writes(tmp_path, section):
    async def run():
        world = make_world(tmp_path)
        await save(world)
        before = world.custom.path.read_bytes()
        with patch.object(world.extension, "read_durable", side_effect=PermissionError("locked")):
            with pytest.raises(PoolLifecycleError):
                await world.lifecycle.source("anima", section, "create", {"section": section, "title": "No", "prompt": "no"})
        assert world.custom.path.read_bytes() == before
        assert not world.lifecycle._journal("anima").exists()
    asyncio.run(run())
