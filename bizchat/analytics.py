from __future__ import annotations

import re
from collections.abc import Iterable

import pandas as pd


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
    result["posted_date"] = pd.to_datetime(result["posted_date"])
    result["salary_mid_lpa"] = (result["salary_min_lpa"] + result["salary_max_lpa"]) / 2
    result["skills"] = (result["title"].fillna("") + " " + result["description"].fillna("")).map(extract_skills)
    result["skill_count"] = result["skills"].str.len()
    return result


def skill_demand(jobs: pd.DataFrame) -> pd.DataFrame:
    rows = [{"skill": skill} for skills in jobs["skills"] for skill in skills]
    if not rows:
        return pd.DataFrame(columns=["skill", "jobs"])
    return pd.DataFrame(rows).groupby("skill").size().reset_index(name="jobs").sort_values("jobs", ascending=False)


def explode_skills(jobs: pd.DataFrame) -> pd.DataFrame:
    return jobs[["job_id", "location", "salary_mid_lpa", "posted_date", "skills"]].explode("skills").rename(columns={"skills": "skill"}).dropna(subset=["skill"])


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
    scoped = jobs if target_city == "All cities" else jobs[jobs["location"] == target_city]

    if target_role:
        role_terms = [term for term in re.findall(r"[a-z0-9]+", target_role.casefold()) if len(term) > 1]
        if role_terms:
            role_text = scoped["title"].fillna("").str.casefold()
            role_matches = role_text.map(lambda title: all(term in title for term in role_terms))
            if role_matches.any():
                scoped = scoped[role_matches]

    if years_experience is not None:
        scoped = scoped[scoped["experience_min"] <= years_experience]

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
