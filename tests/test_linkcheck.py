"""The monthly link check (scraper/linkcheck.py, .github/workflows/link-check.yml).

No network and no outside programs: links come from the small fixture site in tests/fixtures/linkcheck, every
request goes to a fake transport, time is a fake clock, and the issue sync gets a fake gh runner. The links within
the site are checked on two full builds from the frozen fixture, with a fixed clock."""

import ast
import datetime as dt
import json
import re
from pathlib import Path

import pytest
import yaml

import linkcheck
import render

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures" / "linkcheck"
WORKFLOW = ROOT / ".github" / "workflows" / "link-check.yml"
WHEN = dt.datetime(2026, 11, 2, 19, 41, tzinfo=dt.timezone.utc)


def collect():
    return linkcheck.collect(FIX / "rendered", FIX / "data", FIX / "sources.json")


# ---- collecting -------------------------------------------------------------------------------- #

def test_the_constants_match_the_renderer():
    assert linkcheck.SITE_URL == render.SITE_URL
    assert linkcheck.REPO_URL == render.REPO_URL
    assert set(linkcheck.SOURCE_HOSTS) == set(render.SOURCE_HOSTS)


def test_links_are_deduped_without_their_fragment_and_remember_every_use():
    links, _ = collect()
    assert links["https://en.wikipedia.org/wiki/Monon_Bell"] == ["/", "/alumni/"]
    assert links["https://www.example.edu/people/one"] == ["/alumni/", "data/alumni.json: person-one: school_source"]
    assert links["https://directory.example.gov/one.pdf"] == ["data/alumni.json: person-one: qualifications.senators.source"]
    assert links["https://nces.example.gov/?id=150400"] == ["data/endowment.json: schools.depauw.enrollment.source_url"]
    assert links["https://example.org/page?a=1&b=2"] == ["/"]                         # &amp; read as &


def test_only_outside_links_are_checked_and_every_skip_rule_applies():
    links, skipped = collect()
    assert sorted(links) == [
        "https://directory.example.gov/one.pdf",
        "https://docs.github.com/en/pages",                                        # GitHub, but not this repository
        "https://en.wikipedia.org/wiki/Monon_Bell",
        "https://example.org/page?a=1&b=2",
        "https://nces.example.gov/?id=150400",
        "https://sports.wabash.edu/news/2021/3/21/a-story.aspx",                   # a school site, not a box score
        "https://www.example.edu/people/one",
    ]
    assert links["https://sports.wabash.edu/news/2021/3/21/a-story.aspx"] == [
        "/", "data/overrides.json: sports.swimming.session_of[0].sources[0]"]
    assert {g: len(u) for g, u in skipped.items()} == {
        "box-scores": 3, "scraped-pages": 3, "repository": 3, "own-site": 1, "mailto": 1}
    assert skipped["scraped-pages"] == {
        "https://depauwtigers.com/sports/football/opponent-history/wabash/215",
        "https://sports.wabash.edu/sports/football/opponent-history/depauw-university/4",
        "https://sports.wabash.edu/sports/football/schedule"}


def test_the_skip_rules_one_by_one():
    scraped = {"https://depauwtigers.com/sports/baseball/schedule"}
    group = lambda url: linkcheck.skip_group(url, scraped)
    assert group("mailto:x@example.org") == "mailto"
    assert group("https://depauwvswabash.com/alumni/") == "own-site"
    assert group("https://www.depauwvswabash.com/") == "own-site"
    assert group(render.REPO_URL) == "repository"
    assert group(render.REPO_URL + "/security/advisories/new") == "repository"
    assert group(render.REPO_URL + "-other") is None                                   # a different repository
    assert group("https://www.depauwtigers.com/boxscore.aspx?id=9") == "box-scores"
    assert group("https://sports.wabash.edu/sports/baseball/stats/2025/depauw/boxscore/77") == "box-scores"
    assert group("https://example.org/boxscore.aspx?id=9") is None                     # only the schools' sites
    assert group("https://depauwtigers.com/sports/baseball/schedule") == "scraped-pages"
    assert group("https://depauwtigers.com/sports/baseball/roster") is None


