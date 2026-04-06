# CLAUDE.md — Influencer Matching System

This is the single source of truth for this project. Read it fully before
writing any code, creating any file, or answering architecture questions.
Every decision about structure, naming, style, and behavior is defined here.

---

## What this system does

A friend works as a brand-influencer matchmaker. When a brand contacts her,
she needs a shortlist of suitable Instagram influencers fast. This web app
automates that process:

1. She (or the brand) fills in a brief via a Streamlit form
2. Claude generates the right hashtags to search from the brief
3. Apify scrapes matching Instagram profiles using those hashtags
4. Claude scores and ranks each profile against the brand brief
5. Results are saved locally and shown in a review dashboard
6. She approves or rejects each influencer, then forwards the list to the brand

Everything runs locally. No cloud deployment for the MVP. No authentication.
No queue. One brand search at a time.

---

## Tech stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | Type hints everywhere |
| Web / UI | Streamlit | Both the form and the dashboard |
| HTTP client | httpx | Used for Apify calls |
| AI | Anthropic SDK (`anthropic`) | Hashtag generation and scoring |
| Storage | JSON files (local) | Simple, no DB setup needed for MVP |
| Config | `.env` + `python-dotenv` | Never hardcode secrets |
| Testing | pytest | Unit tests for pure functions |

No FastAPI for the MVP. Streamlit handles everything in one process.
FastAPI can be added later if the app needs to be hosted or multi-user.

---

## Folder structure

```
influencer-matcher/
├── CLAUDE.md                        ← you are here
├── .env                             ← secrets (never commit)
├── .env.example                     ← committed template
├── .gitignore
├── requirements.txt
│
├── frontend/
│   └── app.py                       ← Streamlit app (all UI lives here)
│
├── services/
│   ├── __init__.py
│   ├── hashtag_generator.py         ← Step 1: brief → hashtag list via Claude
│   ├── fetcher.py                   ← Step 2: hashtags → raw profiles via Apify
│   └── scorer.py                    ← Step 3: profiles + brief → scored list via Claude
│
├── db/
│   ├── __init__.py
│   └── store.py                     ← Read/write JSON files in data/
│
├── data/                            ← Auto-created at runtime, gitignored
│   ├── jobs/                        ← One JSON file per brand search job
│   └── influencers/                 ← Cached influencer profiles
│
├── scripts/
│   └── test_run.py                  ← Run a full search from terminal (no UI)
│
└── tests/
    ├── __init__.py
    ├── test_hashtag_generator.py
    ├── test_scorer.py
    └── test_fetcher.py
```

---

## Data models

All models are Pydantic v2. Define them at the top of the module that uses
them — no separate models/ folder for MVP.

### BrandBrief
```python
from pydantic import BaseModel

class BrandBrief(BaseModel):
    brand_name: str
    industry: str           # e.g. "beauty", "fitness", "fashion"
    target_age: str         # e.g. "18-24", "25-34"
    target_gender: str      # "female" | "male" | "all"
    campaign_goal: str      # "awareness" | "conversion" | "content"
    follower_tier: str      # "nano" | "micro" | "macro"
    keywords: str           # free text: values, aesthetics, themes
    red_flags: str          # free text: what to avoid
    contact_email: str
```

### InfluencerProfile
```python
class InfluencerProfile(BaseModel):
    username: str
    full_name: str
    followers: int
    following: int
    posts_count: int
    engagement_rate: float          # decimal, e.g. 0.034 = 3.4%
    bio: str
    profile_url: str
    recent_post_captions: list[str] # last 5 captions
    fetched_at: str                 # ISO datetime string
```

### ScoredInfluencer
```python
class ScoredInfluencer(BaseModel):
    profile: InfluencerProfile
    audience_match: int         # 0-100
    niche_relevance: int        # 0-100
    engagement_quality: int     # 0-100
    brand_safety: int           # 0-100
    overall_score: int          # weighted: 0.35/0.30/0.20/0.15
    rationale: str              # one sentence
    status: str                 # "pending" | "approved" | "rejected"
```

### SearchJob
```python
class SearchJob(BaseModel):
    job_id: str                     # uuid4
    brand_brief: BrandBrief
    status: str                     # "running" | "complete" | "failed"
    hashtags_used: list[str]
    results: list[ScoredInfluencer]
    created_at: str
    completed_at: str | None
    error: str | None
```

