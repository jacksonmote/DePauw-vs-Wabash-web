"""
Keep one GitHub issue (label data-review) in step with pending-changes.json (fetched from the data branch).

Runs in its own workflow job with only `issues: write`, after the build job has committed the pending
file. Uses the `gh` CLI that every runner has (GH_TOKEN and GITHUB_REPOSITORY come from the environment).
  - pending changes and no open issue: create one (and the label, if missing);
  - pending changes and an open issue: edit the oldest open one, but only when the text actually changed
    (the text carries a hash of itself);
  - nothing pending and an open issue: comment and close it ("applied" when the run approved changes).
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import review

ROOT = Path(__file__).resolve().parent.parent
PENDING = ROOT / "data" / "pending-changes.json"
LABEL_COLOR, LABEL_DESC = "d93f0b", "A school changed an old result; waiting for review"


def gh(*args: str, stdin: str | None = None) -> str:
    return subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, check=True).stdout


def sync(pending: list, applied: bool, run_url: str, run=gh) -> str:
    """Do whatever the issue needs and say what was done. `run` is the gh runner (replaced in tests)."""
    open_issues = sorted(json.loads(run("issue", "list", "--label", review.LABEL, "--state", "open",
                                        "--json", "number,body", "--limit", "100")), key=lambda i: i["number"])
    if not pending:
        if not open_issues:
            return "nothing pending and no open issue"
        n = str(open_issues[0]["number"])
        note = ("Approved: the changes listed here were applied by " if applied
                else "Nothing is waiting for review any more (the changes were rejected or the page changed back). Checked by ")
        run("issue", "comment", n, "--body-file", "-", stdin=f"{note}{run_url}".strip() + "\n")
        run("issue", "close", n)
        return f"closed #{n}"
    body = review.render_body(pending)
    if open_issues:
        issue = open_issues[0]
        if review.body_hash(issue["body"]) == review.body_hash(body):
            return f"issue #{issue['number']} is already up to date"
        run("issue", "edit", str(issue["number"]), "--body-file", "-", stdin=body)
        return f"updated #{issue['number']}"
    labels = {l["name"] for l in json.loads(run("label", "list", "--json", "name", "--limit", "200"))}
    if review.LABEL not in labels:
        run("label", "create", review.LABEL, "--color", LABEL_COLOR, "--description", LABEL_DESC)
    run("issue", "create", "--title", review.ISSUE_TITLE, "--label", review.LABEL, "--body-file", "-", stdin=body)
    return "opened a new issue"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pending", type=Path, default=PENDING)
    ap.add_argument("--applied", action="store_true", help="this run applied the reviewed changes")
    ap.add_argument("--run-url", default="")
    args = ap.parse_args(argv)
    print(sync(review.load_pending(args.pending), args.applied, args.run_url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
