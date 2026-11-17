"""The Monon Bell, year by year (/monon-bell/), and the one function behind every Bell figure on the site.

facts.bell_seasons works out who held the Bell after each season; facts.bell (the home page's strip and the football
card's block) and the year-by-year page both read it, so they cannot disagree. Exact numbers here come from made-up
games and the frozen fixture with a fixed `now`; the live data is used only for rules that always hold. Nothing reads
the clock or the network. The contrast of the letters on their tinted cells is in tests/test_accessibility.py."""

import copy
import datetime as dt
import json
import re
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
import belltimeline  # noqa: E402
import facts  # noqa: E402
import heists  # noqa: E402
import render  # noqa: E402

EST = dt.timezone(dt.timedelta(hours=-5))            # Eastern standard time, in effect for every date below
NOW = dt.datetime(2026, 10, 30, 12, tzinfo=dt.timezone.utc)
FROZEN = ROOT / "tests" / "fixtures" / "series-frozen-2026-10-30.json"      # football through 2025, 131 games
BELL_CSS = ROOT / "static" / "bell.css"
BELL_CSS_LIMIT = 4096
D, W = "DePauw", "Wabash"


def eastern(y, m, d, hh=12, mm=0):
    return dt.datetime(y, m, d, hh, mm, tzinfo=EST).astimezone(dt.timezone.utc)


def g(year, result, d=10, w=7):
    return {"date": f"{year}-11-15", "season": str(year), "location": "home", "site": None,
            "result": result, "depauw": d, "wabash": w, "note": None, "boxscore": None}


def frozen():
    return json.loads(FROZEN.read_text(encoding="utf-8"))


def frozen_football():
    return frozen()["sports"]["football"]["games"]


def with_football_game(data, game):
    """The series with one more football game, its record and the totals moved with it (render.load checks them)."""
    data = copy.deepcopy(data)
    football = data["sports"]["football"]
    football["games"].append(game)
    football["games_played"] += 1
    football["current_streak"] = None                 # the card works the streak out from the games
    key = {"W": "depauw_wins", "L": "wabash_wins", "T": "ties"}[game["result"]]
    football["record"][key] += 1
    data["totals"][key] += 1
    data["totals"]["games_played"] += 1
    return data


def game_2026(result, d, w):
    return {"date": "2026-11-14", "season": "2026", "location": "neutral", "site": "Muncie, Ind.", "result": result,
            "depauw": d, "wabash": w, "note": None, "boxscore": None, "source": "both"}


def heists_data():
    return heists.validate(heists.load_json(render.HEISTS_DATA))


def soup(text):
    return BeautifulSoup(text, "html.parser")


def bell_page(data, now):
    return soup(render.curly_html(render.render_monon_bell(data, heists_data(), None, now, {})))


def home_page(data, now):
    return soup(render.curly_html(render.render_index(data, None, now, {})))


def cells(page):
    """{year: td} for every cell of the grid, by the year its hidden text opens with."""
    found = {}
    for td in page.select("table.bt-grid td"):
        said = td.find("span", class_="sr-only", recursive=False).get_text()
        found[int(said[:4])] = td
    return found


def said(td):
    return td.find("span", class_="sr-only", recursive=False).get_text()


def shown(td):
    mark = td.find("span", class_="bt-l")
    return mark.get_text() if mark else ""


# ---- the one function: who held the Bell after each season ------------------------------------------------------

MADE_UP = [g(1931, "W"), g(1932, "T", 0, 0), g(1933, "W"), g(1934, "L"), g(1935, "T", 0, 0), g(1936, "L"),
           g(1938, "W")]


def test_there_is_one_entry_per_season_from_1932_and_games_before_it_are_not_bell_games():
    seasons = facts.bell_seasons(MADE_UP)
    assert [s["year"] for s in seasons] == list(range(1932, 1939))
    assert all(int(game["date"][:4]) >= facts.BELL_START for s in seasons for game in s["games"])
    assert facts.bell_seasons([]) == [] and facts.bell_seasons([g(1920, "W"), g(1931, "L")]) == []


def test_the_winner_takes_the_bell_and_nobody_has_it_before_a_game_is_won():
    seasons = {s["year"]: s for s in facts.bell_seasons(MADE_UP)}
    assert [seasons[y]["holder"] for y in range(1932, 1939)] == [None, D, W, W, W, W, D]
    assert (seasons[1932]["result"], seasons[1932]["since"], seasons[1932]["held"]) == ("T", None, [])
    assert seasons[1933]["since"] == 1933 and seasons[1936]["since"] == 1934 and seasons[1938]["since"] == 1938


