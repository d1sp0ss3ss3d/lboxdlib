from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

import lboxdlib.modules.batch_like_reviews as batch
import lboxdlib.modules.like_state as like_state


def batch_candidate(
    number: int,
    *,
    rating: float | None,
    already_liked: bool = False,
    page_number: int = 1,
) -> batch.ReviewLikeCandidate:
    return batch.ReviewLikeCandidate(
        source="reviews",
        source_url="https://letterboxd.com/reviewer/films/reviews/",
        page_number=page_number,
        owner="reviewer",
        review_url=f"/reviewer/film/film-{number}/",
        viewing_uid=f"viewing:{number}",
        viewing_id=number,
        rating=rating,
        already_liked=already_liked,
    )


def successful_like_result(viewing_id: int = 1) -> like_state.LikeStateResult:
    return like_state.LikeStateResult(
        result=True,
        liked=True,
        count=1,
        likeable_type="viewing",
        likeable_id=viewing_id,
        boxd_it_code=None,
        response={"result": True, "liked": True},
    )


def test_rating_parameters_are_exposed_on_single_and_batch_like_functions() -> None:
    functions = (
        like_state.like_review,
        batch.batch_like_reviews,
        batch.like_num_reviews,
        batch.like_page_range_reviews,
        batch.like_until_liked_reviews,
    )

    for function in functions:
        signature = inspect.signature(function)
        assert signature.parameters["min_rating"].default is None
        assert signature.parameters["max_rating"].default is None
        assert signature.parameters["min_rating"].kind is inspect.Parameter.KEYWORD_ONLY
        assert signature.parameters["max_rating"].kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.parametrize(
    ("min_rating", "max_rating"),
    [
        (0, None),
        (5.5, None),
        (3.25, None),
        (None, float("inf")),
        (None, float("nan")),
        (True, None),
        (4.0, 3.5),
    ],
)
def test_invalid_rating_bounds_are_rejected(min_rating, max_rating) -> None:
    with pytest.raises(ValueError):
        like_state.validate_review_rating_bounds(min_rating, max_rating)

    with pytest.raises(ValueError):
        batch.batch_like_reviews(
            object(),
            like_count=0,
            min_rating=min_rating,
            max_rating=max_rating,
        )


def test_rating_bounds_are_inclusive() -> None:
    minimum, maximum = like_state.validate_review_rating_bounds(3.5, 4.5)

    assert like_state.review_rating_filter_reason(
        3.5,
        min_rating=minimum,
        max_rating=maximum,
    ) is None
    assert like_state.review_rating_filter_reason(
        4.5,
        min_rating=minimum,
        max_rating=maximum,
    ) is None
    assert like_state.review_rating_filter_reason(
        3.0,
        min_rating=minimum,
        max_rating=maximum,
    ) == "rating_below_minimum"
    assert like_state.review_rating_filter_reason(
        5.0,
        min_rating=minimum,
        max_rating=maximum,
    ) == "rating_above_maximum"


def test_like_num_reviews_skips_rating_misses_and_continues_until_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page_one = [
        batch_candidate(1, rating=2.0),
        batch_candidate(2, rating=4.0, already_liked=True),
        batch_candidate(3, rating=None),
    ]
    page_two = [
        batch_candidate(4, rating=3.5, page_number=2),
        batch_candidate(5, rating=4.5, page_number=2),
        batch_candidate(6, rating=5.0, page_number=2),
        batch_candidate(7, rating=4.0, page_number=2),
    ]

    monkeypatch.setattr(batch, "_signed_in_user", lambda session: "signed-in-user")
    monkeypatch.setattr(
        batch,
        "iter_review_page_candidates",
        lambda *args, **kwargs: iter([(1, page_one), (2, page_two)]),
    )

    liked_targets: list[int | str] = []
    monkeypatch.setattr(
        batch,
        "_like_review",
        lambda session, target, *, referer: (
            liked_targets.append(target) or successful_like_result(int(target))
        ),
    )

    result = batch.like_num_reviews(
        object(),
        count=3,
        url="https://letterboxd.com/reviewer/films/reviews/",
        min_rating=3.5,
        max_rating=4.5,
        retry_attempts=1,
    )

    assert liked_targets == [4, 5, 7]
    assert result.liked == 3
    assert result.pages_scanned == 2
    assert result.skipped_below_min_rating == 1
    assert result.skipped_above_max_rating == 1
    assert result.skipped_unrated == 1
    assert result.skipped_already_liked == 1
    assert result.min_rating == 3.5
    assert result.max_rating == 4.5
    assert result.stop_reason == "like_count_reached"


