"""Tests for the freehire.me cross-ATS search source.

Parsers are exercised against frozen API-shaped fixtures with no
network access; ``search_freehire`` pagination, facets and dedupe are
faked by stubbing the module's ``http_get``/``polite_wait``.
"""

from __future__ import annotations

from jobscraper.sources import freehire as fh


def _item(slug="devops-example-acme-mjbh6z4l",
          title="Senior DevOps Engineer",
          company="Acme Cloud",
          description=("<p>Run <strong>Kubernetes</strong> at scale.</p>"
                       "<ul><li>Terraform required</li></ul>"),
          posted_at="2026-10-02T16:54:16Z",
          source="ashby",
          work_mode="remote",
          skills=("kubernetes", "terraform")) -> dict:
    return {
        "public_slug": slug,
        "source": source,
        "external_id": "acme/123",
        "url": f"https://jobs.ashbyhq.com/acme/{slug}",
        "title": title,
        "company": company,
        "company_slug": "acme-cloud",
        "location": "Berlin, Germany",
        "posted_at": posted_at,
        "description": description,
        "skills": list(skills),
        "work_mode": work_mode,
        "countries": ["de"],
        "regions": ["eu"],
        "enrichment": {"employment_type": "full_time",
                       "experience_years_min": 5,
                       "category": "devops"},
    }


class _Resp:
    def __init__(self, payload: dict):
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _stub(monkeypatch, pages: list[dict]):
    calls: list[str] = []

    def fake_get(url: str):
        calls.append(url)
        return _Resp({"data": pages[min(len(calls) - 1,
                                        len(pages) - 1)]})

    monkeypatch.setattr(fh, "http_get", fake_get)
    monkeypatch.setattr(fh, "polite_wait", lambda *a, **k: None)
    return calls


def test_parse_freehire_job_card_shape():
    card = fh.parse_freehire_job(_item())
    assert card is not None
    assert card["job_id"] == "freehire:devops-example-acme-mjbh6z4l"
    assert card["title"] == "Senior DevOps Engineer"
    assert card["company"] == "Acme Cloud"
    assert card["location"] == "Berlin, Germany"
    assert card["url"] == ("https://freehire.me/jobs/"
                           "devops-example-acme-mjbh6z4l")
    assert card["posted_text"] == "2026-10-02"
    assert card["source"] == "freehire"
    assert card["ats"] == "ashby"
    assert card["career_url"].startswith("https://jobs.ashbyhq.com/")
    assert card["work_mode"] == "remote"
    assert card["tags"] == ["kubernetes", "terraform"]
    assert "Kubernetes" in card["description"]
    assert "<p>" not in card["description"]


def test_parse_freehire_job_missing_fields():
    assert fh.parse_freehire_job({}) is None
    assert fh.parse_freehire_job({"public_slug": "x"}) is None
    assert fh.parse_freehire_job({"title": "DevOps"}) is None
    assert fh.parse_freehire_job(None) is None
    # Minimal item still parses; text fields become None.
    card = fh.parse_freehire_job({"public_slug": "a-b", "title": "Ops"})
    assert card is not None
    assert card["company"] is None
    assert card["description"] == ""
    assert card["tags"] == []


def test_posted_date_normalizes_iso():
    assert fh._posted_date("2026-10-02T16:54:16Z") == "2026-10-02"
    assert fh._posted_date(None) is None
    assert fh._posted_date("") is None


def test_search_freehire_pages_with_offset(monkeypatch):
    monkeypatch.setattr(fh, "PAGE_SIZE", 2)
    page1 = [_item(slug="job-one-1"), _item(slug="job-one-2")]
    page2 = [_item(slug="job-two-1")]
    calls = _stub(monkeypatch, [page1, page2])
    cards = fh.search_freehire("devops", limit=5, max_pages=5)
    assert len(cards) == 3
    assert len(calls) == 2  # second page is short: last page
    assert "offset=0" in calls[0]
    assert "offset=2" in calls[1]


def test_search_freehire_facets_in_url(monkeypatch):
    calls = _stub(monkeypatch, [[_item()]])
    fh.search_freehire("devops", limit=1, remote="remote",
                       country="de", category="devops",
                       seniority="senior")
    url = calls[0]
    assert "q=devops" in url
    assert "remote=remote" in url
    assert "country=DE" in url
    assert "category=devops" in url
    assert "seniority=senior" in url


def test_search_freehire_bad_remote():
    try:
        fh.search_freehire("devops", remote="moon")
    except ValueError as exc:
        assert "remote" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_search_freehire_dedupes_by_slug(monkeypatch):
    monkeypatch.setattr(fh, "PAGE_SIZE", 2)
    item_a, item_b = _item(slug="dup-a"), _item(slug="dup-b")
    item_c = _item(slug="dup-c")
    calls = _stub(monkeypatch, [[item_a, item_b], [item_a, item_c], []])
    cards = fh.search_freehire("devops", limit=10, max_pages=5)
    slugs = [c["job_id"] for c in cards]
    assert slugs == [f"freehire:{s}" for s in ("dup-a", "dup-b", "dup-c")]
    assert len(calls) == 3  # empty third page stops pagination


def test_search_freehire_empty_page_stops(monkeypatch):
    calls = _stub(monkeypatch, [[]])
    assert fh.search_freehire("devops", limit=10) == []
    assert len(calls) == 1


def test_build_postings_enriches_without_fetch():
    cards = [fh.parse_freehire_job(_item())]
    assert cards[0] is not None
    postings = fh.build_postings(cards, no_score=True)
    assert len(postings) == 1
    post = postings[0]
    assert post.title == "Senior DevOps Engineer"
    assert post.company == "Acme Cloud"
    assert post.location == "Berlin, Germany"
    assert post.url.startswith("https://freehire.me/jobs/")
    assert post.via == "freehire"
    assert post.fetch_method == "freehire-api"
    assert post.is_live
    assert "ashby" in " ".join(post.fetch_notes)
    assert any("ashbyhq.com" in note for note in post.fetch_notes)
    # Skills come from the API description through shared enrichment.
    assert "Kubernetes" in post.skills_found
    # Seniority inference sees the Senior title.
    assert post.seniority == "senior"
    # Posted date drives the age calculation.
    assert post.posted == "2026-10-02"
