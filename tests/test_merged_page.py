"""How the merged records show on the page, in games.csv and in the issue form, and the cross-check of
the two schools' schedules. Made-up series data, except where a test says it reads the real files."""
import csv
import io
import json
import re
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import render  # noqa: E402
import scrape  # noqa: E402
import upcoming  # noqa: E402
from test_meetings import entry, page as meeting_page, sched  # noqa: E402
from test_render import NOW, build, football, game, sport, text_of  # noqa: E402
from test_scrape import both_dir, fixture_dir  # noqa: E402,F401  (pytest fixtures)

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures"
WABASH_SRC = "https://sports.wabash.edu/sports/{}/opponent-history/depauw-university/4"


def merged_sport(key, name, games, sources=None, unit=None, total_label=None):
    """A sport as the merged series.json carries it: games with a source, plus Wabash's page link."""
    s = sport(key, name, games, total_label)
    for g in games:
        g.setdefault("source", "both")
    s["wabash_source_url"] = WABASH_SRC.format(key)
    s["sources"] = sources or {"both": len(games), "depauw_only": 0, "wabash_only": 0, "disputed": 0,
                               "date_differs": 0, "dates_not_recorded": 0}
    if unit:
        s["unit"] = unit
    return s


def card(html, key):
    start = html.index(f'id="{key}" aria-labelledby')
    return html[start:html.index("</article>", start)]


# ---- where the record comes from -------------------------------------------------------------- #

def counts(**kw):
    return {"both": 140, "depauw_only": 1, "wabash_only": 174, "disputed": 0, "date_differs": 0,
            "dates_not_recorded": 0, **kw}


def test_a_card_says_how_its_record_was_combined(tmp_path):
    s = merged_sport("football", "Football", football()["games"], counts())
    _, html = build(tmp_path, {"football": s})
    assert "Combined from DePauw and Wabash records: 140 games listed by both, 174 by Wabash only, " \
           "1 by DePauw only, 0 disputed." in text_of(card(html, "football"))


def test_a_card_where_the_schools_agree_completely_says_so(tmp_path):
    s = merged_sport("football", "Football", football()["games"], counts(both=5, depauw_only=0, wabash_only=0))
    _, html = build(tmp_path, {"football": s})
    t = text_of(card(html, "football"))
    assert "DePauw and Wabash records agree." in t and "Combined from" not in t


def test_dates_that_differ_are_counted_in_the_sentence_and_disputes_are_not_agreement(tmp_path):
    s = merged_sport("football", "Football", football()["games"], counts(both=5, depauw_only=0, wabash_only=0,
                                                                       disputed=2, date_differs=3))
    _, html = build(tmp_path, {"football": s})
    t = text_of(card(html, "football"))
    assert "2 disputed." in t and "3 of the shared games have different dates in the two records." in t
    assert "records agree" not in t


def test_a_sport_with_only_one_schools_data_makes_no_claim_about_the_other(tmp_path):
    _, html = build(tmp_path, {"football": football()})
    assert "Combined from" not in html and "records agree" not in html and "record only" not in html


# ---- games listed by one school, disputes, dates ---------------------------------------------- #

def test_games_only_one_school_lists_are_marked_in_the_game_list(tmp_path):
    games = [game("2001-11-10", "L", 17, 24), game("2002-11-09", "W", 30, 27), game("2003-11-08", "W", 41, 20)]
    games[0]["source"], games[1]["source"] = "wabash", "depauw"
    _, html = build(tmp_path, {"football": merged_sport("football", "Football", games)})
    rows = {re.search(r'datetime="([^"]+)"', r).group(1): text_of(r)
            for r in re.findall(r"<tr>.*?</tr>", card(html, "football"), re.S) if "datetime=" in r}
    assert "Wabash record only" in rows["2001-11-10"] and "DePauw record only" in rows["2002-11-09"]
    assert "record only" not in rows["2003-11-08"]


