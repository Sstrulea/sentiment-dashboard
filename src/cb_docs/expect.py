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
