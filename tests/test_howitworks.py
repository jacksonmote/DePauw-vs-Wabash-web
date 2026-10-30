"""The /how-it-works/athletics/ page: plain-language, every number from the data, same shell as the home page."""

import datetime as dt
import html
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
import gameday  # noqa: E402
import howitworks  # noqa: E402
import merge  # noqa: E402
import render  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NOW = dt.datetime(2026, 12, 1, 15, 0, tzinfo=dt.timezone.utc)
DATA = json.loads((ROOT / "data" / "series.json").read_text(encoding="utf-8"))


def text_of(html):
    import html as h
    return re.sub(r"\s+", " ", h.unescape(re.sub(r"<[^>]+>", " ", html)))


def build(tmp_path, data=None, overrides=None):
    src = tmp_path / "series.json"
    src.write_text(json.dumps(data or DATA), encoding="utf-8")
    out = tmp_path / "site"
    render.build(src, out, now=NOW, upcoming_path=tmp_path / "none.json",
                 overrides_path=overrides or ROOT / "data" / "overrides.json")
    return out


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    return build(tmp_path_factory.mktemp("how"))


def how(site):
    return (site / "how-it-works" / "athletics" / "index.html").read_text(encoding="utf-8")


def test_the_page_is_written_to_its_own_folder_next_to_the_landing_page_and_the_alumni_page(site):
    for name in ("index.html", "athletics/index.html", "alumni/index.html"):
        assert (site / "how-it-works" / name).exists()


def test_every_number_comes_from_the_data(site):
    t = text_of(how(site))
    src = {k: sum(s["sources"][k] for s in DATA["sports"].values())
           for k in ("both", "depauw_only", "wabash_only", "disputed", "date_differs", "dates_not_recorded")}
    rules = merge.load_overrides(ROOT / "data" / "overrides.json")
    n_rules = sum(len(r["same_game"]) + len(r["different_games"]) + len(r["session_of"]) for r in rules.values())
    assert f"Disputed games right now: {src['disputed']}" in t
    assert f"Rulings made so far: {n_rules}" in t
    assert f"Games with no recorded date right now: {src['dates_not_recorded']}" in t
    assert f"both schools list {src['both']:,} games" in t
    assert f"{DATA['totals']['games_played']:,} games so far" in t


def test_the_swimming_section_explains_dual_meets_and_takes_its_numbers_from_the_data(site):
    t = text_of(how(site))
    swim = DATA["sports"]["mens-swimming-and-diving"]
    half = sum(1 for g in swim["games"] if any(isinstance(g.get(f), float) for f in ("depauw", "wabash")))
    unscored = sum(1 for g in swim["games"] if g.get("score_known") is False)
    assert "Only dual meets, where DePauw’s team swims Wabash’s team head to head, are counted." in t
    assert "Invitationals, relays, and conference championships have many teams in the pool at once" in t
    assert f"meets with a half point right now: {half}" in t and half > 0
    assert f"(right now: {unscored})" in t and unscored > 0
    assert f"rows set aside right now: {swim['sources']['session_rows']}" in t
    assert "A score written the wrong way around" in t and "in March 2021" in t
    assert "tennis counts matches and swimming and diving counts meets" in t
    assert "days of one meet" in t


def test_changing_the_data_changes_the_page(tmp_path):
    data = json.loads(json.dumps(DATA))
    data["sports"]["football"]["sources"]["disputed"] += 40
    data["sports"]["baseball"]["sources"]["wabash_only"] += 7
    t = text_of(how(build(tmp_path, data)))
    total = sum(s["sources"]["disputed"] for s in data["sports"].values())
    wabash_only = sum(s["sources"]["wabash_only"] for s in data["sports"].values())
    assert f"Disputed games right now: {total}" in t and f"{wabash_only:,} Wabash only" in t


