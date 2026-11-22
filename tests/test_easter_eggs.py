"""The easter eggs (the Bell story, each game list's closing line, the tiger, the title entrance), the jokes that
were removed and must stay gone, page-scoped script policy, and asset budgets."""

import copy
import datetime as dt
import html
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from bs4 import BeautifulSoup
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scraper"))
import render  # noqa: E402

NOW = dt.datetime(2026, 10, 30, 12, tzinfo=dt.timezone.utc)
BASE_CSP = "default-src 'none'; style-src 'self'; img-src 'self'; font-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'none'; object-src 'none'"
HOME_CSP = BASE_CSP + "; script-src 'self'"            # the policy of every page that loads the title script
SCRIPT_PAGES = {"index.html": "easter-eggs.js", "alumni/index.html": "../easter-eggs.js"}   # page -> script src
PAGES = (
    "index.html", "alumni/index.html", "how-it-works/index.html",
    "how-it-works/athletics/index.html", "how-it-works/alumni/index.html",
    "404.html", "bell-heist/index.html", "heist-details/index.html", "monon-bell/index.html",
    "privacy/index.html", "accessibility/index.html",
)


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    out = tmp_path_factory.mktemp("easter-eggs") / "site"
    render.build(ROOT / "data" / "series.json", out, now=NOW, upcoming_path=out / "missing.json")
    return out


def page(site, path):
    return (site / path).read_text(encoding="utf-8")


