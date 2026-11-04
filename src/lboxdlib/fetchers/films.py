"""Film-page fetch helpers."""

from __future__ import annotations

from typing import Optional

from lboxdlib.fetchers.pages import fetch_from_pages
from lboxdlib.parsers.films import extract_films, extract_lids


def fetch_films(
    session,
    url: str,
    *,
    start: int = 1,
    pages: Optional[int] = None,
    verbose: bool = False,
):
    """Fetch paginated Letterboxd film pages and return parsed films."""
    return fetch_from_pages(
        session,
        url,
        extract_fn=extract_films,
        start_page=start,
        pages=pages,
        verbose=verbose,
    )


def fetch_lids(
    session,
    url: str,
    *,
    start: int = 1,
    pages: Optional[int] = None,
    verbose: bool = False,
):
    """Fetch paginated Letterboxd film pages and return film lids."""
    return fetch_from_pages(
        session,
        url,
        extract_fn=extract_lids,
        start_page=start,
        pages=pages,
        verbose=verbose,
    )


__all__ = ["fetch_films", "fetch_lids"]
