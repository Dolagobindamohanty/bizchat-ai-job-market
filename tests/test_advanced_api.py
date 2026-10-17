from fastapi.testclient import TestClient

from api.main import app


client = TestClient(app)


def test_resume_match_returns_explainable_skill_gap_score():
    response = client.post("/api/match-resume", json={
        "resume_text": "Python and SQL experience building analytics models",
        "target_role": "Data Scientist",
        "years_experience": 3,
    })
    assert response.status_code == 200
    body = response.json()
    assert {"Python", "SQL"}.issubset(body["matched_skills"])
    assert body["missing_high_demand_skills"]
    assert 0 <= body["score"] <= 100
    assert body["explanations"]


def test_trends_forecast_is_labeled_deterministic_baseline():
    response = client.get("/api/trends/forecast")
    assert response.status_code == 200
    body = response.json()
    assert body["forecast_method"] == "deterministic baseline (not ML)"
    assert len(body["periods"]) == 2
    assert body["week_over_week"]
    assert body["forecast"]


def test_semantic_search_is_deterministic_and_supports_get_and_post():
    get_response = client.get("/api/search/semantic", params={"q": "Python RAG"})
    post_response = client.post("/api/search/semantic", json={"query": "Python RAG"})
    assert get_response.status_code == post_response.status_code == 200
    assert get_response.json()["items"] == post_response.json()["items"]
    assert "pgvector" in get_response.json()["integration"]
    assert get_response.json()["items"][0]["matched_skills"]


def test_alert_preview_filters_without_attempting_delivery():
    response = client.post("/api/alerts/preview", json={"filters": {"city": "Bengaluru", "skill": "Python"}})
    assert response.status_code == 200
    body = response.json()
    assert body["matched_jobs"] > 0
    assert all(item["location"] == "Bengaluru" for item in body["sample_jobs"])
    assert body["delivery"]["status"] == "preview_only"
    assert "authorized email or Telegram provider" in body["delivery"]["message"]
