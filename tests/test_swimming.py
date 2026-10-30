"""Men's swimming and diving: half-point scores, results with no score, scores printed in the wrong order,
the session_of ruling, the first import of a sport, and the schedule filter for multi-team events.

The history fixtures are the live pages captured 2026-10-29 (their complete game lists are in
mens-swimming-and-diving-expected.json and wabash-mens-swimming-and-diving-expected.json); the rulings are the
committed ones in data/overrides.json."""

import copy
import csv
import datetime as dt
import io
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
sys.path.insert(0, str(ROOT / "tests"))
import facts  # noqa: E402
import merge  # noqa: E402
import render  # noqa: E402
import scrape  # noqa: E402
import upcoming  # noqa: E402
from test_scrape import full_html, parse, parse_wabash  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
KEY = "mens-swimming-and-diving"
SPORT = next(s for s in json.loads((ROOT / "scraper" / "sources.json").read_text(encoding="utf-8"))["sports"]
             if s["key"] == KEY)
RULES = merge.load_overrides(ROOT / "data" / "overrides.json")[KEY]


@pytest.fixture(scope="module")
def pages():
    d, ds, _ = parse(full_html(KEY))
    w, ws, _ = parse_wabash(full_html(f"wabash-{KEY}"))
    return {"depauw": (d, ds), "wabash": (w, ws)}


def merged(pages, rules=RULES):
    raw = {school: scrape.raw_payload(SPORT["name"], "u", "points", scrape.game_dicts(g, school), [])
           for school, (g, _) in pages.items()}
    return scrape.merged_payload(SPORT, raw, rules)


# ---- reading each school's page -------------------------------------------------------------------- #

def test_depauws_page_has_30_meets_and_a_half_point_total(pages):
    games, summary = pages["depauw"]
    assert len(games) == 30 and scrape.validate(games, summary) == []
    assert (summary.wins, summary.losses, summary.ties) == (19, 11, 0)
    assert summary.total == 4459.5 and summary.total_label == "points"      # the page's own "Total Points 4459.5"
    assert summary.home == (11, 5, 0) and summary.away == (8, 6, 0)
    assert not any(g.score_order_reversed or not g.score_known for g in games)


def test_wabashs_page_has_41_rows_and_validates_against_its_own_summary(pages):
    games, summary = pages["wabash"]
    assert len(games) == 41 and scrape.validate(games, summary) == []
    assert (summary.wins, summary.losses, summary.ties) == (15, 26, 0)     # Wabash's own point of view
    assert summary.total == 4612


def test_half_point_scores_are_kept_whole_scores_stay_whole(pages):
    games = {g["date"]: g for g in scrape.game_dicts(pages["wabash"][0], "wabash")}     # in DePauw's point of view
    half = games["2006-11-03"]
    assert (half["depauw"], half["wabash"]) == (127.5, 172.5)               # Wabash's page: W 172.5 - 127.5
    both = games["2018-11-02"]
    assert (both["depauw"], both["wabash"]) == (147.5, 143.5)
    whole = games["2021-12-04"]
    assert (whole["depauw"], whole["wabash"]) == (107, 187) and isinstance(whole["wabash"], int)
    assert scrape.to_number("147.50") == 147.5 and scrape.to_number("153.0") == 153
    assert isinstance(scrape.to_number("153.0"), int)


def test_a_result_with_no_score_counts_in_the_record_but_has_no_score(pages):
    games, summary = pages["wabash"]
    none = [g for g in games if not g.score_known]
    assert sorted(g.date for g in none) == ["1934-01-05", "1934-02-12"]
    assert all(g.result == "L" and g.depauw is None and g.wabash is None and g.note is None for g in none)  # Wabash lost
    assert scrape.tally(games)["losses"] == summary.losses                   # they are in the 26 losses
    saved = scrape.game_dicts(games, "wabash")
    row = next(g for g in saved if g["date"] == "1934-01-05")
    assert row["score_known"] is False and row["result"] == "W" and row["depauw"] is None   # flipped to DePauw's view
    assert "score_known" not in next(g for g in saved if g["date"] == "2021-12-04")         # sparse: only the odd row


