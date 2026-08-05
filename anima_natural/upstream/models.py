"""
AstrBot Comfy Anima 插件 v1.7.0

功能描述：
- 定义插件配置、生成参数和任务数据模型

作者: Yen
版本: 1.7.0
日期: 2026-07-26
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from .constants import (
    DEFAULT_DIRECTOR_REFERENCE_FILE,
    DEFAULT_NEGATIVE_NODE_ID,
    DEFAULT_PREVIEW_OUTPUT_NODE_ID,
    DEFAULT_PRIMARY_SAMPLER_NODE_ID,
    DEFAULT_PRIMARY_SEED_NODE_ID,
    DEFAULT_PROMPT_NODE_ID,
    DEFAULT_RESOLUTION_NODE_ID,
    DEFAULT_SECONDARY_SEED_NODE_ID,
    DEFAULT_UPSCALE_OUTPUT_NODE_ID,
    DEFAULT_WORKFLOW_FILE,
    MAX_IMAGE_SIDE,
    MAX_STEPS,
    MIN_IMAGE_SIDE,
)


def _as_bool(value: Any, default: bool) -> bool:
    """将配置值安全转换为布尔值。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _as_int(value: Any, default: int, minimum: int = 0) -> int:
    """将配置值安全转换为有下限的整数。"""
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float, minimum: float = 0.0) -> float:
    """将配置值安全转换为有下限的浮点数。"""
    try:
        return max(minimum, float(value))
    except (TypeError, ValueError):
        return default


def _as_string_list(value: Any, default: list[str]) -> list[str]:
    """将列表或逗号分隔字符串转换为字符串列表。"""
    if isinstance(value, list):
        result = [str(item).strip() for item in value if str(item).strip()]
        return result or list(default)
    if isinstance(value, str):
        result = [item.strip() for item in value.split(",") if item.strip()]
        return result or list(default)
    return list(default)


def _as_mapping_list(value: Any) -> list[dict[str, Any]]:
    """保留 template_list 中的字典项并复制，避免修改原配置对象。"""
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _default_lora_presets() -> list[dict[str, Any]]:
    """返回此 Anima 工作流随包提供的默认风格栈。"""
    return [
        {
            "__template_key": "artist_style_combo",
            "name": "风格001",
            "loras": [
                "(画质)anima-highres-aesthetic-boost=0.5",
                "(美感细节)anima-rl-v0.1=0.4",
                "anima-base-1-masterpiece-v51=0.5",
                "748cm_v2_anima=0.3",
                "hanaru_epoch24=0.31",
                "nekoya_v1_epoch21=0.3",
                "real skin.baka.v1-000010=0.65",
                "(写真背景)anima3-photo-background-v3=0.3",
            ],
            "trigger_words": "",
            "description": "默认画质、美感、画师、皮肤与写真背景风格栈",
            "enabled": True,
        }
    ]


def _as_group_levels(value: Any) -> dict[str, str]:
    """解析群号到 none/lite/full 的映射，兼容旧字典与列表格式。"""
    result: dict[str, str] = {}
    if isinstance(value, Mapping):
        items = value.items()
    else:
        raw_items = value if isinstance(value, list) else []
        parsed_items: list[tuple[str, str]] = []
        for raw_item in raw_items:
            text = str(raw_item).strip()
            separator = "=" if "=" in text else ":" if ":" in text else ""
            if not separator:
                continue
            group_id, level = text.split(separator, 1)
            parsed_items.append((group_id, level))
        items = parsed_items
    for raw_group_id, raw_level in items:
        group_id = str(raw_group_id).strip()
        level = str(raw_level).strip().lower()
        if group_id and level in {"none", "lite", "full"}:
            result[group_id] = level
    return result


