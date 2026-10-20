"""Small synthetic relevance checks, not a claim of real-world model accuracy."""
import pytest

from bizchat.matcher import match_jobs


JOBS = [
    {"job_id": "backend-python", "title": "Backend Engineer", "skills": ["Python", "FastAPI", "PostgreSQL"], "experience_min": 2},
    {"job_id": "backend-data", "title": "Software Engineer", "skills": ["Python", "SQL", "Docker"], "experience_min": 2},
    {"job_id": "web", "title": "Frontend Engineer", "skills": ["JavaScript", "React", "CSS"], "experience_min": 2},
    {"job_id": "web-typescript", "title": "Software Developer", "skills": ["TypeScript", "React", "HTML"], "experience_min": 2},
    {"job_id": "ml", "title": "Machine Learning Engineer", "skills": ["Python", "PyTorch", "Machine Learning"], "experience_min": 2},
    {"job_id": "ml-scientist", "title": "Data Scientist", "skills": ["Python", "Machine Learning", "SQL"], "experience_min": 2},
    {"job_id": "cloud", "title": "DevOps Engineer", "skills": ["AWS", "Terraform", "Kubernetes"], "experience_min": 4},
    {"job_id": "nontech", "title": "Recruiter", "skills": ["Python"], "experience_min": 0},
]


@pytest.mark.parametrize("skills,titles,relevant", [
    (["Python", "FastAPI", "PostgreSQL", "SQL", "Docker"], ["Backend Engineer"], {"backend-python", "backend-data"}),
    (["JavaScript", "TypeScript", "React", "HTML", "CSS"], ["Frontend Engineer"], {"web", "web-typescript"}),
    (["Python", "PyTorch", "Machine Learning", "SQL"], ["Machine Learning Engineer", "Data Scientist"], {"ml", "ml-scientist"}),
])
def test_synthetic_precision_at_two(skills, titles, relevant):
    profile = {"skills": skills, "titles": titles, "years_experience": 2}
    jobs = [{**job, "location": "Bengaluru"} for job in JOBS]
    result = match_jobs(profile, jobs, limit=2)
    ranked = {item["job_id"] for item in result["items"]}
    precision_at_two = len(ranked & relevant) / 2
    assert precision_at_two == 1
    assert "nontech" not in ranked
