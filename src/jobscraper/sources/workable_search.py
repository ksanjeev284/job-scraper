"""Workable cross-board search (jobs.workable.com).

Workable hosts career boards for thousands of employers, and its
cross-employer search is a public, no-authentication JSON API used by the
jobs.workable.com site itself:

- Search: GET https://jobs.workable.com/api/v1/jobs?query=..&limit=..
          — JSON object with ``jobs``, ``totalSize`` and ``nextPageToken``.
          The ``query`` parameter is honored server-side (unlike several
          other board search APIs), so no client-side keyword tightening is
          needed. Pagination advances by passing ``nextPageToken`` back as
          the ``pageToken`` parameter.
- Detail: each card links to its ``url`` (a ``jobs.workable.com/view/..``
          page), fetched by the pipeline's generic path. The API also
          returns the full description HTML per job, but cards only feed
          URLs into the pipeline, like the other search sources.

``search_workable`` returns card dicts shaped like
``jobscraper.sources.linkedin.parse_search_cards``:
``job_id, title, company, location, url, posted_text`` (plus ``tags`` and
``source``), ready for the pipeline's generic fetch path.
"""

from __future__ import annotations

from urllib.parse import urlencode

from jobscraper.http import http_get, polite_wait

WORKABLE_SEARCH_URL = "https://jobs.workable.com/api/v1/jobs"
PAGE_SIZE = 50


def _text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _location(job: dict) -> str | None:
    locations = job.get("locations")
    if isinstance(locations, list) and locations:
        first = _text(locations[0])
        if first:
            return first
    loc = job.get("location")
    if isinstance(loc, dict):
        parts = [_text(loc.get("city")), _text(loc.get("subregion")),
                 _text(loc.get("countryName") or loc.get("country"))]
        return ", ".join(part for part in parts if part) or None
    return _text(loc)


def parse_workable_search(payload: object) -> list[dict]:
    """Parse a jobs.workable.com /api/v1/jobs payload into card dicts."""
    if not isinstance(payload, dict):
        return []
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        return []
    cards: list[dict] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        job_id = job.get("id")
        company = job.get("company") or {}
        company_name = (_text(company.get("title"))
                        if isinstance(company, dict) else None)
        tags = [tag for tag in (
            _text(job.get("department")),
            _text(job.get("employmentType")),
            _text(job.get("workplace")),
        ) if tag]
        cards.append({
            "job_id": f"workable:{job_id}" if job_id
            else f"workable:{job.get('url')}",
            "title": _text(job.get("title")),
            "company": company_name,
            "location": _location(job),
            "url": _text(job.get("url")),
            "posted_text": _text(job.get("created")),
            "tags": tags,
            "source": "workable",
        })
    return cards


def search_workable(keywords: str, limit: int = 25) -> list[dict]:
    """Search every Workable-hosted board for ``keywords``.

    Pages through the public API (``pageToken`` cursor) until ``limit``
    cards are collected or the result set is exhausted. HTTP errors
    propagate to the caller (the CLI reports them as a failed search),
    matching the LinkedIn source's contract.
    """
    cards: list[dict] = []
    page_token: str | None = None
    while len(cards) < limit:
        params: dict[str, str] = {
            "query": keywords,
            "limit": str(min(PAGE_SIZE, limit - len(cards))),
        }
        if page_token:
            params["pageToken"] = page_token
        polite_wait(WORKABLE_SEARCH_URL, base=1.5)
        payload = http_get(WORKABLE_SEARCH_URL + "?" +
                           urlencode(params)).json()
        page_cards = parse_workable_search(payload)
        if not page_cards:
            break
        cards.extend(card for card in page_cards if card.get("url"))
        if len(cards) >= limit:
            break
        page_token = (payload.get("nextPageToken")
                      if isinstance(payload, dict) else None)
        if not page_token:
            break
    seen, unique = set(), []
    for card in cards:
        key = card.get("job_id")
        if key not in seen:
            seen.add(key)
            unique.append(card)
    return unique[:limit]
