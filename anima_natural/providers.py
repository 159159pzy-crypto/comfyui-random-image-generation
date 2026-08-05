from __future__ import annotations

import asyncio
import base64
import ctypes
import json
import os
import re
import tempfile
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

import aiohttp

from anima_studio.domain import LoraSelection
from anima_studio.matching import (
    ArtistResolver,
    AssetRecord,
    ExactAliasResolver,
    LoraResolver,
)
from anima_studio.providers import (
    ProviderError,
    parse_tool_arguments,
    visible_text_content,
)


class ProviderRegistryError(ValueError):
    pass


class SecretStore(Protocol):
    def get(self, key: str) -> str: ...

    def set(self, key: str, value: str) -> None: ...

    def delete(self, key: str) -> None: ...


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


class MemorySecretStore:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, key: str) -> str:
        return self._values.get(key, "")

    def set(self, key: str, value: str) -> None:
        self._values[key] = value

    def delete(self, key: str) -> None:
        self._values.pop(key, None)


class UnavailableSecretStore:
    """Fail closed when an encrypted production secret store is unavailable."""

    def get(self, key: str) -> str:
        return ""

    def set(self, key: str, value: str) -> None:
        raise ProviderRegistryError("当前平台不支持 Windows DPAPI，无法保存 Provider API Key")

    def delete(self, key: str) -> None:
        return None


class DpapiSecretStore:
    """Persist user-scoped secrets encrypted with Windows DPAPI."""

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    def __init__(self, path: str | Path) -> None:
        if os.name != "nt":
            raise RuntimeError("DPAPI secret storage is only available on Windows")
        self.path = Path(path)
        self._values = self._load()

    def _load(self) -> dict[str, str]:
        if not self.path.is_file():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {str(key): str(item) for key, item in value.items()} if isinstance(value, dict) else {}

    @classmethod
    def _blob(cls, value: bytes) -> tuple["DpapiSecretStore._Blob", Any]:
        buffer = ctypes.create_string_buffer(value)
        blob = cls._Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        return blob, buffer

    @classmethod
    def _protect(cls, value: str) -> str:
        source, source_buffer = cls._blob(value.encode("utf-8"))
        destination = cls._Blob()
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        if not crypt32.CryptProtectData(
            ctypes.byref(source),
            "Anima Random Studio Provider Secret",
            None,
            None,
            None,
            0,
            ctypes.byref(destination),
        ):
            raise OSError("CryptProtectData failed")
        try:
            raw = ctypes.string_at(destination.pbData, destination.cbData)
            return base64.b64encode(raw).decode("ascii")
        finally:
            kernel32.LocalFree(destination.pbData)
            del source_buffer

    @classmethod
    def _unprotect(cls, value: str) -> str:
        encrypted = base64.b64decode(value.encode("ascii"), validate=True)
        source, source_buffer = cls._blob(encrypted)
        destination = cls._Blob()
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        if not crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, 0, ctypes.byref(destination)
        ):
            raise OSError("CryptUnprotectData failed")
        try:
            return ctypes.string_at(destination.pbData, destination.cbData).decode("utf-8")
        finally:
            kernel32.LocalFree(destination.pbData)
            del source_buffer

    def _save(self) -> None:
        _atomic_json(self.path, self._values)

    def get(self, key: str) -> str:
        encrypted = self._values.get(key, "")
        if not encrypted:
            return ""
        try:
            return self._unprotect(encrypted)
        except (OSError, ValueError):
            return ""

    def set(self, key: str, value: str) -> None:
        self._values[key] = self._protect(value)
        self._save()

    def delete(self, key: str) -> None:
        if key in self._values:
            del self._values[key]
            self._save()


@dataclass(frozen=True)
class ProviderProfile:
    id: str
    name: str
    base_url: str
    director_model: str = ""
    vision_model: str = ""
    embedding_model: str = ""
    rerank_model: str = ""
    timeout: int = 120
    enabled: bool = True

    def public(self, *, has_key: bool) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "base_url": self.base_url,
            "director_model": self.director_model,
            "vision_model": self.vision_model,
            "embedding_model": self.embedding_model,
            "rerank_model": self.rerank_model,
            "timeout": self.timeout,
            "enabled": self.enabled,
            "has_api_key": has_key,
        }


