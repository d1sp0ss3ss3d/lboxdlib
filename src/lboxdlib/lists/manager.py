"""
Letterboxd List Management Module

Uses an authenticated curl-cffi session and the provided auth utilities.
"""

import json
import re
from typing import Dict, List, Optional, Tuple, Union
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from lboxdlib.auth import get_csrf, get_signed_in_user

BASE_URL = "https://letterboxd.com"


class ListManager:
    """
    Manage Letterboxd lists of a logged-in user.
    
    Provides methods to list all lists, check film membership,
    add/remove films, reorder films, fetch list details, and keep a
    URL-keyed cache of the user's lists synchronized with mutations.

    self.map is keyed by canonical list URL:

        {
            "https://letterboxd.com/{username}/list/{list_slug}/": {
                ...
            }
        }
    """

    def __init__(self, session):
        """
        :param session: Authenticated curl-cffi session object.
        """
        self.session = session
        self._username = None
        self._csrf_token = None
        self.map = {}

    @property
    def username(self) -> str:
        """Return the logged-in username, fetched once."""
        if not self._username:
            self._username = get_signed_in_user(self.session)
        return self._username

    def _get_csrf_token(self) -> str:
        """Fetch a CSRF token, cached after first retrieval."""
        if not self._csrf_token:
            self._csrf_token = get_csrf(self.session)
        return self._csrf_token

    def _canonical_list_url(self, list_slug: str, username: Optional[str] = None) -> str:
        """
        Return the canonical URL for a list slug.

        If username is omitted, use the logged-in user.
        If username is provided, build a URL for that user's list.
        """
        owner = username or self.username
        return f"{BASE_URL}/{owner}/list/{list_slug}/"

    def _list_url_from_info(self, list_info: Dict) -> str:
        """
        Build a canonical Letterboxd list URL from one list record returned
        by /s/load-lists.
        """
        slug = list_info.get("slug")
        if not slug:
            raise ValueError(f"List info does not contain slug: {list_info}")

        return self._canonical_list_url(slug)

    def _extract_lid_from_soup(self, soup: BeautifulSoup) -> Optional[str]:
        """
        Extract the internal Letterboxd list identifier, lid, from a parsed list page.

        Resolution order:

            1. #userpanel data-list-identifier
               Common on public list pages, including other users' lists.

            2. #film-list-editor-lid
               Present when the editor DOM is available.

            3. data-list-identifier on LazyPoster components
               Common on own list pages / list pages with poster metadata.

            4. window.listData fallback
        """

        def lid_from_identifier(raw_identifier: Optional[str]) -> Optional[str]:
            if not raw_identifier:
                return None

            try:
                list_identifier = json.loads(raw_identifier)
            except (TypeError, json.JSONDecodeError):
                return None

            lid = list_identifier.get("lid")
            return str(lid) if lid else None

        # 1. Public list sidebar/user panel.
        userpanel = soup.select_one("#userpanel[data-list-identifier]")
        if userpanel:
            lid = lid_from_identifier(userpanel.get("data-list-identifier"))
            if lid:
                return lid

        # 2. Editor DOM, when present.
        lid_input = soup.select_one("#film-list-editor-lid")
        if lid_input and lid_input.get("value"):
            return lid_input["value"]

        # 3. Normal list page poster metadata.
        poster_with_identifier = soup.select_one(
            '.react-component[data-component-class="LazyPoster"][data-list-identifier]'
        )
        if poster_with_identifier:
            lid = lid_from_identifier(poster_with_identifier.get("data-list-identifier"))
            if lid:
                return lid

        # 4. Inline listData fallback.
        script_tag = soup.find("script", string=re.compile(r"window\.listData\s*="))
        if script_tag and script_tag.string:
            match = re.search(r'"lid":\s*"?([^",}]+)"?', script_tag.string)
            if match:
                return match.group(1)

        return None

    def get_list_lid(
        self,
        list_slug: str,
        *,
        username: Optional[str] = None,
        force_refresh: bool = False,
    ) -> str:
        """
        Resolve the internal Letterboxd list identifier, lid, for a list slug.

        If username is omitted, resolves one of the signed-in user's own lists.

        If username is provided, resolves another user's public list:

            get_list_lid("some-list-slug", username="otheruser")

        This is the identifier required for:

            PATCH /api/v0/list/{lid}

        Note:
            Fetching another user's list lid may work for reading/copying metadata,
            but PATCH operations should generally only be expected to work for lists
            owned by the authenticated account.
        """
        url = self._canonical_list_url(list_slug, username=username)

        if not force_refresh:
            cached = self.map.get(url)
            if cached and cached.get("lid"):
                return cached["lid"]

        resp = self.session.get(url)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        lid = self._extract_lid_from_soup(soup)

        if not lid:
            owner = username or self.username
            raise ValueError(
                f"Could not determine list identifier (lid) for '{owner}/list/{list_slug}'"
            )

        existing = self.map.get(url, {})

        self.map[url] = {
            **existing,
            "url": url,
            "username": username or self.username,
            "slug": list_slug,
            "lid": lid,
        }

        return lid

    def get_all_lists(self) -> Tuple[List[Dict], List[Dict]]:
        """
        Retrieve all public and private lists for the logged-in user.

        Returns:
            (public_lists, private_lists)

        Each list is a dict with keys such as:
            id, name, description, filmCount, slug, etc.
        """
        # Use a dummy filmId to fetch list data.
        resp = self.session.post(
            f"{BASE_URL}/s/load-lists",
            data={"filmId": "2"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        resp.raise_for_status()

        data = resp.json()

        if data.get("csrf"):
            self._csrf_token = data["csrf"]

        return data.get("publicLists", []), data.get("privateLists", [])

    def refresh_lists_map(self) -> Dict[str, Dict]:
        """
        Refresh the top-level list map from Letterboxd.

        Keys are canonical list URLs.
        Values are the raw Letterboxd list dicts plus normalized metadata.

        This does not fully load each list's films. It only stores the list
        records returned by /s/load-lists. Use refresh_list_in_map() to load
        one list's page and film entries.
        """
        public_lists, private_lists = self.get_all_lists()

        new_map = {}

        for visibility, lists in (
            ("public", public_lists),
            ("private", private_lists),
        ):
            for lst in lists:
                url = self._list_url_from_info(lst)

                existing = self.map.get(url, {})

                new_map[url] = {
                    **existing,
                    **lst,
                    "url": url,
                    "visibility": visibility,
                    "films": existing.get("films"),
                    "lid": existing.get("lid"),
                    "loaded": existing.get("loaded", False),
                }

        self.map = new_map
        return self.map

    def get_list_from_map(self, list_url: str) -> Optional[Dict]:
        """
        Return a cached list record by canonical list URL.
        """
        return self.map.get(list_url)

    def _find_list_url_by_id(self, list_id: Union[int, str]) -> Optional[str]:
        """
        Find a cached list URL by Letterboxd list ID.
        """
        wanted = int(list_id)

        for url, lst in self.map.items():
            try:
                if int(lst.get("id")) == wanted:
                    return url
            except (TypeError, ValueError):
                continue

        return None

    def _find_list_slug_by_id(self, list_id: Union[int, str]) -> Optional[str]:
        """
        Find a cached list slug by Letterboxd list ID.
        """
        url = self._find_list_url_by_id(list_id)

        if not url:
            return None

        return self.map[url].get("slug")

    def _parse_list_page(self, list_slug: str) -> Dict:
        """
        Fetch and parse a Letterboxd list page.

        Returns a normalized dict containing:
            url, slug, lid, name, films, loaded
        """
        url = self._canonical_list_url(list_slug)

        resp = self.session.get(url)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")

        lid = self._extract_lid_from_soup(soup)

        title_el = soup.select_one("h1")
        name = title_el.get_text(strip=True) if title_el else None

        films = []

        entries = soup.select("li.film-list-entry")

        for index, entry in enumerate(entries):
            poster = entry.select_one(
                '.react-component[data-component-class="LazyPoster"]'
            )

            if not poster:
                continue

            film = {
                "index": index,
                "filmId": poster.get("data-film-id") or entry.get("data-film-id"),
                "film_name": entry.get("data-film-name"),
                "film_year": entry.get("data-film-year"),
                "film_slug": poster.get("data-item-slug"),
                "film_url": urljoin(BASE_URL, poster.get("data-item-link", "")),
                "owner_rating": entry.get("data-film-owner-rating"),
                "average_rating": entry.get("data-film-average-rating"),
                "length": entry.get("data-film-length"),
            }

            raw_index = poster.get("data-list-index")
            if raw_index is not None:
                try:
                    film["list_index"] = int(raw_index)
                except ValueError:
                    film["list_index"] = index
            else:
                film["list_index"] = index

            films.append(film)

        return {
            "url": url,
            "slug": list_slug,
            "lid": lid,
            "name": name,
            "films": films,
            "loaded": True,
        }

    def refresh_list_in_map(self, list_slug: str) -> Dict:
        """
        Refresh one list and store it in self.map using its canonical URL as the key.
        """
        parsed = self._parse_list_page(list_slug)
        url = parsed["url"]

        existing = self.map.get(url, {})

        self.map[url] = {
            **existing,
            **parsed,
        }

        return self.map[url]

    def get_film_memberships(self, filmId: Union[int, str]) -> Dict:
        """
        Fetch list membership info for a specific film.

        Returns a dict with keys:
            csrf, publicLists, privateLists

        Each list entry includes a 'position' key, null if the film is not in list.
        """
        resp = self.session.post(
            f"{BASE_URL}/s/load-lists",
            data={"filmId": str(filmId)},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        resp.raise_for_status()

        data = resp.json()

        if data.get("csrf"):
            self._csrf_token = data["csrf"]

        return data

    def add_film_to_list(
        self,
        list_id: Union[int, str],
        filmId: Union[int, str],
    ) -> Dict:
        """
        Add a film to a list using Letterboxd's /s/add-film-to-list endpoint.

        The list identifier comes first for consistency with other list operations.

        After success, refresh the affected cached list if its slug can be found.
        """
        if not self.map:
            self.refresh_lists_map()

        csrf = self._get_csrf_token()

        resp = self.session.post(
            f"{BASE_URL}/s/add-film-to-list",
            data={
                "filmId": str(filmId),
                "filmListId": str(list_id),
                "__csrf": csrf,
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        resp.raise_for_status()

        data = resp.json()

        slug = self._find_list_slug_by_id(list_id)

        if not slug:
            slug = self._extract_slug_from_add_response(data)

        if slug:
            self.refresh_list_in_map(slug)

        return data

    def _extract_slug_from_add_response(self, response: Dict) -> Optional[str]:
        """
        Parse the list slug from the 'messages' field of an add response.
        """
        messages = response.get("messages", [])

        if messages:
            match = re.search(r'href="/([^/]+)/list/([^/]+)/"', messages[0])
            if match:
                return match[2]

        return None

    def get_film_data_from_list(
        self,
        list_slug: str,
        filmId: Union[int, str],
    ) -> Dict:
        """
        Fetch a list page and return the film's current list position plus list lid.

        Returns:

            {
                "index": 0-based index if found, otherwise -1,
                "lid": list lid if found, otherwise None
            }

        This mirrors the working JS getFilmDataFromList(listUrl, filmId).
        """
        lid, position = self._get_list_lid_and_film_position(list_slug, filmId)

        return {
            "index": position,
            "lid": lid,
        }

    def _get_list_lid_and_film_position(
        self,
        list_slug: str,
        filmId: Union[int, str],
    ) -> Tuple[Optional[str], int]:
        """
        Fetch the list page and extract:
            - the list identifier, lid
            - the film's current 0-based position

        If the film is not found, position is -1.

        Important:
            lid is a property of the list, so it is extracted independently
            from whether the target film is found.
        """
        filmId = str(filmId)
        url = self._canonical_list_url(list_slug)

        resp = self.session.get(url)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")

        lid = self._extract_lid_from_soup(soup)
        film_position = -1

        posters = soup.select(
            '.react-component[data-component-class="LazyPoster"]'
        )

        for poster in posters:
            poster_filmId = poster.get("data-film-id")

            if poster_filmId == filmId:
                raw_index = poster.get("data-list-index")

                if raw_index is not None:
                    try:
                        film_position = int(raw_index)
                    except ValueError:
                        film_position = -1

                # If no data-list-index exists, fall back to parent li position.
                if film_position == -1:
                    parent_entry = poster.find_parent("li", class_="film-list-entry")
                    if parent_entry:
                        all_entries = soup.select("li.film-list-entry")
                        for index, entry in enumerate(all_entries):
                            if entry is parent_entry:
                                film_position = index
                                break

                break

        return lid, film_position

    def batch_operation(
        self,
        list_slug: str,
        entries: List[Dict],
    ) -> Dict:
        """
        Apply a batch of list operations to a Letterboxd list.

        The list identifier comes first for consistency with add/remove/move.

        entries should be a list of command dicts, for example:

            [
                {"action": "UPDATE", "position": 0, "newPosition": 1},
                {"action": "DELETE", "position": 10},
                {"action": "ADD", "film": "G5v2"},
            ]

        Returns the JSON response from the PATCH request.

        After success, refreshes the affected cached list.
        """
        if not isinstance(entries, list):
            raise TypeError("entries must be a list of command dictionaries")

        if not entries:
            raise ValueError("entries must contain at least one command")

        for entry in entries:
            if not isinstance(entry, dict):
                raise TypeError("each entry must be a dictionary")

            if "action" not in entry:
                raise ValueError(f"entry is missing required 'action': {entry}")

        lid = self.get_list_lid(list_slug)
        csrf = self._get_csrf_token()
        endpoint = f"{BASE_URL}/api/v0/list/{lid}"

        payload = {
            "entries": entries,
        }

        resp = self.session.patch(
            endpoint,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "x-csrf-token": csrf,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        resp.raise_for_status()

        data = resp.json()

        self.refresh_list_in_map(list_slug)

        return data

    def remove_film_from_list(
        self,
        list_slug: str,
        *,
        filmId: Optional[Union[int, str]] = None,
        position: Optional[int] = None,
    ) -> Dict:
        """
        Remove a film from a list.

        The list identifier comes first.

        You may remove by known 0-based position:

            remove_film_from_list("my-list", position=10)

        Or by numeric filmId:

            remove_film_from_list("my-list", filmId=682535)

        DELETE commands require a 0-based position, so removing by filmId first
        resolves the film's current position from the list page.
        """
        if position is None and filmId is None:
            raise ValueError("Either position or filmId must be provided")

        if position is not None and filmId is not None:
            raise ValueError("Provide either position or filmId, not both")

        if position is not None:
            if not isinstance(position, int):
                raise TypeError("position must be an integer")

            if position < 0:
                raise ValueError("position must be a non-negative integer")

            resolved_position = position

        else:
            _, resolved_position = self._get_list_lid_and_film_position(
                list_slug,
                filmId,
            )

            if resolved_position == -1:
                raise ValueError(f"Film {filmId} not found in list '{list_slug}'")

        return self.batch_operation(
            list_slug,
            entries=[
                {
                    "action": "DELETE",
                    "position": resolved_position,
                }
            ],
        )

    def remove_film_from_list_by_id(
        self,
        list_id: Union[int, str],
        filmId: Union[int, str],
    ) -> Dict:
        """
        Remove a film from a list using the numeric Letterboxd list ID.

        The list identifier comes first.

        Resolves the list slug from the cached map.
        """
        if not self.map:
            self.refresh_lists_map()

        slug = self._find_list_slug_by_id(list_id)

        if not slug:
            raise RuntimeError(f"Could not determine list slug for list ID {list_id}")

        return self.remove_film_from_list(
            slug,
            filmId=filmId,
        )

    def toggle_film_in_list(
        self,
        list_id: Union[int, str],
        filmId: Union[int, str],
    ) -> Dict:
        """
        Toggle membership.

        The list identifier comes first.

        If the film is in the list, remove it.
        If the film is not in the list, add it.

        The affected list is refreshed by the underlying add/remove method.
        """
        if not self.map:
            self.refresh_lists_map()

        memberships = self.get_film_memberships(filmId)
        all_lists = memberships.get("publicLists", []) + memberships.get("privateLists", [])

        list_info = next(
            (lst for lst in all_lists if int(lst["id"]) == int(list_id)),
            None,
        )

        if not list_info:
            raise ValueError(f"List ID {list_id} not found in user's lists.")

        if list_info.get("position") is not None:
            slug = self._find_list_slug_by_id(list_id)

            if not slug:
                raise RuntimeError(f"Could not determine list slug for list ID {list_id}")

            return self.remove_film_from_list(
                slug,
                position=int(list_info["position"]),
            )

        return self.add_film_to_list(
            list_id,
            filmId,
        )

    def move_film_in_list(
        self,
        list_slug: str,
        start: int,
        new: int,
    ) -> Dict:
        """
        Move a film within a list from one 0-based position to another.

        The list identifier comes first.
        """
        if not isinstance(start, int):
            raise TypeError("start must be an integer")

        if not isinstance(new, int):
            raise TypeError("new must be an integer")

        if start < 0 or new < 0:
            raise ValueError("start and new must be non-negative integers")

        if start == new:
            return {
                "ok": True,
                "message": "start and new are the same; no reorder needed.",
            }

        return self.batch_operation(
            list_slug,
            entries=[
                {
                    "action": "UPDATE",
                    "position": start,
                    "newPosition": new,
                }
            ],
        )

    def batch_remove(
        self,
        list_slug: str,
        *,
        positions: Optional[List[int]] = None,
        filmIds: Optional[List[Union[int, str]]] = None,
    ) -> Dict:
        """
        Remove multiple films from a list.

        The list identifier comes first.

        You may remove by known 0-based positions:

            batch_remove("my-list", positions=[10, 11, 12])

        Or by numeric filmIds:

            batch_remove("my-list", filmIds=[682535, 123456])

        Provide either positions or filmIds, not both.

        DELETE commands require positions, so removing by filmIds first resolves
        each film's current position from the cached/fetched list data.
        """
        if positions is None and filmIds is None:
            raise ValueError("Either positions or filmIds must be provided")

        if positions is not None and filmIds is not None:
            raise ValueError("Provide either positions or filmIds, not both")

        if positions is not None:
            if not isinstance(positions, list):
                raise TypeError("positions must be a list of integers")

            if not positions:
                raise ValueError("positions must contain at least one position")

            for position in positions:
                if not isinstance(position, int):
                    raise TypeError(f"position must be an integer: {position}")

                if position < 0:
                    raise ValueError(f"position must be non-negative: {position}")

            # Important: delete from highest position to lowest so earlier deletes
            # do not shift the positions of later deletes.
            resolved_positions = sorted(set(positions), reverse=True)

        else:
            if not isinstance(filmIds, list):
                raise TypeError("filmIds must be a list")

            if not filmIds:
                raise ValueError("filmIds must contain at least one filmId")

            cached = self.refresh_list_in_map(list_slug)
            films = cached.get("films") or []

            position_by_filmId = {
                str(film.get("filmId")): film.get("index")
                for film in films
                if film.get("filmId") is not None
            }

            resolved_positions = []

            for filmId in filmIds:
                key = str(filmId)

                if key not in position_by_filmId:
                    raise ValueError(f"Film {filmId} not found in list '{list_slug}'")

                position = position_by_filmId[key]

                if position is None:
                    raise ValueError(
                        f"Could not determine position for film {filmId} "
                        f"in list '{list_slug}'"
                    )

                resolved_positions.append(position)

            # Same reason: delete from highest position to lowest.
            resolved_positions = sorted(set(resolved_positions), reverse=True)

        entries = [
            {
                "action": "DELETE",
                "position": position,
            }
            for position in resolved_positions
        ]

        return self.batch_operation(
            list_slug,
            entries=entries,
        )

    def batch_add(
        self,
        list_slug: str,
        lids: List[str],
    ) -> Dict:
        """
        Add multiple films to a list.

        The list identifier comes first.

        Example:

            batch_add("my-list", lids=["G5v2", "sDAi"])

        The PATCH list API expects ADD commands to use the Letterboxd list-film
        identifier in the "film" field, matching:

            {"action": "ADD", "film": "G5v2"}
        """
        if not isinstance(lids, list):
            raise TypeError("lids must be a list")

        if not lids:
            raise ValueError("lids must contain at least one lid")

        entries = [
            {
                "action": "ADD",
                "film": str(lid),
            }
            for lid in lids
        ]

        return self.batch_operation(
            list_slug,
            entries=entries,
        )

    def create_list_with_lid_list(
        self,
        name: str,
        lids: List[str],
        *,
        published: bool = False,
        share_policy: str = "You",
        ranked: bool = False,
        description: str = "",
        tags: Optional[List[str]] = None,
    ) -> Dict:

        entries = [
            {
                "action": "ADD",
                "film": str(lid),
            }
            for lid in lids
        ]
        
        return self.create_list(
            name,
            entries,
            published=published,
            share_policy=share_policy,
            ranked=ranked,
            description=description,
            tags=tags)

    def create_list(
        self,
        name: str,
        entries: List[Dict],
        *,
        published: bool = False,
        share_policy: str = "You",
        ranked: bool = False,
        description: str = "",
        tags: Optional[List[str]] = None,
    ) -> Dict:
        """
        Create a new Letterboxd list from scratch.

        Uses:

            POST /api/v0/lists

        Payload shape:

            {
                "published": false,
                "name": "test list",
                "sharePolicy": "You",
                "ranked": false,
                "description": "fear",
                "tags": ["adfa"],
                "entries": [
                    {"film": "G5v2", "action": "ADD"}
                ]
            }

        entries must contain at least one command.

        share_policy must be one of:
            "You", "Anyone", "Friends"

        After success, refreshes the top-level list map.
        """
        valid_share_policies = {"You", "Anyone", "Friends"}

        if not isinstance(name, str):
            raise TypeError("name must be a string")

        name = name.strip()

        if not name:
            raise ValueError("name must not be empty")

        if share_policy not in valid_share_policies:
            raise ValueError(
                f"share_policy must be one of {sorted(valid_share_policies)}"
            )

        if not isinstance(entries, list):
            raise TypeError("entries must be a list of command dictionaries")

        if not entries:
            raise ValueError("entries must contain at least one command")

        for entry in entries:
            if not isinstance(entry, dict):
                raise TypeError("each entry must be a dictionary")

            if "action" not in entry:
                raise ValueError(f"entry is missing required 'action': {entry}")

        if tags is None:
            tags = []

        if not isinstance(tags, list):
            raise TypeError("tags must be a list of strings")

        for tag in tags:
            if not isinstance(tag, str):
                raise TypeError(f"tag must be a string: {tag}")

        csrf = self._get_csrf_token()
        endpoint = f"{BASE_URL}/api/v0/lists"

        payload = {
            "published": bool(published),
            "name": name,
            "sharePolicy": share_policy,
            "ranked": bool(ranked),
            "description": description or "",
            "tags": tags,
            "entries": entries,
        }

        resp = self.session.post(
            endpoint,
            json=payload,
            headers={
                "Content-Type": "application/json; charset=UTF-8",
                "x-csrf-token": csrf,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        resp.raise_for_status()

        data = resp.json()

        # A new list now exists, so refresh the URL-keyed cache.
        self.refresh_lists_map()

        return data