"""Parsers for Letterboxd review-comment HTML fragments.

Letterboxd exposes the same ``<ul class=\"comment-list\">`` shape from two
observed endpoints:

* the CSI comments-section response, which is a full thread snapshot;
* ``/ajax/viewing:{id}/comments/?since=...``, which is a delta containing only
  comments whose state has changed since the supplied timestamp.

Deleted comments remain present as tombstone ``<li>`` records, while the
collection's ``data-num-comments`` value counts active comments.
"""

from __future__ import annotations

from bs4 import BeautifulSoup, Tag

from lboxdlib.models.comments import Comment, CommentCollection


def extract_comments(html: str) -> CommentCollection:
    """Parse a Letterboxd comment-list snapshot or delta fragment."""
    soup = BeautifulSoup(html, "lxml")
    comment_list = soup.select_one("ul.comment-list")

    if comment_list is None:
        return CommentCollection()

    comments = [
        _comment_from_node(node)
        for node in comment_list.select(":scope > li.comment")
    ]

    return CommentCollection(
        commentableType=_attr(comment_list, "data-commentable-type"),
        owner=_attr(comment_list, "data-owner"),
        numComments=_int_attr(comment_list, "data-num-comments"),
        timestamp=_int_attr(comment_list, "data-timestamp"),
        comments=comments,
    )


def _comment_from_node(node: Tag) -> Comment:
    person_link = node.select_one(".person-summary strong.name a[href]")
    avatar = node.select_one(".person-summary img[src]")
    time = node.select_one(".comment-permalink time[datetime]")
    permalink = node.select_one("a.comment-permalink[href]")
    body = node.select_one(".comment-body")

    username = _attr(node, "data-person") or _username_from_href(
        _attr(person_link, "href")
    )

    return Comment(
        commentId=_int_attr(node, "data-comment-id"),
        username=username,
        displayName=_clean_text(person_link),
        avatarUrl=_attr(avatar, "src"),
        creationTimestamp=_int_attr(node, "data-creation-timestamp"),
        timestamp=_int_attr(node, "data-timestamp"),
        createdAt=_attr(time, "datetime"),
        permalink=_attr(permalink, "href"),
        commentText=_clean_text(body),
        commentHtml=_inner_html(body),
        edited="edited" in (node.get("class") or []),
        deleted="deleted" in (node.get("class") or []),
        deletedBy=_attr(node, "data-deleted-by"),
        editUrl=_attr(node, "data-edit-url"),
        deleteUrl=_attr(node, "data-delete-url"),
        undeleteUrl=_attr(node, "data-undelete-url"),
        fullTextUrl=_attr(body, "data-full-text-url"),
        editWindowExpiry=_int_attr(node, "data-edit-window-expiry"),
    )


def _attr(node: Tag | None, name: str) -> str | None:
    if node is None:
        return None
    value = node.get(name)
    return str(value) if value is not None else None


def _int_attr(node: Tag | None, name: str) -> int | None:
    value = _attr(node, name)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_text(node: Tag | None) -> str | None:
    if node is None:
        return None
    text = node.get_text(" ", strip=True)
    return text or None


def _inner_html(node: Tag | None) -> str | None:
    if node is None:
        return None
    return "".join(str(child) for child in node.contents).strip() or None


def _username_from_href(href: str | None) -> str | None:
    if not href:
        return None
    parts = [part for part in href.split("/") if part]
    return parts[0] if parts else None


__all__ = ["extract_comments"]
