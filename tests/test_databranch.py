"""scraper/databranch.py against real git: a bare repository in tmp_path stands in for GitHub, so no network.

Every git call names its identity and branch explicitly, so these pass on a runner or a laptop with no git
configuration (and with core.autocrlf on, as on Windows)."""
import datetime as dt
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
import databranch  # noqa: E402
from databranch import BOT_FILES, MARKER, DataBranchError  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NOW = dt.datetime(2026, 11, 14, 18, 7, tzinfo=dt.timezone.utc)
ID = ["-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "init.defaultBranch=main"]


def sh(repo, *args, check=True):
    proc = subprocess.run(["git", *ID, "-C", str(repo), *args], capture_output=True, stdin=subprocess.DEVNULL)
    if check:
        assert proc.returncode == 0, proc.stderr.decode()
    return proc.stdout.decode().strip()


def content(name, version=1):
    # A CRLF inside one file proves the bytes are never normalised.
    return f'{{"file": "{name}", "version": {version}}}\r\n'.encode() if name == "series.json" \
        else f'{{"file": "{name}", "version": {version}}}\n'.encode()


def write_data(data_dir, version=1):
    data_dir.mkdir(parents=True, exist_ok=True)
    for n in BOT_FILES:
        (data_dir / n).write_bytes(content(n, version))


@pytest.fixture
def world(tmp_path):
    """origin (bare, holding a data branch bootstrapped from a 'main'), a working clone and a data/ folder."""
    origin = tmp_path / "origin.git"
    sh(tmp_path, "init", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    sh(tmp_path, "init", "-b", "main", str(work))
    sh(work, "config", "user.name", "Test")
    sh(work, "config", "user.email", "test@example.com")
    sh(work, "config", "core.autocrlf", "false")          # the machine's own setting must not rewrite the CRLF below
    for n in BOT_FILES:                                   # main as it is today: the bot files are still on it
        (work / "data").mkdir(exist_ok=True)
        (work / "data" / n).write_bytes(content(n))
    (work / "data" / "overrides.json").write_text("{}\n")
    sh(work, "add", "-A")
    sh(work, "commit", "-m", "main")
    sh(work, "config", "core.autocrlf", "true")           # as on Windows: nothing below may be affected by it
    sh(work, "remote", "add", "origin", str(origin))
    databranch.bootstrap("main", repo=work, now=NOW)
    sh(work, "push", "origin", "data")
    scratch = tmp_path / "scratch" / "data"
    write_data(scratch)
    return type("World", (), dict(origin=origin, work=work, data=scratch, tmp=tmp_path))


def branch_files(world, ref="data", repo=None):
    repo = repo or world.origin
    out = sh(repo, "ls-tree", "-r", "--name-only", ref)
    return sorted(out.splitlines())


def blob(world, path, ref="data"):
    return subprocess.run(["git", "-C", str(world.origin), "show", f"{ref}:{path}"], capture_output=True,
                          stdin=subprocess.DEVNULL).stdout


# ---- bootstrap --------------------------------------------------------------------------------- #

def test_bootstrap_reuses_mains_blobs_and_holds_only_the_bot_files(world):
    for n in BOT_FILES:
        assert sh(world.work, "rev-parse", f"main:data/{n}") == sh(world.work, "rev-parse", f"data:data/{n}")
    assert branch_files(world, repo=world.work) == sorted(f"data/{n}" for n in BOT_FILES)
    assert sh(world.work, "rev-list", "--parents", "-n1", "refs/heads/data").count(" ") == 0       # a root commit
    assert sh(world.work, "log", "-1", "--format=%s", "refs/heads/data") == "Start the data branch from main's data/"
    assert sh(world.work, "log", "-1", "--format=%an <%ae>", "refs/heads/data") == "Test <test@example.com>"   # a person, not the bot


def test_bootstrap_refuses_to_overwrite_a_branch_and_a_source_without_the_files(world):
    with pytest.raises(DataBranchError, match="already exists"):
        databranch.bootstrap("main", repo=world.work)
    sh(world.work, "branch", "-D", "data")
    sh(world.work, "rm", "-q", "data/upcoming.json")
    sh(world.work, "commit", "-m", "drop")
    with pytest.raises(DataBranchError, match="no data/upcoming.json"):
        databranch.bootstrap("main", repo=world.work)


# ---- fetch ------------------------------------------------------------------------------------- #

def test_fetch_writes_every_bot_file_byte_for_byte(world, tmp_path):
    out = tmp_path / "fresh" / "data"
    tip = databranch.fetch(str(world.origin), data_dir=out, repo=world.work)
    assert tip == sh(world.origin, "rev-parse", "data")
    assert {p.name: p.read_bytes() for p in out.iterdir()} == {n: content(n) for n in BOT_FILES}
    assert b"\r\n" in (out / "series.json").read_bytes()


def test_fetch_fails_loudly_when_the_branch_or_a_file_is_missing(world, tmp_path):
    with pytest.raises(DataBranchError, match="could not fetch the `data` branch"):
        databranch.fetch(str(tmp_path / "nowhere.git"), data_dir=tmp_path / "d", repo=world.work)
    with pytest.raises(DataBranchError, match="could not fetch the `nope` branch"):
        databranch.fetch(str(world.origin), data_dir=tmp_path / "d", repo=world.work, branch="nope")
    assert not (tmp_path / "d").exists()                  # nothing half-written


def test_fetch_writes_nothing_when_one_file_is_missing_on_the_branch(world, tmp_path):
    sh(world.work, "checkout", "-q", "data")
    sh(world.work, "rm", "-q", "data/wabash.json")
    sh(world.work, "commit", "-m", "oops")
    sh(world.work, "push", "origin", "data")
    with pytest.raises(DataBranchError, match="no data/wabash.json"):
        databranch.fetch(str(world.origin), data_dir=tmp_path / "d", repo=world.work)
    assert not (tmp_path / "d").exists()


def test_require_names_the_missing_files_and_the_fix(tmp_path):
    (tmp_path / "series.json").write_text("{}")
    with pytest.raises(DataBranchError) as e:
        databranch.require(tmp_path)
    msg = str(e.value)
    assert "data/depauw.json" in msg and "data/upcoming.json" in msg and "data/series.json" not in msg
    assert "python scraper/databranch.py fetch" in msg
    write_data(tmp_path)
    databranch.require(tmp_path)


# ---- push -------------------------------------------------------------------------------------- #

def test_a_data_change_is_one_bot_commit_on_the_data_branch_and_only_that_branch(world):
    old = sh(world.origin, "rev-parse", "data")
    (world.data / "series.json").write_bytes(content("series.json", 2))
    (world.data / "alumni.json").write_text("{}")        # a curated file in data/ must never travel
    (world.data / "notes.txt").write_text("private")
    assert databranch.push(str(world.origin), world.data, world.work, now=NOW) == "pushed"
    new = sh(world.origin, "rev-parse", "data")
    assert new != old and sh(world.origin, "rev-parse", f"{new}^") == old
    assert sh(world.origin, "diff", "--name-only", old, new) == "data/series.json"
    assert branch_files(world) == sorted(f"data/{n}" for n in BOT_FILES)
    assert blob(world, "data/series.json") == content("series.json", 2)
    assert sh(world.origin, "log", "-1", "--format=%an <%ae>|%s", "data") == \
        "github-actions[bot] <41898282+github-actions[bot]@users.noreply.github.com>|" + databranch.DATA_MESSAGE
    assert sh(world.origin, "branch", "--format=%(refname:short)") == "data"      # no other branch was touched


def test_no_change_means_no_commit(world):
    old = sh(world.origin, "rev-parse", "data")
    assert databranch.push(str(world.origin), world.data, world.work, now=NOW) == "unchanged"
    assert sh(world.origin, "rev-parse", "data") == old


def test_a_missing_local_file_stops_the_push(world):
    (world.data / "upcoming.json").unlink()
    with pytest.raises(DataBranchError, match="data/upcoming.json"):
        databranch.push(str(world.origin), world.data, world.work, now=NOW)


def test_the_test_commit_adds_only_the_marker_and_leaves_real_data_alone(world):
    old = sh(world.origin, "rev-parse", "data")
    write_data(world.data, version=9)                     # this run scraped new data; the test must not commit it
    assert databranch.push(str(world.origin), world.data, world.work, test_commit=True, now=NOW) == "pushed"
    assert sh(world.origin, "diff", "--name-only", old, "data") == MARKER
    assert sh(world.origin, "diff", "--name-status", old, "data").startswith("A\t")
    assert blob(world, "data/series.json") == content("series.json")
    assert blob(world, MARKER) == b"test_data_commit at 2026-11-14T18:07:00+00:00\n"
    assert sh(world.origin, "log", "-1", "--format=%s", "refs/heads/data") == databranch.MARKER_MESSAGE


def test_the_marker_is_removed_by_the_next_real_data_commit_but_never_by_itself(world):
    databranch.push(str(world.origin), world.data, world.work, test_commit=True, now=NOW)
    tip = sh(world.origin, "rev-parse", "data")
    assert databranch.push(str(world.origin), world.data, world.work, now=NOW) == "unchanged"   # a normal run, no data change
    assert sh(world.origin, "rev-parse", "data") == tip and MARKER in branch_files(world)
    (world.data / "wabash.json").write_bytes(content("wabash.json", 2))
    assert databranch.push(str(world.origin), world.data, world.work, now=NOW) == "pushed"
    assert MARKER not in branch_files(world)
    assert blob(world, "data/wabash.json") == content("wabash.json", 2)


def test_a_rejected_push_is_rebuilt_on_the_new_tip_and_tried_once_more(world, monkeypatch):
    stale = sh(world.origin, "rev-parse", "data")
    other = world.tmp / "other"
    write_data(other, version=5)
    databranch.push(str(world.origin), other, world.work, now=NOW)       # another run moves the branch
    moved = sh(world.origin, "rev-parse", "data")
    assert moved != stale

    real, calls = databranch.fetch_tip, []

    def fetch_tip(remote, repo, branch):
        calls.append(1)
        if len(calls) == 1:
            real(remote, repo, branch)                                    # fetch, but report the old tip
            return stale
        return real(remote, repo, branch)
    monkeypatch.setattr(databranch, "fetch_tip", fetch_tip)

    (world.data / "series.json").write_bytes(content("series.json", 7))
    assert databranch.push(str(world.origin), world.data, world.work, now=NOW) == "pushed"
    assert len(calls) == 2
    final = sh(world.origin, "rev-parse", "data")
    assert sh(world.origin, "rev-parse", f"{final}^") == moved           # linear history, nothing overwritten by force
    assert blob(world, "data/series.json") == content("series.json", 7)


def test_two_rejections_in_a_row_are_an_error_that_leaves_the_branch_alone(world, monkeypatch, capsys):
    old = sh(world.origin, "rev-parse", "data")
    real_git = databranch.git

    def refuse_pushes(*args, **kw):
        if args and args[0] == "push":
            return subprocess.CompletedProcess(args, 1, b"", b"! [rejected] (fetch first)")
        return real_git(*args, **kw)
    monkeypatch.setattr(databranch, "git", refuse_pushes)
    (world.data / "series.json").write_bytes(content("series.json", 2))
    with pytest.raises(DataBranchError, match="rejected twice"):
        databranch.push(str(world.origin), world.data, world.work, now=NOW)
    assert capsys.readouterr().err.count("was rejected (attempt") == 2
    assert sh(world.origin, "rev-parse", "data") == old


def test_the_module_never_forces_a_push():
    src = (ROOT / "scraper" / "databranch.py").read_text(encoding="utf-8")
    code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
    assert "--force" not in code.replace('never forces', "") and '"-f"' not in code
    assert "+refs" not in code and '"+' not in code
    pushes = [line for line in code.splitlines() if 'git("push"' in line]
    assert len(pushes) == 1 and "refs/heads/{branch}" in pushes[0] and "refs/heads/main" not in code


# ---- the command line -------------------------------------------------------------------------- #

def test_the_command_line_fetches_pushes_and_reports_errors(world, tmp_path, monkeypatch, capsys):
    out = tmp_path / "cli-data"
    real_fetch, real_push = databranch.fetch, databranch.push     # the defaults point at the real repo; aim them at the test's
    monkeypatch.setattr(databranch, "fetch", lambda remote: real_fetch(remote, data_dir=out, repo=world.work))
    monkeypatch.setattr(databranch, "push", lambda remote, test_commit=False: real_push(
        remote, data_dir=world.data, repo=world.work, test_commit=test_commit, now=NOW))
    assert databranch.main(["fetch", "--remote", str(world.origin)]) == 0
    assert (out / "series.json").read_bytes() == content("series.json")
    assert databranch.main(["push", "--remote", str(world.origin), "--test-commit"]) == 0
    assert MARKER in branch_files(world)
    assert databranch.main(["fetch", "--remote", str(world.tmp / "missing.git")]) == 1
    assert "FAIL could not fetch" in capsys.readouterr().err
