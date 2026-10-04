"""Core data models for jobscraper."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Section:
    """A headed block of the job description (e.g. 'Requirements')."""

    heading: str
    text: str


@dataclass
class MatchResult:
    """0-100 fit score of a posting against a candidate profile."""

    total: int
    breakdown: dict[str, int] = field(default_factory=dict)
    matched_skills: list[str] = field(default_factory=list)
    skill_gaps: list[str] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)


@dataclass
class Posting:
    """Everything extracted from one job posting URL."""

    url: str
    fetched_at: str = ""
    title: str | None = None
    company: str | None = None
    location: str | None = None
    employment_type: str | None = None
    department: str | None = None
    posted: str | None = None
    age_days: int | None = None
    via: str | None = None
    fetch_method: str | None = None
    is_live: bool | None = None
    live_reason: str | None = None
    low_content_warning: str | None = None
    fetch_notes: list[str] = field(default_factory=list)
    board_errors: list[str] = field(default_factory=list)
    skills_found: list[str] = field(default_factory=list)
    experience_years_mentioned: list[int] = field(default_factory=list)
    salary_hits: list[str] = field(default_factory=list)
    signals: dict = field(default_factory=dict)
    requirements: list[Section] = field(default_factory=list)
    nice_to_have: list[Section] = field(default_factory=list)
    responsibilities: list[Section] = field(default_factory=list)
    benefits: list[Section] = field(default_factory=list)
    other_possibly_relevant: list[Section] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    full_text_chars: int = 0
    tracker_status: str | None = None
    match: MatchResult | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        """Serialize to plain dicts for JSON output."""
        d = self.__dict__.copy()
        for key in ("requirements", "nice_to_have", "responsibilities",
                    "benefits", "other_possibly_relevant", "sections"):
            d[key] = [s.__dict__ for s in d[key]]
        if d["match"] is not None:
            d["match"] = d["match"].__dict__
        return d
