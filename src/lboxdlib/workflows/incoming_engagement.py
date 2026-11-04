"""
Incoming-activity engagement report for Letterboxd.

Purpose:
    Fetch all members the signed-in user follows, then scan the signed-in
    user's incoming activity stream and count how often each followed member
    appears as the actor in incoming activity.

Primary public entry point:
    incoming_engagement_report(session, pages_stop=-1, ...)

Uses the canonical lboxdlib auth and fetcher APIs plus a standard
requests-like session object.

Notes:
    The visible incoming page is fetched first:
        https://letterboxd.com/activity/incoming/

    Further pages are fetched from Letterboxd's activity AJAX pagination route.
    The expected route is:
        /ajax/activity-pagination/{viewer}/incoming/

    A fallback route is included for safety:
        /ajax/activity-pagination/{viewer}/
        with incomingActivity=true

    Counts are actor-based: each activity row contributes one count to the
    user represented by the row's actor/avatar/summary link.
"""

from __future__ import annotations

import csv
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Iterator, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag


BASE_URL = "https://letterboxd.com"
INCOMING_ACTIVITY_URL = f"{BASE_URL}/activity/incoming/"
RETRY_STATUSES = {403, 429, 503}


@dataclass(slots=True)
class IncomingActivityActor:
    """One actor occurrence parsed from an incoming activity row."""

    slug: str
    display_name: str | None = None
    url: str | None = None
    activity_id: str | None = None
    activity_kind: str | None = None
    activity_text: str | None = None
    datetime: str | None = None


@dataclass(slots=True)
class FollowedUserIncomingCount:
    """Incoming-activity count for one user the viewer follows."""

    slug: str
    display_name: str | None = None
    url: str | None = None
    incoming_count: int = 0
    follows_viewer: bool | None = None
    followed_by_viewer: bool | None = True
    followers_count: int | None = None
    following_count: int | None = None

    @property
    def has_incoming_activity(self) -> bool:
        return self.incoming_count > 0


