"""Application status tracking: record which postings were applied to.

The scraper finds jobs; the tracker remembers what happened next. Each
application is one row in a SQLite database keyed by the posting's
canonical URL, with a funnel status (applied -> interviewing -> offer,
or rejected/withdrawn), timestamps, and free-text notes.

The database is a separate table from the ``postings`` run-history
table, so it can share a database file with ``--sqlite`` or live in the
default ``~/.jobscraper/applications.db``. Nothing here is networked;
it is pure local bookkeeping (stdlib ``sqlite3`` only).
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone

from jobscraper.urls import canonicalize_url

VALID_STATUSES: tuple[str, ...] = (
    "applied",
    "interviewing",
    "offer",
    "rejected",
    "withdrawn",
)

DEFAULT_DB = os.path.join(
    os.path.expanduser("~"), ".jobscraper", "applications.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    url TEXT PRIMARY KEY,
    title TEXT,
    company TEXT,
    location TEXT,
    status TEXT NOT NULL DEFAULT 'applied',
    applied_date TEXT,
    updated_at TEXT,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_applications_status
    ON applications(status);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def connect(path: str) -> sqlite3.Connection:
    """Open the applications database, creating parent dirs as needed."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    return conn


@dataclass
class Application:
    """One tracked application."""

    url: str
    title: str | None = None
    company: str | None = None
    location: str | None = None
    status: str = "applied"
    applied_date: str | None = None
    updated_at: str | None = None
    notes: str | None = None
    extra: dict = field(default_factory=dict)


def _row_to_app(row: sqlite3.Row) -> Application:
    return Application(
        url=row["url"],
        title=row["title"],
        company=row["company"],
        location=row["location"],
        status=row["status"],
        applied_date=row["applied_date"],
        updated_at=row["updated_at"],
        notes=row["notes"],
    )


def _require_status(status: str) -> str:
    if status not in VALID_STATUSES:
        raise ValueError(
            f"unknown application status {status!r}; "
            f"expected one of {', '.join(VALID_STATUSES)}")
    return status


def mark_applied(conn: sqlite3.Connection, url: str, *,
                 status: str = "applied", title: str | None = None,
                 company: str | None = None, location: str | None = None,
                 notes: str | None = None) -> Application:
    """Record an application for ``url`` (upsert by canonical URL).

    The first call for a URL sets ``applied_date`` and creates the row;
    later calls update the status and ``updated_at``. New notes are
    appended to any existing notes; explicit ``title``/``company``/
    ``location`` only fill blanks, never overwrite what is already
    recorded.
    """
    _require_status(status)
    key = canonicalize_url(url)
    now = _now()
    existing = get_application(conn, key)
    if existing is None:
        conn.execute(
            """INSERT INTO applications
               (url, title, company, location, status,
                applied_date, updated_at, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (key, title, company, location, status, now, now, notes))
    else:
        merged_notes = existing.notes
        if notes:
            merged_notes = (existing.notes + "\n" + notes
                            if existing.notes else notes)
        conn.execute(
            """UPDATE applications
               SET status = ?,
                   title = COALESCE(title, ?),
                   company = COALESCE(company, ?),
                   location = COALESCE(location, ?),
                   updated_at = ?,
                   notes = ?
               WHERE url = ?""",
            (status, title, company, location, now, merged_notes, key))
    conn.commit()
    return get_application(conn, key)  # type: ignore[return-value]


def set_status(conn: sqlite3.Connection, url: str,
               status: str) -> Application:
    """Move an existing application to a new funnel status."""
    _require_status(status)
    key = canonicalize_url(url)
    existing = get_application(conn, key)
    if existing is None:
        raise KeyError(f"no application recorded for {url!r}")
    conn.execute(
        "UPDATE applications SET status = ?, updated_at = ? WHERE url = ?",
        (status, _now(), key))
    conn.commit()
    return get_application(conn, key)  # type: ignore[return-value]


def get_application(conn: sqlite3.Connection,
                    url: str) -> Application | None:
    """Fetch the application recorded for ``url``, or None."""
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM applications WHERE url = ?",
            (canonicalize_url(url),)).fetchone()
    finally:
        conn.row_factory = None
    return _row_to_app(row) if row else None


def list_applications(conn: sqlite3.Connection,
                      status: str | None = None) -> list[Application]:
    """All applications, newest first; optionally filtered to one status."""
    conn.row_factory = sqlite3.Row
    try:
        if status is None:
            rows = conn.execute(
                "SELECT * FROM applications "
                "ORDER BY updated_at DESC").fetchall()
        else:
            _require_status(status)
            rows = conn.execute(
                "SELECT * FROM applications WHERE status = ? "
                "ORDER BY updated_at DESC",
                (status,)).fetchall()
    finally:
        conn.row_factory = None
    return [_row_to_app(row) for row in rows]


def application_summary(conn: sqlite3.Connection) -> dict[str, int]:
    """Count of applications per status (keys are all VALID_STATUSES)."""
    summary = {status: 0 for status in VALID_STATUSES}
    for row in conn.execute(
            "SELECT status, COUNT(*) AS n FROM applications "
            "GROUP BY status"):
        if row[0] in summary:
            summary[row[0]] = row[1]
    return summary


def delete_application(conn: sqlite3.Connection, url: str) -> bool:
    """Remove the application recorded for ``url``; True if one existed."""
    cur = conn.execute(
        "DELETE FROM applications WHERE url = ?",
        (canonicalize_url(url),))
    conn.commit()
    return cur.rowcount > 0


def enrich_from_postings(conn: sqlite3.Connection,
                         url: str) -> dict[str, str | None]:
    """Look up title/company/location for ``url`` in the ``postings`` table.

    Returns {} when the run-history table is absent or has no such row, so
    callers can backfill application details from a shared ``--sqlite``
    database without failing on databases that only hold applications.
    """
    key = canonicalize_url(url)
    try:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    except sqlite3.Error:
        return {}
    if "postings" not in tables:
        return {}
    row = conn.execute(
        "SELECT title, company, location FROM postings WHERE url = ?",
        (key,)).fetchone()
    if row is None:
        return {}
    return {"title": row[0], "company": row[1], "location": row[2]}
