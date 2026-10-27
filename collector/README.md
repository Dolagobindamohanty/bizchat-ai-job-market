# Safe daily collector boundary

The built-in collector adapters use documented JSON APIs only. They do **not**
scrape websites, log in to portals, or bypass CAPTCHAs or robots controls. The
separate Apify adapter delegates collection to an explicitly selected actor and
is opt-in, manual-only, and subject to the actor and portal terms. Run
`python -m collector.cli --output data/raw/jobs-live.csv` to fetch Arbeitnow and
the optional Adzuna provider and write a validated snapshot. Adzuna is enabled
only when `ADZUNA_APP_ID` and `ADZUNA_APP_KEY` are present in the environment.

## Optional Apify actor adapters

Apify actors are separate, opt-in third-party adapters. They require an
`APIFY_API_TOKEN` and an explicit config; the application API never runs them.
The approved configurations include ValiG's Naukri, LinkedIn, and Indeed
actors, plus Curious Coder's LinkedIn actor as an explicitly separate
`linkedin-rich` source. Do not configure both LinkedIn actors for the same
search unless the duplicate cost is intentional.

Copy and review `collector/apify.india.example.json`, then run:

```sh
set APIFY_API_TOKEN=your-token
python -m collector.cli --output data/raw/jobs-live.csv \
  --apify-config collector/apify.india.example.json \
  --report data/apify-collection-report.json \
  --max-posted-age-days 30 --allow-partial
```

The repository also includes a **manual-only** GitHub Actions workflow at
`.github/workflows/apify-refresh.yml`. Add the `APIFY_API_TOKEN` repository
secret and start it from the Actions tab when you have confirmed the actors'
terms and want to collect a paid run. It is not scheduled by default.

Each actor runs independently. A failed actor is reported without discarding
successful actors; publication requires `--allow-partial` when any configured
actor fails. Records are labelled `source_type=third_party_collector`, retain
source-specific IDs and HTTPS URLs, and use `listing_status=unknown` and
`verification_status=observed`. “Recent” means recently collected/posted; it
does not mean the employer page was independently checked or that the role is
still open. Review each actor's current pricing, portal terms, attribution
requirements, and commercial-use permissions before public operation. The
adapter intentionally does not retain recruiter profiles, photos, employee
counts, reviews, or other unnecessary personal/company enrichment.

## Greenhouse Job Board API

Greenhouse is opt-in and requires an explicit JSON board list; this repository
does not claim or seed verified company tokens. Copy
`collector/greenhouse.example.json`, replace the example token with a verified
public board token, and adjust `india_location_keywords` for the locations you
want included. Run:

```sh
python -m collector.cli --output data/raw/jobs-live.csv \
  --greenhouse-config collector/greenhouse.example.json
```

The adapter uses only Greenhouse's official unauthenticated endpoint
`https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true`.
It preserves Greenhouse job IDs and absolute URLs, converts the API's HTML
content to plain text, and prefers `updated_at` over `first_published` for the
snapshot date. Boards are bounded by the explicit config (maximum 100) and a
request timeout. Per-board failures are reported and do not discard successful
boards. Review Greenhouse's current API terms and each board's attribution or
usage requirements before operating a collection; this project does not grant
permission beyond the public endpoint.

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
