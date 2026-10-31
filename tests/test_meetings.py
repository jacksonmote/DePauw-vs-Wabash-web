"""Tests for upcoming / result-pending selection and how next meetings show on the page."""
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import meetings  # noqa: E402
import render  # noqa: E402
from test_render import build, football, lacrosse, basketball, write  # noqa: E402
from test_render import text_of as _text_of  # noqa: E402


def text_of(html):
    return _text_of(html).replace(" ,", ",")

TODAY = dt.date(2026, 11, 10)


def sched(date, time="1:07 PM", location="neutral", site="Scheumann Stadium, Muncie, Ind.",
          status="scheduled", note=None):
    return {"date": date, "time": time, "time_note": note, "location": location, "site": site,
            "status": status, "doubleheader": False}


def hist(*dates):
    return [{"date": d} for d in dates]


# ---- selection ----------------------------------------------------------- #

def test_games_from_today_on_are_ahead_and_earlier_ones_are_not():
    ahead, pending = meetings.split_games(
        [sched("2026-11-09"), sched("2026-11-10"), sched("2026-11-14")], hist("2026-11-09"), TODAY)
    assert [g["date"] for g in ahead] == ["2026-11-10", "2026-11-14"]
    assert pending == []  # Nov 9 is already in the history


def test_a_played_game_missing_from_the_history_is_result_pending():
    ahead, pending = meetings.split_games([sched("2026-11-09")], hist("2025-11-15"), TODAY)
    assert ahead == [] and [g["date"] for g in pending] == ["2026-11-09"]


def test_pending_is_matched_by_how_many_games_are_on_the_date():
    two = [sched("2026-11-09", "12 PM"), sched("2026-11-09", None, note="Following Game 1")]
    assert meetings.split_games(two, hist("2026-11-09"), TODAY)[1] == [two[1]]   # game 2 still missing
    assert meetings.split_games(two, hist("2026-11-09", "2026-11-09"), TODAY)[1] == []


def test_a_postponed_game_is_ahead_when_dated_ahead_but_never_pending():
    ahead, pending = meetings.split_games(
        [sched("2026-11-14", status="postponed"), sched("2026-11-02", status="postponed")], [], TODAY)
    assert [g["status"] for g in ahead] == ["postponed"] and pending == []


def test_a_result_that_never_arrives_stops_being_pending_after_two_weeks():
    assert len(meetings.split_games([sched("2026-10-27")], [], TODAY)[1]) == 1  # 14 days ago: still pending
    assert len(meetings.split_games([sched("2026-10-26")], [], TODAY)[1]) == 0  # 15 days ago: dropped


def test_next_overall_takes_the_soonest_and_skips_postponed():
    best = meetings.next_overall({
        "football": [sched("2026-11-14")],
        "mens-basketball": [sched("2026-11-12", status="postponed"), sched("2027-01-06")],
        "baseball": []})
    assert best[0] == "football"
    assert meetings.next_overall({"football": [], "baseball": []}) is None
    tie = meetings.next_overall({"a": [sched("2026-11-14")], "b": [sched("2026-11-14")]})
    assert tie[0] == "a"


def test_load_upcoming_survives_missing_or_damaged_files(tmp_path):
    assert meetings.load_upcoming(None) == {}
    assert meetings.load_upcoming(tmp_path / "nope.json") == {}
    p = tmp_path / "u.json"
    p.write_text("{not json")
    assert meetings.load_upcoming(p) == {}
    p.write_text(json.dumps({"sports": {"football": {"schedule_url": "x", "games": [
        {"date": "garbage"}, sched("2026-11-14")]}}}))
    assert [g["date"] for g in meetings.load_upcoming(p)["football"]["games"]] == ["2026-11-14"]


# ---- on the page --------------------------------------------------------- #

def page(tmp_path, upcoming, sports=None, now=dt.datetime(2026, 11, 10, 15, 0, tzinfo=dt.timezone.utc)):
    sports = sports or {"football": football(), "mens-basketball": basketball(), "mens-lacrosse": lacrosse()}
    up = tmp_path / "upcoming.json"
    up.write_text(json.dumps({"schema_version": 1, "sports": upcoming}))
    out, html = build(tmp_path, sports, now=now, upcoming_path=up)
    return html


