"""
AstrBot Comfy Anima 插件 v2.0.0

功能描述：
- 使用 AstrBot 中选定的聊天模型规划单图分镜
- 将模型输出规范化为可提交给 Anima 工作流的英文提示词

作者: Yen
版本: 2.0.0
日期: 2026-08-04
"""

import asyncio
import html
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from ..core.lora import LORA_TAG_PATTERN
from ..models import PluginSettings
from .prompt_composer import PromptComposer, PromptDiagnostics
from .prompt_contracts import (
    ANIMA_VISUAL_EXPANSION_PROTOCOL as CONTRACT_VISUAL_EXPANSION_PROTOCOL,
    CAPABILITY_DANBOORU,
    TASK_CONTROL_DRAW,
    TASK_DRAW,
    TASK_MASKED_REDRAW,
    TASK_REVERSE_DRAW,
    TASK_SEMANTIC_REDRAW,
    build_director_contract,
    build_director_user_prompt,
    normalize_capabilities,
    normalize_task_kind,
    transport_terminal_seal,
)
from .provider_response import response_error_code, response_text
from .structured_provider import (
    StructuredProviderError,
    extract_structured_payload,
)


_PIC_TAG_RE = re.compile(
    r"<pic\b(?P<attrs>(?:[^>\"']|\"[^\"]*\"|'[^']*')*)/?>",
    flags=re.IGNORECASE | re.DOTALL,
)
_ANY_PIC_TAG_RE = re.compile(r"</?pic\b[^>]*>", flags=re.IGNORECASE | re.DOTALL)
_EDIT_TAG_RE = re.compile(
    r"<edit\b(?P<attrs>(?:[^>\"']|\"[^\"]*\"|'[^']*')*)/?>",
    flags=re.IGNORECASE | re.DOTALL,
)
_ANY_EDIT_TAG_RE = re.compile(r"</?edit\b[^>]*>", flags=re.IGNORECASE | re.DOTALL)
_THINK_TAG_RE = re.compile(r"</?think\b[^>]*>", flags=re.IGNORECASE)
_EMBEDDED_CONTROL_TAG_RE = re.compile(
    r"</?(?:pic|edit|think)\b",
    flags=re.IGNORECASE,
)
_PROMPT_ATTR_RE = re.compile(
    r"\bprompt\s*=\s*([\"'])(.*?)\1",
    flags=re.IGNORECASE | re.DOTALL,
)
_NEGATIVE_ATTR_RE = re.compile(
    r"\bnegative\s*=\s*([\"'])(.*?)\1",
    flags=re.IGNORECASE | re.DOTALL,
)
_PIPELINE_ATTR_RE = re.compile(
    r"\bpipeline\s*=\s*([\"'])(.*?)\1",
    flags=re.IGNORECASE | re.DOTALL,
)
_CHARACTERS_ATTR_RE = re.compile(
    r"\bcharacters\s*=\s*([\"'])(.*?)\1",
    flags=re.IGNORECASE | re.DOTALL,
)
_MODE_ATTR_RE = re.compile(
    r"\bmode\s*=\s*([\"'])(.*?)\1",
    flags=re.IGNORECASE | re.DOTALL,
)
_CONTROL_ATTR_RE = re.compile(
    r"\s*(?P<name>[A-Za-z_][A-Za-z0-9_-]*)\s*=\s*"
    r"(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
    flags=re.DOTALL,
)

_PROVIDER_ERROR_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "all_models_failed",
        re.compile(r"\ball chat models failed\b", flags=re.IGNORECASE),
    ),
    (
        "empty_model_output",
        re.compile(r"\bEmptyModelOutputError\b", flags=re.IGNORECASE),
    ),
    (
        "completion_without_choices",
        re.compile(r"\bcompletion has no choices\b", flags=re.IGNORECASE),
    ),
    (
        "provider_response_id",
        re.compile(r"\bresponse_id\s*=", flags=re.IGNORECASE),
    ),
    (
        "provider_exception",
        re.compile(
            r"^\s*(?:(?:provider|openai|anthropic|gemini|api|http)\s*"
            r"(?:error|exception|failed|failure)|ProviderError|APIError|"
            r"AuthenticationError|RateLimitError|TimeoutError)\s*[:：]",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "provider_traceback",
        re.compile(r"^\s*traceback\s*\(", flags=re.IGNORECASE),
    ),
)


def _strict_json_object(value: str) -> dict[str, Any]:
    """Parse one finite JSON object while rejecting duplicate keys."""

    def reject_constant(token: str) -> Any:
        raise ValueError(f"invalid JSON constant: {token}")

    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = item
        return result

    parsed = json.loads(
        str(value or "").strip(),
        object_pairs_hook=unique_pairs,
        parse_constant=reject_constant,
    )
    if not isinstance(parsed, dict):
        raise ValueError("JSON root must be an object")
    return parsed


def _has_structured_call_surface(response: Any) -> bool:
    """Return whether a Provider response visibly attempted a tool/function call.

    Auto mode may recover from a Provider that ignores the output schema and
    returns ordinary visible text. It must not reinterpret the same response as
    ``<pic>`` after a malformed, conflicting or unexpected structured call,
    because that would mix two mutually exclusive transports.
    """

    def field(value: Any, name: str) -> Any:
        if isinstance(value, Mapping):
            return value.get(name)
        return getattr(value, name, None)

    def present(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, tuple, dict)):
            return bool(value)
        return True

    if any(
        present(field(response, name))
        for name in (
            "tools_call_name",
            "tools_call_args",
            "tool_calls",
            "function_call",
        )
    ):
        return True

    choices = field(response, "choices")
    if not isinstance(choices, (list, tuple)):
        return False
    for choice in choices:
        message = field(choice, "message")
        if message is not None and any(
            present(field(message, name))
            for name in ("tool_calls", "function_call")
        ):
            return True
    return False


# Public compatibility name. Runtime prompt construction is task-scoped below;
# keep the historical import while sourcing it from the compact v2 contract.
ANIMA_VISUAL_EXPANSION_PROTOCOL = CONTRACT_VISUAL_EXPANSION_PROTOCOL


class PromptDirectorError(RuntimeError):
    """LLM 分镜规划失败。"""

    def __init__(self, user_message: str, detail: str = "", *, fatal: bool = False):
        self.user_message = user_message
        self.detail = detail
        self.fatal = fatal
        super().__init__(detail or user_message)


@dataclass(frozen=True)
class PictureResponse:
    """普通 LLM 回复中的绘图控制信息。

    Attributes:
        prompts: 按标签出现顺序提取并规范化的英文绘图提示词。
        text: 移除 ``think`` 块和全部 ``pic`` 标签后保留的回复正文。
    """

    prompts: tuple[str, ...]
    text: str
    negative_prompts: tuple[str, ...] = ()
    pipelines: tuple[str, ...] = ()
    character_queries: tuple[tuple[str, ...], ...] = ()
    edits: tuple["EditInstruction", ...] = ()


@dataclass(frozen=True)
class PictureInstruction:
    """One normalized drawing request carried by a ``pic`` tag."""

    prompt: str
    negative_prompt: str = ""
    pipeline: str = ""
    character_queries: tuple[str, ...] = ()
    diagnostic_id: str = ""
    diagnostics: PromptDiagnostics | None = None


