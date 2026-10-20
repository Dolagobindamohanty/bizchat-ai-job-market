"""Bounded, stateless job-match request contracts."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
EvidenceText = Annotated[str, StringConstraints(max_length=2000)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ResumeProfile(StrictModel):
    skills: list[ShortText] = Field(default_factory=list, max_length=100)
    titles: list[ShortText] = Field(default_factory=list, max_length=30)
    years_experience: float | None = Field(default=None, ge=0, le=100)
    education: list[EvidenceText] = Field(default_factory=list, max_length=30)
    projects: list[EvidenceText] = Field(default_factory=list, max_length=30)
    redacted_text: str = Field(default="", max_length=50_000)


class ResumeParseRequest(StrictModel):
    resume_text: str = Field(min_length=1, max_length=50_000)


class ResumeMatchRequest(StrictModel):
    profile: ResumeProfile
    source: ShortText = ""
    use_embeddings: bool = False
    consent_external: bool = False
    limit: int = Field(default=50, ge=1, le=100)


class ResumeSuggestionsRequest(StrictModel):
    profile: ResumeProfile
    consent_external: bool = False
    target_role: ShortText = ""


class SuggestedAction(StrictModel):
    skill: ShortText
    action: Literal["learn", "document"]


class ProviderSuggestions(StrictModel):
    suggestions: list[SuggestedAction] = Field(min_length=1, max_length=8)
