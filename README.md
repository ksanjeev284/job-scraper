# job-scraper

[![CI](https://github.com/ksanjeev284/job-scraper/actions/workflows/ci.yml/badge.svg)](https://github.com/ksanjeev284/job-scraper/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Scrape job postings and extract structured requirements: title, company, location, employment type, posting date, requirements, nice-to-haves, responsibilities, detected skills, experience asked, salary figures, work mode, and more. Scores every posting 0-100 against your candidate profile and reports skill gaps.

Works for any profession: configure your skills, role tiers and locations in a profile JSON. Sources include LinkedIn, 14 ATS career-portal feeds, and any posting URL via a hardened headless browser.

## Features

- **LinkedIn as a source** — public guest job-search API (no login): `jobscraper --linkedin "soc analyst" --location "Hyderabad, India" --days 30`
- **Remote-only boards** — RemoteOK, Remotive, We Work Remotely and Working Nomads (all public, no-auth feeds): `jobscraper --remote-boards "security engineer" --limit 20`
- **Career-portal discovery** — enumerate *every* open posting on a company's career page: `--discover lever:spotify`, `--discover workday:acme:wd3:acme_ext`, `--discover eightfold:paypal:paypal.com`
- **Seed-board registry** — `--discover-seeds fintech` sweeps a curated, live-verified registry of company career boards by category (`--list-seeds` shows it); omit the category to sweep them all
- **14 ATS integrations** — Lever, Ashby, Greenhouse, SmartRecruiters, Workday, Teamtailor, Personio, Recruitee, Workable, Breezy HR, BambooHR, Pinpoint, Rippling, Eightfold AI — public APIs/feeds, no login
- **Anti-block fallbacks** — stealth headless Chromium (Playwright) after plain HTTP fails, with bot-challenge detection, per-domain rate limiting, user-agent rotation, retries with backoff, and a 24h page cache. `--browser-pool` (or `JOBSCRAPER_BROWSER_POOL=1`) reuses one browser per worker thread for the whole run instead of launching a fresh Chromium per posting — faster multi-posting runs, with a fresh cookie/storage context per posting and idle browsers shut down automatically
- **Liveness verdicts** — classifies each posting as LIVE / CLOSED / unknown with a reason; closed postings are detected from page text *and* board-API 404s
- **Structured extraction** — splits descriptions into headed sections, buckets them into requirements / responsibilities / nice-to-haves, and detects skills, experience years, salary figures, sponsorship mentions, language requirements and work mode; section headings are recognized in English, German, French, Spanish, Dutch and Italian
- **0-100 match scoring** — against a candidate profile JSON: technical skills (35), experience (25), seniority (15), certifications (10), location (5), role relevance (5), compensation (5); includes skill gaps and a fit/watch summary
- **Seniority inference** — every posting gets an explicit level (`intern` / `entry` / `mid` / `senior` / `staff` / `lead` / `manager` / `director` / `executive` / `unknown`) with match evidence, from title markers first, then description signals, then required-experience bands; `--seniority senior,staff` filters runs by level, and the level shows in CSV/Excel/HTML exports
- **Dedupe** — drops the same job listed on multiple boards, keeping the best-scoring copy; skips URLs already marked applied in your tracker file
- **URL canonicalization** — tracking parameters (`utm_*`, `trk`, `gclid`, …), fragments, host-case variants and trailing slashes are stripped before fetching, so the same posting shared from different sources is fetched once and reported under one clean URL; the applied-tracker check matches across those variants too
- **Parallel** — multi-threaded fetching; JSON, ranked Markdown, CSV, HTML, Excel, JSONL, RSS and SQLite outputs
- **Excel export** — `--excel results.xlsx` writes the ranked spreadsheet: score-colored cells, frozen header with autofilter, clickable posting URLs, formula-injection neutralized (needs `pip install -e ".[excel]"`)
- **RSS feed export** — `--rss feed.xml` writes the ranked results as an RSS 2.0 feed for feed readers: match scores in item titles, fit summaries in the bodies, posting-age `pubDate`, watch-mode newcomers tagged `new`
- **JSONL export** — `--jsonl results.jsonl` writes one full posting record per line, ranked best-first: streams through `jq` and `grep`, appends cleanly across runs (`>>`)
- **SQLite export** — `--sqlite jobs.db` upserts the ranked results by canonical URL (no new dependencies): `first_seen` keeps the original timestamp, `last_seen` advances and `scrape_count` increments on every re-run, so the database becomes a queryable history across runs (`SELECT title, company, score FROM postings WHERE live = 1 ORDER BY score DESC`); list fields stored as JSON text, full record in `raw_json`
- **Source run-summary** — every run prints a per-board table (attempted / ok / errored / filtered, plus a `ok` / `partial` / `failed` / `empty` status and the top error messages) so a blocked or broken board can never silently vanish; programmatic access via `run_pipeline(..., run_stats=True)`
- **Webhook notifications / export hooks** — `--webhook-url URL` POSTs the ranked results after each run: `plain` JSON for custom receivers, or `slack` / `discord` chat notifications; `--webhook-mode pushover` / `--webhook-mode telegram` send a phone alert, `--webhook-mode email` sends an SMTP digest to your inbox instead (see below)

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

Outputs: `results.json` (full structured data), `report.md` (ranked summary + per-posting detail), `scores.csv` (spreadsheet), `report.html` (self-contained ranked HTML report with score breakdowns, inline CSS, no external assets; every scraped field is HTML-escaped so the report is safe to open in a browser), `feed.xml` (RSS 2.0 feed of the ranked results for feed readers), `results.jsonl` (one full posting record per line, ranked best-first, for `jq` pipelines), `jobs.db` (SQLite: ranked postings upserted by URL, a queryable history across runs).

### Candidate profile

Pick the closest starting point in `examples/` (`security-engineer.json`, `software-engineer.json`, `data-analyst.json`) or start blank from `template.json`, copy it to `my-profile.json`, and fill in your skills, years of experience, certs, preferred locations, current CTC, role tiers (`tier1`/`tier2`/`tier3` keyword lists that define what counts as a strong role match for *your* field), and `custom_skills` (extra skill keywords the built-in vocabulary doesn't cover), `skill_aliases` (e.g. `"SIEM": ["Splunk ES", "QRadar"]` so a posting naming a specific tool counts toward the broader skill), and `weights` (rebalance the 35/25/15/10/5/5/5 scoring split, e.g. `"weights": {"technical_skills": 50, "experience": 30}`). Profiles are validated on load — a typo'd key or wrong type fails fast with a clear message instead of silently mis-scoring. Scoring runs against this profile; nothing personal ships with the repo. With no `--profile`, a neutral template is used.

### Options

| Flag | Description |
|---|---|
| `--urls FILE` | file with one URL per line |
| `--linkedin KEYWORDS` | search LinkedIn and scrape results |
| `--remote-boards KEYWORDS` | search remote-only boards (RemoteOK, Remotive, We Work Remotely, Working Nomads) and scrape results |
| `--location TEXT` | location filter for `--linkedin` |
| `--geo-id ID` | LinkedIn geoId for `--linkedin` (more reliable than text) |
| `--limit N` | max LinkedIn results (default 25) |
| `--days N` | only LinkedIn postings from the last N days |
| `--remote MODE` | `onsite` / `remote` / `hybrid` filter for `--linkedin` |
| `--discover BOARD:ID` | enumerate a career portal (repeatable; see below) |
| `--discover-seeds [CATEGORY]` | sweep the verified seed-board registry (see below; category optional) |
| `--list-seeds` | list the seed registry and exit |
| `--locations "A,B"` | preferred locations for this run (overrides profile) |
| `--location-filter TEXT` | keep only postings whose location contains TEXT |
| `--keyword-filter "A,B"` | keep only postings whose title contains a keyword |
| `--min-score N` | keep only postings scoring N or higher (0-100) |
| `--seniority LEVEL[,LEVEL]` | keep only postings at these seniority levels: `intern`, `entry`, `mid`, `senior`, `staff`, `lead`, `manager`, `director`, `executive`, `unknown` |
| `--min-salary AMOUNT` | keep only postings whose salary range can reach AMOUNT (annual), e.g. `--min-salary "80K USD"` or `--min-salary "25 LPA"`; postings with no parsed salary are kept |
| `--max-salary AMOUNT` | keep only postings whose salary range bottom is at or below AMOUNT (annual), e.g. `--max-salary "200K USD"`; postings with no parsed salary are kept |
| `--max-age DAYS` | keep only postings posted within the last N days (freshness filter, JobSpy `hours_old`-style); postings with an unknown age are kept |
| `--out PATH` | JSON output path |
| `--md PATH` | Markdown report path |
| `--csv PATH` | CSV export path |
| `--html PATH` | HTML report path |
| `--rss PATH` | RSS 2.0 feed export path |
| `--jsonl PATH` | JSON Lines export path: one full posting record per line, ranked by match score |
| `--sqlite PATH` | SQLite database path: ranked postings upserted by URL, a queryable history across runs |
| `--profile PATH` | candidate profile JSON for scoring |
| `--no-score` | skip match scoring |
| `--tracker PATH` | tracker file: postings whose URL appears are marked `applied` |
| `--workers N` | parallel fetch workers (default 4) |
| `--no-cache` | ignore the local 24h page cache |
| `--no-dedupe` | keep cross-board duplicates |
| `--respect-robots` | honor robots.txt for every fetched URL (also via the `JOBSCRAPER_RESPECT_ROBOTS` env var) |
| `--proxy URL` | proxy URL for all requests (repeatable; rotated round-robin) |
| `--proxies-file PATH` | text file with one proxy URL per line (`#` comments allowed) |

### Salary threshold filters

`--min-salary` / `--max-salary` filter on the salary figures the scraper
already normalizes (INR LPA, EUR, USD, GBP). A threshold is an annual
amount plus a currency: `--min-salary "80K USD"`, `--max-salary "$200k"`,
`--min-salary "25 LPA"` (lakh/LPA counts as INR). `k`/`M` suffixes are
supported; the currency may be omitted (matches figures in any currency,
documented as approximate in output). `--min-salary` keeps postings whose
range *top* reaches the amount; `--max-salary` keeps postings whose range
*bottom* is at or below it. Postings with no parsed salary figures are
always kept: a missing salary is reported as unknown, never treated as
proof the pay is too low or too high.

### Posting freshness filter

`--max-age DAYS` keeps only postings posted within the last N days —
JobSpy `hours_old`-style, but applied uniformly across every source
(board APIs, LinkedIn, remote boards, `--discover` sweeps) on the
`age_days` the scraper already derives from posting dates and relative
labels ("3 days ago", "posted 2 weeks ago"). A posting whose age cannot
be determined is always kept (reported as unknown), never silently
dropped, and fetch failures stay visible regardless of the filter.

### Respecting robots.txt

Off by default; opt in with `--respect-robots` (or set
`JOBSCRAPER_RESPECT_ROBOTS=1`):

```bash
jobscraper --urls urls.txt --respect-robots
```

Each host's `robots.txt` is fetched once and cached for 24 hours. Any URL
the host disallows raises an explicit error on the posting (recorded in
`fetch_notes`, never silently skipped), so disallowed postings stay
visible in the report with their reason. Hosts with no reachable
robots.txt are treated as fully allowed, and a robots.txt fetch failure
never blocks crawling.

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

# Phone-push alert instead of a chat message (needs Pushover credentials;
# --webhook-url is ignored in this mode)
jobscraper --urls urls.txt --watch state.json --webhook-only-new \
  --webhook-mode pushover

# Telegram alert via your own bot (needs a bot token and chat id;
# --webhook-url is ignored in this mode)
jobscraper --urls urls.txt --watch state.json --webhook-only-new \
  --webhook-mode telegram

# Email digest to your inbox (SMTP settings via env vars or --smtp-*
# flags; --webhook-url is ignored in this mode)
jobscraper --urls urls.txt --watch state.json --webhook-only-new \
  --webhook-mode email

# Watch mode also tracks closures: postings seen in a previous run that
# disappear are reported as "closed since last run" (fetch errors are
# never treated as closures; a posting that reappears is reopened).
jobscraper --urls urls.txt --watch state.json
```

- `--webhook-mode plain` (default) sends the full JSON payload:
  `tool`, `generated_at`, `total`, `new`, and the ranked `postings`.
- `--webhook-mode slack` sends a Block Kit message; `--webhook-mode discord`
  sends an embed message (max 10 embeds; longer lists are truncated).
- `--webhook-mode pushover` sends one phone-push message per run via the
  Pushover API (https://pushover.net/api): ranked postings as lines,
  the top posting attached as the tappable URL, message capped at the
  1024-character API limit. It needs a Pushover application token and
  user key from `JOBSCRAPER_PUSHOVER_TOKEN` / `JOBSCRAPER_PUSHOVER_USER`
  (or `--pushover-token` / `--pushover-user`); `--webhook-url` is
  ignored in this mode. Credentials are never logged.
- `--webhook-mode telegram` sends one message per run via the Telegram
  Bot API (https://core.telegram.org/bots/api): ranked postings as
  plain-text lines with their URLs (web-page previews disabled),
  capped at the 4096-character API limit. It needs a bot token and the
  chat id from `JOBSCRAPER_TELEGRAM_TOKEN` /
  `JOBSCRAPER_TELEGRAM_CHAT_ID` (or `--telegram-token` /
  `--telegram-chat-id`); `--webhook-url` is ignored in this mode.
  Setup: create a bot with @BotFather to get the token, then message
  the bot from your account and read your chat id from
  `https://api.telegram.org/bot<TOKEN>/getUpdates`. Credentials are
  never logged.
- `--webhook-mode email` sends one SMTP digest email per run (a plain
  + HTML multipart message): the ranked postings as clickable job
  cards with title, company, location, score, seniority, salary hits
  and a NEW badge, all posting text HTML-escaped. It needs the SMTP
  settings from `JOBSCRAPER_SMTP_HOST` / `JOBSCRAPER_SMTP_PORT`
  (default 587) / `JOBSCRAPER_SMTP_USER` /
  `JOBSCRAPER_SMTP_PASSWORD` / `JOBSCRAPER_SMTP_FROM` (defaults to
  the username) / `JOBSCRAPER_SMTP_TO`, or the matching
  `--smtp-host` / `--smtp-port` / `--smtp-user` / `--smtp-password`
  / `--smtp-from` / `--smtp-to` flags; STARTTLS is used unless
  `--no-smtp-tls` is passed, and auth is skipped when no username is
  set (internal relays). `--webhook-url` is ignored in this mode.
  Example with Gmail (use an app password, never your real one):
  `export JOBSCRAPER_SMTP_HOST=smtp.gmail.com
  JOBSCRAPER_SMTP_USER=you@gmail.com
  JOBSCRAPER_SMTP_PASSWORD=xxxx-xxxx-xxxx-xxxx
  JOBSCRAPER_SMTP_TO=you@gmail.com`. The password and recipient
  never appear in logs or error output.
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
 ├─ else: requests → Playwright (pooled with `--browser-pool`, else
 │   one fresh browser per posting), with a content-quality gate
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
| `eightfold` | `tenant:domain` | `eightfold:paypal:paypal.com` |

Seed-board sweeps (`--discover-seeds`) automate discovery across a curated
registry of verified company boards (`src/jobscraper/data/seeds.json` —
every entry was live-verified to enumerate postings when added; a stale
board yields nothing and never aborts the sweep):

```bash
jobscraper --list-seeds                        # show the registry + categories
jobscraper --discover-seeds fintech            # sweep every fintech seed board
jobscraper --discover-seeds ai --keyword-filter "security engineer" \
  --locations "Remote"                         # narrow the sweep like any run
jobscraper --discover-seeds                    # sweep every seed board
```

Layout: `src/jobscraper/` — `boards.py` (ATS APIs + discovery), `seeds.py` (curated verified-board registry), `sources/linkedin.py` (LinkedIn guest API), `sources/remote_boards.py` (RemoteOK/Remotive/WWR/Working Nomads remote boards), `extract.py` (parsing), `scoring.py` (match scores), `rendering.py` (browser/HTTP fetch), `pipeline.py` (orchestration), `reporting.py` (outputs), `cli.py`, `models.py`, `http.py` (network plumbing).

## What it doesn't cover

Indeed, Naukri, Glassdoor and ZipRecruiter have no public API and block unauthenticated scraping aggressively, so they are intentionally out of scope. LinkedIn company pages and authenticated features (saved jobs, Easy Apply state) need a login and are not supported either.

## Testing

```bash
pip install -e ".[dev]"
pytest
```

`tests/fixtures/` holds frozen snapshots of real-world shapes (Lever, Ashby
and Greenhouse API payloads; SPA shells with embedded `window.__appData` and
Next.js `__NEXT_DATA__` job data; a JSON-LD posting page; a closed posting;
a bot-block page; a malformed API response). `tests/test_fixtures.py` drives
them through the fetchers, parsers and full pipeline with no network access,
so a board API changing shape or an extraction regression shows up here first.

## Roadmap

See [ROADMAP.md](ROADMAP.md) for the remaining planned work.

## License

MIT — see [LICENSE](LICENSE).
