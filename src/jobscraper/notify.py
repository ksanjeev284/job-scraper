"""Outbound webhook notifications and export hooks.

After a scrape finishes, the ranked results can be POSTed as JSON to one or
more user-supplied webhook URLs. Three payload modes are supported:

- ``plain``   — the full job-scraper JSON payload, for custom receivers
  (e.g. a Google Sheets Apps Script ``doPost`` that appends rows, or a
  Notion API bridge that creates database entries).
- ``slack``   — a Block Kit message announcing the top postings.
- ``discord`` — a message with embeds for the top postings.
- ``pushover`` — a phone-push message via the Pushover API
  (https://pushover.net/api), for users who want alerts on their phone.
  Auth uses the application token and user key from
  ``JOBSCRAPER_PUSHOVER_TOKEN`` / ``JOBSCRAPER_PUSHOVER_USER``
  (or ``--pushover-token`` / ``--pushover-user``); the payload is
  form-encoded, not JSON, and is sent to the fixed Pushover endpoint,
  so ``--webhook-url`` is ignored in this mode. One message per run,
  linking to the top posting.
- ``telegram`` — a message via the Telegram Bot API
  (https://core.telegram.org/bots/api). Auth uses the bot token and
  chat id from ``JOBSCRAPER_TELEGRAM_TOKEN`` /
  ``JOBSCRAPER_TELEGRAM_CHAT_ID`` (or ``--telegram-token`` /
  ``--telegram-chat-id``); the payload is JSON and is sent to the fixed
  Bot API ``sendMessage`` endpoint, so ``--webhook-url`` is ignored in
  this mode. One message per run listing the ranked postings with their
  URLs (plain text, no formatting, so no Markdown escaping edge cases).
- ``ntfy`` — a push notification via ntfy (https://ntfy.sh/docs).
  The only credential is the topic name, resolved from
  ``JOBSCRAPER_NTFY_TOPIC`` (or ``--ntfy-topic``); no account is needed
  for public ntfy.sh, and a self-hosted server can be used via
  ``JOBSCRAPER_NTFY_SERVER`` (or ``--ntfy-server``), defaulting to
  https://ntfy.sh. An access token may be passed with
  ``JOBSCRAPER_NTFY_TOKEN`` (or ``--ntfy-token``) and is sent as a
  Bearer header. Priority defaults to ``default`` and can be one of
  low/default/high via ``JOBSCRAPER_NTFY_PRIORITY`` (or
  ``--ntfy-priority``). The message is plain text (one line per ranked
  posting), the notification title carries the summary, and tapping it
  opens the top-ranked posting via the ``Click`` action. One message per
  run; ``--webhook-url`` is ignored in this mode. The topic name is
  never logged in full — details carry only the server host.

Additionally, a fixed-endpoint ``email`` channel (not a webhook mode)
sends one SMTP digest per run: an HTML email with the ranked postings
as clickable job cards plus a plain-text fallback, using the settings
resolved by ``smtp_settings`` (``JOBSCRAPER_SMTP_*`` env vars or the
matching ``--smtp-*`` flags).

Webhook URLs often carry secrets in their path, so they are never printed
or logged in full: only the host is shown. Delivery failures are reported
to the caller; they never raise into the CLI itself.
"""

from __future__ import annotations

import html
import json
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Literal
from urllib.parse import urlencode, urlsplit

from jobscraper.models import Posting

WebhookMode = Literal["plain", "slack", "discord", "pushover", "telegram",
                      "ntfy"]

_TIMEOUT_SECONDS = 15
_MAX_SLACK_BLOCKS = 50
_MAX_DISCORD_EMBEDS = 10

_PUSHOVER_ENDPOINT = "https://api.pushover.net/1/messages.json"
_PUSHOVER_TOKEN_ENV = "JOBSCRAPER_PUSHOVER_TOKEN"
_PUSHOVER_USER_ENV = "JOBSCRAPER_PUSHOVER_USER"
# Pushover truncates messages over 1024 characters server-side; cap the
# body here so what is sent is what the user sees.
_MAX_PUSHOVER_MESSAGE = 1024

