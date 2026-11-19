"""The rulings that name one row of one school's page, or choose a score (data/overrides.json, scraper/merge.py):
  set_aside  a row that is not a game between the two teams, or that repeats another row: not counted
  found      a date or a place a person found in another source
  note       a note shown on the game
  score      Wabash's version of a disputed score counts (DePauw's own records contradict its history page)
Each needs a reason and its sources, each fails the merge loudly when it no longer finds its row, and none can
change who won a game. The last part runs the committed rulings on the pages captured in October 2026 and on a
site built from them."""
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
import howitworks  # noqa: E402
import merge  # noqa: E402
import render  # noqa: E402
import review  # noqa: E402
import scrape  # noqa: E402
from test_merge import MONTHS, g  # noqa: E402
from test_overrides import committed  # noqa: E402
from test_scrape import both_dir, fixture_dir  # noqa: E402,F401  (pytest fixtures)

ROOT = Path(__file__).resolve().parent.parent
OVERRIDES = ROOT / "data" / "overrides.json"
NOW = dt.datetime(2026, 12, 1, 15, 0, tzinfo=dt.timezone.utc)
SRC = [{"label": "The Daily Example, May 2, 1900, p. 3", "url": "https://example.org/1900-05-02"}]


def load(tmp_path, sports):
    p = tmp_path / "overrides.json"
    p.write_text(json.dumps({"schema_version": 1, "sports": sports}), encoding="utf-8")
    return merge.load_overrides(p)


def rules(tmp_path, **kinds):
    """One sport's parsed rulings from their JSON form, as the scraper reads them."""
    return load(tmp_path, {"baseball": kinds})["baseball"]


def aside(school, date, score, **more):
    return {"school": school, "row": {"date": date, "score": score, **more}, "reason": "checked", "sources": SRC}


# ---- the file ---------------------------------------------------------------------------------- #

def test_the_new_kinds_are_parsed_with_their_reason_and_sources(tmp_path):
    r = rules(tmp_path,
              set_aside=[aside("wabash", "1944-01-06", "69-49")],
              found=[{**aside("wabash", "1900-01-01", "10-11"), "date": "1900-05-07", "location": "home",
                      "site": "Greencastle, IN"}],
              note=[{"school": "wabash", "row": {"date": "1934-01-05", "score": None}, "note": "No score.",
                     "reason": "kept"}],
              score=[{"depauw": {"date": "2011-11-09", "score": "205-80"},
                      "wabash": {"date": "2011-11-09", "score": "205-89"}, "use": "wabash", "reason": "recap",
                      "sources": SRC}])
    assert r["set_aside"] == [{"school": "wabash", "row": {"date": "1944-01-06", "score": (69, 49)},
                               "reason": "checked", "sources": SRC}]
    assert (r["found"][0]["date"], r["found"][0]["location"], r["found"][0]["site"]) == \
        ("1900-05-07", "home", "Greencastle, IN")
    assert r["note"][0]["row"]["score"] == (None, None) and r["note"][0]["sources"] == []     # a note may have none
    assert r["score"][0]["wabash"]["score"] == (205, 89) and r["score"][0]["use"] == "wabash"
    assert set(r) == set(merge.RULING_KINDS)


def test_a_same_game_ruling_may_name_its_sources(tmp_path):
    r = rules(tmp_path, same_game=[{"depauw": {"date": "1928-02-14", "score": "23-22"},
                                    "wabash": {"date": "1928-02-29", "score": "32-30"}, "reason": "papers",
                                    "sources": SRC}])
    assert r["same_game"][0]["sources"] == SRC


BAD = {
    "set_aside with no source": {"set_aside": [{**aside("wabash", "1944-01-06", "69-49"), "sources": []}]},
    "set_aside with no reason": {"set_aside": [{**aside("wabash", "1944-01-06", "69-49"), "reason": " "}]},
    "a source that is not https": {"set_aside": [{**aside("wabash", "1944-01-06", "69-49"),
                                                  "sources": [{"label": "x", "url": "http://example.org/"}]}]},
    "a source that is a bare link": {"set_aside": [{**aside("wabash", "1944-01-06", "69-49"),
                                                    "sources": ["https://example.org/"]}]},
    "an unknown school": {"set_aside": [aside("purdue", "1944-01-06", "69-49")]},
    "a row with no score field": {"set_aside": [{**aside("wabash", "1944-01-06", "69-49"),
                                                 "row": {"date": "1944-01-06"}}]},
    "a misspelt field": {"set_aside": [{**aside("wabash", "1944-01-06", "69-49"), "note": "x"}]},
    "found with nothing found": {"found": [aside("wabash", "1900-01-01", "10-11")]},
    "found with a date that is not one": {"found": [{**aside("wabash", "1900-01-01", "10-11"), "date": "May 7"}]},
    "found with an unknown location": {"found": [{**aside("wabash", "1900-01-01", "10-11"), "location": "there"}]},
    # a place that contradicts itself: home is Greencastle, away is Crawfordsville, neutral is a named place in neither
    "found with a home game in Crawfordsville": {"found": [{**aside("wabash", "1900-01-01", "10-11"),
                                                            "location": "home", "site": "Crawfordsville, IN"}]},
    "found with an away game in Greencastle": {"found": [{**aside("wabash", "1900-01-01", "10-11"),
                                                          "location": "away", "site": "Greencastle, IN"}]},
    "found with a neutral site in one of the two towns": {"found": [{**aside("wabash", "1900-01-01", "10-11"),
                                                                     "location": "neutral", "site": "Greencastle, IN"}]},
    "found with a neutral game and no site": {"found": [{**aside("wabash", "1900-01-01", "10-11"),
                                                         "location": "neutral"}]},
    # a date no season of the row could hold (the row's own season is checked when the ruling is applied)
    "found with a date years from its row": {"found": [{**aside("wabash", "1900-01-01", "10-11"),
                                                        "date": "1902-05-07"}]},
    "note without the note": {"note": [aside("wabash", "1934-01-05", None)]},
    "note that is too long": {"note": [{**aside("wabash", "1934-01-05", None), "note": "x" * (merge.NOTE_MAX + 1)}]},
    "score that picks DePauw's version": {"score": [{
        "depauw": {"date": "2011-11-09", "score": "205-80"}, "wabash": {"date": "2011-11-09", "score": "205-89"},
        "use": "depauw", "reason": "r", "sources": SRC}]},
    "score with no source": {"score": [{
        "depauw": {"date": "2011-11-09", "score": "205-80"}, "wabash": {"date": "2011-11-09", "score": "205-89"},
        "use": "wabash", "reason": "r", "sources": []}]},
    "a same_game source that is not https": {"same_game": [{
        "depauw": {"date": "1928-02-14", "score": "23-22"}, "wabash": {"date": "1928-02-29", "score": "32-30"},
        "reason": "r", "sources": [{"label": "x", "url": "ftp://example.org/"}]}]},
    "an unknown kind": {"not_a_game": []},
}


