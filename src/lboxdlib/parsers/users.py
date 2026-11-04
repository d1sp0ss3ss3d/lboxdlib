"""
Letterboxd user/member extraction helpers.

This module parses standard Letterboxd member-list pages such as:

    /{user}/following/
    /{user}/followers/
    /members/
    /members/popular/
    /search/members/{query}/

Fetching/pagination is intentionally not handled here. Use
``lboxdlib.fetchers.pages.fetch_from_pages`` or the public
``lboxdlib.fetchers.users.fetch_users`` helper.

The parser is deliberately DOM-based and conservative: it extracts only member
summary blocks that Letterboxd renders as ``.person-summary``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from lboxdlib.pronouns import uses_pronouns as _uses_pronouns

BASE_URL = "https://letterboxd.com"


@dataclass(slots=True)
class UserSummary:
    """Normalized summary for a Letterboxd member/user."""

    slug: str | None
    display_name: str | None
    url: str | None = None
    avatar_url: str | None = None

    followers_count: int | None = None
    following_count: int | None = None
    followers_url: str | None = None
    following_url: str | None = None

    bio: str | None = None
    location: str | None = None
    website_url: str | None = None

    raw_text: str | None = None
    pronouns: str | None = None

    def uses_pronouns(self, pronouns: str) -> bool:
        """Return whether this user has the requested established pronouns."""

        return _uses_pronouns(self.pronouns, pronouns)


# Backward-compatible project vocabulary. Letterboxd calls them "members" in
# some page routes, while user-facing code often calls them users.
MemberSummary = UserSummary


def extract_users(html: str) -> list[UserSummary]:
    """Extract UserSummary objects from a Letterboxd HTML page/fragment."""

    soup = BeautifulSoup(html or "", "lxml")
    users: list[UserSummary] = []

    seen: set[str] = set()

    for node in _iter_person_summary_nodes(soup):
        user = user_from_person_summary(node)
        key = user.slug or user.url or user.display_name

        if not key or key in seen:
            continue

        seen.add(key)
        users.append(user)

    return users


def extract_member_summaries(html: str) -> list[UserSummary]:
    """Alias used by older fetcher code."""

    return extract_users(html)


def extract_user_slugs(html: str) -> list[str]:
    """Return unique member slugs from a Letterboxd page/fragment."""

    slugs: list[str] = []
    seen: set[str] = set()

    for user in extract_users(html):
        if not user.slug or user.slug in seen:
            continue
        seen.add(user.slug)
        slugs.append(user.slug)

    return slugs


def user_from_person_summary(node: Tag) -> UserSummary:
    """Parse one ``.person-summary`` block."""

    name_link = _first(
        node.select_one("a.name[href]"),
        node.select_one(".title-3 a[href]"),
        node.select_one(".title-2 a[href]"),
        node.select_one("h3 a[href]"),
        node.select_one("h2 a[href]"),
    )

    avatar_link = node.select_one("a.avatar[href]")
    avatar_img = avatar_link.select_one("img") if avatar_link else None

    href = _attr(name_link, "href") or _attr(avatar_link, "href")
    slug = _member_slug_from_href(href)

    display_name = (
        _clean_text(name_link)
        or _attr(avatar_img, "alt")
        or slug
    )

    metadata = node.select_one(".metadata")
    followers_count, followers_url = _count_and_url_for_relation(metadata, "followers")
    following_count, following_url = _count_and_url_for_relation(metadata, "following")

    return UserSummary(
        slug=slug,
        display_name=display_name,
        url=_absolute_or_none(href),
        avatar_url=_absolute_or_none(_attr(avatar_img, "src")),
        followers_count=followers_count,
        following_count=following_count,
        followers_url=_absolute_or_none(followers_url),
        following_url=_absolute_or_none(following_url),
        bio=_clean_text(node.select_one(".bio, .body-text, .profile-bio")) or None,
        location=_clean_text(node.select_one(".location")) or None,
        website_url=_absolute_or_none(_attr(node.select_one('a[rel~="me"][href], a.website[href]'), "href")),
        raw_text=_clean_text(node) or None,
    )


def users_as_dicts(users: Iterable[UserSummary]) -> list[dict[str, Any]]:
    return [asdict(user) for user in users]


def slugs_for_pronouns(
    users: Iterable[UserSummary],
    pronouns: str,
) -> list[str]:
    """Return slugs for users whose established pronouns include ``pronouns``."""

    return [
        user.slug
        for user in users
        if user.slug is not None and user.uses_pronouns(pronouns)
    ]


def _iter_person_summary_nodes(soup: BeautifulSoup | Tag) -> list[Tag]:
    # Follow/follower pages and member/search pages use this stable block.
    return list(soup.select(".person-summary"))


def _count_and_url_for_relation(
    metadata: Tag | None,
    relation: str,
) -> tuple[int | None, str | None]:
    if not metadata:
        return None, None

    relation = relation.strip("/").lower()

    for link in metadata.select("a[href]"):
        href = _attr(link, "href")
        text = _clean_text(link)

        if not href:
            continue

        path = urlparse(href).path.lower()
        if not path.rstrip("/").endswith(f"/{relation}"):
            continue

        return _int_from_text(text), href

    # Fallback for variants where metadata is not linked.
    text = _clean_text(metadata)
    if relation == "followers":
        match = re.search(r"([\d,]+)\s+followers?\b", text, flags=re.I)
    else:
        match = re.search(r"\bfollowing\s+([\d,]+)", text, flags=re.I)

    if match:
        return _int_from_text(match.group(1)), None

    return None, None


def _member_slug_from_href(href: str | None) -> str | None:
    bits = _path_bits(href)

    if not bits:
        return None

    # Avoid treating site-level routes as member slugs.
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


def _path_bits(href: str | None) -> list[str]:
    if not href:
        return []
    return [bit for bit in urlparse(href).path.split("/") if bit]


def _absolute_or_none(href: str | None) -> str | None:
    if not href:
        return None
    return urljoin(BASE_URL, href)


def _attr(node: Tag | BeautifulSoup | None, attr: str) -> str | None:
    if not node:
        return None
    value = node.get(attr)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_text(node: Tag | BeautifulSoup | None) -> str:
    if not node:
        return ""
    return " ".join(node.get_text(" ", strip=True).replace("\xa0", " ").split())


def _int_from_text(text: str | None) -> int | None:
    if not text:
        return None

    match = re.search(r"\d[\d,]*", text)
    if not match:
        return None

    try:
        return int(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _first(*nodes: Tag | None) -> Tag | None:
    for node in nodes:
        if node is not None:
            return node
    return None


__all__ = [
    "UserSummary",
    "MemberSummary",
    "extract_users",
    "extract_member_summaries",
    "extract_user_slugs",
    "slugs_for_pronouns",
    "user_from_person_summary",
    "users_as_dicts",
]
