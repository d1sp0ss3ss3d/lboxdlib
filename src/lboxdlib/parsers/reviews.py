
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from lboxdlib.models.typedefs import ReviewSummary

BASE_URL = "https://letterboxd.com"


def extract_reviews(html: str) -> list[ReviewSummary]:
    """
    Extract ReviewSummary objects from a Letterboxd reviews page.

    Handles:
      1. /{user}/reviews/
      2. /{user}/film/{film}/reviews/
      3. /{user}/film/{film}/ or /{user}/film/{film}/{n}/ full review pages
    """
    soup = BeautifulSoup(html, "lxml")

    page_review_count, page_reviews_url = _review_count_and_url_from_page(soup)
    page_film_context = _film_context_from_page(soup)

    review_articles = soup.select("article.production-viewing")

    if review_articles:
        reviews: list[ReviewSummary] = []

        for article in review_articles:
            review = _review_from_article(
                article,
                page_film_context=page_film_context,
                page_review_count=page_review_count,
                page_reviews_url=page_reviews_url,
            )

            if review:
                reviews.append(review)

        return reviews

    full_review = _review_from_full_page(
        soup,
        page_film_context=page_film_context,
        page_review_count=page_review_count,
        page_reviews_url=page_reviews_url,
    )

    return [full_review] if full_review else []


def _review_from_article(
    article: Tag,
    *,
    page_film_context: dict[str, Any],
    page_review_count: int | None,
    page_reviews_url: str | None,
) -> ReviewSummary | None:
    body = article.select_one(".js-review-body")

    if not body:
        return None

    context_link = article.select_one(".attribution-detail a.context[href]")
    primary_link = article.select_one(".inline-production-masthead .primaryname a[href], .primaryname a[href]")

    review_href = _attr(context_link, "href") or _attr(primary_link, "href")

    owner = (
        article.get("data-owner")
        or _owner_from_href(review_href)
    )

    owner_display_name = _owner_display_name_from_context(context_link)

    film_context = _film_context_from_container(article)
    film_context = _merge_film_context(film_context, page_film_context)

    film_slug = film_context.get("filmSlug") or _film_slug_from_review_href(review_href)

    review_lid, viewing_uid, viewing_id = _review_identity_from_like_component(article)

    rating_text = _rating_text_from_container(article)

    like_component = article.select_one(".like-link-target[data-likeable-identifier]")
    comments_link = article.select_one('a.metadata[href*="#comments"]')

    reviews_url = (
        page_reviews_url
        or _reviews_url(owner, film_slug)
    )

    return ReviewSummary(
        owner=owner,
        ownerDisplayName=owner_display_name,
        filmSlug=film_slug,
        filmName=film_context.get("filmName"),
        filmYear=film_context.get("filmYear"),
        filmLid=film_context.get("filmLid"),
        filmUid=film_context.get("filmUid"),

        viewingUid=viewing_uid,
        viewingId=viewing_id,
        reviewLid=review_lid,

        reviewUrl=review_href,
        reviewsUrl=reviews_url,
        reviewCountForFilm=page_review_count,
        isNumberedReview=_is_numbered_review_url(review_href),

        date=_attr(article.select_one("time.timestamp"), "datetime"),
        viewingAction=_viewing_action_from_text(_clean_text(context_link)),

        rating=_rating_float(rating_text),
        ratingText=rating_text,
        likedFilm=bool(article.select_one(".content-reactions-strip .inline-liked, .content-reactions-strip .icon-liked")),

        reviewText=_clean_text(body),
        reviewHtml=_inner_html(body),
        fullTextUrl=_attr(body, "data-full-text-url"),

        likesCount=_int_or_none(_attr(like_component, "data-count")),
        likesUrl= BASE_URL + _attr(like_component, "data-likes-page"),

        commentsCount=_int_from_node(comments_link.select_one(".label") if comments_link else None),
        commentsUrl=_attr(comments_link, "href"),

        tags=[],
    )


