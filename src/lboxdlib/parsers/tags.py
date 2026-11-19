"""Parse tag links from review pages and search results."""

from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup, Tag

from lboxdlib.models.tags import TagSummary
from lboxdlib.selectors.filter_sort import LETTERBOXD_HOSTS, parse_letterboxd_url

BASE_URL = "https://letterboxd.com"
REVIEW_SECTION = "section.review.js-review"
REVIEW_TAG_LINKS = "ul.tags a[href]"
SEARCH_TAG_LINKS = "li.search-result.-tag h2.title-2 a[href]"


def extract_tags(html: str) -> list[TagSummary]:
    """Extract tags from a full review page or Letterboxd search results."""
    soup = BeautifulSoup(html, "lxml")
    section = soup.select_one(REVIEW_SECTION)
    if section is not None:
        return tags_from_review(section)
    return _tags_from_links(soup.select(SEARCH_TAG_LINKS))


def tags_from_review(section: Tag) -> list[TagSummary]:
    return _tags_from_links(section.select(REVIEW_TAG_LINKS))


def _tags_from_links(links: list[Tag]) -> list[TagSummary]:
    tags = []
    for link in links:
        name = " ".join(link.get_text(" ", strip=True).split())
        try:
            url = urljoin(BASE_URL, link["href"])
            parsed = parse_letterboxd_url(url)
        except ValueError:
            continue

        slug = parsed.tag_slug
        if (
            name
            and slug
            and parsed.scheme in {"http", "https"}
            and parsed.netloc.lower() in LETTERBOXD_HOSTS
        ):
            tags.append(TagSummary(name, unquote(slug), url, parsed.tag_username))
    return tags
