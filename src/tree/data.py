"""Point-in-time access to data/tree_inputs.parquet (written by
scripts/fetch_tree_inputs.py). A node only ever sees rows with avail <= asof."""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
INPUTS_FILE = ROOT / "data" / "tree_inputs.parquet"
COLUMNS = ["series", "date", "avail", "value"]


class Inputs:
    """Raw series in long format (series, date, avail, value). Each series is kept
    sorted by avail so an as-of slice is one searchsorted."""

    def __init__(self, frame: pd.DataFrame):
        missing = [c for c in COLUMNS if c not in frame.columns]
        if missing:
            raise ValueError(f"tree inputs: missing columns {missing}")
        df = frame[COLUMNS].copy()
        df["date"] = pd.to_datetime(df["date"]).astype("datetime64[ns]")
        df["avail"] = pd.to_datetime(df["avail"]).astype("datetime64[ns]")
        df = df.dropna(subset=["date", "avail", "value"])
        self._by: dict[str, tuple[np.ndarray, pd.Series]] = {}
        for name, g in df.groupby("series", sort=True):
            g = g.sort_values(["avail", "date"]).drop_duplicates("date", keep="last")
            s = pd.Series(g["value"].to_numpy(float), index=pd.DatetimeIndex(g["date"].to_numpy()))
            self._by[str(name)] = (g["avail"].to_numpy(), s)
        self.fingerprint = hashlib.sha256(
            pd.util.hash_pandas_object(df.sort_values(["series", "date"]), index=False).values.tobytes()
        ).hexdigest()[:16]

    @classmethod
    def load(cls, path: Path = INPUTS_FILE) -> "Inputs":
        return cls(pd.read_parquet(path))

    @property
    def names(self) -> list[str]:
        return sorted(self._by)

    def has(self, name: str) -> bool:
        return name in self._by

    def asof(self, name: str, asof: pd.Timestamp, lookback_days: int | None = None) -> pd.Series:
        """Values known on `asof` (avail <= asof), indexed by observation date.
        Empty Series when the name is unknown or nothing is available yet."""
        if name not in self._by:
            return pd.Series(dtype=float)
        avail, s = self._by[name]
        n = int(np.searchsorted(avail, np.datetime64(pd.Timestamp(asof), "ns"), side="right"))
        out = s.iloc[:n]
        if lookback_days is not None and len(out):
            out = out[out.index > out.index[-1] - pd.Timedelta(days=lookback_days)]
        return out

    def full(self, name: str) -> pd.Series:
        """Whole series, ignoring availability: for measurement only (forward
        returns, coverage tables), never for nodes."""
        return self._by[name][1] if name in self._by else pd.Series(dtype=float)
