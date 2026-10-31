"""The page shell every page shares: one header helper with a skip link, 44 px nav links, the web font and the CSP
that allows it, the footer's source list inside one disclosure, and the design tokens' contrast.

Everything here is built from a fixed `now` and the committed curated files; no test reads the clock or the network."""

import datetime as dt
import re
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
import render  # noqa: E402

NOW = dt.datetime(2026, 10, 30, 12, tzinfo=dt.timezone.utc)
PAGES = {   # page -> prefix from the page to the site root
    "index.html": "", "alumni/index.html": "../", "how-it-works/index.html": "../",
    "how-it-works/athletics/index.html": "../../", "how-it-works/alumni/index.html": "../../",
    "404.html": "/", "bell-heist/index.html": "../", "heist-details/index.html": "../",
}
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("shell") / "site"
    render.build(ROOT / "data" / "series.json", out, now=NOW, upcoming_path=out / "missing.json")
    return out


def soup(site, name):
    return BeautifulSoup((site / name).read_text(encoding="utf-8"), "html.parser")


# --- CSP and the web font ---------------------------------------------------------------------------------------

def test_the_csp_allows_same_origin_fonts_and_nothing_from_anywhere_else():
    assert render.CSP == ("default-src 'none'; style-src 'self'; img-src 'self'; font-src 'self'; manifest-src 'self'; "
                          "base-uri 'none'; form-action 'none'; object-src 'none'")
    assert "http" not in render.CSP and "data:" not in render.CSP


@pytest.mark.parametrize("name", PAGES)
def test_every_page_preloads_the_one_font_and_the_stylesheet_names_the_same_file(site, name):
    page = soup(site, name)
    preloads = [l for l in page.find_all("link") if "preload" in (l.get("rel") or [])]
    assert len(preloads) == 1
    link = preloads[0]
    assert (link["as"], link["type"], link.has_attr("crossorigin")) == ("font", "font/woff2", True)
    prefix = PAGES[name]
    assert link["href"] == prefix + render.WEB_FONT
    served = site / render.WEB_FONT
    assert served.read_bytes()[:4] == b"wOF2"
    assert render.WEB_FONT in CSS


def test_the_font_is_one_weight_with_swap_and_published_beside_its_license(site):
    faces = re.findall(r"@font-face\s*\{([^}]*)\}", CSS)
    assert len(faces) == 1
    assert "font-display: swap" in faces[0] and "font-weight: 700" in faces[0]
    assert "format(\"woff2\")" in faces[0] and "http" not in faces[0]
    license_text = (site / render.FONT_LICENSE).read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE Version 1.1" in license_text and "Barlow Project Authors" in license_text
    assert (site / render.FONT_LICENSE).parent == (site / render.WEB_FONT).parent       # the license sits beside the font
    # The OFL declares no Reserved Font Name for Barlow, so the subset keeps its name; if one is ever added this fails.
    assert "Reserved Font Name" not in license_text.split("PREAMBLE", 1)[0]


def test_the_font_files_are_no_larger_than_the_approved_budget():
    assert (ROOT / "static" / render.WEB_FONT).stat().st_size <= 16384
    assert sorted(p.name for p in (ROOT / "static" / "fonts").glob("*.woff2")) == ["BarlowCondensed-Bold-latin.woff2"]


def test_the_system_font_stays_the_body_font_and_ui_rounded_is_gone():
    assert re.search(r"--font-body:\s*system-ui", CSS)
    assert "ui-rounded" not in CSS
    assert re.search(r"--font-display:\s*\"Barlow Condensed\"", CSS)
    assert re.search(r"body \{[^}]*font:\s*1rem/1\.5 var\(--font-body\)", CSS)


# --- skip link and the header -----------------------------------------------------------------------------------

@pytest.mark.parametrize("name", PAGES)
def test_the_skip_link_is_first_in_the_body_and_lands_on_the_one_main(site, name):
    page = soup(site, name)
    first = next(c for c in page.body.children if getattr(c, "name", None))
    assert first.name == "a" and first["href"] == "#main" and first.get_text() == "Skip to content"
    assert "skip-link" in first["class"]
    mains = page.find_all("main")
    assert len(mains) == 1 and mains[0]["id"] == "main"
    assert len(page.find_all(id="main")) == 1
    assert first.find_next_sibling().name == "header"           # skip link, then the header, then (later) main


@pytest.mark.parametrize("name", PAGES)
def test_every_header_comes_from_one_helper_with_title_nav_and_subtitle_in_order(site, name):
    header = soup(site, name).find("header", class_="site-header")
    wrap = header.find("div", class_="wrap")
    tags = [c.name for c in wrap.children if getattr(c, "name", None)]
    assert tags[0] == "h1" and tags[-2:] == ["nav", "p"]
    assert wrap.find("nav")["aria-label"] == "Main navigation"
    links = wrap.find("nav").find_all("a")
    assert [a.get_text() for a in links] == ["Athletics", "Alumni"]
    assert wrap.find("p", class_="subtitle") is wrap.find_all("p")[-1]


def test_the_alumni_nav_marks_the_alumni_page_current_and_links_back_to_athletics(site):
    nav = soup(site, "alumni/index.html").find("nav", class_="site-nav")
    athletics, alumni = nav.find_all("a")
    assert athletics["href"] == "../" and "aria-current" not in athletics.attrs
    assert alumni["aria-current"] == "page"


