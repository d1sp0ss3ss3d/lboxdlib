#!/usr/bin/env python3
"""
Command-line tool to scrape FilmSummary records from a Letterboxd page.

Supports:
  - standard paginated pages
  - cursor/infinite-scroll pages
  - comparing two sources with --diff

Non-enriched mode returns incomplete FilmSummary records parsed from LazyPoster
DOM nodes. Enriched mode fetches only film lids, then hydrates them into full
FilmSummary records via FilmHydrator and the configured generator list.

Optional OMDb enrichment can add full OMDb plots to hydrated FilmSummary records
when --omdb-plot is used.
"""

import argparse
import json
import os
import pprint
import random
import sys
from pathlib import Path
from typing import List, Optional

from curl_cffi import requests

from lboxdlib.lists.hydration import FilmHydrator
from lboxdlib.auth import ensure_session, get_signed_in_user
from lboxdlib.lists.manager import ListManager
from lboxdlib.fetchers.films import fetch_films, fetch_lids
from lboxdlib.models.typedefs import FilmSummary


FIELD_ALIASES = {}


# ----------------------------------------------------------------------
# OMDb enrichment
# ----------------------------------------------------------------------

def enrich_films_with_omdb_plots(
    films: List[FilmSummary],
    *,
    api_key: str,
    cache_path: Optional[Path] = None,
    verbose: bool = True,
) -> List[FilmSummary]:
    """
    Add full OMDb plots to FilmSummary records in place.

    This requires hydrated FilmSummary records with imdb_id available through
    their links.
    """
    if not api_key:
        raise ValueError("OMDb API key is required")

    if verbose:
        with_imdb = sum(1 for film in films if film.imdb_id)
        print(
            f"Fetching OMDb full plots for {with_imdb}/{len(films)} films with IMDb IDs ...",
            file=sys.stderr,
        )

    try:
        from lboxdlib.workflows.omdb_enrichment import OmdbPlotClient
    except ImportError as exc:
        raise RuntimeError(
            "OMDb plot enrichment requested, but lboxdlib.workflows.omdb_enrichment "
            "is not present in this package."
        ) from exc

    client = OmdbPlotClient(
        api_key,
        cache_path=cache_path,
    )

    enriched = client.enrich_films(films)

    if verbose:
        with_plot = sum(1 for film in enriched if getattr(film, "omdb_plot", None))
        print(f"OMDb plots available: {with_plot}/{len(enriched)}", file=sys.stderr)

    return enriched


# ----------------------------------------------------------------------
# Film fetching
# ----------------------------------------------------------------------

def fetch_all_films(
    session: requests.Session,
    url: str,
    user: Optional[str] = None,
    enrich: bool = False,
    *,
    max_pages: Optional[int] = None,
    timeout: int = 30,
    verbose: bool = True,
    generator_list_slug: str = "generator",
    cleanup: bool = True,
    omdb_plot: bool = False,
    omdb_api_key: Optional[str] = None,
    omdb_cache: Optional[Path] = Path("omdb_plot_cache.json"),
) -> List[FilmSummary]:
    """
    Fetch FilmSummary records from a Letterboxd URL.

    If enrich is False:
        Returns incomplete FilmSummary records parsed from LazyPoster DOM nodes.

    If enrich is True:
        Fetches bare film lids from the listing page, then hydrates them through
        FilmHydrator/ListManager using the generator list.

    If omdb_plot is True:
        Adds full OMDb plots to hydrated FilmSummary records using imdb_id.

    timeout is retained for CLI/API compatibility, but page_handling currently
    does not accept timeout.
    """
    if omdb_plot and not enrich:
        raise ValueError(
            "omdb_plot requires enrich=True because IMDb IDs come from hydrated FilmSummary records"
        )

    if verbose:
        print(f"Fetching films from {url} ...", file=sys.stderr)

    if not enrich:
        films = fetch_films(
            session,
            url,
            pages=max_pages,
            verbose=verbose,
        )

        if verbose:
            print(f"Fetched {len(films)} incomplete FilmSummary records", file=sys.stderr)

        return films

    lids = fetch_lids(
        session,
        url,
        pages=max_pages,
        verbose=verbose,
    )

    if verbose:
        print(f"Fetched {len(lids)} film lids", file=sys.stderr)
        print(
            f"Hydrating via generator list '{generator_list_slug}' ...",
            file=sys.stderr,
        )

    list_manager = ListManager(session)
    hydrator = FilmHydrator(
        list_manager,
        generator_list_slug=generator_list_slug,
        cleanup=cleanup,
    )
    films = hydrator.hydrate_lids(lids)

    if verbose:
        print(f"Hydrated {len(films)} FilmSummary records", file=sys.stderr)

    if omdb_plot:
        films = enrich_films_with_omdb_plots(
            films,
            api_key=omdb_api_key or "",
            cache_path=omdb_cache,
            verbose=verbose,
        )

    return films


