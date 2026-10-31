"""
The `data` branch: where the files only the update workflow writes are kept.

main is protected (a pull request and the `checks` status for every change), and the Actions bot cannot be
added to that ruleset's bypass list, so the bot's data commits go to a separate `data` branch instead. The
branch holds the BOT_FILES below at the same paths (data/series.json, ...) and nothing else, no code, so its
history is an audit trail of every data change. The hand-curated files (overrides.json, alumni.json,
endowment.json, bell-heists.json) stay on main and change only through pull requests.

Commands (run from anywhere inside the repository; git must be on the PATH):

  python scraper/databranch.py fetch                # copy the bot-managed files from the data branch into data/
  python scraper/databranch.py fetch --remote origin
  python scraper/databranch.py push                 # commit the changed bot-managed files to the data branch
  python scraper/databranch.py push --test-commit   # commit only a marker file, to prove the push works
  python scraper/databranch.py bootstrap --source origin/main   # one-off: make the data branch from main's blobs

`fetch` is how a workflow or a person gets the data. `--remote` defaults to the public repository, so it works
in a fork or a fresh clone too; the workflows pass `--remote origin`, which is the repository they run in.
A fetch that cannot get every file fails loudly: reading a missing schedule as "no games" would be worse.

`push` never changes main and never forces. It builds the commit from git objects (a temporary index, no
checkout, no worktree), so it uses whatever credentials the checkout already set up for `git push origin`.
If the branch moved since the fetch, it fetches the new tip, rebuilds the commit on top of it from this
run's files and tries once more; if that tree equals the new tip there is nothing left to commit.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

BRANCH = "data"
PUBLIC_REMOTE = "https://github.com/jacksonmote/DePauw-vs-Wabash-web.git"

# Every file only the update workflow writes (scrape.py and review.py), by name inside data/.
# Nothing else is ever committed to the data branch except MARKER.
BOT_FILES = ("depauw.json", "wabash.json", "series.json", "upcoming.json", "pending-changes.json")
# Written by `push --test-commit` and by nothing else; no reader looks at it.
MARKER = "data/.commit-test"

BOT_IDENTITY = ("github-actions[bot]", "41898282+github-actions[bot]@users.noreply.github.com")
DATA_MESSAGE = "Update DePauw vs. Wabash series data"
MARKER_MESSAGE = "Test the data branch push (marker file only, no data change)"
MODE = "100644"


class DataBranchError(Exception):
    pass


def git(*args: str, repo: Path = ROOT, input: bytes | None = None, env: dict | None = None,
        check: bool = True) -> subprocess.CompletedProcess:
    # The repository's own identity settings never matter here; the commit's authors are set per call.
    proc = subprocess.run(["git", "-C", str(repo), *args], input=input, capture_output=True,
                          stdin=None if input is not None else subprocess.DEVNULL,
                          env={**os.environ, **(env or {})})
    if check and proc.returncode != 0:
        raise DataBranchError(f"git {' '.join(args[:2])} failed: {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc


def text(proc: subprocess.CompletedProcess) -> str:
    return proc.stdout.decode("utf-8").strip()


def missing_files(data_dir: Path = DATA, names: tuple = BOT_FILES) -> list:
    return [n for n in names if not (Path(data_dir) / n).is_file()]


def require(data_dir: Path = DATA, names: tuple = BOT_FILES) -> None:
    """Raise a clear error when files that live on the data branch are not in `data_dir`."""
    gone = missing_files(data_dir, names)
    if gone:
        raise DataBranchError(
            f"missing {', '.join('data/' + n for n in gone)}. These files live on the `{BRANCH}` branch, not on "
            f"main. Get them with: python scraper/databranch.py fetch")


# ---- fetch ------------------------------------------------------------------------------------- #

def fetch_tip(remote: str, repo: Path = ROOT, branch: str = BRANCH) -> str:
    """Fetch the newest commit of the data branch (history not needed) and return its id."""
    proc = git("fetch", "--quiet", "--no-tags", "--depth", "1", remote, branch, repo=repo, check=False)
    if proc.returncode != 0:
        raise DataBranchError(f"could not fetch the `{branch}` branch from {remote}: "
                              f"{proc.stderr.decode('utf-8', 'replace').strip()}")
    return text(git("rev-parse", "FETCH_HEAD^{commit}", repo=repo))


def fetch(remote: str = PUBLIC_REMOTE, data_dir: Path = DATA, repo: Path = ROOT, branch: str = BRANCH) -> str:
    """Write every BOT_FILE from the branch's newest commit into `data_dir`, byte for byte. Returns the commit."""
    tip = fetch_tip(remote, repo, branch)
    blobs = {}
    for name in BOT_FILES:
        proc = git("show", f"{tip}:data/{name}", repo=repo, check=False)
        if proc.returncode != 0:
            raise DataBranchError(f"the `{branch}` branch has no data/{name} at {tip[:7]}")
        blobs[name] = proc.stdout
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    for name, content in blobs.items():
        (Path(data_dir) / name).write_bytes(content)
    return tip


# ---- push -------------------------------------------------------------------------------------- #

def tree_entries(commit: str, repo: Path) -> dict:
    """{path: blob id} for every file in a commit."""
    out = {}
    for line in text(git("ls-tree", "-r", commit, repo=repo)).splitlines():
        meta, path = line.split("\t", 1)
        out[path] = meta.split()[2]
    return out


def write_blob(content: bytes, repo: Path) -> str:
    # --no-filters: the bytes go in exactly as given, whatever core.autocrlf says.
    return text(git("hash-object", "-w", "--no-filters", "--stdin", repo=repo, input=content))


