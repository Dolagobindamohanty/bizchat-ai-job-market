# JobMatch Bengaluru

The existing BizChat application now includes a Bengaluru tech-job feed and a
session-only resume workspace. The original five analytics views remain available.
No frontend framework migration, vector database, or new repository is required.

## Try it locally

Install the API dependencies and run the backend from the repository root:

```bash
python -m pip install -r requirements-api.txt
python -m uvicorn api.main:app --port 8000
```

Set `BIZCHAT_DATA_PATH=data/raw/jobs-india.csv` in the backend process
environment to use the committed real snapshot. Otherwise the existing default
is the illustrative `data/raw/jobs.csv` sample. The matcher filters whichever
dataset is loaded to explicit Bengaluru/Bangalore/BLR locations and tech titles.
India-wide remote roles without an explicit Bengaluru location are excluded.

For a local frontend, serve the existing `frontend` directory:

```bash
python -m http.server 8080 --directory frontend
```

Open `http://localhost:8080`. The normal frontend configuration points to production.
Temporarily set `apiUrl` in `frontend/config.js` to `http://localhost:8000/api` for
local testing; restore it before committing. Do not put provider keys in this file.

Workflow:
1. Browse **Bengaluru Jobs**, including its AI-only collection.
2. Open **Upload Resume** and paste text or select a PDF/DOCX (maximum 5 MiB).
3. Parse locally and correct extracted skills, titles, and years of experience.
4. Open **My Matches**. Baseline matching needs no AI credentials.
5. Enable local semantic matching for a lightweight no-key ranking pass, or enable hosted embeddings and consent for that individual request if desired.
6. Review **Role Fit**, **Skill Gaps**, and optional **Resume Suggestions**.
7. **Clear / delete session** discards browser-held resume inputs and results.

## Server-side AI configuration

Configure these on the backend host only. Both adapters use the OpenAI-compatible
HTTP protocol; this does not imply that another vendor's model is an OpenAI model.
Select actual models supported by your chosen provider. Hosted providers are optional; the default local semantic matcher does not require provider configuration.

### Optional hosted setup: OpenAI

The Render configuration selects `JOBMATCH_PROVIDER=openai`,
`text-embedding-3-small` for embeddings, and `gpt-4.1-mini` for suggestions.
Set **`OPENAI_API_KEY` privately in the backend host environment**. Do not add it
to the frontend, source files, reports, or chat. This key has not been supplied or
tested live. API billing is separate from a ChatGPT subscription.

With this explicit OpenAI opt-in, the code supplies `https://api.openai.com/v1`
and the model defaults above. Per-kind variables below can override them. A shared
OpenAI key is never implicitly forwarded to a custom endpoint; such an endpoint
requires its own explicit per-kind key. `.env.example` is a non-secret reference
only; the app does not automatically load `.env` files.

| Variable | Meaning |
| --- | --- |
| `JOBMATCH_EMBEDDING_BASE_URL` | Provider API base, including `/v1` when required |
| `JOBMATCH_EMBEDDING_API_KEY` | Server-only embedding credential |
| `JOBMATCH_EMBEDDING_MODEL` | Exact embedding model ID |
| `JOBMATCH_LLM_BASE_URL` | Chat API base, including `/v1` when required |
| `JOBMATCH_LLM_API_KEY` | Server-only chat credential |
| `JOBMATCH_LLM_MODEL` | Exact chat model ID supporting JSON output |

Requests go to `/embeddings` and `/chat/completions` relative to those bases.
HTTPS is required except for a loopback development server. No provider is selected
or called automatically. Missing configuration returns **503**, missing consent
returns **400**, and invalid/provider failures return a safe **502** without secrets.
There is no fake embedding or canned fallback labelled as successful LLM output.

The LLM receives allowlisted evidence and counted demand in the top 20 baseline
matches. It prioritizes `document` versus `learn` actions from those exact skills.
The server validates every selected skill/action, supplies conditional advice, and
adds placeholder-only bullet templates for documenting supported skills. This is
intentionally constrained guidance, **not unrestricted generated resume rewriting**.
It cannot certify that a candidate really possesses a skill.

## Architecture and endpoints

- `bizchat/skills.py`: boundary-aware canonical skill vocabulary.
- `bizchat/job_classifier.py`: explicit-city and conservative title classification.
- `bizchat/resume_parser.py`: local parsing and bounded PDF/DOCX extraction.
- `bizchat/matcher.py`: explainable baseline/embedding-assisted scores and gaps.
- `bizchat/ai_providers.py`: deterministic local matcher plus consent-gated hosted adapters and public-vector cache.
- `api/models.py`, `api/jobmatch.py`: bounded stateless request contracts/router.
- `api/body_limits.py`: streamed request limits, including missing Content-Length.
- `frontend/jobmatch.js`: six views layered over the original dashboard.

