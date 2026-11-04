from __future__ import annotations

from pathlib import Path
import tomllib

import lboxdlib

ROOT = Path(__file__).resolve().parents[1]


def test_package_version_is_1_0_1_everywhere():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["version"] == "1.0.1"
    assert lboxdlib.__version__ == "1.0.1"


def test_release_scripts_exist():
    assert (ROOT / "scripts/release_check.py").exists()
    assert (ROOT / "scripts/make_release_zip.py").exists()
    assert (ROOT / "docs/RELEASE_CHECKS.md").exists()
