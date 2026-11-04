"""Opinionated batch-review liking script entry point.

This script is intentionally outside the stable public package API. Reusable
primitives live under ``lboxdlib.actions``, ``lboxdlib.fetch``, and
``lboxdlib.select``; the current legacy automation remains available here for
project use.
"""

from lboxdlib.modules.batch_like_reviews import *  # noqa: F401,F403
