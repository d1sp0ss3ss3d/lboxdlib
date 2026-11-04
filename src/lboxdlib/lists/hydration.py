"""
film_hydrator.py

Hydrate bare Letterboxd film lids into FilmSummary records by temporarily
adding them to a dedicated generator list.

Expected setup:
    - A Letterboxd list exists with slug "generator" unless overridden.
    - The generator list has a permanent placeholder film at position 0.
    - Position 0 is never deleted.
    - Up to 11 films are added per hydration batch, because the list API
      response provides up to 12 previewEntries including the placeholder.

This module assumes:
    - list_manager.batch_add(list_slug, lids=[...]) exists.
    - list_manager.batch_remove(list_slug, positions=[...]) exists.
    - batch_add returns a list API payload containing data.previewEntries.
    - typedefs.py defines ListResponse and FilmSummary.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence

from lboxdlib.models.typedefs import FilmSummary, ListResponse


DEFAULT_GENERATOR_LIST_SLUG = "generator-1"
DEFAULT_MAX_BATCH_SIZE = 11


def chunked(items: Sequence[str], size: int) -> Iterable[List[str]]:
    """Yield chunks of size ``size`` from ``items``."""
    if size <= 0:
        raise ValueError("size must be positive")

    for index in range(0, len(items), size):
        yield list(items[index:index + size])


@dataclass
class HydrationResult:
    """
    Result for one hydration batch.

    hydrated:
        Full FilmSummary records extracted from the generator list response.

    added_lids:
        Letterboxd film lids that were added to the generator list.

    cleanup_positions:
        Positions requested for cleanup. These are descending and never include
        position 0.

    raw_response:
        Raw response from list_manager.batch_add().
    """

    hydrated: List[FilmSummary]
    added_lids: List[str]
    cleanup_positions: List[int]
    raw_response: dict


class FilmHydrator:
    """
    Hydrate bare Letterboxd film lids into fuller FilmSummary records.

    Workflow:
        1. Add up to ``max_batch_size`` lids to the generator list.
        2. Parse the returned ListResponse.
        3. Treat previewEntries[0] as the permanent placeholder.
        4. Return previewEntries[1:1 + added_count] as FilmSummary objects.
        5. Remove only the newly-added entries from the generator list.

    The lid is treated as an opaque, case-sensitive alphanumeric string. This
    module deliberately does not coerce, strip, lowercase, or dedupe lids.
    """

    def __init__(
        self,
        list_manager,
        *,
        generator_list_slug: str = DEFAULT_GENERATOR_LIST_SLUG,
        max_batch_size: int = DEFAULT_MAX_BATCH_SIZE,
        cleanup: bool = True,
    ):
        if max_batch_size <= 0:
            raise ValueError("max_batch_size must be positive")

        self.list_manager = list_manager
        self.generator_list_slug = generator_list_slug
        self.max_batch_size = max_batch_size
        self.cleanup = cleanup

        # Captured from previewEntries[0] on the first successful hydration.
        # This is informational and avoids guessing by title.
        self.placeholder_film: Optional[FilmSummary] = None

    def hydrate_lids(
        self,
        lids: Sequence[str],
        *,
        cleanup: Optional[bool] = None,
    ) -> List[FilmSummary]:
        """
        Hydrate bare Letterboxd film lids.

        The returned FilmSummary list follows the order represented in each
        generator-list response. Input lids are not deduped or normalized.
        """
        validated_lids = self._validate_lids(lids)

        hydrated: List[FilmSummary] = []

        for batch in chunked(validated_lids, self.max_batch_size):
            result = self.hydrate_lid_batch(batch, cleanup=cleanup)
            hydrated.extend(result.hydrated)

        return hydrated

    def hydrate_search_films(
        self,
        films: Sequence[Any],
        *,
        cleanup: Optional[bool] = None,
    ) -> List[FilmSummary]:
        """
        Hydrate objects/dicts that expose a lid.

        Accepts:
            - objects with a .lid attribute
            - dictionaries with a "lid" key

        Raw lids should usually be passed directly to hydrate_lids().
        """
        lids = [self._extract_lid(film) for film in films]
        return self.hydrate_lids(lids, cleanup=cleanup)

    def hydrate_lid_batch(
        self,
        lids: Sequence[str],
        *,
        cleanup: Optional[bool] = None,
    ) -> HydrationResult:
        """
        Hydrate one batch of film lids.

        This method enforces max_batch_size. Use hydrate_lids() for automatic
        chunking.
        """
        validated_lids = self._validate_lids(lids)

        if len(validated_lids) > self.max_batch_size:
            raise ValueError(
                f"hydrate_lid_batch accepts at most {self.max_batch_size} lids"
            )

        should_cleanup = self.cleanup if cleanup is None else cleanup

        raw_response = self.list_manager.batch_add(
            self.generator_list_slug,
            lids=validated_lids,
        )

        try:
            hydrated = self._extract_hydrated_films(
                raw_response,
                expected_count=len(validated_lids),
            )
        finally:
            if should_cleanup:
                self._cleanup_added_films(len(validated_lids))

        cleanup_positions = self._cleanup_positions(len(validated_lids))

        return HydrationResult(
            hydrated=hydrated,
            added_lids=list(validated_lids),
            cleanup_positions=cleanup_positions,
            raw_response=raw_response,
        )

    def _extract_hydrated_films(
        self,
        raw_response: dict,
        *,
        expected_count: int,
    ) -> List[FilmSummary]:
        """
        Extract hydrated films from the ListManager.batch_add response.

        previewEntries[0] is treated as the permanent generator placeholder.
        previewEntries[1:] are treated as the newly added films.
        """
        parsed = ListResponse.from_dict(raw_response)
        preview_entries = parsed.data.previewEntries

        if not preview_entries:
            raise RuntimeError("Generator list response did not include previewEntries")

        self.placeholder_film = self.placeholder_film or preview_entries[0].film

        hydrated_entries = preview_entries[1:1 + expected_count]
        hydrated_films = [entry.film for entry in hydrated_entries]

        if len(hydrated_films) != expected_count:
            raise RuntimeError(
                "Generator list response did not return the expected number "
                f"of hydrated films: expected {expected_count}, "
                f"got {len(hydrated_films)}"
            )

        return hydrated_films

    def _cleanup_added_films(self, added_count: int) -> None:
        """
        Remove newly added films from the generator list, never position 0.
        """
        positions = self._cleanup_positions(added_count)

        if not positions:
            return

        self.list_manager.batch_remove(
            self.generator_list_slug,
            positions=positions,
        )

    def _cleanup_positions(self, added_count: int) -> List[int]:
        """Return descending cleanup positions, never including index 0."""
        if not isinstance(added_count, int):
            raise TypeError("added_count must be an integer")

        if added_count < 0:
            raise ValueError("added_count must not be negative")

        return list(range(added_count, 0, -1))

    def _validate_lids(self, lids: Sequence[str]) -> List[str]:
        """
        Validate lids without normalizing them.

        A valid lid is already a string, non-empty, alphanumeric, case-sensitive,
        and contains no whitespace. This intentionally rejects None, ints,
        bytes, leading/trailing spaces, and any repairable-looking values.
        """
        if isinstance(lids, (str, bytes)):
            raise TypeError("lids must be a sequence of strings, not a single string")

        if not isinstance(lids, Sequence):
            raise TypeError("lids must be a sequence of strings")

        validated: List[str] = []

        for lid in lids:
            if not isinstance(lid, str):
                raise TypeError(f"film lid must be a string: {lid!r}")

            if not lid:
                raise ValueError("film lid must not be empty")

            if lid.strip() != lid:
                raise ValueError(
                    f"film lid must not contain leading/trailing whitespace: {lid!r}"
                )

            if any(ch.isspace() for ch in lid):
                raise ValueError(f"film lid must not contain whitespace: {lid!r}")

            if not lid.isalnum():
                raise ValueError(f"film lid must be alphanumeric: {lid!r}")

            validated.append(lid)

        if not validated:
            raise ValueError("at least one film lid is required")

        return validated

    def _extract_lid(self, film: Any) -> str:
        """
        Extract and validate a lid from an autocomplete/search-style object.

        This does not coerce arbitrary objects to strings.
        """
        if hasattr(film, "lid"):
            lid = getattr(film, "lid")
        elif isinstance(film, dict) and "lid" in film:
            lid = film["lid"]
        else:
            raise TypeError(f"film object does not expose a lid: {film!r}")

        return self._validate_lids([lid])[0]


def hydrate_lids(
    film_hydrator: FilmHydrator,
    lids: Sequence[str],
    *,
    cleanup: Optional[bool] = None,
    serial: Optional[bool] = False,
) -> List[FilmSummary] | list[dict]:
    """
    Convenience function matching the desired high-level API:

        hydrate_lids(film_hydrator, lids) -> list[FilmSummary]
        hydrate_lids(film_hydrator, lids, serial=True) -> list[dict]
    """
    hydrated = film_hydrator.hydrate_lids(lids, cleanup=cleanup)

    if serial:
        return [film.to_dict() for film in hydrated]

    return hydrated