# coding=utf-8
"""One-time full Douyin discovery run through TikHub.

Runs 8 category searches, with at most 2 keywords x 2 pages per category.
At the provider's published $0.01/request rate, the hard ceiling is about $0.32.
This script does not call DeepSeek or send email; it produces report files.
"""
import csv
import html
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

API_URL = "https://api.tikhub.io/api/v1/douyin/search/fetch_video_search_v2"
MAX_PER_CATEGORY = 20
MAX_PAGES_PER_KEYWORD = 2
REQUEST_DELAY_SECONDS = 0.8
OUTPUT_DIR = Path(os.environ.get("DOUYIN_OUTPUT_DIR", "output/douyin_full_run"))

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
            "点赞数是当前采集时的累计值，不代表最近7天新增点赞速度，也不能单凭点赞数证明正在爆红。"
            "不要补充外部背景，不要编造人物身份、故事、事件因果、播放量或趋势。"
            "必须为每条输入生成评分，不得遗漏。"
            "五项评分均为0到100整数：story_score（故事性/叙事空间）、"
            "visual_potential_score（视觉表达潜力，仅按文字线索推断）、"
            "global_appeal_score（英语受众理解和兴趣）、"
            "originality_score（新鲜感/文化独特性）、"
            "discussion_score（讨论、共鸣或评论空间）。"
            "不允许把点赞量直接等同于内容质量；分数衡量的是内容作为海外短视频选题的潜力。"
            "地域过滤规则：只保留核心主题属于中国国内的趋势。topic_region 必须是中国国内、国外、不明确之一。核心事件/人物/潮流发生在中国国内，或主题本身是中国社会、人物、娱乐、网红、网络文化，选中国国内；核心主题发生在中国以外任何国家或地区（包括韩国、日本、美国、欧洲及其他地区），包括当地新闻、社会事件、外国明星/网红/娱乐及海外潮流，选国外；标题信息不足以判断地域，选不明确。只是顺带提及外国或作比较，而主体仍是中国内容的，选中国国内。最终仅保留 topic_region=中国国内，国外和不明确均排除。不要因单个国家关键词误删。"
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
            "\"english_hook\":\"\",\"content_angle\":\"\",\"caveat\":\"\",\"topic_region\":\"不明确\",\"confidence\":\"low\"}]}"
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
        "publish_time": "7",
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
    json_path = OUTPUT_DIR / "douyin_full_discovery.json"
    csv_path = OUTPUT_DIR / "douyin_full_discovery.csv"
    html_path = OUTPUT_DIR / "douyin_full_discovery.html"

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
            "publish_time_filter": "last 7 days (provider publish_time=7) plus local timestamp validation",
            "sort": "globally sorted by DeepSeek editorial score; likes retained as a separate metric",
            "important_limit": "Keyword-based search, not a full-site crawl. AI uses video title/description and metadata only; it does not watch the video.",
            "ai_analysis": "Editorial potential scoring for English TikTok/Instagram adaptation. Unverified identity or event background must be marked as unknown.",
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
        "play_count", "comment_count", "share_count", "ai_reason", "ai_caveat",
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
        score_label = f"{int(score)} 分" if isinstance(score, (int, float)) else "未评分"
        title = html.escape(str(video.get("title") or "（无标题）"))
        author = html.escape(str(video.get("author") or "未知作者"))
        subject_raw = str(video.get("subject_explainer") or "标题未说明具体身份/经过，需打开原视频核实。").strip()
        why_raw = str(video.get("why_attention") or video.get("ai_reason") or "").strip()
        angle_raw = str(video.get("content_angle") or "").strip()
        # Keep the digest compact even if the model returns longer text than requested.
        subject = html.escape(subject_raw[:52])
        why = html.escape(why_raw[:32])
        angle = html.escape(angle_raw[:32])
        hook = html.escape(str(video.get("english_hook") or ""))
        caveat = html.escape(str(video.get("ai_caveat") or "AI 仅分析标题/描述和互动数据，未观看视频。"))
        confidence = html.escape(str(video.get("ai_confidence") or "low")).upper()
        categories = video.get("html_categories") or video.get("categories") or ([video.get("category")] if video.get("category") else [])
        category_tags = "".join(
            f'<span class="tag">{html.escape(str(category))}</span>'
            for category in categories
        )
        likes = video.get("likes")
        likes_label = f"{int(likes):,}" if isinstance(likes, (int, float)) else "未知"
        published = html.escape(str(video.get("publish_time_utc") or "未知"))
        video_url = html.escape(str(video.get("video_url") or ""), quote=True)
        web_url = html.escape(str(video.get("web_url") or ""), quote=True)
        link = (f'<a class="open-link" href="{video_url}">打开抖音 App ↗</a>' + (f' <a class="web-link" href="{web_url}">网页备用</a>' if web_url else "")) if video_url else '<span class="muted">暂无视频链接</span>'

        details = ""
        dimensions = [
            ("故事性", video.get("story_score")),
            ("视觉潜力", video.get("visual_potential_score")),
            ("海外吸引力", video.get("global_appeal_score")),
            ("原创性", video.get("originality_score")),
            ("讨论空间", video.get("discussion_score")),
        ]
        if any(value is not None for _, value in dimensions) or caveat:
            dimension_html = "".join(
                f"<span>{label} <b>{value if value is not None else '—'}</b></span>"
                for label, value in dimensions
            )
            angle_html = (
                f'<p><b>海外改编方向：</b>{angle}</p>'
                if angle else ""
            )
            hook_html = (
                f'<div class="hook"><span>英文口播开场</span><p>{hook}</p></div>'
                if hook else ""
            )
            reason_html = html.escape(str(video.get("ai_reason") or ""))
            details = (
                '<details class="more"><summary>更多分析</summary>'
                f'<div class="dimensions">{dimension_html}</div>'
                f'<p><b>评分理由：</b>{reason_html}</p>'
                f'{angle_html}{hook_html}'
                f'<p>{caveat}</p><p>判断可信度：{confidence}</p>'
                '</details>'
            )

        cards.append(
            f'<article class="video-card">'
            f'<div class="card-top"><span class="rank">#{rank:02d}</span>'
            f'<span class="score">{score_label}</span><div class="tags">{category_tags}</div></div>'
            f'<h2>{title}</h2>'
            f'<div class="meta">{author}　·　{likes_label} 赞　·　{published}</div>'
            f'<p class="summary-line"><b>概括：</b>{subject}</p>'
            f'<p class="why-line">{why}</p>'
            f'<div class="card-bottom">{link}{details}</div>'
            f'</article>'
        )

    empty_state = '<p class="empty">当前没有可展示的视频。</p>' if not cards else ""
    html_doc = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>中国抖音热点选题日报</title>
