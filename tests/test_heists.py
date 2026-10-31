"""Monon Bell heist history: validation of the curated file, the tally, the page order, the game lookups, the links."""
import copy
import datetime as dt
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
import heists  # noqa: E402
import render  # noqa: E402

NOW = dt.datetime(2026, 12, 1, 15, 0, tzinfo=dt.timezone.utc)
SRC = [{"label": "A source", "url": "https://example.com/story"}]


def incident(iid, year, outcome="stolen", **kw):
    base = {"id": iid, "year": year, "school": "not-recorded", "outcome": outcome, "title": f"Title {iid}",
            "summary": "A summary.", "counted_in_theft_total": outcome == "stolen", "sources": copy.deepcopy(SRC)}
    base.update(kw)
    return base


def small(incidents):
    return {"schema_version": 1, "verified_at": "2026-10-30", "intro": "Intro.", "method": ["Method."],
            "guarding": {"paragraphs": ["Guarded."], "sources": copy.deepcopy(SRC)}, "incidents": incidents}


SAMPLE = small([
    incident("late", 2012, "foiled", school="depauw"),
    incident("unknown", None, "foiled", school="depauw"),
    incident("bare-one", 1941, bare_record=True),
    incident("big", 1965, school="wabash", details=["Long story."], consequences="It went home."),
    incident("bare-two", 1959, bare_record=True),
    incident("buried", 1967, "hidden", school="depauw"),
])


def game(date, result, depauw, wabash, **kw):
    return {"date": date, "result": result, "depauw": depauw, "wabash": wabash, "location": "home", **kw}


GAMES = [game("1965-11-13", "L", 6, 16), game("1967-11-11", "L", 0, 7), game("2012-11-10", "T", 7, 7)]


def real():
    return heists.load_json(ROOT / "data" / "bell-heists.json")


def test_committed_file_is_valid_and_every_story_has_a_source_and_unique_anchor():
    data = heists.validate(real())
    ids = [i["id"] for i in data["incidents"]]
    assert len(ids) == len(set(ids)) and all(i["sources"] for i in data["incidents"])


def test_missing_source_bad_outcome_and_duplicate_id_are_refused():
    broken = copy.deepcopy(SAMPLE)
    broken["incidents"][0]["sources"] = []
    with pytest.raises(heists.HeistError, match="sources"):
        heists.validate(broken)
    broken = copy.deepcopy(SAMPLE)
    broken["incidents"][0]["sources"] = [{"label": "x", "url": "not a url"}]
    with pytest.raises(heists.HeistError, match="URL"):
        heists.validate(broken)
    broken = copy.deepcopy(SAMPLE)
    broken["incidents"][0]["outcome"] = "borrowed"
    with pytest.raises(heists.HeistError, match="outcome"):
        heists.validate(broken)
    broken = copy.deepcopy(SAMPLE)
    broken["incidents"][1]["id"] = broken["incidents"][0]["id"]
    with pytest.raises(heists.HeistError, match="duplicate"):
        heists.validate(broken)


def test_years_summaries_and_the_theft_flag_are_checked():
    for field, value, match in (("year", 1700, "year"), ("year", True, "year"), ("summary", "  ", "summary"),
                                ("counted_in_theft_total", True, "theft total")):
        broken = copy.deepcopy(SAMPLE)
        broken["incidents"][0][field] = value        # incident 0 is a foiled attempt: it cannot count as a theft
        with pytest.raises(heists.HeistError, match=match):
            heists.validate(broken)
    assert heists.validate(copy.deepcopy(SAMPLE))


def test_the_tally_is_worked_out_from_the_incidents_and_says_at_least():
    counts = heists.tally(SAMPLE)
    assert counts == {"stolen": 3, "foiled": 2, "hidden": 1}
    assert heists.tally_text(counts) == "Stolen at least 3 times · 2 foiled attempts · 1 hidden by its own fans"
    sample = copy.deepcopy(SAMPLE)
    sample["incidents"].append(incident("another", 1988))
    assert heists.tally(sample)["stolen"] == 4          # adding a year changes the count, nothing else does
    assert heists.tally_text({"stolen": 1, "foiled": 0, "hidden": 0}) == "Stolen at least 1 time"


def test_the_real_tally_comes_from_the_file():
    data = real()
    assert heists.tally(data)["stolen"] == sum(1 for i in data["incidents"] if i["outcome"] == "stolen")


def test_incidents_run_oldest_to_newest_with_date_unknown_last():
    years = [i["year"] for i in heists.ordered(SAMPLE)]
    assert years == [1941, 1959, 1965, 1967, 2012, None]
    real_years = [i["year"] for i in heists.ordered(real())]
    dated = [y for y in real_years if y is not None]
    assert dated == sorted(dated) and real_years[-1] is None


