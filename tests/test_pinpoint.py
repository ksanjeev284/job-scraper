"""Pinpoint board: single-posting fetch from the public postings.json feed."""

import jobscraper.boards as boards


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _feed_payload():
    """Synthetic Pinpoint feed (fictional company, modeled on the real
    postings.json schema)."""
    return {
        "data": [
            {
                "id": "547660",
                "title": "Senior Platform Engineer",
                "url": "https://examplecorp.pinpointhq.com/en/postings/"
                       "142f2b86-97ea-478f-9727-69e5049873ce",
                "path": "/en/postings/142f2b86-97ea-478f-9727-69e5049873ce",
                "description": "<div><strong>About the role</strong><br>"
                               "Own our Kubernetes platform end to end.</div>",
                "key_responsibilities": "<ul><li>Run the on-call rota</li></ul>",
                "key_responsibilities_header": "What you will do",
                "skills_knowledge_expertise": "",
                "skills_knowledge_expertise_header": "",
                "benefits": "<p>25 days holiday</p>",
                "benefits_header": "Benefits",
                "location": {"id": "1", "city": "Berlin", "name": "Berlin",
                             "province": "", "postal_code": "10115",
                             "street_address": ""},
                "workplace_type": "hybrid",
                "workplace_type_text": "Hybrid",
                "employment_type": "full_time",
                "employment_type_text": "Full Time",
                "compensation_visible": True,
                "compensation_minimum": 80000,
                "compensation_maximum": 100000,
                "compensation_currency": "EUR",
                "compensation_frequency": "per year",
            },
            {
                "id": "999",
                "title": "Remote Support Engineer",
                "url": "https://examplecorp.pinpointhq.com/en/postings/"
                       "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "path": "/en/postings/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "description": "<p>Support our customers worldwide.</p>",
                "key_responsibilities": "",
                "skills_knowledge_expertise": "",
                "benefits": "",
                "location": {"city": "", "name": "Remote"},
                "workplace_type": "remote",
                "workplace_type_text": "Fully remote",
                "employment_type": "full_time",
                "employment_type_text": "Full Time",
                "compensation_visible": False,
                "compensation_minimum": None,
                "compensation_maximum": None,
                "compensation_currency": None,
                "compensation_frequency": None,
            },
        ]
    }


def _patch(monkeypatch, payload):
    monkeypatch.setattr(
        boards, "http_get", lambda url, **kw: _FakeResp(payload))


def test_fetch_pinpoint_by_uuid_url(monkeypatch):
    _patch(monkeypatch, _feed_payload())
    rec = boards.fetch_pinpoint(
        "https://examplecorp.pinpointhq.com/en/postings/"
        "142f2b86-97ea-478f-9727-69e5049873ce")
    assert rec is not None
    assert rec["title"] == "Senior Platform Engineer"
    assert rec["company"] == "examplecorp"
    assert rec["location"] == "Berlin"
    assert rec["employment_type"] == "Full Time"
    assert rec["source"] == "pinpoint-feed"
    assert "Kubernetes platform" in rec["description_html"]
    assert "What you will do" in rec["description_html"]
    assert "Benefits" in rec["description_html"]
    assert "25 days holiday" in rec["description_html"]
    assert rec["salary_hits_extra"] == ["EUR 80000-100000 per year"]


def test_fetch_pinpoint_by_numeric_jobs_url(monkeypatch):
    _patch(monkeypatch, _feed_payload())
    rec = boards.fetch_pinpoint(
        "https://examplecorp.pinpointhq.com/en/jobs/547660/hiring-process")
    assert rec is not None
    assert rec["title"] == "Senior Platform Engineer"


def test_fetch_pinpoint_remote_location(monkeypatch):
    _patch(monkeypatch, _feed_payload())
    rec = boards.fetch_pinpoint(
        "https://examplecorp.pinpointhq.com/en/postings/"
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert rec is not None
    assert rec["location"] == "Remote"
    assert rec["salary_hits_extra"] == []


def test_fetch_pinpoint_unknown_id(monkeypatch):
    _patch(monkeypatch, _feed_payload())
    assert boards.fetch_pinpoint(
        "https://examplecorp.pinpointhq.com/en/jobs/123/hiring-process"
    ) is None


def test_fetch_pinpoint_non_pinpoint_url(monkeypatch):
    _patch(monkeypatch, _feed_payload())
    assert boards.fetch_pinpoint("https://boards.greenhouse.io/acme/jobs/1"
                                 ) is None


def test_fetch_pinpoint_registered_in_fetchers():
    assert boards.fetch_pinpoint in boards.BOARD_FETCHERS


def test_discover_pinpoint_reads_data_key(monkeypatch):
    """The real feed nests items under "data"; discovery must read it."""
    _patch(monkeypatch, _feed_payload())
    urls = boards.discover_pinpoint("examplecorp")
    assert urls == [
        "https://examplecorp.pinpointhq.com/en/postings/"
        "142f2b86-97ea-478f-9727-69e5049873ce",
        "https://examplecorp.pinpointhq.com/en/postings/"
        "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    ]
