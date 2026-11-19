"""Collect Letterboxd's recommendations for one film."""

from __future__ import annotations

import re

from lboxdlib.auth import BASE_URL
from lboxdlib.fetchers.films import fetch_films
from lboxdlib.models.typedefs import FilmSummary


def fetch_film_recommendations(
    session,
    slug: str,
    *,
    verbose: bool = False,
) -> dict[str, list[FilmSummary]]:
    """Fetch the similar, themes, and nanogenres tabs for a Letterboxd film slug.

    Each key maps to the tab's poster-derived film summaries in page order.
    A film that appears more than once is returned for each appearance.
    """
    slug = slug.strip()
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", slug):
        raise ValueError("Expected a Letterboxd film slug such as 'wishmaster'")

    return {
        tab: fetch_films(session, f"{BASE_URL}/film/{slug}/{tab}/", verbose=verbose)
        for tab in ("similar", "themes", "nanogenres")
    }
