"""Native service facade for the local Anima studio.

The facade keeps orchestration policy out of the HTTP layer.  In particular,
network-backed operations are only exposed through methods whose callers must
explicitly confirm a manual action. Backends remain injectable for controlled
tests, while the default factory uses only local V7 implementations.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import shutil
import threading
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from anima_studio.natural_runtime import NativeNaturalSettings
from anima_studio.studio_services import (
    ConfigProfileService,
    DanbooruApiBuilder,
    DanbooruBuildOptions,
    DanbooruTagIndex,
    DanbooruUpdateScheduler,
    LoraAnalysisPipeline,
    LoraArchiveService,
    LoraCatalogService,
    LoraDownloadService,
    LoraRecord,
    LoraVisualService,
    PromptAssetLibrary,
    PromptLab,
    PromptLabBatch,
    WorkflowRegistry,
)


class StudioServiceError(RuntimeError):
    """A safe, user-facing V6 service error."""


class CapabilityDisabledError(StudioServiceError):
    """Raised when an optional live integration has not been configured."""


class ManualActionRequiredError(StudioServiceError):
    """Raised when an external operation was not explicitly confirmed."""


class ModelQuarantineError(StudioServiceError):
    """Raised when a model cannot be safely quarantined or restored."""


@dataclass(frozen=True)
class Capability:
    available: bool
    ready: bool
    manual_only: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _public(value: Any) -> Any:
    """Convert native dataclasses and paths into JSON-safe snapshots."""

    if is_dataclass(value) and not isinstance(value, type):
        return _public(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _public(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_public(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


async def _await_result(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _manual_confirmation(confirmed: bool) -> None:
    if confirmed is not True:
        raise ManualActionRequiredError(
            "external synchronization requires an explicit manual confirmation"
        )


class PromptStudioService:
    """Prompt asset library and side-effect-free Prompt Lab adapter."""

    def __init__(
        self,
        library: PromptAssetLibrary,
        prompt_lab: PromptLab | None = None,
    ) -> None:
        self.library = library
        self.prompt_lab = prompt_lab or PromptLab()

    def capabilities(self) -> dict[str, dict[str, Any]]:
        return {
            "prompt_assets": Capability(True, True).to_dict(),
            "prompt_lab": Capability(True, True).to_dict(),
            "prompt_asset_remote_sync": Capability(
                True,
                True,
                manual_only=True,
                reason="remote imports run only after an operator action",
            ).to_dict(),
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "capabilities": self.capabilities(),
            "library": _public(self.library.status()),
        }

    def import_native_assets(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        source: str = "anima-native",
        mode: str = "replace_source",
    ) -> dict[str, Any]:
        """Import adapter-owned Anima records without remapping taxonomy.

        Asset types, categories, traits and their ordering are passed to the
        native library unchanged. The caller remains the authority for the
        Anima taxonomy.
        """

        if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
            raise StudioServiceError("native prompt assets must be a sequence")
        detached: list[dict[str, Any]] = []
        for index, record in enumerate(records):
            if not isinstance(record, Mapping):
                raise StudioServiceError(
                    f"native prompt asset at index {index} must be an object"
                )
            if not str(record.get("asset_type") or record.get("type") or "").strip():
                raise StudioServiceError(
                    f"native prompt asset at index {index} has no asset_type"
                )
            detached.append(dict(record))
        payload = json.dumps(
            {"assets": detached, "taxonomy": "anima-native"},
            ensure_ascii=False,
        ).encode("utf-8")
        return _public(
            self.library.import_bytes(
                payload,
                source=source,
                content_type="application/json",
                provenance={"taxonomy": "anima-native"},
                mode=mode,
            )
        )

    async def update_from_url(
        self,
        url: str,
        *,
        confirm_manual: bool = False,
        **options: Any,
    ) -> dict[str, Any]:
        _manual_confirmation(confirm_manual)
        result = await self.library.update_from_url(url, **options)
        return _public(result)

    def search(self, query: str = "", **filters: Any) -> dict[str, Any]:
        return _public(self.library.search(query, **filters))

    def facets(self, **filters: Any) -> dict[str, Any]:
        return _public(self.library.facets(**filters))

    def generate_candidates(self, **request: Any) -> dict[str, Any]:
        return _public(self.prompt_lab.generate_candidates(**request))

    def generate_batch(self, **request: Any) -> PromptLabBatch:
        """Return the typed batch when the caller intends to confirm it."""

        return self.prompt_lab.generate_candidates(**request)

    def confirm_candidate(
        self,
        batch: PromptLabBatch,
        selection: int | str,
    ) -> dict[str, Any]:
        return _public(self.prompt_lab.confirm_candidate(batch, selection))


class LoraStudioService:
    """Narrow facade over optional LoRA catalog and operation services."""

    def __init__(
        self,
        *,
        catalog: Any = None,
        visuals: Any = None,
        analyzer: Any = None,
        archiver: Any = None,
        downloader: Any = None,
    ) -> None:
        self.catalog = catalog
        self.visuals = visuals
        self.analyzer = analyzer
        self.archiver = archiver
        self.downloader = downloader
        self._records: tuple[Any, ...] = ()
        self._refreshed_at = ""

    @staticmethod
    def _capability(backend: Any, *, manual: bool = False) -> dict[str, Any]:
        if backend is None:
            return Capability(
                False,
                False,
                manual_only=manual,
                reason="live backend is not configured",
            ).to_dict()
        return Capability(True, True, manual_only=manual).to_dict()

    def capabilities(self) -> dict[str, dict[str, Any]]:
        return {
            "lora_catalog": self._capability(self.catalog, manual=True),
            "lora_visuals": self._capability(self.visuals),
            "lora_analysis": self._capability(self.analyzer, manual=True),
            "lora_archive": self._capability(self.archiver, manual=True),
            "lora_download": self._capability(self.downloader, manual=True),
        }

    def snapshot(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "capabilities": self.capabilities(),
            "record_count": len(self._records),
            "refreshed_at": self._refreshed_at,
            "records": _public(self._records),
        }
        if self.archiver is not None and hasattr(self.archiver, "catalog_status"):
            result["archive"] = _public(self.archiver.catalog_status(self._records))
        if self.visuals is not None and hasattr(self.visuals, "warmup_status"):
            result["visual_warmup"] = _public(self.visuals.warmup_status())
        return result

    def set_records(self, records: Sequence[Any]) -> None:
        """Publish a caller-supplied fresh catalog without any network work."""

        self._records = tuple(records)
        self._refreshed_at = _utc_now()

    async def refresh_catalog(
        self,
        *,
        confirm_manual: bool = False,
        force: bool = True,
    ) -> dict[str, Any]:
        _manual_confirmation(confirm_manual)
        if self.catalog is None:
            raise CapabilityDisabledError("LoRA catalog backend is not configured")
        method = getattr(self.catalog, "list_loras", None)
        if not callable(method):
            raise CapabilityDisabledError("LoRA catalog backend has no list_loras")
        records = await _await_result(method(force=force))
        self.set_records(tuple(records))
        return self.snapshot()

    def visual_manifest(self) -> dict[str, Any]:
        if self.visuals is None:
            raise CapabilityDisabledError("LoRA visual backend is not configured")
        return _public(self.visuals.build_manifest(self._records))

    def visual_page(self, **filters: Any) -> dict[str, Any]:
        if self.visuals is None:
            raise CapabilityDisabledError("LoRA visual backend is not configured")
        return _public(self.visuals.list_page(self._records, **filters))

    async def analyze(
        self,
        details: Sequence[Any],
        llm_callback: Callable[..., Awaitable[Any]],
        *,
        confirm_manual: bool = False,
        **options: Any,
    ) -> dict[str, Any]:
        _manual_confirmation(confirm_manual)
        if self.analyzer is None:
            raise CapabilityDisabledError("LoRA analysis backend is not configured")
        result = await _await_result(
            self.analyzer.run(details, llm_callback, **options)
        )
        return _public(result)

    async def archive(
        self,
        llm_callback: Callable[..., Awaitable[Any]],
        *,
        confirm_manual: bool = False,
        **options: Any,
    ) -> dict[str, Any]:
        _manual_confirmation(confirm_manual)
        if self.archiver is None:
            raise CapabilityDisabledError("LoRA archive backend is not configured")
        method = getattr(self.archiver, "archive_with_llm", None)
        if not callable(method):
            raise CapabilityDisabledError(
                "LoRA archive backend has no archive_with_llm"
            )
        return _public(
            await _await_result(method(self._records, llm_callback, **options))
        )

    async def download(
        self,
        url: str,
        *,
        confirm_manual: bool = False,
    ) -> dict[str, Any]:
        _manual_confirmation(confirm_manual)
        if self.downloader is None:
            raise CapabilityDisabledError("LoRA download backend is not configured")
        return _public(await _await_result(self.downloader.download_from_url(url)))


class DanbooruStudioService:
    """Manual-only adapter for the resumable official API index builder."""

    def __init__(
        self,
        builder: DanbooruApiBuilder | Any | None,
        scheduler: DanbooruUpdateScheduler | Any | None = None,
    ) -> None:
        self.builder = builder
        if scheduler is None and builder is not None:
            checkpoint_path = getattr(builder, "checkpoint_path", None)
            if checkpoint_path is not None:
                checkpoint = Path(checkpoint_path)
                scheduler = DanbooruUpdateScheduler(
                    builder,
                    checkpoint.with_name("danbooru.schedule.json"),
                )
        self.scheduler = scheduler
        self._last_result: dict[str, Any] | None = None
        self._last_error = ""

    def capabilities(self) -> dict[str, dict[str, Any]]:
        ready = self.builder is not None
        capabilities = {
            "danbooru_builder": Capability(
                ready,
                ready,
                manual_only=True,
                reason="" if ready else "Danbooru builder is not configured",
            ).to_dict()
        }
        capabilities["danbooru_scheduler"] = Capability(
            self.scheduler is not None,
            self.scheduler is not None,
            manual_only=True,
            reason=(
                ""
                if self.scheduler is not None
                else "Danbooru scheduler is not configured"
            ),
        ).to_dict()
        return capabilities

    def snapshot(self) -> dict[str, Any]:
        checkpoint = {"available": False}
        if self.builder is not None:
            status = getattr(self.builder, "checkpoint_status", None)
            if callable(status):
                checkpoint = _public(status())
        return {
            "capabilities": self.capabilities(),
            "checkpoint": checkpoint,
            "schedule": (
                _public(self.scheduler.snapshot())
                if self.scheduler is not None
                else {
                    "enabled": False,
                    "network_default": "offline",
                    "requires_confirmation": True,
                    "available": False,
                }
            ),
            "last_result": self._last_result,
            "last_error": self._last_error,
        }

    async def build(
        self,
        options: DanbooruBuildOptions | Mapping[str, Any] | None = None,
        *,
        confirm_manual: bool = False,
        progress: Callable[[dict[str, Any]], Any] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        _manual_confirmation(confirm_manual)
        if self.builder is None:
            raise CapabilityDisabledError("Danbooru builder is not configured")
        effective = (
            options
            if isinstance(options, DanbooruBuildOptions)
            else DanbooruBuildOptions(**dict(options or {}))
        )
        try:
            result = await _await_result(
                self.builder.build(
                    effective,
                    progress=progress,
                    cancel_event=cancel_event,
                )
            )
        except Exception as exc:
            self._last_error = str(exc)[:500]
            raise
        self._last_result = _public(result)
        self._last_error = ""
        return dict(self._last_result)

    def configure_schedule(
        self,
        *,
        enabled: bool,
        interval_hours: int = 168,
        options: DanbooruBuildOptions | Mapping[str, Any] | None = None,
        confirm_manual: bool = False,
    ) -> dict[str, Any]:
        if self.scheduler is None:
            raise CapabilityDisabledError("Danbooru scheduler is not configured")
        if enabled:
            _manual_confirmation(confirm_manual)
        return _public(
            self.scheduler.configure(
                enabled=enabled,
                interval_hours=interval_hours,
                options=options,
                confirm_manual=confirm_manual,
            )
        )

    async def run_scheduled(
        self,
        *,
        confirm_manual: bool = False,
        force: bool = False,
        progress: Callable[[dict[str, Any]], Any] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        _manual_confirmation(confirm_manual)
        if self.scheduler is None:
            raise CapabilityDisabledError("Danbooru scheduler is not configured")
        try:
            result = await self.scheduler.run_due(
                confirm_scheduled=confirm_manual,
                force=force,
                progress=progress,
                cancel_event=cancel_event,
            )
        except Exception as exc:
            self._last_error = str(exc)[:500]
            raise
        public = _public(result)
        if public.get("started") and isinstance(public.get("result"), Mapping):
            self._last_result = dict(public["result"])
            self._last_error = ""
        return public


class WorkflowProfileStudioService:
    """Local workflow discovery plus secret-free configuration profiles."""

    def __init__(
        self,
        registry: WorkflowRegistry,
        profiles: ConfigProfileService,
    ) -> None:
        self.registry = registry
        self.profiles = profiles

    def capabilities(self) -> dict[str, dict[str, Any]]:
        workflow_ready = self.registry.workflow_dir.is_dir()
        return {
            "workflow_registry": Capability(
                True,
                workflow_ready,
                reason="" if workflow_ready else "workflow directory is missing",
            ).to_dict(),
            "config_profiles": Capability(True, True).to_dict(),
        }

    def snapshot(self) -> dict[str, Any]:
        workflows: list[Any] = []
        error = ""
        try:
            workflows = _public(self.registry.describe())
        except Exception as exc:
            error = str(exc)[:500]
        return {
            "capabilities": self.capabilities(),
            "workflows": workflows,
            "workflow_error": error,
            "profiles": _public(self.profiles.list_profiles()),
        }

    def list_workflows(self) -> list[dict[str, Any]]:
        return _public(self.registry.describe())

    def list_profiles(self) -> list[dict[str, Any]]:
        return _public(self.profiles.list_profiles())

    def save_profile(
        self,
        name: str,
        config: Mapping[str, Any],
        *,
        overwrite: bool = False,
        activate: bool = False,
    ) -> dict[str, Any]:
        return _public(
            self.profiles.save_profile(
                name,
                config,
                overwrite=overwrite,
                activate=activate,
            )
        )

    def export_profile(self, name: str) -> dict[str, Any]:
        return _public(self.profiles.export_profile(name))

    def delete_profile(self, name: str) -> dict[str, Any]:
        return _public(self.profiles.delete_profile(name))

    def import_profile(
        self,
        payload: Mapping[str, Any],
        *,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        return _public(self.profiles.import_profile(payload, overwrite=overwrite))

    def activate_profile(
        self,
        name: str,
        config: dict[str, Any],
        *,
        persist_updates: Callable[[dict[str, Any]], bool | None] | None = None,
    ) -> dict[str, Any]:
        return _public(
            self.profiles.activate_profile(
                name,
                config,
                persist_updates=persist_updates,
            )
        )


ReferenceChecker = Callable[[str, str], Iterable[str]]


class ModelQuarantineService:
    """Reversible, project-managed model removal with exact-name checks."""

    _KINDS = frozenset({"checkpoint", "lora", "unet", "vae"})

    def __init__(
        self,
        model_roots: Mapping[str, Sequence[str | os.PathLike[str]]],
        quarantine_root: str | os.PathLike[str],
        *,
        reference_checker: ReferenceChecker | None = None,
    ) -> None:
        roots: dict[str, tuple[Path, ...]] = {}
        for raw_kind, values in model_roots.items():
            kind = str(raw_kind).strip().casefold()
            if kind not in self._KINDS:
                raise ValueError(f"unsupported model kind: {raw_kind}")
            resolved: list[Path] = []
            for value in values:
                path = Path(value).expanduser().resolve(strict=False)
                if path not in resolved:
                    resolved.append(path)
            roots[kind] = tuple(resolved)
        self.model_roots = roots
        self.quarantine_root = Path(quarantine_root).expanduser().resolve(strict=False)
        self.index_path = self.quarantine_root / "index.json"
        self.audit_path = self.quarantine_root / "audit.jsonl"
        self.reference_checker = reference_checker
        self._lock = threading.RLock()
        self._audit_error = ""

    def capabilities(self) -> dict[str, dict[str, Any]]:
        configured = any(self.model_roots.values())
        return {
            "model_quarantine": Capability(
                configured,
                configured,
                reason="" if configured else "no model roots are configured",
            ).to_dict()
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            index = self._read_index()
        entries = list(index["entries"].values())
        entries.sort(key=lambda item: (item["quarantined_at"], item["id"]))
        return {
            "capabilities": self.capabilities(),
            "entry_count": len(entries),
            "entries": entries,
            "audit_error": self._audit_error,
        }

    @staticmethod
    def _exact_relative_name(value: str) -> str:
        text = str(value or "").strip().replace("\\", "/")
        pure = PurePosixPath(text)
        if (
            not text
            or len(text) > 512
            or pure.is_absolute()
            or any(part in {"", ".", ".."} for part in pure.parts)
            or ":" in text
            or any(ord(character) < 32 for character in text)
        ):
            raise ModelQuarantineError("model name must be an exact relative path")
        return pure.as_posix()

    @staticmethod
    def _confirm(exact_name: str, confirm_name: str) -> None:
        if str(confirm_name or "").strip().replace("\\", "/") != exact_name:
            raise ModelQuarantineError("confirmation must exactly match model name")

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _find_exact(self, kind: str, exact_name: str) -> tuple[Path, Path]:
        roots = self.model_roots.get(kind, ())
        matches: list[tuple[Path, Path]] = []
        for root in roots:
            candidate = root.joinpath(*PurePosixPath(exact_name).parts)
            try:
                resolved = candidate.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if self._within(resolved, root) and resolved.is_file() and not candidate.is_symlink():
                matches.append((root, resolved))
        if not matches:
            raise ModelQuarantineError("exact model file does not exist")
        if len(matches) != 1:
            raise ModelQuarantineError("exact model name is ambiguous across roots")
        return matches[0]

    @staticmethod
    def _matching_references(
        exact_name: str,
        references: Iterable[str],
    ) -> tuple[str, ...]:
        exact_key = exact_name.casefold()
        basename_key = PurePosixPath(exact_name).name.casefold()
        matched: list[str] = []
        for raw in references:
            value = str(raw or "").strip().replace("\\", "/")
            key = value.casefold()
            if key in {exact_key, basename_key}:
                matched.append(value)
        return tuple(dict.fromkeys(matched))

    def quarantine(
        self,
        kind: str,
        exact_name: str,
        *,
        confirm_name: str,
        references: Iterable[str] = (),
    ) -> dict[str, Any]:
        normalized_kind = str(kind or "").strip().casefold()
        if normalized_kind not in self._KINDS:
            raise ModelQuarantineError("unsupported model kind")
        name = self._exact_relative_name(exact_name)
        self._confirm(name, confirm_name)
        discovered = list(references)
        if self.reference_checker is not None:
            discovered.extend(self.reference_checker(normalized_kind, name))
        matched = self._matching_references(name, discovered)
        if matched:
            raise ModelQuarantineError(
                "model is still referenced: " + ", ".join(matched)
            )

        with self._lock:
            root, source = self._find_exact(normalized_kind, name)
            entry_id = uuid.uuid4().hex
            destination = self.quarantine_root / "files" / entry_id / Path(name).name
            destination.parent.mkdir(parents=True, exist_ok=False)
            digest = self._sha256(source)
            entry = {
                "id": entry_id,
                "kind": normalized_kind,
                "exact_name": name,
                "source_root": str(root),
                "source_path": str(source),
                "quarantine_path": str(destination),
                "size": source.stat().st_size,
                "sha256": digest,
                "quarantined_at": _utc_now(),
            }
            index = self._read_index()
            previous_index = json.loads(json.dumps(index))
            try:
                shutil.move(str(source), str(destination))
                index["entries"][entry_id] = entry
                self._write_index(index)
                self._append_audit("quarantine", entry)
            except Exception:
                try:
                    self._write_index(previous_index)
                except Exception:
                    pass
                if destination.is_file() and not source.exists():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(destination), str(source))
                shutil.rmtree(destination.parent, ignore_errors=True)
                raise
            return dict(entry)

    def restore(
        self,
        entry_id: str,
        *,
        confirm_name: str,
    ) -> dict[str, Any]:
        identifier = str(entry_id or "").strip()
        if len(identifier) != 32 or any(
            character not in "0123456789abcdef" for character in identifier
        ):
            raise ModelQuarantineError("quarantine entry id is invalid")
        with self._lock:
            index = self._read_index()
            entry = index["entries"].get(identifier)
            if not isinstance(entry, Mapping):
                raise ModelQuarantineError("quarantine entry does not exist")
            name = self._exact_relative_name(str(entry["exact_name"]))
            self._confirm(name, confirm_name)
            root = Path(str(entry["source_root"])).resolve(strict=False)
            if root not in self.model_roots.get(str(entry["kind"]), ()):
                raise ModelQuarantineError("original model root is no longer trusted")
            try:
                source = Path(str(entry["quarantine_path"])).resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise ModelQuarantineError("quarantine payload is missing") from exc
            expected_parent = (self.quarantine_root / "files" / identifier).resolve(
                strict=False
            )
            if not self._within(source, expected_parent) or not source.is_file():
                raise ModelQuarantineError("quarantine payload is missing or unsafe")
            if self._sha256(source) != entry["sha256"]:
                raise ModelQuarantineError("quarantine payload checksum changed")
            destination = root.joinpath(*PurePosixPath(name).parts)
            resolved_parent = destination.parent.resolve(strict=False)
            if not self._within(resolved_parent, root):
                raise ModelQuarantineError("restore target escapes model root")
            if destination.exists():
                raise ModelQuarantineError("restore target already exists")
            destination.parent.mkdir(parents=True, exist_ok=True)
            previous_index = json.loads(json.dumps(index))
            try:
                shutil.move(str(source), str(destination))
                del index["entries"][identifier]
                self._write_index(index)
                restored = {**dict(entry), "restored_at": _utc_now()}
                self._append_audit("restore", restored)
            except Exception:
                try:
                    self._write_index(previous_index)
                except Exception:
                    pass
                if destination.is_file() and not source.exists():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(destination), str(source))
                raise
            shutil.rmtree(source.parent, ignore_errors=True)
            return restored

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _empty_index(self) -> dict[str, Any]:
        return {"schema_version": 1, "entries": {}}

    def _read_index(self) -> dict[str, Any]:
        if not self.index_path.is_file():
            return self._empty_index()
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ModelQuarantineError("quarantine index is unreadable") from exc
        if payload.get("schema_version") != 1 or not isinstance(
            payload.get("entries"), dict
        ):
            raise ModelQuarantineError("quarantine index has an invalid schema")
        return payload

    def _write_index(self, payload: Mapping[str, Any]) -> None:
        self.quarantine_root.mkdir(parents=True, exist_ok=True)
        temporary = self.index_path.with_name(f".{self.index_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.index_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _append_audit(self, action: str, entry: Mapping[str, Any]) -> None:
        self.quarantine_root.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": _utc_now(),
            "action": action,
            "entry_id": entry["id"],
            "kind": entry["kind"],
            "exact_name": entry["exact_name"],
            "sha256": entry["sha256"],
        }
        try:
            with self.audit_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(
                    json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
                )
            self._audit_error = ""
        except OSError as exc:
            # The index and file move are authoritative.  A secondary audit
            # sink failure must not leave them disagreeing after a rollback.
            self._audit_error = f"audit write failed: {type(exc).__name__}"
            return


class StudioServices:
    """Aggregate snapshot used by the future ``/api/v6/capabilities`` route."""

    def __init__(
        self,
        *,
        prompts: PromptStudioService,
        loras: LoraStudioService,
        danbooru: DanbooruStudioService,
        workflows: WorkflowProfileStudioService,
        models: ModelQuarantineService,
    ) -> None:
        self.prompts = prompts
        self.loras = loras
        self.danbooru = danbooru
        self.workflows = workflows
        self.models = models

    @classmethod
    def create_local(
        cls,
        data_dir: str | os.PathLike[str],
        *,
        settings: NativeNaturalSettings | None = None,
        workflow_dir: str | os.PathLike[str] | None = None,
        model_roots: Mapping[str, Sequence[str | os.PathLike[str]]] | None = None,
        lora_catalog: Any = None,
        lora_visuals: Any = None,
        lora_analyzer: Any = None,
        lora_archiver: Any = None,
        lora_downloader: Any = None,
        danbooru_builder: DanbooruApiBuilder | Any | None = None,
        reference_checker: ReferenceChecker | None = None,
    ) -> "StudioServices":
        root = Path(data_dir).expanduser().resolve(strict=False)
        effective_settings = settings or NativeNaturalSettings()
        workflow_path = Path(workflow_dir) if workflow_dir is not None else (
            Path(__file__).resolve().parent / "upstream" / "workflow"
        )
        if danbooru_builder is None:
            index = DanbooruTagIndex(root / "danbooru.sqlite3")
            danbooru_builder = DanbooruApiBuilder(
                index,
                root / "danbooru.checkpoint.sqlite3",
            )
        return cls(
            prompts=PromptStudioService(
                PromptAssetLibrary(root / "prompt_assets.sqlite3")
            ),
            loras=LoraStudioService(
                catalog=lora_catalog,
                visuals=lora_visuals,
                analyzer=lora_analyzer,
                archiver=lora_archiver,
                downloader=lora_downloader,
            ),
            danbooru=DanbooruStudioService(danbooru_builder),
            workflows=WorkflowProfileStudioService(
                WorkflowRegistry(Path(workflow_path), effective_settings),
                ConfigProfileService(root / "config_profiles.json"),
            ),
            models=ModelQuarantineService(
                model_roots or {},
                root / "quarantine",
                reference_checker=reference_checker,
            ),
        )

    def capabilities(self) -> dict[str, dict[str, Any]]:
        groups = (
            self.prompts,
            self.loras,
            self.danbooru,
            self.workflows,
            self.models,
        )
        result: dict[str, dict[str, Any]] = {}
        for group in groups:
            result.update(group.capabilities())
        return result

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": 6,
            "generated_at": _utc_now(),
            "capabilities": self.capabilities(),
            "prompts": self.prompts.snapshot(),
            "loras": self.loras.snapshot(),
            "danbooru": self.danbooru.snapshot(),
            "workflows": self.workflows.snapshot(),
            "models": self.models.snapshot(),
        }


__all__ = [
    "Capability",
    "CapabilityDisabledError",
    "DanbooruApiBuilder",
    "DanbooruStudioService",
    "DanbooruUpdateScheduler",
    "LoraAnalysisPipeline",
    "LoraArchiveService",
    "LoraCatalogService",
    "LoraDownloadService",
    "LoraRecord",
    "LoraStudioService",
    "LoraVisualService",
    "ManualActionRequiredError",
    "ModelQuarantineError",
    "ModelQuarantineService",
    "PromptStudioService",
    "StudioServiceError",
    "StudioServices",
    "WorkflowProfileStudioService",
]
