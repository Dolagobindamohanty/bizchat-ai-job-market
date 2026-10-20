"""Command line entry point for an API-backed CSV snapshot."""

import argparse
import json
import logging
from pathlib import Path

from collector.providers import collect_enabled_providers, load_greenhouse_config
from collector.snapshot import write_snapshot
from collector.multisource import collect_sources


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch authorized job APIs and write a validated CSV snapshot")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--timeout", type=float, default=15.0, help="HTTP timeout in seconds, default: 15")
    parser.add_argument("--adzuna-country", default="in", help="Adzuna country code, default: in")
    parser.add_argument("--allow-empty", action="store_true", help="Allow publishing an empty snapshot")
    parser.add_argument("--greenhouse-config", type=Path,
                        help="Explicit JSON Greenhouse board config; omitted means Greenhouse is disabled")
    parser.add_argument("--greenhouse-only", action="store_true",
                        help="Use only the configured Greenhouse boards; skip Arbeitnow and Adzuna")
    parser.add_argument("--sources-config", type=Path,
                        help="Explicit Greenhouse/Lever/Ashby boards; skips Arbeitnow and Adzuna")
    parser.add_argument("--allow-partial", action="store_true",
                        help="Allow publishing sources-config results when some boards fail")
    parser.add_argument("--report", type=Path, help="Write a safe JSON per-board collection summary")
    args = parser.parse_args()
    if args.sources_config and (args.greenhouse_config or args.greenhouse_only):
        parser.error("--sources-config is mutually exclusive with --greenhouse-config and --greenhouse-only")
    if (args.allow_partial or args.report) and not args.sources_config:
        parser.error("--allow-partial and --report require --sources-config")
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    if args.sources_config:
        from collector.multisource import load_sources_config

        # A report must never overwrite the snapshot or its input configuration.
        if args.report and args.report.resolve() in {args.output.resolve(), args.sources_config.resolve()}:
            parser.error("--report must differ from --output and --sources-config")
        config = load_sources_config(args.sources_config)
        rows, reports = collect_sources(config, timeout=args.timeout)
        failed = any(report["status"] != "ok" for report in reports)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps({"boards": reports, "jobs": len(rows),
                                              "complete": not failed}, indent=2) + "\n", encoding="utf-8")
        if failed and not args.allow_partial:
            parser.exit(1, "Collection incomplete; refusing to replace snapshot (see --report).\n")
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
    for error in greenhouse_errors:
        print(f"WARNING: {error}", flush=True)
    count = write_snapshot(rows, args.output, allow_empty=args.allow_empty)
    print(f"Wrote and validated {count} jobs to {args.output}")


if __name__ == "__main__":
    main()
