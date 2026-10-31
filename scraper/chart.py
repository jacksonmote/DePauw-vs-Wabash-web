"""
The every-game chart on a sport card: one bar per game, oldest to newest, drawn at build time as inline SVG.

Pure functions over the games in data/series.json; no JavaScript, no inline styles (the CSP forbids both), no
images. Bars above the baseline are DePauw wins, bars below are Wabash wins, taller means a bigger margin, and a
tie is a short gray mark on the baseline. The bars are three <path> elements (one per kind of result), not one
element per game, so a 314-game chart is three nodes. Colors come from classes in static/styles.css.

Bars are placed by the game's position in the list, not by its season, so a sport that meets several times a year
(basketball, baseball) still shows every game. Nothing here is hardcoded to a sport or a year: the span, the axis
labels and the height scale all come from the games.
"""

from __future__ import annotations

import html

import facts

WIDTH, HEIGHT = 270, 104          # the SVG's own coordinate box; CSS scales it to the card
PAD = 4                           # space left and right of the bars
MID = HEIGHT // 2                 # the baseline
HALF = MID - 4                    # the tallest bar's height
MIN_BAR = 3                       # shortest win bar, so a one-point game is still visible
TIE_BAR = 2                       # a tie's mark, centred on the baseline
BAR_MAX_W = 6                     # a sport with few games gets wider bars, up to this
BAR_FILL = 0.7                    # a bar fills this share of the space between two games
CAP_PERCENTILE = 0.9              # margins above the 90th percentile are drawn full height, so one blowout
                                  # does not shrink every other bar to a sliver
AXIS_LABELS = 4                   # years printed under the chart, evenly spaced by game


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def margin_cap(games: list) -> float:
    """The margin at which a bar reaches full height: the CAP_PERCENTILE margin of the games that have a score
    (nearest rank), never below 1. A sport whose margins are all 0 (every game tied) gets 1."""
    margins = sorted(float(facts.margin(g)) for g in games if facts.has_score(g) and g["result"] != "T")
    if not margins:
        return 1.0
    return max(1.0, margins[max(0, min(len(margins) - 1, int(len(margins) * CAP_PERCENTILE + 0.999999) - 1))])


def bar_height(g: dict, cap: float) -> float:
    """A win's bar height. A game listed without a score (score_known false) has no margin, so it gets the
    minimum, which still shows who won."""
    if not facts.has_score(g):
        return float(MIN_BAR)
    return max(float(MIN_BAR), min(float(facts.margin(g)), cap) * HALF / cap)


def axis_years(games: list) -> list:
    """Years under the chart: AXIS_LABELS evenly spaced by game (first and last included), each year once."""
    n = len(games)
    if n == 0:
        return []
    picks = [round(i * (n - 1) / (AXIS_LABELS - 1)) for i in range(AXIS_LABELS)] if n >= AXIS_LABELS else range(n)
    years = []
    for i in picks:
        y = facts.game_year(games[i])
        if not years or years[-1] != y:
            years.append(y)
    return years


def num(x: float) -> str:
    """A coordinate to one decimal without a trailing .0 (the page carries one of these per game)."""
    return f"{x:.1f}".rstrip("0").rstrip(".")


def bar_width(n: int) -> float:
    """The stroke width of every bar in a chart of n games: wide when there are few games, up to BAR_MAX_W."""
    return min(float(BAR_MAX_W), (WIDTH - 2 * PAD) / max(n, 1) * BAR_FILL)


def paths(games: list) -> dict:
    """{"d": path data, "w": ..., "t": ...} for the three kinds of result; "" when a kind has none. Each bar is a
    vertical line segment ("M x y v h"), drawn with the chart's bar width as its stroke width, which is half the
    bytes of a filled rectangle: a 314-game chart is about 5 KB."""
    n = len(games)
    cap = margin_cap(games)
    bw = bar_width(n)
    room = WIDTH - 2 * PAD - bw
    step = room / (n - 1) if n > 1 else 0.0
    start = PAD + bw / 2 if n > 1 else WIDTH / 2                    # a bar's x is its centre
    out = {"d": [], "w": [], "t": []}
    for i, g in enumerate(games):
        x = num(start + i * step)
        if g["result"] == "T":
            out["t"].append(f"M{x} {num(MID - TIE_BAR / 2)}v{TIE_BAR}")
        elif g["result"] == "W":
            out["d"].append(f"M{x} {MID}v-{num(bar_height(g, cap))}")
        else:
            out["w"].append(f"M{x} {MID}v{num(bar_height(g, cap))}")
    return {k: "".join(v) for k, v in out.items()}


