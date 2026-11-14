# coding=utf-8
"""Daily high-like Douyin trend discovery, derived from the keyword-trend robot.

Collects videos published on the current China calendar day, requires >10,000 likes,
keeps China-related topics, and excludes political propaganda before report generation.
"""
import csv
import html
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

import requests

API_URL = "https://api.tikhub.io/api/v1/douyin/search/fetch_video_search_v2"
MAX_PER_CATEGORY = 20
MIN_LIKES_EXCLUSIVE = 10000  # Keep only videos with strictly more than 10,000 likes.
MAX_PAGES_PER_KEYWORD = 2
REQUEST_DELAY_SECONDS = 0.8
OUTPUT_DIR = Path(os.environ.get("DOUYIN_DAILY_VIRAL_OUTPUT_DIR", "output/douyin_daily_viral"))

CATEGORY_KEYWORDS = {
    "时事": ["中国新闻", "热点新闻"],
    "人物突然走红": ["素人走红", "普通人走红"],
    "网红": ["网红", "网络红人"],
    "明星": ["明星", "娱乐新闻"],
    "社会现象": ["社会现象", "社会热点"],
    "时尚潮流": ["时尚穿搭", "潮流趋势"],
    "东方美学": ["东方美学", "中式美学"],
    "摄影美妆": ["妆容", "拍照技巧"],
}


DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-flash"
AI_BATCH_SIZE = 12
AI_SCORE_WEIGHTS = {
    "story_score": 0.25,
    "visual_potential_score": 0.20,
    "global_appeal_score": 0.25,
    "originality_score": 0.15,
    "discussion_score": 0.15,
}


def _parse_deepseek_json(content):
    content = (content or "").strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1]
        if content.endswith("```"):
            content = content[:-3].strip()
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < start:
        raise ValueError("DeepSeek response does not contain a JSON object")
    return json.loads(content[start:end + 1])


