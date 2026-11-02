"""
The monthly check of the site's outbound links (.github/workflows/link-check.yml).

It reads the rendered site/ folder and the hand-curated data files, checks every outbound http(s) link once, and
writes a report. It never changes the site or the data: broken links are reported through one GitHub issue
(label broken-links), never fixed here and never by failing the run.

  python scraper/linkcheck.py check [--site site] [--out link-check]   # the live check, writes the report
  python scraper/linkcheck.py issue --report link-check/link-report.json [--run-url URL]   # sync the issue (gh)

What is checked: every <a href> to http(s) on every rendered page, plus every http(s) string in the curated data
files (DATA_FILES). A URL is fetched once (without its #fragment), and the report lists every page and data entry
that uses it. Left out on purpose, and counted in the report (distinct URLs per group):
  * email addresses (mailto:), links to this site, and this repository's own links (issue forms, security
    advisories, source files);
  * every box score on the schools' athletics sites: there are hundreds, they come from the schools' own pages,
    and checking them would send far more traffic to those sites than the scraper's normal page fetches;
  * the opponent-history and schedule pages the scraper already fetches (from sources.json).

How a link is checked, politely: one request at a time, at least HOST_GAP seconds between requests to the same
host (counted from the end of the last one), a TIMEOUT-second timeout, the scraper's own User-Agent (naming the
site, the repository and the privacy contact; politeness.USER_AGENT). HEAD first; a GET only if HEAD did not answer 2xx (405/501, an error status or no answer: many servers
mishandle HEAD); one retry of that GET after RETRY_WAIT seconds for a timeout, a connection error or a 5xx.
Redirects are followed by hand, at most MAX_REDIRECTS, each hop paced like any request. A GET never reads the
body, so no link costs more than one page.

Statuses: broken (404, 410, DNS failure, or still failing after the retry), moved (a 301 or 308 on the way to a
working page at a different URL; the report gives the new one), couldn't check (401, 403, 429 or another
bot-protection answer: many sites block automated checks, so these are reported but never counted as broken),
other (anything unexpected, with the status), OK.

Links within the site are checked separately, from the built files alone (internal_problems): every link between
pages must reach a page or file that was built, and every #fragment an id on its target page. That needs no
network, so the tests run it on a full build on every pull request and before every deploy, rather than monthly.

Standard library only at import time (requests is imported only by the live check), so the issue job can run
this with the runner's own python3.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import socket
import subprocess
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

from politeness import USER_AGENT  # the one user agent the project sends anywhere; standard library only
from timeutil import to_eastern   # standard library only, like this module

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
DATA = ROOT / "data"
SOURCES = Path(__file__).resolve().parent / "sources.json"
OUT = ROOT / "link-check"   # git-ignored; the workflow writes to RUNNER_TEMP instead

# The same values as in render.py (a test keeps them equal); not imported, so the issue job needs no packages.
SITE_URL = "https://depauwvswabash.com/"
REPO_URL = "https://github.com/jacksonmote/DePauw-vs-Wabash-web"
SOURCE_HOSTS = ("depauwtigers.com", "www.depauwtigers.com", "sports.wabash.edu")

# The hand-curated files on main. The bot-managed files hold only the schools' own pages and box scores.
DATA_FILES = ("alumni.json", "endowment.json", "bell-heists.json", "fun-facts.json", "overrides.json")

HOST_GAP = 2.0          # seconds between requests to the same host, at least
TIMEOUT = 20            # seconds, per request
MAX_REDIRECTS = 5
RETRY_WAIT = 10         # seconds before the one retry
REDIRECTS = {301, 302, 303, 307, 308}
PERMANENT = {301, 308}
BROKEN_CODES = {404, 410}
BLOCKED_CODES = {401, 403, 429, 999}   # 999: what some sites send automated clients
TRANSIENT_ERRORS = {"timeout", "connection", "dns", "tls"}

# Skip groups, in report order, with how each is described.
SKIP_GROUPS = {
    "box-scores": ("box score on the schools’ athletics sites", "box scores on the schools’ athletics sites"),
    "scraped-pages": ("opponent-history or schedule page the scraper already fetches",
                      "opponent-history and schedule pages the scraper already fetches"),
    "repository": ("link to this repository (issue forms, security advisories, source files)",
                   "links to this repository (issue forms, security advisories, source files)"),
    "own-site": ("link to this site", "links to this site"),
    "mailto": ("email address (mailto:)", "email addresses (mailto:)"),
}
# Status keys, in report order, with their headings.
STATUSES = {"broken": "Broken", "moved": "Moved permanently", "other": "Other",
            "blocked": "Couldn’t check", "ok": "OK"}
ISSUE_STATUSES = ("broken", "moved", "other")   # any of these keeps the issue open; "blocked" alone never does

LABEL = "broken-links"
LABEL_COLOR, LABEL_DESC = "b60205", "The monthly link check found a broken, moved or unexpected link"
ISSUE_TITLE = "Broken or moved links on the site"
USES_LISTED = 10                # "used on" entries shown per link before "and N more"
ISSUE_LIMIT = 60000             # GitHub allows 65,536 characters in an issue body
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ---- collecting the links ---------------------------------------------------------------------- #

class _Anchors(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hrefs, self.ids = [], set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        if tag == "a" and attrs.get("href"):
            self.hrefs.append(attrs["href"].strip())


def _parse(html: str) -> _Anchors:
    parser = _Anchors()
    parser.feed(html)
    parser.close()
    return parser


def page_hrefs(html: str) -> list:
    return _parse(html).hrefs


def page_path(site_dir: Path, file: Path) -> str:
    """site/alumni/index.html -> /alumni/, site/404.html -> /404.html."""
    rel = file.relative_to(site_dir).as_posix()
    return "/" + (rel[: -len("index.html")] if rel.endswith("index.html") else rel)


def data_urls(node, where: str = "", entry: str | None = None) -> list:
    """[(url, "entry: path")] for every http(s) string in a JSON value. The entry is the nearest enclosing object's
    `id`; the path runs from there (or from the top when there is none), without a final `url` key."""
    out = []
    if isinstance(node, dict):
        if isinstance(node.get("id"), str):
            entry, where = node["id"], ""
        for key, value in node.items():
            out += data_urls(value, f"{where}.{key}" if where else str(key), entry)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            out += data_urls(value, f"{where}[{i}]", entry)
    elif isinstance(node, str) and node.lower().startswith(("http://", "https://")):
        path = where[: -len(".url")] if where.endswith(".url") else where
        out.append((node, f"{entry}: {path}" if entry and path else (entry or path)))
    return out


def scraped_pages(sources: dict) -> set:
    """The opponent-history and schedule URLs the scraper fetches, for both schools."""
    depauw, wabash = sources["base_url"].rstrip("/"), sources["wabash"]["base_url"].rstrip("/")
    urls = set()
    for sport in sources["sports"]:
        for base, key in ((depauw, "path"), (wabash, "wabash_path"),
                          (depauw, "schedule_path"), (wabash, "wabash_schedule_path")):
            if sport.get(key):
                urls.add(base + sport[key])
    return urls


def skip_group(url: str, scraped: set) -> str | None:
    """Which skip group a link belongs to, or None when it is checked."""
    if url.lower().startswith("mailto:"):
        return "mailto"
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    if host in {urlparse(SITE_URL).hostname, "www." + urlparse(SITE_URL).hostname}:
        return "own-site"
    if url == REPO_URL or url.startswith(REPO_URL + "/") or url.startswith(REPO_URL + "?"):
        return "repository"
    if host in SOURCE_HOSTS and "boxscore" in parts.path.lower():
        return "box-scores"
    if url in scraped:
        return "scraped-pages"
    return None


def collect(site_dir: Path = SITE, data_dir: Path = DATA, sources_path: Path = SOURCES) -> tuple:
    """({url: [where it is used]}, {skip group: set of URLs}). URLs are without their #fragment."""
    scraped = scraped_pages(json.loads(Path(sources_path).read_text(encoding="utf-8")))
    links, skipped = {}, {group: set() for group in SKIP_GROUPS}

    def add(raw: str, where: str) -> None:
        if raw.lower().startswith("mailto:"):
            skipped["mailto"].add(raw.split("?", 1)[0])
            return
        if urlparse(raw).scheme.lower() not in ("http", "https"):
            return                                               # relative, #fragment or another scheme: not outbound
        url = urldefrag(raw)[0]
        group = skip_group(url, scraped)
        if group:
            skipped[group].add(url)
            return
        uses = links.setdefault(url, [])
        if where not in uses:
            uses.append(where)

    site_dir = Path(site_dir)
    pages = sorted(site_dir.rglob("*.html"), key=lambda f: page_path(site_dir, f))
    if not pages:
        raise FileNotFoundError(f"no HTML pages in {site_dir}; run python scraper/render.py first")
    for file in pages:
        for href in page_hrefs(file.read_text(encoding="utf-8")):
            add(href, page_path(site_dir, file))
    for name in DATA_FILES:
        file = Path(data_dir) / name
        if file.is_file():
            for url, entry in data_urls(json.loads(file.read_text(encoding="utf-8"))):
                add(url, f"data/{name}: {entry}")
    return links, skipped


