"""
Feedback persistence — dual backend (Supabase / local JSONL file).
"""
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from db.client import supabase
from db.store import load_job, update_status

logger = logging.getLogger(__name__)

FEEDBACK_LOG = Path("data/feedback_log.jsonl")

REJECTION_REASONS = [
    "Select reason...",
    "Wrong audience demographics",
    "Engagement looks fake or inflated",
    "Content aesthetic doesn't fit brand",
    "Too many competitor brand deals",
    "Follower count outside target range",
    "Brand safety concern",
    "Already worked with this creator",
    "Other",
]


# ── Public API ────────────────────────────────────────────────────────────────

def save_feedback(
    job_id: str,
    username: str,
    status: str,
    rejection_reason: str | None,
    notes: str | None,
) -> None:
    """
    Update the influencer's status + feedback fields in the job record.
    Also append one entry to the feedback log (Supabase table or JSONL file).
    """
    job = load_job(job_id)
    if not job:
        logger.error("save_feedback: job %s not found", job_id)
        return

    now = datetime.now(timezone.utc).isoformat()
    scored_influencer = None
    updated_results = []

    for s in job.results:
        if s.profile.username == username:
            scored_influencer = s.model_copy(update={
                "status": status,
                "rejection_reason": rejection_reason,
                "notes": notes,
                "feedback_given_at": now,
            })
            updated_results.append(scored_influencer)
        else:
            updated_results.append(s)

    if scored_influencer is None:
        logger.warning("save_feedback: '%s' not found in job %s", username, job_id)
        return

    update_status(job_id, job.status, results=updated_results)

    log_entry = {
        "timestamp": now,
        "job_id": job_id,
        "username": username,
        "platform": scored_influencer.profile.platform,
        "brand_industry": job.brand_brief.industry,
        "brand_keywords": job.brand_brief.keywords,
        "follower_tier": job.brand_brief.follower_tier,
        "status": status,
        "rejection_reason": rejection_reason,
        "overall_score": scored_influencer.overall_score,
        "audience_match": scored_influencer.audience_match,
        "niche_relevance": scored_influencer.niche_relevance,
        "engagement_quality": scored_influencer.engagement_quality,
        "brand_safety": scored_influencer.brand_safety,
    }

    if supabase:
        _sb_append_log(log_entry, now)
    else:
        _file_append_log(log_entry)

    logger.info("Feedback saved: %s → %s (job=%s)", username, status, job_id)


def load_feedback_log() -> list[dict]:
    """Return all feedback log entries."""
    if supabase:
        return _sb_load_log()
    return _file_load_log()


def get_feedback_stats() -> dict:
    """
    Aggregate stats from the feedback log:
    total_decisions, approval_rate, top_rejection_reasons,
    avg_score_approved, avg_score_rejected, implied_min_score.
    """
    log = load_feedback_log()
    if not log:
        return {
            "total_decisions": 0,
            "approval_rate": 0.0,
            "top_rejection_reasons": [],
            "avg_score_approved": 0.0,
            "avg_score_rejected": 0.0,
            "implied_min_score": 0.0,
        }

    total    = len(log)
    approved = [e for e in log if e["status"] in ("approved", "maybe")]
    rejected = [e for e in log if e["status"] == "rejected"]

    approval_rate = round(len(approved) / total, 3) if total else 0.0

    rejection_reasons = [e["rejection_reason"] for e in rejected if e.get("rejection_reason")]
    top_rejection_reasons = Counter(rejection_reasons).most_common()

    avg_score_approved = (
        round(sum(e["overall_score"] for e in approved) / len(approved), 1) if approved else 0.0
    )
    avg_score_rejected = (
        round(sum(e["overall_score"] for e in rejected) / len(rejected), 1) if rejected else 0.0
    )

    return {
        "total_decisions": total,
        "approval_rate": approval_rate,
        "top_rejection_reasons": top_rejection_reasons,
        "avg_score_approved": avg_score_approved,
        "avg_score_rejected": avg_score_rejected,
        "implied_min_score": _calc_implied_min_score(log),
    }


def archive_feedback_log() -> Path | None:
    """
    Clear the active feedback log (archive it).
    Supabase: deletes all rows from feedback_log table.
    File: renames the JSONL file with a timestamp suffix.
    Returns the archive path (file mode) or None (Supabase mode).
    """
    if supabase:
        supabase.table("feedback_log").delete().neq("id", 0).execute()
        logger.info("Supabase: feedback_log table cleared")
        return None

    if not FEEDBACK_LOG.exists():
        return None
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = FEEDBACK_LOG.parent / f"feedback_log_archive_{ts}.jsonl"
    FEEDBACK_LOG.rename(archive)
    logger.info("File: feedback log archived to %s", archive)
    return archive


# ── Supabase backend ──────────────────────────────────────────────────────────

def _sb_append_log(entry: dict, logged_at: str) -> None:
    supabase.table("feedback_log").insert({"data": entry, "logged_at": logged_at}).execute()


def _sb_load_log() -> list[dict]:
    resp = supabase.table("feedback_log").select("data").order("logged_at").execute()
    return [row["data"] for row in resp.data]


# ── File backend ──────────────────────────────────────────────────────────────

def _file_append_log(entry: dict) -> None:
    FEEDBACK_LOG.parent.mkdir(parents=True, exist_ok=True)
    with FEEDBACK_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _file_load_log() -> list[dict]:
    if not FEEDBACK_LOG.exists():
        return []
    entries = []
    for line in FEEDBACK_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("Malformed feedback log line: %s", line[:80])
    return entries


# ── Shared helpers ────────────────────────────────────────────────────────────

def _calc_implied_min_score(log: list[dict]) -> float:
    """Highest score threshold below which >80% of decisions are rejections."""
    for threshold in range(100, 49, -1):
        below = [e for e in log if e["overall_score"] < threshold]
        if len(below) < 3:
            continue
        rejection_rate = sum(1 for e in below if e["status"] == "rejected") / len(below)
        if rejection_rate > 0.8:
            return float(threshold)
    return 0.0
