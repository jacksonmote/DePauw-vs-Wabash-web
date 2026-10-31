"""The sport cards: the card scoreboard with its record bar, the two fact groups, the Bell block's record bar and
the build-time every-game chart.

Exact numbers come from small made-up games and a fixed `now`; the real data is used only for rules that always
hold."""

import collections
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import chart  # noqa: E402
import facts  # noqa: E402
import render  # noqa: E402
from test_render import basketball, build, football, game, lacrosse, sport  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")


def card_of(html, key):
    return BeautifulSoup(html, "html.parser").find("article", id=key)


def bars(d):
    """The number of bars in a path's data (each starts with M)."""
    return d.count("M")


def path_d(svg, cls):
    el = svg.find("path", class_=cls)
    return el["d"] if el else ""


def xs(d):
    return [float(m) for m in re.findall(r"M(-?[\d.]+) ", d)]


# --- the card -------------------------------------------------------------------------------------------------

def test_a_card_has_the_scoreboard_a_lead_line_and_two_groups_with_every_old_label(tmp_path):
    _, html = build(tmp_path, {"football": football()})
    c = card_of(html, "football")
    assert c.find("div", class_="board board-sport")
    assert re.findall(r'class="seg (seg-[dtw]) (w-\d+)"', str(c.find("div", class_="rbar-board"))) == [
        ("seg-d", "w-60"), ("seg-t", "w-20"), ("seg-w", "w-20")]                 # 3 wins, 1 tie, 1 loss of 5
    assert c.find("p", class_="lead-line").get_text() == "DePauw leads the series by 2 wins."
    groups = {h.get_text(): [dt.get_text() for dt in h.parent.find_all("dt")] for h in c.select(".groups h3")}
    assert groups["Latest and next"] == ["Most recent"]
    assert groups["The series"] == ["Current streak", "Records since", "Games played", "Total points"]


def test_the_lead_line_is_computed_from_the_record(tmp_path):
    ahead = sport("football", "Football", [game("2020-11-07", "W", 3, 0), game("2021-11-06", "W", 3, 0),
                                           game("2022-11-05", "L", 0, 3)])
    behind = sport("mens-lacrosse", "Men's Lacrosse", [game("2024-04-06", "L", 1, 2), game("2025-04-05", "L", 1, 2)])
    even = sport("mens-basketball", "Men's Basketball", [game("2024-02-01", "W", 3, 2), game("2025-02-01", "L", 2, 3)])
    _, html = build(tmp_path, {"football": ahead, "mens-lacrosse": behind, "mens-basketball": even})
    lines = {k: card_of(html, k).find("p", class_="lead-line").get_text() for k in ("football", "mens-lacrosse", "mens-basketball")}
    assert lines == {"football": "DePauw leads the series by 1 win.", "mens-lacrosse": "Wabash leads the series by 2 wins.",
                     "mens-basketball": "The series is even."}


def test_card_headings_keep_their_levels_and_anchors(tmp_path):
    _, html = build(tmp_path, {"football": football()})
    c = card_of(html, "football")
    assert c.find("h2").find("a", id="football-h", href="#football")             # the existing permalink, unchanged
    assert [h.name for h in c.find_all(["h1", "h2", "h3"])][:1] == ["h2"] and not c.find("h1")
    assert c.find("section", id="monon-bell") and c.find("h3", id="football-chart-h")


def test_the_bell_block_keeps_its_record_as_a_bar_with_text_and_keeps_the_heist_link(tmp_path):
    games = [game("1999-11-06", "W", 7, 3), game("2000-11-04", "L", 3, 7), game("2001-11-03", "T", 7, 7)]
    _, html = build(tmp_path, {"football": sport("football", "Football", games)})
    bell = card_of(html, "football").find("section", id="monon-bell")
    assert "Bell record since 1932" in bell.get_text()
    assert bell.find("p", class_="bell-rec").get_text() == "DePauw 1 · Wabash 1 · 1 tie"
    assert bell.find("div", class_="rbar")["aria-hidden"] == "true"
    assert bell.find("a", class_="bell-heist-link")["href"] == "bell-heist/"


def test_no_id_repeats_on_the_home_page_even_with_every_card_charted(tmp_path):
    _, html = build(tmp_path, {"football": football(), "mens-lacrosse": lacrosse(), "mens-basketball": basketball()})
    ids = re.findall(r' id="([^"]+)"', html)
    assert [k for k, n in collections.Counter(ids).items() if n > 1] == []
    for key in ("football", "mens-lacrosse"):
        assert {f"{key}-chart-h", f"{key}-chart-t", f"{key}-chart-d", f"{key}-latest-h", f"{key}-series-h"} <= set(ids)


