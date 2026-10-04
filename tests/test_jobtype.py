"""Tests for canonical job-type normalization and the --job-type filter."""

import pytest

from jobscraper.jobtype import JOB_TYPES, normalize_job_type, parse_job_type_filter
from jobscraper.models import Posting
from jobscraper.pipeline import apply_filters


def test_explicit_schema_org_enums():
    assert normalize_job_type("FULL_TIME") == "full-time"
    assert normalize_job_type("PART_TIME") == "part-time"
    assert normalize_job_type("CONTRACTOR") == "contract"
    assert normalize_job_type("TEMPORARY") == "temporary"
    assert normalize_job_type("INTERN") == "internship"
    assert normalize_job_type(["FULL_TIME", "CONTRACTOR"]) == "full-time"


def test_explicit_ats_labels():
    assert normalize_job_type("Permanent") == "full-time"
    assert normalize_job_type("Full-time") == "full-time"
    assert normalize_job_type("Full time") == "full-time"
    assert normalize_job_type("Part-time") == "part-time"
    assert normalize_job_type("Contract") == "contract"
    assert normalize_job_type("Contractor") == "contract"
    assert normalize_job_type("Freelance") == "contract"
    assert normalize_job_type("Fixed-term") == "contract"
    assert normalize_job_type("Temp") == "temporary"
    assert normalize_job_type("Internship") == "internship"
    assert normalize_job_type("Apprenticeship") == "internship"
    assert normalize_job_type("Volunteer") == "other"


def test_multi_value_strings():
    assert normalize_job_type("Full-time, Contract") == "full-time"
    assert normalize_job_type("Contract | Part-time") == "contract"


def test_hint_fallback_from_title_and_text():
    assert normalize_job_type(None, title="Security Intern") == "internship"
    assert normalize_job_type("", title="Part-Time Analyst") == "part-time"
    assert normalize_job_type(None, text="6-month contract role") == "contract"
    assert normalize_job_type(None, text="freelance engagement") == "contract"
    assert normalize_job_type(None, text="temporary cover") == "temporary"
    assert normalize_job_type(None, title="Permanent Engineer") == "full-time"


def test_hint_does_not_misread_common_words():
    # "internal" must not imply "intern"; "contemplate" must not imply "temp".
    assert normalize_job_type(None, title="Internal Tools Engineer",
                              text="contemplate the architecture") == "unknown"
    assert normalize_job_type(None) == "unknown"
    assert normalize_job_type("") == "unknown"
    assert normalize_job_type("weird-never-seen") == "unknown"


def test_explicit_beats_hints():
    assert normalize_job_type("FULL_TIME", title="Security Intern") \
        == "full-time"


def test_parse_filter_valid():
    assert parse_job_type_filter("full-time,contract") == {"full-time",
                                                           "contract"}
    assert parse_job_type_filter(None) == set()
    assert parse_job_type_filter("") == set()


def test_parse_filter_rejects_typos():
    with pytest.raises(ValueError, match="unknown job type"):
        parse_job_type_filter("fulltime")


def _post(title, job_type):
    return Posting(url=f"https://example.com/{title.replace(' ', '-')}",
                   title=title, job_type=job_type)


def test_filter_keeps_matching_types():
    posts = [_post("A", "full-time"), _post("B", "contract"),
             _post("C", "internship"), _post("D", "unknown")]
    kept = apply_filters(posts, job_type_filter="full-time,contract")
    assert [p.title for p in kept] == ["A", "B"]


def test_filter_unknown_kept_only_when_listed():
    posts = [_post("A", "full-time"), _post("B", "unknown")]
    assert [p.title for p in apply_filters(posts,
                                           job_type_filter="full-time")] == ["A"]
    assert [p.title for p in apply_filters(posts,
                                           job_type_filter="full-time,unknown")] \
        == ["A", "B"]


def test_filter_errors_always_kept():
    bad = Posting(url="https://example.com/x", error="boom",
                  job_type="contract")
    assert apply_filters([bad], job_type_filter="internship") == [bad]


def test_job_types_constant_covers_expected_labels():
    assert set(JOB_TYPES) == {"full-time", "part-time", "contract",
                              "temporary", "internship", "other", "unknown"}
