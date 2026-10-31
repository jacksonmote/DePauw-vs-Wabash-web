"""
The Monon Bell heist history page (/heist-details/): validation of the hand-curated data/bell-heists.json, the
tally worked out from it, the Bell game result looked up for each year, and the page's main content.

Nothing here is scraped. The JSON file is edited by hand and reviewed through pull requests; render.build
validates it on every build (a bad file fails the render instead of publishing half a page).

  validate                   refuse missing or inconsistent data (a source with a real URL for every story, a known
                             outcome and school, unique ids, sane years)
  tally, tally_text          stolen / foiled / hidden counts, worked out from the incidents (never stored)
  ordered, other_thefts      the timeline order (oldest first, "Date unknown" last) and the undocumented bare years
  bell_game, game_line       that year's Monon Bell football result, read from series.json (skipped when no game)
  render_main                the page's <main> content (render.py supplies the head, the header and the footer)
  correction_url             the prefilled link to .github/ISSUE_TEMPLATE/data-correction.yml
"""

from __future__ import annotations

import datetime as dt
import html
import json
from pathlib import Path
from urllib.parse import quote, urlparse

import facts

REPO_URL = "https://github.com/jacksonmote/DePauw-vs-Wabash-web"
TITLE = "The Bell’s heist history"
CORRECTION_SPORT = "Monon Bell heist history"     # the option added to the data-correction form's sport field

OUTCOMES = {"stolen": "Stolen", "foiled": "Foiled", "hidden": "Hidden"}
SCHOOLS = {"depauw": "DePauw students", "wabash": "Wabash students", "not-recorded": "Not recorded"}
FIRST_YEAR, LAST_YEAR = 1832, 2100     # DePauw’s oldest year; nothing about the Bell predates the schools’ rivalry
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


class HeistError(ValueError):
    """Curated heist data is missing or inconsistent."""


def _fail(message):
    raise HeistError(message)


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        _fail(f"{field} must be a non-empty string")


def _url(value, field):
    _text(value, field)
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname:
        _fail(f"{field} must be an absolute https URL with a host")


def _date(value, field):
    _text(value, field)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        _fail(f"{field} must be an ISO date (YYYY-MM-DD)")
    if parsed.isoformat() != value:
        _fail(f"{field} must be an ISO date (YYYY-MM-DD)")


def _sources(value, field):
    if not isinstance(value, list) or not value:
        _fail(f"{field} must be a non-empty list of sources")
    for i, source in enumerate(value):
        if not isinstance(source, dict):
            _fail(f"{field}[{i}] must be an object")
        _text(source.get("label"), f"{field}[{i}].label")
        _url(source.get("url"), f"{field}[{i}].url")


def _paragraphs(value, field):
    if not isinstance(value, list) or not value:
        _fail(f"{field} must be a non-empty list of paragraphs")
    for i, para in enumerate(value):
        _text(para, f"{field}[{i}]")


def load_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(f"cannot read bell heist data at {path}: {exc}")
    if not isinstance(value, dict):
        _fail("bell heist data must be a JSON object")
    return value


def validate(data: dict) -> dict:
    """Return `data` when it is complete and consistent, else raise HeistError naming the first problem."""
    if isinstance(data.get("schema_version"), bool) or data.get("schema_version") != 1:
        _fail("bell heist data must use schema_version 1")
    _date(data.get("verified_at"), "verified_at")
    _text(data.get("intro"), "intro")
    _paragraphs(data.get("method"), "method")
    guarding = data.get("guarding")
    if not isinstance(guarding, dict):
        _fail("guarding must be an object")
    _paragraphs(guarding.get("paragraphs"), "guarding.paragraphs")
    _sources(guarding.get("sources"), "guarding.sources")
    incidents = data.get("incidents")
    if not isinstance(incidents, list) or not incidents:
        _fail("incidents must be a non-empty list")
    ids = set()
    for incident in incidents:
        if not isinstance(incident, dict):
            _fail("each incident must be an object")
        iid = incident.get("id")
        if not isinstance(iid, str) or not iid.strip() or iid in ids:
            _fail(f"invalid or duplicate incident id: {iid!r}")
        if iid != iid.lower() or " " in iid or iid.startswith("-"):
            _fail(f"incident id {iid!r} must be lowercase with no spaces (it becomes the page anchor)")
        ids.add(iid)
        year = incident.get("year")
        if year is not None and (isinstance(year, bool) or not isinstance(year, int)
                                 or not FIRST_YEAR <= year <= LAST_YEAR):
            _fail(f"incident {iid}.year must be null or a year from {FIRST_YEAR} to {LAST_YEAR}")
        if "date" in incident:
            _date(incident["date"], f"incident {iid}.date")
            if year is None or int(incident["date"][:4]) != year:
                _fail(f"incident {iid}.date must fall in its year")
        if "date_note" in incident:
            _text(incident["date_note"], f"incident {iid}.date_note")
        if incident.get("school") not in SCHOOLS:
            _fail(f"incident {iid} has invalid school {incident.get('school')!r}")
        if incident.get("outcome") not in OUTCOMES:
            _fail(f"incident {iid} has invalid outcome {incident.get('outcome')!r}")
        _text(incident.get("title"), f"incident {iid}.title")
        _text(incident.get("summary"), f"incident {iid}.summary")
        if "details" in incident:
            _paragraphs(incident["details"], f"incident {iid}.details")
        if "consequences" in incident:
            _text(incident["consequences"], f"incident {iid}.consequences")
        if "bare_record" in incident and not isinstance(incident["bare_record"], bool):
            _fail(f"incident {iid}.bare_record must be true or false")
        counted = incident.get("counted_in_theft_total")
        if not isinstance(counted, bool):
            _fail(f"incident {iid}.counted_in_theft_total must be true or false")
        if counted != (incident["outcome"] == "stolen"):
            _fail(f"incident {iid}: only a stolen Bell counts in the theft total")
        if incident.get("bare_record") and (year is None or "details" in incident):
            _fail(f"incident {iid}: a bare record needs a year and has no details")
        _sources(incident.get("sources"), f"incident {iid}.sources")
    return data


