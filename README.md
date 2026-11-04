# lboxdlib

Letterboxd scraping, parsing, selection, list/hydration, relationship, and account-action helpers.

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