def test_the_real_sources_give_every_page_the_scraper_fetches():
    sources = json.loads(linkcheck.SOURCES.read_text(encoding="utf-8"))
    pages = linkcheck.scraped_pages(sources)
    assert len(pages) == 4 * len(sources["sports"])
    assert "https://sports.wabash.edu/sports/tennis/opponent-history/depauw-university/4" in pages


def test_no_pages_is_an_error_not_an_empty_report(tmp_path):
    with pytest.raises(FileNotFoundError):
        linkcheck.collect(tmp_path, FIX / "data", FIX / "sources.json")


# ---- checking ---------------------------------------------------------------------------------- #

class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        assert seconds >= 0
        self.t += seconds


class FakeWeb:
    """Answers from a script: {(method, url): [answer, answer, ...]}; an answer is a status, (status, location),
    or a LinkError kind string. The last answer repeats. Every request takes half a second of fake time."""
    def __init__(self, script, clock):
        self.script, self.clock, self.calls = script, clock, []

    def __call__(self, method, url, timeout):
        assert timeout == linkcheck.TIMEOUT
        self.calls.append((method, url, self.clock()))
        answers = self.script[(method, url)]
        answer = answers.pop(0) if len(answers) > 1 else answers[0]
        self.clock.t += 0.5
        if isinstance(answer, str):
            raise linkcheck.LinkError(answer, f"fake {answer}")
        if isinstance(answer, tuple):
            return linkcheck.Response(answer[0], {"Location": answer[1]})
        return linkcheck.Response(answer)


def check(script, url="https://a.example/x"):
    clock = FakeClock()
    web = FakeWeb(script, clock)
    pacer = linkcheck.Pacer(clock=clock, sleep=clock.sleep)
    return linkcheck.check_link(url, web, pacer, sleep=clock.sleep), web


U = "https://a.example/x"


@pytest.mark.parametrize("script, status, detail, methods", [
    ({("HEAD", U): [200]}, "ok", "200", ["HEAD"]),
    ({("HEAD", U): [404], ("GET", U): [404]}, "broken", "404", ["HEAD", "GET"]),
    ({("HEAD", U): [410], ("GET", U): [410]}, "broken", "410", ["HEAD", "GET"]),
    ({("HEAD", U): [405], ("GET", U): [200]}, "ok", "200", ["HEAD", "GET"]),
    ({("HEAD", U): [501], ("GET", U): [200]}, "ok", "200", ["HEAD", "GET"]),
    ({("HEAD", U): [404], ("GET", U): [200]}, "ok", "200", ["HEAD", "GET"]),           # HEAD mishandled: GET decides
    ({("HEAD", U): ["timeout"], ("GET", U): ["timeout", 200]}, "ok", "200", ["HEAD", "GET", "GET"]),
    ({("HEAD", U): ["timeout"], ("GET", U): ["timeout", "timeout"]}, "broken", "timed out (also on the retry)",
     ["HEAD", "GET", "GET"]),
    ({("HEAD", U): [503], ("GET", U): [503, 503]}, "broken", "503 (also on the retry)", ["HEAD", "GET", "GET"]),
    ({("HEAD", U): [503], ("GET", U): [503, 200]}, "ok", "200", ["HEAD", "GET", "GET"]),
    ({("HEAD", U): [403], ("GET", U): [403]}, "blocked", "403 (the site blocks automated checks)", ["HEAD", "GET"]),
    ({("HEAD", U): [429], ("GET", U): [429]}, "blocked", "429 (the site blocks automated checks)", ["HEAD", "GET"]),
    ({("HEAD", U): [401], ("GET", U): [401]}, "blocked", "401 (the site blocks automated checks)", ["HEAD", "GET"]),
    ({("HEAD", U): ["dns"], ("GET", U): ["dns"]}, "broken", "DNS lookup failed (also on the retry)",
     ["HEAD", "GET", "GET"]),
    ({("HEAD", U): ["connection"], ("GET", U): ["connection"]}, "broken", "could not connect (also on the retry)",
     ["HEAD", "GET", "GET"]),
    ({("HEAD", U): [418], ("GET", U): [418]}, "other", "418", ["HEAD", "GET"]),
])
def test_classification(script, status, detail, methods):
    result, web = check(script)
    assert (result["status"], result["detail"]) == (status, detail)
    assert [m for m, _, _ in web.calls] == methods
    assert web.calls[0][0] == "HEAD"                                                      # HEAD always first


