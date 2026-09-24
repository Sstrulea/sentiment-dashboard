"""Render the COT dashboard as a static HTML page."""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"
PUBLIC = ROOT / "public"
ARCHIVE = PUBLIC / "archive"
CONTRACTS_FILE = ROOT / "data" / "contracts.yaml"

SPARK_WIDTH = 80
SPARK_HEIGHT = 24
SPARK_POINTS = 156


def _ext_class(v: float | None) -> str:
    if v is None or pd.isna(v):
        return "ext-na"
    if v >= 0.95:
        return "ext-95"
    if v >= 0.70:
        return "ext-70"
    if v <= 0.05:
        return "ext-05"
    if v <= 0.30:
        return "ext-30"
    return "ext-neutral"


def _exp_class(long_pct: float | None) -> str:
    if long_pct is None or pd.isna(long_pct):
        return ""
    if long_pct > 0.70:
        return "exp-high-long"
    if (1 - long_pct) > 0.70:
        return "exp-high-short"
    return ""


def _fmt_pct(v: float | None) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v * 100:.0f}%"


def _fmt_int(v: float | None) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{int(round(v)):,}"


def _sparkline(series: pd.Series) -> str:
    """Return an inline SVG of the last ~156 weeks of a numeric series.

    Two filled areas: above zero in blue, below zero in red.
    """
    vals = series.dropna().tail(SPARK_POINTS).reset_index(drop=True)
    if len(vals) < 2:
        return ""
    n = len(vals)
    vmax = max(abs(vals.max()), abs(vals.min()))
    if vmax == 0:
        return ""
    xs = [i * (SPARK_WIDTH - 1) / (n - 1) for i in range(n)]
    mid = SPARK_HEIGHT / 2
    ys = [mid - (v / vmax) * (SPARK_HEIGHT / 2 - 1) for v in vals]

    # Build filled polygon going along the line and back along the midline
    # (so we get a clean shape), then clip into above/below halves via SVG.
    line_pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in zip(xs, ys))
    poly_above = " ".join(
        [f"{xs[0]:.2f},{mid:.2f}"] + [f"{x:.2f},{y:.2f}" for x, y in zip(xs, ys)] + [f"{xs[-1]:.2f},{mid:.2f}"]
    )
    return (
        f'<svg viewBox="0 0 {SPARK_WIDTH} {SPARK_HEIGHT}" width="{SPARK_WIDTH}" height="{SPARK_HEIGHT}" class="spark">'
        f'<defs>'
        f'<clipPath id="above"><rect x="0" y="0" width="{SPARK_WIDTH}" height="{mid:.2f}"/></clipPath>'
        f'<clipPath id="below"><rect x="0" y="{mid:.2f}" width="{SPARK_WIDTH}" height="{mid:.2f}"/></clipPath>'
        f'</defs>'
        f'<polygon points="{poly_above}" fill="#1976d2" fill-opacity="0.35" clip-path="url(#above)"/>'
        f'<polygon points="{poly_above}" fill="#d32f2f" fill-opacity="0.35" clip-path="url(#below)"/>'
        f'<line x1="0" y1="{mid:.2f}" x2="{SPARK_WIDTH}" y2="{mid:.2f}" stroke="#888" stroke-width="0.3"/>'
        f'<polyline points="{line_pts}" fill="none" stroke="#222" stroke-width="0.9"/>'
        f'</svg>'
    )


