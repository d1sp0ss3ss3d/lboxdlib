# lboxdlib/workflows/reciprocal_follow_cleanup.py
"""Unfollow Letterboxd users who do not follow the signed-in viewer back.

This module compares two standard Letterboxd member-list pages:

    /{viewer}/following/
    /{viewer}/followers/

Fetching/pagination is delegated to the existing project fetch layer. The only
Letterboxd write action is delegated to ``lboxdlib.actions.follows.unfollow_user``.

Typical use:

    from lboxdlib.workflows.reciprocal_follow_cleanup import unfollow_nonfollowers

    result = unfollow_nonfollowers(session, dry_run=True)
    print(result.summary())

    # After reviewing:
    result = unfollow_nonfollowers(session, dry_run=False)

Keep-list entries may be raw slugs, profile URLs/paths, or UserSummary-like
objects with a ``.slug`` attribute.

Follower snapshots may also be raw slugs, profile URLs/paths, or UserSummary-like
objects. When provided, the module uses that supplied follower set instead of
fetching the signed-in viewer's current /followers/ page.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Sequence
from urllib.parse import urljoin, urlparse

from lboxdlib.auth import get_signed_in_user

from lboxdlib.fetchers.users import fetch_users as _fetch_users

from lboxdlib.actions.follows import FollowResult, unfollow_user
from lboxdlib.parsers.users import UserSummary, extract_users


BASE_URL = "https://letterboxd.com"


@dataclass(slots=True)
class ReciprocalUserDecision:
    """Decision made for one user that the viewer follows."""

    slug: str
    display_name: str | None = None
    url: str | None = None

    follows_viewer: bool = False
    kept: bool = False
    should_unfollow: bool = False
    action: str = "keep"  # keep | dry_run_unfollow | unfollowed | failed

    reason: str | None = None
    follow_result: FollowResult | None = None
    error: str | None = None


@dataclass(slots=True)
class ReciprocalCleanupResult:
    viewer_slug: str
    following_url: str
    followers_url: str

    following_count: int
    followers_count: int
    keep_count: int

    checked_count: int
    reciprocal_count: int
    keep_list_count: int
    candidate_unfollow_count: int
    unfollowed_count: int
    failed_count: int

    dry_run: bool
    follower_source: str = "live"  # live | supplied
    decisions: list[ReciprocalUserDecision] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "viewer_slug": self.viewer_slug,
            "following_count": self.following_count,
            "followers_count": self.followers_count,
            "keep_count": self.keep_count,
            "checked_count": self.checked_count,
            "reciprocal_count": self.reciprocal_count,
            "keep_list_count": self.keep_list_count,
            "candidate_unfollow_count": self.candidate_unfollow_count,
            "unfollowed_count": self.unfollowed_count,
            "failed_count": self.failed_count,
            "dry_run": self.dry_run,
            "follower_source": self.follower_source,
        }

    def as_dict(self, *, include_decisions: bool = True) -> dict[str, Any]:
        data = self.summary()
        data["following_url"] = self.following_url
        data["followers_url"] = self.followers_url
        if include_decisions:
            data["decisions"] = [asdict(decision) for decision in self.decisions]
        return data


def unfollow_nonfollowers(
    session: Any,
    *,
    keep_list: Iterable[Any] | None = None,
    followers_snapshot: Iterable[Any] | None = None,
    followers: Iterable[Any] | None = None,
    dry_run: bool = True,
    pages: int | None = None,
    start: int = 1,
    max_unfollows: int = -1,
    verbose: bool = False,
    retry_attempts: int = 3,
    retry_backoff_seconds: float = 2.0,
    timeout: float | None = None,
) -> ReciprocalCleanupResult:
    """Unfollow users the viewer follows who do not follow the viewer back.

    Args:
        session:
            Authenticated Letterboxd session.

        keep_list:
            Optional users to never unfollow. Each item may be a slug, profile
            URL/path, or object with ``.slug``.

        followers_snapshot:
            Optional snapshot of users who should count as following the viewer.
            Each item may be a slug, profile URL/path, or object with ``.slug``.
            When provided, the current /followers/ page is not fetched. This is
            useful when comparing against a recent saved follower set rather
            than the live current followers list.

        followers:
            Short alias for ``followers_snapshot``. Do not pass both.

        dry_run:
            When True, only reports who would be unfollowed. Set False to
            actually call ``unfollow_user``.

        pages:
            Optional page limit for both /following/ and /followers/. ``None``
            means fetch all pages according to the standard page handler.

        start:
            Starting page for both lists. Usually leave as 1.

        max_unfollows:
            Maximum number of users to unfollow. ``-1`` means no explicit limit.
            In dry-run mode, this limits the number of dry-run unfollow
            candidates marked as ``dry_run_unfollow``.

        verbose:
            Print progress.

        retry_attempts:
            Retry attempts passed through to ``unfollow_user`` for write actions.

        retry_backoff_seconds:
            Retry backoff passed through to ``unfollow_user`` for write actions.

        timeout:
            Request timeout passed through to ``unfollow_user`` for write actions.

    Returns:
        ReciprocalCleanupResult with a per-user decision log.
    """

    viewer_slug = _signed_in_slug(session)
    following_url = f"{BASE_URL}/{viewer_slug}/following/"
    followers_url = f"{BASE_URL}/{viewer_slug}/followers/"

    if followers_snapshot is not None and followers is not None:
        raise ValueError("Pass either followers_snapshot or followers, not both")

    supplied_followers = followers_snapshot if followers_snapshot is not None else followers

    keep_slugs = _normalize_keep_list(keep_list or [])
    keep_slugs.add(_slug_key(viewer_slug))

    if verbose:
        print(f"[reciprocal] signed in as: {viewer_slug}")
        print(f"[reciprocal] fetching following: {following_url}")

    following_users = _fetch_users_for_url(
        session,
        following_url,
        start=start,
        pages=pages,
        verbose=verbose,
    )

    if verbose:
        print(f"[reciprocal] fetched following users: {len(following_users)}")

    if supplied_followers is None:
        if verbose:
            print(f"[reciprocal] fetching followers: {followers_url}")

        follower_users = _fetch_users_for_url(
            session,
            followers_url,
            start=start,
            pages=pages,
            verbose=verbose,
        )
        follower_slugs = {_slug_key(user.slug) for user in follower_users if user.slug}
        follower_count = len(follower_users)
        follower_source = "live"

        if verbose:
            print(f"[reciprocal] fetched followers: {follower_count}")
    else:
        follower_slugs = _normalize_slug_values(supplied_followers)
        follower_count = len(follower_slugs)
        follower_source = "supplied"

        if verbose:
            print(f"[reciprocal] using supplied follower snapshot: {follower_count}")

    if verbose:
        print(f"[reciprocal] keep-list slugs: {len(keep_slugs)}")

    limit = None if max_unfollows == -1 else max(0, int(max_unfollows))
    unfollow_attempts = 0
    decisions: list[ReciprocalUserDecision] = []

    for user in _dedupe_users(following_users):
        if not user.slug:
            decisions.append(
                ReciprocalUserDecision(
                    slug="",
                    display_name=user.display_name,
                    url=user.url,
                    action="failed",
                    reason="missing_slug",
                    error="Fetched user object had no slug",
                )
            )
            continue

        slug_key = _slug_key(user.slug)
        follows_viewer = slug_key in follower_slugs
        kept = slug_key in keep_slugs
        should_unfollow = (not follows_viewer) and (not kept)

        decision = ReciprocalUserDecision(
            slug=user.slug,
            display_name=user.display_name,
            url=user.url,
            follows_viewer=follows_viewer,
            kept=kept,
            should_unfollow=should_unfollow,
        )

        if follows_viewer:
            decision.action = "keep"
            decision.reason = "follows_viewer"
        elif kept:
            decision.action = "keep"
            decision.reason = "keep_list"
        elif not should_unfollow:
            decision.action = "keep"
            decision.reason = "not_candidate"
        elif limit is not None and unfollow_attempts >= limit:
            decision.action = "keep"
            decision.reason = "max_unfollows_reached"
        elif dry_run:
            decision.action = "dry_run_unfollow"
            decision.reason = "does_not_follow_viewer"
            unfollow_attempts += 1
        else:
            decision.reason = "does_not_follow_viewer"
            unfollow_attempts += 1
            try:
                result = unfollow_user(
                    session,
                    user,
                    retry_attempts=retry_attempts,
                    retry_backoff_seconds=retry_backoff_seconds,
                    timeout=timeout,
                )
                decision.follow_result = result
                if result.desired_state:
                    decision.action = "unfollowed"
                else:
                    decision.action = "failed"
                    decision.error = result.message or "unfollow did not reach desired state"
            except Exception as exc:  # noqa: BLE001 - session implementations vary
                decision.action = "failed"
                decision.error = f"{type(exc).__name__}: {exc}"

        decisions.append(decision)

        if verbose and decision.action != "keep":
            print(f"[reciprocal] {decision.action}: {user.slug} ({decision.reason})")

    reciprocal_count = sum(1 for d in decisions if d.follows_viewer)
    keep_list_count = sum(1 for d in decisions if d.kept and not d.follows_viewer)
    candidate_unfollow_count = sum(1 for d in decisions if d.should_unfollow)
    unfollowed_count = sum(1 for d in decisions if d.action == "unfollowed")
    failed_count = sum(1 for d in decisions if d.action == "failed")

    return ReciprocalCleanupResult(
        viewer_slug=viewer_slug,
        following_url=following_url,
        followers_url=followers_url,
        following_count=len(following_users),
        followers_count=follower_count,
        keep_count=len(keep_slugs),
        checked_count=len(decisions),
        reciprocal_count=reciprocal_count,
        keep_list_count=keep_list_count,
        candidate_unfollow_count=candidate_unfollow_count,
        unfollowed_count=unfollowed_count,
        failed_count=failed_count,
        dry_run=dry_run,
        follower_source=follower_source,
        decisions=decisions,
    )


# More explicit aliases.
cleanup_nonreciprocal_following = unfollow_nonfollowers
prune_nonreciprocal_following = unfollow_nonfollowers
unfollow_nonreciprocal_following = unfollow_nonfollowers


def _fetch_users_for_url(
    session: Any,
    url: str,
    *,
    start: int,
    pages: int | None,
    verbose: bool,
) -> list[UserSummary]:
    """Fetch users through the canonical user fetcher."""
    return list(_fetch_users(session, url, start=start, pages=pages, verbose=verbose))


def _signed_in_slug(session: Any) -> str:
    viewer = get_signed_in_user(session)
    if not viewer:
        raise RuntimeError("Could not determine signed-in Letterboxd user from session")
    return str(viewer).strip().strip("/")


def _normalize_keep_list(values: Iterable[Any]) -> set[str]:
    out: set[str] = set()
    for value in values:
        slug = _resolve_slug(value)
        if slug:
            out.add(_slug_key(slug))
    return out


def _normalize_slug_values(values: Iterable[Any]) -> set[str]:
    """Normalize slugs, profile URLs/paths, or UserSummary-like objects.

    Used for supplied follower snapshots. Invalid or empty values are ignored.
    """

    out: set[str] = set()
    for value in values:
        slug = _resolve_slug(value)
        if slug:
            out.add(_slug_key(slug))
    return out


def _resolve_slug(value: Any) -> str | None:
    slug = getattr(value, "slug", None)
    if slug:
        return str(slug).strip().strip("/") or None

    text = str(value or "").strip()
    if not text:
        return None

    # Plain slug.
    if "/" not in text and not text.startswith("http"):
        return text.strip("/") or None

    parsed = urlparse(urljoin(BASE_URL, text))
    bits = [bit for bit in parsed.path.split("/") if bit]
    if not bits:
        return None

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
        return None

    return bits[0]


def _slug_key(slug: str | None) -> str:
    return str(slug or "").strip().strip("/").lower()


def _dedupe_users(users: Sequence[UserSummary]) -> list[UserSummary]:
    seen: set[str] = set()
    out: list[UserSummary] = []
    for user in users:
        key = _slug_key(user.slug) if user.slug else ""
        if not key:
            # Preserve missing-slug rows so the caller can see the parse issue.
            out.append(user)
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(user)
    return out


__all__ = [
    "ReciprocalUserDecision",
    "ReciprocalCleanupResult",
    "unfollow_nonfollowers",
    "cleanup_nonreciprocal_following",
    "prune_nonreciprocal_following",
    "unfollow_nonreciprocal_following",
]