def test_a_permanent_redirect_is_moved_and_gives_the_new_address():
    new = "https://b.example/new"
    result, web = check({("HEAD", U): [(301, new)], ("HEAD", new): [200]})
    assert result["status"] == "moved" and result["final_url"] == new and result["detail"] == "301 to a new address"
    result, _ = check({("HEAD", U): [(308, "/moved")], ("HEAD", "https://a.example/moved"): [200]})
    assert result["status"] == "moved" and result["final_url"] == "https://a.example/moved"   # relative Location


def test_a_temporary_redirect_is_ok_and_a_redirect_to_a_dead_page_is_broken():
    tmp = "https://a.example/today"
    result, _ = check({("HEAD", U): [(302, tmp)], ("HEAD", tmp): [200]})
    assert result["status"] == "ok" and result["final_url"] == tmp
    gone = "https://a.example/gone"
    result, _ = check({("HEAD", U): [(301, gone)], ("HEAD", gone): [404], ("GET", U): [(301, gone)],
                       ("GET", gone): [404]})
    assert result["status"] == "broken" and result["final_url"] == gone


def test_redirects_stop_after_the_limit():
    script = {(m, f"https://a.example/{i}"): [(301, f"https://a.example/{i + 1}")] for m in ("HEAD", "GET")
              for i in range(10)}
    result, web = check(script, "https://a.example/0")
    assert result["status"] == "other" and "more than 5 redirects" in result["detail"]
    heads = [c for c in web.calls if c[0] == "HEAD"]
    assert len(heads) == linkcheck.MAX_REDIRECTS + 1


def test_requests_to_one_host_are_spaced_and_one_link_never_costs_more_than_three_tries():
    clock = FakeClock()
    urls = [f"https://same.example/{i}" for i in range(3)] + ["https://other.example/a"]
    script = {("HEAD", u): ["timeout"] for u in urls}
    script.update({("GET", u): ["timeout"] for u in urls})
    web = FakeWeb(script, clock)
    pacer = linkcheck.Pacer(clock=clock, sleep=clock.sleep)
    results = linkcheck.run_checks({u: ["/"] for u in urls}, web, pacer, sleep=clock.sleep, log=lambda *_: None)
    assert [r["status"] for r in results] == ["broken"] * 4
    assert len(web.calls) == 3 * len(urls)
    ends = {}
    for method, url, start in web.calls:
        host = url.split("/")[2]
        if host in ends:
            assert start - ends[host] >= linkcheck.HOST_GAP - 1e-9, (url, start, ends[host])
        ends[host] = start + 0.5                                                           # each fake request takes 0.5 s


def test_the_order_alternates_between_hosts():
    order = linkcheck.polite_order(["https://a.example/1", "https://a.example/2", "https://b.example/1"])
    assert order == ["https://a.example/1", "https://b.example/1", "https://a.example/2"]


def test_the_module_needs_no_package_to_import():
    """The issue job runs it with the runner's own python3, so requests may be imported only inside a function."""
    tree = ast.parse(Path(linkcheck.__file__).read_text(encoding="utf-8"))
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    names = {a.name.split(".")[0] for n in top if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module.split(".")[0] for n in top if isinstance(n, ast.ImportFrom)}
    assert "requests" not in names and names <= set(__import__("sys").stdlib_module_names) | {"__future__", "timeutil"}
    timeutil = ast.parse((Path(linkcheck.__file__).parent / "timeutil.py").read_text(encoding="utf-8"))
    assert {a.name for n in timeutil.body if isinstance(n, ast.Import) for a in n.names} <= {"datetime"}


def test_the_user_agent_names_the_site_and_the_repository():
    assert "https://depauwvswabash.com/" in linkcheck.USER_AGENT and render.REPO_URL in linkcheck.USER_AGENT
    assert "@" not in linkcheck.USER_AGENT


# ---- the report and the issue ------------------------------------------------------------------ #

def sample_report(**counts):
    statuses = {"broken": 1, "moved": 1, "other": 1, "blocked": 1, "ok": 2, **counts}
    results = []
    for status, n in statuses.items():
        for i in range(n):
            results.append({"url": f"https://{status}.example/{i}", "status": status, "code": 200,
                            "detail": f"{status} detail", "used_on": ["/", "data/alumni.json: x: source"],
                            "final_url": "https://new.example/" if status == "moved" else None})
    skipped = {"box-scores": {"a", "b"}, "scraped-pages": {"c"}, "repository": set(), "own-site": {"d"},
               "mailto": {"e"}}
    return linkcheck.build_report(results, skipped, WHEN)