@pytest.mark.parametrize("name", BAD)
def test_a_malformed_ruling_is_an_error_not_silently_ignored(tmp_path, name):
    with pytest.raises(merge.MergeError):
        rules(tmp_path, **BAD[name])


# ---- set_aside ---------------------------------------------------------------------------------- #

def test_a_row_set_aside_is_not_counted_matched_or_listed(tmp_path):
    d = [g("1944-01-11", 39, 31), g("1944-02-17", 33, 28)]
    w = [g("1944-01-06", 69, 49), g("1944-01-11", 39, 31), g("1944-02-05", 28, 19), g("1944-02-17", 33, 28)]
    plain = merge.merge_sport(d, w)
    assert (len(plain.games), plain.stats["wabash_only"]) == (4, 2) and "rows_set_aside" not in plain.stats
    r = merge.merge_sport(d, w, rules=rules(tmp_path, set_aside=[aside("wabash", "1944-01-06", "69-49"),
                                                                 aside("wabash", "1944-02-05", "28-19")]))
    assert [x["date"] for x in r.games] == ["1944-01-11", "1944-02-17"] and merge.tally(r.games) == (2, 0, 0)
    assert r.stats == {"both": 2, "depauw_only": 0, "wabash_only": 0, "disputed": 0, "date_differs": 0,
                       "dates_not_recorded": 0, "rows_set_aside": 2,
                       "first_season": {"depauw": "1944", "wabash": "1944"}}
    assert [(x["school"], x["date"], x["depauw"], x["wabash"]) for x in r.set_aside] == [
        ("Wabash", "1944-01-06", 69, 49), ("Wabash", "1944-02-05", 28, 19)]


def test_a_repeated_row_needs_its_season_and_only_that_copy_is_set_aside(tmp_path):
    d = [g("2008-04-16", 9, 0, season="2007-08")]
    w = [g("2008-04-16", 9, 0, season="2007-2008", site="Greencastle, IN"),
         g("2008-04-16", 9, 0, season="2008", loc="neutral")]
    with pytest.raises(merge.MergeError, match='names 2 rows, not one: Wabash\'s row 2008-04-16 9-0. Add the "season"'):
        merge.merge_sport(d, w, rules=rules(tmp_path, set_aside=[aside("wabash", "2008-04-16", "9-0")]))
    r = merge.merge_sport(d, w, rules=rules(tmp_path, set_aside=[aside("wabash", "2008-04-16", "9-0", season="2008")]))
    [game] = r.games
    assert (game["source"], game["site"]) == ("both", "Greencastle, IN")           # the copy that stays is matched
    assert r.set_aside[0]["season"] == "2008" and r.stats["rows_set_aside"] == 1


def test_a_set_aside_ruling_that_no_longer_finds_its_row_fails_loudly(tmp_path):
    # Wabash took the row off its page (or the ruling was mistyped): the ruling must go, not linger.
    with pytest.raises(merge.MergeError, match=r"set_aside ruling no longer finds Wabash's row 1944-01-06 69-49 "):
        merge.merge_sport([], [g("1944-01-11", 39, 31)],
                          rules=rules(tmp_path, set_aside=[aside("wabash", "1944-01-06", "69-49")]))
    gone = aside("wabash", "2008-04-16", "9-0", season="2008")
    with pytest.raises(merge.MergeError, match=r"no longer finds Wabash's row 2008-04-16 9-0 \(season 2008\)"):
        merge.merge_sport([], [g("2008-04-16", 9, 0, season="2007-2008")], rules=rules(tmp_path, set_aside=[gone]))


def test_a_ruling_for_a_repeat_fails_loudly_when_the_page_no_longer_repeats_the_row(tmp_path):
    # Wabash took the other copy (season 2007-2008) off its page. The ruling still finds the row it names, but
    # setting it aside would now remove the match itself and leave DePauw's row as a "DePauw record only" game.
    d = [g("2008-04-16", 9, 0, season="2007-08")]
    w = [g("2008-04-16", 9, 0, season="2008", loc="neutral")]
    ruling = rules(tmp_path, set_aside=[aside("wabash", "2008-04-16", "9-0", season="2008")])
    with pytest.raises(merge.MergeError, match=r"set_aside ruling for a repeat no longer finds the other copy of "
                                               r"Wabash's row 2008-04-16 9-0 \(season 2008\)"):
        merge.merge_sport(d, w, rules=ruling)
    assert merge.merge_sport(d, w).games[0]["source"] == "both"                 # without the ruling they are one match
    # a row set aside without a season (not a repeat) needs no other copy
    alone = merge.merge_sport([], [g("1944-01-06", 69, 49)], rules=rules(tmp_path, set_aside=[
        aside("wabash", "1944-01-06", "69-49")]))
    assert alone.games == [] and alone.stats["rows_set_aside"] == 1
    # and two rulings cannot set aside both copies between them
    both = rules(tmp_path, set_aside=[aside("wabash", "2008-04-16", "9-0", season="2008"),
                                      aside("wabash", "2008-04-16", "9-0", season="2007-2008")])
    with pytest.raises(merge.MergeError, match="no longer finds the other copy"):
        merge.merge_sport(d, [g("2008-04-16", 9, 0, season="2007-2008"), *w], rules=both)


def test_two_rulings_cannot_set_the_same_row_aside_and_a_row_set_aside_takes_no_other_ruling(tmp_path):
    w = [g("1944-01-06", 69, 49)]
    twice = [aside("wabash", "1944-01-06", "69-49"), aside("wabash", "1944-01-06", "69-49")]
    with pytest.raises(merge.MergeError, match="two set_aside rulings for one row"):
        merge.merge_sport([], w, rules=rules(tmp_path, set_aside=twice))
    both = rules(tmp_path, set_aside=twice[:1], note=[{**aside("wabash", "1944-01-06", "69-49"), "note": "x"}])
    with pytest.raises(merge.MergeError, match="names a row a set_aside ruling takes out"):
        merge.merge_sport([], w, rules=both)


def test_the_merge_still_accounts_for_every_remaining_row(tmp_path):
    # check() runs on the rows that are left, so a set-aside row cannot hide a bookkeeping slip
    d = [g("1990-04-01", 5, 3)]
    w = [g("1990-04-01", 5, 3), g("1990-04-09", 1, 2), g("1990-04-20", 7, 0)]
    r = merge.merge_sport(d, w, rules=rules(tmp_path, set_aside=[aside("wabash", "1990-04-20", "7-0")]))
    assert (r.stats["both"], r.stats["wabash_only"], len(r.games)) == (1, 1, 2) and merge.tally(r.games) == (1, 1, 0)


