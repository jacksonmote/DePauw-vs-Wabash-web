"""Tests for scraper/render.py, using a small made-up series file (never the real data)."""
import datetime as dt
import html
import json
import re
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
import render  # noqa: E402

SRC = "https://depauwtigers.com/sports/{}/opponent-history/wabash/215"


def game(date, result, d, w, location="home", site="Greencastle, Ind.", note=None, box=None):
    return {"date": date, "season": date[:4], "location": location, "site": site,
            "result": result, "depauw": d, "wabash": w, "note": note, "boxscore": box}


def sport(key, name, games, total_label=None):
    r = {"W": 0, "L": 0, "T": 0}
    for g in games:
        r[g["result"]] += 1
    return {"name": name, "source_url": SRC.format(key),
            "record": {"depauw_wins": r["W"], "wabash_wins": r["L"], "ties": r["T"]},
            "games_played": len(games), "first_game": games[0]["date"], "last_game": games[-1]["date"],
            "current_streak": None,
            "points": {"depauw": sum(g["depauw"] for g in games), "wabash": sum(g["wabash"] for g in games)},
            "games": games, "skipped_rows": [],
            **({"total_label": total_label} if total_label else {})}


def series(sports):
    t = {"depauw_wins": 0, "wabash_wins": 0, "ties": 0, "games_played": 0}
    for s in sports.values():
        t["depauw_wins"] += s["record"]["depauw_wins"]
        t["wabash_wins"] += s["record"]["wabash_wins"]
        t["ties"] += s["record"]["ties"]
        t["games_played"] += s["games_played"]
    return {"schema_version": 1, "updated_at": "2026-10-28T03:48:10+00:00",
            "perspective": "DePauw", "totals": t, "sports": sports}


def football():
    return sport("football", "Football", [
        game("1999-11-06", "T", 7, 7, "away", "Crawfordsville, Ind."),
        game("2001-11-10", "L", 17, 24, "away", "Crawfordsville, Ind."),
        game("2002-11-09", "W", 30, 27, "home", note="2 OT",
             box="https://depauwtigers.com/boxscore.aspx?id=1&path=football"),
        game("2003-11-08", "W", 41, 20),
        game("2004-11-13", "W", 28, 3, "neutral", "Indianapolis, Ind."),
    ])


def lacrosse():
    return sport("mens-lacrosse", "Men's Lacrosse",
                 [game("2024-04-06", "W", 12, 8), game("2025-04-05", "W", 14, 5)])


def basketball():
    return sport("mens-basketball", "Men's Basketball", [game("2025-02-01", "L", 60, 71)])


def write(tmp_path, data):
    p = tmp_path / "series.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def build(tmp_path, sports, **kw):
    out = tmp_path / "site"
    render.build(write(tmp_path, series(sports)), out, **kw)
    return out, (out / "index.html").read_text(encoding="utf-8")


def text_of(page):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


def test_totals_and_counts_come_from_the_data(tmp_path):
    _, page = build(tmp_path, {"football": football(), "mens-lacrosse": lacrosse()})
    t = text_of(page)
    # football 3-1-1, lacrosse 2-0 => 5 DePauw wins, 1 Wabash, 1 tie, 7 games, 2 sports
    assert "DePauw 5 wins Wabash 1 win Ties 1 tie" in t
    assert "7 games across 2 sports" in t
    assert "DePauw 5 wins, Wabash 1, 1 tie, across 7 games in 2 sports" in page


def test_card_facts(tmp_path):
    _, page = build(tmp_path, {"football": football(), "mens-lacrosse": lacrosse()})
    t = text_of(page)
    assert "Records since 1999" in t and "Records since 2024" in t
    assert "DePauw has won 3 straight" in t
    assert "DePauw 123, Wabash 81" in t  # football points
    assert "Nov 13, 2004 · DePauw 28, Wabash 3" in t
    assert 'href="https://depauwtigers.com/sports/football/opponent-history/wabash/215"' in page


