"""Fresh isolated history databases, restarts, and failed-write protection."""

import asyncio
import sqlite3
from contextlib import closing
from unittest.mock import patch

import pytest

from anima_webui.history import HistoryStore
from anima_webui.persistence import StorageReadOnlyError
from anima_webui.workflow import validate_settings


async def add_sample(store, batch_id="sample"):
    return await store.add_image(
        batch_id=batch_id,
        sequence=1,
        prompt_id="prompt-id",
        image={"filename": "sample.png", "subfolder": "AnimaRandom", "type": "output"},
        positive_prompt="standing",
        negative_prompt="low quality",
        sample_seed=123,
        prompt_seed=456,
        settings=validate_settings({"count": 1}),
        resolved_selection={"pose": [{"id": "pose:stand", "prompt": "standing"}]},
        resolved_prompt="standing",
        bound_resources={"model_profile": "anima"},
        execution_context={"execution_id": "sample", "execution_nonce": 7},
        lora_runtime={"manual": []},
    )


def test_new_database_starts_empty_with_current_schema_and_no_migration_backups(
    tmp_path,
):
    async def run():
        path = tmp_path / "isolated" / "history.sqlite3"
        store = HistoryStore(path)
        try:
            assert store.available
            assert store.load_warnings == []
            assert (await store.list_images())["total"] == 0
            tables = {
                row[0]
                for row in store.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            assert {
                "batches",
                "images",
                "artwork_folders",
                "artwork_memberships",
            } <= tables
            assert "prompt_jobs" not in tables
            columns = {
                row[1] for row in store.connection.execute("PRAGMA table_info(images)")
            }
            assert {
                "resolved_selection_json",
                "resolved_prompt",
                "bound_resources_json",
                "execution_context_json",
                "lora_runtime_json",
            } <= columns
            assert "single_runtime_json" not in columns
            assert store.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        finally:
            await store.close()
        assert path.is_file()
        assert list(path.parent.glob("*.bak")) == []

    asyncio.run(run())


def test_restart_preserves_frozen_history_and_only_interrupts_unfinished_batches(
    tmp_path,
):
    async def run():
        path = tmp_path / "history.sqlite3"
        settings = validate_settings({"count": 1})
        store = HistoryStore(path)
        try:
            await store.create_batch("sample", 1, settings)
            image = await add_sample(store)
            await store.update_batch("sample", completed=1, status="completed")
            await store.create_batch("unfinished", 2, settings)
            await store.update_batch("unfinished", completed=1, status="stopping")
        finally:
            await store.close()
        reopened = HistoryStore(path)
        try:
            assert reopened.available
            assert await reopened.get_image(image["id"]) == image
            completed = await reopened.get_batch("sample")
            assert completed["status"] == "completed"
            assert completed["settings"] == settings
            unfinished = await reopened.get_batch("unfinished")
            assert unfinished["status"] == "interrupted"
            assert unfinished["completed"] == 1
            assert "重启" in unfinished["error"]
            assert (await reopened.list_images())["total"] == 1
        finally:
            await reopened.close()
        assert list(tmp_path.glob("*.bak")) == []

    asyncio.run(run())


def test_failed_image_transaction_rolls_back_prior_trigger_writes(tmp_path):
    async def run():
        store = HistoryStore(tmp_path / "history.sqlite3")
        try:
            await store.create_batch("sample", 1, validate_settings({"count": 1}))
            store.connection.execute("""
                CREATE TRIGGER reject_image BEFORE INSERT ON images BEGIN
                    UPDATE batches SET completed = 99 WHERE id = NEW.batch_id;
                    SELECT RAISE(FAIL, 'test failed image write');
                END
            """)
            store.connection.commit()
            with pytest.raises(sqlite3.IntegrityError, match="failed image write"):
                await add_sample(store)
            assert not store.connection.in_transaction
            assert (await store.get_batch("sample"))["completed"] == 0
            assert (await store.list_images())["total"] == 0
            store.connection.execute("DROP TRIGGER reject_image")
            store.connection.commit()
            saved = await add_sample(store)
            assert saved["filename"] == "sample.png"
            assert store.available
            assert (await store.list_images())["total"] == 1
        finally:
            await store.close()

    asyncio.run(run())


def test_unavailable_existing_database_is_not_replaced_and_rejects_writes(tmp_path):
    async def run():
        path = tmp_path / "history.sqlite3"
        store = HistoryStore(path)
        await store.create_batch("sample", 1, validate_settings({"count": 1}))
        await store.close()
        original = path.read_bytes()
        with patch.object(
            HistoryStore,
            "_open",
            side_effect=sqlite3.OperationalError("database is locked"),
        ):
            protected = HistoryStore(path)
        try:
            assert not protected.available
            assert "database is locked" in protected.read_only_reason
            assert (await protected.list_images())["total"] == 0
            with pytest.raises(StorageReadOnlyError):
                await protected.create_batch("new", 1, validate_settings({"count": 1}))
        finally:
            await protected.close()
        assert path.read_bytes() == original
        assert list(tmp_path.glob("*.bak")) == []
        with closing(sqlite3.connect(path)) as connection:
            assert connection.execute("SELECT id FROM batches").fetchall() == [
                ("sample",)
            ]

    asyncio.run(run())
