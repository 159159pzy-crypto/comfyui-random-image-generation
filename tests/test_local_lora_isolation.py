import asyncio
import copy
from unittest.mock import patch

import pytest

from anima_webui.lora_library import LoraLibraryService, LoraLibraryStore
from anima_webui.lora_triggers import LoraTriggerOverrideStore
from anima_webui.persistence import StorageReadOnlyError
from test_lora_library import Characters, LibraryClient


def test_all_organization_and_trigger_overrides_are_isolated(tmp_path):
    async def run():
        client = LibraryClient()
        client.raw[0].update(favorite=True, tags=["external"], notes="external note")
        original = copy.deepcopy(client.raw)
        store = LoraLibraryStore(tmp_path / "local.json")
        triggers = LoraTriggerOverrideStore(tmp_path / "triggers.json")
        service = LoraLibraryService(client, store, triggers, Characters())
        first = (await service.inventory())[0]
        assert not first["favorite"] and not first["tags"] and not first["notes"]
        assert first["source_trigger_words"] == ["@hero_0"] and first["has_preview"]
        group = (await service.group("POST", values={"name": "Local"}))["groups"][0]["id"]
        await service.update_item(first["id"], {"groupIds": [group], "tags": ["custom"],
            "notes": "local note", "default_strength": .7})
        await triggers.set(first["filename"], ["local_trigger"])
        await service.set_binding("character:hero", [{"id": first["id"]}])
        assert client.raw == original and not client.writes
        reopened = LoraLibraryService(client, LoraLibraryStore(store.path), LoraTriggerOverrideStore(triggers.path), Characters())
        saved = await reopened.item(first["id"])
        assert saved["favorite"] and saved["tags"] == ["custom"] and saved["notes"] == "local note"
        assert saved["groupIds"] == [group] and saved["default_strength"] == .7
        assert saved["trigger_words"] == ["local_trigger"]
        await reopened.trigger_store.delete(first["filename"])
        assert (await reopened.item(first["id"]))["trigger_words"] == ["@hero_0"]
        other = LoraLibraryService(client, LoraLibraryStore(tmp_path / "other.json"),
                                  LoraTriggerOverrideStore(tmp_path / "other-triggers.json"), Characters())
        untouched = await other.item(first["id"])
        assert not untouched["favorite"] and untouched["tags"] == [] and untouched["notes"] == ""
        assert client.raw == original
        await service.close()
        await reopened.close()
        await other.close()
    asyncio.run(run())


def test_local_save_failure_rolls_back_all_fields_and_retry_is_local(tmp_path):
    async def run():
        client = LibraryClient()
        store = LoraLibraryStore(tmp_path / "local.json")
        service = LoraLibraryService(client, store, LoraTriggerOverrideStore(tmp_path / "triggers.json"), Characters())
        first = (await service.inventory())[0]
        await service.update_item(first["id"], {"notes": "keep"})
        original, before = copy.deepcopy(client.raw), store.path.read_bytes()
        with patch.object(store, "_write", side_effect=OSError("disk full")):
            with pytest.raises(StorageReadOnlyError):
                await service.update_item(first["id"], {"notes": "failed", "favorite": True, "tags": ["failed"]})
        assert store.path.read_bytes() == before and client.raw == original
        await service.update_item(first["id"], {"notes": "retry", "favorite": True})
        item = await service.item(first["id"])
        assert item["notes"] == "retry" and item["favorite"] and not item["tags"]
        assert not client.writes
        await service.close()
    asyncio.run(run())
