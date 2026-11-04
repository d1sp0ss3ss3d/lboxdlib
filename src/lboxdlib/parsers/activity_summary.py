"""Serializable summary records for Letterboxd activity feeds.

``ActivitySummary.from_activities`` accepts either the activity dataclasses used
by the activity parser or dictionaries produced from them with
``dataclasses.asdict`` / ``json.loads``.

Example::

    import json
    from dataclasses import asdict

    summary = ActivitySummary.from_activities(activities)
    print(json.dumps(asdict(summary), ensure_ascii=False, indent=4))
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from lboxdlib.pronouns import infer_pronouns_from_activity_text, merge_pronouns


@dataclass(slots=True)
class ReferencedUser:
    slug: str
    displayName: str | None
    gender: str | None = None
    pronouns: str | None = None


@dataclass(slots=True)
class AssignedRating:
    title: str | None
    slug: str | None
    rating: float


@dataclass(slots=True)
class ReferencedFilm:
    title: str | None
    slug: str | None


@dataclass(slots=True)
class ActivitySummary:
    """A JSON-safe report derived from one subject user's activity feed."""

    subjectSlug: str | None
    subjectDisplayName: str | None
    likedReviewsCount: int
    likedListsCount: int
    followedUsersCount: int
    referencedUsers: list[ReferencedUser]
    commentTexts: list[str]
    reviewTexts: list[str]
    assignedRatings: list[AssignedRating]
    otherFilms: list[ReferencedFilm]
    subjectPronouns: str | None = None
    subjectGender: str | None = None

    @classmethod
    def from_activities(
        cls,
        activities: Iterable[Any],
        *,
        subject_slug: str | None = None,
    ) -> ActivitySummary:
        """Build a summary from activity objects or activity dictionaries.

        ``subject_slug`` is optional for a normal single-user activity feed. If
        omitted, the subject is inferred from the most frequent ``actor.slug``;
        feeds without actors fall back to the most frequent ``ownerSlug``.

        Duplicate non-null activity IDs are counted only once. Text and rating
        lists preserve feed order. Referenced users and other films are unique
        by slug and preserve their first appearance.
        """

        rows = _dedupe_activities(activities)
        inferred_subject = _normalize_slug(subject_slug) or _infer_subject_slug(rows)
        subject_key = _slug_key(inferred_subject)

        users: dict[str, ReferencedUser] = {}
        user_order: list[str] = []

        def remember_user(
            slug: Any,
            display_name: Any = None,
            *,
            gender: Any = None,
            pronouns: Any = None,
        ) -> None:
            clean_slug = _normalize_slug(slug)
            if not clean_slug:
                return
            key = _slug_key(clean_slug)
            clean_display = _clean_string(display_name)
            clean_gender = _clean_string(gender)
            clean_pronouns = _clean_string(pronouns)
            existing = users.get(key)
            if existing is None:
                users[key] = ReferencedUser(
                    clean_slug,
                    clean_display,
                    gender=clean_gender,
                    pronouns=clean_pronouns,
                )
                user_order.append(key)
            else:
                if existing.displayName is None and clean_display is not None:
                    existing.displayName = clean_display
                if existing.gender is None and clean_gender is not None:
                    existing.gender = clean_gender
                existing.pronouns = merge_pronouns(existing.pronouns, clean_pronouns)

        # Collect all structured identities first so a display name found in one
        # activity can fill a slug-only reference found in another.
        for row in rows:
            actor = _get(row, "actor")
            actor_pronouns = _clean_string(_get(actor, "pronouns"))
            if actor_pronouns is None:
                actor_pronouns = infer_pronouns_from_activity_text(_get(row, "rawText"))
            remember_user(
                _get(actor, "slug"),
                _get(actor, "displayName"),
                gender=_get(actor, "gender"),
                pronouns=actor_pronouns,
            )
            remember_user(_get(row, "ownerSlug"), _get(row, "ownerDisplayName"))

            review_display = _get(row, "reviewOwnerDisplayName")
            if review_display is None:
                review_display = _review_owner_display_from_raw(row)
            remember_user(_get(row, "reviewOwnerSlug"), review_display)

            list_display = _list_owner_display_from_raw(row)
            remember_user(_get(row, "listOwnerSlug"), list_display)
            remember_user(_get(row, "targetSlug"), _get(row, "targetDisplayName"))

            basic_user = _basic_comment_user(row)
            if basic_user is not None:
                remember_user(*basic_user)

        subject = users.get(subject_key) if subject_key else None
        if subject is None and inferred_subject:
            subject = ReferencedUser(inferred_subject, None)

        liked_reviews = 0
        liked_lists = 0
        followed_users = 0
        comments: list[str] = []
        reviews: list[str] = []
        ratings: list[AssignedRating] = []
        other_films: list[ReferencedFilm] = []
        seen_other_films: set[str] = set()

        for row in rows:
            action = _clean_string(_get(row, "action"))
            if action == "liked_review_reference":
                liked_reviews += 1
            elif action == "liked_list":
                liked_lists += 1
            elif action == "followed_member":
                followed_users += 1

            comment_text = _clean_string(_get(row, "commentText"))
            if comment_text is None:
                comment_text = _basic_comment_text(row)
            if comment_text is not None:
                comments.append(comment_text)

            review_text = _clean_string(_get(row, "reviewText"))
            if review_text is not None:
                reviews.append(review_text)

            assigned_rating = _assigned_rating(row)
            if assigned_rating is not None:
                ratings.append(assigned_rating)
                continue

            film = _get(row, "film")
            film_record = _referenced_film(film)
            if film_record is None:
                film_record = _basic_film_reference(row)
            if film_record is not None:
                film_key = _film_key(film_record)
                if film_key not in seen_other_films:
                    seen_other_films.add(film_key)
                    other_films.append(film_record)

        referenced_users = [
            users[key]
            for key in user_order
            if subject_key is None or key != subject_key
        ]

        return cls(
            subjectSlug=subject.slug if subject else inferred_subject,
            subjectDisplayName=subject.displayName if subject else None,
            likedReviewsCount=liked_reviews,
            likedListsCount=liked_lists,
            followedUsersCount=followed_users,
            referencedUsers=referenced_users,
            commentTexts=comments,
            reviewTexts=reviews,
            assignedRatings=ratings,
            otherFilms=other_films,
            subjectPronouns=subject.pronouns if subject else None,
            subjectGender=subject.gender if subject else None,
        )

    def format_for_human(self) -> str:
        """Return a readable plain-text rendering of the summary."""

        lines = [
            f"Activity summary for {self.subjectDisplayName or self.subjectSlug or 'unknown user'}",
            f"Slug: {self.subjectSlug or 'unknown'}",
            f"Pronouns used in activity text: {self.subjectPronouns or 'not established'}",
        ]
        if self.subjectGender:
            lines.append(f"Gender: {self.subjectGender}")
        lines.extend(
            [
                f"Liked reviews: {self.likedReviewsCount}",
                f"Liked lists: {self.likedListsCount}",
                f"Followed users: {self.followedUsersCount}",
                "",
                "Referenced users:",
            ]
        )

        if self.referencedUsers:
            for user in self.referencedUsers:
                details = user.displayName or "unknown display name"
                if user.pronouns:
                    details += f"; {user.pronouns}"
                lines.append(f"- {details} ({user.slug})")
        else:
            lines.append("- None")

        _append_numbered_section(lines, "Comments", self.commentTexts)
        _append_numbered_section(lines, "Reviews", self.reviewTexts)

        lines.extend(["", "Assigned ratings:"])
        if self.assignedRatings:
            for rating in self.assignedRatings:
                title = rating.title or "unknown title"
                slug = f" ({rating.slug})" if rating.slug else ""
                lines.append(f"- {title}{slug}: {rating.rating:g}")
        else:
            lines.append("- None")

        lines.extend(["", "Other films:"])
        if self.otherFilms:
            for film in self.otherFilms:
                title = film.title or "unknown title"
                slug = f" ({film.slug})" if film.slug else ""
                lines.append(f"- {title}{slug}")
        else:
            lines.append("- None")

        return "\n".join(lines)

    def __str__(self) -> str:
        return self.format_for_human()


