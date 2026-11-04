from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from lboxdlib.parsers.activity import (
    DiaryActivity,
    ReviewActivity,
    ReviewReferenceActivity,
    extract_activity,
)
from lboxdlib.modules import rated_viewing_selector as selector_impl
from lboxdlib.select import select_rated_viewings

FIXTURES = Path(__file__).parent / "fixtures"


def _activity_items(name: str):
    html = (FIXTURES / name).read_text(encoding="utf-8")
    return extract_activity(html, hydrate_likes=False)


def test_embedded_activity_review_fixture_parses_rating_and_review_identity():
    items = _activity_items("activity_review_entry.html")
    reviews = [item for item in items if isinstance(item, ReviewActivity)]

    assert len(reviews) == 1
    review = reviews[0]
    assert review.ownerSlug == "friend"
    assert review.film is not None
    assert review.film.slug == "great-film"
    assert review.film.name == "Great Film"
    assert review.film.year == 2024
    assert review.rating == 4.0
    assert review.ratingText == "★★★★"
    assert review.viewingUid == "viewing:123456789"
    assert review.viewingId == 123456789
    assert review.reviewLid == "review-lid-1"
    assert review.likesCount == 7
    assert review.reviewText == "A strong recent favorite."


def test_compact_liked_review_reference_fixture_is_not_a_review_or_diary():
    items = _activity_items("activity_liked_review_reference.html")

    assert len(items) == 1
    item = items[0]
    assert isinstance(item, ReviewReferenceActivity)
    assert not isinstance(item, ReviewActivity)
    assert not isinstance(item, DiaryActivity)
    assert item.rating == 4.0


def test_select_rated_viewings_filters_range_and_ignores_reference_rows(monkeypatch):
    review = _activity_items("activity_review_entry.html")[0]
    diary = _activity_items("activity_diary_entry.html")[0]
    reference = _activity_items("activity_liked_review_reference.html")[0]

    def fake_fetch_raw_items(*args, **kwargs):
        return [review, diary, reference]

    monkeypatch.setattr(selector_impl, "_fetch_raw_items", fake_fetch_raw_items)

    result = select_rated_viewings(
        SimpleNamespace(),
        url="https://letterboxd.com/activity/",
        min_rating=4.0,
        max_rating=5.0,
    )

    assert result.total_seen == 2
    assert result.total_matched == 2
    assert {item.raw_type for item in result.items} == {"ReviewActivity", "DiaryActivity"}
    assert {item.film_slug for item in result.items} == {"great-film", "the-ugly-stepsister"}


def test_select_rated_viewings_supports_review_or_diary_only(monkeypatch):
    review = _activity_items("activity_review_entry.html")[0]
    diary = _activity_items("activity_diary_entry.html")[0]

    monkeypatch.setattr(selector_impl, "_fetch_raw_items", lambda *args, **kwargs: [review, diary])

    reviews_only = select_rated_viewings(
        SimpleNamespace(),
        url="https://letterboxd.com/activity/",
        min_rating=4.0,
        include_diary_entries=False,
    )
    diaries_only = select_rated_viewings(
        SimpleNamespace(),
        url="https://letterboxd.com/activity/",
        min_rating=4.0,
        include_reviews=False,
    )

    assert [item.raw_type for item in reviews_only.items] == ["ReviewActivity"]
    assert [item.raw_type for item in diaries_only.items] == ["DiaryActivity"]
