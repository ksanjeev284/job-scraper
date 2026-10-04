"""Report writers: ranked Markdown and CSV exports."""

from __future__ import annotations

import csv
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
