"""
DePauw vs. Wabash head-to-head scraper.

Reads each school's Sidearm "opponent history" page for every sport (DePauw's at depauwtigers.com,
Wabash's at sports.wabash.edu), parses every game in the history table, and checks the parsed games
against that page's own summary block (wins / losses / ties, home and away records). Each school's
games are kept as they were read (data/depauw.json and data/wabash.json, both in DePauw's point of
view) and merged into data/series.json, which the site reads. The matching rules are in merge.py.

Safety rules, so a bad night never wipes good data:
  * A school's page for a sport that fails to load, parses to zero games, or disagrees with its own
    summary is NOT updated; that school's previous games for the sport are kept, the merge is
    redone from the two copies, and everything else still updates.
  * A school's page whose game count shrinks compared with its saved copy is NOT updated unless
    --allow-shrink is passed.
  * Files are only rewritten when their content actually changed, so the scheduled job doesn't make
    empty commits.
  * A page whose own total ("Total Points/Goals/Runs") disagrees with its parsed games only gets a warning
    (total_warning): its games are kept and the exit code is unchanged.
  * A page that redirects to another site or to plain http, or is over MAX_PAGE_BYTES (10 MB), counts as failed to
    load (see fetch), so the same rule applies: that school's previous games are kept.
  * Exit code is non-zero if any page failed, so the GitHub Action shows red.
  * The "last checked" time (.checked-at, uncommitted) is written only after a live run in which
    at least one page loaded and validated. A run where every page failed leaves it alone.

Politeness (scraper/politeness.py): each host's robots.txt is read once per run before any page, a page it
disallows is never requested (that school keeps its last good data, the run exits non-zero, and --blocked-file
lists the URL so the workflow's report job can name it), and requests to one host are at least its Crawl-delay
apart (30 seconds on both schools' sites). --only limits a run to some sports (a game-day run reads only the
sport being played); every other sport keeps its saved data and schedule exactly as they were.

Upcoming meetings (data/upcoming.json) come from each sport's schedule page at both schools and are
separate from the records: a schedule that fails to load or parse only logs a warning, keeps that
school's last good games, and never changes the exit code or the validation of the history.

Usage:
  python scraper/scrape.py                      # fetch live pages, update the data files
  python scraper/scrape.py --fixtures DIR       # parse saved HTML instead: DIR/<sport-key>.html for
                                                # DePauw, DIR/wabash-<sport-key>.html for Wabash
  python scraper/scrape.py --dry-run            # parse and report, don't write
  python scraper/scrape.py --remerge-only       # no fetching: rebuild series.json from the saved copies and rulings
  python scraper/scrape.py --only football      # read only these sports' pages (comma-separated keys)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup

import merge
import politeness
import review
import upcoming
from timeutil import to_eastern

ROOT = Path(__file__).resolve().parent.parent
SOURCES = Path(__file__).resolve().parent / "sources.json"
OUTPUT = ROOT / "data" / "series.json"
UPCOMING = ROOT / "data" / "upcoming.json"
# Time of the last scrape that actually reached at least one sport. Never committed (it is
# gitignored and lives outside data/), so series.json still changes only when games change.
CHECKED_AT = ROOT / ".checked-at"

USER_AGENT = politeness.USER_AGENT   # the same one the link check sends
SLEEP = time.sleep                   # how the pacer waits (tests replace it so they never wait)

NUMBER = r"\d+(?:\.\d+)?"          # swimming scores can end in .5
SCORE_RE = re.compile(rf"^([WLT])\s+({NUMBER})\s*-\s*({NUMBER})$")
NO_SCORE_RE = re.compile(r"^([WLT])\s*-?$")   # "L -": the result is listed, the score is not
RECORD_RE = re.compile(r"^(\d+)-(\d+)(?:-(\d+))?$")


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

@dataclass
class Game:
    date: str                      # ISO yyyy-mm-dd
    season: str
    location: str                  # "home" | "away" | "neutral" | "unknown" (DePauw's perspective)
    site: Optional[str]            # "Crawfordsville, Ind." etc., None when the page says N/A
    result: str                    # "W" | "L" | "T" for DePauw
    depauw: Optional[float]        # None when the page lists a result but no score (see score_known)
    wabash: Optional[float]        # a whole score is an int; only swimming has halves (147.5)
    note: Optional[str] = None     # e.g. "2 OT"
    boxscore: Optional[str] = None # absolute URL when the page links one
    score_known: bool = True       # False: the result counts in the record, there is no score to count
    score_order_reversed: bool = False   # the result letter and the score order disagreed; see parse_page


@dataclass
class Summary:
    """The page's own summary block, used to cross-check our parse."""
    wins: Optional[int] = None
    losses: Optional[int] = None
    ties: Optional[int] = None
    home: Optional[tuple] = None
    away: Optional[tuple] = None
    streak: Optional[str] = None        # e.g. "W4", "L1"
    total: Optional[float] = None       # DePauw's total points / goals / runs, as the page reports it
    total_label: Optional[str] = None   # "points", "goals", "runs", ...


