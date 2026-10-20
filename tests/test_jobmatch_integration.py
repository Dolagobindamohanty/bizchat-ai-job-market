"""Exercise the real main app, not only the isolated router."""
import io
from pathlib import Path

from docx import Document
from fastapi.testclient import TestClient

import api.main as main
from bizchat.job_classifier import classify_job


def test_main_app_parse_match_and_discovery(monkeypatch):
    monkeypatch.setattr(main, "DATA_PATH", Path(__file__).resolve().parents[1] / "data/raw/jobs-greenhouse-india.csv")
    main.RequestGuardMiddleware._requests.clear()
    client = TestClient(main.app)
    listing = client.get("/api/jobs/bengaluru?page_size=100")
    assert listing.status_code == 200
    assert listing.json()["total"] > 0
    assert all(job["is_tech"] for job in listing.json()["items"])
    parsed = client.post("/api/resume/parse", json={"resume_text": "Private Person\nprivate@example.com\nSoftware Engineer\nPython, SQL, AWS\n2 years experience"})
    assert parsed.status_code == 200
    assert "private" not in str(parsed.json()).lower()
    response = client.post("/api/resume/match", json={"profile": parsed.json()["profile"]})
    assert response.status_code == 200
    assert response.json()["total"] == listing.json()["total"]
    assert response.json()["roles"]


def test_main_app_stream_limits_and_cors():
    main.RequestGuardMiddleware._requests.clear()
    client = TestClient(main.app)
    # No Content-Length: exercise actual streamed byte counting, not just headers.
    chunks = (b"x" * 65_536 for _ in range(5))
    response = client.post("/api/resume/parse", content=chunks, headers={"Content-Type": "application/json", "Origin": "https://bizchat-ai-job-market.vercel.app"})
    assert response.status_code == 413
    assert response.headers["access-control-allow-origin"] == "https://bizchat-ai-job-market.vercel.app"


def test_main_app_accepts_upload_beyond_legacy_json_cap():
    main.RequestGuardMiddleware._requests.clear()
    output = io.BytesIO()
    document = Document()
    document.add_paragraph("Python Software Engineer")
    document.save(output)
    # A valid DOCX plus a ZIP comment is sufficient to exceed the old 256 KiB cap.
    import zipfile
    archive = io.BytesIO(output.getvalue())
    with zipfile.ZipFile(archive, "a", compression=zipfile.ZIP_STORED) as z:
        z.writestr("custom/padding.bin", bytes(range(256)) * 1100)
    response = TestClient(main.app).post("/api/resume/upload", files={"file": ("resume.docx", archive.getvalue())})
    assert response.status_code == 200
    assert "Python" in response.json()["profile"]["skills"]


def test_job_skills_enrich_legacy_ai_only_taxonomy():
    job = classify_job({"title": "Software Engineer", "skills": ["Python"], "description": "Required: Java, PostgreSQL, React and Docker."})
    assert {"Python", "Java", "PostgreSQL", "React", "Docker"} <= set(job["skills"])
    assert classify_job({"title": "Data Scientist"})["is_ai"]


def test_real_multisource_snapshot_works_in_both_dashboards(monkeypatch):
    monkeypatch.setattr(main, "DATA_PATH", Path(__file__).resolve().parents[1] / "data/raw/jobs-india.csv")
    main.RequestGuardMiddleware._requests.clear()
    client = TestClient(main.app)
    assert client.get("/api/jobs?page_size=100").status_code == 200
    assert client.get("/api/overview").status_code == 200
    response = client.get("/api/jobs/bengaluru?page_size=100")
    assert response.status_code == 200
    listing = response.json()
    assert set(listing["facets"]["sources"]) == {"greenhouse", "lever", "ashby"}
    for source in listing["facets"]["sources"]:
        filtered = client.get("/api/jobs/bengaluru", params={"source": source}).json()
        assert filtered["total"] > 0
        assert all(job["source"] == source for job in filtered["items"])
    profile = {"skills": ["Python", "SQL"], "titles": ["Software Engineer"]}
    matches = client.post("/api/resume/match", json={"profile": profile, "limit": 100}).json()
    assert {job["source"] for job in matches["items"]} == {"greenhouse", "lever", "ashby"}


def test_existing_production_dataset_setting_migrates_without_overriding_custom_data():
    root = Path(__file__).resolve().parents[1]
    assert main.resolve_data_path(str(root / "data/raw/jobs-greenhouse-india.csv")) == root / "data/raw/jobs-india.csv"
    assert main.resolve_data_path(None) == root / "data/raw/jobs.csv"
    custom = root / "data/raw/custom.csv"
    assert main.resolve_data_path(str(custom)) == custom
