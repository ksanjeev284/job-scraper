"""Tests for the Workable cross-board search source
(jobs.workable.com public search API) and the jobs.workable.com/view
board fetcher.

Parsers are exercised against frozen real-API fixture payloads with no
network access; ``search_workable`` pagination is faked by stubbing the
module's ``http_get``/``polite_wait``.
"""

from __future__ import annotations

import json
from pathlib import Path

import jobscraper.boards as boards
from jobscraper.sources import workable_search as ws

FIXTURES = Path(__file__).parent / "fixtures"


def _payload() -> dict:
    return json.loads((FIXTURES / "workable-search.json").read_text(
        encoding="utf-8"))


def _job(title: str, company: str, job_id: str) -> dict:
    return {
        "id": job_id,
        "title": title,
        "company": {"id": "c1", "title": company},
        "locations": ["Remote"],
        "location": {"city": None},
        "url": f"https://jobs.workable.com/view/{job_id}/x",
        "created": "2026-10-03T10:00:00.000Z",
        "department": "Technology",
        "employmentType": "Full-time",
        "workplace": "remote",
        "state": "published",
    }


def _page(jobs: list[dict], token: str | None = None) -> dict:
    payload: dict = {"totalSize": len(jobs), "jobs": jobs}
    if token:
        payload["nextPageToken"] = token
    return payload


class _Resp:
    def __init__(self, payload: dict):
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _stub(monkeypatch, pages: list[dict]):
    calls: list[str] = []

    def fake_get(url: str):
        calls.append(url)
        return _Resp(pages[min(len(calls) - 1, len(pages) - 1)])

    monkeypatch.setattr(ws, "http_get", fake_get)
    monkeypatch.setattr(ws, "polite_wait", lambda *a, **k: None)
    return calls


def test_parse_workable_search_card_shape():
    cards = ws.parse_workable_search(_payload())
    assert len(cards) == 3
    first = cards[0]
    assert first["job_id"] == "workable:712cd317-252e-4d5a-88be-5896c9b5023f"
    assert first["title"] == "Security Engineer"
    assert first["company"] == "LMAX Group"
    assert first["location"] == "London, England, United Kingdom"
    assert first["url"] == ("https://jobs.workable.com/view/"
                            "eYz4c3Arh3NUQwtZvK9j9p/"
                            "hybrid-security-engineer-in-london-at-lmax-group")
    assert first["posted_text"] == "2026-09-24T12:59:55.297Z"
    assert first["tags"] == ["Technology", "Full-time", "hybrid"]
    assert first["source"] == "workable"


def test_parse_workable_search_missing_fields():
    cards = ws.parse_workable_search(_payload())
    extra = cards[2]
    assert extra["title"] == "Security Engineer (Test)"  # stripped
    assert extra["company"] is None
    assert extra["location"] == "Berlin"  # location dict fallback
    assert extra["url"] is None
    assert extra["tags"] == ["Full-time"]  # None tags dropped


def test_parse_workable_search_rejects_malformed():
    assert ws.parse_workable_search(None) == []
    assert ws.parse_workable_search([]) == []
    assert ws.parse_workable_search({"jobs": "nope"}) == []
    assert ws.parse_workable_search({"jobs": [None, "x", 42]}) == []


def test_search_workable_paginates_with_page_token(monkeypatch):
    page1 = [_job(f"Security Engineer {i}", "Acme", f"id-{i}")
             for i in range(50)]
    page2 = [_job("Security Engineer 50", "Acme", "id-50")]
    calls = _stub(monkeypatch, [_page(page1, token="tok123"),
                                _page(page2)])
    cards = ws.search_workable("security engineer", limit=51)
    assert len(cards) == 51
    assert len(calls) == 2
    assert "pageToken=tok123" in calls[1]


def test_search_workable_honors_limit_without_extra_page(monkeypatch):
    page1 = [_job(f"Security Engineer {i}", "Acme", f"id-{i}")
             for i in range(50)]
    calls = _stub(monkeypatch, [_page(page1, token="tok123")])
    cards = ws.search_workable("security engineer", limit=25)
    assert len(cards) == 25
    assert len(calls) == 1  # no second request once the limit is met
    assert "limit=25" in calls[0]


def test_search_workable_stops_when_no_next_page(monkeypatch):
    calls = _stub(monkeypatch, [_page([_job("Security Engineer", "Acme",
                                             "id-1")])])
    cards = ws.search_workable("security engineer")
    assert len(cards) == 1
    assert len(calls) == 1


def test_search_workable_skips_cards_without_url(monkeypatch):
    jobs = [_job("Security Engineer", "Acme", "id-1"),
            {"id": "id-2", "title": "Security Engineer 2",
             "company": {"title": "Acme"}, "url": None}]
    _stub(monkeypatch, [_page(jobs)])
    cards = ws.search_workable("security engineer")
    assert [c["job_id"] for c in cards] == ["workable:id-1"]


def test_search_workable_dedupes_repeat_ids(monkeypatch):
    jobs = [_job("Security Engineer", "Acme", "same-id"),
            _job("Security Engineer", "Acme", "same-id")]
    _stub(monkeypatch, [_page(jobs)])
    assert len(ws.search_workable("security engineer")) == 1


def test_search_workable_propagates_http_errors(monkeypatch):
    def boom(url: str):
        raise ConnectionError("blocked")

    monkeypatch.setattr(ws, "http_get", boom)
    monkeypatch.setattr(ws, "polite_wait", lambda *a, **k: None)
    try:
        ws.search_workable("security engineer")
    except ConnectionError:
        pass
    else:
        raise AssertionError("expected the HTTP error to propagate")


def _view_fixture(monkeypatch):
    payload = json.loads((FIXTURES / "workable-view.json").read_text(
        encoding="utf-8"))
    monkeypatch.setattr(boards, "http_get",
                        lambda url, **kw: _Resp(payload))
    return payload


def test_fetch_workable_view(monkeypatch):
    _view_fixture(monkeypatch)
    meta = boards.fetch_workable_view(
        "https://jobs.workable.com/view/eYz4c3Arh3NUQwtZvK9j9p/"
        "hybrid-security-engineer-in-london-at-lmax-group")
    assert meta["title"] == "Security Engineer"
    assert meta["company"] == "LMAX Group"
    assert meta["location"] == "London, England, United Kingdom"
    assert meta["employment_type"] == "Full-time"
    assert meta["department"] == "Technology"
    assert meta["posted"] == "2026-09-24T12:59:55.297Z"
    assert meta["source"] == "workable-api"
    # description + requirements + benefits sections are concatenated
    assert "5+ years security engineering" in meta["description_html"]
    assert "25 days holiday" in meta["description_html"]


def test_fetch_workable_view_rejects_other_urls():
    assert boards.fetch_workable_view(
        "https://apply.workable.com/acme/j/ABC123/") is None
    assert boards.fetch_workable_view(
        "https://jobs.lever.co/acme/123") is None


def test_fetch_workable_view_rejects_malformed_payload(monkeypatch):
    monkeypatch.setattr(boards, "http_get",
                        lambda url, **kw: _Resp({"error": "not found"}))
    assert boards.fetch_workable_view(
        "https://jobs.workable.com/view/deadbeef/x") is None
