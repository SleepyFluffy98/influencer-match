"""
Mock implementations of all service functions.
Used when USE_MOCK=true in .env so the UI can run without real API keys.
"""
import random
from datetime import datetime, timezone

from services.fetcher import InfluencerProfile
from services.hashtag_generator import BrandBrief
from services.scorer import ScoredInfluencer

_IG_USERNAMES = [
    "glowwithsophia", "cleanbeauty.co", "skincarebynat", "minimalbeautyhub",
    "sustainableglow", "theradiantroutine", "purescintilla", "earthtonebeauty",
    "velvetcleanse", "botanicglow",
]

_YT_USERNAMES = [
    "GlowLabReviews", "CleanBeautyWithMia", "SkinMinimalist",
    "SustainableStyleTV", "WellnessWithRen",
]

_RATIONALES = [
    "Strong alignment with clean beauty values and target demographic.",
    "High engagement rate among 18-24 female audience.",
    "Content consistently features sustainable skincare routines.",
    "Bio and recent posts reflect minimalist aesthetic the brand wants.",
    "Micro-influencer with above-benchmark engagement for follower tier.",
    "Niche audience overlap with brand's campaign goal.",
    "Recent posts show genuine product use, not just sponsorships.",
    "Audience demographics match target profile closely.",
]


def generate_hashtags(brief: BrandBrief) -> list[str]:
    """Return a fake hashtag list based on the brief's industry keyword."""
    base = {
        "beauty":    ["cleanbeauty", "skincareroutine", "sustainableskincare", "glowskin", "naturalskincare"],
        "fitness":   ["fitnessmotivation", "workoutroutine", "homegym", "fitlife", "healthylifestyle"],
        "fashion":   ["sustainablefashion", "ootd", "slowfashion", "minimalistfashion", "ethicalstyle"],
        "food":      ["cleaneating", "plantbased", "healthyrecipes", "foodblogger", "mealprep"],
        "lifestyle": ["minimaliving", "slowliving", "intentionalliving", "wellnessroutine", "selfcare"],
    }
    return base.get(brief.industry, ["lifestyle", "content", "creator", "brand", "collab"])[:6]


def fetch_profiles(brief: BrandBrief) -> list[InfluencerProfile]:
    """Return fake InfluencerProfile objects for each requested platform."""
    tier_followers = {"nano": (3_000, 9_500), "micro": (12_000, 95_000), "macro": (110_000, 900_000)}
    low, high = tier_followers.get(brief.follower_tier, (10_000, 100_000))
    profiles: list[InfluencerProfile] = []

    if "instagram" in brief.platforms:
        for username in _IG_USERNAMES:
            followers = random.randint(low, high)
            profiles.append(InfluencerProfile(
                username=username,
                full_name=username.replace(".", " ").title(),
                followers=followers,
                following=random.randint(200, 2_000),
                posts_count=random.randint(50, 500),
                engagement_rate=round(random.uniform(0.02, 0.08), 4),
                bio="✨ #cleanbeauty | sharing what I love",
                profile_url=f"https://www.instagram.com/{username}/",
                recent_post_captions=[
                    "My morning skincare routine ✨",
                    "New favourite cleanser — so gentle!",
                    "Sunday reset 🌿",
                    "Sustainable swaps I made this month",
                    "No-makeup makeup look using clean products",
                ],
                fetched_at=datetime.now(timezone.utc).isoformat(),
                platform="instagram",
                last_posted_at=datetime.now(timezone.utc).isoformat(),
            ))

    if "youtube" in brief.platforms:
        for username in _YT_USERNAMES:
            subscribers = random.randint(low, high)
            avg_views = int(subscribers * random.uniform(0.05, 0.20))
            profiles.append(InfluencerProfile(
                username=username,
                full_name=username.replace("With", " with ").replace("TV", " TV"),
                followers=subscribers,
                following=0,
                posts_count=random.randint(30, 300),
                engagement_rate=round(avg_views / subscribers, 4),
                bio=f"YouTube channel about {brief.industry} | New videos weekly",
                profile_url=f"https://youtube.com/@{username}",
                recent_post_captions=[
                    "My honest review of this viral product",
                    "5 clean beauty swaps you need to try",
                    "Full routine using only sustainable brands",
                    "Testing TikTok skincare trends — worth it?",
                    "The truth about clean beauty (what nobody tells you)",
                ],
                fetched_at=datetime.now(timezone.utc).isoformat(),
                platform="youtube",
                avg_views=avg_views,
                total_videos=random.randint(30, 300),
                last_posted_at=datetime.now(timezone.utc).isoformat(),
            ))

    return profiles


def score_profiles(
    profiles: list[InfluencerProfile],
    brief: BrandBrief,
    min_score: int = 60,
) -> list[ScoredInfluencer]:
    """Return fake ScoredInfluencer objects, all scoring above min_score."""
    scored = []
    for profile in profiles:
        am = random.randint(65, 95)
        nr = random.randint(60, 95)
        eq = random.randint(60, 90)
        bs = random.randint(75, 100)
        overall = int(am * 0.35 + nr * 0.30 + eq * 0.20 + bs * 0.15)
        if overall < min_score:
            continue
        scored.append(ScoredInfluencer(
            profile=profile,
            audience_match=am,
            niche_relevance=nr,
            engagement_quality=eq,
            brand_safety=bs,
            overall_score=overall,
            rationale=random.choice(_RATIONALES),
            status="pending",
        ))
    return sorted(scored, key=lambda s: s.overall_score, reverse=True)
