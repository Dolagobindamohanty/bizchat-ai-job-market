from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import date
import os

import pandas as pd

from bizchat.locations import city_tags, matches_city


DEFAULT_FRESHNESS_DAYS = 30


def freshness_cutoff_days() -> int:
    """Return the configured maximum age for public current-only results."""
    raw = os.getenv("BIZCHAT_FRESHNESS_DAYS", str(DEFAULT_FRESHNESS_DAYS)).strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_FRESHNESS_DAYS
    return value if value >= 0 else DEFAULT_FRESHNESS_DAYS


def with_freshness(jobs: pd.DataFrame, today: date | None = None) -> pd.DataFrame:
    """Annotate listings with freshness fields used by public API responses."""
    result = jobs.copy()
    reference = today or date.today()
    posted = pd.to_datetime(result.get("posted_date", pd.Series(index=result.index)), errors="coerce", utc=True)
    cutoff = freshness_cutoff_days()

    def age(value: pd.Timestamp) -> int | None:
        if pd.isna(value):
            return None
        return max((reference - value.date()).days, 0)

    result["age_days"] = posted.map(age).astype("Int64")
    result["freshness_status"] = result["age_days"].map(
        lambda value: "unknown" if pd.isna(value) else "current" if int(value) <= cutoff else "stale"
    )
    collected = pd.to_datetime(result.get("collected_at", pd.Series(index=result.index)), errors="coerce", utc=True)
    result["collected_at"] = collected
    if "last_verified" not in result:
        result["last_verified"] = collected
    else:
        result["last_verified"] = pd.to_datetime(result["last_verified"], errors="coerce", utc=True).fillna(collected)
    return result


def freshness_counts(jobs: pd.DataFrame) -> dict[str, int]:
    statuses = with_freshness(jobs)["freshness_status"]
    return {status: int((statuses == status).sum()) for status in ("current", "stale", "unknown")}


def current_only(jobs: pd.DataFrame, include_stale: bool = False) -> pd.DataFrame:
    """Apply the public current-only default while preserving explicit opt-in."""
    annotated = with_freshness(jobs)
    if include_stale:
        return annotated
    return annotated[annotated["freshness_status"] == "current"].copy()


SKILL_ALIASES: dict[str, tuple[str, ...]] = {
    "Python": ("python",),
    "SQL": ("sql",),
    "Machine Learning": ("machine learning", "ml"),
    "Deep Learning": ("deep learning",),
    "LLM": ("llm", "llms", "large language model", "large language models"),
    "RAG": ("rag", "retrieval augmented generation"),
    "Prompt Engineering": ("prompt engineering",),
    "LangChain": ("langchain",),
    "AWS": ("aws", "amazon web services"),
    "Azure": ("azure",),
    "Docker": ("docker",),
    "Kubernetes": ("kubernetes",),
    "PyTorch": ("pytorch",),
    "TensorFlow": ("tensorflow",),
    "Pandas": ("pandas",),
    "scikit-learn": ("scikit-learn", "sklearn"),
    "Airflow": ("airflow", "apache airflow"),
    "Spark": ("spark", "apache spark"),
    "Power BI": ("power bi",),
    "spaCy": ("spacy",),
}


def _contains_term(text: str, term: str) -> bool:
    escaped = re.escape(term)
    return re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", text.lower()) is not None


def extract_skills(text: str, skill_aliases: dict[str, Iterable[str]] = SKILL_ALIASES) -> list[str]:
    normalized = str(text or "")
    return [skill for skill, aliases in skill_aliases.items() if any(_contains_term(normalized, alias) for alias in aliases)]