---

## Environment variables

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
APIFY_API_TOKEN=apify_api_...
APIFY_ACTOR_ID=apify~instagram-profile-scraper
```

Load at the top of every module that needs them:
```python
from dotenv import load_dotenv
import os
load_dotenv()
api_key = os.getenv("ANTHROPIC_API_KEY")
```

Never pass secrets as function arguments. Never hardcode them.
If a required env var is missing, raise `EnvironmentError` with a clear message.

---

## Module specifications

### services/hashtag_generator.py

**Purpose:** Take a `BrandBrief`, return a list of 5–8 Instagram hashtags
(without the # symbol) that are likely to surface matching influencers.

**Function signature:**
```python
def generate_hashtags(brief: BrandBrief) -> list[str]:
    """
    Call Claude to generate relevant Instagram hashtags from a brand brief.
    Returns a list of hashtag strings without # prefix.
    Raises ValueError if the response cannot be parsed as a JSON list.
    """
```

**Prompt (use exactly):**
```
You are an Instagram hashtag research expert.

Given this brand brief, generate 5-8 Instagram hashtags that would surface
relevant influencers when searched. Focus on niche-specific tags that
creators in this space actually use — not generic tags like #instagood.

Brand brief:
- Industry: {industry}
- Target audience: {target_age}, {target_gender}
- Campaign goal: {campaign_goal}
- Keywords / values: {keywords}

Return ONLY a JSON array of strings. No # symbol. No markdown. No explanation.
Example: ["veganbeauty", "cleanbeauty", "sustainableskincare"]
```

**Model:** `claude-haiku-4-5` (fast, cheap, sufficient for list generation)

**Parsing:** `json.loads()` on response text. Strip markdown fences and
retry once on failure. Raise `ValueError` if still unparseable.

---

### services/fetcher.py

**Purpose:** Take a list of hashtags and a follower tier, call Apify, return
a filtered list of `InfluencerProfile` objects.

**Function signature:**
```python
def fetch_profiles(
    hashtags: list[str],
    follower_tier: str,
    max_results: int = 60
) -> list[InfluencerProfile]:
    """
    Call Apify Instagram scraper with the given hashtags.
    Filter results by follower_tier before returning.
    Returns empty list (not exception) if Apify returns no results.
    Raises httpx.HTTPError on API failure after logging the error.
    """
```

**Follower tier ranges:**
```python
TIER_RANGES = {
    "nano":  (1_000,    10_000),
    "micro": (10_000,  100_000),
    "macro": (100_000, 10_000_000),
}
```

**Apify call:**
```
POST https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items?token={token}
Body: { "hashtags": [...], "resultsLimit": 60, "scrapeType": "posts" }
Timeout: 120 seconds
```

**Field mapping from Apify response:**
`username`, `fullName`, `followersCount`, `followingCount`, `postsCount`,
`biography`, `profileUrl`. Engagement rate: calculate from likes/comments
if available, else default `0.0`. `fetched_at`: `datetime.utcnow().isoformat()`.

---

### services/scorer.py

**Purpose:** Take profiles + brief, call Claude in batches of 10, return
scored influencers filtered by min score and sorted descending.

**Function signature:**
```python
def score_profiles(
    profiles: list[InfluencerProfile],
    brief: BrandBrief,
    min_score: int = 60
) -> list[ScoredInfluencer]:
    """
    Score each profile against the brand brief using Claude.
    Batches of 10. Filters overall_score < min_score. Sorts descending.
    On JSON parse failure for a batch: log warning and skip that batch.
    """
```

**Scoring prompt (use exactly):**
```
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
  Benchmarks — nano >5%, micro >3%, macro >1.5%
- brand_safety (0-100): Any red flags in bio or recent posts? 100 = clean.
- overall_score = audience_match*0.35 + niche_relevance*0.30 +
                  engagement_quality*0.20 + brand_safety*0.15
- rationale: one sentence, top reason for the score

Return ONLY a valid JSON array. No markdown. No text outside the JSON.
Include all profiles regardless of score — filtering happens in Python.

JSON shape per item:
{"username":"","audience_match":0,"niche_relevance":0,
 "engagement_quality":0,"brand_safety":0,"overall_score":0,"rationale":""}

