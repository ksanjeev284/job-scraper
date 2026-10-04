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


def test_posting_age_days_relative():
    assert posting_age_days("3 days ago") == 3
    assert posting_age_days("2 weeks ago") == 14
    assert posting_age_days("1 month ago") == 30
    assert posting_age_days("5 hours ago") == 0
    assert posting_age_days("today") == 0
    assert posting_age_days("yesterday") == 1


def test_skill_aliases_count_as_matched(tmp_path):
    data = {
        "skills": ["SIEM"],
        "years_total": 4,
        "locations": [],
        "skill_aliases": {"SIEM": ["Splunk ES", "QRadar"]},
    }
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data))
    profile = load_profile(str(path))
    post = _posting(title="Splunk ES Engineer")
    post.skills_found = ["Splunk ES"]
    post.location = ""
    match = score_posting(post, profile)
    assert "splunk es" in match.matched_skills
    assert "splunk es" not in match.skill_gaps


def test_role_tiers_from_profile(tmp_path):
    data = {
        "skills": ["Python"],
        "years_total": 5,
        "locations": ["Remote"],
        "role_tiers": {
            "tier1": ["backend engineer"],
            "tier2": ["devops engineer"],
            "tier3": ["qa engineer"],
        },
    }
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(data))
    profile = load_profile(str(path))

    backend = _posting(title="Backend Engineer")
    backend.location = "Remote"
    devops = _posting(title="DevOps Engineer")
    devops.location = "Remote"
    assert (score_posting(backend, profile).breakdown["role_relevance"]
            == 5)
    assert (score_posting(devops, profile).breakdown["role_relevance"]
            == 4)


def test_custom_skills_extend_vocab():
    from jobscraper.extract import find_skills
    assert "Next.js" in find_skills("We use Next.js daily.",
                                    ("Next.js",))
    assert "Next.js" not in find_skills("We use Next.js daily.")


def test_template_profile_scores_neutrally():
    profile = load_profile(None)  # neutral template
    assert profile["role_tiers"] == {"tier1": [], "tier2": [], "tier3": []}
    post = _posting(title="Backend Engineer")
    post.location = "Berlin"
    match = score_posting(post, profile)
    assert match.breakdown["role_relevance"] == 2  # no tiers configured
    assert match.breakdown["location"] == 3  # no locations configured
    assert 0 <= match.total <= 100


def test_validate_profile_accepts_good():
    from jobscraper.scoring import validate_profile
    assert validate_profile({
        "skills": ["Python"], "years_total": 3, "locations": ["Remote"],
        "role_tiers": {"tier1": ["backend"]},
        "skill_aliases": {"X": ["Y"]},
        "weights": {"technical_skills": 40},
        "mystery_future_key": 1,
    }) == []


def test_validate_profile_rejects_bad():
    from jobscraper.scoring import validate_profile
    errors = validate_profile({
        "skills": "Python", "years_total": -1,
        "role_tiers": {"tier1": "backend"},
        "weights": {"nonsense": 5, "experience": -2},
    })
    assert len(errors) == 5


def test_custom_weights_rescale_breakdown(tmp_path):
    data = {"skills": ["Python"], "years_total": 5, "locations": [],
            "weights": {"technical_skills": 70, "experience": 0,
                        "seniority": 0, "certifications": 0, "location": 0,
                        "role_relevance": 0, "compensation": 0}}
    path = tmp_path / "profile.json"
    path.write_text(__import__("json").dumps(data))
    profile = load_profile(str(path))
    post = _posting(title="Python Engineer")
    post.skills_found = ["Python", "Go", "SQL", "Git", "Linux",
                            "Docker", "AWS"]
    profile["skills"] = ["Python", "Go", "SQL", "Git", "Linux",
                         "Docker", "AWS"]
    post.location = ""
    match = score_posting(post, profile)
    assert match.breakdown["technical_skills"] == 70
    assert match.breakdown["experience"] == 0
    assert match.total == sum(match.breakdown.values())
