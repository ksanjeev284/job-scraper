"""Radancy (TalentBrew) ATS: posting fetch and whole-board discovery."""

from pathlib import Path

import jobscraper.boards as boards
from jobscraper.boards import board_name_for_url

FIX = Path(__file__).parent / "fixtures"
SEARCH_HTML = (FIX / "radancy_search.html").read_text(encoding="utf-8")
JOB_HTML = (FIX / "radancy_job.html").read_text(encoding="utf-8")

JOB_URL = ("https://careers.munichre.com/en/job/hartford/data-product-owner/"
           "3342/45469410752")


class _Resp:
    def __init__(self, text=""):
        self.text = text

    def json(self):  # pragma: no cover - radancy paths never parse JSON
        raise AssertionError("no JSON on the radancy path")


def _patch(monkeypatch, responder):
    monkeypatch.setattr(boards, "http_get", responder)
    monkeypatch.setattr(boards, "polite_wait", lambda *a, **k: None)


def test_fetch_radancy(monkeypatch):
    _patch(monkeypatch, lambda url, **kw: _Resp(JOB_HTML))
    meta = boards.fetch_radancy(JOB_URL)
    assert meta["title"] == "Data Product Owner"
    assert meta["company"] == "ERGO | Munich Re | MEAG"
    assert meta["location"] == "Hartford"
    assert meta["employment_type"] == "Full-Time"
    assert meta["posted"] == "2026-08-26"
    assert meta["source"] == "radancy-page"
    assert meta["position_url"] == JOB_URL
    assert len(meta["description_html"]) > 1000


def test_fetch_radancy_locationless_variant(monkeypatch):
    seen = []
    _patch(monkeypatch,
           lambda url, **kw: seen.append(url) or _Resp(JOB_HTML))
    boards.fetch_radancy(
        "https://careers.example.com/de/job/software-engineer/3382/123456789")
    assert seen and seen[0].startswith("https://careers.example.com/de/job/")


def test_fetch_radancy_non_matching_url_returns_none(monkeypatch):
    assert boards.fetch_radancy(
        "https://jobs.lever.co/spotify/abc-def-123") is None
    assert boards.fetch_radancy(
        "https://careers.munichre.com/en/search-jobs") is None


def test_fetch_radancy_no_json_ld_raises(monkeypatch):
    _patch(monkeypatch, lambda url, **kw: _Resp("<html><body>no data</body>"))
    try:
        boards.fetch_radancy(JOB_URL)
    except ValueError as exc:
        assert "radancy" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_discover_radancy(monkeypatch):
    calls = []

    def responder(url, **kw):
        calls.append(url)
        # page 1 has the three fixture cards; page 2+ repeat them, so the
        # walk stops once no NEW links appear
        return _Resp(SEARCH_HTML)

    _patch(monkeypatch, responder)
    urls = boards.discover_radancy("careers.munichre.com")
    assert len(urls) == 3
    assert urls[0] == ("https://careers.munichre.com/en/job/amelia/"
                       "customer-service-representative/3342/45474278592")
    assert all(u.startswith("https://careers.munichre.com/en/job/")
               for u in urls)
    assert calls[0] == "https://careers.munichre.com/en/search-jobs?p=1"
    assert calls[1] == "https://careers.munichre.com/en/search-jobs?p=2"


def test_discover_radancy_explicit_lang(monkeypatch):
    calls = []
    _patch(monkeypatch,
           lambda url, **kw: calls.append(url) or _Resp("<html></html>"))
    assert boards.discover_radancy("careers.munichre.com:de") == []
    assert calls[0] == "https://careers.munichre.com/de/search-jobs?p=1"


def test_discover_radancy_stops_on_empty_page(monkeypatch):
    empty = ('<section id="search-results" data-total-results="0">'
             '<ul id="search-results-jobs"></ul></section>')

    def responder(url, **kw):
        return _Resp(SEARCH_HTML if "p=1" in url else empty)

    _patch(monkeypatch, responder)
    urls = boards.discover_radancy("careers.munichre.com:en")
    assert len(urls) == 3


def test_discover_radancy_bad_spec():
    for bad in ("", ":en", "a:b:c"):
        try:
            boards.discover_radancy(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad!r}")
    try:
        boards.discover_radancy("careers.munichre.com:english")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for long language code")


def test_board_name_for_url_radancy():
    assert board_name_for_url(JOB_URL) == "radancy"
    assert board_name_for_url(
        "https://careers.munichre.com/en/search-jobs") == "generic"
    assert board_name_for_url("https://example.com/jobs/123") == "generic"
