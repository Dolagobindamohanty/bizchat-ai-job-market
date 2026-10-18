"""Command line entry point for an API-backed CSV snapshot."""

import argparse
import logging
from pathlib import Path

from collector.providers import collect_enabled_providers, load_greenhouse_config
from collector.snapshot import write_snapshot


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
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
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
