import csv
import pandas as pd

from fastapi.testclient import TestClient

import api.main as api_main
from api.main import app, load_jobs
from bizchat.analytics import prepare_jobs


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
    assert body["items"][0]["source_url"].startswith("https://")


def test_saved_data_fails_closed_without_auth_configuration(monkeypatch):
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    assert client.post("/api/saved/profile", json={"target_role": "Data Scientist"}).status_code == 503
    assert client.get("/api/saved/profile").status_code == 503


def test_saved_data_requires_bearer_when_supabase_configured(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_JWT_SECRET", "test-secret")
    assert client.post("/api/saved/searches", json={"city": "Pune"}).status_code == 401


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


def test_cors_allows_bizchat_project_and_localhost_but_not_other_vercel_projects():
    allowed = client.options("/api/health", headers={"Origin": "https://bizchat-ai-job-market.vercel.app", "Access-Control-Request-Method": "GET"})
    localhost = client.options("/api/health", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    denied = client.options("/api/health", headers={"Origin": "https://another-project.vercel.app", "Access-Control-Request-Method": "GET"})

    assert allowed.headers["access-control-allow-origin"] == "https://bizchat-ai-job-market.vercel.app"
    assert localhost.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-origin" not in denied.headers


def test_cors_allows_bizchat_preview_deployment_but_not_similar_project():
    preview = client.options(
        "/api/health",
        headers={"Origin": "https://bizchat-ai-job-market-git-main.vercel.app", "Access-Control-Request-Method": "GET"},
    )
    similar = client.options(
        "/api/health",
        headers={"Origin": "https://bizchat-ai-job-marketing.vercel.app", "Access-Control-Request-Method": "GET"},
    )

    assert preview.headers["access-control-allow-origin"] == "https://bizchat-ai-job-market-git-main.vercel.app"
    assert "access-control-allow-origin" not in similar.headers


def test_request_guard_rejects_oversized_body():
    response = client.post(
        "/api/search/semantic",
        content="{}",
        headers={"Content-Length": str(300 * 1024), "Content-Type": "application/json"},
    )

    assert response.status_code == 413


def test_request_guard_ignores_untrusted_forwarded_for(monkeypatch):
    api_main.RequestGuardMiddleware._requests.clear()
    monkeypatch.delenv("TRUSTED_PROXY_IPS", raising=False)
    request = type("Request", (), {"client": type("Client", (), {"host": "10.0.0.8"})(), "headers": {"x-forwarded-for": "203.0.113.9"}})()
    assert api_main.RequestGuardMiddleware._client_ip(request) == "10.0.0.8"


def test_configured_data_path_validation_fails_loudly(monkeypatch, tmp_path):
    missing = tmp_path / "missing.csv"
    monkeypatch.setattr(api_main, "DATA_PATH", missing)
    try:
        api_main.validate_data_path()
    except RuntimeError as exc:
        assert str(missing) in str(exc)
    else:
        raise AssertionError("missing configured data path should fail validation")


def test_jobs_serialize_missing_salary_source_url_and_nat_as_null(monkeypatch):
    jobs = prepare_jobs(pd.DataFrame([{
        "job_id": "missing-1", "title": "Data Engineer", "company": "Example",
        "location": "Pune", "source": "Feed", "source_url": None,
        "posted_date": pd.NaT, "description": "Python", "salary_min_lpa": None,
         "salary_max_lpa": None, "experience_min": None, "remote_type": None,
         "description": None,
    }]))
    monkeypatch.setattr(api_main, "load_jobs", lambda: jobs)

    response = client.get("/api/jobs", params={"include_stale": "true"})

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["source_url"] is None
    assert item["salary_min_lpa"] is None
    assert item["salary_max_lpa"] is None
    assert item["posted_date"] is None
    assert item["remote_type"] is None
    assert item["description"] is None


def test_date_filters_are_nat_safe_and_reversed_ranges_are_rejected(monkeypatch):
    jobs = prepare_jobs(pd.DataFrame([
        {"job_id": "dated", "title": "Engineer", "company": "Example", "location": "Pune", "source": "Feed", "source_url": "https://example.com/1", "posted_date": "2026-01-10", "description": "", "remote_type": "Unknown"},
        {"job_id": "missing", "title": "Engineer", "company": "Example", "location": "Pune", "source": "Feed", "source_url": "https://example.com/2", "posted_date": None, "description": "", "remote_type": "Unknown"},
    ]))
    monkeypatch.setattr(api_main, "load_jobs", lambda: jobs)

    filtered = client.get("/api/jobs", params={"start_date": "2026-01-01", "end_date": "2026-01-31", "include_stale": "true"})
    reversed_range = client.get("/api/jobs", params={"start_date": "2026-02-01", "end_date": "2026-01-01", "include_stale": "true"})

    assert filtered.status_code == 200
    assert [item["job_id"] for item in filtered.json()["items"]] == ["dated"]
    assert reversed_range.status_code == 422


def test_stale_and_unknown_posted_jobs_are_current_only_by_default(monkeypatch):
    jobs = prepare_jobs(pd.DataFrame([
        {"job_id": "fresh", "title": "Engineer", "company": "Example", "location": "Pune", "source": "Feed", "source_url": "https://example.com/fresh", "posted_date": "2026-10-01", "collected_at": "2026-10-20T00:00:00Z", "description": "Python"},
        {"job_id": "stale-2023", "title": "Engineer", "company": "Example", "location": "Pune", "source": "Feed", "source_url": "https://example.com/stale", "posted_date": "2023-01-01", "collected_at": "2026-10-20T00:00:00Z", "description": "Python"},
        {"job_id": "unknown", "title": "Engineer", "company": "Example", "location": "Pune", "source": "Feed", "source_url": "https://example.com/unknown", "posted_date": None, "collected_at": "2026-10-20T00:00:00Z", "description": "Python"},
    ]))
    monkeypatch.setattr(api_main, "load_jobs", lambda: jobs)

    current = client.get("/api/jobs", params={"page_size": 100})
    assert current.status_code == 200
    assert [item["job_id"] for item in current.json()["items"]] == ["fresh"]
    assert current.json()["items"][0]["freshness_status"] == "current"
    assert current.json()["items"][0]["last_verified"]
    assert current.json()["items"][0]["collected_at"]

    all_jobs = client.get("/api/jobs", params={"page_size": 100, "include_stale": "true"})
    assert [item["job_id"] for item in all_jobs.json()["items"]] == ["fresh", "stale-2023", "unknown"]
    by_id = {item["job_id"]: item for item in all_jobs.json()["items"]}
    assert by_id["stale-2023"]["freshness_status"] == "stale"
    assert by_id["stale-2023"]["age_days"] > 90
    assert by_id["unknown"]["freshness_status"] == "unknown"
    assert by_id["unknown"]["age_days"] is None


def test_data_quality_reports_freshness_and_dataset_scope(monkeypatch):
    jobs = prepare_jobs(pd.DataFrame([
        {"job_id": "fresh", "title": "Engineer", "company": "Example", "location": "Pune", "source": "Feed", "source_url": "https://example.com/fresh", "posted_date": "2026-10-01", "description": "Python"},
        {"job_id": "stale", "title": "Engineer", "company": "OtherCo", "location": "Bengaluru", "source": "Other", "source_url": "https://example.com/stale", "posted_date": "2023-01-01", "description": "Python"},
    ]))
    monkeypatch.setattr(api_main, "load_jobs", lambda: jobs)

    body = client.get("/api/data-quality").json()
    assert body["freshness_cutoff_days"] == 90
    assert body["freshness_cutoff_date"] == (pd.Timestamp.today().date() - pd.Timedelta(days=90)).isoformat()
    assert body["freshness_counts"] == {"current": 1, "stale": 1, "unknown": 0}
    assert body["dataset_scope"]["row_count"] == 2
    assert body["dataset_scope"]["company_count"] == 2
    assert set(body["dataset_scope"]["sources"]) == {"Feed", "Other"}
