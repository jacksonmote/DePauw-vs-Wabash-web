"""The daily health check (scraper/health.py, .github/workflows/health-check.yml).

No network, no clock and no outside programs: every check gets a fixed `now`, GitHub's API is a fake that answers
from a table, the live page is a string, and the issue sync gets a fake gh runner."""

import ast
import datetime as dt
import email.message
import io
import json
import re
import subprocess
import urllib.error
import urllib.request
import urllib.response
from pathlib import Path

import pytest
import yaml

import gameday
import health
import render
from test_render import build as build_site, football

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "health-check.yml"
UPDATE = ROOT / ".github" / "workflows" / "update-and-deploy.yml"
REPO = "owner/site"
NOW = dt.datetime(2026, 11, 20, 20, 28, tzinfo=dt.timezone.utc)          # Nov 20, 2026, 3:28 PM EST
SCHEDULED = [".github/workflows/gameday-watcher.yml", ".github/workflows/health-check.yml",
             ".github/workflows/link-check.yml", ".github/workflows/snapshot.yml",
             ".github/workflows/update-and-deploy.yml"]


def iso(when):
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def ago(**span):
    return NOW - dt.timedelta(**span)


def by_key(checks):
    return {c["key"]: c for c in checks}


# ---- which workflows have a schedule ----------------------------------------------------------- #

def test_the_scheduled_workflows_are_read_from_the_workflow_files():
    assert health.scheduled_workflows() == SCHEDULED                       # checks.yml has no schedule


def test_a_commented_out_cron_line_is_not_a_schedule(tmp_path):
    (tmp_path / "a.yml").write_text("on:\n  schedule:\n    - cron: \"5 4 * * *\"\n", encoding="utf-8")
    (tmp_path / "b.yml").write_text("on:\n  push:\n  # schedule:\n  #   - cron: \"5 4 * * *\"\n", encoding="utf-8")
    (tmp_path / "c.txt").write_text("    - cron: \"5 4 * * *\"\n", encoding="utf-8")
    assert health.scheduled_workflows(tmp_path) == [".github/workflows/a.yml"]


def listed(**states):
    return [{"path": p, "state": states.get(Path(p).stem.replace("-", "_"), "active")} for p in SCHEDULED] + [
        {"path": ".github/workflows/checks.yml", "state": "disabled_manually"},        # no schedule: not our business
        {"path": "dynamic/pages/pages-build-deployment", "state": "active"}]


def test_every_scheduled_workflow_enabled_is_ok():
    c = health.check_workflows(SCHEDULED, listed())
    assert c["status"] == "ok" and "All 5 workflows with a schedule are enabled" in c["detail"]
    assert "`health-check.yml`" in c["detail"] and "checks.yml" not in c["detail"]


@pytest.mark.parametrize("state, words", [
    ("disabled_inactivity", "switched off by GitHub for inactivity"),
    ("disabled_manually", "switched off by hand"),
    ("disabled_fork", "in the state `disabled_fork`"),
])
def test_a_scheduled_workflow_that_is_off_is_a_problem_that_says_how_to_enable_it(state, words):
    c = health.check_workflows(SCHEDULED, listed(update_and_deploy=state, link_check=state))
    assert c["status"] == "problem"
    assert c["detail"] == f"Not enabled: `link-check.yml` is {words}; `update-and-deploy.yml` is {words}."
    assert "`gh workflow enable link-check.yml` and `gh workflow enable update-and-deploy.yml`" in c["todo"]
    assert "Enable workflow" in c["todo"] and "gameday-watcher" not in c["detail"]
    merge_first = "First merge a real pull request into `main`: enabling alone does not meet the 60-day rule"
    assert (merge_first in c["todo"]) == (state == "disabled_inactivity")     # only then is a commit needed too
    if state == "disabled_inactivity":
        assert c["todo"].index(merge_first) < c["todo"].index("To enable a workflow")
    assert "By users' reports" in c["todo"]                                    # not from GitHub's documentation


def test_a_state_that_is_not_a_switch_is_shown_as_github_names_it():
    c = health.check_workflows(SCHEDULED, listed(snapshot="deleted", link_check="disabled_inactivity"))
    assert "`snapshot.yml` is in the state `deleted`" in c["detail"]
    assert "`link-check.yml` is switched off by GitHub for inactivity" in c["detail"]
    assert "gh workflow enable snapshot.yml" not in c["todo"] and "gh workflow enable link-check.yml" in c["todo"]
    assert "For any other state, open the workflow on the Actions tab" in c["todo"]


def test_a_scheduled_workflow_github_does_not_list_is_a_problem():
    c = health.check_workflows(SCHEDULED, [w for w in listed() if "snapshot" not in w["path"]])
    assert c["status"] == "problem" and "`snapshot.yml` is not listed by GitHub" in c["detail"]


def test_no_scheduled_workflow_file_at_all_is_unknown_not_ok():
    assert health.check_workflows([], listed())["status"] == "unknown"


# ---- the 60-day rule ---------------------------------------------------------------------------- #

def commit(when, sha="60b4f2d1c0ffee"):
    return {"sha": sha, "date": iso(when)}


def test_the_limits_are_the_ones_the_docs_and_the_maintainer_set():
    assert health.WARN_AFTER == dt.timedelta(days=45) and health.DISABLE_AFTER == dt.timedelta(days=60)
    assert health.MAX_AGE == dt.timedelta(hours=48)


def test_a_recent_commit_on_main_is_ok_and_says_when_the_warning_would_start():
    c = health.check_activity(NOW, commit(ago(days=5, hours=3)))
    assert c["status"] == "ok" and "`60b4f2d` from Nov 15, 2026, 5 days ago" in c["detail"]
    assert "after 60 days without repository activity; this check warns from 45 days (Dec 30, 2026)" in c["detail"]


def test_the_activity_check_carries_the_limit_as_a_utc_time_whatever_its_result():
    last = dt.datetime(2026, 10, 1, 20, 28, tzinfo=dt.timezone.utc)
    for now in (last + dt.timedelta(days=3), last + dt.timedelta(days=50), last + dt.timedelta(days=70)):
        assert health.check_activity(now, commit(last))["deadline"] == "2026-11-30T20:28:00+00:00"
    eastern = {"sha": "60b4f2d1c0ffee", "date": "2026-10-01T16:28:00-04:00"}          # the same moment, with an offset
    assert health.check_activity(NOW, eastern)["deadline"] == "2026-11-30T20:28:00+00:00"


def test_the_warning_starts_at_exactly_45_days():
    assert health.check_activity(NOW, commit(ago(days=45) + dt.timedelta(seconds=1)))["status"] == "ok"
    c = health.check_activity(NOW, commit(ago(days=45)))
    assert c["status"] == "problem"


def test_the_warning_says_what_to_do_and_the_moment_before_which():
    last = dt.datetime(2026, 10, 6, 15, 0, tzinfo=dt.timezone.utc)          # 45 days and a few hours before NOW
    c = health.check_activity(NOW, commit(last))
    assert c["status"] == "problem"
    assert "from Oct 6, 2026, 45 days ago" in c["detail"]
    assert "that is Dec 5, 2026, 10:00 AM EST, 14 days from now" in c["detail"]   # Oct 6, 11 AM EDT + 60 days
    assert "Merge any real pull request into `main`" in c["todo"] and "Dependabot pull request" in c["todo"]
    assert "If none is waiting, open one with any small real improvement" in c["todo"]
    assert "It must be merged before Dec 5, 2026, 10:00 AM EST" in c["todo"]
    assert "Do not make an empty commit" in c["todo"]
    assert f"After that {health.SCHEDULED_WORK} all stop." in c["todo"]


def test_the_limit_is_the_eastern_time_of_the_commit_plus_60_days():
    late = dt.datetime(2026, 10, 2, 2, 30, tzinfo=dt.timezone.utc)           # Oct 1, 10:30 PM EDT
    c = health.check_activity(NOW, commit(late))
    assert "from Oct 1, 2026" in c["detail"] and "that is Nov 30, 2026, 9:30 PM EST" in c["detail"]


def test_a_late_evening_commit_never_reads_as_a_whole_extra_day():
    """A commit at 11:30 PM EST on Jan 20, 2027 reaches 60 days at 12:30 AM EDT on Mar 22. "By Mar 22" would be read
    as the end of that day, almost a day too late, so the limit is given with its time."""
    last = dt.datetime(2027, 1, 21, 4, 30, tzinfo=dt.timezone.utc)
    now = dt.datetime(2027, 3, 10, 20, 28, tzinfo=dt.timezone.utc)
    c = health.check_activity(now, commit(last))
    assert c["status"] == "problem" and "from Jan 20, 2027, 48 days ago" in c["detail"]
    assert "that is Mar 22, 2027, 12:30 AM EDT, 11 days from now" in c["detail"]
    assert "It must be merged before Mar 22, 2027, 12:30 AM EDT" in c["todo"]
    assert "by Mar 22" not in c["detail"] + c["todo"]


