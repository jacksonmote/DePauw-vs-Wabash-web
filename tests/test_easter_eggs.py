"""The seven requested rivalry jokes, page-scoped script policy, and asset budgets."""

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
BASE_CSP = "default-src 'none'; style-src 'self'; img-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'none'; object-src 'none'"
HOME_CSP = BASE_CSP + "; script-src 'self'"
PAGES = (
    "index.html", "alumni/index.html", "how-it-works/index.html",
    "how-it-works/athletics/index.html", "how-it-works/alumni/index.html",
    "404.html", "bell-heist/index.html",
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


def test_seven_page_inventory_and_script_csp_are_exact(site):
    assert {p.relative_to(site).as_posix() for p in site.rglob("*.html")} == set(PAGES)
    for name in PAGES:
        source = page(site, name)
        soup = BeautifulSoup(source, "html.parser")
        scripts = soup.find_all("script")
        if name == "index.html":
            assert csp(source) == HOME_CSP
            assert len(scripts) == 1 and scripts[0].get("src") == "easter-eggs.js"
            assert scripts[0].has_attr("defer") and scripts[0].string is None
            assert [img.get("src") for img in soup.find_all("img")] == [
                "easter-bell.svg", "easter-paw.svg", "easter-paw.svg", "easter-paw.svg", "easter-tiger.svg"]
        else:
            assert csp(source) == BASE_CSP
            assert scripts == [] and soup.find_all("img") == []
        assert "unsafe-" not in csp(source) and "*" not in csp(source)
        assert not soup.find_all("style")
        assert all(not attr.lower().startswith("on") and attr.lower() != "style"
                   for element in soup.find_all(True) for attr in element.attrs)


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


def test_each_nonempty_history_gets_one_reward_and_empty_history_gets_none():
    data = render.load(ROOT / "data" / "series.json")
    home = BeautifulSoup(render.render_index(data, now=NOW, upcoming={}), "html.parser")
    reward = "You’ve reached the bottom. Wabash jokes practically write themselves."
    histories = home.select("details.games")
    assert len(histories) == sum(bool(sport["games"]) for sport in data["sports"].values())
    for details in histories:
        table = details.find("table")
        result = table.find_next_sibling("p", class_="history-reward")
        assert result and result.get_text(" ", strip=True) == reward
        sport_key = details.find_parent("article", class_="sport")["id"]
        expected_dates = [BeautifulSoup(render.date_html(g), "html.parser").get_text(" ", strip=True)
                          for g in reversed(data["sports"][sport_key]["games"])]
        actual_dates = [row.find("th", attrs={"data-label": "Date"}).get_text(" ", strip=True)
                        for row in table.select("tbody tr")]
        assert actual_dates == expected_dates
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
    assert [r.find("th").get_text(" ", strip=True) for r in rows] == [
        BeautifulSoup(render.date_html(g), "html.parser").get_text(" ", strip=True) for g in reversed(examples)]
    assert rows[0].find(attrs={"data-label": "Result"}).get_text(" ", strip=True) == render.result_html(swim)
    tie_row = next(row for row in rows if row.find(attrs={"data-label": "Result"}).get_text(" ", strip=True)
                   == render.result_html(tie))
    assert tie_row.find(attrs={"data-label": "Result"}) is not None
    assert "date not recorded" in " ".join(cell.get_text(" ", strip=True) for cell in rows[-1].find_all(["th", "td"]))
    assert soup.find("table").find_next_sibling("p", class_="history-reward") is not None


def test_alumni_disclosure_is_scoped_and_native(site):
    alumni = page(site, "alumni/index.html")
    assert alumni.count("How can Wabash improve these numbers?") == 1
    assert "We recommend transferring to DePauw." in alumni
    for name in PAGES:
        if name != "alumni/index.html":
            assert "How can Wabash improve these numbers?" not in page(site, name)
    assert '<details class="admissions-note"><summary>' in alumni


def test_title_entrance_is_progressive_and_uses_one_native_click_handler(site):
    home = page(site, "index.html")
    script = (site / "easter-eggs.js").read_text(encoding="utf-8")
    assert 'id="entrance-fallback">DePauw vs. Wabash</span>' in home
    assert 'id="tiger-entrance" class="entrance-button" type="button" hidden' in home
    assert 'id="entrance-status" class="entrance-status" role="status" aria-live="polite"' in home
    assert 'id="entrance-hint"' in home and "five times" in home
    assert script.count('addEventListener("click"') == 1
    assert 'activations === 5' in script and 'activations = Math.min(5, activations + 1)' in script
    assert 'status.textContent = "Your education has been verified."' in script
    assert "innerHTML" not in script and "keydown" not in script and "touchstart" not in script
    css = (ROOT / "static/styles.css").read_text(encoding="utf-8")
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert ".entrance-button:focus-visible" in css and ".tiger-note summary:focus-visible" in css
    assert re.search(r"\.site-footer summary\s*\{[^}]*min-height:\s*44px", css)
    assert re.search(r"\.admissions-note summary\s*\{[^}]*min-height:\s*44px", css)


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
        assert match, f"missing CSS token {name}"
        return tuple(int(match.group(1)[i:i + 2], 16) / 255 for i in (1, 3, 5))

    def luminance(rgb):
        channels = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
                    for value in rgb]
        return sum(weight * value for weight, value in zip((0.2126, 0.7152, 0.0722), channels))

    outline = luminance(token(light, "--color-header-text"))
    for source in (light, dark):
        background = luminance(token(source, "--color-header"))
        assert (max(outline, background) + 0.05) / (min(outline, background) + 0.05) >= 3


def test_shared_footer_comment_and_disclosure_are_exactly_once_on_all_seven_pages(site):
    for name in PAGES:
        source = page(site, name)
        assert source.count("<!-- Both schools supplied the records. Only one supplied the developers. -->") == 1
        assert source.count("A note on impartiality") == 1
        assert source.count("The data is independently sourced. The developers were educated correctly.") == 1
        assert source.index("<!-- Both schools supplied") < source.index('<p class="footer-links">')
        assert source.index("Unofficial fan site") < source.index("A note on impartiality") < source.index('class="copyright"')
        without_comments = re.sub(r"<!--.*?-->", "", source, flags=re.S)
        assert "Only one supplied the developers." not in without_comments
    for export in ("games.csv", "data/series.json", "data/alumni.json", "data/endowment.json", "site.webmanifest"):
        assert "Only one supplied the developers." not in (site / export).read_text(encoding="utf-8")


def test_new_assets_and_stylesheet_stay_within_the_approved_budgets():
    assert len((ROOT / "static/easter-eggs.js").read_bytes()) <= 4096
    assert sum((ROOT / f"static/{asset}").stat().st_size for asset in
               ("easter-bell.svg", "easter-paw.svg", "easter-tiger.svg")) <= 12288
    # Baseline stylesheet on c9fcced: 13,666 bytes; feature allowance: 6,144 bytes.
    assert (ROOT / "static/styles.css").stat().st_size <= 19810


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
        for filename in ("series.json", "alumni.json", "endowment.json"):
            assert (out / "data" / filename).read_bytes() == source_files[ROOT / "data" / filename]
        assert (out / "games.csv").read_bytes() == render.render_csv(data).encode()
    assert outputs[0] == outputs[1]
    # The site publishes three JSON downloads, not a fourth; preserve the current export set.
    assert {name for name in outputs[0] if name.startswith("data/") and name.endswith(".json")} == {
        "data/series.json", "data/alumni.json", "data/endowment.json"}
    assert all(path.read_bytes() == content for path, content in source_files.items())
