"""Board discovery: enumerate every posting on a career portal."""

import jobscraper.boards as boards


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _patch(monkeypatch, payload):
    monkeypatch.setattr(
        boards, "http_get", lambda url, **kw: _FakeResp(payload))


def test_discover_lever(monkeypatch):
    _patch(monkeypatch, [{"hostedUrl": "https://jobs.lever.co/acme/1"},
                         {"hostedUrl": None}])
    assert boards.discover_lever("acme") == ["https://jobs.lever.co/acme/1"]


def test_discover_ashby(monkeypatch):
    _patch(monkeypatch, {"jobs": [{"jobUrl": "https://jobs.ashbyhq.com/a/1"},
                                  {"jobUrl": ""}]})
    assert boards.discover_ashby("a") == ["https://jobs.ashbyhq.com/a/1"]


def test_discover_greenhouse(monkeypatch):
    _patch(monkeypatch, {"jobs": [
        {"absolute_url": "https://boards.greenhouse.io/acme/jobs/1"}]})
    assert boards.discover_greenhouse("acme") == [
        "https://boards.greenhouse.io/acme/jobs/1"]


def test_discover_teamtailor(monkeypatch):
    _patch(monkeypatch, {"items": [
        {"url": "https://careers.acme.com/jobs/1"}, {"url": None}]})
    assert boards.discover_teamtailor("careers.acme.com") == [
        "https://careers.acme.com/jobs/1"]


def test_discover_breezy(monkeypatch):
    _patch(monkeypatch, [{"url": "https://acme.breezy.hr/p/abc"},
                         {"name": "no url"}])
    assert boards.discover_breezy("acme") == ["https://acme.breezy.hr/p/abc"]


def test_discover_workable(monkeypatch):
    _patch(monkeypatch, {"jobs": [{"shortcode": "ABC123"}]})
    assert boards.discover_workable("acme") == [
        "https://apply.workable.com/acme/j/ABC123/"]


def test_discoverers_registry():
    for name in ("lever", "ashby", "greenhouse", "smartrecruiters",
                 "workday", "teamtailor", "personio", "recruitee",
                 "workable", "breezy", "pinpoint", "rippling"):
        assert name in boards.DISCOVERERS, name
        assert callable(boards.DISCOVERERS[name])


def test_discover_personio(monkeypatch):
    xml = ("<jobs><position><id>42</id><name>Eng</name></position>"
           "</jobs>")

    class _FakeText:
        text = xml

    monkeypatch.setattr(boards, "http_get",
                        lambda url, **kw: _FakeText())
    assert boards.discover_personio("acme") == [
        "https://acme.jobs.personio.de/job/42"]
