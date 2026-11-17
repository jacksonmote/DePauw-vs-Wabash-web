"""
Numbers worked out from a sport's game list: the Monon Bell standings and the per-sport fun facts
(biggest wins, streaks, one-score games, highest-scoring games, overtime, season-by-season).

Pure functions over the games in data/series.json (dicts with date, result, depauw, wabash, ...).
Results are from DePauw's side: "W" is a DePauw win, "L" a Wabash win, "T" a tie.
"""

from __future__ import annotations

from merge import season_years

# Between the items of one line of display text (a decade's summary in a game list, a heist card's tag line): a
# no-break space holds each "·" to the item before it, so a line may end with the dot but can never start with
# one, however the line wraps.
SEPARATOR = "\u00a0· "

# The football game became the Monon Bell game with the 1932 season.
BELL_START = 1932


BELL_WINNERS = {"W": "DePauw", "L": "Wabash"}      # a game's result, from DePauw's side, to the school that won it
# What a season is, in bell_seasons: its game was played, no game was played that year, or it is the season of
# `this_year` and its game has no result yet.
PLAYED, NO_GAME, NOT_YET = "played", "no game", "not yet played"


def bell_seasons(games: list, this_year: int | None = None) -> list:
    """The Monon Bell, season by season: the one place that works out who holds it. Everything the site says about
    the Bell (the home page's strip and the football card's block through `bell`, the year-by-year page through
    this list and `bell_summary`) is read from here, so the pages cannot disagree.

    One entry per season from BELL_START through the year of the newest Bell game, or through `this_year` (the
    Eastern year of the build) when that is later, oldest first:

      year      the season (a football season is one calendar year)
      status    PLAYED; NO_GAME for a past year without a game (the Bell stays where it was); NOT_YET for
                `this_year` while it has no result
      games     that year's games, oldest first (one, or none); `game` is the last of them and `result` its
                result ("W" a DePauw win, "L" a Wabash win, "T" a tie), both None without a game
      holder    "DePauw", "Wabash" or None: who had the Bell once the season was over. The winner takes it, a tie
                leaves it where it was, and nobody has it until a game has been won (the first Bell game was a tie)
      changed   True when the Bell went from one school to the other that season (the first win of all is not a
                change of hands: nobody held the Bell before it)
      since     the year the holder's unbroken hold began (the win that took the Bell), None with no holder
      held      the games of that hold so far, from that win on: the holder's wins, and the ties that left it there

    A game with no usable date would be placed by its placeholder year; no Bell game has one."""
    by_year: dict = {}
    for g in sorted((g for g in games if int(g["date"][:4]) >= BELL_START), key=lambda g: g["date"]):
        by_year.setdefault(int(g["date"][:4]), []).append(g)
    if not by_year:
        return []
    out, holder, since, held = [], None, None, []
    for year in range(BELL_START, max(max(by_year), this_year or 0) + 1):
        before, played = holder, by_year.get(year, [])
        for g in played:
            winner = BELL_WINNERS.get(g["result"])
            if winner and winner != holder:
                holder, since, held = winner, year, []
            if holder:
                held = held + [g]
        out.append({"year": year, "games": played, "game": played[-1] if played else None,
                    "result": played[-1]["result"] if played else None,
                    "status": PLAYED if played else NOT_YET if year == this_year else NO_GAME,
                    "holder": holder, "changed": before is not None and holder != before,
                    "since": since, "held": held})
    return out


def bell(games: list) -> dict | None:
    """The Monon Bell standings from the games played for it, read from `bell_seasons`.

    The holder is whoever won the most recent Bell game that was not a tie: a tie leaves the Bell where
    it is. "since" is the year of the holder's first win in the unbroken run of wins and ties that
    ends today, and "held_games" counts the games in that run, split into "held_wins" and "held_ties".
    Returns None when there are no Bell games; the holder is None when none was ever decided.
    """
    seasons = bell_seasons(games)
    if not seasons:
        return None
    played = [g for s in seasons for g in s["games"]]
    last = seasons[-1]
    ties = sum(1 for g in last["held"] if g["result"] == "T")
    return {
        "depauw": sum(1 for g in played if g["result"] == "W"),
        "wabash": sum(1 for g in played if g["result"] == "L"),
        "ties": sum(1 for g in played if g["result"] == "T"),
        "games": len(played),
        "holder": last["holder"], "since": last["since"], "held_games": len(last["held"]),
        "held_wins": len(last["held"]) - ties, "held_ties": ties,
    }


