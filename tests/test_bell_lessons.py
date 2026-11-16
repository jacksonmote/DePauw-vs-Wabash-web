"""The fictional /bell-heist/ page's two sections from the real heist stories: the tactic tags and their validation,
the checklist built from the tags and each story's outcome, the odds box and its year arithmetic, and the rule that
the page says only how past attempts ended."""
import copy
import datetime as dt
import html
import json
import re
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
import heists  # noqa: E402
import render  # noqa: E402

NOW = dt.datetime(2026, 12, 1, 15, 0, tzinfo=dt.timezone.utc)
TODAY = dt.date(2026, 12, 1)
HISTORY = "../heist-details/"
SRC = [{"label": "A source", "url": "https://example.com/story"}]
BASE_CSP = ("default-src 'none'; style-src 'self'; img-src 'self'; font-src 'self'; manifest-src 'self'; "
            "base-uri 'none'; form-action 'none'; object-src 'none'")
FIRST, SECOND, THIRD = list(heists.TACTICS)[:3]


def incident(iid, year, outcome="stolen", **kw):
    base = {"id": iid, "year": year, "school": "not-recorded", "outcome": outcome, "rating": "reported",
            "title": f"Title {iid}", "summary": "A summary.", "counted_in_theft_total": outcome == "stolen",
            "sources": copy.deepcopy(SRC)}
    base.update(kw)
    return base


def small(incidents):
    return {"schema_version": 1, "verified_at": "2026-10-30", "intro": "Intro.", "method": ["Method."],
            "guarding": {"paragraphs": ["Guarded."], "sources": copy.deepcopy(SRC)}, "incidents": incidents}


# Out of order on purpose, like the real file may be. The last theft is 1998; three attempts and one hiding follow.
SAMPLE = small([
    incident("masks", 2017, "foiled", tactics=[SECOND], lesson="it ended with police outside"),
    incident("first", 1941),
    incident("posed", 1965, tactics=[FIRST]),
    incident("posed-again", 1959, tactics=[FIRST]),
    incident("stayed-late", 1988, tactics=[SECOND]),
    incident("halloween", 1998, tactics=[SECOND]),
    incident("alarmed", 2000, "foiled"),
    incident("handle", 2012, "foiled", tactics=[THIRD, FIRST]),
    incident("own-goal", 2005, "hidden"),
    incident("undated", None, "foiled"),
])


def real():
    return heists.load_json(ROOT / "data" / "bell-heists.json")


def broken(index, **changes):
    data = copy.deepcopy(SAMPLE)
    data["incidents"][index].update(changes)
    return data


# ---- the tags and their validation ----

def test_the_tactic_keys_are_a_small_fixed_set_with_a_name_each():
    assert 3 <= len(heists.TACTICS) <= 8
    for key, step in heists.TACTICS.items():
        assert re.fullmatch(r"[a-z]+(-[a-z]+)*", key) and step.strip() and step[0].isupper() and step[-1].isalnum()
    assert set(heists.TACTIC_RESULTS) == set(heists.OUTCOMES)             # a word for every outcome a story can have
    assert set(heists.ATTEMPTS) < set(heists.OUTCOMES)


def test_the_sample_and_the_committed_file_are_valid_and_every_tag_is_a_known_key():
    assert heists.validate(copy.deepcopy(SAMPLE))
    data = heists.validate(real())
    for story in data["incidents"]:
        assert set(story.get("tactics", [])) <= set(heists.TACTICS), story["id"]
        assert "lesson" not in story or story.get("tactics"), story["id"]


@pytest.mark.parametrize("value", ["tunnel", "Impersonation", "", None, 3, ["impersonation"]])
def test_an_unknown_tactic_key_is_refused_and_named(value):
    with pytest.raises(heists.HeistError, match="unknown tactic"):
        heists.validate(broken(2, tactics=[FIRST, value]))


@pytest.mark.parametrize(("value", "match"), [("impersonation", "non-empty list"), ([], "non-empty list"),
                                               (None, "non-empty list"), ({"impersonation": True}, "non-empty list")])
