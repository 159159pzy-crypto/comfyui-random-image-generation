from __future__ import annotations

import copy
import json
import logging
import random
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


logger = logging.getLogger(__name__)

# 内置的热门角色/系列中文译名表(插件数据本身不含中文,详见 zh_names.json)。
# 未收录的条目回退英文原名;文件缺失或损坏不影响启动。
_ZH_NAMES_PATH = Path(__file__).with_name("zh_names.json")
try:
    _zh_names = json.loads(_ZH_NAMES_PATH.read_text(encoding="utf-8"))
except (OSError, ValueError):
    _zh_names = {}
CHARACTER_ZH = {str(key).casefold(): str(value) for key, value in (_zh_names.get("characters") or {}).items()}
SERIES_ZH = {str(key).casefold(): str(value) for key, value in (_zh_names.get("series") or {}).items()}

SECTIONS = ("character", "clothing", "pose", "background", "expression")
DATA_FILES = {
    "character": "character_data.js",
    "clothing": "clothing_data.js",
    "pose": "pose_data.js",
    "background": "background_data.js",
}
EXPRESSION_DATA = [
    {"id": "gentle-smile", "name": "Gentle Smile", "name_zh": "温柔微笑", "tags": "gentle smile, relaxed expression", "categories": ["愉悦"], "traits": ["smile", "relaxed"]},
    {"id": "bright-smile", "name": "Bright Smile", "name_zh": "灿烂笑容", "tags": "bright smile, open mouth, happy", "categories": ["愉悦"], "traits": ["smile", "happy"]},
    {"id": "laughing", "name": "Laughing", "name_zh": "开心大笑", "tags": "laughing, closed eyes, open mouth", "categories": ["愉悦"], "traits": ["laughing", "closed eyes"]},
    {"id": "shy", "name": "Shy", "name_zh": "害羞", "tags": "shy, blush, looking away", "categories": ["羞涩"], "traits": ["blush", "looking away"]},
    {"id": "embarrassed", "name": "Embarrassed", "name_zh": "窘迫", "tags": "embarrassed, deep blush, nervous smile", "categories": ["羞涩"], "traits": ["blush", "nervous"]},
    {"id": "sad", "name": "Sad", "name_zh": "悲伤", "tags": "sad, downcast eyes, frown", "categories": ["悲伤"], "traits": ["frown", "downcast eyes"]},
    {"id": "crying", "name": "Crying", "name_zh": "哭泣", "tags": "crying, tears, trembling lips", "categories": ["悲伤"], "traits": ["tears", "crying"]},
    {"id": "angry", "name": "Angry", "name_zh": "生气", "tags": "angry, furrowed brow, glaring", "categories": ["愤怒"], "traits": ["glaring", "furrowed brow"]},
    {"id": "annoyed", "name": "Annoyed", "name_zh": "不耐烦", "tags": "annoyed, half-closed eyes, pout", "categories": ["愤怒"], "traits": ["pout", "half-closed eyes"]},
    {"id": "surprised", "name": "Surprised", "name_zh": "惊讶", "tags": "surprised, wide eyes, open mouth", "categories": ["惊讶"], "traits": ["wide eyes", "open mouth"]},
    {"id": "shocked", "name": "Shocked", "name_zh": "震惊", "tags": "shocked, pupils dilated, aghast", "categories": ["惊讶"], "traits": ["wide eyes", "aghast"]},
    {"id": "nervous", "name": "Nervous", "name_zh": "紧张", "tags": "nervous, sweatdrop, uneasy smile", "categories": ["紧张"], "traits": ["sweatdrop", "uneasy"]},
    {"id": "determined", "name": "Determined", "name_zh": "坚定", "tags": "determined expression, focused eyes", "categories": ["坚定"], "traits": ["focused", "serious"]},
    {"id": "smug", "name": "Smug", "name_zh": "得意", "tags": "smug, smirk, half-closed eyes", "categories": ["个性"], "traits": ["smirk", "half-closed eyes"]},
    {"id": "playful", "name": "Playful", "name_zh": "俏皮", "tags": "playful expression, wink, tongue out", "categories": ["个性"], "traits": ["wink", "tongue out"]},
    {"id": "neutral", "name": "Neutral", "name_zh": "平静", "tags": "neutral expression, relaxed face", "categories": ["平静"], "traits": ["neutral", "relaxed"]},
]
PERSON_TAGS = {
    "1girl", "2girls", "3girls", "4girls", "5girls", "multiple girls",
    "1boy", "2boys", "3boys", "4boys", "5boys", "multiple boys",
    "1other", "2others", "multiple people", "multiple persons", "solo",
    "no humans", "no human",
}

