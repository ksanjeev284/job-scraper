"""Canonical job-type normalization.

Scraped sources report employment type in many different shapes:
schema.org JSON-LD ``employmentType`` enums (``FULL_TIME``), ATS labels
(``"Permanent"``, ``"Contractor"``), LinkedIn card text, or nothing at all.
This module folds those raw values — plus conservative title/description
hints when the source says nothing — into one canonical label per posting:

``full-time``, ``part-time``, ``contract``, ``temporary``, ``internship``,
``other``, ``unknown``.

The mapping is purely lexical and deterministic; ``unknown`` is an explicit
state, never a guess.
"""

from __future__ import annotations

import re

#: Canonical job-type labels, in display order.
JOB_TYPES: tuple[str, ...] = (
    "full-time",
    "part-time",
    "contract",
    "temporary",
    "internship",
    "other",
    "unknown",
)

# Explicit raw values seen in the wild, keyed on a normalized form
# (lowercased, non-alphanumeric runs collapsed to a single space).
_EXPLICIT: dict[str, str] = {
    # schema.org employmentType enums
    "full time": "full-time",
    "part time": "part-time",
    "contractor": "contract",
    "temporary": "temporary",
    "intern": "internship",
    # ATS / board labels
    "permanent": "full-time",
    "regular": "full-time",
    "fulltime": "full-time",
    "parttime": "part-time",
    "contract": "contract",
    "freelance": "contract",
    "freelancer": "contract",
    "fixed term": "contract",
    "fixed-term": "contract",
    "temp": "temporary",
    "seasonal": "temporary",
    "internship": "internship",
    "trainee": "internship",
    "apprenticeship": "internship",
    "volunteer": "other",
}

# Fallback hints scanned in title + description text when the source gives
# no explicit employment type. Ordered most-specific first; the first hit
# wins. Patterns deliberately avoid matching inside other words
# (e.g. "internal" must not imply "intern").
_HINTS: tuple[tuple[str, str], ...] = (
    ("internship", r"\bintern(?:ship|s)?\b|\btrainee\b|\bapprentice(?:ship)?\b"),
    ("part-time", r"\bpart[\s-]?time\b"),
    ("contract", r"\bcontract(?:or|ing)?\b|\bfreelance\b|\bfixed[\s-]?term\b"),
    ("temporary", r"\btemporar(?:y|ies)\b|\btemp\b"),
    ("full-time", r"\bfull[\s-]?time\b|\bpermanent\b"),
)


def _squash(raw: str) -> str:
    """Lowercase and collapse separators: ``"FULL_TIME"`` -> ``"full time"``."""
    return re.sub(r"[\s_\-]+", " ", raw.strip().lower()).strip()


def normalize_job_type(raw: str | list | tuple | None,
                       title: str | None = None,
                       text: str | None = None) -> str:
    """Fold a raw employment-type value into a canonical label.

    ``raw`` may be a string, a list of strings (schema.org allows several),
    or ``None``. When it carries no usable value, conservative keyword
    hints from ``title``/``text`` are tried; anything still unrecognized is
    ``"unknown"``.
    """
    values: list[str] = []
    if isinstance(raw, (list, tuple)):
        values = [str(v) for v in raw if v]
    elif raw:
        values = [str(raw)]
    for value in values:
        key = _squash(value)
        if key in _EXPLICIT:
            return _EXPLICIT[key]
        # Comma/pipe-joined multi values ("Full-time, Contract")
        for part in re.split(r"[,/|;]", key):
            part = part.strip()
            if part in _EXPLICIT:
                return _EXPLICIT[part]
    blob = f"{title or ''}\n{text or ''}".lower()
    if blob.strip():
        for label, pattern in _HINTS:
            if re.search(pattern, blob):
                return label
    return "unknown"


def parse_job_type_filter(spec: str | None) -> set[str]:
    """Validate a comma-separated ``--job-type`` spec, raising on typos."""
    wanted = {part.strip().lower() for part in (spec or "").split(",")
              if part.strip()}
    unknown = wanted - set(JOB_TYPES)
    if unknown:
        raise ValueError(
            f"unknown job type(s): {sorted(unknown)} "
            f"(valid: {', '.join(JOB_TYPES)})")
    return wanted
