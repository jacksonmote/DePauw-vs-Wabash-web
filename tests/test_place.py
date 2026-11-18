"""Where a game was played, when the two schools' pages say different things, and the wording around it:
  - a home/away (or neutral-site) disagreement is flagged like a date or score disagreement, DePauw's place is the
    game's, and Wabash's site is never borrowed for a place DePauw's page contradicts (no "Away · Greencastle");
  - a game only Wabash lists, which its page marks neutral with no site, reads "Site not recorded";
  - a tie is written one way everywhere ("Tie 6–6");
  - football's close-games fact carries its margin in the label."""
import csv
import datetime as dt
import html
import io
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import facts  # noqa: E402
import heists  # noqa: E402
import merge  # noqa: E402
import render  # noqa: E402
import scrape  # noqa: E402
from test_merge import g  # noqa: E402
from test_overrides import committed  # noqa: E402
from test_scrape import both_dir, fixture_dir  # noqa: E402,F401  (pytest fixtures)

ROOT = Path(__file__).resolve().parent.parent
OVERRIDES = ROOT / "data" / "overrides.json"
NOW = dt.datetime(2026, 12, 1, 15, 0, tzinfo=dt.timezone.utc)


def one(d, w):
    [game] = merge.merge_sport([d], [w]).games
    return game


# ---- the merge ---------------------------------------------------------------------------------- #

def test_a_home_and_away_disagreement_is_flagged_and_wabashs_site_is_not_borrowed():
    game = one(g("1903-11-21", 0, 10, loc="away"), g("1903-11-21", 0, 10, loc="home", site="Greencastle, IN"))
    assert (game["location"], game["site"]) == ("away", None)            # DePauw's place; no "Away · Greencastle"
    assert game["place_differs"] is True
    assert game["wabash_place"] == {"location": "home", "site": "Greencastle, IN"}
    assert "disputed" not in game and "date_differs" not in game           # the place is its own kind of difference


def test_rows_that_agree_on_the_place_still_take_wabashs_site_and_carry_no_flag():
    game = one(g("1990-04-01", 5, 3, loc="away"), g("1990-04-01", 5, 3, loc="away", site="Crawfordsville, IN"))
    assert (game["location"], game["site"]) == ("away", "Crawfordsville, IN")
    assert "place_differs" not in game and "wabash_place" not in game
    neutral = one(g("1916-11-25", 0, 0, loc="neutral"), g("1916-11-25", 0, 0, loc="neutral", site="Indianapolis, IN"))
    assert (neutral["location"], neutral["site"]) == ("neutral", "Indianapolis, IN") and "place_differs" not in neutral


def test_wabashs_neutral_with_no_site_lists_no_place_so_it_disagrees_with_nothing():
    game = one(g("1985-04-10", 9, 0, loc="home", site="Greencastle, Ind."), g("1985-04-10", 9, 0, loc="neutral"))
    assert (game["location"], game["site"]) == ("home", "Greencastle, Ind.") and "place_differs" not in game
    assert merge.no_place({"location": "neutral", "site": None}) and merge.no_place({"location": "neutral", "site": ""})
    assert not merge.no_place({"location": "neutral", "site": "Indianapolis, IN"})
    assert not merge.no_place({"location": "away", "site": None})


def test_a_neutral_site_one_school_names_against_a_campus_game_on_the_other_is_flagged():
    game = one(g("2008-04-22", 3, 2, loc="neutral", site="Crawfordsville High School"),
               g("2008-04-22", 3, 2, loc="home", site="Crawfordsville, IN"))
    assert (game["location"], game["site"]) == ("neutral", "Crawfordsville High School")   # DePauw's own site stays
    assert game["wabash_place"] == {"location": "home", "site": "Crawfordsville, IN"}
    other = one(g("1915-11-20", 0, 34, loc="away"), g("1915-11-20", 0, 34, loc="neutral", site="Indianapolis, IN"))
    assert other["place_differs"] and (other["location"], other["site"]) == ("away", None)


def test_the_count_of_place_disagreements_is_in_the_stats_only_when_there_is_one():
    d = [g("1990-04-01", 5, 3, loc="away"), g("1990-04-09", 1, 2, loc="home")]
    w = [g("1990-04-01", 5, 3, loc="home", site="Greencastle, IN"), g("1990-04-09", 1, 2, loc="home")]
    assert merge.merge_sport(d, w).stats["place_differs"] == 1
    assert "place_differs" not in merge.merge_sport(d[1:], w[1:]).stats
    assert merge.place_differs(d[0], w[0]) and not merge.place_differs(d[1], w[1])
    assert not merge.place_differs({"location": "unknown"}, {"location": "home"})


