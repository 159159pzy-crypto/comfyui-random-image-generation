"""Anima negative-conditioning policy reaches every active repair sampler."""
from pathlib import Path
import pytest
from anima_webui.workflow import DEFAULT_SETTINGS, WorkflowError, WorkflowTemplates, validate_settings, default_settings_for_profile
ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture(scope="module")
def templates():
    return WorkflowTemplates.load(ROOT / "templates")


def submission(templates, **settings):
    return templates.submission(settings, 123, 456, "zero-test", "client", 1)


def assert_visual_edges_match(payload):
    api = payload["prompt"]
    ui = payload["extra_data"]["extra_pnginfo"]["workflow"]
    visual = {n["properties"]["pipeline_api_id"]: n for n in ui["nodes"]}
    links = {link[0]: link for link in ui["links"]}
    for api_id, node in api.items():
        assert visual[api_id]["type"] == node["class_type"]
        for name, value in node["inputs"].items():
            if not (isinstance(value, list) and len(value) == 2 and isinstance(value[0], str)
                    and isinstance(value[1], int)):
                continue
            port = next(p for p in visual[api_id]["inputs"] if p["name"] == name)
            link = links[port["link"]]
            assert link[1:3] == [visual[value[0]]["id"], value[1]]


@pytest.mark.parametrize("value", [None, 0, 1, "true", "false", [], {}])
def test_zero_rejects_non_boolean_values(value):
    with pytest.raises(WorkflowError, match="zero_negative"):
        validate_settings({"zero_negative": value})


@pytest.mark.parametrize("version", [4, 5])
@pytest.mark.parametrize("base_mode", ["normal", "dual_sampler"])
@pytest.mark.parametrize("profile", ["anima"])
@pytest.mark.parametrize("enabled", [False, True])
def test_random_zero_routes_follow_the_setting(templates, version, base_mode, profile, enabled):
    payload = submission(templates, pipeline_version=version, base_mode=base_mode,
                         model_profile=profile, zero_negative=enabled, negative_prompt="retain negative",
                         upscalers={"post_sampler": {"enabled": True}, "face_refine": {"enabled": True},
                                    "latent": {"enabled": True}, "seedvr2": {"enabled": True}})
    api = payload["prompt"]
    first = "701:1245" if base_mode == "dual_sampler" else "691:1240"
    final = "701:1246" if base_mode == "dual_sampler" else first
    for node_id in (first, final, "v3:post", "v3:face", *(["v4:latent:sample"] if version >= 4 else [])):
        negative = api[node_id]["inputs"]["negative"]
        node = api[negative[0]]
        assert node["class_type"] == ("ConditioningZeroOut" if enabled else "CLIPTextEncode")
        if enabled:
            node = api[node["inputs"]["conditioning"][0]]
        assert node["inputs"]["text"] == "retain negative"
    assert api["v3:post"]["inputs"]["negative"] == api[final]["inputs"]["negative"]
    assert api["v3:seed:sample"]["inputs"]["negative"] == ["v3:seed:conditioning", 1]
    assert payload["extra_data"]["extra_pnginfo"]["anima_random_webui"]["settings"]["zero_negative"] is enabled
    assert_visual_edges_match(payload)



def test_new_defaults_do_not_retune_versionless_saved_settings():
    fresh=default_settings_for_profile()
    assert fresh==DEFAULT_SETTINGS
    assert (fresh["pipeline_version"],fresh["steps"],fresh["cfg"],fresh["sampler_name"])==(5,30,4,"er_sde")
    assert fresh["dual"]["switch_step"]==15 and fresh["dual"]["second_cfg"]==4
    assert fresh["upscalers"]["face_refine"]["steps"]==16
    old=validate_settings({"base_mode":"normal"})
    assert (old["pipeline_version"],old["steps"],old["cfg"],old["sampler_name"])==(4,20,8,"euler")
    assert old["upscalers"]["face_refine"]["sampler"]=="res_2m_ode"
    assert old["zero_negative"] is False
    saved=validate_settings({"pipeline_version":4,"base_mode":"normal","steps":13,"cfg":6,"sampler_name":"heun","zero_negative":True})
    assert validate_settings(saved)==saved
