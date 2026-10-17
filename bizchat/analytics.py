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


def recommendations(jobs: pd.DataFrame, current_skills: Iterable[str], target_city: str = "All cities") -> pd.DataFrame:
    current = {item.strip() for item in current_skills if item.strip()}
    scoped = jobs if target_city == "All cities" else jobs[jobs["location"] == target_city]
    demand = skill_demand(scoped).rename(columns={"jobs": "demand"})
    if demand.empty:
        return demand.assign(reason=[])
    demand["share"] = demand["demand"] / max(len(scoped), 1)
    demand["reason"] = demand.apply(lambda row: f"Appears in {int(row.demand)} jobs ({row.share:.0%})", axis=1)
    return demand[~demand["skill"].isin(current)].sort_values(["demand", "skill"], ascending=[False, True]).head(8)
