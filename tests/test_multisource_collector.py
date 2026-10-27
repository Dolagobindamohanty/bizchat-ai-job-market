"""Recorded public-API-shaped responses; these tests never contact a job board."""

import copy
import csv
import json
import sys
from datetime import date

import pytest

from collector import cli, multisource
from collector.multisource import (SourcesConfigError, SourcesPublicationError,
                                   collect_sources, load_sources_config,
                                   publish_sources_snapshot)
from collector.providers import ProviderError
from collector.snapshot import filter_recent_rows, write_snapshot


def board(source="lever", token="acme"):
    return {"source": source, "board_token": token, "company": "Acme"}


def config(*boards, keywords=None):
    return {"boards": list(boards) or [board()], "location_keywords": keywords or ["India"]}


def test_recent_snapshot_filter_excludes_old_and_undated_rows():
    rows = [
        {"job_id": "new", "posted_date": "2026-10-20"},
        {"job_id": "old", "posted_date": "2026-09-26"},
        {"job_id": "ancient", "posted_date": "2023-01-01"},
        {"job_id": "unknown", "posted_date": None},
    ]
    recent = filter_recent_rows(rows, 30, today=date(2026, 10, 27))
    assert [row["job_id"] for row in recent] == ["new"]


LEVER_JOB = {
    "id": "same-id", "text": "Platform Engineer",
    "categories": {"location": "London", "allLocations": ["London", "Bengaluru, India"],
                   "commitment": "Full-time"},
    "descriptionPlain": "Build platforms. Remote benefits are available.",
    "additional": "<p>Extra <b>details</b></p>",
    "lists": [{"text": "Requirements", "content": "<ul><li>Python</li></ul>"}],
    "hostedUrl": "https://jobs.lever.co/acme/same-id",
    "applyUrl": "https://jobs.lever.co/acme/same-id/apply",
    "createdAt": 1767225600000, "workplaceType": "hybrid",
}
ASHBY_JOB = {
    "id": "same-id", "title": "Platform Engineer", "location": "London",
    "secondaryLocations": [{"location": "Bengaluru, India", "address": {"addressCountry": "IN"}}],
    "isListed": True, "descriptionHtml": "<p>Build <b>platforms</b>.</p>",
    "publishedAt": "2026-01-02T03:00:00Z", "workplaceType": "OnSite", "employmentType": "FullTime",
    "jobUrl": "https://jobs.ashbyhq.com/acme/same-id",
    "applyUrl": "https://jobs.ashbyhq.com/acme/same-id/application",
}
GREENHOUSE_JOB = {
    "id": "same-id", "title": "Platform Engineer", "location": {"name": "Bengaluru, India"},
    "absolute_url": "https://boards.greenhouse.io/acme/jobs/same-id",
    "content": "<p>Remote benefits</p>", "first_published": "2026-01-01T00:00:00Z",
    "updated_at": "2026-10-01T00:00:00Z", "workplace_type": "hybrid",
}


def payload(source, jobs=None):
    job = {"lever": LEVER_JOB, "ashby": ASHBY_JOB, "greenhouse": GREENHOUSE_JOB}[source]
    items = copy.deepcopy([job] if jobs is None else jobs)
    return items if source == "lever" else {"jobs": items}


def test_plain_description_preserves_lines_without_trailing_whitespace():
    job = copy.deepcopy(LEVER_JOB)
    job["descriptionPlain"] = "First line  \r\n   \r\nSecond line\t\n"
    rows, reports = collect_sources(config(board()), http_get=lambda url, query: [job])
    assert reports[0]["status"] == "ok"
    assert "First line\n\nSecond line" in rows[0]["description"]
    assert all(line == line.rstrip() for line in rows[0]["description"].splitlines())


