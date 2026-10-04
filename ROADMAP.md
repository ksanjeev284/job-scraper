# Roadmap

Planned improvements, in rough priority order.

## Board coverage
- [ ] Breezy HR (`breezy.hr` candidate API)
- [ ] BambooHR (`*.bamboohr.com` public JSON)
- [ ] Pinpoint (`*.pinpointhq.com` API)
- [ ] Rippling ATS
- [ ] Full-board discovery: given a company career page, enumerate all open postings via that board's list API

## Extraction
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
- [ ] HTML report option
- [ ] Notion / Google Sheets export hooks
