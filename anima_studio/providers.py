from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .domain import DomainValidationError


class ProviderError(RuntimeError):
    pass


def visible_text_content(value: Any) -> str:
    """Normalize visible text from OpenAI-compatible content variants."""

    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        recognized = False
        for key in ("text", "output_text", "content"):
            if key in value:
                recognized = True
                text = visible_text_content(value[key])
                if text:
                    return text
        if recognized:
            return ""
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError):
            return ""
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return "".join(visible_text_content(item) for item in value)
    return ""


def parse_tool_arguments(value: Any) -> dict[str, Any]:
    """Accept both canonical JSON strings and pre-decoded gateway objects."""

    if value in (None, ""):
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str):
        raise ProviderError("provider tool arguments must be JSON or an object")
    try:
        arguments = json.loads(value)
    except ValueError as exc:
        raise ProviderError("provider tool arguments are invalid JSON") from exc
    if not isinstance(arguments, Mapping):
        raise ProviderError("provider tool arguments must be an object")
    return dict(arguments)


@dataclass(frozen=True, slots=True)
class ProviderMessage:
    role: str
    content: Any = ""
    tool_calls: tuple["ToolCall", ...] = ()
    tool_call_id: str = ""
    name: str = ""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            result["tool_calls"] = [call.to_dict() for call in self.tool_calls]
        if self.tool_call_id:
            result["tool_call_id"] = self.tool_call_id
        if self.name:
            result["name"] = self.name
        return result


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(dict(self.arguments), ensure_ascii=False, separators=(",", ":")),
            },
        }


ToolHandler = Callable[[Mapping[str, Any]], Any | Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: Mapping[str, Any]
    handler: ToolHandler

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": dict(self.parameters),
            },
        }


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    model: str
    messages: tuple[ProviderMessage, ...]
    temperature: float = 0.2
    max_tokens: int = 1600
    tools: tuple[ToolDefinition, ...] = ()
    response_format: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not str(self.model).strip():
            raise DomainValidationError("provider model is required")
        if not self.messages:
            raise DomainValidationError("provider messages are required")
        if not 0 <= float(self.temperature) <= 2:
            raise DomainValidationError("temperature must be between 0 and 2")
        if not 1 <= int(self.max_tokens) <= 32_000:
            raise DomainValidationError("max_tokens must be between 1 and 32000")


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    message: ProviderMessage
    finish_reason: str = ""
    usage: Mapping[str, int] = field(default_factory=dict)
    raw: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class NativeProvider(Protocol):
    async def complete(self, request: CompletionRequest) -> ProviderResponse: ...

    async def embeddings(self, model: str, inputs: Sequence[str]) -> list[list[float]]: ...

    async def rerank(
        self, model: str, query: str, documents: Sequence[str], *, top_n: int
    ) -> list[tuple[int, float]]: ...


Transport = Callable[[str, Mapping[str, Any]], Mapping[str, Any] | Awaitable[Mapping[str, Any]]]


