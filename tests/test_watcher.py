"""The game-day watcher (scraper/watcher.py, .github/workflows/gameday-watcher.yml) and the run scopes of the update
workflow (gameday.plan): what each kind of run reads, when the watcher waits, dispatches, hands over and stops,
and how the workflows are wired. Fake clock, fake GitHub API, fixtures only."""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gameday  # noqa: E402
import watcher  # noqa: E402
from test_render import game  # noqa: E402
from timeutil import from_eastern  # noqa: E402

FLOWS = ROOT / ".github" / "workflows"
WATCHER_FLOW = yaml.safe_load((FLOWS / "gameday-watcher.yml").read_text(encoding="utf-8"))
WATCHER_TEXT = (FLOWS / "gameday-watcher.yml").read_text(encoding="utf-8")
UPDATE_FLOW = yaml.safe_load((FLOWS / "update-and-deploy.yml").read_text(encoding="utf-8"))
needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash on PATH")


def et(text):
    return from_eastern(dt.datetime.fromisoformat(text))


def sched(date="2026-11-14", time="1:07 PM", status="scheduled", doubleheader=False):
    return {"date": date, "time": time, "time_note": None, "location": "neutral", "site": "Muncie, Ind.",
            "status": status, "doubleheader": doubleheader, "source": "both"}


def world(games=(sched(),), history=(), key="football", name="Football"):
    upcoming = {key: {"games": list(games)}}
    sports = {key: {"name": name, "games": [game("2025-11-15", "W", 41, 20), *history]}}
    return upcoming, sports


def result(date="2026-11-14"):
    return game(date, "W", 28, 21, "neutral", "Muncie, Ind.")


def decide(now, games=(sched(),), history=(), started=None, last=None, watched=()):
    up, sports = world(games, history)
    return watcher.decide(et(now), et(started or now), et(last) if last else None, set(watched), up, sports)


# ---- the gate's decision: watch or not ------------------------------------------------------------------ #

def watch(now, games=(sched(),), history=()):
    up, sports = world(games, history)
    return watcher.should_watch(et(now), up, sports)[0]


def test_no_game_today_means_no_watch():
    assert watch("2026-11-13 12:00") is False
    assert watch("2026-11-14 12:00", games=()) is False


def test_any_game_later_today_is_watched_even_hours_ahead():
    assert watch("2026-11-14 05:30") is True                  # the late-starting 11:47 PM daily run can hand off
    assert watch("2026-11-14 12:30") is True


def test_an_open_window_is_watched_and_a_closed_one_is_not():
    assert watch("2026-11-14 14:00") is True
    assert watch("2026-11-14 16:45", history=[result()]) is False                   # the result is in
    assert watch("2026-11-15 03:00") is False                                        # 3 AM the next day


def test_a_postponed_game_is_never_watched():
    assert watch("2026-11-14 12:30", games=[sched(status="postponed")]) is False
    assert watch("2026-11-14 14:00", games=[sched(status="postponed")]) is False


# ---- one pass of the loop ------------------------------------------------------------------------------- #

def test_before_the_window_it_waits_until_kickoff_rechecking_every_15_minutes():
    d = decide("2026-11-14 12:30")
    assert d.action == "wait" and d.until == et("2026-11-14 12:45")
    d = decide("2026-11-14 13:00")
    assert d.action == "wait" and d.until == et("2026-11-14 13:07")


def test_a_game_with_no_start_time_opens_at_noon():
    d = decide("2026-11-14 11:50", games=[sched(time=None)])
    assert d.action == "wait" and d.until == et("2026-11-14 12:00")
    assert decide("2026-11-14 12:00", games=[sched(time=None)]).action == "dispatch"


def test_inside_the_window_it_dispatches_at_most_every_15_minutes():
    d = decide("2026-11-14 13:07")
    assert d.action == "dispatch" and d.until == et("2026-11-14 13:22")
    d = decide("2026-11-14 13:15", last="2026-11-14 13:07")
    assert d.action == "wait" and d.until == et("2026-11-14 13:22")
    assert decide("2026-11-14 13:22", last="2026-11-14 13:07").action == "dispatch"