def test_a_row_whose_letter_and_scores_disagree_trusts_the_letter(pages):
    games, _ = pages["wabash"]
    flagged = [g for g in games if g.score_order_reversed]
    assert [g.date for g in flagged] == ["2008-11-12"]
    g = scrape.game_dicts(flagged, "wabash")[0]                            # the page printed "L 153 - 141"
    assert (g["result"], g["depauw"], g["wabash"]) == ("W", 153, 141)       # Wabash lost 141-153, so DePauw won 153-141
    assert g["score_order_reversed"] is True


def test_a_row_whose_letter_and_scores_agree_is_not_touched():
    html = full_html("football").replace("<span> W 41 - 20 </span>", "<span> W 41 - 20 </span>")
    games, _, _ = parse(html)
    assert not any(g.score_order_reversed for g in games)


# ---- the merge ---------------------------------------------------------------------------------------- #

def test_the_merged_series_is_39_meets_26_13(pages):
    payload, result = merged(pages)
    assert payload["games_played"] == 39
    assert payload["record"] == {"depauw_wins": 26, "wabash_wins": 13, "ties": 0}
    src = payload["sources"]
    assert (src["both"], src["depauw_only"], src["wabash_only"], src["disputed"], src["date_differs"]) == (30, 0, 9, 3, 1)
    assert src["session_rows"] == 2
    assert payload["points"] == {"depauw": 4957, "wabash": 4369}            # whole totals stay whole numbers
    assert (payload["first_game"], payload["last_game"], payload["current_streak"]) == ("1927-02-08", "2025-12-05", "W1")


def test_three_disputes_none_changes_a_winner(pages):
    payload, _ = merged(pages)
    disputed = {g["date"]: (g["depauw"], g["wabash"], g["wabash_version"]["depauw"], g["wabash_version"]["wabash"])
                for g in payload["games"] if g.get("disputed")}
    assert disputed == {"1999-01-27": (109, 135, 108, 135), "2008-01-25": (150, 141, 159, 141),
                        "2011-11-09": (205, 80, 205, 89)}
    assert all(g["wabash_version"]["result"] == g["result"] for g in payload["games"] if g.get("disputed"))


def test_the_reversed_row_matches_depauws_game_and_is_not_a_dispute(pages):
    payload, _ = merged(pages)
    g = next(g for g in payload["games"] if g["date"] == "2008-11-12")
    assert (g["source"], g["depauw"], g["wabash"], g["result"]) == ("both", 153, 141, "W")
    assert not g.get("disputed") and g["score_order_reversed"] == ["Wabash"]


def test_the_1934_meets_are_wins_without_scores_and_stay_out_of_the_point_totals(pages):
    payload, _ = merged(pages)
    old = [g for g in payload["games"] if g["date"].startswith("1934")]
    assert len(old) == 2 and all(g["score_known"] is False and g["result"] == "W" for g in old)
    scored = [g for g in payload["games"] if g.get("score_known", True)]
    assert sum(g["depauw"] for g in scored) == payload["points"]["depauw"]
    assert sum(g["wabash"] for g in scored) == payload["points"]["wabash"]


def test_the_1998_meet_is_pinned_by_a_ruling_not_a_guess(pages):
    payload, result = merged(pages)
    g = next(g for g in payload["games"] if g["date"] == "1998-02-04")
    assert g["source"] == "both" and g["date_differs"] and g["wabash_date"] == "1998-02-01"
    assert [(p["kind"], p["wabash"]["date"]) for p in merge.beyond_window(result)] == [("ruling", "1998-02-01")]


def test_the_three_day_meet_of_march_2021_counts_once(pages):
    payload, result = merged(pages)
    march = [g for g in payload["games"] if g["date"].startswith("2021-03")]
    assert len(march) == 1
    meet = march[0]
    assert (meet["date"], meet["result"], meet["depauw"], meet["wabash"], meet["source"]) == ("2021-03-21", "L", 120, 231, "both")
    assert [(x["date"], x["depauw"], x["wabash"]) for x in meet["sessions"]] == [
        ("2021-03-19", 32, 59), ("2021-03-20", 76, 163)]
    assert len(result.sessions) == 2


