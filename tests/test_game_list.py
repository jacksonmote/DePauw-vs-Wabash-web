"""The every-game list on a sport card: the decade groups (boundaries, each decade's record, which one is open, the
undated group last), the links into the home page (none may land inside a closed group), and the phone layout,
which changes only the stylesheet, so every value keeps its column name for a screen reader.

Exact counts come from the frozen fixture and made-up games; nothing here reads the clock, the network or the
bot-updated data. The browser side (axe-core with the lists closed and open) is the `axe` job in checks.yml."""

import datetime as dt
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import pytest
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
import facts  # noqa: E402
import render  # noqa: E402

NOW = dt.datetime(2026, 10, 30, 12, tzinfo=dt.timezone.utc)
FROZEN = ROOT / "tests" / "fixtures" / "series-frozen-2026-10-30.json"
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8").replace("\r\n", "\n")
SEP = "\u00a0· "                              # a no-break space, then the dot: a line never starts with the dot
PHONE_MAX = "42rem"                                # the widest screen that gets the two-line layout
# the hint above every list; a list with undated games adds one sentence about them
HINT = "Newest first, by decade. Home and away, W (win), L (loss), and T (tie) are from DePauw’s point of view."


def game(date, result, d=None, w=None, **extra):
    """A made-up game; the score follows the result unless given."""
    if d is None:
        d, w = {"W": (2, 1), "L": (1, 2), "T": (1, 1)}[result]
    return {"date": date, "season": date[:4], "location": "home", "site": None, "result": result,
            "depauw": d, "wabash": w, "note": None, "boxscore": None, **extra}


def dates(groups):
    return [(decade, [g["date"] for g in games]) for decade, games in groups]


# --- grouping -------------------------------------------------------------------------------------------------------

def test_a_decade_runs_from_its_year_0_to_its_year_9_by_the_date_of_the_game():
    games = [game("1929-11-23", "W"), game("1929-12-31", "L", season="1929-30"),
             game("1930-01-01", "W", season="1929-30"),          # the same season, the next decade
             game("1939-12-31", "T"), game("1940-01-01", "W"), game("1999-12-31", "L"), game("2000-01-01", "W")]
    assert dates(facts.by_decade(games)) == [
        (2000, ["2000-01-01"]), (1990, ["1999-12-31"]), (1940, ["1940-01-01"]),
        (1930, ["1939-12-31", "1930-01-01"]), (1920, ["1929-12-31", "1929-11-23"])]
    # a decade nobody played in (the 1950s to the 1980s here) has no group at all
    assert [d for d, _ in facts.by_decade(games)] == [2000, 1990, 1940, 1930, 1920]


def test_groups_and_games_are_newest_first_and_a_doubleheader_keeps_its_later_game_first():
    opener, nightcap = game("2024-04-06", "W", 5, 1), game("2024-04-06", "L", 2, 3)
    games = [game("2019-04-06", "W"), opener, nightcap, game("2025-04-05", "W")]         # stored oldest first
    groups = facts.by_decade(games)
    assert [d for d, _ in groups] == [2020, 2010]
    assert groups[0][1] == [games[3], nightcap, opener]
    assert facts.by_decade([]) == []


def test_games_with_no_recorded_date_are_one_last_group_whatever_their_placeholder_date_says():
    undated = [game("1888-01-03", "L", date_known=False, season="1887-88"), game("1915-01-01", "W", date_known=False),
               game("1954-01-01", "W", date_known=False, season="1953-54")]
    dated = [game("1915-05-08", "W"), game("1954-12-04", "T"), game("2026-04-04", "W")]
    games = sorted(undated + dated, key=lambda g: g["date"])
    groups = facts.by_decade(games)
    assert [d for d, _ in groups] == [2020, 1950, 1910, None]                           # the undated group is last
    assert dates(groups)[-1][1] == ["1954-01-01", "1915-01-01", "1888-01-03"]           # newest season first
    assert all(facts.is_dated(g) for d, group in groups[:-1] for g in group)            # and never inside a decade
    assert dates(facts.by_decade(undated)) == [(None, ["1954-01-01", "1915-01-01", "1888-01-03"])]


