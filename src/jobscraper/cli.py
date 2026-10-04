"""Command-line interface for jobscraper."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from jobscraper.pipeline import run_pipeline
from jobscraper.reporting import (
    write_csv,
    write_html,
    write_jsonl,
    write_markdown,
    write_rss,
    write_sqlite,
    write_xlsx,
)
from jobscraper.scoring import load_profile, validate_profile


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobscraper",
        description="Scrape job postings and extract structured requirements.",
    )
    parser.add_argument("urls", nargs="*", help="Job posting URLs")
    parser.add_argument("--urls", dest="urlfile",
                        help="Text file with one URL per line")
    parser.add_argument("--out", default=None, help="JSON output path")
    parser.add_argument("--md", default=None, help="Markdown report path")
    parser.add_argument("--profile", default=None,
                        help="Candidate profile JSON for match scoring "
                             "(default: examples/profile.example.json)")
    parser.add_argument("--no-score", action="store_true",
                        help="Skip match scoring")
    parser.add_argument("--tracker", default=None,
                        help="Path to a tracker file for applied-dedupe")
    app_group = parser.add_argument_group(
        "Application tracking",
        "Record what happened after a posting was found: applied, "
        "interviewing, offer, rejected, withdrawn. Stored in a local "
        "SQLite database (default ~/.jobscraper/applications.db, or the "
        "--sqlite database when one is given).")
    app_group.add_argument("--mark-applied", dest="mark_applied",
                           action="append", default=None, metavar="URL",
                           help="Record an application for a posting URL "
                                "(repeatable); re-marking an existing URL "
                                "updates its status")
    app_group.add_argument("--application-status", default="applied",
                           help="Status to record with --mark-applied, or "
                                "to filter by with --list-applications "
                                "('all' lists every status; default: "
                                "applied)")
    app_group.add_argument("--application-notes", default=None,
                           help="Notes to attach when marking an "
                                "application (appended on re-mark)")
    app_group.add_argument("--applications-db", default=None,
                           help="Applications database path (default: the "
                                "--sqlite path, else "
                                "~/.jobscraper/applications.db)")
    app_group.add_argument("--list-applications", action="store_true",
                           help="List tracked applications (optionally "
                                "filtered by --application-status)")
    app_group.add_argument("--applications-stats", action="store_true",
                           help="Print application counts per status")
    parser.add_argument("--csv", default=None, help="CSV export path")
    parser.add_argument("--html", default=None, help="HTML report path")
    parser.add_argument("--excel", default=None,
                        help="Excel (.xlsx) export path "
                             "(needs the 'excel' extra)")
    parser.add_argument("--rss", default=None,
                        help="RSS 2.0 feed export path (ranked by match "
                             "score, for feed readers)")
    parser.add_argument("--jsonl", default=None,
                        help="JSON Lines export path: one full posting "
                             "record per line, ranked by match score")
    parser.add_argument("--sqlite", default=None,
                        help="SQLite database path: ranked postings upserted "
                             "by URL so runs accumulate a queryable history")
    parser.add_argument("--no-cache", action="store_true",
                        help="Ignore the local page cache and refetch")
    parser.add_argument("--workers", type=int, default=4,
                        help="Parallel fetch workers (default 4)")
    parser.add_argument("--no-dedupe", action="store_true",
                        help="Keep cross-board duplicates instead of "
                             "dropping them")
    parser.add_argument("--fuzzy-dedupe", action="store_true",
                        help="Also merge near-duplicate postings: same "
                             "employer with near-identical titles (e.g. "
                             "\"Splunk Engineer (Nights)\" vs \"Splunk "
                             "Engineer - Night Shift\"); keeps the "
                             "highest-scored copy (also via "
                             "JOBSCRAPER_FUZZY_DEDUPE=1, threshold via "
                             "JOBSCRAPER_FUZZY_THRESHOLD)")
    parser.add_argument("--detect-reposts", action="store_true",
                        help="Flag reposts: postings whose content "
                             "fingerprint (company, level-stripped title, "
                             "location, description body) matches another "
                             "posting scraped under a different URL get a "
                             "'repost_of' pointer instead of merging away; "
                             "with --sqlite, postings matching a closed "
                             "posting from the history are flagged as "
                             "possible ghost jobs "
                             "(also via JOBSCRAPER_DETECT_REPOSTS=1)")
    parser.add_argument("--linkedin", default=None, metavar="KEYWORDS",
                        help="Search LinkedIn jobs for KEYWORDS and scrape "
                             "the results")
    parser.add_argument("--remote-boards", default=None, metavar="KEYWORDS",
                        help="Search remote-only job boards (RemoteOK, "
                             "Remotive, We Work Remotely, Working Nomads, "
                             "Jobicy, Arbeitnow, Himalayas) "
                             "for KEYWORDS and scrape the results")
    parser.add_argument("--workable-search", default=None, metavar="KEYWORDS",
                        help="Search every Workable-hosted career board at "
                             "once (jobs.workable.com, public no-auth API) "
                             "for KEYWORDS and scrape the results")
    parser.add_argument("--themuse-search", default=None, metavar="KEYWORDS",
                        help="Search The Muse's aggregated listings "
                             "(themuse.com, public no-auth API) for "
                             "KEYWORDS and scrape the results; keywords "
                             "match the title, company, level/category "
                             "tags and description")
    parser.add_argument("--themuse-location", default=None,
                        metavar="LOCATION",
                        help="Server-side location filter for "
                             "--themuse-search (e.g. \"India\")")
    parser.add_argument("--themuse-category", default=None,
                        metavar="CATEGORY",
                        help="Server-side category filter for "
                             "--themuse-search (e.g. \"Data Science\")")
    parser.add_argument("--hn-hiring", default=None, metavar="KEYWORDS",
                        help="Search the current month's \"Ask HN: Who is "
                             "hiring?\" thread (Hacker News, public no-auth "
                             "APIs) for KEYWORDS and scrape the matching "
                             "comments as postings; keywords match the "
                             "title, company and comment text")
    parser.add_argument("--hn-month", default=None, metavar="YYYY-MM",
                        help="Which Who-is-hiring thread to scrape "
                             "(default: current month)")
    parser.add_argument("--hn-max-comments", type=int, default=300,
                        help="Max top-level HN comments to fetch for "
                             "--hn-hiring (default 300)")
    parser.add_argument("--location", default=None,
                        help="Location filter for --linkedin "
                             "(e.g. \"Hyderabad, India\")")
    parser.add_argument("--geo-id", default=None,
                        help="LinkedIn geoId for --linkedin (more reliable "
                             "than --location)")
    parser.add_argument("--limit", type=int, default=25,
                        help="Max --linkedin/--remote-boards/--workable-search/"
                             "--themuse-search/--hn-hiring results to scrape "
                             "(default 25)")
    parser.add_argument("--days", type=int, default=None,
                        help="Only LinkedIn postings from the last N days")
    parser.add_argument("--remote", default=None,
                        choices=["onsite", "remote", "hybrid"],
                        help="Work-mode filter for --linkedin")
    parser.add_argument("--discover", action="append", default=[],
                        metavar="BOARD:ID",
                        help="Enumerate every open posting on a company's "
                             "career portal, e.g. --discover lever:spotify "
                             "--discover workday:acme:wd3:acme_ext "
                             "--discover eightfold:paypal:paypal.com "
                             "(repeatable)")
    parser.add_argument("--discover-seeds", nargs="?", const="*",
                        default=None, metavar="CATEGORY",
                        help="Sweep the curated registry of verified company "
                             "career boards for one category, e.g. "
                             "--discover-seeds fintech (omit the category "
                             "to sweep every seed; combine with "
                             "--keyword-filter to narrow results)")
    parser.add_argument("--list-seeds", action="store_true",
                        help="List the curated seed registry of verified "
                             "company career boards and exit")
    parser.add_argument("--locations", default=None, metavar="\"A,B\"",
                        help="Comma-separated preferred locations for this "
                             "run; overrides the profile's locations in "
                             "scoring, e.g. --locations \"Pune,Remote\"")
    parser.add_argument("--location-filter", default=None, metavar="TEXT",
                        help="Keep only postings whose location contains "
                             "TEXT (case-insensitive), e.g. "
                             "--location-filter hyderabad")
    parser.add_argument("--keyword-filter", default=None, metavar="\"A,B\"",
                        help="Keep only postings whose title contains one of "
                             "these comma-separated keywords, e.g. "
                             "--keyword-filter \"analyst,engineer\"")
    parser.add_argument("--min-score", type=int, default=None, metavar="N",
                        help="Keep only postings scoring N or higher "
                             "(0-100), e.g. --min-score 65")
    parser.add_argument("--exclude-companies", default=None, metavar="\"A,B\"",
                        help="Drop postings from these companies "
                             "(case-insensitive), e.g. "
                             '--exclude-companies "tcs,infosys"')
    parser.add_argument("--exclude-keywords", default=None, metavar="\"A,B\"",
                        help="Drop postings whose title contains these "
                             "keywords, e.g. "
                             '--exclude-keywords "intern,trainee"')
    parser.add_argument("--seniority", default=None, metavar="LEVEL[,LEVEL]",
                        help="Keep only postings at these seniority levels "
                             "(comma-separated): intern, entry, mid, senior, "
                             "staff, lead, manager, director, executive, "
                             "unknown; e.g. --seniority senior,staff")
    parser.add_argument("--job-type", default=None, metavar="TYPE[,TYPE]",
                        help="Keep only postings with these canonical job "
                             "types: full-time, part-time, contract, "
                             "temporary, internship, other, unknown; "
                             "e.g. --job-type full-time,contract")
    parser.add_argument("--min-salary", default=None, metavar="AMOUNT",
                        help="Keep only postings whose salary range can "
                             "reach AMOUNT (annual), e.g. --min-salary "
                             "\"80K USD\" or --min-salary \"25 LPA\"; "
                             "postings without a parsed salary are kept")
    parser.add_argument("--max-salary", default=None, metavar="AMOUNT",
                        help="Keep only postings whose salary range bottom "
                             "is at or below AMOUNT (annual), e.g. "
                             "--max-salary \"200K USD\"; postings without "
                             "a parsed salary are kept")
    parser.add_argument("--max-age", type=int, default=None, metavar="DAYS",
                        help="Keep only postings posted within the last "
                             "DAYS days (JobSpy-style freshness filter); "
                             "postings with an unknown age are kept")
    parser.add_argument("--watch", default=None, metavar="STATE.json",
                        help="Watch mode: flag postings never seen before; "
                             "seen postings persist in STATE.json")
    parser.add_argument("--proxy", action="append", default=[],
                        metavar="URL",
                        help="Proxy URL for HTTP requests "
                             "(e.g. http://user:pass@host:8080); "
                             "repeatable, rotated round-robin")
    parser.add_argument("--proxies-file", default=None, metavar="PATH",
                        help="Text file with one proxy URL per line "
                             "(# comments allowed)")
    parser.add_argument("--webhook-url", action="append", default=[],
                        metavar="URL",
                        help="POST the ranked results as JSON to this "
                             "webhook URL (repeatable); see README for "
                             "Slack/Discord/Google Sheets/Notion recipes")
    parser.add_argument("--webhook-mode", default="plain",
                        choices=["plain", "slack", "discord", "pushover",
                                 "telegram", "ntfy", "email"],
                        help="Webhook payload format (default plain; "
                             "slack/discord send a chat notification; "
                             "pushover/telegram/ntfy/email send an alert "
                             "via the Pushover API, a Telegram bot, ntfy.sh, "
                             "or SMTP email)")
    parser.add_argument("--ntfy-topic", default=None, metavar="TOPIC",
                        help="ntfy.sh topic name for --webhook-mode ntfy "
                             "(or JOBSCRAPER_NTFY_TOPIC; the topic is the "
                             "only credential needed)")
    parser.add_argument("--ntfy-server", default=None, metavar="URL",
                        help="ntfy server base URL for --webhook-mode ntfy "
                             "(default https://ntfy.sh, or "
                             "JOBSCRAPER_NTFY_SERVER; self-hosted "
                             "servers welcome)")
    parser.add_argument("--ntfy-token", default=None, metavar="TOKEN",
                        help="ntfy access token for --webhook-mode ntfy "
                             "(or JOBSCRAPER_NTFY_TOKEN; only needed when "
                             "the topic is access-protected)")
    parser.add_argument("--ntfy-priority", default=None,
                        choices=["low", "default", "high"],
                        help="ntfy message priority for --webhook-mode "
                             "ntfy (default default, or "
                             "JOBSCRAPER_NTFY_PRIORITY)")
    parser.add_argument("--pushover-token", default=None, metavar="TOKEN",
                        help="Pushover application token for --webhook-mode "
                             "pushover (or JOBSCRAPER_PUSHOVER_TOKEN)")
    parser.add_argument("--pushover-user", default=None, metavar="KEY",
                        help="Pushover user key for --webhook-mode pushover "
                             "(or JOBSCRAPER_PUSHOVER_USER)")
    parser.add_argument("--telegram-token", default=None, metavar="TOKEN",
                        help="Telegram bot token for --webhook-mode "
                             "telegram (or JOBSCRAPER_TELEGRAM_TOKEN)")
    parser.add_argument("--telegram-chat-id", default=None, metavar="ID",
                        help="Telegram chat id for --webhook-mode telegram "
                             "(or JOBSCRAPER_TELEGRAM_CHAT_ID)")
    parser.add_argument("--smtp-host", default=None, metavar="HOST",
                        help="SMTP server host for --webhook-mode email "
                             "(or JOBSCRAPER_SMTP_HOST)")
    parser.add_argument("--smtp-port", type=int, default=None,
                        metavar="PORT",
                        help="SMTP server port for --webhook-mode email "
                             "(default 587; or JOBSCRAPER_SMTP_PORT)")
    parser.add_argument("--smtp-user", default=None, metavar="USER",
                        help="SMTP username for --webhook-mode email "
                             "(or JOBSCRAPER_SMTP_USER)")
    parser.add_argument("--smtp-password", default=None, metavar="PASSWORD",
                        help="SMTP password (app password) for "
                             "--webhook-mode email "
                             "(or JOBSCRAPER_SMTP_PASSWORD)")
    parser.add_argument("--smtp-from", default=None, metavar="ADDRESS",
                        help="From address for --webhook-mode email "
                             "(defaults to the SMTP username; "
                             "or JOBSCRAPER_SMTP_FROM)")
    parser.add_argument("--smtp-to", default=None, metavar="ADDRESS",
                        help="Recipient address for --webhook-mode email "
                             "(or JOBSCRAPER_SMTP_TO)")
    parser.add_argument("--no-smtp-tls", action="store_true",
                        help="Skip STARTTLS for --webhook-mode email "
                             "(for internal relays without TLS support)")
    parser.add_argument("--webhook-only-new", action="store_true",
                        help="With --watch, only notify about postings "
                             "flagged new since the last run "
                             "(skips silently when nothing is new)")
    parser.add_argument("--webhook-top", type=int, default=25, metavar="N",
                        help="Max postings included in the webhook payload "
                             "(default 25)")
    parser.add_argument("--respect-robots", action="store_true",
                        help="Honor robots.txt for every fetched URL "
                             "(hosts with no reachable robots.txt are "
                             "treated as allowed; also honored via the "
                             "JOBSCRAPER_RESPECT_ROBOTS env var)")
    parser.add_argument("--browser-pool", action="store_true",
                        help="Reuse one headless Chromium per worker "
                             "thread for the whole run instead of "
                             "launching a fresh browser per posting "
                             "(requires the 'browser' extra; also honored "
                             "via the JOBSCRAPER_BROWSER_POOL env var)")
    profiles_group = parser.add_argument_group(
        "search profiles",
        "Named saved configurations: save a full invocation with "
        "--save-search-profile NAME, rerun it later with --search-profile "
        "NAME. Saved values act as defaults; any flag given on the "
        "command line overrides the profile.")
    profiles_group.add_argument(
        "--search-profile", default=None, metavar="NAME",
        help="Load a saved search profile as defaults for every option "
             "(flags given on the command line still win)")
    profiles_group.add_argument(
        "--save-search-profile", default=None, metavar="NAME",
        help="Save the current invocation's options as a named search "
             "profile and exit (notification credentials are stored too; "
             "the file is written with mode 0600)")
    profiles_group.add_argument(
        "--list-search-profiles", action="store_true",
        help="List the names of all saved search profiles and exit")
    profiles_group.add_argument(
        "--search-profiles-file", default=None, metavar="PATH",
        help="Use a different search-profiles JSON file "
             "(or JOBSCRAPER_SEARCH_PROFILES)")
    return parser


def _applications_db_path(args: argparse.Namespace) -> str:
    """Where the applications database lives for this invocation."""
    from jobscraper.applications import DEFAULT_DB
    if args.applications_db:
        return args.applications_db
    if getattr(args, "sqlite", None):
        return args.sqlite
    return DEFAULT_DB


def _run_application_tracker(args: argparse.Namespace) -> int:
    """Handle --mark-applied / --list-applications / --applications-stats."""
    from jobscraper.applications import (
        VALID_STATUSES,
        application_summary,
        connect,
        enrich_from_postings,
        list_applications,
        mark_applied,
    )
    path = _applications_db_path(args)
    conn = connect(path)
    try:
        if args.mark_applied:
            if args.application_status not in VALID_STATUSES:
                print(f"error: --application-status must be one of "
                      f"{', '.join(VALID_STATUSES)}", file=sys.stderr)
                return 2
            for url in args.mark_applied:
                details = enrich_from_postings(conn, url)
                app = mark_applied(
                    conn, url, status=args.application_status,
                    notes=args.application_notes, **details)
                title = app.title or "(untitled)"
                print(f"{app.status:<12}{title[:52]:<54}{app.url}")
            print(f"\nTracked in {path}")
        if args.applications_stats or args.list_applications:
            summary = application_summary(conn)
            total = sum(summary.values())
            if args.applications_stats:
                print(f"Applications tracked in {path}: {total}")
                for status in VALID_STATUSES:
                    print(f"  {status:<12}{summary[status]}")
            if args.list_applications:
                wanted = args.application_status
                if wanted != "all" and wanted not in VALID_STATUSES:
                    print(f"error: --application-status must be 'all' or "
                          f"one of {', '.join(VALID_STATUSES)}",
                          file=sys.stderr)
                    return 2
                apps = list_applications(
                    conn, None if wanted == "all" else wanted)
                print(f"{'STATUS':<12}{'TITLE':<54}URL")
                for app in apps:
                    print(f"{app.status:<12}"
                          f"{(app.title or '(untitled)')[:52]:<54}"
                          f"{app.url}")
                print(f"\n{len(apps)} application(s)")
    finally:
        conn.close()
    return 0


def _resolve_search_profile_argv(argv: list[str] | None) -> tuple[str | None,
                                                           str | None]:
    """Extract --search-profile/--search-profiles-file before full parsing."""
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--search-profile", default=None)
    pre.add_argument("--search-profiles-file", default=None)
    known, _ = pre.parse_known_args(argv)
    return known.search_profile, known.search_profiles_file


def _apply_search_profile(parser: argparse.ArgumentParser,
                          name: str, path: str | None) -> int | None:
    """Load a saved profile and set its options as parser defaults.

    Saved values act as defaults only: any flag actually given on the
    command line still overrides the profile.  Unknown keys (e.g. from a
    renamed option) are ignored with a warning.  Returns an exit code on
    failure, else None.
    """
    from jobscraper.search_profiles import ProfileError, get_profile
    try:
        stored = get_profile(name, path)
    except ProfileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    valid_dests = {action.dest for action in parser._actions}
    known = {key: value for key, value in stored.items()
             if key in valid_dests}
    unknown = sorted(set(stored) - set(known))
    if unknown:
        print(f"warning: search profile '{name}' has unrecognized "
              f"options, ignored: {', '.join(unknown)}", file=sys.stderr)
    parser.set_defaults(**known)
    return None


def main(argv: list[str] | None = None) -> int:
    from jobscraper.search_profiles import (
        ProfileError,
        default_profiles_path,
        list_profile_names,
        save_search_profile,
    )
    parser = build_parser()
    profile_name, profiles_file = _resolve_search_profile_argv(argv)
    if profile_name:
        exit_code = _apply_search_profile(parser, profile_name, profiles_file)
        if exit_code is not None:
            return exit_code
    args = parser.parse_args(argv)

    profiles_path = args.search_profiles_file or str(default_profiles_path())
    if args.list_search_profiles:
        for name in list_profile_names(profiles_path):
            print(name)
        return 0
    if args.save_search_profile:
        from jobscraper.search_profiles import sanitize_for_profile
        try:
            saved = save_search_profile(
                args.save_search_profile, sanitize_for_profile(vars(args)),
                profiles_path)
        except ProfileError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"Saved search profile '{args.save_search_profile}' "
              f"to {saved}")
        return 0

    if args.list_seeds:
        from jobscraper.seeds import SeedError, categories, load_seeds
        try:
            seeds = load_seeds()
        except SeedError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"{'COMPANY':<16}{'BOARD':<14}{'CATEGORY':<18}SPEC")
        for seed in sorted(seeds, key=lambda s: s.name.lower()):
            print(f"{seed.name:<16}{seed.board:<14}"
                  f"{','.join(seed.categories):<18}{seed.spec}")
        print(f"\n{len(seeds)} seeds, categories: "
              f"{', '.join(categories(seeds))}")
        print("Sweep a category: "
              "jobscraper --discover-seeds CATEGORY --keyword-filter X")
        return 0

    if (args.mark_applied or args.list_applications
            or args.applications_stats):
        return _run_application_tracker(args)

    from jobscraper.http import configure_proxies, load_proxies_file, proxies_from_env
    proxy_specs = list(args.proxy)
    if args.proxies_file:
        try:
            proxy_specs += load_proxies_file(args.proxies_file)
        except OSError as exc:
            print(f"error: cannot read --proxies-file: {exc}",
                  file=sys.stderr)
            return 2
    if not proxy_specs:
        proxy_specs = proxies_from_env()
    if proxy_specs:
        try:
            configure_proxies(proxy_specs)
        except ValueError as exc:
            print(f"error: bad proxy: {exc}", file=sys.stderr)
            return 2
        print(f"proxies: {len(proxy_specs)} configured (round-robin)")

    urls = list(args.urls)
    if args.urlfile:
        with open(args.urlfile, encoding="utf-8") as fh:
            urls += [line.strip() for line in fh
                     if line.strip() and not line.startswith("#")]

    if args.discover:
        from jobscraper.boards import DISCOVERERS
        for spec in args.discover:
            board, _, ident = spec.partition(":")
            discover = DISCOVERERS.get(board.lower())
            if not discover:
                print(f"error: unknown board '{board}' for --discover "
                      f"(choose from: {', '.join(sorted(DISCOVERERS))})",
                      file=sys.stderr)
                return 2
            try:
                found = discover(ident)
            except Exception as exc:
                print(f"discover {spec} failed: {exc}", file=sys.stderr)
                continue
            print(f"discover {spec}: {len(found)} postings")
            urls += found

    if args.discover_seeds is not None:
        from jobscraper.seeds import SeedError, discover_seeds, seeds_for
        category = None if args.discover_seeds == "*" else args.discover_seeds
        try:
            seeds = seeds_for(category)
        except SeedError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"discover-seeds: sweeping {len(seeds)} verified boards"
              + (f" (category: {category})" if category else ""))
        found = discover_seeds(seeds)
        for spec, seed_urls in found.items():
            print(f"discover {spec}: {len(seed_urls)} postings")
            for url in seed_urls:
                if url not in urls:
                    urls.append(url)

    if args.linkedin:
        from jobscraper.sources.linkedin import search_jobs
        added = 0
        start = 0
        while added < args.limit:
            cards = search_jobs(args.linkedin, location=args.location,
                                geo_id=args.geo_id, start=start,
                                remote=args.remote,
                                posted_within_days=args.days)
            if not cards:
                break
            for card in cards:
                if card["url"] not in urls:
                    urls.append(card["url"])
                    added += 1
                    if added >= args.limit:
                        break
            start += 10
            if len(cards) < 10:
                break
        print(f"linkedin: {added} postings for '{args.linkedin}'")

    if args.remote_boards:
        from jobscraper.sources.remote_boards import search_remote_boards
        cards = search_remote_boards(args.remote_boards, limit=args.limit)
        added = 0
        for card in cards:
            if card.get("url") and card["url"] not in urls:
                urls.append(card["url"])
                added += 1
                if added >= args.limit:
                    break
        print(f"remote-boards: {added} postings for "
              f"'{args.remote_boards}'")

    if args.workable_search:
        from jobscraper.sources.workable_search import search_workable
        cards = search_workable(args.workable_search, limit=args.limit)
        added = 0
        for card in cards:
            if card.get("url") and card["url"] not in urls:
                urls.append(card["url"])
                added += 1
                if added >= args.limit:
                    break
        print(f"workable-search: {added} postings for "
              f"'{args.workable_search}'")

    if args.themuse_search:
        from jobscraper.sources.themuse import search_themuse
        cards = search_themuse(args.themuse_search,
                               location=args.themuse_location,
                               category=args.themuse_category,
                               limit=args.limit)
        added = 0
        for card in cards:
            if card.get("url") and card["url"] not in urls:
                urls.append(card["url"])
                added += 1
                if added >= args.limit:
                    break
        print(f"themuse-search: {added} postings for "
              f"'{args.themuse_search}'")

    profile = load_profile(args.profile)
    problems = validate_profile(profile)
    if problems:
        print("error: invalid profile "
              f"({args.profile or 'built-in template'}):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    hn_postings = []
    hn_stat = None
    if args.hn_hiring:
        from jobscraper.models import SourceStat
        from jobscraper.sources.hn_hiring import (
            build_postings,
            scrape_hn_hiring,
        )
        try:
            cards, thread = scrape_hn_hiring(
                args.hn_hiring, month=args.hn_month,
                limit=args.limit,
                max_comments=args.hn_max_comments)
        except Exception as exc:
            print(f"hn-hiring FAILED: {exc}", file=sys.stderr)
            return 1
        hn_postings = build_postings(
            cards, thread["time"], profile=profile,
            tracker_path=args.tracker, no_score=args.no_score)
        hn_stat = SourceStat(name="hn_whoishiring",
                             attempted=len(cards),
                             ok=len([p for p in hn_postings
                                     if not p.error]),
                             errored=len([p for p in hn_postings
                                          if p.error]))
        print(f"hn-hiring: {len(hn_postings)} postings for "
              f"'{args.hn_hiring}' ({thread['title']})")

    if not urls and not hn_postings:
        print("error: give URLs or --urls file", file=sys.stderr)
        return 2

    if args.locations:
        profile["locations"] = [loc.strip() for loc in
                                args.locations.split(",") if loc.strip()]

    if args.max_age is not None and args.max_age < 0:
        print("error: --max-age must be >= 0", file=sys.stderr)
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    out = args.out or f"scrape-{stamp}.json"
    md = args.md or f"scrape-{stamp}.md"

    def progress(done: int, total: int) -> None:
        print(f"[{done}/{total}]", flush=True)

    before = len(urls) + len(hn_postings)
    results, new_count, closed, stats = run_pipeline(
        urls, profile=profile, tracker_path=args.tracker,
        no_score=args.no_score, use_cache=not args.no_cache,
        workers=args.workers, no_dedupe=args.no_dedupe,
        fuzzy_dedupe=args.fuzzy_dedupe,
        location_filter=args.location_filter,
        keyword_filter=args.keyword_filter,
        exclude_companies=args.exclude_companies,
        exclude_keywords=args.exclude_keywords,
        min_score=args.min_score, seniority=args.seniority,
        job_type=args.job_type,
        salary_min=args.min_salary, salary_max=args.max_salary,
        max_age=args.max_age,
        watch_path=args.watch,
        respect_robots=args.respect_robots,
        browser_pool=args.browser_pool,
        detect_reposts=args.detect_reposts,
        progress_cb=progress, run_stats=True)
    if hn_postings:
        # HN comments arrive as finished postings (no page to fetch),
        # so they get the same post-processing the pipeline applies to
        # URL results: dedupe, filters, repost flags and watch state.
        from jobscraper.pipeline import (
            apply_filters,
            apply_watch,
            dedupe_results,
        )
        from jobscraper.reposts import mark_reposts
        posts = (dedupe_results(hn_postings)
                 if not args.no_dedupe else hn_postings)
        from jobscraper.dedupe import (
            fuzzy_dedupe_from_env,
            fuzzy_dedupe_results,
        )
        if args.fuzzy_dedupe or fuzzy_dedupe_from_env():
            posts, _ = fuzzy_dedupe_results(posts)
        posts = apply_filters(
            posts, args.location_filter, args.keyword_filter,
            args.exclude_companies, args.exclude_keywords,
            seniority_filter=args.seniority,
            job_type_filter=args.job_type,
            salary_min=args.min_salary, salary_max=args.max_salary,
            max_age=args.max_age)
        if args.min_score is not None:
            posts = [p for p in posts
                     if p.error or (p.match and
                                    p.match.total >= args.min_score)]
        results.extend(posts)
        if args.detect_reposts:
            mark_reposts(results)
        if args.watch:
            posts, hn_new, hn_closed = apply_watch(posts, args.watch)
            new_count += hn_new
            closed.extend(hn_closed)
        if hn_stat is not None:
            stats.append(hn_stat)
    after = sum(1 for p in results if not p.error)
    if before - after:
        print(f"Filtered {before - after} posting(s) out")
    merged = sum(1 for p in results
                 if any(n.startswith("fuzzy-dedupe merged")
                        for n in p.fetch_notes))
    if merged:
        print(f"Fuzzy-dedupe merged near-duplicates into {merged} "
              f"posting(s)")
    reposts = sum(1 for p in results
                  if p.signals.get("repost_of"))
    if reposts:
        print(f"Repost check: flagged {reposts} repost(s) of same-content "
              f"posting(s)")
    if args.watch:
        print(f"{new_count} new posting(s) since last run")
        if closed:
            print(f"{len(closed)} closed since last run:")
            for entry in closed:
                title = entry.get("title") or "(untitled)"
                print(f"  - {title} ({entry.get('url')})")

    # Annotate results with any tracked application status before the
    # exports are written, so JSON/CSV/HTML/Excel all carry it.
    from jobscraper.applications import connect, get_application
    conn = connect(_applications_db_path(args))
    try:
        tracked = 0
        for post in results:
            app = get_application(conn, post.url)
            if app is not None:
                post.application_status = app.status
                tracked += 1
        if tracked:
            print(f"Applications: {tracked} of {len(results)} posting(s) "
                  f"already tracked")
    finally:
        conn.close()

    with open(out, "w", encoding="utf-8") as fh:
        json.dump([p.to_dict() for p in results], fh, indent=2,
                  ensure_ascii=False)
    write_markdown(results, md, profile)
    if args.csv:
        write_csv(results, args.csv, profile)
        print(f"CSV:  {args.csv}")
    if args.html:
        write_html(results, args.html, profile)
        print(f"HTML: {args.html}")
    if args.excel:
        try:
            write_xlsx(results, args.excel, profile)
        except RuntimeError as exc:
            print(f"Excel: {exc}", file=sys.stderr)
            return 1
        print(f"XLSX: {args.excel}")
    if args.rss:
        write_rss(results, args.rss, profile)
        print(f"RSS:  {args.rss}")
    if args.jsonl:
        write_jsonl(results, args.jsonl, profile)
        print(f"JSONL: {args.jsonl}")
    if args.sqlite:
        from jobscraper.reposts import (
            detect_reposts_from_env,
            flag_historical_reposts,
        )
        if args.detect_reposts or detect_reposts_from_env():
            ghosts = flag_historical_reposts(results, args.sqlite)
            if ghosts:
                print(f"Repost check: {ghosts} posting(s) look like reposts "
                      f"of previously closed postings (possible ghost jobs)")
        write_sqlite(results, args.sqlite, profile)
        print(f"SQLite: {args.sqlite}")

    from jobscraper.notify import (
        deliver,
        send_email_digest,
        send_ntfy,
        send_pushover,
        send_telegram,
        smtp_settings,
        webhook_urls_from_env,
    )
    if args.webhook_mode in ("pushover", "telegram", "ntfy", "email"):
        # Direct-notification channels: post to the fixed service endpoint;
        # --webhook-url is ignored in these modes.
        if args.webhook_mode == "pushover":
            sender, name = send_pushover, "Pushover"
            creds = {"token": args.pushover_token,
                     "user": args.pushover_user}
        elif args.webhook_mode == "telegram":
            sender, name = send_telegram, "Telegram"
            creds = {"token": args.telegram_token,
                     "chat_id": args.telegram_chat_id}
        elif args.webhook_mode == "ntfy":
            sender, name = send_ntfy, "ntfy"
            creds = {"server": args.ntfy_server,
                     "topic": args.ntfy_topic,
                     "token": args.ntfy_token,
                     "priority": args.ntfy_priority}
        else:
            sender, name = send_email_digest, "Email"
            try:
                settings = smtp_settings(
                    host=args.smtp_host, port=args.smtp_port,
                    user=args.smtp_user, password=args.smtp_password,
                    from_addr=args.smtp_from, to_addr=args.smtp_to,
                    use_tls=not args.no_smtp_tls)
            except ValueError as exc:
                print(f"Email: {exc}", file=sys.stderr)
                return 1
            creds = {"settings": settings}
        try:
            ok, detail = sender(
                results, only_new=args.webhook_only_new,
                top=args.webhook_top, **creds)
        except ValueError as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            return 1
        if ok is None:
            print(f"{name}: skipped ({detail})")
        elif ok:
            print(f"{name}: {detail}")
        else:
            print(f"{name} FAILED: {detail}", file=sys.stderr)
            return 1
    webhook_urls = list(args.webhook_url) + webhook_urls_from_env()
    if webhook_urls and args.webhook_mode not in ("pushover", "telegram",
                                                  "ntfy", "email"):
        seen, deduped = set(), []
        for url in webhook_urls:
            if url not in seen:
                seen.add(url)
                deduped.append(url)
        results_ = deliver(results, deduped, mode=args.webhook_mode,
                           only_new=args.webhook_only_new,
                           top=args.webhook_top)
        failed = 0
        for host, ok, detail in results_:
            if ok is None:
                print(f"Webhook {host}: skipped ({detail})")
            elif ok:
                print(f"Webhook {detail}")
            else:
                print(f"Webhook FAILED: {detail}", file=sys.stderr)
                failed += 1
        if failed:
            print("error: one or more webhooks failed",
                  file=sys.stderr)
            return 1
    ok = sum(1 for p in results if not p.error)
    print(f"\nDone: {ok}/{len(results)} scraped OK")
    if stats:
        print("Sources:")
        for stat in stats:
            secs = stat.duration_ms / 1000.0
            print(f"  {stat.name:<16}attempted={stat.attempted} "
                  f"ok={stat.ok} errored={stat.errored} "
                  f"filtered={stat.filtered} status={stat.status} "
                  f"({secs:.1f}s)")
            if stat.status in ("failed", "partial"):
                top = sorted(stat.errors.items(),
                             key=lambda kv: kv[1], reverse=True)[:2]
                for msg, count in top:
                    print(f"    - {msg} (x{count})")
    print(f"JSON: {out}\nMD:   {md}")
    for post in sorted(results,
                       key=lambda p: p.match.total if p.match else -1,
                       reverse=True)[:10]:
        if post.match:
            print(f"  {post.match.total:3d}  {(post.title or '?')[:50]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
