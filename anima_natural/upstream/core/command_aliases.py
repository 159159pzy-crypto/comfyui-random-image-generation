"""Pure, context-aware normalization for QQ command option aliases.

The plugin exposes several commands whose option names overlap deliberately.
For example, ``--m`` means a ControlNet mode while drawing, but an edit mode
for semantic redraw, masked inpaint, and character replacement.  This module
keeps those meanings explicit by requiring a parsing context.  It performs no
I/O and never guesses arbitrary prefixes.

The generation context expands every control mode to a fixed-width pair::

    --m p d --m p r

becomes::

    --control-mode pose --control-mode depth --control-mode reference

Repeated modes are removed while preserving their first-seen order.  Other
options retain their original order and values for the command-specific parser
to validate.
"""

from __future__ import annotations

from collections.abc import Iterable


CONTEXT_GENERATION = "generation"
CONTEXT_SEMANTIC_REDRAW = "semantic_redraw"
CONTEXT_INPAINT = "inpaint"
CONTEXT_CHARACTER_SWAP = "character_swap"

SUPPORTED_CONTEXTS = frozenset(
    {
        CONTEXT_GENERATION,
        CONTEXT_SEMANTIC_REDRAW,
        CONTEXT_INPAINT,
        CONTEXT_CHARACTER_SWAP,
    }
)


class CommandAliasError(ValueError):
    """Raised when an exact alias is present but its required value is invalid."""


_GENERATION_OPTION_ALIASES = {
    "--negative": "--negative",
    "--n": "--negative",
    "--seed": "--seed",
    "--sd": "--seed",
    "--size": "--size",
    "--sz": "--size",
    "--steps": "--steps",
    "--st": "--steps",
    "--cfg": "--cfg",
    "--c": "--cfg",
    "--pipeline": "--pipeline",
    "--p": "--pipeline",
    "--denoise": "--denoise",
    "--d": "--denoise",
    "--upscale": "--upscale",
    "--u": "--upscale",
    "--no-upscale": "--no-upscale",
    "--nu": "--no-upscale",
    "--llm": "--llm",
    "--l": "--llm",
    "--llmcc": "--llm-character-change",
    "--lcc": "--llm-character-change",
    "--raw": "--raw",
    "--no-llm": "--raw",
    "--r": "--raw",
    "--preset": "--preset",
    "--lora-preset": "--preset",
    "--pr": "--preset",
}

_CHARACTER_SWAP_OPTION_ALIASES = {
    "--mode": "--mode",
    "--m": "--mode",
    "--weight": "--weight",
    "--w": "--weight",
    "--preset": "--preset",
    "--pr": "--preset",
    "--size": "--size",
    "--sz": "--size",
    "--negative": "--negative",
    "--n": "--negative",
    "--preview": "--preview",
    "--v": "--preview",
    "--no-character-lora": "--no-character-lora",
    "--no-lora": "--no-character-lora",
    "--nl": "--no-character-lora",
}

_GENERATION_CHARACTER_SWAP_OPTION_ALIASES = {
    "--mode": "--swap-mode",
    "--weight": "--swap-weight",
    "--w": "--swap-weight",
    "--preview": "--swap-preview",
    "--v": "--swap-preview",
    "--no-character-lora": "--swap-no-character-lora",
    "--no-lora": "--swap-no-character-lora",
    "--nl": "--swap-no-character-lora",
}

_PIPELINE_VALUES = {
    "b": "base",
    "base": "base",
    "原图": "base",
    "r": "rtx",
    "rtx": "rtx",
    "放大": "rtx",
    "i": "iterative",
    "iterative": "iterative",
    "迭代": "iterative",
    "迭代放大": "iterative",
}