@pytest.mark.parametrize("source,endpoint,params,date", [
    ("lever", "https://api.lever.co/v0/postings/acme", {"mode": "json"}, "2026-01-01"),
    ("ashby", "https://api.ashbyhq.com/posting-api/job-board/acme", {}, "2026-01-02"),
    ("greenhouse", "https://boards-api.greenhouse.io/v1/boards/acme/jobs", {"content": "true"}, "2026-01-01"),
])
def test_documented_endpoints_schema_and_provenance(source, endpoint, params, date):
    calls = []

    def get(url, query):
        calls.append((url, query))
        return payload(source)

    rows, reports = collect_sources(config(board(source)), http_get=get)
    assert calls == [(endpoint, params)]
    assert reports == [{**board(source), "status": "ok", "jobs": 1, "error": None}]
    row = rows[0]
    assert row["source"] == source
    assert row["source_job_id"] == row["job_id"] == "acme:same-id"
    assert row["raw_source_job_id"] == "same-id"
    assert row["company"] == "Acme"
    assert row["posted_date"] == date
    assert row["collected_at"].endswith("Z")
    assert "Bengaluru" in row["location"]
    assert row["source_url"].startswith("https://")
    assert row["remote_type"] == row["remote"] == ("On-site" if source == "ashby" else "Hybrid")


def test_lever_plain_text_additional_and_list_content():
    rows, _ = collect_sources(config(), http_get=lambda *_: payload("lever"))
    row = rows[0]
    assert row["description"] == "Build platforms. Remote benefits are available. Extra details Requirements Python"
    assert row["employment_type"] == "Full-time"
    assert row["apply_url"].endswith("/apply")


@pytest.mark.parametrize("source", ["lever", "ashby"])
def test_fallback_html_description_and_application_url(source):
    response = payload(source)
    item = response[0] if source == "lever" else response["jobs"][0]
    item.pop("hostedUrl" if source == "lever" else "jobUrl")
    item["descriptionPlain"] = None
    item["description" if source == "lever" else "descriptionHtml"] = "<p>Build &amp; ship</p>"
    rows, _ = collect_sources(config(board(source)), http_get=lambda *_: response)
    assert rows[0]["source_url"] == item["applyUrl"]
    assert "Build & ship" in rows[0]["description"]


def test_ashby_html_and_unlisted_jobs():
    hidden = dict(ASHBY_JOB, isListed=False, id=None, title=None, jobUrl=None)
    rows, reports = collect_sources(config(board("ashby")),
                                    http_get=lambda *_: payload("ashby", [hidden, ASHBY_JOB]))
    assert len(rows) == 1 and reports[0]["jobs"] == 1
    assert rows[0]["description"] == "Build platforms."
    assert rows[0]["employment_type"] == "FullTime"


@pytest.mark.parametrize("source", ["lever", "ashby", "greenhouse"])
@pytest.mark.parametrize("location,expected", [("Indiana", 0), ("Indianapolis", 0), ("India", 1),
                                               ("Remote - INDIA", 1), ("India-based", 1)])
def test_word_boundary_location_filter(source, location, expected):
    response = payload(source)
    item = response[0] if source == "lever" else response["jobs"][0]
    if source == "lever":
        item["categories"].update(location=location, allLocations=[])
    elif source == "ashby":
        item.update(location=location, secondaryLocations=[])
    else:
        item["location"] = {"name": location}
    rows, reports = collect_sources(config(board(source)), http_get=lambda *_: response)
    assert len(rows) == expected
    assert reports[0]["status"] == "ok"


@pytest.mark.parametrize("source", ["lever", "ashby"])
def test_secondary_locations_are_preserved_and_match_city_keyword(source):
    rows, _ = collect_sources(config(board(source), keywords=["Bengaluru"]), http_get=lambda *_: payload(source))
    assert rows[0]["location"] == "London; Bengaluru, India"


@pytest.mark.parametrize("source", ["lever", "ashby", "greenhouse"])
def test_remote_benefits_in_description_never_infer_remote(source):
    response = payload(source)
    item = response[0] if source == "lever" else response["jobs"][0]
    item.pop("workplace_type" if source == "greenhouse" else "workplaceType")
    rows, _ = collect_sources(config(board(source)), http_get=lambda *_: response)
    assert rows[0]["remote"] is None
    assert rows[0]["remote_type"] is None


@pytest.mark.parametrize("source,date_field", [("lever", "createdAt"), ("ashby", "publishedAt"),
                                             ("greenhouse", "first_published")])