def test_after_60_days_it_says_the_workflows_may_already_be_off():
    assert "days from now" in health.check_activity(NOW, commit(ago(days=60) + dt.timedelta(seconds=1)))["detail"]
    c = health.check_activity(NOW, commit(ago(days=60)))
    assert c["status"] == "problem" and "that limit passed on Nov 20, 2026, 3:28 PM EST" in c["detail"]
    assert "may already be off" in c["detail"] and "enable every workflow that is off" in c["todo"]
    assert "a commit does not switch them back on" in c["todo"]


# ---- the three 48-hour checks ------------------------------------------------------------------- #

@pytest.mark.parametrize("make", [
    lambda when: health.check_scrape(NOW, when, "in the last 7 days (9 runs looked at)"),
    lambda when: health.check_deploy(NOW, when, "in the last 7 days (9 runs looked at)"),
    lambda when: health.check_last_checked(
        NOW, f'<p>Data last changed <time datetime="2026-11-14T21:25:16+00:00">Nov 14, 4:25 PM</time> · Last checked '
             f'<time datetime="{when.isoformat()}">x</time> (Eastern time).</p>' if when else
        '<p>Data last changed <time datetime="2026-11-14T21:25:16+00:00">Nov 14, 4:25 PM</time> (Eastern time).</p>'),
], ids=["scrape", "deploy", "last-checked"])
def test_each_time_check_is_ok_up_to_48_hours_and_a_problem_after(make):
    assert make(ago(hours=3))["status"] == "ok"
    assert make(ago(hours=48))["status"] == "ok"                                              # the limit itself
    over = make(ago(hours=48, seconds=1))
    assert over["status"] == "problem" and "over the 48-hour limit" in over["detail"] and over["todo"]
    none = make(None)
    assert none["status"] == "problem" and none["todo"]


def test_the_time_checks_say_when_and_how_long_ago_in_eastern_time():
    c = health.check_deploy(NOW, dt.datetime(2026, 11, 19, 3, 5, tzinfo=dt.timezone.utc), "")
    assert c["detail"] == ("The last successful deploy finished Nov 18, 2026, 10:05 PM EST, 1 day 17 hours ago "
                           "(the limit is 48 hours).")
    old = health.check_scrape(NOW, ago(days=6), "in the last 7 days (40 runs looked at)")
    assert "Nov 14, 2026, 3:28 PM EST, 6 days ago: over the 48-hour limit" in old["detail"]
    assert health.check_scrape(NOW, None, "in the last 7 days (40 runs looked at)")["detail"] == (
        "No successful run that read every sport was found in the last 7 days (40 runs looked at).")


def without_time(tmp_path):
    """The home page as the renderer writes it when the time of the last real scrape is not known."""
    return build_site(tmp_path, {"football": football()}, now=dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.timezone.utc))[1]


def test_last_checked_is_read_from_a_page_the_renderer_really_writes(tmp_path):
    _, page = build_site(tmp_path, {"football": football()}, checked_at="2026-11-19T10:19:44+00:00",
                         now=dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.timezone.utc))
    assert health.parse_last_checked(page) == dt.datetime(2026, 11, 19, 10, 19, 44, tzinfo=dt.timezone.utc)
    assert health.check_last_checked(NOW, page)["status"] == "ok"
    assert health.OUR_PAGE in page and health.OUR_PAGE in without_time(tmp_path)
    without = without_time(tmp_path)
    assert health.parse_last_checked(without) is None
    c = health.check_last_checked(NOW, without)
    assert c["status"] == "problem" and "shows no \"Last checked\" time" in c["detail"]


def test_a_live_page_that_does_not_load_could_not_be_checked():
    c = health.check_last_checked(NOW, None, "HTTP 503")
    assert c["status"] == "unknown"
    assert c["detail"] == "Could not be checked: the live home page did not load (HTTP 503)."
    assert health.SITE_URL.rstrip("/") == render.SITE_URL.rstrip("/")


def test_an_answer_that_is_not_our_home_page_could_not_be_checked():
    """A 200 answer from something else (a parking page, a login wall) says nothing about our updates."""
    c = health.check_last_checked(NOW, "<html><body><h1>Site not found</h1></body></html>")
    assert c["status"] == "unknown" and "answered, but not with our home page" in c["detail"]
    assert "shows no" not in c["detail"]


def test_the_data_branch_check():
    c = health.check_data_branch(commit(dt.datetime(2026, 11, 14, 21, 25, 47, tzinfo=dt.timezone.utc), "fc80d1d00000"))
    assert c["status"] == "ok" and c["detail"] == "The `data` branch's last commit is `fc80d1d` from Nov 14, 2026."
    gone = health.check_data_branch(None, "GitHub says there is no such branch")
    assert gone["status"] == "problem" and "could not be read: GitHub says there is no such branch" in gone["detail"]
    assert "If the branch is ever lost" in gone["todo"]
    assert "If the branch is ever lost" in (ROOT / "README.md").read_text(encoding="utf-8")


# ---- finding the last full scrape and the last deploy among the update runs --------------------- #

def test_the_gate_line_is_read_as_the_gate_really_prints_it(tmp_path, capsys):
    up, series = tmp_path / "upcoming.json", tmp_path / "series.json"
    up.write_text(json.dumps({"schema_version": 1, "sports": {}}), encoding="utf-8")
    series.write_text(json.dumps({"sports": {}}), encoding="utf-8")

    def gate(schedule):
        gameday.main(["gate", "--schedule", schedule, "--now", "2026-11-20T12:00:00-05:00",
                      "--upcoming", str(up), "--series", str(series)])
        return capsys.readouterr().out
    daily = gate("19 10 * * *")
    assert health.gate_scope("2026-11-20T10:19:44.1234567Z " + daily) == health.FULL_SCOPE == "all"
    assert health.gate_scope(gate(gameday.FREQUENT_CRON)) == "(nothing)"
    assert health.gate_scope("run=true scope=football,mens-basketball: game-day update") == "football,mens-basketball"
    assert health.gate_scope("the job's log without the gate's line") is None
    first = "\ufeff2026-11-20T10:19:40.0000000Z run=true scope=all: a daily run\n"   # a log starts with a byte-order mark
    assert health.gate_scope(first) == "all"
    # Only the gate's own line counts: the same words inside another line (a command being echoed, say) do not.
    echoed = ("2026-11-20T10:19:40.0000000Z ##[group]Run echo run=true scope=all: x\n"
              "2026-11-20T10:19:44.0000000Z run=true scope=football: game-day update\n")
    assert health.gate_scope(echoed) == "football"


def test_the_names_the_run_search_relies_on_are_in_the_update_workflow():
    wf = yaml.safe_load(UPDATE.read_text(encoding="utf-8"))
    assert health.UPDATE_WORKFLOW == UPDATE.name
    assert {health.GATE_JOB, health.BUILD_JOB, health.DEPLOY_JOB} <= set(wf["jobs"])
    for job in (health.GATE_JOB, health.BUILD_JOB, health.DEPLOY_JOB):
        assert "name" not in wf["jobs"][job]                                  # the API lists a job by its id then
    assert health.SCRAPE_STEP in [s.get("name") for s in wf["jobs"][health.BUILD_JOB]["steps"]]


class Runs:
    """A fake history of update runs. add() describes one run; jobs_of and log_of answer like GitHub's API and
    record what was asked."""
    def __init__(self):
        self.runs, self.jobs, self.logs, self.asked_jobs, self.asked_logs = [], {}, {}, [], []
        self.asked_pages, self.broken_logs = [], {}

    def add(self, run_id, finished, scope="all", conclusion="success", scraped=True, deployed=True):
        """scope: what the gate said ("all", "football", "" for a run the gate ended)."""
        self.runs.append({"id": run_id, "created_at": iso(finished - dt.timedelta(minutes=10)),
                          "conclusion": conclusion, "event": "schedule"})
        ran = bool(scope)
        self.jobs[run_id] = [
            {"id": run_id * 10, "name": "gate", "conclusion": "success"},
            {"id": run_id * 10 + 1, "name": "build", "conclusion": "success" if ran else "skipped", "steps": [
                {"name": "Scrape", "conclusion": ("success" if scraped else "failure") if ran and scope != "push" else "skipped",
                 "completed_at": iso(finished - dt.timedelta(minutes=3))}]},
            {"id": run_id * 10 + 2, "name": "deploy", "conclusion": "success" if ran and deployed else "skipped",
             "completed_at": iso(finished)}]
        said = {"": "run=false scope=(nothing): no game-day window is open", "push": "run=true scope=(nothing): a push"}
        self.logs[run_id * 10] = f"2026-11-20T00:00:00.0000000Z {said.get(scope, f'run=true scope={scope}: why')}\n"
        return self

    def jobs_of(self, run_id):
        self.asked_jobs.append(run_id)
        return self.jobs[run_id]

    def log_of(self, job_id):
        self.asked_logs.append(job_id)
        if job_id in self.broken_logs:
            raise self.broken_logs[job_id]
        return self.logs[job_id]

    def page_of(self, page):
        self.asked_pages.append(page)
        return self.runs[(page - 1) * health.PAGE_SIZE: page * health.PAGE_SIZE]

    def find(self):
        return health.find_latest(NOW, self.page_of, self.jobs_of, self.log_of)

    def found(self):
        f = self.find()
        return f["scrape"], f["deploy"], f["looked"]


