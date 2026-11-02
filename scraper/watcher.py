"""
The game-day watcher: keeps the site updating every 15 minutes during a DePauw vs. Wabash game.

Why it exists: GitHub starts scheduled runs late or not at all when it is busy (on Nov 1, 2026 about one in
ten of the 30-minute runs started), so a game-day window cannot rely on the schedule. Once any one watcher run
starts on a game day, it starts "Update data and deploy site" itself (workflow_dispatch, which GITHUB_TOKEN may
do) every CADENCE until the window closes, so no further scheduled run has to fire.

What it never does: fetch the schools' sites, commit, or deploy. It reads the data branch's upcoming.json and
series.json (the same files the gate reads), uses gameday.py's window rules, and dispatches the existing
workflow with `gameday: true`, which makes that run read only the sports playing today.

Commands (each run from .github/workflows/gameday-watcher.yml, standard library only, like the gate):

  python scraper/watcher.py gate  [--relay --watched G] [--dry-run --now T | --test-dispatch]
                                                                   should this run watch? (writes watch=)
  python scraper/watcher.py watch [--relay --watched G] [--dry-run --now T | --test-dispatch]
                                                                   the loop below
  python scraper/watcher.py running                                is a watcher run in progress? (running=)
  python scraper/watcher.py handoff                                a daily run starts the watcher on a game day

The gate: watch when today (Eastern) has a game that is not postponed and whose window is open or still to
open ("any game today"; a run that would wait longer than its job may last relays to a fresh run). A run that
is not a relay or a dry run also stands aside when an older watcher run is in progress, so two never watch at
once; the newer one always yields, so two gates starting together cannot both stand aside.

The loop, one pass every CADENCE (sooner when the window opens): fetch the data branch again, then
  window not open yet    wait until it opens
  window open            start an update (at most one every CADENCE)
  window closed, result  start that game's one final update; then stop, or, when a later game's window is
                         still to open, keep waiting for it (each game gets exactly one final update; while
                         another window is open the final waits for a gap)
  window closed, 3 AM    stop (a result never arrived; the daily runs pick it up)
  no game left           stop
and after RELAY_AFTER in the job, while a window is open or still to open, start a fresh watcher run marked
as a relay, then stop: jobs on GitHub's runners may last 6 hours at most. The relay is handed the games still
owed a final update (`--watched sport:date,...`), so a relay that starts after the last window closed still
starts that final update.

`--test-dispatch` (a hand-started run with the test_dispatch input) skips the gate's game check and starts one
"Update data and deploy site" run on main with gameday=true, then stops: proof that the dispatch works, on any
day. It cannot be combined with --dry-run.

A dry run (`--dry-run`) does all of that except the dispatches, which it logs, and simulates the clock instead
of sleeping, so a whole game day passes in seconds; the data does not change during it (nothing is
dispatched), so a dry run inside a window keeps dispatching on paper until 3 AM or the relay. `--now` sets a
pretend start time for a dry run: Eastern when it has no offset (2026-11-14T12:30), as given when it has one.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import gameday
from politeness import USER_AGENT
from timeutil import from_eastern, to_eastern

ROOT = Path(__file__).resolve().parent.parent
WATCHER_WORKFLOW = "gameday-watcher.yml"
UPDATE_WORKFLOW = "update-and-deploy.yml"
REF = "main"
CADENCE = dt.timedelta(minutes=15)                    # at most one update this often
RELAY_AFTER = dt.timedelta(hours=4, minutes=45)       # the next pass after this hands over (jobs stop at 6 hours)
ACTIVE = ("in_progress", "queued", "waiting", "pending", "requested")
DRY_TITLE = "(dry run)"                               # in a dry run's run name, so the checks below ignore it
TEST_TITLE = "(test dispatch)"                        # likewise for a test dispatch, which watches nothing
MAX_FAILURES = 3                                      # in a row (dispatches, or fetches of the data branch), then it gives up, red
RETRY = dt.timedelta(minutes=1)                       # after a failed dispatch or fetch

BEFORE, OPEN, NONE = "before", "open", "none"


# ---- the decision logic (plain functions, no clock, no network) ----------------------------------------- #

def status(now: dt.datetime, upcoming: dict, sports: dict) -> tuple[str, str, dt.datetime | None]:
    """(OPEN, reasons, None), (BEFORE, what, when the first window opens) or (NONE, why, None)."""
    reasons = gameday.open_windows(now, upcoming, sports)
    if reasons:
        return OPEN, "; ".join(reasons), None
    today = to_eastern(now)[0].date()
    starts = []
    for key, s in sports.items():
        sched, hist = gameday.games_on(today.isoformat(), upcoming.get(key, {}).get("games", []), s["games"])
        if sched and len(hist) < len(sched):
            start = gameday.start_of(sched, today)
            if now < start:
                starts.append((start, s["name"]))
    if starts:
        start, name = min(starts)
        return BEFORE, f"{name} window opens at {fmt(start)}", start
    return NONE, "no game today that is not postponed, finished or past its window", None


def result_in(key: str, date: str, upcoming: dict, sports: dict) -> bool:
    """Has the history every game the schedule listed for that sport and date? (Once the result is in, the
    schedule may drop the game, so any history game on the date with nothing left on the schedule counts.)"""
    sched, hist = gameday.games_on(date, upcoming.get(key, {}).get("games", []), sports.get(key, {}).get("games", []))
    return bool(hist) and len(hist) >= len(sched)


def live(now: dt.datetime, date: str) -> bool:
    """Can a game on `date` still be updated: today (Eastern), or yesterday until its window ends at 3 AM?"""
    day = dt.date.fromisoformat(date)
    today = to_eastern(now)[0].date()
    return day == today or (day == today - dt.timedelta(days=1) and now < gameday.window_end(day))


def owed_finals(now: dt.datetime, watched: set, finalized: set, upcoming: dict, sports: dict) -> set:
    """Games watched (in this run, or handed over by the run before) whose result is now in and that have not
    had their final update yet."""
    return {(k, d) for k, d in set(watched) - set(finalized) if live(now, d) and result_in(k, d, upcoming, sports)}


def should_watch(now: dt.datetime, upcoming: dict, sports: dict, watched: set = frozenset()) -> tuple[bool, str]:
    """Watch when a window is open or still to open today, or when a game handed over by a relay's
    predecessor has its result in and still needs its final update."""
    state, why, _ = status(now, upcoming, sports)
    if state in (OPEN, BEFORE):
        return True, why
    owed = owed_finals(now, watched, set(), upcoming, sports)
    if owed:
        names = ", ".join(f"{k} {d}" for k, d in sorted(owed))
        return True, f"the result is in for {names}, watched by the run before this relay: one final update"
    return False, why


def encode_watched(games: set) -> str:
    """(sport key, date) pairs for the relay input: "football:2026-11-14,mens-basketball:2026-11-14"."""
    return ",".join(f"{k}:{d}" for k, d in sorted(games))


def decode_watched(text: str) -> set:
    """The relay input back into (sport key, date) pairs; a malformed one raises ValueError."""
    out = set()
    for item in (text or "").split(","):
        item = item.strip()
        if not item:
            continue
        key, _, date = item.partition(":")
        if not key:
            raise ValueError(item)
        dt.date.fromisoformat(date)
        out.add((key, date))
    return out


@dataclass
class Decision:
    action: str             # "dispatch", "wait", "final", "stop", "relay"
    until: dt.datetime | None   # the next pass; None after a "final" means stop once it is started
    why: str
    state: str = ""
    finals: frozenset = frozenset()   # for "final": the (sport key, date) this update finishes


def decide(now: dt.datetime, started: dt.datetime, last_dispatch: dt.datetime | None, watched: set,
           upcoming: dict, sports: dict, finalized: set = frozenset()) -> Decision:
    """One pass of the loop. `watched` holds the (sport key, date) of every window seen open in this run (and
    those a relay's predecessor handed over); `finalized`, those that have had their final update.

    Each game gets exactly one final update, as soon as no window is open after its result is in: right away
    when a later game's window is still to open (the watcher then keeps waiting for it), or at the end of the
    day. While another window is open the regular updates carry on and the final waits for a gap. A final
    keeps the CADENCE too: never sooner than 15 minutes after the update before it."""
    state, why, start = status(now, upcoming, sports)
    owed = owed_finals(now, watched, finalized, upcoming, sports)
    if owed and state != OPEN:
        if last_dispatch is not None and now - last_dispatch < CADENCE:
            return Decision("wait", last_dispatch + CADENCE,
                            "a result is in; its final update comes at the next 15-minute mark", state)
        names = ", ".join(f"{k} {d}" for k, d in sorted(owed))
        if state == BEFORE:
            return Decision("final", min(start, now + CADENCE),
                            f"the result is in for {names}, so its window closed; {why}", state, frozenset(owed))
        return Decision("final", None, f"the result is in for {names}, so the window closed", state, frozenset(owed))
    if state in (OPEN, BEFORE) and now - started >= RELAY_AFTER:
        return Decision("relay", None, f"{why}; {fmt_span(now - started)} in this job, so a fresh run takes over", state)
    if state == OPEN:
        if last_dispatch is None or now - last_dispatch >= CADENCE:
            return Decision("dispatch", now + CADENCE, why, state)
        return Decision("wait", last_dispatch + CADENCE, why, state)
    if state == BEFORE:
        return Decision("wait", min(start, now + CADENCE), why, state)
    if set(watched) - set(finalized):
        return Decision("stop", None, "the window closed at 3 AM Eastern without every result", state)
    if watched:
        return Decision("stop", None, "every game watched has had its final update", state)
    return Decision("stop", None, why, state)


def open_keys(now: dt.datetime, upcoming: dict, sports: dict) -> set:
    """(sport key, date) of every window open now."""
    today = to_eastern(now)[0].date()
    out = set()
    for date in (today - dt.timedelta(days=1), today):
        for key, s in sports.items():
            sched, hist = gameday.games_on(date.isoformat(), upcoming.get(key, {}).get("games", []), s["games"])
            if sched and len(hist) < len(sched) and gameday.start_of(sched, date) <= now < gameday.window_end(date):
                out.add((key, date.isoformat()))
    return out


@dataclass
class Outcome:
    dispatches: list = field(default_factory=list)      # when each update was started
    watched: set = field(default_factory=set)
    finalized: set = field(default_factory=set)         # games that have had their final update
    stopped: str = ""
    relayed: bool = False
    failed: bool = False                                # gave up after repeated failures (the run turns red)
    log: list = field(default_factory=list)


def run(clock, sleep, refresh, dispatch_update, dispatch_relay, log=print, handed_over: set = frozenset()) -> Outcome:
    """The watch loop. clock() -> aware UTC now; sleep(seconds); refresh() -> (upcoming, sports) from the data
    branch; dispatch_update() and dispatch_relay(owed) start the runs (no-ops that log in a dry run), where owed
    is the set of games watched and still owed a final update, for the relay to carry on. `handed_over` is that
    set as this run received it from the run before (a relay).

    A failed fetch of the data branch is tried again after RETRY (the last data read is never used to start an
    update); a failed dispatch or relay is retried after RETRY, without moving the 15-minute clock. MAX_FAILURES
    in a row of either stop the watcher and mark the outcome failed."""
    out = Outcome(watched=set(handed_over))
    started = clock()
    last = None
    data = None
    fetch_failures = failures = 0
    while True:
        try:
            data = refresh()
            fetch_failures = 0
        except Exception as exc:  # noqa: BLE001 - a passing network problem must not end the watch
            fetch_failures += 1
            log(f"{fmt(clock())}  could not read the data branch ({fetch_failures} in a row): {exc}")
            if fetch_failures >= MAX_FAILURES:
                out.stopped, out.failed = f"the data branch could not be read ({fetch_failures} in a row)", True
                return out
            sleep(RETRY.total_seconds())
            continue
        upcoming, sports = data
        now = clock()
        d = decide(now, started, last, out.watched, upcoming, sports, out.finalized)
        out.watched |= open_keys(now, upcoming, sports)
        line = f"{fmt(now)}  {d.state or '-':<6}  {d.action:<8}  {d.why}"
        out.log.append(line)
        log(line)
        if d.action in ("dispatch", "final", "relay"):
            try:
                if d.action == "relay":
                    dispatch_relay(out.watched - out.finalized)
                else:
                    dispatch_update()
                failures = 0
            except Exception as exc:  # noqa: BLE001 - one failed dispatch must not end the watch
                failures += 1
                log(f"  {d.action} FAILED ({failures} in a row), trying again in a minute: {exc}")
                if failures >= MAX_FAILURES:
                    out.stopped, out.failed = f"{failures} dispatches failed in a row", True
                    return out
                sleep(RETRY.total_seconds())
                continue
            if d.action != "relay":
                out.dispatches.append(now)
            out.finalized |= d.finals
        if d.action == "final" and d.until is None:
            out.stopped = d.why + "; started one last update"
            return out
        if d.action == "stop":
            out.stopped = d.why
            return out
        if d.action == "relay":
            out.relayed = True
            out.stopped = d.why
            return out
        last = now if d.action in ("dispatch", "final") else last
        sleep(max(1.0, (d.until - now).total_seconds()))


# ---- GitHub (standard library only; the token comes from the workflow) ---------------------------------- #

def api(method: str, path: str, token: str, body: dict | None = None, opener=None):
    req = urllib.request.Request(
        f"https://api.github.com{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28", "User-Agent": USER_AGENT})
    with (opener or urllib.request.urlopen)(req, timeout=30) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


def active_watchers(repo: str, token: str, opener=None) -> list:
    """Watcher runs that are queued or in progress, leaving out dry runs and test dispatches."""
    runs = {}
    for state in ACTIVE:
        data = api("GET", f"/repos/{repo}/actions/workflows/{WATCHER_WORKFLOW}/runs?status={state}&per_page=50",
                   token, opener=opener)
        for r in (data or {}).get("workflow_runs", []):
            title = r.get("display_title") or ""
            if DRY_TITLE not in title and TEST_TITLE not in title:
                runs[r["id"]] = r
    return sorted(runs.values(), key=lambda r: r["id"])


def older_watcher(own_id: int, runs: list) -> dict | None:
    """The run this one must yield to: an active watcher started before it (a lower run id). The newer of two
    always yields, so two runs starting together never both stand aside."""
    older = [r for r in runs if r["id"] < own_id]
    return older[0] if older else None


def dispatch(repo: str, token: str, workflow: str, inputs: dict, opener=None) -> None:
    api("POST", f"/repos/{repo}/actions/workflows/{workflow}/dispatches", token,
        {"ref": REF, "inputs": inputs}, opener=opener)


# ---- helpers --------------------------------------------------------------------------------------------- #

def fmt(t: dt.datetime) -> str:
    local, zone = to_eastern(t)
    return f"{local:%Y-%m-%d %H:%M} {zone}"


def fmt_span(span: dt.timedelta) -> str:
    minutes = int(span.total_seconds() // 60)
    return f"{minutes // 60} h {minutes % 60:02d} min"


def parse_now(text: str | None) -> dt.datetime | None:
    """A pretend time: Eastern without an offset, as given with one; None when empty."""
    if not text:
        return None
    t = dt.datetime.fromisoformat(text)
    return from_eastern(t) if t.tzinfo is None else t.astimezone(dt.timezone.utc)


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def load(fetch: bool):
    if fetch:
        import databranch
        databranch.fetch(remote="origin")
    return gameday.load_inputs(gameday.UPCOMING, gameday.SERIES)


def output(**values) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            for k, v in values.items():
                f.write(f"{k}={v}\n")


def summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)


def describe(out: Outcome, dry: bool) -> str:
    games = ", ".join(f"{k} {d}" for k, d in sorted(out.watched)) or "none"
    times = ", ".join(fmt(t)[11:16] for t in out.dispatches) or "none"
    verb = "Would have started" if dry else "Started"
    return (f"## Game-day watcher{' (dry run)' if dry else ''}\n\n"
            f"- Games watched: {games}\n- {verb} {len(out.dispatches)} update(s): {times}\n"
            f"- Final updates for: {', '.join(f'{k} {d}' for k, d in sorted(out.finalized)) or 'none'}\n"
            f"- {'Would hand over' if dry else 'Handed over'} to a fresh run: {'yes' if out.relayed else 'no'}\n"
            f"- Stopped: {out.stopped}\n")


# ---- commands ------------------------------------------------------------------------------------------- #

def test_dispatch(repo: str, token: str) -> int:
    """Start one game-day update on main and stop: proof that GITHUB_TOKEN may dispatch, on any day."""
    try:
        dispatch(repo, token, UPDATE_WORKFLOW, {"gameday": "true"})
    except Exception as exc:  # noqa: BLE001 - this is the thing being tested: say so plainly, and fail
        text = (f"## Game-day watcher (test dispatch)\n\n- FAILED to start {UPDATE_WORKFLOW} on {REF} "
                f"with gameday=true: {exc}\n")
        print(text)
        summary(text)
        return 1
    text = (f"## Game-day watcher (test dispatch)\n\n- Started {UPDATE_WORKFLOW} on {REF} with gameday=true. "
            "It should appear under \"Update data and deploy site\" within a minute.\n")
    print(text)
    summary(text)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("gate", "watch", "running", "handoff"))
    ap.add_argument("--dry-run", action="store_true", help="dispatch nothing; simulate the clock")
    ap.add_argument("--now", default="", help="pretend start time for a dry run (Eastern without an offset)")
    ap.add_argument("--relay", action="store_true", help="this run was started by a watcher handing over")
    ap.add_argument("--no-fetch", action="store_true", help="use data/ as it is (local checks)")
    ap.add_argument("--watched", default="", help="for a relay: the games the run before watched and still owes a "
                                                  "final update (sport:YYYY-MM-DD, comma-separated)")
    ap.add_argument("--test-dispatch", action="store_true",
                    help="start one game-day update now, whatever the day, and stop (proves the dispatch works)")
    args = ap.parse_args(argv)
    repo, token = os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GH_TOKEN", "")
    if args.test_dispatch and args.dry_run:
        print("test_dispatch and dry_run cannot be combined: a test dispatch exists to really start a run",
              file=sys.stderr)
        return 2
    if args.test_dispatch and args.now:
        print("test_dispatch takes no pretend time", file=sys.stderr)
        return 2
    try:
        handed_over = decode_watched(args.watched) if args.relay else set()
    except ValueError:
        print(f"--watched must be sport:YYYY-MM-DD, comma-separated: {args.watched!r}", file=sys.stderr)
        return 2
    pretend = parse_now(args.now)
    if pretend and not args.dry_run:
        print("--now is for dry runs only", file=sys.stderr)
        return 2
    if pretend:
        print(f"Pretend start time: {args.now} read as {fmt(pretend)}")
    if args.command == "running":
        try:
            busy = bool(active_watchers(repo, token))
        except Exception as exc:  # noqa: BLE001 - when GitHub cannot say, the 30-minute run goes ahead
            print(f"could not list watcher runs ({exc}); assuming none is running")
            busy = False
        print(f"running={'true' if busy else 'false'}")
        output(running="true" if busy else "false")
        return 0

    if args.test_dispatch and args.command == "gate":
        print("watch=true: test dispatch (no game check): the watch job starts one game-day update and stops")
        output(watch="true")
        return 0
    if args.test_dispatch and args.command == "watch":
        return test_dispatch(repo, token)

    upcoming, sports = load(not args.no_fetch)
    now = pretend or now_utc()

    if args.command == "handoff":
        watch, why = should_watch(now, upcoming, sports)
        if not watch:
            print(f"No hand-off: {why}")
            return 0
        try:
            busy = bool(active_watchers(repo, token))
        except Exception as exc:  # noqa: BLE001 - start it anyway: the watcher's own gate stands aside if needed
            print(f"could not list watcher runs ({exc}); starting the watcher anyway")
            busy = False
        if busy:
            print(f"No hand-off: a watcher is already running ({why})")
            return 0
        dispatch(repo, token, WATCHER_WORKFLOW, {})
        print(f"Started the game-day watcher: {why}")
        return 0

    if args.command == "gate":
        watch, why = should_watch(now, upcoming, sports, handed_over)
        if watch and not args.relay and not args.dry_run:
            try:
                runs = active_watchers(repo, token)
            except Exception as exc:  # noqa: BLE001 - the watch job's concurrency group still keeps one at a time
                print(f"could not list watcher runs ({exc}); going ahead")
                runs = []
            older = older_watcher(int(os.environ.get("GITHUB_RUN_ID", "0") or 0), runs)
            if older:
                watch, why = False, f"another watcher run is already in progress ({older.get('html_url', older['id'])})"
        print(f"{fmt(now)}  watch={'true' if watch else 'false'}: {why}")
        output(watch="true" if watch else "false")
        return 0

    # watch
    if args.dry_run:
        sim = {"now": now}
        clock = lambda: sim["now"]  # noqa: E731

        def sleep(seconds):
            sim["now"] += dt.timedelta(seconds=seconds)

        def update():
            print(f"  DRY RUN: would start {UPDATE_WORKFLOW} on {REF} with gameday=true")

        def relay(owed):
            print(f"  DRY RUN: would start a fresh {WATCHER_WORKFLOW} run marked as a relay"
                  + (f", handing over {encode_watched(owed)}" if owed else ""))
    else:
        clock, sleep = now_utc, time.sleep

        def update():
            dispatch(repo, token, UPDATE_WORKFLOW, {"gameday": "true"})

        def relay(owed):
            dispatch(repo, token, WATCHER_WORKFLOW, {"relay": "true", "watched": encode_watched(owed)})

    out = run(clock, sleep, lambda: load(not args.no_fetch), update, relay, handed_over=handed_over)
    text = describe(out, args.dry_run)
    print(text)
    summary(text)
    return 1 if out.failed else 0


if __name__ == "__main__":
    sys.exit(main())
