"""Fail-closed semantic replacement for one selected Anima subject.

This module deliberately does not perform image editing.  It rewrites a
prompt, replaces exactly one selected character identity, and preserves the
remaining outfit, pose, composition, scene and style terms unless the caller
explicitly requests the target character's metadata-backed default outfit.
"""

from __future__ import annotations

import json
import math
import re
import shlex
import unicodedata
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping, Optional, Sequence

from ..core.command_aliases import (
    CONTEXT_CHARACTER_SWAP,
    normalize_command_aliases,
)
from ..core.lora import LoraWorkflowError, canonical_lora_name, extract_lora_selections
from ..models import LoraIdentityExpectation, LoraSelection
from .danbooru_index import escape_prompt_tag
from .lora_catalog import LoraRecord
from .lora_prompting import (
    character_identity_trigger_candidates,
    choose_character_identity_trigger,
    is_character_identity_trigger_candidate,
)
from .lora_semantic import (
    LoraSemanticIndex,
    SemanticEntry,
    semantic_source_fingerprint,
)
from .provider_response import response_text as _provider_response_text
from .subject_slots import (
    SubjectSelectionError,
    analyze_prompt_subject_selection,
)


SWAP_MODE_KEEP_OUTFIT = "keep-outfit"
SWAP_MODE_TARGET_OUTFIT = "target-outfit"
SWAP_MODES = frozenset({SWAP_MODE_KEEP_OUTFIT, SWAP_MODE_TARGET_OUTFIT})

FEATURE_HAIR_STYLE = "hair_style"
FEATURE_HAIR_COLOR = "hair_color"
FEATURE_HAIR_ORNAMENT = "hair_ornament"
FEATURE_EYE_COLOR = "eye_color"
FEATURE_UNIQUE_BODY_PARTS = "unique_body_parts"
FEATURE_BODY_SHAPE = "body_shape"
FEATURE_EAR_SHAPE = "ear_shape"
DEFAULT_CHARACTER_FEATURE_SWAP_CATEGORIES = (
    FEATURE_HAIR_STYLE,
    FEATURE_HAIR_COLOR,
    FEATURE_HAIR_ORNAMENT,
    FEATURE_EYE_COLOR,
    FEATURE_UNIQUE_BODY_PARTS,
    FEATURE_BODY_SHAPE,
    FEATURE_EAR_SHAPE,
)

_CLASSIFICATION_FIELDS = (
    "source_identity_ids",
    "outfit_ids",
    "pose_action_ids",
    "composition_ids",
    "scene_lighting_ids",
    "style_quality_ids",
    "uncertain_ids",
)
_MULTI_SUBJECT_KEYS = frozenset(
    {
        "multiplepeople",
        "multiplegirls",
        "multipleboys",
        "group",
        "crowd",
        "twogirls",
        "twoboys",
        "couple",
        "duo",
        "trio",
    }
)
_SPLIT_NAME_RE = re.compile(r"\s*(?:/|\||；|;|，|,)\s*")
_WEIGHT_SUFFIX_RE = re.compile(r":\s*[+-]?(?:\d+(?:\.\d+)?|\.\d+)\s*$")
_EXPLICIT_LORA_FILE_RE = re.compile(
    r"\.(?:safetensors|ckpt|pt|bin)$",
    flags=re.IGNORECASE,
)
_TARGET_ROLE_PREFIX_RE = re.compile(
    r"^(?:(?:目标|新|替换后(?:的)?)\s*)?(?:角色|人物)\s*[:：]?\s*",
    flags=re.IGNORECASE,
)
_UNTRUSTED_CONFIDENCE_DIRECTIVE_RE = re.compile(
    r"(?:[,，。；;]\s*)?"
    r"(?:置信度|可信度|confidence)\s*"
    r"(?:(?:只需|需要|需|满足|达到|设为|设置为|降低到|降到|大于|小于)\s*)?"
    r"(?:>=|<=|>|<|=)?\s*"
    r"(?:为|到)?\s*"
    r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)\s*%?\s*"
    r"(?:即可|就行|以上|以下|左右)?",
    flags=re.IGNORECASE,
)
_STRICT_LORA_TAG_RE = re.compile(
    r"<lora:([^<>:]+):([+-]?(?:\d+(?:\.\d+)?|\.\d+))>",
    re.IGNORECASE,
)
_GENERIC_NON_IDENTITY_KEYS = frozenset(
    {
        "1girl",
        "1boy",
        "solo",
        "masterpiece",
        "bestquality",
        "highquality",
        "veryaesthetic",
    }
)
_OBVIOUS_OUTFIT_MARKERS = (
    "dress",
    "uniform",
    "outfit",
    "clothes",
    "clothing",
    "shirt",
    "skirt",
    "coat",
    "jacket",
    "pants",
    "boots",
    "shoes",
    "gloves",
    "hat",
    "armor",
    "suit",
    "costume",
    "attire",
    "robe",
    "kimono",
    "swimsuit",
    "bikini",
    "lingerie",
    "服装",
    "衣服",
    "制服",
    "裙",
    "外套",
    "鞋",
    "手套",
    "帽",
    "盔甲",
)
_DETERMINISTIC_OUTFIT_MARKERS = (
    *_OBVIOUS_OUTFIT_MARKERS,
    "underwear",
    "bra",
    "panties",
    "buruma",
    "blouse",
    "sweater",
    "hoodie",
    "shorts",
    "trousers",
    "stockings",
    "thighhighs",
    "thigh highs",
    "socks",
    "sleeves",
    "necktie",
    "apron",
    "cap",
)
_APPEARANCE_MARKERS = (
    "hair",
    "braid",
    "bangs",
    "ahoge",
    "ponytail",
    "twintails",
    "twin tails",
    "eyes",
    "eye",
    "pupil",
    "heterochromia",
    "eyebrow",
    "eyelash",
    "skin",
    "face",
    "facial",
    "nose",
    "lips",
    "teeth",
    "freckles",
    "mole",
    "beauty mark",
    "scar",
    "tattoo",
    "marking",
    "ears",
    "horns",
    "wings",
    "tail",
    "halo",
    "fangs",
    "species",
    "petite",
    "slender",
    "slim",
    "curvy",
    "muscular",
    "tall",
    "short stature",
    "breasts",
    "wide hips",
    "narrow waist",
    "body build",
    "figure",
    "发",
    "头发",
    "辫",
    "刘海",
    "呆毛",
    "马尾",
    "双马尾",
    "眼",
    "瞳",
    "异色瞳",
    "眉",
    "睫毛",
    "皮肤",
    "脸",
    "鼻",
    "唇",
    "牙",
    "雀斑",
    "痣",
    "美人痣",
    "伤疤",
    "纹身",
    "耳",
    "角",
    "翅",
    "尾",
    "娇小",
    "纤细",
    "苗条",
    "丰满",
    "肌肉",
    "高挑",
    "矮小",
    "胸",
    "胯",
    "腰",
    "体型",
    "身材",
)
_TRANSIENT_APPEARANCE_STATE_MARKERS = (
    "open mouth",
    "closed mouth",
    "smile",
    "grin",
    "blush",
    "crying",
    "tears",
    "wink",
    "winking",
    "closed eyes",
    "half-closed eyes",
    "one eye closed",
    "looking at viewer",
    "looking back",
    "looking away",
    "angry",
    "embarrassed",
    "surprised",
    "张嘴",
    "闭嘴",
    "微笑",
    "笑",
    "脸红",
    "哭",
    "泪",
    "眨眼",
    "闭眼",
    "半闭眼",
    "看向",
    "回头看",
    "生气",
    "害羞",
    "惊讶",
)
_ATOMIC_APPEARANCE_EXCLUDED_MARKERS = (
    "ornament",
    "ribbon",
    "bow",
    "glasses",
    "eyepatch",
    "makeup",
    "headwear",
    "headpiece",
    "collar",
    "earrings",
    "necklace",
    "accessory",
    "fake ears",
    "fake animal ears",
    "fake rabbit ears",
    "fake bunny ears",
    "fake cat ears",
    "fake fox ears",
    "fake wolf ears",
    "fake horns",
    "reflection",
    "lighting",
    "view",
    "perspective",
)
_DETERMINISTIC_PRESERVED_VISUAL_TERMS = frozenset(
    {
        "1girl",
        "1boy",
        "1other",
        "solo",
        "^ ^ ^",
        "areola slip",
        "areolae",
        "bare shoulders",
        "breasts",
        "cleavage",
        "covered nipples",
        "feet",
        "foot focus",
        "navel",
        "nipples",
        "saliva",
        "saliva trail",
        "spread legs",
        "thighs",
        "toes",
        "tongue",
        "tongue out",
    }
)
_ATOMIC_HAIR_MODIFIERS = frozenset(
    {
        "black",
        "blonde",
        "blond",
        "brown",
        "red",
        "blue",
        "green",
        "purple",
        "pink",
        "white",
        "silver",
        "grey",
        "gray",
        "orange",
        "yellow",
        "aqua",
        "teal",
        "cyan",
        "dark",
        "light",
        "pale",
        "vivid",
        "short",
        "medium",
        "long",
        "very",
        "shoulder",
        "waist",
        "floor",
        "length",
        "messy",
        "wavy",
        "curly",
        "straight",
        "spiked",
        "streaked",
        "gradient",
        "multicolored",
        "two",
        "tone",
        "to",
    }
)
_ATOMIC_EYE_MODIFIERS = frozenset(
    {
        "black",
        "brown",
        "red",
        "blue",
        "green",
        "purple",
        "pink",
        "white",
        "silver",
        "grey",
        "gray",
        "orange",
        "yellow",
        "gold",
        "golden",
        "aqua",
        "teal",
        "cyan",
        "amber",
        "violet",
        "dark",
        "light",
        "pale",
        "bright",
    }
)
_ORIGINAL_CHARACTER_RE = re.compile(
    r"(?:原创(?:角色|人物)?|自创(?:角色|人物)?|原创设定|自设|"
    r"(?<![a-z0-9])oc(?![a-z0-9])|original\s+character)",
    re.IGNORECASE,
)
_GENERIC_IDENTITY_QUALIFIERS = frozenset(
    {"character", "fiction", "game", "original", "series", "work"}
)
_CONCEPT_DESCRIPTOR_TOKENS = frozenset(
    {
        "black",
        "blonde",
        "blue",
        "brown",
        "casual",
        "dark",
        "formal",
        "green",
        "grey",
        "gray",
        "long",
        "maid",
        "military",
        "orange",
        "pink",
        "purple",
        "red",
        "school",
        "short",
        "silver",
        "traditional",
        "white",
        "yellow",
        *(
            marker
            for marker in (*_APPEARANCE_MARKERS, *_OBVIOUS_OUTFIT_MARKERS)
            if marker.isascii() and marker.isalpha()
        ),
    }
)
_NON_CHARACTER_REQUEST_MARKERS = (
    "背景",
    "场景",
    "天空",
    "光线",
    "灯光",
    "构图",
    "镜头",
    "姿势",
    "动作",
    "表情",
    "衣服",
    "服装",
    "颜色",
    "风格",
    "画风",
    "泳装",
    "比基尼",
    "三点式",
    "内衣",
    "丝袜",
    "白丝",
    "黑丝",
    "袜",
    "礼服",
    "制服",
    "裙",
    "外套",
    "上衣",
    "裤",
    "鞋",
    "配饰",
)
_GENERIC_SOURCE_QUERY_KEYS = frozenset(
    {
        "角色",
        "人物",
        "主角",
        "原角色",
        "原人物",
        "当前角色",
        "当前人物",
        "这个角色",
        "这个人物",
        "那个角色",
        "那个人物",
        "图中角色",
        "图中人物",
        "图片中角色",
        "图片中人物",
        "画面中角色",
        "画面中人物",
        "她",
        "他",
        "ta",
    }
)
_NO_CHARACTER_LORA_RE = re.compile(
    r"(?:无需|不用|不要|别|勿|禁用|禁止|不想|不一定要|"
    r"不需要|不使用|不加载|不添加|不挂载)"
    r"(?:再)?(?:强制)?\s*(?:使用|加载|添加|挂载|用)?\s*"
    r"(?:任何\s*)?(?:目标\s*)?(?:角色\s*)?lo[-\s]?ra",
    re.IGNORECASE,
)
_REQUIRE_CHARACTER_LORA_RE = re.compile(
    r"(?<!不)(?<!别)(?<!勿)(?<!禁)"
    r"(?:请|请务必|务必|必须|一定要|强制)?\s*"
    r"(?:使用|加载|添加|挂载|用)\s*"
    r"(?:目标\s*)?(?:角色\s*)?lo[-\s]?ra(?:文件|模型)?"
    r"(?!\s*(?:风格|组合|预设|串|style|preset))"
    r"(?=$|[\s,，。.;；、]|并|同时|然后|再)",
    re.IGNORECASE,
)
_OPTIONAL_CHARACTER_LORA_RE = re.compile(
    r"(?:可以|可|有的话|如果有|若有|不建议|建议)?\s*"
    r"(?:使用|加载|添加|挂载|用)\s*"
    r"(?:目标\s*)?(?:角色\s*)?lo[-\s]?ra(?:文件|模型)?",
    re.IGNORECASE,
)


