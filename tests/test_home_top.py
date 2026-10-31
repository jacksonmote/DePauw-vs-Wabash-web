"""The top of the home page: the scoreboard with its record bar, the Monon Bell strip, the next meeting with the
Bell-week countdown, the summary sentence and the "Jump to a sport" list.

Exact numbers come from the small made-up series in test_render.py and a fixed `now`; the real data is used only for
rules that always hold."""

import datetime as dt
import itertools
import re
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import render  # noqa: E402
from test_meetings import entry, page, sched  # noqa: E402
from test_render import build, football, lacrosse  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
EST = dt.timezone(dt.timedelta(hours=-5))            # Eastern standard time, in effect for every date below


def eastern(y, m, d, hh=12, mm=0):
    return dt.datetime(y, m, d, hh, mm, tzinfo=EST).astimezone(dt.timezone.utc)


def soup(html):
    return BeautifulSoup(html, "html.parser")


# --- record bar widths --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("counts", [[541, 20, 357], [59, 9, 63], [12, 0, 0], [0, 0, 5], [1, 1, 1], [3, 0, 7]])
def test_bar_widths_sum_to_exactly_100(counts):
    widths = render.record_widths(counts)
    assert sum(widths) == 100 and all(0 <= w <= 100 for w in widths)
    assert all(c > 0 or w == 0 for w, c in zip(widths, counts))                 # an empty count never gets a share


def test_bar_widths_for_the_real_totals_and_for_empty_and_single_cases():
    assert render.record_widths([541, 20, 357]) == [59, 2, 39]
    assert render.record_widths([12, 0, 0]) == [100, 0, 0]
    assert render.record_widths([0, 0, 0]) == [0, 0, 0]


def test_bar_widths_always_sum_to_100_over_a_grid_of_records():
    for a, b, c in itertools.product([0, 1, 2, 7, 59, 133, 541], repeat=3):
        if a + b + c:
            assert sum(render.record_widths([a, b, c])) == 100, (a, b, c)


def test_a_record_bar_uses_only_width_classes_in_order_and_omits_empty_segments():
    bar = render.record_bar(541, 20, 357)
    assert bar.count("<span") == 3 and 'aria-hidden="true"' in bar and "style" not in bar
    assert re.findall(r'class="seg (seg-[dtw]) (w-\d+)"', bar) == [("seg-d", "w-59"), ("seg-t", "w-2"), ("seg-w", "w-39")]
    assert re.findall(r'class="seg (seg-[dtw]) (w-\d+)"', render.record_bar(12, 0, 0)) == [("seg-d", "w-100")]
    assert render.record_bar(0, 0, 0) == '<div class="rbar" aria-hidden="true"></div>'


def test_every_width_class_from_0_to_100_is_defined_once_and_nothing_inline_is_needed():
    for n in range(101):
        assert len(re.findall(rf"\.w-{n}\{{width:{n}%\}}", CSS)) == 1, n
    assert ".w-101" not in CSS


# --- the board ----------------------------------------------------------------------------------------------------

def test_the_board_reads_wins_ties_wins_with_the_record_bar_as_its_bottom_strip(tmp_path):
    _, html = build(tmp_path, {"football": football(), "mens-lacrosse": lacrosse()})
    board = soup(html).select_one("section.totals .board")
    assert [s.get_text(" ") for s in board.select(".board-side")] == ["DePauw 5 wins", "Ties 1 tie", "Wabash 1 win"]
    assert board.select_one(".board-num").get_text() == "5"
    assert board.find_all(recursive=False)[-1]["class"][0] == "rbar"                 # the bar closes the board
    assert re.findall(r"w-(\d+)", str(board.select_one(".rbar"))) == ["72", "14", "14"]
    assert "7 games across 2 sports" in html and '<h2 id="overall-h">All head-to-head sports</h2>' in html


def test_the_board_has_no_ties_column_when_there_are_no_ties():
    html = render.board_html(5, 3, 0)
    assert "board-t" not in html and "has-ties" not in html and html.count("board-side") == 2


# --- order and ids ----------------------------------------------------------------------------------------------------