def test_a_found_place_ruling_settles_the_place_so_nothing_is_left_to_flag():
    d, w, football = committed("football")
    game = next(x for x in football.games if x["date"] == "1915-11-20")       # DePauw's page says away; ruled neutral
    assert (game["location"], game["site"]) == ("neutral", "Indianapolis, IN") and "place_differs" not in game


def test_the_committed_pages_have_no_row_that_borrows_a_site_from_the_other_side():
    other_campus = {"home": "crawfordsville, in", "away": "greencastle, in"}     # Wabash's spelling of each town
    flagged = 0
    for key in ("football", "mens-basketball", "baseball", "mens-soccer", "mens-lacrosse", "mens-tennis",
                "mens-swimming-and-diving"):
        d, w, r = committed(key)
        for game in r.games:
            if game["location"] in other_campus:                # never "Away · Greencastle" or "Home · Crawfordsville"
                assert (game.get("site") or "").lower() != other_campus[game["location"]], game
            if game.get("place_differs"):
                flagged += 1
                assert game["source"] == "both" and game["wabash_place"]["location"] != game["location"]
        assert r.stats.get("place_differs", 0) == sum(1 for x in r.games if x.get("place_differs"))
    assert flagged > 0


# ---- the row ------------------------------------------------------------------------------------ #

def test_the_row_shows_depauws_place_and_says_where_wabashs_record_puts_the_game():
    game = one(g("1903-11-21", 0, 10, loc="away"), g("1903-11-21", 0, 10, loc="home", site="Greencastle, IN"))
    assert render.where_text(game) == "Away · Crawfordsville, Ind."
    assert "Wabash’s record lists this game at DePauw (Greencastle, Ind.)" in render.game_flags("football", game, True)
    assert render.place_words({"location": "away", "site": "Crawfordsville, IN"}) == "at Wabash (Crawfordsville, Ind.)"
    assert render.place_words({"location": "neutral", "site": "Indianapolis, IN"}) == \
        "at a neutral site (Indianapolis, Ind.)"
    assert render.place_words({"location": "home", "site": None}) == "at DePauw"
    # Wabash's row for Feb 26, 1957 says DePauw was at home and names Crawfordsville, as DePauw's row does: the
    # town is not what differs, so the note leaves it out
    odd = one(g("1957-02-26", 62, 65, loc="away", site="Crawfordsville, Ind."),
              g("1957-02-26", 62, 65, loc="home", site="Crawfordsville, IN"))
    assert render.where_text(odd) == "Away · Crawfordsville, Ind."
    assert "Wabash’s record lists this game at DePauw" in render.game_flags("mens-basketball", odd, True)


def test_a_wabash_only_game_with_no_place_reads_site_not_recorded_never_neutral():
    [undated] = merge.merge_sport([], [g("1907-01-01", 5, 7, loc="neutral")], season_months=(3, 4, 5)).games
    assert render.where_text(undated) == render.NO_SITE == "Site not recorded"
    [named] = merge.merge_sport([], [g("1916-11-25", 0, 0, loc="neutral", site="Indianapolis, IN")]).games
    assert render.where_text(named) == "Neutral · Indianapolis, Ind."       # a real neutral site keeps its word
    [depauw] = merge.merge_sport([g("2020-04-03", 3, 2, loc="neutral")], []).games
    assert render.where_text(depauw) == "Neutral"                             # DePauw's page has no such default
    [away] = merge.merge_sport([], [g("1950-05-01", 3, 2, loc="away")]).games
    assert render.where_text(away) == "Away · Crawfordsville, Ind."


# ---- one tie format ----------------------------------------------------------------------------- #

def test_a_tie_is_written_the_same_way_in_the_game_list_and_on_the_heist_history():
    tie = {"date": "1959-11-21", "result": "T", "depauw": 6, "wabash": 6}
    assert render.score_text(tie) == "Tie 6–6"
    assert heists.game_line(tie) == "That year’s Bell game: Tie 6–6"
    assert heists.game_line(tie, 1959) == "The 1959 Bell game: Tie 6–6"
    assert facts.TIE_FORMAT.format(depauw=6, wabash=6) == "Tie 6–6"
    assert heists.game_line({**tie, "result": "L", "depauw": 6, "wabash": 16}) == "That year’s Bell game: Wabash 16, DePauw 6"
    for source in ("render.py", "heists.py", "belltimeline.py", "howitworks.py"):
        assert "(tie)" not in (ROOT / "scraper" / source).read_text(encoding="utf-8"), source