class CharacterSwapError(RuntimeError):
    """A semantic replacement could not be proven safe."""

    def __init__(
        self,
        user_message: str,
        *,
        code: str = "character_swap_error",
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.user_message = user_message
        self.code = code
        self.details = dict(details or {})
        super().__init__(user_message)


@dataclass(frozen=True)
class CharacterSwapRequest:
    source_query: str
    target_query: str
    tags: str = ""
    mode: str = SWAP_MODE_KEEP_OUTFIT
    target_lora_strength: float = 0.65
    preset: str = ""
    width: Optional[int] = None
    height: Optional[int] = None
    negative_prompt: str = ""
    preview: bool = False
    use_target_lora: bool = True
    require_target_lora: bool = False
    edit_requirement: str = ""
    pipeline: str = ""
    prompt_expansion_mode: str = "standard"
    seed: Optional[int] = None
    steps: Optional[int] = None
    cfg: Optional[float] = None
    enable_upscale: Optional[bool] = None
    denoise: Optional[float] = None
    semantic_identity_confidence: float = 0.0
    semantic_identity_index_verified: bool = False
    semantic_identity_anchor_source: str = ""
    semantic_identity_match_variant: str = ""
    semantic_identity_match_type: str = ""
    semantic_identity_candidate_count: int = 0
    semantic_identity_query_count: int = 0
    semantic_identity_canonical_tag: str = ""
    semantic_appearance_source: str = ""
    semantic_appearance_count: int = 0
    semantic_appearance_sample_count: int = 0
    require_target_appearance_slots: bool = False
    ignored_control_directives: tuple[str, ...] = ()
    feature_swap_enabled: bool = False
    feature_swap_categories: tuple[str, ...] = ()
    source_subject_count: int = 1
    source_selector_terms: tuple[str, ...] = ()
    protected_subject_terms: tuple[str, ...] = ()
    source_selector_basis: str = ""
    source_selector_direction_used: bool = False

    @property
    def source_kind(self) -> str:
        return "tags" if self.tags.strip() else "image"


@dataclass(frozen=True)
class CharacterSwapClassification:
    source_identity_ids: tuple[int, ...]
    outfit_ids: tuple[int, ...]
    pose_action_ids: tuple[int, ...]
    composition_ids: tuple[int, ...]
    scene_lighting_ids: tuple[int, ...]
    style_quality_ids: tuple[int, ...]
    uncertain_ids: tuple[int, ...]
    target_identity_trigger_id: Optional[int]
    target_appearance_trigger_ids: tuple[int, ...]
    target_default_outfit_trigger_ids: tuple[int, ...]
    subject_count: int
    confidence: float


@dataclass(frozen=True)
class CharacterSwapPreparation:
    request: CharacterSwapRequest
    tags: tuple[str, ...]
    negative_prompt: str
    target_record: Optional[LoraRecord]
    target_metadata_record: Optional[LoraRecord]
    source_record: Optional[LoraRecord]
    preserved_loras: tuple[LoraSelection, ...]
    preserved_lora_records: tuple[LoraRecord, ...]
    removed_character_loras: tuple[LoraSelection, ...]
    deterministic_target_trigger: str
    target_trigger_words: tuple[str, ...]
    source_identity_hints: tuple[str, ...]
    target_identity_hints: tuple[str, ...]
    target_activation_terms: tuple[str, ...] = ()
    verified_target_appearance_terms: tuple[str, ...] = ()
    verified_target_appearance_evidence: tuple[tuple[str, str], ...] = ()
    source_tag_categories: tuple[str, ...] = ()
    source_tag_verified: tuple[bool, ...] = ()
    source_tag_canonicals: tuple[str, ...] = ()
    source_subject_count: int = 1
    multi_subject: bool = False
    source_selector_term_ids: tuple[int, ...] = ()
    source_selector_terms: tuple[str, ...] = ()
    protected_subject_terms: tuple[str, ...] = ()
    source_selector_basis: str = "single_subject"
    source_selector_direction_used: bool = False


@dataclass(frozen=True)
class CharacterSwapPlan:
    prompt: str
    negative_prompt: str
    loras: tuple[LoraSelection, ...]
    expectations: tuple[LoraIdentityExpectation, ...]
    target_record: Optional[LoraRecord]
    source_record: Optional[LoraRecord]
    target_identity_trigger: str
    removed_terms: tuple[str, ...]
    kept_terms: tuple[str, ...]
    added_terms: tuple[str, ...]
    suppressed_terms: tuple[str, ...]
    suppress_default_style: bool
    target_activation_terms: tuple[str, ...] = ()
    preserved_character_lora_names: tuple[str, ...] = ()
    promoted_uncertain_count: int = 0
    promoted_uncertain_outfit_count: int = 0
    promoted_uncertain_visual_count: int = 0
    promoted_source_canonical_count: int = 0
    corrected_general_source_count: int = 0
    danbooru_verified_tag_count: int = 0
    danbooru_character_tag_count: int = 0
    danbooru_copyright_tag_count: int = 0
    classification_confidence: float = 0.0
    effective_confidence_floor: float = 0.0
    reauthorized_appearance_terms: tuple[str, ...] = ()
    feature_swap_categories: tuple[str, ...] = ()
    feature_swap_removed_count: int = 0
    target_appearance_terms: tuple[str, ...] = ()
    target_appearance_source: str = ""
    target_appearance_evidence_sources: tuple[str, ...] = ()
    target_feature_categories: tuple[str, ...] = ()
    missing_target_feature_categories: tuple[str, ...] = ()
    model_native_fallback_categories: tuple[str, ...] = ()
    target_slot_decisions: tuple[tuple[str, str], ...] = ()
    source_subject_count: int = 1
    multi_subject: bool = False
    source_selector_terms: tuple[str, ...] = ()
    protected_subject_terms: tuple[str, ...] = ()
    source_selector_basis: str = "single_subject"
    source_selector_direction_used: bool = False

    def preview_text(self) -> str:
        removed = "、".join(self.removed_terms[:12]) or "无"
        added = "、".join(self.added_terms[:12]) or "无"
        selector = "、".join(self.source_selector_terms[:8]) or "单角色"
        protected = "、".join(self.protected_subject_terms[:8]) or "无"
        subject_summary = (
            f"识别角色：{self.source_subject_count} 名\n"
            f"选中依据：{self.source_selector_basis}｜{selector}\n"
            f"保护其他角色：{protected}\n"
            if self.multi_subject
            else ""
        )
        return (
            "语义换角预览（未提交 ComfyUI）\n"
            + subject_summary
            +
            f"目标身份来源：{self.target_record.name if self.target_record else '纯语义 Tags（未使用角色 LoRA）'}\n"
            f"保留 Tags：{len(self.kept_terms)} 项\n"
            f"移除身份：{removed}\n"
            f"新增身份：{added}\n"
            "说明：这是整图语义重绘，不是像素级或局部替换。"
        )


def _clean_text(value: Any, limit: int = 500) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _identity_key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    text = _WEIGHT_SUFFIX_RE.sub("", text)
    # Spaces and underscores are presentation variants in Danbooru/Manager
    # metadata.  Keeping one while dropping the other made otherwise identical
    # values such as ``BanG Dream!`` and ``bang_dream!`` compare differently.
    return re.sub(r"[^0-9a-z@\u3400-\u9fff]+", "", text)


def _is_generic_source_query(value: Any) -> bool:
    return _identity_key(value) in _GENERIC_SOURCE_QUERY_KEYS


def _is_original_character_query(value: Any) -> bool:
    return bool(_ORIGINAL_CHARACTER_RE.search(str(value or "")))


def is_original_character_query(value: Any) -> bool:
    """Public bounded check shared by the Provider bridge and planner."""

    return _is_original_character_query(value)


def _is_stable_appearance_term(value: Any) -> bool:
    """Recognize identity-bearing appearance without deleting expressions.

    Eye and mouth words are especially ambiguous in prompt tags.  Stable color,
    shape and anatomy may be replaced during a character change, while transient
    gaze, blinking, smiling and mouth-state tags belong to the preserved action /
    expression layer.
    """

    folded = unicodedata.normalize("NFKC", str(value or "")).casefold()
    if any(marker in folded for marker in _TRANSIENT_APPEARANCE_STATE_MARKERS):
        return False
    return bool(
        any(marker in folded for marker in _APPEARANCE_MARKERS)
        or _character_feature_categories_for_term(folded)
    )


def _is_deterministic_source_appearance_term(value: Any) -> bool:
    """Recognize only atomic, unweighted source-identity appearance tags.

    This predicate is deliberately narrower than ``_is_stable_appearance_term``.
    It is used only to repair a classifier's ``uncertain`` bucket and must not
    absorb accessories, camera phrases, expressions or compound prompt groups.
    """

    raw = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    unescaped = raw.replace(r"\(", "(").replace(r"\)", ")")
    if re.fullmatch(
        r"alternate breast size\s*\((?:smaller|small|medium|large|larger|huge)\)",
        unescaped,
    ):
        return True
    if (
        not raw
        or any(character in raw for character in "()[]{}<>,，;；:\\")
        or re.search(r"(?:^|\s)(?:and|break)(?:\s|$)", raw, re.IGNORECASE)
        or any(marker in raw for marker in _TRANSIENT_APPEARANCE_STATE_MARKERS)
        or any(marker in raw for marker in _OBVIOUS_OUTFIT_MARKERS)
        or any(marker in raw for marker in _ATOMIC_APPEARANCE_EXCLUDED_MARKERS)
    ):
        return False
    if re.fullmatch(r"fake(?: [a-z]+)? (?:ears|horns|tail|wings)", raw):
        return False
    normalized = re.sub(r"[_-]+", " ", raw)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    if normalized.endswith(" hair"):
        modifiers = normalized[:-5].split()
        return bool(modifiers) and all(
            token in _ATOMIC_HAIR_MODIFIERS for token in modifiers
        )
    if normalized in {
        "ahoge",
        "bangs",
        "crossed bangs",
        "hair between eyes",
        "twin tails",
        "twintails",
        "ponytail",
        "side ponytail",
        "low ponytail",
        "high ponytail",
        "braid",
        "braids",
        "twin braids",
        "side braid",
        "single braid",
        "hair bun",
        "double bun",
        "bob cut",
        "hime cut",
    }:
        return True
    if normalized.endswith(" eyes"):
        modifiers = normalized[:-5].split()
        return bool(modifiers) and all(
            token in _ATOMIC_EYE_MODIFIERS for token in modifiers
        )
    if normalized in {
        "heterochromia",
        "slit pupils",
        "round pupils",
        "heart shaped pupils",
    }:
        return True
    if re.fullmatch(
        r"(?:fair|pale|light|dark|brown|black|white|tan|tanned|glowing) skin",
        normalized,
    ):
        return True
    if re.fullmatch(
        r"(?:(?:black|blonde|blond|brown|red|blue|green|purple|pink|white|"
        r"silver|grey|gray|orange|yellow|aqua|teal|cyan) )?streaks",
        normalized,
    ):
        return True
    if re.fullmatch(
        r"(?:(?:black|blue|brown|gold|golden|green|orange|pink|purple|red|"
        r"silver|white|yellow|tilted|broken|double|round|rectangular) )*halo",
        normalized,
    ):
        return True
    if re.fullmatch(
        r"(?:(?:v shaped|thick|thin|short|long|arched|straight) )?eyebrows",
        normalized,
    ):
        return True
    if re.fullmatch(
        r"(?:animal|cat|fox|wolf|dog|horse|rabbit|bunny|dragon|demon|angel) girl",
        normalized,
    ):
        return True
    if re.fullmatch(
        r"(?:(?:cat|fox|wolf|dog|horse|rabbit|bunny|elf|pointed|animal) ears|"
        r"(?:(?:dragon|demon|oni|ram|bull) )?horns|"
        r"(?:(?:cat|fox|wolf|dog|horse|rabbit|dragon|demon) )?tail|"
        r"(?:(?:angel|demon|bat|bird|dragon) )?wings|"
        r"angel|demon|elf|vampire)",
        normalized,
    ):
        return True
    if re.fullmatch(
        r"(?:petite|slender|slim|curvy|muscular|tall|short stature|"
        r"small breasts|medium breasts|large breasts|huge breasts|"
        r"small areolae|large areolae|wide hips|"
        r"narrow waist|teenage girl|young woman|adult woman)",
        normalized,
    ):
        return True
    if re.fullmatch(
        r"(?:(?:small|button|sharp) nose|(?:thin|full) lips|freckles|"
        r"(?:beauty mark|mole|scar|tattoo)(?: (?:under|below|beside|on) "
        r"(?:the )?(?:left|right)? ?(?:eye|cheek|face))?)",
        normalized,
    ):
        return True
    return False


def _character_feature_categories_for_term(value: Any) -> frozenset[str]:
    """Classify one atomic prompt term into the workflow's feature families.

    The upstream ``CharacterFeatureSwapNode`` delegates this decision to an
    unconstrained LLM.  Here the same seven-category scope is deterministic:
    uncertain ordinary terms are left untouched, while weighted/composite
    appearance groups are sent back to the strict classifier path.
    """

    raw = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    raw = raw.replace(r"\(", "(").replace(r"\)", ")")
    if not raw or _is_weighted_or_composite_prompt_term(raw):
        return frozenset()
    normalized = re.sub(r"[_-]+", " ", raw)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if any(marker in normalized for marker in _TRANSIENT_APPEARANCE_STATE_MARKERS):
        return frozenset()

    categories: set[str] = set()
    hair_colors = {
        "black",
        "blonde",
        "blond",
        "brown",
        "red",
        "blue",
        "green",
        "purple",
        "pink",
        "white",
        "silver",
        "grey",
        "gray",
        "orange",
        "yellow",
        "aqua",
        "teal",
        "cyan",
        "gold",
        "golden",
        "lavender",
        "multicolored",
        "gradient",
        "two tone",
        "streaked",
    }
    hair_styles = {
        "short",
        "medium",
        "long",
        "very long",
        "shoulder length",
        "waist length",
        "floor length",
        "messy",
        "wavy",
        "curly",
        "straight",
        "spiked",
    }
    if normalized.endswith(" hair"):
        prefix = normalized[:-5].strip()
        if any(
            re.search(rf"(?:^|\s){re.escape(color)}(?:\s|$)", prefix)
            for color in hair_colors
        ):
            categories.add(FEATURE_HAIR_COLOR)
        if any(
            re.search(rf"(?:^|\s){re.escape(style)}(?:\s|$)", prefix)
            for style in hair_styles
        ):
            categories.add(FEATURE_HAIR_STYLE)
    if re.fullmatch(
        r"(?:(?:black|blue|brown|gold|golden|green|orange|pink|purple|red|"
        r"silver|white|yellow|multicolored|colored|color) )?(?:hair )?streaks?",
        normalized,
    ):
        categories.add(FEATURE_HAIR_COLOR)
    if normalized in {
        "ahoge",
        "bangs",
        "crossed bangs",
        "parted bangs",
        "hair between eyes",
        "twin tails",
        "twintails",
        "ponytail",
        "side ponytail",
        "low ponytail",
        "high ponytail",
        "braid",
        "braids",
        "twin braids",
        "side braid",
        "single braid",
        "hair bun",
        "double bun",
        "single side bun",
        "one side up",
        "bob cut",
        "hime cut",
    }:
        categories.add(FEATURE_HAIR_STYLE)
    if re.fullmatch(
        r"(?:(?:black|blue|brown|gold|golden|green|orange|pink|purple|red|"
        r"silver|white|yellow|rabbit ear|animal ear) )?"
        r"(?:x hair ornament|hair ornament|hair ornaments|hairclip|hairclips|hair ribbon|"
        r"hair bow|hair flower|hair pin|hairband|headband)",
        normalized,
    ):
        categories.add(FEATURE_HAIR_ORNAMENT)

    if normalized == "heterochromia" or normalized.endswith(" eyes"):
        eye_prefix = normalized[:-5].strip() if normalized.endswith(" eyes") else ""
        if normalized == "heterochromia" or any(
            re.search(rf"(?:^|\s){re.escape(color)}(?:\s|$)", eye_prefix)
            for color in _ATOMIC_EYE_MODIFIERS
        ):
            categories.add(FEATURE_EYE_COLOR)

    if not normalized.startswith("fake ") and re.fullmatch(
        r"(?:(?:black|blue|brown|gold|golden|green|orange|pink|purple|red|"
        r"silver|white|yellow|tilted|broken|double|round|rectangular) )*halo|"
        r"(?:(?:dragon|demon|oni|ram|bull) )?horns|"
        r"(?:(?:cat|fox|wolf|dog|horse|rabbit|dragon|demon) )?tail|"
        r"(?:(?:angel|demon|bat|bird|dragon) )?wings|"
        r"fangs?|freckles|beauty mark|mole(?: under (?:mouth|eye))?|scar|tattoo|"
        r"angel|demon|elf|vampire|"
        r"(?:animal|cat|fox|wolf|dog|horse|rabbit|bunny|dragon|demon) girl",
        normalized,
    ):
        categories.add(FEATURE_UNIQUE_BODY_PARTS)

    if re.fullmatch(
        r"(?:petite|slender|slim|curvy|muscular|tall|short stature|"
        r"small breasts|medium breasts|large breasts|huge breasts|"
        r"wide hips|narrow waist|pear shaped figure|hourglass figure|"
        r"teenage girl|young woman|adult woman)",
        normalized,
    ) or re.fullmatch(
        r"alternate breast size \((?:smaller|small|medium|large|larger|huge)\)",
        normalized,
    ):
        categories.add(FEATURE_BODY_SHAPE)

    if not normalized.startswith("fake ") and not any(
        marker in normalized
        for marker in (
            "earring",
            "ear piercing",
            "earpiece",
            "earphone",
            "headphone",
            "in ear",
            "monitor",
        )
    ) and re.fullmatch(
        r"(?:animal|cat|fox|wolf|dog|horse|rabbit|bunny|elf|pointed) ears",
        normalized,
    ):
        categories.add(FEATURE_EAR_SHAPE)
    return frozenset(categories)


def _normalized_feature_swap_categories(values: Sequence[Any]) -> tuple[str, ...]:
    requested = {
        str(value or "").strip().casefold()
        for value in values
        if str(value or "").strip()
    }
    return tuple(
        category
        for category in DEFAULT_CHARACTER_FEATURE_SWAP_CATEGORIES
        if category in requested
    )


def _is_deterministic_accessory_or_garment_term(value: Any) -> bool:
    """Recognize atomic Danbooru garments/accessories before hair/ear checks."""

    raw = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    if (
        not raw
        or any(character in raw for character in "()[]{}<>,，;；:\\")
        or re.search(r"(?:^|\s)(?:and|break)(?:\s|$)", raw, re.IGNORECASE)
    ):
        return False
    normalized = re.sub(r"[_-]+", " ", raw)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized in {
        "bow",
        "bowtie",
        "fake animal ears",
        "fake bunny ears",
        "fake cat ears",
        "fake fox ears",
        "fake rabbit ears",
        "fake wolf ears",
        "hair ornament",
        "hairband",
        "headband",
        "jewelry",
        "playboy bunny",
        "strapless",
    }:
        return True
    for marker in (
        "hairband",
        "headband",
        "bowtie",
        "collar",
        "earrings",
        "leotard",
        "choker",
        "necklace",
        "bracelet",
        "anklet",
        "ribbon",
    ):
        match = re.search(rf"(?:^|\s){re.escape(marker)}$", normalized)
        if match is None:
            continue
        prefix = normalized[: match.start()].strip()
        if re.search(
            r"(?:^|\s)(?:with|looking|holding|blowing|flowing|fluttering)"
            r"(?:\s|$)",
            prefix,
        ):
            return False
        if marker == "ribbon" and re.search(r"(?:^|\s)hair(?:\s|$)", prefix):
            return False
        return len(prefix.split()) <= 4
    return False


def _is_deterministic_outfit_term(value: Any) -> bool:
    """Recognize a bounded top-level garment term safe for deterministic repair."""

    raw = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    if (
        not raw
        or any(character in raw for character in "()[]{}<>,，;；:\\")
        or re.search(r"(?:^|\s)(?:and|break)(?:\s|$)", raw, re.IGNORECASE)
    ):
        return False
    normalized = re.sub(r"[_-]+", " ", raw)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if _is_deterministic_accessory_or_garment_term(normalized):
        return True
    if any(
        re.search(rf"(?:^|\s){re.escape(marker)}(?:\s|$)", normalized)
        for marker in _APPEARANCE_MARKERS
        if marker.isascii()
    ):
        return False
    if re.search(
        r"(?:^|\s)(?:looking|holding|lifting|wearing|under|over|with|"
        r"behind|beside|blowing|flowing|fluttering)(?:\s|$)",
        normalized,
    ):
        return False
    for marker in sorted(
        (
            item
            for item in _DETERMINISTIC_OUTFIT_MARKERS
            if item.isascii()
        ),
        key=len,
        reverse=True,
    ):
        match = re.search(rf"(?:^|\s){re.escape(marker)}$", normalized)
        if match is None:
            continue
        prefix = normalized[: match.start()].strip()
        return len(prefix.split()) <= 5
    return False


def _is_deterministic_preserved_visual_term(value: Any) -> bool:
    """Recognize atomic non-identity visual state safe to keep.

    These terms describe exposure, transient mouth/eye state, pose or generic
    subject anatomy.  They must not become source identity merely because an
    LLM placed them in ``source_identity_ids`` or ``uncertain_ids``.
    """

    raw = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    if (
        not raw
        or any(character in raw for character in "()[]{}<>,，;；:\\")
        or re.search(r"(?:^|\s)(?:and|break)(?:\s|$)", raw, re.IGNORECASE)
    ):
        return False
    normalized = re.sub(r"[_-]+", " ", raw)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized in _DETERMINISTIC_PRESERVED_VISUAL_TERMS:
        return True
    return any(marker == normalized for marker in _TRANSIENT_APPEARANCE_STATE_MARKERS)


def _danbooru_character_parts(value: Any) -> Optional[tuple[str, tuple[str, ...]]]:
    """Parse a bounded Danbooru ``character_(variant)_(work)`` canonical."""

    raw = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    raw = _WEIGHT_SUFFIX_RE.sub("", raw)
    raw = raw.replace(r"\(", "(").replace(r"\)", ")")
    match = re.fullmatch(
        r"(?P<base>[a-z0-9][a-z0-9_.' /&+\-:]{0,79}?)"
        r"[_\s]*(?P<groups>(?:\([^()]{1,80}\)[_\s]*){1,4})",
        raw,
    )
    if match is None:
        return None
    raw_base = match.group("base").strip(" _")
    if " " in raw_base:
        return None
    base = _prompt_term_key(raw_base)
    qualifiers = tuple(
        _prompt_term_key(item)
        for item in re.findall(r"\(([^()]+)\)", match.group("groups"))
        if _prompt_term_key(item)
    )
    if (
        not base
        or base in _GENERIC_NON_IDENTITY_KEYS
        or not qualifiers
        or qualifiers[-1] in _GENERIC_IDENTITY_QUALIFIERS
    ):
        return None
    return base, qualifiers


def _matches_source_character_lineage(value: Any, anchors: Sequence[Any]) -> bool:
    """Match a character variant only to the same base name and copyright."""

    value_key = _prompt_term_key(str(value or ""))
    parsed = _danbooru_character_parts(value)
    if not value_key or parsed is None:
        return False
    base, qualifiers = parsed
    for anchor in anchors:
        anchor_key = _prompt_term_key(str(anchor or ""))
        if not anchor_key:
            continue
        if value_key == anchor_key or base == anchor_key:
            return True
        anchor_parts = _danbooru_character_parts(anchor)
        if anchor_parts is None:
            continue
        anchor_base, anchor_qualifiers = anchor_parts
        if base == anchor_base and qualifiers[-1] == anchor_qualifiers[-1]:
            return True
    return False


def _matches_source_copyright_context(value: Any, anchors: Sequence[Any]) -> bool:
    """Recognize a standalone copyright duplicated beside a source canonical."""

    value_key = _prompt_term_key(str(value or ""))
    if not value_key:
        return False
    return any(
        parts is not None and value_key == parts[1][-1]
        for parts in (_danbooru_character_parts(anchor) for anchor in anchors)
    )


def _is_weighted_or_composite_prompt_term(value: Any) -> bool:
    raw = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not raw or _danbooru_character_parts(raw) is not None:
        return False
    if _WEIGHT_SUFFIX_RE.search(raw):
        return True
    if any(character in raw for character in "[]{}<>,，;；"):
        return True
    return bool(
        (raw.startswith("(") and raw.endswith(")"))
        or re.search(r"(?:^|\s)(?:AND|BREAK)(?:\s|$)", raw)
    )


def _is_composite_source_appearance_term(value: Any) -> bool:
    """Accept only groups whose every component is atomic source appearance."""

    if not _is_weighted_or_composite_prompt_term(value):
        return False
    raw = unicodedata.normalize("NFKC", str(value or "")).strip()
    if raw.startswith("(") and raw.endswith(")"):
        raw = raw[1:-1].strip()
    raw = _WEIGHT_SUFFIX_RE.sub("", raw)
    parts = tuple(
        part.strip()
        for part in re.split(r"[,，;；]|(?:^|\s)(?:AND|BREAK)(?:\s|$)", raw)
        if part.strip()
    )
    return bool(parts) and all(
        _is_deterministic_source_appearance_term(part) for part in parts
    )


def _is_strict_unqualified_source_canonical(value: Any) -> bool:
    """Recognize Danbooru-style unqualified names without concept phrases."""

    raw = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    normalized = re.sub(r"[_-]+", " ", raw)
    return bool(
        "_" in raw
        and " " not in raw
        and _semantic_identity_discovery_anchor_candidate(raw)
        and not any(
            marker in normalized for marker in _ATOMIC_APPEARANCE_EXCLUDED_MARKERS
        )
        and not _is_deterministic_source_appearance_term(raw)
        and not _is_deterministic_outfit_term(raw)
        and not _is_deterministic_preserved_visual_term(raw)
    )


def _source_identity_leaks_into_term(container: Any, suppressed: Any) -> bool:
    """Check exact top-level leaks while keeping compound groups fail-closed."""

    container_key = _prompt_term_key(str(container or ""))
    suppressed_key = _prompt_term_key(str(suppressed or ""))
    if not container_key or not suppressed_key:
        return False
    if container_key == suppressed_key:
        return True
    if not _is_weighted_or_composite_prompt_term(container):
        return False
    return _contains_identity_fragment(
        _identity_key(container),
        _identity_key(suppressed),
    )


def _is_meaningful_identity_key(value: str) -> bool:
    if re.search(r"[\u3400-\u9fff]", value):
        return len(value) >= 2
    return len(value) >= 3


def _escape_lora_identity_trigger(value: str) -> str:
    """Preserve a trained-word spelling while escaping Comfy prompt groups."""

    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    normalized = normalized.replace(r"\(", "(").replace(r"\)", ")")
    return normalized.replace("(", r"\(").replace(")", r"\)")


def _contains_identity_fragment(container: str, fragment: str) -> bool:
    return bool(
        container
        and fragment
        and _is_meaningful_identity_key(fragment)
        and fragment in container
    )


def _strip_no_character_lora_suffix(value: str) -> tuple[str, bool]:
    """Remove a trailing natural-language no-LoRA directive from a target."""

    normalized = unicodedata.normalize("NFKC", str(value or ""))
    match = _NO_CHARACTER_LORA_RE.search(normalized)
    if match is None:
        return normalized, False
    target = normalized[: match.start()].rstrip(" \t，,。；;、:-")
    return target, True


def _strip_required_character_lora_directive(value: str) -> tuple[str, bool]:
    """Consume a positive LoRA control phrase without treating it as an edit."""

    normalized = unicodedata.normalize("NFKC", str(value or ""))
    masked = list(normalized)
    for pattern in (_NO_CHARACTER_LORA_RE, _OPTIONAL_CHARACTER_LORA_RE):
        for match in pattern.finditer(normalized):
            if pattern is _OPTIONAL_CHARACTER_LORA_RE and not re.match(
                r"(?:可以|可|有的话|如果有|若有|不建议|建议)",
                match.group(0).lstrip(),
            ):
                continue
            masked[match.start() : match.end()] = " " * (
                match.end() - match.start()
            )
    positive_matches = list(_REQUIRE_CHARACTER_LORA_RE.finditer("".join(masked)))
    if not positive_matches:
        return normalized, False
    cleaned = normalized
    for match in reversed(positive_matches):
        cleaned = cleaned[: match.start()] + " " + cleaned[match.end() :]
    cleaned = re.sub(r"\s*([，,。.;；、])\s*", r"\1", cleaned)
    cleaned = re.sub(r"[，,。.;；、]{2,}", "，", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" \t，,。.;；、:-")
    return cleaned, True


def is_explicit_lora_reference(value: str) -> bool:
    """Return whether a target is an explicit LoRA file reference.

    A slash by itself is not sufficient evidence: users commonly separate
    aliases with ``/`` (for example ``今汐/今夕``). Only a supported model-file
    suffix or an explicit ``lora:`` prefix activates strict file semantics.
    """

    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    return bool(
        _EXPLICIT_LORA_FILE_RE.search(normalized)
        or normalized.casefold().startswith("lora:")
    )


def _normalize_target_character_query(value: str) -> tuple[str, tuple[str, ...]]:
    """Remove transport/control prose that cannot redefine safety policy."""

    normalized = unicodedata.normalize("NFKC", str(value or "")).strip()
    ignored: list[str] = []
    if _UNTRUSTED_CONFIDENCE_DIRECTIVE_RE.search(normalized):
        normalized = _UNTRUSTED_CONFIDENCE_DIRECTIVE_RE.sub(" ", normalized)
        ignored.append("confidence_override")
    normalized = _TARGET_ROLE_PREFIX_RE.sub("", normalized, count=1)
    normalized = re.sub(r"\s+", " ", normalized).strip(" \t，,。；;")
    return normalized, tuple(ignored)


def _canonical_key(value: Any) -> str:
    return canonical_lora_name(str(value or "")).casefold()


def _basename_key(value: Any) -> str:
    return PurePosixPath(_canonical_key(value)).name


def _dedupe_text(values: Iterable[Any]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_text(value)
        key = _identity_key(text)
        if text and key and key not in seen:
            seen.add(key)
            result.append(text)
    return tuple(result)


def _semantic_identity_anchor_candidate(value: str) -> bool:
    """Validate an LLM-only identity anchor without LoRA filename heuristics.

    Qualified Danbooru character tags may legitimately contain words such as
    ``hat`` or ``eyes`` in the proper name.  The shared LoRA trigger heuristic
    intentionally rejects those substrings, so pure semantic planning permits
    the stricter ``name_(work)`` shape while still rejecting generic subjects.
    """

    folded = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    key = _prompt_term_key(folded)
    if not key or key in _GENERIC_NON_IDENTITY_KEYS:
        return False
    if is_character_identity_trigger_candidate(folded):
        return True
    qualified = re.fullmatch(
        r"(?P<name>[a-z0-9][a-z0-9_.' /&+\-:]{1,63})_?"
        r"\((?P<work>[a-z0-9][a-z0-9_.' /&+\-:]{1,63})\)",
        folded,
    )
    if qualified is None:
        return False
    work_key = _prompt_term_key(qualified.group("work"))
    if work_key in _GENERIC_IDENTITY_QUALIFIERS:
        return False
    name_tokens = tuple(
        token
        for token in re.split(r"[^a-z0-9]+", qualified.group("name"))
        if token
    )
    if name_tokens and all(
        token in _CONCEPT_DESCRIPTOR_TOKENS for token in name_tokens
    ):
        return False
    return True


def _semantic_identity_discovery_anchor_candidate(value: str) -> bool:
    """Accept a safe unqualified character-name candidate for local lookup only.

    Some canonical Danbooru characters, including ``hatsune_miku``, do not use
    a ``character_(work)`` qualifier.  LoRA-oriented trigger heuristics can
    reject those names because a proper-name fragment happens to contain an
    outfit word (``hat`` in ``hatsune``).  This predicate deliberately checks
    whole tokens instead.  It never authorizes the identity by itself; callers
    must require an exact local ``character`` index hit before using it.
    """

    folded = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    key = _prompt_term_key(folded)
    if (
        not key
        or key in _GENERIC_NON_IDENTITY_KEYS
        or "(" in folded
        or ")" in folded
        or not re.fullmatch(r"[a-z0-9][a-z0-9_.' /&+\-:]{1,79}", folded)
    ):
        return False
    name_tokens = tuple(
        token for token in re.split(r"[^a-z0-9]+", folded) if token
    )
    if not name_tokens or all(
        token in _CONCEPT_DESCRIPTOR_TOKENS
        or token in _GENERIC_IDENTITY_QUALIFIERS
        for token in name_tokens
    ):
        return False
    return True


def semantic_identity_anchor_requires_local_exact(value: str) -> bool:
    """Return whether an unqualified discovery anchor needs exact authorization."""

    folded = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    return bool(
        _semantic_identity_discovery_anchor_candidate(folded)
        and not re.search(r"_?\([^()]+\)$", folded)
    )


def _target_identity_anchor_candidate(
    value: str,
    target_query: str,
    *,
    locally_verified: bool = False,
) -> bool:
    """Accept a proven named identity or an explicitly requested original OC."""

    if _semantic_identity_anchor_candidate(value):
        return True
    if locally_verified and _semantic_identity_discovery_anchor_candidate(value):
        return True
    return bool(
        _is_original_character_query(target_query)
        and _prompt_term_key(value) == "originalcharacter"
    )


def _trusted_identity_signature(
    record: LoraRecord,
    semantic_index: LoraSemanticIndex,
) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    names = {
        _identity_key(value)
        for value in _split_names(record.character_name)
        if _identity_key(value)
    }
    works = {
        _identity_key(value)
        for value in _work_title_candidates(record.source_work)
        if _identity_key(value)
    }
    entry = semantic_index.entry_for(record)
    if _entry_is_fresh(entry, record):
        assert entry is not None
        for field_name, target in (
            ("character_names", names),
            ("source_works", works),
        ):
            for fact in entry.effective_facts(field_name):
                if fact.source in {"manual", "observed"} or (
                    fact.confidence >= 0.85 and entry.analysis_confidence >= 0.85
                ):
                    key = _identity_key(fact.value)
                    if key:
                        target.add(key)
    triggers = {
        _prompt_term_key(value)
        for value in character_identity_trigger_candidates(record)
        if _semantic_identity_anchor_candidate(value)
        and "(" in unicodedata.normalize("NFKC", value)
    }
    return frozenset(names), frozenset(works), frozenset(triggers)


def _records_share_proven_identity(
    records: Sequence[LoraRecord],
    semantic_index: LoraSemanticIndex,
) -> bool:
    signatures = [
        _trusted_identity_signature(record, semantic_index) for record in records
    ]
    if len(signatures) < 2:
        return True
    shared_names = set(signatures[0][0])
    shared_works = set(signatures[0][1])
    shared_triggers = set(signatures[0][2])
    for names, works, triggers in signatures[1:]:
        shared_names &= set(names)
        shared_works &= set(works)
        shared_triggers &= set(triggers)
    return bool(shared_triggers or (shared_names and shared_works))


def _semantic_payload_mapping(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    active: Mapping[str, Any] = payload
    for _depth in range(2):
        nested = next(
            (
                active.get(key)
                for key in ("data", "result", "output")
                if isinstance(active.get(key), Mapping)
            ),
            None,
        )
        if not isinstance(nested, Mapping):
            break
        active = nested
    return active


def semantic_identity_lookup_hints(
    payload: Mapping[str, Any],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Extract bounded ASCII discovery hints without authorizing identity."""

    active = _semantic_payload_mapping(payload)

    def values(keys: Sequence[str], limit: int) -> tuple[str, ...]:
        raw: Any = next(
            (active.get(key) for key in keys if active.get(key) is not None),
            [],
        )
        if isinstance(raw, str):
            raw = [item.strip() for item in re.split(r"[,;\n]+", raw) if item.strip()]
        if not isinstance(raw, list):
            raise CharacterSwapError(
                "纯语义角色检索候选字段格式无效",
                code="semantic_target_lookup_hint_type",
            )
        output: list[str] = []
        seen: set[str] = set()
        for item in raw[:limit]:
            if not isinstance(item, str):
                raise CharacterSwapError(
                    "纯语义角色检索候选必须为字符串",
                    code="semantic_target_lookup_hint_type",
                )
            value = unicodedata.normalize("NFKC", item)
            value = re.sub(r"\\([()\[\]{}])", r"\1", value)
            value = re.sub(r"\s+", " ", value).strip(" ,")
            if (
                not value
                or len(value) > 80
                or not value.isascii()
                or not re.fullmatch(r"[A-Za-z0-9_().'/&+ :\-]+", value)
                or re.search(
                    r"(?:embedding|wildcard|lora)\s*:|https?://|\\|\.\.|"
                    r"^(?:assistant|developer|system|user)\s*:",
                    value,
                    re.IGNORECASE,
                )
            ):
                raise CharacterSwapError(
                    "纯语义角色检索候选未通过安全校验",
                    code="semantic_target_lookup_hint_invalid",
                )
            folded = value.casefold()
            if folded not in seen:
                seen.add(folded)
                output.append(value)
        return tuple(output)

    identities = values(
        (
            "identity_candidates",
            "character_name_candidates",
            "lookup_names",
            "romanized_names",
        ),
        8,
    )
    works = values(
        (
            "work_hints",
            "work_hint",
            "copyright_candidates",
            "lookup_works",
        ),
        4,
    )
    return identities, works


def normalize_semantic_identity_payload(
    payload: Mapping[str, Any],
    *,
    allow_original: bool = False,
) -> tuple[tuple[str, ...], float, int]:
    """Canonicalize safe, common semantic-identity JSON response shapes."""

    active = _semantic_payload_mapping(payload)

    canonical = next(
        (
            active.get(key)
            for key in (
                "canonical_identity_tag",
                "identity_tag",
                "character_tag",
                "canonical_tag",
            )
            if isinstance(active.get(key), str) and str(active.get(key)).strip()
        ),
        "",
    )
    raw_tags = next(
        (
            active.get(key)
            for key in ("identity_tags", "character_tags", "tags")
            if active.get(key) is not None
        ),
        None,
    )
    if isinstance(raw_tags, str):
        raw_tags = [
            item.strip()
            for item in re.split(r"[,;\n]+", raw_tags)
            if item.strip()
        ]
    if raw_tags is None:
        raw_tags = []
    if not isinstance(raw_tags, list):
        raise CharacterSwapError(
            "纯语义身份 Tags 字段格式无效",
            code="semantic_target_tag_type",
        )

    appearance = next(
        (
            active.get(key)
            for key in ("appearance_tags", "stable_appearance_tags")
            if active.get(key) is not None
        ),
        [],
    )
    if isinstance(appearance, str):
        appearance = [
            item.strip()
            for item in re.split(r"[,;\n]+", appearance)
            if item.strip()
        ]
    if not isinstance(appearance, list):
        raise CharacterSwapError(
            "纯语义稳定外观 Tags 字段格式无效",
            code="semantic_target_tag_type",
        )
    combined: list[Any] = []
    if canonical:
        combined.append(canonical)
    combined.extend(raw_tags)
    combined.extend(appearance)
    if not 1 <= len(combined) <= 16:
        raise CharacterSwapError(
            "纯语义身份 Tags 数量必须为 1 到 16 项",
            code="semantic_target_tag_count",
        )

    confidence_raw = next(
        (
            active.get(key)
            for key in ("confidence", "score", "certainty")
            if active.get(key) is not None
        ),
        None,
    )
    if isinstance(confidence_raw, bool):
        raise CharacterSwapError(
            "纯语义身份置信度格式无效",
            code="semantic_target_confidence_invalid",
        )
    percent = False
    if isinstance(confidence_raw, str):
        value = confidence_raw.strip()
        percent = value.endswith("%")
        if percent:
            value = value[:-1].strip()
        try:
            confidence = float(value)
        except ValueError as exc:
            raise CharacterSwapError(
                "纯语义身份置信度格式无效",
                code="semantic_target_confidence_invalid",
            ) from exc
    elif isinstance(confidence_raw, (int, float)):
        confidence = float(confidence_raw)
    else:
        raise CharacterSwapError(
            "纯语义身份置信度格式无效",
            code="semantic_target_confidence_invalid",
        )
    if not math.isfinite(confidence):
        raise CharacterSwapError(
            "纯语义身份置信度格式无效",
            code="semantic_target_confidence_invalid",
        )
    if percent or 2.0 <= confidence <= 100.0:
        confidence /= 100.0
    if not 0.0 <= confidence <= 1.0:
        raise CharacterSwapError(
            "纯语义身份置信度超出范围",
            code="semantic_target_confidence_invalid",
        )
    if confidence < 0.8:
        raise CharacterSwapError(
            "绘图模型对目标角色身份不够确定，请补充作品名或英文角色名",
            code="semantic_target_low_confidence",
        )

    tags: list[str] = []
    seen: set[str] = set()
    for index, raw_tag in enumerate(combined):
        if not isinstance(raw_tag, str):
            raise CharacterSwapError(
                "纯语义身份 Tag 必须为字符串",
                code="semantic_target_tag_type",
            )
        tag = unicodedata.normalize("NFKC", raw_tag)
        tag = re.sub(r"\\([()\[\]{}])", r"\1", tag)
        tag = re.sub(r"\s+", " ", tag).strip(" ,")
        folded_tag = tag.casefold()
        if any(ord(char) < 32 or ord(char) > 126 for char in tag):
            raise CharacterSwapError(
                "纯语义身份 Tag 必须使用 ASCII 英文 Danbooru Tags",
                code="semantic_target_non_ascii",
            )
        if (
            not tag
            or len(tag) > 80
            or "," in tag
            or not re.fullmatch(r"[A-Za-z0-9_().'\-:/&+ ]+", tag)
            or re.search(
                r"(?:^|\s)(?:BREAK|AND)(?:\s|$)|"
                r"(?:embedding|wildcard|lora)\s*:|__|"
                r"https?://|\\|\.\.|\.(?:safetensors|ckpt|pt|bin)$|"
                r"(?:ignore|disregard|override|follow|obey).{0,24}"
                r"(?:instruction|rule|prompt)|"
                r"^(?:assistant|developer|system|user)\s*:",
                tag,
                re.IGNORECASE,
            )
            or "<" in tag
            or ">" in tag
        ):
            raise CharacterSwapError(
                "纯语义身份 Tag 未通过安全校验",
                code="semantic_target_unsafe_tag",
            )
        if folded_tag not in seen:
            seen.add(folded_tag)
            tags.append(tag)
        if index > 0 and "(" in tag and _semantic_identity_anchor_candidate(tag):
            raise CharacterSwapError(
                "稳定外观 Tags 中不能混入第二个角色身份锚点",
                code="semantic_target_multiple_identity",
            )
    original_anchor = (
        allow_original and _prompt_term_key(tags[0] if tags else "") == "originalcharacter"
    )
    if not tags or not (
        _semantic_identity_anchor_candidate(tags[0])
        or _semantic_identity_discovery_anchor_candidate(tags[0])
        or original_anchor
    ):
        raise CharacterSwapError(
            "纯语义身份首项不是可验证的角色身份锚点",
            code="semantic_target_identity_anchor",
        )
    if original_anchor:
        stable_appearance = tuple(
            tag for tag in tags[1:] if _is_stable_appearance_term(tag)
        )
        if len(stable_appearance) < 3:
            raise CharacterSwapError(
                "原创角色至少需要 3 项稳定外貌特征",
                code="semantic_original_appearance_missing",
            )
    elif len(tags) > 5:
        # Known characters are anchored by the qualified ``character_(work)``
        # tag.  Extra appearance is optional evidence, never a checklist to be
        # filled from memory; bounding it to four prevents verbose speculative
        # reconstructions from reaching the second-stage classifier.
        raise CharacterSwapError(
            "已知角色最多只允许 4 项高置信稳定外貌候选",
            code="semantic_target_appearance_excessive",
        )

    recognized_fields = {
        "canonical_identity_tag",
        "identity_tag",
        "character_tag",
        "canonical_tag",
        "identity_tags",
        "character_tags",
        "tags",
        "appearance_tags",
        "stable_appearance_tags",
        "confidence",
        "score",
        "certainty",
        "identity_candidates",
        "character_name_candidates",
        "lookup_names",
        "romanized_names",
        "work_hints",
        "work_hint",
        "copyright_candidates",
        "lookup_works",
    }
    ignored_field_count = len(set(active) - recognized_fields)
    return tuple(tags), confidence, ignored_field_count


def _split_names(value: Any) -> tuple[str, ...]:
    return _dedupe_text(_SPLIT_NAME_RE.split(_clean_text(value)))


def _work_title_candidates(value: Any) -> tuple[str, ...]:
    """Keep a complete work title before emitting legacy split hints.

    LoRA metadata often serializes translated work aliases with ``/`` or
    ``|``, but those characters can also belong to the real Copyright name
    (for example ``Fate/Grand Order``). The complete value therefore remains
    the first candidate. Split values are discovery hints only; downstream
    callers must still exact-confirm Copyright/Character evidence.
    """

    complete = _clean_text(value)
    if not complete:
        return ()
    return _dedupe_text((complete, *_SPLIT_NAME_RE.split(complete)))


def _split_prompt_terms(prompt: str) -> tuple[str, ...]:
    """Split top-level prompt terms without damaging weighted groups."""

    result: list[str] = []
    buffer: list[str] = []
    depth = 0
    pairs = {"(": ")", "[": "]", "{": "}"}
    closing = set(pairs.values())
    for character in str(prompt or ""):
        if character in pairs:
            depth += 1
        elif character in closing and depth > 0:
            depth -= 1
        if depth == 0 and character in {",", "，", ";", "；", "\n", "\r"}:
            value = "".join(buffer).strip(" ,")
            if value:
                result.append(value)
            buffer = []
            continue
        buffer.append(character)
    value = "".join(buffer).strip(" ,")
    if value:
        result.append(value)
    return tuple(result)


def _prompt_term_key(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    text = text.strip("()[]{}<>‘’“”\"' ")
    text = re.sub(r"[_-]+", " ", text)
    return _identity_key(text)


def _dedupe_prompt_terms(values: Iterable[Any]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_text(value)
        key = _prompt_term_key(text)
        if text and key and key not in seen:
            seen.add(key)
            result.append(text)
    return tuple(result)


def _target_identity_insert_index(terms: Sequence[str]) -> int:
    """Place the target immediately after subject/cardinality anchors."""

    cardinality_keys = {"1girl", "1boy", "1other"}
    for index, term in enumerate(terms):
        if _prompt_term_key(term) not in cardinality_keys:
            continue
        insert_at = index + 1
        if (
            insert_at < len(terms)
            and _prompt_term_key(terms[insert_at]) == "solo"
        ):
            insert_at += 1
        return insert_at
    for index, term in enumerate(terms):
        if _prompt_term_key(term) == "solo":
            return index + 1
    return 0


def _is_character_record(
    record: LoraRecord,
    semantic_index: LoraSemanticIndex | None = None,
) -> bool:
    category = str(record.category or "").strip().casefold()
    # Character identity evidence is authoritative even if an older archive or
    # manual edit left the broad category stale. Failing closed here prevents
    # a misclassified character LoRA from surviving a semantic replacement.
    if category == "character" or bool(str(record.character_name or "").strip()):
        return True
    if semantic_index is None:
        return False
    entry = semantic_index.entry_for(record)
    if not _entry_is_fresh(entry, record):
        return False
    assert entry is not None
    return bool(
        entry.effective_values("character_names")
        or entry.effective_category == "character"
    )


def _entry_is_fresh(entry: Optional[SemanticEntry], record: LoraRecord) -> bool:
    if entry is None or not entry.overlay_valid:
        return False
    if not entry.source_fingerprint:
        return entry.has_manual_facts
    return (
        entry.source_fingerprint.casefold()
        == semantic_source_fingerprint(record).casefold()
    )


def _trusted_identity_values(
    record: LoraRecord,
    semantic_index: LoraSemanticIndex,
) -> tuple[str, ...]:
    values: list[str] = []
    canonical = canonical_lora_name(record.name)
    basename = PurePosixPath(canonical).name
    values.extend((canonical, basename))
    record_names = _split_names(record.character_name)
    values.extend(record_names)
    record_works = _work_title_candidates(record.source_work)
    # A work title is contextual evidence, not a character identity. Likewise,
    # LoraRecord.aliases mixes filenames, titles, tags and trained words without
    # provenance, so neither may independently authorize an automatic swap.
    for name in record_names:
        for work in record_works:
            values.extend((f"{work} {name}", f"{name} {work}"))

    entry = semantic_index.entry_for(record)
    if _entry_is_fresh(entry, record):
        assert entry is not None
        names = tuple(
            fact.value
            for fact in entry.effective_facts("character_names")
            if fact.source in {"manual", "observed"}
            or (
                fact.confidence >= 0.85
                and entry.analysis_confidence >= 0.85
            )
        )
        raw_aliases = tuple(
            fact.value
            for fact in entry.effective_facts("aliases")
            if fact.source == "manual"
            or (
                fact.source == "llm_inferred"
                and
                fact.confidence >= 0.9
                and entry.analysis_confidence >= 0.85
            )
        )
        works = tuple(
            fact.value
            for fact in entry.effective_facts("source_works")
            if fact.source in {"manual", "observed"}
            or fact.confidence >= 0.9
        )
        work_keys = {
            key
            for work in (*record_works, *works)
            if (key := _identity_key(work))
        }
        aliases = tuple(
            alias
            for alias in raw_aliases
            if (alias_key := _identity_key(alias))
            and alias_key not in work_keys
        )
        values.extend(names)
        values.extend(aliases)
        trusted_names = _dedupe_text((*names, *aliases)) or record_names
        for name in trusted_names:
            for work in works:
                values.extend((f"{work} {name}", f"{name} {work}"))
    return _dedupe_text(values)


def _query_identity_keys(value: str) -> tuple[str, ...]:
    """Build conservative Chinese-natural-language variants for exact/fuzzy lookup."""

    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    variants = [text]
    stripped = re.sub(
        r"(?:这个|那个|一位|一个)?(?:角色|人物|角色名)$",
        "",
        text,
    ).strip()
    variants.append(stripped)
    variants.append(
        re.sub(r"(?:里面|里边|当中|之中|中)?的", " ", stripped).strip()
    )
    decorated = re.sub(r"[《》〈〉【】「」『』\[\]]+", " ", stripped)
    decorated = re.sub(r"\s+", " ", decorated).strip()
    variants.append(decorated)
    parenthetical = tuple(
        match.group(1).strip()
        for match in re.finditer(r"[（(]([^（）()]{1,80})[）)]", decorated)
        if match.group(1).strip()
    )
    variants.extend(parenthetical)
    for item in parenthetical:
        variants.extend(
            part.strip()
            for part in re.split(r"\s*[-–—]\s*", item)
            if part.strip()
        )
    without_alias = re.sub(r"[（(][^（）()]{1,80}[）)]", " ", decorated)
    without_alias = re.sub(r"\s+", " ", without_alias).strip()
    variants.append(without_alias)
    variants.extend(
        part.strip()
        for part in re.split(r"\s*[-–—]\s*", without_alias)
        if part.strip()
    )
    variants.extend(
        part.strip()
        for part in re.split(r"(?:里面|里边|当中|之中|中)?的", without_alias)
        if part.strip()
    )
    return tuple(
        dict.fromkeys(key for item in variants if (key := _identity_key(item)))
    )


def _query_key_matches_identity(
    query_keys: Sequence[str],
    identity_key: str,
    *,
    allow_cjk_suffix: bool,
) -> bool:
    if not identity_key:
        return False
    if identity_key in query_keys:
        return True
    if not allow_cjk_suffix or not re.search(r"[\u3400-\u9fff]", identity_key):
        return False
    if len(identity_key) < 2:
        return False
    return any(
        query_key.endswith(identity_key) or identity_key in query_key
        for query_key in query_keys
    )


def _query_explicit_work_keys(value: str) -> tuple[str, ...]:
    """Extract only explicitly bracketed work titles for cross-work guarding."""

    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    candidates = [
        match.group(1)
        for match in re.finditer(
            r"[《〈【「『]([^》〉】」』]{1,80})[》〉】」』]",
            text,
        )
    ]
    candidates.extend(
        match.group(1)
        for match in re.finditer(
            r"(?:^|[，,。.;；:：])\s*([0-9a-z][0-9a-z _-]{1,79}?)"
            r"(?:里面的|里边的|当中的|之中的|里的|中的|的)"
            r"(?=[^，,。.;；]{1,80}$)",
            text,
        )
    )
    candidates.extend(
        match.group(1)
        for match in re.finditer(
            r"(?:^|[，,。.;；:：])\s*([\u3400-\u9fff][0-9a-z\u3400-\u9fff _-]{1,79}?)"
            r"(?:里面的|里边的|当中的|之中的|里的|中的)"
            r"(?=[^，,。.;；]{1,80}$)",
            text,
        )
    )
    return tuple(
        dict.fromkeys(
            key for candidate in candidates if (key := _identity_key(candidate))
        )
    )


def _trusted_source_work_keys(
    record: LoraRecord,
    semantic_index: LoraSemanticIndex,
) -> frozenset[str]:
    values = list(_work_title_candidates(record.source_work))
    entry = semantic_index.entry_for(record)
    if _entry_is_fresh(entry, record):
        assert entry is not None
        values.extend(
            fact.value
            for fact in entry.effective_facts("source_works")
            if fact.source in {"manual", "observed"} or fact.confidence >= 0.9
        )
    return frozenset(
        key for value in values if (key := _identity_key(value))
    )


def _trusted_semantic_character_facts(
    entry: Optional[SemanticEntry],
    record: LoraRecord,
) -> tuple[Any, ...]:
    if not _entry_is_fresh(entry, record):
        return ()
    assert entry is not None
    return tuple(
        fact
        for fact in entry.effective_facts("character_names")
        if fact.source in {"manual", "observed"}
        or (
            fact.confidence >= 0.85
            and entry.analysis_confidence >= 0.85
        )
    )


def _semantic_pair_identity_hints(
    entry: Optional[SemanticEntry],
    record: LoraRecord,
    query_keys: Sequence[str],
    *,
    allow_cjk_suffix: bool,
) -> tuple[str, ...]:
    """Map a trusted localized name to its adjacent romanized metadata name.

    The result remains discovery-only.  Callers must exact-check it against the
    local Danbooru ``character`` category before it can authorize identity.
    """

    facts = _trusted_semantic_character_facts(entry, record)
    hints: list[str] = []
    for index, fact in enumerate(facts):
        fact_value = str(fact.value or "").strip()
        key = _identity_key(fact_value)
        if not _query_key_matches_identity(
            query_keys,
            key,
            allow_cjk_suffix=allow_cjk_suffix,
        ):
            continue
        if fact_value.isascii() and re.search(r"[A-Za-z]", fact_value):
            # Some archives group all romanized names before all localized
            # names. An explicitly matched ASCII fact is already the answer;
            # borrowing its neighbour would turn ``Rio`` into ``Toki``.
            hints.append(fact_value)
            continue
        # Archives list localized/romanized pairs in order.  Prefer the next
        # ASCII fact so the previous character's romanization is never borrowed.
        for neighbor in (index + 1, index - 1):
            if not 0 <= neighbor < len(facts):
                continue
            value = str(facts[neighbor].value or "").strip()
            if value.isascii() and re.search(r"[A-Za-z]", value):
                hints.append(value)
                break

    if entry is not None and entry.analysis_confidence >= 0.85:
        summary = unicodedata.normalize("NFKC", entry.analysis_summary)
        pairs = re.findall(
            r"([\u3400-\u9fff][\u3400-\u9fff·・]{1,20})\s*"
            r"(?:[（(]([A-Za-z][A-Za-z0-9_ .'-]{1,40})[）)]|"
            r"[/／]([A-Za-z][A-Za-z0-9_ .'-]{1,40}))",
            summary,
        )
        for localized, parenthetical, slash_name in pairs:
            localized_key = _identity_key(localized)
            if _query_key_matches_identity(
                query_keys,
                localized_key,
                allow_cjk_suffix=allow_cjk_suffix,
            ):
                hints.append(parenthetical or slash_name)
    return _dedupe_text(hints)


def _explicit_ascii_identity_hints(value: str) -> tuple[str, ...]:
    """Extract bounded user-written romanized identity hints.

    A parenthetical value such as ``(Viola-bang_dream!_yumemita)`` carries a
    useful leading identity token even when the complete compound is not a
    Danbooru canonical. These hints only select within one already-resolved
    current LoRA record or become candidates for a later local exact check.
    """

    text = unicodedata.normalize("NFKC", str(value or ""))
    raw_values: list[str] = []
    for match in re.finditer(r"[（(]([^（）()]{1,80})[）)]", text):
        raw = match.group(1).strip()
        if not raw or not raw.isascii() or not re.search(r"[A-Za-z]", raw):
            continue
        raw_values.append(raw)
        raw_values.extend(
            part.strip()
            for part in re.split(r"\s*[-–—/|]\s*", raw)
            if part.strip() and part.strip().isascii()
        )
    return _dedupe_text(
        item
        for item in raw_values
        if 2 <= len(_identity_key(item)) <= 80
    )[:8]


def character_lookup_hints_for_query(
    records: Sequence[LoraRecord],
    query: str,
    semantic_index: LoraSemanticIndex,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return LoRA-derived Danbooru discovery hints for one target query.

    LoRA metadata may bridge a localized name to a romanized candidate, but it
    never authorizes identity by itself.  The caller must still require an exact
    local Danbooru ``character`` result and use source works only as copyright
    constraints.
    """

    try:
        record = resolve_character_record(
            records,
            query,
            semantic_index,
            allow_equivalent_variants=True,
        )
    except CharacterSwapError:
        return (), ()
    query_keys = _query_identity_keys(query)
    requested_work_keys = _query_explicit_work_keys(query)
    entry = semantic_index.entry_for(record)
    hints: list[str] = list(
        _semantic_pair_identity_hints(
            entry,
            record,
            query_keys,
            allow_cjk_suffix=bool(requested_work_keys),
        )
    )
    for fact in _trusted_semantic_character_facts(entry, record):
        value = str(fact.value or "").strip()
        key = _identity_key(value)
        if (
            value.isascii()
            and re.search(r"[A-Za-z]", value)
            and _query_key_matches_identity(
                query_keys,
                key,
                allow_cjk_suffix=False,
            )
        ):
            hints.append(value)
    paired_keys = {_identity_key(value) for value in hints if _identity_key(value)}
    for trigger in character_identity_trigger_candidates(record):
        trigger_key = _identity_key(trigger)
        if not any(key in trigger_key for key in paired_keys if len(key) >= 3):
            continue
        root = re.split(r"[\s_(]", trigger, maxsplit=1)[0].strip()
        if root:
            hints.append(root)
    works = tuple(
        value
        for value in _work_title_candidates(record.source_work)
        if _identity_key(value)
    )
    if _entry_is_fresh(entry, record):
        assert entry is not None
        works = _dedupe_text(
            (
                *works,
                *(
                    fact.value
                    for fact in entry.effective_facts("source_works")
                    if fact.source in {"manual", "observed"}
                    or fact.confidence >= 0.9
                ),
            )
        )
    return _dedupe_text(hints)[:8], works[:4]


def _bounded_edit_distance(left: str, right: str, limit: int = 1) -> int:
    """Return a small Levenshtein distance, stopping once it exceeds ``limit``."""

    if left == right:
        return 0
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, start=1):
        current = [row]
        row_min = current[0]
        for column, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (left_char != right_char),
                )
            )
            row_min = min(row_min, current[-1])
        if row_min > limit:
            return limit + 1
        previous = current
    return previous[-1]


def _same_lora(left: LoraRecord, right: LoraRecord) -> bool:
    left_hash = str(left.sha256 or "").strip().casefold()
    right_hash = str(right.sha256 or "").strip().casefold()
    if left_hash and right_hash:
        return left_hash == right_hash
    return _canonical_key(left.name) == _canonical_key(right.name)


def resolve_character_record(
    records: Sequence[LoraRecord],
    query: str,
    semantic_index: LoraSemanticIndex,
    *,
    role_label: str = "目标",
    allow_equivalent_variants: bool = False,
) -> LoraRecord:
    """Resolve a character only from exact, provenance-aware identity evidence."""

    raw_query = unicodedata.normalize("NFKC", str(query or "")).strip().replace(
        "\\", "/"
    )
    lookup_query = (
        raw_query[5:].strip()
        if raw_query.casefold().startswith("lora:")
        else raw_query
    )
    explicit_file = is_explicit_lora_reference(raw_query)
    explicit_path = explicit_file and "/" in lookup_query
    query_keys = _query_identity_keys(
        canonical_lora_name(lookup_query) if explicit_file else lookup_query
    )
    requested_work_keys = _query_explicit_work_keys(lookup_query)
    if not query_keys:
        raise CharacterSwapError(
            f"{role_label}角色不能为空",
            code="empty_character_query",
        )
    matches: list[tuple[int, LoraRecord, str]] = []
    for record in records:
        if not _is_character_record(record, semantic_index):
            continue
        if requested_work_keys and not (
            set(requested_work_keys)
            & _trusted_source_work_keys(record, semantic_index)
        ):
            continue
        canonical = canonical_lora_name(record.name)
        basename = PurePosixPath(canonical).name
        if explicit_path:
            candidates: list[tuple[int, str]] = [(120, canonical)]
        elif explicit_file:
            candidates = [(115, basename)]
        else:
            # A bare word may be a character name as well as a basename.  Keep
            # both at the same rank so another character variant makes the
            # request ambiguous instead of silently preferring one filename.
            candidates = [(100, canonical), (100, basename)]
        candidates.extend(
            (100, value)
            for value in _trusted_identity_values(record, semantic_index)
        )
        best_score = 0
        best_value = ""
        for score, value in candidates:
            key = _identity_key(value)
            if (
                _query_key_matches_identity(
                    query_keys,
                    key,
                    allow_cjk_suffix=bool(requested_work_keys),
                )
                and score > best_score
            ):
                best_score = score
                best_value = value
        if best_score:
            matches.append((best_score, record, best_value))

    if not matches and not explicit_path and not explicit_file:
        fuzzy_matches: list[tuple[int, LoraRecord, str]] = []
        for record in records:
            if not _is_character_record(record, semantic_index):
                continue
            if requested_work_keys and not (
                set(requested_work_keys)
                & _trusted_source_work_keys(record, semantic_index)
            ):
                continue
            best_distance = 2
            best_value = ""
            for value in _trusted_identity_values(record, semantic_index):
                candidate_key = _identity_key(value)
                if len(candidate_key) < 2:
                    continue
                for query_key in query_keys:
                    distance = _bounded_edit_distance(query_key, candidate_key, 1)
                    if distance < best_distance:
                        best_distance = distance
                        best_value = value
            if best_distance == 1:
                fuzzy_matches.append((80, record, best_value))
        fuzzy_unique = {
            (str(item[1].sha256 or "").casefold(), _canonical_key(item[1].name))
            for item in fuzzy_matches
        }
        if len(fuzzy_unique) == 1:
            suggested = fuzzy_matches[0][1]
            suggested_name = (
                _split_names(suggested.character_name)[0]
                if _split_names(suggested.character_name)
                else suggested.name
            )
            raise CharacterSwapError(
                f"没有精确找到{role_label}角色“{query}”；疑似“{suggested_name}”。"
                "为避免换错角色，请使用建议名称重新发送",
                code="character_suggestion",
                details={"suggested_lora": suggested.name},
            )
        matches = fuzzy_matches

    if not matches:
        raise CharacterSwapError(
            f"未在最新 LoRA 清单中找到可唯一确认的{role_label}角色“{query}”",
            code="character_not_found",
        )
    highest = max(score for score, _record, _value in matches)
    finalists = [item for item in matches if item[0] == highest]
    unique = {
        (str(item[1].sha256 or "").casefold(), _canonical_key(item[1].name))
        for item in finalists
    }
    if len(unique) != 1:
        finalist_records = tuple(item[1] for item in finalists)
        if allow_equivalent_variants and _records_share_proven_identity(
            finalist_records,
            semantic_index,
        ):
            return sorted(
                finalist_records,
                key=lambda record: _canonical_key(record.name),
            )[0]
        names = "、".join(item[1].name for item in finalists[:5])
        raise CharacterSwapError(
            f"{role_label}角色“{query}”命中多个 LoRA：{names}；请使用完整精确文件名",
            code="ambiguous_character",
            details={"candidate_count": len(unique)},
        )
    return finalists[0][1]


def _resolve_prompt_lora(
    selection: LoraSelection,
    records: Sequence[LoraRecord],
) -> LoraRecord:
    key = _canonical_key(selection.name)
    exact = [record for record in records if _canonical_key(record.name) == key]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise CharacterSwapError(
            f"LoRA 完整名称出现冲突：{selection.name}",
            code="ambiguous_prompt_lora",
        )
    basename = _basename_key(selection.name)
    fallback = [
        record for record in records if _basename_key(record.name) == basename
    ]
    if len(fallback) == 1:
        return fallback[0]
    if not fallback:
        raise CharacterSwapError(
            f"提示词中的 LoRA 已不存在或当前不可加载：{selection.name}",
            code="prompt_lora_missing",
        )
    raise CharacterSwapError(
        f"LoRA 简称“{selection.name}”存在多个同名文件，请改用完整路径",
        code="ambiguous_prompt_lora",
    )


def _extract_prompt_loras_strict(
    prompt: str,
    *,
    max_loras: int,
) -> tuple[str, tuple[LoraSelection, ...]]:
    """Reject malformed or duplicate runtime tags before the legacy parser."""

    source = str(prompt or "")
    without_valid = _STRICT_LORA_TAG_RE.sub("", source)
    if re.search(r"<\s*lora\b", without_valid, re.IGNORECASE):
        raise CharacterSwapError(
            "提示词含有残缺或非法的 <lora:名称:权重> 标签",
            code="invalid_prompt_lora",
        )
    seen: set[str] = set()
    for match in _STRICT_LORA_TAG_RE.finditer(source):
        key = _canonical_key(match.group(1))
        if key in seen:
            raise CharacterSwapError(
                f"提示词重复指定了同一个 LoRA：{match.group(1).strip()}",
                code="duplicate_prompt_lora",
            )
        seen.add(key)
    try:
        return extract_lora_selections(source, max_loras=max_loras)
    except LoraWorkflowError as exc:
        raise CharacterSwapError(
            str(exc),
            code="invalid_prompt_lora",
        ) from exc


def _reject_obvious_multi_subject(tags: Sequence[str]) -> None:
    keys = {_prompt_term_key(tag) for tag in tags}
    if keys & _MULTI_SUBJECT_KEYS:
        raise CharacterSwapError(
            "首版语义换角只支持单角色，检测到多人或群像 Tags",
            code="multiple_subjects",
        )
    for key in keys:
        match = re.fullmatch(r"(\d+)(girls?|boys?|people|persons?)", key)
        if match and int(match.group(1)) != 1:
            raise CharacterSwapError(
                "首版语义换角只支持单角色，检测到多人数量 Tag",
                code="multiple_subjects",
            )
    if {"1girl", "1boy"}.issubset(keys):
        raise CharacterSwapError(
            "同时检测到 1girl 与 1boy，无法安全绑定唯一角色",
            code="multiple_subjects",
        )


def _semantic_alias_trigger_candidates(
    record: LoraRecord,
    semantic_index: LoraSemanticIndex,
    query: str,
) -> tuple[str, ...]:
    entry = semantic_index.entry_for(record)
    if not _entry_is_fresh(entry, record):
        return ()
    assert entry is not None
    base_query_keys = _query_identity_keys(query)
    paired_hints = _semantic_pair_identity_hints(
        entry,
        record,
        base_query_keys,
        allow_cjk_suffix=bool(_query_explicit_work_keys(query)),
    )
    query_keys = tuple(
        dict.fromkeys(
            (
                *base_query_keys,
                *(
                    key
                    for value in _explicit_ascii_identity_hints(query)
                    if (key := _identity_key(value))
                ),
                *(
                    key
                    for value in paired_hints
                    if (key := _identity_key(value))
                ),
            )
        )
    )
    work_keys = _trusted_source_work_keys(record, semantic_index)
    candidates: list[str] = []
    for field_name in ("character_names", "aliases"):
        field_candidates: list[str] = []
        for fact in entry.effective_facts(field_name):
            if not (
                fact.source in {"manual", "observed"}
                or (
                    fact.confidence >= 0.9
                    and entry.analysis_confidence >= 0.85
                )
            ):
                continue
            value = str(fact.value or "").strip()
            key = _identity_key(value)
            if (
                not value
                or not value.isascii()
                or key in work_keys
                or not is_character_identity_trigger_candidate(value)
                or not _query_key_matches_identity(
                    query_keys,
                    key,
                    allow_cjk_suffix=False,
                )
            ):
                continue
            field_candidates.append(value)
        if field_candidates:
            candidates.extend(field_candidates)
            # A trusted character_names match is stronger than broad archive
            # aliases such as ``bang_dream!_yumemita``. Do not let a work or
            # package alias create a second competing identity trigger.
            if field_name == "character_names":
                break
    return _dedupe_text(candidates)


def _target_trigger_candidates(
    record: LoraRecord,
    semantic_index: LoraSemanticIndex,
    query: str,
) -> tuple[str, ...]:
    query_keys = _query_identity_keys(query)
    entry = semantic_index.entry_for(record)
    paired_hints = _semantic_pair_identity_hints(
        entry,
        record,
        query_keys,
        allow_cjk_suffix=bool(_query_explicit_work_keys(query)),
    )
    explicit_ascii_hints = _explicit_ascii_identity_hints(query)
    if not explicit_ascii_hints and str(query or "").isascii():
        explicit_ascii_hints = (str(query).strip(),)
    query_hints = explicit_ascii_hints or paired_hints or query_keys
    selected = choose_character_identity_trigger(record, query_hints)
    if selected:
        selected_key = _prompt_term_key(selected)
        all_identity_candidates = character_identity_trigger_candidates(record)
        if len(all_identity_candidates) == 1:
            all_terms = tuple(
                term.replace(r"\(", "(").replace(r"\)", ")")
                for raw in record.trigger_words
                for term in _split_prompt_terms(raw)
            )
            return _dedupe_text(
                (
                    selected,
                    *(term for term in all_terms if _prompt_term_key(term) != selected_key),
                )
            )
        for raw in record.trigger_words:
            terms = tuple(
                term.replace(r"\(", "(").replace(r"\)", ")")
                for term in _split_prompt_terms(raw)
            )
            if any(_prompt_term_key(term) == selected_key for term in terms):
                return _dedupe_text(
                    (
                        selected,
                        *(term for term in terms if _prompt_term_key(term) != selected_key),
                    )
                )
        return (selected,)
    semantic = _semantic_alias_trigger_candidates(record, semantic_index, query)
    return semantic if len(semantic) == 1 else ()


def _trusted_lora_appearance_terms(
    record: Optional[LoraRecord],
    trigger_candidates: Sequence[str],
) -> tuple[str, ...]:
    """Extract bounded physical traits from the matched LoRA trigger group.

    The record itself has already been uniquely resolved against the current
    loadable catalog. Even then, only atomic stable appearance terms are
    accepted here: identity, outfit, pose, scene and quality triggers remain
    outside the appearance evidence package.
    """

    if record is None:
        return ()
    identity_keys = {
        _prompt_term_key(value)
        for value in character_identity_trigger_candidates(record)
        if _prompt_term_key(value)
    }
    accepted: list[str] = []
    for term in trigger_candidates:
        key = _prompt_term_key(term)
        folded = unicodedata.normalize("NFKC", str(term or "")).casefold()
        if (
            not key
            or key in identity_keys
            or not _character_feature_categories_for_term(term)
            or not _is_stable_appearance_term(folded)
            or any(marker in folded for marker in _OBVIOUS_OUTFIT_MARKERS)
        ):
            continue
        accepted.append(str(term).strip())
    return _dedupe_text(accepted)


def trusted_lora_character_appearance(
    record: LoraRecord,
    semantic_index: LoraSemanticIndex,
    query: str,
) -> tuple[str, ...]:
    """Return bounded stable appearance from one uniquely resolved character LoRA."""

    return _trusted_lora_appearance_terms(
        record,
        _target_trigger_candidates(record, semantic_index, query),
    )


def _unrequested_multi_character_variant(
    record: LoraRecord,
    semantic_index: LoraSemanticIndex,
    query: str,
    identity_trigger: str,
) -> bool:
    identities = character_identity_trigger_candidates(record)
    if len(identities) <= 1:
        return False
    work_keys = _trusted_source_work_keys(record, semantic_index)
    query_keys = _query_identity_keys(query)
    qualifiers = tuple(
        _identity_key(value)
        for value in re.findall(r"[（(]([^（）()]{1,80})[）)]", identity_trigger)
        if _identity_key(value)
    )
    variant_keys = tuple(key for key in qualifiers if key not in work_keys)
    return bool(
        variant_keys
        and not any(
            any(variant in query_key for query_key in query_keys)
            for variant in variant_keys
        )
    )


def _expectation(record: LoraRecord) -> LoraIdentityExpectation:
    return LoraIdentityExpectation(
        name=record.name,
        sha256=str(record.sha256 or "").strip().casefold(),
        source_fingerprint=semantic_source_fingerprint(record),
    )


class CharacterSwapPlanner:
    """Prepare and deterministically finalize one semantic character swap."""

    def __init__(self, semantic_index: LoraSemanticIndex) -> None:
        self._semantic_index = semantic_index

    @staticmethod
    def attach_source_tag_evidence(
        preparation: CharacterSwapPreparation,
        lookups: Sequence[Any],
    ) -> CharacterSwapPreparation:
        """Attach exact local Danbooru categories without trusting suggestions."""

        if len(lookups) != len(preparation.tags):
            raise CharacterSwapError(
                "本地 Danbooru Tag 证据数量与源提示词不一致",
                code="source_tag_evidence_mismatch",
            )
        categories: list[str] = []
        verified: list[bool] = []
        canonicals: list[str] = []
        for lookup in lookups:
            is_verified = bool(getattr(lookup, "verified", False))
            category = (
                str(getattr(lookup, "category", "") or "").strip().casefold()
                if is_verified
                else ""
            )
            if category not in {"artist", "copyright", "character", "general"}:
                category = ""
                is_verified = False
            canonical = (
                str(getattr(lookup, "canonical_tag", "") or "").strip()
                if is_verified
                else ""
            )
            categories.append(category)
            verified.append(is_verified)
            canonicals.append(canonical)
        return replace(
            preparation,
            source_tag_categories=tuple(categories),
            source_tag_verified=tuple(verified),
            source_tag_canonicals=tuple(canonicals),
        )

    @staticmethod
    def deterministic_classification(
        preparation: CharacterSwapPreparation,
    ) -> Optional[CharacterSwapClassification]:
        """Build a complete local classification when exact evidence is enough.

        This path intentionally covers only the ordinary keep-outfit swap.  It
        requires one provable source identity, a deterministic target identity,
        exact Danbooru evidence for every non-trivial term, and allows only a
        small bounded set of unindexed visual syntax such as emoticon Tags.
        Anything ambiguous returns ``None`` and continues through the LLM
        classifier instead of weakening the existing fail-closed behavior.
        """

        if preparation.request.mode != SWAP_MODE_KEEP_OUTFIT:
            return None
        if preparation.multi_subject:
            source_hint_keys = {
                _prompt_term_key(value)
                for value in preparation.source_identity_hints
                if _prompt_term_key(value)
            }
            if preparation.source_record is not None:
                source_activation = choose_character_identity_trigger(
                    preparation.source_record
                )
                if _prompt_term_key(source_activation):
                    source_hint_keys.add(_prompt_term_key(source_activation))
            selected_ids = tuple(
                index
                for index in preparation.source_selector_term_ids
                if 0 <= index < len(preparation.tags)
                and _prompt_term_key(preparation.tags[index])
                not in _GENERIC_NON_IDENTITY_KEYS
                and (
                    _is_deterministic_source_appearance_term(
                        preparation.tags[index]
                    )
                    or _danbooru_character_parts(preparation.tags[index]) is not None
                    or _prompt_term_key(preparation.tags[index]) in source_hint_keys
                )
            )
            selected_set = set(selected_ids)
            return CharacterSwapClassification(
                source_identity_ids=selected_ids,
                outfit_ids=(),
                pose_action_ids=(),
                composition_ids=(),
                scene_lighting_ids=(),
                style_quality_ids=tuple(
                    index
                    for index in range(len(preparation.tags))
                    if index not in selected_set
                ),
                uncertain_ids=(),
                target_identity_trigger_id=(
                    0 if preparation.target_trigger_words else None
                ),
                target_appearance_trigger_ids=(),
                target_default_outfit_trigger_ids=(),
                subject_count=preparation.source_subject_count,
                confidence=1.0,
            )
        feature_categories = _normalized_feature_swap_categories(
            preparation.request.feature_swap_categories
        )
        if preparation.request.feature_swap_enabled and feature_categories:
            if not (
                preparation.deterministic_target_trigger
                or preparation.request.semantic_identity_index_verified
            ):
                return None
            tag_count = len(preparation.tags)
            if not tag_count:
                return None
            has_complete_evidence = bool(
                len(preparation.source_tag_categories) == tag_count
                and len(preparation.source_tag_verified) == tag_count
                and len(preparation.source_tag_canonicals) == tag_count
            )
            exact_character_ids = tuple(
                index
                for index in range(tag_count)
                if has_complete_evidence
                and preparation.source_tag_verified[index]
                and preparation.source_tag_categories[index] == "character"
            )
            if len(exact_character_ids) > 1:
                return None
            lineage_anchors = (
                *preparation.source_identity_hints,
                *(preparation.tags[index] for index in exact_character_ids),
            )
            source_hint_keys = {
                _prompt_term_key(value)
                for value in preparation.source_identity_hints
                if _prompt_term_key(value)
            }
            selected = frozenset(feature_categories)
            source_identity_ids: list[int] = []
            outfit_ids: list[int] = []
            pose_action_ids: list[int] = []
            composition_ids: list[int] = []
            scene_lighting_ids: list[int] = []
            style_quality_ids: list[int] = []
            for index, term in enumerate(preparation.tags):
                verified = bool(
                    has_complete_evidence
                    and preparation.source_tag_verified[index]
                )
                category = (
                    preparation.source_tag_categories[index] if verified else ""
                )
                if category == "character":
                    source_identity_ids.append(index)
                    continue
                if category == "copyright":
                    if _matches_source_copyright_context(term, lineage_anchors):
                        source_identity_ids.append(index)
                    else:
                        style_quality_ids.append(index)
                    continue
                if category == "artist":
                    style_quality_ids.append(index)
                    continue

                term_key = _prompt_term_key(term)
                if term_key and term_key in source_hint_keys:
                    source_identity_ids.append(index)
                    continue
                term_features = _character_feature_categories_for_term(term)
                if term_features & selected:
                    source_identity_ids.append(index)
                    continue
                if _is_weighted_or_composite_prompt_term(term) and any(
                    marker in unicodedata.normalize(
                        "NFKC", str(term or "")
                    ).casefold()
                    for marker in _APPEARANCE_MARKERS
                ):
                    # A weighted mixed feature may hide both identity and visual
                    # content.  Keep the strict Provider path for this rare case.
                    return None
                if _is_deterministic_outfit_term(term):
                    outfit_ids.append(index)
                elif _is_deterministic_preserved_visual_term(term):
                    pose_action_ids.append(index)
                else:
                    # This is the important divergence from the old full-bucket
                    # contract: unknown scene, material and descriptive terms are
                    # preserved instead of becoming a task-wide hard failure.
                    style_quality_ids.append(index)

            target_identity_id = 0 if preparation.target_trigger_words else None
            return CharacterSwapClassification(
                source_identity_ids=tuple(source_identity_ids),
                outfit_ids=tuple(outfit_ids),
                pose_action_ids=tuple(pose_action_ids),
                composition_ids=tuple(composition_ids),
                scene_lighting_ids=tuple(scene_lighting_ids),
                style_quality_ids=tuple(style_quality_ids),
                uncertain_ids=(),
                target_identity_trigger_id=target_identity_id,
                target_appearance_trigger_ids=(),
                target_default_outfit_trigger_ids=(),
                subject_count=1,
                confidence=1.0,
            )
        tag_count = len(preparation.tags)
        if (
            not tag_count
            or len(preparation.source_tag_categories) != tag_count
            or len(preparation.source_tag_verified) != tag_count
            or len(preparation.source_tag_canonicals) != tag_count
        ):
            return None
        if not (
            preparation.deterministic_target_trigger
            or preparation.request.semantic_identity_index_verified
        ):
            return None

        exact_character_ids = tuple(
            index
            for index, (category, verified) in enumerate(
                zip(
                    preparation.source_tag_categories,
                    preparation.source_tag_verified,
                )
            )
            if verified and category == "character"
        )
        if len(exact_character_ids) > 1:
            return None
        source_authorized = bool(
            exact_character_ids
            or preparation.source_record is not None
            or preparation.removed_character_loras
        )
        if not source_authorized:
            return None

        lineage_anchors = (
            *preparation.source_identity_hints,
            *(preparation.tags[index] for index in exact_character_ids),
        )
        source_identity_ids: list[int] = []
        outfit_ids: list[int] = []
        pose_action_ids: list[int] = []
        composition_ids: list[int] = []
        scene_lighting_ids: list[int] = []
        style_quality_ids: list[int] = []

        for index, term in enumerate(preparation.tags):
            verified = preparation.source_tag_verified[index]
            category = preparation.source_tag_categories[index]
            if verified and category == "character":
                source_identity_ids.append(index)
                continue
            if verified and category == "copyright":
                if _matches_source_copyright_context(term, lineage_anchors):
                    source_identity_ids.append(index)
                else:
                    style_quality_ids.append(index)
                continue
            if verified and category == "artist":
                style_quality_ids.append(index)
                continue
            if verified and category == "general":
                if _is_deterministic_outfit_term(term):
                    outfit_ids.append(index)
                elif _is_deterministic_source_appearance_term(term):
                    source_identity_ids.append(index)
                elif _is_deterministic_preserved_visual_term(term):
                    pose_action_ids.append(index)
                else:
                    # Exact General proves that this is not a character-name or
                    # Copyright token.  The keep-outfit mode preserves it even
                    # when no narrower visual bucket can be derived locally.
                    style_quality_ids.append(index)
                continue
            if verified:
                return None

            if _is_deterministic_outfit_term(term):
                outfit_ids.append(index)
            elif _is_deterministic_preserved_visual_term(term):
                pose_action_ids.append(index)
            else:
                # Do not guess about an unindexed atomic or composite term.
                return None

        if not source_identity_ids:
            return None
        target_identity_id = 0 if preparation.target_trigger_words else None
        return CharacterSwapClassification(
            source_identity_ids=tuple(source_identity_ids),
            outfit_ids=tuple(outfit_ids),
            pose_action_ids=tuple(pose_action_ids),
            composition_ids=tuple(composition_ids),
            scene_lighting_ids=tuple(scene_lighting_ids),
            style_quality_ids=tuple(style_quality_ids),
            uncertain_ids=(),
            target_identity_trigger_id=target_identity_id,
            target_appearance_trigger_ids=(),
            target_default_outfit_trigger_ids=(),
            subject_count=1,
            confidence=1.0,
        )

    def prepare(
        self,
        request: CharacterSwapRequest,
        *,
        positive_prompt: str,
        negative_prompt: str,
        records: Sequence[LoraRecord],
        replace_source_style: bool = False,
        fallback_target_tags: Sequence[str] = (),
    ) -> CharacterSwapPreparation:
        if request.mode not in SWAP_MODES:
            raise CharacterSwapError(
                "换角模式只支持 keep-outfit 或 target-outfit",
                code="unsupported_swap_mode",
            )
        if (
            request.source_query.strip()
            and _identity_key(request.source_query) == _identity_key(request.target_query)
        ):
            raise CharacterSwapError(
                "原角色与目标角色名称相同，已停止无效换角",
                code="same_character",
            )
        target_metadata: Optional[LoraRecord] = None
        explicit_target = is_explicit_lora_reference(request.target_query)
        if request.require_target_lora and not request.use_target_lora:
            raise CharacterSwapError(
                "不能同时要求使用目标角色 LoRA 和禁用目标角色 LoRA",
                code="conflicting_target_lora_directives",
            )
        if request.require_target_lora and _is_original_character_query(
            request.target_query
        ):
            raise CharacterSwapError(
                "原创角色没有可精确绑定的现有角色 LoRA",
                code="required_target_lora_missing",
            )
        try:
            if not _is_original_character_query(request.target_query):
                target_metadata = resolve_character_record(
                    records,
                    request.target_query,
                    self._semantic_index,
                    role_label="目标",
                    allow_equivalent_variants=not request.use_target_lora,
                )
        except CharacterSwapError as exc:
            if request.require_target_lora and exc.code == "character_not_found":
                raise CharacterSwapError(
                    "用户明确要求使用目标角色 LoRA，但最新 LoRA Manager 清单中未找到可唯一确认的目标文件",
                    code="required_target_lora_missing",
                ) from exc
            # Pure semantic mode intentionally does not select a target LoRA file.
            # Multiple LoRA variants of the same requested character therefore do
            # not authorize or block the operation; the separately generated and
            # classified identity tags are the execution authority.  A typo
            # suggestion and explicit file/path requests still fail closed.
            semantic_metadata_optional = (
                not request.use_target_lora
                and not explicit_target
                and exc.code == "character_not_found"
            )
            equivalent_ambiguous_variants = False
            if (
                request.use_target_lora
                and not explicit_target
                and exc.code == "ambiguous_character"
            ):
                try:
                    resolve_character_record(
                        records,
                        request.target_query,
                        self._semantic_index,
                        role_label="目标",
                        allow_equivalent_variants=True,
                    )
                except CharacterSwapError:
                    pass
                else:
                    equivalent_ambiguous_variants = True
            optional_lora_fallback = (
                not request.require_target_lora
                and not explicit_target
                and bool(fallback_target_tags)
                and (
                    exc.code == "character_not_found"
                    or equivalent_ambiguous_variants
                )
            )
            if not (semantic_metadata_optional or optional_lora_fallback):
                raise
        metadata_target_triggers = (
            _target_trigger_candidates(
                target_metadata,
                self._semantic_index,
                request.target_query,
            )
            if target_metadata is not None
            else ()
        )
        bound_target_identity = ""
        target_activation_terms: tuple[str, ...] = ()
        if target_metadata is not None:
            semantic_entry = self._semantic_index.entry_for(target_metadata)
            requested_canonical_key = _prompt_term_key(
                request.semantic_identity_canonical_tag
            )
            if semantic_entry is not None and requested_canonical_key:
                binding_matches = tuple(
                    binding
                    for binding in semantic_entry.identity_bindings
                    if _prompt_term_key(binding.character_canonical)
                    == requested_canonical_key
                )
                if len(binding_matches) == 1:
                    binding = binding_matches[0]
                    bound_target_identity = binding.character_canonical
                    target_activation_terms = _dedupe_text(
                        (
                            *binding.activation_terms,
                            *semantic_entry.effective_values("activation_terms"),
                        )
                    )
        if (
            target_metadata is not None
            and request.use_target_lora
            and not request.require_target_lora
            and metadata_target_triggers
            and _unrequested_multi_character_variant(
                target_metadata,
                self._semantic_index,
                request.target_query,
                metadata_target_triggers[0],
            )
        ):
            if fallback_target_tags:
                target_metadata = None
                metadata_target_triggers = ()
            else:
                raise CharacterSwapError(
                    "当前唯一角色 LoRA 是多角色变体包，且其触发词包含用户未要求的变体；"
                    "将先用该归档辅助确认 Danbooru 身份，不会直接加载错误变体",
                    code="character_variant_lora_requires_semantic",
                    details={"candidate_lora": target_metadata.name},
                )
        target = target_metadata if request.use_target_lora else None
        if (
            (target is None or not request.use_target_lora)
            and request.mode == SWAP_MODE_TARGET_OUTFIT
        ):
            raise CharacterSwapError(
                "未加载目标角色 LoRA 时只支持 keep-outfit，不能自动替换为目标默认服装",
                code="semantic_target_outfit_unsupported",
            )
        source: Optional[LoraRecord] = None
        if request.source_query.strip():
            try:
                source = resolve_character_record(
                    records,
                    request.source_query,
                    self._semantic_index,
                    role_label="原",
                )
            except CharacterSwapError as exc:
                # A source character may be represented only by prompt tags.
                # Missing is tolerable; ambiguity is not.
                if exc.code != "character_not_found":
                    raise
        if (
            source is not None
            and target_metadata is not None
            and _same_lora(source, target_metadata)
        ):
            raise CharacterSwapError(
                "原角色与目标角色解析为同一个 LoRA，已停止无效换角",
                code="same_character",
            )

        clean_prompt, parsed_loras = _extract_prompt_loras_strict(
            positive_prompt,
            max_loras=max(1, len(records)),
        )
        tags = _split_prompt_terms(clean_prompt)
        if not tags:
            raise CharacterSwapError(
                "移除 LoRA 标签后没有可用于语义换角的画面 Tags",
                code="empty_swap_prompt",
            )
        if len(tags) > 240:
            raise CharacterSwapError(
                "换角提示词最多支持 240 个顶层 Tag，请先精简",
                code="too_many_tags",
            )
        try:
            selector_text = request.source_query
            if source is not None:
                source_activation = choose_character_identity_trigger(source)
                selector_text = " ".join(
                    _dedupe_text(
                        (
                            request.source_query,
                            *_trusted_identity_values(source, self._semantic_index),
                            source_activation,
                        )
                    )
                )
            subject_selection = analyze_prompt_subject_selection(
                tags,
                selector_text,
            )
            if request.source_subject_count > 1 and not subject_selection.multi_subject:
                augmented_tags = (*tags, f"{request.source_subject_count}people")
                subject_selection = analyze_prompt_subject_selection(
                    augmented_tags,
                    selector_text,
                )
                subject_selection = replace(
                    subject_selection,
                    subject_count=request.source_subject_count,
                    matched_term_ids=tuple(
                        index
                        for index in subject_selection.matched_term_ids
                        if index < len(tags)
                    ),
                    matched_terms=tuple(
                        term
                        for index, term in zip(
                            subject_selection.matched_term_ids,
                            subject_selection.matched_terms,
                        )
                        if index < len(tags)
                    ),
                    protected_term_ids=tuple(
                        index
                        for index in subject_selection.protected_term_ids
                        if index < len(tags)
                    ),
                    protected_terms=tuple(
                        term
                        for index, term in zip(
                            subject_selection.protected_term_ids,
                            subject_selection.protected_terms,
                        )
                        if index < len(tags)
                    ),
                )
        except SubjectSelectionError as exc:
            raise CharacterSwapError(
                str(exc),
                code=exc.code,
                details=exc.details,
            ) from exc
        if subject_selection.multi_subject and request.mode == SWAP_MODE_TARGET_OUTFIT:
            raise CharacterSwapError(
                "多人换角暂不支持自动套用目标默认服装；请明确写出要保留或更换的衣装",
                code="multi_subject_target_outfit_unsupported",
            )
        if request.protected_subject_terms:
            tag_keys = {
                _prompt_term_key(term): term
                for term in tags
                if _prompt_term_key(term)
            }
            observed_protected_terms = tuple(
                tag_keys[key]
                for term in request.protected_subject_terms
                if (key := _prompt_term_key(term)) in tag_keys
            )
            subject_selection = replace(
                subject_selection,
                protected_terms=_dedupe_text(
                    (*subject_selection.protected_terms, *observed_protected_terms)
                ),
            )

        resolved_pairs = tuple(
            (selection, _resolve_prompt_lora(selection, records))
            for selection in parsed_loras
        )
        character_pairs = tuple(
            pair for pair in resolved_pairs if _is_character_record(pair[1])
        )
        distinct_character_keys = {
            _canonical_key(record.name) for _selection, record in character_pairs
        }
        prompt_source: Optional[LoraRecord] = None
        if character_pairs:
            if subject_selection.multi_subject:
                matching_source_pairs = tuple(
                    pair
                    for pair in character_pairs
                    if source is not None and _same_lora(pair[1], source)
                )
                if len(matching_source_pairs) != 1:
                    raise CharacterSwapError(
                        "多人提示词含有角色 LoRA，但自然语言选择器无法把唯一一个 LoRA 绑定到待替换角色",
                        code="multi_subject_character_lora_unbound",
                        details={
                            "character_lora_count": len(distinct_character_keys),
                            "source_selector_basis": subject_selection.basis,
                        },
                    )
                prompt_source = matching_source_pairs[0][1]
            elif len(distinct_character_keys) > 1:
                raise CharacterSwapError(
                    "提示词中含有多个不同角色 LoRA，无法安全确定要替换哪一个",
                    code="multiple_character_loras",
                )
            else:
                prompt_source = character_pairs[0][1]
            if target_metadata is not None and _same_lora(
                prompt_source,
                target_metadata,
            ):
                raise CharacterSwapError(
                    "提示词已经使用目标角色 LoRA，无法确认原角色身份",
                    code="target_already_present",
                )
            if source is not None and not _same_lora(source, prompt_source):
                raise CharacterSwapError(
                    "指定的原角色与提示词中的角色 LoRA 不一致",
                    code="source_character_mismatch",
                )
            source = prompt_source

        preserved_selections: list[LoraSelection] = []
        preserved_records: list[LoraRecord] = []
        removed_character_loras: list[LoraSelection] = []
        for selection, record in resolved_pairs:
            if _is_character_record(record):
                if source is not None and _same_lora(record, source):
                    removed_character_loras.append(selection)
                    continue
                if not subject_selection.multi_subject:
                    removed_character_loras.append(selection)
                    continue
            if replace_source_style and str(record.category or "") in {
                "artist_style",
                "mixed",
            }:
                continue
            preserved_selections.append(
                LoraSelection(record.name, selection.strength)
            )
            preserved_records.append(record)

        verified_fallback_tags = _dedupe_text(fallback_target_tags)
        target_triggers = _dedupe_text(
            (
                bound_target_identity,
                *metadata_target_triggers,
                *verified_fallback_tags,
            )
        )
        deterministic_trigger = (
            bound_target_identity
            or (
                metadata_target_triggers[0]
                if target is not None and metadata_target_triggers
                else ""
            )
        )
        if not target_triggers:
            if not request.use_target_lora:
                raise CharacterSwapError(
                    "用户已禁用目标角色 LoRA，但未取得可验证的普通身份 Tags",
                    code="semantic_target_tags_missing",
                )
            raise CharacterSwapError(
                "目标角色 LoRA 没有可验证的 Civitai/Manager 触发词",
                code="missing_target_trigger",
            )
        source_hints = (
            _trusted_identity_values(source, self._semantic_index)
            if source is not None
            else _dedupe_text((request.source_query,))
        )
        target_hints = (
            _trusted_identity_values(target_metadata, self._semantic_index)
            if target_metadata is not None
            else _dedupe_text((request.target_query,))
        )
        if request.semantic_identity_canonical_tag:
            target_hints = _dedupe_text(
                (*target_hints, request.semantic_identity_canonical_tag)
            )
        appearance_count = min(
            max(0, request.semantic_appearance_count),
            max(0, len(verified_fallback_tags) - 1),
        )
        gallery_appearance_terms = (
            tuple(verified_fallback_tags[-appearance_count:])
            if appearance_count
            else ()
        )
        lora_appearance_terms = _trusted_lora_appearance_terms(
            target_metadata,
            metadata_target_triggers,
        )
        evidence_by_key: dict[str, tuple[str, list[str]]] = {}

        def add_appearance_evidence(
            terms: Sequence[str],
            source: str,
        ) -> None:
            for term in terms:
                key = _prompt_term_key(term)
                if not key:
                    continue
                if key not in evidence_by_key:
                    evidence_by_key[key] = (str(term).strip(), [source])
                    continue
                existing_term, sources = evidence_by_key[key]
                if source not in sources:
                    sources.append(source)
                evidence_by_key[key] = (existing_term, sources)

        if lora_appearance_terms:
            add_appearance_evidence(
                lora_appearance_terms,
                (
                    "civitai_trained_words"
                    if target_metadata is not None and target_metadata.from_civitai
                    else "lora_manager_triggers"
                ),
            )
        add_appearance_evidence(gallery_appearance_terms, "danbooru_gallery")
        verified_appearance_evidence = tuple(
            (term, "+".join(sources))
            for term, sources in evidence_by_key.values()
        )
        verified_appearance_terms = tuple(
            term for term, _source in verified_appearance_evidence
        )
        return CharacterSwapPreparation(
            request=request,
            tags=tags,
            negative_prompt=negative_prompt.strip(" ,"),
            target_record=target,
            target_metadata_record=target_metadata,
            source_record=source,
            preserved_loras=tuple(preserved_selections),
            preserved_lora_records=tuple(preserved_records),
            removed_character_loras=tuple(removed_character_loras),
            deterministic_target_trigger=deterministic_trigger,
            target_trigger_words=target_triggers,
            target_activation_terms=target_activation_terms,
            source_identity_hints=source_hints,
            target_identity_hints=target_hints,
            verified_target_appearance_terms=verified_appearance_terms,
            verified_target_appearance_evidence=verified_appearance_evidence,
            source_subject_count=subject_selection.subject_count,
            multi_subject=subject_selection.multi_subject,
            source_selector_term_ids=subject_selection.matched_term_ids,
            source_selector_terms=subject_selection.matched_terms,
            protected_subject_terms=subject_selection.protected_terms,
            source_selector_basis=subject_selection.basis,
            source_selector_direction_used=subject_selection.direction_used,
        )

    @staticmethod
    def classification_prompts(
        preparation: CharacterSwapPreparation,
    ) -> tuple[str, str]:
        """Return a bounded JSON-only classification request for the LLM."""

        system_prompt = """You are a conservative Anima/Danbooru tag classifier.
You do not rewrite prompts and you never invent tags. Classify every numbered source
tag into exactly one provided bucket. The task is a single-character identity swap.
Identity includes the source character name, identity token, hair color/style
(including braids, bangs, ahoge and ponytails), eye color/pupil/heterochromia,
stable face traits, brows/lashes, skin, scars/tattoos/moles, species traits,
body build/height/breast size and other character-defining appearance. Outfit
includes clothes, shoes and ordinary accessories. Preserve pose, action and
transient expression or gaze such as smile, blush, open mouth, closed eyes,
looking direction and tears, plus camera, composition, scene, lighting, style
and quality. Weighted tag
groups that mix incompatible buckets must go to uncertain_ids. Also verify the
The payload may include an exact local Danbooru category for a source tag. Treat
that evidence as authoritative: ``character`` is a source identity token,
``copyright`` is source work context, and ``artist`` is style. A ``general`` tag
is never a character-name token, but stable hair/eye/face/species/body appearance
within General still belongs to source identity; clothing and accessories belong
to outfit; exposure, anatomy visibility, expression, pose, camera, scene and effects
must be preserved. Fake ears, hairbands, bows, collars, earrings and jewelry are
accessories, not species identity. Halo color/shape, hair streaks/styles, eyebrows
and species-girl traits are stable appearance. Do not override an exact local
category with a guess. Also verify the
numbered target candidates against the exact requested target character and select
exactly one unique identity token when it is supported. Return null when no candidate
can be proven to identify that exact character. Generic subject, physical appearance,
outfit, pose, style and quality tags are invalid identities. Separately identify only
stable physical appearance candidates and default-outfit candidates; leave pose,
scene, style, quality and unknown candidates unselected. Do not invent any target
tag. An atomic source appearance tag remains source identity even when the target has
the same trait, and contradictory atomic source appearance tags remain in the same
source-identity bucket; overlap or same-bucket contradiction alone is not uncertainty.
Use uncertain_ids only for one source term that genuinely mixes incompatible buckets
or cannot be assigned without guessing. For target-outfit mode, select only candidates that explicitly describe the
target's default outfit. For a known named character, one qualified
``character_(work)`` identity candidate is sufficient and physical appearance
candidates are optional. Do not lower confidence merely because no target hair, eye,
face or body candidates were supplied. When target_identity_is_pinned is true,
candidate 0 was already exact-verified by the local Danbooru character index: set
target_identity_trigger_id to 0 and let confidence reflect source-tag classification
rather than re-judging that identity. Otherwise confidence must reflect both source
classification and the target-name-to-identity match. When target_character explicitly
says original/OC,
the exact candidate ``original character`` is the required identity anchor; select
it only when at least three other candidates are coherent stable physical traits.
Return one JSON object only. Do not include
explanations."""
        payload = {
            "source_character": preparation.request.source_query,
            "source_identity_hints": list(preparation.source_identity_hints[:24]),
            "target_character": preparation.request.target_query,
            "target_identity_hints": list(preparation.target_identity_hints[:24]),
            "target_candidate_source": (
                "lora_metadata"
                if preparation.target_metadata_record is not None
                else "bounded_semantic_generation"
            ),
            "target_lora_will_be_loaded": preparation.target_record is not None,
            "target_identity_is_pinned": bool(
                preparation.request.semantic_identity_index_verified
            ),
            "target_identity_evidence": (
                preparation.request.semantic_identity_anchor_source or "unverified"
            ),
            "mode": preparation.request.mode,
            "source_tags": [
                {
                    "id": index,
                    "tag": tag,
                    "danbooru_exact": bool(
                        index < len(preparation.source_tag_verified)
                        and preparation.source_tag_verified[index]
                    ),
                    "danbooru_category": (
                        preparation.source_tag_categories[index]
                        if index < len(preparation.source_tag_categories)
                        else ""
                    ),
                    "danbooru_canonical": (
                        preparation.source_tag_canonicals[index]
                        if index < len(preparation.source_tag_canonicals)
                        else ""
                    ),
                }
                for index, tag in enumerate(preparation.tags)
            ],
            "target_metadata_triggers": [
                {"id": index, "tag": tag}
                for index, tag in enumerate(preparation.target_trigger_words)
            ],
            "required_schema": {
                "source_identity_ids": ["integer"],
                "outfit_ids": ["integer"],
                "pose_action_ids": ["integer"],
                "composition_ids": ["integer"],
                "scene_lighting_ids": ["integer"],
                "style_quality_ids": ["integer"],
                "uncertain_ids": ["integer"],
                "target_identity_trigger_id": "integer or null",
                "target_appearance_trigger_ids": ["integer"],
                "target_default_outfit_trigger_ids": ["integer"],
                "subject_count": "integer",
                "confidence": "number 0..1",
            },
        }
        user_prompt = (
            "Classify this bounded payload and return strict JSON only:\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )
        return system_prompt, user_prompt

    @staticmethod
    def parse_classification(
        text: str,
        *,
        tag_count: int,
        target_trigger_count: int,
        deterministic_target_identity_id: Optional[int] = None,
    ) -> CharacterSwapClassification:
        payload = _strict_json_object(text)
        required = set(_CLASSIFICATION_FIELDS) | {
            "target_identity_trigger_id",
            "target_appearance_trigger_ids",
            "target_default_outfit_trigger_ids",
            "subject_count",
            "confidence",
        }
        if set(payload) != required:
            raise CharacterSwapError(
                "换角分类模型返回了错误的 JSON 字段",
                code="classification_schema_invalid",
            )

        groups: dict[str, tuple[int, ...]] = {}
        owner: dict[int, str] = {}
        for field_name in _CLASSIFICATION_FIELDS:
            ids = _integer_ids(payload.get(field_name), tag_count, field_name)
            for item_id in ids:
                if item_id in owner:
                    raise CharacterSwapError(
                        "换角分类结果含有重复 Tag ID",
                        code="classification_duplicate_id",
                    )
                owner[item_id] = field_name
            groups[field_name] = ids
        expected_ids = set(range(tag_count))
        if set(owner) != expected_ids:
            raise CharacterSwapError(
                "换角分类结果没有完整覆盖所有 Tags",
                code="classification_incomplete",
            )

        target_identity_raw = payload.get("target_identity_trigger_id")
        if target_identity_raw is None:
            target_identity_id = None
        elif isinstance(target_identity_raw, bool) or not isinstance(
            target_identity_raw, int
        ):
            raise CharacterSwapError(
                "目标身份触发词 ID 必须为整数或 null",
                code="target_trigger_invalid",
            )
        elif not 0 <= target_identity_raw < target_trigger_count:
            raise CharacterSwapError(
                "目标身份触发词 ID 越界",
                code="target_trigger_invalid",
            )
        else:
            target_identity_id = target_identity_raw
        target_appearance_ids = _integer_ids(
            payload.get("target_appearance_trigger_ids"),
            target_trigger_count,
            "target_appearance_trigger_ids",
        )
        target_outfit_ids = _integer_ids(
            payload.get("target_default_outfit_trigger_ids"),
            target_trigger_count,
            "target_default_outfit_trigger_ids",
        )
        if deterministic_target_identity_id is not None:
            if not 0 <= deterministic_target_identity_id < target_trigger_count:
                raise CharacterSwapError(
                    "可信目标身份触发词 ID 越界",
                    code="target_trigger_invalid",
                )
            target_identity_id = deterministic_target_identity_id
            target_appearance_ids = tuple(
                item
                for item in target_appearance_ids
                if item != deterministic_target_identity_id
            )
            target_outfit_ids = tuple(
                item
                for item in target_outfit_ids
                if item != deterministic_target_identity_id
            )
        if target_identity_id is not None and (
            target_identity_id in target_outfit_ids
            or target_identity_id in target_appearance_ids
        ):
            raise CharacterSwapError(
                "目标身份触发词不能同时被归为外观或默认服装",
                code="target_trigger_overlap",
            )
        if set(target_appearance_ids) & set(target_outfit_ids):
            raise CharacterSwapError(
                "目标外观触发词与默认服装触发词不能重叠",
                code="target_trigger_overlap",
            )
        subject_count = payload.get("subject_count")
        if isinstance(subject_count, bool) or not isinstance(subject_count, int):
            raise CharacterSwapError(
                "换角分类结果缺少有效 subject_count",
                code="subject_count_invalid",
            )
        try:
            confidence = float(payload.get("confidence"))
        except (TypeError, ValueError) as exc:
            raise CharacterSwapError(
                "换角分类结果缺少有效置信度",
                code="classification_confidence_invalid",
            ) from exc
        if not 0.0 <= confidence <= 1.0:
            raise CharacterSwapError(
                "换角分类置信度超出 0 到 1",
                code="classification_confidence_invalid",
            )
        return CharacterSwapClassification(
            **groups,
            target_identity_trigger_id=target_identity_id,
            target_appearance_trigger_ids=target_appearance_ids,
            target_default_outfit_trigger_ids=target_outfit_ids,
            subject_count=subject_count,
            confidence=confidence,
        )

    def finalize(
        self,
        preparation: CharacterSwapPreparation,
        classification: CharacterSwapClassification,
    ) -> CharacterSwapPlan:
        if classification.subject_count != preparation.source_subject_count:
            raise CharacterSwapError(
                "换角分类返回的角色数量与来源角色选择结果不一致",
                code="subject_count_mismatch",
                details={
                    "expected": preparation.source_subject_count,
                    "actual": classification.subject_count,
                },
            )
        semantic_original = bool(
            preparation.target_record is None
            and _is_original_character_query(preparation.request.target_query)
        )
        feature_swap_categories = _normalized_feature_swap_categories(
            preparation.request.feature_swap_categories
        )
        feature_swap_enabled = bool(
            preparation.request.feature_swap_enabled and feature_swap_categories
        )
        selected_feature_categories = frozenset(feature_swap_categories)
        if preparation.target_record is not None:
            evidence_tier = "lora_exact"
            minimum_confidence = 0.82
        elif preparation.target_metadata_record is not None:
            evidence_tier = "lora_metadata"
            minimum_confidence = 0.82
        elif semantic_original:
            evidence_tier = "original_profile"
            minimum_confidence = 0.90
        elif preparation.request.semantic_identity_index_verified:
            evidence_tier = "danbooru_exact"
            minimum_confidence = 0.82
        elif preparation.request.semantic_identity_confidence >= 0.90:
            evidence_tier = "provider_high"
            minimum_confidence = 0.82
        else:
            evidence_tier = "provider_guarded"
            minimum_confidence = 0.90
        source_identity_keys = {
            _prompt_term_key(value)
            for value in preparation.source_identity_hints
            if _prompt_term_key(value)
        }
        if preparation.source_record is not None:
            reliable_source_trigger = choose_character_identity_trigger(
                preparation.source_record
            )
            if reliable_source_trigger:
                source_identity_keys.add(
                    _prompt_term_key(reliable_source_trigger)
                )
        verified_tag_ids = {
            index
            for index, verified in enumerate(preparation.source_tag_verified)
            if verified and index < len(preparation.tags)
        }
        exact_character_ids = {
            index
            for index in verified_tag_ids
            if index < len(preparation.source_tag_categories)
            and preparation.source_tag_categories[index] == "character"
        }
        exact_copyright_ids = {
            index
            for index in verified_tag_ids
            if index < len(preparation.source_tag_categories)
            and preparation.source_tag_categories[index] == "copyright"
        }
        exact_general_ids = {
            index
            for index in verified_tag_ids
            if index < len(preparation.source_tag_categories)
            and preparation.source_tag_categories[index] == "general"
        }
        exact_artist_ids = {
            index
            for index in verified_tag_ids
            if index < len(preparation.source_tag_categories)
            and preparation.source_tag_categories[index] == "artist"
        }
        selected_subject_ids = set(preparation.source_selector_term_ids)
        classified_source_identity_ids = set(classification.source_identity_ids)
        if preparation.multi_subject:
            out_of_scope_ids = classified_source_identity_ids - selected_subject_ids
            if out_of_scope_ids:
                raise CharacterSwapError(
                    "换角分类试图移除未被自然语言选择器绑定的其他角色 Tags",
                    code="source_selector_scope_violation",
                    details={"term_ids": sorted(out_of_scope_ids)},
                )
            classified_source_identity_ids.intersection_update(selected_subject_ids)
        classified_source_canonical_ids = {
            index
            for index in classified_source_identity_ids
            if _danbooru_character_parts(preparation.tags[index]) is not None
        }
        if not preparation.multi_subject:
            classified_source_canonical_ids.update(exact_character_ids)
        else:
            classified_source_canonical_ids.update(
                exact_character_ids & selected_subject_ids
            )
        lineage_anchors = (
            *preparation.source_identity_hints,
            *(
                preparation.tags[index]
                for index in sorted(classified_source_canonical_ids)
            ),
        )
        matching_source_copyright_ids = {
            index
            for index in exact_copyright_ids
            if (not preparation.multi_subject or index in selected_subject_ids)
            if _matches_source_copyright_context(
                preparation.tags[index],
                lineage_anchors,
            )
        }
        classified_source_identity_ids.difference_update(
            exact_copyright_ids - matching_source_copyright_ids
        )
        promoted_source_canonical_ids = {
            index
            for index, term in enumerate(preparation.tags)
            if index not in classified_source_identity_ids
            and (not preparation.multi_subject or index in selected_subject_ids)
            and (
                _matches_source_character_lineage(term, lineage_anchors)
                or _matches_source_copyright_context(term, lineage_anchors)
            )
        }
        promoted_source_canonical_ids.update(
            exact_character_ids
            if not preparation.multi_subject
            else exact_character_ids & selected_subject_ids
        )
        all_deterministic_appearance_ids = {
            index
            for index, term in enumerate(preparation.tags)
            if _is_deterministic_source_appearance_term(term)
        }
        feature_source_ids = {
            index
            for index, term in enumerate(preparation.tags)
            if _character_feature_categories_for_term(term)
            & selected_feature_categories
        }
        deterministic_appearance_ids = (
            feature_source_ids
            if feature_swap_enabled
            else all_deterministic_appearance_ids
        )
        if preparation.multi_subject:
            deterministic_appearance_ids.intersection_update(selected_subject_ids)
            feature_source_ids.intersection_update(selected_subject_ids)
        deterministic_outfit_ids = {
            index
            for index, term in enumerate(preparation.tags)
            if _is_deterministic_outfit_term(term)
        }
        deterministic_preserved_visual_ids = {
            index
            for index, term in enumerate(preparation.tags)
            if _is_deterministic_preserved_visual_term(term)
        }
        reliable_classified_source_ids = {
            index
            for index in classified_source_identity_ids
            if (
                _prompt_term_key(preparation.tags[index]) in source_identity_keys
                or index in classified_source_canonical_ids
                or index in deterministic_appearance_ids
            )
        }
        uncertain_appearance_ids = {
            index
            for index in classification.uncertain_ids
            if index in deterministic_appearance_ids
        }
        has_reliable_source_profile = bool(
            feature_swap_enabled
            or preparation.removed_character_loras
            or preparation.source_record is not None
            or reliable_classified_source_ids
            or promoted_source_canonical_ids
            or len(uncertain_appearance_ids) >= 2
        )
        forced_source_appearance_ids = (
            deterministic_appearance_ids if has_reliable_source_profile else set()
        )
        promoted_uncertain_ids = set(classification.uncertain_ids) & set(
            forced_source_appearance_ids
        )
        promoted_uncertain_outfit_ids = set(classification.uncertain_ids) & set(
            deterministic_outfit_ids
        )
        promoted_uncertain_visual_ids = set(classification.uncertain_ids) & set(
            deterministic_preserved_visual_ids
        )
        promoted_uncertain_general_ids = set(classification.uncertain_ids) & (
            (exact_general_ids - set(forced_source_appearance_ids))
            | exact_artist_ids
        )
        source_identity_ids = set(classified_source_identity_ids)
        source_identity_ids.difference_update(deterministic_outfit_ids)
        source_identity_ids.difference_update(deterministic_preserved_visual_ids)
        corrected_general_source_ids = source_identity_ids & (
            (exact_general_ids - set(forced_source_appearance_ids))
            | exact_artist_ids
        )
        source_identity_ids.difference_update(corrected_general_source_ids)
        source_identity_ids.update(forced_source_appearance_ids)
        source_identity_ids.update(promoted_source_canonical_ids)
        remaining_uncertain_ids = (
            set(classification.uncertain_ids)
            - set(promoted_uncertain_ids)
            - promoted_uncertain_outfit_ids
            - promoted_uncertain_visual_ids
            - promoted_uncertain_general_ids
            - set(promoted_source_canonical_ids)
        )
        if remaining_uncertain_ids:
            raise CharacterSwapError(
                (
                    "目标角色 LoRA 已确认，但"
                    if preparation.target_record is not None
                    else ""
                )
                + "部分 Tags 仍无法可靠区分身份、衣装或画面属性；"
                "可先使用 --preview 检查分类边界",
                code="uncertain_tags",
                details={
                    "uncertain_count": len(classification.uncertain_ids),
                    "promoted_source_appearance_count": len(
                        promoted_uncertain_ids
                    ),
                    "remaining_uncertain_count": len(remaining_uncertain_ids),
                    "remaining_uncertain_ids": sorted(remaining_uncertain_ids),
                    "promoted_outfit_count": len(
                        promoted_uncertain_outfit_ids
                    ),
                    "promoted_visual_count": len(
                        promoted_uncertain_visual_ids
                    ),
                    "promoted_general_count": len(
                        promoted_uncertain_general_ids
                    ),
                    "promoted_source_canonical_count": len(
                        promoted_source_canonical_ids
                    ),
                },
            )
        if (
            not feature_swap_enabled
            and not source_identity_ids
            and not preparation.removed_character_loras
        ):
            raise CharacterSwapError(
                "没有找到可移除的原角色身份 Tag 或角色 LoRA",
                code="source_identity_missing",
            )
        classified_source_ids = set(source_identity_ids)
        for index, term in enumerate(preparation.tags):
            nested_key = _identity_key(term)
            if (
                index not in classified_source_ids
                and _is_weighted_or_composite_prompt_term(term)
                and any(
                    _contains_identity_fragment(nested_key, source_key)
                    for source_key in source_identity_keys
                )
            ):
                raise CharacterSwapError(
                    "含有可靠原角色身份词的加权或复合 Tag 未被完整移除",
                    code="source_identity_group_misclassified",
                )
        for item_id in sorted(source_identity_ids):
            term = preparation.tags[item_id]
            folded = unicodedata.normalize("NFKC", term).casefold()
            key = _prompt_term_key(term)
            if key in _GENERIC_NON_IDENTITY_KEYS or any(
                marker in folded for marker in _OBVIOUS_OUTFIT_MARKERS
            ):
                raise CharacterSwapError(
                    "分类模型把通用主体或明显衣装 Tag 误判为角色身份",
                    code="unsafe_source_identity_classification",
                    details={"term_id": item_id},
                )
            if (
                key not in source_identity_keys
                and item_id not in exact_character_ids
                and item_id not in exact_copyright_ids
                and _danbooru_character_parts(term) is None
                and not _matches_source_copyright_context(term, lineage_anchors)
                and not _is_strict_unqualified_source_canonical(term)
                and not _is_deterministic_source_appearance_term(folded)
                and not _is_composite_source_appearance_term(term)
                and item_id not in feature_source_ids
            ):
                raise CharacterSwapError(
                    "分类模型把无法证明属于身份外观的 Tag 标为角色身份",
                    code="unsafe_source_identity_classification",
                    details={
                        "term_id": item_id,
                        "danbooru_category": (
                            preparation.source_tag_categories[item_id]
                            if item_id < len(preparation.source_tag_categories)
                            else ""
                        ),
                        "danbooru_verified": item_id in verified_tag_ids,
                    },
                )
        if preparation.source_record is not None:
            source_trigger = choose_character_identity_trigger(
                preparation.source_record
            )
            source_trigger_key = _prompt_term_key(source_trigger)
            source_trigger_ids = {
                index
                for index, tag in enumerate(preparation.tags)
                if source_trigger_key
                and _prompt_term_key(tag) == source_trigger_key
            }
            if source_trigger_ids and not source_trigger_ids.issubset(
                source_identity_ids
            ):
                raise CharacterSwapError(
                    "原角色的可靠身份触发词未被分类为身份，已停止改写",
                    code="source_trigger_misclassified",
                )

        exact_lora_relaxation = bool(
            preparation.target_record is not None
            and preparation.deterministic_target_trigger
            and source_identity_ids
            and not remaining_uncertain_ids
        )
        effective_confidence_floor = (
            0.75 if exact_lora_relaxation else minimum_confidence
        )
        if classification.confidence < effective_confidence_floor:
            raise CharacterSwapError(
                "换角分类置信度 "
                f"{classification.confidence:.2f} 低于当前身份证据要求 "
                f"{effective_confidence_floor:.2f}，已停止自动改写",
                code="low_classification_confidence",
                details={
                    "actual_confidence": round(classification.confidence, 4),
                    "minimum_confidence": effective_confidence_floor,
                    "evidence_tier": evidence_tier,
                    "exact_lora_relaxation": exact_lora_relaxation,
                    "resolver_confidence": round(
                        preparation.request.semantic_identity_confidence,
                        4,
                    ),
                    "index_verified": bool(
                        preparation.request.semantic_identity_index_verified
                    ),
                    "deterministic_source_identity_count": len(
                        forced_source_appearance_ids
                    ),
                    "deterministic_outfit_count": len(
                        deterministic_outfit_ids
                    ),
                    "deterministic_preserved_visual_count": len(
                        deterministic_preserved_visual_ids
                    ),
                },
            )

        target_trigger = preparation.deterministic_target_trigger
        if preparation.target_record is None:
            trigger_id = (
                0
                if preparation.request.semantic_identity_index_verified
                else classification.target_identity_trigger_id
            )
            if trigger_id is None:
                raise CharacterSwapError(
                    "纯 Tags 换角无法确认唯一目标身份 Tag",
                    code="semantic_target_identity_unverified",
                )
            target_trigger = preparation.target_trigger_words[trigger_id]
            target_key = _prompt_term_key(target_trigger)
            if (
                not _target_identity_anchor_candidate(
                    target_trigger,
                    preparation.request.target_query,
                    locally_verified=bool(
                        preparation.request.semantic_identity_index_verified
                    ),
                )
                or target_key in _GENERIC_NON_IDENTITY_KEYS
            ):
                raise CharacterSwapError(
                    "分类模型选择了通用、外观或服装词作为目标身份触发词",
                    code="unsafe_target_trigger",
                )
            if preparation.target_metadata_record is not None:
                expected_trigger = choose_character_identity_trigger(
                    preparation.target_metadata_record
                )
                if not expected_trigger or _prompt_term_key(
                    expected_trigger
                ) != _prompt_term_key(target_trigger):
                    raise CharacterSwapError(
                        "分类模型未选择 LoRA 元数据中可证明的目标身份词",
                        code="semantic_target_identity_unverified",
                    )
            elif trigger_id != 0:
                raise CharacterSwapError(
                    "纯语义身份规划的首项身份锚点未通过分类确认",
                    code="semantic_target_identity_unverified",
                )
        elif not target_trigger:
            trigger_id = classification.target_identity_trigger_id
            if trigger_id is None:
                raise CharacterSwapError(
                    "无法从目标 LoRA 元数据中确认唯一身份触发词",
                    code="missing_target_trigger",
                )
            target_trigger = preparation.target_trigger_words[trigger_id]
            candidates = tuple(
                value
                for value in preparation.target_trigger_words
                if is_character_identity_trigger_candidate(value)
            )
            if not is_character_identity_trigger_candidate(target_trigger):
                raise CharacterSwapError(
                    "分类模型选择了通用、外观或服装词作为目标身份触发词",
                    code="unsafe_target_trigger",
                )
            if len(candidates) != 1:
                target_keys = {
                    _identity_key(value)
                    for value in preparation.target_identity_hints
                    if _identity_key(value)
                }
                trigger_key = _identity_key(target_trigger)
                if not any(
                    hint in trigger_key or trigger_key in hint
                    for hint in target_keys
                    if len(hint) >= 3
                ):
                    raise CharacterSwapError(
                        "目标 LoRA 有多个可能身份触发词，无法安全自动选择",
                        code="ambiguous_target_trigger",
                    )

        verified_appearance_keys = {
            _prompt_term_key(term)
            for term in preparation.verified_target_appearance_terms
            if _prompt_term_key(term)
        }
        verified_appearance_source_by_key = {
            _prompt_term_key(term): source
            for term, source in preparation.verified_target_appearance_evidence
            if _prompt_term_key(term) and str(source or "").strip()
        }
        evidence_appearance_ids = tuple(
            index
            for index, term in enumerate(preparation.target_trigger_words)
            if _prompt_term_key(term) in verified_appearance_keys
        )
        target_appearance_ids = tuple(
            dict.fromkeys(
                (
                    *classification.target_appearance_trigger_ids,
                    *evidence_appearance_ids,
                )
            )
        )
        target_default_outfit_ids = tuple(
            trigger_id
            for trigger_id in classification.target_default_outfit_trigger_ids
            if trigger_id not in evidence_appearance_ids
        )
        if semantic_original:
            if len(target_appearance_ids) < 3:
                raise CharacterSwapError(
                    "原创角色最终至少需要 3 项经过分类确认的稳定外貌特征",
                    code="semantic_original_appearance_unverified",
                    details={
                        "verified_appearance_count": len(target_appearance_ids),
                        "minimum_appearance_count": 3,
                    },
                )
        elif (
            preparation.target_record is None
            and classification.confidence < 0.92
            and not evidence_appearance_ids
        ):
            # A known model-native character never needs appearance tags to prove
            # identity. Optional metadata or Provider appearance below the stricter
            # appearance threshold is dropped rather than guessed.
            target_appearance_ids = ()

        for trigger_id in target_appearance_ids:
            trigger = preparation.target_trigger_words[trigger_id]
            folded = unicodedata.normalize("NFKC", trigger).casefold()
            if not _is_stable_appearance_term(folded) or any(
                marker in folded for marker in _OBVIOUS_OUTFIT_MARKERS
            ):
                raise CharacterSwapError(
                    "目标外观触发词没有可验证的外观证据",
                    code="unsafe_target_appearance_trigger",
                )
        for trigger_id in target_default_outfit_ids:
            trigger = preparation.target_trigger_words[trigger_id]
            folded = unicodedata.normalize("NFKC", trigger).casefold()
            if not any(marker in folded for marker in _OBVIOUS_OUTFIT_MARKERS):
                raise CharacterSwapError(
                    "目标默认服装触发词没有可验证的衣装证据",
                    code="unsafe_target_outfit_trigger",
                )

        removed_ids = set(source_identity_ids)
        if preparation.request.mode == SWAP_MODE_TARGET_OUTFIT:
            if not target_default_outfit_ids:
                raise CharacterSwapError(
                    "目标 LoRA 元数据不足，无法可靠应用默认服装",
                    code="target_outfit_metadata_missing",
                )
            removed_ids.update(classification.outfit_ids)
            removed_ids.update(deterministic_outfit_ids)
        protected_keys = {
            _prompt_term_key(term)
            for term in preparation.protected_subject_terms
            if _prompt_term_key(term)
        }
        protected_removed_ids = {
            index
            for index in removed_ids
            if _prompt_term_key(preparation.tags[index]) in protected_keys
        }
        if protected_removed_ids:
            raise CharacterSwapError(
                "换角计划试图移除另一个角色的受保护特征",
                code="protected_subject_term_removed",
                details={"term_ids": sorted(protected_removed_ids)},
            )
        kept_terms = tuple(
            tag for index, tag in enumerate(preparation.tags) if index not in removed_ids
        )
        removed_terms = tuple(
            tag for index, tag in enumerate(preparation.tags) if index in removed_ids
        )
        removed_term_keys = {
            _prompt_term_key(term) for term in removed_terms if _prompt_term_key(term)
        }
        target_appearance_terms = tuple(
            preparation.target_trigger_words[index]
            for index in target_appearance_ids
        )
        target_appearance_evidence_sources = tuple(
            dict.fromkeys(
                source
                for term in target_appearance_terms
                for source in verified_appearance_source_by_key.get(
                    _prompt_term_key(term),
                    "provider_classified",
                ).split("+")
                if source
            )
        )
        target_feature_categories = frozenset(
            category
            for term in target_appearance_terms
            for category in _character_feature_categories_for_term(term)
        )
        removed_feature_categories = frozenset(
            category
            for index in removed_ids
            for category in _character_feature_categories_for_term(
                preparation.tags[index]
            )
        )
        required_replacement_categories = frozenset(
            {FEATURE_HAIR_STYLE, FEATURE_HAIR_COLOR, FEATURE_EYE_COLOR}
        )
        unfilled_removed_feature_categories = (
            removed_feature_categories - target_feature_categories
        )
        missing_target_feature_categories = tuple(
            sorted(
                unfilled_removed_feature_categories
                & required_replacement_categories
            )
        )
        model_native_identity_authorized = bool(
            not semantic_original
            and (
                preparation.target_record is not None
                or preparation.request.semantic_identity_index_verified
            )
        )
        model_native_fallback_categories = (
            tuple(sorted(unfilled_removed_feature_categories))
            if model_native_identity_authorized
            else ()
        )
        if (
            preparation.request.require_target_appearance_slots
            and missing_target_feature_categories
            and not model_native_identity_authorized
        ):
            raise CharacterSwapError(
                "目标角色的核心外貌槽位缺少可信替代证据，且身份不足以启用模型原生兜底",
                code="target_appearance_slots_missing",
                details={
                    "missing_categories": list(missing_target_feature_categories),
                    "target_appearance_count": len(target_appearance_terms),
                    "appearance_source": preparation.request.semantic_appearance_source,
                    "appearance_sample_count": (
                        preparation.request.semantic_appearance_sample_count
                    ),
                    "target_lora_matched": bool(preparation.target_record),
                    "identity_index_verified": bool(
                        preparation.request.semantic_identity_index_verified
                    ),
                },
            )
        category_sources: dict[str, list[str]] = {}
        for term in target_appearance_terms:
            sources = verified_appearance_source_by_key.get(
                _prompt_term_key(term),
                "provider_classified",
            ).split("+")
            for category in _character_feature_categories_for_term(term):
                category_sources.setdefault(category, [])
                for source in sources:
                    if source and source not in category_sources[category]:
                        category_sources[category].append(source)
        target_slot_decisions: list[tuple[str, str]] = []
        for category in sorted(removed_feature_categories):
            if category in target_feature_categories:
                sources = category_sources.get(category, ["verified"])
                target_slot_decisions.append(
                    (category, "evidence:" + "+".join(sources))
                )
            elif category in model_native_fallback_categories:
                target_slot_decisions.append((category, "model_native"))
            else:
                target_slot_decisions.append((category, "unfilled"))
        reauthorized_appearance_terms = tuple(
            term
            for term in target_appearance_terms
            if (
                _is_deterministic_source_appearance_term(term)
                or bool(
                    _character_feature_categories_for_term(term)
                    & selected_feature_categories
                )
            )
            and any(
                _contains_identity_fragment(
                    _prompt_term_key(term),
                    removed_key,
                )
                or _contains_identity_fragment(
                    removed_key,
                    _prompt_term_key(term),
                )
                for removed_key in removed_term_keys
            )
        )

        if preparation.target_record is not None:
            rendered_target_trigger = _escape_lora_identity_trigger(target_trigger)
        elif preparation.request.semantic_identity_index_verified:
            rendered_target_trigger = escape_prompt_tag(target_trigger)
        else:
            rendered_target_trigger = target_trigger
        added_terms: list[str] = [rendered_target_trigger]
        canonical_tag = preparation.request.semantic_identity_canonical_tag.strip()
        if canonical_tag and _prompt_term_key(canonical_tag) != _prompt_term_key(
            target_trigger
        ):
            added_terms.append(escape_prompt_tag(canonical_tag))
        added_terms.extend(target_appearance_terms)
        if preparation.request.mode == SWAP_MODE_TARGET_OUTFIT:
            added_terms.extend(
                preparation.target_trigger_words[index]
                for index in target_default_outfit_ids
            )
        added_terms = list(_dedupe_text(added_terms))
        insert_at = _target_identity_insert_index(kept_terms)
        final_terms = _dedupe_prompt_terms(
            (
                *kept_terms[:insert_at],
                *added_terms,
                *kept_terms[insert_at:],
            )
        )
        prompt = ", ".join(final_terms).strip(" ,")
        if not prompt:
            raise CharacterSwapError(
                "语义换角后的正面提示词为空",
                code="empty_final_prompt",
            )

        target_negative_keys = {
            _prompt_term_key(value)
            for value in (
                target_trigger,
                *preparation.target_identity_hints,
                *added_terms,
                *(
                    preparation.target_trigger_words[index]
                    for index in target_appearance_ids
                ),
            )
            if _prompt_term_key(value)
        }
        negative_terms = _split_prompt_terms(preparation.negative_prompt)
        kept_negative = tuple(
            term
            for term in negative_terms
            if not any(
                _contains_identity_fragment(_prompt_term_key(term), target_key)
                for target_key in target_negative_keys
            )
        )
        negative_prompt = ", ".join(kept_negative)

        source_suppressed: list[str] = list(removed_terms)
        source_suppressed.extend(preparation.source_identity_hints)
        if preparation.source_record is not None:
            source_trigger = choose_character_identity_trigger(
                preparation.source_record
            )
            if source_trigger:
                source_suppressed.append(source_trigger)
        suppressed_terms = _dedupe_text(source_suppressed)

        if preparation.target_record is not None:
            target_selection = LoraSelection(
                preparation.target_record.name,
                preparation.request.target_lora_strength,
            )
            loras = (*preparation.preserved_loras, target_selection)
            records = (*preparation.preserved_lora_records, preparation.target_record)
        else:
            loras = preparation.preserved_loras
            records = preparation.preserved_lora_records
        self._verify_final_invariants(
            preparation,
            prompt,
            negative_prompt,
            loras,
            records,
            target_trigger,
            suppressed_terms,
            reauthorized_appearance_terms,
            target_appearance_terms,
        )
        return CharacterSwapPlan(
            prompt=prompt,
            negative_prompt=negative_prompt,
            loras=tuple(loras),
            expectations=tuple(_expectation(record) for record in records),
            target_record=preparation.target_record,
            source_record=preparation.source_record,
            target_identity_trigger=target_trigger,
            target_activation_terms=preparation.target_activation_terms,
            preserved_character_lora_names=tuple(
                record.name
                for record in preparation.preserved_lora_records
                if _is_character_record(record)
            ),
            removed_terms=removed_terms,
            kept_terms=kept_terms,
            added_terms=tuple(added_terms),
            suppressed_terms=suppressed_terms,
            suppress_default_style=any(
                str(record.category or "").casefold() in {"artist_style", "mixed"}
                for record in preparation.preserved_lora_records
            ),
            promoted_uncertain_count=len(promoted_uncertain_ids),
            promoted_uncertain_outfit_count=len(
                promoted_uncertain_outfit_ids
            ),
            promoted_uncertain_visual_count=len(
                promoted_uncertain_visual_ids
            ),
            promoted_source_canonical_count=len(
                promoted_source_canonical_ids
            ),
            corrected_general_source_count=(
                len(corrected_general_source_ids)
                + len(promoted_uncertain_general_ids)
            ),
            danbooru_verified_tag_count=len(verified_tag_ids),
            danbooru_character_tag_count=len(exact_character_ids),
            danbooru_copyright_tag_count=len(exact_copyright_ids),
            classification_confidence=classification.confidence,
            effective_confidence_floor=effective_confidence_floor,
            reauthorized_appearance_terms=reauthorized_appearance_terms,
            feature_swap_categories=(
                feature_swap_categories if feature_swap_enabled else ()
            ),
            feature_swap_removed_count=len(
                set(removed_ids) & set(feature_source_ids)
            ),
            target_appearance_terms=target_appearance_terms,
            target_appearance_source=(
                "+".join(target_appearance_evidence_sources)
                if target_appearance_evidence_sources
                else preparation.request.semantic_appearance_source
            ),
            target_appearance_evidence_sources=target_appearance_evidence_sources,
            target_feature_categories=tuple(sorted(target_feature_categories)),
            missing_target_feature_categories=missing_target_feature_categories,
            model_native_fallback_categories=model_native_fallback_categories,
            target_slot_decisions=tuple(target_slot_decisions),
            source_subject_count=preparation.source_subject_count,
            multi_subject=preparation.multi_subject,
            source_selector_terms=preparation.source_selector_terms,
            protected_subject_terms=preparation.protected_subject_terms,
            source_selector_basis=preparation.source_selector_basis,
            source_selector_direction_used=(
                preparation.source_selector_direction_used
            ),
        )

    @staticmethod
    def _verify_final_invariants(
        preparation: CharacterSwapPreparation,
        prompt: str,
        negative_prompt: str,
        loras: Sequence[LoraSelection],
        records: Sequence[LoraRecord],
        target_trigger: str,
        suppressed_terms: Sequence[str],
        reauthorized_appearance_terms: Sequence[str] = (),
        target_appearance_terms: Sequence[str] = (),
    ) -> None:
        character_keys = {
            _canonical_key(record.name) for record in records if _is_character_record(record)
        }
        preserved_character_keys = {
            _canonical_key(record.name)
            for record in preparation.preserved_lora_records
            if _is_character_record(record)
        }
        if preparation.target_record is None:
            if character_keys != preserved_character_keys:
                raise CharacterSwapError(
                    "纯语义换角改变了其他角色的 LoRA 栈",
                    code="final_character_stack_invalid",
                )
        else:
            target_key = _canonical_key(preparation.target_record.name)
            expected_character_keys = preserved_character_keys | {target_key}
            if character_keys != expected_character_keys:
                raise CharacterSwapError(
                    "最终 LoRA 栈未能只替换选中的来源角色",
                    code="final_character_stack_invalid",
                )
            if sum(_canonical_key(item.name) == target_key for item in loras) != 1:
                raise CharacterSwapError(
                    "目标角色 LoRA 必须且只能注入一次",
                    code="target_lora_count_invalid",
                )
        positive_terms = _split_prompt_terms(prompt)
        positive_keys = {_prompt_term_key(term) for term in positive_terms}
        negative_keys = {
            _prompt_term_key(term) for term in _split_prompt_terms(negative_prompt)
        }
        target_trigger_key = _prompt_term_key(target_trigger)
        target_in_negative = any(
            _contains_identity_fragment(key, target_trigger_key)
            for key in negative_keys
        )
        if target_trigger_key not in positive_keys or target_in_negative:
            raise CharacterSwapError(
                "目标身份触发词未正确进入正面提示词或仍存在于负面提示词",
                code="target_trigger_conflict",
            )
        missing_target_appearance = tuple(
            term
            for term in target_appearance_terms
            if _prompt_term_key(term) not in positive_keys
            or _prompt_term_key(term) in negative_keys
        )
        if missing_target_appearance:
            raise CharacterSwapError(
                "可信目标外貌未完整进入正面提示词或仍残留于负面提示词",
                code="target_appearance_dropped",
                details={"missing_count": len(missing_target_appearance)},
            )
        reauthorized_keys = {
            _prompt_term_key(term)
            for term in reauthorized_appearance_terms
            if _prompt_term_key(term)
        }
        leaked: set[str] = set()
        for term in suppressed_terms:
            suppressed_key = _prompt_term_key(term)
            if not suppressed_key:
                continue
            for positive_term in positive_terms:
                positive_key = _prompt_term_key(positive_term)
                if (
                    positive_key in reauthorized_keys
                    and (
                        _contains_identity_fragment(
                            positive_key,
                            suppressed_key,
                        )
                        or _contains_identity_fragment(
                            suppressed_key,
                            positive_key,
                        )
                    )
                ):
                    continue
                if _source_identity_leaks_into_term(positive_term, term):
                    leaked.add(suppressed_key)
                    break
        if leaked:
            raise CharacterSwapError(
                "最终提示词仍残留原角色身份词",
                code="source_identity_leak",
            )
        missing_protected_terms = tuple(
            term
            for term in preparation.protected_subject_terms
            if _prompt_term_key(term) not in positive_keys
        )
        if missing_protected_terms:
            raise CharacterSwapError(
                "最终提示词丢失了另一个角色的受保护特征",
                code="protected_subject_term_dropped",
                details={"missing_count": len(missing_protected_terms)},
            )


def _strict_json_object(text: str) -> Mapping[str, Any]:
    clean = str(text or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", clean, re.DOTALL | re.I)
    if fenced:
        clean = fenced.group(1)
    try:
        payload = json.loads(clean)
    except json.JSONDecodeError as exc:
        raise CharacterSwapError(
            "换角分类模型没有返回合法 JSON",
            code="classification_invalid_json",
        ) from exc
    if not isinstance(payload, Mapping):
        raise CharacterSwapError(
            "换角分类结果必须是 JSON 对象",
            code="classification_invalid_json",
        )
    return payload


def _integer_ids(value: Any, upper_bound: int, field_name: str) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise CharacterSwapError(
            f"换角分类字段 {field_name} 必须是数组",
            code="classification_schema_invalid",
        )
    result: list[int] = []
    seen: set[int] = set()
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise CharacterSwapError(
                f"换角分类字段 {field_name} 含有非整数 ID",
                code="classification_schema_invalid",
            )
        if not 0 <= item < upper_bound:
            raise CharacterSwapError(
                f"换角分类字段 {field_name} 含有越界 ID",
                code="classification_id_out_of_range",
            )
        if item in seen:
            raise CharacterSwapError(
                f"换角分类字段 {field_name} 含有重复 ID",
                code="classification_duplicate_id",
            )
        seen.add(item)
        result.append(item)
    return tuple(result)


def parse_character_swap_request(command_text: str) -> CharacterSwapRequest:
    """Parse `/换角色 A -> B [options] | full tags`."""

    head, separator, tags = str(command_text or "").partition("|")
    try:
        tokens = list(
            normalize_command_aliases(
                shlex.split(head.strip(), posix=True),
                context=CONTEXT_CHARACTER_SWAP,
            )
        )
    except ValueError as exc:
        raise CharacterSwapError(
            f"参数引号不完整：{exc}",
            code="invalid_swap_arguments",
        ) from exc
    mapping_parts: list[str] = []
    mode = SWAP_MODE_KEEP_OUTFIT
    strength = 0.65
    preset = ""
    width: Optional[int] = None
    height: Optional[int] = None
    negative_prompt = ""
    preview = False
    use_target_lora = True
    index = 0

    def require_value(option: str) -> str:
        nonlocal index
        if index + 1 >= len(tokens):
            raise CharacterSwapError(
                f"{option} 缺少参数",
                code="invalid_swap_arguments",
            )
        index += 1
        return tokens[index]

    while index < len(tokens):
        token = tokens[index]
        if token == "--mode":
            mode = require_value(token).strip().casefold()
        elif token == "--weight":
            try:
                strength = float(require_value(token))
            except ValueError as exc:
                raise CharacterSwapError(
                    "--weight 必须是数字",
                    code="invalid_swap_arguments",
                ) from exc
        elif token == "--preset":
            preset = require_value(token).strip()
        elif token == "--size":
            size = require_value(token).lower().replace("×", "x").replace("*", "x")
            match = re.fullmatch(r"(\d{2,5})x(\d{2,5})", re.sub(r"\s+", "", size))
            if not match:
                raise CharacterSwapError(
                    "--size 格式应为 宽x高",
                    code="invalid_swap_arguments",
                )
            width, height = int(match.group(1)), int(match.group(2))
        elif token == "--negative":
            negative_prompt = require_value(token).strip()
        elif token == "--preview":
            preview = True
        elif token in {"--no-character-lora", "--no-lora"}:
            use_target_lora = False
        elif token.startswith("--"):
            raise CharacterSwapError(
                f"不支持的换角选项：{token}",
                code="invalid_swap_arguments",
            )
        else:
            mapping_parts.append(token)
        index += 1

    mapping = " ".join(mapping_parts).strip()
    match = re.fullmatch(
        r"(?P<source>.*?)\s*(?:->|=>|→|替换成|替换为|换成|换为)\s*(?P<target>.+)",
        mapping,
    )
    if not match:
        raise CharacterSwapError(
            "用法：/换角色 A角色 -> B角色 [选项]，或在末尾用 | 提供完整 Tags",
            code="invalid_swap_mapping",
        )
    source_query = match.group("source").strip(" \"'，,")
    target_text, required_lora = _strip_required_character_lora_directive(
        match.group("target")
    )
    target_text, natural_no_lora = _strip_no_character_lora_suffix(target_text)
    target_query, ignored_control_directives = _normalize_target_character_query(
        target_text
    )
    target_query = target_query.strip(" \"'，,")
    if natural_no_lora:
        use_target_lora = False
    if not target_query:
        raise CharacterSwapError(
            "目标角色不能为空",
            code="empty_character_query",
        )
    if mode not in SWAP_MODES:
        raise CharacterSwapError(
            "--mode 只支持 keep-outfit 或 target-outfit",
            code="unsupported_swap_mode",
        )
    if not 0.55 <= strength <= 0.75:
        raise CharacterSwapError(
            "语义换角的角色 LoRA 权重必须在 0.55 到 0.75 之间",
            code="unsafe_target_weight",
        )
    return CharacterSwapRequest(
        source_query=source_query,
        target_query=target_query,
        tags=tags.strip() if separator else "",
        mode=mode,
        target_lora_strength=strength,
        preset=preset,
        width=width,
        height=height,
        negative_prompt=negative_prompt,
        preview=preview,
        use_target_lora=use_target_lora,
        require_target_lora=required_lora,
        ignored_control_directives=ignored_control_directives,
        feature_swap_enabled=True,
        feature_swap_categories=DEFAULT_CHARACTER_FEATURE_SWAP_CATEGORIES,
    )


def parse_text_character_change_request(
    prompt_text: str,
    *,
    preset: str = "",
    mode: str = SWAP_MODE_KEEP_OUTFIT,
    target_lora_strength: float = 0.65,
    preview: bool = False,
    use_target_lora: Optional[bool] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    negative_prompt: str = "",
    pipeline: str = "",
    prompt_expansion_mode: str = "standard",
    seed: Optional[int] = None,
    steps: Optional[int] = None,
    cfg: Optional[float] = None,
    enable_upscale: Optional[bool] = None,
    denoise: Optional[float] = None,
) -> CharacterSwapRequest:
    """Parse the explicit ``/画图 ... --llm c`` text-to-text swap form.

    This parser is intentionally unavailable to ordinary chat.  The primary
    form is ``<complete source tags>, 把角色换成<target>``.  Keeping the source
    tags before the replacement clause makes the edit boundary deterministic
    and prevents an LLM from deciding which text is transport metadata.
    """

    source = unicodedata.normalize("NFKC", str(prompt_text or "")).strip()
    source, required_lora = _strip_required_character_lora_directive(source)
    match = re.search(
        r"(?P<tags>.*?)"
        r"(?:把|将)\s*(?P<source>[^，,。；;\n]{0,120}?)\s*"
        r"(?:替换成|替换为|换成|换为)\s*(?P<target>.+)$",
        source,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        raise CharacterSwapError(
            "换角模式需要明确写成：<原 Tag 串>，把角色换成目标角色",
            code="text_character_change_mapping_missing",
        )

    tags = match.group("tags").strip(" \t，,。；;|:")
    if not tags:
        raise CharacterSwapError(
            "换角模式缺少原始 Tag 串；请先写完整 Tags，再写“把角色换成……”",
            code="text_character_change_tags_missing",
        )

    source_query = match.group("source").strip(" \t\"'，,。；;")
    source_query = re.sub(
        r"^(?:原|当前|这个|该|图中|画面中)?(?:角色|人物)$",
        "",
        source_query,
    ).strip()

    target_tail = match.group("target").strip(" \t，,。；;")
    target_tail, target_required_lora = _strip_required_character_lora_directive(
        target_tail
    )
    required_lora = required_lora or target_required_lora
    target_tail, no_lora = _strip_no_character_lora_suffix(target_tail)
    target_tail, ignored_control_directives = _normalize_target_character_query(
        target_tail
    )
    target_tail = target_tail.strip(" \t，,。；;")
    target_query = target_tail
    edit_requirement = ""
    if _is_original_character_query(target_tail):
        boundary = re.search(
            r"[，,。；;]\s*(?=(?:其余|其他|剩余|动作|姿势|表情|构图|镜头|"
            r"场景|背景|光线|风格|服装|衣服).{0,18}"
            r"(?:保持|保留|不变|改成|换成|穿|戴|增加|添加|去掉|移除))",
            target_tail,
        )
        if boundary is not None:
            target_query = target_tail[: boundary.start()].strip(" \t，,。；;")
            edit_requirement = target_tail[boundary.end() :].strip(" \t，,。；;")
    else:
        parts = re.split(r"[，,。；;\n]+", target_tail, maxsplit=1)
        target_query = parts[0].strip(" \t\"'，,。；;")
        if len(parts) == 2:
            edit_requirement = parts[1].strip(" \t，,。；;")

    target_query = target_query.strip(" \t\"'，,。；;")
    if not target_query:
        raise CharacterSwapError(
            "目标角色不能为空",
            code="empty_character_query",
        )
    normalized_mode = str(mode or SWAP_MODE_KEEP_OUTFIT).strip().casefold()
    if normalized_mode not in SWAP_MODES:
        raise CharacterSwapError(
            "换角模式只支持 keep-outfit 或 target-outfit",
            code="unsupported_swap_mode",
        )
    try:
        normalized_strength = float(target_lora_strength)
    except (TypeError, ValueError) as exc:
        raise CharacterSwapError(
            "语义换角的角色 LoRA 权重必须是数字",
            code="unsafe_target_weight",
        ) from exc
    if not 0.55 <= normalized_strength <= 0.75:
        raise CharacterSwapError(
            "语义换角的角色 LoRA 权重必须在 0.55 到 0.75 之间",
            code="unsafe_target_weight",
        )
    effective_use_target_lora = not no_lora
    if use_target_lora is False:
        effective_use_target_lora = False
    return CharacterSwapRequest(
        source_query=source_query,
        target_query=target_query,
        tags=tags,
        mode=normalized_mode,
        target_lora_strength=normalized_strength,
        preset=str(preset or "").strip(),
        width=width,
        height=height,
        negative_prompt=str(negative_prompt or "").strip(" ,"),
        preview=bool(preview),
        use_target_lora=effective_use_target_lora,
        require_target_lora=required_lora,
        edit_requirement=edit_requirement,
        pipeline=str(pipeline or "").strip(),
        prompt_expansion_mode=(
            "ultra" if str(prompt_expansion_mode).casefold() == "ultra" else "standard"
        ),
        seed=seed,
        steps=steps,
        cfg=cfg,
        enable_upscale=enable_upscale,
        denoise=denoise,
        ignored_control_directives=ignored_control_directives,
        feature_swap_enabled=True,
        feature_swap_categories=DEFAULT_CHARACTER_FEATURE_SWAP_CATEGORIES,
    )


def parse_natural_character_swap(text: str) -> Optional[CharacterSwapRequest]:
    """Recognize only explicit A-to-B natural-language replacement requests."""

    source = unicodedata.normalize("NFKC", _clean_text(text, 1000))
    source, required_lora = _strip_required_character_lora_directive(source)
    if not re.search(r"(?:替换成|替换为|换成|换为)", source):
        return None
    match = re.search(
        r"(?:把|将)?(?P<source>.+?)(?:替换成|替换为|换成|换为)(?P<target>[^，,。；;\n]+)",
        source,
    )
    if not match:
        return None
    source_query = re.sub(
        r"^(?:这张|这个|该|引用|回复)?(?:图片|图像|图|画面)?(?:里的|里|中的|内的)?",
        "",
        match.group("source").strip(),
    ).strip(" \"'，,")
    if _is_generic_source_query(source_query):
        source_query = ""
    target_text, target_no_lora = _strip_no_character_lora_suffix(match.group("target"))
    target_text, ignored_control_directives = _normalize_target_character_query(
        target_text
    )
    embedded_edit = ""
    edit_split = re.split(
        r"(?=(?:并|且|同时)(?:让|改|换|穿|戴|加|去掉|移除|保持|保留))",
        target_text,
        maxsplit=1,
    )
    if len(edit_split) == 2:
        target_text, embedded_edit = edit_split
    target_query = re.split(
        r"\s*(?:并|且|同时|衣服|服装|姿势|动作|表情|构图|背景|光线|保持|分辨率|尺寸|画布)\b",
        target_text.strip(),
        maxsplit=1,
    )[0].strip(" \"'，,")
    if not target_query:
        return None
    if any(marker in source_query for marker in _NON_CHARACTER_REQUEST_MARKERS):
        return None
    if _is_generic_source_query(target_query) or any(
        marker in target_query for marker in _NON_CHARACTER_REQUEST_MARKERS
    ):
        return None
    trailing_edit = source[match.end() :].strip(" \t，,。；;")
    edit_requirement = "，".join(
        part.strip(" \t，,。；;")
        for part in (embedded_edit, trailing_edit)
        if part.strip(" \t，,。；;")
    )
    edit_requirement = _NO_CHARACTER_LORA_RE.sub("", edit_requirement).strip(
        " \t，,。；;"
    )
    if _UNTRUSTED_CONFIDENCE_DIRECTIVE_RE.search(edit_requirement):
        edit_requirement = _UNTRUSTED_CONFIDENCE_DIRECTIVE_RE.sub(
            " ", edit_requirement
        ).strip(" \t，,。；;")
        ignored_control_directives = tuple(
            dict.fromkeys((*ignored_control_directives, "confidence_override"))
        )
    mode = (
        SWAP_MODE_TARGET_OUTFIT
        if re.search(r"(?:用|换成|采用).{0,8}(?:默认|原版|角色).{0,4}(?:衣服|服装|造型)", source)
        else SWAP_MODE_KEEP_OUTFIT
    )
    use_target_lora = not (
        target_no_lora or bool(_NO_CHARACTER_LORA_RE.search(source))
    )
    return CharacterSwapRequest(
        source_query=source_query,
        target_query=target_query,
        mode=mode,
        use_target_lora=use_target_lora,
        require_target_lora=required_lora,
        edit_requirement=edit_requirement,
        ignored_control_directives=ignored_control_directives,
        feature_swap_enabled=True,
        feature_swap_categories=DEFAULT_CHARACTER_FEATURE_SWAP_CATEGORIES,
    )


def fit_canvas_to_aspect_ratio(
    width: int,
    height: int,
    *,
    target_pixels: int = 1_000_000,
    multiple: int = 64,
    minimum: int = 256,
    maximum: int = 2048,
) -> tuple[int, int]:
    """Preserve aspect ratio near one megapixel without copying huge inputs."""

    if width <= 0 or height <= 0:
        raise CharacterSwapError("输入图片尺寸无效", code="invalid_image_size")
    ratio = width / height
    raw_width = math.sqrt(target_pixels * ratio)
    raw_height = raw_width / ratio

    def snap(value: float) -> int:
        bounded = min(maximum, max(minimum, int(round(value / multiple)) * multiple))
        return max(multiple, bounded)

    return snap(raw_width), snap(raw_height)


def response_text(response: Any) -> str:
    return _provider_response_text(response)


__all__ = [
    "CharacterSwapClassification",
    "CharacterSwapError",
    "CharacterSwapPlan",
    "CharacterSwapPlanner",
    "CharacterSwapPreparation",
    "CharacterSwapRequest",
    "SWAP_MODE_KEEP_OUTFIT",
    "SWAP_MODE_TARGET_OUTFIT",
    "character_identity_trigger_candidates",
    "character_lookup_hints_for_query",
    "fit_canvas_to_aspect_ratio",
    "is_explicit_lora_reference",
    "is_original_character_query",
    "normalize_semantic_identity_payload",
    "semantic_identity_anchor_requires_local_exact",
    "semantic_identity_lookup_hints",
    "parse_character_swap_request",
    "parse_natural_character_swap",
    "parse_text_character_change_request",
    "resolve_character_record",
    "response_text",
    "trusted_lora_character_appearance",
]
