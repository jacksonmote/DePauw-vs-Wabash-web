"""Game-day mode: the gate that decides whether a frequent run does anything, the banner for each phase, the
schedule carry-over that keeps a just-finished game visible until its result is in, and the workflow wiring."""
import datetime as dt
import hashlib
import json
import os
import re
import shlex
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
import images  # noqa: E402
import render  # noqa: E402
import scrape  # noqa: E402
import upcoming  # noqa: E402
from test_render import build, game, lacrosse, sport, text_of  # noqa: E402
from timeutil import from_eastern  # noqa: E402

FREQ = gameday.FREQUENT_CRON
WORKFLOW = (ROOT / ".github" / "workflows" / "update-and-deploy.yml").read_text(encoding="utf-8")


def et(text):
    """An Eastern wall-clock time ('2026-11-14 13:07') as an aware UTC time."""
    return from_eastern(dt.datetime.fromisoformat(text))


def sched(date="2026-11-14", time="1:07 PM", status="scheduled", location="neutral",
          site="Scheumann Stadium, Muncie, IN", source="both"):
    return {"date": date, "time": time, "time_note": None, "location": location, "site": site,
            "status": status, "doubleheader": False, "source": source}


def played(date="2026-11-14", result="W", d=41, w=20, **kw):
    return game(date, result, d, w, kw.pop("location", "neutral"), kw.pop("site", "Scheumann Stadium, Muncie, IN"), **kw)


def football(*extra):
    return sport("football", "Football", [
        game("2024-11-16", "W", 21, 14), game("2025-11-15", "W", 41, 20), *extra])


def inputs(games=(), history=(), key="football", name="Football"):
    up = {key: {"games": list(games)}}
    sports = {key: {"name": name, "games": [game("2025-11-15", "W", 41, 20), *history]}}
    return up, sports


def gate(now, games=(), history=(), schedule=FREQ):
    up, sports = inputs(games, history)
    return gameday.gate(et(now), schedule, up, sports)


# ---- the gate ------------------------------------------------------------ #

def test_no_game_scheduled_means_the_frequent_run_does_nothing():
    run, why = gate("2026-11-14 14:00")
    assert run is False and "no game-day window" in why


def test_before_the_window_opens_nothing_runs():
    assert gate("2026-11-14 13:06", [sched()])[0] is False        # a minute before the 1:07 PM start
    assert gate("2026-11-13 20:00", [sched()])[0] is False        # the night before
    assert gate("2026-11-14 07:07", [sched()])[0] is False        # game day morning: the daily run covers it


def test_inside_the_window_the_run_does_the_real_work():
    assert gate("2026-11-14 13:07", [sched()])[0] is True         # exactly at the start
    assert gate("2026-11-14 16:37", [sched()])[0] is True
    assert gate("2026-11-14 23:37", [sched()])[0] is True
    run, why = gate("2026-11-14 14:07", [sched()])
    assert run and "Football 2026-11-14: 0 of 1 result(s) in" in why


def test_once_the_result_is_on_a_history_page_the_run_stops():
    assert gate("2026-11-14 16:37", [sched()], [played("2026-11-14")])[0] is False


def test_the_window_closes_at_3_am_eastern_the_next_day():
    assert gate("2026-11-15 02:59", [sched()])[0] is True          # the carried game, result still missing
    assert gate("2026-11-15 03:00", [sched()])[0] is False
    assert gate("2026-11-15 12:00", [sched()])[0] is False


def test_a_game_with_no_time_opens_at_noon_eastern():
    assert gate("2026-11-14 11:59", [sched(time=None)])[0] is False
    assert gate("2026-11-14 12:00", [sched(time=None)])[0] is True


def test_a_doubleheader_stays_open_until_both_results_are_in():
    dh = [sched("2027-04-28", "12:00 PM", site="Goodrich Ballpark, Crawfordsville, IN"),
          sched("2027-04-28", "3:30 PM", site="Goodrich Ballpark, Crawfordsville, IN")]
    assert gate("2027-04-28 11:59", dh)[0] is False
    assert gate("2027-04-28 12:07", dh)[0] is True                  # opens at the first game's start
    assert gate("2027-04-28 15:37", dh, [played("2027-04-28", "W", 5, 3)])[0] is True       # one of two
    assert gate("2027-04-28 17:37", dh, [played("2027-04-28", "W", 5, 3), played("2027-04-28", "L", 2, 4)])[0] is False


def test_a_postponed_game_opens_no_window():
    assert gate("2026-11-14 14:00", [sched(status="postponed")])[0] is False


def test_only_the_frequent_schedule_can_be_skipped():
    for schedule in ("", "19 10 * * *", "47 3 * * *"):               # push / dispatch, and the two daily crons
        run, why = gate("2026-11-14 04:00", [], schedule=schedule)
        assert run is True and "always runs" in why


def test_a_game_with_no_usable_date_in_the_history_never_counts_as_the_result():
    undated = {**played("2026-11-14"), "date_known": False}
    assert gate("2026-11-14 16:37", [sched()], [undated])[0] is True


