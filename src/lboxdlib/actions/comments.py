"""Authenticated Letterboxd review-comment mutations.

The request shapes in this module are based on observed Letterboxd web traffic.
All functions require a caller-supplied authenticated session. They never create
or replace a session, and they deliberately avoid supplying browser headers that
curl_cffi can manage itself.

Observed endpoints:

POST COMMENT
    POST /s/viewing:{viewing_id}/add-comment
    form: __csrf, comment

EDIT COMMENT
    POST /ajax/viewingComment:{comment_id}/update-comment/
    form: __csrf, comment

DELETE COMMENT
    POST /ajax/viewingComment:{comment_id}/delete-comment/
    form: __csrf

The server returns JSON containing at least ``result``, ``csrf``, ``messages``,
``errorCodes``, and ``errorFields`` for the captured successful mutations.
Posting may additionally return ``audienceMemberState``. These dictionaries are
returned unchanged so callers retain the complete Letterboxd response.
"""

from __future__ import annotations

from typing import Any

from lboxdlib.auth import get_csrf


LETTERBOXD_ORIGIN = "https://letterboxd.com"


def post_comment(
    session: Any,
    viewing_id: int | str,
    comment: str,
) -> dict[str, Any]:
    """Post a comment to a Letterboxd review/viewing.

    Args:
        session:
            Caller-supplied authenticated curl_cffi Session or compatible
            LBoxdSession wrapper.
        viewing_id:
            Numeric Letterboxd viewing id used by the review comment endpoint.
        comment:
            Comment text to submit.

    Returns:
        Letterboxd's decoded JSON response unchanged.
    """
    url = f"{LETTERBOXD_ORIGIN}/s/viewing:{viewing_id}/add-comment"
    payload = {
        "__csrf": get_csrf(session),
        "comment": comment,
    }

    response = session.post(url, data=payload)
    response.raise_for_status()
    return response.json()


def edit_comment(
    session: Any,
    comment_id: int | str,
    comment: str,
) -> dict[str, Any]:
    """Replace the text of an existing Letterboxd review comment.

    Letterboxd's observed browser request uses POST rather than PATCH for this
    mutation.

    Args:
        session:
            Caller-supplied authenticated curl_cffi Session or compatible
            LBoxdSession wrapper.
        comment_id:
            Numeric id from the ``viewingComment:{comment_id}`` identifier.
        comment:
            Replacement comment text.

    Returns:
        Letterboxd's decoded JSON response unchanged.
    """
    url = (
        f"{LETTERBOXD_ORIGIN}/ajax/viewingComment:{comment_id}/update-comment/"
    )
    payload = {
        "__csrf": get_csrf(session),
        "comment": comment,
    }

    response = session.post(url, data=payload)
    response.raise_for_status()
    return response.json()


def delete_comment(
    session: Any,
    comment_id: int | str,
) -> dict[str, Any]:
    """Delete an existing Letterboxd review comment.

    Args:
        session:
            Caller-supplied authenticated curl_cffi Session or compatible
            LBoxdSession wrapper.
        comment_id:
            Numeric id from the ``viewingComment:{comment_id}`` identifier.

    Returns:
        Letterboxd's decoded JSON response unchanged.
    """
    url = (
        f"{LETTERBOXD_ORIGIN}/ajax/viewingComment:{comment_id}/delete-comment/"
    )
    payload = {
        "__csrf": get_csrf(session),
    }

    response = session.post(url, data=payload)
    response.raise_for_status()
    return response.json()


__all__ = [
    "post_comment",
    "edit_comment",
    "delete_comment",
]