class ProviderRegistry:
    ROLES = ("director", "vision", "embedding", "rerank")

    def __init__(self, data_dir: str | Path, secret_store: SecretStore | None = None) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "providers.json"
        self.secret_store = secret_store or (
            DpapiSecretStore(self.data_dir / "provider_secrets.json")
            if os.name == "nt"
            else UnavailableSecretStore()
        )
        self._profiles: dict[str, ProviderProfile] = {}
        self._bindings: dict[str, str] = {role: "" for role in self.ROLES}
        self._load()

    @staticmethod
    def _normalize_url(value: Any) -> str:
        text = str(value or "").strip().rstrip("/")
        parsed = urlparse(text)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ProviderRegistryError("Provider Base URL 必须是 HTTP 或 HTTPS 地址")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ProviderRegistryError("Provider Base URL 不能包含凭据、查询参数或片段")
        return text

    @classmethod
    def _profile(cls, payload: Mapping[str, Any], profile_id: str = "") -> ProviderProfile:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ProviderRegistryError("Provider 名称不能为空")
        return ProviderProfile(
            id=profile_id or str(payload.get("id") or uuid.uuid4().hex[:12]),
            name=name[:100],
            base_url=cls._normalize_url(payload.get("base_url")),
            director_model=str(payload.get("director_model") or "").strip()[:200],
            vision_model=str(payload.get("vision_model") or "").strip()[:200],
            embedding_model=str(payload.get("embedding_model") or "").strip()[:200],
            rerank_model=str(payload.get("rerank_model") or "").strip()[:200],
            timeout=min(600, max(5, int(payload.get("timeout") or 120))),
            enabled=bool(payload.get("enabled", True)),
        )

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for item in raw.get("profiles") or [] if isinstance(raw, dict) else []:
            if not isinstance(item, Mapping):
                continue
            try:
                profile = self._profile(item, str(item.get("id") or ""))
            except (ProviderRegistryError, TypeError, ValueError):
                continue
            self._profiles[profile.id] = profile
        bindings = raw.get("bindings") if isinstance(raw, dict) else None
        if isinstance(bindings, Mapping):
            for role in self.ROLES:
                candidate = str(bindings.get(role) or "")
                self._bindings[role] = candidate if candidate in self._profiles else ""

    def _save(self) -> None:
        _atomic_json(
            self.path,
            {
                "version": 1,
                "profiles": [
                    {
                        key: value
                        for key, value in profile.public(has_key=False).items()
                        if key != "has_api_key"
                    }
                    for profile in self._profiles.values()
                ],
                "bindings": dict(self._bindings),
            },
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "profiles": [
                profile.public(has_key=bool(self.secret_store.get(profile.id)))
                for profile in self._profiles.values()
            ],
            "bindings": dict(self._bindings),
        }

    def get(self, profile_id: str) -> ProviderProfile:
        try:
            return self._profiles[str(profile_id)]
        except KeyError as exc:
            raise ProviderRegistryError("Provider 不存在") from exc

    def bound(self, role: str) -> ProviderProfile:
        if role not in self.ROLES:
            raise ProviderRegistryError("未知 Provider 角色")
        profile_id = self._bindings.get(role, "")
        if not profile_id:
            raise ProviderRegistryError(f"尚未绑定 {role} Provider")
        profile = self.get(profile_id)
        if not profile.enabled:
            raise ProviderRegistryError("所选 Provider 已停用")
        return profile

    def upsert(self, payload: Mapping[str, Any], profile_id: str = "") -> dict[str, Any]:
        profile = self._profile(payload, profile_id)
        self._profiles[profile.id] = profile
        api_key = payload.get("api_key")
        if isinstance(api_key, str) and api_key:
            self.secret_store.set(profile.id, api_key)
        if payload.get("clear_api_key") is True:
            self.secret_store.delete(profile.id)
        self._save()
        return profile.public(has_key=bool(self.secret_store.get(profile.id)))

    def delete(self, profile_id: str) -> None:
        profile_id = str(profile_id)
        if profile_id not in self._profiles:
            raise ProviderRegistryError("Provider 不存在")
        del self._profiles[profile_id]
        self.secret_store.delete(profile_id)
        for role, bound_id in tuple(self._bindings.items()):
            if bound_id == profile_id:
                self._bindings[role] = ""
        self._save()

    def set_bindings(self, payload: Mapping[str, Any]) -> dict[str, str]:
        for role in self.ROLES:
            if role not in payload:
                continue
            profile_id = str(payload.get(role) or "")
            if profile_id and profile_id not in self._profiles:
                raise ProviderRegistryError(f"{role} Provider 不存在")
            self._bindings[role] = profile_id
        self._save()
        return dict(self._bindings)

    def api_key(self, profile_id: str) -> str:
        return self.secret_store.get(profile_id)


