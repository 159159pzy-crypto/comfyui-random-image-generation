"""One pure resource-selection contract for the editor, queue and history."""
from __future__ import annotations

import copy
from typing import Any

from .model_profiles import get_model_profile
from .resource_usage import RESOURCE_ROLES, ResourceUsageStore


def _identity(value: Any) -> str:
    return str(value or "").replace("\\", "/").casefold()


def build_resource_catalog(inventory: dict[str, Any], model_items: list[dict[str, Any]] | None = None,
                           usage_store: ResourceUsageStore | None = None) -> list[dict[str, Any]]:
    """Join real loader names to inspected file roles and current confirmations.

    A manager classification can identify a base model, but a filename cannot.
    The inspector is authoritative about malformed or recognized tensor data.
    """
    inspection = inventory.get("resource_inspection") or {}
    inspected = {(item.get("kind"), _identity(item.get("name"))): item
                 for item in inspection.get("items", []) if isinstance(item, dict)}
    classified = {_identity(item.get("name") or item.get("filename")): item
                  for item in model_items or inventory.get("model_items", [])}
    result = []
    for kind in RESOURCE_ROLES:
        seen = set()
        for raw_name in inventory.get(kind) or []:
            name = str(raw_name).replace("\\", "/")
            identity = _identity(name)
            if identity in seen:
                continue
            seen.add(identity)
            item = {"kind": kind, "name": name, "roles": [], "source": "inventory",
                    "status": "unknown", "fingerprint": ""}
            probe = inspected.get((kind, identity))
            if probe:
                item.update({key: copy.deepcopy(probe[key]) for key in
                             ("roles", "source", "status", "fingerprint", "message") if key in probe})
                item["roles"] = [role for role in item["roles"] if role in RESOURCE_ROLES[kind]]
            metadata = classified.get(identity) if kind == "models" else None
            if metadata:
                item.update({key: metadata[key] for key in ("classification_reason", "base_model") if key in metadata})
            if item["status"] == "unknown" and metadata:
                profile = metadata.get("model_profile")
                status = metadata.get("classification_status")
                if profile == "anima" and status in {None, "recognized"}:
                    item.update(roles=[profile + ".model"], status="recognized", source="lora-manager")
                elif status == "unsupported":
                    item.update(status="incompatible", source="lora-manager",
                                message=metadata.get("classification_reason", "模型架构不受支持"))
                if not item["fingerprint"] and metadata.get("sha256"):
                    item["fingerprint"] = "sha256:" + str(metadata["sha256"]).lower()
            if kind == "upscale_models" and item["status"] == "unknown":
                item.update(roles=["upscale"], status="recognized", source="loader")
            if item["status"] == "unknown" and usage_store:
                usage = usage_store.lookup(kind, name, item["fingerprint"])
                if usage:
                    item.update(roles=usage["roles"], status="recognized", source="manual")
            result.append(item)
    return sorted(result, key=lambda item: (item["kind"], _identity(item["name"]), item["name"]))


def _get(settings: dict[str, Any], field: str, default: Any = None) -> Any:
    value: Any = settings
    for part in field.split("."):
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return value


def _set(settings: dict[str, Any], field: str, value: Any) -> None:
    target = settings
    parts = field.split(".")
    for part in parts[:-1]:
        target = target.setdefault(part, {})
    target[parts[-1]] = value