def tally(data: dict) -> dict:
    """Stolen (at least), foiled and hidden counts, from the incidents. The theft count is "at least" because
    the record is incomplete."""
    incidents = data["incidents"]
    return {"stolen": sum(1 for i in incidents if i["counted_in_theft_total"]),
            "foiled": sum(1 for i in incidents if i["outcome"] == "foiled"),
            "hidden": sum(1 for i in incidents if i["outcome"] == "hidden")}


def tally_text(counts: dict) -> str:
    stolen, foiled, hidden = counts["stolen"], counts["foiled"], counts["hidden"]
    parts = [f"Stolen at least {stolen} {'time' if stolen == 1 else 'times'}"]
    if foiled:
        parts.append(f"{foiled} foiled {'attempt' if foiled == 1 else 'attempts'}")
    if hidden:
        parts.append(f"{hidden} hidden by its own fans")
    return " · ".join(parts)


def ordered(data: dict) -> list:
    """Dated incidents oldest first (ties by date, then file order), then the ones with no year."""
    incidents = data["incidents"]
    dated = sorted((i for i in incidents if i["year"] is not None), key=lambda i: (i["year"], i.get("date", "")))
    return dated + [i for i in incidents if i["year"] is None]


def other_thefts(data: dict) -> list:
    """The bare years: recorded thefts nobody has documented further, shown together on one compact card."""
    return [i for i in ordered(data) if i.get("bare_record")]


def bell_game(football_games: list, year: int) -> dict | None:
    """The football game (the Monon Bell game since 1932) played in `year`, or None. A game with only a
    placeholder date has no trustworthy year, so it is skipped."""
    found = [g for g in football_games
             if g.get("date_known", True) and int(g["date"][:4]) == year and year >= facts.BELL_START]
    return max(found, key=lambda g: g["date"]) if found else None


def game_line(game: dict) -> str:
    """"Associated game: Wabash 16, DePauw 6", winner first; "DePauw 7, Wabash 7" for a tie; just the result when
    the score is unknown."""
    month = MONTHS[int(game["date"][5:7]) - 1]
    result = game["result"]
    if not facts.has_score(game):
        outcome = {"W": "DePauw won", "L": "Wabash won", "T": "a tie"}[result]
        return f"Associated game: {outcome}"
    d, w = facts.num(game["depauw"]), facts.num(game["wabash"])
    first, second = (("Wabash", w), ("DePauw", d)) if result == "L" else (("DePauw", d), ("Wabash", w))
    return f"Associated game: {first[0]} {first[1]:,}, {second[0]} {second[1]:,}" + (" (tie)" if result == "T" else "")


def correction_url(title: str | None = None, repo_url: str = REPO_URL) -> str:
    """Opens the data-correction form with the sport option set to the heist history (and the story's title)."""
    label = f"{CORRECTION_SPORT}: {title}" if title else CORRECTION_SPORT
    return (f"{repo_url}/issues/new?template=data-correction.yml&title={quote('Data correction: ' + label)}"
            f"&sport={quote(CORRECTION_SPORT)}")


