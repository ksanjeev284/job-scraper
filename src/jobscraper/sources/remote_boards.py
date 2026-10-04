"""Remote-only job boards: RemoteOK, Remotive, We Work Remotely, Working Nomads,
Jobicy, Arbeitnow and Himalayas.

All seven expose a public, no-authentication feed of remote-only listings
(JobSpy-style remote presets). Because every result is remote, the
``location`` argument that LinkedIn searches take is ignored here; callers
filter by keyword instead:

- RemoteOK:         GET https://remoteok.com/api — JSON array; the first
                    element is a legal/metadata notice, not a job. No
                    pagination (a fixed ~100 most-recent feed). Its
                    ``?tags=`` parameter ANDs multiple tags, so filtering is
                    done client-side. Terms ask callers to link back to the
                    posting and credit "Remote OK"; card URLs point at the
                    remoteok.com job page, which satisfies that.
- Remotive:         GET https://remotive.com/api/remote-jobs?search=..&limit=..
                    — public JSON API; server-side keyword search. Rate-limited
                    (~2 req/min, a few requests/day recommended), so exactly
                    one request is made per search.
- We Work Remotely: GET https://weworkremotely.com/remote-jobs.rss — RSS
                    feed; item titles are "Company: Title".
- Working Nomads:   GET https://www.workingnomads.com/api/exposed_jobs/ —
                    flat JSON array. ``url`` is a /job/go/<id>/ redirect to
                    the real posting.
- Jobicy:           GET https://jobicy.com/?feed=job_feed — RSS feed; company,
                    location, job type and category live in the
                    ``job_listing`` (https://jobicy.com) XML namespace.
- Arbeitnow:        GET https://www.arbeitnow.com/api/job-board-api — JSON
                    object with a ``data`` array of the most recent ~325
                    postings; ``created_at`` is a Unix timestamp. Company
                    cards carry a canonical ``url``.
- Himalayas:       GET https://himalayas.app/jobs/api — JSON object with a
                    ``jobs`` array and a ``nextCursor`` for further paging
                    (cursor preferred by the API); one ~20-job page is fetched
                    per search. ``pubDate`` is a Unix timestamp and
                    ``applicationLink`` is the canonical job URL.

Each search function returns card dicts shaped like
``jobscraper.sources.linkedin.parse_search_cards``:
``job_id, title, company, location, url, posted_text`` (plus ``tags`` and
``source``), ready for the pipeline's generic fetch path.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime, timezone
from urllib.parse import urlencode

from jobscraper.http import http_get, polite_wait

REMOTEOK_URL = "https://remoteok.com/api"
REMOTIVE_URL = "https://remotive.com/api/remote-jobs"
WWR_FEED_URL = "https://weworkremotely.com/remote-jobs.rss"
WORKINGNOMADS_URL = "https://www.workingnomads.com/api/exposed_jobs/"
JOBICY_FEED_URL = "https://jobicy.com/?feed=job_feed"
ARBEITNOW_URL = "https://www.arbeitnow.com/api/job-board-api"
HIMALAYAS_URL = "https://himalayas.app/jobs/api"
JOBICY_NS = "https://jobicy.com"

BOARDS = ("remoteok", "remotive", "weworkremotely", "workingnomads",
          "jobicy", "arbeitnow", "himalayas")


def _text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _epoch_to_iso(value: object) -> str | None:
    """Normalize a Unix timestamp (seconds) to an ISO 8601 UTC string."""
    try:
        epoch = float(str(value))
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def matches_keywords(card: dict, keywords: str) -> bool:
    """True when every keyword token appears in the card's title, company
    or tags (case-insensitive)."""
    haystack = " ".join([
        str(card.get("title") or ""),
        str(card.get("company") or ""),
        " ".join(str(tag) for tag in (card.get("tags") or [])),
    ]).lower()
    return all(token in haystack for token in keywords.lower().split())


def parse_remoteok(payload: object) -> list[dict]:
    """Parse a RemoteOK /api JSON payload into card dicts.

    ``payload`` is the decoded JSON array; its first element is a
    legal/metadata notice and is skipped.
    """
    if not isinstance(payload, list):
        return []
    cards: list[dict] = []
    for job in payload:
        if not isinstance(job, dict) or "position" not in job:
            continue  # legal notice or malformed entry
        job_id = str(job.get("id") or "")
        path = str(job.get("url") or "")
        url = ("https://remoteok.com" + path) if path.startswith("/") else path
        cards.append({
            "job_id": f"remoteok:{job_id}" if job_id else f"remoteok:{url}",
            "title": _text(job.get("position")),
            "company": _text(job.get("company")),
            "location": _text(job.get("location")) or "Remote",
            "url": url or None,
            "posted_text": _text(job.get("date")),
            "tags": list(job.get("tags") or []),
            "source": "remoteok",
        })
    return cards


def parse_remotive(payload: object) -> list[dict]:
    """Parse a Remotive /api/remote-jobs JSON payload into card dicts."""
    if not isinstance(payload, dict):
        return []
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        return []
    cards: list[dict] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        job_id = str(job.get("id") or "")
        location = _text(job.get("candidate_required_location")) or "Remote"
        cards.append({
            "job_id": f"remotive:{job_id}" if job_id else f"remotive:{job.get('url')}",
            "title": _text(job.get("title")),
            "company": _text(job.get("company_name")),
            "location": location,
            "url": _text(job.get("url")),
            "posted_text": _text(job.get("publication_date")),
            "tags": list(job.get("tags") or []),
            "source": "remotive",
        })
    return cards


def parse_wwr_rss(text: str) -> list[dict]:
    """Parse a We Work Remotely RSS feed into card dicts.

    Item titles follow the "Company: Title" convention.
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    cards: list[dict] = []
    for item in root.iter("item"):
        title_el = item.find("title")
        raw_title = title_el.text if title_el is not None else ""
        company, _, title = (raw_title or "").partition(":")
        title = title.strip() or (raw_title or "").strip()
        company = company.strip()
        link_el = item.find("link")
        link = (link_el.text or "").strip() if link_el is not None else ""
        pub_el = item.find("pubDate")
        pub = (pub_el.text or "").strip() if pub_el is not None else None
        cat_el = item.find("category")
        tags = [cat_el.text.strip()] if cat_el is not None and cat_el.text else []
        cards.append({
            "job_id": f"weworkremotely:{link}" if link else f"weworkremotely:{title}",
            "title": title or None,
            "company": company or None,
            "location": "Remote",
            "url": link or None,
            "posted_text": pub,
            "tags": tags,
            "source": "weworkremotely",
        })
    return cards


