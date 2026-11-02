"""Tests for the DePauw vs. Wabash scraper, run against markup captured from depauwtigers.com."""

import datetime as dt
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
import scrape  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
BASE = "https://depauwtigers.com"


def parse(html):
    return scrape.parse_page(html, base_url=BASE, opponent_match="wabash")


MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]


def full_html(sport: str) -> str:
    """Rebuild a sport's full page: the real page markup from its subset fixture, with the history
    table body filled from the complete captured game list (<sport>-expected.json)."""
    page = (FIX / f"{sport}-subset.html").read_text(encoding="utf-8")
    ref = json.loads((FIX / f"{sport}-expected.json").read_text(encoding="utf-8"))
    rows = []
    for date, season, loc, site, res, us, them, note in ref["games"]:
        m, d, y = date.split("/")
        long_date = f"{MONTHS[int(m)-1]} {int(d)}, {y}"
        if us is None:       # a result with no score: the page prints "L -" and says so beside it
            score_text = f"{res} -"
        else:
            score_text = f"{res} {us} - {them}"
        rows.append(
            f'<tr><th scope="row"><span class="hide-on-large">{long_date}</span><span class="hide-on-medium-down">{date}</span></th>'
            f'<td class="sidearm-table-custom-field" data-label="Season"> {season} </td>'
            f'<td class="sidearm-table-custom-field location" data-label="Location"><div><span class="hide-on-medium-down"> {loc} </span><span>{site}</span></div></td>'
            f'<td class="sidearm-table-custom-field score {res}" data-label="Score"><div><span class="hide-on-medium-down post-score">{(" " + note + " ") if note else ""}</span><span> {score_text} </span></div></td>'
            f'<td class="sidearm-table-custom-field" data-label="Media"><div class="media-list"></div></td></tr>'
        )
    # Replace only the history table's body (the last-matchup box score has its own <tbody>).
    # The class list and attribute order differ a little between sports.
    start = re.search(r'<table[^>]*class="sidearm-table[ "][^>]*>', page).start()
    head, table = page[:start], page[start:]
    table = re.sub(r"<tbody>.*?</tbody>", "<tbody>" + "".join(rows) + "</tbody>", table, flags=re.S, count=1)
    return head + table


def full_football_html() -> str:
    return full_html("football")


# --------------------------------------------------------------------------- #
# Row parsing (real markup)
# --------------------------------------------------------------------------- #

def test_football_rows_parse_correctly():
    games, summary, skipped = parse((FIX / "football-subset.html").read_text())
    assert len(games) == 18 and skipped == []

    latest = games[0]
    assert (latest.date, latest.season, latest.location, latest.site) == ("2025-11-15", "2025", "away", "Crawfordsville, Ind.")
    assert (latest.result, latest.depauw, latest.wabash) == ("W", 41, 20)
    assert latest.boxscore == "https://depauwtigers.com/boxscore.aspx?id=18817&path=football"

    ot = next(g for g in games if g.date == "2023-11-11")
    assert ot.note == "2 OT" and (ot.depauw, ot.wabash) == (33, 30)

    tie = next(g for g in games if g.date == "1992-11-14")
    assert tie.result == "T" and tie.depauw == tie.wabash == 17

    neutral = next(g for g in games if g.date == "1922-11-25")
    assert neutral.location == "neutral" and neutral.site == "Irwin Field; Indianapolis, Ind."

    first = games[-1]
    assert first.date == "1890-11-22" and first.site is None and first.boxscore is None

    # Summary block read straight from the page
    assert (summary.wins, summary.losses, summary.ties) == (59, 63, 9)
    assert summary.home == (29, 29, 3) and summary.away == (28, 31, 5)


def test_validation_catches_missing_games():
    """The subset fixture only has 18 of 131 games, so validation must flag it."""
    games, summary, _ = parse((FIX / "football-subset.html").read_text())
    problems = scrape.validate(games, summary)
    assert any(p.startswith("wins:") for p in problems)


# --------------------------------------------------------------------------- #
# Full-series tallies agree with the page's own summary
# --------------------------------------------------------------------------- #

def test_full_football_matches_page_summary():
    games, summary, _ = parse(full_football_html())
    assert len(games) == 131
    assert scrape.validate(games, summary) == []
    t = scrape.tally(games)
    assert (t["wins"], t["losses"], t["ties"]) == (59, 63, 9)
    assert t["by_location"]["neutral"] == (2, 3, 1)  # neutral-site games aren't in the page's home/away boxes


def test_lacrosse_matches_page_summary():
    games, summary, _ = parse((FIX / "mens-lacrosse.html").read_text())
    assert len(games) == 12
    assert summary.ties == 0  # page has no Ties box; treated as zero
    assert scrape.validate(games, summary) == []
    assert all(g.result == "W" for g in games)
    assert games[0].date == "2026-04-04" and (games[0].depauw, games[0].wabash) == (14, 5)


# --------------------------------------------------------------------------- #
# Guard rails
# --------------------------------------------------------------------------- #