def test_the_cli_reads_the_committed_files_and_writes_only_the_output_file(tmp_path, monkeypatch, capsys):
    up, series = tmp_path / "upcoming.json", tmp_path / "series.json"
    up.write_text(json.dumps({"schema_version": 1, "sports": {"football": {"schedule_url": "x", "games": [sched()]}}}))
    series.write_text(json.dumps({"sports": {"football": {"name": "Football", "games": [game("2025-11-15", "W", 41, 20)]}}}))
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()}
    out = tmp_path.parent / f"{tmp_path.name}-output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))

    code = gameday.main(["gate", "--schedule", FREQ, "--now", "2026-11-12T12:00:00-05:00",
                         "--upcoming", str(up), "--series", str(series)])
    assert code == 0 and out.read_text() == "run=false\n"
    assert "run=false: no game-day window is open" in capsys.readouterr().out
    assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.iterdir()} == before   # no commits to make

    out.unlink()
    gameday.main(["gate", "--schedule", FREQ, "--now", "2026-11-14T14:00:00-05:00",
                  "--upcoming", str(up), "--series", str(series)])
    assert out.read_text() == "run=true\n"


def test_the_gate_stops_and_says_so_when_it_cannot_read_the_data(tmp_path, monkeypatch, capsys):
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    code = gameday.main(["gate", "--schedule", FREQ, "--now", "2026-11-14T14:00:00-05:00",
                         "--series", str(tmp_path / "missing.json"),
                         "--upcoming", str(tmp_path / "missing2.json")])
    assert code == 1 and out.read_text() == "run=false\n" and "could not read" in capsys.readouterr().err


