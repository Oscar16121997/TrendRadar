# External scheduling for TrendRadar bots 2 and 3

This external scheduler is for:
- Bot 2: `douyin_full_discovery` (8-category Douyin keyword discovery)
- Bot 3: `marketing_trend` (selected Douyin marketing-account trend detector)

Bot 4 (`douyin_daily_viral`) is excluded from the daily schedule. It remains manually runnable through the workflow's `workflow_dispatch` input.

## GitHub workflow

The workflow is `.github/workflows/crawler.yml`, on branch `main`. It already has:
- `workflow_dispatch` enabled.
- Inputs `run_douyin_full_discovery` and `run_marketing_trend`.
- Job conditions that run bot 2 and bot 3 when their corresponding inputs are true.
- Bot 4's job condition is manual-input-only.

The native GitHub schedule currently remains set to UTC 13:34 as a temporary fallback. Do not leave it enabled at the same time as the external daily job once the external job is active, or both may start duplicate runs and send duplicate emails.

## Configure cron-job.org

Create a fine-grained GitHub personal access token:
1. GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens.
2. Resource owner: the owner of `Oscar16121997/TrendRadar`.
3. Repository access: only `Oscar16121997/TrendRadar`.
4. Repository permissions: `Actions: Read and write`.
5. Create the token and keep it private. Do not put it in this repository or share it in chat.

In cron-job.org, create one job with these settings:

- Title: `TrendRadar - bots 2 and 3`
- URL: `https://api.github.com/repos/Oscar16121997/TrendRadar/actions/workflows/416533052/dispatches`
- Request method: `POST`
- Schedule: every day at `13:34`
- Time zone: `UTC`
- Enable the job's failure notification.
- Save the response / execution history if the option is available.

Add these custom HTTP headers:
- `Accept: application/vnd.github+json`
- `Authorization: Bearer YOUR_FINE_GRAINED_GITHUB_TOKEN`
- `X-GitHub-Api-Version: 2022-11-28`
- `Content-Type: application/json`

Set the request body to exactly:

```json
{
  "ref": "main",
  "inputs": {
    "run_douyin_full_discovery": true,
    "run_marketing_trend": true
  }
}
```

GitHub's workflow-dispatch endpoint requires Actions repository permission `write` for a fine-grained token. A successful workflow-dispatch request returns HTTP `204 No Content`. The workflow run appears in GitHub Actions shortly afterward.

## Safe test and cutover

1. Keep the native GitHub schedule enabled while creating the external job, but do not wait for its daily 13:34 schedule yet.
2. Use cron-job.org's manual `Run now` / test function at a time other than 13:34 UTC. This avoids colliding with the native scheduled run.
3. Check the corresponding GitHub Actions run. It should have event `workflow_dispatch`, and jobs `douyin_full_discovery` and `marketing_trend` must both finish with `success`. Check that both emails arrive.
4. After this test succeeds, disable the native GitHub cron by removing only the `schedule:` trigger from `.github/workflows/crawler.yml` and committing the change. Keep `workflow_dispatch:` enabled.
5. Keep the cron-job.org recurring job enabled at 13:34 UTC. From then on it is the sole daily starter.

Do not activate the external job's recurring 13:34 schedule before the native schedule has been removed, otherwise both can run on the same day.

## What each success signal means

- cron-job.org HTTP 204 means GitHub accepted the dispatch request. It does not prove the jobs completed.
- GitHub Actions job conclusions `success` for both bot jobs confirm that both workflows completed through their email steps.
- Check the recipient inboxes to confirm actual delivery. A successful SMTP send does not guarantee inbox placement.

Do not share the GitHub token or store it in source code.
