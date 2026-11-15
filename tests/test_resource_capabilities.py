import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from anima_webui.comfy_extension.atelier_resources import capabilities, inspector


def model_file(path, key="unknown"):
    raw = json.dumps({key: {"dtype": "F16", "shape": [1], "data_offsets": [0, 2]}}).encode()
    Path(path).write_bytes(len(raw).to_bytes(8, "little") + raw + b"\0\0")


def test_header_cache_uses_file_identity_and_returns_isolated_results(tmp_path, monkeypatch):
    path = tmp_path / "one.safetensors"
    model_file(path)
    folders = SimpleNamespace(get_filename_list=lambda kind: [path.name] if kind == "diffusion_models" else [],
                              get_full_path=lambda kind, name: str(path))
    calls = []
    original = inspector.read_header
    def counted(file):
        calls.append(file)
        return original(file)
    monkeypatch.setattr(inspector, "read_header", counted)
    inspector._FILE_CACHE.clear()
    first = inspector.inspect_inventory(folders)
    first[0]["roles"].append("wrong")
    assert inspector.inspect_inventory(folders)[0]["roles"] == []
    assert len(calls) == 1
    model_file(path, "blocks.31.mlp.all.proj_in_gate.weight")
    assert inspector.inspect_inventory(folders)[0]["roles"] == ["seedvr2.model"]
    assert len(calls) == 2
    path.unlink()
    assert inspector.inspect_inventory(folders)[0]["status"] == "invalid"


def test_lora_identity_uses_registered_files_without_reading_or_classifying_weights(tmp_path, monkeypatch):
    path = tmp_path / "lora.pt"
    path.write_bytes(b"not deserialized")
    categories = []
    def filenames(kind):
        categories.append(kind)
        return ["nested\\lora.pt"]
    folders = SimpleNamespace(get_filename_list=filenames, get_full_path=lambda kind, name: str(path))
    def no_header(*args):
        raise AssertionError("LoRA identity inspection must not classify weights")
    monkeypatch.setattr(inspector, "read_header", no_header)
    before = inspector.inspect_lora_files(folders)
    assert categories == ["loras"]
    assert before[0]["name"] == "nested/lora.pt"
    assert before[0]["status"] == "available" and "roles" not in before[0]
    path.write_bytes(b"replaced same named LoRA")
    assert inspector.inspect_lora_files(folders)[0]["fingerprint"] != before[0]["fingerprint"]
    path.unlink()
    assert inspector.inspect_lora_files(folders)[0]["status"] == "invalid"


def test_onnx_only_detection_accepts_the_selected_device_provider(monkeypatch):
    monkeypatch.setitem(sys.modules, "comfy.model_management", SimpleNamespace(get_torch_device=lambda: SimpleNamespace(type="cuda")))
    monkeypatch.setattr(capabilities.importlib, "import_module", lambda name: SimpleNamespace(get_available_providers=lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"]))
    assert capabilities._onnx_detection()["status"] == "ready"
    monkeypatch.setattr(capabilities.importlib, "import_module", lambda name: SimpleNamespace(get_available_providers=lambda: ["CPUExecutionProvider"]))
    assert capabilities._onnx_detection()["status"] == "missing"
    monkeypatch.setitem(sys.modules, "comfy.model_management", SimpleNamespace(get_torch_device=lambda: SimpleNamespace(type="cpu")))
    assert capabilities._onnx_detection()["status"] == "ready"


def test_cuda_driver_query_is_read_only_and_returns_version(monkeypatch):
    def version(pointer):
        pointer._obj.value = 13010
        return 0
    monkeypatch.setattr(capabilities.sys, "platform", "win32")
    monkeypatch.setattr(capabilities.ctypes, "WinDLL", lambda name: SimpleNamespace(cuDriverGetVersion=version), raising=False)
    assert capabilities._driver_version() == "13010"


def test_successful_native_probe_survives_long_jobs_but_runtime_changes_reprobe(monkeypatch):
    identity = ("python", "driver-1", "runtime-1", "gpu-1")
    monkeypatch.setattr(capabilities, "_NATIVE_CACHE", {
        ("nvidia", *identity): (0, {"status": "ready", "message": "validated"}),
    })
    monkeypatch.setattr(capabilities, "_NATIVE_LOCK", None)
    monkeypatch.setattr(capabilities.time, "monotonic", lambda: 86400)
    calls = []

    async def launch(*args, **kwargs):
        calls.append(args)
        async def communicate():
            return b'{"status":"ready","message":"new runtime validated"}', b""
        return SimpleNamespace(communicate=communicate, returncode=0)

    monkeypatch.setattr(capabilities.asyncio, "create_subprocess_exec", launch)

    async def check():
        context = {"identity": identity, "device": 0, "dlss_root": "", "generation": "rtx50"}
        value = {"nvidia.vsr": {"status": "unknown"}}
        await capabilities.probe_native(["nvidia"], value, context)
        assert value["nvidia.vsr"]["message"] == "validated"
        assert calls == []
        # An identity change requires execution even when a prior GPU succeeded.
        context["identity"] = ("python", "driver-2", "runtime-1", "gpu-1")
        value = {"nvidia.vsr": {"status": "unknown"}}
        await capabilities.probe_native(["nvidia"], value, context)
        assert value["nvidia.vsr"]["message"] == "new runtime validated"
        assert len(calls) == 1

    asyncio.run(check())


def test_unsuccessful_native_probes_expire_for_retry(monkeypatch):
    monkeypatch.setattr(capabilities, "_NATIVE_CACHE", {
        ("nvidia", "runtime"): (10, {"status": "missing", "message": "retry later"}),
    })
    monkeypatch.setattr(capabilities.time, "monotonic", lambda: 20)
    cached = capabilities._cached_native(("nvidia", "runtime"))
    assert cached["status"] == "missing"
    cached["status"] = "ready"
    monkeypatch.setattr(capabilities.time, "monotonic", lambda: 80)
    assert capabilities._cached_native(("nvidia", "runtime")) is None
