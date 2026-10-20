import io
import sys
import types
import zipfile

import pytest

from bizchat.job_classifier import classify_job, is_bengaluru
from bizchat.matcher import match_jobs
from bizchat.resume_parser import MAX_FILE_BYTES, extract_resume_file, parse_resume
from bizchat.skills import canonicalize_skills, extract_skills


def job(identifier="1", **updates):
    return {"job_id": identifier, "title": "Software Engineer", "company": "Demo",
            "location": "Bengaluru", "skills": ["Python", "SQL"],
            "experience_min": 4, **updates}


def test_skill_aliases_and_punctuation_boundaries():
    assert {"JavaScript", "C++", "C#", ".NET", "scikit-learn", "Node.js"} <= set(
        extract_skills("JavaScript, C++, C#, .NET, sklearn, nodejs"))
    assert not {"Java", "SQL", "RAG", "Git", "JavaScript"} & set(
        extract_skills("NoSQL, JavaScripted, paragraph, github, JavaBeans"))
    assert "C++" not in extract_skills("abc++ c+++ c++17")
    assert canonicalize_skills(["Go", "python", "PYTHON", "sklearn"]) == ["Python", "scikit-learn", "Go"]


@pytest.mark.parametrize("location", ["Bengaluru", "BANGALORE, Karnataka", "BLR / Hybrid", "Remote - Bengaluru", "Bangalore/Hyderabad"])
def test_city_aliases(location):
    assert is_bengaluru(location)


@pytest.mark.parametrize("location", ["India remote", "Remote", "Bangaloreville", "XBLR", "Bengalurux", "", None])
def test_city_boundaries_and_remote(location):
    assert not is_bengaluru(location)


@pytest.mark.parametrize("title", ["AI Recruiter", "Marketing Manager", "Technical Recruiter", "Sales Engineer", "Talent Acquisition"])
def test_non_tech_titles_are_not_promoted_by_blurb(title):
    result = classify_job(job(title=title, description="We build AI software with Python and ML engineers."))
    assert not result["is_tech"]
    assert not result["is_ai"]


def test_title_classification_is_enriched_without_mutation():
    original = job(title="Senior Machine Learning Engineer")
    result = classify_job(original)
    assert result["is_tech"] and result["is_ai"]
    assert result["role_category"] == "AI/ML Engineering"
    assert "is_tech" not in original
    assert classify_job(job(title="Data Scientist"))["role_category"] == "Data Science"


@pytest.mark.parametrize("title", ["Director Data Science", "Engineering Manager - Backend", "Staff Engineer, API Platform", "Member of Technical Staff - Enterprise Platform", "Performance Engineer, Inference"])
def test_technical_title_variants_from_additional_public_boards(title):
    assert classify_job(job(title=title))["is_tech"]


def test_privacy_profile_contains_only_reconstructed_facts():
    text = """Jane Unique Smith
Contact: jane.private@example.com | +91 98765 43210
https://linkedin.com/in/jane-private
Address: 34 Secret Road, Bengaluru 560001
Software Engineer at Secret Employer
4 years of professional experience using Python and SQL
Education
B.Tech, Secret University, 2018
Projects
Private Client Atlas: built with Python and FastAPI for Dr. Private Person
"""
    profile = parse_resume(text)
    assert profile["years_experience"] == 4
    assert profile["titles"] == ["Software Engineering"]
    assert profile["education"] == ["Bachelor's degree"]
    assert profile["projects"] == ["Project technologies: Python, FastAPI"]
    outbound = str(profile).lower()
    for personal in ["jane", "smith", "98765", "example.com", "linkedin", "secret", "560001", "private", "atlas"]:
        assert personal not in outbound
    assert "text" not in profile
    assert "Python" in profile["redacted_text"]


def test_empty_and_ambiguous_resume_does_not_invent_experience():
    assert parse_resume("") == {"skills": [], "titles": [], "years_experience": None,
                                 "education": [], "projects": [], "redacted_text": ""}
    assert parse_resume("Graduated 2018. Python projects for 2 years.")["years_experience"] is None
    with pytest.raises(ValueError):
        parse_resume("x" * 200_001)


def test_baseline_scoring_is_explainable_and_renormalized():
    profile = {"skills": ["Python"], "titles": ["Software Engineer"], "years_experience": 2}
    item = match_jobs(profile, [job()])["items"][0]
    assert item["score"] == pytest.approx(56.67)
    assert item["matched_skills"] == ["Python"]
    assert item["missing_skills"] == ["SQL"]
    assert item["breakdown"]["experience"]["score"] == 50
    assert item["breakdown"]["semantic"]["score"] is None
    assert "no embedding" in item["reason"]
    assert "not a hiring probability" in item["reason"]