@dataclass(frozen=True)
class PluginSettings:
    """插件运行配置。"""

    comfyui_url: str = "http://127.0.0.1:8188"
    api_token: str = ""
    enable_web_ui: bool = False
    web_ui_host: str = "0.0.0.0"
    web_ui_port: int = 6198
    web_ui_username: str = "admin"
    web_ui_password: str = ""
    web_ui_session_ttl: int = 43200
    workflow_file: str = DEFAULT_WORKFLOW_FILE
    upscale_workflow_file: str = "workflow/rtx_upscale_api.json"
    base_workflow_file: str = "workflow/anima_base_api.json"
    rtx_generation_workflow_file: str = "workflow/anima_rtx_api.json"
    iterative_workflow_file: str = "workflow/anima_iterative_api.json"
    inpaint_crop_workflow_file: str = "workflow/anima_inpaint_crop_api.json"
    lanpaint_workflow_file: str = "workflow/anima_lanpaint_api.json"
    default_generation_pipeline: str = "rtx"
    enable_prompt_llm: bool = True
    prompt_llm_provider_id: str = ""
    prompt_llm_timeout: int = 120
    character_swap_timeout: int = 240
    prompt_llm_temperature: float = 0.3
    prompt_llm_max_tokens: int = 1000
    prompt_llm_fallback: bool = True
    show_llm_prompt: bool = False
    director_reference_file: str = DEFAULT_DIRECTOR_REFERENCE_FILE
    director_extra_instruction: str = ""
    enable_natural_draw: bool = True
    enable_llm_pic_trigger: bool = True
    enable_chat_draw_terminal_guard: bool = True
    auto_draw_system_prompt: str = ""
    max_auto_images_per_reply: int = 1
    enable_reverse_prompt: bool = True
    enable_reverse_json_formatter: bool = True
    enable_reverse_json_repair_retry: bool = True
    reverse_prompt_provider_id: str = ""
    reverse_prompt_timeout: int = 120
    reverse_prompt_temperature: float = 0.1
    reverse_prompt_max_tokens: int = 1600
    reverse_prompt_system_prompt: str = ""
    max_input_image_size_mb: int = 20
    max_input_image_pixels: int = 40_000_000
    workflow_dir: str = "workflow"
    enable_unet_switch: bool = True
    unet_catalog_url: str = ""
    unet_catalog_timeout: int = 20
    unet_lan_only: bool = True
    unet_loader_node_id: str = "429"
    unet_model_input_name: str = "unet_name"
    unet_model_name: str = ""
    whitelist_only: bool = False
    group_whitelist: list[str] = field(default_factory=list)
    global_lock: bool = False
    enable_lock_command: bool = True
    admin_ignore_cooldown: bool = True
    admin_ignore_whitelist: bool = True
    admin_ignore_blocklist: bool = False
    default_block_level: str = "lite"
    group_block_levels: dict[str, str] = field(default_factory=dict)
    forward_sender_name: str = "ComfyUI 绘图"
    enable_lora_tool: bool = True
    lora_catalog_url: str = ""
    enable_lora_manager: bool = True
    lora_manager_url: str = ""
    lora_manager_scan_on_refresh: bool = True
    lora_manager_scan_interval: int = 60
    lora_manager_scan_timeout: int = 180
    lora_manager_page_size: int = 100
    enable_lora_download: bool = True
    lora_download_timeout: int = 3600
    lora_metadata_timeout: int = 300
    lora_download_max_concurrent: int = 1
    lora_download_allowed_hosts: list[str] = field(
        default_factory=lambda: [
            "civitai.com",
            "www.civitai.com",
            "civitai.red",
            "www.civitai.red",
        ]
    )
    lora_lan_only: bool = True
    lora_catalog_timeout: int = 15
    lora_cache_ttl: int = 300
    lora_max_results: int = 50
    enable_lora_visual_gallery: bool = True
    lora_visual_roots: list[str] = field(default_factory=list)
    lora_visual_cache_mb: int = 256
    lora_visual_warmup_workers: int = 2
    lora_visual_preview_max_mb: int = 4
    lora_visual_thumbnail_size: int = 512
    lora_alias_rules: list[str] = field(default_factory=list)
    enable_lora_hybrid_search: bool = False
    lora_embedding_provider_id: str = ""
    lora_rerank_provider_id: str = ""
    lora_embedding_top_k: int = 20
    lora_rerank_top_n: int = 8
    lora_retrieval_timeout: int = 30
    lora_tool_max_steps: int = 4
    enable_task_lora_snapshot: bool = True
    lora_snapshot_max_age: int = 300
    enable_parallel_preflight: bool = True
    provider_max_concurrent_jobs: int = 4
    enable_local_intent_router: bool = True
    structured_director_mode: str = "auto"
    enable_prompt_composer_v2: bool = True
    adaptive_negative_mode: str = "conservative"
    enable_prompt_diagnostics: bool = True
    prompt_diagnostics_include_content: bool = False
    prompt_diagnostics_capacity: int = 50
    danbooru_validation_mode: str = "report"
    danbooru_index_url: str = ""
    danbooru_index_timeout: int = 30
    danbooru_index_max_size_mb: int = 256
    danbooru_api_base_url: str = "https://danbooru.donmai.us"
    danbooru_api_proxy_url: str = ""
    danbooru_api_mode: str = "identity"
    danbooru_api_general_min_posts: int = 10
    danbooru_api_meta_min_posts: int = 10
    danbooru_api_page_size: int = 1000
    danbooru_api_request_interval_ms: int = 750
    danbooru_api_timeout: int = 60
    danbooru_api_max_records: int = 2_000_000
    danbooru_api_include_aliases: bool = True
    danbooru_auto_update_enabled: bool = False
    danbooru_auto_update_interval_hours: int = 168
    enable_prompt_asset_library: bool = True
    prompt_asset_remote_import_enabled: bool = False
    prompt_asset_max_download_mb: int = 16
    enable_prompt_lab: bool = True
    prompt_lab_batch_capacity: int = 32
    prompt_lab_ttl_seconds: int = 1800
    enable_layered_lora_retrieval: bool = True
    lora_loader_node_id: str = "462"
    dynamic_lora_mode: str = "append"
    max_dynamic_loras: int = 3
    max_preset_loras: int = 12
    max_total_dynamic_loras: int = 12
    default_style_preset: str = "风格001"
    auto_reload_after_style_save: bool = True
    lora_presets: list[dict[str, Any]] = field(default_factory=_default_lora_presets)
    strict_lora_validation: bool = True
    prompt_node_id: str = DEFAULT_PROMPT_NODE_ID
    negative_node_id: str = DEFAULT_NEGATIVE_NODE_ID
    primary_seed_node_id: str = DEFAULT_PRIMARY_SEED_NODE_ID
    secondary_seed_node_id: str = DEFAULT_SECONDARY_SEED_NODE_ID
    resolution_node_id: str = DEFAULT_RESOLUTION_NODE_ID
    default_width: int = 832
    default_height: int = 1216
    sampler_node_ids: list[str] = field(
        default_factory=lambda: [DEFAULT_PRIMARY_SAMPLER_NODE_ID]
    )
    sampler_steps_override: int = 0
    output_node_ids: list[str] = field(
        default_factory=lambda: [
            DEFAULT_UPSCALE_OUTPUT_NODE_ID,
            DEFAULT_PREVIEW_OUTPUT_NODE_ID,
        ]
    )
    upscale_output_node_id: str = DEFAULT_UPSCALE_OUTPUT_NODE_ID
    enable_upscale: bool = True
    rtx_scale: float = 2.0
    rtx_quality: str = "ULTRA"
    iterative_scale: float = 1.5
    iterative_steps: int = 3
    iterative_denoise: float = 0.35
    enable_inpaint: bool = True
    send_generation_notice: bool = True
    allow_global_interrupt: bool = False
    max_concurrent_jobs: int = 1
    user_cooldown: int = 30
    request_timeout: int = 30
    generation_timeout: int = 1200
    poll_interval: float = 2.0
    max_prompt_length: int = 2000
    max_image_size_mb: int = 50

    @classmethod
    def from_mapping(cls, config: Optional[Mapping[str, Any]]) -> "PluginSettings":
        """从 AstrBot 配置对象创建配置实例。

        Args:
            config: 字典兼容的 AstrBot 插件配置。

        Returns:
            经过类型清洗的插件配置。
        """
        data = config or {}
        return cls(
            comfyui_url=str(data.get("comfyui_url", cls.comfyui_url)).strip(),
            api_token=str(data.get("api_token", "")).strip(),
            enable_web_ui=_as_bool(data.get("enable_web_ui"), False),
            web_ui_host=(str(data.get("web_ui_host", "0.0.0.0")).strip() or "0.0.0.0"),
            web_ui_port=min(
                65535,
                _as_int(data.get("web_ui_port"), 6198, 1024),
            ),
            web_ui_username=(
                str(data.get("web_ui_username", "admin")).strip() or "admin"
            ),
            web_ui_password=str(data.get("web_ui_password", "")),
            web_ui_session_ttl=min(
                86400,
                _as_int(data.get("web_ui_session_ttl"), 43200, 300),
            ),
            workflow_file=str(data.get("workflow_file", DEFAULT_WORKFLOW_FILE)).strip(),
            upscale_workflow_file=str(
                data.get("upscale_workflow_file", "workflow/rtx_upscale_api.json")
            ).strip()
            or "workflow/rtx_upscale_api.json",
            base_workflow_file=str(
                data.get("base_workflow_file", "workflow/anima_base_api.json")
            ).strip()
            or "workflow/anima_base_api.json",
            rtx_generation_workflow_file=str(
                data.get(
                    "rtx_generation_workflow_file",
                    "workflow/anima_rtx_api.json",
                )
            ).strip()
            or "workflow/anima_rtx_api.json",
            iterative_workflow_file=str(
                data.get(
                    "iterative_workflow_file",
                    "workflow/anima_iterative_api.json",
                )
            ).strip()
            or "workflow/anima_iterative_api.json",
            inpaint_crop_workflow_file=str(
                data.get(
                    "inpaint_crop_workflow_file",
                    "workflow/anima_inpaint_crop_api.json",
                )
            ).strip()
            or "workflow/anima_inpaint_crop_api.json",
            lanpaint_workflow_file=str(
                data.get("lanpaint_workflow_file", "workflow/anima_lanpaint_api.json")
            ).strip()
            or "workflow/anima_lanpaint_api.json",
            default_generation_pipeline=(
                str(data.get("default_generation_pipeline")).strip().lower()
                if str(data.get("default_generation_pipeline", "")).strip().lower()
                in {"base", "rtx", "iterative"}
                else (
                    "rtx" if _as_bool(data.get("enable_upscale"), True) else "base"
                )
            ),
            enable_prompt_llm=_as_bool(data.get("enable_prompt_llm"), True),
            prompt_llm_provider_id=str(data.get("prompt_llm_provider_id", "")).strip(),
            prompt_llm_timeout=_as_int(data.get("prompt_llm_timeout"), 120, 10),
            character_swap_timeout=min(
                600,
                _as_int(data.get("character_swap_timeout"), 240, 30),
            ),
            prompt_llm_temperature=_as_float(data.get("prompt_llm_temperature"), 0.3),
            prompt_llm_max_tokens=_as_int(data.get("prompt_llm_max_tokens"), 1000, 128),
            prompt_llm_fallback=_as_bool(data.get("prompt_llm_fallback"), True),
            show_llm_prompt=_as_bool(data.get("show_llm_prompt"), False),
            director_reference_file=str(
                data.get("director_reference_file", DEFAULT_DIRECTOR_REFERENCE_FILE)
            ).strip(),
            director_extra_instruction=str(
                data.get("director_extra_instruction", "")
            ).strip(),
            enable_natural_draw=_as_bool(data.get("enable_natural_draw"), True),
            enable_llm_pic_trigger=_as_bool(data.get("enable_llm_pic_trigger"), True),
            enable_chat_draw_terminal_guard=_as_bool(
                data.get("enable_chat_draw_terminal_guard"),
                True,
            ),
            auto_draw_system_prompt=str(
                data.get("auto_draw_system_prompt", "")
            ).strip(),
            max_auto_images_per_reply=_as_int(
                data.get("max_auto_images_per_reply"), 1, 1
            ),
            enable_reverse_prompt=_as_bool(data.get("enable_reverse_prompt"), True),
            enable_reverse_json_formatter=_as_bool(
                data.get("enable_reverse_json_formatter"),
                True,
            ),
            enable_reverse_json_repair_retry=_as_bool(
                data.get("enable_reverse_json_repair_retry"),
                True,
            ),
            reverse_prompt_provider_id=str(
                data.get("reverse_prompt_provider_id", "")
            ).strip(),
            reverse_prompt_timeout=min(
                300,
                _as_int(data.get("reverse_prompt_timeout"), 120, 10),
            ),
            reverse_prompt_temperature=min(
                2.0,
                max(0.0, _as_float(data.get("reverse_prompt_temperature"), 0.1)),
            ),
            reverse_prompt_max_tokens=min(
                8000,
                _as_int(data.get("reverse_prompt_max_tokens"), 1600, 256),
            ),
            reverse_prompt_system_prompt=str(
                data.get("reverse_prompt_system_prompt", "")
            ).strip(),
            max_input_image_size_mb=min(
                100,
                _as_int(data.get("max_input_image_size_mb"), 20, 1),
            ),
            max_input_image_pixels=min(
                100_000_000,
                _as_int(data.get("max_input_image_pixels"), 40_000_000, 1_000_000),
            ),
            workflow_dir=str(data.get("workflow_dir", "workflow")).strip()
            or "workflow",
            enable_unet_switch=_as_bool(data.get("enable_unet_switch"), True),
            unet_catalog_url=str(data.get("unet_catalog_url", "")).strip(),
            unet_catalog_timeout=_as_int(data.get("unet_catalog_timeout"), 20, 1),
            unet_lan_only=_as_bool(data.get("unet_lan_only"), True),
            unet_loader_node_id=str(data.get("unet_loader_node_id", "429")).strip()
            or "429",
            unet_model_input_name=str(
                data.get("unet_model_input_name", "unet_name")
            ).strip()
            or "unet_name",
            unet_model_name=str(data.get("unet_model_name", "")).strip(),
            whitelist_only=_as_bool(data.get("whitelist_only"), False),
            group_whitelist=(
                _as_string_list(data.get("group_whitelist"), [])
                if data.get("group_whitelist")
                else []
            ),
            global_lock=_as_bool(data.get("global_lock"), False),
            enable_lock_command=_as_bool(data.get("enable_lock_command"), True),
            admin_ignore_cooldown=_as_bool(data.get("admin_ignore_cooldown"), True),
            admin_ignore_whitelist=_as_bool(data.get("admin_ignore_whitelist"), True),
            admin_ignore_blocklist=_as_bool(data.get("admin_ignore_blocklist"), False),
            default_block_level=(
                str(data.get("default_block_level", "lite")).strip().lower()
                if str(data.get("default_block_level", "lite")).strip().lower()
                in {"none", "lite", "full"}
                else "lite"
            ),
            group_block_levels=_as_group_levels(data.get("group_block_levels", [])),
            forward_sender_name=(
                str(data.get("forward_sender_name", "ComfyUI 绘图")).strip()
                or "ComfyUI 绘图"
            ),
            enable_lora_tool=_as_bool(data.get("enable_lora_tool"), True),
            lora_catalog_url=str(data.get("lora_catalog_url", "")).strip(),
            enable_lora_manager=_as_bool(data.get("enable_lora_manager"), True),
            lora_manager_url=str(data.get("lora_manager_url", "")).strip(),
            lora_manager_scan_on_refresh=_as_bool(
                data.get("lora_manager_scan_on_refresh"), True
            ),
            lora_manager_scan_interval=_as_int(
                data.get("lora_manager_scan_interval"), 60, 0
            ),
            lora_manager_scan_timeout=_as_int(
                data.get("lora_manager_scan_timeout"), 180, 10
            ),
            lora_manager_page_size=_as_int(data.get("lora_manager_page_size"), 100, 10),
            enable_lora_download=_as_bool(data.get("enable_lora_download"), True),
            lora_download_timeout=_as_int(data.get("lora_download_timeout"), 3600, 60),
            lora_metadata_timeout=_as_int(data.get("lora_metadata_timeout"), 300, 10),
            lora_download_max_concurrent=_as_int(
                data.get("lora_download_max_concurrent"), 1, 1
            ),
            lora_download_allowed_hosts=_as_string_list(
                data.get("lora_download_allowed_hosts"),
                [
                    "civitai.com",
                    "www.civitai.com",
                    "civitai.red",
                    "www.civitai.red",
                ],
            ),
            lora_lan_only=_as_bool(data.get("lora_lan_only"), True),
            lora_catalog_timeout=_as_int(data.get("lora_catalog_timeout"), 15, 1),
            lora_cache_ttl=_as_int(data.get("lora_cache_ttl"), 300, 0),
            lora_max_results=_as_int(data.get("lora_max_results"), 50, 1),
            enable_lora_visual_gallery=_as_bool(
                data.get("enable_lora_visual_gallery"), True
            ),
            lora_visual_roots=_as_string_list(data.get("lora_visual_roots"), []),
            lora_visual_cache_mb=min(
                8192,
                _as_int(data.get("lora_visual_cache_mb"), 256, 0),
            ),
            lora_visual_warmup_workers=min(
                4,
                _as_int(data.get("lora_visual_warmup_workers"), 2, 1),
            ),
            lora_visual_preview_max_mb=min(
                32,
                _as_int(data.get("lora_visual_preview_max_mb"), 4, 1),
            ),
            lora_visual_thumbnail_size=min(
                1024,
                _as_int(data.get("lora_visual_thumbnail_size"), 512, 128),
            ),
            lora_alias_rules=_as_string_list(
                data.get("lora_alias_rules"),
                [],
            ),
            enable_lora_hybrid_search=_as_bool(
                data.get("enable_lora_hybrid_search"), False
            ),
            lora_embedding_provider_id=str(
                data.get("lora_embedding_provider_id", "")
            ).strip(),
            lora_rerank_provider_id=str(
                data.get("lora_rerank_provider_id", "")
            ).strip(),
            lora_embedding_top_k=min(
                100,
                _as_int(data.get("lora_embedding_top_k"), 20, 4),
            ),
            lora_rerank_top_n=min(
                50,
                _as_int(data.get("lora_rerank_top_n"), 8, 1),
            ),
            lora_retrieval_timeout=min(
                120,
                _as_int(data.get("lora_retrieval_timeout"), 30, 3),
            ),
            lora_tool_max_steps=_as_int(data.get("lora_tool_max_steps"), 4, 1),
            enable_task_lora_snapshot=_as_bool(
                data.get("enable_task_lora_snapshot"), True
            ),
            lora_snapshot_max_age=min(
                1800,
                _as_int(data.get("lora_snapshot_max_age"), 300, 15),
            ),
            enable_parallel_preflight=_as_bool(
                data.get("enable_parallel_preflight"), True
            ),
            provider_max_concurrent_jobs=min(
                32,
                _as_int(data.get("provider_max_concurrent_jobs"), 4, 1),
            ),
            enable_local_intent_router=_as_bool(
                data.get("enable_local_intent_router"), True
            ),
            structured_director_mode=(
                str(data.get("structured_director_mode", "auto")).strip().lower()
                if str(data.get("structured_director_mode", "auto")).strip().lower()
                in {"auto", "function_call", "json", "legacy"}
                else "auto"
            ),
            enable_prompt_composer_v2=_as_bool(
                data.get("enable_prompt_composer_v2"), True
            ),
            adaptive_negative_mode=(
                str(data.get("adaptive_negative_mode", "conservative"))
                .strip()
                .lower()
                if str(data.get("adaptive_negative_mode", "conservative"))
                .strip()
                .lower()
                in {"off", "conservative", "standard"}
                else "conservative"
            ),
            enable_prompt_diagnostics=_as_bool(
                data.get("enable_prompt_diagnostics"), True
            ),
            prompt_diagnostics_include_content=_as_bool(
                data.get("prompt_diagnostics_include_content"), False
            ),
            prompt_diagnostics_capacity=min(
                500,
                _as_int(data.get("prompt_diagnostics_capacity"), 50, 10),
            ),
            danbooru_validation_mode=(
                str(data.get("danbooru_validation_mode", "report")).strip().lower()
                if str(data.get("danbooru_validation_mode", "report")).strip().lower()
                in {"off", "report", "guarded"}
                else "report"
            ),
            danbooru_index_url=str(data.get("danbooru_index_url", "")).strip(),
            danbooru_index_timeout=min(
                300,
                _as_int(data.get("danbooru_index_timeout"), 30, 5),
            ),
            danbooru_index_max_size_mb=min(
                512,
                _as_int(data.get("danbooru_index_max_size_mb"), 256, 1),
            ),
            danbooru_api_base_url=(
                str(
                    data.get(
                        "danbooru_api_base_url",
                        "https://danbooru.donmai.us",
                    )
                ).strip()
                or "https://danbooru.donmai.us"
            ),
            danbooru_api_proxy_url=str(
                data.get("danbooru_api_proxy_url", "")
            ).strip(),
            danbooru_api_mode=(
                str(data.get("danbooru_api_mode", "identity")).strip().casefold()
                if str(data.get("danbooru_api_mode", "identity")).strip().casefold()
                in {"identity", "full"}
                else "identity"
            ),
            danbooru_api_general_min_posts=min(
                1_000_000,
                _as_int(data.get("danbooru_api_general_min_posts"), 10, 0),
            ),
            danbooru_api_meta_min_posts=min(
                1_000_000,
                _as_int(data.get("danbooru_api_meta_min_posts"), 10, 0),
            ),
            danbooru_api_page_size=min(
                1000,
                _as_int(data.get("danbooru_api_page_size"), 1000, 1),
            ),
            danbooru_api_request_interval_ms=min(
                10_000,
                _as_int(data.get("danbooru_api_request_interval_ms"), 750, 250),
            ),
            danbooru_api_timeout=min(
                300,
                _as_int(data.get("danbooru_api_timeout"), 60, 10),
            ),
            danbooru_api_max_records=min(
                3_000_000,
                _as_int(data.get("danbooru_api_max_records"), 2_000_000, 1_000),
            ),
            danbooru_api_include_aliases=_as_bool(
                data.get("danbooru_api_include_aliases"), True
            ),
            danbooru_auto_update_enabled=_as_bool(
                data.get("danbooru_auto_update_enabled"), False
            ),
            danbooru_auto_update_interval_hours=min(
                2160,
                _as_int(
                    data.get("danbooru_auto_update_interval_hours"),
                    168,
                    24,
                ),
            ),
            enable_prompt_asset_library=_as_bool(
                data.get("enable_prompt_asset_library"), True
            ),
            prompt_asset_remote_import_enabled=_as_bool(
                data.get("prompt_asset_remote_import_enabled"), False
            ),
            prompt_asset_max_download_mb=min(
                16,
                _as_int(data.get("prompt_asset_max_download_mb"), 16, 1),
            ),
            enable_prompt_lab=_as_bool(data.get("enable_prompt_lab"), True),
            prompt_lab_batch_capacity=min(
                128,
                _as_int(data.get("prompt_lab_batch_capacity"), 32, 4),
            ),
            prompt_lab_ttl_seconds=min(
                86400,
                _as_int(data.get("prompt_lab_ttl_seconds"), 1800, 60),
            ),
            enable_layered_lora_retrieval=_as_bool(
                data.get("enable_layered_lora_retrieval"), True
            ),
            lora_loader_node_id=str(data.get("lora_loader_node_id", "462")).strip()
            or "462",
            dynamic_lora_mode=(
                str(data.get("dynamic_lora_mode", "append")).strip().lower()
                if str(data.get("dynamic_lora_mode", "append")).strip().lower()
                in {"append", "replace"}
                else "append"
            ),
            max_dynamic_loras=_as_int(data.get("max_dynamic_loras"), 3, 0),
            max_preset_loras=_as_int(data.get("max_preset_loras"), 12, 1),
            max_total_dynamic_loras=_as_int(data.get("max_total_dynamic_loras"), 12, 1),
            default_style_preset=str(
                data.get("default_style_preset", "风格001")
            ).strip(),
            auto_reload_after_style_save=_as_bool(
                data.get("auto_reload_after_style_save"), True
            ),
            lora_presets=_as_mapping_list(
                data.get("lora_presets", _default_lora_presets())
            ),
            strict_lora_validation=_as_bool(data.get("strict_lora_validation"), True),
            prompt_node_id=str(
                data.get("prompt_node_id", DEFAULT_PROMPT_NODE_ID)
            ).strip(),
            negative_node_id=str(
                data.get("negative_node_id", DEFAULT_NEGATIVE_NODE_ID)
            ).strip(),
            primary_seed_node_id=str(
                data.get("primary_seed_node_id", DEFAULT_PRIMARY_SEED_NODE_ID)
            ).strip(),
            secondary_seed_node_id=str(
                data.get("secondary_seed_node_id", DEFAULT_SECONDARY_SEED_NODE_ID)
            ).strip(),
            resolution_node_id=str(
                data.get("resolution_node_id", DEFAULT_RESOLUTION_NODE_ID)
            ).strip(),
            default_width=min(
                MAX_IMAGE_SIDE,
                _as_int(data.get("default_width"), 832, MIN_IMAGE_SIDE),
            ),
            default_height=min(
                MAX_IMAGE_SIDE,
                _as_int(data.get("default_height"), 1216, MIN_IMAGE_SIDE),
            ),
            sampler_node_ids=_as_string_list(
                data.get("sampler_node_ids"), [DEFAULT_PRIMARY_SAMPLER_NODE_ID]
            ),
            sampler_steps_override=min(
                MAX_STEPS,
                _as_int(data.get("sampler_steps_override"), 0, 0),
            ),
            output_node_ids=_as_string_list(
                data.get("output_node_ids"),
                [DEFAULT_UPSCALE_OUTPUT_NODE_ID, DEFAULT_PREVIEW_OUTPUT_NODE_ID],
            ),
            upscale_output_node_id=str(
                data.get("upscale_output_node_id", DEFAULT_UPSCALE_OUTPUT_NODE_ID)
            ).strip(),
            enable_upscale=_as_bool(data.get("enable_upscale"), True),
            rtx_scale=min(
                4.0,
                max(1.0, _as_float(data.get("rtx_scale"), 2.0)),
            ),
            rtx_quality=(
                str(data.get("rtx_quality", "ULTRA")).strip().upper()
                if str(data.get("rtx_quality", "ULTRA")).strip().upper()
                in {"LOW", "MEDIUM", "HIGH", "ULTRA"}
                else "ULTRA"
            ),
            iterative_scale=min(
                2.0,
                max(1.1, _as_float(data.get("iterative_scale"), 1.5)),
            ),
            iterative_steps=min(
                4,
                _as_int(data.get("iterative_steps"), 3, 1),
            ),
            iterative_denoise=min(
                0.8,
                max(0.1, _as_float(data.get("iterative_denoise"), 0.35)),
            ),
            enable_inpaint=_as_bool(data.get("enable_inpaint"), True),
            send_generation_notice=_as_bool(data.get("send_generation_notice"), True),
            allow_global_interrupt=_as_bool(data.get("allow_global_interrupt"), False),
            max_concurrent_jobs=_as_int(data.get("max_concurrent_jobs"), 1, 1),
            user_cooldown=_as_int(data.get("user_cooldown"), 30),
            request_timeout=_as_int(data.get("request_timeout"), 30, 1),
            generation_timeout=_as_int(data.get("generation_timeout"), 1200, 10),
            poll_interval=_as_float(data.get("poll_interval"), 2.0, 0.25),
            max_prompt_length=_as_int(data.get("max_prompt_length"), 2000, 1),
            max_image_size_mb=_as_int(data.get("max_image_size_mb"), 50, 1),
        )

    def resolve_workflow_path(self, plugin_dir: Path) -> Path:
        """解析并返回工作流路径。"""
        path = Path(self.workflow_file).expanduser()
        return path if path.is_absolute() else plugin_dir / path

    def resolve_upscale_workflow_path(self, plugin_dir: Path) -> Path:
        """Resolve the standalone RTX workflow path."""
        path = Path(self.upscale_workflow_file).expanduser()
        return path if path.is_absolute() else plugin_dir / path

    @staticmethod
    def _resolve_plugin_path(plugin_dir: Path, value: str) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else plugin_dir / path

    def resolve_pipeline_workflow_path(
        self,
        plugin_dir: Path,
        pipeline: str,
    ) -> Path:
        """Resolve one explicit per-request generation pipeline."""
        value = {
            "base": self.base_workflow_file,
            "rtx": self.rtx_generation_workflow_file,
            "iterative": self.iterative_workflow_file,
        }.get(str(pipeline or "").strip().lower())
        if value is None:
            raise ValueError("未知生成管线")
        return self._resolve_plugin_path(plugin_dir, value)

    def resolve_inpaint_workflow_path(
        self,
        plugin_dir: Path,
        mode: str,
    ) -> Path:
        """Resolve the quick or LanPaint redraw workflow."""
        value = {
            "quick": self.inpaint_crop_workflow_file,
            "lanpaint": self.lanpaint_workflow_file,
        }.get(str(mode or "").strip().lower())
        if value is None:
            raise ValueError("未知重绘模式")
        return self._resolve_plugin_path(plugin_dir, value)

    def resolve_director_reference_path(self, plugin_dir: Path) -> Path:
        """解析并返回分镜导演参考提示词路径。"""
        path = Path(self.director_reference_file).expanduser()
        return path if path.is_absolute() else plugin_dir / path


