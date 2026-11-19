"""Fetch tags from a review or a Letterboxd search."""

from urllib.parse import urljoin, urlsplit

from lboxdlib.auth import BASE_URL, get_csrf
from lboxdlib.fetchers.pages import fetch_cursored_pages
from lboxdlib.models.tags import TagSummary
from lboxdlib.parsers.tags import extract_tags
from lboxdlib.selectors.filter_sort import LETTERBOXD_HOSTS


def fetch_tags(
    session,
    url: str,
    *,
    pages: int | None = None,
    verbose: bool = False,
) -> list[TagSummary]:
    """Fetch tags from a full review URL or a mixed or tag-only search URL."""
    if pages == 0:
        return []

    parts = urlsplit(urljoin(BASE_URL, url))
    if parts.scheme not in {"http", "https"} or parts.netloc.lower() not in LETTERBOXD_HOSTS:
        raise ValueError("Expected a Letterboxd review or search URL")
    if parts.path.startswith("/s/search/"):
        parts = parts._replace(path=parts.path.removeprefix("/s"))
    url = parts.geturl()

    if not parts.path.startswith("/search/"):
        response = session.get(url)
        response.raise_for_status()
        return extract_tags(response.text)

    try:
        get_csrf(session)
    except RuntimeError:
        # An anonymous session can acquire the search cookie from the visible page.
        response = session.get(url)
        response.raise_for_status()

    return [
        tag
        for html in fetch_cursored_pages(session, url, pages=pages, verbose=verbose)
        for tag in extract_tags(html)
    ]


__all__ = ["fetch_tags"]