def _append_numbered_section(lines: list[str], title: str, values: list[str]) -> None:
    lines.extend(["", f"{title}:"])
    if not values:
        lines.append("- None")
        return
    for index, value in enumerate(values, start=1):
        rendered = str(value).replace("\n", "\n    ")
        lines.append(f"{index}. {rendered}")


def _get(value: Any, field_name: str, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, Mapping):
        return value.get(field_name, default)
    return getattr(value, field_name, default)


def _kind(row: Any) -> str | None:
    value = _get(row, "kind")
    enum_value = getattr(value, "value", value)
    return _clean_string(enum_value)


def _clean_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_slug(value: Any) -> str | None:
    text = _clean_string(value)
    if text is None:
        return None
    return text.strip("/") or None


def _slug_key(value: Any) -> str:
    slug = _normalize_slug(value)
    return slug.casefold() if slug else ""


def _dedupe_activities(activities: Iterable[Any]) -> list[Any]:
    rows: list[Any] = []
    seen_ids: set[str] = set()
    for row in activities:
        activity_id = _clean_string(_get(row, "activityId"))
        if activity_id is not None:
            if activity_id in seen_ids:
                continue
            seen_ids.add(activity_id)
        rows.append(row)
    return rows


def _infer_subject_slug(rows: list[Any]) -> str | None:
    actor_counts: Counter[str] = Counter()
    actor_slugs: dict[str, str] = {}
    owner_counts: Counter[str] = Counter()
    owner_slugs: dict[str, str] = {}

    for row in rows:
        actor_slug = _normalize_slug(_get(_get(row, "actor"), "slug"))
        if actor_slug:
            key = _slug_key(actor_slug)
            actor_counts[key] += 1
            actor_slugs.setdefault(key, actor_slug)

        owner_slug = _normalize_slug(_get(row, "ownerSlug"))
        if owner_slug:
            key = _slug_key(owner_slug)
            owner_counts[key] += 1
            owner_slugs.setdefault(key, owner_slug)

    if actor_counts:
        key = actor_counts.most_common(1)[0][0]
        return actor_slugs[key]
    if owner_counts:
        key = owner_counts.most_common(1)[0][0]
        return owner_slugs[key]
    return None