def parse_workingnomads(payload: object) -> list[dict]:
    """Parse a Working Nomads /api/exposed_jobs/ JSON payload into card dicts."""
    if not isinstance(payload, list):
        return []
    cards: list[dict] = []
    for job in payload:
        if not isinstance(job, dict):
            continue
        url = _text(job.get("url"))
        match = re.search(r"/job/go/(\d+)/?", url or "")
        job_id = f"workingnomads:{match.group(1)}" if match else f"workingnomads:{url}"
        tags = job.get("tags") or []
        cards.append({
            "job_id": job_id,
            "title": _text(job.get("title")),
            "company": _text(job.get("company_name")),
            "location": _text(job.get("location")) or "Remote",
            "url": url,
            "posted_text": _text(job.get("pub_date")),
            "tags": list(tags) if isinstance(tags, list) else [],
            "source": "workingnomads",
        })
    return cards


def fetch_remoteok() -> list[dict]:
    """Fetch RemoteOK's recent-jobs feed (browser UA; no pagination)."""
    polite_wait(REMOTEOK_URL, base=2.0)
    return parse_remoteok(http_get(REMOTEOK_URL).json())


def fetch_remotive(keywords: str, limit: int = 50) -> list[dict]:
    """Fetch Remotive with a server-side keyword search (one request)."""
    polite_wait(REMOTIVE_URL, base=2.0)
    params = {"search": keywords, "limit": str(limit)}
    data = http_get(REMOTIVE_URL + "?" + urlencode(params)).json()
    return parse_remotive(data)


def fetch_wwr() -> list[dict]:
    """Fetch the We Work Remotely full RSS feed."""
    polite_wait(WWR_FEED_URL, base=2.0)
    return parse_wwr_rss(http_get(WWR_FEED_URL).text)


def fetch_workingnomads() -> list[dict]:
    """Fetch the Working Nomads exposed-jobs feed."""
    polite_wait(WORKINGNOMADS_URL, base=2.0)
    return parse_workingnomads(http_get(WORKINGNOMADS_URL).json())


