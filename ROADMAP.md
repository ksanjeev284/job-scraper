# Roadmap

Planned improvements, in rough priority order.

## Board coverage
- [x] Breezy HR (`{slug}.breezy.hr/json`)
- [x] BambooHR (career pages via generic scrape path)
- [x] Pinpoint (`{slug}.pinpointhq.com/postings.json`)
- [x] Rippling (`api.rippling.com/.../board/{slug}/jobs`)
- [x] Full-board discovery: enumerate all open postings from a company career page (`--discover`)
- [x] LinkedIn guest job-search API (no login)
- [x] Remote-only boards (RemoteOK, Remotive, WeWorkRemotely, Working Nomads — JobSpy-style remote presets; `--remote-boards KEYWORDS`, client-side keyword filtering, cross-board de-dupe)
- [x] Curated seed registry of verified company boards for `--discover` sweeps (`src/jobscraper/data/seeds.json`, 12 live-verified boards; `--discover-seeds CATEGORY`, `--list-seeds`)

## Extraction
- [x] Structured benefits extraction (health, PTO, bonus, equity)
- [x] Posting-age parsing for relative dates ("2 days ago", "3 weeks ago")
- [x] Structured salary normalization (INR LPA, EUR/USD/GBP ranges)
- [x] Watch mode: flag new postings since last run
- [x] Watch mode: detect closed/removed postings since last run (recorded with `closed_since`; fetch errors are never treated as closures, reappearing postings are reopened)
- [x] Company and title-keyword exclusions
- [x] Skill taxonomy with aliases (e.g. "Splunk ES" counts toward "SIEM")
- [x] Fixture-based regression tests (frozen Lever/Ashby/Greenhouse API payloads, SPA shell with embedded JSON, Next.js shell, JSON-LD page, closed posting, block page, duplicates, malformed responses — `tests/fixtures/`, `tests/test_fixtures.py`)
- [ ] Better heading detection for non-English postings

## Scoring
- [x] Profile schema validation with helpful errors
- [x] Configurable score weights
- [x] Skill taxonomy with aliases (e.g. "Splunk ES" counts toward "SIEM")

## Robustness
- [x] Optional proxy rotation support (`--proxy`, `--proxies-file`, `JOBSCRAPER_PROXIES`; round-robin with failure parking)
- [x] Respect `robots.txt` per host (opt-in: `--respect-robots` / `JOBSCRAPER_RESPECT_ROBOTS`; per-host 24h cache; disallowed URLs get an explicit error, never a silent skip; missing/unreachable robots.txt = allowed)
- [x] URL canonicalization (`src/jobscraper/urls.py`): strip tracking params (`utm_*`, `trk`, `gclid`, …), fragments, default ports; lowercase scheme/host; sort remaining query params; path case preserved. Applied at pipeline entry (input dedupe + clean reported URLs) and in the applied-tracker check
- [ ] Headless-browser pool to reuse Chromium across postings

## Reporting
- [x] Web GUI service (FastAPI + browser UI, JSON/CSV export, Docker)
- [x] HTML report option (self-contained, XSS-safe)
- [x] Excel (.xlsx) export — ranked sheet, score bands, autofilter, clickable URLs, formula-injection neutralization (optional openpyxl extra)
- [x] Notion / Google Sheets export hooks (`--webhook-url` with `plain` JSON payload; README documents the Sheets Apps Script receiver and Notion bridge recipes; `slack`/`discord` notification modes)
- [x] Pushover phone-push notifications (`--webhook-mode pushover`; credentials via `JOBSCRAPER_PUSHOVER_TOKEN`/`JOBSCRAPER_PUSHOVER_USER` or `--pushover-token`/`--pushover-user`; one message per run with the top posting attached, 1024-char cap; credentials never logged)
