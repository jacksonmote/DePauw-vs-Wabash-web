"""data/overrides.json: rulings a person made on whether two rows are the same game, applied before any
automatic matching (scraper/merge.py)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import merge  # noqa: E402
import scrape  # noqa: E402
from test_merge import g  # noqa: E402
from test_scrape import both_dir, fixture_dir  # noqa: E402,F401  (pytest fixtures)

ROOT = Path(__file__).resolve().parent.parent


def side(date, sc):
    return {"date": date, "score": sc}


def write(tmp_path, sports):
    p = tmp_path / "overrides.json"
    p.write_text(json.dumps({"schema_version": 1, "sports": sports}), encoding="utf-8")
    return p


def same(depauw, wabash, reason="checked the box score"):
    return {"depauw": side(*depauw), "wabash": side(*wabash), "reason": reason}


# ---- the file ---------------------------------------------------------------------------------- #

def test_the_committed_file_is_valid_and_every_ruling_has_a_reason():
    rules = merge.load_overrides(ROOT / "data" / "overrides.json")
    assert {k: {kind: len(found) for kind, found in v.items() if found} for k, v in rules.items()} == {
        "football": {"same_game": 1, "found": 1},
        "mens-basketball": {"same_game": 9, "set_aside": 2},
        "baseball": {"same_game": 3, "found": 19, "note": 1},
        "mens-soccer": {"same_game": 3, "note": 1},
        "mens-tennis": {"set_aside": 2, "found": 3},
        "mens-swimming-and-diving": {"same_game": 1, "session_of": 1, "score": 1, "note": 2}}
    assert all(set(v) == set(merge.RULING_KINDS) for v in rules.values())
    assert all(r["reason"] for v in rules.values() for kind in v.values() for r in kind)


def test_a_missing_file_means_no_rulings(tmp_path):
    assert merge.load_overrides(tmp_path / "nope.json") == {}


def test_rulings_are_parsed_with_scores_as_depauw_wabash_pairs(tmp_path):
    p = write(tmp_path, {"football": {
        "same_game": [same(("1891-11-21", "1-0"), ("1891-11-02", "1-0"))],
        "different_games": [same(("1906-01-29", "7-40"), ("1906-02-24", "7-40"), "two games")]}})
    rules = merge.load_overrides(p)["football"]
    assert rules["same_game"][0]["depauw"] == {"date": "1891-11-21", "score": (1, 0)}
    assert rules["same_game"][0]["reason"] == "checked the box score"
    assert rules["different_games"][0]["wabash"]["score"] == (7, 40)


@pytest.mark.parametrize("text", [
    "not json",
    "[]",
    '{"sports": []}',
    '{"sports": {"football": {"same_games": []}}}',                                        # misspelt key
    '{"sports": {"football": {"same_game": [{"depauw": {"date": "1891-11-21", "score": "1-0"}}]}}}',   # no wabash
    '{"sports": {"football": {"same_game": [{"depauw": {"date": "1891-11-21", "score": "1-0"},'
    ' "wabash": {"date": "1891-13-02", "score": "1-0"}}]}}}',                              # not a date
    '{"sports": {"football": {"same_game": [{"depauw": {"date": "1891-11-21", "score": "1-0"},'
    ' "wabash": {"date": "1891-11-02", "score": "one-nil"}}]}}}',                          # not a score
])
def test_a_malformed_file_is_an_error_not_silently_ignored(tmp_path, text):
    p = tmp_path / "overrides.json"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(merge.MergeError):
        merge.load_overrides(p)


# ---- same-game rulings -------------------------------------------------------------------------- #

def rules_for(*pairs):
    return {"same_game": [{**same(*p), "depauw": {"date": p[0][0], "score": tuple(map(int, p[0][1].split("-")))},
                           "wabash": {"date": p[1][0], "score": tuple(map(int, p[1][1].split("-")))}}
                          for p in pairs], "different_games": []}


def test_a_ruling_pairs_games_the_automatic_rules_would_leave_apart():
    d, w = [g("1891-11-21", 1, 0)], [g("1891-11-02", 1, 0)]
    assert len(merge.merge_sport(d, w).games) == 2
    r = merge.merge_sport(d, w, rules=rules_for((("1891-11-21", "1-0"), ("1891-11-02", "1-0"))))
    game = r.games[0]
    assert len(r.games) == 1 and (game["source"], game["date"], game["wabash_date"], game["date_differs"]) == \
        ("both", "1891-11-21", "1891-11-02", True)
    assert r.stats["both"] == 1
    assert [m["kind"] for m in r.pairs] == ["ruling"] and len(merge.beyond_window(r)) == 1


def test_a_ruling_can_pair_games_with_different_scores_and_far_apart_dates_as_a_dispute():
    d, w = [g("1928-02-14", 23, 22)], [g("1928-02-29", 32, 30)]
    r = merge.merge_sport(d, w, rules=rules_for((("1928-02-14", "23-22"), ("1928-02-29", "32-30"))))
    game = r.games[0]
    assert len(r.games) == 1 and game["disputed"] and game["date_differs"]
    assert (game["depauw"], game["wabash"]) == (23, 22) and game["wabash_version"]["depauw"] == 32
    assert merge.tally(r.games) == (1, 0, 0)


def test_a_ruling_is_applied_before_the_automatic_rules_can_use_the_rows():
    # Without the ruling, DePauw's Mar 16 game pairs with Wabash's Mar 17 (same score, a day apart).
    d = [g("2005-03-16", 11, 1), g("2005-03-30", 11, 1)]
    w = [g("2005-03-17", 11, 1), g("2005-03-20", 11, 1)]
    auto = merge.merge_sport(d, w)
    assert {(m["depauw"]["date"], m["wabash"]["date"]) for m in auto.pairs} >= {("2005-03-16", "2005-03-17")}
    ruled = merge.merge_sport(d, w, rules=rules_for((("2005-03-16", "11-1"), ("2005-03-20", "11-1"))))
    kinds = {(m["depauw"]["date"], m["wabash"]["date"]): m["kind"] for m in ruled.pairs}
    assert kinds[("2005-03-16", "2005-03-20")] == "ruling"
    assert ("2005-03-16", "2005-03-17") not in kinds


def test_a_ruling_uses_each_row_once_so_identical_doubleheader_rows_take_the_next_one():
    d = [g("1992-04-03", 2, 1), g("1992-04-03", 2, 1)]
    w = [g("1992-04-01", 2, 1), g("1992-04-01", 2, 1)]
    both = rules_for((("1992-04-03", "2-1"), ("1992-04-01", "2-1")), (("1992-04-03", "2-1"), ("1992-04-01", "2-1")))
    r = merge.merge_sport(d, w, rules=both)
    assert r.stats["both"] == 2 and [m["kind"] for m in r.pairs] == ["ruling", "ruling"]
    once = merge.merge_sport(d, w, rules=rules_for((("1992-04-03", "2-1"), ("1992-04-01", "2-1"))))
    assert sorted(m["kind"] for m in once.pairs) == ["near_date", "ruling"]       # the other pair is automatic


def test_a_ruling_that_no_longer_finds_both_rows_fails_loudly():
    d, w = [g("1891-11-21", 1, 0)], [g("1891-11-03", 1, 0)]                      # Wabash's row has another date
    with pytest.raises(merge.MergeError, match=r"no longer finds Wabash's row: DePauw 1891-11-21 1-0 / Wabash 1891-11-02 1-0"):
        merge.merge_sport(d, w, rules=rules_for((("1891-11-21", "1-0"), ("1891-11-02", "1-0"))))
    with pytest.raises(merge.MergeError, match="no longer finds DePauw's row"):
        merge.merge_sport([g("1891-11-20", 1, 0)], [g("1891-11-02", 1, 0)],
                          rules=rules_for((("1891-11-21", "1-0"), ("1891-11-02", "1-0"))))
    with pytest.raises(merge.MergeError, match="no longer finds DePauw and Wabash's row"):
        merge.merge_sport([], [], rules=rules_for((("1891-11-21", "1-0"), ("1891-11-02", "1-0"))))


def test_a_ruling_that_names_a_row_twice_but_the_page_has_it_once_fails():
    d, w = [g("1992-04-03", 2, 1)], [g("1992-04-01", 2, 1)]
    twice = rules_for((("1992-04-03", "2-1"), ("1992-04-01", "2-1")), (("1992-04-03", "2-1"), ("1992-04-01", "2-1")))
    with pytest.raises(merge.MergeError):
        merge.merge_sport(d, w, rules=twice)


def test_a_different_games_ruling_that_no_longer_finds_its_rows_fails_loudly():
    d, w = [g("1928-02-14", 23, 22)], [g("1928-02-28", 32, 30)]                  # Wabash's date moved
    with pytest.raises(merge.MergeError, match="different_games ruling no longer finds Wabash's row"):
        merge.merge_sport(d, w, rules=different((("1928-02-14", "23-22"), ("1928-02-29", "32-30"))))


# ---- different-game rulings --------------------------------------------------------------------- #

def different(*pairs):
    return {"same_game": [], "different_games": rules_for(*pairs)["same_game"]}


@pytest.mark.parametrize("d,w,months", [
    (g("1990-04-07", 8, 7), g("1990-04-04", 8, 7), None),          # one-digit typo
    (g("2005-03-16", 11, 1), g("2005-03-17", 11, 1), None),        # inside the window
    (g("1984-04-25", 12, 5), g("1984-04-24", 11, 5), None),        # a close-score dispute
    (g("1984-04-25", 12, 5), g("1984-04-25", 11, 5), None),        # same date, different score
    (g("1962-12-10", 66, 69, season="1962-63"), g("1962-09-03", 66, 69, season="1962-63"),
     (11, 12, 1, 2, 3)),                                            # no usable date
    (g("1960-04-01", 5, 3), g("1960-04-01", 5, 3), None),          # even an exact match
])
def test_a_different_games_ruling_keeps_two_rows_apart_in_every_pass(d, w, months):
    assert len(merge.merge_sport([d], [w], season_months=months).games) == 1       # they merge without the ruling
    key = ((d["date"], f"{d['depauw']}-{d['wabash']}"), (w["date"], f"{w['depauw']}-{w['wabash']}"))
    r = merge.merge_sport([d], [w], season_months=months, rules=different(key))
    assert len(r.games) == 2 and r.stats["both"] == 0
    assert sorted(x["source"] for x in r.games) == ["depauw", "wabash"]


def test_a_different_games_ruling_only_blocks_that_pair():
    d = [g("2005-03-16", 11, 1)]
    w = [g("2005-03-17", 11, 1), g("2005-03-18", 11, 1)]
    r = merge.merge_sport(d, w, rules=different((("2005-03-16", "11-1"), ("2005-03-17", "11-1"))))
    assert [m["wabash"]["date"] for m in r.pairs] == ["2005-03-18"]


# ---- the scraper reads the file ----------------------------------------------------------------- #

def run(both_dir, tmp_path, *extra):
    return scrape.main(["--fixtures", str(both_dir), "--output", str(tmp_path / "series.json"),
                        "--upcoming-output", str(tmp_path / "upcoming.json"), *extra])


def test_the_scraper_applies_the_rulings_next_to_the_output_file(both_dir, tmp_path, capsys):
    assert run(both_dir, tmp_path) == 0
    plain = json.loads((tmp_path / "series.json").read_text(encoding="utf-8"))
    assert plain["sports"]["football"]["games_played"] == 132
    write(tmp_path, {"football": {"same_game": [same(("1891-11-21", "1-0"), ("1891-11-02", "1-0"))]}})
    capsys.readouterr()
    assert run(both_dir, tmp_path) == 0
    ruled = json.loads((tmp_path / "series.json").read_text(encoding="utf-8"))["sports"]["football"]
    assert (ruled["games_played"], ruled["record"]) == (131, {"depauw_wins": 59, "wabash_wins": 63, "ties": 9})
    assert "REVIEW Football: matched beyond 2 days (ruling): DePauw 1891-11-21 1-0  /  Wabash 1891-11-02 1-0" \
        in capsys.readouterr().out


def test_the_run_lists_every_match_beyond_the_window_and_every_leftover_pair(both_dir, tmp_path, capsys):
    assert run(both_dir, tmp_path) == 0
    out = capsys.readouterr().out
    assert "REVIEW Men's Soccer: matched beyond 2 days (typo): DePauw 1969-11-16 3-0  /  Wabash 1968-11-16 3-0" in out
    assert "REVIEW Men's Basketball: matched beyond 2 days (no_date): DePauw 1962-12-10 66-69  /  Wabash 1962-09-03 66-69" in out
    assert "CHECK Football: DePauw only 1891-11-21 1-0  /  Wabash only 1891-11-02 1-0" in out


def test_a_stale_ruling_turns_the_run_red_and_keeps_that_sports_last_good_data(both_dir, tmp_path, capsys):
    assert run(both_dir, tmp_path) == 0
    good = json.loads((tmp_path / "series.json").read_text(encoding="utf-8"))["sports"]
    write(tmp_path, {"football": {"same_game": [same(("1801-01-01", "1-0"), ("1801-01-02", "1-0"))]}})
    capsys.readouterr()
    assert run(both_dir, tmp_path) == 1
    err = capsys.readouterr().err
    assert "FAIL Football" in err and "no longer finds DePauw and Wabash's row" in err
    now = json.loads((tmp_path / "series.json").read_text(encoding="utf-8"))["sports"]
    assert now["football"] == good["football"] and now["baseball"] == good["baseball"]   # nothing half-applied


def test_rulings_are_skipped_when_only_one_school_has_data_for_the_sport(both_dir, tmp_path):
    (both_dir / "wabash-football.html").write_text("<html><body>Service unavailable</body></html>")
    write(tmp_path, {"football": {"same_game": [same(("1891-11-21", "1-0"), ("1891-11-02", "1-0"))]}})
    assert run(both_dir, tmp_path) == 1                      # red because Wabash's page failed, not the ruling
    sports = json.loads((tmp_path / "series.json").read_text(encoding="utf-8"))["sports"]
    assert sports["football"]["games_played"] == 131 and "sources" not in sports["football"]


def test_a_broken_overrides_file_stops_the_run_before_anything_is_written(both_dir, tmp_path, capsys):
    (tmp_path / "overrides.json").write_text("{oops", encoding="utf-8")
    assert run(both_dir, tmp_path) == 1
    assert "FAIL overrides" in capsys.readouterr().err
    assert not (tmp_path / "series.json").exists() and not (tmp_path / "depauw.json").exists()


def test_the_overrides_path_can_be_given_explicitly(both_dir, tmp_path):
    elsewhere = tmp_path / "rules" / "mine.json"
    elsewhere.parent.mkdir()
    elsewhere.write_text(json.dumps({"schema_version": 1, "sports": {"football": {"same_game": [
        same(("1891-11-21", "1-0"), ("1891-11-02", "1-0"))]}}}), encoding="utf-8")
    assert run(both_dir, tmp_path, "--overrides", str(elsewhere)) == 0
    assert json.loads((tmp_path / "series.json").read_text(encoding="utf-8"))["sports"]["football"]["games_played"] == 131


# ---- the committed rulings, applied to the real pages ------------------------------------------- #

def committed(key):
    from test_merge import MONTHS, both_schools
    d, w = both_schools(key)
    rules = merge.load_overrides(ROOT / "data" / "overrides.json").get(key)
    return d, w, merge.merge_sport(d, w, season_months=MONTHS[key], rules=rules)      # raises if a ruling is stale


EXPECTED = {   # sport: (games, DePauw wins, Wabash wins, ties)
    "football": (131, 59, 63, 9), "mens-basketball": (231, 130, 100, 1), "baseball": (314, 155, 156, 3),
    "mens-soccer": (65, 41, 18, 6), "mens-lacrosse": (12, 12, 0, 0), "mens-tennis": (121, 113, 7, 1)}


@pytest.mark.parametrize("key", EXPECTED)
def test_with_the_committed_rulings_each_sport_has_its_expected_record(key):
    d, w, r = committed(key)
    assert (len(r.games), *merge.tally(r.games)) == EXPECTED[key]


def test_the_committed_rulings_give_874_games_and_a_510_344_20_total():
    games = wins = losses = ties = 0
    for key in EXPECTED:
        r = committed(key)[2]
        games += len(r.games)
        w_, l_, t_ = merge.tally(r.games)
        wins, losses, ties = wins + w_, losses + l_, ties + t_
    assert (games, wins, losses, ties) == (874, 510, 344, 20)


def test_the_committed_rulings_make_football_and_baseball_equal_the_wabash_page_or_both():
    _, _, football = committed("football")
    assert football.stats["both"] == 131 and football.stats["depauw_only"] == football.stats["wabash_only"] == 0
    d, w, baseball = committed("baseball")
    assert merge.tally(baseball.games) == merge.tally(w) == (155, 156, 3)             # Wabash's own page, exactly
    assert baseball.stats["depauw_only"] == 0 and baseball.stats["wabash_only"] == 173


def test_the_committed_rulings_pin_every_match_beyond_the_two_day_window():
    pinned = set()
    for key in ("mens-basketball", "baseball", "mens-soccer"):
        for m in merge.beyond_window(committed(key)[2]):
            assert m["kind"] == "ruling", (key, m["depauw"]["date"])                    # nothing left to automatic guesses
            pinned.add((key, m["depauw"]["date"]))
    football = committed("football")[2]
    assert [m["kind"] for m in merge.beyond_window(football)] == ["ruling"]
    assert pinned == {("mens-basketball", "1936-01-21"), ("mens-basketball", "1956-01-10"),
                      ("mens-basketball", "1962-12-10"), ("mens-basketball", "1978-01-07"),
                      ("mens-basketball", "1906-01-29"), ("mens-basketball", "1906-02-13"),
                      ("mens-basketball", "1907-02-05"), ("mens-basketball", "1907-02-27"),
                      ("mens-basketball", "1928-02-14"),
                      ("baseball", "1990-04-07"), ("baseball", "1986-03-19"), ("baseball", "1986-03-19"),
                      ("mens-soccer", "1969-10-12"), ("mens-soccer", "1969-10-15"), ("mens-soccer", "1969-11-16")}


def test_the_1928_basketball_rows_are_one_game_by_ruling_with_wabashs_version_shown():
    # Wabash played Notre Dame on Feb 29, 1928; its Feb 29 row is the Feb 14 game with another date and score.
    d, w, r = committed("mens-basketball")
    [game] = [x for x in r.games if x["date"].startswith("1928-02-")]
    assert (game["date"], game["source"], game["depauw"], game["wabash"], game["result"]) == \
        ("1928-02-14", "both", 23, 22, "W")
    assert game["disputed"] and game["wabash_version"] == {"result": "W", "depauw": 32, "wabash": 30}
    assert game["date_differs"] and game["wabash_date"] == "1928-02-29"
    assert merge.near_misses(r) == []                  # no leftover pair is left to look like the same game


def test_with_the_committed_rulings_basketball_equals_depauws_own_page():
    d, w, r = committed("mens-basketball")
    assert merge.tally(r.games) == merge.tally(d) == (130, 100, 1) and len(r.games) == len(d) == 231
    assert (r.stats["both"], r.stats["depauw_only"], r.stats["wabash_only"]) == (231, 0, 0)


def test_the_1986_doubleheader_is_two_games_with_the_dates_shown():
    d, w, r = committed("baseball")
    games = [x for x in r.games if x["date"].startswith("1986-03")]
    assert [(x["date"], x["depauw"], x["wabash"], x["source"], x["wabash_date"]) for x in games] == [
        ("1986-03-19", 12, 4, "both", "1986-03-23"), ("1986-03-19", 16, 4, "both", "1986-03-23")]
