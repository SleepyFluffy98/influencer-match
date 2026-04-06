"""
Minimal-cost test: verifies Azure OpenAI is wired up correctly.
- Calls generate_hashtags (real API, ~$0.001)
- Calls score_profiles with 3 hardcoded profiles (real API, ~$0.005)
- No Apify call — zero scraping cost

Usage: python scripts/test_ai.py
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.fetcher import InfluencerProfile
from services.hashtag_generator import BrandBrief, generate_hashtags
from services.scorer import score_profiles

BRIEF = BrandBrief(
    brand_name="GlowLab",
    industry="beauty",
    target_age="18-24",
    target_gender="female",
    campaign_goal="awareness",
    follower_tier="micro",
    keywords="clean beauty, sustainable skincare, minimalist",
    red_flags="no alcohol, no fast fashion",
    contact_email="test@example.com",
)

MOCK_PROFILES = [
    InfluencerProfile(
        username="cleanbeautysophia",
        full_name="Sophia Lee",
        followers=42_000,
        following=800,
        posts_count=210,
        engagement_rate=0.048,
        bio="Clean beauty lover 🌿 Sharing honest skincare reviews",
        profile_url="https://instagram.com/cleanbeautysophia",
        recent_post_captions=[
            "My zero-waste morning routine ✨",
            "Why I switched to clean SPF — honest review",
            "Minimalist shelfie goals 🧴",
        ],
        fetched_at=datetime.now(timezone.utc).isoformat(),
    ),
    InfluencerProfile(
        username="fastfashionqueen",
        full_name="Mia B",
        followers=55_000,
        following=1_200,
        posts_count=450,
        engagement_rate=0.021,
        bio="Fashion & lifestyle. Collab: dm me 💌",
        profile_url="https://instagram.com/fastfashionqueen",
        recent_post_captions=[
            "Haul from Shein 🛍️",
            "My favourite vodka soda recipe 🍸",
            "Night out look inspo",
        ],
        fetched_at=datetime.now(timezone.utc).isoformat(),
    ),
    InfluencerProfile(
        username="botanicglow",
        full_name="Ren Nakamura",
        followers=18_000,
        following=400,
        posts_count=130,
        engagement_rate=0.062,
        bio="Botanist turned beauty creator 🌸 Plant-powered skincare",
        profile_url="https://instagram.com/botanicglow",
        recent_post_captions=[
            "Ingredients I avoid and why 🌿",
            "DIY face oil — 3 ingredients only",
            "Sustainable packaging swaps I made",
        ],
        fetched_at=datetime.now(timezone.utc).isoformat(),
    ),
]


def main() -> None:
    print("\n=== Step 1: Hashtag generation (Azure OpenAI) ===")
    try:
        hashtags = generate_hashtags(BRIEF)
        print(f"  OK — {hashtags}")
    except Exception as exc:
        print(f"  FAILED — {exc}")
        return

    print("\n=== Step 2: Scoring 3 mock profiles (Azure OpenAI) ===")
    try:
        scored = score_profiles(MOCK_PROFILES, BRIEF, min_score=0)
        for s in scored:
            flag = "✓" if s.overall_score >= 60 else "✗"
            print(f"  {flag} @{s.profile.username} — overall={s.overall_score} | {s.rationale}")
    except Exception as exc:
        print(f"  FAILED — {exc}")
        return

    print("\nAll checks passed. Azure OpenAI is connected and working.\n")


if __name__ == "__main__":
    main()
