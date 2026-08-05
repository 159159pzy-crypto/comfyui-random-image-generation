"""
AstrBot Comfy Anima 插件 v1.2.0

功能描述：
- 解析提示词中的 LoRA 标签
- 将动态 LoRA 注入 Anima 工作流的 LoraManager 节点

作者: Yen
版本: 1.2.0
日期: 2026-07-19
"""

import re
from typing import Any

from ..models import LoraSelection


LORA_TAG_PATTERN = re.compile(
    r"<lora:([^<>:]+):([+-]?(?:\d+(?:\.\d+)?|\.\d+))>",
    flags=re.IGNORECASE,
)
LORA_EXTENSIONS = (".safetensors", ".ckpt", ".pt", ".bin")


class LoraWorkflowError(ValueError):
    """LoRA 标签或工作流注入配置无效。"""


def canonical_lora_name(name: str) -> str:
    """规范化 LoRA 名称，用于清单匹配与去重。"""
    normalized = str(name).strip().replace("\\", "/")
    lower = normalized.lower()
    for extension in LORA_EXTENSIONS:
        if lower.endswith(extension):
            normalized = normalized[: -len(extension)]
            break
    return normalized.strip(" /.")


def extract_lora_selections(
    prompt: str, max_loras: int = 3
) -> tuple[str, tuple[LoraSelection, ...]]:
    """提取并移除提示词中的动态 LoRA 标签。

    Args:
        prompt: LLM 或用户生成的完整提示词。
        max_loras: 单次允许的最大 LoRA 数量。

    Returns:
        清理后的绘图提示词与去重后的 LoRA 选择。
    """
    if max_loras < 0:
        raise LoraWorkflowError("max_loras 不能为负数")
    selections: list[LoraSelection] = []
    seen: set[str] = set()
    for match in LORA_TAG_PATTERN.finditer(prompt):
        name = canonical_lora_name(match.group(1))
        if not name:
            raise LoraWorkflowError("LoRA 名称不能为空")
        strength = float(match.group(2))
        if not 0.0 <= strength <= 2.0:
            raise LoraWorkflowError("LoRA 权重必须在 0 到 2 之间")
        key = name.casefold()
        if key in seen:
            continue
        if len(selections) >= max_loras:
            raise LoraWorkflowError(f"单次最多允许 {max_loras} 个动态 LoRA")
        seen.add(key)
        selections.append(LoraSelection(name=name, strength=strength))

    cleaned = LORA_TAG_PATTERN.sub("", prompt)
    cleaned = re.sub(r"(?:\s*,\s*){2,}", ", ", cleaned)
    cleaned = re.sub(r"^\s*,|,\s*$", "", cleaned).strip()
    return cleaned, tuple(selections)


def inject_loras(
    workflow: dict[str, Any],
    node_id: str,
    selections: tuple[LoraSelection, ...],
    mode: str = "append",
) -> None:
    """将 LoRA 选择注入 LoraManager API 节点。

    Args:
        workflow: 已深拷贝的单次 ComfyUI 工作流。
        node_id: `Lora Loader (LoraManager)` 节点 ID。
        selections: 本次动态 LoRA。
        mode: `append` 保留基础 LoRA，`replace` 仅保留动态 LoRA。
    """
    if mode not in {"append", "replace"}:
        raise LoraWorkflowError("动态 LoRA 模式必须是 append 或 replace")
    if not selections and mode == "append":
        return
    node = workflow.get(str(node_id))
    if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
        raise LoraWorkflowError(f"工作流缺少 LoRA 节点 {node_id}")
    inputs = node["inputs"]
    raw_wrapper = inputs.get("loras")
    if isinstance(raw_wrapper, dict) and isinstance(raw_wrapper.get("__value__"), list):
        records = list(raw_wrapper["__value__"])
    else:
        records = []
        raw_wrapper = {"__value__": records}

    text = str(inputs.get("text", "")).strip()
    if mode == "replace":
        records = []
        text = ""

    record_map: dict[str, dict[str, Any]] = {}
    for record in records:
        if isinstance(record, dict) and record.get("name"):
            record_map[canonical_lora_name(str(record["name"])).casefold()] = record

    for selection in selections:
        key = canonical_lora_name(selection.name).casefold()
        record = record_map.get(key)
        if record is None:
            record = {
                "name": selection.name,
                "strength": selection.strength,
                "active": True,
                "expanded": False,
                "clipStrength": selection.strength,
                "locked": False,
            }
            records.append(record)
            record_map[key] = record
        else:
            record["strength"] = selection.strength
            record["clipStrength"] = selection.strength
            record["active"] = True

        replaced = False

        def replace_existing(match: re.Match[str]) -> str:
            nonlocal replaced
            if canonical_lora_name(match.group(1)).casefold() != key:
                return match.group(0)
            replaced = True
            return f"<lora:{selection.name}:{selection.strength:g}>"

        text = LORA_TAG_PATTERN.sub(replace_existing, text)
        if not replaced:
            tag = f"<lora:{selection.name}:{selection.strength:g}>"
            text = f"{text}, {tag}" if text else tag

    raw_wrapper["__value__"] = records
    inputs["loras"] = raw_wrapper
    inputs["text"] = text