# ---- links within the site (files only, no network) -------------------------------------------- #

def _target_file(site_dir: Path, path: str) -> Path | None:
    """The built file a site path is served from (/alumni/ -> alumni/index.html), or None if there is none.
    GitHub Pages also serves /alumni (no slash) from alumni/index.html."""
    rel = path.lstrip("/")
    candidates = [site_dir / rel / "index.html"] if rel.endswith("/") or not rel else         [site_dir / rel, site_dir / rel / "index.html"]
    return next((c for c in candidates if c.is_file()), None)


def internal_problems(site_dir: Path = SITE) -> list:
    """[(page, href, problem)] for every link within the site that leads nowhere: a page or file that was not
    built, or a #fragment with no matching id on its target page. Relative and root-relative links and absolute
    links to this site all count. Reads the built files only; the monthly check never needs this, the tests do."""
    site_dir = Path(site_dir)
    pages = sorted(site_dir.rglob("*.html"), key=lambda f: page_path(site_dir, f))
    parsed = {f.resolve(): _parse(f.read_text(encoding="utf-8")) for f in pages}
    own = {urlparse(SITE_URL).hostname, "www." + urlparse(SITE_URL).hostname}
    problems = []
    for file in pages:
        here = page_path(site_dir, file)
        for href in parsed[file.resolve()].hrefs:
            target = urlparse(urljoin(SITE_URL.rstrip("/") + here, href))
            if target.scheme not in ("http", "https") or (target.hostname or "").lower() not in own:
                continue                                         # outbound or mailto: the monthly check's job
            found = _target_file(site_dir, target.path)
            if found is None:
                problems.append((here, href, f"no built file for {target.path}"))
            elif target.fragment and found.suffix == ".html" and target.fragment not in parsed[found.resolve()].ids:
                problems.append((here, href, f"no id=\"{target.fragment}\" on {page_path(site_dir, found)}"))
    return problems


