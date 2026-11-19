"""
The Monon Bell, year by year (/monon-bell/): who held the Bell after every season since it became the trophy.

Nothing here decides who holds the Bell. Every cell, count and sentence is read from facts.bell_seasons (one entry
per season) and facts.bell_summary, the same list the home page's Bell strip and the football card's Bell block
get their record, holder and streak from (through facts.bell), so the pages cannot disagree. The heist marks come
from the hand-curated data/bell-heists.json, each linking to its story's card on /heist-details/.

  season_text                one season in words, for screen readers ("1965: Wabash won 16 to 6 and took the Bell")
  grid_html                  the table: a row per decade, a column per final digit of the year
  legend_html, notes_html    what the marks mean; the three things the grid cannot say by itself (the first game's
                             tie, the tie rules, a year without a game), with their source
  rules_lines                the same notes as the "Monon Bell rules" section of /how-it-works/athletics/
  summary_rows               the figures above the grid, from facts.bell and facts.bell_summary
  song_html                  "The Bell in song": a short note on the rivalry's song, with a plain link to a video
                             of it and the news release it is told from (a link, never an embed)
  description                the page's meta description, from the same figures
  render_main                the page's <main> content (render.py supplies the head, the header and the footer)

The page's styles are static/bell.css, which only this page loads. No script, no inline style, and nothing loaded
from another site: the video is a plain link.
"""

from __future__ import annotations

import html

import facts
import heists

TITLE = "The Monon Bell, year by year"
PATH = "monon-bell/"                       # relative to the site root
HOME_LINK = "The Bell, year by year"       # the link's words on the home page's Bell strip and the heist history
HEIST_LINK = "Read the Bell’s heist history"
STYLESHEET = "bell.css"
LETTERS = {"DePauw": "D", "Wabash": "W"}   # the holder's letter in a cell; the words are in the cell's hidden text
CELL_CLASSES = {"DePauw": "bt-d", "Wabash": "bt-w", None: "bt-t"}
TIE_MARK, NOT_YET_MARK = "=", "…"
# A heist story's mark is a dot drawn by bell.css (a round border, so it shows in forced colors too), not a
# character: a checker cannot measure the contrast of a symbol, and it can skip an empty box.
HEIST_MARK = '<span class="bt-dot" aria-hidden="true"></span>'
# Where the first season's story and the tie rules come from (the tiger fact both-tie-rule cites the same page;
# tests/test_bell_timeline.py keeps the two in step). Opened and read before it was cited.
SOURCE = {"label": "DePauw Athletics: Rivalry facts",
          "url": "https://depauwtigers.com/sports/2022/6/28/rivalry-facts.aspx"}

# "The Bell in song" (song_html). Told from DePauw's news release of the recording day, read in its Internet Archive
# copy of 2012 (the live page is gone); the video's own page, on the DePauw University Video Archive channel, names
# the same three alumni and who edited the 2012 video (its description, read 2026-11-19, gives his title then). Both
# were opened and read before they were cited. No lyrics, and the video is a plain link: an embed would load
# another site's page and break the privacy policy.
SONG_HEADING = "The Bell in song"
SONG_TITLE = "The Ballad of the Monon Bell"
SONG_MAKERS = ("Darel Lindquist", "Nancy Ford", "Jimmy Ibbotson")   # DePauw alumni, in the release's order
SONG_LEADER = "Ibbotson"                                            # he led the musicians at the recording
SONG_RECORDED = "1985-10-15"                                        # in Nashville
SONG_VIDEO = {"year": 2012, "url": "https://www.youtube.com/watch?v=xW74vxcnskw",
              "editor": "Ken Owen ’82", "editor_role": "DePauw’s Executive Director of Media Relations"}
SONG_SOURCE = {"label": "DePauw University, Oct 15, 1985: Ballad of the Monon Bell is Recorded in Nashville "
                        "(archived copy)",
               "url": "https://web.archive.org/web/20120914170940/"
                      "https://www.depauw.edu/news-media/latest-news/details/23724/"}


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def score_words(game: dict) -> str:
    """A game's score for a sentence, the larger number first: "16 to 6", "7 to 7". Empty when no score is recorded."""
    if not facts.has_score(game):
        return ""
    high, low = sorted((facts.num(game["depauw"]), facts.num(game["wabash"])), reverse=True)
    return f"{high:,} to {low:,}"