_CONTROL_MODE_VALUES = {
    "p": "pose",
    "pose": "pose",
    "姿势": "pose",
    "姿态": "pose",
    "d": "depth",
    "depth": "depth",
    "深度": "depth",
    "构图": "depth",
    "l": "lineart",
    "lineart": "lineart",
    "线稿": "lineart",
    "线描": "lineart",
    "r": "reference",
    "reference": "reference",
    "参考": "reference",
    "参考图": "reference",
}

_SEMANTIC_REDRAW_MODE_VALUES = {
    "p": "preserve",
    "preserve": "preserve",
    "保守": "preserve",
    "保持": "preserve",
    "b": "balanced",
    "balanced": "balanced",
    "balance": "balanced",
    "平衡": "balanced",
    "默认": "balanced",
    "f": "free",
    "free": "free",
    "自由": "free",
    "重画": "free",
}

_INPAINT_MODE_VALUES = {
    "q": "quick",
    "quick": "quick",
    "快速": "quick",
    "局部": "quick",
    "l": "lanpaint",
    "lanpaint": "lanpaint",
    "精细": "lanpaint",
    "多轮": "lanpaint",
}

_CHARACTER_SWAP_MODE_VALUES = {
    "k": "keep-outfit",
    "keep-outfit": "keep-outfit",
    "t": "target-outfit",
    "target-outfit": "target-outfit",
}

_CONTROL_OPTIONS = frozenset({"--m", "--control", "--control-mode"})
_CHARACTER_CHANGE_MODE_VALUES = frozenset(
    {
        "c",
        "cc",
        "char-change",
        "char_change",
        "character-change",
        "character_change",
    }
)


def _normalized_value(value: str) -> str:
    return str(value).strip().casefold()


def _require_value(tokens: tuple[str, ...], index: int, option: str) -> str:
    if index + 1 >= len(tokens):
        raise CommandAliasError(f"{option} 缺少参数")
    return tokens[index + 1]


def _normalize_enum_value(
    value: str,
    aliases: dict[str, str],
    *,
    option: str,
    supported: str,
) -> str:
    normalized = aliases.get(_normalized_value(value))
    if normalized is None:
        raise CommandAliasError(f"{option} 仅支持 {supported}")
    return normalized


def _normalize_control_modes(
    tokens: tuple[str, ...],
    start: int,
) -> tuple[tuple[str, ...], int]:
    """Consume one or more exact control-mode values after a control option."""

    index = start + 1
    modes: list[str] = []
    while index < len(tokens):
        mode = _CONTROL_MODE_VALUES.get(_normalized_value(tokens[index]))
        if mode is None:
            break
        modes.append(mode)
        index += 1
    if not modes:
        raise CommandAliasError(
            f"{tokens[start]} 仅支持 p/pose、d/depth、l/lineart 或 r/reference"
        )
    return tuple(modes), index


def _generation_requests_character_swap(tokens: tuple[str, ...]) -> bool:
    """Detect an explicit generation-scoped character-change activator."""

    for index, token in enumerate(tokens):
        normalized = token.casefold()
        if normalized in {"--llmcc", "--lcc", "--llm-character-change"}:
            return True
        if normalized not in {"--llm", "--l"}:
            continue
        cursor = index + 1
        while cursor < len(tokens) and not tokens[cursor].startswith("--"):
            value = _normalized_value(tokens[cursor])
            if value in _CHARACTER_CHANGE_MODE_VALUES:
                return True
            if (
                value == "char"
                and cursor + 1 < len(tokens)
                and _normalized_value(tokens[cursor + 1]) == "change"
            ):
                return True
            cursor += 1
    return False


