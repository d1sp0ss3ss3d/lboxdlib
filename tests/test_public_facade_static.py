from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "lboxdlib"


def read(rel: str) -> str:
    return (SRC / rel).read_text(encoding="utf-8")


def test_public_facade_modules_exist():
    for rel in [
        "auth.py",
        "fetch.py",
        "parse.py",
        "select.py",
        "actions/__init__.py",
        "lists/__init__.py",
        "relationships.py",
        "workflows/__init__.py",
        "models/__init__.py",
    ]:
        assert (SRC / rel).exists(), rel


def test_public_facades_expose_expected_names():
    expected = {
        "auth.py": ["LBoxdSession", "ensure_session", "ensure_raw_session", "get_signed_in_user", "get_csrf"],
        "fetch.py": ["fetch_reviews", "fetch_users", "fetch_review_and_diary_activity"],
        "parse.py": ["extract_activity", "DiaryActivity", "extract_reviews"],
        "select.py": ["select_rated_viewings", "select_rating_range"],
        "actions/__init__.py": ["like_review", "follow_user", "unfollow_user", "batch_unfollow_users"],
        "lists/__init__.py": ["ListManager", "FilmHydrator", "hydrate_lids"],
        "relationships.py": ["user_follows", "resolve_user_slug"],
        "workflows/__init__.py": ["batch_like_reviews", "unfollow_nonfollowers"],
        "models/__init__.py": ["User", "Review", "RatedViewing"],
    }
    for rel, names in expected.items():
        text = read(rel)
        for name in names:
            assert name in text, f"{name} missing from {rel}"
