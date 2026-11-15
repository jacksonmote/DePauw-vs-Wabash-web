"""The npm side of the lock: package.json names the HTML validator at one exact version, package-lock.json pins it and
everything it pulls in with integrity hashes and no install scripts, and the workflows install it with `npm ci
--ignore-scripts` and run it with `npx --no-install`, never fetching it at run time. The accessibility check has its
own locked folder, axe/ (axe-core and the browser driver), held to the same rules and kept out of the root lock, so
the production validator install never changes with it. Reads files only."""
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
LOCK = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
AXE_PACKAGE = json.loads((ROOT / "axe" / "package.json").read_text(encoding="utf-8"))
AXE_LOCK = json.loads((ROOT / "axe" / "package-lock.json").read_text(encoding="utf-8"))
NPM_CI = "npm ci --ignore-scripts --no-audit --no-fund"


def test_package_json_pins_the_validator_to_one_exact_version_and_is_private():
    assert PACKAGE["private"] is True
    assert set(PACKAGE) == {"name", "private", "description", "devDependencies"}          # nothing that publishes or runs scripts
    assert list(PACKAGE["devDependencies"]) == ["html-validate"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", PACKAGE["devDependencies"]["html-validate"])


def test_the_lock_matches_package_json_and_every_package_has_an_integrity_hash():
    assert LOCK["lockfileVersion"] == 3
    pkgs = LOCK["packages"]
    assert pkgs[""]["devDependencies"] == PACKAGE["devDependencies"]
    assert pkgs["node_modules/html-validate"]["version"] == PACKAGE["devDependencies"]["html-validate"]
    for path, info in pkgs.items():
        if not path:
            continue
        assert info.get("integrity", "").startswith("sha512-"), path
        assert info["resolved"].startswith("https://registry.npmjs.org/"), path               # no git or tarball URLs
        assert re.fullmatch(r"\d+\.\d+\.\d+", info["version"]), (path, info["version"])       # exact releases only


def test_no_locked_package_can_run_an_install_script():
    assert [p for p, info in LOCK["packages"].items() if info.get("hasInstallScript")] == []


def test_every_workflow_installs_with_npm_ci_without_scripts_and_never_fetches_the_validator_at_run_time():
    installs = 0
    for flow in WORKFLOWS:
        data = yaml.safe_load(flow.read_text(encoding="utf-8"))
        for job_id, job in data["jobs"].items():
            steps = job["steps"]
            for i, step in enumerate(steps):
                run = step.get("run", "")
                if re.search(r"\bnpm (ci|install)\b", run):
                    installs += 1
                    assert run.strip() == "npm ci --ignore-scripts --no-audit --no-fund", (flow.name, job_id, run)
                if "html-validate" in run:
                    assert "npx --no-install html-validate" in run and "--yes" not in run, (flow.name, job_id, run)
                    earlier = [s.get("run", "") for s in steps[:i]]
                    assert "npm ci --ignore-scripts --no-audit --no-fund" in earlier, (flow.name, job_id)
    assert installs == 3                                             # the PR checks job, the validate job and the axe job


def test_the_axe_folder_is_locked_the_same_way_and_holds_only_axe_and_the_browser_driver():
    assert AXE_PACKAGE["private"] is True
    assert set(AXE_PACKAGE) == {"name", "private", "description", "devDependencies"}      # nothing that publishes or runs scripts
    assert sorted(AXE_PACKAGE["devDependencies"]) == ["axe-core", "playwright-core"]
    assert all(re.fullmatch(r"\d+\.\d+\.\d+", v) for v in AXE_PACKAGE["devDependencies"].values())
    assert AXE_LOCK["lockfileVersion"] == 3
    pkgs = AXE_LOCK["packages"]
    assert pkgs[""]["devDependencies"] == AXE_PACKAGE["devDependencies"]
    # two packages and nothing they pull in: the driver has no dependencies, and no browser is downloaded
    assert sorted(p for p in pkgs if p) == ["node_modules/axe-core", "node_modules/playwright-core"]
    for path, info in pkgs.items():
        if not path:
            continue
        assert info["version"] == AXE_PACKAGE["devDependencies"][path.removeprefix("node_modules/")]
        assert info.get("integrity", "").startswith("sha512-"), path
        assert info["resolved"].startswith("https://registry.npmjs.org/"), path
        assert not info.get("hasInstallScript"), path


def test_the_two_locks_stay_apart():
    """The production `validate` job installs the root lock; the accessibility tools must never ride along."""
    root_names = {p.rsplit("node_modules/", 1)[-1] for p in LOCK["packages"]}
    assert not {"axe-core", "playwright-core", "playwright"} & root_names
    assert "html-validate" not in json.dumps(AXE_LOCK)


def test_only_the_pull_request_axe_job_installs_and_runs_the_axe_folder():
    """axe runs in a read-only job of checks.yml, never in a workflow that deploys or holds a write token, and its
    install is the same `npm ci --ignore-scripts`, inside axe/."""
    found = []
    for flow in WORKFLOWS:
        data = yaml.safe_load(flow.read_text(encoding="utf-8"))
        for job_id, job in data["jobs"].items():
            for step in job["steps"]:
                if step.get("working-directory") == "axe" or "axe/" in step.get("run", ""):
                    found.append((flow.name, job_id, step.get("working-directory"), step["run"].strip()))
    assert found == [("checks.yml", "axe", "axe", NPM_CI), ("checks.yml", "axe", None, "node axe/run.js site")]
    checks = yaml.safe_load((ROOT / ".github" / "workflows" / "checks.yml").read_text(encoding="utf-8"))
    assert checks["permissions"] == {"contents": "read"} and "permissions" not in checks["jobs"]["axe"]
    script = (ROOT / "axe" / "run.js").read_text(encoding="utf-8")
    assert "127.0.0.1" in script and "https://" not in script                             # loopback only
    assert 'require("playwright-core")' in script and 'require.resolve("axe-core/axe.min.js")' in script


def test_dependabot_watches_npm_in_both_folders():
    data = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    npm = [(u["directory"], u["schedule"]["interval"]) for u in data["updates"] if u["package-ecosystem"] == "npm"]
    assert npm == [("/", "weekly"), ("/axe", "weekly")]