def commit_tree(entries: dict, parent: str | None, message: str, repo: Path, when: dt.datetime) -> str:
    """A commit holding exactly `entries` ({path: blob id}), by the bot, on top of `parent` (or a root commit)."""
    fd, index = tempfile.mkstemp(prefix="databranch-index-")
    os.close(fd)
    os.unlink(index)   # git wants to create it
    try:
        env = {"GIT_INDEX_FILE": index}
        for path, blob in sorted(entries.items()):
            git("update-index", "--add", "--cacheinfo", f"{MODE},{blob},{path}", repo=repo, env=env)
        tree = text(git("write-tree", repo=repo, env=env))
    finally:
        if os.path.exists(index):
            os.unlink(index)
    stamp = when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    who = {"GIT_AUTHOR_NAME": BOT_IDENTITY[0], "GIT_AUTHOR_EMAIL": BOT_IDENTITY[1], "GIT_AUTHOR_DATE": stamp,
           "GIT_COMMITTER_NAME": BOT_IDENTITY[0], "GIT_COMMITTER_EMAIL": BOT_IDENTITY[1], "GIT_COMMITTER_DATE": stamp}
    args = ["commit-tree", tree, "-m", message] + (["-p", parent] if parent else [])
    return text(git(*args, repo=repo, env=who))


def plan(tip: str, data_dir: Path, repo: Path, test_commit: bool, when: dt.datetime) -> tuple:
    """(entries for the new commit, message) from the branch's tip and this run's files."""
    entries = tree_entries(tip, repo)
    if test_commit:
        # Only the marker. Real data from this run is deliberately left out; the next normal run commits it.
        entries[MARKER] = write_blob(f"test_data_commit at {when.astimezone(dt.timezone.utc).isoformat()}\n"
                                     .encode("utf-8"), repo)
        return entries, MARKER_MESSAGE
    require(data_dir)
    for name in BOT_FILES:
        entries[f"data/{name}"] = write_blob((Path(data_dir) / name).read_bytes(), repo)
    if entries != tree_entries(tip, repo):
        entries.pop(MARKER, None)   # a leftover marker goes with the next real data commit, never alone
    return entries, DATA_MESSAGE


def push(remote: str = "origin", data_dir: Path = DATA, repo: Path = ROOT, branch: str = BRANCH,
         test_commit: bool = False, now: dt.datetime | None = None) -> str:
    """Commit this run's bot-managed files (or only the marker) to the data branch. Returns "pushed" or
    "unchanged" (nothing differs from the branch, so no commit). Never forces; raises DataBranchError when the
    push is still rejected after one rebuild on the branch's new tip."""
    when = now or dt.datetime.now(dt.timezone.utc)
    last = ""
    for attempt in (1, 2):
        tip = fetch_tip(remote, repo, branch)
        entries, message = plan(tip, data_dir, repo, test_commit, when)
        if entries == tree_entries(tip, repo):
            return "unchanged"
        commit = commit_tree(entries, tip, message, repo, when)
        proc = git("push", "--quiet", remote, f"{commit}:refs/heads/{branch}", repo=repo, check=False)
        if proc.returncode == 0:
            return "pushed"
        last = proc.stderr.decode("utf-8", "replace").strip()
        print(f"push to {branch} was rejected (attempt {attempt} of 2): {last}", file=sys.stderr)
    raise DataBranchError(f"the push to the `{branch}` branch was rejected twice: {last}")


# ---- bootstrap --------------------------------------------------------------------------------- #

def bootstrap(source: str, repo: Path = ROOT, branch: str = BRANCH, now: dt.datetime | None = None) -> str:
    """Create the local `data` branch as a root commit holding the BOT_FILES exactly as `source` (a ref such as
    origin/main) has them: the blobs are reused, not copied, so they are identical by construction."""
    if git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", repo=repo, check=False).returncode == 0:
        raise DataBranchError(f"a local `{branch}` branch already exists")
    entries = {}
    for name in BOT_FILES:
        proc = git("rev-parse", "--verify", f"{source}:data/{name}", repo=repo, check=False)
        if proc.returncode != 0:
            raise DataBranchError(f"{source} has no data/{name}")
        entries[f"data/{name}"] = text(proc)
    commit = commit_tree(entries, None, "Start the data branch from main's data/", repo,
                         now or dt.datetime.now(dt.timezone.utc))
    git("update-ref", f"refs/heads/{branch}", commit, repo=repo)
    return commit


# ---- command line ------------------------------------------------------------------------------ #

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    f = sub.add_parser("fetch", help="copy the bot-managed files from the data branch into data/")
    f.add_argument("--remote", default=PUBLIC_REMOTE, help="a remote name or URL (default: the public repository)")
    p = sub.add_parser("push", help="commit the changed bot-managed files to the data branch")
    p.add_argument("--remote", default="origin")
    p.add_argument("--test-commit", action="store_true", help=f"commit only {MARKER}, no real data")
    b = sub.add_parser("bootstrap", help="make the local data branch from the bot-managed files of a ref")
    b.add_argument("--source", default="origin/main")
    args = ap.parse_args(argv)
    try:
        if args.command == "fetch":
            tip = fetch(args.remote)
            print(f"Wrote {', '.join(BOT_FILES)} to data/ from the {BRANCH} branch ({tip[:7]}).")
        elif args.command == "push":
            result = push(args.remote, test_commit=args.test_commit)
            print("Pushed a commit to the data branch." if result == "pushed" else "No data changes.")
        else:
            commit = bootstrap(args.source)
            print(f"Created the local {BRANCH} branch at {commit[:7]}. Nothing was pushed.")
    except DataBranchError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
