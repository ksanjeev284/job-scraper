"""Tests for jobscraper.urls: URL canonicalization and input dedupe."""

from jobscraper.urls import canonicalize_url, input_dedupe


def test_strips_common_tracking_params():
    url = ("https://jobs.lever.co/acme/abc123?utm_source=linkedin"
           "&utm_medium=cpc&gclid=XYZ&fbclid=abc&msclkid=1")
    assert canonicalize_url(url) == "https://jobs.lever.co/acme/abc123"


def test_strips_linkedin_trk():
    url = ("https://www.linkedin.com/jobs/view/123456?trk=public_jobs"
           "&refId=abc")
    assert canonicalize_url(url) == "https://www.linkedin.com/jobs/view/123456"


def test_drops_fragment():
    assert (canonicalize_url("https://boards.greenhouse.io/acme/jobs/42#apply")
            == "https://boards.greenhouse.io/acme/jobs/42")


def test_lowercases_scheme_and_host():
    assert (canonicalize_url("HTTPS://Jobs.AshbyHQ.Com/acme/abc")
            == "https://jobs.ashbyhq.com/acme/abc")


def test_drops_default_ports():
    assert (canonicalize_url("https://example.com:443/jobs")
            == "https://example.com/jobs")
    assert (canonicalize_url("http://example.com:80/jobs")
            == "http://example.com/jobs")
    # Non-default port is kept.
    assert (canonicalize_url("https://example.com:8443/jobs")
            == "https://example.com:8443/jobs")


def test_strips_trailing_slash_but_keeps_root():
    assert canonicalize_url("https://example.com/jobs/") == \
        "https://example.com/jobs"
    assert canonicalize_url("https://example.com/") == "https://example.com/"


def test_path_case_preserved():
    # SmartRecruiters-style slugs can be case-sensitive in the path.
    assert (canonicalize_url("https://careers.smartrecruiters.com/"
                             "OracleCorporation/job-1")
            == "https://careers.smartrecruiters.com/OracleCorporation/job-1")


def test_keeps_legitimate_query_params_sorted():
    url = ("https://boards-api.greenhouse.io/v1/boards/acme/jobs"
           "?token=zzz&lang=en")
    assert canonicalize_url(url) == \
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs?lang=en&token=zzz"


def test_ats_api_urls_untouched():
    url = "https://api.lever.co/v0/postings/acme/abc-123"
    assert canonicalize_url(url) == url


def test_empty_and_non_url_input_pass_through():
    assert canonicalize_url("") == ""
    assert canonicalize_url("   ") == ""
    assert canonicalize_url("not a url") == "not a url"


def test_whitespace_trimmed():
    assert (canonicalize_url("  https://example.com/jobs  ")
            == "https://example.com/jobs")


def test_input_dedupe_collapses_tracking_variants():
    urls = [
        "https://jobs.lever.co/acme/abc123?utm_source=linkedin",
        "https://jobs.lever.co/acme/abc123?utm_source=indeed&trk=x",
        "https://jobs.lever.co/acme/other-job",
    ]
    assert input_dedupe(urls) == [urls[0], urls[2]]


def test_input_dedupe_keeps_order():
    urls = ["https://b.example/x", "https://a.example/y", "https://b.example/x"]
    assert input_dedupe(urls) == ["https://b.example/x", "https://a.example/y"]


def test_canonicalize_idempotent():
    url = "HTTPS://Example.COM:443/jobs/?utm_source=x#frag"
    once = canonicalize_url(url)
    assert canonicalize_url(once) == once
    assert once == "https://example.com/jobs"
