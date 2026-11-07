"""Small, strict parsers for the application's untrusted HTTP boundary."""

from __future__ import annotations

import json
import math
from typing import Any
from urllib.parse import urlsplit

from aiohttp import web


SQLITE_MAX_INTEGER = 2**63 - 1
# Root objects/arrays count as one level. Application payloads are much
# shallower; keep the limit independent of Python's JSON recursion behavior.
MAX_JSON_DEPTH = 64


class LimitedMultipartStream:
    """Bound all multipart bytes, including chunked preambles and headers.

    ``Request.multipart()`` bypasses aiohttp's ``client_max_size`` read path.
    Counting only image chunks also leaves multipart framing unbounded.
    """

    def __init__(self, stream: Any, limit: int):
        self._stream = stream
        self._limit = limit
        self._consumed = 0

    def _count(self, value: bytes) -> bytes:
        self._consumed += len(value)
        if self._consumed > self._limit:
            raise web.HTTPRequestEntityTooLarge(max_size=self._limit, actual_size=self._consumed)
        return value

    async def read(self, size: int = -1) -> bytes:
        return self._count(await self._stream.read(size))

    async def readline(self, *args: Any, **kwargs: Any) -> bytes:
        return self._count(await self._stream.readline(*args, **kwargs))

    def at_eof(self) -> bool:
        return self._stream.at_eof()

    def unread_data(self, value: bytes) -> None:
        self._stream.unread_data(value)
        self._consumed -= len(value)


def parse_authority(value: str) -> tuple[str, int | None] | None:
    """Accept only a Host authority, never URL/userinfo/path lookalikes."""
    if not value or any(char.isspace() or char in "/\\?#@" for char in value):
        return None
    if value.endswith(":"):
        return None
    try:
        parsed = urlsplit("//" + value)
        hostname, port = parsed.hostname, parsed.port
    except ValueError:
        return None
    if not hostname or parsed.path or parsed.query or parsed.fragment:
        return None
    if port is not None and not 1 <= port <= 65535:
        return None
    return hostname.lower(), port


def parse_origin(value: str) -> tuple[str, str, int] | None:
    """A browser Origin is exactly an HTTP(S) scheme and authority."""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or parsed.path or parsed.query or parsed.fragment:
        return None
    # Reject empty ?/# components too; urlsplit otherwise erases them.
    if value != f"{parsed.scheme}://{parsed.netloc}":
        return None
    authority = parse_authority(parsed.netloc)
    if authority is None:
        return None
    hostname, port = authority
    return parsed.scheme, hostname, port or (443 if parsed.scheme == "https" else 80)


def _check_json_depth(value: str) -> None:
    """Bound containers before decoding; leave JSON syntax to json.loads."""
    depth = 0
    in_string = False
    escaped = False
    for char in value:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ValueError(f"JSON nesting exceeds {MAX_JSON_DEPTH} container levels")
        elif char in "]}":
            depth -= 1


def strict_json_loads(value: str) -> Any:
    """Reject excessive nesting, non-JSON constants and overflowing floats."""
    _check_json_depth(value)

    def invalid_constant(raw: str) -> Any:
        raise ValueError(f"Invalid JSON numeric constant: {raw}")

    def finite_float(raw: str) -> float:
        result = float(raw)
        if not math.isfinite(result):
            raise ValueError("JSON number is outside the finite range")
        return result

    return json.loads(value, parse_constant=invalid_constant, parse_float=finite_float)
