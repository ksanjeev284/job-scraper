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
- [x] Remote-only boards, part 2 (2026-10-04): Jobicy (namespaced RSS), Arbeitnow (public job-board API, ~325 recent), Himalayas (public `/jobs/api`, cursor-paged, one page/search) — all no-auth remote feeds wired into `--remote-boards` alongside the existing four
- [x] Curated seed registry of verified company boards for `--discover` sweeps (`src/jobscraper/data/seeds.json`, 13 live-verified boards; `--discover-seeds CATEGORY`, `--list-seeds`)
- [x] Eightfold AI (`{tenant}.eightfold.ai`): whole-board discovery via the public pcsx search API (`--discover eightfold:tenant:domain`, e.g. `eightfold:paypal:paypal.com`; pagination advances through 10-row pages) plus plain-HTTP posting fetch from server-rendered JobPosting JSON-LD; PayPal board added to the seed registry (live-verified, 288 postings on 2026-10-04)
- [x] Workable cross-board search (`jobs.workable.com/api/v1/jobs`): one keyword query across every Workable-hosted career board via the public no-auth API (server-side search, `pageToken` cursor pagination; live-verified 2026-10-04) — `--workable-search KEYWORDS`
- [x] The Muse cross-board search (`themuse.com/api/public/jobs`): one keyword query across The Muse's aggregated listings via the public no-auth API (`--themuse-search KEYWORDS`; keywords match title, company, level/category tags and description client-side, 20-posting pages, capped scan; `--themuse-location` / `--themuse-category` narrow server-side; live-verified 2026-10-04)

## Extraction
- [x] Structured benefits extraction (health, PTO, bonus, equity)
- [x] Posting-age parsing for relative dates ("2 days ago", "3 weeks ago")
- [x] Structured salary normalization (INR LPA, EUR/USD/GBP ranges)
- [x] Watch mode: flag new postings since last run
- [x] Watch mode: detect closed/removed postings since last run (recorded with `closed_since`; fetch errors are never treated as closures, reappearing postings are reopened)
- [x] Company and title-keyword exclusions
- [x] Skill taxonomy with aliases (e.g. "Splunk ES" counts toward "SIEM")
- [x] Fixture-based regression tests (frozen Lever/Ashby/Greenhouse API payloads, SPA shell with embedded JSON, Next.js shell, JSON-LD page, closed posting, block page, duplicates, malformed responses — `tests/fixtures/`, `tests/test_fixtures.py`)
- [x] Better heading detection for non-English postings (DE/FR/ES/NL/IT section headings + multilingual requirements-hint fallback)

## Scoring
- [x] Profile schema validation with helpful errors
- [x] Posting freshness filter (`--max-age DAYS`, JobSpy `hours_old`-style, applied across all sources on parsed posting age; unknown ages kept as unknown)
- [x] Configurable score weights
- [x] Skill taxonomy with aliases (e.g. "Splunk ES" counts toward "SIEM")
- [x] Structured seniority inference (`src/jobscraper/seniority.py`): explicit level ladder (intern/entry/mid/senior/staff/lead/manager/director/executive/unknown) with match evidence and confidence, from title markers, description signals, and required-experience bands; `--seniority` filter; level column in CSV/Excel/HTML exports
- [x] Salary threshold filters (`src/jobscraper/salary.py`): `--min-salary` / `--max-salary` over normalized figures (e.g. `"80K USD"`, `"$200k"`, `"25 LPA"`; k/M suffixes; currency optional); min keeps postings whose range top reaches the amount, max keeps postings whose range bottom is at or below it; postings with no parsed salary are always kept as unknown; also exposed in the web GUI request model and options form
- [x] Fuzzy near-duplicate detection (`src/jobscraper/dedupe.py`): same-employer postings whose titles are near-identical but not exactly equal (thefuzz-style token-set similarity on stdlib `difflib`, default floor 0.85 — e.g. "Splunk Engineer (Nights)" vs "Splunk Engineer - Night Shift" scores ~0.90 and merges, "Frontend Engineer" vs "Backend Engineer" scores ~0.67 and stays separate) merge into the highest-scored copy (ties keep the earliest), with merged URLs recorded on the kept posting's fetch notes; `--fuzzy-dedupe` / `JOBSCRAPER_FUZZY_DEDUPE=1`, floor tunable via `JOBSCRAPER_FUZZY_THRESHOLD` (unit-tested 2026-10-04)
- [x] Repost / ghost-job detection (`src/jobscraper/reposts.py`): `--detect-reposts` / `JOBSCRAPER_DETECT_REPOSTS=1` flags postings whose content fingerprint (company, level-stripped title, location, description body) matches another posting scraped under a different canonical URL — flagged via `signals["repost_of"]` (repost column in CSV/Excel, badge in HTML/Markdown, raw JSON) instead of merging; with `--sqlite`, fresh postings matching a `live = 0` history row under a different URL are flagged as possible ghost jobs (`signals["repost_of_closed"]`); thin postings without a reliable fingerprint stay unflagged; fetch errors are never treated as closures (unit-tested 2026-10-04)