# ---- found -------------------------------------------------------------------------------------- #

def found(school, row_date, score, **what):
    return {**aside(school, row_date, score), **what}


def test_a_found_date_makes_an_undated_game_a_dated_one_and_says_what_the_page_listed(tmp_path):
    w = [g("1900-01-01", 10, 11, loc="neutral"), g("1900-01-01", 4, 2, loc="neutral"), g("1900-05-20", 3, 1)]
    plain = merge.merge_sport([], w, season_months=(3, 4, 5))
    assert [x.get("date_known") for x in plain.games] == [False, False, None]
    r = merge.merge_sport([], w, season_months=(3, 4, 5), rules=rules(tmp_path, found=[
        found("wabash", "1900-01-01", "10-11", date="1900-05-07", location="home", site="Greencastle, IN")]))
    assert [(x["date"], x.get("date_known")) for x in r.games] == [
        ("1900-01-01", False), ("1900-05-07", None), ("1900-05-20", None)]      # in date order, the undated one first
    game = r.games[1]
    assert (game["location"], game["site"], game["source"], game["depauw"], game["wabash"]) == \
        ("home", "Greencastle, IN", "wabash", 10, 11)
    assert game["ruled"] == [{"kind": "found", "school": "Wabash", "what": ["date", "place"],
                              "listed": {"date": None, "location": "neutral", "site": None}, "sources": SRC}]
    assert r.stats["dates_not_recorded"] == 1
    assert not any(k.startswith("_") for x in r.games for k in x)              # no bookkeeping key leaves the merge


def test_a_found_date_counts_even_outside_the_months_the_sport_is_played(tmp_path):
    w = [g("1920-01-01", 4, 7, loc="neutral")]
    r = merge.merge_sport([], w, season_months=(3, 4, 5),
                          rules=rules(tmp_path, found=[found("wabash", "1920-01-01", "4-7", date="1920-06-04")]))
    assert (r.games[0]["date"], r.games[0].get("date_known")) == ("1920-06-04", None)
    assert r.games[0]["ruled"][0]["what"] == ["date"] and r.games[0]["ruled"][0]["listed"] == {"date": None}


def test_a_found_place_on_depauws_row_replaces_what_its_page_lists_and_keeps_it_visible(tmp_path):
    d = [g("1915-11-20", 0, 34, loc="away")]
    w = [g("1915-11-20", 0, 34, loc="neutral", site="Indianapolis, IN")]
    r = merge.merge_sport(d, w, rules=rules(tmp_path, found=[found("depauw", "1915-11-20", "0-34", location="neutral",
                                                                   site="Indianapolis, IN")]))
    [game] = r.games
    assert (game["source"], game["location"], game["site"]) == ("both", "neutral", "Indianapolis, IN")
    assert game["ruled"] == [{"kind": "found", "school": "DePauw", "what": ["place"],
                              "listed": {"location": "away", "site": None}, "sources": SRC}]
    assert "date_differs" not in game and "disputed" not in game


def test_other_rulings_still_find_a_row_a_found_ruling_gave_a_date(tmp_path):
    # rulings name a row by what the page lists, whatever a found ruling did to it since
    d = [g("1900-05-09", 10, 11)]
    w = [g("1900-01-01", 10, 11, loc="neutral")]
    both = rules(tmp_path, found=[found("wabash", "1900-01-01", "10-11", location="home")],
                 same_game=[{"depauw": {"date": "1900-05-09", "score": "10-11"},
                             "wabash": {"date": "1900-01-01", "score": "10-11"}, "reason": "one game"}])
    r = merge.merge_sport(d, w, rules=both)
    assert len(r.games) == 1 and r.games[0]["source"] == "both"


def test_a_found_date_that_depauws_page_now_contradicts_fails_loudly(tmp_path):
    # DePauw's page lists the game on another day: a person has to look, the ruling must not pose as Wabash's date
    d = [g("1900-05-09", 10, 11)]
    w = [g("1900-01-01", 10, 11, loc="neutral")]
    both = rules(tmp_path, found=[found("wabash", "1900-01-01", "10-11", date="1900-05-07")],
                 same_game=[{"depauw": {"date": "1900-05-09", "score": "10-11"},
                             "wabash": {"date": "1900-01-01", "score": "10-11"}, "reason": "one game"}])
    with pytest.raises(merge.MergeError, match="found ruling gives Wabash's row 1900-01-01 the date 1900-05-07"):
        merge.merge_sport(d, w, rules=both)


def test_a_found_ruling_that_no_longer_finds_its_row_fails_loudly(tmp_path):
    with pytest.raises(merge.MergeError, match="found ruling no longer finds Wabash's row 1900-01-01 10-11"):
        merge.merge_sport([], [g("1900-05-07", 10, 11)],            # Wabash's page now has the date itself
                          rules=rules(tmp_path, found=[found("wabash", "1900-01-01", "10-11", date="1900-05-07")]))


def test_a_found_date_never_lets_one_game_be_counted_twice(tmp_path):
    # Wabash lists the game with no date; a ruling found May 7. DePauw's page later adds the same game on May 16,
    # too far from May 7 for any dated pass. Without the ruling the two rows pair by season and score; with it they
    # would be left as two games. The merge stops instead, naming the ruling and both rows.
    d = [g("1900-05-16", 10, 11)]
    w = [g("1900-01-01", 10, 11, loc="neutral"), g("1900-05-20", 3, 1)]
    plain = merge.merge_sport(d, w, season_months=(3, 4, 5))
    assert len(plain.games) == 2 and plain.stats["both"] == 1                    # one game, as it should be
    ruling = [found("wabash", "1900-01-01", "10-11", date="1900-05-07")]
    with pytest.raises(merge.MergeError) as err:
        merge.merge_sport(d, w, season_months=(3, 4, 5), rules=rules(tmp_path, found=ruling))
    assert ("found ruling gives Wabash's row 1900-01-01 10-11 the date 1900-05-07, and the other school's page has an "
            "unmatched row from the same season with the same score (DePauw 1900-05-16 / Wabash 1900-01-01): they "
            "may be one game counted twice") in str(err.value)
    # a person who checked that they are two games says so, and the merge goes through with both
    apart = rules(tmp_path, found=ruling, different_games=[{
        "depauw": {"date": "1900-05-16", "score": "10-11"}, "wabash": {"date": "1900-01-01", "score": "10-11"},
        "reason": "two games"}])
    assert len(merge.merge_sport(d, w, season_months=(3, 4, 5), rules=apart).games) == 3
    # a row from another season, or with another score, is not the same game
    for other in (g("1901-05-16", 10, 11), g("1900-05-16", 10, 12)):
        r = merge.merge_sport([other], w, season_months=(3, 4, 5), rules=rules(tmp_path, found=ruling))
        assert len(r.games) == 3
    # and it holds the other way around: a found date on DePauw's row, Wabash's row left unmatched
    d2, w2 = [g("1900-09-01", 10, 11)], [g("1900-05-16", 10, 11)]
    with pytest.raises(merge.MergeError, match="found ruling gives DePauw's row 1900-09-01 10-11 the date 1900-05-07"):
        merge.merge_sport(d2, w2, season_months=(3, 4, 5),
                          rules=rules(tmp_path, found=[found("depauw", "1900-09-01", "10-11", date="1900-05-07")]))