def test_tactics_must_be_a_list_with_something_in_it(value, match):
    with pytest.raises(heists.HeistError, match=match):
        heists.validate(broken(2, tactics=value))


def test_a_tactic_listed_twice_is_refused():
    with pytest.raises(heists.HeistError, match="twice"):
        heists.validate(broken(2, tactics=[FIRST, FIRST]))


def test_a_tag_on_a_story_with_no_year_is_refused_because_a_lesson_names_the_year():
    with pytest.raises(heists.HeistError, match="needs a year"):
        heists.validate(broken(9, tactics=[FIRST]))                       # index 9 is the undated story


def test_lesson_words_need_a_tag_a_sane_length_and_no_closing_punctuation():
    with pytest.raises(heists.HeistError, match="only used with tactics"):
        heists.validate(broken(1, lesson="it worked"))                    # index 1 has no tactics
    for words in ("it ended badly.", "it ended badly;", " it ended badly", "x" * (heists.LESSON_MAX + 1)):
        with pytest.raises(heists.HeistError, match="lesson"):
            heists.validate(broken(0, lesson=words))
    for words in ("", "   ", 7):
        with pytest.raises(heists.HeistError, match="lesson"):
            heists.validate(broken(0, lesson=words))
    assert heists.validate(broken(0, lesson="x" * heists.LESSON_MAX))


# ---- the checklist ----

def test_the_checklist_is_built_from_the_tags_in_tactic_order_with_each_storys_outcome():
    found = heists.lessons(SAMPLE)
    assert [lesson["key"] for lesson in found] == [FIRST, SECOND, THIRD]
    assert [[i["id"] for i in lesson["incidents"]] for lesson in found] == [
        ["posed-again", "posed", "handle"], ["stayed-late", "halloween", "masks"], ["handle"]]
    assert [heists.lesson_text(lesson) for lesson in found] == [
        f"{heists.TACTICS[FIRST]}: worked in 1959 and 1965; failed in 2012.",
        f"{heists.TACTICS[SECOND]}: worked in 1988 and 1998; in 2017 it ended with police outside.",
        f"{heists.TACTICS[THIRD]}: failed in 2012."]


def test_a_changed_outcome_or_tag_changes_the_lesson_and_nothing_is_typed_in():
    changed = copy.deepcopy(SAMPLE)
    changed["incidents"][4].update(outcome="foiled", counted_in_theft_total=False)       # 1988 now failed
    assert heists.lesson_text(heists.lessons(heists.validate(changed))[1]) == (
        f"{heists.TACTICS[SECOND]}: failed in 1988; worked in 1998; in 2017 it ended with police outside.")
    hidden = copy.deepcopy(SAMPLE)
    hidden["incidents"][8]["tactics"] = [THIRD]                                          # the 2005 hiding
    assert heists.lesson_text(heists.lessons(heists.validate(hidden))[2]) == (
        f"{heists.TACTICS[THIRD]}: {heists.TACTIC_RESULTS['hidden']} in 2005; failed in 2012.")
    three = small([incident("a", 1950, tactics=[FIRST]), incident("b", 1960, tactics=[FIRST]),
                   incident("c", 1970, tactics=[FIRST])])
    assert heists.lesson_text(heists.lessons(three)[0]).endswith(": worked in 1950, 1960, and 1970.")   # serial comma
    untagged = copy.deepcopy(SAMPLE)
    for story in untagged["incidents"]:
        story.pop("tactics", None), story.pop("lesson", None)
    assert heists.lessons(untagged) == [] and heists.lessons_html(untagged, HISTORY) == ""


def test_a_story_that_is_not_in_the_file_is_not_in_the_checklist():
    """The tags live on the stories themselves, so a tag cannot point at a story that does not exist: take a story
    out and it leaves the checklist, and a tactic with no story left goes with it."""
    without = copy.deepcopy(SAMPLE)
    without["incidents"] = [i for i in without["incidents"] if i["id"] != "handle"]
    found = heists.lessons(without)
    assert [lesson["key"] for lesson in found] == [FIRST, SECOND]
    assert "2012" not in heists.lessons_html(without, HISTORY)
    assert "handle" not in heists.lessons_html(without, HISTORY)


