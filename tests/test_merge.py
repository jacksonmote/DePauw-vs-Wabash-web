"""Tests for merging DePauw's and Wabash's records (scraper/merge.py), on made-up games and on the
real pages captured from both sites on 2026-10-28."""
import json
import random
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import facts  # noqa: E402
import merge  # noqa: E402
import scrape  # noqa: E402
from test_scrape import FIX as scrape_fixtures, full_html, parse, parse_wabash  # noqa: E402


def g(date, d=10, w=5, season=None, loc="home", site=None, note=None, box=None):
    """A game in DePauw's point of view; the result follows from the score."""
    result = "W" if d > w else "L" if d < w else "T"
    return {"date": date, "season": season or date[:4], "location": loc, "site": site, "result": result,
            "depauw": d, "wabash": w, "note": note, "boxscore": box}


def merged(depauw, wabash, **kw):
    return merge.merge_sport(depauw, wabash, **kw)


# ---- the matching passes ------------------------------------------------------------------- #

def test_the_same_date_and_score_is_one_game_from_both_records():
    r = merged([g("2020-11-14", 20, 10)], [g("2020-11-14", 20, 10)])
    assert len(r.games) == 1
    only = r.games[0]
    assert only["source"] == "both" and "disputed" not in only and "date_differs" not in only
    assert r.stats == {"both": 1, "depauw_only": 0, "wabash_only": 0, "disputed": 0, "date_differs": 0,
                       "dates_not_recorded": 0}


def test_a_doubleheader_pairs_by_score_then_by_order():
    d = [g("2024-04-10", 6, 2), g("2024-04-10", 3, 9)]
    w = [g("2024-04-10", 3, 9), g("2024-04-10", 6, 2)]       # Wabash lists the games in the other order
    r = merged(d, w)
    assert r.stats["both"] == 2 and r.stats["depauw_only"] == r.stats["wabash_only"] == 0
    assert [(x["depauw"], x["wabash"]) for x in r.games] == [(6, 2), (3, 9)]    # DePauw's order is kept


def test_a_doubleheader_with_identical_scores_pairs_each_row_once():
    r = merged([g("1992-04-01", 2, 1), g("1992-04-01", 2, 1)], [g("1992-04-01", 2, 1), g("1992-04-01", 2, 1)])
    assert r.stats["both"] == 2 and len(r.games) == 2
    one_short = merged([g("1992-04-01", 2, 1), g("1992-04-01", 2, 1)], [g("1992-04-01", 2, 1)])
    assert (one_short.stats["both"], one_short.stats["depauw_only"], len(one_short.games)) == (1, 1, 2)


def test_a_placeholder_date_matches_by_season_and_score_and_keeps_depauws_date():
    d = [g("1997-04-09", 9, 0, season="1996-97")]
    w = [g("1997-01-01", 9, 0, season="1997")]
    r = merged(d, w)
    game = r.games[0]
    assert game["source"] == "both" and game["date"] == "1997-04-09" and game["season"] == "1996-97"
    assert "date_differs" not in game and "date_known" not in game and r.stats["dates_not_recorded"] == 0


def test_a_placeholder_with_no_match_stays_a_wabash_game_with_no_known_date():
    r = merged([g("1997-04-09", 9, 0, season="1996-97")],
               [g("1915-01-01", 0, 3, season="1915"), g("1997-01-01", 8, 1, season="1997")])
    wabash = [x for x in r.games if x["source"] == "wabash"]
    assert len(wabash) == 2 and all(x["date_known"] is False for x in wabash)
    assert r.stats["dates_not_recorded"] == 2 and r.stats["both"] == 0


def test_only_jan_1_to_3_count_as_placeholders_and_a_confirmed_date_is_real():
    assert [merge.is_placeholder(g(d)) for d in ("2020-01-01", "2020-01-03", "2020-01-04", "2020-02-01")] == \
        [True, True, False, False]
    r = merged([g("1978-01-02", 82, 73)], [g("1978-01-02", 82, 73)])     # DePauw agrees on a Jan 2 date
    assert r.games[0]["source"] == "both" and "date_known" not in r.games[0]
    real = merged([], [g("1889-01-05", 2, 5)])                           # not Jan 1-3: taken at its word
    assert "date_known" not in real.games[0]


