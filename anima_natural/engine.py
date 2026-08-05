from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from anima_studio import GenerationIntent, LoraSelection
from anima_studio.natural_runtime import (
    LoraIdentityExpectation,
    NativeNaturalSettings,
    WorkflowGenerationOptions,
)

from .assets import AssetStore, NaturalAsset
from .providers import (
    NativeNaturalPlanner,
    NativePlanningError,
    NativePlanningToolRegistry,
    NativeProviderGateway,
    NativeReversePrompt,
    OpenAIProviderClient,
    ProviderRegistry,
    ProviderRegistryError,
    SecretStore,
    parse_json_object,
)
from .workspace_data import NaturalWorkspaceData
from anima_studio.workflow_builder import (
    ControlWorkflowBuilder,
    ImageWorkflowBuilder,
    Img2ImgWorkflowBuilder,
    InpaintWorkflowBuilder,
    WorkflowBuilder,
    WorkflowError,
)
from anima_studio.natural_services import (
    CharacterSwapError,
    CharacterSwapPlan,
    CharacterSwapPlanner,
    CharacterSwapRequest,
    DanbooruIndexError,
    DanbooruTagIndex,
    LoraRecord,
    LoraSemanticIndex,
    ObservedSubject,
    PromptComposer,
    PromptDiagnosticsStore,
    ReversePromptError,
    SubjectSelectionError,
    normalize_semantic_identity_payload,
    parse_natural_character_swap,
    resolve_character_identity,
    select_observed_subject,
    semantic_identity_lookup_hints,
)


class NaturalEngineError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "natural_error",
        status: int = 400,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.status = status
        self.details = dict(details or {})
        super().__init__(message)


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