@dataclass
class SportResult:
    key: str
    name: str
    source_url: str
    total_label: str = "points"
    games: list = field(default_factory=list)
    summary: Summary = field(default_factory=Summary)
    skipped_rows: list = field(default_factory=list)


class ParseError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def to_number(text) -> float:
    """A score as a number: a whole score is an int (147), a half stays a float (147.5), never 147.0."""
    value = float(str(text).replace(",", ""))
    return int(value) if value.is_integer() else value


def _text(el) -> str:
    return " ".join(el.get_text(" ", strip=True).split()) if el else ""


def _parse_date(th) -> str:
    """Rows carry the date twice: '11/15/2025' (short) and 'November 15, 2025' (long)."""
    short = th.select_one("span.hide-on-medium-down")
    if short and _text(short):
        return dt.datetime.strptime(_text(short), "%m/%d/%Y").date().isoformat()
    long_ = th.select_one("span.hide-on-large")
    if long_ and _text(long_):
        return dt.datetime.strptime(_text(long_), "%B %d, %Y").date().isoformat()
    raise ParseError(f"no date in row header: {_text(th)!r}")


def _parse_record(text: str) -> Optional[tuple]:
    m = RECORD_RE.match(text.strip())
    if not m:
        return None
    w, l, t = m.groups()
    return (int(w), int(l), int(t or 0))


def parse_summary(soup: BeautifulSoup) -> Summary:
    s = Summary()
    for item in soup.select("section.sidearm-opponent-history__data .sidearm-opponent-history__item"):
        label = _text(item.find("h2")).lower()
        number = _text(item.select_one(".sidearm-opponent-history__item--number"))
        if label == "wins":
            s.wins = int(number)
        elif label == "losses":
            s.losses = int(number)
        elif label == "ties":
            s.ties = int(number)
        elif label == "home record":
            s.home = _parse_record(number)
        elif label == "away record":
            s.away = _parse_record(number)
        elif label == "streak":
            m = re.match(r"^([WLT])(\d+)$", number.replace(" ", ""))
            s.streak = m.group(0) if m else None
        elif label.startswith("total ") and re.fullmatch(NUMBER, number.replace(",", "")):
            s.total = to_number(number)
            s.total_label = label[len("total "):].strip()
    if s.wins is not None and s.ties is None:
        s.ties = 0  # the page omits the Ties box when there have never been any
    return s


def parse_page(html: str, *, base_url: str, opponent_match: str) -> tuple[list, Summary, list]:
    """Return (games, summary, skipped_rows) for one opponent-history page."""
    soup = BeautifulSoup(html, "html.parser")

    article = soup.select_one("article.sidearm-opponent-history")
    if article is None:
        raise ParseError("opponent-history article not found (layout changed or wrong page)")

    opponent = _text(article.select_one(".sidearm-opponent-history-breadcrumbs-opponent"))
    if opponent_match.lower() not in opponent.lower():
        raise ParseError(f"page is for {opponent!r}, expected an opponent matching {opponent_match!r}")

    table = article.select_one("table.sidearm-table")
    if table is None:
        raise ParseError("history table not found")

    headers = [_text(th).lower() for th in table.select("thead th")]
    for needed in ("date", "location", "score"):
        if needed not in headers:
            raise ParseError(f"history table is missing a {needed!r} column (headers: {headers})")

    games, skipped = [], []
    for row in table.select("tbody tr"):
        th = row.find("th")
        if th is None:
            continue
        date = _parse_date(th)
        season = _text(row.select_one('td[data-label="Season"]'))

        loc_cell = row.select_one("td.location")
        loc_spans = loc_cell.find_all("span") if loc_cell else []
        loc_type = _text(loc_spans[0]).lower() if loc_spans else ""
        location = loc_type if loc_type in ("home", "away", "neutral") else "unknown"
        site = _text(loc_spans[1]) if len(loc_spans) > 1 else ""
        site = None if site in ("", "N/A") else site

        score_cell = row.select_one("td.score")
        spans = score_cell.find_all("span") if score_cell else []
        post = score_cell.select_one(".post-score") if score_cell else None
        note = _text(post) or None
        score_text = _text(spans[-1]) if spans else ""
        m = SCORE_RE.match(score_text)
        bare = NO_SCORE_RE.match(score_text)
        reversed_order = False
        if m:
            result, ours, theirs = m.group(1), to_number(m.group(2)), to_number(m.group(3))
            # The letter is the result. When the scores clearly say the other side won, the page wrote the
            # two scores in the other order (Wabash's Nov 12, 2008 meet: "L 153 - 141"), so trust the letter.
            if (result == "W" and ours < theirs) or (result == "L" and ours > theirs):
                ours, theirs, reversed_order = theirs, ours, True
        elif bare:
            # "L -" beside "No Score Listed (Wabash Loss)": a result with no score. It counts in the record
            # (the page's own summary includes it) but has nothing to add to point totals.
            result, ours, theirs, note = bare.group(1), None, None, None
        else:
            # Canceled / postponed rows: keep a record but don't count them.
            skipped.append({"date": date, "season": season, "text": score_text or _text(score_cell)})
            continue

        # The cell's CSS class (score W / L / T) should agree with the text.
        classes = score_cell.get("class", [])
        if any(c in ("W", "L", "T") for c in classes) and result not in classes:
            raise ParseError(f"{date}: score text says {result} but cell class says {classes}")

        box = None
        for a in row.select(".media-list a"):
            if "boxscore" in (a.get("href") or "").lower():
                href = a["href"]
                box = href if href.startswith("http") else base_url.rstrip("/") + href
                break

        games.append(Game(date=date, season=season, location=location, site=site,
                          result=result, depauw=ours, wabash=theirs, note=note, boxscore=box,
                          score_known=ours is not None, score_order_reversed=reversed_order))

    return games, parse_summary(soup), skipped


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