def test_a_found_date_is_only_for_a_row_whose_page_lists_no_usable_date(tmp_path):
    # a found ruling never overrules a date a page lists: that would be picking a date without showing the page's
    for school, rows in (("wabash", ([], [g("1900-05-09", 10, 11)])), ("depauw", ([g("1900-05-09", 10, 11)], []))):
        with pytest.raises(merge.MergeError, match=r"the date 1900-05-07, but the page lists a usable date for that "
                                                   r"row \(1900-05-09\): a found date is only for a row with none"):
            merge.merge_sport(*rows, season_months=(3, 4, 5),
                              rules=rules(tmp_path, found=[found(school, "1900-05-09", "10-11", date="1900-05-07")]))
    # DePauw's row is undated only when its date falls outside the sport's months; then a date may be found
    r = merge.merge_sport([g("1900-09-01", 10, 11)], [], season_months=(3, 4, 5),
                          rules=rules(tmp_path, found=[found("depauw", "1900-09-01", "10-11", date="1900-05-07")]))
    assert (r.games[0]["date"], r.games[0]["ruled"][0]["listed"]) == ("1900-05-07", {"date": None})
    # the place of a dated row can still be found: only the date is refused
    r = merge.merge_sport([], [g("1900-05-09", 10, 11, loc="neutral")], season_months=(3, 4, 5), rules=rules(
        tmp_path, found=[found("wabash", "1900-05-09", "10-11", location="home", site="Greencastle, IN")]))
    assert (r.games[0]["date"], r.games[0]["location"]) == ("1900-05-09", "home")


def test_a_found_date_must_fall_in_its_rows_season(tmp_path):
    spring = [g("1900-01-01", 10, 11, season="1900", loc="neutral")]
    with pytest.raises(merge.MergeError, match=r"the date 1901-05-07, which is outside the row's season \(1900\)"):
        merge.merge_sport([], spring, rules=rules(tmp_path, found=[
            found("wabash", "1900-01-01", "10-11", date="1901-05-07")]))
    winter = [g("1906-01-01", 20, 30, season="1905-06", loc="neutral")]
    for date in ("1905-12-09", "1906-02-24"):                                   # either year of a two-year season
        r = merge.merge_sport([], winter, rules=rules(tmp_path, found=[found("wabash", "1906-01-01", "20-30", date=date)]))
        assert r.games[0]["date"] == date
    with pytest.raises(merge.MergeError, match=r"outside the row's season \(1905-06\)"):
        merge.merge_sport([], winter, rules=rules(tmp_path, found=[
            found("wabash", "1906-01-01", "20-30", date="1907-01-15")]))


def test_a_found_site_must_fit_the_home_or_away_its_page_lists(tmp_path):
    # a ruling that gives only the site is checked against the row's own location when it is applied
    w = [g("1900-05-09", 10, 11, loc="away")]
    with pytest.raises(merge.MergeError, match=r'found ruling for Wabash\'s row 1900-05-09 10-11: a "away" game is in '
                                               r'Crawfordsville, Ind\., not at "Greencastle, IN"'):
        merge.merge_sport([], w, rules=rules(tmp_path, found=[found("wabash", "1900-05-09", "10-11", site="Greencastle, IN")]))
    r = merge.merge_sport([], w, rules=rules(tmp_path, found=[
        found("wabash", "1900-05-09", "10-11", site="Ingalls Field, Crawfordsville, IN")]))
    assert (r.games[0]["location"], r.games[0]["site"]) == ("away", "Ingalls Field, Crawfordsville, IN")
    assert merge.place_problem("home", None) is None and merge.place_problem("away", "Crawfordsville, Ind.") is None
    assert merge.place_problem("neutral", "Indianapolis, IN") is None and merge.place_problem("unknown", None) is None


# ---- note --------------------------------------------------------------------------------------- #

def test_a_note_ruling_puts_its_note_on_the_game_and_changes_nothing_else(tmp_path):
    unscored = {**g("1934-01-05", 1, 0), "depauw": None, "wabash": None, "score_known": False}
    d = [g("1966-10-29", 1, 2, loc="away")]
    note = [{"school": "depauw", "row": {"date": "1966-10-29", "score": "1-2"}, "note": "A club side.",
             "reason": "the yearbook", "sources": SRC},
            {"school": "wabash", "row": {"date": "1934-01-05", "score": None}, "note": "No other source.",
             "reason": "kept"}]
    plain = merge.merge_sport(d, [unscored])
    r = merge.merge_sport(d, [unscored], rules=rules(tmp_path, note=note))
    assert [x.pop("ruled") for x in r.games] == [[{"kind": "note", "note": "No other source.", "sources": []}],
                                                 [{"kind": "note", "note": "A club side.", "sources": SRC}]]
    assert r.games == plain.games and r.stats == plain.stats


# ---- score -------------------------------------------------------------------------------------- #

def score_rule(tmp_path, d=("2011-11-09", "205-80"), w=("2011-11-09", "205-89")):
    return rules(tmp_path, score=[{"depauw": {"date": d[0], "score": d[1]}, "wabash": {"date": w[0], "score": w[1]},
                                   "use": "wabash", "reason": "DePauw's own recap", "sources": SRC}])


def test_a_score_ruling_counts_wabashs_version_and_keeps_depauws_page_visible(tmp_path):
    d, w = [g("2011-11-09", 205, 80)], [g("2011-11-09", 205, 89)]
    plain = merge.merge_sport(d, w)
    assert plain.games[0]["disputed"] and plain.stats["disputed"] == 1 and plain.games[0]["wabash"] == 80
    r = merge.merge_sport(d, w, rules=score_rule(tmp_path))
    [game] = r.games
    assert (game["depauw"], game["wabash"], game["result"], game["source"]) == (205, 89, "W", "both")
    assert "disputed" not in game and "wabash_version" not in game and r.stats["disputed"] == 0 and r.disputed == []
    assert game["ruled"] == [{"kind": "score", "school": "DePauw", "listed": {"depauw": 205, "wabash": 80},
                              "sources": SRC}]


