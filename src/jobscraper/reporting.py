"""Report writers: ranked Markdown, CSV, and HTML exports."""

from __future__ import annotations

import csv
import html as html_lib
from datetime import datetime, timezone

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


def write_csv(posts: list[Posting], path: str, profile: dict) -> None:
    """Write the spreadsheet-friendly CSV, best score first."""
    ordered = sorted(posts,
                     key=lambda p: p.match.total if p.match else -1,
                     reverse=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["score", "live", "age_days", "title", "company",
                         "location", "type", "exp_years", "salary",
                         "matched_skills", "skill_gaps", "fit_summary",
                         "benefits",
                         "sponsorship", "german_required", "work_mode",
                         "tracker", "url"])
        for post in ordered:
            match = post.match
            live = "yes" if post.is_live else (
                "no" if post.is_live is False else "unknown")
            writer.writerow([
                match.total if match else "",
                live,
                post.age_days if post.age_days is not None else "",
                post.title or "",
                post.company or "",
                post.location or "",
                post.employment_type or "",
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
                post.url,
            ])


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
                     "<th>Location</th><th>Gaps</th></tr>")
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
                f"<td>{gaps}</td></tr>")
        parts.append("</table>")
    else:
        parts.append("<p class=\"meta\">No postings to rank.</p>")

    for idx, post in enumerate(posts):
        parts.append(f"<div class=\"card\" id=\"p{idx}\">")
        parts.append(f"<h2>{_esc(post.title)}</h2>")
        meta = [f"Company: {_esc(post.company)}",
                f"Location: {_esc(post.location)}",
                f"Type: {_esc(post.employment_type)}"]
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
