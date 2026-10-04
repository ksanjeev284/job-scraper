"""HTTP plumbing: user-agent rotation, polite rate limiting, retries, cache.

Every network call in jobscraper goes through :func:`http_get`, which
applies per-domain rate limiting with jitter, rotates user agents, retries
transient failures with exponential backoff, and fails fast on permanent
ones (401/403/404).
"""

from __future__ import annotations

import hashlib
import os
import random
import threading
import time
from urllib.parse import urlparse

import requests

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 "
    "Firefox/128.0",
]

CACHE_DIR = os.path.expanduser("~/.cache/jobscraper")
CACHE_TTL = 24 * 3600  # seconds

_domain_last_hit: dict[str, float] = {}
_domain_lock = threading.Lock()


def get_ua() -> str:
    """Return a random user agent from the pool."""
    return random.choice(UA_POOL)


def polite_wait(url: str, base: float = 1.5) -> None:
    """Per-domain rate limit with jitter. Thread-safe."""
    host = urlparse(url).netloc
    with _domain_lock:
        now = time.time()
        last = _domain_last_hit.get(host, 0.0)
        wait = base + random.uniform(0, 1.5) - (now - last)
        _domain_last_hit[host] = now + max(0.0, wait)
    if wait > 0:
        time.sleep(wait)


def http_get(url: str, timeout: int = 30,
             max_retries: int = 3) -> requests.Response:
    """GET with retries, backoff and UA rotation.

    Retries 429/5xx and network errors; fails fast on 401/403/404.
    Raises the last error when retries are exhausted.
    """
    last_err: Exception | None = None
    for attempt in range(max_retries):
        polite_wait(url)
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": get_ua(),
                         "Accept-Language": "en-US,en;q=0.9"},
                timeout=timeout,
            )
            if resp.status_code in (401, 403, 404):
                raise requests.HTTPError(
                    f"HTTP {resp.status_code} (no retry)")
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(
                    f"HTTP {resp.status_code} (retryable)")
            resp.raise_for_status()
            return resp
        except requests.HTTPError as exc:
            if "no retry" in str(exc):
                raise
            last_err = exc
        except Exception as exc:  # network-level: DNS, timeouts, resets
            last_err = exc
        time.sleep((2 ** attempt) + random.uniform(0, 1))
    assert last_err is not None
    raise last_err


def _cache_path(url: str) -> str:
    key = hashlib.sha256(url.encode()).hexdigest()
    return os.path.join(CACHE_DIR, key + ".html")


def cache_get(url: str, ttl: int = CACHE_TTL) -> str | None:
    """Return cached page HTML, or None on miss/expiry."""
    try:
        path = _cache_path(url)
        if os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
            with open(path, encoding="utf-8") as fh:
                return fh.read()
    except OSError:
        pass
    return None


def cache_put(url: str, html: str) -> None:
    """Store page HTML in the local cache (best effort)."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_cache_path(url), "w", encoding="utf-8") as fh:
            fh.write(html[:2_000_000])
    except OSError:
        pass
