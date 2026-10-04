"""Tests for the --max-age posting-freshness filter (pipeline.apply_filters)."""

import pytest

from jobscraper.cli import build_parser
from jobscraper.models import Posting
from jobscraper.pipeline import apply_filters


def _post(age_days=None, error=None):
    return Posting(url="https://example.com/j", age_days=age_days,
                   error=error)


class TestMaxAgeFilter:
    def test_none_disables_filter(self):
        posts = [_post(5), _post(200), _post(None)]
        assert apply_filters(posts, max_age=None) == posts

    def test_fresh_kept_stale_dropped(self):
        kept = apply_filters([_post(5), _post(200)], max_age=7)
        assert len(kept) == 1 and kept[0].age_days == 5

    def test_boundary_is_inclusive(self):
        kept = apply_filters([_post(7)], max_age=7)
        assert len(kept) == 1

    def test_unknown_age_is_kept(self):
        kept = apply_filters([_post(None)], max_age=1)
        assert len(kept) == 1

    def test_zero_keeps_only_today(self):
        kept = apply_filters([_post(0), _post(1)], max_age=0)
        assert [p.age_days for p in kept] == [0]

    def test_errored_postings_are_kept(self):
        bad = _post(365, error="fetch failed")
        assert apply_filters([bad], max_age=1) == [bad]

    def test_negative_rejected(self):
        with pytest.raises(ValueError):
            apply_filters([_post(1)], max_age=-1)

    def test_combines_with_other_filters(self):
        fresh_match = Posting(url="https://example.com/a", age_days=5,
                              title="Security Analyst")
        stale_match = Posting(url="https://example.com/b", age_days=200,
                              title="Security Analyst")
        kept = apply_filters([fresh_match, stale_match],
                             max_age=7, keyword_filter="analyst")
        assert kept == [fresh_match]


class TestMaxAgeCli:
    def test_parser_accepts_max_age(self):
        args = build_parser().parse_args(
            ["https://example.com/j", "--max-age", "7"])
        assert args.max_age == 7

    def test_parser_defaults_to_none(self):
        args = build_parser().parse_args(["https://example.com/j"])
        assert args.max_age is None
