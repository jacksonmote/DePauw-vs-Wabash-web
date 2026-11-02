"""Polite scraping: robots.txt is read once per host and obeyed, requests to one host are at least its Crawl-delay
apart (each host on its own clock), and the user agent says who we are. Fake HTTP, fake clock: no network, no
real waiting."""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
import linkcheck  # noqa: E402
import politeness  # noqa: E402
import privacy  # noqa: E402
import scrape  # noqa: E402

D, W = "https://depauwtigers.com", "https://sports.wabash.edu"
ROBOTS = "User-agent: *\nDisallow: /admin/\nAllow: /\nCrawl-delay: 30\n"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        assert seconds > 0
        self.now += seconds


class Web:
    """Serves robots.txt per host and a page for anything else; records every request and when it was made."""
    def __init__(self, clock, robots=None, cost=1.0):
        self.clock, self.robots, self.cost, self.log = clock, robots or {}, cost, []

    def __call__(self, url):
        self.log.append((url, self.clock.now))
        self.clock.now += self.cost                       # each request takes a second
        if url.endswith("/robots.txt"):
            body = self.robots.get(politeness.host_of(url), ROBOTS)
            if isinstance(body, Exception):
                raise body
            return body
        return "<html></html>"


def pacer(robots=None):
    clock = Clock()
    web = Web(clock, robots)
    return politeness.Pacer(fetch=web, sleep=clock.sleep, clock=clock, log=lambda m: None), web, clock


def gaps(web, host):
    times = [(t, url) for url, t in web.log if politeness.host_of(url) == host]
    # start of each request minus the end (start + 1 s) of the one before it on the same host
    return [b[0] - (a[0] + 1.0) for a, b in zip(times, times[1:])]


def test_the_delay_comes_from_robots_txt_and_each_host_keeps_its_own_clock():
    p, web, clock = pacer()
    urls = [f"{D}/sports/a", f"{W}/sports/a", f"{D}/sports/b", f"{W}/sports/b", f"{D}/sports/c", f"{W}/sports/c"]
    p.prepare(urls)
    for u in urls:
        p.get(u)
    for host in ("depauwtigers.com", "sports.wabash.edu"):
        assert all(g >= 30 - 1e-9 for g in gaps(web, host)), gaps(web, host)
        assert p.requests[host] == 4                                          # robots.txt + three pages
    # interleaved, the two hosts wait in parallel: the whole run is about 3 x 30 s, not 6 x 30 s
    assert clock.now - 1000 < 3 * 30 + 10


def test_robots_txt_is_read_once_per_host_per_run_and_before_any_page():
    p, web, _ = pacer()
    for u in [f"{D}/x", f"{D}/y", f"{W}/x", f"{D}/z"]:
        p.get(u)
    robots = [u for u, _ in web.log if u.endswith("/robots.txt")]
    assert robots == [f"{D}/robots.txt", f"{W}/robots.txt"]
    assert web.log[0][0] == f"{D}/robots.txt"


@pytest.mark.parametrize("robots, expected", [
    ("User-agent: *\nAllow: /\n", 30.0),                          # no Crawl-delay: the 30-second fallback
    ("User-agent: *\nAllow: /\nCrawl-delay: 1\n", 3.0),           # never below 3 seconds
    ("User-agent: *\nAllow: /\nCrawl-delay: 45\n", 45.0),
    (OSError("timed out"), 30.0),                                  # unreadable: the fallback, and go ahead
])
def test_the_delay_falls_back_to_30_seconds_and_never_goes_below_3(robots, expected):
    p, _, _ = pacer({"depauwtigers.com": robots})
    assert p.allowed(f"{D}/sports/football/schedule")
    assert p.delays["depauwtigers.com"] == expected


def test_a_missing_robots_txt_allows_everything():
    class NotFound(Exception):
        response = type("R", (), {"status_code": 404})()
    p, _, _ = pacer({"depauwtigers.com": NotFound("404")})
    assert p.allowed(f"{D}/anything") and p.delays["depauwtigers.com"] == 30.0


