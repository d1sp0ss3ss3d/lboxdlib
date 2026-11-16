from __future__ import annotations

import importlib
import json
import random
from itertools import groupby
from types import SimpleNamespace

import pytest

from lboxdlib.actions.likes import RateLimitError
from lboxdlib.workflows import MultiSourceBatchLikeResult, batch_like_reviews_from_urls

batch = importlib.import_module("lboxdlib.workflows.batch_like_reviews")

A = "https://letterboxd.com/alice/films/reviews/"
B = "https://letterboxd.com/bob/films/reviews/"
C = "https://letterboxd.com/carol/films/reviews/"


def candidate(number, url=A, **overrides):
    values = dict(
        source=batch.detect_source_kind(url),
        source_url=url,
        page_number=1,
        owner="other-member",
        review_url=f"/other-member/film/film-{number}/",
        viewing_uid=f"viewing:{number}",
        viewing_id=number,
        rating=4.0,
        already_liked=False,
    )
    values.update(overrides)
    return batch.ReviewLikeCandidate(**values)


def install_fakes(monkeypatch, source_pages):
    calls = []
    fetched = []
    closed = []
    monkeypatch.setattr(batch, "_signed_in_user", lambda session: "signed-in-user")

    def pages(session, url, **kwargs):
        try:
            for page_number, candidates in source_pages[url]:
                fetched.append((url, page_number, kwargs))
                yield page_number, candidates
        finally:
            closed.append(url)

    def like(session, target, *, referer):
        calls.append((referer, target))
        return SimpleNamespace(liked=True)

    monkeypatch.setattr(batch, "iter_review_page_candidates", pages)
    monkeypatch.setattr(batch, "iter_activity_review_candidates", pages)
    monkeypatch.setattr(batch, "_like_review", like)
    return calls, fetched, closed


def test_switches_sources_and_resumes_mid_page_without_refetching(monkeypatch):
    calls, fetched, closed = install_fakes(
        monkeypatch,
        {
            A: [(1, [candidate(1), candidate(2)]), (2, [candidate(3, page_number=2)])],
            B: [(1, [candidate(n, B) for n in (4, 5, 6)])],
        },
    )

    result = batch_like_reviews_from_urls(
        object(), urls=[A, B], like_count=5, min_batch_size=1, max_batch_size=1,
        seed=1, interval=0,
    )

    assert calls == [(A, 1), (B, 4), (A, 2), (B, 5), (A, 3)]
    assert [(url, page) for url, page, _ in fetched] == [(A, 1), (B, 1), (A, 2)]
    assert sorted(closed) == [A, B]
    assert isinstance(result, MultiSourceBatchLikeResult)
    assert result.liked == result.like_attempts == 5
    assert result.pages_scanned == 3
    assert result.stop_reason == "like_count_reached"
    assert [item.liked for item in result.source_results] == [3, 2]
    assert all(item.stop_reason is None for item in result.source_results)


def test_seed_repeats_variable_source_batches_without_changing_global_rng(monkeypatch):
    def run():
        calls, _, _ = install_fakes(
            monkeypatch,
            {
                url: [(1, [candidate(offset + n, url) for n in range(1, 21)])]
                for url, offset in ((A, 0), (B, 100), (C, 200))
            },
        )
        result = batch_like_reviews_from_urls(
            object(), urls=[A, B, C], like_count=12, seed=42, interval=0,
        )
        assert result.liked == 12
        return calls

    global_state = random.getstate()
    expected = [201, 1, 2, 3, 101, 102, 4, 103, 104, 105, 106, 107]
    first = run()
    assert [target for _, target in first] == expected
    assert run() == first
    assert random.getstate() == global_state


