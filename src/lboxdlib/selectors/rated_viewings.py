"""
Select Letterboxd rated viewing rows by star rating.

This module is intentionally read-only. It does not like, follow, unfollow,
or modify Letterboxd state.

It normalizes two existing project extraction paths:

- Activity pages:
    lboxdlib.fetchers.activity.fetch_activities()
    lboxdlib.parsers.activity.ReviewActivity
    lboxdlib.parsers.activity.DiaryActivity
    default activity_filter_preset="diary_reviews" so the fetch request only
    asks for diaryEntries=true and reviews=true

- Normal review pages:
    lboxdlib.fetchers.fetch_reviews()
    lboxdlib.models.typedefs.ReviewSummary via lboxdlib.parsers.reviews.extract_reviews()

The result is a list of RatedViewingSelection objects with consistent fields.

Primary public function: select_rated_viewings().
Legacy alias: select_rated_reviews().
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Literal, Optional
from urllib.parse import urljoin, urlparse

LETTERBOXD_BASE = "https://letterboxd.com"
DEFAULT_ACTIVITY_URL = f"{LETTERBOXD_BASE}/activity/"

SourceKind = Literal["activity", "reviews"]


@dataclass(slots=True)
class RatingFilter:
    """Inclusive rating filter for Letterboxd star ratings."""

    rating: float | None = None
    ratings: set[float] | None = None
    min_rating: float | None = None
    max_rating: float | None = None
    include_unrated: bool = False

    def matches(self, value: float | None) -> bool:
        if value is None:
            return self.include_unrated

        value = float(value)

        if self.rating is not None and value != float(self.rating):
            return False

        if self.ratings is not None and value not in self.ratings:
            return False

        if self.min_rating is not None and value < float(self.min_rating):
            return False

        if self.max_rating is not None and value > float(self.max_rating):
            return False

        return True


@dataclass(slots=True)
class RatedReviewSelection:
    """Normalized selected review/diary viewing row."""

    source: SourceKind
    source_url: str

    owner: str | None = None
    owner_display_name: str | None = None

    film_slug: str | None = None
    film_name: str | None = None
    film_year: int | None = None
    film_lid: str | None = None
    film_uid: str | None = None

    rating: float | None = None
    rating_text: str | None = None

    review_url: str | None = None
    diary_url: str | None = None
    reviews_url: str | None = None
    viewing_uid: str | None = None
    viewing_id: int | None = None
    review_lid: str | None = None

    date: str | None = None
    activity_id: str | None = None
    viewing_action: str | None = None

    review_text: str | None = None
    full_text_url: str | None = None

    liked_film: bool | None = None
    likes_count: int | None = None
    likes_url: str | None = None
    comments_count: int | None = None
    comments_url: str | None = None

    raw_type: str | None = None

    @property
    def absolute_review_url(self) -> str | None:
        return _absolute_url(self.review_url) if self.review_url else None

    @property
    def absolute_diary_url(self) -> str | None:
        return _absolute_url(self.diary_url) if self.diary_url else None

    @property
    def best_url(self) -> str | None:
        return self.absolute_review_url or self.absolute_diary_url

    @property
    def has_written_review(self) -> bool:
        return bool((self.review_text or "").strip())

    @property
    def is_diary_like_entry(self) -> bool:
        """Best-effort flag: a rated viewing with no parsed written review text."""
        return not self.has_written_review

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RatingSelectionResult:
    url: str
    source: SourceKind
    start_page: int
    stop_page: int | None
    filter: RatingFilter
    total_seen: int = 0
    total_matched: int = 0
    items: list[RatedReviewSelection] = field(default_factory=list)

    def sorted_by_rating(self, *, reverse: bool = True) -> list[RatedReviewSelection]:
        return sorted(
            self.items,
            key=lambda item: (-1 if item.rating is None else item.rating),
            reverse=reverse,
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # sets are not JSON-serializable by default
        if data.get("filter", {}).get("ratings") is not None:
            data["filter"]["ratings"] = sorted(data["filter"]["ratings"])
        return data

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def write_csv(self, path: str) -> None:
        fieldnames = [
            "source",
            "owner",
            "owner_display_name",
            "film_name",
            "film_year",
            "film_slug",
            "rating",
            "rating_text",
            "viewing_action",
            "date",
            "review_url",
            "diary_url",
            "likes_count",
            "comments_count",
            "has_written_review",
            "is_diary_like_entry",
        ]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for item in self.items:
                row = item.to_dict()
                row["has_written_review"] = item.has_written_review
                row["is_diary_like_entry"] = item.is_diary_like_entry
                writer.writerow({key: row.get(key) for key in fieldnames})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def select_rated_viewings(
    session: Any,
    *,
    url: str = DEFAULT_ACTIVITY_URL,
    start_page: int = 1,
    stop_page: int | None = None,
    min_rating: float | str | None = None,
    max_rating: float | str | None = None,
    rating: float | str | None = None,
    ratings: Iterable[float | str] | None = None,
    include_unrated: bool = False,
    include_reviews: bool = True,
    include_diary_entries: bool = True,
    activity_filter_preset: str = "diary_reviews",
    activity_filters: Any | None = None,
    verbose: bool = False,
) -> RatingSelectionResult:
    """Fetch review and diary/rated-viewing rows and return those in a rating range.

    This is the primary function for the discovery use case. It handles both:

    - activity pages such as ``https://letterboxd.com/activity/``; and
    - non-activity review pages such as ``/{user}/reviews/`` or
      ``/{user}/film/{film}/reviews/``.

    Activity pages are fetched with the ``diary_reviews`` filter preset by
    default, which asks Letterboxd only for ``diaryEntries=true`` and
    ``reviews=true``. Compact social references such as "Alice liked Bob's
    ★★★★ review of Film" are not returned by this selector because they parse
    as ReviewReferenceActivity, not ReviewActivity or DiaryActivity.

    Rating bounds are inclusive. With no rating arguments, all rated rows are
    returned.
    """
    result = select_rated_reviews(
        session,
        url=url,
        start_page=start_page,
        stop_page=stop_page,
        rating=rating,
        ratings=ratings,
        min_rating=min_rating,
        max_rating=max_rating,
        include_unrated=include_unrated,
        activity_filter_preset=activity_filter_preset,
        activity_filters=activity_filters,
        verbose=verbose,
    )

    if include_reviews and include_diary_entries:
        return result

    filtered = []
    for item in result.items:
        raw_type = (item.raw_type or "").lower()
        is_diary = raw_type == "diaryactivity"
        is_review = raw_type == "reviewactivity" or item.source == "reviews"
        if is_diary and include_diary_entries:
            filtered.append(item)
        elif is_review and include_reviews:
            filtered.append(item)

    result.items = filtered
    result.total_matched = len(filtered)
    return result


def select_rated_reviews(
    session: Any,
    *,
    url: str = DEFAULT_ACTIVITY_URL,
    start_page: int = 1,
    stop_page: int | None = None,
    rating: float | str | None = None,
    ratings: Iterable[float | str] | None = None,
    min_rating: float | str | None = None,
    max_rating: float | str | None = None,
    include_unrated: bool = False,
    activity_filter_preset: str = "diary_reviews",
    activity_filters: Any | None = None,
    verbose: bool = False,
) -> RatingSelectionResult:
    """Fetch review-like rows from an activity or review URL and filter by rating.

    Args:
        session:
            Signed-in curl_cffi/requests-like Letterboxd session.
        url:
            Activity URL or normal review URL. Defaults to /activity/.
        start_page:
            1-based page to start fetching.
        stop_page:
            Inclusive final page. None means continue until the underlying
            fetcher exhausts pages.
        rating:
            Exact rating to match, e.g. 5, 4.5, "★★★★½".
        ratings:
            Set/list of exact ratings to match, e.g. [4, 4.5, 5].
        min_rating:
            Inclusive lower bound, e.g. 4.
        max_rating:
            Inclusive upper bound, e.g. 5.
        include_unrated:
            If True, include unrated rows when they otherwise pass filters.
            Usually leave False for star-rating discovery.
        activity_filter_preset:
            Activity-only. Defaults to "diary_reviews", which requests only
            diaryEntries=true and reviews=true from the activity AJAX feed.
            This avoids compact reference rows such as reviewLikes and
            reviewComments at the fetch layer.
        activity_filters:
            Activity-only explicit filter override, passed through to
            lboxdlib.fetchers.activity.fetch_activities().

    With no rating/range arguments, this returns all rated rows.
    """

    _validate_pages(start_page=start_page, stop_page=stop_page)

    normalized_url = _normalize_url(url)
    source = detect_source_kind(normalized_url)
    rating_filter = RatingFilter(
        rating=_coerce_rating(rating) if rating is not None else None,
        ratings={_coerce_rating(r) for r in ratings} if ratings is not None else None,
        min_rating=_coerce_rating(min_rating) if min_rating is not None else None,
        max_rating=_coerce_rating(max_rating) if max_rating is not None else None,
        include_unrated=include_unrated,
    )

    result = RatingSelectionResult(
        url=normalized_url,
        source=source,
        start_page=start_page,
        stop_page=stop_page,
        filter=rating_filter,
    )

    raw_items = _fetch_raw_items(
        session,
        normalized_url,
        source=source,
        start_page=start_page,
        stop_page=stop_page,
        verbose=verbose,
        activity_filter_preset=activity_filter_preset,
        activity_filters=activity_filters,
    )

    for raw in raw_items:
        selected = _selection_from_raw(raw, source=source, source_url=normalized_url)
        if selected is None:
            continue

        result.total_seen += 1

        if rating_filter.matches(selected.rating):
            result.items.append(selected)
            result.total_matched += 1

    return result


def select_star_range_reviews(
    session: Any,
    *,
    url: str = DEFAULT_ACTIVITY_URL,
    min_rating: float | str,
    max_rating: float | str = 5.0,
    start_page: int = 1,
    stop_page: int | None = None,
    activity_filter_preset: str = "diary_reviews",
    activity_filters: Any | None = None,
    verbose: bool = False,
) -> RatingSelectionResult:
    """Convenience wrapper for an inclusive rating range."""
    return select_rated_reviews(
        session,
        url=url,
        start_page=start_page,
        stop_page=stop_page,
        min_rating=min_rating,
        max_rating=max_rating,
        activity_filter_preset=activity_filter_preset,
        activity_filters=activity_filters,
        verbose=verbose,
    )


def select_exact_star_reviews(
    session: Any,
    *,
    url: str = DEFAULT_ACTIVITY_URL,
    rating: float | str,
    start_page: int = 1,
    stop_page: int | None = None,
    activity_filter_preset: str = "diary_reviews",
    activity_filters: Any | None = None,
    verbose: bool = False,
) -> RatingSelectionResult:
    """Convenience wrapper for one exact star rating."""
    return select_rated_reviews(
        session,
        url=url,
        start_page=start_page,
        stop_page=stop_page,
        rating=rating,
        activity_filter_preset=activity_filter_preset,
        activity_filters=activity_filters,
        verbose=verbose,
    )


def select_rating_range(
    session: Any,
    *,
    url: str = DEFAULT_ACTIVITY_URL,
    min_rating: float | str,
    max_rating: float | str = 5.0,
    start_page: int = 1,
    stop_page: int | None = None,
    include_reviews: bool = True,
    include_diary_entries: bool = True,
    activity_filter_preset: str = "diary_reviews",
    activity_filters: Any | None = None,
    verbose: bool = False,
) -> RatingSelectionResult:
    """Convenience name for inclusive rating-range discovery."""
    return select_rated_viewings(
        session,
        url=url,
        min_rating=min_rating,
        max_rating=max_rating,
        start_page=start_page,
        stop_page=stop_page,
        include_reviews=include_reviews,
        include_diary_entries=include_diary_entries,
        activity_filter_preset=activity_filter_preset,
        activity_filters=activity_filters,
        verbose=verbose,
    )


def find_recent_highly_rated_by_friends(
    session: Any,
    *,
    min_rating: float | str = 4.0,
    start_page: int = 1,
    stop_page: int | None = 3,
    verbose: bool = False,
) -> RatingSelectionResult:
    """Convenience wrapper for the main use case: recent high-rated activity."""
    return select_rated_viewings(
        session,
        url=DEFAULT_ACTIVITY_URL,
        min_rating=min_rating,
        max_rating=5.0,
        start_page=start_page,
        stop_page=stop_page,
        verbose=verbose,
    )


# Backward-friendly aliases for discoverability.
fetch_rated_viewings = select_rated_viewings
filter_rated_viewings = select_rated_viewings
fetch_rated_reviews = select_rated_reviews
filter_rated_reviews = select_rated_reviews
RatedViewingSelection = RatedReviewSelection


# ---------------------------------------------------------------------------
# Fetch dispatch
# ---------------------------------------------------------------------------


def detect_source_kind(url: str) -> SourceKind:
    parsed = urlparse(_normalize_url(url))
    path = parsed.path.rstrip("/") + "/"

    if path == "/activity/":
        return "activity"
    if path.startswith("/ajax/activity-pagination/"):
        return "activity"

    bits = [bit for bit in parsed.path.split("/") if bit]
    if len(bits) == 2 and bits[1] == "activity":
        return "activity"

    return "reviews"


def _fetch_raw_items(
    session: Any,
    url: str,
    *,
    source: SourceKind,
    start_page: int,
    stop_page: int | None,
    verbose: bool,
    activity_filter_preset: str = "diary_reviews",
    activity_filters: Any | None = None,
) -> list[Any]:
    pages = _page_count(start_page=start_page, stop_page=stop_page)

    if source == "activity":
        from lboxdlib.fetchers.activity import fetch_activities

        # No need to hydrate likedReviewByMe for rating selection.
        try:
            return list(
                fetch_activities(
                    session,
                    url,
                    start=start_page,
                    pages=pages,
                    verbose=verbose,
                    include_raw_html=False,
                    hydrate_likes=False,
                    strict_hydration=False,
                    activity_filter_preset=activity_filter_preset,
                    activity_filters=activity_filters,
                )
            )
        except TypeError as exc:
            # Older activity_page_handling versions do not accept activity_filter_*
            # arguments. This fallback still works if activity_from_page parses
            # ReviewActivity/DiaryActivity and this selector ignores reference rows,
            # but it may fetch extra irrelevant activity rows.
            if "activity_filter" not in str(exc):
                raise
            return list(
                fetch_activities(
                    session,
                    url,
                    start=start_page,
                    pages=pages,
                    verbose=verbose,
                    include_raw_html=False,
                    hydrate_likes=False,
                    strict_hydration=False,
                )
            )

    from lboxdlib.fetchers.reviews import fetch_reviews

    return list(
        fetch_reviews(
            session,
            url,
            start=start_page,
            pages=pages,
            verbose=verbose,
        )
    )


def _selection_from_raw(raw: Any, *, source: SourceKind, source_url: str) -> RatedReviewSelection | None:
    if source == "activity":
        from lboxdlib.parsers.activity import DiaryActivity, ReviewActivity

        if isinstance(raw, ReviewActivity):
            return _selection_from_activity(raw, source_url=source_url)
        if isinstance(raw, DiaryActivity):
            return _selection_from_diary_activity(raw, source_url=source_url)
        return None

    return _selection_from_review_summary(raw, source_url=source_url)


def _selection_from_activity(item: Any, *, source_url: str) -> RatedReviewSelection:
    film = getattr(item, "film", None)
    rating = _rating_from_fields(getattr(item, "rating", None), getattr(item, "ratingText", None))

    return RatedReviewSelection(
        source="activity",
        source_url=source_url,
        owner=getattr(item, "ownerSlug", None),
        owner_display_name=getattr(item, "ownerDisplayName", None),
        film_slug=getattr(film, "slug", None) if film else None,
        film_name=getattr(film, "name", None) if film else None,
        film_year=getattr(film, "year", None) if film else None,
        film_lid=getattr(film, "lid", None) if film else None,
        film_uid=getattr(film, "uid", None) if film else None,
        rating=rating,
        rating_text=getattr(item, "ratingText", None),
        review_url=getattr(item, "reviewUrl", None),
        diary_url=None,
        viewing_uid=getattr(item, "viewingUid", None),
        viewing_id=getattr(item, "viewingId", None),
        review_lid=getattr(item, "reviewLid", None),
        date=getattr(item, "datetime", None),
        activity_id=getattr(item, "activityId", None),
        viewing_action=getattr(item, "viewingAction", None),
        review_text=getattr(item, "reviewText", None),
        full_text_url=getattr(item, "fullTextUrl", None),
        liked_film=getattr(item, "likedFilm", None),
        likes_count=getattr(item, "likesCount", None),
        likes_url=getattr(item, "likesUrl", None),
        comments_count=getattr(item, "commentsCount", None),
        comments_url=getattr(item, "commentsUrl", None),
        raw_type=type(item).__name__,
    )


def _selection_from_diary_activity(item: Any, *, source_url: str) -> RatedReviewSelection:
    film = getattr(item, "film", None)
    rating = _rating_from_fields(getattr(item, "rating", None), getattr(item, "ratingText", None))

    return RatedReviewSelection(
        source="activity",
        source_url=source_url,
        owner=getattr(item, "ownerSlug", None),
        owner_display_name=getattr(item, "ownerDisplayName", None),
        film_slug=getattr(film, "slug", None) if film else None,
        film_name=getattr(film, "name", None) if film else None,
        film_year=getattr(film, "year", None) if film else None,
        film_lid=getattr(film, "lid", None) if film else None,
        film_uid=getattr(film, "uid", None) if film else None,
        rating=rating,
        rating_text=getattr(item, "ratingText", None),
        review_url=None,
        diary_url=getattr(item, "targetUrl", None),
        viewing_uid=None,
        viewing_id=None,
        review_lid=None,
        date=getattr(item, "datetime", None),
        activity_id=getattr(item, "activityId", None),
        viewing_action=getattr(item, "viewingAction", None),
        review_text=None,
        full_text_url=None,
        liked_film=getattr(item, "likedFilm", None),
        likes_count=None,
        likes_url=None,
        comments_count=None,
        comments_url=None,
        raw_type=type(item).__name__,
    )


def _selection_from_review_summary(review: Any, *, source_url: str) -> RatedReviewSelection:
    rating = _rating_from_fields(getattr(review, "rating", None), getattr(review, "ratingText", None))

    return RatedReviewSelection(
        source="reviews",
        source_url=source_url,
        owner=getattr(review, "owner", None),
        owner_display_name=getattr(review, "ownerDisplayName", None),
        film_slug=getattr(review, "filmSlug", None),
        film_name=getattr(review, "filmName", None),
        film_year=getattr(review, "filmYear", None),
        film_lid=getattr(review, "filmLid", None),
        film_uid=getattr(review, "filmUid", None),
        rating=rating,
        rating_text=getattr(review, "ratingText", None),
        review_url=getattr(review, "reviewUrl", None),
        diary_url=None,
        reviews_url=getattr(review, "reviewsUrl", None),
        viewing_uid=getattr(review, "viewingUid", None),
        viewing_id=getattr(review, "viewingId", None),
        review_lid=getattr(review, "reviewLid", None),
        date=getattr(review, "date", None),
        viewing_action=getattr(review, "viewingAction", None),
        review_text=getattr(review, "reviewText", None),
        full_text_url=getattr(review, "fullTextUrl", None),
        liked_film=getattr(review, "likedFilm", None),
        likes_count=getattr(review, "likesCount", None),
        likes_url=getattr(review, "likesUrl", None),
        comments_count=getattr(review, "commentsCount", None),
        comments_url=getattr(review, "commentsUrl", None),
        raw_type=type(review).__name__,
    )


# ---------------------------------------------------------------------------
# Rating helpers
# ---------------------------------------------------------------------------


def _rating_from_fields(rating: Any, rating_text: Any) -> float | None:
    if rating is not None:
        try:
            return float(rating)
        except (TypeError, ValueError):
            pass
    if rating_text is None:
        return None
    return _coerce_rating(rating_text)


def _coerce_rating(value: float | int | str | None) -> float | None:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None

    if "★" in text or "½" in text:
        return float(text.count("★")) + (0.5 if "½" in text else 0.0)

    # Accept common ASCII spellings like "4.5", "4 1/2", "4½".
    text = text.replace("½", ".5")
    fraction_match = re.fullmatch(r"(\d+)\s*1/2", text)
    if fraction_match:
        return float(fraction_match.group(1)) + 0.5

    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"Could not parse rating value: {value!r}") from exc


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


def _normalize_url(url: str | None) -> str:
    text = (url or DEFAULT_ACTIVITY_URL).strip()
    return urljoin(LETTERBOXD_BASE, text)


def _absolute_url(url: str) -> str:
    return urljoin(LETTERBOXD_BASE, url)


def _page_count(*, start_page: int, stop_page: int | None) -> int | None:
    if stop_page is None:
        return None
    return stop_page - start_page + 1


def _validate_pages(*, start_page: int, stop_page: int | None) -> None:
    if start_page < 1:
        raise ValueError("start_page must be >= 1")
    if stop_page is not None and stop_page < start_page:
        raise ValueError("stop_page must be None or >= start_page")


__all__ = [
    "RatedReviewSelection",
    "RatedViewingSelection",
    "RatingFilter",
    "RatingSelectionResult",
    "detect_source_kind",
    "select_rated_viewings",
    "select_rating_range",
    "select_rated_reviews",
    "select_star_range_reviews",
    "select_exact_star_reviews",
    "find_recent_highly_rated_by_friends",
    "fetch_rated_viewings",
    "filter_rated_viewings",
    "fetch_rated_reviews",
    "filter_rated_reviews",
]