@pytest.mark.parametrize("results, words", [
    ("WWWWL", "DePauw 4-1"), ("LLLLLLWWWT", "Wabash 6-3-1"), ("WWLL", "Split 2-2"), ("WLT", "Split 1-1-1"),
    ("WWT", "DePauw 2-0-1"), ("L", "Wabash 1-0"), ("TT", "Split 0-0-2"),
])
def test_a_record_names_its_leader_first_and_counts_ties_last(results, words):
    games = [game(f"20{20 + i}-01-01", r) for i, r in enumerate(results)]
    assert facts.tally(games) == {"W": results.count("W"), "L": results.count("L"), "T": results.count("T")}
    assert facts.record_words(facts.tally(games)) == words


def test_the_season_by_season_list_and_the_decades_word_a_record_the_same_way():
    games = [game("2024-01-06", "W", season="2023-24"), game("2024-02-03", "W", season="2023-24"),
             game("2025-01-04", "W", season="2024-25"), game("2025-02-01", "L", season="2024-25")]
    assert facts.season_series(games) == [("2024-25", "Split 1-1"), ("2023-24", "DePauw 2-0")]
    assert render.decade_summary(2020, games) == f"2020s{SEP}4 games{SEP}DePauw 3-1"


def test_a_decades_summary_gives_its_name_its_count_and_its_record_from_its_own_games():
    five = [game(f"202{i}-11-14", r) for i, r in enumerate("LWWWW", 1)]
    assert render.decade_summary(2020, five) == f"2020s{SEP}5 games{SEP}DePauw 4-1"
    assert render.decade_summary(1950, [game(f"195{i}-11-14", r) for i, r in enumerate("LLLLLLWWWT")]) == \
        f"1950s{SEP}10 games{SEP}Wabash 6-3-1"
    assert render.decade_summary(1900, five[:1], ("match", "matches")) == f"1900s{SEP}1 match{SEP}Wabash 1-0"
    assert render.decade_summary(None, five[:2], ("meet", "meets")) == f"Date not recorded{SEP}2 meets{SEP}Split 1-1"
    assert render.decade_label(None) == render.UNDATED_GROUP == "Date not recorded"
    assert facts.SEPARATOR == SEP            # one separator for every dotted line (the heist cards use it too)


# --- the rendered list ----------------------------------------------------------------------------------------------

def groups_of(html):
    """[(summary text, is open, rows)] for each decade group of one rendered game list."""
    soup = BeautifulSoup(html, "html.parser")
    return [(d.find("summary").get_text(), d.has_attr("open"), d.select("tbody tr"))
            for d in soup.select("details.games details")]


def test_the_newest_decade_is_open_every_other_group_is_closed_and_the_whole_list_starts_closed():
    games = [game("1915-01-01", "W", date_known=False), game("1999-11-13", "T"), game("2019-11-16", "W"),
             game("2021-11-13", "L"), game("2026-11-14", "W")]
    html = render.games_table("football", games, name="Football")
    soup = BeautifulSoup(html, "html.parser")
    assert not soup.select_one("details.games").has_attr("open")
    assert [(text, is_open, len(rows)) for text, is_open, rows in groups_of(html)] == [
        (f"2020s{SEP}2 games{SEP}Split 1-1", True, 2), (f"2010s{SEP}1 game{SEP}DePauw 1-0", False, 1),
        (f"1990s{SEP}1 game{SEP}Split 0-0-1", False, 1), (f"Date not recorded{SEP}1 game{SEP}DePauw 1-0", False, 1)]
    # disclosures are native: a summary first in each, no script, no tabindex, nothing but the open attribute
    for d in soup.find_all("details"):
        assert d.find(True, recursive=False).name == "summary" and set(d.attrs) <= {"class", "open"}
    assert soup.find_all(tabindex=True) == [] and soup.find_all("script") == []


def test_a_list_with_only_undated_games_opens_nothing_and_an_empty_list_has_no_group():
    html = render.games_table("mens-tennis", [game("1915-01-01", "W", date_known=False)], ("match", "matches"))
    assert [(text, is_open) for text, is_open, _ in groups_of(html)] == [
        (f"Date not recorded{SEP}1 match{SEP}DePauw 1-0", False)]
    assert groups_of(render.games_table("football", [])) == []


