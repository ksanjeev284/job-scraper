"""The Muse cross-board search (themuse.com public jobs API).

The Muse aggregates postings from thousands of employers and its
job-search page is backed by a public, no-authentication JSON API:

- Search: GET https://www.themuse.com/api/public/jobs?page=N&descending=true
          — JSON object with ``results`` (20 per page), ``total`` and
          ``page_count``. ``location`` and ``category`` are honored
          server-side (e.g. ``location=India``); there is no server-side
          keyword parameter, so keyword matching is done client-side on the
          title, company, levels/categories and the posting's description
          text. Pagination advances the ``page`` counter (0-based).

``search_themuse`` returns card dicts shaped like
``jobscraper.sources.linkedin.parse_search_cards``:
``job_id, title, company, location, url, posted_text`` (plus ``tags`` and
``source``), ready for the pipeline's generic fetch path. Each card's
``url`` is the posting's landing page on themuse.com.
"""

from __future__ import annotations

from urllib.parse import urlencode

from bs4 import BeautifulSoup

from jobscraper.http import http_get, polite_wait

THEMUSE_API_URL = "https://www.themuse.com/api/public/jobs"

#: Postings per API page (fixed by the API).
PAGE_SIZE = 20

#: Upper bound on pages scanned per search (400 cards), so a niche
#: keyword cannot turn into an unbounded crawl.
MAX_PAGES = 20


def _text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _description_text(contents: object) -> str:
    """Plain text of a posting's HTML ``contents``, for keyword matching."""
    if not contents:
        return ""
    soup = BeautifulSoup(str(contents), "html.parser")
    return soup.get_text(" ", strip=True)


def _location(job: dict) -> str | None:
    locations = job.get("locations")
    if isinstance(locations, list):
        names = [_text(loc.get("name"))
                 for loc in locations if isinstance(loc, dict)]
        joined = ", ".join(name for name in names if name)
        if joined:
            return joined
    return None


def _tags(job: dict) -> list[str]:
    tags: list[str] = []
    for key in ("levels", "categories"):
        entries = job.get(key)
        if isinstance(entries, list):
            tags.extend(_text(e.get("name"))
                        for e in entries if isinstance(e, dict))
    return [tag for tag in tags if tag]


def _parse_job(job: dict) -> tuple[dict, str]:
    """Parse one API result into a (card, description-text) pair."""
    job_id = job.get("id")
    company = job.get("company")
    company_name = (_text(company.get("name"))
                    if isinstance(company, dict) else None)
    refs = job.get("refs")
    url = (_text(refs.get("landing_page"))
           if isinstance(refs, dict) else None)
    card = {
        "job_id": f"themuse:{job_id}" if job_id
        else f"themuse:{url}",
        "title": _text(job.get("name")),
        "company": company_name,
        "location": _location(job),
        "url": url,
        "posted_text": _text(job.get("publication_date")),
        "tags": _tags(job),
        "source": "themuse",
    }
    return card, _description_text(job.get("contents"))


def parse_themuse_search(payload: object) -> list[dict]:
    """Parse a themuse.com /api/public/jobs payload into card dicts."""
    if not isinstance(payload, dict):
        return []
    results = payload.get("results")
    if not isinstance(results, list):
        return []
    cards: list[dict] = []
    for job in results:
        if not isinstance(job, dict):
            continue
        card, _ = _parse_job(job)
        cards.append(card)
    return cards


def matches_keywords(card: dict, description: str, keywords: str) -> bool:
    """True when every keyword token appears in the card's title, company,
    tags or description text (case-insensitive)."""
    haystack = " ".join([
        str(card.get("title") or ""),
        str(card.get("company") or ""),
        " ".join(str(tag) for tag in (card.get("tags") or [])),
        description,
    ]).lower()
    return all(token in haystack for token in keywords.lower().split())


def search_themuse(keywords: str,
                   location: str | None = None,
                   category: str | None = None,
                   limit: int = 25,
                   max_pages: int = MAX_PAGES) -> list[dict]:
    """Search every The Muse listing for ``keywords``.

    Pages through the public API until ``limit`` keyword-matching cards
    are collected, the result set is exhausted, or ``max_pages`` pages
    have been scanned. Keyword matching is client-side (the API has no
    keyword parameter); ``location`` and ``category`` narrow the
    server-side result set when given. HTTP errors propagate to the
    caller (the CLI reports them as a failed search), matching the
    LinkedIn/Workable sources' contract.
    """
    cards: list[dict] = []
    page = 0
    pages_scanned = 0
    while len(cards) < limit and pages_scanned < max_pages:
        params: dict[str, str] = {
            "page": str(page),
            "descending": "true",
        }
        if location:
            params["location"] = location
        if category:
            params["category"] = category
        polite_wait(THEMUSE_API_URL, base=1.5)
        payload = http_get(THEMUSE_API_URL + "?" +
                           urlencode(params)).json()
        results = (payload.get("results")
                   if isinstance(payload, dict) else None)
        if not isinstance(results, list) or not results:
            break
        for job in results:
            if not isinstance(job, dict):
                continue
            card, description = _parse_job(job)
            if not card.get("url"):
                continue
            if matches_keywords(card, description, keywords):
                cards.append(card)
            if len(cards) >= limit:
                break
        pages_scanned += 1
        page += 1
        if len(results) < PAGE_SIZE:
            break  # last page
    seen, unique = set(), []
    for card in cards:
        key = card.get("job_id")
        if key not in seen:
            seen.add(key)
            unique.append(card)
    return unique[:limit]
