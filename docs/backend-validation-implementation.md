# BizChat Backend Validation & Implementation Specification

## 1. Purpose

This document defines the backend validation contract for BizChat 2.0. It is the implementation reference for checking whether the deployed FastAPI service and the job dataset are safe to serve to users.

The validation design is intentionally network-free. It must not scrape job portals, access credentials, bypass authentication, or call external services. It validates the local dataset that the API loads.

## 2. User-visible outcome

An operator can call:

```text
GET /api/validation
```

and receive a machine-readable readiness report. A healthy report has `deployment_safe: true`; a malformed dataset produces a successful response with actionable failure counts and `deployment_safe: false`.

## 3. Validation contract

### Response

```json
{
  "api_status": "ok",
  "dataset_row_count": 20,
  "required_columns_present": true,
  "duplicate_key_count": 0,
  "missing_source_urls": 0,
  "invalid_timestamps": 0,
  "deployment_safe": true
}
```

### Fields

| Field | Meaning |
|---|---|
| `api_status` | The validation endpoint executed successfully. It is not a substitute for an HTTP health check. |
| `dataset_row_count` | Number of CSV data rows inspected. |
| `required_columns_present` | Whether the CSV contains the minimum ingestion contract. |
| `duplicate_key_count` | Repeated `(source, job_id)` keys after trimming whitespace. |
| `missing_source_urls` | Rows without a source URL. |
| `invalid_timestamps` | Rows whose `collected_at` is not timezone-aware ISO-8601. |
| `deployment_safe` | True only when all required checks pass. |

## 4. Dataset contract

The minimum required columns are:

```text
job_id
title
source
source_url
posted_date
collected_at
```

Production records should additionally preserve company, location, salary, description, remote type, and source metadata used by the analytics layer.

Rules:

1. `job_id` and `source` must both be non-empty.
2. `(source, job_id)` must be unique.
3. `source_url` must be an HTTP or HTTPS URL.
4. `collected_at` must be ISO-8601 and include a timezone, preferably UTC.
5. Validation is a dry run; it does not rewrite or upload data.

## 5. Implementation map

| Component | Responsibility |
|---|---|
| `collector/validate.py` | Reusable, network-free CSV validation and CLI validation command. |
| `api/main.py` | `GET /api/validation` adapter and Pydantic response model. |
| `data/raw/jobs.csv` | Current illustrative dataset. It is not a live production feed. |
| `database/schema.sql` | PostgreSQL evolution path with source metadata and deduplication index. |
| `.github/workflows/daily-refresh.yml` | Scheduled validation only; no scraping and no secrets. |
| `tests/test_api.py` | Endpoint behavior for healthy and invalid datasets. |

## 6. API readiness checks

Use the network from a terminal that can access the deployed service:

### Local

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/health"
Invoke-RestMethod "http://127.0.0.1:8000/api/validation"
```

### Render

```powershell
Invoke-RestMethod "https://bizchat-ai-job-market.onrender.com/api/health"
Invoke-RestMethod "https://bizchat-ai-job-market.onrender.com/api/validation"
```

Expected healthy values for the current sample dataset:

```text
api_status = ok
dataset_row_count = 20
required_columns_present = True
duplicate_key_count = 0
missing_source_urls = 0
invalid_timestamps = 0
deployment_safe = True
```

## 7. Verification plan

Run from `D:\omni`:

```powershell
python -m pytest -q
python -m collector.validate --input data/raw/jobs.csv
python -m compileall -q api bizchat collector tests
git diff --check
```

Acceptance criteria:

- Existing analytics, jobs, and recommendation endpoints remain compatible.
- `/api/validation` returns HTTP 200 for both valid and invalid local CSVs.
- Invalid data is reported with counts instead of causing an unhandled exception.
- The sample dataset returns `deployment_safe: true`.
- The workflow remains network-free and secret-free.
- Deployment can be checked with PowerShell without treating a URL as a command.

## 8. Operational boundaries

This validation layer does not prove that a job listing is legally collectible, fresh, or semantically correct. It proves only that the local ingestion contract is structurally safe for the current API.

Real daily ingestion must use official APIs, licensed datasets, or lawful user-provided exports. Any adapter must write the documented CSV contract and run the validator before publishing data to PostgreSQL or the API.

## 9. Implementation status

Implemented in the current repository:

- `GET /api/validation`
- Shared `validation_report()` logic
- Healthy and invalid-data tests
- Scheduled dry-run validation workflow
- Source URL and timestamp fields in the sample dataset/schema

The next production step is connecting an authorized ingestion adapter to a staging table, validating it, deduplicating it, and promoting only safe records to the serving database.