def csp(source):
    return html.unescape(re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', source).group(1))


def test_the_page_inventory_and_script_csp_are_exact(site):
    assert {p.relative_to(site).as_posix() for p in site.rglob("*.html")} == set(PAGES)
    for name in PAGES:
        source = page(site, name)
        soup = BeautifulSoup(source, "html.parser")
        scripts = soup.find_all("script")
        if name in SCRIPT_PAGES:
            assert csp(source) == HOME_CSP
            assert len(scripts) == 1 and scripts[0].get("src") == SCRIPT_PAGES[name]
            assert scripts[0].has_attr("defer") and scripts[0].string is None
            if name == "index.html":
                assert [img.get("src") for img in soup.find_all("img")] == [
                    "easter-bell.svg", "easter-paw.svg", "easter-paw.svg", "easter-paw.svg", "easter-tiger.svg"]
            else:
                assert soup.find_all("img") == []
        else:
            assert csp(source) == BASE_CSP
            assert scripts == [] and soup.find_all("img") == []
        assert "unsafe-" not in csp(source) and "*" not in csp(source)
        assert not soup.find_all("style")
        assert all(not attr.lower().startswith("on") and attr.lower() != "style"
                   for element in soup.find_all(True) for attr in element.attrs)


def test_the_alumni_title_is_the_same_entrance_as_the_home_page_and_no_other_page_has_one(site):
    def entrance(name):
        soup = BeautifulSoup(page(site, name), "html.parser")
        return [str(soup.find("h1")), str(soup.find(id="entrance-hint")), str(soup.find(id="entrance-status"))]

    assert entrance("alumni/index.html") == entrance("index.html")
    for name in PAGES:
        soup = BeautifulSoup(page(site, name), "html.parser")
        has = soup.find(id="tiger-entrance") is not None
        assert has == (name in SCRIPT_PAGES), name
        assert (soup.find(id="entrance-status") is not None) == (name in SCRIPT_PAGES), name


def test_the_title_entrance_leaves_no_gap_between_the_title_and_the_nav(site):
    css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    status = re.search(r"\.entrance-status\s*\{([^}]*)\}", css).group(1)
    assert "min-height" not in status and re.search(r"margin:\s*0\s*;", status)   # empty until the message appears
    button = re.search(r"\.entrance-button\s*\{([^}]*)\}", css).group(1)
    assert "height" not in button                                             # nothing taller than a plain title
    assert re.search(r"\.entrance-button\s*\{[^}]*inset:[^}]*44px", css)  # the 44px tap area is not layout
    for name in SCRIPT_PAGES:
        soup = BeautifulSoup(page(site, name), "html.parser")
        status_line = soup.find(id="entrance-status")
        assert status_line.get_text() == ""
        assert status_line.find_previous_sibling().get("id") == "entrance-hint"   # title, hidden hint, empty status,
        assert status_line.find_next_sibling().name == "nav"                    # then straight to the nav


def test_bell_heist_is_a_discoverable_unindexed_story_not_in_the_sitemap(site):
    home = page(site, "index.html")
    heist = page(site, "bell-heist/index.html")
    assert 'href="bell-heist/" aria-label="A little Bell business"' in home
    assert '<h2>Mission accomplished</h2>' in heist
    assert "You’ve successfully relocated the Monon Bell to Greencastle. Your alumni association has no knowledge of this operation." in heist
    assert "An entirely fictional mission." in heist
    assert '<a href="../#monon-bell">Check the real Bell holder</a>' in heist
    assert 'name="robots" content="noindex"' in heist and "canonical" not in heist
    assert len(re.findall(r'<p class="back">', heist)) == 1
    assert "bell-heist" not in page(site, "sitemap.xml")


def test_the_bell_heist_page_uses_block_flow_so_paragraph_margins_do_not_stack_on_the_grid_gap():
    css = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    assert re.search(r"^\.bell-heist \{ display: block;", css, re.M)
    assert re.search(r"^\.bell-heist p \{ margin: 0 0 0\.75rem; \}", css, re.M)
    assert "gap" not in re.search(r"^\.bell-heist \{[^}]*\}", css, re.M).group(0)
    # the class is only on that page's <main>; the home page's Bell link is a different class
    assert '<main class="wrap prose bell-heist" id="main">' in (ROOT / "scraper" / "render.py").read_text(encoding="utf-8")


def test_each_nonempty_history_gets_one_reward_and_empty_history_gets_none():
    data = render.load(ROOT / "data" / "series.json")
    home = BeautifulSoup(render.render_index(data, now=NOW, upcoming={}), "html.parser")
    assert render.HISTORY_REWARD == "You’ve reached the first {unit}. Every rivalry starts somewhere."
    histories = home.select("details.games")
    assert len(histories) == sum(bool(sport["games"]) for sport in data["sports"].values())
    seen = set()
    for details in histories:
        sport = data["sports"][details.find_parent("article", class_="sport")["id"]]
        unit = render.game_words(sport)[0]           # "game", or the sport's own word: a match, a meet
        seen.add(unit)
        rewards = details.select("p.history-reward")
        assert len(rewards) == 1
        assert rewards[0].get_text(" ", strip=True) == f"You’ve reached the first {unit}. Every rivalry starts somewhere."
        # it waits at the very bottom: after the table of the last group (the oldest games), inside that group
        last_group = details.find_all("details")[-1]
        assert rewards[0].parent is last_group
        assert rewards[0].find_previous_sibling("table") is last_group.find("table")
        assert rewards[0].find_next_sibling() is None
        sport_key = details.find_parent("article", class_="sport")["id"]
        games = data["sports"][sport_key]["games"]
        # every game is in the list exactly once: the dated ones newest first, then the ones with no recorded date
        ordered = [g for g in reversed(games) if render.date_known(g)] + \
                  [g for g in reversed(games) if not render.date_known(g)]
        expected_dates = [BeautifulSoup(render.date_html(g), "html.parser").get_text(" ", strip=True) for g in ordered]
        actual_dates = [row.find("th", scope="row").get_text(" ", strip=True) for row in details.select("tbody tr")]
        assert actual_dates == expected_dates
    assert seen == {"game", "match", "meet"}
    assert "history-reward" not in render.games_table("football", [])
    assert len(home.select("details.tiger-note")) == 1
    assert len(home.select("img[src='easter-paw.svg']")) == min(3, len(data["sports"]))


def test_history_reward_keeps_ties_undated_games_and_swimming_rows_intact():
    data = render.load(ROOT / "data" / "series.json")
    tie = next(g for g in data["sports"]["mens-basketball"]["games"] if g["result"] == "T")
    undated = next(g for g in data["sports"]["baseball"]["games"] if not render.date_known(g))
    swim = data["sports"]["mens-swimming-and-diving"]["games"][-1]
    examples = sorted([tie, undated, swim], key=lambda g: g["date"])
    output = render.games_table("mens-swimming-and-diving", examples, ("meet", "meets"), merged=True)
    soup = BeautifulSoup(output, "html.parser")
    rows = soup.select("tbody tr")
    assert len(rows) == 3

    def result_of(row):
        """(letter, words) of a row's result cell: the phone layout's letter, then the result as every width shows it."""
        cell = row.find_all("td")[render.GAME_COLUMNS.index("Result") - 1]
        letter = cell.find("abbr").extract().get_text()
        return letter, cell.get_text(" ", strip=True)

    # newest first by decade (the swimming meet, then the 1954 tie), and the game with no date in the last group
    assert [r.find("th").get_text(" ", strip=True) for r in rows] == [
        BeautifulSoup(render.date_html(g), "html.parser").get_text(" ", strip=True) for g in (swim, tie, undated)]
    assert result_of(rows[0]) == (swim["result"], render.result_html(swim))
    assert result_of(rows[1]) == ("T", render.result_html(tie))
    assert "date not recorded" in " ".join(cell.get_text(" ", strip=True) for cell in rows[-1].find_all(["th", "td"]))
    groups = soup.select("details.games details")
    assert [g.find("summary").get_text().split(" ")[0] for g in groups][-1] == render.UNDATED_GROUP
    assert groups[-1].find("table").find_next_sibling("p", class_="history-reward") is not None
    assert [p.get_text() for p in soup.select("p.history-reward")] == [
        "You’ve reached the first meet. Every rivalry starts somewhere."]


def test_the_removed_side_taking_jokes_are_on_no_page(site):
    """The footer's impartiality note and the alumni page's admissions note took DePauw's side; both were removed."""
    for name in PAGES:
        source = page(site, name)
        for gone in ("impartiality", "A note on impartiality", "The developers were educated correctly.",
                     "admissions-note", "How can Wabash improve these numbers?", "We recommend transferring to DePauw."):
            assert gone not in source, (name, gone)
    css = (ROOT / "static/styles.css").read_text(encoding="utf-8")
    assert ".impartiality" not in css and ".admissions-note" not in css


# Three more lines read as digs at Wabash and were replaced or removed: the game lists' old closing line, the title
# entrance's old message and the footer's HTML comment. None may come back, in any letter case.
REMOVED_JOKES = ("Wabash jokes", "jokes practically write themselves", "education has been verified",
                 "Only one supplied the developers", "Both schools supplied the records")
TEXT_SUFFIXES = {".html", ".js", ".css", ".json", ".csv", ".xml", ".txt", ".webmanifest", ".svg", ".py", ".md", ".yml"}


def test_the_removed_wabash_jokes_are_in_no_built_file_and_no_source_file(site):
    """Fails if "Wabash jokes" or "education has been verified" (or the footer comment) comes back: every text file
    of the built site, HTML comments included, and the files they are built from."""
    built = [p for p in site.rglob("*") if p.is_file() and p.suffix in TEXT_SUFFIXES]
    sources = [p for folder in ("scraper", "static", "data", ".github") for p in (ROOT / folder).rglob("*")
               if p.is_file() and p.suffix in TEXT_SUFFIXES]
    sources += [ROOT / "README.md", ROOT / "AGENTS.md"]
    assert {p.name for p in built} >= {"index.html", "easter-eggs.js", "games.csv", "series.json"}
    assert {p.name for p in sources} >= {"render.py", "easter-eggs.js", "README.md"}
    for path in built + sources:
        text = path.read_text(encoding="utf-8").lower()
        for gone in REMOVED_JOKES:
            assert gone.lower() not in text, (path.name, gone)
    script =(site / "easter-eggs.js").read_text(encoding="utf-8")
    assert script.count("status.textContent") == 1                  # the entrance has one message, the new one


def test_title_entrance_is_progressive_and_uses_one_native_click_handler(site):
    home = page(site, "index.html")
    script = (site / "easter-eggs.js").read_text(encoding="utf-8")
    assert '<div class="entrance-wrap"><h1>DePauw vs. Wabash</h1><button id="tiger-entrance" class="entrance-button" type="button" hidden aria-label="Tiger entrance"' in home
    assert "entrance-fallback" not in home and "entrance-fallback" not in script
    assert 'id="entrance-status" class="entrance-status" role="status" aria-live="polite"' in home
    assert '<span id="entrance-hint" class="sr-only" hidden>' in home and "five times" in home   # only a description
    assert script.count('addEventListener("click"') == 1
    assert 'activations === 5' in script and 'activations = Math.min(5, activations + 1)' in script
    assert 'status.textContent = "The Tiger appreciates your persistence."' in script
    assert "innerHTML" not in script and "keydown" not in script and "touchstart" not in script
    css = (ROOT / "static/styles.css").read_text(encoding="utf-8")
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert ".entrance-button:focus-visible" in css and ".tiger-note summary:focus-visible" in css
    assert re.search(r"\.site-footer summary\s*\{[^}]*min-height:\s*44px", css)


def test_title_button_focus_outline_contrasts_with_header_in_light_and_dark_modes():
    css = (ROOT / "static/styles.css").read_text(encoding="utf-8")
    selector = r"\.site-header \.entrance-button:focus-visible\s*\{([^}]*)\}"
    rules = re.findall(selector, css)
    assert len(rules) == 2
    assert re.search(r"outline-color:\s*var\(--color-header-text\)", rules[0])
    assert re.search(r"outline-color:\s*ButtonText", rules[1])

    light = css.split("@media (prefers-color-scheme: dark)", 1)[0]
    dark = css.split("@media (prefers-color-scheme: dark)", 1)[1].split("\n}", 1)[0]

    def token(source, name):
        match = re.search(rf"{re.escape(name)}:\s*(#[0-9a-fA-F]{{6}})", source)
        if not match and source is dark:                      # dark mode keeps a light-mode token it does not redefine
            match = re.search(rf"{re.escape(name)}:\s*(#[0-9a-fA-F]{{6}})", light)
        assert match, f"missing CSS token {name}"
        return tuple(int(match.group(1)[i:i + 2], 16) / 255 for i in (1, 3, 5))

    def luminance(rgb):
        channels = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
                    for value in rgb]
        return sum(weight * value for weight, value in zip((0.2126, 0.7152, 0.0722), channels))

    for source in (light, dark):
        outline = luminance(token(source, "--color-header-text"))     # the outline is drawn in the header's own text color
        background = luminance(token(source, "--color-header"))
        assert (max(outline, background) + 0.05) / (min(outline, background) + 0.05) >= 3