# ---- football's close games ---------------------------------------------------------------------- #

def test_only_footballs_close_games_fact_carries_its_margin_in_the_label():
    assert facts.MARGIN_IN_LABEL == {"football"} and facts.CLOSE_WORD["football"] == "Close"
    assert facts.ONE_SCORE["football"] == 8                                  # the number in the label is this one


# ---- on the pages built from the October 2026 fixtures ------------------------------------------- #

@pytest.fixture
def built(both_dir, tmp_path):
    (tmp_path / "overrides.json").write_text(OVERRIDES.read_text(encoding="utf-8"), encoding="utf-8")
    assert scrape.main(["--fixtures", str(both_dir), "--output", str(tmp_path / "series.json"),
                        "--upcoming-output", str(tmp_path / "upcoming.json")]) == 0
    out = tmp_path / "site"
    render.build(tmp_path / "series.json", out, now=NOW, upcoming_path=tmp_path / "none.json",
                 overrides_path=tmp_path / "overrides.json")
    return out, json.loads((tmp_path / "series.json").read_text(encoding="utf-8"))


def text_of(markup):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", markup)))


def row_text(page, date):
    at = page.index(f">{date}</time>")
    return text_of(page[page.rindex("<tr>", 0, at):page.index("</tr>", at)])


def test_no_row_on_the_page_contradicts_itself_about_the_place(built):
    out, data = built
    page = (out / "index.html").read_text(encoding="utf-8")
    text = text_of(page)
    assert "Home · Crawfordsville, Ind." not in text and "Away · Greencastle, Ind." not in text
    rows = {
        "Nov 21, 1903": ("Away · Crawfordsville, Ind.", "Wabash’s record lists this game at DePauw (Greencastle, Ind.)"),
        "Nov 16, 1935": ("Away · Crawfordsville, Ind.", "Wabash’s record lists this game at DePauw (Greencastle, Ind.)"),
        "Nov 13, 1971": ("Home · Greencastle, Ind.", "Wabash’s record lists this game at Wabash (Crawfordsville, Ind.)"),
        "Feb 14, 1928": ("Home · Greencastle, Ind.", "Wabash’s record lists this game at Wabash (Crawfordsville, Ind.)"),
        "Feb 17, 1944": ("Home · Greencastle, Ind.", "Wabash’s record lists this game at Wabash (Crawfordsville, Ind.)"),
    }
    for date, (where, note) in rows.items():
        row = row_text(page, date)
        assert where in row and note in row, row
    assert "Wabash’s record lists this game at" not in row_text(page, "Nov 20, 1915")        # settled by a ruling
    flagged = sum(s["sources"].get("place_differs", 0) for s in data["sports"].values())
    assert flagged == text.count("Wabash’s record lists this game at ") > 0


def test_the_card_and_the_how_it_works_page_count_the_place_disagreements_from_the_data(built):
    out, data = built
    text = text_of((out / "index.html").read_text(encoding="utf-8"))
    for s in data["sports"].values():
        n = s["sources"].get("place_differs", 0)
        words = "matches" if s["name"].endswith("Tennis") else "meets" if "Swimming" in s["name"] else "games"
        if n > 1:
            assert f"{n} of the shared {words} are listed at different places in the two records." in text
        elif n == 1:
            assert f"1 of the shared {words} is listed at a different place in the two records." in text
    how = text_of((out / "how-it-works" / "athletics" / "index.html").read_text(encoding="utf-8"))
    total = sum(s["sources"].get("place_differs", 0) for s in data["sports"].values())
    no_site = sum(1 for s in data["sports"].values() for x in s["games"]
                  if x["source"] == "wabash" and x["location"] == "neutral" and not x.get("site"))
    assert f"and so are games they list at different places ({total} right now)" in how
    assert f"The site shows such a game as “Site not recorded” ({no_site} right now), not as a neutral-site game." in how
    assert no_site == text.count("Site not recorded") > 100
    assert "a person checks the two records, and other sources where there are any, and records a ruling, with a reason" in how
    assert "checks the box scores" not in how


