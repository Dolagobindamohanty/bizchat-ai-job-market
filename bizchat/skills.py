"""Shared, boundary-aware skill vocabulary for resume matching."""

from __future__ import annotations

import re
from collections.abc import Iterable

from bizchat.analytics import SKILL_ALIASES as ANALYTICS_ALIASES


SKILL_ALIASES = {
    **ANALYTICS_ALIASES,
    "Java": ("java",), "JavaScript": ("javascript", "js"),
    "TypeScript": ("typescript", "ts"), "C++": ("c++", "cpp"),
    "C#": ("c#", "c sharp"), ".NET": (".net", "dotnet"),
    "Go": ("golang", "go language"), "Rust": ("rust",),
    "React": ("react", "reactjs", "react.js"),
    "Node.js": ("node.js", "nodejs"), "Angular": ("angular",),
    "HTML": ("html", "html5"), "CSS": ("css", "css3"),
    "Git": ("git",), "Linux": ("linux",), "GCP": ("gcp", "google cloud"),
    "PostgreSQL": ("postgresql", "postgres"), "MySQL": ("mysql",),
    "MongoDB": ("mongodb",), "Redis": ("redis",),
    "FastAPI": ("fastapi",), "Django": ("django",), "Flask": ("flask",),
    "Spring": ("spring", "spring boot"), "Kafka": ("kafka",),
    "Terraform": ("terraform",), "CI/CD": ("ci/cd", "ci cd"),
    "Tableau": ("tableau",), "Excel": ("excel",),
    "NLP": ("nlp", "natural language processing"),
    "Computer Vision": ("computer vision",), "MLOps": ("mlops",),
    "Generative AI": ("generative ai", "genai", "gen ai"),
}

_SKILL_PATTERNS = tuple(
    (name, re.compile(r"(?<![\w+#.])(?:" + "|".join(re.escape(alias) for alias in aliases) + r")(?![\w+#])", re.I))
    for name, aliases in SKILL_ALIASES.items()
)
_CANONICAL_NAMES = {name.casefold(): name for name in SKILL_ALIASES}


def extract_skills(text: str) -> list[str]:
    """Return canonical known skills; punctuation is part of C++/C#/.NET."""
    text = str(text or "").casefold()
    return [name for name, pattern in _SKILL_PATTERNS if pattern.search(text)]


def canonicalize_skills(values: Iterable[str] | str | None) -> list[str]:
    if isinstance(values, str):
        return extract_skills(values)
    found = set()
    for value in values or ():
        exact = _CANONICAL_NAMES.get(str(value).strip().casefold())
        found.update([exact] if exact else extract_skills(value))
    return [skill for skill in SKILL_ALIASES if skill in found]
