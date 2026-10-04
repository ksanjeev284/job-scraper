# Roadmap

Planned improvements, in rough priority order.

## Board coverage
- [x] Breezy HR (`{slug}.breezy.hr/json`)
- [x] BambooHR (career pages via generic scrape path)
- [x] Pinpoint (`{slug}.pinpointhq.com/postings.json`)
- [x] Rippling (`api.rippling.com/.../board/{slug}/jobs`)
- [x] Full-board discovery: enumerate all open postings from a company career page (`--discover`)
- [x] LinkedIn guest job-search API (no login)

## Extraction
- [x] Structured benefits extraction (health, PTO, bonus, equity)
- [x] Posting-age parsing for relative dates ("2 days ago", "3 weeks ago")
- [x] Structured salary normalization (INR LPA, EUR/USD/GBP ranges)
- [x] Watch mode: flag new postings since last run
- [x] Company and title-keyword exclusions
- [x] Skill taxonomy with aliases (e.g. "Splunk ES" counts toward "SIEM")
- [ ] Fixture-based regression tests (live posting, closed ATS API posting, SPA shell with embedded JSON, block page, duplicates, malformed responses)
- [ ] Better heading detection for non-English postings

## Scoring
- [x] Profile schema validation with helpful errors
- [x] Configurable score weights
- [x] Skill taxonomy with aliases (e.g. "Splunk ES" counts toward "SIEM")

## Robustness
- [ ] Optional proxy rotation support
- [ ] Headless-browser pool to reuse Chromium across postings
- [ ] Respect `robots.txt` per host (opt-in flag)

## Reporting
- [x] Web GUI service (FastAPI + browser UI, JSON/CSV export, Docker)
- [x] HTML report option (self-contained, XSS-safe)
- [ ] Notion / Google Sheets export hooks
