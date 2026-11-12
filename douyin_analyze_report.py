# coding=utf-8
"""Re-analyze an existing Douyin discovery JSON artifact with DeepSeek only.

This path deliberately does NOT call TikHub, so it reuses collected data and
avoids any additional Douyin search API charges.
"""
import json
import os
import sys
from pathlib import Path

from douyin_full_discovery import (
    AI_SCORE_WEIGHTS,
    CATEGORY_KEYWORDS,
    MAX_PER_CATEGORY,
    analyze_douyin_videos_with_deepseek,
    write_reports,
)


INPUT_FILE = Path(os.environ.get(
    "DOUYIN_INPUT_JSON",
    "input/douyin_full_discovery.json",
))


def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(
            f"找不到历史采集数据：{INPUT_FILE}. "
            "请确认下载的 GitHub Actions artifact 中包含 douyin_full_discovery.json。"
        )

    source = json.loads(INPUT_FILE.read_text(encoding="utf-8"))
    original_summary = source.get("summary", {})
    source_rows = source.get("videos", [])
    if not source_rows:
        raise RuntimeError("历史报告中没有 videos 数据，无法分析。")

    # Reconstruct unique videos and categories from the category-oriented rows.
    unique = {}
    for row in source_rows:
        video_id = str(row.get("video_id") or "").strip()
        if not video_id:
            continue
        if video_id not in unique:
            unique[video_id] = dict(row)
            unique[video_id]["categories"] = []
            unique[video_id]["matched_keywords"] = []
        item = unique[video_id]
        category = row.get("category")
        if category and category not in item["categories"]:
            item["categories"].append(category)
        for category in row.get("categories", []):
            if category and category not in item["categories"]:
                item["categories"].append(category)
        for keyword in row.get("matched_keywords", []):
            if keyword and keyword not in item["matched_keywords"]:
                item["matched_keywords"].append(keyword)

    unique_videos = list(unique.values())
    print(
        f"复用历史抖音数据：{len(source_rows)}条分类记录，"
        f"去重后{len(unique_videos)}条视频；本任务不会调用TikHub。"
    )

    ai_summary = analyze_douyin_videos_with_deepseek(unique_videos)
    unique = {video["video_id"]: video for video in unique_videos}

    report_rows = []
    category_summary = original_summary.get("categories", {})
    for category in CATEGORY_KEYWORDS:
        category_videos = [
            video for video in unique.values()
            if category in video.get("categories", [])
        ]
        category_videos.sort(
            key=lambda video: (
                video.get("likes") if video.get("likes") is not None else -1
            ),
            reverse=True,
        )
        category_videos = category_videos[:MAX_PER_CATEGORY]
        likes_rank = {
            video["video_id"]: rank
            for rank, video in enumerate(category_videos, 1)
        }
        ai_sorted = sorted(
            category_videos,
            key=lambda video: (
                video.get("ai_score") if video.get("ai_score") is not None else -1,
                video.get("likes") if video.get("likes") is not None else -1,
            ),
            reverse=True,
        )
        ai_rank = {
            video["video_id"]: rank
            for rank, video in enumerate(ai_sorted, 1)
        }
        for video in ai_sorted:
            output = dict(video)
            output["category"] = category
            output["category_rank_by_likes"] = likes_rank.get(video["video_id"])
            output["ai_rank"] = ai_rank.get(video["video_id"])
            report_rows.append(output)

        category_summary.setdefault(category, {})
        category_summary[category]["ai_scored"] = sum(
            1 for video in category_videos
            if video.get("ai_analysis_status") == "completed"
        )

    summary = dict(original_summary)
    summary["categories"] = category_summary
    summary["deepseek_analysis"] = ai_summary
    summary["ai_ranked_category_rows"] = len(report_rows)
    summary["analysis_source"] = str(INPUT_FILE)
    summary["tikHub_additional_requests"] = 0
    summary["ai_score_weights"] = AI_SCORE_WEIGHTS

    paths = write_reports(report_rows, summary)
    print("\nDeepSeek分析完成")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for path in paths:
        print("REPORT: " + path)

    if ai_summary.get("analyzed", 0) == 0:
        raise RuntimeError(
            "DeepSeek没有成功分析任何视频。请查看API Key、账户余额和日志。"
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"❌ 历史报告DeepSeek分析失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
