"""Authorized JSON API adapters for job collection.

These adapters use provider APIs only; they never fetch or parse HTML.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_TIMEOUT = 15.0
DEFAULT_MAX_PAGES = 3
DEFAULT_PAGE_SIZE = 50
JsonGetter = Callable[[str, dict[str, str]], dict[str, Any]]
LOGGER = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    """A provider request or response could not be completed safely."""


class GreenhouseConfigError(ValueError):
    """The explicit Greenhouse board configuration is invalid."""


def _http_json(url: str, params: dict[str, str], timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    query = urlencode(params)
    request = Request(f"{url}?{query}" if query else url, headers={"Accept": "application/json", "User-Agent": "job-collector/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:  # nosec B310 - URL is a fixed provider endpoint
            payload = json.load(response)
    except Exception as exc:
        raise ProviderError(f"request failed for {url}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProviderError(f"provider returned a non-object response for {url}")
    return payload


def _date(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return text[:10] if len(text) >= 10 else None


class _HTMLText(HTMLParser):
    """Small, dependency-free HTML-to-text decoder for API-provided content."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if tag in {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return
        if tag in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")


def _strip_html(value: Any) -> str | None:
    if value in (None, ""):
        return None
    parser = _HTMLText()
    parser.feed(unescape(str(value)))
    parser.close()
    text = " ".join(" ".join(parser.parts).split()).strip()
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    return re.sub(r"\n{3,}", "\n\n", text) or None


