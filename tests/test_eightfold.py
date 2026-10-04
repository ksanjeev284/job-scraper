"""Eightfold AI board: posting fetch and whole-portal discovery."""

import json
from pathlib import Path

import pytest

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


def test_fetch_eightfold(monkeypatch):
    html = (FIX / "eightfold_job.html").read_text(encoding="utf-8")
    _patch(monkeypatch, lambda url, **kw: _Resp(text=html))
    meta = boards.fetch_eightfold(
        "https://acme.eightfold.ai/careers/job/111")
    assert meta["title"] == "Detection Engineer"
    assert meta["company"] == "Acme Security"
    assert meta["location"] == "Hyderabad"
    assert meta["employment_type"] == "FULL_TIME"
    assert meta["posted"] == "2026-10-01T00:00:00"
    assert meta["source"] == "eightfold-page"
    assert "Splunk" in meta["description_html"]


def test_fetch_eightfold_career_detail_variant(monkeypatch):
    html = (FIX / "eightfold_job.html").read_text(encoding="utf-8")
    _patch(monkeypatch, lambda url, **kw: _Resp(text=html))
    meta = boards.fetch_eightfold(
        "https://acme.eightfold.ai/career_detail/111")
    assert meta["title"] == "Detection Engineer"


def test_fetch_eightfold_foreign_url_is_none():
    assert boards.fetch_eightfold(
        "https://example.com/careers/engineer") is None


def test_fetch_eightfold_no_structured_data_raises(monkeypatch):
    _patch(monkeypatch,
           lambda url, **kw: _Resp(text="<html><body>empty</body></html>"))
    with pytest.raises(ValueError, match="structured data"):
        boards.fetch_eightfold("https://acme.eightfold.ai/careers/job/111")


def _paged(monkeypatch, pages):
    seen = []

    def responder(url, **kw):
        seen.append(url)
        return _Resp(payload=pages[len(seen) - 1])
    _patch(monkeypatch, responder)
    return seen


def test_discover_eightfold_paginates(monkeypatch):
    page1 = json.loads((FIX / "eightfold_search.json").read_text())
    page2 = {"data": {"count": 3, "positions": [
        {"id": 333, "name": "IR Analyst",
         "positionUrl": "https://acme.eightfold.ai/careers/job/333"}]}}
    seen = _paged(monkeypatch, [page1, page2])
    urls = boards.discover_eightfold("acme:acme.com")
    assert urls == [
        "https://acme.eightfold.ai/careers/job/111",
        "https://acme.eightfold.ai/careers/job/222",
        "https://acme.eightfold.ai/careers/job/333",
    ]
    assert "start=0" in seen[0] and "domain=acme.com" in seen[0]
    assert "start=2" in seen[1]


def test_discover_eightfold_single_page(monkeypatch):
    _paged(monkeypatch,
           [{"data": {"count": 1, "positions": [
               {"id": 1, "positionUrl": "/careers/job/1"}]}}])
    assert boards.discover_eightfold("acme:acme.com") == [
        "https://acme.eightfold.ai/careers/job/1"]


def test_discover_eightfold_skips_rows_without_url(monkeypatch):
    _paged(monkeypatch,
           [{"data": {"count": 1, "positions": [{"id": 9}]}}])
    assert boards.discover_eightfold("acme:acme.com") == []


def test_discover_eightfold_bad_spec_raises():
    with pytest.raises(ValueError, match="tenant:domain"):
        boards.discover_eightfold("acme")
    with pytest.raises(ValueError, match="tenant:domain"):
        boards.discover_eightfold(":")


def test_eightfold_in_registry():
    assert boards.DISCOVERERS["eightfold"] is boards.discover_eightfold
    assert boards.fetch_eightfold in boards.BOARD_FETCHERS
