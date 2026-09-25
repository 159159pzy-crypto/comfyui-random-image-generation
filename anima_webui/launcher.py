"""Optional startup resource mapping supplied by the Windows launcher."""
from __future__ import annotations

import copy
from typing import Any

from .workflow import DEFAULT_SETTINGS, WorkflowError, WorkflowTemplates, normalize_lora_path


def configure_resources(templates: WorkflowTemplates, mapping: dict[str, str] | None) -> dict[str, Any]:
    defaults = copy.deepcopy(DEFAULT_SETTINGS)
    if mapping is None:
        return defaults
    if not isinstance(mapping, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in mapping.items()):
        raise WorkflowError("启动器模型映射必须是字符串字典")
    paths = {normalize_lora_path(key): normalize_lora_path(value) for key, value in mapping.items()}

    def replace(value: Any) -> Any:
        if isinstance(value, str):
            return paths.get(value, value)
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value

    templates.api = replace(templates.api)
    templates.ui = replace(templates.ui)
    defaults = replace(defaults)
    # A fresh install may deliberately omit the optional upscaler.
    defaults["hires"]["enabled"] = DEFAULT_SETTINGS["hires"]["model_name"] in paths
    return defaults
