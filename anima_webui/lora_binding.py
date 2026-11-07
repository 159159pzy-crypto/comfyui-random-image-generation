"""Per-image character LoRA resolution, separate from mutable library state."""
from __future__ import annotations

import copy
from typing import Any

from .catalog import split_prompt
from .workflow import MAX_LORAS, MAX_LORA_MANAGED_TRIGGERS, WorkflowError, normalize_lora_path, validate_loras


def binding_policy(regeneration: dict[str, Any] | None) -> str:
    mode = (regeneration or {}).get("mode")
    if mode in {"replay", "prompt_variant"}:
        return "frozen"
    if mode == "content_redraw" and "character" not in regeneration.get("redraw_sections", []):
        return "preserve_character"
    return "current"


def runtime_snapshot(regeneration: dict[str, Any] | None) -> dict[str, Any]:
    value = (regeneration or {}).get("lora_runtime") or {}
    return copy.deepcopy(value) if isinstance(value, dict) and value.get("version") == 1 else {}


def manual_settings(settings: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    """Recover only known manual inputs; never infer provenance for old images."""
    value = copy.deepcopy(settings)
    if runtime.get("version") == 1:
        if "manual_loras" not in runtime or "manual_managed_triggers" not in runtime:
            raise WorkflowError("历史 LoRA 运行快照缺少手动配置")
        value["loras"] = validate_loras({"loras": runtime["manual_loras"]})
        value["lora_managed_triggers"] = copy.deepcopy(runtime["manual_managed_triggers"])
    return value


def _identity(filename: Any) -> str:
    return normalize_lora_path(filename).casefold()


def _words(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        if isinstance(value, str) and (word := value.strip()) and word.casefold() not in seen:
            result.append(word)
            seen.add(word.casefold())
    return result


def append_trigger_words(resolved: dict[str, Any], words: list[str]) -> dict[str, Any]:
    value = copy.deepcopy(resolved)
    for field in ("composer_prompt", "full_prompt"):
        prompt = str(value.get(field) or "")
        seen = {word.casefold() for word in split_prompt(prompt)}
        additions = [word for word in _words(words) if word.casefold() not in seen]
        if additions:
            value[field] = ", ".join([part for part in (prompt.rstrip(" ,\r\n"), *additions) if part])
    return value


def inventory_lookup(inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {_identity(item.get("filename") or item.get("name")): item for item in inventory["items"]}


def resource_problem(item: dict[str, Any] | None, expected: dict[str, Any]) -> tuple[str, str] | None:
    if item is None:
        return "missing", "LoRA 文件已失效"
    if item.get("model_profile") != "anima" or item.get("classification_status") not in {None, "recognized"}:
        return "incompatible", "LoRA 不适用于 Anima 人物池"
    expected_hash = str(expected.get("sha256") or "").casefold()
    actual_hash = str(item.get("sha256") or "").casefold()
    if expected_hash and actual_hash and expected_hash != actual_hash:
        return "changed", "LoRA 文件内容已变化，请重新确认绑定"
    return None


def validate_frozen_resources(settings: dict[str, Any], runtime: dict[str, Any], inventory: dict[str, Any]) -> None:
    """Historical enabled resources are required, regardless of their origin."""
    lookup = inventory_lookup(inventory)
    expected = {_identity(item["filename"]): item for item in runtime.get("applied", [])}
    for item in settings.get("loras", []):
        if not item["enabled"]:
            continue
        key = _identity(item["filename"])
        problem = resource_problem(lookup.get(key), expected.get(key, item))
        if problem:
            raise WorkflowError(f"历史 LoRA 不可用: {item['filename']}；{problem[1]}")


def resolve_character_loras(settings: dict[str, Any], selected: dict[str, Any], snapshot: dict[str, Any],
                            inventory: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    effective = copy.deepcopy(settings)
    manual = copy.deepcopy(settings.get("loras", []))
    runtime: dict[str, Any] = {
        "version": 1, "binding_revision": snapshot.get("revision"),
        "manual_loras": manual, "manual_managed_triggers": copy.deepcopy(settings.get("lora_managed_triggers", [])),
        "applied": [], "skipped": [], "conflicts": [], "automatic_trigger_words": [],
    }
    occupied = {_identity(item["filename"]): "manual" for item in manual}
    lookup = inventory_lookup(inventory)
    bindings = snapshot.get("bindings") or {}
    for character in selected.get("character") or []:
        character_id = character.get("id")
        binding = bindings.get(character_id) or {}
        for item in binding.get("loras") or []:
            if not item.get("enabled", True):
                continue
            filename = normalize_lora_path(item["filename"])
            key = _identity(filename)
            entry = {"character_id": character_id,
                     "character_name": character.get("title") or binding.get("name") or character_id,
                     "id": item.get("id", ""), "filename": filename, "strength": item["strength"],
                     "sha256": item.get("sha256", ""), "trigger_words": _words(item.get("trigger_words"))}
            if key in occupied:
                runtime["conflicts"].append({**entry, "reason": "manual_override" if occupied[key] == "manual" else "duplicate"})
                continue
            problem = resource_problem(lookup.get(key), item)
            if problem is None and len(effective["loras"]) >= MAX_LORAS:
                problem = ("capacity_exceeded", f"已达到 {MAX_LORAS} 项 LoRA 上限")
            words = _words([*runtime["manual_managed_triggers"], *runtime["automatic_trigger_words"], *entry["trigger_words"]])
            if problem is None and len(words) > MAX_LORA_MANAGED_TRIGGERS:
                problem = ("trigger_capacity_exceeded", f"自动触发词超过 {MAX_LORA_MANAGED_TRIGGERS} 项上限")
            if problem:
                runtime["skipped"].append({**entry, "reason": problem[0], "message": problem[1]})
                continue
            actual = lookup[key]
            entry["filename"] = normalize_lora_path(actual.get("filename") or actual["name"])
            effective["loras"].append({"filename": entry["filename"], "strength": entry["strength"], "enabled": True})
            occupied[key] = "automatic"
            runtime["applied"].append(entry)
            runtime["automatic_trigger_words"] = _words([*runtime["automatic_trigger_words"], *entry["trigger_words"]])
    effective["loras"] = validate_loras(effective)
    effective["lora_managed_triggers"] = _words([*runtime["manual_managed_triggers"], *runtime["automatic_trigger_words"]])
    runtime["effective_loras"] = copy.deepcopy(effective["loras"])
    return effective, runtime
