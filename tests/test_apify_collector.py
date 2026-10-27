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


def test_apify_known_actor_shapes_are_normalized_without_enrichment_fields():
    naukri = normalize_apify_item("naukri", {
        "id": "n-1",
        "title": "ML Engineer",
        "company": {"name": "Example AI"},
        "locations": [{"label": "Bengaluru"}],
        "salary": {"minimum": 1200000, "maximum": 1800000, "currency": "INR", "label": "12-18 Lacs"},
        "description": {"full": "<p>Build models</p>"},
        "skills": {"preferred": ["Python", "SQL"], "other": ["Python"]},
        "createdDateText": "2026-10-27 10:00:00",
        "url": "https://www.naukri.com/jobs/n-1",
    })
    assert naukri["company"] == "Example AI"
    assert naukri["location"] == "Bengaluru"
    assert naukri["salary_min_lpa"] == 12
    assert naukri["salary_max_lpa"] == 18
    assert naukri["skills"] == ["Python", "SQL"]

    indeed = normalize_apify_item("indeed", {
        "refNum": "i-1",
        "title": "Data Scientist",
        "employer": {"name": "Example Data"},
        "location": {"city": "Bengaluru", "countryCode": "IN"},
        "baseSalary": {"min": 1000000, "max": 1500000, "currencyCode": "INR"},
        "description": {"text": "Analyze data"},
        "datePublished": "2026-10-26T00:00:00Z",
        "jobUrl": "https://in.indeed.com/viewjob?jk=i-1",
    })
    assert indeed["source_job_id"] == "indeed:i-1"
    assert indeed["salary_min_lpa"] == 10
    assert indeed["salary_max_lpa"] == 15


def test_apify_alternative_actor_configs_are_supported():
    thirdwatch = {
        "actors": [{"source": "naukri-thirdwatch", "actor": "thirdwatch/naukri-jobs-scraper", "input": {}}],
        "location_keywords": ["Bengaluru"],
    }
    rapid = {
        "actors": [{"source": "linkedin-rapid", "actor": "worldunboxer/rapid-linkedin-scraper", "input": {}}],
        "location_keywords": ["Bengaluru"],
    }
    assert load_apify_config_from_dict(thirdwatch)["actors"][0]["max_total_charge_usd"] == 0.05
    assert load_apify_config_from_dict(rapid)["actors"][0]["max_total_charge_usd"] == 0.05


def test_apify_http_runner_uses_actor_id_and_billing_cap(monkeypatch):
    from collector import apify

    calls = []

    class Response:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit):
            return self.body

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            return Response(b'{"data":{"defaultDatasetId":"dataset-1"}}')
        return Response(b"[]")

    monkeypatch.setattr(apify, "urlopen", fake_urlopen)
    assert apify._http_runner("valig/linkedin-jobs-scraper", {}, "secret", 5, max_total_charge_usd=0.05) == []
    assert "/acts/valig~linkedin-jobs-scraper/runs" in calls[0]
    assert "maxTotalChargeUsd=0.05" in calls[0]


def load_apify_config_from_dict(value):
    from collector.apify import _validate_config
    return _validate_config(value)


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
