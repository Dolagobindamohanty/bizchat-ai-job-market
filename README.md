# BizChat 2.0 · AI Job Market Intelligence

BizChat 2.0 is a production-style web application for understanding India's AI/ML/GenAI hiring market. It combines a polished dependency-free static frontend with a FastAPI analytics backend, while preserving the transparent CSV dataset, normalized skill extraction, and explainable learning recommendations from the MVP.

## Features

- KPI cards for tracked jobs, median salary, top skill, and leading city
- Skill demand rankings and weekly comparisons
- City-wise hiring and city/skill heatmap
- Salary midpoint analysis by city and skill
- Explainable recommendations based on skills the user already has
- Salary-aware recommendations that can account for target role, experience, and desired salary
- Responsive product-style interface with Overview, Skills, Locations, Salaries, and Learn next views
- FastAPI endpoints for dashboard KPIs, jobs, skills, salaries, locations, and recommendations
- Local fallback data so the frontend remains viewable while the API is offline

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

All analytical endpoints accept optional `city`, `source`, `start_date`, and `end_date` filters. The jobs endpoint also supports `page` and `page_size`.

The recommendations endpoint additionally accepts repeated `current_skills` values plus `target_role`, `years_experience`, and `desired_salary_lpa`. Example:

```text
/api/recommendations?current_skills=Python&current_skills=SQL&target_role=GenAI%20Engineer&years_experience=2&desired_salary_lpa=20
```

## Data and production notes

The included data is a small illustrative sample, not a live feed. Production collection should use official APIs, licensed datasets, or sources that permit automated collection. Add source URLs, collection timestamps, salary parsing, deduplication, and retry/rate-limit handling before using real data.

The dashboard is intentionally separate from collection jobs. Run collectors on a schedule, write cleaned records to PostgreSQL, and let the API read the processed tables. Do not run scrapers during web requests.

The repository includes a safe daily validation workflow at `.github/workflows/daily-refresh.yml`. It validates the data contract but does not scrape portals or make network requests. Real ingestion should be connected only to official APIs, licensed datasets, or lawful user-provided exports.

See [`docs/backend-validation-implementation.md`](docs/backend-validation-implementation.md) for the backend validation contract, deployment readiness checks, implementation map, and acceptance criteria.

## Suggested PostgreSQL evolution

`database/schema.sql` contains a normalized starting schema for `jobs`, `skills`, and `job_skills`. The current MVP uses CSV so it can run without external services.

## Deploy

Deploy the static `frontend/` directory to Vercel and the API to Render. No Node.js, npm, or frontend build is required for this version. In Vercel, import the repository, choose **Other** as the framework, leave the build command empty, and set the output directory to `frontend`. The frontend is configured to use `https://bizchat-ai-job-market.onrender.com/api` by default.

For Render, create a Web Service from the same repository. The included `render.yaml` uses `requirements-api.txt` and starts FastAPI with Uvicorn. For daily ingestion, use a separate scheduled worker or an authorized workflow rather than the web process.

The previous Streamlit MVP remains in `dashboard/app.py` for reference, but BizChat 2.0 uses `frontend/` and `api/`.