class OpenAIProviderClient:
    def __init__(self, registry: ProviderRegistry) -> None:
        self.registry = registry
        self.session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self.session is not None and not self.session.closed:
            await self.session.close()

    async def _session(self) -> aiohttp.ClientSession:
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    @staticmethod
    def _endpoint(profile: ProviderProfile, suffix: str) -> str:
        base = profile.base_url.rstrip("/")
        return f"{base}{suffix}" if base.endswith("/v1") else f"{base}/v1{suffix}"

    def _headers(self, profile: ProviderProfile) -> dict[str, str]:
        key = self.registry.api_key(profile.id)
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    async def _json(self, profile: ProviderProfile, method: str, suffix: str, **kwargs: Any) -> Any:
        session = await self._session()
        timeout = aiohttp.ClientTimeout(total=profile.timeout)
        try:
            async with session.request(
                method,
                self._endpoint(profile, suffix),
                headers=self._headers(profile),
                timeout=timeout,
                **kwargs,
            ) as response:
                text = await response.text()
                try:
                    data = json.loads(text)
                except ValueError:
                    data = None
                if response.status >= 400:
                    message = "Provider 请求失败"
                    if isinstance(data, Mapping):
                        error = data.get("error")
                        if isinstance(error, Mapping):
                            message = str(error.get("message") or message)
                    raise ProviderRegistryError(f"Provider HTTP {response.status}: {message[:300]}")
                if data is None:
                    raise ProviderRegistryError("Provider 返回了无效 JSON")
                return data
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ProviderRegistryError(f"无法连接 Provider: {type(exc).__name__}") from exc

    async def _stream_json(
        self,
        profile: ProviderProfile,
        suffix: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        session = await self._session()
        timeout = aiohttp.ClientTimeout(total=profile.timeout)
        content_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        finish_reason = ""
        try:
            async with session.post(
                self._endpoint(profile, suffix),
                headers=self._headers(profile),
                timeout=timeout,
                json=dict(payload),
            ) as response:
                if response.status >= 400:
                    raw = await response.text()
                    try:
                        data = json.loads(raw)
                    except ValueError:
                        data = None
                    message = "Provider 请求失败"
                    if isinstance(data, Mapping) and isinstance(data.get("error"), Mapping):
                        message = str(data["error"].get("message") or message)
                    raise ProviderRegistryError(
                        f"Provider HTTP {response.status}: {message[:300]}"
                    )
                buffer = b""
                async for chunk in response.content.iter_chunked(16 * 1024):
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        line = line.strip()
                        if not line or line.startswith(b":"):
                            continue
                        if not line.startswith(b"data:"):
                            continue
                        value = line[5:].strip()
                        if value == b"[DONE]":
                            buffer = b""
                            break
                        try:
                            event = json.loads(value.decode("utf-8"))
                        except (UnicodeDecodeError, ValueError) as exc:
                            raise ProviderRegistryError("Provider 流式响应包含无效 JSON") from exc
                        choices = event.get("choices") if isinstance(event, Mapping) else None
                        if not isinstance(choices, list):
                            continue
                        for choice in choices:
                            if not isinstance(choice, Mapping):
                                continue
                            finish_reason = str(choice.get("finish_reason") or finish_reason)
                            delta = choice.get("delta")
                            if not isinstance(delta, Mapping):
                                continue
                            content = delta.get("content")
                            if isinstance(content, str):
                                content_parts.append(content)
                            for call in delta.get("tool_calls") or []:
                                if not isinstance(call, Mapping):
                                    continue
                                index = int(call.get("index") or 0)
                                current = tool_calls.setdefault(
                                    index,
                                    {
                                        "id": "",
                                        "type": "function",
                                        "function": {"name": "", "arguments": ""},
                                    },
                                )
                                if call.get("id"):
                                    current["id"] = str(call["id"])
                                function = call.get("function")
                                if isinstance(function, Mapping):
                                    current_function = current["function"]
                                    if function.get("name"):
                                        current_function["name"] += str(function["name"])
                                    if function.get("arguments"):
                                        current_function["arguments"] += str(function["arguments"])
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "".join(content_parts),
                                "tool_calls": [tool_calls[key] for key in sorted(tool_calls)],
                            },
                            "finish_reason": finish_reason,
                        }
                    ]
                }
        except ProviderRegistryError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ProviderRegistryError(f"无法连接 Provider: {type(exc).__name__}") from exc

    async def list_models(self, profile_id: str) -> list[str]:
        profile = self.registry.get(profile_id)
        data = await self._json(profile, "GET", "/models")
        return sorted(
            str(item.get("id"))
            for item in data.get("data") or []
            if isinstance(item, Mapping) and item.get("id")
        )

    async def chat(
        self,
        profile: ProviderProfile,
        *,
        model: str,
        prompt: str,
        system_prompt: str = "",
        image_paths: list[str] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1600,
        stream: bool = False,
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: str | Mapping[str, Any] | None = None,
        messages: Sequence[Mapping[str, Any]] | None = None,
        response_format: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not model:
            raise ProviderRegistryError("Provider 未配置对应模型")
        request_messages: list[dict[str, Any]] = [dict(item) for item in messages or ()]
        if not request_messages:
            if system_prompt:
                request_messages.append({"role": "system", "content": system_prompt})
            if image_paths:
                content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
                for image_path in image_paths:
                    path = Path(image_path)
                    if not path.is_file():
                        raise ProviderRegistryError("输入图片不存在")
                    suffix = path.suffix.casefold()
                    media_type = (
                        "image/png"
                        if suffix == ".png"
                        else "image/webp"
                        if suffix == ".webp"
                        else "image/jpeg"
                    )
                    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{encoded}"},
                        }
                    )
                request_messages.append({"role": "user", "content": content})
            else:
                request_messages.append({"role": "user", "content": prompt})
        payload: dict[str, Any] = {
            "model": model,
            "messages": request_messages,
            "temperature": max(0.0, min(2.0, float(temperature))),
            "max_tokens": max(1, min(32000, int(max_tokens))),
            "stream": bool(stream),
        }
        if tools:
            payload["tools"] = [dict(item) for item in tools]
            payload["tool_choice"] = tool_choice or "auto"
        if response_format:
            payload["response_format"] = dict(response_format)
        if stream:
            return await self._stream_json(profile, "/chat/completions", payload)
        data = await self._json(profile, "POST", "/chat/completions", json=payload)
        if not isinstance(data, Mapping):
            raise ProviderRegistryError("Provider 返回格式无效")
        return dict(data)

    @staticmethod
    def _message(data: Mapping[str, Any]) -> Mapping[str, Any]:
        choices = data.get("choices") if isinstance(data, Mapping) else None
        if not isinstance(choices, list) or not choices:
            raise ProviderRegistryError("Provider 没有返回 choices")
        message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
        if not isinstance(message, Mapping):
            raise ProviderRegistryError("Provider 没有返回 message")
        return message

    async def complete(
        self,
        profile: ProviderProfile,
        *,
        model: str,
        prompt: str,
        system_prompt: str = "",
        image_paths: list[str] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1600,
        stream: bool = False,
    ) -> str:
        response_format = (
            {"type": "json_object"}
            if image_paths and "json" in system_prompt.casefold()
            else None
        )
        effective_max_tokens = max_tokens
        for attempt in range(3):
            try:
                data = await self.chat(
                    profile,
                    model=model,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    image_paths=image_paths,
                    temperature=temperature,
                    max_tokens=effective_max_tokens,
                    stream=stream,
                    response_format=response_format if attempt == 0 else None,
                )
            except ProviderRegistryError as exc:
                if (
                    "Provider HTTP 400: Request contains an invalid argument." not in str(exc)
                    or attempt == 2
                ):
                    raise
                await asyncio.sleep(0.2 * (attempt + 1))
                continue
            message = self._message(data)
            content = visible_text_content(message.get("content"))
            if not content:
                content = visible_text_content(
                    message.get("output_text", message.get("text"))
                )
            if content.strip():
                return content.strip()
            if image_paths and attempt < 2:
                # Some reasoning-capable OpenAI-compatible gateways spend the
                # initial output budget before emitting public vision content.
                # Retry with more room without reading or persisting hidden
                # reasoning fields from the response.
                effective_max_tokens = min(
                    32000, max(4096, int(effective_max_tokens) * 2)
                )
                await asyncio.sleep(0.2 * (attempt + 1))
                continue
            raise ProviderRegistryError("Provider 没有返回可见文本")
        raise ProviderRegistryError("Provider 没有返回可见文本")

    async def embeddings(
        self,
        profile: ProviderProfile,
        inputs: Sequence[str],
        *,
        model: str = "",
    ) -> list[list[float]]:
        selected_model = model or profile.embedding_model
        if not selected_model:
            raise ProviderRegistryError("Provider 未配置 Embedding 模型")
        data = await self._json(
            profile,
            "POST",
            "/embeddings",
            json={"model": selected_model, "input": [str(item) for item in inputs]},
        )
        rows = data.get("data") if isinstance(data, Mapping) else None
        if not isinstance(rows, list) or len(rows) != len(inputs):
            raise ProviderRegistryError("Embedding 返回数量不匹配")
        ordered = sorted(
            (item for item in rows if isinstance(item, Mapping)),
            key=lambda item: int(item.get("index") or 0),
        )
        vectors: list[list[float]] = []
        for item in ordered:
            vector = item.get("embedding")
            if not isinstance(vector, list) or not vector:
                raise ProviderRegistryError("Embedding 返回向量无效")
            vectors.append([float(value) for value in vector])
        return vectors

    async def rerank(
        self,
        profile: ProviderProfile,
        query: str,
        documents: Sequence[str],
        *,
        top_n: int = 8,
        model: str = "",
    ) -> list[dict[str, Any]]:
        selected_model = model or profile.rerank_model
        if not selected_model:
            raise ProviderRegistryError("Provider 未配置 Rerank 模型")
        data = await self._json(
            profile,
            "POST",
            "/rerank",
            json={
                "model": selected_model,
                "query": str(query),
                "documents": [str(item) for item in documents],
                "top_n": min(len(documents), max(1, int(top_n))),
            },
        )
        rows = data.get("results") if isinstance(data, Mapping) else None
        if not isinstance(rows, list):
            raise ProviderRegistryError("Rerank 返回格式无效")
        result: list[dict[str, Any]] = []
        for item in rows:
            if not isinstance(item, Mapping):
                continue
            index = int(item.get("index"))
            if not 0 <= index < len(documents):
                raise ProviderRegistryError("Rerank 返回越界索引")
            result.append(
                {
                    "index": index,
                    "relevance_score": float(
                        item.get("relevance_score", item.get("score", 0.0))
                    ),
                }
            )
        if not result:
            raise ProviderRegistryError("Rerank 没有返回候选")
        return result

    async def bounded_tool_loop(
        self,
        profile: ProviderProfile,
        *,
        model: str,
        prompt: str,
        system_prompt: str,
        tools: Mapping[str, tuple[Mapping[str, Any], Callable[[Mapping[str, Any]], Awaitable[Any] | Any]]],
        max_steps: int = 4,
        max_result_chars: int = 12000,
    ) -> str:
        definitions = [dict(item[0]) for item in tools.values()]
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        output_budget = 2000
        for step in range(min(8, max(1, int(max_steps)))):
            empty_attempt = 0
            while True:
                data = await self.chat(
                    profile,
                    model=model,
                    prompt="",
                    tools=definitions,
                    messages=messages,
                    max_tokens=output_budget,
                )
                message = dict(self._message(data))
                calls = message.get("tool_calls") or []
                if calls:
                    break
                content = visible_text_content(message.get("content"))
                if not content:
                    content = visible_text_content(
                        message.get("output_text", message.get("text"))
                    )
                if content.strip():
                    return content.strip()
                if empty_attempt >= 2:
                    raise ProviderRegistryError(
                        "Provider 工具循环没有返回最终文本"
                    )
                output_budget = min(32000, max(4096, output_budget * 2))
                empty_attempt += 1
                await asyncio.sleep(0.2 * empty_attempt)
            if not isinstance(calls, Sequence) or isinstance(calls, (str, bytes, bytearray)):
                raise ProviderRegistryError("Provider tool_calls 格式无效")
            if not visible_text_content(message.get("content")).strip():
                message["content"] = None
            prepared_calls: list[tuple[str, str, dict[str, Any]]] = []
            normalized_calls: list[dict[str, Any]] = []
            for index, call in enumerate(calls):
                function = call.get("function") if isinstance(call, Mapping) else None
                name = str(function.get("name") or "") if isinstance(function, Mapping) else ""
                if name not in tools:
                    raise ProviderRegistryError("Provider 调用了未授权工具")
                try:
                    arguments = parse_tool_arguments(function.get("arguments"))
                except ProviderError as exc:
                    raise ProviderRegistryError(str(exc)) from exc
                call_id = str(call.get("id") or f"call_{step}_{index}")
                prepared_calls.append((call_id, name, arguments))
                normalized_calls.append(
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(
                                arguments, ensure_ascii=False, separators=(",", ":")
                            ),
                        },
                    }
                )
            message["role"] = "assistant"
            message["tool_calls"] = normalized_calls
            messages.append(message)
            for call_id, name, arguments in prepared_calls:
                output = tools[name][1](arguments)
                if asyncio.iscoroutine(output):
                    output = await output
                serialized = json.dumps(output, ensure_ascii=False, separators=(",", ":"))
                if len(serialized) > max_result_chars:
                    raise ProviderRegistryError("本地工具返回体超过限制")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": serialized,
                    }
                )
            output_budget = max(4096, output_budget)
        raise ProviderRegistryError("Provider 工具循环超过最大步骤")

    async def test(self, profile_id: str) -> dict[str, Any]:
        profile = self.registry.get(profile_id)
        models = await self.list_models(profile_id)
        return {"ok": True, "models": models, "model_count": len(models), "profile": profile.name}


