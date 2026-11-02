"""
How the project behaves on the schools' sites: who it says it is, how long it waits between requests and
which pages it may read. Standard library only.

  * USER_AGENT names the site, the repository and the privacy contact, so either school can reach us. The
    scraper and the link check send the same one.
  * Each host's robots.txt is read once per run, before the first page from that host. A page robots.txt
    disallows for our user agent is never requested: `Pacer.get` raises Disallowed and the URL is recorded in
    `Pacer.blocked`, so the caller keeps its last good data and the run can say what was blocked.
  * Between two requests to the same host it waits at least that host's Crawl-delay (robots.txt), counted from
    the end of the previous request; DEFAULT_CRAWL_DELAY when robots.txt has none or cannot be read, never less
    than MIN_GAP_SECONDS. Each host keeps its own clock, so two hosts read in turn each wait only for themselves:
    a full scrape of both schools takes about as long as one school's.

A robots.txt that is missing (404) allows everything; one that cannot be read at all (an error page, a bot
wall, a timeout) also lets the run go ahead, at the default delay, with a warning: the pages themselves were
allowed the last time robots.txt was read, and a failure to read it says nothing about them.
"""

from __future__ import annotations

import sys
import time
import urllib.robotparser
from collections import Counter
from urllib.parse import urlparse

from privacy import PRIVACY_EMAIL

SITE_URL = "https://depauwvswabash.com"
REPO_URL = "https://github.com/jacksonmote/DePauw-vs-Wabash-web"
USER_AGENT = f"DePauw-vs-Wabash-fan-site/1.0 (+{SITE_URL}; {REPO_URL}; {PRIVACY_EMAIL})"

DEFAULT_CRAWL_DELAY = 30.0   # seconds, when robots.txt sets none or cannot be read (both schools set 30)
MIN_GAP_SECONDS = 3.0        # never closer together than this, whatever robots.txt says


class Disallowed(Exception):
    """robots.txt does not allow this page for our user agent; it was not requested."""


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


class Pacer:
    """Polite access to the schools' sites for one run. `fetch(url) -> str` does the request (scrape.fetch in
    production); `sleep` and `clock` are injectable so tests never wait."""

    def __init__(self, fetch=None, sleep=time.sleep, clock=time.monotonic, user_agent: str = USER_AGENT,
                 log=None):
        self.fetch = fetch
        self.sleep = sleep
        self.clock = clock
        self.user_agent = user_agent
        self.log = log or (lambda msg: print(msg, file=sys.stderr))
        self.rules: dict = {}      # host -> RobotFileParser, or None when robots.txt could not be read
        self.delays: dict = {}     # host -> seconds between requests
        self.last: dict = {}       # host -> clock() at the end of the previous request
        self.requests = Counter()  # host -> requests made this run (robots.txt included)
        self.blocked: list = []    # URLs not requested because robots.txt disallows them
        self.waited = Counter()    # host -> seconds spent waiting

    def _request(self, url: str) -> str:
        host = host_of(url)
        if host in self.last:
            remaining = self.last[host] + self.delays.get(host, DEFAULT_CRAWL_DELAY) - self.clock()
            if remaining > 0:
                self.waited[host] += remaining
                self.sleep(remaining)
        try:
            return self.fetch(url)
        finally:
            self.requests[host] += 1
            self.last[host] = self.clock()

    def prepare(self, urls) -> None:
        """Read robots.txt for every host among `urls` (each once per run), before any page."""
        for url in urls:
            self._robots(host_of(url), url)

    def _robots(self, host: str, url: str):
        if host in self.delays:
            return self.rules[host]
        robots_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}/robots.txt"
        self.delays[host] = DEFAULT_CRAWL_DELAY
        try:
            text = self._request(robots_url)
        except Exception as exc:  # noqa: BLE001 - any failure means "could not read it"
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 404:
                self.log(f"INFO {host}: no robots.txt (404), so every page is allowed; waiting "
                         f"{DEFAULT_CRAWL_DELAY:g} s between requests")
                parser = urllib.robotparser.RobotFileParser()
                parser.parse([])
                self.rules[host] = parser
            else:
                self.log(f"WARN {host}: robots.txt could not be read ({exc}); going ahead with "
                         f"{DEFAULT_CRAWL_DELAY:g} s between requests")
                self.rules[host] = None
            return self.rules[host]
        parser = urllib.robotparser.RobotFileParser()
        parser.parse(text.splitlines())
        delay = parser.crawl_delay(self.user_agent)
        self.delays[host] = max(MIN_GAP_SECONDS, float(delay) if delay is not None else DEFAULT_CRAWL_DELAY)
        self.rules[host] = parser
        self.log(f"INFO {host}: robots.txt read, {self.delays[host]:g} s between requests")
        return parser

    def allowed(self, url: str) -> bool:
        rules = self._robots(host_of(url), url)
        return rules is None or rules.can_fetch(self.user_agent, url)

    def get(self, url: str) -> str:
        """One page, after robots.txt says yes and the host's delay has passed."""
        if not self.allowed(url):
            self.blocked.append(url)
            raise Disallowed(f"robots.txt on {host_of(url)} disallows {url} for our user agent; not requested")
        return self._request(url)

    def report(self) -> str:
        return "; ".join(f"{h}: {self.requests[h]} request(s), {self.delays.get(h, DEFAULT_CRAWL_DELAY):g} s apart"
                         for h in sorted(self.requests)) or "no requests"
