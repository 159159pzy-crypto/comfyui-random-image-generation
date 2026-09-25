"""Read-only ComfyUI path inventory. Does not import custom nodes or torch."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys


def probe(root):
    root = Path(root).resolve()
    os.chdir(root)
    sys.path.insert(0, str(root))
    sys.argv = [str(root / "main.py")]
    with contextlib.redirect_stdout(io.StringIO()):
        import folder_paths
        from utils.extra_config import load_extra_path_config

        extra = root / "extra_model_paths.yaml"
        if extra.is_file():
            load_extra_path_config(str(extra))
        paths = {key: list(value[0]) for key, value in folder_paths.folder_names_and_paths.items()}
    paths.setdefault("sams", [str(root / "models" / "sams")])
    paths.setdefault("ultralytics", [str(root / "models" / "ultralytics")])
    # Core lists legacy `unet` first for searching; new files use the documented
    # canonical directory unless extra_model_paths explicitly moved a path first.
    for key, legacy, canonical in [("diffusion_models", "unet", "diffusion_models"), ("text_encoders", "clip", "text_encoders")]:
        defaults = {str(root / "models" / legacy), str(root / "models" / canonical)}
        if paths.get(key) and paths[key][0] in defaults:
            preferred = str(root / "models" / canonical)
            paths[key] = [preferred] + [p for p in paths[key] if p != preferred]
    # Impact Subpack also recognizes these extra_model_paths keys.
    for key, suffix in [("ultralytics_bbox", "bbox"), ("ultralytics_segm", "segm")]:
        paths.setdefault(key, [str(Path(p) / suffix) for p in paths["ultralytics"]])
    return {"root": str(root), "python": sys.executable, "paths": paths}


if __name__ == "__main__":
    result = probe(sys.argv[1])
    print(json.dumps(result, ensure_ascii=True))
