"""Safe, source-aware LoRA detail aggregation for the v2 archive pipeline.

This module deliberately contains no network or persistence code.  Callers fetch a
fresh, ComfyUI-loadable :class:`LoraRecord` and the relevant LoRA Manager endpoint
responses, then hand those snapshots to :class:`LoraDetailAggregator`.  Only a
small allow-list of fields is retained, so absolute server paths, signed download
URLs and arbitrary image metadata cannot leak into the archive or an LLM prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
import re
from typing import Any, Iterable, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit

from .lora_catalog import LoraRecord
from .lora_semantic import semantic_source_fingerprint


_UNSET = object()
_SOURCE_RECORD = "fresh_record"
_SOURCE_LIST = "manager_list"
_SOURCE_METADATA = "manager_metadata"
_SOURCE_DESCRIPTION = "model_description"
_SOURCE_USAGE = "usage_tips"
_REQUIRED_METADATA_SOURCES = (
    _SOURCE_LIST,
    _SOURCE_METADATA,
    _SOURCE_DESCRIPTION,
)
_HEALTH_VALUES = frozenset({"missing", "partial", "complete", "error", "stale"})

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b", re.IGNORECASE),
    re.compile(r"\bBearer\s+[^\s,;]+", re.IGNORECASE),
    re.compile(
        r"(?i)(token|api[_-]?key|authorization|auth|signature|secret)\s*[:=]\s*"
        r"(?:[\"']?)[^\s,;\"']+"
    ),
    re.compile(
        r"(?i)([?&](?:token|api[_-]?key|authorization|auth|signature|sig|secret)=)"
        r"[^&#\s]+"
    ),
)
_WINDOWS_ABSOLUTE_PATH = re.compile(
    r"(?i)(?<![\w])(?:[A-Z]:[\\/]|\\\\)[^\r\n<>|]+"
)
_UNIX_ABSOLUTE_PATH = re.compile(
    r"(?<![:\w])/(?:home|users|root|mnt|media|opt|srv|var|tmp|astrbot|comfyui)"
    r"(?:/[^\s,;<>]+)+",
    re.IGNORECASE,
)
_SAFE_USAGE_KEYS = frozenset(
    {
        "strength",
        "strength_min",
        "strength_max",
        "strength_range",
        "clip_strength",
        "clip_strength_min",
        "clip_strength_max",
        "clip_skip",
        "steps",
        "cfg",
        "cfg_scale",
        "sampler",
        "scheduler",
        "recommended_width",
        "recommended_height",
        "resolution",
        "notes",
        "trigger_words",
    }
)
_GEN_PARAM_ALIASES = {
    "prompt": "positive_prompt",
    "positiveprompt": "positive_prompt",
    "positive_prompt": "positive_prompt",
    "negativeprompt": "negative_prompt",
    "negative_prompt": "negative_prompt",
    "seed": "seed",
    "steps": "steps",
    "sampler": "sampler",
    "samplername": "sampler",
    "sampler_name": "sampler",
    "scheduler": "scheduler",
    "cfg": "cfg_scale",
    "cfgscale": "cfg_scale",
    "cfg_scale": "cfg_scale",
    "clipskip": "clip_skip",
    "clip_skip": "clip_skip",
    "model": "model",
    "modelname": "model",
    "model_name": "model",
    "vae": "vae",
    "size": "size",
    "width": "width",
    "height": "height",
    "denoisingstrength": "denoising_strength",
    "denoising_strength": "denoising_strength",
    "hiresupscale": "hires_upscale",
    "hires_upscale": "hires_upscale",
    "hiresupscaler": "hires_upscaler",
    "hires_upscaler": "hires_upscaler",
}


class _PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


@dataclass(frozen=True)
class CreatorSummary:
    username: str = ""
    display_name: str = ""
    profile_url: str = ""


@dataclass(frozen=True)
class LicenseSummary:
    name: str = ""
    allow_no_credit: Optional[bool] = None
    allow_commercial_use: tuple[str, ...] = ()
    allow_derivatives: Optional[bool] = None
    allow_different_license: Optional[bool] = None
    flags: Optional[int] = None


@dataclass(frozen=True)
class ImageGenerationSummary:
    source: str
    width: Optional[int] = None
    height: Optional[int] = None
    nsfw_level: Optional[int] = None
    url: str = ""
    parameters: tuple[tuple[str, Any], ...] = ()

    def parameter_dict(self) -> dict[str, Any]:
        return dict(self.parameters)


@dataclass(frozen=True)
class VersionStatus:
    model_id: str = ""
    version_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    published_at: str = ""
    update_available: bool = False
    version_count: Optional[int] = None
    civitai_deleted: bool = False


@dataclass(frozen=True)
class FileStatus:
    loadable: bool = True
    sha256: str = ""
    file_size: Optional[int] = None
    modified: str = ""
    favorite: bool = False
    excluded: bool = False
    usage_count: Optional[int] = None
    from_civitai: bool = False
    hash_status: str = ""
    metadata_source: str = ""
    last_checked_at: str = ""
    skip_metadata_refresh: bool = False


@dataclass(frozen=True)
class FieldProvenance:
    field: str
    sources: tuple[str, ...]


@dataclass(frozen=True)
class MetadataHealth:
    status: str
    available_sources: tuple[str, ...] = ()
    missing_sources: tuple[str, ...] = ()
    error_sources: tuple[str, ...] = ()
    stale_sources: tuple[str, ...] = ()
    checked_at: str = ""

    def __post_init__(self) -> None:
        if self.status not in _HEALTH_VALUES:
            raise ValueError(f"unsupported metadata health: {self.status}")


@dataclass(frozen=True)
class LoraDetailV2:
    """Normalized LoRA dossier with no raw endpoint payloads or local paths."""

    asset_id: str
    name: str
    file_name: str
    source_fingerprint: str = ""
    folder: str = ""
    model_name: str = ""
    version_name: str = ""
    base_model: str = ""
    model_type: str = ""
    sub_type: str = ""
    model_description: str = ""
    version_description: str = ""
    local_notes: str = ""
    trigger_words: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    category: str = "unknown"
    aliases: tuple[str, ...] = ()
    character_name: str = ""
    source_work: str = ""
    preview_url: str = ""
    creator: CreatorSummary = field(default_factory=CreatorSummary)
    license: LicenseSummary = field(default_factory=LicenseSummary)
    images: tuple[ImageGenerationSummary, ...] = ()
    usage_tips: tuple[tuple[str, Any], ...] = ()
    version_status: VersionStatus = field(default_factory=VersionStatus)
    file_status: FileStatus = field(default_factory=FileStatus)
    provenance: tuple[FieldProvenance, ...] = ()
    metadata_health: MetadataHealth = field(
        default_factory=lambda: MetadataHealth(status="missing")
    )

    def usage_tips_dict(self) -> dict[str, Any]:
        return dict(self.usage_tips)

    def provenance_dict(self) -> dict[str, tuple[str, ...]]:
        return {entry.field: entry.sources for entry in self.provenance}

    def to_public_dict(self) -> dict[str, Any]:
        """Return the full safe dossier for the authenticated LAN WebUI."""
        return {
            "schema_version": 2,
            "asset_id": self.asset_id,
            "name": self.name,
            "file_name": self.file_name,
            "folder": self.folder,
            "model_name": self.model_name,
            "version_name": self.version_name,
            "base_model": self.base_model,
            "model_type": self.model_type,
            "sub_type": self.sub_type,
            "descriptions": {
                "model": self.model_description,
                "version": self.version_description,
                "local_notes": self.local_notes,
            },
            "trigger_words": list(self.trigger_words),
            "tags": list(self.tags),
            "category": self.category,
            "aliases": list(self.aliases),
            "character_name": self.character_name,
            "source_work": self.source_work,
            "preview_url": self.preview_url,
            "creator": {
                "username": self.creator.username,
                "display_name": self.creator.display_name,
                "profile_url": self.creator.profile_url,
            },
            "license": {
                "name": self.license.name,
                "allow_no_credit": self.license.allow_no_credit,
                "allow_commercial_use": list(self.license.allow_commercial_use),
                "allow_derivatives": self.license.allow_derivatives,
                "allow_different_license": self.license.allow_different_license,
                "flags": self.license.flags,
            },
            "example_images": [
                {
                    "source": image.source,
                    "width": image.width,
                    "height": image.height,
                    "nsfw_level": image.nsfw_level,
                    "url": image.url,
                    "generation_parameters": image.parameter_dict(),
                }
                for image in self.images
            ],
            "usage_tips": self.usage_tips_dict(),
            "version_status": {
                "model_id": self.version_status.model_id,
                "version_id": self.version_status.version_id,
                "created_at": self.version_status.created_at,
                "updated_at": self.version_status.updated_at,
                "published_at": self.version_status.published_at,
                "update_available": self.version_status.update_available,
                "version_count": self.version_status.version_count,
                "civitai_deleted": self.version_status.civitai_deleted,
            },
            "file_status": {
                "loadable": self.file_status.loadable,
                "sha256": self.file_status.sha256,
                "file_size": self.file_status.file_size,
                "modified": self.file_status.modified,
                "favorite": self.file_status.favorite,
                "excluded": self.file_status.excluded,
                "usage_count": self.file_status.usage_count,
                "from_civitai": self.file_status.from_civitai,
                "hash_status": self.file_status.hash_status,
                "metadata_source": self.file_status.metadata_source,
                "last_checked_at": self.file_status.last_checked_at,
                "skip_metadata_refresh": self.file_status.skip_metadata_refresh,
            },
            "metadata_health": {
                "status": self.metadata_health.status,
                "available_sources": list(self.metadata_health.available_sources),
                "missing_sources": list(self.metadata_health.missing_sources),
                "error_sources": list(self.metadata_health.error_sources),
                "stale_sources": list(self.metadata_health.stale_sources),
                "checked_at": self.metadata_health.checked_at,
            },
            "provenance": {
                field: list(sources)
                for field, sources in self.provenance_dict().items()
            },
        }

    def to_llm_payload(
        self,
        *,
        max_images: int = 4,
        max_description_chars: int = 6000,
        max_prompt_chars: int = 2400,
    ) -> dict[str, Any]:
        """Return a bounded, JSON-safe semantic dossier for classification.

        Network URLs, the standalone file-status hash, local paths and endpoint
        error messages are intentionally excluded.  ``asset_id`` remains as the
        immutable correlation handle the model must return.  Image metadata was
        allow-listed during aggregation and is bounded again here to control
        token use.
        """

        image_payload: list[dict[str, Any]] = []
        for image in self.images[: max(0, max_images)]:
            params: dict[str, Any] = {}
            for key, value in image.parameters:
                if key in {"positive_prompt", "negative_prompt"}:
                    params[key] = _bounded_text(value, max_prompt_chars)
                else:
                    params[key] = value
            image_payload.append(
                {
                    "source": image.source,
                    "width": image.width,
                    "height": image.height,
                    "nsfw_level": image.nsfw_level,
                    "generation_parameters": params,
                }
            )

        provenance = {
            entry.field: list(entry.sources) for entry in self.provenance
        }
        return {
            "schema_version": 2,
            "asset_id": self.asset_id,
            "identity": {
                "lora_name": self.name,
                "file_name": self.file_name,
                "model_name": self.model_name,
                "version_name": self.version_name,
                "base_model": self.base_model,
                "model_type": self.model_type,
                "sub_type": self.sub_type,
            },
            "existing_semantics": {
                "category": self.category,
                "aliases": list(self.aliases[:100]),
                "character_name": self.character_name,
                "source_work": self.source_work,
            },
            "descriptions": {
                "model": _bounded_text(
                    self.model_description, max_description_chars
                ),
                "version": _bounded_text(
                    self.version_description, max_description_chars
                ),
                "local_notes": _bounded_text(self.local_notes, 3000),
            },
            "trigger_words": list(self.trigger_words[:100]),
            "tags": list(self.tags[:150]),
            "creator": {
                "username": self.creator.username,
                "display_name": self.creator.display_name,
            },
            "license": {
                "name": self.license.name,
                "allow_no_credit": self.license.allow_no_credit,
                "allow_commercial_use": list(self.license.allow_commercial_use),
                "allow_derivatives": self.license.allow_derivatives,
                "allow_different_license": self.license.allow_different_license,
            },
            "usage_tips": self.usage_tips_dict(),
            "example_images": image_payload,
            "version_status": {
                "model_id": self.version_status.model_id,
                "version_id": self.version_status.version_id,
                "created_at": self.version_status.created_at,
                "updated_at": self.version_status.updated_at,
                "published_at": self.version_status.published_at,
                "update_available": self.version_status.update_available,
                "version_count": self.version_status.version_count,
                "civitai_deleted": self.version_status.civitai_deleted,
            },
            "file_status": {
                "loadable": self.file_status.loadable,
                "favorite": self.file_status.favorite,
                "excluded": self.file_status.excluded,
                "from_civitai": self.file_status.from_civitai,
                "usage_count": self.file_status.usage_count,
            },
            "metadata_health": {
                "status": self.metadata_health.status,
                "available_sources": list(
                    self.metadata_health.available_sources
                ),
                "missing_sources": list(self.metadata_health.missing_sources),
                "stale_sources": list(self.metadata_health.stale_sources),
                "error_sources": list(self.metadata_health.error_sources),
            },
            "provenance": provenance,
        }


class LoraDetailAggregator:
    """Merge fresh inventory and allow-listed LoRA Manager endpoint data."""

    @classmethod
    def aggregate(
        cls,
        record: LoraRecord,
        *,
        manager_list: Any = _UNSET,
        manager_metadata: Any = _UNSET,
        model_description: Any = _UNSET,
        usage_tips: Any = _UNSET,
        source_errors: Optional[Mapping[str, Any]] = None,
        stale_sources: Iterable[str] = (),
        checked_at: Optional[datetime] = None,
        source_fetched_at: Optional[Mapping[str, Any]] = None,
        stale_after_seconds: Optional[float] = None,
    ) -> LoraDetailV2:
        now = checked_at or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)

        list_item, list_available, list_failed = cls._unwrap_list_item(
            manager_list, record
        )
        metadata, metadata_available, metadata_failed = cls._unwrap_endpoint(
            manager_metadata, "metadata"
        )
        description_value, description_available, description_failed = (
            cls._unwrap_description(model_description)
        )
        usage_value, usage_available, usage_failed = cls._unwrap_usage(usage_tips)

        available = {_SOURCE_RECORD}
        if list_available:
            available.add(_SOURCE_LIST)
        if metadata_available:
            available.add(_SOURCE_METADATA)
        if description_available:
            available.add(_SOURCE_DESCRIPTION)
        if usage_available:
            available.add(_SOURCE_USAGE)

        errors = {
            source
            for source, failed in (
                (_SOURCE_LIST, list_failed),
                (_SOURCE_METADATA, metadata_failed),
                (_SOURCE_DESCRIPTION, description_failed),
                (_SOURCE_USAGE, usage_failed),
            )
            if failed
        }
        errors.update(str(key) for key in (source_errors or {}) if str(key))

        stale = {str(value) for value in stale_sources if str(value)}
        if source_fetched_at and stale_after_seconds is not None:
            for source, value in source_fetched_at.items():
                observed = _parse_datetime(value)
                if observed is not None:
                    age = (now - observed).total_seconds()
                    if age > max(0.0, float(stale_after_seconds)):
                        stale.add(str(source))

        nested_model = _mapping(metadata.get("model"))
        list_civitai = _mapping(list_item.get("civitai"))

        provenance: dict[str, list[str]] = {}

        def mark(field_name: str, source: str, value: Any) -> None:
            if _has_value(value):
                provenance.setdefault(field_name, []).append(source)

        safe_name = _safe_relative_name(record.name)
        list_file_name = _safe_file_name(list_item.get("file_name"))
        # The fresh ComfyUI-loadable record is the authoritative identity.
        # Some Manager versions expose display names without the file extension;
        # using that value here would break exact selection after a safe refresh.
        file_name = _safe_file_name(safe_name) or list_file_name
        folder = _safe_folder(record.folder)
        if not folder and "/" in safe_name:
            folder = _safe_folder(safe_name.rsplit("/", 1)[0])
        if not safe_name and folder and file_name:
            safe_name = f"{folder}/{file_name}"
        elif not safe_name and file_name:
            safe_name = file_name

        raw_hash = _clean_hash(list_item.get("sha256")) or _clean_hash(record.sha256)
        if raw_hash:
            asset_id = f"sha256:{raw_hash}"
        else:
            asset_id = "catalog:" + sha256(
                safe_name.casefold().encode("utf-8", errors="replace")
            ).hexdigest()

        model_name, model_name_source = _pick_text(
            (nested_model.get("name"), _SOURCE_METADATA),
            (list_item.get("model_name"), _SOURCE_LIST),
            (record.model_name, _SOURCE_RECORD),
        )
        mark("model_name", model_name_source, model_name)
        version_name, version_name_source = _pick_text(
            (metadata.get("name"), _SOURCE_METADATA),
            (list_civitai.get("name"), _SOURCE_LIST),
        )
        mark("version_name", version_name_source, version_name)
        base_model, base_model_source = _pick_text(
            (metadata.get("baseModel"), _SOURCE_METADATA),
            (list_item.get("base_model"), _SOURCE_LIST),
            (record.base_model, _SOURCE_RECORD),
        )
        mark("base_model", base_model_source, base_model)
        model_type, model_type_source = _pick_text(
            (nested_model.get("type"), _SOURCE_METADATA),
            (list_item.get("model_type"), _SOURCE_LIST),
        )
        mark("model_type", model_type_source, model_type)
        sub_type, sub_type_source = _pick_text(
            (list_item.get("sub_type"), _SOURCE_LIST),
            (metadata.get("subType"), _SOURCE_METADATA),
        )
        mark("sub_type", sub_type_source, sub_type)

        model_descriptions: list[str] = []
        if description_available:
            clean = _clean_text(description_value, 12000)
            if clean:
                model_descriptions.append(clean)
                mark("model_description", _SOURCE_DESCRIPTION, clean)
        nested_model_description = _clean_text(nested_model.get("description"), 12000)
        if nested_model_description and nested_model_description not in model_descriptions:
            model_descriptions.append(nested_model_description)
            mark("model_description", _SOURCE_METADATA, nested_model_description)
        model_description_text = "\n\n".join(model_descriptions)
        version_description = _clean_text(metadata.get("description"), 12000)
        mark("version_description", _SOURCE_METADATA, version_description)
        local_notes = _clean_text(list_item.get("notes"), 6000)
        mark("local_notes", _SOURCE_LIST, local_notes)

        triggers = _merge_terms(
            (_SOURCE_RECORD, record.trigger_words),
            (_SOURCE_LIST, list_item.get("trigger_words")),
            (_SOURCE_LIST, list_item.get("trained_words")),
            (_SOURCE_LIST, list_civitai.get("trainedWords")),
            (_SOURCE_METADATA, metadata.get("trainedWords")),
        )
        for source, value in (
            (_SOURCE_RECORD, record.trigger_words),
            (_SOURCE_LIST, list_item.get("trigger_words")),
            (_SOURCE_LIST, list_item.get("trained_words")),
            (_SOURCE_LIST, list_civitai.get("trainedWords")),
            (_SOURCE_METADATA, metadata.get("trainedWords")),
        ):
            mark("trigger_words", source, value)

        tags = _merge_terms(
            (_SOURCE_RECORD, record.tags),
            (_SOURCE_LIST, list_item.get("tags")),
            (_SOURCE_LIST, list_item.get("auto_tags")),
            (_SOURCE_METADATA, nested_model.get("tags")),
        )
        for source, value in (
            (_SOURCE_RECORD, record.tags),
            (_SOURCE_LIST, list_item.get("tags")),
            (_SOURCE_LIST, list_item.get("auto_tags")),
            (_SOURCE_METADATA, nested_model.get("tags")),
        ):
            mark("tags", source, value)

        metadata_creator = _mapping(metadata.get("creator"))
        model_creator = _mapping(nested_model.get("creator"))
        creator_data = {**model_creator, **metadata_creator}
        creator = CreatorSummary(
            username=_clean_text(
                creator_data.get("username") or creator_data.get("userName"), 160
            ),
            display_name=_clean_text(
                creator_data.get("name") or creator_data.get("displayName"), 160
            ),
            profile_url=_safe_url(
                creator_data.get("url") or creator_data.get("profileUrl")
            ),
        )
        mark("creator", _SOURCE_METADATA, creator_data)

        license_data = _mapping(nested_model.get("license"))
        commercial = (
            nested_model.get("allowCommercialUse")
            if "allowCommercialUse" in nested_model
            else license_data.get("allowCommercialUse")
        )
        flags = _optional_int(list_item.get("license_flags"))
        license_summary = LicenseSummary(
            name=_clean_text(
                license_data.get("name") or nested_model.get("licenseName"), 200
            ),
            allow_no_credit=_optional_bool(
                nested_model.get("allowNoCredit", license_data.get("allowNoCredit"))
            ),
            allow_commercial_use=_string_tuple(commercial, limit=20),
            allow_derivatives=_optional_bool(
                nested_model.get(
                    "allowDerivatives", license_data.get("allowDerivatives")
                )
            ),
            allow_different_license=_optional_bool(
                nested_model.get(
                    "allowDifferentLicense",
                    license_data.get("allowDifferentLicense"),
                )
            ),
            flags=flags,
        )
        mark("license", _SOURCE_METADATA, nested_model.get("allowNoCredit"))
        mark("license", _SOURCE_METADATA, commercial)
        mark("license", _SOURCE_LIST, flags)

        images = cls._image_summaries(metadata)
        mark("images", _SOURCE_METADATA, images)

        usage_source = usage_value
        if usage_tips is _UNSET:
            usage_source = list_item.get("usage_tips")
            if _has_value(usage_source):
                mark("usage_tips", _SOURCE_LIST, usage_source)
        else:
            mark("usage_tips", _SOURCE_USAGE, usage_source)
        safe_usage = _safe_usage_tips(usage_source)

        model_id = _clean_id(metadata.get("modelId") or list_civitai.get("modelId"))
        version_id = _clean_id(metadata.get("id") or list_civitai.get("id"))
        version_status = VersionStatus(
            model_id=model_id,
            version_id=version_id,
            created_at=_clean_timestamp(metadata.get("createdAt")),
            updated_at=_clean_timestamp(metadata.get("updatedAt")),
            published_at=_clean_timestamp(metadata.get("publishedAt")),
            update_available=bool(list_item.get("update_available", False)),
            version_count=_optional_int(list_item.get("version_count")),
            civitai_deleted=bool(list_item.get("civitai_deleted", False)),
        )
        mark("version_status", _SOURCE_METADATA, (model_id, version_id))
        mark("version_status", _SOURCE_LIST, list_item.get("update_available"))

        modified = _clean_timestamp(list_item.get("modified"))
        file_status = FileStatus(
            loadable=True,
            sha256=raw_hash,
            file_size=_optional_int(
                list_item.get("file_size", list_item.get("size"))
            ),
            modified=modified,
            favorite=bool(list_item.get("favorite", record.favorite)),
            excluded=bool(list_item.get("exclude", False)),
            usage_count=_optional_int(list_item.get("usage_count")),
            from_civitai=bool(
                list_item.get("from_civitai", record.from_civitai)
                or metadata
            ),
            hash_status=_clean_text(list_item.get("hash_status"), 80),
            metadata_source=_clean_text(list_item.get("metadata_source"), 120),
            last_checked_at=_clean_timestamp(list_item.get("last_checked_at")),
            skip_metadata_refresh=bool(
                list_item.get("skip_metadata_refresh", False)
            ),
        )
        mark("file_status", _SOURCE_RECORD, True)
        mark("file_status", _SOURCE_LIST, list_item)

        missing = tuple(
            source for source in _REQUIRED_METADATA_SOURCES if source not in available
        )
        if errors:
            health_status = "error"
        elif stale:
            health_status = "stale"
        elif not ({_SOURCE_LIST, _SOURCE_METADATA, _SOURCE_DESCRIPTION} & available):
            health_status = "missing"
        elif not missing:
            health_status = "complete"
        else:
            health_status = "partial"
        health = MetadataHealth(
            status=health_status,
            available_sources=tuple(sorted(available)),
            missing_sources=missing,
            error_sources=tuple(sorted(errors)),
            stale_sources=tuple(sorted(stale)),
            checked_at=now.isoformat().replace("+00:00", "Z"),
        )

        preview_url = _safe_url(list_item.get("preview_url") or record.preview_url)
        return LoraDetailV2(
            asset_id=asset_id,
            name=safe_name,
            file_name=file_name,
            source_fingerprint=semantic_source_fingerprint(record),
            folder=folder,
            model_name=model_name,
            version_name=version_name,
            base_model=base_model,
            model_type=model_type,
            sub_type=sub_type,
            model_description=model_description_text,
            version_description=version_description,
            local_notes=local_notes,
            trigger_words=triggers,
            tags=tags,
            category=_clean_text(record.category, 80) or "unknown",
            aliases=_merge_plain_terms(record.aliases, limit=100),
            character_name=_clean_text(record.character_name, 240),
            source_work=_clean_text(record.source_work, 240),
            preview_url=preview_url,
            creator=creator,
            license=license_summary,
            images=images,
            usage_tips=tuple(safe_usage.items()),
            version_status=version_status,
            file_status=file_status,
            provenance=tuple(
                FieldProvenance(field_name, tuple(dict.fromkeys(sources)))
                for field_name, sources in sorted(provenance.items())
            ),
            metadata_health=health,
        )

    @classmethod
    def _unwrap_list_item(
        cls, payload: Any, record: LoraRecord
    ) -> tuple[dict[str, Any], bool, bool]:
        if payload is _UNSET or payload is None:
            return {}, False, False
        if not isinstance(payload, Mapping):
            return {}, False, True
        if payload.get("success") is False:
            return {}, False, bool(payload.get("error", True))
        items = payload.get("items")
        if isinstance(items, list):
            candidates = [dict(item) for item in items if isinstance(item, Mapping)]
            if not candidates:
                return {}, False, False
            wanted_hash = _clean_hash(record.sha256)
            wanted_name = _safe_file_name(record.name).casefold()
            for item in candidates:
                if wanted_hash and _clean_hash(item.get("sha256")) == wanted_hash:
                    return item, True, False
            for item in candidates:
                if _safe_file_name(item.get("file_name")).casefold() == wanted_name:
                    return item, True, False
            return {}, False, False
        item = dict(payload)
        return (item, bool(item), False)

    @staticmethod
    def _unwrap_endpoint(
        payload: Any, key: str
    ) -> tuple[dict[str, Any], bool, bool]:
        if payload is _UNSET or payload is None:
            return {}, False, False
        if not isinstance(payload, Mapping):
            return {}, False, True
        if payload.get("success") is False:
            return {}, False, bool(payload.get("error", True))
        if key in payload:
            value = payload.get(key)
            if isinstance(value, Mapping):
                return dict(value), True, False
            return {}, False, value is not None
        return dict(payload), bool(payload), False

    @staticmethod
    def _unwrap_description(payload: Any) -> tuple[str, bool, bool]:
        if payload is _UNSET or payload is None:
            return "", False, False
        if isinstance(payload, str):
            return payload, True, False
        if not isinstance(payload, Mapping):
            return "", False, True
        if payload.get("success") is False:
            return "", False, bool(payload.get("error", True))
        if "description" in payload:
            value = payload.get("description")
            return str(value or ""), True, False
        return "", False, False

    @staticmethod
    def _unwrap_usage(payload: Any) -> tuple[Any, bool, bool]:
        if payload is _UNSET or payload is None:
            return {}, False, False
        if isinstance(payload, (str, Mapping)):
            if isinstance(payload, Mapping) and payload.get("success") is False:
                return {}, False, bool(payload.get("error", True))
            if isinstance(payload, Mapping) and "usage_tips" in payload:
                return payload.get("usage_tips"), True, False
            return payload, True, False
        return {}, False, True

    @staticmethod
    def _image_summaries(metadata: Mapping[str, Any]) -> tuple[ImageGenerationSummary, ...]:
        result: list[ImageGenerationSummary] = []
        for key, source in (("images", "civitai"), ("customImages", "custom")):
            values = metadata.get(key)
            if not isinstance(values, list):
                continue
            for value in values:
                if not isinstance(value, Mapping):
                    continue
                width = _optional_int(value.get("width"))
                height = _optional_int(value.get("height"))
                nsfw = _optional_int(
                    value.get("nsfwLevel", value.get("nsfw_level"))
                )
                raw_meta: Mapping[str, Any] = {}
                for meta_key in (
                    "meta",
                    "metadata",
                    "generation_params",
                    "generationParams",
                ):
                    candidate = value.get(meta_key)
                    if isinstance(candidate, Mapping):
                        raw_meta = candidate
                        break
                parameters = _safe_generation_parameters(raw_meta)
                result.append(
                    ImageGenerationSummary(
                        source=source,
                        width=width,
                        height=height,
                        nsfw_level=nsfw,
                        url=_safe_url(value.get("url")),
                        parameters=tuple(parameters.items()),
                    )
                )
                if len(result) >= 8:
                    return tuple(result)
        return tuple(result)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (Mapping, list, tuple, set)):
        return bool(value)
    return True


def _bounded_text(value: Any, limit: int) -> str:
    text = _clean_text(value, max(0, limit))
    return text


def _clean_text(value: Any, limit: int = 12000) -> str:
    text = str(value or "")
    if "<" in text and ">" in text:
        parser = _PlainTextParser()
        try:
            parser.feed(text)
            parser.close()
            text = parser.text()
        except Exception:
            text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    text = _WINDOWS_ABSOLUTE_PATH.sub("[local-path]", text)
    text = _UNIX_ABSOLUTE_PATH.sub("[local-path]", text)
    if limit >= 0 and len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def _safe_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return ""
    host = parsed.hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme.lower(), host, parsed.path, "", ""))


def _safe_relative_name(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    text = text.split("?", 1)[0].split("#", 1)[0]
    if re.match(r"^[A-Za-z]:/", text) or text.startswith("/") or text.startswith("//"):
        text = text.rstrip("/").rsplit("/", 1)[-1]
    parts = [part for part in text.split("/") if part not in {"", ".", ".."}]
    return "/".join(_clean_text(part, 255) for part in parts[-8:])


def _safe_file_name(value: Any) -> str:
    return _safe_relative_name(value).rsplit("/", 1)[-1]


def _safe_folder(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", text) or text.startswith("/") or text.startswith("//"):
        return ""
    parts = [part for part in text.split("/") if part not in {"", ".", ".."}]
    return "/".join(_clean_text(part, 120) for part in parts[-7:])


def _clean_hash(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if re.fullmatch(r"[0-9a-f]{64}", text) else ""


def _clean_id(value: Any) -> str:
    text = str(value or "").strip()
    return text if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", text) else ""


def _clean_timestamp(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), timezone.utc).isoformat().replace(
                "+00:00", "Z"
            )
        except (OverflowError, OSError, ValueError):
            return ""
    text = _clean_text(value, 80)
    return text if re.fullmatch(r"[0-9T: .+\-Z]+", text) else ""


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, (int, float)):
        try:
            result = datetime.fromtimestamp(float(value), timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            result = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _optional_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any) -> Optional[bool]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().casefold()
    if text in {"true", "yes", "1", "on"}:
        return True
    if text in {"false", "no", "0", "off"}:
        return False
    return None


def _pick_text(*candidates: tuple[Any, str]) -> tuple[str, str]:
    for value, source in candidates:
        text = _clean_text(value, 2000)
        if text:
            return text, source
    return "", ""


def _string_tuple(value: Any, *, limit: int = 100) -> tuple[str, ...]:
    if isinstance(value, str):
        values: Iterable[Any] = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        return ()
    result: list[str] = []
    seen: set[str] = set()
    for part in values:
        if isinstance(part, Mapping):
            part = part.get("name") or part.get("tag") or ""
        text = _clean_text(part, 500)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
            if len(result) >= limit:
                break
    return tuple(result)


def _merge_plain_terms(*values: Any, limit: int = 200) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        for text in _string_tuple(value, limit=limit):
            key = text.casefold()
            if key not in seen:
                seen.add(key)
                result.append(text)
                if len(result) >= limit:
                    return tuple(result)
    return tuple(result)


def _merge_terms(*sources: tuple[str, Any]) -> tuple[str, ...]:
    return _merge_plain_terms(*(value for _, value in sources), limit=200)


def _normalize_key(value: Any) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(value or ""))
    return re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")


def _safe_usage_tips(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        text = value.strip()
        if not text or text in {"{}", "[]", "null"}:
            return {}
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return {}
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        key = _normalize_key(raw_key)
        if key == "clipstrength":
            key = "clip_strength"
        if key not in _SAFE_USAGE_KEYS:
            continue
        if key == "trigger_words":
            result[key] = list(_string_tuple(raw_value, limit=50))
        elif isinstance(raw_value, bool):
            result[key] = raw_value
        elif isinstance(raw_value, (int, float)):
            result[key] = raw_value
        elif isinstance(raw_value, str):
            result[key] = _clean_text(raw_value, 1000)
    return result


def _safe_generation_parameters(value: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        compact_key = re.sub(r"[^A-Za-z0-9_]", "", str(raw_key or ""))
        normalized = _normalize_key(raw_key)
        key = _GEN_PARAM_ALIASES.get(compact_key.casefold()) or _GEN_PARAM_ALIASES.get(
            normalized
        )
        if not key:
            continue
        if isinstance(raw_value, bool):
            result[key] = raw_value
        elif isinstance(raw_value, (int, float)):
            result[key] = raw_value
        elif isinstance(raw_value, str):
            limit = 4000 if key in {"positive_prompt", "negative_prompt"} else 500
            result[key] = _clean_text(raw_value, limit)
    return result


__all__ = [
    "CreatorSummary",
    "FieldProvenance",
    "FileStatus",
    "ImageGenerationSummary",
    "LicenseSummary",
    "LoraDetailAggregator",
    "LoraDetailV2",
    "MetadataHealth",
    "VersionStatus",
]
