"""The monthly link check (scraper/linkcheck.py, .github/workflows/link-check.yml).

No network and no outside programs: links come from the small fixture site in tests/fixtures/linkcheck, every
request goes to a fake transport, time is a fake clock, and the issue sync gets a fake gh runner. The links within
the site are checked on two full builds from the frozen fixture, with a fixed clock."""

import ast
import datetime as dt
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest
import yaml

import linkcheck
import render
import scrape

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
        "box-scores": 3, "scraped-pages": 3, "repository": 3, "own-site": 1, "mailto": 1,
        "robots": 0}                                                               # filled during the run, not here
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


VIDEO = "https://www.youtube.com/watch?v=xW74vxcnskw"
OEMBED = "https://www.youtube.com/oembed?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3DxW74vxcnskw&format=json"


@pytest.mark.parametrize("url, oembed", [
    (VIDEO, OEMBED),
    ("https://youtube.com/watch?v=xW74vxcnskw", OEMBED),                               # any of YouTube's own hosts,
    ("https://m.youtube.com/watch?v=xW74vxcnskw&t=42s", OEMBED),                        # other query keys ignored
    ("https://www.youtube.com/channel/UCxyz", None),                                     # not a video: checked as a page
    ("https://www.youtube.com/watch?v=short", None),                                     # not an 11-character id
    ("https://www.youtube.com/watch?v=xW74vxcnskw&v=aaaaaaaaaaa", None),                 # two ids: not a plain link
    ("https://www.youtube.com.example/watch?v=xW74vxcnskw", None),                       # not YouTube
    ("https://a.example/watch?v=xW74vxcnskw", None),
])
def test_a_youtube_watch_link_is_checked_through_its_oembed_address(url, oembed):
    assert linkcheck.youtube_oembed(url) == oembed


def test_the_bell_songs_video_is_a_link_the_oembed_check_covers():
    import belltimeline
    assert linkcheck.youtube_oembed(belltimeline.SONG_VIDEO["url"]) == OEMBED


@pytest.mark.parametrize("answer, status, detail", [
    (200, "ok", "200 from YouTube’s oEmbed address: the video is there"),
    (404, "broken", "404 from YouTube’s oEmbed address: the video is gone"),
    (401, "blocked", "401 from YouTube’s oEmbed address: the video is private or may not be embedded, so it was not "
                     "checked"),
    (403, "blocked", "403 from YouTube’s oEmbed address: the video is private or may not be embedded, so it was not "
                     "checked"),
    (429, "blocked", "429 from YouTube’s oEmbed address"),
    (500, "blocked", "500 from YouTube’s oEmbed address"),
    (503, "blocked", "503 from YouTube’s oEmbed address"),
    ("timeout", "blocked", "timed out at YouTube’s oEmbed address"),
    ("connection", "blocked", "could not connect at YouTube’s oEmbed address"),
    ("dns", "blocked", "DNS lookup failed at YouTube’s oEmbed address"),
    ("tls", "blocked", "TLS (certificate) error at YouTube’s oEmbed address"),
    (400, "broken", "400 from YouTube’s oEmbed address: YouTube does not know the video"),
    (418, "other", "418 from YouTube’s oEmbed address"),
])
def test_a_youtube_video_costs_one_get_of_its_oembed_address_and_only_a_400_or_404_is_broken(answer, status,
                                                                                               detail):
    result, web = check({("GET", OEMBED): [answer]}, url=VIDEO)
    assert (result["status"], result["detail"]) == (status, detail)
    assert [(m, u) for m, u, _ in web.calls] == [("GET", OEMBED)]          # one request: no HEAD, no retry
    assert result["url"] == VIDEO and result["final_url"] is None          # reported as the link on the page
    assert (result["status"] == "broken") == (answer in (400, 404))


def test_a_400_counts_as_broken_only_at_youtubes_oembed_address():
    result, _ = check({("HEAD", U): [400], ("GET", U): [400]})
    assert (result["status"], result["detail"]) == ("other", "400")


def test_youtube_links_keep_the_two_second_spacing_and_land_in_the_report_under_the_watch_link():
    other = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
    other_oembed = linkcheck.youtube_oembed(other)
    clock = FakeClock()
    web = FakeWeb({("GET", OEMBED): [200], ("GET", other_oembed): [404]}, clock)
    pacer = linkcheck.Pacer(clock=clock, sleep=clock.sleep)
    results = linkcheck.run_checks({VIDEO: ["monon-bell/"], other: ["index.html"]}, web, pacer,
                                   sleep=clock.sleep, log=lambda *_: None)
    assert [(r["url"], r["status"], r["used_on"]) for r in results] == [
        (other, "broken", ["index.html"]), (VIDEO, "ok", ["monon-bell/"])]
    (_, _, first), (_, _, second) = web.calls
    assert second - (first + 0.5) >= linkcheck.HOST_GAP                     # same host: the usual gap


