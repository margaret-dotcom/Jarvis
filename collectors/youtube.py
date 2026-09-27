"""YouTube search for the content scout.

Usage:
    python -m collectors.youtube --query "tongue tie feeding" --days 14 --max 15

Prints a JSON list to stdout: title, channel, url, views, published, duration_s.
Needs YOUTUBE_API_KEY in .env. Without a key it prints an empty list and a
note on stderr, so the content scout can fall back to web search.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone

import requests

from .config import get_env

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
TIMEOUT = 15


def _iso_duration_to_seconds(value: str) -> int:
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not match:
        return 0
    hours, minutes, seconds = (int(x) if x else 0 for x in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def search(query: str, days: int = 14, max_results: int = 15) -> list[dict]:
    key = get_env("YOUTUBE_API_KEY")
    if not key:
        print("YOUTUBE_API_KEY is not set, returning no results", file=sys.stderr)
        return []

    published_after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "order": "viewCount",
        "publishedAfter": published_after,
        "maxResults": min(max_results, 50),
        "relevanceLanguage": "en",
        "safeSearch": "moderate",
        "key": key,
    }
    resp = requests.get(SEARCH_URL, params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    items = resp.json().get("items", [])
    ids = [item["id"]["videoId"] for item in items if item.get("id", {}).get("videoId")]
    if not ids:
        return []

    stats = requests.get(
        VIDEOS_URL,
        params={"part": "statistics,contentDetails", "id": ",".join(ids), "key": key},
        timeout=TIMEOUT,
    )
    stats.raise_for_status()
    by_id = {v["id"]: v for v in stats.json().get("items", [])}

    results = []
    for item in items:
        video_id = item["id"]["videoId"]
        snippet = item["snippet"]
        extra = by_id.get(video_id, {})
        results.append(
            {
                "title": snippet.get("title", ""),
                "channel": snippet.get("channelTitle", ""),
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "views": int(extra.get("statistics", {}).get("viewCount", 0) or 0),
                "published": snippet.get("publishedAt", ""),
                "duration_s": _iso_duration_to_seconds(
                    extra.get("contentDetails", {}).get("duration", "")
                ),
                "platform": "youtube",
            }
        )
    results.sort(key=lambda r: r["views"], reverse=True)
    return results[:max_results]


def main() -> int:
    parser = argparse.ArgumentParser(description="Search recent YouTube videos.")
    parser.add_argument("--query", required=True)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--max", type=int, default=15)
    args = parser.parse_args()
    try:
        results = search(args.query, args.days, args.max)
    except requests.RequestException as exc:
        print(f"YouTube request failed: {exc}", file=sys.stderr)
        results = []
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