def description(name: str, games: list, cap: float, unit_many: str, words: tuple) -> str:
    """The text alternative: what the chart shows, with the counts it is drawn from."""
    d = sum(1 for g in games if g["result"] == "W")
    w = sum(1 for g in games if g["result"] == "L")
    t = len(games) - d - w
    unscored = sum(1 for g in games if not facts.has_score(g))
    first, last = facts.game_year(games[0]), facts.game_year(games[-1])
    span = first if first == last else f"{first} to {last}"
    text = (f"{name}: one bar for each of {len(games):,} {words[1]}, oldest to newest, {span}. "
            f"Bars above the line are DePauw wins ({d:,}), bars below are Wabash wins ({w:,}) and short gray marks "
            f"are ties ({t:,}). Taller means a bigger margin; a margin of {facts.num(cap)} {unit_many} or more "
            f"is drawn full height.")
    if unscored:
        text += (f" {unscored:,} {words[0] + ' has' if unscored == 1 else words[1] + ' have'} no recorded score and "
                 f"{'is' if unscored == 1 else 'are'} drawn at the minimum height.")
    return text


def series_chart(key: str, name: str, games: list, unit_many: str = "points", words: tuple = ("game", "games")) -> str:
    """The chart block for one card (a plain div, not a landmark: seven cards would give seven same-named regions): heading, SVG, year axis, legend. Ids are prefixed with the sport's key so
    seven cards on one page never repeat an id. Returns "" when there are fewer than two games to compare."""
    if len(games) < 2:
        return ""
    k = esc(key)
    cap = margin_cap(games)
    p = paths(games)
    sw = num(bar_width(len(games)))     # the bar width, as a presentation attribute (not a style attribute)
    shapes = "".join(f'<path class="c-{kind}" stroke-width="{sw}" d="{p[kind]}"/>' for kind in ("d", "w", "t") if p[kind])
    years = axis_years(games)
    heading = (f"Every {words[0]}, {years[0]}" if len(years) == 1 else f"Every {words[0]}, {years[0]}–{years[-1]}")
    return (
        '<div class="chart-sec">\n'
        f'<h3 id="{k}-chart-h">{esc(heading)}</h3>\n'
        f'<svg class="chart" viewBox="0 0 {WIDTH} {HEIGHT}" preserveAspectRatio="none" role="img" '
        f'aria-labelledby="{k}-chart-t {k}-chart-d" focusable="false">'
        f'<title id="{k}-chart-t">{esc(name)} results, {words[0]} by {words[0]}</title>'
        f'<desc id="{k}-chart-d">{esc(description(name, games, cap, unit_many, words))}</desc>'
        f'<line class="c-base" x1="0" x2="{WIDTH}" y1="{MID}" y2="{MID}"/>{shapes}</svg>\n'
        f'<p class="chart-axis" aria-hidden="true">{"".join(f"<span>{esc(y)}</span>" for y in years)}</p>\n'
        '<ul class="chart-legend">'
        '<li><span class="sw sw-d" aria-hidden="true"></span>DePauw win</li>'
        '<li><span class="sw sw-w" aria-hidden="true"></span>Wabash win</li>'
        '<li><span class="sw sw-t" aria-hidden="true"></span>Tie</li></ul>\n'
        f'<p class="hint">One bar per {esc(words[0])}, oldest to newest. Taller means a bigger margin; a margin of '
        f'{esc(facts.num(cap))} {esc(unit_many)} or more is drawn full height.</p>\n'
        '</div>'
    )
