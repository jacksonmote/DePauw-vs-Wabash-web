"""Every workflow job runs on one pinned runner image, never `ubuntu-latest`.

GitHub moves the `ubuntu-latest` label to a new Ubuntu release over several weeks, so a run could land on the new
image without any change of ours, including on a game day. Moving to a newer image is a deliberate edit of every
`runs-on:` line, tried on a pull request first; this test fails when the lines disagree or use a moving label.

Reads the workflow files only: no network, no clock."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
PINNED = "ubuntu-24.04"


def jobs():
    """[(workflow file, job id, job)] for every job in every workflow."""
    out = []
    for flow in WORKFLOWS:
        data = yaml.safe_load(flow.read_text(encoding="utf-8"))
        out += [(flow.name, job_id, job) for job_id, job in data["jobs"].items()]
    return out


def test_there_are_eleven_jobs_across_the_three_workflows():
    assert [f.name for f in WORKFLOWS] == ["checks.yml", "snapshot.yml", "update-and-deploy.yml"]
    assert len(jobs()) == 11


@pytest.mark.parametrize("flow, job_id, job", jobs(), ids=lambda v: v if isinstance(v, str) else "")
def test_every_job_runs_on_the_one_pinned_image(flow, job_id, job):
    assert job["runs-on"] == PINNED, f"{flow}: job {job_id} runs on {job['runs-on']!r}"


def test_no_workflow_text_uses_a_moving_runner_label():
    for flow in WORKFLOWS:
        text = flow.read_text(encoding="utf-8")
        for label in ("ubuntu-latest", "windows-latest", "macos-latest"):
            assert label not in text, f"{flow.name} mentions {label}"


def test_the_required_status_check_keeps_its_name():
    """The ruleset requires a check called `checks`, matched by job name; pinning the image must not rename it."""
    data = yaml.safe_load((ROOT / ".github" / "workflows" / "checks.yml").read_text(encoding="utf-8"))
    assert "checks" in data["jobs"] and "name" not in data["jobs"]["checks"]
