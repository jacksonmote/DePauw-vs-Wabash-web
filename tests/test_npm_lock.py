"""The npm side of the lock: package.json names the HTML validator at one exact version, package-lock.json pins it and
everything it pulls in with integrity hashes and no install scripts, and the workflows install it with `npm ci
--ignore-scripts` and run it with `npx --no-install`, never fetching it at run time. Reads files only."""
import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
LOCK = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


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
    assert installs == 2                                                                      # the PR checks job and the validate job


def test_dependabot_watches_npm():
    text = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    assert "package-ecosystem: npm" in text