def test_the_report_groups_broken_first_and_counts_everything():
    report = sample_report()
    assert report["totals"] == {"checked": 6, "broken": 1, "moved": 1, "other": 1, "blocked": 1, "ok": 2}
    assert report["skipped"] == {"box-scores": 2, "scraped-pages": 1, "repository": 0, "own-site": 1, "mailto": 1}
    assert [l["status"] for l in report["links"]] == ["broken", "moved", "other", "blocked", "ok", "ok"]
    md = linkcheck.render_markdown(report)
    assert md.startswith("# Link check, Nov 2, 2026\n")
    assert "Checked 6 links: 1 broken, 1 moved, 1 other, 1 couldn’t check, 2 OK." in md
    assert "2 box scores on the schools’ athletics sites" in md and "Skipped on purpose (5 distinct URLs)" in md
    assert "; 1 email address (mailto:)." in md and "0 links to this repository" in md

    heads = re.findall(r"^## (.+)$", md, re.M)
    assert heads == ["Broken (1)", "Moved permanently (1)", "Other (1)", "Couldn’t check (1)", "OK (2)"]
    assert "now at <https://new.example/>" in md and "  - Used on: /, data/alumni.json: x: source" in md
    summary = linkcheck.render_markdown(report, include_ok=False)
    assert "## OK" not in summary and "## Couldn’t check (1)" in summary


def test_dates_are_eastern_like_the_rest_of_the_site():
    assert linkcheck.fmt_date("2026-11-02T03:30:00+00:00") == "Nov 1, 2026"                # 10:30 PM EST on Nov 1
    assert linkcheck.fmt_date("2026-11-02T19:41:00+00:00") == "Nov 2, 2026"


def test_the_issue_body_lists_only_broken_moved_and_other():
    body = linkcheck.render_issue_body(sample_report(), "https://run.example/1")
    assert re.findall(r"^## (.+)$", body, re.M) == ["Broken (1)", "Moved permanently (1)", "Other (1)"]
    assert "blocked.example" not in body and "1 link couldn’t be checked" in body
    assert "Latest run: https://run.example/1" in body and "2026" not in body          # the date is in the comment


def test_a_long_use_list_and_a_huge_body_are_capped():
    report = sample_report(broken=3000)
    report["links"][0]["used_on"] = [f"/p{i}/" for i in range(25)]
    body = linkcheck.render_issue_body(report)
    assert len(body) <= linkcheck.ISSUE_LIMIT and "more link" in body
    lines = body.splitlines()
    for i, line in enumerate(lines):                                    # every link keeps its "Used on" line
        if line.startswith("- <"):
            assert lines[i + 1].startswith("  - Used on: "), line
    shown = sum(line.startswith("- <") for line in lines)
    assert f"… and {3002 - shown} more links" in body                    # 3,000 broken + 1 moved + 1 other
    assert ", and 15 more" in linkcheck.render_markdown(report)


def test_truncation_never_leaves_a_heading_without_links():
    report = sample_report(broken=1200, moved=300, other=0)
    body = linkcheck.render_issue_body(report)
    lines = body.splitlines()
    assert len(body) <= linkcheck.ISSUE_LIMIT and "## Moved" not in body           # the last section dropped whole
    for i, line in enumerate(lines):
        if line.startswith("## "):
            assert lines[i + 2].startswith("- <"), line
        if line.startswith("- <"):
            assert lines[i + 1].startswith("  - Used on: "), line


class FakeGh:
    def __init__(self, open_issues=(), labels=()):
        self.open, self.labels, self.calls = list(open_issues), list(labels), []

    def __call__(self, *args, stdin=None):
        self.calls.append((args, stdin))
        if args[:2] == ("issue", "list"):
            return json.dumps([{"number": n} for n in self.open])
        if args[:2] == ("label", "list"):
            return json.dumps([{"name": n} for n in self.labels])
        if args[:2] == ("issue", "create"):
            return "https://github.com/x/y/issues/9\n"
        return ""

    def verbs(self):
        return [a[:2] for a, _ in self.calls]


