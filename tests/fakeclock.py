"""Run the whole test process under a fake clock (`pytest --fake-now=2026-11-14T13:30:00-05:00`).

The tests must not depend on the real date, so this lets anyone (and the `fake-clock` CI job) prove it:
every call to `datetime.now`, `date.today` and `time.time` in the process, including ones a test never
meant to make, sees the date given here, frozen. It uses time-machine (pinned in requirements-clock.txt),
which patches the clock at the C level, so a stray `from datetime import datetime` import is covered too.
Only the process itself is faked: a subprocess such as bash still sees the real time."""

import datetime as dt


def parse_fake_now(value: str) -> dt.datetime:
    """An ISO date and time with a UTC offset, e.g. 2026-11-14T13:30:00-05:00. The offset is required so a
    simulated moment means the same instant on every machine."""
    when = dt.datetime.fromisoformat(value)
    if when.tzinfo is None:
        raise ValueError(f"--fake-now needs a UTC offset (for example {value}-05:00), got {value!r}")
    return when


def start(value: str):
    """Freeze the clock at `value` and return the traveller; call .stop() on it to restore the real clock."""
    try:
        import time_machine
    except ImportError as err:       # only needed when --fake-now is used; not part of requirements.txt
        raise RuntimeError("--fake-now needs time-machine: pip install -r requirements-clock.txt") from err
    traveller = time_machine.travel(parse_fake_now(value), tick=False)
    traveller.start()
    return traveller
