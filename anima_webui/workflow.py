from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path
from typing import Any


DEFAULT_QUALITY = "masterpiece, best quality, score_9, score_8, highres, 2025, newest, safe"
DEFAULT_NEGATIVE = (
    "worst quality, low quality, lowres, score_1, score_2, score_3, blurry, "
    "jpeg artifacts, bad anatomy, watermark, artist name,"
)
DEFAULT_LORAS: list[dict[str, Any]] = []
DEFAULT_MODEL = "miaomiaoHarem_anima14.safetensors"
DEFAULT_HIRES = {
    "enabled": True,
    "model_name": "4x_foolhardy_Remacri.pth",
    "percent": 45,
}
DEFAULT_DETAILERS = {
    "hand": False,
    "nsfw": False,
    "face": False,
    "eyes": False,
}
DETAILER_ORDER = tuple(DEFAULT_DETAILERS)
DETAILER_NODES = {
    "hand": {"detector": 8, "editor": 13, "detailer": 27, "detector_input": "bbox_detector"},
    "nsfw": {"detector": 9, "editor": 14, "detailer": 28, "detector_input": "segm_detector"},
    "face": {"detector": 10, "editor": 15, "detailer": 29, "detector_input": "bbox_detector"},
    "eyes": {"detector": 11, "editor": 16, "detailer": 30, "detector_input": "bbox_detector"},
}
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
    "model_name": DEFAULT_MODEL,
    "loras": DEFAULT_LORAS,
    "hires": DEFAULT_HIRES,
    "detailers": DEFAULT_DETAILERS,
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
HIRES_SCALE_ID = 51
HIRES_MODEL_ID = 61
HIRES_UPSCALE_ID = 62
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


