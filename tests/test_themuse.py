"""Tests for The Muse cross-board search source (themuse.com public
jobs API).

Parsers are exercised against a frozen real-API fixture payload with no
network access; ``search_themuse`` pagination and keyword matching are
faked by stubbing the module's ``http_get``/``polite_wait``.
"""

from __future__ import annotations

import json
from pathlib import Path

from jobscraper.sources import themuse as tm

FIXTURES = Path(__file__).parent / "fixtures"


def _payload() -> dict:
    return json.loads((FIXTURES / "themuse-search.json").read_text(
        encoding="utf-8"))


def _job(job_id: int, title: str, company: str, description: str) -> dict:
    return {
        "id": job_id,
        "name": title,
        "company": {"id": 1, "short_name": company.lower(),
                    "name": company},
        "locations": [{"name": "Bangalore, India"}],
        "levels": [{"name": "Mid Level", "short_name": "mid"}],
        "categories": [{"name": "Engineering", "short_name": "eng"}],
        "refs": {"landing_page": f"https://www.themuse.com/jobs/x/{job_id}"},
        "publication_date": "2026-10-03T21:25:38Z",
        "contents": f"<p>{description}</p>",
    }


def _page(jobs: list[dict], total: int = 100) -> dict:
    return {"page": 0, "page_count": 5, "total": total, "results": jobs}


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

    monkeypatch.setattr(tm, "http_get", fake_get)
    monkeypatch.setattr(tm, "polite_wait", lambda *a, **k: None)
    return calls


def test_parse_themuse_search_card_shape():
    cards = tm.parse_themuse_search(_payload())
    assert len(cards) == 3
    first = cards[0]
    assert first["job_id"] == "themuse:22229841"
    assert first["title"] == ("Principal Technical Product Manager - "
                              "Commerce (Billing & Monetization)")
    assert first["company"] == "Atlassian"
    assert first["location"] == "Bangalore, India, Flexible / Remote"
    assert first["url"] == ("https://www.themuse.com/jobs/atlassian/"
                            "principal-technical-product-manager-commerce-"
                            "billing-monetization")
    assert first["posted_text"] == "2026-10-03T21:25:38Z"
    assert first["tags"] == ["Senior Level"]
    assert first["source"] == "themuse"


def test_parse_themuse_search_missing_fields():
    cards = tm.parse_themuse_search(_payload())
    sparse = cards[2]
    assert sparse["title"] == "Test Engineer (QA)"  # stripped
    assert sparse["company"] is None
    assert sparse["location"] is None
    assert sparse["url"] is None
    assert sparse["tags"] == []


def test_parse_themuse_search_rejects_malformed():
    assert tm.parse_themuse_search(None) == []
    assert tm.parse_themuse_search([]) == []
    assert tm.parse_themuse_search({"results": "nope"}) == []
    assert tm.parse_themuse_search({"results": [None, "x", 42]}) == []


def test_matches_keywords_title_and_description():
    card = {"title": "Security Engineer", "company": "Acme",
            "tags": ["Mid Level"]}
    assert tm.matches_keywords(card, "we need splunk and siem skills",
                                "splunk")
    assert tm.matches_keywords(card, "anything here",
                                "security engineer")
    assert not tm.matches_keywords(card, "we need splunk skills",
                                    "splunk python")
    assert tm.matches_keywords(card, "x", "SECURITY")  # case-insensitive


def test_description_text_strips_html():
    assert tm._description_text("<p>hello <strong>world</strong></p>") \
        == "hello world"
    assert tm._description_text(None) == ""
    assert tm._description_text("") == ""


def test_search_themuse_filters_client_side(monkeypatch):
    jobs = [
        _job(1, "Security Engineer", "Acme",
             "splunk siem detection engineering"),
        _job(2, "Frontend Developer", "Acme", "react typescript"),
    ]
    _stub(monkeypatch, [_page(jobs)])
    cards = tm.search_themuse("splunk")
    assert [c["job_id"] for c in cards] == ["themuse:1"]
    assert cards[0]["company"] == "Acme"


def test_search_themuse_paginates_until_limit(monkeypatch):
    page1 = [_job(i, f"Splunk Engineer {i}", "Acme", "splunk siem")
             for i in range(1, 21)]
    page2 = [_job(21, "Splunk Engineer 21", "Acme", "splunk siem")]
    calls = _stub(monkeypatch, [_page(page1), _page(page2, total=21)])
    cards = tm.search_themuse("splunk", limit=21)
    assert len(cards) == 21
    assert len(calls) == 2
    assert "page=1" in calls[1]


def test_search_themuse_honors_limit_without_extra_page(monkeypatch):
    page1 = [_job(i, f"Splunk Engineer {i}", "Acme", "splunk siem")
             for i in range(1, 21)]
    calls = _stub(monkeypatch, [_page(page1), _page([])])
    cards = tm.search_themuse("splunk", limit=10)
    assert len(cards) == 10
    assert len(calls) == 1  # no second request once the limit is met


def test_search_themuse_stops_on_short_last_page(monkeypatch):
    jobs = [_job(1, "Splunk Engineer", "Acme", "splunk")]
    calls = _stub(monkeypatch, [_page(jobs, total=1)])
    assert len(tm.search_themuse("splunk")) == 1
    assert len(calls) == 1


def test_search_themuse_respects_max_pages(monkeypatch):
    pages = [[_job(100 * p + i, "Splunk Engineer", "Acme", "splunk")
              for i in range(20)] for p in range(5)]
    calls = _stub(monkeypatch, [_page(p) for p in pages])
    cards = tm.search_themuse("splunk", limit=1000, max_pages=3)
    assert len(cards) == 60
    assert len(calls) == 3


def test_search_themuse_passes_location_and_category(monkeypatch):
    calls = _stub(monkeypatch, [_page(
        [_job(1, "Splunk Engineer", "Acme", "splunk")])])
    tm.search_themuse("splunk", location="India",
                      category="Data Science", limit=1)
    assert "location=India" in calls[0]
    assert "category=Data+Science" in calls[0]


def test_search_themuse_skips_cards_without_url(monkeypatch):
    jobs = [_job(1, "Splunk Engineer", "Acme", "splunk")]
    jobs[0]["refs"] = {}
    _stub(monkeypatch, [_page(jobs)])
    assert tm.search_themuse("splunk") == []


def test_search_themuse_dedupes_repeat_ids(monkeypatch):
    jobs = [_job(7, "Splunk Engineer", "Acme", "splunk"),
            _job(7, "Splunk Engineer", "Acme", "splunk")]
    _stub(monkeypatch, [_page(jobs)])
    cards = tm.search_themuse("splunk")
    assert [c["job_id"] for c in cards] == ["themuse:7"]


def test_search_themuse_propagates_http_errors(monkeypatch):
    def boom(url: str):
        raise ConnectionError("blocked")

    monkeypatch.setattr(tm, "http_get", boom)
    monkeypatch.setattr(tm, "polite_wait", lambda *a, **k: None)
    try:
        tm.search_themuse("splunk")
    except ConnectionError:
        pass
    else:
        raise AssertionError("expected the HTTP error to propagate")