def merge_resource_replacements(*groups: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep one original-to-effective record per field across rechecks."""
    records: dict[str, dict[str, str]] = {}
    for group in groups:
        for change in group:
            field = change["field"]
            previous = records.get(field)
            record = dict(change)
            if previous:
                record["from"] = previous["from"]
                if change.get("reason") == "path_normalized":
                    record["reason"] = previous.get("reason", "missing_model")
            records[field] = record
    return [item for item in records.values() if item["from"] != item["to"]]


def resources_changed(replacements: list[dict[str, str]]) -> bool:
    return any(item.get("reason") != "path_normalized" for item in replacements)


def resolve_resources(settings: dict[str, Any], inventory: dict[str, Any], *, strict: bool = False, allow_basename: bool | None = None) -> dict[str, Any]:
    """Resolve missing weights without mutating settings or choosing unknowns.

    Missing enabled LoRAs remain errors. Strict historical replay keeps exact
    saved resource paths; other callers receive and record substitutions.
    """
    if allow_basename is None:
        allow_basename = not strict
    effective = copy.deepcopy(settings)
    issues: list[dict[str, str]] = []
    replacements: list[dict[str, str]] = []
    selected_resources: list[dict[str, str]] = []
    profile = get_model_profile(settings.get("model_profile", "anima"))
    profile_id = profile["id"]
    stages = settings.get("upscalers") or {}
    catalog = inventory.get("resource_catalog")
    if not isinstance(catalog, list):
        catalog = build_resource_catalog(inventory)
    by_kind = {kind: [item for item in catalog if item.get("kind") == kind] for kind in RESOURCE_ROLES}

    def issue(module: str, field: str, code: str, message: str) -> None:
        entry = {"module": module, "field": field, "code": code, "message": message}
        if entry not in issues:
            issues.append(entry)

    def choose(module: str, field: str, kind: str, role: str, recommended: str) -> None:
        if kind not in inventory and not by_kind[kind]:
            # Embedded/legacy clients may not advertise codecs at all. An
            # explicitly advertised [] is missing and does not take this path.
            return
        current = str(_get(settings, field, recommended) or "")
        items = by_kind[kind]
        compatible = [item for item in items if item.get("status") == "recognized" and role in item.get("roles", [])]
        exact = next((item for item in items if _identity(item["name"]) == _identity(current)), None)
        # A basename is accepted only when it names one actual loader choice.
        matches = [item for item in items if _identity(item["name"]).rsplit("/", 1)[-1] == _identity(current).rsplit("/", 1)[-1]] if current and allow_basename and "/" not in _identity(current) else []
        if not exact and len(matches) > 1:
            issue(module, field, "ambiguous_path", f"{current} 的 basename 匹配不唯一，请选择完整模型路径")
            return
        selected = exact or (matches[0] if len(matches) == 1 else None)
        inspection = inventory.get("resource_inspection") or {}
        # An older ComfyUI can still execute an explicitly selected codec through
        # its real loader. Without the inspection extension we do not infer a
        # role from its filename, select another codec, or claim verified identity.
        legacy_codec = (kind in {"clips", "vaes"} and selected is not None
                        and selected.get("status") == "unknown"
                        and inspection.get("supported") is not True and not inspection.get("items"))
        if selected in compatible or legacy_codec:
            target = selected
        elif strict:
            target = None
        else:
            preferred = [item for item in compatible if _identity(item["name"]) == _identity(recommended)]
            if not preferred:
                preferred = [item for item in compatible if _identity(item["name"]).rsplit("/", 1)[-1] == _identity(recommended).rsplit("/", 1)[-1]]
            target = sorted(preferred or compatible, key=lambda item: (_identity(item["name"]), item["name"]))[0] if compatible else None
        if target:
            _set(effective, field, target["name"])
            selected_resources.append({"field": field, "kind": kind, "name": target["name"],
                                       "fingerprint": "" if legacy_codec else str(target.get("fingerprint") or "")})
            if current != target["name"]:
                reason = ("path_normalized" if selected is target else
                          "unknown_compatibility" if selected and selected.get("status") == "unknown" else
                          "incompatible_model" if selected else "missing_model")
                replacements.append({"module": module, "field": field, "from": current, "to": target["name"], "reason": reason})
            return
        status = selected.get("status") if selected else None
        if status == "unknown":
            reason = selected.get("classification_reason") or selected.get("base_model") or "Unknown"
            issue(module, field, "unknown_compatibility", f"无法确认 {current} 的兼容用途（{reason}），请先指定用途或安装兼容模型")
        elif status in {"invalid", "incompatible"} or selected and role not in selected.get("roles", []):
            reason = selected.get("classification_reason") or selected.get("base_model") or ", ".join(selected.get("roles", []))
            issue(module, field, "incompatible_model", f"{current} 不适用于当前模块（{reason}），未找到兼容替代模型")
        elif len(matches) > 1:
            issue(module, field, "ambiguous_model", f"{current} 存在多个同名路径，请选择完整模型路径")
        else:
            issue(module, field, "missing_model", f"缺少可用于当前模块的模型：{current or recommended}")

    choose("base", "model_name", "models", profile_id + ".model", profile["model_name"])
    choose("base", "clip_name", "clips", profile_id + ".clip", profile["clip_name"])
    choose("base", "vae_name", "vaes", profile_id + ".vae", profile["vae_name"])
    if strict and effective.get("clip_type") != profile["clip_type"]:
        issue("base", "clip_type", "incompatible_encoder_type", "历史编码器类型不兼容，严格复现不会替换")
    elif effective.get("clip_type") != profile["clip_type"]:
        replacements.append({"module": "base", "field": "clip_type", "from": str(effective.get("clip_type") or ""), "to": profile["clip_type"], "reason": "incompatible_encoder_type"})
        effective["clip_type"] = profile["clip_type"]
    if stages.get("pixel_hires", {}).get("enabled"):
        choose("pixel_hires", "upscalers.pixel_hires.model_name", "upscale_models", "upscale", profile["pixel_hires_model"])
    if stages.get("seedvr2", {}).get("enabled"):
        choose("seedvr2", "upscalers.seedvr2.model_name", "models", "seedvr2.model", profile["seedvr2_model"])
        choose("seedvr2", "upscalers.seedvr2.vae_name", "vaes", "seedvr2.vae", profile["seedvr2_vae"])
    face = stages.get("face_refine") or {}
    if face.get("enabled") and face.get("enable_pre_upscale"):
        face_model = str(face.get("upscaler_model") or "")
        if face_model and not face_model.startswith("Fast "):
            choose("face_refine", "upscalers.face_refine.upscaler_model", "upscale_models", "upscale", profile["pixel_hires_model"])
        face_schema = (inventory.get("node_inputs") or {}).get("ForbiddenVisionFaceProcessorIntegrated", {}).get("upscaler_model")
        fast_choices = {"Fast 4x (Bicubic AA)", "Fast 4x (Lanczos)", "Fast 2x (Bicubic AA)", "Fast 2x (Lanczos)"}
        face_choices = None
        if isinstance(face_schema, list) and face_schema:
            face_choices = face_schema[0] if isinstance(face_schema[0], list) else (face_schema[1].get("options") if len(face_schema) > 1 and isinstance(face_schema[1], dict) else None)
        resolved_face = _get(effective, "upscalers.face_refine.upscaler_model", "")
        if (face_choices is not None and resolved_face not in face_choices
                or str(resolved_face).startswith("Fast ") and resolved_face not in fast_choices):
            issue("face_refine", "upscalers.face_refine.upscaler_model", "invalid_choice", f"当前人脸节点不支持预放大选项：{resolved_face}")

    node_types = inventory.get("node_types")
    nodes = set(node_types) if isinstance(node_types, list) else None
    if nodes is None and isinstance(inventory.get("nodes"), dict):
        # Compact old inventories only describe selected classes, not the
        # entire graph; missing keys are unknown rather than absent.
        flags = inventory["nodes"]
    else:
        flags = {}

    def node(module: str, name: str) -> None:
        if nodes is not None and name not in nodes or nodes is None and flags.get(name) is False:
            issue(module, "nodes." + name, "missing_node",
                  "缺少清缓存和垃圾回收节点 VRAM_Debug，请安装或更新 ComfyUI-KJNodes 后重启 ComfyUI"
                  if name == "VRAM_Debug" else f"缺少节点：{name}")

    for name in ("UNETLoader", "CLIPLoader", "VAELoader", "CLIPTextEncode", "SaveImage"):
        node("base", name)
    node("base", "VRAM_Debug")
    if settings.get("zero_negative"):
        node("base", "ConditioningZeroOut")
    dual = settings.get("base_mode") == "dual_sampler"
    node("base_dual" if dual else "base", "KSamplerAdvanced" if dual else "KSampler")
    stage_nodes = {
        "pixel_hires": ("UpscaleModelLoader", "ImageUpscaleWithModel"),
        "latent": (), "post_sampler": ("KSampler",),
        "face_refine": ("ForbiddenVisionFaceProcessorIntegrated",),
        "seedvr2": ("UNETLoader", "VAELoader", "SeedVR2Preprocess", "SeedVR2Conditioning", "SeedVR2PostProcessing", "VAEEncodeTiled", "VAEDecodeTiled", "KSampler", "ImageScaleBy"),
        "nvidia": ("RTXVideoSuperResolution",), "dlss": ("DLSS5NeuralRender",),
    }
    for module, names in stage_nodes.items():
        if stages.get(module, {}).get("enabled"):
            for name in names:
                node(module, name)
    if stages.get("pixel_hires", {}).get("enabled"):
        from .sampling import pixel_hires_resizes
        if pixel_hires_resizes(settings):
            node("pixel_hires", "ImageScaleBy")
    if stages.get("latent", {}).get("enabled"):
        node("latent", "KSampler")

    capabilities = inventory.get("capabilities") or (inventory.get("resource_inspection") or {}).get("capabilities") or {}

    def capability(module: str, key: str) -> None:
        value = capabilities.get(key)
        # Older peers have no capabilities API. Do not invent success; the
        # node/resource checks still run and the UI can show probe support.
        if not isinstance(value, dict):
            return
        status = value.get("status")
        if status != "ready":
            issue(module, "capabilities." + key, "dependency_" + str(status or "unknown"),
                  str(value.get("message") or "尚未确认模块依赖，请检查运行环境"))

    if dual:
        config = settings.get("dual") or stages.get("latent", {})
        node("base_dual", "SesquiLatentUpscale" if config.get("upscale_method", "sesqui") == "sesqui" else "LatentUpscaleBy")
        if config.get("upscale_method", "sesqui") == "sesqui":
            capability("base_dual", "sesqui." + config.get("model_format", "Wan 2.1"))
    if stages.get("latent", {}).get("enabled"):
        config = stages["latent"]
        node("latent", "SesquiLatentUpscale" if config.get("upscale_method", "sesqui") == "sesqui" else "LatentUpscaleBy")
        if config.get("upscale_method", "sesqui") == "sesqui":
            capability("latent", "sesqui." + config.get("model_format", "Wan 2.1"))
    if face.get("enabled"):
        capability("face_refine", "face.detect")
        if face.get("enable_segmentation", True):
            capability("face_refine", "face.segment")
    for module, key in (("nvidia", "nvidia.vsr"), ("dlss", "nvidia.dlss")):
        if stages.get(module, {}).get("enabled"):
            capability(module, key)

    # This layer never replaces a LoRA with a semantically different LoRA.
    # The API's ownership and frozen-binding checks remain authoritative too.
    lora_names = inventory.get("loras")
    lora_files = inventory.get("lora_files")
    file_identities = {}
    if isinstance(lora_files, list):
        file_identities = {_identity(item.get("name")): item for item in lora_files
                           if isinstance(item, dict) and isinstance(item.get("name"), str)}
        lora_names = list(file_identities)
    if isinstance(lora_names, list):
        available = {_identity(item.get("filename") or item.get("name")) if isinstance(item, dict) else _identity(item) for item in lora_names}
        from .graph_utils import active_lora_pipelines
        try:
            active = active_lora_pipelines(settings)
        except (KeyError, TypeError):
            active = {"primary"}
        for stack, values in (("loras", settings.get("loras") or []),):
            if (stack == "loras" and "primary" not in active or stack != "loras" and "secondary" not in active):
                continue
            for index, value in enumerate(values):
                if not value.get("enabled"):
                    continue
                field = f"{stack}.{index}.filename"
                name = value.get("filename", "")
                identity = _identity(name)
                if identity not in available:
                    issue("loras", field, "missing_lora", f"启用的 LoRA 不存在：{name}")
                    continue
                file = file_identities.get(identity)
                if file is not None:
                    fingerprint = file.get("fingerprint")
                    if file.get("status") == "invalid" or not isinstance(fingerprint, str) or not fingerprint:
                        issue("loras", field, "invalid_lora", f"无法确认启用的 LoRA 文件身份：{name}")
                    else:
                        selected_resources.append({"field": field, "kind": "loras", "name": file["name"],
                                                   "fingerprint": fingerprint})
    return {"version": 1, "effective_settings": effective, "issues": issues,
            "replacements": replacements, "resources": selected_resources, "ready": not issues}