def test_a_broken_link_opens_the_issue_with_the_label_created_if_missing():
    gh = FakeGh()
    assert linkcheck.sync_issue(sample_report(), "https://run", run=gh) == "opened https://github.com/x/y/issues/9"
    assert gh.verbs() == [("issue", "list"), ("label", "list"), ("label", "create"), ("issue", "create"),
                          ("issue", "comment")]
    create = gh.calls[3][0]
    assert create[create.index("--label") + 1] == "broken-links"
    assert gh.calls[4][1].startswith("Link check of Nov 2, 2026: Checked 6 links: 1 broken")


def test_an_open_issue_is_updated_and_commented_never_duplicated():
    gh = FakeGh(open_issues=[12, 7], labels=["broken-links"])
    assert linkcheck.sync_issue(sample_report(), run=gh) == "updated #7"
    assert gh.verbs() == [("issue", "list"), ("issue", "edit"), ("issue", "comment")]
    assert gh.calls[1][0][2] == "7" and "## Broken (1)" in gh.calls[1][1]


def test_nothing_wrong_closes_the_issue_with_a_comment():
    gh = FakeGh(open_issues=[7])
    clean = sample_report(broken=0, moved=0, other=0)
    assert linkcheck.sync_issue(clean, run=gh) == "closed #7"
    assert gh.verbs() == [("issue", "list"), ("issue", "comment"), ("issue", "close")]
    assert "nothing broken, moved or other" in gh.calls[1][1]


def test_couldnt_check_alone_opens_no_issue():
    gh = FakeGh()
    only_blocked = sample_report(broken=0, moved=0, other=0, blocked=4)
    assert not linkcheck.needs_issue(only_blocked)
    assert linkcheck.sync_issue(only_blocked, run=gh) == "nothing broken, moved or other, and no open issue"
    assert gh.verbs() == [("issue", "list")]


