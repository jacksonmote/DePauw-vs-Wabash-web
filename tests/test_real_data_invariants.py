"""Rules the committed data/series.json must always satisfy, whatever the latest results are.

The bot rewrites that file with every new game, so these tests never pin a count (918 games, 43-44-6 and so
on: those are checked against frozen copies in the other test files). They check what has to be true after any
scrape, so a Bell game on a Saturday cannot make a test fail unless the data itself is broken."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
import facts  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SERIES = json.loads((ROOT / "data" / "series.json").read_text(encoding="utf-8"))
SPORTS = SERIES["sports"]
RESULTS = {"W", "L", "T"}


def tally(games):
    return tuple(sum(1 for g in games if g["result"] == r) for r in "WLT")


def test_the_totals_are_the_sum_of_the_sports():
    t = SERIES["totals"]
    assert t["games_played"] == sum(s["games_played"] for s in SPORTS.values())
    assert t["depauw_wins"] == sum(s["record"]["depauw_wins"] for s in SPORTS.values())
    assert t["wabash_wins"] == sum(s["record"]["wabash_wins"] for s in SPORTS.values())
    assert t["ties"] == sum(s["record"]["ties"] for s in SPORTS.values())
    assert t["depauw_wins"] + t["wabash_wins"] + t["ties"] == t["games_played"]


@pytest.mark.parametrize("key", SPORTS)
def test_each_sports_record_and_count_match_its_games(key):
    s = SPORTS[key]
    assert s["games_played"] == len(s["games"])
    assert tally(s["games"]) == (s["record"]["depauw_wins"], s["record"]["wabash_wins"], s["record"]["ties"])


@pytest.mark.parametrize("key", SPORTS)
def test_every_result_is_valid_and_agrees_with_its_score(key):
    for g in SPORTS[key]["games"]:
        assert g["result"] in RESULTS, (key, g["date"])
        if g.get("score_known", True):
            expected = "W" if g["depauw"] > g["wabash"] else "L" if g["depauw"] < g["wabash"] else "T"
            assert g["result"] == expected, (key, g["date"], g["depauw"], g["wabash"])


@pytest.mark.parametrize("key", SPORTS)
def test_the_merge_used_each_row_once(key):
    s = SPORTS[key]
    src = s.get("sources")
    if src:                                      # every game came from exactly one of the three groups
        assert src["both"] + src["depauw_only"] + src["wabash_only"] == len(s["games"])
    assert {g["source"] for g in s["games"]} <= {"both", "depauw", "wabash"}
    seen = [(g["date"], g["depauw"], g["wabash"], g.get("boxscore")) for g in s["games"] if g.get("boxscore")]
    assert len(seen) == len(set(seen)), "a box score appears twice"


@pytest.mark.parametrize("key", SPORTS)
def test_the_streak_and_last_game_match_the_games(key):
    s = SPORTS[key]
    games = s["games"]
    last = games[-1]["result"]
    n = 0
    for g in reversed(games):
        if g["result"] != last:
            break
        n += 1
    assert s["current_streak"] == f"{last}{n}"
    assert s["last_game"] == max(g["date"] for g in games if g.get("date_known") is not False)


def test_the_bell_era_starts_in_1932_and_its_record_adds_up():
    assert facts.BELL_START == 1932
    b = facts.bell(SPORTS["football"]["games"])
    assert b["depauw"] + b["wabash"] + b["ties"] == b["games"]
    assert b["holder"] in ("DePauw", "Wabash")
    assert b["since"] >= facts.BELL_START
    assert min(int(g["date"][:4]) for g in SPORTS["football"]["games"] if int(g["date"][:4]) >= facts.BELL_START) >= 1932
