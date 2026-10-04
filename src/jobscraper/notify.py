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

Webhook URLs often carry secrets in their path, so they are never printed
or logged in full: only the host is shown. Delivery failures are reported
to the caller; they never raise into the CLI itself.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlencode, urlsplit

from jobscraper.models import Posting

WebhookMode = Literal["plain", "slack", "discord", "pushover", "telegram"]

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