def test_wrong_opponent_is_rejected():
    html = (FIX / "mens-lacrosse.html").read_text().replace(
        'breadcrumbs-opponent">Wabash College', 'breadcrumbs-opponent">Denison University')
    with pytest.raises(scrape.ParseError, match="Denison"):
        parse(html)


def test_layout_change_is_rejected():
    with pytest.raises(scrape.ParseError, match="article not found"):
        parse("<html><body><div>Site redesign</div></body></html>")


def test_class_and_text_disagreement_is_rejected():
    html = (FIX / "mens-lacrosse.html").read_text().replace("score W", "score L", 1)
    with pytest.raises(scrape.ParseError, match="cell class"):
        parse(html)


def test_canceled_row_is_skipped_not_counted():
    html = (FIX / "mens-lacrosse.html").read_text().replace("<span> W 9 - 8 </span>", "<span> Canceled </span>", 1)
    games, summary, skipped = parse(html)
    assert len(games) == 11 and skipped[0]["text"] == "Canceled"
    assert scrape.validate(games, summary)  # and the count mismatch is reported


# --------------------------------------------------------------------------- #
# End to end: fixtures in, series.json out
# --------------------------------------------------------------------------- #

@pytest.fixture
def fixture_dir(tmp_path):
    d = tmp_path / "pages"
    d.mkdir()
    (d / "football.html").write_text(full_html("football"), encoding="utf-8")
    (d / "mens-basketball.html").write_text(full_html("mens-basketball"), encoding="utf-8")
    (d / "baseball.html").write_text(full_html("baseball"), encoding="utf-8")
    (d / "mens-soccer.html").write_text((FIX / "mens-soccer.html").read_text(encoding="utf-8"), encoding="utf-8")
    (d / "mens-lacrosse.html").write_text((FIX / "mens-lacrosse.html").read_text(encoding="utf-8"), encoding="utf-8")
    (d / "mens-tennis.html").write_text((FIX / "mens-tennis.html").read_text(encoding="utf-8"), encoding="utf-8")
    (d / "mens-swimming-and-diving.html").write_text(full_html("mens-swimming-and-diving"), encoding="utf-8")
    return d


WABASH_BASE = "https://sports.wabash.edu"
SPORT_KEYS = ["football", "mens-basketball", "baseball", "mens-soccer", "mens-lacrosse", "mens-tennis",
              "mens-swimming-and-diving"]


def parse_wabash(html):
    return scrape.parse_page(html, base_url=WABASH_BASE, opponent_match="depauw")


@pytest.fixture
def both_dir(fixture_dir, tmp_path):
    """DePauw's six pages plus Wabash's six, rebuilt from the real markup captured on 2026-10-28."""
    d = tmp_path / "both"
    d.mkdir()
    for page in fixture_dir.iterdir():
        (d / page.name).write_text(page.read_text(encoding="utf-8"), encoding="utf-8")
    for key in SPORT_KEYS:
        (d / f"wabash-{key}.html").write_text(full_html(f"wabash-{key}"), encoding="utf-8")
    return d


def test_end_to_end_writes_series_json(fixture_dir, tmp_path, capsys):
    out = tmp_path / "series.json"
    assert scrape.main(["--fixtures", str(fixture_dir), "--output", str(out)]) == 0
    data = json.loads(out.read_text())

    # Sports come out in sources.json order, which is the order the page shows them.
    assert list(data["sports"]) == ["football", "mens-basketball", "baseball", "mens-soccer",
                                    "mens-lacrosse", "mens-tennis", "mens-swimming-and-diving"]
    sp = data["sports"]
    assert sp["football"]["record"] == {"depauw_wins": 59, "wabash_wins": 63, "ties": 9}
    assert sp["football"]["first_game"] == "1890-11-22" and sp["football"]["last_game"] == "2025-11-15"
    assert {k: (v["record"]["depauw_wins"], v["record"]["wabash_wins"], v["record"]["ties"], v["games_played"])
            for k, v in sp.items()} == {
        "football": (59, 63, 9, 131), "mens-basketball": (130, 100, 1, 231), "baseball": (84, 57, 0, 141),
        "mens-soccer": (41, 18, 6, 65), "mens-lacrosse": (12, 0, 0, 12), "mens-tennis": (33, 0, 0, 33),
        "mens-swimming-and-diving": (19, 11, 0, 30)}
    assert {k: v["current_streak"] for k, v in sp.items()} == {
        "football": "W4", "mens-basketball": "L1", "baseball": "L1",
        "mens-soccer": "W2", "mens-lacrosse": "W12", "mens-tennis": "W33", "mens-swimming-and-diving": "W1"}
    assert data["totals"] == {"depauw_wins": 378, "wabash_wins": 249, "ties": 16, "games_played": 643}
    assert data["updated_at"]

    # Each sport carries the label for its total; tennis overrides the page's "points".
    assert {k: v["total_label"] for k, v in sp.items()} == {
        "football": "points", "mens-basketball": "points", "baseball": "runs",
        "mens-soccer": "goals", "mens-lacrosse": "goals", "mens-tennis": "individual matches",
        "mens-swimming-and-diving": "points"}
    assert sp["baseball"]["points"] == {"depauw": 904, "wabash": 709}
    # Swimming scores can end in .5; the total keeps the half and a whole total stays a whole number.
    assert sp["mens-swimming-and-diving"]["points"] == {"depauw": 4459.5, "wabash": 3968.5}
    assert sp["mens-swimming-and-diving"]["unit"] == ["meet", "meets"]

    # A second run with identical pages must not rewrite the file (no empty scheduled commits).
    stamp = data["updated_at"]
    assert scrape.main(["--fixtures", str(fixture_dir), "--output", str(out)]) == 0
    assert json.loads(out.read_text())["updated_at"] == stamp
    assert "unchanged" in capsys.readouterr().out


