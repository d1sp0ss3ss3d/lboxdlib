from __future__ import annotations

import inspect
from importlib import import_module
from types import SimpleNamespace

import pytest

module = import_module("lboxdlib.workflows.batch_like_reviews")


class FakeClock:
    def __init__(self, start: float = 100.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def candidate(number: int) -> module.ReviewLikeCandidate:
    return module.ReviewLikeCandidate(
        source="activity",
        source_url="https://letterboxd.com/activity/",
        page_number=1,
        owner=f"member-{number}",
        review_url=f"https://letterboxd.com/member-{number}/film/example/",
        viewing_id=number,
        already_liked=False,
    )


def install_batch_fakes(monkeypatch: pytest.MonkeyPatch, candidates):
    monkeypatch.setattr(module, "_signed_in_user", lambda session: "signed-in-user")
    monkeypatch.setattr(
        module,
        "iter_activity_review_candidates",
        lambda *args, **kwargs: iter([(1, list(candidates))]),
    )


def test_interval_is_exposed_by_batch_and_all_convenience_functions() -> None:
    functions_and_defaults = (
        (module.batch_like_reviews, 15.0),
        (module.batch_like_reviews_from_urls, 15.0),
        (module.like_num_reviews, 0.0),
        (module.like_page_range_reviews, 0.0),
        (module.like_until_liked_reviews, 0.0),
    )

    for function, default in functions_and_defaults:
        parameter = inspect.signature(function).parameters["interval"]
        assert parameter.default == default
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.parametrize(
    ("interval", "delays"),
    [(5, (5.0, 5.0)), ((3, 7), (4.0, 6.0))],
    ids=["fixed", "range"],
)
def test_positive_interval_spaces_requests_without_trailing_sleep(
    monkeypatch, interval, delays
) -> None:
    install_batch_fakes(monkeypatch, [candidate(1), candidate(2), candidate(3)])
    clock = FakeClock()
    request_times: list[float] = []

    monkeypatch.setattr(module.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(module.time, "sleep", clock.sleep)

    def fake_like(session, target, *, referer):
        request_times.append(clock.now)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(module, "_like_review", fake_like)

    if isinstance(interval, tuple):
        samples = iter(delays)

        def sample_interval(low, high):
            assert (low, high) == interval
            return next(samples)

        monkeypatch.setattr(module.random, "uniform", sample_interval)

    result = module.batch_like_reviews(
        object(),
        like_count=3,
        interval=interval,
        retry_attempts=1,
    )

    assert result.liked == 3
    assert result.interval == interval
    assert request_times == [100.0, 100.0 + delays[0], 100.0 + sum(delays)]
    assert clock.sleeps == list(delays)


def test_zero_interval_performs_no_interval_sleep(monkeypatch) -> None:
    install_batch_fakes(monkeypatch, [candidate(1), candidate(2)])
    clock = FakeClock()
    request_times: list[float] = []

    monkeypatch.setattr(module.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(module.time, "sleep", clock.sleep)
    monkeypatch.setattr(
        module,
        "_like_review",
        lambda session, target, *, referer: (
            request_times.append(clock.now) or SimpleNamespace(status_code=200)
        ),
    )

    result = module.batch_like_reviews(
        object(),
        like_count=2,
        interval=0,
        retry_attempts=1,
    )

    assert result.liked == 2
    assert request_times == [100.0, 100.0]
    assert clock.sleeps == []


def test_retry_attempts_use_backoff_without_drawing_more_intervals(monkeypatch) -> None:
    install_batch_fakes(monkeypatch, [candidate(1), candidate(2)])
    clock = FakeClock()
    request_times: list[float] = []
    attempts = 0
    interval_samples: list[tuple[float, float]] = []

    monkeypatch.setattr(module.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(module.time, "sleep", clock.sleep)

    def fake_like(session, target, *, referer):
        nonlocal attempts
        attempts += 1
        request_times.append(clock.now)
        if target == 2 and attempts < 4:
            raise RuntimeError("HTTP 503")
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(module, "_like_review", fake_like)

    def sample_interval(low, high):
        interval_samples.append((low, high))
        return 255.13

    monkeypatch.setattr(module.random, "uniform", sample_interval)

    result = module.batch_like_reviews(
        object(),
        like_count=2,
        interval=(18.9, 601.1),
        retry_attempts=3,
        retry_backoff_seconds=2,
    )

    assert result.liked == 2
    assert request_times == [100.0, 355.13, 357.13, 361.13]
    assert clock.sleeps == [255.13, 2, 4]
    assert interval_samples == [(18.9, 601.1)]


def test_like_num_reviews_forwards_interval(monkeypatch) -> None:
    captured = {}
    expected = object()

    def fake_batch(session, **kwargs):
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(module, "batch_like_reviews", fake_batch)

    result = module.like_num_reviews(object(), count=12, interval=9.5)

    assert result is expected
    assert captured["like_count"] == 12
    assert captured["interval"] == 9.5


@pytest.mark.parametrize(
    "bad_interval",
    [-1, float("inf"), float("nan"), "not-a-number", (7, 3), (-1, 3), (3,), (1, 2, 3)],
)
def test_invalid_interval_is_rejected(bad_interval) -> None:
    with pytest.raises(ValueError, match="interval"):
        module.batch_like_reviews(object(), like_count=0, interval=bad_interval)


def test_interval_cannot_be_mixed_with_legacy_sleep_controls() -> None:
    with pytest.raises(ValueError, match="cannot be combined"):
        module.batch_like_reviews(
            object(),
            like_count=0,
            interval=3,
            sleep_after_attempts=1,
            sleep_time=2,
        )

