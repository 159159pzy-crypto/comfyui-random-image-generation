from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from .domain import DomainValidationError, GenerationIntent
from .matching import AssetMatch


@dataclass(frozen=True, slots=True)
class PlanningRequest:
    instruction: str
    base_intent: GenerationIntent
    explicit_overrides: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.instruction, str) or len(self.instruction) > 20_000:
            raise DomainValidationError("planning instruction must be a string of at most 20000 characters")
        if not isinstance(self.explicit_overrides, Mapping):
            raise DomainValidationError("explicit_overrides must be an object")


@dataclass(frozen=True, slots=True)
class PlanningResult:
    intent: GenerationIntent
    matches: tuple[AssetMatch[Any], ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def needs_confirmation(self) -> bool:
        return any(match.needs_confirmation for match in self.matches)


@dataclass(frozen=True, slots=True)
class PromptComposition:
    positive_prompt: str
    negative_prompt: str = ""
    sections: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.positive_prompt, str) or len(self.positive_prompt) > 20_000:
            raise DomainValidationError("positive_prompt must be a string of at most 20000 characters")
        if not isinstance(self.negative_prompt, str) or len(self.negative_prompt) > 20_000:
            raise DomainValidationError("negative_prompt must be a string of at most 20000 characters")
        if not isinstance(self.sections, Mapping):
            raise DomainValidationError("prompt sections must be an object")


@dataclass(frozen=True, slots=True)
class WorkflowBuildRequest:
    intent: GenerationIntent
    prompt: PromptComposition
    sample_seed: int = -1
    prompt_seed: int = -1

    def __post_init__(self) -> None:
        for name, value in (("sample_seed", self.sample_seed), ("prompt_seed", self.prompt_seed)):
            if isinstance(value, bool) or not isinstance(value, int) or not -1 <= value <= 2**63 - 1:
                raise DomainValidationError(f"{name} must be an integer between -1 and 2^63-1")


@dataclass(frozen=True, slots=True)
class WorkflowBuildResult:
    api_workflow: Mapping[str, Any]
    ui_workflow: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.api_workflow, Mapping) or not self.api_workflow:
            raise DomainValidationError("api_workflow must be a non-empty object")
        if self.ui_workflow is not None and not isinstance(self.ui_workflow, Mapping):
            raise DomainValidationError("ui_workflow must be an object or null")
        if not isinstance(self.metadata, Mapping):
            raise DomainValidationError("workflow metadata must be an object")


@runtime_checkable
class NativePlanner(Protocol):
    async def plan(self, request: PlanningRequest) -> PlanningResult: ...


@runtime_checkable
class NativePromptComposer(Protocol):
    def compose(self, intent: GenerationIntent) -> PromptComposition: ...


@runtime_checkable
class NativeDanbooruResolver(Protocol):
    def resolve(self, query: str, *, categories: Sequence[str] = ()) -> AssetMatch[Any]: ...


@runtime_checkable
class NativeWorkflowBuilder(Protocol):
    def build(self, request: WorkflowBuildRequest) -> WorkflowBuildResult: ...


@runtime_checkable
class NativeTaskScheduler(Protocol):
    async def submit(self, intent: GenerationIntent) -> str: ...

    async def cancel(self, job_id: str) -> bool: ...
