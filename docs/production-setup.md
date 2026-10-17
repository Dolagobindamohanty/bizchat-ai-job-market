# BizChat Production Setup

This guide covers source links, PostgreSQL history, scheduled collection hooks, profile-aware recommendations, and Supabase authentication.

## Render environment

Set these on the Render Web Service:

```text
DATABASE_URL=postgresql://...
SUPABASE_JWT_SECRET=...
SUPABASE_JWT_AUDIENCE=authenticated
```

`DATABASE_URL` enables PostgreSQL persistence. Without it, the API uses an in-memory repository for local development and tests.

`SUPABASE_JWT_SECRET` protects saved profiles, searches, and learning plans with bearer-token verification. Without it, local anonymous mode is enabled; do not use that mode for production user data.

Run `database/schema.sql` against PostgreSQL before first use. The repository also creates its small persistence tables defensively at startup.

## Supabase Auth

Create a Supabase project, enable Email OTP or Email/Password, and allow this redirect URL:

```text
https://bizchat-ai-job-market.vercel.app/
```

Configure the static frontend before `app.js` loads:

```html
<script>
  window.BIZCHAT_SUPABASE_URL = 'https://YOUR_PROJECT.supabase.co';
  window.BIZCHAT_SUPABASE_ANON_KEY = 'YOUR_PUBLIC_ANON_KEY';
</script>
```

`frontend/index.html` loads the Supabase browser client from its public CDN. Never put a Supabase service-role key in the frontend. The avatar becomes a magic-link sign-in/sign-out control, and authenticated users can save searches and learning plans.

## Job source links

Every job API item includes `source_url`. The frontend renders it as a secure external link. Real ingestion must provide the original listing URL and use only permitted sources.

## PostgreSQL job history

`job_runs` records collection status, date, source, counts, and errors. `saved_user_data` stores profile, search, and learning-plan JSON by user. Extend the schema with normalized `jobs`, `skills`, and `job_skills` ingestion writers when moving beyond the current CSV serving path.

## Scheduled collection

`collector/scheduled.py` provides the scheduler boundary. It accepts an already-authorized collector and records a run without invoking collectors during API requests.

Use a Render Cron Job, GitHub Actions, or another scheduler to call a permitted adapter. The current daily workflow validates the CSV contract only; it intentionally does not scrape restricted portals or require secrets.

Required adapter flow:

```text
authorized API/export → normalize → validate → deduplicate → persist → record job run
```

## Recommendation profile

The recommendation endpoint accepts `current_skills`, `target_role`, `years_experience`, and `desired_salary_lpa`. It combines demand and salary fit and returns a score and explanation. The Learn next view exposes these inputs and displays skill gaps.

## Deployment checks

```powershell
Invoke-RestMethod "https://bizchat-ai-job-market.onrender.com/api/health"
Invoke-RestMethod "https://bizchat-ai-job-market.onrender.com/api/validation"
```

The Vercel site remains browseable without Supabase; saving is disabled with an explicit message instead of failing silently.
