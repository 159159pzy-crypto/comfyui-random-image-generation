"""Fail-closed structured payload extraction for LLM Provider responses.

The adapter deliberately understands only visible, final-answer surfaces and
explicit function/tool-call envelopes.  It never inspects reasoning fields or
tries to recover a JSON object from explanatory prose.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


_TOOL_NAME_RE = re.compile(r"[A-Za-z0-9_.:\-]{1,128}")
_VISIBLE_TEXT_FIELDS = ("completion_text", "text", "content")
_MAX_JSON_CHARS = 32_000
_MAX_DEPTH = 32
_MAX_ITEMS = 4_096


class StructuredProviderError(ValueError):
    """A Provider response cannot be trusted as one structured invocation."""

    def __init__(self, user_message: str, *, code: str) -> None:
        self.user_message = user_message
        self.code = code
        super().__init__(user_message)


@dataclass(frozen=True)
class StructuredProviderPayload:
    """One validated structured payload and its visible response source."""

    arguments: Mapping[str, Any]
    source: str
    tool_name: str = ""


@dataclass(frozen=True)
class _ToolCallCandidate:
    name: str
    arguments: Mapping[str, Any]
    source: str


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _has_field(value: Any, name: str) -> bool:
    if isinstance(value, Mapping):
        return name in value
    try:
        getattr(value, name)
    except (AttributeError, TypeError):
        return False
    return True


def _error(message: str, code: str) -> StructuredProviderError:
    return StructuredProviderError(message, code=code)


def _json_object_from_text(value: str, *, source: str) -> Mapping[str, Any]:
    text = str(value or "").strip()
    if not text:
        raise _error("Provider 没有返回结构化参数", "empty_payload")
    if len(text) > _MAX_JSON_CHARS:
        raise _error("Provider 结构化参数过长", "payload_too_large")

    fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", text, re.I | re.S)
    if fenced:
        text = fenced.group(1)

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise _error(
                    "Provider 结构化参数含有重复字段",
                    "duplicate_json_key",
                )
            result[key] = item
        return result

    try:
        payload = json.loads(
            text,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                _error("Provider 结构化参数含有非有限数字", "non_finite_number")
            ),
        )
    except StructuredProviderError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise _error(
            f"Provider 的{source}不是合法 JSON 对象",
            "invalid_json",
        ) from exc
    if not isinstance(payload, Mapping):
        raise _error("Provider 结构化参数必须是 JSON 对象", "payload_not_object")
    return _normalize_json_object(payload)


def _normalize_json_object(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Copy one JSON-compatible mapping while rejecting cycles and exotic values."""

    active: set[int] = set()
    item_count = 0

    def normalize(item: Any, depth: int) -> Any:
        nonlocal item_count
        item_count += 1
        if item_count > _MAX_ITEMS:
            raise _error("Provider 结构化参数项目过多", "payload_too_complex")
        if depth > _MAX_DEPTH:
            raise _error("Provider 结构化参数嵌套过深", "payload_too_deep")
        if item is None or isinstance(item, (str, bool, int)):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise _error(
                    "Provider 结构化参数含有非有限数字",
                    "non_finite_number",
                )
            return item
        if isinstance(item, Mapping):
            identity = id(item)
            if identity in active:
                raise _error("Provider 结构化参数含有循环引用", "cyclic_payload")
            active.add(identity)
            try:
                result: dict[str, Any] = {}
                for key, nested in item.items():
                    if not isinstance(key, str):
                        raise _error(
                            "Provider 结构化参数字段名必须是字符串",
                            "invalid_object_key",
                        )
                    if key in result:
                        raise _error(
                            "Provider 结构化参数含有重复字段",
                            "duplicate_json_key",
                        )
                    result[key] = normalize(nested, depth + 1)
                return result
            finally:
                active.remove(identity)
        if isinstance(item, Sequence) and not isinstance(
            item,
            (str, bytes, bytearray),
        ):
            identity = id(item)
            if identity in active:
                raise _error("Provider 结构化参数含有循环引用", "cyclic_payload")
            active.add(identity)
            try:
                return [normalize(nested, depth + 1) for nested in item]
            finally:
                active.remove(identity)
        raise _error(
            "Provider 结构化参数含有非 JSON 类型",
            "non_json_value",
        )

    normalized = normalize(value, 0)
    assert isinstance(normalized, Mapping)
    return normalized


def _arguments_object(value: Any, *, source: str) -> Mapping[str, Any]:
    if isinstance(value, str):
        return _json_object_from_text(value, source=f"{source} arguments")
    if isinstance(value, Mapping):
        return _normalize_json_object(value)
    raise _error("Provider 工具参数必须是 JSON 对象", "arguments_not_object")


def _tool_candidate(value: Any, *, source: str) -> _ToolCallCandidate:
    function = _field(value, "function")
    envelope = function if function is not None else value
    name = _field(envelope, "name")
    arguments = _field(envelope, "arguments")
    if arguments is None:
        arguments = _field(envelope, "args")
    if not isinstance(name, str) or not _TOOL_NAME_RE.fullmatch(name.strip()):
        raise _error("Provider 工具调用名称无效", "invalid_tool_name")
    if arguments is None:
        raise _error("Provider 工具调用缺少参数", "missing_tool_arguments")
    return _ToolCallCandidate(
        name=name.strip(),
        arguments=_arguments_object(arguments, source=source),
        source=source,
    )


