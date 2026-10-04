"""Tests for the remote-only job boards source (RemoteOK, Remotive,
We Work Remotely, Working Nomads).

Parsers are exercised against frozen fixture payloads with no network
access; only ``search_remote_boards``'s fetch dispatch is faked.
"""

from __future__ import annotations

import json
from pathlib import Path

from jobscraper.sources import remote_boards as rb

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_remoteok_skips_legal_notice():
    cards = rb.parse_remoteok(json.loads(_load("remoteok.json")))
    assert len(cards) == 2
    assert cards[0]["title"] == "Senior Security Engineer"
    assert cards[0]["company"] == "Northwind Labs"
    assert cards[0]["url"] == ("https://remoteok.com/remote-jobs/"
                               "912345-senior-security-engineer-northwind-labs")
    assert cards[0]["location"] == "Worldwide"
    assert cards[0]["posted_text"] == "2026-10-02T08:15:00"
    assert cards[0]["tags"] == ["security", "python", "aws"]
    assert cards[0]["source"] == "remoteok"
    # blank location normalizes to Remote
    assert cards[1]["location"] == "Remote"


def test_parse_remotive():
    cards = rb.parse_remotive(json.loads(_load("remotive.json")))
    assert len(cards) == 2
    first = cards[0]
    assert first["title"] == "Security Engineer (Detection)"
    assert first["company"] == "Bluepine Systems"
    assert first["location"] == "Worldwide"
    assert first["url"] == ("https://remotive.com/remote-jobs/security/"
                            "2201450-security-engineer-detection-bluepine")
    assert first["posted_text"] == "2026-10-03T09:30:00"
    assert first["tags"] == ["security", "detection", "siem"]
    assert first["source"] == "remotive"


def test_parse_wwr_rss_splits_company_from_title():
    cards = rb.parse_wwr_rss(_load("wwr.rss"))
    assert len(cards) == 2
    first = cards[0]
    assert first["company"] == "Clearline Security"
    assert first["title"] == "Security Operations Engineer"
    assert first["location"] == "Remote"
    assert first["url"] == ("https://weworkremotely.com/remote-jobs/"
                            "clearline-security-security-operations-engineer")
    assert first["source"] == "weworkremotely"


def test_parse_workingnomads_extracts_id_from_go_url():
    cards = rb.parse_workingnomads(json.loads(_load("workingnomads.json")))
    assert len(cards) == 2
    first = cards[0]
    assert first["job_id"] == "workingnomads:778899"
    assert first["title"] == "SOC Analyst, Remote"
    assert first["company"] == "Nightwatch Cyber"
    assert first["location"] == "Worldwide"
    assert first["url"] == "https://www.workingnomads.com/job/go/778899/"
    assert first["posted_text"] == "2026-10-03T07:00:00Z"
    assert first["source"] == "workingnomads"


def test_matches_keywords_requires_all_tokens():
    card = {"title": "Senior Security Engineer",
            "company": "Northwind Labs", "tags": ["security", "aws"]}
    assert rb.matches_keywords(card, "security engineer")
    assert rb.matches_keywords(card, "SECURITY AWS")
    assert not rb.matches_keywords(card, "security designer")
    # tags participate in matching
    assert rb.matches_keywords(card, "aws")


def test_malformed_payloads_return_empty():
    assert rb.parse_remoteok(None) == []
    assert rb.parse_remoteok({"error": "nope"}) == []
    assert rb.parse_remotive(None) == []
    assert rb.parse_remotive({"jobs": "garbage"}) == []
    assert rb.parse_wwr_rss("<not xml") == []
    assert rb.parse_workingnomads(None) == []
    assert rb.parse_workingnomads([42]) == []


def _fake_search(monkeypatch, results: dict[str, list[dict]]):
    def fake_fetcher_factory(cards):
        def fetch(_keywords, _limit):
            return cards
        return fetch
    monkeypatch.setattr(rb, "_FETCHERS",
                        {board: fake_fetcher_factory(cards)
                         for board, cards in results.items()})


def test_search_dedupes_cross_board_duplicates(monkeypatch):
    dup = {"job_id": "remotive:1", "title": "Security Engineer",
           "company": "Northwind Labs", "location": "Remote",
           "url": "https://remotive.example/x", "posted_text": None,
           "tags": ["security"], "source": "remotive"}
    other = dict(dup, job_id="remoteok:9", source="remoteok",
                 url="https://remoteok.example/y")
    other_board = {"job_id": "workingnomads:2", "title": "Security Engineer, Remote",
                   "company": "Nightwatch Cyber", "location": "Remote",
                   "url": "https://wn.example/z", "posted_text": None,
                   "tags": ["soc"], "source": "workingnomads"}
    _fake_search(monkeypatch, {"remoteok": [other], "remotive": [dup],
                               "workingnomads": [other_board]})
    cards = rb.search_remote_boards("security engineer")
    assert len(cards) == 2
    sources = {c["source"] for c in cards}
    assert sources == {"remoteok", "workingnomads"}


def test_search_filters_by_keyword_and_respects_limit(monkeypatch):
    matching = [{"job_id": f"remotive:{i}", "title": "Security Engineer",
                 "company": f"Company {i}", "location": "Remote",
                 "url": f"https://remotive.example/{i}", "posted_text": None,
                 "tags": [], "source": "remotive"} for i in range(10)]
    _fake_search(monkeypatch, {"remotive": matching})
    assert len(rb.search_remote_boards("security", limit=3)) == 3
    assert rb.search_remote_boards("dentist") == []
    assert rb.search_remote_boards("security", boards=("remoteok",)) == []


def test_search_survives_board_failure(monkeypatch):
    def boom(_keywords, _limit):
        raise RuntimeError("board down")
    ok_cards = [{"job_id": "remoteok:1", "title": "Security Engineer",
                 "company": "Northwind Labs", "location": "Remote",
                 "url": "https://remoteok.example/x", "posted_text": None,
                 "tags": [], "source": "remoteok"}]
    monkeypatch.setattr(rb, "_FETCHERS",
                        {"remoteok": boom,
                         "remotive": lambda _kw, _limit: ok_cards})
    cards = rb.search_remote_boards("security")
    assert len(cards) == 1
    assert cards[0]["source"] == "remoteok"


def test_search_ignores_unknown_board_names():
    cards = rb.search_remote_boards("security", boards=("nope",))
    assert cards == []
