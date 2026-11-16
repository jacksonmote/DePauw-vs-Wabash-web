"""
The daily health check: is the site still updating, and will it keep updating?

Why it exists: the site is quiet for months at a time (no games from late spring to late summer), and a site that
stops updating fails silently. Worse, in a public repository GitHub switches every scheduled workflow off after 60
days without repository activity, which would stop the twice-daily update, the game-day watcher, the monthly link
check, the twice-a-year data snapshot and this health check (SCHEDULED_WORK). Nothing in the repository can undo
that by itself, so this check warns early instead: when the last commit on main is WARN_AFTER old, it opens an
issue that says what to do (merge any real pull request) and before which moment, and it adds a reminder comment
when 7 and when 2 days are left (REMIND_AT).

What it checks, once a day (.github/workflows/health-check.yml), from GitHub's API, the workflow files and one
request per run to our own home page (one more if it fails):

  workflows     every workflow file with a schedule is enabled ("active")
  activity      the last commit on main is younger than WARN_AFTER (45 days; GitHub's limit is DISABLE_AFTER, 60)
  full scrape   the last successful run of "Update data and deploy site" that read every sport (a daily or
                hand-started run, or a push that changed the scraper) is at most MAX_AGE old (48 hours)
  deploy        the last successful deploy is at most MAX_AGE old
  last checked  the live home page's "Last checked" time is at most MAX_AGE old
  data branch   the data branch exists and its last commit can be read

What it never does: fetch the schools' sites, commit, push, deploy, or enable, disable or start a workflow. It
holds no write permission except, in its own `issues` job, the one to keep the `site-health` issue. It does not
try to keep the schedules alive by itself (no enable call, no empty commit): the fix for 60 days without activity
is a real commit on main, made by a person.

Each check is "ok", a "problem" or "unknown" (it could not be made: GitHub's API did not answer, a job's log
could not be downloaded, or the live page did not load, also on the second try). A problem opens or updates the
one open issue labeled `site-health` (assigned to the repository's owner when it is opened, mentioning nobody);
when every check is ok the issue is closed with a comment. An unknown check
never opens or closes the issue, and never counts as a change in the problems; it turns the run red instead, so a
red run means the check itself could not finish, and an open issue means it found something.

Commands (standard library only, like the gate and the watcher):

  python scraper/health.py check [--repo OWNER/NAME] [--out DIR]   make every check, write the report (read-only)
  python scraper/health.py issue --report FILE [--run-url URL] [--assignee LOGIN]
                                                                   open, update or close the issue (needs gh)
  python scraper/health.py verdict --report FILE                   exit 1 when a check could not be made

`check` needs a token in GH_TOKEN or GITHUB_TOKEN that may read the repository and its Actions runs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from politeness import USER_AGENT  # the project's one user agent; standard library only
from timeutil import to_eastern

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
OUT = ROOT / "health-check"

SITE_URL = "https://depauwvswabash.com/"
UPDATE_WORKFLOW = "update-and-deploy.yml"
MAIN_BRANCH, DATA_BRANCH = "main", "data"

MAX_AGE = dt.timedelta(hours=48)        # the last full scrape, the last deploy and "Last checked" may be this old
WARN_AFTER = dt.timedelta(days=45)      # the last commit on main: warn from this age
DISABLE_AFTER = dt.timedelta(days=60)   # GitHub's limit: scheduled workflows go off after this long without activity
LOOKBACK = dt.timedelta(days=7)         # how far back the update runs are searched
PAGE_SIZE = 100                         # runs per request (the API's largest page)
MAX_PAGES = 5                           # and at most this many pages; the report says so when this stops the search
MAX_BYTES = 5_000_000                   # the most that is read of the home page or of a job's log
TIMEOUT = 30                            # seconds, every request
SITE_TRIES = 2                          # the home page: one request, one more if it fails
SITE_RETRY_WAIT = 10                    # seconds before that second request
REPORT_MAX_AGE = dt.timedelta(hours=6)  # the issue command ignores a report older than this (a re-run old job)
REMIND_AT = (7, 2)                      # days left before GitHub's limit: a reminder comment at each

# What stops when GitHub switches the scheduled workflows off (the README uses the same words; a test checks).
SCHEDULED_WORK = ("the twice-daily update, the game-day watcher, the monthly link check, the twice-a-year data "
                  "snapshot and this health check")

# Names in update-and-deploy.yml that the run search relies on (a test checks they are still there).
GATE_JOB, BUILD_JOB, DEPLOY_JOB, SCRAPE_STEP = "gate", "build", "deploy", "Scrape"
# The line gameday.py's gate prints, at the start of a log line (GitHub puts a timestamp before it).
SCOPE_LINE = re.compile(r"^\ufeff?(?:\d{4}-\d\d-\d\dT[\d:.]+Z )?run=(true|false) scope=(\S+?): ", re.MULTILINE)
FULL_SCOPE = "all"
LAST_CHECKED = re.compile(r'Last checked <time datetime="([^"]+)"')  # as render.py writes it in the footer
OUR_PAGE = "Data last changed"                                       # every home page of ours has this footer text
CRON_LINE = re.compile(r"^\s*-\s*cron:", re.MULTILINE)
STATE_WORDS = {"disabled_inactivity": "switched off by GitHub for inactivity",
               "disabled_manually": "switched off by hand"}

OK, PROBLEM, UNKNOWN = "ok", "problem", "unknown"
WORDS = {OK: "OK", PROBLEM: "Problem", UNKNOWN: "Could not check"}

LABEL = "site-health"
LABEL_COLOR = "d93f0b"
LABEL_DESC = "The daily health check found a problem"
ISSUE_TITLE = "Site health: a check needs attention"
MARKER = re.compile(r"<!-- site-health: ([a-z, -]*) -->")
# The reminders already sent, kept in the issue's text: the limit they belong to and the smallest threshold covered.
REMINDED = re.compile(r"<!-- site-health-reminded: (\S+) (\d+) -->")
CARRIED = "Could not be checked today; it was a problem at the last check that could be made."
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# ---- small helpers ------------------------------------------------------------------------------ #

def parse_time(text: str) -> dt.datetime:
    """An ISO time from GitHub's API or the page ("2026-11-14T21:25:47Z"), always aware."""
    when = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)


