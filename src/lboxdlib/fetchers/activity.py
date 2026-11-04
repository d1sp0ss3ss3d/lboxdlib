"""
Activity feed fetcher for Letterboxd activity pages and AJAX fragments.

Important boundary:
    Server-side activity filters are only valid for the signed-in Friends
    activity stream at:

        https://letterboxd.com/activity/

    Member activity pages such as:

        https://letterboxd.com/{member}/activity/

    should be fetched without activity filter params. Those pages can contain
    mixed row families: reviews, diary/rating rows, review references, follows,
    list activity, watchlist activity, and other activity rows that the parser
    should preserve as typed items or BasicActivity.

Common use:
    fetch_activities(session, "https://letterboxd.com/activity/")
    fetch_activities(session, "https://letterboxd.com/abandonchimp/activity/")

Filtered signed-in stream examples:
    fetch_activities(
        session,
        "https://letterboxd.com/activity/",
        activity_filter_preset="reviews",
    )

    fetch_review_and_diary_activity(session, "https://letterboxd.com/activity/")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Iterable, Mapping, Optional, Sequence
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from lboxdlib.auth import get_signed_in_user
from lboxdlib.parsers.activity import (
    ActivityActor,
    ActivityItem,
    ReviewReferenceActivity,
    extract_activity,
    hydrate_review_likes,
)


LETTERBOXD_BASE = "https://letterboxd.com"


# Order is kept close to the observed /activity/ filter form/request shape.
# These are only applied to the signed-in Friends stream at /activity/.
DEFAULT_FRIENDS_ACTIVITY_FILTERS: tuple[tuple[str, str], ...] = (
    ("diaryEntries", "true"),
    ("follows", "true"),
    ("incomingActivity", "true"),
    ("listComments", "true"),
    ("listLikes", "true"),
    ("lists", "true"),
    ("reviewComments", "true"),
    ("reviewLikes", "true"),
    ("reviews", "true"),
    ("stories", "true"),
    ("storyComments", "true"),
    ("storyLikes", "true"),
    ("watchlistAdditions", "true"),
    ("yourActivity", "true"),
)

# Same followed-member action families as the full Friends feed, but excludes
# activity available from Letterboxd's cheaper dedicated personal and incoming
# streams. This is useful for timing and for callers that merge those streams.
FRIENDS_ONLY_ACTIVITY_FILTERS: tuple[tuple[str, str], ...] = tuple(
    pair
    for pair in DEFAULT_FRIENDS_ACTIVITY_FILTERS
    if pair[0] not in {"incomingActivity", "yourActivity"}
)

# Browser-observed review-ish payload. It still includes comments/story/list rows,
# so it is not appropriate for rating-only extraction, but it is useful for
# reproducing that browser feed shape.
BROWSER_REVIEWISH_ACTIVITY_FILTERS: tuple[tuple[str, str], ...] = (
    ("listComments", "true"),
    ("lists", "true"),
    ("reviewComments", "true"),
    ("reviews", "true"),
    ("storyComments", "true"),
    ("storyLikes", "true"),
)

# For rating discovery, use only the row families that should create embedded
# viewing/review activity rows. Deliberately excludes reviewLikes/reviewComments
# so compact references like "X liked Y's review" are not requested.
DIARY_AND_REVIEW_ACTIVITY_FILTERS: tuple[tuple[str, str], ...] = (
    ("diaryEntries", "true"),
    ("reviews", "true"),
)

REVIEW_ONLY_ACTIVITY_FILTERS: tuple[tuple[str, str], ...] = (("reviews", "true"),)
DIARY_ONLY_ACTIVITY_FILTERS: tuple[tuple[str, str], ...] = (("diaryEntries", "true"),)

ACTIVITY_FILTER_PRESETS: dict[str, tuple[tuple[str, str], ...]] = {
    "all": DEFAULT_FRIENDS_ACTIVITY_FILTERS,
    "friends_only": FRIENDS_ONLY_ACTIVITY_FILTERS,
    "following_only": FRIENDS_ONLY_ACTIVITY_FILTERS,
    "browser_reviewish": BROWSER_REVIEWISH_ACTIVITY_FILTERS,
    "diary_reviews": DIARY_AND_REVIEW_ACTIVITY_FILTERS,
    "reviews_diary": DIARY_AND_REVIEW_ACTIVITY_FILTERS,
    "review_diary": DIARY_AND_REVIEW_ACTIVITY_FILTERS,
    "reviews": REVIEW_ONLY_ACTIVITY_FILTERS,
    "review": REVIEW_ONLY_ACTIVITY_FILTERS,
    "review_only": REVIEW_ONLY_ACTIVITY_FILTERS,
    "diary": DIARY_ONLY_ACTIVITY_FILTERS,
    "diary_entries": DIARY_ONLY_ACTIVITY_FILTERS,
    "diary_only": DIARY_ONLY_ACTIVITY_FILTERS,
    "none": (),
}

ActivityFilters = (
    str
    | Mapping[str, bool | str | int]
    | Sequence[str]
    | Sequence[tuple[str, bool | str | int]]
    | None
)


@dataclass(frozen=True, slots=True)
class ActivityAjaxSpec:
    ajax_url: str
    referer: str
    base_params: tuple[tuple[str, str], ...]
    mode: str
    initial_after: str | None = None
    prefer_ajax_first_page: bool = False


@dataclass(slots=True)
class ReviewLiker:
    """One member who liked at least one matching review activity row.

    ``likeCount`` counts matching incoming activity occurrences, not the
    member's lifetime likes. Lists preserve newest-first activity order and
    omit duplicate values.
    """

    slug: str | None
    displayName: str | None
    url: str | None = None
    likeCount: int = 0
    activityIds: list[str] = field(default_factory=list)
    reviewUrls: list[str] = field(default_factory=list)
    filmNames: list[str] = field(default_factory=list)
    filmSlugs: list[str] = field(default_factory=list)
    newestDatetime: str | None = None
    oldestDatetime: str | None = None


def activity_filter_params(
    activity_filters: ActivityFilters = None,
    *,
    preset: str = "all",
) -> tuple[tuple[str, str], ...]:
    """Normalize activity filters into Letterboxd query params.

    Args:
        activity_filters:
            - None: use ``preset``.
            - str: treated as a preset name.
            - mapping: {"reviews": True, "reviewLikes": False}; truthy values
              become ``name=true`` and falsey values are omitted.
            - sequence[str]: each string becomes ``name=true``.
            - sequence[tuple[str, value]]: truthy values are emitted.
        preset:
            One of ``all``, ``diary_reviews``, ``reviews``, ``diary``,
            ``browser_reviewish``, or ``none``.
    """

    if isinstance(activity_filters, str):
        preset = activity_filters
        activity_filters = None

    if activity_filters is None:
        key = (preset or "all").strip().lower()
        if key not in ACTIVITY_FILTER_PRESETS:
            known = ", ".join(sorted(ACTIVITY_FILTER_PRESETS))
            raise ValueError(f"Unknown activity filter preset {preset!r}; known presets: {known}")
        return ACTIVITY_FILTER_PRESETS[key]

    if isinstance(activity_filters, Mapping):
        return tuple(
            (str(name), _activity_filter_value(value))
            for name, value in activity_filters.items()
            if _activity_filter_enabled(value)
        )

    out: list[tuple[str, str]] = []
    for item in activity_filters:
        if isinstance(item, tuple):
            if len(item) != 2:
                raise ValueError(f"Activity filter tuple must be (name, value), got {item!r}")
            name, value = item
            if _activity_filter_enabled(value):
                out.append((str(name), _activity_filter_value(value)))
        else:
            out.append((str(item), "true"))
    return tuple(out)


def activity_ajax_spec(
    session: Any,
    url: str,
    *,
    activity_filters: ActivityFilters = None,
    activity_filter_preset: str = "all",
    include_all_friends_filters: bool | None = None,
) -> ActivityAjaxSpec:
    """Return the AJAX request contract for a Letterboxd activity URL.

    Supported visible routes:
        /activity/
            Signed-in Friends stream. Uses ``/{viewer}/following/`` and is the
            only route on which activity filter query parameters are valid.

        /activity/incoming/
            Signed-in incoming stream. Uses ``/{viewer}/incoming/`` without
            Friends activity-filter query parameters.

        /activity/you/
            Signed-in personal stream. Uses the member-style ``/{viewer}/``
            AJAX endpoint without Friends activity-filter query parameters.

        /{member}/activity/
            Public/member activity page. Uses ``/{member}/`` without filters.

        /ajax/activity-pagination/{...}/
            Explicit AJAX route. Existing non-filter parameters are preserved
            and ``after`` is separated as the initial cursor. Browser-observed
            activity GETs do not require a ``__csrf`` query parameter.
    """

    if include_all_friends_filters is not None and activity_filters is None:
        activity_filter_preset = "all" if include_all_friends_filters else "none"

    parsed = urlparse(urljoin(LETTERBOXD_BASE, url))
    path = parsed.path
    bits = [bit for bit in path.split("/") if bit]

    if path.startswith("/ajax/activity-pagination/"):
        ajax_url = urljoin(LETTERBOXD_BASE, path)
        member_slug = bits[2] if len(bits) >= 3 else None
        stream = bits[3].lower() if len(bits) >= 4 else None
        is_following_ajax = stream == "following"

        params: list[tuple[str, str]] = []
        initial_after: str | None = None
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            if not key:
                continue
            if key == "after":
                initial_after = value.strip() or None
                continue
            if key == "__csrf":
                # Browser-observed activity pagination GETs do not send this.
                continue
            if not is_following_ajax and key in _known_filter_names():
                continue
            params.append((key, value))

        if stream == "following":
            referer = f"{LETTERBOXD_BASE}/activity/"
            mode = "explicit_ajax_following"
        elif stream == "incoming":
            referer = f"{LETTERBOXD_BASE}/activity/incoming/"
            mode = "explicit_ajax_incoming"
        elif stream == "you" and member_slug:
            # Older callers may still supply the previously assumed /you/ AJAX
            # route. Letterboxd actually uses the member-style endpoint.
            ajax_url = f"{LETTERBOXD_BASE}/ajax/activity-pagination/{member_slug}/"
            referer = f"{LETTERBOXD_BASE}/activity/you/"
            mode = "explicit_ajax_you"
        elif member_slug:
            referer = f"{LETTERBOXD_BASE}/{member_slug}/activity/"
            mode = "explicit_ajax_member_activity"
        else:
            referer = f"{LETTERBOXD_BASE}/activity/"
            mode = "explicit_ajax"

        return ActivityAjaxSpec(
            ajax_url=ajax_url,
            referer=referer,
            base_params=tuple(params),
            mode=mode,
            initial_after=initial_after,
            prefer_ajax_first_page=True,
        )

    if bits == ["activity"]:
        viewer = get_signed_in_user(session)
        if not viewer:
            raise RuntimeError("Could not determine signed-in Letterboxd user for /activity/.")

        params = list(activity_filter_params(activity_filters, preset=activity_filter_preset))

        return ActivityAjaxSpec(
            ajax_url=f"{LETTERBOXD_BASE}/ajax/activity-pagination/{viewer}/following/",
            referer=f"{LETTERBOXD_BASE}/activity/",
            base_params=tuple(params),
            mode="signed_in_friends_following",
            prefer_ajax_first_page=True,
        )

    if bits == ["activity", "incoming"]:
        viewer = get_signed_in_user(session)
        if not viewer:
            raise RuntimeError(
                "Could not determine signed-in Letterboxd user for /activity/incoming/."
            )

        return ActivityAjaxSpec(
            ajax_url=f"{LETTERBOXD_BASE}/ajax/activity-pagination/{viewer}/incoming/",
            referer=f"{LETTERBOXD_BASE}/activity/incoming/",
            base_params=(),
            mode="signed_in_incoming",
            prefer_ajax_first_page=True,
        )

    if bits == ["activity", "you"]:
        viewer = get_signed_in_user(session)
        if not viewer:
            raise RuntimeError(
                "Could not determine signed-in Letterboxd user for /activity/you/."
            )

        return ActivityAjaxSpec(
            ajax_url=f"{LETTERBOXD_BASE}/ajax/activity-pagination/{viewer}/",
            referer=f"{LETTERBOXD_BASE}/activity/you/",
            base_params=(),
            mode="signed_in_you",
            prefer_ajax_first_page=True,
        )

    if len(bits) == 2 and bits[1] == "activity":
        member_slug = bits[0]
        return ActivityAjaxSpec(
            ajax_url=f"{LETTERBOXD_BASE}/ajax/activity-pagination/{member_slug}/",
            referer=f"{LETTERBOXD_BASE}/{member_slug}/activity/",
            base_params=(),
            mode="member_activity",
        )

    raise ValueError(f"Unsupported Letterboxd activity URL shape: {url!r}")


def fetch_activity_html_pages(
    session: Any,
    url: str,
    *,
    start: int = 1,
    pages: Optional[int] = 20,
    verbose: bool = False,
    activity_filters: ActivityFilters = None,
    activity_filter_preset: str = "all",
    include_all_friends_filters: bool | None = None,
    request_timeout: float | None = 30.0,
) -> list[str]:
    """Fetch Letterboxd activity pages/fragments with activity-aware pagination.

    ``pages`` is the number of returned pages beginning at ``start``. Earlier
    pages are still fetched when necessary to obtain the ``after`` cursor.

    Signed-in Friends, incoming, and personal streams use their browser-
    observed AJAX-first contracts. Personal activity uses the member-style
    ``/{viewer}/`` endpoint; incoming uses ``/{viewer}/incoming/``. Only the
    Friends stream accepts activity-filter query parameters.

    ``request_timeout`` is passed to each HTTP GET. Use ``None`` to retain the
    session implementation's unlimited/default timeout behavior.
    """

    if start < 1:
        raise ValueError("start must be >= 1")
    if pages is not None and pages < 0:
        raise ValueError("pages must be None or >= 0")
    if pages == 0:
        return []

    normalized_url = urljoin(LETTERBOXD_BASE, url)
    is_explicit_ajax = _is_explicit_activity_ajax_url(normalized_url)
    is_signed_in_activity = _is_signed_in_activity_url(normalized_url)
    custom_filters_requested = is_signed_in_activity and _has_requested_custom_filters(
        activity_filters=activity_filters,
        activity_filter_preset=activity_filter_preset,
        include_all_friends_filters=include_all_friends_filters,
    )

    spec = activity_ajax_spec(
        session,
        normalized_url,
        activity_filters=activity_filters,
        activity_filter_preset=activity_filter_preset,
        include_all_friends_filters=include_all_friends_filters,
    )

    visible_html: str | None = None
    visible_rows: list[Any] = []

    # Only routes that are not known to be AJAX-first need their visible page
    # inspected for server-rendered rows or a discovered load-more endpoint.
    if (
        not is_explicit_ajax
        and not custom_filters_requested
        and not spec.prefer_ajax_first_page
    ):
        request_started = perf_counter()
        response = session.get(
            normalized_url,
            timeout=request_timeout,
        )
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()

        visible_html = _response_text(response)
        visible_rows = _activity_rows_from_html(visible_html)

        discovered_ajax_url = _activity_ajax_url_from_html(visible_html)
        if discovered_ajax_url:
            spec = _spec_with_discovered_ajax_url(spec, discovered_ajax_url)

        if verbose:
            elapsed = perf_counter() - request_started
            print(
                f"[activity] visible mode={spec.mode} rows={len(visible_rows)} "
                f"seconds={elapsed:.3f} url={normalized_url} ajax={spec.ajax_url}"
            )

    html_pages: list[str] = []
    after = spec.initial_after
    current_page = 1

    while pages is None or len(html_pages) < pages:
        html: str
        rows: list[Any]
        source_url: str

        use_visible_page = (
            current_page == 1
            and visible_html is not None
            and bool(visible_rows)
            and not spec.prefer_ajax_first_page
        )

        if use_visible_page:
            html = visible_html
            rows = visible_rows
            source_url = normalized_url
        else:
            params = _activity_ajax_request_params(spec, after=after)
            request_started = perf_counter()
            response = session.get(
                spec.ajax_url,
                params=params,
                timeout=request_timeout,
            )
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()

            html = _response_text(response)
            rows = _activity_rows_from_html(html)
            source_url = spec.ajax_url + ("?" + urlencode(params) if params else "")

            discovered_ajax_url = _activity_ajax_url_from_html(html)
            if discovered_ajax_url:
                spec = _spec_with_discovered_ajax_url(spec, discovered_ajax_url)

            # A saved/rendered page can already contain rows even when the live
            # route normally loads page 1 through AJAX. Retain it as a fallback
            # if the first AJAX response unexpectedly contains no activity.
            if not rows and current_page == 1 and visible_rows:
                html = visible_html or ""
                rows = visible_rows
                source_url = normalized_url

        if verbose:
            elapsed_text = "visible"
            if not use_visible_page:
                elapsed_text = f"{perf_counter() - request_started:.3f}"
            print(
                f"[activity] page={current_page} mode={spec.mode} "
                f"rows={len(rows)} seconds={elapsed_text} url={source_url}"
            )

        if not rows:
            break

        if current_page >= start:
            html_pages.append(html)

        next_after = rows[-1].get("data-activity-id")
        next_after = str(next_after).strip() if next_after else None
        if not next_after or next_after == after:
            break

        after = next_after
        current_page += 1

    return html_pages


def fetch_activities(
    session: Any,
    url: str,
    *,
    start: int = 1,
    pages: Optional[int] = 20,
    verbose: bool = False,
    include_raw_html: bool = False,
    hydrate_likes: bool = True,
    strict_hydration: bool = True,
    metadata_batch_size: int = 100,
    activity_filters: ActivityFilters = None,
    activity_filter_preset: str = "all",
    include_all_friends_filters: bool | None = None,
    request_timeout: float | None = 30.0,
) -> list[ActivityItem]:
    """Fetch, parse, and optionally hydrate Letterboxd activity rows."""

    html_pages = fetch_activity_html_pages(
        session,
        url,
        start=start,
        pages=pages,
        verbose=verbose,
        activity_filters=activity_filters,
        activity_filter_preset=activity_filter_preset,
        include_all_friends_filters=include_all_friends_filters,
        request_timeout=request_timeout,
    )

    items: list[ActivityItem] = []

    for page_number, html in enumerate(html_pages, start=start):
        page_items = extract_activity(
            html,
            session=session,
            include_raw_html=include_raw_html,
            hydrate_likes=False,
            strict_hydration=strict_hydration,
            metadata_batch_size=metadata_batch_size,
            referer=url,
        )
        if verbose:
            counts = _type_counts(page_items)
            print(f"[activity] parsed page={page_number} counts={counts}")
        items.extend(page_items)

    if hydrate_likes:
        hydrate_review_likes(
            session,
            items,
            strict=strict_hydration,
            batch_size=metadata_batch_size,
            referer=url,
        )

    return items


def fetch_activity(
    session: Any,
    url: str,
    *,
    start: int = 1,
    pages: Optional[int] = 20,
    verbose: bool = False,
    include_raw_html: bool = False,
    hydrate_likes: bool = True,
    strict_hydration: bool = True,
    metadata_batch_size: int = 100,
    activity_filters: ActivityFilters = None,
    activity_filter_preset: str = "all",
    include_all_friends_filters: bool | None = None,
    request_timeout: float | None = 30.0,
) -> list[ActivityItem]:
    """Singular-name alias for :func:`fetch_activities`.

    New code should use ``fetch_activities`` because the function returns a
    collection and follows the naming convention used by ``fetch_films``,
    ``fetch_reviews``, and ``fetch_users``.
    """

    return fetch_activities(
        session,
        url,
        start=start,
        pages=pages,
        verbose=verbose,
        include_raw_html=include_raw_html,
        hydrate_likes=hydrate_likes,
        strict_hydration=strict_hydration,
        metadata_batch_size=metadata_batch_size,
        activity_filters=activity_filters,
        activity_filter_preset=activity_filter_preset,
        include_all_friends_filters=include_all_friends_filters,
        request_timeout=request_timeout,
    )


def review_like_events(
    items: Iterable[ActivityItem],
    *,
    review_owner_slug: str | None = None,
) -> list[ReviewReferenceActivity]:
    """Return compact activity rows representing likes of reviews.

    ``review_owner_slug`` can restrict results to reviews owned by one member.
    Matching is case-insensitive. The returned rows remain in feed order.
    """

    owner_key = _normalized_slug_key(review_owner_slug)
    matches: list[ReviewReferenceActivity] = []

    for item in items:
        if not isinstance(item, ReviewReferenceActivity):
            continue
        if item.action != "liked_review_reference":
            continue
        if owner_key is not None and _normalized_slug_key(item.reviewOwnerSlug) != owner_key:
            continue
        matches.append(item)

    return matches


def summarize_review_likers(
    items: Iterable[ActivityItem],
    *,
    review_owner_slug: str | None = None,
) -> list[ReviewLiker]:
    """Aggregate review-like activity into one newest-first row per actor."""

    by_actor: dict[str, ReviewLiker] = {}
    ordered_keys: list[str] = []

    for item in review_like_events(items, review_owner_slug=review_owner_slug):
        actor = item.actor
        if actor is None:
            continue

        key = _activity_actor_key(actor)
        if key is None:
            continue

        liker = by_actor.get(key)
        if liker is None:
            liker = ReviewLiker(
                slug=actor.slug,
                displayName=actor.displayName,
                url=actor.url,
                newestDatetime=item.datetime,
                oldestDatetime=item.datetime,
            )
            by_actor[key] = liker
            ordered_keys.append(key)

        liker.likeCount += 1
        liker.oldestDatetime = item.datetime or liker.oldestDatetime
        _append_unique(liker.activityIds, item.activityId)
        _append_unique(liker.reviewUrls, item.reviewUrl)
        if item.film is not None:
            _append_unique(liker.filmNames, item.film.name)
            _append_unique(liker.filmSlugs, item.film.slug)

    return [by_actor[key] for key in ordered_keys]


def fetch_review_like_events(
    session: Any,
    url: str = "https://letterboxd.com/activity/incoming/",
    *,
    start: int = 1,
    pages: Optional[int] = 20,
    verbose: bool = False,
    only_own_reviews: bool = True,
    include_raw_html: bool = False,
) -> list[ReviewReferenceActivity]:
    """Fetch review-like events from an activity stream.

    The default scans the signed-in incoming stream. With
    ``only_own_reviews=True`` (the default), compact likes are retained only
    when the review URL identifies the signed-in member as its owner.
    """

    items = fetch_activities(
        session,
        url,
        start=start,
        pages=pages,
        verbose=verbose,
        include_raw_html=include_raw_html,
        hydrate_likes=False,
        strict_hydration=False,
    )
    owner_slug = get_signed_in_user(session) if only_own_reviews else None
    return review_like_events(items, review_owner_slug=owner_slug)


def fetch_review_likers(
    session: Any,
    url: str = "https://letterboxd.com/activity/incoming/",
    *,
    start: int = 1,
    pages: Optional[int] = 20,
    verbose: bool = False,
    only_own_reviews: bool = True,
) -> list[ReviewLiker]:
    """Return unique members who liked matching reviews over ``pages`` pages."""

    events = fetch_review_like_events(
        session,
        url,
        start=start,
        pages=pages,
        verbose=verbose,
        only_own_reviews=only_own_reviews,
    )
    return summarize_review_likers(events)


def fetch_review_liker_names(
    session: Any,
    url: str = "https://letterboxd.com/activity/incoming/",
    *,
    start: int = 1,
    pages: Optional[int] = 20,
    verbose: bool = False,
    only_own_reviews: bool = True,
) -> list[str]:
    """Return unique review-liker display names in newest-first feed order."""

    names: list[str] = []
    for liker in fetch_review_likers(
        session,
        url,
        start=start,
        pages=pages,
        verbose=verbose,
        only_own_reviews=only_own_reviews,
    ):
        name = liker.displayName or liker.slug
        if name:
            names.append(name)
    return names


def fetch_review_activity_html_pages(
    session: Any,
    url: str = "https://letterboxd.com/activity/",
    *,
    start: int = 1,
    pages: Optional[int] = 20,
    verbose: bool = False,
) -> list[str]:
    """Fetch review-only activity fragments for the signed-in /activity/ stream.

    For member activity URLs, server-side filters are not applied; use
    ``fetch_activities`` and filter parsed ReviewActivity items client-side if
    needed.
    """
    return fetch_activity_html_pages(
        session,
        url,
        start=start,
        pages=pages,
        verbose=verbose,
        activity_filter_preset="review_only",
    )


def fetch_review_activity(
    session: Any,
    url: str = "https://letterboxd.com/activity/",
    *,
    start: int = 1,
    pages: Optional[int] = 20,
    verbose: bool = False,
    include_raw_html: bool = False,
    hydrate_likes: bool = False,
    strict_hydration: bool = False,
    metadata_batch_size: int = 100,
) -> list[ActivityItem]:
    """Fetch and parse review-only activity for the signed-in /activity/ stream."""
    return fetch_activities(
        session,
        url,
        start=start,
        pages=pages,
        verbose=verbose,
        include_raw_html=include_raw_html,
        hydrate_likes=hydrate_likes,
        strict_hydration=strict_hydration,
        metadata_batch_size=metadata_batch_size,
        activity_filter_preset="review_only",
    )


def fetch_review_and_diary_activity_html_pages(
    session: Any,
    url: str = "https://letterboxd.com/activity/",
    *,
    start: int = 1,
    pages: Optional[int] = 20,
    verbose: bool = False,
) -> list[str]:
    """Fetch diary-entry and review activity for the signed-in /activity/ stream.

    For member activity URLs, server-side filters are not applied.
    """
    return fetch_activity_html_pages(
        session,
        url,
        start=start,
        pages=pages,
        verbose=verbose,
        activity_filter_preset="diary_reviews",
    )


def fetch_review_and_diary_activity(
    session: Any,
    url: str = "https://letterboxd.com/activity/",
    *,
    start: int = 1,
    pages: Optional[int] = 20,
    verbose: bool = False,
    include_raw_html: bool = False,
    hydrate_likes: bool = False,
    strict_hydration: bool = False,
    metadata_batch_size: int = 100,
) -> list[ActivityItem]:
    """Fetch and parse diary-entry and review activity rows.

    On the signed-in /activity/ stream this uses the ``diary_reviews`` server
    filter. On member activity pages, server-side filters are not applied and
    mixed row types are expected.
    """
    return fetch_activities(
        session,
        url,
        start=start,
        pages=pages,
        verbose=verbose,
        include_raw_html=include_raw_html,
        hydrate_likes=hydrate_likes,
        strict_hydration=strict_hydration,
        metadata_batch_size=metadata_batch_size,
        activity_filter_preset="diary_reviews",
    )



def _activity_ajax_url_from_html(html: str) -> str | None:
    soup = BeautifulSoup(html or "", "lxml")
    node = soup.select_one('[data-url*="/ajax/activity-pagination/"]')
    if node is None:
        return None
    value = node.get("data-url")
    if value is None:
        return None
    text = str(value).strip()
    return urljoin(LETTERBOXD_BASE, text) if text else None


def _spec_with_discovered_ajax_url(
    spec: ActivityAjaxSpec,
    discovered_url: str,
) -> ActivityAjaxSpec:
    parsed = urlparse(urljoin(LETTERBOXD_BASE, discovered_url))
    if not parsed.path.startswith("/ajax/activity-pagination/"):
        return spec

    existing = [(key, value) for key, value in spec.base_params if key not in {"after", "__csrf"}]
    discovered: list[tuple[str, str]] = []
    initial_after = spec.initial_after

    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key == "after":
            initial_after = value.strip() or initial_after
        elif key != "__csrf":
            discovered.append((key, value))

    merged: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pair in [*discovered, *existing]:
        if pair not in seen:
            merged.append(pair)
            seen.add(pair)
    return ActivityAjaxSpec(
        ajax_url=urljoin(LETTERBOXD_BASE, parsed.path),
        referer=spec.referer,
        base_params=tuple(merged),
        mode=spec.mode,
        initial_after=initial_after,
        prefer_ajax_first_page=spec.prefer_ajax_first_page,
    )


def _activity_ajax_request_params(
    spec: ActivityAjaxSpec,
    *,
    after: str | None,
) -> list[tuple[str, str]]:
    params: list[tuple[str, str]] = []
    if after:
        params.append(("after", after))

    params.extend(
        (key, value)
        for key, value in spec.base_params
        if key not in {"after", "__csrf"}
    )
    return params


def _normalized_slug_key(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip().strip("/")
    return text.lower() if text else None


def _activity_actor_key(actor: ActivityActor) -> str | None:
    slug = _normalized_slug_key(actor.slug)
    if slug:
        return f"slug:{slug}"
    if actor.url:
        return f"url:{actor.url.strip().lower()}"
    if actor.displayName:
        return f"label:{actor.displayName.strip().lower()}"
    return None


def _append_unique(values: list[str], value: str | None) -> None:
    if value and value not in values:
        values.append(value)


def _response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if text is not None:
        return text
    content = getattr(response, "content", b"")
    return content.decode("utf-8", "replace")


def _activity_rows_from_html(html: str) -> list[Any]:
    soup = BeautifulSoup(html or "", "lxml")
    return list(soup.select("section.activity-row[data-activity-id]"))


def _type_counts(items: Iterable[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        name = type(item).__name__
        counts[name] = counts.get(name, 0) + 1
    return counts


def _activity_filter_enabled(value: bool | str | int) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off", "none"}
    return bool(value)


def _activity_filter_value(value: bool | str | int) -> str:
    if isinstance(value, str):
        text = value.strip()
        return text if text else "true"
    return "true" if bool(value) else "false"


def _known_filter_names() -> set[str]:
    names: set[str] = set()
    for preset in ACTIVITY_FILTER_PRESETS.values():
        names.update(name for name, _ in preset)
    return names


def _normalized_activity_path(url: str) -> str:
    parsed = urlparse(urljoin(LETTERBOXD_BASE, url))
    return parsed.path.rstrip("/") + "/"


def _is_signed_in_activity_url(url: str) -> bool:
    return _normalized_activity_path(url) == "/activity/"


def _is_explicit_activity_ajax_url(url: str) -> bool:
    parsed = urlparse(urljoin(LETTERBOXD_BASE, url))
    return parsed.path.startswith("/ajax/activity-pagination/")


def _has_requested_custom_filters(
    *,
    activity_filters: ActivityFilters,
    activity_filter_preset: str,
    include_all_friends_filters: bool | None,
) -> bool:
    if activity_filters is not None:
        return True
    if include_all_friends_filters is not None:
        return True
    return (activity_filter_preset or "all").strip().lower() not in {"", "all"}


__all__ = [
    "ActivityAjaxSpec",
    "ActivityFilters",
    "ReviewLiker",
    "ACTIVITY_FILTER_PRESETS",
    "DEFAULT_FRIENDS_ACTIVITY_FILTERS",
    "FRIENDS_ONLY_ACTIVITY_FILTERS",
    "BROWSER_REVIEWISH_ACTIVITY_FILTERS",
    "DIARY_AND_REVIEW_ACTIVITY_FILTERS",
    "REVIEW_ONLY_ACTIVITY_FILTERS",
    "DIARY_ONLY_ACTIVITY_FILTERS",
    "activity_filter_params",
    "activity_ajax_spec",
    "fetch_activity_html_pages",
    "fetch_activities",
    "fetch_activity",
    "review_like_events",
    "summarize_review_likers",
    "fetch_review_like_events",
    "fetch_review_likers",
    "fetch_review_liker_names",
    "fetch_review_activity_html_pages",
    "fetch_review_activity",
    "fetch_review_and_diary_activity_html_pages",
    "fetch_review_and_diary_activity",
]