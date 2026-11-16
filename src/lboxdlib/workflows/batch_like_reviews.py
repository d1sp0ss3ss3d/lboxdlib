"""
Batch-like Letterboxd reviews from activity pages or review pages using sane
page-range semantics.

Public API uses:
    start_page: first page/fragment to process, 1-based
    stop_page: last page/fragment to process, inclusive; None means until exhausted
    like_count: number of newly created likes to perform; None means no like cap
    interval: fixed seconds or a (min, max) range between review-like requests

Convenience wrappers:
    like_num_reviews(...)
    like_page_range_reviews(...)
    like_until_liked_reviews(...)

Multi-source scheduling:
    batch_like_reviews_from_urls(...)

Compact activity review-reference rows are deliberately ignored. Only embedded
/full ReviewActivity rows from activity pages are considered.
"""

from __future__ import annotations

import json
import math
import random
import time
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from typing import Any, Literal
from urllib.parse import urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from lboxdlib.actions.likes import (
    get_review_like_states,
    get_viewing_id_from_review_url,
    is_rate_limit_error,
    like_review,
    review_rating_filter_reason,
    validate_review_rating_bounds,
)

LETTERBOXD_BASE = "https://letterboxd.com"
DEFAULT_ACTIVITY_URL = f"{LETTERBOXD_BASE}/activity/"

SourceKind = Literal["activity", "reviews"]
Interval = float | tuple[float, float]
StopReason = Literal[
    "like_count_reached",
    "rate_limited",
    "authentication_failed",
    "consecutive_already_liked_reached",
    "stop_page_reached",
    "no_more_pages",
    "no_candidates",
]

HANDLED_HTTP_ERROR_STATUSES = {403, 429, 503}
RETRYABLE_HTTP_STATUSES = {403, 503}


@dataclass(slots=True)
class ReviewLikeCandidate:
    """Normalized candidate for setting a review/viewing like state."""

    source: SourceKind
    source_url: str
    page_number: int
    owner: str | None = None
    film_slug: str | None = None
    review_url: str | None = None
    viewing_uid: str | None = None
    viewing_id: int | None = None
    rating: float | None = None
    already_liked: bool | None = None
    raw_type: str | None = None

    @property
    def target(self) -> int | str:
        """Return the best target accepted by lboxdlib.actions.likes.like_review."""
        if self.viewing_id is not None:
            return self.viewing_id
        if self.viewing_uid:
            return self.viewing_uid
        if self.review_url:
            return _absolute_url(self.review_url)
        raise ValueError("Candidate has no viewing id, viewing uid, or review URL")


@dataclass(slots=True)
class ReviewLikeFailure:
    candidate: ReviewLikeCandidate | None
    error_type: str
    error: str
    page_number: int | None = None
    url: str | None = None


@dataclass(slots=True)
class BatchLikeResult:
    url: str
    source: SourceKind
    start_page: int
    stop_page: int | None
    like_count: int | None
    interval: Interval = 0.0
    min_rating: float | None = None
    max_rating: float | None = None
    pages_scanned: int = 0
    candidates_seen: int = 0
    skipped_own_reviews: int = 0
    skipped_already_liked: int = 0
    skipped_unrated: int = 0
    skipped_below_min_rating: int = 0
    skipped_above_max_rating: int = 0
    skipped_missing_target: int = 0
    like_attempts: int = 0
    liked: int = 0
    failures: list[ReviewLikeFailure] = field(default_factory=list)
    stop_reason: StopReason | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


@dataclass(slots=True)
class MultiSourceBatchLikeResult:
    """Aggregate outcome with individual results in input URL order.

    A source's ``stop_reason`` remains ``None`` when the overall run stops
    before that source is exhausted. Its ``like_count`` is uncapped; the cap
    on this result applies across all sources.
    """

    source_results: list[BatchLikeResult]
    like_count: int | None
    min_batch_size: int
    max_batch_size: int
    seed: int | None
    stop_reason: Literal[
        "like_count_reached", "rate_limited", "authentication_failed", "sources_exhausted"
    ] | None = None

    @property
    def liked(self) -> int:
        return sum(result.liked for result in self.source_results)

    @property
    def like_attempts(self) -> int:
        return sum(result.like_attempts for result in self.source_results)

    @property
    def pages_scanned(self) -> int:
        return sum(result.pages_scanned for result in self.source_results)

    @property
    def failures(self) -> list[ReviewLikeFailure]:
        return [failure for result in self.source_results for failure in result.failures]

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "liked": self.liked,
            "like_attempts": self.like_attempts,
            "pages_scanned": self.pages_scanned,
            "failures": [asdict(failure) for failure in self.failures],
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


