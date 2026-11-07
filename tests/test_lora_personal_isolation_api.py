"""Plugin model facts are shared; personal LoRA data stays project-local."""

import asyncio
import copy
import json

from aiohttp.test_utils import TestClient, TestServer

from anima_webui.resource_classification import classify_resources
from anima_webui.server import create_app
from anima_webui.workflow import DEFAULT_SETTINGS
from test_server import FakeComfy, _write_fake_tools


PERSONAL = {
    "tags": ["PLUGIN_PRIVATE_TAG"],
    "notes": "PLUGIN_PRIVATE_NOTE",
    "favorite": True,
    "usage_count": 987654321,
    "groupIds": ["PLUGIN_PRIVATE_GROUP"],
    "personal_extra": {"secret": "PLUGIN_PRIVATE_EXTRA"},
}
PERSONAL_KEYS = set(PERSONAL)


def metadata(filename="style.safetensors", base_model="Anima", root="F:/models/loras"):
    return {
        "file_path": f"{root}/{filename}",
        "model_name": filename.removesuffix(".safetensors"),
        "base_model": base_model,
        "file_size": 123,
        "sha256": "a" * 64,
        "modified": 100,
        "preview_url": "/preview/style",
        "preview_nsfw_level": 1,
        "civitai": {"trainedWords": ["style trigger"]},
        **copy.deepcopy(PERSONAL),
    }


def assert_no_plugin_personal_data(payload):
    text = json.dumps(payload)
    assert "PLUGIN_PRIVATE" not in text
    assert "987654321" not in text


def test_classification_adapter_preserves_only_technical_model_metadata():
    raw = metadata()
    original = copy.deepcopy(raw)
    item = classify_resources(["style.safetensors"], [raw], ["F:/models/loras"])[0]
    assert not PERSONAL_KEYS.intersection(item)
    assert item["model_profile"] == "anima"
    assert item["size"] == 123 and item["sha256"] == "a" * 64
    assert item["trigger_words"] == ["style trigger"]
    assert item["modified"] == 100 and item["has_preview"]
    assert raw == original


class PrivatePluginComfy(FakeComfy):
    def __init__(self):
        super().__init__()
        self.raw = [metadata(), metadata("unknown.safetensors", "Unknown")]

    async def resource_classifications(self, kind, *, refresh=False):
        root = "F:/models/loras" if kind == "loras" else "F:/models/unet"
        names = (
            ["style.safetensors", "unknown.safetensors"]
            if kind == "loras"
            else [DEFAULT_SETTINGS["model_name"]]
        )
        raw = self.raw if kind == "loras" else [metadata(names[0], root=root)]
        items = classify_resources(names, raw, [root])
        # A connector can accidentally expand its payload later. Every public
        # technical endpoint must still reject external personal fields.
        for item in items:
            item.update(copy.deepcopy(PERSONAL))
        return {
            "items": items,
            "roots": [root],
            "manager_items": copy.deepcopy(raw),
            "source": "lora-manager",
            "manager_url": self.base_url + "/" + kind,
        }

    async def lora_inventory(self):
        return await self.resource_classifications("loras")

    async def update_lora_metadata(self, *_args, **_kwargs):
        raise AssertionError("Plugin metadata must remain unchanged")


def test_all_technical_api_surfaces_exclude_plugin_personal_fields(tmp_path):
    async def run():
        comfy = PrivatePluginComfy()
        app = create_app(
            data_dir=tmp_path / "data",
            comfy=comfy,
            anima_tools_dir=_write_fake_tools(tmp_path),
        )
        async with TestClient(TestServer(app)) as client:
            for endpoint in (
                "/api/loras",
                "/api/resource-ownership?kind=loras",
                "/api/resources",
                "/api/resource-ownership?kind=models",
            ):
                response = await client.get(endpoint)
                assert response.status == 200, await response.text()
                value = await response.json()
                assert_no_plugin_personal_data(value)
                lists = [
                    value[key]
                    for key in (
                        "items",
                        "classification_items",
                        "unassigned",
                        "model_items",
                    )
                    if key in value
                ]
                for items in lists:
                    for item in items:
                        assert not PERSONAL_KEYS.intersection(item)
                        assert item["trigger_words"] == ["style trigger"]
            for method, endpoint, body in (
                (
                    "put",
                    "/api/loras/triggers",
                    {
                        "filename": "style.safetensors",
                        "triggerWords": ["local trigger"],
                    },
                ),
                ("delete", "/api/loras/triggers?filename=style.safetensors", None),
            ):
                response = await getattr(client, method)(endpoint, json=body)
                assert response.status == 200, await response.text()
                value = await response.json()
                assert_no_plugin_personal_data(value)
                assert not PERSONAL_KEYS.intersection(value)

    asyncio.run(run())


def test_personal_library_fields_remain_local_editable_and_persistent(tmp_path):
    async def run():
        comfy = PrivatePluginComfy()
        original_plugin = copy.deepcopy(comfy.raw)
        tools = _write_fake_tools(tmp_path)
        for first_start in (True, False):
            app = create_app(
                data_dir=tmp_path / "data", comfy=comfy, anima_tools_dir=tools
            )
            async with TestClient(TestServer(app)) as client:
                response = await client.get("/api/lora-library?model_profile=anima")
                assert response.status == 200, await response.text()
                value = await response.json()
                assert_no_plugin_personal_data(value)
                item = value["items"][0]
                if first_start:
                    assert (
                        item["favorite"],
                        item["tags"],
                        item["notes"],
                        item["groupIds"],
                        item["usage_count"],
                    ) == (False, [], "", [], 0)
                    group = await (
                        await client.post(
                            "/api/lora-library/groups", json={"name": "Local group"}
                        )
                    ).json()
                    group_id = group["groups"][-1]["id"]
                    response = await client.patch(
                        f"/api/lora-library/items/{item['id']}",
                        json={
                            "favorite": True,
                            "tags": ["local-tag"],
                            "notes": "local notes",
                            "groupIds": [group_id],
                            "default_strength": 0.7,
                        },
                    )
                    assert response.status == 200, await response.text()
                saved = await (
                    await client.get(f"/api/lora-library/items/{item['id']}")
                ).json()
                assert_no_plugin_personal_data(saved)
                assert (saved["favorite"], saved["tags"], saved["notes"]) == (
                    True,
                    ["local-tag"],
                    "local notes",
                )
                assert saved["groupIds"] == [group_id]
                assert saved["usage_count"] == 0
                technical = await (await client.get("/api/loras")).json()
                assert_no_plugin_personal_data(technical)
                assert technical["items"][0]["default_strength"] == 0.7
                assert not PERSONAL_KEYS.intersection(technical["items"][0])
        assert comfy.raw == original_plugin

    asyncio.run(run())