def test_a_disallowed_page_is_never_requested_and_is_recorded():
    p, web, _ = pacer({"depauwtigers.com": "User-agent: *\nDisallow: /sports/football/\nCrawl-delay: 30\n"})
    with pytest.raises(politeness.Disallowed):
        p.get(f"{D}/sports/football/schedule")
    assert f"{D}/sports/football/schedule" not in [u for u, _ in web.log]
    assert p.blocked == [f"{D}/sports/football/schedule"]
    assert p.get(f"{D}/sports/baseball/schedule") == "<html></html>"


def test_the_real_robots_rules_of_both_schools_allow_every_page_we_read():
    """The wildcard section both schools served on 2026-11-01 (the rest of the file names other crawlers)."""
    real = ("User-agent: rogerbot\nCrawl-delay: 2\nDisallow: /admin/\nAllow: /\n\n"
            "User-agent: *\nDisallow: /common/\nDisallow: /images/\nDisallow: /documents/\nDisallow: /admin/\n"
            "Disallow: /services/\nDisAllow: /site/\nDisAllow: /hidden/\nDisallow: /*.js$\nDisallow: /*.css$\n"
            "Disallow: /*print=true*\nAllow: /\nCrawl-delay: 30\n")
    p, _, _ = pacer({"depauwtigers.com": real, "sports.wabash.edu": real})
    cfg = json.loads(scrape.SOURCES.read_text(encoding="utf-8"))
    urls = scrape.page_urls(cfg)
    assert len(urls) == 28 and all(p.allowed(u) for u in urls)
    assert p.delays == {"depauwtigers.com": 30.0, "sports.wabash.edu": 30.0}


def test_the_user_agent_says_what_it_is_and_how_to_reach_us():
    ua = politeness.USER_AGENT
    assert ua == ("DePauw-vs-Wabash-fan-site/1.0 (+https://depauwvswabash.com; "
                  "https://github.com/jacksonmote/DePauw-vs-Wabash-web; " + privacy.PRIVACY_EMAIL + ")")
    assert "twice a day" not in ua
    assert scrape.USER_AGENT == ua and linkcheck.USER_AGENT == ua            # one user agent everywhere


# ---- the scraper uses it -------------------------------------------------------------------------------- #

def test_a_live_run_waits_the_crawl_delay_between_pages_of_one_host(tmp_path, monkeypatch):
    """The real Pacer inside scrape.main: with a fake clock, every wait it asks for keeps 30 s between requests."""
    clock = Clock()
    log = []

    def fake_fetch(url):
        log.append((politeness.host_of(url), clock.now))
        clock.now += 1
        if url.endswith("/robots.txt"):
            return ROBOTS
        raise OSError("pages are not part of this test")

    monkeypatch.setattr(scrape, "fetch", fake_fetch)
    monkeypatch.setattr(scrape, "SLEEP", clock.sleep)
    monkeypatch.setattr(politeness.time, "monotonic", clock)
    monkeypatch.setattr(scrape, "Pacer", lambda: politeness.Pacer(fetch=fake_fetch, sleep=clock.sleep, clock=clock,
                                                                  log=lambda m: None))
    scrape.main(["--output", str(tmp_path / "series.json"), "--upcoming-output", str(tmp_path / "up.json"),
                 "--checked-file", str(tmp_path / "c"), "--only", "football,baseball", "--dry-run"])
    for host in ("depauwtigers.com", "sports.wabash.edu"):
        times = [t for h, t in log if h == host]
        assert len(times) == 1 + 2 * 2                                     # robots.txt, 2 sports x (history, schedule)
        assert all(b - (a + 1) >= 30 for a, b in zip(times, times[1:]))


def test_no_module_sends_the_schools_any_other_user_agent():
    for path in (ROOT / "scraper").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"User-Agent\"?\s*:\s*\"", text), path.name     # never a literal header value
