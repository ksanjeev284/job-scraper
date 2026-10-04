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
- [x] Posting-age parsing for relative dates ("2 days ago", "3 weeks ago")
- [x] Skill taxonomy with aliases (e.g. "Splunk ES" counts toward "SIEM")
- [ ] Fixture-based regression tests (live posting, closed ATS API posting, SPA shell with embedded JSON, block page, duplicates, malformed responses)
- [ ] Better heading detection for non-English postings
- [ ] Structured benefits extraction (bonus, equity, visa sponsorship terms)
- [ ] Posting-age parsing for relative dates ("2 days ago", "Posted 3 weeks ago")

## Scoring
- [ ] Profile schema validation with helpful errors
- [ ] Configurable score weights
- [ ] Skill taxonomy with aliases (e.g. "Splunk ES" counts toward "SIEM")

## Robustness
- [ ] Optional proxy rotation support
- [ ] Headless-browser pool to reuse Chromium across postings
- [ ] Respect `robots.txt` per host (opt-in flag)

## Reporting
- [x] Web GUI service (FastAPI + browser UI, JSON/CSV export, Docker)
- [ ] HTML report option
- [ ] Notion / Google Sheets export hooks
