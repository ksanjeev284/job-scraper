"""Tests for the headless-browser pool (BrowserPool) in rendering.py.

Playwright is not installed in the test environment, so these tests
inject a fake ``playwright.sync_api`` module that records browser
launches and serves canned pages.
"""

from __future__ import annotations

import sys
import threading
import types

import pytest

from jobscraper import rendering
from jobscraper.rendering import (
    BrowserPool,
    clear_default_pool,
    fetch_playwright,
    get_default_pool,
    set_default_pool,
)

GOOD_HTML = ("<html><head><title>Senior Engineer</title></head>"
             "<body><p>" + "job description text. " * 200 + "</p></body></html>")
BLOCK_HTML = ("<html><head><title>Blocked</title></head>"
              "<body><p>Please verify you are human to continue.</p>"
              "</body></html>")


class _FakePage:
    def __init__(self, tracker, url_html):
        self._tracker = tracker
        self._url_html = url_html
        self.url = None

    def goto(self, url, wait_until=None, timeout=None):
        self.url = url

    def wait_for_load_state(self, state, timeout=None):
        return None

    def evaluate(self, js):
        return None

    def title(self):
        html = self._url_html.get(self.url, ("Senior Engineer", GOOD_HTML))[0]
        return html

    def content(self):
        return self._url_html.get(self.url,
                                  ("Senior Engineer", GOOD_HTML))[1]


class _FakeContext:
    def __init__(self, browser):
        self._browser = browser
        self.closed = False
        self.init_script = None

    def add_init_script(self, js):
        self.init_script = js

    def new_page(self):
        self._browser.pages_created += 1
        return _FakePage(self._browser.tracker, self._browser.tracker["pages"])

    def close(self):
        self.closed = True


class _FakeBrowser:
    def __init__(self, tracker):
        self.tracker = tracker
        self.contexts = []
        self.pages_created = 0
        self.closed = False

    def new_context(self, **kwargs):
        ctx = _FakeContext(self)
        self.contexts.append(ctx)
        return ctx

    def close(self):
        self.closed = True
        self.tracker["closed"] += 1


class _FakeChromium:
    def __init__(self, tracker):
        self._tracker = tracker

    def launch(self, headless=True):
        self._tracker["launches"] += 1
        browser = _FakeBrowser(self._tracker)
        self._tracker["browsers"].append(browser)
        return browser


class _FakePlaywright:
    def __init__(self, tracker):
        self._tracker = tracker
        self.chromium = _FakeChromium(tracker)
        self.stopped = False

    def stop(self):
        self.stopped = True


class _FakeSyncPlaywrightCM:
    """Mimics playwright's sync_playwright() context manager / .start()."""

    def __init__(self, tracker):
        self._tracker = tracker
        self._pw = None

    def start(self):
        self._pw = _FakePlaywright(self._tracker)
        return self._pw

    def stop(self):
        if self._pw is not None:
            self._pw.stop()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()
        return False


@pytest.fixture()
def tracker():
    return {"launches": 0, "closed": 0, "browsers": [],
            "pages": {}}  # url -> (title, html)


@pytest.fixture()
def fake_playwright(monkeypatch, tracker):
    module = types.ModuleType("playwright.sync_api")
    module.sync_playwright = lambda: _FakeSyncPlaywrightCM(tracker)  # noqa: E731
    package = types.ModuleType("playwright")
    package.sync_api = module
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", module)
    return tracker


def _url(i: int) -> str:
    return f"https://example.com/jobs/{i}?x={i}"


def test_pool_starts_browser_lazily(fake_playwright):
    pool = BrowserPool()
    try:
        assert fake_playwright["launches"] == 0
    finally:
        pool.shutdown()


def test_pool_reuses_browser_across_renders(fake_playwright):
    pool = BrowserPool()
    try:
        t1, h1 = pool.render(_url(1))
        t2, h2 = pool.render(_url(2))
        assert fake_playwright["launches"] == 1
        assert pool.stats() == {"launches": 1, "renders": 2, "threads": 1}
        assert t1 == "Senior Engineer"
        assert h1 == h2 == GOOD_HTML
    finally:
        pool.shutdown()


