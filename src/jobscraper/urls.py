"""URL canonicalization for stable de-duplication.

Job postings are shared, syndicated and re-fetched across runs with
tracking query parameters (``utm_*``, ``trk``, ``gclid``, ...), case
variants in the host, and stray fragments/trailing slashes. Treating
those variants as different URLs causes duplicate fetches, duplicate
results, and false "new posting" flags in watch mode.

:func:`canonicalize_url` reduces a posting URL to a canonical form so
all of those variants collapse to one identity:

- scheme and host lowercased; default ports dropped
- fragment removed
- known tracking query parameters removed, remaining ones sorted
- trailing slash removed from non-root paths; path case preserved
  (some ATS hosts, e.g. SmartRecruiters, are case-sensitive in the path)
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit, urlunsplit

#: Query parameters that exist only for attribution/tracking. Values are
#: checked by name (lowercased); any parameter whose name matches an
#: entry here, or starts with ``utm_``, is dropped.
_TRACKING_PARAMS = frozenset({
    # Google Ads / Analytics
    "gclid", "gbraid", "wbraid", "gad_source", "gad_campaignid",
    # Microsoft/Bing
    "msclkid",
    # Facebook/Meta
    "fbclid", "fb_action_ids", "fb_action_types", "fb_source",
    # LinkedIn
    "trk", "trkid", "veh", "lipi",
    # Generic/referral
    "ref", "refid", "ref_id", "originalreferer", "original_referer",
    "srsltid", "scid", "sc_cid", "vero_conv", "vero_id", "mc_cid",
    "mc_eid", "_hsenc", "_hsmi", "hsctatracking", "mkt_tok", "pk_campaign",
    "pk_kwd", "pk_medium", "pk_source", "piwik_kwd", "piwik_campaign",
    "matomo_campaign", "matomo_kwd", "yclid", "dclid", "gdfms",
    "aff_id", "aff_sub", "aff_sub2", "ircid", "irgwc", "zanpid",
    "c_id", "tblci", "outbrain_click_id",
})

#: Query parameters some job boards add to the *view* page itself that do
#: not identify the posting. Kept separate so call sites can reason about
#: them, but dropped just like tracking params.
_BOARD_NOISE_PARAMS = frozenset({
    "currentjobid",  # LinkedIn search UI state
})


def _is_tracking(name: str) -> bool:
    low = name.lower()
    return (
        low.startswith("utm_")
        or low in _TRACKING_PARAMS
        or low in _BOARD_NOISE_PARAMS
    )


def canonicalize_url(url: str) -> str:
    """Return the canonical form of a posting URL.

    Non-URL input (empty string, unparsable text) is returned unchanged
    except for surrounding whitespace, which is always stripped.
    """
    url = (url or "").strip()
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.scheme or not parts.netloc:
        return url

    scheme = parts.scheme.lower()
    host = parts.hostname or ""
    host = host.lower()
    port = parts.port
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None
    netloc = f"{host}:{port}" if port else host
    if parts.username:
        userinfo = parts.username
        if parts.password:
            userinfo += f":{parts.password}"
        netloc = f"{userinfo}@{netloc}"

    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")

    kept = sorted(
        (name, value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking(name)
    )
    query = "&".join(
        f"{name}={value}" if value else name for name, value in kept
    )

    return urlunsplit((scheme, netloc, path, query, ""))


def input_dedupe(urls: list[str]) -> list[str]:
    """Drop input URLs that canonicalize to an already-seen URL.

    Preserves order and returns the first spelling seen for each
    canonical form.
    """
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        key = canonicalize_url(url)
        if key not in seen:
            seen.add(key)
            out.append(url)
    return out