def test_no_page_has_the_footer_comment_or_the_impartiality_note(site):
    """The shared footer once carried a "source confession" in an HTML comment; it was removed with the other
    jokes that took a side."""
    for name in PAGES:
        source = page(site, name)
        assert "supplied the records" not in source and "supplied the developers" not in source
        assert "A note on impartiality" not in source and "The developers were educated correctly." not in source
        assert source.count('<p class="footer-links">') == 1
        assert source.index("Unofficial fan site") < source.index('class="copyright"')
    for export in ("games.csv", "data/series.json", "data/alumni.json", "data/endowment.json", "site.webmanifest"):
        assert "Only one supplied the developers." not in (site / export).read_text(encoding="utf-8")


STYLESHEET_LIMIT = 32768
SCRIPT_LIMIT = 4096           # static/easter-eggs.js, the site's one script
ART = ("easter-bell.svg", "easter-paw.svg", "easter-tiger.svg")
ART_LIMIT = 12288             # the three easter-egg drawings together


def shipped_size(data: bytes) -> int:
    """A text file's size as it ships. The repository and the deployed site hold LF line endings; a Windows working
    copy may hold CRLF (git's autocrlf), one byte more per line that no visitor ever downloads. So each CRLF is
    counted as the single byte it is on the site, and the budget reads the same on every machine."""
    return len(data.replace(b"\r\n", b"\n"))


