"""The footer is one function with a page-specific data block, so every page (home, the three how-it-works
pages, alumni, 404) carries the same structure, styles and wording after it."""

import datetime as dt
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
import alumni  # noqa: E402
import render  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NOW = dt.datetime(2026, 12, 1, 15, 0, tzinfo=dt.timezone.utc)
MEN_ONLY = ("Only men’s head-to-head competition is compared: Wabash is an all-male college, and multi-team "
            "events (cross country, track and field, golf, and swimming invitationals and championships) "
            "aren’t head-to-head.")
SITE_SUGGEST = f" · {render.enhancement_link('Whole site')}"        # the neutral pages carry it in the shared line
ATHLETICS_ONLY = ("Data last changed", "series.json", "games.csv", "Report a data error", "depauwtigers.com",
                  "sports.wabash.edu", MEN_ONLY, "Last checked")


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("footer") / "site"
    render.build(ROOT / "data" / "series.json", out, now=NOW, upcoming_path=out / "none.json")
    return out


def footer(site, name):
    html = (site / name).read_text(encoding="utf-8")
    return html[html.index('<footer class="site-footer">'):]


# page -> (prefix to the site root, the "How the numbers work" page it links to, kind of data block)
PAGES = {
    "index.html": ("", "how-it-works/athletics/", "athletics"),
    "how-it-works/athletics/index.html": ("../../", "how-it-works/athletics/", "athletics"),
    "alumni/index.html": ("../", "how-it-works/alumni/", "alumni"),
    "how-it-works/alumni/index.html": ("../../", "how-it-works/alumni/", "alumni"),
    "how-it-works/index.html": ("../", None, "neutral"),          # the landing page does not link to itself
    "404.html": ("/", "how-it-works/", "neutral"),
    "bell-heist/index.html": ("../", "how-it-works/", "neutral"),
}


def shared_tail(f):
    """Everything from the 'How the numbers work' line on: the part every page has in common."""
    return f[f.index('<p class="footer-links">'):]


@pytest.mark.parametrize("name", PAGES)
def test_every_page_has_the_shared_lines_in_order_and_links_to_its_own_how_page(site, name):
    prefix, how, kind = PAGES[name]
    f = footer(site, name)
    assert f.count("How the numbers work") == (0 if how is None else 1) and f.count('class="footer-links"') == 1
    if how:
        assert f'<a href="{prefix}{how}">How the numbers work</a>' in f
    assert f'<a href="{render.REPO_URL}">Built in the open: code and data on GitHub</a>' in f
    assert f.index("Built in the open") < f.index(render.DISCLAIMER) < f.index("A note on impartiality") < f.index("© 2026")
    assert f.count("A note on impartiality") == f.count("The data is independently sourced. The developers were educated correctly.") == 1
    assert f.count("<!-- Both schools supplied the records. Only one supplied the developers. -->") == 1
    if how:
        assert f.index("<!-- Both schools supplied") < f.index("How the numbers work") < f.index("Built in the open")
    assert 'class="copyright"' in f[-200:]                       # the copyright is still the last line
    assert f.count(render.DISCLAIMER) == 1
    assert "Code and data on GitHub" not in f.replace("Built in the open: code and data on GitHub", "")


@pytest.mark.parametrize("name", PAGES)
def test_the_mens_note_shows_on_the_athletics_pages_only(site, name):
    f = footer(site, name)
    assert (MEN_ONLY in f) == (PAGES[name][2] == "athletics")
    assert "Only men’s sports are compared because" not in f


