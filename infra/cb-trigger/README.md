# cb-trigger - the external trigger of the Central Banks pipeline (phase 4)

A Cloudflare Worker with one cron (every 5 minutes) that fires the GitHub workflows at the right moment, because GitHub's own schedules are late and drop ticks.
The goal: **at most 15 minutes from a rate decision to the site.**

| when | what the Worker dispatches | what the job does |
|---|---|---|
| the tick that is not later than *official time + 1 min* of each decision | `cb-refresh.yml` `event=decision bank=X date=D` | looks for the statement every 60 s for at most 15 min, stores it, then that bank's documents, the decisions, the statement's summary, the pages, and commits |
| BoJ (no fixed time): every tick in 11:30-13:30 JST | the same, at the first `/mopo/mpmdeci/` item of the BoJ RSS (or at the end of the window) | the same |
| *conference start + 2 h* | `cb-refresh.yml` `event=conference bank=X date=D` | the transcript and the video of that bank, then the summaries pending for it |
| every 2 hours at :37 (UTC) | `cb-refresh.yml` | the regular full run |
| every hour at :05 on weekdays, every 4 h at the weekend (UTC) | `econ-refresh.yml` | the regular economic-calendar run |

The times are not in the Worker. `cb-refresh` writes `data/cb/trigger_schedule.json` (stage `schedule`, from `data/cb/meetings.yaml` + `config/central_banks.yaml` +
`config/cb_trigger.yaml`: local time -> UTC through the bank's zone, so DST is the zone's) and the Worker reads it through the GitHub API (works for a private repository), keeping
the last good copy in KV. One dispatch per (event, date): a mark in KV. A dispatch that fails is retried 3 times (2 s, 6 s, 18 s); if it still fails the Worker opens **an issue** in
the repository (GitHub emails its owner) and tries again at the next tick until the event's grace period is over. The GitHub schedules that remain are only the backup: slower, and
their `guard` job skips them when a run started by the Worker succeeded recently.

## Files

- `src/schedule.js` - what is due at a tick (pure); `src/github.js` - the schedule file, the dispatch with retries, the issue; `src/index.js` - the tick.
- `test/*.test.mjs` - `node --test` (also run by `pytest tests/test_cb_trigger.py`); `test/e2e_local.mjs` - the Worker in the real runtime against a mock GitHub.
- `wrangler.toml` - name, cron, variables, the KV binding. No dependency: wrangler is run through `npx`.

## Tests, locally (no account, no token)

```
cd infra/cb-trigger
npm test                      # 19 unit tests (node --test)
node test/e2e_local.mjs       # wrangler dev + a mock GitHub: dispatch, BoJ RSS, regular run, dedupe
```

## One-time setup (George)

1. `cd infra/cb-trigger && npx wrangler login` (opens the browser; a free Cloudflare account is enough).
2. `npx wrangler kv namespace create STATE` - paste the printed `id` into `wrangler.toml` (`[[kv_namespaces]]`).
3. A **fine-grained** GitHub token, only for `Sstrulea/sentiment-dashboard`: Settings -> Developer settings -> Fine-grained tokens -> repository access "Only select repositories" ->
   permissions **Actions: Read and write**, **Contents: Read**, **Issues: Read and write** (nothing else). Then `npx wrangler secret put GH_TOKEN` and paste it when asked.
   The token lives only as a Worker secret - never in the repository, never in `wrangler.toml`.
4. `npx wrangler deploy`. Check: `npx wrangler tail` shows one line per tick (`{"action":"idle",...}` when nothing is due).

## Watching it

- `npx wrangler tail` - live: what each tick dispatched, retried, skipped.
- `python -m src.cb_collect --status` and the "How is this calculated?" panel of each bank page: the measured delay of every decision (`first_seen_at` - official time), target <= 15 min.
- Cost: the free plan (100,000 requests a day; this is ~288 a day; KV a few dozen writes a day against 1,000).

## Changing what it does

Everything is in `config/cb_trigger.yaml` (lead / grace, the BoJ RSS, the regular cadence, the latency target, `measure_from`); the next `cb-refresh` regenerates the schedule file. The cron itself
(`*/5 * * * *`) is in `wrangler.toml` and must stay in step with `tick_minutes`.