def test_new_assets_and_stylesheet_stay_within_the_approved_budgets():
    # the script and the three drawings, like the stylesheet, as they ship (shipped_size)
    assert shipped_size((ROOT / "static/easter-eggs.js").read_bytes()) <= SCRIPT_LIMIT
    assert sum(shipped_size((ROOT / f"static/{asset}").read_bytes()) for asset in ART) <= ART_LIMIT
    # Baseline stylesheet on c9fcced: 13,666 bytes; feature allowance: 6,144 bytes. The front-end polish (design
    # tokens, compact header, quiet footer) raised the allowance to 24,576 bytes; the home top (scoreboard, record
    # bars with 101 width classes, jump list) to 32,768; later polish PRs state their own. The count is of the
    # file as it ships (shipped_size), not of a Windows working copy with its longer line endings.
    assert shipped_size((ROOT / "static/styles.css").read_bytes()) <= STYLESHEET_LIMIT
    # one .sr-only rule: a second copy once sat beside the alumni styles, bytes for nothing
    assert (ROOT / "static/styles.css").read_text(encoding="utf-8").count(".sr-only {") == 1
    # The one web font: a Latin subset of Barlow Condensed Bold, served from this site, license text beside it.
    assert (ROOT / "static/fonts/BarlowCondensed-Bold-latin.woff2").stat().st_size <= 16384