def test_the_newest_full_scrape_and_the_newest_deploy_are_found_and_the_search_stops_there():
    h = Runs()
    h.add(9, ago(hours=1), scope="")                         # a 30-minute run the gate ended
    h.add(8, ago(hours=2), scope="push")                     # a push: deployed, read nothing
    h.add(7, ago(hours=5), scope="football")                 # a game-day update: one sport only
    h.add(6, ago(hours=9), scope="all")                      # the daily run
    h.add(5, ago(hours=20), scope="all")
    scrape, deploy, looked = h.found()
    assert deploy == ago(hours=2) and scrape == ago(hours=9, minutes=3) and looked == 4
    assert h.asked_pages == [1]
    assert h.asked_jobs == [9, 8, 7, 6]                      # run 5 is never read
    assert h.asked_logs == [70, 60]                          # a gate log only where the Scrape step succeeded


def test_a_game_day_update_is_not_a_full_scrape():
    h = Runs().add(3, ago(hours=1), scope="football").add(2, ago(hours=2), scope="football,mens-soccer")
    scrape, deploy, _ = h.found()
    assert scrape is None and deploy == ago(hours=1)


def test_a_failed_scrape_or_a_red_run_is_not_a_successful_full_scrape_but_its_deploy_counts():
    h = Runs()
    h.add(4, ago(hours=1), scope="all", scraped=False, conclusion="failure")     # the scraper failed; fallback data deployed
    h.add(3, ago(hours=2), scope="all", conclusion="failure")                      # scraped, then turned red (validation, say)
    h.add(2, ago(hours=30), scope="all")
    scrape, deploy, _ = h.found()
    assert deploy == ago(hours=1) and scrape == ago(hours=30, minutes=3)
    assert h.asked_logs == [20]                               # the red runs' logs are never read


def test_a_run_whose_deploy_did_not_happen_does_not_count_as_a_deploy():
    h = Runs().add(2, ago(hours=1), scope="all", deployed=False).add(1, ago(hours=50), scope="all")
    scrape, deploy, _ = h.found()
    assert scrape == ago(hours=1, minutes=3) and deploy == ago(hours=50)


def test_the_search_goes_back_seven_days_and_no_further():
    h = Runs().add(2, ago(days=1), scope="").add(1, ago(days=8), scope="all")
    assert h.found() == (None, None, 1) and h.asked_jobs == [2]
    assert health.LOOKBACK == dt.timedelta(days=7)
    assert health.searched_words(h.find()) == "in the last 7 days (1 run looked at)"


def test_no_runs_at_all_finds_nothing():
    found = health.find_latest(NOW, lambda page: [], None, None)
    assert found == {"scrape": None, "deploy": None, "looked": 0, "capped": False, "log_error": ""}


def quiet_runs(h, count, first_id=1000):
    """`count` runs the gate ended (nothing read, nothing deployed), a minute apart, newest first."""
    for i in range(count):
        h.add(first_id - i, ago(minutes=30 + i), scope="")
    return h


def test_the_search_reads_further_pages_until_both_are_found():
    """A game day makes far more than one page of runs in 7 days: the full scrape behind them must still be found."""
    h = quiet_runs(Runs(), health.PAGE_SIZE)
    h.add(5, ago(hours=9), scope="all").add(4, ago(hours=20), scope="all")
    scrape, deploy, looked = h.found()
    assert scrape == ago(hours=9, minutes=3) and deploy == ago(hours=9) and looked == health.PAGE_SIZE + 1
    assert h.asked_pages == [1, 2] and 4 not in h.asked_jobs
    assert health.PAGE_SIZE == 100                                             # the API's largest page


def test_a_short_page_is_the_last_one():
    h = quiet_runs(Runs(), 3)
    assert h.found() == (None, None, 3) and h.asked_pages == [1]               # no request for an empty page 2


def test_a_search_stopped_by_the_page_cap_says_so_instead_of_claiming_seven_days(monkeypatch):
    monkeypatch.setattr(health, "MAX_PAGES", 2)
    h = quiet_runs(Runs(), 2 * health.PAGE_SIZE + 1)
    found = h.find()
    assert found["capped"] and found["looked"] == 200 and h.asked_pages == [1, 2]
    words = health.searched_words(found)
    assert words == "in the newest 200 runs (the search stops after 200 runs, before it reached 7 days back)"
    c = by_key(run_all(FakeGitHub(h)))
    assert c["scrape"]["status"] == c["deploy"]["status"] == "problem"
    assert c["scrape"]["detail"] == f"No successful run that read every sport was found {words}."
    assert c["deploy"]["detail"] == f"No successful deploy was found {words}."
    assert "in the last 7 days" not in c["scrape"]["detail"] + c["deploy"]["detail"]


def test_the_cap_does_not_matter_once_both_are_found(monkeypatch):
    monkeypatch.setattr(health, "MAX_PAGES", 1)
    h = Runs().add(2, ago(hours=9), scope="all")
    assert not h.find()["capped"]
    # Both found only at the very last run of the last page allowed: the search ends at the cap, but nothing is missing.
    last = quiet_runs(Runs(), health.PAGE_SIZE - 1).add(5, ago(hours=9), scope="all")
    found = last.find()
    assert found["looked"] == health.PAGE_SIZE and found["scrape"] == ago(hours=9, minutes=3) and not found["capped"]
    assert health.searched_words(found) == "in the last 7 days (100 runs looked at)"


def test_a_gate_log_that_cannot_be_downloaded_leaves_the_deploy_its_answer():
    h = Runs().add(3, ago(hours=1), scope="push").add(2, ago(hours=9), scope="all").add(1, ago(hours=20), scope="all")
    h.broken_logs[20] = http_error(410)                      # the newest run that may have been the full scrape
    found = h.find()
    assert found["deploy"] == ago(hours=1) and found["scrape"] is None
    assert found["log_error"] == "the gate job's log of run 2 could not be downloaded (HTTP 410)"
    assert h.asked_logs == [20]                               # an older full scrape would not be the newest: not read
    c = by_key(run_all(FakeGitHub(h)))
    assert c["deploy"]["status"] == "ok" and c["scrape"]["status"] == "unknown"
    assert c["scrape"]["detail"] == ("Could not be checked: the gate job's log of run 2 could not be downloaded "
                                     "(HTTP 410).")
    assert {k for k, x in c.items() if x["status"] != "ok"} == {"scrape"}


def test_an_unreadable_log_does_not_stop_the_search_for_the_deploy():
    h = Runs().add(3, ago(hours=1), scope="all", deployed=False).add(2, ago(hours=9), scope="all")
    h.broken_logs[30] = urllib.error.URLError(TimeoutError("timed out"))
    found = h.find()
    assert found["deploy"] == ago(hours=9) and "no connection: TimeoutError" in found["log_error"]
    assert h.asked_logs == [30]


# ---- every check together, against a fake GitHub ------------------------------------------------ #

def http_error(code, location=None):
    headers = email.message.Message()
    if location:
        headers["Location"] = location
    return urllib.error.HTTPError("https://api.github.com/x", code, "error", headers, None)


class FakeGitHub:
    """Answers GET paths from a table of (path start, answer); an answer that is an exception is raised."""
    def __init__(self, history, main=None, data=None, workflows=None, broken=()):
        self.history, self.calls, self.broken = history, [], tuple(broken)
        self.main = main if main is not None else commit(ago(days=5))
        self.data = data if data is not None else commit(ago(days=6), "fc80d1d00000")
        self.workflows = workflows if workflows is not None else listed()

    def get(self, path):
        self.calls.append(path)
        for part in self.broken:
            if part in path:
                raise http_error(502)
        if path.startswith(f"/repos/{REPO}/actions/workflows?"):
            return {"workflows": self.workflows}
        if path.startswith(f"/repos/{REPO}/commits?sha="):
            answer = self.main if "sha=main" in path else self.data
            if isinstance(answer, Exception):
                raise answer
            return [{"sha": answer["sha"], "commit": {"committer": {"date": answer["date"]}}}]
        if path.startswith(f"/repos/{REPO}/actions/workflows/update-and-deploy.yml/runs?"):
            assert "branch=main" in path and "status=completed" in path and f"per_page={health.PAGE_SIZE}&" in path
            return {"workflow_runs": self.history.page_of(int(re.search(r"&page=(\d+)$", path).group(1)))}
        m = re.fullmatch(rf"/repos/{REPO}/actions/runs/(\d+)/jobs\?per_page=100", path)
        assert m, path
        return {"jobs": self.history.jobs_of(int(m.group(1)))}


def page(when):
    return (f'<p>Data last changed <time datetime="2026-11-14T21:25:16+00:00">x</time> · Last checked '
            f'<time datetime="{when.isoformat()}">x</time> (Eastern time).</p>')


def never_wait(seconds):
    pytest.fail("the check waited although the page loaded at once")


def healthy_history():
    return Runs().add(3, ago(hours=1), scope="push").add(2, ago(hours=9), scope="all")


