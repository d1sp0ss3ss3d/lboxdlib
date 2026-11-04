from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTIVITY_FETCHER = ROOT / "src" / "lboxdlib" / "modules" / "activity_page_handling.py"


def test_diary_review_activity_filter_preset_is_present():
    text = ACTIVITY_FETCHER.read_text(encoding="utf-8")
    assert "DIARY_AND_REVIEW_ACTIVITY_FILTERS" in text
    assert '("diaryEntries", "true")' in text
    assert '("reviews", "true")' in text
    assert '"diary_reviews"' in text
