"""
Debug script: prints the raw Apify response so we can see the exact field names.
Usage: python scripts/debug_apify.py
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("APIFY_API_TOKEN")
actor_id = os.getenv("APIFY_ACTOR_ID", "apify~instagram-scraper")
actor_url_id = actor_id.replace("/", "~")

url = f"https://api.apify.com/v2/acts/{actor_url_id}/run-sync-get-dataset-items?token={token}"
### Step 1: get posts from hashtag (discover usernames)
body = {
    "directUrls": ["https://www.instagram.com/explore/tags/cleanbeauty/"],
    "resultsLimit": 3,
    "resultsType": "posts",
}

print(f"POST {url}")
print(f"Body: {json.dumps(body)}\n")

response = httpx.post(url, json=body, timeout=120)
print(f"Status: {response.status_code}\n")

items = response.json()
print(f"Items returned: {len(items)}\n")

if not items:
    print("No items returned.")
    sys.exit(1)

usernames = list({p.get("ownerUsername") for p in items if p.get("ownerUsername")})
print(f"Unique usernames: {usernames}\n")

### Step 2: fetch full profiles for those usernames
profile_urls = [f"https://www.instagram.com/{u}/" for u in usernames[:2]]
body2 = {"directUrls": profile_urls, "resultsType": "details"}

print(f"Step 2 — fetching profiles: {profile_urls}")
r2 = httpx.post(url, json=body2, timeout=120)
print(f"Status: {r2.status_code}")
profiles = r2.json()
print(f"Profiles returned: {len(profiles)}\n")

if profiles:
    first = profiles[0]
    key_fields = ["username", "fullName", "followersCount", "followingCount",
                  "postsCount", "biography", "url"]
    print("=== First profile (key fields) ===")
    print(json.dumps({k: first.get(k) for k in key_fields}, indent=2))
