"""Explainable match ranking; scores express evidence fit, not hiring odds."""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from bizchat.job_classifier import classify_job, is_bengaluru
from bizchat.skills import canonicalize_skills, extract_skills


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except (ValueError, TypeError):
        return None


def match_jobs(profile: dict, jobs: list[dict], semantic_scores: list[float] | None = None, limit: int = 50) -> dict:
    if semantic_scores is not None and len(semantic_scores) != len(jobs):
        raise ValueError("Semantic scores must align with the input jobs array.")
    if semantic_scores is not None and any(_number(s) is None or float(s) > 1 for s in semantic_scores):
        raise ValueError("Semantic scores must be finite numbers between 0 and 1.")
    known = set(canonicalize_skills(profile.get("skills")))
    titles = profile.get("titles") or []
    if isinstance(titles, str):
        titles = [titles]
    roles = {classify_job({"title": title})["role_category"] for title in titles}
    # Parser's canonical role labels and user-corrected literal titles both work.
    roles.update(title for title in titles if title in {
        "AI/ML Engineering", "Data Science", "Data Engineering", "Data Analytics",
        "Platform/DevOps", "Security Engineering", "QA Engineering", "Software Engineering"})
    roles.discard("Unknown")
    experience = _number(profile.get("years_experience"))
    results = []
    for index, raw_job in enumerate(jobs):
        job = classify_job(raw_job)
        if not job["is_tech"] or not is_bengaluru(job.get("location")):
            continue
        required = canonicalize_skills(job.get("skills"))
        if not required:
            required = extract_skills(str(job.get("title") or "") + " " + str(job.get("description") or ""))
        matched = sorted(known.intersection(required))
        missing = sorted(set(required) - known)
        minimum = _number(job.get("experience_min"))
        values = {
            "skill": len(matched) / len(required) if required and known else None,
            "semantic": float(semantic_scores[index]) if semantic_scores is not None else None,
            "experience": min(1.0, experience / minimum) if experience is not None and minimum is not None and minimum > 0 else
                          (1.0 if experience is not None and minimum == 0 else None),
            "role": float(job["role_category"] in roles) if roles else None,
        }
        # Missing evidence earns no points. Only the optional embedding weight is
        # renormalized away, so unknown profiles cannot receive perfect scores.
        weights = {"skill": .5, "semantic": .25, "experience": .15, "role": .1}
        denominator = 1.0 if semantic_scores is not None else .75
        breakdown = {key: {"score": round(value * 100, 2) if value is not None else None,
                           "weight": round(weights[key] / denominator, 6) if key != "semantic" or semantic_scores is not None else 0,
                           "status": "known" if value is not None else "unknown"}
                     for key, value in values.items()}
        score = round(100 * sum((value or 0) * weights[key] for key, value in values.items()) / denominator, 2)
        method = "embedding-assisted" if semantic_scores is not None else "no embedding; deterministic baseline"
        reason = (f"{len(matched)}/{len(required)} listed skills found in profile. " if required else "Job skills unknown. ")
        if not known:
            reason += "Profile skills unknown. "
        if experience is None or minimum is None:
            reason += "Experience comparison unknown. "
        if not roles:
            reason += "Profile role unknown. "
        reason += f"{method}; evidence-fit score, not a hiring probability."
        results.append({**job, "score": score, "matched_skills": matched,
                        "missing_skills": missing, "breakdown": breakdown, "reason": reason})
    results.sort(key=lambda row: (-row["score"], str(row.get("title", "")), str(row.get("job_id", ""))))
    top = results[:20]
    gap_counts = Counter(skill for job in top for skill in job["missing_skills"])
    grouped = defaultdict(list)
    for job in results:
        grouped[job["role_category"]].append(job["score"])
    role_summary = [{"role": role, "score": round(sum(scores) / len(scores), 2), "jobs": len(scores)} for role, scores in grouped.items()]
    role_summary.sort(key=lambda row: (-row["score"], row["role"]))
    gaps = [{"skill": skill, "count": count, "total": len(top),
             "advice": f"{skill} was not found in profile and appears in {count} of the top {len(top)} jobs. Add supporting experience if applicable, or consider a focused project."}
            for skill, count in sorted(gap_counts.items(), key=lambda pair: (-pair[1], pair[0]))]
    return {"items": results[:max(0, min(int(limit), 500))], "roles": role_summary, "gaps": gaps, "total": len(results)}
