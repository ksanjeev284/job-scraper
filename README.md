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
- **Parallel** — multi-threaded fetching; JSON, ranked Markdown, CSV, HTML and Excel outputs
- **Excel export** — `--excel results.xlsx` writes the ranked spreadsheet: score-colored cells, frozen header with autofilter, clickable posting URLs, formula-injection neutralized (needs `pip install -e ".[excel]"`)
- **Webhook notifications / export hooks** — `--webhook-url URL` POSTs the ranked results after each run: `plain` JSON for custom receivers, or `slack` / `discord` chat notifications (see below)

## Web GUI

Prefer clicking to typing? Run the built-in web service:

```bash
pip install -e ".[web]"
jobscraper-serve
# open http://127.0.0.1:8000
```

Paste posting URLs, search LinkedIn, or enumerate a career portal — pick a profile, set filters, watch the progress bar, and browse ranked results with score pills, fit/gap chips and expandable requirements. Download JSON or CSV when done. `jobscraper-serve --host 0.0.0.0 --port 8000` exposes it on your LAN; a `Dockerfile` is included for container deploys (`docker build -t jobscraper . && docker run -p 8000:8000 jobscraper`).

The service binds to localhost by default. Only expose it wider on networks you trust: anyone with access can make it fetch arbitrary URLs.

## Install

```bash
git clone https://github.com/ksanjeev284/job-scraper
cd job-scraper
pip install -e .
# optional: headless browser fallback
pip install -e ".[browser]"
playwright install chromium
# optional: Excel export
pip install -e ".[excel]"
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

# any profession, any location: filter and re-target per run
jobscraper --linkedin "data analyst" --location "Mumbai, India" \
  --profile examples/data-analyst.json --location-filter mumbai
```

Outputs: `results.json` (full structured data), `report.md` (ranked summary + per-posting detail), `scores.csv` (spreadsheet), `report.html` (self-contained ranked HTML report with score breakdowns, inline CSS, no external assets; every scraped field is HTML-escaped so the report is safe to open in a browser).

### Candidate profile

Pick the closest starting point in `examples/` (`security-engineer.json`, `software-engineer.json`, `data-analyst.json`) or start blank from `template.json`, copy it to `my-profile.json`, and fill in your skills, years of experience, certs, preferred locations, current CTC, role tiers (`tier1`/`tier2`/`tier3` keyword lists that define what counts as a strong role match for *your* field), and `custom_skills` (extra skill keywords the built-in vocabulary doesn't cover), `skill_aliases` (e.g. `"SIEM": ["Splunk ES", "QRadar"]` so a posting naming a specific tool counts toward the broader skill), and `weights` (rebalance the 35/25/15/10/5/5/5 scoring split, e.g. `"weights": {"technical_skills": 50, "experience": 30}`). Profiles are validated on load — a typo'd key or wrong type fails fast with a clear message instead of silently mis-scoring. Scoring runs against this profile; nothing personal ships with the repo. With no `--profile`, a neutral template is used.

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
| `--locations "A,B"` | preferred locations for this run (overrides profile) |
| `--location-filter TEXT` | keep only postings whose location contains TEXT |
| `--keyword-filter "A,B"` | keep only postings whose title contains a keyword |
| `--min-score N` | keep only postings scoring N or higher (0-100) |
| `--out PATH` | JSON output path |
| `--md PATH` | Markdown report path |
| `--csv PATH` | CSV export path |
| `--html PATH` | HTML report path |
| `--profile PATH` | candidate profile JSON for scoring |
| `--no-score` | skip match scoring |
| `--tracker PATH` | tracker file: postings whose URL appears are marked `applied` |
| `--workers N` | parallel fetch workers (default 4) |
| `--no-cache` | ignore the local 24h page cache |
| `--no-dedupe` | keep cross-board duplicates |
| `--proxy URL` | proxy URL for all requests (repeatable; rotated round-robin) |
| `--proxies-file PATH` | text file with one proxy URL per line (`#` comments allowed) |

### Proxy rotation

For boards that rate-limit datacenter IPs, route requests through a proxy pool:

```bash
jobscraper --urls urls.txt --proxy http://user:pass@proxy1:8080 \
  --proxy http://proxy2:8080 --profile my-profile.json

# or from a file, or from the JOBSCRAPER_PROXIES env var
# (whitespace/comma separated). Precedence: --proxy > --proxies-file > env.
jobscraper --urls urls.txt --proxies-file proxies.txt
```

Requests are spread round-robin across the pool. A proxy that fails three
requests in a row (connection refused, timeout, DNS) is parked for five
minutes and then automatically re-enters the pool; if every proxy is parked,
requests fall back to a direct connection rather than failing. HTTP error
responses (429/5xx) are not counted against a proxy — the proxy did its job
by delivering the response.

## How it works

### Webhook notifications and export hooks

After the reports are written, the ranked results can be POSTed as JSON to
one or more webhook URLs (repeatable `--webhook-url`, or comma-separated in
the `JOBSCRAPER_WEBHOOK_URL` env var):

```bash
# Full JSON payload to a custom receiver (e.g. a Sheets/Notion bridge)
jobscraper --urls urls.txt --webhook-url https://example.com/receive

# Slack or Discord notification of the run's results
jobscraper --urls urls.txt \
  --webhook-url https://hooks.slack.com/services/T.../B.../secret \
  --webhook-mode slack

# Watch mode + notifications: only ping when new postings appear
jobscraper --urls urls.txt --watch state.json --webhook-only-new \
  --webhook-url https://discord.com/api/webhooks/123/secret \
  --webhook-mode discord --webhook-top 10

# Watch mode also tracks closures: postings seen in a previous run that
# disappear are reported as "closed since last run" (fetch errors are
# never treated as closures; a posting that reappears is reopened).
jobscraper --urls urls.txt --watch state.json
```

- `--webhook-mode plain` (default) sends the full JSON payload:
  `tool`, `generated_at`, `total`, `new`, and the ranked `postings`.
- `--webhook-mode slack` sends a Block Kit message; `--webhook-mode discord`
  sends an embed message (max 10 embeds; longer lists are truncated).
- `--webhook-only-new` skips the POST entirely when watch mode finds nothing
  new. `--webhook-top N` caps the postings included (default 25).
- Webhook URLs carry secrets in their path: only the host is ever logged.
  Delivery failures print an error and exit non-zero; they never take down
  the scrape results already written.

**Google Sheets:** create an Apps Script with a `doPost(e)` that parses
`JSON.parse(e.postData.contents)` and appends one row per
`payload.postings` entry to your sheet, deploy as a web app, and pass its
URL to `--webhook-url`.

**Notion:** point `--webhook-url` at a small bridge (a few-line server or
serverless function) that reads `payload.postings` and creates pages in
your Notion database via the Notion API (`/v1/pages`); the `plain` payload
already carries title, company, location, score, URL, salary, and skill
gaps per posting.

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
 └─ JSON / Markdown / CSV / HTML reports
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

See [ROADMAP.md](ROADMAP.md) for the remaining planned work (fixture-based
regression tests, non-English heading detection, headless-browser pool,
`robots.txt` support).

## License

MIT — see [LICENSE](LICENSE).
