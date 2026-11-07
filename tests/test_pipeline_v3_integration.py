"""Public capability, persistence and execution reporting for pipeline v4."""

import asyncio
import pytest

from anima_webui.comfy import ComfyClient, ComfyError
from anima_webui.history import HistoryStore
from anima_webui.runner import BatchManager
from anima_webui.sampling import enabled_postprocess_parameter_issues
from anima_webui.style_presets import preset_settings
from anima_webui.workflow import upgrade_pipeline_settings


def current(**stages):
    return upgrade_pipeline_settings(
        {"pipeline_version": 4, "base_mode": "normal", "upscalers": stages}
    )


def inventory(settings, **overrides):
    return {
        "models": [settings["model_name"]],
        "samplers": [settings["sampler_name"]],
        "schedulers": [settings["scheduler"]],
        "upscale_models": [],
        **overrides,
    }


@pytest.mark.parametrize(
    "available", [{"DLSS5NeuralRender"}, {"RTXVideoSuperResolution"}]
)
def test_capability_discovery_distinguishes_vsr_from_dlss(available):
    class Client(ComfyClient):
        async def object_info(self):
            return {
                "UNETLoader": {
                    "input": {"required": {"unet_name": [["anima.safetensors"]]}}
                },
                "KSampler": {
                    "input": {
                        "required": {
                            "sampler_name": [["euler"]],
                            "scheduler": [["simple"]],
                        }
                    }
                },
                **{name: {} for name in available},
            }

    resources = asyncio.run(Client().resource_inventory())
    assert resources["nodes"]["RTXVideoSuperResolution"] == (
        "RTXVideoSuperResolution" in available
    )
    assert resources["plugins"]["nvidia_upscale"] == (
        "RTXVideoSuperResolution" in available
    )
    assert resources["plugins"]["dlss"] == ("DLSS5NeuralRender" in available)


def test_vsr_does_not_require_dlss_and_ignores_misleading_plugin_flag():
    settings = current(nvidia={"enabled": True})
    resources = inventory(settings, nodes={"RTXVideoSuperResolution": True})
    assert BatchManager._resource_inventory_issues(settings, resources) == []
    resources.update(
        nodes={"RTXVideoSuperResolution": False},
        plugins={"nvidia_upscale": True, "dlss": True},
    )
    assert [
        (item["type"], item["name"])
        for item in BatchManager._resource_inventory_issues(settings, resources)
    ] == [("nvidia_node", "RTXVideoSuperResolution")]


def test_dlss_runs_independently_of_vsr():
    settings = current(dlss={"enabled": True})
    resources = inventory(
        settings, nodes={"DLSS5NeuralRender": True, "RTXVideoSuperResolution": False}
    )
    assert BatchManager._resource_inventory_issues(settings, resources) == []
    resources["nodes"]["DLSS5NeuralRender"] = False
    assert [
        (item["type"], item["name"])
        for item in BatchManager._resource_inventory_issues(settings, resources)
    ] == [("dlss_node", "DLSS5NeuralRender")]


def test_post_sampler_inventory_checked_without_latent_upscale():
    settings = current(
        post_sampler={"enabled": True, "sampler_name": "heun", "scheduler": "karras"}
    )
    issues = BatchManager._resource_inventory_issues(settings, inventory(settings))
    assert {(issue["type"], issue["name"]) for issue in issues} == {
        ("post_sampler", "heun"),
        ("post_scheduler", "karras"),
    }


def test_disabled_optional_stages_do_not_require_models_or_nodes():
    settings = current(
        pixel_hires={"model_name": "missing.pth"},
        post_sampler={"sampler_name": "missing", "scheduler": "missing"},
    )
    assert (
        BatchManager._resource_inventory_issues(
            settings, inventory(settings, nodes={}, plugins={})
        )
        == []
    )


def test_pixel_model_and_exact_nodes_are_required_only_when_enabled():
    settings = current(pixel_hires={"enabled": True, "model_name": "missing.pth"})
    issues = BatchManager._resource_inventory_issues(
        settings, inventory(settings, nodes={}, plugins={})
    )
    assert {(issue["type"], issue["name"]) for issue in issues} == {
        ("upscale_model", "missing.pth"),
        ("pixel_hires_node", "UpscaleModelLoader"),
        ("pixel_hires_node", "ImageUpscaleWithModel"),
    }