def test_every_per_game_detail_is_still_in_its_row():
    """The score, home or away and the place, the box-score link, an overtime note, a disputed score, a meet with
    half points and a meet with no score: each is in the row, whatever the width."""
    box = "https://depauwtigers.com/boxscore.aspx?id=1&path=football"
    games = [
        game("1934-01-05", "W", None, None, score_known=False, source="wabash"),
        game("2006-11-03", "W", 172.5, 127.5, location="away", site="Crawfordsville, IN"),
        game("2023-11-11", "W", 33, 30, note="(2ot)", boxscore=box, location="neutral", site="Indianapolis, IN"),
        game("2024-04-06", "W", 12, 5, disputed=True, wabash_version={"depauw": 11, "wabash": 5}, source="both"),
    ]
    games[0].update(depauw=None, wabash=None)
    rows = BeautifulSoup(render.games_table("mens-swimming-and-diving", games, ("meet", "meets"), merged=True),
                         "html.parser").select("tbody tr")
    text = [[c.get_text(" ", strip=True) for c in r.find_all(["th", "td"])] for r in rows]
    assert text[0] == ["Apr 6, 2024", "Home · Greencastle, Ind.", "W DePauw 12, Wabash 5",
                       "DePauw’s record (DePauw-Wabash): 12-5 · Wabash’s record (DePauw-Wabash): 11-5", ""]
    assert text[1] == ["Nov 11, 2023", "Neutral · Indianapolis, Ind.", "W DePauw 33, Wabash 30", "2 OT", "Box score"]
    assert rows[1].find("a")["href"] == box
    assert text[2] == ["Nov 3, 2006", "Away · Crawfordsville, Ind.", "W DePauw 172.5, Wabash 127.5", "", ""]
    assert text[3][:3] == ["Jan 5, 1934", "Home · Greencastle, Ind.", "W DePauw won · score not recorded"]
    assert "Wabash record only" in text[3][3] and "Score not recorded" in text[3][3]


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("game-list") / "site"
    render.build(FROZEN, out, now=NOW, upcoming_path=out / "missing.json")
    return out


@pytest.fixture(scope="module")
def home(site):
    return BeautifulSoup((site / "index.html").read_text(encoding="utf-8"), "html.parser")


def test_the_frozen_football_list_reads_as_expected(home):
    summaries = [d.find("summary").get_text() for d in home.select("#football details.games details")]
    assert summaries[:3] == [f"2020s{SEP}5 games{SEP}DePauw 4-1", f"2010s{SEP}10 games{SEP}Wabash 8-2",
                             f"2000s{SEP}10 games{SEP}Wabash 6-4"]
    assert summaries[-1] == f"1890s{SEP}7 games{SEP}DePauw 5-2" and len(summaries) == 14
    assert f"1950s{SEP}10 games{SEP}Wabash 5-3-2" in summaries                          # ties are counted, last
    last = [d.find("summary").get_text() for d in home.select("details.games > details:last-of-type")]
    assert f"Date not recorded{SEP}64 games{SEP}Wabash 36-27-1" in last                 # baseball
    assert f"Date not recorded{SEP}88 matches{SEP}DePauw 80-7-1" in last                # tennis
    assert sum(text.startswith(render.UNDATED_GROUP) for text in last) == 2             # and no other sport has one


def test_on_every_card_the_groups_hold_every_game_once_and_their_records_add_up(home):
    data = json.loads(FROZEN.read_text(encoding="utf-8"))
    cards = home.select("article.sport")
    assert [c["id"] for c in cards] == list(data["sports"])
    for card in cards:
        s = data["sports"][card["id"]]
        outer = card.select_one("details.games")
        groups = outer.find_all("details", recursive=False)
        assert not outer.has_attr("open")
        assert [g.has_attr("open") for g in groups] == [True] + [False] * (len(groups) - 1), card["id"]
        names = [g.find("summary").get_text().split(SEP)[0] for g in groups]
        decades = [n for n in names if n != render.UNDATED_GROUP]
        assert decades == sorted(decades, reverse=True) and len(set(names)) == len(names)
        assert names.index(render.UNDATED_GROUP) == len(names) - 1 if render.UNDATED_GROUP in names else True
        won = lost = tied = rows = 0
        for g, name in zip(groups, names):
            _, count, record = g.find("summary").get_text().split(SEP)
            results = [r.find("abbr").get_text() for r in g.select("tbody tr")]
            assert int(count.split()[0]) == len(results), (card["id"], name)
            assert record == facts.record_words({r: results.count(r) for r in "WLT"}), (card["id"], name)
            for r in g.select("tbody tr"):                                              # each game sits in its decade
                stamp = r.find("time")
                if name == render.UNDATED_GROUP:
                    assert "date not recorded" in r.find("th").get_text()
                else:
                    assert stamp["datetime"][:3] + "0s" == name and "not recorded" not in r.find("th").get_text()
            won, lost, tied, rows = won + results.count("W"), lost + results.count("L"), tied + results.count("T"), rows + len(results)
        rec = s["record"]
        assert (won, lost, tied, rows) == (rec["depauw_wins"], rec["wabash_wins"], rec["ties"], s["games_played"])
        assert outer.find("summary").get_text().startswith(f"All {s['games_played']:,} ")