def normalize_command_aliases(
    tokens: Iterable[str],
    *,
    context: str,
) -> tuple[str, ...]:
    """Return canonical command tokens for one exact parsing context.

    Unknown option tokens are intentionally left unchanged so the owning
    command parser can report them.  Known enum-bearing options are validated
    exactly; values such as ``it`` are never treated as prefixes of
    ``iterative``.
    """

    normalized_context = str(context).strip().casefold()
    if normalized_context not in SUPPORTED_CONTEXTS:
        raise CommandAliasError(
            "不支持的命令别名上下文: "
            f"{context!r}; 支持 {', '.join(sorted(SUPPORTED_CONTEXTS))}"
        )

    source = tuple(str(token) for token in tokens)
    output: list[str] = []
    seen_control_modes: set[str] = set()
    index = 0
    generation_character_swap = bool(
        normalized_context == CONTEXT_GENERATION
        and _generation_requests_character_swap(source)
    )

    if normalized_context == CONTEXT_CHARACTER_SWAP:
        option_aliases = _CHARACTER_SWAP_OPTION_ALIASES
    else:
        option_aliases = _GENERATION_OPTION_ALIASES

    while index < len(source):
        token = source[index]

        if (
            normalized_context == CONTEXT_GENERATION
            and token.casefold() in _CONTROL_OPTIONS
        ):
            if (
                generation_character_swap
                and token.casefold() == "--m"
                and index + 1 < len(source)
                and _normalized_value(source[index + 1])
                in _CHARACTER_SWAP_MODE_VALUES
            ):
                output.extend(
                    (
                        "--swap-mode",
                        _normalize_enum_value(
                            source[index + 1],
                            _CHARACTER_SWAP_MODE_VALUES,
                            option=token,
                            supported="k/keep-outfit 或 t/target-outfit",
                        ),
                    )
                )
                index += 2
                continue
            modes, next_index = _normalize_control_modes(source, index)
            for mode in modes:
                if mode in seen_control_modes:
                    continue
                seen_control_modes.add(mode)
                output.extend(("--control-mode", mode))
            index = next_index
            continue

        canonical_option = (
            _GENERATION_CHARACTER_SWAP_OPTION_ALIASES.get(token.casefold())
            if generation_character_swap
            else None
        ) or option_aliases.get(token.casefold(), token)
        if (
            normalized_context in {CONTEXT_SEMANTIC_REDRAW, CONTEXT_INPAINT}
            and token.casefold() == "--m"
        ):
            canonical_option = "--mode"

        output.append(canonical_option)

        if canonical_option == "--pipeline":
            value = _require_value(source, index, token)
            output.append(
                _normalize_enum_value(
                    value,
                    _PIPELINE_VALUES,
                    option=token,
                    supported="b/base、r/rtx 或 i/iterative",
                )
            )
            index += 2
            continue

        if canonical_option in {"--mode", "--swap-mode"}:
            value = _require_value(source, index, token)
            if canonical_option == "--swap-mode":
                aliases = _CHARACTER_SWAP_MODE_VALUES
                supported = "k/keep-outfit 或 t/target-outfit"
            elif normalized_context == CONTEXT_SEMANTIC_REDRAW:
                aliases = _SEMANTIC_REDRAW_MODE_VALUES
                supported = "p/preserve、b/balanced 或 f/free"
            elif normalized_context == CONTEXT_INPAINT:
                aliases = _INPAINT_MODE_VALUES
                supported = "q/quick 或 l/lanpaint"
            elif normalized_context == CONTEXT_CHARACTER_SWAP:
                aliases = _CHARACTER_SWAP_MODE_VALUES
                supported = "k/keep-outfit 或 t/target-outfit"
            else:
                # Generation deliberately reserves --m for control modes and
                # never guesses a legacy edit-mode meaning.
                output.append(value)
                index += 2
                continue
            output.append(
                _normalize_enum_value(
                    value,
                    aliases,
                    option=token,
                    supported=supported,
                )
            )
            index += 2
            continue

        index += 1

    return tuple(output)


__all__ = [
    "CONTEXT_CHARACTER_SWAP",
    "CONTEXT_GENERATION",
    "CONTEXT_INPAINT",
    "CONTEXT_SEMANTIC_REDRAW",
    "SUPPORTED_CONTEXTS",
    "CommandAliasError",
    "normalize_command_aliases",
]
