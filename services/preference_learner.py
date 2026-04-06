"""
Analyses the feedback log and generates a natural-language summary of the
matchmaker's observed preferences. Injected into the scoring prompt so future
scores reflect learned taste, not just the brand brief alone.
"""
import logging
import os

from dotenv import load_dotenv
from openai import OpenAI

from db.feedback import get_feedback_stats, load_feedback_log

load_dotenv()
logger = logging.getLogger(__name__)

_key = os.getenv("OPENAI_API_KEY")
_MODEL = "gpt-4o-mini"  # cheap — this runs on every search

_client: OpenAI | None = None
if _key:
    _client = OpenAI(api_key=_key)


def build_preference_context(min_decisions: int = 15) -> str:
    """
    Read feedback_log.jsonl. If fewer than min_decisions exist, return empty string.

    Otherwise, analyse the log and return a natural-language paragraph summarising
    observed preferences, e.g.:
      "Based on 34 past decisions: you tend to approve creators with engagement
       rate above 4.2% and reject those below 2.8%. ..."

    This string is injected into the scoring prompt before the brand brief.
    """
    log = load_feedback_log()
    if len(log) < min_decisions:
        return ""

    if not _client:
        logger.warning("preference_learner: OPENAI_API_KEY not configured — skipping context.")
        return ""

    stats = get_feedback_stats()
    top_reasons = stats["top_rejection_reasons"][:5]
    reasons_text = (
        ", ".join(f"'{r}' ({n}x)" for r, n in top_reasons)
        if top_reasons else "none recorded"
    )

    stats_summary = (
        f"Total decisions: {stats['total_decisions']}\n"
        f"Approval rate: {stats['approval_rate']:.1%}\n"
        f"Avg score of approved: {stats['avg_score_approved']}\n"
        f"Avg score of rejected: {stats['avg_score_rejected']}\n"
        f"Implied min score (below which 80%+ are rejections): {stats['implied_min_score']}\n"
        f"Top rejection reasons: {reasons_text}"
    )

    prompt = (
        "You are analysing an influencer matchmaker's past decisions to summarise their preferences.\n\n"
        f"Aggregate statistics:\n{stats_summary}\n\n"
        "Write a single concise paragraph (2-4 sentences) summarising the matchmaker's observed "
        "preferences. Focus on: score thresholds they use, common rejection patterns, and what "
        "predicts approval. Be specific with numbers. "
        f"Start with \"Based on {stats['total_decisions']} past decisions:\". "
        "No markdown, no bullet points — plain prose only."
    )

    try:
        response = _client.chat.completions.create(
            model=_MODEL,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        logger.warning("preference_learner: AI call failed: %s", exc)
        return ""