def _review_from_full_page(
    soup: BeautifulSoup,
    *,
    page_film_context: dict[str, Any],
    page_review_count: int | None,
    page_reviews_url: str | None,
) -> ReviewSummary | None:
    section = soup.select_one("section.review.js-review")

    if not section:
        return None

    body = section.select_one(".js-review-body")

    if not body:
        return None

    og_url = soup.select_one('meta[property="og:url"][content]')
    review_href = _path_from_url(_attr(og_url, "content"))

    owner_link = section.select_one(".person-summary a.name[href], .person-summary strong.name a[href]")
    owner = _owner_from_href(_attr(owner_link, "href")) or _owner_from_href(review_href)

    owner_display_name = _clean_text(owner_link)

    film_context = _film_context_from_container(section)
    film_context = _merge_film_context(film_context, page_film_context)

    film_slug = film_context.get("filmSlug") or _film_slug_from_review_href(review_href)

    like_component = section.select_one(".like-link-target[data-likeable-identifier]")
    review_lid, viewing_uid, viewing_id = _review_identity_from_like_component(section)

    rating_text = _rating_text_from_container(section)

    comments_heading = soup.select_one("[data-comments-link]")

    reviews_url = (
        page_reviews_url
        or _reviews_url(owner, film_slug)
    )

    return ReviewSummary(
        owner=owner,
        ownerDisplayName=owner_display_name,

        filmSlug=film_slug,
        filmName=film_context.get("filmName"),
        filmYear=film_context.get("filmYear"),
        filmLid=film_context.get("filmLid"),
        filmUid=film_context.get("filmUid"),

        viewingUid=viewing_uid,
        viewingId=viewing_id,
        reviewLid=review_lid,

        reviewUrl=review_href,
        reviewsUrl=reviews_url,
        reviewCountForFilm=page_review_count,
        isNumberedReview=_is_numbered_review_url(review_href),

        date=_date_from_full_review(section),
        viewingAction=_viewing_action_from_text(_clean_text(section.select_one(".view-date"))),

        rating=_rating_float(rating_text),
        ratingText=rating_text,
        likedFilm=bool(section.select_one(".content-reactions-strip .inline-liked, .content-reactions-strip .icon-liked")),

        reviewText=_clean_text(body),
        reviewHtml=_inner_html(body),
        fullTextUrl=_attr(body, "data-full-text-url"),

        likesCount=_int_or_none(_attr(like_component, "data-count")),
        likesUrl=BASE_URL + _attr(like_component, "data-likes-page"),

        commentsCount=_int_from_text(_clean_text(comments_heading)),
        commentsUrl=_attr(comments_heading, "data-comments-link"),

        tags=[_clean_text(a) for a in section.select("ul.tags a")],
    )


def _film_context_from_container(container: Tag | BeautifulSoup) -> dict[str, Any]:
    poster = container.select_one('[data-component-class="LazyPoster"][data-item-slug]')

    if not poster:
        return {}

    ident = _json_attr(poster, "data-postered-identifier")

    name = (
        _clean_text(container.select_one(".inline-production-masthead .primaryname a, .primaryname a"))
        or poster.get("data-item-name")
        or poster.get("data-item-full-display-name")
    )

    return {
        "filmSlug": poster.get("data-item-slug"),
        "filmName": name,
        "filmYear": _int_from_node(container.select_one(".releasedate a")),
        "filmLid": ident.get("lid"),
        "filmUid": ident.get("uid"),
    }


def _film_context_from_page(soup: BeautifulSoup) -> dict[str, Any]:
    header = soup.select_one(".production-page-header")

    if header:
        context = _film_context_from_container(header)
        if context:
            return context

    return _film_context_from_container(soup)