def _append_tool_call_list(
    result: list[_ToolCallCandidate],
    raw_calls: Any,
    *,
    source: str,
) -> None:
    if raw_calls is None:
        return
    if not isinstance(raw_calls, Sequence) or isinstance(
        raw_calls,
        (str, bytes, bytearray),
    ):
        raise _error("Provider tool_calls 必须是数组", "invalid_tool_calls")
    for index, item in enumerate(raw_calls):
        result.append(_tool_candidate(item, source=f"{source}[{index}]"))


def _append_astrbot_tool_calls(
    result: list[_ToolCallCandidate],
    names: Any,
    arguments: Any,
) -> None:
    """Append AstrBot's native tool-call fields.

    AstrBot 4.26.x exposes ``LLMResponse.tools_call_name`` and
    ``LLMResponse.tools_call_args`` as parallel lists.  Older/plugin-local
    adapters used scalar values, so both representations are accepted, but
    mixed scalar/list values and mismatched list lengths fail closed.
    """

    names_is_list = isinstance(names, Sequence) and not isinstance(
        names, (str, bytes, bytearray)
    )
    args_is_list = isinstance(arguments, Sequence) and not isinstance(
        arguments, (str, bytes, bytearray, Mapping)
    )

    if names_is_list or args_is_list:
        if not (names_is_list and args_is_list):
            raise _error(
                "AstrBot Provider 工具调用字段类型不一致",
                "malformed_astrbot_tool_call",
            )
        if len(names) != len(arguments):
            raise _error(
                "AstrBot Provider 工具调用名称与参数数量不一致",
                "malformed_astrbot_tool_call",
            )
        for index, (name, call_args) in enumerate(zip(names, arguments)):
            if not isinstance(name, str) or not _TOOL_NAME_RE.fullmatch(name.strip()):
                raise _error(
                    "Provider 工具调用名称无效",
                    "invalid_tool_name",
                )
            if call_args is None:
                raise _error(
                    "Provider 工具调用缺少参数",
                    "missing_tool_arguments",
                )
            result.append(
                _ToolCallCandidate(
                    name=name.strip(),
                    arguments=_arguments_object(
                        call_args,
                        source=f"astrbot.tools_call_args[{index}]",
                    ),
                    source=f"astrbot.tools_call[{index}]",
                )
            )
        return

    if names in (None, "") and arguments in (None, ""):
        return
    if not isinstance(names, str) or arguments is None:
        raise _error(
            "AstrBot Provider 工具调用字段不完整",
            "malformed_astrbot_tool_call",
        )
    if not _TOOL_NAME_RE.fullmatch(names.strip()):
        raise _error("Provider 工具调用名称无效", "invalid_tool_name")
    result.append(
        _ToolCallCandidate(
            name=names.strip(),
            arguments=_arguments_object(arguments, source="astrbot.tools_call_args"),
            source="astrbot.tools_call",
        )
    )


def _collect_tool_calls(response: Any) -> list[_ToolCallCandidate]:
    calls: list[_ToolCallCandidate] = []

    has_astrbot_name = _has_field(response, "tools_call_name")
    has_astrbot_args = _has_field(response, "tools_call_args")
    astrbot_name = _field(response, "tools_call_name")
    astrbot_args = _field(response, "tools_call_args")
    if has_astrbot_name or has_astrbot_args:
        _append_astrbot_tool_calls(calls, astrbot_name, astrbot_args)

    if _has_field(response, "tool_calls"):
        _append_tool_call_list(
            calls,
            _field(response, "tool_calls"),
            source="response.tool_calls",
        )
    if _has_field(response, "function_call"):
        function_call = _field(response, "function_call")
        if function_call is not None:
            calls.append(
                _tool_candidate(function_call, source="response.function_call")
            )

    choices = _field(response, "choices")
    if choices is not None:
        if not isinstance(choices, Sequence) or isinstance(
            choices,
            (str, bytes, bytearray),
        ):
            raise _error("Provider choices 必须是数组", "invalid_choices")
        for choice_index, choice in enumerate(choices):
            message = _field(choice, "message")
            if message is None:
                continue
            if _has_field(message, "tool_calls"):
                _append_tool_call_list(
                    calls,
                    _field(message, "tool_calls"),
                    source=f"choices[{choice_index}].message.tool_calls",
                )
            if _has_field(message, "function_call"):
                function_call = _field(message, "function_call")
                if function_call is not None:
                    calls.append(
                        _tool_candidate(
                            function_call,
                            source=f"choices[{choice_index}].message.function_call",
                        )
                    )
    return calls


