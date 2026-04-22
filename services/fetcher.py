"""
Thin router: reads brief.platforms and delegates to the right sub-fetcher(s).
Re-exports InfluencerProfile and TIER_RANGES for backward-compatibility
with scorer.py, db/store.py, and services/mocks.py.
"""
import logging

from services.fetchers import InfluencerProfile, TIER_RANGES  # noqa: F401 — re-exported
from services.fetchers.instagram import fetch_instagram
from services.fetchers.youtube import fetch_youtube
from services.hashtag_generator import BrandBrief

logger = logging.getLogger(__name__)


def fetch_profiles(brief: BrandBrief) -> list[InfluencerProfile]:
    """
    Call the appropriate sub-fetcher(s) based on brief.platforms,
    merge results, and deduplicate by platform+username.
    """
    results: list[InfluencerProfile] = []

    if "instagram" in brief.platforms:
        ig = fetch_instagram(brief)
        logger.info("Instagram fetch returned %d profiles", len(ig))
        results.extend(ig)

    if "youtube" in brief.platforms:
        yt = fetch_youtube(brief)
        logger.info("YouTube fetch returned %d profiles", len(yt))
        results.extend(yt)

    seen: set[str] = set()
    deduped: list[InfluencerProfile] = []
    for p in results:
        key = f"{p.platform}:{p.username}"
        if key not in seen:
            seen.add(key)
            deduped.append(p)

    logger.info("fetch_profiles: %d total after dedup", len(deduped))
    return deduped