@dataclass(slots=True)
class _BatchLikeState:
    signed_in_user: str | None
    last_like_succeeded_at: float | None = None
    liked_keys: set[str] | None = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def batch_like_reviews(
    session: Any,
    *,
    url: str = DEFAULT_ACTIVITY_URL,
    start_page: int = 1,
    stop_page: int | None = None,
    like_count: int | None = None,
    verbose: bool = False,
    dry_run: bool = False,
    interval: Interval = 15.0,
    min_rating: float | None = None,
    max_rating: float | None = None,
    stop_after_consecutive_already_liked: int | None = None,
    sleep_after_attempts: int | None = None,
    sleep_time: float | None = None,
    retry_attempts: int = 3,
    retry_backoff_seconds: float = 2.0,
) -> BatchLikeResult:
    """Batch-like reviews from a Letterboxd activity or review URL.

    Args:
        session: A signed-in curl_cffi/requests-like Letterboxd session.
        url: Starting URL. Defaults to ``https://letterboxd.com/activity/``.
        start_page: First page/fragment to process, 1-based.
        stop_page: Last page/fragment to process, inclusive. ``None`` means
            continue until no more pages or another stop condition.
        like_count: Number of newly created review likes to perform. Reviews
            already liked before the run and failed requests do not count.
            ``None`` means no like-count cap.
        verbose: Print progress while scanning.
        dry_run: Discover candidates and stop points, but do not POST likes.
        interval: Minimum number of seconds after a successful review like
            before beginning the next like candidate. A ``(min, max)`` pair
            samples one delay for each successful gap. ``0`` disables interval
            throttling. The first request is immediate, retries use only
            ``retry_backoff_seconds``, and no unnecessary trailing sleep occurs
            after the final like.
        min_rating: Inclusive minimum reviewer rating from 0.5 to 5.0 in
            half-star increments. ``None`` means no lower bound.
        max_rating: Inclusive maximum reviewer rating from 0.5 to 5.0 in
            half-star increments. ``None`` means no upper bound.
        stop_after_consecutive_already_liked: Stop after this many consecutive
            candidates are already liked. Activity and review-page candidates
            both have their liked-by-me state hydrated before processing.
        sleep_after_attempts: Deprecated compatibility control. When explicitly
            supplied with ``sleep_time`` and ``interval=0``, sleep after each
            requested number of successful likes.
        sleep_time: Deprecated compatibility sleep duration paired with
            ``sleep_after_attempts``.
        retry_attempts: Attempts for transient HTTP failures. Confirmed rate
            limits and authentication failures stop the batch immediately and
            are not retried.
        retry_backoff_seconds: Backoff multiplier for retryable failures.

    Notes:
        Compact activity review-reference rows are intentionally ignored.
        Review-page candidates are batch-hydrated through Letterboxd metadata
        before any like request, so only newly created likes count toward
        ``like_count``.
    """

    _validate_page_range(start_page=start_page, stop_page=stop_page, like_count=like_count)
    interval = _validate_interval(interval)
    min_rating, max_rating = validate_review_rating_bounds(min_rating, max_rating)
    _validate_legacy_sleep_options(
        interval=interval,
        sleep_after_attempts=sleep_after_attempts,
        sleep_time=sleep_time,
    )

    normalized_url = _normalize_url(url or DEFAULT_ACTIVITY_URL)
    source = detect_source_kind(normalized_url)
    result = BatchLikeResult(
        url=normalized_url,
        source=source,
        start_page=start_page,
        stop_page=stop_page,
        like_count=like_count,
        interval=interval,
        min_rating=min_rating,
        max_rating=max_rating,
    )

    if like_count == 0:
        result.stop_reason = "like_count_reached"
        return result

    state = _BatchLikeState(signed_in_user=_signed_in_user(session))
    for _ in _iter_batch_like_source(
        session,
        result,
        state,
        verbose=verbose,
        dry_run=dry_run,
        stop_after_consecutive_already_liked=stop_after_consecutive_already_liked,
        sleep_after_attempts=sleep_after_attempts,
        sleep_time=sleep_time,
        retry_attempts=retry_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
    ):
        pass
    return result


