"""Session-backed relationship checks for Letterboxd users."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

from lboxdlib.parsers.users import extract_users

try:
    from lboxdlib.fetchers.pages import ensure_page, parse_pagination, raise_for_status_with_preview
except ImportError:  # pragma: no cover
    ensure_page = None  # type: ignore[assignment]
    parse_pagination = None  # type: ignore[assignment]
    raise_for_status_with_preview = None  # type: ignore[assignment]

try:
    from lboxdlib.auth import get_signed_in_user
except ImportError:  # pragma: no cover
    get_signed_in_user = None  # type: ignore[assignment]


BASE_URL = "https://letterboxd.com"
TRANSIENT_STATUS_CODES = {403, 429, 503}


@dataclass(slots=True)
class RelationshipCheckResult:
    subject_slug: str
    target_slug: str
    relation: str
    value: bool | None
    pages_scanned: int
    stopped_reason: str



def user_follows(
    session: Any,
    user: Any,
    target: Any,
    *,
    pages: int | None = None,
    verbose: bool = False,
    retry_attempts: int = 3,
    retry_backoff_seconds: float = 2.0,
) -> bool | None:
    """Return whether ``user`` follows ``target``.

    ``user`` and ``target`` may be slugs, profile URLs/paths, or UserSummary-like
    objects exposing ``.slug`` and/or ``.url``.

    If ``pages`` is None, scan until the end of ``user``'s following pages. If a
    positive page limit is reached before finding ``target`` and before reaching
    the final page, return None to indicate an unknown result.
    """

    result = user_follows_slug(
        session,
        user,
        resolve_user_slug(target),
        pages_stop=-1 if pages is None else pages,
        verbose=verbose,
        retry_attempts=retry_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
    )
    return result.value


def user_follows_signed_in_user(
    session: Any,
    user: Any,
    *,
    pages: int | None = None,
    verbose: bool = False,
    retry_attempts: int = 3,
    retry_backoff_seconds: float = 2.0,
) -> bool | None:
    """Return whether ``user`` follows the signed-in Letterboxd user.

    This is a clearer public alias for the older ``user_follows_viewer`` helper.
    """

    return user_follows_viewer(
        session,
        user,
        pages_stop=-1 if pages is None else pages,
        verbose=verbose,
        retry_attempts=retry_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
    )

def user_follows_viewer(
    session: Any,
    user: Any,
    *,
    viewer_slug: str | None = None,
    pages_stop: int = -1,
    verbose: bool = False,
    retry_attempts: int = 3,
    retry_backoff_seconds: float = 2.0,
) -> bool | None:
    """Return whether ``user`` follows the signed-in viewer.

    This scans ``/{user}/following/`` for the signed-in viewer. If ``pages_stop``
    is -1, it scans until the end and returns True/False. If a positive page
    limit is reached without finding the viewer while a next page still exists,
    it returns None because the answer is still unknown.
    """

    subject_slug = resolve_user_slug(user)
    target_slug = viewer_slug or _signed_in_user(session)
    if not target_slug:
        raise ValueError("viewer_slug is required when signed-in user cannot be determined")

    result = user_follows_slug(
        session,
        subject_slug,
        target_slug,
        pages_stop=pages_stop,
        verbose=verbose,
        retry_attempts=retry_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
    )
    return result.value


def user_follows_slug(
    session: Any,
    user: Any,
    target_slug: str,
    *,
    pages_stop: int = -1,
    verbose: bool = False,
    retry_attempts: int = 3,
    retry_backoff_seconds: float = 2.0,
) -> RelationshipCheckResult:
    """Check whether ``user`` follows ``target_slug`` by scanning following pages."""

    subject_slug = resolve_user_slug(user)
    target_slug = resolve_user_slug(target_slug)

    following_url = f"{BASE_URL}/{subject_slug}/following/"
    max_pages = None if pages_stop == -1 else max(0, pages_stop)

    if max_pages == 0:
        return RelationshipCheckResult(
            subject_slug=subject_slug,
            target_slug=target_slug,
            relation="follows",
            value=None,
            pages_scanned=0,
            stopped_reason="page_limit",
        )

    page = 1
    pages_scanned = 0

    while True:
        if max_pages is not None and pages_scanned >= max_pages:
            return RelationshipCheckResult(
                subject_slug=subject_slug,
                target_slug=target_slug,
                relation="follows",
                value=None,
                pages_scanned=pages_scanned,
                stopped_reason="page_limit",
            )

        html = _fetch_standard_page_with_retry(
            session,
            following_url,
            page=page,
            verbose=verbose,
            retry_attempts=retry_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
        )
        pages_scanned += 1

        users = extract_users(html, source_url=following_url)
        if verbose:
            print(f"[relationship] {subject_slug}/following page={page} users={len(users)}")

        if any(_same_slug(u.slug, target_slug) for u in users):
            return RelationshipCheckResult(
                subject_slug=subject_slug,
                target_slug=target_slug,
                relation="follows",
                value=True,
                pages_scanned=pages_scanned,
                stopped_reason="found",
            )

        info = parse_pagination(html) if parse_pagination else None
        if not info or info.is_last_page or not info.next_href:
            return RelationshipCheckResult(
                subject_slug=subject_slug,
                target_slug=target_slug,
                relation="follows",
                value=False,
                pages_scanned=pages_scanned,
                stopped_reason="end",
            )

        page += 1


def resolve_user_slug(value: Any) -> str:
    """Resolve a slug from a UserSummary-like object, path, URL, or raw slug."""

    slug = getattr(value, "slug", None)
    if slug:
        return str(slug).strip().strip("/")

    url = getattr(value, "url", None)
    if url:
        value = url

    text = str(value or "").strip()
    if not text:
        raise ValueError("empty user slug")

    if "/" not in text and not text.startswith("http"):
        return text.strip("/")

    parsed = urlparse(urljoin(BASE_URL, text))
    bits = [bit for bit in parsed.path.split("/") if bit]
    if not bits:
        raise ValueError(f"Could not resolve user slug from {value!r}")

    reserved = {
        "about",
        "activity",
        "crew",
        "films",
        "journal",
        "lists",
        "members",
        "search",
        "settings",
        "sign-in",
        "user",
    }
    if bits[0] in reserved:
        raise ValueError(f"Not a Letterboxd member URL/slug: {value!r}")

    return bits[0]


def _signed_in_user(session: Any) -> str | None:
    if get_signed_in_user is None:
        return None
    user = get_signed_in_user(session)
    return str(user).strip() if user else None


def _fetch_standard_page_with_retry(
    session: Any,
    base_url: str,
    *,
    page: int,
    verbose: bool,
    retry_attempts: int,
    retry_backoff_seconds: float,
) -> str:
    if ensure_page is None:
        url = f"{base_url.rstrip('/')}/page/{page}/"
    else:
        url = ensure_page(base_url, page=page)

    attempts = max(1, retry_attempts)
    last_exc: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            if verbose:
                print(f"[relationship] fetching page {page}: {url}")

            response = session.get(url)
            status = getattr(response, "status_code", None)

            if status in TRANSIENT_STATUS_CODES and attempt < attempts:
                if retry_backoff_seconds > 0:
                    time.sleep(retry_backoff_seconds * attempt)
                continue

            if raise_for_status_with_preview is not None:
                raise_for_status_with_preview(response, context=f"relationship page {page}", verbose=verbose)
            elif hasattr(response, "raise_for_status"):
                response.raise_for_status()

            return getattr(response, "text", "") or ""

        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < attempts:
                if retry_backoff_seconds > 0:
                    time.sleep(retry_backoff_seconds * attempt)
                continue
            raise

    raise RuntimeError(f"Failed to fetch relationship page: {last_exc}")


def _same_slug(left: str | None, right: str | None) -> bool:
    return bool(left and right and left.strip("/").lower() == right.strip("/").lower())


__all__ = [
    "RelationshipCheckResult",
    "user_follows",
    "user_follows_signed_in_user",
    "user_follows_viewer",
    "user_follows_slug",
    "resolve_user_slug",
]