def test_a_permanent_redirect_is_moved_and_gives_the_new_address():
    new = "https://b.example/archive/x"                                                  # an equally specific page
    result, web = check({("HEAD", U): [(301, new)], ("HEAD", new): [200]})
    assert result["status"] == "moved" and result["final_url"] == new and result["detail"] == "301 to a new address"
    result, _ = check({("HEAD", U): [(308, "/moved")], ("HEAD", "https://a.example/moved"): [200]})
    assert result["status"] == "moved" and result["final_url"] == "https://a.example/moved"   # relative Location


@pytest.mark.parametrize("old, new, status", [
    # The Cummins pattern: a subdomain's page now lands on the main site's /company (behind a language prefix).
    ("https://www.social.cummins.com/timeline", "https://www.cummins.com/en-na/company", "broken"),
    # The Wabash news pattern: the same story under a new address scheme.
    ("https://www.wabash.edu/news/displaystory.cfm?news_ID=2768", "https://www.wabash.edu/news/story/2768", "moved"),
    ("https://a.example/people/one", "https://a.example/", "broken"),                     # to the homepage
    ("https://a.example/news/2017/story", "https://a.example/news", "broken"),             # to a section page
    ("https://a.example/page?id=7", "https://a.example/about", "broken"),                 # the identifying query gone
    ("https://a.example/a/story", "https://a.example/a/story/", "moved"),                  # only a trailing slash
    ("https://a.example/about", "https://www.a.example/about/", "moved"),                 # same page, www and slash
    ("https://a.example/en/", "https://a.example/en-us/", "moved"),                       # a homepage stays a homepage
    ("https://sports.wabash.edu/mononbell", "https://sports.wabash.edu/sports/2011/7/25/FB_0725114739.aspx", "moved"),
    ("https://a.example/x/y/z", "https://a.example/2017-annual-report", "moved"),          # one segment, but specific
    ("https://old.example/about", "https://new.example/about", "moved"),                  # same page, new domain
    ("https://a.example/about?ref=x", "https://a.example/about", "moved"),                # a tracking query dropped
])
def test_a_permanent_redirect_to_a_general_page_is_probably_broken(old, new, status):
    result, _ = check({("HEAD", old): [(301, new)], ("HEAD", new): [200]}, old)
    assert result["status"] == status and result["final_url"] == new
    if status == "broken":
        assert result["detail"] == "probably broken (redirects to a general page)"
    else:
        assert result["detail"] == "301 to a new address"


def test_a_probably_broken_link_counts_as_broken_and_opens_the_issue():
    result, _ = check({("HEAD", U): [(308, "https://a.example/")], ("HEAD", "https://a.example/"): [200]})
    result["used_on"] = ["/alumni/"]
    report = linkcheck.build_report([result], {g: set() for g in linkcheck.SKIP_GROUPS}, WHEN)
    assert report["totals"]["broken"] == 1 and report["totals"]["moved"] == 0
    assert linkcheck.needs_issue(report)
    body = linkcheck.render_issue_body(report)
    assert "## Broken (1)" in body and "probably broken (redirects to a general page); ended at <https://a.example/>" in body


def test_a_temporary_redirect_to_a_general_page_is_still_ok():
    result, _ = check({("HEAD", U): [(302, "https://a.example/")], ("HEAD", "https://a.example/"): [200]})
    assert result["status"] == "ok"


UNT = "https://digital.library.unt.edu/ark:/67531/metadc980580/m1/24/"
UNT_WALL = "https://digital.library.unt.edu/dam/?next=%2Fark%3A%2F67531%2Fmetadc980580%2Fm1%2F24%2F"


def test_a_redirect_to_a_bot_check_is_couldnt_check_not_ok():
    """The UNT Digital Library pattern (seen 2026-11-02): a 302 to /dam/?next=<the page>, which answers 200."""
    result, _ = check({("HEAD", UNT): [(302, "/dam/?next=%2Fark%3A%2F67531%2Fmetadc980580%2Fm1%2F24%2F")],
                       ("HEAD", UNT_WALL): [200]}, UNT)
    assert result["status"] == "blocked" and result["final_url"] == UNT_WALL
    assert result["detail"] == "redirects to a bot check or sign-in page (the site blocks automated checks)"


@pytest.mark.parametrize("location, status", [
    ("https://a.example/login?return_to=https%3A%2F%2Fa.example%2Fx", "blocked"),        # a full URL back
    ("https://a.example/check?Next=/x", "blocked"),                                       # keys in any case
    ("https://a.example/login?next=/elsewhere", "ok"),                                    # not back to the page
    ("https://a.example/search?q=/x", "ok"),                                              # not a return key
    ("https://a.example/login?next=", "ok"),                                              # empty
])
def test_only_a_query_that_returns_to_the_cited_page_is_a_bot_check(location, status):
    result, _ = check({("HEAD", U): [(302, location)], ("HEAD", location): [200]})
    assert result["status"] == status


def test_a_permanent_redirect_to_a_bot_check_is_couldnt_check_not_moved_or_broken():
    wall = "https://a.example/?redirect=%2Fx"
    result, _ = check({("HEAD", U): [(301, wall)], ("HEAD", wall): [200]})
    assert result["status"] == "blocked"


