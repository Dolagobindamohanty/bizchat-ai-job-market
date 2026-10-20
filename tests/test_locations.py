import pandas as pd
import pytest

from bizchat.analytics import prepare_jobs, recommendations, top_cities
from bizchat.locations import city_tags, matches_city


@pytest.mark.parametrize("location", [
    "Bangalore", "Bangalore India", "Bengaluru", "Bengaluru India",
    "Bengaluru-VTP India", "India Bangalore", "Remote Bangalore India",
    "BLR", "Bengaluru, India", "Bangalore (Whitefield campus)",
])
def test_bengaluru_aliases(location):
    assert city_tags(location) == ["Bengaluru"]
    assert matches_city(location, "Bengaluru, India")
    assert matches_city(location, "Bangalore")


@pytest.mark.parametrize("location", ["Indiana", "Remote - India", "India", "Unknown", "Bangalorean", "BLRoffice", None, float("nan")])
def test_no_city_inferred_from_country_unknown_or_substring(location):
    assert city_tags(location) == []
    assert not matches_city(location, "Bengaluru")


def test_multi_city_tags_are_unique_and_ordered():
    assert city_tags("Bangalore, India; Mumbai, India / Bengaluru-VTP India") == ["Bengaluru", "Mumbai"]
    assert city_tags("Mumbai / Bangalore") == ["Mumbai", "Bengaluru"]
    assert city_tags("Gurgaon; Bombay; New Delhi / Delhi") == ["Gurugram", "Mumbai", "Delhi"]
    assert matches_city("Unlisted Place", "unlisted place")
    assert not matches_city("Indiana", "India")


def test_preparation_and_analytics_preserve_raw_locations_and_count_memberships():
    raw = pd.DataFrame([
        {"job_id": str(i), "title": "Engineer", "description": skill,
         "location": location, "posted_date": "2026-10-01"}
        for i, (location, skill) in enumerate([
            ("Bangalore; Mumbai", "Python"), ("Bengaluru-VTP India", "SQL"),
            ("Pune", "Docker"), ("India", ""), ("Unknown", ""),
        ])
    ])
    original = raw.copy(deep=True)
    jobs = prepare_jobs(raw)
    pd.testing.assert_frame_equal(raw, original)
    assert jobs["location"].tolist() == raw["location"].tolist()
    assert jobs.loc[0, "cities"] == ["Bengaluru", "Mumbai"]
    assert top_cities(jobs).set_index("location")["jobs"].to_dict() == {
        "Bengaluru": 2, "Mumbai": 1, "Pune": 1, "India": 1, "Unknown": 1,
    }
    for prepared in [jobs, jobs.drop(columns="cities")]:
        recs = recommendations(prepared, target_city="Bengaluru, India")
        assert set(recs["skill"]) == {"Python", "SQL"}
        assert set(recs["demand"]) == {1}
        assert set(recs["share"]) == {0.5}
        assert top_cities(prepared).equals(top_cities(jobs))