def test_every_game_is_listed_newest_first_with_ties_ots_and_boxscores(tmp_path):
    _, page = build(tmp_path, {"football": football()})
    t = text_of(page)
    for expected in ("DePauw 28, Wabash 3", "DePauw 41, Wabash 20", "DePauw 30, Wabash 27",
                     "Wabash 24, DePauw 17", "Tie 7–7", "2 OT", "Neutral · Indianapolis, Ind.",
                     "Away · Crawfordsville, Ind."):
        assert expected in t
    assert page.count('<tr><th scope="row"') == 5
    assert page.index("2004-11-13") < page.index("2003-11-08") < page.index("1999-11-06")
    assert page.count(">Box score</a>") == 1
    assert '<details class="games">' in page and "<details open" not in page


@pytest.mark.parametrize("code,words", [
    ("W4", "DePauw has won 4 straight"), ("W1", "DePauw won the last meeting"),
    ("L3", "Wabash has won 3 straight"), ("L1", "Wabash won the last meeting"),
    ("T1", "The last meeting ended in a tie"), ("T2", "The last 2 meetings ended in ties"),
])
def test_streak_wording(code, words):
    assert render.streak_text(code) == words


def test_scraped_text_is_escaped_and_unsafe_links_dropped(tmp_path):
    evil = football()
    evil["name"] = "<script>alert(1)</script>"
    evil["games"][2]["note"] = '"><img src=x onerror=alert(1)>'
    evil["games"][2]["site"] = "<b>Bold</b> & co"
    evil["games"][2]["boxscore"] = "javascript:alert(1)"
    evil["games"][3]["boxscore"] = "http://depauwtigers.com/insecure"
    evil["games"][4]["boxscore"] = "https://evil.example/depauwtigers.com"
    _, page = build(tmp_path, {"football": evil})
    soup = BeautifulSoup(page, "html.parser")
    scripts = soup.find_all("script")
    assert len(scripts) == 1 and scripts[0].get("src") == "easter-eggs.js" and scripts[0].has_attr("defer")
    assert set(scripts[0].attrs) == {"src", "defer"}
    assert scripts[0].string is None
    assert [image.get("src") for image in soup.find_all("img")] == [
        "easter-bell.svg", "easter-paw.svg", "easter-tiger.svg"]
    assert not soup.find_all(["iframe", "object", "embed", "foreignobject"])
    assert soup.find("b") is None and soup.find("img", src="x") is None
    assert all(not attr.lower().startswith("on") and attr.lower() != "style"
               for element in soup.find_all(True) for attr in element.attrs)
    assert "<b>Bold</b>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "&lt;b&gt;Bold&lt;/b&gt; &amp; co" in page
    assert "javascript:" not in page and "evil.example" not in page and "http://" not in page
    assert page.count(">Box score</a>") == 0


def test_a_third_sport_appears_with_no_template_changes(tmp_path):
    _, page = build(tmp_path, {"football": football(), "mens-basketball": basketball(),
                               "mens-lacrosse": lacrosse()})
    t = text_of(page)
    assert 'id="mens-basketball"' in page and "Men’s Basketball" in t
    assert "Wabash won the last meeting" in t
    assert "8 games across 3 sports" in t
    assert 'href="https://depauwtigers.com/sports/mens-basketball/opponent-history/wabash/215"' in page


def test_no_inline_script_or_style_and_security_meta(tmp_path):
    out, page = build(tmp_path, {"football": football()})
    for name in ("index.html", "404.html"):
        p = (out / name).read_text(encoding="utf-8")
        soup = BeautifulSoup(p, "html.parser")
        scripts = soup.find_all("script")
        if name == "index.html":
            assert len(scripts) == 1 and scripts[0].get("src") == "easter-eggs.js" and scripts[0].has_attr("defer")
            assert set(scripts[0].attrs) == {"src", "defer"}
            assert scripts[0].string is None
        else:
            assert scripts == []
        csp = soup.find("meta", attrs={"http-equiv": "Content-Security-Policy"})["content"]
        assert csp == (render.CSP + "; script-src 'self'" if name == "index.html" else render.CSP)
        assert all(not attr.lower().startswith("on") and attr.lower() != "style"
                   for element in soup.find_all(True) for attr in element.attrs)
        assert "default-src &#x27;none&#x27;" in p and "base-uri &#x27;none&#x27;" in p
        assert 'name="referrer" content="strict-origin-when-cross-origin"' in p
    assert 'href="styles.css"' in page and 'href="favicon.svg"' in page
    assert 'rel="canonical" href="https://depauwvswabash.com/"' in page
    # relative URLs only for assets on the main page
    assert not re.search(r'(?:src|href)="/(?!/)', page)


