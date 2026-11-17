# Diary retrieval and parsing

```python
from lboxdlib import fetch_diary

# Use an existing session; reads numbered diary pages without fetching reviews.
entries = fetch_diary(session, "https://letterboxd.com/m4xr3nn/diary/", pages=2)
```

`start=1` selects the first numbered page, `pages=None` reads to the end, and
`pages=0` makes no requests. Diary URLs can include sort/filter paths and query
parameters. Entry order and repeated viewings are preserved.

For saved HTML:

```python
from pathlib import Path
from lboxdlib import extract_diary

entries = extract_diary(Path("diarypage.html").read_text(encoding="utf-8"))
```

Both paths return the existing `DiaryEntry` class. The functions are also
available from `lboxdlib.fetchers` and `lboxdlib.parsers`, respectively.

| Field | Contents |
| --- | --- |
| `viewing_id`, `viewing_uid` | Numeric viewing ID and `viewing:<id>` UID |
| `owner` | Diary owner username/slug |
| `film_name`, `film_year`, `film_slug` | Title, release year, human-readable slug |
| `film_lid`, `film_uid` | Film LID and `film:<id>` UID; distinct from external IDs |
| `review_lid` | Viewing/review LID from its likeable identifier, when exposed |
| `viewing_date` | Watched date as `YYYY-MM-DD`, read independently for each row |
| `rating` | Existing integer 1–10 scale; `None` for unrated |
| `rating_stars` | Derived 0.5–5 star scale; `None` for unrated |
| `url` | Absolute URL to this viewing, retaining any numbered suffix |
| `review_url` | Absolute review-page link, only when a written-review link exists |
| `rewatch`, `liked_film` | Markers from the diary row; `None` if the relevant column is missing |
| `review`, `tags` | Remain `None`: not supplied by the diary listing |
| `watched`, `in_watchlist` | Remain `None`: aggregate film state is not inferred from a diary row |

The review link opens the complete review page; it is not an inline text
response or a review excerpt. Fetch it separately when needed:

```python
from lboxdlib import fetch_reviews

entry = entries[0]
if entry.review_url:
    reviews = fetch_reviews(session, entry.review_url, pages=1)
    if reviews:
        entry.review = reviews[0].reviewText
```

The parser accepts table rows from diary HTML, decodes escaped identifier JSON,
and skips rows missing numeric viewing identity, owner, or film slug. It does
not create a session, submit account actions, or invent missing text or IDs.
