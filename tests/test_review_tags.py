from dataclasses import dataclass

import pytest
from curl_cffi.requests.exceptions import HTTPError

from lboxdlib import ReviewSummary, TagSummary, fetch_reviews, fetch_tags
from lboxdlib.parsers import extract_tags

BASE = "https://letterboxd.com"
LISTING = f"{BASE}/member/reviews/"
FIRST = "/member/film/example/"
SECOND = "/member/film/example/2/"
CSRF_COOKIE = "com.xk72.webparts.csrf"
MIXED_RESULTS = """
    <li class="search-result -tag">
        <span class="icon-container"></span>
        <h2 class="title-2"><a href="/tag/mormon-horror/">mormon-horror</a></h2>
    </li>
    <li class="search-result -list">
        <h2 class="title-2"><a href="/member/list/mormon-horror/">Mormon Horror</a></h2>
        <a href="/tag/related/">related</a>
    </li>
"""


@dataclass
class Response:
    text: str = ""
    status_code: int = 200
    url: str | None = None

    def raise_for_status(self):
        if self.status_code >= 400:
            raise HTTPError(f"HTTP {self.status_code}")


class Session:
    def __init__(self, responses, *, prime_url=None):
        self.responses = responses
        self.prime_url = prime_url
        self.cookies = {}
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        if url == self.prime_url:
            self.cookies[CSRF_COOKIE] = "test-csrf"
        response = self.responses[url]
        if isinstance(response, OSError):
            raise response
        return Response(response) if isinstance(response, str) else response


def like_component(viewing_id):
    if viewing_id is None:
        return ""
    return f"""
        <span class="like-link-target"
              data-likeable-identifier='{{"uid": "viewing:{viewing_id}", "lid": "lid{viewing_id}"}}'>
        </span>
    """


def card(path, viewing_id):
    return f"""
        <article class="production-viewing" data-owner="member">
            <div class="attribution-detail"><a class="context" href="{path}">Watched</a></div>
            <time class="timestamp" datetime="2026-01-05"></time>
            <div class="js-review-body" data-full-text-url="/s/viewing/{viewing_id}/">Excerpt</div>
            {like_component(viewing_id)}
        </article>
    """


def detail(path, viewing_id, text, tags=""):
    canonical = f'<meta property="og:url" content="{BASE}{path}">' if path else ""
    return f"""
        {canonical}
        <section class="review js-review">
            <div class="js-review-body"><p>{text}</p></div>
            {like_component(viewing_id)}
            {tags}
        </section>
        <aside><ul class="tags"><li><a href="/tag/unrelated/">unrelated</a></li></ul></aside>
    """


def test_listing_fetch_stays_on_the_listing_by_default():
    session = Session({LISTING: card(FIRST, 123)})

    (review,) = fetch_reviews(session, LISTING)

    assert session.urls == [LISTING]
    assert (review.reviewText, review.isFullReview, review.tags, review.tagDetails) == (
        "Excerpt",
        False,
        [],
        [],
    )


def test_full_review_and_standalone_tag_fetch_share_the_same_ordered_tags():
    url = BASE + FIRST
    tags = """
        <ul class="tags">
            <li><a href="/member/tag/found-footage/films/">found footage</a></li>
            <li><a href="/tag/caf%C3%A9/">café</a></li>
        </ul>
    """
    session = Session({url: detail(FIRST, None, "Full text.", tags)})
    expected = [
        TagSummary(
            "found footage", "found-footage", f"{BASE}/member/tag/found-footage/films/", "member"
        ),
        TagSummary("café", "café", f"{BASE}/tag/caf%C3%A9/"),
    ]

    (review,) = fetch_reviews(session, url, full_reviews=True)

    assert session.urls == [url]
    assert review.isFullReview and review.likesUrl is None
    assert review.tags == [tag.name for tag in expected]
    assert review.tagDetails == expected
    assert ReviewSummary.from_dict(review.to_dict()) == review
    assert fetch_tags(session, FIRST) == expected
    assert session.urls == [url, url]


