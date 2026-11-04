"""Multi-step workflows built from lboxdlib fetchers, selectors, and actions."""

from lboxdlib.workflows.batch_like_reviews import (
    BatchLikeResult,
    ReviewLikeCandidate,
    ReviewLikeFailure,
    batch_like_reviews,
    like_num_reviews,
    like_page_range_reviews,
    like_until_liked_reviews,
)
from lboxdlib.workflows.incoming_engagement import incoming_engagement_report
from lboxdlib.workflows.reciprocal_follow_cleanup import (
    ReciprocalCleanupResult,
    ReciprocalUserDecision,
    unfollow_nonfollowers,
)
from lboxdlib.workflows.scrape_films import (
    compare_two_lists,
    enrich_films_with_omdb_plots,
    fetch_all_films,
    list_diff,
)

__all__ = [
    "batch_like_reviews",
    "like_num_reviews",
    "like_page_range_reviews",
    "like_until_liked_reviews",
    "BatchLikeResult",
    "ReviewLikeCandidate",
    "ReviewLikeFailure",
    "incoming_engagement_report",
    "unfollow_nonfollowers",
    "ReciprocalCleanupResult",
    "ReciprocalUserDecision",
    "fetch_all_films",
    "list_diff",
    "compare_two_lists",
    "enrich_films_with_omdb_plots",
]