# ----------------------------------------------------------------------
# Diff utilities
# ----------------------------------------------------------------------

def _film_diff_key(film: FilmSummary):
    return (
        film.lid
        or film.uid
        or film.link
        or (film.name, film.releaseYear)
    )


def _build_film_index(films: List[FilmSummary], name: str, key_func) -> dict:
    out = {}

    for f in films:
        key = key_func(f)

        if key is None or key == "":
            print(
                f"Warning: {name}: skipping film with missing diff key: {f!r}",
                file=sys.stderr,
            )
            continue

        if key in out:
            print(
                f"Warning: {name}: duplicate diff key {key!r}; keeping last",
                file=sys.stderr,
            )

        out[key] = f

    return out


def list_diff(
    list1: List[FilmSummary],
    list2: List[FilmSummary],
    key_func=_film_diff_key,
) -> dict:
    """
    Compare two lists of FilmSummary objects and return the differences.
    """
    dict1 = _build_film_index(list1, "list1", key_func)
    dict2 = _build_film_index(list2, "list2", key_func)

    keys1 = set(dict1.keys())
    keys2 = set(dict2.keys())

    return {
        "only_in_list1": [dict1[k] for k in sorted(keys1 - keys2, key=str)],
        "shared": [dict1[k] for k in sorted(keys1 & keys2, key=str)],
        "only_in_list2": [dict2[k] for k in sorted(keys2 - keys1, key=str)],
    }


def compare_two_lists(
    session: requests.Session,
    url1: str,
    url2: str,
    user: Optional[str] = None,
    enrich: bool = False,
    *,
    max_pages: Optional[int] = None,
    timeout: int = 30,
    verbose: bool = True,
    generator_list_slug: str = "generator",
    cleanup: bool = True,
    omdb_plot: bool = False,
    omdb_api_key: Optional[str] = None,
    omdb_cache: Optional[Path] = Path("omdb_plot_cache.json"),
) -> dict:
    """
    Fetch all films from two Letterboxd URLs and compare them.
    """
    if user is None:
        try:
            user = get_signed_in_user(session)
        except RuntimeError:
            user = None

    films1 = fetch_all_films(
        session,
        url1,
        user=user,
        enrich=enrich,
        max_pages=max_pages,
        timeout=timeout,
        verbose=verbose,
        generator_list_slug=generator_list_slug,
        cleanup=cleanup,
        omdb_plot=omdb_plot,
        omdb_api_key=omdb_api_key,
        omdb_cache=omdb_cache,
    )

    films2 = fetch_all_films(
        session,
        url2,
        user=user,
        enrich=enrich,
        max_pages=max_pages,
        timeout=timeout,
        verbose=verbose,
        generator_list_slug=generator_list_slug,
        cleanup=cleanup,
        omdb_plot=omdb_plot,
        omdb_api_key=omdb_api_key,
        omdb_cache=omdb_cache,
    )

    if verbose:
        print(f"url1 films: {len(films1)}", file=sys.stderr)
        print(f"url2 films: {len(films2)}", file=sys.stderr)

    return list_diff(films1, films2)


# ----------------------------------------------------------------------
# CLI helpers
# ----------------------------------------------------------------------

