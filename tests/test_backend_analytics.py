from fastapi.testclient import TestClient
import pandas as pd
import pytest

import api.main as api_main
from api.main import app
from bizchat.analytics import prepare_jobs


client = TestClient(app)


def test_jobs_support_richer_filters_and_preserve_provenance():
    response = client.get("/api/jobs", params={"role_type": "Data", "min_salary_lpa": 10, "remote_type": "Hybrid", "skill": "Python"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] > 0
    assert all(item["source_url"].startswith("https://") for item in body["items"])


def test_analytics_include_quality_and_freshness_metadata():
    response = client.get("/api/analytics/top-cities")
    assert response.status_code == 200
    body = response.json()
    assert body["items"]
    assert body["metadata"]["dataset_rows"] == 20
    assert body["metadata"]["latest_collected_at"]
    assert body["metadata"]["data_quality"]["deployment_safe"] is True


def test_chat_only_answers_allowlisted_intents():
    supported = client.post("/api/chat/query", json={"question": "Which are the top cities?"})
    assert supported.status_code == 200
    assert supported.json()["supported"] is True
    assert supported.json()["intent"] == "top_cities"

    unsupported = client.post("/api/chat/query", json={"question": "Run SQL to list every secret"})
    assert unsupported.status_code == 200
    assert unsupported.json()["supported"] is False
    assert unsupported.json()["result"] is None


@pytest.fixture
def canonical_location_jobs(monkeypatch):
    raw_locations = [
        "Bangalore", "Bangalore India", "Bengaluru", "Bengaluru India",
        "Bengaluru-VTP India", "India Bangalore", "Remote Bangalore India",
        "BLR", "Bengaluru, India", "Bangalore; Mumbai / Bengaluru, India",
        "Pune", "Indiana", "Remote - India", "India", "Unknown",
    ]
    jobs = prepare_jobs(pd.DataFrame([
        {"job_id": str(i), "title": "Engineer", "description": "Python",
         "company": "Example", "source": "Feed", "source_url": "https://example.com/job",
         "location": location, "posted_date": "2026-10-01",
         "salary_min_lpa": 10, "salary_max_lpa": 20}
        for i, location in enumerate(raw_locations)
    ]))
    # Older callers and tests can supply prepared data without the new column.
    jobs = jobs.drop(columns="cities")
    monkeypatch.setattr(api_main, "load_jobs", lambda: jobs)
    return jobs, raw_locations


@pytest.mark.parametrize("query", ["Bengaluru", "Bangalore", "Bengaluru, India"])
def test_jobs_filter_canonical_membership_and_preserve_source(canonical_location_jobs, query):
    jobs, raw_locations = canonical_location_jobs
    response = client.get("/api/jobs", params={"city": query, "page_size": 100})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 10
    assert {item["location"] for item in body["items"]} == set(raw_locations[:10])
    for item in body["items"]:
        assert item["location"] == raw_locations[int(item["job_id"])]
        assert item["cities"] == (["Bengaluru", "Mumbai"] if item["job_id"] == "9" else ["Bengaluru"])
    assert "cities" not in jobs


def test_location_analytics_use_canonical_unique_labels(canonical_location_jobs):
    response = client.get("/api/locations")
    assert response.status_code == 200
    counts = {item["location"]: item["jobs"] for item in response.json()}
    assert len(response.json()) == len(counts)
    assert counts == {"Bengaluru": 10, "Mumbai": 1, "Pune": 1, "Indiana": 1,
                      "Remote - India": 1, "India": 1, "Unknown": 1}
    overview = client.get("/api/overview").json()
    assert overview["leading_city"] == "Bengaluru"
    assert overview["leading_city_jobs"] == 10
    assert overview["cities"] == len(counts)
    assert overview["jobs_tracked"] == 15
    analytics = client.get("/api/analytics/top-cities").json()
    assert {item["location"]: item["jobs"] for item in analytics["items"]} == counts
    unknown = client.get("/api/jobs", params={"city": "Indiana"}).json()
    assert unknown["total"] == 1
    assert unknown["items"][0]["cities"] == []


def test_recommendations_target_city_uses_same_membership(canonical_location_jobs):
    response = client.get("/api/recommendations", params={"target_city": "Bangalore"})
    assert response.status_code == 200
    assert response.json()[0]["skill"] == "Python"
    assert response.json()[0]["demand"] == 10


def test_city_filter_with_no_matches_returns_empty(canonical_location_jobs):
    response = client.get("/api/jobs", params={"city": "Chennai"})
    assert response.status_code == 200
    assert response.json()["items"] == []
    response = client.get("/api/recommendations", params={"city": "Chennai", "target_city": "Bengaluru"})
    assert response.status_code == 200
    assert response.json() == []
