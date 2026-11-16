"""Tests for the Monon Bell numbers and how the block appears in the football card."""
import re
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import facts  # noqa: E402
import render  # noqa: E402
import scrape  # noqa: E402
from test_meetings import entry, page, sched  # noqa: E402
from test_render import football  # noqa: E402
from test_scrape import BASE, full_football_html  # noqa: E402


def real_football():
    """All 131 football games, parsed from the page markup captured on 2026-10-27."""
    games, _, _ = scrape.parse_page(full_football_html(), base_url=BASE, opponent_match="wabash")
    return [asdict(g) for g in scrape.chronological(games)]


def g(year, result, d=10, w=7):
    return {"date": f"{year}-11-15", "season": str(year), "location": "home", "site": None,
            "result": result, "depauw": d, "wabash": w, "note": None, "boxscore": None}


# ---- the standings --------------------------------------------------------- #

def test_bell_record_matches_wikipedia_through_the_2025_season():
    b = facts.bell(real_football())
    assert (b["wabash"], b["depauw"], b["ties"]) == (44, 43, 6)
    assert b["games"] == 93


def test_games_before_1932_are_not_bell_games():
    games = [g(1931, "W"), g(1932, "T"), g(1933, "L")]
    b = facts.bell(games)
    assert (b["depauw"], b["wabash"], b["ties"], b["games"]) == (0, 1, 1, 2)


def test_depauw_has_held_the_bell_since_2022():
    b = facts.bell(real_football())
    assert (b["holder"], b["since"], b["held_games"]) == ("DePauw", 2022, 4)
    assert (b["held_wins"], b["held_ties"]) == (4, 0)


def test_a_tie_leaves_the_bell_where_it_was_and_counts_toward_the_hold():
    b = facts.bell([g(2018, "L"), g(2019, "W"), g(2020, "T"), g(2021, "T"), g(2022, "W")])
    assert (b["holder"], b["since"], b["held_games"]) == ("DePauw", 2019, 4)
    assert (b["held_wins"], b["held_ties"]) == (2, 2)


def test_ties_before_the_first_win_do_not_start_the_hold():
    b = facts.bell([g(2018, "L"), g(2019, "T"), g(2020, "W")])
    assert (b["holder"], b["since"], b["held_games"]) == ("DePauw", 2020, 1)


def test_wabash_can_hold_it_and_a_late_tie_does_not_take_it_away():
    b = facts.bell([g(2018, "W"), g(2019, "L"), g(2020, "L"), g(2021, "T")])
    assert (b["holder"], b["since"], b["held_games"]) == ("Wabash", 2019, 3)


def test_no_decided_game_means_no_holder():
    b = facts.bell([g(1932, "T"), g(1933, "T")])
    assert b["holder"] is None and b["ties"] == 2 and b["games"] == 2
    assert facts.bell([g(1920, "W")]) is None


def test_held_text_gives_only_the_win_count_when_the_run_has_no_ties():
    assert render.bell_held_text(facts.bell(real_football())) == "4 straight wins"
    assert render.bell_held_text(facts.bell([g(2018, "L"), g(2019, "W")])) == "1 win"


def test_held_text_says_so_when_a_tie_kept_the_bell_with_the_holder():
    b = facts.bell([g(2018, "L"), g(2019, "W"), g(2020, "W"), g(2021, "W"), g(2022, "W"), g(2023, "T")])
    assert render.bell_held_text(b) == "5 games: 4 wins and 1 tie"
    mid = facts.bell([g(2018, "L"), g(2019, "W"), g(2020, "T"), g(2021, "T"), g(2022, "W")])
    assert render.bell_held_text(mid) == "4 games: 2 wins and 2 ties"


# ---- the block on the page ------------------------------------------------- #

def block(html):
    start = html.index('<section class="bell"')
    return html[start:html.index("</section>", start)]


