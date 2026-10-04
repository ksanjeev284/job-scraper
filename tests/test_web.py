"""Web service: job submission, status, exports, examples."""

import time

from fastapi.testclient import TestClient

import jobscraper.web as web
from jobscraper.models import Posting

client = TestClient(web.app)


def _fake_pipeline(urls, **kwargs):
    cb = kwargs.get("progress_cb")
    posts = []
    for i, url in enumerate(urls):
        post = Posting(url=url)
        post.title, post.company = f"Title {i}", "Acme"
        posts.append(post)
        if cb:
            cb(i + 1, len(urls))
    return posts, 0


def test_index_serves_gui():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "jobscraper" in resp.text
    assert "Scrape" in resp.text


def test_examples_list_and_get():
    resp = client.get("/api/examples")
    assert resp.status_code == 200
    names = resp.json()["examples"]
    assert "template" in names
    resp = client.get("/api/examples/template")
    assert resp.status_code == 200
    assert "skills" in resp.json()


def test_examples_path_traversal_blocked():
    assert client.get("/api/examples/..%2F..%2Fsecret").status_code == 404


def test_scrape_rejects_empty():
    resp = client.post("/api/scrape", json={"urls": []})
    assert resp.status_code == 400


def test_scrape_rejects_unknown_board():
    resp = client.post("/api/scrape",
                       json={"urls": [], "discover": ["nosuch:x"]})
    assert resp.status_code == 400


def test_scrape_full_cycle(monkeypatch):
    monkeypatch.setattr(web, "run_pipeline", _fake_pipeline)
    resp = client.post("/api/scrape",
                       json={"urls": ["https://example.com/1",
                                      "https://example.com/2"]})
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]
    for _ in range(50):  # wait for the background thread
        status = client.get(f"/api/jobs/{job_id}").json()
        if status["status"] == "done":
            break
        time.sleep(0.1)
    assert status["status"] == "done"
    assert len(status["results"]) == 2
    assert status["results"][0]["title"] == "Title 0"

    exp = client.get(f"/api/export/{job_id}.json")
    assert exp.status_code == 200
    assert len(exp.json()) == 2
    csv_resp = client.get(f"/api/export/{job_id}.csv")
    assert csv_resp.status_code == 200
    assert "Title 0" in csv_resp.text


def test_job_unknown():
    assert client.get("/api/jobs/nope").status_code == 404
