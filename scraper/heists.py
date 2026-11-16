"""
The Monon Bell heist history page (/heist-details/): validation of the hand-curated data/bell-heists.json, the
tally worked out from it, the Bell game result looked up for each year, and the page's main content.

Nothing here is scraped. The JSON file is edited by hand and reviewed through pull requests; render.build
validates it on every build (a bad file fails the render instead of publishing half a page).

  validate                   refuse missing or inconsistent data (a source with a real URL for every story, a known
                             outcome, school and rating, unique ids, sane years)
  tally, tally_text          stolen / foiled / hidden counts, worked out from the incidents (never stored)
  RATINGS                    the four ratings (how well a story is sourced) and what each means
  SEPARATOR                  the "·" between the items of a card's tag line, held to the item before it by a
                             no-break space so a wrapped line never starts with the dot
  ordered                    the timeline order (oldest first, "Date unknown" last)
  bell_game, game_line       that year's Monon Bell football result, read from series.json (skipped when no game);
                             a story may name another year's game to show instead (`game_year`)
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
# How well each story is sourced: the label on the card, and what it means (the line near the top of the page).
RATINGS = {"documented": ("Documented", "a source from the time says so"),
           "reported": ("Reported", "a later, reputable source says so, and no report from the time has been found"),
           "lore": ("Lore", "repeated without a source"),
           "disputed": ("Disputed", "the sources disagree")}
RATINGS_QUESTION = "How sure is each story?"
# Between the items of a card's tag line: a no-break space holds each "·" to the item before it, so a line may end
# with the dot but can never start with one, however the line wraps.
SEPARATOR = " · "
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
        if incident.get("rating") not in RATINGS:
            _fail(f"incident {iid} has invalid rating {incident.get('rating')!r} (use one of {', '.join(RATINGS)})")
        if "game_year" in incident:
            shown = incident["game_year"]
            if (isinstance(shown, bool) or not isinstance(shown, int) or not FIRST_YEAR <= shown <= LAST_YEAR
                    or year is None or shown == year):
                _fail(f"incident {iid}.game_year must be a year other than the story's own (leave it out to show "
                      "that year's game)")
        _text(incident.get("title"), f"incident {iid}.title")
        _text(incident.get("summary"), f"incident {iid}.summary")
        if "details" in incident:
            _paragraphs(incident["details"], f"incident {iid}.details")
        if "consequences" in incident:
            _text(incident["consequences"], f"incident {iid}.consequences")
        counted = incident.get("counted_in_theft_total")
        if not isinstance(counted, bool):
            _fail(f"incident {iid}.counted_in_theft_total must be true or false")
        if counted != (incident["outcome"] == "stolen"):
            _fail(f"incident {iid}: only a stolen Bell counts in the theft total")
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


def bell_game(football_games: list, year: int) -> dict | None:
    """The football game (the Monon Bell game since 1932) played in `year`, or None. A game with only a
    placeholder date has no trustworthy year, so it is skipped."""
    found = [g for g in football_games
             if g.get("date_known", True) and int(g["date"][:4]) == year and year >= facts.BELL_START]
    return max(found, key=lambda g: g["date"]) if found else None


def game_line(game: dict, year: int | None = None) -> str:
    """"That year’s Bell game: Wabash 16, DePauw 6", winner first; "DePauw 7, Wabash 7" for a tie; just the result when
    the score is unknown. With `year` (a story that shows another year's game) it starts "The 1967 Bell game:"."""
    lead = f"The {year} Bell game" if year is not None else "That year’s Bell game"
    result = game["result"]
    if not facts.has_score(game):
        outcome = {"W": "DePauw won", "L": "Wabash won", "T": "a tie"}[result]
        return f"{lead}: {outcome}"
    d, w = facts.num(game["depauw"]), facts.num(game["wabash"])
    first, second = (("Wabash", w), ("DePauw", d)) if result == "L" else (("DePauw", d), ("Wabash", w))
    return f"{lead}: {first[0]} {first[1]:,}, {second[0]} {second[1]:,}" + (" (tie)" if result == "T" else "")


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
    return SEPARATOR.join(parts)


def _game_html(incident: dict, football_games: list, game_href: str) -> str:
    if incident["year"] is None:
        return ""
    other_year = incident.get("game_year")          # the burial: buried one November, dug up at the next year's game
    game = bell_game(football_games, other_year or incident["year"])
    if not game:
        return ""
    return f'<p class="heist-game"><a href="{_esc(game_href)}">{_esc(game_line(game, other_year))}</a></p>'


def _tag(outcome: str) -> str:
    return f'<span class="heist-tag heist-{outcome}">{OUTCOMES[outcome]}</span>'


def _rating(rating: str) -> str:
    return f'<span class="heist-rating heist-rating-{rating}">{RATINGS[rating][0]}</span>'


def _ratings_html() -> str:
    parts = " ".join(f"<strong>{_esc(label)}:</strong> {_esc(meaning)}." for label, meaning in RATINGS.values())
    return f'<p class="heist-ratings" id="ratings"><strong>{_esc(RATINGS_QUESTION)}</strong> {parts}</p>'


def _card(incident: dict, football_games: list, game_href: str) -> str:
    iid = anchor(incident["id"])
    who = SCHOOLS[incident["school"]]
    body = [f'<p class="heist-meta">{_tag(incident["outcome"])} {_rating(incident["rating"])} '
            f'<span class="heist-when">{_esc(_when(incident) + SEPARATOR.rstrip())}</span> '
            f'<span class="heist-who">{_esc(who)}</span></p>',
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


def render_main(data: dict, football_games: list, *, back_html: str, game_href: str,
                repo_url: str = REPO_URL) -> list:
    """The page's <main> content as lines of HTML. `back_html` is the shared back link, `game_href` points at
    the football card."""
    counts = tally(data)
    timeline = []
    undated_started = False
    for incident in ordered(data):
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
        _ratings_html(),
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
