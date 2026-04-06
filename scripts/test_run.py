"""
Run the full pipeline from the terminal with a hardcoded brand brief.
Usage: python scripts/test_run.py
Add --youtube flag to include YouTube results.
"""
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from db.store import new_job, save_job, update_status
from services.fetcher import fetch_profiles
from services.hashtag_generator import BrandBrief
from services.scorer import score_profiles

if "--youtube-only" in sys.argv:
    platforms = ["youtube"]
elif "--youtube" in sys.argv:
    platforms = ["instagram", "youtube"]
else:
    platforms = ["instagram"]

SAMPLE_BRIEF = BrandBrief(
    brand_name="GlowLab",
    industry="beauty",
    target_age="18-24",
    target_gender="female",
    campaign_goal="awareness",
    follower_tier="micro",
    keywords="clean beauty, sustainable skincare, minimalist",
    red_flags="no alcohol, no fast fashion",
    contact_email="test@example.com",
    platforms=platforms,
)


def main() -> None:
    print(f"\n=== Running search for: {SAMPLE_BRIEF.brand_name} | platforms: {platforms} ===\n")

    job = new_job(SAMPLE_BRIEF)
    save_job(job)

    print("Step 1 — Fetching profiles...")
    profiles = fetch_profiles(SAMPLE_BRIEF)
    print(f"  Profiles fetched: {len(profiles)}")
    if not profiles:
        print("  No profiles returned.")
        update_status(job.job_id, "failed", error="No profiles returned")
        return

    print("\nStep 2 — Scoring profiles with Azure OpenAI...")
    scored = score_profiles(profiles, SAMPLE_BRIEF)
    print(f"  Scored (≥60): {len(scored)}")

    update_status(
        job.job_id, "complete", results=scored,
        completed_at=datetime.now(timezone.utc).isoformat(),
    )

    print("\n=== Top 5 results ===\n")
    for s in scored[:5]:
        print(
            f"  [{s.profile.platform.upper()}] @{s.profile.username} | "
            f"score={s.overall_score} | followers={s.profile.followers:,} | {s.rationale}"
        )

    print(f"\nJob saved: data/jobs/{job.job_id}.json\n")


if __name__ == "__main__":
    main()