def test_a_disputed_game_shows_both_versions(tmp_path):
    g = game("1941-11-15", "L", 19, 27, "away", "Crawfordsville, Ind.")
    g.update({"source": "both", "disputed": True, "wabash_version": {"result": "L", "depauw": 9, "wabash": 27}})
    ok = game("2003-11-08", "W", 41, 20)
    _, html = build(tmp_path, {"football": merged_sport("football", "Football", [g, ok])})
    t = text_of(card(html, "football"))
    assert "Wabash 27, DePauw 19" in t                       # the counted version stays the result
    assert "DePauw’s record (DePauw-Wabash): 19-27 · Wabash’s record (DePauw-Wabash): 9-27" in t
    flipped = game("1901-11-11", "W", 5, 3)
    flipped.update({"source": "both", "disputed": True, "wabash_version": {"result": "L", "depauw": 3, "wabash": 5}})
    _, html = build(tmp_path, {"football": merged_sport("football", "Football", [flipped, ok])})
    assert "DePauw’s record (DePauw-Wabash): 5-3 · Wabash’s record (DePauw-Wabash): 3-5" in text_of(card(html, "football"))


def test_a_game_the_schools_date_differently_shows_wabashs_date(tmp_path):
    g = game("1891-11-21", "W", 1, 0)
    g.update({"source": "both", "date_differs": True, "wabash_date": "1891-11-02"})
    _, html = build(tmp_path, {"football": merged_sport("football", "Football", [g, game("2003-11-08", "W", 41, 20)])})
    assert "Wabash’s record lists this game on Nov 2, 1891" in text_of(card(html, "football"))


def test_a_game_with_no_recorded_date_shows_its_year_never_a_made_up_january_day(tmp_path):
    old = game("1895-01-01", "L", 2, 39, "unknown", None)
    old.update({"source": "wabash", "date_known": False})
    games = [old, game("2003-11-08", "W", 41, 20)]
    out, html = build(tmp_path, {"football": merged_sport("football", "Football", games)})
    row = re.findall(r"<tr>.*?</tr>", card(html, "football"), re.S)[-1]       # oldest game is the last row
    assert '<time datetime="1895">1895</time>' in row and "(date not recorded)" in text_of(row)
    assert "Jan 1, 1895" not in html and "1895-01-01" not in html
    assert "Jan 1" not in text_of(card(html, "football"))
    csv_rows = list(csv.DictReader(io.StringIO((out / "games.csv").read_text(encoding="utf-8"))))
    first = csv_rows[0]
    assert (first["date"], first["season"], first["date_recorded"], first["source"]) == ("", "1895", "no", "wabash")


def test_fun_facts_never_print_a_placeholder_date(tmp_path):
    old = game("1895-01-01", "L", 2, 39, "unknown", None)
    old.update({"source": "wabash", "date_known": False})
    games = [old, game("2003-11-08", "W", 41, 20), game("2004-11-13", "W", 28, 3)]
    _, html = build(tmp_path, {"football": merged_sport("football", "Football", games)})
    facts_text = text_of(html[html.index('class="fun-facts"'):html.index("</div>", html.index('class="fun-facts"'))])
    assert "Biggest Wabash win: Wabash 39, DePauw 2 · 1895 (date not recorded)" in facts_text
    assert "Jan 1" not in facts_text


def test_the_1954_basketball_tie_has_its_footnote_only_while_both_schools_list_it(tmp_path):
    tie = game("1954-12-04", "T", 67, 67)
    tie["source"] = "both"
    other = game("1955-01-10", "W", 80, 70)
    _, html = build(tmp_path, {"mens-basketball": merged_sport("mens-basketball", "Men's Basketball", [tie, other])})
    assert "Listed as a tie by both schools." in text_of(card(html, "mens-basketball"))
    tie["source"] = "depauw"
    _, html = build(tmp_path, {"mens-basketball": merged_sport("mens-basketball", "Men's Basketball", [tie, other])})
    assert "Listed as a tie" not in html
    other_tie = game("1960-12-04", "T", 67, 67)
    other_tie["source"] = "both"
    _, html = build(tmp_path, {"mens-basketball": merged_sport("mens-basketball", "Men's Basketball",
                                                              [other_tie, other])})
    assert "Listed as a tie" not in html


# ---- tennis plays matches --------------------------------------------------------------------- #