def test_semantic_alignment_survives_filtered_input_jobs():
    jobs = [job("outside", location="Pune"), job("low"), job("excluded", title="Recruiter"), job("high")]
    result = match_jobs({}, jobs, [1, 0, 1, 1])
    assert result["total"] == 2
    assert [item["job_id"] for item in result["items"]] == ["high", "low"]
    assert result["items"][0]["score"] == 25
    assert result["items"][0]["breakdown"]["semantic"]["weight"] == .25


@pytest.mark.parametrize("scores", [[1, 0], [float("nan")], [-.1], [1.1], [True]])
def test_invalid_semantic_scores_fail_explicitly(scores):
    with pytest.raises(ValueError):
        match_jobs({}, [job()], scores)


def test_empty_profile_and_job_unknowns_are_honest():
    item = match_jobs({}, [job()])["items"][0]
    assert item["score"] == 0
    assert item["breakdown"]["skill"]["status"] == "unknown"
    assert "Profile role unknown" in item["reason"]
    item = match_jobs({"skills": ["Python"]}, [job(skills=[], description="", experience_min=None)])["items"][0]
    assert item["breakdown"]["skill"]["score"] is None
    assert item["breakdown"]["experience"]["score"] is None
    assert match_jobs({}, []) == {"items": [], "roles": [], "gaps": [], "total": 0}


def test_explicit_profile_corrections_control_role_and_experience():
    jobs = [job("software"), job("data", title="Data Engineer")]
    profile = {"skills": ["Python", "SQL"], "titles": ["Data Engineer"], "years_experience": 4,
               "redacted_text": "Software Engineer; 1 year experience"}
    result = match_jobs(profile, jobs)
    assert result["items"][0]["job_id"] == "data"
    assert result["items"][0]["score"] == 100
    assert result["items"][0]["breakdown"]["experience"]["score"] == 100


def test_gaps_use_top_twenty_independent_of_display_limit():
    result = match_jobs({"skills": ["Python"]}, [job(str(i)) for i in range(25)], limit=1)
    assert result["total"] == 25 and len(result["items"]) == 1
    assert result["gaps"][0]["skill"] == "SQL"
    assert result["gaps"][0]["count"] == result["gaps"][0]["total"] == 20
    assert "not found in profile" in result["gaps"][0]["advice"]
    assert result["roles"][0]["jobs"] == 25


@pytest.mark.parametrize("data,name", [(b"", "a.pdf"), (b"plain", "a.txt"), (b"plain", "a.pdf"), (b"badzip", "a.docx")])
def test_invalid_files_fail_safely(data, name):
    with pytest.raises(ValueError) as error:
        extract_resume_file(data, name)
    assert "Traceback" not in str(error.value)


def test_file_size_limit():
    with pytest.raises(ValueError, match="5 MiB"):
        extract_resume_file(b"x" * (MAX_FILE_BYTES + 1), "a.pdf")


def test_docx_zip_bomb_is_rejected_before_import():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "a" * 1_000_000)
        archive.writestr("[Content_Types].xml", "types")
    with pytest.raises(ValueError, match="expansion limits"):
        extract_resume_file(stream.getvalue(), "a.docx")


def test_pdf_encryption_scanning_success_and_parser_errors(monkeypatch):
    reader = types.SimpleNamespace(is_encrypted=True, pages=[])
    monkeypatch.setitem(sys.modules, "pypdf", types.SimpleNamespace(PdfReader=lambda _: reader))
    with pytest.raises(ValueError, match="Encrypted"):
        extract_resume_file(b"%PDF-stub", "a.pdf")
    reader.is_encrypted = False
    reader.pages = [types.SimpleNamespace(extract_text=lambda: "")]
    with pytest.raises(ValueError, match="Scanned"):
        extract_resume_file(b"%PDF-stub", "a.pdf")
    reader.pages = [types.SimpleNamespace(extract_text=lambda: "Python SQL")]
    assert extract_resume_file(b"%PDF-stub", "a.PDF") == "Python SQL"
    def broken_reader(_):
        raise ValueError("PRIVATE PARSER CONTENT")
    monkeypatch.setitem(sys.modules, "pypdf", types.SimpleNamespace(PdfReader=broken_reader))
    with pytest.raises(ValueError, match="could not be read") as error:
        extract_resume_file(b"%PDF-stub", "a.pdf")
    assert "PRIVATE" not in str(error.value)


def test_docx_extracts_paragraphs_and_table_cells(monkeypatch):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("word/document.xml", "document")
        archive.writestr("[Content_Types].xml", "types")
    document = types.SimpleNamespace(paragraphs=[types.SimpleNamespace(text="Python")], tables=[
        types.SimpleNamespace(rows=[types.SimpleNamespace(cells=[types.SimpleNamespace(text="SQL")])])])
    monkeypatch.setitem(sys.modules, "docx", types.SimpleNamespace(Document=lambda _: document))
    assert extract_resume_file(stream.getvalue(), "a.docx") == "Python\nSQL"
