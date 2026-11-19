# lboxdlib

Letterboxd scraping, parsing, selection, list/hydration, relationship, and account-action helpers.

## Diary pages

```python
from lboxdlib import LBoxdSession, fetch_diary

session = LBoxdSession()  # Existing login/session configuration
entries = fetch_diary(session, "https://letterboxd.com/m4xr3nn/diary/", pages=2)
for entry in entries:
    print(entry.viewing_date, entry.film_name, entry.rating_stars, entry.review_url)
```

Returns existing `DiaryEntry` records, extended with film details, viewing/review
identifiers, review links, rewatch state, and like markers. Review text is not in
the diary listing and remains `None`; the specific review-page URL is retained.
See [Diary retrieval and parsing](docs/diary.md) for fields and offline parsing.

## Film recommendations

```python
from lboxdlib import new_session
from lboxdlib.workflows import fetch_film_recommendations

with new_session() as session:
    recommendations = fetch_film_recommendations(session, "wishmaster")

for tab, films in recommendations.items():
    print(tab, [film.display_title for film in films])
```

The result has `similar`, `themes`, and `nanogenres` keys. Each value is a
list of poster-derived `FilmSummary` records in Letterboxd's display order,
including repeated appearances across the grids on that tab.

## Full reviews and tags

```python
from lboxdlib import fetch_reviews, fetch_tags

reviews = fetch_reviews(
    session, "https://letterboxd.com/m4xr3nn/reviews/", pages=1, full_reviews=True
)
tags = fetch_tags(session, "https://letterboxd.com/search/tags/mormon+horror/")
```

Use `review.isFullReview` to identify successfully loaded detail pages.
`review.tags` contains the display labels and `review.tagDetails` contains
`TagSummary` records with their URL, slug, and optional member owner.
`fetch_tags` also accepts a full review URL or a mixed search URL.

## Preferred public API

Most users should start with package-root imports for the stable, commonly used objects:

```python
from lboxdlib import LBoxdSession, FilmHydrator, ListManager, hydrate_lids
from lboxdlib import fetch_reviews, fetch_users, select_rated_viewings
from lboxdlib import like_review, unfollow_user, batch_unfollow_users
from lboxdlib import user_follows, resolve_user_slug
```

Grouped facade modules are available when a user wants a more explicit namespace:

```python
from lboxdlib import LBoxdSession
from lboxdlib.auth import ensure_session, get_signed_in_user
from lboxdlib.fetch import fetch_reviews, fetch_users, fetch_activity
from lboxdlib.parse import extract_activity, extract_reviews, extract_users
from lboxdlib.select import select_rated_viewings, select_rating_range
from lboxdlib.actions import like_review, follow_user, unfollow_user, batch_unfollow_users
from lboxdlib.lists import FilmHydrator, ListManager, hydrate_lids
from lboxdlib.relationships import user_follows, resolve_user_slug
from lboxdlib.models import User, Review, RatedViewing, DiaryActivity, ReviewActivity
```

## API groups

- `lboxdlib.auth`: `LBoxdSession`, session, login, cookie, CSRF, and signed-in user helpers.
- `lboxdlib.fetch`: read-only network fetchers.
- `lboxdlib.parse`: HTML/fragment parsers without network behavior.
- `lboxdlib.select`: read-only filtering and discovery helpers.
- `lboxdlib.actions`: authenticated single-purpose write actions.
- `lboxdlib.lists`: `ListManager`, `FilmHydrator`, and list/hydration constants.
- `lboxdlib.relationships`: read-only user relationship predicates.
- `lboxdlib.models`: dataclasses and result objects.

## Scripts versus library API

Opinionated account-maintenance jobs and project-specific automations belong in `scripts/`, not in the core public API. Current script candidates include:

- `scripts/batch_like_reviews.py`
- `scripts/unfollow_nonfollowers.py`
- `scripts/scrape_films.py`

Reusable primitives used by those scripts remain in the library facades.

### Randomized review liking from multiple URLs

Use the multi-source workflow with an existing signed-in session:

```python
from lboxdlib.workflows import batch_like_reviews_from_urls

result = batch_like_reviews_from_urls(
    session,
    urls=[
        "https://letterboxd.com/alice/films/reviews/",
        "https://letterboxd.com/bob/films/reviews/",
        "https://letterboxd.com/activity/",
    ],
    like_count=30,       # total new likes across all sources
    min_batch_size=1,
    max_batch_size=5,    # inclusive number of new likes per source visit
    seed=42,            # optional; omit for a fresh random schedule
    interval=15,        # seconds between like requests across all sources
    dry_run=True,       # preview proposed likes without submitting them
    verbose=True,
)
print(result.liked, result.stop_reason)
print(result.to_json())
```

Each turn randomly selects a different active source while alternatives remain,
then likes a random number of reviews within the configured bounds. Sources
resume at their next review when revisited; review order within each source is
preserved. Already-liked reviews, duplicates across sources, filtered reviews,
and failed likes do not consume the batch quota or total like count. In a dry
run, `liked` counts proposed likes. Set `dry_run=False` to submit them.

`start_page` and inclusive `stop_page` apply separately to every source.
`min_rating`, `max_rating`, and `stop_after_consecutive_already_liked` work as in
`batch_like_reviews`; the consecutive-already-liked threshold retires only that
source. With `like_count=None`, processing continues until all sources stop.
Rate limits and authentication failures stop the entire run; other fetch errors
retire the affected source and are recorded in the result.

`result.source_results` contains individual `BatchLikeResult` objects in input
URL order, after removing duplicate normalized URLs. Aggregate `liked`,
`like_attempts`, `pages_scanned`, and `failures` are available on the result and
in its dictionary/JSON output. Existing single-URL functions remain available.

## Internal organization

The deeper packages remain available during the migration:

- `lboxdlib.core`
- `lboxdlib.parsers`
- `lboxdlib.fetchers`
- `lboxdlib.selectors`
- `lboxdlib.modules`

Prefer the root and facade modules above for normal use. The internal layout can continue to change as long as the public facade API stays stable.

See `docs/API_CONTRACT.md`, `docs/public_api_facades.md`, and `docs/restructure_step1_import_map.md`.

## Packaging

Packaging/build validation notes are in `docs/PACKAGING_HARDENING.md`.

Advanced session control is available through the public wrapper:

```python
from pathlib import Path
from lboxdlib import LBoxdSession

session = LBoxdSession(
    json_path=Path("~/.lb_session.json").expanduser(),
    cookies=cookies,  # optional cookie mapping or cookie-record list
)

print(session.username)
print(session.csrf)
```


## Internal migration notes

- `ListManager` and `FilmHydrator` now live in the real `lboxdlib.lists` package. See `docs/internal_lists_migration.md`.


## Release validation

For a full local release check, install development dependencies and run:

```bash
python -m pip install -e ".[dev]"
python scripts/release_check.py
```

To create a clean source zip after validation:

```bash
python scripts/make_release_zip.py
```

See `docs/RELEASE_CHECKS.md` for details.
