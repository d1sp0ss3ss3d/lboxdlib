"""Letterboxd review-comment fetch helpers."""

from __future__ import annotations

from typing import Any

from lboxdlib.models.comments import CommentCollection
from lboxdlib.parsers.comments import extract_comments


LETTERBOXD_ORIGIN = "https://letterboxd.com"


def fetch_comments_section(
    session: Any,
    viewing_id: int | str,
) -> CommentCollection:
    """Fetch and parse the full comments-section snapshot for a viewing.

    Uses the CSI endpoint observed on full review-page loads. The caller's
    supplied session is used unchanged; this function does not create a session
    or supply browser headers.
    """
    url = f"{LETTERBOXD_ORIGIN}/csi/viewing/{viewing_id}/comments-section/"
    response = session.get(url, params={"esiAllowUser": "true"})
    response.raise_for_status()
    return extract_comments(response.text)


def fetch_comment_updates(
    session: Any,
    viewing_id: int | str,
    since: int | str,
) -> CommentCollection:
    """Fetch comment-state changes after a known Letterboxd timestamp.

    Letterboxd's observed delta endpoint returns an empty ``comment-list`` when
    there are no changes and returns changed/new/deleted comment records when
    state has advanced. Cursor advancement policy is intentionally left to the
    caller.
    """
    url = f"{LETTERBOXD_ORIGIN}/ajax/viewing:{viewing_id}/comments/"
    response = session.get(url, params={"since": since})
    response.raise_for_status()
    return extract_comments(response.text)


__all__ = [
    "fetch_comments_section",
    "fetch_comment_updates",
]