## Robustness
- [x] Optional proxy rotation support (`--proxy`, `--proxies-file`, `JOBSCRAPER_PROXIES`; round-robin with failure parking)
- [x] Respect `robots.txt` per host (opt-in: `--respect-robots` / `JOBSCRAPER_RESPECT_ROBOTS`; per-host 24h cache; disallowed URLs get an explicit error, never a silent skip; missing/unreachable robots.txt = allowed)
- [x] URL canonicalization (`src/jobscraper/urls.py`): strip tracking params (`utm_*`, `trk`, `gclid`, …), fragments, default ports; lowercase scheme/host; sort remaining query params; path case preserved. Applied at pipeline entry (input dedupe + clean reported URLs) and in the applied-tracker check
- [x] Headless-browser pool (`src/jobscraper/rendering.py` `BrowserPool`): reuse one Chromium per worker thread across postings instead of launching per URL; fresh cookie/storage context per posting (closed afterwards), same anti-bot hardening, idle browsers evicted after 5 min, stats via `pool.stats()`; opt-in with `--browser-pool` / `JOBSCRAPER_BROWSER_POOL=1` (requires the `browser` extra); thread-safe for the pipeline's worker pool

## Reporting
- [x] Source run-summary (`src/jobscraper/models.py` `SourceStat`, `boards.py` `board_name_for_url`, `run_pipeline(..., run_stats=True)`): every run reports per-source attempted/ok/errored/filtered with an `ok` / `partial` / `failed` / `empty` status and the top error messages (CLI "Sources" table), so a blocked or broken board can never silently vanish — JobSpy/ts-jobspy "honest results" style
- [x] Web GUI service (FastAPI + browser UI, JSON/CSV export, Docker)
- [x] HTML report option (self-contained, XSS-safe)
- [x] Excel (.xlsx) export — ranked sheet, score bands, autofilter, clickable URLs, formula-injection neutralization (optional openpyxl extra)
- [x] Notion / Google Sheets export hooks (`--webhook-url` with `plain` JSON payload; README documents the Sheets Apps Script receiver and Notion bridge recipes; `slack`/`discord` notification modes)
- [x] Pushover phone-push notifications (`--webhook-mode pushover`; credentials via `JOBSCRAPER_PUSHOVER_TOKEN`/`JOBSCRAPER_PUSHOVER_USER` or `--pushover-token`/`--pushover-user`; one message per run with the top posting attached, 1024-char cap; credentials never logged)
- [x] Telegram bot notifications (`--webhook-mode telegram`; credentials via `JOBSCRAPER_TELEGRAM_TOKEN`/`JOBSCRAPER_TELEGRAM_CHAT_ID` or `--telegram-token`/`--telegram-chat-id`; one plain-text message per run with posting URLs, previews disabled, 4096-char cap; checks the Bot API `ok` body on 200s; credentials never logged)
- [x] ntfy push notifications (`--webhook-mode ntfy`; 2026-10-04): credential-free push via ntfy.sh (or a self-hosted ntfy server) — one plain-text message per run with ranked postings, scores and NEW markers, tap opens the top posting via the Click action; only a topic name needed (`JOBSCRAPER_NTFY_TOPIC` / `--ntfy-topic`), optional access token (`JOBSCRAPER_NTFY_TOKEN` / `--ntfy-token`, Bearer header) and priority (`JOBSCRAPER_NTFY_PRIORITY` / `--ntfy-priority`, low/default/high); topic/token never logged
- [x] SMTP email digest notifications (`--webhook-mode email`; settings via `JOBSCRAPER_SMTP_HOST`/`JOBSCRAPER_SMTP_PORT` (default 587)/`JOBSCRAPER_SMTP_USER`/`JOBSCRAPER_SMTP_PASSWORD`/`JOBSCRAPER_SMTP_FROM`/`JOBSCRAPER_SMTP_TO` or the matching `--smtp-*` flags; one plain+HTML multipart email per run with ranked postings as clickable XSS-safe job cards, NEW badge, seniority and salary hits; STARTTLS by default (`--no-smtp-tls` to skip), auth skipped when no username set; credentials never logged)
- [x] RSS 2.0 feed export (`--rss`): ranked results as a feed-reader-friendly feed — match score in item titles, fit-summary bodies, `pubDate` derived from posting age, `new` category for watch-mode newcomers, company/seniority categories; all scraped fields XML-escaped
- [x] JSONL export (`--jsonl`): ranked results as one full posting record per line — best score first, 1-based `rank` field, UTF-8 with Unicode preserved, unscored postings last; streams through `jq`/`grep`, appends cleanly across runs
- [x] SQLite export (`--sqlite`): ranked postings upserted by canonical URL (stdlib `sqlite3`, no extras) — `first_seen`/`last_seen`/`scrape_count` columns turn repeated runs into a queryable posting history (`SELECT title, company, score FROM postings WHERE live = 1 ORDER BY score DESC`); full record in `raw_json`
- [x] Application status tracking (`src/jobscraper/applications.py`): `--mark-applied URL [--application-status STATUS] [--application-notes TEXT]` records a funnel status (applied/interviewing/offer/rejected/withdrawn) per canonical URL in a local SQLite `applications` table (default `~/.jobscraper/applications.db`, or the `--sqlite` database when one is given); re-marking updates status, appends notes, preserves `applied_date`; `--list-applications [--application-status all]` and `--applications-stats` report the pipeline; scrape runs annotate matching postings so JSON/CSV/HTML/Excel all carry an "applied" column

## Configuration
- [x] Saved search profiles (`src/jobscraper/search_profiles.py`, 2026-10-04): named snapshots of the full CLI option set stored in `~/.jobscraper/search_profiles.json` (override: `--search-profiles-file` / `JOBSCRAPER_SEARCH_PROFILES`), written with mode 0600 since notification credentials may be stored — `--save-search-profile NAME` saves the current invocation, `--search-profile NAME` loads it as defaults (command-line flags still override; unknown keys from renamed options are ignored with a warning), `--list-search-profiles` lists what is saved; distinct from `--profile` (candidate scoring profile)