def fmt_date(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{MONTHS[d.month - 1][:3]} {d.day}, {d.year}"


def anchor(incident_id: str) -> str:
    """The page anchor of a story: its id with a letter in front, because an HTML id may not start with a digit."""
    return "heist-" + incident_id


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def _heading(anchor: str, text: str) -> str:
    """The heading text is the link to its own card, like the athletics cards."""
    return f'<h3><a class="permalink" id="{_esc(anchor)}-h" href="#{_esc(anchor)}">{_esc(text)}</a></h3>'


def _sources_html(sources: list) -> str:
    links = " · ".join(f'<a href="{_esc(s["url"])}">{_esc(s["label"])}</a>' for s in sources)
    return f'<p class="heist-sources">Sources: {links}</p>'


def _when(incident: dict) -> str:
    year, parts = incident["year"], []
    if year is None:
        return "Date unknown"
    parts.append(fmt_date(incident["date"]) if "date" in incident else str(year))
    if "date_note" in incident:
        parts.append(incident["date_note"])
    return " · ".join(parts)


def _game_html(incident: dict, football_games: list, game_href: str) -> str:
    if incident["year"] is None:
        return ""
    game = bell_game(football_games, incident["year"])
    if not game:
        return ""
    return f'<p class="heist-game"><a href="{_esc(game_href)}">{_esc(game_line(game))}</a></p>'


def _tag(outcome: str) -> str:
    return f'<span class="heist-tag heist-{outcome}">{OUTCOMES[outcome]}</span>'


def _card(incident: dict, football_games: list, game_href: str) -> str:
    iid = anchor(incident["id"])
    who = SCHOOLS[incident["school"]]
    body = [f'<p class="heist-meta">{_tag(incident["outcome"])} <span class="heist-when">{_esc(_when(incident))}</span>'
            f' · <span class="heist-who">{_esc(who)}</span></p>',
            _heading(iid, incident["title"]),
            f'<p>{_esc(incident["summary"])}</p>',
            _game_html(incident, football_games, game_href)]
    if "details" in incident:
        paras = "".join(f"<p>{_esc(p)}</p>" for p in incident["details"])
        body.append(f'<details class="heist-details"><summary>The longer story</summary>{paras}</details>')
    if "consequences" in incident:
        body.append(f'<p class="heist-consequences"><strong>Aftermath:</strong> {_esc(incident["consequences"])}</p>')
    body.append(_sources_html(incident["sources"]))
    return (f'<section class="heist heist-school-{incident["school"]}" id="{_esc(iid)}" aria-labelledby="{_esc(iid)}-h">'
            + "\n".join(b for b in body if b) + '</section>')


def _other_card(bare: list, football_games: list, game_href: str) -> str:
    """One compact card for the years that are recorded as thefts and nothing more."""
    rows = []
    for incident in bare:
        game = bell_game(football_games, incident["year"])
        line = f' · <a href="{_esc(game_href)}">{_esc(game_line(game))}</a>' if game else ""
        links = " · ".join(f'<a href="{_esc(s["url"])}">{_esc(s["label"])}</a>' for s in incident["sources"])
        rows.append(f'<li id="{_esc(anchor(incident["id"]))}"><strong>{incident["year"]}</strong>{line}'
                    f' <span class="heist-row-sources">({links})</span></li>')
    return ('<section class="heist heist-school-not-recorded heist-other" id="other-recorded-thefts" '
            'aria-labelledby="other-recorded-thefts-h">'
            f'<p class="heist-meta">{_tag("stolen")} <span class="heist-when">{len(bare)} recorded years</span></p>'
            + _heading("other-recorded-thefts", "Other recorded thefts")
            + '<p>These years are recorded as thefts of the Bell. The details of who took it, how and what '
              'followed haven’t been added to this page yet.</p>'
            + f'<ul class="heist-years">{"".join(rows)}</ul></section>')


def render_main(data: dict, football_games: list, *, back_html: str, game_href: str,
                repo_url: str = REPO_URL) -> list:
    """The page's <main> content as lines of HTML. `back_html` is the shared back link, `game_href` points at
    the football card."""
    counts = tally(data)
    timeline = []
    bare = other_thefts(data)
    bare_done = False
    undated_started = False
    for incident in ordered(data):
        if incident.get("bare_record"):
            if not bare_done:                      # the compact card sits where the oldest bare year falls
                timeline.append(_other_card(bare, football_games, game_href))
                bare_done = True
            continue
        if incident["year"] is None and not undated_started:
            timeline.append('<h2 id="date-unknown-h" class="heist-era">Date unknown</h2>')
            undated_started = True
        timeline.append(_card(incident, football_games, game_href))
    guarding = data["guarding"]
    return [
        back_html,
        f'<h2 class="heist-title">{_esc(TITLE)}</h2>',
        f'<p class="heist-intro">{_esc(data["intro"])}</p>',
        f'<p class="heist-tally" id="tally"><strong>{_esc(tally_text(counts))}</strong></p>',
        '<p class="heist-note">At least, because the record is incomplete. Who took the Bell is unknown for many '
        'of the thefts, so there is no scoreboard here.</p>',
        '<section class="heist-method" aria-label="About these stories">'
        + "".join(f"<p>{_esc(p)}</p>" for p in data["method"]) + '</section>',
        '<div class="heist-timeline">',
        '<h2 class="heist-era" id="timeline-h">The timeline, oldest first</h2>',
        *timeline,
        '</div>',
        '<section class="heist-guarding" id="guarded-now" aria-labelledby="guarded-now-h">',
        '<h2><a class="permalink" id="guarded-now-h" href="#guarded-now">How the Bell is guarded now</a></h2>',
        *[f"<p>{_esc(p)}</p>" for p in guarding["paragraphs"]],
        _sources_html(guarding["sources"]),
        '</section>',
    ]
