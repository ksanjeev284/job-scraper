"""freehire.me cross-ATS job search (public JSON API, no authentication).

freehire.me is an open-source job aggregator that normalizes postings
from ~50 ATS platforms (Comeet, Ashby, Lever, Greenhouse, ...) into
one schema. Its public API needs no credentials:

- Search: GET https://freehire.me/api/v1/agent/jobs/search?q=KEYWORDS
          &limit=N&offset=M
          — JSON object with ``data`` (postings, up to ``limit`` each);
          pagination advances ``offset`` (the ``page`` parameter is
          ignored). ``q`` is a server-side full-text search over title,
          skills and description, so no client-side keyword matching is
          needed. Facet parameters narrow server-side: ``remote``
          (``remote``/``hybrid``/``onsite``), ``country`` (ISO-3166
          alpha-2, e.g. ``DE``), ``category`` (e.g. ``backend``),
          ``seniority`` (e.g. ``senior``). The base URL can be pointed
          at a self-hosted freehire instance (MIT-licensed,
          ``strelov1/freehire``) via ``JOBSCRAPER_FREEHIRE_API_URL``.

Each search hit already carries the full description HTML, so
``search_freehire`` returns card dicts shaped like
``jobscraper.sources.themuse.parse_themuse_search`` cards
(``job_id, title, company, location, url, posted_text``) plus the full
``description`` text, the employer's ``career_url`` (the original ATS
apply link) and the ``ats`` platform name. ``build_postings`` turns
those cards into scored :class:`~jobscraper.models.Posting` objects
via the pipeline's shared enrichment, so skills, seniority, salary and
scoring behave exactly like postings scraped from posting URLs — no
per-posting page fetch is needed. HTTP errors propagate to the caller
(the CLI reports them as a failed search), matching the
LinkedIn/Workable/The Muse sources' contract.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from jobscraper.http import http_get, polite_wait
from jobscraper.models import Posting, Section
from jobscraper.pipeline import enrich_posting

#: Hosted freehire API; override with a self-hosted instance.
FREEHIRE_API_URL = os.environ.get(
    "JOBSCRAPER_FREEHIRE_API_URL", "https://freehire.me")

SEARCH_PATH = "/api/v1/agent/jobs/search"
JOB_PAGE_URL = "https://freehire.me/jobs/{slug}"

#: Work-mode facet values freehire resolves (``--freehire-remote``).
REMOTE_MODES = ("remote", "hybrid", "onsite")

#: Results per API call; the API honors ``limit`` + ``offset``.
PAGE_SIZE = 25

#: Upper bound on pages scanned per search (500 cards), so a broad
#: keyword cannot turn into an unbounded crawl.
MAX_PAGES = 20


def _text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _description_text(html: object) -> str:
    """Plain text of a posting's HTML description."""
    if not html:
        return ""
    soup = BeautifulSoup(str(html), "html.parser")
    return soup.get_text("\n", strip=True)


def _posted_date(value: object) -> str | None:
    """Normalize an ISO ``posted_at`` timestamp to ``YYYY-MM-DD``."""
    text = _text(value)
    if not text:
        return None
    try:
        stamp = text.replace("Z", "+00:00")
        return datetime.fromisoformat(stamp).date().isoformat()
    except ValueError:
        return text[:10] or None


def _skills(item: dict[str, Any]) -> list[str]:
    skills = item.get("skills")
    if not isinstance(skills, list):
        return []
    return [text for text in (_text(skill) for skill in skills) if text]


def parse_freehire_job(item: dict[str, Any]) -> dict[str, Any] | None:
    """Parse one API result into a posting card, or None to skip it.

    The card's ``url`` is the stable freehire.me job page (canonical
    for dedupe, watch mode and application tracking); the employer's
    own ATS apply link is kept as ``career_url``.
    """
    if not isinstance(item, dict):
        return None
    slug = _text(item.get("public_slug"))
    title = _text(item.get("title"))
    if not slug or not title:
        return None
    return {
        "job_id": f"freehire:{slug}",
        "title": title,
        "company": _text(item.get("company")),
        "location": _text(item.get("location")),
        "url": JOB_PAGE_URL.format(slug=slug),
        "posted_text": _posted_date(item.get("posted_at")),
        "description": _description_text(item.get("description")),
        "tags": _skills(item),
        "source": "freehire",
        "ats": _text(item.get("source")),
        "career_url": _text(item.get("url")),
        "work_mode": _text(item.get("work_mode")),
    }


