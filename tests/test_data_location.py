"""Which data files live on main and which on the data branch, and what happens when the latter are missing."""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
import databranch  # noqa: E402
import render  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CURATED = ("overrides.json", "alumni.json", "endowment.json", "bell-heists.json")


def test_the_bot_files_and_the_curated_files_are_two_disjoint_lists_that_cover_data():
    assert not set(databranch.BOT_FILES) & set(CURATED)
    # Every file the scrape writes by default is a bot file, and nothing else is.
    import scrape
    written = {scrape.OUTPUT.name, scrape.UPCOMING.name, "depauw.json", "wabash.json", "pending-changes.json"}
    assert written == set(databranch.BOT_FILES)


def test_main_ignores_the_bot_files_and_does_not_track_them():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for name in databranch.BOT_FILES:
        assert f"data/{name}" in ignored
    assert databranch.MARKER in ignored
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "data"], capture_output=True, text=True,
                             stdin=subprocess.DEVNULL)
    if tracked.returncode == 0 and tracked.stdout.strip():          # skipped in an export with no .git
        assert sorted(Path(p).name for p in tracked.stdout.split()) == sorted(CURATED)


def test_missing_files_are_reported_by_name_with_the_fix(tmp_path):
    (tmp_path / "series.json").write_text("{}")
    gone = databranch.missing_files(tmp_path)
    assert gone == ["depauw.json", "wabash.json", "upcoming.json", "pending-changes.json"]
    assert "python scraper/databranch.py fetch" in databranch.missing_message(gone)


def test_render_says_where_the_data_is_instead_of_file_not_found(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(render, "DATA", tmp_path / "series.json")
    monkeypatch.setattr(render, "UPCOMING", tmp_path / "upcoming.json")
    assert render.main(["--out", str(tmp_path / "site")]) == 1
    err = capsys.readouterr().err
    assert "data/series.json" in err and "data/upcoming.json" in err
    assert "`data` branch" in err and "python scraper/databranch.py fetch" in err
    assert not (tmp_path / "site").exists()


def test_render_with_explicit_paths_is_not_affected(tmp_path):
    data = tmp_path / "s.json"
    data.write_text(json.dumps({}), encoding="utf-8")
    # an explicit --data that is not the default is the caller's business: no data-branch message
    assert render.main(["--data", str(data), "--upcoming", str(tmp_path / "u.json"), "--out", str(tmp_path / "o")]) == 1