@pytest.mark.parametrize("days", [1, 2])
def test_a_date_a_day_or_two_apart_with_the_same_score_is_one_game_flagged(days):
    r = merged([g("2005-03-16", 11, 1)], [g(f"2005-03-{16 + days}", 11, 1)])
    game = r.games[0]
    assert game["source"] == "both" and game["date"] == "2005-03-16"
    assert game["date_differs"] is True and game["wabash_date"] == f"2005-03-{16 + days}"
    assert r.stats["date_differs"] == 1 and len(r.date_differs) == 1



def test_three_days_apart_is_two_games_unless_the_dates_differ_by_one_typo():
    two = merged([g("1990-04-07", 8, 7)], [g("1990-04-05", 8, 7)])        # 2 days: the window
    assert len(two.games) == 1 and two.games[0]["date_differs"] is True
    far = merged([g("1990-04-12", 8, 7)], [g("1990-04-04", 8, 7)])        # 8 days, both day digits differ
    assert len(far.games) == 2 and far.stats["depauw_only"] == far.stats["wabash_only"] == 1
    typo = merged([g("1990-04-07", 8, 7)], [g("1990-04-04", 8, 7)])       # 07 vs 04: one digit wrong
    assert len(typo.games) == 1 and typo.games[0]["date_differs"] is True and typo.games[0]["wabash_date"] == "1990-04-04"
    assert [m["kind"] for m in merge.beyond_window(typo)] == ["typo"]



@pytest.mark.parametrize("a,b,kind", [
    ("2005-03-16", "2004-03-16", "year"), ("2005-03-16", "2005-04-16", "month"), ("2005-03-16", "2005-03-18", "day"), ("2005-03-16", "2005-03-29", None),
    ("2005-03-16", "2005-03-26", "day"), ("2005-03-16", "2005-03-17", "day"), ("2005-03-21", "2005-03-02", None),
    ("2005-03-16", "2005-04-17", None), ("2005-03-16", "2004-04-16", None), ("2005-03-16", "2003-03-16", None),
    ("2005-03-16", "2005-05-16", None)])
def test_only_a_single_field_typo_counts_as_one(a, b, kind):
    assert merge.typo_kind(a, b) == kind


def test_a_typo_match_needs_the_same_score_and_two_typos_are_not_a_typo():
    assert len(merged([g("1956-01-10", 85, 72)], [g("1956-01-20", 85, 72)]).games) == 1
    assert len(merged([g("1956-01-10", 85, 72)], [g("1956-01-20", 85, 73)]).games) == 2     # score differs too
    assert len(merged([g("1891-11-21", 1, 0)], [g("1891-11-02", 1, 0)]).games) == 2         # both day digits differ
    assert len(merged([g("1906-01-29", 7, 40)], [g("1906-02-24", 7, 40)]).games) == 2       # month and day differ
    other_season = merged([g("1906-12-10", 7, 40, season="1906-07")], [g("1905-12-10", 7, 40, season="1905-06")])
    assert len(other_season.games) == 1                       # a year typo is a typo; the seasons are not compared


def test_a_year_off_by_one_in_the_date_is_matched_as_a_typo():
    r = merged([g("1969-11-16", 3, 0)], [g("1968-11-16", 3, 0)])
    game = r.games[0]
    assert (game["source"], game["date"], game["wabash_date"], game["date_differs"]) == \
        ("both", "1969-11-16", "1968-11-16", True)


# ---- dates outside the sport's season are unknown, like placeholders --------------------------- #

BASKETBALL = (11, 12, 1, 2, 3)


def test_a_date_outside_the_season_is_unknown_and_matches_by_season_and_score():
    d = [g("1962-12-10", 66, 69, season="1962-63")]
    w = [g("1962-09-03", 66, 69, season="1962-63")]               # basketball in September
    r = merged(d, w, season_months=BASKETBALL)
    game = r.games[0]
    assert (game["source"], game["date"], game["wabash_date"], game["date_differs"]) == \
        ("both", "1962-12-10", "1962-09-03", True)
    assert "date_known" not in game
    assert len(merged(d, w).games) == 2                           # without season months it is a different game


