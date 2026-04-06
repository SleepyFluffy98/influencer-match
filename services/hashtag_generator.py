import json
import logging
import os

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, model_validator

load_dotenv()
logger = logging.getLogger(__name__)

_key = os.getenv("OPENAI_API_KEY")

if not _key:
    raise EnvironmentError("OPENAI_API_KEY is not set. Add it to your .env file.")

_client = OpenAI(api_key=_key)
_MODEL = "gpt-4o-mini"  # fast and cheap — sufficient for hashtag generation

PROMPT_TEMPLATE = """\
You are an Instagram hashtag research expert.

Given this brand brief, generate 5-8 Instagram hashtags that would surface
relevant influencers when searched. Focus on niche-specific tags that
creators in this space actually use — not generic tags like #instagood.

Brand brief:
- Industry: {industry}
- Target audience: {target_age}, {target_gender}
- Campaign goal: {campaign_goal}
- Keywords / values: {keywords}
- Countries / regions: {countries}

Return ONLY a JSON array of strings. No # symbol. No markdown. No explanation.
Example: ["veganbeauty", "cleanbeauty", "sustainableskincare"]\
"""


class BrandBrief(BaseModel):
    brand_name: str
    industry: str
    target_age: str
    target_gender: str
    campaign_goal: str
    follower_tier: str              # default tier when platform_tiers is empty
    keywords: str
    red_flags: str
    contact_email: str
    platforms: list[str] = ["instagram"]   # "instagram" | "youtube"
    countries: list[str] = []              # e.g. ["United Kingdom", "France"]
    platform_tiers: dict[str, str] = {}   # per-platform override, e.g. {"instagram": "nano"}

    @model_validator(mode="before")
    @classmethod
    def _migrate_country(cls, data: dict) -> dict:
        """Migrate old single-string 'country' field to the new 'countries' list."""
        if isinstance(data, dict) and "country" in data and "countries" not in data:
            old = data.pop("country", "")
            data["countries"] = [old] if old else []
        return data

    def get_tier_for(self, platform: str) -> str:
        """Return the follower tier to use for a given platform."""
        return self.platform_tiers.get(platform, self.follower_tier)


def generate_hashtags(brief: BrandBrief) -> list[str]:
    """
    Call Azure OpenAI to generate relevant Instagram hashtags from a brand brief.
    Returns a list of hashtag strings without # prefix.
    Raises ValueError if the response cannot be parsed as a JSON list.
    """
    prompt = PROMPT_TEMPLATE.format(
        industry=brief.industry,
        target_age=brief.target_age,
        target_gender=brief.target_gender,
        campaign_goal=brief.campaign_goal,
        keywords=brief.keywords,
        countries=", ".join(brief.countries) if brief.countries else "global",
    )

    response = _client.chat.completions.create(
        model=_MODEL,
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.choices[0].message.content.strip()
    return _parse_hashtag_response(raw, prompt)


def _parse_hashtag_response(raw: str, original_prompt: str) -> list[str]:
    """Parse the response as a JSON list, retrying once if it contains markdown fences."""
    cleaned = raw.strip("```json").strip("```").strip()
    try:
        result = json.loads(cleaned)
        if not isinstance(result, list):
            raise ValueError("Expected a JSON array")
        return [str(tag).lstrip("#") for tag in result]
    except (json.JSONDecodeError, ValueError):
        logger.warning("First hashtag parse attempt failed, retrying with stricter prompt.")

    retry_prompt = original_prompt + "\n\nIMPORTANT: Return ONLY the raw JSON array, nothing else."
    response = _client.chat.completions.create(
        model=_MODEL,
        max_tokens=256,
        messages=[{"role": "user", "content": retry_prompt}],
    )
    retry_raw = response.choices[0].message.content.strip().strip("```json").strip("```").strip()
    try:
        result = json.loads(retry_raw)
        if not isinstance(result, list):
            raise ValueError("Expected a JSON array")
        return [str(tag).lstrip("#") for tag in result]
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Could not parse hashtag response after retry: {retry_raw!r}") from exc