def test_a_score_ruling_can_never_change_who_won(tmp_path):
    with pytest.raises(merge.MergeError, match="needs two rows with different scores and the same winner"):
        merge.merge_sport([g("2011-11-09", 90, 89)], [g("2011-11-09", 88, 89)],
                          rules=score_rule(tmp_path, ("2011-11-09", "90-89"), ("2011-11-09", "88-89")))


def test_a_score_ruling_goes_stale_when_depauw_fixes_its_page(tmp_path):
    with pytest.raises(merge.MergeError, match="score ruling no longer finds DePauw's row"):
        merge.merge_sport([g("2011-11-09", 205, 89)], [g("2011-11-09", 205, 89)], rules=score_rule(tmp_path))


# ---- the review hold knows the rows these rulings name -------------------------------------------- #

@pytest.mark.parametrize("kind,school,row", [
    ("set_aside", "wabash", {"date": "1944-01-06", "depauw": 69, "wabash": 49}),
    ("found", "wabash", {"date": "1900-01-01", "depauw": 10, "wabash": 11}),
    ("note", "wabash", {"date": "1934-01-05", "depauw": None, "wabash": None}),
    ("score", "depauw", {"date": "2011-11-09", "depauw": 205, "wabash": 80}),
    ("score", "wabash", {"date": "2011-11-09", "depauw": 205, "wabash": 89}),
])
def test_a_change_to_a_row_one_of_these_rulings_names_is_flagged_for_review(tmp_path, kind, school, row):
    r = rules(tmp_path,
              set_aside=[aside("wabash", "1944-01-06", "69-49")],
              found=[found("wabash", "1900-01-01", "10-11", date="1900-05-07")],
              note=[{**aside("wabash", "1934-01-05", None), "note": "x"}],
              score=[{"depauw": {"date": "2011-11-09", "score": "205-80"},
                      "wabash": {"date": "2011-11-09", "score": "205-89"}, "use": "wabash", "reason": "r",
                      "sources": SRC}])
    assert review.touches_ruling({"old": row, "new": None}, school, r) is True
    other = "depauw" if school == "wabash" else "wabash"
    if kind != "score":
        assert review.touches_ruling({"old": row, "new": None}, other, r) is False      # a ruling is for one school
    assert review.touches_ruling({"old": {"date": "1950-01-01", "depauw": 1, "wabash": 0}, "new": None}, school, r) is False


# ---- the committed rulings, applied to the pages captured in October 2026 ------------------------- #

def test_every_sourced_ruling_in_the_committed_file_has_a_labelled_https_source():
    parsed = merge.load_overrides(OVERRIDES)
    for key, by_kind in parsed.items():
        for kind in ("set_aside", "found", "score"):
            for r in by_kind[kind]:
                assert r["sources"], (key, kind, r)
        for kind in (*merge.ROW_KINDS, "score"):
            for r in by_kind[kind]:
                assert all(s["url"].startswith("https://") and s["label"] for s in r["sources"])
                assert "'" not in r["reason"] and '"' not in r["reason"], r["reason"]     # shown on the site: curly marks


def test_the_two_1944_rows_against_the_navy_team_are_not_basketball_games():
    d, w, r = committed("mens-basketball")
    assert [x["date"] for x in r.games if x["date"].startswith("1944-")] == ["1944-01-11", "1944-02-17"]
    assert [(x["school"], x["date"], x["depauw"], x["wabash"]) for x in r.set_aside] == [
        ("Wabash", "1944-01-06", 69, 49), ("Wabash", "1944-02-05", 28, 19)]
    assert r.stats["rows_set_aside"] == 2


def test_the_two_tennis_repeats_are_counted_once():
    d, w, r = committed("mens-tennis")
    for date in ("2008-04-16", "2009-04-15"):
        [game] = [x for x in r.games if x["date"] == date]
        assert (game["source"], game["depauw"], game["wabash"]) == ("both", 9, 0)
    assert [(x["date"], x["season"]) for x in r.set_aside] == [("2008-04-16", "2008"), ("2009-04-15", "2009")]
    assert (len(r.games), sum(x["depauw"] for x in r.games)) == (121, 821)     # two matches and 18 points fewer


def test_the_1915_football_game_is_a_neutral_site_game_by_ruling():
    d, w, r = committed("football")
    game = next(x for x in r.games if x["date"] == "1915-11-20")
    assert (game["location"], game["site"], game["source"]) == ("neutral", "Indianapolis, IN", "both")
    assert game["ruled"][0]["listed"] == {"location": "away", "site": None}
    # one more than before the ruling (the page fixtures are from before the 2026 game at Muncie)
    assert len([x for x in r.games if x["location"] == "neutral"]) == 7
    assert min(x["date"] for x in r.games if x["location"] == "neutral") == "1915-11-20"


def test_the_found_dates_move_22_games_out_of_the_undated_group_and_change_no_result():
    found_dates = {"baseball": 19, "mens-tennis": 3}
    for key, n in found_dates.items():
        d, w, r = committed(key)
        plain = merge.merge_sport(d, w, season_months=MONTHS[key],
                                  rules={**merge.load_overrides(OVERRIDES)[key], "found": []})
        assert plain.stats["dates_not_recorded"] - r.stats["dates_not_recorded"] == n
        assert merge.tally(plain.games) == merge.tally(r.games) and len(plain.games) == len(r.games)
        dated = [x for x in r.games if any(e["kind"] == "found" and "date" in e["what"] for e in x.get("ruled", []))]
        assert len(dated) == n and all(x.get("date_known") is None and x["source"] == "wabash" for x in dated)
        assert all(x["date"][:4] == x["season"][-4:] or x["date"][:4] == str(merge.season_key(x)) for x in dated)
    d, w, baseball = committed("baseball")
    assert baseball.games[0]["date"] == "1888-05-19"                            # the first game now has its day
    assert merge.tally(baseball.games) == merge.tally(w) == (155, 156, 3)       # still Wabash's own page, exactly


# Every date and place a found ruling gives, pinned: (the date the game now has, DePauw's score, Wabash's score,
# the location from DePauw's side, the site). A date a day off, two dates swapped between rows of one season or
# a flipped home and away fails here. Change a line only together with its ruling, from a source you have opened.
HOME, AWAY = "Greencastle, IN", "Crawfordsville, IN"
FOUND = {
    "baseball": [
        ("1888-05-19", 7, 6, "away", AWAY), ("1894-05-26", 4, 7, "home", HOME), ("1899-05-01", 17, 7, "home", HOME),
        ("1900-05-07", 10, 11, "home", HOME), ("1907-04-26", 5, 7, "away", AWAY), ("1913-04-22", 6, 3, "home", HOME),
        ("1913-05-19", 7, 1, "home", HOME), ("1915-05-07", 6, 3, "home", HOME), ("1915-05-14", 5, 6, "away", AWAY),
        ("1918-05-08", 9, 6, "away", AWAY), ("1919-04-11", 6, 1, "away", AWAY), ("1919-04-18", 4, 5, "home", HOME),
        ("1920-04-27", 7, 7, "neutral", None),            # the date only: no place was found, so Wabash's page's stays
        ("1920-05-04", 5, 8, "away", AWAY), ("1920-05-11", 7, 2, "home", HOME), ("1920-05-18", 5, 4, "away", AWAY),
        ("1920-06-04", 4, 7, "home", HOME), ("1921-05-03", 0, 9, "away", AWAY), ("1921-05-10", 8, 11, "home", HOME)],
    "mens-tennis": [("1915-05-01", 0, 3, "home", HOME), ("1929-04-26", 7, 0, "home", HOME),
                    ("1937-05-05", 9, 0, "away", AWAY)],
    "football": [("1915-11-20", 0, 34, "neutral", "Indianapolis, IN")],
}


