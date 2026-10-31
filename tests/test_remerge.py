"""scrape.py --remerge-only: rebuild series.json from the saved copies and the rulings, fetching nothing.

The data branch holds series.json, but the rulings (overrides.json) and the merge code live on main, so a
change to either leaves series.json stale until something re-merges it. The workflows do that first."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import scrape  # noqa: E402
from test_scrape import both_dir, fixture_dir  # noqa: E402,F401  (pytest fixtures)


def first_run(both_dir, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    assert scrape.main(["--fixtures", str(both_dir), "--output", str(data / "series.json"),
                        "--upcoming-output", str(data / "upcoming.json"),
                        "--checked-file", str(tmp_path / ".checked-at")]) == 0
    return data


def remerge(data, tmp_path, *extra):
    return scrape.main(["--remerge-only", "--output", str(data / "series.json"),
                        "--upcoming-output", str(data / "upcoming.json"),
                        "--checked-file", str(tmp_path / "checked-after"), *extra])


def snapshot(data):
    return {p.name: p.read_bytes() for p in data.iterdir()}


def test_nothing_to_change_changes_nothing_and_fetches_nothing(both_dir, tmp_path, monkeypatch):
    data = first_run(both_dir, tmp_path)
    before = snapshot(data)

    def no_network(*a, **k):
        raise AssertionError("--remerge-only must not fetch")
    monkeypatch.setattr(scrape, "fetch", no_network)
    assert remerge(data, tmp_path) == 0
    assert snapshot(data) == before                       # even updated_at is untouched
    assert not (tmp_path / "checked-after").exists()      # no live check happened, so no check time


def test_a_stale_series_file_is_rebuilt_from_the_saved_copies(both_dir, tmp_path):
    data = first_run(both_dir, tmp_path)
    good = json.loads((data / "series.json").read_text(encoding="utf-8"))
    stale = json.loads(json.dumps(good))
    stale["sports"]["football"]["games"].pop()
    stale["sports"]["football"]["games_played"] -= 1
    (data / "series.json").write_text(json.dumps(stale, indent=2) + "\n", encoding="utf-8")
    others = {n: b for n, b in snapshot(data).items() if n != "series.json"}

    assert remerge(data, tmp_path) == 0
    rebuilt = json.loads((data / "series.json").read_text(encoding="utf-8"))
    assert rebuilt["sports"] == good["sports"] and rebuilt["totals"] == good["totals"]
    assert {n: b for n, b in snapshot(data).items() if n != "series.json"} == others


def test_a_ruling_that_finds_nothing_keeps_that_sports_last_good_data_and_exits_non_zero(both_dir, tmp_path, capsys):
    data = first_run(both_dir, tmp_path)
    good = json.loads((data / "series.json").read_text(encoding="utf-8"))
    ruling = {"depauw": {"date": "1801-01-01", "score": "1-0"}, "wabash": {"date": "1801-01-02", "score": "1-0"},
              "reason": "a ruling whose rows are gone"}
    (data / "overrides.json").write_text(
        json.dumps({"schema_version": 1, "sports": {"football": {"same_game": [ruling]}}}), encoding="utf-8")

    assert remerge(data, tmp_path) == 1
    assert "no longer finds" in capsys.readouterr().err
    kept = json.loads((data / "series.json").read_text(encoding="utf-8"))
    assert kept["sports"] == good["sports"]               # football kept, every other sport re-merged unchanged


def test_remerge_does_not_touch_the_saved_copies_or_the_pending_file(both_dir, tmp_path):
    data = first_run(both_dir, tmp_path)
    saved = {n: b for n, b in snapshot(data).items() if n in ("depauw.json", "wabash.json", "pending-changes.json")}
    assert set(saved) == {"depauw.json", "wabash.json", "pending-changes.json"}
    assert remerge(data, tmp_path, "--allow-shrink", "--apply-reviewed") == 0
    assert {n: (data / n).read_bytes() for n in saved} == saved