class NativeProviderGateway:
    """Role-based provider access with no chat framework or event surface."""

    def __init__(self, registry: ProviderRegistry, client: OpenAIProviderClient) -> None:
        self.registry = registry
        self.client = client

    def profile(self, role: str) -> ProviderProfile:
        return self.registry.bound(role)

    async def complete(
        self,
        role: str,
        *,
        prompt: str,
        system_prompt: str = "",
        image_paths: Sequence[str] = (),
        temperature: float = 0.2,
        max_tokens: int = 1600,
    ) -> tuple[str, str]:
        profile = self.profile(role)
        model = profile.vision_model if image_paths else profile.director_model
        if role == "vision":
            model = profile.vision_model
        text = await self.client.complete(
            profile,
            model=model,
            prompt=prompt,
            system_prompt=system_prompt,
            image_paths=list(image_paths) or None,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return text, profile.id

    async def embeddings(self, inputs: Sequence[str]) -> list[list[float]]:
        profile = self.profile("embedding")
        return await self.client.embeddings(profile, inputs)

    async def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        top_n: int = 8,
    ) -> list[dict[str, Any]]:
        profile = self.profile("rerank")
        return await self.client.rerank(profile, query, documents, top_n=top_n)

    async def tool_loop(
        self,
        *,
        prompt: str,
        system_prompt: str,
        tools: Mapping[
            str,
            tuple[
                Mapping[str, Any],
                Callable[[Mapping[str, Any]], Awaitable[Any] | Any],
            ],
        ],
        max_steps: int = 4,
    ) -> tuple[str, str]:
        profile = self.profile("director")
        text = await self.client.bounded_tool_loop(
            profile,
            model=profile.director_model,
            prompt=prompt,
            system_prompt=system_prompt,
            tools=tools,
            max_steps=max_steps,
        )
        return text, profile.id