# ---- checking one link ------------------------------------------------------------------------- #

class LinkError(Exception):
    """A request that got no HTTP answer. kind: timeout, connection, dns or tls."""
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(detail or kind)
        self.kind, self.detail = kind, detail


class Response:
    def __init__(self, status: int, headers: dict | None = None):
        self.status = status
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}


class Pacer:
    """At least `gap` seconds between the end of one request to a host and the start of the next."""
    def __init__(self, gap: float = HOST_GAP, clock=None, sleep=None):
        self.gap, self.clock, self.sleep, self.last = gap, clock or time.monotonic, sleep or time.sleep, {}

    def before(self, host: str) -> None:
        if host in self.last:
            wait = self.last[host] + self.gap - self.clock()
            if wait > 0:
                self.sleep(wait)

    def after(self, host: str) -> None:
        self.last[host] = self.clock()


def follow(method: str, url: str, transport, pacer: Pacer) -> dict:
    """One request and its redirects. {status, headers, error, detail, final_url, hops: [(code, to)]}."""
    hops, current = [], url
    for _ in range(MAX_REDIRECTS + 1):
        host = (urlparse(current).hostname or "").lower()
        pacer.before(host)
        try:
            resp = transport(method, current, TIMEOUT)
        except LinkError as exc:
            return {"status": None, "error": exc.kind, "detail": exc.detail, "final_url": current, "hops": hops}
        finally:
            pacer.after(host)
        location = resp.headers.get("location")
        if resp.status in REDIRECTS and location:
            target = urldefrag(urljoin(current, location))[0]
            hops.append((resp.status, target))
            if urlparse(target).scheme not in ("http", "https"):
                return {"status": resp.status, "error": "bad-redirect", "detail": f"redirects to {target}",
                        "final_url": target, "hops": hops}
            current = target
            continue
        return {"status": resp.status, "headers": resp.headers, "error": None, "final_url": current, "hops": hops}
    return {"status": None, "error": "redirects", "detail": f"more than {MAX_REDIRECTS} redirects",
            "final_url": current, "hops": hops}


