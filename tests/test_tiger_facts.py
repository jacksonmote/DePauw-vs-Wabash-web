"""The tiger easter egg: every fact is in the page, one shows without JavaScript (picked from the Eastern date), and
the script shows a different random one on each open. Fixtures only: no real clock, no network, no live fact text."""

import datetime as dt
import json
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
import funfacts  # noqa: E402
import render  # noqa: E402

HOME_CSP = ("default-src 'none'; style-src 'self'; img-src 'self'; font-src 'self'; manifest-src 'self'; "
            "base-uri 'none'; form-action 'none'; object-src 'none'; script-src 'self'")


def fixture_facts(n=7):
    facts = []
    for i in range(n):
        school = ("depauw", "wabash", "both")[i % 3]
        sources = [{"label": f"Source {i}", "url": f"https://example.com/fact-{i}"}]
        if i == 1:
            sources.append({"label": "Second", "url": "https://example.com/fact-1b"})
        facts.append({"id": f"fact-{i}", "school": school, "text": f"Fixture fact number {i}.", "sources": sources})
    return {"schema_version": 1, "verified_at": "2026-10-31", "facts": facts}


SERIES = ROOT / "tests" / "fixtures" / "series-frozen-2026-10-30.json"


def home(now, facts=None):
    data = render.load(SERIES)
    return render.render_index(data, now=now, upcoming={}, fun_facts_data=facts or fixture_facts())


def note(html):
    return BeautifulSoup(html, "html.parser").select_one("details.tiger-note")


def at(date_text, hour=17):
    """A UTC time that falls on this Eastern calendar date."""
    day = dt.date.fromisoformat(date_text)
    return dt.datetime(day.year, day.month, day.day, hour, tzinfo=dt.timezone.utc)


def visible(tiger):
    return [li for li in tiger.select("li.tiger-fact") if not li.has_attr("hidden")]


def test_every_fact_is_in_the_page_with_its_school_label_and_source_links():
    tiger = note(home(at("2026-11-14")))
    items = tiger.select("ul.tiger-facts > li.tiger-fact")
    facts = fixture_facts()["facts"]
    assert len(items) == len(facts)
    for item, fact in zip(items, facts):
        assert fact["text"] in item.get_text(" ", strip=True)
        assert item.select_one(".tiger-fact-school").get_text() == funfacts.SCHOOL_LABELS[fact["school"]]
        links = [a["href"] for a in item.select(".tiger-fact-source a")]
        assert links == [s["url"] for s in fact["sources"]]
    assert [a.get_text() for a in items[1].select("a")] == ["Source 1", "Source 2"]      # several sources are numbered
    assert [a.get_text() for a in items[0].select("a")] == ["Source"]
    assert items[0].select_one("a")["aria-label"] == "Source: Source 0"


def test_the_disclosure_is_the_summary_the_art_and_the_facts_with_no_closing_line():
    tiger = note(home(at("2026-11-14")))
    assert tiger.summary.get_text(" ", strip=True) == "A tiger with something to say"
    assert tiger.summary.find("img")["src"] == "easter-tiger.svg"
    assert [child.name for child in tiger.find_all(True, recursive=False)] == ["summary", "ul"]
    assert tiger.find("p") is None and "No further questions" not in str(tiger)


def test_each_school_label_ends_with_a_colon_and_both_facts_say_both_schools():
    facts = {"schema_version": 1, "verified_at": "2026-10-31", "facts": [
        {"id": "a", "school": "depauw", "text": "One.", "sources": [{"label": "S", "url": "https://example.com/a"}]},
        {"id": "b", "school": "wabash", "text": "Two.", "sources": [{"label": "S", "url": "https://example.com/b"}]},
        {"id": "c", "school": "both", "text": "Three.", "sources": [{"label": "S", "url": "https://example.com/c"}]}]}
    tiger = note(home(at("2026-11-14"), facts))
    assert [s.get_text() for s in tiger.select(".tiger-fact-school")] == ["DePauw:", "Wabash:", "Both Schools:"]
    assert funfacts.SCHOOL_LABELS == {"depauw": "DePauw:", "wabash": "Wabash:", "both": "Both Schools:"}
    first = tiger.select_one("li.tiger-fact").get_text(" ", strip=True)
    assert first.startswith("DePauw: One.")                           # the label stays a separate element before the text


def test_exactly_one_fact_is_visible_without_javascript_and_the_rest_use_the_hidden_attribute():
    tiger = note(home(at("2026-11-14")))
    items = tiger.select("li.tiger-fact")
    assert len(visible(tiger)) == 1
    assert sum(item.has_attr("hidden") for item in items) == len(items) - 1
    assert all(not item.has_attr("style") for item in items)


