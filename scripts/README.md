# lboxdlib scripts

This folder is for opinionated, account-specific automations built from the
stable library primitives. These scripts are kept outside the public package API
so the library remains focused on reusable fetch, parse, select, action,
relationship, and list/hydration helpers.

Current script candidates:

- `batch_like_reviews.py` — batch liking policy/automation.
- `unfollow_nonfollowers.py` — non-reciprocal follow cleanup policy.
- `scrape_films.py` — project/data-acquisition script behavior.

The reusable primitives these scripts should use live in package facades such as
`lboxdlib.fetch`, `lboxdlib.actions`, `lboxdlib.select`, `lboxdlib.lists`, and
`lboxdlib.relationships`.
