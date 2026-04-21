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