def steps(markup):
    """Each step of the checklist as (title, pill, line), plus the list element itself."""
    plan = BeautifulSoup(markup, "html.parser").select_one("#lessons .heist-plan")
    rows = plan.find_all("li", recursive=False)
    for row in rows:
        title, record, result = row.find_all(recursive=False)                # three lines: title, pill, result
        assert [title.name, record.name, result.name] == ["strong", "div", "div"]
        assert row.contents[0] is title                                       # nothing sits before the title
        assert [child.name for child in record.contents] == ["span"]          # the pill has a line to itself
    return plan, [(row.strong.get_text(), row.span.get_text(), row.find_all("div", recursive=False)[1].get_text())
                  for row in rows]


def test_each_lesson_is_a_numbered_step_with_its_record_and_one_line_that_links_every_year_to_its_card():
    markup = heists.lessons_html(SAMPLE, HISTORY)
    section = BeautifulSoup(markup, "html.parser")
    assert section.find("h2").get_text() == heists.LESSONS_TITLE and section.find("h3") is None
    assert section.section["aria-labelledby"] == section.find("h2")["id"]
    plan, rows = steps(markup)
    assert plan.name == "ol" and section.find("dl") is None and section.find("ul") is None    # a numbered list
    assert rows == [
        (heists.TACTICS[FIRST], "Record: 2–1", "Worked in 1959 and 1965; failed in 2012."),
        (heists.TACTICS[SECOND], "Record: 2–1", "Worked in 1988 and 1998; in 2017 it ended with police outside."),
        (heists.TACTICS[THIRD], "Record: 0–1", "Failed in 2012.")]
    assert all(span["class"] == ["heist-tag", "nowrap"] for span in plan.select("li > div > span"))   # the history page's pill
    assert len(plan.select("li > div > span")) == len(rows) and not plan.select("li > span")   # every step, own line
    links = [(a.get_text(), a["href"]) for a in plan.find_all("li", recursive=False)[1].find_all("a")]
    assert links == [("1988", HISTORY + "#heist-stayed-late"), ("1998", HISTORY + "#heist-halloween"),
                     ("2017", HISTORY + "#heist-masks")]
    assert all(not a.has_attr("aria-label") for a in section.find_all("a"))   # the year is the whole name


def lessons_of(*stories):
    """The lessons of a small file whose stories are all tagged with the first tactic."""
    return heists.lessons(heists.validate(small([incident(f"s{n}", 1950 + n, outcome, tactics=[FIRST])
                                                 for n, outcome in enumerate(stories)])))


@pytest.mark.parametrize(("outcomes", "record"), [
    (("stolen", "stolen"), "Record: 2–0"),
    (("stolen", "stolen", "foiled"), "Record: 2–1"),
    (("foiled",), "Record: 0–1"),
    (("foiled", "foiled", "foiled", "stolen"), "Record: 1–3"),
    (("hidden", "hidden"), "Hidden: 2"),                               # every story hid the Bell: no wins or losses
    (("hidden",), "Hidden: 1"),
    (("hidden", "stolen", "foiled", "hidden"), "Record: 1–1"),          # a hidden Bell beside others is left out
    (("stolen",) * 11 + ("foiled",) * 10, "Record: 11–10"),
])
def test_a_steps_record_is_counted_from_its_stories_outcomes(outcomes, record):
    (lesson,) = lessons_of(*outcomes)
    assert lesson["record"] == record == heists.record_text(lesson["incidents"])
    assert "-" not in record and record.count("–") == (0 if record.startswith("Hidden") else 1)   # a real en dash
    pill = BeautifulSoup(heists.lessons_html(small(lesson["incidents"]), HISTORY), "html.parser").select_one(".heist-tag")
    assert pill.get_text() == record


