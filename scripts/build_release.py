"""Build and audit a user source package from one immutable Git commit.

The repository keeps its tests and development tools. Git exports and user
downloads contain only the runtime, installation instructions and product images.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile
import tomllib
import zipfile

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = frozenset({
    "Install.ps1", "Start.ps1", "Start.cmd", "run.py", "pyproject.toml",
    "LICENSE", "README.md", "RELEASE_NOTES.md",
})
DOCUMENTS = frozenset({
    "docs/guide.md", "docs/dependencies.md",
    "docs/readme/random-workbench.png", "docs/readme/prompt-pool.png",
    "docs/readme/lora-manager.png",
})
TEMPLATES = frozenset({
    "templates/workflow_api_v2.json", "templates/workflow_ui_v2.json",
})
REQUIRED_FILES = ROOT_FILES | DOCUMENTS | TEMPLATES | {
    "anima_webui/__init__.py", "anima_webui/server.py",
    "anima_webui/workflow.py", "anima_webui/zh_names.json",
    "static/index.html", "static/app.js",
}
TEXT_SUFFIXES = {".py", ".js", ".css", ".html", ".svg", ".json", ".toml", ".md", ".ps1", ".cmd"}
MAX_UNPACKED_BYTES = 128 * 1024 * 1024
SENSITIVE_TEXT = re.compile(
    r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}\b"
    r"|\bgithub_pat_[A-Za-z0-9_]{30,}\b"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r"|\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b"
    r"|[A-Za-z]:[/\\]Users[/\\][^\s/\\<>]+"
    r"|/(?:Users|home)/[^\s/<>]+"
)


class ReleaseError(ValueError):
    """The source tree or archive is not suitable for a user download."""


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=False,
    )
    if result.returncode:
        raise ReleaseError(result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout


def allowed_path(name: str) -> bool:
    if name in ROOT_FILES | DOCUMENTS | TEMPLATES:
        return True
    path = PurePosixPath(name)
    if path.parent == PurePosixPath("anima_webui"):
        return path.suffix == ".py" or path.name == "zh_names.json"
    return path.parent == PurePosixPath("static") and path.suffix in {".js", ".css", ".html", ".svg"}


def source_manifest(repo: Path, ref: str) -> tuple[str, dict[str, str]]:
    commit = git(repo, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").decode().strip()
    files: dict[str, str] = {}
    for entry in git(repo, "ls-tree", "-rz", commit).split(b"\0"):
        if not entry:
            continue
        metadata, raw_name = entry.split(b"\t", 1)
        name = raw_name.decode("utf-8")
        if not allowed_path(name):
            continue
        mode, kind, object_id = metadata.decode().split()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ReleaseError(f"Non-regular runtime file: {name}")
        files[name] = object_id
    missing = REQUIRED_FILES - files.keys()
    if missing:
        raise ReleaseError(f"Required source files missing: {', '.join(sorted(missing))}")
    return commit, files


def safe_path(name: str) -> str:
    if not name or "\\" in name or "\0" in name or ":" in name:
        raise ReleaseError(f"Unsafe archive path: {name!r}")
    cleaned = name[:-1] if name.endswith("/") else name
    parts = cleaned.split("/")
    if not cleaned or any(part in {"", ".", ".."} for part in parts):
        raise ReleaseError(f"Unsafe archive path: {name!r}")
    return cleaned


def archive_files(data: bytes) -> dict[str, bytes]:
    entries: dict[str, bytes] = {}
    seen: set[str] = set()
    total = 0

    def record(name: str, size: int) -> str:
        nonlocal total
        name = safe_path(name)
        if name in seen:
            raise ReleaseError(f"Duplicate archive path: {name}")
        seen.add(name)
        total += size
        if total > MAX_UNPACKED_BYTES:
            raise ReleaseError("Archive exceeds the source package size limit")
        return name

    if zipfile.is_zipfile(BytesIO(data)):
        with zipfile.ZipFile(BytesIO(data)) as archive:
            for item in archive.infolist():
                # ZipInfo normalizes platform separators and truncates NULs;
                # audit the original name before accepting that normalization.
                name = record(item.orig_filename, item.file_size)
                file_type = stat.S_IFMT(item.external_attr >> 16)
                if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise ReleaseError(f"Archive link or special file: {name}")
                if not item.is_dir():
                    entries[name] = archive.read(item)
    else:
        try:
            with tarfile.open(fileobj=BytesIO(data), mode="r:*") as archive:
                for item in archive:
                    name = record(item.name, item.size)
                    if item.isdir():
                        continue
                    if not item.isfile():
                        raise ReleaseError(f"Archive link or special file: {name}")
                    stream = archive.extractfile(item)
                    if stream is None:
                        raise ReleaseError(f"Unreadable archive member: {name}")
                    entries[name] = stream.read()
        except tarfile.TarError as error:
            raise ReleaseError("Not a readable ZIP or tar archive") from error
    if not entries:
        raise ReleaseError("Empty source archive")
    parents = {str(parent) for name in entries for parent in PurePosixPath(name).parents if str(parent) != "."}
    unexpected_directories = seen - entries.keys() - parents
    if unexpected_directories:
        raise ReleaseError(f"Unexpected archive directories: {sorted(unexpected_directories)}")
    if not (entries.keys() & ROOT_FILES):
        roots = {name.split("/", 1)[0] for name in entries}
        if len(roots) != 1 or any("/" not in name for name in entries):
            raise ReleaseError("Archive must have one package root")
        entries = {name.split("/", 1)[1]: content for name, content in entries.items()}
    return entries


def inspect_content(name: str, data: bytes) -> None:
    if name.endswith(".png"):
        try:
            with Image.open(BytesIO(data)) as picture:
                if picture.format != "PNG":
                    raise ReleaseError(f"Product screenshot is not PNG: {name}")
                picture.verify()
            with Image.open(BytesIO(data)) as picture:
                picture.load()
                unexpected = picture.info.keys() - {"srgb", "gamma", "dpi"}
                if unexpected or picture.getexif():
                    raise ReleaseError(f"Unexpected image metadata: {name}")
        except (OSError, SyntaxError) as error:
            raise ReleaseError(f"Unreadable screenshot: {name}") from error
    elif PurePosixPath(name).suffix in TEXT_SUFFIXES or name == "LICENSE":
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ReleaseError(f"Non-UTF-8 source file: {name}") from error
        if SENSITIVE_TEXT.search(text):
            raise ReleaseError(f"Possible credential or personal home path: {name}")


def validate_bytes(data: bytes, repo: Path, ref: str) -> dict[str, object]:
    commit, manifest = source_manifest(repo, ref)
    files = archive_files(data)
    extra = files.keys() - manifest.keys()
    missing = manifest.keys() - files.keys()
    if extra or missing:
        raise ReleaseError(f"Archive manifest mismatch; extra={sorted(extra)}, missing={sorted(missing)}")
    for name, content in files.items():
        inspect_content(name, content)
        original = git(repo, "cat-file", "blob", manifest[name])
        is_text = PurePosixPath(name).suffix in TEXT_SUFFIXES or name == "LICENSE"
        if is_text:
            original = original.replace(b"\r\n", b"\n")
            content = content.replace(b"\r\n", b"\n")
        if original != content:
            raise ReleaseError(f"Archive content differs from source commit: {name}")
    html = files["static/index.html"].decode("utf-8-sig")
    references = re.findall(r'''(?:src|href)=["']/static/([^"'?#]+)''', html)
    for reference in references:
        if f"static/{reference}" not in files:
            raise ReleaseError(f"Missing HTML runtime asset: {reference}")
    readme = files["README.md"].decode("utf-8-sig")
    images = re.findall(r"!\[[^\]]*\]\(([^)\s]+)", readme)
    images += re.findall(r'''<img\b[^>]*\bsrc=["']([^"']+)''', readme)
    for reference in images:
        if not re.match(r"https?://", reference) and reference not in files:
            raise ReleaseError(f"Missing README image: {reference}")
    return {"commit": commit, "files": len(files), "sha256": sha256(data).hexdigest()}


def verify_archive(path: Path, repo: Path, ref: str) -> dict[str, object]:
    return validate_bytes(path.read_bytes(), repo, ref)


def build_release(repo: Path, ref: str, output_dir: Path) -> dict[str, object]:
    commit, manifest = source_manifest(repo, ref)
    metadata = tomllib.loads(git(repo, "cat-file", "blob", manifest["pyproject.toml"]).decode("utf-8"))
    version = metadata["project"]["version"]
    if metadata["project"]["name"] != "anima-random-webui" or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ReleaseError("Unexpected project name or release version")
    filename = f"AnimaRandomWebUI-{version}-source.zip"
    data = git(repo, "archive", "--format=zip", f"--prefix=AnimaRandomWebUI-{version}/", commit)
    report = validate_bytes(data, repo, commit)
    build_info = {"version": version, "commit": commit, "artifact": filename, "sha256": report["sha256"]}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / filename).write_bytes(data)
    (output_dir / "SHA256SUMS.txt").write_text(f"{report['sha256']}  {filename}\n", encoding="ascii")
    (output_dir / "build-info.json").write_text(json.dumps(build_info, indent=2) + "\n", encoding="utf-8")
    return {**report, "artifact": str(output_dir / filename), "version": version}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--ref", default="HEAD", help="Git commit, tag or branch to export and verify")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output" / "release")
    parser.add_argument("--verify", type=Path, nargs="+", help="Audit existing ZIP/tar.gz downloads instead of building")
    args = parser.parse_args()
    try:
        if args.verify:
            result = [{"archive": str(path), **verify_archive(path, args.repo, args.ref)} for path in args.verify]
        else:
            result = build_release(args.repo, args.ref, args.output_dir)
    except (ReleaseError, OSError, zipfile.BadZipFile) as error:
        parser.exit(1, f"Release validation failed: {error}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