@dataclass(slots=True)
class IncomingEngagementReport:
    """Result object returned by incoming_engagement_report()."""

    viewer: str
    following_url: str
    incoming_url: str = INCOMING_ACTIVITY_URL
    following_pages_stop: int = -1
    incoming_pages_stop: int = -1

    following_count: int = 0
    incoming_pages_scanned: int = 0
    incoming_rows_seen: int = 0
    incoming_actor_occurrences: int = 0
    followed_actor_occurrences: int = 0

    users: list[FollowedUserIncomingCount] = field(default_factory=list)
    non_engagers: list[FollowedUserIncomingCount] = field(default_factory=list)
    unknown_or_external_actors: dict[str, int] = field(default_factory=dict)
    fetch_failures: list[str] = field(default_factory=list)
    stop_reason: str | None = None

    def sorted_users(self, *, reverse: bool = True) -> list[FollowedUserIncomingCount]:
        return sorted(
            self.users,
            key=lambda u: (u.incoming_count, (u.display_name or u.slug).lower()),
            reverse=reverse,
        )

    def sorted_non_engagers(self) -> list[FollowedUserIncomingCount]:
        return sorted(self.non_engagers, key=lambda u: (u.display_name or u.slug).lower())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def write_csv(self, path: str, *, include_zeroes: bool = True) -> None:
        rows = self.sorted_users(reverse=True)
        if not include_zeroes:
            rows = [row for row in rows if row.incoming_count > 0]

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "slug",
                    "display_name",
                    "url",
                    "incoming_count",
                    "follows_viewer",
                    "followed_by_viewer",
                    "followers_count",
                    "following_count",
                ],
            )
            writer.writeheader()
            for row in rows:
                writer.writerow(asdict(row))

    def summary(self) -> str:
        return (
            f"viewer={self.viewer} following={self.following_count} "
            f"incoming_pages={self.incoming_pages_scanned} "
            f"incoming_rows={self.incoming_rows_seen} "
            f"followed_actor_occurrences={self.followed_actor_occurrences} "
            f"non_engagers={len(self.non_engagers)} stop_reason={self.stop_reason}"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def incoming_engagement_report(
    session: Any,
    *,
    pages_stop: int = -1,
    following_pages_stop: int = -1,
    start_page: int = 1,
    retry_attempts: int = 3,
    retry_backoff_seconds: float = 2.0,
    verbose: bool = False,
) -> IncomingEngagementReport:
    """Count incoming activity appearances for users the signed-in user follows.

    Args:
        session:
            Signed-in curl_cffi / requests-like Letterboxd session.
        pages_stop:
            Number of incoming activity pages/fragments to scan. ``-1`` means
            continue until there are no more incoming activity rows.
        following_pages_stop:
            Number of following pages to fetch. ``-1`` means all pages.
        start_page:
            Incoming activity page number to begin counting from. Normally 1.
            The first visible page is page 1; AJAX fragments begin after that.
        retry_attempts:
            Attempts for transient 403/429/503 responses.
        retry_backoff_seconds:
            Base sleep between retry attempts. Delay scales by attempt count.
        verbose:
            Print progress.

    Returns:
        IncomingEngagementReport
    """

    if pages_stop < -1:
        raise ValueError("pages_stop must be -1 or >= 0")
    if following_pages_stop < -1:
        raise ValueError("following_pages_stop must be -1 or >= 0")
    if start_page < 1:
        raise ValueError("start_page must be >= 1")

    viewer = _get_signed_in_user(session)
    following_url = f"{BASE_URL}/{viewer}/following/"

    following_pages = None if following_pages_stop == -1 else following_pages_stop
    incoming_pages = None if pages_stop == -1 else pages_stop

    followed_users = _fetch_following_users(
        session,
        following_url,
        pages=following_pages,
        verbose=verbose,
    )

    followed_by_slug: dict[str, Any] = {}
    for user in followed_users:
        slug = _normal_slug(getattr(user, "slug", None) or getattr(user, "name", None))
        if not slug:
            continue
        followed_by_slug[slug.lower()] = user

    report = IncomingEngagementReport(
        viewer=viewer,
        following_url=following_url,
        following_pages_stop=following_pages_stop,
        incoming_pages_stop=pages_stop,
        following_count=len(followed_by_slug),
    )

    counts: dict[str, int] = {slug: 0 for slug in followed_by_slug}
    external_counts: dict[str, int] = {}

    if pages_stop == 0:
        report.stop_reason = "pages_stop_reached"
        report.users = _build_count_rows(followed_by_slug, counts)
        report.non_engagers = [row for row in report.users if row.incoming_count == 0]
        return report

    for page_number, html in iter_incoming_activity_html_pages(
        session,
        viewer=viewer,
        start_page=start_page,
        pages=incoming_pages,
        retry_attempts=retry_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
        verbose=verbose,
        failures=report.fetch_failures,
    ):
        actors = extract_incoming_activity_actors(html)
        rows_seen = _activity_row_count(html)

        report.incoming_pages_scanned += 1
        report.incoming_rows_seen += rows_seen
        report.incoming_actor_occurrences += len(actors)

        if verbose:
            print(f"[incoming] page={page_number} rows={rows_seen} actors={len(actors)}")

        for actor in actors:
            key = actor.slug.lower()
            if key in counts:
                counts[key] += 1
                report.followed_actor_occurrences += 1
            else:
                external_counts[key] = external_counts.get(key, 0) + 1

    report.unknown_or_external_actors = dict(sorted(external_counts.items(), key=lambda kv: (-kv[1], kv[0])))
    report.users = _build_count_rows(followed_by_slug, counts)
    report.non_engagers = [row for row in report.users if row.incoming_count == 0]

    if report.fetch_failures:
        report.stop_reason = "fetch_error"
    elif pages_stop != -1 and report.incoming_pages_scanned >= pages_stop:
        report.stop_reason = "pages_stop_reached"
    else:
        report.stop_reason = "no_more_pages"

    return report


# Friendly aliases.
count_incoming_engagement = incoming_engagement_report
following_incoming_engagement = incoming_engagement_report


# ---------------------------------------------------------------------------
# Incoming activity fetching
# ---------------------------------------------------------------------------


def iter_incoming_activity_html_pages(
    session: Any,
    *,
    viewer: str,
    start_page: int = 1,
    pages: Optional[int] = None,
    retry_attempts: int = 3,
    retry_backoff_seconds: float = 2.0,
    verbose: bool = False,
    failures: list[str] | None = None,
) -> Iterator[tuple[int, str]]:
    """Yield incoming activity HTML pages/fragments.

    Page 1 is the visible page. Further pages use the activity AJAX cursor.
    """

    fetched = 0
    current_page = 1

    visible_html = _request_text_with_retries(
        session,
        INCOMING_ACTIVITY_URL,
        headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": BASE_URL + "/",
        },
        retry_attempts=retry_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
        verbose=verbose,
    )

    rows = _activity_rows_from_html(visible_html)
    if current_page >= start_page:
        yield current_page, visible_html
        fetched += 1

    if pages is not None and fetched >= pages:
        return

    after = _last_activity_id(rows)
    if not after:
        return

    endpoint_modes = ["incoming_path", "incoming_param"]
    chosen_mode: str | None = None

    while True:
        current_page += 1
        if pages is not None and fetched >= pages:
            return

        html: str | None = None
        last_error: Exception | None = None

        modes_to_try = [chosen_mode] if chosen_mode else endpoint_modes
        for mode in modes_to_try:
            if mode is None:
                continue
            ajax_url, params = _incoming_ajax_request(viewer, after, mode=mode)
            try:
                html = _request_text_with_retries(
                    session,
                    ajax_url,
                    params=params,
                    headers={
                        "Accept": "text/html, */*; q=0.01",
                        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": INCOMING_ACTIVITY_URL,
                    },
                    retry_attempts=retry_attempts,
                    retry_backoff_seconds=retry_backoff_seconds,
                    verbose=verbose,
                )
                chosen_mode = mode
                break
            except Exception as exc:  # noqa: BLE001 - try fallback endpoint
                last_error = exc
                if verbose:
                    print(f"[incoming] ajax mode={mode} failed: {type(exc).__name__}: {exc}")
                continue

        if html is None:
            msg = f"incoming activity ajax failed after={after}: {type(last_error).__name__}: {last_error}"
            if failures is not None:
                failures.append(msg)
            if verbose:
                print(f"[incoming] stopping: {msg}")
            return

        rows = _activity_rows_from_html(html)
        if verbose:
            print(f"[incoming] ajax page={current_page} mode={chosen_mode} rows={len(rows)} after={after}")

        if not rows:
            return

        if current_page >= start_page:
            yield current_page, html
            fetched += 1

        next_after = _last_activity_id(rows)
        if not next_after or next_after == after:
            return
        after = next_after