def test_the_game_result_comes_from_the_series_data_and_is_skipped_without_a_game():
    assert heists.game_line(heists.bell_game(GAMES, 1965)) == "Associated game: Wabash 16, DePauw 6"
    assert heists.game_line(heists.bell_game(GAMES, 2012)) == "Associated game: DePauw 7, Wabash 7 (tie)"
    assert heists.bell_game(GAMES, 1941) is None                       # before the Bell game
    assert heists.bell_game(GAMES, 1999) is None
    assert heists.bell_game([game("1965-01-01", "L", 6, 16, date_known=False)], 1965) is None
    changed = [game("1965-11-13", "W", 21, 14)]
    assert heists.game_line(heists.bell_game(changed, 1965)) == "Associated game: DePauw 21, Wabash 14"


def page(games, data=SAMPLE):
    return "\n".join(heists.render_main(data, games, back_html="", game_href="../#football", joke_href="../bell-heist/"))


def test_cards_have_anchors_sources_and_the_bare_years_share_one_compact_card():
    html = page(GAMES)
    for iid in ("heist-late", "heist-unknown", "heist-big", "heist-buried", "other-recorded-thefts"):
        assert f'id="{iid}"' in html and f'href="#{iid}"' in html
    assert 'id="heist-bare-one"' in html and 'id="heist-bare-two"' in html          # each bare year keeps its own anchor
    assert html.count('id="other-recorded-thefts"') == 1
    assert html.count("Sources: ") == 4 + 1                              # four cards, plus the guarding section
    assert html.count('<details class="heist-details">') == 1            # only the story with details
    assert "Aftermath" in html and "It went home." in html
    assert "Associated game: Wabash 16, DePauw 6" in html
    assert 'href="../#football"' in html


def test_page_order_places_date_unknown_last_and_the_bare_card_first():
    html = page(GAMES)
    order = [html.index(x) for x in ('id="other-recorded-thefts"', 'id="heist-big"', 'id="heist-buried"', 'id="heist-late"',
                                      'id="date-unknown-h"', 'id="heist-unknown"', 'id="guarded-now"')]
    assert order == sorted(order)


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("heists") / "site"
    render.build(ROOT / "data" / "series.json", out, now=NOW, upcoming_path=out / "none.json")
    return out


def read(site, name):
    return (site / name).read_text(encoding="utf-8")


def test_every_anchor_starts_with_a_letter_so_the_html_is_valid(site):
    html = read(site, "heist-details/index.html")
    ids = re.findall(r'(?<![\w-])id="([^"]+)"', html)
    assert ids and all(i[0].isalpha() for i in ids) and len(ids) == len(set(ids))
    assert 'id="heist-1965-operation-frijoles"' in html


def test_the_page_is_built_with_the_strict_csp_athletics_nav_and_share_tags(site):
    html = read(site, "heist-details/index.html")
    assert "script-src" not in html and "<script" not in html
    assert '<a href="../" aria-current="page">Athletics</a>' in html
    assert "<title>The Bell’s heist history | DePauw vs. Wabash</title>" in html
    assert f'<link rel="canonical" href="{render.SITE_URL}heist-details/">' in html
    assert 'property="og:image"' in html
    assert "Stolen at least" in html and "Please let" in html and 'href="../bell-heist/"' in html
    assert f"{render.SITE_URL}heist-details/" in read(site, "sitemap.xml")
    assert (site / "data" / "bell-heists.json").exists()


def test_the_football_card_and_the_joke_page_link_to_the_history(site):
    home = read(site, "index.html")
    assert '<a href="heist-details/">The Bell’s heist history</a>' in home
    assert home.index("Read more on Wikipedia") < home.index("The Bell’s heist history")
    joke = read(site, "bell-heist/index.html")
    assert '<a href="../heist-details/">Read the real heist history</a>' in joke
    assert "Mission accomplished" in joke and "An entirely fictional mission." in joke


def test_the_correction_link_prefills_the_data_correction_form():
    url = heists.correction_url()
    assert "template=data-correction.yml" in url and "sport=Monon%20Bell%20heist%20history" in url
    form = (ROOT / ".github" / "ISSUE_TEMPLATE" / "data-correction.yml").read_text(encoding="utf-8")
    assert heists.CORRECTION_SPORT in form


def test_no_internal_link_on_the_new_pages_points_to_a_missing_page(site):
    for name in ("heist-details/index.html", "bell-heist/index.html"):
        text = read(site, name)
        for href in re.findall(r'href="([^"#:]*)(?:#([^"]*))?"', text):
            path, fragment = href
            if path.startswith("http") or not path and not fragment:
                continue
            target = (site / name).parent / path if path else site / name
            target = target / "index.html" if target.is_dir() else target
            assert target.exists(), (name, path)
            if fragment:
                assert f'id="{fragment}"' in target.read_text(encoding="utf-8"), (name, path, fragment)


def test_the_athletics_how_it_works_page_mentions_the_curated_heist_history(site):
    html = read(site, "how-it-works/athletics/index.html")
    assert 'href="../../heist-details/"' in html and "not updated automatically" in html