def test_a_tie_leaves_the_bell_with_the_holder_and_counts_toward_the_hold():
    seasons = {s["year"]: s for s in facts.bell_seasons(MADE_UP)}
    assert (seasons[1935]["result"], seasons[1935]["holder"], seasons[1935]["changed"]) == ("T", W, False)
    assert [game["result"] for game in seasons[1936]["held"]] == ["L", "T", "L"]


def test_a_season_with_no_game_leaves_the_bell_where_it_was():
    seasons = {s["year"]: s for s in facts.bell_seasons(MADE_UP)}
    gap = seasons[1937]
    assert (gap["status"], gap["game"], gap["result"], gap["games"]) == (facts.NO_GAME, None, None, [])
    assert (gap["holder"], gap["changed"], gap["since"]) == (W, False, 1934)
    assert all(s["status"] == facts.PLAYED for y, s in seasons.items() if y != 1937)


def test_the_current_season_is_not_yet_played_until_its_result_is_in():
    ahead = facts.bell_seasons(MADE_UP, 1939)
    assert (ahead[-1]["year"], ahead[-1]["status"], ahead[-1]["holder"]) == (1939, facts.NOT_YET, D)
    assert facts.bell_seasons(MADE_UP, 1938) == facts.bell_seasons(MADE_UP)          # its result is in: played
    assert facts.bell_seasons(MADE_UP, 1900) == facts.bell_seasons(MADE_UP)          # a clock set in the past adds nothing
    later = facts.bell_seasons(MADE_UP, 1941)                                        # past years with no game, then this one
    assert [s["status"] for s in later[-3:]] == [facts.NO_GAME, facts.NO_GAME, facts.NOT_YET]


def test_the_bell_changes_hands_only_from_one_school_to_the_other():
    seasons = facts.bell_seasons(MADE_UP, 1939)
    assert [s["year"] for s in seasons if s["changed"]] == [1934, 1938]              # 1933 is the first win, not a change
    assert facts.bell_summary(seasons)["changes"] == 2


def test_the_summary_counts_seasons_longest_holds_and_leaves_out_a_season_not_yet_played():
    summary = facts.bell_summary(facts.bell_seasons(MADE_UP, 1939))
    assert summary["seasons"] == {D: 2, W: 4, None: 1}                               # 1937 (no game) counts for Wabash
    assert summary["longest"][W] == {"seasons": 4, "first": 1934, "last": 1937, "times": 1}
    assert summary["longest"][D] == {"seasons": 1, "first": 1938, "last": 1938, "times": 2}     # the most recent of two
    assert summary == facts.bell_summary(facts.bell_seasons(MADE_UP))                # 1939 changed nothing
    only_ties = facts.bell_summary(facts.bell_seasons([g(1932, "T"), g(1933, "T")]))
    assert only_ties == {"seasons": {D: 0, W: 0, None: 2}, "longest": {D: None, W: None}, "changes": 0}


def test_the_holder_by_season_in_the_frozen_fixture():
    seasons = {s["year"]: s for s in facts.bell_seasons(frozen_football())}
    assert list(seasons) == list(range(1932, 2026))
    assert seasons[1932]["holder"] is None and seasons[1933]["holder"] == D          # the first game was a 0-0 tie
    ties = {y: s["holder"] for y, s in seasons.items() if s["result"] == "T"}
    assert ties == {1932: None, 1935: W, 1956: D, 1959: D, 1980: W, 1992: W}         # each tie: the holder kept it
    assert (seasons[1964]["holder"], seasons[1965]["holder"], seasons[1966]["holder"]) == (D, W, D)
    assert [y for y, s in seasons.items() if s["status"] == facts.NO_GAME] == [2020]
    assert (seasons[2019]["holder"], seasons[2020]["holder"], seasons[2021]["holder"]) == (D, D, W)
    assert all(seasons[y]["holder"] == D and seasons[y]["since"] == 2022 for y in range(2022, 2026))