def test_without_the_ruling_the_three_days_count_as_three_meets(pages):
    payload, _ = merged(pages, {k: v for k, v in RULES.items() if k != "session_of"})
    assert payload["games_played"] == 41 and payload["record"]["wabash_wins"] == 15


def test_a_session_ruling_that_no_longer_finds_its_rows_fails_loudly(pages):
    rules = dict(RULES)
    rules["session_of"] = [{**RULES["session_of"][0], "sessions": [{"date": "2021-03-18", "score": (32, 59)}]}]
    with pytest.raises(merge.MergeError, match="session_of ruling no longer finds the session 2021-03-18"):
        merged(pages, rules)
    rules["session_of"] = [{**RULES["session_of"][0], "meet": {"date": "2021-03-22", "score": (120, 231)}}]
    with pytest.raises(merge.MergeError, match="the meet 2021-03-22"):
        merged(pages, rules)


def test_a_session_ruling_must_be_well_formed(tmp_path):
    def load(entry):
        p = tmp_path / "o.json"
        p.write_text(json.dumps({"schema_version": 1, "sports": {KEY: {"session_of": [entry]}}}), encoding="utf-8")
        return merge.load_overrides(p)
    good = {"school": "wabash", "meet": {"date": "2021-03-21", "score": "120-231"},
            "sessions": [{"date": "2021-03-19", "score": "32-59"}], "reason": "r"}
    assert load(good)[KEY]["session_of"][0]["sessions"][0]["score"] == (32, 59)
    for bad in ({**good, "school": "nobody"}, {**good, "sessions": []},
                {**good, "sessions": [{"date": "2021-03-22", "score": "32-59"}]},       # after the meet
                {**good, "meet": {"date": "2021-03-21", "score": "x"}}):
        with pytest.raises(merge.MergeError):
            load(bad)


def test_the_committed_ruling_cites_wabashs_recaps_and_gives_a_reason():
    ruling = RULES["session_of"][0]
    assert ruling["school"] == "wabash" and ruling["reason"]
    assert len(ruling["sources"]) == 3 and all(u.startswith("https://sports.wabash.edu/news/2021/3/") for u in ruling["sources"])


# ---- review hold ------------------------------------------------------------------------------------- #

def test_a_sport_with_no_saved_copy_is_taken_whole_and_holds_nothing(pages):
    cfg = json.loads((ROOT / "scraper" / "sources.json").read_text(encoding="utf-8"))
    cfg["sports"] = [SPORT]
    parsed = {(school, KEY): scrape.raw_payload(SPORT["name"], "u", "points", scrape.game_dicts(g, school), [])
              for school, (g, _) in pages.items()}
    new_raw = {"depauw": {}, "wabash": {}}
    pending = scrape.hold_changes(cfg, parsed, {"depauw": {}, "wabash": {}}, new_raw, [], {}, {}, False, "2026-10-29T00:00:00+00:00")
    assert pending == []                                                    # nothing held, so no data-review issue
    assert len(new_raw["depauw"][KEY]["games"]) == 30 and len(new_raw["wabash"][KEY]["games"]) == 41

    # From the next run on, the normal hold applies: an edited score in an old season is held, not applied.
    edited = copy.deepcopy(parsed)
    old = next(g for g in edited[("depauw", KEY)]["games"] if g["date"] == "2011-11-09")
    old["depauw"] = 206
    again = {"depauw": {}, "wabash": {}}
    held = scrape.hold_changes(cfg, edited, new_raw, again, [], {}, {}, False, "2026-10-30T00:00:00+00:00")
    assert [(e["school"], e["kind"]) for e in held] == [("depauw", "edited")]
    assert next(g for g in again["depauw"][KEY]["games"] if g["date"] == "2011-11-09")["depauw"] == 205


# ---- what the page shows ------------------------------------------------------------------------------ #