def test_a_timeout_on_a_bot_check_host_is_couldnt_check():
    """GitHub Actions, 2026-11-02: the UNT page timed out on the HEAD and both GETs."""
    script = {("HEAD", UNT): ["timeout"], ("GET", UNT): ["timeout", "timeout"]}
    result, web = check(script, UNT)
    assert (result["status"], result["detail"]) == (
        "blocked", "timed out (also on the retry); the site puts its pages behind a bot check")
    assert [m for m, _, _ in web.calls] == ["HEAD", "GET", "GET"]                         # same politeness
    result, _ = check({("HEAD", UNT): ["connection"], ("GET", UNT): ["connection"]}, UNT)
    assert result["status"] == "blocked"
    result, _ = check({("HEAD", UNT): ["dns"], ("GET", UNT): ["dns"]}, UNT)
    assert result["status"] == "broken"                                                   # a DNS failure is real
    result, _ = check({("HEAD", UNT): [404], ("GET", UNT): [404]}, UNT)
    assert result["status"] == "broken"                                                   # so is a 404
    result, _ = check({("HEAD", U): ["timeout"], ("GET", U): ["timeout"]})
    assert result["status"] == "broken"                                                   # any other host


def test_every_bot_check_host_is_still_used_by_a_curated_link():
    """The list is only for hosts the site links to; a host with no links left should be taken off."""
    hosts = set()
    for name in linkcheck.DATA_FILES:
        file = linkcheck.DATA / name
        if file.is_file():
            hosts |= {urlparse(url).hostname for url, _ in linkcheck.data_urls(json.loads(file.read_text(encoding="utf-8")))}
    assert set(linkcheck.BOT_CHECK_HOSTS) <= hosts
    for host, note in linkcheck.BOT_CHECK_HOSTS.items():
        assert host == host.lower() and re.match(r"\d{4}-\d{2}-\d{2}: ", note), host       # says what was seen, when


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
    script = {("HEAD", u): [503] for u in urls}               # an answer each time, so the host is never given up on
    script.update({("GET", u): [503] for u in urls})
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


# ---- the schools' sites: robots.txt and its crawl delay (the scraper's rules, politeness.py) ---- #

D, W = "https://depauwtigers.com", "https://sports.wabash.edu"
SCHOOL_ROBOTS = "User-agent: *\nDisallow: /admin/\nDisallow: /services/\nAllow: /\nCrawl-delay: 30\n"


class FakeRobots:
    """Serves a robots.txt per host (SCHOOL_ROBOTS unless told otherwise; an exception is raised) and records
    every read with its fake time. A read takes half a second, like a FakeWeb request."""
    def __init__(self, clock, by_host=None):
        self.clock, self.by_host, self.calls = clock, by_host or {}, []

    def __call__(self, url):
        assert url.endswith("/robots.txt"), url                                            # nothing else is read this way
        self.calls.append((url, self.clock()))
        self.clock.t += 0.5
        body = self.by_host.get(urlparse(url).hostname, SCHOOL_ROBOTS)
        if isinstance(body, Exception):
            raise body
        return body


def run_with_robots(urls, script=None, by_host=None):
    """run_checks over `urls` with the school rules on. (results, skipped, web, robots, clock)"""
    clock = FakeClock()
    script = script if script is not None else {("HEAD", u): [200] for u in urls}
    web, robots = FakeWeb(script, clock), FakeRobots(clock, by_host)
    pacer = linkcheck.Pacer(clock=clock, sleep=clock.sleep, robots_fetch=robots, log=lambda m: None)
    skipped = {g: set() for g in linkcheck.SKIP_GROUPS}
    results = linkcheck.run_checks({u: ["/"] for u in urls}, web, pacer, sleep=clock.sleep, log=lambda *_: None,
                                   skipped=skipped)
    return results, skipped, web, robots, clock


def request_gaps(web, robots, host):
    """Seconds from the end of each request to `host` (robots.txt included) to the start of the next one."""
    starts = sorted([t for _, url, t in web.calls if urlparse(url).hostname == host]
                    + [t for url, t in robots.calls if urlparse(url).hostname == host])
    return [b - (a + 0.5) for a, b in zip(starts, starts[1:])]


def test_links_on_the_schools_sites_wait_the_robots_txt_crawl_delay_and_every_other_host_two_seconds():
    urls = ([f"{D}/sports/2022/6/28/story-{i}.aspx" for i in range(3)]
            + [f"{W}/news/2021/3/2{i}/story.aspx" for i in range(3)]
            + [f"https://other.example/{i}" for i in range(3)])
    results, skipped, web, robots, _ = run_with_robots(urls)
    assert [r["status"] for r in results] == ["ok"] * 9 and not skipped["robots"]
    for host in ("depauwtigers.com", "sports.wabash.edu"):
        gaps = request_gaps(web, robots, host)
        assert len(gaps) == 3 and all(g >= 30 - 1e-9 for g in gaps), (host, gaps)   # robots.txt, then three links
    other = request_gaps(web, robots, "other.example")
    assert len(other) == 2 and all(g >= linkcheck.HOST_GAP - 1e-9 for g in other), other
    # by themselves (no school link in between to wait for), other hosts are exactly 2 seconds apart, as before
    alone = [f"https://other.example/{i}" for i in range(3)] + [f"https://www.wabash.edu/news/{i}" for i in range(3)]
    results, _, web, robots, _ = run_with_robots(alone)
    assert [r["status"] for r in results] == ["ok"] * 6 and not robots.calls
    for host in ("other.example", "www.wabash.edu"):
        assert request_gaps(web, robots, host) == pytest.approx([linkcheck.HOST_GAP] * 2), host
    assert linkcheck.HOST_GAP == 2.0


