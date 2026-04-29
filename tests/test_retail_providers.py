"""Tests for the Myfxbook provider parser. No network."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.retail_providers.myfxbook import MyfxbookProvider, _strip_int, _strip_number

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "myfxbook_outlook_sample.json"
SYMBOLS_YAML = ROOT / "data" / "retail_symbols.yaml"


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("MYFXBOOK_EMAIL", "test@example.com")
    monkeypatch.setenv("MYFXBOOK_PASSWORD", "fake")
    return MyfxbookProvider(symbols_yaml=SYMBOLS_YAML)


@pytest.fixture
def fixture_payload() -> dict:
    return json.loads(FIXTURE.read_text())


def test_strip_number_handles_commas():
    assert _strip_number("35,914,737.56") == pytest.approx(35914737.56)
    assert _strip_number("1,000") == 1000.0
    assert _strip_number(42.5) == 42.5
    assert _strip_number(None) is None
    assert _strip_number("") is None
    assert _strip_number("not a number") is None


def test_strip_int_rounds():
    assert _strip_int("1,234.7") == 1235
    assert _strip_int(99.4) == 99


def test_parse_symbols_normalizes_aliases(provider, fixture_payload):
    snapshots = provider._parse_symbols(fixture_payload)

    by_sym = {s.symbol: s for s in snapshots}

    # Direct mapping
    assert "EURUSD" in by_sym
    assert by_sym["EURUSD"].long_pct == 72
    assert by_sym["EURUSD"].short_pct == 28
    assert by_sym["EURUSD"].long_positions == 2932

    # Alias mapping: USTEC -> NAS100, SPX500 -> US500, DJ30 -> US30
    assert "NAS100" in by_sym
    assert "US500" in by_sym
    assert "US30" in by_sym


def test_parse_symbols_drops_unmapped(provider, fixture_payload):
    snapshots = provider._parse_symbols(fixture_payload)
    syms = {s.symbol for s in snapshots}
    # 'SOMEUNKNOWN' is not in retail_symbols.yaml or aliases.
    assert "SOMEUNKNOWN" not in syms


def test_parse_symbols_filters_out_of_tolerance(provider, fixture_payload):
    # Fixture has BADSYM_BAD with long+short=80 → should be skipped.
    snapshots = provider._parse_symbols(fixture_payload)
    assert all(s.symbol != "BADSYM_BAD" for s in snapshots)


def test_parse_symbols_count_excludes_invalid(provider, fixture_payload):
    snapshots = provider._parse_symbols(fixture_payload)
    # 11 input rows - 1 unmapped (SOMEUNKNOWN) - 1 unmapped/bad-pct (BADSYM_BAD)
    assert len(snapshots) == 9


def test_parse_general_strips_commas(provider, fixture_payload):
    g = provider._parse_general(fixture_payload)
    assert g.real_account_pct == pytest.approx(56.5)
    assert g.profitable_pct == pytest.approx(31.5)
    assert g.total_funds_usd == pytest.approx(35914737.56)
    assert g.average_deposit_usd == pytest.approx(5278.10)


def test_parse_general_handles_missing(provider):
    g = provider._parse_general({})
    assert g.real_account_pct is None
    assert g.total_funds_usd is None


def test_init_requires_credentials(monkeypatch):
    monkeypatch.delenv("MYFXBOOK_EMAIL", raising=False)
    monkeypatch.delenv("MYFXBOOK_PASSWORD", raising=False)
    from src.retail_providers import RetailAuthError
    with pytest.raises(RetailAuthError):
        MyfxbookProvider(symbols_yaml=SYMBOLS_YAML)


def test_login_session_is_url_decoded_before_reuse(provider, fixture_payload):
    """Regression: Myfxbook returns the session URL-encoded (%2B, %2F, %3D).

    If we passed it through `requests.params={'session': sid}` without
    decoding, the % signs would be double-encoded as %25 and the second
    API call would fail with 'Invalid session'. Verify that the URL the
    second request actually builds contains the decoded `+` / `/` / `=`,
    not `%2B` / `%2F` / `%3D`.
    """
    encoded_session = "abc123%2BXYZ%2Fdef%3D%3D"
    decoded_session = "abc123+XYZ/def=="

    captured_urls: list[str] = []

    def fake_get(url, params=None, timeout=None):
        # Build the prepared URL the same way `requests` would, so we capture
        # exactly what would go on the wire.
        prepared = requests.Request("GET", url, params=params).prepare()
        captured_urls.append(prepared.url)

        resp = MagicMock()
        resp.status_code = 200
        # First call: login. Subsequent calls: outlook then logout.
        if "login.json" in url:
            resp.json.return_value = {"error": False, "session": encoded_session}
        elif "get-community-outlook.json" in url:
            resp.json.return_value = fixture_payload
        else:
            resp.json.return_value = {"error": False}
        return resp

    import requests
    with patch.object(provider._session, "get", side_effect=fake_get):
        result = provider.fetch_all()

    # 3 calls: login, get-community-outlook, logout.
    assert len(captured_urls) == 3

    # The login URL should NOT contain a session yet.
    assert "session=" not in captured_urls[0]

    # The outlook URL must use the DECODED session — i.e., contain `+`/`/`/`=`
    # (re-encoded once by requests as %2B/%2F/%3D), but NOT %252B/%252F/%253D
    # which would indicate double-encoding from passing the raw response through.
    outlook_url = captured_urls[1]
    assert "%252B" not in outlook_url
    assert "%252F" not in outlook_url
    assert "%253D" not in outlook_url
    # And it must encode the actual `+` `/` `=` characters from the decoded session.
    assert "%2B" in outlook_url
    assert "%2F" in outlook_url
    assert "%3D" in outlook_url

    # Logout call also uses the decoded session.
    logout_url = captured_urls[2]
    assert "%252B" not in logout_url
    assert "%2B" in logout_url

    # Sanity: parsing the outlook URL back yields the decoded session.
    from urllib.parse import urlparse, parse_qs
    qs = parse_qs(urlparse(outlook_url).query)
    assert qs["session"] == [decoded_session]

    # And the result parsed correctly.
    assert len(result.snapshots) > 0


def test_health_check_decodes_session(provider):
    """health_check() must decode the session before logout, same as fetch_all()."""
    encoded_session = "tok%2Bwith%2Fspecial%3Dchars"
    decoded_session = "tok+with/special=chars"
    captured_urls: list[str] = []

    def fake_get(url, params=None, timeout=None):
        import requests as _r
        prepared = _r.Request("GET", url, params=params).prepare()
        captured_urls.append(prepared.url)
        resp = MagicMock()
        resp.status_code = 200
        if "login.json" in url:
            resp.json.return_value = {"error": False, "session": encoded_session}
        else:
            resp.json.return_value = {"error": False}
        return resp

    with patch.object(provider._session, "get", side_effect=fake_get):
        ok = provider.health_check()
    assert ok is True
    # Logout URL uses the decoded session (not double-encoded).
    assert any("%252B" not in u for u in captured_urls[1:])
    from urllib.parse import urlparse, parse_qs
    qs = parse_qs(urlparse(captured_urls[1]).query)
    assert qs["session"] == [decoded_session]


def test_user_agent_is_browser_like(provider):
    ua = provider._session.headers.get("User-Agent", "")
    assert ua.startswith("Mozilla/5.0")
