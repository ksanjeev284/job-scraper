"""Hacker News "Ask HN: Who is hiring?" thread search.

Every month the ``whoishiring`` account posts a "Who is hiring?" thread
on Hacker News and employers reply with top-level comments, usually in
the form ``Company | Role | Location | ...`` followed by a description.
This module treats the current month's thread as a job board:

- Thread discovery: the public Algolia HN API
  (``hn.algolia.com/api/v1/search_by_date``), no authentication, queried
  for the ``whoishiring`` author's monthly thread title.
- Comment fetch: the public Firebase HN API
  (``hacker-news.firebaseio.com/v0/item/{id}.json``), no
  authentication, top-level comments only.

``scrape_hn_hiring`` returns card dicts shaped like
``jobscraper.sources.themuse.parse_themuse_search`` cards
(``job_id, title, company, location, url, posted_text``) plus the full
``description`` text. ``build_postings`` turns those cards into scored
:class:`~jobscraper.models.Posting` objects via the pipeline's shared
enrichment, so skills, seniority, salary and scoring behave exactly
like postings scraped from posting URLs. HTTP errors propagate to the
caller (the CLI reports them as a failed search), matching the
LinkedIn/Workable/The Muse sources' contract.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from bs4 import BeautifulSoup

from jobscraper.http import http_get
from jobscraper.models import Posting, Section
from jobscraper.pipeline import enrich_posting

ALGOLIA_URL = "https://hn.algolia.com/api/v1/search_by_date"
FIREBASE_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{item}.json"
HN_ITEM_URL = "https://news.ycombinator.com/item?id={item}"
THREAD_AUTHOR = "whoishiring"

#: Upper bound on top-level comments fetched per thread, so a huge
#: thread cannot turn into an unbounded crawl.
DEFAULT_MAX_COMMENTS = 300

#: Comments shorter than this are almost always thread meta ("Please
#: don't post ...") rather than job postings.
MIN_COMMENT_CHARS = 40

#: First-line segments that are job-type noise, never a location.
_JOB_TYPE_TOKENS = re.compile(
    r"^(full[\s-]?time|part[\s-]?time|contract|contractor|internship|"
    r"intern|temporary|temp|freelance)$", re.IGNORECASE)

#: Segments that are bare URLs, never a job title.
_URL_LIKE = re.compile(r"^(https?://|\S+\.(com|io|ai|co|dev|tech|app)\b)",
                       re.IGNORECASE)


def _month_label(month: str | None) -> str:
    """Turn ``YYYY-MM`` (or None = current UTC month) into the thread
    title's month label, e.g. ``"October 2026"``."""
    if month is None:
        now = datetime.now(timezone.utc)
        return now.strftime("%B %Y")
    try:
        dt = datetime.strptime(month, "%Y-%m")
    except ValueError:
        raise ValueError(
            f"bad --hn-month {month!r}: expected YYYY-MM") from None
    return dt.strftime("%B %Y")


def find_hiring_thread(month: str | None = None) -> dict[str, Any]:
    """Locate the "Ask HN: Who is hiring?" thread for ``month``.

    Returns ``{"story_id", "title", "time", "created_at",
    "num_comments"}``. Prefers the exact "(Month YYYY)" thread; falls
    back to the author's most recent "Who is hiring?" thread when the
    month has no thread yet (e.g. early on the 1st). Raises
    RuntimeError when no thread is found.
    """
    label = _month_label(month)
    params = (f"?tags=story,author_{THREAD_AUTHOR}"
              f"&query=who%20is%20hiring%20{label.replace(' ', '%20')}")
    payload = http_get(ALGOLIA_URL + params).json()
    hits = payload.get("hits") if isinstance(payload, dict) else None
    if not isinstance(hits, list) or not hits:
        raise RuntimeError("Algolia returned no Who-is-hiring threads")
    exact = [h for h in hits
             if isinstance(h, dict) and f"({label})" in str(h.get("title"))]
    pool = exact or [h for h in hits
                     if isinstance(h, dict)
                     and "who is hiring" in str(h.get("title")).lower()]
    if not pool:
        raise RuntimeError(
            f"no 'Who is hiring?' thread found for {label}")
    hit = pool[0]
    story_id = hit.get("objectID")
    if not story_id:
        raise RuntimeError("Algolia hit has no story id")
    return {
        "story_id": int(story_id),
        "title": str(hit.get("title") or ""),
        "time": int(hit.get("created_at_i") or 0),
        "created_at": str(hit.get("created_at") or ""),
        "num_comments": int(hit.get("num_comments") or 0),
    }


def fetch_thread_comments(story_id: int,
                          max_comments: int = DEFAULT_MAX_COMMENTS,
                          workers: int = 8) -> list[dict[str, Any]]:
    """Fetch top-level comments of a thread via the Firebase HN API.

    Deleted, dead and textless comments are dropped. Comment fetch is
    parallelized; the story's own ``kids`` list caps the work.
    """
    story = http_get(FIREBASE_ITEM_URL.format(item=story_id)).json()
    if not isinstance(story, dict):
        raise RuntimeError(f"HN API returned no story {story_id}")
    kids = story.get("kids") or []
    ids = [kid for kid in kids if isinstance(kid, int)][:max_comments]

    comments: list[dict[str, Any]] = []

    def fetch(item_id: int) -> dict[str, Any] | None:
        payload = http_get(
            FIREBASE_ITEM_URL.format(item=item_id)).json()
        if not isinstance(payload, dict):
            return None
        if payload.get("deleted") or payload.get("dead"):
            return None
        if not payload.get("text"):
            return None
        return payload

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(fetch, item_id): item_id for item_id in ids}
        for future in as_completed(futures):
            comment = future.result()
            if comment is not None:
                comments.append(comment)
    comments.sort(key=lambda c: c.get("id", 0))
    return comments