def test_an_unknown_date_never_matches_across_seasons():
    r = merged([g("1962-12-10", 66, 69, season="1962-63")], [g("1963-09-03", 66, 69, season="1963-64")],
               season_months=BASKETBALL)
    assert len(r.games) == 2


def test_an_unmatched_game_dated_out_of_season_has_no_usable_date():
    r = merged([g("1962-07-04", 50, 40, season="1962-63")], [g("1950-09-03", 1, 2, season="1950-51")],
               season_months=BASKETBALL)
    assert [x.get("date_known") for x in r.games] == [False, False] and r.stats["dates_not_recorded"] == 2


def test_a_game_both_schools_date_out_of_season_the_same_way_still_matches_exactly():
    r = merged([g("1960-04-01", 5, 3, season="1959-60")], [g("1960-04-01", 5, 3, season="1959-60")],
               season_months=BASKETBALL)
    assert r.games[0]["source"] == "both" and "date_known" not in r.games[0]        # they agree, so it stands


def test_when_depauws_date_is_the_unusable_one_waba_shs_date_is_used():
    r = merged([g("1962-09-03", 66, 69, season="1962-63")], [g("1962-12-10", 66, 69, season="1962-63")],
               season_months=BASKETBALL)
    game = r.games[0]
    assert (game["date"], game["depauw_date"], game["date_differs"]) == ("1962-12-10", "1962-09-03", True)
    assert "wabash_date" not in game


def test_two_games_with_no_usable_date_match_each_other_but_stay_undated():
    r = merged([g("1962-09-03", 66, 69, season="1962-63")], [g("1962-09-05", 66, 69, season="1962-63")],
               season_months=BASKETBALL)
    assert len(r.games) == 1 and r.games[0]["source"] == "both" and r.games[0]["date_known"] is False


# ---- a near-date dispute: same winner, close score -------------------------------------------- #

def test_a_day_apart_with_the_same_winner_and_a_close_score_is_one_disputed_game():
    r = merged([g("1984-04-25", 12, 5)], [g("1984-04-24", 11, 5)])
    game = r.games[0]
    assert len(r.games) == 1 and game["source"] == "both" and game["disputed"] and game["date_differs"]
    assert (game["depauw"], game["wabash"], game["date"], game["wabash_date"]) == (12, 5, "1984-04-25", "1984-04-24")
    assert game["wabash_version"] == {"result": "W", "depauw": 11, "wabash": 5}
    assert (r.stats["disputed"], r.stats["date_differs"], r.stats["both"]) == (1, 1, 1)
    assert merge.tally(r.games) == (1, 0, 0)               # one game, DePauw's version


@pytest.mark.parametrize("d,w", [((12, 5), (12, 9)),      # the other score differs by 4 points
                                 ((12, 5), (5, 12)),      # a different winner
                                 ((5, 5), (6, 5))])       # a tie is not the same as a win
def test_a_near_date_pair_that_is_not_close_or_has_another_winner_stays_two_games(d, w):
    r = merged([g("1984-04-25", *d)], [g("1984-04-24", *w)])
    assert len(r.games) == 2 and r.stats["both"] == 0


def test_a_near_date_dispute_is_three_days_at_most_never_more_than_the_window():
    assert len(merged([g("1984-04-25", 12, 5)], [g("1984-04-22", 11, 5)]).games) == 2


def test_a_same_score_pair_is_preferred_to_a_close_score_pair():
    d = [g("1984-04-25", 12, 5), g("1984-04-25", 11, 4)]
    w = [g("1984-04-24", 11, 5), g("1984-04-24", 12, 5)]
    r = merged(d, w)
    assert r.stats["both"] == 2 and r.stats["disputed"] == 1
    assert sorted((x["depauw"], x["wabash"], bool(x.get("disputed"))) for x in r.games) == [(11, 4, True), (12, 5, False)]


def test_a_same_date_dispute_pairs_the_closest_scores_first():
    d = [g("1990-04-01", 10, 2), g("1990-04-01", 3, 1)]
    w = [g("1990-04-01", 3, 2), g("1990-04-01", 10, 3)]
    r = merged(d, w)
    pairs = {(x["depauw"], x["wabash"]): (x["wabash_version"]["depauw"], x["wabash_version"]["wabash"]) for x in r.games}
    assert pairs == {(10, 2): (10, 3), (3, 1): (3, 2)}


