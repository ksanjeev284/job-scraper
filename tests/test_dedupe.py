"""Fuzzy near-duplicate detection (dedupe.py)."""

import pytest

from jobscraper.dedupe import (
    FUZZY_TITLE_THRESHOLD,
    fuzzy_dedupe_from_env,
    fuzzy_dedupe_results,
    fuzzy_threshold_from_env,
    normalize_company,
    normalize_title,
    title_similarity,
)
from jobscraper.models import MatchResult, Posting


def _post(company, title, score=50, error=None):
    post = Posting(url=f"https://example.com/{company}/{title}", error=error)
    post.company, post.title = company, title
    if score is not None:
        post.match = MatchResult(total=score)
    return post


# --- normalization --------------------------------------------------------


def test_normalize_title_strips_levels_and_punctuation():
    assert normalize_title("Senior Splunk Engineer (Nights)") == \
        "splunk engineer nights"
    assert normalize_title("Sr. Splunk Engineer - NIGHTS") == \
        "splunk engineer nights"


def test_normalize_company():
    assert normalize_company("Acme, Inc.") == "acme inc"


# --- title_similarity -----------------------------------------------------


def test_similarity_identical_titles():
    assert title_similarity("Splunk Engineer", "Splunk Engineer") == 1.0


def test_similarity_ignores_word_order():
    assert title_similarity("Detection Security Engineer",
                            "Security Engineer, Detection") == 1.0


def test_similarity_reworded_repost_scores_high():
    assert title_similarity("Splunk Engineer (Nights)",
                            "Splunk Engineer - Night Shift") >= \
        FUZZY_TITLE_THRESHOLD


def test_similarity_distinct_roles_score_low():
    assert title_similarity("SOC Analyst", "Splunk Administrator") < \
        FUZZY_TITLE_THRESHOLD


def test_similarity_empty_title_is_zero():
    assert title_similarity("", "Splunk Engineer") == 0.0
    assert title_similarity(None, None) == 0.0


# --- fuzzy_dedupe_results -------------------------------------------------


def test_merges_reworded_reposts_keeping_best_score():
    low = _post("Acme", "Splunk Engineer (Nights)", 40)
    high = _post("Acme", "Splunk Engineer - Night Shift", 80)
    kept, dropped = fuzzy_dedupe_results([low, high])
    assert dropped == 1
    assert kept == [high]
    assert any(n.startswith("fuzzy-dedupe merged 1 near-duplicate")
               for n in high.fetch_notes)
    assert low.url in high.fetch_notes[0]


def test_keeps_earliest_on_score_tie():
    first = _post("Acme", "Splunk Engineer (Nights)", 60)
    second = _post("Acme", "Splunk Engineer - Night Shift", 60)
    kept, dropped = fuzzy_dedupe_results([first, second])
    assert kept == [first]
    assert dropped == 1


def test_does_not_merge_different_companies():
    posts = [_post("Acme", "Splunk Engineer (Nights)", 60),
             _post("Globex", "Splunk Engineer - Night Shift", 70)]
    kept, dropped = fuzzy_dedupe_results(posts)
    assert kept == posts
    assert dropped == 0


def test_does_not_merge_distinct_roles():
    posts = [_post("Acme", "SOC Analyst", 60),
             _post("Acme", "Splunk Administrator", 70)]
    kept, dropped = fuzzy_dedupe_results(posts)
    assert kept == posts
    assert dropped == 0


def test_never_merges_errored_or_untitled():
    posts = [_post("Acme", "Splunk Engineer (Nights)", 60),
             _post("Acme", "Splunk Engineer - Night Shift", 70,
                   error="boom"),
             _post("Acme", None, 90)]
    kept, dropped = fuzzy_dedupe_results(posts)
    assert kept == posts
    assert dropped == 0


def test_threshold_is_respected():
    a = _post("Acme", "Splunk Engineer (Nights)", 60)
    b = _post("Acme", "Splunk Engineer - Night Shift", 70)
    similarity = title_similarity(normalize_title(a.title),
                                  normalize_title(b.title))
    assert 0.85 < similarity < 0.95
    # A floor just above the real similarity keeps both; just below merges.
    kept, dropped = fuzzy_dedupe_results([a, b],
                                         threshold=similarity + 0.01)
    assert kept == [a, b]
    assert dropped == 0
    kept, dropped = fuzzy_dedupe_results([a, b],
                                         threshold=similarity - 0.01)
    assert dropped == 1
    assert kept == [b]


def test_transitive_grouping_merges_chain():
    a = _post("Acme", "Splunk Engineer", 50)
    b = _post("Acme", "Splunk Engineer Nights", 60)
    c = _post("Acme", "Splunk Engineer Night Shift", 40)
    kept, dropped = fuzzy_dedupe_results([a, b, c])
    assert dropped == 2
    assert kept == [b]
    assert any("2 near-duplicate" in n for n in b.fetch_notes)


def test_original_order_preserved():
    posts = [_post("Acme", "SOC Analyst", 10),
             _post("Acme", "Splunk Engineer (Nights)", 90),
             _post("Acme", "Splunk Engineer - Night Shift", 20),
             _post("Acme", "Splunk Administrator", 30)]
    kept, dropped = fuzzy_dedupe_results(posts)
    assert dropped == 1
    assert [p.title for p in kept] == [
        "SOC Analyst", "Splunk Engineer (Nights)", "Splunk Administrator"]


# --- env helpers ----------------------------------------------------------


def test_fuzzy_dedupe_from_env(monkeypatch):
    monkeypatch.delenv("JOBSCRAPER_FUZZY_DEDUPE", raising=False)
    assert fuzzy_dedupe_from_env() is False
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv("JOBSCRAPER_FUZZY_DEDUPE", value)
        assert fuzzy_dedupe_from_env() is True
    monkeypatch.setenv("JOBSCRAPER_FUZZY_DEDUPE", "0")
    assert fuzzy_dedupe_from_env() is False


def test_fuzzy_threshold_from_env(monkeypatch):
    monkeypatch.delenv("JOBSCRAPER_FUZZY_THRESHOLD", raising=False)
    assert fuzzy_threshold_from_env() == FUZZY_TITLE_THRESHOLD
    monkeypatch.setenv("JOBSCRAPER_FUZZY_THRESHOLD", "0.9")
    assert fuzzy_threshold_from_env() == 0.9
    monkeypatch.setenv("JOBSCRAPER_FUZZY_THRESHOLD", "nope")
    with pytest.raises(ValueError, match="must be a number"):
        fuzzy_threshold_from_env()
    monkeypatch.setenv("JOBSCRAPER_FUZZY_THRESHOLD", "1.5")
    with pytest.raises(ValueError, match="between 0 and 1"):
        fuzzy_threshold_from_env()