def bell_summary(seasons: list) -> dict:
    """The year-by-year page's figures, counted from `bell_seasons`. A season that has not been played yet
    (NOT_YET) is left out of all of them: nobody has ended it with the Bell.

      seasons   {"DePauw": n, "Wabash": n, None: n}: the seasons each school ended holding the Bell, a year
                without a game included (the holder kept it), and the seasons nobody held it
      longest   {"DePauw": run, "Wabash": run}: each school's longest unbroken hold as {"seasons", "first",
                "last", "times"} (how many holds reached that length; the years are the most recent one's), or
                None for a school that never held it
      changes   how many times the Bell went from one school to the other"""
    done = [s for s in seasons if s["status"] != NOT_YET]
    runs = []
    for s in done:
        if runs and runs[-1][0] == s["holder"]:
            runs[-1][1].append(s["year"])
        else:
            runs.append((s["holder"], [s["year"]]))
    longest = {}
    for school in BELL_WINNERS.values():
        own = [years for holder, years in runs if holder == school]
        best = max((len(years) for years in own), default=0)
        top = [years for years in own if len(years) == best]
        longest[school] = ({"seasons": best, "first": top[-1][0], "last": top[-1][-1], "times": len(top)}
                           if best else None)
    return {"seasons": {who: sum(1 for s in done if s["holder"] == who) for who in (*BELL_WINNERS.values(), None)},
            "longest": longest, "changes": sum(1 for s in done if s["changed"])}


# --------------------------------------------------------------------------- #
# Per-sport fun facts
# --------------------------------------------------------------------------- #

import re  # noqa: E402

# What counts as a "one-score" game in each sport: decided by this many points/runs/goals/matches or fewer.
# A sport that isn't listed gets no one-score fact rather than a guess.
ONE_SCORE = {"football": 8, "mens-basketball": 3, "baseball": 1, "mens-soccer": 1,
             "mens-lacrosse": 1, "mens-tennis": 1, "mens-swimming-and-diving": 10}
# The word before "games" in that fact. A swimming meet is scored in dozens of points, so 10 points is "close",
# not "one score".
CLOSE_WORD = {"mens-swimming-and-diving": "Close"}
UNIT = {"points": "point", "runs": "run", "goals": "goal", "individual matches": "match"}
# Sports that meet more than once a year get a season-by-season list when at least one season in five
# has two or more games. Football (two such seasons) and soccer don't qualify; basketball, baseball and
# tennis do.
MULTI_SEASON_SHARE = 0.2
EXTRA_INNING_FROM = 10

OT_RE = re.compile(r"\b\d*\s*ot\b", re.I)
INNINGS_RE = re.compile(r"(\d+)\s*inn", re.I)


def chronological(games: list) -> list:
    """The games as stored: oldest first, a doubleheader in playing order, and a game with no usable date
    first within its season. Sorting by the stored date would put such a game on a day it was not played."""
    return list(games)


def is_dated(g: dict) -> bool:
    return g.get("date_known", True)


def most_recent(games: list) -> dict | None:
    """The newest game, going by dates: a game with no recorded date is never newer than one with a date.
    Only when none of the games has a date does the last one stand in."""
    dated = [g for g in games if is_dated(g)]
    return (dated or games or [None])[-1]


def game_year(g: dict) -> str:
    """The year to show for a game: its date's year, or the year its season ends in when it has no date."""
    if is_dated(g):
        return g["date"][:4]
    return str(season_years(g.get("season"), g["date"])[1])


def has_score(g: dict) -> bool:
    """False for a game whose school lists the result but no score (two 1934 swimming meets)."""
    return g.get("score_known", True)


def num(x):
    """A score or margin without float noise: 147.5 - 143.5 is 4, not 4.0; 172.5 - 127.5 is 45."""
    x = round(x, 1)
    return int(x) if float(x).is_integer() else x


def margin(g: dict):
    return num(abs(g["depauw"] - g["wabash"]))


def biggest_wins(games: list, result: str) -> list:
    """Every game of DePauw's ("W") or Wabash's ("L") widest margin, oldest first."""
    wins = [g for g in chronological(games) if g["result"] == result and has_score(g)]
    if not wins:
        return []
    best = max(margin(g) for g in wins)
    return [g for g in wins if margin(g) == best]


def biggest_win(games: list, result: str) -> dict | None:
    """The widest win for one side. When several share the margin, the most recent."""
    return most_recent(biggest_wins(games, result))


