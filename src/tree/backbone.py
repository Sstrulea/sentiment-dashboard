"""Backbone of the reaction layer: data/backbone_rates.parquet (scripts/fetch_backbone.py)
plus the pre-registered alignment rules (scripts/measure/prereg_reaction_v0.yml).

Alignment
  - a release at UTC time t moves a currency's 2Y on the first LOCAL bond-market
    close after t: the first observation date d of Y2_<CCY> whose close time
    (CLOSES[ccy] on date d, local time zone) is > t; the move is y[d] - y[prev obs]
  - the FX move is the first H.10 noon New York fixing after t, minus the
    previous fixing
  - US control of a non-USD 2Y move: the US 2Y daily move whose US close is
    nearest in time to the currency's close (Tokyo / Sydney -> previous US day,
    Europe / Toronto -> same US day)
Basket: for each currency, its log value in USD minus the equal-weight mean of
the other seven (USD included, log value 0)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
BACKBONE_FILE = ROOT / "data" / "backbone_rates.parquet"
CCYS = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF"]

# Local close of each 2Y series (pre-registered; the time the official fixing refers to)
CLOSES = {
    "USD": ("15:30", "America/New_York"),     # H.15 CMT, ~3:30 pm bid-side quotes
    "EUR": ("17:30", "Europe/Berlin"),        # ECB AAA curve, end-of-day prices
    "GBP": ("16:30", "Europe/London"),        # BoE GLC, close of business
    "JPY": ("15:00", "Asia/Tokyo"),           # MoF / JSDA reference prices, 15:00
    "CAD": ("16:00", "America/Toronto"),      # BoC benchmark, closing mid-market
    "AUD": ("16:30", "Australia/Sydney"),     # RBA F2, closing yields
    "NZD": ("16:30", "Pacific/Auckland"),     # RBNZ B2 (no data: Cloudflare wall)
    "CHF": ("17:00", "Europe/Zurich"),        # SNB rendoblid
}
H10_FIX = ("12:00", "America/New_York")       # FRED H.10 noon buying rates
MAX_GAP_DAYS = 5                              # a move across a longer data gap is not used


def close_times(dates: pd.DatetimeIndex, hhmm: str, tz: str) -> np.ndarray:
    """UTC instants (naive, ns) of the local close on each date."""
    h, m = (int(x) for x in hhmm.split(":"))
    local = (pd.DatetimeIndex(dates).normalize() + pd.Timedelta(hours=h, minutes=m)).tz_localize(
        tz, nonexistent="shift_forward", ambiguous=False)
    return local.tz_convert("UTC").tz_localize(None).values


class AlignedSeries:
    """One daily series with the UTC instant each observation refers to."""

    def __init__(self, s: pd.Series, hhmm: str, tz: str):
        s = s.dropna().sort_index()
        self.s = s
        self.dates = s.index.values
        self.vals = s.to_numpy(float)
        self.instants = close_times(s.index, hhmm, tz)

    def __len__(self):
        return len(self.s)

    def first_after(self, t: pd.Timestamp) -> int:
        """Position of the first observation whose instant is > t (len if none)."""
        return int(np.searchsorted(self.instants, np.datetime64(pd.Timestamp(t), "ns"), side="right"))

    def move_at(self, k: int) -> tuple[float, pd.Timestamp | None]:
        """(value[k] - value[k-1], date k); NaN when k is out of range or the
        previous observation is more than MAX_GAP_DAYS earlier."""
        if k <= 0 or k >= len(self.vals):
            return float("nan"), None
        gap = (self.dates[k] - self.dates[k - 1]) / np.timedelta64(1, "D")
        if gap > MAX_GAP_DAYS:
            return float("nan"), pd.Timestamp(self.dates[k])
        return float(self.vals[k] - self.vals[k - 1]), pd.Timestamp(self.dates[k])

    def move_after(self, t: pd.Timestamp) -> tuple[float, pd.Timestamp | None]:
        k = self.first_after(t)
        if k >= len(self.vals):
            return float("nan"), None
        if (self.instants[k] - np.datetime64(pd.Timestamp(t), "ns")) / np.timedelta64(1, "D") > MAX_GAP_DAYS:
            return float("nan"), None
        return self.move_at(k)

    def nearest(self, instant: np.datetime64) -> int:
        """Position of the observation whose instant is nearest to `instant`."""
        k = int(np.searchsorted(self.instants, instant))
        cands = [j for j in (k - 1, k) if 0 <= j < len(self.instants)]
        return min(cands, key=lambda j: abs(self.instants[j] - instant)) if cands else -1


class Backbone:
    def __init__(self, frame: pd.DataFrame):
        self.frame = frame
        self.by: dict[str, pd.Series] = {
            name: pd.Series(g["value"].to_numpy(float), index=pd.DatetimeIndex(g["date"].values))
            for name, g in frame.sort_values("date").groupby("series")}

    @classmethod
    def load(cls, path: Path = BACKBONE_FILE) -> "Backbone":
        return cls(pd.read_parquet(path))

    def has(self, name: str) -> bool:
        return name in self.by

    def y2(self, ccy: str) -> AlignedSeries | None:
        s = self.by.get(f"Y2_{ccy}")
        return None if s is None else AlignedSeries(s, *CLOSES[ccy])

    def fx_levels(self) -> pd.DataFrame:
        """Log value in USD per currency on the H.10 dates where all seven exist;
        USD = 0."""
        cols = {c: self.by[f"FX_{c}"] for c in CCYS if c != "USD" and f"FX_{c}" in self.by}
        w = pd.DataFrame(cols).dropna()
        w.insert(0, "USD", 0.0)
        return w[[c for c in CCYS if c in w.columns]]

    def basket_levels(self) -> pd.DataFrame:
        return basket(self.fx_levels())

    def fx_aligned(self, ccy: str, which: str = "basket") -> AlignedSeries:
        lv = self.basket_levels() if which == "basket" else self.fx_levels()
        return AlignedSeries(lv[ccy], *H10_FIX)


def basket(levels: pd.DataFrame) -> pd.DataFrame:
    """value_c - mean of the other currencies (all columns are currencies)."""
    n = levels.shape[1]
    tot = levels.sum(axis=1)
    return levels.apply(lambda col: col - (tot - col) / (n - 1))


def us_control(us: AlignedSeries, ccy_series: AlignedSeries, k: int) -> float:
    """US 2Y daily move whose close is nearest to the currency's close k."""
    if k <= 0 or k >= len(ccy_series):
        return float("nan")
    j = us.nearest(ccy_series.instants[k])
    return us.move_at(j)[0] if j >= 0 else float("nan")
