"""Candidate-profile match scoring.

Scores a posting 0-100 against a candidate profile JSON:

- technical_skills 35 — posting skills matched against the profile
- experience 25 — profile years vs the years the posting asks for
- seniority 15 — title seniority vs profile level
- certifications 10 — profile certs mentioned vs missing required certs
- location 5 — preferred locations
- role_relevance 5 — role tier keywords (configurable per profile)
- compensation 5 — listed salary vs current CTC

The profile is plain JSON (see ``examples/``); pass ``--profile`` to use
your own. Role relevance tiers come from the profile's ``role_tiers``
(tier1/tier2/tier3 keyword lists) so the scorer works for any profession;
profiles without ``role_tiers`` fall back to security-role defaults.
Nothing personal is bundled.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from jobscraper.models import MatchResult, Posting

CERTS_LACKED_RE = re.compile(
    r"\b(OSCP|OSCE|CISSP|CISM|CISA|CEH|GSEC|GCIA|GCIH|GPEN|GXPN|"
    r"Security\+|CySA\+|CASP|SC-200|SC-100|AZ-500|GCLD|CCSP)\b")

# Default tiers for security roles; any profile can override with its own
# "role_tiers": {"tier1": [...], "tier2": [...], "tier3": [...]} keyword lists.
DEFAULT_ROLE_TIERS = {
    "tier1": ["siem", "splunk", "detection engineer", "security engineer",
              "detection & response", "threat detection"],
    "tier2": ["soc", "incident response", "threat hunt", "blue team",
              "vulnerability management", "security operation",
              "threat intel", "cloud security"],
    "tier3": ["appsec", "application security", "product security",
              "devsecops", "api security"],
}


def _tier_regex(profile: dict, tier: str) -> re.Pattern | None:
    tiers = profile.get("role_tiers")
    if tiers is None:  # no role_tiers key: security-role defaults
        keywords = DEFAULT_ROLE_TIERS[tier]
    else:  # explicit (possibly empty) keyword lists: honor them
        keywords = tiers.get(tier) or []
    if not keywords:
        return None
    return re.compile("|".join(re.escape(k) for k in keywords), re.I)

def load_profile(path: str | None) -> dict:
    """Load a candidate profile JSON file (or the neutral template)."""
    if path:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    import os
    template = os.path.join(os.path.dirname(__file__), "..", "..",
                            "examples", "template.json")
    with open(os.path.normpath(template), encoding="utf-8") as fh:
        return json.load(fh)


RELATIVE_DATE_RE = re.compile(
    r"(\d+)\s*(minute|hour|day|week|month|year)s?\s+ago", re.I)


def posting_age_days(posted: str | None) -> int | None:
    """Parse a posted-date string into days-old; None if unparseable.

    Handles ISO dates, common absolute formats, and relative labels like
    "3 days ago" / "2 weeks ago" (as LinkedIn's guest API returns).
    """
    if not posted:
        return None
    text = str(posted).strip()
    lowered = text.lower()
    if lowered in ("today", "just now", "just posted"):
        return 0
    if lowered == "yesterday":
        return 1
    rel = RELATIVE_DATE_RE.search(lowered)
    if rel:
        n, unit = int(rel.group(1)), rel.group(2).lower()
        mult = {"minute": 0, "hour": 0, "day": 1, "week": 7,
                "month": 30, "year": 365}[unit]
        return n * mult
    text2 = re.sub(r"(\.\d+)?(Z)$", r"\1+0000", text)
    fmts = ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y/%m/%d",
            "%d %b %Y", "%b %d, %Y", "%Y-%m-%dT%H:%M%z")
    for fmt in fmts:
        try:
            dt = datetime.strptime(text2, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt).days
        except ValueError:
            continue
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        try:
            dt = datetime(int(match.group(1)), int(match.group(2)),
                          int(match.group(3)), tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt).days
        except ValueError:
            pass
    return None


def score_posting(post: Posting, profile: dict) -> MatchResult:
    """Score a posting 0-100 against the candidate profile."""
    title = (post.title or "").lower()
    loc = (post.location or "").lower()
    text = " ".join(s.text for s in post.sections).lower()
    req_text = " ".join(s.text for s in post.requirements).lower() or text

    profile_skills = {s.lower() for s in profile.get("skills", [])}
    posting_skills = {s.lower() for s in post.skills_found}
    # skill_aliases: {"SIEM": ["Splunk ES", "QRadar"]} lets a posting that
    # names a specific tool count toward the broader profile skill
    aliases: dict[str, set[str]] = {}
    for canonical, variants in (profile.get("skill_aliases") or {}).items():
        aliases[canonical.lower()] = {v.lower() for v in variants}
    matched = sorted(
        s for s in posting_skills
        if s in profile_skills
        or any(s in aliases.get(p, set()) for p in profile_skills)
    )
    gaps = sorted(posting_skills - set(matched) - profile_skills)
    profile_certs = {c.lower() for c in profile.get("certs", [])}
    pref_locs = [loc.lower() for loc in profile.get("locations", [])]
    years = float(profile.get("years_total", 0))

    breakdown: dict[str, int] = {}

    breakdown["technical_skills"] = min(35, len(matched) * 5)

    exp = post.experience_years_mentioned
    if not exp:
        breakdown["experience"] = 15
    else:
        lo, hi = min(exp), max(exp)
        if lo - 1 <= years <= hi + 1:
            breakdown["experience"] = 25
        elif lo - 2 <= years <= hi + 2:
            breakdown["experience"] = 15
        else:
            breakdown["experience"] = 5

    if re.search(r"(manager|director|head of|vp|chief)", title):
        breakdown["seniority"] = 3
    elif re.search(r"(senior|sr\.|lead|staff|principal)", title):
        breakdown["seniority"] = 12 if breakdown["experience"] >= 15 else 8
    elif re.search(r"(junior|jr\.|intern|trainee|\bl1\b)", title):
        breakdown["seniority"] = 8
    else:
        breakdown["seniority"] = 10

    if any(c in text for c in profile_certs):
        breakdown["certifications"] = 10
    elif CERTS_LACKED_RE.search(req_text):
        breakdown["certifications"] = 3
    else:
        breakdown["certifications"] = 6

    home = pref_locs[0] if pref_locs else ""
    if home and home in loc:
        breakdown["location"] = 5
    elif any(c in loc for c in pref_locs):
        breakdown["location"] = 4
    elif loc and "remote" in loc:
        breakdown["location"] = 4
    else:
        breakdown["location"] = 3

    tier1 = _tier_regex(profile, "tier1")
    tier2 = _tier_regex(profile, "tier2")
    tier3 = _tier_regex(profile, "tier3")
    if tier1 and tier1.search(title):
        breakdown["role_relevance"] = 5
    elif tier2 and tier2.search(title):
        breakdown["role_relevance"] = 4
    elif tier3 and tier3.search(title):
        breakdown["role_relevance"] = 2
    else:
        breakdown["role_relevance"] = 2

    current_ctc = float(profile.get("current_ctc_lpa", 0))
    lpa = [float(m.group(1)) for m in
           re.finditer(r"([\d.]+)\s*(?:lakh|LPA)",
                       " ".join(post.salary_hits), re.I)]
    if lpa and max(lpa) >= current_ctc and current_ctc > 0:
        breakdown["compensation"] = 5
    elif post.salary_hits:
        breakdown["compensation"] = 3
    else:
        breakdown["compensation"] = 3

    return MatchResult(total=sum(breakdown.values()),
                       breakdown=breakdown,
                       matched_skills=matched,
                       skill_gaps=gaps)


def fit_summary(post: Posting, profile: dict) -> list[str]:
    """Short 'why you fit / watch out' bullets for the application."""
    bullets: list[str] = []
    match = post.match
    if match and match.matched_skills:
        bullets.append("Fit: " + ", ".join(match.matched_skills[:8]))
    exp = post.experience_years_mentioned
    if exp:
        years_total = profile.get("years_total")
        have = f"{years_total}" if years_total is not None else "?"
        bullets.append(f"Asks {min(exp)}-{max(exp)} yrs; you have {have}")
    if match and match.skill_gaps:
        bullets.append("Watch: " + ", ".join(match.skill_gaps[:5]))
    if post.signals.get("german_required"):
        bullets.append("Watch: German required")
    if post.signals.get("sponsorship_mentioned"):
        bullets.append("Note: sponsorship mentioned in posting")
    if post.age_days is not None:
        bullets.append(f"Posted {post.age_days}d ago" + (
            " (fresh)" if post.age_days <= 14 else
            " (stale)" if post.age_days > 60 else ""))
    if post.tracker_status == "applied":
        bullets.append("Already applied (tracker)")
    return bullets
