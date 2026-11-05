"""Website source factory: the sample stand-in by default, live HTTP when configured.

Selected by environment (``RE_WEBSITE_SOURCE``), not by code change, so the maintenance
worker runs unmodified in a hackathon demo (``data/sample_websites/``) and against
organizations' real sites once that's turned on.
"""

from __future__ import annotations

import os


def get_website_source(store=None):
    """``RE_WEBSITE_SOURCE`` ("sample" | "live") picks explicitly; "sample" by default."""
    choice = os.environ.get("RE_WEBSITE_SOURCE", "sample").strip().lower()
    if choice == "live":
        from maintenance.website_source.live import LiveWebsiteSource

        return LiveWebsiteSource(store)
    from maintenance.website_source.sample import SampleWebsiteSource

    return SampleWebsiteSource()
