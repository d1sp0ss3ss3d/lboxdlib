"""Parse diary-table HTML without fetching review pages or changing account state."""

from __future__ import annotations

import json
import re
from datetime import date
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from lboxdlib.models.typedefs import DiaryEntry

BASE_URL = "https://letterboxd.com"


def extract_diary(html: str) -> list[DiaryEntry]:
    """Extract diary viewings in page order, preserving repeat viewings.

    Review text, tags, and aggregate watched/watchlist state remain unknown.
    ``review_url`` records the review link when present; ``url`` records the
    viewing link even when there is no written review. Ratings retain the
    existing DiaryEntry integer 1–10 scale (zero/unrated becomes None).
    """
    soup = BeautifulSoup(html, "lxml")
    entries = []
    for row in soup.select("tr.diary-entry-row"):
        entry = _entry_from_row(row)
        if entry is not None:
            entries.append(entry)
    return entries


def _entry_from_row(row: Tag) -> DiaryEntry | None:
    actions = row.select_one('[data-component-class="DiaryEntryRowActions"]')
    poster = row.select_one('[data-component-class="LazyPoster"]')
    like = row.select_one(".col-like [data-likeable-identifier]")
    film = _identifier(poster, "data-postered-identifier") or _identifier(
        actions, "data-viewingable-identifier"
    )
    viewing = _identifier(like, "data-likeable-identifier")
    viewing_uid = (
        _attr(row, "data-object-id") or _attr(actions, "data-viewing-uid") or viewing.get("uid")
    )
    viewing_id = _int(_attr(row, "data-viewing-id") or _attr(actions, "data-viewing-id"))
    if viewing_id is None:
        viewing_id = _viewing_id(viewing_uid)

    title = row.select_one(".primaryname a[href]")
    review_link = row.select_one(".col-review a[href]")
    entry_href = _attr(title, "href") or _attr(review_link, "href")
    owner = _attr(row, "data-owner") or _attr(actions, "data-owner-username")
    slug = _attr(poster, "data-item-slug") or _attr(actions, "data-viewingable-slug")
    path = urlsplit(entry_href or "").path.split("/")
    if len(path) >= 4 and path[2] == "film":
        owner = owner or path[1]
        slug = slug or path[3]
    if viewing_id is None or not owner or not slug:
        return None

    rewatch = row.select_one(".col-rewatch")
    liked = row.select_one(".col-like")
    film_year = row.select_one(".col-releaseyear, .releasedate")
    return DiaryEntry(
        viewing_id=viewing_id,
        owner=owner,
        film_slug=slug,
        film_lid=film.get("lid"),
        film_uid=film.get("uid") or _attr(row, "data-production-uid"),
        rating=_rating(row),
        viewing_date=_viewing_date(row),
        film_name=_text(title) or _attr(actions, "data-viewingable-name"),
        film_year=_int(_text(film_year)),
        viewing_uid=f"viewing:{viewing_id}",
        review_lid=viewing.get("lid"),
        url=urljoin(BASE_URL, entry_href) if entry_href else None,
        review_url=(urljoin(BASE_URL, review_link["href"]) if review_link else None),
        rewatch=("icon-status-off" not in rewatch.get("class", [])) if rewatch else None,
        liked_film=(bool(liked.select_one(".icon-liked, .inline-liked")) if liked else None),
    )


def _identifier(node: Tag | None, attribute: str) -> dict:
    try:
        value = json.loads(_attr(node, attribute) or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _attr(node: Tag | None, name: str) -> str | None:
    value = node.get(name) if node is not None else None
    return str(value) if value is not None else None


def _text(node: Tag | None) -> str | None:
    if node is None:
        return None
    return node.get_text(" ", strip=True) or None


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _viewing_id(uid: str | None) -> int | None:
    match = re.fullmatch(r"viewing:(\d+)", str(uid or ""))
    return int(match[1]) if match else None


def _viewing_date(row: Tag) -> str | None:
    day = row.select_one(".col-daydate a[href]")
    match = re.search(r"/for/(\d{4})/(\d{2})/(\d{2})/", _attr(day, "href") or "")
    if match:
        try:
            return date(*(int(part) for part in match.groups())).isoformat()
        except ValueError:
            return None
    return None


def _rating(row: Tag) -> int | None:
    cell = row.select_one(".col-rating")
    if cell is None:
        return None
    value = cell.select_one("input.rateit-field[value]")
    if value is not None:
        rating = _int(_attr(value, "value"))
        if rating is not None and 0 <= rating <= 10:
            return rating or None
    for span in cell.select(".rating"):
        for class_name in span.get("class", []):
            match = re.fullmatch(r"rated-(\d+)", class_name)
            if match and 1 <= int(match[1]) <= 10:
                return int(match[1])
        text = _text(span) or ""
        if "★" in text or "½" in text:
            rating = text.count("★") * 2 + int("½" in text)
            if 1 <= rating <= 10:
                return rating
    return None


__all__ = ["extract_diary"]
