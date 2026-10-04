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


def test_apply_filters_exclusions():
    tcs = _post("TCS", "Engineer")
    acme = _post("Acme", "Senior Intern")
    good = _post("Globex", "Engineer")
    kept = apply_filters([tcs, acme, good],
                         exclude_companies="tcs",
                         exclude_keywords="intern")
    assert kept == [good]


def test_apply_watch_flags_new(tmp_path):
    from jobscraper.pipeline import apply_watch
    state = str(tmp_path / "watch.json")
    a = _post("Acme", "Engineer")
    posts, new_count, closed = apply_watch([a], state)
    assert new_count == 1 and a.is_new and closed == []
    b = _post("Acme", "Engineer")
    c = _post("Globex", "Analyst")
    posts, new_count, closed = apply_watch([b, c], state)
    assert new_count == 1 and not b.is_new and c.is_new and closed == []


def test_apply_watch_detects_closed(tmp_path):
    import json
    from datetime import date

    from jobscraper.pipeline import apply_watch
    state = str(tmp_path / "watch.json")
    a = _post("Acme", "Engineer")
    b = _post("Globex", "Analyst")
    apply_watch([a, b], state)
    today = date.today().isoformat()
    # b disappears from the next run -> reported closed once.
    posts, new_count, closed = apply_watch([a], state)
    assert new_count == 0
    assert len(closed) == 1
    assert closed[0]["title"] == "Analyst"
    assert closed[0]["closed_since"] == today
    assert "first_seen" in closed[0]
    # A third run reports nothing new: already-closed stays closed.
    posts, new_count, closed = apply_watch([a], state)
    assert new_count == 0 and closed == []
    persisted = json.loads(open(state, encoding="utf-8").read())
    assert any(e.get("closed_since") == today for e in persisted.values())


def test_apply_watch_errored_not_closed(tmp_path):
    from jobscraper.pipeline import apply_watch
    state = str(tmp_path / "watch.json")
    a = _post("Acme", "Engineer")
    b = _post("Globex", "Analyst")
    apply_watch([a, b], state)
    # b now fails to fetch: transient error, must not be marked closed.
    b2 = _post("Globex", "Analyst")
    b2.error = "timeout"
    posts, new_count, closed = apply_watch([a, b2], state)
    assert new_count == 0 and closed == []


def test_apply_watch_reopened(tmp_path):
    from jobscraper.pipeline import apply_watch
    state = str(tmp_path / "watch.json")
    a = _post("Acme", "Engineer")
    b = _post("Globex", "Analyst")
    apply_watch([a, b], state)
    apply_watch([a], state)  # b closes
    posts, new_count, closed = apply_watch([a, b], state)
    assert new_count == 0 and closed == []  # b reopened, not new/closed
