from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any


DEFAULT_QUALITY = "masterpiece, best quality, score_9, score_8, highres, 2025, newest, safe"
DEFAULT_NEGATIVE = (
    "worst quality, low quality, lowres, score_1, score_2, score_3, blurry, "
    "jpeg artifacts, bad anatomy, watermark, artist name,"
)
DEFAULT_LORAS = [
    {
        "filename": "anima-highres-aesthetic-boost.safetensors",
        "enabled": True,
        "strength": 0.75,
    },
    {
        "filename": "BlueArchiveStyleB1.safetensors",
        "enabled": True,
        "strength": 0.95,
    },
    {
        "filename": "Cunnyfunkyv3.safetensors",
        "enabled": True,
        "strength": 0.75,
    },
]
MAX_LORAS = 64
MIN_LORA_STRENGTH = -100.0
MAX_LORA_STRENGTH = 100.0
PROMPT_SECTIONS = ("character", "clothing", "pose", "background", "expression")
DEFAULT_SETTINGS = {
    "count": 10,
    "random_character": False,
    "random_clothing": False,
    "random_pose": False,
    "random_background": False,
    "random_expression": False,
    "random_character_count": 1,
    "random_clothing_count": 1,
    "random_pose_count": 1,
    "random_background_count": 1,
    "random_expression_count": 1,
    "fixed_character": "",
    "fixed_clothing": "",
    "fixed_pose": "",
    "fixed_background": "",
    "fixed_expression": "",
    "female_count": 1,
    "male_count": 0,
    "loras": DEFAULT_LORAS,
    "pools": {
        "character": {"mode": "include", "ids": [], "excluded_ids": []},
        "clothing": {"mode": "include", "ids": [], "excluded_ids": []},
        "pose": {"mode": "include", "ids": [], "excluded_ids": []},
        "background": {"mode": "include", "ids": [], "excluded_ids": []},
        "expression": {"mode": "include", "ids": [], "excluded_ids": []},
    },
    "character_detail": "trigger_tags",
    "manual_artist": "",
    "quality_prompt": DEFAULT_QUALITY,
    "extra_prompt": "",
    "negative_prompt": DEFAULT_NEGATIVE,
    "width": 832,
    "height": 1216,
    "steps": 30,
    "cfg": 4.0,
}

MAX_SAMPLE_SEED = 1125899906842624

COMPOSER_ID = 60
POSITIVE_ID = 42
NEGATIVE_ID = 45
REMOVED_NODE_IDS = {3, 4}


class WorkflowError(ValueError):
    pass


