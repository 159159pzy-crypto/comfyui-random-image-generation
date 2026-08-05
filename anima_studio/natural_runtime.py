from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .domain import LoraSelection


@dataclass(frozen=True, slots=True)
class LoraIdentityExpectation:
    name: str
    sha256: str = ""
    source_fingerprint: str = ""


@dataclass(frozen=True, slots=True)
class WorkflowGenerationOptions:
    """Native workflow inputs consumed by the transitional JSON builders."""

    prompt: str
    negative_prompt: str = ""
    seed: int | None = None
    width: int | None = None
    height: int | None = None
    steps: int | None = None
    cfg: float | None = None
    enable_upscale: bool | None = None
    dynamic_loras: tuple[LoraSelection, ...] = ()
    lora_injection_mode: str | None = None
    suppress_default_style: bool = False
    suppressed_prompt_terms: tuple[str, ...] = ()
    lora_identity_expectations: tuple[LoraIdentityExpectation, ...] = ()
    character_swap_target_lora: str = ""
    character_swap_preserved_character_loras: tuple[str, ...] = ()
    character_swap_target_lora_strength: float | None = None
    character_swap_forbid_character_loras: bool = False
    pipeline: str = ""
    denoise: float | None = None
    control_modes: tuple[str, ...] = ()
    inpaint_mode: str = ""
    semantic_redraw_mode: str = ""


@dataclass(frozen=True, slots=True)
class NativeNaturalSettings:
    """Only settings owned by the local WebUI generation runtime."""

    workflow_file: str = "workflow/anima_v2_api.json"
    upscale_workflow_file: str = "workflow/rtx_upscale_api.json"
    base_workflow_file: str = "workflow/anima_base_api.json"
    rtx_generation_workflow_file: str = "workflow/anima_rtx_api.json"
    iterative_workflow_file: str = "workflow/anima_iterative_api.json"
    inpaint_crop_workflow_file: str = "workflow/anima_inpaint_crop_api.json"
    lanpaint_workflow_file: str = "workflow/anima_lanpaint_api.json"
    director_reference_file: str = "prompts/director_reference.txt"
    default_generation_pipeline: str = "rtx"
    default_width: int = 832
    default_height: int = 1216
    rtx_scale: float = 2.0
    rtx_quality: str = "ULTRA"
    iterative_scale: float = 1.5
    iterative_steps: int = 3
    iterative_denoise: float = 0.35
    enable_upscale: bool = True
    dynamic_lora_mode: str = "append"
    unet_loader_node_id: str = "429"
    unet_model_input_name: str = "unet_name"
    unet_model_name: str = ""
    lora_loader_node_id: str = "462"
    prompt_node_id: str = "76"
    negative_node_id: str = "77"
    primary_seed_node_id: str = "21"
    secondary_seed_node_id: str = ""
    resolution_node_id: str = ""
    sampler_node_ids: list[str] = field(default_factory=lambda: ["21"])
    output_node_ids: list[str] = field(default_factory=lambda: ["458", "26"])
    upscale_output_node_id: str = "26"

    @classmethod
    def from_runtime_config(
        cls,
        config: dict[str, object],
    ) -> "NativeNaturalSettings":
        return cls(
            default_generation_pipeline=str(config.get("default_pipeline") or "rtx"),
            default_width=int(config.get("default_width") or 832),
            default_height=int(config.get("default_height") or 1216),
            rtx_scale=float(config.get("rtx_scale") or 2.0),
            rtx_quality=str(config.get("rtx_quality") or "ULTRA").upper(),
        )

    @staticmethod
    def _resolve(root: Path, value: str) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else root / path

    def resolve_workflow_path(self, root: Path) -> Path:
        return self._resolve(root, self.workflow_file)

    def resolve_upscale_workflow_path(self, root: Path) -> Path:
        return self._resolve(root, self.upscale_workflow_file)

    def resolve_pipeline_workflow_path(self, root: Path, pipeline: str) -> Path:
        value = {
            "base": self.base_workflow_file,
            "rtx": self.rtx_generation_workflow_file,
            "iterative": self.iterative_workflow_file,
        }.get(str(pipeline or "").strip().casefold())
        if value is None:
            raise ValueError("unknown generation pipeline")
        return self._resolve(root, value)

    def resolve_inpaint_workflow_path(self, root: Path, mode: str) -> Path:
        value = {
            "quick": self.inpaint_crop_workflow_file,
            "lanpaint": self.lanpaint_workflow_file,
        }.get(str(mode or "").strip().casefold())
        if value is None:
            raise ValueError("unknown inpaint mode")
        return self._resolve(root, value)

    def resolve_director_reference_path(self, root: Path) -> Path:
        return self._resolve(root, self.director_reference_file)