@pytest.mark.parametrize("key", FOUND)
def test_every_found_date_and_place_is_pinned(key):
    d, w, r = committed(key)
    got = [(x["date"], x["depauw"], x["wabash"], x["location"], x["site"]) for x in r.games
           if any(e["kind"] == "found" for e in x.get("ruled", []))]
    assert got == FOUND[key]
    assert len(merge.load_overrides(OVERRIDES)[key]["found"]) == len(FOUND[key])      # and no ruling is missing here
    assert all(x.get("date_known") is None for x in r.games if any(e["kind"] == "found" for e in x.get("ruled", [])))


def test_the_pinned_table_holds_every_found_ruling():
    parsed = merge.load_overrides(OVERRIDES, sports=merge.sport_keys())
    assert {key: len(by_kind["found"]) for key, by_kind in parsed.items() if by_kind["found"]} == \
        {key: len(rows) for key, rows in FOUND.items()}
    assert sum(len(rows) for rows in FOUND.values()) == 23
    assert sum(1 for rows in FOUND.values() for row in rows if row[0] != "1915-11-20") == 22     # the found dates


def test_both_committed_tennis_repeats_still_have_their_other_copy():
    # the rule for a repeat (the other copy must still be on the page) holds for both rulings as committed
    d, w, r = committed("mens-tennis")
    for row in r.set_aside:
        copies = [x for x in w if (x["date"], x["depauw"], x["wabash"]) == (row["date"], row["depauw"], row["wabash"])]
        assert len(copies) == 2 and {x["season"] for x in copies} > {row["season"]}
    assert r.stats["rows_set_aside"] == 2 and r.stats["depauw_only"] == 0


def test_the_1966_soccer_game_and_the_1934_meets_carry_their_notes():
    d, w, soccer = committed("mens-soccer")
    game = next(x for x in soccer.games if x["date"] == "1966-10-29")
    note = game["ruled"][0]["note"]
    assert game["source"] == "depauw" and "not yet a varsity team" in note and "club" not in note
    assert "gives the score as 3-1" in note and (game["depauw"], game["wabash"]) == (1, 2)   # DePauw's score counts
    assert len(soccer.games) == 65                                              # kept, as DePauw's record lists it


# ---- on the pages ------------------------------------------------------------------------------- #

@pytest.fixture
def built(both_dir, tmp_path):
    """The site built from the October 2026 pages with the committed rulings: (site folder, series data)."""
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


def test_the_totals_with_the_committed_rulings(built):
    _, data = built
    assert data["totals"] == {"depauw_wins": 536, "wabash_wins": 357, "ties": 20, "games_played": 913}
    src = {k: sum(s["sources"].get(k, 0) for s in data["sports"].values())
           for k in ("disputed", "dates_not_recorded", "rows_set_aside", "session_rows")}
    assert src == {"disputed": 15, "dates_not_recorded": 130, "rows_set_aside": 4, "session_rows": 2}


def test_each_ruled_game_says_so_in_its_row_with_what_the_page_lists_and_the_source(built):
    out, _ = built
    page = (out / "index.html").read_text(encoding="utf-8")
    assert ("Away · Crawfordsville, Ind. W DePauw 7, Wabash 6 Wabash record only Date and place from a ruling; "
            "Wabash’s page lists neither. Source: The Indianapolis Journal, May 20, 1888, p. 9 (Library of Congress)."
            ) in row_text(page, "May 19, 1888")
    assert "Date from a ruling; Wabash’s page lists no date. Source: Indiana Daily Times, Apr 28, 1920" in \
        row_text(page, "Apr 27, 1920")
    football = row_text(page, "Nov 20, 1915")
    assert "Neutral · Indianapolis, Ind." in football
    assert "Place from a ruling; DePauw’s page lists an away game. Sources: DePauw University, Mirage yearbook, " \
        "1916 (Internet Archive); Grand Forks Daily Herald, Nov 20, 1915, p. 6 (Library of Congress)." in football
    swim = row_text(page, "Nov 9, 2011")
    assert "DePauw 205, Wabash 89" in swim and "Score from a ruling; DePauw’s page lists 205-80 (DePauw-Wabash)." in swim
    assert "Wabash’s record (DePauw-Wabash)" not in swim                         # no longer shown as a dispute
    assert ("Wabash’s side was not yet a varsity team; soccer became a varsity sport at Wabash in 1967, so Wabash’s "
            "record does not list this game. Wabash’s yearbook gives the score as 3-1.") in row_text(page, "Oct 29, 1966")
    for date in ("Jan 5, 1934", "Feb 12, 1934"):
        assert ("Score not recorded; the result counts in the record but not in the point totals. No source other than "
                "Wabash’s page has been found for this meet.") in row_text(page, date)
    assert "Jan 6, 1944" not in page and "Feb 5, 1944" not in page              # set aside: in no list
    assert page.count(">Apr 16, 2008</time>") == page.count(">Apr 15, 2009</time>") == 1


def test_a_sports_card_says_how_many_rows_a_ruling_set_aside(built):
    out, _ = built
    page = text_of((out / "index.html").read_text(encoding="utf-8"))
    assert ("A ruling sets 2 rows aside (not a game between the two teams, or a repeat), so they are not counted; "
            "“How the numbers work” lists them.") in page
    assert "A ruling sets 2 rows aside (not a match between the two teams, or a repeat)" in page