def test_bad_page_keeps_last_good_data(fixture_dir, tmp_path):
    out = tmp_path / "series.json"
    scrape.main(["--fixtures", str(fixture_dir), "--output", str(out)])
    good = json.loads(out.read_text())["sports"]["football"]

    (fixture_dir / "football.html").write_text("<html><body>Service unavailable</body></html>")
    assert scrape.main(["--fixtures", str(fixture_dir), "--output", str(out)]) == 1
    assert json.loads(out.read_text())["sports"]["football"] == good


def test_shrinking_history_is_refused(fixture_dir, tmp_path):
    out = tmp_path / "series.json"
    scrape.main(["--fixtures", str(fixture_dir), "--output", str(out)])
    lax = (fixture_dir / "mens-lacrosse.html").read_text()
    # Drop the oldest game *and* adjust the summary so the page is self-consistent but shorter.
    lax = re.sub(r'<tr><th scope="row"><span class="hide-on-large">April 2, 2015.*?</tr>', "", lax, flags=re.S)
    lax = lax.replace('number">12</div>', 'number">11</div>').replace(" 6-0 ", " 5-0 ", 1)
    lax = lax.replace("streak W12", "streak W11").replace("> W12<", "> W11<")
    (fixture_dir / "mens-lacrosse.html").write_text(lax)
    assert scrape.main(["--fixtures", str(fixture_dir), "--output", str(out)]) == 1
    assert json.loads(out.read_text())["sports"]["mens-lacrosse"]["games_played"] == 12
    # --allow-shrink only lifts the count check: dropping an old game is still a historical change, so it
    # is held for review (tests/test_review.py) and the record keeps all 12 until it is approved.
    assert scrape.main(["--fixtures", str(fixture_dir), "--output", str(out), "--allow-shrink"]) == 0
    assert json.loads(out.read_text())["sports"]["mens-lacrosse"]["games_played"] == 12
    assert scrape.main(["--fixtures", str(fixture_dir), "--output", str(out), "--allow-shrink",
                        "--apply-reviewed"]) == 0
    assert json.loads(out.read_text())["sports"]["mens-lacrosse"]["games_played"] == 11


# --------------------------------------------------------------------------- #
# More sports: basketball, baseball, soccer, tennis
# --------------------------------------------------------------------------- #

def G(date, result, us, them, *, loc="home", box=None, note=None):
    return scrape.Game(date=date, season=date[:4], location=loc, site=None, result=result,
                       depauw=us, wabash=them, note=note, boxscore=box)


def test_soccer_matches_page_summary():
    games, summary, skipped = parse((FIX / "mens-soccer.html").read_text(encoding="utf-8"))
    assert len(games) == 65 and skipped == []
    assert scrape.validate(games, summary) == []
    t = scrape.tally(games)
    assert (t["wins"], t["losses"], t["ties"]) == (41, 18, 6)
    assert summary.ties == 6 and summary.home == (23, 9, 3) and summary.away == (18, 9, 3)
    assert games[0].date == "2026-10-21" and (games[0].result, games[0].depauw, games[0].wabash) == ("W", 5, 0)
    assert games[-1].date == "1966-10-29" and games[-1].site is None and games[-1].result == "L"


def test_tennis_team_match_scores_parse():
    games, summary, _ = parse((FIX / "mens-tennis.html").read_text(encoding="utf-8"))
    assert len(games) == 33
    assert scrape.validate(games, summary) == []
    assert all(g.result == "W" for g in games)
    neutral = next(g for g in games if g.date == "2026-04-24")
    assert neutral.location == "neutral" and neutral.site == "Denison University; Granville, Ohio"
    assert (neutral.depauw, neutral.wabash) == (5, 0)
    assert any((g.depauw, g.wabash) == (9, 0) for g in games)
    assert any((g.depauw, g.wabash) == (4, 3) for g in games)  # the page's smallest margin of victory


def test_tennis_loss_row_parses():
    """The tennis page has no losses, so check a loss like "L 0 - 9" by editing a real row."""
    html = (FIX / "mens-tennis.html").read_text(encoding="utf-8")
    row = re.search(r'<tr><th scope="row"><span class="hide-on-large">April 24, 2026.*?</tr>', html, flags=re.S).group(0)
    edited = row.replace("score W", "score L").replace(" W 5 - 0 ", " L 0 - 9 ")
    assert edited != row
    games, _, _ = parse(html.replace(row, edited))
    g = next(g for g in games if g.date == "2026-04-24")
    assert (g.result, g.depauw, g.wabash) == ("L", 0, 9)


