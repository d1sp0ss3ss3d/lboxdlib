from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "lboxdlib"


EXPECTED_WRAPPERS = [
    "models/typedefs.py",
    "parsers/activity.py",
    "parsers/reviews.py",
    "parsers/films.py",
    "parsers/users.py",
    "fetchers/activity.py",
    "fetchers/reviews.py",
    "fetchers/users.py",
    "actions/likes.py",
    "actions/follows.py",
    "selectors/rated_viewings.py",
    "workflows/batch_like_reviews.py",
    "workflows/reciprocal_follow_cleanup.py",
]


def test_public_restructure_wrapper_files_exist():
    missing = [path for path in EXPECTED_WRAPPERS if not (SRC / path).exists()]
    assert missing == []


def test_wrapper_modules_delegate_to_existing_implementations_for_step1():
    wrapper = (SRC / "selectors" / "rated_viewings.py").read_text(encoding="utf-8")
    assert "from lboxdlib.modules.rated_viewing_selector import *" in wrapper

    wrapper = (SRC / "parsers" / "activity.py").read_text(encoding="utf-8")
    assert "from lboxdlib.modules.activity_from_page import *" in wrapper

    wrapper = (SRC / "fetchers" / "reviews.py").read_text(encoding="utf-8")
    assert "from lboxdlib.core.fetchers import fetch_reviews" in wrapper