def analyze_douyin_videos_with_deepseek(videos):
    """Score unique videos once; all claims must be grounded in supplied metadata."""
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        print("⚠️ 未配置 DEEPSEEK_API_KEY，保留原始采集报告，不执行 AI 评分")
        return {"calls": 0, "analyzed": 0, "failed_batches": 0, "status": "missing_api_key"}

    session = requests.Session()
    total_calls = 0
    analyzed = 0
    failed_batches = 0

    for batch_no, start in enumerate(range(0, len(videos), AI_BATCH_SIZE), 1):
        batch = videos[start:start + AI_BATCH_SIZE]
        payload = []
        for index, video in enumerate(batch):
            payload.append({
                "id": index,
                "video_id": video.get("video_id"),
                "title_or_caption": video.get("title", ""),
                "author": video.get("author", ""),
                "categories": video.get("categories", []),
                "likes": video.get("likes"),
                "play_count": video.get("play_count"),
                "comment_count": video.get("comment_count"),
                "share_count": video.get("share_count"),
                "publish_time_utc": video.get("publish_time_utc", ""),
            })

        system_prompt = (
            "你是面向美国、英国、加拿大、澳大利亚等英语受众的短视频选题编辑。"
            "你正在评估中国抖音视频是否适合改编为 TikTok/Instagram 英语内容。"
            "只能依据输入里的标题/文案、作者、类别、发布时间和互动数字判断。"
            "你没有观看视频，也没有字幕、画面或评论正文，因此绝对不能声称看到了画面、人物行为或视频情节。"
            "视觉潜力只能根据标题中明确出现的视觉线索谨慎推断；证据不足时降低该分并降低 confidence。"
            "点赞数是当前采集时的累计值，不代表当天新增点赞速度，也不能单凭点赞数证明正在爆红。"
            "不要补充外部背景，不要编造人物身份、故事、事件因果、播放量或趋势。"
            "必须为每条输入生成评分，不得遗漏。"
            "五项评分均为0到100整数：story_score（故事性/叙事空间）、"
            "visual_potential_score（视觉表达潜力，仅按文字线索推断）、"
            "global_appeal_score（英语受众理解和兴趣）、"
            "originality_score（新鲜感/文化独特性）、"
            "discussion_score（讨论、共鸣或评论空间）。"
            "不允许把点赞量直接等同于内容质量；分数衡量的是内容作为海外短视频选题的潜力。"
            "地域过滤规则：只保留核心主题属于中国国内的趋势。topic_region 必须是中国国内、国外、不明确之一。核心事件/人物/潮流发生在中国国内，或主题本身是中国社会、人物、娱乐、网红、网络文化，选中国国内；核心主题发生在中国以外任何国家或地区（包括韩国、日本、美国、欧洲及其他地区），包括当地新闻、社会事件、外国明星/网红/娱乐及海外潮流，选国外；标题信息不足以判断地域，选不明确。只是顺带提及外国或作比较，而主体仍是中国内容的，选中国国内。最终仅保留 topic_region=中国国内，国外和不明确均排除。不要因单个国家关键词误删。"
            "政治宣传判断：political_promotion 必须为“是”“否”“不确定”之一。只有以政治人物/政党/政府机构形象塑造、政治口号或意识形态动员、政治功绩宣传、政治宣传片或政治活动号召为主要目的时才判“是”；客观新闻报道、政策解读、民生服务、普通社会事件、历史文化知识，不因出现政府或政策字样就自动判“是”。证据不足时判“不确定”。日报只保留“否”，“是”和“不确定”均排除。"
            "subject_explainer 用简体中文写1句，控制在20至40个汉字，必须直接说清人物是谁/做什么，或事件具体发生了什么，或潮流是什么。"
            "只写核心事实，不写背景铺陈、评价、重复标题；人物职业/身份和事件经过只能依据标题/描述明确提供的信息。"
            "资料不足时简短写‘标题未说明具体身份/经过，需核实’，不要猜测，也不要写长段免责声明。"
            "why_attention 用简体中文写1句，最多25个汉字，概括最直接的关注点；不能把点赞数当成走红速度。"
            "reason 用简体中文写评分依据，最多45个汉字；english_hook 用自然简洁英文写一个短口播开场；"
            "content_angle 用简体中文写短语或短句，最多25个汉字；caveat 最多25个汉字；confidence 只能是 high、medium、low。"
            "输出必须精炼，不要使用‘值得关注的是’‘从某种程度上’等铺垫，不要重复标题。"
            "严格返回 JSON："
            "{\"items\":[{\"id\":0,\"story_score\":0,\"visual_potential_score\":0,"
            "\"global_appeal_score\":0,\"originality_score\":0,\"discussion_score\":0,"
            "\"subject_explainer\":\"\",\"why_attention\":\"\",\"reason\":\"\","
            "\"english_hook\":\"\",\"content_angle\":\"\",\"caveat\":\"\",\"topic_region\":\"不明确\",\"confidence\":\"low\",\"political_promotion\":\"不确定\"}]}"
        )
        user_prompt = (
            "请分析以下抖音视频元数据。请特别考虑英语受众是否能快速理解、"
            "是否有跨文化反差/视觉记忆点/清楚的故事冲突，但没有证据时不要臆测。\n"
            + json.dumps(payload, ensure_ascii=False)
        )

        try:
            response = session.post(
                DEEPSEEK_API_URL,
                headers={
                    "Authorization": "Bearer " + api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "model": DEEPSEEK_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "thinking": {"type": "disabled"},
                    "temperature": 0.2,
                    "max_tokens": 8192,
                    "response_format": {"type": "json_object"},
                },
                timeout=120,
            )
            total_calls += 1
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            parsed = _parse_deepseek_json(content)
            items = parsed.get("items", []) if isinstance(parsed, dict) else []
            by_id = {}
            for result in items:
                try:
                    by_id[int(result["id"])] = result
                except (KeyError, TypeError, ValueError):
                    continue

            for index, video in enumerate(batch):
                ai = by_id.get(index)
                if ai is None:
                    video["ai_analysis_status"] = "missing_result"
                    continue
                scores = {}
                for field in AI_SCORE_WEIGHTS:
                    try:
                        scores[field] = max(0, min(100, int(ai.get(field, 0))))
                    except (TypeError, ValueError):
                        scores[field] = 0
                overall = round(sum(scores[k] * weight for k, weight in AI_SCORE_WEIGHTS.items()))
                video.update(scores)
                video["ai_score"] = overall
                video["subject_explainer"] = str(ai.get("subject_explainer", ""))[:700]
                video["why_attention"] = str(ai.get("why_attention", ""))[:400]
                video["ai_reason"] = str(ai.get("reason", ""))[:600]
                video["english_hook"] = str(ai.get("english_hook", ""))[:300]
                video["content_angle"] = str(ai.get("content_angle", ""))[:500]
                video["ai_caveat"] = str(ai.get("caveat", ""))[:400]
                region = str(ai.get("topic_region", "不明确")).strip()
                video["topic_region"] = region if region in ("中国国内", "国外", "不明确") else "不明确"
                confidence = str(ai.get("confidence", "low")).lower()
                video["ai_confidence"] = confidence if confidence in ("high", "medium", "low") else "low"
                political_status = str(ai.get("political_promotion", "不确定")).strip()
                video["political_promotion"] = political_status if political_status in ("是", "否", "不确定") else "不确定"
                video["ai_analysis_status"] = "completed"
                video["ai_model"] = DEEPSEEK_MODEL
                analyzed += 1

            print(f"🤖 DeepSeek 已完成第 {batch_no} 批：{len(batch)} 条输入，成功标注 {sum(1 for v in batch if v.get('ai_analysis_status') == 'completed')} 条")
        except Exception as exc:
            failed_batches += 1
            print(f"⚠️ DeepSeek 第 {batch_no} 批分析失败，保留原始数据：{exc}", file=sys.stderr)
            for video in batch:
                video.setdefault("ai_analysis_status", "failed")

    return {
        "calls": total_calls,
        "analyzed": analyzed,
        "failed_batches": failed_batches,
        "status": "completed" if analyzed == len(videos) else "partial",
        "model": DEEPSEEK_MODEL,
        "batch_size": AI_BATCH_SIZE,
        "score_weights": AI_SCORE_WEIGHTS,
    }


