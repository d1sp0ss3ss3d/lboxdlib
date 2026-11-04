from pathlib import Path
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_uses_setuptools_src_layout():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert data["build-system"]["build-backend"] == "setuptools.build_meta"
    assert "setuptools>=68" in data["build-system"]["requires"]
    assert data["tool"]["setuptools"]["package-dir"] == {"": "src"}
    assert data["tool"]["setuptools"]["packages"]["find"]["where"] == ["src"]


def test_pyproject_declares_runtime_and_dev_dependencies():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = set(data["project"]["dependencies"])

    assert "beautifulsoup4>=4.12" in deps
    assert "curl-cffi>=0.7" in deps
    assert "lxml>=5.0" in deps
    assert "dev" in data["project"]["optional-dependencies"]