def _header_segments(header: str) -> tuple[str | None, str | None,
                                           str | None]:
    """Split a comment's header line into (company, title, location).

    Handles the two common conventions: ``Company | Role | Location``
    and ``Company — Role — ...``. Segments that are bare URLs or
    job-type tokens are never treated as the title or location.
    """
    header = header.strip()
    if "|" in header:
        parts = [p.strip() for p in header.split("|")]
    elif " — " in header or " – " in header:
        parts = [p.strip()
                 for p in re.split(r"\s+[—–]\s+", header)]
    else:
        parts = [header]
    company = parts[0] or None
    title = parts[1] if len(parts) > 1 else None
    if title and _URL_LIKE.match(title):
        title = None
    location_bits = [p for p in parts[2:]
                     if p and not _JOB_TYPE_TOKENS.match(p)
                     and not _URL_LIKE.match(p)]
    location = ", ".join(location_bits) or None
    if not title:
        # Fallback title keeps the posting identifiable in reports.
        title = company + (f" — {location}" if location else "")
        title = title or header
    return company, title, location


def parse_comment(comment: dict[str, Any],
                  thread: dict[str, Any]) -> dict[str, Any] | None:
    """Parse one HN comment into a posting card, or None to skip it.

    The card carries the full comment text as ``description`` so the
    pipeline's keyword filter and skill/salary extractors see the same
    content a human reader would.
    """
    comment_id = comment.get("id")
    raw = str(comment.get("text") or "")
    soup = BeautifulSoup(raw, "html.parser")
    text = soup.get_text("\n", strip=True)
    if len(text) < MIN_COMMENT_CHARS:
        return None
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    header = lines[0]
    lowered = header.lower()
    if lowered.startswith("please ") or "who is hiring" in lowered:
        return None  # thread instructions, not a posting
    company, title, location = _header_segments(header)
    return {
        "job_id": f"hn:{comment_id}",
        "title": title,
        "company": company,
        "location": location,
        "url": HN_ITEM_URL.format(item=comment_id),
        "posted_text": thread.get("created_at"),
        "description": text,
        "source": "hn_whoishiring",
        "thread": thread.get("title"),
    }


def comment_matches_keywords(card: dict[str, Any],
                             keywords: str) -> bool:
    """True when every keyword token appears in the card's title,
    company or description text (case-insensitive)."""
    haystack = " ".join([
        str(card.get("title") or ""),
        str(card.get("company") or ""),
        str(card.get("description") or ""),
    ]).lower()
    return all(token in haystack for token in keywords.lower().split())


def scrape_hn_hiring(keywords: str,
                     month: str | None = None,
                     limit: int = 25,
                     max_comments: int = DEFAULT_MAX_COMMENTS
                     ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Scrape the Who-is-hiring thread for ``keywords``.

    Returns (cards, thread). Keyword matching is client-side: every
    token must appear in the comment's title, company or full text.
    Cards are capped at ``limit``; comment fetching is capped at
    ``max_comments``.
    """
    thread = find_hiring_thread(month)
    cards: list[dict[str, Any]] = []
    for comment in fetch_thread_comments(thread["story_id"],
                                         max_comments=max_comments):
        card = parse_comment(comment, thread)
        if card is None:
            continue
        if comment_matches_keywords(card, keywords):
            cards.append(card)
        if len(cards) >= limit:
            break
    seen, unique = set(), []
    for card in cards:
        key = card.get("job_id")
        if key not in seen:
            seen.add(key)
            unique.append(card)
    return unique[:limit], thread


def build_postings(cards: list[dict[str, Any]], story_time: int,
                   profile: dict | None = None,
                   tracker_path: str | None = None,
                   no_score: bool = False) -> list[Posting]:
    """Turn HN cards into fully enriched, scored postings.

    The comment text is the posting body, so no page fetch happens;
    enrichment (skills, seniority, salary, signals, scoring) runs
    through the pipeline's shared :func:`enrich_posting`, identical to
    URL-scraped postings.
    """
    posted = (datetime.fromtimestamp(story_time, tz=timezone.utc)
              .strftime("%Y-%m-%d")) if story_time else None
    postings: list[Posting] = []
    for card in cards:
        post = Posting(url=str(card.get("url") or ""),
                       fetched_at=datetime.now(timezone.utc).isoformat())
        post.via = "hn_whoishiring"
        post.fetch_method = "hn-api"
        post.is_live = True
        post.live_reason = ("comment text fetched from the public "
                            "Hacker News API")
        description = str(card.get("description") or "")
        meta = {
            "title": card.get("title"),
            "company": card.get("company"),
            "location": card.get("location"),
            "posted": posted,
            "source": "hn_whoishiring",
        }
        sections = [Section("Description", description)]
        postings.append(enrich_posting(post, meta, description, sections,
                                       profile=profile,
                                       tracker_path=tracker_path,
                                       no_score=no_score))
    return postings
