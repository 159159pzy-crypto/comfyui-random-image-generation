from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOTS = ("anima_studio", "anima_webui", "anima_natural")
FRONTEND_ROOT = "static"

# These paths are evidence or migration boundaries, never active V7 runtime.
EXCLUDED_RUNTIME_FILES = frozenset(
    {
        "anima_webui/migrations.py",
    }
)
EXCLUDED_RUNTIME_PARTS = frozenset({"upstream", "__pycache__"})
EXCLUDED_FRONTEND_PARTS = frozenset({"vendor", "__pycache__"})

RUNTIME_FORBIDDEN = (
    ("upstream_import", re.compile(r"anima_natural\.upstream")),
    ("relative_upstream_import", re.compile(r"(?:from|import)\s+\.upstream")),
    (
        "transport_compatibility",
        re.compile(r"\b(?:ProviderContext|BrowserEvent|PluginSettings)\b"),
    ),
    ("astrbot_runtime", re.compile(r"astrbot", re.IGNORECASE)),
)
FRONTEND_FORBIDDEN = (
    ("legacy_natural_api", re.compile(r"/api/natural(?:/|\b)")),
    ("legacy_batches_api", re.compile(r"/api/batches(?:/|\b)")),
    ("legacy_v6_api", re.compile(r"/api/v6(?:/|\b)")),
)


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def runtime_files(root: Path = ROOT) -> list[Path]:
    files: list[Path] = []
    for relative_root in RUNTIME_ROOTS:
        base = root / relative_root
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            relative = path.relative_to(root)
            if (
                relative.as_posix() in EXCLUDED_RUNTIME_FILES
                or EXCLUDED_RUNTIME_PARTS.intersection(relative.parts)
            ):
                continue
            files.append(path)
    return sorted(files)


def frontend_files(root: Path = ROOT) -> list[Path]:
    base = root / FRONTEND_ROOT
    if not base.is_dir():
        return []
    return sorted(
        path
        for path in base.rglob("*")
        if path.is_file()
        and path.suffix.casefold() in {".js", ".mjs", ".html"}
        and not EXCLUDED_FRONTEND_PARTS.intersection(path.relative_to(root).parts)
    )


def scan_files(
    files: Iterable[Path],
    patterns: tuple[tuple[str, re.Pattern[str]], ...],
    *,
    root: Path = ROOT,
) -> list[str]:
    failures: list[str] = []
    for path in files:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for code, pattern in patterns:
                if pattern.search(line):
                    failures.append(
                        f"{_relative(path, root)}:{line_number}: {code}: {pattern.pattern}"
                    )
    return failures


def violations(root: Path = ROOT) -> list[str]:
    return [
        *scan_files(runtime_files(root), RUNTIME_FORBIDDEN, root=root),
        *scan_files(frontend_files(root), FRONTEND_FORBIDDEN, root=root),
    ]


def main() -> int:
    failures = violations(ROOT)
    if failures:
        print("V7 native boundary violations:")
        print("\n".join(failures))
        return 1
    print(
        "V7 native boundary passed "
        f"({len(runtime_files(ROOT))} Python files, "
        f"{len(frontend_files(ROOT))} frontend files scanned)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