def test_pool_uses_fresh_closed_context_per_render(fake_playwright):
    pool = BrowserPool()
    try:
        pool.render(_url(1))
        pool.render(_url(2))
        browser = fake_playwright["browsers"][0]
        assert len(browser.contexts) == 2
        assert browser.contexts[0] is not browser.contexts[1]
        assert all(c.closed for c in browser.contexts)
        # Anti-bot hardening applied on every context.
        assert all(c.init_script for c in browser.contexts)
    finally:
        pool.shutdown()


def test_pool_evits_idle_browser(fake_playwright):
    pool = BrowserPool(idle_timeout=60.0)
    try:
        pool.render(_url(1))
        assert fake_playwright["launches"] == 1
        ident = threading.get_ident()
        # Simulate a long-idle browser without sleeping.
        pool._browsers[ident]["last_used"] -= 3600.0
        pool.render(_url(2))
        assert fake_playwright["launches"] == 2
        assert fake_playwright["browsers"][0].closed
    finally:
        pool.shutdown()


def test_pool_isolates_browsers_per_thread(fake_playwright):
    pool = BrowserPool()
    errors = []

    def work(i):
        try:
            pool.render(_url(i))
        except Exception as exc:  # pragma: no cover - test failure path
            errors.append(exc)

    try:
        threads = [threading.Thread(target=work, args=(i,)) for i in (1, 2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert fake_playwright["launches"] == 2
        assert pool.stats()["threads"] == 2
    finally:
        pool.shutdown()


def test_pool_block_page_raises(fake_playwright):
    fake_playwright["pages"][_url(9)] = ("Blocked", BLOCK_HTML)
    pool = BrowserPool()
    try:
        with pytest.raises(RuntimeError, match="bot challenge"):
            pool.render(_url(9))
    finally:
        pool.shutdown()


def test_pool_shutdown_idempotent(fake_playwright):
    pool = BrowserPool()
    pool.render(_url(1))
    pool.shutdown()
    pool.shutdown()  # must not raise
    assert fake_playwright["closed"] == 1
    # A render after shutdown restarts cleanly.
    pool.render(_url(2))
    assert fake_playwright["launches"] == 2
    pool.shutdown()


def test_pool_context_manager_shuts_down(fake_playwright):
    with BrowserPool() as pool:
        pool.render(_url(1))
    assert fake_playwright["closed"] == 1


def test_fetch_playwright_uses_explicit_pool(fake_playwright):
    pool = BrowserPool()
    try:
        title, html = fetch_playwright(_url(1), use_cache=False, pool=pool)
        assert title == "Senior Engineer"
        assert fake_playwright["launches"] == 1
        fetch_playwright(_url(2), use_cache=False, pool=pool)
        assert fake_playwright["launches"] == 1
    finally:
        pool.shutdown()


def test_fetch_playwright_uses_default_pool(fake_playwright):
    pool = BrowserPool()
    set_default_pool(pool)
    try:
        assert get_default_pool() is pool
        fetch_playwright(_url(1), use_cache=False)
        assert fake_playwright["launches"] == 1
        # Explicit pool=None still picks up the default.
        fetch_playwright(_url(2), use_cache=False, pool=None)
        assert fake_playwright["launches"] == 1
    finally:
        clear_default_pool()
        pool.shutdown()
    assert get_default_pool() is None


def test_fetch_playwright_legacy_path_launches_per_call(fake_playwright):
    # No pool anywhere: one browser launch per call, as before.
    fetch_playwright(_url(1), use_cache=False)
    fetch_playwright(_url(2), use_cache=False)
    assert fake_playwright["launches"] == 2
    assert fake_playwright["closed"] == 2


def test_pool_serves_per_url_pages(fake_playwright):
    fake_playwright["pages"][_url(7)] = (
        "DevOps Engineer",
        "<html><head><title>DevOps Engineer</title></head><body><p>"
        + "devops text. " * 200 + "</p></body></html>")
    pool = BrowserPool()
    try:
        title, _ = pool.render(_url(7))
        assert title == "DevOps Engineer"
    finally:
        pool.shutdown()


def test_rendering_module_importable_without_playwright(monkeypatch):
    # Importing and instantiating the pool must not require Playwright.
    monkeypatch.delitem(sys.modules, "playwright", raising=False)
    monkeypatch.delitem(sys.modules, "playwright.sync_api", raising=False)
    import importlib
    importlib.reload(rendering)
    pool = rendering.BrowserPool()
    assert pool.stats() == {"launches": 0, "renders": 0, "threads": 0}
    pool.shutdown()