def test_404_uses_root_relative_assets_under_the_subpath(tmp_path):
    out, _ = build(tmp_path, {"football": football()})
    p = (out / "404.html").read_text(encoding="utf-8")
    assert 'href="/styles.css"' in p
    assert 'content="noindex"' in p


def test_extra_files_and_footer(tmp_path):
    out, page = build(tmp_path, {"football": football()})
    for name in ("styles.css", "favicon.svg", "robots.txt", "sitemap.xml", ".nojekyll", "404.html"):
        assert (out / name).exists()
    assert "Sitemap: https://depauwvswabash.com/sitemap.xml" in (
        out / "robots.txt").read_text()
    t = text_of(page)
    assert "Data last changed Oct 27, 11:48 PM (Eastern time)." in t
    assert "Last checked" not in t  # no recorded check, so no claim about one
    assert "Wabash is an all-male college" in t
    assert "Unofficial fan site, not affiliated with DePauw University or Wabash College." in t
    assert "depauwtigers.com" in t


@pytest.mark.parametrize("utc,expected", [
    ("2026-03-08T06:59:00+00:00", "EST"),   # just before 2 AM EST on DST start day
    ("2026-03-08T07:00:00+00:00", "EDT"),
    ("2026-11-01T05:59:00+00:00", "EDT"),   # 1:59 AM EDT
    ("2026-11-01T06:00:00+00:00", "EST"),
    ("2026-01-15T12:00:00+00:00", "EST"),
    ("2026-07-04T12:00:00+00:00", "EDT"),
])
def test_eastern_time_boundaries(utc, expected):
    assert render.to_eastern(dt.datetime.fromisoformat(utc))[1] == expected


NOW = dt.datetime(2026, 10, 28, 16, 0, tzinfo=dt.timezone.utc)


def test_eastern_formatting():
    assert render.fmt_stamp("2026-01-15T17:05:00+00:00", NOW) == "Jan 15, 12:05 PM"
    assert render.fmt_stamp("2026-07-04T04:00:00+00:00", NOW) == "Jul 4, 12:00 AM"
    assert render.fmt_stamp("2025-12-31T23:30:00+00:00", NOW) == "Dec 31, 2025, 6:30 PM"  # other year shows it


def test_last_checked_sits_next_to_last_changed_in_eastern_time(tmp_path):
    _, page = build(tmp_path, {"football": football()}, checked_at="2026-10-28T10:19:44+00:00", now=NOW)
    assert "Data last changed Oct 27, 11:48 PM · Last checked Oct 28, 6:19 AM (Eastern time)." in text_of(page)
    assert '<time datetime="2026-10-28T10:19:44+00:00">Oct 28, 6:19 AM</time>' in page


def test_checked_time_is_read_from_the_file_and_ignores_junk(tmp_path):
    f = tmp_path / ".checked-at"
    assert render.read_checked(f) is None            # missing: every sport failed, or a PR check
    f.write_text("not a time\n")
    assert render.read_checked(f) is None
    f.write_text("2026-10-28T10:19:44")              # no zone: can't be placed, so not trusted
    assert render.read_checked(f) is None
    f.write_text("2026-10-28T10:19:44+00:00\n")
    assert render.read_checked(f) == "2026-10-28T10:19:44+00:00"
    assert render.read_checked(None) is None


def test_main_picks_up_the_checked_file(tmp_path):
    f = tmp_path / "checked"
    f.write_text("2026-10-28T10:19:44+00:00\n")
    out = tmp_path / "site"
    assert render.main(["--data", str(write(tmp_path, series({"football": football()}))), "--out", str(out),
                        "--checked-file", str(f)]) == 0
    assert "Last checked" in text_of((out / "index.html").read_text())


# ---- loud failures ------------------------------------------------------- #