def test_the_summary_of_the_frozen_fixture():
    summary = facts.bell_summary(facts.bell_seasons(frozen_football(), 2026))
    assert summary["seasons"] == {D: 46, W: 47, None: 1}
    assert summary["longest"][D] == {"seasons": 10, "first": 1955, "last": 1964, "times": 1}
    assert summary["longest"][W] == {"seasons": 7, "first": 2009, "last": 2015, "times": 1}
    assert summary["changes"] == 34


@pytest.mark.parametrize("games", [MADE_UP, [g(2018, "L"), g(2019, "W"), g(2020, "T"), g(2021, "T"), g(2022, "W")],
                                   [g(1932, "T"), g(1933, "T")], [g(2018, "W"), g(2019, "L"), g(2021, "T")]])
def test_the_standings_are_read_from_the_seasons(games):
    """facts.bell holds no rule of its own: every figure it gives can be counted from bell_seasons."""
    b, seasons = facts.bell(games), facts.bell_seasons(games)
    last = seasons[-1]
    assert (b["holder"], b["since"], b["held_games"]) == (last["holder"], last["since"], len(last["held"]))
    assert b["games"] == sum(len(s["games"]) for s in seasons)
    assert b["held_wins"] + b["held_ties"] == b["held_games"]
    assert b["held_ties"] == sum(1 for game in last["held"] if game["result"] == "T")


def test_the_live_data_obeys_the_same_rules():
    """Rules that hold whatever the newest result is; no number is pinned."""
    games = json.loads((ROOT / "data" / "series.json").read_text(encoding="utf-8"))["sports"]["football"]["games"]
    seasons, b = facts.bell_seasons(games), facts.bell(games)
    assert [s["year"] for s in seasons] == list(range(facts.BELL_START, seasons[-1]["year"] + 1))
    for before, s in zip([None] + seasons, seasons):
        held_before = before["holder"] if before else None
        winner = facts.BELL_WINNERS.get(s["result"])
        assert s["holder"] == (winner or held_before)
        assert s["changed"] == (held_before is not None and s["holder"] != held_before)
    summary = facts.bell_summary(seasons)
    assert sum(summary["seasons"].values()) == len(seasons)
    assert (b["holder"], b["since"]) == (seasons[-1]["holder"], seasons[-1]["since"])


# ---- the page and the home page agree ---------------------------------------------------------------------------

def bell_lines(data, now):
    """What each page says about the holder, the streak and the record: (home strip, football card, year-by-year)."""
    home, page = home_page(data, now), bell_page(data, now)
    strip, block = home.select_one("section.bell-strip"), home.select_one("section.bell")
    record = page.find("dt", string=f"Bell record since {facts.BELL_START}").find_next_sibling("dd")
    return {
        "home": (str(strip.select_one(".bell-now")), [str(s) for s in strip.select(".bell-rec span")]),
        "page": (str(page.select_one(".bt-card .bell-now")), [str(s) for s in record.select("span")]),
        "block": (block.select_one(".bell-holder").get_text(" ", strip=True),
                  [str(s) for s in block.select(".bell-rec span")]),
        "ties": (strip.select_one(".bell-rec").get_text(), record.get_text()),
    }


def check_agreement(data, now):
    lines = bell_lines(data, now)
    assert lines["home"] == lines["page"]                                   # the same holder line and record, tag for tag
    assert lines["home"][1] == lines["block"][1]
    b = facts.bell(data["sports"]["football"]["games"])
    assert lines["block"][0] == f'{b["holder"]} has held the Bell since {b["since"]} ({render.bell_held_text(b)})'
    assert f'{b["holder"]} holds the Bell' in lines["page"][0]
    assert f'since {b["since"]} · {render.bell_held_text(b)}' in lines["page"][0]
    tie_words = render.plural(b["ties"], "tie")
    assert all((tie_words in text) == bool(b["ties"]) for text in lines["ties"])
    return lines, b


def test_the_page_and_the_home_page_agree_on_the_live_data():
    data = render.load(ROOT / "data" / "series.json")
    check_agreement(data, NOW)


def test_the_page_and_the_home_page_agree_on_the_frozen_fixture():
    lines, b = check_agreement(frozen(), NOW)
    assert (b["holder"], b["since"], b["depauw"], b["wabash"], b["ties"]) == (D, 2022, 43, 44, 6)
    assert "since 2022 · 4 straight wins" in lines["page"][0]


