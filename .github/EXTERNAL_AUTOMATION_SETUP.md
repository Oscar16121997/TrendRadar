# External scheduling and completion monitoring (bots 2 and 3 only)

This setup is for:
- Bot 2: `douyin_full_discovery` (eight-category Douyin keyword discovery)
- Bot 3: `marketing_trend` (selected Douyin marketing-account trend detector)

Bot 4 (`douyin_daily_viral`) is deliberately excluded from the daily schedule. It remains available only as a manual workflow-dispatch option.

## 1. Healthchecks.io

The current repository scheduler is UTC 13:34 daily. Configure Healthchecks to expect one ping per day at UTC 13:34.

Create one Cron check:
- Name: `TrendRadar daily bots 2+3`
- Schedule: `34 13 * * *`
- Time zone: `UTC`
- Grace period: 90 minutes

Copy the check's **Ping URL**. In GitHub, open:
`Settings → Secrets and variables → Actions → New repository secret`

Create this repository secret:
- Name: `HEALTHCHECKS_PING_URL`
- Value: the Ping URL copied from Healthchecks.io

The workflow sends a success ping only after both bot jobs complete successfully. If either job fails or is skipped during a scheduled run, it sends the Healthchecks failure ping (`/fail`). If the secret has not been added yet, the workflow emits a warning and skips monitoring.

## 2. cron-job.org

Use UTC scheduling to match the current GitHub cron expression.

Create a daily job with these settings:
- Name: `TrendRadar - bots 2 and 3`
- Time zone: `UTC`
- Time: every day at 13:34 UTC (14:34 Paris winter time)
- Request URL: `https://api.github.com/repos/Oscar16121997/TrendRadar/actions/workflows/crawler.yml/dispatches`
- Method: `POST`

Add these HTTP headers:
- `Accept: application/vnd.github+json`
- `Authorization: Bearer YOUR_FINE_GRAINED_GITHUB_TOKEN`
- `X-GitHub-Api-Version: 2022-11-28`
- `Content-Type: application/json`

Request body:
```json
{
  "ref": "main",
  "inputs": {
    "run_douyin_full_discovery": "true",
    "run_marketing_trend": "true"
  }
}
```

Create a GitHub fine-grained personal access token limited to repository `Oscar16121997/TrendRadar`, with **Actions: Read and write** permission. Store it only in cron-job.org's Authorization header. Do not put the token in this repository, in source code, or in chat.

A successful GitHub workflow-dispatch API request returns HTTP `204 No Content`. In cron-job.org, treat 2xx as success and enable its own failure notification if desired.

## 3. Prevent duplicate daily runs

The repository currently retains its native GitHub schedule at 13:34 UTC as a fallback. **Do not enable the cron-job.org job at 13:34 UTC while the native schedule is still enabled**, or both triggers can start the same bots and duplicate API usage and emails.

After cron-job.org is configured and its first manual test returns HTTP 204 and starts the two bot jobs, remove the native `schedule:` trigger from `.github/workflows/crawler.yml`. Keep the `workflow_dispatch:` trigger. This leaves cron-job.org as the sole daily starter, while Healthchecks.io independently alerts if the daily completion ping is missing.

## 4. Manual verification

Use cron-job.org's "Run now" / test function once. Then check GitHub Actions:
- The run event should be `workflow_dispatch`.
- Jobs `douyin_full_discovery` and `marketing_trend` should both finish with `success`.
- `report_daily_status` should finish with `success`.
- Healthchecks.io should show a successful ping.

Do not share either the GitHub token or the Healthchecks Ping URL in a screenshot or chat.
