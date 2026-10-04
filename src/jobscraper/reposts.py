"""Repost and ghost-job detection for scrape runs.

A posting that disappears from a board and reappears days later under a
new URL (or requisition ID) is usually a repost, not a new role -- and
postings that keep coming back after the job was closed are a classic
ghost-job signal. The fuzzy dedupe in :mod:`jobscraper.dedupe` merges
same-employer near-identical titles into one copy; this module catches
what dedupe cannot, and flags it instead of merging it:

* **In-run reposts**: two postings in the same run with the same content
  fingerprint (company, level-stripped title, location, and description
  body) but different canonical URLs. All but the first are flagged via
  ``post.signals["repost_of"]`` pointing at the canonical copy.

* **Historical reposts**: when ``--sqlite`` export is used, the SQLite
  database is a queryable history (see :func:`jobscraper.reporting.write_sqlite`).
  A fresh posting whose fingerprint matches a posting currently recorded
  as closed (``live = 0``) under a different URL is flagged via
  ``post.signals["repost_of_closed"]`` with a "possible ghost job" note.
  This never treats a fetch error as a closure: the check only reads the
  stored ``live`` column.

Postings without enough description body for a reliable fingerprint are
left unflagged (explicit unknown rather than a false match).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3

from jobscraper.dedupe import normalize_company, normalize_title
from jobscraper.models import Posting

#: Minimum unique description tokens for a fingerprint to count. Below
#: this the body is too thin to distinguish a repost from a coincidentally
#: similar posting, so the posting is left unflagged.
MIN_BODY_TOKENS = 10


def _body_tokens(record: dict) -> set[str]:
    """Collect the description tokens of a posting or a raw_json record.

    Accepts both live :class:`Posting` section objects and the plain
    ``dict`` sections stored by the SQLite export, so the same collector
    serves in-run and historical matching.
    """
    texts: list[str] = []
    for key in ("sections", "requirements", "responsibilities",
                "nice_to_have", "other_possibly_relevant"):
        for section in record.get(key) or []:
            if isinstance(section, dict):
                texts.append(section.get("text") or "")
            else:
                texts.append(getattr(section, "text", None) or "")
    words = re.findall(r"[a-z0-9]+", " ".join(texts).lower())
    return set(words)


def content_fingerprint(record: dict) -> str | None:
    """Stable fingerprint of a posting's content, or ``None`` when unknown.

    Hashes the normalized company, level-stripped title, normalized
    location, and the sorted unique description tokens. ``record`` may be
    a :class:`Posting` attribute dict or a raw_json record dict from the
    SQLite history.
    """
    get = record.get if isinstance(record, dict) else None
    if get is None:
        record = record.__dict__
        get = record.get
    company = normalize_company(get("company"))
    title = normalize_title(get("title"))
    if not company or not title:
        return None
    tokens = _body_tokens(record)
    if len(tokens) < MIN_BODY_TOKENS:
        return None
    location = normalize_company(get("location"))
    body_hash = hashlib.sha256(
        " ".join(sorted(tokens)).encode("utf-8")).hexdigest()
    digest = hashlib.sha256(
        f"{company}|{title}|{location}|{body_hash}".encode())
    return digest.hexdigest()


def detect_reposts_from_env() -> bool:
    """True when ``JOBSCRAPER_DETECT_REPOSTS`` is set to a truthy value."""
    return os.environ.get("JOBSCRAPER_DETECT_REPOSTS", "").strip().lower() in (
        "1", "true", "yes", "on")


def mark_reposts(posts: list[Posting]) -> int:
    """Flag in-run reposts; return how many postings were flagged.

    Postings sharing a content fingerprint but scraped under different
    canonical URLs are reposts of the same role. The first posting seen
    keeps its canonical status; the rest get
    ``signals["repost_of"] = <canonical url>`` plus a fetch note. Errored
    postings and postings without a fingerprint are never flagged.
    """
    first_seen: dict[str, str] = {}
    flagged = 0
    for post in posts:
        if post.error:
            continue
        fingerprint = content_fingerprint(post.__dict__)
        if fingerprint is None:
            continue
        canonical = first_seen.get(fingerprint)
        if canonical is None:
            first_seen[fingerprint] = post.url
        elif canonical != post.url:
            post.signals["repost_of"] = canonical
            post.fetch_notes.append(
                f"repost: same content as {canonical}")
            flagged += 1
    return flagged


def _closed_fingerprints(sqlite_path: str) -> dict[str, str]:
    """Map content fingerprint -> URL for postings stored as closed.

    Reads ``live = 0`` rows with a stored ``raw_json`` record. Any schema
    or JSON problem yields an empty mapping: a missing history is an
    explicit unknown, never a false repost flag.
    """
    if not os.path.exists(sqlite_path):
        return {}
    mapping: dict[str, str] = {}
    try:
        conn = sqlite3.connect(sqlite_path)
    except sqlite3.Error:
        return {}
    try:
        try:
            rows = conn.execute(
                "SELECT url, raw_json FROM postings WHERE live = 0"
            ).fetchall()
        except sqlite3.Error:
            return {}
        for url, raw_json in rows:
            if not raw_json:
                continue
            try:
                record = json.loads(raw_json)
            except (json.JSONDecodeError, TypeError):
                continue
            fingerprint = content_fingerprint(record)
            if fingerprint is not None:
                mapping.setdefault(fingerprint, url)
    finally:
        conn.close()
    return mapping


def flag_historical_reposts(posts: list[Posting], sqlite_path: str) -> int:
    """Flag fresh postings that resurrect previously closed ones.

    Returns how many postings were flagged. A fresh posting whose content
    fingerprint matches a ``live = 0`` row in the SQLite history under a
    different URL gets ``signals["repost_of_closed"] = <old url>`` plus a
    "possible ghost job" fetch note.
    """
    closed = _closed_fingerprints(sqlite_path)
    if not closed:
        return 0
    flagged = 0
    for post in posts:
        if post.error:
            continue
        fingerprint = content_fingerprint(post.__dict__)
        if fingerprint is None:
            continue
        old_url = closed.get(fingerprint)
        if old_url and old_url != post.url:
            post.signals["repost_of_closed"] = old_url
            post.fetch_notes.append(
                f"possible ghost job: repost of previously closed "
                f"posting {old_url}")
            flagged += 1
    return flagged