def test_null_posting_dates_never_use_collection_or_update_date(source, date_field):
    response = payload(source)
    item = response[0] if source == "lever" else response["jobs"][0]
    item[date_field] = None
    rows, reports = collect_sources(config(board(source)), http_get=lambda *_: response)
    assert rows[0]["posted_date"] is None
    assert rows[0]["collected_at"]
    assert reports[0]["status"] == "ok"


def test_lever_zero_millisecond_timestamp_is_epoch_not_missing():
    response = payload("lever")
    response[0]["createdAt"] = 0
    rows, _ = collect_sources(config(), http_get=lambda *_: response)
    assert rows[0]["posted_date"] == "1970-01-01"


def test_namespaced_identity_preserves_same_native_ids_across_boards_and_sources(tmp_path):
    boards = [board(source, token) for source in ["lever", "ashby", "greenhouse"] for token in ["first", "second"]]

    def get(url, _):
        return payload("lever" if "lever.co" in url else "ashby" if "ashbyhq.com" in url else "greenhouse")

    rows, _ = collect_sources(config(*boards), http_get=get)
    output = tmp_path / "jobs.csv"
    assert write_snapshot(rows + rows, output) == 6
    with output.open(newline="", encoding="utf-8") as handle:
        saved = list(csv.DictReader(handle))
    assert len({(r["source"], r["source_job_id"]) for r in saved}) == 6
    assert all(r["source_job_id"].startswith(("first:", "second:")) for r in saved)


@pytest.mark.parametrize("source", ["lever", "ashby", "greenhouse"])
@pytest.mark.parametrize("bad_job", [None, {}, {"id": "broken", "title": "Only partial"}])
def test_malformed_partial_response_fails_entire_board(source, bad_job):
    valid = {"lever": LEVER_JOB, "ashby": ASHBY_JOB, "greenhouse": GREENHOUSE_JOB}[source]
    rows, reports = collect_sources(config(board(source)), http_get=lambda *_: payload(source, [valid, bad_job]))
    assert rows == []
    assert reports[0]["status"] == "failed" and reports[0]["jobs"] == 0


@pytest.mark.parametrize("source,response", [("lever", {"jobs": []}), ("ashby", []),
                                             ("ashby", {"jobs": None}), ("greenhouse", {})])
def test_wrong_response_shape_fails_board(source, response):
    rows, reports = collect_sources(config(board(source)), http_get=lambda *_: response)
    assert rows == [] and reports[0]["status"] == "failed"


@pytest.mark.parametrize("source,field,value", [
    ("lever", "createdAt", "2026-01-01"), ("lever", "createdAt", float("inf")),
    ("lever", "lists", [None]), ("lever", "categories", []),
    ("ashby", "publishedAt", "invalid-date"), ("ashby", "secondaryLocations", [None]),
    ("ashby", "descriptionHtml", {"private": "data"}), ("ashby", "isListed", "false"),
    ("greenhouse", "first_published", "invalid-date"),
])
def test_malformed_optional_fields_fail_board_instead_of_dropping_jobs(source, field, value):
    response = payload(source)
    item = response[0] if source == "lever" else response["jobs"][0]
    item[field] = value
    rows, reports = collect_sources(config(board(source)), http_get=lambda *_: response)
    assert rows == [] and reports[0]["status"] == "failed"


def test_safe_board_errors_and_continuation():
    def get(url, _):
        if "lever.co" in url:
            raise RuntimeError("private-token https://user:secret@proxy.test secret response body")
        return payload("ashby")

    rows, reports = collect_sources(config(board(), board("ashby")), http_get=get)
    assert len(rows) == 1 and rows[0]["source"] == "ashby"
    assert [r["status"] for r in reports] == ["failed", "ok"]
    assert "secret" not in json.dumps(reports)
    assert "private-token" not in json.dumps(reports)


