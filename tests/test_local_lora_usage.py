"""Usage is derived only from this project's successful image history."""
import asyncio
import copy

from aiohttp.test_utils import TestClient, TestServer

from anima_webui.history import HistoryStore
from anima_webui.lora_library import LoraLibraryService, LoraLibraryStore
from anima_webui.lora_triggers import LoraTriggerOverrideStore
from anima_webui.server import HISTORY_KEY, create_app
from test_lora_library import Characters, LibraryClient
from test_server import _write_fake_tools


async def save_image(history, loras):
    return await history.add_image(
        batch_id="local", sequence=1, prompt_id="done", image={"filename": "local.png"},
        positive_prompt="saved", negative_prompt="", sample_seed=1, prompt_seed=2,
        settings={"model_profile": "anima", "loras": loras},
    )


def lora(index, **overrides):
    return {"filename": f"people/hero-{index}.safetensors", "enabled": True, "strength": 1, **overrides}


def test_plugin_usage_is_never_a_fallback_without_local_history(tmp_path):
    async def run():
        client = LibraryClient()
        for raw in client.raw:
            raw["usage_count"] = 999
        service = LoraLibraryService(client, LoraLibraryStore(tmp_path / "library.json"),
                                     LoraTriggerOverrideStore(tmp_path / "triggers.json"), Characters())
        try:
            assert all(item["usage_count"] == 0 for item in await service.inventory())
            assert not service.store.path.exists()
        finally:
            await service.close()

    asyncio.run(run())


def test_usage_counts_deduplicate_each_saved_image_and_ignore_invalid_or_disabled_entries(tmp_path):
    async def run():
        history = HistoryStore(tmp_path / "history.sqlite3")
        try:
            await history.create_batch("local", 10, {"model_profile": "anima"})
            assert await history.lora_usage_counts() == {}
            await save_image(history, [lora(0), lora(0, filename="PEOPLE\\HERO-0.SAFETENSORS"),
                                       lora(1, enabled=False), lora(1, enabled="true"),
                                       lora(1, filename="../outside.safetensors"), None, "invalid"])
            image = await save_image(history, [lora(0), lora(2)])
            await save_image(history, {"filename": "not-a-list"})
            before = history.connection.total_changes
            assert await history.lora_usage_counts() == {
                "people/hero-0.safetensors": 2, "people/hero-2.safetensors": 1,
            }
            assert history.connection.total_changes == before
            await history.delete_image(image["id"])
            assert await history.lora_usage_counts() == {"people/hero-0.safetensors": 1}
        finally:
            await history.close()

    asyncio.run(run())


def test_local_usage_drives_listing_detail_sorting_and_stays_in_its_database(tmp_path):
    async def run():
        client = LibraryClient()
        client.raw[1]["usage_count"] = 10000
        original = copy.deepcopy(client.raw)
        one = HistoryStore(tmp_path / "first" / "history.sqlite3")
        two = HistoryStore(tmp_path / "second" / "history.sqlite3")
        services = []
        for directory, history in (("first", one), ("second", two)):
            services.append(LoraLibraryService(
                client, LoraLibraryStore(tmp_path / directory / "library.json"),
                LoraTriggerOverrideStore(tmp_path / directory / "triggers.json"), Characters(),
                local_usage=history.lora_usage_counts,
            ))
        try:
            assert all(item["usage_count"] == 0 for item in await services[0].inventory())
            await one.create_batch("local", 3, {"model_profile": "anima"})
            await save_image(one, [lora(0), lora(2), lora(2)])
            await save_image(one, [lora(2)])
            listing = await services[0].query({"sort": "usage", "order": "desc"})
            assert [(item["filename"], item["usage_count"]) for item in listing["items"]] == [
                ("people/hero-2.safetensors", 2), ("people/hero-0.safetensors", 1), ("people/hero-1.safetensors", 0),
            ]
            assert (await services[0].item(listing["items"][0]["id"]))["usage_count"] == 2
            assert all(item["usage_count"] == 0 for item in await services[1].inventory())
            assert client.raw == original and not client.writes
            assert all(not service.store.path.exists() for service in services)
        finally:
            for service in services:
                await service.close()
            await one.close()
            await two.close()

    asyncio.run(run())


def test_application_injects_history_usage_provider(tmp_path):
    async def run():
        app = create_app(data_dir=tmp_path / "data", comfy=LibraryClient(),
                         anima_tools_dir=_write_fake_tools(tmp_path))
        async with TestClient(TestServer(app)) as client:
            response = await client.get("/api/lora-library")
            assert response.status == 200
            assert all(item["usage_count"] == 0 for item in (await response.json())["items"])
            history = app[HISTORY_KEY]
            await history.create_batch("local", 1, {"model_profile": "anima"})
            await save_image(history, [lora(2)])
            response = await client.get("/api/lora-library?sort=usage&order=desc")
            assert response.status == 200
            items = (await response.json())["items"]
            assert items[0]["filename"] == "people/hero-2.safetensors"
            assert items[0]["usage_count"] == 1

    asyncio.run(run())