def test_a_posted_result_closes_the_window_with_one_final_dispatch():
    d = decide("2026-11-14 16:52", history=[result()], last="2026-11-14 16:37", watched={("football", "2026-11-14")})
    assert d.action == "final" and "result is in" in d.why


def test_a_result_after_the_game_left_the_schedule_still_counts():
    d = decide("2026-11-14 16:52", games=(), history=[result()], watched={("football", "2026-11-14")})
    assert d.action == "final"


def test_3_am_closes_the_window_without_a_final_dispatch():
    d = decide("2026-11-15 03:00", started="2026-11-15 00:30", watched={("football", "2026-11-14")})
    assert d.action == "stop" and "3 AM" in d.why


def test_a_doubleheader_stays_open_until_both_results_are_in():
    two = [sched(time="1:00 PM", doubleheader=True), sched(time="4:00 PM", doubleheader=True)]
    assert decide("2026-11-14 15:30", games=two, history=[result()]).action == "dispatch"
    d = decide("2026-11-14 19:00", games=two, history=[result(), result()], watched={("football", "2026-11-14")})
    assert d.action == "final"


def test_after_about_five_hours_in_the_job_it_hands_over_while_the_window_is_open_or_ahead():
    assert decide("2026-11-14 17:52", started="2026-11-14 13:07").action == "relay"
    assert decide("2026-11-14 12:00", started="2026-11-14 07:15").action == "relay"       # still waiting for kickoff
    assert decide("2026-11-14 17:50", started="2026-11-14 13:07", last="2026-11-14 17:37").action == "wait"


def test_no_game_means_stop_at_once():
    assert decide("2026-11-13 12:00").action == "stop"
    assert decide("2026-11-14 12:30", games=[sched(status="postponed")]).action == "stop"


# ---- the whole loop, with a fake clock --------------------------------------------------------------------- #

UPDATE_MINUTES = 4     # a game-day update: setup, tests, about a minute of polite reading, render, data commit


def simulate(start, posted=None, games=(sched(),), relay_start=dt.timedelta(minutes=1)):
    """Run the watcher from `start` (Eastern), following each relay into a fresh run that starts `relay_start`
    later. The result is posted on the schools' pages at `posted`; the data branch has it UPDATE_MINUTES after the
    first update started at or after that. Returns (outcomes of each run, every dispatch time as HH:MM)."""
    clock = {"now": et(start)}
    dispatched = []

    def refresh():
        known = posted and any(d >= et(posted) and clock["now"] >= d + dt.timedelta(minutes=UPDATE_MINUTES)
                               for d in dispatched)
        return world(games, [result()] if known else [])

    def sleep(seconds):
        clock["now"] += dt.timedelta(seconds=seconds)

    runs = []
    while True:
        out = watcher.run(lambda: clock["now"], sleep, refresh, lambda: dispatched.append(clock["now"]),
                          lambda: None, log=lambda line: None)
        runs.append(out)
        if not out.relayed:
            return runs, [watcher.fmt(t)[11:16] for t in dispatched]
        clock["now"] += relay_start


def test_nov_14_started_at_10_am_with_the_result_posted_at_4_30():
    """Wait until the 1:07 PM kickoff, update every 15 minutes, hand over to a relay at 2:52 PM (4 h 45 min in
    the job), keep updating; the 4:38 update reads the 4:30 result and commits it, so the 4:53 pass sees the window
    closed, starts one last update and stops."""
    runs, times = simulate("2026-11-14 10:00", posted="2026-11-14 16:30")
    assert len(runs) == 2 and runs[0].relayed and not runs[1].relayed
    assert times == ["13:07", "13:22", "13:37", "13:52", "14:07", "14:22", "14:37",
                     "14:53", "15:08", "15:23", "15:38", "15:53", "16:08", "16:23", "16:38", "16:53"]
    assert runs[1].stopped.startswith("the result is in")
    every = runs[0].dispatches + runs[1].dispatches
    assert all(b - a >= watcher.CADENCE for a, b in zip(every, every[1:]))    # never more than one every 15 minutes