@dataclass(frozen=True)
class EditInstruction:
    """One normalized local-redraw request carried by an ``edit`` tag."""

    prompt: str
    negative_prompt: str = ""
    mode: str = "quick"


class PromptDirector:
    """封装模型选择、LLM 调用及输出解析。"""

    def __init__(
        self,
        reference_path: Path,
        settings: PluginSettings,
        composer: PromptComposer | None = None,
        danbooru_status_provider: Callable[[], Mapping[str, Any]] | None = None,
    ):
        self._settings = settings
        self._reference = self._load_reference(reference_path)
        self._composer = composer
        self._danbooru_status_provider = danbooru_status_provider

    def compose_picture_instruction(
        self,
        instruction: PictureInstruction,
        *,
        provider_id: str = "",
        source: str = "director",
    ) -> PictureInstruction:
        """Apply the optional local composer exactly once to a picture plan."""

        if self._composer is None or instruction.diagnostic_id:
            return instruction
        try:
            composed = self._composer.compose(
                instruction.prompt,
                instruction.negative_prompt,
                source=source,
                provider_id=provider_id,
                pipeline=instruction.pipeline,
            )
        except (TypeError, ValueError) as exc:
            raise PromptDirectorError(
                "提示词本地组合校验失败，已停止且不会提交 ComfyUI",
                "prompt_composition_failed",
                fatal=True,
            ) from exc
        return PictureInstruction(
            prompt=composed.positive_prompt,
            negative_prompt=composed.negative_prompt,
            pipeline=instruction.pipeline,
            character_queries=instruction.character_queries,
            diagnostic_id=composed.diagnostic_id,
            diagnostics=composed.diagnostics,
        )

    def compose_edit_instruction(
        self,
        instruction: EditInstruction,
        *,
        provider_id: str = "",
        source: str = "edit",
    ) -> EditInstruction:
        """Compose masked-redraw tags without requiring a full-scene sentence."""

        if self._composer is None:
            return instruction
        try:
            composed = self._composer.compose(
                instruction.prompt,
                instruction.negative_prompt,
                source=source,
                provider_id=provider_id,
            )
        except (TypeError, ValueError) as exc:
            raise PromptDirectorError(
                "重绘提示词本地组合校验失败，已停止且不会提交 ComfyUI",
                "prompt_composition_failed",
                fatal=True,
            ) from exc
        return EditInstruction(
            prompt=composed.positive_prompt,
            negative_prompt=composed.negative_prompt,
            mode=instruction.mode,
        )

    @staticmethod
    def _load_reference(path: Path) -> str:
        """读取分镜导演参考提示词。"""
        if not path.is_file():
            raise PromptDirectorError(f"分镜参考文件不存在: {path}")
        if path.stat().st_size > 1024 * 1024:
            raise PromptDirectorError("分镜参考文件超过 1MB")
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise PromptDirectorError(
                "无法读取分镜参考文件", f"读取 {path} 失败: {exc}"
            ) from exc

    def _system_prompt(
        self,
        *,
        task_kind: str = TASK_DRAW,
        expansion_mode: str = "standard",
        capabilities: tuple[str, ...] | None = None,
        transport: str = "pic",
    ) -> str:
        """Compose one versioned contract using only this request's abilities."""

        normalized_task_kind = normalize_task_kind(task_kind)
        normalized_capabilities = normalize_capabilities(
            capabilities if capabilities is not None else ()
        )
        creative_preference_tasks = {
            TASK_DRAW,
            TASK_REVERSE_DRAW,
            TASK_SEMANTIC_REDRAW,
            TASK_CONTROL_DRAW,
        }
        parts = [
            build_director_contract(
                task_kind=normalized_task_kind,
                expansion_mode=expansion_mode,
                capabilities=normalized_capabilities,
                transport=transport,
            )
        ]
        if CAPABILITY_DANBOORU in normalized_capabilities:
            danbooru_context = self.danbooru_runtime_context()
            if danbooru_context:
                parts.append(danbooru_context)
        if (
            normalized_task_kind in creative_preference_tasks
            and self._settings.auto_draw_system_prompt
        ):
            parts.extend(
                [
                    "以下是管理员创作偏好；不得覆盖上面的传输、证据、实时资产和安全约束：",
                    self._settings.auto_draw_system_prompt,
                ]
            )
        elif normalized_task_kind in creative_preference_tasks:
            parts.extend(
                [
                    "以下内容仅作为创作与标签写法参考，不提供身份或资产授权：",
                    self._reference,
                ]
            )
        if (
            normalized_task_kind in creative_preference_tasks
            and self._settings.director_extra_instruction
        ):
            parts.extend(
                ["管理员补充要求：", self._settings.director_extra_instruction]
            )
        parts.append(transport_terminal_seal(transport))
        return "\n\n".join(parts)

    def danbooru_runtime_context(self) -> str:
        """Expose only bounded index readiness, never source paths or provenance."""

        provider = self._danbooru_status_provider
        if provider is None:
            return ""
        try:
            status = provider()
        except Exception:
            status = {}
        if not isinstance(status, Mapping) or not bool(status.get("ready")):
            return (
                "本地 Danbooru 索引状态：ready=false。不得声称已查库或已验证标签；"
                "若本次没有查询工具，只使用你能高置信确认的普通英文 Tags。"
            )
        tag_count = max(0, int(status.get("tag_count") or 0))
        alias_count = max(0, int(status.get("alias_count") or 0))
        revision = re.sub(
            r"[^A-Za-z0-9._-]",
            "",
            str(status.get("revision") or "")[:64],
        )
        return (
            "本地 Danbooru 索引状态：ready=true，"
            f"canonical_tags={tag_count}，aliases={alias_count}，"
            f"revision={revision or 'unknown'}。"
            "这套索引可通过 search_anima_danbooru_tags 进行有界只读查询；"
            "工具不在本次列表时不得假装调用。候选结果只帮助发现，最终应使用"
            " verified exact canonical/alias 所指向的 canonical_tag。"
        )

    def _lora_tool_call_timeout(self) -> int:
        """Return a per-call budget that covers Manager scan and catalog read."""
        catalog_timeout = max(1, self._settings.lora_catalog_timeout)
        retrieval_timeout = (
            max(3, self._settings.lora_retrieval_timeout)
            if getattr(self._settings, "enable_lora_hybrid_search", False)
            else 0
        )
        if not self._settings.enable_lora_manager:
            return catalog_timeout + retrieval_timeout
        return (
            max(1, self._settings.lora_manager_scan_timeout)
            + catalog_timeout
            + retrieval_timeout
        )

    def _lora_agent_timeout(self, tool_call_timeout: int) -> int:
        """Reserve the configured LLM budget in addition to all tool calls."""
        return self._settings.prompt_llm_timeout + (
            max(1, self._settings.lora_tool_max_steps) * tool_call_timeout
        )

    async def generate(
        self,
        context: Any,
        event: Any,
        scene_text: str,
        tools: Any = None,
        *,
        task_kind: str = TASK_DRAW,
        runtime_capabilities: tuple[str, ...] = (),
    ) -> tuple[str, str]:
        """Backward-compatible positive-prompt API."""
        prompt, provider_id, _ = await self.generate_with_negative(
            context,
            event,
            scene_text,
            tools,
            task_kind=task_kind,
            runtime_capabilities=runtime_capabilities,
        )
        return prompt, provider_id

    async def generate_with_negative(
        self,
        context: Any,
        event: Any,
        scene_text: str,
        tools: Any = None,
        expansion_mode: str = "standard",
        *,
        task_kind: str = TASK_DRAW,
        runtime_capabilities: tuple[str, ...] = (),
    ) -> tuple[str, str, str]:
        """调用指定 AstrBot 模型生成提示词。

        Args:
            context: AstrBot 插件 Context。
            event: 当前消息事件，用于获取会话默认模型。
            scene_text: 用户提供的剧情或画面描述。

        Returns:
            规范化提示词和实际 provider ID。
        """
        instruction, provider_id = await self.generate_instruction(
            context,
            event,
            scene_text,
            tools,
            expansion_mode=expansion_mode,
            task_kind=task_kind,
            runtime_capabilities=runtime_capabilities,
        )
        return instruction.prompt, provider_id, instruction.negative_prompt

    async def generate_instruction(
        self,
        context: Any,
        event: Any,
        scene_text: str,
        tools: Any = None,
        output_tools: Any = None,
        lookup_tool_call_timeout: int | None = None,
        expansion_mode: str = "standard",
        task_kind: str = TASK_DRAW,
        runtime_capabilities: tuple[str, ...] = (),
        compose_result: bool = True,
    ) -> tuple[PictureInstruction, str]:
        """Generate one validated picture instruction including its pipeline."""

        if tools is not None and output_tools is not None:
            raise PromptDirectorError(
                "本地资产查询工具与结构化输出工具不能同时启用",
                "conflicting_tool_transports",
                fatal=True,
            )
        provider_id = await self._resolve_provider_id(context, event)
        normalized_expansion_mode = str(
            expansion_mode or "standard"
        ).strip().casefold()
        if normalized_expansion_mode not in {"standard", "ultra"}:
            normalized_expansion_mode = "standard"
        normalized_task_kind = normalize_task_kind(task_kind)
        normalized_capabilities = normalize_capabilities(runtime_capabilities)
        structured_mode = str(
            getattr(self._settings, "structured_director_mode", "auto") or "auto"
        ).casefold()
        transport = (
            "function"
            if output_tools is not None
            else "json"
            if structured_mode in {"json", "function_call"}
            else "pic"
        )
        base_user_prompt = build_director_user_prompt(
            scene_text,
            task_kind=normalized_task_kind,
            expansion_mode=normalized_expansion_mode,
            transport=transport,
        )
        user_prompt = base_user_prompt
        base_system_prompt = self._system_prompt(
            task_kind=normalized_task_kind,
            expansion_mode=normalized_expansion_mode,
            capabilities=normalized_capabilities,
            transport=transport,
        )
        system_prompt = base_system_prompt
        pic_user_prompt = build_director_user_prompt(
            scene_text,
            task_kind=normalized_task_kind,
            expansion_mode=normalized_expansion_mode,
            transport="pic",
        )
        pic_system_prompt = self._system_prompt(
            task_kind=normalized_task_kind,
            expansion_mode=normalized_expansion_mode,
            capabilities=normalized_capabilities,
            transport="pic",
        )
        terminal_repair_system_prompt = self._system_prompt(
            task_kind=normalized_task_kind,
            expansion_mode=normalized_expansion_mode,
            capabilities=(),
            transport="json",
        )
        kwargs = {
            "prompt": user_prompt,
            "system_prompt": system_prompt,
            "temperature": min(2.0, self._settings.prompt_llm_temperature),
            "max_tokens": self._settings.prompt_llm_max_tokens,
        }

        uses_lookup_tools = tools is not None
        tool_call_timeout = 0
        request_timeout = self._settings.prompt_llm_timeout
        if uses_lookup_tools:
            tool_call_timeout = max(
                1,
                int(
                    lookup_tool_call_timeout
                    if lookup_tool_call_timeout is not None
                    else self._lora_tool_call_timeout()
                ),
            )
            request_timeout = self._lora_agent_timeout(tool_call_timeout)

        async def invoke(
            active_prompt: str,
            *,
            include_output_tools: bool = True,
            include_lookup_tools: bool = True,
        ) -> Any:
            use_output_tools = include_output_tools and output_tools is not None
            use_lookup_tools = include_lookup_tools and uses_lookup_tools
            if use_lookup_tools:
                if not hasattr(context, "tool_loop_agent"):
                    raise PromptDirectorError(
                        "当前 AstrBot 不支持本地资产查询工具，已停止本次绘图",
                        fatal=True,
                    )
                response = await asyncio.wait_for(
                    context.tool_loop_agent(
                        event=event,
                        chat_provider_id=provider_id,
                        prompt=active_prompt,
                        system_prompt=kwargs["system_prompt"],
                        tools=tools,
                        max_steps=self._settings.lora_tool_max_steps,
                        tool_call_timeout=tool_call_timeout,
                    ),
                    timeout=request_timeout,
                )
            elif hasattr(context, "llm_generate"):
                llm_kwargs = {
                    **kwargs,
                    "prompt": active_prompt,
                    "system_prompt": (
                        terminal_repair_system_prompt
                        if uses_lookup_tools and not include_lookup_tools
                        else
                        system_prompt
                        if use_output_tools
                        else pic_system_prompt
                        if output_tools is not None
                        else base_system_prompt
                    ),
                }
                if use_output_tools:
                    llm_kwargs["tools"] = output_tools
                try:
                    response = await asyncio.wait_for(
                        context.llm_generate(
                            chat_provider_id=provider_id,
                            **llm_kwargs,
                        ),
                        timeout=self._settings.prompt_llm_timeout,
                    )
                except TypeError:
                    if not use_output_tools or structured_mode != "auto":
                        raise
                    llm_kwargs.pop("tools", None)
                    llm_kwargs["prompt"] = (
                        pic_user_prompt
                        + "\n\nReturn exactly one "
                        '<pic prompt="English Anima tags. One concise scene sentence."> '
                        "tag and nothing else. Do not return Markdown, explanation or "
                        "plain conversational text."
                    )
                    llm_kwargs["system_prompt"] = pic_system_prompt
                    response = await asyncio.wait_for(
                        context.llm_generate(
                            chat_provider_id=provider_id,
                            **llm_kwargs,
                        ),
                        timeout=self._settings.prompt_llm_timeout,
                    )
            else:
                provider = self._get_legacy_provider(context, event, provider_id)
                response = await asyncio.wait_for(
                    provider.text_chat(
                        contexts=[],
                        **{**kwargs, "prompt": active_prompt},
                    ),
                    timeout=self._settings.prompt_llm_timeout,
                )
            return response

        try:
            response = await invoke(user_prompt)
        except asyncio.TimeoutError as exc:
            if uses_lookup_tools:
                raise PromptDirectorError(
                    "本地资产查询或 LLM 分镜超时，已停止本次绘图",
                    (
                        f"provider={provider_id}, "
                        f"tool_call_timeout={tool_call_timeout}, "
                        f"agent_timeout={request_timeout}"
                    ),
                    fatal=True,
                ) from exc
            raise PromptDirectorError("LLM 分镜超时") from exc
        except PromptDirectorError:
            raise
        except Exception as exc:
            if uses_lookup_tools:
                raise PromptDirectorError(
                    "本地资产查询工具调用失败，已停止本次绘图",
                    f"provider={provider_id}, error={exc}",
                    fatal=True,
                ) from exc
            raise PromptDirectorError(
                "LLM 分镜调用失败", f"provider={provider_id}, error={exc}"
            ) from exc

        first_error: PromptDirectorError | None = None
        terminal_repair_response = False
        for attempt in range(2):
            try:
                provider_error = response_error_code(response)
                if provider_error:
                    raise PromptDirectorError(
                        "绘图 Provider 没有返回可用结果",
                        provider_error,
                        fatal=True,
                    )
                if terminal_repair_response:
                    instruction = self._extract_terminal_repair_instruction(response)
                    if compose_result:
                        instruction = self.compose_picture_instruction(
                            instruction,
                            provider_id=provider_id,
                        )
                    return instruction, provider_id
                if transport in {"function", "json"}:
                    payload: Any = None
                    if transport == "function":
                        try:
                            structured = extract_structured_payload(
                                response,
                                expected_tool_name="emit_anima_plan_v1",
                                allow_json_fallback=(
                                    structured_mode != "function_call"
                                ),
                            )
                        except StructuredProviderError as structured_exc:
                            if (
                                structured_mode == "function_call"
                                or _has_structured_call_surface(response)
                            ):
                                raise PromptDirectorError(
                                    "绘图模型没有返回合法的结构化 Function Call",
                                    structured_exc.code,
                                    fatal=True,
                                ) from structured_exc
                        else:
                            payload = structured.arguments
                    else:
                        visible = response_text(response)
                        try:
                            payload = _strict_json_object(str(visible or ""))
                        except (TypeError, ValueError, json.JSONDecodeError) as json_exc:
                            raise PromptDirectorError(
                                "绘图模型没有返回合法的结构化 JSON",
                                "invalid_director_json",
                                fatal=True,
                            ) from json_exc
                    if payload is not None:
                        instruction = self._picture_instruction_from_payload(payload)
                        if compose_result:
                            instruction = self.compose_picture_instruction(
                                instruction,
                                provider_id=provider_id,
                            )
                        return instruction, provider_id
                completion = response_text(response)
                if not isinstance(completion, str) or not completion.strip():
                    raise PromptDirectorError(
                        "绘图模型没有返回有效提示词",
                        "empty_completion",
                        fatal=True,
                    )
                instruction = self.extract_instruction(
                    completion,
                    strict_protocol=True,
                )
                if compose_result:
                    instruction = self.compose_picture_instruction(
                        instruction,
                        provider_id=provider_id,
                    )
                return instruction, provider_id
            except PromptDirectorError as exc:
                if exc.detail in {
                    "error_role",
                    "all_models_failed",
                    "no_choices",
                    "provider_error",
                    "prompt_composition_failed",
                }:
                    raise
                if attempt == 1:
                    detail = exc.detail or exc.user_message
                    if first_error is not None and not detail:
                        detail = first_error.detail or first_error.user_message
                    raise PromptDirectorError(
                        (
                            "本地资产工具分镜结果无效；连续两次修复失败，已停止且不会提交 ComfyUI"
                            if uses_lookup_tools
                            else "绘图模型连续两次没有返回可用的 <pic> 提示词，已停止且不会提交 ComfyUI"
                        ),
                        detail,
                        fatal=True,
                    ) from exc
                first_error = exc
                if uses_lookup_tools and not hasattr(context, "llm_generate"):
                    raise PromptDirectorError(
                        "本地资产工具分镜结果无效，且当前 AstrBot 不支持无工具终端修复",
                        exc.detail or exc.user_message,
                        fatal=True,
                    ) from exc
                auto_protocol_fallback = (
                    transport == "function"
                    and output_tools is not None
                    and structured_mode == "auto"
                )
                repair_prompt = (
                    user_prompt
                    + "\n\nThe local asset lookup is complete. Return exactly one JSON "
                    "object with positive_tags, negative_tags, pipeline and optional "
                    "characters fields. Do not call any tool again. Do not return "
                    "explanation, Markdown, XML, an error message or plain text."
                    if uses_lookup_tools
                    else (pic_user_prompt if auto_protocol_fallback else user_prompt)
                    + (
                        "\n\nYour previous response was invalid. Return exactly one "
                        '<pic prompt="English Anima tags. One concise scene sentence." '
                        'negative="optional English negative tags" '
                        'pipeline="base|rtx|iterative"> tag and nothing else. '
                        "Do not return an error message, explanation, Markdown or plain text."
                        if auto_protocol_fallback
                        else
                        "\n\nYour previous response was invalid. Call "
                        "emit_anima_plan_v1 exactly once with valid JSON arguments. "
                        "positive_tags must contain ordered English tags followed by a "
                        "period and one concise natural-language scene sentence."
                        if transport == "function"
                        else "\n\nYour previous response was invalid. Return exactly one "
                        "JSON object with positive_tags, negative_tags, pipeline and "
                        "optional characters fields. Do not return explanation, "
                        "Markdown or plain text."
                        if transport == "json"
                        else "\n\nYour previous response was invalid. Return exactly one "
                        '<pic prompt="English Anima tags. One concise scene sentence."> '
                        "tag and nothing else. "
                        "Do not return an error message, explanation, Markdown or plain text."
                    )
                )
                try:
                    response = await invoke(
                        repair_prompt,
                        include_output_tools=not auto_protocol_fallback,
                        include_lookup_tools=not uses_lookup_tools,
                    )
                    terminal_repair_response = uses_lookup_tools
                except asyncio.TimeoutError as retry_exc:
                    raise PromptDirectorError(
                        "绘图模型修复重试超时，已停止且不会提交 ComfyUI",
                        "repair_timeout",
                        fatal=True,
                    ) from retry_exc
                except PromptDirectorError:
                    raise
                except Exception as retry_exc:
                    raise PromptDirectorError(
                        "绘图模型修复重试失败，已停止且不会提交 ComfyUI",
                        f"provider={provider_id}, error_type={type(retry_exc).__name__}",
                        fatal=True,
                    ) from retry_exc

        raise AssertionError("unreachable")

    async def generate_edit_instruction(
        self,
        context: Any,
        event: Any,
        scene_text: str,
        tools: Any = None,
        lookup_tool_call_timeout: int | None = None,
        runtime_capabilities: tuple[str, ...] = (),
    ) -> tuple[EditInstruction, str]:
        """Plan a masked redraw without allowing the model to invent a mask."""

        provider_id = await self._resolve_provider_id(context, event)
        normalized_capabilities = normalize_capabilities(runtime_capabilities)
        user_prompt = build_director_user_prompt(
            scene_text,
            task_kind=TASK_MASKED_REDRAW,
            expansion_mode="standard",
            transport="edit",
        )
        system_prompt = self._system_prompt(
            task_kind=TASK_MASKED_REDRAW,
            expansion_mode="standard",
            capabilities=normalized_capabilities,
            transport="edit",
        )
        tool_call_timeout = (
            max(
                1,
                int(
                    lookup_tool_call_timeout
                    if lookup_tool_call_timeout is not None
                    else self._lora_tool_call_timeout()
                ),
            )
            if tools is not None
            else 0
        )
        request_timeout = (
            self._lora_agent_timeout(tool_call_timeout)
            if tools is not None
            else self._settings.prompt_llm_timeout
        )

        async def invoke(active_prompt: str) -> Any:
            if tools is not None:
                if not hasattr(context, "tool_loop_agent"):
                    raise PromptDirectorError(
                        "当前 AstrBot 不支持本地资产查询工具，已停止本次重绘",
                        fatal=True,
                    )
                response = await asyncio.wait_for(
                    context.tool_loop_agent(
                        event=event,
                        chat_provider_id=provider_id,
                        prompt=active_prompt,
                        system_prompt=system_prompt,
                        tools=tools,
                        max_steps=self._settings.lora_tool_max_steps,
                        tool_call_timeout=tool_call_timeout,
                    ),
                    timeout=request_timeout,
                )
            elif hasattr(context, "llm_generate"):
                response = await asyncio.wait_for(
                    context.llm_generate(
                        chat_provider_id=provider_id,
                        prompt=active_prompt,
                        system_prompt=system_prompt,
                        temperature=min(2.0, self._settings.prompt_llm_temperature),
                        max_tokens=self._settings.prompt_llm_max_tokens,
                    ),
                    timeout=request_timeout,
                )
            else:
                provider = self._get_legacy_provider(context, event, provider_id)
                response = await asyncio.wait_for(
                    provider.text_chat(
                        contexts=[],
                        prompt=active_prompt,
                        system_prompt=system_prompt,
                        temperature=min(2.0, self._settings.prompt_llm_temperature),
                        max_tokens=self._settings.prompt_llm_max_tokens,
                    ),
                    timeout=request_timeout,
                )
            return response

        try:
            response = await invoke(user_prompt)
        except asyncio.TimeoutError as exc:
            raise PromptDirectorError("LLM 重绘规划超时", fatal=tools is not None) from exc
        except PromptDirectorError:
            raise
        except Exception as exc:
            raise PromptDirectorError(
                "LLM 重绘规划失败",
                f"provider={provider_id}, error={exc}",
                fatal=tools is not None,
            ) from exc
        for attempt in range(2):
            try:
                provider_error = response_error_code(response)
                if provider_error:
                    raise PromptDirectorError(
                        "重绘 Provider 没有返回可用结果",
                        provider_error,
                        fatal=True,
                    )
                completion = response_text(response)
                if not isinstance(completion, str) or not completion.strip():
                    raise PromptDirectorError(
                        "LLM 没有返回有效重绘提示词",
                        "empty_edit_completion",
                        fatal=True,
                    )
                self.reject_provider_error_output(completion)
                instruction = self.extract_edit_instruction(
                    completion,
                    strict_protocol=True,
                )
                instruction = self.compose_edit_instruction(
                    instruction,
                    provider_id=provider_id,
                    source="edit",
                )
                return instruction, provider_id
            except PromptDirectorError as exc:
                if exc.detail in {
                    "error_role",
                    "all_models_failed",
                    "no_choices",
                    "provider_error",
                    "prompt_composition_failed",
                }:
                    raise
                if attempt == 1:
                    raise PromptDirectorError(
                        "重绘模型连续两次没有返回可用的 <edit> 提示词，已停止且不会提交 ComfyUI",
                        exc.detail or exc.user_message,
                        fatal=True,
                    ) from exc
                repair_prompt = (
                    user_prompt
                    + "\n\nYour previous response was invalid. Return exactly one "
                    '<edit prompt="English Anima tags" mode="quick|lanpaint"> tag '
                    "and nothing else. Do not return an error message, explanation, "
                    "Markdown or plain text."
                )
                try:
                    response = await invoke(repair_prompt)
                except asyncio.TimeoutError as retry_exc:
                    raise PromptDirectorError(
                        "重绘模型修复重试超时，已停止且不会提交 ComfyUI",
                        "edit_repair_timeout",
                        fatal=True,
                    ) from retry_exc
                except Exception as retry_exc:
                    raise PromptDirectorError(
                        "重绘模型修复重试失败，已停止且不会提交 ComfyUI",
                        (
                            f"provider={provider_id}, "
                            f"error_type={type(retry_exc).__name__}"
                        ),
                        fatal=True,
                    ) from retry_exc

        raise AssertionError("unreachable")

    async def _resolve_provider_id(self, context: Any, event: Any) -> str:
        """优先使用配置模型，否则使用当前会话模型。"""
        if self._settings.prompt_llm_provider_id:
            return self._settings.prompt_llm_provider_id
        umo = getattr(event, "unified_msg_origin", None)
        if hasattr(context, "get_current_chat_provider_id") and umo:
            try:
                provider_id = await context.get_current_chat_provider_id(umo=umo)
            except TypeError:
                provider_id = await context.get_current_chat_provider_id(umo)
            if provider_id:
                return str(provider_id)
        provider = self._get_legacy_provider(context, event, "")
        meta = provider.meta() if hasattr(provider, "meta") else None
        provider_id = getattr(meta, "id", "") if meta else ""
        if not provider_id:
            raise PromptDirectorError("未选择 LLM，当前会话也没有可用模型")
        return str(provider_id)

    async def resolve_provider_id(self, context: Any, event: Any) -> str:
        """Public provider resolver shared by bounded internal LLM tasks."""

        return await self._resolve_provider_id(context, event)

    @staticmethod
    def _get_legacy_provider(context: Any, event: Any, provider_id: str) -> Any:
        """获取 AstrBot v4.5.7 之前的 Provider 对象。"""
        provider = None
        if provider_id and hasattr(context, "get_provider_by_id"):
            provider = context.get_provider_by_id(provider_id)
        if provider is None and hasattr(context, "get_using_provider"):
            umo = getattr(event, "unified_msg_origin", None)
            try:
                provider = context.get_using_provider(umo)
            except TypeError:
                provider = context.get_using_provider()
        if provider is None or not hasattr(provider, "text_chat"):
            raise PromptDirectorError("找不到可用的 LLM Provider")
        return provider

    @staticmethod
    def extract_prompt(model_output: str) -> str:
        """从 pic 标签、JSON 或纯文本输出中提取单行英文提示词。"""
        return PromptDirector.extract_instruction(model_output).prompt

    @staticmethod
    def extract_instruction(
        model_output: str,
        *,
        strict_protocol: bool = False,
    ) -> PictureInstruction:
        """Extract one positive prompt and an optional negative prompt."""
        if strict_protocol:
            match = PromptDirector._strict_control_match(
                model_output,
                pattern=_PIC_TAG_RE,
                control_name="pic",
                detail="invalid_picture_protocol",
            )
            return PromptDirector._picture_instruction_from_match(match)

        text = PromptDirector._remove_think_content(model_output).strip()
        PromptDirector.reject_provider_error_output(text)
        instructions = PromptDirector.extract_pic_instructions(text, max_prompts=1)
        if instructions:
            return instructions[0]
        prompt = ""
        negative_prompt = ""
        character_queries: tuple[str, ...] = ()
        parsed: Any = None
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                prompt, negative_prompt = PromptDirector._structured_prompt_values(parsed)
                if not isinstance(prompt, str):
                    prompt = ""
                if not isinstance(negative_prompt, str):
                    negative_prompt = ""
                character_queries = PromptDirector._normalize_character_queries(
                    parsed.get("characters")
                )
        except json.JSONDecodeError:
            pass
        if not prompt:
            cleaned = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text)
            cleaned = re.sub(
                r"^\s*(?:prompt|final prompt)\s*:\s*",
                "",
                cleaned,
                flags=re.IGNORECASE,
            )
            prompt = cleaned.strip().strip("\"'")

        return PictureInstruction(
            prompt=PromptDirector._normalize_prompt(prompt),
            negative_prompt=PromptDirector._normalize_negative_prompt(negative_prompt),
            pipeline=(
                PromptDirector._normalize_pipeline(parsed.get("pipeline"))
                if isinstance(parsed, dict) and parsed.get("pipeline") is not None
                else ""
            ),
            character_queries=character_queries,
        )

    @staticmethod
    def _picture_instruction_from_payload(payload: Any) -> PictureInstruction:
        """Validate one sealed JSON/function picture plan."""

        if not isinstance(payload, dict):
            raise PromptDirectorError(
                "结构化分镜必须是 JSON 对象",
                "invalid_structured_root",
                fatal=True,
            )
        allowed_fields = {
            "positive_tags",
            "prompt",
            "negative_tags",
            "negative_prompt",
            "negative",
            "pipeline",
            "characters",
        }
        if any(key not in allowed_fields for key in payload):
            raise PromptDirectorError(
                "结构化分镜包含不受支持的字段",
                "unexpected_structured_fields",
                fatal=True,
            )
        positive, negative = PromptDirector._structured_prompt_values(payload)
        pipeline = payload.get("pipeline", "")
        characters = payload.get("characters", ())
        if not isinstance(positive, str) or not positive.strip():
            raise PromptDirectorError(
                "结构化分镜缺少 positive_tags",
                "missing_positive_tags",
                fatal=True,
            )
        if not isinstance(negative, str) or not isinstance(pipeline, str):
            raise PromptDirectorError(
                "结构化分镜字段类型无效",
                "invalid_structured_fields",
                fatal=True,
            )
        return PictureInstruction(
            prompt=PromptDirector._normalize_prompt(positive),
            negative_prompt=PromptDirector._normalize_negative_prompt(negative),
            pipeline=PromptDirector._normalize_pipeline(pipeline),
            character_queries=PromptDirector._normalize_character_queries(characters),
        )

    @staticmethod
    def _terminal_repair_diagnostics(response: Any) -> str:
        """Return bounded shape diagnostics without leaking Provider content."""

        visible = response_text(response)
        text = str(visible or "")
        stripped = text.strip()
        return (
            f"chars={len(text)};visible={bool(stripped)};"
            f"pic_count={len(_PIC_TAG_RE.findall(text))};"
            f"json_shape={stripped.startswith('{') and stripped.endswith('}')};"
            f"structured={_has_structured_call_surface(response)}"
        )

    @staticmethod
    def _extract_terminal_repair_instruction(response: Any) -> PictureInstruction:
        """Accept one strict repair envelope without reopening lookup authority."""

        diagnostics = PromptDirector._terminal_repair_diagnostics(response)
        if _has_structured_call_surface(response):
            try:
                structured = extract_structured_payload(
                    response,
                    expected_tool_name="emit_anima_plan_v1",
                    allow_json_fallback=False,
                )
                return PromptDirector._picture_instruction_from_payload(
                    structured.arguments
                )
            except (PromptDirectorError, StructuredProviderError) as exc:
                raise PromptDirectorError(
                    "绘图模型终端修复返回了无效结构化结果",
                    f"invalid_terminal_repair_structured:{diagnostics}",
                    fatal=True,
                ) from exc

        visible = str(response_text(response) or "")
        PromptDirector.reject_provider_error_output(visible)
        try:
            match = PromptDirector._strict_control_match(
                visible,
                pattern=_PIC_TAG_RE,
                control_name="pic",
                detail="invalid_picture_protocol",
            )
            return PromptDirector._picture_instruction_from_match(match)
        except PromptDirectorError:
            pass
        try:
            payload = _strict_json_object(visible)
            return PromptDirector._picture_instruction_from_payload(payload)
        except (PromptDirectorError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PromptDirectorError(
                "绘图模型终端修复没有返回唯一合法的 JSON 或 <pic> 分镜",
                f"invalid_terminal_repair:{diagnostics}",
                fatal=True,
            ) from exc

    @staticmethod
    def _structured_prompt_values(payload: Mapping[str, Any]) -> tuple[Any, Any]:
        """Select structured prompt aliases only when they are unambiguous."""

        positive_aliases = tuple(
            key for key in ("positive_tags", "prompt") if key in payload
        )
        if len(positive_aliases) > 1:
            raise PromptDirectorError(
                "结构化分镜重复声明了正向提示词字段",
                "conflicting_positive_aliases",
                fatal=True,
            )
        negative_aliases = tuple(
            key
            for key in ("negative_tags", "negative_prompt", "negative")
            if key in payload
        )
        if len(negative_aliases) > 1:
            raise PromptDirectorError(
                "结构化分镜重复声明了负向提示词字段",
                "conflicting_negative_aliases",
                fatal=True,
            )
        positive = payload.get(positive_aliases[0], "") if positive_aliases else ""
        negative = payload.get(negative_aliases[0], "") if negative_aliases else ""
        return positive, negative

    @staticmethod
    def _strict_control_match(
        model_output: str,
        *,
        pattern: re.Pattern[str],
        control_name: str,
        detail: str,
    ) -> re.Match[str]:
        """Require one control tag as the entire visible transport envelope."""

        source = str(model_output or "")
        PromptDirector.reject_provider_error_output(source)
        matches = list(pattern.finditer(source))
        if len(matches) != 1:
            raise PromptDirectorError(
                f"LLM 没有返回唯一合法的 <{control_name}> 标签",
                detail,
                fatal=True,
            )
        match = matches[0]
        if source[: match.start()].strip() or source[match.end() :].strip():
            raise PromptDirectorError(
                f"LLM 在 <{control_name}> 标签之外返回了额外内容",
                detail,
                fatal=True,
            )
        return match

    @staticmethod
    def _reject_embedded_control_tags(value: Any, *, field: str) -> None:
        decoded = html.unescape(str(value or ""))
        if _EMBEDDED_CONTROL_TAG_RE.search(decoded):
            raise PromptDirectorError(
                f"LLM 在 {field} 字段中嵌入了控制标签",
                "embedded_control_tag",
                fatal=True,
            )

    @staticmethod
    def _parse_control_attributes(
        attributes: str,
        *,
        allowed: frozenset[str],
    ) -> dict[str, str]:
        """Parse top-level quoted attributes and reject ambiguity."""

        source = str(attributes or "")
        cursor = 0
        parsed: dict[str, str] = {}
        while cursor < len(source):
            if source[cursor:].strip() in {"", "/"}:
                break
            match = _CONTROL_ATTR_RE.match(source, cursor)
            if match is None:
                raise PromptDirectorError(
                    "LLM 返回了无法解析的绘图标签属性",
                    "invalid_control_attributes",
                    fatal=True,
                )
            key = match.group("name").casefold()
            if key not in allowed:
                raise PromptDirectorError(
                    f"LLM 返回了未知绘图标签属性: {key}",
                    "unknown_control_attribute",
                    fatal=True,
                )
            if key in parsed:
                raise PromptDirectorError(
                    f"LLM 重复返回了绘图标签属性: {key}",
                    "duplicate_control_attribute",
                    fatal=True,
                )
            value = html.unescape(match.group("value"))
            PromptDirector._reject_embedded_control_tags(value, field=key)
            parsed[key] = value
            cursor = match.end()
        return parsed

    @staticmethod
    def render_picture_instruction(instruction: PictureInstruction) -> str:
        """Serialize one validated instruction without attribute injection."""

        attributes = [
            f'prompt="{html.escape(instruction.prompt, quote=True)}"'
        ]
        if instruction.negative_prompt:
            attributes.append(
                f'negative="{html.escape(instruction.negative_prompt, quote=True)}"'
            )
        if instruction.pipeline:
            attributes.append(
                f'pipeline="{html.escape(instruction.pipeline, quote=True)}"'
            )
        if instruction.character_queries:
            characters = ";".join(instruction.character_queries)
            attributes.append(
                f'characters="{html.escape(characters, quote=True)}"'
            )
        return "<pic " + " ".join(attributes) + ">"

    @staticmethod
    def extract_pic_instructions(
        model_output: str, *, max_prompts: int | None = None
    ) -> list[PictureInstruction]:
        """Extract normalized positive/negative prompt pairs from ``pic`` tags."""
        if max_prompts is not None and max_prompts < 0:
            raise ValueError("max_prompts 不能小于 0")
        if max_prompts == 0:
            return []

        visible_text = PromptDirector._remove_think_content(model_output)
        instructions: list[PictureInstruction] = []
        for match in _PIC_TAG_RE.finditer(visible_text):
            try:
                instruction = PromptDirector._picture_instruction_from_match(match)
            except PromptDirectorError as exc:
                if exc.detail == "missing_picture_prompt":
                    continue
                raise
            instructions.append(instruction)
            if max_prompts is not None and len(instructions) >= max_prompts:
                break
        return instructions

    @staticmethod
    def _picture_instruction_from_match(match: re.Match[str]) -> PictureInstruction:
        attributes = PromptDirector._parse_control_attributes(
            match.group("attrs"),
            allowed=frozenset({"prompt", "negative", "pipeline", "characters"}),
        )
        prompt = attributes.get("prompt", "")
        if not prompt:
            raise PromptDirectorError(
                "LLM 返回的 pic 标签缺少 prompt 属性",
                "missing_picture_prompt",
                fatal=True,
            )
        negative_prompt = (
            PromptDirector._normalize_negative_prompt(attributes["negative"])
            if "negative" in attributes
            else ""
        )
        return PictureInstruction(
            prompt=PromptDirector._normalize_prompt(prompt),
            negative_prompt=negative_prompt,
            pipeline=PromptDirector._normalize_pipeline(
                attributes.get("pipeline", "")
            ),
            character_queries=PromptDirector._normalize_character_queries(
                attributes.get("characters")
            ),
        )

    @staticmethod
    def extract_edit_instruction(
        model_output: str,
        *,
        strict_protocol: bool = False,
    ) -> EditInstruction:
        """Extract one edit instruction, optionally enforcing a sealed envelope."""

        if strict_protocol:
            match = PromptDirector._strict_control_match(
                model_output,
                pattern=_EDIT_TAG_RE,
                control_name="edit",
                detail="invalid_edit_protocol",
            )
            return PromptDirector._edit_instruction_from_match(match)
        instructions = PromptDirector.extract_edit_instructions(
            model_output,
            max_edits=1,
        )
        if not instructions:
            raise PromptDirectorError(
                "LLM 没有返回合法 edit 标签",
                "invalid_edit_protocol",
                fatal=True,
            )
        return instructions[0]

    @staticmethod
    def _edit_instruction_from_match(match: re.Match[str]) -> EditInstruction:
        attributes = PromptDirector._parse_control_attributes(
            match.group("attrs"),
            allowed=frozenset({"prompt", "negative", "mode"}),
        )
        prompt = attributes.get("prompt", "")
        if not prompt:
            raise PromptDirectorError(
                "LLM 返回的 edit 标签缺少 prompt 属性",
                "missing_edit_prompt",
                fatal=True,
            )
        return EditInstruction(
            prompt=PromptDirector._normalize_prompt(prompt),
            negative_prompt=PromptDirector._normalize_negative_prompt(
                attributes.get("negative", "")
            ),
            mode=PromptDirector._normalize_inpaint_mode(
                attributes.get("mode", "quick")
            ),
        )

    @staticmethod
    def extract_edit_instructions(
        model_output: str, *, max_edits: int | None = None
    ) -> list[EditInstruction]:
        """Extract validated masked-redraw instructions from ``edit`` tags."""

        if max_edits is not None and max_edits < 0:
            raise ValueError("max_edits 不能小于 0")
        if max_edits == 0:
            return []
        visible_text = PromptDirector._remove_think_content(model_output)
        instructions: list[EditInstruction] = []
        for match in _EDIT_TAG_RE.finditer(visible_text):
            try:
                instruction = PromptDirector._edit_instruction_from_match(match)
            except PromptDirectorError as exc:
                if exc.detail == "missing_edit_prompt":
                    continue
                raise
            instructions.append(instruction)
            if max_edits is not None and len(instructions) >= max_edits:
                break
        return instructions

    @staticmethod
    def extract_pic_prompts(
        model_output: str, *, max_prompts: int | None = None
    ) -> list[str]:
        """提取普通 LLM 回复中的所有有效 ``pic`` 提示词。

        ``think`` 块中的标签会被忽略。调用方可以用 ``max_prompts`` 限制
        本次实际处理的图片数量，而无需改变原始回复的清理结果。

        Args:
            model_output: LLM 返回的完整文本。
            max_prompts: 最多返回多少条提示词；``None`` 表示不限制。

        Returns:
            按 ``pic`` 标签出现顺序排列的规范化提示词列表。

        Raises:
            ValueError: ``max_prompts`` 小于零。
            PromptDirectorError: 被选中的标签含有无效提示词。
        """
        if max_prompts is not None and max_prompts < 0:
            raise ValueError("max_prompts 不能小于 0")
        if max_prompts == 0:
            return []

        return [
            instruction.prompt
            for instruction in PromptDirector.extract_pic_instructions(
                model_output,
                max_prompts=max_prompts,
            )
        ]

    @staticmethod
    def clean_response_text(model_output: str) -> str:
        """移除 LLM 控制标签和隐藏思考，同时保留可发送给用户的正文。"""
        marker = "\x00"
        text = PromptDirector._remove_think_content(model_output, marker)
        text = _PIC_TAG_RE.sub(marker, text)
        text = _ANY_PIC_TAG_RE.sub(marker, text)
        text = _EDIT_TAG_RE.sub(marker, text)
        text = _ANY_EDIT_TAG_RE.sub(marker, text)
        escaped_marker = re.escape(marker)
        text = re.sub(rf"(?m)^[ \t]*(?:{escaped_marker}[ \t]*)+(?:\r?\n|$)", "", text)
        text = re.sub(
            rf"[ \t]*{escaped_marker}(?:[ \t]*{escaped_marker})*[ \t]*",
            " ",
            text,
        )
        text = re.sub(r"[ \t]+(?=\r?$)", "", text, flags=re.MULTILINE)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def parse_picture_response(
        model_output: str, *, max_prompts: int | None = None
    ) -> PictureResponse:
        """同时解析绘图控制标签并生成可见回复正文。

        Args:
            model_output: LLM 返回的完整文本。
            max_prompts: 最多返回多少条提示词；正文始终移除全部控制标签。

        Returns:
            包含有序提示词和清理后正文的不可变解析结果。
        """
        instructions = PromptDirector.extract_pic_instructions(
            model_output, max_prompts=max_prompts
        )
        edits = PromptDirector.extract_edit_instructions(
            model_output,
            max_edits=max_prompts,
        )
        return PictureResponse(
            prompts=tuple(item.prompt for item in instructions),
            text=PromptDirector.clean_response_text(model_output),
            negative_prompts=tuple(item.negative_prompt for item in instructions),
            pipelines=tuple(item.pipeline for item in instructions),
            character_queries=tuple(item.character_queries for item in instructions),
            edits=tuple(edits),
        )

    @staticmethod
    def _normalize_pipeline(value: Any) -> str:
        PromptDirector._reject_embedded_control_tags(value, field="pipeline")
        raw = str(value or "").strip()
        if not raw:
            return ""
        aliases = {
            "base": "base",
            "原图": "base",
            "不放大": "base",
            "rtx": "rtx",
            "高清放大": "rtx",
            "iterative": "iterative",
            "迭代": "iterative",
            "迭代放大": "iterative",
        }
        normalized = aliases.get(raw.casefold())
        if normalized is None:
            raise PromptDirectorError("LLM 返回了未知生成管线")
        return normalized

    @staticmethod
    def _normalize_character_queries(value: Any) -> tuple[str, ...]:
        """Normalize bounded character identity hints without authorizing them."""

        if value is None:
            return ()
        if isinstance(value, str):
            values: list[str] | tuple[str, ...] = re.split(
                r"[;；\r\n]+",
                html.unescape(value),
            )
        elif isinstance(value, (list, tuple)):
            if any(not isinstance(item, str) for item in value):
                raise PromptDirectorError("LLM 返回了非字符串角色声明")
            values = value
        else:
            raise PromptDirectorError("LLM 返回了无效的角色声明字段")
        result: list[str] = []
        seen: set[str] = set()
        for raw in values:
            item = re.sub(r"\s+", " ", html.unescape(raw)).strip(" ,;；")
            if not item:
                continue
            PromptDirector._reject_embedded_control_tags(item, field="characters")
            if re.search(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", item):
                raise PromptDirectorError("LLM 返回的角色声明含有控制字符")
            if len(item) > 160:
                raise PromptDirectorError("LLM 返回的单个角色声明过长")
            if item.count("|") > 1:
                raise PromptDirectorError("角色声明只能包含一个 name|work 分隔符")
            name, separator, work = item.partition("|")
            name = name.strip()
            work = work.strip() if separator else ""
            if not name:
                raise PromptDirectorError("角色声明缺少角色名")
            item = f"{name}|{work}" if work else name
            if any(unicodedata.category(char) in {"Cf", "Cc"} for char in item):
                raise PromptDirectorError("LLM 返回的角色声明含有不可见控制字符")
            key = item.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
            if len(result) > 4:
                raise PromptDirectorError("单张图最多声明 4 个明确角色")
        return tuple(result)

    @staticmethod
    def _normalize_inpaint_mode(value: Any) -> str:
        PromptDirector._reject_embedded_control_tags(value, field="mode")
        aliases = {
            "quick": "quick",
            "快速": "quick",
            "局部": "quick",
            "lanpaint": "lanpaint",
            "精细": "lanpaint",
            "多轮": "lanpaint",
        }
        normalized = aliases.get(str(value or "quick").strip().casefold())
        if normalized is None:
            raise PromptDirectorError("LLM 返回了未知重绘模式")
        return normalized

    @staticmethod
    def _remove_think_content(model_output: str, replacement: str = " ") -> str:
        """删除完整、嵌套及未闭合的 ``think`` 区域。"""
        visible_parts: list[str] = []
        cursor = 0
        depth = 0
        control_spans = tuple(
            sorted(
                (
                    (match.start(), match.end())
                    for pattern in (_PIC_TAG_RE, _EDIT_TAG_RE)
                    for match in pattern.finditer(model_output)
                ),
                key=lambda item: item[0],
            )
        )
        for match in _THINK_TAG_RE.finditer(model_output):
            if any(start <= match.start() < end for start, end in control_spans):
                continue
            if depth == 0:
                visible_parts.append(model_output[cursor : match.start()])

            is_closing_tag = match.group(0).lstrip().startswith("</")
            if is_closing_tag:
                if depth > 0:
                    depth -= 1
                    if depth == 0:
                        visible_parts.append(replacement)
                else:
                    visible_parts.append(replacement)
            else:
                depth += 1
            cursor = match.end()

        if depth == 0:
            visible_parts.append(model_output[cursor:])
        return "".join(visible_parts)

    @staticmethod
    def _normalize_prompt(prompt: str) -> str:
        """规范化并校验一条英文绘图提示词。"""
        prompt = html.unescape(prompt)
        PromptDirector._reject_embedded_control_tags(prompt, field="prompt")
        PromptDirector.reject_provider_error_output(prompt)
        prompt = re.sub(r"\s*[\r\n]+\s*", ", ", prompt)
        prompt = re.sub(r"(?:,\s*){2,}", ", ", prompt)
        prompt = re.sub(r"\s{2,}", " ", prompt).strip(" ,")
        if not prompt:
            raise PromptDirectorError("LLM 返回的提示词为空")
        if len(prompt) > 6000:
            raise PromptDirectorError("LLM 返回的提示词过长")
        prompt_without_loras = LORA_TAG_PATTERN.sub("", prompt)
        if re.search(r"[\u3400-\u9fff]", prompt_without_loras):
            raise PromptDirectorError("LLM 返回了中文提示词，请更换模型或调整附加要求")
        return prompt

    @staticmethod
    def provider_error_code(model_output: Any) -> str:
        """Return a stable code when Provider failure prose leaked as model output."""

        text = str(model_output or "").strip()
        for code, pattern in _PROVIDER_ERROR_PATTERNS:
            if pattern.search(text):
                return code
        return ""

    @staticmethod
    def reject_provider_error_output(model_output: Any) -> None:
        """Fail closed without persisting or logging the raw Provider response."""

        text = str(model_output or "")
        code = PromptDirector.provider_error_code(text)
        if code:
            raise PromptDirectorError(
                "绘图模型 Provider 调用失败，已停止且不会提交 ComfyUI",
                f"provider_output_error:{code}:chars={len(text)}",
                fatal=True,
            )

    @staticmethod
    def _normalize_negative_prompt(prompt: str) -> str:
        """Normalize an optional negative prompt without allowing control tags."""
        prompt = html.unescape(str(prompt or ""))
        PromptDirector._reject_embedded_control_tags(prompt, field="negative")
        prompt = re.sub(r"\s*[\r\n]+\s*", ", ", prompt)
        prompt = re.sub(r"(?:,\s*){2,}", ", ", prompt)
        prompt = re.sub(r"\s{2,}", " ", prompt).strip(" ,")
        if not prompt:
            return ""
        if len(prompt) > 2000:
            raise PromptDirectorError("LLM 返回的负面提示词过长")
        if LORA_TAG_PATTERN.search(prompt):
            raise PromptDirectorError("负面提示词不能包含 LoRA 标签")
        if re.search(r"[\u3400-\u9fff]", prompt):
            raise PromptDirectorError("LLM 返回了中文负面提示词")
        return prompt