def resolve_omdb_api_key(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> Optional[str]:
    """
    Resolve OMDb API key from CLI arg or OMDB_API_KEY environment variable.
    """
    if not args.omdb_plot:
        return None

    if not args.enrich:
        parser.error(
            "--omdb-plot requires --enrich because IMDb IDs come from hydrated FilmSummary links"
        )

    api_key = args.omdb_api_key or os.environ.get("OMDB_API_KEY")

    if not api_key:
        parser.error("--omdb-plot requires --omdb-api-key or OMDB_API_KEY")

    return api_key


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Scrape FilmSummary records from a Letterboxd page or compare two lists."
    )

    parser.add_argument("url", nargs="?", help="Letterboxd URL to scrape")

    parser.add_argument(
        "-r",
        "--random",
        type=int,
        nargs="?",
        const=1,
        default=None,
        help="Output N random films (default: 1)",
    )

    parser.add_argument(
        "-t",
        "--tail",
        type=int,
        nargs="?",
        const=3,
        default=None,
        help="Output last N films (default: 3)",
    )

    parser.add_argument(
        "-H",
        "--head",
        type=int,
        nargs="?",
        const=3,
        default=None,
        help="Output first N films (default: 3) (use -H to avoid conflict with -h help)",
    )

    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write output to file instead of stdout",
    )

    parser.add_argument(
        "--diff",
        nargs=2,
        metavar=("URL1", "URL2"),
        help="Compare two list URLs and show differences (ignores head/tail/random)",
    )

    parser.add_argument(
        "--diff-types",
        "--diff-type",
        choices=["ls1-only", "shared-only", "ls2-only", "all"],
        default="all",
        help="Which parts of the diff to show (default: all)",
    )

    parser.add_argument(
        "--fields",
        type=str,
        default="all",
        help=(
            "Comma-separated list of FilmSummary fields to include in output "
            "(e.g., 'name,releaseYear,lid,uid,filmId'). "
            "Default: all fields"
        ),
    )

    parser.add_argument(
        "--enrich",
        action="store_true",
        help=(
            "Hydrate scraped film lids into complete FilmSummary records using "
            "FilmHydrator and the generator list. Without this, records are "
            "incomplete LazyPoster-derived FilmSummary objects."
        ),
    )

    parser.add_argument(
        "--generator-list-slug",
        default="generator",
        help="Generator list slug used by FilmHydrator when --enrich is set (default: generator).",
    )

    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Do not remove newly added films from the generator list after hydration.",
    )

    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help=(
            "Maximum number of listing pages/fragments to fetch. "
            "Useful for testing large services."
        ),
    )

    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help=(
            "Per-request timeout in seconds. Retained for interface compatibility; "
            "currently not passed into page_handling."
        ),
    )

    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress/debug output on stderr.",
    )

    parser.add_argument(
        "--omdb-plot",
        action="store_true",
        help="Fetch full OMDb plot/synopsis for hydrated films using IMDb IDs.",
    )

    parser.add_argument(
        "--omdb-api-key",
        type=str,
        default=None,
        help="OMDb API key. If omitted, reads OMDB_API_KEY from the environment.",
    )

    parser.add_argument(
        "--omdb-cache",
        type=Path,
        default=Path("omdb_plot_cache.json"),
        help="Path to OMDb plot cache JSON file.",
    )

    args = parser.parse_args()
    verbose = not args.quiet
    cleanup = not args.no_cleanup
    omdb_api_key = resolve_omdb_api_key(args, parser)

    if args.fields == "all":
        requested_fields = None
    else:
        requested_fields = [
            f.strip()
            for f in args.fields.split(",")
            if f.strip()
        ]

    session = ensure_session()

    def film_to_output_dict(film: FilmSummary) -> dict:
        film_dict = film.to_dict()

        if requested_fields is None:
            return film_dict

        out = {}
        for requested in requested_fields:
            native = FIELD_ALIASES.get(requested, requested)

            if native in film_dict:
                out[requested] = film_dict[native]
            elif hasattr(film, requested):
                value = getattr(film, requested)
                if value is not None:
                    out[requested] = value

        return out

    # --- Diff mode ---
    if args.diff:
        url1, url2 = args.diff

        if verbose:
            print(f"Comparing:\n  {url1}\n  {url2}", file=sys.stderr)

        diff_result = compare_two_lists(
            session,
            url1,
            url2,
            user=None,
            enrich=args.enrich,
            max_pages=args.max_pages,
            timeout=args.timeout,
            verbose=verbose,
            generator_list_slug=args.generator_list_slug,
            cleanup=cleanup,
            omdb_plot=args.omdb_plot,
            omdb_api_key=omdb_api_key,
            omdb_cache=args.omdb_cache,
        )

        diff_types = args.diff_types

        if diff_types == "all":
            filtered = diff_result
        elif diff_types == "ls1-only":
            filtered = {"only_in_list1": diff_result["only_in_list1"]}
        elif diff_types == "shared-only":
            filtered = {"shared": diff_result["shared"]}
        else:
            filtered = {"only_in_list2": diff_result["only_in_list2"]}

        result_dict = {}
        for key, films in filtered.items():
            result_dict[key] = [film_to_output_dict(f) for f in films]

        if verbose:
            for key in result_dict:
                count = len(result_dict[key])

                if key == "only_in_list1":
                    print(f"Only in first list: {count}", file=sys.stderr)
                elif key == "shared":
                    print(f"Shared: {count}", file=sys.stderr)
                elif key == "only_in_list2":
                    print(f"Only in second list: {count}", file=sys.stderr)

        out_file = args.output

        if out_file:
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump(result_dict, f, ensure_ascii=False, indent=2)

            if verbose:
                print(f"Diff written to {out_file}", file=sys.stderr)
        else:
            pprint.pprint(result_dict, indent=2, sort_dicts=False)

        return

    # --- Single URL scraping mode ---
    if not args.url:
        parser.error("the following arguments are required when --diff is not used: url")

    modes = [
        m
        for m in ("head", "tail", "random")
        if getattr(args, m) is not None
    ]

    if len(modes) > 1:
        parser.error("Only one of --head, --tail, --random may be used at a time")

    selection_mode = modes[0] if modes else None
    selection_count = None

    if selection_mode == "head":
        selection_count = args.head
    elif selection_mode == "tail":
        selection_count = args.tail
    elif selection_mode == "random":
        selection_count = args.random

    try:
        user = get_signed_in_user(session)
    except RuntimeError:
        user = None

        if verbose:
            print(
                "Warning: Not signed in – hydration and some endpoints may be incomplete.",
                file=sys.stderr,
            )

    films = fetch_all_films(
        session,
        args.url,
        user=user,
        enrich=args.enrich,
        max_pages=args.max_pages,
        timeout=args.timeout,
        verbose=verbose,
        generator_list_slug=args.generator_list_slug,
        cleanup=cleanup,
        omdb_plot=args.omdb_plot,
        omdb_api_key=omdb_api_key,
        omdb_cache=args.omdb_cache,
    )

    if verbose:
        print(f"Total films found: {len(films)}", file=sys.stderr)

    if selection_mode is None:
        selected_films = films
    else:
        n = selection_count

        if n is None:
            n = {"head": 3, "tail": 3, "random": 1}[selection_mode]

        if n > len(films):
            if verbose:
                print(
                    f"Requested {n} but only {len(films)} available – using all.",
                    file=sys.stderr,
                )

            n = len(films)

        if selection_mode == "head":
            selected_films = films[:n]
        elif selection_mode == "tail":
            selected_films = films[-n:]
        else:
            selected_films = random.sample(films, n)

    output_data = [film_to_output_dict(f) for f in selected_films]

    out_file = args.output

    if out_file:
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)

        if verbose:
            print(f"Output written to {out_file}", file=sys.stderr)
    else:
        pprint.pprint(output_data, indent=2, sort_dicts=False)

        if verbose:
            print(f"Total output films: {len(output_data)}", file=sys.stderr)


if __name__ == "__main__":
    main()
