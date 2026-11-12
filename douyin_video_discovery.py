# coding=utf-8
"""One-request smoke test for TikHub's Douyin video-search API.

This deliberately makes a single billable request. It is only run when the
GitHub Actions workflow is manually dispatched with run_douyin_test=true.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

API_URL = "https://api.tikhub.io/api/v1/douyin/search/fetch_video_search_v2"
TEST_KEYWORD = "东方美学"


def _find_business_items(payload):
    """Find business_data in the common TikHub response wrappers."""
    queue = [payload]
    visited = set()
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            marker = id(current)
            if marker in visited:
                continue
            visited.add(marker)
            items = current.get("business_data")
            if isinstance(items, list):
                return items
            for key in ("data", "result", "search_result"):
                child = current.get(key)
                if isinstance(child, (dict, list)):
                    queue.append(child)
        elif isinstance(current, list):
            for child in current:
                if isinstance(child, (dict, list)):
                    queue.append(child)
    return []


def _normalize_item(entry):
    node = entry if isinstance(entry, dict) else {}
    for _ in range(4):
        if isinstance(node.get("aweme_info"), dict):
            node = node["aweme_info"]
            break
        child = node.get("data")
        if isinstance(child, dict):
            node = child
        else:
            break

    author = node.get("author") or {}
    stats = node.get("statistics") or {}
    aweme_id = str(node.get("aweme_id") or "")
    created = node.get("create_time")
    try:
        created_num = int(created) if created is not None else None
    except (TypeError, ValueError):
        created_num = None

    publish_time = None
    if created_num:
        publish_time = datetime.fromtimestamp(
            created_num, tz=timezone.utc
        ).isoformat()

    share_info = node.get("share_info") or {}
    video_url = (
        node.get("share_url")
        or share_info.get("share_url")
        or (f"https://www.douyin.com/video/{aweme_id}" if aweme_id else None)
    )
    likes = stats.get("digg_count")
    try:
        likes = int(likes) if likes is not None else None
    except (TypeError, ValueError):
        likes = None

    return {
        "video_id": aweme_id or None,
        "title": node.get("desc") or "",
        "author": author.get("nickname") or "",
        "likes": likes,
        "publish_time_utc": publish_time,
        "video_url": video_url,
        "play_count": stats.get("play_count"),
        "comment_count": stats.get("comment_count"),
        "share_count": stats.get("share_count"),
    }


def main():
    api_key = os.environ.get("TIKHUB_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing TIKHUB_API_KEY GitHub Secret.")

    request_body = {
        "keyword": TEST_KEYWORD,
        "cursor": 0,
        "sort_type": "1",
        "publish_time": "7",
        "filter_duration": "0",
        "content_type": "1",
        "search_id": "",
        "backtrace": "",
    }

    response = requests.post(
        API_URL,
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        },
        json=request_body,
        timeout=45,
    )

    try:
        payload = response.json()
    except ValueError:
        raise RuntimeError(
            f"TikHub returned non-JSON response (HTTP {response.status_code})."
        )

    if response.status_code != 200:
        detail = payload.get("message") or payload.get("detail") or str(payload)[:500]
        raise RuntimeError(f"TikHub HTTP {response.status_code}: {detail}")

    api_code = payload.get("code")
    if api_code not in (None, 0, 200, "0", "200"):
        detail = payload.get("message") or payload.get("message_zh") or str(payload)[:500]
        raise RuntimeError(f"TikHub API code {api_code}: {detail}")

    raw_items = _find_business_items(payload)
    videos = [_normalize_item(item) for item in raw_items]
    output = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "TikHub Douyin video-search V2",
        "query": TEST_KEYWORD,
        "filters": {
            "sort": "most_likes",
            "publish_time": "last_7_days",
            "content_type": "video",
        },
        "request_count": 1,
        "returned_items": len(videos),
        "videos_with_like_count": sum(v["likes"] is not None for v in videos),
        "videos_with_publish_time": sum(v["publish_time_utc"] is not None for v in videos),
        "videos_with_author": sum(bool(v["author"]) for v in videos),
        "videos_with_link": sum(bool(v["video_url"]) for v in videos),
        "videos": videos,
        "provider_message": payload.get("message") or payload.get("message_zh"),
        "request_id": payload.get("request_id"),
    }

    destination = Path(
        os.environ.get("DOUYIN_TEST_OUTPUT", "douyin_test_results.json")
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("TikHub single-request test summary")
    print(f"Keyword: {TEST_KEYWORD}")
    print(f"Results returned: {output['returned_items']}")
    print(f"With likes: {output['videos_with_like_count']}")
    print(f"With publish time: {output['videos_with_publish_time']}")
    print(f"With author: {output['videos_with_author']}")
    print(f"With video link: {output['videos_with_link']}")
    print(f"Result file: {destination}")

    if not videos:
        raise RuntimeError(
            "The API request succeeded but returned no business_data videos. "
            "Check the response and try a different keyword only after reviewing cost."
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"❌ TikHub test failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