def batch_like_reviews_from_urls(
    session: Any,
    *,
    urls: Iterable[str],
    like_count: int | None = None,
    min_batch_size: int = 1,
    max_batch_size: int = 5,
    seed: int | None = None,
    start_page: int = 1,
    stop_page: int | None = None,
    verbose: bool = False,
    dry_run: bool = False,
    interval: Interval = 15.0,
    min_rating: float | None = None,
    max_rating: float | None = None,
    stop_after_consecutive_already_liked: int | None = None,
    retry_attempts: int = 3,
    retry_backoff_seconds: float = 2.0,
) -> MultiSourceBatchLikeResult:
    """Like reviews in pseudo-random batches drawn from multiple source URLs.

    Each turn chooses an active source uniformly at random, excluding the last
    chosen source while another is available, then creates a random number of
    new likes between ``min_batch_size`` and ``max_batch_size`` (inclusive).
    Skips and failures do not consume this quota. Each source resumes at its
    next candidate on subsequent turns, preserving page order and pagination
    cursors without fetching earlier pages again.

    ``like_count`` caps new likes across the entire run. ``None`` processes all
    sources to exhaustion. ``start_page`` and ``stop_page`` apply independently
    to every source. Activity and review-page URLs may be mixed; duplicate
    normalized URLs are removed while preserving input order. ``urls`` must be
    a nonempty iterable of nonblank URL strings, not a single string.

    ``seed`` initializes a private random generator for repeatable source and
    batch-size selection given the same candidates and request outcomes. It
    does not change Python's global random state. The default is unseeded.

    Rating filters, own-review skipping, liked-state hydration, and retries
    follow ``batch_like_reviews``. A shared ``interval`` applies once between
    successful review likes, including across source changes. Retries use the
    separate retry backoff and do not draw another interval. Already-liked
    reviews and reviews liked earlier in this run are skipped, including in
    ``dry_run`` mode. Dry runs count proposed likes but never submit them.

    A confirmed rate limit or authentication failure stops the whole run.
    Other fetch errors retire only the affected source; other like failures
    are recorded and scanning continues. The consecutive-already-liked stop
    condition, when supplied, applies independently to each source.

    Returns aggregate counts and per-source ``BatchLikeResult`` objects in
    normalized input order. A turn may be shorter than its chosen size when a
    source stops or the global like cap is reached.
    """
    _validate_page_range(start_page=start_page, stop_page=stop_page, like_count=like_count)
    interval = _validate_interval(interval)
    min_rating, max_rating = validate_review_rating_bounds(min_rating, max_rating)
    _validate_batch_sizes(min_batch_size, max_batch_size)
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise ValueError("seed must be an integer or None")
    if stop_after_consecutive_already_liked is not None and (
        isinstance(stop_after_consecutive_already_liked, bool)
        or not isinstance(stop_after_consecutive_already_liked, int)
        or stop_after_consecutive_already_liked < 1
    ):
        raise ValueError("stop_after_consecutive_already_liked must be an integer >= 1")
    normalized_urls = _normalize_source_urls(urls)
    result = MultiSourceBatchLikeResult(
        source_results=[
            BatchLikeResult(
                url=url,
                source=detect_source_kind(url),
                start_page=start_page,
                stop_page=stop_page,
                like_count=None,
                interval=interval,
                min_rating=min_rating,
                max_rating=max_rating,
            )
            for url in normalized_urls
        ],
        like_count=like_count,
        min_batch_size=min_batch_size,
        max_batch_size=max_batch_size,
        seed=seed,
    )
    if like_count == 0:
        result.stop_reason = "like_count_reached"
        return result

    rng = random.Random(seed)
    state = _BatchLikeState(signed_in_user=_signed_in_user(session), liked_keys=set())
    source_iters = [
        _iter_batch_like_source(
            session,
            source_result,
            state,
            verbose=verbose,
            dry_run=dry_run,
            stop_after_consecutive_already_liked=stop_after_consecutive_already_liked,
            retry_attempts=retry_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
        )
        for source_result in result.source_results
    ]
    active = list(range(len(source_iters)))
    previous: int | None = None
    total_liked = 0
    try:
        while active:
            choices = [index for index in active if index != previous] or active
            index = rng.choice(choices)
            previous = index
            batch_size = rng.randint(min_batch_size, max_batch_size)
            if like_count is not None:
                batch_size = min(batch_size, like_count - total_liked)
            if verbose:
                print(
                    f"[batch-like] source={normalized_urls[index]} "
                    f"batch_size={batch_size} total_liked={total_liked}"
                )
            for _ in range(batch_size):
                try:
                    next(source_iters[index])
                except StopIteration:
                    active.remove(index)
                    reason = result.source_results[index].stop_reason
                    if reason in {"rate_limited", "authentication_failed"}:
                        result.stop_reason = reason
                        return result
                    break
                total_liked += 1
                if like_count is not None and total_liked >= like_count:
                    result.stop_reason = "like_count_reached"
                    return result
        result.stop_reason = "sources_exhausted"
        return result
    finally:
        for source_iter in source_iters:
            source_iter.close()


