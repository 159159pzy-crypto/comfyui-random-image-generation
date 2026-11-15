"""Versioned, read-only adapters for optional node dependencies."""
from __future__ import annotations

import asyncio
import ctypes
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import re
import sys
import time
from typing import Any

from .inspector import file_fingerprint, read_header

_NATIVE_CACHE: dict[tuple, tuple[float, dict]] = {}
_NATIVE_LOCK: asyncio.Lock | None = None
_NATIVE_RETRY_SECONDS = 60


def _status(status: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, "message": message, **extra}


def _cached_native(identity: tuple) -> dict[str, Any] | None:
    cached = _NATIVE_CACHE.get(identity)
    if cached and (cached[1].get("status") == "ready" or time.monotonic() - cached[0] < _NATIVE_RETRY_SECONDS):
        # Success belongs to the complete runtime identity, not an idle-time
        # window: a long generation must not force another smoke test.
        return dict(cached[1])
    return None


def _node_root(nodes: Any, name: str) -> Path | None:
    cls = getattr(nodes, "NODE_CLASS_MAPPINGS", {}).get(name)
    module = sys.modules.get(getattr(cls, "__module__", ""))
    path = getattr(module, "__file__", None)
    return Path(path).resolve().parent if path else None


def _package(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def _file(path: Path, label: str) -> dict[str, Any]:
    try:
        if not path.is_file() or path.stat().st_size < 16:
            return _status("missing", "缺少" + label)
        if path.suffix == ".safetensors":
            read_header(path)
        return _status("ready", label + "文件已就绪", verification="file")
    except (OSError, ValueError, UnicodeError) as error:
        return _status("missing", label + "文件无效：" + str(error))


def _onnx_detection() -> dict[str, Any]:
    """Match the detector's actual provider selection without loading weights."""
    try:
        ort = importlib.import_module("onnxruntime")
        providers = set(ort.get_available_providers())
        management = sys.modules.get("comfy.model_management")
        device = management.get_torch_device() if management else None
        requested = {"CUDAExecutionProvider", "ROCMExecutionProvider"} if device is not None and device.type == "cuda" else {"CPUExecutionProvider"}
        matching = sorted(providers & requested)
        if matching:
            return _status("ready", "人脸检测 ONNX 文件与设备执行提供器已就绪", verification="provider", providers=matching)
        return _status("missing", "ONNX 未提供当前 ComfyUI 设备所需的执行提供器；可安装 PT 检测权重")
    except Exception as error:
        return _status("missing", "ONNX 运行库无法初始化：" + str(error))


def _driver_version() -> str:
    if sys.platform != "win32":
        return ""
    try:
        driver = ctypes.WinDLL("nvcuda.dll")
        value = ctypes.c_int()
        result = driver.cuDriverGetVersion(ctypes.byref(value))
        return str(value.value) if result == 0 else "unavailable"
    except (OSError, AttributeError):
        return "unavailable"


def inspect_capabilities(folder_paths: Any, nodes: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    result = {}
    sesqui = _node_root(nodes, "SesquiLatentUpscale")
    try:
        roots = [Path(root) for root in folder_paths.get_folder_paths("sesqui_lsr")]
    except KeyError:
        roots = []
    if sesqui:
        roots += [sesqui / "models", sesqui]
    for fmt, filename in (("SDXL", "upscaler_SDXL.safetensors"), ("Flux", "upscaler_Flux.safetensors"),
                          ("Flux2", "upscaler_Flux2.safetensors"), ("Ideogram 4", "upscaler_Flux2.safetensors"),
                          ("Wan 2.1", "upscaler_Wan21.safetensors")):
        found = next((root / filename for root in roots if (root / filename).is_file()), None)
        result["sesqui." + fmt] = _file(found, "Sesqui " + fmt + " 权重") if found else _status("missing", "缺少 Sesqui 权重：" + filename)

    face_dir = Path(folder_paths.models_dir) / "forbidden_vision"
    pt = _file(face_dir / "ForbiddenVision_face_detect_v1.pt", "人脸检测 PT")
    onnx = _file(face_dir / "ForbiddenVision_face_detect_v1.onnx", "人脸检测 ONNX")
    if not _package("ultralytics"):
        result["face.detect"] = _status("missing", "缺少人脸检测运行包 ultralytics")
    elif pt["status"] == "ready":
        result["face.detect"] = pt
    elif onnx["status"] == "ready" and _package("onnxruntime"):
        result["face.detect"] = _onnx_detection()
    else:
        result["face.detect"] = _status("missing", "缺少可用的人脸检测权重（PT 或 ONNX）")
    segment = _file(face_dir / "ForbiddenVision_face_segment_v1.safetensors", "人脸分割")
    result["face.segment"] = segment if _package("segmentation_models_pytorch") else _status("missing", "缺少人脸分割运行包 segmentation_models_pytorch")

    torch = sys.modules.get("torch")
    management = sys.modules.get("comfy.model_management")
    device_index = 0
    gpu = ""
    try:
        device = management.get_torch_device() if management else None
        if torch and torch.cuda.is_available() and (device is None or device.type == "cuda"):
            device_index = device.index if device is not None and device.index is not None else torch.cuda.current_device()
            gpu = torch.cuda.get_device_name(device_index)
    except Exception:
        pass
    match = re.search(r"\bRTX\s*([345])\d{2,3}\b", gpu, re.I)
    generation = "rtx" + match.group(1) + "0" if match else ""
    dlss_root = _node_root(nodes, "DLSS5NeuralRender")
    context = {"device": device_index, "gpu": gpu, "generation": generation, "dlss_root": str(dlss_root or "")}
    if sys.platform != "win32" or not generation:
        value = _status("unsupported", "当前 NVIDIA 插件运行库需要受支持的 Windows RTX 30/40/50 设备", gpu=gpu)
        result.update({"nvidia.vsr": dict(value), "nvidia.dlss": dict(value)})
        return result, context
    try:
        ctypes.WinDLL("vcruntime140.dll")
        ctypes.WinDLL("msvcp140.dll")
        vc_ready = True
    except OSError:
        vc_ready = False
    result["nvidia.vsr"] = _status("unknown", "VSR 运行库尚未执行验证") if _package("nvvfx") and vc_ready else _status("missing", "缺少 NVIDIA VFX 或 Visual C++ 运行库")
    dlls = [dlss_root / "vendor/dlssnr_host.dll", dlss_root / "vendor" / generation / "nvngx_dlssnr.dll"] if dlss_root else []
    result["nvidia.dlss"] = _status("unknown", "DLSS 运行库尚未执行验证") if vc_ready and len(dlls) == 2 and all(path.is_file() for path in dlls) else _status("missing", "缺少当前显卡对应的 DLSS DLL 或 Visual C++ 运行库")
    identities = [file_fingerprint(path) for path in dlls if path.is_file()]
    try:
        vfx = importlib.metadata.version("nvidia-vfx")
    except importlib.metadata.PackageNotFoundError:
        vfx = ""
    worker = Path(__file__).with_name("native_probe.py")
    try:
        python_identity = file_fingerprint(sys.executable)
        worker_identity = hashlib.sha256(worker.read_bytes()).hexdigest()
    except OSError:
        python_identity, worker_identity = "unavailable", "unavailable"
    cuda_version = getattr(getattr(torch, "version", None), "cuda", "")
    context["identity"] = (sys.executable, python_identity, worker_identity, gpu, device_index, generation,
                           _driver_version(), cuda_version, getattr(torch, "__version__", ""), vfx, *identities)
    for module, key in (("nvidia", "nvidia.vsr"), ("dlss", "nvidia.dlss")):
        cached = _cached_native((module, *context["identity"]))
        if result[key]["status"] == "unknown" and cached:
            result[key] = cached
    return result, context


async def probe_native(modules: list[str], capabilities: dict[str, Any], context: dict[str, Any]) -> None:
    global _NATIVE_LOCK
    if _NATIVE_LOCK is None:
        _NATIVE_LOCK = asyncio.Lock()
    async with _NATIVE_LOCK:
        for module in modules:
            key = "nvidia.vsr" if module == "nvidia" else "nvidia.dlss"
            if capabilities.get(key, {}).get("status") != "unknown" or "identity" not in context:
                continue
            identity = (module, *context["identity"])
            cached = _cached_native(identity)
            if cached:
                capabilities[key] = cached
                continue
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-B", str(Path(__file__).with_name("native_probe.py")), module,
                str(context["device"]), context["dlss_root"], context["generation"],
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                **({"creationflags": 0x08000000} if sys.platform == "win32" else {}),
            )
            try:
                output, _ = await asyncio.wait_for(process.communicate(), 15)
                rows = output.decode("utf-8", errors="replace").splitlines()
                value = next((json.loads(line) for line in reversed(rows) if line.startswith("{")), None)
                if not isinstance(value, dict) or value.get("status") not in {"ready", "missing", "unsupported", "unknown"}:
                    raise ValueError("验证进程未返回有效结果")
            except asyncio.CancelledError:
                process.kill()
                await process.communicate()
                raise
            except (asyncio.TimeoutError, ValueError) as error:
                if process.returncode is None:
                    process.kill()
                    await process.communicate()
                value = _status("unknown", "原生运行库验证未完成：" + (str(error) or "检查超时"))
            capabilities[key] = value
            _NATIVE_CACHE[identity] = (time.monotonic(), dict(value))
