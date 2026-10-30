# Registry of pre-registered attempts

Every pre-registered measurement gets a number here, whatever its outcome. A
change to a pre-registration after its first measurement is a new attempt.

| # | id | branch | pre-registration | tests run | outcome |
|---|---|---|---|---|---|
| 1 | tree_jpy_v0 (JPY decision tree v0) | feat/tree-engine | 8e18ace4 `scripts/measure/prereg_tree_jpy_v0.yml` | 5 horizons x signal + 4 cells x 5 horizons x filter, primary window 2016 | no horizon promoted (2w IC −0.004, t 0.06); no cell promoted; stays context. Report docs/eval/tree-jpy-v0.md |
| 2 | reaction_v0 (rates backbone + reaction layer) | feat/reaction-layer | e7cd96f8 `scripts/measure/prereg_reaction_v0.yml` | 15 gated (H1 2, H2 4, H3 9) + 5 descriptive (H2 discrete 4, H4 1) | 1 of 15 gated passes: H3 GBP (net +17.6 bp, t 2.57, N 36) -> filter by the rule; H1 fails (first half empty); H2 fails with the opposite sign (digest_fx IC -0.07 at 2w). Report docs/eval/reaction-v0.md |
