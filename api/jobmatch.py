"""Stateless resume and Bengaluru job discovery endpoints."""
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from api.models import ResumeMatchRequest, ResumeParseRequest, ResumeProfile, ResumeSuggestionsRequest
from api.serialization import json_safe
from bizchat.ai_providers import (LOCAL_EMBEDDING_MODEL, MAX_JOBS, LocalEmbeddingUnavailable, ProviderError,
                                   deterministic_suggestions, generate_suggestions,
                                   local_semantic_scores, semantic_scores)
from bizchat.job_classifier import classify_job, is_bengaluru
from bizchat.matcher import match_jobs
from bizchat.resume_parser import extract_resume_file, parse_resume

MAX_UPLOAD = 5 * 1024 * 1024


async def _memory_upload(request: Request) -> tuple[bytes, str]:
    """Bound multipart in memory: UploadFile would spool raw resumes to disk."""
    from python_multipart import MultipartParser
    from python_multipart.multipart import parse_options_header

    content_type, options = parse_options_header(request.headers.get("content-type", ""))
    boundary = options.get(b"boundary")
    if content_type != b"multipart/form-data" or not boundary or len(boundary) > 200:
        raise HTTPException(422, "Use multipart/form-data with one file field named file.")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_UPLOAD + 64 * 1024:
            raise HTTPException(413, "Resume file exceeds the 5 MiB limit.")
        raw.extend(chunk)
    state = {"parts": 0, "filename": None, "ended": False}
    headers = {}
    header_name, header_value, data = bytearray(), bytearray(), bytearray()

    def part_begin():
        state["parts"] += 1
        if state["parts"] > 1:
            raise ValueError()

    def header_field(value, start, end):
        header_name.extend(value[start:end])

    def header_data(value, start, end):
        header_value.extend(value[start:end])

    def header_end():
        headers[bytes(header_name).lower()] = bytes(header_value)
        header_name.clear()
        header_value.clear()

    def headers_finished():
        _, disposition = parse_options_header(headers.get(b"content-disposition", b""))
        if disposition.get(b"name") != b"file" or not disposition.get(b"filename"):
            raise ValueError()
        state["filename"] = disposition[b"filename"].decode("utf-8", errors="replace")[:255]

    def part_data(value, start, end):
        if len(data) + end - start > MAX_UPLOAD:
            raise HTTPException(413, "Resume file exceeds the 5 MiB limit.")
        data.extend(value[start:end])

    def ended():
        state["ended"] = True

    try:
        parser = MultipartParser(boundary, {"on_part_begin": part_begin, "on_header_field": header_field,
            "on_header_value": header_data, "on_header_end": header_end,
            "on_headers_finished": headers_finished, "on_part_data": part_data, "on_end": ended})
        parser.write(bytes(raw))
        parser.finalize()
        if not state["ended"] or not state["filename"] or not data:
            raise ValueError()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, "Invalid multipart resume upload.") from None
    return bytes(data), state["filename"]