def test_the_tennis_card_says_matches_where_other_cards_say_games(tmp_path):
    games = [game("2024-04-06", "W", 9, 0), game("2025-04-05", "W", 7, 2), game("2026-04-04", "W", 5, 4)]
    tennis = merged_sport("mens-tennis", "Men's Tennis", games, unit=["match", "matches"],
                          total_label="individual matches")
    _, html = build(tmp_path, {"mens-tennis": tennis, "football": merged_sport("football", "Football",
                                                                               football()["games"])})
    t = text_of(card(html, "mens-tennis"))
    assert "Matches played 3" in t and "All 3 matches" in t
    assert re.search(r"\bgames?\b", t, re.I) is None
    assert "Every mens tennis match, newest first" in card(html, "mens-tennis")
    f = text_of(card(html, "football"))
    assert "Games played 5" in f and "All 5 games" in f and "match" not in f.lower()


def test_one_tennis_match_is_not_pluralised(tmp_path):
    tennis = merged_sport("mens-tennis", "Men's Tennis", [game("2024-04-06", "W", 9, 0)], unit=["match", "matches"])
    _, html = build(tmp_path, {"mens-tennis": tennis})
    assert "All 1 match" in text_of(card(html, "mens-tennis")) and "All 1 matches" not in html


def test_the_real_tennis_data_and_sources_json_say_matches():
    cfg = json.loads((ROOT / "scraper" / "sources.json").read_text(encoding="utf-8"))
    units = {s["key"]: s.get("unit") for s in cfg["sports"]}
    assert units["mens-tennis"] == ["match", "matches"] and units["mens-swimming-and-diving"] == ["meet", "meets"]
    assert all(v is None for k, v in units.items() if k not in ("mens-tennis", "mens-swimming-and-diving"))


# ---- links to both schools -------------------------------------------------------------------- #

def test_every_card_and_the_footer_link_both_schools_history_and_schedule_pages(tmp_path):
    s = merged_sport("football", "Football", football()["games"])
    up = tmp_path / "upcoming.json"
    up.write_text(json.dumps({"schema_version": 1, "sports": {"football": {
        "schedule_url": "https://depauwtigers.com/sports/football/schedule",
        "wabash_schedule_url": "https://sports.wabash.edu/sports/football/schedule", "games": []}}}))
    _, html = build(tmp_path, {"football": s}, upcoming_path=up)
    for url in ("https://depauwtigers.com/sports/football/opponent-history/wabash/215",
                WABASH_SRC.format("football"),
                "https://depauwtigers.com/sports/football/schedule",
                "https://sports.wabash.edu/sports/football/schedule"):
        assert f'<a href="{url}">' in card(html, "football"), url
        assert f'<a href="{url}">' in html[html.index("<footer"):], url
    c = text_of(card(html, "football"))
    assert "Sources: DePauw history · Wabash history · DePauw schedule · Wabash schedule · Report a data error" in c


def test_the_footer_names_both_sites_as_the_data_source(tmp_path):
    _, html = build(tmp_path, {"football": merged_sport("football", "Football", football()["games"])})
    foot = html[html.index("<footer"):]
    assert 'Data from <a href="https://depauwtigers.com/">depauwtigers.com</a> and ' \
           '<a href="https://sports.wabash.edu/">sports.wabash.edu</a>.' in foot


def test_only_the_two_source_sites_are_linkable():
    assert render.safe_url("https://sports.wabash.edu/boxscore.aspx?id=1") is not None
    assert render.safe_url("https://depauwtigers.com/x") is not None
    for bad in ("http://sports.wabash.edu/x", "https://sports.wabash.edu.evil.example/x",
                "https://evil.example/sports.wabash.edu", "https://wabash.edu/x", "javascript:alert(1)"):
        assert render.safe_url(bad) is None, bad


# ---- games.csv -------------------------------------------------------------------------------- #