def test_they_still_agree_when_the_newest_game_flips_the_holder():
    data = with_football_game(frozen(), game_2026("L", 10, 20))
    lines, b = check_agreement(data, eastern(2026, 11, 15))
    assert (b["holder"], b["since"], b["depauw"], b["wabash"]) == (W, 2026, 43, 45)
    assert "Wabash holds the Bell" in lines["home"][0] and "since 2026 · 1 win" in lines["home"][0]
    page = bell_page(data, eastern(2026, 11, 15))
    newest = cells(page)[2026]
    assert (newest["class"], shown(newest), said(newest)) == (["bt-w"], "W", "2026: Wabash won 20 to 10 and took the Bell")
    assert page.find("dt", string="Changes of hands").find_next_sibling("dd").get_text() == "35"


# ---- the grid ---------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("bell-timeline") / "site"
    render.build(FROZEN, out, now=NOW, upcoming_path=out / "missing.json")
    return out


@pytest.fixture(scope="module")
def page(site):
    return soup((site / "monon-bell" / "index.html").read_text(encoding="utf-8"))


def test_the_grid_has_one_row_per_decade_and_one_column_per_year(page):
    grid = page.select_one("table.bt-grid")
    assert grid.caption.get_text().startswith("Who held the Monon Bell after each season, 1932 to 2026")
    head = grid.thead.find_all("th")
    assert [th.get_text() for th in head] == ["Decade"] + [str(n) for n in range(10)]
    assert all(th["scope"] == "col" for th in head)
    rows = grid.tbody.find_all("tr")
    assert [row.th.get_text() for row in rows] == [f"{decade}s" for decade in range(1930, 2030, 10)]
    assert all(row.th["scope"] == "row" and len(row.find_all("td")) == 10 for row in rows)
    assert sorted(cells(page)) == list(range(1930, 2030))                   # every cell names its own year, in order
    for row in rows:
        assert [int(said(td)[:4]) % 10 for td in row.find_all("td")] == list(range(10))


def test_a_played_season_shows_the_holders_letter_and_says_the_result_in_words(page):
    grid = cells(page)
    assert (grid[1965]["class"], shown(grid[1965])) == (["bt-w"], "W")
    assert said(grid[1965]) == "1965: Wabash won 16 to 6 and took the Bell"
    assert said(grid[1933]) == "1933: DePauw won 14 to 0 and took the Bell"          # the first win of all
    assert said(grid[1958]) == "1958: DePauw won 24 to 8 and kept the Bell"
    for year in range(2022, 2026):
        assert (grid[year]["class"], shown(grid[year])) == (["bt-d"], "D")
        assert said(grid[year]).startswith(f"{year}: DePauw won ")
    assert said(grid[2022]).endswith("and took the Bell") and said(grid[2025]).endswith("and kept the Bell")


def test_every_cell_matches_its_season(page):
    """The letter, the class and the words of each cell, against the one function."""
    seasons = {s["year"]: s for s in facts.bell_seasons(frozen_football(), 2026)}
    for year, td in cells(page).items():
        s = seasons.get(year)
        assert td.find("span", class_="sr-only", recursive=False) is not None
        letter = td.find("span", class_="bt-l")
        assert letter is None or letter["aria-hidden"] == "true"                    # the words are in the hidden text
        if s is None or s["status"] == facts.NO_GAME:
            assert shown(td) == "" and not td.get("class"), year
            continue
        assert said(td) == belltimeline.season_text(s)
        if s["status"] == facts.PLAYED:
            assert td["class"] == [belltimeline.CELL_CLASSES[s["holder"]]], year
            assert shown(td) == belltimeline.LETTERS.get(s["holder"], "") + ("=" if s["result"] == "T" else ""), year


def test_a_tie_shows_the_holders_letter_with_the_tie_mark_and_says_who_kept_the_bell(page):
    grid = cells(page)
    assert (grid[1956]["class"], shown(grid[1956]), said(grid[1956])) == (
        ["bt-d"], "D=", "1956: 7 to 7 tie, DePauw kept the Bell")
    assert (grid[1935]["class"], shown(grid[1935]), said(grid[1935])) == (
        ["bt-w"], "W=", "1935: 0 to 0 tie, Wabash kept the Bell")
    assert (grid[1932]["class"], shown(grid[1932]), said(grid[1932])) == (
        ["bt-t"], "=", "1932: 0 to 0 tie; neither school held the Bell")           # nobody had won it yet


