"""Explicitly configured public Greenhouse, Lever and Ashby job boards.

Only documented, fixed JSON endpoints are requested. A malformed job invalidates
its entire board so that a truncated response cannot silently replace a snapshot.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from collector.providers import ProviderError, _record, _strip_html, fetch_greenhouse
from collector.snapshot import write_snapshot

JsonGetter = Callable[[str, dict[str, str]], Any]
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
SOURCES = {"greenhouse", "lever", "ashby"}


class SourcesConfigError(ValueError):
    """The explicit board configuration is invalid."""


class SourcesPublicationError(RuntimeError):
    """Collection was incomplete; the existing snapshot has been preserved."""

    def __init__(self, reports: list[dict[str, Any]]) -> None:
        super().__init__("refusing to publish: one or more source boards failed")
        self.reports = reports


def _validate_config(config: Any) -> dict[str, Any]:
    if not isinstance(config, dict) or set(config) - {"boards", "location_keywords"}:
        raise SourcesConfigError("sources config accepts only boards and location_keywords")
    boards = config.get("boards")
    if not isinstance(boards, list) or not 1 <= len(boards) <= 100:
        raise SourcesConfigError("sources config requires between 1 and 100 boards")
    normalized = []
    seen = set()
    for board in boards:
        if not isinstance(board, dict) or set(board) != {"source", "board_token", "company"}:
            raise SourcesConfigError("each board requires only source, board_token and company")
        source, token, company = (board[key] for key in ("source", "board_token", "company"))
        if not isinstance(source, str) or source not in SOURCES:
            raise SourcesConfigError("board source must be greenhouse, lever or ashby")
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", token):
            raise SourcesConfigError("board_token contains unsupported characters")
        if not isinstance(company, str) or not company.strip() or any(ord(c) < 32 for c in company):
            raise SourcesConfigError("company must be a non-empty single-line string")
        if (source, token) in seen:
            raise SourcesConfigError("duplicate source board")
        seen.add((source, token))
        normalized.append({"source": source, "board_token": token, "company": company.strip()})
    keywords = config.get("location_keywords", ["india"])
    if not isinstance(keywords, list) or not keywords or not all(isinstance(k, str) and k.strip() for k in keywords):
        raise SourcesConfigError("location_keywords must be a non-empty list of non-empty strings")
    return {"boards": normalized, "location_keywords": [k.strip() for k in keywords]}


def load_sources_config(path: Path) -> dict[str, Any]:
    """Read a bounded explicit board list. Custom endpoints are not accepted."""
    try:
        config = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SourcesConfigError("cannot read sources config JSON") from None
    return _validate_config(config)


def _http_json(url: str, params: dict[str, str], timeout: float = 15) -> Any:
    # Defense in depth: even internal callers cannot introduce another endpoint.
    if not re.fullmatch(
        r"https://(?:api\.lever\.co/v0/postings|api\.ashbyhq\.com/posting-api/job-board|"
        r"boards-api\.greenhouse\.io/v1/boards)/[A-Za-z0-9_-]+(?:/jobs)?", url
    ):
        raise ProviderError("unsupported source endpoint")
    query = urlencode(params)
    request = Request(url + ("?" + query if query else ""),
                      headers={"Accept": "application/json", "User-Agent": "job-collector/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:  # nosec B310 - fixed public API endpoints
            body = response.read(MAX_RESPONSE_BYTES + 1)
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("too large")
        payload = json.loads(body)
        if not isinstance(payload, (dict, list)):
            raise ValueError("not an object or array")
        return payload
    except Exception:
        # Network exceptions can contain URLs, proxy credentials or response bodies.
        raise ProviderError("source request failed or returned invalid JSON") from None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ProviderError("malformed text field")
    return "\n".join(line.rstrip() for line in value.splitlines()).strip()


def _url(value: Any) -> str:
    text = _text(value)
    try:
        parsed = urlsplit(text)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("invalid URL")
        if any(c.isspace() or ord(c) < 32 for c in text):
            raise ValueError("invalid URL")
        parsed.port
    except ValueError:
        raise ProviderError("job URL must be a valid HTTPS URL") from None
    return text


def _date(value: Any, *, milliseconds: bool = False) -> str | None:
    if value is None or value == "":
        return None
    try:
        if milliseconds:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("invalid timestamp")
            return datetime.fromtimestamp(value / 1000, timezone.utc).date().isoformat()
        if not isinstance(value, str):
            raise ValueError("invalid timestamp")
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except (ValueError, OverflowError, OSError):
        raise ProviderError("malformed posting date") from None


def _remote(value: Any) -> str | None:
    text = _text(value).casefold().replace("_", "").replace("-", "").replace(" ", "")
    return {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site", "office": "On-site"}.get(text)


def _matches(location: str, keywords: list[str]) -> bool:
    return any(re.search(r"(?<!\w)" + re.escape(k) + r"(?!\w)", location, re.IGNORECASE) for k in keywords)


def _locations(primary: Any, secondary: Any, *, ashby: bool = False) -> str:
    locations = [_text(primary)]
    if secondary is None:
        secondary = []
    if not isinstance(secondary, list):
        raise ProviderError("malformed additional locations")
    for value in secondary:
        if ashby:
            if not isinstance(value, dict):
                raise ProviderError("malformed secondary location")
            value = value.get("location")
        locations.append(_text(value))
    return "; ".join(dict.fromkeys(value for value in locations if value))


def _job(board: dict[str, str], item: dict[str, Any], *, title: Any, url: Any,
         location: str, description: str | None, date: str | None, remote: Any) -> dict[str, Any]:
    native_id = item.get("id")
    if isinstance(native_id, bool) or not isinstance(native_id, (str, int)) or not str(native_id).strip():
        raise ProviderError("job is missing an identifier")
    title = _text(title)
    if not title:
        raise ProviderError("job is missing a title")
    identity = f"{board['board_token']}:{native_id}"
    # _record normally infers remote from description/location; provide no raw
    # fields and explicitly override it with structured workplace information.
    row = _record(board["source"], {}, source_job_id=identity, source_url=_url(url),
                  title=title, company=board["company"], location=location,
                  description=description, posted_date=date)
    row["remote"] = row["remote_type"] = _remote(remote)
    row["raw_source_job_id"] = str(native_id)
    return row


def _lever(board: dict[str, str], payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise ProviderError("Lever response must be an array")
    rows = []
    for item in payload:
        if not isinstance(item, dict):
            raise ProviderError("malformed job")
        categories = item.get("categories", {})
        if not isinstance(categories, dict):
            raise ProviderError("malformed categories")
        location = _locations(categories.get("location"), categories.get("allLocations"))
        parts = [_text(item.get("descriptionPlain")) or _strip_html(_text(item.get("description"))),
                 _text(item.get("additionalPlain")) or _strip_html(_text(item.get("additional")))]
        lists = item.get("lists", [])
        if not isinstance(lists, list):
            raise ProviderError("malformed job lists")
        for entry in lists:
            if not isinstance(entry, dict):
                raise ProviderError("malformed job list entry")
            parts += [_text(entry.get("text")), _strip_html(_text(entry.get("content")))]
        row = _job(board, item, title=item.get("text"), url=item.get("hostedUrl") or item.get("applyUrl"),
                   location=location, description=" ".join(part for part in parts if part) or None,
                   date=_date(item.get("createdAt"), milliseconds=True), remote=item.get("workplaceType"))
        row["employment_type"] = _text(categories.get("commitment")) or None
        if item.get("applyUrl"):
            row["apply_url"] = _url(item["applyUrl"])
        rows.append(row)
    return rows


def _ashby(board: dict[str, str], payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ProviderError("Ashby response must contain jobs array")
    rows = []
    for item in payload["jobs"]:
        if not isinstance(item, dict):
            raise ProviderError("malformed job")
        if item.get("isListed") is False:
            continue
        if item.get("isListed") is not None and not isinstance(item["isListed"], bool):
            raise ProviderError("malformed listing visibility")
        row = _job(board, item, title=item.get("title"), url=item.get("jobUrl") or item.get("applyUrl"),
                   location=_locations(item.get("location"), item.get("secondaryLocations"), ashby=True),
                   description=_text(item.get("descriptionPlain")) or _strip_html(_text(item.get("descriptionHtml"))),
                   date=_date(item.get("publishedAt")), remote=item.get("workplaceType"))
        row["employment_type"] = _text(item.get("employmentType")) or None
        if item.get("applyUrl"):
            row["apply_url"] = _url(item["applyUrl"])
        rows.append(row)
    return rows


def _greenhouse(board: dict[str, str], payload: Any, timeout: float) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise ProviderError("Greenhouse response must contain jobs array")
    originals = {}
    for item in payload["jobs"]:
        if not isinstance(item, dict):
            raise ProviderError("malformed job")
        location = item.get("location")
        if isinstance(location, dict):
            location = location.get("name")
        # Validate before invoking the legacy adapter, which otherwise skips bad
        # entries. Date is first publication only, never the last update time.
        row = _job(board, item, title=item.get("title"), url=item.get("absolute_url"),
                   location=_text(location), description=_strip_html(_text(item.get("content"))),
                   date=_date(item.get("first_published")), remote=item.get("workplace_type"))
        originals[row["raw_source_job_id"]] = row
    errors: list[str] = []
    rows = fetch_greenhouse(config={"boards": [board], "india_location_keywords": [""]},
                            timeout=timeout, http_get=lambda url, params: payload, errors=errors)
    if errors:
        raise ProviderError("Greenhouse board could not be parsed")
    return [originals[row["source_job_id"]] for row in rows]


def collect_sources(config: dict[str, Any], timeout: float = 15,
                    http_get: JsonGetter | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return matching jobs and one safe report per board, continuing on failures.

    Injectable http_get(url, params) must return decoded JSON (object or array).
    Jobs from a failed board are discarded in full. No publication occurs here.
    """
    config = _validate_config(config)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be positive and finite")
    getter = http_get or (lambda url, params: _http_json(url, params, timeout))
    rows, reports = [], []
    for board in config["boards"]:
        source, token = board["source"], board["board_token"]
        report = {**board, "status": "ok", "jobs": 0, "error": None}
        try:
            if source == "lever":
                jobs = _lever(board, getter(f"https://api.lever.co/v0/postings/{token}", {"mode": "json"}))
            elif source == "ashby":
                jobs = _ashby(board, getter(f"https://api.ashbyhq.com/posting-api/job-board/{token}", {}))
            else:
                jobs = _greenhouse(board, getter(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
                                                {"content": "true"}), timeout)
            jobs = [job for job in jobs if _matches(job.get("location") or "", config["location_keywords"])]
            rows.extend(jobs)
            report["jobs"] = len(jobs)
        except Exception:
            report.update(status="failed", error="board request or response validation failed")
        reports.append(report)
    return rows, reports


def publish_sources_snapshot(config: dict[str, Any], output: Path, timeout: float = 15,
                             http_get: JsonGetter | None = None, *, allow_partial: bool = False,
                             allow_empty: bool = False) -> tuple[int, list[dict[str, Any]]]:
    """Publish atomically only after collection passes the explicit safety guards."""
    rows, reports = collect_sources(config, timeout, http_get)
    if not allow_partial and any(report["status"] != "ok" for report in reports):
        raise SourcesPublicationError(reports)
    return write_snapshot(rows, Path(output), allow_empty=allow_empty), reports
