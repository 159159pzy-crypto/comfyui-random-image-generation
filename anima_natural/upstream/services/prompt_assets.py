"""Local visual prompt-asset catalogue with atomic SQLite imports.

The plugin intentionally ships without third-party prompt data. Administrators can
import a reviewed JSON/CSV catalogue into a caller-provided database path. Imported
records, custom records and favourites all share stable asset IDs, while provenance
remains explicit and secret-free.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import csv
import errno
import hashlib
import io
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence
import unicodedata
from urllib.parse import unquote, urlsplit, urlunsplit
import uuid

import aiohttp


ASSET_TYPES = frozenset({"artist", "character", "clothing", "background", "pose"})
DEFAULT_MAX_IMPORT_BYTES = 16 * 1024 * 1024
MAX_IMPORT_BYTES = 128 * 1024 * 1024
MAX_REMOTE_IMPORT_BYTES = 16 * 1024 * 1024
MAX_IMPORT_RECORDS = 100_000
MAX_LIBRARY_TEXT_BYTES = 256 * 1024 * 1024
MAX_IMPORT_FIELDS = 64
MAX_NAME_LENGTH = 256
MAX_LIST_ITEMS = 128
MAX_LIST_ITEM_LENGTH = 256
MAX_QUERY_LENGTH = 256
MAX_PAGE = 1_000_000
MAX_PAGE_SIZE = 200
MAX_REMOTE_TIMEOUT = 300.0
MAX_PROVENANCE_JSON_LENGTH = 8192
MAX_URL_LENGTH = 2048
_SCHEMA_VERSION = "1"
_ASSET_ID = re.compile(r"^pa_[0-9a-f]{32}$")
_LIST_SEPARATOR = re.compile(r"[|;,]")
_SPACE = re.compile(r"\s+")
_SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(?:access[_-]?key(?:[_-]?id)?|access[_-]?token|"
    r"api[_-]?key|auth(?:entication)?|authorization|bearer|"
    r"client[_-]?secret|cookie|credentials?|password|passwd|private[_-]?key|"
    r"refresh[_-]?token|secret|session|signature|token)(?:$|[_-])",
    re.IGNORECASE,
)
_SENSITIVE_COMPACT_KEY = re.compile(
    r"(?:accesskey(?:id)?|accesstoken|apikey|awsaccesskeyid|clientsecret|"
    r"credentials?|privatekey|refreshtoken|securitytoken|sessiontoken|"
    r"xamzcredential|xamzsecuritytoken|xamzsignature)$",
    re.IGNORECASE,
)
_CONTROL_PROTOCOL = re.compile(
    r"<\s*/?\s*(?:comfy|controlnet|edit|function|lora|pic|think|tool|workflow)"
    r"\b(?:\s|:|/|>|$)|\bemit_anima_plan_v\d+\b",
    re.IGNORECASE,
)
_KNOWN_CREDENTIAL_VALUE = re.compile(
    r"(?<![A-Za-z0-9])(?:basic|bearer)\s+[A-Za-z0-9._~+/=-]{8,}|"
    r"(?<![A-Za-z0-9])(?:gh[pousr]_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|(?:AKIA|ASIA)[0-9A-Z]{16}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|glpat-[A-Za-z0-9_-]{12,}|"
    r"npm_[A-Za-z0-9]{20,}|sk_live_[A-Za-z0-9]{12,}|"
    r"sk-[A-Za-z0-9_-]{12,})(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}\."
    r"[A-Za-z0-9_-]{16,}(?![A-Za-z0-9_-])|"
    r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----",
    re.IGNORECASE,
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?<![A-Za-z0-9])(?:access[_-]?key(?:[_-]?id)?|access[_-]?token|"
    r"api[_-]?key|apikey|authorization|awsaccesskeyid|client[_-]?secret|"
    r"cookie|credentials?|password|passwd|private[_-]?key|refresh[_-]?token|"
    r"secret|session(?:[_-]?(?:id|key|token))?|signature|token|"
    r"x-amz-(?:credential|security-token|signature))\s*(?:=|:)\s*"
    r"[\"']?[^\s\"'&;,]{8,}",
    re.IGNORECASE,
)
_OPAQUE_LOCAL_SOURCE = re.compile(r"^local-import:[0-9a-f]{20}$")
_NON_HTTP_URI_SCHEMES = frozenset(
    {
        "azure",
        "data",
        "file",
        "ftp",
        "ftps",
        "gopher",
        "gs",
        "javascript",
        "mongodb",
        "mysql",
        "postgres",
        "postgresql",
        "s3",
        "scp",
        "sftp",
        "ssh",
    }
)
_LOCAL_FILE_SUFFIXES = frozenset(
    {
        ".csv",
        ".db",
        ".json",
        ".jsonl",
        ".sqlite",
        ".sqlite3",
        ".toml",
        ".tsv",
        ".txt",
        ".yaml",
        ".yml",
    }
)
_RECORD_FIELDS = frozenset(
    {
        "alias",
        "aliases",
        "alternate_names",
        "asset_id",
        "asset_type",
        "attributes",
        "categories",
        "category",
        "chinese_name",
        "cn_name",
        "description",
        "en",
        "en_name",
        "english_name",
        "external_id",
        "features",
        "genres",
        "groups",
        "id",
        "image",
        "image_url",
        "kind",
        "name",
        "name_cn",
        "name_en",
        "name_zh",
        "preview",
        "preview_url",
        "prompts",
        "provenance",
        "slug",
        "source_id",
        "tag",
        "tags",
        "thumbnail",
        "thumbnail_url",
        "traits",
        "trigger_words",
        "type",
        "zh",
    }
)
_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCK_STATE = threading.local()
_PROCESS_LOCK_TIMEOUT = 30.0
_PROCESS_LOCK_POLL_INTERVAL = 0.025
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class PromptAssetError(Exception):
    """The prompt-asset operation failed without changing a valid snapshot."""


class PromptAssetValidationError(PromptAssetError):
    """An asset, filter, URL or import payload is invalid."""


class PromptAssetNotFoundError(PromptAssetError):
    """The requested asset does not exist."""


class PromptAssetConflictError(PromptAssetError):
    """An operation conflicts with an existing asset."""


class _PinnedResolver(aiohttp.abc.AbstractResolver):
    """Use only addresses validated before opening the HTTP connection."""

    def __init__(self, hostname: str, addresses: Sequence[IPAddress]):
        self._hostname = str(hostname or "").casefold()
        self._addresses = tuple(addresses)

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: int = socket.AF_INET,
    ) -> list[dict[str, Any]]:
        if str(host or "").casefold() != self._hostname:
            raise OSError("unexpected host requested by pinned resolver")
        results: list[dict[str, Any]] = []
        for address in self._addresses:
            address_family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
            if family not in {socket.AF_UNSPEC, address_family}:
                continue
            results.append(
                {
                    "hostname": host,
                    "host": str(address),
                    "port": port,
                    "family": address_family,
                    "proto": 0,
                    "flags": 0,
                }
            )
        if not results:
            raise OSError("validated host has no address for requested family")
        return results

    async def close(self) -> None:
        return None


def _path_lock(path: Path) -> threading.RLock:
    key = str(path.resolve(strict=False)).casefold()
    with _PATH_LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def normalize_asset_text(value: Any) -> str:
    """Return a stable multilingual key for names, aliases and filters."""

    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    text = text.replace("_", " ").replace("`", "'").replace("’", "'")
    return _SPACE.sub(" ", text)


def stable_asset_id(asset_type: Any, identity: Any, namespace: Any = "") -> str:
    """Derive a stable, opaque ID from a semantic identity and optional namespace."""

    kind = _asset_type(asset_type)
    normalized_identity = normalize_asset_text(identity)
    normalized_namespace = normalize_asset_text(namespace)
    if not normalized_identity:
        raise PromptAssetValidationError("asset identity is required")
    digest = hashlib.sha256(
        f"prompt-asset-v1\0{kind}\0{normalized_namespace}\0{normalized_identity}".encode(
            "utf-8"
        )
    ).hexdigest()
    return f"pa_{digest[:32]}"


def _asset_type(value: Any) -> str:
    text = normalize_asset_text(value).replace(" ", "_")
    aliases = {
        "artists": "artist",
        "characters": "character",
        "clothes": "clothing",
        "costume": "clothing",
        "costumes": "clothing",
        "backgrounds": "background",
        "poses": "pose",
    }
    text = aliases.get(text, text)
    if text not in ASSET_TYPES:
        raise PromptAssetValidationError("unsupported prompt asset type")
    return text


def _clean_text(
    value: Any,
    field: str,
    maximum: int = MAX_NAME_LENGTH,
    *,
    allow_number: bool = False,
) -> str:
    if value is None:
        return ""
    if allow_number and isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            raise PromptAssetValidationError(f"{field} must be finite")
        value = str(value)
    if not isinstance(value, str):
        raise PromptAssetValidationError(f"{field} must be scalar text")
    text = unicodedata.normalize("NFC", str(value)).strip()
    if len(text) > maximum:
        raise PromptAssetValidationError(f"{field} exceeds length limit")
    if any(unicodedata.category(char).startswith("C") for char in text):
        raise PromptAssetValidationError(f"{field} contains control characters")
    if _CONTROL_PROTOCOL.search(text):
        raise PromptAssetValidationError(
            f"{field} cannot contain executable prompt protocols"
        )
    return text


def _reject_embedded_protocols(value: Any, *, depth: int = 0) -> None:
    if depth > 6:
        raise PromptAssetValidationError("prompt asset record is too deeply nested")
    if isinstance(value, str):
        if _CONTROL_PROTOCOL.search(unicodedata.normalize("NFKC", value)):
            raise PromptAssetValidationError(
                "prompt asset cannot contain executable prompt protocols"
            )
        return
    if isinstance(value, Mapping):
        if len(value) > MAX_IMPORT_FIELDS:
            raise PromptAssetValidationError("prompt asset record has too many fields")
        for key, item in value.items():
            _reject_embedded_protocols(str(key), depth=depth + 1)
            _reject_embedded_protocols(item, depth=depth + 1)
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        if len(value) > MAX_LIST_ITEMS:
            raise PromptAssetValidationError(
                "prompt asset nested list has too many items"
            )
        for item in value:
            _reject_embedded_protocols(item, depth=depth + 1)


def _list_values(value: Any, field: str) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                decoded = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise PromptAssetValidationError(
                    f"{field} contains invalid JSON"
                ) from exc
            if not isinstance(decoded, list):
                raise PromptAssetValidationError(f"{field} must be a list")
            values: Iterable[Any] = decoded
        else:
            values = _LIST_SEPARATOR.split(value)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        values = value
    else:
        raise PromptAssetValidationError(f"{field} must be a string or list")

    if len(values) > MAX_LIST_ITEMS:
        raise PromptAssetValidationError(f"{field} has too many items")
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        if not isinstance(raw, str):
            raise PromptAssetValidationError(f"{field} items must be strings")
        item = _clean_text(raw, field, MAX_LIST_ITEM_LENGTH)
        key = normalize_asset_text(item)
        if item and key not in seen:
            seen.add(key)
            result.append(item)
            if len(result) > MAX_LIST_ITEMS:
                raise PromptAssetValidationError(f"{field} has too many items")
    return tuple(result)


def _sensitive_key(value: Any) -> bool:
    raw = unicodedata.normalize("NFKC", str(value or "")).strip()
    raw = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", raw)
    key = re.sub(r"[^a-z0-9]+", "_", raw.casefold()).strip("_")
    compact = key.replace("_", "")
    return bool(_SENSITIVE_KEY.search(key) or _SENSITIVE_COMPACT_KEY.search(compact))


def _contains_embedded_credential(value: Any) -> bool:
    """Recognise credential material without treating ordinary prose as a secret."""

    text = unicodedata.normalize("NFKC", str(value or ""))
    candidates = (text, unquote(text)) if "%" in text else (text,)
    return any(
        _KNOWN_CREDENTIAL_VALUE.search(candidate)
        or _CREDENTIAL_ASSIGNMENT.search(candidate)
        for candidate in candidates
    )


def _reject_embedded_credentials(value: Any, *, depth: int = 0) -> None:
    if depth > 6:
        raise PromptAssetValidationError("prompt asset record is too deeply nested")
    if isinstance(value, str):
        if _contains_embedded_credential(value):
            raise PromptAssetValidationError(
                "prompt asset cannot contain credential material"
            )
        return
    if isinstance(value, Mapping):
        if len(value) > MAX_IMPORT_FIELDS:
            raise PromptAssetValidationError("prompt asset record has too many fields")
        for key, item in value.items():
            if _sensitive_key(key):
                raise PromptAssetValidationError(
                    "prompt asset cannot contain credential fields"
                )
            _reject_embedded_credentials(str(key), depth=depth + 1)
            _reject_embedded_credentials(item, depth=depth + 1)
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        if len(value) > MAX_LIST_ITEMS:
            raise PromptAssetValidationError(
                "prompt asset nested list has too many items"
            )
        for item in value:
            _reject_embedded_credentials(item, depth=depth + 1)


def _looks_like_local_reference(value: str, parsed: Any | None = None) -> bool:
    text = str(value or "").strip()
    candidate = parsed if parsed is not None else urlsplit(text)
    path = str(candidate.path or text)
    if re.match(r"^[A-Za-z]:[\\/]", text):
        return True
    if text.startswith(("/", "\\", "./", ".\\", "../", "..\\", "~/", "~\\")):
        return True
    if "\\" in text:
        return True
    if "/" in path and not any(char.isspace() for char in path):
        return True
    return Path(path).suffix.casefold() in _LOCAL_FILE_SUFFIXES


def _non_http_uri(value: str, parsed: Any) -> bool:
    scheme = str(parsed.scheme or "").casefold()
    if not scheme or scheme in {"http", "https"}:
        return False
    return "://" in value or scheme in _NON_HTTP_URI_SCHEMES


def _opaque_local_source(value: str) -> str:
    normalized = unicodedata.normalize(
        "NFC", os.path.normcase(str(Path(value).resolve(strict=False)))
    )
    digest = hashlib.sha256(
        f"prompt-asset-local-source-v1\0{normalized}".encode("utf-8")
    ).hexdigest()
    return f"local-import:{digest[:20]}"


def _sanitize_provenance(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        raise PromptAssetValidationError("provenance is too deeply nested")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PromptAssetValidationError("provenance contains a non-finite number")
        return value
    if isinstance(value, str):
        text = _clean_text(value, "provenance", 2048)
        if _contains_embedded_credential(text):
            raise PromptAssetValidationError(
                "provenance cannot contain credential values"
            )
        try:
            parsed = urlsplit(text)
        except ValueError as exc:
            raise PromptAssetValidationError("provenance URL is invalid") from exc
        if parsed.scheme and (
            parsed.username is not None or parsed.password is not None
        ):
            raise PromptAssetValidationError(
                "provenance cannot contain credential URLs"
            )
        if parsed.scheme.casefold() in {"http", "https"}:
            return _canonical_remote_source(
                urlsplit(_validate_url(text, "provenance URL"))
            )
        if _non_http_uri(text, parsed) or _looks_like_local_reference(text, parsed):
            raise PromptAssetValidationError(
                "provenance cannot contain local paths or non-HTTP URIs"
            )
        return text
    if isinstance(value, Mapping):
        if len(value) > MAX_IMPORT_FIELDS:
            raise PromptAssetValidationError("provenance has too many fields")
        result: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = _clean_text(raw_key, "provenance key", 128)
            if _sensitive_key(key):
                raise PromptAssetValidationError(
                    "provenance cannot contain credentials"
                )
            result[key] = _sanitize_provenance(item, depth=depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        if len(value) > MAX_LIST_ITEMS:
            raise PromptAssetValidationError("provenance list has too many items")
        return [_sanitize_provenance(item, depth=depth + 1) for item in value]
    raise PromptAssetValidationError("provenance contains unsupported data")


def _provenance(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        result: Any = {}
    elif isinstance(value, str):
        try:
            result = json.loads(value)
        except json.JSONDecodeError as exc:
            raise PromptAssetValidationError(
                "provenance contains invalid JSON"
            ) from exc
    else:
        result = value
    if not isinstance(result, Mapping):
        raise PromptAssetValidationError("provenance must be an object")
    sanitized = _sanitize_provenance(result)
    encoded = json.dumps(
        sanitized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    if len(encoded) > MAX_PROVENANCE_JSON_LENGTH:
        raise PromptAssetValidationError("provenance exceeds length limit")
    return dict(sanitized)


def _validate_url(value: Any, field: str) -> str:
    url = _clean_text(value, field, MAX_URL_LENGTH)
    if not url:
        return ""
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise PromptAssetValidationError(f"{field} is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise PromptAssetValidationError(f"{field} must use HTTP or HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise PromptAssetValidationError(f"{field} cannot contain credentials")
    if parsed.fragment:
        raise PromptAssetValidationError(f"{field} cannot contain a fragment")
    if parsed.query:
        raise PromptAssetValidationError(f"{field} cannot contain a query")
    try:
        port = parsed.port
    except ValueError as exc:
        raise PromptAssetValidationError(f"{field} has an invalid port") from exc
    if port is not None and not (1 <= port <= 65535):
        raise PromptAssetValidationError(f"{field} has an invalid port")
    host = parsed.hostname.casefold().rstrip(".")
    address: IPAddress | None = None
    if host != "localhost":
        try:
            address = ipaddress.ip_address(host.split("%", 1)[0])
        except ValueError:
            address = None
    if address is not None and (
        address.is_link_local
        or address.is_unspecified
        or address.is_multicast
        or address.is_reserved
    ):
        raise PromptAssetValidationError(f"{field} uses a forbidden address")
    if parsed.scheme == "http":
        if host == "localhost":
            return url
        if address is None:
            raise PromptAssetValidationError(
                f"{field} plain HTTP requires a private IP host"
            )
        if not (address.is_private or address.is_loopback):
            raise PromptAssetValidationError(
                f"{field} plain HTTP requires a private IP host"
            )
    return url


def _canonical_remote_source(parsed: Any) -> str:
    path_digest = hashlib.sha256(str(parsed.path or "/").encode("utf-8")).hexdigest()
    return f"remote-{parsed.scheme.casefold()}:{parsed.netloc}:{path_digest[:12]}"


def _source_label(value: Any, *, local: bool = False) -> str:
    source = _clean_text(value, "source", MAX_URL_LENGTH)
    if not source:
        return ""
    if _OPAQUE_LOCAL_SOURCE.fullmatch(source):
        return source
    if _contains_embedded_credential(source):
        raise PromptAssetValidationError("source cannot contain credentials")
    try:
        parsed = urlsplit(source)
    except ValueError as exc:
        raise PromptAssetValidationError("source URL is invalid") from exc
    if parsed.scheme and (parsed.username is not None or parsed.password is not None):
        raise PromptAssetValidationError("source cannot contain credential URLs")
    if parsed.scheme.casefold() in {"http", "https"}:
        return _canonical_remote_source(urlsplit(_validate_url(source, "source")))
    if _non_http_uri(source, parsed):
        raise PromptAssetValidationError("source cannot use a non-HTTP URI")
    if parsed.query or parsed.fragment:
        if not (local or _looks_like_local_reference(source, parsed)):
            raise PromptAssetValidationError("source cannot contain a query or fragment")
    if local or _looks_like_local_reference(source, parsed):
        return _opaque_local_source(source)
    return source


def _preview_reference(value: Any) -> tuple[str, bool]:
    url = _validate_url(value, "preview URL")
    if not url:
        return "", False
    parsed = urlsplit(url)
    canonical = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"pav_{digest[:32]}", True


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _reject_json_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def _reject_duplicate_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PromptAssetValidationError(
                "prompt asset JSON contains a duplicate object key"
            )
        result[key] = value
    return result


def _decode_json_list(value: Any) -> tuple[str, ...]:
    try:
        decoded = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return ()
    if not isinstance(decoded, list):
        return ()
    return tuple(str(item) for item in decoded)


def _decode_json_object(value: Any) -> dict[str, Any]:
    try:
        decoded = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(decoded) if isinstance(decoded, Mapping) else {}


def _token_blob(values: Sequence[str]) -> str:
    return "\n" + "\n".join(normalize_asset_text(item) for item in values) + "\n"


def _search_blob(parts: Iterable[Any]) -> str:
    values = [normalize_asset_text(item) for item in parts if str(item or "").strip()]
    return "\n".join(dict.fromkeys(values))


def _flatten_search_values(value: Any, *, depth: int = 0) -> Iterable[str]:
    if depth > 6:
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from _flatten_search_values(item, depth=depth + 1)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            yield from _flatten_search_values(item, depth=depth + 1)
    elif value is not None:
        yield str(value)


def _provenance_search_blob(value: Any) -> str:
    decoded = _decode_json_object(value) if isinstance(value, str) else value
    return _search_blob(_flatten_search_values(decoded))


def _word_boundary_contains(text: str, query: str) -> bool:
    start = 0
    while True:
        index = text.find(query, start)
        if index < 0:
            return False
        if index == 0 or not text[index - 1].isalnum():
            return True
        start = index + 1


def _relevance_score(
    name_zh: Any,
    name_en: Any,
    aliases_json: Any,
    tags_json: Any,
    traits_json: Any,
    categories_json: Any,
    provenance_json: Any,
    search_text: Any,
    query: Any,
) -> int:
    normalized_query = normalize_asset_text(query)
    if not normalized_query:
        return 0
    primary = tuple(
        value
        for value in (
            normalize_asset_text(name_zh),
            normalize_asset_text(name_en),
        )
        if value
    )
    aliases = tuple(normalize_asset_text(item) for item in _decode_json_list(aliases_json))
    semantic = tuple(
        normalize_asset_text(item)
        for raw in (tags_json, traits_json, categories_json)
        for item in _decode_json_list(raw)
    )
    provenance = _provenance_search_blob(provenance_json)
    general = normalize_asset_text(search_text)

    def rank(needle: str) -> int:
        if any(value == needle for value in primary):
            return 600
        if any(value == needle for value in aliases):
            return 590
        if any(value.startswith(needle) for value in primary):
            return 520
        if any(value.startswith(needle) for value in aliases):
            return 510
        if any(_word_boundary_contains(value, needle) for value in primary):
            return 500
        if any(_word_boundary_contains(value, needle) for value in aliases):
            return 490
        if any(value == needle for value in semantic):
            return 400
        if any(value.startswith(needle) for value in semantic):
            return 390
        if any(_word_boundary_contains(value, needle) for value in semantic):
            return 380
        if any(needle in value for value in semantic):
            return 370
        if needle in provenance:
            return 200
        if needle in general:
            return 100
        return 0

    phrase_rank = rank(normalized_query)
    terms = tuple(term for term in normalized_query.split(" ") if term)
    term_ranks = tuple(rank(term) for term in terms)
    if phrase_rank:
        return phrase_rank * 1000 + 999
    if term_ranks and all(term_ranks):
        return min(term_ranks) * 1000 + min(sum(term_ranks), 998)
    return 0


def _provenance_contains(provenance_json: Any, query: Any) -> int:
    normalized_query = normalize_asset_text(query)
    return int(bool(normalized_query and normalized_query in _provenance_search_blob(provenance_json)))


def _record_text_size(record: Mapping[str, Any]) -> int:
    values = (
        record.get("name_zh", ""),
        record.get("name_en", ""),
        _json_text(list(record.get("aliases") or ())),
        _json_text(list(record.get("tags") or ())),
        _json_text(list(record.get("traits") or ())),
        _json_text(list(record.get("categories") or ())),
        record.get("preview_key", ""),
        _json_text(record.get("provenance") or {}),
        record.get("source_key", ""),
        record.get("search_text", ""),
        record.get("category_tokens", ""),
        record.get("trait_tokens", ""),
        record.get("tag_tokens", ""),
    )
    return sum(len(str(value).encode("utf-8")) for value in values)


def _like_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
    return f"%{escaped}%"


def _fsync_directory(path: Path) -> bool:
    if os.name == "nt":
        return True
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        # The atomic replace has already committed. Some filesystems do not
        # support directory fsync, so this is a durability warning rather than
        # a false transactional rollback signal.
        return False
    return True


@contextmanager
def _process_file_lock(
    database_path: Path,
    *,
    shared: bool = False,
    timeout: float = _PROCESS_LOCK_TIMEOUT,
) -> Iterable[None]:
    """Acquire a re-entrant process lock with cross-platform reader parity."""

    database_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = database_path.parent / f".{database_path.name}.lock"
    lock_key = str(lock_path.resolve(strict=False)).casefold()
    state = getattr(_PROCESS_LOCK_STATE, "locks", None)
    if state is None:
        state = {}
        _PROCESS_LOCK_STATE.locks = state
    held = state.get(lock_key)
    requested_mode = "shared" if shared else "exclusive"
    if held is not None:
        if held["mode"] == "shared" and requested_mode == "exclusive":
            raise PromptAssetConflictError(
                "cannot upgrade a shared prompt asset lock to exclusive"
            )
        held["depth"] += 1
        try:
            yield
        finally:
            held["depth"] -= 1
        return

    try:
        timeout_value = float(timeout)
    except (TypeError, ValueError) as exc:
        raise PromptAssetValidationError("process lock timeout is invalid") from exc
    if not math.isfinite(timeout_value) or timeout_value < 0:
        raise PromptAssetValidationError("process lock timeout is invalid")
    deadline = time.monotonic() + timeout_value

    def retry_or_raise(exc: OSError) -> None:
        would_block = isinstance(exc, BlockingIOError) or exc.errno in {
            errno.EACCES,
            errno.EAGAIN,
            errno.EDEADLK,
        }
        if not would_block:
            raise exc
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PromptAssetConflictError(
                "timed out waiting for the prompt asset process lock"
            ) from exc
        time.sleep(min(_PROCESS_LOCK_POLL_INTERVAL, remaining))

    with lock_path.open("a+b", buffering=0) as handle:
        if os.name == "nt":
            import msvcrt

            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"0")
                handle.flush()
            mode = msvcrt.LK_NBRLCK if shared else msvcrt.LK_NBLCK
            while True:
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), mode, 1)
                    break
                except OSError as exc:
                    retry_or_raise(exc)
            state[lock_key] = {"mode": requested_mode, "depth": 1}
            try:
                yield
            finally:
                state.pop(lock_key, None)
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            mode = (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB
            while True:
                try:
                    fcntl.flock(handle.fileno(), mode)
                    break
                except OSError as exc:
                    retry_or_raise(exc)
            state[lock_key] = {"mode": requested_mode, "depth": 1}
            try:
                yield
            finally:
                state.pop(lock_key, None)
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _windows_replace_is_retryable(exc: OSError) -> bool:
    return os.name == "nt" and (
        isinstance(exc, PermissionError)
        or getattr(exc, "winerror", None) in {5, 32, 33}
        or exc.errno in {errno.EACCES, errno.EBUSY, errno.EPERM}
    )


def _replace_with_retry(
    source: Path,
    target: Path,
    *,
    timeout: float = _PROCESS_LOCK_TIMEOUT,
) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            if not _windows_replace_is_retryable(exc):
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PromptAssetConflictError(
                    "timed out replacing the prompt asset database"
                ) from exc
            time.sleep(min(_PROCESS_LOCK_POLL_INTERVAL, remaining))


class PromptAssetLibrary:
    """SQLite-backed visual prompt assets stored outside the plugin package."""

    def __init__(self, database_path: str | Path):
        self.path = Path(database_path)
        self._lock = _path_lock(self.path)
        self._last_error = ""
        self._generation_lock = threading.Lock()
        self._next_generation = 0
        self._cancelled_generations: set[int] = set()
        self._committed_generations: set[int] = set()
        self._remote_generations: dict[str, int] = {}

    @contextmanager
    def _write_lock(self) -> Iterable[None]:
        with self._lock:
            with _process_file_lock(self.path, shared=False):
                yield

    @contextmanager
    def _read_lock(self) -> Iterable[None]:
        with self._lock:
            with _process_file_lock(self.path, shared=True):
                yield

    def _begin_update(self) -> int:
        with self._generation_lock:
            self._next_generation += 1
            return self._next_generation

    def _begin_remote_update(self, source: str) -> int:
        with self._generation_lock:
            self._next_generation += 1
            generation = self._next_generation
            previous = self._remote_generations.get(source)
            if previous is not None and previous not in self._committed_generations:
                self._cancelled_generations.add(previous)
            self._remote_generations[source] = generation
            return generation

    def _finish_remote_update(self, source: str, generation: int) -> None:
        with self._generation_lock:
            if self._remote_generations.get(source) == generation:
                self._remote_generations.pop(source, None)

    def _invalidate_update(self, generation: int) -> bool:
        with self._generation_lock:
            if generation in self._committed_generations:
                return False
            self._cancelled_generations.add(generation)
            return True

    def _generation_is_current(self, generation: int) -> bool:
        with self._generation_lock:
            return generation not in self._cancelled_generations

    def _finish_update(self, generation: int) -> None:
        with self._generation_lock:
            self._cancelled_generations.discard(generation)
            self._committed_generations.discard(generation)

    @staticmethod
    def _content_kind(content_type: str, data: bytes) -> str:
        hint = str(content_type or "").split(";", 1)[0].strip().casefold()
        if "json" in hint or hint.endswith(".json"):
            return "json"
        if "csv" in hint or hint.endswith(".csv"):
            return "csv"
        candidate = data
        if candidate.startswith(b"\xef\xbb\xbf"):
            candidate = candidate[3:]
        return "json" if candidate.lstrip()[:1] in {b"[", b"{"} else "csv"

    def import_file(
        self,
        path: str | Path,
        *,
        source: str = "",
        provenance: Mapping[str, Any] | None = None,
        mode: str = "merge",
    ) -> dict[str, Any]:
        file_path = Path(path)
        try:
            size = file_path.stat().st_size
            if size > MAX_IMPORT_BYTES:
                raise PromptAssetValidationError(
                    "prompt asset import exceeds size limit"
                )
            payload = file_path.read_bytes()
        except PromptAssetError:
            raise
        except OSError as exc:
            raise PromptAssetError("cannot read prompt asset import file") from exc
        source_label = _source_label(source or str(file_path), local=True)
        return self.import_bytes(
            payload,
            source=source_label,
            content_type=file_path.suffix,
            provenance=provenance,
            mode=mode,
        )

    def import_bytes(
        self,
        data: bytes,
        *,
        source: str = "",
        content_type: str = "",
        provenance: Mapping[str, Any] | None = None,
        mode: str = "merge",
        _expected_generation: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes")
        try:
            payload = data if isinstance(data, bytes) else bytes(data)
            if not payload:
                raise PromptAssetValidationError("prompt asset import is empty")
            if len(payload) > MAX_IMPORT_BYTES:
                raise PromptAssetValidationError(
                    "prompt asset import exceeds size limit"
                )
            if mode not in {"merge", "replace", "replace_source"}:
                raise PromptAssetValidationError("unsupported prompt asset import mode")
            base_provenance = _provenance(provenance)
            if source:
                base_provenance["source"] = _source_label(source)
        except PromptAssetError as exc:
            self._last_error = str(exc)
            raise
        generation = (
            _expected_generation
            if _expected_generation is not None
            else self._begin_update()
        )
        externally_managed = _expected_generation is not None
        if not self._generation_is_current(generation):
            raise PromptAssetConflictError("stale prompt asset import was discarded")
        try:
            kind = self._content_kind(content_type, payload)
            if kind == "json":
                raw_records, embedded = self._records_from_json(payload)
            else:
                raw_records, embedded = self._records_from_csv(payload)
            metadata_provenance = _provenance(embedded)
            combined_provenance = {**metadata_provenance, **base_provenance}
            records = self._prepare_records(raw_records, combined_provenance)
            if not records:
                raise PromptAssetValidationError(
                    "prompt asset import contains no records"
                )
            digest = hashlib.sha256(payload).hexdigest()
            import_metadata = {
                "last_import_at": _utc_now(),
                "last_import_sha256": digest,
                "last_import_source": str(combined_provenance.get("source", "")),
                "last_import_mode": mode,
                "last_import_count": str(len(records)),
            }
            result = self._replace_import(
                records,
                import_metadata,
                mode=mode,
                expected_generation=generation,
            )
        except PromptAssetError as exc:
            if self._generation_is_current(generation):
                self._last_error = str(exc)
            self._invalidate_update(generation)
            if not externally_managed:
                self._finish_update(generation)
            raise
        except Exception as exc:
            message = f"prompt asset import failed: {type(exc).__name__}"
            if self._generation_is_current(generation):
                self._last_error = message
            self._invalidate_update(generation)
            if not externally_managed:
                self._finish_update(generation)
            raise PromptAssetError(message) from exc
        if self._generation_is_current(generation):
            self._last_error = ""
        if not externally_managed:
            self._finish_update(generation)
        return result

    async def import_bytes_async(
        self,
        data: bytes,
        *,
        source: str = "",
        content_type: str = "",
        provenance: Mapping[str, Any] | None = None,
        mode: str = "merge",
        timeout: float = MAX_REMOTE_TIMEOUT,
    ) -> dict[str, Any]:
        """Run a local import without allowing cancellation to publish later."""

        timeout_value = self._validated_timeout(timeout)
        generation = self._begin_update()
        worker = asyncio.create_task(
            asyncio.to_thread(
                self.import_bytes,
                data,
                source=source,
                content_type=content_type,
                provenance=provenance,
                mode=mode,
                _expected_generation=generation,
            )
        )
        deadline = asyncio.get_running_loop().time() + timeout_value
        return await self._await_import_worker(worker, generation, deadline)

    @staticmethod
    def _validated_timeout(timeout: Any) -> float:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(float(timeout))
            or not 0 < float(timeout) <= MAX_REMOTE_TIMEOUT
        ):
            raise PromptAssetValidationError(
                f"timeout must be between 0 and {MAX_REMOTE_TIMEOUT:g} seconds"
            )
        return float(timeout)

    def _consume_worker_result(self, task: asyncio.Task[Any], generation: int) -> None:
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            pass
        finally:
            self._finish_update(generation)

    async def _await_import_worker(
        self,
        worker: asyncio.Task[dict[str, Any]],
        generation: int,
        deadline: float,
    ) -> dict[str, Any]:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            self._invalidate_update(generation)
            worker.add_done_callback(
                lambda task: self._consume_worker_result(task, generation)
            )
            raise PromptAssetError("prompt asset import timed out")
        try:
            result = await asyncio.wait_for(asyncio.shield(worker), timeout=remaining)
        except asyncio.CancelledError:
            if worker.done():
                try:
                    return worker.result()
                finally:
                    self._finish_update(generation)
            invalidated = self._invalidate_update(generation)
            if not invalidated:
                try:
                    return await worker
                finally:
                    self._finish_update(generation)
            worker.add_done_callback(
                lambda task: self._consume_worker_result(task, generation)
            )
            raise
        except asyncio.TimeoutError as exc:
            if worker.done():
                try:
                    return worker.result()
                finally:
                    self._finish_update(generation)
            invalidated = self._invalidate_update(generation)
            if not invalidated:
                try:
                    return await worker
                finally:
                    self._finish_update(generation)
            worker.add_done_callback(
                lambda task: self._consume_worker_result(task, generation)
            )
            raise PromptAssetError("prompt asset import timed out") from exc
        except Exception:
            self._invalidate_update(generation)
            self._finish_update(generation)
            raise
        self._finish_update(generation)
        return result

    @staticmethod
    def _records_from_json(
        payload: bytes,
    ) -> tuple[list[tuple[Mapping[str, Any], str]], dict[str, Any]]:
        try:
            decoded = json.loads(
                payload.decode("utf-8-sig"),
                parse_constant=_reject_json_constant,
                object_pairs_hook=_reject_duplicate_json_object,
            )
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise PromptAssetValidationError("prompt asset JSON is invalid") from exc
        records: list[tuple[Mapping[str, Any], str]] = []
        metadata: dict[str, Any] = {}
        if isinstance(decoded, list):
            for item in decoded:
                records.append((item, ""))
        elif isinstance(decoded, Mapping):
            metadata = {
                str(key): value
                for key, value in decoded.items()
                if key
                not in {
                    "assets",
                    "artists",
                    "characters",
                    "clothing",
                    "clothes",
                    "backgrounds",
                    "poses",
                }
            }
            if "assets" in decoded:
                assets = decoded["assets"]
                if not isinstance(assets, list):
                    raise PromptAssetValidationError("assets must be a list")
                records.extend((item, "") for item in assets)
            sections = {
                "artists": "artist",
                "characters": "character",
                "clothing": "clothing",
                "clothes": "clothing",
                "backgrounds": "background",
                "poses": "pose",
            }
            for key, kind in sections.items():
                if key not in decoded:
                    continue
                items = decoded[key]
                if not isinstance(items, list):
                    raise PromptAssetValidationError(
                        "prompt asset section must be a list"
                    )
                records.extend((item, kind) for item in items)
            if not records and any(
                key in decoded
                for key in (
                    "asset_type",
                    "type",
                    "kind",
                    "category",
                    "name",
                    "name_en",
                    "name_zh",
                    "alias",
                    "aliases",
                    "tag",
                    "tags",
                )
            ):
                records.append((decoded, ""))
                metadata = {}
        else:
            raise PromptAssetValidationError("prompt asset JSON root is invalid")
        if len(records) > MAX_IMPORT_RECORDS:
            raise PromptAssetValidationError("prompt asset import has too many records")
        return records, metadata

    @staticmethod
    def _records_from_csv(
        payload: bytes,
    ) -> tuple[list[tuple[Mapping[str, Any], str]], dict[str, Any]]:
        try:
            text = payload.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise PromptAssetValidationError("prompt asset CSV must be UTF-8") from exc
        try:
            reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
            if not reader.fieldnames:
                raise PromptAssetValidationError("prompt asset CSV has no header")
            if len(reader.fieldnames) > MAX_IMPORT_FIELDS:
                raise PromptAssetValidationError("prompt asset CSV has too many fields")
            headers = [
                _clean_text(value, "CSV header", 128) for value in reader.fieldnames
            ]
            normalized_headers = [normalize_asset_text(value) for value in headers]
            if any(not value for value in normalized_headers):
                raise PromptAssetValidationError("prompt asset CSV has an empty header")
            if len(set(normalized_headers)) != len(normalized_headers):
                raise PromptAssetValidationError(
                    "prompt asset CSV contains duplicate headers"
                )
            reader.fieldnames = [value.strip().casefold() for value in headers]
            records: list[tuple[Mapping[str, Any], str]] = []
            for row in reader:
                if None in row:
                    raise PromptAssetValidationError(
                        "prompt asset CSV row is malformed"
                    )
                if any(str(value or "").strip() for value in row.values()):
                    records.append((row, ""))
                if len(records) > MAX_IMPORT_RECORDS:
                    raise PromptAssetValidationError(
                        "prompt asset import has too many records"
                    )
        except csv.Error as exc:
            raise PromptAssetValidationError("prompt asset CSV is invalid") from exc
        return records, {}

    def _prepare_records(
        self,
        raw_records: Sequence[tuple[Mapping[str, Any], str]],
        base_provenance: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, pair in enumerate(raw_records, start=1):
            raw, default_type = pair
            if not isinstance(raw, Mapping):
                raise PromptAssetValidationError(
                    f"prompt asset record {index} must be an object"
                )
            if len(raw) > MAX_IMPORT_FIELDS:
                raise PromptAssetValidationError(
                    f"prompt asset record {index} has too many fields"
                )
            try:
                record = self._prepare_record(raw, default_type, base_provenance)
            except PromptAssetValidationError as exc:
                raise PromptAssetValidationError(
                    f"prompt asset record {index}: {exc}"
                ) from exc
            asset_id = record["asset_id"]
            if asset_id in seen:
                raise PromptAssetConflictError(
                    f"duplicate prompt asset ID in import at record {index}"
                )
            seen.add(asset_id)
            result.append(record)
        return result

    @staticmethod
    def _first(raw: Mapping[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in raw and raw[key] not in (None, ""):
                return raw[key]
        return ""

    def _prepare_record(
        self,
        raw: Mapping[str, Any],
        default_type: str,
        base_provenance: Mapping[str, Any],
        *,
        custom: bool = False,
        forced_asset_id: str = "",
    ) -> dict[str, Any]:
        _reject_embedded_protocols(raw)
        _reject_embedded_credentials(raw)
        normalized_fields: set[str] = set()
        for raw_key in raw:
            key = _clean_text(raw_key, "prompt asset field", 128).casefold()
            if key not in _RECORD_FIELDS:
                raise PromptAssetValidationError(
                    "prompt asset record contains an unsupported field"
                )
            normalized_fields.add(key)
        if "description" in normalized_fields:
            _clean_text(raw.get("description"), "description", 4096)
        kind_value = self._first(raw, "asset_type", "type", "kind") or default_type
        if not kind_value and normalize_asset_text(raw.get("category")) in ASSET_TYPES:
            kind_value = raw.get("category")
        kind = _asset_type(kind_value)

        name_zh = _clean_text(
            self._first(raw, "name_zh", "name_cn", "cn_name", "chinese_name", "zh"),
            "name_zh",
        )
        name_en = _clean_text(
            self._first(raw, "name_en", "en_name", "english_name", "en"),
            "name_en",
        )
        generic_name = _clean_text(raw.get("name", ""), "name")
        if generic_name and not (name_zh or name_en):
            if re.search(r"[\u3400-\u9fff]", generic_name):
                name_zh = generic_name
            else:
                name_en = generic_name

        aliases = _list_values(
            self._first(raw, "aliases", "alias", "alternate_names"), "aliases"
        )
        tags = _list_values(
            self._first(raw, "tags", "tag", "trigger_words", "prompts"), "tags"
        )
        traits = _list_values(
            self._first(raw, "traits", "attributes", "features"), "traits"
        )
        categories_value = self._first(raw, "categories", "groups", "genres")
        if categories_value in (None, "") and raw.get("category") not in (None, ""):
            if normalize_asset_text(raw.get("category")) not in ASSET_TYPES:
                categories_value = raw.get("category")
        categories = _list_values(categories_value, "categories")
        if not (name_zh or name_en or aliases or tags):
            raise PromptAssetValidationError(
                "an asset requires a name, alias or prompt tag"
            )

        preview_key, preview_available = _preview_reference(
            self._first(
                raw,
                "preview_url",
                "preview",
                "thumbnail_url",
                "thumbnail",
                "image_url",
                "image",
            )
        )
        record_provenance = _provenance(raw.get("provenance"))
        combined_provenance = {**record_provenance, **dict(base_provenance)}
        source_id = _clean_text(
            self._first(raw, "source_id", "external_id", "id", "slug"),
            "source_id",
            256,
            allow_number=True,
        )
        if source_id:
            combined_provenance.setdefault("source_id", source_id)
        if custom:
            combined_provenance["source"] = "custom"
        combined_provenance = _provenance(combined_provenance)

        supplied_id = _clean_text(raw.get("asset_id", ""), "asset_id", 64)
        if not (name_zh or name_en) and not (
            forced_asset_id or supplied_id or source_id
        ):
            raise PromptAssetValidationError(
                "assets without a primary name require source_id or asset_id"
            )
        if forced_asset_id:
            asset_id = forced_asset_id
        elif supplied_id:
            if not _ASSET_ID.fullmatch(supplied_id):
                raise PromptAssetValidationError("asset_id has an invalid format")
            asset_id = supplied_id
        else:
            identity = source_id or name_en or name_zh or aliases[0] or tags[0]
            namespace = _clean_text(
                self._first(combined_provenance, "namespace", "dataset", "source"),
                "asset namespace",
                512,
            )
            asset_id = stable_asset_id(kind, identity, namespace)

        source_key = normalize_asset_text(
            _clean_text(
                self._first(combined_provenance, "source", "dataset", "namespace"),
                "asset source",
                MAX_URL_LENGTH,
            )
        )
        identity_values = [name_zh, name_en]
        identity_keys = tuple(
            dict.fromkeys(
                key
                for key in (normalize_asset_text(item) for item in identity_values)
                if key
            )
        )
        now = _utc_now()
        searchable = [
            name_zh,
            name_en,
            *aliases,
            *tags,
            *traits,
            *categories,
        ]
        return {
            "asset_id": asset_id,
            "asset_type": kind,
            "name_zh": name_zh,
            "name_en": name_en,
            "aliases": aliases,
            "tags": tags,
            "traits": traits,
            "categories": categories,
            "preview_key": preview_key,
            "preview_available": preview_available,
            "provenance": combined_provenance,
            "source_key": source_key,
            "is_custom": bool(custom),
            "search_text": _search_blob(searchable),
            "category_tokens": _token_blob(categories),
            "trait_tokens": _token_blob(traits),
            "tag_tokens": _token_blob(tags),
            "created_at": now,
            "updated_at": now,
            "_identity_explicit": bool(forced_asset_id or supplied_id or source_id),
            "_identity_mode": (
                "asset_id"
                if forced_asset_id or supplied_id
                else "source_id"
                if source_id
                else "weak"
            ),
            "_identity_keys": identity_keys,
        }

    def _replace_import(
        self,
        imported: Sequence[dict[str, Any]],
        metadata_updates: Mapping[str, Any],
        *,
        mode: str,
        expected_generation: int,
    ) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._write_lock():
            current, favourites, metadata = self._read_snapshot_unlocked()
            if mode == "replace":
                retained = {
                    asset_id: record
                    for asset_id, record in current.items()
                    if record["is_custom"]
                }
            elif mode == "replace_source":
                source_keys = {record["source_key"] for record in imported}
                if not source_keys or "" in source_keys:
                    raise PromptAssetValidationError(
                        "replace_source imports require explicit source provenance"
                    )
                retained = {
                    asset_id: record
                    for asset_id, record in current.items()
                    if record["is_custom"] or record["source_key"] not in source_keys
                }
            else:
                retained = dict(current)

            used_ids: set[str] = set()
            for record in imported:
                identity_mode = str(record.get("_identity_mode") or "weak")
                migrated_existing: Mapping[str, Any] | None = None
                if identity_mode in {"weak", "source_id", "asset_id"}:
                    identity_keys = set(record.get("_identity_keys") or ())
                    matches = [
                        (candidate_id, candidate)
                        for candidate_id, candidate in current.items()
                        if not candidate["is_custom"]
                        and candidate["asset_type"] == record["asset_type"]
                        and candidate["source_key"] == record["source_key"]
                        and identity_keys.intersection(
                            self._identity_keys_for_record(candidate)
                        )
                        and (
                            identity_mode == "weak"
                            or (
                                identity_mode == "source_id"
                                and (
                                    not candidate["provenance"].get("source_id")
                                    or candidate["provenance"].get("source_id")
                                    == record["provenance"].get("source_id")
                                )
                            )
                            or (
                                identity_mode == "asset_id"
                                and not candidate["provenance"].get("source_id")
                                and self._record_uses_derived_id(
                                    candidate_id, candidate
                                )
                            )
                        )
                    ]
                    if len(matches) > 1:
                        raise PromptAssetConflictError(
                            "weak prompt asset identity matches multiple existing assets; "
                            "provide source_id or asset_id"
                        )
                    if matches and identity_mode == "weak":
                        record = {**record, "asset_id": matches[0][0]}
                    elif matches and identity_mode in {"source_id", "asset_id"}:
                        old_id, migrated_existing = matches[0]
                        if old_id != record["asset_id"]:
                            retained.pop(old_id, None)
                            if old_id in favourites:
                                favourites.discard(old_id)
                                favourites.add(record["asset_id"])
                asset_id = record["asset_id"]
                if asset_id in used_ids:
                    raise PromptAssetConflictError(
                        "prompt asset import resolves multiple records to one asset ID"
                    )
                used_ids.add(asset_id)
                existing = (
                    retained.get(asset_id) or current.get(asset_id) or migrated_existing
                )
                if existing and existing["is_custom"]:
                    raise PromptAssetConflictError(
                        "an imported asset cannot overwrite a custom asset"
                    )
                if (
                    existing
                    and mode != "replace"
                    and existing["source_key"] != record["source_key"]
                ):
                    raise PromptAssetConflictError(
                        "an imported asset ID is owned by a different source"
                    )
                if existing:
                    record = {
                        **record,
                        "created_at": existing["created_at"],
                    }
                retained[asset_id] = record
            if len(retained) > MAX_IMPORT_RECORDS:
                raise PromptAssetValidationError(
                    "prompt asset library exceeds total record limit"
                )
            if sum(_record_text_size(record) for record in retained.values()) > (
                MAX_LIBRARY_TEXT_BYTES
            ):
                raise PromptAssetValidationError(
                    "prompt asset library exceeds total text budget"
                )
            active_favourites = favourites.intersection(retained)
            complete_metadata = {**metadata, **dict(metadata_updates)}
            revision = self._write_snapshot_unlocked(
                retained.values(),
                active_favourites,
                complete_metadata,
                expected_generation=expected_generation,
            )
            return self._status_from_snapshot(
                retained.values(),
                active_favourites,
                complete_metadata,
                revision,
            )

    def _status_from_snapshot(
        self,
        records: Iterable[Mapping[str, Any]],
        favourites: Iterable[str],
        metadata: Mapping[str, Any],
        revision: str,
    ) -> dict[str, Any]:
        rows = list(records)
        type_counts = {kind: 0 for kind in sorted(ASSET_TYPES)}
        for record in rows:
            type_counts[str(record["asset_type"])] += 1
        try:
            last_import_count = int(metadata.get("last_import_count", 0))
        except (TypeError, ValueError):
            last_import_count = 0
        return {
            "ready": True,
            "schema_version": _SCHEMA_VERSION,
            "revision": revision,
            "asset_count": len(rows),
            "custom_count": sum(bool(record["is_custom"]) for record in rows),
            "favorite_count": len(set(favourites)),
            "type_counts": type_counts,
            "last_import_at": str(metadata.get("last_import_at", "")),
            "last_import_sha256": str(metadata.get("last_import_sha256", "")),
            "last_import_source": str(metadata.get("last_import_source", "")),
            "last_import_mode": str(metadata.get("last_import_mode", "")),
            "last_import_count": last_import_count,
            "error": "",
        }

    @staticmethod
    def _identity_keys_for_record(record: Mapping[str, Any]) -> set[str]:
        values = [record.get("name_zh", ""), record.get("name_en", "")]
        return {key for key in (normalize_asset_text(item) for item in values) if key}

    def _read_snapshot_unlocked(
        self,
    ) -> tuple[dict[str, dict[str, Any]], set[str], dict[str, str]]:
        if not self.path.is_file():
            return {}, set(), {}
        try:
            connection = self._connect(read_only=True)
            try:
                metadata = {
                    str(row["key"]): str(row["value"])
                    for row in connection.execute(
                        "SELECT key, value FROM metadata"
                    ).fetchall()
                }
                if metadata.get("schema_version") != _SCHEMA_VERSION:
                    raise PromptAssetError("unsupported prompt asset schema")
                records = {
                    str(row["asset_id"]): self._record_from_row(row)
                    for row in connection.execute("SELECT * FROM assets").fetchall()
                }
                favourites = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT asset_id FROM favourites"
                    ).fetchall()
                }
                return records, favourites, metadata
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise PromptAssetError("cannot read prompt asset database") from exc

    def _write_snapshot_unlocked(
        self,
        records: Iterable[Mapping[str, Any]],
        favourites: Iterable[str],
        metadata: Mapping[str, Any],
        *,
        expected_generation: int | None = None,
    ) -> str:
        revision = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
        snapshot_metadata = {**dict(metadata), "revision": revision}
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            self._build_database(temporary, records, favourites, snapshot_metadata)
            with temporary.open("r+b") as handle:
                os.fsync(handle.fileno())
            if expected_generation is None:
                _replace_with_retry(temporary, self.path)
                _fsync_directory(self.path.parent)
            else:
                with self._generation_lock:
                    if expected_generation in self._cancelled_generations:
                        raise PromptAssetConflictError(
                            "stale prompt asset import was discarded"
                        )
                    _replace_with_retry(temporary, self.path)
                    _fsync_directory(self.path.parent)
                    self._committed_generations.add(expected_generation)
            return revision
        except Exception:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    @staticmethod
    def _schema_sql() -> str:
        allowed = ",".join(f"'{item}'" for item in sorted(ASSET_TYPES))
        return f"""
            PRAGMA journal_mode=DELETE;
            PRAGMA synchronous=FULL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE assets (
                asset_id TEXT PRIMARY KEY,
                asset_type TEXT NOT NULL CHECK(asset_type IN ({allowed})),
                name_zh TEXT NOT NULL DEFAULT '',
                name_en TEXT NOT NULL DEFAULT '',
                aliases TEXT NOT NULL DEFAULT '[]',
                tags TEXT NOT NULL DEFAULT '[]',
                traits TEXT NOT NULL DEFAULT '[]',
                categories TEXT NOT NULL DEFAULT '[]',
                preview_key TEXT NOT NULL DEFAULT '',
                preview_available INTEGER NOT NULL DEFAULT 0
                    CHECK(preview_available IN (0, 1)),
                provenance TEXT NOT NULL DEFAULT '{{}}',
                source_key TEXT NOT NULL DEFAULT '',
                is_custom INTEGER NOT NULL DEFAULT 0 CHECK(is_custom IN (0, 1)),
                search_text TEXT NOT NULL DEFAULT '',
                category_tokens TEXT NOT NULL DEFAULT '',
                trait_tokens TEXT NOT NULL DEFAULT '',
                tag_tokens TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE favourites (
                asset_id TEXT PRIMARY KEY
                    REFERENCES assets(asset_id) ON DELETE CASCADE,
                created_at TEXT NOT NULL
            );
            CREATE INDEX idx_assets_type ON assets(asset_type);
            CREATE INDEX idx_assets_custom ON assets(is_custom);
            CREATE INDEX idx_assets_source ON assets(source_key);
            CREATE INDEX idx_assets_updated ON assets(updated_at);
        """

    @classmethod
    def _build_database(
        cls,
        path: Path,
        records: Iterable[Mapping[str, Any]],
        favourites: Iterable[str],
        metadata: Mapping[str, Any],
    ) -> None:
        connection = sqlite3.connect(path, timeout=30)
        try:
            connection.executescript(cls._schema_sql())
            rows = list(records)
            connection.executemany(
                """INSERT INTO assets (
                    asset_id, asset_type, name_zh, name_en, aliases, tags,
                    traits, categories, preview_key, preview_available,
                    provenance, source_key, is_custom, search_text,
                    category_tokens, trait_tokens, tag_tokens, created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [cls._record_parameters(record) for record in rows],
            )
            favourite_ids = set(favourites)
            valid_ids = {str(record["asset_id"]) for record in rows}
            now = _utc_now()
            connection.executemany(
                "INSERT INTO favourites(asset_id, created_at) VALUES (?, ?)",
                [(asset_id, now) for asset_id in sorted(favourite_ids & valid_ids)],
            )
            complete_metadata = {
                "schema_version": _SCHEMA_VERSION,
                "created_at": str(metadata.get("created_at") or now),
                **{str(key): str(value) for key, value in metadata.items()},
                "updated_at": now,
            }
            connection.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                complete_metadata.items(),
            )
            check = connection.execute("PRAGMA integrity_check").fetchone()
            if not check or check[0] != "ok":
                raise PromptAssetError("prompt asset SQLite integrity check failed")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _record_parameters(record: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            record["asset_id"],
            record["asset_type"],
            record["name_zh"],
            record["name_en"],
            _json_text(list(record["aliases"])),
            _json_text(list(record["tags"])),
            _json_text(list(record["traits"])),
            _json_text(list(record["categories"])),
            record["preview_key"],
            int(bool(record["preview_available"])),
            _json_text(record["provenance"]),
            record["source_key"],
            int(bool(record["is_custom"])),
            record["search_text"],
            record["category_tokens"],
            record["trait_tokens"],
            record["tag_tokens"],
            record["created_at"],
            record["updated_at"],
        )

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "asset_id": str(row["asset_id"]),
            "asset_type": str(row["asset_type"]),
            "name_zh": str(row["name_zh"]),
            "name_en": str(row["name_en"]),
            "aliases": _decode_json_list(row["aliases"]),
            "tags": _decode_json_list(row["tags"]),
            "traits": _decode_json_list(row["traits"]),
            "categories": _decode_json_list(row["categories"]),
            "preview_key": str(row["preview_key"]),
            "preview_available": bool(row["preview_available"]),
            "provenance": _decode_json_object(row["provenance"]),
            "source_key": str(row["source_key"]),
            "is_custom": bool(row["is_custom"]),
            "search_text": str(row["search_text"]),
            "category_tokens": str(row["category_tokens"]),
            "trait_tokens": str(row["trait_tokens"]),
            "tag_tokens": str(row["tag_tokens"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    @staticmethod
    def _record_uses_derived_id(asset_id: str, record: Mapping[str, Any]) -> bool:
        identity = (
            record.get("name_en")
            or record.get("name_zh")
            or next(iter(record.get("aliases") or ()), "")
            or next(iter(record.get("tags") or ()), "")
        )
        provenance = record.get("provenance") or {}
        namespace = (
            provenance.get("namespace")
            or provenance.get("dataset")
            or provenance.get("source")
            or ""
        )
        if not identity:
            return False
        return asset_id == stable_asset_id(record["asset_type"], identity, namespace)

    @staticmethod
    def _public_record(record: Mapping[str, Any], favourite: bool) -> dict[str, Any]:
        return {
            key: value
            for key, value in record.items()
            if not key.startswith("_")
            and key
            not in {
                "source_key",
                "search_text",
                "category_tokens",
                "trait_tokens",
                "tag_tokens",
            }
        } | {"favorite": bool(favourite)}

    def _connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        if read_only:
            uri = self.path.resolve(strict=False).as_uri() + "?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=10)
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
        else:
            connection = sqlite3.connect(self.path, timeout=30)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA synchronous=FULL")
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _bump_revision(connection: sqlite3.Connection) -> str:
        revision = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
        now = _utc_now()
        connection.execute(
            """INSERT INTO metadata(key, value) VALUES ('revision', ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (revision,),
        )
        connection.execute(
            """INSERT INTO metadata(key, value) VALUES ('updated_at', ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (now,),
        )
        return revision

    @staticmethod
    def _database_text_size(connection: sqlite3.Connection) -> int:
        columns = (
            "name_zh",
            "name_en",
            "aliases",
            "tags",
            "traits",
            "categories",
            "preview_key",
            "provenance",
            "source_key",
            "search_text",
            "category_tokens",
            "trait_tokens",
            "tag_tokens",
        )
        expression = " + ".join(f"length(CAST({column} AS BLOB))" for column in columns)
        row = connection.execute(
            f"SELECT COALESCE(SUM({expression}), 0) FROM assets"
        ).fetchone()
        return int(row[0] if row else 0)

    def _ensure_database_unlocked(self) -> None:
        if self.path.is_file():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._write_snapshot_unlocked([], [], {})

    def status(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ready": False,
            "schema_version": _SCHEMA_VERSION,
            "revision": "",
            "asset_count": 0,
            "custom_count": 0,
            "favorite_count": 0,
            "type_counts": {kind: 0 for kind in sorted(ASSET_TYPES)},
            "last_import_at": "",
            "last_import_sha256": "",
            "last_import_source": "",
            "last_import_mode": "",
            "last_import_count": 0,
            "error": self._last_error,
        }
        with self._read_lock():
            if not self.path.is_file():
                return result
            try:
                connection = self._connect(read_only=True)
                try:
                    metadata = {
                        str(row["key"]): str(row["value"])
                        for row in connection.execute(
                            "SELECT key, value FROM metadata"
                        ).fetchall()
                    }
                    if metadata.get("schema_version") != _SCHEMA_VERSION:
                        raise PromptAssetError("unsupported prompt asset schema")
                    result["asset_count"] = int(
                        connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
                    )
                    result["custom_count"] = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM assets WHERE is_custom = 1"
                        ).fetchone()[0]
                    )
                    result["favorite_count"] = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM favourites"
                        ).fetchone()[0]
                    )
                    result["type_counts"].update(
                        {
                            str(row[0]): int(row[1])
                            for row in connection.execute(
                                "SELECT asset_type, COUNT(*) FROM assets GROUP BY asset_type"
                            ).fetchall()
                        }
                    )
                    for key in (
                        "revision",
                        "last_import_at",
                        "last_import_sha256",
                        "last_import_source",
                        "last_import_mode",
                    ):
                        result[key] = metadata.get(key, "")
                    try:
                        result["last_import_count"] = int(
                            metadata.get("last_import_count", "0")
                        )
                    except ValueError:
                        result["last_import_count"] = 0
                    result["ready"] = True
                finally:
                    connection.close()
            except (sqlite3.Error, PromptAssetError) as exc:
                result["error"] = str(exc)
        return result

    def get(self, asset_id: str) -> dict[str, Any]:
        identifier = self._validate_asset_id(asset_id)
        with self._read_lock():
            if not self.path.is_file():
                raise PromptAssetNotFoundError("prompt asset does not exist")
            connection = self._connect(read_only=True)
            try:
                row = connection.execute(
                    """SELECT a.*, CASE WHEN f.asset_id IS NULL THEN 0 ELSE 1 END
                              AS favourite
                       FROM assets a LEFT JOIN favourites f USING(asset_id)
                       WHERE a.asset_id = ?""",
                    (identifier,),
                ).fetchone()
                revision_row = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'revision'"
                ).fetchone()
            finally:
                connection.close()
        if row is None:
            raise PromptAssetNotFoundError("prompt asset does not exist")
        result = self._public_record(self._record_from_row(row), bool(row["favourite"]))
        result["revision"] = str(revision_row[0]) if revision_row else ""
        return result

    @staticmethod
    def _validate_asset_id(asset_id: Any) -> str:
        identifier = str(asset_id or "").strip()
        if not _ASSET_ID.fullmatch(identifier):
            raise PromptAssetValidationError("asset_id has an invalid format")
        return identifier

    def search(
        self,
        query: str = "",
        *,
        asset_type: str = "",
        source: str = "",
        categories: Sequence[str] | str = (),
        traits: Sequence[str] | str = (),
        tags: Sequence[str] | str = (),
        favorite_only: bool = False,
        custom_only: bool | None = None,
        page: int = 1,
        page_size: int = 50,
        sort: str = "relevance",
    ) -> dict[str, Any]:
        normalized_query = normalize_asset_text(query)
        if len(normalized_query) > MAX_QUERY_LENGTH:
            raise PromptAssetValidationError("search query exceeds length limit")
        kind = _asset_type(asset_type) if asset_type else ""
        source_filter = normalize_asset_text(
            _clean_text(source, "source filter", MAX_URL_LENGTH)
        )
        category_filters = tuple(
            normalize_asset_text(item)
            for item in _list_values(categories, "categories")
        )
        trait_filters = tuple(
            normalize_asset_text(item) for item in _list_values(traits, "traits")
        )
        tag_filters = tuple(
            normalize_asset_text(item) for item in _list_values(tags, "tags")
        )
        if (
            isinstance(page, bool)
            or not isinstance(page, int)
            or not 1 <= page <= MAX_PAGE
        ):
            raise PromptAssetValidationError(f"page must be between 1 and {MAX_PAGE}")
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= MAX_PAGE_SIZE
        ):
            raise PromptAssetValidationError(
                f"page_size must be between 1 and {MAX_PAGE_SIZE}"
            )
        if sort not in {"relevance", "name", "updated", "created"}:
            raise PromptAssetValidationError("unsupported prompt asset sort")

        where: list[str] = []
        parameters: list[Any] = []
        if normalized_query:
            query_terms = [term for term in normalized_query.split(" ") if term] or [
                normalized_query
            ]
            for term in query_terms:
                where.append(
                    "(a.search_text LIKE ? ESCAPE '\\' OR "
                    "prompt_asset_provenance_contains(a.provenance, ?) = 1)"
                )
                parameters.extend((_like_pattern(term), term))
        if kind:
            where.append("a.asset_type = ?")
            parameters.append(kind)
        if source_filter:
            where.append("a.source_key = ?")
            parameters.append(source_filter)
        if category_filters:
            options = []
            for item in category_filters:
                options.append("a.category_tokens LIKE ? ESCAPE '\\'")
                parameters.append(_like_pattern(f"\n{item}\n"))
            where.append("(" + " OR ".join(options) + ")")
        for column, values in (
            ("trait_tokens", trait_filters),
            ("tag_tokens", tag_filters),
        ):
            for item in values:
                where.append(f"a.{column} LIKE ? ESCAPE '\\'")
                parameters.append(_like_pattern(f"\n{item}\n"))
        if favorite_only:
            where.append("f.asset_id IS NOT NULL")
        if custom_only is not None:
            where.append("a.is_custom = ?")
            parameters.append(int(bool(custom_only)))
        clause = " WHERE " + " AND ".join(where) if where else ""

        display_name = (
            "COALESCE(NULLIF(a.name_zh, ''), NULLIF(a.name_en, ''), a.asset_id)"
        )
        if sort == "updated":
            order = f"a.updated_at DESC, {display_name} COLLATE NOCASE, a.asset_id"
        elif sort == "created":
            order = f"a.created_at DESC, {display_name} COLLATE NOCASE, a.asset_id"
        elif sort == "relevance" and normalized_query:
            order = (
                "prompt_asset_relevance(a.name_zh, a.name_en, a.aliases, a.tags, "
                "a.traits, a.categories, a.provenance, a.search_text, ?) DESC, "
                f"{display_name} COLLATE NOCASE, a.asset_id"
            )
        else:
            order = f"{display_name} COLLATE NOCASE, a.asset_id"

        with self._read_lock():
            if not self.path.is_file():
                return {
                    "items": [],
                    "total": 0,
                    "page": page,
                    "page_size": page_size,
                    "pages": 0,
                    "revision": "",
                }
            connection = self._connect(read_only=True)
            try:
                connection.create_function(
                    "prompt_asset_provenance_contains", 2, _provenance_contains
                )
                connection.create_function(
                    "prompt_asset_relevance", 9, _relevance_score
                )
                base = "FROM assets a LEFT JOIN favourites f USING(asset_id)" + clause
                total = int(
                    connection.execute(
                        "SELECT COUNT(*) " + base, parameters
                    ).fetchone()[0]
                )
                rows = connection.execute(
                    """SELECT a.*, CASE WHEN f.asset_id IS NULL THEN 0 ELSE 1 END
                              AS favourite """
                    + base
                    + " ORDER BY "
                    + order
                    + " LIMIT ? OFFSET ?",
                    [
                        *parameters,
                        *([normalized_query] if sort == "relevance" and normalized_query else []),
                        page_size,
                        (page - 1) * page_size,
                    ],
                ).fetchall()
                revision_row = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'revision'"
                ).fetchone()
            finally:
                connection.close()
        return {
            "items": [
                self._public_record(self._record_from_row(row), bool(row["favourite"]))
                for row in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
            "revision": str(revision_row[0]) if revision_row else "",
        }

    def facets(
        self,
        *,
        asset_type: str = "",
        source: str = "",
        favorite_only: bool = False,
        custom_only: bool | None = None,
        limit: int = MAX_PAGE_SIZE,
    ) -> dict[str, Any]:
        """Return bounded category/trait counts for visual filter controls."""

        kind = _asset_type(asset_type) if asset_type else ""
        source_filter = normalize_asset_text(
            _clean_text(source, "source filter", MAX_URL_LENGTH)
        )
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_PAGE_SIZE
        ):
            raise PromptAssetValidationError(
                f"facet limit must be between 1 and {MAX_PAGE_SIZE}"
            )
        where: list[str] = []
        parameters: list[Any] = []
        if kind:
            where.append("a.asset_type = ?")
            parameters.append(kind)
        if source_filter:
            where.append("a.source_key = ?")
            parameters.append(source_filter)
        if favorite_only:
            where.append("f.asset_id IS NOT NULL")
        if custom_only is not None:
            where.append("a.is_custom = ?")
            parameters.append(int(bool(custom_only)))
        clause = " WHERE " + " AND ".join(where) if where else ""
        with self._read_lock():
            if not self.path.is_file():
                return {
                    "type_counts": {item: 0 for item in sorted(ASSET_TYPES)},
                    "sources": [],
                    "categories": [],
                    "traits": [],
                    "revision": "",
                }
            connection = self._connect(read_only=True)
            try:
                rows = connection.execute(
                    """SELECT a.asset_type, a.categories, a.traits,
                              a.provenance, a.source_key
                       FROM assets a LEFT JOIN favourites f USING(asset_id)"""
                    + clause,
                    parameters,
                ).fetchall()
                revision_row = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'revision'"
                ).fetchone()
            finally:
                connection.close()
        type_counts = {item: 0 for item in sorted(ASSET_TYPES)}
        category_labels: dict[str, str] = {}
        trait_labels: dict[str, str] = {}
        source_labels: dict[str, str] = {}
        category_counts: dict[str, int] = {}
        trait_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}
        for row in rows:
            type_counts[str(row["asset_type"])] += 1
            provenance = _decode_json_object(row["provenance"])
            source_value = str(provenance.get("source") or row["source_key"])
            source_key = normalize_asset_text(source_value)
            if source_key:
                source_labels.setdefault(source_key, source_value)
                source_counts[source_key] = source_counts.get(source_key, 0) + 1
            for raw, labels, counts in (
                (row["categories"], category_labels, category_counts),
                (row["traits"], trait_labels, trait_counts),
            ):
                for value in _decode_json_list(raw):
                    key = normalize_asset_text(value)
                    if not key:
                        continue
                    labels.setdefault(key, value)
                    counts[key] = counts.get(key, 0) + 1

        def ranked(
            labels: Mapping[str, str], counts: Mapping[str, int]
        ) -> list[dict[str, Any]]:
            return [
                {"value": labels[key], "count": count}
                for key, count in sorted(
                    counts.items(),
                    key=lambda item: (-item[1], labels[item[0]].casefold()),
                )[:limit]
            ]

        return {
            "type_counts": type_counts,
            "sources": ranked(source_labels, source_counts),
            "categories": ranked(category_labels, category_counts),
            "traits": ranked(trait_labels, trait_counts),
            "revision": str(revision_row[0]) if revision_row else "",
        }

    def create_custom(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise PromptAssetValidationError("custom asset must be an object")
        requested_id = str(payload.get("asset_id") or "").strip()
        if requested_id:
            identifier = self._validate_asset_id(requested_id)
        else:
            identifier = f"pa_{uuid.uuid4().hex}"
        record = self._prepare_record(
            payload,
            "",
            {},
            custom=True,
            forced_asset_id=identifier,
        )
        with self._write_lock():
            if not self.path.is_file():
                self.path.parent.mkdir(parents=True, exist_ok=True)
                revision = self._write_snapshot_unlocked([record], [], {})
                result = self._public_record(record, False)
                result["revision"] = revision
                return result
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                count = int(
                    connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
                )
                if count >= MAX_IMPORT_RECORDS:
                    raise PromptAssetValidationError(
                        "prompt asset library exceeds total record limit"
                    )
                if self._database_text_size(connection) + _record_text_size(record) > (
                    MAX_LIBRARY_TEXT_BYTES
                ):
                    raise PromptAssetValidationError(
                        "prompt asset library exceeds total text budget"
                    )
                if connection.execute(
                    "SELECT 1 FROM assets WHERE asset_id = ?", (identifier,)
                ).fetchone():
                    raise PromptAssetConflictError("prompt asset already exists")
                connection.execute(
                    """INSERT INTO assets (
                        asset_id, asset_type, name_zh, name_en, aliases, tags,
                        traits, categories, preview_key, preview_available,
                        provenance, source_key, is_custom, search_text,
                        category_tokens, trait_tokens, tag_tokens, created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    self._record_parameters(record),
                )
                revision = self._bump_revision(connection)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            result = self._public_record(record, False)
            result["revision"] = revision
            return result

    def update_custom(
        self, asset_id: str, changes: Mapping[str, Any]
    ) -> dict[str, Any]:
        identifier = self._validate_asset_id(asset_id)
        if not isinstance(changes, Mapping):
            raise PromptAssetValidationError("custom asset changes must be an object")
        forbidden = {"asset_id", "is_custom", "favorite", "created_at", "updated_at"}
        if forbidden.intersection(changes):
            raise PromptAssetValidationError("custom asset contains immutable fields")
        with self._write_lock():
            if not self.path.is_file():
                raise PromptAssetNotFoundError("prompt asset does not exist")
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM assets WHERE asset_id = ?", (identifier,)
                ).fetchone()
                if row is None:
                    raise PromptAssetNotFoundError("prompt asset does not exist")
                current = self._record_from_row(row)
                if not current["is_custom"]:
                    raise PromptAssetConflictError(
                        "only custom prompt assets can be edited"
                    )
                merged: dict[str, Any] = {
                    "asset_type": current["asset_type"],
                    "name_zh": current["name_zh"],
                    "name_en": current["name_en"],
                    "aliases": current["aliases"],
                    "tags": current["tags"],
                    "traits": current["traits"],
                    "categories": current["categories"],
                    "provenance": current["provenance"],
                    **dict(changes),
                }
                prepared = self._prepare_record(
                    merged,
                    "",
                    {},
                    custom=True,
                    forced_asset_id=identifier,
                )
                prepared["created_at"] = current["created_at"]
                prepared["updated_at"] = _utc_now()
                preview_fields = {
                    "preview_url",
                    "preview",
                    "thumbnail_url",
                    "thumbnail",
                    "image_url",
                    "image",
                }
                if not preview_fields.intersection(changes):
                    prepared["preview_key"] = current["preview_key"]
                    prepared["preview_available"] = current["preview_available"]
                projected_size = (
                    self._database_text_size(connection)
                    - _record_text_size(current)
                    + _record_text_size(prepared)
                )
                if projected_size > MAX_LIBRARY_TEXT_BYTES:
                    raise PromptAssetValidationError(
                        "prompt asset library exceeds total text budget"
                    )
                connection.execute(
                    """UPDATE assets SET
                        asset_type = ?, name_zh = ?, name_en = ?, aliases = ?,
                        tags = ?, traits = ?, categories = ?, preview_key = ?,
                        preview_available = ?, provenance = ?, source_key = ?,
                        is_custom = ?, search_text = ?, category_tokens = ?,
                        trait_tokens = ?, tag_tokens = ?, created_at = ?, updated_at = ?
                       WHERE asset_id = ?""",
                    (*self._record_parameters(prepared)[1:], identifier),
                )
                favourite = (
                    connection.execute(
                        "SELECT 1 FROM favourites WHERE asset_id = ?", (identifier,)
                    ).fetchone()
                    is not None
                )
                revision = self._bump_revision(connection)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            result = self._public_record(prepared, favourite)
            result["revision"] = revision
            return result

    def delete_custom(self, asset_id: str) -> dict[str, Any]:
        identifier = self._validate_asset_id(asset_id)
        with self._write_lock():
            if not self.path.is_file():
                raise PromptAssetNotFoundError("prompt asset does not exist")
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT is_custom FROM assets WHERE asset_id = ?", (identifier,)
                ).fetchone()
                if row is None:
                    raise PromptAssetNotFoundError("prompt asset does not exist")
                if not bool(row["is_custom"]):
                    raise PromptAssetConflictError(
                        "only custom prompt assets can be deleted"
                    )
                connection.execute(
                    "DELETE FROM assets WHERE asset_id = ?", (identifier,)
                )
                revision = self._bump_revision(connection)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            return {
                "deleted": True,
                "asset_id": identifier,
                "revision": revision,
            }

    def set_favorite(self, asset_id: str, favorite: bool = True) -> dict[str, Any]:
        identifier = self._validate_asset_id(asset_id)
        if not isinstance(favorite, bool):
            raise PromptAssetValidationError("favorite must be a boolean")
        with self._write_lock():
            if not self.path.is_file():
                raise PromptAssetNotFoundError("prompt asset does not exist")
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM assets WHERE asset_id = ?", (identifier,)
                ).fetchone()
                if row is None:
                    raise PromptAssetNotFoundError("prompt asset does not exist")
                if favorite:
                    connection.execute(
                        """INSERT INTO favourites(asset_id, created_at) VALUES (?, ?)
                           ON CONFLICT(asset_id) DO NOTHING""",
                        (identifier, _utc_now()),
                    )
                else:
                    connection.execute(
                        "DELETE FROM favourites WHERE asset_id = ?", (identifier,)
                    )
                revision = self._bump_revision(connection)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            result = self._public_record(self._record_from_row(row), favorite)
            result["revision"] = revision
            return result

    def clear_source(self, source: str) -> dict[str, Any]:
        """Remove one named imported source without touching custom assets."""

        source_label = _clean_text(source, "source", 256)
        if not source_label:
            raise PromptAssetValidationError("source is required")
        if "/" in source_label or "\\" in source_label:
            raise PromptAssetValidationError("clear_source does not accept paths")
        try:
            parsed = urlsplit(source_label)
        except ValueError as exc:
            raise PromptAssetValidationError("source is invalid") from exc
        if parsed.scheme or parsed.netloc:
            raise PromptAssetValidationError("clear_source accepts named sources only")
        if _contains_embedded_credential(source_label) or _sensitive_key(source_label):
            raise PromptAssetValidationError("source cannot contain credentials")
        source_key = normalize_asset_text(source_label)
        with self._write_lock():
            if not self.path.is_file():
                return {"removed": 0, "source": source_label, "revision": ""}
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                revision_row = connection.execute(
                    "SELECT value FROM metadata WHERE key = 'revision'"
                ).fetchone()
                cursor = connection.execute(
                    """DELETE FROM assets
                       WHERE source_key = ? AND is_custom = 0""",
                    (source_key,),
                )
                removed = max(0, int(cursor.rowcount))
                if removed:
                    revision = self._bump_revision(connection)
                else:
                    revision = str(revision_row[0]) if revision_row else ""
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            return {
                "removed": removed,
                "source": source_label,
                "revision": revision,
            }

    async def update_from_url(
        self,
        url: str,
        *,
        timeout: float = 30,
        max_bytes: int = DEFAULT_MAX_IMPORT_BYTES,
        provenance: Mapping[str, Any] | None = None,
        mode: str = "merge",
        allow_private_http: bool = False,
    ) -> dict[str, Any]:
        timeout_value = self._validated_timeout(timeout)
        if (
            isinstance(max_bytes, bool)
            or not isinstance(max_bytes, int)
            or not 0 < max_bytes <= MAX_REMOTE_IMPORT_BYTES
        ):
            raise PromptAssetValidationError("max_bytes exceeds allowed range")
        if not isinstance(allow_private_http, bool):
            raise PromptAssetValidationError("allow_private_http must be a boolean")
        parsed = urlsplit(_validate_url(url, "prompt asset URL"))
        if parsed.scheme != "https" and not allow_private_http:
            raise PromptAssetValidationError(
                "remote prompt asset import requires public HTTPS"
            )
        source = _canonical_remote_source(parsed)
        generation = self._begin_remote_update(source)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_value
        try:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            addresses = await asyncio.wait_for(
                self._validate_remote_host(
                    parsed, allow_private_http=allow_private_http
                ),
                timeout=min(remaining, 10.0),
            )
            connector = aiohttp.TCPConnector(
                resolver=_PinnedResolver(parsed.hostname or "", addresses),
                use_dns_cache=True,
            )
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            client_timeout = aiohttp.ClientTimeout(total=remaining)
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=client_timeout,
                auto_decompress=False,
            ) as session:
                async with session.get(
                    urlunsplit(parsed),
                    allow_redirects=False,
                    headers={"Accept-Encoding": "identity"},
                ) as response:
                    if 300 <= response.status < 400:
                        raise PromptAssetValidationError(
                            "prompt asset URL redirects are not allowed"
                        )
                    if response.status >= 400:
                        raise PromptAssetError(
                            f"prompt asset URL returned HTTP {response.status}"
                        )
                    content_encoding = response.headers.get(
                        "Content-Encoding", ""
                    ).strip()
                    if content_encoding and content_encoding.casefold() != "identity":
                        raise PromptAssetValidationError(
                            "compressed prompt asset responses are not allowed"
                        )
                    declared = response.headers.get("Content-Length", "")
                    if declared:
                        try:
                            declared_size = int(declared)
                        except ValueError:
                            declared_size = 0
                        if declared_size > max_bytes:
                            raise PromptAssetValidationError(
                                "prompt asset download exceeds size limit"
                            )
                    payload = bytearray()
                    total = 0
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        total += len(chunk)
                        if total > max_bytes:
                            raise PromptAssetValidationError(
                                "prompt asset download exceeds size limit"
                            )
                        payload.extend(chunk)
                    content_type = response.headers.get("Content-Type", "")
        except asyncio.CancelledError:
            self._invalidate_update(generation)
            self._finish_update(generation)
            self._finish_remote_update(source, generation)
            raise
        except PromptAssetError as exc:
            self._invalidate_update(generation)
            self._finish_update(generation)
            self._finish_remote_update(source, generation)
            self._last_error = str(exc)
            raise
        except asyncio.TimeoutError as exc:
            self._invalidate_update(generation)
            self._finish_update(generation)
            self._finish_remote_update(source, generation)
            self._last_error = "prompt asset download timed out"
            raise PromptAssetError(self._last_error) from exc
        except aiohttp.ClientError as exc:
            self._invalidate_update(generation)
            self._finish_update(generation)
            self._finish_remote_update(source, generation)
            self._last_error = f"prompt asset request failed: {type(exc).__name__}"
            raise PromptAssetError(self._last_error) from exc
        body = bytes(payload)
        del payload
        try:
            remote_provenance = {
                **_provenance(provenance),
                "transport": parsed.scheme,
            }
        except PromptAssetError:
            self._invalidate_update(generation)
            self._finish_update(generation)
            self._finish_remote_update(source, generation)
            raise
        worker = asyncio.create_task(
            asyncio.to_thread(
                self.import_bytes,
                body,
                source=source,
                content_type=content_type,
                provenance=remote_provenance,
                mode=mode,
                _expected_generation=generation,
            )
        )
        try:
            return await self._await_import_worker(worker, generation, deadline)
        finally:
            self._finish_remote_update(source, generation)

    @staticmethod
    async def _validate_remote_host(
        parsed: Any, *, allow_private_http: bool
    ) -> tuple[IPAddress, ...]:
        host = parsed.hostname or ""
        try:
            addresses = [ipaddress.ip_address(host.split("%", 1)[0])]
        except ValueError:
            try:
                loop = asyncio.get_running_loop()
                resolved = await loop.getaddrinfo(
                    host,
                    parsed.port or (443 if parsed.scheme == "https" else 80),
                    type=socket.SOCK_STREAM,
                )
                addresses = list(
                    {
                        ipaddress.ip_address(item[4][0].split("%", 1)[0])
                        for item in resolved
                    }
                )
            except (OSError, ValueError) as exc:
                raise PromptAssetValidationError(
                    "cannot resolve prompt asset host"
                ) from exc
        if not addresses:
            raise PromptAssetValidationError("prompt asset host has no usable address")
        if any(
            PromptAssetLibrary._address_is_forbidden(address) for address in addresses
        ):
            raise PromptAssetValidationError(
                "prompt asset host resolves to a forbidden address"
            )
        if parsed.scheme == "https":
            if any(not address.is_global for address in addresses):
                raise PromptAssetValidationError(
                    "remote HTTPS prompt assets must use public hosts"
                )
        elif not allow_private_http or any(
            not (address.is_private or address.is_loopback) for address in addresses
        ):
            raise PromptAssetValidationError(
                "plain HTTP prompt assets require an explicitly allowed private host"
            )
        return tuple(addresses)

    @staticmethod
    def _address_is_forbidden(address: IPAddress) -> bool:
        if (
            address.is_link_local
            or getattr(address, "is_site_local", False)
            or address.is_unspecified
            or address.is_multicast
            or address.is_reserved
        ):
            return True
        if isinstance(address, ipaddress.IPv6Address):
            embedded: list[IPAddress] = []
            if address.ipv4_mapped is not None:
                embedded.append(address.ipv4_mapped)
            if address.sixtofour is not None:
                embedded.append(address.sixtofour)
            if address.teredo is not None:
                embedded.extend(address.teredo)
            if any(PromptAssetLibrary._address_is_forbidden(item) for item in embedded):
                return True
            if any(not item.is_global for item in embedded):
                return True
        return False


__all__ = [
    "ASSET_TYPES",
    "DEFAULT_MAX_IMPORT_BYTES",
    "MAX_IMPORT_RECORDS",
    "MAX_IMPORT_BYTES",
    "MAX_PAGE",
    "MAX_PAGE_SIZE",
    "PromptAssetConflictError",
    "PromptAssetError",
    "PromptAssetLibrary",
    "PromptAssetNotFoundError",
    "PromptAssetValidationError",
    "normalize_asset_text",
    "stable_asset_id",
]
