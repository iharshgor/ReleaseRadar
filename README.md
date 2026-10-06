# ReleaseRadar

[![Feed Check](https://github.com/iharshgor/ReleaseRadar/actions/workflows/feed_check.yml/badge.svg)](https://github.com/iharshgor/ReleaseRadar/actions/workflows/feed_check.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)

A serverless RSS monitor that posts new movie releases to Discord. It runs on GitHub Actions and stores its state in the repository, so it needs no hosting.

## Architecture

```
GitHub Actions (every 12 hours (00:00 and 12:00 UTC) or manual)
  1. Fetch feed (feedparser)
  2. Parse HTML description (BeautifulSoup + regex)
  3. Compare IDs with data/seen_items.json
  4. Post new items to Discord, oldest first
  5. Commit updated seen_items.json (git-auto-commit-action)
```

- **Deduplication:** each entry's `id` (or `link`) is stored in `data/seen_items.json`. Only the newest 500 IDs are kept.
- **Delivery guarantee:** an ID is recorded only after Discord returns HTTP 200 or 204. Failed items are retried on the next run.
- **Secret safety:** the webhook URL is read from the environment and is never logged. Failures log only a status code or exception type.
- **Dry-run mode:** without `DISCORD_WEBHOOK_URL`, parsed embeds are logged and no state is written.

## Local setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 -m unittest discover -s tests   # run tests
python3 notifier.py                     # dry run
DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..." python3 notifier.py   # live
```

`FEED_URL` can be overridden through the environment (default `https://atlas.rssly.org/feed`).

## GitHub setup

1. In the repository, go to **Settings > Secrets and variables > Actions > New repository secret**.
2. Add `DISCORD_WEBHOOK_URL` with your Discord channel webhook URL.
3. Under **Settings > Actions > General > Workflow permissions**, allow read and write access (the workflow also declares `contents: write`).
4. Run the workflow once from the **Actions** tab using **Run workflow**.

Note: the first live run with an empty state file sends a notification for every item currently in the feed.

## License

MIT. See [LICENSE](LICENSE).