def test_a_watcher_started_early_hands_over_before_its_job_runs_out_and_the_relay_carries_on():
    runs, times = simulate("2026-11-14 05:30", posted="2026-11-14 16:30")
    assert runs[0].relayed and runs[0].dispatches == []              # 5:30 + 4:45 = 10:15, before kickoff
    assert times[0] == "13:07" and runs[-1].stopped.startswith("the result is in")


def test_a_result_that_never_arrives_keeps_updating_until_3_am_then_stops_without_a_final():
    runs, times = simulate("2026-11-14 13:00")
    assert times[-1] < "03:00" and times[0] == "13:07" and "3 AM" in runs[-1].stopped
    assert len(times) == 56                                          # the worst case for one game


def test_three_failed_dispatches_in_a_row_stop_the_watcher():
    clock = {"now": et("2026-11-14 13:07")}

    def boom():
        raise OSError("GitHub said no")
    out = watcher.run(lambda: clock["now"], lambda s: clock.__setitem__("now", clock["now"] + dt.timedelta(seconds=s)),
                      lambda: world(), boom, lambda: None, log=lambda line: None)
    assert out.stopped == "3 dispatches failed in a row" and out.dispatches == [] and out.failed


def _ticking(start):
    clock = {"now": et(start)}
    return clock, (lambda: clock["now"]), (lambda s: clock.__setitem__("now", clock["now"] + dt.timedelta(seconds=s)))


def test_a_failed_dispatch_is_tried_again_a_minute_later_and_the_cadence_counts_from_the_one_that_worked():
    clock, now, sleep = _ticking("2026-11-14 13:07")
    tries = []

    def flaky():
        tries.append(clock["now"])
        if len(tries) == 1:
            raise OSError("502 from GitHub")
    out = watcher.run(now, sleep, lambda: world(history=[result()] if clock["now"] >= et("2026-11-14 13:20") else []),
                      flaky, lambda: None, log=lambda line: None)
    assert [fmt_t(t) for t in tries] == ["13:07", "13:08", "13:23"] and not out.failed
    assert out.stopped.startswith("the result is in")


def test_a_failed_read_of_the_data_branch_is_tried_again_and_never_ends_the_watch_by_itself():
    clock, now, sleep = _ticking("2026-11-14 13:07")
    reads = []

    def refresh():
        reads.append(clock["now"])
        if len(reads) == 2:
            raise OSError("timed out")
        return world(history=[result()] if clock["now"] >= et("2026-11-14 13:30") else [])
    out = watcher.run(now, sleep, refresh, lambda: None, lambda: None, log=lambda line: None)
    assert fmt_t(reads[2]) == "13:23" and not out.failed and out.stopped.startswith("the result is in")


def test_three_failed_reads_in_a_row_stop_the_watcher_and_turn_its_run_red():
    clock, now, sleep = _ticking("2026-11-14 13:07")

    def refresh():
        raise OSError("timed out")
    dispatched = []
    out = watcher.run(now, sleep, refresh, lambda: dispatched.append(1), lambda: None, log=lambda line: None)
    assert out.failed and "could not be read (3 in a row)" in out.stopped and dispatched == []


def fmt_t(t):
    return watcher.to_eastern(t)[0].strftime("%H:%M")


def test_each_pass_logs_the_eastern_time_the_state_and_the_action():
    clock = {"now": et("2026-11-14 13:00")}
    lines = []
    watcher.run(lambda: clock["now"], lambda s: clock.__setitem__("now", clock["now"] + dt.timedelta(seconds=s)),
                lambda: world(history=[result()] if clock["now"] >= et("2026-11-14 13:30") else []),
                lambda: None, lambda: None, log=lines.append)
    assert lines[0].startswith("2026-11-14 13:00 EST  before  wait")
    assert lines[1].startswith("2026-11-14 13:07 EST  open    dispatch")
    assert lines[-1].startswith("2026-11-14 13:37 EST") and "final" in lines[-1]


