from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "lboxdlib"


def read(rel: str) -> str:
    return (SRC / rel).read_text(encoding="utf-8")


def test_root_exports_base_library_objects():
    text = read("__init__.py")
    for name in [
        "LBoxdSession",
        "FilmHydrator",
        "ListManager",
        "hydrate_lids",
        "fetch_reviews",
        "fetch_users",
        "select_rated_viewings",
        "like_review",
        "unfollow_user",
        "batch_unfollow_users",
        "user_follows",
        "resolve_user_slug",
    ]:
        assert name in text, f"{name} missing from package root"


def test_lists_facade_exists_and_exposes_hydration_api():
    text = read("lists/__init__.py")
    for name in [
        "ListManager",
        "FilmHydrator",
        "HydrationResult",
        "hydrate_lids",
        "DEFAULT_GENERATOR_LIST_SLUG",
        "DEFAULT_MAX_BATCH_SIZE",
    ]:
        assert name in text, f"{name} missing from lists facade"


def test_relationships_facade_uses_user_follows_not_fetchers():
    text = read("relationships.py")
    for name in ["user_follows", "resolve_user_slug", "RelationshipCheckResult"]:
        assert name in text, f"{name} missing from relationships facade"
    assert "fetchers" not in text.lower()


def test_policy_automations_are_script_candidates():
    scripts = ROOT / "scripts"
    for rel in ["batch_like_reviews.py", "unfollow_nonfollowers.py", "scrape_films.py"]:
        assert (scripts / rel).exists(), rel
