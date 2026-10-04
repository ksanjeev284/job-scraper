"""Tests for the webhook notification / export-hook module."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from jobscraper.models import MatchResult, Posting
from jobscraper.notify import (
    build_payload,
    build_pushover_payload,
    deliver,
    host_of,
    pushover_credentials,
    send_pushover,
    send_webhook,
    webhook_urls_from_env,
)


def _posting(title: str, score: int, *, is_new: bool = False) -> Posting:
    return Posting(
        url=f"https://example.com/jobs/{title.lower()}",
        title=title,
        company="Acme Corp",
        location="Hyderabad, India",
        is_new=is_new,
        match=MatchResult(total=score),
    )


def test_plain_payload_shape_and_ranking():
    posts = [_posting("b", 60), _posting("a", 90), _posting("c", 30)]
    payload = build_payload(posts, "plain", top=10)
    assert payload is not None
    assert payload["tool"] == "job-scraper"
    assert payload["total"] == 3
    titles = [p["title"] for p in payload["postings"]]
    assert titles == ["a", "b", "c"]


def test_plain_payload_only_new_filters():
    posts = [_posting("old", 90), _posting("fresh", 50, is_new=True)]
    payload = build_payload(posts, "plain", only_new=True, top=10)
    assert payload is not None
    assert payload["new"] == 1
    assert [p["title"] for p in payload["postings"]] == ["fresh"]


def test_only_new_nothing_new_returns_none():
    posts = [_posting("old", 90)]
    assert build_payload(posts, "plain", only_new=True) is None
    assert build_payload(posts, "slack", only_new=True) is None


def test_top_cap_applies():
    posts = [_posting(f"job{i}", i) for i in range(30)]
    payload = build_payload(posts, "plain", top=10)
    assert payload is not None
    assert len(payload["postings"]) == 10


def test_slack_blocks_structure():
    posts = [_posting("Analyst", 80, is_new=True), _posting("Dev", 40)]
    payload = build_payload(posts, "slack")
    assert payload is not None
    blocks = payload["blocks"]
    assert blocks[0]["type"] == "section"
    assert "1 new" in blocks[0]["text"]["text"]
    assert blocks[1]["type"] == "section"
    body = blocks[1]["text"]["text"]
    assert "<https://example.com/jobs/analyst|Analyst>" in body
    assert ":new:" in body
    assert "Acme Corp" in body and "score 80" in body


def test_slack_escapes_mrkdwn_specials():
    posts = [_posting("Dev <lead> & staff", 70)]
    payload = build_payload(posts, "slack")
    assert payload is not None
    body = payload["blocks"][1]["text"]["text"]
    assert "Dev &lt;lead&gt; &amp; staff" in body


def test_slack_never_prints_secret_paths_in_blocks():
    posts = [_posting("Analyst", 80)]
    payload = build_payload(posts, "slack")
    assert payload is not None
    assert "secret" not in json.dumps(payload)


def test_discord_embed_limits():
    posts = [_posting(f"job{i}", i) for i in range(25)]
    payload = build_payload(posts, "discord")
    assert payload is not None
    assert len(payload["embeds"]) <= 10
    assert "showing top 10 of 25" in payload["content"]
    first = payload["embeds"][0]
    assert first["title"] == "job24"
    assert "Score: 24" in first["description"]


def test_discord_title_truncated():
    posts = [_posting("x" * 400, 70)]
    payload = build_payload(posts, "discord")
    assert payload is not None
    assert len(payload["embeds"][0]["title"]) <= 256


def test_unknown_mode_raises():
    with pytest.raises(ValueError):
        build_payload([_posting("a", 1)], "email")  # type: ignore[arg-type]


def test_host_of_never_leaks_path():
    url = "https://hooks.slack.com/services/T000/B000/secret-token-xyz"
    assert host_of(url) == "hooks.slack.com"
    assert "secret-token-xyz" not in host_of(url)


def test_send_webhook_rejects_non_http():
    with pytest.raises(ValueError):
        send_webhook("ftp://example.com/hook", {"a": 1})


class _Capture(BaseHTTPRequestHandler):
    received: list[bytes] = []
    content_types: list[str] = []
    status: int = 200

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        _Capture.received.append(self.rfile.read(length))
        _Capture.content_types.append(
            self.headers.get("Content-Type", ""))
        self.send_response(_Capture.status)
        self.end_headers()

    def log_message(self, *args):  # noqa: ANN002, ANN202
        pass


def _serve() -> tuple[HTTPServer, threading.Thread]:
    server = HTTPServer(("127.0.0.1", 0), _Capture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_send_webhook_posts_json_locally():
    _Capture.received.clear()
    server, thread = _serve()
    try:
        url = f"http://127.0.0.1:{server.server_port}/hook/secret"
        ok, detail = send_webhook(url, {"hello": "world"})
        assert ok, detail
        assert "127.0.0.1" in detail
        assert "secret" not in detail  # host only, never the secret path
        assert len(_Capture.received) == 1
        assert json.loads(_Capture.received[0].decode()) == {
            "hello": "world"}
    finally:
        server.shutdown()
        thread.join()


def test_send_webhook_connection_refused():
    ok, detail = send_webhook("http://127.0.0.1:1/hook", {"a": 1})
    assert not ok
    assert detail  # non-empty, host-only


def test_deliver_skips_when_nothing_new():
    posts = [_posting("old", 90)]
    results = deliver(posts, ["https://hooks.example.com/hook/abc"],
                      only_new=True)
    assert results == [("hooks.example.com", None,
                        "skipped: no new postings")]


def test_deliver_bad_scheme_reported_not_raised():
    posts = [_posting("a", 10)]
    results = deliver(posts, ["ftp://example.com/hook"], mode="plain")
    assert results[0][0] == "example.com"
    assert results[0][1] is False


def test_webhook_urls_from_env(monkeypatch):
    monkeypatch.setenv(
        "JOBSCRAPER_WEBHOOK_URL",
        "https://a.example.com/hook/1, https://b.example.com/hook/2 ,")
    assert webhook_urls_from_env() == [
        "https://a.example.com/hook/1", "https://b.example.com/hook/2"]
    monkeypatch.delenv("JOBSCRAPER_WEBHOOK_URL")
    assert webhook_urls_from_env() == []


# --- Pushover phone-push channel -------------------------------------------


def _pushover_posts() -> list[Posting]:
    return [_posting("analyst", 80, is_new=True), _posting("dev", 40)]


def test_pushover_payload_fields_and_ranking():
    payload = build_pushover_payload(_pushover_posts(), token="tok",
                                     user="usr")
    assert payload is not None
    assert payload["token"] == "tok"
    assert payload["user"] == "usr"
    assert payload["priority"] == 0
    assert "1 new" in payload["title"]
    # ranked: analyst (80) before dev (40); top post attached as URL
    assert payload["message"].splitlines()[0].startswith("analyst -")
    assert "[NEW]" in payload["message"]
    assert payload["url"] == "https://example.com/jobs/analyst"
    assert payload["url_title"] == "Open top posting"


def test_pushover_only_new_filters():
    posts = [_posting("old", 90), _posting("fresh", 50, is_new=True)]
    payload = build_pushover_payload(posts, token="t", user="u",
                                     only_new=True)
    assert payload is not None
    assert "fresh -" in payload["message"]
    assert "old -" not in payload["message"]


def test_pushover_only_new_nothing_new_returns_none():
    assert (build_pushover_payload([_posting("old", 90)], token="t",
                                   user="u", only_new=True) is None)


def test_pushover_message_capped_at_limit():
    posts = [_posting(f"job{i} with a fairly long descriptive title", i)
             for i in range(60)]
    payload = build_pushover_payload(posts, token="t", user="u")
    assert payload is not None
    assert len(payload["message"]) <= 1024


def test_pushover_credentials_from_env(monkeypatch):
    monkeypatch.setenv("JOBSCRAPER_PUSHOVER_TOKEN", "tok123")
    monkeypatch.setenv("JOBSCRAPER_PUSHOVER_USER", "usr456")
    assert pushover_credentials() == ("tok123", "usr456")
    # explicit args win over the environment
    assert pushover_credentials(token="x", user="y") == ("x", "y")


def test_pushover_credentials_missing_raises(monkeypatch):
    monkeypatch.delenv("JOBSCRAPER_PUSHOVER_TOKEN", raising=False)
    monkeypatch.delenv("JOBSCRAPER_PUSHOVER_USER", raising=False)
    with pytest.raises(ValueError, match="JOBSCRAPER_PUSHOVER_TOKEN"):
        pushover_credentials()
    with pytest.raises(ValueError):
        send_pushover(_pushover_posts())


def test_send_pushover_posts_form_encoded(monkeypatch):
    _Capture.received.clear()
    _Capture.content_types.clear()
    server, thread = _serve()
    try:
        monkeypatch.setattr(
            "jobscraper.notify._PUSHOVER_ENDPOINT",
            f"http://127.0.0.1:{server.server_port}/1/messages.json")
        ok, detail = send_pushover(_pushover_posts(), token="tok",
                                   user="usr")
        assert ok, detail
        assert "127.0.0.1" in detail
        assert "tok" not in detail and "usr" not in detail
        assert len(_Capture.received) == 1
        assert (_Capture.content_types[0]
                == "application/x-www-form-urlencoded")
        fields = dict(pair.split("=", 1)
                      for pair in _Capture.received[0].decode().split("&"))
        assert fields["token"] == "tok"
        assert fields["user"] == "usr"
        assert "analyst" in fields["message"]
    finally:
        server.shutdown()
        thread.join()


def test_send_pushover_server_error_returns_false(monkeypatch):
    _Capture.status = 400
    server, thread = _serve()
    try:
        monkeypatch.setattr(
            "jobscraper.notify._PUSHOVER_ENDPOINT",
            f"http://127.0.0.1:{server.server_port}/1/messages.json")
        ok, detail = send_pushover(_pushover_posts(), token="t",
                                   user="u")
        assert not ok
        assert "HTTP 400" in detail
    finally:
        _Capture.status = 200
        server.shutdown()
        thread.join()


def test_send_pushover_skips_when_nothing_new():
    ok, detail = send_pushover([_posting("old", 90)], token="t",
                               user="u", only_new=True)
    assert ok is None
    assert "no new postings" in detail


def test_cli_offers_pushover_webhook_mode():
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "jobscraper", "--help"],
        capture_output=True, text=True, timeout=30, cwd=".")
    assert proc.returncode == 0
    assert "pushover" in proc.stdout
