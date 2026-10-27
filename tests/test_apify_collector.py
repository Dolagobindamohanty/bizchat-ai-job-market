import json

import pytest

from collector.apify import (ApifyConfigError, collect_apify_sources,
                              load_apify_config, normalize_apify_item)


def config(*sources):
    actors = {
        "naukri": {"source": "naukri", "actor": "valig/naukri-jobs-scraper", "input": {"location": "Bengaluru"}},
        "linkedin": {"source": "linkedin", "actor": "valig/linkedin-jobs-scraper", "input": {"location": "Bengaluru"}},
        "indeed": {"source": "indeed", "actor": "valig/indeed-jobs-scraper", "input": {"country": "in"}},
    }
    return {"actors": [actors[source] for source in sources], "location_keywords": ["India", "Bengaluru"]}


def item(**overrides):
    value = {
        "jobId": "120526025748",
        "title": "Software Developer",
        "companyName": "Example Technologies",
        "location": "Bengaluru, India",
        "description": "Build Python and SQL services.",
        "salary_min_lpa": 8,
        "salary_max_lpa": 14,
        "job_type": "full-time",
        "postedDate": "2026-10-27T10:00:00Z",
        "url": "https://example.test/jobs/120526025748",
    }
    value.update(overrides)
    return value


def test_apify_item_normalizes_provenance_and_observed_status():
    row = normalize_apify_item("naukri", item())
    assert row["source_job_id"] == "naukri:120526025748"
    assert row["salary_min_lpa"] == 8
    assert row["salary_max_lpa"] == 14
    assert row["employment_type"] == "full-time"
    assert row["source_type"] == "third_party_collector"
    assert row["listing_status"] == "unknown"
    assert row["verification_status"] == "observed"
    assert row["last_verified_at"] is None


def test_apify_invalid_or_missing_source_url_is_not_published():
    assert normalize_apify_item("indeed", item(url="http://insecure.test/job")) is None
    assert normalize_apify_item("indeed", item(url=None)) is None
    assert normalize_apify_item("indeed", item(title="")) is None


def test_apify_actors_are_isolated_and_location_scoped():
    calls = []

    def runner(actor, payload, token, timeout):
        calls.append((actor, payload, token))
        if actor == "valig/linkedin-jobs-scraper":
            raise RuntimeError("actor unavailable")
        return [item()] if actor == "valig/naukri-jobs-scraper" else [item(location="New York")]

    rows, reports = collect_apify_sources(config("naukri", "linkedin", "indeed"), token="secret", runner=runner)
    assert len(rows) == 1
    assert reports[0]["status"] == "ok"
    assert reports[0]["jobs"] == 1
    assert reports[1]["status"] == "failed"
    assert reports[2]["status"] == "ok"
    assert reports[2]["jobs"] == 0
    assert calls[0][2] == "secret"


def test_apify_config_rejects_unapproved_actor(tmp_path):
    path = tmp_path / "apify.json"
    path.write_text(json.dumps({"actors": [{"source": "naukri", "actor": "other/actor", "input": {}}]}), encoding="utf-8")
    with pytest.raises(ApifyConfigError, match="does not match"):
        load_apify_config(path)
