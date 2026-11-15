"""The daily health check (scraper/health.py, .github/workflows/health-check.yml).

No network, no clock and no outside programs: every check gets a fixed `now`, GitHub's API is a fake that answers
from a table, the live page is a string, and the issue sync gets a fake gh runner."""

import ast
import datetime as dt
import email.message
import json
import re
import urllib.error
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


@pytest.mark.parametrize("state", ["disabled_inactivity", "disabled_manually"])
def test_a_scheduled_workflow_that_is_off_is_a_problem_that_says_how_to_enable_it(state):
    c = health.check_workflows(SCHEDULED, listed(update_and_deploy=state, link_check=state))
    assert c["status"] == "problem"
    assert f"`update-and-deploy.yml` is {state.replace('_', ' ')}" in c["detail"] and "`link-check.yml`" in c["detail"]
    assert "`gh workflow enable link-check.yml` and `gh workflow enable update-and-deploy.yml`" in c["todo"]
    assert "Enable workflow" in c["todo"] and "gameday-watcher" not in c["detail"]


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


def test_the_warning_starts_at_exactly_45_days():
    assert health.check_activity(NOW, commit(ago(days=45) + dt.timedelta(seconds=1)))["status"] == "ok"
    c = health.check_activity(NOW, commit(ago(days=45)))
    assert c["status"] == "problem"


def test_the_warning_says_what_to_do_and_the_date_by_which():
    last = dt.datetime(2026, 10, 6, 15, 0, tzinfo=dt.timezone.utc)          # 45 days and a few hours before NOW
    c = health.check_activity(NOW, commit(last))
    assert c["status"] == "problem"
    assert "from Oct 6, 2026, 45 days ago" in c["detail"]
    assert "that is Dec 5, 2026, 14 days from now" in c["detail"]            # Oct 6 + 60 days
    assert "Merge any real pull request into `main`" in c["todo"] and "Dependabot pull request" in c["todo"]
    assert "It must happen by Dec 5, 2026" in c["todo"]
    assert "Do not make an empty commit" in c["todo"]


def test_the_date_is_the_eastern_date_of_the_commit_plus_60_days():
    late = dt.datetime(2026, 10, 2, 2, 30, tzinfo=dt.timezone.utc)           # Oct 1, 10:30 PM EDT
    c = health.check_activity(NOW, commit(late))
    assert "from Oct 1, 2026" in c["detail"] and "that is Nov 30, 2026" in c["detail"]


def test_after_60_days_it_says_the_workflows_may_already_be_off():
    assert "days from now" in health.check_activity(NOW, commit(ago(days=60) + dt.timedelta(seconds=1)))["detail"]
    c = health.check_activity(NOW, commit(ago(days=60)))
    assert c["status"] == "problem" and "that limit passed on Nov 20, 2026" in c["detail"]
    assert "may already be off" in c["detail"] and "enable every workflow that is off" in c["todo"]
    assert "a commit does not switch them back on" in c["todo"]


# ---- the three 48-hour checks ------------------------------------------------------------------- #

@pytest.mark.parametrize("make", [
    lambda when: health.check_scrape(NOW, when, "in the last 7 days (9 runs looked at)"),
    lambda when: health.check_deploy(NOW, when, "in the last 7 days (9 runs looked at)"),
    lambda when: health.check_last_checked(
        NOW, f'<p>Data last changed <time datetime="2026-11-14T21:25:16+00:00">Nov 14, 4:25 PM</time> · Last checked '
             f'<time datetime="{when.isoformat()}">x</time> (Eastern time).</p>' if when else "<p>no time here</p>"),
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


def test_last_checked_is_read_from_a_page_the_renderer_really_writes(tmp_path):
    _, page = build_site(tmp_path, {"football": football()}, checked_at="2026-11-19T10:19:44+00:00",
                         now=dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.timezone.utc))
    assert health.parse_last_checked(page) == dt.datetime(2026, 11, 19, 10, 19, 44, tzinfo=dt.timezone.utc)
    assert health.check_last_checked(NOW, page)["status"] == "ok"
    _, without = build_site(tmp_path, {"football": football()}, now=dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.timezone.utc))
    assert health.parse_last_checked(without) is None
    c = health.check_last_checked(NOW, without)
    assert c["status"] == "problem" and "shows no \"Last checked\" time" in c["detail"]


def test_a_live_page_that_does_not_load_is_a_problem():
    c = health.check_last_checked(NOW, None, "HTTP 503")
    assert c["status"] == "problem" and c["detail"] == "The live home page did not load: HTTP 503."
    assert health.SITE_URL in c["todo"]
    assert health.SITE_URL.rstrip("/") == render.SITE_URL.rstrip("/")


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
        return self.logs[job_id]

    def find(self):
        return health.find_latest(NOW, self.runs, self.jobs_of, self.log_of)