def test_full_basketball_matches_page_summary():
    games, summary, _ = parse(full_html("mens-basketball"))
    assert len(games) == 231
    assert scrape.validate(games, summary) == []
    t = scrape.tally(games)
    assert (t["wins"], t["losses"], t["ties"]) == (130, 100, 1)
    assert t["by_location"]["neutral"] == (0, 1, 0)
    tie = next(g for g in games if g.result == "T")
    assert (tie.date, tie.depauw, tie.wabash) == ("1954-12-04", 67, 67)
    assert next(g for g in games if g.date == "1960-02-23").note == "2OT"
    assert next(g for g in games if g.date == "1976-01-21").note == "(1ot)"
    assert next(g for g in games if g.date == "1960-02-23").site is None  # the page says N/A


def test_full_baseball_matches_page_summary_with_doubleheaders():
    games, summary, _ = parse(full_html("baseball"))
    assert len(games) == 141
    assert scrape.validate(games, summary) == []
    t = scrape.tally(games)
    assert (t["wins"], t["losses"], t["ties"]) == (84, 57, 0)
    # Doubleheaders: two rows on one date count as two games, even with the same score.
    twice = [g for g in games if g.date == "2012-04-28"]
    assert [(g.result, g.depauw, g.wabash) for g in twice] == [("L", 4, 7), ("L", 4, 7)]
    assert next(g for g in games if g.date == "2023-04-04" and g.depauw == 4).note == "(7 inn.)"


def test_doubleheader_order_gives_the_pages_streak():
    """The page lists newest first. On 2026-04-28 game 2 (L 3-15) was last, so the streak is L1."""
    games, summary, _ = parse(full_html("baseball"))
    order = [(g.result, g.depauw, g.wabash) for g in scrape.chronological(games) if g.date == "2026-04-28"]
    assert order == [("W", 13, 2), ("L", 3, 15)]
    assert summary.streak == "L1" and scrape.streak_code(scrape.chronological(games)) == "L1"


def test_streak_disagreeing_with_the_page_is_rejected():
    games, summary, _ = parse((FIX / "mens-lacrosse.html").read_text(encoding="utf-8"))
    summary.streak = "L3"
    assert any(p.startswith("streak:") for p in scrape.validate(games, summary))


@pytest.mark.parametrize("sport,label", [
    ("football", "points"), ("mens-basketball", "points"), ("baseball", "runs"),
    ("mens-soccer", "goals"), ("mens-lacrosse", "goals"), ("mens-tennis", "points"),
])
def test_page_total_label_and_figure_match_the_games(sport, label):
    if sport in ("football", "mens-basketball", "baseball"):
        html = full_html(sport)
    else:
        html = (FIX / f"{sport}.html").read_text(encoding="utf-8")
    games, summary, _ = parse(html)
    assert summary.total_label == label
    assert summary.total == sum(g.depauw for g in games)  # the page's own total agrees with our parse


def test_same_date_same_score_doubleheader_is_not_a_duplicate():
    games = [G("2012-04-28", "L", 4, 7), G("2012-04-28", "L", 4, 7)]
    summary = scrape.Summary(wins=0, losses=2, ties=0, home=(0, 2, 0))
    assert scrape.validate(games, summary) == []


def test_true_duplicate_rows_are_still_caught():
    box = "https://depauwtigers.com/boxscore.aspx?id=1&path=baseball"
    repeated_box = [G("2020-04-01", "W", 5, 1, box=box), G("2020-04-02", "W", 6, 2, box=box)]
    summary = scrape.Summary(wins=2, losses=0, ties=0, home=(2, 0, 0))
    assert any("box score" in p for p in scrape.validate(repeated_box, summary))

    triple = [G("2020-04-01", "W", 5, 1)] * 3
    summary = scrape.Summary(wins=3, losses=0, ties=0, home=(3, 0, 0))
    assert any("three or more" in p for p in scrape.validate(triple, summary))


def test_a_page_that_repeats_its_table_is_rejected():
    html = full_html("baseball")
    body = re.search(r"<tbody>.*?</tbody>", html[html.index('class="sidearm-table'):], flags=re.S).group(0)
    doubled = html.replace(body, body[:-len("</tbody>")] + body[len("<tbody>"):], 1)
    games, summary, _ = parse(doubled)
    assert len(games) == 282
    assert scrape.validate(games, summary)  # the tallies no longer match the page's summary


def test_canceled_row_in_a_new_sport_is_skipped():
    html = (FIX / "mens-soccer.html").read_text(encoding="utf-8")
    row = re.search(r'<tr><th scope="row"><span class="hide-on-large">October 21, 2026.*?</tr>', html, flags=re.S).group(0)
    canceled = row.replace(" W 5 - 0 ", " Canceled ").replace("score W", "score")
    games, _, skipped = parse(html.replace(row, canceled))
    assert len(games) == 64 and len(skipped) == 1 and skipped[0]["date"] == "2026-10-21"