| Method | Endpoint | Result |
| --- | --- | --- |
| GET | `/api/jobs/bengaluru` | Paginated Bengaluru tech jobs |
| GET | `/api/jobs/ai` | AI/data-science subset of Bengaluru jobs |
| POST | `/api/resume/parse` | `{resume_text}` to `{profile}` |
| POST | `/api/resume/upload` | One multipart `file` to `{profile}` |
| POST | `/api/resume/match` | `{profile,use_embeddings,consent_external,limit}` to items, roles, gaps |
| GET | `/api/resume/roles`, `/api/resume/gaps` | Stateless summaries from optional comma-separated skills/titles and years_experience |
| POST | `/api/resume/suggestions` | `{profile,consent_external,target_role}` to grounded actions |

Prefer POST matching over the GET summaries: even skills in URLs can appear in
browser history and access logs. GET routes never retrieve a previous resume.
Job discovery accepts `q`, `company`, `role`, comma-separated `skills`, `page`, and
`page_size`. Both AI and ordinary lists are Bengaluru-only.

Local and hosted semantic scoring use skills 50%, semantic similarity 25%, experience 15%,
and role 10%. Without semantic matching, the remaining weights are normalized by 0.75.
Unknown evidence earns no points and is marked unknown in the breakdown. All skill
mentions are currently weighted equally; required/preferred distinctions are not
yet extracted. Scores are evidence-fit measures, not interview/hiring probabilities.

At most 200 locally shortlisted jobs are scored semantically per request, in batches of 32.
The default local matcher uses deterministic 256-dimensional token-hash vectors and
does not call a provider. When a hosted provider is explicitly configured and consented,
public job vectors use a bounded 512-entry in-process cache keyed by endpoint, model,
and content; resume vectors are never cached. This is not pgvector or a persistent vector
index. Role/gap summaries in semantic mode describe the shortlisted corpus, not necessarily
every available job.

## Privacy and deployment boundaries

The service has no resume table, bucket, or saved-resume endpoint. Resume files and
raw text are processed transiently in memory; the parser returns reconstructed
facts rather than names, contacts, addresses, employers, or institutions. Outbound
AI requests reconstruct allowed skills/role/action tokens again, so a client cannot
make arbitrary `redacted_text` leave the server. Public job descriptions may be
sent to the embedding provider. Provider retention policies still apply to any
consented request; clearing this tab cannot revoke an already-sent provider call.

The browser keeps the original pasted text/file only in tab memory. It does not
use localStorage/sessionStorage for resumes. Clear invalidates pending responses
so late results cannot restore deleted data; it does not claim to cancel a remote
request already in progress. Existing account/workspace features are separate and
do not automatically save this resume profile.

Uploads reject unsupported, encrypted, empty/scanned, malformed, and oversized
files. DOCX expansion and PDF page/text limits are enforced, but parsing is not
an OS-level sandbox or malware scanner. Production deployment should isolate file
parsing, enforce distributed rate/cost limits, and ensure proxies/monitoring do not
record request bodies. The app's current rate limiter is per-process, not a budget
control across replicas. Live provider latency, billing, and retention need review
before enabling the public external-AI endpoints.

The daily workflow now collects all boards in `collector/sources.india.json` into
`data/raw/jobs-india.csv`, while keeping the old snapshot intact. Its schedule is
unchanged. `collector/greenhouse.bengaluru.json` remains an optional narrower collection
configuration using the same six tracked boards, not a claim of complete coverage:

```bash
python -m collector.cli --output data/raw/jobs-bengaluru.csv --greenhouse-config collector/greenhouse.bengaluru.json --greenhouse-only --timeout 20
```

Set `BIZCHAT_DATA_PATH` to that output only if deliberately switching all analytics
to the narrower dataset. Do not deploy the unrelated Arbeitnow experiment.

## Verification

```bash
python -m pytest -q
node --check frontend/jobmatch.js
git diff --check
```

Provider tests use mocked HTTP transports, not paid live calls. Frontend regression
tests execute both scripts in a controlled Node DOM/API harness, not a real browser
layout engine. Synthetic ranking examples protect basic role/skill relevance; they
are not evidence of real-world recruiting accuracy or embedding quality. Hosted
quality, visual accessibility, provider latency, and real-resume evaluation still
need live testing before calling the product production-ready.

