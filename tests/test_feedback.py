"""Tests for db/feedback.py and services/preference_learner.py."""
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from services.fetcher import InfluencerProfile
from services.hashtag_generator import BrandBrief
from services.scorer import ScoredInfluencer


# ── Fixtures ───────────────────────────────────────────────────────────────────

def make_profile(username: str = "testuser", platform: str = "instagram") -> InfluencerProfile:
    return InfluencerProfile(
        username=username,
        full_name="Test User",
        followers=25_000,
        following=500,
        posts_count=100,
        engagement_rate=0.04,
        bio="Clean beauty creator",
        profile_url=f"https://instagram.com/{username}",
        recent_post_captions=["Post 1"],
        fetched_at="2026-01-01T00:00:00+00:00",
        platform=platform,
    )


def make_scored(username: str = "testuser", overall_score: int = 80) -> ScoredInfluencer:
    return ScoredInfluencer(
        profile=make_profile(username),
        audience_match=80,
        niche_relevance=80,
        engagement_quality=80,
        brand_safety=100,
        overall_score=overall_score,
        rationale="Good fit.",
        status="pending",
    )


BRIEF = BrandBrief(
    brand_name="GlowLab",
    industry="beauty",
    target_age="18-24",
    target_gender="female",
    campaign_goal="awareness",
    follower_tier="micro",
    keywords="clean beauty",
    red_flags="none",
    contact_email="test@example.com",
)


# ── save_feedback — writes correct jsonl format ────────────────────────────────

