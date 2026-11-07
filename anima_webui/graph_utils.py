from __future__ import annotations
from typing import Any


def _prune_graph(api: dict[str, Any], sink: str) -> dict[str, Any]:
    from .workflow import WorkflowError

    required: set[str] = set()
    pending = [sink]
    while pending:
        node_id = pending.pop()
        if node_id in required:
            continue
        if node_id not in api:
            raise WorkflowError(f"工作流缺少节点: {node_id}")
        required.add(node_id)
        for value in api[node_id].get("inputs", {}).values():
            if (
                isinstance(value, list)
                and len(value) == 2
                and isinstance(value[0], str)
                and isinstance(value[1], int)
            ):
                pending.append(value[0])
    return {key: value for key, value in api.items() if key in required}


def active_lora_pipelines(settings: dict[str, Any]) -> frozenset[str]:
    """The random workbench has one shared LoRA stack."""
    return frozenset({"primary"})