def _ok(result: dict) -> bool:
    return result["error"] is None and 200 <= result["status"] < 300


def _transient(result: dict) -> bool:
    return result["error"] in TRANSIENT_ERRORS or (result["error"] is None and result["status"] >= 500)


def _bot_wall(result: dict) -> bool:
    headers = result.get("headers") or {}
    return result["status"] in BLOCKED_CODES or "cf-mitigated" in headers


ERROR_WORDS = {"timeout": "timed out", "connection": "could not connect", "dns": "DNS lookup failed",
               "tls": "TLS (certificate) error"}


def classify(url: str, result: dict, retried: bool) -> dict:
    """{status, code, detail, final_url} for the last result of a link."""
    code, final = result["status"], result["final_url"]
    again = " (also on the retry)" if retried else ""
    out = {"code": code, "final_url": final if final != url else None}
    if result["error"] in TRANSIENT_ERRORS:
        return {**out, "status": "broken", "detail": ERROR_WORDS[result["error"]] + again}
    if result["error"]:
        return {**out, "status": "other", "detail": result["detail"]}
    if 200 <= code < 300:
        permanent = [hop for hop, _ in result["hops"] if hop in PERMANENT]
        if final != url and permanent:
            return {**out, "status": "moved", "detail": f"{permanent[0]} to a new address"}
        return {**out, "status": "ok", "detail": f"{code}" + (" after a temporary redirect" if final != url else "")}
    if code in BROKEN_CODES:
        return {**out, "status": "broken", "detail": f"{code}"}
    if _bot_wall(result):
        return {**out, "status": "blocked", "detail": f"{code} (the site blocks automated checks)"}
    if code >= 500:
        return {**out, "status": "broken", "detail": f"{code}{again}"}
    return {**out, "status": "other", "detail": f"{code}"}


def check_link(url: str, transport, pacer: Pacer, sleep=None) -> dict:
    """HEAD, then a GET if HEAD did not answer 2xx, then one retry of the GET for a transient failure."""
    sleep = sleep or time.sleep
    result, retried = follow("HEAD", url, transport, pacer), False
    if not _ok(result):
        result = follow("GET", url, transport, pacer)
        if _transient(result):
            sleep(RETRY_WAIT)
            result, retried = follow("GET", url, transport, pacer), True
    return {"url": url, **classify(url, result, retried)}


def polite_order(urls: list) -> list:
    """Round-robin over hosts, so consecutive requests rarely go to the same host and the pacer seldom waits."""
    by_host = {}
    for url in sorted(urls):
        by_host.setdefault((urlparse(url).hostname or "").lower(), []).append(url)
    queues, out = [by_host[h] for h in sorted(by_host)], []
    while any(queues):
        for q in queues:
            if q:
                out.append(q.pop(0))
    return out


def run_checks(links: dict, transport, pacer: Pacer | None = None, sleep=None, log=print) -> list:
    pacer = pacer or Pacer(sleep=sleep)
    results, order = [], polite_order(list(links))
    for i, url in enumerate(order, 1):
        result = check_link(url, transport, pacer, sleep)
        result["used_on"] = links[url]
        log(f"[{i}/{len(order)}] {result['status']:8} {result['detail']}  {url}")
        results.append(result)
    return results