# A copy of data/series.json as it was on 2026-10-30. The bot rewrites the real file with every new result, so
# the exact swimming numbers below are checked against this copy, never the live file.
FROZEN_SERIES = ROOT / "tests" / "fixtures" / "series-frozen-2026-10-30.json"

@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("site")
    render.build(FROZEN_SERIES, out, upcoming_path=tmp_path_factory.mktemp("none") / "upcoming.json",
                 now=dt.datetime(2026, 11, 20, 15, 0, tzinfo=dt.timezone.utc))
    return out


def card_text(site):
    page = (site / "index.html").read_text(encoding="utf-8")
    start = page.index('id="mens-swimming-and-diving"')
    return page, page[start:page.index("</article>", start)]


def text(html):
    import html as h
    return h.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)))


def test_the_card_shows_half_points_as_halves_and_whole_scores_as_whole(site):
    _, card = card_text(site)
    t = text(card)
    assert "Wabash 172.5, DePauw 127.5" in t and "DePauw 147.5, Wabash 143.5" in t
    assert "172.50" not in t and "147.50" not in t and ".0," not in t
    assert "DePauw 4,957, Wabash 4,369" in t and "Meets played 39" in t and "All 39 meets" in t


def test_a_meet_with_no_score_says_so_and_names_the_winner(site):
    _, card = card_text(site)
    t = text(card)
    assert t.count("DePauw won · score not recorded") == 2
    assert "Score not recorded; the result counts in the record but not in the point totals." in t


def test_the_reversed_row_and_the_three_day_meet_are_explained_on_the_card(site):
    _, card = card_text(site)
    t = text(card)
    assert "The two scores were listed in the opposite order on Wabash’s page" in t
    assert ("One meet over several days. Wabash’s record also lists Mar 19, 2021 (DePauw 32, Wabash 59) and "
            "Mar 20, 2021 (DePauw 76, Wabash 163) as separate games; they are running scores of this meet") in t
    assert "2 rows listed as separate meets are the running score of a multi-day meet" in t


def test_fun_facts_say_meets_and_leave_out_the_unscored_meets(site):
    _, card = card_text(site)
    t = text(card)
    assert "Close meets: 8 decided by 10 points or fewer" in t
    assert "Highest-scoring meet: Wabash 231, DePauw 120" in t and "Biggest DePauw win: DePauw 205, Wabash 80" in t
    games = json.loads(FROZEN_SERIES.read_text(encoding="utf-8"))["sports"][KEY]["games"]
    assert all(g.get("score_known", True) for g in facts.one_score_games(KEY, games) + facts.biggest_wins(games, "W")
               + facts.highest_scoring_games(games))


def test_a_straight_win_streak_uses_the_sports_word():
    assert render.streak_text("W3", "meets") == "DePauw has won 3 straight meets"
    assert render.streak_text("L2") == "Wabash has won 2 straight"


def test_the_page_notes_say_dual_meets_only(site):
    page, _ = card_text(site)
    t = text(page)
    assert "Not included: cross country, track and field, and golf" in t
    assert "Swimming and diving counts dual meets only; invitationals and championships are left out." in t
    assert "swimming invitationals and championships) aren’t head-to-head" in t
    assert "DePauw leads basketball, soccer, lacrosse, tennis, and swimming and diving." in t


def test_the_csv_leaves_scores_blank_for_unscored_meets_and_keeps_halves(site):
    rows = [r for r in csv.reader(io.StringIO((site / "games.csv").read_text(encoding="utf-8")))
            if r[0] == "Men's Swimming and Diving"]
    assert len(rows) == 39
    by_date = {r[1]: r for r in rows}
    assert by_date["1934-01-05"][5:8] == ["", "", "DePauw win"] and by_date["1934-02-12"][5:7] == ["", ""]
    assert by_date["2006-11-03"][5:8] == ["127.5", "172.5", "Wabash win"]
    assert by_date["2011-11-09"][-2:] == ["205-80", "205-89"]
    assert "127.50" not in (site / "games.csv").read_text(encoding="utf-8")


