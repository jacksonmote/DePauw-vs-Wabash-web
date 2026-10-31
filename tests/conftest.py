"""Guards that keep every test independent of the outside world.

Tests gate every production deploy, so one that starts failing by itself (because the date changed, a page
changed, the network was down) would stop the site updating on a game day. These guards make the usual
causes fail loudly in the test that causes them, on any day:

  * the network: any attempt to open a connection, or to run gh, npm, npx, node, curl or wget, raises;
  * the environment: GITHUB_* and GH_* variables from a CI runner are removed, so a test never writes to a
    real run summary or output file and behaves the same on a laptop and in Actions;
  * the clock: `--fake-now` (or FAKE_NOW) runs the whole process at a fixed moment, see fakeclock.py.

Tests use fixtures (tests/fixtures), tmp_path copies of the data, and an injected `now`; they never read the
real clock, the network, or the bot-updated files in data/ for an exact count."""

import datetime as dt
import itertools
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
import fakeclock  # noqa: E402
import databranch  # noqa: E402
import scrape  # noqa: E402

OUTSIDE_TOOLS = {"gh", "npm", "npx", "node", "curl", "wget"}
_fake_clock = None


def pytest_addoption(parser):
    parser.addoption("--fake-now", default=os.environ.get("FAKE_NOW"), metavar="ISO",
                     help="run the whole test process with the clock frozen at this moment, with a UTC offset "
                          "(for example 2026-11-14T13:30:00-05:00); also read from FAKE_NOW")


def pytest_configure(config):
    global _fake_clock
    value = config.getoption("--fake-now")
    if value:
        _fake_clock = fakeclock.start(value)


def pytest_unconfigure(config):
    global _fake_clock
    if _fake_clock is not None:
        _fake_clock.stop()
        _fake_clock = None


def pytest_sessionstart(session):
    """Several tests check rules on the real data files, which live on the data branch and not on main. Stop with
    the fix instead of a FileNotFoundError from deep inside a test module."""
    gone = databranch.missing_files()
    if gone:
        pytest.exit(f"{databranch.missing_message(gone)}", returncode=3)


def pytest_report_header(config):
    value = config.getoption("--fake-now")
    return f"fake clock: frozen at {value}" if value else "fake clock: off (real time)"


@pytest.fixture(autouse=True)
def no_network_and_no_outside_tools(monkeypatch):
    def refuse_network(*args, **kwargs):
        raise RuntimeError("a test tried to use the network; use a fixture or an injected opener instead")

    for target, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"),
                         (socket, "create_connection"), (socket, "getaddrinfo")):
        monkeypatch.setattr(target, name, refuse_network)

    real_popen = subprocess.Popen

    class GuardedPopen(real_popen):
        def __init__(self, args, *more, **kwargs):
            first = args if isinstance(args, (str, bytes, os.PathLike)) else (args[0] if args else "")
            if Path(os.fsdecode(first).split()[0] if os.fsdecode(first).split() else "").stem in OUTSIDE_TOOLS:
                raise RuntimeError(f"a test tried to run {first!r}; inject a fake runner instead")
            super().__init__(args, *more, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", GuardedPopen)


@pytest.fixture(autouse=True)
def scraper_clock_is_a_fixed_date(monkeypatch):
    """scrape.now_utc stamps updated_at and picks "today" for the schedule carry-over. It starts at a fixed
    instant and moves one second per call, so a test sees the same date every day, and one that checks a stamp
    stays put (or changes) between two runs still can."""
    ticks = itertools.count()
    start = dt.datetime(2026, 10, 30, 12, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(scrape, "now_utc", lambda: start + dt.timedelta(seconds=next(ticks)))


@pytest.fixture(autouse=True)
def clean_ci_environment(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("GITHUB_", "GH_")) or name in {"CI", "RUNNER_TEMP"}:
            monkeypatch.delenv(name)
