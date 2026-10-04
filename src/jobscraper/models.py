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
class SourceStat:
    """Per-source run diagnostics: how one board/source behaved this run.

    Mirrors the "honest results" idea from leading scrapers (JobSpy,
    ts-jobspy): a failing or blocked source shows up explicitly instead
    of silently vanishing. ``status`` is one of:

    - ``ok``: every attempted posting scraped cleanly
    - ``partial``: some scraped, some errored
    - ``failed``: none scraped cleanly
    - ``empty``: nothing survived dedupe/filters
    """

    name: str
    attempted: int = 0
    ok: int = 0
    errored: int = 0
    duration_ms: float = 0.0
    errors: dict[str, int] = field(default_factory=dict)

    @property
    def filtered(self) -> int:
        """Postings attempted but removed by dedupe/filters."""
        return max(0, self.attempted - self.ok - self.errored)

    @property
    def status(self) -> str:
        if self.errored and not self.ok:
            return "failed"
        if self.errored:
            return "partial"
        if not self.ok:
            return "empty"
        return "ok"

    def record_error(self, message: str) -> None:
        """Count one failed posting; keep a capped, truncated message."""
        key = (message or "unknown error")[:120]
        self.errors[key] = self.errors.get(key, 0) + 1

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["filtered"] = self.filtered
        d["status"] = self.status
        return d


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
    seniority: str | None = None
    seniority_evidence: list[str] = field(default_factory=list)
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
    salary_normalized: list[dict] = field(default_factory=list)
    is_new: bool = False
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
