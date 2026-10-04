"""HTTP plumbing: user-agent rotation, polite rate limiting, retries, cache.

Every network call in jobscraper goes through :func:`http_get`, which
applies per-domain rate limiting with jitter, rotates user agents, retries
transient failures with exponential backoff, and fails fast on permanent
ones (401/403/404).

Optional proxy rotation: :func:`configure_proxies` installs a pool of proxy
URLs (e.g. ``http://user:pass@host:8080``). ``http_get`` then picks a proxy
round-robin per request. Proxies that fail :data:`PROXY_MAX_FAILURES` times
in a row are parked for :data:`PROXY_PARK_SECONDS` seconds and re-enter the
pool automatically afterwards; when every proxy is parked, requests go out
directly rather than fail.

Optional robots.txt honoring: :func:`configure_robots` turns on an
opt-in check — :func:`http_get` then raises
:class:`RobotsDisallowedError` for URLs a host's robots.txt disallows
(robots files are cached per host for a day; a missing or unreachable
robots.txt means "allowed").
"""

from __future__ import annotations

import hashlib
import os
import random
import threading
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

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

#: Proxy URLs parked after this many consecutive failures re-enter the pool
#: after PROXY_PARK_SECONDS. Tunable module constants for power users.
PROXY_MAX_FAILURES = 3
PROXY_PARK_SECONDS = 300.0

#: How long a fetched robots.txt stays in the per-host cache.
ROBOTS_TTL = 24 * 3600


class RobotsDisallowedError(Exception):
    """Raised when the opt-in robots.txt check blocks a fetch.

    Carries ``url`` and the host's robots.txt path for diagnostics.
    """

    def __init__(self, url: str):
        host = urlparse(url).netloc
        super().__init__(
            f"{url} is disallowed by {host}/robots.txt "
            "(re-run without --respect-robots to skip this check)")
        self.url = url


_robots_enabled = False
_robots_cache: dict[str, tuple[RobotFileParser, float]] = {}
_robots_lock = threading.Lock()


def configure_robots(enabled: bool) -> None:
    """Enable or disable honoring robots.txt for every fetch.

    Off by default. When enabled, :func:`http_get` refuses URLs the
    target host's robots.txt disallows (raising
    :class:`RobotsDisallowedError`); hosts with no reachable robots.txt
    are treated as fully allowed.
    """
    global _robots_enabled
    with _robots_lock:
        _robots_enabled = bool(enabled)


def robots_enabled() -> bool:
    """True when robots.txt is currently being honored."""
    with _robots_lock:
        return _robots_enabled


def reset_robots() -> None:
    """Disable robots.txt checks and drop the per-host robots cache."""
    configure_robots(False)
    with _robots_lock:
        _robots_cache.clear()


def robots_from_env(var: str = "JOBSCRAPER_RESPECT_ROBOTS") -> bool:
    """True when the env var opts into robots.txt (1/true/yes/on)."""
    return os.environ.get(var, "").strip().lower() in {
        "1", "true", "yes", "on"}


def _fetch_robots_text(scheme: str, host: str) -> str | None:
    """Download a host's robots.txt; None when missing/unreachable.

    A fetch failure never blocks crawling: it is treated as "no rules".
    """
    try:
        resp = requests.get(f"{scheme}://{host}/robots.txt",
                            headers={"User-Agent": UA_POOL[0]},
                            timeout=15)
    except Exception:
        return None
    if resp.status_code >= 400:
        return None
    return resp.text


def _robots_parser(url: str) -> RobotFileParser | None:
    """Per-host cached robots.txt parser (None only on internal error)."""
    parsed = urlparse(url)
    host = parsed.netloc
    scheme = parsed.scheme or "https"
    with _robots_lock:
        cached = _robots_cache.get(host)
        if cached and time.time() - cached[1] < ROBOTS_TTL:
            return cached[0]
    parser = RobotFileParser()
    parser.set_url(f"{scheme}://{host}/robots.txt")
    try:
        text = _fetch_robots_text(scheme, host)
        if text:
            parser.parse(text.splitlines())
        else:
            # No reachable robots.txt: treat as fully allowed (and keep
            # the cache entry so we do not refetch on every URL).
            parser.allow_all = True
    except Exception:
        return None
    with _robots_lock:
        _robots_cache[host] = (parser, time.time())
    return parser


def robots_allowed(url: str, user_agent: str = "*") -> bool:
    """True when the URL may be fetched under the host's robots.txt.

    Returns True when the robots check is disabled, when the host has
    no reachable robots.txt, or when the parser errors: a missing or
    unreadable robots.txt is never grounds for blocking.
    """
    if not robots_enabled():
        return True
    parser = _robots_parser(url)
    if parser is None:
        return True
    try:
        return bool(parser.can_fetch(user_agent, url))
    except Exception:
        return True

_PROXY_SCHEMES = {"http", "https", "socks4", "socks5", "socks4h", "socks5h"}

_domain_last_hit: dict[str, float] = {}
_domain_lock = threading.Lock()

