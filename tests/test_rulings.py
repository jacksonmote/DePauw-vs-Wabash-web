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
                       "dates_not_recorded": 0, "rows_set_aside": 2}
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
    r = merge.merge_sport(d, w, rules=rules(tmp_path, found=[found("depauw", "1915-11-20", "0-34", location="neutral")]))
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


def test_the_1966_soccer_game_and_the_1934_meets_carry_their_notes():
    d, w, soccer = committed("mens-soccer")
    game = next(x for x in soccer.games if x["date"] == "1966-10-29")
    assert game["source"] == "depauw" and "student club team" in game["ruled"][0]["note"]
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
    assert "Wabash’s side was a student club team" in row_text(page, "Oct 29, 1966")
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
