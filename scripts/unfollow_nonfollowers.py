"""Opinionated non-reciprocal follow cleanup script entry point.

The policy of deciding who to unfollow belongs in scripts. Reusable primitives
such as ``unfollow_user``, ``batch_unfollow_users``, and ``user_follows`` are
part of the library API.
"""

from lboxdlib.modules.reciprocal_follow_cleanup import *  # noqa: F401,F403