def test_the_same_date_with_a_different_score_is_a_dispute_and_depauws_score_counts():
    r = merged([g("1913-01-15", 20, 46)], [g("1913-01-15", 20, 47)])
    game = r.games[0]
    assert game["source"] == "both" and game["disputed"] is True
    assert (game["depauw"], game["wabash"], game["result"]) == (20, 46, "L")
    assert game["wabash_version"] == {"result": "L", "depauw": 20, "wabash": 47}
    assert r.stats["disputed"] == 1 and merge.tally(r.games) == (0, 1, 0)


def test_a_dispute_can_be_over_who_won():
    r = merged([g("1941-11-15", 19, 7)], [g("1941-11-15", 7, 19)])
    game = r.games[0]
    assert game["disputed"] and game["result"] == "W" and game["wabash_version"]["result"] == "L"
    assert merge.tally(r.games) == (1, 0, 0)          # DePauw's version is the one that counts


def test_a_game_only_one_school_lists_still_counts_and_says_whose_record_it_is():
    r = merged([g("2001-04-01", 3, 2)], [g("2002-04-01", 1, 4)])
    by_source = {x["source"]: x for x in r.games}
    assert set(by_source) == {"depauw", "wabash"} and by_source["wabash"]["result"] == "L"
    assert r.stats["depauw_only"] == r.stats["wabash_only"] == 1 and merge.tally(r.games) == (1, 1, 0)


def test_both_records_fill_each_others_gaps_but_depauws_values_win():
    d = g("2020-11-14", 20, 10, loc="unknown", site=None, note=None, box=None)
    w = g("2020-11-14", 20, 10, loc="home", site="Greencastle, IN", note="2 OT",
          box="https://sports.wabash.edu/boxscore.aspx?id=1")
    game = merged([d], [w]).games[0]
    assert (game["location"], game["site"], game["note"]) == ("home", "Greencastle, IN", "2 OT")
    assert game["boxscore"] == "https://sports.wabash.edu/boxscore.aspx?id=1"
    kept = merged([g("2020-11-14", 20, 10, site="Blackstock Stadium", note="OT")],
                  [g("2020-11-14", 20, 10, site="Greencastle, IN", note="2 OT")]).games[0]
    assert (kept["site"], kept["note"]) == ("Blackstock Stadium", "OT")


def test_seasons_are_written_one_way_on_the_page():
    assert merge.normalize_season("1966-1967", "1967-03-01") == "1966-67"
    assert merge.normalize_season("1999-00", "2000-02-01") == "1999-00"
    assert merge.normalize_season("1997", "1997-04-01") == "1997"
    assert merge.season_key(g("1997-04-09", season="1996-97")) == merge.season_key(g("1997-01-01", season="1997")) \
        == merge.season_key(g("1997-04-09", season="1996-1997")) == 1997
    assert merge.season_key(g("2000-02-01", season="1999-00")) == 2000
    assert merge.season_key(g("1999-11-01", season="odd label")) == 1999        # unreadable: the game's own year


def test_wabashs_view_flips_to_depauws():
    w = {"date": "2025-11-15", "season": "2025", "location": "home", "site": "Crawfordsville, Ind.",
         "result": "L", "depauw": 20, "wabash": 41, "note": None, "boxscore": None}     # Wabash's own page
    f = merge.flip(w)
    assert (f["result"], f["depauw"], f["wabash"], f["location"]) == ("W", 41, 20, "away")
    assert merge.flip(merge.flip(w)) == w
    assert merge.flip({**w, "location": "neutral"})["location"] == "neutral"
    assert merge.flip({**w, "location": "unknown", "result": "T"})["result"] == "T"


# ---- no row is ever used twice ---------------------------------------------------------------- #


def test_one_wabash_row_cannot_match_two_depauw_games():
    d = [g("2005-03-15", 11, 1), g("2005-03-17", 11, 1)]       # both within 2 days of Wabash's one row
    w = [g("2005-03-16", 11, 1)]
    r = merged(d, w)
    assert r.stats["both"] == 1 and r.stats["depauw_only"] == 1 and len(r.games) == 2



