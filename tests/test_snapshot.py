"""Data snapshots: release files and notes, the unchanged check, tags and archiving after the release."""

import datetime as dt
import inspect
import json
import re
import shutil
import sys
import types
import urllib.request
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
import snapshot  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = (ROOT / ".github" / "workflows" / "snapshot.yml").read_text(encoding="utf-8")
SERIES = json.loads((ROOT / "data" / "series.json").read_text(encoding="utf-8"))
DAY = dt.date(2027, 1, 2)


def quiet(*a, **k):
    pass


def fake_gh(releases=(), bodies=None):
    def run(*args):
        if args[:2] == ("release", "list"):
            return json.dumps([{"tagName": t} for t in releases])
        if args[:2] == ("release", "view"):
            return json.dumps({"body": (bodies or {})[args[2]]})
        raise AssertionError(args)
    return run


def data_copy(tmp_path):
    d = tmp_path / "data"
    shutil.copytree(ROOT / "data", d)
    return d


def test_the_notes_list_the_totals_and_every_sports_record():
    t = SERIES["totals"]
    notes = snapshot.release_notes(SERIES, "abc123", DAY)
    assert f"DePauw {t['depauw_wins']}, Wabash {t['wabash_wins']}, {snapshot.ties(t['ties'])}, {t['games_played']} games" in notes
    for s in SERIES["sports"].values():
        r = s["record"]
        assert f"- {s['name']}: DePauw {r['depauw_wins']}, Wabash {r['wabash_wins']}, {snapshot.ties(r['ties'])} (" in notes
    assert "Tennis: " in notes and "matches)" in notes                       # tennis counts matches
    assert snapshot.recorded_hash(notes) == "abc123"


def test_new_notes_say_the_archive_copies_are_being_saved():
    notes = snapshot.release_notes(SERIES, "abc", DAY)
    assert "Wayback Machine copies: being saved" in notes
    assert notes.count(snapshot.ARCHIVE_START) == 1 and notes.count(snapshot.ARCHIVE_END) == 1


def test_the_archive_section_lists_links_and_what_was_not_saved():
    sec = snapshot.archive_section(["https://web.archive.org/web/1/https://a"], ["https://x/1"])
    assert "- https://web.archive.org/web/1/https://a" in sec and "Not saved (1): https://x/1" in sec
    none = snapshot.archive_section([], ["https://x/1", "https://x/2"])
    assert "Wayback Machine copies: none could be saved this time." in none and "Not saved (2)" in none
    assert "not requested" in snapshot.archive_section(None, requested=False)


def test_updating_the_notes_replaces_only_the_placeholder():
    before = snapshot.release_notes(SERIES, "abc123", DAY)
    sec = snapshot.archive_section(["https://web.archive.org/web/1/https://a"], [])
    after = snapshot.replace_archive(before, sec)
    assert "being saved" not in after and "https://web.archive.org/web/1/https://a" in after
    head, tail = before.split(snapshot.ARCHIVE_START)[0], before.split(snapshot.ARCHIVE_END)[1]
    assert after.startswith(head) and after.endswith(tail)                    # the rest is byte for byte the same
    assert snapshot.recorded_hash(after) == "abc123"
    again = snapshot.replace_archive(after, snapshot.archive_section([], ["https://x"]))   # a retry replaces it again
    assert "none could be saved" in again and "https://web.archive.org/web/1" not in again
    assert again.startswith(head) and again.endswith(tail)


def test_notes_from_before_the_markers_are_updated_too():
    legacy = ("Snapshot.\n\nAttached: files.\n\nInternet Archive copies of the source pages, saved when this "
              "snapshot was made:\n\n- https://old\n\nNot saved (1): https://x\n\n<!-- data-hash: abc -->\n")
    after = snapshot.replace_archive(legacy, snapshot.archive_section(["https://new"], []))
    assert "https://old" not in after and "- https://new" in after and "Attached: files." in after
    assert snapshot.recorded_hash(after) == "abc"


def test_the_source_urls_are_the_seven_history_pages_of_each_school():
    cfg = json.loads((ROOT / "scraper" / "sources.json").read_text(encoding="utf-8"))
    urls = snapshot.source_urls(cfg)
    assert len(urls) == 14 and len(set(urls)) == 14
    assert sum("depauwtigers.com" in u for u in urls) == 7 and sum("sports.wabash.edu" in u for u in urls) == 7
    assert all("opponent-history" in u for u in urls)