<style>
:root{color-scheme:light;--ink:#17212b;--muted:#64748b;--line:#e6eaf0;--accent:#315efb;--paper:#f5f7fb}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif}
.wrap{max-width:760px;margin:0 auto;padding:9px 8px 20px}
header{margin-bottom:7px}h1{font-size:19px;margin:0 0 3px;letter-spacing:-.2px}
.subtitle{color:var(--muted);font-size:11px;line-height:1.35;margin:0}
.summary{display:flex;gap:5px;flex-wrap:wrap;margin:7px 0 9px}
.stat{background:#fff;border:1px solid var(--line);border-radius:7px;padding:5px 8px;min-width:72px}
.stat b{display:inline;font-size:13px;margin-right:3px}.stat span{font-size:10px;color:var(--muted)}
.section-title{font-size:11px;color:var(--muted);font-weight:600;margin:0 0 5px}
.video-card{background:#fff;border:1px solid var(--line);border-radius:8px;padding:8px 9px;margin:0 0 6px;box-shadow:none}
.card-top{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.rank{font-size:10px;font-weight:700;color:var(--muted)}
.score{font-weight:800;color:#fff;background:var(--accent);border-radius:5px;padding:3px 6px;font-size:11px}
.tags{display:flex;gap:5px;flex-wrap:wrap}.tag{font-size:9px;color:#526078;background:#f0f3f8;border-radius:4px;padding:2px 4px}
.video-card h2{font-size:14px;line-height:1.28;margin:4px 0 2px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.meta{font-size:10px;color:var(--muted);margin-bottom:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.summary-line{font-size:11px;line-height:1.35;margin:3px 0 2px;color:#273444;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.why-line{font-size:10px;line-height:1.3;margin:0;color:#64748b;display:-webkit-box;-webkit-line-clamp:1;-webkit-box-orient:vertical;overflow:hidden}
.hook{background:#f6f8ff;border-left:3px solid var(--accent);padding:5px 7px;margin:6px 0}
.hook span{font-size:10px;color:var(--muted);font-weight:700}.hook p{font-size:11px;line-height:1.35;margin:2px 0 0}
.card-bottom{display:flex;align-items:center;justify-content:space-between;gap:7px;flex-wrap:wrap;margin-top:5px}
.open-link,.web-link{font-size:10px;font-weight:700;color:var(--accent);text-decoration:none}
.more{font-size:10px;color:var(--muted)}.more summary{cursor:pointer}.dimensions{display:flex;gap:5px;flex-wrap:wrap;margin:5px 0}
.dimensions span{background:#f3f5f8;padding:3px 5px;border-radius:5px}
.more p{line-height:1.4;margin:4px 0}.empty{color:var(--muted)}
footer{color:var(--muted);font-size:10px;line-height:1.4;margin-top:8px;border-top:1px solid var(--line);padding-top:6px}
@media(max-width:600px){.wrap{padding:7px 6px 15px}.video-card{padding:7px 8px;margin-bottom:5px}h1{font-size:18px}.video-card h2{font-size:13px}}
</style>
</head>
<body><main class="wrap">
<header><h1>中国抖音热点选题日报</h1>
<p class="subtitle">只保留中国国内趋势 · 最近7天 · 按英语短视频选题潜力排序</p></header>
<div class="summary">
<div class="stat"><b>""" + str(total_unique) + """</b><span>条去重视频</span></div>
<div class="stat"><b>""" + str(scored) + """</b><span>条已有评分</span></div>
<div class="stat"><b>8</b><span>个中国内容类别</span></div>
<div class="stat"><b>""" + str(summary.get("excluded_foreign_topics", 0)) + """</b><span>排除海外热点</span></div>
<div class="stat"><b>""" + str(summary.get("excluded_unclear_region", 0) + summary.get("excluded_unclassified", 0)) + """</b><span>排除地域不明/未分类</span></div>
</div>
<p class="section-title">中国本土高潜力选题 · 综合分降序</p>
""" + empty_state + "".join(cards) + """
<footer>
排序依据：故事性 25%、海外受众吸引力 25%、视觉潜力 20%、原创性 15%、讨论空间 15%。地域过滤：只保留中国国内主题。<br>
重要说明：AI 只读取视频标题/描述、作者和互动数据，没有实际观看视频或读取评论。人物身份、事件背景、画面内容如未在文案中明确提供，会标注资料不足。点赞数是采集时的累计值，不代表最近 7 天点赞增速。<br>
采集时间（UTC）：""" + generated_at + """
</footer></main></body></html>"""
    html_path.write_text(html_doc, encoding="utf-8")
    print(f"HTML报告已按AI综合分全局排序：{html_path}")
    return [str(json_path), str(csv_path), str(html_path)]


def main():
    api_key = os.environ.get("TIKHUB_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing TIKHUB_API_KEY GitHub Secret.")

    captured_at = datetime.now(timezone.utc).isoformat()
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
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
    print(f"开始完整抖音采集：8类，目标每类最多{MAX_PER_CATEGORY}条")
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
                        if published < cutoff or published > datetime.now(timezone.utc) + timedelta(minutes=10):
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
        print(f"{category} 去重并校验7天后：{count}条")

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
    unique_videos = [video for video in unique_videos if video.get("ai_analysis_status") == "completed" and video.get("topic_region") == "中国国内"]
    by_id = {video["video_id"]: video for video in unique_videos}
    summary["unique_videos_before_region_filter"] = unique_before_region_filter
    summary["excluded_foreign_topics"] = excluded_foreign
    summary["excluded_unclear_region"] = excluded_unclear
    summary["excluded_unclassified"] = excluded_unclassified
    summary["unique_videos"] = len(unique_videos)
    summary["topic_filter"] = "中国国内；国外/地域不明/未完成AI分类均排除"

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
        print("本轮没有符合中国国内地域规则的视频；仍生成空报告并发送每日摘要。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"❌ 完整抖音采集失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