def create_jobmatch_router(load_jobs: Callable[[], pd.DataFrame]) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["jobmatch"])

    def jobs() -> list[dict]:
        records = json_safe(load_jobs().to_dict(orient="records"))
        result = []
        for record in records:
            record["job_id"] = str(record.get("job_id", ""))
            result.append(classify_job(record))
        return result

    def bengaluru_jobs() -> list[dict]:
        return [job for job in jobs() if job["is_tech"] and is_bengaluru(job.get("location"))]

    def listing(ai: bool, page: int, page_size: int, q: str, role: str, company: str, skills: str, source: str = "") -> dict:
        items = bengaluru_jobs()
        if ai:
            items = [job for job in items if job["is_ai"]]
        facets = {"sources": sorted({str(job.get("source", "")) for job in items if job.get("source")}),
                  "companies": sorted({str(job.get("company", "")) for job in items if job.get("company")}),
                  "roles": sorted({str(job["role_category"]) for job in items})}
        if source.strip():
            items = [job for job in items if str(job.get("source", "")).casefold() == source.strip().casefold()]
        for value, fields in ((q, ("title", "company", "skills", "role_category", "description")),
                              (role, ("title", "role_category")), (company, ("company",))):
            if value.strip():
                items = [job for job in items if value.casefold().strip() in " ".join(str(job.get(field, "")) for field in fields).casefold()]
        required = [skill.strip().casefold() for skill in skills.split(",") if skill.strip()]
        if required:
            items = [job for job in items if all(skill in {str(s).casefold() for s in job.get("skills", [])} for skill in required)]
        items.sort(key=lambda job: (str(job.get("title", "")), job["job_id"]))
        return {"items": items[(page - 1) * page_size:page * page_size], "total": len(items), "page": page, "page_size": page_size, "facets": facets}

    @router.get("/jobs/bengaluru")
    def bengaluru(page: Annotated[int, Query(ge=1, le=100_000)] = 1,
                  page_size: Annotated[int, Query(ge=1, le=100)] = 20,
                  q: Annotated[str, Query(max_length=200)] = "",
                  role: Annotated[str, Query(max_length=200)] = "",
                  company: Annotated[str, Query(max_length=200)] = "",
                  skills: Annotated[str, Query(max_length=500)] = "",
                  source: Annotated[str, Query(max_length=100)] = ""):
        return listing(False, page, page_size, q, role, company, skills, source)

    @router.get("/jobs/ai")
    def ai_jobs(page: Annotated[int, Query(ge=1, le=100_000)] = 1,
                page_size: Annotated[int, Query(ge=1, le=100)] = 20,
                q: Annotated[str, Query(max_length=200)] = "",
                role: Annotated[str, Query(max_length=200)] = "",
                company: Annotated[str, Query(max_length=200)] = "",
                skills: Annotated[str, Query(max_length=500)] = "",
                source: Annotated[str, Query(max_length=100)] = ""):
        return listing(True, page, page_size, q, role, company, skills, source)

    @router.post("/resume/parse")
    def parse(payload: ResumeParseRequest):
        if not payload.resume_text.strip():
            raise HTTPException(422, "Resume text must not be blank.")
        return {"profile": ResumeProfile.model_validate(parse_resume(payload.resume_text)).model_dump()}

    @router.post("/resume/upload")
    async def upload(request: Request):
        data, filename = await _memory_upload(request)
        try:
            text = await run_in_threadpool(extract_resume_file, data, filename)
            profile = await run_in_threadpool(parse_resume, text)
            return {"profile": ResumeProfile.model_validate(profile).model_dump()}
        except ValueError:
            raise HTTPException(422, "Resume could not be extracted. Use a readable, unencrypted PDF or DOCX within the supported limits.") from None

    @router.post("/resume/match")
    def match(payload: ResumeMatchRequest):
        profile = payload.profile.model_dump()
        corpus = bengaluru_jobs()
        if payload.source:
            corpus = [job for job in corpus if str(job.get("source", "")).casefold() == payload.source.casefold()]
        integration = "local deterministic matching; no external AI call"
        scores = None
        if payload.use_embeddings:
            # Rank locally first to bound hosted cost on large public datasets.
            corpus = match_jobs(profile, corpus, limit=MAX_JOBS)["items"]
            # ATS numeric IDs are not globally unique across company boards.
            # Map vectors by this request's ordinal without changing public IDs.
            embedding_jobs = [{**job, "job_id": str(index)} for index, job in enumerate(corpus)]
            try:
                if payload.consent_external:
                    mapped = semantic_scores(profile, embedding_jobs, consent=True)
                    scores = [mapped[str(index)] for index in range(len(corpus))]
                    integration = f"hosted OpenAI-compatible embeddings; consented, top {len(corpus)} local candidates (maximum {MAX_JOBS})"
                else:
                    mapped = local_semantic_scores(profile, embedding_jobs)
                    scores = [mapped[str(index)] for index in range(len(corpus))]
                integration = f"local {LOCAL_EMBEDDING_MODEL} embeddings; no external AI call"
            except LocalEmbeddingUnavailable:
                # The optional package/model may be absent in small deployments.
                # Continue with the explainable deterministic matcher rather than failing.
                integration = "local deterministic matching fallback; MiniLM unavailable; no external AI call"
            except ProviderError as exc:
                raise HTTPException(exc.status_code, str(exc)) from None
        return json_safe({**match_jobs(profile, corpus, semantic_scores=scores, limit=payload.limit), "integration": integration})

    @router.get("/resume/roles")
    @router.get("/resume/gaps")
    def stateless_results(skills: Annotated[str, Query(max_length=500)] = "",
                          titles: Annotated[str, Query(max_length=500)] = "",
                          years_experience: Annotated[float | None, Query(ge=0, le=100)] = None):
        profile = {"skills": skills.split(","), "titles": titles.split(","), "years_experience": years_experience}
        result = match_jobs(profile, bengaluru_jobs())
        return {"stateless": True, "roles": result["roles"], "gaps": result["gaps"], "total": result["total"],
                "endpoint": "/api/resume/match",
                "message": "Computed from these query skills and titles only. No previous resume is stored. Prefer POST /api/resume/match to keep profile information out of URL history."}

    @router.post("/resume/suggestions")
    def suggestions(payload: ResumeSuggestionsRequest):
        profile = payload.profile.model_dump()
        corpus = bengaluru_jobs()
        if payload.target_role.strip():
            target = classify_job({"title": payload.target_role})["role_category"]
            corpus = [job for job in corpus if job["role_category"].casefold() == payload.target_role.strip().casefold()
                      or (target != "Unknown" and job["role_category"] == target)
                      or payload.target_role.casefold() in str(job.get("title", "")).casefold()]
        matched = match_jobs(profile, corpus, limit=50)["items"]
        try:
            if not payload.consent_external:
                return deterministic_suggestions(profile, matched, target_role=payload.target_role)
            return generate_suggestions(profile, matched, target_role=payload.target_role, consent=True)
        except ProviderError as exc:
            raise HTTPException(exc.status_code, str(exc)) from None

    return router