@pytest.mark.parametrize("seed", range(40))
def test_every_row_is_accounted_for_exactly_once_on_random_data(seed):
    rnd = random.Random(seed)

    def games():
        out = []
        for _ in range(rnd.randint(0, 25)):
            day = rnd.choice(["01-01", "01-02", "03-14", "03-15", "03-16", "03-26", "04-02", "04-02", "07-09", "11-09"])
            out.append(g(f"{rnd.choice([1990, 1991])}-{day}", rnd.randint(0, 3), rnd.randint(0, 3),
                         season=rnd.choice(["1990", "1991"])))
        return sorted(out, key=lambda x: x["date"])

    d, w = games(), games()
    forbidden = [{"different_games": [{"depauw": {"date": x["date"], "score": (x["depauw"], x["wabash"])},
                                       "wabash": {"date": y["date"], "score": (y["depauw"], y["wabash"])}, "reason": ""}
                                      for x in d[:3] for y in w[:3]],
                  "same_game": []}, None]
    for months in (None, (3, 4, 5)):
        for rules in forbidden:
            r = merged(d, w, season_months=months, rules=rules)       # raises MergeError if the books do not balance
            s = r.stats
            assert s["both"] + s["depauw_only"] == len(d) and s["both"] + s["wabash_only"] == len(w)
            assert len(r.games) == s["both"] + s["depauw_only"] + s["wabash_only"]
            assert merge.tally(r.games) == tuple(a + b for a, b in zip(merge.tally(d), merge.tally(r.wabash_only)))
            assert sum(1 for x in r.games if x["source"] == "both") == s["both"]
            assert all("_order" not in x for x in r.games)


def test_a_merge_that_does_not_add_up_is_an_error_not_wrong_data():
    r = merged([g("2020-11-14", 20, 10)], [g("2020-11-14", 20, 10)])
    r.stats["both"] = 2
    with pytest.raises(merge.MergeError):
        merge.check([g("2020-11-14", 20, 10)], [g("2020-11-14", 20, 10)], r)



def test_leftovers_that_look_like_the_same_game_are_listed_not_merged():
    r = merged([g("1984-04-25", 12, 5)], [g("1984-04-24", 16, 5)])
    assert len(r.games) == 2
    near = merge.near_misses(r)
    assert len(near) == 1 and near[0]["days"] == 1 and near[0]["same_score"] is False
    assert merge.near_misses(merged([g("2000-04-01", 2, 1)], [g("2001-04-01", 2, 1)])) == []


def test_the_run_summary_names_every_count():
    r = merged([g("2020-11-14", 20, 10), g("2021-11-13", 3, 0)], [g("2020-11-14", 20, 10), g("2022-11-12", 1, 2)])
    assert merge.summary_line("Football", r.stats) == \
        "Football: 1 matched, 1 DePauw only, 1 Wabash only, 0 disputed, 0 date-differs"




# ---- order: games with no usable date -------------------------------------------------------- #

def test_games_with_no_usable_date_come_first_in_their_season_in_page_order():
    d = [g("1996-11-20", 70, 60, season="1996-97"), g("1997-02-01", 71, 60, season="1996-97")]
    w = [g("1997-01-01", 1, 2, season="1996-97"), g("1997-01-01", 3, 4, season="1996-97"),
         g("1990-01-01", 5, 6, season="1989-90")]
    r = merged(d, w)
    assert [(x["depauw"], x["wabash"]) for x in r.games] == [(5, 6), (1, 2), (3, 4), (70, 60), (71, 60)]
    assert [x.get("date_known") for x in r.games] == [False, False, False, None, None]


def test_an_undated_game_stays_in_its_season_even_when_its_stored_date_says_otherwise():
    r = merged([g("1962-12-10", 50, 40, season="1962-63"), g("1963-01-12", 51, 40, season="1962-63")],
               [g("1962-07-04", 9, 8, season="1962-63"), g("1961-12-01", 7, 6, season="1961-62")],
               season_months=BASKETBALL)
    assert [(x["depauw"], x["wabash"]) for x in r.games] == [(7, 6), (9, 8), (50, 40), (51, 40)]