def test_games_csv_carries_source_dispute_and_date_columns(tmp_path):
    a = game("1941-11-15", "L", 19, 27, "away")
    a.update({"source": "both", "disputed": True, "wabash_version": {"result": "L", "depauw": 9, "wabash": 27}})
    b = game("1891-11-21", "W", 1, 0)
    b.update({"source": "both", "date_differs": True, "wabash_date": "1891-11-02"})
    c = game("1895-01-01", "L", 2, 39, "unknown", None)
    c.update({"source": "wabash", "date_known": False})
    d = game("2003-11-08", "W", 41, 20)
    d["source"] = "depauw"
    out, _ = build(tmp_path, {"football": merged_sport("football", "Football", [c, b, a, d])})
    rows = list(csv.DictReader(io.StringIO((out / "games.csv").read_text(encoding="utf-8"))))
    by = {(r["season"], r["depauw_score"]): r for r in rows}
    disputed = by[("1941", "19")]
    assert (disputed["source"], disputed["disputed"], disputed["depauw_record_score"],
            disputed["wabash_record_score"]) == ("both", "yes", "19-27", "9-27")
    differs = by[("1891", "1")]
    assert (differs["date_differs"], differs["wabash_record_date"], differs["date"]) == ("yes", "1891-11-02", "1891-11-21")
    unknown = by[("1895", "2")]
    assert (unknown["date"], unknown["date_recorded"], unknown["source"]) == ("", "no", "wabash")
    plain = by[("2003", "41")]
    assert (plain["source"], plain["disputed"], plain["date_differs"], plain["wabash_record_score"]) == \
        ("depauw", "no", "no", "")


# ---- the error form --------------------------------------------------------------------------- #

def test_the_error_form_asks_which_schools_record_looks_wrong():
    form = yaml.safe_load((ROOT / ".github" / "ISSUE_TEMPLATE" / "data-correction.yml").read_text(encoding="utf-8"))
    which = next(b for b in form["body"] if b.get("id") == "which_record")
    assert which["attributes"]["label"] == "Which school’s record looks wrong?"
    assert which["attributes"]["options"] == ["DePauw’s record", "Wabash’s record", "Both", "Not sure"]


# ---- the real data files ---------------------------------------------------------------------- #

def test_the_real_series_file_is_the_merge_of_the_two_raw_files():
    series = json.loads((ROOT / "data" / "series.json").read_text(encoding="utf-8"))
    depauw = json.loads((ROOT / "data" / "depauw.json").read_text(encoding="utf-8"))
    wabash = json.loads((ROOT / "data" / "wabash.json").read_text(encoding="utf-8"))
    import merge
    cfg = {x["key"]: x for x in json.loads((ROOT / "scraper" / "sources.json").read_text(encoding="utf-8"))["sports"]}
    rules = merge.load_overrides(ROOT / "data" / "overrides.json")
    for key, s in series["sports"].items():
        r = merge.merge_sport(depauw["sports"][key]["games"], wabash["sports"][key]["games"],
                              season_months=cfg[key]["season_months"], rules=rules.get(key))
        assert r.games == s["games"] and r.stats == s["sources"], key
        assert s["games_played"] == len(r.games)
    totals = series["totals"]
    assert totals["games_played"] == sum(s["games_played"] for s in series["sports"].values())


def test_the_real_series_never_shows_a_placeholder_january_date(tmp_path):
    out = tmp_path / "site"
    render.build(ROOT / "data" / "series.json", out, now=NOW, upcoming_path=tmp_path / "no-upcoming.json")
    page = (out / "index.html").read_text(encoding="utf-8")
    series = json.loads((ROOT / "data" / "series.json").read_text(encoding="utf-8"))
    unknown = [g for s in series["sports"].values() for g in s["games"] if g.get("date_known") is False]
    assert unknown and all(g["source"] == "wabash" for g in unknown)
    assert page.count("(date not recorded)") >= len(unknown)
    for g in unknown:
        assert render.undated_label(g) in page
    assert "Jan 1, 18" not in page and "Jan 3, 18" not in page
    csv_rows = list(csv.DictReader(io.StringIO((out / "games.csv").read_text(encoding="utf-8"))))
    assert sum(1 for r in csv_rows if r["date_recorded"] == "no") == len(unknown)
    assert all(r["date"] == "" for r in csv_rows if r["date_recorded"] == "no")


# ---- the other school's schedule -------------------------------------------------------------- #

def s(date, time="1:07 PM", location="neutral", site="Scheumann Stadium, Muncie, Ind.", status="scheduled"):
    return sched(date, time, location, site, status)


def test_a_game_both_schedules_list_is_shown_once_as_depauw_lists_it():
    games, warnings = upcoming.merge_schedules("Football", [s("2026-11-14")], [s("2026-11-14")])
    assert len(games) == 1 and games[0]["source"] == "both" and warnings == []