def get_nested_business_data(payload):
    """Locate TikHub's business_data list without assuming wrapper depth."""
    queue = [payload]
    seen = set()
    while queue:
        current = queue.pop(0)
        if isinstance(current, dict):
            marker = id(current)
            if marker in seen:
                continue
            seen.add(marker)
            rows = current.get("business_data")
            if isinstance(rows, list):
                return rows, current
            for value in current.values():
                if isinstance(value, (dict, list)):
                    queue.append(value)
        elif isinstance(current, list):
            for value in current:
                if isinstance(value, (dict, list)):
                    queue.append(value)
    return [], {}


def _unwrap_aweme(entry):
    node = entry if isinstance(entry, dict) else {}
    for _ in range(6):
        if isinstance(node.get("aweme_info"), dict):
            return node["aweme_info"]
        next_node = node.get("data")
        if isinstance(next_node, dict):
            node = next_node
        else:
            break
    return node


def normalize_video(entry, category, keyword, captured_at):
    node = _unwrap_aweme(entry)
    author = node.get("author") or {}
    stats = node.get("statistics") or {}
    aweme_id = str(node.get("aweme_id") or "").strip()

    try:
        created = int(node.get("create_time"))
    except (TypeError, ValueError):
        created = None

    published = (
        datetime.fromtimestamp(created, timezone.utc)
        if created else None
    )

    share_info = node.get("share_info") or {}
    share_url = (
        node.get("share_url")
        or share_info.get("share_url")
        or (f"https://www.douyin.com/video/{aweme_id}" if aweme_id else "")
    )
    try:
        likes = int(stats.get("digg_count")) if stats.get("digg_count") is not None else None
    except (TypeError, ValueError):
        likes = None

    return {
        "video_id": aweme_id,
        "title": str(node.get("desc") or "").strip(),
        "author": str(author.get("nickname") or "").strip(),
        "likes": likes,
        "publish_time_utc": published.isoformat() if published else "",
        "publish_timestamp": created,
        "video_url": (f"snssdk1128://aweme/detail/{aweme_id}" if aweme_id else ""),
        "web_url": (f"https://www.douyin.com/video/{aweme_id}" if aweme_id else str(share_url or "")),
        "play_count": stats.get("play_count"),
        "comment_count": stats.get("comment_count"),
        "share_count": stats.get("share_count"),
        "collect_count": stats.get("collect_count"),
        "category": category,
        "search_keyword": keyword,
        "captured_at_utc": captured_at,
        "source": "TikHub Douyin video search V2",
    }


