import json
import logging
import os

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

from services.fetchers import InfluencerProfile
from services.hashtag_generator import BrandBrief

load_dotenv()
logger = logging.getLogger(__name__)

_key = os.getenv("OPENAI_API_KEY")

if not _key:
    raise EnvironmentError("OPENAI_API_KEY is not set. Add it to your .env file.")

_client = OpenAI(api_key=_key)
_MODEL = "gpt-4o"  # stronger reasoning for nuanced brand-fit scoring

_PREFERENCE_PREFIX = """\
Learned preferences from past decisions:
{preference_context}

Apply these preferences when scoring — they reflect what this matchmaker values beyond the stated brand brief.

"""

PROMPT_TEMPLATE = """\
You are an expert influencer-brand matchmaker.

Brand brief:
- Industry: {industry}
- Target audience: {target_age}, {target_gender}
- Campaign goal: {campaign_goal}
- Follower tier: {follower_tier}
- Keywords / values: {keywords}
- Red flags to avoid: {red_flags}

Score each of the {count} influencer profiles below.

Scoring dimensions:
- audience_match (0-100): Do their followers match the brand's target demographics?
- niche_relevance (0-100): Does their content align with the brand's industry and values?
- engagement_quality (0-100): Is engagement rate healthy for their follower tier?
  Benchmarks by platform:
  Instagram (likes+comments/followers): nano >5%, micro >3%, macro >1.5%
  YouTube (avg_views/subscribers): <10k subs >20%, 10-100k >10%, 100k+ >5%
  Use profile.platform to apply the correct benchmark.
- brand_safety (0-100): Any red flags in bio or recent posts? 100 = clean.
- overall_score = audience_match*0.35 + niche_relevance*0.30 + \
engagement_quality*0.20 + brand_safety*0.15
- rationale: one sentence, top reason for the score

Return ONLY a valid JSON array. No markdown. No text outside the JSON.
Include all profiles regardless of score — filtering happens in Python.

JSON shape per item:
{{"username":"","audience_match":0,"niche_relevance":0,\
"engagement_quality":0,"brand_safety":0,"overall_score":0,"rationale":""}}

Profiles:
{profiles_json}\
"""

BATCH_SIZE = 10


class ScoredInfluencer(BaseModel):
    profile: InfluencerProfile
    audience_match: int
    niche_relevance: int
    engagement_quality: int
    brand_safety: int
    overall_score: int
    rationale: str
    status: str = "pending"              # "pending" | "approved" | "maybe" | "rejected"
    rejection_reason: str | None = None
    notes: str | None = None
    feedback_given_at: str | None = None  # ISO datetime


def score_profiles(
    profiles: list[InfluencerProfile],
    brief: BrandBrief,
    min_score: int = 60,
) -> list[ScoredInfluencer]:
    """
    Score each profile against the brand brief using Azure OpenAI.
    Batches of 10. Filters overall_score < min_score. Sorts descending.
    On JSON parse failure for a batch: log warning and skip that batch.
    Injects learned preferences from past decisions when enough data exists.
    """
    # Late import to avoid circular dependency: scorer ← preference_learner ← feedback ← store ← scorer
    from services.preference_learner import build_preference_context
    preference_context = build_preference_context()
    scored: list[ScoredInfluencer] = []

    for i in range(0, len(profiles), BATCH_SIZE):
        batch = profiles[i : i + BATCH_SIZE]
        scored.extend(_score_batch(batch, brief, preference_context))

    filtered = [s for s in scored if s.overall_score >= min_score]
    return sorted(filtered, key=lambda s: s.overall_score, reverse=True)


def _score_batch(
    batch: list[InfluencerProfile],
    brief: BrandBrief,
    preference_context: str = "",
) -> list[ScoredInfluencer]:
    """Call Azure OpenAI to score one batch of profiles. Returns empty list on parse failure."""
    profiles_json = json.dumps([_profile_to_dict(p) for p in batch], indent=2)
    prefix = _PREFERENCE_PREFIX.format(preference_context=preference_context) if preference_context else ""
    prompt = prefix + PROMPT_TEMPLATE.format(
        industry=brief.industry,
        target_age=brief.target_age,
        target_gender=brief.target_gender,
        campaign_goal=brief.campaign_goal,
        follower_tier=brief.follower_tier,
        keywords=brief.keywords,
        red_flags=brief.red_flags,
        count=len(batch),
        profiles_json=profiles_json,
    )

    response = _client.chat.completions.create(
        model=_MODEL,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.choices[0].message.content.strip()
    return _parse_scored_batch(raw, batch)


def _parse_scored_batch(
    raw: str,
    batch: list[InfluencerProfile],
) -> list[ScoredInfluencer]:
    """Parse Azure OpenAI's JSON response into ScoredInfluencer objects."""
    cleaned = raw.strip("```json").strip("```").strip()
    try:
        items = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("JSON parse failed for scoring batch. Raw: %s", raw[:200])
        return []

    profile_map = {p.username: p for p in batch}
    results: list[ScoredInfluencer] = []

    for item in items:
        username = item.get("username", "")
        profile = profile_map.get(username)
        if not profile:
            logger.warning("Scored username '%s' not found in batch — skipping.", username)
            continue
        try:
            results.append(
                ScoredInfluencer(
                    profile=profile,
                    audience_match=int(item.get("audience_match", 0)),
                    niche_relevance=int(item.get("niche_relevance", 0)),
                    engagement_quality=int(item.get("engagement_quality", 0)),
                    brand_safety=int(item.get("brand_safety", 0)),
                    overall_score=int(item.get("overall_score", 0)),
                    rationale=item.get("rationale", ""),
                )
            )
        except Exception as exc:
            logger.warning("Could not build ScoredInfluencer for '%s': %s", username, exc)

    return results


def _profile_to_dict(profile: InfluencerProfile) -> dict:
    """Serialize an InfluencerProfile to a compact dict for the scoring prompt."""
    d: dict = {
        "username": profile.username,
        "platform": profile.platform,
        "followers": profile.followers,
        "engagement_rate": f"{profile.engagement_rate:.1%}",
        "bio": profile.bio,
        "recent_posts": profile.recent_post_captions,
    }
    if profile.avg_views is not None:
        d["avg_views"] = profile.avg_views
    return d
