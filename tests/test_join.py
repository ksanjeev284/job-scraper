"""join.com ATS board: posting fetch and whole-board discovery."""

import json
from pathlib import Path

import jobscraper.boards as boards

FIX = Path(__file__).parent / "fixtures"


class _Resp:
    def __init__(self, text=None, payload=None):
        self.text = text
        self._payload = payload

    def json(self):
        return self._payload


def _patch(monkeypatch, responder):
    monkeypatch.setattr(boards, "http_get", responder)


def test_fetch_join(monkeypatch):
    detail = json.loads((FIX / "join_detail.json").read_text(
        encoding="utf-8"))
    _patch(monkeypatch, lambda url, **kw: _Resp(payload=detail))
    meta = boards.fetch_join(
        "https://join.com/companies/alteos/"
        "16769212-customer-care-and-claims-agent-netherlands-m-f-d")
    assert meta["title"] == ("Customer Care & Claims Agent Netherlands "
                             "(m/f/d)")
    assert meta["company"] == "Alteos GmbH"
    assert meta["location"] == "Tauentzienstraße 7B/C, Berlin, Deutschland"
    assert meta["employment_type"] is None  # detail endpoint only has the id
    assert meta["department"] is None  # detail endpoint only has categoryId
    assert meta["posted"] == "2026-09-17T11:34:31.734Z"
    assert meta["source"] == "join-api"
    assert "<ul>" in meta["description_html"]
    assert "<li>" in meta["description_html"]
    assert "<strong>" not in meta["description_html"]  # no raw markup leak


def test_fetch_join_markdown_headings_and_bold(monkeypatch):
    detail = {
        "idParam": "1-x", "status": "ONLINE",
        "title": "Dev", "company": {"name": "Acme"},
        "description": ("## Tasks\n\n* **fast** code\n\n"
                        "[apply](https://example.com/a)"),
        "country": {"name": "Germany"}, "city": {"cityName": "Munich"},
        "workplaceType": "ONSITE",
    }
    _patch(monkeypatch, lambda url, **kw: _Resp(payload=detail))
    meta = boards.fetch_join("https://join.com/companies/acme/1-x")
    assert "<h3>Tasks</h3>" in meta["description_html"]
    assert "<li><strong>fast</strong> code</li>" in meta["description_html"]
    assert ('<a href="https://example.com/a">apply</a>'
            in meta["description_html"])


def test_fetch_join_remote_and_hybrid_location(monkeypatch):
    base = {"idParam": "1-x", "status": "ONLINE", "title": "Dev",
            "company": {"name": "Acme"}, "description": "",
            "country": {"name": "Germany"}, "city": {"cityName": "Munich"}}
    remote = dict(base, workplaceType="REMOTE")
    _patch(monkeypatch, lambda url, **kw: _Resp(payload=remote))
    assert boards.fetch_join("https://join.com/companies/acme/1-x")[
        "location"] == "Remote, Germany"
    hybrid = dict(base, workplaceType="HYBRID")
    _patch(monkeypatch, lambda url, **kw: _Resp(payload=hybrid))
    assert boards.fetch_join("https://join.com/companies/acme/1-x")[
        "location"] == "Munich, Germany (Hybrid)"


def test_fetch_join_html_escaping(monkeypatch):
    detail = {"idParam": "1-x", "status": "ONLINE", "title": "Dev",
              "company": {"name": "Acme"},
              "description": "<script>alert(1)</script>",
              "country": {}, "city": {}, "workplaceType": "ONSITE"}
    _patch(monkeypatch, lambda url, **kw: _Resp(payload=detail))
    meta = boards.fetch_join("https://join.com/companies/acme/1-x")
    assert "<script>" not in meta["description_html"]
    assert "&lt;script&gt;" in meta["description_html"]


def test_fetch_join_foreign_url_is_none():
    assert boards.fetch_join("https://example.com/careers/engineer") is None


def test_discover_join(monkeypatch):
    page1 = json.loads((FIX / "join_board_page1.json").read_text(
        encoding="utf-8"))
    company_html = '<html>"company":{"id":1080,"name":"Alteos GmbH"}</html>'

    def responder(url, **kw):
        if url.startswith("https://join.com/companies/alteos"):
            return _Resp(text=company_html)
        payload = json.loads(json.dumps(page1))
        if "page=2" in url:
            payload["items"] = [dict(payload["items"][0],
                                     idParam="99999999-second-page-job")]
        return _Resp(payload=payload)

    _patch(monkeypatch, responder)
    urls = boards.discover_join("alteos")
    assert len(urls) == 6  # 5 on page 1, 1 on page 2
    assert all(u.startswith("https://join.com/companies/alteos/")
               for u in urls)
    assert len(set(urls)) == len(urls)


def test_discover_join_unknown_company(monkeypatch):
    _patch(monkeypatch, lambda url, **kw: _Resp(text="<html></html>"))
    assert boards.discover_join("no-such-company-xyz") == []


def test_board_name_for_join_url():
    assert boards.board_name_for_url(
        "https://join.com/companies/alteos/123-abc") == "join"
    assert boards.DISCOVERERS["join"] is boards.discover_join
    assert boards.fetch_join in boards.BOARD_FETCHERS