POSE_SLOT_LABELS = {
    "hand_action": "手部动作",
    "body_pose": "整体姿态",
    "interaction": "双人互动",
}
HAND_CATEGORIES = {"gestures & arms", "props & holding", "adjusting & dressing"}
BODY_CATEGORIES = {"standing & dynamic", "sitting poses", "lying & prone"}
INTERACTION_CATEGORIES = {"duo & interaction"}
HAND_TRAITS = {
    "hand", "hands", "arm", "arms", "finger", "fingers", "holding", "hold",
    "grab", "grabbing", "fist", "palm", "thumb", "thumbs", "pointing", "salute",
}
BODY_TRAITS = {
    "standing", "sitting", "lying", "kneeling", "crouching", "squatting", "walking",
    "running", "jumping", "leg", "legs", "knee", "knees", "prone", "straddle",
}
INTERACTION_TRAITS = {
    "hug", "hugging", "kiss", "kissing", "carrying", "feeding", "another", "duo",
}
ANIMA_CATEGORY_ORDER = {
    "clothing": [
        "Dress & Gown", "Casual & Daily", "Uniform & Suit",
        "Swimsuit & Lingerie", "Fantasy & Cosplay", "Revealing",
    ],
    "pose": [
        "Gestures & Arms", "Standing & Dynamic", "Sitting Poses", "Lying & Prone",
        "Adjusting & Dressing", "Props & Holding", "Duo & Interaction", "Daily & Miscellaneous",
    ],
    "background": [
        "Nature & Outdoors", "Urban & Daily", "Fantasy & Sci-Fi", "Minimalist & Abstract",
    ],
    "expression": ["愉悦", "羞涩", "悲伤", "愤怒", "惊讶", "紧张", "坚定", "个性", "平静"],
}


class CatalogError(ValueError):
    pass


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().replace("_", " ").split())


def split_prompt(value: Any) -> list[str]:
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            parts.extend(split_prompt(item))
        return parts
    return [
        part.replace("_raw_:", "", 1).strip()
        for part in str(value or "").replace("\r", ",").replace("\n", ",").split(",")
        if part.replace("_raw_:", "", 1).strip()
    ]


def _read_js_array(path: Path) -> list[dict[str, Any]]:
    content = path.read_text(encoding="utf-8")
    start = content.find("[")
    end = content.rfind("]")
    if start < 0 or end <= start:
        raise CatalogError(f"数据文件格式无效: {path.name}")
    try:
        data = json.loads(content[start : end + 1])
    except json.JSONDecodeError as error:
        raise CatalogError(f"数据文件无法解析: {path.name}") from error
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def discover_tools_dir(app_dir: str | Path, override: str | Path | None = None) -> Path | None:
    candidates = []
    if override:
        candidates.append(Path(override))
    import os

    for key in ("ANIMA_TOOLS_DIR", "COMFYUI_ANIMA_TOOLS"):
        if os.environ.get(key):
            candidates.append(Path(os.environ[key]))
    root = Path(app_dir)
    # 只保留可移植的探测规则:显式参数、环境变量、项目同级的 comfyui 目录。
    # (Windows 文件系统大小写不敏感,同级规则同样覆盖 ComfyUI 等写法。)
    candidates.append(root.parent / "comfyui" / "custom_nodes" / "Comfyui-Anima-Tools")
    for candidate in candidates:
        if (candidate / "js").is_dir() and all(
            (candidate / "js" / name).is_file() for name in DATA_FILES.values()
        ):
            return candidate
    return None


def _character_key(item: dict[str, Any]) -> str:
    return f"character:{normalize_text(item.get('name'))}||{normalize_text(item.get('copyright'))}"