def test_the_record_follows_the_outcomes_and_the_labels_come_from_the_constants():
    assert [lesson["record"] for lesson in heists.lessons(SAMPLE)] == ["Record: 2–1", "Record: 2–1", "Record: 0–1"]
    changed = copy.deepcopy(SAMPLE)
    changed["incidents"][4].update(outcome="foiled", counted_in_theft_total=False)        # 1988 now failed
    assert [lesson["record"] for lesson in heists.lessons(heists.validate(changed))] == [
        "Record: 2–1", "Record: 1–2", "Record: 0–1"]
    hidden = copy.deepcopy(SAMPLE)
    hidden["incidents"][8]["tactics"] = [list(heists.TACTICS)[3]]                         # the 2005 hiding, alone
    assert heists.lessons(heists.validate(hidden))[-1]["record"] == "Hidden: 1"
    assert (heists.RECORD_LABEL, heists.HIDDEN_LABEL) == ("Record", "Hidden")
    source = (ROOT / "scraper" / "heists.py").read_text(encoding="utf-8")
    assert not re.search(r"\d+–\d+", re.sub(r'""".*?"""|#[^\n]*', "", source, flags=re.S))   # no record typed in the code


def test_the_real_records_add_up_to_the_tagged_stories():
    data = heists.validate(real())
    for lesson in heists.lessons(data):
        outcomes = [i["outcome"] for i in lesson["incidents"]]
        numbers = [int(n) for n in re.findall(r"\d+", lesson["record"])]
        if lesson["record"].startswith(heists.HIDDEN_LABEL):
            assert set(outcomes) == {"hidden"} and numbers == [len(outcomes)]
        else:
            assert numbers == [outcomes.count("stolen"), outcomes.count("foiled")] and sum(numbers) > 0


def test_every_link_in_both_sections_reaches_a_card_on_the_history_page_built_from_the_same_stories():
    for data in (SAMPLE, real()):
        history = "\n".join(heists.render_main(data, [], back_html="", game_href="../#football"))
        both = heists.lessons_html(data, HISTORY) + heists.odds_html(data, TODAY, HISTORY)
        targets = re.findall(r'href="([^"]+)"', both)
        assert targets and all(t.startswith(HISTORY + "#heist-") for t in targets)
        for target in targets:
            assert f'<section class="heist heist-school-' in history
            assert re.search(rf'<section [^>]*id="{re.escape(target.split("#", 1)[1])}"', history), target


def test_the_lesson_words_are_escaped():
    data = small([incident("a", 1950, tactics=[FIRST], lesson="it ended <b>badly</b> & late")])
    assert "<div>In <a href=\"../heist-details/#heist-a\">1950</a> it ended &lt;b&gt;badly&lt;/b&gt; &amp; late.</div>" in (
        heists.lessons_html(heists.validate(data), HISTORY))


# ---- the odds box ----

def test_the_odds_come_from_the_dated_stories():
    found = heists.odds(SAMPLE, TODAY)
    assert found["last"]["id"] == "halloween" and found["years_ago"] == 28 and found["since_year"] == 1999
    assert [i["id"] for i in found["attempts"]] == ["alarmed", "handle", "masks"]   # not the hiding, not the undated one
    assert found["successful"] == 0 and found["stolen"] == 5 and found["first_year"] == 1941
    box = BeautifulSoup(heists.odds_html(SAMPLE, TODAY, HISTORY), "html.parser")
    assert box.find("h2").get_text() == heists.ODDS_TITLE and box.find("h3") is None
    assert box.section["aria-labelledby"] == box.find("h2")["id"]
    assert "heist" not in box.section["class"]                           # the card is drawn once, around both sections
    assert [(row.dt.get_text(), row.dd.get_text()) for row in box.select("dl > div")] == [
        ("Last successful heist", "1998 (28 years ago)"),
        ("Attempts since 1999", "0 for 3 (2000, 2012, and 2017)"),
        ("On the record", f"Stolen at least 5 times since 1941, {heists.ODDS_CAME_BACK}.")]
    assert [a["href"] for a in box.find_all("a")] == [HISTORY + f"#heist-{i}" for i in
                                                     ("halloween", "alarmed", "handle", "masks")]
    assert box.select_one("p.heist-plea").get_text() == heists.ODDS_PLEA and "don’t" in heists.ODDS_PLEA