# --- links into the home page ---------------------------------------------------------------------------------------

def home_targets(site_dir):
    """{fragment: [(page, link text)]} for every link on the built site that points at an id on the home page."""
    found = {}
    for file in sorted(site_dir.rglob("*.html")):
        here = "/" + file.relative_to(site_dir).as_posix().replace("index.html", "")
        page = BeautifulSoup(file.read_text(encoding="utf-8"), "html.parser")
        for a in page.find_all("a", href=True):
            target = urlparse(urljoin(render.SITE_URL.rstrip("/") + here, a["href"]))
            if target.hostname == urlparse(render.SITE_URL).hostname and target.path == "/" and target.fragment:
                found.setdefault(target.fragment, []).append((here, a.get_text(" ", strip=True)))
    return found


BELL_GAME = {"football": {"schedule_url": "https://depauwtigers.com/sports/football/schedule", "games": [{
    "date": "2026-11-14", "time": "1:07 PM", "time_note": None, "location": "away", "status": "scheduled",
    "site": "Hollett Little Giant Stadium, Crawfordsville, Ind.", "doubleheader": False}]}}


@pytest.mark.parametrize("now, upcoming, banner", [
    (NOW, None, None),
    (dt.datetime(2026, 11, 14, 14, 0, tzinfo=dt.timezone.utc), BELL_GAME,                # 9 AM Eastern
     "Game day: Football vs. Wabash today, 1:07 PM"),
    (dt.datetime(2026, 11, 14, 18, 30, tzinfo=dt.timezone.utc), BELL_GAME,               # 1:30 PM Eastern
     "Football vs. Wabash is underway or just finished."),
    (dt.datetime(2025, 11, 16, 1, 0, tzinfo=dt.timezone.utc), None, "Final: DePauw 41, Wabash 20"),
], ids=["ordinary-day", "game-morning", "underway", "final-banner"])
def test_no_link_lands_inside_a_closed_group_so_every_link_target_is_in_view(tmp_path, now, upcoming, banner):
    """Every link to a place on the home page (the jump list, the headings' own links, the Bell strip, the game-day
    banner's final score, "That year's Bell game" on the heist history, the joke page's Bell link) goes to a card or
    a section, never to something inside a disclosure, in each state of the game-day banner. A closed <details>
    hides what is in it. Current engines open it themselves for a link to something inside (Chrome 157, Firefox
    157 and Playwright's WebKit 27.2 did, from a fresh load and from a link within the page, with JavaScript on
    and off, on a test copy on Nov 16, 2026); older browsers were not tested. So the rule stays: no id and no link
    inside a game list without a maintainer decision, and this test fails the day one is added."""
    out, up = tmp_path / "site", tmp_path / "upcoming.json"                           # no file: nothing scheduled
    if upcoming:
        up.write_text(json.dumps({"schema_version": 1, "sports": upcoming}), encoding="utf-8")
    render.build(FROZEN, out, now=now, upcoming_path=up)
    page = BeautifulSoup((out / "index.html").read_text(encoding="utf-8"), "html.parser")
    shown = page.find(id="game-day")
    assert (banner in shown.get_text()) if banner else shown is None                    # the state asked for
    targets = home_targets(out)
    sports = [c["id"] for c in page.select("article.sport")]
    assert set(sports) | {"monon-bell", "next-meeting", "main"} >= set(targets) >= set(sports) | {"monon-bell", "main"}
    for fragment, uses in targets.items():
        el = page.find(id=fragment)
        assert el is not None, (fragment, uses)
        assert el.find_parent("details") is None, (fragment, uses)
    if banner and banner.startswith("Final"):                                           # the final score is a link
        assert ("/", banner) in targets["football"]
    elif banner:                                                                        # before it, plain text
        assert shown.find_all("a") == []
    assert ("/heist-details/", ) == tuple({p for p, _ in targets["football"]} - {"/"})  # the Bell-game links
    assert ("/bell-heist/", "Check the real Bell holder") in targets["monon-bell"]
    # nothing inside a game list can be linked to at all: no id, and no link but a box score
    for details in page.select("details.games"):
        assert details.find_all(id=True) == [] and not details.has_attr("id")
        assert {a.get_text() for a in details.find_all("a")} <= {"Box score"}
    assert page.find("svg").find_all("a") == []                                         # the charts link nowhere


