"""LinkedIn source: public guest job-search and job-detail APIs.

LinkedIn serves its logged-out job search through a guest endpoint that
needs no login, no cookies and no browser:

- search:  GET https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search
- detail:  GET https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}

Both return HTML fragments parsed here with BeautifulSoup. LinkedIn
rate-limits aggressively, so calls are throttled (2s base) and callers
should keep result counts modest.
"""

from __future__ import annotations

import re
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from jobscraper.extract import soup_text
from jobscraper.http import http_get, polite_wait

SEARCH_URL = ("https://www.linkedin.com/jobs-guest/jobs/api/"
              "seeMoreJobPostings/search")
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"

# f_WT values: 1=onsite, 2=remote, 3=hybrid. f_E: 1..6 intern..exec.
REMOTE_CODES = {"onsite": "1", "remote": "2", "hybrid": "3"}
JOB_TYPE_CODES = {"full-time": "F", "part-time": "P", "contract": "C",
                  "temporary": "T", "internship": "I"}


def search_jobs(keywords: str, location: str | None = None,
                geo_id: str | None = None, start: int = 0,
                remote: str | None = None,
                posted_within_days: int | None = None,
                job_type: str | None = None,
                experience: str | None = None) -> list[dict]:
    """Search LinkedIn jobs; returns job-card dicts (id, title, company,
    location, url, posted_text). Paginate with ``start`` (steps of ~10)."""
    params: dict[str, str] = {"keywords": keywords, "start": str(start)}
    if geo_id:
        params["geoId"] = geo_id
    elif location:
        params["location"] = location
    if remote and remote in REMOTE_CODES:
        params["f_WT"] = REMOTE_CODES[remote]
    if posted_within_days:
        params["f_TPR"] = f"r{posted_within_days * 86400}"
    if job_type and job_type in JOB_TYPE_CODES:
        params["f_JT"] = JOB_TYPE_CODES[job_type]
    if experience:
        params["f_E"] = experience
    polite_wait(SEARCH_URL, base=2.0)
    html_text = http_get(SEARCH_URL + "?" + urlencode(params)).text
    return parse_search_cards(html_text)


def parse_search_cards(html_text: str) -> list[dict]:
    """Parse job cards from a guest-search HTML fragment."""
    soup = BeautifulSoup(html_text, "lxml")
    jobs: list[dict] = []
    for card in soup.select("div.job-search-card, li div.base-card"):
        link = card.select_one("a.base-card__full-link")
        href = (link.get("href") or "") if link else ""
        match = re.search(r"/jobs/view/(\d+)", href)
        if not match:
            urn = card.get("data-entity-urn", "")
            match = re.search(r"jobPosting:(\d+)", urn)
        if not match:
            continue
        job_id = match.group(1)
        title = card.select_one(".base-search-card__title")
        company = card.select_one(".base-search-card__subtitle")
        loc = card.select_one(".job-search-card__location")
        time_el = card.select_one("time")
        jobs.append({
            "job_id": job_id,
            "title": soup_text(title) if title else None,
            "company": soup_text(company) if company else None,
            "location": soup_text(loc) if loc else None,
            "url": f"https://www.linkedin.com/jobs/view/{job_id}/",
            "posted_text": (time_el.get("datetime")
                            if time_el and time_el.get("datetime")
                            else soup_text(time_el) if time_el else None),
        })
    # de-dupe by id, keep order
    seen, unique = set(), []
    for job in jobs:
        if job["job_id"] not in seen:
            seen.add(job["job_id"])
            unique.append(job)
    return unique


def fetch_job(job_id: str) -> dict | None:
    """Fetch one posting's detail fragment; normalized dict or None."""
    polite_wait(DETAIL_URL, base=2.0)
    try:
        html_text = http_get(DETAIL_URL.format(job_id)).text
    except Exception:
        return None
    return parse_job_detail(html_text)


def parse_job_detail(html_text: str) -> dict | None:
    """Parse the guest job-detail HTML fragment."""
    soup = BeautifulSoup(html_text, "lxml")
    title_el = soup.select_one(".top-card-layout__title")
    if not title_el:
        return None
    company_el = soup.select_one(".topcard__org-name-link")
    desc_el = soup.select_one(".description__text")
    posted_el = soup.select_one(".posted-time-ago__text")
    flavors = [soup_text(el) for el in
               soup.select(".topcard__flavor--bullet")]
    location = flavors[1] if len(flavors) > 1 else None
    employment_type = flavors[0] if flavors else None
    return {
        "title": soup_text(title_el),
        "company": soup_text(company_el) if company_el else None,
        "location": location,
        "employment_type": employment_type,
        "department": None,
        "description_html": str(desc_el) if desc_el else "",
        "posted": soup_text(posted_el) if posted_el else None,
        "source": "linkedin-guest",
    }


def fetch_linkedin(url: str) -> dict | None:
    """Board-fetcher adapter for linkedin.com/jobs/view/{id} URLs."""
    match = re.search(r"linkedin\.com/jobs/view/(\d+)", url)
    if not match:
        return None
    return fetch_job(match.group(1))
