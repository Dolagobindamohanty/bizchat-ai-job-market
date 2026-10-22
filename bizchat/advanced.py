"""Deterministic, network-free helpers for the advanced BizChat API slices."""

from __future__ import annotations

import re
from collections import Counter
from datetime import timedelta
from typing import Any

import pandas as pd

from bizchat.analytics import SKILL_ALIASES, extract_skills


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9+#.-]+", str(value or "").casefold()) if len(token) > 1}


def resume_match(resume_text: str, target_role: str, years_experience: float, jobs: pd.DataFrame) -> dict[str, Any]:
    jobs = jobs.copy()
    resume_skills = extract_skills(resume_text)
    role_terms = _tokens(target_role)
    role_jobs = jobs
    if role_terms:
        matches = jobs["title"].fillna("").map(lambda title: role_terms.issubset(_tokens(title)))
        if matches.any():
            role_jobs = jobs[matches]
    eligible = role_jobs[role_jobs["experience_min"].isna() | (role_jobs["experience_min"] <= years_experience)]
    if eligible.empty:
        eligible = role_jobs
    demand = Counter(skill for skills in eligible["skills"] for skill in skills)
    normalized_resume = {skill.casefold() for skill in resume_skills}
    matched = [skill for skill, _ in sorted(demand.items(), key=lambda pair: (-pair[1], pair[0])) if skill.casefold() in normalized_resume]
    missing = [skill for skill, _ in sorted(demand.items(), key=lambda pair: (-pair[1], pair[0])) if skill.casefold() not in normalized_resume]
    total = sum(demand.values())
    score = round(100 * sum(demand[skill] for skill in matched) / total, 2) if total else 0.0
    explanations = [f"Matched {skill}: found in the resume and requested by {demand[skill]} target-role job(s)." for skill in matched]
    explanations.extend(f"Missing {skill}: requested by {demand[skill]} target-role job(s), so it is a high-demand gap." for skill in missing[:8])
    if not demand:
        explanations.append("No matching local job demand was available for this target role and experience level.")
    return {"matched_skills": matched, "missing_high_demand_skills": missing[:8], "score": score, "explanations": explanations,
            "baseline": "Deterministic skill-frequency comparison over the local CSV dataset; not ML."}


def trend_forecast(jobs: pd.DataFrame) -> dict[str, Any]:
    jobs = jobs.copy()
    jobs = jobs.dropna(subset=["posted_date"])
    if jobs.empty:
        return {"forecast_method": "deterministic baseline (not ML)", "periods": [], "week_over_week": [], "forecast": []}
    latest = jobs["posted_date"].max().date()
    current_start = latest - timedelta(days=6)
    previous_start = latest - timedelta(days=13)
    previous_end = latest - timedelta(days=7)

    def counts(start, end):
        scoped = jobs[(jobs["posted_date"].dt.date >= start) & (jobs["posted_date"].dt.date <= end)]
        return Counter(skill for skills in scoped["skills"] for skill in skills)

    current, previous = counts(current_start, latest), counts(previous_start, previous_end)
    skills = sorted(set(current) | set(previous))
    wow = []
    for skill in skills:
        now, before = current[skill], previous[skill]
        wow.append({"skill": skill, "current_jobs": now, "previous_jobs": before, "change": now - before,
                    "change_pct": round((now - before) * 100 / before, 2) if before else None})
    wow.sort(key=lambda item: (-item["current_jobs"], item["skill"]))
    return {
        "forecast_method": "deterministic baseline (not ML)",
        "periods": [{"period_start": previous_start, "period_end": previous_end,
                     "skills": [{"skill": skill, "jobs": previous[skill]} for skill in sorted(previous)]},
                    {"period_start": current_start, "period_end": latest,
                     "skills": [{"skill": skill, "jobs": current[skill]} for skill in sorted(current)]}],
        "week_over_week": wow,
        "forecast": [{"skill": skill, "baseline_next_period_jobs": max(0, current[skill] + current[skill] - previous[skill])}
                      for skill in sorted(set(current) | set(previous), key=lambda item: (-current[item], item))],
    }


def semantic_search(query: str, jobs: pd.DataFrame, limit: int = 20) -> list[dict[str, Any]]:
    jobs = jobs.copy()
    query_tokens = _tokens(query)
    query_skills = {skill.casefold() for skill in extract_skills(query)}
    results = []
    for row in jobs.itertuples():
        title_tokens = _tokens(row.title)
        description_tokens = _tokens(row.description)
        skill_matches = sorted(query_skills & {skill.casefold() for skill in row.skills})
        token_matches = query_tokens & (title_tokens | description_tokens)
        score = round(10 * len(skill_matches) + 2 * len(query_tokens & title_tokens) + len(query_tokens & description_tokens), 2)
        if score:
            result = {"job_id": row.job_id, "title": row.title, "company": row.company, "location": row.location,
                      "source_url": row.source_url, "score": score, "matched_skills": skill_matches,
                      "matched_tokens": sorted(token_matches),
                      "explanation": "Skill matches are weighted above title and description token overlap."}
            for field in ("freshness_status", "age_days", "last_verified", "collected_at"):
                if hasattr(row, field):
                    result[field] = getattr(row, field)
            results.append(result)
    return sorted(results, key=lambda item: (-item["score"], item["title"], item["job_id"]))[:limit]


def alert_preview(filters: dict[str, Any], jobs: pd.DataFrame) -> dict[str, Any]:
    jobs = jobs.copy()
    allowed = {"city", "source", "start_date", "end_date", "role_type", "experience_level", "min_salary_lpa", "max_salary_lpa", "remote_type", "skill"}
    applied = {key: value for key, value in filters.items() if key in allowed and value not in (None, "", [])}
    scoped = jobs
    for key, value in applied.items():
        column = "location" if key == "city" else key
        if key == "skill":
            values = value if isinstance(value, list) else [value]
            scoped = scoped[scoped["skills"].map(lambda skills: any(str(v).casefold() in {s.casefold() for s in skills} for v in values))]
        elif key in {"min_salary_lpa", "max_salary_lpa"}:
            scoped = scoped[scoped["salary_max_lpa"] >= float(value)] if key == "min_salary_lpa" else scoped[scoped["salary_min_lpa"] <= float(value)]
        elif key in {"start_date", "end_date"}:
            boundary_value = pd.to_datetime(value, errors="coerce")
            if pd.isna(boundary_value):
                continue
            boundary = boundary_value.date()
            scoped = scoped[scoped["posted_date"].dt.date >= boundary] if key == "start_date" else scoped[scoped["posted_date"].dt.date <= boundary]
        else:
            scoped = scoped[scoped[column].astype(str).str.casefold() == str(value).casefold()]
    sample = []
    for row in scoped.sort_values(["posted_date", "job_id"], ascending=[False, True], na_position="last").head(5).itertuples():
        item = {"job_id": row.job_id, "title": row.title, "company": row.company, "location": row.location}
        for field in ("freshness_status", "age_days", "last_verified", "collected_at"):
            if hasattr(row, field):
                item[field] = getattr(row, field)
        sample.append(item)
    return {"filters": applied, "matched_jobs": len(scoped), "sample_jobs": sample,
            "delivery": {"status": "preview_only", "message": "Delivery requires an authorized email or Telegram provider."}}