def test_reordering_sources_rewrites_the_file(fixture_dir, tmp_path, monkeypatch):
    out = tmp_path / "series.json"
    assert scrape.main(["--fixtures", str(fixture_dir), "--output", str(out)]) == 0
    cfg = json.loads(scrape.SOURCES.read_text())
    cfg["sports"].reverse()
    reordered = tmp_path / "sources.json"
    reordered.write_text(json.dumps(cfg))
    monkeypatch.setattr(scrape, "SOURCES", reordered)
    assert scrape.main(["--fixtures", str(fixture_dir), "--output", str(out)]) == 0
    assert list(json.loads(out.read_text())["sports"])[0] == "mens-swimming-and-diving"


# --------------------------------------------------------------------------- #
# "Last checked": only a live run that reached at least one sport advances it
# --------------------------------------------------------------------------- #

@pytest.fixture
def live(both_dir, monkeypatch):
    """Make live fetches serve the fixture pages of both schools, with a switch to break individual
    sports (by sport key, at both schools)."""
    state = {"broken": set(), "robots": {}, "requested": []}

    def fake_fetch(url):
        state["requested"].append(url)
        if url.endswith("/robots.txt"):
            return state["robots"].get("wabash" if "wabash.edu" in url else "depauw",
                                       "User-agent: *\nAllow: /\nCrawl-delay: 30\n")
        wabash = "wabash.edu" in url
        slug = re.search(r"/sports/([^/]+)/", url).group(1)
        key = {"tennis": "mens-tennis", "swimming-and-diving": "mens-swimming-and-diving"}.get(slug, slug)             if wabash else slug                                          # Wabash's slugs for tennis and swimming
        if url.endswith("/schedule"):
            raise OSError("schedule pages are not part of these tests")
        if key in state["broken"]:
            raise OSError("network down")
        return (both_dir / (f"wabash-{key}.html" if wabash else f"{key}.html")).read_text(encoding="utf-8")

    monkeypatch.setattr(scrape, "fetch", fake_fetch)
    return state


def run_live(tmp_path, *extra):
    return scrape.main(["--output", str(tmp_path / "series.json"),
                        "--upcoming-output", str(tmp_path / "upcoming.json"),
                        "--checked-file", str(tmp_path / "checked"), *extra])


def test_good_live_run_records_the_check_time_without_touching_the_data_file(tmp_path, live):
    assert run_live(tmp_path) == 0
    stamp = (tmp_path / "checked").read_text().strip()
    assert dt.datetime.fromisoformat(stamp).tzinfo is not None
    data_before = (tmp_path / "series.json").read_text()
    assert run_live(tmp_path) == 0                      # nothing changed in the games
    assert (tmp_path / "series.json").read_text() == data_before
    assert "checked" not in data_before


def test_partial_failure_still_counts_as_a_check_but_exits_red(tmp_path, live):
    live["broken"] = {"football"}
    assert run_live(tmp_path) == 1
    assert (tmp_path / "checked").exists()


def test_every_sport_failing_does_not_advance_the_check_time(tmp_path, live):
    assert run_live(tmp_path) == 0
    (tmp_path / "checked").write_text("2026-01-01T00:00:00+00:00\n")
    live["broken"] = set(SPORT_KEYS)
    assert run_live(tmp_path) == 1
    assert (tmp_path / "checked").read_text() == "2026-01-01T00:00:00+00:00\n"


def test_fixture_and_dry_runs_are_not_checks(tmp_path, fixture_dir, live):
    assert scrape.main(["--fixtures", str(fixture_dir), "--output", str(tmp_path / "series.json"),
                        "--checked-file", str(tmp_path / "checked")]) == 0
    assert run_live(tmp_path, "--dry-run") == 0
    assert not (tmp_path / "checked").exists()


# ---- run scopes: a game-day run reads only today's sport ------------------------------------------------ #

def pages_read(live):
    return [u for u in live["requested"] if not u.endswith("/robots.txt")]


def test_a_full_run_reads_every_sport_at_both_schools_and_robots_txt_once_per_host(tmp_path, live):
    assert run_live(tmp_path) == 0
    history = [u for u in pages_read(live) if "/opponent-history/" in u]
    assert len(history) == 2 * len(SPORT_KEYS)
    assert sorted(u for u in live["requested"] if u.endswith("/robots.txt")) == [
        "https://depauwtigers.com/robots.txt", "https://sports.wabash.edu/robots.txt"]


def test_a_game_day_run_reads_only_that_sport_and_keeps_every_other_sport_as_it_was(tmp_path, live):
    assert run_live(tmp_path) == 0
    saved = {n: read(tmp_path, n) for n in ("depauw.json", "wabash.json", "series.json")}
    (tmp_path / "checked").write_text("2026-01-01T00:00:00+00:00\n")
    live["requested"].clear()
    assert run_live(tmp_path, "--only", "football") == 0
    assert pages_read(live) and all("/sports/football/" in u for u in pages_read(live))
    assert len(pages_read(live)) == 4                                   # history and schedule, at both schools
    for name, before in saved.items():
        assert read(tmp_path, name)["sports"] == before["sports"]       # the same pages give the same data
    assert (tmp_path / "checked").read_text() != "2026-01-01T00:00:00+00:00\n"   # a real check: the time moves


