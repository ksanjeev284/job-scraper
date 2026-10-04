# job-scraper

[![CI](https://github.com/ksanjeev284/job-scraper/actions/workflows/ci.yml/badge.svg)](https://github.com/ksanjeev284/job-scraper/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Scrape job postings and extract structured requirements: title, company, location, employment type, posting date, requirements, nice-to-haves, responsibilities, detected skills, experience asked, salary figures, work mode, and more. Scores every posting 0-100 against your candidate profile and reports skill gaps.

Built for security-engineering job hunting, useful for anyone who wants posting text in a structured, comparable form.

## Features

- **10 no-browser board integrations** — Lever, Ashby, Greenhouse, SmartRecruiters, Workday, Teamtailor, Personio, Recruitee and Workable public APIs/feed endpoints, plus JS-embedded page data (`__NEXT_DATA__`, `window.__appData`) and JSON-LD `JobPosting` schema
- **Anti-block fallbacks** — stealth headless Chromium (Playwright) after plain HTTP fails, with bot-challenge detection, per-domain rate limiting, user-agent rotation, retries with backoff, and a 24h page cache
- **Liveness verdicts** — classifies each posting as LIVE / CLOSED / unknown with a reason; closed postings are detected from page text *and* board-API 404s
- **Structured extraction** — splits descriptions into headed sections, buckets them into requirements / responsibilities / nice-to-haves, and detects skills, experience years, salary figures, sponsorship mentions, language requirements and work mode
- **0-100 match scoring** — against a candidate profile JSON: technical skills (35), experience (25), seniority (15), certifications (10), location (5), role relevance (5), compensation (5); includes skill gaps and a fit/watch summary
- **Dedupe** — drops the same job listed on multiple boards, keeping the best-scoring copy; skips URLs already marked applied in your tracker file
- **Parallel** — multi-threaded fetching; JSON, ranked Markdown and CSV outputs

## Install

```bash
git clone https://github.com/ksanjeev284/job-scraper
cd job-scraper
pip install -e .
# optional: headless browser fallback
pip install -e ".[browser]"
playwright install chromium
```

## Usage

```bash
# one or more URLs
jobscraper "https://boards.greenhouse.io/acme/jobs/123" "https://jobs.ashbyhq.com/acme/abc"

# many URLs from a file (one per line, # = comment)
jobscraper --urls urls.txt --out results.json --md report.md --csv scores.csv

# with your own candidate profile for match scoring
jobscraper --urls urls.txt --profile my-profile.json

# or as a module
python -m jobscraper --urls urls.txt --workers 8 --no-cache
```

Outputs: `results.json` (full structured data), `report.md` (ranked summary + per-posting detail), `scores.csv` (spreadsheet).

### Candidate profile

Copy `examples/profile.example.json` to `my-profile.json` and edit it: skills, years of experience, certs, preferred locations, current CTC. Scoring runs against this profile; nothing personal ships with the repo.

### Options

| Flag | Description |
|---|---|
| `--urls FILE` | file with one URL per line |
| `--out PATH` | JSON output path |
| `--md PATH` | Markdown report path |
| `--csv PATH` | CSV export path |
| `--profile PATH` | candidate profile JSON for scoring |
| `--no-score` | skip match scoring |
| `--tracker PATH` | tracker file: postings whose URL appears are marked `applied` |
| `--workers N` | parallel fetch workers (default 4) |
| `--no-cache` | ignore the local 24h page cache |
| `--no-dedupe` | keep cross-board duplicates |

## How it works

```
URL
 └─ board API fast path (Lever, Ashby, Greenhouse, SmartRecruiters,
    Workday, Teamtailor, Personio, Recruitee, Workable)
 └─ else: requests → Playwright, with a content-quality gate
     (SPA shells are salvaged via embedded job JSON)
 └─ JSON-LD JobPosting schema when present
 └─ extract: sections, skills, salary, signals, liveness
 └─ score against candidate profile
 └─ dedupe + tracker check
 └─ JSON / Markdown / CSV reports
```

Layout: `src/jobscraper/` — `boards.py` (ATS APIs), `extract.py` (parsing), `scoring.py` (match scores), `rendering.py` (browser/HTTP fetch), `pipeline.py` (orchestration), `reporting.py` (outputs), `cli.py`, `models.py`, `http.py` (network plumbing).

## Testing

```bash
pip install -e ".[dev]"
pytest
```

## Roadmap

See [ROADMAP.md](ROADMAP.md) for planned board integrations (Breezy HR, BambooHR, Pinpoint, Rippling), full-board discovery from a company career page, and fixture-based regression tests.

## License

MIT — see [LICENSE](LICENSE).
