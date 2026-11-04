#!/usr/bin/env python3
"""Remove macOS AppleDouble/resource-fork metadata files from the repo tree."""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

removed_files = 0
removed_dirs = 0

for path in ROOT.rglob("._*"):
    if path.is_file() or path.is_symlink():
        path.unlink()
        removed_files += 1

for path in sorted(ROOT.rglob("__MACOSX"), reverse=True):
    if path.is_dir():
        shutil.rmtree(path)
        removed_dirs += 1

print(f"removed AppleDouble files: {removed_files}")
print(f"removed __MACOSX directories: {removed_dirs}")
