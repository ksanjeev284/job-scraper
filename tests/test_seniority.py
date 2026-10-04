"""Tests for structured seniority inference and the --seniority filter."""

import pytest

from jobscraper.models import Posting
from jobscraper.pipeline import apply_filters
from jobscraper.seniority import (
    LEVELS,
    Seniority,
    infer_seniority,
    rank,
)


@pytest.mark.parametrize("title,expected", [
    ("Senior Security Engineer", "senior"),
    ("Sr. DevOps Engineer", "senior"),
    ("Senior Staff Engineer", "staff"),
    ("Staff Platform Engineer", "staff"),
    ("Principal Engineer", "staff"),
    ("Distinguished Engineer", "staff"),
    ("Tech Lead", "lead"),
    ("Team Lead, Backend", "lead"),
    ("Engineering Manager", "manager"),
    ("Senior Engineering Manager", "manager"),
    ("People Manager", "manager"),
    ("Director of Engineering", "director"),
    ("Head of Product", "director"),
    ("VP of Sales", "executive"),
    ("Chief Technology Officer", "executive"),
    ("CTO", "executive"),
    ("Junior Analyst", "entry"),
    ("Graduate Trainee", "entry"),
    ("Software Engineering Intern", "intern"),
    ("Working Student", "intern"),
    ("Mid-level Python Developer", "mid"),
    ("Software Engineer", "unknown"),
    ("Banana Designer", "unknown"),
])
def test_title_markers(title, expected):
    result = infer_seniority(title)
    assert result.level == expected
    if expected == "unknown":
        assert result.confidence == 0.0
        assert result.evidence == []
    else:
        assert result.confidence >= 0.8
        assert result.evidence


def test_intern_beats_other_markers():
    # A posting calling itself an internship must not be ranked
    # higher on a stray "senior" somewhere in the title.
    assert infer_seniority("Senior Engineering Intern").level == "intern"


def test_management_beats_ic_markers():
    assert infer_seniority("Junior Product Manager").level == "manager"
    assert infer_seniority("Senior Director, Data").level == "director"


def test_description_signals_without_title_markers():
    result = infer_seniority(
        "Backend Engineer",
        description="We are hiring for an entry-level role. "
                    "0-2 years of experience required.",
        experience_years=[0, 2])
    assert result.level == "entry"
    assert result.confidence == 0.65  # description + years agree

    result = infer_seniority(
        "Backend Engineer",
        description="Requires 8+ years of distributed systems experience.")
    assert result.level == "senior"

    result = infer_seniority(
        "Backend Engineer",
        description="Requires 12+ years; you will mentor the org.")
    assert result.level == "staff"


def test_description_years_band():
    result = infer_seniority(
        "Data Scientist", experience_years=[1, 2])
    assert result.level == "entry"
    assert result.confidence == 0.4

    result = infer_seniority(
        "Data Scientist", experience_years=[6, 8])
    assert result.level == "senior"

    result = infer_seniority("Data Scientist")
    assert result.level == "unknown"
    assert result.confidence == 0.0


def test_title_confirmed_by_description_has_high_confidence():
    result = infer_seniority(
        "Senior Backend Engineer",
        description="We need 6+ years of Python experience.")
    assert result.level == "senior"
    assert result.confidence == 0.95  # title + description agree
    assert len(result.evidence) == 2


def test_rank_ordering():
    assert rank("intern") < rank("entry") < rank("mid")
    assert rank("mid") < rank("senior") < rank("staff")
    assert rank("staff") < rank("lead") < rank("manager")
    assert rank("manager") < rank("director") < rank("executive")
    assert rank("bogus") == -1
    assert set(LEVELS) >= {
        "intern", "entry", "mid", "senior", "staff", "lead",
        "manager", "director", "executive", "unknown",
    }


def _posting(title: str) -> Posting:
    post = Posting(url=f"https://example.com/{title.replace(' ', '-')}")
    post.title = title
    seniority = infer_seniority(title)
    post.seniority = seniority.level
    return post


def test_seniority_filter_keeps_matches():
    posts = [_posting("Senior Security Engineer"),
             _posting("Junior Analyst"),
             _posting("Engineering Manager")]
    kept = apply_filters(posts, seniority_filter="senior,staff")
    assert [p.title for p in kept] == ["Senior Security Engineer"]


def test_seniority_filter_drops_unknown_unless_listed():
    posts = [_posting("Banana Designer")]
    assert apply_filters(posts, seniority_filter="senior") == []
    assert len(apply_filters(posts, seniority_filter="senior,unknown")) == 1


def test_seniority_filter_rejects_bad_levels():
    with pytest.raises(ValueError, match="unknown seniority level"):
        apply_filters([_posting("Senior Security Engineer")],
                      seniority_filter="ceo,senior")


def test_seniority_filter_keeps_errored_postings():
    post = Posting(url="https://example.com/broken", error="boom")
    kept = apply_filters([post], seniority_filter="senior")
    assert kept == [post]


def test_seniority_dataclass_shape():
    result = Seniority(level="senior", evidence=["title: 'senior'"],
                       confidence=0.8)
    assert result.level == "senior"
    assert result.evidence == ["title: 'senior'"]
    assert result.confidence == 0.8