def run_all(github, site=lambda: page(ago(hours=9)), sleep=never_wait):
    return health.run_checks(NOW, REPO, github.get, github.history.log_of, site, SCHEDULED, sleep=sleep)


def test_a_healthy_repository_passes_every_check_in_the_reports_order():
    checks = run_all(FakeGitHub(healthy_history()))
    assert [c["key"] for c in checks] == ["workflows", "activity", "scrape", "deploy", "last-checked", "data-branch"]
    assert [c["status"] for c in checks] == ["ok"] * 6
    report = health.build_report(NOW, checks)
    assert report["totals"] == {"ok": 6, "problem": 0, "unknown": 0}
    assert report["checked_at"] == "2026-11-20T20:28:00+00:00"
    assert health.totals_line(report) == "6 checks: 0 problems, 6 OK."


def test_each_failing_check_is_reported_by_itself():
    old = Runs().add(2, ago(hours=60), scope="all")
    checks = by_key(run_all(FakeGitHub(old, main=commit(ago(days=50)), workflows=listed(gameday_watcher="disabled_inactivity")),
                            site=lambda: page(ago(hours=60))))
    assert {k: c["status"] for k, c in checks.items()} == {
        "workflows": "problem", "activity": "problem", "scrape": "problem", "deploy": "problem",
        "last-checked": "problem", "data-branch": "ok"}


def test_a_missing_data_branch_is_a_problem_and_other_errors_there_are_unknown():
    assert by_key(run_all(FakeGitHub(healthy_history(), data=http_error(404))))["data-branch"]["status"] == "problem"
    assert by_key(run_all(FakeGitHub(healthy_history(), data=http_error(500))))["data-branch"]["status"] == "unknown"


def test_a_site_that_cannot_be_fetched_is_tried_once_more_and_then_could_not_be_checked():
    tries, waits = [], []

    def down():
        tries.append(1)
        raise urllib.error.URLError(TimeoutError("timed out"))
    checks = by_key(run_all(FakeGitHub(healthy_history()), site=down, sleep=waits.append))
    c = checks["last-checked"]
    assert c["status"] == "unknown"
    assert c["detail"] == "Could not be checked: the live home page did not load (no connection: TimeoutError)."
    assert len(tries) == 2 == health.SITE_TRIES and waits == [health.SITE_RETRY_WAIT]   # one more, never a third
    assert all(x["status"] == "ok" for k, x in checks.items() if k != "last-checked")
    report = health.build_report(NOW, list(checks.values()))
    gh = FakeGh(open_issues=[(7, "old\n<!-- site-health: activity -->\n")])
    assert health.sync_issue(report, run=gh).startswith("left #7 open") and gh.verbs() == [("issue", "list")]
    assert health.sync_issue(report, run=FakeGh()) == "no problem found, and no open issue"


def test_a_site_that_loads_on_the_second_try_is_checked_as_usual():
    answers, waits = [http_error(503), page(ago(hours=9))], []

    def flaky():
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    c = by_key(run_all(FakeGitHub(healthy_history()), site=flaky, sleep=waits.append))["last-checked"]
    assert c["status"] == "ok" and answers == [] and waits == [health.SITE_RETRY_WAIT]   # two requests, one wait


def test_a_site_that_answers_with_an_error_twice_names_the_last_error():
    answers = [urllib.error.URLError(TimeoutError("timed out")), http_error(503)]

    def down():
        raise answers.pop(0)
    c = by_key(run_all(FakeGitHub(healthy_history()), site=down, sleep=lambda s: None))["last-checked"]
    assert c["detail"] == "Could not be checked: the live home page did not load (HTTP 503)."


def test_a_failure_that_is_not_githubs_is_not_blamed_on_github():
    checks = by_key(run_all(FakeGitHub(healthy_history(), main=KeyError("commit"))))
    assert checks["activity"]["status"] == "unknown"
    assert checks["activity"]["detail"] == "Could not be checked: the check failed (KeyError)."


@pytest.mark.parametrize("broken, unknown", [
    ("/actions/workflows?", {"workflows"}), ("commits?sha=main", {"activity"}),
    ("update-and-deploy.yml/runs", {"scrape", "deploy"}), ("/jobs?", {"scrape", "deploy"}),
])
def test_a_github_read_that_fails_makes_only_its_own_checks_unknown(broken, unknown):
    checks = by_key(run_all(FakeGitHub(healthy_history(), broken=[broken])))
    assert {k for k, c in checks.items() if c["status"] == "unknown"} == unknown
    assert all(c["status"] == "ok" for k, c in checks.items() if k not in unknown)
    assert "GitHub's API did not answer (HTTP 502)" in checks[sorted(unknown)[0]]["detail"]


def test_the_check_only_reads_the_six_kinds_of_address_it_needs():
    github = FakeGitHub(healthy_history())
    run_all(github)
    assert github.calls == [
        f"/repos/{REPO}/actions/workflows?per_page=100",
        f"/repos/{REPO}/commits?sha=main&per_page=1",
        f"/repos/{REPO}/actions/workflows/update-and-deploy.yml/runs?branch=main&status=completed&per_page=100&page=1",
        f"/repos/{REPO}/actions/runs/3/jobs?per_page=100",
        f"/repos/{REPO}/actions/runs/2/jobs?per_page=100",
        f"/repos/{REPO}/commits?sha=data&per_page=1"]


# ---- the network code, with fake openers --------------------------------------------------------- #

class Resp:
    def __init__(self, body):
        self.body = body.encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, limit=None):
        return self.body


def test_api_sends_a_get_with_the_token_and_the_projects_user_agent():
    seen = []

    def opener(req, timeout):
        seen.append((req.get_method(), req.full_url, dict(req.header_items()), timeout))
        return Resp('{"ok": true}')
    assert health.api("/repos/o/r/commits?sha=main", "TOKEN", opener=opener) == {"ok": True}
    method, url, headers, timeout = seen[0]
    assert (method, url, timeout) == ("GET", "https://api.github.com/repos/o/r/commits?sha=main", health.TIMEOUT)
    assert headers["Authorization"] == "Bearer TOKEN" and headers["User-agent"] == health.USER_AGENT


def test_api_does_not_follow_a_redirect_so_the_token_never_leaves_githubs_api(monkeypatch):
    """urllib's own opener would repeat the request, Authorization header included, at the redirect's address."""
    seen = []

    class Redirecting(urllib.request.HTTPSHandler):
        def https_open(self, req):
            seen.append((req.full_url, dict(req.header_items())))
            headers = email.message.Message()
            headers["Location"] = "https://elsewhere.example/repos/o/r"
            resp = urllib.response.addinfourl(io.BytesIO(b"{}"), headers, req.full_url, 302)
            resp.msg = "Found"
            return resp
    real = health.no_redirect_opener
    monkeypatch.setattr(health, "no_redirect_opener", lambda: real(Redirecting()))
    with pytest.raises(urllib.error.HTTPError) as caught:
        health.api("/repos/o/r", "TOKEN")
    assert caught.value.code == 302
    assert [url for url, _ in seen] == ["https://api.github.com/repos/o/r"]   # the second address is never requested
    assert sum("TOKEN" in json.dumps(headers) for _, headers in seen) == 1

    def get(path):
        return health.api(path, "TOKEN")
    checks = by_key(health.run_checks(NOW, REPO, get, None, lambda: page(ago(hours=9)), SCHEDULED, sleep=never_wait))
    assert checks["activity"]["status"] == "unknown" and "HTTP 302" in checks["activity"]["detail"]
    assert all("elsewhere.example" not in url for url, _ in seen)


def test_job_log_asks_github_through_the_no_redirect_opener_so_the_token_stays_there(monkeypatch):
    """GitHub answers a log request with a redirect to another host. An opener that followed it would send the
    Authorization header there; the module's own opener hands the redirect back, and `plain` fetches it."""
    seen, fetched = [], []

    class Redirecting(urllib.request.HTTPSHandler):
        def https_open(self, req):
            seen.append((req.full_url, dict(req.header_items())))
            headers = email.message.Message()
            headers["Location"] = "https://logs.example/signed?sig=abc"
            resp = urllib.response.addinfourl(io.BytesIO(b""), headers, req.full_url, 302)
            resp.msg = "Found"
            return resp
    real = health.no_redirect_opener
    monkeypatch.setattr(health, "no_redirect_opener", lambda: real(Redirecting()))

    def plain(req, timeout):
        fetched.append((req.full_url, dict(req.header_items())))
        return Resp("2026-11-20T10:19:44.0000000Z run=true scope=all: a daily run\n")
    assert health.gate_scope(health.job_log(REPO, "TOKEN", 70, plain=plain)) == "all"
    assert [url for url, _ in seen] == [f"https://api.github.com/repos/{REPO}/actions/jobs/70/logs"]
    assert seen[0][1]["Authorization"] == "Bearer TOKEN"
    assert [url for url, _ in fetched] == ["https://logs.example/signed?sig=abc"]   # once, and only by `plain`
    assert "Authorization" not in fetched[0][1] and "TOKEN" not in json.dumps(fetched[0][1])