def tally(games: list) -> dict:
    out = {"W": 0, "L": 0, "T": 0}
    by_loc: dict = {}
    for g in games:
        out[g.result] += 1
        rec = by_loc.setdefault(g.location, [0, 0, 0])
        rec["WLT".index(g.result)] += 1
    return {"wins": out["W"], "losses": out["L"], "ties": out["T"],
            "by_location": {k: tuple(v) for k, v in by_loc.items()}}


def chronological(games: list) -> list:
    """Oldest first. The page lists games newest first, so reversing before the (stable) date sort
    keeps a doubleheader in playing order: game 1, then game 2."""
    return sorted(reversed(games), key=lambda g: g.date)


def streak_code(games: list) -> str:
    """Current streak from DePauw's side, e.g. "W4". Expects games oldest first."""
    r, n = games[-1].result, 0
    for g in reversed(games):
        if g.result != r:
            break
        n += 1
    return f"{r}{n}"


def validate(games: list, summary: Summary) -> list:
    """Return a list of problems (empty list means the parse agrees with the page)."""
    problems = []
    if not games:
        problems.append("parsed zero games")
        return problems
    t = tally(games)
    for k in ("wins", "losses", "ties"):
        page_val = getattr(summary, k)
        if page_val is None:
            problems.append(f"page summary has no {k} figure")
        elif page_val != t[k]:
            problems.append(f"{k}: parsed {t[k]}, page says {page_val}")
    for loc in ("home", "away"):
        page_rec = getattr(summary, loc)
        mine = t["by_location"].get(loc, (0, 0, 0))
        if page_rec is not None and tuple(page_rec) != tuple(mine):
            problems.append(f"{loc} record: parsed {'-'.join(map(str, mine))}, "
                            f"page says {'-'.join(map(str, page_rec))}")
    if summary.streak is not None and streak_code(chronological(games)) != summary.streak:
        problems.append(f"streak: parsed {streak_code(chronological(games))}, page says {summary.streak}")
    # Doubleheaders put two real games on one date, sometimes with the same score, so a repeated
    # date and score is not a duplicate by itself. A page that really repeats rows is caught by a
    # box-score link appearing on two rows, by three or more identical games on one date, and by
    # the tally check above (repeated rows make the totals disagree with the page's summary).
    boxes = [g.boxscore for g in games if g.boxscore]
    if len(set(boxes)) != len(boxes):
        problems.append("duplicate game rows (the same box score appears on more than one row)")
    same_day = Counter((g.date, g.depauw, g.wabash) for g in games)
    if any(n > 2 for n in same_day.values()):
        problems.append("duplicate game rows (three or more identical games on one date)")
    return problems


def printed_total(games: list):
    """What the page's own "Total Points/Goals/Runs" box should equal: the sum of the first score printed on
    each row that has a score, from the page's own side, before any flipping or merging (so disputes between
    the schools do not matter). A result with no score ("L -") adds nothing. A row whose two scores were
    read in swapped order (score_order_reversed) is counted as it was printed, not as it was swapped."""
    return merge.norm_num(sum(g.wabash if g.score_order_reversed else g.depauw for g in games if g.score_known))


def total_warning(games: list, summary: Summary, school: str, sport: str):
    """A warning (a dict) when the page's own total disagrees with its parsed games, otherwise None. Only a
    warning for now: the page's data is kept and the run stays green (see "How the data updates" in the README)."""
    if summary.total is None:
        return None
    parsed = printed_total(games)
    if parsed == summary.total:
        return None
    return {"school": school, "sport": sport, "label": summary.total_label or "points", "page": summary.total,
            "parsed": parsed, "difference": merge.norm_num(summary.total - parsed)}


