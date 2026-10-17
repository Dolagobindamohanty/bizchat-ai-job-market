# Safe daily collector boundary

The collector is intentionally a local validation/normalization boundary. It
does **not** scrape websites, log in to portals, bypass CAPTCHAs or robots
controls, or need API keys. The scheduled workflow currently runs only the
network-free placeholder check in `collector.validate`.

## Permitted source ingestion

Future adapters may ingest data only from:

- an official API or public feed whose terms explicitly permit this use;
- a licensed dataset or provider export; or
- a user-provided CSV export that was obtained lawfully.

Do not add an adapter for a restricted portal, authenticated page, or source
that prohibits automated collection. Keep collection separate from the API and
record provenance for every row. A source-specific adapter should write the
contract below, then run `python -m collector.validate --input <file>`.

## CSV contract

`data/raw/jobs.csv` is the sample contract. Required ingestion fields are:

| Column | Rule |
| --- | --- |
| `job_id` | Source-native stable identifier; required with `source`. |
| `title` | Job title. |
| `source` | Provider or export name. |
| `source_url` | Canonical HTTP(S) URL for the originating record; never omit provenance. |
| `posted_date` | Source posting date in `YYYY-MM-DD` when available. |
| `collected_at` | ISO-8601 timestamp including timezone, preferably UTC (`Z`). |

Other columns are preserved as documented by the database schema. Empty optional
values should remain empty rather than being guessed. The validator trims the
identity fields, checks URLs and timestamps, and performs a dry-run
normalization; it does not write or upload data.

## Deduplication

Deduplicate before loading into the database using `(source, job_id)` as the
primary stable key. If a provider has no stable ID, use the canonical
`source_url`; as a last resort use a documented fingerprint of normalized title,
company, location, and posted date. Keep the newest `collected_at` for a
duplicate and retain the original `source_url`. Do not merge records from
different sources solely because their titles look alike.

The PostgreSQL `jobs` table stores `source_url` and `collected_at` so this
provenance survives ingestion. The checked-in CSV uses sample `example.com`
URLs and is not a live feed.