# The bot token is part of the URL path (Bot API convention), so the
# endpoint template keeps it out of the public constant; only the host
# ever appears in logs. The Bot API rejects messages over 4096 chars.
_TELEGRAM_ENDPOINT_TEMPLATE = "https://api.telegram.org/bot{token}/sendMessage"
_TELEGRAM_TOKEN_ENV = "JOBSCRAPER_TELEGRAM_TOKEN"
_TELEGRAM_CHAT_ID_ENV = "JOBSCRAPER_TELEGRAM_CHAT_ID"
_MAX_TELEGRAM_MESSAGE = 4096


def host_of(url: str) -> str:
    """Return just the host (and port) of a URL for safe logging.

    Webhook URLs embed secrets in their path, so the full URL must never
    appear in logs, errors, or reports.
    """
    try:
        return urlsplit(url).netloc or "?"
    except ValueError:
        return "?"


def webhook_urls_from_env() -> list[str]:
    """Read webhook URLs from JOBSCRAPER_WEBHOOK_URL (comma-separated)."""
    raw = os.environ.get("JOBSCRAPER_WEBHOOK_URL", "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def _ranked(posts: list[Posting]) -> list[Posting]:
    return sorted(posts,
                  key=lambda p: p.match.total if p.match else -1,
                  reverse=True)


def _display(post: Posting) -> tuple[str, str, str, str]:
    """Title, company, location, score as display strings."""
    score = str(post.match.total) if post.match else "-"
    return (post.title or "(no title)",
            post.company or "?",
            post.location or "?",
            score)


def build_payload(posts: list[Posting], mode: WebhookMode, *,
                  only_new: bool = False, top: int = 25) -> dict | None:
    """Build the webhook JSON payload for a set of scraped postings.

    Returns ``None`` when ``only_new`` is set and nothing is new, which
    the caller treats as "skip this webhook".
    """
    ranked = _ranked(posts)
    new_posts = [p for p in ranked if p.is_new]
    selected = (new_posts if only_new else ranked)[:top]
    if only_new and not new_posts:
        return None

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    summary = (f"{len(new_posts)} new" if new_posts
               else f"{len(ranked)} ranked")

    if mode == "plain":
        return {
            "tool": "job-scraper",
            "generated_at": stamp,
            "total": len(ranked),
            "new": len(new_posts),
            "postings": [p.to_dict() for p in selected],
        }

    if mode == "slack":
        blocks: list[dict] = [{
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f":briefcase: *Job scrape — {summary}* ({stamp})"},
        }]
        for post in selected[:_MAX_SLACK_BLOCKS - 1]:
            title, company, location, score = _display(post)
            title = title.replace("&", "&amp;").replace("<", "&lt;")
            title = title.replace(">", "&gt;")
            new = " :new:" if post.is_new else ""
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn",
                         "text": f"*<{post.url}|{title}>*{new}\n"
                                 f"{company} · {location} · score {score}"},
            })
        return {"blocks": blocks}

    if mode == "discord":
        embeds: list[dict] = []
        for post in selected[:_MAX_DISCORD_EMBEDS]:
            title, company, location, score = _display(post)
            description = (f"{company} · {location}\n"
                           f"Score: {score}"
                           f"{' · NEW' if post.is_new else ''}")
            embeds.append({
                "title": title[:256],
                "url": post.url,
                "description": description[:1024],
            })
        text = f"Job scrape — {summary} ({stamp})"
        if len(selected) > _MAX_DISCORD_EMBEDS:
            text += (f" · showing top {_MAX_DISCORD_EMBEDS} of "
                     f"{len(selected)}")
        return {"content": text, "embeds": embeds}

    raise ValueError(f"unknown webhook mode: {mode!r}")


def pushover_credentials(token: str | None = None,
                        user: str | None = None) -> tuple[str, str]:
    """Resolve the Pushover application token and user key.

    Explicit arguments win; each falls back to its environment variable
    (``JOBSCRAPER_PUSHOVER_TOKEN`` / ``JOBSCRAPER_PUSHOVER_USER``).
    Raises ``ValueError`` naming the missing source when either is absent.
    Values are never logged or echoed.
    """
    token = token or os.environ.get(_PUSHOVER_TOKEN_ENV, "").strip()
    user = user or os.environ.get(_PUSHOVER_USER_ENV, "").strip()
    missing = []
    if not token:
        missing.append(f"{_PUSHOVER_TOKEN_ENV} (or --pushover-token)")
    if not user:
        missing.append(f"{_PUSHOVER_USER_ENV} (or --pushover-user)")
    if missing:
        raise ValueError(
            "pushover mode needs credentials: missing "
            + ", ".join(missing))
    return token, user