class NaturalEngine:
    MAX_PLANS = 100
    PLAN_OVERRIDE_FIELDS = frozenset(
        {
            "positive_prompt",
            "negative_prompt",
            "pipeline",
            "inpaint_mode",
            "locked_tags",
            "locked_pool_selection",
        }
    )
    JOB_TYPES = {
        "text_to_image",
        "reverse",
        "control",
        "img2img",
        "inpaint",
        "upscale",
        "character_swap",
    }
    TASK_KINDS = {
        "text_to_image": "draw",
        "control": "control_draw",
        "img2img": "semantic_redraw",
        "inpaint": "masked_redraw",
        "character_swap": "character_swap_edit",
    }

    def __init__(
        self,
        root: str | Path,
        data_dir: str | Path,
        *,
        secret_store: SecretStore | None = None,
        comfy: Any | None = None,
    ) -> None:
        self.root = Path(root)
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.workflow_assets_dir = Path(__file__).resolve().parent / "upstream"
        self.settings_path = self.data_dir / "settings.json"
        self.config = self._load_settings()
        self.registry = ProviderRegistry(self.data_dir, secret_store=secret_store)
        self.provider_client = OpenAIProviderClient(self.registry)
        self.provider_gateway = NativeProviderGateway(self.registry, self.provider_client)
        self.assets = AssetStore(self.data_dir / "uploads")
        self.danbooru = DanbooruTagIndex(self.data_dir / "danbooru_v2.sqlite3")
        self.semantic_path = self.data_dir / "lora_semantic_v3.json"
        self.semantic_index = LoraSemanticIndex.load(self.semantic_path)
        self.workspace_data = NaturalWorkspaceData(self.data_dir)
        self._external_planning_catalog: dict[str, tuple[Mapping[str, Any] | str, ...]] = {}
        self.comfy = comfy
        self.plans_path = self.data_dir / "plans.json"
        self._plans: dict[str, dict[str, Any]] = {}
        self._load_plans()
        self.diagnostics = PromptDiagnosticsStore(capacity=100)
        self.composer = PromptComposer(
            adaptive_negative_mode=str(self.config.get("adaptive_negative_mode") or "conservative"),
            diagnostics_store=self.diagnostics,
            tag_index=self.danbooru,
            validation_mode=str(self.config.get("danbooru_validation_mode") or "report"),
            include_content=True,
        )
        self._refresh_services()

    @staticmethod
    def _plan_digest(plan: Mapping[str, Any]) -> str:
        content = {str(key): value for key, value in plan.items() if key != "digest"}
        encoded = json.dumps(
            content,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _load_plans(self) -> None:
        try:
            document = json.loads(self.plans_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return
        records = document.get("plans") if isinstance(document, Mapping) else None
        if not isinstance(records, list):
            return
        valid: list[dict[str, Any]] = []
        for item in records:
            if not isinstance(item, Mapping):
                continue
            plan = dict(item)
            plan_id = str(plan.get("id") or "")
            try:
                revision = int(plan.get("revision") or 0)
            except (TypeError, ValueError):
                continue
            if (
                not plan_id.startswith("plan_")
                or revision < 1
                or str(plan.get("digest") or "") != self._plan_digest(plan)
            ):
                continue
            valid.append(plan)
        valid.sort(key=lambda item: float(item.get("updated_at") or item.get("created_at") or 0))
        for plan in valid[-self.MAX_PLANS :]:
            self._plans[str(plan["id"])] = plan

    def _persist_plans(self) -> None:
        records = list(self._plans.values())[-self.MAX_PLANS :]
        document = {"version": 1, "plans": records}
        # Validate serializability before replacing the last known-good snapshot.
        encoded = json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2)
        self.plans_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=f".{self.plans_path.name}.",
            suffix=".tmp",
            dir=self.plans_path.parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.plans_path)
        finally:
            if os.path.exists(temporary):
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def _remember_plan(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        record = json.loads(json.dumps(dict(plan), ensure_ascii=False))
        record["digest"] = self._plan_digest(record)
        plan_id = str(record["id"])
        self._plans.pop(plan_id, None)
        self._plans[plan_id] = record
        while len(self._plans) > self.MAX_PLANS:
            self._plans.pop(next(iter(self._plans)))
        self._persist_plans()
        return dict(record)

    def _resolve_plan_reference(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        raw_reference = payload.get("plan_revision")
        if raw_reference is not None and not isinstance(raw_reference, Mapping):
            raise NaturalEngineError(
                "plan_revision 必须包含 id、revision 和 digest",
                code="plan_revision_invalid",
                status=409,
            )
        reference = dict(raw_reference or {})
        requested_plan_id = str(
            reference.get("id") or payload.get("plan_id") or ""
        ).strip()
        if not requested_plan_id:
            if payload.get("overrides") is not None:
                raise NaturalEngineError(
                    "计划修改缺少 plan_revision",
                    code="plan_revision_required",
                    status=409,
                )
            return None
        cached = self._plans.get(requested_plan_id)
        if cached is None:
            raise NaturalEngineError(
                "Prompt Plan 不存在或已过期", code="plan_not_found", status=404
            )
        if raw_reference is not None:
            if str(payload.get("plan_id") or requested_plan_id).strip() != requested_plan_id:
                raise NaturalEngineError(
                    "计划引用 ID 不一致", code="plan_revision_conflict", status=409
                )
            try:
                revision = int(reference.get("revision"))
            except (TypeError, ValueError):
                revision = 0
            if (
                revision != int(cached.get("revision") or 0)
                or str(reference.get("digest") or "") != str(cached.get("digest") or "")
            ):
                raise NaturalEngineError(
                    "Prompt Plan 已更新，请重新确认最新修订",
                    code="plan_revision_conflict",
                    status=409,
                )
        overrides = payload.get("overrides")
        if overrides is None:
            return dict(cached)
        if raw_reference is None or not isinstance(overrides, Mapping):
            raise NaturalEngineError(
                "overrides 必须与有效的 plan_revision 一起提交",
                code="plan_overrides_invalid",
                status=409,
            )
        if not overrides:
            return dict(cached)
        unknown = set(map(str, overrides)) - self.PLAN_OVERRIDE_FIELDS
        if unknown:
            raise NaturalEngineError(
                f"计划修改包含不支持的字段: {', '.join(sorted(unknown))}",
                code="plan_overrides_invalid",
                status=422,
            )
        revised = dict(cached)
        for key, value in overrides.items():
            key = str(key)
            if key in {"positive_prompt", "negative_prompt"}:
                text = str(value or "").strip()
                if key == "positive_prompt" and not text:
                    raise NaturalEngineError("正向提示词不能为空", code="plan_overrides_invalid")
                if len(text) > 100_000:
                    raise NaturalEngineError("提示词修改过长", code="plan_overrides_invalid")
                revised[key] = text
            elif key == "pipeline":
                pipeline = str(value or "").strip().casefold()
                if pipeline not in {"base", "rtx", "iterative"}:
                    raise NaturalEngineError("计划管线无效", code="plan_overrides_invalid")
                revised[key] = pipeline
            elif key == "inpaint_mode":
                mode = str(value or "").strip().casefold()
                if mode not in {"quick", "lanpaint"}:
                    raise NaturalEngineError("局部重绘模式无效", code="plan_overrides_invalid")
                revised[key] = mode
            elif key == "locked_tags":
                revised[key] = list(self._locked_tags({"locked_tags": value}))
            else:
                if not isinstance(value, Mapping):
                    raise NaturalEngineError("锁定池选择格式无效", code="plan_overrides_invalid")
                revised[key] = dict(value)
        revised["revision"] = int(cached.get("revision") or 1) + 1
        revised["updated_at"] = time.time()
        revised["override_fields"] = sorted(map(str, overrides))
        revised.pop("digest", None)
        return self._remember_plan(revised)

    def _load_settings(self) -> dict[str, Any]:
        defaults = {
            "default_pipeline": "base",
            "default_width": 832,
            "default_height": 1216,
            "default_steps": 8,
            "default_cfg": 5.0,
            "adaptive_negative_mode": "conservative",
            "danbooru_validation_mode": "report",
            "rtx_scale": 2.0,
            "rtx_quality": "ULTRA",
        }
        if not self.settings_path.is_file():
            return defaults
        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return defaults
        if isinstance(raw, Mapping):
            defaults.update(raw)
        return defaults

    def update_settings(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "default_pipeline",
            "default_width",
            "default_height",
            "default_steps",
            "default_cfg",
            "adaptive_negative_mode",
            "danbooru_validation_mode",
            "rtx_scale",
            "rtx_quality",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise NaturalEngineError("包含未知自然语言设置: " + ", ".join(sorted(unknown)))
        updated = dict(self.config)
        updated.update(payload)
        if str(updated.get("default_pipeline")) not in {"base", "rtx", "iterative"}:
            raise NaturalEngineError("默认管线必须是 base、rtx 或 iterative")
        for key, low, high in (
            ("default_width", 256, 4096),
            ("default_height", 256, 4096),
            ("default_steps", 1, 100),
        ):
            value = int(updated[key])
            if not low <= value <= high:
                raise NaturalEngineError(f"{key} 必须在 {low}-{high} 之间")
            updated[key] = value
        updated["default_cfg"] = max(0.1, min(30.0, float(updated["default_cfg"])))
        updated["rtx_scale"] = max(1.0, min(4.0, float(updated["rtx_scale"])))
        self.config = updated
        _atomic_json(self.settings_path, self.config)
        self.composer = PromptComposer(
            adaptive_negative_mode=str(self.config["adaptive_negative_mode"]),
            diagnostics_store=self.diagnostics,
            tag_index=self.danbooru,
            validation_mode=str(self.config["danbooru_validation_mode"]),
            include_content=True,
        )
        self._refresh_services()
        return self.settings_snapshot()

    def settings_snapshot(self) -> dict[str, Any]:
        return {**self.config, "providers": self.registry.snapshot()}

    @staticmethod
    def _json_items(path: Path) -> list[dict[str, Any]]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        items = value.get("items") if isinstance(value, Mapping) else None
        return [dict(item) for item in items or () if isinstance(item, Mapping)]

    @staticmethod
    def _dedupe_planning_records(
        values: Sequence[Mapping[str, Any] | str],
    ) -> tuple[Mapping[str, Any] | str, ...]:
        result: list[Mapping[str, Any] | str] = []
        seen: set[str] = set()
        for value in values:
            if isinstance(value, Mapping):
                key = str(
                    value.get("id")
                    or value.get("asset_id")
                    or value.get("filename")
                    or value.get("name")
                    or value.get("title")
                    or ""
                ).strip().casefold()
            else:
                key = str(value).strip().casefold()
            if key and key not in seen:
                seen.add(key)
                result.append(value)
        return tuple(result)

    def configure_planning_tools(
        self,
        *,
        artists: Sequence[Mapping[str, Any] | str] = (),
        loras: Sequence[Mapping[str, Any] | str] = (),
        presets: Sequence[Mapping[str, Any]] = (),
        prompt_assets: Sequence[Mapping[str, Any]] = (),
        prompt_plans: Sequence[Mapping[str, Any]] = (),
        character_aliases: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        """Attach WebUI-owned catalogs without coupling the engine to HTTP or SQLite routes."""

        self._external_planning_catalog = {
            "artists": tuple(artists),
            "loras": tuple(loras),
            "presets": tuple(presets),
            "prompt_assets": tuple(prompt_assets),
            "prompt_plans": tuple(prompt_plans),
            "character_aliases": tuple(character_aliases),
        }

    def _planning_tools(self) -> NativePlanningToolRegistry:
        presets = self._json_items(self.root / "data" / "style_presets.json")
        artists: list[Mapping[str, Any] | str] = []
        for preset in presets:
            settings = preset.get("settings") if isinstance(preset.get("settings"), Mapping) else {}
            raw_artists = str(settings.get("manual_artist") or "")
            for raw in re.split(r"[,，\r\n]+", raw_artists):
                name = raw.strip().removeprefix("@").strip()
                if name:
                    artists.append({"id": f"artist:{name.casefold()}", "name": name})
        try:
            profiles = self.workspace_data.list("lora_profiles")
            identities = self.workspace_data.list("identities")
        except ValueError:
            profiles, identities = [], []
        loras: list[Mapping[str, Any] | str] = []
        for profile in profiles:
            filename = str(profile.get("filename") or "").strip()
            if not filename:
                continue
            aliases = [str(profile.get("display_name") or "").strip()]
            aliases.append(Path(filename).stem)
            try:
                verified_bindings = self.workspace_data.active_identity_bindings(
                    str(profile.get("id") or "")
                )
            except (KeyError, ValueError):
                verified_bindings = []
            loras.append(
                {
                    "id": str(profile.get("id") or filename),
                    "filename": filename,
                    "aliases": [alias for alias in dict.fromkeys(aliases) if alias],
                    "role": "character" if verified_bindings else "style",
                }
            )
        prompt_plans = [dict(plan) for plan in self._plans.values()]

        def combined(name: str, local: Sequence[Mapping[str, Any] | str]) -> tuple[Mapping[str, Any] | str, ...]:
            external = self._external_planning_catalog.get(name, ())
            return self._dedupe_planning_records((*external, *local))

        return NativePlanningToolRegistry(
            artists=combined("artists", artists),
            loras=combined("loras", loras),
            presets=tuple(combined("presets", presets)),
            prompt_assets=tuple(combined("prompt_assets", ())),
            prompt_plans=tuple(combined("prompt_plans", prompt_plans)),
            character_aliases=tuple(combined("character_aliases", identities)),
        )

    def _runtime_settings(self) -> NativeNaturalSettings:
        return NativeNaturalSettings.from_runtime_config(self.config)

    def _refresh_services(self) -> None:
        settings = self._runtime_settings()
        try:
            self.director = NativeNaturalPlanner(
                self.provider_gateway,
                settings.resolve_director_reference_path(self.workflow_assets_dir),
                tools=self._planning_tools,
            )
        except (OSError, ValueError, NativePlanningError) as exc:
            raise NaturalEngineError(f"自然语言导演初始化失败: {exc}") from exc
        self.reverse_service = NativeReversePrompt(self.provider_gateway, max_tokens=4096)

    async def _reverse_image(
        self,
        path: Path,
        supplement: str,
        *,
        profile: str = "full",
    ) -> tuple[Any, str]:
        if isinstance(self.reverse_service, NativeReversePrompt):
            return await self.reverse_service.reverse(path, supplement, profile=profile)
        return await self.reverse_service.reverse(None, None, path, supplement, profile=profile)

    async def _direct_instruction(
        self,
        text: str,
        *,
        task_kind: str,
    ) -> tuple[Any, str]:
        if isinstance(self.director, NativeNaturalPlanner):
            return await self.director.generate_instruction(
                text,
                task_kind=task_kind,
                runtime_capabilities=(),
                compose_result=False,
            )
        return await self.director.generate_instruction(
            None,
            None,
            text,
            task_kind=task_kind,
            runtime_capabilities=(),
            compose_result=False,
        )

    async def _direct_edit_instruction(self, text: str) -> tuple[Any, str]:
        if isinstance(self.director, NativeNaturalPlanner):
            return await self.director.generate_edit_instruction(
                text,
                runtime_capabilities=(),
            )
        return await self.director.generate_edit_instruction(
            None,
            None,
            text,
            runtime_capabilities=(),
        )

    @staticmethod
    def _text(payload: Mapping[str, Any]) -> str:
        text = str(payload.get("positive_prompt") or payload.get("text") or "").strip()
        if len(text) > 6000:
            raise NaturalEngineError("自然语言描述不能超过 6000 字符")
        return text

    @staticmethod
    def _locked_tags(payload: Mapping[str, Any]) -> tuple[str, ...]:
        raw = payload.get("locked_tags") or []
        if isinstance(raw, str):
            raw = raw.replace("\r", ",").replace("\n", ",").split(",")
        if not isinstance(raw, list):
            raise NaturalEngineError("locked_tags 必须是字符串数组")
        result = tuple(dict.fromkeys(str(item).strip() for item in raw if str(item).strip()))
        if len(result) > 500:
            raise NaturalEngineError("锁定提示词数量过多")
        return result

    def _danbooru_ready(self) -> bool:
        try:
            return bool(self.danbooru.status().get("ready"))
        except (DanbooruIndexError, OSError, RuntimeError, ValueError):
            return False

    async def _lora_records(self) -> tuple[LoraRecord, ...]:
        if self.comfy is None:
            return ()
        try:
            inventory = await self.comfy.lora_inventory()
        except Exception:
            return ()
        profile_items = self.workspace_data.list("lora_profiles")
        profiles = {
            str(item.get("filename") or "").replace("\\", "/").casefold(): item
            for item in profile_items
            if isinstance(item, Mapping) and item.get("filename")
        }
        records: list[LoraRecord] = []
        for item in inventory.get("items") or []:
            if not isinstance(item, Mapping):
                continue
            filename = str(item.get("filename") or item.get("name") or "").strip()
            if not filename:
                continue
            profile = profiles.get(filename.replace("\\", "/").casefold()) or {}
            profile_id = str(profile.get("id") or "")
            try:
                identities = self.workspace_data.active_identity_bindings(profile_id)
            except (KeyError, ValueError):
                identities = []
            identity_tags = tuple(
                dict.fromkeys(
                    str(value).strip()
                    for value in profile.get("identity_tags") or []
                    if str(value).strip()
                )
            )
            canonical_tags = tuple(
                str(
                    identity.get("character_canonical")
                    or identity.get("canonical_tag")
                    or ""
                ).strip()
                for identity in identities
                if str(
                    identity.get("character_canonical")
                    or identity.get("canonical_tag")
                    or ""
                ).strip()
            )
            activation_terms = tuple(
                dict.fromkeys(
                    str(value).strip()
                    for value in (
                        *profile.get("activation_terms", ()),
                        *(
                            term
                            for identity in identities
                            for term in identity.get("activation_terms") or ()
                        ),
                    )
                    if str(value).strip()
                )
            )
            aliases = tuple(
                dict.fromkeys(
                    value
                    for identity in identities
                    for value in (
                        str(identity.get("name") or "").strip(),
                        *(str(alias).strip() for alias in identity.get("aliases") or []),
                    )
                    if value
                )
            )
            records.append(
                LoraRecord(
                    name=filename,
                    trigger_words=tuple(
                        dict.fromkeys(
                            (*canonical_tags, *activation_terms, *identity_tags)
                        )
                    ),
                    description=", ".join(
                        str(value).strip()
                        for value in profile.get("style_tags") or []
                        if str(value).strip()
                    ),
                    folder=str(item.get("folder") or ""),
                    preview_url=str(item.get("preview") or ""),
                    category="character" if identities or identity_tags else "unknown",
                    aliases=aliases,
                    character_name=" / ".join(
                        str(identity.get("name") or "")
                        for identity in identities
                        if str(identity.get("name") or "")
                    ),
                    source="webui",
                    sha256=str(profile.get("sha256") or ""),
                    source_fingerprint=str(
                        profile.get("source_fingerprint") or ""
                    ),
                    source_work=" / ".join(
                        str(identity.get("copyright_canonical") or "")
                        for identity in identities
                        if str(identity.get("copyright_canonical") or "")
                    ),
                )
            )
        self.semantic_index.sync_presence(records)
        return tuple(records)

    @staticmethod
    def _swap_request(text: str, payload: Mapping[str, Any]) -> CharacterSwapRequest:
        request = parse_natural_character_swap(text)
        if request is None:
            english = re.search(
                r"(?:replace|swap|change)\s+(?:the\s+)?(?:character|person|subject)?\s*"
                r"(?:with|to|into)\s+(.+?)(?:[,.;]|$)",
                text,
                flags=re.IGNORECASE,
            )
            if english is None:
                raise NaturalEngineError(
                    "换角描述必须明确目标角色，例如“把图中角色替换成初音未来”",
                    code="character_swap_target_required",
                    status=422,
                )
            request = CharacterSwapRequest("", english.group(1).strip())
        return replace(
            request,
            preview=bool(payload.get("preview_only")),
            use_target_lora=bool(payload.get("use_target_lora", request.use_target_lora)),
            require_target_lora=bool(payload.get("require_target_lora", request.require_target_lora)),
            target_lora_strength=float(
                payload.get("target_lora_strength", request.target_lora_strength)
            ),
            pipeline=str(payload.get("pipeline") or request.pipeline),
            seed=(None if payload.get("seed") in (None, "", -1, "-1") else int(payload["seed"])),
            steps=(int(payload["steps"]) if payload.get("steps") not in (None, "") else request.steps),
            cfg=(float(payload["cfg"]) if payload.get("cfg") not in (None, "") else request.cfg),
            denoise=(
                float(payload["denoise"])
                if payload.get("denoise") not in (None, "")
                else request.denoise
            ),
        )

    async def _semantic_target_tags(
        self,
        request: CharacterSwapRequest,
    ) -> tuple[tuple[str, ...], dict[str, Any], str]:
        if not self._danbooru_ready():
            raise NaturalEngineError(
                "严格换角需要先完成本地 Danbooru 索引安装",
                code="danbooru_index_required",
                status=422,
            )
        system_prompt = (
            "You convert one target character request into conservative Anima/Danbooru "
            "identity tags. Return one JSON object with exactly these fields: "
            '"canonical_identity_tag", "identity_candidates", "work_hints", '
            '"appearance_tags", and "confidence". canonical_identity_tag must be one '
            "short ASCII English canonical character tag including the work qualifier "
            "whenever one exists. identity_candidates must contain one to eight short "
            "ASCII official romanized names or canonical spellings for only the same "
            "requested character. work_hints must contain zero to four ASCII Danbooru "
            "copyright tags. appearance_tags may contain zero to six iconic stable "
            "General tags. Never include clothing, pose, scene, style, quality or LoRA "
            "tokens. confidence must be a JSON number from 0 to 1. If uncertain, return "
            "confidence below 0.8 instead of guessing. Return JSON only."
        )
        prompt = json.dumps(
            {"target_character": request.target_query[:200]},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        provider = self.registry.bound("director")
        last_code = "invalid_json"
        for attempt in (1, 2):
            try:
                text = await self.provider_client.complete(
                    provider,
                    model=provider.director_model,
                    prompt=(
                        prompt
                        if attempt == 1
                        else prompt
                        + "\nThe previous response failed validation. Return only the exact JSON object."
                    ),
                    system_prompt=system_prompt,
                    temperature=0.0,
                    max_tokens=500,
                )
                parsed = parse_json_object(text)
                tags, confidence, _ignored = normalize_semantic_identity_payload(parsed)
                identity_candidates, work_hints = semantic_identity_lookup_hints(parsed)
                resolution = await asyncio.to_thread(
                    resolve_character_identity,
                    self.danbooru,
                    target_query=request.target_query,
                    canonical_tag=tags[0],
                    identity_candidates=identity_candidates,
                    work_hints=work_hints,
                )
                if resolution.ambiguous:
                    raise NaturalEngineError(
                        "本地 Danbooru 找到多个目标角色身份，已停止猜选",
                        code="semantic_target_danbooru_ambiguous",
                        status=422,
                        details={"candidates": list(resolution.candidates[:8])},
                    )
                if not resolution.verified:
                    last_code = "semantic_target_identity_unverified"
                    continue
                canonical = resolution.canonical_tag
                return (
                    (canonical,),
                    {
                        "confidence": confidence,
                        "index_verified": True,
                        "canonical_tag": canonical,
                        "anchor_source": "danbooru_exact",
                        "match_variant": resolution.match_variant,
                        "match_type": resolution.match_type,
                        "candidate_count": resolution.candidate_count,
                        "query_count": resolution.query_count,
                    },
                    provider.id,
                )
            except NaturalEngineError:
                raise
            except (CharacterSwapError, ReversePromptError) as exc:
                last_code = str(getattr(exc, "code", "invalid_json"))
            except (DanbooruIndexError, ProviderRegistryError, OSError, RuntimeError, ValueError) as exc:
                last_code = str(getattr(exc, "code", type(exc).__name__))
        raise NaturalEngineError(
            "目标角色未通过本地 Danbooru character exact 验证",
            code="semantic_target_identity_unverified",
            status=422,
            details={"last_error_code": last_code},
        )

    async def _classify_character_swap(
        self,
        planner: CharacterSwapPlanner,
        preparation: Any,
    ) -> tuple[Any, str]:
        classification = planner.deterministic_classification(preparation)
        if classification is not None:
            return classification, "deterministic"
        system_prompt, user_prompt = planner.classification_prompts(preparation)
        provider = self.registry.bound("director")
        last_code = "classification_invalid"
        for attempt in (1, 2):
            try:
                text = await self.provider_client.complete(
                    provider,
                    model=provider.director_model,
                    prompt=(
                        user_prompt
                        if attempt == 1
                        else user_prompt
                        + "\nThe previous response failed validation. Return the exact JSON object only."
                    ),
                    system_prompt=system_prompt,
                    temperature=0.0,
                    max_tokens=2000,
                )
                return (
                    planner.parse_classification(
                        text,
                        tag_count=len(preparation.tags),
                        target_trigger_count=len(preparation.target_trigger_words),
                        deterministic_target_identity_id=(
                            0 if preparation.deterministic_target_trigger else None
                        ),
                    ),
                    provider.id,
                )
            except CharacterSwapError as exc:
                last_code = exc.code
            except ProviderRegistryError as exc:
                last_code = type(exc).__name__
        raise NaturalEngineError(
            "换角分类器连续两次未返回可用结果",
            code="classification_repair_exhausted",
            status=422,
            details={"last_error_code": last_code},
        )

    async def _plan_character_swap(
        self,
        payload: Mapping[str, Any],
        observed: Any,
        selection: Any,
    ) -> tuple[CharacterSwapPlan, dict[str, Any], str]:
        request = self._swap_request(self._text(payload), payload)
        request = replace(
            request,
            source_subject_count=selection.subject_count,
            source_selector_terms=selection.matched_terms,
            protected_subject_terms=selection.protected_terms,
            source_selector_basis=selection.basis,
            source_selector_direction_used=selection.direction_used,
            require_target_appearance_slots=True,
        )
        records = await self._lora_records()
        target_tags, evidence, provider_id = await self._semantic_target_tags(request)
        request = replace(
            request,
            semantic_identity_confidence=float(evidence["confidence"]),
            semantic_identity_index_verified=True,
            semantic_identity_anchor_source=str(evidence["anchor_source"]),
            semantic_identity_match_variant=str(evidence["match_variant"]),
            semantic_identity_match_type=str(evidence["match_type"]),
            semantic_identity_candidate_count=int(evidence["candidate_count"]),
            semantic_identity_query_count=int(evidence["query_count"]),
            semantic_identity_canonical_tag=str(evidence["canonical_tag"]),
        )
        prompt = str(observed.positive_tags or "").strip()
        selected_loras = payload.get("loras") or []
        if selected_loras:
            prompt = ", ".join(
                (
                    prompt,
                    *(
                        f"<lora:{str(item.get('name') or '').strip()}:{float(item.get('strength', 0.8)):g}>"
                        for item in selected_loras
                        if isinstance(item, Mapping) and str(item.get("name") or "").strip()
                    ),
                )
            ).strip(" ,")
        planner = CharacterSwapPlanner(self.semantic_index)
        try:
            preparation = planner.prepare(
                request,
                positive_prompt=prompt,
                negative_prompt=str(observed.negative_tags or ""),
                records=records,
                fallback_target_tags=target_tags,
            )
            lookups = await asyncio.to_thread(self.danbooru.lookup_many, preparation.tags)
            preparation = planner.attach_source_tag_evidence(preparation, lookups)
            classification, classifier = await self._classify_character_swap(
                planner,
                preparation,
            )
            plan = planner.finalize(preparation, classification)
        except CharacterSwapError as exc:
            raise NaturalEngineError(
                str(exc),
                code=exc.code,
                status=422,
                details=exc.details,
            ) from exc
        return (
            plan,
            {
                "request": asdict(request),
                "identity_evidence": evidence,
                "classifier": classifier,
                "target_identity_trigger": plan.target_identity_trigger,
                "removed_terms": list(plan.removed_terms),
                "kept_terms": list(plan.kept_terms),
                "added_terms": list(plan.added_terms),
                "suppressed_terms": list(plan.suppressed_terms),
                "suppress_default_style": plan.suppress_default_style,
                "loras": [asdict(item) for item in plan.loras],
                "expectations": [asdict(item) for item in plan.expectations],
                "target_lora": plan.target_record.name if plan.target_record else "",
                "preserved_character_loras": list(plan.preserved_character_lora_names),
                "forbid_character_loras": bool(
                    plan.target_record is None and not plan.preserved_character_lora_names
                ),
                "source_subject_count": plan.source_subject_count,
                "multi_subject": plan.multi_subject,
                "source_selector_terms": list(plan.source_selector_terms),
                "protected_subject_terms": list(plan.protected_subject_terms),
                "source_selector_basis": plan.source_selector_basis,
                "feature_swap_categories": list(plan.feature_swap_categories),
                "target_feature_categories": list(plan.target_feature_categories),
                "missing_target_feature_categories": list(
                    plan.missing_target_feature_categories
                ),
            },
            provider_id,
        )

    async def plan(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        cached = self._resolve_plan_reference(payload)
        if cached is not None:
            return cached
        job_type = str(payload.get("job_type") or "text_to_image").strip().casefold()
        if job_type not in self.JOB_TYPES:
            raise NaturalEngineError("不支持的自然语言任务类型")
        text = self._text(payload)
        if job_type not in {"reverse", "upscale"} and not text:
            raise NaturalEngineError("请输入自然语言描述")
        pipeline = str(payload.get("pipeline") or self.config["default_pipeline"]).strip().casefold()
        if pipeline not in {"base", "rtx", "iterative"}:
            raise NaturalEngineError("管线必须是 base、rtx 或 iterative")
        locked_tags = self._locked_tags(payload)
        use_llm = bool(payload.get("use_llm", True)) and job_type not in {"upscale"}
        provider_id = ""
        raw_positive = text
        raw_negative = str(payload.get("negative_prompt") or "").strip()
        inpaint_mode = str(payload.get("inpaint_mode") or "quick").strip().casefold()
        reverse_result: dict[str, Any] | None = None
        swap_result: dict[str, Any] | None = None
        planning_instruction: Any | None = None

        source_asset: NaturalAsset | None = None
        if job_type == "reverse" or str(payload.get("asset_id") or ""):
            source_asset = self.assets.get(str(payload.get("asset_id") or ""))

        if job_type == "reverse":
            try:
                result, provider_id = await self._reverse_image(source_asset.path, text)
            except (NativePlanningError, ReversePromptError, ProviderRegistryError) as exc:
                raise NaturalEngineError(
                    str(exc),
                    code="reverse_failed",
                    status=422,
                    details={"reverse_code": str(getattr(exc, "code", ""))},
                ) from exc
            reverse_result = asdict(result)
            raw_positive = str(reverse_result.get("positive_tags") or "")
            raw_negative = str(reverse_result.get("negative_tags") or raw_negative)
        elif job_type in {"control", "img2img", "inpaint", "character_swap"} and use_llm:
            if source_asset is None:
                raise NaturalEngineError("图片编辑任务缺少原图", code="source_asset_required")
            try:
                observed, provider_id = await self._reverse_image(
                    source_asset.path,
                    str(payload.get("source_selector") or "") if job_type == "character_swap" else text,
                    profile="swap" if job_type == "character_swap" else "full",
                )
            except (NativePlanningError, ReversePromptError, ProviderRegistryError) as exc:
                raise NaturalEngineError(
                    str(exc),
                    code="source_reverse_failed",
                    status=422,
                    details={"reverse_code": str(getattr(exc, "code", ""))},
                ) from exc
            reverse_result = asdict(observed)
            if job_type == "character_swap":
                subjects = tuple(
                    ObservedSubject(
                        name=item.name,
                        source_work=item.source_work,
                        gender=item.gender,
                        appearance_tags=item.appearance_tags,
                        outfit_tags=item.outfit_tags,
                        action_tags=item.action_tags,
                        position=item.position,
                        confidence=item.confidence,
                    )
                    for item in observed.characters
                )
                try:
                    selected_index, selection = select_observed_subject(
                        subjects, str(payload.get("source_selector") or "")
                    )
                except SubjectSelectionError as exc:
                    raise NaturalEngineError(
                        str(exc),
                        code=exc.code,
                        status=422,
                        details={
                            **exc.details,
                            "subjects": [asdict(subject) for subject in subjects],
                        },
                    ) from exc
                reverse_result["selected_subject_index"] = selected_index
                reverse_result["subject_selection"] = asdict(selection)
                swap_plan, swap_result, swap_provider_id = await self._plan_character_swap(
                    payload,
                    observed,
                    selection,
                )
                provider_id = swap_provider_id or provider_id
                raw_positive = swap_plan.prompt
                raw_negative = swap_plan.negative_prompt
            else:
                source_context = str(reverse_result.get("positive_tags") or "").strip()
                director_text = (
                    f"Observed source image tags: {source_context}. Requested edit: {text}"
                    if source_context
                    else text
                )
                try:
                    if job_type == "inpaint":
                        instruction, director_provider_id = (
                            await self._direct_edit_instruction(director_text)
                        )
                        inpaint_mode = instruction.mode
                    else:
                        instruction, director_provider_id = (
                            await self._direct_instruction(
                                director_text,
                                task_kind=self.TASK_KINDS.get(job_type, "draw"),
                            )
                        )
                        planning_instruction = instruction
                except (NativePlanningError, ProviderRegistryError) as exc:
                    raise NaturalEngineError(str(exc), code="director_failed", status=422) from exc
                provider_id = director_provider_id or provider_id
                raw_positive = instruction.prompt
                raw_negative = instruction.negative_prompt or raw_negative
                directed_pipeline = str(getattr(instruction, "pipeline", "") or "")
                if directed_pipeline in {"base", "rtx", "iterative"} and not payload.get("pipeline"):
                    pipeline = directed_pipeline
        elif use_llm:
            try:
                instruction, provider_id = await self._direct_instruction(
                    text,
                    task_kind=self.TASK_KINDS.get(job_type, "draw"),
                )
            except (NativePlanningError, ProviderRegistryError) as exc:
                raise NaturalEngineError(str(exc), code="director_failed", status=422) from exc
            raw_positive = instruction.prompt
            raw_negative = instruction.negative_prompt or raw_negative
            planning_instruction = instruction
            if instruction.pipeline in {"base", "rtx", "iterative"} and not payload.get("pipeline"):
                pipeline = instruction.pipeline

        planning_matches: list[dict[str, Any]] = []
        requires_confirmation: list[dict[str, Any]] = []
        resolution_sources: dict[str, Any] = {}
        style_preset_id = ""
        selected_model = str(payload.get("model") or payload.get("model_name") or "").strip()
        selected_loras: list[dict[str, Any]] = []
        artist_tags: list[str] = []
        prompt_asset_ids: list[str] = []
        prompt_plan_id = ""
        if planning_instruction is not None:
            planning_matches = [
                dict(item)
                for item in getattr(planning_instruction, "matches", ())
                if isinstance(item, Mapping)
            ]
            requires_confirmation = [
                dict(item)
                for item in getattr(planning_instruction, "requires_confirmation", ())
                if isinstance(item, Mapping)
            ]
            resolution_sources = dict(getattr(planning_instruction, "sources", {}) or {})
            artist_tags.extend(str(item) for item in getattr(planning_instruction, "artist_tags", ()))
            prompt_asset_ids.extend(
                str(item) for item in getattr(planning_instruction, "prompt_asset_ids", ())
            )
            prompt_plan_id = str(getattr(planning_instruction, "prompt_plan_id", "") or "")
            style_preset_id = str(getattr(planning_instruction, "style_preset_id", "") or "")
            selected_preset = getattr(planning_instruction, "selected_preset", {})
            preset_settings: Mapping[str, Any] = {}
            if isinstance(selected_preset, Mapping):
                raw_settings = selected_preset.get("intent", selected_preset.get("settings", {}))
                if isinstance(raw_settings, Mapping):
                    preset_settings = raw_settings
            if preset_settings:
                preset_intent = GenerationIntent.from_mapping(preset_settings, "natural")
                selected_model = selected_model or preset_intent.model
                if not payload.get("pipeline") and not getattr(planning_instruction, "pipeline", ""):
                    pipeline = preset_intent.pipeline or pipeline
                if not raw_negative:
                    raw_negative = preset_intent.negative_prompt
                artist_tags[0:0] = list(preset_intent.artist_tags)
                selected_loras.extend(item.to_dict() for item in preset_intent.loras)
                raw_positive = ", ".join(
                    value
                    for value in (
                        str(preset_settings.get("quality_prompt") or "").strip(),
                        str(preset_settings.get("extra_prompt") or "").strip(),
                        raw_positive,
                    )
                    if value
                )
            for asset in getattr(planning_instruction, "selected_prompt_assets", ()):
                if not isinstance(asset, Mapping):
                    continue
                asset_prompt = str(
                    asset.get("prompt") or asset.get("positive_prompt") or asset.get("tags") or ""
                ).strip()
                if asset_prompt:
                    raw_positive = f"{asset_prompt}, {raw_positive}" if raw_positive else asset_prompt
            selected_plan = getattr(planning_instruction, "selected_prompt_plan", {})
            if isinstance(selected_plan, Mapping):
                plan_prompt = str(selected_plan.get("positive_prompt") or "").strip()
                if plan_prompt:
                    raw_positive = f"{plan_prompt}, {raw_positive}" if raw_positive else plan_prompt
                if not raw_negative:
                    raw_negative = str(selected_plan.get("negative_prompt") or "").strip()
            by_filename = {
                str(item.get("filename") or "").casefold(): item
                for item in selected_loras
                if item.get("filename")
            }
            for lora in getattr(planning_instruction, "loras", ()):
                if isinstance(lora, LoraSelection):
                    by_filename[lora.filename.casefold()] = lora.to_dict()
            selected_loras = list(by_filename.values())
            raw_positive = ", ".join(
                value for value in (*artist_tags, raw_positive) if str(value).strip()
            )

        composed = self.composer.compose(
            raw_positive,
            raw_negative,
            hard_tags=locked_tags,
            anchors=[(tag, "locked_pool") for tag in locked_tags],
            source=f"web:{job_type}",
            provider_id=provider_id,
            pipeline=pipeline,
        )
        diagnostics = asdict(composed.diagnostics)
        now = time.time()
        result = {
            "id": f"plan_{uuid.uuid4().hex[:12]}",
            "revision": 1,
            "job_type": job_type,
            "source_text": text,
            "pipeline": pipeline,
            "inpaint_mode": inpaint_mode,
            "provider_id": provider_id,
            "matches": planning_matches,
            "requires_confirmation": requires_confirmation,
            "sources": resolution_sources,
            "style_preset_id": style_preset_id,
            "model": selected_model,
            "loras": selected_loras,
            "artist_tags": list(dict.fromkeys(artist_tags)),
            "prompt_asset_ids": list(dict.fromkeys(prompt_asset_ids)),
            "prompt_plan_id": prompt_plan_id,
            "positive_prompt": composed.positive_prompt,
            "negative_prompt": composed.negative_prompt,
            "layers": asdict(composed.layers),
            "diagnostics": diagnostics,
            "locked_tags": list(locked_tags),
            "locked_pool_selection": dict(payload.get("locked_pool_selection") or {}),
            "reverse": reverse_result,
            "character_swap": swap_result,
            "created_at": now,
            "updated_at": now,
        }
        return self._remember_plan(result)

    def _options(
        self,
        payload: Mapping[str, Any],
        plan: Mapping[str, Any],
    ) -> WorkflowGenerationOptions:
        swap = plan.get("character_swap")
        swap = swap if isinstance(swap, Mapping) else {}
        if swap:
            raw_loras = swap.get("loras")
        elif "loras" in payload:
            raw_loras = payload.get("loras")
        else:
            raw_loras = plan.get("loras")
        raw_loras = raw_loras or []
        loras: list[LoraSelection] = []
        if not isinstance(raw_loras, list):
            raise NaturalEngineError("loras 必须是数组")
        for item in raw_loras:
            if not isinstance(item, Mapping) or not str(
                item.get("filename") or item.get("name") or ""
            ).strip():
                raise NaturalEngineError("LoRA 项目格式无效")
            strength = float(item.get("strength", 0.8))
            if not -10.0 <= strength <= 10.0:
                raise NaturalEngineError("LoRA 强度必须在 -10 到 10 之间")
            loras.append(
                LoraSelection.from_mapping(
                    {
                        "filename": str(item.get("filename") or item.get("name") or "").strip(),
                        "enabled": item.get("enabled", True),
                        "strength": strength,
                        "role": item.get("role", "style"),
                        "order": item.get("order", len(loras)),
                    },
                    order=len(loras),
                )
            )
        seed_value = payload.get("seed")
        seed = None if seed_value in (None, "", -1, "-1") else int(seed_value)
        if seed is not None and not 0 <= seed <= 2**63 - 1:
            raise NaturalEngineError("Seed 超出范围")
        intent = GenerationIntent.from_mapping(
            {
                "workspace": "natural",
                "positive_prompt": str(plan["positive_prompt"]),
                "negative_prompt": str(plan.get("negative_prompt") or ""),
                "pipeline": str(plan["pipeline"]),
                "loras": [item.to_dict() for item in loras],
                "width": payload.get("width") or self.config["default_width"],
                "height": payload.get("height") or self.config["default_height"],
                "steps": payload.get("steps") or self.config["default_steps"],
                "cfg": payload.get("cfg") or self.config["default_cfg"],
                "seed": -1 if seed is None else seed,
                "denoise": payload.get("denoise", 0.55),
            },
            "natural",
        )
        expectations = tuple(
            LoraIdentityExpectation(
                name=str(item.get("name") or ""),
                sha256=str(item.get("sha256") or ""),
                source_fingerprint=str(item.get("source_fingerprint") or ""),
            )
            for item in swap.get("expectations") or []
            if isinstance(item, Mapping) and str(item.get("name") or "")
        )
        return WorkflowGenerationOptions(
            prompt=intent.positive_prompt,
            negative_prompt=intent.negative_prompt,
            seed=seed,
            width=intent.sampling.width,
            height=intent.sampling.height,
            steps=intent.sampling.steps,
            cfg=intent.sampling.cfg,
            enable_upscale=str(plan["pipeline"]) == "rtx",
            dynamic_loras=tuple(loras),
            lora_injection_mode=("replace" if swap or loras else None),
            suppress_default_style=bool(swap.get("suppress_default_style")),
            suppressed_prompt_terms=tuple(str(item) for item in swap.get("suppressed_terms") or []),
            lora_identity_expectations=expectations,
            character_swap_target_lora=str(swap.get("target_lora") or ""),
            character_swap_preserved_character_loras=tuple(
                str(item) for item in swap.get("preserved_character_loras") or []
            ),
            character_swap_target_lora_strength=(
                float(payload.get("target_lora_strength", 0.65))
                if swap.get("target_lora")
                else None
            ),
            character_swap_forbid_character_loras=bool(
                swap.get("forbid_character_loras")
            ),
            pipeline=str(plan["pipeline"]),
            denoise=intent.sampling.denoise,
            control_modes=tuple(str(item) for item in payload.get("control_modes") or ()),
            inpaint_mode=str(plan.get("inpaint_mode") or payload.get("inpaint_mode") or "quick"),
            semantic_redraw_mode=str(payload.get("semantic_redraw_mode") or "balanced"),
        )

    @staticmethod
    def _bypass_empty_lora_manager(
        result: tuple[dict[str, Any], int, list[str]],
        options: WorkflowGenerationOptions,
    ) -> tuple[dict[str, Any], int, list[str]]:
        workflow, seed, outputs = result
        if options.dynamic_loras:
            return result
        for node_id, node in tuple(workflow.items()):
            if not isinstance(node, Mapping) or node.get("class_type") != "Lora Loader (LoraManager)":
                continue
            inputs = node.get("inputs") if isinstance(node.get("inputs"), Mapping) else {}
            replacements = {0: inputs.get("model"), 1: inputs.get("clip")}
            for candidate in workflow.values():
                candidate_inputs = candidate.get("inputs") if isinstance(candidate, Mapping) else None
                if not isinstance(candidate_inputs, dict):
                    continue
                for name, value in tuple(candidate_inputs.items()):
                    if (
                        isinstance(value, list)
                        and len(value) >= 2
                        and str(value[0]) == str(node_id)
                        and int(value[1]) in replacements
                    ):
                        replacement = replacements[int(value[1])]
                        if not isinstance(replacement, list) or len(replacement) < 2:
                            raise NaturalEngineError("空 LoRA 节点无法安全旁路", code="lora_bypass_failed")
                        candidate_inputs[name] = list(replacement)
            del workflow[node_id]
        return workflow, seed, outputs

    def build_workflow(
        self,
        payload: Mapping[str, Any],
        plan: Mapping[str, Any],
        uploaded: Mapping[str, str],
    ) -> tuple[dict[str, Any], int, list[str]]:
        if plan.get("requires_confirmation"):
            raise NaturalEngineError(
                "generation intent contains ambiguous local assets that require confirmation",
                code="asset_confirmation_required",
                status=409,
                details={"requires_confirmation": list(plan["requires_confirmation"])},
            )
        job_type = str(plan["job_type"])
        settings = self._runtime_settings()
        selected_model = str(payload.get("model") or payload.get("model_name") or plan.get("model") or "").strip()
        if selected_model:
            settings = replace(settings, unet_model_name=selected_model)
        options = self._options(payload, plan)
        workflow_dir = self.workflow_assets_dir / "workflow"
        try:
            if job_type == "text_to_image":
                builder = WorkflowBuilder(
                    settings.resolve_pipeline_workflow_path(
                        self.workflow_assets_dir, str(plan["pipeline"])
                    ),
                    settings,
                )
                return self._bypass_empty_lora_manager(builder.build(options), options)
            if job_type == "character_swap":
                builder = Img2ImgWorkflowBuilder(workflow_dir / "anima_img2img_api.json", settings)
                return self._bypass_empty_lora_manager(
                    builder.build_img2img(uploaded["source"], options), options
                )
            if job_type == "control":
                builder = ControlWorkflowBuilder(workflow_dir / "anima_control_api.json", settings)
                return self._bypass_empty_lora_manager(
                    builder.build_control(uploaded["source"], options), options
                )
            if job_type == "img2img":
                builder = Img2ImgWorkflowBuilder(workflow_dir / "anima_img2img_api.json", settings)
                return self._bypass_empty_lora_manager(
                    builder.build_img2img(uploaded["source"], options), options
                )
            if job_type == "inpaint":
                mode = options.inpaint_mode or "quick"
                builder = InpaintWorkflowBuilder(
                    settings.resolve_inpaint_workflow_path(self.workflow_assets_dir, mode), settings
                )
                return self._bypass_empty_lora_manager(
                    builder.build(uploaded["source"], uploaded["mask"], options), options
                )
            if job_type == "upscale":
                quality = str(payload.get("quality") or settings.rtx_quality).upper()
                if quality not in {"LOW", "MEDIUM", "HIGH", "ULTRA"}:
                    raise NaturalEngineError(
                        "RTX quality 必须是 LOW、MEDIUM、HIGH 或 ULTRA",
                        code="invalid_rtx_quality",
                    )
                builder = ImageWorkflowBuilder(
                    settings.resolve_upscale_workflow_path(self.workflow_assets_dir), settings
                )
                workflow, outputs = builder.build(
                    uploaded["source"],
                    scale=float(payload.get("scale") or settings.rtx_scale),
                    quality=quality,
                )
                return workflow, 0, outputs
        except (WorkflowError, KeyError, ValueError) as exc:
            raise NaturalEngineError(f"工作流构建失败: {exc}", code="workflow_build_failed") from exc
        raise NaturalEngineError("该任务不会提交 ComfyUI")

    async def validate_workflow_dependencies(
        self,
        payload: Mapping[str, Any],
        plan: Mapping[str, Any],
        comfy: Any,
    ) -> None:
        """Validate the concrete, pipeline-pruned workflow before it enters the queue."""
        if str(plan.get("job_type") or "") == "reverse":
            return
        workflow, _, preferred_nodes = self.build_workflow(
            payload,
            plan,
            {"source": "dependency-check.png", "mask": "dependency-check-mask.png"},
        )
        try:
            object_info = await comfy.object_info()
        except Exception as exc:
            raise NaturalEngineError(
                f"无法连接 ComfyUI 复核工作流依赖: {exc}",
                code="comfy_unavailable",
                status=503,
            ) from exc
        if not object_info:
            raise NaturalEngineError(
                "ComfyUI 节点清单为空，无法提交任务",
                code="comfy_unavailable",
                status=503,
            )
        required = {
            str(node.get("class_type"))
            for node in workflow.values()
            if isinstance(node, Mapping) and node.get("class_type")
        }
        missing = sorted(name for name in required if name not in object_info)
        if missing:
            raise NaturalEngineError(
                "工作流缺少 ComfyUI 节点: " + ", ".join(missing),
                code="workflow_unavailable",
                status=422,
            )
        contract = self._workflow_contract(
            {
                "bindings": {},
                "output_variants": {
                    "selected": {"preferred_node_ids": preferred_nodes}
                },
            },
            workflow,
            object_info,
        )
        if contract["errors"]:
            raise NaturalEngineError(
                "工作流合同校验失败: " + "; ".join(contract["errors"]),
                code="workflow_contract_invalid",
                status=422,
            )
        swap = plan.get("character_swap")
        raw_loras = (
            swap.get("loras")
            if isinstance(swap, Mapping)
            else payload.get("loras")
        ) or []
        if raw_loras:
            inventory = await comfy.lora_inventory()
            names = {
                str(item.get("filename") or item.get("name") or "").replace("\\", "/").casefold()
                for item in inventory.get("items") or []
                if isinstance(item, Mapping)
            }
            requested_loras = [
                str(item.get("filename") or item.get("name") or item.get("path") or "").strip()
                for item in raw_loras
                if isinstance(item, Mapping)
            ]
            missing_loras = sorted(
                name
                for name in requested_loras
                if name and name.replace("\\", "/").casefold() not in names
            )
            if missing_loras:
                raise NaturalEngineError(
                    "LoRA 不在实时清单中: " + ", ".join(missing_loras),
                    code="lora_not_found",
                    status=422,
                )

    def _manifest_for_plan(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        job_type = str(plan.get("job_type") or "")
        pipeline = str(plan.get("pipeline") or "base")
        filename = {
            "text_to_image": {
                "base": "anima_base_api.json",
                "rtx": "anima_rtx_api.json",
                "iterative": "anima_iterative_api.json",
            }.get(pipeline, "anima_base_api.json"),
            "control": "anima_control_api.json",
            "img2img": "anima_img2img_api.json",
            "character_swap": "anima_img2img_api.json",
            "inpaint": "anima_lanpaint_api.json"
            if str(plan.get("inpaint_mode") or "") == "lanpaint"
            else "anima_inpaint_crop_api.json",
            "upscale": "rtx_upscale_api.json",
        }.get(job_type, "")
        if not filename:
            return {}
        path = self.workflow_assets_dir / "workflow" / "manifests" / filename
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _workflow_contract(
        manifest: Mapping[str, Any],
        workflow: Mapping[str, Any],
        object_info: Mapping[str, Any],
    ) -> dict[str, list[str]]:
        errors: list[str] = []
        missing_models: list[str] = []

        def check_binding(value: Any, label: str) -> None:
            if isinstance(value, Mapping):
                node_id = value.get("node_id")
                if node_id is not None:
                    node = workflow.get(str(node_id))
                    if not isinstance(node, Mapping):
                        if label.endswith(".lora"):
                            return
                        errors.append(f"{label} 节点 {node_id} 不存在")
                        return
                    inputs = node.get("inputs") if isinstance(node.get("inputs"), Mapping) else {}
                    for key, input_name in value.items():
                        if key == "input" or key.endswith("_input"):
                            if str(input_name) not in inputs:
                                errors.append(
                                    f"{label} 节点 {node_id} 缺少输入 {input_name}"
                                )
                    return
                for key, item in value.items():
                    check_binding(item, f"{label}.{key}")
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    check_binding(item, f"{label}[{index}]")

        check_binding(manifest.get("bindings") or {}, "bindings")
        variants = manifest.get("output_variants") or {}
        for variant, config in variants.items() if isinstance(variants, Mapping) else ():
            for node_id in config.get("preferred_node_ids") or [] if isinstance(config, Mapping) else []:
                node = workflow.get(str(node_id))
                if not isinstance(node, Mapping):
                    errors.append(f"输出变体 {variant} 的节点 {node_id} 不存在")
                    continue
                info = object_info.get(str(node.get("class_type") or ""))
                if isinstance(info, Mapping) and info.get("output_node") is False:
                    errors.append(f"输出变体 {variant} 的节点 {node_id} 不是输出节点")

        for node_id, node in workflow.items():
            if not isinstance(node, Mapping):
                continue
            class_name = str(node.get("class_type") or "")
            info = object_info.get(class_name)
            if not isinstance(info, Mapping):
                continue
            declared: dict[str, Any] = {}
            input_info = info.get("input")
            if isinstance(input_info, Mapping):
                for group in ("required", "optional"):
                    values = input_info.get(group)
                    if isinstance(values, Mapping):
                        declared.update(values)
            inputs = node.get("inputs") if isinstance(node.get("inputs"), Mapping) else {}
            for input_name, input_value in inputs.items():
                spec = declared.get(str(input_name))
                if not isinstance(spec, list) or not spec:
                    continue
                choices = spec[0]
                if (
                    (
                        "loader" in class_name.casefold()
                        or str(input_name).casefold().endswith("_name")
                    )
                    and class_name != "LoadImage"
                    and isinstance(choices, list)
                    and choices
                    and all(isinstance(item, str) for item in choices)
                    and isinstance(input_value, str)
                    and input_value not in choices
                ):
                    missing_models.append(
                        f"{node_id}.{input_name}={input_value}"
                    )
        errors.extend(f"模型或枚举值不可用: {item}" for item in missing_models)
        return {"errors": errors, "missing_models": missing_models}

    async def capabilities(self, comfy: Any) -> dict[str, Any]:
        workflow_dir = self.workflow_assets_dir / "workflow"
        workflows: list[dict[str, Any]] = []
        object_info: dict[str, Any] = {}
        comfy_error = ""
        try:
            object_info = await comfy.object_info()
        except Exception as exc:
            comfy_error = str(exc)
        manifest_dir = workflow_dir / "manifests"
        for manifest_path in sorted(manifest_dir.glob("*.json")):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                workflow_path = workflow_dir / str(manifest.get("workflow_file") or "")
                workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
                classes = sorted(
                    {
                        str(node.get("class_type"))
                        for node in workflow.values()
                        if isinstance(node, Mapping) and node.get("class_type")
                    }
                )
                missing_all = [name for name in classes if object_info and name not in object_info]
                optional_missing = [
                    name for name in missing_all if name == "Lora Loader (LoraManager)"
                ]
                missing = [name for name in missing_all if name not in optional_missing]
                contract = self._workflow_contract(manifest, workflow, object_info)
                workflows.append(
                    {
                        "id": manifest.get("profile_id"),
                        "name": manifest.get("display_name"),
                        "task_type": manifest.get("task_type"),
                        "file": workflow_path.name,
                        "ready": bool(object_info) and not missing and not contract["errors"],
                        "missing_nodes": missing,
                        "optional_missing_nodes": optional_missing,
                        "contract_errors": contract["errors"],
                        "missing_models": contract["missing_models"],
                    }
                )
            except (OSError, ValueError, TypeError) as exc:
                workflows.append(
                    {"id": manifest_path.stem, "name": manifest_path.stem, "ready": False, "error": str(exc)}
                )
        return {
            "comfy_online": bool(object_info),
            "comfy_error": comfy_error,
            "providers": self.registry.snapshot(),
            "workflows": workflows,
            "danbooru": self.danbooru.status(),
            "features": sorted(self.JOB_TYPES),
        }

    def danbooru_search(self, query: str, category: str = "") -> dict[str, Any]:
        candidates = self.danbooru.search(query, category=category, limit=30)
        return {
            "items": [asdict(item) for item in candidates],
            "status": self.danbooru.status(),
        }

    async def close(self) -> None:
        await self.provider_client.close()
