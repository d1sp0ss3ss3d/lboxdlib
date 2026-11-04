"""Models for Letterboxd review comments and comment-list responses."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Comment:
    """A Letterboxd review-comment record parsed from comment-list HTML."""

    commentId: int | None = None
    username: str | None = None
    displayName: str | None = None
    avatarUrl: str | None = None

    creationTimestamp: int | None = None
    timestamp: int | None = None
    createdAt: str | None = None

    permalink: str | None = None
    commentText: str | None = None
    commentHtml: str | None = None

    edited: bool = False
    deleted: bool = False
    deletedBy: str | None = None

    editUrl: str | None = None
    deleteUrl: str | None = None
    undeleteUrl: str | None = None
    fullTextUrl: str | None = None
    editWindowExpiry: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Comment":
        return cls(**data)


@dataclass
class CommentCollection:
    """Collection metadata plus comment records from a snapshot or delta.

    ``numComments`` is Letterboxd's active-comment count. ``comments`` may also
    contain deleted tombstones, so the two values are intentionally independent.
    ``timestamp`` is present on the observed full comments-section snapshot but
    not on observed delta fragments.
    """

    commentableType: str | None = None
    owner: str | None = None
    numComments: int | None = None
    timestamp: int | None = None
    comments: list[Comment] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "commentableType": self.commentableType,
            "owner": self.owner,
            "numComments": self.numComments,
            "timestamp": self.timestamp,
            "comments": [comment.to_dict() for comment in self.comments],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CommentCollection":
        return cls(
            commentableType=data.get("commentableType"),
            owner=data.get("owner"),
            numComments=data.get("numComments"),
            timestamp=data.get("timestamp"),
            comments=[
                Comment.from_dict(comment)
                for comment in data.get("comments", [])
            ],
        )


__all__ = ["Comment", "CommentCollection"]
