import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import load_dotenv

from services.fetchers import (
    MAX_DAYS_SINCE_POST,
    MIN_AVG_ENGAGEMENT,
    InfluencerProfile,
    _calc_engagement,
    _filter_by_tier,
)
from services.hashtag_generator import BrandBrief, generate_hashtags

load_dotenv()
logger = logging.getLogger(__name__)

_apify_token = os.getenv("APIFY_API_TOKEN")
_actor_id = os.getenv("APIFY_ACTOR_ID", "apify~instagram-scraper")

if not _apify_token:
    raise EnvironmentError("APIFY_API_TOKEN is not set. Add it to your .env file.")


def fetch_instagram(brief: BrandBrief) -> list[InfluencerProfile]:
    """
    Two-step fetch:
      1. Generate hashtags from the brief via Claude.
      2. Scrape hashtag pages to discover unique usernames + post data.
      3. Scrape each profile page to get followers, bio, and engagement stats.
    Filters by follower_tier and activity (last post ≤30 days, avg likes >1000).
    Returns empty list if nothing found. Raises httpx.HTTPError on API failure.
    """
    hashtags = generate_hashtags(brief)
    logger.info("Instagram hashtags: %s", hashtags)

    posts = _scrape_hashtag_posts(hashtags, max_results=60)
    if not posts:
        return []

    post_data_by_user = _extract_post_data_by_user(posts)
    logger.info("Found %d unique Instagram authors", len(post_data_by_user))

    profiles = _scrape_profiles(post_data_by_user)
    tier = brief.get_tier_for("instagram")
    tier_filtered = _filter_by_tier(profiles, tier, platform="instagram")

    # Activity filter: last post within 30 days, avg likes > 1000
    active = [
        p for p in tier_filtered
        if _passes_activity_filter(p, post_data_by_user.get(p.username, {}))
    ]
    logger.info(
        "Instagram: %d after tier filter, %d after activity filter",
        len(tier_filtered), len(active),
    )
    return active


def _scrape_hashtag_posts(hashtags: list[str], max_results: int) -> list[dict]:
    """Step 1: scrape hashtag explore pages, return raw post items."""
    direct_urls = [f"https://www.instagram.com/explore/tags/{tag}/" for tag in hashtags]
    body = {"directUrls": direct_urls, "resultsLimit": max_results, "resultsType": "posts"}
    logger.info("Step 1 — scraping hashtag posts")
    response = _apify_post(body)
    items = response.json()
    logger.info("Step 1 returned %d posts", len(items))
    return items


def _extract_post_data_by_user(posts: list[dict]) -> dict[str, dict]:
    """Group post-level captions, engagement, and dates by ownerUsername."""
    data: dict[str, dict] = defaultdict(
        lambda: {
            "captions": [],
            "total_likes": 0,
            "total_comments": 0,
            "post_count": 0,
            "last_posted_at": None,
            "per_post_rates": [],  # (likes+comments)/views per post, where views>0
        }
    )
    for post in posts:
        username = post.get("ownerUsername", "")
        if not username:
            continue
        caption = post.get("caption", "")
        if caption:
            data[username]["captions"].append(caption)

        likes    = int(post.get("likesCount") or 0)
        comments = int(post.get("commentsCount") or 0)
        views    = int(post.get("videoPlayCount") or 0)

        data[username]["total_likes"]    += likes
        data[username]["total_comments"] += comments
        data[username]["post_count"]     += 1

        # Per-post engagement: views-based where available (Reels/videos)
        if views > 0:
            rate = _calc_engagement(likes + comments, views)
            data[username]["per_post_rates"].append(rate)

        # Track the most recent post timestamp
        ts = post.get("timestamp")
        if ts:
            current = data[username]["last_posted_at"]
            if current is None or ts > current:
                data[username]["last_posted_at"] = ts

    return dict(data)


