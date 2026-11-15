"""Bounded model-header inspection. No torch, pickle or weight loading here."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

MAX_HEADER_BYTES = 32 * 1024 * 1024
MAX_TENSORS = 200_000
KINDS = {"models": "diffusion_models", "clips": "text_encoders", "vaes": "vae", "upscale_models": "upscale_models"}
_FILE_CACHE: dict[tuple[str, str], tuple[tuple, dict[str, Any]]] = {}


def read_header(path: str | Path) -> tuple[dict[str, Any], str]:
    """Read only a safetensors JSON header and verify offsets are in bounds."""
    file = Path(path)
    before = file.stat()
    with file.open("rb") as stream:
        prefix = stream.read(8)
        if len(prefix) != 8:
            raise ValueError("模型头不完整")
        length = int.from_bytes(prefix, "little")
        if not 2 <= length <= MAX_HEADER_BYTES or length > before.st_size - 8:
            raise ValueError("模型头长度无效或超过检查上限")
        raw = stream.read(length)
    if len(raw) != length:
        raise ValueError("模型头读取不完整")
    header = json.loads(raw)
    if not isinstance(header, dict) or len(header) > MAX_TENSORS:
        raise ValueError("模型头格式无效")
    payload = before.st_size - 8 - length
    tensors = 0
    for key, value in header.items():
        if key == "__metadata__":
            if not isinstance(value, dict):
                raise ValueError("模型元数据格式无效")
            continue
        tensors += 1
        if not isinstance(value, dict) or not isinstance(value.get("dtype"), str):
            raise ValueError("张量信息无效")
        shape, offsets = value.get("shape"), value.get("data_offsets")
        if not isinstance(shape, list) or any(type(n) is not int or n < 0 for n in shape):
            raise ValueError("张量维度无效")
        if not isinstance(offsets, list) or len(offsets) != 2 or any(type(n) is not int for n in offsets) or not 0 <= offsets[0] <= offsets[1] <= payload:
            raise ValueError("张量数据范围无效")
    if not tensors:
        raise ValueError("模型没有张量")
    after = file.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("检查期间模型文件发生变化，请刷新重试")
    identity = f"{file.resolve()}|{after.st_dev}|{after.st_ino}|{after.st_size}|{after.st_mtime_ns}|{hashlib.sha256(raw).hexdigest()}"
    return header, "v1:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def file_fingerprint(path: str | Path) -> str:
    """Identity for formats that are never deserialized by this extension."""
    file = Path(path)
    stat = file.stat()
    identity = f"{file.resolve()}|{stat.st_dev}|{stat.st_ino}|{stat.st_size}|{stat.st_mtime_ns}"
    return "stat-v1:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def recognize_header(header: dict[str, Any], kind: str, *, _cross_check: bool = True) -> tuple[list[str], str]:
    """Conservative signatures from supported ComfyUI loader branches.

    The signatures use tensor names AND shape where architecture dimensions
    differ. Names of files and embedded prose metadata are not evidence.
    """
    values = {key: value for key, value in header.items() if key != "__metadata__"}
    for prefix in ("model.diffusion_model.", "diffusion_model.", "first_stage_model.", "net."):
        if values and all(key.startswith(prefix) for key in values):
            values = {key[len(prefix):]: value for key, value in values.items()}

    def shape(key: str) -> list[int]:
        value = values.get(key)
        return value.get("shape", []) if isinstance(value, dict) else []

    roles: list[str] = []
    known_other = False
    if kind == "models":
        if "blocks.0.mlp.layer1.weight" in values and "llm_adapter.blocks.0.cross_attn.q_proj.weight" in values:
            roles = ["anima.model"]
        elif "txtfusion.projector.weight" in values and "first.weight" in values and "blocks.0.attn.wq.weight" in values:
            roles = ["krea2.model"]
        elif ("blocks.31.mlp.all.proj_in_gate.weight" in values or "blocks.35.mlp.all.proj_in_gate.weight" in values
              or shape("blocks.35.mlp.vid.proj_out.weight")[:1] == [3072]):
            roles = ["seedvr2.model"]
        else:
            known_other = any(key in values for key in ("input_blocks.0.0.weight", "double_blocks.0.img_attn.qkv.weight", "head.modulation"))
    elif kind == "clips":
        if "model.visual.deepstack_merger_list.0.norm.weight" in values:
            if shape("model.visual.merger.linear_fc2.weight")[:1] == [2560]:
                roles = ["krea2.clip"]
            else:
                known_other = True
        elif "model.layers.0.self_attn.q_norm.weight" in values and "model.layers.0.post_attention_layernorm.weight" in values:
            if shape("model.layers.0.post_attention_layernorm.weight") == [1024]:
                roles = ["anima.clip"]
            else:
                known_other = True
        else:
            known_other = any(key in values for key in ("text_model.encoder.layers.0.mlp.fc1.weight", "encoder.block.0.layer.0.SelfAttention.k.weight"))
    elif kind == "vaes":
        if "decoder.up_blocks.2.upsamplers.0.upscale_conv.weight" in values:
            roles = ["seedvr2.vae"]
        elif "decoder.middle.0.residual.0.gamma" in values:
            # Wan 2.2/Qwen Image 2.1 use different latent channels/scale.
            if ("decoder.upsamples.0.upsamples.0.residual.2.weight" not in values
                    and shape("decoder.head.2.weight")[:1] == [3]
                    and len(shape("encoder.conv1.weight")) >= 2 and shape("encoder.conv1.weight")[1] == 3):
                roles = ["anima.vae", "krea2.vae"]
            else:
                known_other = True
        else:
            known_other = "decoder.conv_in.weight" in values
    elif kind == "upscale_models":
        # This loader has one image-upscale role; no cross-family text/latent
        # pairing is involved. ComfyUI/spandrel validates its supported format.
        roles = ["upscale"]
    if _cross_check and (not roles or kind == "upscale_models"):
        for other in ("models", "clips", "vaes"):
            if other != kind and recognize_header(header, other, _cross_check=False)[0]:
                return [], "incompatible"
    return roles, "recognized" if roles else "incompatible" if known_other else "unknown"


def inspect_file(kind: str, name: str, path: str | Path) -> dict[str, Any]:
    item: dict[str, Any] = {"kind": kind, "name": name.replace("\\", "/"), "roles": [],
                            "source": "header", "status": "unknown", "fingerprint": ""}
    try:
        if Path(path).suffix.casefold() in {".safetensors", ".sft"}:
            header, item["fingerprint"] = read_header(path)
            item["roles"], item["status"] = recognize_header(header, kind)
        else:
            item["fingerprint"] = file_fingerprint(path)
            item["source"] = "loader" if kind == "upscale_models" else "file"
            if kind == "upscale_models":
                item.update(roles=["upscale"], status="recognized")
    except (OSError, ValueError, UnicodeError, OverflowError) as error:
        item.update(status="invalid", message=str(error))
    return item


def inspect_inventory(folder_paths: Any) -> list[dict[str, Any]]:
    result = []
    active = set()
    for kind, category in KINDS.items():
        try:
            names = folder_paths.get_filename_list(category)
        except (KeyError, OSError):
            names = []
        for name in names:
            try:
                path = folder_paths.get_full_path(category, name)
            except (KeyError, OSError):
                path = None
            if path:
                key = (kind, str(Path(path).resolve()))
                active.add(key)
                try:
                    stat = Path(path).stat()
                    signature = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                except OSError:
                    signature = None
                cached = _FILE_CACHE.get(key)
                if signature is not None and cached and signature == cached[0]:
                    item = copy.deepcopy(cached[1])
                    item["name"] = name.replace("\\", "/")
                else:
                    item = inspect_file(kind, name, path)
                    if signature is not None:
                        _FILE_CACHE[key] = (signature, copy.deepcopy(item))
                result.append(item)
    for key in list(_FILE_CACHE):
        if key not in active:
            _FILE_CACHE.pop(key, None)
    return result


def inspect_lora_files(folder_paths: Any) -> list[dict[str, str]]:
    """Record registered LoRA file identities without inferring their roles."""
    try:
        names = folder_paths.get_filename_list("loras")
    except (KeyError, OSError):
        names = []
    result = []
    for name in names:
        item = {"name": name.replace("\\", "/"), "fingerprint": "", "status": "invalid"}
        try:
            path = folder_paths.get_full_path("loras", name)
            if path and Path(path).is_file():
                item.update(fingerprint=file_fingerprint(path), status="available")
        except OSError:
            pass
        result.append(item)
    return result