def test_a_failed_page_in_a_game_day_run_keeps_its_last_good_data_and_exits_red(tmp_path, live):
    assert run_live(tmp_path) == 0
    before = read(tmp_path, "depauw.json")["sports"]
    live["broken"] = {"football"}
    assert run_live(tmp_path, "--only", "football") == 1
    assert read(tmp_path, "depauw.json")["sports"] == before


def test_a_game_day_run_keeps_the_review_entries_of_the_sports_it_did_not_read(tmp_path, live):
    assert run_live(tmp_path) == 0
    entry = {"id": "x1", "school": "depauw", "sport": "baseball", "kind": "changed", "season": "1990",
             "old": {"date": "1990-04-01"}, "new": {"date": "1990-04-02"}, "found_at": "2026-10-30T12:00:00+00:00",
             "source_url": "https://depauwtigers.com/sports/baseball/opponent-history/wabash/215"}
    (tmp_path / "pending-changes.json").write_text(json.dumps({"schema_version": 1, "changes": [entry]}))
    assert run_live(tmp_path, "--only", "football") == 0
    assert read(tmp_path, "pending-changes.json")["changes"] == [entry]     # not read this run, so still waiting


def test_the_shrink_check_still_applies_to_the_sport_a_game_day_run_reads(tmp_path, live):
    assert run_live(tmp_path) == 0
    depauw = read(tmp_path, "depauw.json")
    depauw["sports"]["football"]["games_played"] += 5                   # the saved copy says it had more games
    (tmp_path / "depauw.json").write_text(json.dumps(depauw, indent=2) + "\n", encoding="utf-8")
    assert run_live(tmp_path, "--only", "football") == 1
    assert read(tmp_path, "depauw.json")["sports"]["football"]["games_played"] == \
        depauw["sports"]["football"]["games_played"]


def test_an_empty_scope_reads_nothing_and_does_not_count_as_a_check(tmp_path, live):
    assert run_live(tmp_path) == 0
    (tmp_path / "checked").write_text("2026-01-01T00:00:00+00:00\n")
    live["requested"].clear()
    assert run_live(tmp_path, "--only", "") == 0
    assert live["requested"] == []
    assert (tmp_path / "checked").read_text() == "2026-01-01T00:00:00+00:00\n"


def test_an_unknown_sport_in_the_scope_stops_the_run(tmp_path, live):
    assert run_live(tmp_path, "--only", "curling") == 1
    assert live["requested"] == []


def test_a_page_robots_txt_disallows_is_not_requested_keeps_its_data_and_is_listed(tmp_path, live):
    assert run_live(tmp_path) == 0
    before = read(tmp_path, "depauw.json")["sports"]["football"]
    live["robots"]["depauw"] = "User-agent: *\nDisallow: /sports/football/opponent-history/\nCrawl-delay: 30\n"
    live["requested"].clear()
    blocked = tmp_path / "blocked.txt"
    assert run_live(tmp_path, "--only", "football", "--blocked-file", str(blocked)) == 1
    url = "https://depauwtigers.com/sports/football/opponent-history/wabash/215"
    assert url not in live["requested"]
    assert blocked.read_text(encoding="utf-8") == url + "\n"
    assert read(tmp_path, "depauw.json")["sports"]["football"] == before


def test_a_run_with_nothing_blocked_writes_no_blocked_file(tmp_path, live):
    assert run_live(tmp_path, "--only", "football", "--blocked-file", str(tmp_path / "blocked.txt")) == 0
    assert not (tmp_path / "blocked.txt").exists()


def test_fixture_runs_never_read_robots_txt_or_wait(tmp_path, fixture_dir, live):
    assert scrape.main(["--fixtures", str(fixture_dir), "--output", str(tmp_path / "series.json"),
                        "--upcoming-output", str(tmp_path / "upcoming.json")]) == 0
    assert live["requested"] == [] and scrape.SLEPT == []


# --------------------------------------------------------------------------- #
# Both schools: Wabash's pages, the merge, and the per-school raw files
# --------------------------------------------------------------------------- #

def run_both(both_dir, tmp_path, *extra):
    return scrape.main(["--fixtures", str(both_dir), "--output", str(tmp_path / "series.json"),
                        "--upcoming-output", str(tmp_path / "upcoming.json"), *extra])


def read(tmp_path, name):
    return json.loads((tmp_path / name).read_text(encoding="utf-8"))


