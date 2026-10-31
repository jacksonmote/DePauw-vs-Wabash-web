"""The tiger easter egg: every fact is in the page, one shows without JavaScript (picked from the Eastern date), and
the script shows a different random one on each open. Fixtures only: no real clock, no network, no live fact text."""

import datetime as dt
import json
import re
import shutil
import subprocess
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


def test_the_summary_art_and_closing_line_are_kept():
    tiger = note(home(at("2026-11-14")))
    assert tiger.summary.get_text(" ", strip=True) == "A tiger with something to say"
    assert tiger.summary.find("img")["src"] == "easter-tiger.svg"
    assert tiger.find_all("p", recursive=False)[-1].get_text() == "No further questions."
    assert tiger.find("ul").find_next_sibling("p") is not None          # the line comes after the fact


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

NODE = shutil.which("node")

HARNESS = r"""
const fs = require("fs");
const source = fs.readFileSync(process.argv[1], "utf8");
const count = Number(process.argv[2]);
const randoms = JSON.parse(process.argv[3]);
const handlers = {};
const facts = Array.from({ length: count }, (_, i) => ({ hidden: i !== 0 }));
const note = { open: false, querySelectorAll: () => facts, addEventListener: (type, fn) => { handlers[type] = fn; } };
const document = {
  getElementById: () => null,
  querySelector: (selector) => (selector === ".tiger-note" && count >= 0 ? note : null),
};
let k = 0;
const math = Object.create(Math);
math.random = () => randoms[k++ % randoms.length];
new Function("document", "Math", source)(document, math);
const shownLog = [];
if (!handlers.toggle) { console.log(JSON.stringify({ shownLog, hasHandler: false })); process.exit(0); }
for (let n = 0; n < 12; n++) {
  note.open = true; handlers.toggle(); shownLog.push(facts.map((f, i) => (f.hidden ? null : i)).filter((i) => i !== null));
  note.open = false; handlers.toggle();
}
console.log(JSON.stringify({ shownLog, hasHandler: Boolean(handlers.toggle) }));
"""


def run_script(count, randoms):
    result = subprocess.run([NODE, "-e", HARNESS, str(ROOT / "static" / "easter-eggs.js"), str(count), json.dumps(randoms)],
                            capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_opening_shows_exactly_one_fact_and_two_opens_in_a_row_never_repeat():
    # randoms that would repeat the visible fact if the script did not skip it
    out = run_script(5, [0.0, 0.0, 0.99, 0.99, 0.5, 0.5, 0.2, 0.2, 0.0])
    shown = [entry for entry in out["shownLog"]]
    assert all(len(entry) == 1 for entry in shown)                    # exactly one visible fact per open
    flat = [entry[0] for entry in shown]
    assert all(a != b for a, b in zip(flat, flat[1:]))                # never the same fact twice in a row
    assert flat[0] != 0                                               # the first open differs from the no-JS fact


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_closing_the_disclosure_changes_nothing_and_a_single_fact_page_is_left_alone():
    out = run_script(3, [0.0, 0.5, 0.9])
    assert out["hasHandler"] is True
    assert run_script(1, [0.0])["hasHandler"] is False                # one fact: nothing to rotate, so no handler at all


def test_the_script_has_no_network_storage_or_dynamic_html_and_still_has_one_click_handler():
    script = (ROOT / "static" / "easter-eggs.js").read_text(encoding="utf-8")
    for banned in ("fetch(", "XMLHttpRequest", "localStorage", "sessionStorage", "document.cookie", "innerHTML",
                   "eval(", "new Function", "import("):
        assert banned not in script
    assert script.count('addEventListener("click"') == 1
    assert 'addEventListener("toggle"' in script
    assert re.search(r"if \(!note \|\| facts\.length < 2\) return;", script)       # no elements: it does nothing
