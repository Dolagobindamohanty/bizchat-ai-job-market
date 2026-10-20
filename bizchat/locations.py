"""Canonical city tags derived from, without rewriting, source locations."""

from __future__ import annotations

import re


CITY_ALIASES: dict[str, tuple[str, ...]] = {
    "Bengaluru": ("bengaluru", "bangalore", "blr"),
    "Mumbai": ("mumbai", "bombay"),
    "Delhi": ("new delhi", "delhi"),
    "Gurugram": ("gurugram", "gurgaon"),
    "Hyderabad": ("hyderabad", "hyd"),
    "Pune": ("pune", "poona"),
    "Chennai": ("chennai", "madras"),
    "Kolkata": ("kolkata", "calcutta"),
    "Noida": ("noida",),
    "Ahmedabad": ("ahmedabad", "amdavad"),
    "Chandigarh": ("chandigarh",),
    "Jaipur": ("jaipur",),
    "Kochi": ("kochi", "cochin"),
    "Thiruvananthapuram": ("thiruvananthapuram", "trivandrum"),
    "Coimbatore": ("coimbatore",),
    "Indore": ("indore",),
    "Nagpur": ("nagpur",),
    "Lucknow": ("lucknow",),
    "Bhubaneswar": ("bhubaneswar",),
    "Visakhapatnam": ("visakhapatnam", "vizag"),
    "Vadodara": ("vadodara", "baroda"),
    "Surat": ("surat",),
    "Mysuru": ("mysuru", "mysore"),
    "Mangaluru": ("mangaluru", "mangalore"),
    "Nashik": ("nashik", "nasik"),
    "Bhopal": ("bhopal",),
    "Patna": ("patna",),
    "Ranchi": ("ranchi",),
    "Mohali": ("mohali",),
    "Faridabad": ("faridabad",),
    "Ghaziabad": ("ghaziabad",),
}
_CITY_PATTERNS = {
    city: re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, aliases)) + r")(?!\w)", re.IGNORECASE)
    for city, aliases in CITY_ALIASES.items()
}


def city_tags(location: object) -> list[str]:
    """Return unique recognized Indian cities in source order.

    Semicolons and slashes separate locations; commas remain city/country
    punctuation. Campus and country suffixes do not change a recognized city.
    Country-only, remote-India, missing and unknown locations have no city tags.
    No country or city is inferred from substrings such as ``Indiana``.
    """
    if not isinstance(location, str):
        return []
    cities: list[str] = []
    for part in re.split(r"[;/]", location):
        matches = sorted(
            (match.start(), city)
            for city, pattern in _CITY_PATTERNS.items()
            if (match := pattern.search(part)) is not None
        )
        for _, city in matches:
            if city not in cities:
                cities.append(city)
    return cities


def matches_city(location: object, query: str, cities: object = None) -> bool:
    """Match canonical membership, retaining exact fallback for unknown places."""
    requested = city_tags(query)
    if requested:
        tags = cities if isinstance(cities, list) else city_tags(location)
        return any(city in tags for city in requested)
    return isinstance(location, str) and location.strip().casefold() == query.strip().casefold()
