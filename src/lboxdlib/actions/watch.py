from __future__ import annotations

from typing import Dict, List, Optional
from urllib.parse import quote

from lboxdlib.auth import get_csrf

BASE_URL = "https://letterboxd.com"


# -------------------------------------------------
# Metadata
# -------------------------------------------------

def _metadata_request(session, film_ids: List[int]) -> dict:
    """
    Internal helper for:
        POST /ajax/letterboxd-metadata/

    film_ids are numeric Letterboxd film IDs.
    Request body repeats:
        productions=film:{id}
    """
    url = f"{BASE_URL}/ajax/letterboxd-metadata/"

    data = [("productions", f"film:{int(fid)}") for fid in film_ids]

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": BASE_URL,
        "Referer": f"{BASE_URL}/",
    }

    r = session.post(url, data=data, headers=headers)
    r.raise_for_status()
    return r.json()


def _normalize_metadata_ids(values) -> set[str]:
    """
    Normalize Letterboxd metadata IDs to {'film:<id>', ...}.

    The observed payload uses strings such as 'film:129928'. This also accepts
    bare ints/strings defensively so callers do not get false negatives if the
    response shape is slightly different.
    """
    out: set[str] = set()

    for value in values or []:
        if isinstance(value, int):
            out.add(f"film:{value}")
            continue

        raw = str(value).strip()
        if not raw:
            continue

        if raw.startswith("film:"):
            out.add(raw)
        elif raw.isdigit():
            out.add(f"film:{raw}")

    return out


def get_metadata(session, film_ids: List[int]) -> Dict[int, Dict[str, bool]]:
    """
    Batch fetch watched + watchlisted state.

    Returns:
        {
            1324074: {
                "watched": True,
                "watchlisted": False,
            }
        }
    """
    if not film_ids:
        return {}

    normalized_film_ids = [int(fid) for fid in film_ids]
    payload = _metadata_request(session, normalized_film_ids)

    watched_set = _normalize_metadata_ids(payload.get("watched", []))
    watchlisted_set = _normalize_metadata_ids(payload.get("watchlisted", []))

    result: Dict[int, Dict[str, bool]] = {}

    for fid in normalized_film_ids:
        uid = f"film:{fid}"
        result[fid] = {
            "watched": uid in watched_set,
            "watchlisted": uid in watchlisted_set,
        }

    return result


def get_watched_status(session, film_ids: List[int]) -> Dict[int, bool]:
    """
    Batch watched-only convenience wrapper.
    """
    meta = get_metadata(session, film_ids)
    return {int(fid): meta[int(fid)]["watched"] for fid in film_ids}


def get_watchlist_status(session, film_ids: List[int]) -> Dict[int, bool]:
    """
    Batch watchlist-only convenience wrapper.
    """
    meta = get_metadata(session, film_ids)
    return {int(fid): meta[int(fid)]["watchlisted"] for fid in film_ids}


# -------------------------------------------------
# Watchlist mutations
# -------------------------------------------------

def _film_referer(slug: Optional[str] = None) -> str:
    if slug:
        return f"{BASE_URL}/film/{slug}/"
    return f"{BASE_URL}/"


def _watchlist_patch(
    session,
    lid: str,
    value: bool,
    slug: Optional[str] = None,
) -> bool:
    """
    PATCH /api/v0/me/watchlist/{lid}

    lid = Letterboxd short code, e.g. "Tywk"
    """
    if not lid or not str(lid).strip():
        raise ValueError("lid must not be empty")

    csrf = get_csrf(session)

    url = f"{BASE_URL}/api/v0/me/watchlist/{quote(str(lid).strip(), safe='')}"

    headers = {
        "Content-Type": "application/json; charset=UTF-8",
        "X-CSRF-Token": csrf,
        "Origin": BASE_URL,
        "Referer": _film_referer(slug),
    }

    r = session.patch(
        url,
        json={"inWatchlist": bool(value)},
        headers=headers,
    )
    r.raise_for_status()

    return True


def set_watchlist(
    session,
    lid: str,
    watchlisted: bool,
    slug: Optional[str] = None,
) -> bool:
    """
    Set a film's watchlist state.
    """
    return _watchlist_patch(session, lid, bool(watchlisted), slug=slug)


def add_to_watchlist(
    session,
    lid: str,
    slug: Optional[str] = None,
) -> bool:
    """
    Add a film to the user's watchlist.

    lid  = Letterboxd short code, e.g. "Tywk"
    slug = film slug, used only for Referer when available
    """
    return _watchlist_patch(session, lid, True, slug=slug)


def remove_from_watchlist(
    session,
    lid: str,
    slug: Optional[str] = None,
) -> bool:
    """
    Remove a film from the user's watchlist.
    """
    return _watchlist_patch(session, lid, False, slug=slug)


def toggle_watchlist(
    session,
    *,
    film_id: int,
    lid: str,
    slug: Optional[str] = None,
) -> Dict[str, object]:
    """
    Toggle watchlist state by first reading current metadata.

    film_id is required to read current state.
    lid is required to mutate watchlist state.
    """
    fid = int(film_id)
    previous = get_metadata(session, [fid])[fid]["watchlisted"]
    new_value = not previous
    ok = set_watchlist(session, lid, new_value, slug=slug)

    return {
        "ok": ok,
        "film_id": fid,
        "lid": lid,
        "previous": previous,
        "watchlisted": new_value,
    }


# -------------------------------------------------
# Watched mutations
# -------------------------------------------------

def set_watched(
    session,
    film_id: int,
    watched: bool,
    slug: Optional[str] = None,
) -> bool:
    """
    POST /s/film:{id}/watch/

    Uses numeric film_id / uid, not lid.

    Form body:
        watched=true|false
        __csrf=<csrf>
    """
    fid = int(film_id)
    csrf = get_csrf(session)

    url = f"{BASE_URL}/s/film:{fid}/watch/"

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": BASE_URL,
        "Referer": _film_referer(slug),
    }

    data = {
        "watched": "true" if watched else "false",
        "__csrf": csrf,
    }

    r = session.post(url, data=data, headers=headers)
    r.raise_for_status()

    return True


def toggle_watched(
    session,
    *,
    film_id: int,
    slug: Optional[str] = None,
) -> Dict[str, object]:
    """
    Toggle watched state by first reading current metadata.
    """
    fid = int(film_id)
    previous = get_metadata(session, [fid])[fid]["watched"]
    new_value = not previous
    ok = set_watched(session, fid, new_value, slug=slug)

    return {
        "ok": ok,
        "film_id": fid,
        "previous": previous,
        "watched": new_value,
    }