def longest_streak(games: list, result: str) -> dict | None:
    """Longest run of straight wins for one side (a tie ends a streak). "times" is how many separate runs
    reached that length; the years are those of the most recent one. Returns None for a run shorter than
    two, which isn't worth a line."""
    best, times, run = None, 0, []
    for g in chronological(games) + [None]:
        if g is not None and g["result"] == result:
            run.append(g)
            continue
        if len(run) >= 2:
            if best is None or len(run) > len(best):
                best, times = run, 1
            elif len(run) == len(best):
                best, times = run, times + 1
        run = []
    if best is None:
        return None
    return {"games": len(best), "first": game_year(best[0]), "last": game_year(best[-1]), "times": times}


def one_score_games(key: str, games: list) -> list:
    """Decided games (not ties) won by no more than the sport's one-score margin, oldest first."""
    limit = ONE_SCORE.get(key)
    if limit is None:
        return []
    return [g for g in chronological(games) if g["result"] != "T" and has_score(g) and margin(g) <= limit]


def highest_scoring_games(games: list) -> list:
    """Every game with the most combined points/goals/runs, oldest first."""
    scored = [g for g in games if has_score(g)]
    if not scored:
        return []
    best = max(num(g["depauw"] + g["wabash"]) for g in scored)
    return [g for g in chronological(scored) if num(g["depauw"] + g["wabash"]) == best]


def highest_scoring(games: list) -> dict | None:
    """Most combined points/goals/runs in one game; the most recent when tied."""
    return most_recent(highest_scoring_games(games))


def overtime_games(games: list) -> list:
    return [g for g in chronological(games) if OT_RE.search(str(g.get("note") or ""))]


def extra_inning_games(games: list) -> list:
    """Baseball notes look like '(7 inn.)' for a shortened game; only a count past nine is extra innings."""
    out = []
    for g in chronological(games):
        m = INNINGS_RE.search(str(g.get("note") or ""))
        if m and int(m.group(1)) >= EXTRA_INNING_FROM:
            out.append(g)
    return out


def meets_more_than_once_a_year(games: list) -> bool:
    per_season: dict = {}
    for g in games:
        s = g.get("season") or g["date"][:4]
        per_season[s] = per_season.get(s, 0) + 1
    return bool(per_season) and sum(1 for n in per_season.values() if n >= 2) / len(per_season) >= MULTI_SEASON_SHARE


def season_series(games: list) -> list:
    """[(season, 'DePauw 2-0'), ...] newest first, for sports that meet more than once a year; [] otherwise."""
    if not meets_more_than_once_a_year(games):
        return []
    per_season: dict = {}
    for g in chronological(games):
        s = g.get("season") or g["date"][:4]
        rec = per_season.setdefault(s, {"W": 0, "L": 0, "T": 0})
        rec[g["result"]] += 1
    return list(reversed([(season, record_words(r)) for season, r in per_season.items()]))


def record_words(r: dict) -> str:
    """A short record with its leader first, ties last when there are any: 'DePauw 4-1', 'Wabash 6-3-1',
    'Split 2-2'. `r` counts results from DePauw's side: {"W": DePauw wins, "L": Wabash wins, "T": ties}."""
    tail = f"-{r['T']}" if r["T"] else ""
    if r["W"] > r["L"]:
        return f"DePauw {r['W']}-{r['L']}{tail}"
    if r["L"] > r["W"]:
        return f"Wabash {r['L']}-{r['W']}{tail}"
    return f"Split {r['W']}-{r['L']}{tail}"


def tally(games: list) -> dict:
    """{"W": DePauw wins, "L": Wabash wins, "T": ties} for any list of games."""
    return {result: sum(1 for g in games if g["result"] == result) for result in "WLT"}


def by_decade(games: list) -> list:
    """The game list in groups for display: [(decade, games)], newest decade first and each group's games newest
    first (a doubleheader's later game before its earlier one). `decade` is the first year of the decade the game
    was played in (2020 for Jan 1, 2020 to Dec 31, 2029), taken from the game's date, never its season, so a
    January game of the 1929-30 season is in the 1930s. Games with no recorded date carry a placeholder date that
    must not be read, so they form one last group whose decade is None. A decade with no games has no group."""
    groups: dict = {}
    for g in reversed(chronological(games)):
        groups.setdefault(int(g["date"][:3]) * 10 if is_dated(g) else None, []).append(g)
    dated = sorted((d for d in groups if d is not None), reverse=True)
    return [(d, groups[d]) for d in dated] + ([(None, groups[None])] if None in groups else [])
