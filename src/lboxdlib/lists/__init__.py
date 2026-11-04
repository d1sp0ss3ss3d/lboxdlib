"""Stable public list and film-hydration API.

Most callers should import the list/hydration objects either from this facade:

    from lboxdlib.lists import FilmHydrator, ListManager, hydrate_lids

or directly from the package root:

    from lboxdlib import FilmHydrator, ListManager, hydrate_lids
"""

from __future__ import annotations

from lboxdlib.lists.hydration import (
    DEFAULT_GENERATOR_LIST_SLUG,
    DEFAULT_MAX_BATCH_SIZE,
    FilmHydrator,
    HydrationResult,
    chunked,
    hydrate_lids,
)
from lboxdlib.lists.manager import ListManager

__all__ = [
    "ListManager",
    "FilmHydrator",
    "HydrationResult",
    "hydrate_lids",
    "chunked",
    "DEFAULT_GENERATOR_LIST_SLUG",
    "DEFAULT_MAX_BATCH_SIZE",
]
