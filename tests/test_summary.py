"""The one-line series summary under the all-sports totals: who leads which sports, from the data."""
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import render  # noqa: E402
from test_render import build, game, sport, text_of  # noqa: E402


def games(depauw, wabash, ties=0, first_year=2000):
    """That many wins, losses and ties, two days apart, in a stable order."""
    results = ["W"] * depauw + ["L"] * wabash + ["T"] * ties
    return [game((dt.date(first_year, 1, 1) + dt.timedelta(days=2 * i)).isoformat(), r, 7 if r == "W" else 3 if r == "L" else 7, 3 if r == "W" else 7, "home")
            for i, r in enumerate(results)]


def sports(spec):
    """spec: (key, name, depauw wins, wabash wins, ties) in page order -> sports dict as render reads it."""
    return {key: sport(key, name, games(d, w, t)) for key, name, d, w, t in spec}


def football_with_bell(pre_1932_depauw, pre_1932_wabash, bell_depauw, bell_wabash, bell_ties=0):
    """A football record: some games before the Bell era (1932 on) and the Bell-era record asked for."""
    early = games(pre_1932_depauw, pre_1932_wabash, first_year=1890)
    bell = games(bell_depauw, bell_wabash, bell_ties, first_year=1932)
    return sport("football", "Football", early + bell)


CURRENT = [("football", "Football", 59, 63, 9), ("mens-basketball", "Men's Basketball", 133, 100, 1),
           ("baseball", "Baseball", 155, 156, 3), ("mens-soccer", "Men's Soccer", 41, 18, 6),
           ("mens-lacrosse", "Men's Lacrosse", 12, 0, 0), ("mens-tennis", "Men's Tennis", 115, 7, 1)]


def current_sports():
    s = sports(CURRENT)
    # the Bell era (1932 on) through the 2025 season: Wabash 44, DePauw 43, 6 ties; the earlier games make the all-time record
    s["football"] = sport("football", "Football", games(16, 19, 3, first_year=1890) + games(43, 44, 6, first_year=1932))
    assert (s["football"]["record"]["depauw_wins"], s["football"]["record"]["wabash_wins"]) == (59, 63)
    return s


def test_the_current_series_reads_like_the_example():
    assert render.series_summary(current_sports()) == (
        "Wabash leads football and baseball, including the Monon Bell era; "
        "DePauw leads basketball, soccer, lacrosse, and tennis.")


def test_the_committed_data_gives_a_sentence_that_matches_its_own_records():
    data = json.loads((ROOT / "data" / "series.json").read_text(encoding="utf-8"))
    text = render.series_summary(data["sports"])
    for key, s in data["sports"].items():
        r = s["record"]
        word = render.sport_word(s["name"])
        side = "DePauw" if r["depauw_wins"] > r["wabash_wins"] else "Wabash" if r["wabash_wins"] > r["depauw_wins"] else None
        clause = next((c for c in text.rstrip(".").split("; ") if c.startswith(f"{side} leads")), "") if side else ""
        assert (word in clause) if side else (word.capitalize() in text and "even" in text)


def test_a_tied_sport_is_listed_separately_not_given_to_either_school():
    s = sports([("mens-basketball", "Men's Basketball", 10, 10, 0), ("baseball", "Baseball", 5, 3, 0),
                ("mens-soccer", "Men's Soccer", 2, 2, 1)])
    assert render.series_summary(s) == "DePauw leads baseball. Basketball and soccer are even."
    one = sports([("baseball", "Baseball", 2, 2, 0), ("mens-lacrosse", "Men's Lacrosse", 3, 0, 0)])
    assert render.series_summary(one) == "DePauw leads lacrosse. Baseball is even."


def test_everything_even_has_no_leader_clause():
    assert render.series_summary(sports([("baseball", "Baseball", 2, 2, 0)])) == "Baseball is even."


def test_a_sport_with_no_games_is_not_called_even():
    s = sports([("baseball", "Baseball", 2, 1, 0)])
    s["mens-golf"] = {**sport("mens-golf", "Men's Golf", games(1, 0)), "games": [], "record": {"depauw_wins": 0, "wabash_wins": 0, "ties": 0}}
    assert render.series_summary(s) == "DePauw leads baseball."


def test_a_bell_era_leader_who_differs_from_the_all_time_football_leader_is_named():
    # Wabash leads football all-time (60-55) but DePauw leads the Bell era (30-20 over 50 games since 1932).
    s = {"football": football_with_bell(25, 40, 30, 20)}
    assert (s["football"]["record"]["depauw_wins"], s["football"]["record"]["wabash_wins"]) == (55, 60)
    assert render.series_summary(s) == "Wabash leads football, though DePauw leads the Monon Bell era."
    # and the other way round
    s = {"football": football_with_bell(40, 20, 10, 25)}
    assert render.series_summary(s) == "DePauw leads football, though Wabash leads the Monon Bell era."


def test_an_even_bell_era_is_said_to_be_even():
    s = {"football": football_with_bell(30, 5, 10, 10, 2)}
    assert render.series_summary(s) == "DePauw leads football, though the Monon Bell era is even."


def test_when_football_is_even_the_bell_era_gets_its_own_sentence():
    s = {"football": football_with_bell(20, 10, 5, 15), "baseball": sport("baseball", "Baseball", games(3, 1))}
    assert (s["football"]["record"]["depauw_wins"], s["football"]["record"]["wabash_wins"]) == (25, 25)
    assert render.series_summary(s) == "DePauw leads baseball. Football is even. Wabash leads the Monon Bell era."
    s = {"football": football_with_bell(5, 5, 10, 10)}
    assert render.series_summary(s) == "Football is even. The Monon Bell era is even."


def test_without_football_there_is_no_bell_clause():
    assert render.series_summary(sports([("baseball", "Baseball", 3, 1, 0)])) == "DePauw leads baseball."


def test_clauses_follow_the_page_order_of_each_schools_first_sport():
    s = sports([("football", "Football", 1, 2, 0), ("mens-basketball", "Men's Basketball", 5, 1, 0),
                ("baseball", "Baseball", 1, 2, 0)])
    text = render.series_summary(s)
    assert text.startswith("Wabash leads") and text.index("Wabash") < text.index("DePauw")
    s = sports([("mens-basketball", "Men's Basketball", 5, 1, 0), ("football", "Football", 1, 2, 0)])
    assert render.series_summary(s).startswith("DePauw leads basketball; Wabash leads football")


def test_it_updates_by_itself_when_results_change():
    s = sports([("baseball", "Baseball", 2, 2, 0)])
    assert render.series_summary(s) == "Baseball is even."
    s = sports([("baseball", "Baseball", 3, 2, 0)])
    assert render.series_summary(s) == "DePauw leads baseball."


def test_the_sentence_sits_under_the_all_sports_totals_and_before_the_cards(tmp_path):
    _, page = build(tmp_path, {"football": football_with_bell(40, 20, 10, 25), "mens-basketball": sport(
        "mens-basketball", "Men's Basketball", games(1, 3))})
    meta = page.index('class="overall-meta"')
    summary = page.index('class="series-summary"')
    assert meta < summary < page.index('class="not-included"') < page.index('class="sport"')
    assert "DePauw leads football, though Wabash leads the Monon Bell era; Wabash leads basketball." in text_of(page)


def test_lists_of_three_or_more_use_a_serial_comma():
    assert render.join_and(["a"]) == "a"
    assert render.join_and(["a", "b"]) == "a and b"
    assert render.join_and(["a", "b", "c"]) == "a, b, and c"
    assert render.join_and(["a", "b", "c", "d"]) == "a, b, c, and d"