def _actor_display(row: Any) -> str | None:
    return _clean_string(_get(_get(row, "actor"), "displayName"))


def _raw_without_actor(row: Any) -> str | None:
    raw = _clean_string(_get(row, "rawText"))
    if raw is None:
        return None
    actor_display = _actor_display(row)
    if actor_display and raw.startswith(actor_display):
        return raw[len(actor_display) :].lstrip()
    return raw


def _review_owner_display_from_raw(row: Any) -> str | None:
    if _kind(row) != "review_reference":
        return None
    raw = _raw_without_actor(row)
    if raw is None:
        return None
    match = re.match(
        r"(?:liked|commented on)\s+(?P<name>.+?)[’']s\s+(?:[★½]+\s+)?review of\s+",
        raw,
    )
    return _clean_string(match.group("name")) if match else None


def _list_owner_display_from_raw(row: Any) -> str | None:
    if _kind(row) != "list":
        return None
    raw = _raw_without_actor(row)
    list_name = _clean_string(_get(row, "listName"))
    if raw is None or list_name is None:
        return None
    match = re.match(
        rf"liked\s+(?P<name>.+?)[’']s\s+{re.escape(list_name)}\s+list(?:\s+on\s+\d{{4}}-\d{{2}}-\d{{2}})?$",
        raw,
    )
    return _clean_string(match.group("name")) if match else None