_proxy_pool: list[str] = []
_proxy_index = 0
_proxy_lock = threading.Lock()
_proxy_failures: dict[str, int] = {}
_proxy_parked_until: dict[str, float] = {}


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
    """GET with retries, backoff, UA rotation and proxy rotation.

    Retries 429/5xx and network errors; fails fast on 401/403/404.
    Each attempt uses the next proxy from the rotation pool (if any).
    Raises the last error when retries are exhausted.
    Raises :class:`RobotsDisallowedError` immediately when the opt-in
    robots.txt check is enabled and the URL is disallowed.
    """
    ua = get_ua()
    if robots_enabled() and not robots_allowed(url, ua):
        raise RobotsDisallowedError(url)
    last_err: Exception | None = None
    for attempt in range(max_retries):
        polite_wait(url)
        proxy = next_proxy()
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": ua,
                         "Accept-Language": "en-US,en;q=0.9"},
                proxies={"http": proxy, "https": proxy} if proxy else None,
                timeout=timeout,
            )
            if proxy:
                mark_proxy_ok(proxy)
            if resp.status_code in (401, 403, 404):
                raise requests.HTTPError(
                    f"HTTP {resp.status_code} (no retry)")
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(
                    f"HTTP {resp.status_code} (retryable)")
            resp.raise_for_status()
            return resp
        except requests.HTTPError as exc:
            # The proxy delivered a response, so it is not at fault;
            # rotation alone moves the next attempt to another proxy.
            if "no retry" in str(exc):
                raise
            last_err = exc
        except Exception as exc:  # network-level: DNS, timeouts, resets
            last_err = exc
            if proxy:
                mark_proxy_bad(proxy)
        time.sleep((2 ** attempt) + random.uniform(0, 1))
    assert last_err is not None
    raise last_err


def parse_proxy(spec: str) -> str:
    """Validate a proxy URL spec; return the cleaned spec.

    Accepted form: ``scheme://[user:pass@]host[:port]`` where scheme is
    http, https, socks4, socks5 (optionally with an ``h`` suffix for
    remote DNS resolution). Raises :class:`ValueError` on invalid specs.
    """
    spec = spec.strip()
    if not spec:
        raise ValueError("empty proxy spec")
    parsed = urlparse(spec)
    if parsed.scheme.lower() not in _PROXY_SCHEMES:
        raise ValueError(
            f"bad proxy scheme in {spec!r} "
            f"(want one of: {', '.join(sorted(_PROXY_SCHEMES))})")
    if not parsed.hostname:
        raise ValueError(f"no host in proxy spec {spec!r}")
    return spec


def configure_proxies(specs: list[str]) -> None:
    """Install the proxy rotation pool, replacing any previous pool.

    All specs are validated up front; a single bad spec aborts with
    :class:`ValueError` and leaves the existing pool untouched.
    Pass an empty list to disable proxies (direct connections).
    """
    validated = [parse_proxy(spec) for spec in specs]
    with _proxy_lock:
        global _proxy_pool, _proxy_index
        _proxy_pool = validated
        _proxy_index = 0
        _proxy_failures.clear()
        _proxy_parked_until.clear()


def reset_proxies() -> None:
    """Clear the proxy pool (direct connections from here on)."""
    configure_proxies([])


def load_proxies_file(path: str) -> list[str]:
    """Read proxy URLs from a text file (one per line).

    Blank lines and ``#`` comments are ignored.
    """
    specs: list[str] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                specs.append(line)
    return specs


def proxies_from_env(var: str = "JOBSCRAPER_PROXIES") -> list[str]:
    """Read proxies from an environment variable.

    Accepts whitespace- or comma-separated proxy URLs; unset or blank
    means no proxies.
    """
    raw = os.environ.get(var, "")
    return [part for part in
            [p.strip() for p in raw.replace(",", " ").split()]
            if part]


def proxy_count() -> int:
    """Number of proxies currently in the pool (parked ones included)."""
    with _proxy_lock:
        return len(_proxy_pool)


def _unparked(pool: list[str]) -> list[str]:
    now = time.time()
    return [p for p in pool
            if _proxy_parked_until.get(p, 0.0) <= now]


def next_proxy() -> str | None:
    """Return the next proxy URL in round-robin order, or None.

    Parked proxies are skipped. Returns None when no proxies are
    configured or every proxy is currently parked (callers then fall
    back to a direct connection).
    """
    with _proxy_lock:
        if not _proxy_pool:
            return None
        candidates = _unparked(_proxy_pool)
        if not candidates:
            return None
        global _proxy_index
        chosen = candidates[_proxy_index % len(candidates)]
        _proxy_index = (_proxy_index + 1) % len(candidates)
        return chosen


def mark_proxy_bad(proxy: str) -> None:
    """Record a failed request through ``proxy``; park it on threshold.

    A proxy with :data:`PROXY_MAX_FAILURES` consecutive failures stops
    being handed out for :data:`PROXY_PARK_SECONDS` seconds.
    """
    with _proxy_lock:
        if proxy not in _proxy_pool:
            return
        fails = _proxy_failures.get(proxy, 0) + 1
        _proxy_failures[proxy] = fails
        if fails >= PROXY_MAX_FAILURES:
            _proxy_parked_until[proxy] = time.time() + PROXY_PARK_SECONDS
            _proxy_failures[proxy] = 0


def mark_proxy_ok(proxy: str) -> None:
    """Clear a proxy's consecutive-failure streak after a good response."""
    with _proxy_lock:
        _proxy_failures.pop(proxy, None)


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