def test_bell_block_sits_in_the_football_card_above_the_game_list(tmp_path):
    html = page(tmp_path, {})
    card = html[html.index('id="football"'):html.index("</article>", html.index('id="football"'))]
    assert card.index('id="monon-bell"') < card.index('<details class="games">')
    assert "mens-lacrosse" not in block(html) and html.count('id="monon-bell"') == 1


def test_bell_block_shows_record_holder_and_the_wikipedia_link(tmp_path):
    # made-up football(): 1999 tie, 2001 L, then three DePauw wins (2002 to 2004)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", block(page(tmp_path, {}))))
    assert "DePauw has held the Bell since 2002 (3 straight wins)" in text
    assert "Bell record since 1932" in text
    assert "Last Bell game" not in text and "Next Bell game" not in text
    html = block(page(tmp_path, {}))
    assert '<a href="https://en.wikipedia.org/wiki/Monon_Bell">Read more on Wikipedia</a>' in html


def test_bell_games_appear_only_in_the_football_cards_own_stats(tmp_path):
    html = page(tmp_path, {"football": entry(sched("2026-11-14"))})
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", block(html)))
    assert "Nov 14" not in text and "Nov 13, 2004" not in text
    card = html[html.index('id="football"'):html.index("</article>", html.index('id="football"'))]
    assert "Most recent" in card and "Next meeting" in card


def test_the_hand_written_history_has_no_changing_numbers_and_stays_short():
    numbers = set(re.findall(r"\d+", render.BELL_HISTORY))
    # the bell's weight, the year it became a trophy and the year overtime ended ties
    assert numbers == {str(render.BELL_WEIGHT_POUNDS), str(facts.BELL_START), str(render.OVERTIME_START)}
    sentences = [s for s in re.split(r"(?<=\.) ", render.BELL_HISTORY) if s]
    assert 3 <= len(sentences) <= 5
    assert "tie" in render.BELL_HISTORY and "stolen" in render.BELL_HISTORY
    # the tie rule is told as history: no Bell game has been able to end in a tie since overtime came in
    assert render.OVERTIME_START == 1996
    assert f"Before overtime came to college football in {render.OVERTIME_START}, a tied game left" in render.BELL_HISTORY
    assert "ends in a tie" not in render.BELL_HISTORY and "keeps it" not in render.BELL_HISTORY


# ---- fun facts ------------------------------------------------------------- #

def gm(date, result, d, w, note=None, season=None):
    return {"date": date, "season": season or date[:4], "location": "home", "site": None,
            "result": result, "depauw": d, "wabash": w, "note": note, "boxscore": None}


def test_biggest_win_uses_the_widest_margin_and_the_latest_game_on_a_tie():
    games = [gm("2000-11-01", "W", 30, 0), gm("2010-11-01", "W", 40, 10), gm("2005-11-01", "L", 3, 20),
             gm("2012-11-01", "T", 7, 7)]
    assert facts.biggest_win(games, "W")["date"] == "2010-11-01"          # 30-0 and 40-10 both by 30
    assert facts.biggest_win(games, "L")["date"] == "2005-11-01"
    assert facts.biggest_win([gm("2000-11-01", "T", 7, 7)], "W") is None   # a tie is nobody's win


def test_longest_streak_is_broken_by_ties_and_prefers_the_recent_one_when_equal():
    games = [gm(f"{y}-11-01", r, 10, 5 if r == "W" else 20)
             for y, r in [(2000, "W"), (2001, "W"), (2002, "T"), (2003, "W"), (2004, "W"), (2005, "L")]]
    assert facts.longest_streak(games, "W") == {"games": 2, "first": "2003", "last": "2004", "times": 2}
    assert facts.longest_streak(games, "L") is None                         # a single loss is not a streak
    assert facts.longest_streak([], "W") is None


def streak_games(spans, result="L"):
    """A game per year inside each (first, last) span, with a DePauw win between them to end the streak."""
    out = []
    for first, last in spans:
        out += [gm(f"{y}-11-01", result, 10, 20) if result == "L" else gm(f"{y}-11-01", result, 20, 10)
                for y in range(first, last + 1)]
        out.append(gm(f"{last + 1}-11-01", "W" if result == "L" else "L", 20 if result == "L" else 10,
                      10 if result == "L" else 20))
    return out


