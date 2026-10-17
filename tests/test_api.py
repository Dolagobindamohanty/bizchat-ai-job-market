import csv

from fastapi.testclient import TestClient

import api.main as api_main
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


def test_validation_reports_healthy_dataset():
    response = client.get("/api/validation")

    assert response.status_code == 200
    assert response.json() == {
        "api_status": "ok",
        "dataset_row_count": 20,
        "required_columns_present": True,
        "duplicate_key_count": 0,
        "missing_source_urls": 0,
        "invalid_timestamps": 0,
        "deployment_safe": True,
    }


def test_validation_reports_invalid_data_without_network_or_secrets(tmp_path, monkeypatch):
    path = tmp_path / "jobs.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["job_id", "title", "source", "source_url", "posted_date", "collected_at"],
        )
        writer.writeheader()
        writer.writerow({"job_id": "J001", "title": "One", "source": "Feed", "source_url": "", "posted_date": "2026-01-01", "collected_at": "not-a-timestamp"})
        writer.writerow({"job_id": "J001", "title": "Duplicate", "source": "Feed", "source_url": "ftp://invalid.example/job", "posted_date": "2026-01-01", "collected_at": "2026-01-01T00:00:00"})

    monkeypatch.setattr(api_main, "DATA_PATH", path)
    response = client.get("/api/validation")

    assert response.status_code == 200
    body = response.json()
    assert body["api_status"] == "ok"
    assert body["dataset_row_count"] == 2
    assert body["required_columns_present"] is True
    assert body["duplicate_key_count"] == 1
    assert body["missing_source_urls"] == 1
    assert body["invalid_timestamps"] == 2
    assert body["deployment_safe"] is False