def report_total_warnings(found: list) -> None:
    """Log every total-points mismatch again together at the end of the run and, on GitHub Actions, list them
    in the run's summary page."""
    if not found:
        return
    lines = [f"{w['sport']}, {w['school']}'s page: total {w['label']} {w['page']}, parsed games add up to "
             f"{w['parsed']} (page minus parsed {w['difference']:+})" for w in found]
    print(f"WARN {len(found)} page(s) whose own total does not match the parsed games (data kept, run not failed):",
          file=sys.stderr)
    for line in lines:
        print(f"WARN   {line}", file=sys.stderr)
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write("### Total points check (warning only)\n\n"
                     + "".join(f"- {line}\n" for line in lines) + "\n")


# --------------------------------------------------------------------------- #
# Fetching and output
# --------------------------------------------------------------------------- #

def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


MAX_PAGE_BYTES = 10_000_000   # the largest real page is about 1 MB (men's basketball history); 10 MB is far past any real one


def _site(url: str) -> str:
    """The host with a leading www. removed, so https://example.com and https://www.example.com count as the same site."""
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def fetch(url: str) -> str:
    """One page as text. Refuses a page that redirects to another site or to plain http, and one bigger than
    MAX_PAGE_BYTES (checked while reading, so a huge body is never held in memory). The real pages do neither."""
    import requests  # imported here so fixture runs and tests don't need it
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30, stream=True)
    try:
        resp.raise_for_status()
        for hop in [*resp.history, resp]:
            if not hop.url.startswith("https://") or _site(hop.url) != _site(url):
                raise ValueError(f"{url} redirected to {hop.url}; only the same site over https is accepted")
        declared = resp.headers.get("Content-Length", "")
        if declared.isdigit() and int(declared) > MAX_PAGE_BYTES:
            raise ValueError(f"{url} is {declared} bytes, over the {MAX_PAGE_BYTES} limit")
        body = bytearray()
        for chunk in resp.iter_content(chunk_size=65536):
            body += chunk
            if len(body) > MAX_PAGE_BYTES:
                raise ValueError(f"{url} is over the {MAX_PAGE_BYTES} byte limit")
        return bytes(body).decode(resp.encoding or "utf-8", errors="replace")
    finally:
        resp.close()


def Pacer() -> politeness.Pacer:
    """The run's polite access to the schools' sites (robots.txt, per-host crawl delay), fetching with fetch()."""
    return politeness.Pacer(fetch=lambda url: fetch(url), sleep=lambda seconds: SLEEP(seconds))


def page_urls(cfg: dict, only: Optional[set] = None) -> list:
    """Every history and schedule page a live run of these sports reads."""
    urls = []
    for sport in cfg["sports"]:
        if only is not None and sport["key"] not in only:
            continue
        for kind in ("path", "schedule"):
            for school in ("depauw", "wabash"):
                sc = school_cfg(cfg, school)
                if sport.get(sc[kind]):
                    urls.append(sc["base_url"].rstrip("/") + sport[sc[kind]])
    return urls


def report_blocked(blocked: list, path: Optional[Path]) -> None:
    """Pages robots.txt disallowed this run: a clear warning, the run summary, and --blocked-file for the report job."""
    if not blocked:
        return
    print(f"WARN robots.txt disallows {len(blocked)} page(s) the scraper needs; they were not requested and their "
          f"last good data is kept:", file=sys.stderr)
    for url in blocked:
        print(f"WARN   {url}", file=sys.stderr)
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write("### robots.txt disallows pages the scraper needs\n\n"
                     + "".join(f"- {u}\n" for u in blocked)
                     + "\nThey were not requested; the last good data is kept.\n\n")
    if path:
        path.write_text("".join(f"{u}\n" for u in blocked), encoding="utf-8")


def school_cfg(cfg: dict, school: str) -> dict:
    """How to reach one school's pages. Wabash's pages are the same Sidearm markup, with the opponent
    (DePauw) matched by its own name and id on sports.wabash.edu."""
    if school == "depauw":
        return {"name": "DePauw", "base_url": cfg["base_url"], "opponent_match": cfg["opponent_match"],
                "opponent_id": cfg["opponent_id"], "path": "path", "schedule": "schedule_path", "prefix": ""}
    w = cfg["wabash"]
    return {"name": "Wabash", "base_url": w["base_url"], "opponent_match": w["opponent_match"],
            "opponent_id": w["opponent_id"], "path": "wabash_path", "schedule": "wabash_schedule_path",
            "prefix": "wabash-"}


def game_dicts(games: list, school: str) -> list:
    """Parsed games as dicts, oldest first, in DePauw's point of view. Wabash's page is written from
    Wabash's side, so its games are flipped here."""
    out = []
    for g in chronological(games):
        d = asdict(g)
        for flag, default in (("score_known", True), ("score_order_reversed", False)):
            if d[flag] is default:
                del d[flag]                # only the unusual row carries a flag, so other games stay as they were
        out.append(d)
    return [merge.flip(g) for g in out] if school == "wabash" else out


