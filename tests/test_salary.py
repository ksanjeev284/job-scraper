"""Tests for salary threshold parsing and filtering (src/jobscraper/salary.py)."""

import pytest

from jobscraper.models import Posting
from jobscraper.pipeline import apply_filters
from jobscraper.salary import meets_salary_threshold, parse_salary_threshold


def _post(figures=None, error=None):
    return Posting(url="https://example.com/j",
                   salary_normalized=figures or [], error=error)


def _fig(currency, lo, hi):
    return {"raw": "x", "currency": currency,
            "min_annual": lo, "max_annual": hi}


class TestParseSalaryThreshold:
    def test_usd_with_suffix(self):
        assert parse_salary_threshold("80K USD") == (80000, "USD")

    def test_lowercase_suffix(self):
        assert parse_salary_threshold("120k usd") == (120000, "USD")

    def test_dollar_symbol(self):
        assert parse_salary_threshold("$120K") == (120000, "USD")

    def test_lpa_is_inr_with_lakh_multiplier(self):
        assert parse_salary_threshold("25 LPA") == (2_500_000, "INR")

    def test_lakh_word(self):
        assert parse_salary_threshold("18 lakh") == (1_800_000, "INR")

    def test_million(self):
        assert parse_salary_threshold("1.5M INR") == (1_500_000, "INR")

    def test_eur_symbol(self):
        assert parse_salary_threshold("€60k") == (60000, "EUR")

    def test_gbp(self):
        assert parse_salary_threshold("£45k") == (45000, "GBP")

    def test_plain_number_no_currency(self):
        assert parse_salary_threshold("80000") == (80000, None)

    def test_commas(self):
        assert parse_salary_threshold("$1,20,000") == (120000, "USD")

    def test_conflicting_currencies(self):
        with pytest.raises(ValueError):
            parse_salary_threshold("$100 EUR")

    def test_unknown_currency(self):
        with pytest.raises(ValueError):
            parse_salary_threshold("80K YEN")

    def test_garbage(self):
        with pytest.raises(ValueError):
            parse_salary_threshold("lots of money")

    def test_zero_or_empty(self):
        with pytest.raises(ValueError):
            parse_salary_threshold("0 USD")
        with pytest.raises(ValueError):
            parse_salary_threshold("  ")


class TestMeetsSalaryThreshold:
    def test_no_thresholds_keeps_all(self):
        post = _post([_fig("USD", 50000, 70000)])
        assert meets_salary_threshold(post, None, None) is True

    def test_min_salary_range_top_reaches(self):
        post = _post([_fig("USD", 70000, 90000)])
        assert meets_salary_threshold(post, (80000, "USD"), None) is True

    def test_min_salary_range_top_too_low(self):
        post = _post([_fig("USD", 50000, 70000)])
        assert meets_salary_threshold(post, (80000, "USD"), None) is False

    def test_min_salary_currency_mismatch_drops(self):
        post = _post([_fig("USD", 50000, 70000)])
        assert meets_salary_threshold(post, (50000, "INR"), None) is False

    def test_min_salary_currency_agnostic_matches_any(self):
        post = _post([_fig("EUR", 60000, 80000)])
        assert meets_salary_threshold(post, (75000, None), None) is True

    def test_max_salary_bottom_within(self):
        post = _post([_fig("USD", 50000, 70000)])
        assert meets_salary_threshold(post, None, (60000, "USD")) is True

    def test_max_salary_bottom_too_high(self):
        post = _post([_fig("USD", 120000, 150000)])
        assert meets_salary_threshold(post, None, (100000, "USD")) is False

    def test_unknown_salary_always_kept(self):
        post = _post()
        assert meets_salary_threshold(post, (200000, "USD"),
                                      (1000, "USD")) is True

    def test_errored_posting_kept(self):
        post = _post(error="fetch failed")
        assert meets_salary_threshold(post, (200000, "USD"),
                                      (1000, "USD")) is True

    def test_any_matching_figure_passes(self):
        post = _post([_fig("USD", 40000, 50000), _fig("USD", 80000, 100000)])
        assert meets_salary_threshold(post, (90000, "USD"), None) is True

    def test_inr_lpa_figures(self):
        post = _post([_fig("INR", 1_800_000, 2_200_000)])
        assert meets_salary_threshold(post, (2_000_000, "INR"),
                                      None) is True
        assert meets_salary_threshold(post, (2_500_000, "INR"),
                                      None) is False


class TestApplyFiltersSalary:
    def test_filters_through_apply_filters(self):
        high = _post([_fig("USD", 90000, 120000)])
        low = _post([_fig("USD", 40000, 55000)])
        kept = apply_filters([high, low], salary_min="80K USD")
        assert kept == [high]

    def test_max_salary_filter(self):
        cheap = _post([_fig("USD", 40000, 55000)])
        pricey = _post([_fig("USD", 120000, 150000)])
        kept = apply_filters([cheap, pricey], salary_max="$100K")
        assert kept == [cheap]

    def test_invalid_threshold_raises(self):
        with pytest.raises(ValueError):
            apply_filters([_post()], salary_min="not a salary")

    def test_combines_with_other_filters(self):
        post = _post([_fig("USD", 90000, 120000)])
        post.title = "Junior Clerk"
        kept = apply_filters([post], salary_min="80K USD",
                             keyword_filter="engineer")
        assert kept == []
