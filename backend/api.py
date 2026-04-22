"""
FastAPI REST backend — powers the HTML frontend (frontend/index.html).
Run with: uvicorn backend.api:app --reload --port 8000

The Streamlit frontend (frontend/app.py) continues to work independently.
Both share the same db/ layer and Supabase / local JSON storage.
"""
import logging
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from db.feedback import REJECTION_REASONS, get_feedback_stats, save_feedback
from db.store import list_jobs, load_job, new_job, save_job, update_status
from services.hashtag_generator import BrandBrief

app = FastAPI(title="Influencer Match API", docs_url="/api/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_FRONTEND = Path(__file__).parent.parent / "frontend" / "index.html"


# ── Auth ───────────────────────────────────────────────────────────────────────

class AuthRequest(BaseModel):
    password: str


@app.post("/api/auth")
def api_auth(req: AuthRequest):
    correct = os.getenv("APP_PASSWORD", "")
    if not correct or req.password == correct:
        return {"ok": True}
    raise HTTPException(status_code=401, detail="Wrong password")


# ── HTML frontend ──────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def serve_index():
    return FileResponse(_FRONTEND)


# In-memory progress log and cancellation flags per job_id
_progress: dict[str, list[str]] = {}
_cancelled: set[str] = set()

def _log(job_id: str, msg: str) -> None:
    _progress.setdefault(job_id, []).append(msg)
    logger.info("[%s] %s", job_id[:8], msg)

def _is_cancelled(job_id: str) -> bool:
    return job_id in _cancelled


# ── Jobs ───────────────────────────────────────────────────────────────────────

@app.get("/api/jobs")
def api_list_jobs():
    return [
        {
            "job_id": j.job_id,
            "brand_name": j.brand_brief.brand_name,
            "industry": j.brand_brief.industry,
            "platforms": getattr(j.brand_brief, "platforms", ["instagram"]),
            "status": j.status,
            "created_at": j.created_at,
            "completed_at": j.completed_at,
            "result_count": len(j.results),
            "approved": sum(1 for r in j.results if r.status == "approved"),
            "rejected": sum(1 for r in j.results if r.status == "rejected"),
            "pending": sum(1 for r in j.results if r.status == "pending"),
        }
        for j in list_jobs()
    ]


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: str):
    job = load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    data = job.model_dump()
    data["progress"] = _progress.get(job_id, [])
    return data


@app.get("/api/jobs/{job_id}/progress")
def api_job_progress(job_id: str):
    return {"progress": _progress.get(job_id, [])}


@app.post("/api/jobs/{job_id}/cancel")
def api_cancel_job(job_id: str):
    job = load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "running":
        raise HTTPException(status_code=400, detail="Job is not running")
    _cancelled.add(job_id)
    _log(job_id, "Cancellation requested...")
    return {"ok": True}


class BriefRequest(BaseModel):
    brand_name: str
    industry: str
    target_age: str
    target_gender: str
    campaign_goal: str
    follower_tier: str
    keywords: str
    red_flags: str
    contact_email: str
    platforms: list[str] = ["instagram"]
    countries: list[str] = []
    platform_tiers: dict[str, str] = {}


@app.post("/api/jobs", status_code=202)
def api_create_job(req: BriefRequest):
    brief = BrandBrief(**req.model_dump())
    job = new_job(brief)
    save_job(job)
    t = threading.Thread(target=_run_search, args=(job.job_id, brief), daemon=True)
    t.start()
    return {"job_id": job.job_id}


def _run_search(job_id: str, brief: BrandBrief) -> None:
    use_mock = os.getenv("USE_MOCK", "false").lower() == "true"
    platforms = getattr(brief, "platforms", ["instagram"])
    try:
        if use_mock:
            from services.mocks import fetch_profiles, score_profiles
        else:
            from services.fetcher import fetch_profiles
            from services.scorer import score_profiles

        _log(job_id, "Generating hashtags from brief...")
        profiles = []

        if not use_mock:
            from services.fetchers.instagram import fetch_instagram
            from services.fetchers.youtube import fetch_youtube

            if "instagram" in platforms:
                if _is_cancelled(job_id): raise InterruptedError()
                _log(job_id, "Scraping Instagram hashtag pages (this takes ~60s)...")
                ig = fetch_instagram(brief)
                _log(job_id, f"Found {len(ig)} Instagram profiles after filters.")
                profiles.extend(ig)

            if "youtube" in platforms:
                if _is_cancelled(job_id): raise InterruptedError()
                _log(job_id, "Searching YouTube channels...")
                yt = fetch_youtube(brief)
                _log(job_id, f"Found {len(yt)} YouTube channels after filters.")
                profiles.extend(yt)
        else:
            profiles = fetch_profiles(brief)

        if _is_cancelled(job_id): raise InterruptedError()

        if not profiles:
            _log(job_id, "No profiles matched — try broader keywords or a different tier.")
            update_status(job_id, "complete", results=[],
                          completed_at=datetime.now(timezone.utc).isoformat())
            return

        _log(job_id, f"Scoring {len(profiles)} profiles with AI (batches of 10)...")
        results = score_profiles(profiles, brief)
        _log(job_id, f"Done — {len(results)} influencers scored above threshold.")
        update_status(
            job_id, "complete",
            results=results,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
    except InterruptedError:
        _log(job_id, "Search cancelled.")
        update_status(job_id, "cancelled",
                      completed_at=datetime.now(timezone.utc).isoformat())
        _cancelled.discard(job_id)
    except Exception as exc:
        _log(job_id, f"Error: {exc}")
        update_status(
            job_id, "failed",
            error=str(exc),
            completed_at=datetime.now(timezone.utc).isoformat(),
        )


# ── Feedback ───────────────────────────────────────────────────────────────────

class FeedbackRequest(BaseModel):
    status: str
    rejection_reason: Optional[str] = None
    notes: Optional[str] = None


@app.post("/api/jobs/{job_id}/feedback/{username}")
def api_save_feedback(job_id: str, username: str, req: FeedbackRequest):
    save_feedback(job_id, username, req.status, req.rejection_reason, req.notes)
    return {"ok": True}


@app.get("/api/stats")
def api_get_stats():
    return get_feedback_stats()


@app.get("/api/rejection-reasons")
def api_rejection_reasons():
    return REJECTION_REASONS
