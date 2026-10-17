"""Static startup contracts only: these checks do not parse or execute JavaScript."""

from html.parser import HTMLParser
from pathlib import Path
import re

import pytest


FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


class PageContract(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.add(attrs["id"])
        if tag == "script":
            self.scripts.append((attrs.get("src"), self.ids.copy()))


def assert_job_table_contract(source):
    table = source.split("function jobTable(list)", 1)[1].split(
        "function watchList", 1
    )[0]
    # Keep row generation separate from the quoted empty-state fallback. This
    # specifically catches the missing quote that prevented the script parsing.
    assert re.search(r"const rows\s*=\s*list\.map\(.*?\)\.join\(''\);", table, re.S)
    assert re.search(
        r"return panel\('Latest roles',\s*"
        r"'(?P<header><div class=\"table-head\">.*?)'\s*\+\s*"
        r"\(rows\s*\|\|\s*'<p class=\"empty\">No roles match these filters\.</p>'\),"
        r"\s*'View all'\);",
        table,
        re.S,
    )
    for field in ("title", "posted", "company", "city", "source"):
        assert f"escapeHTML(j.{field})" in table
    assert 'class="job-row"' in table


def test_job_table_static_regression_contract():
    assert_job_table_contract((FRONTEND / "app.js").read_text(encoding="utf-8"))


def test_job_table_guard_rejects_missing_empty_state_quote():
    source = (FRONTEND / "app.js").read_text(encoding="utf-8")
    broken = source.replace(
        "No roles match these filters.</p>'", "No roles match these filters.</p>", 1
    )
    assert broken != source
    with pytest.raises(AssertionError):
        assert_job_table_contract(broken)


def test_public_page_supplies_startup_dom_before_script():
    source = (FRONTEND / "app.js").read_text(encoding="utf-8")
    page = PageContract()
    page.feed((FRONTEND / "index.html").read_text(encoding="utf-8"))
    # Learning-view controls are created dynamically; these are the fixed DOM
    # elements used by setup, filtered, render, and load on the public page.
    required = {
        "navigation", "city-filter", "source-filter", "role-count", "kpis",
        "page-title", "data-status", "refresh-status", "avatar", "view-root",
    }
    scripts = [ids for src, ids in page.scripts if src == "app.js"]
    assert len(scripts) == 1
    assert required <= scripts[0]
    assert re.search(r"let jobs\s*=\s*SAMPLE,\s*view\s*=\s*'Overview'", source)
    assert re.search(
        r"async function load\(\)\s*\{\s*const requestId\s*=\s*\+\+loadRequestId;"
        r"\s*setup\(\);\s*render\(\);\s*try\s*\{"
        r"\s*const response\s*=\s*await fetchAPI\('/jobs\?page_size=100'\)",
        source,
    )
    assert "bindAccount();load();" in source
    assert "${jobTable(list.slice(0,6))}" in source


def test_salaries_static_expression_contract():
    source = (FRONTEND / "app.js").read_text(encoding="utf-8")
    # Guard the reviewed delimiter order: denominator, round, row, map, bars,
    # panel. This is a targeted source assertion, not a JS syntax checker.
    expression = (
        "${panel('Salary by city',bars(cities.map(([c])=>[c,Math.round("
        "list.filter(j=>j.city===c).reduce((a,j)=>a+(j.min+j.max)/2,0)/"
        "(list.filter(j=>j.city===c).length||1))]),'#f59e0b','L'))}"
    )
    assert expression in source