def test_robots_txt_is_read_once_per_school_host_before_any_page_there_and_for_no_other_host():
    urls = [f"{D}/a", f"{D}/b", f"{W}/a", f"{W}/b", "https://www.depauwtigers.com/c", "https://other.example/x",
            "https://www.wabash.edu/news/story/1"]                                        # the college, not the athletics site
    _, _, web, robots, _ = run_with_robots(urls)
    assert sorted(u for u, _ in robots.calls) == [
        f"{D}/robots.txt", f"{W}/robots.txt", "https://www.depauwtigers.com/robots.txt"]
    for robots_url, read_at in robots.calls:
        host = urlparse(robots_url).hostname
        assert read_at < min(t for _, url, t in web.calls if urlparse(url).hostname == host)
    assert set(linkcheck.SOURCE_HOSTS) == {"depauwtigers.com", "www.depauwtigers.com", "sports.wabash.edu"}


def test_a_page_robots_txt_disallows_is_never_requested_and_is_counted_as_skipped():
    urls = [f"{D}/admin/page.aspx", f"{D}/sports/2022/6/28/monon-bell-history.aspx", f"{W}/services/feed.ashx",
            "https://other.example/admin/page.aspx"]
    results, skipped, web, _, _ = run_with_robots(urls)
    assert skipped["robots"] == {f"{D}/admin/page.aspx", f"{W}/services/feed.ashx"}
    assert sorted(r["url"] for r in results) == sorted([urls[1], urls[3]])            # the rule is the schools' only
    assert not [url for _, url, _ in web.calls if url in skipped["robots"]]
    report = linkcheck.build_report(results, skipped, WHEN)
    assert report["skipped"]["robots"] == 2 and report["totals"]["checked"] == 2
    assert "2 pages on the schools’ athletics sites that robots.txt disallows" in linkcheck.skipped_line(report)


@pytest.mark.parametrize("robots, delay", [
    ("User-agent: *\nAllow: /\n", 30.0),                               # no Crawl-delay: the scraper's 30-second fallback
    ("User-agent: *\nAllow: /\nCrawl-delay: 1\n", 3.0),                # never under 3 seconds
    ("User-agent: *\nAllow: /\nCrawl-delay: 45\n", 45.0),
    (OSError("timed out"), 30.0),                                       # unreadable: go ahead at the fallback
])
def test_the_school_delay_has_the_scrapers_fallback_and_minimum(robots, delay):
    urls = [f"{W}/news/a.aspx", f"{W}/news/b.aspx"]
    results, skipped, web, fake, _ = run_with_robots(urls, by_host={"sports.wabash.edu": robots})
    assert [r["status"] for r in results] == ["ok", "ok"] and not skipped["robots"]
    assert all(g >= delay - 1e-9 for g in request_gaps(web, fake, "sports.wabash.edu"))
    assert request_gaps(web, fake, "sports.wabash.edu")[-1] < delay + 1
    assert delay >= linkcheck.politeness.MIN_GAP_SECONDS and linkcheck.politeness.DEFAULT_CRAWL_DELAY == 30.0


def test_a_retry_and_a_get_on_a_school_site_keep_the_crawl_delay_too():
    url = f"{D}/sports/2022/6/28/rivalry-facts.aspx"
    script = {("HEAD", url): ["timeout"], ("GET", url): ["timeout", 200]}
    results, _, web, robots, _ = run_with_robots([url], script)
    assert results[0]["status"] == "ok" and [m for m, _, _ in web.calls] == ["HEAD", "GET", "GET"]
    gaps = request_gaps(web, robots, "depauwtigers.com")
    assert len(gaps) == 3 and all(g >= 30 - 1e-9 for g in gaps), gaps


def test_a_redirect_onto_a_disallowed_school_page_is_not_followed_and_is_couldnt_check():
    old, target = "https://www.wabash.edu/athletics/old-page", f"{W}/admin/moved.aspx"
    script = {("HEAD", old): [(301, target)], ("GET", old): [(301, target)]}
    results, skipped, web, _, _ = run_with_robots([old], script)
    assert (results[0]["status"], results[0]["detail"]) == ("blocked", linkcheck.ROBOTS_DETAIL)
    assert results[0]["final_url"] == target and not skipped["robots"]
    assert target not in [url for _, url, _ in web.calls]


# ---- the two limits: a host that gives no answer, and the run's own time limit ------------------- #

