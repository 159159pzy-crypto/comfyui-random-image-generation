"""Discover and select ComfyUI API workflow files safely."""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from ..models import PluginSettings
from .workflow import (
    ControlWorkflowBuilder,
    ImageWorkflowBuilder,
    Img2ImgWorkflowBuilder,
    InpaintWorkflowBuilder,
    WorkflowBuilder,
    WorkflowError,
)
from .workflow_profiles import WorkflowProfileError, load_workflow_profile


class WorkflowRegistryError(ValueError):
    """Raised when workflow discovery or selection cannot be completed."""


SELECTABLE_GENERATION_PROFILES = frozenset(
    {"anima_base", "anima_rtx", "anima_iterative"}
)


@dataclass(frozen=True)
class WorkflowEntry:
    """A workflow exposed by the registry.

    Attributes:
        index: Stable 1-based index for the current discovery result.
        filename: File name displayed to administrators.
        path: Resolved absolute path of the workflow file.
    """

    index: int
    filename: str
    path: Path


@dataclass(frozen=True)
class WorkflowSelection:
    """A selected workflow and the builder configured for it.

    Attributes:
        entry: Selected workflow metadata.
        settings: Copy of the base settings with workflow/node overrides applied.
        builder: Ready-to-use workflow builder.
    """

    entry: WorkflowEntry
    settings: PluginSettings
    builder: WorkflowBuilder


@dataclass(frozen=True)
class WorkflowDescriptor:
    """Freshly inspected workflow metadata for management surfaces."""

    entry: WorkflowEntry
    task_type: str
    profile_id: str
    display_name: str
    selectable: bool
    error: str = ""