def test_a_new_theft_moves_every_line_of_the_box():
    newer = copy.deepcopy(SAMPLE)
    newer["incidents"].append(incident("again", 2012))
    found = heists.odds(heists.validate(newer), TODAY)
    assert (found["last"]["id"], found["years_ago"], found["since_year"]) == ("again", 14, 2013)
    assert [i["id"] for i in found["attempts"]] == ["masks"] and found["stolen"] == 6
    rows = BeautifulSoup(heists.odds_html(newer, TODAY, HISTORY), "html.parser").select("dl > div")
    assert [(row.dt.get_text(), row.dd.get_text()) for row in rows] == [
        ("Last successful heist", "2012 (14 years ago)"), ("Attempts since 2013", "0 for 1 (2017)"),
        ("On the record", f"Stolen at least 6 times since 1941, {heists.ODDS_CAME_BACK}.")]


def test_the_box_without_later_attempts_or_without_any_theft():
    quiet = small([incident("only", 1998), incident("own-goal", 2005, "hidden")])
    rows = BeautifulSoup(heists.odds_html(quiet, TODAY, HISTORY), "html.parser").select("dl > div")
    assert rows[1].get_text(" ") == "Attempts since 1999 None on record"
    assert rows[2].dd.get_text().startswith("Stolen at least 1 time since 1998,")
    none = small([incident("tried", 2012, "foiled"), incident("undated", None)])       # the one theft has no year
    assert heists.odds(none, TODAY) is None and heists.odds_html(none, TODAY, HISTORY) == ""


@pytest.mark.parametrize(("today", "years", "words"), [
    (dt.date(2026, 12, 31), 28, "28 years ago"), (dt.date(2027, 1, 1), 29, "29 years ago"),
    (dt.date(1999, 6, 1), 1, "1 year ago"), (dt.date(1998, 12, 31), 0, "this year"), (dt.date(1997, 1, 1), -1, "")])
def test_years_ago_counts_calendar_years_from_the_date_it_is_given(today, years, words):
    found = heists.odds(SAMPLE, today)
    assert found["years_ago"] == years and heists.years_ago_text(years) == words
    cell = BeautifulSoup(heists.odds_html(SAMPLE, today, HISTORY), "html.parser").select_one("dl > div dd").get_text()
    assert cell == (f"1998 ({words})" if words else "1998")


@pytest.mark.parametrize(("now", "words"), [
    (dt.datetime(2027, 1, 1, 4, 59, tzinfo=dt.timezone.utc), "28 years ago"),    # 11:59 PM Eastern on Dec 31, 2026
    (dt.datetime(2027, 1, 1, 5, 0, tzinfo=dt.timezone.utc), "29 years ago"),     # midnight Eastern: the new year
    (dt.datetime(2026, 7, 4, 3, 59, tzinfo=dt.timezone.utc), "28 years ago"),    # summer time, still the same year
])
def test_the_page_counts_the_years_on_the_eastern_date_of_the_build_not_the_utc_one(now, words):
    page = BeautifulSoup(render.render_bell_heist(now, SAMPLE), "html.parser")
    assert page.select_one("#odds dl > div dd").get_text() == f"1998 ({words})"


# ---- the page ----

@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("bell-lessons") / "site"
    render.build(ROOT / "data" / "series.json", out, now=NOW, upcoming_path=out / "none.json")
    return out


@pytest.fixture(scope="module")
def fixture_site(tmp_path_factory):
    """The whole site built from the sample stories at the fixed time, so the page's numbers are known."""
    folder = tmp_path_factory.mktemp("bell-lessons-fixture")
    stories = folder / "stories.json"
    stories.write_text(json.dumps(SAMPLE, ensure_ascii=False), encoding="utf-8")
    render.build(ROOT / "data" / "series.json", folder / "site", now=NOW, upcoming_path=folder / "none.json",
                 heists_path=stories)
    return folder / "site"


def joke(site):
    return (site / "bell-heist" / "index.html").read_text(encoding="utf-8")