def test_the_how_it_works_page_lists_every_row_set_aside_with_its_reason_and_sources(built):
    out, _ = built
    page = (out / "how-it-works" / "athletics" / "index.html").read_text(encoding="utf-8")
    text = text_of(page)
    parsed = merge.load_overrides(OVERRIDES)
    counts = howitworks.ruling_counts(parsed)
    assert counts == {"same_game": 17, "different_games": 0, "session_of": 1, "set_aside": 4, "score": 1,
                      "found": 23, "note": 3}
    assert ("Rulings made so far: 49 (17 saying two entries are the same game, 1 saying several entries are days of "
            "one meet, 4 setting a row aside, 1 choosing which score counts, 23 giving a date or a place found in "
            "another source, 3 adding a note to a game)") in text
    assert "saying two entries are different games" not in text                 # a kind with no ruling is left out
    assert "Right now there are 4 such rows:" in text
    for wanted in ("Wabash’s row for Jan 6, 1944 (DePauw 69, Wabash 49).", "Wabash’s row for Feb 5, 1944 (DePauw 28, Wabash 19).",
                   "Wabash’s row for Apr 16, 2008 (DePauw 9, Wabash 0).", "Wabash’s row for Apr 15, 2009 (DePauw 9, Wabash 0)."):
        assert wanted in text
    for by_kind in parsed.values():
        for kind in ("set_aside", "score", "found", "note"):
            for r in by_kind[kind]:
                assert r["reason"] in text
                for s in r["sources"]:
                    assert f'<a href="{html.escape(s["url"], quote=True)}">{html.escape(s["label"], quote=True)}</a>' in page
    assert "Wabash’s row for 1888 (DePauw 7, Wabash 6): May 19, 1888, at Wabash (Crawfordsville, Ind.)." in text
    assert "DePauw’s row for Nov 20, 1915 (DePauw 0, Wabash 34): at a neutral site (Indianapolis, Ind.)." in text
    assert "Wabash’s row for Jan 5, 1934 (no score listed): “No source other than Wabash’s page" in text
    aside_at, rest_at = page.index('id="rows-set-aside"'), page.index('<details class="rulings">')
    assert aside_at < rest_at < page.index("How often it updates")              # the rows set aside are in the open
    assert "<script" not in page


def test_the_csv_download_carries_the_ruling_notes(built):
    out, _ = built
    rows = list(csv.DictReader(io.StringIO((out / "games.csv").read_text(encoding="utf-8"))))
    assert len(rows) == 913
    by = {(r["sport"], r["date"]): r for r in rows}
    first = by[("Baseball", "1888-05-19")]
    assert (first["date_recorded"], first["location"], first["site"]) == ("yes", "away", "Crawfordsville, IN")
    assert first["ruling_notes"].startswith("Date and place from a ruling; Wabash’s page lists neither. Source: ")
    swim = by[("Men's Swimming and Diving", "2011-11-09")]
    assert (swim["depauw_score"], swim["wabash_score"], swim["disputed"], swim["wabash_record_score"]) == ("205", "89", "no", "")
    assert swim["ruling_notes"].startswith("Score from a ruling; DePauw’s page lists 205-80 (DePauw-Wabash).")
    assert sum(1 for r in rows if r["ruling_notes"]) == 27
    assert sum(1 for r in rows if r["date_recorded"] == "no") == 130


def test_a_found_date_changes_the_decade_groups_of_its_card(built):
    # on the October pages: baseball's first game gets its day, so the card gains an 1880s group, and the 19 dated
    # games leave "Date not recorded"
    out, data = built
    page = (out / "index.html").read_text(encoding="utf-8")
    card = text_of(page[page.index('id="baseball"'):page.index('id="mens-soccer"')])
    assert "1880s · 1 game · DePauw 1-0" in card
    d, w, r = committed("baseball")
    plain = merge.merge_sport(d, w, season_months=MONTHS["baseball"],
                              rules={**merge.load_overrides(OVERRIDES)["baseball"], "found": []})
    undated = {name: sum(1 for x in games if x.get("date_known") is False)
               for name, games in (("plain", plain.games), ("ruled", data["sports"]["baseball"]["games"]))}
    assert undated == {"plain": 64, "ruled": 45} and undated["plain"] - undated["ruled"] == len(FOUND["baseball"])
    assert f"Date not recorded · {undated['ruled']} games · " in card
    assert "1880s" not in render.games_table("baseball", plain.games, merged=True)      # no such group without the ruling
    first = [x for x in data["sports"]["baseball"]["games"] if x["date"] < "1890" and x.get("date_known") is not False]
    assert [(x["date"], x["depauw"], x["wabash"]) for x in first] == [("1888-05-19", 7, 6)]


def test_the_how_it_works_page_says_which_rulings_are_exceptions_to_depauws_version_counts(built):
    out, data = built
    text = text_of((out / "how-it-works" / "athletics" / "index.html").read_text(encoding="utf-8"))
    assert ("It can record a date that a newspaper or yearbook of the time gives when the school’s page has none, "
            "and a place when the page has none or the source shows another one.") in text
    assert ("Two kinds of ruling are exceptions to “DePauw’s version counts”: one chooses the score, when DePauw’s "
            "own records contradict its history page, and one replaces the place DePauw’s page lists, when a source "
            "of the time shows where the game was played (1 game so far: Football, Nov 20, 1915). Each of these "
            "rulings names its sources, and none changes who won a game.") in text
    assert howitworks.replaced_places(data["sports"]) == ["Football, Nov 20, 1915"]
    # the games are counted from the data: a place found for a row whose page lists none replaces nothing
    placed = [x for s in data["sports"].values() for x in s["games"]
              if any(e["kind"] == "found" and "place" in e["what"] for e in x.get("ruled", []))]
    assert len(placed) == 22 and [x["date"] for x in placed if x["source"] == "both"] == ["1915-11-20"]
    no_ruling = {k: {**s, "games": [{**x, "ruled": []} for x in s["games"]]} for k, s in data["sports"].items()}
    assert howitworks.replaced_places(no_ruling) == []


def test_the_readme_tells_the_1944_ruling_the_way_the_ruling_does():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    [example_1944] = [line for line in readme.splitlines() if line.startswith("- *Basketball 1944:*")]
    assert "stationed at Wabash" not in example_1944 and "stationed at DePauw" in example_1944
    assert "not DePauw's team" in example_1944
    ruling = merge.load_overrides(OVERRIDES)["mens-basketball"]["set_aside"][0]
    start = readme.index('"set_aside": [')
    example = readme[start:readme.index('"found": [', start)]
    assert '"row": {"date": "1944-01-06", "score": "69-49"}' in example
    assert f'"label": "{ruling["sources"][0]["label"]}", "url": "{ruling["sources"][0]["url"]}"' in example
    assert "stationed at DePauw" in example and "Wabash College's team" not in example
    assert ruling["reason"].startswith("Not a game against DePauw’s team.")


# ---- in the pipeline: a ruling the pages have overtaken keeps that sport's last good data ----------- #

def stale_copy(tmp_path, change):
    """The committed rulings with one of them changed by `change(sports)`, written next to the output file."""
    doc = json.loads(OVERRIDES.read_text(encoding="utf-8"))
    change(doc["sports"])
    (tmp_path / "overrides.json").write_text(json.dumps(doc), encoding="utf-8")


