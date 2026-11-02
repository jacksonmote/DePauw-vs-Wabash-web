"""How the workflows get the bot-managed data files (the `data` branch) and how the update workflow
writes them back. The behaviour itself is tested against real git in test_databranch.py; this pins the wiring."""
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
import databranch  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
ALL_WORKFLOWS = sorted(p.name for p in WORKFLOWS.glob("*.yml"))   # every workflow, including later ones
FETCH = "python3 scraper/databranch.py fetch --remote origin"
REMERGE = "Re-merge series.json from the saved copies"


def load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def text(name):
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def steps_of(name, job):
    return load(name)["jobs"][job]["steps"]


def index_of(steps, predicate):
    return next(i for i, step in enumerate(steps) if predicate(step))


def is_fetch(step):
    return step.get("run") == FETCH


def is_checkout(step):
    return step.get("uses", "").startswith("actions/checkout@")


def test_every_job_that_reads_the_data_fetches_it_right_after_checking_out_main():
    for workflow, job in (("update-and-deploy.yml", "gate"), ("update-and-deploy.yml", "build"),
                          ("update-and-deploy.yml", "issues"), ("checks.yml", "checks"), ("checks.yml", "fake-clock"),
                          ("snapshot.yml", "prepare")):
        steps = steps_of(workflow, job)
        assert is_checkout(steps[0]), (workflow, job)
        assert is_fetch(steps[1]), (workflow, job)
        assert sum(is_fetch(s) for s in steps) == 1


def test_the_gate_reads_the_schedule_and_series_from_the_data_branch_and_not_from_main():
    steps = steps_of("update-and-deploy.yml", "gate")
    fetch, gate = index_of(steps, is_fetch), index_of(steps, lambda s: s.get("id") == "gate")
    assert fetch < gate
    assert steps[0]["with"]["sparse-checkout"].split() == ["scraper"]      # no data/ from main at all
    assert "python3 scraper/gameday.py gate" in steps[gate]["run"]


def test_the_build_job_fetches_then_remerges_then_tests_then_scrapes_and_pushes_last():
    steps = steps_of("update-and-deploy.yml", "build")
    names = [s.get("name") or s.get("uses", "").split("@")[0] for s in steps]
    order = [names.index(n) for n in ("Get the data files from the data branch", REMERGE,
                                      "Run tests", "Scrape", "Render site", "Commit if data changed")]
    assert order == sorted(order)
    remerge = steps[names.index(REMERGE)]
    assert remerge["run"] == "python scraper/scrape.py --remerge-only"
    assert remerge["continue-on-error"] is True            # must not become a new hard stop in production


def test_checks_remerge_is_a_hard_step_so_a_stale_ruling_fails_the_pull_request():
    for job in ("checks", "fake-clock"):
        steps = steps_of("checks.yml", job)
        remerge = next(s for s in steps if s.get("name") == REMERGE)
        assert remerge["run"] == "python scraper/scrape.py --remerge-only" and "continue-on-error" not in remerge
        assert index_of(steps, is_fetch) < steps.index(remerge) < index_of(steps, lambda s: "pytest" in s.get("run", ""))


def test_pull_request_checks_stay_read_only_and_never_use_a_token_for_the_fetch():
    checks = load("checks.yml")
    assert checks["permissions"] == {"contents": "read"}
    assert "pull_request_target" not in text("checks.yml") and "secrets." not in text("checks.yml")
    for job in ("checks", "fake-clock"):
        assert steps_of("checks.yml", job)[0]["with"] == {"persist-credentials": False}
        assert "token" not in "".join(str(s.get("env", "")) for s in steps_of("checks.yml", job)).lower()