Earlier local core-only smoke measurement on this development machine: ranking 10,000
synthetic short-description jobs took 2.543 seconds, returning 50 displayed jobs.
This excludes HTTP, parsing, database reads, hosted provider calls and LLM latency;
it is not a production throughput guarantee.

## Multi-source coverage

The new live collection produced **264 India-location listings** from nine boards:
163 Greenhouse, 56 Lever, and 45 Ashby. The current classifier returns **89 Bengaluru
tech jobs** (60 Greenhouse, 12 Lever, 17 Ashby), including 16 AI-tagged jobs. These
are snapshot-specific counts, not full-market or future freshness guarantees.

New verified boards are Meesho (Lever), Sarvam AI (Ashby), and Composio (Ashby).
The six Greenhouse boards remain. Other probed boards were not added merely because
their names were plausible. No LinkedIn, Indeed, or Naukri scraping is implemented.
Adzuna remains an optional legacy adapter, not an active source in this pipeline.

Collect from the repository root:

```bash
python -m collector.cli --output data/raw/jobs-india.csv --sources-config collector/sources.india.json --report data/source-collection-report.json --timeout 20
```

The report lists successful and failed boards without raw API payloads. A failed
board blocks publication by default and preserves the previous snapshot. The
explicit `--allow-partial` escape hatch is not used by scheduled refreshes. Empty
snapshots are also rejected. Board-scoped source IDs prevent unrelated companies'
numeric IDs from colliding. Locations use word boundaries and include secondary
locations; India does not match Indiana. Dates not supplied by a source remain
unknown rather than being replaced with collection/update dates.

The jobs page supports source, company, role, comma-separated skills, and free-text
filters. Resume matching includes all sources by default or an explicit source
selection. Job cards retain the original HTTPS listing link and source label.
Sources mode uses documented APIs, not unrestricted web scraping; source and
employer terms must still be respected before public redistribution.

Official references:
- Lever: https://github.com/lever/postings-api
- Ashby: https://developers.ashbyhq.com/docs/public-job-posting-api
- Greenhouse: https://developers.greenhouse.io/job-board.html

## Hosted-call allowance

`JOBMATCH_AI_DAILY_CALL_LIMIT` defaults to 100 external HTTP calls per UTC day,
shared across users/IPs and both provider kinds. Set it to 0 to disable hosted calls.
Calls are reserved atomically before sending; failed calls consume allowance too.
Missing consent/configuration and locally rejected input do not consume allowance.
One uncached 200-job embedding match can use up to 8 calls; suggestions use one.

The SQLite usage ledger contains only UTC day, a fixed category, and call count.
It contains no resumes, prompts, names, API keys, job payloads, or responses.
`JOBMATCH_USAGE_DB` can select a path whose parent directory already exists.
Workers must use the same ledger for a shared cap. The default is outside the
repository in a private temporary runtime directory. Temporary disks, redeploys,
separate replicas, or deleting the ledger can reset/split the allowance: this is
**not a durable dollar-spend guarantee** on Render's ephemeral free filesystem.
Use durable shared storage and provider-side spending controls before a public
launch. Exhaustion returns 429 and ledger failures fail closed with 503.

## City filter correction

The dashboard uses canonical `cities` tags rather than raw location labels for
its dropdown and city membership filters. Bangalore, Bengaluru, BLR, campus names
such as Bengaluru-VTP, and country-prefixed/suffixed labels resolve to one
**Bengaluru** option. A Bengaluru/Mumbai posting can match either city, without
rewriting its original displayed location. City totals can therefore sum to more
than the number of jobs. Country-only/unknown locations do not invent a city.

The legacy Greenhouse adapter and the multi-source collector both match location
keywords at word boundaries: **India does not match Indiana**. `/api/jobs` retains
the original `location` and supplies `cities` separately. API city query aliases
and saved frontend searches use canonical membership too. Salary-by-city views
use the same tags and exclude missing salaries rather than treating them as zero.

The original dashboard's title-derived seniority is labelled separately from
minimum experience. Years of experience and work mode remain unknown when absent
from the data. Filters are combined with AND, so a restrictive combination can
legitimately return no jobs; use **Clear filters** to reset it.

For existing Render services, the retired `data/raw/jobs-greenhouse-india.csv`
environment setting aliases the new multi-source snapshot when it exists. This
lets the code deployment migrate the existing service without depending on an
immediate Blueprint environment sync. Custom paths and the local sample default
are unchanged; set `BIZCHAT_DATA_PATH=data/raw/jobs-india.csv` explicitly for clarity.