def test_save_feedback_writes_jsonl(tmp_path):
    """save_feedback should append one correctly-shaped JSON line to the log."""
    from db import feedback as fb_module

    # Build a minimal fake job
    from db.store import SearchJob
    job_id = "test-job-123"
    scored = make_scored("alice", overall_score=85)
    job = SearchJob(
        job_id=job_id,
        brand_brief=BRIEF,
        status="complete",
        hashtags_used=[],
        results=[scored],
        created_at="2026-01-01T00:00:00+00:00",
    )

    log_path = tmp_path / "feedback_log.jsonl"

    with (
        patch.object(fb_module, "FEEDBACK_LOG", log_path),
        patch("db.feedback.load_job", return_value=job),
        patch("db.feedback.update_status") as mock_update,
    ):
        fb_module.save_feedback(job_id, "alice", "approved", None, "Great creator")

    assert log_path.exists(), "JSONL file should be created"
    lines = [l for l in log_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 1

    entry = json.loads(lines[0])
    assert entry["username"] == "alice"
    assert entry["status"] == "approved"
    assert entry["rejection_reason"] is None
    assert entry["overall_score"] == 85
    assert entry["audience_match"] == 80
    assert entry["brand_industry"] == "beauty"
    assert entry["follower_tier"] == "micro"
    assert "timestamp" in entry
    mock_update.assert_called_once()


def test_save_feedback_appends_multiple_lines(tmp_path):
    """Multiple calls should append separate lines, not overwrite."""
    from db import feedback as fb_module
    from db.store import SearchJob

    job_id = "test-job-456"
    alice = make_scored("alice")
    bob   = make_scored("bob", overall_score=70)
    job   = SearchJob(
        job_id=job_id, brand_brief=BRIEF, status="complete",
        hashtags_used=[], results=[alice, bob],
        created_at="2026-01-01T00:00:00+00:00",
    )

    log_path = tmp_path / "feedback_log.jsonl"

    with (
        patch.object(fb_module, "FEEDBACK_LOG", log_path),
        patch("db.feedback.load_job", return_value=job),
        patch("db.feedback.update_status"),
    ):
        fb_module.save_feedback(job_id, "alice", "approved", None, None)
        fb_module.save_feedback(job_id, "bob", "rejected", "Wrong audience demographics", None)

    lines = [l for l in log_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["status"] == "approved"
    assert json.loads(lines[1])["status"] == "rejected"
    assert json.loads(lines[1])["rejection_reason"] == "Wrong audience demographics"


def test_save_feedback_unknown_username_does_not_write(tmp_path):
    """save_feedback should not write to the log if username is not in the job."""
    from db import feedback as fb_module
    from db.store import SearchJob

    job = SearchJob(
        job_id="job-x", brand_brief=BRIEF, status="complete",
        hashtags_used=[], results=[make_scored("alice")],
        created_at="2026-01-01T00:00:00+00:00",
    )
    log_path = tmp_path / "feedback_log.jsonl"

    with (
        patch.object(fb_module, "FEEDBACK_LOG", log_path),
        patch("db.feedback.load_job", return_value=job),
        patch("db.feedback.update_status"),
    ):
        fb_module.save_feedback("job-x", "unknown_user", "approved", None, None)

    assert not log_path.exists()


# ── get_feedback_stats — returns correct approval_rate ─────────────────────────

def _make_log_entries(statuses: list[str]) -> list[dict]:
    return [
        {
            "timestamp": "2026-01-01T00:00:00+00:00",
            "job_id": "job-1",
            "username": f"user{i}",
            "platform": "instagram",
            "brand_industry": "beauty",
            "brand_keywords": "clean beauty",
            "follower_tier": "micro",
            "status": s,
            "rejection_reason": "Wrong audience demographics" if s == "rejected" else None,
            "overall_score": 80 if s == "approved" else 60,
            "audience_match": 80,
            "niche_relevance": 80,
            "engagement_quality": 70,
            "brand_safety": 100,
        }
        for i, s in enumerate(statuses)
    ]


def test_get_feedback_stats_approval_rate():
    """approval_rate should be (approved+maybe) / total."""
    from db import feedback as fb_module

    entries = _make_log_entries(["approved", "approved", "rejected", "maybe"])
    with patch.object(fb_module, "load_feedback_log", return_value=entries):
        stats = fb_module.get_feedback_stats()

    assert stats["total_decisions"] == 4
    assert stats["approval_rate"] == pytest.approx(0.75)  # 3 approved/maybe out of 4


def test_get_feedback_stats_empty_log():
    """Empty log should return zero values without raising."""
    from db import feedback as fb_module

    with patch.object(fb_module, "load_feedback_log", return_value=[]):
        stats = fb_module.get_feedback_stats()

    assert stats["total_decisions"] == 0
    assert stats["approval_rate"] == 0.0
    assert stats["top_rejection_reasons"] == []


def test_get_feedback_stats_top_rejection_reasons():
    """top_rejection_reasons should be sorted by frequency descending."""
    from db import feedback as fb_module

    entries = _make_log_entries(["rejected", "rejected", "rejected", "approved"])
    entries[0]["rejection_reason"] = "Brand safety concern"
    entries[1]["rejection_reason"] = "Wrong audience demographics"
    entries[2]["rejection_reason"] = "Brand safety concern"

    with patch.object(fb_module, "load_feedback_log", return_value=entries):
        stats = fb_module.get_feedback_stats()

    reasons = stats["top_rejection_reasons"]
    assert reasons[0] == ("Brand safety concern", 2)
    assert reasons[1] == ("Wrong audience demographics", 1)


def test_get_feedback_stats_avg_scores():
    """avg_score_approved and avg_score_rejected should be computed separately."""
    from db import feedback as fb_module

    entries = _make_log_entries(["approved", "approved", "rejected", "rejected"])
    entries[0]["overall_score"] = 90
    entries[1]["overall_score"] = 80
    entries[2]["overall_score"] = 65
    entries[3]["overall_score"] == 55

    with patch.object(fb_module, "load_feedback_log", return_value=entries):
        stats = fb_module.get_feedback_stats()

    assert stats["avg_score_approved"] == pytest.approx(85.0)


# ── build_preference_context — returns empty string below threshold ────────────

def test_build_preference_context_below_threshold():
    """Should return empty string when fewer than min_decisions entries exist."""
    from services import preference_learner as pl_module

    with patch.object(pl_module, "load_feedback_log", return_value=[{"x": 1}] * 5):
        result = pl_module.build_preference_context(min_decisions=15)

    assert result == ""


def test_build_preference_context_returns_empty_without_client():
    """Should return empty string gracefully when Azure client is not configured."""
    from services import preference_learner as pl_module

    entries = _make_log_entries(["approved"] * 10 + ["rejected"] * 10)
    with (
        patch.object(pl_module, "load_feedback_log", return_value=entries),
        patch.object(pl_module, "_client", None),
    ):
        result = pl_module.build_preference_context(min_decisions=15)

    assert result == ""


def test_build_preference_context_calls_ai_above_threshold():
    """Should call the AI client and return its response when enough data exists."""
    from services import preference_learner as pl_module

    entries = _make_log_entries(["approved"] * 10 + ["rejected"] * 10)
    fake_response = MagicMock()
    fake_response.choices[0].message.content = "Based on 20 past decisions: prefer high engagement."
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_response

    with (
        patch.object(pl_module, "load_feedback_log", return_value=entries),
        patch.object(pl_module, "_client", fake_client),
        patch("db.feedback.load_feedback_log", return_value=entries),
    ):
        result = pl_module.build_preference_context(min_decisions=15)

    assert "Based on 20 past decisions" in result
    fake_client.chat.completions.create.assert_called_once()
