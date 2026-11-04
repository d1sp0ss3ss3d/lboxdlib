
from __future__ import annotations

from typing import List, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from lboxdlib.models.typedefs import MemberSummary


def _clean_text(node: Optional[Tag]) -> str:
    if not node:
        return ""
    return " ".join(node.get_text(" ", strip=True).split())


def _slug_from_href(href: str | None) -> str | None:
    """
    Convert a Letterboxd profile href like '/jason99x/' into 'jason99x'.
    """
    if not href:
        return None

    path = urlparse(href).path
    bits = [bit for bit in path.split("/") if bit]

    if not bits:
        return None

    return bits[0]


def _username_from_context(summary: Tag, slug: str) -> str:
    """
    Prefer the follow wrapper's data-username when present because it can
    preserve username casing. Fall back to slug.
    """
    row = summary.find_parent("tr")
    wrapper = None

    if row:
        wrapper = row.select_one(".js-follow-button-wrapper[data-username]")

    if not wrapper:
        wrapper = summary.select_one(".js-follow-button-wrapper[data-username]")

    username = wrapper.get("data-username") if wrapper else None
    return username.strip() if username else slug


def member_summary_from_dom(summary: Tag) -> MemberSummary | None:
    """
    Parse one Letterboxd <div class="person-summary"> block into a basic
    MemberSummary.

    The DOM blocks currently expose:
      - slug/profile href
      - display name
      - avatar URL
      - sometimes username through follow-button wrapper

    They do not expose profile location, website, bio, pronoun, poster prefs,
    includeInMembers, or adultContent, so those stay as defaults / blanks.
    """
    name_a = summary.select_one("a.name[href]")
    avatar_a = summary.select_one("a.avatar[href]")

    href = None
    if name_a:
        href = name_a.get("href")
    elif avatar_a:
        href = avatar_a.get("href")

    slug = _slug_from_href(href)
    if not slug:
        return None

    img = summary.select_one("a.avatar img[src], img[src]")

    display_name = _clean_text(name_a)
    if not display_name and img:
        display_name = (img.get("alt") or "").strip()

    username = _username_from_context(summary, slug)

    avatar_url = None
    if img and img.get("src"):
        avatar_url = img.get("src").strip()

    return MemberSummary(
        username=username,
        givenName=display_name or username,
        familyName=None,
        slug=slug,
        location="",
        website=None,
        bio=None,
        avatarUrl=avatar_url,
    )


def extract_member_summaries(html: str) -> List[MemberSummary]:
    """
    Extract all basic MemberSummary records from a Letterboxd page/fragment
    containing person-summary DOM blocks.
    """
    soup = BeautifulSoup(html, "lxml")

    members: list[MemberSummary] = []
    seen_slugs: set[str] = set()

    for summary in soup.select("div.person-summary"):
        member = member_summary_from_dom(summary)

        if not member:
            continue

        if member.slug in seen_slugs:
            continue

        seen_slugs.add(member.slug)
        members.append(member)

    return members
    