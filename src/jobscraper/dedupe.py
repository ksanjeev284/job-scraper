"""Fuzzy near-duplicate detection for multi-board scrape runs.

Exact dedupe (:func:`jobscraper.pipeline.dedupe_results`) only merges the
same posting on two boards when its normalized ``(company, title)`` key
matches exactly. In practice boards disagree about wording -- "Splunk
Engineer (Nights)" vs "Splunk Engineer - Night Shift" vs "Splunk Engineer,
Night Shift" -- so this module merges postings from the same employer whose
titles are *nearly* identical instead of exactly equal.

Similarity is a thefuzz-style token-set ratio built on :mod:`difflib`
(stdlib only): token order does not matter, and a title that is a
token-subset of another scores high, so reworded reposts merge while
genuinely different roles stay separate.
"""

from __future__ import annotations

import os
import re
from difflib import SequenceMatcher

from jobscraper.models import Posting

#: Default title-similarity floor (0..1) for two postings to count as the
#: same role. Chosen so reworded reposts merge -- "Splunk Engineer (Nights)"
#: vs "Splunk Engineer - Night Shift" scores ~0.90 -- while genuinely
#: different roles stay separate ("Frontend Engineer" vs "Backend Engineer"
#: scores ~0.67; "Software Engineer" vs "Software Engineering Manager"
#: ~0.76). Token-subset titles always score 1.0 and merge.
FUZZY_TITLE_THRESHOLD = 0.85

_LEVEL_TOKENS = re.compile(
    r"\b(senior|sr|junior|jr|lead|staff|principal|i{1,3}|iv)\b")


def normalize_company(text: str | None) -> str:
    """Lowercase and strip punctuation/extra whitespace from a company."""
    text = re.sub(r"[^a-z0-9 ]", " ", (text or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def normalize_title(text: str | None) -> str:
    """Normalize a posting title for comparison.

    Same normalization as the exact dedupe key: lowercased, punctuation
    stripped, seniority/level tokens removed (so "Senior X" and "X" compare
    on the role itself), whitespace collapsed.
    """
    title = normalize_company(text)
    return re.sub(r"\s+", " ", _LEVEL_TOKENS.sub(" ", title)).strip()


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _ratio(first: str, second: str) -> float:
    return SequenceMatcher(None, first, second).ratio()


def title_similarity(first: str | None, second: str | None) -> float:
    """Token-set similarity of two posting titles, in the range 0..1.

    Word order is ignored (tokens are sorted) and a title that is a
    token-subset of the other scores 1.0: the best of three comparisons is
    kept -- ``intersection`` vs each title, and the two titles against each
    other. Empty titles score 0.
    """
    tokens1 = sorted(set(_tokens(first or "")))
    tokens2 = sorted(set(_tokens(second or "")))
    if not tokens1 or not tokens2:
        return 0.0
    common = sorted(set(tokens1) & set(tokens2))
    base = " ".join(common)
    candidate1 = " ".join(common + sorted(set(tokens1) - set(tokens2)))
    candidate2 = " ".join(common + sorted(set(tokens2) - set(tokens1)))
    return max(_ratio(base, candidate1), _ratio(base, candidate2),
               _ratio(" ".join(tokens1), " ".join(tokens2)))


def fuzzy_dedupe_from_env() -> bool:
    """True when ``JOBSCRAPER_FUZZY_DEDUPE`` is set to a truthy value."""
    return os.environ.get("JOBSCRAPER_FUZZY_DEDUPE", "").strip().lower() in (
        "1", "true", "yes", "on")


def fuzzy_threshold_from_env() -> float:
    """Similarity threshold override from ``JOBSCRAPER_FUZZY_THRESHOLD``.

    Falls back to :data:`FUZZY_TITLE_THRESHOLD` when unset or unparseable;
    values outside 0..1 raise ``ValueError`` with a clear message.
    """
    raw = os.environ.get("JOBSCRAPER_FUZZY_THRESHOLD", "").strip()
    if not raw:
        return FUZZY_TITLE_THRESHOLD
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"JOBSCRAPER_FUZZY_THRESHOLD must be a number, got {raw!r}"
        ) from exc
    if not 0.0 <= value <= 1.0:
        raise ValueError(
            f"JOBSCRAPER_FUZZY_THRESHOLD must be between 0 and 1, "
            f"got {value}")
    return value


def fuzzy_dedupe_results(
        posts: list[Posting],
        threshold: float = FUZZY_TITLE_THRESHOLD,
) -> tuple[list[Posting], int]:
    """Merge near-duplicate postings from the same employer.

    Postings are grouped by normalized company; within a company, titles
    with :func:`title_similarity` >= ``threshold`` are unioned into one
    group. Each group keeps its highest-scored posting (ties keep the
    earliest, mirroring :func:`jobscraper.pipeline.dedupe_results`); the
    kept posting gets a ``fetch_notes`` entry naming the merged URLs so
    the merge is never silent.

    Errored postings and postings without a usable company/title are never
    merged and are always kept. Returns ``(kept, dropped)`` with ``kept``
    in the original order and ``dropped`` the number of postings merged
    away.
    """
    count = len(posts)
    parent = list(range(count))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        root_i, root_j = find(i), find(j)
        if root_i != root_j:
            parent[max(root_i, root_j)] = min(root_i, root_j)

    # None = not eligible for merging (errored, or missing company/title).
    keys: list[tuple[str, str] | None] = []
    for post in posts:
        if post.error:
            keys.append(None)
            continue
        company = normalize_company(post.company)
        title = normalize_title(post.title)
        keys.append((company, title) if company and title else None)

    by_company: dict[str, list[int]] = {}
    for idx, key in enumerate(keys):
        if key is not None:
            by_company.setdefault(key[0], []).append(idx)

    titles = {idx: key[1] for idx, key in enumerate(keys) if key is not None}
    for members in by_company.values():
        for x in range(len(members)):
            for y in range(x + 1, len(members)):
                i, j = members[x], members[y]
                if title_similarity(titles[i], titles[j]) >= threshold:
                    union(i, j)

    groups: dict[int, list[int]] = {}
    for idx, key in enumerate(keys):
        if key is not None:
            groups.setdefault(find(idx), []).append(idx)

    def score(idx: int) -> int:
        match = posts[idx].match
        return match.total if match else -1

    keep: set[int] = set()
    merged_urls: dict[int, list[str]] = {}
    dropped = 0
    for members in groups.values():
        # max() returns the first maximal member, so ties keep the
        # earliest posting, exactly like dedupe_results' strict `>`.
        winner = max(members, key=score)
        keep.add(winner)
        dropped += len(members) - 1
        losers = [posts[m].url for m in members if m != winner]
        if losers:
            merged_urls[winner] = losers

    kept = [post for idx, post in enumerate(posts)
            if keys[idx] is None or idx in keep]
    for idx, urls in merged_urls.items():
        posts[idx].fetch_notes.append(
            f"fuzzy-dedupe merged {len(urls)} near-duplicate posting(s): "
            + ", ".join(urls))
    return kept, dropped
