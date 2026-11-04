"""
Letterboxd diary entry helpers.

Proven traffic covered here:

CREATE:
    POST https://letterboxd.com/api/v0/production-log-entries

UPDATE:
    PATCH https://letterboxd.com/api/v0/production-log-entry/{entry_id}

DELETE:
    POST https://letterboxd.com/s/viewing:{viewing_id}/delete
    form body: __csrf={csrf}

Notes:
    - CREATE/UPDATE use the API log-entry id, e.g. "ePmttr".
    - DELETE uses the numeric viewing id, e.g. 1358500807.
    - The numeric viewing id is discoverable from the public review/diary page DOM.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol, TypeVar, cast

from lboxdlib.auth import get_csrf


LETTERBOXD_ORIGIN = "https://letterboxd.com"
CREATE_LOG_ENTRY_URL = f"{LETTERBOXD_ORIGIN}/api/v0/production-log-entries"
UPDATE_LOG_ENTRY_URL = f"{LETTERBOXD_ORIGIN}/api/v0/production-log-entry"
DELETE_VIEWING_URL = f"{LETTERBOXD_ORIGIN}/s/viewing"


class HasLid(Protocol):
    """Minimal FilmSummary-like shape needed by this module."""

    lid: str


@dataclass(slots=True)
class DiaryEntryResult:
    """
    Result wrapper for created/updated diary entries.

    entry_id:
        Short API log-entry id returned as ``response["logEntry"]["id"]``.
        Used by PATCH /api/v0/production-log-entry/{entry_id}.

    viewing_id:
        Numeric legacy/internal viewing id.
        Used by POST /s/viewing:{viewing_id}/delete.
        Usually extracted lazily from the public review URL.
    """

    entry_id: str
    url: str | None
    payload: dict[str, Any]
    response: dict[str, Any]
    viewing_id: int | None = None


class _Unset:
    pass


UNSET = _Unset()
T = TypeVar("T")


def _production_id(film_or_production_id: HasLid | str) -> str:
    production_id = getattr(film_or_production_id, "lid", film_or_production_id)

    if not isinstance(production_id, str) or not production_id:
        raise ValueError(
            "Expected a FilmSummary-like object with .lid or a raw production id string"
        )

    return production_id


def _date_string(viewing_date: date | datetime | str | None) -> str:
    if viewing_date is None:
        return date.today().isoformat()

    if isinstance(viewing_date, datetime):
        return viewing_date.date().isoformat()

    if isinstance(viewing_date, date):
        return viewing_date.isoformat()

    if not isinstance(viewing_date, str) or not viewing_date:
        raise ValueError("viewing_date must be a date, datetime, ISO date string, or None")

    return viewing_date


def _json_headers(session, *, referer: str = "https://letterboxd.com/films/") -> dict[str, str]:
    return {
        "content-type": "application/json; charset=UTF-8",
        "x-csrf-token": get_csrf(session),
        "origin": LETTERBOXD_ORIGIN,
        "referer": referer,
    }


def _form_headers(*, referer: str = "https://letterboxd.com/") -> dict[str, str]:
    return {
        "Origin": LETTERBOXD_ORIGIN,
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": referer,
    }


def _extract_url(log_entry: dict[str, Any]) -> str | None:
    links = log_entry.get("links") or []
    if not links:
        return None

    first = links[0]
    if not isinstance(first, dict):
        return None

    url = first.get("url")
    return url if isinstance(url, str) else None


def _extract_entry_result(
    *,
    payload: dict[str, Any],
    response_json: dict[str, Any],
    viewing_id: int | None = None,
) -> DiaryEntryResult:
    log_entry = response_json["logEntry"]

    return DiaryEntryResult(
        entry_id=log_entry["id"],
        url=_extract_url(log_entry),
        payload=deepcopy(payload),
        response=response_json,
        viewing_id=viewing_id,
    )


def build_create_diary_payload(
    film_or_production_id: HasLid | str,
    *,
    rating: float | int | None = None,
    review_text: str | None = None,
    tags: list[str] | tuple[str, ...] | None = None,
    viewing_date: date | datetime | str | None = None,
    rewatch: bool = False,
    like: bool = False,
    contains_spoilers: bool = False,
) -> dict[str, Any]:
    """
    Build the proven CREATE payload.

    Minimal useful payload:
        {
            "productionId": "MDJY",
            "diaryDetails": {"diaryDate": "YYYY-MM-DD", "rewatch": False},
            "tags": [],
            "like": False,
            "rating": 3,
        }

    When review_text is present:
        "review": {"text": review_text, "containsSpoilers": bool}
    """

    payload: dict[str, Any] = {
        "productionId": _production_id(film_or_production_id),
        "diaryDetails": {
            "diaryDate": _date_string(viewing_date),
            "rewatch": bool(rewatch),
        },
        "tags": list(tags or []),
        "like": bool(like),
    }

    if rating is not None:
        payload["rating"] = rating

    if review_text is not None:
        payload["review"] = {
            "text": review_text,
            "containsSpoilers": bool(contains_spoilers),
        }

    return payload


def save_diary_entry(
    session,
    film_or_production_id: HasLid | str,
    *,
    rating: float | int | None = None,
    review_text: str | None = None,
    tags: list[str] | tuple[str, ...] | None = None,
    viewing_date: date | datetime | str | None = None,
    rewatch: bool = False,
    like: bool = False,
    contains_spoilers: bool = False,
    hydrate_viewing_id: bool = False,
) -> DiaryEntryResult:
    """
    Create a Letterboxd diary entry.

    Examples:
        created = save_diary_entry(session, film, rating=3)

        created = save_diary_entry(
            session,
            film.lid,
            rating=3,
            review_text="This is my review.",
            tags=["sample", "tag with spaces"],
            contains_spoilers=False,
        )

    Returns:
        DiaryEntryResult, where:
            result.entry_id is the API id for later PATCH updates.
            result.url is the public review/diary URL.
            result.viewing_id is optionally hydrated for deletion.
    """

    payload = build_create_diary_payload(
        film_or_production_id,
        rating=rating,
        review_text=review_text,
        tags=tags,
        viewing_date=viewing_date,
        rewatch=rewatch,
        like=like,
        contains_spoilers=contains_spoilers,
    )

    response = session.post(
        CREATE_LOG_ENTRY_URL,
        headers=_json_headers(session),
        json=payload,
    )
    response.raise_for_status()

    result = _extract_entry_result(payload=payload, response_json=response.json())

    if hydrate_viewing_id and result.url:
        result.viewing_id = get_viewing_id_from_review_url(session, result.url)

    return result


def build_update_diary_payload(
    entry: DiaryEntryResult | None = None,
    *,
    base_payload: dict[str, Any] | None = None,
    rating: float | int | None | _Unset = UNSET,
    review_text: str | None | _Unset = UNSET,
    tags: list[str] | tuple[str, ...] | None | _Unset = UNSET,
    viewing_date: date | datetime | str | None | _Unset = UNSET,
    rewatch: bool | _Unset = UNSET,
    like: bool | _Unset = UNSET,
    contains_spoilers: bool | _Unset = UNSET,
    privacy_policy: str | None | _Unset = UNSET,
) -> dict[str, Any]:
    """
    Build the proven UPDATE payload.

    Observed PATCH traffic sent the full editable state, not a sparse patch.
    This builder therefore starts from a known base payload and applies changes.

    Preferred:
        build_update_diary_payload(entry, review_text="New text")

    If no DiaryEntryResult is available:
        build_update_diary_payload(base_payload=existing_payload, like=True)
    """

    if entry is not None:
        payload = deepcopy(entry.payload)
    elif base_payload is not None:
        payload = deepcopy(base_payload)
    else:
        raise ValueError("entry or base_payload is required to build an update payload")

    # PATCH payload does not include productionId.
    payload.pop("productionId", None)

    # Browser PATCH included privacyPolicy, even as null.
    payload.setdefault("privacyPolicy", None)

    if viewing_date is not UNSET:
        payload.setdefault("diaryDetails", {})["diaryDate"] = _date_string(
            cast(date | datetime | str | None, viewing_date)
        )

    if rewatch is not UNSET:
        payload.setdefault("diaryDetails", {})["rewatch"] = bool(rewatch)

    if tags is not UNSET:
        payload["tags"] = list(cast(list[str] | tuple[str, ...] | None, tags) or [])

    if rating is not UNSET:
        if rating is None:
            payload.pop("rating", None)
        else:
            payload["rating"] = rating

    if like is not UNSET:
        payload["like"] = bool(like)

    if review_text is not UNSET:
        if review_text is None:
            # Removal of review text was not part of the captured traffic.
            # We omit the review key rather than guessing a deletion semantics.
            payload.pop("review", None)
        else:
            existing_review = payload.get("review") or {}
            payload["review"] = {
                "text": review_text,
                "containsSpoilers": (
                    bool(contains_spoilers)
                    if contains_spoilers is not UNSET
                    else bool(existing_review.get("containsSpoilers", False))
                ),
            }
    elif contains_spoilers is not UNSET:
        existing_review = payload.get("review")
        if not isinstance(existing_review, dict):
            raise ValueError("contains_spoilers was provided, but payload has no review")
        existing_review["containsSpoilers"] = bool(contains_spoilers)

    if privacy_policy is not UNSET:
        payload["privacyPolicy"] = privacy_policy

    return payload


def update_diary_entry(
    session,
    entry: DiaryEntryResult | str,
    *,
    base_payload: dict[str, Any] | None = None,
    rating: float | int | None | _Unset = UNSET,
    review_text: str | None | _Unset = UNSET,
    tags: list[str] | tuple[str, ...] | None | _Unset = UNSET,
    viewing_date: date | datetime | str | None | _Unset = UNSET,
    rewatch: bool | _Unset = UNSET,
    like: bool | _Unset = UNSET,
    contains_spoilers: bool | _Unset = UNSET,
    privacy_policy: str | None | _Unset = UNSET,
    referer: str = "https://letterboxd.com/films/",
) -> DiaryEntryResult:
    """
    Update an existing Letterboxd diary entry.

    Preferred:
        updated = update_diary_entry(
            session,
            created,
            review_text="Updated review.",
            like=True,
        )

    Raw entry-id path:
        updated = update_diary_entry(
            session,
            "ePmttr",
            base_payload=created.payload,
            like=True,
        )

    Important:
        entry_id is the API log-entry id, not the numeric viewing id.
    """

    if isinstance(entry, DiaryEntryResult):
        entry_id = entry.entry_id
        viewing_id = entry.viewing_id
        payload = build_update_diary_payload(
            entry,
            rating=rating,
            review_text=review_text,
            tags=tags,
            viewing_date=viewing_date,
            rewatch=rewatch,
            like=like,
            contains_spoilers=contains_spoilers,
            privacy_policy=privacy_policy,
        )
    else:
        entry_id = entry
        viewing_id = None
        payload = build_update_diary_payload(
            base_payload=base_payload,
            rating=rating,
            review_text=review_text,
            tags=tags,
            viewing_date=viewing_date,
            rewatch=rewatch,
            like=like,
            contains_spoilers=contains_spoilers,
            privacy_policy=privacy_policy,
        )

    if not entry_id:
        raise ValueError("entry_id is required")

    response = session.patch(
        f"{UPDATE_LOG_ENTRY_URL}/{entry_id}",
        headers=_json_headers(session, referer=referer),
        json=payload,
    )
    response.raise_for_status()

    return _extract_entry_result(
        payload=payload,
        response_json=response.json(),
        viewing_id=viewing_id,
    )


def extract_viewing_id_from_html(html: str) -> int:
    """
    Extract the numeric viewing id from a Letterboxd diary/review page DOM.

    Proven DOM examples:
        data-object-id="viewing:1358500807"
        /ajax/viewing:1358500807/comments/
        /s/report/viewing:1358500807/
        sharing-toggle-body-viewing-1358500807
    """

    patterns = [
        r'data-object-id=["\']viewing:(\d+)["\']',
        r"/ajax/viewing:(\d+)/comments/",
        r"/s/report/viewing:(\d+)/",
        r"sharing-toggle-body-viewing-(\d+)",
        r"/viewing/(\d+)/json/",
        r"viewing:(\d+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            return int(match.group(1))

    raise ValueError("Could not find numeric viewing_id in review page HTML")


def get_viewing_id_from_review_url(session, review_url: str) -> int:
    """
    Fetch a Letterboxd diary/review URL and extract its numeric viewing id.
    """

    if not review_url:
        raise ValueError("review_url is required")

    response = session.get(
        review_url,
        headers={
            "accept": "text/html,*/*",
            "referer": "https://letterboxd.com/",
        },
    )
    response.raise_for_status()

    return extract_viewing_id_from_html(response.text)


def _coerce_viewing_id(value: int | str) -> int:
    """
    Accept:
        1358500807
        "1358500807"
        "viewing:1358500807"

    Reject:
        "ePmttr"  # API log-entry id, not numeric viewing id.
    """

    if isinstance(value, int):
        return value

    value = str(value).strip()

    if value.startswith("viewing:"):
        value = value.split(":", 1)[1]

    if not value.isdigit():
        raise ValueError(
            f"Expected numeric viewing_id, got {value!r}. "
            "The API logEntry id is not the same as the numeric viewing id."
        )

    return int(value)


def delete_diary_entry(
    session,
    entry: DiaryEntryResult | int | str,
    *,
    referer: str = "https://letterboxd.com/",
) -> bool:
    """
    Delete a Letterboxd diary/review entry.

    Preferred:
        delete_diary_entry(session, created_or_updated_result)

    Also valid:
        delete_diary_entry(session, 1358500807)
        delete_diary_entry(session, "viewing:1358500807")

    Not valid:
        delete_diary_entry(session, "ePmttr")

    Route:
        POST /s/viewing:{viewing_id}/delete

    Success signal:
        302 redirect to a diary page.
    """

    if isinstance(entry, DiaryEntryResult):
        if entry.viewing_id is not None:
            viewing_id = entry.viewing_id
        elif entry.url:
            viewing_id = get_viewing_id_from_review_url(session, entry.url)
            entry.viewing_id = viewing_id
        else:
            raise ValueError(
                "Cannot delete DiaryEntryResult without viewing_id or url. "
                "The API logEntry id alone is not enough for this delete route."
            )
    else:
        viewing_id = _coerce_viewing_id(entry)

    csrf = get_csrf(session)

    response = session.post(
        f"{DELETE_VIEWING_URL}:{viewing_id}/delete",
        headers=_form_headers(referer=referer),
        data={"__csrf": csrf},
        allow_redirects=False,
    )

    location = response.headers.get("Location", "")

    if response.status_code == 302 and "/diary/" in location:
        return True

    if response.status_code in (401, 403):
        raise PermissionError("Not authorized to delete diary entry")

    if response.status_code == 404:
        return False

    raise RuntimeError(
        "Unexpected response deleting diary entry: "
        f"{response.status_code} Location={location!r} Body={response.text[:500]!r}"
    )
