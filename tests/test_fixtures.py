"""Fixture-based regression tests: frozen API/page snapshots.

These tests load realistic (but fully fictional) payloads from
``tests/fixtures/`` and drive them through the real fetchers, parsers
and pipeline stages — with no network access. If a board API changes
shape, a page template changes, or a regression creeps into the
extraction code, these tests pin the expected behaviour.

The six fixture scenarios the roadmap asks for are covered:
live posting (board API), closed ATS posting, SPA shell with embedded
JSON, block page, cross-board duplicates and malformed responses.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

import jobscraper.boards as boards
from jobscraper.extract import (
    check_liveness,
    extract_balanced_json,
    extract_requirements,
    looks_blocked,
    parse_description_html,
    parse_embedded_job_json,
    parse_json_ld,
    soup_text,
    split_sections,
)
from jobscraper.models import MatchResult, Posting
from jobscraper.pipeline import dedupe_results, process_url

FIX = Path(__file__).parent / "fixtures"


def read(name: str) -> str:
    """Read a fixture file as text."""
    return (FIX / name).read_text(encoding="utf-8")


def load_json(name: str):
    """Read a fixture file as parsed JSON."""
    return json.loads(read(name))


class _JsonResp:
    """Minimal stand-in for requests.Response with a JSON payload."""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _patch_json(monkeypatch, payload):
    """Serve one JSON payload (or raw text) from every http_get call."""
    monkeypatch.setattr(boards, "http_get",
                        lambda url, **kw: _JsonResp(payload))


# ---------------------------------------------------------------------------
# Board API fixtures
# ---------------------------------------------------------------------------

def test_lever_fixture_normalizes(monkeypatch):
    _patch_json(monkeypatch, load_json("lever-posting.json"))
    meta = boards.fetch_lever("https://jobs.lever.co/acme/a1b2c3d4")
    assert meta["title"] == "Senior Security Engineer (Detection)"
    assert meta["company"] == "Northwind Cloud Security"
    assert meta["location"] == "Remote - India"
    assert meta["employment_type"] == "Full-time"
    assert meta["department"] == "Security"
    assert meta["source"] == "lever-api"
    assert "Sigma" in meta["description_html"]


def test_ashby_fixture_normalizes(monkeypatch):
    _patch_json(monkeypatch, load_json("ashby-posting.json"))
    meta = boards.fetch_ashby(
        "https://jobs.ashbyhq.com/contoso/"
        "f47ac10b-58cc-4372-a567-0e02b2c3d479")
    assert meta["title"] == "SOC Analyst L2"
    assert meta["company"] == "Contoso Defense Labs"
    assert meta["location"] == "Hyderabad, India"
    assert meta["source"] == "ashby-api"
    assert meta["compensation"] == "$95k-$120k"


def test_greenhouse_fixture_normalizes(monkeypatch):
    _patch_json(monkeypatch, load_json("greenhouse-posting.json"))
    meta = boards.fetch_greenhouse(
        "https://boards.greenhouse.io/initech/jobs/4321098")
    assert meta["title"] == "Security Automation Engineer"
    assert meta["company"] == "Initech Cybernetics"
    assert meta["location"] == "Berlin, Germany"
    assert meta["employment_type"] == "Full-Time"
    assert meta["department"] == "Engineering"
    assert meta["source"] == "greenhouse-api"


def test_board_fetchers_ignore_foreign_urls():
    """Fetchers must return None (never network) for other sites' URLs."""
    url = "https://example.com/careers/engineer"
    assert boards.fetch_lever(url) is None
    assert boards.fetch_ashby(url) is None
    assert boards.fetch_greenhouse(url) is None
    assert boards.fetch_workday(url) is None
    assert boards.fetch_teamtailor(
        "https://example.com/careers/engineer") is None


def test_malformed_api_response_raises(monkeypatch):
    """A truncated API body raises ValueError; the pipeline's board loop
    catches it into board_errors instead of crashing the run."""
    class _BadResp:
        def json(self):
            return json.loads(read("malformed-api.json"))

    monkeypatch.setattr(boards, "http_get",
                        lambda url, **kw: _BadResp())
    with pytest.raises(ValueError):
        boards.fetch_lever("https://jobs.lever.co/acme/a1b2c3d4")


# ---------------------------------------------------------------------------
# Full pipeline through a frozen board-API fixture (no network)
# ---------------------------------------------------------------------------

