from types import SimpleNamespace

from lboxdlib import FilmSummary
from lboxdlib.parsers.films import extract_films
from lboxdlib.workflows import fetch_film_recommendations


def poster(slug, lid, film_id):
    return f"""
        <div class="react-component" data-component-class="LazyPoster"
             data-item-link="/film/{slug}/" data-item-name="{slug.title()} (1997)"
             data-postered-identifier='{{"lid":"{lid}","uid":"film:{film_id}"}}'></div>
    """


def recommendation_page(*cards):
    source = poster("wishmaster", "1WtG", 46312)
    grids = "".join(
        f'<section class="genre-group"><div class="poster-grid">{card}</div></section>'
        for card in cards
    )
    return f"""
        <html><body class="screen-productions-similar">
            <header>{source}</header>
            <div class="body">{grids}</div>
            <aside class="sidebar">{source}</aside>
        </body></html>
    """


def test_fetch_film_recommendations_returns_ordered_results_from_all_tabs():
    first = poster("hellraiser", "29Bi", 100)
    second = poster("warlock", "1Wgs", 200)
    base = "https://letterboxd.com/film/wishmaster"
    html_by_url = {
        f"{base}/similar/": recommendation_page(first + second),
        f"{base}/themes/": recommendation_page(second, first, second),
        f"{base}/nanogenres/": recommendation_page(first),
    }

    def get(url):
        return SimpleNamespace(text=html_by_url[url], status_code=200)

    result = fetch_film_recommendations(SimpleNamespace(get=get), "wishmaster")

    assert {tab: [film.slug for film in films] for tab, films in result.items()} == {
        "similar": ["hellraiser", "warlock"],
        "themes": ["warlock", "hellraiser", "warlock"],
        "nanogenres": ["hellraiser"],
    }
    film = result["similar"][0]
    assert isinstance(film, FilmSummary)
    assert (film.lid, film.uid, film.year) == ("29Bi", "film:100", 1997)


def test_recommendation_page_without_grids_returns_no_films():
    assert extract_films(recommendation_page()) == []