def load_greenhouse_config(path: Path) -> dict[str, Any]:
    """Load and validate an explicit JSON board list; no default boards exist."""
    try:
        with path.open(encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise GreenhouseConfigError(f"cannot read Greenhouse config {path}: {exc}") from exc
    if not isinstance(config, dict) or not isinstance(config.get("boards"), list) or not config["boards"]:
        raise GreenhouseConfigError("Greenhouse config must contain a non-empty 'boards' list")
    if len(config["boards"]) > 100:
        raise GreenhouseConfigError("Greenhouse config may contain at most 100 boards")
    for index, board in enumerate(config["boards"], start=1):
        if not isinstance(board, dict) or not str(board.get("board_token", "")).strip():
            raise GreenhouseConfigError(f"boards[{index}] requires a non-empty board_token")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", str(board["board_token"]).strip()):
            raise GreenhouseConfigError(f"boards[{index}].board_token contains unsupported characters")
    keywords = config.get("india_location_keywords", ["india"])
    if not isinstance(keywords, list) or not all(isinstance(item, str) and item.strip() for item in keywords):
        raise GreenhouseConfigError("india_location_keywords must be a list of non-empty strings")
    config["india_location_keywords"] = keywords
    return config


def _greenhouse_location(item: dict[str, Any]) -> str | None:
    location = item.get("location")
    if isinstance(location, dict):
        return str(location.get("name") or "").strip() or None
    return str(location).strip() if location else None


def fetch_greenhouse(*, config: dict[str, Any], timeout: float = DEFAULT_TIMEOUT,
                     http_get: JsonGetter | None = None, errors: list[str] | None = None) -> list[dict[str, Any]]:
    """Fetch bounded public Greenhouse boards and retain only configured locations."""
    if not isinstance(config, dict) or not isinstance(config.get("boards"), list):
        raise GreenhouseConfigError("Greenhouse config must contain a boards list")
    keywords = [str(value).casefold() for value in config.get("india_location_keywords", ["india"])]
    if not keywords:
        raise GreenhouseConfigError("india_location_keywords must not be empty")
    getter = http_get or (lambda url, params: _http_json(url, params, timeout))
    rows: list[dict[str, Any]] = []
    failures = errors if errors is not None else []
    for board in config["boards"]:
        token = str(board.get("board_token", "")).strip()
        label = str(board.get("name") or token)
        try:
            payload = getter(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", {"content": "true"})
            jobs = payload.get("jobs") if isinstance(payload, dict) else None
            if not isinstance(jobs, list):
                raise ProviderError("response field 'jobs' is not a list")
            for item in jobs:
                if not isinstance(item, dict):
                    continue
                location = _greenhouse_location(item)
                if not location or not any(keyword in location.casefold() for keyword in keywords):
                    continue
                company_value = item.get("company")
                company = company_value.get("name") if isinstance(company_value, dict) else board.get("company")
                rows.append(_record("greenhouse", item, source_job_id=item.get("id"),
                                    source_url=item.get("absolute_url"), title=item.get("title"), company=company,
                                    location=location, description=_strip_html(item.get("content")),
                                    posted_date=item.get("updated_at") or item.get("first_published"),
                                    remote=item.get("workplace_type")))
        except Exception as exc:
            message = f"Greenhouse board '{label}' ({token}) failed: {exc}"
            failures.append(message)
            LOGGER.error(message)
    return rows


def _remote(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, bool):
            return "Remote" if value else "Unknown"
    text = " ".join(str(value or "") for value in values).strip()
    lowered = text.casefold()
    if "remote" in lowered or "work from home" in lowered:
        return "Remote"
    if "hybrid" in lowered:
        return "Hybrid"
    if text:
        return "On-site"
    return None


def _record(source: str, item: dict[str, Any], *, source_job_id: Any, source_url: Any,
            title: Any, company: Any, location: Any, description: Any, posted_date: Any,
            salary: Any = None, remote: Any = None) -> dict[str, Any]:
    collected = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    salary_value = str(salary).strip() if salary not in (None, "") else None
    salary_min = item.get("salary_min_lpa")
    salary_max = item.get("salary_max_lpa")
    remote_value = _remote(remote, item.get("description"), item.get("location"))
    record = {
        "source": source,
        "source_job_id": str(source_job_id or source_url or "").strip(),
        "source_url": str(source_url or "").strip(),
        "title": str(title or "").strip(),
        "company": str(company or "").strip() or None,
        "location": str(location or "").strip() or None,
        "description": str(description or "").strip() or None,
        "posted_date": _date(posted_date),
        "collected_at": collected,
        "salary": salary_value,
        "remote": remote_value,
    }
    # Compatibility with the existing CSV/database analytics schema.
    record.update(job_id=record["source_job_id"], remote_type=remote_value,
                  salary_min_lpa=salary_min, salary_max_lpa=salary_max)
    return record


def fetch_arbeitnow(*, max_pages: int = DEFAULT_MAX_PAGES, page_size: int = DEFAULT_PAGE_SIZE,
                    timeout: float = DEFAULT_TIMEOUT, http_get: JsonGetter | None = None) -> list[dict[str, Any]]:
    """Fetch up to ``max_pages`` from Arbeitnow's documented public API."""
    if max_pages < 1 or page_size < 1:
        raise ValueError("max_pages and page_size must be positive")
    getter = http_get or (lambda url, params: _http_json(url, params, timeout))
    rows: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        payload = getter("https://www.arbeitnow.com/api/job-board-api", {"page": str(page)})
        items = payload.get("data", [])
        if not isinstance(items, list):
            raise ProviderError("Arbeitnow response field 'data' is not a list")
        for item in items:
            if not isinstance(item, dict):
                continue
            rows.append(_record("arbeitnow", item, source_job_id=item.get("slug") or item.get("id"),
                                source_url=item.get("url"), title=item.get("title"),
                                company=item.get("company_name"), location=item.get("location"),
                                description=item.get("description"), posted_date=item.get("created_at"),
                                remote=item.get("remote")))
        if len(items) < page_size or not items:
            break
    return rows


def fetch_adzuna(*, country: str = "gb", max_pages: int = DEFAULT_MAX_PAGES,
                 page_size: int = DEFAULT_PAGE_SIZE, timeout: float = DEFAULT_TIMEOUT,
                 http_get: JsonGetter | None = None) -> list[dict[str, Any]]:
    """Fetch Adzuna jobs when both required environment credentials exist."""
    app_id, app_key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        return []
    if max_pages < 1 or page_size < 1:
        raise ValueError("max_pages and page_size must be positive")
    getter = http_get or (lambda url, params: _http_json(url, params, timeout))
    rows: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
        payload = getter(url, {"app_id": app_id, "app_key": app_key, "results_per_page": str(page_size)})
        items = payload.get("results", [])
        if not isinstance(items, list):
            raise ProviderError("Adzuna response field 'results' is not a list")
        for item in items[:page_size]:
            if not isinstance(item, dict):
                continue
            location = item.get("location", {}).get("display_name") if isinstance(item.get("location"), dict) else item.get("location")
            company = item.get("company", {}).get("display_name") if isinstance(item.get("company"), dict) else item.get("company")
            salary_min = item.get("salary_min")
            salary_max = item.get("salary_max")
            predicted = bool(item.get("salary_is_predicted"))
            rows.append(_record("adzuna", item, source_job_id=item.get("id"), source_url=item.get("redirect_url"),
                                title=item.get("title"), company=company, location=location,
                                description=item.get("description"), posted_date=item.get("created"),
                                salary=salary_max or salary_min, remote=item.get("contract_type")))
            rows[-1]["salary_is_predicted"] = predicted
            # Adzuna's India salary values are annual INR. Only employer-provided
            # values are promoted to LPA analytics; predicted values remain raw
            # provenance and never become training labels.
            if country.casefold() == "in" and not predicted:
                rows[-1]["salary_min_lpa"] = float(salary_min) / 100_000 if salary_min is not None else None
                rows[-1]["salary_max_lpa"] = float(salary_max) / 100_000 if salary_max is not None else None
        if len(items) < page_size or not items:
            break
    return rows


def collect_enabled_providers(*, adzuna_country: str = "in", greenhouse_config: dict[str, Any] | None = None,
                              greenhouse_errors: list[str] | None = None, include_arbeitnow: bool = True,
                              include_adzuna: bool = True, **kwargs: Any) -> list[dict[str, Any]]:
    """Collect Arbeitnow and, when configured, Adzuna."""
    rows = (fetch_arbeitnow(**kwargs) if include_arbeitnow else [])
    if include_adzuna:
        rows += fetch_adzuna(country=adzuna_country, **kwargs)
    if greenhouse_config is not None:
        rows += fetch_greenhouse(config=greenhouse_config, errors=greenhouse_errors,
                                 timeout=kwargs.get("timeout", DEFAULT_TIMEOUT))
    return rows