def test_only_the_build_job_can_write_and_only_through_databranch_push():
    for name in ALL_WORKFLOWS:
        for job_name, job in load(name)["jobs"].items():
            for step in job["steps"]:
                assert "git push" not in step.get("run", ""), (name, job_name)
    wf = load("update-and-deploy.yml")
    assert wf["jobs"]["build"]["permissions"] == {"contents": "write"}
    assert wf["jobs"]["gate"]["permissions"] == {"contents": "read", "actions": "read"}   # sees whether the watcher runs
    assert wf["jobs"]["watcher-handoff"]["permissions"] == {"contents": "read", "actions": "write"}
    assert wf["jobs"]["issues"]["permissions"] == {"contents": "read", "issues": "write"}
    assert wf["jobs"]["build"]["concurrency"] == {"group": "update-and-deploy", "cancel-in-progress": False}
    assert wf["permissions"] == {"contents": "read"}


def test_the_test_data_commit_input_exists_defaults_to_off_and_only_adds_the_flag():
    wf = load("update-and-deploy.yml")
    option = wf[True]["workflow_dispatch"]["inputs"]["test_data_commit"]            # YAML reads `on` as True
    assert option["type"] == "boolean" and option["default"] is False
    commit = next(s for s in steps_of("update-and-deploy.yml", "build") if s.get("name") == "Commit if data changed")
    assert commit["env"] == {"TEST_DATA_COMMIT": "${{ inputs.test_data_commit }}"}
    assert 'args="--test-commit"' in commit["run"] and commit["run"].count("databranch.py push") == 1
    assert commit["run"].rstrip().endswith("push --remote origin $args")


def test_snapshot_reads_the_bot_files_from_the_data_branch_and_overrides_from_main():
    steps = steps_of("snapshot.yml", "prepare")
    assert steps[0]["with"]["ref"] == "main" and steps[0]["with"]["persist-credentials"] is False
    assert "overrides.json" not in databranch.BOT_FILES                       # stays on main, in the checkout
    archive = steps_of("snapshot.yml", "archive")
    assert not any(is_fetch(s) for s in archive)                              # the archive job never reads data/


def test_a_failed_push_to_the_data_branch_still_deploys_warns_and_turns_the_run_red():
    wf = load("update-and-deploy.yml")
    build = wf["jobs"]["build"]
    assert next(s for s in build["steps"] if s.get("id") == "commit")["continue-on-error"] is True
    assert "commit" in build["outputs"]
    assert "needs.build.result == 'success'" in wf["jobs"]["deploy"]["if"]
    summary = next(s for s in build["steps"] if s.get("id") == "summary")["run"]
    assert "data branch" in summary and "::warning title=Data commit::" in summary
    report = "\n".join(s.get("run", "") for s in wf["jobs"]["report"]["steps"])
    assert "data branch" in report and "exit 1" in report
    report_if = wf["jobs"]["report"]["if"]
    assert "!cancelled()" in report_if and "needs.build.outputs.problems == 'true'" in report_if


def test_every_action_stays_pinned_to_a_full_sha_with_a_version_comment():
    for name in ALL_WORKFLOWS:
        for line in text(name).splitlines():
            m = re.match(r"\s*(?:- )?uses: (\S+)(.*)", line)
            if m:
                assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", m.group(1)), line
                assert re.search(r"# v\d", m.group(2)), line


def test_the_workflows_never_name_main_as_a_place_to_push_data():
    for name in ALL_WORKFLOWS:
        assert "HEAD:main" not in text(name) and "git add data/" not in text(name)


def test_the_readme_and_agents_describe_the_data_branch():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for name in databranch.BOT_FILES:
        assert f"`{name}`" in readme.split("### The `data` branch", 1)[1].split("### Why the merge", 1)[0]
    for phrase in ("python scraper/databranch.py fetch", "test_data_commit", "data/.commit-test", "Protect data",
                   "Restrict deletions", "Block force pushes", "--remerge-only", "never forced"):
        assert phrase in readme, phrase
    assert "databranch.py" in readme.split("## Where things are", 1)[1]
    assert "`data` branch" in agents and "never force-pushed" in agents and "never to `main`" in agents
    assert "commits data<br/>to the data branch" in readme                         # the run-flow diagram
    assert "commits data to main" not in readme and "commits data to `main`" not in readme