def _merge_film_context(
    primary: dict[str, Any],
    fallback: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(fallback)

    for key, value in primary.items():
        if value is not None:
            merged[key] = value

    return merged


def _review_identity_from_like_component(container: Tag) -> tuple[str | None, str | None, int | None]:
    like_component = container.select_one(".like-link-target[data-likeable-identifier]")
    ident = _json_attr(like_component, "data-likeable-identifier")

    review_lid = ident.get("lid")
    viewing_uid = ident.get("uid")
    viewing_id = _viewing_id_from_uid(viewing_uid)

    return review_lid, viewing_uid, viewing_id


def _review_count_and_url_from_page(soup: BeautifulSoup) -> tuple[int | None, str | None]:
    """
    Full review pages expose the useful disambiguation link in the sidebar:

      reviewed <span class="count">2</span>

    The film-specific reviews page may expose count text in nav tooltip metadata.
    """
    action_link = soup.select_one('span.action-large.-watch a[href*="/reviews/"]')

    if action_link:
        return (
            _int_from_node(action_link.select_one(".count")),
            action_link.get("href"),
        )

    for link in soup.select('a[href*="/reviews/"][data-original-title]'):
        title = link.get("data-original-title") or ""

        if re.search(r"\breviews?\b", title, flags=re.I):
            count = _int_from_text(title)

            if count is not None:
                return count, link.get("href")

    return None, None


def _rating_text_from_container(container: Tag) -> str | None:
    svg = container.select_one(".inline-rating svg[aria-label], svg.-rating[aria-label]")

    if svg and svg.get("aria-label"):
        return svg.get("aria-label", "").strip() or None

    title = container.select_one(".inline-rating svg title, svg.-rating title")

    if title:
        text = _clean_text(title)
        return text or None

    rating = container.select_one("span.rating")

    if rating:
        text = _clean_text(rating)

        if text:
            return text

        for class_name in rating.get("class", []):
            match = re.fullmatch(r"rated-(\d+)", class_name)

            if match:
                return str(int(match.group(1)) / 2)

    return None


def _rating_float(rating_text: str | None) -> float | None:
    if not rating_text:
        return None

    if "★" in rating_text or "½" in rating_text:
        return float(rating_text.count("★")) + (0.5 if "½" in rating_text else 0.0)

    try:
        return float(rating_text)
    except ValueError:
        return None


def _date_from_full_review(section: Tag) -> str | None:
    """
    Full review pages often show the diary date as three links:
      /diary/for/YYYY/MM/DD/
      /diary/for/YYYY/MM/
      /diary/for/YYYY/
    """
    for link in section.select(".view-date a[href]"):
        href = link.get("href") or ""
        match = re.search(r"/diary/for/(\d{4})/(\d{2})/(\d{2})/", href)

        if match:
            year, month, day = match.groups()
            return f"{year}-{month}-{day}"

    time_node = section.select_one("time.timestamp[datetime]")

    if time_node:
        return time_node.get("datetime")

    return None


def _viewing_action_from_text(text: str | None) -> str | None:
    if not text:
        return None

    if "Rewatched" in text:
        return "Rewatched"

    if "Watched" in text:
        return "Watched"

    return None


def _reviews_url(owner: str | None, film_slug: str | None) -> str | None:
    if not owner or not film_slug:
        return None

    return f"/{owner}/film/{film_slug}/reviews/"


def _owner_from_href(href: str | None) -> str | None:
    bits = _path_bits(href)

    if not bits:
        return None

    if bits[0] == "film":
        return None

    return bits[0]


def _owner_display_name_from_context(context_link: Tag | None) -> str | None:
    if not context_link:
        return None

    owner = context_link.select_one(".owner .displayname")

    if owner:
        return _clean_text(owner)

    return None


def _film_slug_from_review_href(href: str | None) -> str | None:
    bits = _path_bits(href)

    if len(bits) >= 3 and bits[1] == "film":
        return bits[2]

    if len(bits) >= 2 and bits[0] == "film":
        return bits[1]

    return None


def _is_numbered_review_url(href: str | None) -> bool:
    bits = _path_bits(href)

    return (
        len(bits) >= 4
        and bits[1] == "film"
        and bits[3].isdigit()
    )


def _viewing_id_from_uid(uid: str | None) -> int | None:
    if not uid:
        return None

    match = re.search(r"viewing:(\d+)", uid)

    if not match:
        return None

    return int(match.group(1))


def _path_bits(href: str | None) -> list[str]:
    if not href:
        return []

    return [bit for bit in urlparse(href).path.split("/") if bit]


def _path_from_url(url: str | None) -> str | None:
    if not url:
        return None

    parsed = urlparse(url)

    if parsed.scheme or parsed.netloc:
        return parsed.path

    return url


def _json_attr(node: Tag | None, attr: str) -> dict[str, Any]:
    if not node:
        return {}

    raw = node.get(attr)

    if not raw:
        return {}

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _attr(node: Tag | None, attr: str) -> str | None:
    if not node:
        return None

    value = node.get(attr)

    if value is None:
        return None

    return str(value).strip()


def _clean_text(node: Tag | None) -> str:
    if not node:
        return ""

    return " ".join(node.get_text(" ", strip=True).split())


def _inner_html(node: Tag | None) -> str | None:
    if not node:
        return None

    html = "".join(str(child) for child in node.children).strip()

    return html or None


def _int_from_node(node: Tag | None) -> int | None:
    if not node:
        return None

    return _int_from_text(_clean_text(node))


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None

    return _int_from_text(str(value))


def _int_from_text(text: str | None) -> int | None:
    if not text:
        return None

    match = re.search(r"\d+", text.replace(",", ""))

    if not match:
        return None

    return int(match.group(0))


__all__ = [
    "ReviewSummary",
    "extract_reviews",
]