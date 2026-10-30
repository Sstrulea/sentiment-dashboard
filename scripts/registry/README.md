# Registry v0 (prospective, manual)

Records what each score says **before** the outcome is known, then scores it when due.
No cron, no UI.

Weekly, on Friday after the close (after the US session, ~22:00 UTC):

```bash
cd ~/projects/macro-dev
.venv/bin/python scripts/registry/snapshot.py          # today's v3 / macro_momentum / carry ranks
.venv/bin/python scripts/registry/score.py             # fills realized basket returns for due calls
```

- `snapshot.py` reads the production payload `public/data/economic.json` (v3 `score_v3`,
  read only), `data/macro_monthly.parquet` (refresh first with `scripts/fetch_oecd_macro.py`
  when OECD publishes) and `data/backbone_rates.parquet` (policy rates; refresh with
  `scripts/fetch_backbone.py`). Re-running on the same day replaces that day's rows.
- `score.py` pulls FRED H.10 (keyless) and fills `realized_bp` for calls whose due date
  (asof + 2/4/8/13 weeks) has passed; `--offline` uses the backbone parquet instead.
- Output: `data/registry/calls.parquet` (asof, source, ccy, score, rank, horizon, due,
  realized_bp). Nothing here feeds production.
