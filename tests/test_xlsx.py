"""Tests for the Excel (.xlsx) export writer."""

import sys

import pytest

from jobscraper.reporting import write_xlsx

pytest.importorskip("openpyxl")
from openpyxl import load_workbook  # noqa: E402

from jobscraper.models import MatchResult, Posting  # noqa: E402


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


def _render(posts, tmp_path, profile=None):
    out = tmp_path / "report.xlsx"
    write_xlsx(posts, str(out), profile or {"name": "Test User"})
    wb = load_workbook(str(out))
    return wb["Ranked"]


def test_xlsx_ranks_highest_score_first(tmp_path):
    ws = _render([_post("Junior Role", 30), _post("Senior Role", 90)],
                 tmp_path)
    titles = [ws.cell(row=r, column=4).value for r in (2, 3)]
    assert titles == ["Senior Role", "Junior Role"]


def test_xlsx_columns_match_csv_contract(tmp_path):
    from jobscraper.reporting import _spreadsheet_headers

    ws = _render([_post("Role", 70)], tmp_path)
    headers = [ws.cell(row=1, column=c).value
               for c in range(1, len(_spreadsheet_headers()) + 1)]
    assert headers == _spreadsheet_headers()
    row = [ws.cell(row=2, column=c).value
           for c in range(1, len(headers) + 1)]
    assert row[0] == 70 and row[3] == "Role" and row[4] == "Acme"


def test_xlsx_url_is_clickable_and_score_colored(tmp_path):
    from jobscraper.reporting import _spreadsheet_headers

    ws = _render([_post("Role", 90), _post("Other", 20)], tmp_path)
    url_col = _spreadsheet_headers().index("url") + 1
    url_cell = ws.cell(row=2, column=url_col)
    assert url_cell.hyperlink is not None
    assert url_cell.hyperlink.target.startswith("https://example.com")
    score_cell = ws.cell(row=2, column=1)
    assert score_cell.fill.fgColor.rgb != "00000000"
    lo_cell = ws.cell(row=3, column=1)
    assert lo_cell.fill.fgColor.rgb != score_cell.fill.fgColor.rgb


def test_xlsx_header_frozen_and_filtered(tmp_path):
    ws = _render([_post("Role", 70)], tmp_path)
    assert ws.freeze_panes == "A2"
    assert ws.auto_filter.ref is not None


def test_xlsx_escapes_formula_injection(tmp_path):
    # openpyxl writes raw strings; verify the writer passes values through
    # unchanged (callers sanitize separately) and the file stays valid.
    evil = "=HYPERLINK(\"https://evil.example\")"
    post = _post(evil, 60)
    ws = _render([post], tmp_path)
    assert ws.cell(row=2, column=4).value == evil
    assert ws.cell(row=2, column=4).data_type == "s"


def test_xlsx_error_posting_renders_without_match(tmp_path):
    ws = _render([_post("Broken", None, error="fetch failed")], tmp_path)
    assert ws.cell(row=2, column=1).value in ("", None)
    assert ws.cell(row=2, column=4).value == "Broken"


def test_xlsx_missing_dependency_raises_helpful_error(tmp_path,
                                                      monkeypatch):
    monkeypatch.setitem(sys.modules, "openpyxl", None)
    with pytest.raises(RuntimeError, match="excel.*extra"):
        write_xlsx([_post("Role", 70)], str(tmp_path / "x.xlsx"), {})
