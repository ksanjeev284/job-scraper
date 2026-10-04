"""Tests for jobscraper.reposts (repost / ghost-job detection)."""

import sqlite3

from jobscraper.models import Posting, Section
from jobscraper.reposts import (
    content_fingerprint,
    detect_reposts_from_env,
    flag_historical_reposts,
    mark_reposts,
)


def _body(n: int = 60) -> list[Section]:
    words = ("python django postgres aws kubernetes docker rest api testing "
             "ci cd linux monitoring logging alerting incident deploy "
             "automation scripting terraform security compliance audit "
             "review mentor design architecture review backlog sprint "
             "standup retrospective").split()
    text = " ".join((words * 3)[:n])
    return [Section(heading="About the role", text=text)]


def _posting(url: str, title: str = "Security Engineer",
             company: str = "Acme Corp", location: str = "Remote",
             body: int = 60) -> Posting:
    return Posting(url=url, title=title, company=company, location=location,
                   sections=_body(body))


def test_fingerprint_is_stable_and_url_independent():
    one = _posting("https://boards.example.com/jobs/123?utm_source=x")
    two = _posting("https://boards.example.com/jobs/456")
    assert content_fingerprint(one.__dict__) == content_fingerprint(two.__dict__)
    assert content_fingerprint(one.__dict__) is not None


def test_fingerprint_ignores_seniority_tokens():
    one = _posting("https://a.example/1", title="Senior Security Engineer")
    two = _posting("https://a.example/2", title="Security Engineer")
    assert content_fingerprint(one.__dict__) == content_fingerprint(two.__dict__)


def test_fingerprint_differs_on_body():
    one = _posting("https://a.example/1")
    other = _posting("https://a.example/2")
    other.sections = _body()
    other.sections[0].text = " ".join(
        f"unrelatedword{i}" for i in range(60))
    assert (content_fingerprint(one.__dict__)
            != content_fingerprint(other.__dict__))


def test_fingerprint_requires_company_and_body():
    assert content_fingerprint(_posting("https://a.example/1",
                                        company="").__dict__) is None
    assert content_fingerprint(_posting("https://a.example/1",
                                        body=0).__dict__) is None
    thin = _posting("https://a.example/1")
    thin.sections = [Section(heading="Role", text="great role apply now")]
    assert content_fingerprint(thin.__dict__) is None


def test_mark_reposts_flags_duplicates_not_canonical():
    posts = [_posting("https://a.example/1"),
             _posting("https://a.example/2"),
             _posting("https://a.example/3", title="Network Engineer")]
    flagged = mark_reposts(posts)
    assert flagged == 1
    assert posts[0].signals.get("repost_of") is None
    assert posts[1].signals["repost_of"] == "https://a.example/1"
    assert any(n.startswith("repost:") for n in posts[1].fetch_notes)
    assert posts[2].signals.get("repost_of") is None


def test_mark_reposts_ignores_errored_postings():
    posts = [_posting("https://a.example/1"),
             Posting(url="https://a.example/2", error="timeout")]
    assert mark_reposts(posts) == 0
    assert posts[1].signals.get("repost_of") is None


def test_mark_reposts_same_url_never_flags_itself():
    posts = [_posting("https://a.example/1"), _posting("https://a.example/1")]
    assert mark_reposts(posts) == 0


def test_detect_reposts_from_env(monkeypatch):
    monkeypatch.delenv("JOBSCRAPER_DETECT_REPOSTS", raising=False)
    assert detect_reposts_from_env() is False
    monkeypatch.setenv("JOBSCRAPER_DETECT_REPOSTS", "1")
    assert detect_reposts_from_env() is True


def test_flag_historical_reposts(tmp_path):
    db = tmp_path / "history.db"
    old = _posting("https://a.example/old")
    old.is_live = False
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE postings (url TEXT PRIMARY KEY, live INTEGER, "
                 "raw_json TEXT)")
    import json
    record = old.__dict__.copy()
    record["sections"] = [s.__dict__ for s in record["sections"]]
    conn.execute("INSERT INTO postings VALUES (?,?,?)",
                 (old.url, 0, json.dumps(record, default=str)))
    conn.commit()
    conn.close()

    fresh = _posting("https://a.example/new")
    flagged = flag_historical_reposts([fresh], str(db))
    assert flagged == 1
    assert fresh.signals["repost_of_closed"] == "https://a.example/old"
    assert any("ghost job" in n for n in fresh.fetch_notes)


def test_flag_historical_reposts_missing_db_is_unknown(tmp_path):
    fresh = _posting("https://a.example/new")
    assert flag_historical_reposts([fresh],
                                   str(tmp_path / "nope.db")) == 0
    assert "repost_of_closed" not in fresh.signals
