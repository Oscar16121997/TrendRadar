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


def parse_deepseek_json(content):
    text = (content or "").strip()
    fence = chr(96) * 3
    if text.startswith(fence):
        text = text.split("\n", 1)[-1]
        if text.endswith(fence):
            text = text[:-3].strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("DeepSeek response does not contain JSON")
    return json.loads(text[start:end + 1])


def analyze_notes_with_deepseek(notes):
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        print("⚠️ DEEPSEEK_API_KEY 未配置，跳过 AI 评分")
        return {"calls": 0, "analyzed": 0, "failed_batches": 0, "status": "missing_api_key"}
    session = requests.Session()
    calls = 0
    analyzed = 0
    failed = 0
    for batch_no, start in enumerate(range(0, len(notes), AI_BATCH_SIZE), 1):
        batch = notes[start:start + AI_BATCH_SIZE]
        payload = []
        for index, note in enumerate(batch):
            payload.append({
                "id": index, "title": note.get("title", ""),
                "description": note.get("desc", "")[:1200],
                "author": note.get("author", ""), "categories": note.get("categories", []),
                "likes": note.get("likes"), "collects": note.get("collects"),
                "comments": note.get("comments"), "shares": note.get("shares"),
                "publish_time_utc": note.get("publish_time_utc", ""),
            })
        system_prompt = (
            "你是面向英语受众的短视频选题编辑，评估中国小红书笔记是否适合改编为 TikTok/Instagram 英语内容。"
            "只能依据标题、正文描述、作者、类别、发布时间和互动数字判断。"
            "你没有观看图片/视频或读取评论正文，不能声称看到了画面、装修细节、穿搭细节或人物行为。"
            "视觉潜力只能根据文字线索谨慎推断，证据不足时降低分数和 confidence。"
            "互动数字是采集时累计值，不代表最近7天新增速度；不能单凭互动量证明正在爆红。"
            "不要编造人物身份、故事、设计风格、装修预算、材质或穿搭单品。"
            "只保留核心主题属于中国国内的内容。topic_region 必须是中国国内、国外、不明确之一。"
            "中国本土人物、生活方式、穿搭、家居、装修和社会文化选中国国内；"
            "核心主题属于中国以外地区选国外；资料不足选不明确。最终仅保留中国国内。"
            "五项评分均为0到100整数：story_score、visual_potential_score、global_appeal_score、originality_score、discussion_score。"
            "不要把点赞或收藏量直接等同于内容质量。"
            "subject_explainer 用简体中文写1句说明笔记具体分享什么；why_attention 最多25字；reason 最多45字。"
            "english_hook 用自然简洁英文写短口播开场；content_angle 和 caveat 各最多25字。"
            "资料不足时写标题和正文未说明，需打开笔记核实，不要猜测。"
            "严格返回 JSON，items 数组每条含 id、五项评分、subject_explainer、why_attention、reason、"
            "english_hook、content_angle、caveat、topic_region、confidence。"
        )

        user_prompt = (
            "请评估以下小红书笔记作为英语短视频选题的潜力，重点关注生活方式、穿搭、装修和美学内容。"
            "没有文字证据时不要推测画面或事实。\n"
            + json.dumps(payload, ensure_ascii=False)
        )
        try:
            response = session.post(
                DEEPSEEK_API_URL,
                headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
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
            calls += 1
            response.raise_for_status()
            parsed = parse_deepseek_json(response.json()["choices"][0]["message"]["content"])
            items = parsed.get("items", []) if isinstance(parsed, dict) else []
            by_id = {}
            for item in items:
                try:
                    by_id[int(item["id"])] = item
                except (KeyError, TypeError, ValueError):
                    continue
            for index, note in enumerate(batch):
                item = by_id.get(index)
                if item is None:
                    note["ai_analysis_status"] = "missing_result"
                    continue
                scores = {}
                for field in AI_SCORE_WEIGHTS:
                    try:
                        scores[field] = max(0, min(100, int(item.get(field, 0))))
                    except (TypeError, ValueError):
                        scores[field] = 0
                note.update(scores)
                note["ai_score"] = round(sum(scores[k] * v for k, v in AI_SCORE_WEIGHTS.items()))
                for field, source, limit in (
                    ("subject_explainer", "subject_explainer", 700),
                    ("why_attention", "why_attention", 400),
                    ("ai_reason", "reason", 600),
                    ("english_hook", "english_hook", 300),
                    ("content_angle", "content_angle", 500),
                    ("ai_caveat", "caveat", 400),
                ):
                    note[field] = str(item.get(source, ""))[:limit]
                region = str(item.get("topic_region", "不明确")).strip()
                note["topic_region"] = region if region in ("中国国内", "国外", "不明确") else "不明确"
                confidence = str(item.get("confidence", "low")).lower()
                note["ai_confidence"] = confidence if confidence in ("high", "medium", "low") else "low"
                note["ai_analysis_status"] = "completed"
                analyzed += 1
            print(
                f"DeepSeek 第 {batch_no} 批完成：{len(batch)} 条输入，"
                f"{sum(1 for n in batch if n.get('ai_analysis_status') == 'completed')} 条成功评分"
            )
        except Exception as exc:
            failed += 1
            print(f"DeepSeek 第 {batch_no} 批分析失败：{exc}", file=sys.stderr)
            for note in batch:
                note.setdefault("ai_analysis_status", "failed")

    return {
        "calls": calls, "analyzed": analyzed, "failed_batches": failed,
        "status": "completed" if analyzed == len(notes) else "partial",
        "model": DEEPSEEK_MODEL, "batch_size": AI_BATCH_SIZE,
        "score_weights": AI_SCORE_WEIGHTS,
    }


def write_reports(notes, summary):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = OUTPUT_DIR / "xiaohongshu_full_discovery.json"
    csv_path = OUTPUT_DIR / "xiaohongshu_full_discovery.csv"
    html_path = OUTPUT_DIR / "xiaohongshu_full_discovery.html"

    def rank_key(note):
        score = note.get("ai_score")
        likes = note.get("likes")
        return (
            score if isinstance(score, (int, float)) else -1,
            likes if isinstance(likes, (int, float)) else -1,
        )

    sorted_notes = sorted(notes, key=rank_key, reverse=True)
    json_path.write_text(
        json.dumps({"summary": summary, "notes": sorted_notes}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    columns = [
        "ai_rank", "ai_score", "category", "subject_explainer", "why_attention",
        "content_angle", "english_hook", "story_score", "visual_potential_score",
        "global_appeal_score", "originality_score", "discussion_score",
        "ai_confidence", "ai_analysis_status", "likes", "collects", "comments",
        "shares", "title", "desc", "author", "publish_time_utc", "note_url",
        "note_id", "search_keyword", "topic_region", "ai_reason", "ai_caveat",
        "captured_at_utc",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for rank, note in enumerate(sorted_notes, 1):
            row = dict(note)
            row["ai_rank"] = rank
            writer.writerow(row)

    cards = []
    for rank, note in enumerate(sorted_notes, 1):
        score = note.get("ai_score")
        score_label = f"{int(score)}分" if isinstance(score, (int, float)) else "待评分"
        title = html.escape(str(note.get("title") or note.get("desc") or "（无标题）")[:180])
        desc = html.escape(str(note.get("desc") or "").strip()[:420])
        author = html.escape(str(note.get("author") or "未知作者"))
        categories = html.escape(" / ".join(note.get("categories") or [note.get("category", "")]))
        likes = f"{note['likes']:,}" if isinstance(note.get("likes"), int) else "未知"
        collects = f"{note['collects']:,}" if isinstance(note.get("collects"), int) else "未知"
        comments = f"{note['comments']:,}" if isinstance(note.get("comments"), int) else "未知"
        published = html.escape(str(note.get("publish_time_utc") or "时间未知"))
        url = html.escape(str(note.get("note_url") or ""), quote=True)
        link = f'<a href="{url}" target="_blank" rel="noopener">打开小红书笔记 ↗</a>' if url else "暂无笔记链接"
        explainer = html.escape(str(note.get("subject_explainer") or "标题和正文未说明，需打开笔记核实。")[:240])
        why = html.escape(str(note.get("why_attention") or "")[:150])
        angle = html.escape(str(note.get("content_angle") or "")[:180])
        hook = html.escape(str(note.get("english_hook") or "")[:180])
        reason = html.escape(str(note.get("ai_reason") or "")[:220])
        caveat = html.escape(str(note.get("ai_caveat") or "")[:160])
        cards.append(
            '<article class="card">'
            f'<div class="top"><span>#{rank:02d}</span><b class="score">{score_label}</b><span class="tag">{categories}</span></div>'
            f'<h2>{title}</h2><p class="meta">{author} · {likes}赞 · {collects}收藏 · {comments}评论 · {published}</p>'
            f'<p>{desc}</p><div class="section"><b>核心内容</b><p>{explainer}</p></div>'
            f'<div class="section"><b>关注点</b><p>{why}</p></div>'
            f'<div class="section"><b>海外改编角度</b><p>{angle}</p></div>'
            f'<div class="section"><b>英文口播开场</b><p>{hook}</p></div>'
            f'<details><summary>评分依据与资料限制</summary><p>{reason}</p><p>{caveat}</p>'
            f'<p>判断可信度：{html.escape(str(note.get("ai_confidence") or "low"))}</p></details>'
            f'<div class="link">{link}</div></article>'
        )

    summary_line = (
        "去重笔记 " + str(summary.get("unique_notes_before_like_filter", 0))
        + " 条；点赞筛选后 " + str(summary.get("unique_notes_after_like_filter", 0))
        + " 条；最终中国国内主题 " + str(len(sorted_notes)) + " 条。"
    )
    html_doc = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>小红书趋势选题日报</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f4f6f9;color:#17212b;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:26px 15px 50px}h1{font-size:25px;margin:0 0 8px}
.sub,.meta,footer{color:#667085;font-size:12px;line-height:1.6}.card{background:#fff;border:1px solid #e4e8ef;border-radius:13px;padding:17px 18px;margin:12px 0;box-shadow:0 2px 8px rgba(20,40,80,.035)}
.top{display:flex;align-items:center;gap:9px;flex-wrap:wrap;color:#667085;font-size:12px}.score{background:#315efb;color:#fff;padding:4px 8px;border-radius:6px}.tag{background:#eef2f8;padding:4px 7px;border-radius:5px}
h2{font-size:17px;line-height:1.5;margin:12px 0 5px}.meta{margin:0 0 12px}.card>p{font-size:13px;line-height:1.65;margin:7px 0}
.section{margin:11px 0}.section>b{font-size:12px;color:#475467}.section p,details p{font-size:13px;line-height:1.65;margin:3px 0}
details{color:#667085;font-size:12px;margin-top:10px}summary{cursor:pointer}.link{margin-top:12px}.link a{color:#315efb;font-weight:700;text-decoration:none}
footer{margin-top:24px;border-top:1px solid #dfe4ea;padding-top:12px}
@media(max-width:600px){.wrap{padding:18px 10px 35px}.card{padding:14px}h1{font-size:22px}}
</style></head><body><main class="wrap">
<h1>小红书趋势选题日报</h1>
<p class="sub">最近 7 天 · 9 个内容类别 · 点赞严格大于 5,000 · 按 DeepSeek 海外短视频选题潜力排序</p>
<div class="sub">""" + html.escape(summary_line) + """</div>
""" + ("".join(cards) if cards else '<p class="card">本轮没有找到通过点赞门槛及中国国内主题筛选的笔记。</p>') + """
<footer>说明：关键词搜索不是全站爬取。搜索结果和互动数据来自 TikHub；AI 只分析笔记标题、正文描述和元数据，没有观看图片/视频或读取评论正文。互动数为采集时累计值，不代表最近一周的增长量。<br>
""" + html.escape(str(summary.get("captured_at_utc", ""))) + """ UTC</footer></main></body></html>"""
    html_path.write_text(html_doc, encoding="utf-8")
    return [str(json_path), str(csv_path), str(html_path)]
