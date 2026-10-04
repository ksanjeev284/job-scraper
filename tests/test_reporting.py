"""Tests for the HTML report writer."""

import html as html_lib

from jobscraper.cli import build_parser
from jobscraper.models import MatchResult, Posting, Section
from jobscraper.reporting import write_html


def _post(title, score=50, company="Acme", location="Hyderabad",
          error=None, **kw):
    post = Posting(url=f"https://example.com/{company}/{title}", error=error)
    post.company, post.title, post.location = company, title, location
    if score is not None and error is None:
        post.match = MatchResult(
            total=score,
            breakdown={"technical_skills": 20, "experience": 15},
            weights={"technical_skills": 40, "experience": 25},
            matched_skills=["Splunk"],
            skill_gaps=["Kubernetes"],
        )
    for key, value in kw.items():
        setattr(post, key, value)
    return post


def _render(posts, profile=None, tmp_path=None):
    assert tmp_path is not None
    out = tmp_path / "report.html"
    write_html(posts, str(out), profile or {"name": "Test User"})
    return out.read_text(encoding="utf-8")


def test_html_ranks_highest_score_first(tmp_path):
    body = _render([_post("Junior Role", 30), _post("Senior Role", 90)],
                   tmp_path=tmp_path)
    assert body.index("Senior Role") < body.index("Junior Role")
    assert "Senior Role" in body and "Junior Role" in body


def test_html_is_self_contained(tmp_path):
    body = _render([_post("Role", 70)], tmp_path=tmp_path)
    assert body.startswith("<!DOCTYPE html>")
    assert "<style>" in body
    assert "cdn" not in body.lower()
    assert "http" not in body.replace(
        "https://example.com", "").replace("<!DOCTYPE html>", "")


def test_html_escapes_untrusted_content(tmp_path):
    evil = "<script>alert('xss')</script><img src=x onerror=alert(1)>"
    post = _post(evil, 60)
    post.requirements = [Section(heading=evil, text=evil)]
    post.salary_hits = [evil]
    body = _render([post], tmp_path=tmp_path)
    assert evil not in body
    assert html_lib.escape(evil, quote=True) in body


def test_html_error_posting_renders_without_match(tmp_path):
    body = _render([_post("Dead Role", error="HTTP 404")],
                   tmp_path=tmp_path)
    assert "ERROR" in body
    assert "Dead Role" in body


def test_html_empty_posts(tmp_path):
    body = _render([], tmp_path=tmp_path)
    assert "No postings to rank." in body
    assert "</html>" in body


def test_html_shows_new_badge_and_live_state(tmp_path):
    body = _render([_post("Role", 80, is_new=True, is_live=True,
                          live_reason="HTTP 200, posting still listed")],
                   tmp_path=tmp_path)
    assert "NEW" in body
    assert "LIVE" in body


def test_html_shows_sections_and_signals(tmp_path):
    post = _post("Role", 55)
    post.benefits = [Section(heading="Perks", text="free lunch")]
    post.signals = {"sponsorship_mentioned": True,
                    "work_mode": ["remote"]}
    post.salary_hits = ["$100k"]
    body = _render([post], tmp_path=tmp_path)
    assert "free lunch" in body
    assert "sponsorship" in body
    assert "remote" in body
    assert "$100k" in body


def test_cli_accepts_html_flag():
    args = build_parser().parse_args(["https://example.com/j/1",
                                      "--html", "report.html"])
    assert args.html == "report.html"
