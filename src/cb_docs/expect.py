"""When the follow-up documents of a decision are due (lags measured in 0B) - shared by the collector's --status warnings and the payloads."""
from __future__ import annotations

GRACE_DAYS = 2
EXPECTED_LAG = {                                       # (currency, type) -> days after the decision
    ("USD", "minutes"): 21, ("EUR", "account"): 35, ("GBP", "minutes"): 0, ("JPY", "summary_of_opinions"): 14, ("JPY", "minutes"): 60,
    ("CAD", "deliberations"): 14, ("AUD", "minutes"): 14, ("CHF", "deliberations"): 28,
}
TYPE_LABEL = {"statement": "Statement", "minutes": "Minutes", "account": "Account", "summary_of_opinions": "Summary of opinions",
              "deliberations": "Summary of deliberations", "presser_transcript": "Press conference transcript", "presser_video": "Press conference video",
              "opening_statement": "Introductory statement", "speech": "Speech", "testimony": "Testimony"}

VIDEO_NA = {                                           # why a meeting has no press-conference video link (the YouTube feeds are not read: robots.txt)
    "USD": "the FOMC press-conference page carries no video for this meeting",
    "EUR": "the ECB page shows only the last press conference: this one was not current when the collector ran",
    "GBP": "no Monetary Policy Report press conference at this meeting (the BoE holds a pooled broadcast interview instead)",
    "JPY": "the BoJ pages link only to its YouTube channel; the per-meeting video needs the YouTube feed, which robots.txt disallows",
    "CAD": "the press release links no press-conference page",
    "AUD": "the RBA transcript page carries no video link for this media conference",
    "CHF": "the SNB links only to its YouTube channel; the per-meeting video needs the YouTube feed, which robots.txt disallows",
    "NZD": "the RBNZ site is behind a Cloudflare challenge; its videos are only on YouTube",
}