def _iter_batch_like_source(
    session: Any,
    result: BatchLikeResult,
    state: _BatchLikeState,
    *,
    verbose: bool,
    dry_run: bool,
    stop_after_consecutive_already_liked: int | None,
    retry_attempts: int,
    retry_backoff_seconds: float,
    sleep_after_attempts: int | None = None,
    sleep_time: float | None = None,
) -> Iterator[None]:
    """Process one source, yielding after each new like so it can be paused."""
    normalized_url = result.url
    source = result.source
    start_page, stop_page, like_count = result.start_page, result.stop_page, result.like_count
    interval, min_rating, max_rating = result.interval, result.min_rating, result.max_rating

    if source == "activity":
        page_iter = iter_activity_review_candidates(
            session,
            normalized_url,
            start_page=start_page,
            stop_page=stop_page,
            verbose=verbose,
            retry_attempts=retry_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
        )
    else:
        page_iter = iter_review_page_candidates(
            session,
            normalized_url,
            start_page=start_page,
            stop_page=stop_page,
            verbose=verbose,
            retry_attempts=retry_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
        )

    any_candidate = False
    consecutive_already_liked = 0

    def wait_for_like_interval() -> None:
        """Wait once between successful likes, never once per retry attempt."""

        if not _interval_enabled(interval) or state.last_like_succeeded_at is None:
            return
        elapsed = time.monotonic() - state.last_like_succeeded_at
        remaining = _sample_interval(interval) - elapsed
        if remaining > 0:
            if verbose:
                print(f"  waiting {remaining:.2f}s before next like request")
            time.sleep(remaining)

    try:
        for page_number, candidates in page_iter:
            result.pages_scanned += 1

            if verbose:
                print(f"[batch-like] page={page_number} candidates={len(candidates)}")

            for candidate in candidates:
                any_candidate = True
                result.candidates_seen += 1

                if _same_slug(candidate.owner, state.signed_in_user):
                    result.skipped_own_reviews += 1
                    consecutive_already_liked = 0
                    if verbose:
                        print(f"  skip own review: {candidate.review_url or candidate.viewing_uid}")
                    continue

                if min_rating is not None or max_rating is not None:
                    rating_reason = review_rating_filter_reason(
                        candidate.rating,
                        min_rating=min_rating,
                        max_rating=max_rating,
                        rating_known=True,
                    )
                    if rating_reason is not None:
                        consecutive_already_liked = 0
                        if rating_reason == "unrated":
                            result.skipped_unrated += 1
                        elif rating_reason == "rating_below_minimum":
                            result.skipped_below_min_rating += 1
                        elif rating_reason == "rating_above_maximum":
                            result.skipped_above_max_rating += 1
                        if verbose:
                            print(
                                "  skip rating filter: "
                                f"rating={candidate.rating!r} reason={rating_reason} "
                                f"target={candidate.review_url or candidate.viewing_uid}"
                            )
                        continue

                candidate_keys = (
                    _candidate_like_keys(candidate) if state.liked_keys is not None else set()
                )
                duplicate = (
                    state.liked_keys is not None
                    and not candidate_keys.isdisjoint(state.liked_keys)
                )
                if candidate.already_liked is True or duplicate:
                    if state.liked_keys is not None:
                        state.liked_keys.update(candidate_keys)
                    result.skipped_already_liked += 1
                    consecutive_already_liked += 1
                    if verbose:
                        print(f"  skip already liked: {candidate.review_url or candidate.viewing_uid}")
                    if (
                        stop_after_consecutive_already_liked is not None
                        and consecutive_already_liked >= stop_after_consecutive_already_liked
                    ):
                        result.stop_reason = "consecutive_already_liked_reached"
                        return
                    continue

                consecutive_already_liked = 0

                try:
                    target = candidate.target
                except ValueError:
                    result.skipped_missing_target += 1
                    if verbose:
                        print(f"  skip missing target: {candidate}")
                    continue

                if dry_run:
                    if verbose:
                        print(f"  dry-run would like: {target}")
                    result.like_attempts += 1
                    result.liked += 1
                else:
                    # Count the candidate-level attempt even when the HTTP action
                    # fails. The prior implementation only incremented this field
                    # after success, which produced misleading like_attempts=0
                    # alongside a recorded failure.
                    result.like_attempts += 1
                    try:
                        wait_for_like_interval()
                        _retry_call(
                            lambda candidate=candidate, target=target: _like_review(
                                session, target, referer=candidate.source_url
                            ),
                            retry_attempts=retry_attempts,
                            retry_backoff_seconds=retry_backoff_seconds,
                        )
                        state.last_like_succeeded_at = time.monotonic()
                        result.liked += 1
                        if verbose:
                            print(f"  liked: {target}")
                    except Exception as exc:  # noqa: BLE001 - classify before continuing
                        result.failures.append(
                            ReviewLikeFailure(
                                candidate=candidate,
                                page_number=page_number,
                                url=candidate.review_url,
                                error_type=type(exc).__name__,
                                error=str(exc),
                            )
                        )
                        if verbose:
                            print(f"  failed: {target}: {type(exc).__name__}: {exc}")
                        if _is_rate_limit_error(exc):
                            result.stop_reason = "rate_limited"
                            if verbose:
                                print("[batch-like] stopped: Letterboxd rate limit reached")
                            return
                        if _is_authentication_error(exc):
                            result.stop_reason = "authentication_failed"
                            if verbose:
                                print(
                                    "[batch-like] stopped: Letterboxd rejected the "
                                    "authenticated action request"
                                )
                            return
                        continue

                if state.liked_keys is not None:
                    state.liked_keys.update(candidate_keys)

                if like_count is not None and result.liked >= like_count:
                    result.stop_reason = "like_count_reached"

                yield None

                if result.stop_reason is not None:
                    return

                if (
                    not dry_run
                    and not _interval_enabled(interval)
                    and sleep_after_attempts is not None
                    and sleep_after_attempts > 0
                    and sleep_time is not None
                    and sleep_time > 0
                    and result.like_attempts % sleep_after_attempts == 0
                ):
                    time.sleep(sleep_time)

    except Exception as exc:  # noqa: BLE001 - return partial results with failure detail
        result.failures.append(
            ReviewLikeFailure(
                candidate=None,
                page_number=None,
                url=normalized_url,
                error_type=type(exc).__name__,
                error=str(exc),
            )
        )
        if _is_rate_limit_error(exc):
            result.stop_reason = "rate_limited"
            if verbose:
                print("[batch-like] stopped while fetching: Letterboxd rate limit reached")
        elif _is_authentication_error(exc):
            result.stop_reason = "authentication_failed"
            if verbose:
                print("[batch-like] stopped while fetching: authentication rejected")
        else:
            result.stop_reason = "no_more_pages" if any_candidate else "no_candidates"
        return
    finally:
        close = getattr(page_iter, "close", None)
        if close is not None:
            close()

    if stop_page is not None and result.pages_scanned >= max(0, stop_page - start_page + 1):
        result.stop_reason = "stop_page_reached"
    else:
        result.stop_reason = "no_more_pages" if any_candidate else "no_candidates"


def like_num_reviews(
    session: Any,
    *,
    count: int,
    url: str = DEFAULT_ACTIVITY_URL,
    start_page: int = 1,
    stop_page: int | None = None,
    verbose: bool = False,
    dry_run: bool = False,
    interval: Interval = 0.0,
    min_rating: float | None = None,
    max_rating: float | None = None,
    retry_attempts: int = 3,
    retry_backoff_seconds: float = 2.0,
) -> BatchLikeResult:
    """Create ``count`` new review likes, or stop when the source is exhausted."""
    if count < 0:
        raise ValueError("count must be >= 0")
    return batch_like_reviews(
        session,
        url=url,
        start_page=start_page,
        stop_page=stop_page,
        like_count=count,
        verbose=verbose,
        dry_run=dry_run,
        interval=interval,
        min_rating=min_rating,
        max_rating=max_rating,
        retry_attempts=retry_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
    )


def like_page_range_reviews(
    session: Any,
    *,
    url: str,
    start_page: int,
    stop_page: int,
    like_count: int | None = None,
    verbose: bool = False,
    dry_run: bool = False,
    interval: Interval = 0.0,
    min_rating: float | None = None,
    max_rating: float | None = None,
    retry_attempts: int = 3,
    retry_backoff_seconds: float = 2.0,
) -> BatchLikeResult:
    """Like eligible reviews from an inclusive page range."""
    return batch_like_reviews(
        session,
        url=url,
        start_page=start_page,
        stop_page=stop_page,
        like_count=like_count,
        verbose=verbose,
        dry_run=dry_run,
        interval=interval,
        min_rating=min_rating,
        max_rating=max_rating,
        retry_attempts=retry_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
    )


