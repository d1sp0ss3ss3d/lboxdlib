"""Pronoun normalization and conservative Letterboxd activity inference."""

from __future__ import annotations

import re
from collections.abc import Iterable

_CANONICAL_PRONOUNS = {
    "he/him": "he/him",
    "he/him/his": "he/him",
    "she/her": "she/her",
    "she/her/hers": "she/her",
    "they/them": "they/them",
    "they/them/theirs": "they/them",
}

_ACTIVITY_POSSESSIVE_TO_PRONOUNS = {
    "his": "he/him",
    "her": "she/her",
    "their": "they/them",
}

_SUBJECT_ACTIVITY_PATTERNS = (
    re.compile(r"\bto\s+(his|her|their)\s+watchlist\b", flags=re.I),
    re.compile(r"\bcommented\s+on\s+(his|her|their)\s+own\s+review\b", flags=re.I),
)


def normalize_pronoun_set(value: str | None) -> str | None:
    """Return a stable representation for one pronoun set."""

    if not value:
        return None
    compact = re.sub(r"\s+", "", str(value).casefold())
    return _CANONICAL_PRONOUNS.get(compact, compact or None)


def pronoun_sets(value: str | None) -> set[str]:
    """Split a serialized pronoun value into normalized pronoun sets."""

    if not value:
        return set()
    return {
        normalized
        for item in re.split(r"[,;|]", str(value))
        if (normalized := normalize_pronoun_set(item)) is not None
    }


def merge_pronouns(*values: str | None) -> str | None:
    """Combine pronoun values without duplicating sets, preserving order."""

    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        for item in re.split(r"[,;|]", str(value)):
            normalized = normalize_pronoun_set(item)
            if normalized is None or normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(normalized)
    return ", ".join(ordered) or None


def merge_pronoun_values(values: Iterable[str | None]) -> str | None:
    """Iterable form of :func:`merge_pronouns`."""

    return merge_pronouns(*values)


def uses_pronouns(value: str | None, requested: str) -> bool:
    """Return whether ``value`` includes the requested pronoun set."""

    normalized = normalize_pronoun_set(requested)
    return normalized is not None and normalized in pronoun_sets(value)


def infer_pronouns_from_activity_text(text: str | None) -> str | None:
    """Infer pronouns only from Letterboxd phrases tied to the activity actor.

    General review and comment prose is intentionally ignored. The recognized
    templates are ``to her/his/their watchlist`` and
    ``commented on her/his/their own review``.
    """

    if not text:
        return None

    matches: list[str] = []
    for pattern in _SUBJECT_ACTIVITY_PATTERNS:
        matches.extend(
            _ACTIVITY_POSSESSIVE_TO_PRONOUNS[match.group(1).casefold()]
            for match in pattern.finditer(str(text))
        )
    return merge_pronoun_values(matches)


__all__ = [
    "infer_pronouns_from_activity_text",
    "merge_pronoun_values",
    "merge_pronouns",
    "normalize_pronoun_set",
    "pronoun_sets",
    "uses_pronouns",
]