def test_years_without_a_bell_game_are_empty_and_say_why(page):
    grid = cells(page)
    for year, words in ((1930, "1930: before the Bell"), (1931, "1931: before the Bell"),
                        (2020, "2020: no game; DePauw kept the Bell"),
                        (2027, "2027: a future season"), (2029, "2029: a future season")):
        assert (shown(grid[year]), grid[year].get("class"), said(grid[year])) == ("", None, words)
    assert (grid[2026]["class"], shown(grid[2026]), said(grid[2026])) == (["bt-next"], "…", "2026: not yet played")


def test_a_year_without_a_game_says_so_and_says_who_kept_the_bell_because_it_counts_for_that_school(page):
    """2020 is an empty box, yet it is one of the seasons DePauw ended with the Bell: the cell's hidden text has to
    say both things, and the count above the grid has to include it."""
    seasons = facts.bell_seasons(frozen_football(), 2026)
    gaps = [s for s in seasons if s["status"] == facts.NO_GAME]
    assert [(s["year"], s["holder"]) for s in gaps] == [(2020, D)]
    cell = cells(page)[2020]
    assert said(cell) == "2020: no game; DePauw kept the Bell"
    assert "no game" in said(cell) and "DePauw kept the Bell" in said(cell)
    assert shown(cell) == "" and not cell.get("class") and cell.find("span", class_="bt-l") is None   # still empty
    played = sum(1 for s in seasons if s["status"] == facts.PLAYED and s["holder"] == D)
    counted = facts.bell_summary(seasons)["seasons"][D]
    assert counted == played + len(gaps) == 46                                       # the year is in DePauw's count
    row = page.find("dt", string="Seasons ended with the Bell").find_next_sibling("dd").get_text()
    assert f"DePauw {counted}" in row


@pytest.mark.parametrize("games, year, words", [
    ([g(1933, "W"), g(1935, "L")], 1934, "1934: no game; DePauw kept the Bell"),
    ([g(1933, "L"), g(1936, "L")], 1935, "1935: no game; Wabash kept the Bell"),
    ([g(1932, "T", 0, 0), g(1934, "W")], 1933, "1933: no game"),                     # nobody had won it yet
])
def test_any_year_without_a_game_names_the_school_that_kept_the_bell(games, year, words):
    seasons = {s["year"]: s for s in facts.bell_seasons(games)}
    assert seasons[year]["status"] == facts.NO_GAME
    assert belltimeline.season_text(seasons[year]) == words
    held = facts.bell_summary(list(seasons.values()))["seasons"]
    assert sum(held.values()) == len(seasons)                                        # every season is counted once
    assert held[seasons[year]["holder"]] >= 1


def test_every_dated_heist_story_has_a_link_in_its_year_to_its_card(site, page):
    history = soup((site / "heist-details" / "index.html").read_text(encoding="utf-8"))
    anchors = {el["id"] for el in history.find_all(id=True)}
    grid = cells(page)
    stories = [i for i in heists_data()["incidents"] if i["year"] is not None]
    assert stories and len(page.select("a.bt-heist")) == len(stories)
    for story in stories:
        links = [a for a in grid[story["year"]].select("a.bt-heist")
                 if a["href"] == f'../heist-details/#heist-{story["id"]}']
        assert len(links) == 1, story["id"]
        assert links[0]["href"].split("#")[1] in anchors                            # the card exists on the history page
        name = links[0].find("span", class_="sr-only").get_text()
        assert name == f'{story["year"]} heist story: {story["title"]}'
        dot = links[0].find("span", class_="bt-dot")                                # drawn by bell.css, no character
        assert dot["aria-hidden"] == "true" and dot.get_text() == "" and dot.name == "span"
    assert len(grid[1966].select("a.bt-heist")) == 2                                # two stories, one cell
    assert shown(grid[1966]) == "D" and said(grid[1966]).startswith("1966: DePauw won")    # and still its result


def test_the_legend_explains_every_mark_in_words(page):
    legend = [li.get_text(" ", strip=True) for li in page.select("ul.bt-legend li")]
    assert legend == ["D DePauw had the Bell when that season ended", "W Wabash had the Bell when that season ended",
                      "= The game was a tie, so the Bell stayed where it was",
                      "A dot: a heist story from that year; the dot is a link to it",
                      "… This season’s game has not been played yet", "An empty box: no Bell game that year"]
    keys = page.select("ul.bt-legend .bt-key")
    assert [k.get("class")[1:] for k in keys] == [["bt-d"], ["bt-w"], ["bt-t"], [], ["bt-next"], ["bt-empty"]]


