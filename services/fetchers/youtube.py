import logging
import os
from datetime import datetime, timedelta, timezone

import httpx
from dotenv import find_dotenv, load_dotenv

from services.fetchers import (
    MAX_DAYS_SINCE_POST,
    MIN_AVG_ENGAGEMENT,
    InfluencerProfile,
    _filter_by_tier,
)
from services.hashtag_generator import BrandBrief

load_dotenv(find_dotenv())
logger = logging.getLogger(__name__)

_YT_BASE = "https://www.googleapis.com/youtube/v3"

# Map display country names → ISO 3166-1 alpha-2 codes for YouTube regionCode param
COUNTRY_CODES: dict[str, str] = {
    "United Kingdom": "GB", "United States": "US", "France": "FR",
    "Germany": "DE", "Italy": "IT", "Spain": "ES", "Australia": "AU",
    "Canada": "CA", "Japan": "JP", "South Korea": "KR", "Brazil": "BR",
    "India": "IN", "Netherlands": "NL", "Sweden": "SE", "Singapore": "SG",
}


def fetch_youtube(brief: BrandBrief) -> list[InfluencerProfile]:
    """
    Fetch YouTube channels matching the brand brief.
    Flow: search videos by keyword + country → unique channels → stats → recent videos.
    Filters by follower_tier and activity (last video ≤30 days, avg views >1000).
    Returns empty list on any failure.
    """
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        raise EnvironmentError("YOUTUBE_API_KEY is not set. Add it to your .env file.")

    try:
        query = _build_search_query(brief)
        # YouTube regionCode accepts only one country; use the first selected one
        region_code = COUNTRY_CODES.get(brief.countries[0], "") if brief.countries else ""
        channel_ids = _search_channels(query, api_key, region_code=region_code)
        if not channel_ids:
            logger.info("YouTube search returned no channels for query: %s", query)
            return []

        channels = _get_channel_details(channel_ids, api_key)
        profiles = []
        for ch in channels:
            video_data = _get_recent_video_data(ch["uploads_playlist"], api_key)
            profile = _map_channel(ch, video_data)
            if profile:
                profiles.append(profile)

        tier = brief.get_tier_for("youtube")
        tier_filtered = _filter_by_tier(profiles, tier, platform="youtube")

        # Activity filter: last video ≤30 days, avg views >1000
        active = [p for p in tier_filtered if _passes_activity_filter(p)]
        logger.info(
            "YouTube: %d after tier filter, %d after activity filter",
            len(tier_filtered), len(active),
        )
        return active

    except httpx.HTTPError as exc:
        logger.error("YouTube API request failed: %s", exc)
        return []


def _build_search_query(brief: BrandBrief) -> str:
    """Build a clean YouTube search query from the brand brief."""
    top_keywords = [k.strip() for k in brief.keywords.split(",") if k.strip()][:3]
    parts = [brief.industry] + top_keywords
    return " ".join(parts)[:100]


def _search_channels(query: str, api_key: str, region_code: str = "") -> list[str]:
    """
    Search YouTube videos matching the query, then extract unique channel IDs.
    Searching by video content finds creators by niche, not just channel name.
    """
    params: dict = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": 50,
        "key": api_key,
    }
    if region_code:
        params["regionCode"] = region_code

    response = httpx.get(f"{_YT_BASE}/search", params=params, timeout=30)
    response.raise_for_status()
    items = response.json().get("items", [])
    seen: set[str] = set()
    channel_ids: list[str] = []
    for item in items:
        cid = item.get("snippet", {}).get("channelId", "")
        if cid and cid not in seen:
            seen.add(cid)
            channel_ids.append(cid)
    return channel_ids


def _get_channel_details(channel_ids: list[str], api_key: str) -> list[dict]:
    """Fetch statistics and metadata for a batch of channel IDs."""
    params = {
        "part": "snippet,statistics,contentDetails",
        "id": ",".join(channel_ids),
        "key": api_key,
    }
    response = httpx.get(f"{_YT_BASE}/channels", params=params, timeout=30)
    response.raise_for_status()
    items = response.json().get("items", [])

    result = []
    for item in items:
        stats   = item.get("statistics", {})
        snippet = item.get("snippet", {})
        uploads = (
            item.get("contentDetails", {})
                .get("relatedPlaylists", {})
                .get("uploads", "")
        )
        result.append({
            "channel_id":       item["id"],
            "username":         snippet.get("customUrl", "").lstrip("@") or item["id"],
            "full_name":        snippet.get("title", ""),
            "bio":              snippet.get("description", ""),
            "subscribers":      int(stats.get("subscriberCount") or 0),
            "view_count":       int(stats.get("viewCount") or 0),
            "video_count":      int(stats.get("videoCount") or 0),
            "uploads_playlist": uploads,
        })
    return result


