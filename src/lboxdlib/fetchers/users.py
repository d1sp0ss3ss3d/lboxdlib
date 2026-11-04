"""Letterboxd user/member fetch helpers."""

from __future__ import annotations

from dataclasses import replace

from lboxdlib.auth import get_signed_in_user
from lboxdlib.fetchers.pages import fetch_from_pages
from lboxdlib.parsers.users import UserSummary, extract_users


def fetch_users(
    session,
    url: str,
    *,
    start: int = 1,
    pages: int | None = None,
    verbose: bool = False,
):
    """Fetch paginated Letterboxd member pages and return UserSummary objects."""
    return fetch_from_pages(
        session,
        url,
        extract_fn=extract_users,
        start_page=start,
        pages=pages,
        verbose=verbose,
    )


def fetch_members(
    session,
    url: str,
    *,
    start: int = 1,
    pages: int | None = None,
    verbose: bool = False,
):
    """Member-vocabulary alias for :func:`fetch_users`."""
    return fetch_users(session, url, start=start, pages=pages, verbose=verbose)


def fetch_following_users(
    session,
    *,
    username: str | None = None,
    start: int = 1,
    pages: int | None = None,
    verbose: bool = False,
):
    """Fetch users followed by ``username`` or by the signed-in user."""
    user = username or get_signed_in_user(session)
    if not user:
        raise RuntimeError("Could not determine signed-in Letterboxd user.")
    return fetch_users(
        session,
        f"https://letterboxd.com/{user}/following/",
        start=start,
        pages=pages,
        verbose=verbose,
    )


def fetch_follower_users(
    session,
    *,
    username: str | None = None,
    start: int = 1,
    pages: int | None = None,
    verbose: bool = False,
):
    """Fetch followers of ``username`` or of the signed-in user."""
    user = username or get_signed_in_user(session)
    if not user:
        raise RuntimeError("Could not determine signed-in Letterboxd user.")
    return fetch_users(
        session,
        f"https://letterboxd.com/{user}/followers/",
        start=start,
        pages=pages,
        verbose=verbose,
    )


def determine_pronouns_for_users(
    session,
    user_list_url: str,
    *,
    user_start: int = 1,
    user_pages: int | None = None,
    activity_pages: int | None = 20,
    verbose: bool = False,
) -> list[UserSummary]:
    """Fetch users and establish their pronouns from their own activity feeds.

    Only conservative Letterboxd activity templates tied to the actor are used.
    Review/comment prose does not count as evidence. Existing pronouns on a
    ``UserSummary`` are preserved and combined with activity evidence.

    Review-like hydration is disabled because it is unrelated to pronoun
    detection and would add unnecessary metadata requests.
    """

    from lboxdlib.fetchers.activity import fetch_activities
    from lboxdlib.parsers.activity_summary import ActivitySummary
    from lboxdlib.pronouns import merge_pronouns

    users = fetch_users(
        session,
        user_list_url,
        start=user_start,
        pages=user_pages,
        verbose=verbose,
    )
    results: list[UserSummary] = []

    for user in users:
        if not user.url or not user.slug:
            results.append(user)
            continue

        activity_url = f"{user.url.rstrip('/')}/activity/"
        activities = fetch_activities(
            session,
            activity_url,
            pages=activity_pages,
            verbose=verbose,
            hydrate_likes=False,
            strict_hydration=False,
        )
        summary = ActivitySummary.from_activities(
            activities,
            subject_slug=user.slug,
        )
        results.append(
            replace(
                user,
                pronouns=merge_pronouns(user.pronouns, summary.subjectPronouns),
            )
        )

    return results


def members_from_review(session, reviews):
    """Collect members who liked the supplied reviews."""
    out = []
    for review in reviews:
        if getattr(review, "likesUrl", None):
            out.extend(fetch_users(session, review.likesUrl))
    return out


def diff_members(ls1, ls2, group=None, session=None):
    """Return member slugs unique to each of two member collections."""
    if session and isinstance(ls1, str) and isinstance(ls2, str) and ls1.startswith("https") and ls2.startswith("https"):
        ls1 = fetch_members(session, ls1)
        ls2 = fetch_members(session, ls2)
    left = {getattr(mem, "slug", None) for review in ls1 for mem in review}
    right = {getattr(mem, "slug", None) for review in ls2 for mem in review}
    left.discard(None)
    right.discard(None)
    return sorted(left - right), sorted(right - left)


# Alternate Letterboxd vocabulary retained as aliases, without shim modules.
fetch_following_members = fetch_following_users
fetch_follower_members = fetch_follower_users
users_from_review = members_from_review
diff_users = diff_members


__all__ = [
    "fetch_users",
    "fetch_members",
    "fetch_following_users",
    "fetch_follower_users",
    "determine_pronouns_for_users",
    "fetch_following_members",
    "fetch_follower_members",
    "members_from_review",
    "users_from_review",
    "diff_members",
    "diff_users",
]
