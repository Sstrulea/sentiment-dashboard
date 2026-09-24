# Phone view v1 — design confirmed on 24 Sep 2026

Static references for Faza 11 (phone, max-width 600 px). Drawn at 390 px on the real payloads of 24 Sep 2026,
dark theme. The HTML uses inline styles only as a visual reference: build it with the site's theme variables so
light and dark both work. Every number in these files comes from the payloads (economic.json, cb/overview.json,
cb/usd.json, carry.json, the COT payload of 15 Sep).

| File | Screen |
|---|---|
| Economic.html / .png | Economic list, first visit (banner shown) |
| Economic-detail.html / .png | Tap on AUD/JPY: the detail sheet, starting with "What moves the score" |
| Central-Banks.html / .png | Central Banks, Banks tab |
| Bank-page.html / .png | The USD bank page on the phone |
| Currency-Strength.html / .png | Ranked list + divergence matrix |
| Carry.html / .png | Carry list |
| COT.html / .png | COT on the phone: the 24 Sep design (docs/design/cot-v2/Mobile.html) with the header folded |
| Shared.html / .png | Every page: navbar with the active tab in view, first-visit banner, "How to read" closed and open, filter sheet |

## Rules the boards follow

- Phone = max-width 600 px. 601–1024 keeps the desktop layout: wide tables scroll inside their container with the
  first column sticky; no table turns into stacked cards. Two-row navbar with scrollable tabs below 900 px.
- 16 px side margins, 40 px controls, touch targets of at least 44 px, H1 22 px, body 13 px, list rows 44–52 px,
  card radius 12, typographic minus (−) in numbers.
- Colours: ScorePalette.gradientStyle. Economic score chip: scale 6 (Math.round for the integer, as fmtScoreInt);
  indicator cells: 3; COT: 4; Central Banks "Priced in": 25 bp; Strength matrix: 2.5; Carry bars: scale_pp.
- "What moves the score" = the pair's `contributions` summed per category (category null = COT,
  monetary = "Monetary · 2Y"), zero groups left out, sorted by absolute value. The bars add up to `score` exactly.
- Central Banks "in N d" = whole days to `next.time.utc` (BoJ without a time: the date).