def build_pushover_payload(posts: list[Posting], *,
                           token: str, user: str,
                           only_new: bool = False,
                           top: int = 25) -> dict | None:
    """Build the Pushover API fields for a set of scraped postings.

    One message per run (not one per posting) so watch-mode users get a
    single phone alert; the top posting is attached as the tappable URL.
    Returns ``None`` when ``only_new`` is set and nothing is new.
    """
    ranked = _ranked(posts)
    new_posts = [p for p in ranked if p.is_new]
    selected = (new_posts if only_new else ranked)[:top]
    if only_new and not new_posts:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    summary = (f"{len(new_posts)} new" if new_posts
               else f"{len(ranked)} ranked")
    lines = []
    for post in selected:
        title, company, location, score = _display(post)
        marker = " [NEW]" if post.is_new else ""
        lines.append(f"{title} - {company} - {location} - "
                     f"score {score}{marker}")
    message = "\n".join(lines)[:_MAX_PUSHOVER_MESSAGE]
    top_post = selected[0] if selected else None
    return {
        "token": token,
        "user": user,
        "title": f"Job scrape - {summary} ({stamp})",
        "message": message or "(no postings)",
        "url": top_post.url if top_post else "",
        "url_title": "Open top posting",
        "priority": 0,
    }


def _post(url: str, body: bytes, content_type: str) -> tuple[bool, str]:
    """POST ``body`` to ``url``; return ``(ok, detail)``.

    ``detail`` carries only the host and status, never the URL path or
    body, because both may embed secrets.
    """
    host = host_of(url)
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": content_type,
                 "User-Agent": "job-scraper/1.0"})
    try:
        with urllib.request.urlopen(request,
                                    timeout=_TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", 200)
    except urllib.error.HTTPError as exc:
        return False, f"{host}: HTTP {exc.code}"
    except OSError as exc:
        return False, f"{host}: {exc.__class__.__name__}: {exc}"
    if 200 <= status < 300:
        return True, f"{host}: delivered ({len(body)} bytes)"
    return False, f"{host}: HTTP {status}"


def send_webhook(url: str, payload: dict) -> tuple[bool, str]:
    """POST a JSON payload to one webhook URL.

    Returns ``(True, detail)`` on a 2xx response and ``(False, detail)``
    otherwise. ``detail`` carries only the host, never the URL path,
    because webhook URLs contain secrets. Raises ``ValueError`` for
    non-HTTP(S) URLs.
    """
    scheme = urlsplit(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"webhook URL must be http(s), got {scheme!r}")
    return _post(url, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                 "application/json")


def send_pushover(posts: list[Posting], *, token: str | None = None,
                  user: str | None = None, only_new: bool = False,
                  top: int = 25) -> tuple[bool | None, str]:
    """Send one Pushover phone-push message for a set of postings.

    Returns ``(ok, detail)``; ``ok`` is ``None`` when ``only_new`` is set
    and nothing is new (nothing worth pushing). Raises ``ValueError``
    when the Pushover credentials are missing. The token and user key
    never appear in results, logs, or error strings.
    """
    resolved_token, resolved_user = pushover_credentials(token, user)
    fields = build_pushover_payload(posts, token=resolved_token,
                                    user=resolved_user,
                                    only_new=only_new, top=top)
    if fields is None:
        return None, "skipped: no new postings"
    return _post(_PUSHOVER_ENDPOINT,
                 urlencode(fields).encode("utf-8"),
                 "application/x-www-form-urlencoded")


# --- Telegram bot-message channel -------------------------------------------

def telegram_credentials(token: str | None = None,
                         chat_id: str | None = None) -> tuple[str, str]:
    """Resolve the Telegram bot token and chat id.

    Explicit arguments win; each falls back to its environment variable
    (``JOBSCRAPER_TELEGRAM_TOKEN`` / ``JOBSCRAPER_TELEGRAM_CHAT_ID``).
    Raises ``ValueError`` naming the missing source when either is
    absent. Values are never logged or echoed.
    """
    token = token or os.environ.get(_TELEGRAM_TOKEN_ENV, "").strip()
    chat_id = chat_id or os.environ.get(_TELEGRAM_CHAT_ID_ENV, "").strip()
    missing = []
    if not token:
        missing.append(f"{_TELEGRAM_TOKEN_ENV} (or --telegram-token)")
    if not chat_id:
        missing.append(f"{_TELEGRAM_CHAT_ID_ENV} (or --telegram-chat-id)")
    if missing:
        raise ValueError(
            "telegram mode needs credentials: missing "
            + ", ".join(missing))
    return token, chat_id


def build_telegram_payload(posts: list[Posting], *, chat_id: str,
                           only_new: bool = False,
                           top: int = 25) -> dict | None:
    """Build the Telegram Bot API ``sendMessage`` fields.

    One message per run (not one per posting) so watch-mode users get a
    single alert. Each line is ``Title - company - location - score
    [NEW]`` followed by the posting URL, sent as plain text to avoid
    Markdown escaping edge cases; web-page previews are disabled.
    Returns ``None`` when ``only_new`` is set and nothing is new.
    """
    ranked = _ranked(posts)
    new_posts = [p for p in ranked if p.is_new]
    selected = (new_posts if only_new else ranked)[:top]
    if only_new and not new_posts:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    summary = (f"{len(new_posts)} new" if new_posts
               else f"{len(ranked)} ranked")
    lines = [f"Job scrape - {summary} ({stamp})"]
    for post in selected:
        title, company, location, score = _display(post)
        marker = " [NEW]" if post.is_new else ""
        lines.append(f"{title} - {company} - {location} - "
                     f"score {score}{marker}")
        lines.append(post.url)
    text = "\n".join(lines)[:_MAX_TELEGRAM_MESSAGE]
    return {
        "chat_id": chat_id,
        "text": text or "(no postings)",
        "disable_web_page_preview": True,
    }


def _post_telegram(url: str, payload: dict) -> tuple[bool, str]:
    """POST JSON to a Telegram Bot API endpoint.

    Unlike generic webhooks, the Bot API returns HTTP 200 with an
    ``{"ok": false, ...}`` body for most API-level failures (bad chat
    id, revoked token, ...), so the response body is checked too.
    ``detail`` carries only the host and the API description, never the
    URL (which embeds the bot token) or the message body.
    """
    host = host_of(url)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "User-Agent": "job-scraper/1.0"})
    try:
        with urllib.request.urlopen(request,
                                    timeout=_TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return False, f"{host}: HTTP {exc.code}"
    except OSError as exc:
        return False, f"{host}: {exc.__class__.__name__}: {exc}"
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    if data.get("ok") is True:
        return True, f"{host}: delivered ({len(body)} bytes)"
    description = data.get("description") or "unknown error"
    return False, f"{host}: Bot API error: {description}"


def send_telegram(posts: list[Posting], *, token: str | None = None,
                  chat_id: str | None = None, only_new: bool = False,
                  top: int = 25) -> tuple[bool | None, str]:
    """Send one Telegram bot message for a set of postings.

    Returns ``(ok, detail)``; ``ok`` is ``None`` when ``only_new`` is set
    and nothing is new. Raises ``ValueError`` when the Telegram
    credentials are missing. The bot token and chat id never appear in
    results, logs, or error strings.
    """
    resolved_token, resolved_chat_id = telegram_credentials(token, chat_id)
    fields = build_telegram_payload(posts, chat_id=resolved_chat_id,
                                    only_new=only_new, top=top)
    if fields is None:
        return None, "skipped: no new postings"
    return _post_telegram(
        _TELEGRAM_ENDPOINT_TEMPLATE.format(token=resolved_token), fields)


# --- ntfy push-notification channel ---------------------------------------------

# One plain-text push message per run via ntfy (https://ntfy.sh/docs):
# a POST to {server}/{topic}. The topic name IS the credential — treat it
# like one and never log it in full; only the server host appears in logs.
# Message bodies are capped at 4096 bytes (ntfy.sh's default per-message
# limit), the notification title carries the run summary, and the Click
# action opens the top-ranked posting.
_NTFY_SERVER_ENV = "JOBSCRAPER_NTFY_SERVER"
_NTFY_TOPIC_ENV = "JOBSCRAPER_NTFY_TOPIC"
_NTFY_TOKEN_ENV = "JOBSCRAPER_NTFY_TOKEN"
_NTFY_PRIORITY_ENV = "JOBSCRAPER_NTFY_PRIORITY"
_NTFY_DEFAULT_SERVER = "https://ntfy.sh"
_NTFY_PRIORITIES = ("low", "default", "high")
_MAX_NTFY_MESSAGE = 4096


def ntfy_endpoint(server: str | None, topic: str | None) -> str:
    """Build the ntfy publish URL from a server and topic.

    Defaults to https://ntfy.sh when no server is given; the topic is
    mandatory. Raises ``ValueError`` when the server scheme is not
    http(s) or the topic is missing/empty. The returned URL is only
    ever passed to the HTTP layer — never to logs.
    """
    server = (server or "").strip() or _NTFY_DEFAULT_SERVER
    scheme = urlsplit(server).scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(
            f"ntfy server must be an http(s) URL, got {scheme!r}")
    topic = (topic or "").strip().strip("/")
    if not topic or any(ch in topic for ch in " ?#"):
        raise ValueError(
            "ntfy mode needs a topic: missing "
            f"{_NTFY_TOPIC_ENV} (or --ntfy-topic)")
    return f"{server.rstrip('/')}/{topic}"


def ntfy_credentials(server: str | None = None,
                     topic: str | None = None,
                     token: str | None = None,
                     priority: str | None = None,
                     ) -> tuple[str, str, str | None, str]:
    """Resolve the ntfy server, topic, optional access token, and priority.

    Explicit arguments win; each falls back to its environment variable
    (``JOBSCRAPER_NTFY_SERVER`` / ``JOBSCRAPER_NTFY_TOPIC`` /
    ``JOBSCRAPER_NTFY_TOKEN`` / ``JOBSCRAPER_NTFY_PRIORITY``).
    The topic is mandatory and raises ``ValueError`` when missing; the
    priority must be one of ``low``/``default``/``high``. The topic and
    token are never logged or echoed.
    """
    server = (server or os.environ.get(_NTFY_SERVER_ENV, "").strip()
              or _NTFY_DEFAULT_SERVER)
    topic = (topic or os.environ.get(_NTFY_TOPIC_ENV, "").strip())
    token = (token or os.environ.get(_NTFY_TOKEN_ENV, "").strip()) or None
    resolved_priority = (
        (priority or os.environ.get(_NTFY_PRIORITY_ENV, "").strip())
        or "default")
    if resolved_priority not in _NTFY_PRIORITIES:
        raise ValueError(
            "ntfy priority must be one of "
            f"{'/'.join(_NTFY_PRIORITIES)}, got {resolved_priority!r}")
    endpoint = ntfy_endpoint(server, topic)
    return endpoint, resolved_priority, token, topic


def build_ntfy_payload(posts: list[Posting], *,
                       only_new: bool = False,
                       top: int = 25) -> dict | None:
    """Build the ntfy publish fields for a set of postings.

    One notification per run: the title carries the run summary and the
    message body lists the ranked postings, one per line, with their
    scores and a NEW marker where watch mode flagged them. ``click``
    opens the top-ranked posting; ``tags`` mark new-posting runs so
    they stand out in the ntfy app. Returns ``None`` when ``only_new``
    is set and nothing is new.
    """
    ranked = _ranked(posts)
    new_posts = [p for p in ranked if p.is_new]
    selected = (new_posts if only_new else ranked)[:top]
    if only_new and not new_posts:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    summary = (f"{len(new_posts)} new" if new_posts
               else f"{len(ranked)} ranked")
    title = f"Job scrape — {summary} ({stamp})"
    lines = []
    for post in selected:
        post_title, company, location, score = _display(post)
        marker = " [NEW]" if post.is_new else ""
        lines.append(f"{post_title} — {company} · {location} · "
                     f"score {score}{marker}")
    message = "\n".join(lines)[:_MAX_NTFY_MESSAGE] or "(no postings)"
    tags = ["briefcase"] + (["bell"] if new_posts else [])
    return {
        "title": title,
        "message": message,
        "click": selected[0].url if selected else "",
        "tags": tags,
    }


def _post_ntfy(endpoint: str, fields: dict,
               token: str | None) -> tuple[bool, str]:
    """POST a plain-text message to an ntfy publish endpoint.

    Notification metadata travels in headers (``Title``, ``Priority``,
    ``Click``, ``Tags``); the access token, when set, is sent as an
    ``Authorization: Bearer`` header. ``detail`` carries only the
    server host — never the endpoint path, which contains the topic —
    and never the message body or token.
    """
    host = host_of(endpoint)
    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "User-Agent": "job-scraper/1.0",
        "Title": fields["title"].encode("utf-8", errors="replace"),
        "Priority": fields["priority"].encode("ascii", errors="replace"),
        "Tags": ",".join(fields["tags"]).encode("ascii",
                                                errors="replace"),
    }
    if fields.get("click"):
        headers["Click"] = fields["click"].encode("utf-8",
                                                  errors="replace")
    if token:
        headers["Authorization"] = f"Bearer {token}".encode(
            "utf-8", errors="replace")
    request = urllib.request.Request(
        endpoint, data=fields["message"].encode("utf-8"),
        method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request,
                                    timeout=_TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", 200)
    except urllib.error.HTTPError as exc:
        return False, f"{host}: HTTP {exc.code}"
    except OSError as exc:
        return False, f"{host}: {exc.__class__.__name__}: {exc}"
    if 200 <= status < 300:
        return True, f"{host}: delivered ({len(fields['message'])} chars)"
    return False, f"{host}: HTTP {status}"


def send_ntfy(posts: list[Posting], *, server: str | None = None,
              topic: str | None = None, token: str | None = None,
              priority: str | None = None, only_new: bool = False,
              top: int = 25) -> tuple[bool | None, str]:
    """Send one ntfy push notification for a set of postings.

    Returns ``(ok, detail)``; ``ok`` is ``None`` when ``only_new`` is set
    and nothing is new (nothing worth pushing). Raises ``ValueError``
    when the topic is missing or the priority/server is invalid. The
    topic and token never appear in results, logs, or error strings.
    """
    endpoint, resolved_priority, resolved_token, _topic = ntfy_credentials(
        server=server, topic=topic, token=token, priority=priority)
    fields = build_ntfy_payload(posts, only_new=only_new, top=top)
    if fields is None:
        return None, "skipped: no new postings"
    fields["priority"] = resolved_priority
    return _post_ntfy(endpoint, fields, resolved_token)


# --- SMTP email digest channel ------------------------------------------------

# One HTML email per run with the ranked postings as clickable job cards,
# so a cron/watch-mode schedule lands the day's matches in the inbox.
# Auth is plain SMTP with STARTTLS (the Gmail/Outlook app-password
# pattern); credentials come from the JOBSCRAPER_SMTP_* env vars or the
# matching --smtp-* flags, and are never logged.
_SMTP_HOST_ENV = "JOBSCRAPER_SMTP_HOST"
_SMTP_PORT_ENV = "JOBSCRAPER_SMTP_PORT"
_SMTP_USER_ENV = "JOBSCRAPER_SMTP_USER"
_SMTP_PASSWORD_ENV = "JOBSCRAPER_SMTP_PASSWORD"
_SMTP_FROM_ENV = "JOBSCRAPER_SMTP_FROM"
_SMTP_TO_ENV = "JOBSCRAPER_SMTP_TO"
_DEFAULT_SMTP_PORT = 587
_SMTP_TIMEOUT_SECONDS = 30


@dataclass
class SMTPSettings:
    """Resolved SMTP connection settings for the email digest channel."""

    host: str
    port: int = _DEFAULT_SMTP_PORT
    user: str = ""
    password: str = ""
    from_addr: str = ""
    to_addr: str = ""
    use_tls: bool = True


def smtp_settings(host: str | None = None,
                  port: int | None = None,
                  user: str | None = None,
                  password: str | None = None,
                  from_addr: str | None = None,
                  to_addr: str | None = None,
                  use_tls: bool = True) -> SMTPSettings:
    """Resolve the SMTP settings for the email digest channel.

    Explicit arguments win; each falls back to its ``JOBSCRAPER_SMTP_*``
    environment variable. ``port`` defaults to 587; ``from_addr``
    defaults to the SMTP username. Auth is skipped entirely when no
    username is set (internal relays on localhost-style servers).
    Raises ``ValueError`` naming the missing source when the host,
    recipient, or the password for a given username is absent. The
    password never appears in logs or error strings.
    """
    resolved_host = (host or os.environ.get(_SMTP_HOST_ENV, "")).strip()
    raw_port = (port if port is not None
                else os.environ.get(_SMTP_PORT_ENV, "").strip())
    resolved_user = (user or os.environ.get(_SMTP_USER_ENV, "")).strip()
    resolved_password = (password
                         or os.environ.get(_SMTP_PASSWORD_ENV, "")).strip()
    resolved_from = (from_addr
                     or os.environ.get(_SMTP_FROM_ENV, "")).strip()
    resolved_to = (to_addr or os.environ.get(_SMTP_TO_ENV, "")).strip()
    missing = []
    if not resolved_host:
        missing.append(f"{_SMTP_HOST_ENV} (or --smtp-host)")
    if not resolved_to:
        missing.append(f"{_SMTP_TO_ENV} (or --smtp-to)")
    if resolved_user and not resolved_password:
        missing.append(f"{_SMTP_PASSWORD_ENV} (or --smtp-password)")
    if missing:
        raise ValueError(
            "email mode needs SMTP settings: missing "
            + ", ".join(missing))
    try:
        resolved_port = int(raw_port) if raw_port else _DEFAULT_SMTP_PORT
    except (TypeError, ValueError):
        raise ValueError(
            f"invalid SMTP port: {raw_port!r} "
            f"(from --smtp-port or {_SMTP_PORT_ENV})") from None
    return SMTPSettings(
        host=resolved_host,
        port=resolved_port,
        user=resolved_user,
        password=resolved_password,
        from_addr=resolved_from or resolved_user,
        to_addr=resolved_to,
        use_tls=use_tls,
    )


def _selected(posts: list[Posting], only_new: bool,
              top: int) -> tuple[list[Posting], int]:
    """Ranked selection shared by the email builder and sender."""
    ranked = _ranked(posts)
    new_posts = [p for p in ranked if p.is_new]
    selected = (new_posts if only_new else ranked)[:top]
    return selected, len(new_posts)


def _email_plain_lines(posts: list[Posting]) -> list[str]:
    lines = []
    for post in posts:
        title, company, location, score = _display(post)
        marker = " [NEW]" if post.is_new else ""
        lines.append(f"{title} - {company} - {location} - "
                     f"score {score}{marker}")
        lines.append(post.url)
    return lines


def _email_card(post: Posting) -> str:
    """One HTML job card; every field is escaped against posting text."""
    title, company, location, score = _display(post)
    title = html.escape(title, quote=True)
    company = html.escape(company, quote=True)
    location = html.escape(location, quote=True)
    url = html.escape(post.url, quote=True)
    meta = f"{company} &middot; {location} &middot; score {score}"
    seniority = (post.seniority or "").strip()
    if seniority and seniority != "unknown":
        meta += f" &middot; {html.escape(seniority, quote=True)}"
    if post.salary_hits:
        meta += " &middot; " + ", ".join(
            html.escape(hit, quote=True) for hit in post.salary_hits)
    new = (" <span style=\"background:#16a34a;color:#fff;"
           "padding:1px 6px;border-radius:3px;font-size:11px;\">NEW</span>"
           if post.is_new else "")
    return (f'<tr><td style="padding:10px;border:1px solid #ddd;">'
            f'<a href="{url}" style="font-size:15px;">{title}</a>{new}<br>'
            f'<span style="color:#555;font-size:13px;">{meta}</span></td></tr>')


def build_email_message(posts: list[Posting], *, from_addr: str,
                        to_addr: str, only_new: bool = False,
                        top: int = 25) -> EmailMessage | None:
    """Build the per-run HTML digest email.

    A multipart message: a plain-text part (same one-line-per-posting
    shape as the Pushover/Telegram channels) plus an HTML part with one
    clickable job card per posting — title links to the posting, with
    company, location, score, seniority, salary hits, and a NEW badge.
    Returns ``None`` when ``only_new`` is set and nothing is new, which
    the caller treats as "skip this email".
    """
    selected, new_count = _selected(posts, only_new, top)
    if only_new and not selected:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    summary = (f"{new_count} new" if new_count
               else f"{len(_ranked(posts))} ranked")
    message = EmailMessage()
    message["Subject"] = f"Job scrape: {summary} ({stamp})"
    message["From"] = from_addr
    message["To"] = to_addr
    lines = _email_plain_lines(selected) or ["(no postings)"]
    message.set_content("\n".join(lines))
    cards = "\n".join(_email_card(post) for post in selected)
    if not cards:
        cards = ('<tr><td style="padding:10px;">(no postings)</td></tr>')
    body = (f"<html><body style=\"font-family:sans-serif;\">"
            f"<h2>Job scrape &mdash; {html.escape(summary)} "
            f"({html.escape(stamp)})</h2>"
            f'<table style="border-collapse:collapse;width:100%;">'
            f"{cards}</table>"
            f'<p style="color:#888;font-size:12px;">Generated by '
            f"job-scraper.</p></body></html>")
    message.add_alternative(body, subtype="html")
    return message


def send_email_digest(posts: list[Posting], *,
                      settings: SMTPSettings,
                      only_new: bool = False,
                      top: int = 25) -> tuple[bool | None, str]:
    """Send one SMTP digest email for a set of postings.

    Connects with STARTTLS (skippable via ``settings.use_tls`` for
    internal relays), logs in only when a username is set, and sends a
    single message to the configured recipient. Returns ``(ok,
    detail)``; ``ok`` is ``None`` when ``only_new`` is set and nothing is
    new. Raises ``ValueError`` from :func:`smtp_settings` when settings
    are incomplete — callers resolve credentials first. The password
    and recipient never appear in results, logs, or error strings.
    """
    selected, _ = _selected(posts, only_new, top)
    if only_new and not selected:
        return None, "skipped: no new postings"
    message = build_email_message(posts, from_addr=settings.from_addr,
                                  to_addr=settings.to_addr,
                                  only_new=only_new, top=top)
    host = settings.host
    try:
        with smtplib.SMTP(host, settings.port,
                          timeout=_SMTP_TIMEOUT_SECONDS) as server:
            server.ehlo()
            if settings.use_tls:
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            if settings.user:
                server.login(settings.user, settings.password)
            server.send_message(message)
    except smtplib.SMTPException as exc:
        return False, f"{host}: SMTP error: {exc}"
    except OSError as exc:
        return False, f"{host}: {exc.__class__.__name__}: {exc}"
    return True, f"{host}: email digest delivered"


def deliver(posts: list[Posting], urls: list[str], *,
            mode: WebhookMode = "plain",
            only_new: bool = False,
            top: int = 25) -> list[tuple[str, bool | None, str]]:
    """Deliver one payload to each webhook URL.

    Builds the payload once, then POSTs it per URL. Returns one
    ``(host, ok, detail)`` tuple per URL, where ``ok`` is ``None`` when
    the payload builder decided there was nothing worth sending
    (``only_new`` with zero new postings).
    """
    try:
        payload = build_payload(posts, mode, only_new=only_new, top=top)
    except ValueError as exc:
        return [(host_of(url), False, str(exc)) for url in urls]
    if payload is None:
        return [(host_of(url), None, "skipped: no new postings")
                for url in urls]
    results = []
    for url in urls:
        try:
            ok, detail = send_webhook(url, payload)
        except ValueError as exc:
            ok, detail = False, str(exc)
        results.append((host_of(url), ok, detail))
    return results
