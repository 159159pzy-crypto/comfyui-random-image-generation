"""Conservative AstrBot Provider response normalization.

Only visible final-answer fields are inspected.  Hidden reasoning fields are
deliberately ignored, and Provider failures are reported by stable codes rather
than leaking raw upstream responses into logs or user-facing errors.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


_ERROR_ROLES = frozenset({"err", "error", "failed", "failure"})
_ERROR_MARKERS = (
    ("all_models_failed", "all_models_failed"),
    ("all chat models failed", "all_models_failed"),
    ("completion has no choices", "no_choices"),
    ("no choices", "no_choices"),
    ("provider error", "provider_error"),
    ("provider failed", "provider_error"),
)
_VISIBLE_TEXT_FIELDS = ("completion_text", "text", "content", "message")
_VISIBLE_CHAIN_FIELDS = (
    "result_chain",
    "chain",
    "components",
    "items",
    "messages",
    "choices",
)
_IGNORED_ROLES = frozenset({"developer", "system", "tool", "user"})
_IGNORED_TYPES = frozenset(
    {"function_call", "reasoning", "thinking", "tool_call", "tool_result"}
)


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _visible_component_text(value: Any, *, depth: int = 0) -> str:
    if value is None or depth > 4:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        role = str(value.get("role") or "").strip().casefold()
        component_type = str(
            value.get("type") or value.get("component_type") or ""
        ).strip().casefold()
        if role in _IGNORED_ROLES or component_type in _IGNORED_TYPES:
            return ""
        choices = value.get("choices")
        if isinstance(choices, Sequence) and not isinstance(
            choices, (str, bytes, bytearray)
        ):
            for choice in reversed(choices):
                text = _visible_component_text(choice, depth=depth + 1)
                if text:
                    return text
        if component_type in {"json", "json_component", "jsoncomponent"}:
            payload = value.get("data", value.get("value"))
            if isinstance(payload, Mapping):
                try:
                    return json.dumps(payload, ensure_ascii=False)
                except (TypeError, ValueError, RecursionError):
                    return ""
        for key in _VISIBLE_TEXT_FIELDS:
            nested = value.get(key)
            if isinstance(nested, str) and nested.strip():
                return nested
            text = _visible_component_text(nested, depth=depth + 1)
            if text:
                return text
        for key in _VISIBLE_CHAIN_FIELDS:
            nested = value.get(key)
            text = _visible_component_text(nested, depth=depth + 1)
            if text:
                return text
        return ""
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for item in reversed(value):
            text = _visible_component_text(item, depth=depth + 1)
            if text:
                return text
        return ""

    getter = getattr(value, "get_plain_text", None)
    if callable(getter):
        try:
            text = getter()
        except Exception:
            text = ""
        if isinstance(text, str) and text.strip():
            return text
    role = str(getattr(value, "role", "") or "").strip().casefold()
    component_type = str(
        getattr(value, "type", getattr(value, "component_type", "")) or ""
    ).strip().casefold()
    if role in _IGNORED_ROLES or component_type in _IGNORED_TYPES:
        return ""
    choices = getattr(value, "choices", None)
    if isinstance(choices, Sequence) and not isinstance(
        choices, (str, bytes, bytearray)
    ):
        for choice in reversed(choices):
            text = _visible_component_text(choice, depth=depth + 1)
            if text:
                return text
    class_name = type(value).__name__.casefold()
    if component_type in {"json", "json_component", "jsoncomponent"} or class_name in {
        "json",
        "jsoncomponent",
        "json_component",
    }:
        payload = getattr(value, "data", getattr(value, "value", None))
        if isinstance(payload, Mapping):
            try:
                return json.dumps(payload, ensure_ascii=False)
            except (TypeError, ValueError, RecursionError):
                return ""
    for key in _VISIBLE_TEXT_FIELDS:
        nested = getattr(value, key, None)
        if isinstance(nested, str) and nested.strip():
            return nested
        text = _visible_component_text(nested, depth=depth + 1)
        if text:
            return text
    for key in _VISIBLE_CHAIN_FIELDS:
        nested = getattr(value, key, None)
        text = _visible_component_text(nested, depth=depth + 1)
        if text:
            return text
    return ""


def response_text(response: Any) -> str:
    """Return a visible final answer from common AstrBot response wrappers."""

    if isinstance(response, str):
        return response
    for key in _VISIBLE_TEXT_FIELDS:
        value = _field(response, key)
        if isinstance(value, str) and value.strip():
            return value
    if isinstance(response, Mapping) and (
        {"identity_tags", "confidence"}.issubset(response)
        or {"source_identity_ids", "confidence"}.issubset(response)
        or isinstance(response.get("prompt"), str)
        or isinstance(response.get("canonical_identity_tag"), str)
        or isinstance(response.get("identity_tag"), str)
    ):
        try:
            return json.dumps(response, ensure_ascii=False)
        except (TypeError, ValueError, RecursionError):
            return ""
    for key in _VISIBLE_CHAIN_FIELDS:
        text = _visible_component_text(_field(response, key))
        if text:
            return text
    return ""


def _contains_error_role(value: Any, *, depth: int = 0) -> bool:
    if value is None or depth > 4 or isinstance(value, str):
        return False
    role = str(_field(value, "role") or "").strip().casefold()
    component_type = str(
        _field(value, "type") or _field(value, "component_type") or ""
    ).strip().casefold()
    if role in _ERROR_ROLES or component_type in _ERROR_ROLES:
        return True
    if isinstance(value, Mapping):
        nested_values = [
            value.get(key)
            for key in (*_VISIBLE_CHAIN_FIELDS, "choices", "message")
            if value.get(key) is not None
        ]
    elif isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        nested_values = list(value)
    else:
        nested_values = [
            getattr(value, key, None)
            for key in (*_VISIBLE_CHAIN_FIELDS, "choices", "message")
            if getattr(value, key, None) is not None
        ]
    return any(
        _contains_error_role(item, depth=depth + 1) for item in nested_values
    )


def response_error_code(response: Any) -> str:
    """Return a stable Provider failure code without exposing response content."""

    if _contains_error_role(response):
        return "error_role"
    text = response_text(response)
    folded = re.sub(r"\s+", " ", text).strip().casefold()
    for marker, code in _ERROR_MARKERS:
        if marker in folded:
            return code
    return ""


__all__ = ["response_error_code", "response_text"]
