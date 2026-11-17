"""Accessibility rules that must keep holding (WCAG 2.2 level AA is the goal): contrast for every color token pair in
light and dark, a text alternative for everything that is not text, names that start with the visible text, keyboard
and reduced-motion support, and the page structure a screen reader relies on.

The contrast numbers come from the tokens in static/styles.css, the same ones the pages use, so a changed color is
checked without a browser. The pages are built from a fixed `now`; nothing here reads the clock or the network. The
browser side (axe-core on every rendered page) is the separate `axe` job in checks.yml, because tests never run node."""

import datetime as dt
import json
import re
import sys
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
import accessibility  # noqa: E402
import images  # noqa: E402
import privacy  # noqa: E402
import render  # noqa: E402

NOW = dt.datetime(2026, 10, 30, 12, tzinfo=dt.timezone.utc)
FROZEN = ROOT / "tests" / "fixtures" / "series-frozen-2026-10-30.json"
CSS = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
MODES = ("light", "dark")

# --- contrast ---------------------------------------------------------------------------------------------------
# Every --color-* token has exactly one entry here, so a new color cannot ship unchecked: the test below fails until
# it is listed with the surfaces it is drawn on.
# WCAG 1.4.3 (normal-size text) and 1.4.11 (fills, edges, focus rings): the numbers the accessibility page states
TEXT_MIN, NON_TEXT_MIN = accessibility.TEXT_MIN, accessibility.NON_TEXT_MIN
BODY_GROUNDS = ("page", "surface", "row-stripe")
TEXT_ON = {                                  # text color -> every background it is read on, at least 4.5:1
    "text": BODY_GROUNDS,
    "text-muted": BODY_GROUNDS,
    "link": BODY_GROUNDS,
    "depauw-text": BODY_GROUNDS,
    "wabash-text": BODY_GROUNDS,
    "header-text": ("header",),
    "header-muted": ("header",),
    "banner-text": ("banner-bg",),           # the game-day banner and the Bell-week box; their links inherit it
    "board-text": ("board",),
    "board-muted": ("board",),
    "board-depauw": ("board",),
    "board-wabash": ("board",),
    "board-tie": ("board",),
    "page": ("text",),                       # the skip link: page-colored words on a text-colored block
}
MARK_ON = {                                  # fills, bars, stripes and focus rings -> what they sit on, at least 3:1
    "focus": BODY_GROUNDS + ("header", "banner-bg"),
    "header-text": ("header",),              # the focus ring of the title button
    # row-stripe: the result edge of a game in a striped table row; banner-bg: the edge of the Bell-week box
    "depauw-accent": ("page", "surface", "row-stripe", "banner-bg", "board"),
    "wabash-accent": ("page", "surface", "row-stripe"),
    "tie-accent": ("page", "surface", "row-stripe"),
    "board-depauw": ("board",),
    "bar-wabash": ("board", "page"),
    "board-tie-fill": ("board",),
}
GROUNDS = {"page", "surface", "row-stripe", "header", "banner-bg", "board"}
# Never the only thing that marks a control or carries meaning: a hairline around cards, tiles and tables.
DECORATIVE = {"border"}


def tokens():
    """({token: hex}, {token: hex}) for light and dark; dark is the light values with the dark block's overrides."""
    light_css, dark_css = CSS.split("@media (prefers-color-scheme: dark)", 1)
    dark_css = dark_css.split("\n}", 1)[0]
    pick = lambda text: {name: value.lower() for name, value in
                         re.findall(r"--color-([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\b", text)}
    light = pick(light_css)
    return light, {**light, **pick(dark_css)}