def test_cards_use_no_inline_styles_or_scripts(tmp_path):
    _, html = build(tmp_path, {"football": football(), "mens-lacrosse": lacrosse()})
    for article in BeautifulSoup(html, "html.parser").find_all("article"):
        assert not article.find(style=True) and not article.find("script") and not article.find("style")


# --- the chart ---------------------------------------------------------------------------------------------------

def test_one_bar_per_game_in_three_paths_and_a_tie_is_a_short_mark(tmp_path):
    _, html = build(tmp_path, {"football": football()})
    svg = card_of(html, "football").find("svg", class_="chart")
    assert len(svg.find_all("path")) == 3
    assert (bars(path_d(svg, "c-d")), bars(path_d(svg, "c-w")), bars(path_d(svg, "c-t"))) == (3, 1, 1)
    tie = path_d(svg, "c-t")
    assert tie.endswith(f"v{chart.TIE_BAR}")                                     # the tie mark's own height, not a margin


def test_bars_are_placed_by_game_not_by_season_so_a_doubleheader_shows_both_games():
    games = [game("2024-04-06", "W", 5, 1), game("2024-04-06", "L", 1, 5), game("2024-04-07", "W", 9, 1)]
    p = chart.paths(games)
    positions = xs(p["d"]) + xs(p["w"])
    assert len(set(positions)) == 3                                               # three games, three places


def test_bar_height_grows_with_margin_and_is_capped_by_the_sports_own_scale():
    games = [game(f"2020-04-{d:02d}", "W", 10 + d, 10) for d in range(1, 21)] + [game("2021-04-01", "W", 99, 0)]
    cap = chart.margin_cap(games)
    assert cap < 99 and cap >= 1                                                  # the blowout does not set the scale
    small, big, huge = (chart.bar_height(g, cap) for g in (games[0], games[19], games[-1]))
    assert chart.MIN_BAR <= small < big <= huge == chart.HALF


def test_the_scale_is_data_derived_not_football_shaped():
    tennis = [game(f"2020-04-{d:02d}", "W", 5 + d % 4, 4 - d % 4) for d in range(1, 21)]      # margins up to 9
    assert chart.margin_cap(tennis) <= 9 and chart.margin_cap([game("2020-04-01", "T", 2, 2)]) == 1.0


def test_a_game_with_no_score_is_drawn_at_the_minimum_height_and_the_text_says_so(tmp_path):
    unscored = dict(game("1934-02-01", "W", 0, 0), score_known=False)
    games = [unscored, game("1935-02-01", "W", 40, 0), game("1936-02-01", "L", 3, 30)]
    assert chart.bar_height(unscored, chart.margin_cap(games)) == chart.MIN_BAR
    desc = chart.description("Swimming", games, chart.margin_cap(games), "points", ("meet", "meets"))
    assert "1 meet has no recorded score and is drawn at the minimum height." in desc


def test_the_chart_uses_the_sports_own_words_and_the_years_come_from_the_games(tmp_path):
    tennis = sport("mens-tennis", "Men's Tennis", [game("2024-04-06", "W", 9, 0), game("2025-04-05", "W", 7, 2),
                                                   game("2026-04-04", "W", 5, 4)], total_label="individual matches")
    tennis["unit"] = ["match", "matches"]
    _, html = build(tmp_path, {"mens-tennis": tennis, "football": football()})
    section = card_of(html, "mens-tennis").find("div", class_="chart-sec")
    text = section.get_text(" ")
    assert "Every match, 2024–2026" in text and "one bar for each of 3 matches" in text
    assert re.search(r"\bgames?\b", text, re.I) is None
    assert [s.get_text() for s in section.select(".chart-axis span")] == ["2024", "2025", "2026"]
    football_text = card_of(html, "football").find("div", class_="chart-sec").get_text(" ")
    assert "Every game, 1999–2004" in football_text


def test_axis_years_are_evenly_spaced_by_game_and_never_repeat():
    games = [game(f"{y}-10-01", "W", 3, 1) for y in range(1990, 2030)]
    years = chart.axis_years(games)
    assert years[0] == "1990" and years[-1] == "2029" and len(years) == chart.AXIS_LABELS == len(set(years))
    assert chart.axis_years([game("2020-01-01", "W", 1, 0), game("2020-06-01", "W", 1, 0)]) == ["2020"]