def season_text(season: dict) -> str:
    """One season in words: who won, the score, and what became of the Bell. A tie says who kept it, a year with no
    game says so, and the season of the build says it has not been played yet."""
    year, holder, game = season["year"], season["holder"], season["game"]
    if season["status"] == facts.NOT_YET:
        return f"{year}: not yet played"
    if season["status"] == facts.NO_GAME:
        return f"{year}: no game" + (f"; {holder} kept the Bell" if holder else "")
    score = score_words(game)
    if season["result"] == "T":
        tie = f"{score} tie" if score else "a tie"
        return f"{year}: {tie}, {holder} kept the Bell" if holder else f"{year}: {tie}; neither school held the Bell"
    verb = "took" if season["since"] == year else "kept"      # the hold began with this year's win, or went on
    return f"{year}: {holder} won{' ' + score if score else ''} and {verb} the Bell"


def heists_by_year(heists_data: dict | None) -> dict:
    """{year: [incident, ...]} for the stories that have a year, in the history page's order."""
    found: dict = {}
    for incident in heists.ordered(heists_data) if heists_data else []:
        if incident["year"] is not None:
            found.setdefault(incident["year"], []).append(incident)
    return found


def _heist_link(incident: dict, heist_href: str) -> str:
    """A story's mark: a dot that links to its card, named by its year and title for a screen reader."""
    return (f'<a class="bt-heist" href="{_esc(heist_href)}#{_esc(heists.anchor(incident["id"]))}">'
            f'{HEIST_MARK}'
            f'<span class="sr-only">{incident["year"]} heist story: {_esc(incident["title"])}</span></a>')


def _cell(year: int, season: dict | None, future: bool, stories: list, heist_href: str) -> str:
    """One year's cell. A played season shows the holder's letter (with the tie mark after a tie) on its school's
    tint; the words are in hidden text, so the letter itself is hidden from screen readers. A year before the
    Bell, a year without a game and a future year show nothing and say so in hidden text."""
    cls, shown = "", ""
    if season is None:
        said = f"{year}: a future season" if future else f"{year}: before the Bell"
    else:
        said = season_text(season)
        if season["status"] == facts.NOT_YET:
            cls, shown = "bt-next", NOT_YET_MARK
        elif season["status"] == facts.PLAYED:
            cls = CELL_CLASSES[season["holder"]]
            shown = LETTERS.get(season["holder"], "") + (TIE_MARK if season["result"] == "T" else "")
    attr = f' class="{cls}"' if cls else ""
    mark = f'<span class="bt-l" aria-hidden="true">{shown}</span>' if shown else ""
    links = "".join(_heist_link(i, heist_href) for i in stories)
    return f'<td{attr}>{mark}<span class="sr-only">{_esc(said)}</span>{links}</td>'


