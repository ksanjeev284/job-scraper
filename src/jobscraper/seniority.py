"""Structured seniority inference for job postings.

Maps a title/description/experience signal mix onto a small, explicit
ladder instead of the ad-hoc title heuristics scattered elsewhere.
Unknown stays unknown: when no signal matches, the level is
``"unknown"`` with 0.0 confidence rather than a guessed middle band.

Ladder (lowest to highest):

    intern -> entry -> mid -> senior -> staff -> lead -> manager
    -> director -> executive

``staff`` and ``principal`` titles land on ``staff``; ``lead`` is an IC
level; ``manager`` implies people management.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

LEVELS = ("intern", "entry", "mid", "senior", "staff", "lead",
          "manager", "director", "executive", "unknown")

_RANK = {level: idx for idx, level in enumerate(LEVELS)}

# Title markers, checked highest-priority group first. Word boundaries
# keep "senior" out of "seniority" and "sr" out of "srchitectures".
_TITLE_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("intern", re.compile(
        r"\b(intern|internship|co-op|coop|working student|student worker)\b")),
    ("executive", re.compile(
        r"\b(chief|c\.?e\.?o|c\.?t\.?o|c\.?f\.?o|c\.?i\.?o|c\.?o\.?o|"
        r"president|vp\b|vice president|founder|co-founder|partner)\b")),
    ("director", re.compile(
        r"\b(director|head of|senior director|associate director|"
        r"assistant director)\b")),
    ("manager", re.compile(
        r"\b(manager|engineering manager|people manager|"
        r"senior manager|associate manager)\b")),
    ("staff", re.compile(
        r"\b(staff|principal|distinguished|architect|fellow)\b")),
    ("lead", re.compile(
        r"\b(lead|team lead|tech lead|lead developer|leader)\b")),
    ("senior", re.compile(
        r"\b(senior|sr\.?|snr)\b")),
    ("entry", re.compile(
        r"\b(junior|jr\.?|entry[- ]level|early[- ]career|graduate|"
        r"grad|trainee|associate)\b")),
    ("mid", re.compile(
        r"\b(mid[- ]?level|midlevel)\b")),
)

# Description signals: (level, pattern). Years-based signals are mapped
# to the ladder band the range usually falls in.
_DESC_SIGNALS: tuple[tuple[str, re.Pattern], ...] = (
    ("intern", re.compile(
        r"\b(internship|intern position|open to students)\b")),
    ("entry", re.compile(
        r"\b(entry[- ]level|new grad|recent graduate|no experience "
        r"required|0[-–]2\s*(?:years?|yrs?)\s*(?:of\s+)?experience)\b")),
    ("senior", re.compile(
        r"\b(5|6|7|8)\s*\+\s*(?:years?|yrs?)\b")),
    ("staff", re.compile(
        r"\b(9|1\d)\s*\+\s*(?:years?|yrs?)\b")),
    ("manager", re.compile(
        r"\b(people management|managing a team|line management|"
        r"direct reports)\b")),
    ("executive", re.compile(
        r"\b(p&l (?:responsibility|ownership)|executive leadership)\b")),
)

# Resolution priority when the title matches more than one group:
# explicit management markers beat IC markers, and intern beats
# everything (a posting calling itself an internship should never be
# ranked higher on a stray "senior" somewhere).
_RESOLUTION_ORDER = ("intern", "executive", "director", "manager",
                     "staff", "lead", "senior", "entry", "mid")


def rank(level: str) -> int:
    """Ladder position of a level; -1 for anything unrecognized."""
    return _RANK.get(level, -1)


@dataclass
class Seniority:
    """Inferred seniority of one posting."""

    level: str  # one of LEVELS
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.0  # 0.0-1.0; 0.0 means unknown


def _title_level(title: str) -> tuple[str | None, list[str]]:
    """Level from title markers; (None, []) when nothing matches."""
    text = title.lower()
    matches: list[tuple[str, str]] = []
    for level, pattern in _TITLE_PATTERNS:
        hit = pattern.search(text)
        if hit:
            matches.append((level, hit.group(0).strip()))
    if not matches:
        return None, []
    found = {level for level, _ in matches}
    for level in _RESOLUTION_ORDER:
        if level in found:
            evidence = [f"title: '{word}'"
                        for lv, word in matches if lv == level]
            return level, evidence
    return None, []  # unreachable; _RESOLUTION_ORDER covers all groups


def _description_level(text: str) -> tuple[str | None, list[str]]:
    """Level from description signals; (None, []) when nothing matches."""
    lowered = text.lower()
    matches: list[tuple[str, str]] = []
    for level, pattern in _DESC_SIGNALS:
        hit = pattern.search(lowered)
        if hit:
            matches.append((level, hit.group(0).strip()))
    if not matches:
        return None, []
    found = {level for level, _ in matches}
    for level in _RESOLUTION_ORDER:
        if level in found:
            evidence = [f"description: '{phrase}'"
                        for lv, phrase in matches if lv == level]
            return level, evidence
    return None, []  # unreachable


def _years_level(years: list[int]) -> tuple[str | None, list[str]]:
    """Level hint from required-experience numbers (low confidence)."""
    if not years:
        return None, []
    lo, hi = min(years), max(years)
    if hi <= 2:
        return "entry", [f"asks {lo}-{hi} years"]
    if lo >= 8:
        return "staff", [f"asks {lo}-{hi} years"]
    if lo >= 5:
        return "senior", [f"asks {lo}-{hi} years"]
    if hi >= 3:
        return "mid", [f"asks {lo}-{hi} years"]
    return None, []


def infer_seniority(title: str | None,
                    description: str | None = None,
                    experience_years: list[int] | None = None) -> Seniority:
    """Infer a posting's seniority level with evidence and confidence.

    Title markers win over description signals, which win over the
    years-of-experience band. A title level confirmed by the description
    gets the highest confidence; conflicting signals resolve in favor
    of the title (titles are the more deliberate signal).
    """
    title_level, title_evidence = _title_level(title or "")
    desc_level, desc_evidence = _description_level(description or "")
    years_level, years_evidence = _years_level(experience_years or [])

    if title_level is not None:
        evidence = list(title_evidence)
        confidence = 0.8
        if desc_level == title_level:
            evidence.extend(desc_evidence)
            confidence = 0.95
        elif years_level == title_level:
            evidence.extend(years_evidence)
            confidence = 0.9
        return Seniority(level=title_level, evidence=evidence,
                         confidence=confidence)

    if desc_level is not None:
        evidence = list(desc_evidence)
        confidence = 0.55
        if years_level == desc_level:
            evidence.extend(years_evidence)
            confidence = 0.65
        return Seniority(level=desc_level, evidence=evidence,
                         confidence=confidence)

    if years_level is not None:
        return Seniority(level=years_level, evidence=years_evidence,
                         confidence=0.4)

    return Seniority(level="unknown", evidence=[], confidence=0.0)