class NativePlanningError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class NativePictureInstruction:
    prompt: str
    negative_prompt: str = ""
    pipeline: str = ""
    character_queries: tuple[str, ...] = ()
    artist_tags: tuple[str, ...] = ()
    loras: tuple[LoraSelection, ...] = ()
    style_preset_id: str = ""
    prompt_asset_ids: tuple[str, ...] = ()
    prompt_plan_id: str = ""
    selected_preset: Mapping[str, Any] = field(default_factory=dict)
    selected_prompt_assets: tuple[Mapping[str, Any], ...] = ()
    selected_prompt_plan: Mapping[str, Any] = field(default_factory=dict)
    matches: tuple[Mapping[str, Any], ...] = ()
    requires_confirmation: tuple[Mapping[str, Any], ...] = ()
    sources: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NativeEditInstruction:
    prompt: str
    negative_prompt: str = ""
    mode: str = "quick"


@dataclass(frozen=True, slots=True)
class NativeReverseCharacter:
    name: str
    source_work: str = ""
    confidence: float = 0.0
    gender: str = ""
    appearance_tags: tuple[str, ...] = ()
    outfit_tags: tuple[str, ...] = ()
    action_tags: tuple[str, ...] = ()
    position: str = ""


@dataclass(frozen=True, slots=True)
class NativeReverseResult:
    positive_tags: str
    negative_tags: str = ""
    composition: str = ""
    scene_description_zh: str = ""
    characters: tuple[NativeReverseCharacter, ...] = ()
    style_notes: str = ""
    text_in_image: tuple[str, ...] = ()
    uncertain_terms: tuple[str, ...] = ()
    confidence: float = 0.0