def entry(*games):
    return {"schedule_url": "https://depauwtigers.com/sports/x/schedule", "games": list(games)}


def test_next_meeting_sits_under_the_totals_board_and_names_sport_day_time_and_place(tmp_path):
    html = page(tmp_path, {"football": entry(sched("2026-11-14"))})
    assert html.index('id="overall-h"') < html.index('id="next-meeting"') < html.index('id="jump-h"')   # board, Bell, next meeting, jump list
    top = text_of(html[html.index('id="next-meeting"'):html.index('id="jump-h"')])
    assert "Next meeting 4 days to the Bell game Football, Sat, Nov 14 · 1:07 PM · Neutral site · Scheumann Stadium, Muncie, Ind." in top   # Nov 10 to Nov 14
    assert '<time datetime="2026-11-14">' in html


def test_the_soonest_sport_wins_the_top_spot_and_every_card_shows_its_own(tmp_path):
    html = page(tmp_path, {
        "football": entry(sched("2026-11-14")),
        "mens-basketball": entry(sched("2026-11-12", "7:00 PM", "home", "Neal Fieldhouse, Greencastle, Ind."))})
    top = text_of(html[html.index('id="next-meeting"'):html.index('id="jump-h"')])
    assert "Men’s Basketball, Thu, Nov 12 · 7:00 PM · Neal Fieldhouse" in top
    football_card = html[html.index('id="football"'):html.index('id="mens-basketball"')]
    assert "Next meeting" in text_of(football_card) and "Sat, Nov 14 · 1:07 PM" in text_of(football_card)
    assert '>DePauw schedule</a>' in football_card


def test_a_game_with_no_time_says_tba_and_one_next_year_shows_the_year(tmp_path):
    html = page(tmp_path, {"football": entry(sched("2027-01-06", None, "home", None))})
    assert "Wed, Jan 6, 2027 · Time TBA · Greencastle, Ind." in text_of(html)


def test_a_postponed_game_is_shown_as_postponed_on_its_card_not_at_the_top(tmp_path):
    html = page(tmp_path, {"football": entry(sched("2026-11-14", status="postponed"))})
    assert "Sat, Nov 14 · Postponed" in text_of(html)
    assert 'id="next-meeting"' not in html  # nothing dated to count down to, so no box


def test_result_pending_replaces_the_game_that_was_played(tmp_path):
    html = page(tmp_path, {"football": entry(sched("2026-11-09"))})
    assert "Football: result pending" in text_of(html)
    assert "Result pending Mon, Nov 9" in text_of(html)
    assert "Football, Mon" not in text_of(html) and "Sat, Nov 14" not in text_of(html)


def test_a_game_today_still_counts_by_eastern_time(tmp_path):
    late = dt.datetime(2026, 11, 15, 3, 0, tzinfo=dt.timezone.utc)   # 10 PM Eastern on Nov 14
    assert "Sat, Nov 14" in text_of(page(tmp_path, {"football": entry(sched("2026-11-14"))}, now=late))
    after = dt.datetime(2026, 11, 15, 6, 0, tzinfo=dt.timezone.utc)  # 1 AM Eastern on Nov 15
    gone = text_of(page(tmp_path, {"football": entry(sched("2026-11-14"))}, now=after))
    assert "Sat, Nov 14 · 1:07 PM" not in gone and "Football: result pending" in gone


def test_off_season_shows_no_box_and_no_stray_label(tmp_path):
    html = page(tmp_path, {"football": entry(), "mens-basketball": entry()})
    assert "next-meeting" not in html and "Next meeting" not in html and "Result pending" not in html
    assert html.count('>DePauw schedule</a>') == 2  # the link stays so visitors can check for new games


def test_missing_schedule_data_never_stops_the_records_page(tmp_path):
    out, html = build(tmp_path, {"football": football()})
    assert "Next meeting" not in html and "DePauw" in html


def test_the_default_upcoming_file_is_the_one_scrape_writes():
    assert render.UPCOMING.name == "upcoming.json" and render.UPCOMING.parent.name == "data"


# ---- doubleheaders ------------------------------------------------------- #

