"""Tests for the Hacker News "Ask HN: Who is hiring?" source.

Thread discovery, comment fetching and card parsing are exercised
against frozen API fixtures with no network access; the module's
``http_get`` is stubbed by URL, the same pattern as test_themuse.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from jobscraper.scoring import load_profile
from jobscraper.sources import hn_hiring as hn

FIXTURES = Path(__file__).parent / "fixtures"

THREAD = {"story_id": 49922569,
          "title": "Ask HN: Who is hiring? (October 2026)",
          "time": 1790866927,
          "created_at": "2026-10-01T15:02:07Z",
          "num_comments": 280}

PIPE_COMMENT = {
    "id": 1,
    "by": "pondai",
    "time": 1790900000,
    "text": ("Pond AI | Head of Engineering (founding-level) | "
             "San Francisco | Onsite | Full-time"
             "<p>We build custom AI agents for non-tech businesses. "
             "Our security team runs on Splunk and Python, and we need "
             "someone with 5+ years of SOC experience."
             "<p>Apply at https://joinpond.ai/careers"),
}

DASH_COMMENT = {
    "id": 3,
    "by": "mwest",
    "time": 1790900100,
    "text": ("PrairieLearn (Remote US) \u2014 Full-Stack Software Engineer "
             "\u2014 TypeScript / Postgres / React / AI"
             "<p>PrairieLearn is an open-source assessment platform. "
             "Small, profitable, fully remote team."),
}

META_COMMENT = {
    "id": 4,
    "by": "dang",
    "time": 1790900200,
    "text": ("Please don't post job listings for other companies here. "
             "Post your own openings only."),
}


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _stub(monkeypatch, algolia=None, items=None):
    """Route the module's http_get by URL: Algolia or Firebase items."""
    items = items or {}

    def fake_get(url: str):
        if "algolia" in url:
            return _Resp(algolia if algolia is not None
                         else _fixture("hn-algolia-thread.json"))
        for item_id, payload in items.items():
            if f"/item/{item_id}.json" in url:
                return _Resp(payload)
        raise AssertionError(f"unexpected URL in test: {url}")

    monkeypatch.setattr(hn, "http_get", fake_get)


def test_find_hiring_thread_exact_month(monkeypatch):
    _stub(monkeypatch)
    thread = hn.find_hiring_thread("2026-10")
    assert thread["story_id"] == 49922569
    assert thread["title"] == "Ask HN: Who is hiring? (October 2026)"
    assert thread["time"] == 1790866927
    assert thread["num_comments"] == 280


def test_find_hiring_thread_falls_back_to_latest(monkeypatch):
    payload = {"hits": [
        {"objectID": "111", "title": "Ask HN: Who is hiring? (May 2026)",
         "author": "whoishiring", "created_at_i": 1, "num_comments": 10},
    ], "nbHits": 1}
    _stub(monkeypatch, algolia=payload)
    thread = hn.find_hiring_thread("2026-10")
    assert thread["story_id"] == 111


def test_find_hiring_thread_none_found(monkeypatch):
    _stub(monkeypatch, algolia={"hits": [], "nbHits": 0})
    try:
        hn.find_hiring_thread("2026-10")
    except RuntimeError as exc:
        assert "no" in str(exc).lower()
    else:
        raise AssertionError("expected RuntimeError")


def test_month_label_rejects_bad_format():
    try:
        hn._month_label("October 2026")
    except ValueError as exc:
        assert "YYYY-MM" in str(exc)
    else:
        raise AssertionError("expected ValueError")
    assert hn._month_label("2026-10") == "October 2026"


def test_fetch_thread_comments_skips_dead(monkeypatch):
    _stub(monkeypatch, items={
        49922569: _fixture("hn-story.json"),
        1: PIPE_COMMENT,
        2: {"id": 2, "deleted": True},
        3: {"id": 3, "dead": True},
        4: META_COMMENT,
    })
    comments = hn.fetch_thread_comments(49922569)
    assert [c["id"] for c in comments] == [1, 4]


def test_parse_comment_pipe_style():
    card = hn.parse_comment(PIPE_COMMENT, THREAD)
    assert card is not None
    assert card["job_id"] == "hn:1"
    assert card["company"] == "Pond AI"
    assert card["title"] == "Head of Engineering (founding-level)"
    assert card["location"] == "San Francisco, Onsite"
    assert card["url"] == "https://news.ycombinator.com/item?id=1"
    assert card["source"] == "hn_whoishiring"
    assert "Splunk" in card["description"]


def test_parse_comment_dash_style():
    card = hn.parse_comment(DASH_COMMENT, THREAD)
    assert card is not None
    assert card["company"] == "PrairieLearn (Remote US)"
    assert card["title"] == "Full-Stack Software Engineer"
    assert card["location"] == "TypeScript / Postgres / React / AI"


def test_parse_comment_skips_short_and_meta():
    assert hn.parse_comment(
        {"id": 9, "text": "hiring! dm me"}, THREAD) is None
    assert hn.parse_comment(META_COMMENT, THREAD) is None


def test_comment_matches_keywords_requires_all_tokens():
    card = hn.parse_comment(PIPE_COMMENT, THREAD)
    assert hn.comment_matches_keywords(card, "splunk engineer")
    assert hn.comment_matches_keywords(card, "SPLUNK")
    assert not hn.comment_matches_keywords(card, "splunk nurse")


def test_scrape_hn_hiring_limit_and_shape(monkeypatch):
    _stub(monkeypatch, items={
        49922569: _fixture("hn-story.json"),
        1: PIPE_COMMENT,
        2: {"id": 2, "deleted": True},
        3: DASH_COMMENT,
        4: META_COMMENT,
    })
    cards, thread = hn.scrape_hn_hiring("engineer", month="2026-10",
                                        limit=1)
    assert thread["story_id"] == 49922569
    assert len(cards) == 1
    assert cards[0]["job_id"] == "hn:1"
    cards, _ = hn.scrape_hn_hiring("engineer", month="2026-10",
                                   limit=25)
    assert len(cards) == 2


def _profile(tmp_path):
    data = {"skills": ["Splunk", "SIEM", "Python", "SOC"],
            "years_total": 4.5,
            "certs": ["Security+"],
            "locations": ["Hyderabad", "Bengaluru"],
            "current_ctc_lpa": 16.8}
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data))
    return load_profile(str(path))


def test_build_postings_enriches_like_url_postings(tmp_path):
    card = hn.parse_comment(PIPE_COMMENT, THREAD)
    postings = hn.build_postings([card], THREAD["time"],
                                 profile=_profile(tmp_path))
    assert len(postings) == 1
    post = postings[0]
    assert post.title == "Head of Engineering (founding-level)"
    assert post.company == "Pond AI"
    assert post.location == "San Francisco, Onsite"
    assert post.via == "hn_whoishiring"
    assert post.fetch_method == "hn-api"
    assert post.is_live is True
    assert post.posted == "2026-10-01"
    assert post.age_days is not None and post.age_days >= 0
    assert "Splunk" in post.skills_found
    assert "Python" in post.skills_found
    assert post.seniority in ("lead", "executive", "director", "manager",
                              "senior", "staff")
    assert post.match is not None and post.match.total > 0
    assert post.full_text_chars > 0


def test_build_postings_no_score(tmp_path):
    card = hn.parse_comment(PIPE_COMMENT, THREAD)
    postings = hn.build_postings([card], THREAD["time"],
                                 profile=_profile(tmp_path),
                                 no_score=True)
    assert postings[0].match is None