def test_a_chart_needs_two_games(tmp_path):
    assert chart.series_chart("mens-basketball", "Men's Basketball", [game("2025-02-01", "L", 60, 71)]) == ""
    _, html = build(tmp_path, {"mens-basketball": basketball(), "football": football()})
    assert card_of(html, "mens-basketball").find("svg") is None and card_of(html, "football").find("svg")


def test_the_accessible_description_carries_the_counts(tmp_path):
    _, html = build(tmp_path, {"football": football()})
    svg = card_of(html, "football").find("svg", class_="chart")
    assert svg["role"] == "img" and svg["aria-labelledby"] == "football-chart-t football-chart-d"
    desc = svg.find("desc").get_text()
    assert "one bar for each of 5 games" in desc and "DePauw wins (3)" in desc and "Wabash wins (1)" in desc and "ties (1)" in desc


def test_chart_colors_come_from_the_same_tokens_that_the_contrast_test_checks():
    for cls, token in (("c-d", "depauw-accent"), ("c-w", "wabash-accent"), ("c-t", "tie-accent")):
        assert re.search(rf"\.{cls} \{{ stroke: var\(--color-{token}\); fill: none; \}}", CSS), cls
    assert "forced-colors: active" in CSS and "vector-effect: non-scaling-stroke" in CSS


def test_the_real_cards_chart_every_game_once():
    import json
    data = json.loads((ROOT / "data" / "series.json").read_text(encoding="utf-8"))["sports"]
    for key, s in data.items():
        p = chart.paths(s["games"])
        assert sum(bars(d) for d in p.values()) == len(s["games"]), key
        assert facts.game_year(s["games"][0]) <= facts.game_year(s["games"][-1]), key


# --- alumni: record bars and the 44 px correction link ------------------------------------------------------------

def alumni_pages():
    import alumni
    from test_alumni import page_for, read_data, read_endowment
    data, endow = alumni.validate_alumni(read_data()), alumni.validate_endowment(read_endowment())
    return alumni, data, endow, page_for(data, endow)


def test_scoreboard_rows_get_a_bar_except_empty_categories_and_the_about_even_endowment_row():
    alumni, data, endow, page = alumni_pages()
    assert alumni.about_even(endow)
    rows = BeautifulSoup(page, "html.parser").select("table.scoreboard tbody tr")
    by_id = {r.find("a")["href"][1:]: r for r in rows}
    assert by_id["endowment"].find("div", class_="rbar") is None                   # "about even": no bar showing a gap
    counts = alumni.counts(data)
    for cid, c in counts.items():
        bar = by_id[cid].find("div", class_="rbar")
        assert (bar is not None) == bool(c["depauw"] or c["wabash"]), cid
        if bar:
            assert bar["aria-hidden"] == "true" and "style" not in str(bar)


def test_a_higher_endowment_figure_gets_a_bar_and_no_total_bar_exists():
    import alumni
    from test_alumni import page_for, read_data, read_endowment
    e = read_endowment()
    e["schools"]["depauw"]["enrollment"]["count"] = 1500                           # far apart: not "about even"
    page = page_for(alumni.validate_alumni(read_data()), alumni.validate_endowment(e))
    soup = BeautifulSoup(page, "html.parser")
    row = [r for r in soup.select("table.scoreboard tbody tr") if r.find("a")["href"] == "#endowment"][0]
    assert row.find("div", class_="rbar")
    assert soup.select_one("table.scoreboard tfoot") is None                       # there is deliberately no overall score
    # every bar sits in a scoreboard row, a category card or the endowment card, never on its own in the overview
    overview = soup.find("section", class_="alumni-overview")
    assert all(b.find_parent("table") for b in overview.select("div.rbar"))


def test_each_category_card_and_the_endowment_card_have_a_bar_after_their_tiles():
    alumni, data, endow, page = alumni_pages()
    soup = BeautifulSoup(page, "html.parser")
    counts = alumni.counts(data)
    for card in soup.select("section.alumni-card"):
        c = counts[card["id"]]
        nxt = card.find("div", class_="tiles").find_next_sibling()
        assert (nxt.get("class") == ["rbar"]) == bool(c["depauw"] or c["wabash"]), card["id"]
    assert soup.select_one("section.endowment > div.rbar") is None                 # about even: no bar


def test_the_correction_link_is_a_44px_target():
    assert re.search(r"\.alumni-report a \{[^}]*min-height: var\(--tap\)", CSS)
    assert "--tap: 44px" in CSS


def test_the_shared_bar_module_is_what_render_and_alumni_use():
    import alumni, bars
    assert render.record_bar is bars.record_bar and render.record_widths is bars.record_widths and alumni.bars is bars
