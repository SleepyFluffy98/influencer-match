"""
Job storage — dual backend.
  • Supabase (cloud): used when SUPABASE_URL + SUPABASE_ANON_KEY are set.
  • JSON files (local): used otherwise (local dev / mock mode).
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel

from db.client import supabase
from services.fetcher import InfluencerProfile
from services.hashtag_generator import BrandBrief
from services.scorer import ScoredInfluencer

logger = logging.getLogger(__name__)

JOBS_DIR = Path("data/jobs")


class SearchJob(BaseModel):
    job_id: str
    brand_brief: BrandBrief
    status: str  # "running" | "complete" | "failed"
    hashtags_used: list[str]
    results: list[ScoredInfluencer]
    created_at: str
    completed_at: str | None = None
    error: str | None = None


def new_job(brief: BrandBrief) -> SearchJob:
    """Create a new SearchJob with status 'running' and return it (does not save)."""
    return SearchJob(
        job_id=str(uuid4()),
        brand_brief=brief,
        status="running",
        hashtags_used=[],
        results=[],
        created_at=datetime.now(timezone.utc).isoformat(),
    )


# ── Public API ────────────────────────────────────────────────────────────────

def save_job(job: SearchJob) -> None:
    """Persist a job. Uses Supabase if configured, else writes a local JSON file."""
    if supabase:
        _sb_save_job(job)
    else:
        _file_save_job(job)


def load_job(job_id: str) -> SearchJob | None:
    """Load and return a job by ID, or None if not found."""
    if supabase:
        return _sb_load_job(job_id)
    return _file_load_job(job_id)


def list_jobs() -> list[SearchJob]:
    """Return all jobs sorted by created_at descending."""
    if supabase:
        return _sb_list_jobs()
    return _file_list_jobs()


def update_status(job_id: str, status: str, **kwargs) -> None:
    """Load job, update status + any extra fields (results, error, completed_at), save."""
    job = load_job(job_id)
    if job is None:
        logger.error("update_status: job %s not found", job_id)
        return
    save_job(job.model_copy(update={"status": status, **kwargs}))


# ── Supabase backend ──────────────────────────────────────────────────────────

def _sb_save_job(job: SearchJob) -> None:
    data = json.loads(job.model_dump_json())
    supabase.table("jobs").upsert(
        {"job_id": job.job_id, "data": data, "created_at": job.created_at}
    ).execute()
    logger.info("Supabase: saved job %s (%s)", job.job_id, job.status)


def _sb_load_job(job_id: str) -> SearchJob | None:
    resp = supabase.table("jobs").select("data").eq("job_id", job_id).execute()
    if not resp.data:
        return None
    try:
        return SearchJob.model_validate(resp.data[0]["data"])
    except Exception as exc:
        logger.warning("Supabase: could not parse job %s: %s", job_id, exc)
        return None


def _sb_list_jobs() -> list[SearchJob]:
    resp = supabase.table("jobs").select("data").order("created_at", desc=True).execute()
    jobs = []
    for row in resp.data:
        try:
            jobs.append(SearchJob.model_validate(row["data"]))
        except Exception as exc:
            logger.warning("Supabase: skipping malformed job row: %s", exc)
    return jobs


# ── File backend ──────────────────────────────────────────────────────────────

def _file_save_job(job: SearchJob) -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    path = JOBS_DIR / f"{job.job_id}.json"
    path.write_text(job.model_dump_json(indent=2), encoding="utf-8")
    logger.info("File: saved job %s (%s)", job.job_id, job.status)


def _file_load_job(job_id: str) -> SearchJob | None:
    path = JOBS_DIR / f"{job_id}.json"
    if not path.exists():
        return None
    try:
        return SearchJob.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:
        logger.warning("File: could not parse job %s: %s", job_id, exc)
        return None


def _file_list_jobs() -> list[SearchJob]:
    if not JOBS_DIR.exists():
        return []
    jobs = []
    for path in JOBS_DIR.glob("*.json"):
        try:
            jobs.append(SearchJob.model_validate(json.loads(path.read_text(encoding="utf-8"))))
        except Exception as exc:
            logger.warning("File: could not load %s: %s", path.name, exc)
    return sorted(jobs, key=lambda j: j.created_at, reverse=True)