def test_a_game_only_wabashs_schedule_lists_is_shown_and_says_so():
    games, warnings = upcoming.merge_schedules("Baseball", [], [s("2027-04-10", None, "away", "Greencastle, IN")])
    assert [(g["source"], g["date"]) for g in games] == [("wabash", "2027-04-10")]
    assert warnings == ["Baseball 2027-04-10: only on Wabash's schedule; showing it as Wabash's"]


def test_when_the_schedules_differ_depauws_version_shows_and_the_difference_is_a_warning():
    d = s("2026-11-14", "1:07 PM", "neutral", "Scheumann Stadium, Muncie, Ind.")
    w = s("2026-11-14", "1:00 PM", "home", "Ball State, Muncie, IN")
    games, warnings = upcoming.merge_schedules("Football", [d], [w])
    assert games[0]["time"] == "1:07 PM" and games[0]["source"] == "both"
    assert len(warnings) == 1 and "time (Wabash 1:00 PM)" in warnings[0] and "home/away (Wabash home)" in warnings[0]
    assert "site (Wabash Ball State, Muncie, IN)" in warnings[0] and warnings[0].endswith("showing DePauw's")


def test_the_same_place_written_two_ways_is_not_a_difference():
    assert upcoming.same_site("Scheumann Stadium, Muncie, Ind.", "Scheumann Stadium, Muncie, IN")
    assert upcoming.same_site("Neal Fieldhouse, Greencastle, Ind.", "Greencastle, IN")
    assert not upcoming.same_site("Greencastle, Ind.", "Crawfordsville, IN")
    assert upcoming.same_site(None, "Greencastle, IN")


def test_dates_a_day_or_two_apart_are_one_game_with_a_date_warning():
    games, warnings = upcoming.merge_schedules("Football", [s("2026-11-14")], [s("2026-11-15")])
    assert len(games) == 1 and games[0]["date"] == "2026-11-14" and "date (Wabash 2026-11-15)" in warnings[0]


def test_a_doubleheader_pairs_game_for_game():
    d = [s("2027-04-28", "12:00 PM", "home"), s("2027-04-28", "3:30 PM", "home")]
    w = [s("2027-04-28", "12:00 PM", "home"), s("2027-04-28", "3:30 PM", "home")]
    games, warnings = upcoming.merge_schedules("Baseball", d, w)
    assert [g["source"] for g in games] == ["both", "both"] and warnings == []


def test_wabashs_home_and_away_flip_to_depauws_view():
    assert upcoming.flip_location(s("2027-01-06", location="away"))["location"] == "home"
    assert upcoming.flip_location(s("2027-01-06", location="neutral"))["location"] == "neutral"


def schedules_dir(tmp_path, wabash=True, depauw=True):
    d = tmp_path / "pages"
    d.mkdir(exist_ok=True)
    for key in ("football", "mens-basketball", "baseball", "mens-lacrosse"):
        if wabash:
            (d / f"wabash-{key}-schedule.html").write_text(
                (FIX / f"wabash-schedule-{key}.html").read_text(encoding="utf-8"), encoding="utf-8")
        src = FIX / f"schedule-{key}.html"
        if depauw and src.exists():
            (d / f"{key}-schedule.html").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return d


def run_schedules(tmp_path, d):
    out = tmp_path / "upcoming.json"
    code = scrape.main(["--fixtures", str(d), "--output", str(tmp_path / "series.json"), "--upcoming-output", str(out)])
    return code, json.loads(out.read_text(encoding="utf-8"))["sports"] if out.exists() else {}


def test_wabashs_real_schedule_pages_are_read_and_merged_into_upcoming_json(tmp_path, capsys):
    code, sports = run_schedules(tmp_path, schedules_dir(tmp_path))
    assert code == 0
    football = sports["football"]
    assert football["wabash_schedule_url"] == "https://sports.wabash.edu/sports/football/schedule"
    assert [(g["date"], g["source"]) for g in football["games"]] == [("2026-11-14", "both")]
    assert football["games"][0]["time"] == "1:07 PM" and football["games"][0]["site"] == "Scheumann Stadium, Muncie, Ind."
    basketball = sports["mens-basketball"]["games"]
    assert [g["source"] for g in basketball] == ["both", "both"]
    assert [g["location"] for g in basketball] == ["home", "away"]          # Wabash's "away" flipped to DePauw's "home"
    # Wabash lists a baseball doubleheader and a lacrosse game that DePauw's schedule does not have yet.
    assert [g["source"] for g in sports["baseball"]["games"]] == ["wabash", "wabash"]
    assert [(g["date"], g["source"]) for g in sports["mens-lacrosse"]["games"]] == [("2027-04-10", "wabash")]
    assert sports["mens-lacrosse"]["games"][0]["location"] == "home"
    err = capsys.readouterr().err
    assert "Men's Lacrosse 2027-04-10: only on Wabash's schedule" in err


