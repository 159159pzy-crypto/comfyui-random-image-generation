"""Comfy Anima 插件的纯逻辑访问控制与敏感词过滤。

本模块不依赖 AstrBot。调用方负责识别管理员身份，并通过
``AccessBypass`` 明确传入管理员可用的绕过权限。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Final, Iterable, Mapping, Pattern


class FilterLevel(str, Enum):
    """敏感词过滤等级。"""

    NONE = "none"
    LITE = "lite"
    FULL = "full"


class AccessReason(str, Enum):
    """访问检查的判定原因。"""

    ALLOWED = "allowed"
    GLOBAL_LOCKED = "global_locked"
    GROUP_NOT_WHITELISTED = "group_not_whitelisted"
    SENSITIVE_CONTENT = "sensitive_content"


DEFAULT_LITE_WORDS: Final[frozenset[str]] = frozenset(
    {
        "儿童色情",
        "未成年色情",
        "幼女色情",
        "强奸",
        "迷奸",
        "兽交",
        "乱伦",
        "恐怖主义",
        "child porn",
        "child pornography",
        "underage sex",
        "loli porn",
        "lolicon porn",
        "rape",
        "date rape",
        "bestiality",
        "incest",
        "terrorism",
    }
)

DEFAULT_FULL_WORDS: Final[frozenset[str]] = frozenset(
    {
        "色情",
        "裸体",
        "全裸",
        "露点",
        "性交",
        "口交",
        "乳头",
        "生殖器",
        "阴茎",
        "阴道",
        "精液",
        "血腥",
        "肢解",
        "断头",
        "内脏",
        "自杀",
        "自残",
        "海洛因",
        "可卡因",
        "冰毒",
        "porn",
        "pornography",
        "nude",
        "nudity",
        "naked",
        "sex",
        "sexual intercourse",
        "oral sex",
        "blowjob",
        "nipple",
        "genitals",
        "penis",
        "vagina",
        "semen",
        "hentai",
        "gore",
        "dismemberment",
        "decapitation",
        "entrails",
        "suicide",
        "self harm",
        "heroin",
        "cocaine",
        "methamphetamine",
    }
)

_CJK_PATTERN: Final[Pattern[str]] = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_ENGLISH_TOKEN_PATTERN: Final[Pattern[str]] = re.compile(r"[a-z0-9]+")


def normalize_filter_level(level: FilterLevel | str) -> FilterLevel:
    """将配置值规范化为过滤等级。

    Args:
        level: 枚举值或不区分大小写的 ``none/lite/full`` 字符串。

    Returns:
        规范化后的 ``FilterLevel``。

    Raises:
        ValueError: 过滤等级无效。
        TypeError: 传入值既不是字符串也不是 ``FilterLevel``。
    """
    if isinstance(level, FilterLevel):
        return level
    if not isinstance(level, str):
        raise TypeError("过滤等级必须是字符串或 FilterLevel")
    try:
        return FilterLevel(level.strip().lower())
    except ValueError as exc:
        raise ValueError("过滤等级必须是 none、lite 或 full") from exc


def normalize_group_id(group_id: str | int | None) -> str | None:
    """将群号规范化为便于比较和持久化的字符串。

    Args:
        group_id: 群号；``None`` 或空字符串代表私聊。

    Returns:
        去除首尾空白的群号字符串，或 ``None``。
    """
    if group_id is None:
        return None
    normalized = str(group_id).strip()
    return normalized or None


def _normalize_text(text: str) -> str:
    """统一全角字符、大小写和组合字符。"""
    return unicodedata.normalize("NFKC", text).casefold()


def _compact_text(text: str) -> str:
    """删除标点和空白，以识别被简单分隔的中文敏感词。"""
    return "".join(character for character in text if character.isalnum())


@dataclass(frozen=True, slots=True)
class SensitiveMatch:
    """一次敏感词命中。

    Attributes:
        term: 词库中命中的规范词。
        level: 该词所属的最低过滤等级。
    """

    term: str
    level: FilterLevel


@dataclass(frozen=True, slots=True)
class _SensitiveRule:
    """内部使用的已编译敏感词规则。"""

    term: str
    level: FilterLevel
    compact_term: str | None = None
    pattern: Pattern[str] | None = None

    def matches(self, normalized_text: str, compact_text: str) -> bool:
        """判断规则是否命中指定文本。"""
        if self.compact_term is not None:
            return self.compact_term in compact_text
        return bool(self.pattern and self.pattern.search(normalized_text))


class SensitiveWordFilter:
    """支持 none/lite/full 的中英文敏感词过滤器。

    中文规则会忽略词语之间的空白和标点。英文规则使用单词边界，
    避免 ``rape`` 误命中 ``grape``，同时允许短语以空格、连字符或
    其他标点分隔。Full 等级始终包含 Lite 等级的规则。
    """

    def __init__(
        self,
        lite_words: Iterable[str] | None = None,
        full_words: Iterable[str] | None = None,
    ) -> None:
        """初始化过滤器。

        Args:
            lite_words: Lite 级词库；``None`` 使用内置词库。
            full_words: Full 级附加词库；``None`` 使用内置词库。
        """
        lite_source = DEFAULT_LITE_WORDS if lite_words is None else lite_words
        full_source = DEFAULT_FULL_WORDS if full_words is None else full_words
        lite_terms = self._prepare_terms(lite_source)
        full_terms = self._prepare_terms(full_source)

        self._lite_rules = tuple(
            self._compile_rule(term, FilterLevel.LITE) for term in lite_terms
        )
        self._full_rules = tuple(
            self._compile_rule(term, FilterLevel.FULL)
            for term in full_terms
            if term not in lite_terms
        )

    @staticmethod
    def _prepare_terms(words: Iterable[str]) -> tuple[str, ...]:
        """清理、去重并稳定排序词库。"""
        terms: set[str] = set()
        for word in words:
            if not isinstance(word, str):
                raise TypeError("敏感词必须是字符串")
            normalized = _normalize_text(word).strip()
            if normalized:
                terms.add(normalized)
        return tuple(sorted(terms))

    @staticmethod
    def _compile_rule(term: str, level: FilterLevel) -> _SensitiveRule:
        """将一个词编译为中文紧凑匹配或英文边界匹配规则。"""
        if _CJK_PATTERN.search(term):
            compact_term = _compact_text(term)
            return _SensitiveRule(term=term, level=level, compact_term=compact_term)

        tokens = _ENGLISH_TOKEN_PATTERN.findall(term)
        if not tokens:
            return _SensitiveRule(
                term=term,
                level=level,
                pattern=re.compile(re.escape(term), re.IGNORECASE),
            )
        body = r"[\W_]*".join(re.escape(token) for token in tokens)
        pattern = re.compile(
            rf"(?<![a-z0-9]){body}(?![a-z0-9])",
            re.IGNORECASE,
        )
        return _SensitiveRule(term=term, level=level, pattern=pattern)

    def find_matches(
        self, text: str, level: FilterLevel | str
    ) -> tuple[SensitiveMatch, ...]:
        """查找指定等级下的全部敏感词命中。

        Args:
            text: 待检查的用户输入或提示词。
            level: 生效的过滤等级。

        Returns:
            按等级和词语稳定排序的命中元组。

        Raises:
            TypeError: ``text`` 不是字符串。
            ValueError: 过滤等级无效。
        """
        if not isinstance(text, str):
            raise TypeError("待过滤内容必须是字符串")
        normalized_level = normalize_filter_level(level)
        if normalized_level is FilterLevel.NONE or not text:
            return ()

        normalized_text = _normalize_text(text)
        compact_text = _compact_text(normalized_text)
        rules = self._lite_rules
        if normalized_level is FilterLevel.FULL:
            rules += self._full_rules
        return tuple(
            SensitiveMatch(term=rule.term, level=rule.level)
            for rule in rules
            if rule.matches(normalized_text, compact_text)
        )

    def contains_sensitive(self, text: str, level: FilterLevel | str) -> bool:
        """返回文本在指定等级下是否包含敏感词。"""
        return bool(self.find_matches(text, level))


@dataclass(frozen=True, slots=True)
class AccessBypass:
    """由调用方确认身份后授予的显式绕过权限。

    本类不会判断用户是否为管理员。普通用户应传入默认实例。
    """

    global_lock: bool = False
    whitelist: bool = False
    sensitive_words: bool = False


@dataclass(frozen=True, slots=True)
class AccessDecision:
    """一次绘图访问检查的结果。"""

    allowed: bool
    reason: AccessReason
    filter_level: FilterLevel
    matches: tuple[SensitiveMatch, ...] = ()


@dataclass(slots=True)
class AccessPolicy:
    """可由插件配置和管理命令构造的访问策略。

    Attributes:
        global_locked: 是否仅允许具有锁定绕过权的调用。
        whitelist_enabled: 是否启用群白名单。
        whitelist_groups: 允许使用绘图功能的群号集合。
        default_filter_level: 未单独配置的会话所用过滤等级。
        group_filter_levels: 各群覆盖的过滤等级。

    Notes:
        群白名单只约束群聊。私聊仍会接受全局锁定和敏感词检查。
    """

    global_locked: bool = False
    whitelist_enabled: bool = False
    whitelist_groups: set[str] = field(default_factory=set)
    default_filter_level: FilterLevel = FilterLevel.LITE
    group_filter_levels: dict[str, FilterLevel] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """规范化从 JSON/YAML 配置传入的字符串值。"""
        self.default_filter_level = normalize_filter_level(self.default_filter_level)
        self.whitelist_groups = {
            normalized
            for group_id in self.whitelist_groups
            if (normalized := normalize_group_id(group_id)) is not None
        }
        self.group_filter_levels = {
            normalized: normalize_filter_level(level)
            for group_id, level in self.group_filter_levels.items()
            if (normalized := normalize_group_id(group_id)) is not None
        }


class AccessController:
    """组合全局锁定、群白名单和分级敏感词检查。"""

    def __init__(
        self,
        policy: AccessPolicy | None = None,
        word_filter: SensitiveWordFilter | None = None,
    ) -> None:
        """初始化访问控制器。

        Args:
            policy: 初始访问策略。
            word_filter: 自定义过滤器；省略时使用内置两级词库。
        """
        self.policy = policy or AccessPolicy()
        self.word_filter = word_filter or SensitiveWordFilter()

    def get_filter_level(self, group_id: str | int | None) -> FilterLevel:
        """获取群聊或私聊当前生效的过滤等级。"""
        normalized = normalize_group_id(group_id)
        if normalized is None:
            return self.policy.default_filter_level
        return self.policy.group_filter_levels.get(
            normalized, self.policy.default_filter_level
        )

    def set_group_filter_level(
        self, group_id: str | int, level: FilterLevel | str
    ) -> FilterLevel:
        """设置群级过滤策略并返回规范化后的等级。

        Raises:
            ValueError: 群号为空或过滤等级无效。
        """
        normalized_group = normalize_group_id(group_id)
        if normalized_group is None:
            raise ValueError("设置群级策略时群号不能为空")
        normalized_level = normalize_filter_level(level)
        self.policy.group_filter_levels[normalized_group] = normalized_level
        return normalized_level

    def clear_group_filter_level(self, group_id: str | int) -> bool:
        """删除群级覆盖；返回此前是否存在覆盖。"""
        normalized_group = normalize_group_id(group_id)
        if normalized_group is None:
            return False
        return self.policy.group_filter_levels.pop(normalized_group, None) is not None

    def set_global_lock(self, enabled: bool) -> None:
        """动态切换全局锁定状态。"""
        self.policy.global_locked = bool(enabled)

    def set_whitelist_groups(self, group_ids: Iterable[str | int]) -> None:
        """整体替换群白名单。"""
        self.policy.whitelist_groups = {
            normalized
            for group_id in group_ids
            if (normalized := normalize_group_id(group_id)) is not None
        }

    def is_group_whitelisted(self, group_id: str | int | None) -> bool:
        """判断群是否在白名单；私聊不属于群白名单检查范围。"""
        normalized = normalize_group_id(group_id)
        return normalized is None or normalized in self.policy.whitelist_groups

    def evaluate(
        self,
        text: str,
        group_id: str | int | None = None,
        *,
        bypass: AccessBypass | None = None,
    ) -> AccessDecision:
        """按固定优先级检查一次绘图请求。

        检查顺序为全局锁定、群白名单、敏感词。调用方应仅在确认
        管理员身份及对应配置开关后，才传入启用字段的 ``AccessBypass``。

        Args:
            text: 用户原始输入或最终绘图提示词。
            group_id: QQ 群号；``None`` 表示私聊。
            bypass: 调用方授予的绕过权限。

        Returns:
            包含允许状态、原因、等级和敏感词命中的不可变结果。
        """
        normalized_group = normalize_group_id(group_id)
        filter_level = self.get_filter_level(normalized_group)
        effective_bypass = bypass or AccessBypass()

        if self.policy.global_locked and not effective_bypass.global_lock:
            return AccessDecision(
                allowed=False,
                reason=AccessReason.GLOBAL_LOCKED,
                filter_level=filter_level,
            )

        if (
            self.policy.whitelist_enabled
            and normalized_group is not None
            and normalized_group not in self.policy.whitelist_groups
            and not effective_bypass.whitelist
        ):
            return AccessDecision(
                allowed=False,
                reason=AccessReason.GROUP_NOT_WHITELISTED,
                filter_level=filter_level,
            )

        matches: tuple[SensitiveMatch, ...] = ()
        if not effective_bypass.sensitive_words:
            matches = self.word_filter.find_matches(text, filter_level)
        if matches:
            return AccessDecision(
                allowed=False,
                reason=AccessReason.SENSITIVE_CONTENT,
                filter_level=filter_level,
                matches=matches,
            )

        return AccessDecision(
            allowed=True,
            reason=AccessReason.ALLOWED,
            filter_level=filter_level,
        )


def policy_from_mapping(config: Mapping[str, object]) -> AccessPolicy:
    """从通用配置映射创建访问策略。

    该辅助函数只读取与风控有关的键，便于调用方直接传入插件配置。

    Args:
        config: 包含风控键的映射。

    Returns:
        已完成类型规范化的 ``AccessPolicy``。

    Raises:
        TypeError: 白名单或群级策略不是预期的容器类型。
    """
    raw_whitelist = config.get("whitelist_groups", ())
    if isinstance(raw_whitelist, (str, bytes)) or not isinstance(
        raw_whitelist, Iterable
    ):
        raise TypeError("whitelist_groups 必须是群号列表")

    raw_group_levels = config.get("group_filter_levels", {})
    if not isinstance(raw_group_levels, Mapping):
        raise TypeError("group_filter_levels 必须是群号到等级的映射")

    return AccessPolicy(
        global_locked=bool(config.get("global_locked", False)),
        whitelist_enabled=bool(config.get("whitelist_enabled", False)),
        whitelist_groups=set(raw_whitelist),
        default_filter_level=normalize_filter_level(
            config.get("default_filter_level", FilterLevel.LITE)  # type: ignore[arg-type]
        ),
        group_filter_levels=dict(raw_group_levels),  # type: ignore[arg-type]
    )