def requests_transport():
    """The live transport: one request, no redirects followed, the body never read."""
    import requests   # only the live check needs it

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})

    def is_dns(exc) -> bool:
        seen, todo = set(), [exc]
        while todo:
            e = todo.pop()
            if e is None or id(e) in seen:
                continue
            seen.add(id(e))
            if isinstance(e, socket.gaierror) or "NameResolution" in type(e).__name__:
                return True
            todo += [getattr(e, "reason", None), e.__cause__, e.__context__,
                     *[a for a in getattr(e, "args", ()) if isinstance(a, BaseException)]]
        return False

    def send(method: str, url: str, timeout: float) -> Response:
        try:
            resp = session.request(method, url, allow_redirects=False, timeout=timeout, stream=True)
        except requests.exceptions.Timeout as exc:
            raise LinkError("timeout", str(exc)) from exc
        except requests.exceptions.SSLError as exc:
            raise LinkError("tls", str(exc)) from exc
        except requests.exceptions.ConnectionError as exc:
            raise LinkError("dns" if is_dns(exc) else "connection", str(exc)) from exc
        except requests.exceptions.RequestException as exc:
            raise LinkError("connection", str(exc)) from exc
        try:
            return Response(resp.status_code, dict(resp.headers))
        finally:
            resp.close()

    return send


# ---- the report -------------------------------------------------------------------------------- #

def build_report(results: list, skipped: dict, checked_at: dt.datetime) -> dict:
    order = list(STATUSES)
    links = sorted(results, key=lambda r: (order.index(r["status"]), r["url"]))
    return {
        "schema_version": 1,
        "checked_at": checked_at.astimezone(dt.timezone.utc).isoformat(timespec="seconds"),
        "totals": {"checked": len(results), **{s: sum(r["status"] == s for r in results) for s in STATUSES}},
        "skipped": {group: len(urls) for group, urls in skipped.items()},
        "links": links,
    }


def fmt_date(iso: str) -> str:
    """The Eastern calendar date of a UTC time, like the site's dates: "Nov 2, 2026"."""
    d = to_eastern(dt.datetime.fromisoformat(iso))[0].date()
    return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def totals_line(report: dict) -> str:
    t = report["totals"]
    return (f"Checked {plural(t['checked'], 'link')}: {t['broken']} broken, {t['moved']} moved, {t['other']} other, "
            f"{t['blocked']} couldn’t check, {t['ok']} OK.")


def skipped_line(report: dict) -> str:
    parts = [f"{n} {SKIP_GROUPS[g][0 if n == 1 else 1]}" for g, n in report["skipped"].items()]
    return f"Skipped on purpose ({sum(report['skipped'].values())} distinct URLs): " + "; ".join(parts) + "."


def _md_url(url: str) -> str:
    return "<" + url.replace("<", "%3C").replace(">", "%3E").replace(" ", "%20") + ">"


def link_lines(link: dict) -> list:
    head = f"- {_md_url(link['url'])}: {link['detail']}"
    if link.get("final_url"):
        head += f"; now at {_md_url(link['final_url'])}" if link["status"] == "moved" else f"; ended at {_md_url(link['final_url'])}"
    uses = link["used_on"]
    shown = ", ".join(uses[:USES_LISTED]) + (f", and {len(uses) - USES_LISTED} more" if len(uses) > USES_LISTED else "")
    return [head, f"  - Used on: {shown}"]


def sections(report: dict, statuses, keep: int | None = None) -> list:
    """A heading per status (with its full count) and its links, in order; only the first `keep` links overall
    when `keep` is given (whole links only: a link is never split from its "Used on" line)."""
    lines, left = [], keep
    for status in statuses:
        group = [link for link in report["links"] if link["status"] == status]
        shown = group if left is None else group[:max(left, 0)]
        if shown:
            lines += ["", f"## {STATUSES[status]} ({len(group)})", ""]
            for link in shown:
                lines += link_lines(link)
        if left is not None:
            left -= len(shown)
    return lines


def render_markdown(report: dict, include_ok: bool = True) -> str:
    """The full report (include_ok) or the job summary (every status but OK)."""
    shown = list(STATUSES) if include_ok else [s for s in STATUSES if s != "ok"]
    lines = [f"# Link check, {fmt_date(report['checked_at'])}", "", totals_line(report), "", skipped_line(report)]
    lines += sections(report, shown)
    if not include_ok:
        lines += ["", "The full report, including every OK link, is the run’s link-report artifact."]
    return "\n".join(lines) + "\n"


def needs_issue(report: dict) -> bool:
    return any(report["totals"][s] for s in ISSUE_STATUSES)