def test_failed_board_preserves_existing_snapshot_and_partial_requires_opt_in(tmp_path):
    output = tmp_path / "jobs.csv"
    output.write_text("previous snapshot", encoding="utf-8")

    def get(url, _):
        return payload("lever") if "lever.co" in url else {"jobs": "bad"}

    cfg = config(board(), board("ashby"))
    with pytest.raises(SourcesPublicationError) as error:
        publish_sources_snapshot(cfg, output, http_get=get)
    assert error.value.reports[1]["status"] == "failed"
    assert output.read_text(encoding="utf-8") == "previous snapshot"
    count, reports = publish_sources_snapshot(cfg, output, http_get=get, allow_partial=True)
    assert count == 1 and reports[1]["status"] == "failed"
    with output.open(newline="", encoding="utf-8") as handle:
        assert next(csv.DictReader(handle))["source"] == "lever"


def test_empty_successful_snapshot_requires_separate_opt_in(tmp_path):
    output = tmp_path / "jobs.csv"
    output.write_text("previous snapshot", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        publish_sources_snapshot(config(), output, http_get=lambda *_: [])
    assert output.read_text(encoding="utf-8") == "previous snapshot"
    count, _ = publish_sources_snapshot(config(), output, http_get=lambda *_: [], allow_empty=True)
    assert count == 0 and output.read_text(encoding="utf-8").startswith("job_id,")


@pytest.mark.parametrize("bad_config", [
    {}, {"boards": []}, {"boards": [board()] * 101},
    config(board("unsupported")), config(dict(board(), board_token="../private")),
    config(dict(board(), board_token="https://evil.test")), config(dict(board(), board_token="a?secret=b")),
    config(dict(board(), board_token=42)), config(dict(board(), company="")),
    config(dict(board(), url="https://evil.test")),
    {"boards": [board()], "location_keywords": []},
    {"boards": [board()], "location_keywords": "India"},
    {"boards": [board()], "location_keywords": [None]},
])
def test_sources_config_rejects_unsafe_or_invalid_input(tmp_path, bad_config):
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(bad_config), encoding="utf-8")
    with pytest.raises(SourcesConfigError):
        load_sources_config(path)


def test_load_sources_config_valid_and_read_error_safe(tmp_path):
    path = tmp_path / "sources.json"
    cfg = config(board("lever", "safe-Token_12"), board("ashby"))
    path.write_text(json.dumps(cfg), encoding="utf-8")
    assert load_sources_config(path) == cfg
    with pytest.raises(SourcesConfigError, match="cannot read sources config JSON"):
        load_sources_config(tmp_path / "missing-private-path.json")


@pytest.mark.parametrize("url", ["http://jobs.lever.co/a", "https://user:secret@example.test/a", "javascript:alert(1)"])
def test_insecure_job_urls_fail_board(url):
    response = payload("lever")
    response[0]["hostedUrl"] = url
    rows, reports = collect_sources(config(), http_get=lambda *_: response)
    assert rows == [] and reports[0]["status"] == "failed"


def test_http_helper_accepts_arrays_bounds_reads_and_hides_network_errors(monkeypatch):
    reads = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, limit):
            reads.append(limit)
            return b'[{"id":"one"}]'

    monkeypatch.setattr(multisource, "urlopen", lambda *args, **kwargs: Response())
    assert multisource._http_json("https://api.lever.co/v0/postings/acme", {"mode": "json"}) == [{"id": "one"}]
    assert reads == [16 * 1024 * 1024 + 1]
    monkeypatch.setattr(multisource, "MAX_RESPONSE_BYTES", 2)
    with pytest.raises(ProviderError, match="source request failed"):
        multisource._http_json("https://api.lever.co/v0/postings/acme", {})

    def broken(*args, **kwargs):
        raise OSError("private proxy password")

    monkeypatch.setattr(multisource, "urlopen", broken)
    with pytest.raises(ProviderError) as error:
        multisource._http_json("https://api.lever.co/v0/postings/acme", {})
    assert "private" not in str(error.value)
    with pytest.raises(ProviderError, match="unsupported"):
        multisource._http_json("https://evil.test/jobs", {})


