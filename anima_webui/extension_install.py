"""Install the shared ComfyUI extension without owning the ComfyUI environment."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import stat
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import urlopen
import uuid

DIRECTORY = "ComfyUI-Atelier-Resources"
RECEIPT = ".atelier-managed.json"
SOURCE = Path(__file__).parent / "comfy_extension" / "atelier_resources"
REQUIRED_FILES = frozenset({
    "__init__.py", "capabilities.py", "inspector.py", "memory_cleanup.py", "native_probe.py",
})


def is_link(path: Path) -> bool:
    value = path.lstat()
    return stat.S_ISLNK(value.st_mode) or bool(
        getattr(value, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def manifest(root: Path) -> dict[str, str]:
    if is_link(root):
        raise ValueError("扩展目录是链接，请在原环境管理它。")
    result = {}
    for entry in root.iterdir():
        if entry.name == "__pycache__" or entry.name == RECEIPT or entry.suffix == ".pyc":
            continue
        if is_link(entry):
            raise ValueError("扩展包含链接，原文件保持不变。")
        if entry.is_dir():
            result.update({entry.name + "/" + key: value for key, value in manifest(entry).items()})
        elif entry.is_file():
            result[entry.name] = hashlib.sha256(entry.read_bytes()).hexdigest().upper()
        else:
            raise ValueError("扩展包含非普通文件。")
    return result


def inspect(comfy_root: Path, source: Path = SOURCE) -> dict:
    root = comfy_root.resolve(strict=True)
    if not (root / "main.py").is_file():
        raise ValueError("请选择包含 main.py 的 ComfyUI 根目录。")
    expected = manifest(source)
    missing = REQUIRED_FILES - expected.keys()
    if missing:
        raise ValueError("源码包缺少配套扩展文件：" + ", ".join(sorted(missing)))
    custom = root / "custom_nodes"
    if custom.exists() and is_link(custom):
        raise ValueError("custom_nodes 是链接，请在实际 ComfyUI 环境中管理扩展。")
    target = custom / DIRECTORY
    status = "missing"
    if target.exists() or target.is_symlink():
        actual = manifest(target)
        if actual == expected:
            status = "ready"
        else:
            try:
                receipt_path = target / RECEIPT
                if is_link(receipt_path):
                    raise ValueError("安装记录不能是链接。")
                receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                receipt = {}
            if not isinstance(receipt, dict) or receipt.get("version") != 1 or receipt.get("files") != actual:
                raise ValueError("已有扩展未受管或已被修改，已保留原文件；请在原环境处理。")
            status = "outdated"
    return {"status": status, "target": str(target), "files": expected}


def install(comfy_root: Path, source: Path = SOURCE, *, running: bool = False) -> dict:
    result = inspect(comfy_root, source)
    if result["status"] == "ready":
        return {**result, "changed": False}
    if running:
        raise ValueError("请先正常停止 ComfyUI，再安装配套扩展；脚本不会停止或重启服务。")
    root = comfy_root.resolve(strict=True)
    target = Path(result["target"])
    custom = target.parent
    custom.mkdir(exist_ok=True)
    staging = custom / (".atelier-resources-" + uuid.uuid4().hex + ".disabled")
    staging.mkdir()
    backup = None
    try:
        for relative in result["files"]:
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, destination)
        if manifest(staging) != result["files"]:
            raise OSError("暂存文件校验失败，原安装保持不变。")
        (staging / RECEIPT).write_text(json.dumps({"version": 1, "files": result["files"]}, indent=2), encoding="utf-8")
        # Revalidate ownership immediately before replacing an existing tree.
        current = inspect(root, source)
        if current["status"] != result["status"] or current["files"] != result["files"]:
            raise OSError("安装期间扩展发生变化，请重新检查。")
        if target.exists():
            backups = root / ".anima-extension-backups"
            if backups.exists() and is_link(backups):
                raise ValueError("扩展备份目录是链接，安装已取消。")
            backups.mkdir(exist_ok=True)
            backup = backups / (DIRECTORY + "-" + uuid.uuid4().hex)
            target.rename(backup)
        try:
            staging.rename(target)
        except OSError:
            if backup is not None and not target.exists():
                try:
                    backup.rename(target)
                except OSError as error:
                    raise OSError(f"扩展更新失败且无法自动恢复；原扩展已保留在 {backup}，请恢复后重试。") from error
            raise
    finally:
        # Only remove this invocation's verified staging directory.
        if staging.exists() and staging.resolve().parent == custom.resolve() and not is_link(staging):
            shutil.rmtree(staging)
    return {**result, "status": "ready", "changed": True, "backup": str(backup) if backup else None}


def running_at(url: str) -> bool:
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise ValueError("ComfyUI 地址必须是本机回环 HTTP 地址。")
    try:
        with urlopen(url.rstrip("/") + "/system_stats", timeout=3):
            return True
    except HTTPError:
        # A responding HTTP service, even one returning an error, is not stopped.
        return True
    except URLError as error:
        if isinstance(error.reason, ConnectionRefusedError):
            return False
        raise OSError("无法确认 ComfyUI 是否已停止，未安装扩展；请检查地址和服务状态。") from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comfy-root", required=True, type=Path)
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    parser.add_argument("--check", action="store_true", help="仅检查文件，不安装或修改")
    args = parser.parse_args()
    try:
        result = inspect(args.comfy_root) if args.check else install(args.comfy_root, running=running_at(args.comfy_url))
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "files"}, ensure_ascii=False, indent=2))
    if result.get("changed"):
        print("配套扩展已安装。请自行启动 ComfyUI；不需要重新安装或升级 ComfyUI。")
    if args.check and result["status"] != "ready":
        parser.exit(1, "配套扩展需要安装或更新。\n")


if __name__ == "__main__":
    main()
