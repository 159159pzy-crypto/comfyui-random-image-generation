"""
AstrBot Comfy Anima 插件 v2.0.0

功能描述：
- 定义插件常量与默认节点映射
- 集中维护版本及指令信息

作者: Yen
版本: 2.0.0
日期: 2026-08-04
"""

from typing import Final


PLUGIN_NAME: Final[str] = "astrbot_plugin_comfy_anima"
PLUGIN_VERSION: Final[str] = "2.0.0"

DEFAULT_WORKFLOW_FILE: Final[str] = "workflow/anima_v2_api.json"
DEFAULT_DIRECTOR_REFERENCE_FILE: Final[str] = "prompts/director_reference.txt"
DEFAULT_PROMPT_NODE_ID: Final[str] = "210"
DEFAULT_NEGATIVE_NODE_ID: Final[str] = "13"
DEFAULT_PRIMARY_SEED_NODE_ID: Final[str] = "8"
DEFAULT_SECONDARY_SEED_NODE_ID: Final[str] = "262"
DEFAULT_RESOLUTION_NODE_ID: Final[str] = "437"
DEFAULT_PRIMARY_SAMPLER_NODE_ID: Final[str] = "8"
DEFAULT_UPSCALE_OUTPUT_NODE_ID: Final[str] = "285"
DEFAULT_PREVIEW_OUTPUT_NODE_ID: Final[str] = "20"

MIN_IMAGE_SIDE: Final[int] = 256
MAX_IMAGE_SIDE: Final[int] = 4096
MAX_STEPS: Final[int] = 100
MAX_CFG: Final[float] = 30.0
MAX_SEED: Final[int] = 2**63 - 1


class MessageEmoji:
    """消息中使用的表情符号。"""

    ERROR: Final[str] = "❌"
    SUCCESS: Final[str] = "✅"
    WARNING: Final[str] = "⚠️"
    INFO: Final[str] = "ℹ️"
    DRAW: Final[str] = "🎨"