# ---- the real pages -------------------------------------------------------------------------- #

SOURCES = json.loads((Path(__file__).resolve().parent.parent / "scraper" / "sources.json").read_text(encoding="utf-8"))
MONTHS = {s["key"]: s["season_months"] for s in SOURCES["sports"]}


def both_schools(key):
    """(DePauw's games, Wabash's games flipped to DePauw's view), parsed and validated as the scraper does."""
    subset = scrape_fixtures / f"{key}-subset.html"       # the big pages are stored as subset + expected games
    dg, ds, _ = parse(full_html(key) if subset.exists() else (scrape_fixtures / f"{key}.html").read_text(encoding="utf-8"))
    wg, ws, _ = parse_wabash(full_html(f"wabash-{key}"))
    assert scrape.validate(dg, ds) == [] and scrape.validate(wg, ws) == []     # each page against its own summary
    return scrape.game_dicts(dg, "depauw"), scrape.game_dicts(wg, "wabash")


def real(key, rules=None):
    d, w = both_schools(key)
    return d, w, merged(d, w, season_months=MONTHS[key], rules=rules)


def ruling(depauw, wabash, **kw):
    """A parsed same_game ruling: each side is (date, "D-W")."""
    side = lambda x: {"date": x[0], "score": tuple(int(n) for n in x[1].split("-"))}
    return {"depauw": side(depauw), "wabash": side(wabash), "reason": ""}


def test_real_football_has_one_game_left_on_each_side_until_a_person_rules_on_1891():
    d, w, r = real("football")
    assert len(r.games) == 132 and merge.tally(r.games) == (60, 63, 9)
    assert r.stats == {"both": 130, "depauw_only": 1, "wabash_only": 1, "disputed": 2, "date_differs": 2,
                       "dates_not_recorded": 0, "place_differs": 4}       # 1903, 1915, 1935 and 1971
    # The two schools disagree on two scores (1901 and 1941); neither changes who won.
    assert sorted(x["date"] for x in r.disputed) == ["1901-11-11", "1941-11-15"]
    assert all(x["wabash_version"]["result"] == x["result"] for x in r.disputed)
    assert [(m["depauw"]["date"], m["wabash"]["date"]) for m in merge.near_misses(r)] == [("1891-11-21", "1891-11-02")]
    assert merge.beyond_window(r) == []


def test_real_football_with_the_1891_ruling_equals_both_schools_pages():
    ruled = {"same_game": [ruling(("1891-11-21", "1-0"), ("1891-11-02", "1-0"))], "different_games": []}
    d, w, r = real("football", ruled)
    assert len(r.games) == 131 and merge.tally(r.games) == (59, 63, 9)
    assert r.stats["both"] == 131 and r.stats["date_differs"] == 3
    game = next(x for x in r.games if x["date"] == "1891-11-21")
    assert (game["source"], game["wabash_date"], game["date_differs"]) == ("both", "1891-11-02", True)
    b = facts.bell(r.games)
    assert (b["depauw"], b["wabash"], b["ties"]) == (43, 44, 6)               # the Monon Bell record is unchanged
    assert (b["holder"], b["since"]) == ("DePauw", 2022)


def test_real_tennis_gains_88_old_matches_with_no_date_and_two_with_dates():
    d, w, r = real("mens-tennis")
    assert (len(d), len(w), len(r.games)) == (33, 123, 123)
    assert merge.tally(r.games) == (115, 7, 1)
    assert r.stats == {"both": 33, "depauw_only": 0, "wabash_only": 90, "disputed": 1, "date_differs": 0,
                       "dates_not_recorded": 88}
    assert [x["date"] for x in r.disputed] == ["2011-04-13"] and r.disputed[0]["depauw"] == 9
    one_nine_ninety_seven = [x for x in r.games if x["date"].startswith("1997")]
    assert [(x["source"], x["date"]) for x in one_nine_ninety_seven] == [("both", "1997-04-09")]
    undated = [x for x in r.games if x.get("date_known") is False]
    assert len(undated) == 88 and all(x["source"] == "wabash" for x in undated)
    assert r.games[0].get("date_known") is False and r.games[-1].get("date_known") is None


