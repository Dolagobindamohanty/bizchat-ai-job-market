from fastapi.testclient import TestClient

from api.main import app


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
