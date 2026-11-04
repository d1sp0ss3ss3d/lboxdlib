"""
Letterboxd activity-page extraction helpers.

This module parses the activity DOM that Letterboxd returns from activity pages
and activity pagination fragments. It deliberately separates full embedded
review rows from compact rows that merely reference a review.

Public entry points:
    extract_activity(html_doc, *, session, ...)
        Parse one activity page/fragment. If hydrate_likes=True, full embedded
        ReviewActivity rows are hydrated through /ajax/letterboxd-metadata/.

    fetch_activities(session, url, start=1, pages=20, verbose=False, ...)
        Fetch activity pages through the specialized activity AJAX paginator,
        parse each fragment, then hydrate all ReviewActivity liked states in one pass.

Core boundary:
    ReviewActivity is an embedded review/viewing object:
        section.activity-row[data-activity-id]
        + article.production-viewing
        + LazyPoster film object
        + review LikeComponent / viewing uid

    ReviewReferenceActivity is a compact feed event about a review:
        "X liked Y's review of Film"
        "X commented on Y's review of Film"

Only ReviewActivity has likedReviewByMe. Compact review references do not have
review LikeComponent data, so they cannot directly hydrate review liked state.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, TypeAlias
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from lboxdlib.pronouns import infer_pronouns_from_activity_text

BASE_URL = "https://letterboxd.com"
METADATA_ENDPOINT = f"{BASE_URL}/ajax/letterboxd-metadata/"
MODULE_VERSION = "activity_from_page_incoming_v5_20260804"


class ActivityParseError(RuntimeError):
    """Raised when a row cannot be parsed according to the declared DOM contract."""


class ActivityHydrationError(RuntimeError):
    """Raised when review liked-state hydration fails or is incomplete."""


class ActivityKind(str, Enum):
    REVIEW = "review"
    REVIEW_REFERENCE = "review_reference"
    DIARY = "diary"
    WATCHLIST = "watchlist"
    LIST = "list"
    FOLLOW = "follow"
    BASIC = "basic"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class ActivityActor:
    slug: str | None
    displayName: str | None
    url: str | None = None
    avatarUrl: str | None = None
    # Activity templates can establish pronoun usage, not gender identity.
    # ``gender`` remains available for explicit information supplied elsewhere.
    pronouns: str | None = None
    gender: str | None = None


@dataclass(slots=True)
class FilmRef:
    slug: str | None
    name: str | None
    year: int | None = None
    lid: str | None = None
    uid: str | None = None
    url: str | None = None


@dataclass(slots=True)
class ActivityBase:
    kind: ActivityKind
    activityId: str | None
    actor: ActivityActor | None
    datetime: str | None
    rowClasses: tuple[str, ...] = field(default_factory=tuple)
    rawText: str | None = None
    rawHtml: str | None = None


@dataclass(slots=True)
class ReviewActivity(ActivityBase):
    ownerSlug: str | None = None
    ownerDisplayName: str | None = None

    film: FilmRef | None = None

    reviewUrl: str | None = None
    reviewLid: str | None = None
    viewingUid: str | None = None
    viewingId: int | None = None

    rating: float | None = None
    ratingText: str | None = None

    viewingAction: str | None = None
    reviewText: str | None = None
    reviewHtml: str | None = None
    fullTextUrl: str | None = None

    # This is whether the review author liked the film/viewing.
    # It is separate from the signed-in viewer liking the review.
    likedFilm: bool | None = None

    # This is the signed-in viewer's liked state for the review itself.
    # It is hydrated from /ajax/letterboxd-metadata/ using likeables=viewing:<id>.
    likedReviewByMe: bool | None = None
    needsHydrationForLikeState: bool = True

    likesCount: int | None = None
    likesUrl: str | None = None

    commentsCount: int | None = None
    commentsUrl: str | None = None

    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ReviewReferenceActivity(ActivityBase):
    """Compact row that references a review but does not embed the review object."""

    action: str | None = None  # liked_review_reference / commented_on_review_reference

    reviewOwnerSlug: str | None = None
    reviewOwnerDisplayName: str | None = None

    film: FilmRef | None = None
    reviewUrl: str | None = None

    rating: float | None = None
    ratingText: str | None = None

    commentId: str | None = None
    commentUid: str | None = None
    commentText: str | None = None
    commentHtml: str | None = None
    commentFullTextUrl: str | None = None


@dataclass(slots=True)
class DiaryActivity(ActivityBase):
    """Basic activity row for a diary/rated viewing without an embedded review."""

    ownerSlug: str | None = None
    ownerDisplayName: str | None = None

    film: FilmRef | None = None

    # For non-review diary/viewing rows this is the user film/viewing URL,
    # e.g. /celfromhell/film/the-ugly-stepsister/.
    targetUrl: str | None = None

    rating: float | None = None
    ratingText: str | None = None

    viewingAction: str | None = None
    likedFilm: bool | None = None
    diaryDateText: str | None = None


@dataclass(slots=True)
class WatchlistActivity(ActivityBase):
    action: str | None = None
    film: FilmRef | None = None
    targetUrl: str | None = None


@dataclass(slots=True)
class ListActivity(ActivityBase):
    action: str | None = None
    listOwnerSlug: str | None = None
    listSlug: str | None = None
    listName: str | None = None
    listUrl: str | None = None


@dataclass(slots=True)
class FollowActivity(ActivityBase):
    action: str | None = None
    targetSlug: str | None = None
    targetDisplayName: str | None = None
    targetUrl: str | None = None


@dataclass(slots=True)
class BasicActivity(ActivityBase):
    action: str | None = None
    targetUrl: str | None = None
    targetText: str | None = None
    reason: str | None = None


@dataclass(slots=True)
class UnknownActivity(ActivityBase):
    reason: str | None = None


ActivityItem: TypeAlias = (
    ReviewActivity
    | ReviewReferenceActivity
    | DiaryActivity
    | WatchlistActivity
    | ListActivity
    | FollowActivity
    | BasicActivity
    | UnknownActivity
)


@dataclass(slots=True)
class MetadataHydrationResult:
    requestedLikeables: set[str]
    likedLikeables: set[str]
    requestedProductions: set[str]
    csrf: str | None = None
    raw: dict[str, Any] | None = None


# ============================================================
# Public API
# ============================================================


def extract_activity(
    html_doc: str,
    *,
    session: Any | None = None,
    include_raw_html: bool = False,
    hydrate_likes: bool = True,
    strict_hydration: bool = True,
    metadata_batch_size: int = 100,
    referer: str = "https://letterboxd.com/activity/",
) -> list[ActivityItem]:
    """Parse one Letterboxd activity page/fragment.

    ``session`` is only required when ``hydrate_likes=True`` and at least one
    full embedded ReviewActivity row is present.

    For full ReviewActivity rows, hydrate_likes=True means likedReviewByMe must
    become True or False. Leaving it as None is treated as an error in strict
    mode.
    """

    soup = BeautifulSoup(_repair_surrogates(html_doc or ""), "lxml")
    items = [
        activity_from_row(row, include_raw_html=include_raw_html)
        for row in iter_activity_rows_from_soup(soup)
    ]

    if hydrate_likes:
        hydrate_review_likes(
            session,
            items,
            strict=strict_hydration,
            batch_size=metadata_batch_size,
            referer=referer,
        )

    return items


# Backward-compatible alias for older wrapper code.
def extract_activity_items(
    html_doc: str,
    *,
    session: Any | None = None,
    include_raw_html: bool = False,
    hydrate_likes: bool = True,
    strict_hydration: bool = True,
    metadata_batch_size: int = 100,
    referer: str = "https://letterboxd.com/activity/",
) -> list[ActivityItem]:
    return extract_activity(
        html_doc,
        session=session,
        include_raw_html=include_raw_html,
        hydrate_likes=hydrate_likes,
        strict_hydration=strict_hydration,
        metadata_batch_size=metadata_batch_size,
        referer=referer,
    )


def iter_activity_rows(html_doc: str) -> list[Tag]:
    soup = BeautifulSoup(_repair_surrogates(html_doc or ""), "lxml")
    return iter_activity_rows_from_soup(soup)


def iter_activity_rows_from_soup(soup: BeautifulSoup | Tag) -> list[Tag]:
    """Return real activity rows only.

    Ad placeholders may have classes like ``activity-row -review`` but are divs
    with no data-activity-id. They are intentionally ignored.
    """

    return list(soup.select("section.activity-row[data-activity-id]"))


def activity_from_row(row: Tag, *, include_raw_html: bool = False) -> ActivityItem:
    if _is_embedded_review_row(row):
        return _review_activity_from_row(row, include_raw_html=include_raw_html)

    if _is_diary_activity_row(row):
        return _diary_activity_from_row(row, include_raw_html=include_raw_html)

    if _is_watchlist_row(row):
        return _watchlist_activity_from_row(row, include_raw_html=include_raw_html)

    if _is_review_comment_reference_row(row):
        return _review_reference_activity_from_row(
            row,
            include_raw_html=include_raw_html,
            action="commented_on_review_reference",
        )

    if _is_liked_review_reference_row(row):
        return _review_reference_activity_from_row(
            row,
            include_raw_html=include_raw_html,
            action="liked_review_reference",
        )

    if _is_list_like_row(row):
        return _list_activity_from_row(row, include_raw_html=include_raw_html)

    if _is_follow_row(row):
        return _follow_activity_from_row(row, include_raw_html=include_raw_html)

    return _basic_activity_from_row(
        row,
        include_raw_html=include_raw_html,
        reason="unrecognized supported activity shape",
    )


def hydrate_review_likes(
    session: Any | None,
    items: Iterable[ActivityItem],
    *,
    strict: bool = True,
    batch_size: int = 100,
    referer: str = "https://letterboxd.com/activity/",
) -> list[MetadataHydrationResult]:
    """Hydrate likedReviewByMe for full ReviewActivity items.

    Full ReviewActivity rows contain ``viewing:<id>`` values. The metadata
    endpoint returns the subset of requested viewing IDs liked by the signed-in
    viewer. Therefore, for every requested viewingUid:

        uid in response["liked"] -> True
        uid absent from response["liked"] -> False

    Compact ReviewReferenceActivity rows are ignored because they do not contain
    viewing UIDs.
    """

    materialized = list(items)
    reviews = [item for item in materialized if isinstance(item, ReviewActivity)]

    if not reviews:
        return []

    if session is None:
        if strict:
            raise ActivityHydrationError(
                "hydrate_review_likes requires a session when ReviewActivity rows are present"
            )
        return []

    missing_uid = [r for r in reviews if not r.viewingUid]
    if missing_uid and strict:
        details = ", ".join(
            f"activityId={r.activityId} reviewUrl={r.reviewUrl}" for r in missing_uid[:10]
        )
        raise ActivityHydrationError(f"ReviewActivity rows missing viewingUid: {details}")

    all_likeables = _dedupe_keep_order(r.viewingUid for r in reviews if r.viewingUid)
    production_by_likeable = {
        r.viewingUid: r.film.uid
        for r in reviews
        if r.viewingUid and r.film and r.film.uid
    }

    if not all_likeables:
        return []

    _assert_signed_in_session(session, strict=strict)

    results: list[MetadataHydrationResult] = []
    liked_total: set[str] = set()
    requested_total: set[str] = set()

    for likeables_batch in _chunks(all_likeables, max(1, batch_size)):
        # Productions are film metadata, not review liked state. Include only the
        # productions corresponding to this likeables batch rather than repeating
        # every production across every metadata request.
        productions_batch = _dedupe_keep_order(
            production_by_likeable.get(uid) for uid in likeables_batch
        )
        result = _post_letterboxd_metadata(
            session,
            likeables=likeables_batch,
            productions=productions_batch,
            referer=referer,
        )
        results.append(result)
        requested_total.update(result.requestedLikeables)
        liked_total.update(result.likedLikeables)

    review_by_uid = {r.viewingUid: r for r in reviews if r.viewingUid}

    for uid, review in review_by_uid.items():
        if uid not in requested_total:
            if strict:
                raise ActivityHydrationError(
                    f"ReviewActivity was not sent to metadata hydration: "
                    f"activityId={review.activityId} viewingUid={uid} reviewUrl={review.reviewUrl}"
                )
            continue
        review.likedReviewByMe = uid in liked_total
        review.needsHydrationForLikeState = False

    if strict:
        unknown = [r for r in reviews if r.likedReviewByMe is None]
        if unknown:
            details = ", ".join(
                f"activityId={r.activityId} viewingUid={r.viewingUid} reviewUrl={r.reviewUrl}"
                for r in unknown[:10]
            )
            raise ActivityHydrationError(
                "ReviewActivity liked state remained unknown after hydration: " + details
            )

    return results


def as_dicts(items: Iterable[ActivityItem]) -> list[dict[str, Any]]:
    return [asdict(item) for item in items]


# ============================================================
# Classifiers
# ============================================================


def _is_embedded_review_row(row: Tag) -> bool:
    article = row.select_one("article.production-viewing")
    if not article:
        return False

    if not _find_component(article, "LazyPoster"):
        return False

    like_component = _review_like_component(article)
    if like_component:
        return True

    # Fallback: some rendered variants expose the review identity directly on the
    # article and may lazy-render the LikeComponent later. This still identifies
    # an embedded review object, but hydration will fail loudly if viewingUid is
    # missing.
    object_name = (_attr(article, "data-object-name") or "").lower()
    object_id = _attr(article, "data-object-id") or ""
    return object_name == "review" and object_id.startswith("viewing:")


def _is_liked_review_reference_row(row: Tag) -> bool:
    if not _has_class(row, "-basic"):
        return False
    if row.select_one("article.production-viewing"):
        return False

    summary = row.select_one(".activity-summary")
    text = _clean_text(summary).lower()
    target = summary.select_one("a.target[href]") if summary else None
    href = _attr(target, "href")

    return (
        bool(target and href and "/film/" in href)
        and " liked " in f" {text} "
        and "review of" in text
    )


def _is_review_comment_reference_row(row: Tag) -> bool:
    if not _has_class(row, "-basic"):
        return False
    if row.select_one("article.production-viewing"):
        return False

    summary = row.select_one(".activity-summary")
    text = _clean_text(summary).lower()
    target = summary.select_one("a.target[href]") if summary else None
    href = _attr(target, "href")

    return (
        bool(target and href and "/film/" in href)
        and "commented on" in text
        and "review of" in text
    )


def _is_diary_activity_row(row: Tag) -> bool:
    """Return True for basic watched/rated diary/viewing activity rows.

    Example:
        Cel watched, liked and rated The Ugly Stepsister ★★★★½

    This intentionally excludes compact review-reference rows such as:
        X liked Y's ★★★★ review of Film
    because those are represented as ReviewReferenceActivity.
    """
    if not _has_class(row, "-basic"):
        return False
    if row.select_one("article.production-viewing"):
        return False

    summary = row.select_one(".activity-summary")
    if not summary:
        return False

    text = _clean_text(summary).lower()
    target = summary.select_one("a.target[href]")
    href = _attr(target, "href")
    bits = _path_bits(href)

    if not target or len(bits) < 3 or bits[1] != "film":
        return False

    # Exclude compact social/reference events even if they contain stars.
    if "review of" in text or "commented on" in text or " liked " in f" {text} " and "review" in text:
        return False

    return (
        "watched" in text
        or "rewatched" in text
        or "rated" in text
        or _rating_text_from_container(summary) is not None
    )


def _is_watchlist_row(row: Tag) -> bool:
    if not _has_class(row, "-basic"):
        return False

    summary = row.select_one(".activity-summary")
    text = _clean_text(summary).lower()
    target = summary.select_one("a.target[href]") if summary else None
    href = _attr(target, "href")

    return bool(target and href and href.startswith("/film/") and "watchlist" in text)


def _is_list_like_row(row: Tag) -> bool:
    if not _has_class(row, "-basic"):
        return False

    summary = row.select_one(".activity-summary")
    text = _clean_text(summary).lower()
    target = summary.select_one("a.target[href]") if summary else None
    href = _attr(target, "href")

    return bool(target and href and "/list/" in href and " liked " in f" {text} ")


def _is_follow_row(row: Tag) -> bool:
    if not _has_class(row, "-basic"):
        return False

    summary = row.select_one(".activity-summary")
    text = _clean_text(summary).lower()
    if " followed " not in f" {text} ":
        return False

    # Outgoing/member feeds normally link the followed member as a.target.
    # Incoming rows such as "wallesa followed you" contain only the actor
    # name/avatar link, so the text plus a parseable actor is sufficient.
    target = summary.select_one("a.target[href]") if summary else None
    return bool(target or _actor_from_row(row, summary=summary))


# ============================================================
# Row parsers
# ============================================================


def _review_activity_from_row(row: Tag, *, include_raw_html: bool) -> ReviewActivity:
    article = row.select_one("article.production-viewing")
    if not article:
        raise ActivityParseError(f"Review row missing article.production-viewing: {row}")

    poster = _find_component(article, "LazyPoster")
    like_component = _review_like_component(article)
    like_ident = _json_attr(like_component, "data-likeable-identifier")

    article_object_id = _attr(article, "data-object-id")
    viewing_uid = _str_or_none(like_ident.get("uid")) or _str_or_none(article_object_id)
    viewing_id = _viewing_id_from_uid(viewing_uid)
    review_lid = _str_or_none(like_ident.get("lid"))

    primary_link = article.select_one(
        ".inline-production-masthead .primaryname a[href], .primaryname a[href]"
    )
    review_url = _attr(primary_link, "href")

    owner_slug = _normalize_slug(_attr(article, "data-owner")) or _owner_from_href(
        _attr(article.select_one(".attribution-detail a.owner[href]"), "href")
    )
    owner_display = (
        _clean_text(article.select_one(".attribution-detail .displayname"))
        or _clean_text(article.select_one(".attribution-detail a.owner"))
        or owner_slug
    )

    body = article.select_one(".js-review-body")
    rating_text = _rating_text_from_container(article)
    comments_link = article.select_one('a.metadata[href*="#comments"]')

    base = _base_fields(row, kind=ActivityKind.REVIEW, include_raw_html=include_raw_html)

    return ReviewActivity(
        **base,
        ownerSlug=owner_slug,
        ownerDisplayName=owner_display,
        film=_film_ref_from_lazy_poster(poster, article=article),
        reviewUrl=review_url,
        reviewLid=review_lid,
        viewingUid=viewing_uid,
        viewingId=viewing_id,
        rating=_rating_float(rating_text),
        ratingText=rating_text,
        viewingAction=_viewing_action_from_text(_clean_text(article.select_one(".attribution-detail"))),
        reviewText=_clean_text(body) or None,
        reviewHtml=_inner_html(body),
        fullTextUrl=_attr(body, "data-full-text-url"),
        likedFilm=bool(
            article.select_one(
                ".content-reactions-strip .inline-liked, "
                ".content-reactions-strip .icon-liked, "
                ".content-reactions-strip svg[aria-label='Liked']"
            )
        ),
        likedReviewByMe=None,
        needsHydrationForLikeState=True,
        likesCount=_int_or_none(_attr(like_component, "data-count")),
        likesUrl=_absolute_or_none(_attr(like_component, "data-likes-page")),
        commentsCount=_int_from_node(comments_link.select_one(".label") if comments_link else None),
        commentsUrl=_attr(comments_link, "href"),
        tags=[_clean_text(a) for a in article.select("ul.tags a") if _clean_text(a)],
    )


def _review_reference_activity_from_row(
    row: Tag,
    *,
    include_raw_html: bool,
    action: str,
) -> ReviewReferenceActivity:
    summary = row.select_one(".activity-summary")
    target = summary.select_one("a.target[href]") if summary else None
    href = _attr(target, "href")

    review_owner_slug, film_slug = _owner_and_film_slug_from_review_href(href)
    rating_text = _rating_text_from_container(summary or row)

    actor = _actor_from_row(row, summary=summary)
    owner_display = _review_owner_display_from_basic_summary(summary)
    if not owner_display and action == "commented_on_review_reference":
        context_text = _clean_text(target.select_one(".context") if target else None).lower()
        if "own review" in context_text:
            owner_display = actor.displayName if actor else None

    comment_body = row.select_one("blockquote.activity-comment-text .body-text")
    comment_full_text_url = _attr(comment_body, "data-full-text-url")
    comment_id = _comment_id_from_href(href) or _comment_id_from_full_text_url(comment_full_text_url)

    base = _base_fields(
        row,
        kind=ActivityKind.REVIEW_REFERENCE,
        include_raw_html=include_raw_html,
        actor=actor,
    )

    return ReviewReferenceActivity(
        **base,
        action=action,
        reviewOwnerSlug=review_owner_slug,
        reviewOwnerDisplayName=owner_display,
        film=FilmRef(
            slug=film_slug,
            name=_target_film_name(target),
            url=_film_url_from_slug(film_slug),
        ),
        reviewUrl=href,
        rating=_rating_float(rating_text),
        ratingText=rating_text,
        commentId=comment_id,
        commentUid=f"viewingComment:{comment_id}" if comment_id else None,
        commentText=_clean_text(comment_body) or None,
        commentHtml=_inner_html(comment_body),
        commentFullTextUrl=comment_full_text_url,
    )


def _diary_activity_from_row(row: Tag, *, include_raw_html: bool) -> DiaryActivity:
    summary = row.select_one(".activity-summary")
    target = summary.select_one("a.target[href]") if summary else None
    href = _attr(target, "href")
    owner_slug, film_slug = _owner_and_film_slug_from_review_href(href)
    actor = _actor_from_row(row, summary=summary)
    rating_text = _rating_text_from_container(summary or row)
    summary_text = _clean_text(summary)

    base = _base_fields(
        row,
        kind=ActivityKind.DIARY,
        include_raw_html=include_raw_html,
        actor=actor,
    )

    return DiaryActivity(
        **base,
        ownerSlug=owner_slug or (actor.slug if actor else None),
        ownerDisplayName=(actor.displayName if actor else None),
        film=FilmRef(
            slug=film_slug,
            name=_target_film_name(target),
            url=_film_url_from_slug(film_slug),
        ),
        targetUrl=href,
        rating=_rating_float(rating_text),
        ratingText=rating_text,
        viewingAction=_viewing_action_from_text(summary_text),
        likedFilm=" liked " in f" {summary_text.lower()} " if summary_text else None,
        diaryDateText=_diary_date_text_from_summary(summary),
    )


def _watchlist_activity_from_row(row: Tag, *, include_raw_html: bool) -> WatchlistActivity:
    summary = row.select_one(".activity-summary")
    target = summary.select_one("a.target[href]") if summary else None
    href = _attr(target, "href")
    film_slug = _film_slug_from_film_href(href)

    base = _base_fields(row, kind=ActivityKind.WATCHLIST, include_raw_html=include_raw_html)

    return WatchlistActivity(
        **base,
        action="added_to_watchlist",
        film=FilmRef(
            slug=film_slug,
            name=_clean_text(target) or None,
            url=href,
        ),
        targetUrl=href,
    )


def _list_activity_from_row(row: Tag, *, include_raw_html: bool) -> ListActivity:
    summary = row.select_one(".activity-summary")
    target = summary.select_one("a.target[href]") if summary else None
    href = _attr(target, "href")
    bits = _path_bits(href)

    list_owner_slug = bits[0] if bits else None
    list_slug = None
    if "list" in bits:
        idx = bits.index("list")
        if idx + 1 < len(bits):
            list_slug = bits[idx + 1]

    base = _base_fields(row, kind=ActivityKind.LIST, include_raw_html=include_raw_html)

    return ListActivity(
        **base,
        action="liked_list",
        listOwnerSlug=list_owner_slug,
        listSlug=list_slug,
        listName=_clean_text(target) or None,
        listUrl=href,
    )


def _follow_activity_from_row(row: Tag, *, include_raw_html: bool) -> FollowActivity:
    summary = row.select_one(".activity-summary")
    target = summary.select_one("a.target[href]") if summary else None
    href = _attr(target, "href")
    actor = _actor_from_row(row, summary=summary)
    summary_text = _clean_text(summary).lower()

    # Incoming "X followed you" rows do not contain a.target. In that shape
    # the actor is the member who performed the follow and is the useful target
    # identity for callers.
    incoming_follow = target is None and "followed you" in summary_text
    if incoming_follow and actor is not None:
        href = actor.url
        target_slug = actor.slug
        target_display = actor.displayName
        action = "followed_you"
    else:
        target_slug = _owner_from_href(href)
        target_display = _clean_text(target) or None
        action = "followed_member"

    base = _base_fields(
        row,
        kind=ActivityKind.FOLLOW,
        include_raw_html=include_raw_html,
        actor=actor,
    )

    return FollowActivity(
        **base,
        action=action,
        targetSlug=target_slug,
        targetDisplayName=target_display,
        targetUrl=href,
    )


def _basic_activity_from_row(
    row: Tag,
    *,
    include_raw_html: bool,
    reason: str | None = None,
) -> BasicActivity:
    summary = row.select_one(".activity-summary")
    target = summary.select_one("a.target[href]") if summary else None

    base = _base_fields(row, kind=ActivityKind.BASIC, include_raw_html=include_raw_html)

    return BasicActivity(
        **base,
        action=None,
        targetUrl=_attr(target, "href"),
        targetText=_clean_text(target) or None,
        reason=reason,
    )


# ============================================================
# Metadata hydration
# ============================================================


def _post_letterboxd_metadata(
    session: Any,
    *,
    likeables: list[str],
    productions: list[str] | None = None,
    referer: str = "https://letterboxd.com/activity/",
) -> MetadataHydrationResult:
    _ensure_csrf_cookie(session)

    payload: list[tuple[str, str]] = []
    for production in productions or []:
        payload.append(("productions", production))
    for likeable in likeables:
        payload.append(("likeables", likeable))

    response = session.post(
        METADATA_ENDPOINT,
        data=payload,
        headers={
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Origin": BASE_URL,
            "Referer": referer,
        },
    )

    # requests and curl_cffi both expose raise_for_status().
    if hasattr(response, "raise_for_status"):
        response.raise_for_status()

    try:
        data = response.json()
    except Exception as exc:  # noqa: BLE001 - library response type varies by session implementation
        text = getattr(response, "text", "")
        raise ActivityHydrationError(
            f"Unable to decode Letterboxd metadata JSON response: {text[:500]!r}"
        ) from exc

    if not isinstance(data, dict):
        raise ActivityHydrationError(f"Unexpected metadata response type: {type(data).__name__}")

    if data.get("result") is not True:
        raise ActivityHydrationError(f"Letterboxd metadata request failed: {data!r}")

    liked_raw = data.get("liked", [])
    if liked_raw is None:
        liked_raw = []
    if not isinstance(liked_raw, list):
        raise ActivityHydrationError(f"Unexpected metadata liked field: {liked_raw!r}")

    requested_likeables = set(likeables)
    liked_likeables = {str(uid) for uid in liked_raw if str(uid) in requested_likeables}

    return MetadataHydrationResult(
        requestedLikeables=requested_likeables,
        likedLikeables=liked_likeables,
        requestedProductions=set(productions or []),
        csrf=_str_or_none(data.get("csrf")),
        raw=data,
    )


def _ensure_csrf_cookie(session: Any) -> str | None:
    # The observed metadata payload does not include a __csrf form field; the
    # Letterboxd session/cookies carry the token. Calling get_csrf keeps this
    # module aligned with lboxdlib.auth's CSRF discovery without guessing a
    # hidden form field that was not present in the captured request body.
    try:
        from lboxdlib.auth import get_csrf
    except Exception:
        return None

    return get_csrf(session)


def _assert_signed_in_session(session: Any, *, strict: bool) -> str | None:
    try:
        from lboxdlib.auth import get_signed_in_user
    except Exception:
        return None

    try:
        username = get_signed_in_user(session)
    except Exception as exc:  # noqa: BLE001
        if strict:
            raise ActivityHydrationError("Unable to determine signed-in Letterboxd user") from exc
        return None

    if strict and not username:
        raise ActivityHydrationError("Review liked-state hydration requires a signed-in session")

    return _str_or_none(username)


# ============================================================
# Shared parsing helpers
# ============================================================


def _find_component(container: Tag | BeautifulSoup | None, component_class: str) -> Tag | None:
    if not container:
        return None
    for node in container.select("[data-component-class]"):
        if (_attr(node, "data-component-class") or "").strip() == component_class:
            return node
    return None


def _review_like_component(container: Tag | BeautifulSoup | None) -> Tag | None:
    if not container:
        return None

    candidates: list[Tag] = []

    for scope_selector in (".review-actions", ""):
        scope = container.select_one(scope_selector) if scope_selector else container
        if not scope:
            continue
        for component in scope.select("[data-likeable-identifier]"):
            component_class = (_attr(component, "data-component-class") or "").strip()
            class_names = {str(c).strip() for c in component.get("class", [])}
            if component_class != "LikeComponent" and "like-link-target" not in class_names:
                continue
            if component not in candidates:
                candidates.append(component)

    for component in candidates:
        ident = _json_attr(component, "data-likeable-identifier")
        if _is_review_like_identifier(ident):
            return component

    return None


def _is_review_like_identifier(ident: dict[str, Any]) -> bool:
    uid = str(ident.get("uid", "")).strip()
    type_value = str(ident.get("type", "")).strip().lower()
    type_name = str(ident.get("typeName", "")).strip().lower()
    return uid.startswith("viewing:") and type_value == "viewing" and type_name == "review"


def _film_ref_from_lazy_poster(poster: Tag | None, *, article: Tag | None = None) -> FilmRef | None:
    if not poster:
        return None

    ident = _json_attr(poster, "data-postered-identifier")

    name = (
        _str_or_none(poster.get("data-item-name"))
        or _str_or_none(poster.get("data-item-full-display-name"))
        or _clean_text(article.select_one(".primaryname a") if article else None)
        or None
    )

    return FilmRef(
        slug=_str_or_none(poster.get("data-item-slug")),
        name=name,
        year=_int_from_node(article.select_one(".releasedate a") if article else None),
        lid=_str_or_none(ident.get("lid")),
        uid=_str_or_none(ident.get("uid")),
        url=_str_or_none(poster.get("data-item-link")),
    )


def _diary_date_text_from_summary(summary: Tag | None) -> str | None:
    if not summary:
        return None

    text = _clean_text(summary)
    if not text:
        return None

    # Common activity form: "... on Saturday Mar 21, 2026". This is display
    # text rather than a machine-readable diary date, but it is useful context.
    match = re.search(r"\bon\s+(.+)$", text)
    if match:
        return match.group(1).strip() or None
    return None


def _rating_text_from_container(container: Tag | BeautifulSoup | None) -> str | None:
    if not container:
        return None

    svg = container.select_one(".inline-rating svg[aria-label], svg.-rating[aria-label]")
    if svg and svg.get("aria-label"):
        return str(svg.get("aria-label", "")).strip() or None

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
            match = re.fullmatch(r"rated-(\d+)", str(class_name))
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


def _viewing_id_from_uid(uid: str | None) -> int | None:
    if not uid:
        return None
    match = re.fullmatch(r"viewing:(\d+)", uid.strip())
    return int(match.group(1)) if match else None


def _viewing_action_from_text(text: str | None) -> str | None:
    if not text:
        return None
    lowered = text.lower()
    if "rewatched" in lowered:
        return "Rewatched"
    if "watched" in lowered:
        return "Watched"
    return None


def _review_owner_display_from_basic_summary(summary: Tag | None) -> str | None:
    if not summary:
        return None

    # In compact liked-review rows, the review owner is usually:
    #   <strong class="name">Owner’s</strong>
    nodes = summary.select("strong.name")
    if not nodes:
        return None

    text = _clean_text(nodes[0])
    return _strip_possessive(text) or None


def _target_film_name(target: Tag | None) -> str | None:
    if not target:
        return None

    cloned = BeautifulSoup(str(target), "lxml")
    cloned_target = cloned.select_one("a.target") or cloned

    for context in cloned_target.select(".context"):
        context.decompose()

    text = _clean_text(cloned_target)
    return text or None


def _base_fields(
    row: Tag,
    *,
    kind: ActivityKind,
    include_raw_html: bool,
    actor: ActivityActor | None = None,
) -> dict[str, Any]:
    summary = row.select_one(".activity-summary")

    return {
        "kind": kind,
        "activityId": _attr(row, "data-activity-id"),
        "actor": actor or _actor_from_row(row, summary=summary),
        "datetime": _attr(row.select_one("time[datetime]"), "datetime"),
        "rowClasses": tuple(str(c) for c in row.get("class", [])),
        "rawText": _clean_text(row) or None,
        "rawHtml": str(row) if include_raw_html else None,
    }


def _actor_from_row(row: Tag, *, summary: Tag | None = None) -> ActivityActor | None:
    avatar_link = row.select_one(".table-activity-user a.avatar[href]")
    avatar_img = avatar_link.select_one("img[alt]") if avatar_link else None

    summary_link = summary.select_one(":scope > a.name[href]") if summary else None
    if summary_link is None and summary:
        summary_link = summary.select_one("a.name[href]")

    href = _attr(summary_link, "href") or _attr(avatar_link, "href")
    slug = _owner_from_href(href)

    display = _clean_text(summary_link) or _attr(avatar_img, "alt") or None
    avatar_url = _attr(avatar_img, "src")

    if not slug and not display:
        return None

    return ActivityActor(
        slug=slug,
        displayName=display,
        url=href,
        avatarUrl=avatar_url,
        pronouns=infer_pronouns_from_activity_text(_clean_text(summary)),
    )


def _owner_and_film_slug_from_review_href(href: str | None) -> tuple[str | None, str | None]:
    bits = _path_bits(href)
    if len(bits) >= 3 and bits[1] == "film":
        return bits[0], bits[2]
    return None, None


def _comment_id_from_href(href: str | None) -> str | None:
    if not href:
        return None
    fragment = urlparse(href).fragment
    match = re.fullmatch(r"comment-(\d+)", fragment)
    return match.group(1) if match else None


def _comment_id_from_full_text_url(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"viewingComment:(\d+)", url)
    return match.group(1) if match else None


def _has_class(node: Tag | None, class_name: str) -> bool:
    if not node:
        return False
    return class_name in {str(c) for c in node.get("class", [])}


def _clean_text(node: Tag | BeautifulSoup | None) -> str:
    if not node:
        return ""
    return " ".join(node.get_text(" ", strip=True).split())


def _inner_html(node: Tag | BeautifulSoup | None) -> str | None:
    if not node:
        return None
    return "".join(str(child) for child in node.children)


def _attr(node: Tag | BeautifulSoup | None, attr: str) -> str | None:
    if not node:
        return None
    value = node.get(attr)
    return str(value).strip() if value is not None else None


def _json_attr(node: Tag | BeautifulSoup | None, attr: str) -> dict[str, Any]:
    raw = _attr(node, attr)
    if not raw:
        return {}

    raw = html_lib.unescape(raw)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}

    return parsed if isinstance(parsed, dict) else {}


def _path_bits(href: str | None) -> list[str]:
    if not href:
        return []
    return [bit for bit in urlparse(href).path.split("/") if bit]


def _owner_from_href(href: str | None) -> str | None:
    bits = _path_bits(href)
    return bits[0] if bits else None


def _film_slug_from_film_href(href: str | None) -> str | None:
    bits = _path_bits(href)
    if len(bits) >= 2 and bits[0] == "film":
        return bits[1]
    return None


def _film_url_from_slug(slug: str | None) -> str | None:
    return f"/film/{slug}/" if slug else None


def _absolute_or_none(href: str | None) -> str | None:
    if not href:
        return None
    return urljoin(BASE_URL, href)


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    match = re.search(r"\d+", text)
    if not match:
        return None
    try:
        return int(match.group(0))
    except ValueError:
        return None


def _int_from_node(node: Tag | BeautifulSoup | None) -> int | None:
    return _int_or_none(_clean_text(node))


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _strip_possessive(text: str) -> str:
    return re.sub(r"[’']s$", "", text.strip()).strip()


def _normalize_slug(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip().strip("/")
    if not text:
        return None
    if "/" in text:
        bits = _path_bits(text)
        return bits[0] if bits else None
    return text


def _dedupe_keep_order(values: Iterable[str | None]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not value:
            continue
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for i in range(0, len(values), size):
        yield values[i : i + size]


def _repair_surrogates(text: str) -> str:
    """Normalize repr-captured UTF-16 surrogate pairs before lxml parsing."""

    try:
        text.encode("utf-8")
        return text
    except UnicodeEncodeError:
        pass

    try:
        return text.encode("utf-16", "surrogatepass").decode("utf-16")
    except UnicodeError:
        return text.encode("utf-8", "replace").decode("utf-8")
