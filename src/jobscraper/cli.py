"""Command-line interface for jobscraper."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from jobscraper.pipeline import run_pipeline
from jobscraper.reporting import write_csv, write_html, write_markdown, write_xlsx
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
    parser.add_argument("--csv", default=None, help="CSV export path")
    parser.add_argument("--html", default=None, help="HTML report path")
    parser.add_argument("--excel", default=None,
                        help="Excel (.xlsx) export path "
                             "(needs the 'excel' extra)")
    parser.add_argument("--no-cache", action="store_true",
                        help="Ignore the local page cache and refetch")
    parser.add_argument("--workers", type=int, default=4,
                        help="Parallel fetch workers (default 4)")
    parser.add_argument("--no-dedupe", action="store_true",
                        help="Keep cross-board duplicates instead of "
                             "dropping them")
    parser.add_argument("--linkedin", default=None, metavar="KEYWORDS",
                        help="Search LinkedIn jobs for KEYWORDS and scrape "
                             "the results")
    parser.add_argument("--remote-boards", default=None, metavar="KEYWORDS",
                        help="Search remote-only job boards (RemoteOK, "
                             "Remotive, We Work Remotely, Working Nomads) "
                             "for KEYWORDS and scrape the results")
    parser.add_argument("--location", default=None,
                        help="Location filter for --linkedin "
                             "(e.g. \"Hyderabad, India\")")
    parser.add_argument("--geo-id", default=None,
                        help="LinkedIn geoId for --linkedin (more reliable "
                             "than --location)")
    parser.add_argument("--limit", type=int, default=25,
                        help="Max --linkedin/--remote-boards results to "
                             "scrape (default 25)")
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
                                 "telegram"],
                        help="Webhook payload format (default plain; "
                             "slack/discord send a chat notification; "
                             "pushover/telegram send an alert via the "
                             "Pushover API or a Telegram bot)")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

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

    if not urls:
        print("error: give URLs or --urls file", file=sys.stderr)
        return 2

    profile = load_profile(args.profile)
    problems = validate_profile(profile)
    if problems:
        print("error: invalid profile "
              f"({args.profile or 'built-in template'}):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2
    if args.locations:
        profile["locations"] = [loc.strip() for loc in
                                args.locations.split(",") if loc.strip()]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    out = args.out or f"scrape-{stamp}.json"
    md = args.md or f"scrape-{stamp}.md"

    def progress(done: int, total: int) -> None:
        print(f"[{done}/{total}]", flush=True)

    before = len(urls)
    results, new_count, closed = run_pipeline(
        urls, profile=profile, tracker_path=args.tracker,
        no_score=args.no_score, use_cache=not args.no_cache,
        workers=args.workers, no_dedupe=args.no_dedupe,
        location_filter=args.location_filter,
        keyword_filter=args.keyword_filter,
        exclude_companies=args.exclude_companies,
        exclude_keywords=args.exclude_keywords,
        min_score=args.min_score, watch_path=args.watch,
        respect_robots=args.respect_robots,
        progress_cb=progress)
    after = sum(1 for p in results if not p.error)
    if before - after:
        print(f"Filtered {before - after} posting(s) out")
    if args.watch:
        print(f"{new_count} new posting(s) since last run")
        if closed:
            print(f"{len(closed)} closed since last run:")
            for entry in closed:
                title = entry.get("title") or "(untitled)"
                print(f"  - {title} ({entry.get('url')})")

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

    from jobscraper.notify import (
        deliver,
        send_pushover,
        send_telegram,
        webhook_urls_from_env,
    )
    if args.webhook_mode in ("pushover", "telegram"):
        # Direct-notification channels: post to the fixed service endpoint;
        # --webhook-url is ignored in these modes.
        if args.webhook_mode == "pushover":
            sender, name = send_pushover, "Pushover"
            creds = {"token": args.pushover_token,
                     "user": args.pushover_user}
        else:
            sender, name = send_telegram, "Telegram"
            creds = {"token": args.telegram_token,
                     "chat_id": args.telegram_chat_id}
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
    if webhook_urls and args.webhook_mode not in ("pushover", "telegram"):
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
    print(f"JSON: {out}\nMD:   {md}")
    for post in sorted(results,
                       key=lambda p: p.match.total if p.match else -1,
                       reverse=True)[:10]:
        if post.match:
            print(f"  {post.match.total:3d}  {(post.title or '?')[:50]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