def test_the_gate_imports_nothing_outside_the_standard_library():
    import ast
    modules = set()
    for name in ("gameday", "meetings", "timeutil"):
        tree = ast.parse((ROOT / "scraper" / f"{name}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules.add(node.module.split(".")[0])
    assert modules <= set(sys.stdlib_module_names) | {"meetings", "timeutil"}, modules


# ---- the banner ---------------------------------------------------------- #

def banner(tmp_path, now, games, sports=None, text=True):
    sports = sports or {"football": football()}
    up = tmp_path / "upcoming.json"
    up.write_text(json.dumps({"schema_version": 1, "sports": {
        k: {"schedule_url": "https://depauwtigers.com/sports/x/schedule", "games": g} for k, g in games.items()}}))
    out, html = build(tmp_path, sports, now=et(now), upcoming_path=up)
    m = re.search(r'<section class="game-day".*?</section>', html, re.S)
    return (text_of(m.group(0)).strip() if text else m.group(0)) if m else None, html, out


def test_before_the_start_it_says_game_day_with_the_time_and_place(tmp_path):
    t, html, _ = banner(tmp_path, "2026-11-14 07:30", {"football": [sched()]})
    assert t == "Game day: Football vs. Wabash today, 1:07 PM · Scheumann Stadium, Muncie, Ind."
    assert html.index('class="game-day"') < html.index('id="overall-h"') < html.index('id="next-meeting"')   # the banner stays first


def test_after_the_scheduled_start_it_says_underway_until_the_result_is_in(tmp_path):
    t, _, _ = banner(tmp_path, "2026-11-14 13:07", {"football": [sched()]})
    assert t == ("Football vs. Wabash is underway or just finished. "
                 "The result will appear here soon after it’s posted.")
    t, _, _ = banner(tmp_path, "2026-11-14 22:00", {"football": [sched()]})
    assert t.startswith("Football vs. Wabash is underway or just finished.")


def test_once_the_result_is_in_it_shows_the_final_winner_first_and_links_to_the_card(tmp_path):
    sports = {"football": football(played("2026-11-14", "W", 41, 20))}
    t, _, _ = banner(tmp_path, "2026-11-14 17:00", {"football": [sched()]}, sports)
    assert t == "Final: DePauw 41, Wabash 20 · DePauw holds the Monon Bell."
    sports = {"football": football(played("2026-11-14", "L", 20, 27))}
    t, html, _ = banner(tmp_path, "2026-11-14 17:00", {"football": [sched()]}, sports)
    assert t == "Final: Wabash 27, DePauw 20 · Wabash holds the Monon Bell."
    assert '<a href="#football">Final: Wabash 27, DePauw 20</a>' in html and 'id="football"' in html


def test_the_final_stays_up_after_the_game_drops_off_the_schedule(tmp_path):
    sports = {"football": football(played("2026-11-14", "W", 41, 20))}
    t, _, _ = banner(tmp_path, "2026-11-14 23:50", {"football": []}, sports)      # schedule page already dropped it
    assert t.startswith("Final: DePauw 41, Wabash 20")


def test_a_tie_is_a_final_too_and_the_bell_stays_where_it_was(tmp_path):
    sports = {"football": football(played("2026-11-14", "T", 7, 7))}
    t, _, _ = banner(tmp_path, "2026-11-14 17:00", {"football": [sched()]}, sports)
    assert t == "Final: Tie 7–7 · DePauw holds the Monon Bell."


def test_only_football_mentions_the_bell(tmp_path):
    sports = {"mens-lacrosse": sport("mens-lacrosse", "Men's Lacrosse", lacrosse()["games"] + [
        played("2026-04-10", "W", 12, 4, location="home", site="Greencastle, IN")])}
    t, _, _ = banner(tmp_path, "2026-04-10 20:00", {"mens-lacrosse": [sched("2026-04-10", "1:00 PM", location="home", site="Greencastle, IN")]}, sports)
    assert t == "Final: DePauw 12, Wabash 4" and "Bell" not in t


def test_two_sports_on_one_day_share_one_banner_each_in_its_own_phase(tmp_path):
    sports = {"football": football(played("2026-11-14", "W", 41, 20)), "mens-lacrosse": lacrosse()}
    games = {"football": [sched()], "mens-lacrosse": [sched(time="3:00 PM", location="home", site="Greencastle, IN")]}
    t, html, _ = banner(tmp_path, "2026-11-14 14:00", games, sports)
    assert html.count('class="game-day"') == 1
    assert t == ("Final, Football: DePauw 41, Wabash 20 · DePauw holds the Monon Bell. "
                 "Game day: Men’s Lacrosse vs. Wabash today, 3:00 PM · Greencastle, Ind.")


def test_a_doubleheader_banner_lists_every_game(tmp_path):
    bs = sport("baseball", "Baseball", [game("2026-04-28", "L", 3, 15)])
    dh = [sched("2027-04-28", "12:00 PM", location="away", site="Goodrich Ballpark, Crawfordsville, IN"),
          sched("2027-04-28", None, location="away", site="Goodrich Ballpark, Crawfordsville, IN")]
    t, _, _ = banner(tmp_path, "2027-04-28 08:00", {"baseball": dh}, {"baseball": bs})
    assert t == ("Game day: Baseball vs. Wabash today (doubleheader), 12:00 PM and TBA · "
                 "Goodrich Ballpark, Crawfordsville, Ind.")
    both = [*bs["games"], played("2027-04-28", "W", 5, 3), played("2027-04-28", "L", 2, 4)]
    t, _, _ = banner(tmp_path, "2027-04-28 19:00", {"baseball": []}, {"baseball": sport("baseball", "Baseball", both)})
    assert t == "Final: DePauw 5, Wabash 3 and Wabash 4, DePauw 2"
    one = sport("baseball", "Baseball", both[:-1])
    t, _, _ = banner(tmp_path, "2027-04-28 15:00", {"baseball": dh}, {"baseball": one})
    assert t.startswith("Baseball vs. Wabash is underway")               # game 1 is in, game 2 is not


def test_a_game_with_no_time_says_so_and_counts_as_underway_from_noon(tmp_path):
    t, _, _ = banner(tmp_path, "2026-11-14 09:00", {"football": [sched(time=None)]})
    assert t == "Game day: Football vs. Wabash today, time TBA · Scheumann Stadium, Muncie, Ind."
    t, _, _ = banner(tmp_path, "2026-11-14 12:00", {"football": [sched(time=None)]})
    assert t.startswith("Football vs. Wabash is underway")


def test_no_banner_when_nothing_is_played_today_or_the_game_is_postponed(tmp_path):
    for now, games in (("2026-11-13 12:00", [sched()]), ("2026-11-14 12:00", [sched(status="postponed")]),
                       ("2026-11-14 12:00", [])):
        t, html, _ = banner(tmp_path, now, {"football": games})
        assert t is None and "game-day" not in html


def test_the_banner_is_gone_at_the_first_deploy_after_game_day(tmp_path):
    sports = {"football": football(played("2026-11-14", "W", 41, 20))}
    assert banner(tmp_path, "2026-11-14 23:47", {"football": []}, sports)[0].startswith("Final")   # the 11:47 PM run
    assert banner(tmp_path, "2026-11-15 06:19", {"football": []}, sports)[0] is None                # the 6:19 AM run
    assert banner(tmp_path, "2026-11-15 01:00", {"football": [sched()]})[0] is None   # after midnight, even if the window is open


def test_a_game_already_in_the_history_is_not_also_shown_as_the_next_meeting(tmp_path):
    sports = {"football": football(played("2026-11-14", "W", 41, 20))}
    _, html, _ = banner(tmp_path, "2026-11-14 17:00", {"football": [sched()]}, sports)
    assert 'id="next-meeting"' not in html and "Next meeting" not in text_of(html)


def test_the_banner_is_static_html_with_no_script_or_inline_style(tmp_path):
    _, html, _ = banner(tmp_path, "2026-11-14 17:00", {"football": [sched()]})
    assert html.count('<script src="easter-eggs.js" defer></script>') == 1 and "style=" not in html


# ---- the share image ----------------------------------------------------- #

def test_the_share_image_gets_a_game_day_or_final_line_and_a_new_version(tmp_path):
    plain = images.og_image({"depauw_wins": 5, "wabash_wins": 3, "ties": 1}, "DePauw")
    day = images.og_image({"depauw_wins": 5, "wabash_wins": 3, "ties": 1}, "DePauw", "Game day")
    final = images.og_image({"depauw_wins": 5, "wabash_wins": 3, "ties": 1}, "DePauw", "Final")
    assert len({plain, day, final}) == 3
    t = {"depauw_wins": 5, "wabash_wins": 3, "ties": 1}
    assert len({images.og_version(t, "DePauw"), images.og_version(t, "DePauw", "Game day"),
                images.og_version(t, "DePauw", "Final")}) == 3
    assert images.og_version(t, "DePauw", None) == images.og_version(t, "DePauw")      # a normal day is unchanged


def test_the_page_points_at_the_matching_image_version_and_describes_it(tmp_path):
    _, html, out = banner(tmp_path, "2026-11-14 08:00", {"football": [sched()]})
    version = lambda page: re.search(r"og-image\.png\?v=(\w+)", page).group(1)
    _, normal, _ = banner(tmp_path, "2026-11-13 08:00", {"football": [sched()]})
    assert version(html) != version(normal) and "Game day." in html
    sports = {"football": football(played("2026-11-14", "W", 41, 20))}
    _, final, _ = banner(tmp_path, "2026-11-14 18:00", {"football": []}, sports)
    assert "Final." in final and version(final) not in (version(html), version(normal))
    assert (out / "og-image.png").exists()


# ---- carrying a just-finished game over --------------------------------- #

TODAY = dt.date(2026, 11, 14)


def test_a_game_that_dropped_off_the_schedule_is_kept_until_the_history_has_it():
    kept = upcoming.carry_over([sched()], [], history_dates=[], canceled=set(), today=TODAY)
    assert kept == [sched()]
    assert upcoming.carry_over([sched()], [], history_dates=["2026-11-14"], canceled=set(), today=TODAY) == []


def test_it_is_still_kept_the_next_day_and_then_let_go():
    assert upcoming.carry_over([sched()], [], history_dates=[], canceled=set(), today=dt.date(2026, 11, 15)) == [sched()]
    assert upcoming.carry_over([sched()], [], history_dates=[], canceled=set(), today=dt.date(2026, 11, 16)) == []


def test_a_game_still_on_the_schedule_is_not_carried_twice_and_a_canceled_one_is_never_carried():
    assert upcoming.carry_over([sched()], [sched()], history_dates=[], canceled=set(), today=TODAY) == []
    assert upcoming.carry_over([sched()], [], history_dates=[], canceled={"2026-11-14"}, today=TODAY) == []


def test_a_game_from_the_future_or_a_postponed_one_is_not_carried():
    assert upcoming.carry_over([sched("2026-11-20")], [], history_dates=[], canceled=set(), today=TODAY) == []
    assert upcoming.carry_over([sched(status="postponed")], [], history_dates=[], canceled=set(), today=TODAY) == []


def test_for_a_doubleheader_only_the_game_that_dropped_off_and_is_missing_is_carried():
    g1, g2 = sched("2026-11-14", "12:00 PM"), sched("2026-11-14", "3:30 PM")
    assert upcoming.carry_over([g1, g2], [g2], history_dates=[], canceled=set(), today=TODAY) == [g1]
    assert upcoming.carry_over([g1, g2], [g2], history_dates=["2026-11-14"], canceled=set(), today=TODAY) == []
    assert upcoming.carry_over([g1, g2], [], history_dates=["2026-11-14"], canceled=set(), today=TODAY) == [g2]


def test_the_parser_reports_canceled_dates_for_the_carry_over():
    head, row = (ROOT / "tests" / "fixtures" / "schedule-football.html").read_text(encoding="utf-8").split(
        'data-game-opponent-id="215"')
    row = row.replace('<div class="sidearm-schedule-game-conference-conference',
                      '<div class="sidearm-schedule-game-result"><span>Canceled</span></div>'
                      '<div class="sidearm-schedule-game-conference-conference', 1)
    seen = []
    assert upcoming.parse_schedule(head + 'data-game-opponent-id="215"' + row, opponent_id=215, canceled=seen) == []
    assert seen == ["2026-11-14"]


def finished_football_page():
    """The real football schedule fixture with the Wabash game marked finished, as a schedule page does just
    after the game (it then lists a result, so the parser leaves the game out)."""
    html = (ROOT / "tests" / "fixtures" / "schedule-football.html").read_text(encoding="utf-8")
    return html.replace("sidearm-schedule-game-upcoming", "sidearm-schedule-game-completed")


def schedule_run(tmp_path, page, previous, history, today):
    d = tmp_path / "pages"
    d.mkdir(exist_ok=True)
    (d / "football-schedule.html").write_text(page, encoding="utf-8")
    cfg = json.loads(scrape.SOURCES.read_text(encoding="utf-8"))
    cfg["sports"] = [s for s in cfg["sports"] if s["key"] == "football"]
    return scrape.scrape_schedules(cfg, previous, d, scrape.Pacer(), history=history, today=today)


def test_the_scraper_keeps_the_game_when_the_schedule_posts_the_final_before_the_history_does(tmp_path):
    previous = {"football": {"schedule_url": "x", "games": [sched()]}}
    got = schedule_run(tmp_path, finished_football_page(), previous, {"football": ["2025-11-15"]}, TODAY)
    assert [g["date"] for g in got["football"]["games"]] == ["2026-11-14"]
    # the history now has it: the game leaves the file and the window closes
    got = schedule_run(tmp_path, finished_football_page(), previous, {"football": ["2026-11-14"]}, TODAY)
    assert got["football"]["games"] == []
    # without history information (an older caller) nothing is carried
    assert schedule_run(tmp_path, finished_football_page(), previous, None, None)["football"]["games"] == []


# ---- the workflow --------------------------------------------------------- #

def job(name):
    m = re.search(rf"^  {name}:\n(.*?)(?=^  \w[\w-]*:\n|\Z)", WORKFLOW, re.S | re.M)
    assert m, name
    return m.group(1)


def workflow_jobs():
    return yaml.safe_load(WORKFLOW)["jobs"]


def checks_workflow_jobs():
    return yaml.safe_load((ROOT / ".github" / "workflows" / "checks.yml").read_text(encoding="utf-8"))["jobs"]


def build_steps():
    return workflow_jobs()["build"]["steps"]


def validate_steps():
    return workflow_jobs()["validate"]["steps"]


def named_step(name, in_job="build"):
    return next(step for step in workflow_jobs()[in_job]["steps"] if step.get("name") == name)


def step_index(steps, predicate):
    return next(i for i, step in enumerate(steps) if predicate(step))


def test_the_frequent_cron_in_the_workflow_is_the_one_the_gate_recognises_and_is_jittered():
    crons = re.findall(r'^\s+- cron: "([^"]+)"', WORKFLOW, re.M)
    assert crons == ["19 10 * * *", "47 3 * * *", FREQ]                  # the two daily runs are unchanged
    minutes = FREQ.split()[0].split(",")
    assert minutes == ["7", "37"] and "0" not in minutes and "30" not in minutes


def test_only_the_gate_decides_and_build_deploy_and_report_follow_it():
    assert "needs: gate" in job("build") and "needs.gate.outputs.run == 'true'" in job("build")
    assert "needs: build" in job("deploy") and "needs.build.result == 'success'" in job("deploy")
    assert "needs.build.outputs.problems" in job("report")
    for step in ("Scrape", "Commit if data changed", "Render site", "upload-pages-artifact"):
        assert step in job("build") and step not in job("gate")        # nothing that changes anything runs in the gate
    assert "git push" not in job("gate") and "git commit" not in job("gate")


def test_required_tests_run_before_scraping_without_failure_suppression():
    steps = build_steps()
    tests = next(i for i, step in enumerate(steps) if step.get("id") == "tests")
    scrape = next(i for i, step in enumerate(steps) if step.get("id") == "scrape")
    assert steps[tests]["run"] == "python -m pytest -q tests"
    assert tests < scrape
    assert "continue-on-error" not in steps[tests]
    assert "continue-on-error" not in workflow_jobs()["build"]


def test_a_test_failure_blocks_later_build_steps_and_downstream_writes():
    steps = build_steps()
    tests = next(i for i, step in enumerate(steps) if step.get("id") == "tests")
    for step in steps[tests + 1:]:
        condition = step.get("if", "")
        assert not re.search(r"\b(always|failure)\s*\(", condition), step.get("name", step.get("uses"))
        assert not re.search(r"!\s*cancelled\s*\(\s*\)", condition), step.get("name", step.get("uses"))

    assert "needs.build.result == 'success'" in job("deploy")
    assert "needs.build.result == 'success'" in job("issues")
    assert "github.ref == 'refs/heads/main'" in job("deploy")
    assert "github.ref == 'refs/heads/main'" in job("issues")


def test_scraper_failure_remains_tolerated_and_the_summary_takes_only_the_two_step_outcomes():
    assert named_step("Scrape")["continue-on-error"] is True
    summary = named_step("Summarize")
    assert summary["env"] == {
        "SCRAPE": "${{ steps.scrape.outcome }}",
        "COMMIT": "${{ steps.commit.outcome }}",
    }
    outputs = workflow_jobs()["build"]["outputs"]
    assert set(outputs) == {"problems", "scrape", "commit"}                       # validation has its own job now


def test_a_rejected_data_commit_does_not_stop_the_deploy_and_goes_to_the_data_branch_only():
    commit = named_step("Commit if data changed")
    assert commit["id"] == "commit" and commit["continue-on-error"] is True
    assert commit["if"] == "${{ github.ref == 'refs/heads/main' }}"                  # still main only
    script = commit["run"]
    assert "python3 scraper/databranch.py push --remote origin" in script
    assert "--force" not in script and "HEAD:main" not in script and "git push" not in script
    # the deploy and the issue sync still depend on the build job succeeding, and a failed commit step no longer fails it
    assert "needs.build.result == 'success'" in job("deploy") and "needs.build.result == 'success'" in job("issues")


# The workflow's shell steps run under bash on the Linux runner. A Windows machine without bash on PATH skips
# these tests; CI on Linux always has bash, so they run there.
needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash on PATH (the workflow's shell steps run under bash)")


@needs_bash
@pytest.mark.parametrize(
    ("scrape_result", "expected"),
    [
        ("success", ["problems=false", "scrape=ok", "commit=ok"]),
        ("failure", ["problems=true", "scrape=failed", "commit=ok"]),
    ],
)
def test_summary_shell_reports_the_scrape_outcome(tmp_path, scrape_result, expected):
    output = tmp_path / "github-output"
    result = subprocess.run(
        ["bash", "-e", "-c", named_step("Summarize")["run"]],
        env={"PATH": os.environ["PATH"], "SCRAPE": scrape_result, "COMMIT": "success", "GITHUB_OUTPUT": str(output)},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8").splitlines() == expected


@needs_bash
@pytest.mark.parametrize(
    ("commit_result", "expected_tail", "warns"),
    [
        ("success", ["problems=false", "scrape=ok", "commit=ok"], False),
        ("skipped", ["problems=false", "scrape=ok", "commit=ok"], False),   # not on main: nothing to commit
        ("failure", ["problems=true", "scrape=ok", "commit=failed"], True),
    ],
)
def test_summary_shell_reports_the_data_commit_outcome(tmp_path, commit_result, expected_tail, warns):
    output, summary = tmp_path / "github-output", tmp_path / "step-summary"
    result = subprocess.run(
        ["bash", "-e", "-c", named_step("Summarize")["run"]],
        env={"PATH": os.environ["PATH"], "SCRAPE": "success", "COMMIT": commit_result,
             "GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(summary)},
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8").splitlines() == expected_tail
    assert result.stdout.startswith("::warning title=Data commit::") == warns
    assert summary.exists() == warns


@needs_bash
@pytest.mark.parametrize(("validate_result", "expected"), [("success", "ok"), ("failure", "failed"), ("skipped", "skipped")])
def test_validate_summary_shell_reports_the_validation_outcome(tmp_path, validate_result, expected):
    output = tmp_path / "github-output"
    result = subprocess.run(
        ["bash", "-e", "-c", named_step("Summarize", "validate")["run"]],
        env={"PATH": os.environ["PATH"], "VALIDATE": validate_result, "GITHUB_OUTPUT": str(output)},
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8").splitlines() == [f"validation={expected}"]
    assert named_step("Summarize", "validate")["env"] == {"VALIDATE": "${{ steps.validate_html.outcome }}"}
    assert workflow_jobs()["validate"]["outputs"] == {"validation": "${{ steps.summary.outputs.validation }}"}


def test_bash_is_never_missing_where_ci_runs():
    # Only a Windows machine may skip the bash tests; a skip on Linux (where CI runs) would hide them.
    assert sys.platform == "win32" or shutil.which("bash")


@needs_bash
@pytest.mark.parametrize(
    ("validate", "node", "install", "expected"),
    [
        ("failure", "success", "success", "The HTML validator found a problem"),
        ("skipped", "failure", "skipped", "HTML validation was skipped because Node could not be set up"),
        ("skipped", "success", "failure", "HTML validation was skipped because the validator could not be installed from the lockfile"),
        ("skipped", "success", "success", "HTML validation was skipped because the generated site could not be fetched from the build job"),
    ],
)
def test_validation_warning_shell_names_what_happened_in_the_annotation_and_the_summary(tmp_path, validate, node, install, expected):
    summary = tmp_path / "step-summary"
    result = subprocess.run(
        ["bash", "-e", "-c", named_step("Report HTML validation problem", "validate")["run"]],
        env={"PATH": os.environ["PATH"], "VALIDATE": validate, "SETUP_NODE": node, "INSTALL": install,
             "GITHUB_STEP_SUMMARY": str(summary)},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("::warning title=HTML validation::")
    assert expected in result.stdout
    assert expected in summary.read_text(encoding="utf-8")


def test_report_names_the_problem_and_still_fails_the_run_after_the_deploy():
    report = workflow_jobs()["report"]
    report_text = "\n".join(step.get("run", "") for step in report["steps"])
    assert "needs.build.outputs.problems == 'true'" in report["if"]
    assert "needs.validate.outputs.validation != 'ok'" in report["if"] and "needs.validate.result != 'success'" in report["if"]
    assert "needs.build.result == 'success'" in report["if"] and "!cancelled()" in report["if"]
    assert set(report["needs"]) == {"build", "deploy", "validate"}
    env = report["steps"][0]["env"]
    assert env == {
        "SCRAPE": "${{ needs.build.outputs.scrape }}",
        "VALIDATION": "${{ needs.validate.outputs.validation }}",
        "VALIDATE_JOB": "${{ needs.validate.result }}",
        "COMMIT": "${{ needs.build.outputs.commit }}",
    }
    assert "The data commit to the data branch failed" in report_text
    assert "exit 1" in report_text
    assert "The scraper reported a failure" in report_text
    assert "HTML validation failed" in report_text
    assert "HTML validation was skipped because Node, the validator or the generated site could not be set up" in report_text
    assert "HTML validation did not finish because the validate job did not complete" in report_text
    assert "site was still deployed" not in report_text.lower()


@needs_bash
@pytest.mark.parametrize(
    ("scrape", "validation", "job_result", "commit", "lines"),
    [
        ("ok", "failed", "success", "ok", ["HTML validation failed."]),
        ("ok", "skipped", "success", "ok", ["HTML validation was skipped because Node, the validator or the generated site"]),
        ("ok", "", "failure", "ok", ["HTML validation did not finish"]),
        ("failed", "ok", "success", "ok", ["The scraper reported a failure."]),
        ("ok", "ok", "success", "failed", ["The data commit to the data branch failed"]),
    ],
)
def test_report_shell_says_which_problem_it_was_and_always_fails(scrape, validation, job_result, commit, lines):
    report = workflow_jobs()["report"]["steps"][0]
    result = subprocess.run(
        ["bash", "-e", "-c", report["run"]],
        env={"PATH": os.environ["PATH"], "SCRAPE": scrape, "VALIDATION": validation, "VALIDATE_JOB": job_result, "COMMIT": commit},
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    for line in lines:
        assert line in result.stdout


def test_a_validation_problem_is_a_warning_annotation_and_a_summary_note():
    report = named_step("Report HTML validation problem", "validate")["run"]
    assert '::warning title=HTML validation::$message' in report
    assert '>> "$GITHUB_STEP_SUMMARY"' in report
    assert "review the" in report and "step logs" in report              # where to look


def test_readme_documents_distinct_test_and_scraper_failure_paths():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "tests fail" in readme.lower() and "build stops" in readme.lower()
    assert "scraper failure" in readme.lower() and "fallback" in readme.lower()


def test_the_build_job_runs_no_node_and_no_npm_and_validation_has_its_own_job():
    build_text = job("build")
    assert "setup-node" not in build_text and "npm" not in build_text and "npx" not in build_text and "html-validate" not in build_text
    assert "contents: write" in build_text and "contents: write" not in job("validate")
    assert workflow_jobs()["validate"]["needs"] == "build"
    assert workflow_jobs()["deploy"]["needs"] == "build"                         # validation can never hold up publishing
    assert "validate" not in str(workflow_jobs()["deploy"]) and "validate" not in str(workflow_jobs()["issues"])


def test_the_validate_job_holds_no_write_token_and_no_persisted_credentials():
    v = workflow_jobs()["validate"]
    assert v["permissions"] == {"contents": "read"}
    checkout = next(step for step in v["steps"] if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"] == {"persist-credentials": False}
    assert "secrets." not in job("validate") and "github.token" not in job("validate") and "GITHUB_TOKEN" not in job("validate")


def test_render_precedes_data_commit_and_upload_and_validation_runs_in_order_after_the_artifact_exists():
    steps = build_steps()
    render_index = step_index(steps, lambda step: step.get("name") == "Render site")
    commit_index = step_index(steps, lambda step: step.get("name") == "Commit if data changed")
    upload_index = step_index(steps, lambda step: step.get("uses", "").startswith("actions/upload-pages-artifact@"))
    assert render_index < commit_index < upload_index == commit_index + 1
    assert sum(step.get("name") == "Render site" for step in steps) == 1

    v = validate_steps()
    order = ["Get the generated site from the build job", "Unpack the site", "Set up Node for HTML validation",
             "Install the HTML validator from the lockfile", "Check the title script syntax", "Validate generated HTML",
             "Report HTML validation problem", "Summarize"]
    names = [step.get("name") for step in v]
    assert [n for n in names if n in order] == order
    download = v[names.index(order[0])]
    assert download["uses"].startswith("actions/download-artifact@") and download["with"]["name"] == "github-pages"


def test_render_is_fail_closed_and_every_validation_step_is_non_blocking_and_reported():
    assert "continue-on-error" not in workflow_jobs()["build"] and "continue-on-error" not in workflow_jobs()["validate"]
    render_step = named_step("Render site")
    assert render_step["run"] == "python scraper/render.py"
    assert "continue-on-error" not in render_step
    assert not re.search(r"\b(always|failure|cancelled)\s*\(", render_step.get("if", ""))

    for name in ("Get the generated site from the build job", "Unpack the site", "Set up Node for HTML validation",
                 "Install the HTML validator from the lockfile", "Check the title script syntax", "Validate generated HTML"):
        assert named_step(name, "validate")["continue-on-error"] is True, name
    node_step = named_step("Set up Node for HTML validation", "validate")
    assert node_step["id"] == "setup_node"
    assert named_step("Install the HTML validator from the lockfile", "validate")["run"] == "npm ci --ignore-scripts --no-audit --no-fund"
    assert named_step("Check the title script syntax", "validate")["if"] == "${{ steps.setup_node.outcome == 'success' }}"

    validation = named_step("Validate generated HTML", "validate")
    assert validation["id"] == "validate_html"
    assert validation["if"] == ("${{ steps.setup_node.outcome == 'success' && steps.install.outcome == 'success' "
                                "&& steps.unpack.outcome == 'success' }}")                 # skipped, not failed, without its inputs
    assert "npx --no-install html-validate" in validation["run"] and "--yes" not in validation["run"]

    report = named_step("Report HTML validation problem", "validate")
    assert report["if"] == "${{ steps.validate_html.outcome != 'success' }}"
    assert "$GITHUB_STEP_SUMMARY" in report["run"]
    assert "generated HTML validation failed" in report["run"]
    assert "Publishing continues" in report["run"]
    assert "needs.build.result == 'success'" in job("deploy")
    assert "needs.build.result == 'success'" in job("issues")


def test_production_html_validation_matches_pull_request_checks():
    production = named_step("Validate generated HTML", "validate")
    production_checks = checks_workflow_jobs()["checks"]["steps"]
    pr_validation = next(step for step in production_checks if step.get("name") == "Validate generated HTML")
    production_command = shlex.split(production["run"])
    assert production_command == shlex.split(pr_validation["run"])
    assert production_command[:3] == ["npx", "--no-install", "html-validate"]
    assert set(production_command[3:]) == {
        "site/index.html",
        "site/404.html",
        "site/bell-heist/index.html",
        "site/heist-details/index.html",
        "site/privacy/index.html",
        "site/how-it-works/index.html",
        "site/how-it-works/athletics/index.html",
        "site/how-it-works/alumni/index.html",
        "site/alumni/index.html",
    }

    production_node = named_step("Set up Node for HTML validation", "validate")
    pr_node = next(step for step in production_checks if step.get("uses", "").startswith("actions/setup-node@"))
    assert production_node["uses"] == pr_node["uses"]
    assert production_node["with"] == pr_node["with"] == {"node-version": "22"}
    production_install = named_step("Install the HTML validator from the lockfile", "validate")
    pr_install = next(step for step in production_checks if step.get("name") == "Install the HTML validator from the lockfile")
    assert production_install["run"] == pr_install["run"] == "npm ci --ignore-scripts --no-audit --no-fund"
    names = [step.get("name") for step in production_checks]
    assert names.index("Install the HTML validator from the lockfile") < names.index("Validate generated HTML")
    production_syntax = named_step("Check the title script syntax", "validate")
    assert production_syntax["continue-on-error"] is True
    pr_syntax = next(step for step in production_checks if step.get("name") == "Check the title script syntax")
    assert "continue-on-error" not in pr_syntax and "if" not in pr_syntax


def test_readme_documents_render_and_html_validation_in_its_own_job():
    readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    workflow_table = next(line for line in readme.splitlines() if "`update-and-deploy.yml`" in line)
    assert "renders the candidate site, which must succeed" in workflow_table
    assert "`validate` (`contents: read`" in workflow_table and "no write token" in workflow_table
    assert "with `html-validate`, which only warns" in workflow_table
    assert "turns the run red after the deploy" in workflow_table and "validation failed or was skipped" in workflow_table
    assert "test and render failures stop the build" in workflow_table
    assert "commits changed `data/`" in workflow_table
    operator_text = readme.split("what each run of `update-and-deploy.yml` does:", 1)[0]
    assert "render failure stops the build before the data commit" in operator_text
    assert "html validation problems" in operator_text and "do not stop publishing" in operator_text
    assert "html validation never stops the build" in operator_text
    assert "if node cannot be set up the validation is skipped" in operator_text
    assert "a warning annotation and a run-summary note" in operator_text
    assert "turns the run red for a scraper failure, a failed validation or a skipped validation" in operator_text
    assert "separate `validate` job" in operator_text and "package-lock.json" in operator_text
    workflow_section = readme.split("what each run of `update-and-deploy.yml` does:", 1)[1].split("### why the merge works", 1)[0]
    assert "render" in workflow_section and "validate" in workflow_section and "commit" in workflow_section
    assert (
        workflow_section.index("render site")
        < workflow_section.index("data/ changed?")
        < workflow_section.index("commits data")
        < workflow_section.index("upload site artifact")
        < workflow_section.index("validate job")
        < workflow_section.index("set up node")
        < workflow_section.index("validate all nine")
    )
    assert "render fails" in workflow_section and "no commit, no deploy" in workflow_section
    assert "validation skipped" in workflow_section and "run turns red" in workflow_section
    assert "the site still publishes" in workflow_section


def test_a_skipped_build_means_no_commit_no_deploy_and_no_new_check_time():
    # build is skipped when the gate says no, so its commit and scrape steps (the only place .checked-at and
    # data/ are written) never run, and deploy only runs when build succeeded.
    assert "contents: write" in job("build") and "contents: write" not in job("gate")
    assert "checked" not in job("gate").lower()
    assert "!cancelled() && needs.build.result == 'success'" in job("deploy")


def test_the_gate_installs_nothing_and_uses_the_runners_own_python():
    g = job("gate")
    assert "setup-python" not in g and "pip install" not in g and "python3 scraper/gameday.py gate" in g
    assert "SCHEDULE: ${{ github.event.schedule }}" in g                 # passed through env, never spliced into the shell


def test_frequent_runs_cannot_pile_up_or_cancel_a_running_scheduled_run():
    assert not re.search(r"^concurrency:", WORKFLOW, re.M)               # no workflow-level group for no-op runs to queue in
    assert "group: update-and-deploy" in job("build") and "group: pages" in job("deploy")
    assert "group:" not in job("gate")
    assert WORKFLOW.count("cancel-in-progress: false") == 3 and "cancel-in-progress: true" not in WORKFLOW


def test_the_preview_only_now_option_never_reaches_the_workflow():
    assert "--now" not in WORKFLOW
    assert "--now" in (ROOT / "scraper" / "render.py").read_text(encoding="utf-8")


def test_now_option_reads_eastern_without_an_offset_and_utc_with_one():
    assert render.parse_now(None) is None
    assert render.parse_now("2026-11-14T13:07") == dt.datetime(2026, 11, 14, 18, 7, tzinfo=dt.timezone.utc)   # EST
    assert render.parse_now("2026-10-14T13:07") == dt.datetime(2026, 10, 14, 17, 7, tzinfo=dt.timezone.utc)   # EDT
    assert render.parse_now("2026-11-14T13:07:00+00:00") == dt.datetime(2026, 11, 14, 13, 7, tzinfo=dt.timezone.utc)
