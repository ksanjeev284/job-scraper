"""LinkedIn guest-API parsing (fixture-based, no network)."""

from jobscraper.sources.linkedin import (
    parse_job_detail,
    parse_search_cards,
)

SEARCH_HTML = """
<ul>
<li><div class="base-card job-search-card" data-entity-urn="urn:li:jobPosting:111">
<a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/111/"></a>
<h3 class="base-search-card__title">Splunk Engineer</h3>
<h4 class="base-search-card__subtitle">Acme Corp</h4>
<span class="job-search-card__location">Hyderabad, India</span>
<time datetime="2026-09-30">4 days ago</time>
</div></li>
<li><div class="base-card job-search-card" data-entity-urn="urn:li:jobPosting:111">
<a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/111/"></a>
<h3 class="base-search-card__title">Splunk Engineer</h3>
<h4 class="base-search-card__subtitle">Acme Corp</h4>
<span class="job-search-card__location">Hyderabad, India</span>
</div></li>
<li><div class="base-card job-search-card" data-entity-urn="urn:li:jobPosting:222">
<a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/222/"></a>
<h3 class="base-search-card__title">SOC Analyst</h3>
<h4 class="base-search-card__subtitle">Globex</h4>
<span class="job-search-card__location">Bengaluru, India</span>
</div></li>
</ul>
"""

DETAIL_HTML = """
<div>
<h2 class="top-card-layout__title">Splunk Engineer</h2>
<a class="topcard__org-name-link">Acme Corp</a>
<span class="topcard__flavor--bullet">Full-time</span>
<span class="topcard__flavor--bullet">Hyderabad, India</span>
<span class="posted-time-ago__text">2 weeks ago</span>
<div class="description__text"><p>We need <b>Splunk</b> expertise.</p></div>
</div>
"""


def test_parse_search_cards():
    jobs = parse_search_cards(SEARCH_HTML)
    assert len(jobs) == 2  # duplicate id deduped
    first = jobs[0]
    assert first["job_id"] == "111"
    assert first["title"] == "Splunk Engineer"
    assert first["company"] == "Acme Corp"
    assert first["location"] == "Hyderabad, India"
    assert first["url"] == "https://www.linkedin.com/jobs/view/111/"
    assert first["posted_text"] == "2026-09-30"


def test_parse_search_cards_empty():
    assert parse_search_cards("<html><body>no jobs</body></html>") == []


def test_parse_job_detail():
    meta = parse_job_detail(DETAIL_HTML)
    assert meta["title"] == "Splunk Engineer"
    assert meta["company"] == "Acme Corp"
    assert meta["location"] == "Hyderabad, India"
    assert meta["employment_type"] == "Full-time"
    assert "Splunk" in meta["description_html"]
    assert meta["source"] == "linkedin-guest"


def test_parse_job_detail_no_title():
    assert parse_job_detail("<div>authwall</div>") is None


def test_fetch_linkedin_url_match(monkeypatch):
    import jobscraper.sources.linkedin as li

    seen = {}

    def fake_fetch(job_id):
        seen["id"] = job_id
        return {"title": "X"}

    monkeypatch.setattr(li, "fetch_job", fake_fetch)
    assert li.fetch_linkedin("https://example.com/jobs/1") is None
    out = li.fetch_linkedin("https://www.linkedin.com/jobs/view/42424242/")
    assert out == {"title": "X"}
    assert seen["id"] == "42424242"