@pytest.mark.parametrize("name", [n for n, p in PAGES.items() if p[2] == "athletics"])
def test_the_athletics_footer_keeps_its_data_block_in_front_of_the_shared_lines(site, name):
    prefix = PAGES[name][0]
    f = footer(site, name)
    assert "Data last changed" in f and "Last checked" not in f          # no live check in a fixture build
    assert f'href="{prefix}data/series.json"' in f and f'href="{prefix}games.csv"' in f
    assert render.report_url() in f.replace("&amp;", "&")
    assert ('Data from <a href="https://depauwtigers.com/">depauwtigers.com</a> and '
            '<a href="https://sports.wabash.edu/">sports.wabash.edu</a>. Source pages:') in f
    assert f.index('class="sources"') < f.index("How the numbers work") < f.index(MEN_ONLY)
    assert "alumni.json" not in f and "Records last verified" not in f


@pytest.mark.parametrize("name", [n for n, p in PAGES.items() if p[2] == "alumni"])
def test_the_alumni_footer_has_the_alumni_data_block_and_nothing_athletics_only(site, name):
    prefix = PAGES[name][0]
    f = footer(site, name)
    endowment = alumni.load_json(ROOT / "data" / "endowment.json", "endowment")
    assert "Records last verified <time datetime=\"2026-10-29\">Oct 29, 2026</time>." in f
    assert f'<a href="{prefix}data/alumni.json">alumni.json</a>' in f
    assert f'<a href="{prefix}data/endowment.json">endowment.json</a>' in f
    assert f'<a href="{alumni.correction_url()}">Suggest a correction</a>' in f
    assert ("Data from: DePauw and Wabash audited FY2025 financial statements, the U.S. Department of Education’s "
            "College Navigator (IPEDS), and the sources listed with each name.") in f
    for item in endowment["schools"].values():
        assert f'<a href="{item["source_url"]}">FY2025 audited financial statements</a>' in f
        assert f'<a href="{item["enrollment"]["source_url"]}">College Navigator (IPEDS)</a>' in f
    assert f.index("Records last verified") < f.index('class="sources"') < f.index("How the numbers work")
    for athletics_only in ATHLETICS_ONLY:
        assert athletics_only not in f, athletics_only
    assert "Alumni and endowment records use manually reviewed sources" not in f


@pytest.mark.parametrize("name", [n for n, p in PAGES.items() if p[2] == "neutral"])
def test_the_neutral_footer_is_only_the_shared_lines(site, name):
    f = footer(site, name)
    how_link = "" if PAGES[name][1] is None else f'<a href="{PAGES[name][0]}how-it-works/">How the numbers work</a> · '
    assert f == ('<footer class="site-footer">\n<div class="wrap">\n<!-- Both schools supplied the records. Only one supplied the developers. -->\n<p class="footer-links">' + how_link +
                 f'<a href="{render.REPO_URL}">Built in the open: code and data on GitHub</a>{SITE_SUGGEST}</p>\n'
                 f'<p>{render.DISCLAIMER}</p>\n<details class="impartiality"><summary>A note on impartiality</summary><p>The data is independently sourced. The developers were educated correctly.</p></details>\n<p class="copyright">© 2026 Jackson Mote and Joe Haynes</p>\n'
                 '</div>\n</footer>\n</body>\n</html>\n')
    for athletics_only in ATHLETICS_ONLY:
        assert athletics_only not in f
    assert "alumni.json" not in f and "Records last verified" not in f and 'class="sources"' not in f


def test_the_records_verified_date_is_the_newest_of_the_two_curated_files_in_the_athletics_date_style():
    data = alumni.load_json(ROOT / "data" / "alumni.json", "alumni")
    endowment = alumni.load_json(ROOT / "data" / "endowment.json", "endowment")
    data["verified_at"], endowment["verified_at"] = "2026-11-02", "2026-11-09"
    block = "\n".join(render.alumni_footer_block("../", data, endowment))
    assert 'datetime="2026-11-09">Nov 9, 2026</time>' in block
    assert render.alumni_verified_date(data, endowment) == "2026-11-09"


