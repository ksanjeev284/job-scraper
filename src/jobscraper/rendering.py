"""Page rendering: headless Chromium (Playwright) with plain-requests fallback.

The browser is hardened for bot-walled career sites: the ``navigator.webdriver``
flag is hidden, a realistic viewport/locale is used, pages get two chances to
settle, and lazy-loaded descriptions are nudged into view with a scroll.

:class:`BrowserPool` keeps one headless Chromium alive per worker thread and
hands every render a fresh browser *context* (fresh cookies/storage per
posting), so multi-posting runs stop paying a full browser launch per URL.
"""

from __future__ import annotations

import atexit
import os
import threading
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

# Context hardening shared by the pool and the legacy single-shot path.
_CONTEXT_KWARGS = {
    "viewport": {"width": 1366, "height": 900},
    "locale": "en-US",
    "timezone_id": "Asia/Kolkata",
}
_INIT_SCRIPT = ("Object.defineProperty(navigator, 'webdriver', "
                "{get: () => undefined});")


def _render_page(page, url: str, page_timeout: int) -> tuple[str, str]:
    """Drive one Playwright page through load, settle and scroll; (title, html)."""
    page.goto(url, wait_until="domcontentloaded", timeout=page_timeout)
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
    return page.title(), page.content()


def _new_hardened_context(browser):
    """Fresh browser context with the anti-bot hardening applied."""
    ctx = browser.new_context(user_agent=get_ua(), **_CONTEXT_KWARGS)
    ctx.add_init_script(_INIT_SCRIPT)
    return ctx


class BrowserPool:
    """Reuse headless Chromium across postings instead of launching per URL.

    Playwright's sync API binds a browser to the thread that created it,
    so the pool keeps one lazily-started browser per calling thread (one
    per pipeline worker at most) and hands every render a fresh browser
    context, which is closed afterwards: cookies, storage and state never
    leak from one posting into the next. Browsers idle longer than
    ``idle_timeout`` seconds are shut down and recreated on next use, and
    a dead thread's browser is never reused by a new thread.

    Usage::

        with BrowserPool() as pool:
            title, html = pool.render("https://example.com/job/1")

    or hand it to :func:`fetch_playwright` / :func:`set_default_pool`
    so the pipeline uses it automatically (see ``--browser-pool``).
    """

    def __init__(self, idle_timeout: float = 300.0,
                 page_timeout: int = 60000):
        self.idle_timeout = idle_timeout
        self.page_timeout = page_timeout
        self._lock = threading.Lock()
        self._browsers: dict[int, dict] = {}  # thread ident -> entry
        self.launches = 0
        self.renders = 0
        atexit.register(self.shutdown)

    def __enter__(self) -> BrowserPool:
        return self

    def __exit__(self, *exc) -> None:
        self.shutdown()

    def _close_entry_locked(self, entry: dict) -> None:
        try:
            entry["browser"].close()
        except Exception:
            pass
        try:
            entry["playwright"].stop()
        except Exception:
            pass

    def _entry_locked(self) -> dict:
        """Return this thread's live browser entry, starting/evicting as needed."""
        from playwright.sync_api import sync_playwright

        ident = threading.get_ident()
        thread = threading.current_thread()
        now = time.monotonic()
        entry = self._browsers.get(ident)
        if entry is not None and (
                entry["thread"] is not thread
                or now - entry["last_used"] > self.idle_timeout):
            # Never reuse a dead thread's browser; evict idle ones.
            self._close_entry_locked(entry)
            del self._browsers[ident]
            entry = None
        if entry is None:
            playwright = sync_playwright().start()
            browser = playwright.chromium.launch(headless=True)
            entry = {"playwright": playwright, "browser": browser,
                     "thread": thread, "last_used": now}
            self._browsers[ident] = entry
            self.launches += 1
        entry["last_used"] = now
        return entry

    def render(self, url: str) -> tuple[str, str]:
        """Render one URL with the pooled browser; returns (title, html).

        Raises on failure so the caller can fall back to plain requests.
        Raises :class:`RobotsDisallowedError` when the opt-in robots.txt
        check is enabled and the host disallows the URL.
        """
        if robots_enabled() and not robots_allowed(url, get_ua()):
            raise RobotsDisallowedError(url)
        with self._lock:
            entry = self._entry_locked()
            ctx = _new_hardened_context(entry["browser"])
            try:
                title, html = _render_page(ctx.new_page(), url,
                                          self.page_timeout)
            finally:
                ctx.close()
            entry["last_used"] = time.monotonic()
            self.renders += 1
        if looks_blocked(html):
            raise RuntimeError("bot challenge / block page detected")
        return title, html

    def stats(self) -> dict:
        """Pool counters: browser launches, renders served, live threads."""
        with self._lock:
            return {"launches": self.launches,
                    "renders": self.renders,
                    "threads": len(self._browsers)}

    def shutdown(self) -> None:
        """Close every pooled browser. Idempotent; safe to call twice."""
        with self._lock:
            for entry in self._browsers.values():
                self._close_entry_locked(entry)
            self._browsers.clear()


# Process-wide default pool, used when fetch_playwright gets pool=None.
_default_pool: BrowserPool | None = None
_default_lock = threading.Lock()


def set_default_pool(pool: BrowserPool | None) -> None:
    """Make ``pool`` the process-wide default for :func:`fetch_playwright`."""
    global _default_pool
    with _default_lock:
        _default_pool = pool


def get_default_pool() -> BrowserPool | None:
    """Return the process-wide default pool, if any."""
    with _default_lock:
        return _default_pool


def clear_default_pool() -> None:
    """Drop the process-wide default pool without shutting it down."""
    set_default_pool(None)


def browser_pool_from_env() -> bool:
    """True when ``JOBSCRAPER_BROWSER_POOL`` opts into pooled rendering."""
    return os.environ.get("JOBSCRAPER_BROWSER_POOL",
                          "").strip().lower() in ("1", "true", "yes", "on")


def fetch_playwright(url: str, use_cache: bool = True,
                     pool: BrowserPool | None = None) -> tuple[str | None, str]:
    """Render with headless Chromium; returns (title, html).

    ``pool`` reuses a pooled browser; when omitted, the process-wide
    default pool (if :func:`set_default_pool` was called) is used, and
    otherwise a fresh browser is launched for this one URL (legacy
    behavior). Raises on failure so the caller can fall back to plain
    requests. Raises :class:`RobotsDisallowedError` when the opt-in
    robots.txt check is enabled and the host disallows the URL.
    """
    pool = pool if pool is not None else get_default_pool()
    if robots_enabled() and not robots_allowed(url, get_ua()):
        raise RobotsDisallowedError(url)
    if use_cache:
        cached = cache_get(url)
        if cached is not None:
            return None, cached  # title unknown; caller re-derives
    if pool is not None:
        title, html_text = pool.render(url)
    else:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                ctx = _new_hardened_context(browser)
                try:
                    title, html_text = _render_page(ctx.new_page(), url,
                                                  60000)
                finally:
                    ctx.close()
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