def parse_json_object(text: str) -> dict[str, Any]:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.IGNORECASE)
    try:
        parsed = json.loads(value)
    except ValueError:
        start = value.find("{")
        end = value.rfind("}")
        if start < 0 or end <= start:
            raise NativePlanningError("provider did not return a JSON object") from None
        try:
            parsed = json.loads(value[start : end + 1])
        except ValueError as exc:
            raise NativePlanningError("provider returned invalid JSON") from exc
    if not isinstance(parsed, Mapping):
        raise NativePlanningError("provider JSON root must be an object")
    return dict(parsed)


def _text_list(value: Any, *, limit: int = 64) -> tuple[str, ...]:
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray)):
        return ()
    return tuple(
        dict.fromkeys(str(item).strip()[:300] for item in value if str(item).strip())
    )[:limit]


def _planning_record(value: Mapping[str, Any], kind: str) -> AssetRecord[Mapping[str, Any]]:
    item = dict(value)
    identifier = str(item.get("id") or item.get("asset_id") or item.get("filename") or "").strip()
    names = {
        "preset": ("name", "title"),
        "prompt_asset": ("name", "name_zh", "name_en", "title"),
        "prompt_plan": ("name", "title", "source_text", "id"),
        "character_alias": ("name", "canonical_tag", "id"),
    }.get(kind, ("name", "title"))
    name = next((str(item.get(key) or "").strip() for key in names if item.get(key)), identifier)
    aliases = item.get("aliases") or ()
    if isinstance(aliases, str):
        aliases = (aliases,)
    extra_aliases: list[str] = [str(alias).strip() for alias in aliases if str(alias).strip()]
    for key in names:
        candidate = str(item.get(key) or "").strip()
        if candidate and candidate != name:
            extra_aliases.append(candidate)
    return AssetRecord(
        identifier or name,
        name,
        tuple(dict.fromkeys(extra_aliases)),
        item,
    )


