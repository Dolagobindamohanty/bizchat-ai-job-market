from __future__ import annotations

from datetime import date, timedelta
import ipaddress
import json
import os
import re
import time
from collections import defaultdict, deque
from functools import lru_cache
from pathlib import Path
from typing import Annotated

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from bizchat.analytics import (current_only, freshness_counts, freshness_cutoff_days, prepare_jobs,
                               recommendations, skill_demand, top_cities, roles_over_time,
                               salary_distribution_by_role, explode_cities, with_freshness)
from bizchat.locations import city_tags, matches_city
from collector.validate import validation_report
from database.repository import DatabaseRepository, get_repository
from api.auth import admin_user, current_user
from api.serialization import json_safe
from api.jobmatch import create_jobmatch_router
from api.body_limits import ResumeBodyLimitMiddleware
from bizchat.advanced import alert_preview, resume_match, semantic_search, trend_forecast


ROOT = Path(__file__).resolve().parents[1]


def resolve_data_path(configured: str | None) -> Path:
    """Migrate the former production snapshot name, preserving custom datasets.

    Existing Render services may retain the old environment variable until their
    Blueprint is synced. That retired production name now aliases the daily
    multi-source snapshot; arbitrary operator-supplied paths remain unchanged.
    """
    path = Path(configured) if configured else ROOT / "data" / "raw" / "jobs.csv"
    current = ROOT / "data" / "raw" / "jobs-india.csv"
    legacy = ROOT / "data" / "raw" / "jobs-greenhouse-india.csv"
    return current if path.resolve() == legacy.resolve() and current.exists() else path


DATA_PATH = resolve_data_path(os.getenv("BIZCHAT_DATA_PATH"))


def validate_data_path() -> None:
    if not DATA_PATH.is_file():
        raise RuntimeError(f"Configured BIZCHAT_DATA_PATH does not exist or is not a file: {DATA_PATH}")


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
    cities: list[str] = Field(default_factory=list)
    salary_min_lpa: float | None
    salary_max_lpa: float | None
    salary_mid_lpa: float | None
    experience_min: float | None
    remote_type: str | None = None
    source: str
    source_url: str | None
    posted_date: date | None
    description: str | None = None
    skills: list[str]
    freshness_status: str
    age_days: int | None
    last_verified: str | None = None
    collected_at: str | None = None
    source_type: str | None = None
    employment_type: str | None = None
    listing_status: str | None = None
    verification_status: str | None = None
    last_verified_at: str | None = None


class JobsResponse(BaseModel):
    items: list[JobListing]
    total: int
    page: int
    page_size: int


class AnalyticsMetadata(BaseModel):
    dataset_rows: int
    filtered_rows: int
    source_count: int
    latest_collected_at: str | None
    oldest_posted_date: date | None
    newest_posted_date: date | None
    data_quality: dict[str, int | bool]
    freshness_cutoff_days: int
    freshness_cutoff_date: date
    freshness_counts: dict[str, int]
    dataset_scope: dict[str, object]


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    include_stale: bool = False


class ChatResponse(BaseModel):
    supported: bool
    intent: str | None = None
    result: dict | list | None = None
    message: str


class LearningRecommendation(BaseModel):
    skill: str
    demand: int
    share: float
    reason: str
    score: float = 0.0


class ResumeMatchRequest(BaseModel):
    resume_text: str = Field(min_length=1, max_length=50_000)
    target_role: str = Field(min_length=1, max_length=200)
    years_experience: float = Field(ge=0, le=100)


class SemanticSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=20, ge=1, le=100)
    include_stale: bool = False


class AlertPreviewRequest(BaseModel):
    filters: dict = Field(default_factory=dict)
    model_config = {"extra": "allow"}


class SemanticSearchResponse(BaseModel):
    query: str
    integration: str
    items: list[dict]


class ValidationResponse(BaseModel):
    api_status: str
    dataset_row_count: int
    required_columns_present: bool
    duplicate_key_count: int
    missing_source_urls: int
    invalid_timestamps: int
    deployment_safe: bool


