"""v5 fixes pixel resize coupling without rewriting historical execution."""
import copy
import itertools
from pathlib import Path

import pytest

from anima_webui.pipeline_v3 import required_node_classes
from anima_webui.runner import BatchManager
from anima_webui.sampling import enabled_postprocess_parameter_issues
from anima_webui.workflow import WorkflowTemplates, validate_settings


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def templates():
    return WorkflowTemplates.load(ROOT / "templates")


def pixel_settings(version, *, post=True, latent=False, percent=50, method="lanczos", workbench="random"):
    return validate_settings({"pipeline_version": version, "base_mode": "normal", "workbench": workbench,
                              "upscalers": {"pixel_hires": {"enabled": True, "percent": percent, "upscale_method": method},
                                            "post_sampler": {"enabled": post}, "latent": {"enabled": latent}}})


@pytest.mark.parametrize("post,latent,percent,method,workbench", list(itertools.product(
    (False, True), (False, True), (25, 100), ("nearest-exact", "lanczos"), ("random",))))
def test_v5_always_applies_pixel_resize_and_other_graph_edges_match_v4(templates, post, latent, percent, method, workbench):
    graphs = {}
    for version in (4, 5):
        settings = pixel_settings(version, post=post, latent=latent, percent=percent, method=method, workbench=workbench)
        payload = templates.submission(settings, 12, 34, "test", "client", 1, execution_nonce=56)
        graphs[version] = payload["prompt"]
        assert payload["extra_data"]["extra_pnginfo"]["workflow"]["extra"]["pipeline"]["version"] == version
    new = graphs[5]
    assert new["v3:pixel:resize"]["inputs"] == {
        "image": ["v3:pixel:upscale", 0], "upscale_method": method, "scale_by": percent / 100}
    assert ("v3:pixel:resize" in graphs[4]) == (not post)
    if post:
        # v5 only inserts the requested resize into this formerly bypassed
        # image edge; all model, conditioning, sampling and other stages match.
        restored = copy.deepcopy(new)
        restored.pop("v3:pixel:resize")
        for node in restored.values():
            for key, value in node["inputs"].items():
                if value == ["v3:pixel:resize", 0]:
                    node["inputs"][key] = ["v3:pixel:upscale", 0]
        assert restored == graphs[4]
    else:
        assert new == graphs[4]


@pytest.mark.parametrize("version", [4, 5])
def test_resize_resource_preflight_matches_the_versioned_graph(templates, version):
    settings = pixel_settings(version)
    graph = templates.submission(settings, 12, 34, "test", "client", 1)["prompt"]
    advertised = {node["class_type"] for node in graph.values()} - {"ImageScaleBy"}
    issues = BatchManager._resource_inventory_issues(settings, {"node_types": sorted(advertised)})
    assert ("ImageScaleBy" in required_node_classes(settings)) == (version == 5)
    assert any(issue["name"] == "ImageScaleBy" for issue in issues) == (version == 5)
    schema = {"node_inputs": {"ImageScaleBy": {"upscale_method": [["bicubic"]]}}}
    parameter_issues = enabled_postprocess_parameter_issues(settings, schema)
    assert any("像素高清缩图" in issue["label"] for issue in parameter_issues) == (version == 5)