def test_missing_file_fails(tmp_path):
    with pytest.raises(render.DataError):
        render.build(tmp_path / "nope.json", tmp_path / "site")


def test_empty_sports_fails_and_exit_code_is_nonzero(tmp_path):
    data = series({"football": football()})
    data["sports"] = {}
    p = write(tmp_path, data)
    with pytest.raises(render.DataError):
        render.build(p, tmp_path / "site")
    assert render.main(["--data", str(p), "--out", str(tmp_path / "site")]) == 1
    assert not (tmp_path / "site").exists()


def test_sport_without_games_fails(tmp_path):
    s = football()
    s["games"], s["games_played"] = [], 0
    with pytest.raises(render.DataError):
        render.build(write(tmp_path, series({"football": s})), tmp_path / "site")


def test_inconsistent_totals_or_record_fail(tmp_path):
    data = series({"football": football()})
    data["totals"]["depauw_wins"] += 1
    with pytest.raises(render.DataError):
        render.build(write(tmp_path, data), tmp_path / "site")
    data = series({"football": football()})
    data["sports"]["football"]["record"]["ties"] = 0
    with pytest.raises(render.DataError):
        render.build(write(tmp_path, data), tmp_path / "site")


def test_missing_updated_at_fails(tmp_path):
    data = series({"football": football()})
    data["updated_at"] = None
    with pytest.raises(render.DataError):
        render.build(write(tmp_path, data), tmp_path / "site")


def test_bad_json_and_unsafe_key_fail(tmp_path):
    p = tmp_path / "series.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(render.DataError):
        render.build(p, tmp_path / "site")
    data = series({"football": football()})
    data["sports"]['"><x'] = data["sports"].pop("football")
    with pytest.raises(render.DataError):
        render.build(write(tmp_path, data), tmp_path / "site")


def test_real_data_file_renders(tmp_path):
    real = Path(__file__).resolve().parent.parent / "data" / "series.json"
    render.build(real, tmp_path / "site")
    assert "<article" in (tmp_path / "site" / "index.html").read_text(encoding="utf-8")


# ---- more sports -------------------------------------------------------- #

def test_total_label_comes_from_each_sport(tmp_path):
    base = sport("baseball", "Baseball", [game("2025-04-01", "W", 7, 2)], total_label="runs")
    tennis = sport("mens-tennis", "Men's Tennis", [game("2025-04-02", "W", 5, 4)], total_label="individual matches")
    _, page = build(tmp_path, {"baseball": base, "mens-tennis": tennis, "football": football()})
    t = text_of(page)
    assert "Total runs DePauw 7, Wabash 2" in t
    assert "Total individual matches DePauw 5, Wabash 4" in t
    assert "Total points DePauw 123, Wabash 81" in t  # football has no label in the sample: falls back to points
    assert t.count("Total runs") == 1


def test_missing_site_is_filled_from_home_or_away_but_not_neutral(tmp_path):
    s = sport("baseball", "Baseball", [
        game("2020-04-01", "W", 5, 1, "home", None),
        game("2020-04-02", "L", 1, 5, "away", None),
        game("2020-04-03", "W", 3, 2, "neutral", None),
        game("2020-04-04", "W", 4, 2, "home", "Westfield, IN"),
    ])
    _, page = build(tmp_path, {"baseball": s})
    t = text_of(page)
    assert "Home · Greencastle, Ind." in t and "Away · Crawfordsville, Ind." in t
    assert "Home · Westfield, Ind." in t  # a site the source gives is never replaced (only its state is restyled)
    neutral_cell = re.search(r'<td data-label="Site">(Neutral[^<]*)</td>', page).group(1)
    assert neutral_cell == "Neutral"
    # the data file itself is untouched
    assert s["games"][0]["site"] is None


def test_overtime_notes_are_tidied(tmp_path):
    s = sport("mens-basketball", "Men's Basketball", [
        game("1990-01-01", "W", 70, 68, note="(1ot)"), game("1991-01-01", "L", 60, 62, note="2OT"),
        game("1992-01-01", "W", 5, 4, note="(7 inn.)")])
    _, page = build(tmp_path, {"mens-basketball": s})
    cells = re.findall(r'<td data-label="Notes">([^<]*)</td>', page)
    assert sorted(cells) == ["1 OT", "2 OT", "7 inn."]