def test_process_url_lever_fixture_end_to_end(monkeypatch):
    _patch_json(monkeypatch, load_json("lever-posting.json"))
    post = process_url("https://jobs.lever.co/acme/a1b2c3d4",
                       profile=None, no_score=True, use_cache=False)

    assert post.error is None
    assert post.title == "Senior Security Engineer (Detection)"
    assert post.company == "Northwind Cloud Security"
    assert post.location == "Remote - India"
    assert post.via == "lever-api"
    assert post.is_live is True

    # Extraction: skills, experience, salary, signals.
    assert "Splunk" in post.skills_found
    assert "SIEM" in post.skills_found
    assert "Python" in post.skills_found
    assert 5 in post.experience_years_mentioned
    assert any("₹" in hit for hit in post.salary_hits)
    inr = [s for s in post.salary_normalized if s["currency"] == "INR"]
    annuals = sorted(s["min_annual"] for s in inr)
    assert annuals == [1800000, 2200000], annuals
    assert post.signals["sponsorship_mentioned"] is True
    assert "remote" in post.signals["work_mode"]

    # Section bucketing: requirements / nice-to-have / responsibilities /
    # benefits land in the right buckets.
    req_titles = [s.heading for s in post.requirements]
    nice_titles = [s.heading for s in post.nice_to_have]
    resp_titles = [s.heading for s in post.responsibilities]
    ben_titles = [s.heading for s in post.benefits]
    assert any("Requirement" in t for t in req_titles)
    assert any("Nice to have" in t for t in nice_titles)
    assert any("About the role" in t for t in resp_titles)
    assert any("Benefit" in t for t in ben_titles)


# ---------------------------------------------------------------------------
# Page templates: SPA shells, JSON-LD, closed and blocked pages
# ---------------------------------------------------------------------------

def test_spa_shell_embedded_appdata():
    soup = BeautifulSoup(read("spa-shell.html"), "lxml")
    meta = parse_embedded_job_json(soup)
    assert meta is not None
    assert meta["title"] == "Threat Intelligence Analyst"
    assert meta["company"] == "Contoso Defense Labs"
    assert meta["location"] == "Remote, US"
    assert meta["source"] == "embedded-json"
    assert "OSINT" in meta["description_html"]


def test_nextjs_shell_embedded_data():
    soup = BeautifulSoup(read("nextjs-posting.html"), "lxml")
    meta = parse_embedded_job_json(soup)
    assert meta is not None
    assert meta["title"] == "Detection Engineer"
    assert meta["source"] == "embedded-next-data"
    assert "Sigma" in meta["description_html"]


def test_json_ld_page_parse():
    soup = BeautifulSoup(read("jsonld-posting.html"), "lxml")
    meta = parse_json_ld(soup)
    assert meta["title"] == "Cloud Security Engineer"
    assert meta["company"] == "Umbrella Security Corp"
    assert meta["location"] == "Singapore"
    assert meta["posted"] == "2026-09-20"
    # The visible page also extracts cleanly through the normal path.
    soup_desc = parse_description_html(meta["description_html"])
    sections = split_sections(soup_desc)
    assert sections, "expected at least one section from the page"


def test_closed_posting_fixture_is_closed():
    soup = BeautifulSoup(read("closed-posting.html"), "lxml")
    is_live, reason = check_liveness(soup_text(soup))
    assert is_live is False
    assert "closed" in reason or "expired" in reason or "removed" in reason


def test_block_page_fixture_is_blocked():
    html_text = read("block-page.html")
    assert looks_blocked(html_text) is True
    soup = BeautifulSoup(html_text, "lxml")
    is_live, reason = check_liveness(soup_text(soup))
    assert is_live is None
    assert "bot challenge" in reason or "block" in reason


def test_extract_balanced_json_nested():
    txt = 'window.__appData = {"a": {"b": [1, {"c": "}"}]}, "d": 2}; trailing'
    start = txt.index("{")
    blob = extract_balanced_json(txt, start)
    assert blob is not None
    assert json.loads(blob)["a"]["b"][1]["c"] == "}"


def test_extract_balanced_json_unbalanced():
    assert extract_balanced_json('{"a": {"b": 1}', 0) is None


# ---------------------------------------------------------------------------
# Cross-board duplicates from frozen sources
# ---------------------------------------------------------------------------

def _dup_post(source: str, score: int) -> Posting:
    post = Posting(url=f"https://{source}/job/123")
    post.company = "Northwind Cloud Security"
    post.title = "Senior Security Engineer (Detection)"
    post.via = source
    post.match = MatchResult(total=score)
    return post


def test_duplicate_across_boards_keeps_best_score():
    lever_copy = _dup_post("lever-api", 72)
    linkedin_copy = _dup_post("linkedin", 91)
    kept = dedupe_results([lever_copy, linkedin_copy])
    assert len(kept) == 1
    assert kept[0].via == "linkedin"
    assert kept[0].match.total == 91


# ---------------------------------------------------------------------------
# Section bucketing on a frozen description blob
# ---------------------------------------------------------------------------

def test_section_bucketing_on_fixture_description():
    data = load_json("lever-posting.json")
    soup = parse_description_html(data["description"])
    req, nice, resp, _other = extract_requirements(split_sections(soup))
    assert any("Requirement" in s.heading for s in req)
    assert any("Nice to have" in s.heading for s in nice)
    assert any("About the role" in s.heading for s in resp)
