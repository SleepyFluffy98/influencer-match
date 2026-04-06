"""Tests for fetcher logic — pure functions only, no real HTTP calls."""
import json
from unittest.mock import MagicMock, patch

import pytest

from services.fetchers import TIER_RANGES, InfluencerProfile, _calc_engagement, _filter_by_tier
from services.fetchers.instagram import _build_profile, _extract_post_data_by_user
from services.fetchers.youtube import _map_channel, _search_channels, _get_channel_details


# ── Shared utilities ──────────────────────────────────────────────────────────

def test_calc_engagement_normal():
    # (likes + comments) / views: 350 / 10_000 = 0.035
    assert _calc_engagement(350, 10_000) == pytest.approx(0.035, abs=1e-4)


def test_calc_engagement_zero_views():
    assert _calc_engagement(100, 0) == 0.0


def test_calc_engagement_clamped():
    # More interactions than views should clamp to 1.0
    assert _calc_engagement(5_000, 1_000) == 1.0


def test_tier_ranges_defined():
    for platform in ("instagram", "youtube"):
        assert platform in TIER_RANGES
        for tier in ("nano", "micro", "macro"):
            assert tier in TIER_RANGES[platform]


def test_filter_by_tier_micro():
    def _make(username, followers):
        return InfluencerProfile(
            username=username, full_name=username, followers=followers,
            following=0, posts_count=10, engagement_rate=0.03,
            bio="", profile_url="", recent_post_captions=[],
            fetched_at="2026-01-01T00:00:00+00:00",
        )
    profiles = [
        _make("nano_user", 5_000),
        _make("micro_user", 50_000),
        _make("macro_user", 500_000),
    ]
    result = _filter_by_tier(profiles, "micro", platform="instagram")
    assert len(result) == 1
    assert result[0].username == "micro_user"


# ── Instagram fetcher ─────────────────────────────────────────────────────────

def test_extract_post_data_by_user():
    posts = [
        {"ownerUsername": "alice", "caption": "Post 1", "likesCount": 100, "commentsCount": 10},
        {"ownerUsername": "alice", "caption": "Post 2", "likesCount": 200, "commentsCount": 20},
        {"ownerUsername": "bob",   "caption": "Post A", "likesCount": 50,  "commentsCount": 5},
    ]
    data = _extract_post_data_by_user(posts)
    assert set(data.keys()) == {"alice", "bob"}
    assert data["alice"]["total_likes"] == 300
    assert data["alice"]["total_comments"] == 30
    assert len(data["alice"]["captions"]) == 2


def test_extract_post_data_skips_missing_username():
    posts = [{"caption": "no username", "likesCount": 10, "commentsCount": 1}]
    data = _extract_post_data_by_user(posts)
    assert data == {}


def test_build_profile_basic():
    item = {
        "username": "alice",
        "fullName": "Alice A",
        "followersCount": 50_000,
        "followingCount": 400,
        "postsCount": 120,
        "biography": "Clean beauty creator",
        "url": "https://instagram.com/alice",
    }
    post_data = {"captions": ["Post 1", "Post 2"], "total_likes": 500, "total_comments": 50}
    profile = _build_profile("alice", item, post_data)
    assert profile is not None
    assert profile.username == "alice"
    assert profile.followers == 50_000
    assert profile.platform == "instagram"
    assert profile.engagement_rate > 0


def test_build_profile_missing_username_returns_none():
    assert _build_profile("", {}, {}) is None


@patch("services.fetchers.instagram.httpx.post")
@patch("services.fetchers.instagram.generate_hashtags", return_value=["cleanbeauty"])
def test_fetch_instagram_returns_empty_on_no_posts(mock_hashtags, mock_post):
    """If Apify returns no posts, fetch_instagram returns []."""
    from services.fetchers.instagram import fetch_instagram
    from services.hashtag_generator import BrandBrief

    mock_response = MagicMock()
    mock_response.json.return_value = []
    mock_response.raise_for_status = MagicMock()
    mock_post.return_value = mock_response

    brief = BrandBrief(
        brand_name="Test", industry="beauty", target_age="18-24",
        target_gender="female", campaign_goal="awareness", follower_tier="micro",
        keywords="clean beauty", red_flags="none", contact_email="t@t.com",
    )
    result = fetch_instagram(brief)
    assert result == []


# ── YouTube fetcher ───────────────────────────────────────────────────────────

def test_map_channel_basic():
    ch = {
        "channel_id": "UC123",
        "username": "glowchannel",
        "full_name": "Glow Channel",
        "bio": "Clean beauty reviews",
        "subscribers": 40_000,
        "view_count": 2_000_000,
        "video_count": 100,
        "uploads_playlist": "UU123",
    }
    # engagement_rate now comes from per-video (likes+comments)/views in video_data
    video_data = {"titles": ["Video 1", "Video 2"], "last_published_at": None, "engagement_rate": 0.05}
    profile = _map_channel(ch, video_data)
    assert profile is not None
    assert profile.platform == "youtube"
    assert profile.avg_views == 20_000
    assert profile.engagement_rate == pytest.approx(0.05, abs=1e-4)
    assert profile.recent_post_captions == ["Video 1", "Video 2"]


def test_map_channel_zero_videos():
    ch = {
        "channel_id": "UC456", "username": "empty", "full_name": "Empty",
        "bio": "", "subscribers": 10_000, "view_count": 0,
        "video_count": 0, "uploads_playlist": "",
    }
    profile = _map_channel(ch, {"titles": [], "last_published_at": None, "engagement_rate": 0.0})
    assert profile is not None
    assert profile.avg_views == 0
    assert profile.engagement_rate == 0.0


@patch("services.fetchers.youtube.httpx.get")
def test_search_channels_parses_response(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "items": [
            {"snippet": {"channelId": "UC001"}},
            {"snippet": {"channelId": "UC002"}},
        ]
    }
    mock_response.raise_for_status = MagicMock()
    mock_get.return_value = mock_response

    result = _search_channels("clean beauty", "fake_key")
    assert result == ["UC001", "UC002"]


@patch("services.fetchers.youtube.os.getenv", return_value="fake_key")
@patch("services.fetchers.youtube.httpx.get")
def test_fetch_youtube_returns_empty_on_http_error(mock_get, mock_env):
    import httpx
    from services.fetchers.youtube import fetch_youtube
    from services.hashtag_generator import BrandBrief

    mock_get.side_effect = httpx.HTTPError("connection failed")

    brief = BrandBrief(
        brand_name="Test", industry="beauty", target_age="18-24",
        target_gender="female", campaign_goal="awareness", follower_tier="micro",
        keywords="clean beauty", red_flags="none", contact_email="t@t.com",
        platforms=["youtube"],
    )
    result = fetch_youtube(brief)
    assert result == []
