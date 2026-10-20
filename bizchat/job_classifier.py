"""Conservative title-based classification, independent of company blurbs."""

from __future__ import annotations

import re

from bizchat.skills import canonicalize_skills, extract_skills


def is_bengaluru(location: str) -> bool:
    return bool(re.search(r"(?<!\w)(?:bengaluru|bangalore|blr)(?!\w)", str(location or ""), re.I))


def classify_job(job: dict) -> dict:
    result = dict(job)
    title = str(job.get("title") or "").casefold()
    excluded = re.search(r"\b(recruit\w*|talent|marketing|sales|business development|human resources|hr|account executive)\b", title)
    categories = (
        ("AI/ML Engineering", r"\b(?:ai|ml|ai/ml|machine learning|deep learning|genai|generative ai|llm|nlp|computer vision)\b.*\b(?:engineer|engineering|developer|scientist|researcher|architect)\b"),
        ("Data Science", r"\bdata (?:scientist|science)\b"),
        ("Data Engineering", r"\b(?:data|analytics) (?:engineer|architect)\b"),
        ("Data Analytics", r"\b(?:data|business intelligence|bi) analyst\b|\banalytics engineer\b"),
        ("Platform/DevOps", r"\b(?:devops|sre|site reliability|cloud|platform|infrastructure|mlops)\b.*\b(?:engineer|developer|architect)\b|\b(?:engineer|engineering manager|member (?:of )?technical staff)\b.*\b(?:platform|infrastructure)\b|\b(?:devops|sre|mlops)\b"),
        ("Security Engineering", r"\b(?:security|cybersecurity) (?:engineer|analyst|architect)\b"),
        ("QA Engineering", r"\b(?:qa|quality assurance|test|sdet)\b.*\b(?:engineer|developer|analyst)\b|\bsdet\b"),
        ("Software Engineering", r"\b(?:software|backend|back.end|frontend|front.end|full.stack|web|mobile|android|ios|python|java|javascript|c\+\+|\.net|performance|systems|firmware)\b.*\b(?:engineer|developer|architect)\b|\b(?:software developer|programmer|engineering manager|staff engineer|principal engineer|member (?:of )?technical staff)\b"),
    )
    role = "Unknown"
    if not excluded:
        role = next((name for name, pattern in categories if re.search(pattern, title)), "Unknown")
    tech = role != "Unknown"
    ai = tech and (role == "Data Science" or bool(re.search(r"\b(?:ai|ml|machine learning|deep learning|genai|generative ai|llm|nlp|computer vision|mlops)\b", title)))
    result.update(is_tech=tech, is_ai=ai, role_category=role)
    # The legacy analytics taxonomy focuses on AI; enrich rather than trusting its
    # partial skill list as a complete inventory of software-job requirements.
    result["skills"] = canonicalize_skills(
        canonicalize_skills(job.get("skills")) + extract_skills(str(job.get("title") or "") + " " + str(job.get("description") or "")[:50_000])
    )
    return result