def grid_html(seasons: list, heists_data: dict | None, heist_href: str) -> str:
    """The grid: one row per decade from the Bell's first to the newest season's, one column per final digit."""
    by_year = {s["year"]: s for s in seasons}
    first, last = seasons[0]["year"], seasons[-1]["year"]
    stories = heists_by_year(heists_data)
    head = '<th scope="col"><span class="sr-only">Decade</span></th>' + "".join(
        f'<th scope="col">{digit}</th>' for digit in range(10))
    rows = []
    for decade in range(first // 10 * 10, last + 1, 10):
        cells = "".join(_cell(y, by_year.get(y), y > last, stories.get(y, []), heist_href)
                        for y in range(decade, decade + 10))
        rows.append(f'<tr><th scope="row">{decade}s</th>{cells}</tr>')
    return ('<table class="bt-grid">\n'
            f'<caption class="sr-only">Who held the Monon Bell after each season, {first} to {last}: one row per '
            'decade, one column per year</caption>\n'
            f'<thead><tr>{head}</tr></thead>\n<tbody>\n' + "\n".join(rows) + '\n</tbody>\n</table>')


def legend_html(seasons: list, heists_data: dict | None) -> str:
    """What each mark means, drawn the way the grid draws it. A mark the grid does not hold is left out."""
    def key(cls, mark, text):
        return f'<li><span class="bt-key{" " + cls if cls else ""}">{mark}</span><span>{_esc(text)}</span></li>'
    items = [key(CELL_CLASSES[school], letter, f"{school} had the Bell when that season ended")
             for school, letter in LETTERS.items()]
    if any(s["result"] == "T" for s in seasons):
        items.append(key("bt-t", TIE_MARK, "The game was a tie, so the Bell stayed where it was"))
    if heists_by_year(heists_data):
        items.append(key("", HEIST_MARK, "A dot: a heist story from that year; the dot is a link to it"))
    if any(s["status"] == facts.NOT_YET for s in seasons):
        items.append(key("bt-next", NOT_YET_MARK, "This season’s game has not been played yet"))
    items.append(key("bt-empty", "", "An empty box: no Bell game that year"))
    return '<ul class="bt-legend">\n' + "\n".join(items) + '\n</ul>'


def notes(seasons: list, overtime_start: int, where: str = "the grid") -> list:
    """[(label, sentence)] for what the grid cannot say by itself, each worked out from the seasons: the first
    game when it was a tie nobody could keep the Bell after, the tie rule (with the early rule the source also
    gives, and the first tie it could have applied to), and every year without a game. `where` names what follows
    the rule: "the grid" under the grid, "the site" on the how-it-works page."""
    out = []
    opener = seasons[0]
    if opener["result"] == "T" and opener["holder"] is None:
        score = score_words(opener["game"]).replace(" to ", "–")
        out.append((str(opener["year"]),
                    f"the first Bell game ended in a {score + ' ' if score else ''}tie. Neither team had won the "
                    "Bell, so it was kept out of sight for the year, and that season counts for neither school."))
    ties = [s for s in seasons if s["result"] == "T"]
    if ties:
        text = (f"before overtime came to college football in {overtime_start}, a tied game left the Bell "
                "with the school that already had it.")
        kept = [s for s in ties if s["holder"]]
        if kept:
            text += (" Early rules had the schools share it for six months each after a tie. No source we have "
                     f"read says whether that was done after the {kept[0]['year']} tie, the first with a holder, "
                     f"so {where} leaves the Bell with {kept[0]['holder']}.")
        out.append(("Ties", text))
    for s in seasons:
        if s["status"] == facts.NO_GAME:
            out.append((str(s["year"]), "no game was played" + (
                f", so {s['holder']} kept the Bell and the season counts as one {s['holder']} ended with it."
                if s["holder"] else ".")))
    return out


def _source_link() -> str:
    return f'<a href="{_esc(SOURCE["url"])}">{_esc(SOURCE["label"])}</a>'


def notes_html(seasons: list, overtime_start: int) -> str:
    found = notes(seasons, overtime_start)
    if not found:
        return ""
    return ('<ul class="bt-notes">\n' + "\n".join(f"<li>{_esc(label)}: {_esc(text)}</li>" for label, text in found)
            + '\n</ul>\n'
            f'<p class="bt-source">Source for the first season and the tie rules: {_source_link()}.</p>')


RULES_HEADING = "Monon Bell rules"


def rules_lines(seasons: list, overtime_start: int, page_href: str) -> list:
    """The "Monon Bell rules" section of the athletics how-it-works page, as lines of HTML: the rule after a game,
    then the same notes the grid carries (worded for the whole site), their source and a link to this page at
    `page_href`. Nothing when no Bell game is on record."""
    if not seasons:
        return []
    items = [("After a game", "the winner takes the Bell and holds it until the other school wins.")]
    items += notes(seasons, overtime_start, where="the site")
    return [
        f'<h3>{_esc(RULES_HEADING)}</h3>',
        f'<p>The football game has been played for the Monon Bell since the {seasons[0]["year"]} season. The home '
        f'page’s Bell record and holder and <a href="{_esc(page_href)}">{_esc(TITLE)}</a>, which shows who had the '
        'Bell after every season, are worked out from one set of rules:</p>',
        '<ul>',
        *[f'<li><strong>{_esc(label)}:</strong> {_esc(text)}</li>' for label, text in items],
        '</ul>',
        f'<p>The first season’s story and the tie rules are from {_source_link()}.</p>',
    ]


def _times(n: int) -> str:
    return "twice" if n == 2 else f"{n} times"


def _seasons(n: int) -> str:
    return f"{n:,} {'season' if n == 1 else 'seasons'}"


def run_text(run: dict | None) -> str:
    """A school's longest hold: "10 seasons · 1955–1964", or "3 seasons (twice, most recently 1987–1989)"."""
    if not run:
        return "None yet"
    span = str(run["first"]) if run["first"] == run["last"] else f"{run['first']}–{run['last']}"
    if run["times"] > 1:
        return f"{_seasons(run['seasons'])} ({_times(run['times'])}, most recently {span})"
    return f"{_seasons(run['seasons'])}{facts.SEPARATOR}{span}"


def summary_rows(summary: dict, record_html: str) -> list:
    """[(label, html)] for the figures above the grid. `record_html` is the Bell-era record exactly as the home
    page words it (render.bell_record_text)."""
    held = summary["seasons"]
    seasons = (f'<span class="t-d">DePauw {held["DePauw"]}</span> · <span class="t-w">Wabash {held["Wabash"]}</span>'
               + (f' · neither {held[None]}' if held[None] else ""))
    return [(f"Bell record since {facts.BELL_START}", record_html),
            ("Seasons ended with the Bell", seasons),
            ("Longest DePauw hold", _esc(run_text(summary["longest"]["DePauw"]))),
            ("Longest Wabash hold", _esc(run_text(summary["longest"]["Wabash"]))),
            ("Changes of hands", f'{summary["changes"]:,}')]


def song_html() -> str:
    """The "The Bell in song" card: three short sentences, a plain link to the video and the sources line."""
    *first, last = SONG_MAKERS
    video = SONG_VIDEO
    return (
        '<section class="bt-card" aria-labelledby="bt-song-h">\n'
        f'<h3 class="eyebrow" id="bt-song-h">{_esc(SONG_HEADING)}</h3>\n'
        f'<p>“{_esc(SONG_TITLE)}” is a song about the rivalry, created by DePauw alumni '
        f'{_esc(", ".join(first))}, and {_esc(last)}. Musicians led by {_esc(SONG_LEADER)} recorded it in Nashville '
        f'on {heists.fmt_date(SONG_RECORDED)}. The {video["year"]} video was edited by {_esc(video["editor"])}, then '
        f'{_esc(video["editor_role"])}. <a href="{_esc(video["url"])}">Watch DePauw’s {video["year"]} video of the '
        'song on YouTube</a>.</p>\n'
        f'<p class="bt-source">Sources: <a href="{_esc(SONG_SOURCE["url"])}">{_esc(SONG_SOURCE["label"])}</a> and '
        f'the {video["year"]} video’s description on YouTube.</p>\n'
        '</section>')


def lead(seasons: list) -> str:
    return (f"Who took the Monon Bell home after each DePauw–Wabash football game, from the "
            f"{seasons[0]['year'] if seasons else facts.BELL_START} season to today.")


def description(seasons: list, bell: dict | None) -> str:
    """The meta description, from the same figures the page shows."""
    if not seasons:
        return "Who held the Monon Bell after every DePauw–Wabash football season."
    summary = facts.bell_summary(seasons)
    held = summary["seasons"]
    text = (f"Who held the Monon Bell after every season since {seasons[0]['year']}: DePauw "
            f"{_seasons(held['DePauw'])}, Wabash {held['Wabash']:,}, and {summary['changes']:,} changes of hands.")
    if bell and bell["holder"]:
        text += f" {bell['holder']} has held it since {bell['since']}."
    return text


def render_main(seasons: list, heists_data: dict | None, *, back_html: str, back_link: str, heist_href: str,
                holder_html: str, record_html: str, overtime_start: int) -> list:
    """The page's <main> content as lines of HTML. `seasons` is facts.bell_seasons for the football games and the
    Eastern year of the build; `holder_html` and `record_html` are the home page's own holder line and record
    (render.bell_now_html, render.bell_record_text), so both pages word them the same; `back_html` is the shared
    back link for the top of the page and `back_link` the same link for the foot."""
    top = [back_html, f'<h2>{_esc(TITLE)}</h2>', f'<p class="bt-lead">{_esc(lead(seasons))}</p>']
    foot = [f'<p class="bt-more"><a href="{_esc(heist_href)}">{_esc(HEIST_LINK)}</a> · {back_link}</p>']
    if not seasons:
        return top + ['<p>No Bell game is on record yet.</p>', song_html()] + foot
    rows = summary_rows(facts.bell_summary(seasons), record_html)
    return top + [
        '<section class="bt-card" aria-labelledby="bt-numbers-h">',
        '<h3 class="eyebrow" id="bt-numbers-h">The Bell in numbers</h3>',
        holder_html,
        '<dl class="facts">\n' + "\n".join(f'<div><dt>{_esc(k)}</dt><dd>{v}</dd></div>' for k, v in rows) + '\n</dl>',
        '</section>',
        '<section class="bt-card" aria-labelledby="bt-seasons-h">',
        '<h3 class="eyebrow" id="bt-seasons-h">Season by season</h3>',
        grid_html(seasons, heists_data, heist_href),
        legend_html(seasons, heists_data),
        notes_html(seasons, overtime_start),
        '</section>',
        song_html(),
    ] + foot
