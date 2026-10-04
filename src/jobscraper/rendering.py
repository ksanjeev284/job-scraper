"""Page rendering: headless Chromium (Playwright) with plain-requests fallback.

The browser is hardened for bot-walled career sites: the ``navigator.webdriver``
flag is hidden, a realistic viewport/locale is used, pages get two chances to
settle, and lazy-loaded descriptions are nudged into view with a scroll.
"""

from __future__ import annotations

import time

from bs4 import BeautifulSoup

from jobscraper.extract import looks_blocked
from jobscraper.http import (
    RobotsDisallowedError,
    cache_get,
    cache_put,
    get_ua,
    http_get,
    robots_allowed,
    robots_enabled,
)


def fetch_playwright(url: str, use_cache: bool = True
                     ) -> tuple[str | None, str]:
    """Render with headless Chromium; returns (title, html).

    Raises on failure so the caller can fall back to plain requests.
    Raises :class:`RobotsDisallowedError` when the opt-in robots.txt
    check is enabled and the host disallows the URL.
    """
    from playwright.sync_api import sync_playwright
    if robots_enabled() and not robots_allowed(url, get_ua()):
        raise RobotsDisallowedError(url)
    if use_cache:
        cached = cache_get(url)
        if cached is not None:
            return None, cached  # title unknown; caller re-derives
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=get_ua(),
            viewport={"width": 1366, "height": 900},
            locale="en-US",
            timezone_id="Asia/Kolkata",
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', "
            "{get: () => undefined});")
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            for _ in range(2):  # give JS boards two chances to settle
                try:
                    page.wait_for_load_state("networkidle", timeout=12000)
                    break
                except Exception:
                    time.sleep(3)
            try:  # nudge lazy-loaded job descriptions into view
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(2)
            except Exception:
                pass
            title = page.title()
            html_text = page.content()
        finally:
            browser.close()
    if looks_blocked(html_text):
        raise RuntimeError("bot challenge / block page detected")
    if use_cache:
        cache_put(url, html_text)
    return title, html_text


def fetch_requests(url: str, use_cache: bool = True
                   ) -> tuple[str | None, str]:
    """Plain HTTP fetch; returns (title, html)."""
    if use_cache:
        cached = cache_get(url)
        if cached is not None:
            return None, cached
    resp = http_get(url)
    if looks_blocked(resp.text):
        raise RuntimeError("bot challenge / block page detected")
    soup = BeautifulSoup(resp.text, "lxml")
    title = soup.title.string.strip() \
        if soup.title and soup.title.string else ""
    if use_cache:
        cache_put(url, resp.text)
    return title, resp.text
