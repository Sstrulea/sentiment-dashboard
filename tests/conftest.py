"""Test-wide: the single HTTP policy (src/http_policy.py) in a permissive mode for the tests that are not about it - no robots.txt
request, no crawl-delay sleep; its transport stays `requests` (module functions), so the tests that patch requests.get keep working.
tests/test_http_policy.py builds its own Policy objects."""
import pytest
import requests

from src import http_policy as HP


@pytest.fixture(autouse=True)
def _permissive_http_policy(monkeypatch):
    monkeypatch.setattr(HP, "POLICY", HP.Policy(session=requests, default_delay=0.0, robots="allow"))
