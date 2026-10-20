"""Local resume extraction. Outbound text is reconstructed from allowlisted facts.

Reconstruction deliberately drops personal headers, employers, institutions and
free-form prose: regex-only redaction cannot reliably identify arbitrary names.
"""

from __future__ import annotations

import io
import re
import zipfile

from bizchat.job_classifier import classify_job
from bizchat.skills import extract_skills


MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_EXPANDED_BYTES = 25 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 1000
MAX_TEXT_CHARS = 200_000


class _SafeExtractionError(ValueError):
    pass


def parse_resume(text: str) -> dict:
    if not isinstance(text, str):
        raise ValueError("Resume text must be a string.")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError("Resume text is too long.")
    skills = extract_skills(text)
    titles = []
    for line in text.splitlines():
        role = classify_job({"title": line})["role_category"]
        if role != "Unknown" and role not in titles:
            titles.append(role)
    # Require explicit experience language; graduation dates are not experience.
    years = re.findall(r"\b(\d{1,2}(?:\.\d)?)\s*\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:professional\s+|work\s+|industry\s+|relevant\s+)?experience\b", text, re.I)
    experience = max((float(value) for value in years if float(value) <= 60), default=None)
    degrees = (
        ("Doctorate", r"\b(?:ph\.?d\.?|doctorate)\b"),
        ("Master's degree", r"\b(?:m\.?tech\.?|m\.?sc\.?|masters?|m\.s\.)\b"),
        ("Bachelor's degree", r"\b(?:b\.?tech\.?|b\.?sc\.?|bachelor(?:'s)?|b\.e\.)\b"),
    )
    education = [label for label, pattern in degrees if re.search(pattern, text, re.I)]
    projects = []
    project_section = re.search(r"(?ims)^\s*(?:personal\s+|selected\s+)?projects?\s*:?\s*\n(.*?)(?=^\s*(?:education|experience|employment|certifications?|skills)\s*:?\s*$|\Z)", text)
    if project_section:
        project_skills = extract_skills(project_section.group(1))
        if project_skills:
            projects = ["Project technologies: " + ", ".join(project_skills)]
    safe = []
    if skills:
        safe.append("Skills: " + ", ".join(skills))
    if titles:
        safe.append("Roles: " + ", ".join(titles))
    if experience is not None:
        safe.append(f"Explicit experience: {experience:g} years")
    if education:
        safe.append("Education: " + ", ".join(education))
    safe.extend(projects)
    return {"skills": skills, "titles": titles, "years_experience": experience,
            "education": education, "projects": projects, "redacted_text": "\n".join(safe)}


def extract_resume_file(data: bytes, filename: str) -> str:
    """Extract PDF/DOCX, raising safe ValueErrors without parser internals."""
    if not isinstance(data, bytes) or not data:
        raise ValueError("Resume file is empty or invalid.")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("Resume file exceeds the 5 MiB limit.")
    extension = str(filename).rsplit(".", 1)[-1].lower()
    if extension not in {"pdf", "docx"}:
        raise ValueError("Unsupported resume file. Use PDF or DOCX.")
    try:
        if extension == "pdf":
            if not data.startswith(b"%PDF-"):
                raise _SafeExtractionError("Invalid PDF file.")
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise _SafeExtractionError("Encrypted PDF files are not supported.")
            if len(reader.pages) > 100:
                raise _SafeExtractionError("Resume PDF has too many pages.")
            chunks = []
            size = 0
            for page in reader.pages:
                chunk = page.extract_text() or ""
                size += len(chunk)
                if size > MAX_TEXT_CHARS:
                    raise _SafeExtractionError("Extracted resume text is too long.")
                chunks.append(chunk)
            text = "\n".join(chunks)
        else:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                if (len(entries) > MAX_ARCHIVE_ENTRIES
                        or sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES
                        or any(entry.flag_bits & 1 or entry.file_size / max(entry.compress_size, 1) > 200 for entry in entries)):
                    raise _SafeExtractionError("DOCX archive exceeds safe expansion limits or is encrypted.")
                if "word/document.xml" not in archive.namelist() or "[Content_Types].xml" not in archive.namelist():
                    raise _SafeExtractionError("Invalid DOCX file.")
            from docx import Document

            document = Document(io.BytesIO(data))
            text = "\n".join([p.text for p in document.paragraphs] +
                             [cell.text for table in document.tables for row in table.rows for cell in row.cells])
    except ImportError:
        raise ValueError("Resume file extraction dependency is unavailable.") from None
    except _SafeExtractionError:
        raise
    except Exception:
        raise ValueError("Resume file could not be read. Use a valid, unencrypted PDF or DOCX.") from None
    if not text.strip():
        raise ValueError("No readable text found. Scanned resumes require OCR before upload.")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError("Extracted resume text is too long.")
    return text.strip()