def get_page_state(payload, business_wrapper):
    candidates = [
        payload,
        payload.get("data") if isinstance(payload, dict) else None,
        business_wrapper,
    ]
    cursor = None
    has_more = None
    search_id = ""
    backtrace = ""
    for obj in candidates:
        if not isinstance(obj, dict):
            continue
        if obj.get("cursor") is not None:
            cursor = obj.get("cursor")
        if obj.get("has_more") is not None:
            has_more = obj.get("has_more")
        if obj.get("search_id"):
            search_id = str(obj.get("search_id"))
        if obj.get("backtrace"):
            backtrace = str(obj.get("backtrace"))
    if isinstance(has_more, str):
        has_more_bool = has_more.strip().lower() in ("1", "true", "yes")
    else:
        has_more_bool = bool(has_more)
    return cursor, has_more_bool, search_id, backtrace


def request_page(session, api_key, keyword, cursor, search_id, backtrace):
    body = {
        "keyword": keyword,
        "cursor": cursor,
        "sort_type": "1",
        "publish_time": "1",
        "filter_duration": "0",
        "content_type": "1",
        "search_id": search_id,
        "backtrace": backtrace,
    }
    response = session.post(
        API_URL,
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        },
        json=body,
        timeout=60,
    )
    try:
        payload = response.json()
    except ValueError:
        raise RuntimeError(f"TikHub returned non-JSON HTTP {response.status_code}")
    if response.status_code != 200:
        detail = payload.get("message_zh") or payload.get("message") or str(payload)[:400]
        raise RuntimeError(f"TikHub HTTP {response.status_code}: {detail}")
    code = payload.get("code") if isinstance(payload, dict) else None
    if code not in (None, 0, 200, "0", "200"):
        detail = payload.get("message_zh") or payload.get("message") or str(payload)[:400]
        raise RuntimeError(f"TikHub API code {code}: {detail}")
    rows, wrapper = get_nested_business_data(payload)
    cursor_next, has_more, next_search_id, next_backtrace = get_page_state(payload, wrapper)
    return rows, cursor_next, has_more, next_search_id or search_id, next_backtrace or backtrace


