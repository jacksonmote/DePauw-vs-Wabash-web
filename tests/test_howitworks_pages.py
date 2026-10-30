"""The three how-it-works pages (landing, athletics, alumni) and the links between every page of the site."""

import datetime as dt
import html as htmllib
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scraper"))
import alumni  # noqa: E402
import howitworks  # noqa: E402
import render  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NOW = dt.datetime(2026, 12, 1, 15, 0, tzinfo=dt.timezone.utc)
SITE_URL = "https://depauwvswabash.com/"


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("pages") / "site"
    render.build(ROOT / "data" / "series.json", out, now=NOW, upcoming_path=out / "none.json")
    return out


def read(site, name):
    return (site / name).read_text(encoding="utf-8")


def text_of(page):
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", page)))


def data():
    return alumni.load_json(ROOT / "data" / "alumni.json", "alumni"), alumni.load_json(ROOT / "data" / "endowment.json", "endowment")


LANDING, ATHLETICS, ALUMNI = "how-it-works/index.html", "how-it-works/athletics/index.html", "how-it-works/alumni/index.html"


def test_the_landing_page_keeps_the_old_address_with_one_line_and_a_link_for_each_page(site):
    page = read(site, LANDING)
    assert 'href="athletics/">How the athletics numbers work</a>' in page
    assert 'href="alumni/">How the alumni numbers work</a>' in page
    assert page.count("<h3>") == 2 and page.count("<h1>") == 1
    assert "<script" not in page and "http-equiv=\"refresh\"" not in page.lower() and "location" not in page.lower().replace("location=", "")
    assert len(text_of(page[page.index("<main"):page.index("</main>")]).split()) < 90        # short
    assert '<link rel="canonical" href="https://depauwvswabash.com/how-it-works/">' in page


@pytest.mark.parametrize("name, path, current", [
    (LANDING, "how-it-works/", None), (ATHLETICS, "how-it-works/athletics/", "Athletics"),
    (ALUMNI, "how-it-works/alumni/", "Alumni")])
def test_each_page_has_its_own_title_description_canonical_open_graph_and_nav(site, name, path, current):
    page = read(site, name)
    title = re.search(r"<title>(.*?)</title>", page).group(1)
    assert title.endswith("| DePauw vs. Wabash")
    description = re.search(r'name="description" content="([^"]+)"', page).group(1)
    assert description and description != re.search(r'name="description" content="([^"]+)"', read(site, "index.html")).group(1)
    assert f'<link rel="canonical" href="{SITE_URL}{path}">' in page
    assert f'property="og:url" content="{SITE_URL}{path}"' in page
    for prop in ("og:title", "og:description", "og:image", "og:image:width", "og:image:height", "og:image:alt"):
        assert f'"{prop}"' in page, prop
    assert 'name="twitter:card" content="summary_large_image"' in page
    marked = re.findall(r'<a href="[^"]*" aria-current="page">([^<]+)</a>', page)
    assert marked == ([current] if current else [])
    assert f"<loc>{SITE_URL}{path}</loc>" in read(site, "sitemap.xml")


def test_each_page_header_carries_its_halfs_subtitle(site):
    assert alumni.SUBTITLE in read(site, ALUMNI) and render.SUBTITLE not in read(site, ALUMNI)
    assert render.SUBTITLE in read(site, ATHLETICS) and alumni.SUBTITLE not in read(site, ATHLETICS)


def test_the_neutral_pages_share_a_neutral_subtitle(site):
    assert render.NEUTRAL_SUBTITLE == ("DePauw and Wabash, side by side: head-to-head athletics, notable graduates "
                                       "and endowments.")
    for name in (LANDING, "404.html"):
        page = read(site, name)
        assert f'<p class="subtitle">{render.NEUTRAL_SUBTITLE}</p>' in page
        assert render.SUBTITLE not in page and alumni.SUBTITLE not in page and "men’s sports" not in page


def test_the_landing_and_404_pages_link_back_to_both_halves(site):
    landing = read(site, LANDING)
    assert ('<p class="back"><a href="../">← Back to the head-to-head records</a> · '
            '<a href="../alumni/">← Back to the alumni and endowment page</a></p>') in landing
    page = read(site, "404.html")
    assert ('<p class="back"><a href="/">← Back to the head-to-head records</a> · '
            '<a href="/alumni/">← Back to the alumni and endowment page</a></p>') in page
    assert ATHLETICS and 'class="back"><a href="../../">← Back to the head-to-head records</a></p>' in read(site, ATHLETICS)
    assert 'class="back"><a href="../../alumni/">← Back to the alumni and endowment page</a></p>' in read(site, ALUMNI)


def test_titles_are_different_on_all_three_pages(site):
    titles = {re.search(r"<title>(.*?)</title>", read(site, n)).group(1) for n in (LANDING, ATHLETICS, ALUMNI)}
    assert len(titles) == 3


