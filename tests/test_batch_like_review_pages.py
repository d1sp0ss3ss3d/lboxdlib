from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

try:
    import curl_cffi  # noqa: F401
except ModuleNotFoundError:  # Fake-session tests do not require curl_cffi.
    curl_cffi = types.ModuleType("curl_cffi")
    curl_cffi.requests = types.SimpleNamespace(Session=type("Session", (), {}))
    sys.modules["curl_cffi"] = curl_cffi

import lboxdlib.modules.batch_like_reviews as batch
import lboxdlib.modules.like_state as like_state


class FakeResponse:
    def __init__(self, payload, *, status_code: int = 200, text: str = "") -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class MetadataSession:
    def __init__(self) -> None:
        self.posts: list[dict] = []

    def post(self, url, *, data, headers):
        call = {"url": url, "data": list(data), "headers": dict(headers)}
        self.posts.append(call)
        requested = [value for key, value in data if key == "likeables"]
        liked = [uid for uid in requested if uid in {"viewing:2", "viewing:4"}]
        return FakeResponse({"result": True, "liked": liked})


def review(number: int):
    return SimpleNamespace(
        owner="other-member",
        filmSlug=f"film-{number}",
        reviewUrl=f"/other-member/film/film-{number}/",
        viewingUid=f"viewing:{number}",
        viewingId=number,
    )


def candidate(number: int, *, already_liked: bool) -> batch.ReviewLikeCandidate:
    return batch.ReviewLikeCandidate(
        source="reviews",
        source_url="https://letterboxd.com/other-member/films/reviews/",
        page_number=1 if number <= 3 else 2,
        owner="other-member",
        review_url=f"/other-member/film/film-{number}/",
        viewing_uid=f"viewing:{number}",
        viewing_id=number,
        already_liked=already_liked,
    )


def test_get_review_like_states_returns_false_for_requested_ids_absent_from_liked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MetadataSession()
    monkeypatch.setattr(like_state, "get_csrf", lambda session: "csrf-token")

    states = like_state.get_review_like_states(
        session,
        [1, "viewing:2", SimpleNamespace(viewingId=3), "viewing:4", 1],
        referer="https://letterboxd.com/other-member/films/reviews/",
        batch_size=2,
    )

    assert states == {
        "viewing:1": False,
        "viewing:2": True,
        "viewing:3": False,
        "viewing:4": True,
    }
    assert len(session.posts) == 2
    assert session.posts[0]["data"] == [
        ("likeables", "viewing:1"),
        ("likeables", "viewing:2"),
    ]
    assert session.posts[1]["data"] == [
        ("likeables", "viewing:3"),
        ("likeables", "viewing:4"),
    ]
    for post in session.posts:
        assert post["url"] == "https://letterboxd.com/ajax/letterboxd-metadata/"
        assert post["headers"]["X-Requested-With"] == "XMLHttpRequest"
        assert post["headers"]["Referer"] == "https://letterboxd.com/other-member/films/reviews/"


def test_review_page_iterator_hydrates_every_candidate_before_yielding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lboxdlib.fetchers.pages as pages
    import lboxdlib.parsers.reviews as reviews_parser

    monkeypatch.setattr(
        pages,
        "detect_pagination_type",
        lambda session, url, verbose=False: ("single", "<html></html>"),
    )
    monkeypatch.setattr(reviews_parser, "extract_reviews", lambda html: [review(1), review(2), review(3)])

    captured = {}

    def fake_states(session, targets, *, referer, batch_size):
        captured["targets"] = list(targets)
        captured["referer"] = referer
        return {"viewing:1": True, "viewing:2": False, "viewing:3": True}

    monkeypatch.setattr(like_state, "get_review_like_states", fake_states)

    pages_out = list(
        batch.iter_review_page_candidates(
            object(),
            "https://letterboxd.com/other-member/films/reviews/",
            retry_attempts=1,
        )
    )

    assert len(pages_out) == 1
    page_number, candidates = pages_out[0]
    assert page_number == 1
    assert [item.already_liked for item in candidates] == [True, False, True]
    assert captured["targets"] == ["viewing:1", "viewing:2", "viewing:3"]
    assert captured["referer"] == "https://letterboxd.com/other-member/films/reviews/"


