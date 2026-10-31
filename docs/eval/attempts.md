# Registry of pre-registered attempts

Every pre-registered measurement gets a number here, whatever its outcome. A
change to a pre-registration after its first measurement is a new attempt.

| # | id | branch | pre-registration | tests run | outcome |
|---|---|---|---|---|---|
| 1 | tree_jpy_v0 (JPY decision tree v0) | feat/tree-engine | 8e18ace4 `scripts/measure/prereg_tree_jpy_v0.yml` | 5 horizons x signal + 4 cells x 5 horizons x filter, primary window 2016 | no horizon promoted (2w IC −0.004, t 0.06); no cell promoted; stays context. Report docs/eval/tree-jpy-v0.md |
| 2 | reaction_v0 (rates backbone + reaction layer) | feat/reaction-layer | e7cd96f8 `scripts/measure/prereg_reaction_v0.yml` | 15 gated (H1 2, H2 4, H3 9) + 5 descriptive (H2 discrete 4, H4 1) | 1 of 15 gated passes: H3 GBP (net +17.6 bp, t 2.57, N 36) -> filter by the rule; H1 fails (first half empty); H2 fails with the opposite sign (digest_fx IC -0.07 at 2w). Report docs/eval/reaction-v0.md |
| 3 | phase3 (regime v1, macro_momentum 2000-2026, attempt #3 GNBP episodes, stage H) | feat/macro-momentum | 4aead6a8 `scripts/measure/prereg_phase3.yml` | 10 gated: C 4 + C secondary 2 + E 2 + H 2; the rest descriptive | 0 of 10 pass: macro_momentum IC 0.007 at 2w (t 0.51), below carry; GNBP episodes vs control +18.9 bp at 4w, t 0.68 (the phase-2 t 3.39 came from overlap); H 8w IC 0.064 t 1.82, 13w 0.049 t 1.36. Report docs/eval/phase3.md |
| 4 | phase4 (TFF, pre-meeting drift, Fed shock type, REER, carry 26w, crowding filter; MS baseline; v4 score; "better than the market") | feat/v4-score | `scripts/measure/prereg_phase4.yml` (hash in the results commit) | 12 gated: A 10 + D 2 | pending. **Last round of historical search: after it, no new hypothesis is tested on the same data.** |
