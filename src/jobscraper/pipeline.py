"""End-to-end pipeline: URL -> Posting.

Tries board APIs first, then falls back through the render chain
(requests -> Playwright) with a content-quality gate that understands
SPA shells via their embedded job JSON. Every posting gets a liveness
verdict and, optionally, a match score.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from jobscraper.boards import BOARD_FETCHERS
from jobscraper.extract import (
    MIN_CONTENT_CHARS,
    check_liveness,
    detect_signals,
    extract_benefits,
    extract_requirements,
    extract_salary,
    find_experience,
    find_skills,
    normalize_salary,
    parse_description_html,
    parse_embedded_job_json,
    parse_json_ld,
    soup_text,
    split_sections,
)
from jobscraper.http import configure_robots, robots_from_env
from jobscraper.models import Posting, Section
from jobscraper.rendering import fetch_playwright, fetch_requests
from jobscraper.scoring import posting_age_days, score_posting
from jobscraper.seniority import LEVELS as SENIORITY_LEVELS
from jobscraper.seniority import infer_seniority
from jobscraper.urls import canonicalize_url, input_dedupe


def check_tracker(post: Posting, tracker_path: str | None) -> str | None:
    """Return 'applied' if the posting URL already appears in a tracker file.

    Both the posting URL and every URL mentioned in the tracker file are
    canonicalized before comparing, so a tracker entry saved with or
    without tracking parameters still matches. Lines that do not contain
    a plain URL keep the old substring-match behavior.
    """
    if not tracker_path:
        return None
    try:
        with open(tracker_path, errors="ignore") as fh:
            text = fh.read().lower()
    except OSError:
        return None
    url = canonicalize_url((post.url or "").lower())
    if not url:
        return None
    mentioned = {canonicalize_url(u)
                 for u in re.findall(r"https?://[^\s<>()\"']+", text)}
    if url in mentioned or url in text:
        return "applied"
    return None


def dedupe_key(post: Posting) -> tuple[str, str]:
    """Normalize (company, title) so the same job on two boards dedupes."""

    def norm(text: str | None) -> str:
        text = re.sub(r"[^a-z0-9 ]", " ", (text or "").lower())
        return re.sub(r"\s+", " ", text).strip()

    title = norm(post.title)
    title = re.sub(r"\b(senior|sr|junior|jr|lead|staff|principal|i{1,3}|iv)\b",
                   "", title)
    return norm(post.company), re.sub(r"\s+", " ", title).strip()


def dedupe_results(posts: list[Posting]) -> list[Posting]:
    """Drop cross-board duplicates, keeping the highest-scored copy."""
    best: dict[tuple[str, str], Posting] = {}
    skipped: set[int] = set()
    for post in posts:
        if post.error:
            continue
        key = dedupe_key(post)
        if not key[0] or not key[1]:
            skipped.add(id(post))  # nothing to match on; always keep
            continue
        score = post.match.total if post.match else -1
        if key not in best or score > (best[key].match.total
                                       if best[key].match else -1):
            best[key] = post
    kept = {id(p) for p in best.values()} | skipped
    return [p for p in posts
            if p.error or id(p) in kept]


def run_pipeline(urls: list[str], profile: dict | None = None,
                 tracker_path: str | None = None,
                 no_score: bool = False, use_cache: bool = True,
                 workers: int = 4, no_dedupe: bool = False,
                 location_filter: str | None = None,
                 keyword_filter: str | None = None,
                 exclude_companies: str | None = None,
                 exclude_keywords: str | None = None,
                 min_score: int | None = None,
                 seniority: str | None = None,
                 watch_path: str | None = None,
                 respect_robots: bool = False,
                 progress_cb=None) -> tuple[list[Posting], int]:
    """Scrape every URL and return (postings, new_count, closed).

    ``progress_cb(done, total)`` is called as each URL finishes, so web
    UIs and CLIs can show progress. ``new_count`` is nonzero only in
    watch mode (postings never seen before); ``closed`` lists postings
    seen before that disappeared this run. ``respect_robots`` turns on
    the opt-in robots.txt check (also honored via the
    ``JOBSCRAPER_RESPECT_ROBOTS`` env var); disallowed URLs end with an
    explicit error on the posting, never a silent skip.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    configure_robots(respect_robots or robots_from_env())

    # Canonicalize up front: the same posting shared with different
    # tracking parameters (or host case / trailing slash) is fetched once
    # and reported under one clean URL.
    urls = [canonicalize_url(u) for u in input_dedupe(urls)]

    def work(url: str):
        try:
            return process_url(url, use_cache=use_cache,
                               profile=profile, tracker_path=tracker_path,
                               no_score=no_score)
        except Exception as exc:  # never let one URL kill the run
            return Posting(url=url, error=str(exc)[:300])

    results: list[Posting] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(work, url): url for url in urls}
        done = 0
        for future in as_completed(futures):
            done += 1
            if progress_cb:
                progress_cb(done, len(urls))
            results.append(future.result())
    order = {url: i for i, url in enumerate(urls)}
    results.sort(key=lambda post: order.get(post.url, 0))

    if not no_dedupe:
        results = dedupe_results(results)
    results = apply_filters(results, location_filter, keyword_filter,
                            exclude_companies, exclude_keywords,
                            seniority_filter=seniority)
    if min_score is not None:
        results = [p for p in results
                   if p.error or (p.match and p.match.total >= min_score)]
    new_count, closed = 0, []
    if watch_path:
        results, new_count, closed = apply_watch(results, watch_path)
    return results, new_count, closed


