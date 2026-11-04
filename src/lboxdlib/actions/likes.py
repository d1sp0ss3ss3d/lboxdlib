
"""
Letterboxd like-state helpers for review/diary viewings.

Proven traffic covered here:

LIKE / UNLIKE REVIEW:
    POST https://letterboxd.com/s/viewing:{viewing_id}/like/

Payload:
    liked=true&__csrf={csrf}
    liked=false&__csrf={csrf}

Response shape:
    {
        "result": true,
        "csrf": "...",
        "count": 1,
        "liked": true,
        "likeable": {
            "typeName": "viewing",
            "id": 1358506798,
            "boxdItCode": "ePnyLH"
        }
    }

REVIEW LIKE-STATE LOOKUP:
    POST https://letterboxd.com/ajax/letterboxd-metadata/

    Repeated payload field:
        likeables=viewing:{viewing_id}

    The response's ``liked`` list contains the requested viewing identifiers
    already liked by the signed-in user.

Notes:
    - This module is for reading and changing review/diary viewing like state.
    - It uses the numeric viewing id, e.g. 1358506798.
    - It does not use the API log-entry id, e.g. "ePmttr".
    - If given a review URL or object with .url, this module can fetch the page
      and extract the numeric viewing id from the DOM.
"""


from __future__ import annotations

import re
from dataclasses import dataclass
import math
from typing import Any, Iterable, Protocol
from urllib.parse import urljoin

from lboxdlib.auth import get_csrf
from lboxdlib.parsers.reviews import extract_reviews

LETTERBOXD_ORIGIN = "https://letterboxd.com"
VIEWING_ACTION_URL = f"{LETTERBOXD_ORIGIN}/s/viewing"
METADATA_ENDPOINT = f"{LETTERBOXD_ORIGIN}/ajax/letterboxd-metadata/"


RATE_LIMIT_ERROR_CODES = frozenset(
    {
        "error.rate.limit",
        "rate_limit",
        "rate.limit",
        "ratelimit",
    }
)
RATE_LIMIT_MESSAGE_MARKERS = (
    "too many recent review likes",
    "too many requests",
    "429 client error",
    "http 429",
    "status code: 429",
    "response code 429",
    "rate limit",
    "rate-limit",
    "rate limited",
    "ratelimited",
)


class RateLimitError(RuntimeError):
    """Letterboxd refused an action because its rate limit was reached."""


def is_rate_limit_error(exc: BaseException) -> bool:
    """Return whether *exc* represents a confirmed Letterboxd rate limit.

    Detection deliberately includes both HTTP 429 and Letterboxd's application
    response (``error.rate.limit``), because the latter commonly arrives with a
    non-error HTTP status and is exposed by ``like_state`` as ``RuntimeError``.
    """

    seen: set[int] = set()
    current: BaseException | None = exc

    while current is not None and id(current) not in seen:
        seen.add(id(current))

        if isinstance(current, RateLimitError):
            return True

        response = getattr(current, "response", None)
        status = getattr(response, "status_code", None)
        if status is None:
            status = getattr(current, "status_code", None)
        if status == 429:
            return True

        text = str(current).lower()
        if any(code in text for code in RATE_LIMIT_ERROR_CODES):
            return True
        if any(marker in text for marker in RATE_LIMIT_MESSAGE_MARKERS):
            return True

        current = current.__cause__ or current.__context__

    return False


def like_review(*args: Any, **kwargs: Any) -> Any:
    """Like a review, promoting a reported rate limit to ``RateLimitError``."""

    try:
        return _like_review(*args, **kwargs)
    except Exception as exc:
        if is_rate_limit_error(exc):
            raise RateLimitError(str(exc)) from exc
        raise




class HasViewingId(Protocol):
    viewing_id: int | None


class HasUrl(Protocol):
    url: str | None


LikeTarget = int | str | HasViewingId | HasUrl


@dataclass(slots=True)
class LikeStateResult:
    """
    Normalized result from setting a review/viewing like state.
    """

    result: bool
    liked: bool
    count: int | None
    likeable_type: str | None
    likeable_id: int | None
    boxd_it_code: str | None
    response: dict[str, Any]
    attempted: bool = True
    changed: bool | None = None
    reason: str | None = None
    rating: float | None = None


class ReviewLikeStateHydrationError(RuntimeError):
    """Raised when Letterboxd review liked-state metadata is invalid."""