def _path_parts(url: Any) -> list[str]:
    text = _clean_string(url)
    if text is None:
        return []
    return [part for part in urlparse(text).path.split("/") if part]


def _basic_comment_user(row: Any) -> tuple[str, str | None] | None:
    if _kind(row) != "basic":
        return None
    target_url = _clean_string(_get(row, "targetUrl"))
    target_text = _clean_string(_get(row, "targetText"))
    if target_url is None or "#comment-" not in target_url or target_text is None:
        return None
    parts = _path_parts(target_url)
    if not parts:
        return None
    match = re.match(r"commented on\s+(?P<name>.+?)[’']s\s+", target_text)
    display_name = _clean_string(match.group("name")) if match else None
    return parts[0], display_name


def _basic_comment_text(row: Any) -> str | None:
    if _kind(row) != "basic":
        return None
    target_url = _clean_string(_get(row, "targetUrl"))
    target_text = _clean_string(_get(row, "targetText"))
    raw = _raw_without_actor(row)
    if (
        target_url is None
        or "#comment-" not in target_url
        or target_text is None
        or raw is None
    ):
        return None

    if raw.startswith(target_text):
        raw = raw[len(target_text) :].lstrip()
    raw = re.sub(r"\s+on\s+\d{4}-\d{2}-\d{2}\s*$", "", raw).strip()
    return raw or None


def _film_title(film: Any) -> str | None:
    title = _clean_string(_get(film, "name"))
    year = _get(film, "year")
    if title and year is not None:
        suffix = f" ({year})"
        if title.endswith(suffix):
            title = title[: -len(suffix)].rstrip()
    return title


def _rating_from_stars(stars: str) -> float:
    return float(stars.count("★")) + (0.5 if "½" in stars else 0.0)


def _basic_assigned_rating(row: Any) -> AssignedRating | None:
    if _kind(row) != "basic":
        return None
    parts = _path_parts(_get(row, "targetUrl"))
    title = _clean_string(_get(row, "targetText"))
    raw = _clean_string(_get(row, "rawText"))
    if len(parts) < 2 or parts[0] != "film" or title is None or raw is None:
        return None
    match = re.search(
        r"(?P<stars>★{1,5}½?|½)\s+on\s+\d{4}-\d{2}-\d{2}\s*$",
        raw,
    )
    if match is None or " rated " not in raw:
        return None
    return AssignedRating(title=title, slug=parts[1], rating=_rating_from_stars(match.group("stars")))


def _assigned_rating(row: Any) -> AssignedRating | None:
    kind = _kind(row)
    if kind == "basic":
        return _basic_assigned_rating(row)
    if kind not in {"review", "diary"}:
        return None
    rating = _get(row, "rating")
    film = _get(row, "film")
    if rating is None or film is None:
        return None
    return AssignedRating(
        title=_film_title(film),
        slug=_normalize_slug(_get(film, "slug")),
        rating=float(rating),
    )


def _referenced_film(film: Any) -> ReferencedFilm | None:
    if film is None:
        return None
    title = _film_title(film)
    slug = _normalize_slug(_get(film, "slug"))
    if title is None and slug is None:
        return None
    return ReferencedFilm(title=title, slug=slug)


def _basic_film_reference(row: Any) -> ReferencedFilm | None:
    if _kind(row) != "basic":
        return None
    parts = _path_parts(_get(row, "targetUrl"))
    if len(parts) < 2 or parts[0] != "film":
        return None
    return ReferencedFilm(
        title=_clean_string(_get(row, "targetText")),
        slug=parts[1],
    )


def _film_key(film: ReferencedFilm) -> str:
    if film.slug:
        return f"slug:{film.slug.casefold()}"
    return f"title:{(film.title or '').casefold()}"


__all__ = [
    "ActivitySummary",
    "AssignedRating",
    "ReferencedFilm",
    "ReferencedUser",
]
