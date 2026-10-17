from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Annotated

import pandas as pd
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from bizchat.analytics import prepare_jobs, recommendations, skill_demand


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "raw" / "jobs.csv"


class Overview(BaseModel):
    jobs_tracked: int
    median_salary_lpa: float | None
    top_skill: str | None
    top_skill_jobs: int
    leading_city: str | None
    leading_city_jobs: int
    cities: int
    companies: int
    sources: int
    posted_from: date | None
    posted_to: date | None


class SkillDemand(BaseModel):
    skill: str
    jobs: int
    share: float


class LocationSummary(BaseModel):
    location: str
    jobs: int
    median_salary_lpa: float | None


class SalarySummary(BaseModel):
    location: str
    median_salary_lpa: float | None


class SkillSalarySummary(BaseModel):
    skill: str
    median_salary_lpa: float | None


class SalariesResponse(BaseModel):
    by_location: list[SalarySummary]
    by_skill: list[SkillSalarySummary]


class JobListing(BaseModel):
    job_id: str
    title: str
    company: str
    location: str
    salary_min_lpa: float | None
    salary_max_lpa: float | None
    salary_mid_lpa: float | None
    experience_min: float | None
    remote_type: str
    source: str
    posted_date: date
    description: str
    skills: list[str]


class JobsResponse(BaseModel):
    items: list[JobListing]
    total: int
    page: int
    page_size: int


class LearningRecommendation(BaseModel):
    skill: str
    demand: int
    share: float
    reason: str
    score: float = 0.0