def fmt_date(when: dt.datetime) -> str:
    """The Eastern calendar date, like the site's dates: "Nov 15, 2026"."""
    d = to_eastern(when)[0].date()
    return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"


def fmt_time(when: dt.datetime) -> str:
    """"Nov 15, 2026, 4:26 PM EST"."""
    local, zone = to_eastern(when)
    hour = local.hour % 12 or 12
    return f"{fmt_date(when)}, {hour}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'} {zone}"


def fmt_age(age: dt.timedelta) -> str:
    """"3 hours", "1 day 4 hours", "46 days": rounded down, never negative."""
    hours = max(0, int(age.total_seconds() // 3600))
    days, rest = divmod(hours, 24)
    if days >= 3:
        return plural(days, "day")
    if days:
        return f"{plural(days, 'day')} {plural(rest, 'hour')}" if rest else plural(days, "day")
    return plural(hours, "hour") if hours else "under an hour"


def plural(n: int, one: str) -> str:
    return f"{n} {one}{'' if n == 1 else 's'}"


def hours(span: dt.timedelta) -> int:
    return int(span.total_seconds() // 3600)


def check(key: str, title: str, status: str, detail: str, todo: str = "", **extra) -> dict:
    """One check's entry in the report. `extra`: further fields (the activity check's "deadline")."""
    return {"key": key, "title": title, "status": status, "detail": detail, "todo": todo, **extra}


# ---- the checks (no network, no clock: everything is passed in) -------------------------------- #

def scheduled_workflows(folder: Path = WORKFLOWS) -> list:
    """The workflow files that have a schedule (a `- cron:` line that is not a comment), as repository paths."""
    return sorted(f".github/workflows/{f.name}" for f in sorted(Path(folder).glob("*.yml"))
                  if CRON_LINE.search(f.read_text(encoding="utf-8")))


def state_words(state: str | None) -> str:
    """A workflow's state in plain words; a state without words of ours is shown as GitHub names it."""
    if not state:
        return "not listed by GitHub"
    return STATE_WORDS.get(state) or f"in the state `{state}`"


def check_workflows(scheduled: list, listed: list) -> dict:
    """`scheduled`: paths from scheduled_workflows; `listed`: the workflows as GitHub's API lists them."""
    title = "Scheduled workflows are enabled"
    if not scheduled:
        return check("workflows", title, UNKNOWN, "No workflow file with a schedule was found in the checkout.")
    states = {w.get("path"): w.get("state") for w in listed}
    names = ", ".join(f"`{Path(p).name}`" for p in scheduled)
    off = [(Path(p).name, states.get(p)) for p in scheduled if states.get(p) != "active"]
    if not off:
        return check("workflows", title, OK, f"All {len(scheduled)} workflows with a schedule are enabled: {names}.")
    detail = "; ".join(f"`{name}` is {state_words(state)}" for name, state in off)
    todo = []
    if any(state == "disabled_inactivity" for _, state in off):
        todo.append("First merge a real pull request into `main`: enabling alone does not meet the 60-day rule, so "
                    "GitHub would switch the workflows off again. Then enable each one.")
    disabled = [name for name, state in off if (state or "").startswith("disabled")]
    if disabled:
        todo.append("To enable a workflow: on the repository's Actions tab choose it and press \"Enable workflow\", or "
                    "run " + " and ".join(f"`gh workflow enable {name}`" for name in disabled) + ". By users' reports "
                    "a workflow that is off starts for no trigger at all, a manual run included.")
    if any(not state for _, state in off):
        todo.append("A file GitHub does not list is not on `main` yet, or its workflow was deleted there.")
    if any(state and not state.startswith("disabled") for _, state in off):
        todo.append("For any other state, open the workflow on the Actions tab and read what GitHub says there.")
    return check("workflows", title, PROBLEM, f"Not enabled: {detail}.", " ".join(todo))


def check_activity(now: dt.datetime, commit: dict) -> dict:
    """`commit`: {"sha", "date"} of the last commit on main (its committer date, which for a merge is the merge).

    The entry also carries "deadline": GitHub's limit (the commit plus DISABLE_AFTER) as an ISO time in UTC, which
    the issue command needs for the reminder comments."""
    title = "Recent activity on main (GitHub's 60-day rule)"
    when = parse_time(commit["date"])
    age, deadline = now - when, when + DISABLE_AFTER
    limit = deadline.astimezone(dt.timezone.utc).isoformat(timespec="seconds")
    last = (f"The last commit on `{MAIN_BRANCH}` is `{commit['sha'][:7]}` from {fmt_date(when)}, "
            f"{plural(max(0, age.days), 'day')} ago.")
    rule = (f"GitHub switches every scheduled workflow in a public repository off after {DISABLE_AFTER.days} days "
            f"without repository activity")
    if age < WARN_AFTER:
        return check("activity", title, OK,
                     f"{last} {rule}; this check warns from {WARN_AFTER.days} days ({fmt_date(when + WARN_AFTER)}).",
                     deadline=limit)
    what = ("Merge any real pull request into `main` (for example a waiting Dependabot pull request: `gh pr list`). "
            "If none is waiting, open one with any small real improvement (a documentation fix, for example). "
            "A commit on `main` is the one kind of activity known to count; workflow runs do not, and commits on "
            "the `data` branch may not. Do not make an empty commit for this.")
    if age < DISABLE_AFTER:
        return check("activity", title, PROBLEM,
                     f"{last} {rule}: that is {fmt_time(deadline)}, {plural((deadline - now).days, 'day')} from now.",
                     f"{what} It must be merged before {fmt_time(deadline)}; sooner is safer, because GitHub does "
                     f"not say exactly when it counts. After that {SCHEDULED_WORK} all stop.", deadline=limit)
    return check("activity", title, PROBLEM,
                 f"{last} {rule}: that limit passed on {fmt_time(deadline)}, so the scheduled workflows may "
                 f"already be off (see the workflows check).",
                 f"{what} Then enable every workflow that is off (Actions tab, the workflow, \"Enable workflow\"); "
                 f"a commit does not switch them back on.", deadline=limit)


def check_age(key: str, title: str, now: dt.datetime, when: dt.datetime | None, found: str, missing: str,
              todo: str) -> dict:
    """ok while `when` is at most MAX_AGE before `now`. `found`: a sentence start such as "The last deploy
    finished"; `missing`: the whole detail when there is no time at all."""
    if when is None:
        return check(key, title, PROBLEM, missing, todo)
    age = now - when
    detail = f"{found} {fmt_time(when)}, {fmt_age(age)} ago"
    if age > MAX_AGE:
        return check(key, title, PROBLEM, f"{detail}: over the {hours(MAX_AGE)}-hour limit.", todo)
    return check(key, title, OK, f"{detail} (the limit is {hours(MAX_AGE)} hours).")


RUNS_TODO = ("Open the Actions tab and look at the newest \"Update data and deploy site\" runs: a red run names its "
             "problem in the run summary. If none has started lately, GitHub is dropping the schedule; start one by "
             "hand (Run workflow, no boxes ticked) and check that the workflow is enabled.")
SCRAPE_TITLE, DEPLOY_TITLE = "Last successful full scrape", "Last successful deploy"
SITE_TITLE = "The live site's \"Last checked\" time"


def check_scrape(now: dt.datetime, when: dt.datetime | None, searched: str) -> dict:
    return check_age("scrape", SCRAPE_TITLE, now, when,
                     "The last successful run that read every sport finished its scrape",
                     f"No successful run that read every sport was found {searched}.", RUNS_TODO)


def check_deploy(now: dt.datetime, when: dt.datetime | None, searched: str) -> dict:
    return check_age("deploy", DEPLOY_TITLE, now, when, "The last successful deploy finished",
                     f"No successful deploy was found {searched}.", RUNS_TODO)


def parse_last_checked(html: str) -> dt.datetime | None:
    """The "Last checked" time in the page's footer, or None when the page shows none."""
    m = LAST_CHECKED.search(html)
    try:
        return parse_time(m.group(1)) if m else None
    except ValueError:
        return None


def check_last_checked(now: dt.datetime, html: str | None, error: str = "") -> dict:
    """`html`: the live home page, or None with `error` when it did not load (also on the second try).

    A page that did not load, or an answer that is not our home page, is unknown: the check could not be made. Our
    page without a "Last checked" time, or with an old one, is a problem."""
    if html is None:
        return check("last-checked", SITE_TITLE, UNKNOWN,
                     f"Could not be checked: the live home page did not load ({error}).")
    if OUR_PAGE not in html:
        return check("last-checked", SITE_TITLE, UNKNOWN,
                     f"Could not be checked: {SITE_URL} answered, but not with our home page (it has no "
                     f"\"{OUR_PAGE}\" line).")
    todo = (f"Open {SITE_URL} and read the footer. \"Last checked\" moves only when a run really reads the schools' "
            f"pages, so an old time means the updates have stopped or keep failing: " + RUNS_TODO)
    return check_age("last-checked", SITE_TITLE, now, parse_last_checked(html),
                     "The live home page says it was last checked",
                     "The live home page loads but shows no \"Last checked\" time (the page leaves it off when the "
                     "time of the last real scrape could not be restored).", todo)


def check_data_branch(commit: dict | None, error: str = "") -> dict:
    """`commit`: {"sha", "date"} of the data branch's last commit, or None with `error` when there is none."""
    title = "The data branch is readable"
    if commit is None:
        return check("data-branch", title, PROBLEM, f"The `{DATA_BRANCH}` branch could not be read: {error}.",
                     "Every update needs this branch. If it is gone, recreate it from the newest snapshot release's "
                     "files, as the README section on the `data` branch describes (\"If the branch is ever lost\").")
    return check("data-branch", title, OK,
                 f"The `{DATA_BRANCH}` branch's last commit is `{commit['sha'][:7]}` from "
                 f"{fmt_date(parse_time(commit['date']))}.")


def gate_scope(log: str) -> str | None:
    """What a run read, from its gate job's log: "all", a list of sports, "(nothing)", or None when the gate's
    line is not there."""
    m = SCOPE_LINE.search(log)
    return m.group(2) if m else None


def step_time(job: dict | None, name: str) -> dt.datetime | None:
    """When a job's step finished, if it succeeded."""
    for step in (job or {}).get("steps") or []:
        if step.get("name") == name and step.get("conclusion") == "success" and step.get("completed_at"):
            return parse_time(step["completed_at"])
    return None


def find_latest(now: dt.datetime, page_of, jobs_of, log_of) -> dict:
    """The last successful full scrape and the last successful deploy among the update workflow's runs:
    {"scrape", "deploy"} (when each finished, or None), "looked" (runs read), "capped" (the search stopped at
    MAX_PAGES before it reached LOOKBACK) and "log_error" (why the scrape could not be determined, or "").

    `page_of(n)`: page n (from 1) of the completed runs, newest first, PAGE_SIZE to a page; `jobs_of(run id)` a
    run's jobs; `log_of(job id)` a job's log. A full scrape is a run that ended in success, whose Scrape step
    succeeded and whose gate says it read every sport (the log is read only for such a run). A deploy counts
    whenever the deploy job succeeded, also in a run that turned red afterwards. The search stops as soon as both are
    found, at the first run older than LOOKBACK, when the runs end, or after MAX_PAGES pages. A gate log that cannot
    be downloaded ends the search for the scrape only (that run may or may not have been the full one), so the
    deploy keeps its answer."""
    found = {"scrape": None, "deploy": None, "looked": 0, "capped": False, "log_error": ""}
    for page in range(1, MAX_PAGES + 1):
        runs = page_of(page)
        for run in runs:
            scrape_settled = found["scrape"] or found["log_error"]
            if (scrape_settled and found["deploy"]) or now - parse_time(run["created_at"]) > LOOKBACK:
                return found
            found["looked"] += 1
            jobs = {j.get("name"): j for j in jobs_of(run["id"])}
            done = jobs.get(DEPLOY_JOB) or {}
            if found["deploy"] is None and done.get("conclusion") == "success" and done.get("completed_at"):
                found["deploy"] = parse_time(done["completed_at"])
            scraped = step_time(jobs.get(BUILD_JOB), SCRAPE_STEP)
            if not scrape_settled and run.get("conclusion") == "success" and scraped and jobs.get(GATE_JOB):
                try:
                    scope = gate_scope(log_of(jobs[GATE_JOB]["id"]))
                except Exception as exc:  # noqa: BLE001 - one unreadable log must not take the deploy check with it
                    found["log_error"] = f"the gate job's log of run {run['id']} could not be downloaded ({why(exc)})"
                    continue
                if scope == FULL_SCOPE:
                    found["scrape"] = scraped
        if len(runs) < PAGE_SIZE:
            return found
    found["capped"] = not ((found["scrape"] or found["log_error"]) and found["deploy"])
    return found


def searched_words(found: dict) -> str:
    """How far the run search really went, for a "nothing was found" detail."""
    if found["capped"]:
        return (f"in the newest {plural(found['looked'], 'run')} (the search stops after {MAX_PAGES * PAGE_SIZE} runs, "
                f"before it reached {LOOKBACK.days} days back)")
    return f"in the last {LOOKBACK.days} days ({plural(found['looked'], 'run')} looked at)"


# ---- reading GitHub and the site (the only network code) ---------------------------------------- #

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def no_redirect_opener(*handlers):
    """An opener that never follows a redirect (the redirect is raised as an HTTPError instead), so a request that
    carries the token can never be re-sent to another address. `handlers`: extra handlers, for tests."""
    return urllib.request.build_opener(_NoRedirect, *handlers)


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": USER_AGENT}


def api(path: str, token: str, opener=None):
    """One GET of GitHub's REST API, as JSON. Only ever a GET: the check writes nothing. A redirect is not
    followed (urllib would send the token on to the new address); it raises, and the check becomes unknown."""
    req = urllib.request.Request(f"https://api.github.com{path}", method="GET", headers=_headers(token))
    with (opener or no_redirect_opener().open)(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read())


def job_log(repo: str, token: str, job_id: int, opener=None, plain=None) -> str:
    """A job's log as text. GitHub answers with a redirect to a short-lived download address on another host; that
    second request is made without the token (it must not leave GitHub's API), over https only."""
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/jobs/{job_id}/logs", method="GET",
                                 headers=_headers(token))
    try:
        with (opener or no_redirect_opener().open)(req, timeout=TIMEOUT) as resp:
            return resp.read(MAX_BYTES).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        location = exc.headers.get("Location") if exc.code in (301, 302, 303, 307, 308) else None
        if not location:
            raise
    if not location.startswith("https://"):
        raise ValueError("the log's download address is not https")
    req = urllib.request.Request(location, method="GET", headers={"User-Agent": USER_AGENT})
    with (plain or urllib.request.urlopen)(req, timeout=TIMEOUT) as resp:
        return resp.read(MAX_BYTES).decode("utf-8", errors="replace")


def fetch_site(url: str = SITE_URL, opener=None) -> str:
    """Our own home page: one request. Anything but a 200 answer is an error."""
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT})
    with (opener or urllib.request.urlopen)(req, timeout=TIMEOUT) as resp:
        status = getattr(resp, "status", 200)
        if status != 200:
            raise urllib.error.HTTPError(url, status, "not a 200 answer", getattr(resp, "headers", None), None)
        return resp.read(MAX_BYTES).decode("utf-8", errors="replace")


