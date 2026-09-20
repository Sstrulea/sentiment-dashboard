"""Generate config/cb_roster.yaml (names, roles, voting status) from the banks' official committee pages.

    python scripts/cb_gen_roster.py [--out config/cb_roster.yaml]

Fed: the FOMC page (members, alternates, and the rotation table -> voters of 2026 / 2027). Other banks: the pages of the decision-making body
(Governing Council, MPC, Policy Board, Monetary Policy Board, Governing Board): the names with their titles; every member of a body that
votes as a whole (BoE MPC, BoJ Policy Board, RBA MPB, BoC Governing Council, RBNZ MPC, SNB Governing Board) counts as a voter; the ECB
Governing Council has rotating voting rights: `voter` is null. RBNZ is behind a Cloudflare challenge: no automatic entry (empty list).
Polite HTTP (robots.txt, rate limit) through src/cb_docs/http.py.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.cb_docs.http import Fetcher            # noqa: E402

PAGES = {
    "USD": ("https://www.federalreserve.gov/monetarypolicy/fomc.htm", "FOMC"),
    "EUR": ("https://www.ecb.europa.eu/ecb/orga/decisions/govc/html/index.en.html", "Governing Council"),
    "GBP": ("https://www.bankofengland.co.uk/about/people/monetary-policy-committee", "MPC"),
    "JPY": ("https://www.boj.or.jp/en/about/organization/policyboard/index.htm", "Policy Board"),
    "CAD": ("https://www.bankofcanada.ca/about/people/governing-council/", "Governing Council"),
    "AUD": ("https://www.rba.gov.au/about-rba/boards/monetary-policy-board.html", "Monetary Policy Board"),
    "CHF": ("https://www.snb.ch/en/the-snb/organisation/supervisory-management-boards", "Governing Board"),
}
ROLE_RX = re.compile(r"([A-Z][\w'’\-\.]+(?: [A-Z][\w'’\-\.]+){1,3}),? (?P<role>Governor|Deputy Governor|Senior Deputy Governor|Chair(?:man)?|Vice[- ]Chair(?:man)?|"
                     r"President|Vice-President|Chief Economist|Executive Director)\b")
NAME = r"[A-Z][a-z]+(?: [A-Z]\.)?(?: (?:[A-Z][a-z]+|[A-Z][a-z]+-[A-Z][a-z]+))+"


def text_of(html: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))


BANKS12 = ("New York", "Chicago", "Richmond", "Atlanta", "San Francisco", "Boston", "Cleveland", "Philadelphia", "Dallas", "St. Louis", "Minneapolis", "Kansas City")


def fed(text: str) -> list:
    m = re.search(r"2026 Committee Members (?P<mem>.*?) Alternate Members (?P<alt>.*?) Rotation on the FOMC", text)
    if not m:
        return []
    def people(seg: str) -> list:
        seg = seg.strip()
        for b in ("Board of Governors", "New York", "San Francisco", "St. Louis", "Kansas City"):             # multi-word places are single tokens here
            seg = seg.replace(b, b.replace(" ", "_"))
        out = []
        for mm in re.finditer(rf"(?P<n>{NAME}) , (?P<rest>.*?)(?= {NAME} , |$)", seg):
            out.append((mm.group("n").strip(), mm.group("rest").strip().rstrip(",").replace("_", " ")))
        return out
    members, alternates = people(m.group("mem")), people(m.group("alt"))
    rot = re.search(r"2027 2028 2029 Members (?P<r>.*?) Alternate Members", text[text.find("Rotation on the FOMC"):])
    banks27 = []
    if rot:
        first = re.split(r"&nbsp;| ", rot.group("r"))[0]
        banks27 = [b for b in BANKS12 if b in first]
    out = []
    for name, rest in members + alternates:
        board = rest.startswith("Board of Governors")
        bank = "" if board else next((b for b in BANKS12 if b in rest), rest.split(",")[0])                     # "Interim President, Atlanta Federal Reserve Bank" -> Atlanta
        role = ("Chair" if "Chairman" in rest else "Vice Chair" if "Vice Chair" in rest else "Governor" if board else
                "First Vice President" if "First Vice President" in rest else "Interim President" if "Interim President" in rest else "President")
        is_member = (name, rest) in members
        holds_seat = role in ("Chair", "Governor", "Vice Chair", "President", "Interim President")               # a First Vice President votes only when the President is absent
        v27 = holds_seat and (board or bank == "New York" or bank in banks27)                                       # 2027 members: New York + Chicago, Richmond, Atlanta, San Francisco
        out.append({"name": name, "role": role, "chair": role == "Chair", "body": "FOMC" if is_member else "FOMC alternate", "bank": bank or "Board of Governors",
                    "voter": {"2026": is_member, "2027": bool(v27)}})
    return out


STOP = {"Deputy", "Senior", "Vice", "First", "Policy", "Board", "Committee", "England", "Bank", "Officer", "Operating", "Council", "Analysis", "Statutory",
        "Appointments", "Payments", "System", "Governance", "Financial", "Regulators", "Foundation", "Anika", "Treasurer", "Assistant", "Chief", "Our", "People",
        "Executive", "Members", "ECB", "None", "Sir", "Ms", "Mr", "Dr", "Governor", "President", "Chair", "Chairman", "Pank", "Ireland", "Greece", "Belgique",
        "Eesti", "Members"}


JUNK = re.compile(r"Advisory|School|Regional|Treasurer|Foundation|Business|Payments|Statutory|Regulators", re.I)


def clean_name(raw: str) -> str:
    """The last two capitalised tokens that are not page furniture: 'Committee Andrew Bailey' -> 'Andrew Bailey'."""
    toks = [t for t in raw.split() if t not in STOP]
    return " ".join(toks[-2:]) if len(toks) >= 2 else ""


def generic(text: str, body: str, voter) -> list:
    seen, out = set(), []
    for m in ROLE_RX.finditer(text):
        raw = m.group(1).strip()
        name = clean_name(raw)
        if not name or name in seen or len(name) > 40 or any(len(t) < 3 for t in name.split()) or "." in name or JUNK.search(name):
            continue
        seen.add(name)
        role = ("Deputy " + m.group("role")) if "Deputy" in raw.split() and m.group("role") == "Governor" else m.group("role")
        out.append({"name": name, "role": role, "body": body, "voter": {"2026": voter, "2027": voter}, "chair": not out and role in ("Governor", "President", "Chair", "Chairman")})
    return out


def boj(text: str) -> list:
    """Members of the Policy Board: `Governor UEDA Kazuo Deputy Governor UCHIDA Shinichi ... Member of the Policy Board TAKATA Hajime` -> Kazuo Ueda."""
    i = text.find("Members of the Policy Board Governor")
    seg = text[i:text.find("Related Releases", i)] if i >= 0 else ""
    out = []
    for m in re.finditer(r"(?P<role>Deputy Governor|Governor|Member of the Policy Board) (?P<sur>[A-Z]{2,}) (?P<given>[A-Z][a-z]+)", seg):
        role = "Member" if m.group("role").startswith("Member") else m.group("role")
        out.append({"name": f"{m.group('given')} {m.group('sur').title()}", "role": role, "body": "Policy Board", "voter": {"2026": True, "2027": True}, "chair": role == "Governor"})
    return out


def rba(text: str) -> list:
    """The Monetary Policy Board page: `Chair: Michele Bullock ...`, `Deputy Chair: Andrew Hauser ...`, `Member: Marnie Baker ...`."""
    i = text.find("The current members of the Monetary Policy Board are")
    seg = text[i:i + 8000] if i >= 0 else ""
    out = []
    uname = r"[A-Z][^\W\d_]+(?: [A-Z][^\W\d_]+(?:-[A-Z][^\W\d_]+)?){1,2}"                                     # accents and hyphens (Renée Fry-McKibbin)
    for m in re.finditer(rf"(?P<role>Chair|Deputy Chair|Member): (?P<n>{uname})(?= (?:AM|AO|PSM|BEc|MA|BBus|Bec|LLB|PhD|BA|BSc|MSc)\b|,)", seg):
        out.append({"name": re.sub(r" (?:AM|AO|AC|PSM|OAM)$", "", m.group("n")), "role": m.group("role"), "body": "Monetary Policy Board", "voter": {"2026": True, "2027": True}, "chair": m.group("role") == "Chair"})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "config" / "cb_roster.yaml"))
    a = ap.parse_args(argv)
    f = Fetcher(min_interval=1.5)
    people, sources = [], {}
    for ccy, (url, body) in PAGES.items():
        r = f.get(url, conditional=False)
        sources[ccy] = {"url": url, "status": r.status, "error": r.error or None}
        if r.error:
            continue
        text = text_of(r.text)
        got = fed(text) if ccy == "USD" else boj(text) if ccy == "JPY" else rba(text) if ccy == "AUD" else generic(text, body, None if ccy == "EUR" else True)
        for p in got:
            people.append({"currency": ccy, **p})
    doc = {"meta": {"generated": date.today().isoformat(), "generator": "scripts/cb_gen_roster.py", "note": "names and titles from the banks' official committee pages; "
                    "`voter`: FOMC rotation 2026 / 2027; the other decision bodies vote as a whole (true), the ECB Governing Council rotates (null); "
                    "RBNZ is behind a Cloudflare challenge (no entries)"}, "sources": sources, "people": people}
    Path(a.out).write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=140))
    print(f"{len(people)} people from {sum(1 for s in sources.values() if not s['error'])} pages -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
