#!/usr/bin/env python3
"""Create a clean source zip for sharing a lboxdlib project snapshot."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__MACOSX",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "site",
}
DEFAULT_EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
DEFAULT_EXCLUDED_NAMES = {".DS_Store"}


def project_version(root: Path = ROOT) -> str:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def should_exclude(path: Path, root: Path) -> bool:
    parts = set(path.relative_to(root).parts)
    if parts.intersection(DEFAULT_EXCLUDED_DIR_NAMES):
        return True
    if path.name in DEFAULT_EXCLUDED_NAMES:
        return True
    if path.name.startswith("._"):
        return True
    if path.suffix in DEFAULT_EXCLUDED_SUFFIXES:
        return True
    if path.name.endswith(".egg-info") or any(part.endswith(".egg-info") for part in path.relative_to(root).parts):
        return True
    return False


def clean_macos_metadata(root: Path = ROOT) -> None:
    for path in root.rglob("._*"):
        if path.is_file() or path.is_symlink():
            path.unlink()
    for path in sorted(root.rglob("__MACOSX"), reverse=True):
        if path.is_dir():
            shutil.rmtree(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None, help="Output zip path.")
    parser.add_argument("--root-name", default=None, help="Top-level directory name inside the zip.")
    parser.add_argument("--skip-check", action="store_true", help="Skip release_check.py before zipping.")
    args = parser.parse_args(argv)

    version = project_version(ROOT)
    root_name = args.root_name or f"lboxdlibv{version}"
    output = args.output or ROOT.parent / f"{root_name}.zip"

    if not args.skip_check:
        subprocess.run([sys.executable, "scripts/release_check.py"], cwd=ROOT, check=True)
    else:
        clean_macos_metadata(ROOT)

    if output.exists():
        output.unlink()
    output.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(ROOT.rglob("*")):
            if should_exclude(path, ROOT):
                continue
            if path == output or output in path.parents:
                continue
            if path.is_dir():
                continue
            arcname = Path(root_name) / path.relative_to(ROOT)
            zf.write(path, arcname.as_posix())

    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
