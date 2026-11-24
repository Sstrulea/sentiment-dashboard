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

## George's calls (phase 5)

- `data/registry/apeluri_george.csv` — `data,banca,directie,probabilitate,motiv,nota`
  (banca: Fed ECB BoE BoJ BoC RBA RBNZ SNB; directie: hawkish dovish fara_opinie;
  probabilitate: integer 50-95, fara_opinie = 50; motiv: munca inflatie crestere comunicare
  risc pricing altul). Example: `2026-11-07,Fed,hawkish,65,inflatie,core PCE sticky`.
- `data/registry/valute_george.csv` — `data,tari,slabe,nota` (2 + 2 codes of the 8, e.g.
  `2026-11-07,USD GBP,JPY CHF,`).
- Outcome (pre-registered): bank calls — 2Y from the close of the first business day after the
  call date (Fri-Sun -> Monday) over 20 business days, hawkish = higher at the end, unchanged =
  excluded; currencies — from the first H.10 fixing after the date, 1/2/4 weeks vs the basket.
- A row counts only if it was committed before its window starts:
  `python scripts/registry/validate_calls.py` lists accepted / rejected rows with the reason.