class JobHistoryResponse(BaseModel):
    items: list[dict]


class SavedDataResponse(BaseModel):
    id: str
    user_id: str
    data_type: str
    payload: dict
    created_at: str
    updated_at: str


app = FastAPI(title="BizChat API", version="2.0.0")


class RequestGuardMiddleware(BaseHTTPMiddleware):
    """Bound request bodies and expensive public endpoints per process.

    Render's proxy should provide the primary rate limit for multi-worker or
    multi-instance deployments. This small local guard still prevents an
    accidental request storm from exhausting one worker.
    """

    MAX_BODY_BYTES = int(os.getenv("MAX_REQUEST_BODY_BYTES", str(256 * 1024)))
    WINDOW_SECONDS = 60
    EXPENSIVE_LIMIT = int(os.getenv("EXPENSIVE_REQUESTS_PER_MINUTE", "30"))
    EXPENSIVE_PATHS = {
        "/api/match-resume",
        "/api/search/semantic",
        "/api/chat/query",
        "/api/chat",
        "/api/alerts/preview",
        "/api/resume/parse",
        "/api/resume/upload",
        "/api/resume/match",
        "/api/resume/suggestions",
    }
    _requests: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    @staticmethod
    def _client_ip(request: Request) -> str:
        """Trust X-Forwarded-For only when the immediate peer is configured."""
        peer = request.client.host if request.client else "unknown"
        trusted = set()
        for value in os.getenv("TRUSTED_PROXY_IPS", "").split(","):
            try:
                if value.strip():
                    trusted.add(ipaddress.ip_address(value.strip()))
            except ValueError:
                continue
        try:
            peer_ip = ipaddress.ip_address(peer)
        except ValueError:
            return peer
        if peer_ip not in trusted:
            return peer
        for candidate in reversed([part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]):
            try:
                candidate_ip = ipaddress.ip_address(candidate)
            except ValueError:
                continue
            if candidate_ip not in trusted:
                return str(candidate_ip)
        return peer

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method == "OPTIONS":
            return await call_next(request)
        content_length = request.headers.get("content-length")
        body_limit = 5 * 1024 * 1024 + 64 * 1024 if request.url.path == "/api/resume/upload" else self.MAX_BODY_BYTES
        if content_length:
            try:
                if int(content_length) < 0:
                    raise ValueError()
                if int(content_length) > body_limit:
                    return JSONResponse(status_code=413, content={"detail": "Request body is too large"})
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Invalid Content-Length"})

        if request.url.path in self.EXPENSIVE_PATHS:
            client = self._client_ip(request)
            key = (client, request.url.path)
            now = time.monotonic()
            if len(self._requests) > 10_000:
                for stale_key in [
                    candidate for candidate, candidate_bucket in self._requests.items()
                    if not candidate_bucket or now - candidate_bucket[-1] >= self.WINDOW_SECONDS
                ]:
                    del self._requests[stale_key]
            bucket = self._requests[key]
            while bucket and now - bucket[0] >= self.WINDOW_SECONDS:
                bucket.popleft()
            if len(bucket) >= self.EXPENSIVE_LIMIT:
                response = JSONResponse(status_code=429, content={"detail": "Too many requests"})
                response.headers["Retry-After"] = str(self.WINDOW_SECONDS)
                return response
            bucket.append(now)
        return await call_next(request)


