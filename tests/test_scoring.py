"""Profile loading and 0-100 match scoring."""

import json

from jobscraper.models import MatchResult, Posting, Section
from jobscraper.scoring import load_profile, posting_age_days, score_posting


def _profile(tmp_path):
    data = {
        "skills": ["Splunk", "SIEM", "Python", "SOC"],
        "years_total": 4.5,
        "years_soc": 4.0,
        "certs": ["Security+"],
        "locations": ["Hyderabad", "Bengaluru"],
        "current_ctc_lpa": 16.8,
    }
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data))
    return str(path)


def _posting(**kwargs):
    post = Posting(url="https://example.com/j/1")
    post.title = kwargs.get("title", "Senior Splunk Engineer")
    post.location = kwargs.get("location", "Hyderabad")
    post.skills_found = ["Splunk", "SIEM", "Python"]
    post.experience_years_mentioned = kwargs.get("exp", [3, 5])
    post.salary_hits = kwargs.get("salary", ["20 LPA"])
    post.sections = [Section("Requirements",
                            "Splunk SIEM SOC work. Security+ a plus.")]
    post.requirements = post.sections
    post.signals = {"sponsorship_mentioned": False,
                    "german_required": False, "work_mode": ["hybrid"]}
    return post


def test_load_profile(tmp_path):
    profile = load_profile(_profile(tmp_path))
    assert profile["years_total"] == 4.5


def test_score_in_range(tmp_path):
    match = score_posting(_posting(), load_profile(_profile(tmp_path)))
    assert isinstance(match, MatchResult)
    assert 0 <= match.total <= 100
    assert sum(match.breakdown.values()) == match.total


def test_strong_match_scores_high(tmp_path):
    match = score_posting(_posting(), load_profile(_profile(tmp_path)))
    assert match.total >= 60


def test_junior_title_lowers_seniority(tmp_path):
    profile = load_profile(_profile(tmp_path))
    junior = score_posting(_posting(title="Junior SOC Analyst L1"),
                           profile)
    senior = score_posting(_posting(title="Senior Splunk Engineer"),
                           profile)
    assert junior.breakdown["seniority"] <= senior.breakdown["seniority"]


def test_exp_mismatch_penalized(tmp_path):
    profile = load_profile(_profile(tmp_path))
    fit = score_posting(_posting(exp=[3, 5]), profile)
    far = score_posting(_posting(exp=[10, 12]), profile)
    assert fit.breakdown["experience"] > far.breakdown["experience"]


def test_posting_age_days_formats():
    assert posting_age_days("2026-10-01") in (2, 3, 4)
    assert posting_age_days("not a date") is None
    assert posting_age_days(None) is None