def test_a_jobs_log_is_downloaded_from_the_redirect_without_the_token():
    first, second = [], []

    def opener(req, timeout):
        first.append((req.full_url, dict(req.header_items())))
        raise http_error(302, "https://logs.example/signed?sig=abc")

    def plain(req, timeout):
        second.append((req.get_method(), req.full_url, dict(req.header_items())))
        return Resp("run=true scope=all: a daily run\n")
    assert health.gate_scope(health.job_log(REPO, "TOKEN", 70, opener=opener, plain=plain)) == "all"
    assert first[0][0] == f"https://api.github.com/repos/{REPO}/actions/jobs/70/logs"
    assert first[0][1]["Authorization"] == "Bearer TOKEN"
    method, url, headers = second[0]
    assert (method, url) == ("GET", "https://logs.example/signed?sig=abc")
    assert "Authorization" not in headers and "TOKEN" not in json.dumps(headers)


def test_a_log_redirect_to_plain_http_is_refused_and_other_errors_pass_through():
    def to_http(req, timeout):
        raise http_error(302, "http://logs.example/signed")
    with pytest.raises(ValueError):
        health.job_log(REPO, "TOKEN", 70, opener=to_http, plain=lambda *a, **k: pytest.fail("must not be fetched"))

    def gone(req, timeout):
        raise http_error(410)
    with pytest.raises(urllib.error.HTTPError):
        health.job_log(REPO, "TOKEN", 70, opener=gone, plain=lambda *a, **k: pytest.fail("must not be fetched"))


def test_a_failed_read_is_described_without_any_address():
    assert health.why(http_error(403)) == "HTTP 403"
    assert health.why(urllib.error.URLError(OSError("https://logs.example/signed?sig=abc"))) == "no connection: OSError"
    assert health.why(ValueError("https://logs.example/signed?sig=abc")) == "ValueError"


def test_the_site_is_fetched_with_one_request_and_the_projects_user_agent():
    seen = []

    def opener(req, timeout):
        seen.append((req.get_method(), req.full_url, dict(req.header_items())))
        return Resp(page(ago(hours=1)))
    assert health.parse_last_checked(health.fetch_site(opener=opener)) == ago(hours=1)
    assert [(m, u) for m, u, _ in seen] == [("GET", "https://depauwvswabash.com/")]   # fetch_site itself never repeats
    assert seen[0][2]["User-agent"] == health.USER_AGENT and "Authorization" not in seen[0][2]


def test_any_answer_but_200_from_the_site_is_a_failed_load():
    def no_content(req, timeout):
        resp = Resp("")
        resp.status = 204
        return resp
    with pytest.raises(urllib.error.HTTPError) as caught:
        health.fetch_site(opener=no_content)
    assert health.why(caught.value) == "HTTP 204"


def test_a_whole_check_makes_at_most_two_requests_to_the_site_and_only_when_the_first_fails():
    assert health.SITE_TRIES == 2 and health.SITE_RETRY_WAIT == 10            # the README says "after 10 seconds"
    assert "one more after 10 seconds if it fails" in (ROOT / "README.md").read_text(encoding="utf-8")
    loads = []
    run_all(FakeGitHub(healthy_history()), site=lambda: loads.append(1) or page(ago(hours=9)))
    assert loads == [1]


# ---- the report and the issue -------------------------------------------------------------------- #

def report(**status):
    """A report whose checks are ok unless named: report(activity="problem", deploy="unknown")."""
    titles = {"workflows": "Scheduled workflows are enabled", "activity": "Recent activity on main (GitHub's 60-day rule)",
              "scrape": "Last successful full scrape", "deploy": "Last successful deploy",
              "last-checked": "The live site's \"Last checked\" time", "data-branch": "The data branch is readable"}
    checks = [health.check(k, t, status.get(k.replace("-", "_"), "ok"), f"detail of {k}", f"todo of {k}")
              for k, t in titles.items()]
    return health.build_report(NOW, checks)


class FakeGh:
    def __init__(self, open_issues=(), labels=(), cannot_assign=False):
        self.open, self.labels, self.calls, self.cannot_assign = list(open_issues), list(labels), [], cannot_assign

    def __call__(self, *args, stdin=None):
        self.calls.append((args, stdin))
        if self.cannot_assign and args[:2] == ("issue", "create") and "--assignee" in args:
            raise subprocess.CalledProcessError(1, ["gh", *args], stderr="could not assign user: not found")
        if args[:2] == ("issue", "list"):
            return json.dumps([{"number": n, "body": body} for n, body in self.open])
        if args[:2] == ("label", "list"):
            return json.dumps([{"name": n} for n in self.labels])
        if args[:2] == ("issue", "create"):
            return "https://github.com/x/y/issues/9\n"
        return ""

    def verbs(self):
        return [a[:2] for a, _ in self.calls]


def test_the_summary_lists_every_check_and_what_to_do_about_each_problem():
    text = health.render_summary(report(activity="problem", deploy="unknown"))
    assert text.startswith("# Site health, Nov 20, 2026\n\n6 checks: 1 problem, 1 could not be checked, 4 OK.\n")
    assert "| Last successful deploy | Could not check | detail of deploy |" in text
    assert "| The data branch is readable | OK | detail of data-branch |" in text
    assert "**Recent activity on main (GitHub's 60-day rule): what to do.** todo of activity" in text
    assert "todo of deploy" not in text and "turns this run red" in text
    assert "turns this run red" not in health.render_summary(report())


def test_the_issue_body_says_what_to_do_first_and_ends_with_the_marker():
    r = health.build_report(NOW, [health.check_activity(NOW, commit(dt.datetime(2026, 10, 6, 15, tzinfo=dt.timezone.utc))),
                                  health.check_data_branch(commit(ago(days=6)))])
    body = health.render_issue_body(r, "https://run")
    assert body.startswith("The daily health check found 1 problem on Nov 20, 2026.")
    assert "## Recent activity on main (GitHub's 60-day rule)" in body
    assert "**What to do:** Merge any real pull request into `main`" in body
    assert "It must be merged before Dec 5, 2026, 10:00 AM EST" in body
    assert body.index("**What to do:**") < body.index("## Every check")
    assert "Run: https://run" in body and body.endswith("<!-- site-health: activity -->\n")
    assert "@" not in body                                                    # nobody is mentioned


def test_a_problem_opens_the_issue_with_the_label_created_if_missing():
    gh = FakeGh()
    assert health.sync_issue(report(activity="problem"), "https://run", run=gh) == "opened https://github.com/x/y/issues/9"
    assert gh.verbs() == [("issue", "list"), ("label", "list"), ("label", "create"), ("issue", "create")]
    create, body = gh.calls[3]
    assert create[create.index("--label") + 1] == "site-health" == health.LABEL
    assert create[create.index("--title") + 1] == health.ISSUE_TITLE and "todo of activity" in body
    gh = FakeGh(labels=["site-health"])
    health.sync_issue(report(activity="problem"), run=gh)
    assert gh.verbs() == [("issue", "list"), ("label", "list"), ("issue", "create")]


def test_the_same_problem_the_next_day_rewrites_the_issue_without_a_comment():
    gh = FakeGh(open_issues=[(7, "old text\n<!-- site-health: activity -->\n")])
    assert health.sync_issue(report(activity="problem"), "https://run", run=gh) == "updated #7 (the same 1 problem, no comment)"
    assert gh.verbs() == [("issue", "list"), ("issue", "edit")]
    assert gh.calls[1][0][2] == "7" and "todo of activity" in gh.calls[1][1]


def test_a_changed_list_of_problems_rewrites_the_issue_and_adds_one_comment():
    gh = FakeGh(open_issues=[(12, "x"), (7, "old\n<!-- site-health: activity -->\n")])
    out = health.sync_issue(report(activity="problem", deploy="problem"), "https://run", run=gh)
    assert out == "updated #7 and commented: the problems changed"            # the oldest open one; never a second issue
    assert gh.verbs() == [("issue", "list"), ("issue", "edit"), ("issue", "comment")]
    assert gh.calls[2][1] == ("Health check of Nov 20, 2026: the problems changed. Now: Recent activity on main "
                              "(GitHub's 60-day rule); Last successful deploy. https://run\n")


def test_a_check_that_cannot_be_made_today_is_not_a_change_in_the_problems():
    """Day 1: activity and deploy are problems. Day 2: the deploy cannot be checked. Day 3: it is a problem again.
    Neither day 2 nor day 3 may comment "the problems changed": nothing is known to have changed."""
    gh = FakeGh(open_issues=[(7, "old\n<!-- site-health: activity, deploy -->\n")])
    out = health.sync_issue(report(activity="problem", deploy="unknown"), "https://run", run=gh)
    assert out == "updated #7 (the same 2 problems, no comment)"
    assert gh.verbs() == [("issue", "list"), ("issue", "edit")]
    body = gh.calls[1][1]
    assert body.endswith("<!-- site-health: activity, deploy -->\n")          # deploy keeps its place in the marker
    assert f"## Last successful deploy\n\n{health.CARRIED}\n" in body
    assert health.CARRIED == "Could not be checked today; it was a problem at the last check that could be made."
    assert body.startswith("The daily health check found 1 problem on Nov 20, 2026.")
    assert "todo of deploy" not in body and "todo of activity" in body

    gh = FakeGh(open_issues=[(7, body)])                                       # the next day, from the body just written
    assert health.sync_issue(report(activity="problem", deploy="problem"), run=gh) == (
        "updated #7 (the same 2 problems, no comment)")
    assert gh.verbs() == [("issue", "list"), ("issue", "edit")]
    assert health.CARRIED not in gh.calls[1][1]