class SlowWeb(FakeWeb):
    """A FakeWeb on which a request that gets no answer takes the whole timeout, as it does live."""
    def __call__(self, method, url, timeout):
        start = self.clock()
        try:
            return super().__call__(method, url, timeout)
        except linkcheck.LinkError as exc:
            if exc.kind in linkcheck.NO_ANSWER:
                self.clock.t = start + timeout
            raise


def run_plain(urls, script, web_class=FakeWeb, deadline=None):
    """run_checks over `urls` with the school rules on and an optional time limit. (results, web, robots, clock)"""
    clock = FakeClock()
    web, robots = web_class(script, clock), FakeRobots(clock)
    pacer = linkcheck.Pacer(clock=clock, sleep=clock.sleep, robots_fetch=robots, log=lambda m: None,
                            deadline=deadline)
    results = linkcheck.run_checks({u: ["/"] for u in urls}, web, pacer, sleep=clock.sleep, log=lambda *_: None)
    return results, web, robots, clock


def test_a_host_that_gives_no_answer_is_asked_about_two_links_and_the_rest_are_couldnt_check():
    """Without the limit, 26 links on one silent host would cost 26 x 3 full timeouts, enough for the job to reach
    its own limit before any report is written. The host costs two links, and the run goes on."""
    silent = [f"https://silent.example/{i:02}" for i in range(26)]
    fine = [f"https://fine.example/{i}" for i in range(4)]
    script = {(m, u): ["timeout"] for u in silent for m in ("HEAD", "GET")}
    script.update({("HEAD", u): [200] for u in fine})
    results, web, _, clock = run_plain(silent + fine, script, SlowWeb)
    by = {r["url"]: r for r in results}
    assert linkcheck.SILENT_LINKS == 2
    asked = [url for _, url, _ in web.calls if "silent.example" in url]
    assert asked == [silent[0]] * 3 + [silent[1]] * 3                                    # HEAD, GET, the retry: twice
    for url in silent[:2]:                                                               # reported as before
        assert (by[url]["status"], by[url]["detail"]) == ("broken", "timed out (also on the retry)")
        assert "not_requested" not in by[url]
    for url in silent[2:]:
        assert by[url]["status"] == "blocked" and by[url]["not_requested"] == "silent"
        assert by[url]["detail"] == ("not requested: silent.example gave no answer to 2 links in a row, so the run "
                                     "stopped asking it")
    assert [by[u]["status"] for u in fine] == ["ok"] * 4                                 # every other host is checked
    one_link = 3 * linkcheck.TIMEOUT + linkcheck.RETRY_WAIT + 3 * linkcheck.HOST_GAP
    assert clock() - 1000.0 <= linkcheck.SILENT_LINKS * one_link + 5                     # minutes saved: 24 links' worth
    report = linkcheck.build_report(results, {g: set() for g in linkcheck.SKIP_GROUPS}, WHEN)
    assert report["not_requested"] == {"silent": 24, "deadline": 0}
    assert report["totals"]["broken"] == 2 and report["totals"]["blocked"] == 24 and linkcheck.needs_issue(report)


def test_a_failed_connection_counts_like_a_timeout_and_a_bot_check_host_goes_silent_too():
    urls = [f"https://down.example/{i}" for i in range(3)]
    results, web, _, _ = run_plain(urls, {(m, u): ["connection"] for u in urls for m in ("HEAD", "GET")})
    assert [(r["status"], r.get("not_requested")) for r in results] == [
        ("broken", None), ("broken", None), ("blocked", "silent")]
    assert len(web.calls) == 6
    unt = [f"https://digital.library.unt.edu/ark:/67531/x/{i}/" for i in range(3)]
    results, web, _, _ = run_plain(unt, {(m, u): ["timeout"] for u in unt for m in ("HEAD", "GET")})
    assert [r["status"] for r in results] == ["blocked"] * 3 and len(web.calls) == 6      # the third is not requested
    assert [r.get("not_requested") for r in results] == [None, None, "silent"]


def test_any_answer_clears_a_hosts_count_and_a_quick_failure_never_counts():
    a = [f"https://a.example/{i}" for i in range(5)]
    script = {(m, u): ["timeout"] for u in a for m in ("HEAD", "GET")}
    script[("HEAD", a[1])] = [403]                                # one answer, of any kind, between two silences
    script[("GET", a[1])] = [403]
    script[("HEAD", a[3])] = ["timeout"]                          # HEAD gets no answer, but the GET does: an answer
    script[("GET", a[3])] = [200]
    results, web, _, _ = run_plain(a, script)
    assert [r["status"] for r in results] == ["broken", "blocked", "broken", "ok", "broken"]
    assert not any("not_requested" in r for r in results) and {url for _, url, _ in web.calls} == set(a)
    for kind in ("dns", "tls"):                                   # quick to fail, and still broken: all are asked
        b = [f"https://b.example/{i}" for i in range(4)]
        results, web, _, _ = run_plain(b, {(m, u): [kind] for u in b for m in ("HEAD", "GET")})
        assert [r["status"] for r in results] == ["broken"] * 4 and len(web.calls) == 12, kind
    assert linkcheck.NO_ANSWER == {"timeout", "connection"} < linkcheck.TRANSIENT_ERRORS