def test_the_game_day_banner_uses_the_sports_name():
    sports = {KEY: {"name": SPORT["name"]}}
    e = {"key": KEY, "name": SPORT["name"], "phase": "pregame",
         "games": [{"time": "7:00 PM", "location": "away", "site": "Crawfordsville, Ind."}]}
    assert render.game_day_text(e, sports, False) == \
        "Game day: Men's Swimming and Diving vs. Wabash today, 7:00 PM · Crawfordsville, Ind."


# ---- the schedule filter ------------------------------------------------------------------------------ #

def schedule(name, opponent_id, *, rename=None, new_id=None):
    html = (FIX / name).read_text(encoding="utf-8")
    if rename:
        html = html.replace(*rename)
    return upcoming.parse_schedule(html, opponent_id=opponent_id)


def test_a_dual_meet_at_wabash_counts():
    games = schedule("schedule-mens-swimming-and-diving.html", 215)
    assert [(g["date"], g["time"], g["location"]) for g in games] == [("2026-12-04", "7:00 PM", "away")]
    mine = schedule("wabash-schedule-mens-swimming-and-diving.html", 4)
    assert [(g["date"], g["location"]) for g in mine] == [("2026-12-04", "home")]


@pytest.mark.parametrize("name", ["Wabash Invitational", "Wabash/Franklin", "Wabash, Franklin and Rose-Hulman",
                                  "Tri-meet with Wabash", "NCAC Swimming and Diving Championships",
                                  "Wabash Relays", "Monon Classic", "Wabash Open"])
def test_a_multi_team_listing_never_counts_even_with_the_other_schools_id(name):
    games = schedule("schedule-mens-swimming-and-diving.html", 215,
                     rename=('target="_blank">Wabash</a>', f'target="_blank">{name}</a>'))
    assert games == []


def test_the_rename_used_above_really_changes_the_dual_meet_row():
    page = (FIX / "schedule-mens-swimming-and-diving.html").read_text(encoding="utf-8")
    assert page.count('target="_blank">Wabash</a>') == 1                  # so the parametrized cases test what they say


def test_a_conference_championship_row_that_carries_wabashs_id_is_skipped():
    page = (FIX / "schedule-mens-swimming-and-diving.html").read_text(encoding="utf-8")
    page = page.replace('data-game-opponent-id="148"', 'data-game-opponent-id="215"')
    games = upcoming.parse_schedule(page, opponent_id=215)
    assert [g["date"] for g in games] == ["2026-12-04"]                    # the four championship days are not meetings


def test_the_filter_matches_whole_words_and_keeps_every_real_school_name():
    for name in ("Wabash", "Wabash (DH)", "DePauw University", "DePauw University (DH)", "Opener at Wabash",
                 "Wabash College", "Franklin"):
        assert not upcoming.is_multi_team(name), name
    for name in ("Wabash Invitational", "DePauw Invitational", "Indiana DIII Invitational", "Tri-meet with Wabash",
                 "Wabash and Franklin", "Wabash & Franklin", "Wabash/Franklin", "NCAC Championships", "Mid-Season Classic",
                 "Wabash Relays", "Indiana Open", "Quad meet"):
        assert upcoming.is_multi_team(name), name


def test_every_real_schedule_fixture_keeps_its_games():
    """The filter must not drop a real head-to-head game from any sport's schedule, football's Bell game included."""
    expected = {"schedule-football.html": 1, "schedule-mens-basketball.html": 2, "schedule-baseball.html": 0,
                "schedule-mens-tennis.html": 0}       # tennis and baseball: DePauw's pages hold no Wabash game this season
    for name, count in expected.items():
        games = upcoming.parse_schedule((FIX / name).read_text(encoding="utf-8"), opponent_id=215)
        assert len(games) == count, name
    wabash = {"wabash-schedule-football.html": 1, "wabash-schedule-mens-basketball.html": 2,
              "wabash-schedule-baseball.html": 2, "wabash-schedule-mens-lacrosse.html": 1}
    for name, count in wabash.items():
        games = upcoming.parse_schedule((FIX / name).read_text(encoding="utf-8"), opponent_id=4)
        assert len(games) == count, name
