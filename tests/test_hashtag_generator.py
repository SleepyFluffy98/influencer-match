"""Tests for services/hashtag_generator.py — pure logic only, no API calls."""
import pytest

from services.hashtag_generator import BrandBrief, _parse_hashtag_response


SAMPLE_BRIEF = BrandBrief(
    brand_name="GlowLab",
    industry="beauty",
    target_age="18-24",
    target_gender="female",
    campaign_goal="awareness",
    follower_tier="micro",
    keywords="clean beauty, sustainable skincare",
    red_flags="no alcohol",
    contact_email="test@example.com",
)


def test_parse_plain_json_array():
    raw = '["cleanbeauty", "sustainableskincare", "skincareRoutine"]'
    result = _parse_hashtag_response(raw, "")
    assert result == ["cleanbeauty", "sustainableskincare", "skincareRoutine"]


def test_parse_strips_markdown_fences():
    raw = '```json\n["veganbeauty", "cleanbeauty"]\n```'
    result = _parse_hashtag_response(raw, "")
    assert result == ["veganbeauty", "cleanbeauty"]


def test_parse_strips_hash_prefix():
    raw = '["#cleanbeauty", "#fitness"]'
    result = _parse_hashtag_response(raw, "")
    assert result == ["cleanbeauty", "fitness"]


def test_parse_invalid_raises_value_error(monkeypatch):
    """Stub the retry call to also return garbage so ValueError is raised."""
    import anthropic

    class FakeContent:
        text = "not json at all"

    class FakeResponse:
        content = [FakeContent()]

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        class messages:
            @staticmethod
            def create(**kwargs):
                return FakeResponse()

    monkeypatch.setattr("services.hashtag_generator._client", FakeClient())

    with pytest.raises(ValueError, match="Could not parse"):
        _parse_hashtag_response("definitely not json", "some prompt")