def test_random_batch_sizes_respect_configured_bounds_and_global_cap(monkeypatch):
    calls, _, _ = install_fakes(
        monkeypatch,
        {
            url: [(1, [candidate(offset + n, url) for n in range(1, 41)])]
            for url, offset in ((A, 0), (B, 100), (C, 200))
        },
    )
    result = batch_like_reviews_from_urls(
        object(), urls=[A, B, C], like_count=30, min_batch_size=2,
        max_batch_size=4, seed=7, interval=0,
    )

    sizes = [len(list(items)) for _, items in groupby(calls, key=lambda call: call[0])]
    assert all(2 <= size <= 4 for size in sizes[:-1])
    assert 1 <= sizes[-1] <= 4
    assert len(set(sizes)) > 1
    assert len(calls) == result.liked == 30


@pytest.mark.parametrize("dry_run", [False, True])
def test_overlapping_buffered_sources_do_not_double_count_reviews(monkeypatch, dry_run):
    calls, _, _ = install_fakes(
        monkeypatch,
        {
            A: [(1, [candidate(1), candidate(2)])],
            B: [(1, [candidate(1, B), candidate(3, B)])],
        },
    )
    result = batch_like_reviews_from_urls(
        object(), urls=[A, B], min_batch_size=1, max_batch_size=1,
        seed=1, interval=0, dry_run=dry_run,
    )

    assert calls == ([] if dry_run else [(A, 1), (B, 3), (A, 2)])
    assert result.liked == result.like_attempts == 3
    assert result.source_results[1].skipped_already_liked == 1
    assert result.stop_reason == "sources_exhausted"


@pytest.mark.parametrize("identity", ["viewing", "url"])
def test_duplicates_match_different_target_representations(monkeypatch, identity):
    if identity == "viewing":
        first = candidate(1, viewing_uid=None, review_url=None)
        second = candidate(1, B, viewing_id=None, review_url=None)
    else:
        first = candidate(1, viewing_id=None, viewing_uid=None)
        second = candidate(
            1, B, viewing_id=None, viewing_uid=None,
            review_url="https://letterboxd.com/other-member/film/film-1/?ref=test#review",
        )
    calls, _, _ = install_fakes(monkeypatch, {A: [(1, [first])], B: [(1, [second])]})

    result = batch_like_reviews_from_urls(object(), urls=[A, B], seed=1, interval=0)

    assert len(calls) == result.liked == 1
    assert sum(item.skipped_already_liked for item in result.source_results) == 1


def test_skips_and_failures_do_not_consume_batch_or_total_quota(monkeypatch):
    calls, _, _ = install_fakes(
        monkeypatch,
        {
            A: [(1, [
                candidate(1, owner="signed-in-user"),
                candidate(2, rating=2.0),
                candidate(3, rating=5.0),
                candidate(4, rating=None),
                candidate(5, already_liked=True),
                candidate(6, viewing_id=None, viewing_uid=None, review_url=None),
                candidate(7), candidate(8), candidate(9),
            ])],
            B: [(1, [candidate(10, B)])],
        },
    )

    def like(session, target, *, referer):
        calls.append((referer, target))
        if target == 7:
            raise RuntimeError("request failed")
        return SimpleNamespace(liked=True)

    monkeypatch.setattr(batch, "_like_review", like)
    result = batch_like_reviews_from_urls(
        object(), urls=[A, B], like_count=3, min_batch_size=2, max_batch_size=2,
        seed=1, interval=0, min_rating=3.5, max_rating=4.5,
    )

    assert calls == [(A, 7), (A, 8), (A, 9), (B, 10)]
    assert result.liked == 3
    assert result.like_attempts == 4
    assert len(result.failures) == 1
    source = result.source_results[0]
    assert source.skipped_own_reviews == source.skipped_already_liked == 1
    assert source.skipped_unrated == source.skipped_missing_target == 1
    assert source.skipped_below_min_rating == source.skipped_above_max_rating == 1


