"""Per-source run diagnostics: classification, aggregation, statuses."""

import pytest

from jobscraper.boards import board_name_for_url
from jobscraper.models import Posting, SourceStat
from jobscraper.pipeline import _collect_source_stats


def _post(url, error=None):
    return Posting(url=url, error=error)


# --- board_name_for_url --------------------------------------------------


@pytest.mark.parametrize("url,expected", [
    ("https://www.lever.co/acme/abc-123", "lever"),
    ("https://api.lever.co/v0/postings/acme/abc-123", "lever"),
    ("https://jobs.ashbyhq.com/acme/abc-123", "ashby"),
    ("https://boards.greenhouse.io/acme/jobs/1", "greenhouse"),
    ("https://acme.smartrecruiters.com/job/1", "smartrecruiters"),
    ("https://acme.myworkdayjobs.com/job/1", "workday"),
    ("https://acme.teamtailor.com/jobs/1", "teamtailor"),
    ("https://acme.jobs.personio.de/job/1", "personio"),
    ("https://acme.recruitee.com/o/1", "recruitee"),
    ("https://acme.workable.com/j/1", "workable"),
    ("https://acme.breezy.hr/p/1", "breezy"),
    ("https://acme.pinpointhq.com/postings/1", "pinpoint"),
    ("https://api.rippling.com/platform/api/ats/v1/board/acme/jobs",
     "rippling"),
    ("https://acme.eightfold.ai/careers/1", "eightfold"),
    ("https://www.linkedin.com/jobs/view/123", "linkedin"),
    ("https://remoteok.com/remote-jobs/1", "remoteok"),
    ("https://remotive.com/remote-jobs/1", "remotive"),
    ("https://weworkremotely.com/remote-jobs/1", "weworkremotely"),
    ("https://workingnomads.com/remote-jobs/1", "workingnomads"),
    ("https://careers.acme.com/jobs/1", "generic"),
    ("https://WWW.LEVER.CO/Acme/ABC-123", "lever"),
])
def test_board_name_for_url(url, expected):
    assert board_name_for_url(url) == expected


# --- SourceStat ----------------------------------------------------------


def test_source_stat_status_ok():
    stat = SourceStat(name="lever", attempted=3, ok=3)
    assert stat.status == "ok"
    assert stat.filtered == 0


def test_source_stat_status_partial():
    stat = SourceStat(name="lever", attempted=3, ok=2, errored=1)
    assert stat.status == "partial"


def test_source_stat_status_failed():
    stat = SourceStat(name="lever", attempted=2, errored=2)
    assert stat.status == "failed"
    assert stat.filtered == 0


def test_source_stat_status_empty_when_all_filtered():
    stat = SourceStat(name="lever", attempted=2)
    assert stat.status == "empty"
    assert stat.filtered == 2


def test_source_stat_record_error_truncates_and_counts():
    stat = SourceStat(name="lever")
    stat.record_error("HTTP 403 forbidden" * 20)
    stat.record_error("HTTP 403 forbidden" * 20)
    stat.record_error("timeout")
    assert len(stat.errors) == 2
    long_key = next(k for k in stat.errors if k.startswith("HTTP 403"))
    assert len(long_key) <= 120
    assert stat.errors[long_key] == 2


def test_source_stat_to_dict_includes_derived():
    d = SourceStat(name="lever", attempted=4, ok=2, errored=1).to_dict()
    assert d["status"] == "partial"
    assert d["filtered"] == 1


# --- _collect_source_stats -----------------------------------------------


def test_collect_source_stats_aggregates():
    urls = [
        "https://www.lever.co/acme/1",
        "https://www.lever.co/acme/2",
        "https://jobs.ashbyhq.com/acme/3",
    ]
    results = [
        _post("https://www.lever.co/acme/1"),
        _post("https://www.lever.co/acme/2", error="403 from board API"),
        _post("https://jobs.ashbyhq.com/acme/3"),
    ]
    durations = {u: 0.5 for u in urls}
    stats = {s.name: s for s in _collect_source_stats(urls, results,
                                                      durations)}
    lever = stats["lever"]
    assert lever.attempted == 2
    assert lever.ok == 1
    assert lever.errored == 1
    assert lever.status == "partial"
    assert lever.errors == {"403 from board API": 1}
    assert lever.duration_ms == pytest.approx(1000.0)
    assert stats["ashby"].status == "ok"


def test_collect_source_stats_sorted_by_name():
    urls = ["https://careers.acme.com/1", "https://www.lever.co/acme/2"]
    stats = _collect_source_stats(urls, [_post(u) for u in urls], {})
    assert [s.name for s in stats] == ["generic", "lever"]


# --- run_pipeline(run_stats=True) -----------------------------------------


def test_run_pipeline_run_stats_flag(monkeypatch):
    import jobscraper.pipeline as pipeline

    calls = {
        "https://www.lever.co/acme/1": _post("https://www.lever.co/acme/1"),
        "https://www.lever.co/acme/2": _post("https://www.lever.co/acme/2",
                                             error="boom"),
        "https://jobs.ashbyhq.com/acme/3": _post(
            "https://jobs.ashbyhq.com/acme/3"),
    }

    def fake_process_url(url, **kwargs):
        return calls[url]

    monkeypatch.setattr(pipeline, "process_url", fake_process_url)
    results, new_count, closed, stats = pipeline.run_pipeline(
        list(calls), no_score=True, use_cache=False, workers=1,
        run_stats=True)
    assert len(results) == 3
    by_name = {s.name: s for s in stats}
    assert by_name["lever"].attempted == 2
    assert by_name["lever"].ok == 1
    assert by_name["lever"].errored == 1
    assert by_name["lever"].status == "partial"
    assert by_name["ashby"].status == "ok"


def test_run_pipeline_default_still_returns_three_tuple(monkeypatch):
    import jobscraper.pipeline as pipeline

    def fake_process_url(url, **kwargs):
        return Posting(url=url)

    monkeypatch.setattr(pipeline, "process_url", fake_process_url)
    out = pipeline.run_pipeline(["https://careers.acme.com/1"],
                                no_score=True, use_cache=False, workers=1)
    assert len(out) == 3
