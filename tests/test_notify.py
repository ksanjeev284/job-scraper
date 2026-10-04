"""Tests for the webhook notification / export-hook module."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from jobscraper.models import MatchResult, Posting
from jobscraper.notify import (
    build_email_message,
    build_payload,
    build_pushover_payload,
    build_telegram_payload,
    deliver,
    host_of,
    pushover_credentials,
    send_email_digest,
    send_pushover,
    send_telegram,
    send_webhook,
    smtp_settings,
    telegram_credentials,
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


# --- Telegram bot-message channel -------------------------------------------

class _TelegramCapture(BaseHTTPRequestHandler):
    received: list[bytes] = []
    ok: bool = True
    description: str = "chat not found"

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        _TelegramCapture.received.append(self.rfile.read(length))
        body = (b'{"ok": true, "result": {}}' if _TelegramCapture.ok
                else json.dumps({"ok": False, "description":
                                 _TelegramCapture.description}).encode())
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: ANN002, ANN202
        pass


def _serve_telegram() -> tuple[HTTPServer, threading.Thread]:
    server = HTTPServer(("127.0.0.1", 0), _TelegramCapture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _telegram_posts() -> list[Posting]:
    return [_posting("analyst", 80, is_new=True), _posting("dev", 40)]


def test_telegram_payload_fields_and_ranking():
    payload = build_telegram_payload(_telegram_posts(), chat_id="12345")
    assert payload is not None
    assert payload["chat_id"] == "12345"
    assert payload["disable_web_page_preview"] is True
    text = payload["text"]
    # ranked: analyst (80) before dev (40); URLs are inline as plain text
    assert text.splitlines()[1].startswith("analyst -")
    assert "[NEW]" in text
    assert "https://example.com/jobs/analyst" in text
    assert "https://example.com/jobs/dev" in text
    assert "1 new" in text.splitlines()[0]


def test_telegram_only_new_filters():
    posts = [_posting("old", 90), _posting("fresh", 50, is_new=True)]
    payload = build_telegram_payload(posts, chat_id="1", only_new=True)
    assert payload is not None
    assert "fresh -" in payload["text"]
    assert "old -" not in payload["text"]


def test_telegram_only_new_nothing_new_returns_none():
    assert (build_telegram_payload([_posting("old", 90)], chat_id="1",
                                    only_new=True) is None)


def test_telegram_message_capped_at_limit():
    posts = [_posting(f"job{i} with a fairly long descriptive title", i)
             for i in range(200)]
    payload = build_telegram_payload(posts, chat_id="1")
    assert payload is not None
    assert len(payload["text"]) <= 4096


def test_telegram_credentials_from_env(monkeypatch):
    monkeypatch.setenv("JOBSCRAPER_TELEGRAM_TOKEN", "tok123")
    monkeypatch.setenv("JOBSCRAPER_TELEGRAM_CHAT_ID", "987654")
    assert telegram_credentials() == ("tok123", "987654")
    # explicit args win over the environment
    assert telegram_credentials(token="x", chat_id="y") == ("x", "y")


def test_telegram_credentials_missing_raises(monkeypatch):
    monkeypatch.delenv("JOBSCRAPER_TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("JOBSCRAPER_TELEGRAM_CHAT_ID", raising=False)
    with pytest.raises(ValueError, match="JOBSCRAPER_TELEGRAM_TOKEN"):
        telegram_credentials()
    with pytest.raises(ValueError):
        send_telegram(_telegram_posts())


def test_send_telegram_posts_json_and_checks_ok_body(monkeypatch):
    _TelegramCapture.received.clear()
    server, thread = _serve_telegram()
    try:
        monkeypatch.setattr(
            "jobscraper.notify._TELEGRAM_ENDPOINT_TEMPLATE",
            f"http://127.0.0.1:{server.server_port}/bot{{token}}/"
            "sendMessage")
        ok, detail = send_telegram(_telegram_posts(), token="tok",
                                   chat_id="12345")
        assert ok, detail
        assert "127.0.0.1" in detail
        assert "tok" not in detail and "12345" not in detail
        assert len(_TelegramCapture.received) == 1
        payload = json.loads(_TelegramCapture.received[0].decode())
        assert payload["chat_id"] == "12345"
        assert "analyst" in payload["text"]
        assert payload["disable_web_page_preview"] is True
    finally:
        server.shutdown()
        thread.join()


def test_send_telegram_api_false_ok_reports_description(monkeypatch):
    _TelegramCapture.ok = False
    _TelegramCapture.received.clear()
    server, thread = _serve_telegram()
    try:
        monkeypatch.setattr(
            "jobscraper.notify._TELEGRAM_ENDPOINT_TEMPLATE",
            f"http://127.0.0.1:{server.server_port}/bot{{token}}/"
            "sendMessage")
        ok, detail = send_telegram(_telegram_posts(), token="tok",
                                   chat_id="12345")
        assert not ok
        assert "chat not found" in detail
        assert "tok" not in detail  # token stays out of error strings
    finally:
        _TelegramCapture.ok = True
        server.shutdown()
        thread.join()
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


# --- SMTP email digest channel -----------------------------------------------


_SMTP_ENV = ["JOBSCRAPER_SMTP_HOST", "JOBSCRAPER_SMTP_PORT",
             "JOBSCRAPER_SMTP_USER", "JOBSCRAPER_SMTP_PASSWORD",
             "JOBSCRAPER_SMTP_FROM", "JOBSCRAPER_SMTP_TO"]


def _clear_smtp_env(monkeypatch):
    for name in _SMTP_ENV:
        monkeypatch.delenv(name, raising=False)


def _email_posts() -> list[Posting]:
    post = Posting(
        url="https://example.com/jobs/analyst",
        title="Security Analyst",
        company="Acme Corp",
        location="Hyderabad, India",
        seniority="mid",
        salary_hits=["15 LPA"],
        is_new=True,
        match=MatchResult(total=80),
    )
    return [post, _posting("dev", 40)]


def _html_part(message) -> str:
    return message.get_body(("html",)).get_content()


def test_email_message_structure_and_ranking():
    msg = build_email_message(_email_posts(), from_addr="a@example.com",
                              to_addr="b@example.com")
    assert msg is not None
    assert "1 new" in msg["Subject"]
    assert msg["From"] == "a@example.com"
    assert msg["To"] == "b@example.com"
    html_body = _html_part(msg)
    # ranked: analyst (80) card before dev (40)
    assert html_body.index("Security Analyst") < html_body.index("dev")
    assert '<a href="https://example.com/jobs/analyst"' in html_body
    assert "NEW" in html_body  # new-posting badge
    assert "mid" in html_body  # seniority level shown
    assert "15 LPA" in html_body  # salary hits shown
    plain = msg.get_body(("plain",)).get_content()
    assert "Security Analyst - Acme Corp" in plain
    assert "https://example.com/jobs/analyst" in plain


def test_email_escapes_posting_html():
    posts = [_posting("Dev <script>alert(1)</script>", 70)]
    msg = build_email_message(posts, from_addr="a@e.com", to_addr="b@e.com")
    assert msg is not None
    html_body = _html_part(msg)
    assert "<script>" not in html_body
    assert "&lt;script&gt;" in html_body


def test_email_only_new_filters_and_skips():
    posts = [_posting("old", 90), _posting("fresh", 50, is_new=True)]
    msg = build_email_message(posts, from_addr="a@e.com", to_addr="b@e.com",
                              only_new=True)
    assert msg is not None
    html_body = _html_part(msg)
    assert "fresh" in html_body and "old" not in html_body
    assert (build_email_message([_posting("old", 90)],
                               from_addr="a@e.com", to_addr="b@e.com",
                               only_new=True) is None)


def test_email_top_cap_applies():
    posts = [_posting(f"job{i}", i) for i in range(30)]
    msg = build_email_message(posts, from_addr="a@e.com", to_addr="b@e.com",
                              top=10)
    assert msg is not None
    assert _html_part(msg).count("<tr><td") == 10


def test_email_empty_posts_still_builds():
    msg = build_email_message([], from_addr="a@e.com", to_addr="b@e.com")
    assert msg is not None
    assert "(no postings)" in _html_part(msg)


def test_smtp_settings_from_env(monkeypatch):
    _clear_smtp_env(monkeypatch)
    monkeypatch.setenv("JOBSCRAPER_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("JOBSCRAPER_SMTP_PORT", "2525")
    monkeypatch.setenv("JOBSCRAPER_SMTP_USER", "bot")
    monkeypatch.setenv("JOBSCRAPER_SMTP_PASSWORD", "s3cret")
    monkeypatch.setenv("JOBSCRAPER_SMTP_TO", "me@example.com")
    settings = smtp_settings()
    assert settings.host == "smtp.example.com"
    assert settings.port == 2525
    assert settings.from_addr == "bot"  # defaults to the username
    # explicit args win over the environment
    settings = smtp_settings(host="other.example.com", use_tls=False)
    assert settings.host == "other.example.com"
    assert settings.use_tls is False


def test_smtp_settings_defaults(monkeypatch):
    _clear_smtp_env(monkeypatch)
    monkeypatch.setenv("JOBSCRAPER_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("JOBSCRAPER_SMTP_TO", "me@example.com")
    settings = smtp_settings()
    assert settings.port == 587
    assert settings.user == "" and settings.password == ""
    assert settings.from_addr == ""  # no username to default to


def test_smtp_settings_missing_raises(monkeypatch):
    _clear_smtp_env(monkeypatch)
    with pytest.raises(ValueError, match="JOBSCRAPER_SMTP_HOST"):
        smtp_settings()
    monkeypatch.setenv("JOBSCRAPER_SMTP_HOST", "smtp.example.com")
    with pytest.raises(ValueError, match="JOBSCRAPER_SMTP_TO"):
        smtp_settings()
    # a username without a password is refused (would fail at login)
    monkeypatch.setenv("JOBSCRAPER_SMTP_TO", "me@example.com")
    with pytest.raises(ValueError, match="JOBSCRAPER_SMTP_PASSWORD"):
        smtp_settings(user="bot")
    # the password never leaks into the error
    try:
        smtp_settings()
    except ValueError as exc:
        assert "s3cret" not in str(exc)


def test_smtp_settings_bad_port_raises(monkeypatch):
    _clear_smtp_env(monkeypatch)
    monkeypatch.setenv("JOBSCRAPER_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("JOBSCRAPER_SMTP_TO", "me@example.com")
    monkeypatch.setenv("JOBSCRAPER_SMTP_PORT", "not-a-port")
    with pytest.raises(ValueError, match="invalid SMTP port"):
        smtp_settings()


class _FakeSMTP:
    instances: list[_FakeSMTP] = []
    fail: Exception | None = None

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.calls: list[str] = []
        self.messages: list = []
        self.login_args = None
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def ehlo(self):
        self.calls.append("ehlo")

    def starttls(self, context=None):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append("login")
        self.login_args = (user, password)

    def send_message(self, message):
        if _FakeSMTP.fail is not None:
            raise _FakeSMTP.fail
        self.calls.append("send_message")
        self.messages.append(message)


@pytest.fixture
def fake_smtp(monkeypatch):
    _FakeSMTP.instances.clear()
    _FakeSMTP.fail = None
    monkeypatch.setattr("jobscraper.notify.smtplib.SMTP", _FakeSMTP)
    return _FakeSMTP


def _settings(**overrides):
    from jobscraper.notify import SMTPSettings
    params = dict(host="smtp.example.com", port=587, user="bot",
                  password="s3cret", from_addr="bot@example.com",
                  to_addr="me@example.com", use_tls=True)
    params.update(overrides)
    return SMTPSettings(**params)


def test_send_email_digest_tls_login_send_order(fake_smtp):
    ok, detail = send_email_digest(_email_posts(),
                                   settings=_settings())
    assert ok, detail
    assert "smtp.example.com" in detail
    assert "s3cret" not in detail and "me@example.com" not in detail
    server = fake_smtp.instances[0]
    assert server.calls == ["ehlo", "starttls", "ehlo", "login",
                            "send_message"]
    assert server.login_args == ("bot", "s3cret")
    sent = server.messages[0]
    assert sent["To"] == "me@example.com"
    assert "Security Analyst" in _html_part(sent)


def test_send_email_digest_skips_tls_and_auth_when_unset(fake_smtp):
    ok, detail = send_email_digest(_email_posts(),
                                   settings=_settings(user="",
                                                      password="",
                                                      use_tls=False))
    assert ok, detail
    server = fake_smtp.instances[0]
    assert server.calls == ["ehlo", "send_message"]


def test_send_email_digest_smtp_error_returns_false(fake_smtp):
    import smtplib
    fake_smtp.fail = smtplib.SMTPException("relaying denied")
    ok, detail = send_email_digest(_email_posts(),
                                   settings=_settings())
    assert not ok
    assert "SMTP error" in detail
    assert "s3cret" not in detail


def test_send_email_digest_connection_refused_returns_false(fake_smtp,
                                                            monkeypatch):
    monkeypatch.setattr(
        "jobscraper.notify.smtplib.SMTP",
        lambda *a, **k: (_ for _ in ()).throw(
            ConnectionRefusedError(111, "refused")))
    ok, detail = send_email_digest(_email_posts(),
                                   settings=_settings())
    assert not ok
    assert "ConnectionRefusedError" in detail


def test_send_email_digest_skips_when_nothing_new(fake_smtp):
    ok, detail = send_email_digest([_posting("old", 90)],
                                   settings=_settings(), only_new=True)
    assert ok is None
    assert "no new postings" in detail
    assert not fake_smtp.instances  # no SMTP connection opened


def test_cli_offers_email_webhook_mode():
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "jobscraper", "--help"],
        capture_output=True, text=True, timeout=30, cwd=".")
    assert proc.returncode == 0
    assert "email" in proc.stdout