def read_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str | Path, value: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _integer(name: str, value: Any, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorkflowError(f"{name} 必须是整数")
    if not minimum <= value <= maximum:
        raise WorkflowError(f"{name} 必须在 {minimum}-{maximum} 之间")
    return value


def _boolean(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise WorkflowError(f"{name} 必须是布尔值")
    return value


def _text(name: str, value: Any) -> str:
    if not isinstance(value, str):
        raise WorkflowError(f"{name} 必须是字符串")
    if len(value) > 20000:
        raise WorkflowError(f"{name} 不能超过 20000 个字符")
    return value.strip()


def _normalize_loras(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise WorkflowError("loras 必须是数组")
    if len(value) > MAX_LORAS:
        raise WorkflowError(f"LoRA 配置不能超过 {MAX_LORAS} 项")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(value, 1):
        if not isinstance(item, dict):
            raise WorkflowError(f"loras[{index}] 必须是对象")
        filename = _text(f"loras[{index}].filename", item.get("filename"))
        if not filename or "/" in filename or "\\" in filename:
            raise WorkflowError(f"loras[{index}].filename 必须是本地文件名")
        if filename in seen:
            raise WorkflowError(f"LoRA 不能重复配置: {filename}")
        seen.add(filename)
        enabled = _boolean(f"loras[{index}].enabled", item.get("enabled"))
        strength = item.get("strength")
        if isinstance(strength, bool) or not isinstance(strength, (int, float)):
            raise WorkflowError(f"loras[{index}].strength 必须是数字")
        if not math.isfinite(float(strength)):
            raise WorkflowError(f"loras[{index}].strength 必须是有限数字")
        strength = float(strength)
        if not MIN_LORA_STRENGTH <= strength <= MAX_LORA_STRENGTH:
            raise WorkflowError(
                f"loras[{index}].strength 必须在 {MIN_LORA_STRENGTH:g}-{MAX_LORA_STRENGTH:g} 之间"
            )
        result.append({"filename": filename, "enabled": enabled, "strength": strength})
    return result


def validate_loras(settings: dict[str, Any], available_filenames: Any | None = None) -> list[dict[str, Any]]:
    """Validate the persisted LoRA shape and, when available, the ComfyUI inventory."""
    loras = _normalize_loras(settings.get("loras", DEFAULT_LORAS))
    if available_filenames is not None:
        available = {str(value) for value in available_filenames}
        missing = [item["filename"] for item in loras if item["filename"] not in available]
        if missing:
            raise WorkflowError(f"LoRA 文件不存在: {', '.join(missing)}")
    return loras


def validate_settings(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    overrides = overrides or {}
    unknown = set(overrides) - set(DEFAULT_SETTINGS)
    if unknown:
        raise WorkflowError(f"未知参数: {', '.join(sorted(unknown))}")

    legacy_pools = "pools" not in overrides and any(overrides.get(f"random_{section}") for section in PROMPT_SECTIONS)
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    settings.update(overrides)
    settings["count"] = _integer("生成数量", settings["count"], 1, 1000)
    for name in tuple(f"random_{section}" for section in PROMPT_SECTIONS):
        settings[name] = _boolean(name, settings[name])
    for name in tuple(f"random_{section}_count" for section in PROMPT_SECTIONS):
        settings[name] = _integer(name, settings[name], 1, 5)
    if settings["random_expression_count"] != 1:
        raise WorkflowError("表情随机池每张只能抽取 1 项")
    settings["female_count"] = _integer("女性人数", settings["female_count"], 0, 5)
    settings["male_count"] = _integer("男性人数", settings["male_count"], 0, 5)
    total_people = settings["female_count"] + settings["male_count"]
    if total_people > 5:
        raise WorkflowError("画面总人数不能超过 5")
    if settings["random_character"] and total_people and settings["random_character_count"] > total_people:
        raise WorkflowError("指定角色数不能超过画面总人数")

    pools = settings.get("pools")
    if legacy_pools:
        pools = {
            section: {"mode": "all", "ids": [], "excluded_ids": []}
            for section in PROMPT_SECTIONS
        }
    if not isinstance(pools, dict):
        raise WorkflowError("pools 必须是对象")
    normalized_pools = {}
    for section in PROMPT_SECTIONS:
        selection = pools.get(section, {})
        if not isinstance(selection, dict):
            raise WorkflowError(f"{section} 随机池设置无效")
        mode = selection.get("mode", "include")
        if mode not in {"include", "all"}:
            raise WorkflowError(f"{section} 随机池模式无效")
        ids = selection.get("ids", [])
        excluded_ids = selection.get("excluded_ids", [])
        if not isinstance(ids, list) or not isinstance(excluded_ids, list):
            raise WorkflowError(f"{section} 随机池条目必须是数组")
        if len(ids) > 50000 or len(excluded_ids) > 50000:
            raise WorkflowError(f"{section} 随机池条目过多")
        normalized_pools[section] = {
            "mode": mode,
            "ids": list(dict.fromkeys(_text(f"{section} 条目", value) for value in ids)),
            "excluded_ids": list(dict.fromkeys(_text(f"{section} 排除条目", value) for value in excluded_ids)),
        }
    settings["pools"] = normalized_pools
    settings["loras"] = validate_loras(settings)

    if settings["character_detail"] not in {"trigger", "trigger_tags"}:
        raise WorkflowError("character_detail 必须是 trigger 或 trigger_tags")

    for name in (
        "manual_artist",
        "fixed_character",
        "fixed_clothing",
        "fixed_pose",
        "fixed_background",
        "fixed_expression",
        "quality_prompt",
        "extra_prompt",
        "negative_prompt",
    ):
        settings[name] = _text(name, settings[name])

    for name in ("width", "height"):
        settings[name] = _integer(name, settings[name], 64, 4096)
        if settings[name] % 8:
            raise WorkflowError(f"{name} 必须能被 8 整除")

    settings["steps"] = _integer("steps", settings["steps"], 1, 150)
    if isinstance(settings["cfg"], bool) or not isinstance(settings["cfg"], (int, float)):
        raise WorkflowError("cfg 必须是数字")
    settings["cfg"] = float(settings["cfg"])
    if not 0.1 <= settings["cfg"] <= 30:
        raise WorkflowError("cfg 必须在 0.1-30 之间")
    return settings


def _api_composer() -> dict[str, Any]:
    return {
        "inputs": {
            "enable_artist": False,
            "enable_character": True,
            "enable_clothing": True,
            "enable_background": True,
            "enable_pose": True,
            "character_detail": "trigger_tags",
            "seed": -1,
            "artist_count": 0,
            "preview_collapsed": False,
            "resolved_prompt": "",
            "character_count": 1,
            "clothing_count": 1,
            "pose_count": 1,
            "background_count": 1,
            "extra_prompt": "",
        },
        "class_type": "AnimaPromptComposer",
        "_meta": {"title": "Anima Prompt Random Draw"},
    }


def _visual_composer(link_id: int) -> dict[str, Any]:
    return {
        "id": COMPOSER_ID,
        "type": "AnimaPromptComposer",
        "pos": [1035, 95],
        "size": [420, 430],
        "flags": {},
        "order": 25,
        "mode": 0,
        "inputs": [],
        "outputs": [{"name": "text", "type": "STRING", "slot_index": 0, "links": [link_id]}],
        "properties": {
            "Node name for S&R": "AnimaPromptComposer",
            "cnr_id": "Comfyui-Anima-Tools",
        },
        "widgets_values": [False, True, True, True, True, "trigger_tags", -1, 0, False, "", 1, 1, 1, 1, ""],
        "title": "随机角色 / 服装 / 姿势 / 背景",
        "color": "#253b36",
        "bgcolor": "#35534b",
    }


def _remove_visual_nodes(workflow: dict[str, Any]) -> set[int]:
    workflow["nodes"] = [node for node in workflow["nodes"] if node.get("id") not in REMOVED_NODE_IDS]
    removed_links = {
        int(link[0])
        for link in workflow.get("links", [])
        if int(link[1]) in REMOVED_NODE_IDS or int(link[3]) in REMOVED_NODE_IDS
    }
    workflow["links"] = [link for link in workflow.get("links", []) if int(link[0]) not in removed_links]
    for node in workflow["nodes"]:
        for input_value in node.get("inputs", []):
            if input_value.get("link") in removed_links:
                input_value["link"] = None
        for output in node.get("outputs", []):
            links = output.get("links")
            if isinstance(links, list):
                output["links"] = [link_id for link_id in links if link_id not in removed_links] or None
    return removed_links


def _visual_node(workflow: dict[str, Any], node_id: int) -> dict[str, Any]:
    for node in workflow["nodes"]:
        if int(node.get("id")) == node_id:
            return node
    raise WorkflowError(f"工作流缺少节点 {node_id}")


def prepare_templates(
    api_source: dict[str, Any], ui_source: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    api = copy.deepcopy(api_source)
    ui = copy.deepcopy(ui_source)

    for node_id in ("3", "4"):
        api.pop(node_id, None)
    api[str(COMPOSER_ID)] = _api_composer()

    positive = api[str(POSITIVE_ID)]
    positive_clip = positive["inputs"]["clip"]
    positive["class_type"] = "AnimaPromptPlusClipEncode"
    positive["inputs"] = {
        "clip": positive_clip,
        "quality_prompt": DEFAULT_QUALITY,
        "artist_tags": "",
        "character_tags": "",
        "clothing_tags": "",
        "pose_tags": "",
        "background_tags": "",
        "extra_prompt": [str(COMPOSER_ID), 0],
        "separator": ", ",
    }
    positive.setdefault("_meta", {})["title"] = "Anima Positive Prompt"

    negative = api[str(NEGATIVE_ID)]
    negative["inputs"] = {"text": DEFAULT_NEGATIVE, "clip": negative["inputs"]["clip"]}

    _remove_visual_nodes(ui)
    next_link_id = max((int(link[0]) for link in ui.get("links", [])), default=0) + 1
    ui["nodes"].append(_visual_composer(next_link_id))

    positive_ui = _visual_node(ui, POSITIVE_ID)
    clip_link = next(item["link"] for item in positive_ui["inputs"] if item["name"] == "clip")
    conditioning_links = positive_ui["outputs"][0].get("links")
    positive_ui.update(
        {
            "type": "AnimaPromptPlusClipEncode",
            "pos": [1485, 75],
            "size": [430, 455],
            "title": "Anima 正面提示词",
            "inputs": [
                {"name": "clip", "type": "CLIP", "link": clip_link},
                {"name": "quality_prompt", "type": "STRING", "widget": {"name": "quality_prompt"}, "link": None},
                {"name": "artist_tags", "type": "STRING", "widget": {"name": "artist_tags"}, "link": None},
                {"name": "character_tags", "type": "STRING", "widget": {"name": "character_tags"}, "link": None},
                {"name": "clothing_tags", "type": "STRING", "widget": {"name": "clothing_tags"}, "link": None},
                {"name": "pose_tags", "type": "STRING", "widget": {"name": "pose_tags"}, "link": None},
                {"name": "background_tags", "type": "STRING", "widget": {"name": "background_tags"}, "link": None},
                {"name": "extra_prompt", "type": "STRING", "widget": {"name": "extra_prompt"}, "link": next_link_id},
                {"name": "separator", "type": "STRING", "widget": {"name": "separator"}, "link": None},
            ],
            "outputs": [
                {"name": "positive", "type": "CONDITIONING", "slot_index": 0, "links": conditioning_links},
                {"name": "text", "type": "STRING", "slot_index": 1, "links": None},
            ],
            "widgets_values": [DEFAULT_QUALITY, "", "", "", "", "", "", "", ", "],
            "properties": {
                "Node name for S&R": "AnimaPromptPlusClipEncode",
                "cnr_id": "Comfyui-Anima-Tools",
            },
            "color": "#253b36",
            "bgcolor": "#35534b",
        }
    )

    negative_ui = _visual_node(ui, NEGATIVE_ID)
    negative_clip_link = next(item["link"] for item in negative_ui["inputs"] if item["name"] == "clip")
    negative_ui["pos"] = [1485, 555]
    negative_ui["size"] = [430, 175]
    negative_ui["inputs"] = [
        {"name": "clip", "type": "CLIP", "link": negative_clip_link},
        {"name": "text", "type": "STRING", "widget": {"name": "text"}, "link": None},
    ]
    negative_ui["widgets_values"] = [DEFAULT_NEGATIVE]

    ui["links"].append([next_link_id, COMPOSER_ID, 0, POSITIVE_ID, 7, "STRING"])
    ui["last_node_id"] = max(int(ui.get("last_node_id", 0)), COMPOSER_ID)
    ui["last_link_id"] = max(int(ui.get("last_link_id", 0)), next_link_id)
    return api, ui


def _set_ui_widget(ui: dict[str, Any], node_id: int, value: Any, index: int | None = None) -> None:
    node = _visual_node(ui, node_id)
    if index is None:
        node["widgets_values"] = value
        return
    values = list(node.get("widgets_values") or [])
    while len(values) <= index:
        values.append("")
    values[index] = value
    node["widgets_values"] = values


def render_workflows(
    api_template: dict[str, Any],
    ui_template: dict[str, Any],
    settings: dict[str, Any],
    sample_seed: int,
    prompt_seed: int,
    filename_prefix: str,
    resolved_prompt: str = "",
    resolved_selection: dict[str, Any] | None = None,
    resolved_prompt_full: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    settings = validate_settings(settings)
    if not 0 <= sample_seed <= MAX_SAMPLE_SEED:
        raise WorkflowError("sample_seed 超出范围")
    if not 0 <= prompt_seed <= 2**31 - 1:
        raise WorkflowError("prompt_seed 超出范围")

    api = copy.deepcopy(api_template)
    ui = copy.deepcopy(ui_template)
    lora_node = api.get("2")
    if not isinstance(lora_node, dict) or not isinstance(lora_node.get("inputs"), dict):
        raise WorkflowError("工作流缺少 LoRA 节点 2")
    lora_inputs = lora_node["inputs"]
    for key in list(lora_inputs):
        if key.startswith("lora_"):
            del lora_inputs[key]
    for index, item in enumerate(settings["loras"], 1):
        lora_inputs[f"lora_{index}"] = {
            "on": item["enabled"],
            "lora": item["filename"],
            "strength": item["strength"],
        }
    lora_ui = _visual_node(ui, 2)
    lora_ui["widgets_values"] = [
        {},
        {"type": "PowerLoraLoaderHeaderWidget"},
        *[
            {
                "on": item["enabled"],
                "lora": item["filename"],
                "strength": item["strength"],
                "strengthTwo": None,
            }
            for item in settings["loras"]
        ],
        {},
        "",
    ]
    composer_inputs = api[str(COMPOSER_ID)]["inputs"]
    composer_inputs.update(
        {
            "enable_artist": False,
            "enable_character": settings["random_character"],
            "enable_clothing": settings["random_clothing"],
            "enable_background": settings["random_background"],
            "enable_pose": settings["random_pose"],
            "character_detail": settings["character_detail"],
            "seed": prompt_seed,
            "artist_count": 0,
            "preview_collapsed": False,
            "resolved_prompt": resolved_prompt,
            "character_count": settings["random_character_count"],
            "clothing_count": settings["random_clothing_count"],
            "pose_count": settings["random_pose_count"],
            "background_count": settings["random_background_count"],
            "extra_prompt": "" if resolved_prompt else settings["extra_prompt"],
        }
    )
    positive = api[str(POSITIVE_ID)]["inputs"]
    positive["quality_prompt"] = settings["quality_prompt"]
    positive["artist_tags"] = settings["manual_artist"]
    positive["character_tags"] = "" if settings["random_character"] else settings["fixed_character"]
    positive["clothing_tags"] = "" if settings["random_clothing"] else settings["fixed_clothing"]
    positive["pose_tags"] = "" if settings["random_pose"] else settings["fixed_pose"]
    positive["background_tags"] = "" if settings["random_background"] else settings["fixed_background"]
    positive["extra_prompt"] = [str(COMPOSER_ID), 0]
    api[str(NEGATIVE_ID)]["inputs"]["text"] = settings["negative_prompt"]
    api["23"]["inputs"]["value"] = settings["width"]
    api["31"]["inputs"]["value"] = settings["height"]
    api["35"]["inputs"]["value"] = 1
    api["37"]["inputs"]["seed"] = sample_seed
    api["39"]["inputs"]["value"] = settings["steps"]
    api["41"]["inputs"]["value"] = settings["cfg"]
    api["12"]["inputs"]["filename_prefix"] = filename_prefix

    _set_ui_widget(
        ui,
        COMPOSER_ID,
        [
            False,
            settings["random_character"],
            settings["random_clothing"],
            settings["random_background"],
            settings["random_pose"],
            settings["character_detail"],
            prompt_seed,
            0,
            False,
            resolved_prompt,
            settings["random_character_count"],
            settings["random_clothing_count"],
            settings["random_pose_count"],
            settings["random_background_count"],
            "" if resolved_prompt else settings["extra_prompt"],
        ],
    )
    _set_ui_widget(ui, POSITIVE_ID, settings["quality_prompt"], 0)
    _set_ui_widget(ui, POSITIVE_ID, settings["manual_artist"], 1)
    _set_ui_widget(ui, POSITIVE_ID, positive["character_tags"], 2)
    _set_ui_widget(ui, POSITIVE_ID, positive["clothing_tags"], 3)
    _set_ui_widget(ui, POSITIVE_ID, positive["pose_tags"], 4)
    _set_ui_widget(ui, POSITIVE_ID, positive["background_tags"], 5)
    _set_ui_widget(ui, POSITIVE_ID, settings["extra_prompt"], 7)
    _set_ui_widget(ui, NEGATIVE_ID, settings["negative_prompt"], 0)
    for node_id, value in ((23, settings["width"]), (31, settings["height"]), (35, 1), (39, settings["steps"]), (41, settings["cfg"]), (12, filename_prefix)):
        _set_ui_widget(ui, node_id, value)
    _set_ui_widget(ui, 37, sample_seed, 0)
    return api, ui


def build_submission(
    api_template: dict[str, Any],
    ui_template: dict[str, Any],
    settings: dict[str, Any],
    sample_seed: int,
    prompt_seed: int,
    filename_prefix: str,
    client_id: str,
    sequence: int,
    resolved_prompt: str = "",
    resolved_selection: dict[str, Any] | None = None,
    resolved_prompt_full: str = "",
) -> dict[str, Any]:
    api, ui = render_workflows(
        api_template,
        ui_template,
        settings,
        sample_seed,
        prompt_seed,
        filename_prefix,
        resolved_prompt,
        resolved_selection,
        resolved_prompt_full,
    )
    metadata = {
        "settings": validate_settings(settings),
        "sample_seed": sample_seed,
        "prompt_seed": prompt_seed,
        "sequence": sequence,
        "resolved_selection": resolved_selection or {},
        "resolved_prompt": resolved_prompt,
        "resolved_prompt_full": resolved_prompt_full,
    }
    return {
        "prompt": api,
        "client_id": client_id,
        "extra_data": {"extra_pnginfo": {"workflow": ui, "anima_random_webui": metadata}},
    }


class WorkflowTemplates:
    def __init__(self, api_template: dict[str, Any], ui_template: dict[str, Any]):
        self.api = api_template
        self.ui = ui_template

    @classmethod
    def load(cls, template_dir: str | Path) -> "WorkflowTemplates":
        directory = Path(template_dir)
        return cls(read_json(directory / "workflow_api.json"), read_json(directory / "workflow_ui.json"))

    def submission(
        self,
        settings: dict[str, Any],
        sample_seed: int,
        prompt_seed: int,
        filename_prefix: str,
        client_id: str,
        sequence: int,
        resolved_prompt: str = "",
        resolved_selection: dict[str, Any] | None = None,
        resolved_prompt_full: str = "",
    ) -> dict[str, Any]:
        return build_submission(
            self.api,
            self.ui,
            settings,
            sample_seed,
            prompt_seed,
            filename_prefix,
            client_id,
            sequence,
            resolved_prompt,
            resolved_selection,
            resolved_prompt_full,
        )