def test_the_notes_say_what_the_grid_cannot_and_name_their_source(page):
    notes = [li.get_text() for li in page.select("ul.bt-notes li")]
    assert len(notes) == 3
    assert notes[0] == ("1932: the first Bell game ended in a 0–0 tie. Neither team had won the Bell, so it was kept "
                        "out of sight for the year, and that season counts for neither school.")
    assert f"before overtime came to college football in {render.OVERTIME_START}" in notes[1]
    assert "after the 1935 tie, the first with a holder, so the grid leaves the Bell with Wabash." in notes[1]
    assert notes[2] == ("2020: no game was played, so DePauw kept the Bell and the season counts as one DePauw "
                        "ended with it.")
    source = page.select_one("p.bt-source a")
    facts_file = json.loads((ROOT / "data" / "fun-facts.json").read_text(encoding="utf-8"))
    tie_rule = next(f for f in facts_file["facts"] if f["id"] == "both-tie-rule")
    assert {"label": source.get_text(), "url": source["href"]} in tie_rule["sources"]     # one source, cited the same way


def test_the_summary_is_counted_from_the_seasons(page):
    rows = {dt_.get_text(): dt_.find_next_sibling("dd").get_text() for dt_ in page.select(".bt-card dl.facts dt")}
    assert rows == {"Bell record since 1932": "DePauw 43 · Wabash 44 · 6 ties",
                    "Seasons ended with the Bell": "DePauw 46 · Wabash 47 · neither 1",
                    "Longest DePauw hold": "10 seasons · 1955–1964",
                    "Longest Wabash hold": "7 seasons · 2009–2015",
                    "Changes of hands": "34"}
    assert belltimeline.run_text({"seasons": 3, "first": 1987, "last": 1989, "times": 2}) == (
        "3 seasons (twice, most recently 1987–1989)")
    assert belltimeline.run_text({"seasons": 1, "first": 1938, "last": 1938, "times": 1}) == "1 season · 1938"
    assert belltimeline.run_text(None) == "None yet"


# ---- the same rules on the how-it-works page ------------------------------------------------------------------

def test_the_how_it_works_page_tells_the_bell_rules_from_the_same_notes(site, page):
    how = soup((site / "how-it-works" / "athletics" / "index.html").read_text(encoding="utf-8"))
    heading = how.find("h3", string=belltimeline.RULES_HEADING)
    assert heading.get_text() == "Monon Bell rules"
    following = [h.get_text() for h in heading.find_all_next("h3")]
    assert following[0] == "The Monon Bell heist history"                            # right before the heist history
    intro, rules, cited = heading.find_next_sibling("p"), heading.find_next_sibling("ul"), None
    cited = rules.find_next_sibling("p")
    assert (intro.a["href"], intro.a.get_text()) == ("../../monon-bell/", "The Monon Bell, year by year")
    assert "since the 1932 season" in intro.get_text()
    items = [li.get_text() for li in rules.find_all("li")]
    assert [li.strong.get_text() for li in rules.find_all("li")] == ["After a game:", "1932:", "Ties:", "2020:"]
    assert items[0] == "After a game: the winner takes the Bell and holds it until the other school wins."
    assert items[1].endswith("so it was kept out of sight for the year, and that season counts for neither school.")
    assert f"before overtime came to college football in {render.OVERTIME_START}" in items[2]
    assert items[2].endswith("after the 1935 tie, the first with a holder, so the site leaves the Bell with Wabash.")
    assert items[3] == ("2020: no game was played, so DePauw kept the Bell and the season counts as one DePauw "
                        "ended with it.")
    under_grid = [li.get_text() for li in page.select("ul.bt-notes li")]             # the page's own notes
    assert items[1:] == [note.replace("so the grid leaves", "so the site leaves") for note in under_grid]
    source = page.select_one("p.bt-source a")
    assert (cited.a["href"], cited.a.get_text()) == (source["href"], source.get_text())   # the one source, cited the same