def test_real_baseball_merges_the_1984_pair_as_a_dispute_and_leaves_1986_for_a_ruling():
    d, w, r = real("baseball")
    assert (len(d), len(w), len(r.games)) == (141, 314, 316)
    assert merge.tally(r.games) == (157, 156, 3)
    assert r.stats == {"both": 139, "depauw_only": 2, "wabash_only": 175, "disputed": 2, "date_differs": 14,
                       "dates_not_recorded": 64, "place_differs": 8}
    pair = sorted((x["depauw"], x["wabash"], x["wabash_version"]["depauw"], x["wabash_version"]["wabash"], x["date"],
                   x["wabash_date"]) for x in r.disputed)
    assert pair == [(11, 1, 10, 2, "1984-04-25", "1984-04-24"), (12, 5, 11, 5, "1984-04-25", "1984-04-24")]
    # the 1986 doubleheader (Mar 19 and Mar 23 differ in both digits) is two games on each side
    assert sorted((x["date"], x["depauw"]) for x in r.depauw_only) == [("1986-03-19", 12), ("1986-03-19", 16)]
    assert r.games[0]["date"] == "1888-01-03" and r.games[0]["date_known"] is False
    assert all(x["source"] == "wabash" for x in r.games if x["date"] < "1978")


def test_real_baseball_with_the_1986_rulings_is_wabashs_by_one():
    ruled = {"same_game": [ruling(("1986-03-19", "12-4"), ("1986-03-23", "12-4")),
                           ruling(("1986-03-19", "16-4"), ("1986-03-23", "16-4"))], "different_games": []}
    d, w, r = real("baseball", ruled)
    assert (len(r.games), merge.tally(r.games)) == (314, (155, 156, 3))
    assert r.stats["both"] == 141 and r.stats["depauw_only"] == 0


def test_real_basketball_has_seven_disputed_scores_and_the_1954_tie_in_both_records():
    d, w, r = real("mens-basketball")
    assert (len(d), len(w), len(r.games)) == (231, 233, 238) and merge.tally(r.games) == (133, 104, 1)
    assert r.stats["disputed"] == 7 and r.stats["date_differs"] == 13
    assert (r.stats["both"], r.stats["depauw_only"], r.stats["wabash_only"]) == (226, 5, 7)
    tie = next(x for x in r.games if x["result"] == "T")
    assert (tie["date"], tie["source"]) == ("1954-12-04", "both") and "disputed" not in tie
    assert r.stats["dates_not_recorded"] == 0
    # The 1962 game was a September date at Wabash: out of season, so matched by season and score.
    game = next(x for x in r.games if x["wabash"] == 69 and x["depauw"] == 66 and x["season"] == "1962-63")
    assert (game["date"], game["wabash_date"], game["source"]) == ("1962-12-10", "1962-09-03", "both")
    assert sorted(x["date"] for x in r.depauw_only)[:4] == ["1906-01-29", "1906-02-13", "1907-02-05", "1907-02-27"]


def test_real_soccer_is_one_game_short_at_wabash_and_lacrosse_agrees_completely():
    d, w, r = real("mens-soccer")
    assert (len(d), len(w), len(r.games)) == (65, 64, 65) and merge.tally(r.games) == (41, 18, 6)
    assert r.stats == {"both": 64, "depauw_only": 1, "wabash_only": 0, "disputed": 0, "date_differs": 5,
                       "dates_not_recorded": 0, "place_differs": 3}
    assert sorted((m["depauw"]["date"], m["wabash"]["date"]) for m in merge.beyond_window(r)) == [
        ("1969-10-12", "1969-11-12"), ("1969-10-15", "1969-10-11"), ("1969-11-16", "1968-11-16")]
    d, w, r = real("mens-lacrosse")
    assert r.stats == {"both": 12, "depauw_only": 0, "wabash_only": 0, "disputed": 0, "date_differs": 0,
                       "dates_not_recorded": 0}
    assert merge.tally(r.games) == (12, 0, 0)


def test_real_games_load_through_the_merge_without_losing_a_boxscore():
    d, w, r = real("football")
    assert sum(1 for x in r.games if x["boxscore"]) >= sum(1 for x in d if x["boxscore"])