def test_schedule_differences_never_turn_the_run_red(tmp_path):
    d = schedules_dir(tmp_path)
    page = (d / "wabash-football-schedule.html").read_text(encoding="utf-8").replace("1:07 p.m.", "2:00 p.m.")
    (d / "wabash-football-schedule.html").write_text(page, encoding="utf-8")
    code, _ = run_schedules(tmp_path, d)
    assert code == 0


def test_a_broken_wabash_schedule_keeps_that_schools_last_good_games_and_never_blocks_anything(tmp_path, capsys):
    d = schedules_dir(tmp_path)
    run_schedules(tmp_path, d)
    (d / "wabash-mens-lacrosse-schedule.html").write_text("<html>oops</html>", encoding="utf-8")
    code, sports = run_schedules(tmp_path, d)
    assert code == 0
    assert [(g["date"], g["source"]) for g in sports["mens-lacrosse"]["games"]] == [("2027-04-10", "wabash")]
    err = capsys.readouterr().err
    assert re.search(r"Men's Lacrosse\s+Wabash\s+schedule not updated", err)


def test_depauw_schedules_alone_still_work_as_before(tmp_path):
    code, sports = run_schedules(tmp_path, schedules_dir(tmp_path, wabash=False))
    assert code == 0 and "wabash_schedule_url" not in sports["football"]
    assert all("source" not in g for g in sports["football"]["games"])


# ---- next meeting on the page ------------------------------------------------------------------ #

def test_a_game_only_wabashs_schedule_lists_shows_on_the_page_with_its_source(tmp_path):
    g = {**sched("2026-11-14"), "source": "wabash"}
    both = {**sched("2026-11-20"), "source": "both"}
    html = meeting_page(tmp_path, {"football": entry(g), "mens-lacrosse": entry(both)})
    top = text_of(html[html.index('id="next-meeting"'):html.index('id="jump-h"')])
    assert "Sat, Nov 14 · 1:07 PM · Neutral site · Scheumann Stadium, Muncie, Ind. (from Wabash’s schedule)" in top
    lax = text_of(html[html.index('id="mens-lacrosse"'):html.index("</article>", html.index('id="mens-lacrosse"'))])
    assert "Nov 20" in lax and "from Wabash" not in lax


# ---- undated games: labels, order, "most recent" ------------------------------------------------ #

def undated(date, result, d, w, season, source="wabash"):
    x = game(date, result, d, w, "unknown", None)
    x.update({"source": source, "date_known": False, "season": season})
    return x


def test_an_undated_game_in_a_two_year_season_shows_the_season_and_never_a_day(tmp_path):
    old = undated("1954-09-03", "L", 40, 55, "1954-55")
    out, html = build(tmp_path, {"mens-basketball": merged_sport("mens-basketball", "Men's Basketball",
                                                                 [old, game("1955-01-10", "W", 80, 70)])})
    row = re.findall(r"<tr>.*?</tr>", card(html, "mens-basketball"), re.S)[-1]
    assert "1954-55" in text_of(row) and "(date not recorded)" in text_of(row)
    assert "Sep 3" not in html and "1954-09-03" not in html
    first = list(csv.DictReader(io.StringIO((out / "games.csv").read_text(encoding="utf-8"))))[0]
    assert (first["date"], first["season"], first["date_recorded"]) == ("", "1954-55", "no")


def test_most_recent_ignores_a_game_with_no_date_even_when_it_is_listed_last(tmp_path):
    games = [game("2003-11-08", "W", 41, 20), game("2004-11-13", "W", 28, 3)]
    games.append(undated("2005-01-01", "L", 3, 9, "2005"))          # a stray undated row at the end of the list
    _, html = build(tmp_path, {"football": merged_sport("football", "Football", games)})
    recent = text_of(card(html, "football"))
    assert "Most recent Nov 13, 2004 · DePauw 28, Wabash 3" in recent


