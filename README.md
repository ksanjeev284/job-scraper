# job-scraper

[![CI](https://github.com/ksanjeev284/job-scraper/actions/workflows/ci.yml/badge.svg)](https://github.com/ksanjeev284/job-scraper/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Scrape job postings and extract structured requirements: title, company, location, employment type, posting date, requirements, nice-to-haves, responsibilities, detected skills, experience asked, salary figures, work mode, and more. Scores every posting 0-100 against your candidate profile and reports skill gaps.

Works for any profession: configure your skills, role tiers and locations in a profile JSON. Sources include LinkedIn, 13 ATS career-portal feeds, and any posting URL via a hardened headless browser.

## Features

- **LinkedIn as a source** — public guest job-search API (no login): `jobscraper --linkedin "soc analyst" --location "Hyderabad, India" --days 30`
- **Career-portal discovery** — enumerate *every* open posting on a company's career page: `--discover lever:spotify`, `--discover workday:acme:wd3:acme_ext`, `--discover greenhouse:acme`
- **13 ATS integrations** — Lever, Ashby, Greenhouse, SmartRecruiters, Workday, Teamtailor, Personio, Recruitee, Workable, Breezy HR, BambooHR, Pinpoint, Rippling — public APIs/feeds, no login
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

# LinkedIn search straight into the pipeline
jobscraper --linkedin "data analyst" --location "Mumbai, India" --days 14 --limit 20

# enumerate a whole career portal, then scrape every posting
jobscraper --discover lever:spotify --discover ashby:acme --limit 50
```

Outputs: `results.json` (full structured data), `report.md` (ranked summary + per-posting detail), `scores.csv` (spreadsheet).

### Candidate profile

Copy one of the examples in `examples/` (`profile.example.json` for security, `profile.software-engineer.json`, `profile.data-analyst.json`) to `my-profile.json` and edit it: skills, years of experience, certs, preferred locations, current CTC, role tiers, and `custom_skills` (extra skill keywords the built-in vocabulary doesn't cover). Scoring runs against this profile; nothing personal ships with the repo.

### Options

| Flag | Description |
|---|---|
| `--urls FILE` | file with one URL per line |
| `--linkedin KEYWORDS` | search LinkedIn and scrape results |
| `--location TEXT` | location filter for `--linkedin` |
| `--geo-id ID` | LinkedIn geoId for `--linkedin` (more reliable than text) |
| `--limit N` | max LinkedIn results (default 25) |
| `--days N` | only LinkedIn postings from the last N days |
| `--remote MODE` | `onsite` / `remote` / `hybrid` filter for `--linkedin` |
| `--discover BOARD:ID` | enumerate a career portal (repeatable; see below) |
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
 ├─ board API fast path (Lever, Ashby, Greenhouse, SmartRecruiters,
 │   Workday, Teamtailor, Personio, Recruitee, Workable, Breezy, LinkedIn)
 ├─ else: requests → Playwright, with a content-quality gate
 │   (SPA shells are salvaged via embedded job JSON)
 ├─ JSON-LD JobPosting schema when present
 ├─ extract: sections, skills (+ profile custom_skills), salary,
 │   signals, liveness
 ├─ score against candidate profile (configurable role tiers)
 ├─ dedupe + tracker check
 └─ JSON / Markdown / CSV reports
```

Discovery (`--discover BOARD:ID`) enumerates a whole career portal first:

| Board | ID format | Example |
|---|---|---|
| `lever` | company slug | `lever:spotify` |
| `ashby` | org slug | `ashby:acme` |
| `greenhouse` | board token | `greenhouse:acme` |
| `smartrecruiters` | company id | `smartrecruiters:acme` |
| `workday` | `tenant:dc:site` | `workday:acme:wd3:acme_ext` |
| `teamtailor` | career-site host | `teamtailor:careers.acme.com` |
| `personio` | company slug | `personio:acme` |
| `recruitee` | company slug | `recruitee:acme` |
| `workable` | company slug | `workable:acme` |
| `breezy` | tenant | `breezy:acme` |
| `pinpoint` | slug | `pinpoint:acme` |
| `rippling` | board slug | `rippling:acme` |

Layout: `src/jobscraper/` — `boards.py` (ATS APIs + discovery), `sources/linkedin.py` (LinkedIn guest API), `extract.py` (parsing), `scoring.py` (match scores), `rendering.py` (browser/HTTP fetch), `pipeline.py` (orchestration), `reporting.py` (outputs), `cli.py`, `models.py`, `http.py` (network plumbing).

## What it doesn't cover

Indeed, Naukri, Glassdoor and ZipRecruiter have no public API and block unauthenticated scraping aggressively, so they are intentionally out of scope. LinkedIn company pages and authenticated features (saved jobs, Easy Apply state) need a login and are not supported either.

## Testing

```bash
pip install -e ".[dev]"
pytest
```

## Roadmap

See [ROADMAP.md](ROADMAP.md) for planned board integrations (Breezy HR, BambooHR, Pinpoint, Rippling), full-board discovery from a company career page, and fixture-based regression tests.

## License

MIT — see [LICENSE](LICENSE).