def tally_dicts(games: list) -> tuple:
    return tuple(sum(1 for g in games if g["result"] == r) for r in "WLT")


def streak_of(games: list) -> str:
    r, n = games[-1]["result"], 0
    for g in reversed(games):
        if g["result"] != r:
            break
        n += 1
    return f"{r}{n}"


def raw_payload(name: str, url: str, label: str, games: list, skipped: list) -> dict:
    w, l, t = tally_dicts(games)
    return {"name": name, "source_url": url, "total_label": label, "games_played": len(games),
            "record": {"depauw_wins": w, "wabash_wins": l, "ties": t},
            "games": games, "skipped_rows": skipped}


def load_sports(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")).get("sports", {}) if path.exists() else {}


def legacy_depauw(previous_sports: dict) -> dict:
    """Before the merge, series.json held DePauw's page alone. On the first run it stands in for
    data/depauw.json as the last good copy and the shrink baseline."""
    if not previous_sports or any("source" in g for s in previous_sports.values() for g in s["games"]):
        return {}
    return {k: {**s, "games": [dict(g) for g in s["games"]]} for k, s in previous_sports.items()}


def scrape_one(sc: dict, sport: dict, school: str, fixtures: Optional[Path], pacer,
               previous: dict, allow_shrink: bool, total_warnings: Optional[list] = None):
    """One school's history page for one sport: fetched, parsed and checked against that page's own
    summary. Returns the raw payload, or None when a fixture run has no page for this school.
    Raises on anything wrong, and the caller keeps the last good copy."""
    key = sport["key"]
    url = sc["base_url"].rstrip("/") + sport[sc["path"]]
    if fixtures:
        page = fixtures / f"{sc['prefix']}{key}.html"
        if not page.exists():
            return None
        html = page.read_text(encoding="utf-8")
    else:
        html = pacer.get(url)
    games, summary, skipped = parse_page(html, base_url=sc["base_url"], opponent_match=sc["opponent_match"])
    problems = validate(games, summary)
    if problems:
        raise ParseError("; ".join(problems))
    mismatch = total_warning(games, summary, sc["name"], sport["name"])
    if mismatch:
        print(f"WARN {sport['name']:<16} {sc['name']:<7} total {mismatch['label']}: page says {mismatch['page']}, "
              f"parsed games add up to {mismatch['parsed']} (difference {mismatch['difference']:+})", file=sys.stderr)
        if total_warnings is not None:
            total_warnings.append(mismatch)
    old_count = previous.get(key, {}).get("games_played", 0)
    if len(games) < old_count and not allow_shrink:
        raise ParseError(f"game count fell from {old_count} to {len(games)}; "
                         f"rerun with --allow-shrink if that's expected")
    label = sport.get("total_label") or summary.total_label or "points"
    return raw_payload(sport["name"], url, label, game_dicts(games, school),
                       [{**r, "school": sc["name"]} for r in skipped])


def merged_payload(sport: dict, raw: dict, rules: Optional[dict] = None) -> tuple:
    """The sport as the site reads it: both schools' games merged. Returns (payload, MergeResult or None)."""
    d, w = raw.get("depauw"), raw.get("wabash")
    # Rulings name a row from each school, so they only apply when both schools' games are here.
    result = merge.merge_sport(d["games"] if d else [], w["games"] if w else [],
                               season_months=sport.get("season_months"), rules=rules if d and w else None)
    games = result.games
    t = tally_dicts(games)
    label = sport.get("total_label") or (d or w).get("total_label") or "points"
    payload = {
        "name": sport["name"],
        "source_url": (d or w)["source_url"],
        **({"wabash_source_url": w["source_url"]} if w else {}),
        "record": {"depauw_wins": t[0], "wabash_wins": t[1], "ties": t[2]},
        "games_played": len(games),
        "first_game": games[0]["date"],
        "last_game": max(g["date"] for g in games if g.get("date_known") is not False),
        "current_streak": streak_of(games),
        "points": {side: merge.norm_num(sum(g[side] for g in games if g.get("score_known", True)))
                   for side in ("depauw", "wabash")},
        "total_label": label,
        **({"unit": sport["unit"]} if sport.get("unit") else {}),
        **({"sources": result.stats} if d and w else {}),
        "games": games,
        "skipped_rows": (d["skipped_rows"] if d else []) + (w["skipped_rows"] if w else []),
    }
    return payload, result


def build_output(sports: dict, previous: dict) -> dict:
    totals = {"depauw_wins": 0, "wabash_wins": 0, "ties": 0, "games_played": 0}
    for s in sports.values():
        totals["depauw_wins"] += s["record"]["depauw_wins"]
        totals["wabash_wins"] += s["record"]["wabash_wins"]
        totals["ties"] += s["record"]["ties"]
        totals["games_played"] += s["games_played"]
    return {
        "schema_version": 2,
        "updated_at": previous.get("updated_at"),
        "perspective": "DePauw",
        "totals": totals,
        "sports": sports,  # in sources.json order, which is the order the page shows
    }


def write_if_changed(path: Path, data: dict) -> bool:
    text = json.dumps(data, indent=2) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"Wrote {path}")
    return True