def dh(date, *times, site="Goodrich Ballpark, Crawfordsville, IN", source="wabash"):
    return [{**sched(date, t, "away", site), "doubleheader": True, "source": source} for t in times]


def baseball_card(html):
    return text_of(html[html.index('id="baseball"'):html.index('</article>', html.index('id="baseball"'))])


def baseball_page(tmp_path, games, **kw):
    from test_render import sport, game
    sports = {"baseball": sport("baseball", "Baseball", [game("2026-04-28", "L", 3, 15)])}
    return page(tmp_path, {"baseball": entry(*games)}, sports=sports, **kw)


def test_a_doubleheader_from_one_school_is_one_entry_with_both_times(tmp_path):
    html = baseball_page(tmp_path, dh("2027-04-28", "12:00 PM", "3:30 PM"))
    top = text_of(html[html.index('id="next-meeting"'):html.index('id="jump-h"')])
    assert ("Baseball, Wed, Apr 28, 2027 · Doubleheader · 12:00 PM and 3:30 PM · "
            "Goodrich Ballpark, Crawfordsville, Ind. (from Wabash's schedule)") in top.replace("’", "'")
    card = baseball_card(html)
    assert "Next meeting Wed, Apr 28, 2027 · Doubleheader · 12:00 PM and 3:30 PM" in card
    assert card.count("Apr 28, 2027") == 1 and html.count("Doubleheader") == 2   # top box + card, never listed twice


def test_the_same_doubleheader_from_both_schools_is_still_two_games_not_four(tmp_path):
    import upcoming
    depauw = [{**g, "doubleheader": True} for g in (sched("2027-04-28", "12:00 PM", "away", "Goodrich Ballpark, Crawfordsville, Ind."),
                                                      sched("2027-04-28", None, "away", "Goodrich Ballpark, Crawfordsville, Ind.", note="Following Game 1"))]
    wabash = dh("2027-04-28", "12:00 PM", "3:30 PM")
    games, _ = upcoming.merge_schedules("Baseball", depauw, wabash)
    assert [g["source"] for g in games] == ["both", "both"] and [g["time"] for g in games] == ["12:00 PM", "3:30 PM"]
    html = baseball_page(tmp_path, games)
    assert "Doubleheader · 12:00 PM and 3:30 PM" in baseball_card(html)
    assert "from Wabash" not in baseball_card(html)        # both schools list it, so no "only Wabash" note


def test_a_doubleheader_with_one_time_missing_says_tba_for_that_game(tmp_path):
    html = baseball_page(tmp_path, dh("2027-04-28", "12:00 PM", None))
    assert "Doubleheader · 12:00 PM and TBA" in baseball_card(html)
    html = baseball_page(tmp_path, dh("2027-04-28", None, None))
    assert "Doubleheader · TBA and TBA" in baseball_card(html)


def test_three_games_on_one_date_and_a_postponed_pair_read_sensibly(tmp_path):
    assert "3 games · 12:00 PM, 2:00 PM, and 4:00 PM" in baseball_card(
        baseball_page(tmp_path, dh("2027-04-28", "12:00 PM", "2:00 PM", "4:00 PM")))
    postponed = [{**g, "status": "postponed"} for g in dh("2027-04-28", "12:00 PM", "3:30 PM")]
    card = baseball_card(baseball_page(tmp_path, postponed))
    assert "Wed, Apr 28, 2027 · Postponed" in card and "12:00 PM" not in card


def test_games_on_different_dates_stay_separate_meetings(tmp_path):
    games = dh("2027-04-28", "12:00 PM") + dh("2027-04-29", "1:00 PM")
    card = baseball_card(baseball_page(tmp_path, games))
    assert "Wed, Apr 28, 2027 · 12:00 PM" in card and "Doubleheader" not in card and "Apr 29" not in card


def test_the_top_box_groups_from_the_game_picked_when_a_postponed_one_comes_first():
    ahead = [{**sched("2026-11-12"), "status": "postponed"}] + dh("2026-11-14", "1:00 PM", "3:00 PM")
    assert meetings.next_group(ahead) == [ahead[0]]            # the postponed game is its own entry
    best = meetings.next_overall({"baseball": ahead})
    assert meetings.next_group(ahead[ahead.index(best[1]):]) == ahead[1:]