def test_wabash_rows_are_read_from_wabashs_side_and_flipped_for_the_site():
    games, summary, skipped = parse_wabash((FIX / "wabash-football-subset.html").read_text(encoding="utf-8"))
    assert skipped == [] and (summary.wins, summary.losses, summary.ties) == (63, 59, 9)   # Wabash's own view
    latest = games[0]
    assert (latest.date, latest.result, latest.location) == ("2025-11-15", "L", "home")    # Wabash lost at home
    flipped = scrape.game_dicts(games, "wabash")[-1]
    assert flipped["date"] == "2025-11-15" and (flipped["result"], flipped["depauw"], flipped["wabash"]) == ("W", 41, 20)
    assert flipped["location"] == "away" and flipped["site"] == "Crawfordsville, IN"


def test_wabashs_old_games_keep_their_placeholder_dates_as_the_page_gives_them():
    games, _, _ = parse_wabash((FIX / "wabash-mens-tennis-subset.html").read_text(encoding="utf-8"))
    assert min(g.date for g in games) == "1915-01-01"


def test_every_wabash_page_agrees_with_its_own_summary():
    for key in SPORT_KEYS:
        games, summary, skipped = parse_wabash(full_html(f"wabash-{key}"))
        assert scrape.validate(games, summary) == [] and skipped == [], key


def test_end_to_end_merges_both_schools_and_keeps_each_schools_games(both_dir, tmp_path, capsys):
    assert run_both(both_dir, tmp_path) == 0
    data = read(tmp_path, "series.json")
    sp = data["sports"]
    assert {k: v["games_played"] for k, v in sp.items()} == {
        "football": 132, "mens-basketball": 238, "baseball": 316, "mens-soccer": 65,
        "mens-lacrosse": 12, "mens-tennis": 123, "mens-swimming-and-diving": 41}
    # No rulings file next to this output, so the swimming meet of March 19-21, 2021 counts as three here.
    assert data["totals"] == {"depauw_wins": 544, "wabash_wins": 363, "ties": 20, "games_played": 927}
    assert sp["football"]["record"] == {"depauw_wins": 60, "wabash_wins": 63, "ties": 9}   # 1891 counted twice until ruled on
    assert sp["mens-tennis"]["record"] == {"depauw_wins": 115, "wabash_wins": 7, "ties": 1}
    # Everything derived comes from the merged games, not from either school's page.
    assert (sp["baseball"]["first_game"], sp["baseball"]["last_game"]) == ("1888-01-03", "2026-04-28")
    assert {k: v["current_streak"] for k, v in sp.items()} == {
        "football": "W4", "mens-basketball": "L1", "baseball": "L1",
        "mens-soccer": "W2", "mens-lacrosse": "W12", "mens-tennis": "W61", "mens-swimming-and-diving": "W1"}
    # 1817-1734: Wabash's page prints three old baseball scores in the opposite order from their result
    # letter (W 7-19, W 7-10, W 1-2 in DePauw's view); trusting the letter moves 16 runs to DePauw.
    assert sp["baseball"]["points"] == {"depauw": 1817, "wabash": 1734}
    assert sp["football"]["sources"] == {"both": 130, "depauw_only": 1, "wabash_only": 1, "disputed": 2,
                                         "date_differs": 2, "dates_not_recorded": 0}
    assert sp["mens-tennis"]["unit"] == ["match", "matches"] and "unit" not in sp["football"]
    assert sp["football"]["wabash_source_url"] == \
        "https://sports.wabash.edu/sports/football/opponent-history/depauw-university/4"
    assert sp["mens-tennis"]["wabash_source_url"].endswith("/sports/tennis/opponent-history/depauw-university/4")
    assert {g["source"] for g in sp["baseball"]["games"]} == {"both", "depauw", "wabash"}

    # Each school's own games are kept apart, both in DePauw's point of view.
    depauw, wabash = read(tmp_path, "depauw.json"), read(tmp_path, "wabash.json")
    assert depauw["school"] == "DePauw" and wabash["school"] == "Wabash" and wabash["perspective"] == "DePauw"
    assert depauw["sports"]["baseball"]["games_played"] == 141 and wabash["sports"]["baseball"]["games_played"] == 314
    assert wabash["sports"]["baseball"]["record"] == {"depauw_wins": 155, "wabash_wins": 156, "ties": 3}
    assert depauw["sports"]["mens-tennis"]["record"] == {"depauw_wins": 33, "wabash_wins": 0, "ties": 0}
    assert wabash["sports"]["football"]["games"][0]["result"] == "W"       # Wabash's 1890 loss, flipped

    out = capsys.readouterr().out
    assert "Football: 130 matched, 1 DePauw only, 1 Wabash only, 2 disputed, 2 date-differs" in out
    assert "Men's Tennis: 33 matched, 0 DePauw only, 90 Wabash only, 1 disputed, 0 date-differs" in out


def test_a_second_run_on_unchanged_pages_exits_green_and_writes_nothing(both_dir, tmp_path, capsys):
    assert run_both(both_dir, tmp_path) == 0
    files = ("series.json", "depauw.json", "wabash.json")
    before = {f: (tmp_path / f).read_bytes() for f in files}
    stamp = read(tmp_path, "series.json")["updated_at"]
    capsys.readouterr()
    assert run_both(both_dir, tmp_path) == 0
    assert {f: (tmp_path / f).read_bytes() for f in files} == before
    assert read(tmp_path, "series.json")["updated_at"] == stamp
    out = capsys.readouterr().out
    assert "unchanged" in out and "Wrote" not in out