def test_the_merge_constants_are_read_from_merge_py(tmp_path, monkeypatch):
    monkeypatch.setattr(merge, "MAX_DATE_GAP_DAYS", 7)
    monkeypatch.setattr(merge, "PLACEHOLDER_DAYS", {(2, 5), (2, 6)})
    t = text_of(how(build(tmp_path)))
    assert "up to 7 days apart" in t and "Feb 5 to Feb 6" in t


def test_the_update_cadence_follows_the_game_day_schedule(site):
    t = text_of(how(site))
    assert "twice a day" in t and f"every {howitworks.frequent_minutes()} minutes" in t
    assert howitworks.frequent_minutes() == 30
    workflow = (ROOT / ".github" / "workflows" / "update-and-deploy.yml").read_text(encoding="utf-8")
    crons = re.findall(r'- cron: "([^"]+)"', workflow)
    assert len(crons) == 3 and crons.count(gameday.FREQUENT_CRON) == 1      # two daily runs plus the frequent one


def test_it_covers_every_topic_the_fans_need(site):
    t = text_of(how(site))
    for word in ("Where the data comes from", "What is counted", "Doubleheaders", "date not recorded",
                 "Disputed games", "DePauw’s version counts", "Rulings", "Last checked", "Data last changed",
                 "GitHub can start a scheduled run late", "Report a data error", "held", "old version",
                 "all-male college", "multi-team events"):
        assert word in t or word.lower() in t.lower(), word


def test_it_links_to_both_schools_pages_the_rulings_and_the_readme(site):
    html = how(site)
    assert html.count("depauwtigers.com/sports/") >= 6 and html.count("sports.wabash.edu/sports/") >= 6
    assert f'href="{render.REPO_URL}/blob/main/data/overrides.json"' in html
    assert f'href="{render.REPO_URL}#readme"' in html


def test_it_has_the_same_shell_as_the_home_page(site):
    page, home = how(site), (site / "index.html").read_text(encoding="utf-8")
    csp = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', page).group(1)
    assert html.unescape(csp) == render.CSP
    home_csp = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', home).group(1)
    assert html.unescape(home_csp) == render.CSP + "; script-src 'self'"
    assert '<link rel="canonical" href="https://depauwvswabash.com/how-it-works/athletics/">' in page
    for prop in ("og:title", "og:description", "og:url", "og:image", "og:image:alt", "twitter:card"):
        assert f'"{prop}"' in page, prop
    assert re.search(r'og:image" content="https://depauwvswabash.com/og-image.png\?v=\w+"', page)
    assert 'href="../../styles.css"' in page and 'href="../../favicon.svg"' in page
    assert 'color-scheme" content="light dark"' in page
    assert 'class="site-header"' in page and 'class="site-footer"' in page
    assert "style=" not in page and "<script" not in page and "<style" not in page
    assert page.count("<h1>") == 1


def test_the_page_uses_only_relative_urls_for_the_site_itself(site):
    page = how(site)
    assert re.findall(r'(?:href|src)="(/[^"]*)"', page) == []          # nothing root-relative: only 404.html does that
    assert 'href="../../"' in page and 'href="../../data/series.json"' in page and 'href="../../games.csv"' in page


def test_the_sitemap_lists_it_and_the_404_page_links_to_it(site):
    sitemap = (site / "sitemap.xml").read_text(encoding="utf-8")
    for path in ("how-it-works/", "how-it-works/athletics/", "how-it-works/alumni/"):
        assert f"<loc>https://depauwvswabash.com/{path}</loc>" in sitemap
    nf = (site / "404.html").read_text(encoding="utf-8")
    assert 'href="/how-it-works/"' in nf          # root-relative: GitHub Pages serves 404.html at any depth


def test_the_page_is_valid_html_markup_balanced(site):
    page = how(site)
    for tag in ("section", "ul", "main", "header", "footer", "p", "li"):
        assert len(re.findall(rf"<{tag}[ >]", page)) == len(re.findall(rf"</{tag}>", page)), tag