def test_the_newest_full_scrape_and_the_newest_deploy_are_found_and_the_search_stops_there():
    h = Runs()
    h.add(9, ago(hours=1), scope="")                         # a 30-minute run the gate ended
    h.add(8, ago(hours=2), scope="push")                     # a push: deployed, read nothing
    h.add(7, ago(hours=5), scope="football")                 # a game-day update: one sport only
    h.add(6, ago(hours=9), scope="all")                      # the daily run
    h.add(5, ago(hours=20), scope="all")
    scrape, deploy, looked = h.find()
    assert deploy == ago(hours=2) and scrape == ago(hours=9, minutes=3) and looked == 4
    assert h.asked_jobs == [9, 8, 7, 6]                      # run 5 is never read
    assert h.asked_logs == [70, 60]                          # a gate log only where the Scrape step succeeded


def test_a_game_day_update_is_not_a_full_scrape():
    h = Runs().add(3, ago(hours=1), scope="football").add(2, ago(hours=2), scope="football,mens-soccer")
    scrape, deploy, _ = h.find()
    assert scrape is None and deploy == ago(hours=1)


def test_a_failed_scrape_or_a_red_run_is_not_a_successful_full_scrape_but_its_deploy_counts():
    h = Runs()
    h.add(4, ago(hours=1), scope="all", scraped=False, conclusion="failure")     # the scraper failed; fallback data deployed
    h.add(3, ago(hours=2), scope="all", conclusion="failure")                      # scraped, then turned red (validation, say)
    h.add(2, ago(hours=30), scope="all")
    scrape, deploy, _ = h.find()
    assert deploy == ago(hours=1) and scrape == ago(hours=30, minutes=3)
    assert h.asked_logs == [20]                               # the red runs' logs are never read


def test_a_run_whose_deploy_did_not_happen_does_not_count_as_a_deploy():
    h = Runs().add(2, ago(hours=1), scope="all", deployed=False).add(1, ago(hours=50), scope="all")
    scrape, deploy, _ = h.find()
    assert scrape == ago(hours=1, minutes=3) and deploy == ago(hours=50)


def test_the_search_goes_back_seven_days_and_no_further():
    h = Runs().add(2, ago(days=1), scope="").add(1, ago(days=8), scope="all")
    assert h.find() == (None, None, 1) and h.asked_jobs == [2]
    assert health.LOOKBACK == dt.timedelta(days=7)


def test_no_runs_at_all_finds_nothing():
    assert health.find_latest(NOW, [], None, None) == (None, None, 0)


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
            assert "branch=main" in path and "status=completed" in path
            return {"workflow_runs": self.history.runs}
        m = re.fullmatch(rf"/repos/{REPO}/actions/runs/(\d+)/jobs\?per_page=100", path)
        assert m, path
        return {"jobs": self.history.jobs_of(int(m.group(1)))}


def page(when):
    return f'<p>Last checked <time datetime="{when.isoformat()}">x</time> (Eastern time).</p>'


def healthy_history():
    return Runs().add(3, ago(hours=1), scope="push").add(2, ago(hours=9), scope="all")


def run_all(github, site=lambda: page(ago(hours=9))):
    return health.run_checks(NOW, REPO, github.get, github.history.log_of, site, SCHEDULED)


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


def test_a_site_that_cannot_be_fetched_is_a_problem():
    def down():
        raise urllib.error.URLError(TimeoutError("timed out"))
    c = by_key(run_all(FakeGitHub(healthy_history()), site=down))["last-checked"]
    assert c["status"] == "problem" and "did not load: no connection (TimeoutError)" in c["detail"]


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
        f"/repos/{REPO}/actions/workflows/update-and-deploy.yml/runs?branch=main&status=completed&per_page=100",
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
    assert health.why(urllib.error.URLError(OSError("https://logs.example/signed?sig=abc"))) == "no connection (OSError)"
    assert health.why(ValueError("https://logs.example/signed?sig=abc")) == "ValueError"


def test_the_site_is_fetched_once_with_the_projects_user_agent():
    seen = []

    def opener(req, timeout):
        seen.append((req.get_method(), req.full_url, dict(req.header_items())))
        return Resp(page(ago(hours=1)))
    assert health.parse_last_checked(health.fetch_site(opener=opener)) == ago(hours=1)
    assert [(m, u) for m, u, _ in seen] == [("GET", "https://depauwvswabash.com/")]
    assert seen[0][2]["User-agent"] == health.USER_AGENT and "Authorization" not in seen[0][2]


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
    def __init__(self, open_issues=(), labels=()):
        self.open, self.labels, self.calls = list(open_issues), list(labels), []

    def __call__(self, *args, stdin=None):
        self.calls.append((args, stdin))
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
    assert "**What to do:** Merge any real pull request into `main`" in body and "It must happen by Dec 5, 2026" in body
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
    monkeypatch.setattr(health, "sync_issue", lambda r, url: seen.append((health.problem_keys(r), url)) or "opened x")
    assert health.main(["issue", "--report", str(path), "--run-url", "https://run"]) == 0
    assert seen == [(["activity"], "https://run")] and "opened x" in capsys.readouterr().out


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