def normalize_lora_path(value: Any) -> str:
    filename = _text("LoRA 文件名", value).replace("\\", "/")
    parts = filename.split("/")
    if (
        not filename
        or filename.startswith("/")
        or len(filename) >= 2 and filename[1] == ":"
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise WorkflowError("LoRA 文件名必须是安全的相对路径")
    return "/".join(parts)


def normalize_artist_tags(value: Any) -> str:
    raw = _text("manual_artist", value).replace("\r", ",").replace("\n", ",")
    names: list[str] = []
    seen: set[str] = set()
    for part in re.split(r",|(?=@)", raw):
        name = part.strip()
        if name.startswith("@"):
            name = name[1:].strip()
        elif name.lower().startswith("by "):
            name = name[3:].strip()
        if not name:
            continue
        identity = name.casefold()
        if identity not in seen:
            names.append(name)
            seen.add(identity)
    return ", ".join(f"@{name}" for name in names)


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
        filename = normalize_lora_path(item.get("filename"))
        identity = filename.casefold()
        if identity in seen:
            raise WorkflowError(f"LoRA 不能重复配置: {filename}")
        seen.add(identity)
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
        available: dict[str, str] = {}
        by_basename: dict[str, list[str]] = {}
        for raw in available_filenames:
            exact = str(raw)
            normalized = normalize_lora_path(exact)
            available.setdefault(normalized.casefold(), exact)
            by_basename.setdefault(normalized.rsplit("/", 1)[-1].casefold(), []).append(exact)
        for item in loras:
            normalized = normalize_lora_path(item["filename"])
            exact = available.get(normalized.casefold())
            if exact is None and "/" not in normalized:
                matches = list(dict.fromkeys(by_basename.get(normalized.casefold(), [])))
                if len(matches) == 1:
                    exact = matches[0]
                elif len(matches) > 1:
                    raise WorkflowError(
                        f"LoRA 文件名存在多个子目录匹配，请重新选择: {item['filename']}"
                    )
            if exact is None:
                raise WorkflowError(f"LoRA 文件不存在: {item['filename']}")
            item["filename"] = exact
    return loras


def _validate_hires(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowError("hires 必须是对象")
    unknown = set(value) - set(DEFAULT_HIRES)
    if unknown:
        raise WorkflowError(f"未知高清修复参数: {', '.join(sorted(unknown))}")
    merged = {**DEFAULT_HIRES, **value}
    return {
        "enabled": _boolean("hires.enabled", merged["enabled"]),
        "model_name": _text("hires.model_name", merged["model_name"]),
        "percent": _integer("hires.percent", merged["percent"], 1, 1000),
    }


def _validate_detailers(value: Any) -> dict[str, bool]:
    if not isinstance(value, dict):
        raise WorkflowError("detailers 必须是对象")
    unknown = set(value) - set(DEFAULT_DETAILERS)
    if unknown:
        raise WorkflowError(f"未知细节修复模块: {', '.join(sorted(unknown))}")
    merged = {**DEFAULT_DETAILERS, **value}
    return {name: _boolean(f"detailers.{name}", merged[name]) for name in DETAILER_ORDER}


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
    settings["model_name"] = _text("model_name", settings["model_name"])
    if not settings["model_name"]:
        raise WorkflowError("model_name 不能为空")
    settings["loras"] = validate_loras(settings)
    settings["hires"] = _validate_hires(settings["hires"])
    settings["detailers"] = _validate_detailers(settings["detailers"])

    if settings["character_detail"] not in {"trigger", "trigger_tags"}:
        raise WorkflowError("character_detail 必须是 trigger 或 trigger_tags")

    settings["manual_artist"] = normalize_artist_tags(settings["manual_artist"])
    for name in (
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


def _prepare_hires_nodes(api: dict[str, Any], ui: dict[str, Any]) -> None:
    existing = api.get(str(HIRES_SCALE_ID), {})
    existing_inputs = existing.get("inputs", {})
    if existing.get("class_type") == "ImageScaleBy":
        model_name = api.get(str(HIRES_MODEL_ID), {}).get("inputs", {}).get(
            "model_name", DEFAULT_HIRES["model_name"]
        )
        percent = round(float(existing_inputs.get("scale_by", DEFAULT_HIRES["percent"] / 100)) * 100)
    else:
        model_name = existing_inputs.get("model_name", DEFAULT_HIRES["model_name"])
        percent = existing_inputs.get("percent", DEFAULT_HIRES["percent"])
    api[str(HIRES_MODEL_ID)] = {
        "inputs": {"model_name": model_name},
        "class_type": "UpscaleModelLoader",
        "_meta": {"title": "高清修复模型"},
    }
    api[str(HIRES_UPSCALE_ID)] = {
        "inputs": {
            "upscale_model": [str(HIRES_MODEL_ID), 0],
            "image": ["48", 0],
        },
        "class_type": "ImageUpscaleWithModel",
        "_meta": {"title": "高清模型放大"},
    }
    api[str(HIRES_SCALE_ID)] = {
        "inputs": {
            "image": [str(HIRES_UPSCALE_ID), 0],
            "upscale_method": "lanczos",
            "scale_by": percent / 100,
        },
        "class_type": "ImageScaleBy",
        "_meta": {"title": "高清输出比例"},
    }
    api["12"]["inputs"]["images"] = [str(HIRES_SCALE_ID), 0]

    scale_node = _visual_node(ui, HIRES_SCALE_ID)
    if scale_node.get("type") == "ImageScaleBy":
        _visual_node(ui, HIRES_MODEL_ID)
        _visual_node(ui, HIRES_UPSCALE_ID)
        ui["last_node_id"] = max(int(ui.get("last_node_id", 0)), HIRES_UPSCALE_ID)
        return
    image_link = next(item["link"] for item in scale_node["inputs"] if item["name"] == "image")
    model_link = next(item["link"] for item in scale_node["inputs"] if item["name"] == "vae")
    output_links = next(item["links"] for item in scale_node["outputs"] if item["name"] == "image")
    scale_link = max((int(link[0]) for link in ui.get("links", [])), default=0) + 1

    for link in ui["links"]:
        if int(link[0]) == image_link:
            link[3:6] = [HIRES_UPSCALE_ID, 1, "IMAGE"]
        elif int(link[0]) == model_link:
            link[1:6] = [HIRES_MODEL_ID, 0, HIRES_UPSCALE_ID, 0, "UPSCALE_MODEL"]
        elif int(link[0]) in output_links:
            link[1] = HIRES_SCALE_ID
            link[2] = 0
    ui["links"].append([scale_link, HIRES_UPSCALE_ID, 0, HIRES_SCALE_ID, 0, "IMAGE"])

    vae_node = _visual_node(ui, 22)
    for output in vae_node.get("outputs", []):
        links = output.get("links")
        if isinstance(links, list) and model_link in links:
            output["links"] = [link for link in links if link != model_link] or None

    x, y = scale_node["pos"]
    common_properties = {"cnr_id": "comfy-core", "ver": "0.11.0"}
    ui["nodes"].extend(
        [
            {
                "id": HIRES_MODEL_ID,
                "type": "UpscaleModelLoader",
                "pos": [x - 650, y],
                "size": [300, 82],
                "flags": {},
                "order": scale_node.get("order", 0),
                "mode": scale_node.get("mode", 0),
                "inputs": [],
                "outputs": [{"name": "UPSCALE_MODEL", "type": "UPSCALE_MODEL", "links": [model_link]}],
                "properties": {**common_properties, "Node name for S&R": "UpscaleModelLoader"},
                "widgets_values": [model_name],
                "title": "高清修复模型",
            },
            {
                "id": HIRES_UPSCALE_ID,
                "type": "ImageUpscaleWithModel",
                "pos": [x - 330, y],
                "size": [300, 82],
                "flags": {},
                "order": scale_node.get("order", 0) + 1,
                "mode": scale_node.get("mode", 0),
                "inputs": [
                    {"name": "upscale_model", "type": "UPSCALE_MODEL", "link": model_link},
                    {"name": "image", "type": "IMAGE", "link": image_link},
                ],
                "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": [scale_link]}],
                "properties": {**common_properties, "Node name for S&R": "ImageUpscaleWithModel"},
                "widgets_values": [],
                "title": "高清模型放大",
            },
        ]
    )
    scale_node.update(
        {
            "type": "ImageScaleBy",
            "size": [300, 110],
            "inputs": [{"name": "image", "type": "IMAGE", "link": scale_link}],
            "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": output_links}],
            "properties": {**common_properties, "Node name for S&R": "ImageScaleBy"},
            "widgets_values": ["lanczos", percent / 100],
            "title": "高清输出比例",
        }
    )
    ui["last_node_id"] = max(int(ui.get("last_node_id", 0)), HIRES_UPSCALE_ID)
    ui["last_link_id"] = max(int(ui.get("last_link_id", 0)), scale_link)


