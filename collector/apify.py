"""Opt-in Apify actor adapters for third-party job-market collection.

Apify actors are treated as observed third-party sources, not official employer
APIs. This module runs only from the collector CLI or an external scheduler;
the web API never invokes it. Each configured actor is isolated so one failed
portal does not silently replace the other sources.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from collector.providers import ProviderError, _record, _strip_html


APIFY_API = "https://api.apify.com/v2"
MAX_ACTORS = 4
MAX_ITEMS = 5_000
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
APIFY_ACTORS = {
    "naukri": "valig/naukri-jobs-scraper",
    "linkedin": "valig/linkedin-jobs-scraper",
    "linkedin-rich": "curious_coder/linkedin-jobs-scraper",
    "indeed": "valig/indeed-jobs-scraper",
}
SOURCES = set(APIFY_ACTORS)


class ApifyConfigError(ValueError):
    """The explicit Apify actor configuration is invalid."""


JsonRunner = Callable[[str, dict[str, Any], str, float], list[dict[str, Any]]]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return str(value).strip()
    return ""


def _first(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, "", []):
            return value
    return None


def _date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        try:
            seconds = value / 1000 if value > 10_000_000_000 else value
            return datetime.fromtimestamp(seconds, timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = _text(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        match = re.search(r"\b(20\d{2})[-/]([01]\d)[-/]([0-3]\d)\b", text)
        return "-".join(match.groups()) if match else None


def _url(value: Any) -> str:
    text = _text(value)
    try:
        parsed = urlsplit(text)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError
        parsed.port
    except ValueError:
        return ""
    return text


def _description(item: dict[str, Any]) -> str | None:
    value = _first(item, "description", "descriptionText", "description_text", "jobDescription", "job_description", "content")
    if isinstance(value, dict):
        value = _first(value, "text", "html", "value")
    text = _text(value)
    return _strip_html(unescape(text)) if text else None


def _location(item: dict[str, Any]) -> str:
    value = _first(item, "location", "locations", "jobLocation", "job_location", "city")
    if isinstance(value, list):
        values = []
        for part in value:
            if isinstance(part, dict):
                part = _first(part, "name", "city", "location", "displayName", "display_name")
            if _text(part):
                values.append(_text(part))
        return "; ".join(dict.fromkeys(values))
    if isinstance(value, dict):
        value = _first(value, "name", "city", "displayName", "display_name", "address")
    return _text(value) or "Unknown"


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _salary(item: dict[str, Any]) -> tuple[float | None, float | None]:
    minimum = _number(_first(item, "salary_min_lpa", "salaryMinLpa", "min_salary_lpa"))
    maximum = _number(_first(item, "salary_max_lpa", "salaryMaxLpa", "max_salary_lpa"))
    salary = _first(item, "salary", "salaryRange", "salary_range")
    if (minimum is None or maximum is None) and isinstance(salary, dict):
        minimum = minimum if minimum is not None else _number(_first(salary, "min", "minimum", "minLpa", "min_lpa"))
        maximum = maximum if maximum is not None else _number(_first(salary, "max", "maximum", "maxLpa", "max_lpa"))
    if minimum is None or maximum is None:
        text = _text(salary)
        if re.search(r"(?:lpa|lakhs?|lac)", text, re.I):
            values = [float(value) for value in re.findall(r"\d+(?:\.\d+)?", text)]
            if values:
                minimum = minimum if minimum is not None else values[0]
                maximum = maximum if maximum is not None else values[-1]
    return minimum, maximum


def _remote(item: dict[str, Any], description: str | None) -> str | None:
    value = _text(_first(item, "work_mode", "workMode", "remote", "remoteType", "workplaceType", "workplace_type"))
    text = f"{value} {description or ''}".casefold()
    if "hybrid" in text:
        return "Hybrid"
    if "remote" in text or "work from home" in text:
        return "Remote"
    if any(term in text for term in ("on-site", "onsite", "office", "in office")):
        return "On-site"
    return None


def _matches(location: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    return any(re.search(r"(?<!\w)" + re.escape(keyword) + r"(?!\w)", location, re.I) for keyword in keywords)


def _stable_id(source: str, item: dict[str, Any], source_url: str, title: str, company: str, location: str, posted: str | None) -> str:
    native = _first(item, "id", "jobId", "job_id", "jobID", "refNum", "refnum", "key", "slug")
    identity = _text(native) or source_url
    if not identity:
        identity = hashlib.sha256("|".join((title.casefold(), company.casefold(), location.casefold(), posted or "")).encode()).hexdigest()[:24]
    return f"{source}:{identity}" if not identity.startswith(f"{source}:") else identity


def normalize_apify_item(source: str, item: dict[str, Any], *, collected_at: str | None = None) -> dict[str, Any] | None:
    """Normalize one actor item while preserving third-party provenance."""
    if source not in SOURCES or not isinstance(item, dict):
        return None
    title = _text(_first(item, "title", "jobTitle", "job_title", "position"))
    company_value = _first(item, "company", "companyName", "company_name", "employer")
    company = _text(company_value.get("name") if isinstance(company_value, dict) else company_value) or "Not provided by employer"
    location = _location(item)
    description = _description(item)
    source_url = _url(_first(item, "source_url", "url", "jobUrl", "job_url", "applyUrl", "apply_url", "link", "jobLink", "job_link"))
    if not title or not source_url:
        return None
    posted = _date(_first(item, "posted_date", "postedDate", "posted_at", "postedAt", "datePosted", "date_posted", "publishedAt", "createdAt", "created_at"))
    minimum, maximum = _salary(item)
    now = collected_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    row = _record(source, item, source_job_id=_stable_id(source, item, source_url, title, company, location, posted),
                  source_url=source_url, title=title, company=company, location=location,
                  description=description, posted_date=posted, salary=_text(_first(item, "salary", "salaryText", "salary_text")),
                  remote=_remote(item, description))
    row.update({
        "source_type": "third_party_collector",
        "listing_status": "unknown",
        "verification_status": "observed",
        "last_verified_at": None,
        "collected_at": now,
        "employment_type": _text(_first(item, "job_type", "jobType", "employmentType", "employment_type")) or None,
        "salary_min_lpa": minimum,
        "salary_max_lpa": maximum,
        "raw_source_job_id": row["source_job_id"].split(":", 1)[-1],
    })
    return row


def _validate_config(config: Any) -> dict[str, Any]:
    if not isinstance(config, dict) or set(config) - {"actors", "location_keywords"}:
        raise ApifyConfigError("Apify config accepts only actors and location_keywords")
    actors = config.get("actors")
    if not isinstance(actors, list) or not 1 <= len(actors) <= MAX_ACTORS:
        raise ApifyConfigError(f"Apify config requires between 1 and {MAX_ACTORS} actors")
    normalized, seen = [], set()
    for actor in actors:
        if not isinstance(actor, dict) or set(actor) - {"source", "actor", "input", "enabled"}:
            raise ApifyConfigError("each Apify actor requires source, actor, input, and optional enabled")
        source = actor.get("source")
        if source not in SOURCES or source in seen:
            raise ApifyConfigError("actor sources must be unique and supported")
        if actor.get("actor") != APIFY_ACTORS[source]:
            raise ApifyConfigError(f"actor does not match the approved {source} adapter")
        if not isinstance(actor.get("input"), dict):
            raise ApifyConfigError("actor input must be an object")
        if actor.get("enabled", True) not in (True, False):
            raise ApifyConfigError("actor enabled must be boolean")
        seen.add(source)
        normalized.append({"source": source, "actor": actor["actor"], "input": actor["input"], "enabled": actor.get("enabled", True)})
    keywords = config.get("location_keywords", [])
    if not isinstance(keywords, list) or not all(isinstance(value, str) and value.strip() for value in keywords):
        raise ApifyConfigError("location_keywords must be a list of non-empty strings")
    return {"actors": normalized, "location_keywords": [value.strip() for value in keywords]}


def load_apify_config(path: Any) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ApifyConfigError("cannot read Apify config JSON") from None
    return _validate_config(config)


def _http_runner(actor: str, payload: dict[str, Any], token: str, timeout: float) -> list[dict[str, Any]]:
    if not token.strip():
        raise ProviderError("APIFY_API_TOKEN is required")
    headers = {"Accept": "application/json", "Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    actor_url = f"{APIFY_API}/acts/{quote(actor, safe='')}/runs?{urlencode({'waitForFinish': '120'})}"
    request = Request(actor_url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES)
        run = json.loads(body)
        dataset_id = run.get("data", {}).get("defaultDatasetId") if isinstance(run, dict) else None
        if not dataset_id:
            raise ValueError("actor run returned no dataset")
        dataset_url = f"{APIFY_API}/datasets/{quote(str(dataset_id), safe='')}/items?clean=true&format=json&limit={MAX_ITEMS}"
        with urlopen(Request(dataset_url, headers={"Accept": "application/json", "Authorization": f"Bearer {token}"}), timeout=timeout) as response:
            items = json.loads(response.read(MAX_RESPONSE_BYTES))
    except Exception:
        raise ProviderError("Apify actor run or dataset request failed") from None
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ProviderError("Apify dataset must be an array of objects")
    return items


def collect_apify_sources(config: dict[str, Any], *, token: str | None = None, timeout: float = 30,
                          runner: JsonRunner | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run configured actors independently and return normalized rows/reports."""
    config = _validate_config(config)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be positive and finite")
    actual_runner = runner or _http_runner
    rows, reports = [], []
    for actor in config["actors"]:
        report = {"source": actor["source"], "actor": actor["actor"], "status": "skipped" if not actor["enabled"] else "ok", "jobs": 0, "invalid_jobs": 0, "error": None}
        if not actor["enabled"]:
            reports.append(report)
            continue
        try:
            items = actual_runner(actor["actor"], actor["input"], token or "", timeout)
            normalized = [normalize_apify_item(actor["source"], item) for item in items]
            valid = [row for row in normalized if row is not None and _matches(row["location"], config["location_keywords"])]
            invalid = len(items) - sum(item is not None for item in normalized)
            report["jobs"] = len(valid)
            report["invalid_jobs"] = invalid
            if invalid:
                report["status"] = "partial"
            rows.extend(valid)
        except Exception:
            report.update(status="failed", error="actor run or response validation failed")
        reports.append(report)
    return rows, reports