def test_a_carried_check_is_marked_in_the_comment_so_it_does_not_read_as_found_today():
    gh = FakeGh(open_issues=[(7, "old\n<!-- site-health: activity, deploy -->\n")])
    out = health.sync_issue(report(activity="problem", deploy="unknown", scrape="problem"), "https://run", run=gh)
    assert out == "updated #7 and commented: the problems changed"
    assert gh.calls[2][1] == (
        "Health check of Nov 20, 2026: the problems changed. Now: Recent activity on main (GitHub's 60-day rule); "
        "Last successful deploy (could not be checked today); Last successful full scrape. https://run\n")


def test_a_carried_check_that_passes_again_is_a_change_and_an_unknown_check_never_joins_the_list():
    gh = FakeGh(open_issues=[(7, "old\n<!-- site-health: activity, deploy -->\n")])
    assert health.sync_issue(report(activity="problem"), run=gh) == "updated #7 and commented: the problems changed"
    assert gh.calls[1][1].endswith("<!-- site-health: activity -->\n")
    gh = FakeGh(open_issues=[(7, "old\n<!-- site-health: activity -->\n")])    # scrape was never a listed problem
    assert health.sync_issue(report(activity="problem", scrape="unknown"), run=gh) == (
        "updated #7 (the same 1 problem, no comment)")
    assert gh.calls[1][1].endswith("<!-- site-health: activity -->\n") and health.CARRIED not in gh.calls[1][1]


def test_every_open_issue_is_closed_when_every_check_passes():
    gh = FakeGh(open_issues=[(12, "a second one, opened by hand"), (7, "old\n<!-- site-health: activity -->\n")])
    assert health.sync_issue(report(), "https://run", run=gh) == "closed #7, #12"
    assert gh.verbs() == [("issue", "list"), ("issue", "comment"), ("issue", "close"),
                          ("issue", "comment"), ("issue", "close")]
    assert [a[2] for a, _ in gh.calls[1:]] == ["7", "7", "12", "12"]


def test_every_check_passing_comments_and_closes_the_open_issue():
    gh = FakeGh(open_issues=[(7, "old\n<!-- site-health: activity -->\n")])
    assert health.sync_issue(report(), "https://run", run=gh) == "closed #7"
    assert gh.verbs() == [("issue", "list"), ("issue", "comment"), ("issue", "close")]
    assert gh.calls[1][1] == ("Health check of Nov 20, 2026: every check passes. 6 checks: 0 problems, 6 OK. "
                              "Closing. https://run\n")


def test_healthy_with_no_open_issue_touches_nothing():
    gh = FakeGh()
    assert health.sync_issue(report(), run=gh) == "no problem found, and no open issue"
    assert gh.verbs() == [("issue", "list")]


def test_a_check_that_could_not_be_made_never_opens_or_closes_the_issue():
    gh = FakeGh()
    assert health.sync_issue(report(deploy="unknown"), run=gh) == "no problem found, and no open issue"
    assert gh.verbs() == [("issue", "list")]
    gh = FakeGh(open_issues=[(7, "old\n<!-- site-health: activity -->\n")])
    assert health.sync_issue(report(deploy="unknown"), run=gh).startswith("left #7 open")
    assert gh.verbs() == [("issue", "list")]


# ---- reminder comments before the 60-day limit ---------------------------------------------------- #

LAST = dt.datetime(2026, 10, 1, 20, 28, tzinfo=dt.timezone.utc)          # the last commit: Oct 1, 2026, 4:28 PM EDT
LIMIT = LAST + health.DISABLE_AFTER                                       # Nov 30, 2026, 3:28 PM EST
LIMIT_ISO = "2026-11-30T20:28:00+00:00"
LIMIT_WORDS = "Nov 30, 2026, 3:28 PM EST"
DEPLOY_TITLE = "Last successful deploy"


def activity_report(left=None, day=None, last=LAST, deploy="ok", activity=None):
    """A report made `left` before the limit (or on day `day` after the commit), with the real activity check.
    `activity`: a status that replaces the real check ("unknown": it could not be made today)."""
    now = last + dt.timedelta(days=day) if day is not None else last + health.DISABLE_AFTER - left
    real = health.check_activity(now, commit(last))
    if activity:
        real = health.check("activity", real["title"], activity, "Could not be checked: GitHub's API did not answer.")
    checks = [real, health.check("deploy", DEPLOY_TITLE, deploy, "detail of deploy", "todo of deploy"),
              health.check_data_branch(commit(now - dt.timedelta(days=6)))]
    return json.loads(json.dumps(health.build_report(now, checks)))       # as the issues job reads it: from JSON


class Issue:
    """The one site-health issue over several runs: each run's fake gh is given the text the last run wrote."""
    def __init__(self, body=None, number=7):
        self.body, self.number, self.comments, self.calls = body, number, [], []

    def run(self, report, **kwargs):
        """One run of the issue command: (what it printed, the comments it added)."""
        gh = FakeGh(open_issues=[(self.number, self.body)] if self.body is not None else [], labels=["site-health"])
        out = health.sync_issue(report, "https://run", run=gh, **kwargs)
        added = [stdin for args, stdin in gh.calls if args[:2] == ("issue", "comment")]
        for args, stdin in gh.calls:
            if args[:2] in (("issue", "edit"), ("issue", "create")):
                self.body = stdin
        self.comments += added
        self.calls += gh.calls
        assert len(added) <= 1                                             # never two comments in one run
        return out, added

    def reminded(self):
        return health.REMINDED.findall(self.body)


def reminder(when, left):
    return (f"Health check of {when}: {left} left before GitHub's 60-day limit. Merge a real pull request into `main` "
            f"before {LIMIT_WORDS}, or the scheduled workflows are switched off. https://run\n")


def test_the_reminders_are_due_at_seven_and_at_two_days():
    assert health.REMIND_AT == (7, 2)
    days = lambda **span: health.due_threshold(dt.timedelta(**span))        # noqa: E731
    assert days(days=7, seconds=1) is None and days(days=15) is None
    assert days(days=7) == 7 and days(days=2, seconds=1) == 7
    assert days(days=2) == 2 and days(seconds=1) == 2
    assert days(seconds=0) is None and days(days=-1) is None                # the limit has passed


def test_a_daily_run_reminds_once_at_seven_days_and_once_at_two():
    issue = Issue()
    out, added = issue.run(activity_report(day=45))
    assert out.startswith("opened ") and added == [] and issue.reminded() == []
    said = {}
    for day in range(46, 60):
        out, added = issue.run(activity_report(day=day))
        said[day] = added
        assert issue.body.endswith("<!-- site-health: activity -->\n")
    assert [day for day, added in said.items() if added] == [53, 58]
    assert said[53] == [reminder("Nov 23, 2026", "7 days")] and said[58] == [reminder("Nov 28, 2026", "2 days")]
    assert issue.reminded() == [(LIMIT_ISO, "2")]
    assert f"<!-- site-health-reminded: {LIMIT_ISO} 2 -->\n<!-- site-health: activity -->\n" in issue.body
    assert all("@" not in text for text in issue.comments) and "@" not in issue.body      # nobody is mentioned


def test_the_run_that_reminds_says_so_and_the_next_day_is_quiet():
    issue = Issue()
    issue.run(activity_report(day=45))
    assert issue.run(activity_report(day=52))[0] == "updated #7 (the same 1 problem, no comment)"
    assert issue.run(activity_report(day=53))[0] == "updated #7 and commented: a reminder (7 days left)"
    assert issue.reminded() == [(LIMIT_ISO, "7")]
    assert issue.run(activity_report(day=54)) == ("updated #7 (the same 1 problem, no comment)", [])
    assert issue.reminded() == [(LIMIT_ISO, "7")]                           # the line survives the rewrite


def test_a_late_first_run_sends_the_seven_day_reminder_once_with_the_real_time_left():
    issue = Issue()
    issue.run(activity_report(day=45))
    out, added = issue.run(activity_report(left=dt.timedelta(days=5, hours=3)))   # GitHub dropped the runs between
    assert added == [reminder("Nov 25, 2026", "5 days")] and issue.reminded() == [(LIMIT_ISO, "7")]
    assert issue.run(activity_report(left=dt.timedelta(days=4, hours=3)))[1] == []
    assert issue.run(activity_report(left=dt.timedelta(days=1, hours=20)))[1] == [reminder("Nov 28, 2026", "1 day 20 hours")]


def test_a_jump_from_eight_days_left_to_one_sends_one_comment_not_two():
    issue = Issue()
    issue.run(activity_report(day=45))
    assert issue.run(activity_report(left=dt.timedelta(days=8)))[1] == []
    out, added = issue.run(activity_report(left=dt.timedelta(days=1)))
    assert added == [reminder("Nov 29, 2026", "1 day")] and issue.reminded() == [(LIMIT_ISO, "2")]
    assert issue.run(activity_report(left=dt.timedelta(hours=12)))[1] == []
    assert len(issue.comments) == 1