Profiles:
{profiles_json}
```

**Model:** `claude-sonnet-4-6` (better reasoning for nuanced judgment)

---

### db/store.py

**Purpose:** All file I/O for the app. Reads and writes `SearchJob` objects
as JSON in `data/jobs/`. No other module writes files directly.

```python
def save_job(job: SearchJob) -> None:
    """Write job to data/jobs/{job_id}.json. Creates directory if needed."""

def load_job(job_id: str) -> SearchJob | None:
    """Load and return job, or None if file not found."""

def list_jobs() -> list[SearchJob]:
    """Return all jobs sorted by created_at descending."""

def update_status(job_id: str, status: str, **kwargs) -> None:
    """Load, update status + any kwargs (error, completed_at, results), save."""
```

Use `model.model_dump()` to serialize and `SearchJob.model_validate(data)`
to deserialize. Pretty-print JSON with `indent=2`.

---

### frontend/app.py

**Purpose:** Streamlit app with two tabs — new search form and results
dashboard.

**Tab 1 — New search:**
- Input widgets for all BrandBrief fields (dropdowns for structured fields)
- "Run search" button that calls the pipeline in sequence:
  1. `generate_hashtags` → display hashtags with `st.info()`
  2. `fetch_profiles` → wrap in `st.spinner("Fetching profiles...")`
  3. `score_profiles` → wrap in `st.spinner("Scoring influencers...")`
  4. `save_job` → `st.success("Done! Switch to Results tab.")`
- `st.error()` on any exception — never let the app crash

**Tab 2 — Results:**
- Job selector: dropdown of past jobs (brand name + date)
- Results table: username, followers, engagement rate, overall score,
  rationale, status — use `st.data_editor` for inline status editing
- Filter buttons: All / Pending / Approved / Rejected
- "Export approved" button: `st.download_button` with CSV

---

### scripts/test_run.py

One-off script to run the full pipeline from the terminal with a hardcoded
brand brief. Prints results to stdout. Used for development without opening
Streamlit. No arguments needed — just `python scripts/test_run.py`.

---

## Error handling rules

| Situation | Behavior |
|---|---|
| Apify timeout (>120s) | Show "Scraping timed out — try fewer hashtags" |
| Apify returns 0 results | Show "No profiles found — try different keywords" |
| Claude JSON parse failure | Skip that batch, log warning, continue |
| Missing env variable | Raise `EnvironmentError` with clear message at startup |
| File not found in store.py | Return `None`, never raise |
| Any exception in Streamlit pipeline | Catch and show via `st.error()` |

---

## Code style rules

- Type hints on all function signatures
- Pydantic v2: use `model_dump()` and `model_validate()`
- `httpx` for HTTP, not `requests`
- `logging.getLogger(__name__)` in each module, not `print()`
- Max 40 lines per function — split into helpers if longer
- Docstring on every public function
- `services/` modules are pure: input → output, no file writes, no Streamlit calls
- File I/O only in `db/store.py`
- UI only in `frontend/app.py`

---

## Build order

Build and test in this exact order — each step is independently runnable:

1. `services/hashtag_generator.py` + `tests/test_hashtag_generator.py`
2. `services/fetcher.py` + `tests/test_fetcher.py`
3. `services/scorer.py` + `tests/test_scorer.py`
4. `db/store.py`
5. `scripts/test_run.py` — wires 1–4 together, confirms end-to-end works
6. `frontend/app.py` — build last, once pipeline is proven

---

## Key decisions and why

| Decision | Reason |
|---|---|
| Streamlit not FastAPI+React | Fastest working UI; matches existing project pattern |
| JSON files not Cosmos DB | Zero setup, zero cost, enough for MVP volume |
| `claude-haiku-4-5` for hashtags | Simple task, no heavy reasoning needed |
| `claude-sonnet-4-6` for scoring | Nuanced judgment, quality matters here |
| Sync httpx not async | Streamlit threading makes full async tricky; sync is simpler |
| Batch size 10 for scoring | Balances context window vs number of API calls |
| No email sending in MVP | Show results in UI; email is v2 |

---

## What NOT to build yet (v2 scope)

- TikTok support
- Email delivery of shortlist
- Cloud hosting or deployment
- Multi-user / authentication
- Scheduled DB refresh
- FastAPI backend
- Docker / CI/CD
- Competitor deal detection
- Brand safety monitoring webhooks
