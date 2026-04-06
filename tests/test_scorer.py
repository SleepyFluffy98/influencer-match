"""Tests for services/scorer.py — pure logic only, no API calls."""
import json

import pytest

from services.fetcher import InfluencerProfile
from services.hashtag_generator import BrandBrief
from services.scorer import ScoredInfluencer, _parse_scored_batch, _profile_to_dict


def make_profile(username: str = "testuser", followers: int = 50_000) -> InfluencerProfile:
    return InfluencerProfile(
        username=username,
        full_name="Test User",
        followers=followers,
        following=500,
        posts_count=100,
        engagement_rate=0.04,
        bio="Clean beauty creator",
        profile_url=f"https://instagram.com/{username}",
        recent_post_captions=["Post 1", "Post 2"],
        fetched_at="2026-01-01T00:00:00+00:00",
    )


SAMPLE_BRIEF = BrandBrief(
    brand_name="GlowLab",
    industry="beauty",
    target_age="18-24",
    target_gender="female",
    campaign_goal="awareness",
    follower_tier="micro",
    keywords="clean beauty",
    red_flags="no alcohol",
    contact_email="test@example.com",
)


def test_parse_scored_batch_valid():
    profile = make_profile("alice")
    raw = json.dumps([{
        "username": "alice",
        "audience_match": 80,
        "niche_relevance": 75,
        "engagement_quality": 70,
        "brand_safety": 90,
        "overall_score": 79,
        "rationale": "Strong fit for clean beauty audience.",
    }])
    results = _parse_scored_batch(raw, [profile])
    assert len(results) == 1
    assert results[0].overall_score == 79
    assert results[0].status == "pending"


def test_parse_scored_batch_invalid_json_returns_empty():
    profile = make_profile("alice")
    results = _parse_scored_batch("not json at all", [profile])
    assert results == []


def test_parse_scored_batch_unknown_username_skipped():
    profile = make_profile("alice")
    raw = json.dumps([{
        "username": "unknown_person",
        "audience_match": 80, "niche_relevance": 75,
        "engagement_quality": 70, "brand_safety": 90,
        "overall_score": 79, "rationale": "N/A",
    }])
    results = _parse_scored_batch(raw, [profile])
    assert results == []


def test_profile_to_dict_keys():
    d = _profile_to_dict(make_profile())
    assert set(d.keys()) == {"username", "platform", "followers", "engagement_rate", "bio", "recent_posts"}