@dataclass(frozen=True)
class LoraIdentityExpectation:
    """One LoRA identity captured before a safety-critical rewrite."""

    name: str
    sha256: str = ""
    source_fingerprint: str = ""


@dataclass(frozen=True)
class GenerationOptions:
    """单次生成使用的动态参数。"""

    prompt: str
    negative_prompt: str = ""
    seed: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    steps: Optional[int] = None
    cfg: Optional[float] = None
    enable_upscale: Optional[bool] = None
    use_prompt_llm: Optional[bool] = None
    prompt_expansion_mode: str = "standard"
    prompt_edit_mode: str = ""
    character_swap_preview: bool = False
    character_swap_mode: str = ""
    character_swap_use_target_lora: Optional[bool] = None
    character_swap_target_lora_strength: Optional[float] = None
    dynamic_loras: tuple["LoraSelection", ...] = ()
    lora_preset: str = ""
    lora_injection_mode: Optional[str] = None
    suppress_default_style: bool = False
    suppressed_prompt_terms: tuple[str, ...] = ()
    lora_identity_expectations: tuple["LoraIdentityExpectation", ...] = ()
    lora_activation_overrides: tuple[tuple[str, str], ...] = ()
    character_swap_target_lora: str = ""
    character_swap_preserved_character_loras: tuple[str, ...] = ()
    character_swap_forbid_character_loras: bool = False
    pipeline: str = ""
    inpaint_mode: str = ""
    semantic_redraw_mode: str = ""
    denoise: Optional[float] = None
    control_modes: tuple[str, ...] = ()
    semantic_required_positive_alias_groups: tuple[
        tuple[str, tuple[str, ...]], ...
    ] = ()
    semantic_forbidden_positive_terms: tuple[str, ...] = ()
    semantic_preserved_positive_terms: tuple[str, ...] = ()
    validate_llm_characters: bool = False
    llm_character_queries: tuple[str, ...] = ()
    llm_character_user_request: str = ""
    llm_prompt_source: str = ""