def _simple_key(section: str, item: dict[str, Any]) -> str:
    item_id = str(item.get("id") or item.get("name") or "").strip()
    return f"{section}:{item_id}"


def _category_english(value: Any) -> str:
    text = str(value or "")
    if "(" in text and ")" in text:
        text = text.rsplit("(", 1)[1].split(")", 1)[0]
    return normalize_text(text)


def anima_category_value(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(")") and "(" in text:
        return text.rsplit("(", 1)[1][:-1].strip()
    return text


def anima_category_label_zh(value: Any) -> str:
    """从「中文 (English)」双语分类里取中文部分;纯文本(纯中文或纯英文)原样返回。"""
    text = str(value or "").strip()
    if text.endswith(")") and "(" in text:
        chinese = text.rsplit("(", 1)[0].strip()
        if chinese:
            return chinese
    return text


def pose_conflict_slots(item: dict[str, Any]) -> list[str]:
    categories = {_category_english(value) for value in item.get("categories") or []}
    traits = {normalize_text(value) for value in item.get("traits") or []}
    slots: list[str] = []
    if categories & HAND_CATEGORIES or ("daily & miscellaneous" in categories and traits & HAND_TRAITS):
        slots.append("hand_action")
    if categories & BODY_CATEGORIES or ("daily & miscellaneous" in categories and traits & BODY_TRAITS):
        slots.append("body_pose")
    if categories & INTERACTION_CATEGORIES or ("daily & miscellaneous" in categories and traits & INTERACTION_TRAITS):
        slots.append("interaction")
    return slots


class PromptCatalog:
    def __init__(self, app_dir: str | Path, tools_dir: str | Path | None = None):
        self.app_dir = Path(app_dir)
        self.tools_dir = discover_tools_dir(self.app_dir, tools_dir)
        self._items: dict[str, list[dict[str, Any]]] = {section: [] for section in SECTIONS}
        self._by_id: dict[str, dict[str, Any]] = {}
        self._official: dict[str, Any] = {}
        self.reload()

    @property
    def available(self) -> bool:
        return self.tools_dir is not None

    def reload(self) -> None:
        self._items = {section: [] for section in SECTIONS}
        self._by_id = {}
        self._official = {}
        self._items["expression"] = [self._to_entry("expression", item) for item in EXPRESSION_DATA]
        self._items["expression"] = [item for item in self._items["expression"] if item]
        if not self.tools_dir:
            self._by_id.update({item["id"]: item for item in self._items["expression"]})
            return
        js_dir = self.tools_dir / "js"
        official_path = js_dir / "character_official_data.json"
        if official_path.is_file():
            try:
                payload = json.loads(official_path.read_text(encoding="utf-8"))
                self._official = payload if isinstance(payload, dict) else {}
            except json.JSONDecodeError as error:
                logger.warning("角色官方数据无法解析,已忽略: %s", error)
                self._official = {}
        for section, filename in DATA_FILES.items():
            try:
                source = _read_js_array(js_dir / filename)
            except (OSError, CatalogError) as error:
                logger.warning("提示词数据文件 %s 无法读取,按空池处理: %s", filename, error)
                source = []
            entries = [self._to_entry(section, item) for item in source]
            self._items[section] = [item for item in entries if item]
            self._by_id.update({item["id"]: item for item in self._items[section]})

    def _to_entry(self, section: str, item: dict[str, Any]) -> dict[str, Any] | None:
        name = str(item.get("name_zh") or item.get("name") or "").strip()
        if not name:
            return None
        if section == "character":
            item_id = _character_key(item)
            raw_name = str(item.get("name") or "").strip()
            copyright_name = str(item.get("copyright") or "").strip()
            official_key = f"{normalize_text(raw_name)}||{normalize_text(copyright_name)}"
            official = self._official.get(official_key) or {}
            trigger = official.get("trigger") or raw_name
            tags = split_prompt(official.get("tags"))
            if not tags:
                tags = [str(item.get("gender"))] if item.get("gender") else []
            title_zh = CHARACTER_ZH.get(raw_name.casefold(), "")
            subtitle_zh = SERIES_ZH.get(copyright_name.casefold(), "")
            return {
                "id": item_id,
                "raw_id": raw_name,
                "favorite_key": raw_name,
                "section": section,
                "title": name,
                "title_zh": title_zh,
                "subtitle": copyright_name,
                "subtitle_zh": subtitle_zh,
                "copyright": copyright_name,
                "prompt": ", ".join(split_prompt(trigger) + tags),
                "trigger": split_prompt(trigger),
                "tags": tags,
                "gender": str(item.get("gender") or "unknown"),
                "hair": str(item.get("hair") or ""),
                "eye": str(item.get("eye") or ""),
                "post_count": int(item.get("post_count") or 0),
                "preview": f"https://blobs.animadex.net/Outputs/thumbs/{_quote_path(_character_raw_name(item))}.webp",
                "source": "内置角色",
                "builtin": True,
            }
        raw_id = str(item.get("id") or item.get("name") or "").strip()
        categories = [str(value) for value in item.get("categories") or [] if str(value).strip()]
        traits = [str(value) for value in item.get("traits") or [] if str(value).strip()]
        entry = {
            "id": _simple_key(section, item),
            "raw_id": raw_id,
            "favorite_key": raw_id,
            "section": section,
            "title": name,
            "subtitle": str(item.get("name") or ""),
            "prompt": ", ".join(split_prompt(item.get("tags"))),
            "tags": split_prompt(item.get("tags")),
            "preview": str(item.get("preview") or ""),
            "categories": categories,
            "traits": traits,
            "source": "内置词库",
            "builtin": True,
        }
        if section == "pose":
            entry["conflict_slots"] = pose_conflict_slots(entry)
        return entry

    def all_items(self, section: str) -> list[dict[str, Any]]:
        self._check_section(section)
        return [copy.deepcopy(item) for item in self._items[section]]

    def count(self, section: str) -> int:
        self._check_section(section)
        return len(self._items[section])

    def add_custom(self, item: dict[str, Any]) -> dict[str, Any]:
        section = str(item.get("section") or "")
        self._check_section(section)
        title = str(item.get("title") or "").strip()
        prompt = str(item.get("prompt") or "").strip()
        if not title or not prompt:
            raise CatalogError("自定义项需要名称和提示词")
        categories = [str(value) for value in item.get("categories") or [] if str(value).strip()]
        traits = [str(value) for value in item.get("traits") or [] if str(value).strip()]
        entry = {
            "id": str(item.get("id") or ""),
            "raw_id": str(item.get("id") or ""),
            "favorite_key": str(item.get("id") or ""),
            "section": section,
            "title": title,
            "subtitle": str(item.get("subtitle") or "").strip(),
            "prompt": prompt,
            "tags": split_prompt(prompt),
            "gender": str(item.get("gender") or "unknown"),
            "hair": str(item.get("hair") or ""),
            "eye": str(item.get("eye") or ""),
            "copyright": str(item.get("copyright") or ""),
            "post_count": 0,
            "categories": categories,
            "traits": traits,
            "preview": str(item.get("preview") or ""),
            "source": "自定义",
            "builtin": False,
            "group_ids": list(item.get("groupIds") or []),
        }
        if section == "pose":
            entry["conflict_slots"] = pose_conflict_slots(entry)
        return entry

    def set_custom_items(self, items: Iterable[dict[str, Any]]) -> None:
        for section in SECTIONS:
            self._items[section] = [item for item in self._items[section] if item.get("builtin")]
        for raw in items:
            item = self.add_custom(raw)
            if not item["id"].startswith("custom:"):
                raise CatalogError("自定义项 ID 无效")
            self._items[item["section"]].append(item)
        self._by_id = {item["id"]: item for section in SECTIONS for item in self._items[section]}

    def search(
        self,
        section: str,
        query: str = "",
        category: str | Iterable[str] = "",
        categories: Iterable[str] | None = None,
        traits: Iterable[str] | None = None,
        gender: str = "",
        hair: str = "",
        eye: str = "",
        series: str = "",
        custom_group: str = "",
        favorite_keys: set[str] | None = None,
        favorites_only: bool = False,
        sort: str = "",
        page: int = 1,
        limit: int = 48,
        selection: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._check_section(section)
        category_values = list(categories or ([] if not category else ([category] if isinstance(category, str) else category)))
        trait_values = [normalize_text(value) for value in (traits or []) if normalize_text(value)]
        entries = self._items[section]
        if custom_group:
            entries = [item for item in entries if not item.get("builtin") and custom_group in (item.get("group_ids") or [])]
        query_text = normalize_text(query)
        if query_text:
            entries = [item for item in entries if query_text in normalize_text(" ".join([
                item.get("title", ""), item.get("title_zh", ""),
                item.get("subtitle", ""), item.get("subtitle_zh", ""),
                item.get("prompt", ""),
                " ".join(item.get("traits") or []),
            ]))]
        if section == "character":
            if gender:
                entries = [item for item in entries if item.get("gender") == gender]
            if hair:
                entries = [item for item in entries if item.get("hair") == hair]
            if eye:
                entries = [item for item in entries if item.get("eye") == eye]
            if series:
                entries = [item for item in entries if item.get("copyright") == series]
        else:
            normalized_categories = {normalize_text(anima_category_value(value)) for value in category_values if normalize_text(anima_category_value(value))}
            if normalized_categories:
                entries = [item for item in entries if normalized_categories & {normalize_text(anima_category_value(value)) for value in item.get("categories") or []}]
            if trait_values:
                entries = [item for item in entries if set(trait_values).issubset({normalize_text(value) for value in item.get("traits") or []})]
        if favorites_only and favorite_keys is not None:
            entries = [item for item in entries if item.get("favorite_key") in favorite_keys]
        if selection is not None:
            mode = str(selection.get("mode") or "include")
            ids = {str(value) for value in selection.get("ids") or []}
            excluded_ids = {str(value) for value in selection.get("excluded_ids") or []}
            if mode == "all":
                entries = [item for item in entries if item["id"] not in excluded_ids]
            else:
                entries = [item for item in entries if item["id"] in ids]
        favorite_keys_for_sort = favorite_keys or set()
        if sort == "favorite-first":
            entries = sorted(entries, key=lambda item: (item.get("favorite_key") not in favorite_keys_for_sort, normalize_text(item.get("title"))))
        elif section == "character":
            entries = sorted(entries, key=lambda item: (-int(item.get("post_count") or 0), normalize_text(item.get("title"))))
        page = max(1, int(page or 1))
        limit = min(100, max(1, int(limit or 48)))
        total = len(entries)
        pages = max(1, (total + limit - 1) // limit)
        page = min(page, pages)
        offset = (page - 1) * limit
        return {
            "items": [self.public_item(item) for item in entries[offset : offset + limit]],
            "page": page,
            "pages": pages,
            "limit": limit,
            "total": total,
            "categories": sorted({value for item in self._items[section] for value in item.get("categories") or []}),
            "facets": self.facets(section),
        }

    def facets(self, section: str) -> dict[str, list[dict[str, Any]]]:
        self._check_section(section)
        values = self._items[section]

        def counted(field: str, *, limit: int | None = None, normalize_category: bool = False) -> list[dict[str, Any]]:
            counts: Counter[str] = Counter()
            zh_labels: dict[str, str] = {}
            for item in values:
                raw = item.get(field)
                items = raw if isinstance(raw, list) else [raw]
                for value in items:
                    if value not in (None, "", "unknown"):
                        key = anima_category_value(value) if normalize_category else str(value)
                        counts[key] += 1
                        # 双语分类「中文 (English)」的中文半边随 facet 一起返回,
                        # 由前端按「中文优先/英文优先」选择展示;筛选值仍用英文规范值。
                        if normalize_category and key not in zh_labels:
                            zh_labels[key] = anima_category_label_zh(value)
            pairs = counts.most_common(limit) if limit else sorted(counts.items(), key=lambda pair: pair[0].lower())
            return [
                {"value": value, "label": value, "label_zh": zh_labels.get(value, value), "count": count}
                for value, count in pairs
            ]

        if section == "character":
            gender = counted("gender")
            gender_labels = {"1girl": "女性", "1boy": "男性"}
            for entry in gender:
                entry["label_zh"] = gender_labels.get(entry["value"], entry["value"])
            series = counted("copyright", limit=120)
            for entry in series:
                entry["label_zh"] = SERIES_ZH.get(str(entry["value"]).casefold(), entry["value"])
            return {
                "gender": gender,
                "hair": counted("hair"),
                "eye": counted("eye"),
                "series": series,
            }
        categories = counted("categories", normalize_category=True)
        order = {value: index for index, value in enumerate(ANIMA_CATEGORY_ORDER.get(section, []))}
        categories.sort(key=lambda item: (order.get(item["value"], len(order)), item["value"].lower()))
        return {"categories": categories, "traits": counted("traits", limit=len(values) * 128)}

    def resolve_selection(self, section: str, selection: dict[str, Any], count: int, rng: random.Random) -> list[dict[str, Any]]:
        self._check_section(section)
        candidates = self._selection_candidates(section, selection)
        if count < 1:
            return []
        if not candidates:
            raise CatalogError(f"{section} 随机池为空")
        if count > len(candidates):
            raise CatalogError(f"{section} 可用条目只有 {len(candidates)} 项，无法抽取 {count} 项")
        if section == "pose":
            resolved = self._compatible_pose_sample(candidates, count, rng)
            if resolved is None:
                raise CatalogError(self._pose_conflict_error(candidates, count))
            return [copy.deepcopy(item) for item in resolved]
        return [copy.deepcopy(item) for item in rng.sample(candidates, count)]

    def prompt_parts(
        self,
        item: dict[str, Any],
        section: str,
        character_detail: str = "trigger_tags",
        strip_person_tags: bool = False,
    ) -> list[str]:
        if section == "character" and item.get("trigger"):
            values = split_prompt(item.get("trigger"))
            if character_detail == "trigger_tags":
                values.extend(split_prompt(item.get("tags")))
        else:
            values = split_prompt(item.get("tags") or item.get("prompt"))
        if strip_person_tags:
            values = [value for value in values if normalize_text(value) not in PERSON_TAGS]
        return values

    def validate_settings(self, settings: dict[str, Any]) -> None:
        for section in SECTIONS:
            if not settings.get(f"random_{section}"):
                continue
            selection = settings.get("pools", {}).get(section, {})
            count = int(settings.get(f"random_{section}_count", 1))
            available = self._selection_candidates(section, selection)
            if not available:
                raise CatalogError(f"{section} 随机池为空，请先选择候选项")
            if count > len(available):
                raise CatalogError(f"{section} 可用条目只有 {len(available)} 项，无法抽取 {count} 项")
            if section == "pose" and self._compatible_pose_sample(available, count, random.Random(0)) is None:
                raise CatalogError(self._pose_conflict_error(available, count))

    def resolve_prompt(self, settings: dict[str, Any], prompt_seed: int) -> dict[str, Any]:
        rng = random.Random(prompt_seed)
        selected: dict[str, list[dict[str, Any]]] = {section: [] for section in SECTIONS}
        composer_parts = compose_people_tags(settings.get("female_count", 0), settings.get("male_count", 0))
        seen = {normalize_text(value) for value in composer_parts}
        strip_people = bool(composer_parts)
        for section in SECTIONS:
            if not settings.get(f"random_{section}"):
                if section == "expression":
                    for part in split_prompt(settings.get("fixed_expression")):
                        key = normalize_text(part)
                        if key and key not in seen:
                            seen.add(key)
                            composer_parts.append(part)
                continue
            entries = self.resolve_selection(
                section,
                settings.get("pools", {}).get(section, {}),
                settings.get(f"random_{section}_count", 1),
                rng,
            )
            selected[section] = entries
            for entry in entries:
                for part in self.prompt_parts(
                    entry,
                    section,
                    settings.get("character_detail", "trigger_tags"),
                    strip_people and section == "character",
                ):
                    key = normalize_text(part)
                    if key and key not in seen:
                        seen.add(key)
                        composer_parts.append(part)
        for part in split_prompt(settings.get("extra_prompt")):
            key = normalize_text(part)
            if key and key not in seen:
                seen.add(key)
                composer_parts.append(part)

        full_parts: list[str] = []
        full_seen: set[str] = set()

        def append(values: Iterable[str]) -> None:
            for value in values:
                key = normalize_text(value)
                if key and key not in full_seen:
                    full_seen.add(key)
                    full_parts.append(value)

        append(split_prompt(settings.get("quality_prompt")))
        append(split_prompt(settings.get("manual_artist")))
        for section in SECTIONS:
            if not settings.get(f"random_{section}"):
                append(split_prompt(settings.get(f"fixed_{section}")))
        append(composer_parts)
        public_selected = {
            section: [self.public_item(item) for item in entries]
            for section, entries in selected.items()
        }
        return {
            "composer_prompt": f"{', '.join(composer_parts)}, " if composer_parts else "",
            "full_prompt": f"{', '.join(full_parts)}, " if full_parts else "",
            "selected": public_selected,
        }

    @staticmethod
    def public_item(item: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "id", "raw_id", "favorite_key", "section", "title", "title_zh", "subtitle", "subtitle_zh",
            "prompt", "gender",
            "hair", "eye", "copyright", "post_count", "preview", "categories", "traits",
            "conflict_slots", "source", "builtin",
            "group_ids",
        )
        result = {key: item.get(key) for key in keys}
        result["tags"] = item.get("tags") or []
        return result

    def get(self, item_id: str) -> dict[str, Any] | None:
        item = self._by_id.get(item_id)
        return copy.deepcopy(item) if item else None

    @staticmethod
    def _check_section(section: str) -> None:
        if section not in SECTIONS:
            raise CatalogError(f"不支持的随机池: {section}")

    def _selection_candidates(self, section: str, selection: dict[str, Any]) -> list[dict[str, Any]]:
        mode = selection.get("mode", "include") if isinstance(selection, dict) else "include"
        ids = {str(value) for value in selection.get("ids", [])} if isinstance(selection, dict) else set()
        excluded = {str(value) for value in selection.get("excluded_ids", [])} if isinstance(selection, dict) else set()
        if mode == "all":
            return [item for item in self._items[section] if item["id"] not in excluded]
        return [self._by_id[item_id] for item_id in ids if item_id in self._by_id and self._by_id[item_id]["section"] == section]

    @staticmethod
    def _compatible_pose_sample(candidates: list[dict[str, Any]], count: int, rng: random.Random) -> list[dict[str, Any]] | None:
        shuffled = list(candidates)
        rng.shuffle(shuffled)

        def choose(start: int, picked: list[dict[str, Any]], used: set[str]) -> list[dict[str, Any]] | None:
            if len(picked) == count:
                return picked
            if len(shuffled) - start < count - len(picked):
                return None
            for index in range(start, len(shuffled)):
                item = shuffled[index]
                slots = set(item.get("conflict_slots") or [])
                if slots & used:
                    continue
                result = choose(index + 1, [*picked, item], used | slots)
                if result is not None:
                    return result
            return None

        return choose(0, [], set())

    @staticmethod
    def _pose_conflict_error(candidates: list[dict[str, Any]], count: int) -> str:
        groups: dict[str, list[str]] = {}
        for item in candidates:
            for slot in item.get("conflict_slots") or []:
                groups.setdefault(slot, []).append(str(item.get("title") or item.get("id")))
        details = []
        for slot, names in groups.items():
            if len(names) > 1:
                details.append(f"{POSE_SLOT_LABELS.get(slot, slot)}: {'、'.join(names[:4])}")
        suffix = f"（{'；'.join(details)}）" if details else ""
        return f"姿势池无法抽取 {count} 个互不冲突的姿势，请减少抽取数量或调整候选项{suffix}"


def compose_people_tags(female_count: int, male_count: int) -> list[str]:
    parts: list[str] = []
    if female_count > 0:
        parts.append("1girl" if female_count == 1 else f"{female_count}girls")
    if male_count > 0:
        parts.append("1boy" if male_count == 1 else f"{male_count}boys")
    return parts


def _quote_path(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def _character_raw_name(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "")
    copyright_name = str(item.get("copyright") or "")
    return f"{name}, {copyright_name}" if copyright_name else name