def test_the_script_does_nothing_for_links_because_none_goes_into_a_game_list():
    script = (ROOT / "static" / "easter-eggs.js").read_text(encoding="utf-8")
    assert "hashchange" not in script and "location.hash" not in script


# --- the phone layout -----------------------------------------------------------------------------------------------

def test_every_table_keeps_its_caption_column_names_and_a_header_cell_per_row(home):
    """The phone layout is CSS only: the markup is the same table at every width, so each value still has its
    column name (the header row is clipped on a phone, not removed) and each row its date as the row header."""
    tables = home.select("details.games table")
    assert len(tables) == len(home.select("details.games details")) > 50
    for table in tables:
        assert table.caption.get_text(strip=True) and "sr-only" in table.caption["class"]
        head = table.select("thead tr th")
        assert [th.get_text() for th in head] == list(render.GAME_COLUMNS)
        assert all(th["scope"] == "col" for th in head)
        for row in table.select("tbody tr"):
            cells = row.find_all(True, recursive=False)
            assert [c.name for c in cells] == ["th", "td", "td", "td", "td"]            # one value per column name
            assert cells[0]["scope"] == "row" and cells[0].get_text(strip=True)
            assert all(not c.attrs or set(c.attrs) == {"class"} for c in cells[1:])     # no label attribute needed
    assert "data-label" not in str(home)


def test_the_result_letter_is_text_and_always_agrees_with_the_words_and_the_edge(home):
    """Never color alone: the W, L or T is in the page as text, and the winner is named in the words after it."""
    names = {"W": "DePauw", "L": "Wabash", "T": "Tie"}
    seen = set()
    for row in home.select("details.games tbody tr"):
        cell = row.find_all("td")[render.GAME_COLUMNS.index("Result") - 1]
        letter = cell.find("abbr").get_text()
        assert cell["class"] == [render.RESULT_CLASSES[letter]]
        assert cell.get_text().startswith(f"{letter} {names[letter]}"), cell
        seen.add(letter)
    assert seen == set(render.RESULT_LETTERS) == set(render.RESULT_CLASSES)
    hints = home.select("details.games > p.hint")
    assert len(hints) == len(home.select("article.sport"))
    for hint in hints:
        assert hint.get_text().startswith(HINT)
        assert hint.find("span", class_="wl").get_text() == ", W (win), L (loss), and T (tie)"


def test_a_card_with_undated_games_says_they_are_in_no_decades_count_and_no_other_card_does(home):
    """A decade's summary counts only the games dated in it, so on a card with undated games the hint says where
    those are; a card with none says nothing about them."""
    said = {}
    for card in home.select("article.sport"):
        hint = card.select_one("details.games > p.hint").get_text()
        names = [d.find("summary").get_text().split(SEP)[0] for d in card.select("details.games > details")]
        assert (render.UNDATED_GROUP in names) == (names[-1] == render.UNDATED_GROUP) == (hint != HINT), card["id"]
        if hint != HINT:
            said[card["id"]] = hint[len(HINT):]
    assert said == {
        "baseball": " Games with no recorded date are in the last group and aren’t included in any decade’s count.",
        "mens-tennis": " Matches with no recorded date are in the last group and aren’t included in any decade’s "
                       "count."}