def test_the_rules_section_is_left_out_when_no_bell_game_is_on_record():
    assert belltimeline.rules_lines([], render.OVERTIME_START, "../../monon-bell/") == []
    only_wins = belltimeline.rules_lines(facts.bell_seasons([g(1932, "W"), g(1933, "L")]), 1996, "x/")
    assert sum(line.startswith("<li>") for line in only_wins) == 1                   # no tie, no gap: only the rule


# ---- the page itself --------------------------------------------------------------------------------------------

def test_the_page_is_static_athletics_side_and_loads_its_own_stylesheet(site, page):
    assert page.find_all("script") == [] and page.find_all("style") == []
    assert all(attr.lower() != "style" and not attr.lower().startswith("on")
               for el in page.find_all(True) for attr in el.attrs)
    policy = page.find("meta", attrs={"http-equiv": "Content-Security-Policy"})["content"]
    assert policy == render.CSP                                                       # the base policy: no script-src
    assert [link["href"] for link in page.find_all("link", rel="stylesheet")] == ["../styles.css", "../bell.css"]
    assert (site / "bell.css").read_bytes() == BELL_CSS.read_bytes()
    nav = page.select_one("nav.site-nav")
    assert nav.find("a", attrs={"aria-current": "page"}).get_text() == "Athletics"
    assert page.h2.get_text() == "The Monon Bell, year by year"
    footer = page.find("footer").get_text(" ", strip=True)
    assert "Data last changed" in footer and "series.json" in footer                 # the athletics data block
    assert render.curly(render.MEN_ONLY) in footer and render.DISCLAIMER in footer


def test_only_the_bell_page_loads_bell_css(site):
    users = [p.relative_to(site).as_posix() for p in site.rglob("*.html")
             if "bell.css" in p.read_text(encoding="utf-8")]
    assert users == ["monon-bell/index.html"]


def test_the_head_has_the_canonical_address_a_description_and_the_athletics_share_image(site, page):
    address = "https://depauwvswabash.com/monon-bell/"
    assert page.find("link", rel="canonical")["href"] == address
    assert page.find("meta", property="og:url")["content"] == address
    assert page.title.get_text() == "The Monon Bell, year by year | DePauw vs. Wabash"
    described = page.find("meta", attrs={"name": "description"})["content"]
    assert described == ("Who held the Monon Bell after every season since 1932: DePauw 46 seasons, Wabash 47, and "
                         "34 changes of hands. DePauw has held it since 2022.")
    home = soup((site / "index.html").read_text(encoding="utf-8"))
    assert page.find("meta", property="og:image")["content"] == home.find("meta", property="og:image")["content"]
    locs = re.findall(r"<loc>([^<]+)</loc>", (site / "sitemap.xml").read_text(encoding="utf-8"))
    assert locs.count(address) == 1


def test_the_links_in_and_out(site, page):
    home = soup((site / "index.html").read_text(encoding="utf-8"))
    strip_links = [(a["href"], a.get_text()) for a in home.select("section.bell-strip .bell-rec a")]
    assert strip_links == [("#monon-bell", "The Bell’s story"), ("monon-bell/", "The Bell, year by year")]
    history = soup((site / "heist-details" / "index.html").read_text(encoding="utf-8"))
    line = history.select_one("p.heist-yearly")
    assert (line.a["href"], line.a.get_text()) == ("../monon-bell/", "The Bell, year by year")
    assert line.find_next("div", class_="heist-timeline") is not None               # above the timeline
    foot = [(a["href"], a.get_text()) for a in page.select("p.bt-more a")]
    assert foot == [("../heist-details/", "Read the Bell’s heist history"), ("../", render.BACK_ATHLETICS)]
    assert page.select_one("p.back a")["href"] == "../"


def test_a_series_without_bell_games_still_builds_the_page():
    data = frozen()
    old = [game for game in data["sports"]["football"]["games"] if int(game["date"][:4]) < facts.BELL_START]
    record = facts.tally(old)
    removed = data["sports"]["football"]["games_played"] - len(old)
    data["sports"]["football"].update(games=old, games_played=len(old), current_streak=None, record={
        "depauw_wins": record["W"], "wabash_wins": record["L"], "ties": record["T"]})
    played = frozen()["sports"]["football"]["record"]
    for key, letter in (("depauw_wins", "W"), ("wabash_wins", "L"), ("ties", "T")):
        data["totals"][key] -= played[key] - record[letter]
    data["totals"]["games_played"] -= removed
    page = bell_page(data, NOW)
    assert page.select_one("table.bt-grid") is None and "No Bell game is on record yet." in page.get_text()
    assert home_page(data, NOW).select_one('a[href="monon-bell/"]') is None          # no strip, so no link from it