def why(exc: Exception) -> str:
    """A short, safe description of a failed read (never the address of a log download)."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return f"no connection: {type(exc.reason).__name__}"
    return type(exc).__name__


def could_not(exc: Exception) -> str:
    """The detail of a check that could not be made: GitHub did not answer, or something else went wrong."""
    if isinstance(exc, OSError):        # HTTPError, URLError, timeouts and failed connections
        return f"Could not be checked: GitHub's API did not answer ({why(exc)})."
    return f"Could not be checked: the check failed ({type(exc).__name__})."


def last_commit(get, repo: str, branch: str) -> dict:
    data = get(f"/repos/{repo}/commits?sha={branch}&per_page=1")
    return {"sha": data[0]["sha"], "date": data[0]["commit"]["committer"]["date"]}


def run_checks(now: dt.datetime, repo: str, get, log_of, site, scheduled: list, sleep=time.sleep) -> list:
    """Every check, in the order of the report. `get(path)` reads GitHub's API, `log_of(job id)` a job's log,
    `site()` the live home page, `sleep(seconds)` waits before the home page's second try. A read that fails makes
    its check unknown, with one exception that is an answer, not a failure: the data branch is not there (404)."""
    checks = []
    workflows_title = "Scheduled workflows are enabled"
    activity_title = "Recent activity on main (GitHub's 60-day rule)"

    try:
        listed = get(f"/repos/{repo}/actions/workflows?per_page=100").get("workflows", [])
        checks.append(check_workflows(scheduled, listed))
    except Exception as exc:  # noqa: BLE001 - any failed read is "unknown", never a crash
        checks.append(check("workflows", workflows_title, UNKNOWN, could_not(exc)))

    try:
        checks.append(check_activity(now, last_commit(get, repo, MAIN_BRANCH)))
    except Exception as exc:  # noqa: BLE001
        checks.append(check("activity", activity_title, UNKNOWN, could_not(exc)))

    try:
        found = find_latest(
            now,
            lambda page: get(f"/repos/{repo}/actions/workflows/{UPDATE_WORKFLOW}/runs?branch={MAIN_BRANCH}"
                             f"&status=completed&per_page={PAGE_SIZE}&page={page}").get("workflow_runs", []),
            lambda run_id: get(f"/repos/{repo}/actions/runs/{run_id}/jobs?per_page=100").get("jobs", []),
            log_of)
        searched = searched_words(found)
        if found["log_error"]:
            checks.append(check("scrape", SCRAPE_TITLE, UNKNOWN, f"Could not be checked: {found['log_error']}."))
        else:
            checks.append(check_scrape(now, found["scrape"], searched))
        checks.append(check_deploy(now, found["deploy"], searched))
    except Exception as exc:  # noqa: BLE001
        checks += [check("scrape", SCRAPE_TITLE, UNKNOWN, could_not(exc)),
                   check("deploy", DEPLOY_TITLE, UNKNOWN, could_not(exc))]

    html, error = None, ""
    for attempt in range(1, SITE_TRIES + 1):
        try:
            html = site()
            break
        except Exception as exc:  # noqa: BLE001
            error = why(exc)
            if attempt < SITE_TRIES:
                sleep(SITE_RETRY_WAIT)
    checks.append(check_last_checked(now, html, error))

    data_title = "The data branch is readable"
    try:
        checks.append(check_data_branch(last_commit(get, repo, DATA_BRANCH)))
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 409):      # no such branch, or an empty repository
            checks.append(check_data_branch(None, "GitHub says there is no such branch"))
        else:
            checks.append(check("data-branch", data_title, UNKNOWN, could_not(exc)))
    except Exception as exc:  # noqa: BLE001
        checks.append(check("data-branch", data_title, UNKNOWN, could_not(exc)))
    return checks


# ---- the report --------------------------------------------------------------------------------- #

def build_report(now: dt.datetime, checks: list) -> dict:
    count = {s: sum(1 for c in checks if c["status"] == s) for s in (OK, PROBLEM, UNKNOWN)}
    return {"schema_version": 1, "checked_at": now.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
            "totals": count, "checks": checks}


def problem_keys(report: dict) -> list:
    return sorted(c["key"] for c in report["checks"] if c["status"] == PROBLEM)


def totals_line(report: dict) -> str:
    t = report["totals"]
    parts = [plural(t[PROBLEM], "problem"), f"{t[OK]} OK"]
    if t[UNKNOWN]:
        parts.insert(1, f"{t[UNKNOWN]} could not be checked")
    return f"{len(report['checks'])} checks: " + ", ".join(parts) + "."


def table(report: dict) -> list:
    rows = ["| Check | Result | What was found |", "|---|---|---|"]
    rows += [f"| {c['title']} | {WORDS[c['status']]} | {c['detail']} |" for c in report["checks"]]
    return rows


def render_summary(report: dict) -> str:
    """The job summary (and the local report): every check, then what to do about each problem."""
    lines = [f"# Site health, {fmt_date(parse_time(report['checked_at']))}", "", totals_line(report), "",
             *table(report)]
    for c in report["checks"]:
        if c["status"] == PROBLEM:
            lines += ["", f"**{c['title']}: what to do.** {c['todo']}"]
    if report["totals"][UNKNOWN]:
        lines += ["", "A check that could not be made turns this run red and leaves the `site-health` issue as it "
                      "is. Run the workflow again; if it keeps failing, look at the log of the check job."]
    return "\n".join(lines) + "\n"


def render_issue_body(report: dict, run_url: str = "", carried: tuple = (), reminded: str = "") -> str:
    """The issue's text. `carried`: keys of checks that could not be made today but were problems at the last check
    that could be made; each keeps a short section, and its place in the marker, until it can be checked again.
    `reminded`: the hidden line that records the reminders already sent (plan_reminder), or ""."""
    problems = [c for c in report["checks"] if c["status"] == PROBLEM]
    lines = [f"The daily health check found {plural(len(problems), 'problem')} on "
             f"{fmt_date(parse_time(report['checked_at']))}. Each run rewrites this issue, and it is closed with a "
             f"comment once every check passes (README, \"The health check\").", ""]
    for c in problems:
        lines += [f"## {c['title']}", "", c["detail"], "", f"**What to do:** {c['todo']}", ""]
    for c in report["checks"]:
        if c["key"] in carried and c["status"] == UNKNOWN:
            lines += [f"## {c['title']}", "", CARRIED, ""]
    lines += ["## Every check", "", *table(report), ""]
    if run_url:
        lines += [f"Run: {run_url}", ""]
    if reminded:
        lines.append(reminded)
    lines.append(f"<!-- site-health: {', '.join(sorted({*problem_keys(report), *carried}))} -->")
    return "\n".join(lines) + "\n"


# ---- the issue ----------------------------------------------------------------------------------- #

def gh(*args: str, stdin: str | None = None) -> str:
    return subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, check=True).stdout


def due_threshold(left: dt.timedelta) -> int | None:
    """The reminder that is due with `left` to go before GitHub's limit: the smallest N in REMIND_AT with at most
    N days left. None with more time than the largest N, and None once the limit has passed."""
    if left <= dt.timedelta(0):
        return None
    due = [n for n in REMIND_AT if left <= dt.timedelta(days=n)]
    return min(due) if due else None


def plan_reminder(report: dict, body: str = "") -> tuple:
    """(the reminded line for the issue's new text, the threshold to remind for now or None, the time left).

    `body`: the open issue's text ("" when the issue is being opened). The line records the limit and the smallest
    threshold already covered; a line for another limit (a newer commit moved it), or no line, means nothing is
    covered. A threshold is returned when one is due and not covered yet, so each is sent at most once per limit,
    and a run that comes late (from more than 7 days left straight to 2 or less) gets one reminder, not two. When
    the activity check could not be made today, the old line is kept as it is and nothing is due; when the check
    passes, or the report carries no limit, there is no line."""
    activity = next((c for c in report["checks"] if c["key"] == "activity"), None)
    old = REMINDED.search(body or "")
    old_line = old.group(0) if old else ""
    if activity is None or activity["status"] == UNKNOWN:
        return old_line, None, None
    if activity["status"] != PROBLEM or not activity.get("deadline"):
        return "", None, None
    deadline = parse_time(activity["deadline"])
    left = deadline - parse_time(report["checked_at"])
    covered = None
    try:
        if old and parse_time(old.group(1)) == deadline:
            covered = int(old.group(2))
    except ValueError:
        pass                                # a line someone edited by hand: nothing is covered
    due = due_threshold(left)
    if due is None or (covered is not None and covered <= due):
        return (old_line if covered is not None else ""), None, left
    return f"<!-- site-health-reminded: {activity['deadline']} {due} -->", due, left


def reminder_words(report: dict, left: dt.timedelta) -> str:
    """The reminder's sentence: the real time left (a late run may come at 6 days, not 7), what to do, and the
    limit with its time, as the issue gives it. It mentions nobody."""
    deadline = next(c for c in report["checks"] if c["key"] == "activity")["deadline"]
    return (f"{fmt_age(left)} left before GitHub's {DISABLE_AFTER.days}-day limit. Merge a real pull request into "
            f"`{MAIN_BRANCH}` before {fmt_time(parse_time(deadline))}, or the scheduled workflows are switched off.")


def sync_issue(report: dict, run_url: str = "", run=gh, assignee: str = "") -> str:
    """Open, update or close the one site-health issue. `run` is the gh runner (replaced in tests).

    A problem opens the issue or rewrites the open one; a comment is added only when the list of problems changed,
    so a problem that lasts for weeks does not send a notification every day. A check that could not be made today
    is not a change: if the issue listed it as a problem, it stays listed until it can be checked again. No problem
    and nothing unknown: comment on and close every open issue. No problem but a check that could not be made: the
    issue is left exactly as it is.

    Reminders: while the activity check is a problem, the open issue gets one comment when REMIND_AT[0] days or
    less are left before GitHub's limit and one more at each smaller threshold (plan_reminder). Opening the issue
    is itself the notification, so a threshold that is due in that run is recorded without a comment. At most one
    comment per run: a reminder and a changed list of problems share it.

    `assignee`: the login the issue is assigned to when it is opened (the repository's owner in the workflow), or
    "" for nobody. Only then: a later run never assigns again, so an owner who removes themselves stays removed. If
    the issue cannot be opened with the assignee (an organization cannot be assigned, say), it is opened without
    one: the issue is never lost for that. The text and every comment mention nobody."""
    open_issues = sorted(json.loads(run("issue", "list", "--label", LABEL, "--state", "open",
                                        "--json", "number,body", "--limit", "100")), key=lambda i: i["number"])
    when = fmt_date(parse_time(report["checked_at"]))
    titles = {c["key"]: c["title"] for c in report["checks"]}
    status = {c["key"]: c["status"] for c in report["checks"]}
    keys = problem_keys(report)
    if not keys:
        if not open_issues:
            return "no problem found, and no open issue"
        if report["totals"][UNKNOWN]:
            return (f"left #{open_issues[0]['number']} open: no problem found, but "
                    f"{report['totals'][UNKNOWN]} could not be checked")
        closed = []
        for issue in open_issues:
            n = str(issue["number"])
            run("issue", "comment", n, "--body-file", "-",
                stdin=f"Health check of {when}: every check passes. {totals_line(report)} Closing. "
                      f"{run_url}".strip() + "\n")
            run("issue", "close", n)
            closed.append(f"#{n}")
        return "closed " + ", ".join(closed)
    if open_issues:
        n = str(open_issues[0]["number"])
        before = MARKER.search(open_issues[0].get("body") or "")
        was = sorted(k for k in (before.group(1).split(", ") if before else []) if k)
        carried = tuple(k for k in was if status.get(k) == UNKNOWN)
        listed = sorted({*keys, *carried})
        reminded, due, left = plan_reminder(report, open_issues[0].get("body") or "")
        run("issue", "edit", n, "--body-file", "-", stdin=render_issue_body(report, run_url, carried, reminded))
        changed = was != listed
        if not changed and due is None:
            return f"updated #{n} (the same {plural(len(listed), 'problem')}, no comment)"
        said = []
        if changed:
            now_failing = "; ".join(titles[k] + (" (could not be checked today)" if k in carried else "")
                                    for k in listed)
            said.append(f"the problems changed. Now: {now_failing}.")
        if due is not None:
            said.append(("Reminder: " if changed else "") + reminder_words(report, left))
        run("issue", "comment", n, "--body-file", "-",          # one comment per run, whatever it has to say
            stdin=f"Health check of {when}: {' '.join(said)} {run_url}".strip() + "\n")
        about = " and ".join(what for what, yes in (("the problems changed", changed),
                                                    (f"a reminder ({fmt_age(left)} left)" if due else "", due)) if yes)
        return f"updated #{n} and commented: {about}"
    labels = {l["name"] for l in json.loads(run("label", "list", "--json", "name", "--limit", "200"))}
    if LABEL not in labels:
        run("label", "create", LABEL, "--color", LABEL_COLOR, "--description", LABEL_DESC)
    reminded, _, _ = plan_reminder(report)     # opening is the notification: a threshold due now counts as covered
    body = render_issue_body(report, run_url, reminded=reminded)
    if assignee:
        try:
            url = run("issue", "create", "--title", ISSUE_TITLE, "--label", LABEL, "--body-file", "-",
                      "--assignee", assignee, stdin=body).strip()
            return f"opened {url}, assigned to the repository's owner"
        except subprocess.CalledProcessError:
            unassigned = " (it could not be assigned to the repository's owner, so it was opened unassigned)"
    else:
        unassigned = ""
    url = run("issue", "create", "--title", ISSUE_TITLE, "--label", LABEL, "--body-file", "-", stdin=body).strip()
    return f"opened {url}{unassigned}"


# ---- command line -------------------------------------------------------------------------------- #

def now_utc() -> dt.datetime:
    """The check time (tests replace it, so they never read the real clock)."""
    return dt.datetime.now(dt.timezone.utc)


def write_report(report: dict, out: Path) -> None:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "health-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "summary.md").write_text(render_summary(report), encoding="utf-8")


def output(**values) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.writelines(f"{key}={value}\n" for key, value in values.items())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="make every check and write the report (read-only)")
    c.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    c.add_argument("--out", type=Path, default=OUT)
    i = sub.add_parser("issue", help="open, update or close the site-health issue from a report (needs gh)")
    i.add_argument("--report", type=Path, required=True)
    i.add_argument("--run-url", default="")
    i.add_argument("--assignee", default=os.environ.get("GITHUB_REPOSITORY_OWNER", ""),
                   help="who a newly opened issue is assigned to (default: the repository's owner, as Actions "
                        "gives it in GITHUB_REPOSITORY_OWNER; empty for nobody)")
    v = sub.add_parser("verdict", help="exit 1 when the report has a check that could not be made")
    v.add_argument("--report", type=Path, required=True)
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.command == "check":
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
        if not args.repo or not token:
            print("health.py check needs --repo (or GITHUB_REPOSITORY) and a token in GH_TOKEN or GITHUB_TOKEN.",
                  file=sys.stderr)
            return 2
        now = now_utc()
        checks = run_checks(now, args.repo, lambda path: api(path, token),
                            lambda job_id: job_log(args.repo, token, job_id), fetch_site, scheduled_workflows())
        report = build_report(now, checks)
        write_report(report, args.out)
        print(render_summary(report))
        output(reported="true", problems=report["totals"][PROBLEM], unknown=report["totals"][UNKNOWN])
        return 0   # problems are reported through the issue; `verdict` turns the run red for an unknown check
    report = json.loads(args.report.read_text(encoding="utf-8"))
    if args.command == "issue":
        made = parse_time(report["checked_at"])
        if now_utc() - made > REPORT_MAX_AGE:
            print(f"The report is from {fmt_time(made)}, more than {hours(REPORT_MAX_AGE)} hours ago: the issue was "
                  f"not touched. Run the workflow again instead of re-running an old run's job.")
            return 0
        print(sync_issue(report, args.run_url, assignee=args.assignee))
        return 0
    unknown = [c["title"] for c in report["checks"] if c["status"] == UNKNOWN]
    if unknown:
        print(f"::error::The health check could not make {plural(len(unknown), 'check')}: {'; '.join(unknown)}. "
              f"The site-health issue was left as it is.")
        return 1
    print("Every check could be made.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
