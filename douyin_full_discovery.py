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
            "reason 用简体中文写出基于现有资料的评分依据；"
            "english_hook 用自然简洁的英文写一个可用于口播开头的钩子；"
            "content_angle 用简体中文写一句改编方向；"
            "caveat 用简体中文标注资料不足或改编风险；"
            "confidence 只能是 high、medium、low。"
            "严格返回 JSON："
            "{\"items\":[{\"id\":0,\"story_score\":0,\"visual_potential_score\":0,"
            "\"global_appeal_score\":0,\"originality_score\":0,\"discussion_score\":0,"
            "\"reason\":\"\",\"english_hook\":\"\",\"content_angle\":\"\","
            "\"caveat\":\"\",\"confidence\":\"low\"}]}"
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
                video["ai_reason"] = str(ai.get("reason", ""))[:600]
                video["english_hook"] = str(ai.get("english_hook", ""))[:300]
                video["content_angle"] = str(ai.get("content_angle", ""))[:500]
                video["ai_caveat"] = str(ai.get("caveat", ""))[:400]
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
        "video_url": str(share_url or ""),
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
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUTPUT_DIR / "douyin_full_discovery.json"
    csv_path = OUTPUT_DIR / "douyin_full_discovery.csv"
    html_path = OUTPUT_DIR / "douyin_full_discovery.html"

    data = {
        "summary": summary,
        "methodology": {
            "platform": "Douyin App video search via TikHub",
            "publish_time_filter": "last 7 days (provider publish_time=7) plus local timestamp validation",
            "sort": "most likes (sort_type=1)",
            "categories": CATEGORY_KEYWORDS,
            "max_per_category": MAX_PER_CATEGORY,
            "max_pages_per_keyword": MAX_PAGES_PER_KEYWORD,
            "important_limit": "Keyword-based search, not a full-site crawl. Rankings are top results for the listed search terms, not guaranteed global Douyin category rankings.",
            "ai_analysis": "DeepSeek metadata-only editorial scoring. The model does not watch videos; story and visual claims are inferred only from supplied metadata.",
        },
        "videos": videos,
    }
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    columns = [
        "category", "category_rank_by_likes", "ai_rank", "ai_score",
        "story_score", "visual_potential_score", "global_appeal_score",
        "originality_score", "discussion_score", "ai_confidence",
        "ai_analysis_status", "likes", "title", "author", "publish_time_utc",
        "video_url", "video_id", "search_keyword", "play_count",
        "comment_count", "share_count", "ai_reason", "english_hook",
        "content_angle", "ai_caveat", "captured_at_utc",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(videos)

    rows = []
    for category in CATEGORY_KEYWORDS:
        cat_videos = [v for v in videos if v["category"] == category]
        cat_videos.sort(
            key=lambda v: (
                v.get("ai_score") if v.get("ai_score") is not None else -1,
                v.get("likes") if v.get("likes") is not None else -1,
            ),
            reverse=True,
        )
        rows.append(f"<h2>{html.escape(category)} ({len(cat_videos)}/{MAX_PER_CATEGORY})</h2>")
        rows.append("<table><thead><tr><th>AI评分</th><th>点赞</th><th>标题 / 作者</th><th>AI判断与选题方向</th><th>英文开场</th><th>发布时间 UTC</th><th>视频</th></tr></thead><tbody>")
        for video in cat_videos:
            title = html.escape(video.get("title") or "(无标题)")
            author = html.escape(video.get("author") or "(未知作者)")
            published = html.escape(video.get("publish_time_utc") or "未知")
            likes = video.get("likes")
            likes_text = f"{likes:,}" if isinstance(likes, int) else "未知"
            ai_score = video.get("ai_score")
            ai_text = str(ai_score) if isinstance(ai_score, int) else "未评分"
            reason = html.escape(video.get("ai_reason") or video.get("ai_caveat") or "暂无分析")
            angle = html.escape(video.get("content_angle") or "")
            hook = html.escape(video.get("english_hook") or "")
            confidence = html.escape(video.get("ai_confidence") or "未评估")
            link = html.escape(video.get("video_url") or "", quote=True)
            link_cell = f'<a href="{link}" target="_blank" rel="noopener">打开抖音视频</a>' if link else "无链接"
            rows.append(
                f"<tr><td>{ai_text}<br><small>{confidence}</small></td>"
                f"<td>{likes_text}</td><td>{title}<br><small>{author}</small></td>"
                f"<td>{reason}<br><b>选题方向：</b>{angle}</td>"
                f"<td>{hook}</td><td>{published}</td><td>{link_cell}</td></tr>"
            )
        rows.append("</tbody></table>")
    html_doc = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<title>抖音最近7天高赞视频发现报告</title>
<style>body{font-family:Arial,sans-serif;margin:28px;color:#222}
h1{margin-bottom:6px}small{color:#666}table{border-collapse:collapse;width:100%;margin:12px 0 28px}
th,td{border:1px solid #ddd;padding:8px;text-align:left;vertical-align:top}
th{background:#f3f5f7}td:first-child{white-space:nowrap}
</style><body><h1>抖音最近7天高赞视频发现报告</h1>
<p>按 DeepSeek 海外选题潜力评分排序，同时保留抖音当前累计点赞数。AI 只分析标题/描述与互动元数据，并没有观看视频；视觉潜力和故事性均为文字线索推断，需人工复核。</p>
""" + "\n".join(rows) + "</body></html>"
    html_path.write_text(html_doc, encoding="utf-8")
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
    by_id = {video["video_id"]: video for video in unique_videos}

    final_videos = []
    for category in CATEGORY_KEYWORDS:
        cat_videos = [v for v in by_id.values() if category in v.get("categories", [])]
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
        raise RuntimeError("Requests completed but no videos passed ID/date validation.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"❌ 完整抖音采集失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
