from fastapi.testclient import TestClient

from api.main import app, load_jobs


client = TestClient(app)


def test_overview_and_skill_demand_use_csv_data():
    overview = client.get("/api/overview")
    skills = client.get("/api/skills")

    assert overview.status_code == 200
    assert overview.json()["jobs_tracked"] == len(load_jobs())
    assert skills.status_code == 200
    assert skills.json()[0]["skill"] == "Python"


def test_job_listings_filter_and_paginate():
    response = client.get("/api/jobs", params={"city": "Bengaluru", "page_size": 2})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 7
    assert len(body["items"]) == 2
    assert all(item["location"] == "Bengaluru" for item in body["items"])


def test_recommendations_exclude_current_skills():
    response = client.get("/api/recommendations", params=[("current_skills", "Python"), ("current_skills", "SQL")])

    assert response.status_code == 200
    assert all(item["skill"] not in {"Python", "SQL"} for item in response.json())


def test_recommendations_accept_profile_inputs_and_return_score():
    response = client.get(
        "/api/recommendations",
        params={
            "target_role": "Data Scientist",
            "years_experience": 3,
            "desired_salary_lpa": 18,
            "current_skills": "Python",
        },
    )

    assert response.status_code == 200
    recommendations = response.json()
    assert recommendations
    assert all(item["skill"] != "Python" for item in recommendations)
    assert all(0 <= item["score"] <= 100 for item in recommendations)
    assert all("median salary" in item["reason"] for item in recommendations)
