"""
Debug script: shows raw YouTube results before tier filtering.
Usage: python scripts/debug_youtube.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import find_dotenv, load_dotenv
load_dotenv(find_dotenv())

from services.fetchers import TIER_RANGES, _filter_by_tier
from services.fetchers.youtube import (
    _build_search_query,
    _get_channel_details,
    _get_recent_video_titles,
    _map_channel,
    _search_channels,
)
from services.hashtag_generator import BrandBrief

import os
api_key = os.getenv("YOUTUBE_API_KEY")

BRIEF = BrandBrief(
    brand_name="TestBrand",
    industry="fashion",
    target_age="18-24",
    target_gender="female",
    campaign_goal="awareness",
    follower_tier="micro",
    keywords="minimalist, clean fit, french chic",
    red_flags="none",
    contact_email="test@test.com",
    platforms=["youtube"],
)

query = _build_search_query(BRIEF)
print(f"\nSearch query: '{query}'")
print(f"Tier filter : {BRIEF.follower_tier} → {TIER_RANGES[BRIEF.follower_tier]}\n")

channel_ids = _search_channels(query, api_key)
print(f"Channels found: {len(channel_ids)}")

channels = _get_channel_details(channel_ids, api_key)
print(f"\n{'Username':<30} {'Subscribers':>12} {'In tier?':>10}")
print("-" * 55)

profiles = []
for ch in channels:
    profile = _map_channel(ch, [])
    if not profile:
        continue
    profiles.append(profile)
    low, high = TIER_RANGES[BRIEF.follower_tier]
    in_tier = "✓" if low <= profile.followers < high else "✗"
    print(f"@{profile.username:<29} {profile.followers:>12,} {in_tier:>10}")

matched = _filter_by_tier(profiles, BRIEF.follower_tier)
print(f"\nBefore filter: {len(profiles)}  |  After filter: {len(matched)}")

if not matched and profiles:
    subs = sorted(p.followers for p in profiles)
    print(f"\nSubscriber range of returned channels: {subs[0]:,} – {subs[-1]:,}")
    print(f"Tier '{BRIEF.follower_tier}' expects: {TIER_RANGES[BRIEF.follower_tier][0]:,} – {TIER_RANGES[BRIEF.follower_tier][1]:,}")
    print("\nSuggestion: try a different follower_tier in the form.")
