# coding: utf-8
"""Xiaohongshu trend discovery bot."""
import requests
API_URL = "https://api.tikhub.io/api/v1/xiaohongshu/app_v2/search_notes"

import csv
import html
import json
import os
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-flash"
MAX_PER_CATEGORY = 20
MIN_LIKES_EXCLUSIVE = 5000
MAX_PAGES_PER_KEYWORD = 2
REQUEST_DELAY_SECONDS = 0.8
AI_BATCH_SIZE = 12
OUTPUT_DIR = Path(os.environ.get("XHS_OUTPUT_DIR", "output/xiaohongshu_full_discovery"))

CATEGORY_KEYWORDS = {
    "人物突然走红": ["素人走红", "普通人爆火"],
    "网红": ["小红书博主", "博主分享"],
    "明星": ["明星同款", "明星穿搭"],
    "社会现象": ["社会现象", "生活方式趋势"],
    "时尚潮流": ["时尚趋势", "潮流趋势"],
    "东方美学": ["东方美学", "新中式美学"],
    "摄影美妆": ["妆容教程", "拍照技巧"],
    "穿搭": ["日常穿搭", "通勤穿搭"],
    "装修": ["装修设计", "家居装修"],
}
AI_SCORE_WEIGHTS = {
    "story_score": 0.25,
    "visual_potential_score": 0.20,
    "global_appeal_score": 0.25,
    "originality_score": 0.15,
    "discussion_score": 0.15,
}


def parse_cn_count(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace(",", "").replace("+", "")
    multiplier = 1
    if text.endswith("万"):
        multiplier, text = 10000, text[:-1]
    elif text.endswith("亿"):
        multiplier, text = 100000000, text[:-1]
    try:
        return int(float(text) * multiplier)
    except (TypeError, ValueError):
        return None


def parse_publish_time(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) or str(value).strip().isdigit():
        try:
            stamp = float(value)
            if stamp > 10000000000:
                stamp /= 1000
            return datetime.fromtimestamp(stamp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def walk_dicts(root):
    queue = deque([root])
    seen = set()
    while queue:
        value = queue.popleft()
        if isinstance(value, dict):
            if id(value) in seen:
                continue
            seen.add(id(value))
            yield value
            queue.extend(v for v in value.values() if isinstance(v, (dict, list)))
        elif isinstance(value, list):
            queue.extend(v for v in value if isinstance(v, (dict, list)))


def extract_items(payload):
    for node in walk_dicts(payload):
        for key in ("items", "notes", "note_list", "list"):
            rows = node.get(key)
            if isinstance(rows, list) and rows:
                return [x for x in rows if isinstance(x, dict)]
    return []


def extract_pagination(payload):
    search_id = search_session_id = ""
    has_more = None
    for node in walk_dicts(payload):
        search_id = search_id or str(node.get("search_id") or "")
        search_session_id = search_session_id or str(node.get("search_session_id") or "")
        if node.get("has_more") is not None:
            has_more = node.get("has_more")
    if isinstance(has_more, str):
        has_more = has_more.lower() in ("1", "true", "yes")
    elif has_more is not None:
        has_more = bool(has_more)
    return search_id, search_session_id, has_more


def request_page(session, api_key, keyword, page, search_id="", search_session_id=""):
    params = {
        "keyword": keyword,
        "page": page,
        "sort_type": "popularity_descending",
        "note_type": "不限",
        "time_filter": "一周内",
        "source": "explore_feed",
        "ai_mode": 0,
    }
    if search_id:
        params["search_id"] = search_id
    if search_session_id:
        params["search_session_id"] = search_session_id
    response = session.get(
        API_URL,
        headers={"Authorization": "Bearer " + api_key},
        params=params,
        timeout=60,
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("TikHub returned a non-JSON response") from exc
    if response.status_code != 200:
        raise RuntimeError(f"TikHub HTTP {response.status_code}: {str(payload)[:350]}")
    code = payload.get("code") if isinstance(payload, dict) else None
    if code not in (None, 0, 200, "0", "200"):
        raise RuntimeError(f"TikHub API code {code}: {str(payload)[:350]}")
    rows = extract_items(payload)
    next_search_id, next_session_id, has_more = extract_pagination(payload)
    return rows, next_search_id or search_id, next_session_id or search_session_id, has_more


def normalize_note(entry, category, keyword, captured_at):
    card = entry.get("note_card") or entry.get("note") or entry.get("note_info") or entry
    if not isinstance(card, dict):
        card = entry
    user = card.get("user") or card.get("author") or {}
    interact = card.get("interact_info") or card.get("note_interact_info") or {}
    note_id = str(entry.get("id") or entry.get("note_id") or card.get("id") or card.get("note_id") or "").strip()
    title = str(card.get("display_title") or card.get("title") or entry.get("title") or "").strip()
    desc = str(card.get("desc") or card.get("content") or entry.get("desc") or "").strip()
    author = str(user.get("nickname") or user.get("nick_name") or user.get("name") or "").strip()
    likes = parse_cn_count(interact.get("liked_count") or interact.get("like_count"))
    collects = parse_cn_count(interact.get("collected_count") or interact.get("collecount") or interact.get("collect_count"))
    comments = parse_cn_count(interact.get("comment_count"))
    shares = parse_cn_count(interact.get("shared_count") or interact.get("share_count"))
    raw_time = card.get("time") or card.get("publish_time") or entry.get("time") or entry.get("publish_time")
    published = parse_publish_time(raw_time)
    token = str(entry.get("xsec_token") or card.get("xsec_token") or "").strip()
    note_url = "https://www.xiaohongshu.com/explore/" + note_id
    if token:
        note_url += "?" + urlencode({"xsec_token": token})
    return {
        "note_id": note_id, "title": title, "desc": desc, "author": author,
        "likes": likes, "collects": collects, "comments": comments, "shares": shares,
        "publish_time_utc": published.isoformat() if published else "",
        "publish_timestamp": published.timestamp() if published else None,
        "note_url": note_url if note_id else "", "category": category,
        "search_keyword": keyword, "captured_at_utc": captured_at,
        "source": "TikHub Xiaohongshu App V2 search_notes",
    }