def test_athletics_pages_use_the_athletics_share_image_and_alumni_pages_the_alumni_one(site):
    for name in ("index.html", LANDING, ATHLETICS):
        assert re.search(r'og:image" content="https://depauwvswabash.com/og-image.png\?v=\w+"', read(site, name)), name
    for name in ("alumni/index.html", ALUMNI):
        assert re.search(r'og:image" content="https://depauwvswabash.com/alumni-og-image.png\?v=\w+"', read(site, name)), name
    assert "$516,000" in re.search(r'og:image:alt" content="([^"]+)"', read(site, ALUMNI)).group(1)


def test_the_athletics_how_page_is_the_old_page_with_new_links(site):
    page = read(site, ATHLETICS)
    assert howitworks.TITLE in htmllib.unescape(page)
    assert '<h2 id="how-h">How the athletics numbers work</h2>' in page
    assert 'href="../../">← Back to the head-to-head records</a>' in page
    assert "Disputed games right now:" in text_of(page) and "Rulings made so far:" in text_of(page)


def test_the_alumni_how_page_takes_every_number_from_the_data_files(site):
    page = text_of(read(site, ALUMNI))
    a, e = data()
    counts = alumni.counts(a)
    for c in alumni.visible_categories(a):
        assert f"{c['name']}. {c['definition']} DePauw {counts[c['id']]['depauw']}, Wabash {counts[c['id']]['wabash']}." in page
    people = {s: {p["id"] for p in a["people"] if p["school"] == s} for s in ("depauw", "wabash")}
    assert f"names {len(people['depauw'])} DePauw graduates and {len(people['wabash'])} Wabash graduates" in page
    d, w = e["schools"]["depauw"], e["schools"]["wabash"]
    excluded = d["comparable"]["excluded"]
    assert f"${d['amount_usd']:,}" in page and f"${w['amount_usd']:,}" in page
    for part in excluded:
        assert f"${part['amount_usd']:,} of {part['label']}" in page
    assert f"Taking those out leaves ${alumni.comparable_amount(d):,}." in page
    assert f"DePauw {d['enrollment']['count']:,}, Wabash {w['enrollment']['count']:,}" in page
    per = alumni.per_student(e)
    assert f"DePauw ${per['depauw']:,} and Wabash ${per['wabash']:,}" in page
    assert f"within {alumni.EVEN_PERCENT}% of the lower one" in page and "about 1.0% apart" in page
    assert "rounded to the nearest $1,000" in page and "no overall alumni score" in page.lower()
    assert "Oct 29, 2026" in page and "Jun 30, 2025" in page and "fall 2024" in page
    for item in e["schools"].values():
        assert f'href="{item["enrollment"]["source_url"]}"' in read(site, ALUMNI)


def test_the_alumni_how_page_carries_the_size_and_history_context_under_who_counts(site):
    page = read(site, ALUMNI)
    _, e = data()
    note = alumni.context_note(e)
    who = page[page.index("<h3>Who counts</h3>"):page.index("<h3>The categories</h3>")]
    assert htmllib.escape(note, quote=False) in who and "about twice as many" in note


def test_the_alumni_how_page_changes_when_the_data_changes(tmp_path):
    a, e = data()
    e["schools"]["depauw"]["enrollment"]["count"] = 1500
    e["schools"]["depauw"]["comparable"]["excluded"][0]["amount_usd"] = 40_000_000
    a["verified_at"] = e["verified_at"] = "2026-12-24"
    page = text_of("\n".join(howitworks.alumni_content(alumni.validate_alumni(a), alumni.validate_endowment(e),
                                                       repo_url=render.REPO_URL)))
    assert "$40,000,000 of pledges receivable" in page and "DePauw 1,500, Wabash 866" in page
    assert "Dec 24, 2026" in page and "which is more than 5%" in page and "marks the higher one" in page
    assert "about even" in page          # the rule itself is still explained


def test_the_alumni_how_page_covers_what_fans_need(site):
    page = text_of(read(site, ALUMNI))
    for topic in ("earned a degree", "once per school and category", "no overall alumni score", "How names were found",
                  "Curated by hand", "pull request", "does not update", "pooled endowment net assets",
                  "pledges receivable", "perpetual trusts", "College Navigator", "nearest $1,000", "about even",
                  "Suggest a correction", "correction form"):
        assert topic.lower() in page.lower() or topic.lower().replace("does not update", "none of it updates") in page.lower(), topic


def test_the_athletics_page_wording_has_no_alumni_text_and_the_alumni_page_no_athletics_only_footer_items(site):
    assert "pledges receivable" not in read(site, ATHLETICS)
    alumni_how = read(site, ALUMNI)
    footer = alumni_how[alumni_how.index('<footer class="site-footer">'):]
    assert "series.json" not in footer and "games.csv" not in footer and "Report a data error" not in footer


