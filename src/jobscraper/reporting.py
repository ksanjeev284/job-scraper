"""Report writers: ranked Markdown, CSV, HTML, Excel, RSS, JSONL, SQLite."""

from __future__ import annotations

import csv
import html as html_lib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from jobscraper.models import Posting
from jobscraper.scoring import fit_summary


def _ranked(posts: list[Posting]) -> list[Posting]:
    return sorted((p for p in posts if p.match),
                  key=lambda p: p.match.total, reverse=True)


def write_markdown(posts: list[Posting], path: str, profile: dict) -> None:
    """Write the human-readable ranked report."""
    lines = [f"# Job scrape report — "
             f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}\n"]
    ranked = _ranked(posts)
    if ranked:
        lines.append("## Ranked by match score\n")
        lines.append("| Score | Live | Age | Title | Company | Location | "
                     "Gaps |")
        lines.append("|---|---|---|---|---|---|---|")
        new_posts = [p for p in ranked if p.is_new]
        if new_posts:
            lines.append(f"**{len(new_posts)} new since last run**\n")
        for post in ranked:
            assert post.match is not None
            gaps = ", ".join(post.match.skill_gaps[:4]) or "-"
            live = "yes" if post.is_live else (
                "no" if post.is_live is False else "?")
            age = f"{post.age_days}d" if post.age_days is not None else "-"
            new = " NEW" if post.is_new else ""
            lines.append(f"| {post.match.total} | {live} | {age} | "
                         f"{post.title or '?'}{new} | {post.company or '?'} | "
                         f"{post.location or '?'} | {gaps} |")
        lines.append("")
    for post in posts:
        lines.append(f"## {post.title or '(no title)'}")
        lines.append(f"- Company: {post.company or '?'}")
        lines.append(f"- Location: {post.location or '?'}")
        lines.append(f"- Type: {post.employment_type or '?'}")
        lines.append(f"- URL: {post.url}")
        if post.is_live is True:
            lines.append(f"- Status: LIVE ({post.live_reason})")
        elif post.is_live is False:
            lines.append(f"- Status: CLOSED/DEAD ({post.live_reason})")
        else:
            lines.append(f"- Status: unknown ({post.live_reason or 'n/a'})")
        if post.low_content_warning:
            lines.append(f"- WARNING: {post.low_content_warning}")
        if post.tracker_status:
            lines.append(f"- Tracker: {post.tracker_status}")
        if post.age_days is not None:
            lines.append(f"- Posted: {post.age_days} days ago")
        if post.error:
            lines.append(f"- ERROR: {post.error}\n")
            continue
        if post.match:
            match = post.match
            brk = match.breakdown
            wts = match.weights or {}
            parts = []
            for key, label in (("technical_skills", "skills"),
                               ("experience", "exp"),
                               ("seniority", "seniority"),
                               ("certifications", "certs"),
                               ("location", "loc"),
                               ("role_relevance", "role"),
                               ("compensation", "comp")):
                denom = wts.get(key)
                val = (f"{brk.get(key, 0)}/{denom:g}"
                       if denom else str(brk.get(key, 0)))
                parts.append(f"{label} {val}")
            total_denom = (f"{sum(wts.values()):g}" if wts else "100")
            lines.append(f"- MATCH SCORE: {match.total}/{total_denom} "
                         f"({', '.join(parts)})")
            lines.append(f"- Matched skills: "
                         f"{', '.join(match.matched_skills) or 'none'}")
            lines.append(f"- Skill gaps: "
                         f"{', '.join(match.skill_gaps) or 'none'}")
        for bullet in fit_summary(post, profile):
            lines.append(f"- {bullet}")
        if post.signals.get("sponsorship_mentioned"):
            lines.append("- Note: sponsorship / work-permit mentioned")
        if post.signals.get("german_required"):
            lines.append("- Note: German language required")
        if post.signals.get("work_mode"):
            lines.append(f"- Work mode: {', '.join(post.signals['work_mode'])}")
        if post.salary_hits:
            lines.append(f"- Salary found: {', '.join(post.salary_hits)}")
        if post.salary_normalized:
            def fmt(n):
                hi = (f"-{n['max_annual']:,}"
                      if n['max_annual'] != n['min_annual'] else "")
                return f"{n['currency']} {n['min_annual']:,}{hi}/yr"
            norm = "; ".join(fmt(n) for n in post.salary_normalized)
            lines.append(f"- Salary normalized: {norm}")
        lines.append(f"- Skills detected: "
                     f"{', '.join(post.skills_found) or 'none'}")
        exp = post.experience_years_mentioned
        lines.append(f"- Experience mentioned: "
                     f"{', '.join(map(str, exp)) + ' yrs' if exp else '?'}")
        for bucket_title, bucket in (
                ("Requirements", post.requirements),
                ("Responsibilities", post.responsibilities),
                ("Nice to have", post.nice_to_have),
                ("Benefits", post.benefits)):
            if bucket:
                lines.append(f"\n### {bucket_title}")
                for sec in bucket:
                    lines.append(f"**{sec.heading}**\n{sec.text}\n")
        if post.other_possibly_relevant:
            lines.append("### Other possibly relevant sections")
            for sec in post.other_possibly_relevant:
                lines.append(f"**{sec.heading}**\n{sec.text[:1500]}\n")
        lines.append("---\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def _spreadsheet_headers() -> list[str]:
    """Column headers shared by the CSV and Excel exports."""
    return ["score", "live", "age_days", "title", "company", "location",
            "type", "seniority", "exp_years", "salary", "matched_skills",
            "skill_gaps", "fit_summary", "benefits", "sponsorship",
            "german_required", "work_mode", "tracker", "applied", "url"]


def _spreadsheet_row(post: Posting, profile: dict) -> list[object]:
    """One export row for a posting; empty strings mark unknown states."""
    match = post.match
    live = ("yes" if post.is_live
            else "no" if post.is_live is False else "unknown")
    return [
        match.total if match else "",
        live,
        post.age_days if post.age_days is not None else "",
        post.title or "",
        post.company or "",
        post.location or "",
        post.employment_type or "",
        post.seniority or "",
        ",".join(map(str, post.experience_years_mentioned)),
        "; ".join(post.salary_hits),
        "; ".join(match.matched_skills) if match else "",
        "; ".join(match.skill_gaps) if match else "",
        "; ".join(fit_summary(post, profile)),
        "; ".join(s.heading for s in post.benefits),
        "yes" if post.signals.get("sponsorship_mentioned") else "",
        "yes" if post.signals.get("german_required") else "",
        ",".join(post.signals.get("work_mode") or []),
        post.tracker_status or "",
        post.application_status or "",
        post.url,
    ]


def _export_order(posts: list[Posting]) -> list[Posting]:
    return sorted(posts,
                  key=lambda p: p.match.total if p.match else -1,
                  reverse=True)


def write_csv(posts: list[Posting], path: str, profile: dict) -> None:
    """Write the spreadsheet-friendly CSV, best score first."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(_spreadsheet_headers())
        for post in _export_order(posts):
            writer.writerow(_spreadsheet_row(post, profile))


_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _neutralize_formulas(ws) -> None:
    """Mark formula-looking text cells as plain strings.

    Spreadsheet apps auto-evaluate cells starting with =, +, -, @, so
    force openpyxl's ``s`` (string) type to keep untrusted posting text
    inert when the workbook is opened.
    """
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            if (cell.data_type == "f" and isinstance(cell.value, str)
                    and cell.value[:1] in _FORMULA_PREFIXES):
                cell.data_type = "s"


def write_xlsx(posts: list[Posting], path: str, profile: dict) -> None:
    """Write the ranked spreadsheet as .xlsx, best score first.

    One sheet, styled header, frozen header row, autofilter, clickable
    posting URLs, and score cells colored green/amber/red by band.
    Needs the optional ``excel`` extra (openpyxl); raises RuntimeError
    with the install hint when it is missing.
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise RuntimeError(
            "Excel export needs the optional 'excel' extra: "
            "pip install jobscraper[excel]"
        ) from exc

    headers = _spreadsheet_headers()
    rows = [_spreadsheet_row(p, profile) for p in _export_order(posts)]
    url_col = headers.index("url") + 1
    score_col = headers.index("score") + 1

    wb = Workbook()
    ws = wb.active
    ws.title = "Ranked"

    head_fill = PatternFill("solid", fgColor="1F2937")
    head_font = Font(bold=True, color="FFFFFF")
    fills = {
        "hi": PatternFill("solid", fgColor="D1E7DD"),
        "mid": PatternFill("solid", fgColor="FFF3CD"),
        "lo": PatternFill("solid", fgColor="F8D7DA"),
    }

    ws.append(headers)
    for cell in ws[1]:
        cell.fill = head_fill
        cell.font = head_font
        cell.alignment = Alignment(vertical="top", wrap_text=False)
    for row in rows:
        ws.append(row)
    for data_row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        score = data_row[score_col - 1].value
        if isinstance(score, (int, float)):
            band = "hi" if score >= 75 else "mid" if score >= 50 else "lo"
            data_row[score_col - 1].fill = fills[band]
        url_cell = data_row[url_col - 1]
        if url_cell.value:
            url_cell.hyperlink = url_cell.value
            url_cell.font = Font(color="0969DA", underline="single")
            url_cell.alignment = Alignment(vertical="top")
    widths = [8, 10, 9, 42, 28, 28, 18, 12, 24, 28, 28, 30, 30, 12, 15, 14,
              12, 60]
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    _neutralize_formulas(ws)
    wb.save(path)


_HTML_CSS = """
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
margin:2em auto;max-width:1100px;padding:0 1em;color:#1a1a1a;
background:#fafafa;line-height:1.5}
h1{font-size:1.6em}h2{font-size:1.25em;margin-top:2em;
border-bottom:2px solid #ddd;padding-bottom:.3em}
table.rank{border-collapse:collapse;width:100%;font-size:.9em}
table.rank th,table.rank td{border:1px solid #ddd;padding:.45em .6em;
text-align:left;vertical-align:top}
table.rank th{background:#f0f0f0}table.rank tr:nth-child(even){background:#fff}
.score{display:inline-block;min-width:2.6em;text-align:center;font-weight:700;
border-radius:6px;padding:.15em .5em;color:#fff}
.score.hi{background:#1a7f37}.score.mid{background:#9a6700}
.score.lo{background:#a40e26}
.new{display:inline-block;background:#ddf4ff;border:1px solid #54aeff;
border-radius:10px;padding:0 .5em;font-size:.8em;font-weight:700}
.live{color:#1a7f37;font-weight:700}.dead{color:#a40e26;font-weight:700}
.card{background:#fff;border:1px solid #ddd;border-radius:8px;
padding:1em 1.2em;margin:1em 0}
.chips span{display:inline-block;background:#eef;margin:.15em;padding:.1em .6em;
border-radius:12px;font-size:.85em}.chips .gap{background:#fee}
.bars div{margin:.2em 0;font-size:.9em}.bar{height:8px;background:#eee;
border-radius:4px;margin-top:.15em}.bar i{display:block;height:8px;
border-radius:4px;background:#2f81f7}
.meta{color:#555;font-size:.9em}.warn{color:#a40e26;font-weight:700}
a{color:#0969da}footer{margin-top:2em;color:#888;font-size:.8em}
"""


def _esc(value: object) -> str:
    """HTML-escape untrusted posting data so the report is XSS-safe."""
    if value is None:
        return "?"
    return html_lib.escape(str(value), quote=True)


def _score_class(total: float) -> str:
    if total >= 65:
        return "hi"
    if total >= 40:
        return "mid"
    return "lo"


def write_html(posts: list[Posting], path: str, profile: dict) -> None:
    """Write a self-contained ranked HTML report (inline CSS, no CDN).

    Every posting field is HTML-escaped: report files are often opened in
    browsers, and scraped description text is untrusted.
    """
    ranked = _ranked(posts)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    name = _esc(profile.get("name", "candidate"))
    new_posts = [p for p in ranked if p.is_new]

    parts: list[str] = [
        "<!DOCTYPE html>", "<html lang=\"en\"><head><meta charset=\"utf-8\">",
        f"<title>Job scrape report — {stamp}</title>",
        f"<style>{_HTML_CSS}</style></head><body>",
        f"<h1>Job scrape report — {stamp}</h1>",
        f"<p class=\"meta\">Profile: {name} · "
        f"{len(posts)} posting(s) scraped, {len(ranked)} scored, "
        f"{len(new_posts)} new since last run</p>",
    ]

    if ranked:
        parts.append("<h2>Ranked by match score</h2>")
        parts.append("<table class=\"rank\"><tr><th>Score</th><th>Live</th>"
                     "<th>Age</th><th>Title</th><th>Company</th>"
                     "<th>Location</th><th>Level</th><th>Applied</th>"
                     "<th>Gaps</th></tr>")
        for idx, post in enumerate(ranked):
            match = post.match
            assert match is not None
            live = ("<span class=\"live\">yes</span>" if post.is_live
                    else "<span class=\"dead\">no</span>"
                    if post.is_live is False else "?")
            age = f"{post.age_days}d" if post.age_days is not None else "-"
            gaps = ", ".join(_esc(g) for g in match.skill_gaps[:4]) or "-"
            new = " <span class=\"new\">NEW</span>" if post.is_new else ""
            parts.append(
                f"<tr><td><span class=\"score {_score_class(match.total)}\">"
                f"{match.total}</span></td><td>{live}</td><td>{age}</td>"
                f"<td><a href=\"#p{idx}\">{_esc(post.title)}</a>{new}</td>"
                f"<td>{_esc(post.company)}</td><td>{_esc(post.location)}</td>"
                f"<td>{_esc(post.seniority or '?')}</td>"
                f"<td>{_esc(post.application_status or '-')}</td>"
                f"<td>{gaps}</td></tr>")
        parts.append("</table>")
    else:
        parts.append("<p class=\"meta\">No postings to rank.</p>")

    for idx, post in enumerate(posts):
        parts.append(f"<div class=\"card\" id=\"p{idx}\">")
        parts.append(f"<h2>{_esc(post.title)}</h2>")
        meta = [f"Company: {_esc(post.company)}",
                f"Location: {_esc(post.location)}",
                f"Type: {_esc(post.employment_type)}",
                f"Seniority: {_esc(post.seniority or 'unknown')}"]
        if post.age_days is not None:
            meta.append(f"Posted: {post.age_days} days ago")
        parts.append(f"<p class=\"meta\">{' · '.join(meta)}</p>")
        parts.append(f"<p><a href=\"{_esc(post.url)}\">{_esc(post.url)}</a></p>")
        if post.is_live is True:
            parts.append(f"<p class=\"live\">LIVE — {_esc(post.live_reason)}</p>")
        elif post.is_live is False:
            parts.append(f"<p class=\"dead\">CLOSED/DEAD — "
                         f"{_esc(post.live_reason)}</p>")
        if post.low_content_warning:
            parts.append(f"<p class=\"warn\">WARNING: "
                         f"{_esc(post.low_content_warning)}</p>")
        if post.error:
            parts.append(f"<p class=\"warn\">ERROR: {_esc(post.error)}</p>")
            parts.append("</div>")
            continue
        if post.match:
            match = post.match
            brk, wts = match.breakdown, match.weights or {}
            denom = sum(wts.values()) if wts else 100
            parts.append(f"<p><span class=\"score "
                         f"{_score_class(match.total)}\">{match.total}</span> "
                         f"/ {denom:g}</p>")
            parts.append("<div class=\"bars\">")
            for key, label in (("technical_skills", "Skills"),
                               ("experience", "Experience"),
                               ("seniority", "Seniority"),
                               ("certifications", "Certs"),
                               ("location", "Location"),
                               ("role_relevance", "Role"),
                               ("compensation", "Comp")):
                denom_w = wts.get(key) or 1
                val = brk.get(key, 0)
                pct = max(0, min(100, 100 * val / denom_w))
                parts.append(f"<div>{label}: {val}/{denom_w:g}"
                             f"<div class=\"bar\"><i style=\"width:"
                             f"{pct:.0f}%\"></i></div></div>")
            parts.append("</div>")
            parts.append("<p class=\"chips\">Matched: " +
                         "".join(f"<span>{_esc(s)}</span>"
                                  for s in match.matched_skills)
                         + "</p>")
            parts.append("<p class=\"chips\">Gaps: " +
                         "".join(f"<span class=\"gap\">{_esc(s)}</span>"
                                  for s in match.skill_gaps) + "</p>")
        for bullet in fit_summary(post, profile):
            parts.append(f"<p>• {_esc(bullet)}</p>")
        if post.signals.get("sponsorship_mentioned"):
            parts.append("<p>Note: sponsorship / work-permit mentioned</p>")
        if post.signals.get("work_mode"):
            parts.append("<p>Work mode: " +
                         ", ".join(_esc(m)
                                   for m in post.signals["work_mode"]) + "</p>")
        if post.salary_hits:
            parts.append("<p>Salary found: " +
                         ", ".join(_esc(s) for s in post.salary_hits) + "</p>")
        for bucket_title, bucket in (
                ("Requirements", post.requirements),
                ("Responsibilities", post.responsibilities),
                ("Nice to have", post.nice_to_have),
                ("Benefits", post.benefits)):
            if bucket:
                parts.append(f"<h3>{bucket_title}</h3><ul>")
                for sec in bucket:
                    parts.append(f"<li><strong>{_esc(sec.heading)}</strong>: "
                                 f"{_esc(sec.text)}</li>")
                parts.append("</ul>")
        parts.append("</div>")

    parts.append("<footer>Generated by job-scraper. Scraped content is "
                 "untrusted; all fields are HTML-escaped in this report."
                 "</footer></body></html>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))


def _rss_pubdate(post: Posting) -> str | None:
    """RFC-2822 pubDate derived from the posting's age; None if unknown."""
    if post.age_days is None:
        return None
    dt = datetime.now(timezone.utc) - timedelta(days=post.age_days)
    return format_datetime(dt, usegmt=True)


def write_rss(posts: list[Posting], path: str, profile: dict) -> None:
    """Write the ranked results as an RSS 2.0 feed for feed readers.

    Item titles carry the match score ("[85] Senior Splunk Engineer -
    Acme"); item bodies summarize the fit with escaped posting data only.
    Unscored postings are skipped, as in the other ranked exports, and
    watch-mode newcomers get a ``new`` category. Every scraped field is
    XML-escaped: feed bodies are untrusted content.
    """
    ranked = _ranked(posts)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    name = _esc(profile.get("name", "candidate"))
    new_posts = [p for p in ranked if p.is_new]
    build_date = format_datetime(datetime.now(timezone.utc), usegmt=True)

    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<rss version=\"2.0\">",
        "<channel>",
        f"<title>job-scraper results — {name} — {stamp}</title>",
        "<link>https://github.com/ksanjeev284/job-scraper</link>",
        f"<description>{len(ranked)} posting(s) ranked by match score; "
        f"{len(new_posts)} new since last run.</description>",
        f"<lastBuildDate>{build_date}</lastBuildDate>",
        "<language>en</language>",
    ]
    for post in ranked:
        match = post.match
        assert match is not None
        title = post.title or "Untitled posting"
        company = post.company or "Unknown company"
        description = "; ".join(
            b for b in fit_summary(post, profile) if b)
        pubdate = _rss_pubdate(post)
        lines.append("<item>")
        lines.append(f"<title>[{match.total}] {_esc(title)} — "
                     f"{_esc(company)}</title>")
        lines.append(f"<link>{_esc(post.url)}</link>")
        lines.append(f"<guid isPermaLink=\"true\">{_esc(post.url)}</guid>")
        if pubdate:
            lines.append(f"<pubDate>{pubdate}</pubDate>")
        if description:
            lines.append(f"<description>{_esc(description)}</description>")
        lines.append(f"<category>{_esc(company)}</category>")
        if post.seniority:
            lines.append(f"<category>{_esc(post.seniority)}</category>")
        if post.is_new:
            lines.append("<category>new</category>")
        lines.append("</item>")
    lines += ["</channel>", "</rss>", ""]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def write_jsonl(posts: list[Posting], path: str, profile: dict) -> None:
    """Write the ranked results as JSON Lines: one full posting per line.

    Every line is one JSON object (UTF-8, non-ASCII preserved) carrying the
    full posting record plus a 1-based ``rank`` field, best score first.
    Unscored postings sort last with a null score. One record per line means
    the file streams through ``jq``/``grep`` and appends cleanly across runs
    (``>>``) without re-parsing a JSON array.
    """
    ranked = _export_order(posts)
    with open(path, "w", encoding="utf-8") as fh:
        for rank, post in enumerate(ranked, start=1):
            record = {"rank": rank}
            record.update(post.to_dict())
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    url TEXT PRIMARY KEY,
    title TEXT,
    company TEXT,
    location TEXT,
    employment_type TEXT,
    seniority TEXT,
    age_days INTEGER,
    score INTEGER,
    last_rank INTEGER,
    live INTEGER,
    salary TEXT,
    matched_skills TEXT,
    skill_gaps TEXT,
    fit_summary TEXT,
    benefits TEXT,
    description_chars INTEGER,
    is_new INTEGER,
    tracker_status TEXT,
    error TEXT,
    first_seen TEXT,
    last_seen TEXT,
    scrape_count INTEGER NOT NULL DEFAULT 1,
    raw_json TEXT
)
"""

_SQLITE_UPSERT = """
INSERT INTO postings (
    url, title, company, location, employment_type, seniority, age_days,
    score, last_rank, live, salary, matched_skills, skill_gaps,
    fit_summary, benefits, description_chars, is_new, tracker_status,
    error, first_seen, last_seen, scrape_count, raw_json
) VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?
)
ON CONFLICT(url) DO UPDATE SET
    title=excluded.title,
    company=excluded.company,
    location=excluded.location,
    employment_type=excluded.employment_type,
    seniority=excluded.seniority,
    age_days=excluded.age_days,
    score=excluded.score,
    last_rank=excluded.last_rank,
    live=excluded.live,
    salary=excluded.salary,
    matched_skills=excluded.matched_skills,
    skill_gaps=excluded.skill_gaps,
    fit_summary=excluded.fit_summary,
    benefits=excluded.benefits,
    description_chars=excluded.description_chars,
    is_new=excluded.is_new,
    tracker_status=excluded.tracker_status,
    error=excluded.error,
    last_seen=excluded.last_seen,
    scrape_count=postings.scrape_count + 1,
    raw_json=excluded.raw_json