def report_merge(name: str, result) -> None:
    print("     " + merge.summary_line(name, result.stats))
    for m in merge.beyond_window(result):
        d, w = m["depauw"], m["wabash"]
        print(f"REVIEW {name}: matched beyond {merge.MAX_DATE_GAP_DAYS} days ({m['kind']}): DePauw {d['date']} "
              f"{d['depauw']}-{d['wabash']}  /  Wabash {w['date']} {w['depauw']}-{w['wabash']}")
    for m in merge.near_misses(result):
        d, w = m["depauw"], m["wabash"]
        print(f"CHECK {name}: DePauw only {d['date']} {d['depauw']}-{d['wabash']}  /  Wabash only {w['date']} "
              f"{w['depauw']}-{w['wabash']}  ({m['days']} days apart"
              f"{', same score' if m['same_score'] else ''}); not merged")


def hold_changes(cfg: dict, parsed: dict, prev_raw: dict, new_raw: dict, pending: list, keep_rules: dict,
                 overrides: dict, apply_reviewed: bool, now: str) -> list:
    """The review hold (see review.py). For every page that passed its own checks, compare it with that
    school's saved copy, keep the saved version of each historical change that is not approved, and put the
    result in `new_raw`. Returns the pending entries after this run: the held changes, plus the old entries
    of any school and sport that was not read this run (a failed page must not clear what is waiting)."""
    out, used = [], set()
    for sport in cfg["sports"]:
        key = sport["key"]
        old_by = {s: (prev_raw[s].get(key) or {}).get("games") for s in ("depauw", "wabash")}
        # Only a school that has a saved copy to compare with can have changes held; the first read is taken.
        current = review.current_season(*(old_by[s] for s in old_by),
                                        *(parsed[(s, key)]["games"] for s in old_by if (s, key) in parsed))
        for school in ("depauw", "wabash"):
            raw = parsed.get((school, key))
            if raw is None:
                continue
            if old_by[school] is None:
                new_raw[school][key] = raw
                continue
            games, entries, notes, matched = review.settle(
                school, key, old_by[school], raw["games"], current, raw["source_url"], pending=pending,
                rules=keep_rules.get(key, []), sport_rules=overrides.get(key), apply_reviewed=apply_reviewed,
                now=now)
            used.update(id(r) for r in matched)
            for n in notes:
                print(f"INFO {sport['name']} {school_cfg(cfg, school)['name']}: {n}")
            if entries:
                print(f"HOLD {sport['name']:<16} {school_cfg(cfg, school)['name']:<7} {len(entries)} change(s) "
                      f"to old games held for review", file=sys.stderr)
            w, l, t = tally_dicts(games)
            new_raw[school][key] = {**raw, "games": games, "games_played": len(games),
                                    "record": {"depauw_wins": w, "wabash_wins": l, "ties": t}}
            out.extend(entries)
    read = {(sc, k) for (sc, k) in parsed}
    out.extend(e for e in pending if (e["school"], e["sport"]) not in read)
    for key, rules in keep_rules.items():
        for r in rules:
            if id(r) not in used:
                print(f"INFO keep_previous ruling for {key} ({r['school']}) matched no change this run")
    return out


def _carry(entry: dict, old: dict, history_dates: Optional[list], today: Optional[dt.date], canceled: list,
           name: str) -> None:
    """Keep a game that just finished on the schedule file until the history has it (changes `entry`)."""
    if history_dates is None or today is None:
        return
    kept = upcoming.carry_over(old.get("games", []), entry["games"], history_dates=history_dates,
                               canceled=set(canceled), today=today)
    if kept:
        entry["games"] = sorted(entry["games"] + kept, key=lambda g: g["date"])
        print(f"INFO {name}: keeping {len(kept)} finished game(s) on the schedule until the history lists them")