def test_cli_sources_mode_skips_aggregators_and_writes_safe_report(tmp_path, monkeypatch):
    path, output, report = (tmp_path / name for name in ["sources.json", "jobs.csv", "report.json"])
    path.write_text(json.dumps(config()), encoding="utf-8")
    monkeypatch.setattr(multisource, "_http_json", lambda *_: payload("lever"))
    monkeypatch.setattr(cli, "collect_enabled_providers", lambda **_: pytest.fail("aggregators must be skipped"))
    monkeypatch.setattr(sys, "argv", ["collector", "--sources-config", str(path), "--output", str(output),
                                      "--report", str(report)])
    cli.main()
    summary = json.loads(report.read_text(encoding="utf-8"))
    assert summary["complete"] is True and summary["jobs"] == 1
    assert summary["boards"][0]["status"] == "ok"
    assert "description" not in report.read_text(encoding="utf-8")
    assert output.exists()


def test_cli_failure_report_is_written_before_publication_guard(tmp_path, monkeypatch):
    path, output, report = (tmp_path / name for name in ["sources.json", "jobs.csv", "report.json"])
    path.write_text(json.dumps(config()), encoding="utf-8")
    output.write_text("previous snapshot", encoding="utf-8")
    monkeypatch.setattr(multisource, "_http_json", lambda *_: {})
    monkeypatch.setattr(sys, "argv", ["collector", "--sources-config", str(path), "--output", str(output),
                                      "--report", str(report), "--allow-empty"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    assert output.read_text(encoding="utf-8") == "previous snapshot"
    assert json.loads(report.read_text(encoding="utf-8"))["complete"] is False


@pytest.mark.parametrize("empty_refresh", [False, True])
def test_cli_preserves_only_uncollected_recent_sources(tmp_path, monkeypatch, empty_refresh):
    path, output = (tmp_path / name for name in ["sources.json", "jobs.csv"])
    path.write_text(json.dumps(config()), encoding="utf-8")
    rows, _ = collect_sources(config(), http_get=lambda *_: payload("lever"))
    old = dict(rows[0], posted_date=date.today().isoformat())
    preserved = dict(old, source="naukri", source_type="third_party_collector",
                     verification_status="observed")
    stale = dict(preserved, source_job_id="stale", job_id="stale", posted_date="2000-01-01")
    unknown = dict(preserved, source_job_id="unknown", job_id="unknown", posted_date="")
    write_snapshot([old, preserved, stale, unknown], output)
    refreshed = dict(old, title="Refreshed title")
    monkeypatch.setattr(cli, "collect_sources", lambda *_, **kw: (
        [] if empty_refresh else [refreshed], [{"status": "ok"}]))
    monkeypatch.setattr(sys, "argv", ["collector", "--sources-config", str(path), "--output", str(output),
                                     "--preserve-uncollected-sources", "--max-posted-age-days", "30"])
    cli.main()
    with output.open(newline="", encoding="utf-8") as handle:
        saved = list(csv.DictReader(handle))
    retained = next(row for row in saved if row["source"] == "naukri")
    assert retained["collected_at"] == preserved["collected_at"]
    assert retained["verification_status"] == "observed"
    assert len(saved) == (1 if empty_refresh else 2)
    if not empty_refresh:
        assert next(row for row in saved if row["source"] == "lever")["title"] == "Refreshed title"


def test_cli_explicit_partial_flag_publishes_only_successful_boards(tmp_path, monkeypatch):
    path, output = (tmp_path / name for name in ["sources.json", "jobs.csv"])
    path.write_text(json.dumps(config(board(), board("ashby"))), encoding="utf-8")
    monkeypatch.setattr(multisource, "_http_json", lambda url, *_: payload("lever") if "lever.co" in url else {})
    monkeypatch.setattr(sys, "argv", ["collector", "--sources-config", str(path), "--output", str(output),
                                      "--allow-partial"])
    cli.main()
    with output.open(newline="", encoding="utf-8") as handle:
        saved = list(csv.DictReader(handle))
    assert len(saved) == 1 and saved[0]["source"] == "lever"


@pytest.mark.parametrize("conflict", ["--greenhouse-only", "--greenhouse-config"])
def test_cli_sources_config_mutual_exclusion(tmp_path, monkeypatch, conflict):
    args = ["collector", "--sources-config", str(tmp_path / "sources.json"), "--output", str(tmp_path / "jobs.csv"), conflict]
    if conflict == "--greenhouse-config":
        args.append(str(tmp_path / "greenhouse.json"))
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
