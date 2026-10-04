"""Outbound webhook notifications and export hooks.

After a scrape finishes, the ranked results can be POSTed as JSON to one or
more user-supplied webhook URLs. Three payload modes are supported:

- ``plain``   — the full job-scraper JSON payload, for custom receivers
  (e.g. a Google Sheets Apps Script ``doPost`` that appends rows, or a
  Notion API bridge that creates database entries).
- ``slack``   — a Block Kit message announcing the top postings.
- ``discord`` — a message with embeds for the top postings.

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
from urllib.parse import urlsplit

from jobscraper.models import Posting

WebhookMode = Literal["plain", "slack", "discord"]

_TIMEOUT_SECONDS = 15
_MAX_SLACK_BLOCKS = 50
_MAX_DISCORD_EMBEDS = 10


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


def send_webhook(url: str, payload: dict) -> tuple[bool, str]:
    """POST a payload to one webhook URL.

    Returns ``(True, detail)`` on a 2xx response and ``(False, detail)``
    otherwise. ``detail`` carries only the host, never the URL path,
    because webhook URLs contain secrets. Raises ``ValueError`` for
    non-HTTP(S) URLs.
    """
    scheme = urlsplit(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"webhook URL must be http(s), got {scheme!r}")
    host = host_of(url)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
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