def write_reports(videos, summary):
    """Write source JSON/CSV and a clean, globally AI-score-ranked HTML report."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUTPUT_DIR / "douyin_daily_viral.json"
    csv_path = OUTPUT_DIR / "douyin_daily_viral.csv"
    html_path = OUTPUT_DIR / "douyin_daily_viral.html"

    def rank_key(video):
        score = video.get("ai_score")
        likes = video.get("likes")
        return (
            score if isinstance(score, (int, float)) else -1,
            likes if isinstance(likes, (int, float)) else -1,
        )

    sorted_rows = sorted(videos, key=rank_key, reverse=True)
    data = {
        "summary": summary,
        "methodology": {
            "platform": "Douyin App video search via TikHub",
            "publish_time_filter": "current China calendar day (provider publish_time=1 plus local Asia/Shanghai date validation)",
            "sort": "globally sorted by DeepSeek editorial score; current cumulative likes retained as a separate metric",
            "important_limit": "Keyword-based search, not a full-site crawl. AI uses video title/description and metadata only; it does not watch the video.",
            "ai_analysis": "Editorial potential scoring for English TikTok/Instagram adaptation. Unverified identity or event background must be marked as unknown.",
            "political_filter": "Only videos classified as not political propaganda are included; propaganda and uncertain classifications are excluded.",
        },
        "videos": sorted_rows,
    }
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    columns = [
        "ai_rank", "ai_score", "category", "subject_explainer", "why_attention",
        "content_angle", "english_hook", "story_score", "visual_potential_score",
        "global_appeal_score", "originality_score", "discussion_score",
        "ai_confidence", "ai_analysis_status", "likes", "title", "author",
        "publish_time_utc", "video_url", "web_url", "video_id", "search_keyword", "topic_region",
        "play_count", "comment_count", "share_count", "ai_reason", "ai_caveat", "political_promotion",
        "captured_at_utc",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for rank, video in enumerate(sorted_rows, 1):
            row = dict(video)
            row["ai_rank"] = rank
            writer.writerow(row)

    # Build one HTML card per unique video, combining its category labels.
    unique_videos = {}
    for video in sorted_rows:
        video_id = str(video.get("video_id") or "").strip()
        key = video_id or f"{video.get('title','')}|{video.get('author','')}"
        if key not in unique_videos:
            unique_videos[key] = dict(video)
            unique_videos[key]["html_categories"] = []
        current = unique_videos[key]
        category_values = list(video.get("categories") or [])
        if video.get("category"):
            category_values.append(video["category"])
        for category in category_values:
            if category and category not in current["html_categories"]:
                current["html_categories"].append(category)
        if rank_key(video) > rank_key(current):
            for field in (
                "ai_score", "story_score", "visual_potential_score",
                "global_appeal_score", "originality_score", "discussion_score",
                "subject_explainer", "why_attention", "ai_reason",
                "english_hook", "content_angle", "ai_caveat",
                "ai_confidence", "ai_analysis_status", "likes",
            ):
                if video.get(field) is not None:
                    current[field] = video[field]

    html_videos = sorted(unique_videos.values(), key=rank_key, reverse=True)
    completed = sum(1 for v in html_videos if v.get("ai_analysis_status") == "completed")
    total_unique = len(html_videos)
    scored = sum(1 for v in html_videos if isinstance(v.get("ai_score"), (int, float)))
    generated_at = html.escape(str(summary.get("captured_at_utc", "")))
    cards = []
    for rank, video in enumerate(html_videos, 1):
        score = video.get("ai_score")
        score_label = f"{int(score)}分" if isinstance(score, (int, float)) else "待评分"
        title = html.escape(str(video.get("title") or "（无标题）"))
        author = html.escape(str(video.get("author") or "未知作者"))
        subject = html.escape(str(video.get("subject_explainer") or "标题未说明具体内容，建议打开视频核实。").strip()[:65])
        why = html.escape(str(video.get("why_attention") or "").strip()[:40])
        angle = html.escape(str(video.get("content_angle") or "").strip()[:45])
        hook = html.escape(str(video.get("english_hook") or "").strip()[:120])
        categories = video.get("html_categories") or video.get("categories") or ([video.get("category")] if video.get("category") else [])
        category_text = html.escape(" / ".join(str(x) for x in categories if x))
        likes = video.get("likes")
        likes_label = f"{int(likes):,}" if isinstance(likes, (int, float)) else "未知"
        published = html.escape(str(video.get("publish_time_utc") or "时间未知").replace("+00:00", " UTC"))
        app_url = html.escape(str(video.get("video_url") or ""), quote=True)
        web_url = html.escape(str(video.get("web_url") or ""), quote=True)
        link_html = (
            f'<a href="{app_url}" style="color:#1558c0;font-size:12px;font-weight:bold;text-decoration:none;">打开抖音 App ↗</a>'
            + (f'&nbsp;&nbsp;<a href="{web_url}" style="color:#64748b;font-size:11px;text-decoration:none;">网页备用</a>' if web_url else "")
        ) if app_url else '<span style="color:#888;font-size:11px;">暂无视频链接</span>'
        content_rows = [
            ("核心内容", subject),
            ("关注点", why),
            ("英文改编角度", angle),
            ("口播开场", hook),
        ]
        content_html = "".join(
            f'<tr><td style="padding:2px 0;font-size:11px;line-height:1.35;color:#586575;vertical-align:top;width:82px;"><b>{label}</b></td><td style="padding:2px 0;font-size:12px;line-height:1.4;color:#263442;">{value}</td></tr>'
            for label, value in content_rows if value
        )
        cards.append(
            '<tr><td style="padding:10px 11px;background:#ffffff;border-bottom:1px solid #dfe4ea;">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
            f'<tr><td style="font-size:11px;line-height:1.3;color:#667085;padding:0 0 3px;">#{rank:02d} &nbsp; <b style="font-size:14px;color:#1558c0;">{score_label}</b> &nbsp; {category_text}</td></tr>'
            f'<tr><td style="font-size:14px;line-height:1.35;font-weight:bold;color:#17212b;padding:2px 0 4px;">{title}</td></tr>'
            f'<tr><td style="font-size:10px;line-height:1.35;color:#6b7280;padding:0 0 5px;">{author} &nbsp;·&nbsp; {likes_label}赞 &nbsp;·&nbsp; {published}</td></tr>'
            f'<tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{content_html}</table></td></tr>'
            f'<tr><td style="padding:6px 0 0;">{link_html}</td></tr>'
            '</table></td></tr>'
        )

    total_unique = len(html_videos)
    scored = sum(1 for v in html_videos if isinstance(v.get("ai_score"), (int, float)))
    excluded_foreign = int(summary.get("excluded_foreign_topics", 0) or 0)
    excluded_unclear = int(summary.get("excluded_unclear_region", 0) or 0)
    excluded_unclassified = int(summary.get("excluded_unclassified", 0) or 0)
    excluded_political = int(summary.get("excluded_political_promotion", 0) or 0)
    excluded_political_unclear = int(summary.get("excluded_political_unclear", 0) or 0)
    empty_state = '<tr><td style="padding:14px;background:#fff;color:#667085;font-size:13px;">今天没有找到同时满足当日发布、点赞超过10,000且非政治宣传的中国热点。</td></tr>' if not cards else ""
    html_doc = (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>中国抖音当日高赞热点日报</title></head>'
        '<body style="margin:0;padding:0;background:#f2f4f7;font-family:-apple-system,BlinkMacSystemFont,Arial,sans-serif;color:#17212b;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f2f4f7;"><tr><td align="center" style="padding:6px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:680px;border-collapse:collapse;">'
        '<tr><td style="padding:9px 11px;background:#ffffff;border-bottom:2px solid #1558c0;">'
        '<div style="font-size:18px;font-weight:bold;line-height:1.25;">中国抖音当日高赞热点日报</div>'
        '<div style="font-size:11px;color:#667085;line-height:1.4;padding-top:3px;">仅当日发布 · 点赞超过10,000 · 排除政治宣传 · 按英语短视频选题潜力排序</div>'
        '</td></tr>'
        f'<tr><td style="padding:6px 10px;background:#f8fafc;font-size:10px;line-height:1.5;color:#667085;">入选 {total_unique} 条 · 已评分 {scored} 条 · 排除海外 {excluded_foreign} 条 · 地域不明 {excluded_unclear} 条 · 未完成AI {excluded_unclassified} 条 · 排除政治宣传 {excluded_political} 条 · 政治属性不明 {excluded_political_unclear} 条</td></tr>'
        '<tr><td style="padding:4px 10px;background:#eef2f7;font-size:10px;font-weight:bold;color:#475467;">热点列表（按综合分排序）</td></tr>'
        + "".join(cards) + empty_state
        + f'<tr><td style="padding:8px 10px;background:#f8fafc;font-size:10px;line-height:1.45;color:#667085;">评分权重：故事性25% · 海外吸引力25% · 视觉潜力20% · 原创性15% · 讨论空间15%。AI仅依据标题/描述和互动数据分析，未观看视频画面。<br>采集时间（UTC）：{generated_at}</td></tr>'
        + '</table></td></tr></table></body></html>'
    )
    html_path.write_text(html_doc, encoding="utf-8")
    print(f"HTML报告已按AI综合分全局排序：{html_path}")
    return [str(json_path), str(csv_path), str(html_path)]


def main():
    api_key = os.environ.get("TIKHUB_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing TIKHUB_API_KEY GitHub Secret.")

    now_china = datetime.now(ZoneInfo("Asia/Shanghai"))
    today_china = now_china.date()
    captured_at = datetime.now(timezone.utc).isoformat()
    session = requests.Session()
    by_id = {}
    category_calls = defaultdict(int)
    category_errors = {}
    total_calls = 0
    total_api_items = 0

    max_total_calls = sum(
        len(words) * MAX_PAGES_PER_KEYWORD
        for words in CATEGORY_KEYWORDS.values()
    )
    print(f"开始当日高赞抖音采集：8类；北京时间日期={today_china}；每类最多{MAX_PER_CATEGORY}条")
    print(f"本轮API请求硬上限：{max_total_calls}次；按$0.01/次估算最高约${max_total_calls * 0.01:.2f}")

    for category, keywords in CATEGORY_KEYWORDS.items():
        print(f"\n== {category} ==")
        for keyword in keywords:
            cursor, search_id, backtrace = 0, "", ""
            for page_no in range(1, MAX_PAGES_PER_KEYWORD + 1):
                current_count = sum(
                    1 for v in by_id.values() if category in v["categories"]
                )
                if current_count >= MAX_PER_CATEGORY:
                    break
                if total_calls >= max_total_calls:
                    break
                try:
                    raw_items, next_cursor, has_more, next_search_id, next_backtrace = request_page(
                        session, api_key, keyword, cursor, search_id, backtrace
                    )
                    total_calls += 1
                    category_calls[category] += 1
                    total_api_items += len(raw_items)
                    print(f"{category} / {keyword} / page {page_no}: {len(raw_items)}条")
                    for raw in raw_items:
                        video = normalize_video(raw, category, keyword, captured_at)
                        if not video["video_id"] or not video["publish_timestamp"]:
                            continue
                        published = datetime.fromtimestamp(
                            video["publish_timestamp"], timezone.utc
                        )
                        published_china = published.astimezone(ZoneInfo("Asia/Shanghai"))
                        if published_china.date() != today_china or published_china > now_china + timedelta(minutes=10):
                            continue
                        video_id = video["video_id"]
                        existing = by_id.get(video_id)
                        if existing:
                            if category not in existing["categories"]:
                                existing["categories"].append(category)
                            if keyword not in existing["matched_keywords"]:
                                existing["matched_keywords"].append(keyword)
                            if video.get("likes") is not None and (
                                existing.get("likes") is None or video["likes"] > existing["likes"]
                            ):
                                existing["likes"] = video["likes"]
                        else:
                            video["categories"] = [category]
                            video["matched_keywords"] = [keyword]
                            by_id[video_id] = video
                    cursor = next_cursor if next_cursor is not None else cursor
                    search_id, backtrace = next_search_id, next_backtrace
                    if not has_more or next_cursor is None:
                        break
                    if page_no < MAX_PAGES_PER_KEYWORD:
                        time.sleep(REQUEST_DELAY_SECONDS)
                except Exception as exc:
                    category_errors.setdefault(category, []).append(
                        {"keyword": keyword, "page": page_no, "error": str(exc)}
                    )
                    print(f"⚠️ {category}/{keyword} 采集失败：{exc}", file=sys.stderr)
                    break
        count = sum(1 for v in by_id.values() if category in v["categories"])
        print(f"{category} 去重并校验北京时间当天发布后：{count}条")

    # Strict likes gate: keep only videos with > 10,000 cumulative likes at collection time.
    unique_before_like_filter = len(by_id)
    excluded_below_like_threshold = sum(
        1 for video in by_id.values()
        if not isinstance(video.get("likes"), (int, float)) or video.get("likes") <= MIN_LIKES_EXCLUSIVE
    )
    by_id = {
        video_id: video for video_id, video in by_id.items()
        if isinstance(video.get("likes"), (int, float)) and video.get("likes") > MIN_LIKES_EXCLUSIVE
    }
    print(
        f"点赞门槛过滤：原始去重 {unique_before_like_filter} 条，"
        f"保留点赞>{MIN_LIKES_EXCLUSIVE} 的 {len(by_id)} 条，"
        f"排除 {excluded_below_like_threshold} 条"
    )

    final_videos = []
    category_summary = {}
    for category in CATEGORY_KEYWORDS:
        cat_videos = [v for v in by_id.values() if category in v["categories"]]
        cat_videos.sort(
            key=lambda v: v["likes"] if v["likes"] is not None else -1,
            reverse=True,
        )
        cat_videos = cat_videos[:MAX_PER_CATEGORY]
        category_summary[category] = {
            "count": len(cat_videos),
            "target": MAX_PER_CATEGORY,
            "api_calls": category_calls[category],
            "top_likes": cat_videos[0]["likes"] if cat_videos else None,
            "complete": len(cat_videos) >= MAX_PER_CATEGORY,
        }
        for rank, video in enumerate(cat_videos, 1):
            row = dict(video)
            row["category"] = category
            row["category_rank"] = rank
            final_videos.append(row)

    summary = {
        "captured_at_utc": captured_at,
        "api_calls": total_calls,
        "estimated_api_cost_usd_at_0_01_per_call": round(total_calls * 0.01, 2),
        "raw_api_items_received": total_api_items,
        "unique_videos_before_like_filter": unique_before_like_filter,
        "excluded_below_like_threshold": excluded_below_like_threshold,
        "min_likes_exclusive": MIN_LIKES_EXCLUSIVE,
        "unique_videos": len(by_id),
        "category_video_rows": len(final_videos),
        "categories": category_summary,
        "errors": category_errors,
        "deepseek_analysis": "pending",
    }

    # Analyze each unique video once, then propagate the annotations to category rows.
    unique_videos = list(by_id.values())
    ai_summary = analyze_douyin_videos_with_deepseek(unique_videos)
    unique_before_region_filter = len(unique_videos)
    excluded_foreign = sum(1 for video in unique_videos if video.get("ai_analysis_status") == "completed" and video.get("topic_region") == "国外")
    excluded_unclear = sum(1 for video in unique_videos if video.get("ai_analysis_status") == "completed" and video.get("topic_region") == "不明确")
    excluded_unclassified = sum(1 for video in unique_videos if video.get("ai_analysis_status") != "completed")
    excluded_political = sum(1 for video in unique_videos if video.get("ai_analysis_status") == "completed" and video.get("political_promotion") == "是")
    excluded_political_unclear = sum(1 for video in unique_videos if video.get("ai_analysis_status") == "completed" and video.get("political_promotion") == "不确定")
    unique_videos = [video for video in unique_videos if video.get("ai_analysis_status") == "completed" and video.get("topic_region") == "中国国内" and video.get("political_promotion") == "否"]
    by_id = {video["video_id"]: video for video in unique_videos}
    summary["unique_videos_before_region_filter"] = unique_before_region_filter
    summary["excluded_foreign_topics"] = excluded_foreign
    summary["excluded_unclear_region"] = excluded_unclear
    summary["excluded_unclassified"] = excluded_unclassified
    summary["excluded_political_promotion"] = excluded_political
    summary["excluded_political_unclear"] = excluded_political_unclear
    summary["unique_videos"] = len(unique_videos)
    summary["topic_filter"] = "中国国内；国外/地域不明/未完成AI分类均排除"
    summary["political_filter"] = "只保留AI判定为非政治宣传的内容；政治宣传及判定不明确均排除"
    summary["likes_filter"] = f"北京时间当天发布且点赞数严格大于 {MIN_LIKES_EXCLUSIVE}"

    final_videos = []
    for category in CATEGORY_KEYWORDS:
        cat_videos = [v for v in by_id.values() if category in v.get("categories", [])]
        if category in summary.get("categories", {}):
            summary["categories"][category]["count"] = min(len(cat_videos), MAX_PER_CATEGORY)
        cat_videos.sort(
            key=lambda v: v.get("likes") if v.get("likes") is not None else -1,
            reverse=True,
        )
        like_ranks = {v["video_id"]: rank for rank, v in enumerate(cat_videos, 1)}
        ai_sorted = sorted(
            cat_videos,
            key=lambda v: (
                v.get("ai_score") if v.get("ai_score") is not None else -1,
                v.get("likes") if v.get("likes") is not None else -1,
            ),
            reverse=True,
        )
        ai_sorted = ai_sorted[:MAX_PER_CATEGORY]
        ai_ranks = {v["video_id"]: rank for rank, v in enumerate(ai_sorted, 1)}
        for video in ai_sorted:
            row = dict(video)
            row["category"] = category
            row["category_rank_by_likes"] = like_ranks.get(video["video_id"])
            row["ai_rank"] = ai_ranks.get(video["video_id"])
            final_videos.append(row)

    summary["deepseek_analysis"] = ai_summary
    summary["ai_ranked_category_rows"] = len(final_videos)
    paths = write_reports(final_videos, summary)
    print("\n采集完成")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for path in paths:
        print(f"REPORT: {path}")

    if total_calls == 0:
        raise RuntimeError("No successful TikHub requests were completed.")
    if not final_videos:
        print("本轮没有符合当日发布、点赞>10,000、中国国内且非政治宣传规则的视频；仍生成空报告并发送每日摘要。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"❌ 完整抖音采集失败：{exc}", file=sys.stderr)
        raise SystemExit(1)