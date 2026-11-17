"""Read-only fetching of numbered Letterboxd diary pages."""

from __future__ import annotations

from urllib.parse import urljoin

from lboxdlib.fetchers.pages import fetch_standard_pages
from lboxdlib.models.typedefs import DiaryEntry
from lboxdlib.parsers.diary import extract_diary


def fetch_diary(
    session,
    url: str,
    *,
    start: int = 1,
    pages: int | None = None,
    verbose: bool = False,
) -> list[DiaryEntry]:
    """Fetch diary pages with the supplied session and return DiaryEntry records.

    ``url`` is a diary URL/path, including any sort/filter segments and query.
    ``start`` is the first numbered page; ``pages=None`` reads to the end and
    ``pages=0`` makes no requests. Review pages are not fetched: their links
    are retained in ``review_url`` and review text remains unknown.
    """
    if start < 1:
        raise ValueError("start must be >= 1")
    if pages is not None and pages < 0:
        raise ValueError("pages must be None or >= 0")
    if pages == 0:
        return []

    html_pages = fetch_standard_pages(
        session,
        urljoin("https://letterboxd.com", url),
        start_page=start,
        pages=pages,
        verbose=verbose,
    )
    return [entry for html in html_pages for entry in extract_diary(html)]


__all__ = ["fetch_diary"]