def test_the_fallback_fact_follows_the_eastern_date_and_is_stable_for_a_given_date():
    facts = fixture_facts()
    count = len(facts["facts"])
    shown = []
    for n in range(count):
        day = dt.date(2026, 11, 14) + dt.timedelta(days=n)
        tiger = note(home(at(day.isoformat()), facts))
        index = [i for i, li in enumerate(tiger.select("li.tiger-fact")) if not li.has_attr("hidden")]
        assert index == [funfacts.fallback_index(count, day)]
        shown.append(index[0])
    assert sorted(shown) == list(range(count))                      # a different fact each day, every fact in a cycle
    morning, evening = note(home(at("2026-11-14", 10))), note(home(at("2026-11-14", 23)))   # same Eastern date
    assert visible(morning)[0].get_text() == visible(evening)[0].get_text()
    assert str(home(at("2026-11-14"))) == str(home(at("2026-11-14")))                      # a rebuild is identical


def test_the_date_is_the_eastern_date_not_the_utc_date():
    late_evening_eastern = dt.datetime(2026, 11, 15, 2, 30, tzinfo=dt.timezone.utc)       # Nov 14, 9:30 PM Eastern
    facts = fixture_facts()
    expected = funfacts.fallback_index(len(facts["facts"]), dt.date(2026, 11, 14))
    items = note(home(late_evening_eastern, facts)).select("li.tiger-fact")
    assert [i for i, li in enumerate(items) if not li.has_attr("hidden")] == [expected]


def test_the_page_has_no_inline_styles_no_inline_scripts_and_the_csp_is_unchanged():
    html = home(at("2026-11-14"))
    soup = BeautifulSoup(html, "html.parser")
    assert not soup.find_all("style")
    assert all(not el.has_attr("style") for el in soup.find_all(True))
    assert all(script.has_attr("src") and not script.get_text().strip() for script in soup.find_all("script"))
    assert soup.find("meta", attrs={"http-equiv": "Content-Security-Policy"})["content"] == HOME_CSP
    assert render.CSP + "; script-src 'self'" == HOME_CSP


def test_the_built_site_has_the_committed_facts_and_no_new_files(tmp_path):
    out = tmp_path / "site"
    render.build(SERIES, out, now=at("2026-11-14"), upcoming_path=out / "missing.json")
    committed = funfacts.validate(funfacts.load_json(ROOT / "data" / "fun-facts.json"))["facts"]
    tiger = note((out / "index.html").read_text(encoding="utf-8"))
    assert len(tiger.select("li.tiger-fact")) == len(committed)
    assert len(visible(tiger)) == 1
    for fact_ in committed:                                      # every committed fact has its source link in the page
        for source in fact_["sources"]:
            assert tiger.find("a", href=source["url"]) is not None
    assert not (out / "fun-facts.json").exists()
    for built in out.rglob("*.html"):                              # the closing line is gone from every page
        assert "No further questions" not in built.read_text(encoding="utf-8"), built


def test_a_bad_facts_file_fails_the_build_before_any_output(tmp_path):
    bad = tmp_path / "bad.json"
    value = fixture_facts()
    value["facts"][0]["sources"] = []
    bad.write_text(json.dumps(value), encoding="utf-8")
    out = tmp_path / "site"
    with pytest.raises(funfacts.FunFactsError):
        render.build(SERIES, out, now=at("2026-11-14"), upcoming_path=tmp_path / "none.json", fun_facts_path=bad)
    assert not out.exists()


# ---- the script ----------------------------------------------------------------------------------------------------
# tests/conftest.py forbids running node from a test (the CI "node --check" step and a hand check cover the script's
# behavior), so these read the script's text for the rules that make it behave.

def test_the_script_rotates_one_random_fact_per_open_and_never_repeats_the_last():
    script = (ROOT / "static" / "easter-eggs.js").read_text(encoding="utf-8")
    assert 'note.addEventListener("toggle"' in script and "if (!note.open) return;" in script   # acts on open, not close
    assert "Math.random()" in script and "while (next === last)" in script                        # random, never the last one
    assert "facts.forEach((fact, i) => { fact.hidden = i !== next; });" in script                   # exactly one shown, the rest hidden
    assert "let last = facts.findIndex((fact) => !fact.hidden);" in script                          # the first open differs from the no-JS fact


def test_the_script_has_no_network_storage_or_dynamic_html_and_still_has_one_click_handler():
    script = (ROOT / "static" / "easter-eggs.js").read_text(encoding="utf-8")
    for banned in ("fetch(", "XMLHttpRequest", "localStorage", "sessionStorage", "document.cookie", "innerHTML",
                   "eval(", "new Function", "import("):
        assert banned not in script
    assert script.count('addEventListener("click"') == 1
    assert 'addEventListener("toggle"' in script
    assert re.search(r"if \(!note \|\| facts\.length < 2\) return;", script)       # no elements: it does nothing
