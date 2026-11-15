"""Isolated, download-free smoke checks for each installed NVIDIA effect."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile


def run(module: str, device_index: int, dlss_root: str, generation: str) -> dict:
    import torch
    torch.cuda.set_device(device_index)
    if module == "nvidia":
        import nvvfx
        frame = torch.linspace(.05, .95, 3 * 256 * 256, device=f"cuda:{device_index}").reshape(3, 256, 256).contiguous()
        with nvvfx.VideoSuperRes(nvvfx.effects.QualityLevel.ULTRA) as effect:
            effect.output_width = 512
            effect.output_height = 512
            effect.load()
            output = torch.from_dlpack(effect.run(frame).image).clone()
            if tuple(output.shape) != (3, 512, 512) or not torch.isfinite(output).all().item():
                raise RuntimeError("NVIDIA VSR 未产生有效测试图像")
        return {"status": "ready", "verification": "execution", "message": "VSR 运行库已通过图像处理验证"}
    if module != "dlss":
        raise ValueError("未知原生能力")
    import numpy as np
    root = Path(dlss_root)
    spec = importlib.util.spec_from_file_location("atelier_dlss_probe", root / "dlss_engine.py")
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    # Import only the backend: the plugin's entry point can download assets.
    with tempfile.TemporaryDirectory(prefix="atelier-dlss-probe-") as temporary:
        engine.configure(str(root / "vendor/dlssnr_host.dll"), str(root / "vendor" / generation / "nvngx_dlssnr.dll"), str(Path(temporary) / "probe.log"))
        session = engine.Live(256, 256, {"preset": 1})
        try:
            rgba = np.full((256, 256, 4), 128, dtype=np.uint8)
            rgba[..., 3] = 255
            output = session.process(rgba, reset=True)
            if output is None or output.shape != rgba.shape:
                raise RuntimeError("DLSS 未产生有效测试图像")
        finally:
            session.close()
    return {"status": "ready", "verification": "execution", "message": "DLSS 运行库已通过图像处理验证"}


if __name__ == "__main__":
    try:
        result = run(sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4])
    except Exception as error:
        result = {"status": "missing", "verification": "execution", "message": "原生运行库验证失败：" + str(error)}
    print(json.dumps(result, ensure_ascii=False))
