"""Tests for the HTML report writer."""

import html as html_lib
import xml.etree.ElementTree as ET

from jobscraper.cli import build_parser
from jobscraper.models import MatchResult, Posting, Section
from jobscraper.reporting import write_html, write_jsonl, write_rss


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


def _feed(posts, profile=None, tmp_path=None):
    assert tmp_path is not None
    out = tmp_path / "feed.xml"
    write_rss(posts, str(out), profile or {"name": "Test User"})
    return out.read_text(encoding="utf-8")


def test_rss_is_well_formed_xml(tmp_path):
    body = _feed([_post("Junior Role", 30), _post("Senior Role", 90)],
                 tmp_path=tmp_path)
    root = ET.fromstring(body)
    assert root.tag == "rss"
    items = root.find("channel").findall("item")
    assert len(items) == 2
    titles = [it.findtext("title") for it in items]
    assert "[90] Senior Role" in titles[0]
    assert titles[0].index("Senior Role") >= 0


def test_rss_ranks_highest_score_first(tmp_path):
    body = _feed([_post("Junior Role", 30), _post("Senior Role", 90)],
                 tmp_path=tmp_path)
    assert body.index("Senior Role") < body.index("Junior Role")


def test_rss_escapes_untrusted_content(tmp_path):
    evil = "<script>alert('xss')</script><img src=x onerror=alert(1)>"
    post = _post(evil, 60)
    post.requirements = [Section(heading=evil, text=evil)]
    post.salary_hits = [evil]
    body = _feed([post], tmp_path=tmp_path)
    ET.fromstring(body)
    assert evil not in body
    assert html_lib.escape(evil, quote=True) in body


def test_rss_pubdate_from_age_and_empty_feed(tmp_path):
    body = _feed([_post("Aged Role", 70, age_days=3),
                  _post("Unknown Age", 60)],
                 tmp_path=tmp_path)
    root = ET.fromstring(body)
    items = root.find("channel").findall("item")
    assert items[0].findtext("pubDate") is not None
    assert items[1].findtext("pubDate") is None
    empty = _feed([], tmp_path=tmp_path)
    assert len(ET.fromstring(empty).find("channel").findall("item")) == 0


def test_rss_marks_new_postings_and_categories(tmp_path):
    body = _feed([_post("Fresh Role", 80, is_new=True,
                        seniority="senior")],
                 tmp_path=tmp_path)
    root = ET.fromstring(body)
    cats = [c.text for c in root.find("channel").find("item").findall(
        "category")]
    assert "Acme" in cats
    assert "senior" in cats
    assert "new" in cats


def test_rss_skips_unscored_postings(tmp_path):
    body = _feed([_post("Scored", 70), _post("Plain", None)],
                 tmp_path=tmp_path)
    assert len(ET.fromstring(body).find("channel").findall("item")) == 1


def test_cli_accepts_rss_flag():
    args = build_parser().parse_args(["https://example.com/j/1",
                                      "--rss", "feed.xml"])
    assert args.rss == "feed.xml"


def _feed_jsonl(posts, profile=None, tmp_path=None):
    assert tmp_path is not None
    out = tmp_path / "results.jsonl"
    write_jsonl(posts, str(out), profile or {"name": "Test User"})
    return out


def test_jsonl_one_record_per_line(tmp_path):
    import json

    out = _feed_jsonl([_post("Role A", 80), _post("Role B", 40)],
                      tmp_path=tmp_path)
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    records = [json.loads(line) for line in lines]
    assert all(isinstance(rec, dict) for rec in records)
    assert {rec["title"] for rec in records} == {"Role A", "Role B"}


def test_jsonl_ranks_highest_score_first(tmp_path):
    import json

    out = _feed_jsonl([_post("Junior Role", 30), _post("Senior Role", 90)],
                      tmp_path=tmp_path)
    records = [json.loads(line)
               for line in out.read_text(encoding="utf-8").splitlines()]
    assert [rec["title"] for rec in records] == ["Senior Role", "Junior Role"]
    assert [rec["rank"] for rec in records] == [1, 2]
    assert [rec["match"]["total"] for rec in records] == [90, 30]


def test_jsonl_includes_full_record_and_sections(tmp_path):
    import json

    post = _post("Role A", 80, company="Acme")
    post.requirements = [Section(heading="Must have", text="Splunk")]
    out = _feed_jsonl([post], tmp_path=tmp_path)
    record = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert record["company"] == "Acme"
    assert record["url"].startswith("https://example.com/")
    assert record["requirements"] == [{"heading": "Must have",
                                       "text": "Splunk"}]


def test_jsonl_preserves_unicode_and_handles_unscored(tmp_path):
    import json

    scored = _post("Büro München", 75)
    plain = _post("Plain", None)
    out = _feed_jsonl([plain, scored], tmp_path=tmp_path)
    records = [json.loads(line)
               for line in out.read_text(encoding="utf-8").splitlines()]
    assert records[0]["title"] == "Büro München"  # not ASCII-escaped
    assert records[1]["match"] is None  # unscored sorts last
    assert records[1]["rank"] == 2


def test_jsonl_empty_feed(tmp_path):
    out = _feed_jsonl([], tmp_path=tmp_path)
    assert out.read_text(encoding="utf-8") == ""


def test_cli_accepts_jsonl_flag():
    args = build_parser().parse_args(["https://example.com/j/1",
                                      "--jsonl", "results.jsonl"])
    assert args.jsonl == "results.jsonl"
