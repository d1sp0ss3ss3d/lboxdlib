from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = ROOT / "tools" / "generate_api_docs.py"

spec = importlib.util.spec_from_file_location("generate_api_docs_for_test", GENERATOR_PATH)
assert spec is not None and spec.loader is not None
generate_api_docs = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = generate_api_docs
spec.loader.exec_module(generate_api_docs)

ModuleDoc = generate_api_docs.ModuleDoc
render_api_page = generate_api_docs.render_api_page


def test_root_api_docs_list_lazy_exports_explicitly():
    init_file = ROOT / "src" / "lboxdlib" / "__init__.py"
    module = ModuleDoc(
        module_name="lboxdlib",
        source_path=init_file,
        output_path=ROOT / "docs" / "api" / "lboxdlib.md",
        title="lboxdlib",
        is_package=True,
    )

    rendered = render_api_page(module, show_source=False, package_name="lboxdlib")

    assert "Package-root public exports" in rendered
    assert "- `LBoxdSession`" in rendered
    assert "- `FilmHydrator`" in rendered
    assert "- `ListManager`" in rendered
    assert "- `fetch_reviews`" in rendered
    assert "      members:" in rendered
    assert "        - LBoxdSession" in rendered