def _build_rows(snapshot: pd.DataFrame, history: pd.DataFrame) -> dict[str, list[dict]]:
    """Group latest-week snapshot rows by category for the template."""
    with open(CONTRACTS_FILE) as f:
        cfg = yaml.safe_load(f)

    # index the history by contract code for sparkline slicing
    hist_by_code: dict[str, pd.Series] = {
        code: g.sort_values("report_date_as_yyyy_mm_dd").assign(
            spec_net=lambda x: x["noncomm_positions_long_all"] - x["noncomm_positions_short_all"]
        )["spec_net"]
        for code, g in history.groupby("cftc_contract_market_code")
    }

    by_cat: dict[str, list[dict]] = {}
    snap_by_code = snapshot.set_index("cftc_contract_market_code").to_dict("index")

    for cat_key, cat in cfg["categories"].items():
        rows: list[dict] = []
        for inst in cat["instruments"]:
            code = inst["cftc_code"]
            r = snap_by_code.get(code)
            if r is None:
                rows.append(
                    {
                        "symbol": inst["symbol"],
                        "name": inst["name"],
                        "missing": True,
                    }
                )
                continue
            spark_series = hist_by_code.get(code, pd.Series(dtype=float))
            top3_spec = bool(r.get("spec_net_rank_in_cat", 99) <= 3)
            top3_comm = bool(r.get("comm_net_rank_in_cat", 99) <= 3)
            rows.append(
                {
                    "symbol": inst["symbol"],
                    "name": inst["name"],
                    "missing": False,
                    "spec_exp_long": r["spec_exp_long"],
                    "spec_exp_short": r["spec_exp_short"],
                    "spec_net": r["spec_net"],
                    "spec_ext_6m": r["spec_extreme_6m"],
                    "spec_ext_3y": r["spec_extreme_3y"],
                    "comm_exp_long": r["comm_exp_long"],
                    "comm_exp_short": r["comm_exp_short"],
                    "comm_net": r["comm_net"],
                    "comm_ext_6m": r["comm_extreme_6m"],
                    "comm_ext_3y": r["comm_extreme_3y"],
                    "spec_exp_long_class": _exp_class(r["spec_exp_long"]),
                    "comm_exp_long_class": _exp_class(r["comm_exp_long"]),
                    "spec_ext_6m_class": _ext_class(r["spec_extreme_6m"]),
                    "spec_ext_3y_class": _ext_class(r["spec_extreme_3y"]),
                    "comm_ext_6m_class": _ext_class(r["comm_extreme_6m"]),
                    "comm_ext_3y_class": _ext_class(r["comm_extreme_3y"]),
                    "spec_net_top3": top3_spec,
                    "comm_net_top3": top3_comm,
                    "spec_flipped": bool(r.get("spec_flipped", False)),
                    "comm_flipped": bool(r.get("comm_flipped", False)),
                    "sparkline": _sparkline(spark_series),
                    "fmt_spec_long_pct": _fmt_pct(r["spec_exp_long"]),
                    "fmt_spec_short_pct": _fmt_pct(r["spec_exp_short"]),
                    "fmt_comm_long_pct": _fmt_pct(r["comm_exp_long"]),
                    "fmt_comm_short_pct": _fmt_pct(r["comm_exp_short"]),
                    "fmt_spec_net": _fmt_int(r["spec_net"]),
                    "fmt_comm_net": _fmt_int(r["comm_net"]),
                    "fmt_spec_ext_6m": _fmt_pct(r["spec_extreme_6m"]),
                    "fmt_spec_ext_3y": _fmt_pct(r["spec_extreme_3y"]),
                    "fmt_comm_ext_6m": _fmt_pct(r["comm_extreme_6m"]),
                    "fmt_comm_ext_3y": _fmt_pct(r["comm_extreme_3y"]),
                }
            )
        by_cat[cat_key] = {"label": cat["label"], "rows": rows}
    return by_cat


def _build_flips(snapshot: pd.DataFrame) -> list[dict]:
    flips: list[dict] = []
    if snapshot.empty:
        return flips
    rows = snapshot[snapshot["any_flipped"].fillna(False)]
    for _, r in rows.iterrows():
        if r["spec_flipped"]:
            flips.append(
                {
                    "symbol": r["symbol"],
                    "name": r["name"],
                    "series": "Spec",
                    "extreme_value": r["spec_extreme_3y"],
                    "direction": "long" if r["spec_extreme_3y"] >= 0.95 else "short",
                }
            )
        if r["comm_flipped"]:
            flips.append(
                {
                    "symbol": r["symbol"],
                    "name": r["name"],
                    "series": "Comm",
                    "extreme_value": r["comm_extreme_3y"],
                    "direction": "long" if r["comm_extreme_3y"] >= 0.95 else "short",
                }
            )
    return flips


def _list_archive() -> list[str]:
    if not ARCHIVE.exists():
        return []
    return sorted(
        [p.stem for p in ARCHIVE.glob("*.html")],
        reverse=True,
    )


def render_dashboard(snapshot: pd.DataFrame, history: pd.DataFrame) -> Path:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    ARCHIVE.mkdir(parents=True, exist_ok=True)

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(["html"]),
    )
    # Allow raw SVG strings through.
    env.filters["safe"] = lambda x: x

    by_cat = _build_rows(snapshot, history)
    flips = _build_flips(snapshot)

    report_date = (
        pd.to_datetime(snapshot["report_date_as_yyyy_mm_dd"].iloc[0]).strftime("%Y-%m-%d")
        if not snapshot.empty
        else "unknown"
    )
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Write today's snapshot to archive first so the date picker sees it.
    archive_path = ARCHIVE / f"{report_date}.html"

    tpl = env.get_template("cot.html.j2")
    html = tpl.render(
        by_cat=by_cat,
        flips=flips,
        report_date=report_date,
        generated_at=generated_at,
        archive=_list_archive() + [report_date],
        active_page="cot",
    )

    # The COT page lives at /cot (public/cot.html); "/" redirects to /economic.
    cot_path = PUBLIC / "cot.html"
    cot_path.write_text(html, encoding="utf-8")
    archive_path.write_text(html, encoding="utf-8")

    # Copy CSS to public/.
    css_src = STATIC / "style.css"
    css_dst = PUBLIC / "style.css"
    if css_src.exists():
        shutil.copyfile(css_src, css_dst)

    return cot_path
