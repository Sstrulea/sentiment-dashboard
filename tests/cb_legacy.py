"""The engine fixture frozen at 2026-09-18 (tests/fixtures/cb_engine) predates the Eurex OIS curves (first as-of 2026-10-08).
The 1B-2 / 3a acceptance numbers pinned on it keep their meaning only with the instruments of that date: the tests that use it
run with the roles and spread pairs of 2026-09-18 (MPT, BoE OIS, JPX, ECB AAA primary; USD spread on SOFR; no EUR / CHF spread).
The methods are unchanged - those instruments are cross-checks now. The Eurex acceptance runs on tests/fixtures/cb_engine_1008."""
from __future__ import annotations

import copy

from src.cb_loader import load_context

ROLES_0918 = {"atlantafed_mpt": "primary", "boe_ois": "primary", "jpx_tona": "primary", "ecb_aaa_fwd": "primary"}
SPREADS_0918 = {"USD": {"benchmark": "fred:SOFR", "policy": ["fred:DFEDTARL", "fred:DFEDTARU"]}, "EUR": None, "CHF": None}


def as_of_0918(ctx):
    ctx.sources = {sid: dict(c, role=ROLES_0918.get(sid, c.get("role"))) for sid, c in ctx.sources.items() if c["adapter"] != "EurexOis"}
    ctx.banks = copy.deepcopy(ctx.banks)
    for cur, sp in SPREADS_0918.items():
        ctx.banks[cur]["spread"] = sp
        ctx.banks[cur].pop("crosscheck_spread", None)
    return ctx


def load_0918(data_dir):
    return as_of_0918(load_context(data_dir))