def test_a_redirect_onto_a_silent_host_is_not_followed_and_counts_for_the_host_that_was_silent():
    def one_after_another(urls, script):
        clock = FakeClock()
        web = FakeWeb(script, clock)
        pacer = linkcheck.Pacer(clock=clock, sleep=clock.sleep)
        return [linkcheck.check_link(u, web, pacer, sleep=clock.sleep) for u in urls], web

    dead = [f"https://dead.example/{i}" for i in range(2)]
    old = "https://other.example/old"
    script = {(m, u): ["timeout"] for u in dead for m in ("HEAD", "GET")}
    script.update({(m, old): [(301, "https://dead.example/new")] for m in ("HEAD", "GET")})
    results, web = one_after_another(dead + [old], script)
    assert (results[2]["status"], results[2]["not_requested"]) == ("blocked", "silent")
    assert results[2]["final_url"] == "https://dead.example/new"
    assert "https://dead.example/new" not in [url for _, url, _ in web.calls]
    # the other way round: a link that redirects to a host that then gives no answer counts for that host
    hops = [f"https://from.example/{i}" for i in range(2)]
    script = {(m, u): [(302, f"https://quiet.example/{i}")] for i, u in enumerate(hops) for m in ("HEAD", "GET")}
    script.update({(m, f"https://quiet.example/{i}"): ["timeout"] for i in range(2) for m in ("HEAD", "GET")})
    script[("HEAD", "https://quiet.example/z")] = [200]
    script[("HEAD", "https://from.example/z")] = [200]
    results, web = one_after_another(hops + ["https://quiet.example/z", "https://from.example/z"], script)
    assert [r["status"] for r in results] == ["broken", "broken", "blocked", "ok"]         # from.example did answer
    assert results[2]["not_requested"] == "silent" and "quiet.example" in results[2]["detail"]
    assert "https://quiet.example/z" not in [url for _, url, _ in web.calls]


def test_the_check_stops_requesting_at_its_time_limit_and_the_rest_are_couldnt_check():
    """One host a link, so nothing waits: a request every half second, and a limit of 1.2 seconds."""
    urls = [f"https://{h}.example/x" for h in "abcde"] + [f"{W}/news/story.aspx"]          # the school host sorts last
    results, web, robots, _ = run_plain(urls, {("HEAD", u): [200] for u in urls}, deadline=1.2)
    assert [r["status"] for r in results] == ["ok"] * 3 + ["blocked"] * 3
    assert [url for _, url, _ in web.calls] == urls[:3] and not robots.calls              # not even robots.txt
    for r in results[3:]:
        assert r["not_requested"] == "deadline"
        assert r["detail"] == "not requested: the run reached its time limit (0.02 minutes)"
    report = linkcheck.build_report(results, {g: set() for g in linkcheck.SKIP_GROUPS}, WHEN)
    assert report["not_requested"] == {"silent": 0, "deadline": 3} and not linkcheck.needs_issue(report)
    # without a limit (how a single link is classified in these tests) nothing ever stops
    results, web, _, _ = run_plain(urls, {("HEAD", u): [200] for u in urls})
    assert [r["status"] for r in results] == ["ok"] * 6


def test_a_retry_the_time_limit_stops_leaves_the_first_get_standing():
    """HEAD at 0 s, GET at 2.5 s (the 2-second gap), then the 10-second wait for the retry runs past the limit."""
    clock = FakeClock()
    web = FakeWeb({("HEAD", U): ["timeout"], ("GET", U): ["timeout"]}, clock)
    pacer = linkcheck.Pacer(clock=clock, sleep=clock.sleep, deadline=5.0)
    result = linkcheck.check_link(U, web, pacer, sleep=clock.sleep)
    assert (result["status"], result["detail"]) == ("broken", "timed out") and "not_requested" not in result
    assert [m for m, _, _ in web.calls] == ["HEAD", "GET"]
    # a GET the limit stops after an answered HEAD: nothing is known about the page, so it is couldn't check
    clock = FakeClock()
    web = FakeWeb({("HEAD", U): [405], ("GET", U): [200]}, clock)
    pacer = linkcheck.Pacer(clock=clock, sleep=clock.sleep, deadline=0.4)
    result = linkcheck.check_link(U, web, pacer, sleep=clock.sleep)
    assert (result["status"], result["not_requested"]) == ("blocked", "deadline")
    assert [m for m, _, _ in web.calls] == ["HEAD"]


def test_a_youtube_video_is_not_requested_after_the_time_limit_either():
    clock = FakeClock()
    web = FakeWeb({("GET", OEMBED): [200]}, clock)
    pacer = linkcheck.Pacer(clock=clock, sleep=clock.sleep, deadline=0)
    result = linkcheck.check_link(VIDEO, web, pacer, sleep=clock.sleep)
    assert (result["url"], result["status"], result["not_requested"]) == (VIDEO, "blocked", "deadline")
    assert not web.calls


def test_the_live_check_reads_robots_txt_the_way_the_scraper_does():
    assert linkcheck.robots_reader() is scrape.fetch
    assert linkcheck.USER_AGENT == linkcheck.politeness.USER_AGENT == scrape.USER_AGENT