def test_the_stylesheet_script_and_drawing_budgets_count_what_ships_whatever_the_working_copys_line_endings():
    lf = b".a { margin: 0; }\n.b { padding: 0; }\n"
    crlf = lf.replace(b"\n", b"\r\n")
    assert len(crlf) == len(lf) + 2 and shipped_size(crlf) == shipped_size(lf) == len(lf)
    assert shipped_size(b"a\rb") == 3 and shipped_size(b"a\n\rb") == 4 and shipped_size(b"") == 0   # only a CRLF pair
    # at the limit itself: a file of exactly the allowed size passes with either line ending, one byte more does not
    full = (b"x" * 63 + b"\n") * (STYLESHEET_LIMIT // 64)
    assert shipped_size(full) == shipped_size(full.replace(b"\n", b"\r\n")) == STYLESHEET_LIMIT
    assert shipped_size(full + b"x") > STYLESHEET_LIMIT
    css = (ROOT / "static/styles.css").read_bytes()
    assert shipped_size(css) == len(css) - css.count(b"\r\n") <= STYLESHEET_LIMIT
    # the script's budget is counted the same way: a script of exactly the limit passes with either line ending
    script = (b"x" * 63 + b"\n") * (SCRIPT_LIMIT // 64)
    assert shipped_size(script) == shipped_size(script.replace(b"\n", b"\r\n")) == SCRIPT_LIMIT
    js = (ROOT / "static/easter-eggs.js").read_bytes()
    assert shipped_size(js) == len(js) - js.count(b"\r\n") <= SCRIPT_LIMIT
    # and so are the three drawings, as one sum: drawings of exactly the limit pass with either line ending
    drawings = [(b"x" * 63 + b"\n") * (ART_LIMIT // 64 // 2), (b"x" * 63 + b"\n") * (ART_LIMIT // 64 // 2), b""]
    assert sum(map(shipped_size, drawings)) == ART_LIMIT
    assert sum(shipped_size(d.replace(b"\n", b"\r\n")) for d in drawings) == ART_LIMIT
    art = [(ROOT / f"static/{asset}").read_bytes() for asset in ART]
    assert sum(map(shipped_size, art)) == sum(len(a) - a.count(b"\r\n") for a in art) <= ART_LIMIT


def test_original_art_is_safe_well_formed_svg():
    for name in ("easter-bell.svg", "easter-paw.svg", "easter-tiger.svg"):
        root = ET.parse(ROOT / "static" / name).getroot()
        assert root.tag.endswith("svg")
        for node in root.iter():
            assert node.tag.rsplit("}", 1)[-1].lower() not in {"script", "style", "foreignobject"}
            for attr, value in node.attrib.items():
                attr = attr.rsplit("}", 1)[-1].lower()
                assert not attr.startswith("on") and attr != "style"
                if attr in {"href", "src"}:
                    assert value.startswith("#")


@pytest.mark.parametrize(("result", "holder"), [("W", "DePauw"), ("L", "Wabash")])
def test_rendered_assets_exist_and_heist_link_works_for_either_bell_holder(site, result, holder):
    games = [{"date": "2024-11-09", "result": result, "depauw": 20, "wabash": 10}]
    block = render.bell_block(games)
    assert 'href="bell-heist/" aria-label="A little Bell business"' in block
    parsed = BeautifulSoup(block, "html.parser")
    assert parsed.select_one(".bell-holder strong").get_text(strip=True).startswith(f"{holder} has held the Bell")
    for asset in ("easter-bell.svg", "easter-paw.svg", "easter-tiger.svg", "easter-eggs.js"):
        assert (site / asset).read_bytes() == (ROOT / "static" / asset).read_bytes()
    assert render.bell_block([]) == ""


def test_bell_icon_is_omitted_when_all_football_games_precede_the_bell():
    data = render.load(ROOT / "data" / "series.json")
    sport = copy.deepcopy(data["sports"]["football"])
    game = copy.deepcopy(sport["games"][0])
    game.update(date="1931-11-01", season="1931")
    sport["games"] = [game]
    data["sports"] = {"football": sport}
    data["totals"].update(depauw_wins=int(game["result"] == "W"),
                          wabash_wins=int(game["result"] == "L"),
                          ties=int(game["result"] == "T"), games_played=1)
    home = BeautifulSoup(render.render_index(data, now=NOW, upcoming={}), "html.parser")
    assert render.bell_block([game]) == ""
    assert home.select_one('a[aria-label="A little Bell business"]') is None
    assert home.select_one('img[src="easter-bell.svg"]') is None


@pytest.mark.parametrize("sport_count", [1, 2, 7])
def test_paw_trail_follows_each_of_the_final_sports_and_ids_stay_unique(sport_count):
    data = copy.deepcopy(render.load(ROOT / "data" / "series.json"))
    data["sports"] = dict(list(data["sports"].items())[:sport_count])
    data["totals"] = {
        "depauw_wins": sum(s["record"]["depauw_wins"] for s in data["sports"].values()),
        "wabash_wins": sum(s["record"]["wabash_wins"] for s in data["sports"].values()),
        "ties": sum(s["record"]["ties"] for s in data["sports"].values()),
        "games_played": sum(len(s["games"]) for s in data["sports"].values()),
    }
    document = BeautifulSoup(render.render_index(data, now=NOW, upcoming={}), "html.parser")
    main = document.main
    cards = main.find_all("article", class_="sport", recursive=False)
    paws = main.find_all("div", class_="paw-trail", recursive=False)
    expected_count = min(3, sport_count)
    assert len(cards) == sport_count and len(paws) == expected_count
    for index, (card, paw) in enumerate(zip(cards[-expected_count:], paws)):
        assert card.find_next_sibling() == paw
        assert ("paw-left" if index % 2 == 0 else "paw-right") in paw["class"]
    assert paws[-1].find_next_sibling("details", class_="tiger-note") is not None
    ids = [node["id"] for node in document.find_all(id=True)]
    assert len(ids) == len(set(ids))


def test_published_inputs_are_unchanged_and_two_fixed_time_builds_match(tmp_path):
    source_files = {path: path.read_bytes() for path in (ROOT / "data").glob("*.json")}
    data = render.load(ROOT / "data" / "series.json")
    before = copy.deepcopy(data)
    render.render_index(data, now=NOW, upcoming={})
    assert data == before

    alumni = render.alumni_page.validate_alumni(
        render.alumni_page.load_json(ROOT / "data" / "alumni.json", "alumni"))
    endowment = render.alumni_page.validate_endowment(
        render.alumni_page.load_json(ROOT / "data" / "endowment.json", "endowment"))
    alumni_before, endowment_before = copy.deepcopy(alumni), copy.deepcopy(endowment)
    render.alumni_page.render_page(alumni, endowment, head_html="", footer_html="")
    assert alumni == alumni_before and endowment == endowment_before
    outputs = []
    for name in ("first", "second"):
        out = tmp_path / name
        render.build(ROOT / "data" / "series.json", out, now=NOW, upcoming_path=out / "missing.json")
        outputs.append({path.relative_to(out).as_posix(): path.read_bytes() for path in out.rglob("*") if path.is_file()})
        for filename in ("series.json", "alumni.json", "endowment.json", "bell-heists.json"):
            assert (out / "data" / filename).read_bytes() == source_files[ROOT / "data" / filename]
        assert (out / "games.csv").read_bytes() == render.render_csv(data).encode()
    assert outputs[0] == outputs[1]
    # The site publishes four JSON downloads, not a fifth; preserve the current export set.
    assert {name for name in outputs[0] if name.startswith("data/") and name.endswith(".json")} == {
        "data/series.json", "data/alumni.json", "data/endowment.json", "data/bell-heists.json"}
    assert all(path.read_bytes() == content for path, content in source_files.items())


def test_the_title_is_a_plain_heading_and_the_entrance_button_sits_beside_it(site):
    for name in SCRIPT_PAGES:
        soup = BeautifulSoup(page(site, name), "html.parser")
        h1 = soup.find("h1")
        assert h1.get_text() == "DePauw vs. Wabash" and h1.find(True) is None     # plain text, no button or span inside
        button = soup.find(id="tiger-entrance")
        assert button.find_parent("h1") is None and button.parent is h1.parent
        assert button["aria-label"] == "Tiger entrance" and button.has_attr("hidden")   # hidden until the script runs
        assert button["aria-describedby"] == "entrance-hint"