def _split_csv(value: str | None) -> list[str]:
    return [v.strip().lower() for v in (value or "").split(",") if v.strip()]


def apply_filters(posts: list[Posting],
                  location_filter: str | None = None,
                  keyword_filter: str | None = None,
                  exclude_companies: str | None = None,
                  exclude_keywords: str | None = None,
                  seniority_filter: str | None = None) -> list[Posting]:
    """Keep/drop postings by location, title keywords and exclusions.

    ``keyword_filter`` keeps titles containing any comma-separated keyword;
    ``exclude_companies``/``exclude_keywords`` drop matching companies/titles
    (all case-insensitive); ``seniority_filter`` keeps only postings whose
    inferred seniority level is in the comma-separated list (postings with
    an unknown level are dropped unless ``unknown`` is listed). Errored
    postings are always kept so failures stay visible.
    """
    keywords = _split_csv(keyword_filter)
    loc_filter = (location_filter or "").lower()
    ex_companies = _split_csv(exclude_companies)
    ex_keywords = _split_csv(exclude_keywords)
    seniority_levels = set(_split_csv(seniority_filter))
    unknown_levels = {lvl for lvl in seniority_levels
                      if lvl not in SENIORITY_LEVELS}
    if unknown_levels:
        raise ValueError(
            f"unknown seniority level(s): {sorted(unknown_levels)} "
            f"(valid: {', '.join(SENIORITY_LEVELS)})")

    def keep(post: Posting) -> bool:
        if post.error:
            return True
        if seniority_levels and post.seniority not in seniority_levels:
            return False
        if loc_filter and loc_filter not in (post.location or "").lower():
            return False
        title = (post.title or "").lower()
        if keywords and not any(k in title for k in keywords):
            return False
        company = (post.company or "").lower()
        if any(e in company for e in ex_companies):
            return False
        if any(e in title for e in ex_keywords):
            return False
        return True

    return [p for p in posts if keep(p)]


def apply_watch(posts: list[Posting],
                state_path: str) -> tuple[list[Posting], int, list[dict]]:
    """Flag postings never seen before and detect closed ones.

    The state file maps dedupe keys to ``{"url", "title", "first_seen"}``
    (plus ``closed_since`` once closed); it is created on first use.
    Postings seen in an earlier run but absent now are recorded as
    closed with today's date, except postings that errored this run
    (a transient fetch failure is not a closed posting). A closed
    posting that reappears is reopened by dropping ``closed_since``.

    Returns (posts, new_count, closed) where ``closed`` lists the
    postings newly closed this run as dicts with ``url``, ``title``,
    ``first_seen`` and ``closed_since``.
    """
    import json
    from datetime import date
    try:
        with open(state_path, encoding="utf-8") as fh:
            seen = json.load(fh)
    except (OSError, ValueError):
        seen = {}
    today = date.today().isoformat()
    new_count = 0
    current: set[str] = set()
    errored: set[str] = set()
    for post in posts:
        key = "|".join(dedupe_key(post))
        if post.error:
            errored.add(key)
            continue
        current.add(key)
        if key not in seen:
            post.is_new = True
            new_count += 1
            seen[key] = {"url": post.url, "title": post.title,
                         "first_seen": today}
        elif seen[key].get("closed_since"):
            # Reopened: the posting is back on the board.
            del seen[key]["closed_since"]
    closed: list[dict] = []
    for key, entry in seen.items():
        if (key not in current and key not in errored
                and not entry.get("closed_since")):
            entry["closed_since"] = today
            closed.append({"url": entry.get("url"),
                           "title": entry.get("title"),
                           "first_seen": entry.get("first_seen"),
                           "closed_since": today})
    try:
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(seen, fh, indent=2, ensure_ascii=False)
    except OSError:
        pass
    return posts, new_count, closed


