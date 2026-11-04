"""Film scraping/data-acquisition script entry point.

This is script-level behavior rather than the main public library API. Stable
fetching and list/hydration objects are available from ``lboxdlib.fetch`` and
``lboxdlib.lists``.
"""

from lboxdlib.modules.scrape_films import *  # noqa: F401,F403
