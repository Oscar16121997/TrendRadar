# Xiaohongshu trend discovery bot

This is a separate, manual-only bot based on the structure of the existing Douyin full-discovery bot.

## Categories

The old current-affairs/news category is removed. The new category set is:

1. 人物突然走红
2. 网红
3. 明星
4. 社会现象
5. 时尚潮流
6. 东方美学
7. 摄影美妆
8. 穿搭
9. 装修

Each category has two search keywords. The detector:
- Uses TikHub Xiaohongshu App V2 \`search_notes\`.
- Requests notes sorted by popularity and filters to the past week.
- Searches up to two pages per keyword.
- Deduplicates by note ID.
- Keeps notes with likes strictly greater than 5,000.
- Uses DeepSeek to score the notes for English short-form content potential.
- Retains AI-classified China-domestic topics only.
- Produces separate JSON, CSV, and HTML reports.
- Emails the report using the existing SMTP GitHub Secrets.

## Manual launch

Open GitHub → Actions → Hot News Crawler → Run workflow.

Select only:
- \`run_xiaohongshu_full_discovery = true\`

Leave other inputs unchecked.

The job is manual-only. It is not part of the daily cron-job.org request for Douyin bots 2 and 3, and it does not change the 15:00 Paris schedule.

## Secrets reused

No new secret is required. The job reuses:
- \`TIKHUB_API_KEY\`
- \`DEEPSEEK_API_KEY\`
- \`SMTP_USERNAME\`
- \`SMTP_PASSWORD\`

## API usage estimate

The hard maximum is:
- 9 categories × 2 keywords × 2 pages = 36 TikHub search requests.

TikHub's current Xiaohongshu FAQ states $0.01 per request, so the maximum search-request cost is approximately **$0.36 per full run**. Actual usage is usually lower when pagination ends early or category limits are reached. DeepSeek scoring is a separate usage charge.

The script logs the actual request count and an estimated TikHub request cost in its run summary. Failed HTTP requests are not charged by TikHub according to its billing FAQ.

## Output

The reports are saved under:
\`output/xiaohongshu_full_discovery/\`

Files:
- \`xiaohongshu_full_discovery.html\`
- \`xiaohongshu_full_discovery.csv\`
- \`xiaohongshu_full_discovery.json\`

The email uses a separate subject and attachments from the Douyin report.

## TikHub permission troubleshooting

If the search step returns HTTP 403 with `API Token lacks required permissions`:

1. Open [TikHub API token management](https://user.tikhub.io/dashboard/api).
2. Edit the same API Token used by the GitHub repository secret `TIKHUB_API_KEY`.
3. Enable the Xiaohongshu App V2 route / permission containing `search_notes`.
4. Keep any existing Douyin permissions enabled; do not remove them.
5. Save the token permission changes and rerun the workflow. If TikHub issues a replacement token instead of editing the existing one, update GitHub's `TIKHUB_API_KEY` repository secret with that new token.

A 403 permission failure is not a successful API response. TikHub's FAQ says only HTTP 200 successful requests are billed; failed requests are not charged. The bot now stops on the first 401/403 instead of repeatedly trying every category.
