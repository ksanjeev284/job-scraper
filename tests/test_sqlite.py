"""Tests for the SQLite export (upsert-by-URL history across runs)."""

import json
import sqlite3

from jobscraper.models import MatchResult, Posting
from jobscraper.reporting import write_sqlite


def _post(title, score=50, company="Acme", location="Hyderabad", **kw):
    post = Posting(url=f"https://example.com/{company}/{title}")
    post.company, post.title, post.location = company, title, location
    if score is not None:
        post.match = MatchResult(
            total=score,
            breakdown={"technical_skills": 20},
            weights={"technical_skills": 40},
            matched_skills=["Splunk"],
            skill_gaps=["Kubernetes"],
        )
    for key, value in kw.items():
        setattr(post, key, value)
    return post


def _rows(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return list(conn.execute("SELECT * FROM postings ORDER BY last_rank"))
    finally:
        conn.close()


def test_sqlite_writes_ranked_rows(tmp_path):
    db = str(tmp_path / "jobs.db")
    write_sqlite([_post("Junior Role", 30), _post("Senior Role", 90)],
                 db, {})
    rows = _rows(db)
    assert [r["title"] for r in rows] == ["Senior Role", "Junior Role"]
    assert [r["last_rank"] for r in rows] == [1, 2]
    assert [r["score"] for r in rows] == [90, 30]
    assert rows[0]["matched_skills"] == json.dumps(["Splunk"])
    assert rows[0]["skill_gaps"] == json.dumps(["Kubernetes"])


def test_sqlite_unscored_postings_rank_last_with_null_score(tmp_path):
    db = str(tmp_path / "jobs.db")
    write_sqlite([_post("No Match", None), _post("Good Match", 60)], db, {})
    rows = _rows(db)
    assert [r["title"] for r in rows] == ["Good Match", "No Match"]
    assert rows[1]["score"] is None


def test_sqlite_rerun_upserts_instead_of_duplicating(tmp_path):
    db = str(tmp_path / "jobs.db")
    posts = [_post("Senior Role", 90)]
    write_sqlite(posts, db, {})
    first = _rows(db)
    assert first[0]["scrape_count"] == 1
    assert first[0]["first_seen"] == first[0]["last_seen"]
    posts[0].match.total = 95
    write_sqlite(posts, db, {})
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0]["scrape_count"] == 2
    assert rows[0]["score"] == 95
    assert rows[0]["first_seen"] == first[0]["first_seen"]


def test_sqlite_live_flags_and_null_unknown(tmp_path):
    db = str(tmp_path / "jobs.db")
    write_sqlite([
        _post("Live", 50, is_live=True),
        _post("Closed", 40, is_live=False),
        _post("Unknown", 30),
    ], db, {})
    rows = {r["title"]: r["live"] for r in _rows(db)}
    assert rows == {"Live": 1, "Closed": 0, "Unknown": None}


def test_sqlite_raw_json_roundtrip_and_unicode(tmp_path):
    db = str(tmp_path / "jobs.db")
    title = "Analyste SOC élite – Montréal"
    write_sqlite([_post(title, 70, salary_hits=["₹18L-₹24L"])], db, {})
    rows = _rows(db)
    assert rows[0]["salary"] == "₹18L-₹24L"
    record = json.loads(rows[0]["raw_json"])
    assert record["title"] == title
    assert record["match"]["total"] == 70
