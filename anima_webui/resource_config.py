"""Optional ComfyUI-relative resource mappings for source installations."""

from __future__ import annotations

import copy
from typing import Any

from .workflow import (
    DEFAULT_SETTINGS,
    WorkflowError,
    WorkflowTemplates,
    normalize_lora_path,
)


def configure_resources(
    templates: WorkflowTemplates, mapping: dict[str, str] | None
) -> dict[str, Any]:
    defaults = copy.deepcopy(DEFAULT_SETTINGS)
    templates.resource_paths = {}
    if mapping is None:
        return defaults
    if not isinstance(mapping, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in mapping.items()
    ):
        raise WorkflowError("资源映射必须是字符串字典")
    paths = {normalize_lora_path(k): normalize_lora_path(v) for k, v in mapping.items()}
    templates.resource_paths = paths

    def replace(value: Any) -> Any:
        if isinstance(value, str):
            return paths.get(value, value)
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value

    templates.api = replace(templates.api)
    return replace(defaults)