# ---- one watcher at a time ----------------------------------------------------------------------------------- #

class FakeGitHub:
    def __init__(self, runs=()):
        self.runs, self.calls = list(runs), []

    def __call__(self, req, timeout=None):
        self.calls.append((req.get_method(), req.full_url, json.loads(req.data) if req.data else None))
        if req.get_method() == "GET":
            state = re.search(r"status=(\w+)", req.full_url).group(1)
            body = {"workflow_runs": [r for r in self.runs if r["status"] == state]}
        else:
            body = None
        return _Resp(json.dumps(body).encode() if body is not None else b"")


class _Resp:
    def __init__(self, raw):
        self.raw = raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.raw


def test_the_newer_watcher_yields_to_an_older_one_and_dry_runs_are_ignored():
    gh = FakeGitHub([{"id": 100, "status": "in_progress", "display_title": "Game-day watcher"},
                     {"id": 90, "status": "in_progress", "display_title": "Game-day watcher (dry run)"},
                     {"id": 120, "status": "queued", "display_title": "Game-day watcher"}])
    runs = watcher.active_watchers("o/r", "t", opener=gh)
    assert [r["id"] for r in runs] == [100, 120]
    assert watcher.older_watcher(120, runs)["id"] == 100              # the newer one stands aside
    assert watcher.older_watcher(100, runs) is None                   # the older one keeps going


def test_dispatch_starts_the_update_workflow_on_main_with_the_game_day_input():
    gh = FakeGitHub()
    watcher.dispatch("o/r", "t", watcher.UPDATE_WORKFLOW, {"gameday": "true"}, opener=gh)
    assert gh.calls == [("POST", "https://api.github.com/repos/o/r/actions/workflows/update-and-deploy.yml/dispatches",
                         {"ref": "main", "inputs": {"gameday": "true"}})]


def write_data(tmp_path, games=(sched(),), history=()):
    up, sports = world(games, history)
    (tmp_path / "upcoming.json").write_text(json.dumps({"schema_version": 1, "sports": up}))
    (tmp_path / "series.json").write_text(json.dumps({"sports": sports}))
    return tmp_path


@pytest.fixture
def data(tmp_path, monkeypatch):
    write_data(tmp_path)
    monkeypatch.setattr(gameday, "UPCOMING", tmp_path / "upcoming.json")
    monkeypatch.setattr(gameday, "SERIES", tmp_path / "series.json")
    monkeypatch.setattr(watcher, "now_utc", lambda: et("2026-11-14 12:30"))
    return tmp_path


def test_the_gate_cli_stands_aside_for_an_older_watcher_unless_it_is_a_relay(data, monkeypatch, capsys):
    out = data / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_RUN_ID", "200")
    monkeypatch.setattr(watcher, "active_watchers", lambda repo, token: [{"id": 150, "html_url": "u150"}])
    assert watcher.main(["gate", "--no-fetch"]) == 0
    assert out.read_text() == "watch=false\n" and "another watcher run is already in progress" in capsys.readouterr().out
    out.unlink()
    assert watcher.main(["gate", "--no-fetch", "--relay"]) == 0
    assert out.read_text() == "watch=true\n"


def test_a_dry_run_reads_a_pretend_time_as_eastern_and_dispatches_nothing(data, monkeypatch, capsys):
    def no_api(*a, **k):
        raise AssertionError("a dry run called GitHub")
    monkeypatch.setattr(watcher, "api", no_api)
    assert watcher.main(["watch", "--dry-run", "--now", "2026-11-14T12:30", "--no-fetch"]) == 0
    text = capsys.readouterr().out
    assert "read as 2026-11-14 12:30 EST" in text
    assert "DRY RUN: would start update-and-deploy.yml on main with gameday=true" in text
    assert "2026-11-14 13:07 EST  open    dispatch" in text
    assert "DRY RUN: would start a fresh gameday-watcher.yml run marked as a relay" in text


