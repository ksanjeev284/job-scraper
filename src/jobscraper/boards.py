"""Fetchers for ATS public job-board APIs.

Every fetcher takes a posting URL, returns a normalized dict with keys
``title, company, location, employment_type, department, description_html,
posted, source`` (plus optional extras), or ``None`` when the URL is not
for that board. All endpoints are public and need no authentication.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable

from jobscraper.http import http_get

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
]
