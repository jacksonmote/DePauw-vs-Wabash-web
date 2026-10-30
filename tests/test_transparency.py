"""Tests for the data downloads and the report-an-error flow."""
import csv
import io
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import render  # noqa: E402
from test_render import NOW, build, football, lacrosse, basketball, series, write  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SOURCES = json.loads((ROOT / "scraper" / "sources.json").read_text(encoding="utf-8"))


def test_the_series_file_is_published_unchanged(tmp_path):
    data = series({"football": football()})
    src = write(tmp_path, data)
    out = tmp_path / "site"
    render.build(src, out, now=NOW, upcoming_path=tmp_path / "no-upcoming.json")
    assert (out / "data" / "series.json").read_bytes() == src.read_bytes()


def test_games_csv_has_one_row_per_game_with_the_documented_columns(tmp_path):
    out, _ = build(tmp_path, {"football": football(), "mens-lacrosse": lacrosse(), "mens-basketball": basketball()})
    rows = list(csv.DictReader(io.StringIO((out / "games.csv").read_text(encoding="utf-8"))))
    assert list(rows[0]) == ["sport", "date", "season", "location", "site", "depauw_score", "wabash_score",
                             "result", "note", "boxscore_url", "source", "date_recorded", "disputed",
                             "date_differs", "wabash_record_date", "depauw_record_score", "wabash_record_score"]
    assert len(rows) == 5 + 2 + len(basketball()["games"])
    ot = next(r for r in rows if r["date"] == "2002-11-09")
    assert ot == {"sport": "Football", "date": "2002-11-09", "season": "2002", "location": "home",
                  "site": "Greencastle, Ind.", "depauw_score": "30", "wabash_score": "27",
                  "result": "DePauw win", "note": "2 OT",
                  "boxscore_url": "https://depauwtigers.com/boxscore.aspx?id=1&path=football",
                  "source": "depauw", "date_recorded": "yes", "disputed": "no", "date_differs": "no",
                  "wabash_record_date": "", "depauw_record_score": "", "wabash_record_score": ""}
    assert {r["result"] for r in rows} <= {"DePauw win", "Wabash win", "Tie"}
    tie = next(r for r in rows if r["date"] == "1999-11-06")
    assert (tie["result"], tie["depauw_score"], tie["wabash_score"]) == ("Tie", "7", "7")


def test_the_real_data_makes_a_csv_that_adds_up_to_the_totals(tmp_path):
    real = ROOT / "data" / "series.json"
    out = tmp_path / "site"
    render.build(real, out, now=NOW, upcoming_path=tmp_path / "no-upcoming.json")
    rows = list(csv.DictReader(io.StringIO((out / "games.csv").read_text(encoding="utf-8"))))
    totals = json.loads(real.read_text(encoding="utf-8"))["totals"]
    assert len(rows) == totals["games_played"]
    assert sum(r["result"] == "DePauw win" for r in rows) == totals["depauw_wins"]
    assert sum(r["result"] == "Wabash win" for r in rows) == totals["wabash_wins"]
    assert sum(r["result"] == "Tie" for r in rows) == totals["ties"]


def test_text_that_would_run_as_a_spreadsheet_formula_is_defused(tmp_path):
    evil = football()
    evil["games"][0]["note"] = "=HYPERLINK(\"http://x\")"
    evil["games"][0]["site"] = "@SUM(1)"
    out, _ = build(tmp_path, {"football": evil})
    first = list(csv.DictReader(io.StringIO((out / "games.csv").read_text(encoding="utf-8"))))[0]
    assert first["note"].startswith("'=") and first["site"].startswith("'@")


def test_footer_links_both_downloads_and_the_error_report(tmp_path):
    _, html = build(tmp_path, {"football": football()})
    footer = html[html.index('<footer'):]
    assert 'Download the data' in footer
    assert '<a href="data/series.json">series.json</a>' in footer and '<a href="games.csv">games.csv</a>' in footer
    assert "Report a data error</a>" in footer
    assert "template=data-correction.yml" in footer


def test_each_card_reports_with_its_own_sport_prefilled(tmp_path):
    _, html = build(tmp_path, {"football": football(), "mens-basketball": basketball()})
    card = html[html.index('id="mens-basketball"'):]
    link = re.search(r'<a href="([^"]+)">Report a data error</a>', card).group(1).replace("&amp;", "&")
    url = urlparse(link)
    assert (url.scheme, url.netloc, url.path) == ("https", "github.com", "/jacksonmote/DePauw-vs-Wabash-web/issues/new")
    q = parse_qs(url.query)
    assert q["template"] == ["data-correction.yml"] and q["sport"] == ["Men’s Basketball"]
    assert q["title"] == ["Data correction: Men’s Basketball"]
    assert html.count("Report a data error</a>") == 3   # two cards and the footer


def test_the_footer_report_link_does_not_prefill_a_sport():
    assert "sport=" not in render.report_url() and "sport=Football" in render.report_url("Football")


# ---- the issue form ---------------------------------------------------------- #

def form():
    return yaml.safe_load((ROOT / ".github" / "ISSUE_TEMPLATE" / "data-correction.yml").read_text(encoding="utf-8"))


def test_the_issue_form_asks_for_sport_date_school_problem_and_source_only():
    body = [b for b in form()["body"] if b["type"] != "markdown"]
    assert [b["id"] for b in body] == ["sport", "game_date", "which_record", "what_is_wrong", "source_link"]
    assert body[0]["type"] == "input"   # the card links prefill it by id
    assert [b["validations"]["required"] for b in body] == [True, False, False, True, True]


def test_the_issue_form_asks_which_schools_record_looks_wrong():
    which = next(b for b in form()["body"] if b.get("id") == "which_record")
    assert which["type"] == "dropdown"
    assert which["attributes"]["options"] == ["DePauw’s record", "Wabash’s record", "Both", "Not sure"]


def test_the_issue_form_asks_for_nothing_personal_and_says_it_is_public():
    text = json.dumps(form()).lower()
    assert "public" in text and "personal" in text
    for ask in ("email address:", "phone number:", "your name", "home address:"):
        assert ask not in text
    assert not any(b["type"] == "checkboxes" for b in form()["body"])
    assert [b["id"] for b in form()["body"] if b["type"] == "dropdown"] == ["which_record"]


def test_blank_issues_are_turned_off():
    cfg = yaml.safe_load((ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml").read_text(encoding="utf-8"))
    assert cfg["blank_issues_enabled"] is False


def test_the_report_link_targets_the_template_that_exists():
    assert (ROOT / ".github" / "ISSUE_TEMPLATE" / "data-correction.yml").exists()
    assert "template=data-correction.yml" in render.report_url()
    assert render.REPO_URL == "https://github.com/jacksonmote/DePauw-vs-Wabash-web"