class NativePlanningToolSession:
    SOURCE_FIELDS = {
        "artist": "artist_tags",
        "lora": "loras",
        "preset": "style_preset_id",
        "prompt_asset": "prompt_asset_ids",
        "prompt_plan": "prompt_plan_id",
        "character_alias": "character_queries",
    }

    def __init__(self, resolvers: Mapping[str, ExactAliasResolver[Any]]) -> None:
        self.resolvers = dict(resolvers)
        self.matches: list[dict[str, Any]] = []
        self.sources: dict[str, list[dict[str, str]]] = {}
        self.selected: dict[str, list[Any]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.resolvers)

    def _resolve(self, kind: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        match = self.resolvers[kind].resolve(query)
        public = match.to_dict()
        self.matches.append(public)
        if match.selected is not None:
            selected = match.selected
            self.selected.setdefault(kind, []).append(selected.value)
            field = self.SOURCE_FIELDS[kind]
            self.sources.setdefault(field, []).append(
                {"kind": kind, "id": selected.id, "matched_by": selected.matched_by}
            )
            public["selected"] = selected.to_dict()
        return public

    def definitions(
        self,
    ) -> dict[str, tuple[dict[str, Any], Callable[[Mapping[str, Any]], dict[str, Any]]]]:
        result: dict[
            str,
            tuple[dict[str, Any], Callable[[Mapping[str, Any]], dict[str, Any]]],
        ] = {}
        labels = {
            "artist": "artist or multilingual artist alias",
            "lora": "local LoRA filename or alias",
            "preset": "style preset name or alias",
            "prompt_asset": "prompt asset name or alias",
            "prompt_plan": "saved Prompt Plan name or identifier",
            "character_alias": "character name or multilingual alias",
        }
        for kind in self.resolvers:
            name = f"resolve_{kind}"
            schema = {
                "type": "function",
                "function": {
                    "name": name,
                    "description": (
                        f"Resolve an explicitly requested {labels[kind]} against local data. "
                        "Exact and unique alias matches are usable; fuzzy matches require user confirmation."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                },
            }
            result[name] = (schema, lambda arguments, selected_kind=kind: self._resolve(selected_kind, arguments))
        return result

    @property
    def confirmations(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(match for match in self.matches if match.get("needs_confirmation") is True)


class NativePlanningToolRegistry:
    """Build bounded, read-only resolver sessions for one natural-language plan."""

    def __init__(
        self,
        *,
        artists: Sequence[Mapping[str, Any] | str] = (),
        loras: Sequence[Mapping[str, Any] | str] = (),
        presets: Sequence[Mapping[str, Any]] = (),
        prompt_assets: Sequence[Mapping[str, Any]] = (),
        prompt_plans: Sequence[Mapping[str, Any]] = (),
        character_aliases: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        resolvers: dict[str, ExactAliasResolver[Any]] = {}
        if artists:
            resolvers["artist"] = ArtistResolver(artists)
        if loras:
            resolvers["lora"] = LoraResolver(loras)
        for kind, values in (
            ("preset", presets),
            ("prompt_asset", prompt_assets),
            ("prompt_plan", prompt_plans),
            ("character_alias", character_aliases),
        ):
            records = tuple(_planning_record(value, kind) for value in values)
            if records:
                resolvers[kind] = ExactAliasResolver(kind, records)
        self._resolvers = resolvers

    @property
    def enabled(self) -> bool:
        return bool(self._resolvers)

    def session(self) -> NativePlanningToolSession:
        return NativePlanningToolSession(self._resolvers)


class NativeNaturalPlanner:
    def __init__(
        self,
        gateway: NativeProviderGateway,
        reference_path: str | Path,
        *,
        tools: NativePlanningToolRegistry | Callable[[], NativePlanningToolRegistry] | None = None,
    ) -> None:
        self.gateway = gateway
        self.tools = tools or NativePlanningToolRegistry()
        path = Path(reference_path)
        try:
            self.reference = path.read_text(encoding="utf-8")[:30_000]
        except OSError:
            self.reference = ""

    async def generate_instruction(
        self,
        text: str,
        *,
        task_kind: str = "draw",
        runtime_capabilities: Sequence[str] = (),
        compose_result: bool = False,
    ) -> tuple[NativePictureInstruction, str]:
        del compose_result
        system = (
            "You are the planning component of a local Anima image workstation. "
            "Return one JSON object with positive_prompt, negative_prompt, pipeline, "
            "and character_queries. Never invent local model or LoRA filenames. "
            "pipeline must be empty, base, rtx, or iterative.\n"
            f"Task: {task_kind}. Available capabilities: {', '.join(runtime_capabilities) or 'none'}.\n"
            + self.reference
        )
        registry = self.tools() if callable(self.tools) else self.tools
        session = registry.session()
        if session.enabled:
            system += (
                "\nCall the matching tool for every explicitly requested artist, LoRA, style "
                "preset, Prompt Asset, Prompt Plan, or character alias. Use only matched results. "
                "Never select needs_confirmation candidates."
            )
            raw, provider_id = await self.gateway.tool_loop(
                prompt=str(text),
                system_prompt=system,
                tools=session.definitions(),
                max_steps=8,
            )
        else:
            raw, provider_id = await self.gateway.complete(
                "director",
                prompt=str(text),
                system_prompt=system,
                temperature=0.2,
                max_tokens=1600,
            )
        result = parse_json_object(raw)
        prompt = str(result.get("positive_prompt") or result.get("prompt") or "").strip()
        if not prompt:
            raise NativePlanningError("director returned an empty positive prompt")
        pipeline = str(result.get("pipeline") or "").strip().casefold()
        if pipeline not in {"", "base", "rtx", "iterative"}:
            pipeline = ""
        artists = tuple(
            str(value or "").strip()
            for value in session.selected.get("artist", ())
            if str(value or "").strip()
        )
        loras = tuple(
            value for value in session.selected.get("lora", ()) if isinstance(value, LoraSelection)
        )
        preset_values = session.selected.get("preset", ())
        prompt_asset_values = session.selected.get("prompt_asset", ())
        prompt_plan_values = session.selected.get("prompt_plan", ())
        character_values = session.selected.get("character_alias", ())
        resolved_characters = tuple(
            str(value.get("canonical_tag") or value.get("name") or "").strip()
            for value in character_values
            if isinstance(value, Mapping)
            and str(value.get("canonical_tag") or value.get("name") or "").strip()
        )
        return (
            NativePictureInstruction(
                prompt=prompt,
                negative_prompt=str(result.get("negative_prompt") or "").strip(),
                pipeline=pipeline,
                character_queries=tuple(
                    dict.fromkeys((*_text_list(result.get("character_queries")), *resolved_characters))
                ),
                artist_tags=artists,
                loras=loras,
                style_preset_id=(
                    str(preset_values[0].get("id") or "")
                    if preset_values and isinstance(preset_values[0], Mapping)
                    else ""
                ),
                prompt_asset_ids=tuple(
                    str(value.get("id") or value.get("asset_id") or "")
                    for value in prompt_asset_values
                    if isinstance(value, Mapping) and (value.get("id") or value.get("asset_id"))
                ),
                prompt_plan_id=(
                    str(prompt_plan_values[0].get("id") or "")
                    if prompt_plan_values and isinstance(prompt_plan_values[0], Mapping)
                    else ""
                ),
                selected_preset=(
                    dict(preset_values[0])
                    if preset_values and isinstance(preset_values[0], Mapping)
                    else {}
                ),
                selected_prompt_assets=tuple(
                    dict(value) for value in prompt_asset_values if isinstance(value, Mapping)
                ),
                selected_prompt_plan=(
                    dict(prompt_plan_values[0])
                    if prompt_plan_values and isinstance(prompt_plan_values[0], Mapping)
                    else {}
                ),
                matches=tuple(session.matches),
                requires_confirmation=session.confirmations,
                sources={key: list(value) for key, value in session.sources.items()},
            ),
            provider_id,
        )

    async def generate_edit_instruction(
        self,
        text: str,
        *,
        runtime_capabilities: Sequence[str] = (),
    ) -> tuple[NativeEditInstruction, str]:
        system = (
            "Plan one local image edit. Return JSON only with positive_prompt, "
            "negative_prompt and mode. mode must be quick or lanpaint. Do not infer "
            "a mask or add subjects not requested by the user. "
            f"Available capabilities: {', '.join(runtime_capabilities) or 'none'}."
        )
        raw, provider_id = await self.gateway.complete(
            "director",
            prompt=str(text),
            system_prompt=system,
            temperature=0.1,
            max_tokens=1200,
        )
        result = parse_json_object(raw)
        prompt = str(result.get("positive_prompt") or result.get("prompt") or "").strip()
        if not prompt:
            raise NativePlanningError("director returned an empty edit prompt")
        mode = str(result.get("mode") or "quick").strip().casefold()
        if mode not in {"quick", "lanpaint"}:
            mode = "quick"
        return (
            NativeEditInstruction(
                prompt,
                str(result.get("negative_prompt") or "").strip(),
                mode,
            ),
            provider_id,
        )


@dataclass(frozen=True, slots=True)
class NativeReverseConfig:
    reverse_prompt_max_tokens: int


class NativeReversePrompt:
    def __init__(self, gateway: NativeProviderGateway, *, max_tokens: int = 4096) -> None:
        self.gateway = gateway
        self.max_tokens = max(512, min(16_000, int(max_tokens)))
        self._settings = NativeReverseConfig(self.max_tokens)

    async def reverse(
        self,
        image_path: str | Path,
        supplement: str = "",
        *,
        profile: str = "full",
    ) -> tuple[NativeReverseResult, str]:
        swap = profile == "swap"
        system = (
            "Analyze only visible image facts and return one JSON object. Required: "
            "positive_tags and negative_tags. Optional: composition, scene_description_zh, "
            "style_notes, text_in_image, uncertain_terms, confidence, characters. Each "
            "character has name, source_work, confidence, gender, appearance_tags, "
            "outfit_tags, action_tags, position. Omit uncertain identity. JSON only."
        )
        prompt = "Analyze this image for character replacement." if swap else "Analyze this image."
        if supplement.strip():
            prompt += f" User request context: {supplement.strip()[:2000]}"
        raw, provider_id = await self.gateway.complete(
            "vision",
            prompt=prompt,
            system_prompt=system,
            image_paths=(str(image_path),),
            temperature=0.0,
            max_tokens=self.max_tokens,
        )
        result = parse_json_object(raw)
        positive = str(result.get("positive_tags") or result.get("positive_prompt") or "").strip()
        if not positive:
            raise NativePlanningError("vision provider returned no positive tags")
        characters: list[NativeReverseCharacter] = []
        for raw_character in result.get("characters") or ():
            if not isinstance(raw_character, Mapping):
                continue
            characters.append(
                NativeReverseCharacter(
                    name=str(raw_character.get("name") or "").strip()[:200],
                    source_work=str(raw_character.get("source_work") or "").strip()[:200],
                    confidence=max(0.0, min(1.0, float(raw_character.get("confidence") or 0))),
                    gender=str(raw_character.get("gender") or "").strip()[:50],
                    appearance_tags=_text_list(raw_character.get("appearance_tags")),
                    outfit_tags=_text_list(raw_character.get("outfit_tags")),
                    action_tags=_text_list(raw_character.get("action_tags")),
                    position=str(raw_character.get("position") or "").strip()[:100],
                )
            )
        return (
            NativeReverseResult(
                positive_tags=positive,
                negative_tags=str(result.get("negative_tags") or result.get("negative_prompt") or "").strip(),
                composition=str(result.get("composition") or "").strip(),
                scene_description_zh=str(
                    result.get("scene_description_zh") or result.get("scene_description") or ""
                ).strip(),
                characters=tuple(characters[:16]),
                style_notes=str(result.get("style_notes") or "").strip(),
                text_in_image=_text_list(result.get("text_in_image")),
                uncertain_terms=_text_list(result.get("uncertain_terms")),
                confidence=max(0.0, min(1.0, float(result.get("confidence") or 0))),
            ),
            provider_id,
        )