def test_opening_the_issue_is_the_notification_so_a_threshold_due_then_is_recorded_without_a_comment():
    issue = Issue()                                                         # no issue yet, 3 and a half days left
    out, added = issue.run(activity_report(left=dt.timedelta(days=3, hours=12)))
    assert out.startswith("opened ") and added == [] and issue.reminded() == [(LIMIT_ISO, "7")]
    assert issue.run(activity_report(left=dt.timedelta(days=2, hours=12)))[1] == []          # the next day: nothing
    assert issue.run(activity_report(left=dt.timedelta(days=1, hours=12)))[1] == [           # the smaller one still comes
        reminder("Nov 29, 2026", "1 day 12 hours")]
    opened_late = Issue()
    opened_late.run(activity_report(left=dt.timedelta(hours=30)))
    assert opened_late.reminded() == [(LIMIT_ISO, "2")] and opened_late.comments == []
    assert opened_late.run(activity_report(left=dt.timedelta(hours=6)))[1] == []


def test_a_new_limit_starts_the_reminders_again():
    issue = Issue()
    issue.run(activity_report(day=45))
    issue.run(activity_report(day=58))
    assert issue.reminded() == [(LIMIT_ISO, "2")]
    later = LAST + dt.timedelta(days=58, hours=1)                           # a commit moved the limit; other problems last
    out, added = issue.run(activity_report(day=1, last=later, deploy="problem"))            # activity passes again
    assert issue.reminded() == [] and "the problems changed" in added[0] and "left before" not in added[0]
    out, added = issue.run(activity_report(day=53, last=later, deploy="problem"))
    assert "the problems changed" in added[0] and "Reminder: 7 days left before GitHub's 60-day limit." in added[0]
    assert issue.reminded() == [("2027-01-27T21:28:00+00:00", "7")]
    stale = Issue(f"old\n<!-- site-health-reminded: {LIMIT_ISO} 2 -->\n<!-- site-health: activity -->\n")
    out, added = stale.run(activity_report(day=53, last=later))             # the old limit's line covers nothing here
    assert len(added) == 1 and stale.reminded() == [("2027-01-27T21:28:00+00:00", "7")]


def test_nothing_is_sent_once_the_limit_has_passed():
    issue = Issue()
    issue.run(activity_report(day=45))
    assert issue.run(activity_report(day=60))[1] == []                      # the limit itself: no time is left
    assert issue.run(activity_report(day=61))[1] == [] and issue.run(activity_report(day=75))[1] == []
    fresh = Issue()
    fresh.run(activity_report(day=63))                                      # opened after the limit: nothing to record
    assert fresh.reminded() == [] and fresh.comments == []


def test_activity_unknown_today_sends_nothing_and_keeps_the_line():
    issue = Issue()
    issue.run(activity_report(day=45, deploy="problem"))
    issue.run(activity_report(day=53, deploy="problem"))
    assert issue.reminded() == [(LIMIT_ISO, "7")] and len(issue.comments) == 1
    out, added = issue.run(activity_report(day=58, deploy="problem", activity="unknown"))   # a reminder would be due
    assert added == [] and out == "updated #7 (the same 2 problems, no comment)"
    assert issue.reminded() == [(LIMIT_ISO, "7")] and health.CARRIED in issue.body
    assert issue.body.endswith("<!-- site-health: activity, deploy -->\n")
    out, added = issue.run(activity_report(left=dt.timedelta(days=1), deploy="problem"))     # checked again: now it is sent
    assert added == [reminder("Nov 29, 2026", "1 day")] and issue.reminded() == [(LIMIT_ISO, "2")]


def test_a_reminder_and_a_change_in_the_problems_share_one_comment():
    issue = Issue()
    issue.run(activity_report(day=45))
    out, added = issue.run(activity_report(day=53, deploy="problem"))
    assert out == "updated #7 and commented: the problems changed and a reminder (7 days left)"
    assert added == [
        "Health check of Nov 23, 2026: the problems changed. Now: Recent activity on main (GitHub's 60-day rule); "
        "Last successful deploy. Reminder: 7 days left before GitHub's 60-day limit. Merge a real pull request into "
        f"`main` before {LIMIT_WORDS}, or the scheduled workflows are switched off. https://run\n"]
    assert [a[:2] for a, _ in issue.calls].count(("issue", "comment")) == 1


def test_no_reminder_without_a_limit_in_the_report_or_while_the_check_passes():
    old_format = report(activity="problem")                                 # a report from before the field existed
    assert health.plan_reminder(old_format, "x") == ("", None, None)
    line = f"<!-- site-health-reminded: {LIMIT_ISO} 7 -->"
    passing = activity_report(day=10, deploy="problem")
    assert health.plan_reminder(passing, line) == ("", None, None)          # and the line is dropped
    edited = f"<!-- site-health-reminded: not-a-time 7 -->"                 # a line someone changed by hand
    assert health.plan_reminder(activity_report(day=53), edited)[1] == 7


# ---- the issue is assigned to the repository's owner when it is opened ---------------------------- #

def test_a_new_issue_is_assigned_to_the_owner_given():
    gh = FakeGh(labels=["site-health"])
    out = health.sync_issue(report(activity="problem"), "https://run", run=gh, assignee="octo-owner")
    assert out == "opened https://github.com/x/y/issues/9, assigned to the repository's owner"
    create = gh.calls[-1][0]
    assert create[:2] == ("issue", "create") and create[create.index("--assignee") + 1] == "octo-owner"
    assert gh.verbs().count(("issue", "create")) == 1
    assert "@" not in gh.calls[-1][1] and "octo-owner" not in gh.calls[-1][1]      # assigned, not mentioned


def test_no_owner_given_means_no_assignee():
    gh = FakeGh(labels=["site-health"])
    assert health.sync_issue(report(activity="problem"), run=gh) == "opened https://github.com/x/y/issues/9"
    assert all("--assignee" not in args for args, _ in gh.calls)


def test_an_owner_who_cannot_be_assigned_never_costs_the_issue():
    gh = FakeGh(labels=["site-health"], cannot_assign=True)                 # an organization, say
    out = health.sync_issue(report(activity="problem"), "https://run", run=gh, assignee="octo-org")
    assert out == ("opened https://github.com/x/y/issues/9 (it could not be assigned to the repository's owner, so "
                   "it was opened unassigned)")
    assert gh.verbs() == [("issue", "list"), ("label", "list"), ("issue", "create"), ("issue", "create")]
    first, second = gh.calls[2], gh.calls[3]
    assert "--assignee" in first[0] and "--assignee" not in second[0]
    assert first[1] == second[1] and "todo of activity" in second[1]         # the same text both times


def test_rewriting_commenting_on_or_closing_an_open_issue_never_assigns():
    body = "old\n<!-- site-health: activity -->\n"
    for todays in (report(activity="problem"), report(activity="problem", deploy="problem"), report()):
        gh = FakeGh(open_issues=[(7, body)])
        health.sync_issue(todays, "https://run", run=gh, assignee="octo-owner")
        assert len(gh.calls) > 1
        for args, stdin in gh.calls:
            assert not any("assignee" in a for a in args) and "octo-owner" not in (stdin or "")
    assert "add-assignee" not in SOURCE and SOURCE.count('"--assignee", assignee') == 1


def test_the_owner_comes_from_the_runners_environment_not_from_the_code(tmp_path, monkeypatch, capsys):
    path = tmp_path / "r.json"
    path.write_text(json.dumps(report(activity="problem")), encoding="utf-8")
    seen = []
    monkeypatch.setattr(health, "sync_issue", lambda r, url, assignee="": seen.append(assignee) or "opened x")
    monkeypatch.setattr(health, "now_utc", lambda: NOW)
    assert health.main(["issue", "--report", str(path)]) == 0               # the test guard removes GITHUB_* variables
    monkeypatch.setenv("GITHUB_REPOSITORY_OWNER", "octo-owner")
    assert health.main(["issue", "--report", str(path)]) == 0
    assert health.main(["issue", "--report", str(path), "--assignee", ""]) == 0
    assert seen == ["", "octo-owner", ""]
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "--assignee" not in text and "GITHUB_REPOSITORY_OWNER" not in text    # Actions sets it; nothing to pass


# ---- the command line ----------------------------------------------------------------------------- #