def _component_json_payload(value: Any) -> Mapping[str, Any] | None:
    component_type = str(
        _field(value, "type") or _field(value, "component_type") or ""
    ).strip().casefold()
    if component_type not in {"json", "json_component", "jsoncomponent"}:
        return None
    payload = _field(value, "data")
    if payload is None:
        payload = _field(value, "value")
    if isinstance(payload, Mapping):
        return _normalize_json_object(payload)
    if isinstance(payload, str):
        return _json_object_from_text(payload, source="result_chain JSON component")
    raise _error(
        "result_chain JSON 组件没有对象数据",
        "invalid_result_chain_json",
    )


def _visible_json_candidates(response: Any) -> list[tuple[Mapping[str, Any], str]]:
    candidates: list[tuple[Mapping[str, Any], str]] = []

    if isinstance(response, str):
        candidates.append(
            (_json_object_from_text(response, source="visible text"), "visible_text")
        )
        return candidates

    for field_name in _VISIBLE_TEXT_FIELDS:
        value = _field(response, field_name)
        if isinstance(value, str) and value.strip():
            candidates.append(
                (
                    _json_object_from_text(value, source=field_name),
                    f"response.{field_name}",
                )
            )
            break

    choices = _field(response, "choices")
    if isinstance(choices, Sequence) and not isinstance(
        choices,
        (str, bytes, bytearray),
    ):
        for choice_index, choice in enumerate(choices):
            message = _field(choice, "message")
            content = _field(message, "content") if message is not None else None
            if isinstance(content, str) and content.strip():
                candidates.append(
                    (
                        _json_object_from_text(
                            content,
                            source=f"choices[{choice_index}].message.content",
                        ),
                        f"choices[{choice_index}].message.content",
                    )
                )

    result_chain = _field(response, "result_chain")
    if result_chain is not None:
        if not isinstance(result_chain, Sequence) or isinstance(
            result_chain,
            (str, bytes, bytearray),
        ):
            raise _error("result_chain 必须是数组", "invalid_result_chain")
        for index, component in enumerate(result_chain):
            payload = _component_json_payload(component)
            if payload is not None:
                candidates.append((payload, f"result_chain[{index}].json"))
                continue
            component_type = str(
                _field(component, "type")
                or _field(component, "component_type")
                or ""
            ).strip().casefold()
            if component_type not in {
                "",
                "text",
                "plain",
                "plain_text",
                "plaintext",
                "final",
                "answer",
            }:
                continue
            text = next(
                (
                    _field(component, field_name)
                    for field_name in _VISIBLE_TEXT_FIELDS
                    if isinstance(_field(component, field_name), str)
                    and str(_field(component, field_name)).strip()
                ),
                None,
            )
            if isinstance(text, str):
                candidates.append(
                    (
                        _json_object_from_text(
                            text,
                            source=f"result_chain[{index}] text",
                        ),
                        f"result_chain[{index}].text",
                    )
                )
    return candidates


def _canonical_payload(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def extract_structured_payload(
    response: Any,
    *,
    expected_tool_name: str,
    allow_json_fallback: bool = True,
) -> StructuredProviderPayload:
    """Extract exactly one expected tool invocation or one visible JSON object.

    Explicit tool-call envelopes take precedence.  Mirrored representations of
    the exact same call are deduplicated, while multiple distinct calls, a
    mismatched tool name, or competing JSON payloads are rejected.
    """

    expected = str(expected_tool_name or "").strip()
    if not _TOOL_NAME_RE.fullmatch(expected):
        raise _error("期望工具名称无效", "invalid_expected_tool_name")

    calls = _collect_tool_calls(response)
    unique_calls: dict[tuple[str, str], _ToolCallCandidate] = {}
    for call in calls:
        if call.name != expected:
            raise _error("Provider 调用了非预期工具", "unexpected_tool_name")
        key = (call.name, _canonical_payload(call.arguments))
        unique_calls.setdefault(key, call)
    if len(unique_calls) > 1:
        raise _error("Provider 返回了多个不同的工具调用", "multiple_tool_calls")

    if unique_calls:
        call = next(iter(unique_calls.values()))
        if allow_json_fallback:
            visible = _visible_json_candidates(response)
            differing = [
                payload
                for payload, _source in visible
                if _canonical_payload(payload) != _canonical_payload(call.arguments)
            ]
            if differing:
                raise _error(
                    "Provider 同时返回了互相冲突的工具参数与 JSON",
                    "conflicting_payload_sources",
                )
        return StructuredProviderPayload(
            arguments=call.arguments,
            source=call.source,
            tool_name=call.name,
        )

    if not allow_json_fallback:
        raise _error("Provider 没有调用期望工具", "missing_tool_call")

    visible = _visible_json_candidates(response)
    unique_visible: dict[str, tuple[Mapping[str, Any], str]] = {}
    for payload, source in visible:
        unique_visible.setdefault(_canonical_payload(payload), (payload, source))
    if not unique_visible:
        raise _error("Provider 没有返回可见 JSON 对象", "missing_structured_payload")
    if len(unique_visible) > 1:
        raise _error("Provider 返回了多个不同的 JSON 对象", "multiple_json_payloads")
    payload, source = next(iter(unique_visible.values()))
    return StructuredProviderPayload(arguments=payload, source=source)


__all__ = [
    "StructuredProviderError",
    "StructuredProviderPayload",
    "extract_structured_payload",
]