def test_the_undated_sentence_follows_the_group_and_names_what_the_sport_plays():
    undated, dated = game("1915-01-01", "W", date_known=False), game("2026-11-14", "W")
    hint = lambda *args: BeautifulSoup(render.games_table(*args), "html.parser").select_one("p.hint").get_text()
    assert hint("football", [dated]) == HINT and hint("football", []) == HINT
    assert hint("football", [undated, dated]) == f"{HINT} Games {render.UNDATED_HINT}"
    assert hint("mens-swimming-and-diving", [undated], ("meet", "meets")) == f"{HINT} Meets {render.UNDATED_HINT}"
    assert render.UNDATED_HINT == ("with no recorded date are in the last group and aren’t included in any "
                                   "decade’s count.")


def phone_block():
    blocks = re.findall(r"@media \(max-width: ([\d.]+rem)\) \{\n(.*?)\n\}", CSS, flags=re.S)
    games = [(width, body) for width, body in blocks if ".games" in body]
    assert len(games) == 1
    return games[0]


def test_the_phone_layout_clips_the_labels_instead_of_removing_them():
    width, block = phone_block()
    assert width == PHONE_MAX
    clip = re.search(r"\.games thead, \.games \.date-note \{([^}]*)\}", block).group(1)
    assert "position: absolute" in clip and "clip-path: inset(50%)" in clip and "overflow: hidden" in clip
    assert "display: none" not in block and "visibility" not in block and "content:" not in block
    assert ".games td:empty" not in CSS                       # an empty cell stays a cell, so the columns still line up
    # the letter and its hint are shown here and hidden everywhere else
    assert ".games abbr, .wl { display: inline; }" in block
    assert CSS.count(".games abbr, .wl { display: none; }") == 1 and ".games abbr, .wl { display: none; }" not in block


def test_the_phone_layout_places_every_column_and_no_other():
    """The stylesheet places the cells by their position in the row, so it has to agree with GAME_COLUMNS: the date
    (the row header) leads line one, and each of the four other columns is placed exactly once."""
    _, block = phone_block()
    placed = re.findall(r"\.games td:nth-child\((\d)\) \{ grid-area: (\d)", block)
    columns = {int(child): int(row) for child, row in placed}
    assert sorted(columns) == list(range(2, len(render.GAME_COLUMNS) + 1))
    line = {render.GAME_COLUMNS[child - 1]: row for child, row in columns.items()}
    assert line == {"Result": 1, "Site": 2, "Box score": 2, "Notes": 3}                # two lines, notes under them
    assert re.search(r"\.games tbody tr \{ display: grid;", block)
    assert re.search(r"\.games td a \{ padding-block: [\d.]+rem; \}", block)            # a taller target for the link


def test_on_wide_screens_every_decades_table_has_the_same_columns():
    """Each decade is a table of its own, so left to themselves the columns would sit in different places from
    one decade to the next. From 64rem the layout is fixed: four columns get a width and the site takes the rest."""
    block = re.search(r"@media \(min-width: 64rem\) \{\n(.*?)\n\}", CSS, flags=re.S).group(1)
    assert ".games table { table-layout: fixed; }" in block
    # a fixed column cannot grow, so the date may wrap there: "1956 (date not recorded)" must never run over the site
    assert ".games tbody th { white-space: normal; }" in block
    sized = re.findall(r"\.games thead th:(first-child|nth-child\(\d\)|last-child) \{ width: [\d.]+(?:rem|%); \}",
                       block)
    assert sized == ["first-child", "nth-child(3)", "nth-child(4)", "last-child"]
    assert render.GAME_COLUMNS[1] == "Site" and len(render.GAME_COLUMNS) == 5        # the one left to flex
    assert "table-layout" not in phone_block()[1]


def test_decade_summaries_get_the_focus_ring_and_a_separating_line():
    assert re.search(r"a:focus-visible, summary:focus-visible, button:focus-visible \{ outline: 3px solid", CSS)
    # 0.625rem above and below a line of text: 44 px tall, like the nav links
    assert ".games summary { cursor: pointer; font-weight: 600; padding: 0.625rem 0; }" in CSS
    assert ".games details { border-top: var(--border); }" in CSS