def prepare_templates(
    api_source: dict[str, Any], ui_source: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    api = copy.deepcopy(api_source)
    ui = copy.deepcopy(ui_source)

    for node_id in ("3", "4"):
        api.pop(node_id, None)
    _prepare_hires_nodes(api, ui)
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


def _set_ui_mode(ui: dict[str, Any], node_id: int, enabled: bool) -> None:
    _visual_node(ui, node_id)["mode"] = 0 if enabled else 4


def _ui_widgets(ui: dict[str, Any], node_id: int) -> list[Any]:
    return list(_visual_node(ui, node_id).get("widgets_values") or [])


def _detailer_nodes(
    ui: dict[str, Any], name: str, image: list[Any]
) -> tuple[dict[str, dict[str, Any]], list[Any]]:
    config = DETAILER_NODES[name]
    detector_id = config["detector"]
    editor_id = config["editor"]
    detailer_id = config["detailer"]
    detector_ui = _visual_node(ui, detector_id)
    detector_widgets = _ui_widgets(ui, detector_id)
    editor_widgets = _ui_widgets(ui, editor_id)
    detailer_widgets = _ui_widgets(ui, detailer_id)
    detector_type = detector_ui["type"]
    detector = {
        "inputs": {"model_name": detector_widgets[0]},
        "class_type": detector_type,
        "_meta": {"title": detector_ui.get("title") or detector_type},
    }
    editor_inputs = {
        "wildcard": editor_widgets[0],
        "Select to add LoRA": "Select the LoRA to add to the text",
        "Select to add Wildcard": "Select the Wildcard to add to the text",
        "detailer_pipe": ["18", 0],
        config["detector_input"]: [str(detector_id), 0],
    }
    editor = {
        "inputs": editor_inputs,
        "class_type": "EditDetailerPipe",
        "_meta": {"title": _visual_node(ui, editor_id).get("title") or f"{name.title()} Detailer Pipe"},
    }
    detailer_inputs = {
        "image": image,
        "detailer_pipe": [str(editor_id), 0],
        "guide_size": detailer_widgets[0],
        "guide_size_for": detailer_widgets[1],
        "max_size": detailer_widgets[2],
        "seed": ["37", 0],
        "steps": detailer_widgets[5],
        "cfg": detailer_widgets[6],
        "sampler_name": detailer_widgets[7],
        "scheduler": detailer_widgets[8],
        "denoise": detailer_widgets[9],
        "feather": detailer_widgets[10],
        "noise_mask": detailer_widgets[11],
        "force_inpaint": detailer_widgets[12],
        "bbox_threshold": detailer_widgets[13],
        "bbox_dilation": detailer_widgets[14],
        "bbox_crop_factor": detailer_widgets[15],
        "sam_detection_hint": detailer_widgets[16],
        "sam_dilation": detailer_widgets[17],
        "sam_threshold": detailer_widgets[18],
        "sam_bbox_expansion": detailer_widgets[19],
        "sam_mask_hint_threshold": detailer_widgets[20],
        "sam_mask_hint_use_negative": detailer_widgets[21],
        "drop_size": detailer_widgets[22],
        "refiner_ratio": detailer_widgets[23],
        "cycle": detailer_widgets[24],
        "noise_mask_feather": detailer_widgets[26],
        "tiled_encode": ["50", 0],
        "tiled_decode": ["50", 0],
    }
    detailer = {
        "inputs": detailer_inputs,
        "class_type": "FaceDetailerPipe",
        "_meta": {"title": _visual_node(ui, detailer_id).get("title") or f"{name.title()} Detailer"},
    }
    return {
        str(detector_id): detector,
        str(editor_id): editor,
        str(detailer_id): detailer,
    }, [str(detailer_id), 0]


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
    api["1"]["inputs"]["unet_name"] = settings["model_name"]
    _set_ui_widget(ui, 1, settings["model_name"], 0)
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

    current_image: list[Any] = ["48", 0]
    hires = settings["hires"]
    if hires["enabled"]:
        api[str(HIRES_MODEL_ID)]["inputs"]["model_name"] = hires["model_name"]
        api[str(HIRES_UPSCALE_ID)]["inputs"]["image"] = current_image
        api[str(HIRES_SCALE_ID)]["inputs"]["scale_by"] = hires["percent"] / 100
        current_image = [str(HIRES_SCALE_ID), 0]
    else:
        for node_id in (HIRES_MODEL_ID, HIRES_UPSCALE_ID, HIRES_SCALE_ID):
            api.pop(str(node_id), None)
    for node_id in (HIRES_MODEL_ID, HIRES_UPSCALE_ID, HIRES_SCALE_ID):
        _set_ui_mode(ui, node_id, hires["enabled"])
    _set_ui_widget(ui, HIRES_MODEL_ID, hires["model_name"], 0)
    _set_ui_widget(ui, HIRES_SCALE_ID, hires["percent"] / 100, 1)

    for name in DETAILER_ORDER:
        config = DETAILER_NODES[name]
        enabled = settings["detailers"][name]
        for node_id in (config["detector"], config["editor"], config["detailer"]):
            _set_ui_mode(ui, node_id, enabled)
        if enabled:
            nodes, current_image = _detailer_nodes(ui, name, current_image)
            api.update(nodes)
        else:
            for node_id in (config["detector"], config["editor"], config["detailer"]):
                api.pop(str(node_id), None)
    api["12"]["inputs"]["images"] = current_image
    _set_ui_mode(ui, 52, False)

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