def test_interval_is_shared_across_sources_but_not_reapplied_to_retries(monkeypatch):
    calls, _, _ = install_fakes(
        monkeypatch,
        {A: [(1, [candidate(1), candidate(2)])], B: [(1, [candidate(3, B)])]},
    )
    now = [100.0]
    sleeps = []
    request_times = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    def like(session, target, *, referer):
        calls.append((referer, target))
        request_times.append(now[0])
        if len(calls) == 1:
            raise RuntimeError("HTTP 503")
        return SimpleNamespace(liked=True)

    monkeypatch.setattr(batch.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(batch.time, "sleep", sleep)
    monkeypatch.setattr(batch, "_like_review", like)

    result = batch_like_reviews_from_urls(
        object(), urls=[A, B], like_count=3, min_batch_size=1, max_batch_size=1,
        seed=1, interval=5, retry_attempts=2, retry_backoff_seconds=2,
    )

    assert calls == [(A, 1), (A, 1), (B, 3), (A, 2)]
    assert request_times == [100.0, 102.0, 107.0, 112.0]
    assert sleeps == [2, 5, 5]
    assert result.liked == result.like_attempts == 3
    assert result.failures == []


@pytest.mark.parametrize("stage", ["fetch", "like"])
@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (RateLimitError("too many recent review likes"), "rate_limited"),
        (RuntimeError("You must be signed in"), "authentication_failed"),
    ],
)
def test_terminal_error_stops_all_sources_without_retrying(monkeypatch, stage, error, reason):
    calls, fetched, _ = install_fakes(
        monkeypatch,
        {A: [(1, [candidate(1), candidate(2)])], B: [(1, [candidate(3, B)])]},
    )
    if stage == "like":
        def like(session, target, *, referer):
            calls.append((referer, target))
            if target == 2:
                raise error
            return SimpleNamespace(liked=True)

        monkeypatch.setattr(batch, "_like_review", like)
    else:
        def pages(session, url, **kwargs):
            fetched.append((url, 1, kwargs))
            yield 1, [candidate(1, url)]
            raise error

        monkeypatch.setattr(batch, "iter_review_page_candidates", pages)

    result = batch_like_reviews_from_urls(
        object(), urls=[A, B], min_batch_size=3, max_batch_size=3,
        seed=1, interval=0, retry_attempts=3,
    )

    assert result.liked == 1
    assert result.stop_reason == reason
    assert len(result.failures) == 1
    assert result.source_results[0].stop_reason == reason
    assert {url for url, _, _ in fetched} == {A}
    assert calls == ([(A, 1), (A, 2)] if stage == "like" else [(A, 1)])


def test_source_fetch_failure_preserves_progress_and_other_sources_continue(monkeypatch):
    calls, _, _ = install_fakes(monkeypatch, {})

    def pages(session, url, **kwargs):
        yield 1, [candidate(1 if url == A else 2, url)]
        if url == A:
            raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(batch, "iter_review_page_candidates", pages)
    result = batch_like_reviews_from_urls(
        object(), urls=[A, B], min_batch_size=3, max_batch_size=3, seed=1, interval=0,
    )

    assert calls == [(A, 1), (B, 2)]
    assert result.liked == 2
    assert result.stop_reason == "sources_exhausted"
    assert len(result.failures) == 1
    assert result.failures[0].url == A
    assert result.failures[0].error == "metadata unavailable"


def test_already_liked_stop_condition_retires_only_that_source(monkeypatch):
    calls, _, _ = install_fakes(
        monkeypatch,
        {
            A: [(1, [candidate(1, already_liked=True), candidate(2, already_liked=True), candidate(3)])],
            B: [(1, [candidate(4, B), candidate(5, B)])],
        },
    )
    result = batch_like_reviews_from_urls(
        object(), urls=[A, B], seed=1, interval=0,
        stop_after_consecutive_already_liked=2,
    )

    assert calls == [(B, 4), (B, 5)]
    assert result.liked == 2
    assert result.source_results[0].stop_reason == "consecutive_already_liked_reached"
    assert result.stop_reason == "sources_exhausted"