def test_one_schools_failed_page_keeps_that_schools_last_good_games(both_dir, tmp_path):
    assert run_both(both_dir, tmp_path) == 0
    good = {f: read(tmp_path, f) for f in ("series.json", "depauw.json", "wabash.json")}
    (both_dir / "wabash-football.html").write_text("<html><body>Service unavailable</body></html>")
    assert run_both(both_dir, tmp_path) == 1
    assert read(tmp_path, "wabash.json")["sports"]["football"] == good["wabash.json"]["sports"]["football"]
    assert read(tmp_path, "series.json")["sports"]["football"] == good["series.json"]["sports"]["football"]
    assert read(tmp_path, "depauw.json") == good["depauw.json"]


def test_a_failed_school_does_not_hold_back_the_pages_that_passed(both_dir, tmp_path):
    (both_dir / "wabash-mens-tennis.html").write_text("<html><body>Service unavailable</body></html>")
    assert run_both(both_dir, tmp_path) == 1                      # red, but everything else still published
    sp = read(tmp_path, "series.json")["sports"]
    assert sp["mens-tennis"]["games_played"] == 33 and "sources" not in sp["mens-tennis"]      # DePauw alone
    assert "wabash_source_url" not in sp["mens-tennis"]
    assert sp["baseball"]["games_played"] == 316 and sp["football"]["sources"]["both"] == 130
    assert "mens-tennis" not in read(tmp_path, "wabash.json")["sports"]
    assert (tmp_path / "depauw.json").exists()


def test_each_page_is_checked_against_its_own_summary_never_the_other_schools(both_dir, tmp_path, capsys):
    page = (both_dir / "wabash-baseball.html").read_text(encoding="utf-8")
    page = re.sub(r'<tr><th scope="row"><span class="hide-on-large">.*?</tr>', "", page, count=1, flags=re.S)
    (both_dir / "wabash-baseball.html").write_text(page, encoding="utf-8")        # a game goes missing
    assert run_both(both_dir, tmp_path) == 1
    err = capsys.readouterr().err
    assert "FAIL Baseball" in err and "Wabash" in err
    sp = read(tmp_path, "series.json")["sports"]
    assert sp["baseball"]["games_played"] == 141                                   # DePauw's page still stands
    assert sp["mens-soccer"]["games_played"] == 65


def test_each_schools_shrink_check_uses_that_schools_own_saved_copy(both_dir, tmp_path):
    assert run_both(both_dir, tmp_path) == 0
    wabash = read(tmp_path, "wabash.json")
    wabash["sports"]["mens-lacrosse"]["games_played"] = 13           # Wabash used to list one more
    (tmp_path / "wabash.json").write_text(json.dumps(wabash), encoding="utf-8")
    assert run_both(both_dir, tmp_path) == 1
    assert read(tmp_path, "wabash.json")["sports"]["mens-lacrosse"]["games_played"] == 13     # refused
    assert read(tmp_path, "series.json")["sports"]["mens-lacrosse"]["games_played"] == 12
    assert run_both(both_dir, tmp_path, "--allow-shrink") == 0
    assert read(tmp_path, "wabash.json")["sports"]["mens-lacrosse"]["games_played"] == 12


def test_merged_game_counts_never_trip_the_shrink_check(both_dir, tmp_path):
    """series.json holds the merged games (far more than DePauw's page has), so the baseline is each
    school's own file. A DePauw page with 141 baseball games must not be measured against 316."""
    assert run_both(both_dir, tmp_path) == 0
    assert read(tmp_path, "series.json")["sports"]["baseball"]["games_played"] == 316
    assert run_both(both_dir, tmp_path) == 0


def test_the_first_run_after_the_merge_starts_from_the_old_depauw_only_series_json(fixture_dir, both_dir, tmp_path):
    assert scrape.main(["--fixtures", str(fixture_dir), "--output", str(tmp_path / "series.json")]) == 0
    (tmp_path / "depauw.json").unlink()                              # the repo has no raw files yet
    (tmp_path / "wabash.json").unlink(missing_ok=True)
    legacy = read(tmp_path, "series.json")
    for sport in legacy["sports"].values():                          # the file as it was before games had a source
        for game in sport["games"]:
            game.pop("source")
    (tmp_path / "series.json").write_text(json.dumps(legacy), encoding="utf-8")
    assert run_both(both_dir, tmp_path) == 0
    assert read(tmp_path, "depauw.json")["sports"]["baseball"]["games_played"] == 141
    assert read(tmp_path, "series.json")["sports"]["baseball"]["games_played"] == 316


def test_dry_run_with_both_schools_writes_nothing(both_dir, tmp_path):
    assert run_both(both_dir, tmp_path, "--dry-run") == 0
    assert not any((tmp_path / f).exists() for f in ("series.json", "depauw.json", "wabash.json"))