def _passes_activity_filter(profile: InfluencerProfile, post_data: dict) -> bool:
    """
    Returns False if the creator is inactive or has very low avg engagement.
    Criteria: last post ≤ MAX_DAYS_SINCE_POST days ago, avg likes ≥ MIN_AVG_ENGAGEMENT.
    """
    last_posted = post_data.get("last_posted_at")
    if last_posted:
        cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_DAYS_SINCE_POST)
        try:
            last_dt = datetime.fromisoformat(last_posted.replace("Z", "+00:00"))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if last_dt < cutoff:
                logger.info("Skipping %s — last post >%d days ago", profile.username, MAX_DAYS_SINCE_POST)
                return False
        except ValueError:
            pass  # unparseable timestamp — allow through

    post_count = max(post_data.get("post_count", 1), 1)
    avg_likes = post_data.get("total_likes", 0) / post_count
    if avg_likes < MIN_AVG_ENGAGEMENT:
        logger.info(
            "Skipping %s — avg likes %.0f < %d", profile.username, avg_likes, MIN_AVG_ENGAGEMENT
        )
        return False

    return True


def _scrape_profiles(post_data_by_user: dict[str, dict]) -> list[InfluencerProfile]:
    """Step 2: fetch full profile details for each discovered username."""
    profile_urls = [
        f"https://www.instagram.com/{u}/" for u in post_data_by_user
    ]
    body = {"directUrls": profile_urls, "resultsType": "details"}
    logger.info("Step 2 — fetching %d Instagram profiles", len(profile_urls))
    response = _apify_post(body)
    raw = response.json()
    logger.info("Step 2 returned %d profile items", len(raw))

    profiles = []
    for item in raw:
        username = item.get("username") or item.get("ownerUsername", "")
        profile = _build_profile(username, item, post_data_by_user.get(username, {}))
        if profile:
            profiles.append(profile)
    return profiles


def _build_profile(username: str, item: dict, post_data: dict) -> InfluencerProfile | None:
    """Map a raw profile item + collected post data into an InfluencerProfile."""
    if not username:
        return None
    try:
        followers   = int(item.get("followersCount") or 0)
        following   = int(item.get("followingCount") or 0)
        posts_count = int(item.get("postsCount") or 0)

        # Engagement rate: average (likes+comments)/views over last 5 posts.
        # Falls back to (likes+comments)/followers when no view data (image posts).
        per_post_rates = post_data.get("per_post_rates", [])
        if per_post_rates:
            recent = per_post_rates[-5:]
            engagement_rate = round(sum(recent) / len(recent), 4)
        else:
            total_likes    = post_data.get("total_likes", 0)
            total_comments = post_data.get("total_comments", 0)
            num_posts      = max(post_data.get("post_count", 1), 1)
            engagement_rate = _calc_engagement(
                (total_likes + total_comments) / num_posts, max(followers, 1)
            )

        bio = item.get("biography") or ""
        profile_url = item.get("url") or f"https://www.instagram.com/{username}/"
        # Normalise URL to www.instagram.com
        if "instagram.com" in profile_url and "www." not in profile_url:
            profile_url = profile_url.replace("instagram.com", "www.instagram.com")

        return InfluencerProfile(
            username=username,
            full_name=item.get("fullName") or username,
            followers=followers,
            following=following,
            posts_count=posts_count,
            engagement_rate=engagement_rate,
            bio=bio,
            profile_url=profile_url,
            recent_post_captions=post_data.get("captions", [])[:5],
            fetched_at=datetime.now(timezone.utc).isoformat(),
            platform="instagram",
            last_posted_at=post_data.get("last_posted_at"),
        )
    except Exception as exc:
        logger.warning("Could not build Instagram profile for '%s': %s", username, exc)
        return None


def _apify_post(body: dict) -> httpx.Response:
    """POST to the Apify run-sync endpoint and return the response."""
    actor_url_id = _actor_id.replace("/", "~")
    url = (
        f"https://api.apify.com/v2/acts/{actor_url_id}"
        f"/run-sync-get-dataset-items?token={_apify_token}"
    )
    try:
        response = httpx.post(url, json=body, timeout=120)
        response.raise_for_status()
        return response
    except httpx.HTTPError as exc:
        logger.error("Apify request failed: %s", exc)
        raise