def test_the_tag_is_the_date_and_only_gains_a_suffix_when_taken():
    assert snapshot.pick_tag(DAY, set()) == "data-2027-01-02"
    assert snapshot.pick_tag(DAY, {"data-2027-01-02"}) == "data-2027-01-02-2"
    assert snapshot.pick_tag(DAY, {"data-2027-01-02", "data-2027-01-02-2"}) == "data-2027-01-02-3"


def test_a_first_snapshot_counts_as_a_change():
    assert snapshot.changed_since_last_release(fake_gh([]))[0] is True
    assert snapshot.changed_since_last_release(fake_gh(["v1.0"]))[0] is True       # only data-* releases count


def test_unchanged_data_is_skipped_and_changed_data_is_not(tmp_path):
    d = data_copy(tmp_path)
    body = snapshot.release_notes(SERIES, snapshot.data_hash(d), DAY)
    run = fake_gh(["data-2026-07-01", "data-2027-01-02"], {"data-2027-01-02": body})
    assert snapshot.changed_since_last_release(run, d)[0] is False
    (d / "wabash.json").write_text((d / "wabash.json").read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert snapshot.changed_since_last_release(run, d)[0] is True


def test_the_schedule_file_does_not_count_as_a_change(tmp_path):
    d = data_copy(tmp_path)
    before = snapshot.data_hash(d)
    (d / "upcoming.json").write_text("{}", encoding="utf-8")
    assert snapshot.data_hash(d) == before


def test_archiving_collects_links_and_survives_failures():
    calls = []

    class Resp:
        def __init__(self, link):
            self.headers = {"Content-Location": link}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def geturl(self):
            return "https://web.archive.org/save/x"

    def opener(req, timeout):
        calls.append((req.full_url, timeout))
        if req.full_url.endswith("/bad"):
            raise OSError("429 Too Many Requests")
        return Resp("/web/20270102030405/" + req.full_url.removeprefix(snapshot.SAVE_URL))

    pauses = []
    links, failed = snapshot.wayback_save(["https://a/1", "https://a/bad", "https://a/3"], opener=opener,
                                          sleep=pauses.append, log=quiet)
    assert links == ["https://web.archive.org/web/20270102030405/https://a/1",
                     "https://web.archive.org/web/20270102030405/https://a/3"]
    assert failed == ["https://a/bad"] and len(calls) == 3 and pauses == [snapshot.SAVE_PAUSE] * 2
    assert all(t == snapshot.SAVE_TIMEOUT for _, t in calls)


class FakeClock:
    """Time that only moves when the code sleeps or a request 'takes' time."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def slow_opener(clock, seconds, seen):
    """Every request takes `seconds`, then times out."""
    def opener(req, timeout):
        seen.append(timeout)
        clock.sleep(seconds)
        raise TimeoutError("timed out")
    return opener


def test_the_budget_stops_archiving_and_lists_the_rest_as_not_saved():
    clock, seen = FakeClock(), []
    urls = [f"https://a/{i}" for i in range(12)]
    links, failed = snapshot.wayback_save(urls, opener=slow_opener(clock, snapshot.SAVE_BUDGET / 4, seen), sleep=clock.sleep,
                                          log=quiet, clock=clock)
    assert links == [] and failed == urls                                    # all 12 recorded, tried or not
    assert 0 < len(seen) < 12                                                 # it stopped early
    assert clock.now <= snapshot.SAVE_BUDGET + snapshot.SAVE_PAUSE + snapshot.SAVE_BUDGET / 4       # and did not run on past the budget
    assert all(t <= snapshot.SAVE_TIMEOUT for t in seen)


def test_the_last_request_is_never_given_more_time_than_the_budget_has_left():
    clock, seen = FakeClock(), []
    snapshot.wayback_save(["https://a/1", "https://a/2"], opener=slow_opener(clock, 0, seen), sleep=clock.sleep,
                          log=quiet, clock=clock, budget=63, timeout=60)
    assert seen == [60, 63 - snapshot.SAVE_PAUSE]


def test_a_page_that_times_out_is_recorded_as_not_saved_and_the_next_is_tried():
    clock = FakeClock()

    class Resp:
        headers = {"Content-Location": "/web/1/x"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def opener(req, timeout):
        clock.sleep(timeout)
        if req.full_url.endswith("/slow"):
            raise TimeoutError("timed out")
        return Resp()

    links, failed = snapshot.wayback_save(["https://a/slow", "https://a/ok"], opener=opener, sleep=clock.sleep,
                                          log=quiet, clock=clock)
    assert failed == ["https://a/slow"] and links == ["https://web.archive.org/web/1/x"]


class FakeRelease:
    """A fake `gh` holding one release's notes."""

    def __init__(self, notes, fail_edit=False):
        self.notes, self.fail_edit, self.edits = notes, fail_edit, []

    def __call__(self, *args):
        if args[:2] == ("release", "view"):
            return json.dumps({"body": self.notes})
        if args[:2] == ("release", "edit"):
            if self.fail_edit:
                raise RuntimeError("HTTP 500")
            self.edits.append(args[2])
            self.notes = Path(args[args.index("--notes-file") + 1]).read_text(encoding="utf-8")
            return ""
        raise AssertionError(args)


def test_a_run_where_every_page_fails_still_updates_the_notes_and_never_raises():
    gh = FakeRelease(snapshot.release_notes(SERIES, "abc123", DAY))
    assert snapshot.archive_release("data-2027-01-02", run=gh, saver=lambda urls: ([], list(urls)), log=quiet) is True
    assert "Wayback Machine copies: none could be saved this time." in gh.notes
    assert "Not saved (14)" in gh.notes and snapshot.recorded_hash(gh.notes) == "abc123"
    assert "being saved" not in gh.notes and gh.edits == ["data-2027-01-02"]


def test_archiving_never_raises_even_when_the_saver_the_edit_or_the_tag_is_bad():
    def boom(urls):
        raise RuntimeError("archive exploded")

    notes = snapshot.release_notes(SERIES, "abc", DAY)
    assert snapshot.archive_release("data-2027-01-02", run=FakeRelease(notes), saver=boom, log=quiet) is False
    assert snapshot.archive_release("data-2027-01-02", run=FakeRelease(notes, fail_edit=True),
                                    saver=lambda u: ([], list(u)), log=quiet) is False
    assert snapshot.archive_release("v1.0; echo hi", run=FakeRelease(notes), saver=boom, log=quiet) is False


def test_the_archive_command_exits_zero_whatever_happens(monkeypatch):
    def no_gh(*a):
        raise RuntimeError("no network")

    monkeypatch.setattr(snapshot, "gh", no_gh)
    monkeypatch.setattr(snapshot, "wayback_save", lambda urls, **k: ([], list(urls)))
    assert snapshot.main(["archive", "--tag", "data-2027-01-02"]) == 0           # gh fails: still 0
    assert snapshot.main(["archive", "--tag", "not-a-tag"]) == 0


FROZEN = ROOT / "tests" / "fixtures" / "series-frozen-2026-10-30.json"


def frozen_notes():
    """A release's notes made from the frozen series, so nothing here follows the bot-updated files."""
    return snapshot.release_notes(json.loads(FROZEN.read_text(encoding="utf-8")), "abc", DAY)


@pytest.mark.parametrize("tag", [
    "v1.0", "v1.0; echo hi", "data-2027-01-02; echo hi", "data-2027-01-02 --repo someone/else", "--help", "-R",
    "data-2027-1-2", "data-2027-01-02-", "data-2027-01-02-x", "DATA-2027-01-02", " data-2027-01-02", "data-2027-01-02\n",
    "latest", "", None])
def test_a_tag_that_is_not_a_snapshot_tag_never_reaches_gh_or_the_internet_archive(tag):
    """The tag comes from a box anyone with write access can type into. Everything here would work if it were let
    through (gh answers, the saver saves, the edit succeeds), so only the check on the tag can stop it."""
    gh = FakeRelease(frozen_notes())
    asked, saved = [], []

    def run(*args):
        asked.append(args)
        return gh(*args)

    def saver(urls):
        saved.extend(urls)
        return [], list(urls)
    assert snapshot.archive_release(tag, run=run, saver=saver, log=quiet) is False
    assert asked == [] and saved == [] and gh.edits == []


@pytest.mark.parametrize("tag", ["data-2027-01-02", "data-2027-01-02-2", "data-2026-07-01-13"])
def test_a_snapshot_tag_with_or_without_its_suffix_is_archived(tag):
    gh = FakeRelease(frozen_notes())
    assert snapshot.archive_release(tag, run=gh, saver=lambda urls: ([], list(urls)), log=quiet) is True
    assert gh.edits == [tag]


URLS = snapshot.source_urls(json.loads((ROOT / "scraper" / "sources.json").read_text(encoding="utf-8")))


def link(url, stamp="20261029173427"):
    return f"https://web.archive.org/web/{stamp}/{url}"


def notes_with(saved):
    """Release notes whose archive block already lists a link for each url in `saved`."""
    missing = [u for u in URLS if u not in saved]
    sec = snapshot.archive_section([link(u) for u in saved], missing)
    return snapshot.replace_archive(snapshot.release_notes(SERIES, "abc123", DAY), sec)


def test_a_retry_only_tries_pages_without_a_link_and_keeps_the_earlier_links():
    first = URLS[:11]
    gh, tried = FakeRelease(notes_with(first)), []

    def saver(urls):
        tried.append(list(urls))
        return [link(urls[0], "20261030010101")], urls[1:]

    assert snapshot.archive_release("data-2027-01-02", run=gh, saver=saver, log=quiet) is True
    assert tried == [URLS[11:]]                                              # the 3 missing pages, nothing else
    for u in first:
        assert link(u) in gh.notes                                           # nothing removed
    assert link(URLS[11], "20261030010101") in gh.notes
    assert f"Not saved (2): {URLS[12]}, {URLS[13]}" in gh.notes
    assert snapshot.recorded_hash(gh.notes) == "abc123"


def test_a_retry_that_saves_nothing_changes_only_the_not_saved_list():
    first = URLS[:11]
    before = notes_with(first)
    gh = FakeRelease(before)
    assert snapshot.archive_release("data-2027-01-02", run=gh, saver=lambda urls: ([], list(urls)), log=quiet)
    assert gh.notes == before                                                # same 11 links, same 3 not saved
    # and from a worse starting point (a "none could be saved" block) it stays that way
    none = notes_with([])
    gh = FakeRelease(none)
    snapshot.archive_release("data-2027-01-02", run=gh, saver=lambda urls: ([], list(urls)), log=quiet)
    assert gh.notes == none


def test_a_retry_when_every_page_is_already_saved_makes_no_requests_and_no_edit():
    gh = FakeRelease(notes_with(URLS))

    def saver(urls):
        raise AssertionError("nothing should be requested")

    assert snapshot.archive_release("data-2027-01-02", run=gh, saver=saver, log=quiet) is True
    assert gh.edits == [] and "Not saved" not in gh.notes


def test_a_retry_keeps_a_link_whose_page_is_no_longer_a_source():
    old = "https://example.org/removed-sport"
    notes = snapshot.replace_archive(snapshot.release_notes(SERIES, "abc", DAY),
                                     snapshot.archive_section([link(old)], URLS))
    gh = FakeRelease(notes)
    snapshot.archive_release("data-2027-01-02", run=gh, saver=lambda urls: ([], list(urls)), log=quiet)
    assert link(old) in gh.notes


def test_one_tie_is_not_pluralised():
    series = json.loads(json.dumps(SERIES))
    series["totals"]["ties"] = 1
    first_sport = next(iter(series["sports"].values()))
    first_sport["record"]["ties"] = 1
    notes = snapshot.release_notes(series, "abc", DAY)
    assert "1 tie," in notes and f"{first_sport['name']}: DePauw {first_sport['record']['depauw_wins']}, "         f"Wabash {first_sport['record']['wabash_wins']}, 1 tie (" in notes
    assert "1 ties" not in notes and "0 ties" in snapshot.release_notes(
        {**series, "totals": {**series["totals"], "ties": 0}}, "abc", DAY)


def test_building_writes_every_file_and_the_notes(tmp_path):
    out = tmp_path / "release"
    tag = snapshot.build(out, run=fake_gh(["data-2027-01-02"]), data_dir=ROOT / "data", today=DAY)
    assert tag == "data-2027-01-02-2" and (out / "TAG").read_text(encoding="utf-8").strip() == tag
    for name in ("series.json", "depauw.json", "wabash.json", "overrides.json", "upcoming.json", "games.csv", "NOTES.md"):
        assert (out / name).stat().st_size > 0, name
    rows = (out / "games.csv").read_text(encoding="utf-8").splitlines()
    assert len(rows) == SERIES["totals"]["games_played"] + 1                    # fresh, one row per game
    notes = (out / "NOTES.md").read_text(encoding="utf-8")
    assert snapshot.recorded_hash(notes) == snapshot.data_hash(ROOT / "data")
    assert "Wayback Machine copies: being saved" in notes


def test_building_without_archiving_says_so_in_the_notes(tmp_path):
    snapshot.build(tmp_path / "r", run=fake_gh([]), data_dir=ROOT / "data", today=DAY, archive=False)
    assert "not requested" in (tmp_path / "r" / "NOTES.md").read_text(encoding="utf-8")


def made_up_data(tmp_path):
    """A data folder that owes nothing to the bot-updated files: the frozen series and empty stand-ins."""
    d = tmp_path / "data"
    d.mkdir()
    for name in snapshot.DATA_FILES:
        (d / name).write_text("{}\n", encoding="utf-8")
    (d / "series.json").write_bytes(FROZEN.read_bytes())
    return d


def clock_at(monkeypatch, instant):
    """Make snapshot.py's own clock read `instant` (an aware time), whatever the real or the frozen clock says."""
    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
    module = types.ModuleType("datetime")
    module.__dict__.update({name: value for name, value in vars(dt).items() if not name.startswith("__")})
    module.datetime = Clock
    monkeypatch.setattr(snapshot, "dt", module)


@pytest.mark.parametrize("utc, tag", [
    ("2027-01-02T03:30", "data-2027-01-01"),      # 10:30 PM on Jan 1 in Indiana: the UTC date is a day ahead
    ("2027-01-02T04:59", "data-2027-01-01"),      # 11:59 PM EST
    ("2027-01-02T05:00", "data-2027-01-02"),      # midnight EST
    ("2027-01-02T15:41", "data-2027-01-02"),      # the January schedule's own minute
    ("2027-07-02T03:59", "data-2027-07-01"),      # 11:59 PM EDT
    ("2027-07-02T04:00", "data-2027-07-02"),      # midnight EDT
])
def test_a_snapshot_is_dated_by_the_day_in_eastern_time_not_the_day_in_utc(tmp_path, monkeypatch, utc, tag):
    clock_at(monkeypatch, dt.datetime.fromisoformat(utc).replace(tzinfo=dt.timezone.utc))
    out = tmp_path / "release"
    assert snapshot.build(out, run=fake_gh([]), data_dir=made_up_data(tmp_path)) == tag
    assert (out / "TAG").read_text(encoding="utf-8") == tag + "\n"
    day = dt.date.fromisoformat(tag[len("data-"):])
    assert (out / "NOTES.md").read_text(encoding="utf-8") == snapshot.release_notes(
        json.loads((out / "series.json").read_text(encoding="utf-8")), snapshot.data_hash(out), day,
        snapshot.archive_section(None, requested=True))


def test_building_the_release_makes_no_requests_to_the_internet_archive(tmp_path, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("the build must not touch the network")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    monkeypatch.setattr(snapshot, "wayback_save", no_network)
    snapshot.build(tmp_path / "r", run=fake_gh([]), data_dir=ROOT / "data", today=DAY)
    assert "wayback_save" not in inspect.getsource(snapshot.build) and "saver" not in inspect.getsource(snapshot.build)


def test_the_workflow_is_scheduled_twice_a_year_and_pinned():
    assert 'cron: "23 14 1 7 *"' in WORKFLOW and 'cron: "41 15 2 1 *"' in WORKFLOW
    assert "workflow_dispatch:" in WORKFLOW
    for line in re.findall(r"uses: (\S+)", WORKFLOW):
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", line), line
    assert all(re.search(r"uses: \S+@[0-9a-f]{40} # v\d", l) for l in WORKFLOW.splitlines() if "uses:" in l)


def job(name):
    """The text of one job in the workflow."""
    jobs = WORKFLOW.split("\njobs:\n")[1]
    m = re.search(rf"^  {name}:\n(.*?)(?=^  \w[\w-]*:\n|\Z)", jobs, re.M | re.S)
    return m.group(1)


def test_only_the_release_and_archive_jobs_can_write():
    assert len(re.findall(r"^\s+contents: write$", WORKFLOW, re.M)) == 2
    assert "contents: write" in job("release") and "contents: write" in job("archive")
    assert "contents: write" not in job("prepare") and "contents: read" in job("prepare")
    assert re.search(r"^permissions:\n  contents: read", WORKFLOW, re.M)


def test_the_jobs_run_in_the_order_prepare_release_archive():
    assert re.findall(r"^  (\w+):\n", WORKFLOW.split("\njobs:\n")[1], re.M) == ["prepare", "release", "archive"]
    assert "needs: prepare" in job("release") and "needs: [prepare, release]" in job("archive")
    assert "snapshot.py archive" not in job("prepare") and "snapshot.py archive" not in job("release")


FLOW = yaml.safe_load(WORKFLOW)


def test_a_release_is_made_only_when_the_check_says_the_data_changed():
    """Three links, each of which alone would make a release out of unchanged data if it went: the check's answer
    becomes the prepare job's output, every later step of that job waits for it, and so does the release job."""
    prepare, release = FLOW["jobs"]["prepare"], FLOW["jobs"]["release"]
    steps = prepare["steps"]
    [check] = [s for s in steps if s.get("id") == "check"]
    assert check["run"] == "python3 scraper/snapshot.py check"
    assert "if" not in check and "continue-on-error" not in check
    assert prepare.get("outputs") == {"changed": "${{ steps.check.outputs.changed }}"}
    after = steps[steps.index(check) + 1:]
    assert [s.get("name") or s["uses"].split("@")[0] for s in after] == [
        "actions/setup-python", "Build the release files", "actions/upload-artifact"]
    for step in after:
        assert step.get("if") == "${{ steps.check.outputs.changed == 'true' }}", step
    assert release.get("needs") == "prepare"
    # the whole condition, on purpose: the data changed, and the run is on main (a hand run on a branch reads
    # main's data, so without the guard it would publish a real data-<today> release tagged at the branch commit)
    assert release.get("if") == "${{ needs.prepare.outputs.changed == 'true' && github.ref == 'refs/heads/main' }}"
    assert "continue-on-error" not in prepare
    creating = [name for name, job in FLOW["jobs"].items() if "gh release create" in json.dumps(job)]
    assert creating == ["release"]


def test_archiving_runs_only_on_main_even_for_a_retry():
    """The archive job can run without the release job (a retry with archive_release_tag), so it needs a main-only
    guard of its own, outside the "or": a hand run on a branch would otherwise edit a real release's notes and send
    Save Page Now requests. The whole condition, on purpose, so a guard moved inside the brackets fails here."""
    assert FLOW["jobs"]["archive"].get("if") == (
        "${{ always() && github.ref == 'refs/heads/main' && github.event.inputs.archive != 'false' && "
        "(github.event.inputs.archive_release_tag != '' || needs.release.result == 'success') }}")


def test_the_check_command_writes_its_answer_where_the_workflow_reads_it(tmp_path, monkeypatch, capsys):
    for changed, word in ((True, "true"), (False, "false")):
        out = tmp_path / f"output-{word}"
        monkeypatch.setenv("GITHUB_OUTPUT", str(out))
        monkeypatch.setattr(snapshot, "changed_since_last_release", lambda changed=changed: (changed, "why"))
        assert snapshot.main(["check"]) == 0
        assert out.read_text(encoding="utf-8") == f"changed={word}\n"
        assert capsys.readouterr().out == f"changed={word}: why\n"


def test_two_snapshot_runs_never_overlap_and_a_running_one_is_never_cancelled():
    """Two runs at once would both see "no release for today yet" and try to make the same tag."""
    assert FLOW.get("concurrency") == {"group": "data-snapshot", "cancel-in-progress": False}
    for job in FLOW["jobs"].values():
        assert "concurrency" not in job                                       # one group, for the whole run


def test_the_archive_job_can_never_fail_the_run():
    a = job("archive")
    assert "continue-on-error: true" in a and "timeout-minutes: 40" in a
    assert "always()" in a                                                    # runs even when prepare was skipped
    assert "continue-on-error" not in job("release")                          # the release is the part that matters
    assert "timeout-minutes: 10" in job("prepare")


def test_manual_runs_can_skip_archiving_or_retry_it_for_an_existing_release():
    head = WORKFLOW.split("\npermissions:\n")[0]
    assert re.search(r"archive:\n\s+description:.*\n\s+type: boolean\n\s+default: true", head)
    assert "archive_release_tag:" in head
    assert "archive_release_tag == ''" in job("prepare")                      # a retry makes no new snapshot
    assert "archive != 'false'" in job("archive")
    assert "needs.release.outputs.tag" in job("archive")
    run_step = job("archive").split("run:")[1]
    assert '"$TAG"' in run_step and "${{" not in run_step                     # the tag goes through env, not the shell


def test_the_time_limits_are_named_constants():
    assert snapshot.SAVE_BUDGET == 1800 and snapshot.SAVE_TIMEOUT == 180 and snapshot.SAVE_PAUSE == 6