def test_mixed_sources_receive_independent_page_ranges_and_empty_sources_retire(monkeypatch):
    activity = batch.DEFAULT_ACTIVITY_URL
    calls, fetched, _ = install_fakes(
        monkeypatch,
        {A: [(2, [])], activity: [(2, [candidate(1, activity, page_number=2)])]},
    )
    result = batch_like_reviews_from_urls(
        object(), urls=[A, activity], start_page=2, stop_page=2, seed=1, interval=0,
    )

    assert calls == [(activity, 1)]
    assert [source.source for source in result.source_results] == ["reviews", "activity"]
    assert all(kwargs["start_page"] == kwargs["stop_page"] == 2 for _, _, kwargs in fetched)
    assert all(source.stop_reason == "stop_page_reached" for source in result.source_results)


def test_zero_count_normalizes_and_deduplicates_urls_without_session_io(monkeypatch):
    monkeypatch.setattr(batch, "_signed_in_user", lambda session: pytest.fail("no session I/O"))
    urls = (url for url in [" /alice/films/reviews/ ", A, B])

    result = batch_like_reviews_from_urls(object(), urls=urls, like_count=0)

    assert [source.url for source in result.source_results] == [A, B]
    assert result.stop_reason == "like_count_reached"
    assert result.liked == result.like_attempts == result.pages_scanned == 0


@pytest.mark.parametrize("urls", [[], None, A, b"url", [None], [42], [""], ["   "]])
def test_invalid_source_urls_are_rejected_before_session_io(monkeypatch, urls):
    monkeypatch.setattr(batch, "_signed_in_user", lambda session: pytest.fail("no session I/O"))
    with pytest.raises(ValueError, match="urls"):
        batch_like_reviews_from_urls(object(), urls=urls)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"min_batch_size": 0}, {"max_batch_size": 0},
        {"min_batch_size": 3, "max_batch_size": 2},
        {"min_batch_size": True}, {"max_batch_size": 2.5},
        {"seed": "seed"}, {"seed": True},
        {"like_count": -1}, {"like_count": 1.5}, {"like_count": True},
        {"start_page": 0}, {"stop_page": 0}, {"start_page": None},
        {"interval": -1}, {"interval": float("nan")},
        {"min_rating": 3.25}, {"min_rating": 4, "max_rating": 3},
        {"stop_after_consecutive_already_liked": 0},
        {"stop_after_consecutive_already_liked": True},
    ],
)
def test_invalid_controls_are_rejected_before_session_io(monkeypatch, kwargs):
    monkeypatch.setattr(batch, "_signed_in_user", lambda session: pytest.fail("no session I/O"))
    with pytest.raises(ValueError):
        batch_like_reviews_from_urls(object(), urls=[A], **kwargs)


def test_result_serializes_aggregate_counts_and_source_failures(monkeypatch):
    install_fakes(monkeypatch, {A: [(1, [candidate(1)])]})
    monkeypatch.setattr(
        batch, "_like_review",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("request failed")),
    )
    result = batch_like_reviews_from_urls(object(), urls=[A], interval=0)
    payload = json.loads(result.to_json())

    assert payload == result.to_dict()
    assert payload["liked"] == 0
    assert payload["like_attempts"] == payload["pages_scanned"] == 1
    assert payload["failures"][0]["candidate"]["viewing_id"] == 1
    assert payload["source_results"][0]["failures"] == payload["failures"]


def test_single_source_entry_point_still_counts_new_likes_and_honors_its_cap(monkeypatch):
    calls, _, closed = install_fakes(
        monkeypatch,
        {A: [(1, [candidate(1, already_liked=True), candidate(2), candidate(3)])]},
    )
    result = batch.batch_like_reviews(object(), url=A, like_count=1, interval=0)

    assert calls == [(A, 2)]
    assert result.liked == result.like_attempts == 1
    assert result.skipped_already_liked == 1
    assert result.stop_reason == "like_count_reached"
    assert closed == [A]