def test_the_check_command_writes_the_report_and_exits_zero_even_with_broken_links(tmp_path, monkeypatch):
    def web(method, url, timeout):
        return linkcheck.Response(404 if "wikipedia" in url else 200)
    monkeypatch.setattr(linkcheck, "requests_transport", lambda: web)
    monkeypatch.setattr(linkcheck.time, "sleep", lambda s: None)
    monkeypatch.setattr(linkcheck.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(linkcheck, "now_utc", lambda: WHEN)
    out = tmp_path / "out"
    code = linkcheck.main(["check", "--site", str(FIX / "rendered"), "--data", str(FIX / "data"), "--out", str(out)])
    assert code == 0
    report = json.loads((out / "link-report.json").read_text(encoding="utf-8"))
    assert report["totals"]["broken"] == 1 and report["links"][0]["url"] == "https://en.wikipedia.org/wiki/Monon_Bell"
    assert report["checked_at"] == "2026-11-02T19:41:00+00:00"
    assert (out / "link-report.md").read_text(encoding="utf-8").startswith("# Link check, Nov 2, 2026")
    assert (out / "summary.md").is_file()

# ---- the workflow ------------------------------------------------------------------------------ #

def workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_the_workflow_runs_monthly_and_by_hand_only():
    wf = workflow()
    triggers = wf[True]                                                                   # YAML reads `on` as True
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert "pull_request" not in WORKFLOW.read_text(encoding="utf-8")
    [entry] = triggers["schedule"]
    minute, hour, day, month, weekday = entry["cron"].split()
    assert minute.isdigit() and minute != "0"                                             # not on the hour
    assert 1 <= int(day) <= 28 and month == "*" and weekday == "*"                        # once a month; never both day fields
    # Outside 6 to 9 AM Pacific in both PST (14:00-17:00 UTC) and PDT (13:00-16:00 UTC).
    assert not 13 <= int(hour) <= 17


def test_the_workflow_permissions_are_minimal_and_never_overlap():
    wf = workflow()
    assert wf["permissions"] == {"contents": "read"}
    assert wf["concurrency"] == {"group": "link-check", "cancel-in-progress": False}
    assert set(wf["jobs"]) == {"check", "issues"}
    assert wf["jobs"]["check"]["permissions"] == {"contents": "read"}
    assert wf["jobs"]["issues"]["permissions"] == {"contents": "read", "issues": "write"}
    assert "secrets." not in WORKFLOW.read_text(encoding="utf-8")
    assert "refs/heads/main" in wf["jobs"]["issues"]["if"]                                 # a branch run opens no issue
    for job in wf["jobs"].values():
        assert job["runs-on"] == "ubuntu-24.04"
        assert job["steps"][0]["with"]["persist-credentials"] is False


def test_the_check_job_fetches_installs_renders_checks_and_uploads_in_that_order():
    steps = workflow()["jobs"]["check"]["steps"]
    runs = [s.get("run", "") + s.get("uses", "") for s in steps]

    def at(text):
        return next(i for i, r in enumerate(runs) if text in r)
    assert (at("databranch.py fetch --remote origin") < at("pip install --require-hashes -r requirements.txt")
            < at("scraper/render.py") < at("scraper/linkcheck.py check") < at("actions/upload-artifact")
            < at("GITHUB_STEP_SUMMARY"))
    assert "linkcheck.py issue" in "".join(s.get("run", "") for s in workflow()["jobs"]["issues"]["steps"])


def test_every_action_in_the_workflow_is_pinned_to_a_full_sha():
    uses = re.findall(r"uses: (\S+)(.*)", WORKFLOW.read_text(encoding="utf-8"))
    assert uses
    for action, comment in uses:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", action) and re.search(r"# v\d", comment), action


# ---- links within the site --------------------------------------------------------------------- #

def test_internal_links_that_lead_nowhere_are_found(tmp_path):
    (tmp_path / "alumni").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "series.json").write_text("{}", encoding="utf-8")
    (tmp_path / "alumni" / "index.html").write_text('<h2 id="nobel">Nobel</h2><a href="../#top">Home</a>',
                                                     encoding="utf-8")
    (tmp_path / "index.html").write_text(
        '<main id="top"><a href="#top">ok</a><a href="alumni/">ok</a><a href="alumni/#nobel">ok</a>'
        '<a href="/alumni">ok, no slash</a><a href="data/series.json">ok</a>'
        '<a href="https://depauwvswabash.com/alumni/#nobel">ok, absolute</a>'
        '<a href="https://example.org/missing/">outbound, not ours</a><a href="mailto:x@example.org">mail</a>'
        '<a href="#gone">bad fragment</a><a href="alumni/#gone">bad fragment elsewhere</a>'
        '<a href="games.csv">missing file</a><a href="/how-it-works/">missing page</a></main>', encoding="utf-8")
    assert linkcheck.internal_problems(tmp_path) == [
        ("/", "#gone", 'no id="gone" on /'),
        ("/", "alumni/#gone", 'no id="gone" on /alumni/'),
        ("/", "games.csv", "no built file for /games.csv"),
        ("/", "/how-it-works/", "no built file for /how-it-works/"),
    ]


FROZEN = Path(__file__).resolve().parent / "fixtures" / "series-frozen-2026-10-30.json"


@pytest.mark.parametrize("now, upcoming", [
    (dt.datetime(2026, 10, 30, 16, 0, tzinfo=dt.timezone.utc), {}),                       # an ordinary day
    (dt.datetime(2026, 11, 14, 18, 30, tzinfo=dt.timezone.utc),                            # the Bell game, underway
     {"football": {"schedule_url": "https://depauwtigers.com/sports/football/schedule",
                   "games": [{"date": "2026-11-14", "time": "1:07 PM", "time_note": None, "location": "away",
                              "site": "Hollett Little Giant Stadium, Crawfordsville, Ind.", "status": "scheduled",
                              "doubleheader": False}]}}),
], ids=["ordinary-day", "game-day"])
def test_every_link_within_the_built_site_leads_somewhere(tmp_path, now, upcoming):
    """Every page, every link between pages, every #fragment and every download link, on the whole built site."""
    up = tmp_path / "upcoming.json"
    up.write_text(json.dumps({"schema_version": 1, "sports": upcoming}), encoding="utf-8")
    out = tmp_path / "site"
    render.build(FROZEN, out, now=now, upcoming_path=up)
    if upcoming:
        assert 'id="game-day"' in (out / "index.html").read_text(encoding="utf-8")         # the banner is there
    assert linkcheck.internal_problems(out) == []
