import html as html_lib
import json
import re
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from lboxdlib.models.typedefs import FilmSummary, Link

BASE_LBXD_URL = "https://letterboxd.com"


def is_list_preview_lazy_poster(tag: Tag) -> bool:
    """
    True for LazyPoster nodes inside Letterboxd list preview cards,
    such as sidebar 'Cloned from…' / related-list poster stacks.
    """
    in_overlapped_preview = (
        tag.find_parent("div", class_="poster-list-overlapped") is not None
    )
    in_preview_link = (
        tag.find_parent("a", class_="poster-list-link") is not None
    )
    in_list_card = (
        tag.find_parent("section", class_="list") is not None
    )

    return in_overlapped_preview and (in_preview_link or in_list_card)


def _parse_postered_identifier(tag: Tag) -> dict:
    raw = tag.attrs.get("data-postered-identifier")
    if not raw:
        return {}

    try:
        return json.loads(html_lib.unescape(raw))
    except (json.JSONDecodeError, TypeError):
        return {}


def _uid_from_lazy_poster(tag: Tag) -> Optional[str]:
    postered_data = _parse_postered_identifier(tag)
    uid = postered_data.get("uid")

    if isinstance(uid, str) and uid.startswith("film:"):
        return uid

    fallback = tag.attrs.get("data-film-id")
    return f"film:{fallback}" if fallback else None


def _lid_from_lazy_poster(tag: Tag) -> Optional[str]:
    postered_data = _parse_postered_identifier(tag)
    lid = postered_data.get("lid")
    return str(lid) if lid else None


def _split_display_name(display_name: Optional[str]) -> tuple[str, Optional[int]]:
    if not display_name:
        return "", None

    match = re.match(r"^(.*?)(?:\s*\((\d{4})\))?$", display_name)
    if not match:
        return display_name, None

    name = match.group(1)
    release_year = int(match.group(2)) if match.group(2) else None
    return name, release_year


def film_summary_from_lazy_poster(tag: Tag) -> Optional[FilmSummary]:
    """
    Build an incomplete FilmSummary from a Letterboxd LazyPoster.

    This intentionally creates only the information available in the page DOM.
    Use FilmHydrator with the film lid for the complete FilmSummary record.
    """
    if not tag or is_list_preview_lazy_poster(tag):
        return None

    attrs = tag.attrs
    lid = _lid_from_lazy_poster(tag)
    if lid is None:
        raise ValueError("Could not extract film lid from LazyPoster tag")

    uid = _uid_from_lazy_poster(tag)
    if uid is None:
        raise ValueError("Could not extract film uid from LazyPoster tag")

    raw_display_name = attrs.get("data-item-name") or attrs.get("data-item-full-display-name")
    display_name = html_lib.unescape(raw_display_name) if raw_display_name else None
    name, release_year = _split_display_name(display_name)

    item_link = attrs.get("data-item-link") or attrs.get("data-target-link")
    absolute_link = urljoin(BASE_LBXD_URL, item_link) if item_link else None

    links = []
    if absolute_link:
        links.append(
            Link(
                type="letterboxd",
                id=lid,
                url=absolute_link,
            )
        )

    return FilmSummary(
        lid=lid,
        uid=uid,
        name=name or display_name or uid,
        link=item_link,
        releaseYear=release_year,
        links=links,
    )


def film_from_lazy_poster(tag, user=None):
    return film_summary_from_lazy_poster(tag)


def lazy_posters_from_page(page: str):
    soup = BeautifulSoup(page, "lxml")

    return soup.find_all(
        "div",
        class_="react-component",
        attrs={"data-component-class": "LazyPoster"},
    )


def lid_from_lazy_poster(tag):
    if not tag or is_list_preview_lazy_poster(tag):
        return None
    return _lid_from_lazy_poster(tag)


def extract_lids(page):
    posters = lazy_posters_from_page(page)

    lids = []
    for poster in posters:
        lid = lid_from_lazy_poster(poster)
        if lid is not None:
            lids.append(lid)

    return lids


def extract_films(page):
    """
    Extract incomplete FilmSummary records from visible LazyPosters.

    For fully hydrated Letterboxd FilmSummary records, fetch lids with
    extract_lids()/page_handling.fetch_lids() and pass them through FilmHydrator.
    """
    posters = lazy_posters_from_page(page)

    films = []
    for poster in posters:
        film = film_summary_from_lazy_poster(poster)
        if film is not None:
            films.append(film)

    return films