def test_nav_links_are_at_least_44_px_tall():
    assert re.search(r"--tap:\s*44px", CSS)
    rule = re.search(r"\.site-nav a \{([^}]*)\}", CSS).group(1)
    assert re.search(r"min-height:\s*var\(--tap\)", rule) and "inline-flex" in rule
    assert re.search(r"\.skip-link \{[^}]*min-height:\s*var\(--tap\)", CSS)
    assert re.search(r"\.skip-link:focus-visible \{[^}]*top:\s*var\(--space-2\)", CSS)     # it appears on focus


# --- the footer's source list -------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["index.html", "how-it-works/athletics/index.html"])
def test_every_athletics_source_link_is_inside_one_data_sources_disclosure(site, name):
    page = soup(site, name)
    details = page.select("footer details.sources-details")
    assert len(details) == 1
    d = details[0]
    assert d.find("summary").get_text() == "Data sources"
    assert d.find("ul", class_="sources") is not None
    assert page.select("footer ul.sources") == d.select("ul.sources")             # no list left outside it
    data = render.load(ROOT / "data" / "series.json")
    for sport in data["sports"].values():
        for key in ("source_url", "wabash_source_url"):
            if sport.get(key):
                assert d.find("a", href=render.safe_url(sport[key]))


def test_the_alumni_footer_source_list_is_in_the_same_kind_of_disclosure(site):
    page = soup(site, "alumni/index.html")
    d = page.select_one("footer details.sources-details")
    assert d and d.find("summary").get_text() == "Data sources" and d.find("ul", class_="sources")
    assert len(d.find_all("a")) == 4                                      # two statements and two College Navigator pages


# --- anchors and structure that must survive ------------------------------------------------------------------------

HOME_IDS = {"entrance-fallback", "tiger-entrance", "entrance-hint", "entrance-status",
            "overall-h", "monon-bell", "monon-bell-h", "main"}


def test_every_id_the_home_page_had_is_still_there(site):
    ids = {e["id"] for e in soup(site, "index.html").find_all(id=True)}
    sports = set(render.load(ROOT / "data" / "series.json")["sports"])
    expected = HOME_IDS | sports | {k + "-h" for k in sports}
    assert expected <= ids
    assert len(ids) == len([e for e in soup(site, "index.html").find_all(id=True)])            # all unique


def test_the_alumni_and_heist_anchors_are_all_still_there(site):
    alumni = {e["id"] for e in soup(site, "alumni/index.html").find_all(id=True)}
    assert {"alumni-h", "endowment", "endowment-h", "nobel", "senators", "academy-awards-h", "main"} <= alumni
    heist = {e["id"] for e in soup(site, "heist-details/index.html").find_all(id=True)}
    assert {"tally", "timeline-h", "guarded-now", "heist-1967-the-burial", "heist-2017-masked-attempt-h", "main"} <= heist


def test_no_page_has_an_inline_style_or_script_after_the_shell_change(site):
    for name in PAGES:
        page = soup(site, name)
        assert not page.find_all("style")
        assert all(not el.has_attr("style") for el in page.find_all(True))


# --- design tokens ----------------------------------------------------------------------------------------------------

def _tokens():
    light_css, dark_css = CSS.split("@media (prefers-color-scheme: dark)", 1)
    dark_css = dark_css.split("\n}", 1)[0]
    pick = lambda text: dict(re.findall(r"(--color-[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", text))
    light = pick(light_css)
    return light, {**light, **pick(dark_css)}


def _lum(hex_):
    rgb = [int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _ratio(a, b):
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


TEXT_ON_PAGE_AND_SURFACE = ["text", "text-muted", "link", "depauw-text", "wabash-text"]
TEXT_ON_BOARD = ["board-text", "board-muted", "board-depauw", "board-wabash", "board-tie"]
FILLS = ["depauw-accent", "wabash-accent", "tie-accent"]


@pytest.mark.parametrize("mode", [0, 1], ids=["light", "dark"])
def test_text_roles_meet_aa_and_fills_meet_3_to_1_in_both_modes(mode):
    t = _tokens()[mode]
    for role in TEXT_ON_PAGE_AND_SURFACE:
        for ground in ("page", "surface"):
            assert _ratio(t[f"--color-{role}"], t[f"--color-{ground}"]) >= 4.5, (role, ground)
    for role in TEXT_ON_BOARD:
        assert _ratio(t[f"--color-{role}"], t["--color-board"]) >= 4.5, role
    assert _ratio(t["--color-header-text"], t["--color-header"]) >= 4.5
    assert _ratio(t["--color-header-muted"], t["--color-header"]) >= 4.5
    assert _ratio(t["--color-banner-text"], t["--color-banner-bg"]) >= 4.5
    for role in FILLS:
        for ground in ("page", "surface"):
            assert _ratio(t[f"--color-{role}"], t[f"--color-{ground}"]) >= 3, (role, ground)
    # the gold and scarlet marks on the dark board (the record bar's strip)
    assert _ratio(t["--color-depauw-accent"], t["--color-board"]) >= 3
    assert _ratio(t["--color-bar-wabash"], t["--color-board"]) >= 3
    assert _ratio(t["--color-board-tie-fill"], t["--color-board"]) >= 3
    assert _ratio(t["--color-board-depauw"], t["--color-board"]) >= 3


def test_dark_mode_scarlet_text_is_red_not_pink():
    import colorsys
    t = _tokens()[1]
    for role in ("wabash-text", "board-wabash"):
        r, g, b = (int(t[f"--color-{role}"][i:i + 2], 16) / 255 for i in (1, 3, 5))
        hue, lightness, sat = colorsys.rgb_to_hls(r, g, b)
        assert hue * 360 >= 345 or hue * 360 <= 5, role                 # a red hue
        assert lightness <= 0.66, role                                  # pink is light: #ff8d9b was 78%
