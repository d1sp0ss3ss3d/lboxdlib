"""Letterboxd review fetch helpers."""

from __future__ import annotations

import re
from dataclasses import fields, replace
from urllib.parse import urljoin, urlsplit

from lboxdlib.auth import BASE_URL
from lboxdlib.fetchers.pages import fetch_from_pages
from lboxdlib.fetchers.users import fetch_users
from lboxdlib.models.typedefs import ReviewSummary
from lboxdlib.parsers.reviews import extract_reviews
from lboxdlib.selectors.filter_sort import LETTERBOXD_HOSTS


def fetch_reviews(
    session,
    url: str,
    *,
    start: int = 1,
    pages: int | None = None,
    verbose: bool = False,
    want_likers: bool = False,
    full_reviews: bool = False,
) -> list[ReviewSummary]:
    """Fetch reviews from a detail page or a paginated listing.

    With full_reviews=True, fetch the detail pages for listing cards. A card
    whose detail is unavailable retains its listing data and isFullReview=False.
    """
    reviews = fetch_from_pages(
        session,
        url,
        extract_fn=extract_reviews,
        start_page=start,
        pages=pages,
        verbose=verbose,
    )

    if full_reviews:
        _hydrate_full_reviews(session, reviews, verbose=verbose)

    if want_likers:
        for review in reviews:
            if getattr(review, "likesUrl", None):
                likers = fetch_users(session, review.likesUrl)
                # Preserve the existing misspelled attribute for already-hydrated
                # objects while also setting the correctly spelled form.
                review.likesMemmbers = likers
                review.likesMembers = likers

    return reviews


def _hydrate_full_reviews(session, reviews: list[ReviewSummary], *, verbose: bool) -> None:
    details: dict[str, tuple[ReviewSummary, str] | None] = {}
    for index, card in enumerate(reviews):
        url = _detail_url(card.reviewUrl)
        if card.isFullReview or url is None:
            continue

        if url not in details:
            if verbose:
                print(f"Fetching full review {url}")
            try:
                response = session.get(url)
            except OSError:
                details[url] = None
            else:
                if response.status_code == 429:
                    if verbose:
                        print("Stopped fetching full reviews after HTTP 429")
                    break
                details[url] = _parse_full_review_response(response, url)

        result = details[url]
        if result is not None:
            detail, response_url = result
            if _matches_review(card, detail, url, response_url):
                reviews[index] = _merge_review(card, detail)


def _detail_url(href: str | None) -> str | None:
    if not href:
        return None
    try:
        parts = urlsplit(urljoin(BASE_URL, href))
    except ValueError:
        return None

    path = parts.path.rstrip("/") + "/"
    if (
        parts.scheme in {"http", "https"}
        and parts.netloc.lower() in LETTERBOXD_HOSTS
        and re.fullmatch(r"/[^/]+/film/[^/]+/(?:[0-9]+/)?", path)
    ):
        return f"{BASE_URL}{path}"
    return None


def _parse_full_review_response(response, requested_url: str) -> tuple[ReviewSummary, str] | None:
    if not 200 <= response.status_code < 300:
        return None
    reviews = extract_reviews(response.text)
    if len(reviews) == 1 and reviews[0].isFullReview:
        return reviews[0], getattr(response, "url", None) or requested_url
    return None


def _matches_review(
    card: ReviewSummary, detail: ReviewSummary, requested_url: str, response_url: str
) -> bool:
    identifiers = [
        (listed, fetched)
        for listed, fetched in (
            (card.viewingId, detail.viewingId),
            (card.reviewLid, detail.reviewLid),
        )
        if listed is not None and fetched is not None
    ]
    if identifiers:
        return all(listed == fetched for listed, fetched in identifiers)
    return requested_url == _detail_url(detail.reviewUrl or response_url)


def _merge_review(card: ReviewSummary, detail: ReviewSummary) -> ReviewSummary:
    missing_metadata = {
        field.name: getattr(card, field.name)
        for field in fields(ReviewSummary)
        if field.name not in {"reviewText", "reviewHtml"} and getattr(detail, field.name) is None
    }
    # Keep the card's specific link, including any repeat-viewing number.
    missing_metadata.update(reviewUrl=card.reviewUrl, isNumberedReview=card.isNumberedReview)
    return replace(
        detail, tags=detail.tags.copy(), tagDetails=detail.tagDetails.copy(), **missing_metadata
    )


__all__ = ["fetch_reviews"]
