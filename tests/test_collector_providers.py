import csv
import json

import pytest

from collector.providers import (GreenhouseConfigError, ProviderError, fetch_adzuna,
                                 fetch_arbeitnow, fetch_greenhouse, load_greenhouse_config)
from collector.snapshot import write_snapshot


def test_arbeitnow_normalizes_json_and_bounds_pages():
    calls = []

    def fake_get(url, params):
        calls.append((url, params))
        return {"data": [{"slug": "abc", "title": "Engineer", "company_name": "Acme",
                              "location": "Berlin", "description": "Remote role",
                          "created_at": "2026-01-02T03:04:05Z", "url": "https://example.test/abc"}]}

    rows = fetch_arbeitnow(max_pages=4, page_size=1, http_get=fake_get)
    assert len(rows) == 4
    assert rows[0]["source"] == "arbeitnow"
    assert rows[0]["source_job_id"] == "abc"
    assert rows[0]["posted_date"] == "2026-01-02"
    assert rows[0]["remote"] == "Remote"
    assert calls[0][1] == {"page": "1"}


def test_greenhouse_html_entities_are_decoded_before_text_extraction():
    from collector.providers import fetch_greenhouse

    rows = fetch_greenhouse(
        config={"boards": [{"name": "Example", "company": "Example", "board_token": "example"}],
                "india_location_keywords": ["India"]},
        http_get=lambda *_: {"jobs": [{"id": 1, "title": "Engineer", "location": {"name": "India"},
                                        "content": "&lt;p&gt;Build&nbsp;&amp;amp;&nbsp;ship&lt;/p&gt;",
                                        "absolute_url": "https://example.test/1", "updated_at": "2026-01-01"}]},
    )

    assert rows[0]["description"] == "Build & ship"


def test_adzuna_requires_both_credentials(monkeypatch):
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.setenv("ADZUNA_APP_KEY", "key-is-not-used")
    assert fetch_adzuna(http_get=lambda *_: pytest.fail("must not call API")) == []


def test_adzuna_normalizes_response_and_does_not_log_secret(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "app-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "app-key")
    seen = []

    def fake_get(url, params):
        seen.append((url, params))
        return {"results": [{"id": "42", "title": "Data Scientist", "company": {"display_name": "Acme"},
                              "location": {"display_name": "London"}, "description": "Hybrid role",
                              "created": "2026-02-03T00:00:00Z", "redirect_url": "https://example.test/42",
                              "salary_min": 50000}]}

    rows = fetch_adzuna(max_pages=1, page_size=10, http_get=fake_get)
    assert rows[0]["source_job_id"] == "42"
    assert rows[0]["salary"] == "50000"
    assert rows[0]["remote"] == "Hybrid"
    assert seen[0][1]["app_id"] == "app-id"
    assert seen[0][1]["app_key"] == "app-key"


def test_adzuna_excludes_predicted_india_salary_from_lpa(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "app-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "app-key")

    rows = fetch_adzuna(country="in", max_pages=1, http_get=lambda *_: {
        "results": [{"id": "predicted", "title": "Engineer", "location": {"display_name": "Pune"},
                      "created": "2026-02-03T00:00:00Z", "redirect_url": "https://example.test/predicted",
                      "salary_min": 1200000, "salary_max": 1800000, "salary_is_predicted": True}]
    })

    assert rows[0]["salary_is_predicted"] is True
    assert rows[0]["salary_min_lpa"] is None
    assert rows[0]["salary_max_lpa"] is None


def test_adzuna_converts_observed_india_salary_to_lpa(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "app-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "app-key")

    rows = fetch_adzuna(country="in", max_pages=1, http_get=lambda *_: {
        "results": [{"id": "observed", "title": "Engineer", "location": {"display_name": "Pune"},
                      "created": "2026-02-03T00:00:00Z", "redirect_url": "https://example.test/observed",
                      "salary_min": 1200000, "salary_max": 1800000, "salary_is_predicted": False}]
    })

    assert rows[0]["salary_min_lpa"] == 12
    assert rows[0]["salary_max_lpa"] == 18


def test_provider_errors_are_clear():
    with pytest.raises(ProviderError, match="data.*not a list"):
        fetch_arbeitnow(http_get=lambda *_: {"data": {}})


def test_greenhouse_fetches_and_normalizes_html_and_dates():
    calls = []
    config = {"boards": [{"name": "Acme", "board_token": "acme"}],
              "india_location_keywords": ["Bengaluru"]}

    def fake_get(url, params):
        calls.append((url, params))
        return {"jobs": [{"id": 123, "title": "Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
                           "location": {"name": "Bengaluru, India"}, "company": {"name": "Acme"},
                           "content": "<p>Build <strong>things</strong>.</p><script>bad()</script>",
                           "updated_at": "2026-03-04T12:00:00Z", "first_published": "2026-03-01T12:00:00Z"}]}

    rows = fetch_greenhouse(config=config, http_get=fake_get)
    assert rows[0]["source_job_id"] == "123"
    assert rows[0]["source_url"].endswith("/123")
    assert rows[0]["description"] == "Build things."
    assert rows[0]["posted_date"] == "2026-03-04"
    assert calls[0][1] == {"content": "true"}


def test_greenhouse_filters_locations_and_reports_board_error():
    errors = []
    config = {"boards": [{"name": "India", "board_token": "india"}, {"name": "Broken", "board_token": "broken"}],
              "india_location_keywords": ["India"]}

    def fake_get(url, params):
        if "broken" in url:
            raise TimeoutError("timed out")
        return {"jobs": [{"id": 1, "title": "US", "absolute_url": "https://x/1", "location": {"name": "New York"}},
                          {"id": 2, "title": "IN", "absolute_url": "https://x/2", "location": {"name": "Remote - India"}}]}

    rows = fetch_greenhouse(config=config, http_get=fake_get, errors=errors)
    assert [row["source_job_id"] for row in rows] == ["2"]
    assert "Broken" in errors[0] and "timed out" in errors[0]


def test_greenhouse_rejects_malformed_response():
    errors = []
    assert fetch_greenhouse(config={"boards": [{"board_token": "acme"}]},
                            http_get=lambda *_: {"jobs": {}}, errors=errors) == []
    assert "jobs' is not a list" in errors[0]


def test_greenhouse_config_requires_explicit_valid_board_list(tmp_path):
    config_path = tmp_path / "boards.json"
    config_path.write_text(json.dumps({"boards": [{"board_token": "acme"}], "india_location_keywords": ["India"]}), encoding="utf-8")
    assert load_greenhouse_config(config_path)["boards"][0]["board_token"] == "acme"
    with pytest.raises(GreenhouseConfigError, match="cannot read Greenhouse config"):
        load_greenhouse_config(tmp_path / "missing.json")


def test_snapshot_is_deduplicated_and_validated(tmp_path):
    row = {"job_id": "1", "source_job_id": "1", "title": "Engineer", "source": "test",
           "source_url": "https://example.test/1", "posted_date": "2026-01-01",
           "collected_at": "2026-01-01T00:00:00Z"}
    output = tmp_path / "jobs.csv"
    assert write_snapshot([row, dict(row, title="newer")], output) == 1
    with output.open(newline="", encoding="utf-8") as handle:
        records = list(csv.DictReader(handle))
    assert len(records) == 1
    assert records[0]["title"] == "newer"