def luminance(color) -> float:
    """WCAG relative luminance of '#rrggbb' or an (r, g, b) tuple."""
    rgb = [int(color[i:i + 2], 16) for i in (1, 3, 5)] if isinstance(color, str) else list(color)
    lin = [v / 255 / 12.92 if v / 255 <= 0.04045 else ((v / 255 + 0.055) / 1.055) ** 2.4 for v in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(a, b) -> float:
    la, lb = luminance(a), luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def test_the_thresholds_are_the_wcag_aa_ones():
    assert (TEXT_MIN, NON_TEXT_MIN) == (4.5, 3)


def test_the_contrast_formula_is_the_wcag_one():
    assert ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert ratio("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)      # the well-known near miss
    assert ratio((255, 255, 255), "#ffffff") == pytest.approx(1.0)


def test_every_color_token_is_classified_so_none_ships_unchecked():
    light, dark = tokens()
    assert set(dark) == set(light)                                            # dark mode only overrides, never adds
    listed = set(TEXT_ON) | set(MARK_ON) | GROUNDS | DECORATIVE
    assert set(light) == listed, sorted(set(light) ^ listed)
    used_grounds = {g for grounds in list(TEXT_ON.values()) + list(MARK_ON.values()) for g in grounds}
    assert used_grounds <= set(light)


def test_colors_are_only_ever_set_through_the_tokens():
    """A one-off color in a rule would escape the contrast check. Outside the two token blocks the stylesheet names
    no color at all: no hex value, no rgb() or hsl(), only var(--color-...) and the system keywords."""
    body = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    rules = re.sub(r"--color-[a-z0-9-]+:\s*#[0-9a-fA-F]{6}\s*;", "", body)
    assert re.findall(r"#[0-9a-fA-F]{3,8}\b", rules) == []
    assert re.findall(r"\b(?:rgba?|hsla?|oklch|color-mix)\(", rules) == []
    for name in re.findall(r"var\(--color-([a-z0-9-]+)\)", rules):
        assert name in tokens()[0], name                                      # no reference to a token that is gone


@pytest.mark.parametrize("mode", [0, 1], ids=MODES)
def test_every_text_and_background_pair_meets_4_5_to_1(mode):
    t = tokens()[mode]
    low = [(text, ground, round(ratio(t[text], t[ground]), 2))
           for text, grounds in TEXT_ON.items() for ground in grounds if ratio(t[text], t[ground]) < TEXT_MIN]
    assert low == []


@pytest.mark.parametrize("mode", [0, 1], ids=MODES)
def test_every_fill_edge_and_focus_ring_meets_3_to_1(mode):
    t = tokens()[mode]
    low = [(mark, ground, round(ratio(t[mark], t[ground]), 2))
           for mark, grounds in MARK_ON.items() for ground in grounds if ratio(t[mark], t[ground]) < NON_TEXT_MIN]
    assert low == []


def test_the_share_image_text_meets_4_5_to_1():
    """The two preview images are drawn in Python, outside the stylesheet, so their text colors are checked here:
    the title, the muted lines, each school's label and the dark words on the gold chip."""
    for name in ("TEXT", "MUTED", "GOLD", "SCARLET_TEXT", "TIE"):
        assert ratio(getattr(images, name), images.BG) >= TEXT_MIN, name
    assert ratio(images.BG, images.GOLD) >= TEXT_MIN


# --- the pages ----------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("accessibility") / "site"
    render.build(FROZEN, out, now=NOW, upcoming_path=out / "missing.json")
    return out


def pages(site):
    return sorted(p.relative_to(site).as_posix() for p in site.rglob("*.html"))


def soup(site, name):
    return BeautifulSoup((site / name).read_text(encoding="utf-8"), "html.parser")


PAGES = {   # every page of the site -> the prefix from it to the site root
    "index.html": "", "alumni/index.html": "../", "how-it-works/index.html": "../",
    "how-it-works/athletics/index.html": "../../", "how-it-works/alumni/index.html": "../../",
    "404.html": "/", "bell-heist/index.html": "../", "heist-details/index.html": "../",
    "privacy/index.html": "../", "accessibility/index.html": "../",
}
TARGET = "accessibility/index.html"


def test_every_page_is_checked(site):
    assert pages(site) == sorted(PAGES)             # a new page is covered by every rule below without being listed


def each_page(site):
    return [(name, soup(site, name)) for name in pages(site)]


def test_every_page_has_a_language_one_h1_one_main_and_a_skip_link_first(site):
    for name, page in each_page(site):
        assert page.html["lang"] == "en", name
        assert len(page.find_all("h1")) == 1, name
        mains = page.find_all("main")
        assert len(mains) == 1 and mains[0]["id"] == "main", name
        first = page.body.find("a")
        assert first["href"] == "#main" and "skip-link" in first["class"], name
        for landmark in ("header", "nav", "footer"):
            assert page.find(landmark) is not None, (name, landmark)
        assert page.title.get_text().strip(), name


def test_headings_never_skip_a_level(site):
    for name, page in each_page(site):
        levels = [int(h.name[1]) for h in page.find_all(re.compile(r"^h[1-6]$"))]
        assert levels[0] == 1, name
        for before, after in zip(levels, levels[1:]):
            assert after <= before + 1, (name, before, after)


def test_zoom_is_never_switched_off(site):
    for name, page in each_page(site):
        viewport = page.find("meta", attrs={"name": "viewport"})["content"]
        assert "user-scalable" not in viewport and "maximum-scale" not in viewport, name


def test_every_image_has_alt_text_or_is_marked_decorative(site):
    for name, page in each_page(site):
        for img in page.find_all("img"):
            assert img.has_attr("alt"), (name, img)
            link = img.find_parent("a")
            if img["alt"] == "" and link is not None and not link.get_text(strip=True):
                assert link.get("aria-label"), (name, link)                    # an image-only link is still named
        for svg in page.find_all("svg"):
            if svg.get("aria-hidden") == "true":
                continue
            assert svg.get("role") == "img" and svg.find("title") is not None, (name, svg.get("class"))
            for ref in svg["aria-labelledby"].split():
                assert page.find(id=ref).get_text(strip=True), (name, ref)
        image = page.find("meta", property="og:image")
        if image is not None:
            assert page.find("meta", property="og:image:alt")["content"].strip(), name


def test_every_chart_says_in_words_what_the_colors_and_positions_mean(site):
    """Gold and scarlet bars are not told apart by color alone (WCAG 1.4.1): the legend names each one's position,
    and the chart's own description carries the counts."""
    home = soup(site, "index.html")
    legends = home.select("ul.chart-legend")
    assert len(legends) == len(home.select("svg.chart")) == len(home.select("article.sport"))
    for legend in legends:
        assert [li.get_text() for li in legend.find_all("li")] == [
            "DePauw win (above the line)", "Wabash win (below the line)", "Tie (short mark on the line)"]
        assert all(sw.get("aria-hidden") == "true" for sw in legend.select(".sw"))
    for svg in home.select("svg.chart"):
        assert "DePauw" in svg.find("desc").get_text() and "Wabash" in svg.find("desc").get_text()


def test_an_accessible_name_always_starts_with_the_visible_text(site):
    """WCAG 2.5.3 Label in Name: someone who says what they see ("click Source 1") reaches the control. Any link,
    button or summary with both visible text and an aria-label keeps the visible text at the start of the label."""
    seen = 0
    for name, page in each_page(site):
        for el in page.find_all(["a", "button", "summary"]):
            text, label = " ".join(el.get_text(" ", strip=True).split()), el.get("aria-label")
            if label is None:
                assert text or el.find("img", alt=re.compile(r"\S")), (name, el)   # nothing is left without a name
                continue
            assert label.strip(), (name, el)
            if text:
                seen += 1
                assert label.lower().startswith(text.lower()), (name, text, label)
    assert seen > 50                                                          # the rule really ran on labelled links


def test_links_that_repeat_on_every_card_say_which_card_they_belong_to(site):
    home, alumni_page = soup(site, "index.html"), soup(site, "alumni/index.html")
    for card in home.select("article.sport"):
        sport = card.find("h2").get_text(strip=True).lower()
        links = card.select("p.source a")
        assert [a.get_text() for a in links][-1] == "Report a data error"
        assert [a["aria-label"] for a in links] == [f"{a.get_text()}: {sport}" for a in links]
    assert home.select_one("#mens-basketball p.source a")["aria-label"] == "DePauw history: men’s basketball"
    reports = alumni_page.select("p.alumni-report a")
    assert len(reports) == len(alumni_page.select("section.alumni-card")) + 1     # each category and the endowment
    for a in reports:
        heading = a.find_parent("section").find(re.compile(r"^h[23]$")).get_text(strip=True)
        assert a.get_text() == "Suggest a correction" and a["aria-label"].startswith("Suggest a correction: ")
        assert a["aria-label"].split(": ", 1)[1] in (heading, "Endowment")
    labels = [a["aria-label"] for a in reports]
    assert len(set(labels)) == len(labels)                                        # no two say the same thing
    # the 200-odd box-score links keep their plain text: the row's date header gives the context
    assert all(not a.has_attr("aria-label") for a in home.select("details.games td a"))


def test_every_table_has_a_caption_named_after_the_sport_and_scoped_headers(site):
    home = soup(site, "index.html")
    tables = [t for name, page in each_page(site) for t in page.find_all("table")]
    groups = home.select("details.games details")                                 # a table per decade of each game list
    assert len(tables) == len(groups) + 1 and len(groups) > len(home.select("article.sport"))   # and the scoreboard
    for table in tables:
        assert table.caption is not None and table.caption.get_text(strip=True)
        assert all(th.get("scope") in ("col", "row") for th in table.find_all("th"))
    captions = [t.caption.get_text() for t in home.select("details.games table")]
    assert len(set(captions)) == len(captions)                                    # no two tables share a name
    assert "Every men’s basketball game of the 2020s, newest first" in captions
    assert "Every men’s tennis match of the 2020s, newest first" in captions
    assert "Every baseball game with no recorded date, newest first" in captions
    assert not any(re.search(r"\bmens\b", c) for c in captions)                   # the name, never the key


def test_every_id_reference_points_at_an_element_on_the_same_page(site):
    for name, page in each_page(site):
        ids = [el["id"] for el in page.find_all(id=True)]
        assert len(ids) == len(set(ids)), (name, [i for i in ids if ids.count(i) > 1])
        for attr in ("aria-labelledby", "aria-describedby", "aria-controls"):
            for el in page.find_all(attrs={attr: True}):
                for ref in el[attr].split():
                    assert ref in ids, (name, attr, ref)


def test_the_title_button_hint_is_only_a_description_never_loose_text(site):
    """The hint is `hidden`, so a screen reader meets it only as the button's description (WCAG 1.3.1), and with
    JavaScript off, when there is no button, it is not read at all."""
    for name in ("index.html", "alumni/index.html"):
        page = soup(site, name)
        hint, button = page.find(id="entrance-hint"), page.find(id="tiger-entrance")
        assert hint.has_attr("hidden") and button.has_attr("hidden")
        assert button["aria-describedby"] == "entrance-hint" and "five times" in hint.get_text()
        status = page.find(id="entrance-status")
        assert (status["role"], status["aria-live"]) == ("status", "polite") and status.get_text() == ""


def test_nothing_takes_keyboard_focus_out_of_order_or_out_of_reach(site):
    for name, page in each_page(site):
        assert [el for el in page.find_all(tabindex=True) if el["tabindex"] not in ("0", "-1")] == [], name
        for el in page.find_all(attrs={"aria-hidden": "true"}):
            assert el.name not in ("a", "button", "summary") and not el.find(["a", "button", "summary"]), (name, el)
        for details in page.find_all("details"):
            assert details.find("summary", recursive=False) is not None, name     # native, keyboard-operable


def test_focus_is_always_visible():
    """Every focusable kind of element gets the 3px ring; no rule switches an outline off."""
    rules = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    assert re.search(r"a:focus-visible, summary:focus-visible, button:focus-visible \{ outline: 3px solid "
                     r"var\(--color-focus\); outline-offset: 2px; \}", rules)
    assert not re.search(r"outline:\s*(none|0)\b", rules)
    assert not re.search(r"outline-width:\s*0\b", rules)


def test_motion_stops_when_the_visitor_asks_for_less():
    """The one animation is cancelled under prefers-reduced-motion, and every transition is declared only inside a
    no-preference block, so nothing moves for someone who asked for reduced motion."""
    rules = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    animated = re.findall(r"([^{}]+)\{[^{}]*\banimation:\s*(?!none)[^;}]+", rules)
    reduce = "".join(re.findall(r"@media \(prefers-reduced-motion: reduce\) \{(.*?\})\s*\}", rules, flags=re.S))
    assert animated
    for selector in animated:
        assert re.search(re.escape(selector.strip()) + r"\s*\{\s*animation:\s*none", reduce), selector
    calm = re.sub(r"@media \(prefers-reduced-motion: no-preference\) \{.*?\}\s*\}", "", rules, flags=re.S)
    assert "transition" not in calm and "scroll-behavior" not in calm


def test_high_contrast_mode_keeps_the_scoreboard_names_readable():
    """Windows high contrast paints a backplate behind the big numbers; the school names are lifted above it."""
    blocks = "".join(re.findall(r"@media \(forced-colors: active\) \{(.*?\})\s*\}", CSS, flags=re.S))
    assert re.search(r"\.board-name \{ position: relative; z-index: 1; \}", blocks)


def test_the_tiger_facts_stay_usable_without_the_script(site):
    facts = soup(site, "index.html").select("li.tiger-fact")
    assert len([f for f in facts if not f.has_attr("hidden")]) == 1               # one is always readable
    assert len(facts) == len(json.loads((ROOT / "data" / "fun-facts.json").read_text(encoding="utf-8"))["facts"])


def test_the_axe_script_checks_every_tiger_fact_and_every_wcag_level_it_says_it_does():
    """Opening the tiger's disclosure makes the page's own script hide every fact but one, in a toggle event that
    fires a moment later. axe/run.js waits for the toggle events, then shows every fact, then runs axe, and reports
    any fact that was hidden when axe finished, so the facts cannot drop out of the check unnoticed."""
    script = (ROOT / "axe" / "run.js").read_text(encoding="utf-8")
    assert (script.index('addEventListener("toggle"') < script.index("d.open = true")
            < script.index("await Promise.race([Promise.all(toggled),") < script.index("f.hidden = false")
            < script.index("await window.axe.run("))
    assert script.count("f.hidden = false") == 1
    assert "hiddenFacts: facts.filter((f) => f.hidden).length" in script
    assert "if (result.hiddenFacts) {\n              problems.push(" in script.replace("\r\n", "\n")
    # three states on one load, in this order: as loaded, each game list open with its decades as shipped, all open
    states = re.search(r"const STATES = \[([^\]]+)\]", script).group(1)
    assert re.findall(r'"([^"]+)"', states) == ["as it loads", "game lists open", "everything open"]
    assert "for (const state of STATES) {" in script and 'querySelectorAll("details.games")' in script
    assert "list.open = true" in script and "if (result.stillClosed) problems.push(" in script
    assert ('stillClosed: state === "everything open" ? document.querySelectorAll("details:not([open])").length : 0'
            in script)
    tags = re.search(r"const TAGS = \[([^\]]+)\]", script).group(1)
    assert re.findall(r'"([^"]+)"', tags) == ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"]


# --- the accessibility page ---------------------------------------------------------------------------------------

def page_text(site):
    return soup(site, TARGET).get_text(" ", strip=True)


def test_the_accessibility_page_renders_like_the_privacy_page(site):
    s = soup(site, TARGET)
    assert s.title.string == "Accessibility | DePauw vs. Wabash"
    assert [h.get_text() for h in s.find_all("h2")] == ["Accessibility"]
    assert [h.get_text() for h in s.find_all("h3")] == [
        "Our goal", "What we did", "What we haven’t tested", "What works", "Known limitations",
        "Content we don’t control", "Report a problem", "Changes"]
    assert s.find("link", rel="canonical")["href"] == "https://depauwvswabash.com/accessibility/"
    assert s.find("meta", attrs={"name": "description"})["content"] == accessibility.DESCRIPTION
    assert len(accessibility.DESCRIPTION) <= 155
    assert s.find("meta", property="og:title")["content"] == accessibility.TITLE
    assert s.find("meta", property="og:description")["content"] == accessibility.DESCRIPTION
    assert s.find("meta", property="og:url")["content"] == "https://depauwvswabash.com/accessibility/"
    assert s.find("meta", property="og:image")["content"].startswith("https://depauwvswabash.com/og-image.png")
    assert not s.find("meta", attrs={"name": "robots"})                                # indexed, like the privacy page
    assert not any(a.has_attr("aria-current") for a in s.find("nav").find_all("a"))     # no nav item marked current
    # the same shell as the privacy page: skip link, shared header, the two back links, one section in <main>
    privacy_page = soup(site, "privacy/index.html")
    assert str(s.find("header")) == str(privacy_page.find("header"))
    assert str(s.find("p", class_="back")) == str(privacy_page.find("p", class_="back"))
    assert [c.name for c in s.find("main").find_all(True, recursive=False)] == ["p", "section"]
    assert s.find("section")["aria-labelledby"] == "accessibility-h"


def test_the_page_has_the_base_csp_no_script_and_no_inline_style(site):
    source = (site / TARGET).read_text(encoding="utf-8")
    csp = re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', source).group(1)
    assert csp.replace("&#x27;", "'") == render.CSP
    s = soup(site, TARGET)
    assert s.find_all("script") == [] and s.find_all("style") == [] and s.find_all(style=True) == []
    assert s.find_all("img") == []


def test_the_last_updated_date_comes_from_the_constant_not_the_clock(tmp_path):
    for now in (dt.datetime(2026, 11, 20, tzinfo=dt.timezone.utc), dt.datetime(2027, 6, 1, tzinfo=dt.timezone.utc)):
        out = tmp_path / now.date().isoformat()
        render.build(FROZEN, out, now=now, upcoming_path=out / "none.json")
        source = (out / TARGET).read_text(encoding="utf-8")
        shown = privacy.updated_text(accessibility.UPDATED)
        assert f'<p class="updated">Last updated <time datetime="{accessibility.UPDATED}">{shown}</time></p>' in source
        entry = re.search(r"<loc>https://depauwvswabash.com/accessibility/</loc><lastmod>([^<]+)</lastmod>",
                          (out / "sitemap.xml").read_text(encoding="utf-8"))
        assert entry.group(1) == accessibility.UPDATED
    assert re.fullmatch(r"\d{4}-\d\d-\d\d", accessibility.UPDATED)
    assert accessibility.REVIEWED <= accessibility.UPDATED          # the page cannot predate the review it describes


def test_the_page_is_in_the_sitemap_right_after_the_privacy_page(site):
    locs = re.findall(r"<loc>([^<]+)</loc>", (site / "sitemap.xml").read_text(encoding="utf-8"))
    assert locs[-2:] == ["https://depauwvswabash.com/privacy/", "https://depauwvswabash.com/accessibility/"]
    assert len(locs) == len(PAGES) - 2                              # every page but the 404 and the Bell-heist joke


@pytest.mark.parametrize("name", PAGES)
def test_every_footer_links_to_the_page_right_after_privacy_and_the_page_does_not_link_to_itself(site, name):
    prefix = PAGES[name]
    links = soup(site, name).find("footer", class_="site-footer").find("p", class_="footer-links")
    items = [(el.name, el.get_text(), el.get("href"), el.get("aria-current")) for el in links.find_all(["a", "span"])]
    labels = [text for _, text, _, _ in items]
    assert labels[-3:] == ["Privacy", "Accessibility", "Built in the open: code and data on GitHub"]
    assert labels[:-3] in ([], ["How the numbers work"])
    assert " · ".join(labels) == links.get_text()                   # the same separator between every item
    item = items[-2]
    if name == TARGET:
        assert item == ("span", "Accessibility", None, "page")      # plain text on its own page, like Privacy
        hrefs = [a["href"] for a in soup(site, name).find_all("a", href=True)]
        assert not any(h.rstrip("/").endswith("accessibility") for h in hrefs)
    else:
        assert item == ("a", "Accessibility", prefix + accessibility.PATH, None)
        target = site / item[2].lstrip("/") if prefix == "/" else (site / name).parent / item[2]
        assert (target / "index.html").resolve() == (site / TARGET).resolve()


def test_the_page_says_aims_to_meet_and_never_claims_conformance_or_certification(site):
    text = page_text(site)
    assert "This site aims to meet the Web Content Accessibility Guidelines (WCAG) 2.2 at level AA." in text
    assert "nobody outside the project has audited the site, and it holds no certification" in text
    lowered = text.lower()
    for claim in ("fully accessible", "fully conform", "is compliant", "conforms to", "wcag compliant",
                  "certified", "guarantee", "ada compliant", "section 508"):
        assert claim not in lowered, claim
    assert "unofficial fan site, not affiliated with DePauw University or Wabash College." in text
    goal = soup(site, TARGET).find("a", string=re.compile("Web Content Accessibility Guidelines"))
    assert goal["href"] == accessibility.WCAG_URL


def test_the_page_says_plainly_what_was_not_tested_and_what_is_left(site):
    text = page_text(site)
    assert ("We haven’t tested with a real screen reader (NVDA, JAWS, VoiceOver, or TalkBack), in Safari, or on "
            "physical phones.") in text
    assert "Automated tools catch only some problems" in text
    limits = soup(site, TARGET).find("h3", string="Known limitations").find_next_sibling("ul")
    assert [li.find("strong").get_text() for li in limits.find_all("li")] == [
        "Easter eggs.", "The scoreboard with wider text spacing.", "Game lists on phones.", "Charts."]
    assert "none is needed to use the site" in limits.get_text()    # the easter eggs are optional
    assert "Tiger entrance" in limits.get_text()                    # the button really is called that
    assert soup(site, "index.html").find(id="tiger-entrance")["aria-label"] == "Tiger entrance"
    third = soup(site, TARGET).find("h3", string="Content we don’t control").find_next_sibling("p").get_text()
    assert "schools’ athletics sites" in third and "We don’t control those sites" in third


def test_the_numbers_on_the_page_are_the_ones_the_checks_use(site):
    """The widths, the zoom level and the contrast minimums come from constants, and the constants match the tests
    and the axe script, so the page cannot drift from what is really checked."""
    text = page_text(site)
    narrow, phone = accessibility.PHONE_WIDTHS
    assert f"at phone widths ({narrow} and {phone} pixels) and at desktop width" in text
    assert f"({accessibility.TEXT_MIN}:1 for text, {accessibility.NON_TEXT_MIN}:1 for bars, edges, and focus rings)" in text
    assert f"zoom to {accessibility.ZOOM_PERCENT}%" in text and f"on screens {narrow} pixels wide" in text
    script = (ROOT / "axe" / "run.js").read_text(encoding="utf-8")
    widths = [int(w) for w in re.search(r"const WIDTHS = \[([^\]]+)\]", script).group(1).split(",")]
    assert widths == [*accessibility.PHONE_WIDTHS, accessibility.DESKTOP_WIDTH]
    assert re.search(r'const THEMES = \["light", "dark"\]', script)
    assert f"On {privacy.updated_text(accessibility.REVIEWED)} we reviewed every page" in text
    assert soup(site, TARGET).find("a", string="axe-core")["href"] == accessibility.AXE_URL


def test_the_report_section_gives_the_email_and_the_github_form_and_promises_a_reply(site):
    s = soup(site, TARGET)
    report = s.find("h3", string="Report a problem")
    choices = report.find_next_sibling("ul")
    block = [report.find_next_sibling("p"), choices, choices.find_next_sibling("p")]
    links = [(a.get_text(), a["href"]) for el in block for a in el.find_all("a", href=True)]
    assert links == [(accessibility.ACCESSIBILITY_EMAIL, "mailto:" + accessibility.ACCESSIBILITY_EMAIL),
                     ("suggestion form on GitHub", render.enhancement_url(render.AREA_SITE))]
    assert "We’ll respond, and we’ll fix what we can." in block[2].get_text()
    assert accessibility.ACCESSIBILITY_EMAIL == "accessibility@motetechnologygroup.com"
    # each contact address stays on its own page
    assert privacy.PRIVACY_EMAIL not in (site / TARGET).read_text(encoding="utf-8")
    assert accessibility.ACCESSIBILITY_EMAIL not in (site / "privacy" / "index.html").read_text(encoding="utf-8")


def test_the_page_source_file_exists_and_both_changes_links_point_at_it(site):
    assert (ROOT / accessibility.SOURCE_PATH).is_file()
    assert Path(accessibility.__file__).resolve() == (ROOT / accessibility.SOURCE_PATH).resolve()
    history, source = accessibility.source_urls(render.REPO_URL)
    hrefs = {a.get_text(): a["href"] for a in soup(site, TARGET).find_all("a", href=True)}
    assert hrefs["changes to this page"] == history == f"{render.REPO_URL}/commits/main/{accessibility.SOURCE_PATH}"
    assert hrefs["the page’s source"] == source == f"{render.REPO_URL}/blob/main/{accessibility.SOURCE_PATH}"
    external = [h for h in hrefs.values() if h.startswith("http")]
    assert external and all(h.startswith("https://") for h in external)
