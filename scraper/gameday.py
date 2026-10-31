"""
Game-day mode: when is a DePauw vs. Wabash game being played, and has its result arrived?

Two things use this, and both need only the stored data (data/upcoming.json and data/series.json, fetched from the data branch):

  * the gate (`python scraper/gameday.py gate`), the first and fastest job of the workflow. The workflow
    also runs every 30 minutes, and on almost every one of those runs the gate says "nothing to do" and
    the run ends there: no scrape, no commit, no deploy, "Last checked" unchanged.
  * the page banner (render.py), which asks for the phase of each game being played today.

Standard library only (the gate job installs nothing), and nothing here reads the clock: "now" is passed in.

The window for a game (a sport's games on one date)
  starts   at the first game's scheduled start, or noon Eastern when no game on that date has a time;
  ends     when the history has as many games on that date as the schedule lists (the result is in, on
           either school's history page), or at 3 AM Eastern the next day, whichever comes first.
A postponed game opens no window. A game that has just finished but is not in the history yet stays in
data/upcoming.json (upcoming.carry_over), which is what keeps the window open until the result arrives.

Phases shown by the banner, for games dated today (Eastern):
  pregame   before the start
  underway  after the start, result not in yet (also when only some of a doubleheader is in)
  final     the history has every game on the date
The banner is for the calendar day only: after midnight Eastern it is gone at the next deploy, even
though the window can stay open until 3 AM so the result still gets scraped.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

import meetings
from timeutil import from_eastern, parse_clock, to_eastern

ROOT = Path(__file__).resolve().parent.parent
UPCOMING = ROOT / "data" / "upcoming.json"
SERIES = ROOT / "data" / "series.json"

# The frequent schedule in the workflow, at jittered minutes (not :00 or :30, when GitHub is busiest).
# A test checks that the workflow's cron line is exactly this string.
FREQUENT_CRON = "7,37 * * * *"

NOON = dt.time(12, 0)
WINDOW_END = dt.time(3, 0)        # Eastern, on the day after the game

PREGAME, UNDERWAY, FINAL = "pregame", "underway", "final"


def games_on(date: str, scheduled: list, history: list) -> tuple[list, list]:
    """(games still on the schedule for `date`, games the history has on `date`). A postponed game is not
    being played, and a history game with no usable date can never be on a game day."""
    sched = [g for g in scheduled if g["date"] == date and g.get("status") != "postponed"]
    hist = [g for g in history if g["date"] == date and g.get("date_known", True)]
    return sched, hist


def start_of(sched: list, date: dt.date) -> dt.datetime:
    """When the first game on `date` starts, as an aware UTC time: the earliest start time any game lists,
    or noon Eastern when none has one (TBA)."""
    known = [t for t in (parse_clock(g.get("time")) for g in sched) if t]
    return from_eastern(dt.datetime.combine(date, min(known) if known else NOON))


def window_end(date: dt.date) -> dt.datetime:
    return from_eastern(dt.datetime.combine(date + dt.timedelta(days=1), WINDOW_END))


def todays_games(now: dt.datetime, upcoming: dict, sports: dict) -> list:
    """What is being played today (Eastern), one entry per sport that has a game on the date:
    {key, name, date, games (the schedule's), results (the history's), start, phase}."""
    today = to_eastern(now)[0].date()
    out = []
    for key, s in sports.items():
        sched, hist = games_on(today.isoformat(), upcoming.get(key, {}).get("games", []), s["games"])
        expected = max(len(sched), len(hist))
        if not expected:
            continue
        start = start_of(sched, today) if sched else None
        if len(hist) >= expected:
            phase = FINAL
        elif start is not None and now < start and not hist:
            phase = PREGAME
        else:
            phase = UNDERWAY
        out.append({"key": key, "name": s["name"], "date": today.isoformat(), "games": sched, "results": hist,
                    "start": start, "phase": phase})
    return out


def open_windows(now: dt.datetime, upcoming: dict, sports: dict) -> list:
    """Reasons the frequent run should do real work right now, one per sport whose window is open. Games
    dated yesterday are looked at too, because a window runs until 3 AM the next day."""
    today = to_eastern(now)[0].date()
    reasons = []
    for date in (today - dt.timedelta(days=1), today):
        for key, s in sports.items():
            sched, hist = games_on(date.isoformat(), upcoming.get(key, {}).get("games", []), s["games"])
            if not sched or len(hist) >= len(sched):
                continue                      # nothing scheduled, or every result is already in
            if start_of(sched, date) <= now < window_end(date):
                reasons.append(f"{s['name']} {date.isoformat()}: {len(hist)} of {len(sched)} result(s) in")
    return reasons


def load_inputs(upcoming_path: Path = UPCOMING, series_path: Path = SERIES) -> tuple[dict, dict]:
    """(upcoming games per sport, sports from series.json). A missing schedule file is an empty schedule;
    a missing or unreadable series file raises, because the gate cannot judge a result without it."""
    upcoming = meetings.load_upcoming(upcoming_path)
    sports = json.loads(Path(series_path).read_text(encoding="utf-8"))["sports"]
    return upcoming, sports


def gate(now: dt.datetime, schedule: str, upcoming: dict, sports: dict) -> tuple[bool, str]:
    """Should this workflow run do the real work? Only the frequent schedule is ever skipped: every other
    trigger (the two daily schedules, a push to main, a manual run) always runs."""
    if schedule != FREQUENT_CRON:
        return True, "not a frequent run, so it always runs"
    reasons = open_windows(now, upcoming, sports)
    if reasons:
        return True, "game-day window open: " + "; ".join(reasons)
    return False, "no game-day window is open"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    g = sub.add_parser("gate", help="print run=true or run=false (and write it to $GITHUB_OUTPUT when set)")
    g.add_argument("--schedule", default="", help="the cron string that started the run (empty for other triggers)")
    g.add_argument("--now", help="ISO time with an offset, for local checks only (default: the real time)")
    g.add_argument("--upcoming", type=Path, default=UPCOMING)
    g.add_argument("--series", type=Path, default=SERIES)
    args = ap.parse_args(argv)

    now = dt.datetime.fromisoformat(args.now).astimezone(dt.timezone.utc) if args.now \
        else dt.datetime.now(dt.timezone.utc)
    try:
        upcoming, sports = (None, None) if args.schedule != FREQUENT_CRON else load_inputs(args.upcoming, args.series)
        run, why = gate(now, args.schedule, upcoming, sports)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"gate could not read the data files: {exc!r}", file=sys.stderr)
        _output(False)
        return 1
    print(f"run={'true' if run else 'false'}: {why}")
    _output(run)
    return 0


def _output(run: bool) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"run={'true' if run else 'false'}\n")


if __name__ == "__main__":
    sys.exit(main())