def test_a_tied_record_streak_is_counted_and_dated_by_the_latest_one():
    games = streak_games([(1921, 1927), (1950, 1952), (2009, 2015)])
    assert facts.longest_streak(games, "L") == {"games": 7, "first": "2009", "last": "2015", "times": 2}
    three = streak_games([(1921, 1927), (1960, 1966), (2009, 2015)])
    assert facts.longest_streak(three, "L")["times"] == 3
    assert facts.longest_streak(streak_games([(1921, 1927), (2009, 2012)]), "L")["times"] == 1


def test_streak_line_for_a_tie_for_the_record(tmp_path):
    games = streak_games([(1921, 1927), (2009, 2015)])
    text = fun_text(tmp_path, "football", sport_of("football", "Football", games))
    assert "Longest Wabash win streak: 7 straight (twice, most recently 2009–2015)" in text
    three = fun_text(tmp_path, "football", sport_of("football", "Football", streak_games([(1921, 1927), (1960, 1966), (2009, 2015)])))
    assert "Longest Wabash win streak: 7 straight (3 times, most recently 2009–2015)" in three
    one = fun_text(tmp_path, "football", sport_of("football", "Football", streak_games([(1921, 1927), (2009, 2012)])))
    assert "Longest Wabash win streak: 7 straight · 1921–1927" in one


def test_one_score_margins_per_sport_and_ties_do_not_count():
    games = [gm("2000-11-01", "W", 24, 16), gm("2001-11-01", "L", 17, 26), gm("2002-11-01", "W", 20, 13),
             gm("2003-11-01", "T", 7, 7)]
    assert [x["date"] for x in facts.one_score_games("football", games)] == ["2000-11-01", "2002-11-01"]
    assert facts.one_score_games("mens-lacrosse", games) == []
    assert [x["date"] for x in facts.one_score_games("mens-basketball", [gm("2000-01-01", "W", 70, 67),
                                                                           gm("2001-01-01", "W", 70, 66)])] == ["2000-01-01"]
    assert facts.one_score_games("curling", games) == []                    # unknown sport: no guess


def test_highest_scoring_and_overtime_and_extra_innings():
    games = [gm("2000-02-01", "W", 80, 70), gm("2001-02-01", "L", 75, 75 + 5, note="(1ot)"),
             gm("2002-02-01", "W", 85, 65), gm("2003-02-01", "W", 60, 59, note="2OT"),
             gm("2004-02-01", "W", 70, 60, note="3 OT")]
    assert facts.highest_scoring(games)["date"] == "2001-02-01"             # 155 combined
    assert [x["date"] for x in facts.overtime_games(games)] == ["2001-02-01", "2003-02-01", "2004-02-01"]
    ball = [gm("2000-04-01", "W", 5, 4, note="(7 inn.)"), gm("2001-04-01", "W", 5, 4, note="(10 inn.)"),
            gm("2002-04-01", "W", 5, 4, note="(9 inn.)"), gm("2003-04-01", "W", 5, 4)]
    assert [x["date"] for x in facts.extra_inning_games(ball)] == ["2001-04-01"]   # 7 inn. is a short game
    assert facts.overtime_games(ball) == []


def test_season_by_season_only_for_sports_that_meet_more_than_once_a_year():
    two_a_year = [gm("2024-01-06", "W", 70, 60, season="2023-24"), gm("2024-02-03", "W", 71, 60, season="2023-24"),
                  gm("2025-01-04", "W", 70, 60, season="2024-25"), gm("2025-02-01", "L", 60, 70, season="2024-25"),
                  gm("2026-01-03", "L", 60, 70, season="2025-26"), gm("2026-02-07", "L", 60, 70, season="2025-26"),
                  gm("2027-01-03", "T", 60, 60, season="2026-27"), gm("2027-02-07", "W", 61, 60, season="2026-27")]
    assert facts.season_series(two_a_year) == [("2026-27", "DePauw 1-0-1"), ("2025-26", "Wabash 2-0"),
                                              ("2024-25", "Split 1-1"), ("2023-24", "DePauw 2-0")]
    assert facts.season_series(real_football()) == []                       # two doubles in 129 seasons


