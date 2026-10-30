"""
Static site generator for the DePauw vs. Wabash page.

Reads data/series.json (and, if present, data/upcoming.json and the uncommitted .checked-at) plus the
hand-curated data/alumni.json and data/endowment.json, and writes a finished site to site/:
  index.html                      the athletics home page (the only page with a deferred script)
  alumni/index.html               the alumni and endowment page (alumni.py)
  how-it-works/index.html         landing page; how-it-works/athletics/ and how-it-works/alumni/ (howitworks.py)
  bell-heist/index.html           public, unindexed fictional story
  404.html                        root-relative URLs, noindex
  styles.css, the icons, easter-eggs.js (home only), three original easter-egg SVGs,
  og-image.png and alumni-og-image.png (drawn by images.py), site.webmanifest,
  games.csv, data/series.json, data/alumni.json, data/endowment.json, robots.txt, sitemap.xml, .nojekyll
Every page gets its header and nav (site_nav), head tags and footer (site_footer: a page-specific data block,
then shared lines) here. Only the home page loads its optional deferred easter-eggs script; Pillow is needed only for images.

Every figure on the page comes from the data file. Anything missing or inconsistent
raises DataError and the script exits non-zero, so a bad data file can never produce
a blank or half-true page.

Usage:
  python scraper/render.py                      # data/series.json -> site/
  python scraper/render.py --data FILE --out DIR
  python scraper/render.py --now 2026-11-14T13:30     # preview game-day mode as if it were this Eastern time
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import re
import io
import shutil
import sys
from pathlib import Path
from urllib.parse import quote, urlparse

import facts
import gameday
import howitworks
import images
import meetings
import merge
import places
import review
import alumni as alumni_page
from timeutil import from_eastern, to_eastern

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "series.json"
STATIC = ROOT / "static"
OUT = ROOT / "site"
UPCOMING = ROOT / "data" / "upcoming.json"
OVERRIDES = ROOT / "data" / "overrides.json"
CHECKED_AT = ROOT / ".checked-at"  # written by scrape.py after a live run; never committed

SITE_URL = "https://depauwvswabash.com/"
HOW_PATH = "how-it-works/"      # the landing page for the two plain-language pages, relative to the site root
HOW_ATHLETICS_PATH = HOW_PATH + "athletics/"
HOW_ALUMNI_PATH = HOW_PATH + "alumni/"
BASE_PATH = urlparse(SITE_URL).path  # "/" on the custom domain
ALUMNI_PATH = "alumni/"
ALUMNI_DATA = ROOT / "data" / "alumni.json"
ENDOWMENT_DATA = ROOT / "data" / "endowment.json"
REPO_URL = "https://github.com/jacksonmote/DePauw-vs-Wabash-web"
SOURCE_HOST = "depauwtigers.com"
WABASH_HOST = "sports.wabash.edu"
SOURCE_HOSTS = (SOURCE_HOST, "www." + SOURCE_HOST, WABASH_HOST)
# Where DePauw's games vs. Wabash are played when the source gives no site (neutral sites are never guessed).
HOME_SITE = "Greencastle, Ind."
AWAY_SITE = "Crawfordsville, Ind."

TITLE = "DePauw University vs. Wabash College: All-Time Head-to-Head Records"
SUBTITLE = "DePauw University vs. Wabash College: all-time head-to-head records in men’s sports."
# The two "back" links of the how-it-works pages and 404, one wording style (arrow first) everywhere.
BACK_ATHLETICS = "← Back to the head-to-head records"
BACK_ALUMNI = "← Back to the alumni and endowment page"
# The header line of the pages that serve both halves of the site (the how-it-works landing page and 404).
NEUTRAL_SUBTITLE = ("DePauw and Wabash, side by side: head-to-head athletics, notable graduates and endowments.")
NOT_INCLUDED = ("cross country, track and field, and golf, which are multi-team events rather than "
                "head-to-head games. Swimming and diving counts dual meets only; invitationals and "
                "championships are left out.")
MEN_ONLY = ("Only men’s head-to-head competition is compared: Wabash is an all-male college, and multi-team "
            "events (cross country, track and field, golf, and swimming invitationals and championships) "
            "aren’t head-to-head.")
DISCLAIMER = "Unofficial fan site, not affiliated with DePauw University or Wabash College."
COPYRIGHT_HOLDER = "Jackson Mote"
COPYRIGHT_START = 2026

BELL_URL = "https://en.wikipedia.org/wiki/Monon_Bell"
# Written in our own words. It holds no record, streak or other figure that changes: those come from the data.
BELL_HISTORY = (
    "The Monon Bell is a 300-pound locomotive bell that once rode on a Monon Railroad engine. "
    f"Starting with the {facts.BELL_START} season it became the traveling trophy for the DePauw–Wabash "
    "football game, an idea credited to a DePauw alumnus. The winner takes the Bell home until the next "
    "meeting, and if the game ends in a tie, the team that already has it keeps it. Students from both "
    "schools have also stolen and hidden the Bell many times over the years, which is now part of the lore."
)

CSP = ("default-src 'none'; style-src 'self'; img-src 'self'; manifest-src 'self'; "
       "base-uri 'none'; form-action 'none'; object-src 'none'")

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
STREAK_RE = re.compile(r"^([WLT])(\d+)$")


class DataError(Exception):
    """The data file is missing, empty or inconsistent."""


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def esc(value) -> str:
    return html.escape(str(value), quote=True)


def safe_url(url) -> str | None:
    """Only link to https pages on the two source sites; drop anything else (javascript:, http:, other hosts)."""
    if not isinstance(url, str):
        return None
    parts = urlparse(url)
    if parts.scheme != "https" or parts.hostname not in SOURCE_HOSTS:
        return None
    return url


# Visible text uses the curly apostrophe (’) everywhere; the data files, sources.json, URLs, ids and games.csv
# keep the sources' straight one. The conversion happens once, when each page is written (curly_html), so
# every renderer can keep working with the sources' own spelling.
APOSTROPHE = re.compile(r"&#x27;|&#39;|'")
READ_ATTR = re.compile(r'(?<![\w-])(alt|aria-label|title|placeholder)="([^"]*)"')
# Meta tags whose content a reader sees (a link preview card, a search result), by name or property.
READ_META = {"description", "twitter:title", "twitter:description", "twitter:image:alt",
             "og:title", "og:description", "og:image:alt", "og:site_name"}


def curly(text: str) -> str:
    """The text with every straight apostrophe, plain or HTML-escaped, written as a curly one."""
    return APOSTROPHE.sub("\u2019", text)


def curly_html(page: str) -> str:
    """A finished page with the curly apostrophe in everything a reader sees or hears: text, the <title>, the
    description and share-card meta tags, and alt, aria-label, title and placeholder attributes. Links, ids,
    classes and every other attribute are left exactly as they were."""
    parts = re.split(r"(<[^>]*>)", page)
    for i, part in enumerate(parts):
        if not part.startswith("<") or part.startswith("<!"):
            parts[i] = curly(part) if not part.startswith("<!") else part
            continue
        if part.startswith("<meta"):
            label = re.search(r'(?<![\w-])(?:name|property)="([^"]*)"', part)
            if label and label.group(1) in READ_META:
                part = re.sub(r'(?<![\w-])content="([^"]*)"', lambda m: f'content="{curly(m.group(1))}"', part)
        parts[i] = READ_ATTR.sub(lambda m: f'{m.group(1)}="{curly(m.group(2))}"', part)
    return "".join(parts)


def report_url(sport_name: str | None = None) -> str:
    """Opens the data-correction issue form, with the sport filled in when the link comes from a card."""
    query = "template=data-correction.yml"
    if sport_name:
        query += f"&title={quote(curly('Data correction: ' + sport_name))}&sport={quote(curly(sport_name))}"
    return f"{REPO_URL}/issues/new?{query}"


def report_link(sport_name: str | None = None) -> str:
    return f'<a href="{esc(report_url(sport_name))}">Report a data error</a>'


def fmt_num(x) -> str:
    """A score, margin or total for display: 4,459.5 and 147.5, but 4,612 and 153 (never 147.50 or 153.0)."""
    return f"{facts.num(x):,}"


def plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def fmt_date(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{MONTHS[d.month - 1][:3]} {d.day}, {d.year}"


def date_known(g: dict) -> bool:
    """False for a game whose only date was a placeholder (Wabash's oldest games sit on Jan 1)."""
    return g.get("date_known", True)


def undated_label(g: dict) -> str:
    """What stands in for a date: the season when it spans two years ("1954-55"), else the year."""
    season = str(g.get("season") or "")
    return season if "-" in season else facts.game_year(g)


def date_text(g: dict) -> str:
    """The game's date for display. A game with no usable date shows its season or year and says so, never a made-up day."""
    return fmt_date(g["date"]) if date_known(g) else f"{undated_label(g)} (date not recorded)"


def date_html(g: dict) -> str:
    if date_known(g):
        return f'<time datetime="{esc(g["date"])}">{esc(fmt_date(g["date"]))}</time>'
    label = esc(undated_label(g))
    shown = f'<time datetime="{label}">{label}</time>' if label.isdigit() else f"<span>{label}</span>"
    return f'{shown} <span class="date-note">(date not recorded)</span>'


def game_words(s: dict) -> tuple:
    """(singular, plural) for what a sport plays: games, or matches for tennis (from sources.json)."""
    one, many = s.get("unit") or ("game", "games")
    return one, many


def count_noun(n: int, words: tuple) -> str:
    return f"{n:,} {words[0] if n == 1 else words[1]}"


def copyright_line(now: dt.datetime | None = None) -> str:
    """'© 2026 Jackson Mote', or '© 2026–2027 Jackson Mote' once the build year (Eastern) is later."""
    year = to_eastern(now or dt.datetime.now(dt.timezone.utc))[0].year
    years = str(COPYRIGHT_START) if year <= COPYRIGHT_START else f"{COPYRIGHT_START}–{year}"
    return f"© {years} {COPYRIGHT_HOLDER}"


def fmt_stamp(iso: str, now: dt.datetime) -> str:
    """Short Eastern-time stamp for the footer: 'Oct 27, 11:48 PM', with the year only when it isn't this year."""
    local, _ = to_eastern(dt.datetime.fromisoformat(iso))
    hour = local.hour % 12 or 12
    year = f", {local.year}" if local.year != to_eastern(now)[0].year else ""
    return f"{MONTHS[local.month - 1][:3]} {local.day}{year}, {hour}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"


def read_checked(path: Path | None) -> str | None:
    """The time of the last successful scrape, or None when there is no (valid) record of one.
    A build without a recorded check (a PR check, or a run where every sport failed) simply
    leaves 'Last checked' off the page instead of showing the build time."""
    if path is None:
        return None
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
        stamp = dt.datetime.fromisoformat(text)
    except (OSError, ValueError):
        return None
    return text if stamp.tzinfo is not None else None


def streak_text(code: str, noun: str = "") -> str:
    """`noun` (a sport's own word, "meets" or "matches") follows "straight": "has won 3 straight meets"."""
    kind, n = STREAK_RE.match(code).groups()
    n = int(n)
    tail = f" {noun}" if noun else ""
    if kind == "W":
        return "DePauw won the last meeting" if n == 1 else f"DePauw has won {n} straight{tail}"
    if kind == "L":
        return "Wabash won the last meeting" if n == 1 else f"Wabash has won {n} straight{tail}"
    return "The last meeting ended in a tie" if n == 1 else f"The last {n} meetings ended in ties"


def derive_streak(games: list) -> str:
    last = games[-1]["result"]
    n = 0
    for g in reversed(games):
        if g["result"] != last:
            break
        n += 1
    return f"{last}{n}"


def score_text(g: dict) -> str:
    """Winner first: 'DePauw 41, Wabash 20', 'Wabash 24, DePauw 17', 'Tie 7–7'."""
    if not facts.has_score(g):
        return {"W": "DePauw won", "L": "Wabash won", "T": "Tie"}[g["result"]] + " · score not recorded"
    d, w = fmt_num(g["depauw"]), fmt_num(g["wabash"])
    if g["result"] == "W":
        return f"DePauw {d}, Wabash {w}"
    if g["result"] == "L":
        return f"Wabash {w}, DePauw {d}"
    return f"Tie {d}–{w}"


def where_text(g: dict) -> str:
    label = {"home": "Home", "away": "Away", "neutral": "Neutral"}.get(g["location"], "Site unknown")
    site = places.ap_place(g.get("site")) or {"home": HOME_SITE, "away": AWAY_SITE}.get(g["location"])
    return f"{label} · {site}" if site else label


WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def fmt_when(g: dict, today: dt.date) -> str:
    """'Sat, Nov 14 · 1:07 PM'. The year appears only when the game is not in this year."""
    d = dt.date.fromisoformat(g["date"])
    year = f", {d.year}" if d.year != today.year else ""
    day = f"{WEEKDAYS[d.weekday()]}, {MONTHS[d.month - 1][:3]} {d.day}{year}"
    if g.get("status") == "postponed":
        return f"{day} · Postponed"
    return f"{day} · {g.get('time') or g.get('time_note') or 'Time TBA'}"


def meeting_site(g: dict) -> str:
    site = places.ap_place(g.get("site")) or {"home": HOME_SITE, "away": AWAY_SITE}.get(g.get("location"))
    return site or ("Neutral site" if g.get("location") == "neutral" else "Site TBA")


def join_and(items: list) -> str:
    """'a', 'a and b', 'a, b, and c': a serial comma whenever there are three or more items."""
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


def fmt_when_group(games: list, today: dt.date) -> str:
    """fmt_when for one meeting, which may be several games on one date:
    'Wed, Apr 28, 2027 · Doubleheader · 12:00 PM and 3:30 PM'. A game without a time says TBA."""
    first = games[0]
    if len(games) == 1 or first.get("status") == "postponed":
        return fmt_when(first, today)
    day = fmt_when({**first, "time": "x"}, today).split(" · ")[0]
    label = "Doubleheader" if len(games) == 2 else f"{len(games)} games"
    return f"{day} · {label} · {join_and([g.get('time') or 'TBA' for g in games])}"


def meeting_html(games: list, today: dt.date) -> str:
    """The next meeting as HTML. `games` is one meeting's games (meetings.next_group), all on one date."""
    sites = list(dict.fromkeys(meeting_site(g) for g in games))
    html = (f'<time datetime="{esc(games[0]["date"])}">{esc(fmt_when_group(games, today))}</time>'
            f' · {esc(" / ".join(sites))}')
    if all(g.get("source") == "wabash" for g in games):
        html += ' <span class="date-note">(from Wabash’s schedule)</span>'
    return html


def fmt_note(note) -> str:
    """Tidy the source's overtime notes: "(1ot)" -> "1 OT", "2OT" -> "2 OT", "(7 inn.)" -> "7 inn."."""
    text = str(note or "").strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    return re.sub(r"(?i)\b(\d+)\s*ot\b", r"\1 OT", text)


# --------------------------------------------------------------------------- #
# Loading and validation
# --------------------------------------------------------------------------- #

GAME_FIELDS = ("date", "location", "result", "depauw", "wabash")


def load(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DataError(f"{path} not found") from None
    except json.JSONDecodeError as exc:
        raise DataError(f"{path} is not valid JSON: {exc}") from None

    if not isinstance(data, dict):
        raise DataError("data file is not a JSON object")
    sports = data.get("sports")
    if not isinstance(sports, dict) or not sports:
        raise DataError("data file has no sports")
    totals = data.get("totals")
    if not isinstance(totals, dict):
        raise DataError("data file has no totals")
    try:
        dt.datetime.fromisoformat(str(data.get("updated_at")))
    except ValueError:
        raise DataError("data file has no valid updated_at timestamp") from None

    sums = {"depauw_wins": 0, "wabash_wins": 0, "ties": 0, "games_played": 0}
    for key, s in sports.items():
        if not KEY_RE.match(str(key)):
            raise DataError(f"unsafe sport key {key!r}")
        games = s.get("games") if isinstance(s, dict) else None
        if not games:
            raise DataError(f"{key}: no games")
        for g in games:
            required = GAME_FIELDS if g.get("score_known", True) else ("date", "location", "result")
            missing = [f for f in required if g.get(f) is None]
            if missing:
                raise DataError(f"{key}: game {g.get('date')} is missing {missing}")
            if g["result"] not in ("W", "L", "T"):
                raise DataError(f"{key}: game {g['date']} has result {g['result']!r}")
            try:
                dt.date.fromisoformat(g["date"])
            except ValueError:
                raise DataError(f"{key}: bad date {g['date']!r}") from None
        for field in ("name", "source_url", "record", "games_played", "points"):
            if s.get(field) in (None, ""):
                raise DataError(f"{key}: missing {field}")
        if s["games_played"] != len(games):
            raise DataError(f"{key}: games_played {s['games_played']} but {len(games)} games listed")
        rec = s["record"]
        by_result = {r: sum(1 for g in games if g["result"] == r) for r in "WLT"}
        if (rec["depauw_wins"], rec["wabash_wins"], rec["ties"]) != (by_result["W"], by_result["L"], by_result["T"]):
            raise DataError(f"{key}: record does not match the game list")
        sums["depauw_wins"] += rec["depauw_wins"]
        sums["wabash_wins"] += rec["wabash_wins"]
        sums["ties"] += rec["ties"]
        sums["games_played"] += s["games_played"]
    if {k: totals.get(k) for k in sums} != sums:
        raise DataError("totals do not match the sum of the sports")
    return data


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #

# Icon files written to the site root (see build()): link tag attributes and the manifest entries.
THEME_LIGHT, THEME_DARK = "#111111", "#000000"   # the header band's color in each mode
BACKGROUND = "#f6f4ee"                           # the page's light-mode background


def preview_info(data: dict, label: str | None = None) -> dict:
    """What the share card shows, all from the data: the totals and the current Monon Bell holder. The
    image URL carries a version that changes only when those do, so chat apps refetch a new picture."""
    t = data["totals"]
    football = data["sports"].get("football")
    bell = facts.bell(football["games"]) if football else None
    holder = bell["holder"] if bell else None
    alt = (f"DePauw vs. Wabash all-time totals: DePauw {t['depauw_wins']:,} wins, "
           f"Wabash {t['wabash_wins']:,} wins" + (f", {plural(t['ties'], 'tie')}" if t["ties"] else "") + "."
           + (f" {holder} holds the Monon Bell." if holder else "") + (f" {label}." if label else ""))
    return {"image": f"{SITE_URL}og-image.png?v={images.og_version(t, holder, label)}", "alt": alt,
            "holder": holder, "label": label}


def alumni_preview_info(endowment: dict) -> dict:
    """The alumni share card shows the endowment per student for each school (no overall alumni score). The
    image URL carries a version that changes only when a drawn figure does."""
    per = alumni_page.per_student(endowment)
    even = alumni_page.about_even(endowment)
    values = [("DePauw", f"${per['depauw']:,}"), ("Wabash", f"${per['wabash']:,}")]
    alt = (f"DePauw vs. Wabash alumni and endowment: endowment per student is {values[0][1]} at DePauw "
           f"and {values[1][1]} at Wabash on the same basis" + (", about even." if even else "."))
    return {"image": f"{SITE_URL}alumni-og-image.png?v={images.alumni_og_version(values, even)}", "alt": alt,
            "values": values, "even": even}


MANIFEST_DESCRIPTION = ("DePauw vs. Wabash, side by side: head-to-head athletics records, notable graduates and "
                        "endowments.")
NUMBER_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine",
                10: "ten", 11: "eleven", 12: "twelve"}


def alumni_description(data: dict) -> str:
    """The alumni page's meta description; the category count is read from the data."""
    n = len(data["categories"])
    return (f"Notable DePauw and Wabash graduates in {NUMBER_WORDS.get(n, str(n))} award and public-service "
            f"categories, plus endowment per student, with a source for every name.")


def render_manifest() -> str:
    icons = [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png"},
             {"src": "icon-512.png", "sizes": "512x512", "type": "image/png"},
             {"src": "icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}]
    return json.dumps({"name": "DePauw vs. Wabash", "short_name": "DePauw v Wabash",
                       "description": MANIFEST_DESCRIPTION, "start_url": BASE_PATH, "scope": BASE_PATH,
                       "display": "browser", "theme_color": THEME_LIGHT, "background_color": BACKGROUND,
                       "icons": icons}, indent=2) + "\n"


def head(title: str, description: str, *, prefix: str, canonical: str | None, noindex: bool = False,
         preview: dict | None = None, easter_eggs: bool = False) -> str:
    """preview is the share-card image and alt text; easter_eggs opts in to the home-only script and CSP rule."""
    policy = CSP + "; script-src 'self'" if easter_eggs else CSP
    lines = [
        '<!DOCTYPE html>',
        '<html lang="en">',
        '<head>',
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f'<meta http-equiv="Content-Security-Policy" content="{esc(policy)}">',
        '<meta name="referrer" content="strict-origin-when-cross-origin">',
        f'<title>{esc(title)}</title>',
        f'<meta name="description" content="{esc(description)}">',
        '<meta name="color-scheme" content="light dark">',
        f'<meta name="theme-color" content="{THEME_LIGHT}" media="(prefers-color-scheme: light)">',
        f'<meta name="theme-color" content="{THEME_DARK}" media="(prefers-color-scheme: dark)">',
    ]
    if noindex:
        lines.append('<meta name="robots" content="noindex">')
    if canonical:
        lines += [
            f'<link rel="canonical" href="{esc(canonical)}">',
            '<meta property="og:type" content="website">',
            '<meta property="og:site_name" content="DePauw vs. Wabash">',
            f'<meta property="og:title" content="{esc(title)}">',
            f'<meta property="og:description" content="{esc(description)}">',
            f'<meta property="og:url" content="{esc(canonical)}">',
        ]
    if canonical and preview:
        lines += [
            f'<meta property="og:image" content="{esc(preview["image"])}">',
            '<meta property="og:image:type" content="image/png">',
            f'<meta property="og:image:width" content="{images.OG_SIZE[0]}">',
            f'<meta property="og:image:height" content="{images.OG_SIZE[1]}">',
            f'<meta property="og:image:alt" content="{esc(preview["alt"])}">',
            '<meta name="twitter:card" content="summary_large_image">',
            f'<meta name="twitter:title" content="{esc(title)}">',
            f'<meta name="twitter:description" content="{esc(description)}">',
            f'<meta name="twitter:image" content="{esc(preview["image"])}">',
            f'<meta name="twitter:image:alt" content="{esc(preview["alt"])}">',
        ]
    elif canonical:
        lines.append('<meta name="twitter:card" content="summary">')
    lines += [
        f'<link rel="icon" href="{esc(prefix)}favicon.svg" type="image/svg+xml">',
        f'<link rel="icon" href="{esc(prefix)}favicon-32.png" type="image/png" sizes="32x32">',
        f'<link rel="apple-touch-icon" href="{esc(prefix)}apple-touch-icon.png" sizes="180x180">',
        f'<link rel="manifest" href="{esc(prefix)}site.webmanifest">',
        f'<link rel="stylesheet" href="{esc(prefix)}styles.css">',
        *(['<script src="easter-eggs.js" defer></script>'] if easter_eggs else []),
        '</head>',
    ]
    return "\n".join(lines)


def heading(level: int, anchor: str, text: str) -> str:
    """A heading whose text is the permalink to its own section, so a visitor can copy a direct link
    without devtools. The link carries the heading's id, so the section is announced as just 'Football'."""
    return f'<h{level}><a class="permalink" id="{esc(anchor)}-h" href="#{esc(anchor)}">{esc(text)}</a></h{level}>'


def tiles(depauw: int, wabash: int, ties: int, css_class: str = "") -> str:
    def tile(cls, name, n, label):
        return (f'<div class="tile {cls}"><span class="tile-name">{esc(name)}</span>'
                f'<span class="tile-num">{n:,}</span><span class="tile-label">{esc(label)}</span></div>')
    parts = [tile("tile-depauw", "DePauw", depauw, "wins" if depauw != 1 else "win"),
             tile("tile-wabash", "Wabash", wabash, "wins" if wabash != 1 else "win")]
    if ties:
        parts.append(tile("tile-tie", "Ties", ties, "tie" if ties == 1 else "ties"))
    classes = f"tiles {css_class}" if css_class else "tiles"
    return f'<div class="{classes}">' + "".join(parts) + "</div>"


UNITS = {"points": ("point", "points"), "runs": ("run", "runs"), "goals": ("goal", "goals"),
         "individual matches": ("match", "matches")}


def unit(n, label: str) -> str:
    one, many = UNITS.get(label, (label, label))
    return f"{fmt_num(n)} {one if n == 1 else many}"


def year_span(first: str, last: str) -> str:
    return first if first == last else f"{first}–{last}"


def game_line(g: dict) -> str:
    return f"{score_text(g)} · {date_text(g)}"


def times_text(n: int) -> str:
    return "twice" if n == 2 else f"{n} times"


def repeat_text(found: list) -> str:
    """" (36 times, most recently Apr 24, 2026)" for a record several games share, else nothing. The date is
    the newest game that has one; games with no recorded date never count as the most recent."""
    if len(found) < 2:
        return ""
    return f" ({times_text(len(found))}, most recently {date_text(facts.most_recent(found))})"


def fun_facts(key: str, s: dict) -> list:
    """[(label, text)] worked out from the game list. A fact that makes no sense for the sport is left out."""
    games, label = s["games"], s.get("total_label") or "points"
    out = []
    for who, result in (("DePauw", "W"), ("Wabash", "L")):
        g = facts.biggest_win(games, result)
        if g:
            found = facts.biggest_wins(games, result)
            lead = score_text(g) if len(found) > 1 else game_line(g)
            out.append((f"Biggest {who} win", f"{lead} · by {unit(facts.margin(g), label)}{repeat_text(found)}"))
    for who, result in (("DePauw", "W"), ("Wabash", "L")):
        run = facts.longest_streak(games, result)
        if run:
            span = year_span(run["first"], run["last"])
            text = (f"{run['games']} straight ({times_text(run['times'])}, most recently {span})"
                    if run["times"] > 1 else f"{run['games']} straight · {span}")
            out.append((f"Longest {who} win streak", text))
    close = facts.one_score_games(key, games)
    if close:
        limit = facts.ONE_SCORE[key]
        by = f"by {unit(limit, label)}" + (" or fewer" if limit > 1 else "")
        out.append((f"{facts.CLOSE_WORD.get(key, 'One-score')} {game_words(s)[1]}",
                    f"{len(close):,} decided {by} · most recent: {game_line(facts.most_recent(close))}"))
    # nine matches are always played, so a tennis total never varies
    tops = facts.highest_scoring_games(games) if label != "individual matches" else []
    if tops:
        top = facts.most_recent(tops)
        lead = score_text(top) if len(tops) > 1 else game_line(top)
        out.append((f"Highest-scoring {game_words(s)[0]}",
                    f"{lead} · {unit(top['depauw'] + top['wabash'], label)} combined{repeat_text(tops)}"))
    for title, found in (("Overtime games", facts.overtime_games(games)),
                         ("Extra-inning games", facts.extra_inning_games(games))):
        if found:
            last = facts.most_recent(found)
            out.append((title, f"{len(found):,} · most recent: {game_line(last)} ({fmt_note(last.get('note'))})"))
    return out


def fun_facts_html(items: list) -> str:
    if not items:
        return ""
    lis = "\n".join(f'<li><span class="fun-label">{esc(k)}:</span> {esc(v)}</li>' for k, v in items)
    return f'<div class="fun-facts">\n<h3>Fun facts</h3>\n<ul>\n{lis}\n</ul>\n</div>'


def series_html(rows: list) -> str:
    if not rows:
        return ""
    lis = "\n".join(f'<li><strong>{esc(season)}:</strong> {esc(text)}</li>' for season, text in rows)
    return f'<h3 class="series-h">Season by season</h3>\n<ul class="series">\n{lis}\n</ul>\n'


# A note on one game, shown beside it in the list. Keyed by sport and date; it only appears while
# both schools still list the game the same way, so it never outlives the facts it states.
FOOTNOTES = {("mens-basketball", "1954-12-04"): "Listed as a tie by both schools."}


def game_flags(key: str, g: dict, merged: bool) -> list:
    """Short notes for one row: whose record it comes from, a disputed score, a date mismatch."""
    flags = []
    if merged and g.get("source") == "wabash":
        flags.append("Wabash record only")
    elif merged and g.get("source") == "depauw":
        flags.append("DePauw record only")
    if g.get("date_differs") and g.get("wabash_date"):
        flags.append(f"Wabash’s record lists this game on {fmt_date(g['wabash_date'])}")
    if g.get("date_differs") and g.get("depauw_date"):
        flags.append(f"DePauw’s record lists this game on {fmt_date(g['depauw_date'])}")
    v = g.get("wabash_version")
    if g.get("disputed") and v:
        flags.append(f"DePauw’s record (DePauw-Wabash): {fmt_num(g['depauw'])}-{fmt_num(g['wabash'])} · "
                     f"Wabash’s record (DePauw-Wabash): {fmt_num(v['depauw'])}-{fmt_num(v['wabash'])}")
    if g.get("score_known") is False:
        flags.append("Score not recorded; the result counts in the record but not in the point totals.")
    if g.get("score_order_reversed"):
        who = join_and([f"{n}’s" for n in g["score_order_reversed"]])
        flags.append(f"The two scores were listed in the opposite order on {who} page; the result "
                     f"(win or loss) was trusted and the scores were swapped to match it.")
    if g.get("sessions"):
        days = join_and([f"{fmt_date(x['date'])} (DePauw {fmt_num(x['depauw'])}, Wabash {fmt_num(x['wabash'])})"
                         for x in g["sessions"]])
        flags.append(f"One meet over several days. Wabash’s record also lists {days} as separate games; "
                     f"they are running scores of this meet, so they aren’t counted.")
    note = FOOTNOTES.get((key, g["date"]))
    if note and g["result"] == "T" and g.get("source") == "both" and not g.get("disputed"):
        flags.append(note)
    return flags


def result_html(g: dict) -> str:
    return esc(score_text(g))      # DePauw's version, the one that counts; a dispute is spelled out in the notes


def games_table(key: str, games: list, words: tuple = ("game", "games"), merged: bool = False) -> str:
    rows = []
    # games arrive oldest first (doubleheaders in playing order), so reverse for newest first
    for g in reversed(games):
        winner = {"W": "win-depauw", "L": "win-wabash", "T": "tied"}[g["result"]]
        box = safe_url(g.get("boxscore"))
        box_html = f'<a href="{esc(box)}">Box score</a>' if box else ""
        notes = [esc(fmt_note(g.get("note")))] if g.get("note") else []
        notes += [f'<span class="game-flag">{esc(f)}</span>' for f in game_flags(key, g, merged)]
        rows.append(
            "<tr>"
            f'<th scope="row" data-label="Date">{date_html(g)}</th>'
            f'<td data-label="Site">{esc(where_text(g))}</td>'
            f'<td data-label="Result" class="{winner}">{result_html(g)}</td>'
            f'<td data-label="Notes">{"<br>".join(notes)}</td>'
            f'<td data-label="Box score">{box_html}</td>'
            "</tr>"
        )
    return (
        f'<details class="games">\n<summary>All {count_noun(len(games), words)}</summary>\n'
        f'{series_html(facts.season_series(games))}'
        '<p class="hint">Newest first. Home and away are from DePauw’s point of view.</p>\n'
        '<table>\n'
        f'<caption class="sr-only">Every {esc(key.replace("-", " "))} {esc(words[0])}, newest first</caption>\n'
        '<thead><tr><th scope="col">Date</th><th scope="col">Site</th><th scope="col">Result</th>'
        '<th scope="col">Notes</th><th scope="col">Box score</th></tr></thead>\n'
        '<tbody>\n' + "\n".join(rows) + '\n</tbody>\n</table>\n'
        + ('<p class="history-reward">You’ve reached the bottom. Wabash jokes practically write themselves.</p>\n'
           if games else '') + '</details>'
    )


def bell_held_text(b: dict) -> str:
    wins, ties = b["held_wins"], b["held_ties"]
    if not ties:
        return f"{wins} straight wins" if wins > 1 else "1 win"
    return f'{plural(b["held_games"], "game")}: {plural(wins, "win")} and {plural(ties, "tie")}'


def bell_block(games: list) -> str:
    """The Monon Bell section of the football card: holder, Bell-era record and short history. The last and
    next games are the football card's own "Most recent" and "Next meeting" stats."""
    b = facts.bell(games)
    if b is None:
        return ""
    rows = []
    if b["holder"]:
        rows.append(f'<p class="bell-holder"><strong>{esc(b["holder"])} has held the Bell since {b["since"]}</strong>'
                    f' <span class="bell-note">({esc(bell_held_text(b))})</span></p>')
    record = tiles(b["depauw"], b["wabash"], b["ties"], "tiles-small")
    return "\n".join([
        '<section class="bell" id="monon-bell" aria-labelledby="monon-bell-h">',
        heading(3, "monon-bell", "The Monon Bell"),
        *rows,
        f'<dl class="facts bell-facts">\n<div><dt>Bell record since {facts.BELL_START}</dt><dd>{record}</dd></div>\n</dl>',
        f'<p class="bell-history">{esc(BELL_HISTORY)} <a href="{esc(BELL_URL)}">Read more on Wikipedia</a></p>',
        '<a class="bell-heist-link" href="bell-heist/" aria-label="A little Bell business">'
        '<img src="easter-bell.svg" width="28" height="28" alt=""></a>',
        '</section>',
    ])


def provenance_text(s: dict) -> str:
    """Where a sport's record comes from, when both schools' pages were merged."""
    c = s.get("sources")
    if not c:
        return ""
    words = game_words(s)
    if not (c["depauw_only"] or c["wabash_only"] or c["disputed"] or c["date_differs"] or c.get("session_rows")):
        return "DePauw and Wabash records agree."
    parts = [f"{count_noun(c['both'], words)} listed by both", f"{c['wabash_only']:,} by Wabash only",
             f"{c['depauw_only']:,} by DePauw only", f"{c['disputed']:,} disputed"]
    text = "Combined from DePauw and Wabash records: " + ", ".join(parts) + "."
    if c["date_differs"]:
        n = c["date_differs"]
        text += (f" {n:,} of the shared {words[1]} have different dates in the two records." if n != 1 else
                 f" 1 of the shared {words[1]} has a different date in the two records.")
    if rows := c.get("session_rows"):       # running scores a ruling set aside (see session_of in overrides.json)
        text += (f" {plural(rows, 'row')} listed as separate {words[1]} {'is' if rows == 1 else 'are'} the running "
                 f"score of a multi-day {words[0]} already counted once, so {'it is' if rows == 1 else 'they are'} "
                 f"not counted.")
    return text


def page_links(s: dict, up: dict | None) -> list:
    """[(label, url)] for both schools' history and schedule pages that this sport has."""
    up = up or {}
    pairs = [("DePauw history", s.get("source_url")), ("Wabash history", s.get("wabash_source_url")),
             ("DePauw schedule", up.get("schedule_url")), ("Wabash schedule", up.get("wabash_schedule_url"))]
    return [(label, u) for label, raw in pairs if raw and (u := safe_url(raw))]


def card(key: str, s: dict, ahead: list, pending: list, up: dict | None, today: dt.date) -> str:
    games = s["games"]               # stored oldest first; games with no usable date come first in their season
    rec = s["record"]
    code = s.get("current_streak")
    if not (isinstance(code, str) and STREAK_RE.match(code)):
        code = derive_streak(games)
    last = facts.most_recent(games)
    words = game_words(s)
    since = facts.game_year(games[0])
    stat_rows = [
        ("Current streak", esc(streak_text(code, words[1] if s.get("unit") else ""))),
        ("Records since", esc(since)),
        (f"{words[1].capitalize()} played", esc(f"{len(games):,}")),
        (f"Total {s.get('total_label') or 'points'}",
         f'DePauw {fmt_num(s["points"]["depauw"])}, Wabash {fmt_num(s["points"]["wabash"])}'),
        ("Most recent",
         f'{date_html(last)} · {esc(score_text(last))}'),
    ]
    if ahead:
        stat_rows.append(("Next meeting", meeting_html(meetings.next_group(ahead), today)))
    if pending:
        day = fmt_when(pending[0], today).split(" · ")[0]
        stat_rows.append(("Result pending", esc(day) + " · not on the history page yet"))
    dl = "\n".join(f'<div><dt>{esc(k)}</dt><dd>{v}</dd></div>' for k, v in stat_rows)
    merged = bool(s.get("sources"))
    links = [f'<a href="{esc(u)}">{esc(label)}</a>' for label, u in page_links(s, up)]
    links.append(report_link(s["name"]))
    provenance = provenance_text(s)
    source = (f'<p class="provenance">{esc(provenance)}</p>\n' if provenance else "") + \
             f'<p class="source">Sources: {" · ".join(links)}</p>'
    return (
        f'<article class="sport" id="{esc(key)}" aria-labelledby="{esc(key)}-h">\n'
        f'{heading(2, key, s["name"])}\n'
        f'{tiles(rec["depauw_wins"], rec["wabash_wins"], rec["ties"])}\n'
        f'<dl class="facts">\n{dl}\n</dl>\n{source}\n'
        f'{bell_block(games) if key == "football" else ""}\n'
        f'{fun_facts_html(fun_facts(key, s))}\n'
        f'{games_table(key, games, words, merged)}\n</article>'
    )


def updated_line(updated: str, checked: str | None, now: dt.datetime) -> str:
    def stamp(iso):
        return f'<time datetime="{esc(iso)}">{esc(fmt_stamp(iso, now))}</time>'
    parts = [f"Data last changed {stamp(updated)}"]
    if checked:
        parts.append(f"Last checked {stamp(checked)}")
    return f'<p>{" · ".join(parts)} (Eastern time).</p>'


def next_meeting_section(per_sport: dict, pending: dict, sports: dict, today: dt.date) -> str:
    """The soonest meeting across all sports, near the top. Empty string (no box at all) when there is none."""
    best = meetings.next_overall(per_sport)
    lines = []
    if best:
        key, g = best
        rest = per_sport[key][per_sport[key].index(g):]      # starts at the game picked, past any postponed one
        lines.append(f'<p class="next-line"><strong>{esc(sports[key]["name"])}</strong>, '
                     f'{meeting_html(meetings.next_group(rest), today)}</p>')
    for key, games in pending.items():
        if games:
            lines.append(f'<p class="next-pending">{esc(sports[key]["name"])}: result pending</p>')
    if not lines:
        return ""
    return "\n".join(['<section class="next-meeting" id="next-meeting" aria-labelledby="next-meeting-h">',
                      heading(2, "next-meeting", "Next meeting"), *lines, '</section>'])


def sport_word(name: str) -> str:
    """"Men's Basketball" -> "basketball": the short, lower-case name used inside a sentence."""
    return re.sub(r"^Men[’']s\s+", "", name).lower()


def series_summary(sports: dict) -> str:
    """One sentence on who leads which sports, from the merged records (never hardcoded).

    A sport is led by whoever has more wins; an even sport is listed on its own ("Soccer is even."). Sports
    keep the page order (sources.json), and each school's clause comes in the order of its first sport.
    When the football card has a Monon Bell era (facts.bell), its leader is named too: appended to the
    football leader's clause as "including the Monon Bell era" when the same school leads it, as "though X
    leads the Monon Bell era" (or "...is even") when not, and as a sentence of its own when football itself
    is even."""
    leads: dict = {"DePauw": [], "Wabash": []}
    even = []
    for key, s in sports.items():
        r = s["record"]
        if r["depauw_wins"] == r["wabash_wins"]:
            if s["games"]:
                even.append(key)
        else:
            leads["DePauw" if r["depauw_wins"] > r["wabash_wins"] else "Wabash"].append(key)

    bell = facts.bell(sports["football"]["games"]) if "football" in sports else None
    bell_leader = None
    if bell:
        bell_leader = "DePauw" if bell["depauw"] > bell["wabash"] else "Wabash" if bell["wabash"] > bell["depauw"] else "even"

    order = list(sports)
    clauses = []
    for school in sorted((k for k in leads if leads[k]), key=lambda k: order.index(leads[k][0])):
        other = "Wabash" if school == "DePauw" else "DePauw"
        text = f"{school} leads {join_and([sport_word(sports[k]['name']) for k in leads[school]])}"
        if bell_leader and "football" in leads[school]:
            if bell_leader == school:
                text += ", including the Monon Bell era"
            elif bell_leader == other:
                text += f", though {other} leads the Monon Bell era"
            else:
                text += ", though the Monon Bell era is even"
        clauses.append(text)
    sentences = ["; ".join(clauses) + "."] if clauses else []
    if even:
        names = [sport_word(sports[k]["name"]) for k in even]
        sentences.append(f"{join_and(names).capitalize()} {'is' if len(names) == 1 else 'are'} even.")
    if bell_leader and "football" not in leads["DePauw"] + leads["Wabash"]:
        sentences.append("The Monon Bell era is even." if bell_leader == "even"
                         else f"{bell_leader} leads the Monon Bell era.")
    return " ".join(sentences)


def game_day_text(e: dict, sports: dict, several: bool) -> str:
    """One banner line for a sport playing today. `several` is true when the banner covers more than one
    sport, which is when a final score needs the sport's name to be understood."""
    name = e["name"]
    if e["phase"] == gameday.FINAL:
        scores = " and ".join(score_text(g) for g in e["results"])
        return f"Final, {name}: {scores}" if several else f"Final: {scores}"
    if e["phase"] == gameday.UNDERWAY:
        return (f"{name} vs. Wabash is underway or just finished. "
                "The result will appear here soon after it's posted.")
    games = e["games"]
    sites = " / ".join(dict.fromkeys(meeting_site(g) for g in games))
    if len(games) == 1:
        when = games[0].get("time") or "time TBA"
        what = ""
    else:
        when = join_and([g.get("time") or "TBA" for g in games])
        what = " (doubleheader)" if len(games) == 2 else f" ({len(games)} games)"
    return f"Game day: {name} vs. Wabash today{what}, {when} · {sites}"


def bell_holder_sentence(e: dict, sports: dict) -> str:
    """After a football final, who holds the Monon Bell now ("" for any other sport)."""
    if e["key"] != "football":
        return ""
    b = facts.bell(sports["football"]["games"])
    return f"{b['holder']} holds the Monon Bell." if b and b["holder"] else ""


def game_day_html(entries: list, sports: dict) -> str:
    """The game-day banner above everything else; empty (nothing at all) when no game is being played today.
    A final score links to its sport's card."""
    if not entries:
        return ""
    several = len(entries) > 1
    lines = []
    for e in entries:
        text = esc(game_day_text(e, sports, several))
        if e["phase"] == gameday.FINAL:
            bell = bell_holder_sentence(e, sports)
            text = f'<a href="#{esc(e["key"])}">{text}</a>' + (f" · {esc(bell)}" if bell else "")
        lines.append(f'<p class="game-day-line">{text}</p>')
    return "\n".join(['<section class="game-day" id="game-day" aria-label="Game day">', *lines, '</section>'])


def game_day_label(entries: list) -> str | None:
    """The small line on the share image while the banner shows: "Final" once every game today has its
    result, "Game day" until then, nothing when there is no banner."""
    if not entries:
        return None
    return "Final" if all(e["phase"] == gameday.FINAL for e in entries) else "Game day"


def footer_source(key: str, s: dict, up: dict | None) -> str:
    links = page_links(s, up)
    if not s.get("wabash_source_url"):                  # one school's page only: the older, shorter form
        first = safe_url(s["source_url"])
        return f'<li><a href="{esc(first)}">{esc(s["name"])}</a></li>' if first else ""
    return (f'<li>{esc(s["name"])}: '
            + " · ".join(f'<a href="{esc(u)}">{esc(label)}</a>' for label, u in links) + '</li>')


def athletics_footer_block(now: dt.datetime, prefix: str, data: dict, checked: str | None,
                           upcoming: dict | None) -> list:
    """The athletics part of the footer: update and check times, downloads, the error report, the sources."""
    upcoming = upcoming or {}
    sources = "\n".join(footer_source(k, s, upcoming.get(k)) for k, s in data["sports"].items())
    return [
        updated_line(data["updated_at"], checked, now),
        f'<p class="downloads">Download the data: <a href="{esc(prefix)}data/series.json">series.json</a> '
        f'(every game, as JSON) · <a href="{esc(prefix)}games.csv">games.csv</a> (one row per game, for '
        f'spreadsheets). {report_link()}.</p>',
        '<p>Data from <a href="https://depauwtigers.com/">depauwtigers.com</a> and '
        '<a href="https://sports.wabash.edu/">sports.wabash.edu</a>. Source pages:</p>',
        f'<ul class="sources">\n{sources}\n</ul>',
    ]


def alumni_verified_date(alumni_data: dict, endowment: dict) -> str:
    """The newest verified_at of the two curated files: the footer's date and the sitemap's lastmod."""
    return max(alumni_data["verified_at"], endowment["verified_at"])


def alumni_footer_block(prefix: str, alumni_data: dict, endowment: dict) -> list:
    """The alumni part of the footer: when the records were last verified, downloads, the correction form
    and the sources (the two audited statements and the two College Navigator pages)."""
    verified = alumni_verified_date(alumni_data, endowment)
    fy = endowment["fiscal_year"]
    sources = "\n".join(
        f'<li>{esc(name)}: <a href="{esc(endowment["schools"][key]["source_url"])}">{esc(fy)} audited financial '
        f'statements</a> · <a href="{esc(endowment["schools"][key]["enrollment"]["source_url"])}">College '
        f'Navigator (IPEDS)</a></li>' for key, name in alumni_page.SCHOOLS.items())
    return [
        f'<p>Records last verified <time datetime="{esc(verified)}">{esc(fmt_date(verified))}</time>.</p>',
        f'<p class="downloads">Download the data: <a href="{esc(prefix)}data/alumni.json">alumni.json</a> '
        f'(named graduates and their sources) · <a href="{esc(prefix)}data/endowment.json">endowment.json</a> '
        f'(endowment, enrollment and basis). <a href="{esc(alumni_page.correction_url())}">Suggest a '
        f'correction</a>.</p>',
        f'<p>Data from: DePauw and Wabash audited {esc(fy)} financial statements, the U.S. Department of '
        f'Education’s College Navigator (IPEDS), and the sources listed with each name. Source pages:</p>',
        f'<ul class="sources">\n{sources}\n</ul>',
    ]


def site_footer(now: dt.datetime, *, prefix: str = "", data: dict | None = None, checked: str | None = None,
                upcoming: dict | None = None, alumni: tuple | None = None, link_how: bool = True) -> list:
    """The footer of every page: one function with a page-specific data block and the same structure and
    lines after it. `prefix` reaches the site root from the page ("" on the home page, "../" one folder
    down, the root path on 404.html). With `data` the block is the athletics one, with `alumni` (the alumni
    and endowment data) the alumni one; with neither (404 and the how-it-works landing page) only the shared
    lines show. "How the numbers work" links to the matching page, and the men's head-to-head note shows
    only on the athletics pages (it is about athletics, so the alumni and the neutral pages leave it out). The
    how-it-works landing page passes link_how=False: a link from that page to itself would go nowhere."""
    block, how, men_note = [], HOW_PATH, False
    if alumni:
        block, how = alumni_footer_block(prefix, *alumni), HOW_ALUMNI_PATH
    elif data:
        block, how, men_note = athletics_footer_block(now, prefix, data, checked, upcoming), HOW_ATHLETICS_PATH, True
    return [
        '<footer class="site-footer">', '<div class="wrap">',
        *block,
        '<!-- Both schools supplied the records. Only one supplied the developers. -->',
        '<p class="footer-links">' + (f'<a href="{esc(prefix + how)}">How the numbers work</a> · ' if link_how else "") +
        f'<a href="{esc(REPO_URL)}">Built in the open: code and data on GitHub</a></p>',
        *([f'<p>{esc(MEN_ONLY)}</p>'] if men_note else []),
        f'<p>{esc(DISCLAIMER)}</p>',
        '<details class="impartiality"><summary>A note on impartiality</summary>'
        '<p>The data is independently sourced. The developers were educated correctly.</p></details>',
        f'<p class="copyright">{esc(copyright_line(now))}</p>',
        '</div>',
        '</footer>',
    ]


def site_nav(prefix: str, current: str) -> str:
    """Two real links, with only the current top-level destination marked current."""
    athletics_current = ' aria-current="page"' if current == "athletics" else ""
    alumni_current = ' aria-current="page"' if current == "alumni" else ""
    athletics_href = prefix or "./"
    return (f'<nav class="site-nav" aria-label="Main navigation">'
            f'<a href="{esc(athletics_href)}"{athletics_current}>Athletics</a> '
            f'<a href="{esc(prefix + ALUMNI_PATH)}"{alumni_current}>Alumni</a></nav>')


def render_index(data: dict, checked: str | None = None, now: dt.datetime | None = None,
                 upcoming: dict | None = None) -> str:
    t = data["totals"]
    sports = data["sports"]
    n_sports = len(sports)
    summary = f"DePauw {t['depauw_wins']:,} wins, Wabash {t['wabash_wins']:,}" + \
              (f", {plural(t['ties'], 'tie')}" if t["ties"] else "")
    description = (f"{SUBTITLE} {summary}, across {plural(t['games_played'], 'game')} "
                   f"in {plural(n_sports, 'sport')}.")
    updated = data["updated_at"]
    now = now or dt.datetime.now(dt.timezone.utc)
    today = to_eastern(now)[0].date()
    upcoming = upcoming or {}
    ahead, pending = {}, {}
    for key, s in sports.items():
        ahead[key], pending[key] = meetings.split_games(
            upcoming.get(key, {}).get("games", []), s["games"], today)

    today_games = gameday.todays_games(now, upcoming, sports)
    sports_cards = []
    paw_start = max(0, n_sports - 3)
    for i, (key, sport) in enumerate(sports.items()):
        sports_cards.append(card(key, sport, ahead[key], pending[key], upcoming.get(key), today))
        if i >= paw_start:
            side = "paw-left" if (i - paw_start) % 2 == 0 else "paw-right"
            sports_cards.append(f'<div class="paw-trail {side}" aria-hidden="true"><img src="easter-paw.svg" width="32" height="32" alt=""></div>')

    return "\n".join([
        head(TITLE, description, prefix="", canonical=SITE_URL,
             preview=preview_info(data, game_day_label(today_games)), easter_eggs=True),
        '<body>',
        '<header class="site-header">',
        '<div class="wrap">',
        '<h1 class="entrance-wrap"><span id="entrance-fallback">DePauw vs. Wabash</span>'
        '<button id="tiger-entrance" class="entrance-button" type="button" hidden aria-describedby="entrance-hint">'
        'DePauw vs. Wabash<span class="sr-only">, Tiger entrance</span></button></h1>',
        '<span id="entrance-hint" class="sr-only">Activate five times, with no speed requirement, to unlock a message.</span>',
        '<p id="entrance-status" class="entrance-status" role="status" aria-live="polite"></p>',
        site_nav("", "athletics"),
        f'<p class="subtitle">{esc(SUBTITLE)}</p>',
        '</div>',
        '</header>',
        '<main class="wrap">',
        game_day_html(today_games, sports),
        next_meeting_section(ahead, pending, sports, today),
        '<section class="overall" aria-labelledby="overall-h">',
        '<h2 id="overall-h">All head-to-head sports</h2>',
        tiles(t["depauw_wins"], t["wabash_wins"], t["ties"], "tiles-big"),
        f'<p class="overall-meta">{esc(plural(t["games_played"], "game"))} across {esc(plural(n_sports, "sport"))}</p>',
        f'<p class="series-summary">{esc(series_summary(sports))}</p>' if series_summary(sports) else '',
        f'<p class="not-included"><strong>Not included:</strong> {esc(NOT_INCLUDED)}</p>',
        '</section>',
        *sports_cards,
        '<details class="tiger-note"><summary><img src="easter-tiger.svg" width="80" height="80" alt="">'
        '<span>A tiger with something to say</span></summary><p>No further questions.</p></details>',
        '</main>',
        *site_footer(now, data=data, checked=checked, upcoming=upcoming),
        '</body>',
        '</html>',
        '',
    ])


def athletics_preview(data: dict, now: dt.datetime, upcoming: dict) -> dict:
    """The athletics share card (totals, Bell holder, game-day label) for any athletics-side page."""
    return preview_info(data, game_day_label(gameday.todays_games(now, upcoming, data["sports"])))


def how_page(title: str, description: str, *, prefix: str, path: str, current: str, preview: dict,
             back: tuple | list, body: list, footer: list, subtitle: str = SUBTITLE) -> str:
    """The shell the three how-it-works pages share: header, nav, back link, the body, a footer."""
    return "\n".join([
        head(title, description, prefix=prefix, canonical=SITE_URL + path, preview=preview),
        '<body>',
        '<header class="site-header">',
        '<div class="wrap">',
        '<h1>DePauw vs. Wabash</h1>',
        site_nav(prefix, current),
        f'<p class="subtitle">{esc(subtitle)}</p>',
        '</div>',
        '</header>',
        '<main class="wrap">',
        '<p class="back">' + " · ".join(f'<a href="{esc(href)}">{esc(label)}</a>' for href, label in
                                        ([back] if isinstance(back, tuple) else back)) + '</p>',
        *body,
        '</main>',
        *footer,
        '</body>',
        '</html>',
        '',
    ])


def render_how_athletics(data: dict, rules: dict, kept: int, checked: str | None = None,
                         now: dt.datetime | None = None, upcoming: dict | None = None) -> str:
    """/how-it-works/athletics/: the plain-language page about the records. Two folders down, so every URL
    it uses is relative to ../../."""
    now = now or dt.datetime.now(dt.timezone.utc)
    upcoming = upcoming or {}
    sports = data["sports"]
    sources = "\n".join(footer_source(k, s, upcoming.get(k)) for k, s in sports.items())
    t = data["totals"]
    description = (f"How the DePauw vs. Wabash records are built: {plural(t['games_played'], 'game')} in "
                   f"{plural(len(sports), 'sport')} from both schools’ records, with disputes and rulings shown openly.")
    return how_page(
        howitworks.TITLE, description, prefix="../../", path=HOW_ATHLETICS_PATH, current="athletics",
        preview=athletics_preview(data, now, upcoming), back=("../../", BACK_ATHLETICS),
        body=howitworks.content(data, rules, kept, sources_html=sources, not_included=NOT_INCLUDED, repo_url=REPO_URL),
        footer=site_footer(now, prefix="../../", data=data, checked=checked, upcoming=upcoming))


def render_how_alumni(alumni_data: dict, endowment: dict, now: dt.datetime | None = None) -> str:
    """/how-it-works/alumni/: who counts, how names were sourced, how the endowment per student is made."""
    now = now or dt.datetime.now(dt.timezone.utc)
    description = ("Who counts as a notable DePauw or Wabash graduate, how names were sourced and checked, and "
                   "how endowment per student is worked out on one common basis.")
    return how_page(
        howitworks.ALUMNI_TITLE, description, prefix="../../", path=HOW_ALUMNI_PATH, current="alumni",
        preview=alumni_preview_info(endowment), back=("../../alumni/", BACK_ALUMNI),
        body=howitworks.alumni_content(alumni_data, endowment, repo_url=REPO_URL),
        footer=site_footer(now, prefix="../../", alumni=(alumni_data, endowment)), subtitle=alumni_page.SUBTITLE)


def render_how_landing(data: dict, now: dt.datetime | None = None, upcoming: dict | None = None) -> str:
    """/how-it-works/: the address that was already shared, now a short page that points to the two."""
    now = now or dt.datetime.now(dt.timezone.utc)
    return how_page(
        howitworks.LANDING_TITLE, howitworks.LANDING_DESCRIPTION, prefix="../", path=HOW_PATH, current="",
        preview=athletics_preview(data, now, upcoming or {}),
        back=[("../", BACK_ATHLETICS), ("../alumni/", BACK_ALUMNI)],
        body=howitworks.landing_content(), footer=site_footer(now, prefix="../", link_how=False), subtitle=NEUTRAL_SUBTITLE)


def render_404(now: dt.datetime | None = None) -> str:
    # GitHub Pages serves this file for any missing path, including nested ones, so relative
    # URLs would break. It is the one page that uses root-relative URLs, built from BASE_PATH.
    return "\n".join([
        head("Page not found | DePauw vs. Wabash", "That page does not exist.",
             prefix=BASE_PATH, canonical=None, noindex=True),
        '<body>',
        '<header class="site-header"><div class="wrap"><h1>DePauw vs. Wabash</h1>',
        site_nav(BASE_PATH, ""),
        f'<p class="subtitle">{esc(NEUTRAL_SUBTITLE)}</p></div></header>',
        '<main class="wrap">',
        '<h2>Page not found</h2>',
        f'<p class="back"><a href="{esc(BASE_PATH)}">{esc(BACK_ATHLETICS)}</a> · '
        f'<a href="{esc(BASE_PATH + ALUMNI_PATH)}">{esc(BACK_ALUMNI)}</a></p>',
        '</main>',
        *site_footer(now or dt.datetime.now(dt.timezone.utc), prefix=BASE_PATH),
        '</body>',
        '</html>',
        '',
    ])


def render_bell_heist(now: dt.datetime | None = None) -> str:
    return "\n".join([
        head("Mission accomplished | DePauw vs. Wabash", "An entirely fictional Monon Bell mission.",
             prefix="../", canonical=None, noindex=True),
        '<body>',
        '<header class="site-header"><div class="wrap"><h1>DePauw vs. Wabash</h1>',
        site_nav("../", ""),
        f'<p class="subtitle">{esc(NEUTRAL_SUBTITLE)}</p></div></header>',
        '<main class="wrap prose bell-heist"><h2>Mission accomplished</h2>',
        '<p>You’ve successfully relocated the Monon Bell to Greencastle. Your alumni association has no knowledge of this operation.</p>',
        '<p>An entirely fictional mission.</p>',
        '<p><a href="../#monon-bell">Check the real Bell holder</a></p>',
        f'<p class="back"><a href="../">{esc(BACK_ATHLETICS)}</a> · <a href="../alumni/">{esc(BACK_ALUMNI)}</a></p>',
        '</main>', *site_footer(now or dt.datetime.now(dt.timezone.utc), prefix="../"),
        '</body>', '</html>', '',
    ])


def render_sitemap(data: dict, checked: str | None = None, alumni_verified: str | None = None) -> str:
    """lastmod is the Eastern date of the newest time the page states: when the data last changed or,
    when there is one, when they were last checked."""
    newest = max(dt.datetime.fromisoformat(s) for s in (data["updated_at"], checked) if s)
    lastmod = to_eastern(newest)[0].date().isoformat()
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f'  <url><loc>{esc(SITE_URL)}</loc><lastmod>{lastmod}</lastmod></url>\n'
            f'  <url><loc>{esc(SITE_URL + HOW_PATH)}</loc><lastmod>{lastmod}</lastmod></url>\n'
            f'  <url><loc>{esc(SITE_URL + HOW_ATHLETICS_PATH)}</loc><lastmod>{lastmod}</lastmod></url>\n'
            + (f'  <url><loc>{esc(SITE_URL + ALUMNI_PATH)}</loc><lastmod>{esc(alumni_verified)}</lastmod></url>\n'
               f'  <url><loc>{esc(SITE_URL + HOW_ALUMNI_PATH)}</loc><lastmod>{esc(alumni_verified)}</lastmod></url>\n'
               if alumni_verified else '') +
            '</urlset>\n')


CSV_COLUMNS = ["sport", "date", "season", "location", "site", "depauw_score", "wabash_score",
               "result", "note", "boxscore_url", "source", "date_recorded", "disputed", "date_differs",
               "wabash_record_date", "depauw_record_score", "wabash_record_score"]
RESULT_WORDS = {"W": "DePauw win", "L": "Wabash win", "T": "Tie"}


def csv_text(value) -> str:
    """Text for a spreadsheet cell. A value that starts with =, +, - or @ would run as a formula in
    Excel or Sheets, so it gets a leading apostrophe."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def fmt_csv_num(x) -> str:
    """A score for a spreadsheet: no thousands separator, halves as 147.5, whole scores as 147."""
    return str(facts.num(x))


def render_csv(data: dict) -> str:
    """One row per game, oldest first within each sport, in the order the sports appear on the page."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for s in data["sports"].values():
        for g in s["games"]:
            v = g.get("wabash_version")
            writer.writerow([csv_text(s["name"]), g["date"] if date_known(g) else "", csv_text(g.get("season")),
                             g["location"], csv_text(g.get("site")),
                             fmt_csv_num(g["depauw"]) if facts.has_score(g) else "",
                             fmt_csv_num(g["wabash"]) if facts.has_score(g) else "",
                             RESULT_WORDS[g["result"]], csv_text(fmt_note(g.get("note"))),
                             safe_url(g.get("boxscore")) or "", g.get("source", "depauw"),
                             "yes" if date_known(g) else "no", "yes" if g.get("disputed") else "no",
                             "yes" if g.get("date_differs") else "no", g.get("wabash_date") or "",
                             f"{fmt_csv_num(g['depauw'])}-{fmt_csv_num(g['wabash'])}" if g.get("disputed") and v else "",
                             f"{fmt_csv_num(v['depauw'])}-{fmt_csv_num(v['wabash'])}" if g.get("disputed") and v else ""])
    return buf.getvalue()


def build(data_path: Path, out: Path, *, checked_at: str | None = None,
          now: dt.datetime | None = None, upcoming_path: Path | None = None,
          overrides_path: Path | None = None, alumni_path: Path | None = None,
          endowment_path: Path | None = None) -> None:
    data = load(data_path)
    # Validate all curated input and render every page before removing the previous output.
    alumni_data = alumni_page.validate_alumni(alumni_page.load_json(alumni_path or ALUMNI_DATA, "alumni"))
    endowment_data = alumni_page.validate_endowment(alumni_page.load_json(endowment_path or ENDOWMENT_DATA, "endowment"))
    alumni_preview = alumni_preview_info(endowment_data)
    alumni_head = head("Alumni and Endowment | DePauw vs. Wabash",
                        alumni_description(alumni_data),
                        prefix="../", canonical=SITE_URL + ALUMNI_PATH, preview=alumni_preview)
    when = now or dt.datetime.now(dt.timezone.utc)
    alumni_html = alumni_page.render_page(
        alumni_data, endowment_data, head_html=alumni_head,
        footer_html="\n".join(site_footer(when, prefix="../", alumni=(alumni_data, endowment_data))))
    upcoming = meetings.load_upcoming(upcoming_path)
    overrides_path = overrides_path or OVERRIDES
    rules = merge.load_overrides(overrides_path)
    kept = sum(len(r) for r in review.load_keep_previous(overrides_path).values())
    pages = {
        "index.html": render_index(data, checked_at, now, upcoming),
        HOW_PATH + "index.html": render_how_landing(data, now, upcoming),
        HOW_ATHLETICS_PATH + "index.html": render_how_athletics(data, rules, kept, checked_at, now, upcoming),
        HOW_ALUMNI_PATH + "index.html": render_how_alumni(alumni_data, endowment_data, now),
        ALUMNI_PATH + "index.html": alumni_html,
        "404.html": render_404(now),
        "bell-heist/index.html": render_bell_heist(now),
        "games.csv": render_csv(data),
        "site.webmanifest": render_manifest(),
        "sitemap.xml": render_sitemap(data, checked_at, alumni_verified_date(alumni_data, endowment_data)),
        "robots.txt": f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}sitemap.xml\n",
        ".nojekyll": "",
    }
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for name, text in pages.items():
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        (out / name).write_text(curly_html(text) if name.endswith(".html") else text, encoding="utf-8", newline="\n")
    for asset in ("styles.css", "favicon.svg", "easter-eggs.js", "easter-bell.svg", "easter-paw.svg", "easter-tiger.svg"):
        shutil.copyfile(STATIC / asset, out / asset)
    # Images are drawn at build time from the bundled font, so every deploy has a card that matches the data.
    info = preview_info(data, game_day_label(gameday.todays_games(when, upcoming, data["sports"])))
    (out / "og-image.png").write_bytes(images.og_image(data["totals"], info["holder"], info["label"]))
    (out / "alumni-og-image.png").write_bytes(images.alumni_og_image(alumni_preview["values"], alumni_preview["even"]))
    (out / "apple-touch-icon.png").write_bytes(images.icon_png(180))
    (out / "favicon-32.png").write_bytes(images.icon_png(32))
    (out / "icon-192.png").write_bytes(images.icon_png(192))
    (out / "icon-512.png").write_bytes(images.icon_png(512))
    (out / "icon-maskable-512.png").write_bytes(images.icon_png(512, maskable=True))
    (out / "data").mkdir()
    shutil.copyfile(data_path, out / "data" / "series.json")  # the data file as-is
    shutil.copyfile(alumni_path or ALUMNI_DATA, out / "data" / "alumni.json")
    shutil.copyfile(endowment_path or ENDOWMENT_DATA, out / "data" / "endowment.json")


def parse_now(text: str | None) -> dt.datetime | None:
    """The --now value as an aware UTC time: a time with an offset is taken as given, one without as
    Eastern. None (the default) means the real clock."""
    if not text:
        return None
    t = dt.datetime.fromisoformat(text)
    return t.astimezone(dt.timezone.utc) if t.tzinfo else from_eastern(t)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=DATA)
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--upcoming", type=Path, default=UPCOMING,
                    help="upcoming meetings from the schedule pages (optional)")
    ap.add_argument("--overrides", type=Path, default=OVERRIDES,
                    help="the rulings file, for the counts on the how-it-works page")
    ap.add_argument("--alumni", type=Path, default=ALUMNI_DATA, help="curated alumni records")
    ap.add_argument("--endowment", type=Path, default=ENDOWMENT_DATA, help="matched-year endowment records")
    ap.add_argument("--checked-file", type=Path, default=CHECKED_AT,
                    help="file holding the time of the last successful scrape (optional)")
    ap.add_argument("--now", help="pretend it is this time, for local previews and tests only (never used by "
                                  "the workflow): an ISO time, with an offset or, without one, Eastern, "
                                  "e.g. 2026-11-14T09:00")
    args = ap.parse_args(argv)
    try:
        build(args.data, args.out, checked_at=read_checked(args.checked_file),
              upcoming_path=args.upcoming, now=parse_now(args.now), overrides_path=args.overrides,
              alumni_path=args.alumni, endowment_path=args.endowment)
    except (merge.MergeError, DataError, KeyError, TypeError, ValueError, AttributeError) as exc:
        print(f"render failed: {exc!r}", file=sys.stderr)
        return 1
    print(f"Wrote site to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