def test_the_undated_games_say_site_not_recorded_and_real_neutral_sites_keep_the_word(built):
    out, _ = built
    page = (out / "index.html").read_text(encoding="utf-8")
    assert "Site not recorded" in row_text(page, "Apr 27, 1920")              # dated by a ruling, the place still unknown
    assert "Neutral · Indianapolis, Ind." in row_text(page, "Nov 20, 1915")
    cells = re.findall(r"<td>(Neutral[^<]*)</td>", page)
    assert cells and all(c.startswith("Neutral · ") for c in cells)           # no bare "Neutral" is left


def test_footballs_fact_reads_close_games_with_the_margin_and_the_other_sports_keep_their_labels(built):
    out, _ = built
    text = text_of((out / "index.html").read_text(encoding="utf-8"))
    assert re.search(r"Close games \(8 points or fewer\): \d+ · most recent: ", text)
    assert "One-score games: " in text and "decided by 3 points or fewer" in text          # basketball
    assert re.search(r"Close meets: \d+ decided by 10 points or fewer", text)              # swimming
    assert "decided by 8 points or fewer" not in text


def test_the_csv_download_matches_the_page_for_games_with_no_recorded_place(built):
    out, data = built
    rows = list(csv.DictReader(io.StringIO((out / "games.csv").read_text(encoding="utf-8"))))
    games = [x for s in data["sports"].values() for x in s["games"]]
    assert len(rows) == len(games)
    page = text_of((out / "index.html").read_text(encoding="utf-8"))
    unplaced = [(row, game) for row, game in zip(rows, games) if render.no_site(game)]
    assert len(unplaced) == page.count(render.NO_SITE) > 100           # the same games as on the page
    for row, game in unplaced:
        assert (row["location"], row["site"], row["source"]) == ("not recorded", "", "wabash"), row
        assert (game["location"], game.get("site")) == ("neutral", None)    # series.json keeps the merged data
    assert render.CSV_NO_SITE == "not recorded"
    for row, game in zip(rows, games):
        if not render.no_site(game):
            assert row["location"] == game["location"] != "not recorded", row
    # what is left as neutral in the download is a neutral site the page names, or DePauw's own listing
    neutral = [r for r in rows if r["location"] == "neutral"]
    assert neutral and all(r["site"] or r["source"] != "wabash" for r in neutral)
    assert {r["location"] for r in rows} <= {"home", "away", "neutral", "not recorded", "unknown"}


def test_a_game_with_no_recorded_place_is_not_recorded_in_the_csv_and_untouched_in_the_data():
    r = merge.merge_sport([g("2020-04-03", 3, 2, loc="neutral")],
                          [g("1907-01-01", 5, 7, loc="neutral"),
                           g("1916-11-25", 0, 0, loc="neutral", site="Indianapolis, IN")], season_months=(3, 4, 5, 11))
    data = {"sports": {"baseball": {"name": "Baseball", "games": r.games}}}
    rows = {row["source"] + ":" + row["site"]: row["location"]
            for row in csv.DictReader(io.StringIO(render.render_csv(data)))}
    assert rows == {"wabash:": "not recorded", "wabash:Indianapolis, IN": "neutral", "depauw:": "neutral"}
    assert [x["location"] for x in r.games] == ["neutral"] * 3                 # the merged games are not changed


def test_the_csv_download_says_where_wabashs_record_puts_a_game_it_lists_elsewhere(built):
    out, data = built
    rows = list(csv.DictReader(io.StringIO((out / "games.csv").read_text(encoding="utf-8"))))
    assert list(rows[0])[-4:] == ["ruling_notes", "place_differs", "wabash_record_location", "wabash_record_site"]
    row = next(r for r in rows if r["sport"] == "Football" and r["date"] == "1903-11-21")
    assert (row["location"], row["site"], row["place_differs"], row["wabash_record_location"],
            row["wabash_record_site"]) == ("away", "", "yes", "home", "Greencastle, IN")
    same = next(r for r in rows if r["sport"] == "Football" and r["date"] == "1915-11-20")
    assert (same["place_differs"], same["wabash_record_location"], same["wabash_record_site"]) == ("no", "", "")
    assert sum(r["place_differs"] == "yes" for r in rows) == \
        sum(s["sources"].get("place_differs", 0) for s in data["sports"].values())
