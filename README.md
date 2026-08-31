# COT Dashboard

Static weekly dashboard for CFTC COT (Legacy Combined) — Large Speculators vs Commercials, exposures, net positions, and trailing percentile ranks for ~55 instruments. Rebuilt every Sunday 04:00 UTC by GitHub Actions and deployed to Vercel.

## Local setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Workflow

```bash
# 1. First-time sanity check: verify that every cftc_code in data/contracts.yaml
#    resolves to an active contract in the CFTC API.
python -m src.list_contracts

# 2. Fetch 3 years of history, compute metrics, and render public/index.html.
python -m src.main

# 3. Run tests.
pytest tests/
```

Open `public/index.html` in a browser.

## Deploy

- Push this repo to GitHub. The `.github/workflows/weekly.yml` workflow runs on Sunday 04:00 UTC (also triggerable manually) and commits the refreshed `data/history.parquet` plus `public/`.
- Import the repo in Vercel. `vercel.json` points to `public/` as the static output directory. Every push to `main` auto-deploys.

## Access control

The site is gated behind a shared-token login via `middleware.js` (Vercel Routing
Middleware, runs on every request). Configure it with a `DASH_TOKENS` env var in the
Vercel project settings (Production/Preview/Development as needed):

```
DASH_TOKENS=alice:s3cret-1,bob:another-pass
```

- Format: comma-separated `name:password` pairs.
- Passwords cannot contain a comma (it's the pair separator).
- If `DASH_TOKENS` is unset or empty, the whole site fails closed and returns 503 —
  there is no bypass.
- Changing the env var requires a redeploy to take effect (Vercel env vars are read
  at request time by the middleware function, but Vercel only picks up changes on
  the next deploy — trigger one from the dashboard or push a commit after editing).
- Never commit tokens to the repo; set them only via the Vercel dashboard/CLI.

## File layout

```
src/            Python: fetch, compute, render, CLI helpers
data/           contracts.yaml (instrument list) + history.parquet (cache)
templates/      Jinja2 dashboard template
static/         CSS (copied into public/ at render time)
public/         Static site output (committed)
public/archive/ Weekly snapshots by report date
tests/          pytest unit tests on compute.py
```
