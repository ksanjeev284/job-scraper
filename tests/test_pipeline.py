"""Dedupe keys and applied-tracker checks."""

from jobscraper.models import Posting
from jobscraper.pipeline import (
    apply_filters,
    check_tracker,
    dedupe_key,
    dedupe_results,
)


def _post(company, title, score=50, error=None):
    post = Posting(url=f"https://example.com/{company}/{title}", error=error)
    post.company, post.title = company, title
    if score is not None:
        from jobscraper.models import MatchResult
        post.match = MatchResult(total=score)
    return post


def test_dedupe_key_normalizes_seniority():
    a = dedupe_key(_post("Acme", "Senior Splunk Engineer"))
    b = dedupe_key(_post("acme", "Splunk Engineer"))
    assert a == b


def test_dedupe_results_keeps_best_score():
    low = _post("Acme", "SOC Analyst", score=40)
    high = _post("Acme", "SOC Analyst", score=80)
    kept = dedupe_results([low, high])
    assert len(kept) == 1
    assert kept[0].match.total == 80


def test_dedupe_results_keeps_distinct():
    kept = dedupe_results([_post("Acme", "SOC Analyst", 40),
                           _post("Globex", "SOC Analyst", 80)])
    assert len(kept) == 2


def test_dedupe_results_keeps_errors():
    bad = _post("Acme", "SOC Analyst", error="fetch failed")
    kept = dedupe_results([bad])
    assert kept == [bad]


def test_check_tracker_finds_url(tmp_path):
    tracker = tmp_path / "TRACKER.md"
    tracker.write_text("applied: https://example.com/acme/soc-analyst\n")
    assert check_tracker(_post("Acme", "SOC Analyst"), str(tracker)) is None
    hit = _post("Acme", "X")
    hit.url = "https://example.com/acme/soc-analyst"
    assert check_tracker(hit, str(tracker)) == "applied"


def test_check_tracker_missing_file():
    assert check_tracker(_post("Acme", "X"), "/no/such/file.md") is None


def test_apply_filters_location():
    hyd = _post("Acme", "Engineer")
    hyd.location = "Hyderabad, India"
    blr = _post("Globex", "Engineer")
    blr.location = "Bengaluru, India"
    kept = apply_filters([hyd, blr], location_filter="hyderabad")
    assert kept == [hyd]


def test_apply_filters_keywords():
    analyst = _post("Acme", "Data Analyst")
    engineer = _post("Globex", "Data Engineer")
    kept = apply_filters([analyst, engineer],
                         keyword_filter="analyst, scientist")
    assert kept == [analyst]


def test_apply_filters_keeps_errors():
    bad = _post("Acme", "Engineer", error="fetch failed")
    bad.location = "Nowhere"
    kept = apply_filters([bad], location_filter="hyderabad",
                         keyword_filter="analyst")
    assert kept == [bad]


def test_apply_filters_no_filters_keeps_all():
    posts = [_post("Acme", "Engineer"), _post("Globex", "Analyst")]
    assert apply_filters(posts) == posts