def _get_recent_video_data(playlist_id: str, api_key: str) -> dict:
    """
    Fetch the 5 most recent uploads for a channel.
    Also fetches per-video statistics to compute (likes+comments)/views engagement.
    Returns dict with 'titles', 'last_published_at', and 'engagement_rate'.
    """
    if not playlist_id:
        return {"titles": [], "last_published_at": None, "engagement_rate": 0.0}
    params = {
        "part": "snippet,contentDetails",
        "playlistId": playlist_id,
        "maxResults": 5,
        "key": api_key,
    }
    try:
        response = httpx.get(f"{_YT_BASE}/playlistItems", params=params, timeout=30)
        response.raise_for_status()
        items = response.json().get("items", [])
        titles = [
            item["snippet"]["title"]
            for item in items
            if item.get("snippet", {}).get("title")
        ]
        last_published_at = None
        if items:
            last_published_at = items[0].get("snippet", {}).get("publishedAt")

        # Collect video IDs to fetch per-video statistics
        video_ids = [
            item.get("contentDetails", {}).get("videoId")
            for item in items
        ]
        video_ids = [v for v in video_ids if v]
        engagement_rate = _calc_per_video_engagement(video_ids, api_key)

        return {"titles": titles, "last_published_at": last_published_at, "engagement_rate": engagement_rate}
    except Exception as exc:
        logger.warning("Could not fetch video data for playlist %s: %s", playlist_id, exc)
        return {"titles": [], "last_published_at": None, "engagement_rate": 0.0}


def _calc_per_video_engagement(video_ids: list[str], api_key: str) -> float:
    """
    Fetch statistics for the given video IDs and return average (likes+comments)/views.
    Clamped to [0, 1]. Returns 0.0 if no usable data.
    """
    if not video_ids:
        return 0.0
    try:
        params = {
            "part": "statistics",
            "id": ",".join(video_ids),
            "key": api_key,
        }
        response = httpx.get(f"{_YT_BASE}/videos", params=params, timeout=30)
        response.raise_for_status()
        stat_items = response.json().get("items", [])
        rates = []
        for item in stat_items:
            stats    = item.get("statistics", {})
            views    = int(stats.get("viewCount") or 0)
            likes    = int(stats.get("likeCount") or 0)
            comments = int(stats.get("commentCount") or 0)
            if views > 0:
                rate = min((likes + comments) / views, 1.0)
                rates.append(rate)
        return round(sum(rates) / len(rates), 4) if rates else 0.0
    except Exception as exc:
        logger.warning("Could not fetch video statistics: %s", exc)
        return 0.0


def _passes_activity_filter(profile: InfluencerProfile) -> bool:
    """
    Returns False if the channel hasn't posted recently or has very low avg views.
    Criteria: last video ≤ MAX_DAYS_SINCE_POST days ago, avg_views ≥ MIN_AVG_ENGAGEMENT.
    """
    if profile.last_posted_at:
        cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_DAYS_SINCE_POST)
        try:
            last_dt = datetime.fromisoformat(profile.last_posted_at.replace("Z", "+00:00"))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if last_dt < cutoff:
                logger.info(
                    "Skipping YT %s — last video >%d days ago", profile.username, MAX_DAYS_SINCE_POST
                )
                return False
        except ValueError:
            pass

    avg = profile.avg_views or 0
    if avg < MIN_AVG_ENGAGEMENT:
        logger.info("Skipping YT %s — avg views %d < %d", profile.username, avg, MIN_AVG_ENGAGEMENT)
        return False

    return True


def _map_channel(ch: dict, video_data: dict) -> InfluencerProfile | None:
    """Map a channel detail dict + recent video data to an InfluencerProfile."""
    try:
        subscribers = ch["subscribers"]
        video_count = ch["video_count"]
        view_count  = ch["view_count"]
        avg_views   = int(view_count / video_count) if video_count > 0 else 0
        # Use per-video (likes+comments)/views averaged over last 5 posts
        engagement_rate = video_data.get("engagement_rate", 0.0)
        username = ch["username"]
        return InfluencerProfile(
            username=username,
            full_name=ch["full_name"],
            followers=subscribers,
            following=0,
            posts_count=video_count,
            engagement_rate=engagement_rate,
            bio=ch["bio"],
            profile_url=f"https://www.youtube.com/@{username}",
            recent_post_captions=video_data["titles"],
            fetched_at=datetime.now(timezone.utc).isoformat(),
            platform="youtube",
            avg_views=avg_views,
            total_videos=video_count,
            last_posted_at=video_data.get("last_published_at"),
        )
    except Exception as exc:
        logger.warning("Could not map YouTube channel '%s': %s", ch.get("username"), exc)
        return None
