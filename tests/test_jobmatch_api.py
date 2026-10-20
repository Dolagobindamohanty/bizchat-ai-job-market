import io

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import jobmatch


@pytest.fixture
def client():
    data = pd.DataFrame([
        {"job_id": "1", "title": "Software Engineer", "company": "Acme", "location": "Bengaluru", "skills": ["Python", "SQL"], "description": "Python software", "experience_min": 2},
        {"job_id": "2", "title": "AI Engineer", "company": "ModelCo", "location": "Bangalore", "skills": ["Python"], "description": "AI", "experience_min": 0},
        {"job_id": "3", "title": "AI Engineer", "company": "Other", "location": "Delhi", "skills": ["Python"], "description": "AI", "experience_min": 0},
        {"job_id": "4", "title": "Sales Manager", "company": "Acme", "location": "Bengaluru", "skills": [], "description": "AI sales", "experience_min": 0},
    ])
    app = FastAPI()
    app.include_router(jobmatch.create_jobmatch_router(lambda: data))
    return TestClient(app)


def test_local_parse_match_discovery_and_stateless_results(client, monkeypatch):
    monkeypatch.setattr(jobmatch, "semantic_scores", lambda *args, **kwargs: pytest.fail("local mode must not call provider"))
    parsed = client.post("/api/resume/parse", json={"resume_text": "Jane Doe jane@example.com\nSoftware Engineer\nPython 3 years experience"})
    assert parsed.status_code == 200
    profile = parsed.json()["profile"]
    assert "jane@example.com" not in str(profile)
    result = client.post("/api/resume/match", json={"profile": profile}).json()
    assert result["total"] == 2
    assert result["roles"]
    assert "no external" in result["integration"]
    assert {item["job_id"] for item in result["items"]} == {"1", "2"}
    assert all("reason" in item and "breakdown" in item for item in result["items"])
    assert client.get("/api/jobs/bengaluru", params={"page_size": 1}).json()["total"] == 2
    assert client.get("/api/jobs/bengaluru", params={"company": "Acme", "skills": "SQL"}).json()["total"] == 1
    assert client.get("/api/jobs/ai").json()["total"] == 1
    assert client.get("/api/jobs/ai", params={"q": "ModelCo"}).json()["total"] == 1
    for path in ("roles", "gaps"):
        assert client.get(f"/api/resume/{path}").json()["stateless"] is True


@pytest.mark.parametrize("body", [
    {"profile": {}, "unknown": True}, {"profile": {"email": "bad"}},
    {"profile": {"years_experience": -1}}, {"profile": {"skills": ["x"] * 101}},
    {"profile": {}, "limit": 101},
])
def test_strict_bounded_requests(client, body):
    assert client.post("/api/resume/match", json=body).status_code == 422


def test_parse_empty_and_oversize(client):
    for text in ("", "  ", "x" * 50_001):
        assert client.post("/api/resume/parse", json={"resume_text": text}).status_code == 422
    assert client.get("/api/jobs/bengaluru?page_size=101").status_code == 422


def test_external_consent_and_unavailable(client, monkeypatch):
    for kind in ("EMBEDDING", "LLM"):
        for part in ("BASE_URL", "API_KEY", "MODEL"):
            monkeypatch.delenv(f"JOBMATCH_{kind}_{part}", raising=False)
    local = client.post("/api/resume/match", json={"profile": {}, "use_embeddings": True})
    assert local.status_code == 200
    assert "no external" in local.json()["integration"]
    assert client.post("/api/resume/match", json={"profile": {}, "use_embeddings": True, "consent_external": True}).status_code == 503
    suggestions = client.post("/api/resume/suggestions", json={"profile": {}})
    assert suggestions.status_code == 200
    assert "no external" in suggestions.json()["integration"]
    assert client.post("/api/resume/suggestions", json={"profile": {}, "consent_external": True}).status_code == 503