def render_issue_body(report: dict, run_url: str = "") -> str:
    """The issue text: broken, moved and other links with where they are used. No timestamp (the comment has it)."""
    intro = [
        "The monthly link check found links on the site that need a look. Nothing was changed: fix a link in the "
        "page or data file it is used in, through a pull request.",
        "",
        totals_line(report),
    ]
    if report["totals"]["blocked"]:
        intro += ["", f"{plural(report['totals']['blocked'], 'link')} couldn’t be checked (the site blocks automated "
                      "checks); they are listed in the run summary, not here."]
    tail = ["", skipped_line(report)] + (["", f"Latest run: {run_url}"] if run_url else [])
    total = sum(report["totals"][s] for s in ISSUE_STATUSES)
    keep = total
    while True:   # drop whole links from the end until the body fits
        more = [] if keep == total else ["", f"… and {plural(total - keep, 'more link')}, see the run’s "
                                             "link-report artifact."]
        text = "\n".join(intro + sections(report, ISSUE_STATUSES, keep) + more + tail) + "\n"
        if len(text) <= ISSUE_LIMIT or keep == 0:
            return text
        keep = max(0, keep - max(1, (len(text) - ISSUE_LIMIT) // 200))   # a link is a few hundred characters


# ---- the issue --------------------------------------------------------------------------------- #

def gh(*args: str, stdin: str | None = None) -> str:
    return subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, check=True).stdout


def sync_issue(report: dict, run_url: str = "", run=gh) -> str:
    """Open, update or close the one broken-links issue. `run` is the gh runner (replaced in tests)."""
    open_issues = sorted(json.loads(run("issue", "list", "--label", LABEL, "--state", "open",
                                        "--json", "number", "--limit", "100")), key=lambda i: i["number"])
    when = fmt_date(report["checked_at"])
    if not needs_issue(report):
        if not open_issues:
            return "nothing broken, moved or other, and no open issue"
        n = str(open_issues[0]["number"])
        run("issue", "comment", n, "--body-file", "-",
            stdin=f"Link check of {when}: nothing broken, moved or other any more. {totals_line(report)} "
                  f"Closing. {run_url}".strip() + "\n")
        run("issue", "close", n)
        return f"closed #{n}"
    body = render_issue_body(report, run_url)
    comment = f"Link check of {when}: {totals_line(report)} {run_url}".strip() + "\n"
    if open_issues:
        n = str(open_issues[0]["number"])
        run("issue", "edit", n, "--body-file", "-", stdin=body)
        run("issue", "comment", n, "--body-file", "-", stdin=comment)
        return f"updated #{n}"
    labels = {l["name"] for l in json.loads(run("label", "list", "--json", "name", "--limit", "200"))}
    if LABEL not in labels:
        run("label", "create", LABEL, "--color", LABEL_COLOR, "--description", LABEL_DESC)
    url = run("issue", "create", "--title", ISSUE_TITLE, "--label", LABEL, "--body-file", "-", stdin=body).strip()
    run("issue", "comment", url, "--body-file", "-", stdin=comment)
    return f"opened {url}"


# ---- command line ------------------------------------------------------------------------------ #

def now_utc() -> dt.datetime:
    """The check time for the report (tests replace it, so they never read the real clock)."""
    return dt.datetime.now(dt.timezone.utc)


def write_report(report: dict, out: Path) -> None:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "link-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "link-report.md").write_text(render_markdown(report), encoding="utf-8")
    (out / "summary.md").write_text(render_markdown(report, include_ok=False), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="check every outbound link and write the report (live)")
    c.add_argument("--site", type=Path, default=SITE)
    c.add_argument("--data", type=Path, default=DATA)
    c.add_argument("--out", type=Path, default=OUT)
    i = sub.add_parser("issue", help="open, update or close the broken-links issue from a report (needs gh)")
    i.add_argument("--report", type=Path, required=True)
    i.add_argument("--run-url", default="")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # the curly apostrophe on a Windows console
    if args.command == "check":
        links, skipped = collect(args.site, args.data)
        results = run_checks(links, requests_transport())
        report = build_report(results, skipped, now_utc())
        write_report(report, args.out)
        print(totals_line(report))
        print(skipped_line(report))
        print(f"Wrote {args.out / 'link-report.md'} and link-report.json")
    else:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        print(sync_issue(report, args.run_url))
    return 0   # broken links are reported through the issue, never by failing the run


if __name__ == "__main__":
    sys.exit(main())