def scrape_schedules(cfg: dict, previous: dict, fixtures: Optional[Path], pacer,
                     history: Optional[dict] = None, today: Optional[dt.date] = None,
                     only: Optional[set] = None) -> dict:
    """Upcoming Wabash games per sport, from both schools' schedule pages. Never raises: a schedule
    problem is a warning, and a school whose page did not load keeps its games from the last run.

    `history` (sport key -> the merged game dates) and `today` (Eastern) switch on the carry-over of a game
    that has just finished (see upcoming.carry_over): the game stays in the file until the history has it.
    `only` limits the run to those sport keys; every other sport keeps its saved schedule untouched."""
    sports = dict(previous)
    for sport in cfg["sports"]:
        key = sport["key"]
        if only is not None and key not in only:
            continue
        old = previous.get(key, {})
        got: dict = {}
        canceled: list = []
        for school in ("depauw", "wabash"):
            sc = school_cfg(cfg, school)
            path = sport.get(sc["schedule"])
            if not path:
                continue
            url = sc["base_url"].rstrip("/") + path
            try:
                if fixtures:
                    page = fixtures / f"{sc['prefix']}{key}-schedule.html"
                    if not page.exists():
                        continue
                    html = page.read_text(encoding="utf-8")
                else:
                    html = pacer.get(url)
                games = upcoming.parse_schedule(html, opponent_id=sc["opponent_id"], canceled=canceled)
                if school == "wabash":
                    games = [upcoming.flip_location(g) for g in games]
                got[school] = (url, games)
                print(f"OK   {sport['name']:<16} {sc['name']:<7} schedule: {len(games)} game(s) not yet played")
            except Exception as exc:  # a schedule problem must never block the records
                print(f"WARN {sport['name']:<16} {sc['name']:<7} schedule not updated: {exc}", file=sys.stderr)
        if not got:
            continue
        kept = old.get("games", [])
        d_url, d_games = got.get("depauw", (old.get("schedule_url"),
                                            [g for g in kept if g.get("source", "depauw") in ("both", "depauw")]))
        w_url, w_games = got.get("wabash", (old.get("wabash_schedule_url"),
                                            [g for g in kept if g.get("source") in ("both", "wabash")]))
        if not w_url:                                   # Wabash's schedule is not part of this sport yet
            sports[key] = {"schedule_url": d_url, "games": d_games}
            _carry(sports[key], old, (history or {}).get(key), today, canceled, sport["name"])
            continue
        try:
            games, warnings = upcoming.merge_schedules(sport["name"], d_games, w_games)
            for w in warnings:
                print(f"WARN {w}", file=sys.stderr)
            sports[key] = {"schedule_url": d_url, "wabash_schedule_url": w_url, "games": games}
            _carry(sports[key], old, (history or {}).get(key), today, canceled, sport["name"])
        except Exception as exc:
            print(f"WARN {sport['name']:<16} schedules not merged: {exc}", file=sys.stderr)
    return {k: sports[k] for k in (s["key"] for s in cfg["sports"]) if k in sports}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixtures", type=Path, help="read <dir>/<sport-key>.html (and wabash-<sport-key>.html) "
                                                  "instead of fetching")
    ap.add_argument("--output", type=Path, default=OUTPUT)
    ap.add_argument("--depauw-output", type=Path, help="default: depauw.json next to --output")
    ap.add_argument("--wabash-output", type=Path, help="default: wabash.json next to --output")
    ap.add_argument("--overrides", type=Path, help="rulings on same/different games; default: overrides.json "
                                                   "next to --output")
    ap.add_argument("--pending", type=Path, help="changes to old games waiting for review; default: "
                                                 "pending-changes.json next to --output")
    ap.add_argument("--apply-reviewed", action="store_true",
                    help="accept the changes already waiting in the pending file (and nothing new)")
    ap.add_argument("--upcoming-output", type=Path, default=UPCOMING)
    ap.add_argument("--checked-file", type=Path, default=CHECKED_AT,
                    help="where to record the time of a successful live check")
    ap.add_argument("--remerge-only", action="store_true",
                    help="fetch nothing: rebuild series.json from the saved depauw.json, wabash.json and the "
                         "rulings, the way a normal run merges (a merge that fails keeps that sport's last good "
                         "data and the run exits non-zero). Leaves upcoming.json and the check time alone.")
    ap.add_argument("--only", help="comma-separated sport keys: read only these sports' pages and keep every other "
                                   "sport's saved data (game-day runs)")
    ap.add_argument("--blocked-file", type=Path, help="write the URLs robots.txt disallowed this run here, one per line")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-shrink", action="store_true",
                    help="accept a sport whose game count went down")
    args = ap.parse_args(argv)
    raw_paths = {"depauw": args.depauw_output or args.output.parent / "depauw.json",
                 "wabash": args.wabash_output or args.output.parent / "wabash.json"}

    cfg = json.loads(SOURCES.read_text(encoding="utf-8"))
    only = None
    if args.only is not None:
        only = {k.strip() for k in args.only.split(",") if k.strip()}
        unknown = only - {s["key"] for s in cfg["sports"]}
        if unknown:
            print(f"FAIL --only names unknown sport(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            return 1
        print(f"INFO this run reads only: {', '.join(sorted(only)) or 'nothing'}; every other sport keeps its saved data")
    pending_path = args.pending or args.output.parent / "pending-changes.json"
    try:
        overrides_path = args.overrides or args.output.parent / "overrides.json"
        overrides = merge.load_overrides(overrides_path)
        keep_rules = review.load_keep_previous(overrides_path)
        pending = review.load_pending(pending_path)
    except (merge.MergeError, ValueError) as exc:
        print(f"FAIL overrides: {exc}", file=sys.stderr)
        return 1
    previous = json.loads(args.output.read_text(encoding="utf-8")) if args.output.exists() else {}
    prev_sports = previous.get("sports", {})
    prev_raw = {school: load_sports(path) for school, path in raw_paths.items()}
    if not prev_raw["depauw"]:
        prev_raw["depauw"] = legacy_depauw(prev_sports)

    pacer = Pacer()
    if not args.fixtures and not args.remerge_only:
        pacer.prepare(page_urls(cfg, only))      # robots.txt for each host first, once
    new_raw: dict = {school: dict(prev_raw[school]) for school in raw_paths}   # last good copy until replaced
    failures = []
    total_warnings: list = []               # pages whose own total disagrees with their parsed games (warning only)
    loaded = 0
    parsed: dict = {}                       # (school, sport key) -> a page that passed its own checks
    for sport in ([] if args.remerge_only else cfg["sports"]):
        key = sport["key"]
        if only is not None and key not in only:
            continue
        for school in ("depauw", "wabash"):
            sc = school_cfg(cfg, school)
            try:
                raw = scrape_one(sc, sport, school, args.fixtures, pacer, prev_raw[school], args.allow_shrink,
                                 total_warnings)
                if raw is None:
                    continue
                parsed[(school, key)] = raw
                loaded += 1
                r = raw["record"]
                print(f"OK   {sport['name']:<16} {sc['name']:<7} {raw['games_played']:>3} games  "
                      f"DePauw {r['depauw_wins']}-{r['wabash_wins']}-{r['ties']}"
                      + (f"  ({len(raw['skipped_rows'])} non-score rows skipped)" if raw["skipped_rows"] else ""))
            except Exception as exc:  # keep going; one bad page shouldn't block the rest
                failures.append((school, key))
                print(f"FAIL {sport['name']:<16} {sc['name']:<7} {exc}", file=sys.stderr)

    report_total_warnings(total_warnings)
    new_pending = hold_changes(cfg, parsed, prev_raw, new_raw, pending, keep_rules, overrides,
                               args.apply_reviewed, now_utc().isoformat())

    new_sports: dict = {}
    for sport in cfg["sports"]:
        key = sport["key"]
        raw = {school: new_raw[school].get(key) for school in raw_paths}
        if not raw["depauw"] and not raw["wabash"]:
            if key in prev_sports:
                new_sports[key] = prev_sports[key]
            continue
        try:
            new_sports[key], result = merged_payload(sport, raw, overrides.get(key))
            report_merge(sport["name"], result)
        except Exception as exc:  # a merge that does not add up never replaces good data
            failures.append(("merge", key))
            print(f"FAIL {sport['name']:<16} merge: {exc}", file=sys.stderr)
            if key in prev_sports:
                new_sports[key] = prev_sports[key]

    out = build_output(new_sports, previous)
    changed = (json.dumps(out["sports"], sort_keys=True) != json.dumps(prev_sports, sort_keys=True)
               or list(out["sports"]) != list(prev_sports))  # a reorder in sources.json also counts
    tot = out["totals"]
    print(f"All sports: DePauw {tot['depauw_wins']}-{tot['wabash_wins']}-{tot['ties']} "
          f"over {tot['games_played']} games. Data {'changed' if changed else 'unchanged'}.")

    if not args.dry_run:
        if review.pending_doc(new_pending) != review.pending_doc(pending) or not pending_path.exists():
            write_if_changed(pending_path, review.pending_doc(new_pending))
        for school, path in raw_paths.items():
            if new_raw[school] != load_sports(path):
                write_if_changed(path, {"schema_version": 1, "school": school_cfg(cfg, school)["name"],
                                        "perspective": "DePauw", "sports": new_raw[school]})
        if changed:
            out["updated_at"] = now_utc().isoformat()
            write_if_changed(args.output, out)

    if args.remerge_only:   # no schedule fetch, no check time: nothing was read from the schools
        return 1 if failures else 0

    old_upcoming = json.loads(args.upcoming_output.read_text(encoding="utf-8")) if args.upcoming_output.exists() else {}
    history = {k: [g["date"] for g in v["games"] if g.get("date_known", True)] for k, v in out["sports"].items()}
    new_upcoming = scrape_schedules(cfg, old_upcoming.get("sports", {}), args.fixtures, pacer,
                                    history=history, today=to_eastern(now_utc())[0].date(), only=only)
    if new_upcoming != old_upcoming.get("sports", {}) and not args.dry_run:
        write_if_changed(args.upcoming_output, {"schema_version": 1, "sports": new_upcoming})

    report_blocked(pacer.blocked, args.blocked_file)
    if pacer.blocked:
        failures.append(("robots", "disallowed"))
    if pacer.requests:
        print(f"INFO requests this run: {pacer.report()}")

    # A fixture run or a dry run is not a check of the live site, and a run where every page failed
    # proves nothing about the data, so only a real run with at least one good page advances the time.
    if loaded and not args.fixtures and not args.dry_run:
        args.checked_file.write_text(now_utc().isoformat() + "\n", encoding="utf-8")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