def test_like_num_reviews_on_review_pages_counts_only_new_likes_and_continues_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page_one = [
        candidate(1, already_liked=True),
        candidate(2, already_liked=False),
        candidate(3, already_liked=True),
    ]
    page_two = [
        candidate(4, already_liked=False),
        candidate(5, already_liked=False),
        candidate(6, already_liked=False),
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
            liked_targets.append(target) or SimpleNamespace(liked=True)
        ),
    )

    result = batch.like_num_reviews(
        object(),
        count=3,
        url="https://letterboxd.com/other-member/films/reviews/",
        retry_attempts=1,
    )

    assert liked_targets == [2, 4, 5]
    assert result.liked == 3
    assert result.skipped_already_liked == 2
    assert result.pages_scanned == 2
    assert result.stop_reason == "like_count_reached"


def test_review_candidate_without_embedded_uid_is_resolved_before_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = batch.ReviewLikeCandidate(
        source="reviews",
        source_url="https://letterboxd.com/other-member/films/reviews/",
        page_number=1,
        owner="other-member",
        review_url="/other-member/film/example/",
    )

    monkeypatch.setattr(
        like_state,
        "get_viewing_id_from_review_url",
        lambda session, url: 77,
    )
    monkeypatch.setattr(
        like_state,
        "get_review_like_states",
        lambda session, targets, *, referer, batch_size: {"viewing:77": False},
    )

    batch._hydrate_review_candidate_like_states(
        object(),
        [item],
        referer=item.source_url,
        retry_attempts=1,
        retry_backoff_seconds=0,
    )

    assert item.viewing_id == 77
    assert item.viewing_uid == "viewing:77"
    assert item.already_liked is False


def test_metadata_failure_prevents_review_like_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lboxdlib.fetchers.pages as pages
    import lboxdlib.parsers.reviews as reviews_parser

    monkeypatch.setattr(batch, "_signed_in_user", lambda session: "signed-in-user")
    monkeypatch.setattr(
        pages,
        "detect_pagination_type",
        lambda session, url, verbose=False: ("single", "<html></html>"),
    )
    monkeypatch.setattr(reviews_parser, "extract_reviews", lambda html: [review(1)])
    monkeypatch.setattr(
        like_state,
        "get_review_like_states",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("metadata unavailable")),
    )

    like_calls = []
    monkeypatch.setattr(
        batch,
        "_like_review",
        lambda *args, **kwargs: like_calls.append((args, kwargs)),
    )

    result = batch.like_num_reviews(
        object(),
        count=1,
        url="https://letterboxd.com/other-member/films/reviews/",
        retry_attempts=1,
    )

    assert like_calls == []
    assert result.liked == 0
    assert result.failures
    assert "metadata unavailable" in result.failures[0].error


def test_failed_like_does_not_count_and_later_unliked_review_is_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [
        candidate(1, already_liked=False),
        candidate(2, already_liked=False),
        candidate(3, already_liked=False),
    ]

    monkeypatch.setattr(batch, "_signed_in_user", lambda session: "signed-in-user")
    monkeypatch.setattr(
        batch,
        "iter_review_page_candidates",
        lambda *args, **kwargs: iter([(1, candidates)]),
    )

    calls: list[int | str] = []

    def fake_like(session, target, *, referer):
        calls.append(target)
        if target == 1:
            raise RuntimeError("request failed")
        return SimpleNamespace(liked=True)

    monkeypatch.setattr(batch, "_like_review", fake_like)

    result = batch.like_num_reviews(
        object(),
        count=2,
        url="https://letterboxd.com/other-member/films/reviews/",
        retry_attempts=1,
    )

    assert calls == [1, 2, 3]
    assert result.liked == 2
    assert len(result.failures) == 1
    assert result.stop_reason == "like_count_reached"
