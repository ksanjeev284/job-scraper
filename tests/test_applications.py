"""Tests for the application status tracker (applications.py)."""

from __future__ import annotations

import os
import sqlite3

import pytest

from jobscraper.applications import (
    VALID_STATUSES,
    application_summary,
    connect,
    delete_application,
    enrich_from_postings,
    get_application,
    list_applications,
    mark_applied,
    set_status,
)


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "apps.db")
    conn = connect(path)
    yield conn, path
    conn.close()


def test_mark_applied_creates_row(db):
    conn, _ = db
    app = mark_applied(conn, "https://example.com/jobs/1?utm_source=x",
                       title="Security Engineer", company="Acme",
                       location="Remote")
    assert app.status == "applied"
    assert app.title == "Security Engineer"
    assert app.company == "Acme"
    assert app.applied_date  # set on first mark
    assert app.updated_at
    assert app.notes is None


def test_mark_applied_canonicalizes_url(db):
    conn, _ = db
    mark_applied(conn, "https://example.com/jobs/1?utm_source=x")
    fetched = get_application(conn, "https://example.com/jobs/1#frag")
    assert fetched is not None
    # only one row despite the different spellings
    assert len(list_applications(conn)) == 1


def test_remark_updates_status_and_appends_notes(db):
    conn, _ = db
    first = mark_applied(conn, "https://example.com/jobs/2",
                         notes="first note")
    remark = mark_applied(conn, "https://example.com/jobs/2",
                          status="interviewing", notes="second note")
    assert remark.status == "interviewing"
    assert remark.applied_date == first.applied_date  # original kept
    assert "first note" in remark.notes
    assert "second note" in remark.notes


def test_remark_does_not_overwrite_title_with_none(db):
    conn, _ = db
    mark_applied(conn, "https://example.com/jobs/3",
                 title="Analyst", company="Beta")
    remark = mark_applied(conn, "https://example.com/jobs/3",
                          status="offer")
    assert remark.title == "Analyst"
    assert remark.company == "Beta"
    assert remark.status == "offer"


def test_invalid_status_rejected(db):
    conn, _ = db
    with pytest.raises(ValueError):
        mark_applied(conn, "https://example.com/jobs/4", status="hired")
    with pytest.raises(ValueError):
        set_status(conn, "https://example.com/jobs/4", status="hired")
    with pytest.raises(ValueError):
        list_applications(conn, status="hired")


def test_set_status_on_unknown_url_raises(db):
    conn, _ = db
    with pytest.raises(KeyError):
        set_status(conn, "https://example.com/jobs/missing",
                   "interviewing")


def test_get_unknown_returns_none(db):
    conn, _ = db
    assert get_application(conn, "https://example.com/jobs/nope") is None


def test_list_and_filter(db):
    conn, _ = db
    mark_applied(conn, "https://example.com/jobs/a")
    mark_applied(conn, "https://example.com/jobs/b", status="rejected")
    mark_applied(conn, "https://example.com/jobs/c", status="offer")
    all_apps = list_applications(conn)
    assert len(all_apps) == 3
    rejected = list_applications(conn, status="rejected")
    assert len(rejected) == 1
    assert rejected[0].status == "rejected"


def test_summary_counts_all_statuses(db):
    conn, _ = db
    summary = application_summary(conn)
    assert set(summary) == set(VALID_STATUSES)
    assert all(v == 0 for v in summary.values())
    mark_applied(conn, "https://example.com/jobs/a")
    mark_applied(conn, "https://example.com/jobs/b", status="rejected")
    summary = application_summary(conn)
    assert summary["applied"] == 1
    assert summary["rejected"] == 1
    assert summary["interviewing"] == 0


def test_delete(db):
    conn, _ = db
    mark_applied(conn, "https://example.com/jobs/del")
    assert delete_application(conn, "https://example.com/jobs/del") is True
    assert get_application(conn, "https://example.com/jobs/del") is None
    assert delete_application(conn, "https://example.com/jobs/del") is False


def test_connect_creates_parent_dirs(tmp_path):
    path = str(tmp_path / "nested" / "dir" / "apps.db")
    conn = connect(path)
    try:
        assert os.path.exists(path)
        mark_applied(conn, "https://example.com/jobs/x")
    finally:
        conn.close()


def test_connect_reopens_existing(db):
    conn, path = db
    mark_applied(conn, "https://example.com/jobs/keep", status="offer")
    conn.close()
    conn2 = connect(path)
    try:
        assert get_application(conn2,
                               "https://example.com/jobs/keep").status == \
            "offer"
    finally:
        conn2.close()


def test_enrich_from_postings_empty_db(db):
    conn, _ = db
    assert enrich_from_postings(conn, "https://example.com/jobs/z") == {}


def test_enrich_from_postings_shared_db(tmp_path):
    path = str(tmp_path / "shared.db")
    conn = connect(path)
    conn.execute(
        "CREATE TABLE postings (url TEXT PRIMARY KEY, title TEXT, "
        "company TEXT, location TEXT)")
    conn.execute(
        "INSERT INTO postings VALUES (?, ?, ?, ?)",
        ("https://example.com/jobs/enr", "Ops Engineer",
         "Gamma Inc", "Bengaluru"))
    conn.commit()
    details = enrich_from_postings(
        conn, "https://example.com/jobs/enr?utm_campaign=x")
    assert details == {"title": "Ops Engineer", "company": "Gamma Inc",
                       "location": "Bengaluru"}
    assert enrich_from_postings(
        conn, "https://example.com/jobs/other") == {}
    conn.close()


def test_applications_survive_postings_upsert(tmp_path):
    """mark_applied into a DB that also gets write_sqlite: no clobbering."""
    from jobscraper.models import Posting
    from jobscraper.reporting import write_sqlite

    path = str(tmp_path / "both.db")
    conn = connect(path)
    conn.close()
    conn = sqlite3.connect(path)
    mark_applied(conn, "https://example.com/jobs/shared",
                 title="DevOps", status="applied")
    conn.close()
    post = Posting(url="https://example.com/jobs/shared", title="DevOps")
    write_sqlite([post], path, {})
    conn = connect(path)
    try:
        assert get_application(
            conn, "https://example.com/jobs/shared").status == "applied"
    finally:
        conn.close()
