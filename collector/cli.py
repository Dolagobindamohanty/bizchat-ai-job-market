"""Command line entry point for an API-backed CSV snapshot."""

import argparse
import csv
import json
import logging
import os
from pathlib import Path

from collector.providers import collect_enabled_providers, load_greenhouse_config
from collector.snapshot import filter_recent_rows, write_snapshot
from collector.multisource import collect_sources


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch authorized job APIs and write a validated CSV snapshot")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--timeout", type=float, default=15.0, help="HTTP timeout in seconds, default: 15")
    parser.add_argument("--max-posted-age-days", type=int,
                        help="Publish only dated postings within this many days of today")
    parser.add_argument("--adzuna-country", default="in", help="Adzuna country code, default: in")
    parser.add_argument("--allow-empty", action="store_true", help="Allow publishing an empty snapshot")
    parser.add_argument("--greenhouse-config", type=Path,
                        help="Explicit JSON Greenhouse board config; omitted means Greenhouse is disabled")
    parser.add_argument("--greenhouse-only", action="store_true",
                        help="Use only the configured Greenhouse boards; skip Arbeitnow and Adzuna")
    parser.add_argument("--sources-config", type=Path,
                        help="Explicit Greenhouse/Lever/Ashby boards; skips Arbeitnow and Adzuna")
    parser.add_argument("--apify-config", type=Path,
                        help="Explicit opt-in Apify actor config; requires APIFY_API_TOKEN")
    parser.add_argument("--allow-partial", action="store_true",
                        help="Allow publishing sources-config results when some boards fail")
    parser.add_argument("--preserve-uncollected-sources", action="store_true",
                        help="Keep existing output rows from sources not configured in this run; apply the same freshness window")
    parser.add_argument("--report", type=Path, help="Write a safe JSON per-board collection summary")
    args = parser.parse_args()
    if (args.sources_config or args.apify_config) and (args.greenhouse_config or args.greenhouse_only):
        parser.error("--sources-config/--apify-config are mutually exclusive with --greenhouse-config and --greenhouse-only")
    if (args.allow_partial or args.report) and not (args.sources_config or args.apify_config):
        parser.error("--allow-partial and --report require --sources-config or --apify-config")
    if args.preserve_uncollected_sources and not (args.sources_config or args.apify_config):
        parser.error("--preserve-uncollected-sources requires --sources-config or --apify-config")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    if args.sources_config or args.apify_config:
        from collector.multisource import load_sources_config
        from collector.apify import load_apify_config, collect_apify_sources

        # A report must never overwrite the snapshot or its input configuration.
        input_paths = {args.output.resolve()}
        if args.sources_config:
            input_paths.add(args.sources_config.resolve())
        if args.apify_config:
            input_paths.add(args.apify_config.resolve())
        if args.report and args.report.resolve() in input_paths:
            parser.error("--report must differ from --output and --sources-config")
        rows, reports, collected_sources = [], [], set()
        if args.sources_config:
            config = load_sources_config(args.sources_config)
            collected_sources.update(board["source"] for board in config["boards"])
            official_rows, official_reports = collect_sources(config, timeout=args.timeout)
            rows.extend(official_rows)
            reports.extend(official_reports)
        if args.apify_config:
            apify_config = load_apify_config(args.apify_config)
            collected_sources.update(actor["source"] for actor in apify_config["actors"])
            apify_rows, apify_reports = collect_apify_sources(apify_config, token=os.getenv("APIFY_API_TOKEN"), timeout=args.timeout)
            rows.extend(apify_rows)
            reports.extend(apify_reports)
        rows = filter_recent_rows(rows, args.max_posted_age_days)
        failed = any(report["status"] not in {"ok", "skipped"} for report in reports)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps({"boards": reports, "jobs": len(rows),
                                              "complete": not failed}, indent=2) + "\n", encoding="utf-8")
        if failed and not args.allow_partial:
            parser.exit(1, "Collection incomplete; refusing to replace snapshot (see --report).\n")
        if args.preserve_uncollected_sources and args.output.exists():
            with args.output.open(encoding="utf-8", newline="") as handle:
                preserved = [row for row in csv.DictReader(handle) if row["source"] not in collected_sources]
            rows.extend(filter_recent_rows(preserved, args.max_posted_age_days))
        count = write_snapshot(rows, args.output, allow_empty=args.allow_empty)
        print(f"Wrote and validated {count} jobs to {args.output}")
        return
    greenhouse_config = load_greenhouse_config(args.greenhouse_config) if args.greenhouse_config else None
    greenhouse_errors: list[str] = []
    rows = collect_enabled_providers(max_pages=args.max_pages, page_size=args.page_size,
                                     adzuna_country=args.adzuna_country, timeout=args.timeout,
                                     greenhouse_config=greenhouse_config,
                                     greenhouse_errors=greenhouse_errors,
                                     include_arbeitnow=not args.greenhouse_only,
                                     include_adzuna=not args.greenhouse_only)
    rows = filter_recent_rows(rows, args.max_posted_age_days)
    for error in greenhouse_errors:
        print(f"WARNING: {error}", flush=True)
    count = write_snapshot(rows, args.output, allow_empty=args.allow_empty)
    print(f"Wrote and validated {count} jobs to {args.output}")


if __name__ == "__main__":
    main()