def validate_review_rating_bounds(
    min_rating: float | None,
    max_rating: float | None,
) -> tuple[float | None, float | None]:
    """Validate inclusive Letterboxd review-rating bounds.

    Bounds must be half-star values from 0.5 through 5.0. ``None`` means the
    corresponding side is unrestricted.
    """

    def normalize(name: str, value: float | None) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool):
            raise ValueError(f"{name} must be None or a half-star value from 0.5 to 5.0")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{name} must be None or a half-star value from 0.5 to 5.0"
            ) from exc
        if (
            not math.isfinite(number)
            or number < 0.5
            or number > 5.0
            or not math.isclose(number * 2, round(number * 2), abs_tol=1e-9)
        ):
            raise ValueError(f"{name} must be None or a half-star value from 0.5 to 5.0")
        return number

    minimum = normalize("min_rating", min_rating)
    maximum = normalize("max_rating", max_rating)
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("min_rating cannot be greater than max_rating")
    return minimum, maximum


def review_rating_filter_reason(
    rating: float | None,
    *,
    min_rating: float | None,
    max_rating: float | None,
    rating_known: bool = True,
) -> str | None:
    """Return why a review fails an inclusive rating filter, or ``None``.

    When neither bound is active, unrated or unavailable ratings are allowed.
    """

    if min_rating is None and max_rating is None:
        return None
    if rating is None:
        return "unrated" if rating_known else "rating_unavailable"
    if min_rating is not None and rating < min_rating:
        return "rating_below_minimum"
    if max_rating is not None and rating > max_rating:
        return "rating_above_maximum"
    return None


def _chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def _normalize_viewing_uid(target: Any) -> str:
    """Normalize a review/viewing identifier to Letterboxd's ``viewing:<id>`` form."""

    if isinstance(target, int):
        return f"viewing:{target}"

    if isinstance(target, str):
        viewing_id = extract_viewing_id_from_text(target)
        if viewing_id is not None:
            return f"viewing:{viewing_id}"

    for attr in ("viewingUid", "viewing_uid"):
        value = getattr(target, attr, None)
        if value:
            viewing_id = extract_viewing_id_from_text(str(value))
            if viewing_id is not None:
                return f"viewing:{viewing_id}"

    for attr in ("viewingId", "viewing_id"):
        value = getattr(target, attr, None)
        if value is not None:
            return f"viewing:{int(value)}"

    raise ValueError(
        "Expected a numeric viewing id, viewing:<id> string, or object with "
        "viewingUid/viewingId attributes"
    )


def get_review_like_states(
    session: Any,
    targets: Iterable[Any],
    *,
    referer: str = f"{LETTERBOXD_ORIGIN}/activity/",
    batch_size: int = 100,
) -> dict[str, bool]:
    """Return the signed-in viewer's current like state for review viewings.

    Letterboxd's metadata endpoint returns only the requested ``viewing:<id>``
    values currently liked by the signed-in user. Every requested value absent
    from that list is therefore ``False``.

    The returned mapping always uses canonical ``viewing:<id>`` keys.
    """

    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")

    viewing_uids: list[str] = []
    seen: set[str] = set()
    for target in targets:
        uid = _normalize_viewing_uid(target)
        if uid not in seen:
            seen.add(uid)
            viewing_uids.append(uid)

    if not viewing_uids:
        return {}

    # Refresh/discover the CSRF cookie before the metadata request. The observed
    # metadata POST carries authentication in the session cookies and does not
    # require a __csrf form field.
    get_csrf(session)

    states = {uid: False for uid in viewing_uids}

    for batch in _chunks(viewing_uids, batch_size):
        response = session.post(
            METADATA_ENDPOINT,
            data=[("likeables", uid) for uid in batch],
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": LETTERBOXD_ORIGIN,
                "Referer": referer,
            },
        )
        response.raise_for_status()

        try:
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - response implementation varies
            text = getattr(response, "text", "")
            raise ReviewLikeStateHydrationError(
                f"Unable to decode Letterboxd metadata response: {text[:500]!r}"
            ) from exc

        if not isinstance(payload, dict) or payload.get("result") is not True:
            raise ReviewLikeStateHydrationError(
                f"Letterboxd metadata request failed: {payload!r}"
            )

        liked = payload.get("liked", [])
        if liked is None:
            liked = []
        if not isinstance(liked, list):
            raise ReviewLikeStateHydrationError(
                f"Unexpected metadata liked field: {liked!r}"
            )

        batch_set = set(batch)
        for raw_uid in liked:
            try:
                uid = _normalize_viewing_uid(raw_uid)
            except (TypeError, ValueError):
                continue
            if uid in batch_set:
                states[uid] = True

    return states


def _bool_form(value: bool) -> str:
    return "true" if value else "false"


def _form_headers(*, referer: str) -> dict[str, str]:
    return {
        "Origin": LETTERBOXD_ORIGIN,
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": referer,
    }