@pytest.mark.parametrize("name", PAGES)
def test_the_shared_lines_are_the_same_on_every_page_apart_from_the_how_link_and_the_mens_note(site, name):
    prefix, how, kind = PAGES[name]
    tail = shared_tail(footer(site, name))
    if how:
        tail = tail.replace(f'href="{prefix}{how}"', 'href="HOW"')
    tail = tail.replace(f"<p>{MEN_ONLY}</p>\n", "")
    tail = tail.replace('<details class="impartiality"><summary>A note on impartiality</summary><p>The data is independently sourced. The developers were educated correctly.</p></details>\n', "IMPARTIALITY\n")
    link = '<a href="HOW">How the numbers work</a> · ' if how else ""
    suggest = SITE_SUGGEST if kind == "neutral" else ""
    assert tail == ('<p class="footer-links">' + link +
                    f'<a href="{render.REPO_URL}">Built in the open: code and data on GitHub</a>{suggest}</p>\n'
                    f'<p>{render.DISCLAIMER}</p>\nIMPARTIALITY\n<p class="copyright">© 2026 Jackson Mote and Joe Haynes</p>\n'
                    '</div>\n</footer>\n</body>\n</html>\n')


AREAS = {"athletics": "Athletics", "alumni": "Alumni and endowment", "neutral": "Whole site"}


@pytest.mark.parametrize("name", PAGES)
def test_every_footer_has_one_enhancement_link_prefilled_for_its_page_type(site, name):
    f = footer(site, name)
    kind = PAGES[name][2]
    assert f.count("Suggest an enhancement</a>") == 1
    link = re.search(r'<a href="([^"]+)">Suggest an enhancement</a>', f).group(1)
    assert link == f"{render.REPO_URL}/issues/new?template=enhancement.yml&amp;area={AREAS[kind].replace(' ', '%20')}"
    assert (ROOT / ".github" / "ISSUE_TEMPLATE" / "enhancement.yml").exists()


@pytest.mark.parametrize("name", PAGES)
def test_the_enhancement_link_leaves_the_correction_links_as_they_were(site, name):
    f = footer(site, name)
    kind = PAGES[name][2]
    assert f.count("Report a data error</a>") == (1 if kind == "athletics" else 0)
    assert f.count("Suggest a correction</a>") == (1 if kind == "alumni" else 0)
    if kind == "athletics":
        assert f'<a href="{render.report_url()}">Report a data error</a>' in f
    if kind == "alumni":
        assert f'<a href="{alumni.correction_url()}">Suggest a correction</a>' in f


@pytest.mark.parametrize("name", [n for n, p in PAGES.items() if p[2] != "neutral"])
def test_the_feedback_links_sit_on_their_own_line_between_the_downloads_and_the_data_from_line(site, name):
    f = footer(site, name)
    para = re.search(r'<p class="feedback">(.*?)</p>', f).group(1)
    first = "Report a data error" if PAGES[name][2] == "athletics" else "Suggest a correction"
    assert re.sub(r"<[^>]+>", "", para) == f"{first}. Suggest an enhancement."
    assert f.index("Download the data") < f.index('<p class="feedback">') < f.index("Data from")
    assert first not in f[f.index("Download the data"):f.index('<p class="feedback">')]


@pytest.mark.parametrize("name", ["index.html", "alumni/index.html"])
def test_the_enhancement_link_is_not_on_the_sport_or_category_cards(site, name):
    page = (site / name).read_text(encoding="utf-8")
    assert page.count("Suggest an enhancement</a>") == 1             # the footer's only


def test_the_not_included_note_under_the_totals_is_unchanged(site):
    html = (site / "index.html").read_text(encoding="utf-8")
    assert render.NOT_INCLUDED in html and "<strong>Not included:</strong>" in html


def test_the_totals_heading_is_renamed_and_keeps_its_anchor(site):
    html = (site / "index.html").read_text(encoding="utf-8")
    assert '<h2 id="overall-h">All head-to-head sports</h2>' in html
    assert 'aria-labelledby="overall-h"' in html                  # the id old links and the section label rely on
    assert "All sports combined" not in html
