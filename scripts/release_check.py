#!/usr/bin/env python3
"""Run the local release validation checks for lboxdlib.

The default check is intentionally offline except for whatever your local Python
packaging tools already have installed. It validates source syntax, the pytest
suite, generated API docs, MkDocs strict build, and wheel creation.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def project_env() -> dict[str, str]:
    env = os.environ.copy()
    src = str(ROOT / "src")
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = src if not existing else f"{src}{os.pathsep}{existing}"
    return env


def clean_macos_metadata(root: Path = ROOT) -> None:
    removed_files = 0
    removed_dirs = 0
    for path in root.rglob("._*"):
        if path.is_file() or path.is_symlink():
            path.unlink()
            removed_files += 1
    for path in sorted(root.rglob("__MACOSX"), reverse=True):
        if path.is_dir():
            shutil.rmtree(path)
            removed_dirs += 1
    print(f"removed AppleDouble files: {removed_files}")
    print(f"removed __MACOSX directories: {removed_dirs}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-docs", action="store_true", help="Skip API doc generation and mkdocs build.")
    parser.add_argument("--skip-wheel", action="store_true", help="Skip wheel build validation.")
    parser.add_argument("--keep-build-artifacts", action="store_true", help="Do not remove dist/build before wheel validation.")
    args = parser.parse_args(argv)

    clean_macos_metadata(ROOT)

    if not args.keep_build_artifacts:
        for name in ("dist", "build"):
            path = ROOT / name
            if path.exists():
                shutil.rmtree(path)
        for path in (ROOT / "src").glob("*.egg-info"):
            if path.is_dir():
                shutil.rmtree(path)

    run([sys.executable, "-m", "compileall", "-q", "src", "tests", "scripts", "tools"])
    run([sys.executable, "-m", "pytest", "-q"])

    if not args.skip_docs:
        run([
            sys.executable,
            "tools/generate_api_docs.py",
            "--src-dir",
            "src",
            "--package",
            "lboxdlib",
            "--write-mkdocs",
        ])
        run([sys.executable, "-m", "mkdocs", "build", "--strict"], env=project_env())

    if not args.skip_wheel:
        run([sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "-w", "dist"])

    print("release check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
