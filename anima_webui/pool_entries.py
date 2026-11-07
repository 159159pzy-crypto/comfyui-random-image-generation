"""Pure validation shared by prompt imports and structured AI responses."""
from __future__ import annotations

from typing import Any


def normalize_prompt_entry(value: Any, *, require_title: bool = False) -> dict[str, str]:
    """Validate display metadata without changing the prompt's language or syntax."""
    if isinstance(value, str) and not require_title:
        value = {"prompt": value}
    if not isinstance(value, dict):
        raise ValueError("提示词条目必须是对象")
    if set(value) - {"title", "subtitle", "prompt", "text"}:
        raise ValueError("提示词条目包含未知字段")
    if "prompt" in value and "text" in value:
        raise ValueError("提示词条目不能同时包含 text 和 prompt")
    prompt = value.get("prompt", value.get("text"))
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("提示词正文不能为空且必须是字符串")
    prompt = prompt.strip()
    if len(prompt) > 20_000:
        raise ValueError("提示词正文不能超过 20000 个字符")
    title = value.get("title")
    if title is None and not require_title:
        title = next((line.strip() for line in prompt.splitlines() if line.strip()), prompt)[:200]
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200:
        raise ValueError("名称需要 1-200 个字符")
    subtitle = value.get("subtitle", "")
    if not isinstance(subtitle, str) or len(subtitle.strip()) > 300:
        raise ValueError("副标题必须是最多 300 个字符的字符串")
    return {"title": title.strip(), "subtitle": subtitle.strip(), "prompt": prompt}


def normalize_group_names(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.split("|") if value.strip() else []
    if not isinstance(value, list) or len(value) > 512:
        raise ValueError("分组必须是数组，最多 512 项")
    names: list[str] = []
    seen: set[str] = set()
    for name in value:
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
            raise ValueError("分组名称需要 1-100 个字符")
        key = name.strip().casefold()
        if key not in seen:
            names.append(name.strip())
            seen.add(key)
    return names
