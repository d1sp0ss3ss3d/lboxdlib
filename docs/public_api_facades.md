# Public API facade plan

The user-facing API is intentionally smaller than the internal package tree.
Callers should generally import common stable objects directly from `lboxdlib`,
or use grouped facade modules when they want a clearer namespace.

## Package-root imports

```python
from lboxdlib import FilmHydrator, ListManager, hydrate_lids
from lboxdlib import fetch_reviews, fetch_users, select_rated_viewings
from lboxdlib import like_review, unfollow_user, batch_unfollow_users
from lboxdlib import user_follows, resolve_user_slug
```

## Stable facade imports

### Authentication/session

```python
from lboxdlib.auth import ensure_session, new_session, lb_login, get_signed_in_user, get_csrf
```

### Fetching

```python
from lboxdlib.fetch import fetch_films, fetch_reviews, fetch_users, fetch_activity
from lboxdlib.fetch import fetch_review_and_diary_activity
```

### Parsing

```python
from lboxdlib.parse import extract_activity, extract_reviews, extract_users, extract_films
from lboxdlib.parse import ReviewActivity, DiaryActivity, ReviewReferenceActivity
```

### Selection/discovery

```python
from lboxdlib.select import select_rated_viewings, select_rating_range
from lboxdlib.select import select_highly_rated_viewings
```

### Authenticated actions

```python
from lboxdlib.actions import like_review, unlike_review
from lboxdlib.actions import follow_user, unfollow_user, batch_unfollow_users
from lboxdlib.actions import set_watchlist, set_watched
```

### Lists and film hydration

```python
from lboxdlib.lists import FilmHydrator, ListManager, hydrate_lids
from lboxdlib.lists import DEFAULT_GENERATOR_LIST_SLUG, DEFAULT_MAX_BATCH_SIZE
```

### Relationships

```python
from lboxdlib.relationships import user_follows, resolve_user_slug
```

### Models/results

```python
from lboxdlib.models import User, Review, RatedViewing
from lboxdlib.models import ReviewActivity, DiaryActivity
```

## Scripts versus library API

Opinionated account-maintenance workflows should live in top-level `scripts/`
rather than being taught as core public API:

```text
scripts/batch_like_reviews.py
scripts/unfollow_nonfollowers.py
scripts/scrape_films.py
```

The reusable building blocks used by those scripts remain public facades.

## Rule of thumb

- Use `fetch` for read-only network retrieval.
- Use `parse` when you already have HTML.
- Use `select` when you want filtered/discovered read-only results.
- Use `actions` for authenticated write operations on explicit targets.
- Use `lists` for list management and film hydration.
- Use `relationships` for read-only follow/follower predicates.
- Use `scripts/` for opinionated policy automations.
- Use deeper modules only when extending the library internals.


## Public session object

`LBoxdSession` is a first-class public API object exposed from both `lboxdlib` and `lboxdlib.auth`. It wraps the underlying curl-cffi session, delegates HTTP methods such as `.get()` and `.post()`, accepts either an existing cookie JSON file or supplied cookies, and exposes `.username` / `.signed_in_user` plus `.csrf` convenience properties. Existing `ensure_session`, `get_csrf`, and `get_signed_in_user` helpers remain part of the public API and now operate safely with either `LBoxdSession` or a raw curl-cffi session.

```python
from pathlib import Path
from lboxdlib import LBoxdSession

session = LBoxdSession(
    json_path=Path("~/.lb_session.json").expanduser(),
    cookies=cookies,
)
```
