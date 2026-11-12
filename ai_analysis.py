# coding=utf-8
"""DeepSeek-powered editorial analysis for TrendRadar daily news."""
import json
import os
import time
from typing import Dict, List

import requests

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-flash"
BATCH_SIZE = 30
CATEGORIES = ["时事", "人物突然走红", "网红", "明星", "社会现象", "时尚潮流", "东方美学", "摄影美妆"]


def _chunks(items, size):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _parse_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3].strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("DeepSeek response does not contain a JSON object")
    return json.loads(text[start:end + 1])

def analyze_news_with_deepseek(news_items: List[Dict]) -> List[Dict]:
    """Annotate every news item; failures leave the original items intact."""
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key or not news_items:
        if not api_key:
            print("⚠️ 未配置 DEEPSEEK_API_KEY，跳过 AI 分析")
        return news_items

    results = [dict(item) for item in news_items]
    session = requests.Session()
    for batch_no, batch in enumerate(_chunks(results, BATCH_SIZE), 1):
        payload = [
            {"id": i, "title": item.get("title", ""),
             "source": item.get("source_name", ""),
             "rank": (item.get("ranks") or [])[:3],
             "appearances": item.get("count", 1)}
            for i, item in enumerate(batch)
        ]
        prompt = (
            "你是面向海外 TikTok 和 Instagram 的短视频选题编辑。"
            "分析下面每条中文热点，判断其是否适合海外英语受众。"
            "只允许使用分类：" + "、".join(CATEGORIES) + "。"
            "分类可多选；没有匹配分类时 categories 返回空数组，但不得遗漏该条新闻。"
            "重点主动发现两类非传统新闻热点：一是普通人或新人物突然走红（例如街头摊主、特殊职业者、素人、网络人物）；"
            "二是时尚与审美风格突然走红（例如穿搭、妆容、发型、复古风、东方美学、视觉风格和生活方式）。"
            "同时识别有传播性的社会现象、网络梗和文化差异。不要要求标题必须包含‘爆红’‘网红’等关键词。"
            "topic_type 必须从 人物突然走红、时尚风格走红、社会现象、时事新闻、娱乐热点、普通内容 中选择一个最主要类型。"
            "trend_signal 只能根据输入中的平台来源、排名和出现次数概括可见信号；信息不足时明确写‘单个平台/单次榜单，暂无法确认趋势’。"
            "why_trending 说明人物、风格或现象为什么可能引发关注；不得编造背景、数据或传播过程。"
            "评分 score 为 0-100，衡量故事性、视觉性、跨文化吸引力、讨论度和可讲述性；不要仅因新闻热度高就给高分。"
            "英文标题要简洁自然；angle 用一句话说明英语口播可以从什么角度切入。"
            '严格返回 JSON 对象，格式为 {"items":[{"id":0,"categories":[],"topic_type":"人物突然走红","trend_signal":"...","why_trending":"...","score":0,"reason":"...","english_title":"...","angle":"..."}]}。'
            "trend_signal、why_trending、reason 和 angle 用简体中文，english_title 用英文。\n新闻数据："
            + json.dumps(payload, ensure_ascii=False)
        )
        try:
            response = session.post(
                API_URL,
                headers={"Authorization": "Bearer " + api_key,
                         "Content-Type": "application/json"},
                json={"model": MODEL, "messages": [
                    {"role": "system", "content": "只输出合法 JSON，不要 Markdown。"},
                    {"role": "user", "content": prompt}],
                    "temperature": 0.2, "response_format": {"type": "json_object"}},
                timeout=90,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            parsed = _parse_json(content)
            if isinstance(parsed, dict):
                parsed = parsed.get("items", [])
            by_id = {int(x["id"]): x for x in parsed if "id" in x}
            for i, item in enumerate(batch):
                ai = by_id.get(i)
                if not ai:
                    continue
                item["categories"] = [c for c in ai.get("categories", []) if c in CATEGORIES]
                try:
                    item["ai_score"] = max(0, min(100, int(ai.get("score", 0))))
                except (TypeError, ValueError):
                    item["ai_score"] = 0
                item["ai_reason"] = str(ai.get("reason", ""))[:500]
                item["topic_type"] = str(ai.get("topic_type", ""))[:80]
                item["trend_signal"] = str(ai.get("trend_signal", ""))[:300]
                item["why_trending"] = str(ai.get("why_trending", ""))[:500]
                item["english_title"] = str(ai.get("english_title", ""))[:240]
                item["ai_angle"] = str(ai.get("angle", ""))[:500]
            print(f"🤖 DeepSeek 已分析第 {batch_no} 批：{len(batch)} 条")
        except Exception as exc:
            print(f"⚠️ DeepSeek 第 {batch_no} 批分析失败，保留原始新闻：{exc}")
        if batch_no * BATCH_SIZE < len(results):
            time.sleep(1)
    return results