def _incoming_ajax_request(viewer: str, after: str, *, mode: str) -> tuple[str, list[tuple[str, str]]]:
    csrf = _current_csrf_placeholder()

    if mode == "incoming_path":
        url = f"{BASE_URL}/ajax/activity-pagination/{viewer}/incoming/"
        params = [("after", after)]
    elif mode == "incoming_param":
        url = f"{BASE_URL}/ajax/activity-pagination/{viewer}/"
        params = [("after", after), ("incomingActivity", "true")]
    else:
        raise ValueError(f"Unsupported incoming ajax mode: {mode!r}")

    # The real CSRF is filled in right before request because some Letterboxd
    # responses rotate the token. This placeholder is replaced by caller helper.
    params.append(("__csrf", csrf))
    return url, params


def _current_csrf_placeholder() -> str:
    return "__LB_CSRF_PLACEHOLDER__"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def extract_incoming_activity_actors(html: str) -> list[IncomingActivityActor]:
    """Extract one actor per incoming activity row.

    The actor is the member represented by the row's avatar/name link. Rows
    without a member slug are ignored.
    """

    actors: list[IncomingActivityActor] = []

    for row in _activity_rows_from_html(html):
        actor = _actor_from_activity_row(row)
        if actor:
            actors.append(actor)

    return actors


def _actor_from_activity_row(row: Tag) -> IncomingActivityActor | None:
    avatar_link = row.select_one(".table-activity-user a.avatar[href]")
    avatar_img = avatar_link.select_one("img[alt]") if avatar_link else None

    summary_link = row.select_one(".activity-summary a.name[href]")
    if summary_link is None:
        summary_link = row.select_one(".attribution-detail a.owner[href]")

    href = _attr(summary_link, "href") or _attr(avatar_link, "href")
    slug = _member_slug_from_href(href)
    if not slug:
        return None

    display_name = (
        _clean_text(summary_link)
        or _attr(avatar_img, "alt")
        or slug
    )

    return IncomingActivityActor(
        slug=slug,
        display_name=display_name,
        url=_absolute_or_none(href),
        activity_id=_attr(row, "data-activity-id"),
        activity_kind=_activity_kind_from_row(row),
        activity_text=_clean_text(row) or None,
        datetime=_attr(row.select_one("time[datetime]"), "datetime"),
    )


def _activity_kind_from_row(row: Tag) -> str | None:
    classes = {str(c).strip() for c in row.get("class", [])}
    for cls in sorted(classes):
        if cls.startswith("-"):
            return cls.lstrip("-")
    return None


def _activity_rows_from_html(html: str) -> list[Tag]:
    soup = BeautifulSoup(html or "", "lxml")
    return list(soup.select("section.activity-row[data-activity-id]"))


def _activity_row_count(html: str) -> int:
    return len(_activity_rows_from_html(html))


def _last_activity_id(rows: list[Tag]) -> str | None:
    if not rows:
        return None
    value = _attr(rows[-1], "data-activity-id")
    return value.strip() if value else None


# ---------------------------------------------------------------------------
# Followed users / result shaping
# ---------------------------------------------------------------------------


def _fetch_following_users(session: Any, url: str, *, pages: Optional[int], verbose: bool) -> list[Any]:
    from lboxdlib.fetchers.users import fetch_users

    return list(fetch_users(session, url, pages=pages, verbose=verbose))