def search_freehire(keywords: str,
                    limit: int = 25,
                    remote: str | None = None,
                    country: str | None = None,
                    category: str | None = None,
                    seniority: str | None = None,
                    max_pages: int = MAX_PAGES) -> list[dict[str, Any]]:
    """Search freehire.me's aggregated postings for ``keywords``.

    ``q`` is matched server-side (title, skills, description), and the
    ``remote``/``country``/``category``/``seniority`` facets narrow the
    result set when given. Pages through the API (``limit`` + ``offset``)
    until ``limit`` cards are collected, the result set is exhausted,
    or ``max_pages`` pages have been scanned. HTTP errors propagate to
    the caller.
    """
    if remote and remote not in REMOTE_MODES:
        raise ValueError(
            f"bad remote mode {remote!r}: expected one of "
            f"{', '.join(REMOTE_MODES)}")
    cards: list[dict[str, Any]] = []
    base = {
        "q": keywords,
        "limit": str(max(limit, 1)),
    }
    if remote:
        base["remote"] = remote
    if country:
        base["country"] = country.upper()
    if category:
        base["category"] = category
    if seniority:
        base["seniority"] = seniority
    offset = 0
    for _ in range(max_pages):
        params = dict(base)
        params["offset"] = str(offset)
        endpoint = FREEHIRE_API_URL + SEARCH_PATH
        polite_wait(endpoint, base=1.5)
        payload = http_get(endpoint + "?" + urlencode(params)).json()
        results = (payload.get("data")
                   if isinstance(payload, dict) else None)
        if not isinstance(results, list) or not results:
            break
        for item in results:
            card = parse_freehire_job(item)
            if card is None:
                continue
            cards.append(card)
            if len(cards) >= limit:
                break
        if len(results) < PAGE_SIZE or len(cards) >= limit:
            break  # last page, or enough cards collected
        offset += len(results)
    seen, unique = set(), []
    for card in cards:
        key = card.get("job_id")
        if key not in seen:
            seen.add(key)
            unique.append(card)
    return unique[:limit]


def build_postings(cards: list[dict[str, Any]],
                   profile: dict | None = None,
                   tracker_path: str | None = None,
                   no_score: bool = False) -> list[Posting]:
    """Turn freehire search cards into fully enriched, scored postings.

    The API search already returns each posting's full description, so
    no page fetch happens; enrichment (skills, seniority, salary,
    signals, scoring) runs through the pipeline's shared
    :func:`enrich_posting`, identical to URL-scraped postings.
    """
    postings: list[Posting] = []
    for card in cards:
        post = Posting(url=str(card.get("url") or ""),
                       fetched_at=datetime.now().isoformat())
        post.via = "freehire"
        post.fetch_method = "freehire-api"
        post.is_live = True
        post.live_reason = ("full description fetched from the public "
                            "freehire.me search API")
        ats = card.get("ats")
        career_url = card.get("career_url")
        notes = []
        if ats:
            notes.append(f"ATS platform: {ats}")
        if career_url and career_url != post.url:
            notes.append(f"Employer apply link: {career_url}")
        description = str(card.get("description") or "")
        meta = {
            "title": card.get("title"),
            "company": card.get("company"),
            "location": card.get("location"),
            "posted": card.get("posted_text"),
            "source": "freehire",
        }
        sections = [Section("Description", description)]
        post = enrich_posting(post, meta, description, sections,
                              profile=profile,
                              tracker_path=tracker_path,
                              no_score=no_score)
        post.fetch_notes.extend(notes)
        postings.append(post)
    return postings