def process_url(url: str, use_cache: bool = True,
                profile: dict | None = None,
                tracker_path: str | None = None,
                no_score: bool = False) -> Posting:
    """Scrape one posting URL into a fully-populated :class:`Posting`."""
    post = Posting(url=url,
                   fetched_at=datetime.now(timezone.utc).isoformat())

    # 1. Board API fast paths.
    meta: dict | None = None
    for fetcher in BOARD_FETCHERS:
        try:
            meta = fetcher(url)
        except Exception as exc:
            msg = str(exc)
            post.board_errors.append(f"{fetcher.__name__}: {msg[:120]}")
            if "404" in msg:
                post.is_live = False
                post.live_reason = \
                    "board API returned 404: posting closed/removed"
                return post
        if meta:
            break

    if meta and meta.get("description_html"):
        soup = parse_description_html(meta["description_html"])
        full_text = soup_text(soup)
        sections = split_sections(soup)
        post.is_live, post.live_reason = check_liveness(full_text)
    else:
        # 2. Render chain with a content-quality gate.
        html_text, page_title, method = "", "", None
        embedded: dict | None = None
        for fetch_fn in (fetch_requests, fetch_playwright):
            # Two attempts per method: the egress path intermittently
            # returns truncated bodies; a quick retry usually heals it.
            for attempt in range(2):
                try:
                    page_title, html_text = fetch_fn(url,
                                                     use_cache=use_cache)
                    method = fetch_fn.__name__.replace("fetch_", "")
                    soup_probe = BeautifulSoup(html_text, "lxml")
                    embedded = parse_embedded_job_json(soup_probe)
                    vis = BeautifulSoup(html_text, "lxml")
                    for tag in vis(["script", "style"]):
                        tag.decompose()
                    nchars = len(soup_text(vis.body or vis))
                    if nchars >= MIN_CONTENT_CHARS or (
                            embedded and embedded.get("description_html")):
                        break
                    post.fetch_notes.append(
                        f"{method} attempt {attempt + 1}: only {nchars} "
                        "chars, no embedded job data")
                    html_text, method, embedded = "", None, None
                    time.sleep(3)
                except Exception as exc:
                    post.fetch_notes.append(
                        f"{fetch_fn.__name__} attempt {attempt + 1}: "
                        f"{str(exc)[:120]}")
                    html_text, method, embedded = "", None, None
                    time.sleep(3)
            else:
                post.fetch_notes.append(
                    f"{fetch_fn.__name__}: exhausted, trying next method")
                time.sleep(2)
                continue
            break
        if not html_text:
            post.error = ("all fetch methods failed or returned shells: "
                          + " | ".join(post.fetch_notes))[:300]
            post.is_live = None
            post.live_reason = "could not fetch a readable page"
            return post

        post.fetch_method = method
        soup = BeautifulSoup(html_text, "lxml")
        if page_title is None:  # served from cache; re-derive
            title_tag = soup.title
            page_title = (title_tag.string.strip()
                          if title_tag and title_tag.string else "")
        if embedded and embedded.get("description_html"):
            post.is_live, post.live_reason = \
                True, "posting data embedded in page"
        else:
            post.is_live, post.live_reason = \
                check_liveness(soup_text(soup.body or soup))
        meta = parse_json_ld(soup)
        if not meta.get("description_html") and embedded and embedded.get(
                "description_html"):
            meta = embedded
        desc_html = meta.get("description_html", "")
        if desc_html:
            dsoup = parse_description_html(desc_html)
            full_text = soup_text(dsoup)
            sections = split_sections(dsoup)
        else:  # strip chrome, use the whole page
            for tag in soup(["script", "style", "nav", "header", "footer",
                             "noscript"]):
                tag.decompose()
            full_text = soup_text(soup.body or soup)
            sections = split_sections(soup)
        if len(full_text) < MIN_CONTENT_CHARS:
            post.low_content_warning = (
                f"only {len(full_text)} chars extracted; page may be "
                "JS-gated or blocked")

    req, nice, resp, maybe = extract_requirements(sections)
    post.title = meta.get("title") or page_title or None
    post.company = meta.get("company")
    post.location = meta.get("location")
    post.employment_type = meta.get("employment_type")
    post.department = meta.get("department")
    post.posted = meta.get("posted")
    post.age_days = posting_age_days(post.posted)
    post.via = meta.get("source") or post.fetch_method
    custom_skills = tuple(profile.get("custom_skills", [])
                          ) if profile else ()
    post.skills_found = find_skills(full_text, custom_skills)
    post.experience_years_mentioned = find_experience(full_text)
    seniority = infer_seniority(post.title, full_text,
                               post.experience_years_mentioned)
    post.seniority = seniority.level
    post.seniority_evidence = seniority.evidence
    post.salary_hits = extract_salary(full_text)
    post.salary_normalized = normalize_salary(post.salary_hits)
    for extra in meta.get("salary_hits_extra") or []:
        if extra and extra not in post.salary_hits:
            post.salary_hits.append(extra)
    post.signals = detect_signals(full_text)
    post.requirements = req
    post.nice_to_have = nice
    post.responsibilities = resp
    post.benefits = extract_benefits(sections)
    post.other_possibly_relevant = maybe[:3]
    post.sections = [Section(s.heading, s.text[:2000]) for s in sections]
    post.full_text_chars = len(full_text)
    post.tracker_status = check_tracker(post, tracker_path)
    if profile is not None and not no_score and post.is_live is not False:
        post.match = score_posting(post, profile)
    return post
