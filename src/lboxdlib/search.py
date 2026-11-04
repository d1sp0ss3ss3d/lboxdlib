# search_manager.py
"""
Letterboxd Search Management Module

Uses an authenticated curl-cffi session to query Letterboxd's autocomplete
film endpoint and cache partial film records.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urljoin

from lboxdlib.models.typedefs import FilmSummary

BASE_URL = "https://letterboxd.com"


@dataclass
class AutocompleteDirector:
    name: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AutocompleteDirector":
        return cls(
            name=data["name"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
        }


@dataclass
class AutocompleteFilm:
    """
    Partial film object returned by /s/autocompletefilm.

    Identifier shape for this source:
        - lid is payload["lid"], e.g. "G5v2"
        - uid is payload["uid"], e.g. "film:1003092"
        - filmId is derived from uid, e.g. "1003092"

    The autocomplete payload also has an "id" field, but for this source that
    value is the numeric Letterboxd filmId. It is not stored; filmId is derived
    from uid.
    """

    lid: str
    uid: str
    name: str
    slug: str
    url: str

    type: str = "film"
    typeName: str = "film"

    image125: Optional[str] = None
    image150: Optional[str] = None
    image230: Optional[str] = None

    releaseYear: Optional[int] = None
    runTime: Optional[int] = None
    originalName: Optional[str] = None

    filmlistAction: Optional[str] = None
    watchlistAction: Optional[str] = None

    directors: List[AutocompleteDirector] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AutocompleteFilm":
        return cls(
            lid=data["lid"],
            uid=data["uid"],
            type=data.get("type", "film"),
            typeName=data.get("typeName", "film"),
            name=data["name"],
            image125=data.get("image125"),
            image150=data.get("image150"),
            image230=data.get("image230"),
            releaseYear=data.get("releaseYear"),
            runTime=data.get("runTime"),
            slug=data["slug"],
            url=data["url"],
            originalName=data.get("originalName"),
            filmlistAction=data.get("filmlistAction"),
            watchlistAction=data.get("watchlistAction"),
            directors=[
                AutocompleteDirector.from_dict(director)
                for director in data.get("directors", [])
            ],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lid": self.lid,
            "uid": self.uid,
            "type": self.type,
            "typeName": self.typeName,
            "name": self.name,
            "image125": self.image125,
            "image150": self.image150,
            "image230": self.image230,
            "releaseYear": self.releaseYear,
            "runTime": self.runTime,
            "slug": self.slug,
            "url": self.url,
            "originalName": self.originalName,
            "filmlistAction": self.filmlistAction,
            "watchlistAction": self.watchlistAction,
            "directors": [
                director.to_dict()
                for director in self.directors
            ],
        }

    def to_film_summary(self) -> FilmSummary:
        """
        Convert this autocomplete result into an incomplete FilmSummary.
        """
        return FilmSummary(
            lid=self.lid,
            uid=self.uid,
            name=self.name,
            link=self.url,
            releaseYear=self.releaseYear,
            runTime=self.runTime,
        )

    @property
    def filmId(self) -> Optional[str]:
        if not self.uid or not self.uid.startswith("film:"):
            return None
        return self.uid[5:]

    @property
    def absolute_url(self) -> str:
        return urljoin(BASE_URL, self.url)

    @property
    def director_names(self) -> List[str]:
        return [director.name for director in self.directors]

    @property
    def display_name(self) -> str:
        if self.releaseYear:
            return f"{self.name} ({self.releaseYear})"
        return self.name


@dataclass
class AutocompleteFilmResponse:
    result: bool
    data: List[AutocompleteFilm]
    csrf: Optional[str] = None

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "AutocompleteFilmResponse":
        return cls(
            result=bool(payload.get("result", False)),
            csrf=payload.get("csrf"),
            data=[
                AutocompleteFilm.from_dict(item)
                for item in payload.get("data", [])
            ],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "result": self.result,
            "csrf": self.csrf,
            "data": [
                film.to_dict()
                for film in self.data
            ],
        }


class SearchManager:
    """
    Search Letterboxd using autocomplete endpoints.

    The film cache is keyed by film lid, because lid is the useful value for
    list mutation commands:

        {"film": "G5v2", "action": "ADD"}

    Secondary indexes are maintained for derived filmId, slug, and URL.
    """

    def __init__(self, session, cache_path: Optional[Union[str, Path]] = None):
        self.session = session
        self.cache_path = Path(cache_path) if cache_path else None

        self._csrf_token: Optional[str] = None

        self.films_by_lid: Dict[str, AutocompleteFilm] = {}
        self.films_by_filmId: Dict[str, AutocompleteFilm] = {}
        self.films_by_slug: Dict[str, AutocompleteFilm] = {}
        self.films_by_url: Dict[str, AutocompleteFilm] = {}

        if self.cache_path and self.cache_path.exists():
            self.load_cache(self.cache_path)

    def _cache_film(self, film: AutocompleteFilm) -> AutocompleteFilm:
        """
        Store a film in all cache indexes.
        """
        self.films_by_lid[film.lid] = film
        if film.filmId is not None:
            self.films_by_filmId[film.filmId] = film
        self.films_by_slug[film.slug] = film
        self.films_by_url[film.url] = film
        self.films_by_url[film.absolute_url] = film

        return film

    def _cache_films(self, films: List[AutocompleteFilm]) -> List[AutocompleteFilm]:
        for film in films:
            self._cache_film(film)

        return films

    def search_films(
        self,
        query: str,
        *,
        limit: int = 100,
        adult: bool = True,
        use_cache: bool = True,
    ) -> List[AutocompleteFilm]:
        """
        Search films using Letterboxd's autocompletefilm endpoint.

        Endpoint observed:

            GET /s/autocompletefilm?q=test&limit=100&timestamp=...&adult=true

        Returns a list of AutocompleteFilm records and caches them.
        """
        if not isinstance(query, str):
            raise TypeError("query must be a string")

        query = query.strip()

        if not query:
            raise ValueError("query must not be empty")

        if limit <= 0:
            raise ValueError("limit must be positive")

        cached_matches = self.find_cached_films(query)

        if use_cache and len(cached_matches) >= limit:
            return cached_matches[:limit]

        params = {
            "q": query,
            "limit": str(limit),
            "timestamp": str(int(time.time() * 1000)),
            "adult": "true" if adult else "false",
        }

        resp = self.session.get(
            f"{BASE_URL}/s/autocompletefilm",
            params=params,
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            },
        )
        resp.raise_for_status()

        payload = resp.json()
        parsed = AutocompleteFilmResponse.from_dict(payload)

        if parsed.csrf:
            self._csrf_token = parsed.csrf

        films = self._cache_films(parsed.data)

        if self.cache_path:
            self.save_cache(self.cache_path)

        return films

    def search_film_summaries(
        self,
        query: str,
        *,
        limit: int = 100,
        adult: bool = True,
        use_cache: bool = True,
    ) -> List[FilmSummary]:
        """
        Search films and return incomplete FilmSummary records.
        """
        return [
            film.to_film_summary()
            for film in self.search_films(
                query,
                limit=limit,
                adult=adult,
                use_cache=use_cache,
            )
        ]

    def find_cached_films(self, query: str) -> List[AutocompleteFilm]:
        """
        Search the local film cache by name, original name, slug, year, director,
        derived filmId, uid, lid, or URL.

        This does not hit Letterboxd.
        """
        if not query:
            return []

        needle = str(query).strip().lower()

        if not needle:
            return []

        matches = []

        for film in self.films_by_lid.values():
            haystack_parts = [
                film.lid,
                film.uid,
                film.filmId or "",
                film.name,
                film.originalName or "",
                film.slug,
                film.url,
                film.absolute_url,
                str(film.releaseYear or ""),
                " ".join(film.director_names),
            ]

            haystack = " ".join(haystack_parts).lower()

            if needle in haystack:
                matches.append(film)

        return matches

    def get_film(
        self,
        identifier: Union[str, int],
    ) -> Optional[AutocompleteFilm]:
        """
        Fetch a cached film by lid, filmId, slug, relative URL, absolute URL,
        or uid.
        """
        if isinstance(identifier, int):
            return self.films_by_filmId.get(str(identifier))

        key = str(identifier).strip()

        if not key:
            return None

        if key in self.films_by_lid:
            return self.films_by_lid[key]

        if key in self.films_by_slug:
            return self.films_by_slug[key]

        if key in self.films_by_url:
            return self.films_by_url[key]

        if key.startswith("film:"):
            return self.films_by_filmId.get(key.split(":", 1)[1])

        return self.films_by_filmId.get(key)

    def get_film_summary(
        self,
        identifier: Union[str, int],
    ) -> Optional[FilmSummary]:
        """
        Fetch a cached film and return it as an incomplete FilmSummary.
        """
        film = self.get_film(identifier)
        return film.to_film_summary() if film else None

    def get_lid(
        self,
        identifier: Union[str, int, AutocompleteFilm, FilmSummary],
    ) -> Optional[str]:
        """
        Return the cached film lid needed by ADD commands.
        """
        if isinstance(identifier, AutocompleteFilm):
            return identifier.lid

        if isinstance(identifier, FilmSummary):
            return identifier.lid

        film = self.get_film(identifier)
        return film.lid if film else None

    def resolve_lids(
        self,
        identifiers: List[Union[str, int, AutocompleteFilm, FilmSummary]],
    ) -> List[str]:
        """
        Resolve multiple cached identifiers to film lids.

        Raises if any identifier cannot be resolved from cache.
        """
        lids = []

        for identifier in identifiers:
            lid = self.get_lid(identifier)

            if not lid:
                raise ValueError(f"Could not resolve film identifier to lid: {identifier}")

            lids.append(lid)

        return lids

    def save_cache(
        self,
        path: Optional[Union[str, Path]] = None,
    ) -> None:
        """
        Save the film autocomplete cache to disk as JSON.
        """
        cache_path = Path(path) if path else self.cache_path

        if not cache_path:
            raise ValueError("No cache path provided")

        cache_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "films": [
                film.to_dict()
                for film in self.films_by_lid.values()
            ]
        }

        with cache_path.open("w", encoding="utf-8") as fp:
            json.dump(data, fp, ensure_ascii=False, indent=2)

    def load_cache(
        self,
        path: Optional[Union[str, Path]] = None,
    ) -> None:
        """
        Load the film autocomplete cache from disk.
        """
        cache_path = Path(path) if path else self.cache_path

        if not cache_path:
            raise ValueError("No cache path provided")

        with cache_path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)

        self.clear_cache()

        for record in data.get("films", []):
            self._cache_film(AutocompleteFilm.from_dict(record))

    def clear_cache(self) -> None:
        """
        Clear all in-memory cache indexes.
        """
        self.films_by_lid.clear()
        self.films_by_filmId.clear()
        self.films_by_slug.clear()
        self.films_by_url.clear()

    def cache_size(self) -> int:
        """
        Return the number of unique cached films.
        """
        return len(self.films_by_lid)


__all__ = ["SearchManager", "AutocompleteFilm", "AutocompleteFilmResponse", "AutocompleteDirector"]
