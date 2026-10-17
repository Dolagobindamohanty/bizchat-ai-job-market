# Advanced backend slices

These endpoints are intentionally safe CSV-mode slices. They use the normalized skill aliases and job descriptions already loaded by the API; they make no network calls, do not scrape, execute SQL, or require secrets.

## Resume matching

`POST /api/match-resume` accepts `resume_text`, `target_role`, and `years_experience`. It extracts known skills, scopes local demand to the role and experience level when possible, and returns matched skills, high-demand gaps, a 0–100 deterministic score, and explanations. The score is a transparent skill-frequency comparison, not an ML prediction.

## Trends forecast

`GET /api/trends/forecast` returns the two latest seven-day windows, skill demand week-over-week, and a next-period baseline (`current + (current - previous)`, floored at zero). The response labels this `deterministic baseline (not ML)`.

## Semantic search

`GET /api/search/semantic?q=...` and `POST /api/search/semantic` use deterministic token overlap plus normalized skill matches across job titles and descriptions. Skill matches receive the highest weight, followed by title and description tokens. The response explicitly documents the optional future pgvector boundary, but pgvector, embeddings, LLMs, and network calls are not enabled in CSV mode.

## Alert previews

`POST /api/alerts/preview` accepts `{ "filters": { ... } }` using the existing allowlisted job filters and returns the matching count and a small sample. This endpoint previews only. Actual delivery requires an authorized email or Telegram provider; no provider credentials or delivery integration are included in CSV mode.