def test_a_pretend_time_is_refused_outside_a_dry_run(data):
    assert watcher.main(["watch", "--now", "2026-11-14T12:30", "--no-fetch"]) == 2


def test_parse_now_reads_eastern_without_an_offset_and_as_given_with_one():
    assert watcher.parse_now("") is None
    assert watcher.parse_now("2026-11-14T12:30") == dt.datetime(2026, 11, 14, 17, 30, tzinfo=dt.timezone.utc)
    assert watcher.parse_now("2026-11-14T12:30:00+00:00") == dt.datetime(2026, 11, 14, 12, 30, tzinfo=dt.timezone.utc)


def test_the_hand_off_starts_the_watcher_only_on_a_game_day_without_one_running(data, monkeypatch):
    started = []
    monkeypatch.setattr(watcher, "dispatch", lambda repo, token, flow, inputs: started.append(flow))
    monkeypatch.setattr(watcher, "active_watchers", lambda repo, token: [{"id": 1}])
    watcher.main(["handoff", "--no-fetch"])
    assert started == []
    monkeypatch.setattr(watcher, "active_watchers", lambda repo, token: [])
    watcher.main(["handoff", "--no-fetch"])
    assert started == ["gameday-watcher.yml"]
    write_data(data, games=())
    started.clear()
    watcher.main(["handoff", "--no-fetch"])
    assert started == []