def test_the_order_alternates_between_hosts():
    order = linkcheck.polite_order(["https://a.example/1", "https://a.example/2", "https://b.example/1"])
    assert order == ["https://a.example/1", "https://b.example/1", "https://a.example/2"]


def test_the_module_needs_no_package_to_import():
    """The issue job runs it with the runner's own python3, so requests may be imported only inside a function."""
    tree = ast.parse(Path(linkcheck.__file__).read_text(encoding="utf-8"))
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    names = {a.name.split(".")[0] for n in top if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module.split(".")[0] for n in top if isinstance(n, ast.ImportFrom)}
    assert "requests" not in names and names <= set(__import__("sys").stdlib_module_names) | {
        "__future__", "timeutil", "politeness"}
    for local in ("politeness", "privacy"):     # the user agent's module and the one it reads the contact from
        tree = ast.parse((Path(linkcheck.__file__).parent / f"{local}.py").read_text(encoding="utf-8"))
        imported = {a.name.split(".")[0] for n in tree.body if isinstance(n, ast.Import) for a in n.names} | {
            n.module.split(".")[0] for n in tree.body if isinstance(n, ast.ImportFrom) and n.module}
        assert imported <= set(__import__("sys").stdlib_module_names) | {"__future__", "privacy"}, local
    timeutil = ast.parse((Path(linkcheck.__file__).parent / "timeutil.py").read_text(encoding="utf-8"))
    assert {a.name for n in timeutil.body if isinstance(n, ast.Import) for a in n.names} <= {"datetime"}


def test_the_user_agent_is_the_scrapers_and_names_the_site_the_repository_and_the_contact():
    """One user agent for everything the project fetches (tests/test_politeness.py pins its text)."""
    assert "https://depauwvswabash.com" in linkcheck.USER_AGENT and render.REPO_URL in linkcheck.USER_AGENT
    assert linkcheck.USER_AGENT == scrape.USER_AGENT


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


def incomplete_report(silent=0, deadline=0, **counts):
    """sample_report with some of its couldn't-check links marked as not requested."""
    report = sample_report(blocked=silent + deadline + 1, **counts)
    blocked = [link for link in report["links"] if link["status"] == "blocked"]
    for link, why in zip(blocked, ["silent"] * silent + ["deadline"] * deadline):
        link["not_requested"] = why
    report["not_requested"] = {"silent": silent, "deadline": deadline}
    return report


def test_an_incomplete_run_says_so_in_the_report_the_summary_and_the_issue():
    line = ("This run was incomplete: 5 links not requested (3 on a host that had stopped answering, 2 after the "
            "run’s time limit), counted under couldn’t check. Run the link check again by hand to check them.")
    report = incomplete_report(silent=3, deadline=2)
    assert linkcheck.not_requested_line(report) == line
    for text in (linkcheck.render_markdown(report), linkcheck.render_markdown(report, include_ok=False),
                 linkcheck.render_issue_body(report)):
        assert text.count(line) == 1 and "6 couldn’t check" in text
    assert linkcheck.not_requested_line(incomplete_report(deadline=1)) == (
        "This run was incomplete: 1 link not requested (1 after the run’s time limit), counted under couldn’t "
        "check. Run the link check again by hand to check it.")
    complete = sample_report()
    assert complete["not_requested"] == {"silent": 0, "deadline": 0}
    assert linkcheck.not_requested_line(complete) == "" and "incomplete" not in linkcheck.render_markdown(complete)
    del complete["not_requested"]                                 # a report written before the limits existed
    assert linkcheck.not_requested_line(complete) == "" and "incomplete" not in linkcheck.render_issue_body(complete)
    assert set(linkcheck.NOT_REQUESTED) == {"silent", "deadline"}


def test_an_incomplete_run_never_closes_the_issue_and_still_opens_or_updates_it():
    """A link listed in the open issue may be one this run did not reach, so "nothing broken" proves nothing."""
    gh = FakeGh(open_issues=[7])
    clean_but_incomplete = incomplete_report(silent=2, broken=0, moved=0, other=0)
    assert linkcheck.sync_issue(clean_but_incomplete, "https://run", run=gh) == "left #7 open: the run was incomplete"
    assert gh.verbs() == [("issue", "list"), ("issue", "comment")]                          # no close, no edit
    comment = gh.calls[1][1]
    assert "this stays open" in comment and "2 links not requested" in comment and comment.endswith("https://run\n")
    gh = FakeGh()                                                  # nothing open: an incomplete run alone opens nothing
    assert linkcheck.sync_issue(clean_but_incomplete, run=gh) == "nothing broken, moved or other, and no open issue"
    assert gh.verbs() == [("issue", "list")]
    gh = FakeGh(open_issues=[7], labels=["broken-links"])         # broken links found: the issue is updated as usual
    assert linkcheck.sync_issue(incomplete_report(deadline=4), "https://run", run=gh) == "updated #7"
    assert gh.verbs() == [("issue", "list"), ("issue", "edit"), ("issue", "comment")]
    assert "4 links not requested" in gh.calls[1][1] and "4 links not requested" in gh.calls[2][1]
    gh = FakeGh(open_issues=[7])                                   # and a complete clean run still closes it
    assert linkcheck.sync_issue(sample_report(broken=0, moved=0, other=0), run=gh) == "closed #7"