class WorkflowRegistry:
    """Discover workflows in one trusted directory and build selected entries.

    The registry does not cache directory contents. Calling :meth:`discover` or
    :meth:`select` therefore sees workflow files added or removed at runtime.

    Args:
        workflow_dir: Trusted directory containing ComfyUI API JSON files.
        settings: Base plugin settings copied for every selection.
    """

    def __init__(self, workflow_dir: Path, settings: PluginSettings) -> None:
        self._workflow_dir = workflow_dir.expanduser().resolve()
        self._settings = settings

    @property
    def workflow_dir(self) -> Path:
        """Return the resolved directory scanned by this registry."""
        return self._workflow_dir

    def discover(self) -> tuple[WorkflowEntry, ...]:
        """Scan and return safe JSON workflows in deterministic order.

        Only direct child files with a case-insensitive ``.json`` suffix are
        included. A symlink or other resolved path outside ``workflow_dir`` is
        ignored to prevent path traversal.

        Returns:
            Workflow entries sorted case-insensitively by file name.

        Raises:
            WorkflowRegistryError: If the configured directory is missing,
                is not a directory, or cannot be read.
        """
        if not self._workflow_dir.is_dir():
            raise WorkflowRegistryError(
                f"Workflow directory does not exist: {self._workflow_dir}"
            )

        try:
            candidates = tuple(self._workflow_dir.iterdir())
        except OSError as exc:
            raise WorkflowRegistryError(
                f"Unable to read workflow directory: {self._workflow_dir}"
            ) from exc

        workflow_paths: list[Path] = []
        for candidate in candidates:
            if candidate.suffix.casefold() != ".json":
                continue
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if not self._is_within_directory(resolved) or not resolved.is_file():
                continue
            workflow_paths.append(resolved)

        workflow_paths.sort(key=lambda path: (path.name.casefold(), path.name))
        return tuple(
            WorkflowEntry(index=index, filename=path.name, path=path)
            for index, path in enumerate(workflow_paths, start=1)
        )

    def list_workflows(self) -> tuple[WorkflowEntry, ...]:
        """Return the current workflow list for command display.

        Returns:
            The same fresh discovery result as :meth:`discover`.
        """
        return self.discover()

    def describe(self) -> tuple[WorkflowDescriptor, ...]:
        """Inspect every fresh entry and distinguish generation from upscale."""

        result: list[WorkflowDescriptor] = []
        for entry in self.discover():
            selected_settings = replace(
                self._settings,
                workflow_file=str(entry.path),
            )
            try:
                profile = load_workflow_profile(entry.path, selected_settings)
                if profile.task_type == "text_to_image":
                    WorkflowBuilder(entry.path, selected_settings)
                    selectable = profile.profile_id in SELECTABLE_GENERATION_PROFILES
                    error = (
                        ""
                        if selectable
                        else "旧版兼容工作流仅用于回滚，不能设为正式默认入口"
                    )
                elif profile.task_type == "upscale":
                    ImageWorkflowBuilder(entry.path, selected_settings)
                    selectable = False
                    error = "独立图片放大工作流不能设为当前生图工作流"
                elif profile.task_type == "inpaint":
                    InpaintWorkflowBuilder(entry.path, selected_settings)
                    selectable = False
                    error = "重绘工作流请通过 /重绘 使用，不能设为当前生图工作流"
                elif profile.task_type == "img2img":
                    Img2ImgWorkflowBuilder(entry.path, selected_settings)
                    selectable = False
                    error = (
                        "Img2img workflows require an input image and cannot be "
                        "selected as the default text-to-image workflow"
                    )
                elif profile.task_type == "control_generation":
                    ControlWorkflowBuilder(entry.path, selected_settings)
                    selectable = False
                    error = "底图控制工作流请通过 /底图控制 使用，不能设为默认文生图"
                else:
                    raise WorkflowRegistryError("不支持的工作流任务类型")
                result.append(
                    WorkflowDescriptor(
                        entry=entry,
                        task_type=profile.task_type,
                        profile_id=profile.profile_id,
                        display_name=profile.display_name,
                        selectable=selectable,
                        error=error,
                    )
                )
            except (OSError, ValueError, WorkflowError, WorkflowProfileError) as exc:
                result.append(
                    WorkflowDescriptor(
                        entry=entry,
                        task_type="invalid",
                        profile_id="",
                        display_name=entry.filename,
                        selectable=False,
                        error=str(exc)[:300],
                    )
                )
        return tuple(result)

    def select_filename(self, filename: str) -> WorkflowSelection:
        """Select one fresh direct-child workflow by exact filename."""

        value = str(filename or "").strip()
        if (
            not value
            or len(value) > 255
            or "/" in value
            or "\\" in value
            or not value.casefold().endswith(".json")
        ):
            raise WorkflowRegistryError("Workflow filename is invalid")
        entries = self.discover()
        matches = [entry for entry in entries if entry.filename.casefold() == value.casefold()]
        if len(matches) != 1:
            raise WorkflowRegistryError("Workflow file is missing or ambiguous")
        return self.select(matches[0].index)

    def select(
        self,
        index: int,
        input_node_id: Optional[str] = None,
        output_node_id: Optional[str] = None,
    ) -> WorkflowSelection:
        """Select a workflow by 1-based index and create its builder.

        Args:
            index: 1-based index from the latest displayed discovery list.
            input_node_id: Optional positive-prompt input node override.
            output_node_id: Optional sole preferred output node override.

        Returns:
            Selection metadata, copied settings, and a ready builder.

        Raises:
            WorkflowRegistryError: If the index or a node ID is invalid.
            WorkflowError: If the selected JSON is not a valid workflow for
                the resulting node settings.
        """
        entries = self.discover()
        if isinstance(index, bool) or not isinstance(index, int):
            raise WorkflowRegistryError("Workflow index must be an integer")
        if index < 1 or index > len(entries):
            raise WorkflowRegistryError(
                f"Workflow index must be between 1 and {len(entries)}"
            )

        entry = entries[index - 1]
        overrides: dict[str, object] = {"workflow_file": str(entry.path)}
        normalized_input = self._normalize_node_id(input_node_id, "input")
        normalized_output = self._normalize_node_id(output_node_id, "output")
        if normalized_input is not None:
            overrides["prompt_node_id"] = normalized_input
        if normalized_output is not None:
            overrides["output_node_ids"] = [normalized_output]

        selected_settings = replace(self._settings, **overrides)
        profile = load_workflow_profile(entry.path, selected_settings)
        if profile.task_type == "img2img":
            raise WorkflowRegistryError(
                "Img2img workflows cannot be selected as the default text-to-image workflow"
            )
        builder = WorkflowBuilder(entry.path, selected_settings)
        return WorkflowSelection(
            entry=entry,
            settings=selected_settings,
            builder=builder,
        )

    def create_builder(
        self,
        index: int,
        input_node_id: Optional[str] = None,
        output_node_id: Optional[str] = None,
    ) -> WorkflowBuilder:
        """Create a builder for a selected workflow.

        Args:
            index: 1-based workflow index.
            input_node_id: Optional positive-prompt input node override.
            output_node_id: Optional sole preferred output node override.

        Returns:
            A builder configured for the selected workflow and node IDs.
        """
        return self.select(index, input_node_id, output_node_id).builder

    def _is_within_directory(self, path: Path) -> bool:
        """Return whether a resolved path remains under the trusted root."""
        try:
            path.relative_to(self._workflow_dir)
        except ValueError:
            return False
        return True

    @staticmethod
    def _normalize_node_id(value: Optional[str], label: str) -> Optional[str]:
        """Normalize an optional node ID and reject empty overrides."""
        if value is None:
            return None
        if not isinstance(value, str):
            raise WorkflowRegistryError(f"{label.title()} node ID must be a string")
        normalized = value.strip()
        if not normalized:
            raise WorkflowRegistryError(f"{label.title()} node ID cannot be empty")
        return normalized


__all__ = [
    "WorkflowDescriptor",
    "WorkflowEntry",
    "WorkflowRegistry",
    "WorkflowRegistryError",
    "WorkflowSelection",
]
