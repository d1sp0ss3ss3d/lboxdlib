"""Letterboxd review fetch helpers."""

from __future__ import annotations

from typing import Optional

from lboxdlib.fetchers.pages import fetch_from_pages
from lboxdlib.fetchers.users import fetch_users
from lboxdlib.parsers.reviews import extract_reviews


def fetch_reviews(
    session,
    url: str,
    *,
    start: int = 1,
    pages: Optional[int] = None,
    verbose: bool = False,
    want_likers: bool = False,
):
    """Fetch paginated review pages/fragments and return ReviewSummary objects."""
    reviews = fetch_from_pages(
        session,
        url,
        extract_fn=extract_reviews,
        start_page=start,
        pages=pages,
        verbose=verbose,
    )

    if want_likers:
        for review in reviews:
            if getattr(review, "likesUrl", None):
                likers = fetch_users(session, review.likesUrl)
                # Preserve the existing misspelled attribute for already-hydrated
                # objects while also setting the correctly spelled form.
                review.likesMemmbers = likers
                review.likesMembers = likers

    return reviews


__all__ = ["fetch_reviews"]
