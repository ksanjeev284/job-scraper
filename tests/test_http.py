"""Tests for HTTP plumbing: proxy rotation pool and http_get integration."""

from __future__ import annotations

import pytest

from jobscraper import http as http_mod
from jobscraper.http import (
    configure_proxies,
    load_proxies_file,
    mark_proxy_bad,
    mark_proxy_ok,
    next_proxy,
    parse_proxy,
    proxies_from_env,
    proxy_count,
    reset_proxies,
)


@pytest.fixture(autouse=True)
def _clean_proxy_state():
    reset_proxies()
    yield
    reset_proxies()


@pytest.fixture(autouse=True)
def _no_waits(monkeypatch):
    monkeypatch.setattr(http_mod, "polite_wait", lambda url: None)
    monkeypatch.setattr(http_mod.time, "sleep", lambda *a, **k: None)


def test_parse_proxy_valid():
    assert parse_proxy("http://host:8080") == "http://host:8080"
    assert parse_proxy("https://user:pass@host:3128") == \
        "https://user:pass@host:3128"
    assert parse_proxy("socks5://127.0.0.1:1080") == "socks5://127.0.0.1:1080"
    assert parse_proxy("SOCKS5H://host") == "SOCKS5H://host"
    assert parse_proxy("  http://host  ") == "http://host"


@pytest.mark.parametrize("spec", [
    "",
    "   ",
    "ftp://host:21",
    "host:8080",          # missing scheme
    "http://",            # missing host
    "://host:8080",       # missing scheme
])
def test_parse_proxy_invalid(spec):
    with pytest.raises(ValueError):
        parse_proxy(spec)


def test_configure_proxies_round_robin():
    configure_proxies(["http://a:8080", "http://b:8080", "http://c:8080"])
    assert proxy_count() == 3
    assert [next_proxy() for _ in range(4)] == \
        ["http://a:8080", "http://b:8080", "http://c:8080", "http://a:8080"]


def test_configure_proxies_atomic_on_bad_spec():
    configure_proxies(["http://a:8080", "http://b:8080"])
    with pytest.raises(ValueError):
        configure_proxies(["http://ok:8080", "bogus-spec"])
    # existing pool untouched
    assert proxy_count() == 2
    assert next_proxy() == "http://a:8080"


def test_next_proxy_none_without_pool():
    assert next_proxy() is None


def test_parking_skips_proxy_until_cooldown():
    configure_proxies(["http://good:8080", "http://flaky:8080"])
    for _ in range(http_mod.PROXY_MAX_FAILURES):
        mark_proxy_bad("http://flaky:8080")
    seen = {next_proxy() for _ in range(5)}
    assert seen == {"http://good:8080"}


def test_all_parked_falls_back_to_direct():
    configure_proxies(["http://solo:8080"])
    for _ in range(http_mod.PROXY_MAX_FAILURES):
        mark_proxy_bad("http://solo:8080")
    assert next_proxy() is None


def test_parked_proxy_reenters_after_cooldown(monkeypatch):
    configure_proxies(["http://good:8080", "http://flaky:8080"])
    for _ in range(http_mod.PROXY_MAX_FAILURES):
        mark_proxy_bad("http://flaky:8080")
    assert next_proxy() == "http://good:8080"
    # fast-forward past the park window
    parked_until = http_mod._proxy_parked_until["http://flaky:8080"]
    monkeypatch.setattr(http_mod.time, "time",
                        lambda: parked_until + 1.0)
    seen = {next_proxy() for _ in range(4)}
    assert "http://flaky:8080" in seen


def test_mark_proxy_ok_resets_failure_streak():
    configure_proxies(["http://a:8080"])
    mark_proxy_bad("http://a:8080")
    mark_proxy_ok("http://a:8080")
    mark_proxy_bad("http://a:8080")
    mark_proxy_bad("http://a:8080")
    assert next_proxy() == "http://a:8080"  # 2 failures < threshold


def test_mark_proxy_bad_ignores_unknown():
    mark_proxy_bad("http://never-configured:8080")  # no pool, no error


def test_load_proxies_file(tmp_path):
    path = tmp_path / "proxies.txt"
    path.write_text(
        "# corporate egress\n"
        "\n"
        "http://proxy1:8080\n"
        "   https://user:pw@proxy2:3128   \n"
        "# disabled: http://old:8080\n",
        encoding="utf-8")
    assert load_proxies_file(str(path)) == \
        ["http://proxy1:8080", "https://user:pw@proxy2:3128"]


def test_proxies_from_env(monkeypatch):
    monkeypatch.setenv("JOBSCRAPER_PROXIES",
                       "http://a:8080, https://b:3128  socks5://c:1080")
    assert proxies_from_env() == \
        ["http://a:8080", "https://b:3128", "socks5://c:1080"]


def test_proxies_from_env_unset(monkeypatch):
    monkeypatch.delenv("JOBSCRAPER_PROXIES", raising=False)
    assert proxies_from_env() == []


class _Resp:
    def __init__(self, status=200):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise http_mod.requests.HTTPError(f"HTTP {self.status_code}")


def test_http_get_routes_through_pool(monkeypatch):
    calls = []
    monkeypatch.setattr(
        http_mod.requests, "get",
        lambda url, **kw: calls.append(kw) or _Resp())
    configure_proxies(["http://p1:8080", "http://p2:8080"])
    http_mod.http_get("https://example.com/jobs")
    http_mod.http_get("https://example.com/jobs")
    assert calls[0]["proxies"] == {"http": "http://p1:8080",
                                   "https": "http://p1:8080"}
    assert calls[1]["proxies"] == {"http": "http://p2:8080",
                                   "https": "http://p2:8080"}


def test_http_get_direct_when_no_proxies(monkeypatch):
    calls = []
    monkeypatch.setattr(
        http_mod.requests, "get",
        lambda url, **kw: calls.append(kw) or _Resp())
    http_mod.http_get("https://example.com/jobs")
    assert calls[0]["proxies"] is None


def test_http_get_marks_proxy_bad_on_network_failure(monkeypatch):
    def boom(url, **kw):
        raise http_mod.requests.ConnectionError("proxy refused")
    monkeypatch.setattr(http_mod.requests, "get", boom)
    configure_proxies(["http://dead:8080"])
    with pytest.raises(http_mod.requests.ConnectionError):
        http_mod.http_get("https://example.com/jobs", max_retries=3)
    assert next_proxy() is None  # parked after 3 consecutive failures


def test_http_get_does_not_punish_proxy_for_server_errors(monkeypatch):
    calls = []
    def flaky(url, **kw):
        calls.append(kw)
        return _Resp(status=500)
    monkeypatch.setattr(http_mod.requests, "get", flaky)
    configure_proxies(["http://a:8080", "http://b:8080"])
    with pytest.raises(http_mod.requests.HTTPError):
        http_mod.http_get("https://example.com/jobs", max_retries=2)
    # both proxies answered, so neither is parked
    assert proxy_count() == 2
    assert next_proxy() in ("http://a:8080", "http://b:8080")
