from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from lboxdlib import DiaryEntry, extract_diary, fetch_diary

HTML = Path(__file__).with_name("fixtures").joinpath("diary_page.html").read_text()


def test_sample_diary_fields_and_review_link():
    entries = extract_diary(HTML)
    assert len(entries) == 5
    first = entries[0]
    assert isinstance(first, DiaryEntry)
    assert first.viewing_id == 1589776307
    assert first.viewing_uid == "viewing:1589776307"
    assert first.owner == "diaryowner"
    assert first.film_name == "The Granny"
    assert first.film_year == 1995
    assert first.film_slug == "the-granny"
    assert first.film_lid == "2VMu"
    assert first.film_uid == "film:69877"
    assert first.review_lid == "hlTopp"
    assert first.viewing_date == "2026-11-16"
    assert first.review_url == "https://letterboxd.com/diaryowner/film/the-granny/"
    assert first.url == first.review_url
    assert first.review is None
    assert first.rating is None
    assert first.rating_stars is None
    assert first.rewatch is False
    assert first.liked_film is True
    assert first.watched is None
    assert first.in_watchlist is None
    assert first.tags is None
    assert DiaryEntry.from_dict(first.to_dict()) == first


def test_unreviewed_entry_retains_viewing_url_and_per_row_date():
    entry = extract_diary(HTML)[1]
    assert entry.film_slug == "kotoko"
    assert entry.viewing_date == "2026-11-14"
    assert entry.url == "https://letterboxd.com/diaryowner/film/kotoko/"
    assert entry.review_url is None
    assert entry.review is None
    assert entry.rewatch is True
    assert extract_diary(HTML)[-1].viewing_date == "2026-10-28"


def test_native_ratings_and_public_page_fallbacks():
    entries = extract_diary(HTML)
    assert entries[2].rating == 10
    assert entries[2].rating_stars == 5.0
    assert entries[3].rating == 8
    assert entries[3].rating_stars == 4.0

    soup = BeautifulSoup(HTML, "lxml")
    for node in soup.select(".editable-rating"):
        node.decompose()
    public = extract_diary(str(soup))
    assert [e.rating for e in public] == [e.rating for e in entries]
    # Star text also works without the integer CSS class (including half stars).
    rating = soup.select("tr.diary-entry-row")[2].select_one("span.rating")
    rating["class"] = ["rating"]
    rating.string = "★★★½"
    assert extract_diary(str(soup))[2].rating == 7
    assert extract_diary(str(soup))[2].rating_stars == 3.5


def test_like_placeholder_is_not_a_positive_like_marker():
    soup = BeautifulSoup(HTML, "lxml")
    row = soup.select_one("tr.diary-entry-row")
    for node in row.select(".icon-liked"):
        node.decompose()
    assert row.select_one(".like-link-target") is not None
    assert extract_diary(str(soup))[0].liked_film is False


def test_repeat_viewings_are_distinct_and_keep_numbered_review_link():
    soup = BeautifulSoup(HTML, "lxml")
    row = soup.select_one("tr.diary-entry-row")
    original = str(row)
    repeated = original.replace("1589776307", "1589776308").replace(
        "/diaryowner/film/the-granny/", "/diaryowner/film/the-granny/2/"
    )
    entries = extract_diary(f"<table>{original}{repeated}</table>")
    assert [e.viewing_id for e in entries] == [1589776307, 1589776308]
    assert entries[0].film_lid == entries[1].film_lid
    assert entries[1].review_url.endswith("/the-granny/2/")


def test_bad_identifier_json_and_missing_optional_columns():
    soup = BeautifulSoup(HTML, "lxml")
    row = soup.select_one("tr.diary-entry-row")
    row.select_one('[data-component-class="LazyPoster"]')["data-postered-identifier"] = "bad JSON"
    row.select_one("[data-likeable-identifier]")["data-likeable-identifier"] = "[]"
    for node in row.select(".col-rating, .col-like, .col-rewatch, .col-releaseyear, .releasedate"):
        node.decompose()
    entry = extract_diary(str(row))[0]
    assert entry.film_lid is None
    assert entry.film_uid == "film:69877"
    assert entry.review_lid is None
    assert entry.rating is None
    assert entry.liked_film is None
    assert entry.rewatch is None
    assert entry.film_year is None


def test_missing_numeric_identity_is_not_replaced_with_film_identity():
    assert (
        extract_diary('<table><tr class="diary-entry-row" data-owner="member"></tr></table>') == []
    )
    assert extract_diary("<html><body>No diary entries.</body></html>") == []


def test_viewing_uid_and_review_url_supply_missing_identity_attributes():
    soup = BeautifulSoup(HTML, "lxml")
    row = soup.select_one("tr.diary-entry-row")
    del row["data-viewing-id"]
    del row["data-object-id"]
    del row["data-owner"]
    del row.select_one('[data-component-class="LazyPoster"]')["data-item-slug"]
    entry = extract_diary(str(row))[0]
    assert entry.viewing_id == 1589776307
    assert entry.viewing_uid == "viewing:1589776307"
    assert entry.owner == "diaryowner"
    assert entry.film_slug == "the-granny"


class Response:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        return next(self.responses)


def test_fetch_paginates_without_fetching_review_pages():
    next_page = '<div class="pagination"><div class="paginate-nextprev"><a class="next" href="/diaryowner/diary/films/page/2/">Older</a></div></div>'
    session = Session([Response(HTML + next_page), Response(HTML)])
    entries = fetch_diary(session, "https://letterboxd.com/diaryowner/diary/films/")
    assert len(entries) == 10
    assert session.urls == [
        "https://letterboxd.com/diaryowner/diary/films/page/1/",
        "https://letterboxd.com/diaryowner/diary/films/page/2/",
    ]
    assert all(e.review is None for e in entries)


def test_fetch_start_limit_and_query_preservation():
    session = Session([Response(HTML)])
    entries = fetch_diary(
        session,
        "/diaryowner/diary/films/by/added/?filters=hide-shorts+hide-tv",
        start=3,
        pages=1,
    )
    assert len(entries) == 5
    assert session.urls == [
        "https://letterboxd.com/diaryowner/diary/films/by/added/page/3/?filters=hide-shorts+hide-tv"
    ]


def test_fetch_zero_pages_and_empty_diary():
    session = Session([])
    assert fetch_diary(session, "/member/diary/", pages=0) == []
    assert session.urls == []
    assert fetch_diary(Session([Response("<html></html>")]), "/member/diary/") == []


@pytest.mark.parametrize("kwargs", [{"start": 0}, {"pages": -1}])
def test_invalid_page_bounds_make_no_requests(kwargs):
    session = Session([])
    with pytest.raises(ValueError):
        fetch_diary(session, "/member/diary/", **kwargs)
    assert session.urls == []


def test_fetch_propagates_http_failure():
    with pytest.raises(RuntimeError, match="HTTP 403"):
        fetch_diary(Session([Response("Forbidden", 403)]), "/member/diary/")


def test_public_exports():
    from lboxdlib.fetchers import fetch_diary as grouped_fetch
    from lboxdlib.parsers import extract_diary as grouped_parse

    assert grouped_fetch is fetch_diary
    assert grouped_parse is extract_diary
