"""Command-line interface for jobscraper."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from jobscraper.pipeline import apply_filters, dedupe_results, process_url
from jobscraper.reporting import write_csv, write_markdown
from jobscraper.scoring import load_profile


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
    parser.add_argument("--location", default=None,
                        help="Location filter for --linkedin "
                             "(e.g. \"Hyderabad, India\")")
    parser.add_argument("--geo-id", default=None,
                        help="LinkedIn geoId for --linkedin (more reliable "
                             "than --location)")
    parser.add_argument("--limit", type=int, default=25,
                        help="Max LinkedIn results to scrape (default 25)")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

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

    if not urls:
        print("error: give URLs or --urls file", file=sys.stderr)
        return 2

    profile = load_profile(args.profile)
    if args.locations:
        profile["locations"] = [loc.strip() for loc in
                                args.locations.split(",") if loc.strip()]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    out = args.out or f"scrape-{stamp}.json"
    md = args.md or f"scrape-{stamp}.md"

    def work(url: str):
        try:
            return process_url(url, use_cache=not args.no_cache,
                               profile=profile, tracker_path=args.tracker,
                               no_score=args.no_score)
        except Exception as exc:  # never let one URL kill the run
            from jobscraper.models import Posting
            return Posting(url=url, error=str(exc)[:300])

    results = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(work, url): url for url in urls}
        done = 0
        for future in as_completed(futures):
            done += 1
            print(f"[{done}/{len(urls)}] {futures[future]}", flush=True)
            results.append(future.result())
    order = {url: i for i, url in enumerate(urls)}
    results.sort(key=lambda post: order.get(post.url, 0))

    if not args.no_dedupe:
        before = sum(1 for p in results if not p.error)
        results = dedupe_results(results)
        dropped = before - sum(1 for p in results if not p.error)
        if dropped:
            print(f"Deduped {dropped} cross-board duplicate(s)")

    if args.location_filter or args.keyword_filter:
        before = len(results)
        results = apply_filters(results, args.location_filter,
                                args.keyword_filter)
        print(f"Filtered {before - len(results)} posting(s) "
              f"out by location/keyword filters")

    with open(out, "w", encoding="utf-8") as fh:
        json.dump([p.to_dict() for p in results], fh, indent=2,
                  ensure_ascii=False)
    write_markdown(results, md, profile)
    if args.csv:
        write_csv(results, args.csv, profile)
        print(f"CSV:  {args.csv}")
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