def test_rating_filter_happens_before_interval_and_like_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [
        batch_candidate(1, rating=1.0),
        batch_candidate(2, rating=None),
        batch_candidate(3, rating=4.0),
    ]
    sleeps: list[float] = []
    calls: list[int | str] = []

    monkeypatch.setattr(batch, "_signed_in_user", lambda session: "signed-in-user")
    monkeypatch.setattr(
        batch,
        "iter_review_page_candidates",
        lambda *args, **kwargs: iter([(1, candidates)]),
    )
    monkeypatch.setattr(batch.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(
        batch,
        "_like_review",
        lambda session, target, *, referer: (
            calls.append(target) or successful_like_result(int(target))
        ),
    )

    result = batch.like_num_reviews(
        object(),
        count=1,
        url="https://letterboxd.com/reviewer/films/reviews/",
        min_rating=3.5,
        interval=30,
        retry_attempts=1,
    )

    assert calls == [3]
    assert sleeps == []
    assert result.liked == 1


def test_activity_and_review_candidates_preserve_reviewer_rating() -> None:
    activity = SimpleNamespace(
        ownerSlug="member",
        film=SimpleNamespace(slug="example"),
        reviewUrl="/member/film/example/",
        viewingUid="viewing:10",
        viewingId=10,
        rating=4.5,
        likedReviewByMe=False,
    )
    review = SimpleNamespace(
        owner="member",
        filmSlug="example",
        reviewUrl="/member/film/example/",
        viewingUid="viewing:11",
        viewingId=11,
        rating=2.5,
        likedReviewByMe=False,
    )

    activity_candidate = batch._candidate_from_activity(
        activity,
        source_url="https://letterboxd.com/activity/",
        page_number=1,
    )
    review_candidate = batch._candidate_from_review(
        review,
        source_url="https://letterboxd.com/member/films/reviews/",
        page_number=1,
    )

    assert activity_candidate.rating == 4.5
    assert review_candidate.rating == 2.5


def test_single_like_skips_below_minimum_without_posting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        like_state,
        "set_review_like_state",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    target = SimpleNamespace(rating=2.5, viewingId=22)

    result = like_state.like_review(object(), target, min_rating=3.0)

    assert calls == []
    assert result.attempted is False
    assert result.changed is False
    assert result.reason == "rating_below_minimum"
    assert result.rating == 2.5
    assert result.likeable_id == 22


def test_single_like_allows_inclusive_boundary_and_posts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    def fake_set(session, target, liked, *, referer):
        captured.update(target=target, liked=liked, referer=referer)
        return successful_like_result(23)

    monkeypatch.setattr(like_state, "set_review_like_state", fake_set)
    target = SimpleNamespace(rating=3.5, viewingId=23)

    result = like_state.like_review(
        object(),
        target,
        min_rating=3.5,
        max_rating=3.5,
        referer="https://letterboxd.com/member/films/reviews/",
    )

    assert captured == {
        "target": target,
        "liked": True,
        "referer": "https://letterboxd.com/member/films/reviews/",
    }
    assert result.attempted is True
    assert result.rating == 3.5


def test_single_like_skips_confirmed_unrated_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        like_state,
        "set_review_like_state",
        lambda *args, **kwargs: pytest.fail("like request must not be sent"),
    )

    result = like_state.like_review(
        object(),
        SimpleNamespace(rating=None, viewingId=24),
        max_rating=5.0,
    )

    assert result.attempted is False
    assert result.reason == "unrated"
    assert result.likeable_id == 24


def test_single_like_with_bare_viewing_id_fails_closed_when_filtering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        like_state,
        "set_review_like_state",
        lambda *args, **kwargs: pytest.fail("like request must not be sent"),
    )

    result = like_state.like_review(object(), 25, min_rating=1.0)

    assert result.attempted is False
    assert result.reason == "rating_unavailable"
    assert result.likeable_id == 25


def test_single_like_url_fetches_rating_once_and_reuses_viewing_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch_calls: list[str] = []
    captured = {}

    def fake_fetch(session, url):
        fetch_calls.append(url)
        return 26, 4.0, True

    def fake_set(session, target, liked, *, referer):
        captured.update(target=target, liked=liked)
        return successful_like_result(26)

    monkeypatch.setattr(like_state, "_fetch_review_details", fake_fetch)
    monkeypatch.setattr(like_state, "set_review_like_state", fake_set)

    result = like_state.like_review(
        object(),
        "https://letterboxd.com/member/film/example/",
        min_rating=4.0,
    )

    assert fetch_calls == ["https://letterboxd.com/member/film/example/"]
    assert captured == {"target": 26, "liked": True}
    assert result.rating == 4.0


def test_single_like_without_rating_bounds_preserves_legacy_fast_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        like_state,
        "_resolve_target_rating",
        lambda *args, **kwargs: pytest.fail("rating must not be resolved without bounds"),
    )
    monkeypatch.setattr(
        like_state,
        "set_review_like_state",
        lambda session, target, liked, *, referer: successful_like_result(27),
    )

    result = like_state.like_review(object(), 27)

    assert result.result is True
    assert result.attempted is True
    assert result.rating is None