def test_listing_full_reviews_keep_repeat_viewings_and_reuse_detail_requests():
    first_tag = '<ul class="tags"><li><a href="/tag/first/">first</a></li></ul>'
    second_tag = '<ul class="tags"><li><a href="/tag/second/">second</a></li></ul>'
    session = Session(
        {
            LISTING: card(SECOND, 456) + card(FIRST, 123) + card(SECOND + "#comments", 456),
            BASE + FIRST: detail(FIRST, 123, "First viewing", first_tag),
            BASE + SECOND: detail(SECOND, 456, "Second viewing", second_tag),
        }
    )

    reviews = fetch_reviews(session, LISTING, full_reviews=True)

    assert session.urls == [LISTING, BASE + SECOND, BASE + FIRST]
    assert [review.viewingId for review in reviews] == [456, 123, 456]
    assert [review.reviewUrl for review in reviews] == [SECOND, FIRST, SECOND + "#comments"]
    assert [review.reviewText for review in reviews] == [
        "Second viewing",
        "First viewing",
        "Second viewing",
    ]
    assert [review.reviewHtml for review in reviews] == [
        "<p>Second viewing</p>",
        "<p>First viewing</p>",
        "<p>Second viewing</p>",
    ]
    assert [review.tags for review in reviews] == [["second"], ["first"], ["second"]]
    assert all(review.isFullReview and review.date == "2026-01-05" for review in reviews)
    assert reviews[0].tags is not reviews[2].tags
    assert reviews[0].tagDetails is not reviews[2].tagDetails


@pytest.mark.parametrize(
    "unavailable",
    [
        Response(status_code=404),
        ConnectionError("Timed out"),
        Response("<h1>Sign in</h1>"),
        detail(FIRST, 999, "Different viewing"),
        detail(SECOND, None, "Different review URL"),
    ],
)
def test_unavailable_details_keep_the_card_and_continue(unavailable):
    session = Session(
        {
            LISTING: card(FIRST, 123) + card(SECOND, None),
            BASE + FIRST: unavailable,
            BASE + SECOND: detail(SECOND, None, "Untagged detail"),
        }
    )

    first, second = fetch_reviews(session, LISTING, full_reviews=True)

    assert session.urls == [LISTING, BASE + FIRST, BASE + SECOND]
    assert (first.reviewText, first.isFullReview, first.tags) == ("Excerpt", False, [])
    assert (second.reviewText, second.isFullReview, second.tags) == ("Untagged detail", True, [])


def test_rate_limit_stops_subsequent_detail_requests():
    third = "/member/film/example/3/"
    session = Session(
        {
            LISTING: card(FIRST, 123) + card(SECOND, 456) + card(third, 789),
            BASE + FIRST: detail(FIRST, 123, "Full text."),
            BASE + SECOND: Response(status_code=429),
        }
    )

    reviews = fetch_reviews(session, LISTING, full_reviews=True)

    assert session.urls == [LISTING, BASE + FIRST, BASE + SECOND]
    assert [review.isFullReview for review in reviews] == [True, False, False]


def test_legacy_review_records_keep_label_lists():
    review = ReviewSummary.from_dict({"tags": ["found footage"]})
    assert review.tags == ["found footage"]
    assert review.tagDetails == []
    assert not review.isFullReview


@pytest.mark.parametrize("search", ["/search/mormon+horror/", "/search/tags/mormon+horror/"])
def test_fetch_tags_primes_the_search_and_uses_cursor_pages(search):
    visible = BASE + search
    ajax = BASE + "/s" + search
    first = ajax + "?__csrf=test-csrf"
    second = ajax + "?cursor=next&__csrf=test-csrf"
    session = Session(
        {
            visible: "<h1>Search</h1>",
            first: f'<ul class="results" data-cursor="next">{MIXED_RESULTS}</ul>',
            second: MIXED_RESULTS.replace("mormon-horror", "folk-horror"),
        },
        prime_url=visible,
    )
    expected = TagSummary("mormon-horror", "mormon-horror", f"{BASE}/tag/mormon-horror/")

    assert extract_tags(MIXED_RESULTS) == [expected]
    assert fetch_tags(session, search) == [
        expected,
        TagSummary("folk-horror", "folk-horror", f"{BASE}/tag/folk-horror/"),
    ]
    assert session.urls == [visible, first, second]
    assert fetch_tags(session, search, pages=1) == [expected]
    assert session.urls == [visible, first, second, first]