def test_the_home_top_order_is_board_bell_next_meeting_summary_jump_list_note_then_the_cards(tmp_path):
    html = page(tmp_path, {"football": entry(sched("2026-11-14"))})
    main = soup(html).find("main")
    kids = [(c.name, c.get("id") or " ".join(c.get("class", []))) for c in main.find_all(recursive=False)]
    names = [k[1] for k in kids]
    assert names[:6] == ["totals", "bell-strip", "next-meeting", "series-summary", "jumps",
                         "not-included"]
    assert [k[0] for k in kids[6:9]] == ["article", "article", "div"] or kids[6][0] == "article"


def test_the_game_day_banner_stays_above_the_board(tmp_path):
    up = {"football": entry(sched("2026-11-14"))}
    html = page(tmp_path, up, now=eastern(2026, 11, 14, 13, 30))
    assert html.index('id="game-day"') < html.index('id="overall-h"') < html.index('id="bell-strip"') < html.index('id="next-meeting"')
    first = soup(html).find("main").find_all(recursive=False)[0]
    assert first.get("id") == "game-day"


def test_ids_stay_unique_and_the_full_bell_block_keeps_monon_bell(tmp_path):
    html = page(tmp_path, {"football": entry(sched("2026-11-14"))})
    ids = [e["id"] for e in soup(html).find_all(id=True)]
    assert len(ids) == len(set(ids))
    assert ids.count("monon-bell") == 1 and "bell-strip" in ids
    assert soup(html).find(id="monon-bell").find_parent("article")["id"] == "football"    # still on the football card
    assert soup(html).select_one('.bell-strip a[href="#monon-bell"]')


def test_the_bell_strip_names_the_holder_the_run_and_the_record(tmp_path):
    _, html = build(tmp_path, {"football": football(), "mens-lacrosse": lacrosse()})
    strip = soup(html).select_one("section.bell-strip")
    text = strip.get_text(" ", strip=True)
    assert strip.h2.get_text() == "The Monon Bell"
    assert "holds the Bell since" in text and "Bell record since 1932:" in text
    assert "DePauw 3" in text and "Wabash 1" in text and "1 tie" in text           # the five made-up games, all Bell era


def test_no_bell_strip_without_a_football_bell_era(tmp_path):
    _, html = build(tmp_path, {"mens-lacrosse": lacrosse()})
    assert 'id="bell-strip"' not in html and soup(html).select_one("section.bell-strip") is None


# --- the jump list ---------------------------------------------------------------------------------------------------

def test_the_jump_list_has_one_row_per_sport_linking_to_its_card(tmp_path):
    _, html = build(tmp_path, {"football": football(), "mens-lacrosse": lacrosse()})
    doc = soup(html)
    rows = doc.select("nav.jumps a.jump")
    assert [r["href"] for r in rows] == ["#football", "#mens-lacrosse"]
    for r in rows:
        assert doc.find("article", id=r["href"][1:])                                 # every link lands on a card
    football_row = rows[0]
    assert football_row.select_one(".jn").get_text() == "Football"
    assert football_row.select_one(".jr").get_text() == "3–1–1" and football_row.select_one(".jr")["aria-hidden"] == "true"
    assert football_row.select_one(".sr-only").get_text() == "DePauw 3, Wabash 1, 1 tie"
    assert football_row.select_one(".jl").get_text() == "DePauw leads"
    assert rows[1].select_one(".jr").get_text() == "2–0–0" and "w-100" in str(rows[1])
    assert doc.select_one("nav.jumps .jump-hint").get_text() == "Records read DePauw–Wabash–ties."


def test_an_even_sport_says_even():
    assert render.lead_label({"depauw_wins": 4, "wabash_wins": 4, "ties": 1}) == "Even"
    assert render.lead_label({"depauw_wins": 4, "wabash_wins": 5, "ties": 0}) == "Wabash leads"


def test_the_jump_list_covers_every_sport_on_the_real_data():
    data = render.load(ROOT / "data" / "series.json")
    doc = soup(render.render_index(data, now=eastern(2026, 11, 1), upcoming={}))
    assert [r["href"][1:] for r in doc.select("nav.jumps a.jump")] == list(data["sports"])
    assert all(doc.find("article", id=k) and doc.find(id=k + "-h") for k in data["sports"])      # old #sport links still land
    assert all("style=" not in str(r) for r in doc.select("nav.jumps"))