# ---- the clock --------------------------------------------------------------------------------------------------

def test_on_bell_game_day_the_season_is_not_yet_played_until_the_result_is_in():
    underway = eastern(2026, 11, 14, 13, 30)                                         # kickoff was 1:07 PM
    before = bell_page(frozen(), underway)
    cell = cells(before)[2026]
    assert (cell["class"], shown(cell), said(cell)) == (["bt-next"], "…", "2026: not yet played")
    assert "DePauw 46" in before.get_text()                                          # the season under way is not counted
    after = bell_page(with_football_game(frozen(), game_2026("W", 16, 10)), underway)
    cell = cells(after)[2026]
    assert (cell["class"], shown(cell), said(cell)) == (["bt-d"], "D", "2026: DePauw won 16 to 10 and kept the Bell")
    assert "DePauw 47" in after.get_text() and "5 straight wins" in after.get_text()
    assert not any("not been played" in li.get_text() for li in after.select("ul.bt-legend li"))


def test_at_new_year_the_new_season_appears_as_not_yet_played():
    data = with_football_game(frozen(), game_2026("W", 16, 10))
    old_year, new_year = bell_page(data, eastern(2026, 12, 31, 23, 30)), bell_page(data, eastern(2027, 1, 1, 0, 30))
    assert (shown(cells(old_year)[2027]), said(cells(old_year)[2027])) == ("", "2027: a future season")
    assert (shown(cells(new_year)[2027]), said(cells(new_year)[2027])) == ("…", "2027: not yet played")
    numbers = lambda page: [dd.get_text() for dd in page.select(".bt-card dl.facts dd")]   # noqa: E731
    assert numbers(old_year) == numbers(new_year)                                    # nothing is counted for 2027 yet
    assert str(old_year.select_one(".bell-now")) == str(new_year.select_one(".bell-now"))
    assert [row.th.get_text() for row in new_year.select("table.bt-grid tbody tr")][-1] == "2020s"


def test_a_new_decade_gets_a_row_when_its_first_season_begins():
    data = with_football_game(frozen(), game_2026("W", 16, 10))
    decades = lambda now: [row.th.get_text() for row in bell_page(data, now).select("table.bt-grid tbody tr")]  # noqa: E731
    assert decades(eastern(2029, 12, 31, 23, 30))[-1] == "2020s"
    assert decades(eastern(2030, 1, 1, 0, 30))[-2:] == ["2020s", "2030s"]


# ---- the stylesheet ---------------------------------------------------------------------------------------------

def test_bell_css_stays_within_its_budget_and_the_shared_stylesheet_did_not_grow_for_this_page():
    shipped = len(BELL_CSS.read_bytes().replace(b"\r\n", b"\n"))                     # as it ships: LF line endings
    assert 0 < shipped <= BELL_CSS_LIMIT
    shared = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    assert ".bt-" not in shared and ".bell-page" not in shared                       # this page's rules live in bell.css


def test_bell_css_takes_every_color_from_the_shared_tokens_and_loads_nothing():
    css = re.sub(r"/\*.*?\*/", "", BELL_CSS.read_text(encoding="utf-8"), flags=re.S)
    assert re.findall(r"#[0-9a-fA-F]{3,8}\b", css) == []
    assert re.findall(r"\b(?:rgba?|hsla?|oklch|oklab|lab|lch|hwb|color)\(", css) == []
    mixes = re.findall(r"color-mix\(([^;]*)\);", css)                                  # a tint: two tokens, nothing else
    assert len(mixes) == 2
    assert all(re.fullmatch(r"in srgb, var\(--color-[a-z-]+\) [0-9.]+%, var\(--color-surface\)", m) for m in mixes)
    assert "--color-" not in re.sub(r"var\(--color-[a-z0-9-]+\)", "", css)           # tokens are used, never defined
    assert "@import" not in css and "url(" not in css and "@font-face" not in css
    shared = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    for name in re.findall(r"var\(--([a-z0-9-]+)\)", css):
        assert f"--{name}:" in shared, name                                           # every token it uses exists