def test_the_built_page_shows_the_fixture_numbers_at_the_fixed_date(fixture_site):
    page = BeautifulSoup(joke(fixture_site), "html.parser")
    assert [row.get_text(" ", strip=True) for row in page.select("#odds dl > div")] == [
        "Last successful heist 1998 (28 years ago)", "Attempts since 1999 0 for 3 ( 2000 , 2012 , and 2017 )",
        f"On the record Stolen at least 5 times since 1941, {heists.ODDS_CAME_BACK}."]
    assert len(page.select("#lessons ol > li")) == 3
    assert [span.get_text() for span in page.select("#lessons .heist-tag")] == ["Record: 2–1", "Record: 2–1", "Record: 0–1"]
    history = (fixture_site / "heist-details" / "index.html").read_text(encoding="utf-8")
    for a in page.select("#lessons a, #odds a"):
        assert a["href"].startswith(HISTORY + "#heist-") and f'id="{a["href"].split("#")[1]}"' in history


@pytest.mark.parametrize("which", ["site", "fixture_site"])
def test_the_page_has_no_script_no_inline_style_and_the_base_csp(which, request):
    source = joke(request.getfixturevalue(which))
    page = BeautifulSoup(source, "html.parser")
    policy = html.unescape(re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', source).group(1))
    assert policy == BASE_CSP == render.CSP and "script-src" not in policy
    assert page.find_all("script") == [] and page.find_all("style") == [] and page.find_all("img") == []
    assert all(not attr.lower().startswith("on") and attr.lower() != "style"
               for element in page.find_all(True) for attr in element.attrs)
    assert 'name="robots" content="noindex"' in source


@pytest.mark.parametrize("which", ["site", "fixture_site"])
def test_the_joke_keeps_its_text_and_links_and_the_new_sections_sit_inside_it(which, request):
    source = joke(request.getfixturevalue(which))
    for kept in ("<h2>Mission accomplished</h2>",
                 "<p>You’ve successfully relocated the Monon Bell to Greencastle. Your alumni association has no "
                 "knowledge of this operation.</p>",
                 "<p>An entirely fictional mission.</p>",
                 '<p><a href="../#monon-bell">Check the real Bell holder</a></p>',
                 '<p><a href="../heist-details/">Read the real heist history</a></p>'):
        assert source.count(kept) == 1, kept
    # The committed file may one day carry no tags (then there is no checklist); the sample always has both sections.
    sections = [x for x in ('id="lessons"', 'id="odds"') if which == "fixture_site" or x in source]
    order = [source.index(x) for x in ("<h2>Mission accomplished</h2>", "An entirely fictional mission.",
                                        *sections, "Check the real Bell holder",
                                        "Read the real heist history", '<p class="back">')]
    assert order == sorted(order)
    page = BeautifulSoup(source, "html.parser")
    assert [h.name for h in page.main.find_all(re.compile(r"^h\d$"))] == ["h2"] * (1 + len(sections))
    assert len(page.find_all("h1")) == 1                                       # the site title; sections are h2
    assert heists.ODDS_PLEA in page.select_one("#odds").get_text()            # every build with a theft asks


@pytest.mark.parametrize("which", ["site", "fixture_site"])
def test_the_real_part_is_one_labelled_card_apart_from_the_joke(which, request):
    source = joke(request.getfixturevalue(which))
    page = BeautifulSoup(source, "html.parser")
    (reality,) = page.select("main > section")
    assert reality["id"] == "reality"
    label, card = reality.find_all(recursive=False)                            # the label above the card, nothing else
    # The label names the region for screen readers and is a plain paragraph in the eyebrow style, not a heading.
    assert reality["aria-labelledby"] == label["id"] and len(page.select(f'#{label["id"]}')) == 1
    assert label.name == "p" and label["class"] == ["eyebrow"]
    assert label.get_text() == heists.REALITY_LABEL == "Back to reality"       # capitals come from the stylesheet
    assert not reality.has_attr("role") and not reality.has_attr("aria-label")
    # One card, the history page's, holding both sections and nothing else.
    assert card.name == "div" and card["class"] == ["heist"] and len(page.select(".bell-heist .heist")) == 1
    assert [child.get("id") for child in card.find_all(recursive=False)] == ["lessons", "odds"]
    assert [child.name for child in card.find_all(recursive=False)] == ["section", "section"]
    # Same heading element as the joke's own, so the sections differ by the card and not by the font.
    assert [(h.name, h.get_text()) for h in page.main.find_all(re.compile(r"^h\d$"))] == [
        ("h2", "Mission accomplished"), ("h2", heists.LESSONS_TITLE), ("h2", heists.ODDS_TITLE)]
    assert all(not h.has_attr("class") for h in page.main.find_all("h2"))
    # The joke stays outside the card and above it; the links and the way back stay outside and below.
    outside = [child for child in page.main.find_all(recursive=False) if child is not reality]
    assert [child.name for child in outside] == ["h2", "p", "p", "p", "p", "p"]
    assert "Mission accomplished" not in reality.get_text() and "fictional" not in reality.get_text()
    order = [source.index(x) for x in ("An entirely fictional mission.", 'id="reality"', 'class="eyebrow"',
                                        '<div class="heist">', 'id="lessons"', 'id="odds"', "</div></section>",
                                        "Check the real Bell holder")]
    assert order == sorted(order)
    assert heists.ODDS_PLEA in card.get_text() and len(card.select("ol.heist-plan > li")) >= 1
    assert all(pill.parent.name == "div" for pill in card.select(".heist-tag"))


def test_the_card_holds_whichever_sections_there_are_and_is_left_out_with_none():
    both = BeautifulSoup(heists.reality_html(SAMPLE, TODAY, HISTORY), "html.parser")
    assert [x["id"] for x in both.select(".heist > section")] == ["lessons", "odds"]
    untagged = copy.deepcopy(SAMPLE)
    for story in untagged["incidents"]:
        story.pop("tactics", None)
        story.pop("lesson", None)
    only_odds = BeautifulSoup(heists.reality_html(heists.validate(untagged), TODAY, HISTORY), "html.parser")
    assert [x["id"] for x in only_odds.select(".heist > section")] == ["odds"]
    assert only_odds.select_one("p.eyebrow").get_text() == heists.REALITY_LABEL
    nothing = heists.validate(small([incident("a", 1950, "foiled")]))             # no tag, and no theft for the odds
    assert heists.lessons_html(nothing, HISTORY) == "" == heists.odds_html(nothing, TODAY, HISTORY)
    assert heists.reality_html(nothing, TODAY, HISTORY) == ""                     # no empty card, no stray label
    page = render.render_bell_heist(NOW, nothing)
    assert "eyebrow" not in page and 'class="heist"' not in page and "<h2>Mission accomplished</h2>" in page


def test_the_card_and_its_label_reuse_existing_styles_and_spacing_tokens():
    css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    assert len(re.findall(r"^\.heist \{", css, re.M)) == 1 and len(re.findall(r"^\.eyebrow\b", css, re.M)) == 1
    eyebrow = re.search(r"^\.eyebrow[^{]*\{([^}]*)\}", css, re.M).group(1)
    assert "text-transform: uppercase" in eyebrow and "letter-spacing" in eyebrow    # the caps are style, not text
    (space,) = re.findall(r"^\.bell-heist \.eyebrow \{ ([^}]*) \}", css, re.M)
    assert space == "margin-top: var(--space-6);"                                   # clear of the joke above
    joke_rules = "\n".join(line for line in css.splitlines() if line.startswith((".bell-heist", ".heist-odds")))
    assert "h3" not in joke_rules and "font" not in joke_rules and "color" not in joke_rules


def test_the_real_page_has_a_lesson_for_every_tactic_in_use_and_only_those(site):
    data = heists.validate(real())
    used = [key for key in heists.TACTICS if any(key in i.get("tactics", []) for i in data["incidents"])]
    page = BeautifulSoup(joke(site), "html.parser")
    assert [title.get_text() for title in page.select("#lessons ol > li > strong")] == [
        heists.TACTICS[k] for k in used]
    assert [pill.get_text() for pill in page.select("#lessons .heist-tag")] == [
        lesson["record"] for lesson in heists.lessons(data)]
    tagged = {i["id"] for i in data["incidents"] if i.get("tactics")}
    linked = {a["href"].split("#heist-", 1)[1] for a in page.select("#lessons a")}
    assert linked == tagged                                            # every tagged story is linked, and no other
    found = heists.odds(data, TODAY)
    assert found["successful"] == 0 and all(i["outcome"] == "foiled" for i in found["attempts"])
    assert found["last"]["year"] == max(i["year"] for i in data["incidents"] if i["outcome"] == "stolen")


# What the page must never say: how the Bell is kept or guarded now, or anything that reads as advice. The history
# page has a sourced section on that; the joke page says only how past attempts ended.
NOT_ON_THE_JOKE_PAGE = re.compile(
    r"\b(sensors?|alarms?|cameras?|locks?|locked|bolt(s|ed)?|unbolt(ed)?|pedestal|alcove|platform|scaffold(ing)?|"
    r"doll(y|ies)|ceiling|bleachers|schedules?|shifts?|patrols?|lilly|chadwick|field ?house|lobby|"
    r"next time|would have worked|should have|the trick is|tips?|how to (get|take|steal|reach))\b", re.I)


def test_the_page_describes_outcomes_only_never_how_the_bell_is_kept_or_guarded():
    data = heists.validate(real())
    for story in data["incidents"]:
        assert not NOT_ON_THE_JOKE_PAGE.search(story.get("lesson", "")), story["id"]
    for step in heists.TACTICS.values():
        assert not NOT_ON_THE_JOKE_PAGE.search(step), step
    page = BeautifulSoup(render.render_bell_heist(NOW, data), "html.parser")
    seen = page.main.get_text(" ", strip=True)
    assert not NOT_ON_THE_JOKE_PAGE.search(seen), NOT_ON_THE_JOKE_PAGE.search(seen)
    for paragraph in data["guarding"]["paragraphs"]:                   # the "guarded now" text stays on the history page
        assert paragraph not in seen and paragraph[:60] not in seen
    assert "don’t" in seen and "fictional" in seen
    assert NOT_ON_THE_JOKE_PAGE.search("it rests on a pressure sensor")   # the pattern really catches that kind of line


def test_the_lessons_name_nobody():
    """The naming rule of the history page holds here too: a lesson says what happened, not who did it."""
    for story in real()["incidents"]:
        words = story.get("lesson", "")
        assert not re.search(r"\b(Mr|Mrs|Ms|Dr)\.|\b[A-Z][a-z]+ [A-Z][a-z]+\b", words.replace("Wabash", "wabash")
                             .replace("DePauw", "depauw").replace("Bell", "bell")), story["id"]


def test_the_checklist_styles_use_no_new_color_and_the_pill_is_the_history_pages_tag():
    css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    rules = "\n".join(re.findall(r"^[^\n{]*(?:heist-plan|heist-plea|\.bell-heist \.heist)[^\n]*$", css, re.M))
    assert rules and "#" not in rules and "color" not in rules
    assert len(re.findall(r"^\.heist-tag \{", css, re.M)) == 1          # one rule draws the pill on both pages
    assert re.search(r"^\.money, \.nowrap \{ white-space: nowrap; \}", css, re.M)   # the record never breaks in two


def test_no_tick_glyph_is_left_anywhere(site, fixture_site):
    """The checklist is numbered by the list itself; the ballot-box ticks are gone from the page, the stylesheet
    and the code."""
    ticks = re.compile("[\u2610-\u2612\u2713-\u2718\u2705\U0001F5F8\U0001F5F9]")
    for built in (site, fixture_site):
        assert not ticks.search(joke(built)) and not ticks.search((built / "styles.css").read_text(encoding="utf-8"))
        assert "::before" not in "\n".join(line for line in (built / "styles.css").read_text(encoding="utf-8")
                                           .splitlines() if "heist-plan" in line)
    for name in ("scraper/heists.py", "scraper/render.py", "static/styles.css", "README.md"):
        assert not ticks.search((ROOT / name).read_text(encoding="utf-8")), name