def test_games_are_listed_in_the_stored_order_not_re_sorted_by_a_stored_date(tmp_path):
    a = undated("1962-09-03", "L", 40, 55, "1962-63")
    b = game("1962-12-10", "W", 60, 50)
    b["season"] = "1962-63"
    c = game("1963-01-12", "W", 61, 50)
    c["season"] = "1962-63"
    _, html = build(tmp_path, {"mens-basketball": merged_sport("mens-basketball", "Men's Basketball", [a, b, c])})
    rows = [text_of(r) for r in re.findall(r"<tr>.*?</tr>", card(html, "mens-basketball"), re.S) if "<td" in r]
    assert [r.strip()[:7] for r in rows] == ["Jan 12,", "Dec 10,", "1962-63"]          # newest first; the undated one last


# ---- fun facts that several games share ---------------------------------------------------------- #

def repeated(n, d=9, w=0):
    return [game(f"{2000 + i}-04-0{1 + i % 8}", "W", d, w) for i in range(n)]


def test_a_repeated_record_says_how_many_times_and_when_it_last_happened(tmp_path):
    games = repeated(36) + [game("2026-04-24", "W", 9, 0), game("2025-04-03", "L", 4, 5)]
    s = merged_sport("mens-tennis", "Men's Tennis", games, unit=["match", "matches"], total_label="individual matches")
    _, html = build(tmp_path, {"mens-tennis": s})
    t = text_of(card(html, "mens-tennis"))
    assert "Biggest DePauw win: DePauw 9, Wabash 0 · by 9 matches (37 times, most recently Apr 24, 2026)" in t
    assert "happened" not in t and "most recent shown" not in t


def test_a_record_that_happened_once_has_no_times_suffix(tmp_path):
    s = merged_sport("football", "Football", [game("2003-11-08", "W", 41, 20), game("2004-11-13", "W", 28, 3)])
    _, html = build(tmp_path, {"football": s})
    t = text_of(card(html, "football"))
    assert "Biggest DePauw win: DePauw 28, Wabash 3 · Nov 13, 2004 · by 25 points Longest" in t     # nothing in parentheses


def test_the_most_recently_date_skips_undated_games_in_a_tie(tmp_path):
    old = undated("1895-01-01", "W", 39, 2, "1895")
    games = [old, game("2004-11-13", "W", 39, 2)]
    _, html = build(tmp_path, {"football": merged_sport("football", "Football", games)})
    t = text_of(card(html, "football"))
    assert "Biggest DePauw win: DePauw 39, Wabash 2 · by 37 points (twice, most recently Nov 13, 2004)" in t
    # and when every game of a tie is undated, the fact says so rather than inventing a day
    only = [undated("1895-01-01", "W", 39, 2, "1895"), undated("1896-01-01", "W", 39, 2, "1896"),
            game("2004-11-13", "W", 3, 2)]
    _, html = build(tmp_path, {"football": merged_sport("football", "Football", only)})
    t = text_of(card(html, "football"))
    assert "(twice, most recently 1896 (date not recorded))" in t and "Jan 1" not in t


def test_a_win_streak_through_undated_games_names_years_not_days():
    import facts
    games = [undated("1895-01-01", "W", 5, 1, "1895"), undated("1896-01-01", "W", 5, 1, "1896"),
             game("1897-04-01", "W", 5, 1), game("1898-04-01", "L", 1, 5)]
    run = facts.longest_streak(games, "W")
    assert (run["games"], run["first"], run["last"]) == (3, "1895", "1897")


def test_the_disputed_game_labels_say_whose_record_and_in_which_order(tmp_path):
    g = game("1948-02-24", "W", 48, 34)
    g.update({"source": "both", "disputed": True, "wabash_version": {"result": "W", "depauw": 48, "wabash": 32}})
    s = merged_sport("mens-basketball", "Men's Basketball", [g, game("1950-01-10", "W", 80, 70)])
    _, html = build(tmp_path, {"mens-basketball": s})
    assert "DePauw’s record (DePauw-Wabash): 48-34 · Wabash’s record (DePauw-Wabash): 48-32" in text_of(card(html, "mens-basketball"))
    assert "alt-score" not in html