def _build_count_rows(
    followed_by_slug: dict[str, Any],
    counts: dict[str, int],
) -> list[FollowedUserIncomingCount]:
    rows: list[FollowedUserIncomingCount] = []

    for slug_key, user in followed_by_slug.items():
        slug = _normal_slug(getattr(user, "slug", None) or slug_key) or slug_key
        rows.append(
            FollowedUserIncomingCount(
                slug=slug,
                display_name=getattr(user, "display_name", None) or getattr(user, "name", None) or slug,
                url=getattr(user, "url", None) or f"{BASE_URL}/{slug}/",
                incoming_count=counts.get(slug_key, 0),
                follows_viewer=getattr(user, "follows_viewer", None),
                followed_by_viewer=getattr(user, "followed_by_viewer", True),
                followers_count=getattr(user, "followers_count", None),
                following_count=getattr(user, "following_count", None),
            )
        )

    rows.sort(key=lambda u: (-u.incoming_count, (u.display_name or u.slug).lower()))
    return rows


# ---------------------------------------------------------------------------
# HTTP / low-level helpers
# ---------------------------------------------------------------------------


def _request_text_with_retries(
    session: Any,
    url: str,
    *,
    params: list[tuple[str, str]] | None = None,
    headers: dict[str, str] | None = None,
    retry_attempts: int = 3,
    retry_backoff_seconds: float = 2.0,
    verbose: bool = False,
) -> str:
    params = _fill_csrf_params(session, params or [])

    attempts = max(1, retry_attempts)
    last_exc: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = session.get(url, params=params, headers=headers or {})
            status = getattr(response, "status_code", None)

            if status in RETRY_STATUSES and attempt < attempts:
                if verbose:
                    print(f"[incoming] transient HTTP {status}; retry {attempt}/{attempts}: {url}")
                time.sleep(retry_backoff_seconds * attempt)
                continue

            if hasattr(response, "raise_for_status"):
                response.raise_for_status()

            text = getattr(response, "text", None)
            if text is not None:
                return text
            content = getattr(response, "content", b"")
            return content.decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < attempts:
                if verbose:
                    print(f"[incoming] request failed; retry {attempt}/{attempts}: {type(exc).__name__}: {exc}")
                time.sleep(retry_backoff_seconds * attempt)
                continue
            raise

    assert last_exc is not None
    raise last_exc


def _fill_csrf_params(session: Any, params: list[tuple[str, str]]) -> list[tuple[str, str]]:
    if not params:
        return params

    csrf_needed = any(value == _current_csrf_placeholder() for _, value in params)
    if not csrf_needed:
        return params

    csrf = _get_csrf(session)
    return [(key, csrf if value == _current_csrf_placeholder() else value) for key, value in params]


def _get_signed_in_user(session: Any) -> str:
    from lboxdlib.auth import get_signed_in_user

    viewer = get_signed_in_user(session)
    if not viewer:
        raise RuntimeError("Could not determine signed-in Letterboxd user from session")
    return str(viewer).strip().strip("/")


def _get_csrf(session: Any) -> str:
    from lboxdlib.auth import get_csrf

    csrf = get_csrf(session)
    if not csrf:
        raise RuntimeError("Could not determine Letterboxd CSRF token from session")
    return str(csrf)


def _member_slug_from_href(href: str | None) -> str | None:
    bits = _path_bits(href)
    if not bits:
        return None

    reserved = {
        "about",
        "activity",
        "crew",
        "films",
        "film",
        "journal",
        "list",
        "lists",
        "members",
        "search",
        "settings",
        "sign-in",
        "user",
    }
    first = bits[0].strip()
    if not first or first.lower() in reserved:
        return None
    return first


def _normal_slug(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("http://") or text.startswith("https://") or text.startswith("/"):
        return _member_slug_from_href(text)
    return text.strip("/")


def _path_bits(href: str | None) -> list[str]:
    if not href:
        return []
    return [bit for bit in urlparse(href).path.split("/") if bit]


def _absolute_or_none(href: str | None) -> str | None:
    if not href:
        return None
    return urljoin(BASE_URL, href)


def _attr(node: Tag | BeautifulSoup | None, attr: str) -> str | None:
    if not node:
        return None
    value = node.get(attr)
    if value is None:
        return None
    return str(value).strip() or None


def _clean_text(node: Tag | BeautifulSoup | None) -> str:
    if not node:
        return ""
    return " ".join(node.get_text(" ", strip=True).split())


__all__ = [
    "IncomingActivityActor",
    "FollowedUserIncomingCount",
    "IncomingEngagementReport",
    "extract_incoming_activity_actors",
    "incoming_engagement_report",
    "count_incoming_engagement",
    "following_incoming_engagement",
]
