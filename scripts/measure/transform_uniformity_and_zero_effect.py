"""MEASUREMENT INSTRUMENT — not production code. Read-only. Writes nothing.

FAZA 0 (`can_be_zero` on the SERIES' real transform, CHF CPI stale 59d) —
validates H-xf ("the `# xf` transform-mismatch in config/ff_aliases.yaml is
harmless because Phase 2 rebuilds z from FF") and measures what a transform-
DERIVED `can_be_zero` rule (legitimate if the real `name_raw` suffix is m/m
or q/q; suspect if y/y or absent) would change vs. today's global,
indicator_key-only `can_be_zero` flag in data/economic_indicators.yaml.

Reads ONLY: data/economic_calendar_ff.parquet, config/ff_aliases.yaml (incl.
the `# xf` comments, which yaml.safe_load discards — recovered via a
line-oriented re-scan), data/economic_indicators.yaml,
data/economic_instruments.yaml. Writes nothing — no file under data/ or
src/ is created, modified, or deleted.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.econ_calendar_ff import canonical_id  # noqa: E402
from src.economic_fetch import CompiledMatcher  # noqa: E402
from src.economic_compute import build_payload  # noqa: E402
from src.ff_scoring import CCY2COUNTRY, load_can_be_zero  # noqa: E402

FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"
ALIASES_YAML = ROOT / "config" / "ff_aliases.yaml"
INDICATORS_YAML = ROOT / "data" / "economic_indicators.yaml"
INSTRUMENTS_YAML = ROOT / "data" / "economic_instruments.yaml"
AS_OF = pd.Timestamp("2026-08-02")   # currentDate at measurement time

_PERIOD_SUFFIX_RE = re.compile(r"\s+(y/y|q/q|m/m|3m/y|q/y|w/w)\s*$", re.IGNORECASE)


def extract_suffix(name) -> str:
    if not isinstance(name, str):
        return "none"
    m = _PERIOD_SUFFIX_RE.search(name)
    return m.group(1).lower() if m else "none"


def derived_can_be_zero(suffix: str) -> bool:
    """Candidate rule under test: legitimate zero iff the REAL transform is
    a period-over-period growth rate (m/m, q/q). y/y or no suffix (levels,
    counts, rate decisions) is suspect under this rule — see V3 for where
    that literal reading breaks down for non-growth-rate indicator types."""
    return suffix in ("m/m", "q/q")


# ---------------------------------------------------------------------------
# Alias index WITH the `# xf` comment (yaml.safe_load discards comments)
# ---------------------------------------------------------------------------

def load_alias_index_with_xf() -> dict[tuple[str, str], bool]:
    """(currency, name_raw) -> is_xf."""
    text = ALIASES_YAML.read_text()
    idx: dict[tuple[str, str], bool] = {}
    currency = None
    in_aliases = False
    for line in text.splitlines():
        if re.match(r"^aliases:\s*$", line):
            in_aliases = True
            continue
        if re.match(r"^excluded_final_variants:\s*$", line):
            break
        if not in_aliases:
            continue
        m_ccy = re.match(r"^  ([A-Z]{3}):\s*$", line)
        if m_ccy:
            currency = m_ccy.group(1)
            continue
        m_kv = re.match(r'^\s{4}"((?:[^"\\]|\\.)*)":\s*"((?:[^"\\]|\\.)*)"\s*(#.*)?$', line)
        if m_kv and currency:
            name_raw, _name_canon, comment = m_kv.groups()
            is_xf = bool(comment) and re.search(r"#\s*xf\b", comment) is not None
            idx[(currency, name_raw)] = is_xf
    return idx


# ---------------------------------------------------------------------------
# Load + enrich parquet
# ---------------------------------------------------------------------------

def load_configs():
    with open(INDICATORS_YAML) as f:
        ind = yaml.safe_load(f)
    with open(INSTRUMENTS_YAML) as f:
        inst = yaml.safe_load(f)
    return ind, inst


def load_enriched(matcher: CompiledMatcher, xf_idx: dict) -> pd.DataFrame:
    df = pd.read_parquet(FF_PARQUET)
    df["datetime_utc"] = pd.to_datetime(df["datetime_utc"])
    df["indicator_key"] = [
        matcher.match(CCY2COUNTRY.get(r.currency, ""), r.name_canonical)
        for r in df.itertuples(index=False)
    ]
    df["suffix_raw"] = df["name_raw"].apply(extract_suffix)
    df["suffix_canonical"] = df["name_canonical"].apply(extract_suffix)
    df["xf"] = [xf_idx.get((r.currency, r.name_raw), False) for r in df.itertuples(index=False)]
    return df


# ---------------------------------------------------------------------------
# V1 — transform uniformity per canonical_id (validates H-xf)
# ---------------------------------------------------------------------------

def v1_uniformity(df: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    print("\n" + "=" * 78)
    print("V1 — uniformitatea transformului per canonical_id (H-xf)")
    print("=" * 78)
    rows = []
    for cid, g in df.groupby("canonical_id", sort=True):
        name_raws = g.groupby("name_raw").agg(
            count=("name_raw", "size"),
            first=("datetime_utc", "min"),
            last=("datetime_utc", "max"),
        ).reset_index()
        suffixes = sorted({extract_suffix(nr) for nr in name_raws["name_raw"]})
        mixed = len(suffixes) >= 2
        ikey = g["indicator_key"].dropna().unique()
        ikey = ikey[0] if len(ikey) else None
        is_xf = bool(g["xf"].any())
        rows.append({
            "canonical_id": cid, "currency": g["currency"].iloc[0],
            "indicator_key": ikey, "in_scoring": ikey is not None,
            "n_name_raw_variants": len(name_raws), "suffixes": suffixes,
            "MIXED": mixed, "xf": is_xf, "n_rows": len(g),
            "name_raw_detail": list(name_raws.itertuples(index=False, name=None)),
        })
    table = pd.DataFrame(rows).sort_values(["MIXED", "in_scoring"], ascending=[False, False])

    xf_table = table[table["xf"]]
    rest_table = table[~table["xf"]]
    print(f"\n{len(table)} canonical_id total. {len(xf_table)} marcate # xf, {len(rest_table)} restul.")
    print(f"\n-- Serii marcate # xf ({len(xf_table)}) --")
    print(xf_table[["canonical_id", "currency", "indicator_key", "in_scoring",
                    "n_name_raw_variants", "suffixes", "MIXED", "n_rows"]].to_string(index=False))
    print(f"\n-- Restul seriilor ({len(rest_table)}) — doar cele cu >1 name_raw variant sau MIXED --")
    interesting = rest_table[(rest_table["n_name_raw_variants"] > 1) | (rest_table["MIXED"])]
    if len(interesting):
        print(interesting[["canonical_id", "currency", "indicator_key", "in_scoring",
                           "n_name_raw_variants", "suffixes", "MIXED", "n_rows"]].to_string(index=False))
    else:
        print("(niciuna — toate celelalte canonical_id sunt alimentate de un singur name_raw)")

    mixed_in_scoring = table[table["MIXED"] & table["in_scoring"]]
    print(f"\nMIXED pe serii care intră în scoring: {len(mixed_in_scoring)}")
    if len(mixed_in_scoring):
        print(mixed_in_scoring[["canonical_id", "currency", "indicator_key",
                                "name_raw_detail"]].to_string(index=False))
        print("\nMean/std pe fiecare sufix, pentru fiecare MIXED-in-scoring canonical_id:")
        for cid in mixed_in_scoring["canonical_id"]:
            sub = df[df["canonical_id"] == cid].copy()
            print(f"\n  {cid}:")
            print(sub.groupby("suffix_raw")["actual"].agg(["count", "mean", "std"]).to_string())

    h_xf_falls = len(mixed_in_scoring) > 0
    print(f"\n>>> H-xf {'CADE' if h_xf_falls else 'ȚINE'} "
          f"({'există' if h_xf_falls else 'nu există'} MIXED pe o serie de scoring).")
    return table, h_xf_falls


# ---------------------------------------------------------------------------
# V2 — point traces
# ---------------------------------------------------------------------------

def freq_max_age(ind_cfg: dict, indicator_key: str, currency: str) -> tuple[str, int]:
    ind = (ind_cfg.get("indicators", {}) or {}).get(indicator_key, {}) or {}
    freq = (ind.get("frequency_overrides", {}) or {}).get(currency) or ind.get("frequency") \
        or ind_cfg.get("defaults", {}).get("default_frequency", "monthly")
    max_age = ind_cfg.get("defaults", {}).get("max_age_by_frequency", {}).get(
        freq, ind_cfg.get("defaults", {}).get("max_age_days", 120))
    return freq, int(max_age)


def trace_series(df: pd.DataFrame, canonical_id_: str, currency: str, indicator_key: str,
                 cbz: set, ind_cfg: dict, label: str) -> None:
    sub = df[df["canonical_id"] == canonical_id_].sort_values("datetime_utc")
    print(f"\n-- {label} (canonical_id={canonical_id_}) --")
    if sub.empty:
        print("   NICIUN rând în parquet pentru acest canonical_id.")
        return
    print(sub[["datetime_utc", "name_raw", "actual", "forecast", "previous"]].to_string(index=False))

    n_zero = int((sub["actual"] == 0.0).sum())
    is_cbz_today = indicator_key in cbz
    survives_today = sub["actual"].notna() & ~((sub["actual"] == 0.0) & (not is_cbz_today))
    survives_recovered = sub["actual"].notna() & ~((sub["actual"] == 0.0) & False)  # zeros kept
    last_today = sub.loc[survives_today, "datetime_utc"].max()
    last_recovered = sub.loc[survives_recovered, "datetime_utc"].max()
    n_today = int(survives_today.sum())
    n_recovered = int(survives_recovered.sum())
    freq, max_age = freq_max_age(ind_cfg, indicator_key, currency)

    def _stale_str(last_dt):
        if pd.isna(last_dt):
            return "n/a (niciun print valid)"
        age = (AS_OF - last_dt).days
        return f"{last_dt.date()} ({age}d vechime, prag {freq}={max_age}d, " \
               f"{'STALE' if age > max_age else 'ok'})"

    print(f"   actual==0.0: {n_zero} rânduri")
    print(f"   can_be_zero azi (global, indicator_key={indicator_key!r}): {is_cbz_today}")
    print(f"   ultimul print care supraviețuiește AZI:       n={n_today}  last={_stale_str(last_today)}")
    print(f"   ultimul print dacă zerourile ar fi păstrate:  n={n_recovered}  last={_stale_str(last_recovered)}")


def v2_point_traces(df: pd.DataFrame, ind_cfg: dict):
    print("\n" + "=" * 78)
    print("V2 — CHF CPI / PPI, CAD CPI / core_cpi — traseul complet")
    print("=" * 78)
    cbz = load_can_be_zero()

    trace_series(df, canonical_id("CHF", "CPI y/y"), "CHF", "cpi_yoy", cbz, ind_cfg,
                "CHF cpi_yoy (fed de 'CPI m/m' # xf)")
    trace_series(df, canonical_id("CHF", "PPI y/y"), "CHF", "ppi_yoy", cbz, ind_cfg,
                "CHF ppi_yoy (fed de 'PPI m/m' # xf)")
    trace_series(df, canonical_id("CAD", "CPI y/y"), "CAD", "cpi_yoy", cbz, ind_cfg,
                "CAD cpi_yoy (fed de 'CPI m/m' # xf)")

    print("\n>>> ATENȚIE, fapt descoperit la V2, nu presupus în pre-înregistrare:")
    print("    data/economic_indicators.yaml, matcher Canada (comentariu 2026-07-30,")
    print("    docs/proposal-cad-core-promotion.md): regula '^Core CPI y/y$' -> core_cpi")
    print("    a fost ELIMINATĂ și înlocuită cu '^Median CPI y/y$' -> core_cpi. Deci")
    print("    CAD core_cpi NU mai e alimentat de aliasul xf 'Core CPI m/m' azi — e")
    print("    alimentat de 'Median CPI y/y', o serie y/y NATIVĂ (nu xf). Arăt ambele:")
    trace_series(df, canonical_id("CAD", "Core CPI y/y"), "CAD", None, cbz, ind_cfg,
                "CAD 'Core CPI y/y' — canonical_id orfan, fed de 'Core CPI m/m' # xf, "
                "NEMAPAT în matcher azi (indicator_key=None, nu intră în scoring)")
    trace_series(df, canonical_id("CAD", "Median CPI y/y"), "CAD", "core_cpi", cbz, ind_cfg,
                "CAD 'Median CPI y/y' — canonical_id LIVE pentru core_cpi azi (y/y nativ, nu xf)")


# ---------------------------------------------------------------------------
# V3 — derived-rule inventory
# ---------------------------------------------------------------------------

def v3_derived_rule(df: pd.DataFrame) -> pd.DataFrame:
    print("\n" + "=" * 78)
    print("V3 — inventar pe regula derivată din sufixul lui name_raw")
    print("=" * 78)
    cbz = load_can_be_zero()
    scored = df[df["indicator_key"].notna()].copy()

    rows = []
    for (ccy, key), g in scored.groupby(["currency", "indicator_key"], sort=True):
        name_raws = g["name_raw"].unique()
        suffixes = {extract_suffix(nr) for nr in name_raws}
        # non-MIXED per V1 gate; take the (single) suffix for this group
        suffix = sorted(suffixes)[0] if len(suffixes) == 1 else "MIXED:" + "|".join(sorted(suffixes))
        cbz_today = key in cbz
        cbz_derived = derived_can_be_zero(suffix) if len(suffixes) == 1 else None
        n_zero = int((g["actual"] == 0.0).sum())
        differs = (cbz_derived is not None) and (cbz_derived != cbz_today)
        rows.append({
            "indicator_key": key, "currency": ccy, "name_raw": "|".join(sorted(name_raws)),
            "suffix": suffix, "can_be_zero_azi": cbz_today, "can_be_zero_derivat": cbz_derived,
            "differs": differs,
            "n_zerouri_afectate": n_zero if differs else 0,
            "direction": (None if not differs else
                         ("would_recover" if cbz_derived and not cbz_today else "would_restrict")),
        })
    table = pd.DataFrame(rows).sort_values(["differs", "n_zerouri_afectate"], ascending=[False, False])
    print(f"\n{len(table)} perechi (currency, indicator_key) în scoring. "
          f"{int(table['differs'].sum())} diferă de configul actual.")
    print()
    print(table.to_string(index=False))

    recover = table[table["direction"] == "would_recover"]
    restrict = table[table["direction"] == "would_restrict"]
    print(f"\n-- Ar RECUPERA (azi False, derivat True): {len(recover)} perechi, "
          f"{int(recover['n_zerouri_afectate'].sum())} zerouri total --")
    print(f"-- Ar RESTRÂNGE (azi True, derivat False, deci MAI STRICT): {len(restrict)} perechi, "
          f"{int(restrict['n_zerouri_afectate'].sum())} zerouri total --")
    if len(restrict):
        print("\n   Notă factuală: pentru perechile 'would_restrict' cu suffix='none' "
              "(ex. employment_change, interest_rate_decision), name_raw nu are NICIUN "
              "sufix de perioadă — sunt indicatori de nivel/schimbare netă, nu rate de "
              "creștere m/m sau q/q. Regula derivată, aplicată literal, le-ar marca "
              "'suspect' — dar comentariul din data/economic_indicators.yaml (liniile "
              "60-67) le clasifică TRUE prin design ('net changes / rate decisions'), "
              "nu prin sufix. Cifrele sunt reale; interpretarea e a ta.")
    return table


# ---------------------------------------------------------------------------
# V4 — scoring effect for changed series
# ---------------------------------------------------------------------------

def to_scoring_frame_with_override(ff_df: pd.DataFrame, matcher: CompiledMatcher,
                                   cbz: set, override: tuple[str, str], override_val: bool):
    """Mirrors ff_scoring.to_scoring_frame EXACTLY, except the actual==0.0
    quarantine for the single (currency, indicator_key) pair `override` is
    forced to `override_val` (True = treat 0.0 as legitimate, False = force
    quarantine) instead of the global can_be_zero set. Consensus quarantine
    is UNCHANGED (still global) — out of scope per the pre-registration."""
    nan = float("nan")
    recs: list[dict] = []
    for r in ff_df.itertuples(index=False):
        key = matcher.match(CCY2COUNTRY.get(r.currency, ""), r.name_canonical)
        if key is None:
            continue
        actual, consensus = r.actual, r.forecast
        is_override = (r.currency, key) == override
        legit_zero = override_val if is_override else (key in cbz)
        if not legit_zero and actual == 0.0:
            actual = nan
        if key not in cbz and consensus == 0.0:
            consensus = nan
        recs.append({
            "currency": r.currency, "indicator_key": key, "release_dt": r.datetime_utc,
            "actual": actual, "consensus": consensus, "previous": r.previous, "source": "ff",
        })
    return pd.DataFrame(recs, columns=["currency", "indicator_key", "release_dt",
                                       "actual", "consensus", "previous", "source"])


def v4_scoring_effect(df: pd.DataFrame, table_v3: pd.DataFrame, matcher: CompiledMatcher,
                      ind_cfg: dict, inst_cfg: dict):
    print("\n" + "=" * 78)
    print("V4 — efect pe scoring pentru seriile unde regula derivată schimbă ceva")
    print("=" * 78)
    cbz = load_can_be_zero()
    changed = table_v3[table_v3["differs"]]
    if changed.empty:
        print("\n(nicio serie nu diferă — nimic de simulat)")
        return

    base_cal = to_scoring_frame_with_override(df, matcher, cbz, ("__none__", "__none__"), False)
    base_cal["release_dt"] = pd.to_datetime(base_cal["release_dt"])
    payload_before = build_payload(base_cal, ind_cfg, inst_cfg, as_of=AS_OF)

    for row in changed.itertuples(index=False):
        ccy, key = row.currency, row.indicator_key
        override_val = row.direction == "would_recover"
        cal = to_scoring_frame_with_override(df, matcher, cbz, (ccy, key), override_val)
        cal["release_dt"] = pd.to_datetime(cal["release_dt"])
        payload_after = build_payload(cal, ind_cfg, inst_cfg, as_of=AS_OF)

        sub_b = base_cal[(base_cal["currency"] == ccy) & (base_cal["indicator_key"] == key)]
        sub_a = cal[(cal["currency"] == ccy) & (cal["indicator_key"] == key)]
        n_b = int(sub_b["actual"].notna().sum())
        n_a = int(sub_a["actual"].notna().sum())

        cat_before = payload_before["currencies"].get(ccy, {}).get("categories", {})
        cat_after = payload_after["currencies"].get(ccy, {}).get("categories", {})
        bd_before = payload_before["currencies"].get(ccy, {}).get("breakdown", {}).get(key, {})
        bd_after = payload_after["currencies"].get(ccy, {}).get("breakdown", {}).get(key, {})
        cat_key = bd_before.get("category") or bd_after.get("category")
        cb = cat_before.get(cat_key, {})
        ca = cat_after.get(cat_key, {})

        b_by_sym = {i["symbol"]: i for i in payload_before["instruments"]}
        a_by_sym = {i["symbol"]: i for i in payload_after["instruments"]}
        flips = [s for s in b_by_sym if b_by_sym[s]["bias"] != a_by_sym[s]["bias"]]

        print(f"\n-- {ccy} {key} ({row.direction}) --")
        print(f"   n actual valid: {n_b} -> {n_a}")
        print(f"   latest flag {bd_before.get('flag')}->{bd_after.get('flag')}  "
             f"latest score {bd_before.get('score')}->{bd_after.get('score')}")
        print(f"   category {cat_key}: N {cb.get('coverage')}->{ca.get('coverage')}  "
             f"precise {cb.get('score_precise')}->{ca.get('score_precise')}  "
             f"cell {cb.get('score_cell')}->{ca.get('score_cell')}")
        print(f"   bias flips: {flips if flips else 'none'}")


# ---------------------------------------------------------------------------
# V5 — display labels
# ---------------------------------------------------------------------------

def v5_labels(df: pd.DataFrame):
    print("\n" + "=" * 78)
    print("V5 — etichete de afișare: name_raw (transform real) vs name_canonical (afișat)")
    print("=" * 78)
    scored = df[df["indicator_key"].notna()].copy()
    mismatched = (scored[scored["suffix_raw"] != scored["suffix_canonical"]]
                 [["currency", "indicator_key", "canonical_id", "name_raw", "name_canonical",
                   "suffix_raw", "suffix_canonical"]]
                 .drop_duplicates()
                 .sort_values(["currency", "indicator_key"]))
    print(f"\n{len(mismatched)} (currency, indicator_key, name_raw) unice unde sufixul real "
          f"!= sufixul afișat, DINTRE cele care ajung pe dashboard (indicator_key mapat):\n")
    print(mismatched.rename(columns={
        "name_canonical": "afișează_azi", "name_raw": "ar_trebui_ (transform real)",
    }).to_string(index=False))


# ---------------------------------------------------------------------------
# Acceptance gate
# ---------------------------------------------------------------------------

def acceptance_gate(h_xf_falls: bool, table_v3: pd.DataFrame):
    print("\n" + "=" * 78)
    print("CRITERIU DE ACCEPTARE (fixat în pre-înregistrare)")
    print("=" * 78)
    cond_a = not h_xf_falls
    print(f"\n(a) V1 fără MIXED pe serie de scoring: {cond_a}")

    recover = table_v3[table_v3["direction"] == "would_recover"]
    max_recover_single = int(recover["n_zerouri_afectate"].max()) if len(recover) else 0
    cond_b1 = max_recover_single >= 8
    print(f"(b1) regula derivată recuperează >= 8 printuri pe cel puțin o serie scorată: "
          f"{cond_b1} (max pe o singură serie: {max_recover_single})")
    print("(b2) de-stale-uiește o serie peste prag — vezi V2 pentru CHF cpi_yoy explicit "
          "(citește 'ultimul print AZI' vs 'ultimul print dacă zerourile ar fi păstrate' "
          "mai sus; verdictul e în output-ul V2, nu recalculat aici ca să nu dubleze logica).")
    cond_b = cond_b1
    passed = cond_a and cond_b
    print(f"\n>>> {'CRITERIUL (a) și (b1) SATISFĂCUTE' if passed else 'CRITERIUL NU E SATISFĂCUT (cel puțin din a/b1)'} "
          f"— vezi V2 pentru verdictul complet pe (b2) (staleness).")


def main() -> None:
    pd.set_option("display.max_rows", None, "display.width", 220, "display.max_colwidth", 60)

    ind_cfg, inst_cfg = load_configs()
    matcher = CompiledMatcher(ind_cfg.get("matcher", {}))
    xf_idx = load_alias_index_with_xf()
    df = load_enriched(matcher, xf_idx)

    print(f"AS_OF = {AS_OF.date()}  (folosit pentru calculul de vechime/staleness în V2)")

    table_v1, h_xf_falls = v1_uniformity(df)
    if h_xf_falls:
        print("\n" + "!" * 78)
        print("STOP — H-xf a picat (MIXED pe o serie de scoring). Nu continui cu V2-V5:")
        print("premisa care le susține (transform uniform per canonical_id) e falsă.")
        print("!" * 78)
        acceptance_gate(h_xf_falls, pd.DataFrame(columns=["direction", "n_zerouri_afectate"]))
        return

    v2_point_traces(df, ind_cfg)
    table_v3 = v3_derived_rule(df)
    v4_scoring_effect(df, table_v3, matcher, ind_cfg, inst_cfg)
    v5_labels(df)
    acceptance_gate(h_xf_falls, table_v3)


if __name__ == "__main__":
    main()
