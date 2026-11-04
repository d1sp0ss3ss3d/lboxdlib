# lboxdlib Public API Contract

Date: 2026-07-08  
Status: implementation target for API hardening

This contract defines the supported user-facing API for `lboxdlib`. It is intentionally smaller and cleaner than the current internal implementation modules.

## 1. Public import policy

Most common objects should be importable directly from the package root:

```python
from lboxdlib import LBoxdSession, FilmHydrator, ListManager, hydrate_lids
from lboxdlib import fetch_reviews, fetch_users, select_rated_viewings
from lboxdlib import like_review, unfollow_user, batch_unfollow_users
from lboxdlib import user_follows, resolve_user_slug
```

Grouped facades are also public and stable:

```python
lboxdlib.auth
lboxdlib.fetch
lboxdlib.parse
lboxdlib.select
lboxdlib.actions
lboxdlib.lists
lboxdlib.relationships
lboxdlib.models
```

The following namespaces are implementation details unless a name is explicitly re-exported from a public facade:

```python
lboxdlib.core
lboxdlib.modules
lboxdlib.parsers
lboxdlib.fetchers
lboxdlib.selectors
lboxdlib.compat
```

## 2. Public root API

The package root should expose the most stable/high-value objects only:

```python
from lboxdlib import (
    # auth
    new_session,
    lb_session,
    ensure_session,
    get_signed_in_user,
    get_csrf,

    # fetch
    fetch_activity,
    fetch_films,
    fetch_review_and_diary_activity,
    fetch_reviews,
    fetch_users,

    # list / hydration
    DEFAULT_GENERATOR_LIST_SLUG,
    DEFAULT_MAX_BATCH_SIZE,
    FilmHydrator,
    HydrationResult,
    ListManager,
    hydrate_lids,

    # selection
    select_rated_viewings,
    select_rating_range,

    # actions
    like_review,
    unlike_review,
    follow_user,
    unfollow_user,
    batch_unfollow_users,

    # relationships
    resolve_user_slug,
    user_follows,

    # common models
    User,
    Review,
    RatedViewing,
    FilmSummary,
    DiaryActivity,
    ReviewActivity,
)
```

Do not expose every helper at package root. Less common or lower-level helpers should remain in grouped facade modules.

## 3. `lboxdlib.lists`

List and hydration APIs are stable and first-class.

Required public imports:

```python
from lboxdlib.lists import (
    ListManager,
    FilmHydrator,
    HydrationResult,
    hydrate_lids,
    DEFAULT_GENERATOR_LIST_SLUG,
    DEFAULT_MAX_BATCH_SIZE,
)
```

The default generator-list slug and max batch size must be public because users need to choose or override their own hydration list.

Example:

```python
from lboxdlib import LBoxdSession, FilmHydrator, ListManager, hydrate_lids

manager = ListManager(session)
hydrator = FilmHydrator(manager, generator_list_slug="my-generator-list")
films = hydrate_lids(hydrator, ["abc123", "def456"])
```

## 4. `lboxdlib.relationships`

Relationship predicates are read-only helpers built on top of follower/following pages. They are not plain fetchers.

Required public imports:

```python
from lboxdlib.relationships import (
    user_follows,
    user_follows_slug,
    user_follows_signed_in_user,
    resolve_user_slug,
    RelationshipCheckResult,
)
```

Preferred public predicate:

```python
user_follows(session, user, target, *, pages: int | None = None, verbose: bool = False) -> bool | None
```

Both `user` and `target` may be raw slugs, absolute URLs, relative URLs, or user-like objects exposing `.slug` and/or `.url`.

Return semantics:

- `True`: relationship found.
- `False`: all relevant scanned pages exhausted and relationship absent.
- `None`: a page limit was reached before the result could be known.

`user_follows_viewer` is not part of the preferred public contract because the name is ambiguous. `user_follows_signed_in_user` is acceptable as a convenience alias.

## 5. Scripts versus public package API

The public package API should focus on reusable primitives. Opinionated account-maintenance jobs should live in top-level `scripts/`.

Script-level behavior:

```text
scripts/batch_like_reviews.py
scripts/unfollow_nonfollowers.py
scripts/scrape_films.py
```

Reusable library primitives used by those scripts:

```python
from lboxdlib.fetch import fetch_reviews, fetch_users, fetch_activity
from lboxdlib.select import select_rated_viewings
from lboxdlib.actions import like_review, unfollow_user, batch_unfollow_users
from lboxdlib.relationships import user_follows
from lboxdlib.lists import ListManager, FilmHydrator
```

Existing workflow modules may remain temporarily as compatibility/internal implementation, but documentation should not teach them as the primary public API.

## 6. Pagination convention

Public task APIs should use:

```python
start_page: int = 1
stop_page: int | None = None
```

Rules:

- `start_page` is one-based.
- `stop_page` is inclusive.
- `stop_page=None` means scan until page exhaustion or another stop condition.

Lower-level compatibility functions may still use `pages`, but new public APIs should avoid names like `pages_stop`, `like_stop`, or `max_likes`.

## 7. Safety convention

Single explicit write actions may execute immediately:

```python
unfollow_user(session, "some_slug")
```

Batch functions acting on explicit targets may execute directly:

```python
batch_unfollow_users(session, ["a", "b"])
```

Policy-based scripts that decide targets should default to dry-run or require an explicit execution flag.

## 8. Remaining hardening decisions

Before marking the API stable:

1. Decide whether public models will remain existing dataclasses with aliases or become clean wrapper dataclasses.
2. Add public import tests for every root and facade export.
3. Add fixture-based tests for `user_follows` slug/url/object resolution.
4. Add packaging checks: `pip install -e .`, `pip install .`, and `python -m build`.
5. Decide whether scripts eventually become console commands under `[project.scripts]`.
6. Move deprecated implementation snapshots such as `rated_review_selector.py`, `filter_sort_minimal_genre_fix.py`, and `incoming_engagement.py` behind compatibility or remove after confirmation.


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