def like_until_liked_reviews(
    session: Any,
    *,
    url: str = DEFAULT_ACTIVITY_URL,
    start_page: int = 1,
    stop_page: int | None = None,
    consecutive_already_liked: int = 3,
    like_count: int | None = None,
    verbose: bool = False,
    dry_run: bool = False,
    interval: Interval = 0.0,
    min_rating: float | None = None,
    max_rating: float | None = None,
    retry_attempts: int = 3,
    retry_backoff_seconds: float = 2.0,
) -> BatchLikeResult:
    """Like reviews until a run of already-liked reviews suggests overlap.

    Both activity and review-page candidates have their current review-like state
    hydrated before processing.
    """
    if consecutive_already_liked < 1:
        raise ValueError("consecutive_already_liked must be >= 1")
    return batch_like_reviews(
        session,
        url=url,
        start_page=start_page,
        stop_page=stop_page,
        like_count=like_count,
        verbose=verbose,
        dry_run=dry_run,
        interval=interval,
        min_rating=min_rating,
        max_rating=max_rating,
        stop_after_consecutive_already_liked=consecutive_already_liked,
        retry_attempts=retry_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
    )


# ---------------------------------------------------------------------------
# Candidate iterators
# ---------------------------------------------------------------------------


def iter_activity_review_candidates(
    session: Any,
    url: str,
    *,
    start_page: int = 1,
    stop_page: int | None = None,
    verbose: bool = False,
    retry_attempts: int = 3,
    retry_backoff_seconds: float = 2.0,
) -> Iterator[tuple[int, list[ReviewLikeCandidate]]]:
    """Yield embedded/full ReviewActivity candidates page by page.

    Activity pagination is cursor-based. This version is deliberately defensive:

    - For signed-in /activity/, it uses the AJAX endpoint from page 1 so review
      like automation can apply the browser-observed review-ish filters.
    - For member activity URLs, it first tries the visible page. If that page
      contains real activity rows, those rows are page 1 and the AJAX cursor
      starts after the last visible row.
    - If the visible page is only a shell and contains no activity rows, page 1
      falls back to the AJAX endpoint with no ``after`` cursor.
    - Page 2+ always uses the AJAX endpoint with the last real row's
      ``data-activity-id`` as ``after``.

    Compact ReviewReferenceActivity rows are intentionally ignored. Only full
    embedded ReviewActivity rows are yielded as candidates.
    """

    from lboxdlib.fetchers.activity import activity_ajax_spec
    from lboxdlib.parsers.activity import ReviewActivity, extract_activity

    normalized_url = _normalize_url(url)
    spec = activity_ajax_spec(session, normalized_url)
    is_explicit_ajax = urlparse(normalized_url).path.startswith("/ajax/activity-pagination/")
    is_signed_in_friends_stream = getattr(spec, "mode", None) == "signed_in_friends_following"

    # Your captured browser request for /activity/ used a narrower filter set
    # than activity_page_handling.DEFAULT_FRIENDS_ACTIVITY_FILTERS. For review
    # liking, keep the AJAX request aligned with that observed activity stream
    # while preserving the CSRF value provided by activity_ajax_spec().
    base_params = _activity_like_base_params(spec)

    after: str | None = None
    current_page = 1

    while stop_page is None or current_page <= stop_page:
        html: str
        source_url: str
        rows: list[Any]

        # For the signed-in /activity/ friends stream, use the AJAX endpoint from
        # page 1 so review-like automation can apply the browser-observed filters.
        # For member activity pages, use the visible page first and then continue
        # with unfiltered AJAX pagination.
        use_ajax = is_explicit_ajax or is_signed_in_friends_stream or current_page > 1

        if not use_ajax:
            response = _retry_call(
                lambda: session.get(
                    normalized_url,
                    headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    },
                ),
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            response.raise_for_status()
            html = _response_text(response)
            source_url = normalized_url
            rows = _activity_rows_from_html(html)

            if verbose:
                print(f"[activity] fetched visible page={current_page} rows={len(rows)}")

            # Some activity pages are shells and contain no embedded rows until
            # the AJAX pagination request. Treat the first AJAX fragment as page
            # 1 instead of stopping with no candidates.
            if not rows:
                if verbose:
                    print("[activity] visible page had no rows; falling back to AJAX page 1")
                use_ajax = True

        if use_ajax:
            params = list(base_params)
            if after:
                params.insert(0, ("after", after))

            response = _retry_call(
                lambda params=params: session.get(
                    spec.ajax_url,
                    params=params,
                    headers={
                        "Accept": "text/html, */*; q=0.01",
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": spec.referer,
                    },
                ),
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            response.raise_for_status()
            html = _response_text(response)
            source_url = spec.ajax_url + ("?" + urlencode(params) if params else "")
            rows = _activity_rows_from_html(html)

            if verbose:
                print(f"[activity] fetched ajax page={current_page} mode={spec.mode} rows={len(rows)}")

        if not rows:
            break

        if current_page >= start_page:
            items = extract_activity(
                html,
                session=session,
                include_raw_html=False,
                hydrate_likes=True,
                strict_hydration=True,
                referer=normalized_url,
            )
            candidates: list[ReviewLikeCandidate] = []
            for item in items:
                if not isinstance(item, ReviewActivity):
                    continue
                candidates.append(
                    _candidate_from_activity(item, source_url=source_url, page_number=current_page)
                )
            yield current_page, candidates

        next_after = rows[-1].get("data-activity-id")
        next_after = str(next_after).strip() if next_after else None
        if not next_after or next_after == after:
            break

        after = next_after
        current_page += 1


def iter_review_page_candidates(
    session: Any,
    url: str,
    *,
    start_page: int = 1,
    stop_page: int | None = None,
    verbose: bool = False,
    retry_attempts: int = 3,
    retry_backoff_seconds: float = 2.0,
) -> Iterator[tuple[int, list[ReviewLikeCandidate]]]:
    """Yield review-page candidates with definitive signed-in like state.

    Each page's viewing identifiers are sent to Letterboxd's metadata endpoint
    before the candidates are yielded. A requested viewing returned in ``liked``
    is already liked; every requested viewing absent from ``liked`` is unliked.
    """

    from lboxdlib.fetchers.pages import (
        detect_pagination_type,
        fetch_cursored_pages,
        fetch_films_browser_csi_pages,
        fetch_standard_pages,
        parse_pagination,
    )
    from lboxdlib.parsers.reviews import extract_reviews

    normalized_url = _normalize_url(url)
    page_type, first_html = detect_pagination_type(session, normalized_url, verbose=verbose)

    if page_type == "single":
        if start_page <= 1 and (stop_page is None or stop_page >= 1):
            reviews = extract_reviews(first_html)
            candidates = [
                _candidate_from_review(r, source_url=normalized_url, page_number=1)
                for r in reviews
            ]
            _hydrate_review_candidate_like_states(
                session,
                candidates,
                referer=normalized_url,
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            yield 1, candidates
        return

    if page_type == "standard":
        current_page = start_page
        while stop_page is None or current_page <= stop_page:
            html_pages = _retry_call(
                lambda current_page=current_page: fetch_standard_pages(
                    session,
                    normalized_url,
                    start_page=current_page,
                    pages=1,
                    verbose=verbose,
                ),
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            if not html_pages:
                break

            html = html_pages[0]
            reviews = extract_reviews(html)
            candidates = [
                _candidate_from_review(r, source_url=normalized_url, page_number=current_page)
                for r in reviews
            ]
            _hydrate_review_candidate_like_states(
                session,
                candidates,
                referer=normalized_url,
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            )
            yield current_page, candidates

            info = parse_pagination(html)
            if info.is_last_page or not info.next_href:
                break
            current_page += 1
        return

    # Fallbacks for unusual review routes. These are finite page lists produced
    # by the existing page_handling implementation.
    page_limit = None if stop_page is None else max(0, stop_page - start_page + 1)
    if page_type == "cursor":
        html_pages = _retry_call(
            lambda: fetch_cursored_pages(session, normalized_url, pages=page_limit, verbose=verbose),
            retry_attempts=retry_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
        )
    elif page_type == "csi":
        html_pages = _retry_call(
            lambda: fetch_films_browser_csi_pages(
                session,
                normalized_url,
                first_html,
                start_page=start_page,
                pages=page_limit,
                verbose=verbose,
            ),
            retry_attempts=retry_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
        )
    else:
        raise ValueError(f"Unsupported pagination type for reviews: {page_type!r}")

    for offset, html in enumerate(html_pages, start=start_page):
        if stop_page is not None and offset > stop_page:
            break
        reviews = extract_reviews(html)
        candidates = [
            _candidate_from_review(r, source_url=normalized_url, page_number=offset)
            for r in reviews
        ]
        _hydrate_review_candidate_like_states(
            session,
            candidates,
            referer=normalized_url,
            retry_attempts=retry_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
        )
        yield offset, candidates


# Browser-observed filters for the signed-in /activity/ friends stream when
# the intent is review-like automation. This matches the captured request shape:
#   listComments=true&lists=true&reviewComments=true&reviews=true&storyComments=true&storyLikes=true&__csrf=...
ACTIVITY_LIKE_FILTERS: tuple[tuple[str, str], ...] = (
    ("listComments", "true"),
    ("lists", "true"),
    ("reviewComments", "true"),
    ("reviews", "true"),
    ("storyComments", "true"),
    ("storyLikes", "true"),
)


def _activity_like_base_params(spec: Any) -> tuple[tuple[str, str], ...]:
    """Return AJAX params for review-liking activity fetches.

    For the signed-in friends stream, prefer the browser-observed narrower
    filters. For member activity or explicit AJAX URLs, preserve the spec params
    produced by lboxdlib.fetchers.activity.
    """

    mode = getattr(spec, "mode", None)
    base_params = tuple(getattr(spec, "base_params", ()) or ())

    if mode != "signed_in_friends_following":
        return base_params

    csrf_params = tuple((k, v) for k, v in base_params if k == "__csrf")
    return ACTIVITY_LIKE_FILTERS + csrf_params


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def detect_source_kind(url: str) -> SourceKind:
    parsed = urlparse(_normalize_url(url))
    path = parsed.path.rstrip("/") + "/"

    if path in {"/activity/", "/activity/incoming/", "/activity/you/"}:
        return "activity"
    if path.startswith("/ajax/activity-pagination/"):
        return "activity"

    bits = [bit for bit in parsed.path.split("/") if bit]
    if len(bits) == 2 and bits[1] == "activity":
        return "activity"

    return "reviews"


def _candidate_from_activity(item: Any, *, source_url: str, page_number: int) -> ReviewLikeCandidate:
    film = getattr(item, "film", None)
    return ReviewLikeCandidate(
        source="activity",
        source_url=source_url,
        page_number=page_number,
        owner=getattr(item, "ownerSlug", None),
        film_slug=getattr(film, "slug", None) if film else None,
        review_url=getattr(item, "reviewUrl", None),
        viewing_uid=getattr(item, "viewingUid", None),
        viewing_id=getattr(item, "viewingId", None),
        rating=getattr(item, "rating", None),
        already_liked=getattr(item, "likedReviewByMe", None),
        raw_type=type(item).__name__,
    )


def _candidate_from_review(review: Any, *, source_url: str, page_number: int) -> ReviewLikeCandidate:
    return ReviewLikeCandidate(
        source="reviews",
        source_url=source_url,
        page_number=page_number,
        owner=getattr(review, "owner", None),
        film_slug=getattr(review, "filmSlug", None),
        review_url=getattr(review, "reviewUrl", None),
        viewing_uid=getattr(review, "viewingUid", None),
        viewing_id=getattr(review, "viewingId", None),
        rating=getattr(review, "rating", None),
        already_liked=getattr(review, "likedReviewByMe", None),
        raw_type=type(review).__name__,
    )


def _candidate_viewing_uid(
    session: Any,
    candidate: ReviewLikeCandidate,
    *,
    retry_attempts: int,
    retry_backoff_seconds: float,
) -> str | None:
    """Resolve and cache a candidate's canonical ``viewing:<id>`` identifier."""

    if candidate.viewing_uid:
        text = str(candidate.viewing_uid).strip()
        if text.startswith("viewing:") and text.split(":", 1)[1].isdigit():
            candidate.viewing_id = candidate.viewing_id or int(text.split(":", 1)[1])
            candidate.viewing_uid = text
            return text

    if candidate.viewing_id is not None:
        candidate.viewing_uid = f"viewing:{int(candidate.viewing_id)}"
        return candidate.viewing_uid

    if not candidate.review_url:
        return None

    viewing_id = _retry_call(
        lambda: get_viewing_id_from_review_url(session, _absolute_url(candidate.review_url)),
        retry_attempts=retry_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
    )
    candidate.viewing_id = int(viewing_id)
    candidate.viewing_uid = f"viewing:{candidate.viewing_id}"
    return candidate.viewing_uid


def _hydrate_review_candidate_like_states(
    session: Any,
    candidates: list[ReviewLikeCandidate],
    *,
    referer: str,
    retry_attempts: int,
    retry_backoff_seconds: float,
    batch_size: int = 100,
) -> None:
    """Populate ``already_liked`` for review-page candidates before yielding.

    Candidates without any resolvable target are left unknown so the normal
    ``skipped_missing_target`` path can handle them. Every resolvable review must
    receive a definite Boolean state; metadata errors are raised rather than
    risking that an existing like is counted as a new one.
    """

    unresolved: list[tuple[ReviewLikeCandidate, str]] = []
    for candidate in candidates:
        if candidate.already_liked is not None:
            continue
        uid = _candidate_viewing_uid(
            session,
            candidate,
            retry_attempts=retry_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
        )
        if uid is not None:
            unresolved.append((candidate, uid))

    if not unresolved:
        return

    states = _retry_call(
        lambda: get_review_like_states(
            session,
            [uid for _, uid in unresolved],
            referer=referer,
            batch_size=batch_size,
        ),
        retry_attempts=retry_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
    )

    for candidate, uid in unresolved:
        if uid not in states:
            raise RuntimeError(
                f"Review like state was not returned for {uid} ({candidate.review_url!r})"
            )
        candidate.already_liked = bool(states[uid])


def _like_review(session: Any, target: int | str, *, referer: str) -> Any:
    """Call the canonical action-layer implementation."""
    return like_review(session, target, referer=_canonical_action_referer(referer))


def _signed_in_user(session: Any) -> str | None:
    try:
        from lboxdlib.auth import get_signed_in_user

        return get_signed_in_user(session)
    except Exception:
        return None


def _same_slug(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return left.strip("/").lower() == right.strip("/").lower()


def _normalize_url(url: str) -> str:
    text = (url or DEFAULT_ACTIVITY_URL).strip()
    if not text:
        text = DEFAULT_ACTIVITY_URL
    return urljoin(LETTERBOXD_BASE, text)


def _normalize_source_urls(urls: Iterable[str]) -> list[str]:
    if isinstance(urls, (str, bytes)):
        raise ValueError("urls must be a nonempty iterable of URL strings, not a single string")
    try:
        values = iter(urls)
    except TypeError as exc:
        raise ValueError("urls must be a nonempty iterable of URL strings") from exc
    normalized: dict[str, None] = {}
    for url in values:
        if not isinstance(url, str) or not url.strip():
            raise ValueError("urls must contain only nonblank URL strings")
        normalized[_normalize_url(url)] = None
    if not normalized:
        raise ValueError("urls must contain at least one URL")
    return list(normalized)


def _validate_batch_sizes(min_batch_size: int, max_batch_size: int) -> None:
    for name, value in (("min_batch_size", min_batch_size), ("max_batch_size", max_batch_size)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be an integer >= 1")
    if max_batch_size < min_batch_size:
        raise ValueError("max_batch_size must be >= min_batch_size")


def _candidate_like_keys(candidate: ReviewLikeCandidate) -> set[str]:
    """Match overlapping candidates by viewing identity or canonical review URL."""
    keys: set[str] = set()
    if candidate.viewing_id is not None:
        keys.add(f"viewing:{candidate.viewing_id}")
    if candidate.viewing_uid:
        keys.add(candidate.viewing_uid)
    if candidate.review_url:
        parsed = urlparse(_absolute_url(candidate.review_url))
        keys.add(parsed._replace(query="", fragment="").geturl().rstrip("/"))
    return keys


def _absolute_url(url: str) -> str:
    return urljoin(LETTERBOXD_BASE, url)


def _response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if text is not None:
        return text
    content = getattr(response, "content", b"")
    return content.decode("utf-8", "replace")


def _is_rate_limit_error(exc: BaseException) -> bool:
    """Delegate confirmed rate-limit classification to the action layer."""
    return bool(is_rate_limit_error(exc))


def _is_authentication_error(exc: BaseException) -> bool:
    """Recognize Letterboxd action responses that reject the signed-in state."""

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = str(current).lower()
        if "must.be.logged.in" in text or "you must be signed in" in text:
            return True
        if "missing a spam-protection token" in text:
            return True
        current = current.__cause__ or current.__context__
    return False


def _canonical_action_referer(referer: str) -> str:
    """Return a visible Letterboxd page URL suitable for action POSTs.

    Activity candidates are fetched from ``/ajax/activity-pagination/...``.
    Passing that internal fragment URL (including its ``__csrf`` query value) as
    the action Referer is both unnecessary and unlike browser traffic. Normalize
    it back to the visible activity page while preserving ordinary review-page
    referers.
    """

    normalized = _normalize_url(referer)
    parsed = urlparse(normalized)
    if parsed.path.startswith("/ajax/activity-pagination/"):
        return DEFAULT_ACTIVITY_URL
    return normalized


def _activity_rows_from_html(html: str) -> list[Any]:
    soup = BeautifulSoup(html or "", "lxml")
    return list(soup.select("section.activity-row[data-activity-id]"))


def _validate_page_range(*, start_page: int, stop_page: int | None, like_count: int | None) -> None:
    for name, value in (("start_page", start_page), ("stop_page", stop_page), ("like_count", like_count)):
        if value is None and name != "start_page":
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be an integer" + (" or None" if name != "start_page" else ""))
    if start_page < 1:
        raise ValueError("start_page must be >= 1")
    if stop_page is not None and stop_page < start_page:
        raise ValueError("stop_page must be None or >= start_page")
    if like_count is not None and like_count < 0:
        raise ValueError("like_count must be None or >= 0")


def _validate_interval(interval: Interval) -> Interval:
    """Normalize a fixed interval or an inclusive (minimum, maximum) range."""

    if isinstance(interval, (tuple, list)):
        if len(interval) != 2:
            raise ValueError("interval range must contain exactly two numbers")
        low, high = (_validate_interval_value(value) for value in interval)
        if low > high:
            raise ValueError("interval range minimum must be <= maximum")
        return (low, high)
    return _validate_interval_value(interval)


def _validate_interval_value(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("interval must contain finite numbers >= 0")
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("interval must contain finite numbers >= 0") from exc
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError("interval must contain finite numbers >= 0")
    return normalized


def _interval_enabled(interval: Interval) -> bool:
    return (interval[1] if isinstance(interval, tuple) else interval) > 0


def _sample_interval(interval: Interval) -> float:
    return random.uniform(*interval) if isinstance(interval, tuple) else interval


def _validate_legacy_sleep_options(
    *,
    interval: Interval,
    sleep_after_attempts: int | None,
    sleep_time: float | None,
) -> None:
    """Reject ambiguous combinations of canonical and legacy timing controls."""

    legacy_used = sleep_after_attempts is not None or sleep_time is not None
    if not legacy_used:
        return

    if _interval_enabled(interval):
        raise ValueError(
            "interval cannot be combined with sleep_after_attempts or sleep_time"
        )
    if sleep_after_attempts is None or sleep_time is None:
        raise ValueError(
            "sleep_after_attempts and sleep_time must be supplied together"
        )
    if sleep_after_attempts < 1:
        raise ValueError("sleep_after_attempts must be >= 1")
    if not math.isfinite(float(sleep_time)) or sleep_time < 0:
        raise ValueError("sleep_time must be a finite number >= 0")


def _retry_call(
    fn,
    *,
    retry_attempts: int,
    retry_backoff_seconds: float,
):
    if isinstance(retry_attempts, bool) or not isinstance(retry_attempts, int):
        raise ValueError("retry_attempts must be an integer >= 1")
    if retry_attempts < 1:
        raise ValueError("retry_attempts must be >= 1")
    attempts = retry_attempts
    last_exc: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            result = fn()
            status = getattr(result, "status_code", None)
            if status in HANDLED_HTTP_ERROR_STATUSES:
                try:
                    result.raise_for_status()
                except Exception as exc:  # noqa: BLE001
                    raise exc
            return result
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if _is_rate_limit_error(exc) or _is_authentication_error(exc):
                raise
            status = getattr(getattr(exc, "response", None), "status_code", None)
            text = str(exc)
            retryable = status in RETRYABLE_HTTP_STATUSES or any(
                marker in text for marker in ("403", "503")
            )
            if not retryable or attempt >= attempts:
                raise
            if retry_backoff_seconds > 0:
                time.sleep(retry_backoff_seconds * attempt)

    if last_exc:
        raise last_exc
    raise RuntimeError("retry_call failed without an exception")


__all__ = [
    "BatchLikeResult",
    "MultiSourceBatchLikeResult",
    "ReviewLikeCandidate",
    "ReviewLikeFailure",
    "batch_like_reviews",
    "batch_like_reviews_from_urls",
    "like_num_reviews",
    "like_page_range_reviews",
    "like_until_liked_reviews",
    "detect_source_kind",
    "iter_activity_review_candidates",
    "iter_review_page_candidates",
]