def test_not_included_note_sits_under_the_totals(tmp_path):
    _, page = build(tmp_path, {"football": football()})
    t = text_of(page)
    assert ("Not included: cross country, track and field, and golf, which are multi-team events rather than "
            "head-to-head games. Swimming and diving counts dual meets only; invitationals and championships "
            "are left out.") in t
    assert page.index("overall-meta") < page.index("not-included") < page.index('class="sport"')


def test_sport_cards_follow_the_data_order(tmp_path):
    order = ["mens-lacrosse", "football", "mens-basketball"]
    sports = {"football": football(), "mens-basketball": basketball(), "mens-lacrosse": lacrosse()}
    _, page = build(tmp_path, {k: sports[k] for k in order})
    assert [m for m in re.findall(r'<article class="sport" id="([^"]+)"', page)] == order


def test_sitemap_lastmod_follows_updated_at(tmp_path):
    out, _ = build(tmp_path, {"football": football()})
    assert "<lastmod>2026-10-27</lastmod>" in (out / "sitemap.xml").read_text()  # 11:48 PM Eastern on Oct 27


def test_sitemap_lastmod_moves_to_the_last_check_when_there_is_one(tmp_path):
    out, _ = build(tmp_path, {"football": football()}, checked_at="2026-10-29T10:19:44+00:00")
    assert "<lastmod>2026-10-29</lastmod>" in (out / "sitemap.xml").read_text()


def test_tiles_always_share_one_row():
    css = (Path(__file__).resolve().parent.parent / "static" / "styles.css").read_text()
    tiles = re.search(r"\.tiles \{[^}]*\}", css).group(0)
    assert "grid-auto-flow: column" in tiles and "auto-fit" not in tiles


def test_full_school_names_are_in_the_tab_title_and_subtitle_but_the_headline_stays_short(tmp_path):
    out, page = build(tmp_path, {"football": football()})
    assert "<title>DePauw University vs. Wabash College: All-Time Head-to-Head Records</title>" in page
    assert 'class="subtitle">DePauw University vs. Wabash College: all-time' in page
    assert 'id="entrance-fallback">DePauw vs. Wabash</span>' in page
    assert json.loads((out / "site.webmanifest").read_text())["name"] == "DePauw vs. Wabash"


def test_copyright_line_shows_one_year_in_2026_and_a_range_later():
    utc = dt.timezone.utc
    assert render.copyright_line(dt.datetime(2026, 10, 28, 12, tzinfo=utc)) == "© 2026 Jackson Mote"
    assert render.copyright_line(dt.datetime(2027, 3, 1, 12, tzinfo=utc)) == "© 2026–2027 Jackson Mote"
    assert render.copyright_line(dt.datetime(2031, 3, 1, 12, tzinfo=utc)) == "© 2026–2031 Jackson Mote"
    # New Year's Eve in Eastern time is already January 1 in UTC: the Eastern year counts, as elsewhere on the site
    assert render.copyright_line(dt.datetime(2027, 1, 1, 3, tzinfo=utc)) == "© 2026 Jackson Mote"


@pytest.mark.parametrize("now,line", [
    (dt.datetime(2026, 10, 28, 12, tzinfo=dt.timezone.utc), "© 2026 Jackson Mote"),
    (dt.datetime(2028, 6, 1, 12, tzinfo=dt.timezone.utc), "© 2026–2028 Jackson Mote"),
])
def test_every_page_ends_its_footer_with_exactly_one_copyright_line(tmp_path, now, line):
    out, _ = build(tmp_path, {"football": football()}, now=now)
    for name in ("index.html", "404.html"):
        page = (out / name).read_text(encoding="utf-8")
        assert page.count("©") == 1 and page.count(line) == 1, name
        footer = page[page.index("<footer"):page.index("</footer>")]
        assert re.findall(r"<p[^>]*>.*?</p>", footer, re.S)[-1] == f'<p class="copyright">{line}</p>', name
        assert footer.index("Unofficial fan site") < footer.index("©"), name
        assert "Mote Technology" not in page
