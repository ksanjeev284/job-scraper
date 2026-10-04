"""Tests for opt-in robots.txt honoring (http + pipeline wiring)."""

from __future__ import annotations

import pytest

from jobscraper import http as http_mod
from jobscraper.http import (
    RobotsDisallowedError,
    configure_robots,
    http_get,
    reset_robots,
    robots_allowed,
    robots_enabled,
    robots_from_env,
)

ROBOTS = """\
User-agent: jobscraper
Allow: /
User-agent: *
Disallow: /jobs/
Disallow: /careers/
"""


@pytest.fixture(autouse=True)
def _clean_robots_state():
    reset_robots()
    yield
    reset_robots()


@pytest.fixture(autouse=True)
def _no_waits(monkeypatch):
    monkeypatch.setattr(http_mod, "polite_wait", lambda url: None)
    monkeypatch.setattr(http_mod.time, "sleep", lambda *a, **k: None)


@pytest.fixture()
def canned_robots(monkeypatch):
    """Serve a canned robots.txt; returns the list of (scheme, host) seen."""
    seen: list[tuple[str, str]] = []

    def fetch(scheme: str, host: str) -> str | None:
        seen.append((scheme, host))
        return ROBOTS

    monkeypatch.setattr(http_mod, "_fetch_robots_text", fetch)
    return seen


def test_robots_disabled_by_default(canned_robots):
    assert not robots_enabled()
    assert robots_allowed("https://example.com/jobs/1") is True
    assert canned_robots == []  # no robots.txt fetched when disabled


def test_disallowed_path(canned_robots):
    configure_robots(True)
    assert robots_allowed("https://example.com/jobs/123") is False
    assert robots_allowed("https://example.com/careers/x") is False
    assert canned_robots == [("https", "example.com")]


def test_allowed_path(canned_robots):
    configure_robots(True)
    assert robots_allowed("https://example.com/jobs") is True  # prefix, not dir
    assert robots_allowed("https://example.com/about") is True
    assert robots_allowed("https://other.org/jobs/1") is False  # own fetch


def test_user_agent_specific_rules(canned_robots):
    configure_robots(True)
    # The "jobscraper" agent group allows everything; "*" does not.
    assert robots_allowed("https://example.com/jobs/123",
                          user_agent="jobscraper/1.0") is True
    assert robots_allowed("https://example.com/jobs/123",
                          user_agent="otherbot") is False


def test_robots_cached_per_host(canned_robots):
    configure_robots(True)
    robots_allowed("https://example.com/jobs/1")
    robots_allowed("https://example.com/jobs/2")
    robots_allowed("https://example.com/careers/x")
    assert canned_robots == [("https", "example.com")]  # one fetch, three URLs


def test_missing_robots_txt_is_allowed(monkeypatch):
    configure_robots(True)
    monkeypatch.setattr(http_mod, "_fetch_robots_text",
                        lambda scheme, host: None)
    assert robots_allowed("https://example.com/jobs/1") is True


def test_robots_fetch_error_is_allowed(monkeypatch):
    configure_robots(True)

    def boom(scheme: str, host: str) -> str:
        raise RuntimeError("dns down")

    monkeypatch.setattr(http_mod, "_fetch_robots_text", boom)
    assert robots_allowed("https://example.com/jobs/1") is True


def test_http_get_raises_when_disallowed(canned_robots):
    configure_robots(True)
    with pytest.raises(RobotsDisallowedError) as excinfo:
        http_get("https://example.com/jobs/123")
    assert "robots.txt" in str(excinfo.value)
    assert excinfo.value.url == "https://example.com/jobs/123"


def test_http_get_still_allows_other_paths(monkeypatch, canned_robots):
    configure_robots(True)
    resp = http_mod.requests.Response()
    resp.status_code = 200
    resp._content = b"ok"

    def fake_get(url, **kwargs):
        return resp

    monkeypatch.setattr(http_mod.requests, "get", fake_get)
    out = http_get("https://example.com/about")
    assert out.text == "ok"


def test_robots_from_env(monkeypatch):
    assert robots_from_env() is False
    for truthy in ("1", "true", "YES", " on "):
        monkeypatch.setenv("JOBSCRAPER_RESPECT_ROBOTS", truthy)
        assert robots_from_env() is True
    monkeypatch.setenv("JOBSCRAPER_RESPECT_ROBOTS", "no")
    assert robots_from_env() is False


def test_configure_robots_toggle():
    assert not robots_enabled()
    configure_robots(True)
    assert robots_enabled()
    configure_robots(False)
    assert not robots_enabled()


def test_run_pipeline_wires_flag(monkeypatch):
    from jobscraper import pipeline as pipe_mod

    calls: list[bool] = []
    monkeypatch.setattr(pipe_mod, "configure_robots", calls.append)
    results, new_count, closed = pipe_mod.run_pipeline(
        [], respect_robots=True)
    assert results == [] and new_count == 0 and closed == []
    assert calls == [True]
    pipe_mod.run_pipeline([], respect_robots=False)
    assert calls == [True, False]


def test_run_pipeline_honors_env(monkeypatch):
    from jobscraper import pipeline as pipe_mod

    calls: list[bool] = []
    monkeypatch.setattr(pipe_mod, "configure_robots", calls.append)
    monkeypatch.setenv("JOBSCRAPER_RESPECT_ROBOTS", "1")
    pipe_mod.run_pipeline([])
    assert calls == [True]
