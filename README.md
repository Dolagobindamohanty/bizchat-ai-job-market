# BizChat 2.0 · AI Job Market Intelligence

BizChat 2.0 is a production-style web application for understanding India's AI/ML/GenAI hiring market. It combines a polished dependency-free static frontend with a FastAPI analytics backend, while preserving the transparent CSV dataset, normalized skill extraction, and explainable learning recommendations from the MVP.

**Problem:** AI job seekers need a practical way to compare demand, cities, salary signals, skills, and next learning steps without manually reading hundreds of postings.

**Live demo:** [bizchat-ai-job-market.vercel.app](https://bizchat-ai-job-market.vercel.app/) · **API:** [Render health check](https://bizchat-ai-job-market.onrender.com/api/health)

## Features

- **JobMatch Bengaluru:** Bengaluru-only tech discovery, PDF/DOCX or pasted-resume parsing, editable profiles, ranked matches, role fit, and skill gaps.
- **Local semantic resume matching** works without an API key or model download; optional consent-gated hosted embeddings and grounded LLM skill suggestions remain available when configured server-side. See [setup, privacy, and limitations](docs/jobmatch-bengaluru.md).
- Multi-source company-board collection from **Greenhouse, Lever, and Ashby**, with source/company/role/skill filtering. Live snapshot: `data/raw/jobs-india.csv`; coverage and collection receipts: `data/source-collection-report.json`.
- KPI cards for tracked jobs, median salary, top skill, and leading city
- Skill demand rankings and weekly comparisons
- City-wise hiring and city/skill heatmap
- Salary midpoint analysis by city and skill
- Explainable recommendations based on skills the user already has
- Salary-aware recommendations that can account for target role, experience, and desired salary
- Responsive product-style interface with Overview, Skills, Locations, Salaries, and Learn next views
- FastAPI endpoints for dashboard KPIs, jobs, skills, salaries, locations, and recommendations
- Local fallback data so the frontend remains viewable while the API is offline
- Loading skeletons, retryable errors, visible live/sample data status, dark mode, and mobile layout
- Searchable/sortable job listings with original source links
- Filters for role type, experience, salary, remote mode, city, source, and skills
- Core takeaways for cities, skills, roles over time, and salary by role
- Supabase magic-link authentication with saved profiles, searches, and learning plans

## Run the BizChat 2.0 web app locally

### 1. Start the API

Run these commands from the repository root (`D:\omni`), not from `D:\omni\web`:

```bash
cd D:\omni
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements-api.txt
uvicorn api.main:app --reload --port 8000
```

### 2. Start the frontend

Open a second terminal and run from the repository root:

```bash
python -m http.server 3000 --directory frontend
```

Open `http://localhost:3000`. The static app points to the deployed Render API by default. For local development, set `window.BIZCHAT_API_URL` to `http://localhost:8000/api` before loading `frontend/app.js`.

The frontend reads `data/raw/jobs.csv` through the API. If the API is unavailable, it renders the bundled sample fallback so the interface can still be explored.

Run the automated checks from the project root:

```bash
pytest -q
```

## API endpoints

- `GET /api/health`
- `GET /api/overview`
- `GET /api/skills`
- `GET /api/locations`
- `GET /api/salaries`
- `GET /api/jobs`
- `GET /api/recommendations`
- `POST /api/match-resume`
- `GET /api/trends/forecast`
- `GET|POST /api/search/semantic`
- `POST /api/alerts/preview`

All analytical endpoints accept optional `city`, `source`, `start_date`, and `end_date` filters. The jobs endpoint also supports `page` and `page_size`.

Analytics endpoints include:

```text
GET /api/analytics/top-cities
GET /api/analytics/top-skills
GET /api/analytics/roles-over-time
GET /api/analytics/salary-distribution-by-role
GET /api/data-quality
POST /api/chat/query
```

Public job and analytics results are current-only by default: listings without a
posted date or older than `BIZCHAT_FRESHNESS_DAYS` (30 by default) are omitted.
Pass `include_stale=true` on supported query endpoints, or the corresponding
request field, to inspect older and unknown-date records. Listing responses
include `freshness_status`, `age_days`, and collection verification timestamps.

The chat endpoint is intentionally read-only and allowlisted: it maps supported natural-language questions to known analytics intents and never executes arbitrary SQL.

The recommendations endpoint additionally accepts repeated `current_skills` values plus `target_role`, `years_experience`, and `desired_salary_lpa`. Example:

```text
/api/recommendations?current_skills=Python&current_skills=SQL&target_role=GenAI%20Engineer&years_experience=2&desired_salary_lpa=20
```

The advanced backend slices are documented in [`docs/advanced-backend-features.md`](docs/advanced-backend-features.md). They are deterministic and local in CSV mode. Semantic search leaves a documented pgvector integration boundary but does not use embeddings or network/LLM services. Alert previews do not send messages; delivery requires an authorized email or Telegram provider.

## Data and production notes

The included data is a small illustrative sample, not a live feed. Production collection should use official APIs, licensed datasets, or sources that permit automated collection. Add source URLs, collection timestamps, salary parsing, deduplication, and retry/rate-limit handling before using real data.

The dashboard is intentionally separate from collection jobs. Run collectors on a schedule, write cleaned records to PostgreSQL, and let the API read the processed tables. Do not run scrapers during web requests.

The repository includes a safe daily validation workflow at `.github/workflows/daily-refresh.yml`. It validates the data contract but does not scrape portals or make network requests. Real ingestion should be connected only to official APIs, licensed datasets, or lawful user-provided exports.

See [`docs/backend-validation-implementation.md`](docs/backend-validation-implementation.md) for the backend validation contract, deployment readiness checks, implementation map, and acceptance criteria.

See [`docs/production-setup.md`](docs/production-setup.md) for PostgreSQL, Supabase Auth, source links, scheduled collection, recommendation profiles, and Render environment setup.

## Suggested PostgreSQL evolution

`database/schema.sql` contains a normalized starting schema for `jobs`, `skills`, and `job_skills`. The current MVP uses CSV so it can run without external services.

## Deploy

Deploy the static `frontend/` directory to Vercel and the API to Render. No Node.js, npm, or frontend build is required for this version. In Vercel, import the repository, choose **Other** as the framework, leave the build command empty, and set the output directory to `frontend`. The frontend is configured to use `https://bizchat-ai-job-market.onrender.com/api` by default.

For Render, create a Web Service from the same repository. The included `render.yaml` uses `requirements-api.txt` and starts FastAPI with Uvicorn. For daily ingestion, use a separate scheduled worker or an authorized workflow rather than the web process.

The previous Streamlit MVP remains in `dashboard/app.py` for reference, but BizChat 2.0 uses `frontend/` and `api/`.

## Tech stack

- **Frontend:** dependency-free HTML, CSS, and JavaScript hosted on Vercel
- **Backend:** FastAPI, Pandas, and Python hosted on Render
- **Storage:** CSV sample mode with PostgreSQL/Supabase schema and RLS for production persistence
- **NLP:** normalized skill aliases, phrase matching, and deterministic local hash-vector matching
- **Auth:** Supabase Auth with PKCE and email magic links
- **Quality:** pytest, GitHub Actions validation, deployment readiness endpoint

## Screenshot

The live dashboard is the canonical current screenshot and is available at the live demo link above. It includes the Overview, Skills, Locations, Salaries, and Learn next views.

## Roadmap

The next high-value production step is an authorized data pipeline: official APIs or licensed exports → normalization → deduplication → PostgreSQL → scheduled refresh. After that, the allowlisted chat layer can be connected to read-only, schema-aware SQL with query validation. Restricted scraping should not be added without permission from each source.