def test_the_check_command_stops_at_the_time_limit_writes_the_report_and_warns(tmp_path, monkeypatch, capsys):
    """The command line passes the limit on: with no time at all, nothing is requested (robots.txt included), the
    report is still written, the exit code is still 0 and the run gets a warning."""
    asked = []
    monkeypatch.setattr(linkcheck, "requests_transport", lambda: lambda *a: asked.append(a))
    monkeypatch.setattr(linkcheck, "robots_reader", lambda: lambda url: asked.append(url))
    monkeypatch.setattr(linkcheck.time, "sleep", lambda s: None)
    monkeypatch.setattr(linkcheck.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(linkcheck, "now_utc", lambda: WHEN)
    monkeypatch.setattr(linkcheck, "DEADLINE_MINUTES", 0)
    out = tmp_path / "out"
    code = linkcheck.main(["check", "--site", str(FIX / "rendered"), "--data", str(FIX / "data"), "--out", str(out)])
    assert code == 0 and not asked
    report = json.loads((out / "link-report.json").read_text(encoding="utf-8"))
    assert report["totals"]["checked"] == report["totals"]["blocked"] == report["not_requested"]["deadline"] == 7
    line = linkcheck.not_requested_line(report)
    assert line in (out / "summary.md").read_text(encoding="utf-8")
    assert f"::warning title=Link check incomplete::{line}" in capsys.readouterr().out


def test_the_check_command_writes_the_report_and_exits_zero_even_with_broken_links(tmp_path, monkeypatch):
    def web(method, url, timeout):
        return linkcheck.Response(404 if "wikipedia" in url else 200)
    robots_read = []

    def robots(url):                                              # the fixture's one checked school link is a news story
        robots_read.append(url)
        return "User-agent: *\nDisallow: /news/\nCrawl-delay: 30\n"
    monkeypatch.setattr(linkcheck, "requests_transport", lambda: web)
    monkeypatch.setattr(linkcheck, "robots_reader", lambda: robots)
    monkeypatch.setattr(linkcheck.time, "sleep", lambda s: None)
    monkeypatch.setattr(linkcheck.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(linkcheck, "now_utc", lambda: WHEN)
    out = tmp_path / "out"
    code = linkcheck.main(["check", "--site", str(FIX / "rendered"), "--data", str(FIX / "data"), "--out", str(out)])
    assert code == 0
    report = json.loads((out / "link-report.json").read_text(encoding="utf-8"))
    assert robots_read == ["https://sports.wabash.edu/robots.txt"]
    assert report["skipped"]["robots"] == 1 and report["totals"]["checked"] == 6          # 7 collected, 1 disallowed
    assert "sports.wabash.edu" not in json.dumps(report["links"])
    assert "1 page on the schools’ athletics sites that robots.txt disallows" in (out / "summary.md").read_text(encoding="utf-8")
    assert report["totals"]["broken"] == 1 and report["links"][0]["url"] == "https://en.wikipedia.org/wiki/Monon_Bell"
    assert report["checked_at"] == "2026-11-02T19:41:00+00:00"
    assert (out / "link-report.md").read_text(encoding="utf-8").startswith("# Link check, Nov 2, 2026")
    assert (out / "summary.md").is_file()
    assert report["not_requested"] == {"silent": 0, "deadline": 0}                         # a complete run
    assert "incomplete" not in (out / "summary.md").read_text(encoding="utf-8")

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


def test_the_checks_own_time_limit_ends_well_inside_the_jobs():
    """A job that reaches `timeout-minutes` is cancelled before the report is written and uploaded, so no report,
    no summary and no issue update: the check must stop by itself first. The room left over is for the steps
    before the check (fetch, install, render), the one request that may still be under way, and the upload."""
    jobs = workflow()["jobs"]
    limit = jobs["check"]["timeout-minutes"]
    assert limit == 60 and linkcheck.DEADLINE_MINUTES == 45
    assert limit - linkcheck.DEADLINE_MINUTES >= 15
    assert 0 < jobs["issues"]["timeout-minutes"] <= 5
    # the limit is the check's own: the workflow passes no time and nothing else ends the step early
    check = next(s for s in jobs["check"]["steps"] if "linkcheck.py check" in s.get("run", ""))
    assert "timeout-minutes" not in check and "continue-on-error" not in check
    source = Path(linkcheck.__file__).read_text(encoding="utf-8")
    assert "deadline=DEADLINE_MINUTES * 60" in source
    readme = (ROOT / "README.md").read_text(encoding="utf-8")                              # the README gives the numbers
    assert f"When {linkcheck.SILENT_LINKS} links in a row to one host end with no answer" in readme
    assert f"And {linkcheck.DEADLINE_MINUTES} minutes after the check starts" in readme
    assert f"the job's own limit is {limit} minutes" in readme


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
