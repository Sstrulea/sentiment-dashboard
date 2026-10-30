"""Node registry. A node function gets a NodeContext and returns a NodeResult;
it reads raw series only through ctx.series (avail <= asof) and other nodes only
through ctx.dep, so nothing after asof can reach it.

    @node("chg")
    def chg(ctx): ...

The YAML `fn` names a registered function; `inputs` are raw series names or ids
of other nodes; `params` go to the function unchanged."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from .data import Inputs

STATUSES = ("context", "filter", "signal")
QUALITY_OK, QUALITY_MISSING = "ok", "missing"
Z_WINDOW_DAYS = 730          # rolling 2y z-score
Z_MIN_OBS = 250              # ~1 trading year inside the window

REGISTRY: dict[str, Callable[["NodeContext"], "NodeResult"]] = {}


def node(fn_id: str):
    def deco(fn):
        if fn_id in REGISTRY:
            raise ValueError(f"node fn {fn_id!r} registered twice")
        REGISTRY[fn_id] = fn
        return fn
    return deco


@dataclass
class NodeResult:
    id: str
    value: float | None
    z: float | None
    asof: str
    source_last_date: str | None
    quality: str                     # ok | missing
    status: str                      # context | filter | signal
    meta: dict = field(default_factory=dict)

    @property
    def missing(self) -> bool:
        return self.quality == QUALITY_MISSING

    def to_dict(self) -> dict:
        return asdict(self)


class NodeContext:
    def __init__(self, spec: dict, inputs: Inputs, asof: pd.Timestamp, deps: dict[str, NodeResult]):
        self.spec = spec
        self.params = spec.get("params") or {}
        self.inputs = inputs
        self.asof = pd.Timestamp(asof)
        self.deps = deps

    # --- reading -------------------------------------------------------------
    def series(self, name: str, lookback_days: int | None = None) -> pd.Series:
        return self.inputs.asof(name, self.asof, lookback_days)

    def dep(self, node_id: str) -> NodeResult:
        return self.deps[node_id]

    @property
    def lookback(self) -> int:
        """Raw history a z-scored node needs: the z window plus room for the transform."""
        return int(self.params.get("z_window_days", Z_WINDOW_DAYS)) + 200

    # --- results -------------------------------------------------------------
    def ok(self, value, z, source_last, **meta) -> NodeResult:
        return NodeResult(self.spec["id"], _f(value), _f(z), self.asof.strftime("%Y-%m-%d"),
                          _d(source_last), QUALITY_OK, self.spec["status"], meta)

    def missing(self, reason: str, source_last=None, **meta) -> NodeResult:
        return NodeResult(self.spec["id"], None, None, self.asof.strftime("%Y-%m-%d"),
                          _d(source_last), QUALITY_MISSING, self.spec["status"], {"reason": reason, **meta})

    def z_of(self, derived: pd.Series) -> float | None:
        """z of the last value of a derived daily series within the rolling window."""
        derived = derived.dropna()
        if derived.empty:
            return None
        win = int(self.params.get("z_window_days", Z_WINDOW_DAYS))
        w = derived[derived.index > derived.index[-1] - pd.Timedelta(days=win)]
        if len(w) < int(self.params.get("z_min_obs", Z_MIN_OBS)):
            return None
        sd = float(w.std(ddof=1))
        return None if not np.isfinite(sd) or sd == 0 else float((w.iloc[-1] - w.mean()) / sd)


def _f(x):
    if x is None:
        return None
    x = float(x)
    return round(x, 6) if np.isfinite(x) else None


def _d(x):
    return None if x is None else pd.Timestamp(x).strftime("%Y-%m-%d")


def _finish(ctx: NodeContext, derived: pd.Series) -> NodeResult:
    """Value = last derived observation. Without enough history for the z window
    the value stays usable (thresholds on raw units) and z is None with a note;
    a z-based primary or modifier then skips the node."""
    derived = derived.dropna()
    if derived.empty:
        return ctx.missing("no observations yet")
    z = ctx.z_of(derived)
    if z is None:
        return ctx.ok(derived.iloc[-1], None, derived.index[-1],
                      z_note=f"short history for z ({len(derived)} obs)")
    return ctx.ok(derived.iloc[-1], z, derived.index[-1])


def _one_input(ctx: NodeContext) -> pd.Series:
    (name,) = ctx.spec["inputs"]
    return ctx.series(name, ctx.lookback)


# ---------------------------------------------------------------------------
# Generic node functions
# ---------------------------------------------------------------------------

def transform_change(s: pd.Series, n: int, mode: str) -> pd.Series:
    if mode == "diff_bp":            # yields / spreads in percent -> basis points
        return (s - s.shift(n)) * 100.0
    if mode == "diff":
        return s - s.shift(n)
    if mode == "log_pct":            # non-positive prices dropped (WTI 2020-04-20)
        s = s[s > 0]
        return np.log(s / s.shift(n)) * 100.0
    if mode == "pct":
        return (s / s.shift(n) - 1.0) * 100.0
    raise ValueError(f"unknown change mode {mode!r}")


@node("chg")
def chg(ctx: NodeContext) -> NodeResult:
    """n-observation change of one series; params n, mode."""
    s = _one_input(ctx)
    if s.empty:
        return ctx.missing("no data at asof")
    return _finish(ctx, transform_change(s, int(ctx.params["n"]), ctx.params.get("mode", "diff")))


@node("level")
def level(ctx: NodeContext) -> NodeResult:
    s = _one_input(ctx)
    if s.empty:
        return ctx.missing("no data at asof")
    return _finish(ctx, s)


@node("ratio")
def ratio(ctx: NodeContext) -> NodeResult:
    """inputs [a, b] -> a / b on the common dates."""
    a, b = (ctx.series(n, ctx.lookback) for n in ctx.spec["inputs"])
    if a.empty or b.empty:
        return ctx.missing("no data at asof for " + ", ".join(
            n for n, x in zip(ctx.spec["inputs"], (a, b)) if x.empty))
    j = pd.concat([a, b], axis=1, join="inner").dropna()
    j = j[j.iloc[:, 1] != 0]
    return _finish(ctx, j.iloc[:, 0] / j.iloc[:, 1])


@node("rolling_corr")
def rolling_corr(ctx: NodeContext) -> NodeResult:
    """inputs [a, b]; params window, a_mode, b_mode: correlation of the 1-obs
    changes of a and b over the last `window` common dates."""
    win = int(ctx.params["window"])
    a, b = (ctx.series(n, ctx.lookback + 2 * win) for n in ctx.spec["inputs"])
    if a.empty or b.empty:
        return ctx.missing("no data at asof")
    j = pd.concat([a, b], axis=1, join="inner").dropna()
    da = transform_change(j.iloc[:, 0], 1, ctx.params.get("a_mode", "log_pct"))
    db = transform_change(j.iloc[:, 1], 1, ctx.params.get("b_mode", "diff_bp"))
    c = pd.concat([da, db], axis=1).dropna()
    corr = c.iloc[:, 0].rolling(win, min_periods=win).corr(c.iloc[:, 1])
    return _finish(ctx, corr)


@node("rolling_pctile")
def rolling_pctile(ctx: NodeContext) -> NodeResult:
    """Percentile (0-100) of the last value inside a rolling window of the series
    itself; params window_days, min_obs. z is the z of the value in the same window."""
    win = int(ctx.params["window_days"])
    s = ctx.series(ctx.spec["inputs"][0], win + 30).dropna()
    if s.empty:
        return ctx.missing("no data at asof")
    w = s[s.index > s.index[-1] - pd.Timedelta(days=win)]
    if len(w) < int(ctx.params.get("min_obs", 1)):
        return ctx.missing(f"short history for percentile ({len(w)} obs)", s.index[-1])
    last = w.iloc[-1]
    pct = 100.0 * float((w <= last).sum()) / len(w)
    sd = float(w.std(ddof=1))
    z = (last - w.mean()) / sd if sd > 0 else None
    return ctx.ok(pct, z, s.index[-1], raw=_f(last), window_obs=len(w))


@node("abs_gt_flag")
def abs_gt_flag(ctx: NodeContext) -> NodeResult:
    """1.0 when |value of the input node| > params.threshold, else 0.0."""
    d = ctx.dep(ctx.spec["inputs"][0])
    if d.missing:
        return ctx.missing(f"input {d.id} missing")
    on = abs(d.value) > float(ctx.params["threshold"])
    meta = {"flag": ctx.params.get("label")} if on else {}
    return ctx.ok(1.0 if on else 0.0, None, d.source_last_date, **meta)


@node("unavailable")
def unavailable(ctx: NodeContext) -> NodeResult:
    """Placeholder for a node without an official keyless source (params.reason)."""
    return ctx.missing(ctx.params.get("reason", "no official keyless source"))
