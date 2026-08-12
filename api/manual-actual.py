"""Manual Actuals Panel — write endpoint (feat/manual-actuals-panel, Phase B).

Persists by committing an updated data/manual_actuals_overrides.json via the
GitHub Contents API, per the chosen persistence design: this repo has no
server beyond static hosting, and every other data file in this pipeline is
already "committed file is the source of truth, CI/a script is the only
writer" — this keeps the same shape instead of introducing a database.

SAFETY BOUNDARY: this endpoint's own checks are best-effort UX only. The real
guarantee — an override can never override a trusted (real, non-null) actual,
even a stale or malicious one that slipped past this endpoint — lives in
`src.manual_actuals.apply_overrides`, which re-derives eligibility from the FF
parquet at EVERY render, not from anything recorded here or at submit time.
This endpoint is intentionally decoupled from that module (no import of
`src.manual_actuals`/`ff_scoring`/etc.) to keep the function bundle light and
the field list below is a deliberate, small duplication of
`manual_actuals.OVERRIDE_COLUMNS` — not a second source of truth for the
merge logic, just the shape of what gets committed.

Auth: shared-secret token (MANUAL_ACTUALS_TOKEN) compared to the
X-Manual-Token request header — chosen over full user auth (OAuth/Sign in
with Vercel) because this panel has exactly one operator; a stolen or leaked
token is still bounded by the SAFETY BOUNDARY above, not by this check.

Required environment variables (Vercel project settings):
  MANUAL_ACTUALS_TOKEN         shared secret the dashboard sends back
  MANUAL_ACTUALS_GITHUB_TOKEN  fine-grained GitHub token with Contents:
                                Read/write AND Actions: Read/write on this
                                repo (the latter is for the refresh dispatch
                                below — contents-only tokens can commit but
                                get a 403 on workflow_dispatch)
  MANUAL_ACTUALS_GITHUB_REPO   "owner/repo", e.g. "Sstrulea/sentiment-dashboard"
  MANUAL_ACTUALS_GITHUB_BRANCH optional, defaults to "main"

Refresh dispatch: a successful commit here doesn't make it into
public/data/economic.json until econ-refresh.yml runs — normally only on
the hourly schedule. To close that gap, _handle triggers econ-refresh.yml
via workflow_dispatch immediately AFTER _commit_override returns (never
before/in parallel — dispatching first risks the run checking out the repo
before the new commit lands, rendering stale data and reproducing the exact
"submit looked ignored" symptom this exists to fix). The dispatch is
best-effort: its failure is reported via the `refresh_triggered` response
flag but never fails the request or implies the commit didn't happen.
Bursts of submits are deduplicated by econ-refresh.yml's own
`concurrency: {group: econ-refresh, cancel-in-progress: false}` — GitHub
auto-cancels a stale queued run when a newer one is queued behind it, so no
separate debounce is implemented here.
"""
from __future__ import annotations

import base64
import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"
OVERRIDES_PATH_IN_REPO = "data/manual_actuals_overrides.json"

REQUIRED_FIELDS = ("canonical_id", "currency", "indicator_key", "datetime_utc",
                   "actual", "state_resolved")
VALID_STATES = ("MISSING", "ZERO_CONFIRM")
NOTE_MAX = 500
ENTERED_BY_MAX = 80
REFRESH_WORKFLOW_FILE = "econ-refresh.yml"


class _HttpError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _entry_key(e: dict) -> tuple:
    try:
        dt = str(pd.Timestamp(e.get("datetime_utc")))
    except Exception:
        dt = e.get("datetime_utc")   # malformed entry -> fall back to raw compare
    return (e.get("canonical_id"), dt)


class handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        try:
            self._handle()
        except _HttpError as e:
            self._send_json(e.status, {"error": e.message})
        except Exception as e:  # noqa: BLE001 — never leak a raw traceback to the client
            self._send_json(500, {"error": "internal error"})
            self.log_error("manual-actual: unhandled %r", e)

    def do_OPTIONS(self) -> None:
        # Same-origin only (the dashboard and this function share a domain) —
        # no CORS headers needed, this isn't meant to be called cross-site.
        self.send_response(204)
        self.end_headers()

    # ---- request handling ---------------------------------------------------

    def _handle(self) -> None:
        token = os.environ.get("MANUAL_ACTUALS_TOKEN")
        if not token:
            raise _HttpError(500, "MANUAL_ACTUALS_TOKEN not configured on the server")
        if self.headers.get("X-Manual-Token") != token:
            raise _HttpError(401, "unauthorized")

        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            raise _HttpError(400, "invalid JSON body")

        entry = self._validate(payload)
        self._sanity_check_still_actionable(entry["canonical_id"], entry["datetime_utc"])
        gh_token, repo, branch = self._github_config()
        commit_url = self._commit_override(gh_token, repo, branch, entry)
        refresh_triggered = self._trigger_refresh(gh_token, repo, branch)
        self._send_json(200, {
            "ok": True,
            "entry": entry,
            "commit": commit_url,
            "refresh_triggered": refresh_triggered,
        })

    def _validate(self, payload: dict) -> dict:
        for f in REQUIRED_FIELDS:
            if payload.get(f) in (None, ""):
                raise _HttpError(400, f"missing field: {f}")
        try:
            actual = float(payload["actual"])
        except (TypeError, ValueError):
            raise _HttpError(400, "actual must be a number")
        if payload["state_resolved"] not in VALID_STATES:
            raise _HttpError(400, "state_resolved must be MISSING or ZERO_CONFIRM")
        try:
            dt_norm = str(pd.Timestamp(payload["datetime_utc"]))
        except Exception:
            raise _HttpError(400, "datetime_utc is not a valid timestamp")

        return {
            "canonical_id": str(payload["canonical_id"]),
            "currency": str(payload["currency"]),
            "indicator_key": str(payload["indicator_key"]),
            "datetime_utc": dt_norm,
            "actual": actual,
            "state_resolved": payload["state_resolved"],
            "entered_by": str(payload.get("entered_by") or "unknown")[:ENTERED_BY_MAX],
            "entered_at": _now_iso(),
            "note": str(payload.get("note") or "")[:NOTE_MAX],
        }

    def _sanity_check_still_actionable(self, canonical_id: str, datetime_utc: str) -> None:
        """Cheap, best-effort only (see module docstring) — gives the operator
        an immediate error instead of a silently-ignored commit, for the
        common cases: typo'd id/time, or the row already resolved with a
        real (non-zero) actual since the panel was last rendered."""
        if not FF_PARQUET.exists():
            return
        ff = pd.read_parquet(FF_PARQUET, columns=["canonical_id", "datetime_utc", "actual"])
        dt = pd.Timestamp(datetime_utc)
        match = ff[(ff["canonical_id"] == canonical_id) & (ff["datetime_utc"] == dt)]
        if match.empty:
            raise _HttpError(404, "no matching row in the FF parquet for that canonical_id/datetime_utc")
        row_actual = match.iloc[0]["actual"]
        if pd.notna(row_actual) and row_actual != 0.0:
            raise _HttpError(409, "this row already has a real actual — refusing to overwrite")

    # ---- GitHub Contents API commit ------------------------------------------

    def _github_config(self) -> tuple[str, str, str]:
        gh_token = os.environ.get("MANUAL_ACTUALS_GITHUB_TOKEN")
        repo = os.environ.get("MANUAL_ACTUALS_GITHUB_REPO")
        branch = os.environ.get("MANUAL_ACTUALS_GITHUB_BRANCH", "main")
        if not gh_token or not repo:
            raise _HttpError(500, "MANUAL_ACTUALS_GITHUB_TOKEN / MANUAL_ACTUALS_GITHUB_REPO not configured")
        return gh_token, repo, branch

    def _commit_override(self, gh_token: str, repo: str, branch: str, entry: dict) -> str:
        api_url = f"https://api.github.com/repos/{repo}/contents/{OVERRIDES_PATH_IN_REPO}"
        headers = {"Authorization": f"Bearer {gh_token}", "Accept": "application/vnd.github+json"}

        r = requests.get(api_url, headers=headers, params={"ref": branch}, timeout=15)
        if r.status_code == 200:
            data = r.json()
            sha = data["sha"]
            current = json.loads(base64.b64decode(data["content"]))
        elif r.status_code == 404:
            sha = None
            current = []
        else:
            raise _HttpError(502, f"GitHub read failed: {r.status_code} {r.text[:200]}")

        # Compare by PARSED timestamp, not raw string: an entry already in the
        # file may have been stored with a different (but equal) datetime_utc
        # format than str(pd.Timestamp(...)) produces here — same robustness
        # as src.manual_actuals's own key matching.
        key = _entry_key(entry)
        current = [e for e in current if _entry_key(e) != key]
        current.append(entry)
        new_content = json.dumps(current, indent=1) + "\n"

        body = {
            "message": ("manual-actuals: {canonical_id} @ {datetime_utc} "
                       "({state_resolved}) by {entered_by}").format(**entry),
            "content": base64.b64encode(new_content.encode()).decode(),
            "branch": branch,
        }
        if sha:
            body["sha"] = sha

        pr = requests.put(api_url, headers=headers, json=body, timeout=15)
        if pr.status_code not in (200, 201):
            raise _HttpError(502, f"GitHub commit failed: {pr.status_code} {pr.text[:200]}")
        return (pr.json().get("commit") or {}).get("html_url", "")

    # ---- refresh dispatch (best-effort, only after the commit is confirmed) --

    def _trigger_refresh(self, gh_token: str, repo: str, branch: str) -> bool:
        """Called only after _commit_override has returned — never before,
        never concurrently — so the workflow run this triggers always checks
        out a repo that already has the new commit. Swallows every failure:
        a broken dispatch must not turn an already-successful commit into an
        error response."""
        url = (f"https://api.github.com/repos/{repo}/actions/workflows/"
               f"{REFRESH_WORKFLOW_FILE}/dispatches")
        headers = {"Authorization": f"Bearer {gh_token}", "Accept": "application/vnd.github+json"}
        try:
            r = requests.post(url, headers=headers, json={"ref": branch}, timeout=10)
        except requests.RequestException as e:
            self.log_error("manual-actual: refresh dispatch failed: %r", e)
            return False
        if r.status_code != 204:
            self.log_error("manual-actual: refresh dispatch rejected: %s %s",
                            r.status_code, r.text[:200])
            return False
        return True

    # ---- response -------------------------------------------------------------

    def _send_json(self, status: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