# --- the Bell-week countdown -------------------------------------------------------------------------------------------

def countdown(html):
    node = soup(html).select_one("#next-meeting .countdown")
    return node.get_text(" ", strip=True) if node else None


FOOTBALL_NOV14 = {"football": entry(sched("2026-11-14"))}


@pytest.mark.parametrize("today,expected", [
    ((2026, 11, 7), "7 days to the Bell game"),         # the first day of Bell week
    ((2026, 11, 13), "1 day to the Bell game"),         # singular
    ((2026, 11, 14), "Today is the Bell game"),         # game day, when the banner shows too
    ((2026, 11, 10), "4 days to the Bell game"),
])
def test_the_countdown_shows_for_the_football_game_within_seven_days(tmp_path, today, expected):
    html = page(tmp_path, FOOTBALL_NOV14, now=eastern(*today))
    assert countdown(html) == expected
    assert "next-meeting-bell" in soup(html).find(id="next-meeting")["class"]
    assert "Sat, Nov 14 · 1:07 PM" in soup(html).find(id="next-meeting").get_text()        # the date is always shown
    assert "hour" not in soup(html).find(id="next-meeting").get_text().lower()


def test_no_countdown_eight_days_out_or_when_there_is_no_game_ahead(tmp_path):
    for today in ((2026, 11, 6), (2026, 11, 1)):
        html = page(tmp_path, FOOTBALL_NOV14, now=eastern(*today))
        assert countdown(html) is None and soup(html).find(id="next-meeting")["class"] == ["next-meeting"]
        assert "Sat, Nov 14 · 1:07 PM" in html
    html = page(tmp_path, {}, now=eastern(2026, 11, 12))                              # no schedule at all
    assert 'id="next-meeting"' not in html and countdown(html) is None


def test_the_countdown_counts_eastern_calendar_days_not_hours(tmp_path):
    # 10:30 PM Nov 13 Eastern is already Nov 14 in UTC: still 1 day to go, and 12:30 AM Nov 14 Eastern is game day.
    assert countdown(page(tmp_path, FOOTBALL_NOV14, now=eastern(2026, 11, 13, 22, 30))) == "1 day to the Bell game"
    assert countdown(page(tmp_path, FOOTBALL_NOV14, now=eastern(2026, 11, 14, 0, 30))) == "Today is the Bell game"
    assert countdown(page(tmp_path, FOOTBALL_NOV14, now=eastern(2026, 11, 7, 0, 5))) == "7 days to the Bell game"


def test_the_countdown_is_only_for_a_football_game_that_is_the_next_meeting(tmp_path):
    basketball_first = {"football": entry(sched("2026-11-14")),
                        "mens-basketball": entry(sched("2026-11-12", "7:00 PM", "home", "Neal Fieldhouse, Greencastle, Ind."))}
    html = page(tmp_path, basketball_first, now=eastern(2026, 11, 10))
    assert countdown(html) is None and "Men’s Basketball, Thu, Nov 12" in soup(html).find(id="next-meeting").get_text()
    only_basketball = {"mens-basketball": entry(sched("2026-11-12", "7:00 PM", "home", "Neal Fieldhouse, Greencastle, Ind."))}
    assert countdown(page(tmp_path, only_basketball, now=eastern(2026, 11, 10))) is None
    postponed = {"football": entry(sched("2026-11-14", status="postponed"))}
    assert countdown(page(tmp_path, postponed, now=eastern(2026, 11, 10))) is None


def test_the_countdown_window_is_one_named_constant():
    assert render.BELL_WEEK_DAYS == 7
    best = ("football", {"date": "2026-11-14"})
    assert render.bell_week_days(best, dt.date(2026, 11, 7)) == 7
    assert render.bell_week_days(best, dt.date(2026, 11, 6)) is None
    assert render.bell_week_days(best, dt.date(2026, 11, 15)) is None                 # the game has been played
    assert render.bell_week_days(None, dt.date(2026, 11, 7)) is None
