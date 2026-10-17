import pandas as pd

from bizchat.analytics import extract_skills, prepare_jobs, recommendations, skill_demand


def test_extract_skills_uses_word_boundaries_and_aliases():
    assert extract_skills("Python, SQL, AWS and RAG") == ["Python", "SQL", "RAG", "AWS"]
    assert "SQL" not in extract_skills("NoSQL database only")


def test_prepare_jobs_calculates_salary_and_skills():
    jobs = prepare_jobs(
        pd.DataFrame(
            [
                {
                    "job_id": "1",
                    "title": "AI Engineer",
                    "company": "Demo",
                    "location": "Pune",
                    "salary_min_lpa": 10,
                    "salary_max_lpa": 20,
                    "posted_date": "2026-10-01",
                    "description": "Python SQL RAG",
                }
            ]
        )
    )
    assert jobs.loc[0, "salary_mid_lpa"] == 15
    assert jobs.loc[0, "skills"] == ["Python", "SQL", "RAG"]


def test_recommendations_exclude_current_skills():
    jobs = prepare_jobs(pd.read_csv("data/raw/jobs.csv"))
    recs = recommendations(jobs, ["Python", "SQL"])
    assert not recs.empty
    assert "Python" not in recs["skill"].tolist()
    assert recs.iloc[0]["demand"] >= recs.iloc[-1]["demand"]


def test_skill_demand_is_sorted():
    jobs = prepare_jobs(pd.read_csv("data/raw/jobs.csv"))
    result = skill_demand(jobs)
    assert result.iloc[0]["jobs"] >= result.iloc[-1]["jobs"]
