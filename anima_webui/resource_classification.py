"""Join ComfyUI loader choices to LoRA Manager's model metadata."""
from __future__ import annotations

import ntpath
import posixpath
import re
from typing import Any

from .workflow import normalize_lora_path


RESOURCE_METADATA_FIELDS = frozenset({
    "name", "filename", "normalized_path", "folder", "basename", "file_path",
    "base_model", "model_profile", "classification_source", "classification_status",
    "classification_reason", "display_name", "preview", "has_preview",
    "preview_nsfw_level", "trigger_words", "trigger_metadata_available",
    "metadata_source", "size", "sha256", "modified",
})


def technical_resource_metadata(item: dict[str, Any]) -> dict[str, Any]:
    """Publish model facts without importing the plugin's personal library."""
    return {key: value for key, value in item.items() if key in RESOURCE_METADATA_FIELDS}


def classify_base_model(value: str | None) -> tuple[str | None, str, str]:
    base_model = (value or "").strip()
    key = re.sub(r"[\s_-]+", "", base_model).casefold()
    if key == "anima":
        return "anima", "recognized", ""
    if not key or key == "unknown":
        return None, "unknown", "LoRA Manager 尚未识别架构，请在插件中补充 base_model"
    return None, "unsupported", f"LoRA Manager 标注为 {base_model}，当前仅支持 Anima"


def _absolute_path(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError("LoRA Manager 返回了无效的模型绝对路径")
    path = value.strip().replace("\\", "/")
    drive, tail = ntpath.splitdrive(path)
    if drive:
        if not (drive.startswith("//") or re.fullmatch(r"[a-zA-Z]:", drive)):
            raise ValueError("LoRA Manager 返回了无效的模型绝对路径")
        if tail and not tail.startswith("/"):
            raise ValueError("LoRA Manager 返回了非绝对模型路径")
        if not tail and not drive.startswith("//"):
            raise ValueError("LoRA Manager 返回了非绝对模型路径")
    elif not path.startswith("/"):
        raise ValueError("LoRA Manager 返回了非绝对模型路径")
    if ".." in path.split("/"):
        raise ValueError("LoRA Manager 返回的模型路径包含上级目录")
    return posixpath.normpath(path)


def _path_key(path: str) -> str:
    # Windows drives and UNC shares are case-insensitive; POSIX roots are not.
    return path.casefold() if ntpath.splitdrive(path)[0] else path


def _trigger_words(metadata: dict[str, Any]) -> list[str]:
    civitai = metadata.get("civitai")
    values = civitai.get("trainedWords") if isinstance(civitai, dict) else []
    if not isinstance(values, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, str):
            word = value.strip()
            if word and word.casefold() not in seen:
                result.append(word)
                seen.add(word.casefold())
    return result


def classify_resources(
    names: list[str], manager_items: list[dict[str, Any]], roots: list[str]
) -> list[dict[str, Any]]:
    """Match only contained, exact relative paths; never guess from basenames."""
    normalized_roots = {_path_key(_absolute_path(root)): _absolute_path(root) for root in roots}
    ordered_roots = sorted(normalized_roots, key=len, reverse=True)
    matches: dict[str, dict[str, dict[str, Any]]] = {}
    for metadata in manager_items:
        if not isinstance(metadata, dict):
            raise ValueError("LoRA Manager 返回了无效清单条目")
        path = _absolute_path(metadata.get("file_path"))
        if metadata.get("base_model") is not None and not isinstance(metadata["base_model"], str):
            raise ValueError("LoRA Manager 返回了无效 base_model")
        key = _path_key(path)
        for root in ordered_roots:
            prefix = root.rstrip("/") + "/"
            if key.startswith(prefix):
                relative = path[len(normalized_roots[root].rstrip("/")) + 1:]
                identity = relative.casefold() if ntpath.splitdrive(path)[0] else relative
                matches.setdefault(identity, {}).setdefault(key, metadata)

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    windows_roots = any(ntpath.splitdrive(root)[0] for root in normalized_roots.values())
    for filename in names:
        normalized = normalize_lora_path(filename)
        identity = normalized.casefold() if windows_roots else normalized
        if identity in seen:
            continue
        seen.add(identity)
        candidates = list(matches.get(identity, {}).values())
        metadata = candidates[0] if len(candidates) == 1 else {}
        base_model = str(metadata.get("base_model") or "").strip()
        if len(candidates) > 1:
            profile, status, reason = None, "ambiguous", "LoRA Manager 中多个模型映射到同一加载路径，请在插件中整理重名资源"
        elif not candidates:
            profile, status, reason = None, "unindexed", "LoRA Manager 尚未收录此加载路径，请在插件中扫描并确认模型目录"
        else:
            profile, status, reason = classify_base_model(base_model)
        preview = str(metadata.get("preview_url") or "")
        items.append({
            "name": normalized,
            "filename": filename,
            "normalized_path": normalized,
            "folder": normalized.rpartition("/")[0],
            "basename": normalized.rsplit("/", 1)[-1],
            "file_path": str(metadata.get("file_path") or ""),
            "base_model": base_model,
            "model_profile": profile,
            "classification_source": "lora-manager",
            "classification_status": status,
            "classification_reason": reason,
            "display_name": str(metadata.get("model_name") or normalized.rsplit("/", 1)[-1].rsplit(".", 1)[0]),
            "preview": preview,
            "has_preview": bool(preview),
            "preview_nsfw_level": metadata.get("preview_nsfw_level", 0),
            "trigger_words": _trigger_words(metadata),
            "trigger_metadata_available": bool(metadata),
            "metadata_source": "lora-manager",
            "size": metadata.get("file_size"),
            "sha256": str(metadata.get("sha256") or ""),
            "modified": metadata.get("modified", 0),
        })
    return items