app.add_middleware(ResumeBodyLimitMiddleware)
app.add_middleware(RequestGuardMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_origin_regex=r"^(?:https://bizchat-ai-job-market(?:-[a-z0-9-]+)?\.vercel\.app|https?://(?:localhost|127\.0\.0\.1)(?::\d+)?)$",
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@lru_cache(maxsize=2)
def _load_jobs(path: str, modified_ns: int, file_size: int) -> pd.DataFrame:
    """Load and normalize one version of the local source."""
    return prepare_jobs(pd.read_csv(path))


def load_jobs() -> pd.DataFrame:
    """Load and normalize the current source version."""
    stat = DATA_PATH.stat()
    return _load_jobs(str(DATA_PATH), stat.st_mtime_ns, stat.st_size)


def data_version() -> tuple[str, int, int]:
    stat = DATA_PATH.stat()
    return str(DATA_PATH), stat.st_mtime_ns, stat.st_size


app.include_router(create_jobmatch_router(load_jobs, data_version))


@app.on_event("startup")
def validate_configured_data_path() -> None:
    validate_data_path()


def repository() -> DatabaseRepository:
    return get_repository()


def filtered_jobs(
    city: str | None,
    source: str | None,
    start_date: date | None,
    end_date: date | None,
    role_type: str | None = None,
    experience_level: str | None = None,
    min_salary_lpa: float | None = None,
    max_salary_lpa: float | None = None,
    remote_type: str | None = None,
    skill: str | None = None,
    include_stale: bool = False,
) -> pd.DataFrame:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date")
    jobs = current_only(load_jobs(), include_stale=include_stale)
    if "cities" not in jobs:
        jobs = jobs.assign(cities=jobs["location"].map(city_tags))
    if city:
        jobs = jobs[jobs.apply(lambda row: matches_city(row["location"], city, row["cities"]), axis=1).astype(bool)]
    if source:
        jobs = jobs[jobs["source"] == source]
    if start_date:
        jobs = jobs[jobs["posted_date"] >= pd.Timestamp(start_date)]
    if end_date:
        jobs = jobs[jobs["posted_date"] < pd.Timestamp(end_date) + pd.Timedelta(days=1)]
    if role_type:
        jobs = jobs[jobs["role_type"].str.casefold() == role_type.casefold()]
    if experience_level:
        jobs = jobs[jobs["experience_level"].str.casefold() == experience_level.casefold()]
    if min_salary_lpa is not None:
        jobs = jobs[jobs["salary_max_lpa"] >= min_salary_lpa]
    if max_salary_lpa is not None:
        jobs = jobs[jobs["salary_min_lpa"] <= max_salary_lpa]
    if remote_type:
        jobs = jobs[jobs["remote_type"].str.casefold() == remote_type.casefold()]
    if skill:
        jobs = jobs[jobs["skills"].map(lambda values: any(skill.casefold() == item.casefold() for item in values))]
    return jobs.copy()


FilterCity = Annotated[str | None, Query(description="Canonical city membership (aliases accepted); exact match for unknown locations")]
FilterSource = Annotated[str | None, Query(description="Exact source filter")]
FilterStart = Annotated[date | None, Query(description="Earliest posted date")]
FilterEnd = Annotated[date | None, Query(description="Latest posted date")]
FilterRole = Annotated[str | None, Query(description="Derived role category")]
FilterExperience = Annotated[str | None, Query(description="Entry, Mid, Senior, or Unknown")]
FilterMinSalary = Annotated[float | None, Query(ge=0)]
FilterMaxSalary = Annotated[float | None, Query(ge=0)]
FilterRemote = Annotated[str | None, Query(description="Remote, Hybrid, On-site, etc.")]
FilterSkill = Annotated[str | None, Query(description="Canonical skill")]
FilterIncludeStale = Annotated[bool, Query(description="Include listings older than the freshness cutoff or without a posted date")]


def filtered_jobs_dependency(
    city: FilterCity = None, source: FilterSource = None,
    start_date: FilterStart = None, end_date: FilterEnd = None,
    role_type: FilterRole = None, experience_level: FilterExperience = None,
    min_salary_lpa: FilterMinSalary = None, max_salary_lpa: FilterMaxSalary = None,
    remote_type: FilterRemote = None, skill: FilterSkill = None,
    include_stale: FilterIncludeStale = False,
) -> pd.DataFrame:
    return filtered_jobs(city, source, start_date, end_date, role_type, experience_level,
                         min_salary_lpa, max_salary_lpa, remote_type, skill, include_stale)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/validation", response_model=ValidationResponse)
def validation() -> ValidationResponse:
    """Return the network-free readiness checks for the deployed dataset."""
    stat = DATA_PATH.stat()
    report = _validation_report(str(DATA_PATH), stat.st_mtime_ns, stat.st_size)
    return ValidationResponse(
        api_status="ok",
        dataset_row_count=report.dataset_row_count,
        required_columns_present=report.required_columns_present,
        duplicate_key_count=report.duplicate_key_count,
        missing_source_urls=report.missing_source_urls,
        invalid_timestamps=report.invalid_timestamps,
        deployment_safe=report.deployment_safe,
    )


@lru_cache(maxsize=4)
def _validation_report(data_path: str, modified_ns: int, file_size: int):
    """Cache validation by path and file identity so replacements are seen."""
    return validation_report(Path(data_path))


@app.get("/api/overview", response_model=Overview)
def overview(jobs: pd.DataFrame = Depends(filtered_jobs_dependency)) -> Overview:
    demand = skill_demand(jobs)
    city_counts = top_cities(jobs).set_index("location")["jobs"]
    disclosed_salaries = jobs["salary_mid_lpa"].dropna()
    median = disclosed_salaries.median() if not disclosed_salaries.empty else None
    return Overview(
        jobs_tracked=len(jobs),
        median_salary_lpa=None if pd.isna(median) else float(median),
        top_skill=None if demand.empty else str(demand.iloc[0]["skill"]),
        top_skill_jobs=0 if demand.empty else int(demand.iloc[0]["jobs"]),
        leading_city=None if city_counts.empty else str(city_counts.index[0]),
        leading_city_jobs=0 if city_counts.empty else int(city_counts.iloc[0]),
        cities=len(city_counts),
        companies=int(jobs["company"].nunique()),
        sources=int(jobs["source"].nunique()),
        posted_from=None if jobs["posted_date"].dropna().empty else jobs["posted_date"].min().date(),
        posted_to=None if jobs["posted_date"].dropna().empty else jobs["posted_date"].max().date(),
    )


def demand_response(jobs: pd.DataFrame) -> list[SkillDemand]:
    demand = skill_demand(jobs)
    total = max(len(jobs), 1)
    return [SkillDemand(skill=str(row.skill), jobs=int(row.jobs), share=float(row.jobs / total)) for row in demand.itertuples()]


@app.get("/api/skills", response_model=list[SkillDemand])
@app.get("/api/skill-demand", response_model=list[SkillDemand], include_in_schema=False)
def skills(jobs: pd.DataFrame = Depends(filtered_jobs_dependency)) -> list[SkillDemand]:
    return demand_response(jobs)


@app.get("/api/locations", response_model=list[LocationSummary])
def locations(jobs: pd.DataFrame = Depends(filtered_jobs_dependency)) -> list[LocationSummary]:
    """Count each job once per city; multi-city jobs appear in each city group."""
    result = []
    for location, group in explode_cities(jobs).groupby("location", sort=False):
        median = group["salary_mid_lpa"].median()
        result.append(LocationSummary(location=str(location), jobs=len(group), median_salary_lpa=None if pd.isna(median) else float(median)))
    return sorted(result, key=lambda item: (-item.jobs, item.location))


@app.get("/api/salaries", response_model=SalariesResponse)
def salaries(jobs: pd.DataFrame = Depends(filtered_jobs_dependency)) -> SalariesResponse:
    """Include multi-city jobs in each city's salary sample, once per city."""
    by_location = []
    for location, group in explode_cities(jobs).groupby("location", sort=False):
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
    jobs: pd.DataFrame = Depends(filtered_jobs_dependency),
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> JobsResponse:
    jobs = jobs.sort_values(
        ["posted_date", "job_id"], ascending=[False, True], na_position="last"
    )
    total = len(jobs)
    page_jobs = jobs.iloc[(page - 1) * page_size : page * page_size]
    items = []
    for row in page_jobs.to_dict(orient="records"):
        payload = json_safe({**row, "job_id": str(row["job_id"]),
                             "posted_date": row["posted_date"].date() if pd.notna(row["posted_date"]) else None,
                             "skills": row["skills"]})
        items.append(JobListing(**payload))
    return JobsResponse(items=items, total=total, page=page, page_size=page_size)


@lru_cache(maxsize=4)
def _static_metadata(data_path: str, modified_ns: int, file_size: int) -> dict:
    path = Path(data_path)
    report = validation_report(path)
    collected = pd.to_datetime(pd.read_csv(path, usecols=["collected_at"])["collected_at"], errors="coerce")
    jobs = load_jobs()
    return {"dataset_rows": len(jobs), "source_count": int(jobs["source"].nunique()),
            "latest_collected_at": None if collected.dropna().empty else collected.max().isoformat(),
            "oldest_posted_date": None if jobs["posted_date"].dropna().empty else jobs["posted_date"].min().date(),
            "newest_posted_date": None if jobs["posted_date"].dropna().empty else jobs["posted_date"].max().date(),
             "data_quality": {"deployment_safe": report.deployment_safe, "duplicate_key_count": report.duplicate_key_count,
                              "missing_source_urls": report.missing_source_urls, "invalid_source_urls": report.invalid_source_urls,
                              "invalid_keys": report.invalid_keys, "invalid_timestamps": report.invalid_timestamps}}


def _metadata(jobs: pd.DataFrame) -> AnalyticsMetadata:
    dataset = with_freshness(load_jobs())
    stat = DATA_PATH.stat()
    static = _static_metadata(str(DATA_PATH), stat.st_mtime_ns, stat.st_size)
    collected = dataset["collected_at"].dropna() if "collected_at" in dataset else pd.Series(dtype="datetime64[ns]")
    posted = dataset["posted_date"].dropna()
    static.update(
        dataset_rows=len(dataset),
        source_count=int(dataset["source"].nunique()),
        latest_collected_at=None if collected.empty else collected.max().isoformat(),
        oldest_posted_date=None if posted.empty else posted.min().date(),
        newest_posted_date=None if posted.empty else posted.max().date(),
    )
    counts = freshness_counts(dataset)
    cutoff_days = freshness_cutoff_days()
    scope = {
        "scope": "configured dataset",
        "row_count": len(dataset),
        "source_count": int(dataset["source"].nunique()),
        "company_count": int(dataset["company"].nunique()),
        "sources": sorted(str(value) for value in dataset["source"].dropna().unique()),
        "location_count": int(dataset["location"].nunique()),
        "locations": sorted(str(value) for value in dataset["location"].dropna().unique()),
    }
    return AnalyticsMetadata(**static, filtered_rows=len(jobs), freshness_cutoff_days=cutoff_days,
                             freshness_cutoff_date=date.today() - timedelta(days=cutoff_days), freshness_counts=counts,
                             dataset_scope=scope)


@app.get("/api/analytics/top-cities")
@app.get("/api/analytics/cities", include_in_schema=False)
@app.get("/api/analytics/top_cities", include_in_schema=False)
def analytics_top_cities(jobs: pd.DataFrame = Depends(filtered_jobs_dependency)) -> dict:
    return json_safe({"items": top_cities(jobs).to_dict(orient="records"), "metadata": _metadata(jobs).model_dump()})


@app.get("/api/analytics/top-skills")
@app.get("/api/analytics/top_skills", include_in_schema=False)
def analytics_top_skills(jobs: pd.DataFrame = Depends(filtered_jobs_dependency)) -> dict:
    return {"items": demand_response(jobs), "metadata": _metadata(jobs).model_dump()}


@app.get("/api/analytics/roles-over-time")
@app.get("/api/analytics/roles_over_time", include_in_schema=False)
def analytics_roles_over_time(frequency: Annotated[str, Query(pattern="^(week|month)$")] = "month", jobs: pd.DataFrame = Depends(filtered_jobs_dependency)) -> dict:
    return json_safe({"items": roles_over_time(jobs, frequency).to_dict(orient="records"), "metadata": _metadata(jobs).model_dump()})


@app.get("/api/analytics/salary-distribution-by-role")
@app.get("/api/analytics/salary-distribution", include_in_schema=False)
@app.get("/api/analytics/salary_distribution_by_role", include_in_schema=False)
def analytics_salary_distribution(jobs: pd.DataFrame = Depends(filtered_jobs_dependency)) -> dict:
    return json_safe({"items": salary_distribution_by_role(jobs).to_dict(orient="records"), "metadata": _metadata(jobs).model_dump()})


@app.get("/api/data-quality")
def data_quality() -> dict:
    jobs = load_jobs()
    metadata = _metadata(jobs).model_dump()
    return json_safe({"metadata": metadata,
                      "freshness_cutoff_days": metadata["freshness_cutoff_days"],
                      "freshness_cutoff_date": metadata["freshness_cutoff_date"],
                      "freshness_counts": metadata["freshness_counts"],
                      "dataset_scope": metadata["dataset_scope"]})


CHAT_INTENTS = {
    "salary distribution by role": "salary_distribution_by_role",
    "salary distribution": "salary_distribution_by_role",
    "salary by role": "salary_distribution_by_role",
    "roles over time": "roles_over_time",
    "top cities": "top_cities",
    "top skills": "top_skills",
    "skills": "top_skills",
}


@app.post("/api/chat/query", response_model=ChatResponse)
@app.post("/api/chat", response_model=ChatResponse, include_in_schema=False)
def chat_query(request: ChatRequest) -> ChatResponse:
    normalized = " ".join(request.question.casefold().split())
    intent = next((value for phrase, value in sorted(CHAT_INTENTS.items(), key=lambda item: -len(item[0]))
                   if re.search(rf"\b{re.escape(phrase)}\b", normalized)), None)
    if intent is None:
        return ChatResponse(supported=False, message="Unsupported question. Ask about top cities, top skills, roles over time, or salary distribution by role.")
    jobs = current_only(load_jobs(), include_stale=request.include_stale)
    if intent == "top_cities":
        result = top_cities(jobs).to_dict(orient="records")
    elif intent == "top_skills":
        result = demand_response(jobs)
    elif intent == "roles_over_time":
        result = roles_over_time(jobs).to_dict(orient="records")
    else:
        result = salary_distribution_by_role(jobs).to_dict(orient="records")
    return ChatResponse(supported=True, intent=intent, result=json_safe(result), message="Query answered from the local analytics dataset.")


@app.get("/api/job-history", response_model=JobHistoryResponse)
@app.get("/api/jobs/history", response_model=JobHistoryResponse, include_in_schema=False)
@app.get("/api/job-runs", response_model=JobHistoryResponse, include_in_schema=False)
def job_history(limit: Annotated[int, Query(ge=1, le=100)] = 30, user_id: str = Depends(admin_user), repo: DatabaseRepository = Depends(repository)) -> JobHistoryResponse:
    return JobHistoryResponse(items=repo.list_job_runs(limit))


def save_data(data_type: str, payload: dict, user_id: str, repo: DatabaseRepository) -> SavedDataResponse:
    payload_size = len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    if payload_size > 20 * 1024:
        raise HTTPException(status_code=413, detail="Saved payload exceeds the 20KB limit")
    try:
        return SavedDataResponse(**repo.save_user_data(user_id, data_type, payload))
    except ValueError as exc:
        if "20KB" in str(exc):
            raise HTTPException(status_code=413, detail="Saved payload exceeds the 20KB limit") from exc
        raise


def list_data(data_type: str, user_id: str, repo: DatabaseRepository) -> list[SavedDataResponse]:
    return [SavedDataResponse(**item) for item in repo.list_user_data(user_id, data_type)]


@app.post("/api/saved/profile", response_model=SavedDataResponse)
def save_profile(payload: dict, user_id: str = Depends(current_user), repo: DatabaseRepository = Depends(repository)) -> SavedDataResponse:
    return save_data("profile", payload, user_id, repo)


@app.get("/api/saved/profile", response_model=list[SavedDataResponse])
def saved_profile(user_id: str = Depends(current_user), repo: DatabaseRepository = Depends(repository)) -> list[SavedDataResponse]:
    return list_data("profile", user_id, repo)


@app.post("/api/saved/searches", response_model=SavedDataResponse)
@app.post("/api/saved/search", response_model=SavedDataResponse, include_in_schema=False)
def save_search(payload: dict, user_id: str = Depends(current_user), repo: DatabaseRepository = Depends(repository)) -> SavedDataResponse:
    return save_data("search", payload, user_id, repo)


@app.get("/api/saved/searches", response_model=list[SavedDataResponse])
@app.get("/api/saved/search", response_model=list[SavedDataResponse], include_in_schema=False)
def saved_searches(user_id: str = Depends(current_user), repo: DatabaseRepository = Depends(repository)) -> list[SavedDataResponse]:
    return list_data("search", user_id, repo)


@app.post("/api/saved/learning-plans", response_model=SavedDataResponse)
@app.post("/api/saved/learning-plan", response_model=SavedDataResponse, include_in_schema=False)
def save_learning_plan(payload: dict, user_id: str = Depends(current_user), repo: DatabaseRepository = Depends(repository)) -> SavedDataResponse:
    return save_data("learning_plan", payload, user_id, repo)


@app.get("/api/saved/learning-plans", response_model=list[SavedDataResponse])
@app.get("/api/saved/learning-plan", response_model=list[SavedDataResponse], include_in_schema=False)
def saved_learning_plans(user_id: str = Depends(current_user), repo: DatabaseRepository = Depends(repository)) -> list[SavedDataResponse]:
    return list_data("learning_plan", user_id, repo)


@app.get("/api/recommendations", response_model=list[LearningRecommendation])
@app.get("/api/learning-recommendations", response_model=list[LearningRecommendation], include_in_schema=False)
def learning_recommendations(
    current_skills: Annotated[list[str] | None, Query()] = None,
    target_city: Annotated[str, Query()] = "All cities",
    target_role: Annotated[str | None, Query(description="Desired job title or role")] = None,
    years_experience: Annotated[float | None, Query(ge=0, description="Years of professional experience")] = None,
    desired_salary_lpa: Annotated[float | None, Query(ge=0, description="Desired annual salary in LPA")] = None,
    jobs: pd.DataFrame = Depends(filtered_jobs_dependency),
) -> list[LearningRecommendation]:
    recs = recommendations(
        jobs,
        current_skills or [],
        target_city,
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


@app.post("/api/match-resume")
def match_resume(request: ResumeMatchRequest) -> dict:
    """Compare resume skills with local target-role demand; no model or network calls."""
    return json_safe(resume_match(request.resume_text, request.target_role, request.years_experience, load_jobs()))


@app.get("/api/trends/forecast")
def trends_forecast(include_stale: bool = False) -> dict:
    """Return week-over-week demand and a deterministic next-period baseline."""
    return json_safe(trend_forecast(current_only(load_jobs(), include_stale=include_stale)))


@app.get("/api/search/semantic", response_model=SemanticSearchResponse)
def semantic_search_get(q: Annotated[str, Query(min_length=1, max_length=500)], limit: Annotated[int, Query(ge=1, le=100)] = 20, include_stale: bool = False) -> SemanticSearchResponse:
    return SemanticSearchResponse(query=q, integration="deterministic local token/skill relevance; pgvector integration boundary documented, not enabled", items=json_safe(semantic_search(q, current_only(load_jobs(), include_stale=include_stale), limit)))


@app.post("/api/search/semantic", response_model=SemanticSearchResponse)
def semantic_search_post(request: SemanticSearchRequest) -> SemanticSearchResponse:
    return SemanticSearchResponse(query=request.query, integration="deterministic local token/skill relevance; pgvector integration boundary documented, not enabled", items=json_safe(semantic_search(request.query, current_only(load_jobs(), include_stale=request.include_stale), request.limit)))


@app.post("/api/alerts/preview")
def alerts_preview(request: AlertPreviewRequest, include_stale: bool = False) -> dict:
    filters = dict(request.filters or {key: value for key, value in request.model_extra.items() if key != "filters"})
    include_stale = include_stale or bool(filters.pop("include_stale", False))
    return json_safe(alert_preview(filters, current_only(load_jobs(), include_stale=include_stale)))