@dataclass(frozen=True)
class LoraSelection:
    """LLM 或高级用户为单次任务选择的 LoRA。"""

    name: str
    strength: float = 0.8


@dataclass(frozen=True)
class ImageReference:
    """ComfyUI 历史记录中的图片引用。"""

    filename: str
    subfolder: str = ""
    image_type: str = "output"
    node_id: str = ""


@dataclass(frozen=True)
class UploadedImageReference:
    """A validated ComfyUI input-image reference."""

    name: str
    subfolder: str = ""
    image_type: str = "input"

    @property
    def workflow_value(self) -> str:
        return f"{self.subfolder.rstrip('/')}/{self.name}" if self.subfolder else self.name


class GeneratedImagePaths(list[Path]):
    """Generated files plus safe execution metadata for the reply layer."""

    def __init__(self) -> None:
        super().__init__()
        self.elapsed_seconds: float = 0.0
        self.llm_elapsed_seconds: float = 0.0
        self.llm_call_count: int = 0
        self.comfy_elapsed_seconds: float = 0.0
        self.gpu_name: str = "未知 GPU"


@dataclass
class GenerationJob:
    """正在执行或排队中的生成任务。"""

    user_id: str
    prompt_preview: str
    created_at: float
    task: Any = None
    prompt_id: Optional[str] = None
    state: str = "queued"
    task_run_id: str = ""
    failed_stage: str = ""
    lora_snapshot: Any = None
    prefetched_gpu_name: str = ""
    llm_elapsed_seconds: float = 0.0
    llm_call_count: int = 0
    llm_external_character_authorities: dict[str, str] = field(default_factory=dict)
    danbooru_revision_signature: str = ""
