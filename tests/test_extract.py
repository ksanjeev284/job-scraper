"""Section, skill and liveness extraction."""

import json

from bs4 import BeautifulSoup

from jobscraper.extract import (
    check_liveness,
    detect_signals,
    extract_requirements,
    extract_salary,
    find_experience,
    find_skills,
    looks_blocked,
    parse_description_html,
    parse_embedded_job_json,
    parse_json_ld,
    split_sections,
)


def test_split_sections_basic():
    soup = BeautifulSoup(
        "<h2>Requirements</h2><p>3 years of Splunk</p>"
        "<h2>Nice to have</h2><p>Python</p>", "lxml")
    sections = split_sections(soup)
    assert sections[0].heading == "Requirements"
    assert "Splunk" in sections[0].text
    assert sections[1].heading == "Nice to have"


def test_extract_requirements_buckets():
    soup = BeautifulSoup(
        "<h2>Requirements</h2><p>Must have Splunk</p>"
        "<h2>What you'll do</h2><p>Hunt threats</p>"
        "<h2>Nice to have</h2><p>Go</p>", "lxml")
    req, nice, resp, _ = extract_requirements(split_sections(soup))
    assert any("Requirements" in s.heading for s in req)
    assert any("Nice to have" in s.heading for s in nice)
    assert any("What you'll do" in s.heading for s in resp)


def test_find_skills_case_insensitive():
    skills = find_skills("Expert in splunk, SPLUNK ES and mitre att&ck.")
    lowered = [s.lower() for s in skills]
    assert "splunk" in lowered
    assert "mitre att&ck" in lowered


def test_find_skills_no_substring_matches():
    # "soc" must not fire inside "social"
    assert "SOC" not in find_skills("social media marketing role")


def test_find_experience_ranges():
    # ranges record the low end of the band
    assert find_experience("3-5 years of experience in SOC") == [3]
    assert find_experience("8+ years experience required") == [8]
    assert find_experience("no experience needed") == []


def test_extract_salary_inr_lpa():
    hits = extract_salary("CTC 18 LPA, range 16.8 lakh per annum")
    assert any("LPA" in h for h in hits)
    assert any("lakh" in h.lower() for h in hits)


def test_extract_salary_eur_usd():
    assert extract_salary("€75,000 per year") != []
    assert extract_salary("$120k base") != []


def test_detect_signals():
    sig = detect_signals("Visa sponsorship available. Hybrid role. "
                         "German required for client meetings.")
    assert sig["sponsorship_mentioned"]
    assert sig["german_required"]
    assert "hybrid" in sig["work_mode"]


def test_looks_blocked_short_page():
    assert looks_blocked(
        "<html><body>Verify you are human: complete the captcha</body></html>")
    assert not looks_blocked(
        "<html><body>" + "<p>Real job description text.</p>" * 100
        + "</body></html>")


def test_check_liveness_closed():
    live, reason = check_liveness("This job posting has been closed.")
    assert live is False
    assert "closed" in reason


def test_check_liveness_open():
    live, _ = check_liveness(
        "We are hiring a Splunk engineer. " * 50)
    assert live is True


def test_parse_json_ld_jobposting():
    payload = {
        "@type": "JobPosting",
        "title": "SOC Analyst",
        "datePosted": "2026-09-20",
        "hiringOrganization": {"name": "Acme"},
        "jobLocation": {"address": {"addressLocality": "Hyderabad"}},
        "description": "<p>Hunt threats</p>",
    }
    soup = BeautifulSoup(
        '<script type="application/ld+json">'
        + json.dumps(payload) + "</script>", "lxml")
    meta = parse_json_ld(soup)
    assert meta["title"] == "SOC Analyst"
    assert meta["company"] == "Acme"
    assert meta["source"] == "json-ld"


def test_parse_embedded_next_data():
    doc = {"props": {"pageProps": {"job": {"title": "X",
                                          "descriptionHtml": "<p>hi</p>"}}}}
    soup = BeautifulSoup(
        '<script id="__NEXT_DATA__">' + json.dumps(doc)
        + "</script>", "lxml")
    meta = parse_embedded_job_json(soup)
    assert meta is not None
    assert meta["description_html"] == "<p>hi</p>"


def test_parse_description_html_double_escaped():
    soup = parse_description_html("&lt;p&gt;Hello&lt;/p&gt;")
    assert "Hello" in soup.get_text()


def test_extract_benefits():
    from jobscraper.extract import extract_benefits
    soup = BeautifulSoup(
        "<h2>Benefits</h2><p>Health insurance, 401k</p>"
        "<h2>Requirements</h2><p>Python</p>", "lxml")
    benefits = extract_benefits(split_sections(soup))
    assert len(benefits) == 1
    assert "Health insurance" in benefits[0].text


def test_extract_benefits_none():
    from jobscraper.extract import extract_benefits
    soup = BeautifulSoup("<h2>Requirements</h2><p>Python</p>", "lxml")
    assert extract_benefits(split_sections(soup)) == []
