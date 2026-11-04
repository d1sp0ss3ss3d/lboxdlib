"""lboxdlib public API.

Common operations are available directly from the package root while their
implementations remain in domain-specific modules. Root exports are lazy so
``import lboxdlib`` stays lightweight.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__version__ = "1.0.6"

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    # Authentication/session
    "LBoxdSession": ("lboxdlib.auth", "LBoxdSession"),
    "LboxdSession": ("lboxdlib.auth", "LboxdSession"),
    "new_session": ("lboxdlib.auth", "new_session"),
    "lb_session": ("lboxdlib.auth", "lb_session"),
    "ensure_session": ("lboxdlib.auth", "ensure_session"),
    "ensure_raw_session": ("lboxdlib.auth", "ensure_raw_session"),
    "get_signed_in_user": ("lboxdlib.auth", "get_signed_in_user"),
    "get_csrf": ("lboxdlib.auth", "get_csrf"),

    # Common fetch operations
    "fetch_films": ("lboxdlib.fetchers.films", "fetch_films"),
    "fetch_lids": ("lboxdlib.fetchers.films", "fetch_lids"),
    "fetch_reviews": ("lboxdlib.fetchers.reviews", "fetch_reviews"),
    "fetch_users": ("lboxdlib.fetchers.users", "fetch_users"),
    "fetch_members": ("lboxdlib.fetchers.users", "fetch_members"),
    "fetch_following_users": ("lboxdlib.fetchers.users", "fetch_following_users"),
    "fetch_follower_users": ("lboxdlib.fetchers.users", "fetch_follower_users"),
    "determine_pronouns_for_users": (
        "lboxdlib.fetchers.users",
        "determine_pronouns_for_users",
    ),
    "fetch_activities": ("lboxdlib.fetchers.activity", "fetch_activities"),
    "fetch_activity": ("lboxdlib.fetchers.activity", "fetch_activity"),
    "fetch_review_like_events": ("lboxdlib.fetchers.activity", "fetch_review_like_events"),
    "fetch_review_likers": ("lboxdlib.fetchers.activity", "fetch_review_likers"),
    "fetch_review_liker_names": ("lboxdlib.fetchers.activity", "fetch_review_liker_names"),
    "fetch_comments_section": ("lboxdlib.fetchers.comments", "fetch_comments_section"),
    "fetch_comment_updates": ("lboxdlib.fetchers.comments", "fetch_comment_updates"),

    # Comment actions
    "post_comment": ("lboxdlib.actions.comments", "post_comment"),
    "edit_comment": ("lboxdlib.actions.comments", "edit_comment"),
    "delete_comment": ("lboxdlib.actions.comments", "delete_comment"),

    # Common write actions
    "like_review": ("lboxdlib.actions.likes", "like_review"),
    "unlike_review": ("lboxdlib.actions.likes", "unlike_review"),
    "get_review_like_states": ("lboxdlib.actions.likes", "get_review_like_states"),
    "follow_user": ("lboxdlib.actions.follows", "follow_user"),
    "unfollow_user": ("lboxdlib.actions.follows", "unfollow_user"),
    "follow_users": ("lboxdlib.actions.follows", "follow_users"),
    "batch_follow_users": ("lboxdlib.actions.follows", "batch_follow_users"),
    "unfollow_users": ("lboxdlib.actions.follows", "unfollow_users"),
    "batch_unfollow_users": ("lboxdlib.actions.follows", "batch_unfollow_users"),
    "rate_by_lid": ("lboxdlib.actions.ratings", "rate_by_lid"),
    "save_diary_entry": ("lboxdlib.actions.diary", "save_diary_entry"),
    "update_diary_entry": ("lboxdlib.actions.diary", "update_diary_entry"),
    "delete_diary_entry": ("lboxdlib.actions.diary", "delete_diary_entry"),
    "add_to_watchlist": ("lboxdlib.actions.watch", "add_to_watchlist"),
    "remove_from_watchlist": ("lboxdlib.actions.watch", "remove_from_watchlist"),
    "set_watched": ("lboxdlib.actions.watch", "set_watched"),
    "get_watched_status": ("lboxdlib.actions.watch", "get_watched_status"),
    "get_watchlist_status": ("lboxdlib.actions.watch", "get_watchlist_status"),

    # Lists / hydration
    "ListManager": ("lboxdlib.lists.manager", "ListManager"),
    "FilmHydrator": ("lboxdlib.lists.hydration", "FilmHydrator"),
    "HydrationResult": ("lboxdlib.lists.hydration", "HydrationResult"),
    "hydrate_lids": ("lboxdlib.lists.hydration", "hydrate_lids"),

    # Selection / search
    "select_rated_viewings": ("lboxdlib.selectors.rated_viewings", "select_rated_viewings"),
    "select_rating_range": ("lboxdlib.selectors.rated_viewings", "select_rating_range"),
    "SearchManager": ("lboxdlib.search", "SearchManager"),

    # Relationships
    "resolve_user_slug": ("lboxdlib.relationships", "resolve_user_slug"),
    "user_follows": ("lboxdlib.relationships", "user_follows"),

    # Common models
    "FilmSummary": ("lboxdlib.models.typedefs", "FilmSummary"),
    "ReviewSummary": ("lboxdlib.models.typedefs", "ReviewSummary"),
    "Comment": ("lboxdlib.models.comments", "Comment"),
    "CommentCollection": ("lboxdlib.models.comments", "CommentCollection"),
    "UserSummary": ("lboxdlib.parsers.users", "UserSummary"),
    "slugs_for_pronouns": ("lboxdlib.parsers.users", "slugs_for_pronouns"),
    "ActivityActor": ("lboxdlib.parsers.activity", "ActivityActor"),
    "ActivitySummary": ("lboxdlib.parsers.activity_summary", "ActivitySummary"),
    "ReferencedUser": ("lboxdlib.parsers.activity_summary", "ReferencedUser"),
    "DiaryEntry": ("lboxdlib.models.typedefs", "DiaryEntry"),
}

__all__ = ["__version__", *_LAZY_EXPORTS]


def __getattr__(name: str) -> Any:
    """Lazily resolve package-root public exports."""
    try:
        module_name, attr_name = _LAZY_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module 'lboxdlib' has no attribute {name!r}") from exc

    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
