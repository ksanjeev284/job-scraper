"""Fetchers for ATS public job-board APIs.

Every fetcher takes a posting URL, returns a normalized dict with keys
``title, company, location, employment_type, department, description_html,
posted, source`` (plus optional extras), or ``None`` when the URL is not
for that board. All endpoints are public and need no authentication.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from urllib.parse import quote, urlsplit

import requests
from bs4 import BeautifulSoup

from jobscraper.extract import parse_json_ld
from jobscraper.http import get_ua, http_get, polite_wait
from jobscraper.sources.linkedin import fetch_linkedin

# A fetcher maps a posting URL to a normalized posting dict, or None.
Fetcher = Callable[[str], dict | None]


def fetch_lever(url: str) -> dict | None:
    """Lever public postings API: api.lever.co/v0/postings/{co}/{id}."""
    match = re.search(r"lever\.co/([^/]+)/([a-f0-9-]+)", url)
    if not match:
        return None
    data = http_get(
        f"https://api.lever.co/v0/postings/{match.group(1)}/{match.group(2)}"
    ).json()
    cats = data.get("categories", {}) or {}
    return {
        "title": data.get("text"),
        "company": data.get("company") or "",
        "location": data.get("location") or cats.get("location"),
        "employment_type": cats.get("commitment"),
        "department": cats.get("department"),
        "description_html": data.get("description", ""),
        "posted": data.get("createdAt"),
        "source": "lever-api",
    }


def fetch_ashby(url: str) -> dict | None:
    """Ashby public posting API: api.ashbyhq.com/posting-api/job-board."""
    match = re.search(r"jobs\.ashbyhq\.com/([^/]+)/([a-f0-9-]+)", url)
    if not match:
        return None
    data = http_get(
        "https://api.ashbyhq.com/posting-api/job-board/"
        f"{match.group(1)}/{match.group(2)}"
    ).json()
    loc = data.get("location") or {}
    return {
        "title": data.get("title"),
        "company": data.get("organizationName"),
        "location": loc.get("name") if isinstance(loc, dict) else loc,
        "employment_type": data.get("employmentType"),
        "department": data.get("departmentName"),
        "description_html": data.get("descriptionHtml", ""),
        "compensation": data.get("compensationTierSummary"),
        "posted": data.get("publishedAt"),
        "source": "ashby-api",
    }


def fetch_greenhouse(url: str) -> dict | None:
    """Greenhouse public boards API: boards-api.greenhouse.io."""
    match = re.search(r"boards\.greenhouse\.io/([^/]+)/jobs/(\d+)", url)
    if not match:
        match = re.search(r"job-boards\.greenhouse\.io/([^/]+)/jobs/(\d+)",
                          url)
    if not match:
        return None
    data = http_get(
        f"https://boards-api.greenhouse.io/v1/boards/{match.group(1)}/"
        f"jobs/{match.group(2)}?questions=true"
    ).json()
    loc = data.get("location") or {}
    offices = ", ".join(o.get("name", "")
                        for o in data.get("offices", []) or [])
    meta = data.get("metadata") or []
    return {
        "title": data.get("title"),
        "company": (data.get("company") or {}).get("name"),
        "location": loc.get("name") or offices,
        "employment_type": meta[0].get("value") if meta else None,
        "department": ", ".join(x.get("name", "")
                                for x in data.get("departments", []) or []),
        "description_html": data.get("content", ""),
        "posted": data.get("updated_at"),
        "source": "greenhouse-api",
    }


def fetch_smartrecruiters(url: str) -> dict | None:
    """SmartRecruiters public API: api.smartrecruiters.com/v1/companies."""
    match = re.search(r"jobs\.smartrecruiters\.com/([^/]+)/([a-f0-9-]+)", url)
    if not match:
        return None
    data = http_get(
        f"https://api.smartrecruiters.com/v1/companies/{match.group(1)}/"
        f"postings/{match.group(2)}"
    ).json()
    loc = data.get("location") or {}
    city = ", ".join(x for x in [loc.get("city"), loc.get("country")] if x)
    return {
        "title": data.get("name"),
        "company": (data.get("company") or {}).get("name"),
        "location": city or None,
        "employment_type": (data.get("employmentType") or {}).get("label"),
        "department": (data.get("department") or {}).get("label"),
        "description_html": (data.get("jobAd") or {}).get("sections", {}).get(
            "jobDescription", {}).get("text", ""),
        "posted": data.get("releasedDate"),
        "source": "smartrecruiters-api",
    }


def fetch_workday(url: str) -> dict | None:
    """Workday public CXS JSON API: {tenant}.wd{n}.myworkdayjobs.com.

    Matches e.g.
    https://covestro.wd3.myworkdayjobs.com/en-US/cov_external/job/..._JR-123-1
    """
    match = re.search(r"https://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com"
                      r"/[\w-]+/([\w-]+)/job/[\w-]+/([\w-]+)", url)
    if not match:
        return None
    tenant, dc, site, job_id = match.groups()
    data = http_get(
        f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/"
        f"{tenant}/{site}/job/{job_id}",
        max_retries=2,
    ).json()
    info = data.get("jobPostingInfo") or {}
    return {
        "title": info.get("title"),
        "company": tenant,
        "location": info.get("location", ""),
        "employment_type": info.get("timeType"),
        "department": None,
        "description_html": info.get("jobDescription", ""),
        "posted": info.get("postedOn") or info.get("startDate"),
        "source": "workday-api",
    }


def fetch_teamtailor(url: str) -> dict | None:
    """Teamtailor public jobs.json feed (JSON Feed 1.1, no auth).

    Matches career-site URLs like
    https://careers.example.com/en/jobs/8011996-some-role
    """
    match = re.search(r"https://([^/]+)/.*?/jobs/(\d+)", url)
    if not match:
        return None
    host, job_id = match.group(1), match.group(2)
    feed = http_get(f"https://{host}/jobs.json?per_page=100",
                    max_retries=2).json()
    items = feed.get("items", []) if isinstance(feed, dict) else []
    for item in items:
        ident = ((item.get("_jobposting") or {}).get("identifier") or {})
        if job_id in (str(item.get("id", "")), str(ident.get("value", "")),) \
                or job_id in (item.get("url") or ""):
            salary = ((item.get("_jobposting") or {}).get("baseSalary") or {})
            val = salary.get("value") or {}
            pay = None
            if val.get("minValue") or val.get("maxValue"):
                pay = (f"{salary.get('currency', '')} "
                       f"{val.get('minValue', '')}-{val.get('maxValue', '')}")
            return {
                "title": item.get("title"),
                "company": host.split(".")[0],
                "location": None,
                "employment_type": None,
                "department": None,
                "description_html": item.get("content_html", ""),
                "posted": item.get("date_published"),
                "salary_hits_extra": [pay] if pay else [],
                "source": "teamtailor-feed",
            }
    return None


def fetch_personio(url: str) -> dict | None:
    """Personio public XML feed: {company}.jobs.personio.de/xml (no auth).

    Matches https://<company>.jobs.personio.de/job/<id> (or .com).
    """
    match = re.search(r"https://([\w-]+)\.jobs\.personio\.(de|com)/job/(\d+)",
                      url)
    if not match:
        return None
    company, _tld, job_id = match.groups()
    xml_text = http_get(
        f"https://{company}.jobs.personio.de/xml?language=en",
        max_retries=2).text
    root = ET.fromstring(xml_text)
    for pos in root.iter("position"):
        if (pos.findtext("id") or "").strip() != job_id:
            continue
        descs = [jd.findtext("value") for jd in pos.iter("jobDescription")]
        return {
            "title": (pos.findtext("name") or "").strip(),
            "company": company,
            "location": (pos.findtext("office") or "").strip() or None,
            "employment_type": (pos.findtext("employmentType") or "").strip()
            or None,
            "department": (pos.findtext("department") or "").strip() or None,
            "description_html": "\n".join(d for d in descs if d),
            "posted": (pos.findtext("createdAt") or "").strip() or None,
            "source": "personio-xml",
        }
    return None


def fetch_recruitee(url: str) -> dict | None:
    """Recruitee public offers API: {company}.recruitee.com/api/offers/."""
    match = re.search(r"https://([\w-]+)\.recruitee\.com/", url)
    if not match:
        return None
    company = match.group(1)
    data = http_get(f"https://{company}.recruitee.com/api/offers/",
                    max_retries=2).json()
    offers = data.get("offers", []) if isinstance(data, dict) else []
    slug = url.rstrip("/").split("/")[-1].lower()
    for offer in offers:
        oid = str(offer.get("id", ""))
        ourl = (offer.get("careers_url") or offer.get("url") or "").lower()
        if slug and (slug in ourl or oid == slug):
            return {
                "title": offer.get("title"),
                "company": company,
                "location": offer.get("location"),
                "employment_type": offer.get("employment_type"),
                "department": offer.get("department"),
                "description_html": offer.get("description", ""),
                "posted": offer.get("created_at") or offer.get("published_at"),
                "source": "recruitee-api",
            }
    return None


def fetch_workable(url: str) -> dict | None:
    """Workable public widget API: apply.workable.com/api/v1/widget.

    Matches https://apply.workable.com/<company>/j/<id>/ style URLs.
    """
    match = re.search(r"apply\.workable\.com/([\w-]+)/j/([\w-]+)", url)
    if not match:
        return None
    company, job_code = match.groups()
    data = http_get("https://apply.workable.com/api/v1/widget/accounts/"
                    f"{company}?details=true", max_retries=2).json()
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    for job in jobs:
        if job_code.lower() in (str(job.get("shortcode", "")).lower(),
                                str(job.get("id", "")).lower()):
            loc = job.get("location")
            loc_str = loc.get("location_str") if isinstance(loc, dict) else loc
            return {
                "title": job.get("title"),
                "company": company,
                "location": loc_str,
                "employment_type": job.get("employment_type"),
                "department": job.get("department"),
                "description_html": job.get("description", ""),
                "posted": job.get("published") or job.get("created_at"),
                "source": "workable-api",
            }
    return None


def fetch_workable_view(url: str) -> dict | None:
    """Workable cross-board job view: jobs.workable.com/view/<token>/...

    Matches https://jobs.workable.com/view/<token>/<slug> URLs produced by
    --workable-search; the per-job JSON API behind the view page returns
    the full posting, so no page scrape is needed.
    """
    match = re.search(r"jobs\.workable\.com/view/([\w-]+)", url)
    if not match:
        return None
    token = match.group(1)
    data = http_get(f"https://jobs.workable.com/api/v1/jobs/{token}",
                    max_retries=2).json()
    if not isinstance(data, dict) or "title" not in data:
        return None
    company = data.get("company") or {}
    locations = data.get("locations") or []
    description = "\n".join(part for part in (
        data.get("description") or "",
        data.get("requirementsSection") or "",
        data.get("benefitsSection") or "",
    ) if part)
    return {
        "title": data.get("title"),
        "company": (company.get("title")
                    if isinstance(company, dict) else None),
        "location": locations[0] if locations else None,
        "employment_type": data.get("employmentType"),
        "department": data.get("department"),
        "description_html": description,
        "posted": data.get("created"),
        "position_url": data.get("url"),
        "source": "workable-api",
    }


def fetch_breezy(url: str) -> dict | None:
    """Breezy HR: public board feed at {tenant}.breezy.hr/json.

    Matches https://{tenant}.breezy.hr/p/{id} style posting URLs; the
    position page itself carries JobPosting JSON-LD for the description.
    """
    match = re.search(r"https://([\w-]+)\.breezy\.hr/p/([\w-]+)", url)
    if not match:
        return None
    tenant, position_id = match.groups()
    feed = http_get(f"https://{tenant}.breezy.hr/json",
                    max_retries=2).json()
    for item in feed if isinstance(feed, list) else []:
        if position_id not in (item.get("url") or ""):
            continue
        loc = item.get("location") or {}
        loc_name = loc.get("name") if isinstance(loc, dict) else loc
        return {
            "title": item.get("name"),
            "company": tenant,
            "location": loc_name,
            "employment_type": (item.get("type") or {}).get("name")
            if isinstance(item.get("type"), dict) else item.get("type"),
            "department": None,
            "description_html": "",  # pipeline falls back to page scrape
            "posted": item.get("published_date"),
            "position_url": item.get("url"),
            "source": "breezy-feed",
        }
    return None


def _pinpoint_matches(item: dict, url: str) -> bool:
    """True if a ``postings.json`` item is the posting at ``url``."""
    item_url = (item.get("url") or "").rstrip("/")
    if item_url and item_url.lower() == url.rstrip("/").lower():
        return True
    item_id = str(item.get("id") or "")
    if item_id:
        # /en/jobs/399793/hiring-process style numeric URLs
        if re.search(rf"/jobs/{re.escape(item_id)}(?:[/?#]|$)", url):
            return True
        # the feed's /en/postings/<uuid> path
        if re.search(rf"/postings/{re.escape(item_id)}(?:[/?#]|$)",
                     item_url, re.IGNORECASE):
            return True
    return False


def _pinpoint_location(item: dict) -> str | None:
    """Build a location string from a Pinpoint payload."""
    loc = item.get("location") or {}
    city = (loc.get("city") or "").strip()
    name = (loc.get("name") or "").strip()
    workplace = (item.get("workplace_type") or "").lower()
    if workplace == "remote":
        return "Remote"
    bits = [b for b in (name, city) if b]
    return ", ".join(dict.fromkeys(bits)) or None


def _pinpoint_salary_hint(item: dict) -> str | None:
    """Format visible Pinpoint compensation as extractable salary text."""
    if not item.get("compensation_visible"):
        return None
    lo, hi = item.get("compensation_minimum"), item.get("compensation_maximum")
    if not lo and not hi:
        return None
    cur = (item.get("compensation_currency") or "").strip()
    freq = (item.get("compensation_frequency") or "").strip()
    band = "-".join(str(v) for v in (lo, hi) if v)
    return " ".join(x for x in (cur, band, freq) if x) or None


def fetch_pinpoint(url: str) -> dict | None:
    """Pinpoint public feed: {slug}.pinpointhq.com/postings.json.

    The same no-auth feed powers whole-board discovery
    (``discover_pinpoint``) and carries the full posting payload:
    description HTML, benefits, workplace and employment type, location
    and compensation. Matches both the ``/jobs/<id>/`` posting URLs and
    the ``/postings/<uuid>`` URLs the feed emits.
    """
    match = re.search(r"https://([\w-]+)\.pinpointhq\.com/"
                      r"(?:[\w-]+/)?(?:jobs|postings)/[\w-]+", url)
    if not match:
        return None
    slug = match.group(1)
    feed = http_get(f"https://{slug}.pinpointhq.com/postings.json",
                    max_retries=2).json()
    if isinstance(feed, dict):
        items = feed.get("data") or feed.get("postings") or []
    elif isinstance(feed, list):
        items = feed
    else:
        items = []
    for item in items:
        if not isinstance(item, dict) or not _pinpoint_matches(item, url):
            continue
        desc_html = item.get("description") or ""
        for body_key, head_key, fallback in (
                ("key_responsibilities", "key_responsibilities_header",
                 "Responsibilities"),
                ("skills_knowledge_expertise",
                 "skills_knowledge_expertise_header",
                 "Skills & expertise"),
                ("benefits", "benefits_header", "Benefits")):
            body = (item.get(body_key) or "").strip()
            if body:
                head = (item.get(head_key) or fallback).strip()
                desc_html += f"\n<h2>{html.escape(head)}</h2>\n{body}"
        pay = _pinpoint_salary_hint(item)
        return {
            "title": item.get("title"),
            "company": slug,
            "location": _pinpoint_location(item),
            "employment_type": item.get("employment_type_text"),
            "department": None,
            "description_html": desc_html,
            "posted": None,
            "salary_hits_extra": [pay] if pay else [],
            "position_url": item.get("url") or url,
            "source": "pinpoint-feed",
        }
    return None


def _join_markdown_inline(text: str) -> str:
    """Render inline markdown (bold/italic/links) as safe HTML."""
    out = html.escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", out)
    out = re.sub(r"\[(.+?)\]\((https?://[^)\s]+)\)",
                 r'<a href="\2">\1</a>', out)
    return out


def _join_markdown_to_html(text: str) -> str:
    """Convert join.com's lightweight markdown description to safe HTML.

    Handles ``##`` headings, ``* `` bullet lists, paragraphs and inline
    bold/italic/links. Everything else is HTML-escaped paragraph text.
    """
    out: list[str] = []
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("### "):
            close_list()
            out.append(f"<h4>{_join_markdown_inline(stripped[4:])}</h4>")
        elif stripped.startswith("## "):
            close_list()
            out.append(f"<h3>{_join_markdown_inline(stripped[3:])}</h3>")
        elif stripped.startswith("* "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_join_markdown_inline(stripped[2:])}</li>")
        elif not stripped:
            close_list()
        else:
            close_list()
            out.append(f"<p>{_join_markdown_inline(stripped)}</p>")
    close_list()
    return "\n".join(out)


def _join_location(data: dict) -> str | None:
    """Build a ``City, Country`` location from a join.com job payload."""
    city = (data.get("city") or {}).get("cityName")
    country = (data.get("country") or {}).get("name")
    workplace = (data.get("workplaceType") or "").upper()
    if workplace == "REMOTE":
        return f"Remote, {country}" if country else "Remote"
    loc = ", ".join(x for x in (city, country) if x)
    if workplace == "HYBRID" and loc:
        loc += " (Hybrid)"
    # The detail endpoint omits city/country; fall back to the company's
    # free-text address ("Street, City, Country").
    return loc or data.get("companyLocation") or None


def fetch_join(url: str) -> dict | None:
    """join.com public API (EU-focused ATS, no auth).

    Matches https://join.com/companies/<slug>/<id>-<title-slug>.
    Archived postings 404 the API, which the pipeline treats as closed.
    """
    match = re.search(r"join\.com/(?:companies/[\w-]+/)?(\d+-[\w-]+)",
                      url)
    if not match:
        return None
    data = http_get(f"https://join.com/api/public/jobs/{match.group(1)}",
                    max_retries=2).json()
    if not isinstance(data, dict):
        return None
    company = data.get("company") or {}
    return {
        "title": data.get("title"),
        "company": company.get("name"),
        "location": _join_location(data),
        "employment_type": (data.get("employmentType") or {}).get("name"),
        "department": (data.get("category") or {}).get("name"),
        "description_html": _join_markdown_to_html(
            data.get("description") or ""),
        "posted": data.get("createdAt"),
        "source": "join-api",
    }


def fetch_eightfold(url: str) -> dict | None:
    """Eightfold AI: ``{tenant}.eightfold.ai`` career boards.

    Matches e.g. https://paypal.eightfold.ai/careers/job/274922421796
    (and the /career_detail/<id> variant). Posting pages are
    server-rendered with JobPosting JSON-LD, so the description is read
    over plain HTTP with no browser involved.
    """
    match = re.search(
        r"https://([\w-]+)\.eightfold\.ai/(?:careers/job|career_detail)/"
        r"([\w-]+)", url)
    if not match:
        return None
    html_text = http_get(url, max_retries=2).text
    meta = parse_json_ld(BeautifulSoup(html_text, "html.parser"))
    if not meta:
        raise ValueError("eightfold: no JobPosting structured data on page")
    meta["source"] = "eightfold-page"
    meta["position_url"] = url
    return meta


# Radancy (TalentBrew) job pages live on tenant-branded domains, e.g.
# https://careers.munichre.com/en/job/hartford/data-product-owner/3342/45469410752
_RADANCY_JOB_URL = re.compile(
    r"^https?://[^/]+/[a-z]{2}/job/(?:[^/]+/)?[^/]+/\d+/\d+/?$")


def fetch_radancy(url: str) -> dict | None:
    """Radancy (TalentBrew) career sites: server-rendered job pages.

    Matches e.g. https://careers.munichre.com/en/job/hartford/data-product-owner/3342/45469410752
    (and the location-less /job/<slug>/<category>/<id> variant).
    Posting pages carry schema.org JobPosting JSON-LD, so plain HTTP
    suffices with no browser and no auth.
    """
    if not _RADANCY_JOB_URL.match(url):
        return None
    html_text = http_get(url, max_retries=2).text
    meta = parse_json_ld(BeautifulSoup(html_text, "html.parser"))
    if not meta:
        raise ValueError("radancy: no JobPosting structured data on page")
    meta["source"] = "radancy-page"
    meta["position_url"] = url
    return meta


# ---------------------------------------------------------------------------
# Discovery: enumerate every open posting on a company's career portal.
# Each discoverer takes a board-specific identifier and returns posting URLs.
# ---------------------------------------------------------------------------

def discover_eightfold(spec: str) -> list[str]:
    """Enumerate every open posting on an Eightfold AI board.

    ``spec`` is ``tenant:domain`` (e.g. ``paypal:paypal.com``). Uses
    Eightfold's public pcsx search API (no auth). The server caps a page
    at 10 rows regardless of the requested ``num``, so ``start`` advances
    by the number of rows actually seen.
    """
    parts = spec.split(":", 1)
    if len(parts) != 2 or not all(p.strip() for p in parts):
        raise ValueError(
            "eightfold discover spec must be 'tenant:domain', e.g. "
            f"'eightfold:paypal:paypal.com' (got {spec!r})")
    tenant, domain = (p.strip() for p in parts)
    urls: list[str] = []
    start = 0
    while True:
        data = http_get(
            f"https://{tenant}.eightfold.ai/api/pcsx/search"
            f"?domain={quote(domain, safe='')}"
            f"&query=&location=&start={start}&sort_by=timestamp",
            max_retries=2).json()
        board = (data or {}).get("data") or {}
        positions = board.get("positions") or []
        for pos in positions:
            path = pos.get("positionUrl") or ""
            if path.startswith("http"):
                urls.append(path)
            elif path.startswith("/"):
                urls.append(f"https://{tenant}.eightfold.ai{path}")
        total = board.get("count") or 0
        start += len(positions)
        if not positions or start >= total:
            break
    return list(dict.fromkeys(urls))


def discover_lever(company: str) -> list[str]:
    data = http_get(f"https://api.lever.co/v0/postings/{company}?mode=json",
                    max_retries=2).json()
    if not isinstance(data, list):
        return []
    return [p["hostedUrl"] for p in data if p.get("hostedUrl")]


def discover_ashby(company: str) -> list[str]:
    data = http_get("https://api.ashbyhq.com/posting-api/job-board/"
                    f"{company}?includeCompensation=true",
                    max_retries=2).json()
    return [j.get("jobUrl") for j in data.get("jobs", [])
            if j.get("jobUrl")]


def discover_greenhouse(board_token: str) -> list[str]:
    data = http_get("https://boards-api.greenhouse.io/v1/boards/"
                    f"{board_token}/jobs", max_retries=2).json()
    return [j.get("absolute_url") for j in data.get("jobs", [])
            if j.get("absolute_url")]


def discover_smartrecruiters(company: str) -> list[str]:
    data = http_get("https://api.smartrecruiters.com/v1/companies/"
                    f"{company}/postings?limit=100", max_retries=2).json()
    out = []
    for post in data.get("content", []):
        ref = post.get("ref") or ""
        if ref.startswith("https://jobs.smartrecruiters.com/"):
            out.append(ref)
    return out


def discover_workday(spec: str) -> list[str]:
    """spec: tenant:dc:site  (e.g. covestro:wd3:cov_external)."""
    tenant, dc, site = spec.split(":")
    base = f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"
    urls: list[str] = []
    offset = 0
    while True:  # Workday search is a POST with a JSON body
        polite_wait(base, base=1.5)
        resp = requests.post(
            base + "/jobs",
            json={"searchText": "", "appliedFacets": {},
                  "limit": 100, "offset": offset},
            headers={"User-Agent": get_ua()},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        postings = data.get("jobPostings", [])
        if not postings:
            break
        for posting in postings:
            path = (posting.get("externalPath") or "").lstrip("/")
            if path:
                urls.append(f"https://{tenant}.{dc}.myworkdayjobs.com/"
                            f"{site}/job/{path}")
        offset += len(postings)
        if offset >= data.get("total", 0):
            break
    return urls


def discover_teamtailor(host: str) -> list[str]:
    feed = http_get(f"https://{host}/jobs.json?per_page=100",
                    max_retries=2).json()
    items = feed.get("items", []) if isinstance(feed, dict) else []
    return [i["url"] for i in items if i.get("url")]


def discover_personio(company: str) -> list[str]:
    xml_text = http_get(f"https://{company}.jobs.personio.de/xml",
                        max_retries=2).text
    root = ET.fromstring(xml_text)
    return [f"https://{company}.jobs.personio.de/job/"
            f"{(p.findtext('id') or '').strip()}"
            for p in root.iter("position") if p.findtext("id")]


def discover_recruitee(company: str) -> list[str]:
    data = http_get(f"https://{company}.recruitee.com/api/offers/",
                    max_retries=2).json()
    offers = data.get("offers", []) if isinstance(data, dict) else []
    return [o.get("careers_url") or o.get("url") for o in offers
            if o.get("careers_url") or o.get("url")]


def discover_workable(company: str) -> list[str]:
    data = http_get("https://apply.workable.com/api/v1/widget/accounts/"
                    f"{company}?details=true", max_retries=2).json()
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    return [f"https://apply.workable.com/{company}/j/{j['shortcode']}/"
            for j in jobs if j.get("shortcode")]


def discover_breezy(tenant: str) -> list[str]:
    feed = http_get(f"https://{tenant}.breezy.hr/json",
                    max_retries=2).json()
    return [i["url"] for i in feed
            if isinstance(feed, list) and i.get("url")]


def discover_pinpoint(slug: str) -> list[str]:
    data = http_get(f"https://{slug}.pinpointhq.com/postings.json",
                    max_retries=2).json()
    if isinstance(data, list):
        items = data
    else:
        items = data.get("postings", []) or data.get("data", [])
    return [i.get("url") or i.get("absolute_url") for i in items
            if i.get("url") or i.get("absolute_url")]


def discover_rippling(slug: str) -> list[str]:
    data = http_get("https://api.rippling.com/platform/api/ats/v1/board/"
                    f"{slug}/jobs", max_retries=2).json()
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    return [f"https://ats.rippling.com/{slug}/jobs/{j['id']}"
            for j in jobs if j.get("id")]


def discover_join(slug: str) -> list[str]:
    """spec: join.com company slug (join.com/companies/<slug>).

    Two-step flow: the numeric company id is scraped from the company
    page, then the public paginated jobs API (pageSize 4/5 only) is
    walked to ``pagination.pageCount``. Canonical posting URLs are
    https://join.com/companies/<slug>/<id>-<title-slug>.
    """
    page = http_get(f"https://join.com/companies/{slug}",
                    max_retries=2).text
    match = re.search(r'"company":\{"id":(\d+)', page)
    if not match:
        return []
    company_id = match.group(1)
    urls: list[str] = []
    seen: set[str] = set()
    page_num = 1
    while True:
        polite_wait("https://join.com/api/public/companies/" + company_id,
                    base=0.5)
        data = http_get(
            f"https://join.com/api/public/companies/{company_id}/jobs"
            f"?page={page_num}&pageSize=5",
            max_retries=2).json()
        if not isinstance(data, dict):
            break
        for item in data.get("items", []):
            param = item.get("idParam")
            if param and param not in seen:
                seen.add(param)
                urls.append(f"https://join.com/companies/{slug}/{param}")
        page_count = (data.get("pagination") or {}).get("pageCount",
                                                         page_num)
        if page_num >= page_count:
            break
        page_num += 1
    return urls


# Cap discovery walks so a very large Radancy board cannot keep the run
# paging for hours.
RADANCY_MAX_PAGES = 100


def discover_radancy(spec: str) -> list[str]:
    """Enumerate every open posting on a Radancy (TalentBrew) career site.

    ``spec`` is ``host[:lang]`` (e.g. ``careers.munichre.com`` or
    ``careers.munichre.com:de``; language defaults to ``en``). Reads the
    server-rendered ``/{lang}/search-jobs?p=N`` result pages (no auth),
    collecting the canonical job URLs. Stops at the first page with no
    new job links; walks at most ``RADANCY_MAX_PAGES`` pages.
    """
    parts = spec.split(":")
    if len(parts) > 2 or not parts[0].strip():
        raise ValueError(
            "radancy discover spec must be 'host[:lang]', e.g. "
            f"'radancy:careers.munichre.com:en' (got {spec!r})")
    host = parts[0].strip().lower()
    lang = parts[1].strip().lower() if len(parts) == 2 else "en"
    if not re.fullmatch(r"[a-z]{2}", lang):
        raise ValueError(
            f"radancy discover language must be a 2-letter code "
            f"(got {lang!r})")
    urls: list[str] = []
    seen: set[str] = set()
    page = 1
    while page <= RADANCY_MAX_PAGES:
        polite_wait(f"https://{host}/{lang}/search-jobs", base=0.5)
        page_html = http_get(
            f"https://{host}/{lang}/search-jobs?p={page}",
            max_retries=2).text
        soup = BeautifulSoup(page_html, "html.parser")
        new = 0
        for anchor in soup.select("a.search-results-list__job-link[href]"):
            href = anchor["href"]
            if href.startswith("/"):
                href = f"https://{host}{href}"
            if href not in seen:
                seen.add(href)
                urls.append(href)
                new += 1
        if new == 0:
            break
        page += 1
    return urls


DISCOVERERS: dict[str, object] = {
    "lever": discover_lever,
    "ashby": discover_ashby,
    "greenhouse": discover_greenhouse,
    "smartrecruiters": discover_smartrecruiters,
    "workday": discover_workday,
    "teamtailor": discover_teamtailor,
    "personio": discover_personio,
    "recruitee": discover_recruitee,
    "workable": discover_workable,
    "breezy": discover_breezy,
    "pinpoint": discover_pinpoint,
    "rippling": discover_rippling,
    "eightfold": discover_eightfold,
    "join": discover_join,
    "radancy": discover_radancy,
}


BOARD_FETCHERS: list[Fetcher] = [
    fetch_lever,
    fetch_ashby,
    fetch_greenhouse,
    fetch_smartrecruiters,
    fetch_workday,
    fetch_teamtailor,
    fetch_personio,
    fetch_recruitee,
    fetch_workable,
    fetch_workable_view,
    fetch_breezy,
    fetch_pinpoint,
    fetch_eightfold,
    fetch_join,
    fetch_radancy,
    fetch_linkedin,
]


# Host fragments used to attribute a posting URL to its board/source in
# run diagnostics. Order matters only for readability; matches are
# substring checks against the lowercased host.
BOARD_HOSTS: tuple[tuple[str, str], ...] = (
    ("lever.co", "lever"),
    ("ashbyhq.com", "ashby"),
    ("greenhouse.io", "greenhouse"),
    ("smartrecruiters.com", "smartrecruiters"),
    ("myworkdayjobs.com", "workday"),
    ("teamtailor.com", "teamtailor"),
    ("personio.de", "personio"),
    ("recruitee.com", "recruitee"),
    ("workable.com", "workable"),
    ("breezy.hr", "breezy"),
    ("pinpointhq.com", "pinpoint"),
    ("rippling.com", "rippling"),
    ("eightfold.ai", "eightfold"),
    ("join.com", "join"),
    ("themuse.com", "themuse"),
    ("news.ycombinator.com", "hn_whoishiring"),
    ("linkedin.com", "linkedin"),
    ("remoteok.com", "remoteok"),
    ("remotive.com", "remotive"),
    ("weworkremotely.com", "weworkremotely"),
    ("workingnomads.com", "workingnomads"),
)


def board_name_for_url(url: str) -> str:
    """Classify a posting URL into its board/source name for diagnostics.

    Returns a short board key (``"lever"``, ``"ashby"``, ...) or
    ``"generic"`` for URLs that match no known board.
    """
    host = urlsplit(url).netloc.lower()
    for fragment, name in BOARD_HOSTS:
        if fragment in host:
            return name
    # Radancy (TalentBrew) boards live on tenant-branded domains, so the
    # host gives nothing away; the /{lang}/job/.../<category>/<id> path
    # shape is distinctive enough to attribute on its own.
    if _RADANCY_JOB_URL.match(url):
        return "radancy"
    return "generic"
