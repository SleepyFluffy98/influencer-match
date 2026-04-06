"""Shared types and utilities for all platform fetchers."""
from datetime import datetime, timezone

from pydantic import BaseModel


class InfluencerProfile(BaseModel):
    username: str
    full_name: str
    followers: int
    following: int
    posts_count: int
    engagement_rate: float          # decimal, e.g. 0.034 = 3.4%
    bio: str
    profile_url: str
    recent_post_captions: list[str] # last 5 captions / video titles
    fetched_at: str                 # ISO datetime string
    platform: str = "instagram"     # "instagram" | "youtube"
    avg_views: int | None = None    # YouTube lifetime avg; Instagram unused
    total_videos: int | None = None # YouTube only
    channel_category: str | None = None
    last_posted_at: str | None = None  # ISO datetime of most recent post/video


# Platform-specific follower tier ranges.
# Instagram: nano 5k-50k, micro 50k-100k, macro 100k+
# YouTube:   nano 1k-10k, micro 10k-100k, macro 100k+
TIER_RANGES: dict[str, dict[str, tuple[int, int]]] = {
    "instagram": {
        "nano":  (5_000,    50_000),
        "micro": (50_000,  100_000),
        "macro": (100_000, 10_000_000),
    },
    "youtube": {
        "nano":  (1_000,    10_000),
        "micro": (10_000,  100_000),
        "macro": (100_000, 10_000_000),
    },
}

# Activity filter thresholds applied to all platforms
MAX_DAYS_SINCE_POST = 30   # creator must have posted within this many days
MIN_AVG_ENGAGEMENT = 1_000 # avg likes (IG) or avg views (YT) per post


def _calc_engagement(interactions: float, views: float) -> float:
    """
    Calculate engagement rate as (likes + comments) / views, clamped to [0, 1].
    Returns 0.0 if views is zero.
    """
    if views == 0:
        return 0.0
    return min(round(interactions / views, 4), 1.0)


def _filter_by_tier(
    profiles: list[InfluencerProfile],
    tier: str,
    platform: str = "instagram",
) -> list[InfluencerProfile]:
    """Keep only profiles whose follower count falls within the requested tier."""
    platform_ranges = TIER_RANGES.get(platform, TIER_RANGES["instagram"])
    low, high = platform_ranges.get(tier, (0, 10_000_000))
    return [p for p in profiles if low <= p.followers < high]