def scrape_pages(both_dir, tmp_path, *extra):
    return scrape.main(["--fixtures", str(both_dir), "--output", str(tmp_path / "series.json"),
                        "--upcoming-output", str(tmp_path / "upcoming.json"), *extra])


def sports_of(tmp_path):
    return json.loads((tmp_path / "series.json").read_text(encoding="utf-8"))["sports"]


def stale_set_aside(sports):
    sports["mens-basketball"]["set_aside"][0]["row"]["date"] = "1944-01-07"      # Wabash moved or removed the row


def stale_found(sports):
    sports["baseball"]["found"][0]["row"]["score"] = "8-6"                      # Wabash changed the score


def stale_score(sports):
    sports["mens-swimming-and-diving"]["score"][0]["depauw"]["score"] = "205-89"   # DePauw fixed its page


@pytest.mark.parametrize("change,key,message", [
    (stale_set_aside, "mens-basketball", "set_aside ruling no longer finds Wabash's row 1944-01-07 69-49"),
    (stale_found, "baseball", "found ruling no longer finds Wabash's row 1888-01-03 8-6"),
    (stale_score, "mens-swimming-and-diving", "score ruling no longer finds DePauw's row"),
])
def test_a_stale_sourced_ruling_keeps_its_sports_last_good_data_and_the_rest_still_updates(
        both_dir, tmp_path, capsys, change, key, message):
    assert scrape_pages(both_dir, tmp_path) == 0                   # the last good run: the pages with no rulings yet
    before = sports_of(tmp_path)
    assert before["football"]["games_played"] == 132 and before["mens-tennis"]["games_played"] == 123
    stale_copy(tmp_path, change)
    capsys.readouterr()
    assert scrape_pages(both_dir, tmp_path) == 1                   # red, so a person looks
    err = capsys.readouterr().err
    assert f"FAIL {before[key]['name']}" in err and message in err
    after = sports_of(tmp_path)
    assert after[key] == before[key]                                # nothing half-applied to the sport
    others = {"football": 131, "mens-basketball": 231, "mens-tennis": 121} | {key: before[key]["games_played"]}
    assert {k: after[k]["games_played"] for k in others} == others  # every other sport took its rulings
    assert sum(1 for k in after if after[k] != before[k]) >= 4


def test_a_found_date_overtaken_by_depauws_page_keeps_baseballs_last_good_data(both_dir, tmp_path, capsys):
    (tmp_path / "overrides.json").write_text(OVERRIDES.read_text(encoding="utf-8"), encoding="utf-8")
    assert scrape_pages(both_dir, tmp_path) == 0
    good = sports_of(tmp_path)
    assert good["baseball"]["games_played"] == 314
    # DePauw's page now lists the 1900 game too, nine days from the date the ruling found
    saved = json.loads((tmp_path / "depauw.json").read_text(encoding="utf-8"))
    saved["sports"]["baseball"]["games"].insert(0, g("1900-05-16", 10, 11))
    (tmp_path / "depauw.json").write_text(json.dumps(saved), encoding="utf-8")
    capsys.readouterr()
    assert scrape.main(["--remerge-only", "--output", str(tmp_path / "series.json")]) == 1
    err = capsys.readouterr().err
    assert "FAIL Baseball" in err and "they may be one game counted twice" in err
    assert "Wabash's row 1900-01-01 10-11 the date 1900-05-07" in err and "DePauw 1900-05-16" in err
    assert sports_of(tmp_path) == good                              # 314 games still: not 315, and nothing else moved
    # without the ruling's date the two rows are one game, which is why the merge must not guess
    doc = json.loads(OVERRIDES.read_text(encoding="utf-8"))
    doc["sports"]["baseball"]["found"] = [r for r in doc["sports"]["baseball"]["found"] if r["row"]["date"] != "1900-01-01"]
    (tmp_path / "overrides.json").write_text(json.dumps(doc), encoding="utf-8")
    assert scrape.main(["--remerge-only", "--output", str(tmp_path / "series.json")]) == 0
    baseball = sports_of(tmp_path)["baseball"]
    assert baseball["games_played"] == 314 and baseball["sources"]["both"] == good["baseball"]["sources"]["both"] + 1


def test_todays_rulings_still_merge_every_sport_on_the_captured_pages():
    for key in MONTHS:
        d, w, r = committed(key)                                    # raises if any new check refuses a committed ruling
        assert len(r.games) == r.stats["both"] + r.stats["depauw_only"] + r.stats["wabash_only"]


# ---- a sport key that is not a sport --------------------------------------------------------------- #

def with_unknown_sport(tmp_path):
    doc = json.loads(OVERRIDES.read_text(encoding="utf-8"))
    doc["sports"]["mens-golf"] = {"same_game": []}
    (tmp_path / "overrides.json").write_text(json.dumps(doc), encoding="utf-8")
    return tmp_path / "overrides.json"


def test_the_scraper_refuses_rulings_for_a_sport_that_is_not_in_sources_json(both_dir, tmp_path, capsys):
    with_unknown_sport(tmp_path)
    assert scrape_pages(both_dir, tmp_path) == 1
    err = capsys.readouterr().err
    assert 'FAIL overrides: overrides.json has rulings for "mens-golf", which is not in scraper/sources.json' in err
    assert not (tmp_path / "series.json").exists() and not (tmp_path / "depauw.json").exists()   # nothing written
    assert scrape.main(["--remerge-only", "--output", str(tmp_path / "series.json")]) == 1        # the offline path too


def test_the_build_refuses_rulings_for_a_sport_that_is_not_in_sources_json(tmp_path):
    frozen = ROOT / "tests" / "fixtures" / "series-frozen-2026-10-30.json"
    out = tmp_path / "site"
    with pytest.raises(merge.MergeError, match='has rulings for "mens-golf", which is not in scraper/sources.json'):
        render.build(frozen, out, now=NOW, upcoming_path=tmp_path / "none.json",
                     overrides_path=with_unknown_sport(tmp_path))
    assert not out.exists()                                         # it stops before any page is written
    assert render.main(["--data", str(frozen), "--out", str(out), "--upcoming", str(tmp_path / "none.json"),
                        "--overrides", str(tmp_path / "overrides.json"), "--now", "2026-12-01T10:00"]) == 1


def test_a_sport_key_is_checked_only_against_the_sports_given(tmp_path):
    path = with_unknown_sport(tmp_path)
    assert "mens-golf" in merge.load_overrides(path)                # a caller that names no sports checks none
    assert merge.sport_keys() == set(MONTHS)                        # sources.json, the same keys the tests use
    with pytest.raises(merge.MergeError, match='"mens-golf"'):
        merge.load_overrides(path, sports=merge.sport_keys())