class OpenAIProviderFacade:
    """OpenAI-compatible facade over an injected transport, with no web framework coupling."""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    async def _request(self, endpoint: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        result = self.transport(endpoint, payload)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, Mapping):
            raise ProviderError("provider transport returned a non-object response")
        return result

    async def complete(self, request: CompletionRequest) -> ProviderResponse:
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [message.to_dict() for message in request.messages],
            "temperature": float(request.temperature),
            "max_tokens": int(request.max_tokens),
        }
        if request.tools:
            payload["tools"] = [tool.schema() for tool in request.tools]
            payload["tool_choice"] = "auto"
        if request.response_format:
            payload["response_format"] = dict(request.response_format)
        data = await self._request("/chat/completions", payload)
        choices = data.get("choices")
        if not isinstance(choices, Sequence) or not choices or not isinstance(choices[0], Mapping):
            raise ProviderError("provider response contains no choice")
        choice = choices[0]
        raw_message = choice.get("message")
        if not isinstance(raw_message, Mapping):
            raise ProviderError("provider response contains no message")
        calls: list[ToolCall] = []
        for raw_call in raw_message.get("tool_calls") or ():
            if not isinstance(raw_call, Mapping) or not isinstance(raw_call.get("function"), Mapping):
                raise ProviderError("provider returned an invalid tool call")
            function = raw_call["function"]
            arguments = parse_tool_arguments(function.get("arguments"))
            calls.append(
                ToolCall(str(raw_call.get("id") or ""), str(function.get("name") or ""), arguments)
            )
        usage = data.get("usage") if isinstance(data.get("usage"), Mapping) else {}
        content = visible_text_content(raw_message.get("content"))
        if not content:
            content = visible_text_content(
                raw_message.get("output_text", raw_message.get("text"))
            )
        return ProviderResponse(
            ProviderMessage(
                role=str(raw_message.get("role") or "assistant"),
                content=content if content else None,
                tool_calls=tuple(calls),
            ),
            finish_reason=str(choice.get("finish_reason") or ""),
            usage={str(key): int(value) for key, value in usage.items() if isinstance(value, int)},
            raw=dict(data),
        )

    async def embeddings(self, model: str, inputs: Sequence[str]) -> list[list[float]]:
        data = await self._request("/embeddings", {"model": model, "input": list(inputs)})
        rows = data.get("data")
        if not isinstance(rows, Sequence):
            raise ProviderError("embedding response contains no data")
        ordered = sorted(
            (row for row in rows if isinstance(row, Mapping)),
            key=lambda row: int(row.get("index", 0)),
        )
        vectors = [list(map(float, row.get("embedding") or ())) for row in ordered]
        if len(vectors) != len(inputs) or any(not vector for vector in vectors):
            raise ProviderError("embedding response does not match the input count")
        return vectors

    async def rerank(
        self, model: str, query: str, documents: Sequence[str], *, top_n: int = 8
    ) -> list[tuple[int, float]]:
        if not documents:
            return []
        data = await self._request(
            "/rerank",
            {
                "model": model,
                "query": query,
                "documents": list(documents),
                "top_n": min(len(documents), max(1, int(top_n))),
            },
        )
        rows = data.get("results")
        if not isinstance(rows, Sequence):
            raise ProviderError("rerank response contains no results")
        result: list[tuple[int, float]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            index = int(row.get("index", -1))
            if not 0 <= index < len(documents):
                raise ProviderError("rerank response contains an invalid index")
            result.append((index, float(row.get("relevance_score", row.get("score", 0.0)))))
        return result


async def run_tool_loop(
    provider: NativeProvider,
    request: CompletionRequest,
    *,
    max_steps: int = 4,
    max_result_chars: int = 12_000,
) -> ProviderResponse:
    """Run an allowlisted tool loop with bounded calls and bounded local output."""

    if not 1 <= max_steps <= 8:
        raise DomainValidationError("max_steps must be between 1 and 8")
    tools = {tool.name: tool for tool in request.tools}
    if len(tools) != len(request.tools):
        raise DomainValidationError("tool names must be unique")
    messages = list(request.messages)
    current = request
    for _ in range(max_steps):
        response = await provider.complete(current)
        if not response.message.tool_calls:
            content = visible_text_content(response.message.content).strip()
            if not content:
                raise ProviderError("provider returned neither tool calls nor final content")
            return ProviderResponse(
                ProviderMessage(
                    role=response.message.role,
                    content=content,
                    tool_call_id=response.message.tool_call_id,
                    name=response.message.name,
                ),
                finish_reason=response.finish_reason,
                usage=response.usage,
                raw=response.raw,
            )
        messages.append(response.message)
        for call in response.message.tool_calls:
            tool = tools.get(call.name)
            if tool is None:
                raise ProviderError(f"provider called unauthorized tool: {call.name}")
            output = tool.handler(call.arguments)
            if inspect.isawaitable(output):
                output = await output
            try:
                serialized = json.dumps(output, ensure_ascii=False, separators=(",", ":"))
            except (TypeError, ValueError) as exc:
                raise ProviderError(f"tool {call.name} returned non-JSON data") from exc
            if len(serialized) > max_result_chars:
                raise ProviderError(f"tool {call.name} result exceeds the size limit")
            messages.append(
                ProviderMessage(
                    role="tool",
                    content=serialized,
                    tool_call_id=call.id,
                )
            )
        current = CompletionRequest(
            model=request.model,
            messages=tuple(messages),
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            tools=request.tools,
            response_format=request.response_format,
        )
    raise ProviderError("provider tool loop exceeded the maximum step count")