"""


def _sqlite_row(post: Posting, rank: int, now: str,
                profile: dict) -> tuple:
    """Flatten one posting into a sqlite3 parameter tuple (best-first rank)."""
    match = post.match
    live = 1 if post.is_live else (0 if post.is_live is False else None)
    return (
        post.url,
        post.title,
        post.company,
        post.location,
        post.employment_type,
        post.seniority,
        post.age_days,
        match.total if match else None,
        rank,
        live,
        "; ".join(post.salary_hits),
        json.dumps(match.matched_skills if match else [],
                   ensure_ascii=False),
        json.dumps(match.skill_gaps if match else [],
                   ensure_ascii=False),
        "; ".join(fit_summary(post, profile)),
        "; ".join(s.heading for s in post.benefits),
        post.full_text_chars,
        1 if post.is_new else 0,
        post.tracker_status,
        post.error,
        now,
        now,
        json.dumps(post.to_dict(), ensure_ascii=False),
    )


def write_sqlite(posts: list[Posting], path: str, profile: dict) -> None:
    """Store the ranked results in a SQLite database (stdlib, no extras).

    Each posting is one row keyed by canonical URL. Re-running against the
    same database upserts instead of duplicating: ``first_seen`` keeps the
    original timestamp, ``last_seen`` advances, and ``scrape_count``
    increments, so the database becomes a queryable history across runs::

        SELECT title, company, score FROM postings
        WHERE live = 1 ORDER BY score DESC;

    ``score`` and ``last_rank`` reflect the most recent run (rank is 1-based,
    best score first, unscored postings last). List-valued fields are stored
    as JSON text; the full record lands in ``raw_json`` for ad-hoc queries.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    conn = sqlite3.connect(path)
    try:
        conn.execute(_SQLITE_SCHEMA)
        rows = [_sqlite_row(post, rank, now, profile)
                for rank, post in enumerate(_export_order(posts), start=1)]
        conn.executemany(_SQLITE_UPSERT, rows)
        conn.commit()
    finally:
        conn.close()