def test_the_watcher_imports_nothing_outside_the_standard_library():
    import ast
    names = set()
    for name in ("watcher", "politeness", "privacy", "databranch"):
        tree = ast.parse((ROOT / "scraper" / f"{name}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names.add(node.module.split(".")[0])
    assert names <= set(sys.stdlib_module_names) | {"gameday", "politeness", "privacy", "timeutil", "databranch",
                                                    "meetings"}, names


# ---- what each kind of update run reads (gameday.plan) ------------------------------------------------------ #

def plan(event, now="2026-11-14 14:00", schedule="", gameday_input=False, changed=(), running=False, **kw):
    up, sports = world(**kw)
    return gameday.plan(et(now), event, schedule, gameday_input, list(changed) if changed is not None else None,
                        running, up, sports)


def test_daily_and_hand_started_runs_read_every_sport():
    assert plan("schedule", schedule="19 10 * * *")[:2] == (True, "all")
    assert plan("schedule", schedule="47 3 * * *")[:2] == (True, "all")
    assert plan("workflow_dispatch")[:2] == (True, "all")


def test_a_game_day_run_reads_only_the_sport_playing_today():
    assert plan("schedule", schedule=gameday.FREQUENT_CRON)[:2] == (True, "football")
    assert plan("workflow_dispatch", gameday_input=True)[:2] == (True, "football")
    # the watcher's final update after the result is in still reads the sport that just played
    assert plan("workflow_dispatch", now="2026-11-14 16:52", gameday_input=True, history=[result()])[:2] == (True, "football")
    # and a game-day update on a day without a game reads nothing
    assert plan("workflow_dispatch", now="2026-11-20 14:00", gameday_input=True)[:2] == (True, "")


def test_the_30_minute_schedule_does_nothing_outside_a_window_and_stands_aside_for_the_watcher():
    assert plan("schedule", now="2026-11-14 10:00", schedule=gameday.FREQUENT_CRON)[0] is False
    run, scope, why = plan("schedule", schedule=gameday.FREQUENT_CRON, running=True)
    assert run is False and "watcher is running" in why


def test_a_push_reads_nothing_unless_it_changed_the_scraper_or_the_sources():
    assert plan("push", changed=["README.md", "scraper/render.py", "data/overrides.json"])[:2] == (True, "")
    for path in gameday.SCRAPE_ON_PUSH:
        assert plan("push", changed=["README.md", path])[:2] == (True, "all")
    assert plan("push", changed=None)[:2] == (True, "all")                  # could not tell: read everything


def test_game_day_scope_reads_only_open_windows_and_never_a_game_that_has_not_started():
    up = {"football": {"games": [sched(date="2026-11-14", time="7:00 PM")]},
          "baseball": {"games": [sched(date="2026-11-15", time="1:00 PM")]}}
    sports = {"football": {"name": "Football", "games": []}, "baseball": {"name": "Baseball", "games": []}}
    assert gameday.gameday_sports(et("2026-11-15 01:00"), up, sports) == ["football"]   # baseball not started
    assert gameday.gameday_sports(et("2026-11-15 03:00"), up, sports) == []             # nothing begun today
    assert gameday.gameday_sports(et("2026-11-15 13:30"), up, sports) == ["baseball"]


def two_sport_day(start, football_posted, basketball_posted):
    """Football at 1:07 PM and basketball at 7:00 PM on Nov 14, watched from `start` (Eastern), following each
    relay into a fresh run a minute later. A result reaches the data branch UPDATE_MINUTES after the first update
    that started once it was posted. Returns (outcome of the last run, [(HH:MM, scope) for every dispatch])."""
    clock = {"now": et(start)}
    dispatched = []
    games = {"football": ("1:07 PM", football_posted), "mens-basketball": ("7:00 PM", basketball_posted)}

    def data():
        up = {k: {"games": [sched(time=t)]} for k, (t, _) in games.items()}
        sports = {}
        for k, (_, posted) in games.items():
            known = posted and any(d >= et(posted) and clock["now"] >= d + dt.timedelta(minutes=UPDATE_MINUTES)
                                   for d, _ in dispatched)
            sports[k] = {"name": k, "games": [result()] if known else []}
        return up, sports

    def dispatch():
        dispatched.append((clock["now"], gameday.gameday_sports(clock["now"], *data())))

    def sleep(sec):
        clock["now"] += dt.timedelta(seconds=sec)
    while True:
        out = watcher.run(lambda: clock["now"], sleep, data, dispatch, lambda: None, log=lambda line: None)
        if not out.relayed:
            return out, [(fmt_t(t), scope) for t, scope in dispatched]
        sleep(60)


def test_a_day_with_two_sports_reads_each_only_in_its_window_and_ends_with_one_final_update():
    out, runs = two_sport_day("2026-11-14 12:00", "2026-11-14 16:30", "2026-11-14 21:00")
    football = [t for t, scope in runs if scope == ["football"]]
    basketball = [t for t, scope in runs if scope == ["mens-basketball"]]
    assert football[0] == "13:07" and basketball[0] == "19:00"
    assert not [t for t, _ in runs if football[-1] < t < "19:00"]      # waits between the games, reads nothing
    assert runs[-1][1] == ["football", "mens-basketball"]               # the final update reads what was played
    assert len(runs) == len(football) + len(basketball) + 1
    assert out.stopped.startswith("the result is in")


def test_one_result_in_and_the_other_never_stops_at_3_am_without_a_final_update():
    out, runs = two_sport_day("2026-11-14 12:00", "2026-11-14 16:30", None)
    assert "3 AM" in out.stopped and "every result" in out.stopped
    assert [scope for _, scope in runs if scope != ["football"]] == [["mens-basketball"]] * (len(runs) - len(
        [1 for _, scope in runs if scope == ["football"]]))


def test_the_gate_cli_writes_the_scope_and_reads_the_changed_files(tmp_path, monkeypatch, capsys):
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    changed = tmp_path / "changed.txt"
    changed.write_text("README.md\nscraper/render.py\n")
    assert gameday.main(["gate", "--event", "push", "--changed", str(changed)]) == 0
    assert out.read_text() == "run=true\nscope=\n"
    out.unlink()
    changed.write_text("scraper/scrape.py\n")
    gameday.main(["gate", "--event", "push", "--changed", str(changed)])
    assert out.read_text() == "run=true\nscope=all\n"
    out.unlink()
    gameday.main(["gate", "--event", "push", "--changed", str(tmp_path / "missing.txt")])
    assert out.read_text() == "run=true\nscope=all\n"


# ---- the workflow files ------------------------------------------------------------------------------------ #

def test_the_watcher_starts_on_a_jittered_schedule_by_hand_or_by_dispatch_never_from_a_pull_request():
    on = WATCHER_FLOW[True]
    assert set(on) == {"schedule", "workflow_dispatch"}
    cron = on["schedule"][0]["cron"]
    minutes = [int(m) for m in cron.split()[0].split(",")]
    assert len(minutes) == 6 and all(b - a == 10 for a, b in zip(minutes, minutes[1:]))       # every 10 minutes
    assert 0 not in minutes and 30 not in minutes and cron != gameday.FREQUENT_CRON
    inputs = on["workflow_dispatch"]["inputs"]
    assert inputs["dry_run"]["type"] == "boolean" and inputs["dry_run"]["default"] is False
    assert inputs["relay"]["type"] == "boolean" and inputs["relay"]["default"] is False
    assert inputs["pretend_now"]["type"] == "string" and inputs["pretend_now"]["default"] == ""
    assert "pull_request" not in WATCHER_TEXT and "secrets." not in WATCHER_TEXT


def test_only_the_watch_job_and_the_hand_off_can_start_runs():
    assert WATCHER_FLOW["permissions"] == {"contents": "read"}
    assert WATCHER_FLOW["jobs"]["gate"]["permissions"] == {"contents": "read", "actions": "read"}
    assert WATCHER_FLOW["jobs"]["watch"]["permissions"] == {"contents": "read", "actions": "write"}
    writers = []
    for flow in FLOWS.glob("*.yml"):
        for name, job in yaml.safe_load(flow.read_text(encoding="utf-8"))["jobs"].items():
            if (job.get("permissions") or {}).get("actions") == "write":
                writers.append((flow.name, name))
    assert sorted(writers) == [("gameday-watcher.yml", "watch"), ("update-and-deploy.yml", "watcher-handoff")]


def test_the_watcher_never_scrapes_commits_or_deploys():
    for word in ("scrape.py", "render.py", "databranch.py push", "deploy-pages", "upload-pages-artifact",
                 "git push", "pages: write", "contents: write", "setup-python", "pip install"):
        assert word not in WATCHER_TEXT, word
    source = (ROOT / "scraper" / "watcher.py").read_text(encoding="utf-8")
    assert "import scrape" not in source and "requests" not in source and "depauwtigers" not in source


def test_one_watch_job_at_a_time_with_timeouts_under_githubs_limit():
    jobs = WATCHER_FLOW["jobs"]
    assert jobs["gate"]["timeout-minutes"] <= 5 and "concurrency" not in jobs["gate"]   # gates never queue
    assert jobs["watch"]["needs"] == "gate" and jobs["watch"]["if"] == "${{ needs.gate.outputs.watch == 'true' }}"
    timeout = jobs["watch"]["timeout-minutes"]
    assert watcher.RELAY_AFTER + watcher.CADENCE < dt.timedelta(minutes=timeout) <= dt.timedelta(hours=6)
    group = jobs["watch"]["concurrency"]
    assert group["cancel-in-progress"] is False and "'gameday-watcher'" in group["group"]
    assert "dry" in group["group"]                                   # a dry run never queues behind a real watcher
    assert watcher.DRY_TITLE in WATCHER_FLOW["run-name"]              # how the run checks recognise a dry run
    assert "--now \"$PRETEND_NOW\"" in WATCHER_TEXT and "PRETEND_NOW: ${{ inputs.pretend_now }}" in WATCHER_TEXT


def test_the_update_workflow_has_a_game_day_input_and_reads_by_scope():
    on = UPDATE_FLOW[True]
    option = on["workflow_dispatch"]["inputs"]["gameday"]
    assert option["type"] == "boolean" and option["default"] is False and "watcher" in option["description"]
    steps = {s.get("name"): s for s in UPDATE_FLOW["jobs"]["build"]["steps"]}
    scrape = steps["Scrape"]
    assert scrape["if"] == "${{ needs.gate.outputs.scope != '' }}"            # an ordinary push reads nothing
    assert "--only $SCOPE" in scrape["run"] and '[ "$SCOPE" != "all" ]' in scrape["run"]
    assert "--blocked-file" in scrape["run"]
    assert UPDATE_FLOW["jobs"]["gate"]["outputs"]["scope"] == "${{ steps.gate.outputs.scope }}"
    gate = next(s for s in UPDATE_FLOW["jobs"]["gate"]["steps"] if s.get("id") == "gate")
    assert gate["env"]["GAMEDAY"] == "${{ inputs.gameday }}" and "--changed" in gate["run"]


def test_the_build_restores_the_last_check_time_and_saves_it_only_after_reading_pages():
    steps = UPDATE_FLOW["jobs"]["build"]["steps"]
    names = [s.get("name") for s in steps]
    restore, scrape, save, render = (names.index(n) for n in ("Restore the last check time", "Scrape",
                                                              "Save the check time", "Render site"))
    assert restore < scrape < save < render
    assert steps[restore]["uses"].startswith("actions/cache/restore@") and steps[restore]["with"]["path"] == ".checked-at"
    assert steps[restore]["with"]["restore-keys"] == "checked-at-" and steps[restore]["continue-on-error"] is True
    assert steps[save]["uses"].startswith("actions/cache/save@") and steps[save]["continue-on-error"] is True
    assert "steps.scrape.outcome" in steps[save]["if"]                       # a run that read nothing saves nothing
    assert UPDATE_FLOW["jobs"]["build"]["timeout-minutes"] >= 20             # a full polite scrape is about 7 minutes


def test_only_the_daily_runs_hand_off_to_the_watcher():
    job = UPDATE_FLOW["jobs"]["watcher-handoff"]
    assert "'19 10 * * *'" in job["if"] and "'47 3 * * *'" in job["if"] and gameday.FREQUENT_CRON not in job["if"]
    assert "refs/heads/main" in job["if"] and "needs" not in job and job["timeout-minutes"] <= 5
    assert any("watcher.py handoff" in s.get("run", "") for s in job["steps"])


@needs_bash
def test_the_report_names_pages_robots_txt_disallows():
    report = UPDATE_FLOW["jobs"]["report"]["steps"][0]
    result = subprocess.run(["bash", "-e", "-c", report["run"]],
                            env={"PATH": os.environ["PATH"], "SCRAPE": "failed", "VALIDATION": "ok",
                                 "VALIDATE_JOB": "success", "COMMIT": "ok", "BLOCKED": "https://depauwtigers.com/x "},
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert "robots.txt now disallows pages the scraper needs" in result.stdout and "https://depauwtigers.com/x" in result.stdout


def test_the_readme_and_agents_describe_the_watcher_the_politeness_rules_and_the_run_scopes():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    watcher_section = readme.split("### The game-day watcher", 1)[1].split("\n## ", 1)[0]
    for phrase in ("every 15 minutes", "Relay.", "dry_run", "pretend_now", "hand start", "One at a time.",
                   "never does", "workflow_dispatch"):
        assert phrase in watcher_section, phrase
    for phrase in ("**Politeness**", "robots.txt", "Crawl-delay", "gameday.SCRAPE_ON_PUSH", "`gameday-watcher.yml`",
                   "What each run reads", "Actions cache", "change all sixteen `runs-on:` lines"):
        assert phrase in readme, phrase
    assert "twice a day: a history page" not in readme and "spaced a few seconds apart" not in readme
    for phrase in ("scraper/politeness.py", "Crawl-delay", "Never increase request volume", "gameday-watcher.yml",
                   "never fetches the schools' sites, commits or deploys", "actions: write"):
        assert phrase in agents, phrase