def prepare_jobs(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    for column, default in {
        "salary_min_lpa": None, "salary_max_lpa": None, "experience_min": None,
        "remote_type": "Unknown", "location": "Unknown", "source": "Unknown",
        "collected_at": None,
    }.items():
        if column not in result:
            result[column] = default
    result["salary_min_lpa"] = pd.to_numeric(result["salary_min_lpa"], errors="coerce")
    result["salary_max_lpa"] = pd.to_numeric(result["salary_max_lpa"], errors="coerce")
    result["experience_min"] = pd.to_numeric(result["experience_min"], errors="coerce")
    # Source feeds occasionally contain blank or malformed dates.  Keep those
    # rows (validation reports them) while making analytics operations safe.
    result["posted_date"] = pd.to_datetime(result["posted_date"], errors="coerce")
    result["collected_at"] = pd.to_datetime(result["collected_at"], errors="coerce", utc=True)
    result["salary_mid_lpa"] = (result["salary_min_lpa"] + result["salary_max_lpa"]) / 2
    def skills_for_row(row: pd.Series) -> list[str]:
        explicit = row.get("skills")
        if isinstance(explicit, str):
            try:
                explicit = json.loads(explicit)
            except (TypeError, ValueError):
                explicit = []
        if not isinstance(explicit, list):
            explicit = []
        values = [str(value).strip() for value in explicit if str(value).strip()]
        derived = extract_skills(f"{row.get('title', '')} {row.get('description', '')}")
        return list(dict.fromkeys(values + derived))
    result["skills"] = result.apply(skills_for_row, axis=1)
    result["skill_count"] = result["skills"].str.len()
    result["role_type"] = result["title"].fillna("").map(role_type)
    result["experience_level"] = result["experience_min"].map(experience_level)
    result["cities"] = result["location"].map(city_tags)
    return result


def role_type(title: str) -> str:
    text = str(title or "").casefold()
    for label, terms in (("Data", ("data", "analytics")), ("Machine Learning", ("machine learning", "ml ")),
                         ("AI/GenAI", ("ai", "genai", "llm")), ("Platform/DevOps", ("platform", "devops", "sre"))):
        if any(term in text for term in terms):
            return label
    return "Software Engineering"


def experience_level(years: float | None) -> str:
    if pd.isna(years):
        return "Unknown"
    years = float(years)
    return "Entry" if years <= 2 else "Mid" if years <= 5 else "Senior"


def skill_demand(jobs: pd.DataFrame) -> pd.DataFrame:
    rows = [{"skill": skill} for skills in jobs["skills"] for skill in skills]
    if not rows:
        return pd.DataFrame(columns=["skill", "jobs"])
    return pd.DataFrame(rows).groupby("skill").size().reset_index(name="jobs").sort_values("jobs", ascending=False)


def explode_skills(jobs: pd.DataFrame) -> pd.DataFrame:
    return jobs[["job_id", "location", "salary_mid_lpa", "posted_date", "skills"]].explode("skills").rename(columns={"skills": "skill"}).dropna(subset=["skill"])


def explode_cities(jobs: pd.DataFrame) -> pd.DataFrame:
    """One row per job/canonical city, with raw fallback for unrecognized places.

    A multi-city job contributes once to each city, so city counts may sum to
    more than the job total. Country-only and unknown labels remain distinct.
    The returned location is an analytics label; source rows are never changed.
    """
    result = jobs.copy()
    tags = result["cities"] if "cities" in result else result["location"].map(city_tags)
    result["location"] = [
        list(dict.fromkeys(cities)) if isinstance(cities, list) and cities else [location]
        for cities, location in zip(tags, result["location"])
    ]
    return result.explode("location")


def top_cities(jobs: pd.DataFrame) -> pd.DataFrame:
    """Count jobs once per city; multi-city jobs count in every listed city."""
    return explode_cities(jobs).groupby("location", dropna=False).size().reset_index(name="jobs").sort_values(["jobs", "location"], ascending=[False, True])


def roles_over_time(jobs: pd.DataFrame, frequency: str = "month") -> pd.DataFrame:
    period = jobs["posted_date"].dt.to_period("W" if frequency == "week" else "M").astype(str)
    return jobs.assign(period=period).groupby(["period", "role_type"], as_index=False).size().rename(columns={"size": "jobs"}).sort_values(["period", "role_type"])


def salary_distribution_by_role(jobs: pd.DataFrame) -> pd.DataFrame:
    return jobs.dropna(subset=["salary_mid_lpa"]).groupby("role_type")["salary_mid_lpa"].agg(
        count="count", min_salary_lpa="min", p25_salary_lpa=lambda x: x.quantile(.25),
        median_salary_lpa="median", p75_salary_lpa=lambda x: x.quantile(.75), max_salary_lpa="max",
    ).reset_index().sort_values("role_type")


def recommendations(
    jobs: pd.DataFrame,
    current_skills: Iterable[str] = (),
    target_city: str = "All cities",
    target_role: str | None = None,
    years_experience: float | None = None,
    desired_salary_lpa: float | None = None,
) -> pd.DataFrame:
    """Rank skills using market demand and salary fit for a learner profile.

    The optional profile arguments deliberately default to the original behavior:
    callers that only provide current skills still receive the same recommendation
    shape, while newer callers get a score that incorporates salary data.
    """
    current = {item.strip().casefold() for item in current_skills if item and item.strip()}
    scoped = jobs.copy()
    normalized_city = target_city.strip() if isinstance(target_city, str) else target_city
    if normalized_city and normalized_city.casefold() != "all cities":
        scoped = scoped[scoped.apply(lambda row: matches_city(row["location"], normalized_city, row.get("cities")), axis=1).astype(bool)].copy()

    if target_role:
        role_terms = [term for term in re.findall(r"[a-z0-9]+", target_role.casefold()) if len(term) > 1]
        if role_terms:
            role_text = scoped["title"].fillna("").str.casefold()
            role_matches = role_text.map(lambda title: all(term in title for term in role_terms))
            if role_matches.any():
                scoped = scoped[role_matches].copy()

    if years_experience is not None:
        scoped = scoped[scoped["experience_min"] <= years_experience].copy()

    demand = skill_demand(scoped).rename(columns={"jobs": "demand"})
    if demand.empty:
        return demand.assign(share=pd.Series(dtype=float), score=pd.Series(dtype=float), reason=pd.Series(dtype=str))
    demand["share"] = demand["demand"] / max(len(scoped), 1)

    skill_salary = (
        explode_skills(scoped).groupby("skill")["salary_mid_lpa"].median().rename("median_salary_lpa")
    )
    demand = demand.join(skill_salary, on="skill")
    max_demand = max(float(demand["demand"].max()), 1.0)
    demand_score = demand["demand"] / max_demand
    if desired_salary_lpa is not None and desired_salary_lpa > 0:
        salary_score = (demand["median_salary_lpa"] / desired_salary_lpa).clip(upper=1).fillna(0)
    else:
        max_salary = demand["median_salary_lpa"].max()
        salary_score = (demand["median_salary_lpa"] / max(float(max_salary), 1.0)).fillna(0)
    demand["score"] = (100 * (0.6 * demand_score + 0.4 * salary_score)).round(2)

    def recommendation_reason(row: pd.Series) -> str:
        reason = f"Appears in {int(row.demand)} jobs ({row.share:.0%})"
        if pd.notna(row.median_salary_lpa):
            reason += f"; median salary ₹{row.median_salary_lpa:.1f} LPA"
        return reason

    demand["reason"] = demand.apply(recommendation_reason, axis=1)
    return demand[~demand["skill"].str.casefold().isin(current)].sort_values(
        ["score", "demand", "skill"], ascending=[False, False, True]
    ).head(8)
