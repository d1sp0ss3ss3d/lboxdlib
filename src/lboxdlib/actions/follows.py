"""Letterboxd follow and unfollow requests.

The functions accept a member slug, a profile URL/path, or an object with
``slug`` and/or ``url`` attributes. They require an authenticated session.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from urllib.parse import urljoin, urlparse

from lboxdlib.auth import get_csrf


BASE_URL = "https://letterboxd.com"
TRANSIENT_STATUS_CODES = {403, 429, 503}
FollowAction = Literal["follow", "unfollow"]


class HasUserIdentity(Protocol):
    slug: str | None
    url: str | None


FollowTarget = str | HasUserIdentity


@dataclass(slots=True)
class FollowableUser:
    slug: str
    url: str


@dataclass(slots=True)
class FollowResult:
    member_slug: str
    action: FollowAction
    changed: bool
    desired_state: bool
    # The requested state, not an independently verified current state.
    following: bool
    message: str | None = None
    error_codes: list[str] = field(default_factory=list)
    error_fields: list[str] = field(default_factory=list)
    csrf: str | None = None
    raw: dict[str, Any] | None = None


@dataclass(slots=True)
class FollowFailure:
    member_slug: str | None
    action: FollowAction
    error: str
    target: str | None = None


def _slug_from_string(value: str) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    if "/" not in text and not text.startswith("http"):
        return text.strip("/") or None

    path = urlparse(urljoin(BASE_URL, text)).path
    parts = [part for part in path.split("/") if part]
    reserved = {
        "about", "activity", "crew", "films", "journal", "lists", "members",
        "search", "settings", "sign-in", "user",
    }
    if not parts or parts[0] in reserved:
        return None
    return parts[0]


def resolve_followable_user(target: FollowTarget) -> FollowableUser:
    """Get the slug and profile URL from a string or user-like object."""
    if isinstance(target, str):
        slug = _slug_from_string(target)
        if not slug:
            raise ValueError(f"Could not resolve Letterboxd member slug from {target!r}")
        return FollowableUser(slug=slug, url=f"{BASE_URL}/{slug}/")

    slug = getattr(target, "slug", None)
    url = getattr(target, "url", None)
    if not slug and url:
        slug = _slug_from_string(url)
    if not slug:
        raise ValueError(
            "Could not resolve Letterboxd member slug from target. "
            "Pass a slug, profile URL/path, or object with .slug."
        )

    if not url:
        url = f"{BASE_URL}/{slug}/"
    else:
        url = urljoin(BASE_URL, str(url))
    return FollowableUser(slug=str(slug).strip().strip("/"), url=url)


def _already_in_requested_state(
    *, action: FollowAction, error_codes: list[str], message: str | None
) -> bool:
    """Recognize known responses for a request that was already satisfied."""
    code = "already.following" if action == "follow" else "not.following"
    phrase = "already following" if action == "follow" else "not following"
    return code in {value.lower() for value in error_codes} or phrase in (message or "").lower()


def set_following(
    session: Any,
    target: FollowTarget,
    following: bool,
    *,
    retry_attempts: int = 3,
    retry_backoff_seconds: float = 2.0,
    timeout: float | None = None,
) -> FollowResult:
    """Request a follow state and return Letterboxd's response as a FollowResult.

    ``desired_state`` says whether the response indicates the requested state
    was achieved or was already in place. ``following`` is the requested
    state; it is not a separate check of the current state.
    """
    user = resolve_followable_user(target)
    action: FollowAction = "follow" if following else "unfollow"
    attempts = max(1, int(retry_attempts))

    for attempt in range(1, attempts + 1):
        try:
            # The complete Letterboxd request: endpoint, form data, and headers.
            url = f"{BASE_URL}/{user.slug}/{action}/"
            payload = {"__csrf": get_csrf(session)}
            headers = {
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": BASE_URL,
                "Referer": user.url,
            }
            if timeout is None:
                response = session.post(url, data=payload, headers=headers)
            else:
                response = session.post(url, data=payload, headers=headers, timeout=timeout)

            # Keep the original retry behavior for these HTTP status codes.
            if getattr(response, "status_code", None) in TRANSIENT_STATUS_CODES and attempt < attempts:
                if retry_backoff_seconds > 0:
                    time.sleep(retry_backoff_seconds * attempt)
                continue
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()

            # Letterboxd returns JSON, including application-level errors.
            data = response.json()
            if not isinstance(data, dict):
                raise RuntimeError(f"Unexpected follow response type: {type(data).__name__}")

            messages = data.get("messages") or []
            message = str(messages[0]) if messages else None
            error_codes = [str(code) for code in (data.get("errorCodes") or [])]
            error_fields = [str(name) for name in (data.get("errorFields") or [])]

            # A nonempty error string must not be mistaken for success.
            result = data.get("result")
            changed = bool(result)
            if isinstance(result, str) and result.strip().lower() in {"error", "failed", "failure", "false"}:
                changed = False
            desired_state = changed or _already_in_requested_state(
                action=action, error_codes=error_codes, message=message
            )

            return FollowResult(
                member_slug=user.slug,
                action=action,
                changed=changed,
                desired_state=desired_state,
                following=(action == "follow"),
                message=message,
                error_codes=error_codes,
                error_fields=error_fields,
                csrf=data.get("csrf"),
                raw=data,
            )
        except Exception:
            # Session and response libraries can use different exception types.
            if attempt == attempts:
                raise
            if retry_backoff_seconds > 0:
                time.sleep(retry_backoff_seconds * attempt)

    raise RuntimeError("Follow action failed")  # The loop returns or raises.


def follow_user(session: Any, target: FollowTarget, **kwargs: Any) -> FollowResult:
    """Follow one member."""
    return set_following(session, target, True, **kwargs)


def unfollow_user(session: Any, target: FollowTarget, **kwargs: Any) -> FollowResult:
    """Unfollow one member."""
    return set_following(session, target, False, **kwargs)


def follow_users(
    session: Any,
    users: list[FollowTarget],
    *,
    following: bool = True,
    stop_after: int = -1,
    retry_attempts: int = 3,
    retry_backoff_seconds: float = 2.0,
    timeout: float | None = None,
) -> tuple[list[FollowResult], list[FollowFailure]]:
    """Apply a state to multiple members, collecting results and exceptions.

    ``stop_after`` limits returned results, including responses that report an
    unsuccessful action. Exceptions are collected separately and do not count
    toward this limit. -1 means no limit.
    """
    results: list[FollowResult] = []
    failures: list[FollowFailure] = []
    action: FollowAction = "follow" if following else "unfollow"
    limit = None if stop_after == -1 else max(0, stop_after)

    for target in users:
        if limit is not None and len(results) >= limit:
            break

        slug: str | None = None
        try:
            slug = resolve_followable_user(target).slug
            result = set_following(
                session, target, following,
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
                timeout=timeout,
            )
            results.append(result)
        except Exception as exc:
            failures.append(
                FollowFailure(
                    member_slug=slug,
                    action=action,
                    error=f"{type(exc).__name__}: {exc}",
                    target=str(target),
                )
            )

    return results, failures


def unfollow_users(
    session: Any,
    users: list[FollowTarget],
    *,
    stop_after: int = -1,
    retry_attempts: int = 3,
    retry_backoff_seconds: float = 2.0,
    timeout: float | None = None,
) -> tuple[list[FollowResult], list[FollowFailure]]:
    """Unfollow an explicit list of members."""
    return follow_users(
        session, users, following=False, stop_after=stop_after,
        retry_attempts=retry_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
        timeout=timeout,
    )


# Existing names kept for callers that use member, ensure, or batch vocabulary.
follow_member = follow_user
unfollow_member = unfollow_user
ensure_following = follow_user
ensure_not_following = unfollow_user
toggle_follow_state = set_following
batch_follow_users = follow_users
batch_unfollow_users = unfollow_users
batch_unfollow_members = unfollow_users
unfollow_members = unfollow_users


__all__ = [
    "FollowAction", "FollowTarget", "FollowableUser", "FollowResult", "FollowFailure",
    "resolve_followable_user", "set_following", "follow_user", "unfollow_user",
    "follow_member", "unfollow_member", "ensure_following", "ensure_not_following",
    "toggle_follow_state", "batch_follow_users", "follow_users", "unfollow_members",
    "batch_unfollow_members", "unfollow_users", "batch_unfollow_users",
]