def test_check_writes_the_report_and_the_outputs_and_exits_0_even_with_a_problem(tmp_path, monkeypatch, capsys):
    github = FakeGitHub(healthy_history(), main=commit(ago(days=50)))
    monkeypatch.setattr(health, "api", lambda path, token: github.get(path))
    monkeypatch.setattr(health, "job_log", lambda repo, token, job_id: github.history.log_of(job_id))
    monkeypatch.setattr(health, "fetch_site", lambda: page(ago(hours=9)))
    monkeypatch.setattr(health, "now_utc", lambda: NOW)
    monkeypatch.setenv("GH_TOKEN", "TOKEN")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output.txt"))
    assert health.main(["check", "--repo", REPO, "--out", str(tmp_path / "out")]) == 0
    saved = json.loads((tmp_path / "out" / "health-report.json").read_text(encoding="utf-8"))
    assert saved["totals"] == {"ok": 5, "problem": 1, "unknown": 0} and health.problem_keys(saved) == ["activity"]
    assert (tmp_path / "out" / "summary.md").read_text(encoding="utf-8") == health.render_summary(saved)
    assert (tmp_path / "output.txt").read_text(encoding="utf-8") == "reported=true\nproblems=1\nunknown=0\n"
    assert "TOKEN" not in capsys.readouterr().out and "TOKEN" not in json.dumps(saved)


def test_check_needs_a_repository_and_a_token(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)              # the test guard removes GH_TOKEN and GITHUB_TOKEN
    assert health.main(["check"]) == 2 and "needs --repo" in capsys.readouterr().err


def test_verdict_is_red_only_for_a_check_that_could_not_be_made(tmp_path, capsys):
    path = tmp_path / "r.json"
    path.write_text(json.dumps(report(activity="problem")), encoding="utf-8")
    assert health.main(["verdict", "--report", str(path)]) == 0               # a problem goes to the issue, not here
    path.write_text(json.dumps(report(deploy="unknown")), encoding="utf-8")
    assert health.main(["verdict", "--report", str(path)]) == 1
    assert "::error::The health check could not make 1 check: Last successful deploy." in capsys.readouterr().out


def test_issue_reads_the_saved_report(tmp_path, monkeypatch, capsys):
    path = tmp_path / "r.json"
    path.write_text(json.dumps(report(activity="problem")), encoding="utf-8")
    seen = []
    monkeypatch.setattr(health, "sync_issue",
                        lambda r, url, assignee="": seen.append((health.problem_keys(r), url)) or "opened x")
    monkeypatch.setattr(health, "now_utc", lambda: NOW + dt.timedelta(minutes=2))
    assert health.main(["issue", "--report", str(path), "--run-url", "https://run"]) == 0
    assert seen == [(["activity"], "https://run")] and "opened x" in capsys.readouterr().out


def test_issue_ignores_an_old_report(tmp_path, monkeypatch, capsys):
    """Re-running the issues job of an old run would apply that day's findings to today's issue."""
    path = tmp_path / "r.json"
    path.write_text(json.dumps(report(activity="problem")), encoding="utf-8")
    monkeypatch.setattr(health, "sync_issue",
                        lambda r, url, assignee="": pytest.fail("an old report must not touch the issue"))
    monkeypatch.setattr(health, "now_utc", lambda: NOW + health.REPORT_MAX_AGE + dt.timedelta(seconds=1))
    assert health.main(["issue", "--report", str(path)]) == 0
    assert "more than 6 hours ago: the issue was not touched" in capsys.readouterr().out
    seen = []
    monkeypatch.setattr(health, "sync_issue", lambda r, url, assignee="": seen.append(1) or "ok")
    monkeypatch.setattr(health, "now_utc", lambda: NOW + health.REPORT_MAX_AGE)       # the limit itself still counts
    assert health.main(["issue", "--report", str(path)]) == 0 and seen == [1]


def test_the_list_of_what_stops_is_the_same_in_the_issue_and_the_readme():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert health.SCHEDULED_WORK in readme
    for name in ("update", "watcher", "link check", "snapshot", "health check"):   # one phrase per scheduled workflow
        assert name in health.SCHEDULED_WORK
    assert len(SCHEDULED) == 5


# ---- what the module and the workflow may and may not do ------------------------------------------ #

SOURCE = Path(health.__file__).read_text(encoding="utf-8")


def test_the_module_needs_no_package_to_import():
    """Both jobs run it with the runner's own python3: nothing is installed."""
    tree = ast.parse(SOURCE)
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    names = {a.name.split(".")[0] for n in top if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module.split(".")[0] for n in top if isinstance(n, ast.ImportFrom)}
    assert names <= set(__import__("sys").stdlib_module_names) | {"__future__", "timeutil", "politeness"}


def test_it_talks_only_to_githubs_api_and_our_own_site_never_to_the_schools():
    assert set(re.findall(r"https://[\w.-]+", SOURCE)) == {"https://api.github.com", "https://depauwvswabash.com"}
    for host in ("depauwtigers", "wabash.edu", *render.SOURCE_HOSTS):
        assert host not in SOURCE and host not in WORKFLOW.read_text(encoding="utf-8").replace("schools' sites", "")
    assert not re.search(r"^\s*(import|from) (scrape|linkcheck|snapshot|databranch|watcher)\b", SOURCE, re.MULTILINE)


def test_it_only_ever_reads_it_never_enables_starts_or_commits_anything():
    assert set(re.findall(r'method="(\w+)"', SOURCE)) == {"GET"}
    assert SOURCE.count("urllib.request.Request(") == SOURCE.count('method="GET"') == 4
    for word in ("/enable", "/dispatches", "/disable", "git push", "commit -", "git commit"):
        assert word not in SOURCE and word not in WORKFLOW.read_text(encoding="utf-8")
    verbs = set(re.findall(r'run\("(\w+)", "(\w+)"', SOURCE))                 # the only gh commands, all about the issue
    assert verbs == {("issue", "list"), ("issue", "comment"), ("issue", "close"), ("issue", "edit"),
                     ("issue", "create"), ("label", "list"), ("label", "create")}


def workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_the_workflow_runs_once_a_day_and_by_hand_only():
    wf = workflow()
    triggers = wf[True]                                                                   # YAML reads `on` as True
    assert set(triggers) == {"schedule", "workflow_dispatch"} and triggers["workflow_dispatch"] is None
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request" not in text and "push:" not in text
    [entry] = triggers["schedule"]
    minute, hour, day, month, weekday = entry["cron"].split()
    assert (day, month, weekday) == ("*", "*", "*") and hour.isdigit()                    # every day, once
    assert minute.isdigit() and minute != "0"                                             # not on the hour
    # Outside 6 to 9 AM Pacific in both PST (14:00-17:00 UTC) and PDT (13:00-16:00 UTC).
    assert not 13 <= int(hour) <= 17
    others = re.findall(r'cron: "(\d+)[ ,]', "".join(
        f.read_text(encoding="utf-8") for f in WORKFLOW.parent.glob("*.yml") if f != WORKFLOW))
    assert minute not in others                                                           # its own minute


def test_the_workflow_permissions_are_minimal():
    wf = workflow()
    text = WORKFLOW.read_text(encoding="utf-8")
    assert wf["permissions"] == {"contents": "read"}
    assert wf["concurrency"] == {"group": "health-check", "cancel-in-progress": False}
    assert set(wf["jobs"]) == {"check", "issues"}
    assert wf["jobs"]["check"]["permissions"] == {"contents": "read", "actions": "read"}
    assert wf["jobs"]["issues"]["permissions"] == {"contents": "read", "issues": "write"}
    assert ": write" not in text.replace("issues: write", "", 1)                          # the one write permission
    assert "secrets." not in text and text.count("github.token") == 2
    assert "refs/heads/main" in wf["jobs"]["issues"]["if"]                                 # a branch run opens no issue
    for job in wf["jobs"].values():
        assert job["runs-on"] == "ubuntu-24.04" and 0 < job["timeout-minutes"] <= 10
        assert job["steps"][0]["with"]["persist-credentials"] is False
        assert "pip install" not in json.dumps(job) and "npm" not in json.dumps(job)


def test_the_check_job_reports_before_it_can_turn_red_and_the_issue_job_still_runs():
    wf = workflow()
    steps = wf["jobs"]["check"]["steps"]
    runs = [s.get("run", "") + s.get("uses", "") for s in steps]

    def at(text):
        return next(i for i, r in enumerate(runs) if text in r)
    assert (at("scraper/health.py check") < at("actions/upload-artifact") < at("GITHUB_STEP_SUMMARY")
            < at("scraper/health.py verdict") == len(steps) - 1)
    assert steps[0]["with"]["sparse-checkout"].split() == ["scraper", ".github/workflows"]   # the schedule list needs them
    assert wf["jobs"]["check"]["outputs"] == {"reported": "${{ steps.health.outputs.reported }}"}
    issues = wf["jobs"]["issues"]
    assert issues["needs"] == "check" and "needs.check.outputs.reported == 'true'" in issues["if"]
    assert "!cancelled()" in issues["if"]
    assert "health.py issue" in "".join(s.get("run", "") for s in issues["steps"])


def test_every_action_in_the_workflow_is_pinned_to_a_full_sha():
    uses = re.findall(r"uses: (\S+)(.*)", WORKFLOW.read_text(encoding="utf-8"))
    assert len(uses) == 4
    for action, comment in uses:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", action) and re.search(r"# v\d", comment), action
    pinned_elsewhere = "".join(f.read_text(encoding="utf-8") for f in WORKFLOW.parent.glob("*.yml") if f != WORKFLOW)
    for action, _ in uses:
        assert action in pinned_elsewhere                                                  # the same pins as the other workflows