def parse_jobicy(text: str) -> list[dict]:
    """Parse a Jobicy RSS feed into card dicts.

    Company/location/job type/category live in the ``job_listing``
    (https://jobicy.com) XML namespace; title and link are plain RSS.
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []

    def job_field(item: ET.Element, name: str) -> str | None:
        return _text(item.findtext(f"{{{JOBICY_NS}}}{name}"))

    cards: list[dict] = []
    for item in root.iter("item"):
        link = _text(item.findtext("link"))
        cards.append({
            "job_id": f"jobicy:{link}" if link else f"jobicy:{item.findtext('title')}",
            "title": _text(item.findtext("title")),
            "company": job_field(item, "company"),
            "location": job_field(item, "location") or "Remote",
            "url": link,
            "posted_text": _text(item.findtext("pubDate")),
            "tags": [t for t in (job_field(item, "category"),
                                 job_field(item, "job_type")) if t],
            "source": "jobicy",
        })
    return cards


def parse_arbeitnow(payload: object) -> list[dict]:
    """Parse an Arbeitnow /api/job-board-api JSON payload into card dicts."""
    if not isinstance(payload, dict):
        return []
    jobs = payload.get("data")
    if not isinstance(jobs, list):
        return []
    cards: list[dict] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        url = _text(job.get("url"))
        location = _text(job.get("location"))
        if job.get("remote") and location:
            location = f"{location} (Remote)"
        elif not location:
            location = "Remote"
        tags = list(job.get("tags") or []) + list(job.get("job_types") or [])
        cards.append({
            "job_id": f"arbeitnow:{url}" if url else f"arbeitnow:{job.get('slug')}",
            "title": _text(job.get("title")),
            "company": _text(job.get("company_name")),
            "location": location,
            "url": url,
            "posted_text": _epoch_to_iso(job.get("created_at")),
            "tags": tags,
            "source": "arbeitnow",
        })
    return cards


def parse_himalayas(payload: object) -> list[dict]:
    """Parse a Himalayas /jobs/api JSON payload into card dicts."""
    if not isinstance(payload, dict):
        return []
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        return []
    cards: list[dict] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        url = _text(job.get("applicationLink")) or _text(job.get("guid"))
        restrictions = job.get("locationRestrictions") or []
        location = ", ".join(str(r) for r in restrictions) or "Remote"
        tags = list(job.get("categories") or [])
        slug = job.get("companySlug")
        cards.append({
            "job_id": f"himalayas:{url}" if url else f"himalayas:{slug}",
            "title": _text(job.get("title")),
            "company": _text(job.get("companyName")),
            "location": location,
            "url": url,
            "posted_text": _epoch_to_iso(job.get("pubDate")),
            "tags": tags,
            "source": "himalayas",
        })
    return cards


def fetch_jobicy() -> list[dict]:
    """Fetch the Jobicy remote-jobs RSS feed (one request)."""
    polite_wait(JOBICY_FEED_URL, base=2.0)
    return parse_jobicy(http_get(JOBICY_FEED_URL).text)


def fetch_arbeitnow() -> list[dict]:
    """Fetch the Arbeitnow job-board API (one request, no pagination)."""
    polite_wait(ARBEITNOW_URL, base=2.0)
    return parse_arbeitnow(http_get(ARBEITNOW_URL).json())


def fetch_himalayas() -> list[dict]:
    """Fetch one page of the Himalayas remote-jobs API.

    The API caps pages at ~20 jobs (cursor paging continues the feed, but
    one page per search keeps request volume low).
    """
    polite_wait(HIMALAYAS_URL, base=2.0)
    data = http_get(HIMALAYAS_URL + "?" + urlencode({"limit": "100"})).json()
    return parse_himalayas(data)


_FETCHERS: dict[str, Callable[[str, int], list[dict]]] = {
    "remoteok": lambda _kw, _limit: fetch_remoteok(),
    "remotive": lambda kw, limit: fetch_remotive(kw, limit=limit),
    "weworkremotely": lambda _kw, _limit: fetch_wwr(),
    "workingnomads": lambda _kw, _limit: fetch_workingnomads(),
    "jobicy": lambda _kw, _limit: fetch_jobicy(),
    "arbeitnow": lambda _kw, _limit: fetch_arbeitnow(),
    "himalayas": lambda _kw, _limit: fetch_himalayas(),
}


def search_remote_boards(keywords: str,
                         boards: tuple[str, ...] = BOARDS,
                         limit: int = 50) -> list[dict]:
    """Search remote-only boards for ``keywords``; returns card dicts.

    Each board is fetched at most once (Remotive is rate-limited).
    Client-side keyword matching is applied to every board, results are
    de-duplicated on (source, job_id) and then on (title, company), and
    capped at ``limit``. One board failing never kills the others.
    """
    cards: list[dict] = []
    for board in boards:
        fetcher = _FETCHERS.get(board)
        if fetcher is None:
            continue
        try:
            for card in fetcher(keywords, limit):
                if card.get("url") and matches_keywords(card, keywords):
                    cards.append(card)
        except Exception:
            continue
    seen, unique = set(), []
    for card in cards:
        key = (card.get("source"), card.get("job_id"))
        if key not in seen:
            seen.add(key)
            unique.append(card)
    seen_pairs, deduped = set(), []
    for card in unique:
        pair = (str(card.get("title") or "").lower(),
                str(card.get("company") or "").lower())
        if pair not in seen_pairs:
            seen_pairs.add(pair)
            deduped.append(card)
    return deduped[:limit]