def extract_viewing_id_from_html(html: str) -> int:
    """
    Extract the numeric viewing id from a Letterboxd review/diary page DOM.

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


def extract_viewing_id_from_text(value: str) -> int | None:
    """
    Extract a viewing id from a string, if the string directly contains one.

    Accepts:
        "1358506798"
        "viewing:1358506798"
        "/viewing/1358506798/json/"
        "... viewing:1358506798 ..."
    """

    value = value.strip()

    if value.isdigit():
        return int(value)

    if value.startswith("viewing:"):
        maybe_id = value.split(":", 1)[1]
        if maybe_id.isdigit():
            return int(maybe_id)

    patterns = [
        r"viewing:(\d+)",
        r"/viewing/(\d+)/json/",
        r"sharing-toggle-body-viewing-(\d+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, value)
        if match:
            return int(match.group(1))

    return None


def get_viewing_id_from_review_url(session, review_url: str) -> int:
    """
    Fetch a Letterboxd review/diary URL and extract its numeric viewing id.
    """

    if not review_url:
        raise ValueError("review_url is required")

    response = session.get(
        urljoin(LETTERBOXD_ORIGIN, review_url),
        headers={
            "accept": "text/html,*/*",
            "referer": LETTERBOXD_ORIGIN + "/",
        },
    )
    response.raise_for_status()

    return extract_viewing_id_from_html(response.text)


def resolve_viewing_id(session, target: LikeTarget) -> int:
    """
    Resolve a like target into a numeric Letterboxd viewing id.

    Accepts:
        1358506798
        "1358506798"
        "viewing:1358506798"
        "https://letterboxd.com/user/film/slug/..."
        an object with .viewing_id
        an object with .url

    Rejects:
        "ePmttr"  # API log-entry id, not numeric viewing id.
    """

    if isinstance(target, int):
        return target

    if isinstance(target, str):
        direct = extract_viewing_id_from_text(target)
        if direct is not None:
            return direct

        if target.startswith("http://") or target.startswith("https://") or target.startswith("/"):
            return get_viewing_id_from_review_url(session, target)

        raise ValueError(
            f"Expected numeric viewing_id, review URL, or viewing:id string; got {target!r}. "
            "The API logEntry id is not the same as the numeric viewing id."
        )

    for attr in ("viewing_id", "viewingId"):
        viewing_id = getattr(target, attr, None)
        if viewing_id is not None:
            return int(viewing_id)

    url = None
    for attr in ("review_url", "reviewUrl", "url"):
        value = getattr(target, attr, None)
        if value:
            url = value
            break
    if url:
        resolved = get_viewing_id_from_review_url(session, str(url))

        # Cache back to mutable result objects when possible, but do not require it.
        try:
            if hasattr(target, "viewingId"):
                setattr(target, "viewingId", resolved)
            else:
                setattr(target, "viewing_id", resolved)
        except Exception:
            pass

        return resolved

    raise ValueError(
        "Could not resolve viewing_id from target. "
        "Pass a numeric viewing id, viewing:id string, review URL, or object with "
        ".viewing_id/.viewingId/.review_url/.reviewUrl/.url."
    )


def _target_review_url(target: LikeTarget) -> str | None:
    if isinstance(target, str) and (
        target.startswith("http://") or target.startswith("https://") or target.startswith("/")
    ):
        return urljoin(LETTERBOXD_ORIGIN, target)

    if not isinstance(target, (str, int)):
        for attr in ("review_url", "reviewUrl", "url"):
            value = getattr(target, attr, None)
            if value:
                return urljoin(LETTERBOXD_ORIGIN, str(value))
    return None


def _fetch_review_details(session: Any, review_url: str) -> tuple[int, float | None, bool]:
    """Fetch a review once and return viewing id, rating, and rating-known flag."""

    response = session.get(
        urljoin(LETTERBOXD_ORIGIN, review_url),
        headers={
            "accept": "text/html,*/*",
            "referer": LETTERBOXD_ORIGIN + "/",
        },
    )
    response.raise_for_status()
    viewing_id = extract_viewing_id_from_html(response.text)

    reviews = extract_reviews(response.text)
    if not reviews:
        return viewing_id, None, False
    return viewing_id, getattr(reviews[0], "rating", None), True


def _resolve_target_rating(
    session: Any,
    target: LikeTarget,
) -> tuple[float | None, bool, LikeTarget, int | None]:
    """Resolve a target rating and, when fetched, reuse its numeric viewing id."""

    if not isinstance(target, (str, int)) and hasattr(target, "rating"):
        value = getattr(target, "rating")
        rating = float(value) if value is not None else None
        viewing_id = None
        for attr in ("viewing_id", "viewingId"):
            raw_id = getattr(target, attr, None)
            if raw_id is not None:
                viewing_id = int(raw_id)
                break
        return rating, True, target, viewing_id

    review_url = _target_review_url(target)
    if review_url:
        viewing_id, rating, rating_known = _fetch_review_details(session, review_url)
        if not isinstance(target, (str, int)):
            try:
                if hasattr(target, "viewingId"):
                    setattr(target, "viewingId", viewing_id)
                else:
                    setattr(target, "viewing_id", viewing_id)
                setattr(target, "rating", rating)
            except Exception:
                pass
        return rating, rating_known, viewing_id, viewing_id

    direct_id: int | None = None
    try:
        direct_id = resolve_viewing_id(session, target)
    except (TypeError, ValueError):
        pass
    return None, False, target, direct_id


def _skipped_like_result(
    *,
    reason: str,
    rating: float | None,
    viewing_id: int | None,
) -> LikeStateResult:
    return LikeStateResult(
        result=False,
        liked=False,
        count=None,
        likeable_type="viewing" if viewing_id is not None else None,
        likeable_id=viewing_id,
        boxd_it_code=None,
        response={},
        attempted=False,
        changed=False,
        reason=reason,
        rating=rating,
    )


def parse_like_state_response(data: dict[str, Any], *, expected_liked: bool | None = None) -> LikeStateResult:
    """
    Normalize and validate the Letterboxd like response.
    """

    if not data.get("result"):
        raise RuntimeError(f"Letterboxd like request failed: {data!r}")

    actual_liked = data.get("liked")

    if expected_liked is not None and actual_liked is not expected_liked:
        raise RuntimeError(
            f"Letterboxd returned liked={actual_liked!r}, expected {expected_liked!r}. "
            f"Full response: {data!r}"
        )

    likeable = data.get("likeable") or {}

    return LikeStateResult(
        result=bool(data.get("result")),
        liked=bool(actual_liked),
        count=data.get("count"),
        likeable_type=likeable.get("typeName"),
        likeable_id=likeable.get("id"),
        boxd_it_code=likeable.get("boxdItCode"),
        response=data,
    )


def set_review_like_state(
    session,
    target: LikeTarget,
    liked: bool,
    *,
    referer: str = "https://letterboxd.com/activity/",
) -> LikeStateResult:
    """
    Set the like state for a Letterboxd review/diary viewing.

    This is a desired-state setter, not a blind toggle.

    Examples:
        set_review_like_state(session, 1358506798, True)
        set_review_like_state(session, "viewing:1358506798", False)
        set_review_like_state(session, review_url, True)
        set_review_like_state(session, diary_entry_result, False)

    Proven route:
        POST /s/viewing:{viewing_id}/like/

    Proven payload:
        liked=true&__csrf={csrf}
        liked=false&__csrf={csrf}
    """

    viewing_id = resolve_viewing_id(session, target)
    csrf = get_csrf(session)

    response = session.post(
        f"{VIEWING_ACTION_URL}:{viewing_id}/like/",
        headers=_form_headers(referer=referer),
        data={
            "liked": _bool_form(liked),
            "__csrf": csrf,
        },
    )
    response.raise_for_status()

    return parse_like_state_response(response.json(), expected_liked=liked)


def _like_review(
    session,
    target: LikeTarget,
    *,
    referer: str = "https://letterboxd.com/activity/",
    min_rating: float | None = None,
    max_rating: float | None = None,
) -> LikeStateResult:
    """Like a Letterboxd review/diary viewing when its rating qualifies.

    Rating bounds are inclusive. When either bound is supplied, unrated reviews
    and targets whose rating cannot be established are skipped without sending a
    like request. The returned result then has ``attempted=False`` and a reason
    such as ``unrated``, ``rating_below_minimum``, ``rating_above_maximum``, or
    ``rating_unavailable``.
    """

    minimum, maximum = validate_review_rating_bounds(min_rating, max_rating)
    resolved_target: LikeTarget = target
    rating: float | None = None

    if minimum is not None or maximum is not None:
        rating, rating_known, resolved_target, viewing_id = _resolve_target_rating(
            session,
            target,
        )
        reason = review_rating_filter_reason(
            rating,
            min_rating=minimum,
            max_rating=maximum,
            rating_known=rating_known,
        )
        if reason is not None:
            return _skipped_like_result(
                reason=reason,
                rating=rating,
                viewing_id=viewing_id,
            )

    result = set_review_like_state(session, resolved_target, True, referer=referer)
    result.rating = rating
    return result


def unlike_review(
    session,
    target: LikeTarget,
    *,
    referer: str = "https://letterboxd.com/activity/",
) -> LikeStateResult:
    """
    Unlike a Letterboxd review/diary viewing.
    """

    return set_review_like_state(session, target, False, referer=referer)


# Backward-compatible aliases if the project prefers shorter names.
set_like_state = set_review_like_state
like = like_review
unlike = unlike_review
