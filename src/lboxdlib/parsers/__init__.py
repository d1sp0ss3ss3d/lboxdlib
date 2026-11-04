"""HTML parsers for Letterboxd pages and fragments."""

from lboxdlib.parsers.activity import ActivityActor, extract_activity, extract_activity_items
from lboxdlib.parsers.activity_summary import ActivitySummary, ReferencedUser
from lboxdlib.parsers.comments import extract_comments
from lboxdlib.parsers.films import extract_films, extract_lids
from lboxdlib.parsers.members import extract_member_summaries, member_summary_from_dom
from lboxdlib.parsers.reviews import extract_reviews
from lboxdlib.parsers.users import extract_user_slugs, extract_users, slugs_for_pronouns

__all__ = [
    "extract_activity",
    "extract_activity_items",
    "ActivityActor",
    "ActivitySummary",
    "ReferencedUser",
    "extract_comments",
    "extract_films",
    "extract_lids",
    "extract_reviews",
    "extract_users",
    "extract_user_slugs",
    "slugs_for_pronouns",
    "extract_member_summaries",
    "member_summary_from_dom",
]