app = FastAPI(title="BizChat API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@lru_cache(maxsize=1)
def load_jobs() -> pd.DataFrame:
    """Load and normalize the local source once per process."""
    return prepare_jobs(pd.read_csv(DATA_PATH))


def filtered_jobs(
    city: str | None,
    source: str | None,
    start_date: date | None,
    end_date: date | None,
) -> pd.DataFrame:
    jobs = load_jobs()
    if city:
        jobs = jobs[jobs["location"] == city]
    if source:
        jobs = jobs[jobs["source"] == source]
    if start_date:
        jobs = jobs[jobs["posted_date"].dt.date >= start_date]
    if end_date:
        jobs = jobs[jobs["posted_date"].dt.date <= end_date]
    return jobs.copy()


FilterCity = Annotated[str | None, Query(description="Exact city filter")]
FilterSource = Annotated[str | None, Query(description="Exact source filter")]
FilterStart = Annotated[date | None, Query(description="Earliest posted date")]
FilterEnd = Annotated[date | None, Query(description="Latest posted date")]


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/overview", response_model=Overview)
def overview(city: FilterCity = None, source: FilterSource = None, start_date: FilterStart = None, end_date: FilterEnd = None) -> Overview:
    jobs = filtered_jobs(city, source, start_date, end_date)
    demand = skill_demand(jobs)
    city_counts = jobs["location"].value_counts()
    median = jobs["salary_mid_lpa"].median() if not jobs.empty else None
    return Overview(
        jobs_tracked=len(jobs),
        median_salary_lpa=None if pd.isna(median) else float(median),
        top_skill=None if demand.empty else str(demand.iloc[0]["skill"]),
        top_skill_jobs=0 if demand.empty else int(demand.iloc[0]["jobs"]),
        leading_city=None if city_counts.empty else str(city_counts.index[0]),
        leading_city_jobs=0 if city_counts.empty else int(city_counts.iloc[0]),
        cities=int(jobs["location"].nunique()),
        companies=int(jobs["company"].nunique()),
        sources=int(jobs["source"].nunique()),
        posted_from=None if jobs.empty else jobs["posted_date"].min().date(),
        posted_to=None if jobs.empty else jobs["posted_date"].max().date(),
    )


def demand_response(jobs: pd.DataFrame) -> list[SkillDemand]:
    demand = skill_demand(jobs)
    total = max(len(jobs), 1)
    return [SkillDemand(skill=str(row.skill), jobs=int(row.jobs), share=float(row.jobs / total)) for row in demand.itertuples()]


@app.get("/api/skills", response_model=list[SkillDemand])
@app.get("/api/skill-demand", response_model=list[SkillDemand], include_in_schema=False)
def skills(city: FilterCity = None, source: FilterSource = None, start_date: FilterStart = None, end_date: FilterEnd = None) -> list[SkillDemand]:
    return demand_response(filtered_jobs(city, source, start_date, end_date))


@app.get("/api/locations", response_model=list[LocationSummary])
def locations(city: FilterCity = None, source: FilterSource = None, start_date: FilterStart = None, end_date: FilterEnd = None) -> list[LocationSummary]:
    jobs = filtered_jobs(city, source, start_date, end_date)
    result = []
    for location, group in jobs.groupby("location", sort=False):
        median = group["salary_mid_lpa"].median()
        result.append(LocationSummary(location=str(location), jobs=len(group), median_salary_lpa=None if pd.isna(median) else float(median)))
    return sorted(result, key=lambda item: (-item.jobs, item.location))


@app.get("/api/salaries", response_model=SalariesResponse)
def salaries(city: FilterCity = None, source: FilterSource = None, start_date: FilterStart = None, end_date: FilterEnd = None) -> SalariesResponse:
    jobs = filtered_jobs(city, source, start_date, end_date)
    by_location = []
    for location, group in jobs.groupby("location", sort=False):
        median = group["salary_mid_lpa"].median()
        by_location.append(SalarySummary(location=str(location), median_salary_lpa=None if pd.isna(median) else float(median)))
    exploded = jobs.explode("skills")
    by_skill = []
    for skill, group in exploded.dropna(subset=["skills"]).groupby("skills", sort=False):
        median = group["salary_mid_lpa"].median()
        by_skill.append(SkillSalarySummary(skill=str(skill), median_salary_lpa=None if pd.isna(median) else float(median)))
    return SalariesResponse(
        by_location=sorted(by_location, key=lambda item: item.location),
        by_skill=sorted(by_skill, key=lambda item: (-item.median_salary_lpa if item.median_salary_lpa is not None else float("inf"), item.skill)),
    )


@app.get("/api/jobs", response_model=JobsResponse)
def job_listings(
    city: FilterCity = None,
    source: FilterSource = None,
    start_date: FilterStart = None,
    end_date: FilterEnd = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> JobsResponse:
    jobs = filtered_jobs(city, source, start_date, end_date).sort_values("posted_date", ascending=False)
    total = len(jobs)
    page_jobs = jobs.iloc[(page - 1) * page_size : page * page_size]
    items = [JobListing(**{**row, "posted_date": row["posted_date"].date(), "skills": row["skills"]}) for row in page_jobs.to_dict(orient="records")]
    return JobsResponse(items=items, total=total, page=page, page_size=page_size)


@app.get("/api/recommendations", response_model=list[LearningRecommendation])
@app.get("/api/learning-recommendations", response_model=list[LearningRecommendation], include_in_schema=False)
def learning_recommendations(
    current_skills: Annotated[list[str] | None, Query()] = None,
    target_city: Annotated[str, Query()] = "All cities",
    target_role: Annotated[str | None, Query(description="Desired job title or role")] = None,
    years_experience: Annotated[float | None, Query(ge=0, description="Years of professional experience")] = None,
    desired_salary_lpa: Annotated[float | None, Query(ge=0, description="Desired annual salary in LPA")] = None,
    city: FilterCity = None,
    source: FilterSource = None,
    start_date: FilterStart = None,
    end_date: FilterEnd = None,
) -> list[LearningRecommendation]:
    jobs = filtered_jobs(city, source, start_date, end_date)
    scoped_city = target_city if target_city != "All cities" else "All cities"
    recs = recommendations(
        jobs,
        current_skills or [],
        scoped_city,
        target_role=target_role,
        years_experience=years_experience,
        desired_salary_lpa=desired_salary_lpa,
    )
    return [
        LearningRecommendation(
            skill=str(row.skill),
            demand=int(row.demand),
            share=float(row.share),
            score=float(row.score),
            reason=str(row.reason),
        )
        for row in recs.itertuples()
    ]