def test_semantic_alignment_and_suggestion_facts_computed_on_server(client, monkeypatch):
    monkeypatch.setattr(jobmatch, "semantic_scores", lambda profile, jobs, consent: {str(job["job_id"]): .9 for job in jobs})
    result = client.post("/api/resume/match", json={"profile": {"skills": ["Python"]}, "use_embeddings": True, "consent_external": True})
    assert result.status_code == 200
    assert all(item["breakdown"]["semantic"]["score"] == 90 for item in result.json()["items"])
    def suggestions(profile, matched_jobs, **kwargs):
        assert {job["job_id"] for job in matched_jobs} == {"1", "2"}
        return {"suggestions": [], "integration": "test"}
    monkeypatch.setattr(jobmatch, "generate_suggestions", suggestions)
    assert client.post("/api/resume/suggestions", json={"profile": {}, "consent_external": True}).status_code == 200
    assert client.post("/api/resume/suggestions", json={"profile": {}, "gaps": ["fake"]}).status_code == 422


def test_hosted_embedding_metadata_is_not_overwritten_by_local_metadata(client, monkeypatch):
    monkeypatch.setattr(jobmatch, "semantic_scores", lambda profile, jobs, consent: {str(job["job_id"]): .9 for job in jobs})
    result = client.post("/api/resume/match", json={"profile": {}, "use_embeddings": True, "consent_external": True})
    assert result.status_code == 200
    assert result.json()["integration"].startswith("hosted OpenAI-compatible embeddings;")


def test_classified_jobs_are_cached_by_data_version():
    rows = pd.DataFrame([{"job_id": "1", "title": "Software Engineer", "company": "Acme", "location": "Bengaluru", "skills": []}])
    calls = {"load": 0}

    def load():
        calls["load"] += 1
        return rows

    app = FastAPI()
    app.include_router(jobmatch.create_jobmatch_router(load, lambda: ("jobs.csv", 1, 2)))
    cached_client = TestClient(app)
    assert cached_client.get("/api/jobs/bengaluru").status_code == 200
    assert cached_client.get("/api/jobs/ai").status_code == 200
    assert cached_client.get("/api/resume/gaps").status_code == 200
    assert calls["load"] == 1


def test_upload_bounded_in_memory_and_invalid_formats(client, monkeypatch):
    monkeypatch.setattr(jobmatch, "extract_resume_file", lambda data, filename: "Python Software Engineer")
    response = client.post("/api/resume/upload", files={"file": ("resume.pdf", b"%PDF-test", "application/pdf")})
    assert response.status_code == 200
    assert "Python" in response.json()["profile"]["skills"]
    assert client.post("/api/resume/upload", files={"file": ("resume.pdf", b"x" * (5 * 1024 * 1024 + 1))}).status_code == 413
    assert client.post("/api/resume/upload", files={"wrong": ("resume.pdf", b"hello")}).status_code == 422
    assert client.post("/api/resume/upload", files=[("file", ("one.pdf", b"one")), ("file", ("two.pdf", b"two"))]).status_code == 422


def test_real_docx_upload(client):
    from docx import Document
    output = io.BytesIO()
    document = Document()
    document.add_paragraph("Python Software Engineer 3 years experience")
    document.save(output)
    response = client.post("/api/resume/upload", files={"file": ("resume.docx", output.getvalue())})
    assert response.status_code == 200
    assert response.json()["profile"]["years_experience"] == 3
    assert client.post("/api/resume/upload", files={"file": ("resume.txt", b"Python")}).status_code == 422


def test_multisource_listing_and_resume_matching():
    rows = [{"job_id": f"{source}:1", "title": "Software Engineer", "company": company,
             "location": "Bengaluru", "skills": ["Python", "SQL"], "source": source}
            for source, company in (("greenhouse", "OldCo"), ("lever", "Meesho"), ("ashby", "Sarvam AI"))]
    app = FastAPI()
    app.include_router(jobmatch.create_jobmatch_router(lambda: pd.DataFrame(rows)))
    client = TestClient(app)
    listing = client.get("/api/jobs/bengaluru?source=lever&company=Meesho&skills=Python,SQL").json()
    assert listing["total"] == 1
    assert listing["items"][0]["source"] == "lever"
    assert listing["facets"]["sources"] == ["ashby", "greenhouse", "lever"]
    assert client.get("/api/jobs/bengaluru?source=unknown").json()["total"] == 0
    profile = {"skills": ["Python"], "titles": ["Software Engineer"]}
    result = client.post("/api/resume/match", json={"profile": profile}).json()
    assert {item["source"] for item in result["items"]} == {"greenhouse", "lever", "ashby"}
    result = client.post("/api/resume/match", json={"profile": profile, "source": "ashby"}).json()
    assert result["total"] == 1
    assert result["items"][0]["source"] == "ashby"