# ---------------------------------------------------------------------------------------------- every link

def internal_targets(site):
    """(page, href) for every site-internal link and image, from every html file of the built site."""
    for page in site.rglob("*.html"):
        text = page.read_text(encoding="utf-8")
        for attr in re.finditer(r'(?:href|src)="([^"]*)"', text):
            href = htmllib.unescape(attr.group(1))
            if href and not re.match(r"(https?:|mailto:|data:)", href):
                yield page, href, text


def resolve(site, page, href):
    path = urlparse(href).path
    base = site if path.startswith("/") else page.parent
    target = (base / unquote(path.lstrip("/"))).resolve() if path else page
    return target / "index.html" if target.is_dir() else target


def test_no_internal_link_points_to_a_page_that_does_not_exist(site):
    seen = 0
    for page, href, text in internal_targets(site):
        target = resolve(site, page, href)
        assert target.exists(), f"{page.relative_to(site)} links to {href}"
        fragment = urlparse(href).fragment
        if fragment:
            body = target.read_text(encoding="utf-8") if target.suffix == ".html" else ""
            assert f'id="{fragment}"' in body, f"{page.relative_to(site)} links to missing anchor {href}"
        seen += 1
    assert seen > 60


def test_nothing_links_to_the_old_single_how_it_works_page_content_anymore(site):
    """/how-it-works/ is only the landing page: the footers and the alumni and athletics pages go straight to the right page."""
    for name in ("index.html", "alumni/index.html", ATHLETICS, ALUMNI):
        page = read(site, name)
        assert not re.search(r'href="(?:\.\./)*how-it-works/"', page), name
    assert 'href="/how-it-works/"' in read(site, "404.html")      # neutral page: the landing page is the right place
    for readme_line in (ROOT / "README.md").read_text(encoding="utf-8").splitlines():
        assert "How the athletics numbers work" not in readme_line or "/how-it-works/athletics/" in readme_line


def test_html_validate_covers_every_page_in_checks_yml():
    yml = (ROOT / ".github" / "workflows" / "checks.yml").read_text(encoding="utf-8")
    for name in ("site/index.html", "site/404.html", "site/how-it-works/index.html", "site/how-it-works/athletics/index.html",
                 "site/how-it-works/alumni/index.html", "site/alumni/index.html"):
        assert name in yml, name


def test_the_landing_page_footer_does_not_link_to_itself_and_every_other_page_keeps_the_link(site):
    landing = read(site, LANDING)
    footer = landing[landing.index('<footer class="site-footer">'):]
    assert "How the numbers work" not in footer and "Built in the open" in footer
    notfound = read(site, "404.html")
    assert "How the numbers work" in notfound[notfound.index('<footer'):] and notfound.count("How the numbers work") == 1
    for name in ("index.html", "alumni/index.html", ATHLETICS, ALUMNI):
        page = read(site, name)
        assert "How the numbers work</a> · " in page[page.index('<footer class="site-footer">'):], name


def test_every_back_link_on_the_site_has_the_arrow_and_one_of_the_two_wordings(site):
    """Back links share one style: arrow first, then one of two fixed phrases."""
    seen = 0
    for page in site.rglob("*.html"):
        text = page.read_text(encoding="utf-8")
        for block in re.findall(r'<p class="back">(.*?)</p>', text):
            for label in re.findall(r'<a href="[^"]*">([^<]*)</a>', block):
                assert label in (render.BACK_ATHLETICS, render.BACK_ALUMNI), (page.name, label)
                assert label.startswith("← Back to the ")
                seen += 1
        assert not re.search(r">\s*Back to the ", text), page      # no back link without the arrow, wherever it sits
    assert seen == 8          # includes both standard links on the unindexed Bell-heist page


def test_each_how_page_heading_matches_its_title_and_the_landing_link(site):
    """The heading, the <title> and the landing-page link say the same thing: 'How the athletics numbers work' and
    'How the alumni numbers work'; the landing page itself is 'How the numbers work'."""
    landing = read(site, LANDING)
    for name, words, href in ((ATHLETICS, "athletics", "athletics/"), (ALUMNI, "alumni", "alumni/")):
        page = read(site, name)
        heading = f"How the {words} numbers work"
        assert f'<h2 id="how-h">{heading}</h2>' in page
        assert re.search(r"<title>(.*?)</title>", page).group(1).startswith(heading)
        assert f'<a href="{href}">{heading}</a>' in landing
    assert '<h2 id="how-h">How the numbers work</h2>' in landing


def test_the_404_page_has_no_body_link_to_how_the_numbers_work_only_the_footer_one(site):
    page = read(site, "404.html")
    main = page[page.index("<main"):page.index("</main>")]
    assert "How the numbers work" not in main and main.count("← Back to") == 2
    assert page.count('>How the numbers work</a>') == 1
