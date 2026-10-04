"""Command-line interface for jobscraper."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from jobscraper.pipeline import dedupe_results, process_url
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    urls = list(args.urls)
    if args.urlfile:
        with open(args.urlfile, encoding="utf-8") as fh:
            urls += [line.strip() for line in fh
                     if line.strip() and not line.startswith("#")]
    if not urls:
        print("error: give URLs or --urls file", file=sys.stderr)
        return 2

    profile = load_profile(args.profile)

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