# ---- on the page ----------------------------------------------------------- #

def fun_text(tmp_path, key, sport):
    from test_render import text_of
    html = render_page(tmp_path, {key: sport})
    start = html.index('<div class="fun-facts">')
    return text_of(html[start:html.index("</div>", start)]).replace(" ,", ",")


def render_page(tmp_path, sports):
    from test_render import build
    return build(tmp_path, sports)[1]


def sport_of(key, name, games, label=None):
    from test_render import sport
    return sport(key, name, games, label)


def test_a_card_lists_its_fun_facts_from_its_own_games(tmp_path):
    games = [gm("2000-02-01", "W", 80, 70), gm("2001-02-01", "L", 75, 80, note="(1ot)"),
             gm("2002-02-01", "W", 85, 65), gm("2003-02-01", "W", 60, 59, note="2OT")]
    text = fun_text(tmp_path, "mens-basketball", sport_of("mens-basketball", "Men's Basketball", games))
    assert "Biggest DePauw win: DePauw 85, Wabash 65 · Feb 1, 2002 · by 20 points" in text
    assert "Biggest Wabash win: Wabash 80, DePauw 75 · Feb 1, 2001 · by 5 points" in text
    assert "Longest DePauw win streak: 2 straight · 2002–2003" in text
    assert "One-score games: 1 decided by 3 points or fewer · most recent: DePauw 60, Wabash 59 · Feb 1, 2003" in text
    assert "Highest-scoring game: Wabash 80, DePauw 75 · Feb 1, 2001 · 155 points combined" in text
    assert "Overtime games: 2 · most recent: DePauw 60, Wabash 59 · Feb 1, 2003 (2 OT)" in text


def test_facts_that_make_no_sense_are_left_out(tmp_path):
    swept = [gm("2020-04-01", "W", 12, 3), gm("2021-04-01", "W", 9, 8), gm("2022-04-01", "W", 14, 5)]
    text = fun_text(tmp_path, "mens-lacrosse", sport_of("mens-lacrosse", "Men's Lacrosse", swept, "goals"))
    assert "Biggest DePauw win: DePauw 14, Wabash 5 · by 9 goals (twice, most recently Apr 1, 2022)" in text
    assert "Biggest Wabash win" not in text and "Longest Wabash win streak" not in text
    assert "Overtime" not in text and "Extra-inning" not in text
    assert "One-score games: 1 decided by 1 goal · most recent: DePauw 9, Wabash 8" in text
    tennis = [gm("2021-04-01", "W", 9, 0), gm("2022-04-01", "W", 5, 4)]
    text = fun_text(tmp_path, "mens-tennis", sport_of("mens-tennis", "Men's Tennis", tennis, "individual matches"))
    assert "Highest-scoring" not in text and "by 9 matches" in text and "1 decided by 1 match" in text


def test_season_list_sits_inside_the_games_details_only_when_it_applies(tmp_path):
    games = [gm("2024-01-06", "W", 70, 60, season="2023-24"), gm("2024-02-03", "W", 71, 60, season="2023-24"),
             gm("2025-01-04", "W", 70, 60, season="2024-25"), gm("2025-02-01", "L", 60, 70, season="2024-25")]
    html = render_page(tmp_path, {"mens-basketball": sport_of("mens-basketball", "Men's Basketball", games)})
    details = html[html.index('<details class="games">'):html.index("</details>")]
    assert "<strong>2024-25:</strong> Split 1-1" in details and "<strong>2023-24:</strong> DePauw 2-0" in details
    assert details.index("Season by season") < details.index("<table>")
    assert "Season by season" not in render_page(tmp_path, {"football": football()})