def test_post_sampler_node_parameters_are_validated_independently():
    resources = {
        "node_inputs": {"KSampler": {"denoise": ["FLOAT", {"min": 0.1, "max": 0.5}]}}
    }
    settings = current(post_sampler={"enabled": True, "denoise": 0.8})
    issues = enabled_postprocess_parameter_issues(settings, resources)
    assert len(issues) == 1 and "整图后采样" in issues[0]["label"]
    settings["upscalers"]["post_sampler"]["enabled"] = False
    assert enabled_postprocess_parameter_issues(settings, resources) == []


def test_pixel_shrink_parameters_are_unused_with_post_sampling():
    settings = current(
        pixel_hires={"enabled": True, "upscale_method": "lanczos"},
        post_sampler={"enabled": True},
    )
    resources = {"node_inputs": {"ImageScaleBy": {"upscale_method": [["bicubic"]]}}}
    assert enabled_postprocess_parameter_issues(settings, resources) == []
    settings["upscalers"]["post_sampler"]["enabled"] = False
    assert (
        "像素高清缩图"
        in enabled_postprocess_parameter_issues(settings, resources)[0]["label"]
    )


@pytest.mark.parametrize("profile,cfg", [("anima", 4)])
def test_preset_roundtrip_uses_current_profile_pipeline(profile, cfg):
    saved = preset_settings({"model_profile": profile})
    assert saved["pipeline_version"] == 4
    assert saved["upscalers"]["post_sampler"]["cfg"] == cfg
    assert preset_settings(saved) == saved


def test_progress_uses_submitted_stage_metadata_without_changing_job_stage():
    manager = BatchManager(None, None, None)
    manager.state = {
        "id": "batch",
        "kind": "generation",
        "status": "running",
        "stage": "generating",
        "prompt_id": "prompt",
    }
    manager._submitted_node_stages = {"post": ("post_sampler", "整图后采样")}
    manager._consume_progress(
        {
            "kind": "event",
            "payload": {
                "type": "executing",
                "data": {"prompt_id": "prompt", "node": "post"},
            },
        }
    )
    assert manager.state["progress_stage"] == "post_sampler"
    assert manager.state["progress_stage_label"] == "整图后采样"
    assert manager.state["stage"] == "generating"


def test_execution_failure_retains_node_identity():
    class Client(ComfyClient):
        async def _json(self, *args, **kwargs):
            return {
                "prompt": {
                    "status": {
                        "status_str": "error",
                        "messages": [
                            [
                                "execution_error",
                                {
                                    "node_id": "post",
                                    "node_type": "KSampler",
                                    "exception_message": "out of memory",
                                },
                            ]
                        ],
                    }
                }
            }

    with pytest.raises(ComfyError) as failure:
        asyncio.run(Client().wait_for_history("prompt"))
    assert failure.value.node_id == "post"
    assert failure.value.node_type == "KSampler"
    assert "out of memory" in str(failure.value)


def test_execution_failure_stage_is_persisted_with_batch(tmp_path):
    class Templates:
        def submission(self, *args, **kwargs):
            return {
                "prompt": {
                    "post": {
                        "class_type": "KSampler",
                        "inputs": {},
                        "_meta": {
                            "pipeline_stage": "post_sampler",
                            "title": "KSampler",
                        },
                    }
                }
            }

    class Client:
        async def lora_filenames(self):
            return []

        async def submit(self, payload):
            return "prompt"

        async def wait_for_history(self, *args, **kwargs):
            raise ComfyError("out of memory", node_id="post", node_type="KSampler")

    async def scenario():
        store = HistoryStore(tmp_path / "failure.sqlite3")
        manager = BatchManager(Templates(), store, Client())
        try:
            await manager.start({**current(), "count": 1})
            result = await manager.wait()
            assert result["status"] == "error"
            assert result["failed_stage"] == "post_sampler"
            assert result["failed_stage_label"] == "整图后采样"
            assert result["error"] == "整图后采样失败：out of memory"
            assert (await store.get_batch(result["id"]))["error"] == result["error"]
        finally:
            await store.close()

    asyncio.run(scenario())
